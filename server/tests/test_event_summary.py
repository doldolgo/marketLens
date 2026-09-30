"""랜딩 사건 요약 조회 `query_event_summary` — 점을 전부 올려 센 기준선과 같은 값 (022 §3.2, 2026-09-28).

가짜 Influx(`event_summary_fakes.FluxEvents`)가 요약·되묻기·상세 Flux 에 CSV 로 답한다 — `top()` 의 동률 순서는
일부러 base 내림차순이라, 후보 끝자리에서 잘린 코인을 되묻지 않으면 기준선과 달라진다. 네트워크 없음.
"""

import random

import pytest

import app.core.influx as influx_mod
from app.core.influx import InfluxClient, PremiumEventRow
from tests.event_summary_fakes import FluxEvents, reference_summary

NOW = 1_790_526_384
START = NOW - 604_800


def _event(
    base: str,
    start_ts: int,
    *,
    end_ts: int = 0,
    last_ts: int | None = None,
    dom: str = "upbit",
    fx: str = "binance",
    dir: str = "kimp",
    max_percent: float = 1.5,
) -> PremiumEventRow:
    return PremiumEventRow(
        dom=dom,
        fx=fx,
        base=base,
        dir=dir,
        start_ts=start_ts,
        end_ts=end_ts,
        duration_seconds=end_ts - start_ts if end_ts else 0,
        max_percent=max_percent,
        max_ts=start_ts + 1,
        last_ts=last_ts if last_ts is not None else (end_ts or start_ts + 90),
        samples=90,
        enter_percent=1.0,
        exit_percent=0.5,
    )


def _summary(
    monkeypatch: pytest.MonkeyPatch, rows: list[PremiumEventRow], chunk: int = 4096
) -> tuple[object, FluxEvents]:
    fake = FluxEvents(rows, chunk=chunk)
    monkeypatch.setattr(influx_mod, "InfluxDBClient", fake)
    got = InfluxClient(url="http://influx.test", token="t").query_event_summary(
        start=START, stop=NOW, open_since=NOW - 600, top_n=5
    )
    return got, fake


def _want(rows: list[PremiumEventRow]) -> object:
    return reference_summary(rows, start=START, stop=NOW, open_since=NOW - 600, top_n=5)


def test_open_counts_live_routes_once_and_skips_orphans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _event("OLD", NOW - 90_000, last_ts=NOW - 601),  # 고아 — 복원이라면 닫을 점
        _event("EDGE", NOW - 5_000, last_ts=NOW - 600),  # 600초 경계는 든다
        # 재기동 직후 — 닫히지 못한 옛 점과 같은 조합의 새 사건: 한 번만
        _event("RE", NOW - 50_000, dom="bithumb", fx="bybit", last_ts=NOW - 400),
        _event("RE", NOW - 200, dom="bithumb", fx="bybit", last_ts=NOW - 10),
        _event("RE", NOW - 300, dom="upbit", fx="bybit", last_ts=NOW - 10),  # 다른 조합
        _event(
            "DONE", NOW - 900, end_ts=NOW - 100
        ),  # 막 끝남 — last_ts 가 최근이어도 닫혔다
        _event("GONE", NOW - 700_000, last_ts=NOW - 5),  # 창 밖에서 시작
    ]
    got, _ = _summary(monkeypatch, rows)
    assert got.open == 3  # type: ignore[attr-defined]
    assert (got.kimp, got.reverse) == (6, 0)  # type: ignore[attr-defined]
    assert got == _want(rows)


def test_same_end_in_one_coin_keeps_the_later_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    end = NOW - 5
    rows = [
        # 같은 코인이 두 거래소에서 같은 틱에 닫힘 — 늦게 시작한 bybit 가 남는다
        _event("ZZTIE", NOW - 900, end_ts=end, dom="bithumb", fx="binance", dir="reverse"),
        _event("ZZTIE", NOW - 600, end_ts=end, dom="bithumb", fx="bybit", dir="reverse"),
        # 시작까지 같으면 (dom, fx, dir) 이 앞서는 것
        _event("TWIN", NOW - 800, end_ts=end - 1, fx="bybit"),
        _event("TWIN", NOW - 800, end_ts=end - 1, fx="binance"),
        _event("AAA", NOW - 700, end_ts=end - 1, max_percent=2.0),
    ]  # fmt: skip
    got, fake = _summary(monkeypatch, rows)
    latest = got.latest  # type: ignore[attr-defined]
    assert [(r.base, r.fx, r.start_ts) for r in latest] == [
        ("ZZTIE", "bybit", NOW - 600),
        ("AAA", "binance", NOW - 700),
        ("TWIN", "binance", NOW - 800),
    ]
    assert latest[1].max_percent == 2.0 and latest[1].duration_seconds == 694
    assert got == _want(rows)
    assert (
        len(fake.flux) == 2
    )  # 요약 한 번 + 상세 한 번 — 후보가 다 들어와 되묻지 않는다


def test_a_tie_cut_at_the_candidate_edge_asks_again_with_every_closed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 재기동이 고아 260건을 같은 시각에 닫은 직후 — 후보 200개 모두 같은 끝 시각이라 순위가 확정되지 않는다
    cluster = NOW - 30
    rows = [
        _event(f"Q{i:03d}", NOW - 5_000 - i, end_ts=cluster, dir="reverse")
        for i in range(260)
    ] + [
        _event(f"R{i:03d}", NOW - 90_000 + i, end_ts=NOW - 80_000 + i)
        for i in range(50)
    ]
    got, fake = _summary(monkeypatch, rows)
    # base 내림차순으로 잘린 후보로 끝냈다면 Q259… 가 나왔을 것이다
    assert [r.base for r in got.latest] == ["Q000", "Q001", "Q002", "Q003", "Q004"]  # type: ignore[attr-defined]
    assert got == _want(rows)
    assert len(fake.flux) == 3
    assert "top(" not in fake.flux[1] and "r._value > 0" in fake.flux[1]


def test_random_event_sets_match_the_full_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    rnd = random.Random(3)
    for trial in range(40):
        rows = []
        coins = [f"C{i:02d}" for i in range(rnd.randint(1, 60))]
        for _ in range(rnd.randint(0, 600)):
            base = rnd.choice(coins)
            start = NOW - rnd.randint(1, 604_700)
            closed = rnd.random() < 0.8
            # 끝 시각을 몇 개 값에 몰아 동률을 많이 만든다
            end = min(NOW - 1, start + rnd.choice((61, 120, 3_600))) if closed else 0
            if closed and rnd.random() < 0.3:
                end = max(start + 61, NOW - rnd.choice((10, 20, 30)))
            rows.append(
                _event(
                    base,
                    start,
                    end_ts=end,
                    last_ts=end or NOW - rnd.choice((5, 599, 600, 601, 5_000)),
                    dom=rnd.choice(("upbit", "bithumb")),
                    fx=rnd.choice(("binance", "bybit", "bitget")),
                    dir=rnd.choice(("kimp", "reverse")),
                    max_percent=round(rnd.uniform(1, 9), 3),
                )
            )
        got, _ = _summary(monkeypatch, rows, chunk=rnd.choice((5, 64, 4096)))
        assert got == _want(rows), trial


def test_summary_folds_in_flux_without_pivoting_every_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_event("AAA", NOW - 900, end_ts=NOW - 100)]
    _, fake = _summary(monkeypatch, rows)
    first, details = fake.flux
    window = "range(start: 2026-09-20T16:26:24Z, stop: 2026-09-27T16:26:24Z)"
    assert window in first and window in details  # 두 요청 모두 같은 7일 창
    assert "pivot(" not in first
    assert 'r._field == "end_ts"' in first and 'r._field == "last_ts"' in first
    assert f"r._value >= {NOW - 600}" in first
    assert "top(n: 200" in first
    # 상세는 고른 코인만 좁혀 세 필드
    assert "r.base =~ /^(AAA)$/" in details and "pivot(" in details
    assert "max_percent" in details and "samples" not in details


def test_a_picked_half_point_without_details_drops_out_of_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 반쪽 점(끝난 시각만 있고 last_ts·max_percent 가 없는 점)도 후보에 들지만, 상세가 없어 top 에서 빠진다 —
    # 같은 코인의 다음 사건으로 채우지 않는다 (022 §3.2). 실데이터에는 없는 모양이다
    half = _event("HALF", NOW - 900, end_ts=NOW - 10)
    half = PremiumEventRow(**{**half.__dict__, "last_ts": None, "max_percent": None})  # type: ignore[arg-type]
    rows = [
        half,
        _event("HALF", NOW - 5_000, end_ts=NOW - 4_000),
        _event("AAA", NOW - 700, end_ts=NOW - 20),
    ]
    got, _ = _summary(monkeypatch, rows)
    assert [r.base for r in got.latest] == ["AAA"]  # type: ignore[attr-defined]
    assert (got.kimp, got.reverse) == (3, 0)  # type: ignore[attr-defined]
