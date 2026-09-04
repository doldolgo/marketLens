"""GET /health/collect — 스펙 011 §3.5. 메모리만 읽는다: 거래소 호출·Influx 조회 0회, 항상 200."""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.features.health.service import build_collect_health

router = APIRouter()


@router.get("/health/collect")
async def get_collect_health(request: Request) -> JSONResponse:
    state = request.app.state
    payload = build_collect_health(
        state.live_store, state.outages, state.started_at, int(time.time() * 1000)
    )
    return JSONResponse(content=payload.model_dump(by_alias=True))
