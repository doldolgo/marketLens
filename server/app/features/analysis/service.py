"""분석 계산 — 스펙 004 §3. 순수 계산: 저장소를 인자로 받고 전역을 import 하지 않는다.

모든 응답은 메모리 스냅샷(1초 수집)만 읽는다 — 거래소 REST 호출 0회 (§2).
"""

import re
import time
from datetime import datetime

from app.core.live_store import LiveStore
from app.core.models import Rate, Row
from app.core.premium import premium_percent
from app.core.rows import NOTIONAL_CAP_KRW
from app.features.analysis.models import (
    ArbitrageCandidate,
    ArbitrageFailure,
    ArbitrageLeg,
    ArbitrageResponse,
    MatrixCoin,
    MatrixDirection,
    MatrixResponse,
    OrderbookLevel,
    OrderbookResponse,
    PremiumDirection,
    PremiumResponse,
    ScanItem,
    ScanResponse,
    SlippageResponse,
)
from app.features.analysis.walk import (
    WalkResult,
    average_price,
    slippage_percent,
    walk_amount,
    walk_quantity,
)

# 거래소 레지스트리 — 004 는 BE 전용이라 표시명도 여기서 만든다 (003 FE 의 표시명 규칙과 동일)
DOMESTIC_EXCHANGES: tuple[str, ...] = ("upbit", "bithumb")
FOREIGN_EXCHANGES: tuple[str, ...] = ("binance",)
EXCHANGE_NAMES: dict[str, str] = {
    "upbit": "업비트",
    "bithumb": "빗썸",
    "binance": "Binance",
}
BASE_EXCHANGE = "upbit"  # 기준 국내 거래소 (§3.0)
FX_EXCHANGE = "binance"  # 해외 거래소는 1곳 (§3.0)
DOMESTIC_QUOTE = "KRW"
FOREIGN_QUOTE = "USDT"
# 스캔·매트릭스 제외 코인 — 서로 다른 코인이 같은 티커를 써서 국내·해외 매칭이 틀린다 (§3.0)
# MANTRA: 2026-08-30 EC2 실측 — 스캔 1위 +40.8%, 같은 티커의 다른 코인(스펙 004 §3.0)
EXCLUDED_BASES: frozenset[str] = frozenset({"AI", "PROS", "MANTRA"})
SUSPICIOUS_PERCENT = 5.0  # |김프| 가 이 이상이면 의심 (§3.2)
LOW_LIQUIDITY_KRW = 1_000_000.0  # scan 1위 유동성 경고 문턱 (§3.2)

# 모든 계산은 수수료·출금 수수료·전송 시간 미반영 이론값 (§3.0)
FEE_WARNING = "모든 수치는 수수료·출금 수수료·전송 시간 미반영 이론값입니다."
CAP_WARNING = "요청 금액이 호가 저장 한도(10억원)를 넘어 슬리피지가 실제보다 작게 계산됐을 수 있습니다."
_NOT_COLLECTED_HINT = (
    "수집 루프가 한 사이클 돌았는지 확인하세요 (아직 수집 안 됨 또는 미상장)."
)
_SUSPICION_REASON = "이름만 같은 다른 코인이거나 한쪽 입출금 중단 가능성이 있습니다. 거래 전 확인하세요."


class AnalysisApiError(Exception):
    """분석 API 오류 — 라우터가 `{"error": {code, message, detail}}` 로 변환한다 (§3.0)."""

    def __init__(
        self, http_status: int, code: str, message: str, detail: object = None
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.detail = detail


# --- 공통 도우미 ---


def _ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt is not None else None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _received_ms(store: LiveStore) -> int | None:
    received = store.received_at
    return received * 1000 if received is not None else None


def _not_found(message: str, detail: object = None) -> AnalysisApiError:
    return AnalysisApiError(
        404, "market_data_not_found", f"{message} {_NOT_COLLECTED_HINT}", detail
    )


def _require_exchange(exchange: str) -> None:
    if exchange not in EXCHANGE_NAMES:
        raise AnalysisApiError(
            404,
            "unsupported_exchange",
            f"레지스트리에 없는 거래소 id 입니다: {exchange}",
            {"supported": sorted(EXCHANGE_NAMES)},
        )


def _require_domestic(dom: str) -> None:
    _require_exchange(dom)
    if dom not in DOMESTIC_EXCHANGES:
        raise AnalysisApiError(
            400,
            "invalid_request",
            f"dom 은 원화 거래소여야 합니다. 선택 가능: {', '.join(DOMESTIC_EXCHANGES)}",
            {"dom": dom},
        )


def _parse_symbol(symbol: str) -> tuple[str, str]:
    """`BASE/QUOTE` 파싱 — `-`·`_` 구분자도 허용, 조각 2개가 아니면 invalid_symbol (§3.0)."""
    parts = re.split(r"[/\-_]", symbol.strip())
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise AnalysisApiError(
            400,
            "invalid_symbol",
            f"symbol 은 BASE/QUOTE 형식이어야 합니다 (구분자 `/`·`-`·`_`): {symbol!r}",
        )
    return parts[0].upper(), parts[1].upper()


def _get_row(store: LiveStore, exchange: str, base: str, quote: str) -> Row:
    row = store.get(exchange, base)
    if row is None:
        raise _not_found(f"{exchange} 에 {base} 스냅샷이 없습니다.")
    if row.quote != quote:
        raise AnalysisApiError(
            404,
            "market_data_not_found",
            f"{exchange} 의 {base} 마켓은 {row.quote} 표시입니다. `{base}/{row.quote}` 로 다시 요청하세요.",
            {"stored_quote": row.quote},
        )
    return row


def _valid_rate(rate: Rate | None) -> bool:
    return rate is not None and rate.ask > 0 and rate.bid > 0


# --- GET /orderbook/{exchange} (§3.2) ---


def build_orderbook(
    store: LiveStore, *, exchange: str, symbol: str, depth: int
) -> OrderbookResponse:
    _require_exchange(exchange)
    base, quote = _parse_symbol(symbol)
    row = _get_row(store, exchange, base, quote)
    if not row.asks and not row.bids:
        raise _not_found(f"{exchange} 의 {base}/{quote} 호가가 비어 있습니다.")
    # 저장 순서 그대로 depth 단계까지 자르기만 한다 — 계산 없음
    return OrderbookResponse(
        exchange=exchange,
        symbol=f"{base}/{quote}",
        base=base,
        quote=quote,
        bids=[OrderbookLevel(price=lv[0], size=lv[1]) for lv in row.bids[:depth]],
        asks=[OrderbookLevel(price=lv[0], size=lv[1]) for lv in row.asks[:depth]],
        timestamp=row.price_timestamp,
        data_updated_at=_ms(row.updated_at),
        data_received_at=_received_ms(store),
        fetched_at=_now_ms(),
    )


# --- GET /slippage/{exchange} (§3.2) ---


def build_slippage(
    store: LiveStore,
    *,
    exchange: str,
    symbol: str,
    side: str,
    amount: float | None,
    quantity: float | None,
    depth: int,
) -> SlippageResponse:
    _require_exchange(exchange)
    base, quote = _parse_symbol(symbol)
    # amount/quantity 검증은 스냅샷 조회보다 먼저 — 빈 메모리여도 400 이 먼저다 (§3.2·§4)
    if (amount is None) == (quantity is None):
        raise AnalysisApiError(
            400,
            "invalid_request",
            "amount 또는 quantity 중 정확히 하나만 주세요.",
        )
    if amount is not None and amount <= 0:
        raise AnalysisApiError(400, "invalid_request", "amount 는 0 보다 커야 합니다.")
    if quantity is not None and quantity <= 0:
        raise AnalysisApiError(
            400, "invalid_request", "quantity 는 0 보다 커야 합니다."
        )

    row = _get_row(store, exchange, base, quote)
    stored = row.asks if side == "buy" else row.bids  # 살 때 asks, 팔 때 bids
    if not stored:
        raise _not_found(
            f"{exchange} 의 {base}/{quote} {side} 쪽 호가가 비어 있습니다."
        )
    levels = stored[:depth]
    result = (
        walk_amount(levels, amount)
        if amount is not None
        else walk_quantity(levels, quantity or 0.0)
    )
    if result.quantity <= 0:
        raise AnalysisApiError(
            400, "invalid_request", "최소 단위도 체결되지 않았습니다."
        )
    best = levels[0][0]
    average = average_price(result)

    warnings: list[str] = []
    if result.levels_consumed <= 1 and not result.exhausted:
        warnings.append(
            "1단계 호가 안에서 전량 체결돼 슬리피지 0 입니다 — 규모를 키우면 생깁니다."
        )
    warnings.append("메모리 스냅샷 기준이라 타이밍 슬리피지는 미반영입니다.")
    warnings.append(FEE_WARNING)

    return SlippageResponse(
        exchange=exchange,
        name=EXCHANGE_NAMES[exchange],
        symbol=f"{base}/{quote}",
        quote_currency=row.quote,
        side=side,  # type: ignore[arg-type]  # 라우터의 Literal 검증을 통과한 값
        requested_amount=amount,
        requested_quantity=quantity,
        best_price=best,
        average_price=average,
        quantity=result.quantity,
        amount=result.amount,
        slippage_percent=slippage_percent(side, best, average),
        levels_consumed=result.levels_consumed,
        depth_exhausted=result.exhausted,
        depth_available=len(stored),
        data_updated_at=_ms(row.updated_at),
        data_received_at=_received_ms(store),
        fetched_at=_now_ms(),
        warnings=warnings,
    )


# --- GET /arbitrage (§3.2) ---


class _Candidate:
    """KRW 로 환산이 끝난 후보 1곳 — 환전도 체결되는 쪽 호가를 쓴다 (asks=rate ask, bids=rate bid)."""

    def __init__(
        self, row: Row, asks_krw: list[list[float]], bids_krw: list[list[float]]
    ) -> None:
        self.row = row
        self.asks_krw = asks_krw
        self.bids_krw = bids_krw


def build_arbitrage(
    store: LiveStore, *, sym: str, amount: float, depth: int
) -> ArbitrageResponse:
    base = sym.strip().upper()
    rows = store.get_all(base=base)
    if not rows:
        raise _not_found(f"{base} 스냅샷이 없습니다.")
    base_rate = store.get_rate(BASE_EXCHANGE)
    if not _valid_rate(base_rate):
        raise _not_found(f"기준 거래소({BASE_EXCHANGE})의 KRW-USDT 환율이 없습니다.")
    assert base_rate is not None

    candidates: list[_Candidate] = []
    failures: list[ArbitrageFailure] = []
    for row in sorted(rows, key=lambda r: r.exchange):
        if row.quote not in (DOMESTIC_QUOTE, FOREIGN_QUOTE):
            continue  # KRW/USDT 가 아니면 후보 풀에서 제외 (§3.2-2)
        if not row.asks or not row.bids:
            failures.append(
                ArbitrageFailure(exchange=row.exchange, reason="호가가 비어 있습니다.")
            )
            continue
        if row.quote == FOREIGN_QUOTE:
            # 국내 거래소는 자기 환율, 해외는 기준 환율 — 남의 테더 프리미엄을 빌리지 않는다 (§3.2-3)
            if row.exchange in DOMESTIC_EXCHANGES:
                own = store.get_rate(row.exchange)
                if not _valid_rate(own):
                    failures.append(
                        ArbitrageFailure(
                            exchange=row.exchange, reason="KRW-USDT 환율이 없습니다."
                        )
                    )
                    continue
                assert own is not None
                rate = own
            else:
                rate = base_rate
            ask_mult, bid_mult = rate.ask, rate.bid
        else:
            ask_mult = bid_mult = 1.0
        candidates.append(
            _Candidate(
                row=row,
                asks_krw=[[lv[0] * ask_mult, lv[1]] for lv in row.asks[:depth]],
                bids_krw=[[lv[0] * bid_mult, lv[1]] for lv in row.bids[:depth]],
            )
        )

    if len(candidates) < 2:
        raise AnalysisApiError(
            409,
            "no_arbitrage_opportunity",
            "차익을 비교할 후보 거래소가 2곳 미만입니다.",
            {
                "candidates": [c.row.exchange for c in candidates],
                "failures": [f.model_dump() for f in failures],
            },
        )

    candidates.sort(key=lambda c: c.asks_krw[0][0])  # 싼 순 (best_ask)
    buy_c = candidates[0]  # 매수처 = 최저 ask
    sell_c = max(candidates, key=lambda c: c.bids_krw[0][0])  # 매도처 = 최고 bid
    if buy_c.row.exchange == sell_c.row.exchange:
        raise AnalysisApiError(
            409,
            "no_arbitrage_opportunity",
            "최저 매수처와 최고 매도처가 같은 거래소입니다.",
            {"exchange": buy_c.row.exchange},
        )

    buy_walk = walk_amount(buy_c.asks_krw, amount)
    if buy_walk.quantity <= 0:
        raise AnalysisApiError(
            400, "invalid_request", "최소 단위도 체결되지 않았습니다."
        )
    sell_walk = walk_quantity(sell_c.bids_krw, buy_walk.quantity)
    buy_exhausted = buy_walk.exhausted
    buy_filled_amount = buy_walk.amount  # 되맞추기 전 매수측 실제 체결액 (경고 a 용)
    if sell_walk.exhausted and sell_walk.quantity < buy_walk.quantity:
        # 못 판 코인을 0원으로 치면 −50% 대 쓰레기 값이 나온다 — 판 수량만큼 매수측을 되맞춘다 (§3.2-5)
        buy_walk = walk_quantity(buy_c.asks_krw, sell_walk.quantity)

    buy_best = buy_c.asks_krw[0][0]
    sell_best = sell_c.bids_krw[0][0]
    profit = sell_walk.amount - buy_walk.amount
    profit_pct = profit / buy_walk.amount * 100 if buy_walk.amount > 0 else 0.0
    prem_pct = premium_percent(buy_krw=buy_best, sell_krw=sell_best)
    capture = profit_pct / prem_pct * 100 if prem_pct != 0 else 0.0
    withdrawal = buy_c.row.withdrawal_enabled  # 매수처 출금 (§3.2-8)
    deposit = sell_c.row.deposit_enabled  # 매도처 입금

    # warnings 순서 고정 (§3.2-9)
    warnings: list[str] = []
    if buy_exhausted:
        warnings.append(
            f"매수측 호가가 소진돼 투입 금액 중 {buy_filled_amount:,.0f}원만 체결됩니다."
        )
    if sell_walk.exhausted:
        warnings.append("매도측 호가가 소진돼 매도 가능 수량만큼 매수를 되맞췄습니다.")
    if profit < 0:
        warnings.append("가장 유리한 조합조차 손해입니다.")
    buy_name = f"매수처({buy_c.row.exchange})"
    if withdrawal is False:
        warnings.append(f"{buy_name} 출금이 막혀 있습니다 — 실행 불가입니다.")
    elif withdrawal is None:
        warnings.append(
            f"{buy_name} 출금 상태를 확인 못 했습니다 — 열려 있다고 가정하지 마세요."
        )
    sell_name = f"매도처({sell_c.row.exchange})"
    if deposit is False:
        warnings.append(f"{sell_name} 입금이 막혀 있습니다 — 실행 불가입니다.")
    elif deposit is None:
        warnings.append(
            f"{sell_name} 입금 상태를 확인 못 했습니다 — 열려 있다고 가정하지 마세요."
        )
    if amount > NOTIONAL_CAP_KRW:
        warnings.append(CAP_WARNING)
    warnings.append(FEE_WARNING)

    def _leg(
        cand: _Candidate, walk: WalkResult, side: str, best: float, exhausted: bool
    ) -> ArbitrageLeg:
        avg = average_price(walk)
        return ArbitrageLeg(
            exchange=cand.row.exchange,
            name=EXCHANGE_NAMES.get(cand.row.exchange, cand.row.exchange),
            average_price_krw=avg,
            amount_krw=walk.amount,
            slippage_percent=slippage_percent(side, best, avg),
            levels_consumed=walk.levels_consumed,
            depth_exhausted=exhausted,
            data_updated_at=_ms(cand.row.updated_at),
        )

    return ArbitrageResponse(
        sym=base,
        input_amount_krw=amount,
        quantity=sell_walk.quantity,  # 실제 판 수량
        usd_krw_rate=base_rate.ask,  # 표시용
        candidates=[
            ArbitrageCandidate(
                exchange=c.row.exchange,
                name=EXCHANGE_NAMES.get(c.row.exchange, c.row.exchange),
                best_bid_krw=c.bids_krw[0][0],
                best_ask_krw=c.asks_krw[0][0],
                depth_levels=min(len(c.asks_krw), len(c.bids_krw)),
            )
            for c in candidates
        ],
        failures=failures,
        buy=_leg(buy_c, buy_walk, "buy", buy_best, buy_exhausted),
        sell=_leg(sell_c, sell_walk, "sell", sell_best, sell_walk.exhausted),
        profit_krw=profit,
        profit_percent=profit_pct,
        premium_percent=prem_pct,
        premium_capture_percent=capture,
        withdrawal_available=withdrawal,
        deposit_available=deposit,
        warnings=warnings,
        data_received_at=_received_ms(store),
        fetched_at=_now_ms(),
    )


# --- GET /premium (§3.2) ---


def build_premium(store: LiveStore, *, sym: str, dom: str) -> PremiumResponse:
    _require_domestic(dom)
    base = sym.strip().upper()
    # 국내 스냅샷은 KRW 마켓이어야 한다 (§3.2-1)
    dom_row = store.get(dom, base)
    if dom_row is None or dom_row.quote != DOMESTIC_QUOTE:
        raise _not_found(f"{dom} 의 {base} KRW 마켓 스냅샷이 없습니다.")
    rate = store.get_rate(dom)
    if not _valid_rate(rate):
        raise _not_found(f"{dom} 의 KRW-USDT 환율이 없습니다.")
    assert rate is not None
    if not dom_row.bids or not dom_row.asks:
        raise _not_found(f"{dom} 의 {base} 호가가 비어 있습니다.")
    fx_row = store.get(FX_EXCHANGE, base)
    if fx_row is None or fx_row.quote != FOREIGN_QUOTE:
        raise _not_found(f"{FX_EXCHANGE} 의 {base} USDT 마켓 스냅샷이 없습니다.")
    if not fx_row.bids or not fx_row.asks:
        raise _not_found(f"{FX_EXCHANGE} 의 {base} 호가가 비어 있습니다.")

    dom_bid, dom_ask = dom_row.bids[0][0], dom_row.asks[0][0]
    fx_bid, fx_ask = fx_row.bids[0][0], fx_row.asks[0][0]
    updated_candidates = [
        ms for ms in (_ms(dom_row.updated_at), _ms(fx_row.updated_at)) if ms is not None
    ]
    data_updated_at = min(updated_candidates) if updated_candidates else None
    rate_updated_at = _ms(rate.updated_at)

    # fwd(해외 매수→국내 매도)는 환율 ask, rev(국내 매수→해외 매도)는 환율 bid (§3.1)
    fwd_buy_krw = fx_ask * rate.ask
    fwd_pct = premium_percent(buy_krw=fwd_buy_krw, sell_krw=dom_bid)
    fwd = PremiumDirection(
        usd=fx_ask,
        usd_krw_rate=rate.ask,
        rate_updated_at=rate_updated_at,
        premium_percent=fwd_pct,
        premium_krw=dom_bid - fwd_buy_krw,
        profitable=fwd_pct > 0,
        data_updated_at=data_updated_at,
    )
    rev_sell_krw = fx_bid * rate.bid
    rev_pct = premium_percent(buy_krw=dom_ask, sell_krw=rev_sell_krw)
    rev = PremiumDirection(
        usd=fx_bid,
        usd_krw_rate=rate.bid,
        rate_updated_at=rate_updated_at,
        premium_percent=rev_pct,
        premium_krw=rev_sell_krw - dom_ask,
        profitable=rev_pct > 0,
        data_updated_at=data_updated_at,
    )
    # best = premium_percent 가 큰 쪽 — 둘 다 손해면 덜 나쁜 쪽 (§3.2-3)
    best_direction = "fwd" if fwd_pct >= rev_pct else "rev"
    return PremiumResponse(
        sym=base,
        dom=dom,
        dom_price=dom_row.price,
        fx=FX_EXCHANGE,
        fwd=fwd,
        rev=rev,
        best_direction=best_direction,  # type: ignore[arg-type]
        best_premium_percent=max(fwd_pct, rev_pct),
        data_received_at=_received_ms(store),
        fetched_at=_now_ms(),
    )


# --- GET /premium/scan (§3.2) ---


def _scan_item(
    *,
    sym: str,
    direction: str,
    dom: str,
    dom_price: float,
    usd: float,
    buy_krw: float,
    sell_krw: float,
    liquidity_krw: float,
) -> ScanItem:
    pct = premium_percent(buy_krw=buy_krw, sell_krw=sell_krw)
    suspicious = abs(pct) >= SUSPICIOUS_PERCENT
    return ScanItem(
        sym=sym,
        direction=direction,  # type: ignore[arg-type]
        dom=dom,
        dom_price=dom_price,
        fx=FX_EXCHANGE,
        fx_name=EXCHANGE_NAMES[FX_EXCHANGE],
        usd=usd,
        premium_percent=pct,
        premium_krw=sell_krw - buy_krw,
        liquidity_krw=liquidity_krw,
        suspicious=suspicious,
        suspicion_reason=_SUSPICION_REASON if suspicious else None,
    )


def build_scan(store: LiveStore, *, dom: str, limit: int) -> ScanResponse:
    _require_domestic(dom)
    # 환율 없음 404 는 스냅샷 검사보다 먼저 (§3.2)
    rate = store.get_rate(dom)
    if not _valid_rate(rate):
        raise _not_found(f"{dom} 의 KRW-USDT 환율이 없습니다.")
    assert rate is not None
    dom_rows = {
        r.base.upper(): r
        for r in store.get_all(exchange=dom)
        if r.quote == DOMESTIC_QUOTE
    }
    if not dom_rows:
        raise _not_found(f"{dom} 의 KRW 스냅샷이 없습니다.")
    fx_rows = {
        r.base.upper(): r
        for r in store.get_all(exchange=FX_EXCHANGE)
        if r.quote == FOREIGN_QUOTE
    }
    if not fx_rows:
        raise _not_found(f"{FX_EXCHANGE} 의 USDT 스냅샷이 없습니다.")

    excluded: list[str] = []
    fwd_items: list[ScanItem] = []
    rev_items: list[ScanItem] = []
    scanned_coins = 0
    scanned_pairs = 0
    for base in sorted(dom_rows):  # 코인 순 (§3.2-2)
        if base in EXCLUDED_BASES:
            excluded.append(base)
            continue
        scanned_coins += 1
        fx_row = fx_rows.get(base)
        if fx_row is None:
            continue  # 국내 상장 코인만 짝짓는다 — 해외에 없으면 짝이 없다
        dom_row = dom_rows[base]
        scanned_pairs += 1
        # 호가가 빈 쪽이 있으면 그 방향만 조용히 건너뛴다 (§3.3)
        if dom_row.bids and fx_row.asks:
            dom_bid, fx_ask = dom_row.bids[0], fx_row.asks[0]
            fwd_items.append(
                _scan_item(
                    sym=base,
                    direction="fwd",
                    dom=dom,
                    dom_price=dom_row.price,
                    usd=fx_ask[0],
                    buy_krw=fx_ask[0] * rate.ask,
                    sell_krw=dom_bid[0],
                    liquidity_krw=min(
                        fx_ask[0] * fx_ask[1] * rate.ask, dom_bid[0] * dom_bid[1]
                    ),
                )
            )
        if dom_row.asks and fx_row.bids:
            dom_ask, fx_bid = dom_row.asks[0], fx_row.bids[0]
            rev_items.append(
                _scan_item(
                    sym=base,
                    direction="rev",
                    dom=dom,
                    dom_price=dom_row.price,
                    usd=fx_bid[0],
                    buy_krw=dom_ask[0],
                    sell_krw=fx_bid[0] * rate.bid,
                    liquidity_krw=min(
                        dom_ask[0] * dom_ask[1], fx_bid[0] * fx_bid[1] * rate.bid
                    ),
                )
            )

    # 수익률 내림차순, 동률은 코인명 순으로 고정
    fwd_items.sort(key=lambda i: (-i.premium_percent, i.sym))
    rev_items.sort(key=lambda i: (-i.premium_percent, i.sym))
    best_fwd = fwd_items[0] if fwd_items else None
    best_rev = rev_items[0] if rev_items else None

    # warnings 순서 고정 (§3.2-6)
    warnings: list[str] = []
    if best_fwd is not None and best_fwd.suspicious:
        warnings.append(f"김프 1위 {best_fwd.sym} 는 의심 항목입니다.")
    if best_rev is not None and best_rev.suspicious:
        warnings.append(f"역김프 1위 {best_rev.sym} 는 의심 항목입니다.")
    if best_fwd is not None and best_fwd.liquidity_krw < LOW_LIQUIDITY_KRW:
        warnings.append(
            f"김프 1위 {best_fwd.sym} 의 1단계 체결 가능 금액이 {best_fwd.liquidity_krw:,.0f}원뿐입니다."
        )
    if best_rev is not None and best_rev.liquidity_krw < LOW_LIQUIDITY_KRW:
        warnings.append(
            f"역김프 1위 {best_rev.sym} 의 1단계 체결 가능 금액이 {best_rev.liquidity_krw:,.0f}원뿐입니다."
        )
    warnings.append(
        "1단계 호가만 보므로 금액 기준 판단은 /matrix 나 /arbitrage 를 쓰세요."
    )
    warnings.append(FEE_WARNING)

    return ScanResponse(
        dom=dom,
        fx=FX_EXCHANGE,
        usd_krw_rate=rate.ask,  # 표시용 ask
        rate_updated_at=_ms(rate.updated_at),
        scanned_coins=scanned_coins,
        scanned_pairs=scanned_pairs,
        excluded_bases=excluded,
        best_fwd=best_fwd,
        best_rev=best_rev,
        top_fwd=fwd_items[:limit],
        top_rev=rev_items[:limit],
        suspicious_count=sum(1 for i in fwd_items if i.suspicious)
        + sum(1 for i in rev_items if i.suspicious),
        warnings=warnings,
        data_received_at=_received_ms(store),
        fetched_at=_now_ms(),
    )


# --- GET /matrix (§3.2) ---


class _ComboPick:
    """코인 하나의 방향별 최대 조합 — 표면 김프가 선정 기준이다 (§3.2-3)."""

    def __init__(
        self,
        surface_percent: float,
        dom_ex: str,
        fx_ex: str,
        dom_row: Row,
        fx_row: Row,
        rate: Rate,
    ) -> None:
        self.surface_percent = surface_percent
        self.dom_ex = dom_ex
        self.fx_ex = fx_ex
        self.dom_row = dom_row
        self.fx_row = fx_row
        self.rate = rate


def _matrix_direction(
    pick: _ComboPick, direction: str, amount_krw: float
) -> MatrixDirection | None:
    """최대 조합을 amount_krw 로 walk 한다. 체결 0 이면 조합 없음 (§3.2-4)."""
    if direction == "fwd":
        # 해외 asks 에 사서(rate ask 환산) 국내 bids 에 판다
        buy_levels = [[lv[0] * pick.rate.ask, lv[1]] for lv in pick.fx_row.asks]
        sell_levels = pick.dom_row.bids
        buy_ex, sell_ex = pick.fx_ex, pick.dom_ex
        buy_row, sell_row = pick.fx_row, pick.dom_row
    else:
        # 국내 asks 에 사서 해외 bids 에 판다(rate bid 환산)
        buy_levels = pick.dom_row.asks
        sell_levels = [[lv[0] * pick.rate.bid, lv[1]] for lv in pick.fx_row.bids]
        buy_ex, sell_ex = pick.dom_ex, pick.fx_ex
        buy_row, sell_row = pick.dom_row, pick.fx_row

    buy_walk = walk_amount(buy_levels, amount_krw)
    if buy_walk.quantity <= 0:
        return None
    sell_walk = walk_quantity(sell_levels, buy_walk.quantity)
    if sell_walk.quantity <= 0:
        return None
    buy_exhausted = buy_walk.exhausted
    if sell_walk.exhausted and sell_walk.quantity < buy_walk.quantity:
        # 매도측 소진 시 판 수량만큼 매수를 되맞춘다 — arbitrage 와 동일 (§3.2-4)
        buy_walk = walk_quantity(buy_levels, sell_walk.quantity)
    effective = (
        (sell_walk.amount / buy_walk.amount - 1) * 100 if buy_walk.amount > 0 else 0.0
    )
    return MatrixDirection(
        buy_exchange=buy_ex,
        sell_exchange=sell_ex,
        premium_percent=pick.surface_percent,
        total_slippage_percent=pick.surface_percent - effective,
        withdrawal_available=buy_row.withdrawal_enabled,
        deposit_available=sell_row.deposit_enabled,
        depth_exhausted=buy_exhausted or sell_walk.exhausted,
    )


def build_matrix(store: LiveStore, *, amount_krw: float) -> MatrixResponse:
    if store.is_empty():
        raise _not_found("메모리에 스냅샷이 없습니다.")
    rates = {ex: r for ex, r in store.rates().items() if _valid_rate(r)}
    if not rates:
        raise _not_found("KRW-USDT 환율이 없습니다.")

    # 스냅샷을 호가통화로 나눈다 — KRW → 국내, USDT → 해외
    domestic: dict[str, dict[str, Row]] = {}
    foreign: dict[str, dict[str, Row]] = {}
    for row in store.get_all():
        if row.quote == DOMESTIC_QUOTE:
            domestic.setdefault(row.exchange, {})[row.base.upper()] = row
        elif row.quote == FOREIGN_QUOTE:
            foreign.setdefault(row.exchange, {})[row.base.upper()] = row

    dom_bases = {b for table in domestic.values() for b in table}
    fx_bases = {b for table in foreign.values() for b in table}
    coins: list[MatrixCoin] = []
    scanned_combinations = 0
    dom_set: set[str] = set()
    fx_set: set[str] = set()
    for base in sorted(dom_bases & fx_bases):  # 양쪽에 있는 코인만 (§3.2-1)
        if base in EXCLUDED_BASES:
            continue  # 제외 코인은 scan 과 동일 (§3.2-2)
        best_fwd: _ComboPick | None = None
        best_rev: _ComboPick | None = None
        for dom_ex in sorted(domestic):
            dom_row = domestic[dom_ex].get(base)
            if dom_row is None:
                continue
            rate = rates.get(dom_ex)
            if rate is None:
                continue  # 환율 없는 국내 거래소 조합은 건너뛴다 — 남의 테더 프리미엄을 빌리지 않는다 (§3.2-2)
            for fx_ex in sorted(foreign):
                if fx_ex == dom_ex:
                    continue
                fx_row = foreign[fx_ex].get(base)
                if fx_row is None:
                    continue
                counted = False
                if dom_row.bids and fx_row.asks:
                    pct = premium_percent(
                        buy_krw=fx_row.asks[0][0] * rate.ask,
                        sell_krw=dom_row.bids[0][0],
                    )
                    counted = True
                    if best_fwd is None or pct > best_fwd.surface_percent:
                        best_fwd = _ComboPick(pct, dom_ex, fx_ex, dom_row, fx_row, rate)
                if dom_row.asks and fx_row.bids:
                    pct = premium_percent(
                        buy_krw=dom_row.asks[0][0],
                        sell_krw=fx_row.bids[0][0] * rate.bid,
                    )
                    counted = True
                    if best_rev is None or pct > best_rev.surface_percent:
                        best_rev = _ComboPick(pct, dom_ex, fx_ex, dom_row, fx_row, rate)
                if counted:
                    scanned_combinations += 1
                    dom_set.add(dom_ex)
                    fx_set.add(fx_ex)

        fwd_entry = _matrix_direction(best_fwd, "fwd", amount_krw) if best_fwd else None
        rev_entry = _matrix_direction(best_rev, "rev", amount_krw) if best_rev else None
        if fwd_entry is None and rev_entry is None:
            continue  # 둘 다 없으면 행 제외 (§3.2-7)
        coins.append(
            MatrixCoin(
                sym=base,
                fwd=fwd_entry,
                rev=rev_entry,
                suspicious=fwd_entry is not None
                and fwd_entry.premium_percent >= SUSPICIOUS_PERCENT,
            )
        )

    # fwd 김프 내림차순, fwd 없는 행은 맨 뒤 — 동률은 코인명 순으로 고정 (§3.2-7)
    coins.sort(
        key=lambda c: (
            c.fwd is None,
            -(c.fwd.premium_percent if c.fwd is not None else 0.0),
            c.sym,
        )
    )

    # warnings 순서 고정 (§3.2-8)
    warnings: list[str] = []
    if amount_krw > NOTIONAL_CAP_KRW:
        warnings.append(CAP_WARNING)
    warnings.append(FEE_WARNING)
    entries = [e for c in coins for e in (c.fwd, c.rev) if e is not None]
    if any(
        not (e.withdrawal_available is True and e.deposit_available is True)
        for e in entries
    ):
        warnings.append(
            "입출금 막힘 표시 조합이 있습니다 — 실제 중단일 수도, 확인 못 한 것일 수도 있습니다(null)."
        )

    return MatrixResponse(
        amount_krw=amount_krw,
        scanned_coins=len(coins),
        scanned_combinations=scanned_combinations,
        dom_list=sorted(dom_set),
        fx_list=sorted(fx_set),
        coins=coins,
        warnings=warnings,
        data_received_at=_received_ms(store),
        fetched_at=_now_ms(),
    )
