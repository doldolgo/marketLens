"""화면 영역 이용 통계 경로 — 받기 `POST /attention` 과 관리자 피드 `GET /admin/attention` (스펙 052 §3.4·§3.6).

api 역할에만 포함한다(`main.py`). 공개 nginx 는 `= /api/attention`(POST 만) 하나를 열고, 관리자 피드는 관리자 nginx 의
`= /svc/api/admin/attention` 으로만 닿는다(공개는 028 허용 목록 밖이라 404).
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.features.attention.models import MAX_BODY_BYTES
from app.features.attention.service import AttentionService

router = APIRouter()

# 오류 응답 — 앱 공통 모양 `{"error":{code,message,detail}}` (architecture.md 계약 규칙)
_ERRORS = {
    "cross_site": (403, "다른 사이트에서 온 요청은 받지 않습니다."),
    "too_large": (413, f"몸통은 {MAX_BODY_BYTES}바이트까지입니다."),
    "bad_beacon": (400, "비콘 모양이 맞지 않습니다."),
}


async def _read_capped(request: Request) -> bytes | None:
    """몸통을 상한 + 1 바이트까지만 읽는다 — 넘으면 None. 배포에선 nginx 가 4k 로 먼저 자른다."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/attention")
async def post_attention(request: Request) -> Response:
    """동의한 브라우저의 비콘 — 더했거나 버렸으면 204(본문 없음), 어긋나면 오류 (§3.4)."""
    service: AttentionService = request.app.state.attention
    sfs = request.headers.get("sec-fetch-site")
    # ① 은 몸통을 읽기 전에 — 다른 사이트의 요청에는 몸통을 읽지 않는다
    body = await _read_capped(request) if sfs in (None, "same-origin") else b""
    outcome = service.accept(body, sfs, request.headers.get("x-client-ip"))
    if outcome in ("added", "dropped"):
        return Response(status_code=204)
    status, message = _ERRORS[outcome]
    return JSONResponse(
        status_code=status,
        content={"error": {"code": outcome, "message": message, "detail": None}},
    )


@router.get("/admin/attention")
async def get_admin_attention(
    request: Request, days: str | None = None
) -> JSONResponse:
    """창 합계 — `days` 1·7·30·90(그 밖·없음은 7). 항상 200 상태 응답, 캐시하지 않게 (§3.6)."""
    state = request.app.state
    service: AttentionService = state.attention
    body = await service.feed(days, getattr(state, "spreads_bus", None))
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
