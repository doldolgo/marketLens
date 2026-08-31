"""김프 수식 공유 모듈 — 스펙 003 §2.

003(spreads)의 fwd/rev 계산이 이 함수를 쓰고, 004(analysis)·005(history)도 쓴다.
"""


def premium_percent(*, buy_krw: float, sell_krw: float) -> float:
    """산 값(KRW) 대비 판 값(KRW)의 프리미엄 %. 값은 `(sell/buy − 1) × 100`."""
    return (sell_krw / buy_krw - 1) * 100
