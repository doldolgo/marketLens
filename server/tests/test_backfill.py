"""백필 순수 계산 — 대상 구간 계산·병합 규칙 (스펙 005 §3.5, §4). 네트워크 없음."""

import pytest

from app.core.premium import premium_percent
from scripts.backfill import (
    DAY,
    dedup_changes,
    merge_premiums,
    plan_day_slices,
    rates_for_slice,
)

# UTC 하루 경계에 정렬된 기준 시각
D0 = 20_000 * DAY


def test_no_records_covers_whole_range_newest_first() -> None:
    # 기록 없음 → 전체 구간, 최신 날부터 거꾸로 (§3.5)
    slices = plan_day_slices(D0, D0 + 3 * DAY, None)
    assert slices == [
        (D0 + 2 * DAY, D0 + 3 * DAY),
        (D0 + DAY, D0 + 2 * DAY),
        (D0, D0 + DAY),
    ]


def test_existing_records_leave_middle_untouched() -> None:
    # 기록 있음 → [목표시작, 첫 time) 과 [마지막 time+1, 목표끝) 만 (§3.5)
    first = D0 + DAY + 3600  # 둘째 날 01:00
    last = D0 + 2 * DAY + 7200  # 셋째 날 02:00
    slices = plan_day_slices(D0, D0 + 4 * DAY, (first, last))
    assert slices == [
        # 앞: 최신 날부터 거꾸로, 첫 time 에서 잘린다
        (D0 + DAY, first),
        (D0, D0 + DAY),
        # 뒤: 마지막 time+1 부터 순방향
        (last + 1, D0 + 3 * DAY),
        (D0 + 3 * DAY, D0 + 4 * DAY),
    ]


def test_fully_covered_range_has_no_slices() -> None:
    # 앞뒤가 모두 채워져 있으면 대상 구간이 없다 → "이미 전부 채워져" (§3.5)
    slices = plan_day_slices(D0, D0 + 2 * DAY, (D0 - 10, D0 + 2 * DAY - 1))
    assert slices == []


def test_records_outside_target_are_clipped() -> None:
    # 첫 time 이 목표시작 이전이면 앞 구간 없음, 마지막 time+1 도 목표시작 아래로 내려가지 않는다
    slices = plan_day_slices(D0, D0 + DAY, (D0 - 5 * DAY, D0 - 5 * DAY + 10))
    assert slices == [(D0, D0 + DAY)]


def test_dedup_removes_consecutive_equal_values() -> None:
    # 각 변동 목록은 직전과 같은 값을 제거한다 (§3.5 합치기)
    assert dedup_changes([(1, 5.0), (2, 5.0), (3, 6.0), (4, 6.0), (5, 5.0)]) == [
        (1, 5.0),
        (3, 6.0),
        (5, 5.0),
    ]


def test_merge_skips_until_all_three_present() -> None:
    # 셋이 다 갖춰지기 전 ts 는 건너뜀 (§4)
    out = merge_premiums(
        [(D0 + 10, 141_000.0)],  # dom
        [(D0 + 5, 100.0)],  # fx
        [(D0 + 20, 1400.0)],  # rate — 20초부터 셋이 갖춰진다
        start=D0,
        stop=D0 + DAY,
    )
    assert [ts for ts, _, _ in out] == [D0 + 20]


def test_merge_symmetric_close_formula() -> None:
    # 종가 대칭식: ratio = dom/(fx×rate), fwd=(ratio−1)×100, rev=(1/ratio−1)×100 (§3.5)
    out = merge_premiums(
        [(D0, 144_900.0)],
        [(D0, 100.0)],
        [(D0 - 300, 1400.0)],  # 환율 씨앗 — 하루 시작 이전 최신 분봉 (§3.5)
        start=D0,
        stop=D0 + DAY,
    )
    assert len(out) == 1
    ts, fwd, rev = out[0]
    assert ts == D0
    ratio = 144_900.0 / (100.0 * 1400.0)
    assert fwd == pytest.approx((ratio - 1) * 100)
    assert rev == pytest.approx((1 / ratio - 1) * 100)
    assert fwd == premium_percent(buy_krw=100.0 * 1400.0, sell_krw=144_900.0)


def test_merge_skips_unchanged_fwd() -> None:
    # fwd 가 직전과 같으면 기록하지 않는다 (§4)
    out = merge_premiums(
        [(D0, 140_000.0), (D0 + 60, 141_400.0)],
        [(D0, 100.0), (D0 + 60, 101.0)],  # 60초에 dom·fx 가 같은 비율로 움직여 fwd 불변
        [(D0 - 60, 1400.0)],
        start=D0,
        stop=D0 + DAY,
    )
    assert [ts for ts, _, _ in out] == [D0]


def test_merge_skips_nonpositive_values() -> None:
    # 셋 중 ≤ 0 이면 건너뜀 (§3.5)
    out = merge_premiums(
        [(D0, 0.0), (D0 + 10, 140_000.0)],
        [(D0, 100.0)],
        [(D0 - 60, 1400.0)],
        start=D0,
        stop=D0 + DAY,
    )
    assert [ts for ts, _, _ in out] == [D0 + 10]


def test_merge_respects_stop_boundary() -> None:
    # stop 이후 ts 는 기록하지 않는다 (UTC 하루 단위 처리)
    out = merge_premiums(
        [(D0, 140_000.0), (D0 + DAY, 150_000.0)],
        [(D0, 100.0)],
        [(D0 - 60, 1400.0)],
        start=D0,
        stop=D0 + DAY,
    )
    assert [ts for ts, _, _ in out] == [D0]


def test_rates_for_slice_includes_seed() -> None:
    # 환율 씨앗 = 하루 시작 이전 최신 분봉 1개 + 구간 안 변동 (§3.5)
    rates = [
        (D0 - 600, 1399.0),
        (D0 - 60, 1400.0),
        (D0 + 30, 1401.0),
        (D0 + DAY, 1402.0),
    ]
    assert rates_for_slice(rates, D0, D0 + DAY) == [
        (D0 - 60, 1400.0),
        (D0 + 30, 1401.0),
    ]


def test_rates_for_slice_without_seed() -> None:
    rates = [(D0 + 30, 1401.0)]
    assert rates_for_slice(rates, D0, D0 + DAY) == [(D0 + 30, 1401.0)]


# ---- 리뷰 확정 결함 회귀: 부분 조각 빈 응답은 중단이 아니다 (005 §3.5) ----


def test_is_full_day_only_for_whole_day_slices():
    from scripts.backfill import DAY, is_full_day

    assert is_full_day(0, DAY)
    assert is_full_day(DAY * 3, DAY * 4)
    # 첫 기록과 맞닿은 부분 조각 [그날 00:00, 첫 기록) — 정의상 업비트 초봉이 없다
    assert not is_full_day(DAY * 9, DAY * 9 + 3)
    # 목표 경계와 맞닿은 부분 조각
    assert not is_full_day(DAY * 2 + 100, DAY * 3)
    assert not is_full_day(DAY * 2 + 100, DAY * 2 + 200)


def test_plan_newest_front_slice_is_partial_when_first_not_on_boundary():
    from scripts.backfill import DAY, is_full_day, plan_day_slices

    # 첫 기록이 자정 3초 뒤: 가장 먼저 처리되는 front 조각은 3초짜리 부분 조각이고,
    # 이 조각에서 빈 응답이 나도 중단하지 않아야 남은 과거 날들이 채워진다
    slices = plan_day_slices(0, DAY * 10, (DAY * 9 + 3, DAY * 9 + 500))
    assert slices[0] == (DAY * 9, DAY * 9 + 3)
    assert not is_full_day(*slices[0])
    assert all(is_full_day(*s) for s in slices[1:10])
