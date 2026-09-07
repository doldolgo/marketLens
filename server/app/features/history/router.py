"""GET /history/premium·/history/streaks·/history/streaks/bulk — 스펙 005 §3.4. /history/events — 013 §3.4. /history/candles — 014 §3.6.

유일하게 DB 를 읽는 조회 경로다(db.md). 저장소 불가(연결 실패·토큰 없음)는 503
`storage_unavailable` — 메모리 조회 경로(/spreads 등)는 영향받지 않는다.
Influx 클라이언트는 동기라 스레드로 돌려 이벤트 루프를 막지 않는다.
"""

import asyncio
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.influx import InfluxUnavailableError
from app.core.premium_events import PremiumEventDetector
from app.core.serialization import camelize_json
from app.features.history.service import (
    CandleReader,
    EventReader,
    HistoryApiError,
    PremiumReader,
    build_bulk,
    build_candles,
    build_events,
    build_premium_history,
    build_streaks,
)

router = APIRouter(prefix="/history")

# base 는 Flux 문자열에 들어간다 — 심볼 문자만 허용(그 외 422)
_BASE_PATTERN = r"^[A-Za-z0-9]{1,20}$"


def _error(status: int, code: str, message: str, detail: object = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=camelize_json(
            {"error": {"code": code, "message": message, "detail": detail}}
        ),
    )


async def _respond(
    request: Request,
    build: Callable[[PremiumReader | EventReader | CandleReader], BaseModel],
) -> JSONResponse:
    reader: PremiumReader | EventReader | CandleReader | None = getattr(
        request.app.state, "influx", None
    )
    if reader is None:
        return _error(
            503,
            "storage_unavailable",
            "저장소를 쓸 수 없습니다 — INFLUX_TOKEN 이 설정되지 않았습니다.",
        )
    try:
        payload = await asyncio.to_thread(build, reader)
    except HistoryApiError as exc:
        return _error(exc.http_status, exc.code, exc.message, exc.detail)
    except InfluxUnavailableError as exc:
        return _error(503, "storage_unavailable", f"저장소 조회에 실패했습니다: {exc}")
    return JSONResponse(content=camelize_json(payload.model_dump()))


@router.get("/premium")
async def get_premium_history(
    request: Request,
    base: str = Query(..., pattern=_BASE_PATTERN),
    unit: Literal["week", "month"] = Query(...),
    date: str | None = Query(None),
    dom: Literal["upbit", "bithumb"] = Query("upbit"),
    fx: Literal["binance"] = Query("binance"),
) -> JSONResponse:
    return await _respond(
        request,
        lambda reader: build_premium_history(
            reader, dom=dom, fx=fx, base=base, unit=unit, date_str=date
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
    fx: Literal["binance"] = Query("binance"),
) -> JSONResponse:
    return await _respond(
        request,
        lambda reader: build_streaks(
            reader,
            dom=dom,
            fx=fx,
            base=base,
            threshold=threshold,
            start=start,
            end=end,
            max_gap=max_gap,
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
    fx: Literal["binance"] = Query("binance"),
) -> JSONResponse:
    # 수 MB 응답의 gzip 은 001 이 켠 앱 전역 GZip 미들웨어가 처리한다 (architecture.md)
    return await _respond(
        request,
        lambda reader: build_bulk(
            reader,
            dom=dom,
            fx=fx,
            threshold=threshold,
            start=start,
            end=end,
            max_gap=max_gap,
        ),
    )


@router.get("/events")
async def get_events(
    request: Request,
    start: int | None = Query(None, ge=0, le=4_102_444_800),
    end: int | None = Query(None, ge=0, le=4_102_444_800),
    dom: Literal["upbit", "bithumb"] | None = Query(None),
    dir: Literal["kimp", "reverse"] | None = Query(None),
    base: str | None = Query(None, pattern=_BASE_PATTERN),
) -> JSONResponse:
    # 진행 중 사건은 메모리(013 감지기)에서 — 감지기가 없으면(테스트) 진행 중 없음.
    # Influx 가 없으면 진행 중만으로 200 을 만들지 않고 503 — 반쪽 답을 주지 않기 위해 (013 §3.4)
    detector: PremiumEventDetector | None = getattr(
        request.app.state, "premium_events", None
    )
    open_events = detector.open_events() if detector is not None else []
    return await _respond(
        request,
        lambda reader: build_events(
            reader,  # type: ignore[arg-type]
            open_events,
            start=start,
            end=end,
            dom=dom,
            dir=dir,
            base=base,
        ),
    )


@router.get("/candles")
async def get_candles(
    request: Request,
    base: str = Query(..., pattern=_BASE_PATTERN),
    res: Literal["1m", "5m", "1h", "4h", "1d"] = Query("1m"),
    dom: Literal["upbit", "bithumb"] = Query("upbit"),
    fx: Literal["binance"] = Query("binance"),
    dir: Literal["kimp", "reverse"] = Query("kimp"),
    start: int | None = Query(None, ge=0, le=4_102_444_800),
    end: int | None = Query(None, ge=0, le=4_102_444_800),
) -> JSONResponse:
    # 계층 버킷 하나만 읽는다 — 진행 중인 창(메모리)은 싣지 않는다 (014 §3.6)
    return await _respond(
        request,
        lambda reader: build_candles(
            reader,  # type: ignore[arg-type]
            base=base,
            res=res,
            dom=dom,
            fx=fx,
            dir=dir,
            start=start,
            end=end,
        ),
    )
