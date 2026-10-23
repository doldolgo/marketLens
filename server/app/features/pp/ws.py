"""`GET /ws/pp` — WebSocket 업그레이드 (스펙 049 §3.3).

048 의 `/ws/gap` 과 같은 모양으로 두 역할(collector·api) 모두 포함한다 — 허브는 017 허브를 표 id `pp` 로 만든 세 번째
인스턴스이고 `main.py` 가 `app.state.pp_hub` 에 건다(기능 간 import 금지 — 이 모듈은 허브 타입을 모른다).
클라이언트 → 서버 메시지는 없다(받아도 무시).
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()


@ws_router.websocket("/ws/pp")
async def ws_pp(ws: WebSocket) -> None:
    hub = ws.app.state.pp_hub
    await ws.accept()
    conn = await hub.attach(ws)
    try:
        while True:
            # 끊김·서버 쪽 닫기(1008·1001)가 여기서 예외로 드러난다 — 내용은 무시한다
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.detach(conn)
