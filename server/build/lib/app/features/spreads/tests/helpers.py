"""spreads 테스트 공용 도구 — 네트워크 없음, 저장소에 직접 시드 (스펙 003 §4)."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.collector import CycleResult
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.networks import Network
from app.main import create_app


def make_row(
    exchange: str,
    base: str,
    *,
    quote: str | None = None,
    price: float = 100.0,
    asks: list[list[float]] | None = None,
    bids: list[list[float]] | None = None,
    dep: bool | None = None,
    wd: bool | None = None,
    networks: list[Network] | None = None,
) -> Row:
    if quote is None:
        quote = "USDT" if exchange == "binance" else "KRW"
    native = f"{base}USDT" if exchange == "binance" else f"KRW-{base}"
    return Row(
        exchange=exchange,
        base=base,
        quote=quote,
        native_symbol=native,
        price=price,
        asks=asks if asks is not None else [[101.0, 1.0]],
        bids=bids if bids is not None else [[99.0, 2.0]],
        price_timestamp=1_700_000_000_000,
        deposit_enabled=dep,
        withdrawal_enabled=wd,
        networks=networks if networks is not None else [],
    )


class FakeCollector:
    """미리 정한 CycleResult 를 돌려주는 가짜 — /refresh 가 거래소를 부르지 않게."""

    def __init__(self, result: CycleResult) -> None:
        self._result = result
        self.cycles = 0

    async def run_cycle(self) -> CycleResult:
        self.cycles += 1
        return self._result


def make_cycle_result(
    *,
    saved: dict[str, int] | None = None,
    rates_observed: list[str] | None = None,
    failures: list[dict[str, str]] | None = None,
    warnings: list[str] | None = None,
    calls: dict[str, int] | None = None,
    wallet_status_available: dict[str, bool] | None = None,
) -> CycleResult:
    return CycleResult(
        saved=saved if saved is not None else {"upbit": 0, "bithumb": 0, "binance": 0},
        rates_observed=rates_observed or [],
        failures=failures or [],
        warnings=warnings or [],
        calls=calls or {},
        duration_ms=12.5,
        fetched_at=1_787_139_510_000,
        wallet_status_available=wallet_status_available or {},
    )


def make_app(
    store: LiveStore | None = None,
    *,
    refresh_token: str | None = None,
    collector: FakeCollector | None = None,
) -> FastAPI:
    """lifespan 없이 앱을 만들고 상태를 직접 채운다 — 수집 루프·네트워크가 돌지 않는다."""
    app = create_app()
    app.state.live_store = store if store is not None else LiveStore()
    # 실제 .env·OS env 에 의존하지 않도록 설정을 스텁으로 바꾼다
    app.state.settings = SimpleNamespace(refresh_token=refresh_token)
    if collector is not None:
        app.state.collector = collector
    return app


def make_client(
    store: LiveStore | None = None,
    *,
    refresh_token: str | None = None,
    collector: FakeCollector | None = None,
) -> TestClient:
    return TestClient(make_app(store, refresh_token=refresh_token, collector=collector))
