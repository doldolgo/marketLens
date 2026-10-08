"""GET /flow/netflow·/flow/recent — 스펙 050 §3.6·§3.7. 수집기 역할만 include 한다(감지기 상태·업비트 현재가가 메모리).

Influx 조회·조립은 스레드에서(history 와 같은 이유 — 루프는 거래소 수신·틱과 같다). 인자 오류는 422 가 아니라
400 `invalid_request`, 저장소 불가(토큰 없음·불통)는 503 `storage_unavailable` — 응답은 앱 공통 `{"error":…}` 모양이다.
"""

import asyncio
import re
import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.core.eth_flow import EthFlowDetector, FlowStatus
from app.core.influx import InfluxUnavailableError
from app.core.serialization import camelize_json
from app.features.flow.service import (
    WINDOWS,
    FlowReader,
    build_netflow,
    build_recent,
    upbit_krw_price,
)

router = APIRouter(prefix="/flow")

LIMIT_MIN = 1
LIMIT_MAX = 500
LIMIT_DEFAULT = 100
DIRS = ("all", "in", "out")
# symbol 은 Flux 문자열에 들어간다 — 심볼 문자만 (history 의 base 와 같은 규칙)
_SYMBOL = re.compile(r"^[A-Za-z0-9]{1,20}$")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=camelize_json(
            {"error": {"code": code, "message": message, "detail": None}}
        ),
    )


def _status(request: Request) -> FlowStatus | None:
    detector: EthFlowDetector | None = getattr(request.app.state, "eth_flow", None)
    if detector is None:
        return None
    return detector.status()


def _reader(request: Request) -> FlowReader | None:
    return getattr(request.app.state, "influx", None)


def _no_storage() -> JSONResponse:
    return _error(
        503,
        "storage_unavailable",
        "저장소를 쓸 수 없습니다 — INFLUX_TOKEN 이 설정되지 않았습니다.",
    )


@router.get("/netflow")
async def get_netflow(request: Request, window: str = Query("1h")) -> JSONResponse:
    if window not in WINDOWS:
        return _error(400, "invalid_request", "window 는 1h·6h·24h 중 하나입니다.")
    reader = _reader(request)
    if reader is None:
        return _no_storage()
    status = _status(request)
    price_of = upbit_krw_price(request.app.state.live_store)
    now = int(time.time())
    try:
        payload = await asyncio.to_thread(
            build_netflow, reader, status, price_of, window=window, now=now
        )
    except InfluxUnavailableError as exc:
        return _error(503, "storage_unavailable", f"저장소 조회에 실패했습니다: {exc}")
    return JSONResponse(content=payload.model_dump(by_alias=True))


@router.get("/recent")
async def get_recent(
    request: Request,
    limit: str = Query(str(LIMIT_DEFAULT)),
    dir: str = Query("all"),
    symbol: str | None = Query(None),
) -> JSONResponse:
    try:
        limit_n = int(limit)
    except ValueError:
        return _error(400, "invalid_request", "limit 은 1~500 의 정수입니다.")
    if not LIMIT_MIN <= limit_n <= LIMIT_MAX:
        return _error(400, "invalid_request", "limit 은 1~500 의 정수입니다.")
    if dir not in DIRS:
        return _error(400, "invalid_request", "dir 은 all·in·out 중 하나입니다.")
    symbol_u: str | None = None
    if symbol:
        if not _SYMBOL.match(symbol):
            return _error(400, "invalid_request", "symbol 은 영숫자 1~20자입니다.")
        symbol_u = symbol.upper()
    reader = _reader(request)
    if reader is None:
        return _no_storage()
    status = _status(request)
    price_of = upbit_krw_price(request.app.state.live_store)
    now = int(time.time())
    try:
        payload = await asyncio.to_thread(
            build_recent,
            reader,
            status,
            price_of,
            limit=limit_n,
            dir=dir,
            symbol=symbol_u,
            now=now,
        )
    except InfluxUnavailableError as exc:
        return _error(503, "storage_unavailable", f"저장소 조회에 실패했습니다: {exc}")
    return JSONResponse(content=payload.model_dump(by_alias=True))
