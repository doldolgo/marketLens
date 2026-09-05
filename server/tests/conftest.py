"""collect(core) 테스트 공용 도구 — 네트워크 호출 없음, 거래소는 fake 로 대체."""

from dataclasses import dataclass

from app.core.models import Row, StreamError
from app.core.ticks import StreamVerdict


def make_row(
    exchange: str,
    base: str,
    *,
    quote: str | None = None,
    price: float = 100.0,
    asks: list[list[float]] | None = None,
    bids: list[list[float]] | None = None,
    price_timestamp: int = 1_700_000_000_000,
) -> Row:
    if quote is None:
        quote = "USDT" if exchange == "binance" else "KRW"
    native = f"{base}USDT" if exchange == "binance" else f"KRW-{base}"
    return Row(
        exchange=exchange,
        base=base,
        quote=quote,
        native_symbol=native,
        price=price,
        asks=asks if asks is not None else [[101.0, 1.0]],
        bids=bids if bids is not None else [[99.0, 2.0]],
        price_timestamp=price_timestamp,
    )


@dataclass
class FakeStream:
    """미리 정한 판정을 돌려주는 가짜 스트림 — 틱 루프·트리거의 판정 배선용."""

    id: str
    verdict: StreamVerdict | None = None

    def judge(self, now_ms: int) -> StreamVerdict | None:
        return self.verdict

    def fail(self, kind: str, message: str = "실패", **kw: object) -> None:
        self.verdict = StreamVerdict(
            ok=False,
            error=StreamError(
                kind=kind,
                message=message,
                status_code=kw.get("status_code"),  # type: ignore[arg-type]
                url=kw.get("url", "wss://x/websocket/v1"),  # type: ignore[arg-type]
                retry_after_sec=kw.get("retry_after_sec"),  # type: ignore[arg-type]
            ),
        )

    def succeed(self) -> None:
        self.verdict = StreamVerdict(ok=True)


class RawLog:
    """원문 싱크 fake — record(exchange, source, received_at_ms, payload) 를 그대로 쌓는다."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, int, str]] = []

    def __call__(
        self, exchange: str, source: str, received_at_ms: int, payload: str
    ) -> None:
        self.entries.append((exchange, source, received_at_ms, payload))

    def payloads(self, source: str | None = None) -> list[str]:
        return [e[3] for e in self.entries if source is None or e[1] == source]
