"""GET /spreads·POST /refresh — 스펙 003 §3.2·§3.3.

조회는 메모리(live_store)만 읽고, 수집은 001 수집 서비스(run_cycle)를 통해서만 한다.
"""

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.collector import Collector
from app.core.live_store import LiveStore
from app.core.serialization import camelize_json
from app.features.spreads.service import (
    MarketDataNotFoundError,
    build_refresh,
    build_spreads,
)

# /spreads·/refresh 는 루트 경로 — prefix 없음
router = APIRouter()


@router.get("/spreads")
async def get_spreads(request: Request) -> JSONResponse:
    store: LiveStore = request.app.state.live_store
    try:
        payload = build_spreads(store)
    except MarketDataNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content=camelize_json(
                {
                    "error": {
                        "code": "market_data_not_found",
                        "message": exc.message,
                        "detail": exc.detail,
                    }
                }
            ),
        )
    return JSONResponse(content=camelize_json(payload.model_dump()))


@router.post("/refresh")
async def post_refresh(request: Request) -> JSONResponse:
    expected: str = request.app.state.settings.refresh_token or ""
    if expected:
        given = request.headers.get("X-Refresh-Token") or ""
        # 타이밍 안전 비교 — 401 은 FastAPI 기본 형식(error 포장 없음, §3.3)
        if not secrets.compare_digest(given.encode(), expected.encode()):
            return JSONResponse(
                status_code=401,
                content={"detail": "X-Refresh-Token 헤더가 없거나 올바르지 않습니다."},
            )
    collector: Collector = request.app.state.collector
    # 동시 호출·수집 루프와의 직렬화는 run_cycle 내부 락이 보장한다
    result = await collector.run_cycle()
    store: LiveStore = request.app.state.live_store
    return JSONResponse(
        content=camelize_json(build_refresh(result, store).model_dump())
    )
