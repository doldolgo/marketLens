"""GET /history/premium·/history/streaks·/history/streaks/bulk — 스펙 005 §3.4. /history/events — 013 §3.4. /history/candles — 014 §3.6.

유일하게 DB 를 읽는 조회 경로다(db.md). 저장소 불가(연결 실패·토큰 없음)는 503
`storage_unavailable` — 메모리 조회 경로(/spreads 등)는 영향받지 않는다.
조회·빌드·camelCase·JSON 인코딩은 전부 스레드에서 끝나고 이벤트 루프에는 응답 bytes 만 돌아온다
(2026-09-28) — 수집 박스의 루프는 거래소 수신·틱과, api 의 루프는 /ws/spreads 허브와 같은 루프라서다.
`/history/events`·`/history/candles` 는 같은 조회 키의 응답을 공유 캐시(`cache.py`)에서 나눠 쓴다.
원값을 수십만 점씩 읽는 세 경로(premium·streaks·bulk)는 게이트(`gate.py`)로 한 번에 하나만 돌리고, 이미 돌고
있으면 곧바로 429 `busy` 다 — 가벼운 조회가 Influx 의 동시 쿼리 칸을 뺏기지 않게 (005 §3.4).

라우터는 둘이다(016 §2): `router` 는 Influx 만 읽는 네 경로, `events_router` 는 진행 중 사건을
메모리(013 감지기)에서도 읽는 `/history/events` — api 역할은 앞의 것만 include 한다. 경로·응답은 같다.
"""

import asyncio
import copy
import time
from collections.abc import Callable, Hashable
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from app.core.influx import InfluxUnavailableError
from app.core.premium_events import PremiumEventDetector
from app.core.serialization import camelize_json
from app.features.history.cache import (
    CANDLES_CLOSED_TTL_SEC,
    CANDLES_OPEN_TTL_SEC,
    EVENTS_TTL_SEC,
    Encoded,
    HistoryCache,
    ResponseCache,
    encode_both,
)
from app.features.history.gate import HeavyGate
from app.features.history.service import (
    CandleReader,
    EventReader,
    HistoryApiError,
    PremiumReader,
    build_bulk,
    build_streaks,
    encode_candles,
    encode_events,
    encode_model,
    encode_premium_history,
)

Reader = PremiumReader | EventReader | CandleReader

router = APIRouter(prefix="/history")
events_router = APIRouter(prefix="/history")

# base 는 Flux 문자열에 들어간다 — 심볼 문자만 허용(그 외 422)
_BASE_PATTERN = r"^[A-Za-z0-9]{1,20}$"


# 429 `busy` 에 싣는 재시도 간격(초) — 무거운 조회 하나가 끝나기를 기다렸다 다시 부르면 되는 값 (005 §3.4)
BUSY_RETRY_AFTER_SEC = 1


def _error(
    status: int,
    code: str,
    message: str,
    detail: object = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=camelize_json(
            {"error": {"code": code, "message": message, "detail": detail}}
        ),
        headers=headers,
    )


def _no_storage() -> JSONResponse:
    return _error(
        503,
        "storage_unavailable",
        "저장소를 쓸 수 없습니다 — INFLUX_TOKEN 이 설정되지 않았습니다.",
    )


async def _respond_heavy(
    request: Request, encode: Callable[[Reader], bytes]
) -> Response:
    """무거운 세 경로 — 게이트가 닫혀 있으면 곧바로 429, 아니면 조회부터 bytes 까지 스레드에서(압축은 전역 GZip 미들웨어).

    혼잡 판정이 인자 검증(400)보다 먼저다 — 돌고 있는 동안은 인자와 무관하게 429 다.
    """
    reader: Reader | None = getattr(request.app.state, "influx", None)
    if reader is None:
        return _no_storage()
    gate: HeavyGate = request.app.state.history_heavy
    if gate.busy:
        return _error(
            429,
            "busy",
            "다른 긴 기록 조회가 돌고 있습니다 — 잠시 뒤 다시 요청하세요.",
            headers={"Retry-After": str(BUSY_RETRY_AFTER_SEC)},
        )
    try:
        body = await gate.run(lambda: encode(reader))
    except HistoryApiError as exc:
        return _error(exc.http_status, exc.code, exc.message, exc.detail)
    except InfluxUnavailableError as exc:
        return _error(503, "storage_unavailable", f"저장소 조회에 실패했습니다: {exc}")
    return Response(content=body, media_type="application/json")


async def _respond_cached(
    request: Request,
    cache: ResponseCache,
    key: Hashable,
    ttl_sec: float,
    encode: Callable[[], Callable[[Reader], bytes]],
) -> Response:
    """공유 캐시 경로 — 적중이면 들고 있던 바이트, 아니면 이 키의 만들기(스레드에서 인코딩 + gzip)를 기다린다.

    `encode` 는 만들 때만 루프에서 한 번 불린다(진행 중 사건 사본처럼 루프에서 읽어야 하는 것을 여기서 잡는다).
    gzip 을 받는 요청에는 미리 압축한 바이트를 `Content-Encoding: gzip` 으로 준다 — 전역 GZip 미들웨어는 이미
    인코딩된 응답을 건너뛴다. 아니면 원본 바이트(미들웨어가 `Vary` 를 붙인다).
    """
    reader: Reader | None = getattr(request.app.state, "influx", None)
    if reader is None:
        return _no_storage()

    async def make() -> Encoded:
        fn = encode()
        return await asyncio.to_thread(lambda: encode_both(fn(reader)))

    try:
        value = await cache.get(key, ttl_sec, make)
    except HistoryApiError as exc:
        return _error(exc.http_status, exc.code, exc.message, exc.detail)
    except InfluxUnavailableError as exc:
        return _error(503, "storage_unavailable", f"저장소 조회에 실패했습니다: {exc}")
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(
            content=value.gz,
            media_type="application/json",
            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
        )
    return Response(content=value.raw, media_type="application/json")


def _cache(request: Request) -> HistoryCache:
    return request.app.state.history_cache


@router.get("/premium")
async def get_premium_history(
    request: Request,
    base: str = Query(..., pattern=_BASE_PATTERN),
    unit: Literal["week", "month"] = Query(...),
    date: str | None = Query(None),
    dom: Literal["upbit", "bithumb"] = Query("upbit"),
    fx: Literal["binance", "bybit", "bitget"] = Query("binance"),
) -> Response:
    return await _respond_heavy(
        request,
        lambda reader: encode_premium_history(
            reader,  # type: ignore[arg-type]
            dom=dom,
            fx=fx,
            base=base,
            unit=unit,
            date_str=date,
        ),
    )


@router.get("/streaks")
async def get_streaks(
    request: Request,
    base: str = Query(..., pattern=_BASE_PATTERN),
    threshold: float = Query(0, ge=0),
    start: int | None = Query(None, ge=0, le=4_102_444_800),
    end: int | None = Query(None, ge=0, le=4_102_444_800),
    max_gap: int = Query(600, ge=1, alias="maxGap"),
    dom: Literal["upbit", "bithumb"] = Query("upbit"),
    fx: Literal["binance", "bybit", "bitget"] = Query("binance"),
) -> Response:
    return await _respond_heavy(
        request,
        lambda reader: encode_model(
            build_streaks(
                reader,  # type: ignore[arg-type]
                dom=dom,
                fx=fx,
                base=base,
                threshold=threshold,
                start=start,
                end=end,
                max_gap=max_gap,
            )
        ),
    )


@router.get("/streaks/bulk")
async def get_streaks_bulk(
    request: Request,
    threshold: float = Query(0, ge=0),
    start: int | None = Query(None, ge=0, le=4_102_444_800),
    end: int | None = Query(None, ge=0, le=4_102_444_800),
    max_gap: int = Query(600, ge=1, alias="maxGap"),
    dom: Literal["upbit", "bithumb"] = Query("upbit"),
    fx: Literal["binance", "bybit", "bitget"] = Query("binance"),
) -> Response:
    # 수 MB 응답의 gzip 은 001 이 켠 앱 전역 GZip 미들웨어가 처리한다 (architecture.md)
    return await _respond_heavy(
        request,
        lambda reader: encode_model(
            build_bulk(
                reader,  # type: ignore[arg-type]
                dom=dom,
                fx=fx,
                threshold=threshold,
                start=start,
                end=end,
                max_gap=max_gap,
            )
        ),
    )


@events_router.get("/events")
async def get_events(
    request: Request,
    start: int | None = Query(None, ge=0, le=4_102_444_800),
    end: int | None = Query(None, ge=0, le=4_102_444_800),
    dom: Literal["upbit", "bithumb"] | None = Query(None),
    dir: Literal["kimp", "reverse"] | None = Query(None),
    base: str | None = Query(None, pattern=_BASE_PATTERN),
) -> Response:
    # 진행 중 사건은 메모리(013 감지기)에서 — 감지기가 없으면(테스트) 진행 중 없음.
    # Influx 가 없으면 진행 중만으로 200 을 만들지 않고 503 — 반쪽 답을 주지 않기 위해 (013 §3.4)
    detector: PremiumEventDetector | None = getattr(
        request.app.state, "premium_events", None
    )

    def encode() -> Callable[[Reader], bytes]:
        # 진행 중 사건은 틱 루프가 제자리에서 고치므로 루프에서 사본을 떠 스레드에 넘긴다
        open_events = (
            [copy.copy(ev) for ev in detector.open_events()]
            if detector is not None
            else []
        )
        return lambda reader: encode_events(
            reader,  # type: ignore[arg-type]
            open_events,
            start=start,
            end=end,
            dom=dom,
            dir=dir,
            base=base,
        )

    # 키는 요청 인자 그대로(base 만 대문자) — 웹은 창 끝을 60초 경계로 맞춰 같은 분에 같은 URL 을 부른다
    key = (dir, dom, base.upper() if base is not None else None, start, end)
    return await _respond_cached(
        request, _cache(request).events, key, EVENTS_TTL_SEC, encode
    )


@router.get("/candles")
async def get_candles(
    request: Request,
    base: str = Query(..., pattern=_BASE_PATTERN),
    res: Literal["1m", "5m", "1h", "4h", "1d"] = Query("1m"),
    dom: Literal["upbit", "bithumb"] = Query("upbit"),
    fx: Literal["binance", "bybit", "bitget"] = Query("binance"),
    dir: Literal["kimp", "reverse"] = Query("kimp"),
    start: int | None = Query(None, ge=0, le=4_102_444_800),
    end: int | None = Query(None, ge=0, le=4_102_444_800),
) -> Response:
    # 계층 버킷 하나만 읽는다 — 진행 중인 창(메모리)은 싣지 않는다 (014 §3.6)
    def encode() -> Callable[[Reader], bytes]:
        return lambda reader: encode_candles(
            reader,  # type: ignore[arg-type]
            base=base,
            res=res,
            dom=dom,
            fx=fx,
            dir=dir,
            start=start,
            end=end,
        )

    # 끝이 지금 뒤인(또는 없는) 청크는 새 봉이 붙으므로 짧게, 지난 청크는 내용이 바뀌지 않으므로 길게
    open_chunk = end is None or end > int(time.time())
    ttl = CANDLES_OPEN_TTL_SEC if open_chunk else CANDLES_CLOSED_TTL_SEC
    key = (base.upper(), res, dom, fx, dir, start, end)
    return await _respond_cached(request, _cache(request).candles, key, ttl, encode)
