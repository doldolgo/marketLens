"""관리자 경로 — api 의 `GET /admin/status`(029 §3.4)·피드 둘(035 §3.1)과 수집기의 피드 둘(034 §3.1).

`router` 는 api 역할에만, `collector_router` 는 collector 역할에만 포함한다(`main.py`) — 다른 역할에서는 404 다.
공개 nginx 는 028 허용 목록 밖이라 404, 관리자 nginx(:8081)가 `/svc/api/admin/{status,access,clarity}` 와
`= /api/admin/aws`·`= /api/admin/alerts` 를 넘긴다. 모두 항상 200 인 상태 응답이다.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.features.admin.feeds import AdminFeeds
from app.features.admin.models import AdminStatusOut
from app.features.admin.service import AdminStatusService
from app.features.admin.visits import VisitFeeds

router = APIRouter()
collector_router = APIRouter()


@router.get("/admin/status", response_model=AdminStatusOut)
async def get_admin_status(request: Request) -> AdminStatusOut:
    state = request.app.state
    service: AdminStatusService = state.admin
    # 버스·Influx 자리(core 객체)와 접속 수 세는 함수는 api lifespan 이 채운다 — 기동 전이면 down·0
    return await service.status(
        bus=getattr(state, "spreads_bus", None),
        influx=getattr(state, "influx", None),
    )


@router.get("/admin/access")
async def get_admin_access(request: Request, window: str | None = None) -> JSONResponse:
    """caddy 접속 로그 요약 — 부분 하나, `window` 24h·7d·30d(목록 밖·지금 고를 수 없는 창은 24h — 038 §3.1)."""
    feeds: VisitFeeds = request.app.state.admin_visits
    return JSONResponse(await feeds.access(window))


@router.get("/admin/clarity")
async def get_admin_clarity(request: Request) -> JSONResponse:
    """Clarity 요약 — 기본 4시간·페이지×기기 12시간, 하위 부분 pages, Redis 기록 둘 (035 §3.3·040 §3.2·§3.5)."""
    state = request.app.state
    feeds: VisitFeeds = state.admin_visits
    return JSONResponse(await feeds.clarity(bus=getattr(state, "spreads_bus", None)))


@collector_router.get("/admin/aws")
async def get_admin_aws(request: Request) -> JSONResponse:
    """경보·24시간 지표·canary·예산 — 부분별 state (034 §3.2)."""
    feeds: AdminFeeds = request.app.state.admin_feeds
    return JSONResponse(await feeds.aws())


@collector_router.get("/admin/alerts")
async def get_admin_alerts(request: Request) -> JSONResponse:
    """보낸 Slack 알림 + 경보 이력 7일 (034 §3.3). 웹훅이 없는 프로세스면 slack 부분은 unconfigured."""
    state = request.app.state
    feeds: AdminFeeds = state.admin_feeds
    return JSONResponse(
        await feeds.alerts(
            bus=getattr(state, "spreads_bus", None),
            slack_configured=getattr(state, "notifier", None) is not None,
        )
    )
