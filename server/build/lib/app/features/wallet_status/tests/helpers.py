"""wallet_status 테스트 공용 도구 — 네트워크 없음, httpx MockTransport 로 대체."""

import httpx


class Capture:
    """조회기가 보낸 요청을 기록하고 미리 정한 응답을 돌려준다. 마지막 응답은 반복된다."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return (
            self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def json_client(
    payload: object, status_code: int = 200
) -> tuple[Capture, httpx.AsyncClient]:
    cap = Capture([httpx.Response(status_code, json=payload)])
    return cap, cap.client()
