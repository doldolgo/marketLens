"""메시지 → 행 갱신 규칙 (스펙 001 §3.4·§3.5) — 거래소와 무관한 공통 규칙.

커넥터는 자기 거래소 형식을 디코드해 여기 `orderbook`·`trade` 로 넘긴다.
우주 밖 버리기·단계 거르기(가격·잔량 0 초과·유한)·누적액 상한·체결가 거르기(같은 기준)·보류·USDT 시세 추출·
입출금 필드 물려받기가 전부 여기서 일어난다. 전부 동기다.
"""

import math
from datetime import UTC, datetime

from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.rows import NOTIONAL_CAP_KRW, NOTIONAL_CAP_USDT, clean_levels

USDT = "USDT"
KRW = "KRW"


class QuoteSink:
    def __init__(self, store: LiveStore) -> None:
        self._store = store
        self._universe: set[str] = (
            set()
        )  # 대문자 base. 비어 있으면 아무 행도 저장하지 않는다
        # 행이 아직 없을 때 온 체결가와, 행이 있어도 마지막으로 본 체결가 — (exchange, BASE) → (price, ts)
        self._trades: dict[tuple[str, str], tuple[float, int]] = {}
        # 직전 호가의 (수신 ms, 그 시각의 datetime) 한 칸 — `_now` 참고. 늘 맞는 짝으로 시작한다(0 ms = 1970-01-01)
        self._now_memo: tuple[int, datetime] = (0, datetime.fromtimestamp(0, tz=UTC))

    def _now(self, received_at_ms: int) -> datetime:
        """수신 시각(epoch ms) → tz-aware UTC datetime. 직전 호출과 같은 ms 면 그때 만든 객체를 그대로 돌려준다.

        소켓을 한 번 읽을 때 프레임 여러 개가 같은 ms 로 오므로 호가의 약 80%가 직전 호가와 같은 ms 다(2026-10-09
        캡처 두 번에서 79%·84%). datetime 은 불변이라 행·시세끼리 같은 객체를 나눠 가져도 값이 같다. 메모를 튜플
        하나로 두고 한 번에 바꾸므로 ms 와 datetime 짝이 어긋날 틈이 없다. 같은 수의 int·float ms 는 나눈 값도 같다.
        """
        ms, dt = self._now_memo
        if ms != received_at_ms:
            dt = datetime.fromtimestamp(received_at_ms / 1000, tz=UTC)
            self._now_memo = (received_at_ms, dt)
        return dt

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
        """호가 메시지 1건 — 그 행의 asks/bids 를 통째 교체한다. KRW-USDT 는 시세만 갱신한다.

        호가 메시지마다(초당 2,500~4,200건) 도는 뜨거운 경로라 순서를 이렇게 둔다(2026-10-09 점검).
        - USDT 시세 → 우주 밖 버리기를 호가 정리**보다 먼저** 한다. 우주 밖 호가(같은 날 캡처에서 전체 호가의 약 5%,
          빗썸은 약 14%)는 행이 될 일이 없으니 단계를 거를 이유도 수신 시각을 만들 이유도 없다. 그래서 단계 모양이
          잘못된 우주 밖 호가도 이제 예외 없이 버려진다(전에는 `clean_levels` 의 ValueError·TypeError 를 커넥터가
          디코드 실패로 셌다) — 여섯 커넥터가 모두 `[float, float]` 목록만 넘기므로 운영에서 달라지는 것은 없다.
        - 누적액 상한은 호가 통화로 한 번만 고른다(KRW 면 원화 상한, 아니면 USDT 상한).
        - 수신 시각 datetime 은 `_now` 의 한 칸 메모로 같은 ms 끼리 나눠 쓴다.
        """
        key = base.upper()
        if key == USDT and quote == KRW:
            # §3.4 — 같은 거르기를 거친 최우선. 한쪽이 비거나 ≤0 이면 갱신하지 않는다. USDT 행은 저장하지 않는다(우주 밖)
            asks = clean_levels(asks, NOTIONAL_CAP_KRW)
            bids = clean_levels(bids, NOTIONAL_CAP_KRW)
            if asks and bids and asks[0][0] > 0 and bids[0][0] > 0:
                self._store.set_rate(
                    exchange, asks[0][0], bids[0][0], self._now(received_at_ms)
                )
            return
        if key not in self._universe:
            return  # 우주 밖 — 정리 전에 버린다 (§3.5-3)
        cap = NOTIONAL_CAP_KRW if quote == KRW else NOTIONAL_CAP_USDT
        asks = clean_levels(asks, cap)
        bids = clean_levels(bids, cap)
        if not asks or not bids:
            # 어느 한쪽이 비면 그 행은 저장하지 않는다 — 있던 행은 지운다 (§3.5-1)
            self._store.remove_row(exchange, key)
            return
        trade = self._trades.get((exchange, key))
        if trade is not None and trade[0] > 0:
            price, price_ts = trade
        else:
            price, price_ts = (bids[0][0] + asks[0][0]) / 2, timestamp_ms
        # Row 는 위치 인자로 만든다 — 키워드 8개보다 생성이 빠르다(로컬 약 260 → 170ns). 순서는 models.Row 의 앞 8필드
        # (exchange, base, quote, native_symbol, price, asks, bids, price_timestamp) 그대로이고, 필드를 끼워 넣으면
        # 여기도 함께 고쳐야 한다 — test_quotes 의 필드 순서 테스트가 어긋남을 잡는다.
        self._store.put_row(
            Row(exchange, key, quote, native_symbol, price, asks, bids, price_ts),
            self._now(received_at_ms),
        )

    def trade(
        self, *, exchange: str, base: str, price: float, price_timestamp: int
    ) -> None:
        """체결가 메시지 1건 — price·price_timestamp 만 갱신. 행이 없으면 보류한다 (§3.5-2).

        체결가가 0 이하이거나 유한하지 않으면(NaN·inf) 메시지를 무시한다 — 호가 정리(§3.5-1)와 같은 기준이다. NaN 이
        행에 들어가면 표 게시 직렬화가 깨지고, 보류값으로 남으면 다음 호가 메시지마다 행에 다시 실린다.
        """
        key = base.upper()
        # 연쇄 비교 하나로 "0 초과·유한" — NaN 은 모든 비교가 거짓이라 여기서 빠진다
        if key not in self._universe or not 0.0 < price < math.inf:
            return
        self._trades[(exchange, key)] = (float(price), int(price_timestamp))
        row = self._store.get(exchange, key)
        if row is not None:
            row.price = float(price)
            row.price_timestamp = int(price_timestamp)
