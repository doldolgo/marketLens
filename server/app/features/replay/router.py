"""화면 이동 기록 경로 — 받기 `POST /replay` 와 관리자 피드 `GET /admin/replay/{live,sessions,session}` (스펙 073 §3.4·§3.6),
수집기의 옮긴 조각 피드 `GET /admin/replay/archive` (078 §3.4).

`router` 는 api 역할에만, `collector_router` 는 수집기 역할에만 포함한다(`main.py`). 공개 nginx 는 `= /api/replay`(POST 만)
하나를 열고, 관리자 피드는 관리자 nginx 의 `= /svc/api/admin/replay/{live,sessions,session}` 셋과 `= /api/admin/replay/archive`
(수집기)로만 닿는다(공개는 028 허용 목록 밖이라 404).
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.features.replay.archive import ArchiveFeed
from app.features.replay.models import MAX_BODY_BYTES, SID, valid_ip_query
from app.features.replay.service import ReplayService

router = APIRouter()
collector_router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}
# 오류 응답 — 앱 공통 모양 `{"error":{code,message,detail}}` (architecture.md 계약 규칙)
_ERRORS = {
    "cross_site": (403, "다른 사이트에서 온 요청은 받지 않습니다."),
    "too_large": (413, f"몸통은 {MAX_BODY_BYTES}바이트까지입니다."),
    "bad_replay": (400, "화면 이동 기록 묶음의 모양이 맞지 않습니다."),
    "bad_ip": (400, "ip 는 IPv4·IPv6 주소(정규 글자) 또는 (없음)이어야 합니다."),
    "bad_sid": (400, "sid 는 16자 소문자 16진수여야 합니다."),
}


def _error(code: str, headers: dict[str, str] | None = None) -> JSONResponse:
    status, message = _ERRORS[code]
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": None}},
        headers=headers,
    )


async def _read_capped(request: Request) -> bytes | None:
    """몸통을 상한 + 1 바이트까지만 읽는다 — 넘으면 None. 배포에선 nginx 가 16k 로 먼저 자른다."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/replay")
async def post_replay(request: Request) -> Response:
    """동의한 브라우저의 묶음 — 썼거나 버렸으면 204(본문 없음), 어긋나면 오류 (§3.4)."""
    state = request.app.state
    service: ReplayService = state.replay
    sfs = request.headers.get("sec-fetch-site")
    # ① 은 몸통을 읽기 전에 — 다른 사이트의 요청에는 몸통을 읽지 않는다
    body = await _read_capped(request) if sfs in (None, "same-origin") else b""
    outcome = await service.accept(
        body,
        sfs,
        request.headers.get("x-client-ip"),
        getattr(state, "spreads_bus", None),
    )
    if outcome in ("stored", "dropped"):
        return Response(status_code=204)
    return _error(outcome)


@router.get("/admin/replay/live")
async def get_admin_replay_live(request: Request) -> JSONResponse:
    """지금 보는 중 — last 가 60초 안인 조각 최근 순 50개. 항상 200 상태 응답·no-store (§3.6)."""
    state = request.app.state
    service: ReplayService = state.replay
    body = await service.live(getattr(state, "spreads_bus", None))
    return JSONResponse(body, headers=NO_STORE)


@router.get("/admin/replay/sessions")
async def get_admin_replay_sessions(
    request: Request, ip: str | None = None
) -> JSONResponse:
    """조각 목록 — ip 가 있으면 그 IP 의 조각, 없으면 모든 IP 의 최근 조각 200개. 잘못된 ip 는 400 `bad_ip` (§3.6)."""
    if ip is not None and not valid_ip_query(ip):
        return _error("bad_ip", NO_STORE)
    state = request.app.state
    service: ReplayService = state.replay
    body = await service.sessions(ip, getattr(state, "spreads_bus", None))
    return JSONResponse(body, headers=NO_STORE)


@router.get("/admin/replay/session")
async def get_admin_replay_session(
    request: Request, sid: str | None = None, after: str | None = None
) -> Response:
    """조각 하나 — 머리(만료면 null)와 after 번째 묶음부터 600개, next·more. 잘못된 sid 는 400 `bad_sid` (§3.6)."""
    if sid is None or not SID.fullmatch(sid):
        return _error("bad_sid", NO_STORE)
    state = request.app.state
    service: ReplayService = state.replay
    body = await service.session(sid, after, getattr(state, "spreads_bus", None))
    return Response(body, media_type="application/json", headers=NO_STORE)


@collector_router.get("/admin/replay/archive")
async def get_admin_replay_archive(
    request: Request, sid: str | None = None, after: str | None = None
) -> Response:
    """S3 로 옮긴 조각 하나 — 073 session 과 같은 꼴(`archived` true). 잘못된 sid 는 400 `bad_sid` (078 §3.4)."""
    if sid is None or not SID.fullmatch(sid):
        return _error("bad_sid", NO_STORE)
    state = request.app.state
    feed: ArchiveFeed = state.replay_archive
    body = await feed.session(sid, after, getattr(state, "spreads_bus", None))
    return Response(body, media_type="application/json", headers=NO_STORE)
