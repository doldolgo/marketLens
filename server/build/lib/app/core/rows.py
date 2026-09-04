"""행 조립 규칙(스펙 001 §3.4)의 공용 순수 함수.

거래소별 quirk 는 각 커넥터가 자기 안에서 흡수하고,
여기는 거래소와 무관한 스펙 공통 규칙(가격 폴백·호가 누적액 상한)만 둔다.
"""

# 국내 호가는 누적 price×size 가 이 값(KRW)에 도달한 단계까지만 저장한다 — 스펙 001 §3.1
NOTIONAL_CAP_KRW: float = 1_000_000_000

# 바이낸스 깊이(012)는 국내 상한과 대칭으로 누적 이만큼(USDT)까지만 담는다 — 스펙 001 §3.4-6.
# 003 이 제공하는 최대 체결 규모 $500k 의 2배 여유다.
NOTIONAL_CAP_USDT: float = 1_000_000


def truncate_levels(
    levels: list[list[float]], cap: float = NOTIONAL_CAP_KRW
) -> list[list[float]]:
    """누적 price×size 가 cap 에 도달한 단계까지 포함하고 자른다.

    cap 이 inf 면 전부 남고, 빈 입력은 빈 목록이다.
    """
    out: list[list[float]] = []
    cum = 0.0
    for level in levels:
        out.append(level)
        cum += level[0] * level[1]
        if cum >= cap:
            break
    return out


def resolve_price(
    trade_price: float | None,
    bids: list[list[float]],
    asks: list[list[float]],
) -> float | None:
    """price = 마지막 체결가. 없거나 0 이하면 (bid+ask)/2. 그것도 없으면 None(그 코인 건너뜀)."""
    if trade_price is not None and trade_price > 0:
        return float(trade_price)
    if bids and asks:
        return (bids[0][0] + asks[0][0]) / 2
    return None
