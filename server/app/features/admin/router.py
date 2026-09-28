"""GET /admin/status — 관리자 페이지가 보는 api 안쪽 상태 (스펙 029 §3.4).

api 역할에만 포함한다(`main.py`) — collector 에는 없고, 공개 nginx 는 028 허용 목록 밖이라 404 다.
관리자 nginx(:8081)가 `/svc/api/admin/status` 를 이 경로로 넘긴다. 항상 200 이다.
"""

from fastapi import APIRouter, Request

from app.features.admin.models import AdminStatusOut
from app.features.admin.service import AdminStatusService

router = APIRouter()


@router.get("/admin/status", response_model=AdminStatusOut)
async def get_admin_status(request: Request) -> AdminStatusOut:
    state = request.app.state
    service: AdminStatusService = state.admin
    # 버스·Influx 자리(core 객체)와 접속 수 세는 함수는 api lifespan 이 채운다 — 기동 전이면 down·0
    return await service.status(
        bus=getattr(state, "spreads_bus", None),
        influx=getattr(state, "influx", None),
    )
