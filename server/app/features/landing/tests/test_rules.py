"""`GET /landing` 의 계산 규칙 — live 후보·정렬·over1, trail, events (스펙 022 §3.2·§4).

공개 동작(HTTP 응답)만 본다. Redis·Influx 는 fake 다(helpers).
"""

from app.features.landing.tests.helpers import (
    NOW,
    FakeBus,
    FakeInflux,
    candle,
    event,
    make_client,
    row,
    table,
)


def _live(rows: list[dict], **table_kw: object) -> dict:
    body = make_client(FakeBus(table(rows, **table_kw))).get("/landing").json()
    return body["live"]


def _routes(live: dict) -> list[tuple[str, str, str, str, float]]:
    return [(t["sym"], t["dom"], t["fx"], t["dir"], t["pct"]) for t in live["top"]]


# ---- live: 옮길 수 있는 방향만 후보다 ----


def test_only_movable_directions_are_candidates_and_null_is_not_open() -> None:
    rows = [
        # 김프만 열림(해외 출금·국내 입금) — 역프 쪽 국내 출금이 막혔다
        row("AAA", fwd=2.0, rev=3.0, wd_dom=False),
        # 역프만 열림(국내 출금·해외 입금) — 김프 쪽 해외 출금이 모름(null)
        row("BBB", fwd=5.0, rev=1.5, wd_fx=None),
        # 두 방향 모두 한쪽이 모름 — 값이 커도 후보가 아니다
        row("CCC", fwd=9.0, rev=9.0, dep_dom=None, wd_dom=None),
    ]
    live = _live(rows)
    assert _routes(live) == [
        ("AAA", "upbit", "binance", "kimp", 2.0),
        ("BBB", "upbit", "binance", "reverse", 1.5),
    ]


def test_route_carries_direction_value_slip_and_row_fields() -> None:
    rows = [
        row(
            "VERONA",
            dom="bithumb",
            fx="bybit",
            fwd=-0.4,
            rev=2.26,
            slip_fwd=0.07,
            slip_rev=0.05,
            krw=12.4,
            usd=0.0089,
            net_dom="ERC20",
            net_fx="Ethereum",
        ),
        row("NONET", fwd=0.3, rev=-0.5, net_dom=None, net_fx=None),
    ]
    live = _live(rows, rate=1360.0, received=1_790_509_107_000)
    assert live["top"][0] == {
        "sym": "VERONA",
        "dom": "bithumb",
        "fx": "bybit",
        "dir": "reverse",
        "pct": 2.26,
        "slip": 0.05,  # 역프 방향의 차감폭
        "krw": 12.4,
        "usd": 0.0089,
        "netDom": "ERC20",
        "netFx": "Ethereum",
    }
    assert live["top"][1]["slip"] == 0.05  # 김프 방향의 차감폭(slipFwd 기본값)
    assert live["top"][1]["netDom"] is None and live["top"][1]["netFx"] is None
    assert live["rate"] == 1360.0
    assert live["dataReceivedAt"] == 1_790_509_107_000


# ---- live: stale·fail 은 빠지고 coins·pairs 는 상태와 무관 ----


def test_stale_and_fail_rows_are_out_of_top_and_over1_but_counted_in_coins_pairs() -> (
    None
):
    rows = [
        row("XXX", fwd=1.2, rev=-1.3),
        row("YYY", fwd=5.0, rev=-5.1, status="stale"),
        row("ZZZ", fwd=-3.1, rev=3.0, status="fail"),
        row("XXX", dom="bithumb", fwd=0.1, rev=-0.2),
    ]
    live = _live(rows)
    assert _routes(live) == [("XXX", "upbit", "binance", "kimp", 1.2)]
    assert live["over1"] == 1 and live["over1Movable"] == 1
    assert live["coins"] == 3
    assert live["pairs"] == 4


# ---- live: 코인당 1개, 값 내림차순 5개, 동률은 sym 오름차순, 0 이하도 들어간다 ----


def test_one_route_per_coin_top_five_desc_ties_by_sym_and_non_positive_included() -> (
    None
):
    rows = [
        row("BBB", fwd=0.5, rev=-0.6),  # AAA 와 동률 — 표에선 먼저지만 sym 순서로 뒤
        row("AAA", fwd=0.5, rev=-0.6),
        row("CCC", fwd=-0.3, rev=-0.5),
        # 한 코인의 두 행 — 거래소·방향 중 값이 큰 것(빗썸·Bybit 역프 2.5) 하나만
        row("DDD", fwd=2.0, rev=-2.1),
        row("DDD", dom="bithumb", fx="bybit", fwd=-2.6, rev=2.5),
        row("EEE", fwd=-1.0, rev=-1.2),
        row("FFF", fwd=0.0, rev=-0.1),
        row("GGG", fwd=-2.0, rev=-2.2),
    ]
    live = _live(rows)
    assert _routes(live) == [
        ("DDD", "bithumb", "bybit", "reverse", 2.5),
        ("AAA", "upbit", "binance", "kimp", 0.5),
        ("BBB", "upbit", "binance", "kimp", 0.5),
        ("FFF", "upbit", "binance", "kimp", 0.0),
        ("CCC", "upbit", "binance", "kimp", -0.3),
    ]


def test_no_movable_route_gives_empty_top_not_null() -> None:
    live = _live([row("AAA", fwd=3.0, rev=3.0, wd_fx=False, wd_dom=False)])
    assert live["top"] == []
    assert live["over1"] == 2 and live["over1Movable"] == 0


# ---- live: over1 경계 — 1.0 은 들어간다 ----


def test_over1_includes_exactly_one_percent_and_counts_movable_separately() -> None:
    rows = [
        row("AAA", fwd=1.0, rev=-1.0),  # 1.0 — 들어간다, 옮길 수 있다
        row("BBB", fwd=-1.0, rev=0.9999),  # 1.0 미만 — 안 들어간다
        row("CCC", fwd=1.0, rev=-1.0, wd_fx=False),  # 들어가지만 옮길 수 없다
    ]
    live = _live(rows)
    assert live["over1"] == 2
    assert live["over1Movable"] == 1


# ---- trail ----


def _trail_client(rows: list[dict], influx: FakeInflux) -> dict:
    return make_client(FakeBus(table(rows)), influx).get("/landing").json()["trail"]


def test_trail_reads_top_route_kimp_closes_from_candles_1m_last_hour_ascending() -> (
    None
):
    influx = FakeInflux()
    # Influx 가 순서 없이 줘도 ts 오름차순으로 낸다
    for ts, fwd_c in ((NOW - 60, 2.41), (NOW - 180, 2.30), (NOW - 120, 2.38)):
        influx.candles.append(
            candle(ts, base="VERONA", fwd_c=fwd_c, rev_c=-fwd_c - 0.1)
        )
    # 다른 경로의 봉은 섞이지 않는다
    influx.candles.append(candle(NOW - 60, dom="bithumb", base="VERONA", fwd_c=9.9))
    trail = _trail_client([row("VERONA", fwd=2.26, rev=-2.3)], influx)
    assert trail == {
        "sym": "VERONA",
        "dom": "upbit",
        "fx": "binance",
        "dir": "kimp",
        "points": [[NOW - 180, 2.30], [NOW - 120, 2.38], [NOW - 60, 2.41]],
    }
    assert influx.candle_calls == [
        {
            "bucket": "candles_1m",
            "start": NOW - 3600,
            "stop": NOW,
            "dom": "upbit",
            "fx": "binance",
            "base": "VERONA",
        }
    ]


def test_trail_uses_rev_close_for_reverse_route() -> None:
    influx = FakeInflux()
    for ts, rev_c in ((NOW - 120, 1.9), (NOW - 60, 2.1)):
        influx.candles.append(
            candle(ts, dom="bithumb", fx="bybit", base="HNT", fwd_c=-3.0, rev_c=rev_c)
        )
    trail = _trail_client(
        [row("HNT", dom="bithumb", fx="bybit", fwd=-2.0, rev=1.8)], influx
    )
    assert trail["dir"] == "reverse"
    assert trail["points"] == [[NOW - 120, 1.9], [NOW - 60, 2.1]]


def test_trail_is_null_below_two_points() -> None:
    influx = FakeInflux()
    influx.candles.append(candle(NOW - 60, base="VERONA", fwd_c=2.4))
    assert _trail_client([row("VERONA", fwd=2.26, rev=-2.3)], influx) is None


def test_trail_is_null_when_top_is_empty_without_reading_influx() -> None:
    influx = FakeInflux()
    rows = [row("AAA", fwd=3.0, rev=3.0, wd_fx=False, wd_dom=False)]
    assert _trail_client(rows, influx) is None
    assert influx.candle_calls == []


# ---- events ----


def test_events_counts_by_direction_and_open_and_top_one_per_coin_desc() -> None:
    influx = FakeInflux()
    influx.events += [
        event("CUDIS", NOW - 100_000, dom="bithumb", fx="bitget", max_percent=231.4),
        event("CUDIS", NOW - 200_000, max_percent=12.0, end_ts=NOW - 199_000),
        event("AAA", NOW - 5_000, dir="reverse", max_percent=3.0, end_ts=NOW - 4_000),
        event("BBB", NOW - 6_000, dir="reverse", max_percent=4.0, end_ts=NOW - 5_500),
        event("CCC", NOW - 7_000, max_percent=1.2, end_ts=NOW - 6_800),
        event("DDD", NOW - 8_000, max_percent=2.0, end_ts=NOW - 7_900),
        event("EEE", NOW - 9_000, max_percent=1.1),  # 진행 중
    ]
    events = make_client(FakeBus(None), influx).get("/landing").json()["events"]
    assert (events["start"], events["stop"]) == (NOW - 604_800, NOW)
    assert influx.event_calls == [{"start": NOW - 604_800, "stop": NOW}]
    assert events["count"] == 7
    assert (events["kimp"], events["reverse"], events["open"]) == (5, 2, 2)
    assert [(e["sym"], e["maxPercent"]) for e in events["top"]] == [
        ("CUDIS", 231.4),
        ("BBB", 4.0),
        ("AAA", 3.0),
        ("DDD", 2.0),
        ("CCC", 1.2),
    ]
    assert events["top"][0] == {
        "sym": "CUDIS",
        "dom": "bithumb",
        "fx": "bitget",
        "dir": "kimp",
        "maxPercent": 231.4,
        "startTs": NOW - 100_000,
        "endTs": 0,
        "durationSeconds": 0,
        "lastTs": NOW - 100_000 + 90,
    }
    assert events["top"][2]["durationSeconds"] == 1_000
    assert events["top"][2]["endTs"] == NOW - 4_000


def test_events_with_no_rows_is_zero_counts_not_null() -> None:
    events = make_client(FakeBus(None), FakeInflux()).get("/landing").json()["events"]
    assert events["count"] == 0 and events["open"] == 0
    assert events["top"] == []
