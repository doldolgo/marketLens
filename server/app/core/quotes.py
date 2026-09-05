"""메시지 → 행 갱신 규칙 (스펙 001 §3.4·§3.5) — 거래소와 무관한 공통 규칙.

커넥터는 자기 거래소 형식을 디코드해 여기 `orderbook`·`trade` 로 넘긴다.
우주 밖 버리기·잔량 필터·누적액 상한·체결가 보류·USDT 시세 추출·입출금 필드 물려받기가
전부 여기서 일어난다. 전부 동기다.
"""

from datetime import UTC, datetime

from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.rows import NOTIONAL_CAP_KRW, NOTIONAL_CAP_USDT, clean_levels

USDT = "USDT"
KRW = "KRW"


def _cap(quote: str) -> float:
    return NOTIONAL_CAP_KRW if quote == KRW else NOTIONAL_CAP_USDT


class QuoteSink:
    def __init__(self, store: LiveStore) -> None:
        self._store = store
        self._universe: set[str] = (
            set()
        )  # 대문자 base. 비어 있으면 아무 행도 저장하지 않는다
        # 행이 아직 없을 때 온 체결가와, 행이 있어도 마지막으로 본 체결가 — (exchange, BASE) → (price, ts)
        self._trades: dict[tuple[str, str], tuple[float, int]] = {}

    # --- 마켓 우주 (§3.2) ---

    def set_universe(self, bases: set[str]) -> int:
        """우주를 바꾼다. 빠진 base 의 행·보류 체결가는 그 자리에서 지운다. 지운 행 수를 돌려준다."""
        self._universe = {b.upper() for b in bases}
        for key in [k for k in self._trades if k[1] not in self._universe]:
            del self._trades[key]
        return self._store.retain_bases(self._universe)

    @property
    def universe(self) -> set[str]:
        return set(self._universe)

    # --- 메시지 (§3.4·§3.5) ---

    def orderbook(
        self,
        *,
        exchange: str,
        base: str,
        quote: str,
        native_symbol: str,
        asks: list[list[float]],
        bids: list[list[float]],
        timestamp_ms: int,
        received_at_ms: int,
    ) -> None:
        """호가 메시지 1건 — 그 행의 asks/bids 를 통째 교체한다. KRW-USDT 는 시세만 갱신한다."""
        key = base.upper()
        asks = clean_levels(asks, _cap(quote))
        bids = clean_levels(bids, _cap(quote))
        now = datetime.fromtimestamp(received_at_ms / 1000, tz=UTC)
        if key == USDT and quote == KRW:
            # §3.4 — 한쪽이 비거나 ≤0 이면 갱신하지 않는다. USDT 행은 저장하지 않는다(우주 밖)
            if asks and bids and asks[0][0] > 0 and bids[0][0] > 0:
                self._store.set_rate(exchange, asks[0][0], bids[0][0], now)
            return
        if key not in self._universe:
            return
        if not asks or not bids:
            # 어느 한쪽이 비면 그 행은 저장하지 않는다 — 있던 행은 지운다 (§3.5-1)
            self._store.remove_row(exchange, key)
            return
        trade = self._trades.get((exchange, key))
        if trade is not None and trade[0] > 0:
            price, price_ts = trade
        else:
            price, price_ts = (bids[0][0] + asks[0][0]) / 2, timestamp_ms
        self._store.put_row(
            Row(
                exchange=exchange,
                base=key,
                quote=quote,
                native_symbol=native_symbol,
                price=price,
                asks=asks,
                bids=bids,
                price_timestamp=price_ts,
            ),
            now,
        )

    def trade(
        self, *, exchange: str, base: str, price: float, price_timestamp: int
    ) -> None:
        """체결가 메시지 1건 — price·price_timestamp 만 갱신. 행이 없으면 보류한다 (§3.5-2)."""
        key = base.upper()
        if key not in self._universe or price <= 0:
            return
        self._trades[(exchange, key)] = (float(price), int(price_timestamp))
        row = self._store.get(exchange, key)
        if row is not None:
            row.price = float(price)
            row.price_timestamp = int(price_timestamp)
