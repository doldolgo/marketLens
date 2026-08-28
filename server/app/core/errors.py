"""거래소 호출 실패 예외.

앱 에러 응답 형식 `{"error": {"code", "message", "detail"}}` 으로의 변환은
main.py 의 예외 핸들러가 한다. 여기는 예외 자체와 detail 조립만.
"""

_BODY_LIMIT = 500  # 비-200 본문은 앞 500자만 담는다 — 스펙 001 §3.1


class ExchangeError(Exception):
    """거래소 호출 실패의 공통 부모. code·http_status 는 하위 클래스가 정한다."""

    code = "exchange_api_error"
    http_status = 502

    def __init__(
        self,
        exchange: str,
        url: str,
        message: str,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.exchange = exchange
        self.url = url
        self.message = message
        self.status_code = status_code
        self.body = body[:_BODY_LIMIT] if body is not None else None

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


class ExchangeApiError(ExchangeError):
    """그 외 거래소 호출 실패(비-200, JSON 아님, 연결 실패) → 502 exchange_api_error."""

    code = "exchange_api_error"
    http_status = 502
