"""선선갭 표 글 갈래 — `build_pp_text` 가 `encode_pp_table(build_pp_table())` 와 바이트까지 같은가 (스펙 049 §3.2).

무작위 저장소(정상·fail·stale·300초 넘은 행·펀딩 없음·-0.0 펀딩·이스케이프가 필요한 코인 이름)를 여럿 만들어 같은
`now` 로 두 갈래를 견준다. 유한하지 않은 값이 든 표는 글 갈래가 ValueError 로 멈추고, 게시기는 dict 갈래로 다시
만들어 기준과 같은 예외를 낸다.
"""

import random
from datetime import timedelta

import pytest

from app.core.config import PERP_SOURCES
from app.core.live_store import LiveStore
from app.features.pp.push import build_pp_json, encode_pp_table
from app.features.pp.service import build_pp_table, build_pp_text
from app.features.pp.tests.helpers import T0, perp

ODD_NAMES = ('Q"X', "역\\슬", "한글코인", "TAB\tX")


def _random_store(seed: int) -> LiveStore:
    rnd = random.Random(seed)
    store = LiveStore()
    names = [f"C{i}" for i in range(80)] + list(ODD_NAMES)
    for base in names:
        for src in rnd.sample(PERP_SOURCES, rnd.randint(1, len(PERP_SOURCES))):
            age = rnd.choice([0, 0, 1, 7, 301])
            now = T0 - timedelta(seconds=age)
            bid = rnd.uniform(0.001, 50_000)
            perp(
                store,
                src,
                base,
                bid=rnd.choice([bid, 0.0]) if rnd.random() < 0.05 else bid,
                ask=bid * rnd.uniform(1.0, 1.01),
                bid_size=rnd.choice([0.0, 1.0, 2.5, 2.5, 2.5]),
                funding_rate=rnd.choice([None, 0.0, -0.0, 1e-4, -3.75e-5, 0.1 + 0.2]),
                next_funding_ms=rnd.choice(
                    [None, 1_791_475_200_000, 1_791_475_200_999]
                ),
                funding_interval_h=rnd.choice([None, 1, 4, 8]),
                now=now,
            )
    store.mark_received(int(T0.timestamp()))
    return store


@pytest.mark.parametrize("seed", range(12))
def test_text_branch_is_byte_identical_to_dict_branch(seed: int) -> None:
    store = _random_store(seed)
    now = T0 + timedelta(seconds=1)
    text = build_pp_text(store, now=now)
    ref = encode_pp_table(build_pp_table(store, now=now))
    # fetchedAt 은 함수마다 지금 시각이라 뺀다 — 나머지 바이트가 같아야 한다
    assert text.rsplit(',"fetchedAt":', 1)[0] == ref.rsplit(',"fetchedAt":', 1)[0]


def test_empty_store_is_an_empty_table() -> None:
    store = LiveStore()
    text = build_pp_text(store, now=T0)
    assert text.startswith('{"rows":[],"warnings":[],"dataReceivedAt":null,')


def test_non_finite_value_falls_back_to_the_dict_branch_error() -> None:
    store = _random_store(3)
    perp(store, "bybit_perp", "C1", bid=float("inf"))
    perp(store, "binance_perp", "C1")
    with pytest.raises(ValueError):
        build_pp_text(store, now=T0)
    # 게시기의 글은 dict 갈래로 다시 만든다 — 기준과 같은 예외(NaN·inf 거부)가 난다
    with pytest.raises(ValueError) as fallback:
        build_pp_json(store)
    with pytest.raises(ValueError) as reference:
        encode_pp_table(build_pp_table(store, now=T0))
    assert str(fallback.value) == str(reference.value)
