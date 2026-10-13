"""attention.js 를 node 로 — 요소의 열쇠·클릭·보인 시간·마우스 올림 (스펙 061 §3.1·§3.2·§4).

가짜 문서와 단계 문자열은 test_attention_js.py 의 것을 쓴다. 요소는 `els` 의 dict 하나씩(영역이나 다른 요소 안).
몸통의 `e` 는 [영역, 열쇠, 클릭, 마우스 올림 ms, 보인 ms] 목록이라 비교는 (영역, 열쇠) → 세 값 dict 로 한다.
"""

from tests.test_attention_js import ON, _bodies, _one

APP = {"ls": ON, "path": "/app/"}


def _e(got: dict) -> dict[tuple[str, str], tuple[int, int, int]]:
    """보낸 몸통 전부의 e 를 (영역, 열쇠) 로 더한다."""
    out: dict[tuple[str, str], tuple[int, int, int]] = {}
    for body in _bodies(got):
        assert body["v"] == 2
        for area, key, c, h, v in body["e"]:
            old = out.get((area, key), (0, 0, 0))
            out[(area, key)] = (old[0] + c, old[1] + h, old[2] + v)
    return out


def _el(ref: str, where: str, tag: str = "button", **more: object) -> dict:
    return {"ref": ref, "in": where, "tag": tag, **more}


def test_keys_from_data_el_aria_link_paths_inputs_and_visible_text() -> None:
    els = [
        _el("logo", "top", "a", attrs={"href": "/"}, text="KimpTrack"),
        _el("nav", "top", "a", attrs={"href": "#faq", "data-el": "nav:faq"}),
        _el("app", "top", "a", attrs={"href": "/app/?tab=history&sym=BTC#x"}),
        _el("ext", "top", "a", attrs={"href": "https://www.spo.go.kr/site/main?x=1"}),
        _el("mail", "top", "a", attrs={"href": "mailto:someone@example.com"}),
        _el("aria", "top", "a", attrs={"href": "/", "aria-label": "KimpTrack 첫 화면"}),
        _el("save", "body", text="  선택한   대로\n저장  "),
        _el("long", "body", text="가나다라마바사아자차카타파하" * 2),
        _el("price", "body", text="BTC ₩98,765,432 · 1.25% 3."),
        # 입력칸의 값은 읽지 않는다 — 이름은 name → placeholder → type
        _el("q", "body", "input", attrs={"placeholder": "심볼 1개", "value": "SECRET"}),
        _el("named", "body", "input", attrs={"name": "q", "placeholder": "x"}),
        _el("num", "body", "input", attrs={"type": "number"}, text="TYPED"),
        _el("bare", "body", "input"),
        _el("pick", "body", "select", attrs={"name": "dom"}),
        _el("faq", "body", "summary", text="김프가 3% 넘으면?"),
        _el("tab", "body", "div", attrs={"role": "tab"}, text="탭 하나"),
        _el(
            "rolebtn", "body", "a", attrs={"href": "/x", "role": "button"}, text="버튼"
        ),
        # 가장 가까운 조상(영역 안)의 data-el 이 먼저 — 안쪽 링크를 눌러도 카드의 열쇠
        _el("card", "body", "div", attrs={"data-el": "live:1"}),
        _el("cardlink", "card", "a", attrs={"href": "/app/"}, text="BTC 1.20%"),
        _el("plain", "body", "span", text="그냥 글자"),
        _el("bad", "body", attrs={"data-el": "a|b"}, text="열쇠 꼴이 틀림"),
    ]
    refs = [e["ref"] for e in els]
    got = _one(
        ls=ON,
        areas=[["top", 0, 100], ["body", 100, 600]],
        els=els,
        steps=[*(f"click:{r}" for r in refs), "hide"],
    )
    sent = _e(got)
    assert sent == {
        ("top", "link:/"): (1, 0, 0),
        ("top", "nav:faq"): (1, 0, 0),
        ("top", "link:/app/"): (1, 0, 0),
        ("top", "link:www.spo.go.kr/site/main"): (1, 0, 0),
        ("top", "link:mailto"): (1, 0, 0),
        ("top", "link:KimpTrack 첫 화면"): (1, 0, 0),
        ("body", "btn:선택한 대로 저장"): (1, 0, 0),
        ("body", "btn:" + ("가나다라마바사아자차카타파하" * 2)[:24]): (1, 0, 0),
        ("body", "btn:BTC ₩# · #% #"): (1, 0, 0),
        ("body", "input:심볼 #개"): (1, 0, 0),
        ("body", "input:q"): (1, 0, 0),
        ("body", "input:number"): (1, 0, 0),
        ("body", "input:text"): (1, 0, 0),
        ("body", "select:dom"): (1, 0, 0),
        ("body", "faq:김프가 #% 넘으면?"): (1, 0, 0),
        ("body", "tab:탭 하나"): (1, 0, 0),
        ("body", "btn:버튼"): (1, 0, 0),
        ("body", "live:1"): (2, 0, 0),
    }
    text = repr(_bodies(got))
    for secret in ("SECRET", "TYPED", "98,765", "someone", "그냥 글자"):
        assert secret not in text, secret
    # 영역 클릭은 052 그대로 — 열쇠가 없거나 틀린 요소도 영역에는 1
    assert _bodies(got)[0]["a"] == {"top": [0, 6, 0], "body": [0, 15, 0]}


def test_rows_count_clicks_only_and_tracked_elements_count_time_and_hover() -> None:
    els = [
        _el("row", "table", "div", attrs={"data-el": "row:BTC"}, top=40, height=40),
        _el("sym", "row", "span", text="BTC", top=40, height=40),
        # 기록 탭처럼 단추인 행 — 대화형이라 찾기에 걸려도 추적하지 않는다
        _el("brow", "table", attrs={"data-el": "row:ETH"}, top=80, height=30),
        _el("sort", "table", attrs={"data-el": "sort:val"}, top=0, height=30),
    ]
    got = _one(
        **APP,
        areas=[["table", 0, 600]],
        els=els,
        steps=[
            *("over:sym", "t:2000", "click:sym", "over:brow", "t:2000"),
            *("click:brow", "over:sort", "t:1000", "hide"),
        ],
    )
    assert _e(got) == {
        ("table", "row:BTC"): (1, 0, 0),  # 클릭만 — 보인 시간·마우스 올림 없음
        ("table", "row:ETH"): (1, 0, 0),
        ("table", "sort:val"): (0, 1000, 5000),
    }
    assert got["observed"] == 1 + 1  # 영역 + 정렬 머리


def test_element_visibility_follows_the_area_rule_and_one_key_counts_once() -> None:
    els = [
        _el("full", "a", attrs={"data-el": "full"}, top=770, height=30),  # 30px 다 보임
        _el("cut", "a", attrs={"data-el": "cut"}, top=785, height=30),  # 15px → 안 보임
        _el("tall", "a", "div", attrs={"data-el": "tall"}, top=400, height=3000),
        _el("twin1", "a", "a", attrs={"href": "/kimp-chart"}, top=10, height=20),
        _el("twin2", "a", "a", attrs={"href": "/kimp-chart"}, top=900, height=20),
    ]
    got = _one(ls=ON, areas=[["a", 0, 5000]], els=els, steps=["t:1500", "hide"])
    assert _e(got) == {
        ("a", "full"): (0, 0, 1500),
        ("a", "tall"): (0, 0, 1500),  # 400px = 화면 절반
        ("a", "link:/kimp-chart"): (0, 0, 1500),  # 둘 중 하나만 보여도 보임, 한 번만
    }
    # 숨김·5분 무입력·스크롤로 화면 밖 — 영역과 같은 뜻
    got = _one(
        ls=ON,
        areas=[["a", 0, 5000]],
        els=els[:1],
        steps=[
            "t:1000",
            "hide",
            "t:9000",
            "show",
            "move",
            "t:300000",
            "t:50000",
            "move",
            "scroll:2000",
            "t:4000",
            "hide",
        ],
    )
    assert [b["e"] for b in _bodies(got)] == [
        [["a", "full", 0, 0, 1000]],
        [["a", "full", 0, 0, 300000]],
    ]


def test_hover_needs_a_mouse_on_pc_drops_short_stays_and_cuts_at_30_seconds() -> None:
    els = [
        _el("b1", "a", attrs={"data-el": "b1"}, top=10, height=30),
        _el("b2", "a", attrs={"data-el": "b2"}, top=50, height=30),
        _el("in2", "b2", "span", text="안", top=50, height=30),
    ]
    steps = [
        "over:b1",
        "t:299",
        "over:b2",  # b1 299ms — 버림
        "t:40000",
        "over:in2",  # 같은 요소 안 — 이어서 머문다
        "t:1000",
        "leave:in2",  # 창 밖 — b2 41,000ms → 30,000 에서 자름
        "over:b1",
        "t:500",
        "hide",  # 숨으면 끝 — b1 500
        "show",
        "t:5000",
        "hide",  # 다시 올리기 전에는 쌓지 않는다
    ]
    got = _one(ls=ON, areas=[["a", 0, 600]], els=els, steps=steps)
    hover = {key: h for (_, key), (_, h, _) in _e(got).items()}
    assert hover == {"b1": 500, "b2": 30000}
    # 창 밖으로 나간 때 끝난다 — 나가 있던 동안은 세지 않는다
    steps = ["over:b2", "t:1000", "leave:b2", "t:2000", "over:b1", "t:400", "hide"]
    got = _one(ls=ON, areas=[["a", 0, 600]], els=els, steps=steps)
    assert {key: h for (_, key), (_, h, _) in _e(got).items()} == {
        "b1": 400,
        "b2": 1000,
    }
    # 휴대폰 폭과 마우스가 아닌 포인터는 세지 않는다
    for case in ({"w": 767}, {}):
        kind = "touch" if not case else "mouse"
        got = _one(
            ls=ON,
            areas=[["a", 0, 600]],
            els=els,
            steps=[f"over:b1:{kind}", "t:5000", "hide"],
            **case,
        )
        assert all(h == 0 for (_, h, _) in _e(got).values()), case


def test_label_click_counts_once_and_script_clicks_only_reach_the_area() -> None:
    els = [
        _el("lab", "f", "label", attrs={"data-el": "only-io"}, control="cb"),
        _el("cb", "lab", "input", attrs={"type": "checkbox"}),
        _el("txt", "lab", "span", text="입출금 열림"),
        _el(
            "plab", "f", "label", control="pcb"
        ),  # data-el 없는 라벨 — 입력칸의 자동 열쇠
        _el("pcb", "plab", "input", attrs={"type": "checkbox"}),
        _el("link", "f", "a", attrs={"href": "/app/"}),
    ]
    steps = [
        "labelclick:lab",  # 라벨 + 이어지는 입력칸 누름 = 1
        "click:cb",  # 입력칸을 바로 = 1
        "click:txt",  # 라벨 안 글자 — 입력칸이 이어 받는다고 보고 여기선 0
        "labelclick:plab",
        "scriptclick:link",  # 페이지 스크립트의 .click() — 요소 0
        "hide",
    ]
    got = _one(ls=ON, areas=[["f", 0, 600]], els=els, steps=steps)
    clicks = {key: c for (_, key), (c, _, _) in _e(got).items() if c}
    assert clicks == {"only-io": 2, "check:checkbox": 1}
    # 영역은 052 그대로 누름마다 1(라벨 둘 = 4, 입력칸 1, 글자 1, 스크립트 1)
    assert _bodies(got)[0]["a"]["f"][1] == 7


def test_element_click_cap_holds_over_the_segment() -> None:
    els = [_el("b", "a", attrs={"data-el": "b"}, top=900, height=30)]
    got = _one(
        ls=ON,
        areas=[["a", 0, 2000]],
        els=els,
        steps=["click:b:40", "hide", "show", "click:b:40", "hide"],
    )
    assert [b["e"] for b in _bodies(got)] == [
        [["a", "b", 40, 0, 0]],
        [["a", "b", 10, 0, 0]],
    ]


def test_tracking_stops_at_200_keys_but_clicks_still_count() -> None:
    els = [
        _el(f"k{i}", "a", attrs={"data-el": f"k{i:03d}"}, top=i * 40, height=30)
        for i in range(210)
    ]
    got = _one(ls=ON, h=9000, areas=[["a", 0, 9000]], els=els, steps=[])
    assert got["observed"] == 1 + 200  # 영역 하나 + 요소 200
    got = _one(
        ls=ON,
        h=9000,
        areas=[["a", 0, 9000]],
        els=els,
        steps=["t:1000", "click:k205", "hide"],
    )
    sent = _e(got)
    assert sent[("a", "k205")] == (1, 0, 0)  # 추적 밖 — 클릭만
    assert sent[("a", "k000")] == (0, 0, 1000) and ("a", "k200") not in sent


def test_late_elements_are_found_and_a_tab_change_starts_a_fresh_set() -> None:
    els = [
        _el("s", "filters", attrs={"data-el": "only-over"}, top=60, height=30),
        _el("late", "filters", attrs={"data-el": "late"}, top=60, height=30),
    ]
    els[1]["later"] = True
    got = _one(
        **APP,
        search="?tab=spread",
        areas=[["filters", 50, 60]],
        els=els,
        steps=[
            "t:1000",
            "attach:late",
            "t:1000",
            "timers",
            "settle",
            "t:500",
            "tab:gap",  # 구간 끝 — 보내고 새 구간에서 다시 찾는다
            "settle",
            "t:300",
            "hide",
        ],
    )
    first, second = _bodies(got)
    assert (first["page"], second["page"]) == ("app-spread", "app-gap")
    assert sorted(first["e"]) == [
        ["filters", "late", 0, 0, 500],
        ["filters", "only-over", 0, 0, 2500],
    ]
    assert sorted(second["e"]) == [
        ["filters", "late", 0, 0, 300],
        ["filters", "only-over", 0, 0, 300],
    ]
    assert (second["d"], second["x"]) == (None, None)
