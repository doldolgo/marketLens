"""GET /history/premium·/history/streaks·/history/streaks/bulk — 스펙 005 §3.4.

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
from app.features.history.service import (
    HistoryApiError,
    PremiumReader,
    build_bulk,
    build_premium_history,
    build_streaks,
)

router = APIRouter(prefix="/history")

# base 는 Flux 문자열에 들어간다 — 심볼 문자만 허용(그 외 422)
_BASE_PATTERN = r"^[A-Za-z0-9]{1,20}$"


def _error(status: int, code: str, message: str, detail: object = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": detail}},
    )


async def _respond(
    request: Request, build: Callable[[PremiumReader], BaseModel]
) -> JSONResponse:
    reader: PremiumReader | None = getattr(request.app.state, "influx", None)
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
    return JSONResponse(content=payload.model_dump())


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
    start: int | None = Query(None),
    end: int | None = Query(None),
    max_gap: int = Query(600, ge=1),
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
    start: int = Query(0),
    end: int | None = Query(None),
    max_gap: int = Query(600, ge=1),
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
