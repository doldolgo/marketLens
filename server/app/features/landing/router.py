"""GET /landing — 랜딩 페이지가 보는 요약 하나 (스펙 022 §3.2).

두 역할(collector·api) 모두 포함한다 — 로컬 단일 프로세스에서도 뜨게(018 의 `/spreads` 와 같은 이유).
배포에선 nginx 가 api 로만 보낸다. 응답은 항상 200 이고, 저장소가 안 되는 부분만 null 이다.
쿼리 파라미터는 없다(와도 무시).
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.serialization import camelize_json
from app.features.landing.service import LandingService

router = APIRouter()


@router.get("/landing")
async def get_landing(request: Request) -> JSONResponse:
    state = request.app.state
    service: LandingService = state.landing
    # Redis·Influx 자리는 lifespan 이 채운다 — 없으면(토큰 없음·기동 전) 그 부분이 null
    summary = await service.summary(
        bus=getattr(state, "spreads_bus", None), influx=getattr(state, "influx", None)
    )
    return JSONResponse(
        content=camelize_json(summary.model_dump()),
        headers={"Cache-Control": "no-store"},
    )
