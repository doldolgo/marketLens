"""분석 API 6개 — 스펙 004 §3.2. 조회는 메모리(live_store)만 읽는다.

쿼리 타입/범위 위반은 FastAPI 기본 422, 그 외 오류는 service 의
AnalysisApiError 를 `{"error": {code, message, detail}}` 로 변환한다 (§3.0).
"""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.live_store import LiveStore
from app.features.analysis.service import (
    AnalysisApiError,
    build_arbitrage,
    build_matrix,
    build_orderbook,
    build_premium,
    build_scan,
    build_slippage,
)

# 6개 경로 전부 루트 — prefix 없음 (§2)
router = APIRouter()


def _respond(build: Callable[[], BaseModel]) -> JSONResponse:
    try:
        payload = build()
    except AnalysisApiError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.detail,
                }
            },
        )
    return JSONResponse(content=payload.model_dump())


def _store(request: Request) -> LiveStore:
    return request.app.state.live_store


@router.get("/orderbook/{exchange}")
async def get_orderbook(
    request: Request,
    exchange: str,
    symbol: str = Query(..., min_length=1),
    depth: int = Query(10, ge=1),
) -> JSONResponse:
    return _respond(
        lambda: build_orderbook(
            _store(request), exchange=exchange, symbol=symbol, depth=depth
        )
    )


@router.get("/slippage/{exchange}")
async def get_slippage(
    request: Request,
    exchange: str,
    symbol: str = Query(..., min_length=1),
    side: Literal["buy", "sell"] = Query("buy"),
    # amount·quantity 의 "정확히 하나, >0" 검증은 400 invalid_request 계약이라 service 가 한다 (§3.2)
    amount: float | None = Query(None),
    quantity: float | None = Query(None),
    depth: int = Query(100, ge=1),
) -> JSONResponse:
    return _respond(
        lambda: build_slippage(
            _store(request),
            exchange=exchange,
            symbol=symbol,
            side=side,
            amount=amount,
            quantity=quantity,
            depth=depth,
        )
    )


@router.get("/arbitrage")
async def get_arbitrage(
    request: Request,
    sym: str = Query(..., min_length=1),
    amount: float = Query(..., gt=0),
    depth: int = Query(100, ge=1),
) -> JSONResponse:
    return _respond(
        lambda: build_arbitrage(_store(request), sym=sym, amount=amount, depth=depth)
    )


@router.get("/premium")
async def get_premium(
    request: Request,
    sym: str = Query(..., min_length=1),
    dom: str = Query("upbit"),
) -> JSONResponse:
    return _respond(lambda: build_premium(_store(request), sym=sym, dom=dom))


@router.get("/premium/scan")
async def get_premium_scan(
    request: Request,
    dom: str = Query("upbit"),
    limit: int = Query(10, ge=1, le=100),
) -> JSONResponse:
    return _respond(lambda: build_scan(_store(request), dom=dom, limit=limit))


@router.get("/matrix")
async def get_matrix(
    request: Request,
    amount_krw: float = Query(10_000_000, gt=0),
) -> JSONResponse:
    return _respond(lambda: build_matrix(_store(request), amount_krw=amount_krw))
