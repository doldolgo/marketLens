"""행 조립 규칙(스펙 001 §3.4) — 호가 누적액 상한·가격 폴백."""

import math

from app.core.rows import resolve_price, truncate_levels


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
