"""메모리 저장소 — 조회 API 의 유일한 진실 (스펙 001 §3.3).

uvicorn 워커 1개 + 단일 이벤트 루프 전제라 잠금이 없다.
"""

from datetime import datetime

from app.core.models import Rate, Row


class LiveStore:
    def __init__(self) -> None:
        # 거래소 id → (base 대문자 → Row). base 조회는 대소문자를 무시한다.
        self._snapshots: dict[str, dict[str, Row]] = {}
        self._rates: dict[str, Rate] = {}
        self._received_at: int | None = None

    # --- 쓰기 (collector 만 부른다) ---

    def replace_exchange(self, exchange: str, rows: list[Row], now: datetime) -> None:
        """거래소 단위 통째 교체 — 상폐 코인은 자동 소멸한다."""
        table: dict[str, Row] = {}
        for row in rows:
            row.updated_at = now
            table[row.base.upper()] = row
        self._snapshots[exchange] = table

    def set_rate(self, exchange: str, ask: float, bid: float, now: datetime) -> None:
        self._rates[exchange] = Rate(
            exchange=exchange, ask=ask, bid=bid, updated_at=now
        )

    def mark_received(self, ts: int) -> None:
        """마지막 사이클 완료 시각(epoch 초)."""
        self._received_at = ts

    # --- 조회 ---

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
        """전체 환율 사본."""
        return dict(self._rates)

    @property
    def received_at(self) -> int | None:
        return self._received_at

    def is_empty(self) -> bool:
        return not any(self._snapshots.values())
