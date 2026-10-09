"""표 글 갈래(017 게시기) — 행 dict 없이 바로 쓴 표 JSON 이 dict 표를 통째로 인코딩한 것과 같은 바이트인지 (2026-10-09).

기준은 바꾸기 전의 게시 경로 `encode_table(build_table(...))`(= `encode_table_with_spark_json`)이다. 글 갈래는
행 계산을 한 벌로 두고 내보내는 자리만 다르므로, 같은 저장소·같은 시각에서 두 경로의 글자가 같아야 한다.
작은 표 하나는 바꾸기 전 코드(9b13b2c)로 만든 글자를 그대로 박아 둔다 — 두 갈래가 함께 틀어지는 것도 잡는다.
망 판정 메모의 회차 규칙(006 §3.7 — 같은 회차의 틱이 채운 칸은 입력 비교 없이 읽는다)도 여기서 본다.
"""

import json
import lzma
import math
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core import networks
from app.core.influx import SparkBucketRow
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.networks import Network, WalletFields, WalletMemo
from app.core.spark import SparkBuffer
from app.core.tick_store import TickRelay
from app.core.ticks import build_tick
from app.features.spreads import push, service
from app.features.spreads.push import (
    SpreadsPublisher,
    encode_table,
    encode_table_with_spark_json,
    encode_text_table,
)
from app.features.spreads.service import build_table
from app.features.spreads.tests.helpers import make_row, seed_rows
from tests.test_real_tables import DATA, _next_second, build_world
from tests.test_real_tables import NOW as REAL_NOW

NOW = datetime(2026, 10, 9, 5, 0, 0, 250_000, tzinfo=UTC)
FETCHED = 1_791_522_000.5
ODD = 'a"b\\c\u2028\x01이더😀'  # 따옴표·역슬래시·U+2028·제어문자·한글·이모지

# 바꾸기 전 코드(9b13b2c)의 `encode_table(build_table(_small_store(), now=NOW, day_open=_DAY_OPEN))` 글자 그대로
GOLDEN = (
    '{"rate":1450.0,"notional":1000.0,"rows":[{"sym":"BTC","dom":"upbit","fx":"binance","fwd":3.447241389655087,"rev":-3.4010303993130653,"usd":100000.0,"spark":[1.235,-0.0,1e+16],"status":"ok","age":2.0,"slipFwd":0.0,"slipRev":0.0,"krw":150000000.0,"netDom":"Bitcoin","depDom":true,"wdDom":true,"depFx":true,"wdFx":true,"netFx":"Bitcoin","dayChg":7.14285714285714},'
    '{"sym":"ETH","dom":"upbit","fx":"binance","fwd":1.404965801175262,"rev":-1.4699706005879887,"usd":3400.25,"spark":[],"status":"ok","age":2.0,"slipFwd":2.220446049250313e-14,"slipRev":0.0,"krw":5000000.0,"netDom":"a\\"b\\\\c\u2028\\u0001이더😀","depDom":true,"wdDom":false,"depFx":false,"wdFx":true,"netFx":"Ethereum (ERC20)","dayChg":-1.9607745098039198},'
    '{"sym":"XRP","dom":"upbit","fx":"binance","fwd":0.0,"rev":0.0,"usd":0.0,"spark":[0.0],"status":"fail","age":2.0,"slipFwd":0.0,"slipRev":0.0,"krw":0.0,"netDom":null,"depDom":null,"wdDom":null,"depFx":null,"wdFx":null,"netFx":null,"dayChg":null}],"warnings":[],"dataReceivedAt":1791522000000,"fetchedAt":1791522000500}'
)
_DAY_OPEN = {("upbit", "BTC"): 140_000_000.0, ("upbit", "ETH"): 5_100_000}


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetchedAt(만든 시각)과 게시기의 now 를 고정한다 — 두 경로를 다른 순간에 불러도 시각 필드가 같게."""
    monkeypatch.setattr(service, "time", SimpleNamespace(time=lambda: FETCHED))


def _fix_publisher_now(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return now

    monkeypatch.setattr(push, "datetime", Fixed)


def _small_store() -> LiveStore:
    store = LiveStore()
    seed_rows(
        store,
        [
            make_row(
                "upbit",
                "BTC",
                price=150_000_000,
                asks=[[150_000_100, 1]],
                bids=[[150_000_000, 2]],
                dep=True,
                wd=True,
                networks=[Network("BTC", "Bitcoin", True, True)],
            ),
            make_row(
                "upbit",
                "ETH",
                price=5_000_000.5,
                asks=[[5_000_100.0, 3.0]],
                bids=[[5_000_000.0, 3.0]],
                dep=True,
                wd=False,
                networks=[Network("ETH", ODD, True, False)],
            ),
            make_row(
                "upbit", "XRP", price=800, asks=[], bids=[[799.0, 10.0]]
            ),  # 빈 호가 → fail 행
        ],
        NOW,
    )
    seed_rows(
        store,
        [
            make_row(
                "binance",
                "BTC",
                price=100_000,
                asks=[[100_001, 1.5]],
                bids=[[99_999, 1.5]],
                dep=True,
                wd=True,
                networks=[Network("BTC", "Bitcoin", True, True)],
            ),
            make_row(
                "binance",
                "ETH",
                price=3400.25,
                asks=[[3400.5, 2.0]],
                bids=[[3400.0, 2.0]],
                dep=False,
                wd=None,
                networks=[
                    Network("ETH", "Ethereum (ERC20)", False, True),
                    Network("ARB", 'Arb "One"', True, True),
                ],
            ),
            make_row("binance", "XRP", price=0.55),
        ],
        NOW - timedelta(seconds=2),
    )
    store.stream("upbit").last_message_at = int(NOW.timestamp() * 1000) - 1500
    store.set_rate("upbit", 1450, 1449, NOW)
    store.mark_received(int(NOW.timestamp()))
    minute = int(NOW.timestamp()) // 60
    buffer = SparkBuffer()
    buffer.seed(
        [
            SparkBucketRow("upbit", "binance", "BTC", (minute - 2 + i) * 60, v)
            for i, v in enumerate([1.23456, -0.0004, 1e16])
        ]
        + [SparkBucketRow("upbit", "binance", "XRP", minute * 60, 1e-05)]
    )
    buffer.publish(store)
    return store


def _text(store: LiveStore, **kw: object) -> str:
    return encode_text_table(build_table(store, spark_json=store.spark_json(), **kw))  # type: ignore[arg-type]


def _outcome(fn, *args, **kw) -> tuple[str, str]:  # noqa: ANN001
    try:
        return ("ok", fn(*args, **kw))
    except ValueError as exc:
        return ("ValueError", str(exc))


# ---- 바이트 동일 ----


def test_small_table_text_is_the_pre_change_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정수 가격("100000.0")·이스케이프 망 이름·fail 행·−0.0·지수 표기·조각 없는 조합·dayChg — 박아 둔 글자와 같다."""
    store = _small_store()
    assert encode_table(build_table(store, now=NOW, day_open=_DAY_OPEN)) == GOLDEN
    assert _text(store, now=NOW, day_open=_DAY_OPEN) == GOLDEN
    # 게시기 경로(메모·회차·글 기억 포함)도 같다 — 같은 게시기로 두 번(두 번째는 기억한 글을 다시 쓴다)
    _fix_publisher_now(monkeypatch, NOW)
    memo = WalletMemo()
    publisher = SpreadsPublisher(
        store=store, bus=None, day_open=_DAY_OPEN, wallet_memo=memo
    )  # type: ignore[arg-type]
    for second in range(2):
        build_tick(store, int(NOW.timestamp()) + second, [], memo)
        assert publisher._encode() == GOLDEN


@pytest.fixture(scope="module")
def tables() -> list[dict[str, list]]:
    return json.loads(lzma.decompress(DATA.read_bytes()))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_publisher_text_equals_the_dict_table_on_real_tables(
    tables: list[dict[str, list]], index: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실데이터 표 3장 × 4초(분이 바뀌는 틱, 매초 망 목록·코인 값이 바뀌는 행 40개 포함) — 게시기 글이 기준 바이트다."""
    store, day_open, buffer = build_world(tables[index], seed=40 + index)
    rng = random.Random(400 + index)
    relay = TickRelay(stream=None, store=store, spark=buffer)
    memo = WalletMemo()
    publisher = SpreadsPublisher(
        store=store, bus=None, day_open=day_open, wallet_memo=memo
    )  # type: ignore[arg-type]
    now = REAL_NOW
    for step in range(4):
        build_tick(store, int(now.timestamp()), [], memo)
        _fix_publisher_now(monkeypatch, now)
        got = publisher._encode()
        # 기준 — 메모 없이 행마다 판정한 dict 표를 통째로 인코딩
        payload = build_table(store, now=now, day_open=day_open)
        expected = encode_table(payload)
        assert (
            got == expected == encode_table_with_spark_json(payload, store.spark_json())
        )
        rows = json.loads(got)["rows"]
        assert len(rows) > 1_400 and {"ok", "fail"} <= {r["status"] for r in rows}
        if step == 0:
            assert "stale" in {r["status"] for r in rows} and (
                "-0.0," in got or "-0.0]" in got
            )
        now += timedelta(seconds=1)
        _next_second(store, rng, now)
        relay(build_tick(store, int(now.timestamp()), []))


def test_wallet_texts_are_reused_only_while_the_verdict_object_is_the_same(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """입출금 캐시가 새 객체로 바뀐 회차 — 바뀐 조합만 글을 새로 쓰고, 표 바이트는 매 회차 기준과 같다."""
    store = _small_store()
    memo = WalletMemo()
    publisher = SpreadsPublisher(
        store=store, bus=None, day_open=_DAY_OPEN, wallet_memo=memo
    )  # type: ignore[arg-type]
    _fix_publisher_now(monkeypatch, NOW)
    build_tick(store, 1, [], memo)
    assert publisher._encode() == GOLDEN
    before = dict(publisher._texts.wallet)
    # 조회기가 새 망 목록을 건 것처럼 — 같은 이름이라도 새 객체, 출금 값은 뒤집는다
    eth = store.get_all(exchange="binance")
    eth_row = next(r for r in eth if r.base == "ETH")
    eth_row.networks = [
        Network("ETH", "Ethereum (ERC20)", False, False),
        Network("ARB", ODD, True, True),
    ]
    build_tick(store, 2, [], memo)
    got = publisher._encode()
    assert got == encode_table(build_table(store, now=NOW, day_open=_DAY_OPEN))
    assert '"wdFx":false,"netFx":"Ethereum (ERC20)"' in got and got != GOLDEN
    after = publisher._texts.wallet
    eth_key, btc_key = ("upbit", "binance", "ETH"), ("upbit", "binance", "BTC")
    assert after[eth_key] is not before[eth_key]  # 판정이 바뀐 조합은 새 글
    assert after[btc_key] is before[btc_key]  # 그대로인 조합은 직전 글 그대로


def test_excluding_every_coin_gives_empty_rows_in_both_paths() -> None:
    store = _small_store()
    for excluded in (["BTC"], ["btc", "ETH", "XRP"]):
        assert _text(store, now=NOW, excluded=excluded) == encode_table(
            build_table(store, now=NOW, excluded=excluded)
        )
    assert '"rows":[]' in _text(store, now=NOW, excluded=["BTC", "ETH", "XRP"])


def test_rows_older_than_300_seconds_carry_their_own_age() -> None:
    store = _small_store()
    old = next(r for r in store.get_all(exchange="binance") if r.base == "BTC")
    store.put_row(
        replace(old), NOW - timedelta(seconds=400)
    )  # 행 자체가 400초 안 바뀌었다
    got = _text(store, now=NOW)
    assert got == encode_table(build_table(store, now=NOW))
    assert (
        '"age":400.0' in got and '"age":2.0' in got
    )  # 그 행만 400초, 나머지는 스트림 기준


def test_encode_text_table_follows_the_payload_key_order() -> None:
    payload = {
        "rate": 1450.0,
        "notional": 1000.0,
        "rows": ['{"a":1}', '{"b":-0.0}'],
        "warnings": ['낡음 "q"'],
        "dataReceivedAt": None,
        "fetchedAt": 5,
    }
    as_dicts = dict(payload, rows=[{"a": 1}, {"b": -0.0}])
    assert encode_text_table(payload) == encode_table(as_dicts)


# ---- 유한하지 않은 값 — 게시기는 dict 갈래로 다시 만들어 기준과 같은 예외를 낸다 ----


def _set_price(store: LiveStore, exchange: str, base: str, price: float) -> None:
    next(r for r in store.get_all(exchange=exchange) if r.base == base).price = price


@pytest.mark.parametrize(
    "case",
    [
        "usd_nan",
        "usd_inf",
        "krw_inf",
        "rate_nan",
        "spark_nan",
        "day_chg_inf",
        "fail_row_usd_nan",
    ],
)
def test_non_finite_values_fail_exactly_like_the_dict_path(
    case: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = _small_store()
    day_open = dict(_DAY_OPEN)
    if case == "usd_nan":
        _set_price(store, "binance", "BTC", math.nan)
    elif case == "usd_inf":
        _set_price(store, "binance", "ETH", math.inf)
    elif case == "krw_inf":
        row = next(r for r in store.get_all(exchange="upbit") if r.base == "BTC")
        row.bids = [[math.inf, 2.0]]
    elif case == "rate_nan":
        rate = store.get_rate("upbit")
        assert rate is not None
        store.set_rate("upbit", math.nan, rate.bid, NOW)
    elif case == "spark_nan":
        minute = int(NOW.timestamp()) // 60
        buffer = SparkBuffer()
        buffer.seed(
            [
                SparkBucketRow("upbit", "binance", "ETH", (minute - 1 + i) * 60, v)
                for i, v in enumerate([1.0, math.nan])
            ]
        )
        buffer.publish(store)
        assert ("upbit", "binance", "ETH") not in store.spark_json()
    elif case == "day_chg_inf":
        day_open[("upbit", "BTC")] = 5e-324  # 국내가 / 기준가 가 넘쳐 inf
    elif case == "fail_row_usd_nan":
        _set_price(
            store, "binance", "XRP", math.nan
        )  # fail 행의 usd 는 0.0 이라 표는 그대로 나간다
    memo = WalletMemo()
    build_tick(store, 1, [], memo)
    expected = _outcome(
        lambda: encode_table_with_spark_json(
            build_table(store, now=NOW, day_open=day_open, wallet_memo=memo),
            store.spark_json(),
        )
    )
    assert expected == _outcome(
        lambda: encode_table(build_table(store, now=NOW, day_open=day_open))
    )
    _fix_publisher_now(monkeypatch, NOW)
    publisher = SpreadsPublisher(
        store=store, bus=None, day_open=day_open, wallet_memo=memo
    )  # type: ignore[arg-type]
    build_tick(store, 2, [], memo)
    assert (
        _outcome(publisher._encode) == expected
    )  # 같은 예외 종류·같은 메시지(또는 같은 바이트)
    if case == "fail_row_usd_nan":
        assert expected[0] == "ok"
        return
    assert expected[0] == "ValueError"
    # 글 갈래 자체는 유한성 검사(또는 같은 인코더)로 ValueError 를 낸다 — 'nan' 글자로 나가지 않는다
    with pytest.raises(ValueError):
        _text(store, now=NOW, day_open=day_open, wallet_memo=memo)
    # observe 는 그 회차를 건너뛰고 예외를 로그로 남긴다 — 틱 루프는 안 멈춘다
    publisher.observe(SimpleNamespace(ts=3))  # type: ignore[arg-type]
    assert publisher.pending == 0
    assert any("표 게시 준비 중 예외" in r.getMessage() for r in caplog.records)


# ---- 망 판정 메모의 회차 (006 §3.7) ----


def test_current_getter_reads_this_round_only() -> None:
    dom, fx = make_row("upbit", "ETH"), make_row("binance", "ETH")
    memo = WalletMemo()
    assert memo.round == 0
    memo.rotate()
    key = ("upbit", "binance", "ETH")
    verdict = memo.fields(key, dom, fx)
    assert memo.round == 1
    entry = memo.current_getter(1)(key)
    assert entry is not None and entry[6] is verdict
    assert memo.current_getter(0)(key) is None  # 다른 회차
    assert memo.current_getter(None)(key) is None  # 회차를 모르는 호출
    memo.rotate()
    assert memo.current_getter(2)(key) is None  # 새 회차엔 아직 칸이 없다


def test_table_reads_tick_cells_without_judging_and_judges_only_fail_combinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _small_store()
    calls: list[tuple[str, str]] = []
    real = networks.wallet_fields

    def counting(dom_row: Row, fx_row: Row) -> WalletFields:
        calls.append((dom_row.exchange, dom_row.base))
        return real(dom_row, fx_row)

    monkeypatch.setattr(networks, "wallet_fields", counting)
    memo = WalletMemo()
    tick = build_tick(store, 1, [], memo)
    assert {(r.dom, r.base) for r in tick.rows} == {("upbit", "BTC"), ("upbit", "ETH")}
    calls.clear()
    got = _text(
        store, now=NOW, day_open=_DAY_OPEN, wallet_memo=memo, memo_round=memo.round
    )
    assert got == GOLDEN
    assert calls == [("upbit", "XRP")]  # 틱이 안 채운 fail 조합만 판정했다


def test_a_second_table_in_the_same_round_does_not_read_stale_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """틱 없이 입출금이 바뀐 뒤 같은 회차에 표를 또 만들면 입력을 비교해 다시 판정한다 — 낡은 판정이 나가지 않는다."""
    store = _small_store()
    memo = WalletMemo()
    publisher = SpreadsPublisher(
        store=store, bus=None, day_open=_DAY_OPEN, wallet_memo=memo
    )  # type: ignore[arg-type]
    _fix_publisher_now(monkeypatch, NOW)
    build_tick(store, 1, [], memo)
    assert publisher._encode() == GOLDEN
    eth = next(r for r in store.get_all(exchange="upbit") if r.base == "ETH")
    eth.networks = [Network("ETH", "Ethereum", False, False)]  # 틱 없이 바뀐 입출금
    eth.deposit_enabled = False
    got = publisher._encode()  # 같은 회차의 두 번째 표
    assert got == encode_table(build_table(store, now=NOW, day_open=_DAY_OPEN))
    assert '"netDom":"Ethereum","depDom":false,"wdDom":false' in got
    # 회차를 넘겨도(memo_round) 회차가 다르면 같은 규칙이다
    assert (
        _text(
            store,
            now=NOW,
            day_open=_DAY_OPEN,
            wallet_memo=memo,
            memo_round=memo.round - 1,
        )
        == got
    )
