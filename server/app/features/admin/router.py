"""관리자 경로 — api 의 `GET /admin/status`(029 §3.4)·피드 둘(035 §3.1)·IP 대역 둘(067 §3.2·§3.3)과 수집기의 피드 셋
(034 §3.1·063 §3.1).

`router` 는 api 역할에만, `collector_router` 는 collector 역할에만 포함한다(`main.py`) — 다른 역할에서는 404 다.
공개 nginx 는 028 허용 목록 밖이라 404, 관리자 nginx(:8081)가 `/svc/api/admin/{status,access,clarity,access/ips,access/ip}` 와
`= /api/admin/aws`·`= /api/admin/alerts` 를, `/api/admin/aws/series` 는 `/api/` 접두 분기로 넘긴다(063 — 새 location 없음).
모두 항상 200 인 상태 응답이다 — 대역 상세의 `band` 가 대역 꼴이 아닐 때의 400 만 예외다.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.features.admin.access_bands import ips_query, is_band
from app.features.admin.feeds import AdminFeeds
from app.features.admin.models import AdminStatusOut
from app.features.admin.service import AdminStatusService
from app.features.admin.visits import VisitFeeds

router = APIRouter()
collector_router = APIRouter()
# 067 — 대역 응답은 가린 IP 대역을 싣는다. 브라우저·중간 캐시에 남지 않게
NO_STORE = {"Cache-Control": "no-store"}


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


@router.get("/admin/access/ips")
async def get_admin_access_ips(
    request: Request,
    window: str | None = None,
    net: str | None = None,
    country: str | None = None,
    q: str | None = None,
    bots: str | None = None,
    sort: str | None = None,
    limit: str | None = None,
    offset: str | None = None,
) -> JSONResponse:
    """가린 IP 대역별 접속 — 거르기(망 종류·나라·대역 앞부분·봇만인 대역 빼기)·정렬·쪽, 60초 캐시 (067 §3.2).
    모양이 틀린 인자는 기본값이다(422 없음)."""
    feeds: VisitFeeds = request.app.state.admin_visits
    query = ips_query(net, country, q, bots, sort, limit, offset)
    return JSONResponse(await feeds.ips(window, query), headers=NO_STORE)


@router.get("/admin/access/ip")
async def get_admin_access_ip(
    request: Request, band: str | None = None, window: str | None = None
) -> JSONResponse:
    """대역 하나 — 시간별·종류·상태·브라우저 정보·경로·짝 흐름·최근 100줄 (067 §3.3). `band` 는 목록의 글자 그대로."""
    if band is None or not is_band(band):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "bad_band",
                    "message": "band 는 대역 목록의 글자 그대로(IPv4 /24·IPv6 /48)여야 합니다.",
                    "detail": None,
                }
            },
            headers=NO_STORE,
        )
    feeds: VisitFeeds = request.app.state.admin_visits
    return JSONResponse(await feeds.ip(band, window), headers=NO_STORE)


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


@collector_router.get("/admin/aws/series")
async def get_admin_aws_series(
    request: Request, range_: Annotated[str | None, Query(alias="range")] = None
) -> JSONResponse:
    """상자 셋·WS 접속·canary 시계열 — `range` 6h·24h·7d·30d(목록 밖·없음은 24h), 부분 하나 (063 §3)."""
    feeds: AdminFeeds = request.app.state.admin_feeds
    return JSONResponse(await feeds.aws_series(range_))


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
