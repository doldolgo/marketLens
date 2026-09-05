"""앱 진입점 — 골격·에러 형식·/health·수집 배선 (스펙 001).

/health 와 틱 루프는 기능 폴더가 아니라 여기(시스템) 소관이다.
메모리가 진실이므로 uvicorn 워커는 1개여야 한다 — 워커가 둘이면 서로 다른 메모리를 본다.
시작 순서: 011 이력 복원 → 마켓 우주 → 스트림 기동 → 틱 루프. 어느 것이 실패해도 앱은 뜬다.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.collect import CollectService
from app.core.config import (
    APP_NAME,
    APP_VERSION,
    EXCHANGE_TIMEOUT_CONNECT,
    EXCHANGE_TIMEOUT_TOTAL,
    USER_AGENT,
    get_settings,
)
from app.core.contracts import NoForeignSymbols, noop_handoff, noop_record
from app.core.errors import ExchangeError
from app.core.influx import InfluxClient
from app.core.live_store import LiveStore
from app.core.outages import OutageTracker
from app.core.quotes import QuoteSink
from app.core.serialization import camelize_json
from app.core.streams.bithumb import BithumbStream
from app.core.streams.upbit import UpbitStream
from app.core.ticks import TickLoop
from app.core.universe import UniverseRefresher
from app.features.analysis.router import router as analysis_router
from app.features.health.router import router as health_router
from app.features.history.router import router as history_router
from app.features.spreads.router import router as spreads_router
from app.features.wallet_status.service import WalletStatusService

logger = logging.getLogger("marketlens.main")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(EXCHANGE_TIMEOUT_TOTAL, connect=EXCHANGE_TIMEOUT_CONNECT),
        headers={"User-Agent": USER_AGENT},
    )
    settings = app.state.settings
    store = LiveStore()
    sink = QuoteSink(store)
    # 원문 싱크(010)·틱 인계(009)·바이낸스 심볼(012)은 아직 없다 — 아무것도 하지 않는 구현
    record = noop_record
    handoff = noop_handoff
    foreign = NoForeignSymbols()

    # 입출금 상태 60초 캐시(006) — 키 없는 거래소는 unknown, 빗썸은 키 불필요
    wallet = WalletStatusService(
        upbit_api_key=settings.upbit_api_key,
        upbit_secret_key=settings.upbit_secret_key,
        binance_api_key=settings.binance_api_key,
        binance_secret_key=settings.binance_secret_key,
    )

    # Influx — 토큰 없으면 /history/* 503·이력 복원 없음, 앱은 뜬다 (스펙 005·011)
    influx: InfluxClient | None = None
    if settings.influx_token:
        influx = InfluxClient(url=settings.influx_url, token=settings.influx_token)
        if not await asyncio.to_thread(influx.ping):
            logger.error(
                "InfluxDB 연결 실패: %s — 회차마다 재시도한다", settings.influx_url
            )
    else:
        logger.warning("INFLUX_TOKEN 이 없어 Influx 를 쓰지 않는다 — /history/* 는 503")
    app.state.influx = influx

    # 1. 수집 실패 이력(011) 복원 — 틱 루프 시작 전에 끝난다. 쓰기는 별도 태스크가 순서대로.
    outages = OutageTracker(writer=influx)
    app.state.started_at = int(time.time() * 1000)
    await outages.restore(influx, app.state.started_at)
    outage_writer_task = asyncio.create_task(outages.run_writer_loop())
    app.state.outages = outages

    # 2~3. 마켓 우주 → 스트림 기동. 목록을 못 받으면 5초 간격 재시도, 그동안 구독은 없다.
    upbit = UpbitStream(store=store, sink=sink, record=record)
    bithumb = BithumbStream(store=store, sink=sink, record=record)
    streams = [upbit, bithumb]
    universe = UniverseRefresher(
        sink=sink, streams=streams, foreign=foreign, client=client
    )
    universe.start()
    for stream in streams:
        stream.start()

    # 4. 틱 루프(1초) — 틱 생성·인계·판정
    ticks = TickLoop(
        store=store,
        streams=streams,
        client=client,
        handoff=handoff,
        outages=outages,
        wallet=wallet,
    )
    ticks.start()

    app.state.live_store = store
    app.state.collector = CollectService(
        store=store, universe=universe, streams=streams, client=client, wallet=wallet
    )
    try:
        yield
    finally:
        await ticks.aclose()  # 슬롯의 마지막 틱을 인계한다
        await universe.aclose()
        await asyncio.gather(*(s.aclose() for s in streams))
        outage_writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await outage_writer_task
        if influx is not None:
            influx.close()
        await client.aclose()


def _error_body(code: str, message: str, detail: object) -> dict[str, object]:
    """앱 에러 응답 형식은 항상 이 모양이다 (architecture.md 계약 규칙)."""
    return camelize_json(
        {"error": {"code": code, "message": message, "detail": detail}}
    )


async def _exchange_error_handler(request: Request, exc: ExchangeError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content=_error_body(exc.code, exc.message, exc.detail()),
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "http_error"
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, str(exc.detail), None),
    )


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=_lifespan)
    app.state.settings = get_settings()

    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    app.add_middleware(GZipMiddleware)

    app.add_exception_handler(ExchangeError, _exchange_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)

    @app.get("/health")
    async def health() -> dict[str, str]:
        # 스트림 상태와 무관하게 항상 ok — 프로세스 liveness 만 나타낸다
        return {"status": "ok", "version": APP_VERSION}

    app.include_router(spreads_router)
    app.include_router(analysis_router)
    app.include_router(history_router)
    app.include_router(health_router)

    return app


app = create_app()
