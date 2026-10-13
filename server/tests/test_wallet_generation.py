"""입출금 캐시 반영 — 조회 결과가 바뀐 틱에만 전 행, 그 사이에는 새로 생긴 행만 (스펙 001 §3.6-1·006, 2026-10-09).

결과는 매 틱 전 행에 반영하던 때와 같아야 한다. 두 세계를 나란히 돌린다 — 같은 실제 WalletStatusService 를 쓰되,
한쪽 틱 루프는 세대(`generation`)를 보고 다른 쪽은 세대를 가린 감싸개를 받아 바꾸기 전처럼 매 틱 전 행에 반영한다.
같은 조작열(행 교체·새 행·삭제 뒤 재생성·우주 축소·실제 _fetch_one 의 성공·WalletStatusError·예상 밖 예외·
/refresh 경로의 전 행 반영)을 먹이고 매 틱 모든 행의 3필드(망 목록은 객체 동일성까지)와 틱 행을 대조한다.
"""

import asyncio
import random
from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import EXCHANGES
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.networks import Network
from app.core.ticks import TickLoop
from app.features.wallet_status.models import CoinStatus, WalletStatusError
from app.features.wallet_status.service import WalletStatusService
from tests.conftest import make_row

NOW = datetime.now(UTC)
T0 = 1_787_000_000
_BASES = ("BTC", "ETH", "XRP", "SOL", "doge", "TRX", "LINK")
_QUOTE = {"upbit": "KRW", "bithumb": "KRW"}


def _client() -> httpx.AsyncClient:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"테스트에서 네트워크 호출 발생: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(fail))


def _service() -> WalletStatusService:
    return WalletStatusService(
        upbit_api_key=None,
        upbit_secret_key=None,
        binance_api_key=None,
        binance_secret_key=None,
    )


class FullEveryTick:
    """세대를 가린 감싸개 — 틱 루프가 바뀐 때를 몰라 매 틱 전 행에 반영한다(바꾸기 전 동작)."""

    def __init__(self, inner: WalletStatusService) -> None:
        self._inner = inner

    async def refresh_if_due(
        self, client: httpx.AsyncClient, *, force: bool = False
    ) -> dict[str, int] | None:
        return None

    def apply(self, rows: list[Row], exchange: str) -> None:
        self._inner.apply(rows, exchange)

    def availability(self) -> dict[str, bool]:
        return self._inner.availability()

    def warnings(self) -> list[str]:
        return self._inner.warnings()

    def failed(self) -> list[str]:
        return self._inner.failed()


def _row(rng: random.Random, ex: str, base: str) -> Row:
    bid = rng.uniform(90.0, 110.0)
    return make_row(
        ex,
        base,
        quote=_QUOTE.get(ex, "USDT"),
        price=bid,
        bids=[[bid, 1.0]],
        asks=[[bid * 1.001, 1.0]],
    )


def _statuses(rng: random.Random) -> dict[str, CoinStatus]:
    out: dict[str, CoinStatus] = {}
    for base in _BASES:
        if rng.random() < 0.3:
            continue  # 응답에 그 코인이 없음 → unknown
        nets = [
            Network(code, code.title(), rng.random() < 0.7, rng.random() < 0.7)
            for code in rng.sample(("ETH", "SOL", "TRX", "BSC"), rng.randint(0, 3))
        ]
        out[base.upper()] = CoinStatus(rng.random() < 0.8, rng.random() < 0.8, nets)
    return out


async def _fetch(service: WalletStatusService, rng: random.Random, ex: str) -> None:
    roll = rng.random()

    async def ok() -> dict[str, CoinStatus]:
        return _statuses(rng)

    async def fails() -> dict[str, CoinStatus]:
        raise WalletStatusError("점검 중", calls=1)

    async def bug() -> dict[str, CoinStatus]:
        raise KeyError("data")

    coro = ok() if roll < 0.7 else fails() if roll < 0.85 else bug()
    await service._fetch_one(ex, coro)  # 실제 조회 1건 경로 — 상태 대입과 세대 올리기


def _snapshot(
    store: LiveStore,
) -> dict[tuple[str, str], tuple[object, object, list[Network]]]:
    return {
        (row.exchange, row.base.upper()): (
            row.deposit_enabled,
            row.withdrawal_enabled,
            row.networks,
        )
        for row in store.get_all()
    }


def _assert_same(a: LiveStore, b: LiveStore, *, identity: bool) -> None:
    sa, sb = _snapshot(a), _snapshot(b)
    assert sa.keys() == sb.keys()
    for key, (dep, wd, nets) in sa.items():
        dep_b, wd_b, nets_b = sb[key]
        assert (dep, wd) == (dep_b, wd_b), key
        if identity:
            # 틱 뒤에는 두 세계 모두 캐시의 같은 망 목록 객체(또는 같은 빈 목록)를 건다 — 망 판정 메모가 기대는 성질
            assert nets is nets_b, key
        else:
            assert nets == nets_b, key


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_generation_path_matches_applying_to_every_row_every_tick(
    seed: int,
) -> None:
    rng = random.Random(seed)
    service = _service()
    new_store, ref_store = LiveStore(), LiveStore()
    client = _client()
    new_loop = TickLoop(store=new_store, streams=[], client=client, wallet=service)
    ref_loop = TickLoop(
        store=ref_store, streams=[], client=client, wallet=FullEveryTick(service)
    )  # type: ignore[arg-type]
    for store in (new_store, ref_store):
        store.set_rate("upbit", 1400.0, 1390.0, NOW)
        store.set_rate("bithumb", 1401.0, 1391.0, NOW)

    ts = T0
    gens_at_tick: list[int] = []
    for _ in range(1_500):
        op = rng.random()
        ex = rng.choice(EXCHANGES)
        base = rng.choice(_BASES)
        if op < 0.45:  # 행 교체 또는 새 행
            seed_row = rng.random()
            for store in (new_store, ref_store):
                store.put_row(_row(random.Random(seed_row), ex, base), NOW)
        elif op < 0.52:
            for store in (new_store, ref_store):
                store.remove_row(ex, base)
        elif op < 0.54:
            keep = set(rng.sample(_BASES, rng.randint(2, len(_BASES))))
            for store in (new_store, ref_store):
                store.retain_bases(keep)
        elif op < 0.62:
            await _fetch(service, rng, ex)
        elif op < 0.64:
            # /refresh 경로(collect.refresh_now) — 틱 밖에서 전 행에 반영
            for store in (new_store, ref_store):
                for e in EXCHANGES:
                    service.apply(store.get_all(exchange=e), e)
        else:
            new_tick = new_loop.tick(ts)
            ref_tick = ref_loop.tick(ts)
            ts += 1
            gens_at_tick.append(service.generation)
            assert new_tick.rows == ref_tick.rows
            assert new_tick.dw_failed == ref_tick.dw_failed
            _assert_same(new_store, ref_store, identity=True)
            continue
        _assert_same(new_store, ref_store, identity=False)
    # 대조가 의미 있으려면 세대가 그대로인 틱(새 행 경로)과 바뀐 틱(전 행 경로)이 둘 다 많아야 한다
    same = sum(
        1 for a, b in zip(gens_at_tick, gens_at_tick[1:], strict=False) if a == b
    )
    assert same > 200 and len(gens_at_tick) - same > 50


async def test_tick_during_a_pending_fetch_does_not_keep_stale_rows() -> None:
    # 세대는 상태를 넣은 뒤에 오른다 — 응답을 기다리는 사이 틱이 끼어도 응답이 들어온 다음 틱이 전 행에 반영한다
    service = _service()
    store = LiveStore()
    store.put_rows([make_row("upbit", "BTC"), make_row("upbit", "ETH")], NOW)
    loop = TickLoop(store=store, streams=[], client=_client(), wallet=service)
    loop.tick(T0)
    assert store.get("upbit", "BTC").deposit_enabled is None  # type: ignore[union-attr]

    gate = asyncio.Event()
    nets = [Network("BTC", "Bitcoin", True, True)]

    async def slow() -> dict[str, CoinStatus]:
        await gate.wait()
        return {
            "BTC": CoinStatus(True, False, nets),
            "ETH": CoinStatus(False, True, []),
        }

    fetch = asyncio.create_task(service._fetch_one("upbit", slow()))
    await asyncio.sleep(0)  # 조회가 응답 대기에 들어갔다
    loop.tick(T0 + 1)  # 응답 전 틱 — 아직 바뀐 것이 없다
    gate.set()
    await fetch
    loop.tick(T0 + 2)
    btc, eth = store.get("upbit", "BTC"), store.get("upbit", "ETH")
    assert btc is not None and eth is not None
    assert (btc.deposit_enabled, btc.withdrawal_enabled) == (
        True,
        False,
    ) and btc.networks is nets
    assert (eth.deposit_enabled, eth.withdrawal_enabled) == (False, True)


async def test_new_row_between_generations_gets_the_cache_on_the_next_tick() -> None:
    service = _service()
    nets = [Network("SOL", "Solana", True, True)]

    async def ok() -> dict[str, CoinStatus]:
        return {"SOL": CoinStatus(True, True, nets)}

    await service._fetch_one("bithumb", ok())
    store = LiveStore()
    loop = TickLoop(store=store, streams=[], client=_client(), wallet=service)
    loop.tick(T0)  # 첫 틱은 전 행(빈 저장소)
    row = make_row("bithumb", "sol")
    store.put_row(row, NOW)  # 세대 사이에 새로 생긴 행(소문자 base)
    assert row.deposit_enabled is None
    loop.tick(T0 + 1)
    assert (row.deposit_enabled, row.withdrawal_enabled) == (
        True,
        True,
    ) and row.networks is nets
    # 교체된 행은 물려받는다 — 새 행 목록에 들어가지 않는다
    replaced = make_row("bithumb", "SOL")
    store.put_row(replaced, NOW)
    assert store.take_new_rows() == {}
    assert replaced.networks is nets


async def test_apply_error_on_the_new_row_path_is_repaired_by_a_full_pass_next_tick() -> (
    None
):
    service = _service()
    nets = [Network("ETH", "Ethereum", True, False)]

    async def ok() -> dict[str, CoinStatus]:
        return {"ETH": CoinStatus(True, False, nets)}

    await service._fetch_one("upbit", ok())

    class Flaky:
        """세대는 실제 조회기 것을 그대로 내고, 한 번만 apply 가 예외를 낸다."""

        def __init__(self) -> None:
            self.fail_next = False

        @property
        def generation(self) -> int:
            return service.generation

        async def refresh_if_due(
            self, client: httpx.AsyncClient, *, force: bool = False
        ) -> None:
            return None

        def apply(self, rows: list[Row], exchange: str) -> None:
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("반영 버그")
            service.apply(rows, exchange)

        def availability(self) -> dict[str, bool]:
            return service.availability()

        def warnings(self) -> list[str]:
            return service.warnings()

        def failed(self) -> list[str]:
            return service.failed()

    wallet = Flaky()
    store = LiveStore()
    loop = TickLoop(store=store, streams=[], client=_client(), wallet=wallet)  # type: ignore[arg-type]
    loop.tick(T0)
    row = make_row("upbit", "ETH")
    store.put_row(row, NOW)
    wallet.fail_next = True
    with pytest.raises(RuntimeError):
        loop.tick(T0 + 1)  # 새 행 경로에서 예외 — 새 행 목록은 이미 비었다
    assert row.deposit_enabled is None
    loop.tick(T0 + 2)  # 세대를 모르는 상태라 전 행으로 다시 반영한다
    assert (row.deposit_enabled, row.withdrawal_enabled) == (
        True,
        False,
    ) and row.networks is nets


def test_take_new_rows_returns_current_rows_of_new_keys_once() -> None:
    store = LiveStore()
    a1, b1 = make_row("upbit", "btc"), make_row("binance", "ETH")
    store.put_rows([a1, b1], NOW)
    a2 = make_row("upbit", "BTC")
    store.put_row(a2, NOW)  # 교체 — 키는 이미 새 행 목록에 있고 지금 행이 나와야 한다
    store.remove_row("binance", "eth")  # 지워진 키는 빠진다
    assert store.take_new_rows() == {"upbit": [a2]}
    assert store.take_new_rows() == {}
    store.put_row(make_row("binance", "ETH"), NOW)  # 지운 뒤 다시 생기면 새 행
    [again] = store.take_new_rows()["binance"]
    assert again.base == "ETH"


async def test_fetch_one_raises_generation_once_per_state_change() -> None:
    service = _service()
    assert service.generation == 0

    async def ok() -> dict[str, CoinStatus]:
        return {}

    async def fails() -> dict[str, CoinStatus]:
        raise WalletStatusError("키 없음", calls=0)

    async def bug() -> dict[str, CoinStatus]:
        raise ValueError("파싱")

    await service._fetch_one("upbit", ok())
    await service._fetch_one("binance", fails())
    await service._fetch_one("okx", bug())
    assert service.generation == 3
