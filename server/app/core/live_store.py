"""메모리 저장소 — 실시간 조회 API 의 유일한 진실 (스펙 001 §3.3).

uvicorn 워커 1개 + 단일 이벤트 루프 전제라 잠금이 없다. 쓰기는 행 단위다 —
거래소 단위 통째 교체는 없다(§3.5-4). 조회는 전부 동기(await 없음)다.
"""

from datetime import datetime

from app.core.models import Rate, Row, StreamState, Tick

SparkKey = tuple[str, str, str]  # (dom, fx, base)


class LiveStore:
    def __init__(self) -> None:
        # 거래소 id → (base 대문자 → Row). base 조회는 대소문자를 무시한다.
        self._snapshots: dict[str, dict[str, Row]] = {}
        self._rates: dict[str, Rate] = {}
        self._streams: dict[str, StreamState] = {}
        self._received_at: int | None = None
        self._tick: Tick | None = None  # 틱 슬롯 — 가장 최근 틱 1개 (§3.6)
        self._spark: dict[SparkKey, list[float]] = {}  # 009 가 게시한다

    # --- 쓰기 (수집 경로만 부른다) ---

    def put_row(self, row: Row, now: datetime) -> None:
        """행 1개 교체. 입출금 3필드는 직전 행에서 물려받는다 (§3.3·§3.5-3)."""
        key = row.base.upper()
        table = self._snapshots.setdefault(row.exchange, {})
        prev = table.get(key)
        if prev is not None:
            row.deposit_enabled = prev.deposit_enabled
            row.withdrawal_enabled = prev.withdrawal_enabled
            row.networks = prev.networks
        row.updated_at = now
        table[key] = row

    def put_rows(self, rows: list[Row], now: datetime) -> None:
        """행 여러 개를 순서대로 put_row — 시드·테스트용 편의. 다른 행은 건드리지 않는다."""
        for row in rows:
            self.put_row(row, now)

    def remove_row(self, exchange: str, base: str) -> None:
        self._snapshots.get(exchange, {}).pop(base.upper(), None)

    def retain_bases(self, bases: set[str]) -> int:
        """우주(§3.2) 밖 base 의 행을 전부 지운다 — 상폐 소멸. 지운 행 수를 돌려준다."""
        allowed = {b.upper() for b in bases}
        removed = 0
        for table in self._snapshots.values():
            for key in [k for k in table if k not in allowed]:
                del table[key]
                removed += 1
        return removed

    def set_rate(self, exchange: str, ask: float, bid: float, now: datetime) -> None:
        self._rates[exchange] = Rate(
            exchange=exchange, ask=ask, bid=bid, updated_at=now
        )

    def stream(self, exchange: str) -> StreamState:
        """거래소 스트림 상태 — 없으면 만든다. 커넥터가 이 객체를 직접 갱신한다."""
        state = self._streams.get(exchange)
        if state is None:
            state = StreamState()
            self._streams[exchange] = state
        return state

    def push_tick(self, tick: Tick) -> Tick | None:
        """새 틱을 슬롯에 넣고 직전 틱을 돌려준다 — 그것이 인계 대상이다 (009 §3.3)."""
        prev, self._tick = self._tick, tick
        return prev

    def mark_received(self, ts: int) -> None:
        """마지막 틱 시각(epoch 초)."""
        self._received_at = ts

    def set_spark(self, spark: dict[SparkKey, list[float]]) -> None:
        self._spark = spark

    # --- 조회 (전부 동기) ---

    def get_all(
        self, exchange: str | None = None, base: str | None = None
    ) -> list[Row]:
        base_key = base.upper() if base is not None else None
        out: list[Row] = []
        for ex, table in self._snapshots.items():
            if exchange is not None and ex != exchange:
                continue
            for key, row in table.items():
                if base_key is not None and key != base_key:
                    continue
                out.append(row)
        return out

    def get(self, exchange: str, base: str) -> Row | None:
        return self._snapshots.get(exchange, {}).get(base.upper())

    def get_rate(self, exchange: str) -> Rate | None:
        return self._rates.get(exchange)

    def rates(self) -> dict[str, Rate]:
        """전체 시세 사본."""
        return dict(self._rates)

    def stream_state(self, exchange: str) -> StreamState | None:
        """읽기 전용 조회 — 등록된 스트림이 없으면 None(만들지 않는다)."""
        return self._streams.get(exchange)

    def streams(self) -> dict[str, StreamState]:
        return dict(self._streams)

    @property
    def tick(self) -> Tick | None:
        return self._tick

    @property
    def received_at(self) -> int | None:
        return self._received_at

    def spark(self, dom: str, fx: str, base: str) -> list[float]:
        return list(self._spark.get((dom, fx, base.upper()), []))

    def is_empty(self) -> bool:
        return not any(self._snapshots.values())
