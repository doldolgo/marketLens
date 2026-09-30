"""행 갱신 규칙(스펙 001 §3.5)의 공용 순수 함수.

거래소별 메시지 형식·quirk 는 각 커넥터가 자기 안에서 흡수하고,
여기는 거래소와 무관한 스펙 공통 규칙(단계 필터·누적액 상한·가격 폴백)만 둔다.
"""

import math

# 국내 호가는 누적 price×size 가 이 값(KRW)에 도달한 단계까지만 저장한다 — 스펙 001 §3.1
NOTIONAL_CAP_KRW: float = 1_000_000_000

# 바이낸스(012)는 국내 상한과 대칭으로 누적 이만큼(USDT)까지만 담는다 — 스펙 001 §3.1
NOTIONAL_CAP_USDT: float = 1_000_000

_INF = math.inf


def truncate_levels(
    levels: list[list[float]], cap: float = NOTIONAL_CAP_KRW
) -> list[list[float]]:
    """누적 price×size 가 cap 에 도달한 단계까지 포함하고 자른다.

    cap 이 inf 면 전부 남고, 빈 입력은 빈 목록이다. 첫 단계가 이미 넘어도 1단계는 남는다.
    """
    out: list[list[float]] = []
    cum = 0.0
    for level in levels:
        out.append(level)
        cum += level[0] * level[1]
        if cum >= cap:
            break
    return out


def clean_levels(
    levels: list[list[float]], cap: float = NOTIONAL_CAP_KRW
) -> list[list[float]]:
    """§3.5-1: 받은 순서대로 담되 가격·잔량이 0 이하이거나 유한하지 않은 단계는 버리고, 누적액 상한까지 자른다.

    다섯 커넥터의 호가가 전부 이 한 곳을 지난다 — 가격 0·NaN·inf 단계가 행에 들어가면 표·틱·분석 API 가
    0 나누기·NaN 직렬화로 깨지므로 여기서 막는다. 거르기와 누적 상한을 한 번 훑기로 한다(호가 메시지마다
    두 번 불리는 뜨거운 경로). 담는 원소·순서·누적 순서는 거른 뒤 `truncate_levels` 를 부른 것과 같다.
    """
    out: list[list[float]] = []
    cum = 0.0
    for level in levels:
        price, size = level
        # 연쇄 비교 하나로 "0 초과·유한" — NaN 은 모든 비교가 거짓이라 여기서 빠진다
        if not (0.0 < price < _INF and 0.0 < size < _INF):
            continue
        out.append(level)
        cum += price * size
        if cum >= cap:
            break
    return out


def resolve_price(
    trade_price: float | None,
    bids: list[list[float]],
    asks: list[list[float]],
) -> float | None:
    """price = 마지막 체결가. 없거나 0 이하면 (bid+ask)/2. 그것도 없으면 None."""
    if trade_price is not None and trade_price > 0:
        return float(trade_price)
    if bids and asks:
        return (bids[0][0] + asks[0][0]) / 2
    return None
