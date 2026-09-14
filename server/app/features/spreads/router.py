"""GET /spreads·POST /refresh — 스펙 018 §3.1·003 §3.3.

`GET /spreads` 는 두 역할(collector·api) 모두 같은 핸들러로, 메모리를 읽지 않고 Redis 키
`spreads:latest`(017 게시기가 만든 $1,000 표)를 바이트 그대로 돌려준다 — 스프레드 탭이 보는
데이터가 전부 api 컨테이너에서 오게 하기 위해서다. `/refresh` 는 collector 전용 — 001 의 즉시
갱신 트리거(refresh_now)를 부를 뿐 시세를 REST 로 묻지 않는다.
"""

import secrets

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.core.collect import CollectService
from app.core.live_store import LiveStore
from app.core.redis_bus import LATEST_KEY, RedisBus, RedisUnavailableError
from app.core.serialization import camelize_json
from app.features.spreads.service import build_refresh

# 두 라우터 모두 루트 경로 — prefix 없음. main.py 가 역할에 따라 골라 포함한다 (018 §3.4)
router = APIRouter()  # GET /spreads — collector·api 둘 다
# POST /refresh — collector 만 (메모리·수집기가 있는 프로세스)
refresh_router = APIRouter()


def _error(status: int, code: str, message: str, detail: object) -> JSONResponse:
    # 003 과 같은 {"error":{"code","message","detail"}} 포장
    return JSONResponse(
        status_code=status,
        content=camelize_json(
            {"error": {"code": code, "message": message, "detail": detail}}
        ),
    )


@router.get("/spreads")
async def get_spreads(request: Request) -> Response:
    # 쿼리는 받지 않는다 — 표는 $1,000 한 장뿐이라 `notional` 을 주면 값이 무엇이든 400 (§3.1).
    # 옛 호출자가 다른 규모의 표를 받았다고 착각하지 않게 하기 위해서다.
    if "notional" in request.query_params:
        return _error(
            400,
            "notional_fixed",
            "체결 규모는 $1,000 고정입니다. notional 쿼리는 받지 않습니다.",
            {"notional": request.query_params["notional"]},
        )
    bus: RedisBus = request.app.state.spreads_bus
    try:
        # 읽기와 want 갱신을 한 왕복으로 — 200·404 어느 쪽이든 want 는 쓴다 (§3.1)
        text = await bus.latest_and_want()
    except RedisUnavailableError as exc:
        return _error(
            503,
            "redis_unavailable",
            "Redis 에 연결할 수 없습니다.",
            {"reason": str(exc)},
        )
    if text is None:
        return _error(
            404,
            "market_data_not_found",
            "스프레드 표가 아직 없습니다. 수집이 표를 만드는 중이거나 멈춰 있습니다.",
            {"key": LATEST_KEY},
        )
    # 파싱·재직렬화하지 않는다 — 수집이 만든 바이트가 곧 응답이다 (GZip 은 전역 미들웨어가)
    return Response(content=text, media_type="application/json")


@refresh_router.post("/refresh")
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
    collector: CollectService = request.app.state.collector
    # 동시 호출의 직렬화는 refresh_now 내부 락이 보장한다 — 틱 루프와는 독립이다
    result = await collector.refresh_now()
    store: LiveStore = request.app.state.live_store
    return JSONResponse(
        content=camelize_json(build_refresh(result, store).model_dump())
    )
