"""랜딩 live 의 spark 걷기가 017 게시기가 실제로 적는 표 글자에서 타고, 결과가 원문 파싱과 같은지 (022 §3.2).

표는 운영 표 3장(`data/spreads_prod_3.json.xz` — 행마다 코인·거래소·가격·spark 30개·입출금·망)에 값·차감폭·
상태를 시드로 붙여 003 응답 모델(`SpreadsResponse`)의 키 순서로 만들고 게시기 인코더로 적는다. 행 키 순서나
인코더 구분자가 바뀌어 걷기가 조용히 원문 파싱(느린 경로)으로 떨어지면 첫 단언이 잡는다. 기준은 같은 표를
공백 있는 JSON 으로 다시 적어 걷지 않고 파싱한 live 다. 기능 둘(spreads·landing)을 함께 쓰므로 기능 폴더 밖에 둔다.
"""

import json
import lzma
import random
from pathlib import Path

import pytest

from app.features.landing.service import _strip_spark, build_live
from app.features.spreads.models import SpreadRow, SpreadsResponse
from app.features.spreads.push import encode_table, encode_table_with_spark_json

DATA = Path(__file__).parent / "data" / "spreads_prod_3.json.xz"
# 조각의 열 — 행마다 이 순서의 값 목록이다 (test_real_tables 와 같은 자료)
FIELDS = (
    "sym", "dom", "fx", "krw", "usd", "spark", "netDom", "depDom", "wdDom", "depFx", "wdFx", "netFx", "dayChg",
)  # fmt: skip
TIES = (
    1.0,
    0.5,
    2.25,
    -0.3,
)  # 값이 같은 후보가 여럿 생기게 — 동률 규칙까지 같아야 한다


@pytest.fixture(scope="module")
def tables() -> list[dict]:
    return json.loads(lzma.decompress(DATA.read_bytes()))


def _value(rng: random.Random) -> float:
    return rng.choice(TIES) if rng.random() < 0.2 else rng.uniform(-3.0, 4.0)


def _payload(table: dict, seed: int) -> dict:
    rng = random.Random(seed)
    rows = []
    for values in table["rows"]:
        r = dict(zip(FIELDS, values, strict=True))
        roll = rng.random()
        rows.append(
            SpreadRow(
                sym=r["sym"], dom=r["dom"], fx=r["fx"],
                fwd=_value(rng), rev=_value(rng), usd=r["usd"], spark=r["spark"],
                status="ok" if roll < 0.9 else ("stale" if roll < 0.95 else "fail"),
                age=0.1,
                slip_fwd=rng.choice((0.1, 0.4)) if rng.random() < 0.3 else rng.uniform(0.0, 1.5),
                slip_rev=rng.uniform(0.0, 1.5),
                krw=r["krw"], net_dom=r["netDom"],
                dep_dom=r["depDom"], wd_dom=r["wdDom"], dep_fx=r["depFx"], wd_fx=r["wdFx"],
                net_fx=r["netFx"], day_chg=r["dayChg"],
            )
        )  # fmt: skip
    response = SpreadsResponse(
        rate=table["rate"],
        notional=1000.0,
        rows=rows,
        warnings=[],
        data_received_at=1_790_509_107_000,
        fetched_at=1_790_509_107_142,
    )
    return response.model_dump(by_alias=True)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_published_table_is_stripped_and_live_matches_full_parse(
    tables: list[dict], index: int
) -> None:
    payload = _payload(tables[index], seed=index)
    text = encode_table_with_spark_json(payload, {})
    assert text == encode_table(payload)  # 게시기가 Redis 에 쓰는 바이트 그대로

    stripped = _strip_spark(text)
    assert '"spark"' not in stripped  # 걷기 경로를 탔다
    assert len(stripped) < 0.8 * len(text)  # spark 30개가 표 글자의 큰 몫이다
    full = json.loads(text)
    for r in full["rows"]:
        del r["spark"]
    assert json.loads(stripped) == full  # spark 키만 빠지고 나머지 값은 같다

    spaced = json.dumps(json.loads(text), ensure_ascii=False)
    assert _strip_spark(spaced) is spaced  # 기준 쪽은 걷지 않는다
    live = build_live(text)
    reference = build_live(spaced)
    assert live is not None and reference is not None
    assert live.model_dump() == reference.model_dump()
    # 기준과 같은 것이 빈 결과끼리 같은 것이 아니게 — 운영 모양의 표는 top 5·깊이 예시가 다 찬다
    assert len(live.top) == 5 and live.depth_gap is not None
    assert live.pairs == len(payload["rows"]) == 1_458
