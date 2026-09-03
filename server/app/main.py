"""앱 진입점 — 골격·에러 형식·/health·수집 루프 기동 (스펙 001).

/health 와 수집 루프는 기능 폴더가 아니라 여기(시스템) 소관이다.
메모리가 진실이므로 uvicorn 워커는 1개여야 한다 — 워커가 둘이면 서로 다른 메모리를 본다.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.collector import Collector
from app.core.config import (
    APP_NAME,
    APP_VERSION,
    EXCHANGE_TIMEOUT_CONNECT,
    EXCHANGE_TIMEOUT_TOTAL,
    USER_AGENT,
    get_settings,
)
from app.core.connectors.binance import BinanceConnector
from app.core.connectors.bithumb import BithumbConnector
from app.core.connectors.upbit import UpbitConnector
from app.core.errors import ExchangeError
from app.core.influx import InfluxClient
from app.core.live_store import LiveStore
from app.core.persist import PersistLoop
from app.core.s3 import S3Uploader
from app.core.serialization import camelize_json
from app.core.snapshot import SnapshotLoop
from app.features.analysis.router import router as analysis_router
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
    store = LiveStore()
    settings = app.state.settings
    # 입출금 상태 60초 캐시(006) — 키 없는 거래소는 unknown, 빗썸은 키 불필요
    wallet = WalletStatusService(
        upbit_api_key=settings.upbit_api_key,
        upbit_secret_key=settings.upbit_secret_key,
        binance_api_key=settings.binance_api_key,
        binance_secret_key=settings.binance_secret_key,
    )
    collector = Collector(
        store=store,
        domestic=[UpbitConnector(), BithumbConnector()],
        foreign=BinanceConnector(),
        client=client,
        wallet=wallet,
    )
    app.state.live_store = store
    app.state.collector = collector

    # Influx — 토큰 없으면 저장 루프 비활성·/history/* 503, 앱은 뜬다 (스펙 005 §3.1)
    influx: InfluxClient | None = None
    persist_task: asyncio.Task[None] | None = None
    if settings.influx_token:
        influx = InfluxClient(url=settings.influx_url, token=settings.influx_token)
        if not await asyncio.to_thread(influx.ping):
            # 기동 시 연결 실패는 에러 로그 1줄 — 저장 루프가 다음 회차에 재시도한다
            logger.error(
                "InfluxDB 연결 실패: %s — 저장 루프가 회차마다 재시도한다",
                settings.influx_url,
            )
        persist = PersistLoop(store=store, collector=collector, influx=influx)
        persist_task = asyncio.create_task(persist.run_loop())
    else:
        logger.warning(
            "INFLUX_TOKEN 이 없어 저장 루프를 켜지 않는다 — /history/* 는 503"
        )
    app.state.influx = influx

    # S3 snapshot — 버킷 없으면 루프 비활성, 앱은 뜬다. persist 루프와 독립 (스펙 010 §3.2)
    s3: S3Uploader | None = None
    snapshot_task: asyncio.Task[None] | None = None
    if settings.s3_bucket:
        s3 = S3Uploader(bucket=settings.s3_bucket, region=settings.s3_region)
        if not await asyncio.to_thread(s3.head_bucket):
            # 기동 시 접근 확인 실패는 에러 로그 1줄 — 루프가 회차마다 다시 시도한다 (§3.3)
            logger.error(
                "S3 버킷 접근 확인 실패: %s — snapshot 루프가 회차마다 재시도한다",
                settings.s3_bucket,
            )
        snapshot = SnapshotLoop(store=store, collector=collector, s3=s3)
        snapshot_task = asyncio.create_task(snapshot.run_loop())
    else:
        logger.warning("S3_BUCKET 이 없어 snapshot 루프를 켜지 않는다")

    task = asyncio.create_task(collector.run_loop())
    try:
        yield
    finally:
        task.cancel()
        if persist_task is not None:
            persist_task.cancel()
        if snapshot_task is not None:
            snapshot_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if persist_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await persist_task
        if snapshot_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await snapshot_task
        if influx is not None:
            influx.close()
        if s3 is not None:
            s3.close()
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
        # 수집 루프 상태와 무관하게 항상 ok
        return {"status": "ok", "version": APP_VERSION}

    app.include_router(spreads_router)
    app.include_router(analysis_router)
    app.include_router(history_router)

    return app


app = create_app()
