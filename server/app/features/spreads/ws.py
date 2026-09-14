"""`GET /ws/spreads` — WebSocket 업그레이드 (스펙 017 §3.3).

두 역할(collector·api) 모두 포함한다 — 로컬은 프로세스 하나가 자기 게시를 자기 구독하고,
배포는 nginx 가 `/api/ws/` 를 api 로만 보낸다. 클라이언트 → 서버 메시지는 없다(받아도 무시).
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.features.spreads.hub import SpreadsHub

ws_router = APIRouter()


@ws_router.websocket("/ws/spreads")
async def ws_spreads(ws: WebSocket) -> None:
    hub: SpreadsHub = ws.app.state.spreads_hub
    await ws.accept()
    conn = await hub.attach(ws)
    try:
        while True:
            # 끊김·서버 쪽 닫기(1008·1001)가 여기서 예외로 드러난다 — 내용은 무시한다 (§3.3)
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.detach(conn)
