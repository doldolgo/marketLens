"""관리자 경로 — api 의 `GET /admin/status`(029 §3.4)·피드 셋(035 §3.1·068 §3.2)·IP 대역 둘(067 §3.2·§3.3)과 수집기의 피드 셋
(034 §3.1·063 §3.1).

`router` 는 api 역할에만, `collector_router` 는 collector 역할에만 포함한다(`main.py`) — 다른 역할에서는 404 다.
공개 nginx 는 028 허용 목록 밖이라 404, 관리자 nginx(:8081)가 정확 일치로 `/svc/api/admin/{status,access,clarity,
clarity/history,access/ips,access/ip,access/addrs,access/addr}` 와 `/api/admin/{aws,alerts,aws/series}` 를 넘긴다. 모두 항상 200 인 상태 응답이다 —
대역 상세의 `band` 가 대역 꼴이 아닐 때·IP 상세의 `ip` 가 주소·대역 꼴이 아닐 때의 400 만 예외다.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.features.admin.access_addr import is_target
from app.features.admin.access_addrs_page import addrs_query
from app.features.admin.access_bands import ips_query, is_band
from app.features.admin.feeds import AdminFeeds
from app.features.admin.models import AdminStatusOut
from app.features.admin.service import AdminStatusService
from app.features.admin.visits import VisitFeeds

router = APIRouter()
collector_router = APIRouter()
# 067 — 대역 응답은 가린 IP 대역을, 069 응답은 IP 전체를 싣는다. 브라우저·중간 캐시에 남지 않게
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


@router.get("/admin/access/addrs")
async def get_admin_access_addrs(
    request: Request,
    window: str | None = None,
    net: str | None = None,
    country: str | None = None,
    verdict: str | None = None,
    q: str | None = None,
    masked: str | None = None,
    sort: str | None = None,
    limit: str | None = None,
    offset: str | None = None,
) -> JSONResponse:
    """IP 하나 단위 접속 — IP 전체(배포 뒤 줄)·가린 대역(앞 줄) 행, 봇 판정·근거·위치, 거르기·정렬·쪽, 60초 캐시 (069 §3.5).
    모양이 틀린 인자는 기본값이다(422 없음)."""
    feeds: VisitFeeds = request.app.state.admin_visits
    query = addrs_query(net, country, verdict, q, masked, sort, limit, offset)
    return JSONResponse(await feeds.addrs(window, query), headers=NO_STORE)


@router.get("/admin/access/addr")
async def get_admin_access_addr(
    request: Request, ip: str | None = None, window: str | None = None
) -> JSONResponse:
    """IP 하나(또는 목록의 가린 대역 글자)의 상세 — 목록 행 값·위치·역방향 DNS·검색 로봇 확인·시간별·최근 100줄 (069 §3.6)."""
    if ip is None or not is_target(ip):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "bad_ip",
                    "message": "ip 는 IPv4·IPv6 주소(정규 글자) 또는 대역 목록의 글자(IPv4 /24·IPv6 /48)여야 합니다.",
                    "detail": None,
                }
            },
            headers=NO_STORE,
        )
    feeds: VisitFeeds = request.app.state.admin_visits
    return JSONResponse(await feeds.addr(ip, window), headers=NO_STORE)


@router.get("/admin/clarity")
async def get_admin_clarity(request: Request) -> JSONResponse:
    """Clarity 요약 — 기본 6시간·페이지×기기 12시간·기기·유입 차원 24시간씩, 하위 부분 pages·dims, Redis 기록 넷
    (035 §3.3·040 §3.2·§3.5·068 §3.1)."""
    state = request.app.state
    feeds: VisitFeeds = state.admin_visits
    return JSONResponse(await feeds.clarity(bus=getattr(state, "spreads_bus", None)))


@router.get("/admin/clarity/history")
async def get_admin_clarity_history(
    request: Request, days: str | None = None
) -> JSONResponse:
    """Clarity 날별 기록 — `days` 7·30·90(목록 밖·없음은 30), 날 오름차순·기록 없는 날은 빠진다, 60초 캐시 (068 §3.2)."""
    state = request.app.state
    feeds: VisitFeeds = state.admin_visits
    return JSONResponse(
        await feeds.clarity_history(days, bus=getattr(state, "spreads_bus", None))
    )


@collector_router.get("/admin/aws")
async def get_admin_aws(request: Request) -> JSONResponse:
    """경보·24시간 지표·canary·예산 — 부분별 state (034 §3.2)."""
    feeds: AdminFeeds = request.app.state.admin_feeds
    return JSONResponse(await feeds.aws())


@collector_router.get("/admin/aws/series")
async def get_admin_aws_series(
    request: Request,
    range_: Annotated[str | None, Query(alias="range")] = None,
    offset: str | None = None,
) -> JSONResponse:
    """상자 셋·WS 접속·canary 시계열 — `range` 6h·24h·7d·30d(목록 밖·없음은 24h), `offset=prev` 면 그 앞 같은 길이의
    이전 기간(목록 밖·없음은 지금 창), 부분 하나 (063 §3·068 §3.3)."""
    feeds: AdminFeeds = request.app.state.admin_feeds
    return JSONResponse(await feeds.aws_series(range_, offset))


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
