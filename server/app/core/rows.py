"""행 갱신 규칙(스펙 001 §3.5)의 공용 순수 함수.

거래소별 메시지 형식·quirk 는 각 커넥터가 자기 안에서 흡수하고,
여기는 거래소와 무관한 스펙 공통 규칙(단계 필터·누적액 상한·가격 폴백)만 둔다.
"""

import math
from itertools import islice

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

    여섯 커넥터의 호가가 전부 이 한 곳을 지난다 — 가격 0·NaN·inf 단계가 행에 들어가면 표·틱·분석 API 가
    0 나누기·NaN 직렬화로 깨지므로 여기서 막는다. 거르기와 누적 상한을 한 번 훑기로 한다(호가 메시지마다
    두 번 불리는 뜨거운 경로). 담는 원소·순서·누적 순서는 거른 뒤 `truncate_levels` 를 부른 것과 같다.

    실데이터에는 버릴 단계가 거의 없다(2026-10-09 캡처 60초: 빗썸 목록의 4.6%에만 끼고 다른 다섯 곳은 0건). 그래서
    단계마다 새 목록에 append 하지 않고, 유효한 앞부분의 길이만 세다가 끝에서 슬라이스 한 번(C 수준 복사)으로 잘라
    돌려준다 — 바이빗 200단계 목록에서 단계마다 부르던 append 가 사라진다(같은 날 점검: 말뭉치 기준 이 함수 −8~9%).
    무효 단계를 만나면 그 앞까지는 슬라이스로 담고 나머지만 `_clean_rest` 가 기존 방식으로 이어 훑는다. 처음부터 다시
    훑지 않으므로 여전히 한 번 훑기이고, 보는 단계·비교·누적 순서가 그대로라 결과가 비트 단위로 같다.
    돌려주는 것은 늘 새 목록이다(입력 목록 자체를 돌려주지 않는다) — 비트겟 북이 받은 목록을 그대로 들고 있어서 행과
    같은 목록을 나눠 쓰면 안 된다. 입력은 list 라고 본다(여섯 커넥터 모두 list 를 넘긴다) — 튜플이면 슬라이스가
    튜플을 돌려준다.
    """
    cum = 0.0
    n = 0
    # 원소를 바로 두 값으로 푼다 — [p, s] 가 아니면 기준처럼 ValueError 다
    for price, size in levels:
        # 연쇄 비교 하나로 "0 초과·유한" — NaN 은 모든 비교가 거짓이라 여기서 빠진다
        if not (0.0 < price < _INF and 0.0 < size < _INF):
            return _clean_rest(levels, cap, n, cum)
        n += 1
        cum += price * size
        if cum >= cap:
            return levels[:n]  # 상한에 닿은 단계까지 포함한다
    return levels[:]  # 버릴 것도 자를 것도 없다 — 그래도 새 목록


def _clean_rest(
    levels: list[list[float]], cap: float, n: int, cum: float
) -> list[list[float]]:
    """`clean_levels` 가 n 번째(0부터) 단계에서 무효 단계를 만났을 때 — 앞 n 단계는 그대로 담고 n+1 번째부터 이어 훑는다.

    `cum` 은 앞 n 단계의 누적액이다(그 단계들에서는 상한에 닿지 않았다). 여기부터는 기존 방식(거르며 append)이라
    무효 단계가 여럿이어도 같다.
    """
    out = levels[:n]
    for level in islice(levels, n + 1, None):
        price, size = level
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
