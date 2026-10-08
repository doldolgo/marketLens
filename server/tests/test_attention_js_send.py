"""attention.js 를 node 로 — 구간·보내기·몸통·화면 이름·기기·늦게 생기는 영역 (스펙 052 §3.1~§3.3·§4).

가짜 환경과 단계 문자열은 test_attention_js.py 의 것을 쓴다.
"""

from tests.test_attention_js import HERO, ON, _bodies, _one, _run


def test_tab_change_ends_the_segment_and_starts_one_with_pv_1() -> None:
    areas = [["header", 0, 52], ["table", 100, 600]]
    got = _one(
        ls=ON,
        path="/app/",
        search="?tab=history",
        areas=areas,
        steps=[
            "t:1000",
            "tab:history",  # 같은 id — 무시(보내지 않고 이어 센다)
            "tab:admin",  # 목록 밖 — 무시
            "t:1000",
            "click:table:2",
            "tab:gap",  # 구간 끝 — 보냄
            "settle",
            "t:500",
            "hide",
            "show",
            "t:300",
            "hide",
        ],
    )
    assert _bodies(got) == [
        {
            "v": 1,
            "page": "app-history",
            "device": "pc",
            "pv": 1,
            "a": {"header": [2000, 0, 1], "table": [2000, 2, 1]},
        },
        {
            "v": 1,
            "page": "app-gap",
            "device": "pc",
            "pv": 1,
            "a": {"header": [500, 0, 0], "table": [500, 0, 0]},
        },
        {
            "v": 1,
            "page": "app-gap",
            "device": "pc",
            "pv": 0,
            "a": {"header": [300, 0, 0], "table": [300, 0, 0]},
        },
    ]


def test_withdrawal_right_before_sending_drops_and_stops() -> None:
    got = _one(
        ls=ON,
        areas=HERO,
        steps=[
            "t:1000",
            "revoke",
            "hide",  # 보내기 직전 판정 — 버리고 멈춘다
            "grant",
            "clarity",
            "show",
            "move",
            "t:1000",
            "hide",
            "pagehide",
        ],
    )
    assert got["sent"] == []


def test_empty_sends_are_skipped_but_the_first_pv_goes_out() -> None:
    got = _one(ls=ON, areas=[], steps=["hide", "show", "hide", "pagehide"])
    assert _bodies(got) == [
        {"v": 1, "page": "landing", "device": "pc", "pv": 1, "a": {}}
    ]
    # 영역 밖 클릭은 버린다
    got = _one(ls=ON, areas=[], steps=["hide", "show", "click:none:3", "hide"])
    assert len(got["sent"]) == 1


def test_beacon_falls_back_to_one_fetch() -> None:
    for case in ({"beacon": False}, {"noBeacon": True}):
        got = _one(ls=ON, areas=HERO, **case, steps=["t:1000", "hide"])
        vias = [s["via"] for s in got["sent"]]
        assert vias == (
            ["beacon", "fetch"] if case.get("beacon") is False else ["fetch"]
        ), case
        fetched = got["sent"][-1]
        assert fetched["url"] == "/api/attention"
        assert fetched["opts"] == {
            "method": "POST",
            "keepalive": True,
            "credentials": "omit",
        }
        assert fetched["body"]["a"] == {"hero": [1000, 0, 1]}
    got = _one(ls=ON, areas=HERO, steps=["t:1000", "hide"])
    assert [(s["via"], s["url"]) for s in got["sent"]] == [("beacon", "/api/attention")]


def test_worst_body_stays_within_4096_bytes_and_40_areas() -> None:
    # 32자 id 41개가 모두 보이고 상한까지 — 41번째는 세지 않는다
    ids = [f"a{i:02d}" + "x" * 29 for i in range(41)]
    areas = [[area, i * 100, 100] for i, area in enumerate(ids)]
    clicks = [f"click:{area}:60" for area in ids]
    steps = [step for _ in range(3) for step in ("t:250000", "move")]
    got = _one(ls=ON, h=5000, areas=areas, steps=[*steps, *clicks, "hide"])
    (sent,) = got["sent"]
    assert sent["bytes"] <= 4096
    assert len(sent["body"]["a"]) == 40 and ids[40] not in sent["body"]["a"]
    assert set(map(tuple, sent["body"]["a"].values())) == {(600000, 50, 1)}


def test_page_name_from_the_path_and_the_tab_query() -> None:
    cases = [
        ("/", "", "landing"),
        ("/privacy", "", "privacy"),
        ("/kimp-chart", "?utm_source=x", "kimp-chart"),
        ("/kimp-history", "", "kimp-history"),
        ("/app/", "", "app-spread"),
        ("/app/xyz", "?tab=bad", "app-spread"),
        ("/app/", "?tab=flow&sym=BTC", "app-flow"),
        ("/app/index.html", "?tab=health", "app-health"),
    ]
    results = _run(
        [{"ls": ON, "path": p, "search": s, "steps": ["hide"]} for p, s, _ in cases]
    )
    assert [_bodies(r)[0]["page"] for r in results] == [page for _, _, page in cases]


def test_device_is_fixed_at_segment_start_by_width() -> None:
    assert _bodies(_one(ls=ON, w=767, steps=["hide"]))[0]["device"] == "mobile"
    assert _bodies(_one(ls=ON, w=768, steps=["hide"]))[0]["device"] == "pc"


def test_late_areas_are_found_by_the_mutation_observer_at_most_once_a_second() -> None:
    got = _one(
        ls=ON,
        path="/app/",
        areas=[],
        steps=[
            "t:2000",
            "add:table:0:500",
            "add:filters:500:100",  # 두 번째 바뀜은 같은 예약을 탄다
            "settle",
            "t:1000",  # 찾기 전 — 아직 안 센다
            "timers",
            "settle",
            "t:1500",
            "hide",
        ],
    )
    assert (got["timers"], got["setTimeouts"]) == (0, 1)
    assert _bodies(got)[0]["a"] == {"table": [1500, 0, 1], "filters": [1500, 0, 1]}
    # 찾은 지 1초가 안 됐으면 다음 찾기는 그 1초가 찰 때
    got = _one(
        ls=ON,
        areas=[],
        steps=[
            "t:200",
            "add:hero:0:300",  # 기동 때 찾은 지 0.2초 — 0.8초 뒤로 예약
            "t:700",
            "timers",
            "settle",
            "t:1000",
            "hide",  # 아직 못 찾았다 — 영역 없이 pv 만
            "show",
            "t:100",
            "timers",
            "settle",
            "t:400",
            "hide",
        ],
    )
    assert [b["a"] for b in _bodies(got)] == [{}, {"hero": [400, 0, 0]}]
