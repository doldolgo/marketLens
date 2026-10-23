"""`GET /ws/spreads` — WebSocket 업그레이드 (스펙 017 §3.3).

두 역할(collector·api) 모두 포함한다 — 로컬은 프로세스 하나가 자기 게시를 자기 구독하고,
배포는 nginx 가 `/api/ws/spreads` 를 api 로만 보낸다. 클라이언트 → 서버 메시지는 없다(받아도 무시).
쿼리 `v=2` 로 붙으면 새 표마다 delta 대신 patch(바뀐 필드만)를 받는다 — 쿼리가 없는 옛 탭은 delta 그대로다.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.features.spreads.hub import SpreadsHub

ws_router = APIRouter()

PATCH_VERSION = "2"  # 017 §3.3 — 이 값의 `v` 쿼리로 붙은 접속자만 patch 를 받는다


@ws_router.websocket("/ws/spreads")
async def ws_spreads(ws: WebSocket) -> None:
    hub: SpreadsHub = ws.app.state.spreads_hub
    await ws.accept()
    conn = await hub.attach(ws, slim=ws.query_params.get("v") == PATCH_VERSION)
    try:
        while True:
            # 끊김·서버 쪽 닫기(1008·1001)가 여기서 예외로 드러난다 — 내용은 무시한다 (§3.3)
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.detach(conn)
