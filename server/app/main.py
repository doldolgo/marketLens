"""앱 진입점 — 골격·에러 형식·/health·수집 배선 (스펙 001).

/health 와 틱 루프는 기능 폴더가 아니라 여기(시스템) 소관이다.
메모리가 진실이므로 uvicorn 워커는 1개여야 한다 — 워커가 둘이면 서로 다른 메모리를 본다.
시작 순서: Influx·Redis 연결 확인 → 010 원문 아카이브(S3_BUCKET 있을 때) → 011 이력 복원 → 013 사건 복원 → 009 spark 복원
→ 마켓 우주 → 스트림 기동(국내 2 + 바이낸스 3샤드) → 틱 루프 → 009 인계 보내기 태스크·flusher.
어느 것이 실패해도 앱은 뜬다.
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
from app.core.contracts import noop_record
from app.core.errors import ExchangeError
from app.core.influx import InfluxClient
from app.core.live_store import LiveStore
from app.core.outages import OutageTracker
from app.core.premium_events import PremiumEventDetector
from app.core.quotes import QuoteSink
from app.core.raw_archive import RawArchive
from app.core.redis_stream import RedisTickStream
from app.core.s3 import S3Uploader
from app.core.serialization import camelize_json
from app.core.spark import SparkBuffer, restore_spark
from app.core.streams.binance import BinanceStream
from app.core.streams.bithumb import BithumbStream
from app.core.streams.upbit import UpbitStream
from app.core.tick_store import Flusher, TickRelay
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

    # 원문 아카이브(010) — S3_BUCKET 이 없으면 비활성(기록 함수는 무동작), 앱은 뜬다.
    # 클라이언트 생성 실패도 비활성 + 에러 1줄. HeadBucket 실패는 에러 1줄뿐이다 — 자격증명이
    # 없어도 뜨고, 이후 실패는 업로드 워커 로그로만.
    archive: RawArchive | None = None
    record = noop_record
    uploader: S3Uploader | None = None
    if not settings.s3_bucket:
        logger.warning(
            "S3_BUCKET 이 없어 원문 아카이브를 쓰지 않는다 — 원문은 남지 않는다"
        )
    else:
        try:
            uploader = S3Uploader(bucket=settings.s3_bucket, region=settings.s3_region)
        except Exception as exc:
            logger.error(
                "S3 클라이언트 생성 실패 (region=%s) — 원문 아카이브를 쓰지 않는다: %r",
                settings.s3_region,
                exc,
            )
    if uploader is not None:
        try:
            await asyncio.to_thread(uploader.head_bucket)
        except Exception as exc:
            logger.error(
                "S3 버킷 %s 접근 실패 — 원문 업로드는 워커가 객체마다 다시 시도한다: %r",
                settings.s3_bucket,
                exc,
            )
        archive = RawArchive(uploader=uploader)
        record = archive.record
        archive.start()

    # 입출금 상태 60초 캐시(006) — 키 없는 거래소는 unknown, 빗썸은 키 불필요.
    # 응답 본문은 시세 원문과 같은 싱크(010)에 남는다.
    wallet = WalletStatusService(
        upbit_api_key=settings.upbit_api_key,
        upbit_secret_key=settings.upbit_secret_key,
        binance_api_key=settings.binance_api_key,
        binance_secret_key=settings.binance_secret_key,
        record=record,
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

    # Redis — 틱 버퍼(009). 불달이면 경고 1줄, 인계된 틱은 버려지고 앱은 뜬다
    tick_stream = RedisTickStream.from_url(settings.redis_url)
    if not await tick_stream.ping():
        logger.warning(
            "Redis 연결 실패: %s — 인계된 틱은 버려진다 (명령마다 재시도)",
            settings.redis_url,
        )
    spark = SparkBuffer()
    handoff = TickRelay(stream=tick_stream, store=store, spark=spark)

    # 1. 수집 실패 이력(011) 복원 — 틱 루프 시작 전에 끝난다. 쓰기는 별도 태스크가 순서대로.
    outages = OutageTracker(writer=influx)
    app.state.started_at = int(time.time() * 1000)
    await outages.restore(influx, app.state.started_at)
    outage_writer_task = asyncio.create_task(outages.run_writer_loop())
    app.state.outages = outages

    # 1-1. 김프/역프 사건(013) 복원 — 7일 안의 진행 중 사건, 3초 상한. 쓰기는 별도 태스크가 회차마다.
    events = PremiumEventDetector(writer=influx)
    await events.restore(influx, app.state.started_at // 1000)
    event_writer_task = asyncio.create_task(events.run_writer_loop())
    app.state.premium_events = events

    # 1-2. spark 복원(009 §3.6) — 최근 30분 1분 버킷, 10초 상한. 틱 루프 시작 전에 끝난다.
    await restore_spark(influx, spark, store, app.state.started_at // 1000)

    # 2~3. 마켓 우주(매초) → 스트림 기동. 목록을 못 받은 거래소는 다음 초에 다시 — 그동안 구독은 없다.
    # 바이낸스 커넥터(012)가 심볼 집합 계약도 맡는다 — 우주가 확정되면 그 심볼만 구독한다.
    upbit = UpbitStream(store=store, sink=sink, record=record)
    bithumb = BithumbStream(store=store, sink=sink, record=record)
    binance = BinanceStream(store=store, sink=sink, record=record)
    streams = [upbit, bithumb, binance]
    universe = UniverseRefresher(
        sink=sink, streams=[upbit, bithumb], foreign=binance, client=client
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
        events=events,
        wallet=wallet,
    )
    ticks.start()

    # 5. 009 — 인계 큐 보내기 태스크와 flusher(60초, Influx 토큰 없으면 비활성)
    handoff.start()
    flusher: Flusher | None = None
    if influx is not None:
        flusher = Flusher(stream=tick_stream, writer=influx)
        flusher.start()

    app.state.live_store = store
    app.state.collector = CollectService(
        store=store, universe=universe, streams=streams, client=client, wallet=wallet
    )
    try:
        yield
    finally:
        await ticks.aclose()  # 슬롯의 마지막 틱을 인계한다
        await handoff.aclose()  # 큐에 남은 틱을 Redis 로 한 번씩 보내 본다(총 5초 상한)
        if flusher is not None:
            await flusher.aclose()
        await universe.aclose()
        await asyncio.gather(*(s.aclose() for s in streams))
        if archive is not None:
            # 스트림이 닫힌 뒤 — 마지막 프레임까지 담아 5초 안에서 올린다 (010 §3.6)
            await archive.aclose()
        for task in (outage_writer_task, event_writer_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if influx is not None:
            influx.close()
        await tick_stream.aclose()
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


def _setup_logging() -> None:
    """앱 로그를 타임스탬프와 함께 stderr 로 — 설정이 없으면 `logging.lastResort` 가 받아
    WARNING 이상만, 그것도 시각 없이 나간다. 그러면 "S3 원문 업로드 재개"·"밀린 틱 n건 적재"
    같은 복구 신호(009 §3.5·010 §3.6)가 아예 보이지 않는다.

    handler 는 루트에 달되 레벨은 `marketlens` 에만 내린다 — 라이브러리 INFO(httpx 의 요청
    한 줄 등)는 루트의 WARNING 에 막힌다. uvicorn 로거는 propagate=False 라 겹치지 않는다.
    """
    root = logging.getLogger()
    if not any(getattr(h, "_marketlens", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        handler._marketlens = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    logging.getLogger("marketlens").setLevel(logging.INFO)


def create_app() -> FastAPI:
    _setup_logging()
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
