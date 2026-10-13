"""관리자 피드 v2 — `page` 인자·`elements`·`exits`·`depth`·(days, page) 60초 캐시 (스펙 061 §3.6·§4)."""

from collections.abc import Mapping

from app.features.attention.tests.conftest import Rig


def _fill(rig: Rig, day: str, fields: Mapping[str, int]) -> None:
    rig.redis.hset(f"attn:d:{day}", mapping=dict(fields))


async def _get(rig: Rig, **params: str) -> dict:
    resp = await rig.client.get("/admin/attention", params=params)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    # 응답의 그 밖 키·상태는 052 그대로
    assert list(body) == [
        "state",
        "code",
        "gateAt",
        "days",
        "from",
        "to",
        "fetchedAt",
        "rows",
    ]
    return body


FIELDS = {
    "landing|pc||pv": 4,
    "landing|pc|faq|ms": 900,
    "landing|pc|faq|x": 2,
    "landing|pc|hero|x": 2,
    "landing|pc|foot|x": 3,
    "landing|pc||d3": 1,
    "landing|pc||d10": 2,
    "landing|pc||d11": 9,  # 칸 밖 — 버림
    "landing|pc||dx": 9,  # 꼴이 다름 — 버림
    "landing|pc|faq|e|link:/kimp-chart|c": 3,
    "landing|pc|faq|e|link:/kimp-chart|h": 700,
    "landing|pc|faq|e|link:/kimp-chart|v": 100,
    "landing|pc|faq|e|faq:why|v": 5000,
    "landing|pc|hero|e|live:1|c": 3,
    "landing|pc|hero|e|live:1|v": 200,
    "landing|pc|hero|e|bad|z": 1,  # 지표 꼴이 다름 — 버림
    "landing|pc|hero|e||c": 1,  # 빈 열쇠 — 버림
    "landing|mobile||pv": 1,
    "app-spread|pc||pv": 2,
    "app-spread|pc|table|x": 2,
    "app-spread|pc|table|e|row:BTC|c": 4,
}


async def test_without_page_rows_carry_exits_and_depth_but_no_elements(
    rig: Rig,
) -> None:
    _fill(rig, "20261019", FIELDS)
    body = await _get(rig)
    rows = {(r["page"], r["device"]): r for r in body["rows"]}
    assert list(rows) == [
        ("landing", "mobile"),
        ("landing", "pc"),
        ("app-spread", "pc"),
    ]
    landing = rows[("landing", "pc")]
    # 떠난 곳은 n 큰 순, 같으면 영역 id 순
    assert landing["exits"] == [["foot", 3], ["faq", 2], ["hero", 2]]
    assert landing["depth"] == [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 2]
    assert rows[("landing", "mobile")]["depth"] == [0] * 11
    assert rows[("landing", "mobile")]["exits"] == []
    # 대시보드 화면의 깊이는 null
    assert rows[("app-spread", "pc")]["depth"] is None
    assert rows[("app-spread", "pc")]["exits"] == [["table", 2]]
    assert all("elements" not in r for r in body["rows"])
    assert landing["areas"] == [{"id": "faq", "ms": 900, "clicks": 0, "seen": 0}]


async def test_page_keeps_that_screen_and_ranks_elements_by_clicks_then_visible_time(
    rig: Rig,
) -> None:
    _fill(
        rig, "20261018", {"landing|pc|faq|e|faq:why|v": 1000}
    )  # 창 안의 다른 날도 더한다
    _fill(rig, "20261019", FIELDS)
    body = await _get(rig, page="landing", days="7")
    assert [(r["page"], r["device"]) for r in body["rows"]] == [
        ("landing", "mobile"),
        ("landing", "pc"),
    ]
    assert body["rows"][0]["elements"] == []
    # 클릭이 같으면 보인 시간 큰 순 — 마우스 올림은 순서에 쓰지 않는다
    assert body["rows"][1]["elements"] == [
        {"area": "hero", "key": "live:1", "clicks": 3, "hoverMs": 0, "visMs": 200},
        {
            "area": "faq",
            "key": "link:/kimp-chart",
            "clicks": 3,
            "hoverMs": 700,
            "visMs": 100,
        },
        {"area": "faq", "key": "faq:why", "clicks": 0, "hoverMs": 0, "visMs": 6000},
    ]
    body = await _get(rig, page="app-spread")
    (row,) = body["rows"]
    assert row["elements"] == [
        {"area": "table", "key": "row:BTC", "clicks": 4, "hoverMs": 0, "visMs": 0}
    ]
    assert row["depth"] is None


async def test_elements_are_cut_at_300_with_ties_in_area_then_key_order(
    rig: Rig,
) -> None:
    fields = {"privacy|pc||pv": 1}
    for i in range(320):
        fields[f"privacy|pc|s{1 + i % 2}|e|k{i:03d}|v"] = 1000 + (i // 10)
    _fill(rig, "20261019", fields)
    (row,) = (await _get(rig, page="privacy"))["rows"]
    elements = row["elements"]
    assert len(elements) == 300
    assert elements[0]["visMs"] == 1031 and elements[-1]["visMs"] == 1002
    # 같은 값이면 (영역, 열쇠) 순 — s1 의 짝수 열쇠가 먼저
    assert [e["key"] for e in elements[:10]] == [
        "k310", "k312", "k314", "k316", "k318", "k311", "k313", "k315", "k317", "k319",
    ]  # fmt: skip


async def test_page_outside_the_list_is_the_same_as_no_page(rig: Rig) -> None:
    _fill(rig, "20261019", FIELDS)
    plain = await _get(rig)
    for page in ("admin", "", "LANDING", "privacy-20261011"):
        assert await _get(rig, page=page) == plain, page


async def test_cache_is_per_days_and_page_for_60_seconds(rig: Rig) -> None:
    _fill(rig, "20261019", {"landing|pc||pv": 1, "kimp-chart|pc||pv": 1})
    first = await _get(rig, page="landing")
    plain = await _get(rig)
    _fill(rig, "20261019", {"landing|pc||pv": 5, "kimp-chart|pc||pv": 5})
    rig.clock.mono += 59.9
    assert await _get(rig, page="landing") == first
    assert await _get(rig) == plain
    # 처음 보는 (days, page) 는 새로 읽는다
    assert (await _get(rig, page="kimp-chart"))["rows"][0]["pv"] == 5
    assert (await _get(rig, page="landing", days="1"))["rows"][0]["pv"] == 5
    # 목록 밖 page 는 page 없음의 칸을 쓴다
    assert await _get(rig, page="nope") == plain
    rig.clock.mono += 0.1
    assert (await _get(rig, page="landing"))["rows"][0]["pv"] == 5
