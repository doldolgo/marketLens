"""호가창 소진(walk) 계산 — 스펙 004 §3.1. analysis 기능 폴더 안에만 산다 (§2).

levels 는 체결되는 쪽 호가([price, size] 목록, 최우선부터).
금액(quote 통화) 기준으로 사거나 팔고, 수량 기준도 대칭이다.
"""

from dataclasses import dataclass

# 부동소수 잔액 찌꺼기를 "소진"으로 오판하지 않기 위한 허용 오차
_EPSILON = 1e-9


@dataclass
class WalkResult:
    quantity: float  # 실제 체결 수량
    amount: float  # 실제 체결 금액 (quote 통화)
    levels_consumed: int  # 먹은 단계 수
    exhausted: bool  # 전 단계를 먹고도 입력이 남았다


def walk_amount(levels: list[list[float]], amount: float) -> WalkResult:
    """금액 기준 소진 — 한 단계의 price×size 가 남은 금액 이상이면 그 단계에서 부분 체결하고 끝."""
    if amount <= 0 or not levels:
        # 입력 ≤ 0 또는 빈 호가 → 체결 0 (§3.1-4)
        return WalkResult(quantity=0.0, amount=0.0, levels_consumed=0, exhausted=False)
    remaining = amount
    quantity = 0.0
    filled = 0.0
    consumed = 0
    for price, size in ((level[0], level[1]) for level in levels):
        consumed += 1
        level_amount = price * size
        if level_amount >= remaining:
            quantity += remaining / price
            filled += remaining
            remaining = 0.0
            break
        quantity += size
        filled += level_amount
        remaining -= level_amount
    return WalkResult(
        quantity=quantity,
        amount=filled,
        levels_consumed=consumed,
        exhausted=remaining > _EPSILON,
    )


def walk_quantity(levels: list[list[float]], quantity: float) -> WalkResult:
    """수량 기준 소진 — 금액 기준과 대칭. 부족하면 quantity 는 실제 체결량 (§3.1-3)."""
    if quantity <= 0 or not levels:
        return WalkResult(quantity=0.0, amount=0.0, levels_consumed=0, exhausted=False)
    remaining = quantity
    filled_qty = 0.0
    filled_amount = 0.0
    consumed = 0
    for price, size in ((level[0], level[1]) for level in levels):
        consumed += 1
        if size >= remaining:
            filled_qty += remaining
            filled_amount += remaining * price
            remaining = 0.0
            break
        filled_qty += size
        filled_amount += size * price
        remaining -= size
    return WalkResult(
        quantity=filled_qty,
        amount=filled_amount,
        levels_consumed=consumed,
        exhausted=remaining > _EPSILON,
    )


def average_price(result: WalkResult) -> float:
    """average_price = amount/quantity, 수량 0 이면 0 (§3.1-4)."""
    return result.amount / result.quantity if result.quantity > 0 else 0.0


def slippage_percent(side: str, best: float, average: float) -> float:
    """불리한 쪽이 양수 — 매수는 평균이 비쌀수록, 매도는 평균이 쌀수록. best ≤ 0 이면 0 (§3.1-5)."""
    if best <= 0 or average <= 0:
        return 0.0
    if side == "sell":
        return max(0.0, (best - average) / best * 100)
    return max(0.0, (average - best) / best * 100)
