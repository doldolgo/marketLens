"""앱 진입점 — 골격·에러 형식·/health·수집 배선 (스펙 001).

/health 와 틱 루프는 기능 폴더가 아니라 여기(시스템) 소관이다.
메모리가 진실이므로 uvicorn 워커는 1개여야 한다 — 워커가 둘이면 서로 다른 메모리를 본다.
프로세스 역할은 ROLE(016) — collector(기본) 는 아래 전체, api 는 Influx 조회 전용(`_api_lifespan`).
시작 순서(collector): 010 원문 아카이브(S3_BUCKET 있을 때) → Influx·Redis 연결 확인 → 마켓 우주·스트림 기동(국내 2 +
해외 3곳 샤드) → 011 이력 복원 → 013 사건 복원 → 014 봉 버킷·롤업 기준점 → 009 spark 복원 → 틱 루프 → 009 인계
보내기 태스크·017 게시기·허브·flusher → GC 정리(001 §3.1 — 두 역할 모두 yield 직전).
스트림을 복원보다 먼저 여는 것은 스트림 준비(목록 REST·WS 연결·스냅샷)가 거의 네트워크 대기라 복원 시간 뒤에
줄 세울 이유가 없어서다(2026-09-28). 틱은 복원이 다 끝난 뒤에야 만들어진다 — 사건·봉·spark 가 복원 전 상태를 만지지 않는다.
어느 것이 실패해도 앱은 뜬다.
"""

import asyncio
import contextlib
import gc
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.candles import CandleAggregator, ensure_candle_buckets
from app.core.collect import CollectService
from app.core.config import (
    APP_NAME,
    APP_VERSION,
    EXCHANGE_TIMEOUT_CONNECT,
    EXCHANGE_TIMEOUT_TOTAL,
    GC_THRESHOLDS,
    GZIP_LEVEL,
    USER_AGENT,
    Settings,
    get_settings,
)
from app.core.contracts import noop_record
from app.core.day_open import DayOpenBook
from app.core.errors import ExchangeError
from app.core.heartbeat import HeartbeatSink
from app.core.influx import InfluxClient
from app.core.live_store import LiveStore
from app.core.networks import WalletMemo
from app.core.notify import Notifier, SlackLogHandler
from app.core.outages import OutageTracker
from app.core.premium_events import PremiumEventDetector
from app.core.quotes import QuoteSink
from app.core.raw_archive import RawArchive
from app.core.redis_bus import RedisBus
from app.core.redis_stream import RedisTickStream
from app.core.s3 import S3Uploader
from app.core.serialization import camelize_json
from app.core.spark import SparkBuffer, restore_spark
from app.core.streams.binance import BinanceStream
from app.core.streams.bitget import BitgetStream
from app.core.streams.bithumb import BithumbStream
from app.core.streams.bybit import BybitStream
from app.core.streams.upbit import UpbitStream
from app.core.tick_store import Flusher, TickRelay
from app.core.ticks import TickLoop
from app.core.universe import UniverseRefresher
from app.features.admin.router import router as admin_router
from app.features.admin.service import AdminStatusService
from app.features.analysis.router import router as analysis_router
from app.features.health.router import router as health_router
from app.features.history.cache import HistoryCache
from app.features.history.gate import HeavyGate
from app.features.history.router import events_router as history_events_router
from app.features.history.router import router as history_router
from app.features.landing.router import router as landing_router
from app.features.landing.service import LandingService
from app.features.spreads.gauge import start_ws_gauge
from app.features.spreads.hub import SpreadsHub
from app.features.spreads.push import SpreadsPublisher
from app.features.spreads.router import GzipSlot
from app.features.spreads.router import refresh_router as spreads_refresh_router
from app.features.spreads.router import router as spreads_router
from app.features.spreads.ws import ws_router as spreads_ws_router
from app.features.wallet_status.service import WalletStatusService

logger = logging.getLogger("marketlens.main")

HEALTH_STALE_MS = 30_000  # 마지막 틱이 이보다 오래면 /health 는 stale·503 (025 §3.5)


async def _open_influx(settings: Settings) -> InfluxClient | None:
    """Influx 클라이언트 생성·ping — 두 역할이 공통으로 하는 유일한 기동 작업 (016 §3.1)."""
    if not settings.influx_token:
        logger.warning("INFLUX_TOKEN 이 없어 Influx 를 쓰지 않는다 — /history/* 는 503")
        return None
    influx = InfluxClient(url=settings.influx_url, token=settings.influx_token)
    if not await asyncio.to_thread(influx.ping):
        logger.error(
            "InfluxDB 연결 실패: %s — 회차마다 재시도한다", settings.influx_url
        )
    return influx


def _settle_gc() -> None:
    """기동을 마친 직후(yield 직전) 한 번 — 001 §3.1·016 §3.1 (2026-09-28 결정).

    기동 때 만든 모듈·클라이언트·복원 객체는 프로세스가 끝날 때까지 산다. 한 번 거둔 뒤 영구 세대로
    옮겨(freeze) 전체 수집이 매번 훑지 않게 하고, 세대 임계를 올려 행 교체가 부르는 수집 빈도를 줄인다.
    """
    gc.collect()
    gc.freeze()
    gc.set_threshold(*GC_THRESHOLDS)


@asynccontextmanager
async def _api_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """api 역할(016 §3.1) — Influx 클라이언트 생성·ping + 017 의 스프레드 표 구독 허브뿐이다.

    거래소·S3 에 연결하지 않고 기동 복원도 없다 — 하나라도 하면 collector 와 같은 measurement 를
    중복으로 쓰거나(collect_fail·premium_event·롤업) 거래소를 이중 구독한다. Redis 는 구독 목적으로만
    쓰고(스트림 `ticks` 는 안 읽는다) 백그라운드 태스크는 그 구독 태스크 + 알림(025)·게이지(027) 각각 설정이 있을 때만.
    """
    _start_notifier(app)
    influx = await _open_influx(app.state.settings)
    app.state.influx = influx
    bus = RedisBus.from_url(app.state.settings.redis_url)
    _plug_alert_log(app, bus)
    hub = SpreadsHub(bus=bus)
    hub.start()
    app.state.spreads_hub = hub
    app.state.spreads_bus = bus  # 018 — GET /spreads 가 요청마다 latest 읽기·want 쓰기

    def ws_connections() -> int:
        return hub.connections

    # 027 — WS 접속 수 게이지. collector 는 띄우지 않는다(허브가 늘 0 이라 api 값을 덮는다)
    gauge = start_ws_gauge(app.state.settings.statsd_addr, ws_connections)
    # 029 — 관리자 상태도 같은 함수로 센다(admin 은 spreads 허브를 모른다)
    app.state.admin.ws_connections = ws_connections
    _settle_gc()
    try:
        yield
    finally:
        if gauge is not None:
            await gauge.aclose()
        await hub.aclose()
        await bus.aclose()
        if influx is not None:
            influx.close()
        await _close_notifier(app)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _start_notifier(app)
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
        bybit_api_key=settings.bybit_api_key,
        bybit_secret_key=settings.bybit_secret_key,
        record=record,
    )

    # Influx — 토큰 없으면 /history/* 503·이력 복원 없음, 앱은 뜬다 (스펙 005·011)
    influx = await _open_influx(settings)
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
    # 017 — 표 게시(틱 직후)와 자기 게시를 자기 구독하는 허브(로컬 단일 프로세스용 — 접속자가 있을 때만 구독)
    bus = RedisBus.from_url(settings.redis_url)
    _plug_alert_log(app, bus)
    # 026 — 일중 기준가 장부(KST 00시 첫 체결가, Redis 보존). 게시기가 매초 읽어 dayChg 를 만든다
    day_open = DayOpenBook(bus=bus)
    # 006 §3.7 — 망 판정 메모. 틱이 회차를 열어 채우고 같은 회차의 표 게시기가 읽는다
    wallet_memo = WalletMemo()
    publisher = SpreadsPublisher(
        store=store, bus=bus, day_open=day_open.prices, wallet_memo=wallet_memo
    )
    hub = SpreadsHub(bus=bus)

    # 1. 마켓 우주(매초) → 스트림 기동 — 복원보다 먼저(001 §3.6 앱 시작 순서). 목록을 못 받은 거래소는 다음 초에 다시.
    # 해외 커넥터(012 바이낸스·019 바이빗·020 비트겟)가 심볼 집합 계약도 맡는다 — 우주가 확정되면 각자 자기 심볼만 구독한다.
    # 틱 루프가 아직 없으므로 스트림은 LiveStore 행·원문만 채운다(판정·사건·봉은 틱이 만든다).
    app.state.started_at = int(time.time() * 1000)
    upbit = UpbitStream(store=store, sink=sink, record=record)
    bithumb = BithumbStream(store=store, sink=sink, record=record)
    binance = BinanceStream(store=store, sink=sink, record=record)
    bybit = BybitStream(store=store, sink=sink, record=record)
    bitget = BitgetStream(store=store, sink=sink, record=record)
    streams = [upbit, bithumb, binance, bybit, bitget]
    universe = UniverseRefresher(
        sink=sink,
        streams=[upbit, bithumb],
        foreigns=[binance, bybit, bitget],
        client=client,
    )
    universe.start()
    for stream in streams:
        stream.start()

    # 2. 복원 4개 — 틱 루프 시작 전에 모두 끝난다. 조회마다 HTTP 타임아웃을 각자의 상한과 같게 건다.
    # 2-1. 수집 실패 이력(011) — 24시간, 3초 상한. 쓰기는 별도 태스크가 순서대로.
    outages = OutageTracker(writer=influx, alerts=_alerts(app))
    await outages.restore(influx, app.state.started_at)
    outage_writer_task = asyncio.create_task(outages.run_writer_loop())
    app.state.outages = outages

    # 2-2. 김프/역프 사건(013) — Redis 사본(`premium_events:open`) 먼저, 없거나 실패면 Influx 7일 안 진행 중, 3초 상한.
    # 쓰기 태스크는 점이 생기면 즉시·60초 회차마다 Influx 에 쓰고 사본을 Redis 에 둔다.
    events = PremiumEventDetector(writer=influx, snapshots=bus)
    await events.restore(influx, app.state.started_at // 1000)
    event_writer_task = asyncio.create_task(events.run_writer_loop())
    app.state.premium_events = events

    # 2-3. 1분 봉(014) — 계층 버킷 5개(없으면 생성, 3초 상한) → 롤업 기준점(계층당 3초 상한).
    # Influx 가 없으면 집계만 돌고 쓰기·롤업은 실패로 남는다. 쓰기 태스크는 분이 닫히면 즉시·없어도 60초 회차.
    candles = CandleAggregator(store=influx)
    await ensure_candle_buckets(influx)
    await candles.restore(app.state.started_at // 1000)
    candle_writer_task = asyncio.create_task(candles.run_writer_loop())

    # 2-4. spark(009 §3.6) — 최근 30분 1분 버킷, 10초 상한. 조회·채우기는 스레드, 게시만 루프에서.
    await restore_spark(influx, spark, store, app.state.started_at // 1000)

    # 3. 틱 루프(1초) — 틱 생성·인계·판정. 025 — 틱 끝마다 Redis 심장박동(api 의 /health 가 읽는다)
    heartbeat = HeartbeatSink(bus=bus)
    ticks = TickLoop(
        store=store,
        streams=streams,
        client=client,
        handoff=handoff,
        outages=outages,
        events=events,
        candles=candles,
        spreads=publisher,
        heartbeat=heartbeat,
        wallet=wallet,
        day_open=day_open,
        wallet_memo=wallet_memo,
    )
    ticks.start()

    # 4. 009 — 인계 큐 보내기 태스크와 flusher(60초, Influx 토큰 없으면 비활성). 017 — 게시·구독 태스크
    handoff.start()
    publisher.start()
    hub.start()
    app.state.spreads_hub = hub
    app.state.spreads_bus = (
        bus  # 018 — collector 역할도 GET /spreads 는 메모리가 아니라 Redis 를 읽는다
    )
    flusher: Flusher | None = None
    if influx is not None:
        flusher = Flusher(stream=tick_stream, writer=influx)
        flusher.start()

    app.state.live_store = store
    app.state.collector = CollectService(
        store=store, universe=universe, streams=streams, client=client, wallet=wallet
    )
    _settle_gc()
    try:
        yield
    finally:
        await ticks.aclose()  # 슬롯의 마지막 틱을 인계한다
        # 013 — 쓰기 태스크를 멈추기 전에 열린 사건 사본 저장 1회·미전송 점 쓰기 1회(총 3초 상한)
        await events.final_round()
        await heartbeat.aclose()
        await day_open.aclose()
        await publisher.aclose()  # 남은 표는 버린다 — 017
        await hub.aclose()  # 접속자 전원 1001
        await handoff.aclose()  # 큐에 남은 틱을 Redis 로 한 번씩 보내 본다(총 5초 상한)
        if flusher is not None:
            await flusher.aclose()
        await universe.aclose()
        await asyncio.gather(*(s.aclose() for s in streams))
        if archive is not None:
            # 스트림이 닫힌 뒤 — 마지막 프레임까지 담아 5초 안에서 올린다 (010 §3.6)
            await archive.aclose()
        for task in (outage_writer_task, event_writer_task, candle_writer_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if influx is not None:
            influx.close()
        await tick_stream.aclose()
        await bus.aclose()
        await client.aclose()
        await _close_notifier(app)


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


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """처리 안 된 예외 → 500 을 앱 에러 형식으로 (025 §3.3). 내용은 응답에 싣지 않고 ERROR 로그 1줄 —
    그 줄이 Slack 로그 핸들러를 타고 알림이 된다."""
    logger.exception("unhandled: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=_error_body("internal_error", "internal error", None),
    )


def _install_notifier(app: FastAPI, settings: Settings) -> None:
    """025 §3.2 — 웹훅 URL 이 없으면 알림기·로그 핸들러 모두 만들지 않는다(로컬·테스트 기본)."""
    app.state.notifier = None
    if not settings.slack_webhook_url:
        return
    notifier = Notifier(webhook_url=settings.slack_webhook_url, role=settings.role)
    app.state.notifier = notifier
    root = logging.getLogger()
    # create_app 이 여러 번 불려도(테스트) 루트에 핸들러가 겹치지 않게
    for h in [h for h in root.handlers if isinstance(h, SlackLogHandler)]:
        root.removeHandler(h)
    root.addHandler(SlackLogHandler(notifier.notify))


def _alerts(app: FastAPI) -> Callable[[str, str], None] | None:
    notifier: Notifier | None = app.state.notifier
    if notifier is None:
        return None
    return notifier.notify


def _plug_alert_log(app: FastAPI, bus: RedisBus) -> None:
    """034 §3.3 — 버스가 생긴 뒤 알림기에 기록 함수를 꽂는다. 그 전에 보낸 알림은 기록하지 않는다."""
    notifier: Notifier | None = app.state.notifier
    if notifier is not None:
        notifier.record = bus.alert_log_push


def _start_notifier(app: FastAPI) -> None:
    notifier: Notifier | None = app.state.notifier
    if notifier is None:
        return
    notifier.start()
    # 기동 알림 — 억제(10분) 덕에 재시작 루프가 10분에 1줄로 보인다 (025 §3.3)
    notifier.notify("startup", f"🟢 {app.state.settings.role} 기동 (v{APP_VERSION})")


async def _close_notifier(app: FastAPI) -> None:
    notifier: Notifier | None = app.state.notifier
    if notifier is not None:
        await notifier.aclose()


async def _health_status(
    app: FastAPI, now_ms: int, *, api_only: bool
) -> tuple[str, int | None]:
    """025 §3.5 — (status, lastTickAt ms). collector 는 메모리의 마지막 틱, api 는 Redis 심장박동."""
    if api_only:
        bus: RedisBus | None = getattr(app.state, "spreads_bus", None)
        if bus is None:
            return "starting", None  # lifespan 전 — Redis 자리가 아직 없다
        try:
            last_ms = await bus.heartbeat()
        except Exception:
            return "redis_down", None
        if last_ms is None:
            return "stale", None
        if now_ms - last_ms < HEALTH_STALE_MS:
            return "ok", last_ms
        return "stale", last_ms
    store: LiveStore | None = getattr(app.state, "live_store", None)
    if store is None or store.received_at is None:
        return "starting", None  # 기동 후 첫 틱 전
    last_ms = store.received_at * 1000
    if now_ms - last_ms < HEALTH_STALE_MS:
        return "ok", last_ms
    return "stale", last_ms


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
    # 설정을 앱 객체보다 먼저 읽는다 — ROLE 이 허용값 밖이면 여기서 실패한다 (016 §3.1)
    settings = get_settings()
    api_only = settings.role == "api"
    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        lifespan=_api_lifespan if api_only else _lifespan,
    )
    app.state.settings = settings
    # 022 — 랜딩 요약의 부분별 캐시. I/O 가 없는 메모리뿐이라 lifespan 이 아니라 앱을 만들 때 하나
    app.state.landing = LandingService()
    # 018·013·014 — 미리 압축한 응답 바이트(GET /spreads 한 칸, 사건·봉 공유 캐시). 같은 이유로 여기서 하나씩
    app.state.spreads_gzip = GzipSlot()
    app.state.history_cache = HistoryCache()
    # 005 §3.4 — /history/premium·streaks·streaks/bulk 동시 1개(돌고 있으면 429). 워커 1개라 앱 안에서 센다
    app.state.history_heavy = HeavyGate()
    _install_notifier(app, settings)

    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    # 레벨 6 — 9 는 CPU 가 2.5배인데 크기는 1~5% 작을 뿐이다 (001 §3.1, 2026-09-28 결정)
    app.add_middleware(GZipMiddleware, compresslevel=GZIP_LEVEL)

    app.add_exception_handler(ExchangeError, _exchange_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        # 025 §3.5 — 프로세스 생존이 아니라 데이터 흐름을 답한다. ok 만 200, 나머지는 503 —
        # 외부 uptime 서비스가 상태코드만 보고 판정하게. 상태 응답이라 {"error":…} 형식이 아니다.
        status, last_ms = await _health_status(
            request.app, int(time.time() * 1000), api_only=api_only
        )
        if status == "ok":
            code = 200
        else:
            code = 503
        return JSONResponse(
            status_code=code,
            content={"status": status, "version": APP_VERSION, "lastTickAt": last_ms},
        )

    # api 역할은 Influx 만 읽는 네 경로 + 017 의 /ws/spreads + 018 의 GET /spreads(Redis 읽기)
    # + 022 의 GET /landing(Redis·Influx 읽기) + 029 의 GET /admin/status — /history/events 는 진행 중
    # 사건을 메모리에서 읽으므로 제외 (016 §3.1, 018 §3.4)
    app.include_router(history_router)
    app.include_router(spreads_ws_router)
    app.include_router(spreads_router)
    app.include_router(landing_router)
    if api_only:
        # 029 — 관리자 상태(WS 접속 수·Redis·Influx). 앞선 Influx ping 을 기억하는 자리라 앱마다 하나
        app.state.admin = AdminStatusService()
        app.include_router(admin_router)
    else:
        app.include_router(spreads_refresh_router)
        app.include_router(analysis_router)
        app.include_router(history_events_router)
        app.include_router(health_router)

    return app


app = create_app()
