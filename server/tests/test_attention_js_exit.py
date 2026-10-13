"""attention.js 를 node 로 — 깊이·떠난 곳·몸통 v2 의 크기 (스펙 061 §3.2·§3.3·§4).

가짜 문서와 단계 문자열은 test_attention_js.py 의 것을 쓴다. 문서 높이는 `docH`, 화면 높이는 `h`.
"""

from tests.test_attention_js import ON, _bodies, _one


def test_depth_is_the_lowest_tenth_reached_and_goes_once_at_pagehide() -> None:
    # 화면 800 · 문서 4,000 — 처음 800/4000 = 2칸, 1,000 내리면 1,800 = 4칸, 2,200 이면 3,000 = 7칸
    got = _one(
        ls=ON,
        docH=4000,
        areas=[["hero", 0, 4000]],
        steps=["scroll:1000", "scroll:2200", "scroll:500", "hide", "pagehide"],
    )
    hide, leave = _bodies(got)
    assert hide["d"] is None  # 숨김은 깊이를 싣지 않는다
    assert leave["d"] == 7  # 올라와도 닿은 가장 아래
    # 끝까지(1px 안) = 10, 다시 떠나도 깊이·떠난 곳은 구간에 한 번만
    for y in ("3200", "3199.5"):
        got = _one(
            ls=ON,
            docH=4000,
            areas=[["hero", 0, 4000]],
            steps=[f"scroll:{y}", "pagehide", "show", "move", "t:2000", "pagehide"],
        )
        first, second = _bodies(got)
        assert (first["d"], first["x"]) == (10, "hero"), y
        assert (second["d"], second["x"]) == (None, None), y
    # 문서가 화면보다 짧으면 처음부터 끝 — 10
    got = _one(ls=ON, docH=500, areas=[["hero", 0, 500]], steps=["pagehide"])
    assert _bodies(got)[0]["d"] == 10


def test_depth_is_only_for_the_four_static_pages() -> None:
    cases = [
        ("/", 2),
        ("/privacy", 2),
        ("/kimp-chart", 2),
        ("/kimp-history", 2),
        ("/app/", None),
    ]
    for path, depth in cases:
        got = _one(
            ls=ON, path=path, docH=4000, areas=[["a", 0, 300]], steps=["pagehide"]
        )
        assert _bodies(got)[0]["d"] == depth, path


def test_exit_area_is_the_most_visible_area_at_pagehide_only() -> None:
    areas = [["hero", 0, 300], ["events", 300, 900], ["faq", 1200, 2000]]
    got = _one(
        ls=ON,
        areas=areas,
        steps=["t:1000", "scroll:1000", "hide", "show", "move", "pagehide"],
    )
    hide, leave = _bodies(got)
    assert hide["x"] is None
    # 1,000 내리면 events 200px · faq 600px 보임 → faq
    assert leave["x"] == "faq"
    # 보이는 높이가 같으면 문서에서 앞
    got = _one(ls=ON, areas=[["one", 0, 400], ["two", 400, 400]], steps=["pagehide"])
    assert _bodies(got)[0]["x"] == "one"
    # 보이는 영역이 없으면 null
    got = _one(ls=ON, areas=[["far", 5000, 100]], steps=["pagehide"])
    assert _bodies(got)[0]["x"] is None


def test_dashboard_tab_change_and_hide_send_no_exit_but_pagehide_does() -> None:
    got = _one(
        ls=ON,
        path="/app/",
        areas=[["header", 0, 52], ["table", 100, 600]],
        steps=["t:1000", "tab:gap", "settle", "t:500", "hide", "pagehide"],
    )
    bodies = _bodies(got)
    assert [(b["page"], b["d"], b["x"]) for b in bodies] == [
        ("app-spread", None, None),
        ("app-gap", None, None),
        ("app-gap", None, "table"),
    ]


def _many(n: int, key: str) -> list[dict]:
    """한 영역 안에 n 개 — 모두 화면 안(높이 9,000)이고 열쇠는 key 꼴."""
    return [
        {
            "ref": f"e{i}",
            "in": "a" * 32,
            "attrs": {"data-el": key.format(i=i)},
            "top": i * 40,
            "height": 30,
        }
        for i in range(n)
    ]


def test_at_most_150_elements_ranked_by_clicks_then_visible_time() -> None:
    area = "a" * 32
    els = _many(160, "k{i:03d}")
    steps = ["t:1000", "click:e159:3", "click:e158:2", "click:e157", "hide"]
    got = _one(ls=ON, h=9000, areas=[[area, 0, 9000]], els=els, steps=steps)
    (body,) = _bodies(got)
    e = body["e"]
    assert len(e) == 150
    assert [row[1] for row in e[:3]] == ["k159", "k158", "k157"]
    assert e[3] == [area, "k000", 0, 0, 1000]
    # 클릭이 같으면 보인 시간 큰 순 — 같은 시간이면 찾은 차례대로 앞의 것이 남는다
    assert {row[1] for row in e[3:]} == {f"k{i:03d}" for i in range(147)}


def test_body_stays_within_16384_bytes_by_dropping_e_from_the_end() -> None:
    area = "a" * 32
    # 64자 열쇠 150개 — 줄이지 않으면 17KB 남짓
    els = _many(150, "k{i:03d}" + "x" * 60)
    clicks = [f"click:e{i}:{1 + i % 50}" for i in range(150)]
    got = _one(ls=ON, h=9000, areas=[[area, 0, 9000]], els=els, steps=[*clicks, "hide"])
    (sent,) = got["sent"]
    e = sent["body"]["e"]
    assert sent["bytes"] <= 16384
    assert 100 < len(e) < 150
    # 남는 것은 앞(많이 누른 것)부터 — 빠진 것은 남은 것보다 덜 눌렸다
    assert [row[2] for row in e] == sorted((row[2] for row in e), reverse=True)
    clicks = {"k" + f"{i:03d}" + "x" * 60: 1 + i % 50 for i in range(150)}
    dropped = set(clicks) - {row[1] for row in e}
    assert dropped and max(clicks[k] for k in dropped) <= min(row[2] for row in e)
    # 여러 바이트 글자도 바이트로 잰다 — 한글 24자 자동 열쇠
    els = [
        {
            "ref": f"e{i}",
            "in": area,
            # 숫자는 # 로 가려지므로 영문 두 자로 가른다
            "text": chr(65 + i // 26)
            + chr(65 + i % 26)
            + "가나다라마바사아자차카타파하가나다라마바사아자",
            "top": i * 40,
            "height": 30,
        }
        for i in range(150)
    ]
    got = _one(
        ls=ON,
        h=9000,
        areas=[[area, 0, 9000]],
        els=els,
        steps=[*(f"click:e{i}" for i in range(150)), "hide"],
    )
    (sent,) = got["sent"]
    assert sent["bytes"] <= 16384 and 100 < len(sent["body"]["e"]) < 150
    assert all(len(row[1]) == len("btn:") + 24 for row in sent["body"]["e"])
