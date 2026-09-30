"""행 조립 규칙(스펙 001 §3.4·§3.5-1) — 호가 정리(단계 거르기·누적액 상한)·가격 폴백."""

import math
import random

from app.core.rows import clean_levels, resolve_price, truncate_levels


def test_truncate_includes_level_that_reaches_cap() -> None:
    # 2단계에서 누적 900+200=1100 ≥ 1000 → 2단계까지 포함하고 자른다
    levels = [[90.0, 10.0], [100.0, 2.0], [110.0, 5.0]]
    assert truncate_levels(levels, cap=1000.0) == [[90.0, 10.0], [100.0, 2.0]]


def test_truncate_first_level_alone_reaches_cap() -> None:
    levels = [[1_000_000_000.0, 2.0], [999.0, 1.0]]
    assert truncate_levels(levels) == [[1_000_000_000.0, 2.0]]


def test_truncate_inf_keeps_everything() -> None:
    levels = [[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]]
    assert truncate_levels(levels, cap=math.inf) == levels


def test_truncate_empty_input_is_empty() -> None:
    assert truncate_levels([], cap=1000.0) == []


def test_price_is_trade_price_when_positive() -> None:
    assert resolve_price(100.5, [[99.0, 1.0]], [[101.0, 1.0]]) == 100.5


def test_price_falls_back_to_mid_when_missing_or_zero() -> None:
    assert resolve_price(None, [[99.0, 1.0]], [[101.0, 1.0]]) == 100.0
    assert resolve_price(0.0, [[99.0, 1.0]], [[101.0, 1.0]]) == 100.0


def test_price_none_when_no_trade_and_no_quotes() -> None:
    assert resolve_price(None, [], []) is None
    assert resolve_price(0.0, [[99.0, 1.0]], []) is None


# --- 호가 정리 clean_levels (§3.5-1) ---


def test_clean_keeps_only_positive_finite_price_and_size_in_received_order() -> None:
    nan, inf = math.nan, math.inf
    good = [[3.0, 1.0], [1.0, 2.0], [2.0, 0.5]]  # 정렬하지 않는다 — 받은 순서 그대로
    bad = [
        [0.0, 1.0],
        [-1.0, 1.0],
        [nan, 1.0],
        [inf, 1.0],
        [1.0, 0.0],
        [1.0, -1.0],
        [1.0, nan],
        [1.0, inf],
    ]
    mixed = [
        bad[0],
        good[0],
        bad[2],
        bad[4],
        good[1],
        bad[3],
        bad[6],
        good[2],
        bad[7],
        bad[1],
        bad[5],
    ]
    assert clean_levels(mixed, cap=math.inf) == good


def test_clean_is_the_same_as_filter_then_truncate_down_to_the_objects() -> None:
    """한 번 훑기의 결과는 거른 뒤 누적 상한으로 자른 것과 원소 객체·순서까지 같다 (F-BOOK-3)."""
    rng = random.Random(7)
    for _ in range(500):
        n = rng.randint(0, 40)
        levels = [
            [
                rng.choice([0.0, -1.0, math.nan, rng.uniform(1, 200)]),
                rng.choice([0.0, rng.uniform(0.001, 50)]),
            ]
            for _ in range(n)
        ]
        cap = rng.choice([math.inf, 500.0, 5_000.0])
        kept = [lv for lv in levels if 0 < lv[0] < math.inf and 0 < lv[1] < math.inf]
        expected = truncate_levels(kept, cap)
        got = clean_levels(levels, cap)
        assert len(got) == len(expected) and all(
            a is b for a, b in zip(got, expected, strict=True)
        )


def test_clean_cuts_at_the_level_that_reaches_the_cap_and_keeps_the_first() -> None:
    assert clean_levels([[90.0, 10.0], [100.0, 2.0], [110.0, 5.0]], cap=1000.0) == [
        [90.0, 10.0],
        [100.0, 2.0],
    ]
    assert clean_levels([[2_000.0, 1.0], [1.0, 1.0]], cap=1000.0) == [[2_000.0, 1.0]]
    assert clean_levels([], cap=1000.0) == []
