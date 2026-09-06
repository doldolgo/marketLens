"""collect(core) 테스트 공용 도구 — 네트워크 호출 없음, 거래소는 fake 로 대체."""

import time
from dataclasses import dataclass

from app.core.influx import (
    InfluxPoint,
    InfluxUnavailableError,
    PremiumRow,
    SparkBucketRow,
)
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
                body=kw.get("body"),  # type: ignore[arg-type]
            ),
        )

    def succeed(self) -> None:
        self.verdict = StreamVerdict(ok=True)


class RawLog:
    """원문 싱크 fake — record(exchange, source, received_at_ms, payload, key) 를 그대로 쌓는다."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, int, str, str | None]] = []

    def __call__(
        self,
        exchange: str,
        source: str,
        received_at_ms: int,
        payload: str,
        key: str | None = None,
    ) -> None:
        self.entries.append((exchange, source, received_at_ms, payload, key))

    def payloads(self, source: str | None = None) -> list[str]:
        return [e[3] for e in self.entries if source is None or e[1] == source]

    def keys(self, source: str | None = None) -> list[str | None]:
        """기록 순서대로의 `key` — 시세 프레임은 `"<종류>:<심볼>"`, 그 밖은 None (001 §3.7)."""
        return [e[4] for e in self.entries if source is None or e[1] == source]


class FakeInflux:
    """Influx fake — (measurement, 태그, 시각) 을 유일키로 덮어쓴다(db.md). 009 flusher·spark 복원용.

    `write`·`query_premium`·`query_spark` 는 core.influx.InfluxClient 와 같은 시그니처다.
    """

    def __init__(self) -> None:
        self.points: dict[
            tuple[str, tuple[tuple[str, str], ...], int], InfluxPoint
        ] = {}
        self.writes: list[int] = []  # 쓰기 호출마다 점 수
        self.fail = False
        self.fail_after_batches: int | None = None  # 이만큼 성공한 뒤의 배치부터 실패
        self.spark_rows: list[SparkBucketRow] = []
        self.spark_fail = False
        self.spark_delay_sec = 0.0

    def write(self, points: list[InfluxPoint]) -> None:
        if self.fail or (
            self.fail_after_batches is not None
            and len(self.writes) >= self.fail_after_batches
        ):
            raise InfluxUnavailableError("쓰기 실패 (테스트)")
        self.writes.append(len(points))
        for p in points:
            self.points[(p.measurement, tuple(sorted(p.tags.items())), p.ts)] = p

    def stored(self, measurement: str) -> list[InfluxPoint]:
        return [p for p in self.points.values() if p.measurement == measurement]

    def query_premium(
        self, *, dom: str, fx: str, base: str | None, start: int, stop: int
    ) -> list[PremiumRow]:
        if self.fail:
            raise InfluxUnavailableError("조회 실패 (테스트)")
        out = [
            PremiumRow(
                base=p.tags["base"],
                ts=p.ts,
                fwd=float(p.fields["fwd"]),
                rev=float(p.fields["rev"]),
            )
            for p in self.stored("premium")
            if p.tags["dom"] == dom
            and p.tags["fx"] == fx
            and (base is None or p.tags["base"] == base.upper())
            and start <= p.ts < stop
        ]
        out.sort(key=lambda r: r.ts)
        return out

    def query_spark(self, *, start: int, stop: int) -> list[SparkBucketRow]:
        if self.spark_fail:
            raise InfluxUnavailableError("조회 실패 (테스트)")
        if self.spark_delay_sec:
            time.sleep(self.spark_delay_sec)
        return [r for r in self.spark_rows if start <= r.bucket_ts < stop]
