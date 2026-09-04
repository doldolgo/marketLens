"""거래소 호출 실패 예외.

앱 에러 응답 형식 `{"error": {"code", "message", "detail"}}` 으로의 변환은
main.py 의 예외 핸들러가 한다. 여기는 예외 자체와 detail 조립만.
"""

_BODY_LIMIT = 500  # 비-200 본문은 앞 500자만 담는다 — 스펙 001 §3.1

# 실패 종류 8종 — 스펙 011 §3.2. 분류는 각 커넥터가 자기 거래소 규칙으로 정한다.
# stale_stream 만 HTTP 응답이 아니라 상시 연결의 정체다(012 §3.6) — url·status_code 가 없다.
FAIL_KINDS = (
    "timeout",
    "network",
    "rate_limit",
    "banned",
    "unavailable",
    "bad_request",
    "bad_response",
    "stale_stream",
)


class ExchangeError(Exception):
    """거래소 호출 실패의 공통 부모. code·http_status 는 하위 클래스가 정한다.

    kind 는 실패 이력(011)이 구간을 나누는 기준이다. 하위 클래스의 기본값이 있어
    커넥터가 안 넘기면 그 기본값이 된다(타임아웃 → timeout, 그 외 → bad_response).
    """

    code = "exchange_api_error"
    http_status = 502
    default_kind = "bad_response"

    def __init__(
        self,
        exchange: str,
        url: str | None,  # stale_stream 은 HTTP 호출이 아니라 url 이 없다 (011 §3.2)
        message: str,
        status_code: int | None = None,
        body: str | None = None,
        kind: str | None = None,
        retry_after_sec: int | None = None,
    ) -> None:
        super().__init__(message)
        self.exchange = exchange
        self.url = url
        self.message = message
        self.status_code = status_code
        self.body = body[:_BODY_LIMIT] if body is not None else None
        self.kind = kind if kind is not None else self.default_kind
        self.retry_after_sec = retry_after_sec

    def detail(self) -> dict[str, object]:
        d: dict[str, object] = {"exchange": self.exchange, "url": self.url}
        if self.status_code is not None:
            d["status_code"] = self.status_code
            d["body"] = self.body
        return d


class ExchangeTimeoutError(ExchangeError):
    """거래소 타임아웃 → 504 exchange_timeout."""

    code = "exchange_timeout"
    http_status = 504
    default_kind = "timeout"


class ExchangeApiError(ExchangeError):
    """그 외 거래소 호출 실패(비-200, JSON 아님, 연결 실패) → 502 exchange_api_error."""

    code = "exchange_api_error"
    http_status = 502
