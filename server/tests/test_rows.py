"""행 조립 규칙(스펙 001 §3.4·§3.5-1) — 호가 정리(단계 거르기·누적액 상한)·가격 폴백."""

import math
import random
from collections.abc import Callable
from typing import Any

import pytest

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


# --- 훑기 + 슬라이스 구현(2026-10-09)이 기존 append 구현과 같은지 ---


def _clean_levels_before(levels: list[list[float]], cap: float) -> list[list[float]]:
    """2026-10-09 이전 구현을 그대로 옮긴 대조용 — 단계마다 거르며 append 한다."""
    out: list[list[float]] = []
    cum = 0.0
    for level in levels:
        price, size = level
        if not (0.0 < price < math.inf and 0.0 < size < math.inf):
            continue
        out.append(level)
        cum += price * size
        if cum >= cap:
            break
    return out


def _outcome(
    fn: Callable[[list[Any], float], list[Any]], levels: list[Any], cap: float
) -> tuple[Any, ...]:
    """결과 원소 객체 id 목록, 또는 예외 (종류, 메시지) — 예외까지 같은지 본다."""
    try:
        got = fn(levels, cap)
    except Exception as exc:
        return ("raise", type(exc), str(exc))
    assert type(got) is list and got is not levels  # 늘 새 list
    return ("ok", [id(lv) for lv in got])


def test_clean_matches_the_append_implementation_down_to_objects_and_errors() -> None:
    """무효 단계 위치(맨 앞·중간·끝·연속)·상한 경계(0·음수·NaN·inf)·곱 오버플로·잘못된 원소까지 기존 구현과 같다."""
    rng = random.Random(20261009)
    nan, inf = math.nan, math.inf
    specials = [0.0, -0.0, -1.0, nan, inf, -inf, 5e-324, 1e300]
    for _ in range(4_000):
        n = rng.choice([0, 1, 2, 3, 15, 30, 200])
        bad_rate = rng.choice([0.0, 0.0, 0.02, 0.3, 1.0])
        levels: list[Any] = []
        for _ in range(n):
            p = (
                rng.choice(specials)
                if rng.random() < bad_rate
                else rng.uniform(1e-3, 1e5)
            )
            s = (
                rng.choice(specials)
                if rng.random() < bad_rate
                else rng.uniform(1e-4, 1e4)
            )
            r = rng.random()
            if r < 0.005:
                levels.append([p])  # 원소가 하나뿐 — 푸는 데서 ValueError
            elif r < 0.01:
                levels.append([p, s, 1.0])  # 원소가 셋 — 같은 ValueError
            elif r < 0.015:
                levels.append([p, "x"])  # 문자열 잔량 — 비교에서 TypeError
            elif r < 0.03:
                levels.append((p, s))  # 튜플 원소도 그대로 담긴다
            elif r < 0.05:
                levels.append([int(p) if math.isfinite(p) else p, 2])  # 정수 원소
            else:
                levels.append([p, s])
        cap = rng.choice([1e3, 1e6, 1e9, inf, 0.0, -1.0, nan, -inf])
        assert _outcome(clean_levels, levels, cap) == _outcome(
            _clean_levels_before, levels, cap
        )


def test_clean_invalid_first_level_starts_empty_and_continues() -> None:
    a, b = [10.0, 1.0], [11.0, 2.0]
    levels = [[0.0, 5.0], a, [math.nan, 1.0], b]
    got = clean_levels(levels, cap=math.inf)
    assert got == [[10.0, 1.0], [11.0, 2.0]] and got[0] is a and got[1] is b


def test_clean_invalid_level_in_the_middle_keeps_the_front_and_scans_the_rest() -> None:
    levels = [[1.0, 1.0], [2.0, 1.0], [3.0, 0.0], [4.0, 1.0], [-5.0, 1.0], [6.0, 1.0]]
    got = clean_levels(levels, cap=math.inf)
    assert got == [[1.0, 1.0], [2.0, 1.0], [4.0, 1.0], [6.0, 1.0]]
    assert [id(x) for x in got] == [id(levels[i]) for i in (0, 1, 3, 5)]


def test_clean_cap_reached_after_an_invalid_level_keeps_that_level() -> None:
    # 누적 100 → (무효) → 100+500=600 → 600+500=1100 ≥ 1000 에서 그 단계까지 담고 자른다
    levels = [[100.0, 1.0], [math.inf, 1.0], [500.0, 1.0], [500.0, 1.0], [7.0, 1.0]]
    assert clean_levels(levels, cap=1000.0) == [
        [100.0, 1.0],
        [500.0, 1.0],
        [500.0, 1.0],
    ]


def test_clean_cap_reached_without_invalid_levels_keeps_that_level() -> None:
    levels = [[100.0, 4.0], [300.0, 2.0], [1.0, 1.0]]  # 400 → 1000 ≥ 1000
    got = clean_levels(levels, cap=1000.0)
    assert got == [[100.0, 4.0], [300.0, 2.0]] and got is not levels


def test_clean_always_returns_a_new_list_even_when_nothing_is_dropped() -> None:
    """비트겟 북이 받은 목록을 그대로 들고 있다 — 행이 같은 목록을 나눠 쓰면 안 된다."""
    for levels in ([], [[1.0, 1.0]], [[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]]):
        got = clean_levels(levels, cap=math.inf)
        assert got == levels and got is not levels
        # 안쪽 [p, s] 는 같은 객체
        assert all(g is x for g, x in zip(got, levels, strict=True))


def test_clean_malformed_level_raises_only_when_scanned() -> None:
    """원소를 받은 순서대로 보다가 상한에서 멈춘다 — 상한 뒤의 잘못된 원소는 보지 않는다(기존과 같다)."""
    with pytest.raises(ValueError, match="too many values to unpack"):
        clean_levels([[1.0, 1.0], [1.0, 1.0, 1.0]], cap=math.inf)
    # 무효 단계 뒤(이어 훑기)에서도 같다
    with pytest.raises(ValueError, match="not enough values to unpack"):
        clean_levels([[0.0, 1.0], [1.0]], cap=math.inf)
    assert clean_levels([[2_000.0, 1.0], [1.0]], cap=1000.0) == [[2_000.0, 1.0]]
