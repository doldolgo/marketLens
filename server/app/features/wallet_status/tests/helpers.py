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


class FakeRecorder:
    """010 원문 싱크 계약의 가짜 — record(exchange, source, received_at_ms, payload, key) 를 그대로 쌓는다.

    입출금 본문은 시세가 아니라 `key` 없이(None) 기록된다 — `keys` 가 그것을 본다.
    """

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, int, str]] = []
        self.keys: list[str | None] = []

    def __call__(
        self,
        exchange: str,
        source: str,
        received_at_ms: int,
        payload: str,
        key: str | None = None,
    ) -> None:
        self.lines.append((exchange, source, received_at_ms, payload))
        self.keys.append(key)


def assert_recorded_only(
    recorder: FakeRecorder, *, exchange: str, source: str, body: str, before_ms: int
) -> None:
    """응답 본문 1건이 그대로, 수신 시각과 함께 남았고 그 외엔 없다 (§3.5·§4)."""
    assert len(recorder.lines) == 1 and recorder.keys == [None]  # 비시세 — 전량 남는다
    ex, src, at, payload = recorder.lines[0]
    assert ex == exchange
    assert src == source
    assert before_ms <= at <= before_ms + 60_000  # 서버가 받은 시각(epoch ms)
    assert payload == body  # 받은 그대로 — 재직렬화 없음
