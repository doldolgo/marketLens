"""spreads 테스트 공용 도구 — 네트워크 없음, 저장소에 직접 시드 (스펙 003 §4)."""

import json
from datetime import datetime
from types import SimpleNamespace

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.collect import RefreshSummary
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.networks import Network
from app.core.redis_bus import RedisBus
from app.features.spreads.push import encode_table
from app.features.spreads.service import build_table
from app.main import create_app


def make_bus() -> tuple[RedisBus, fakeredis.FakeServer]:
    """fakeredis 위의 버스 — 같은 server 로 두 번째 클라이언트를 만들면 구독·게시 양쪽을 흉내낼 수 있다."""
    server = fakeredis.FakeServer()
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server)), server


def spreads_json(store: LiveStore, **build_kw: object) -> dict:
    """저장소로 만든 표를 `GET /spreads` 가 돌려주는 바이트 그대로(017 게시기 인코딩) 파싱한 것.

    018 부터 HTTP 는 Redis 키를 그대로 답하므로 표 **계산** 규칙(003·006·008)은 이 헬퍼로 본다 —
    게시기가 키에 넣는 것과 같은 함수·같은 직렬화라 HTTP 로 받을 값과 같다.
    """
    return json.loads(encode_table(build_table(store, **build_kw)))  # type: ignore[arg-type]


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


def seed_rows(store: LiveStore, rows: list[Row], now: datetime) -> None:
    """행을 시드하면서 그 거래소 스트림의 수신 시각도 `now` 로 둔다.

    런타임에서 행은 스트림 메시지로만 생기므로 행이 있는 거래소의 `last_message_at` 은 항상
    있다 — 저장소를 직접 시드하는 테스트만 이 헬퍼로 같은 전제를 만든다(`age` 는 스트림 기준).
    """
    store.put_rows(rows, now)
    for exchange in {row.exchange for row in rows}:
        store.stream(exchange).last_message_at = int(now.timestamp() * 1000)


class FakeCollector:
    """미리 정한 RefreshSummary 를 돌려주는 가짜 — /refresh 가 거래소를 부르지 않게."""

    def __init__(self, result: RefreshSummary) -> None:
        self._result = result
        self.cycles = 0

    async def refresh_now(self) -> RefreshSummary:
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
) -> RefreshSummary:
    return RefreshSummary(
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
    bus: RedisBus | None = None,
) -> FastAPI:
    """lifespan 없이 앱을 만들고 상태를 직접 채운다 — 수집 루프·네트워크가 돌지 않는다.

    `bus` 는 GET /spreads 가 읽는 Redis 자리(018) — 안 주면 빈 fakeredis 라 키가 없어 404 다.
    """
    app = create_app()
    app.state.live_store = store if store is not None else LiveStore()
    # 실제 .env·OS env 에 의존하지 않도록 설정을 스텁으로 바꾼다
    app.state.settings = SimpleNamespace(refresh_token=refresh_token)
    if collector is not None:
        app.state.collector = collector
    app.state.spreads_bus = bus if bus is not None else make_bus()[0]
    return app


def make_client(
    store: LiveStore | None = None,
    *,
    refresh_token: str | None = None,
    collector: FakeCollector | None = None,
    bus: RedisBus | None = None,
) -> TestClient:
    return TestClient(
        make_app(store, refresh_token=refresh_token, collector=collector, bus=bus)
    )
