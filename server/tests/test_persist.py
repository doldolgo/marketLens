"""persist 루프 — 점 규칙·덮어쓰기·쓰기 1번·생략 (스펙 005 §3.3, §4). Influx 는 fake."""

from datetime import UTC, datetime

import httpx
import pytest

from app.core.collector import Collector
from app.core.influx import InfluxPoint, InfluxUnavailableError
from app.core.live_store import LiveStore
from app.core.persist import PersistLoop, build_points
from app.features.spreads.service import build_spreads
from tests.conftest import FakeConnector, make_row

FIXED_SEC = 1_700_000_000
FIXED_DT = datetime.fromtimestamp(FIXED_SEC, tz=UTC)


class FakeInflux:
    """(measurement, tags, ts) 를 유일키로 덮어쓰는 메모리 저장소 — Influx 의 키 규칙과 같다."""

    def __init__(self) -> None:
        self.data: dict[
            tuple[str, frozenset[tuple[str, str]], int], dict[str, float]
        ] = {}
        self.write_calls = 0
        self.fail = False

    def write(self, points: list[InfluxPoint]) -> None:
        self.write_calls += 1
        if self.fail:
            raise InfluxUnavailableError("연결 실패 (테스트)")
        for p in points:
            self.data[(p.measurement, frozenset(p.tags.items()), p.ts)] = dict(p.fields)

    def count(self, measurement: str) -> int:
        return sum(1 for key in self.data if key[0] == measurement)

    def fields_of(self, measurement: str, tags: dict[str, str]) -> dict[str, float]:
        for (m, t, _ts), fields in self.data.items():
            if m == measurement and t == frozenset(tags.items()):
                return fields
        raise AssertionError(f"{measurement} {tags} 점이 없다")


def seeded_store(*, bithumb_rate: bool = True) -> LiveStore:
    """upbit(BTC·ETH·USDT) + bithumb(BTC) + binance(BTC·ETH) — 조합 3개(환율 2곳일 때)."""
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit", "BTC", asks=[[101_000_000.0, 1.0]], bids=[[100_000_000.0, 1.0]]
            ),
            make_row(
                "upbit", "ETH", asks=[[5_050_000.0, 1.0]], bids=[[5_000_000.0, 1.0]]
            ),
            make_row("upbit", "USDT", asks=[[1401.0, 1.0]], bids=[[1399.0, 1.0]]),
        ],
        FIXED_DT,
    )
    store.replace_exchange(
        "bithumb",
        [
            make_row(
                "bithumb",
                "BTC",
                asks=[[101_100_000.0, 1.0]],
                bids=[[100_100_000.0, 1.0]],
            )
        ],
        FIXED_DT,
    )
    store.replace_exchange(
        "binance",
        [
            make_row("binance", "BTC", asks=[[71_000.0, 1.0]], bids=[[70_900.0, 1.0]]),
            make_row("binance", "ETH", asks=[[3_550.0, 1.0]], bids=[[3_540.0, 1.0]]),
        ],
        FIXED_DT,
    )
    store.set_rate("upbit", 1401.0, 1399.0, FIXED_DT)
    if bithumb_rate:
        store.set_rate("bithumb", 1402.0, 1398.0, FIXED_DT)
    store.mark_received(FIXED_SEC)
    return store


def make_loop(
    store: LiveStore, influx: FakeInflux, client: httpx.AsyncClient
) -> tuple[PersistLoop, Collector]:
    collector = Collector(
        store=store,
        domestic=[FakeConnector("upbit", [[]]), FakeConnector("bithumb", [[]])],
        foreign=FakeConnector("binance", [[]]),
        client=client,
    )
    return PersistLoop(store=store, collector=collector, influx=influx), collector


async def test_point_count_equals_pair_combinations(
    unused_client: httpx.AsyncClient,
) -> None:
    # 수집 1회 후 persist → premium 점 수 = (dom, fx, base) 조합 수 (§4)
    store = seeded_store()
    influx = FakeInflux()
    loop, _ = make_loop(store, influx, unused_client)
    written = await loop.persist_once()
    # upbit×binance: BTC·ETH, bithumb×binance: BTC — USDT 는 바이낸스에 없어 빠진다
    assert written == 3
    assert influx.count("premium") == 3


async def test_same_ts_overwrites_then_new_ts_grows(
    unused_client: httpx.AsyncClient,
) -> None:
    # 수집 없이 persist 2번 → 같은 시각이라 점 수 불변, 수집이 한 번 더 돈 뒤엔 증가 (§4)
    store = seeded_store()
    influx = FakeInflux()
    loop, _ = make_loop(store, influx, unused_client)
    await loop.persist_once()
    await loop.persist_once()
    assert influx.count("premium") == 3
    store.mark_received(FIXED_SEC + 60)  # 수집이 한 번 더 돈 것과 같은 효과
    await loop.persist_once()
    assert influx.count("premium") == 6


async def test_dom_without_rate_is_absent(unused_client: httpx.AsyncClient) -> None:
    # 환율 없는 국내 거래소는 그 회차 premium 에 dom 으로 등장하지 않는다 (§4)
    store = seeded_store(bithumb_rate=False)
    influx = FakeInflux()
    loop, _ = make_loop(store, influx, unused_client)
    written = await loop.persist_once()
    assert written == 2
    doms = {dict(key[1])["dom"] for key in influx.data if key[0] == "premium"}
    assert doms == {"upbit"}


def deep_store() -> LiveStore:
    """seeded_store 의 upbit×binance BTC 호가만 2단계로 — $10,000 이 1단계를 넘긴다."""
    store = seeded_store()
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit",
                "BTC",
                asks=[[101_000_000.0, 0.1], [102_000_000.0, 1.0]],
                bids=[[100_000_000.0, 0.05], [99_000_000.0, 1.0]],
            ),
            make_row(
                "upbit", "ETH", asks=[[5_050_000.0, 1.0]], bids=[[5_000_000.0, 1.0]]
            ),
            make_row("upbit", "USDT", asks=[[1401.0, 1.0]], bids=[[1399.0, 1.0]]),
        ],
        FIXED_DT,
    )
    store.replace_exchange(
        "binance",
        [
            make_row(
                "binance",
                "BTC",
                asks=[[71_000.0, 0.1], [72_000.0, 1.0]],
                bids=[[70_900.0, 0.05], [70_000.0, 1.0]],
            ),
            make_row("binance", "ETH", asks=[[3_550.0, 1.0]], bids=[[3_540.0, 1.0]]),
        ],
        FIXED_DT,
    )
    return store


async def test_values_are_the_raw_premium_behind_spreads(
    unused_client: httpx.AsyncClient,
) -> None:
    # premium 은 슬리피지 차감 **전** 원값이다 — /spreads 의 fwd + slipFwd 와 같다
    # (003 §2·§4, 005 §4). 저장 시점에는 체결 규모가 정의되지 않기 때문이다.
    store = deep_store()
    influx = FakeInflux()
    loop, _ = make_loop(store, influx, unused_client)
    await loop.persist_once()
    spreads = build_spreads(store, now=FIXED_DT)
    # 차감이 0 인 시드면 이 테스트가 아무것도 고정하지 못한다
    assert any(row.slip_fwd > 0 and row.slip_rev > 0 for row in spreads.rows)
    for row in spreads.rows:
        fields = influx.fields_of(
            "premium", {"dom": row.dom, "fx": row.fx, "base": row.sym}
        )
        assert fields["fwd"] == pytest.approx(row.fwd + row.slip_fwd)
        assert fields["rev"] == pytest.approx(row.rev + row.slip_rev)


async def test_dw_fail_point_per_failed_exchange(
    unused_client: httpx.AsyncClient,
) -> None:
    # 입출금 조회 실패 사이클 → 그 거래소 dw_fail 1점, 실패 없으면 0점 (§4)
    store = seeded_store()
    influx = FakeInflux()
    loop, collector = make_loop(store, influx, unused_client)
    await loop.persist_once()
    assert influx.count("dw_fail") == 0
    collector.dw_failed = ["upbit"]  # 006 이 채우게 될 자리를 흉내낸다
    store.mark_received(FIXED_SEC + 60)
    await loop.persist_once()
    assert influx.count("dw_fail") == 1
    assert influx.fields_of("dw_fail", {"exchange": "upbit"}) == {"v": 1.0}


async def test_one_write_per_round(unused_client: httpx.AsyncClient) -> None:
    # 한 회차의 점이 한 번의 쓰기로 나간다 (§4)
    store = seeded_store()
    influx = FakeInflux()
    loop, collector = make_loop(store, influx, unused_client)
    collector.dw_failed = ["binance"]
    await loop.persist_once()
    assert influx.write_calls == 1


async def test_persist_before_any_collect_writes_nothing(
    unused_client: httpx.AsyncClient,
) -> None:
    # 수집은 아직 안 돌았는데 persist 호출 → 아무것도 쓰지 않고 0 반환 (§4)
    store = LiveStore()
    influx = FakeInflux()
    loop, _ = make_loop(store, influx, unused_client)
    assert await loop.persist_once() == 0
    assert influx.write_calls == 0


async def test_no_rate_at_all_skips_round(unused_client: httpx.AsyncClient) -> None:
    # 환율이 하나도 없으면 경고 로그 후 이번 회차 생략 (§3.3)
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [make_row("upbit", "BTC", asks=[[101.0, 1.0]], bids=[[100.0, 1.0]])],
        FIXED_DT,
    )
    store.replace_exchange(
        "binance",
        [make_row("binance", "BTC", asks=[[0.08, 1.0]], bids=[[0.07, 1.0]])],
        FIXED_DT,
    )
    store.mark_received(FIXED_SEC)  # 수집은 돌았지만 환율 관측이 없던 상태
    influx = FakeInflux()
    loop, _ = make_loop(store, influx, unused_client)
    assert await loop.persist_once() == 0
    assert influx.write_calls == 0


async def test_write_failure_logs_and_returns_zero(
    unused_client: httpx.AsyncClient,
) -> None:
    # 저장 실패는 로그 후 다음 회차 재시도 — 예외가 새어나가지 않는다 (§3.3)
    store = seeded_store()
    influx = FakeInflux()
    influx.fail = True
    loop, _ = make_loop(store, influx, unused_client)
    assert await loop.persist_once() == 0
    influx.fail = False
    assert await loop.persist_once() == 3


def test_build_points_pure_function() -> None:
    # 빌더 단독 — dw_failed 인자가 dw_fail 점이 되고 time 은 수집 시각이다
    store = seeded_store()
    points, skip = build_points(store, ["bithumb"])
    assert skip is None
    assert all(p.ts == FIXED_SEC for p in points)
    assert sum(1 for p in points if p.measurement == "dw_fail") == 1
