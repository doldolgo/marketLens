"""`/admin/clarity` 의 `pages` — 페이지 종류×기기 묶음·세션 가중 평균·주소를 남기지 않음 (스펙 040 §3.4·§4 '묶음').

가짜 Data Export 의 묶음 응답(차원 `URL`·`Device` 행)을 바꿔 가며 피드를 부르고 `pages` 를 본다.
"""

from typing import Any

import pytest

from app.features.admin.tests.clarity_fakes import (
    PAGES_EXPORT,
    Clarity,
    World,
    dims,
)

AVERAGE_KEYS = (
    "scrollDepth",
    "totalSec",
    "activeSec",
    "deadClickPct",
    "rageClickPct",
    "excessiveScrollPct",
    "quickbackPct",
    "scriptErrorPct",
    "errorClickPct",
)


def cell(page: str, device: str, **values: Any) -> dict[str, Any]:
    return {
        "page": page,
        "device": device,
        "sessions": None,
        **dict.fromkeys(AVERAGE_KEYS),
        **values,
    }


async def pages_of(export: Any) -> dict[str, Any]:
    return (await World(Clarity(pages=((200, export),))).get())["pages"]


def traffic(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"metricName": "Traffic", "information": list(rows)}]


@pytest.mark.parametrize(
    ("url", "page"),
    [
        ("https://kimptrack.com/", "landing"),
        ("https://kimptrack.com", "landing"),
        ("https://WWW.KimpTrack.com./?utm_source=x", "landing"),
        ("kimptrack.com/?x=1", "landing"),
        ("/", "landing"),
        ("https://kimptrack.com/app/", "app:spread"),
        ("https://kimptrack.com/app/?tab=spread", "app:spread"),
        ("https://kimptrack.com/app/?sym=BTC&tab=history", "app:history"),
        ("/app/?tab=gap", "app:gap"),
        ("www.kimptrack.com/app/?tab=pp", "app:pp"),
        ("https://kimptrack.com/app/?tab=health#top", "app:health"),
        ("https://kimptrack.com/app/?tab=flow", "app:flow"),
        ("https://kimptrack.com/app/?tab=", "app:other"),
        ("https://kimptrack.com/app/?tab=Gap", "app:other"),
        ("https://kimptrack.com/app/?tab=zzz&tab=gap", "app:other"),
        (
            "https://kimptrack.com/app/?TAB=gap",
            "app:spread",
        ),  # 키는 글자 그대로 — tab 이 없다
        ("https://kimptrack.com/privacy", "privacy"),
        ("/privacy?x=1#consent", "privacy"),
        ("/privacy-20261001.html", "other"),
        ("https://kimptrack.com/app/index.html", "other"),
        ("https://kimptrack.com/app", "other"),
        ("https://evil.example/app/?tab=gap", "other"),
        ("https://kimptrack.com.evil.example/", "other"),
        ("Direct", "other"),
        ("", "other"),
    ],
)
async def test_page_kinds(url: str, page: str) -> None:
    got = await pages_of(traffic(dims(url, "PC", totalSessionCount=1)))
    assert got["groups"] == [cell(page, "desktop", sessions=1)]


@pytest.mark.parametrize(
    ("device", "kind"),
    [
        ("Mobile", "mobile"),
        ("MOBILE", "mobile"),
        ("Tablet", "tablet"),
        ("PC", "desktop"),
        ("Desktop", "desktop"),
        ("Email", "other"),
        ("Other", "other"),
        ("", "other"),
    ],
)
async def test_device_kinds(device: str, kind: str) -> None:
    got = await pages_of(traffic(dims("/", device, totalSessionCount="2")))
    assert got["groups"] == [cell("landing", kind, sessions=2)]


async def test_two_addresses_in_one_cell_are_weighted_by_their_sessions() -> None:
    got = await pages_of(PAGES_EXPORT)
    assert got["state"] == "ok"
    assert got["groups"] == [
        cell("landing", "mobile", sessions=4, scrollDepth=50.0),  # (40×3 + 80×1) / 4
        cell("app:gap", "desktop", sessions=2, scrollDepth=12.5),
    ]
    assert (got["numOfDays"], got["rowsIn"], got["rowLimitHit"]) == (3, 7, False)


def metric(name: str, *rows: dict[str, Any]) -> dict[str, Any]:
    return {"metricName": name, "information": list(rows)}


async def test_every_average_cell_reads_its_metric() -> None:
    def signal(pct: Any) -> dict[str, Any]:
        return dims("/privacy", "Tablet", sessionsWithMetricPercentage=pct)

    export = [
        metric(
            "Engagement Time", dims("/privacy", "Tablet", totalTime="90", activeTime=30)
        ),
        metric("DeadClickCount", signal(10)),
        metric("RageClickCount", signal("20")),
        metric("Excessive Scroll", signal(30.333)),
        metric("QuickbackClick", signal(40)),
        metric("ScriptErrorCount", signal(50)),
        metric("ErrorClickCount", signal(60)),
        metric("Browser", dims("/privacy", "Tablet", name="Chrome")),  # 행 수만 센다
    ]
    got = await pages_of(export)
    assert got["groups"] == [
        cell(
            "privacy",
            "tablet",
            totalSec=90.0,
            activeSec=30.0,
            deadClickPct=10.0,
            rageClickPct=20.0,
            excessiveScrollPct=30.33,
            quickbackPct=40.0,
            scriptErrorPct=50.0,
            errorClickPct=60.0,
        )
    ]
    assert got["rowsIn"] == 8


async def test_rows_without_traffic_weigh_by_their_own_sessions_then_alike() -> None:
    export = [
        metric(
            "ScrollDepth",
            dims("/app/?tab=pp&a=1", "PC", averageScrollDepth=10, sessionsCount="3"),
            dims("/app/?tab=pp&a=2", "PC", averageScrollDepth=50, sessionsCount=1),
            dims("/app/?tab=flow&a=1", "PC", averageScrollDepth=10),
            dims("/app/?tab=flow&a=2", "PC", averageScrollDepth="50"),
            dims(
                "/app/?tab=flow&a=3", "PC", averageScrollDepth="n/a"
            ),  # 값 없음 — 뺀다
        ),
    ]
    got = await pages_of(export)
    assert got["groups"] == [
        cell("app:pp", "desktop", scrollDepth=20.0),  # (10×3 + 50×1) / 4
        cell("app:flow", "desktop", scrollDepth=30.0),  # 단순 평균
    ]


async def test_zero_weight_is_null_and_the_cell_keeps_its_zero_sessions() -> None:
    export = traffic(dims("/", "Mobile", totalSessionCount=0)) + [
        metric(
            "ScrollDepth", dims("/", "Mobile", averageScrollDepth=70, sessionsCount=5)
        )
    ]
    got = await pages_of(export)
    assert got["groups"] == [cell("landing", "mobile", sessions=0)]


async def test_cells_come_in_page_then_device_order_and_at_most_forty() -> None:
    urls = {
        "landing": "/",
        "app:spread": "/app/",
        "app:history": "/app/?tab=history",
        "app:gap": "/app/?tab=gap",
        "app:pp": "/app/?tab=pp",
        "app:health": "/app/?tab=health",
        "app:flow": "/app/?tab=flow",
        "app:other": "/app/?tab=x",
        "privacy": "/privacy",
        "other": "/elsewhere",
    }
    devices = {
        "mobile": "Mobile",
        "tablet": "Tablet",
        "desktop": "PC",
        "other": "Email",
    }
    rows = [
        dims(url, raw, totalSessionCount=1)
        for raw in reversed(list(devices.values()))
        for url in reversed(list(urls.values()))
    ]
    rows += [
        dims("/app/?tab=y", "Other", totalSessionCount=1)
    ]  # 같은 칸(app:other, other)
    got = await pages_of(traffic(*rows))
    assert [(g["page"], g["device"]) for g in got["groups"]] == [
        (page, device) for page in urls for device in devices
    ]
    assert len(got["groups"]) == 40
    assert got["groups"][31]["sessions"] == 2


async def test_row_counts_and_the_thousand_row_cap() -> None:
    capped = [metric("PopularPages", *[dims(f"/p{i}", "PC") for i in range(1000)])]
    got = await pages_of(capped + traffic(dims("/", "PC", totalSessionCount=1)))
    assert (got["rowsIn"], got["rowLimitHit"]) == (1001, True)
    under = [metric("PopularPages", *[dims(f"/p{i}", "PC") for i in range(999)])]
    assert (await pages_of(under))["rowLimitHit"] is False


@pytest.mark.parametrize(
    ("url_key", "device_key"), [("Url", "DEVICE"), ("url", "device")]
)
async def test_dimension_keys_match_in_any_spelling(
    url_key: str, device_key: str
) -> None:
    row = {url_key: "/", device_key: "Mobile", "totalSessionCount": 2}
    assert (await pages_of(traffic(row)))["groups"] == [
        cell("landing", "mobile", sessions=2)
    ]


async def test_rows_without_url_and_device_are_bad_data_and_keep_the_last_values() -> (
    None
):
    wrong = traffic({"Page": "/", "Device": "PC", "totalSessionCount": 1})
    w = World(Clarity(pages=((200, PAGES_EXPORT), (200, wrong), (200, []))))
    ok = await w.get()
    w.advance(12)
    body = await w.get()
    assert (body["pages"]["state"], body["pages"]["code"]) == ("error", "bad_data")
    assert body["pages"]["groups"] == ok["pages"]["groups"]
    assert body["pages"]["fetchedAt"] == ok["pages"]["fetchedAt"]
    w.advance(12)
    empty = (await w.get())["pages"]  # 빈 목록 — 성공이다
    assert (empty["state"], empty["groups"], empty["rowsIn"]) == ("ok", [], 0)


async def test_a_page_metric_with_no_rows_is_a_success() -> None:
    got = await pages_of(traffic())
    assert (got["state"], got["groups"], got["rowsIn"]) == ("ok", [], 0)
