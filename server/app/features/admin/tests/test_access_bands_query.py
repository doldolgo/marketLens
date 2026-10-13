"""IP 대역별 목록 — 거르기 넷·정렬 셋·쪽·limit 500·60초 캐시·1시간 비움·내지 않는 것·경로 (스펙 067 §3.2·§3.5·§4)."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.features.admin.access_bands import ips_query
from app.features.admin.access_cache import AccessLog
from app.features.admin.access_pairs import pair_hash
from app.features.admin.geo_fetch import utc_month
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    GATE,
    GOOGLEBOT,
    HOUR,
    IPHONE,
    Feeds,
    line,
    rotated,
    watch_opens,
    write,
)
from app.features.admin.tests.geo_fakes import (
    DE_TELECOM,
    KR_CLOUD,
    KR_TELECOM,
    DbIp,
    inline,
)
from app.main import create_app

CURL = "curl/8.7.1"
KEY = b"\x07" * 16


def ips(
    directory: Path, now: float = AFTER, window: str = "24h", **params: str
) -> dict[str, Any]:
    """새 캐시 하나로 창 하나의 목록."""
    return AccessLog(str(directory), GATE).ips(window, now, ips_query(**params))[0]


def by_band(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["band"]: row for row in body["bands"]}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("ACCESS_LOG_DIR", raising=False)
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


# --- 거르기·정렬·쪽 (§3.2) ---------------------------------------------------------------


async def test_filters_by_network_kind_country_prefix_and_bots(tmp_path: Path) -> None:
    t = AFTER - 2 * HOUR
    write(
        tmp_path,
        "access.log",
        [
            line(t, "/", ua=CHROME, ip=KR_TELECOM),
            line(
                t + 1, "/", ua=GOOGLEBOT, ip=KR_CLOUD
            ),  # 사람 브라우저 모양 요청이 없는 대역
            line(t + 2, "/", ua=CURL, ip=KR_CLOUD),
            line(t + 3, "/", ua=CHROME, ip=DE_TELECOM),
            line(t + 4, "/", ua=CHROME, ip="2001:db8:1::"),
            line(t + 5, "/", ua=CHROME, ip=None),
        ],
    )
    dbip = DbIp().serve(utc_month(AFTER))
    f = Feeds(tmp_path, AFTER, geo_transport=dbip.transport, geo_start=inline)
    f.feeds.geo.ensure()

    async def bands(**params: str) -> tuple[list[str], int]:
        body = await f.ips(**params)
        return [row["band"] for row in body["bands"]], body["total"]

    rows = by_band(await f.ips())
    assert {band: (row["country"], row["net"]) for band, row in rows.items()} == {
        "203.0.113.0/24": ("KR", "telecom_kr"),
        "198.51.100.0/24": ("KR", "cloud"),
        "192.0.2.0/24": ("DE", "telecom"),
        "2001:db8:1::/48": (None, "ipv6"),
        "(없음)": (None, None),
    }
    assert await bands(net="cloud") == (["198.51.100.0/24"], 1)
    assert await bands(net="ipv6") == (["2001:db8:1::/48"], 1)
    assert await bands(country="kr") == (["198.51.100.0/24", "203.0.113.0/24"], 2)
    assert await bands(country="DE", net="telecom") == (["192.0.2.0/24"], 1)
    assert await bands(q="203.0") == (["203.0.113.0/24"], 1)
    assert await bands(q="2001:DB8") == (["2001:db8:1::/48"], 1)
    assert await bands(net="nothing") == ([], 0)
    kept, total = await bands(bots="0")
    assert total == 4 and "198.51.100.0/24" not in kept
    assert (await bands(bots="1"))[1] == (await bands())[1] == 5


def test_sorts_by_requests_pages_or_last_and_ties_by_band_text(tmp_path: Path) -> None:
    t = AFTER - 3 * HOUR
    write(
        tmp_path,
        "access.log",
        [
            *(
                line(t + i, "/a.css", ip="10.0.1.0") for i in range(3)
            ),  # 요청 3·페이지 0
            line(t + 3, "/", ip="10.0.2.0"),
            line(t + 4, "/", ip="10.0.2.0"),
            line(t + 5, "/", ip="10.0.4.0"),
            line(t + 6, "/", ip="10.0.4.0"),
            line(t + 7, "/", ip="10.0.3.0"),  # 가장 늦은 줄
        ],
    )

    def order(sort: str) -> list[str]:
        return [row["band"].split(".")[2] for row in ips(tmp_path, sort=sort)["bands"]]

    assert order("requests") == ["1", "2", "4", "3"]
    assert order("pages") == ["2", "4", "3", "1"]
    assert order("last") == ["3", "4", "2", "1"]
    assert order("bogus") == order("requests")


def test_pages_of_rows_by_offset_and_limit_up_to_500(tmp_path: Path) -> None:
    t = AFTER - 3 * HOUR
    bands = [f"10.{1 + i // 250}.{i % 250}.0" for i in range(501)]
    write(
        tmp_path, "access.log", [line(t + i, "/", ip=ip) for i, ip in enumerate(bands)]
    )
    texts = sorted(f"{ip}/24" for ip in bands)

    def page(**params: str) -> list[str]:
        body = ips(tmp_path, **params)
        assert body["total"] == 501
        return [row["band"] for row in body["bands"]]

    assert page() == texts[:200]
    assert page(limit="500") == page(limit="9999") == texts[:500]
    assert page(limit="0") == texts[:1]
    assert page(limit="abc", offset="x") == texts[:200]
    assert page(offset="500") == texts[500:]
    assert page(offset="-3", limit="2") == texts[:2]


# --- 피드·HTTP (§3.2·§3.5) -----------------------------------------------------------------


async def test_the_list_is_cached_60_seconds_per_window_and_query(
    tmp_path: Path,
) -> None:
    t = AFTER - 2 * HOUR
    write(tmp_path, "access.log", [line(t, "/")])
    f = Feeds(tmp_path, AFTER)
    assert (await f.ips())["bands"][0]["requests"] == 1
    write(tmp_path, "access.log", [line(t, "/"), line(t + 1, "/")])
    f.t = 59
    assert (await f.ips())["bands"][0]["requests"] == 1
    f.t = 60
    assert (await f.ips())["bands"][0]["requests"] == 2
    assert (await f.ips(q="9"))["total"] == 0  # 거르기마다 따로


async def test_band_requests_rearm_the_hour_drop_that_empties_their_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rotated(tmp_path, [line(AFTER - 5 * HOUR, "/")])
    write(tmp_path, "access.log", [line(AFTER - 60, "/")])
    opens = watch_opens(monkeypatch)
    f = Feeds(tmp_path, AFTER)
    await f.ips()
    await f.ips(sort="last")  # 다른 칸이지만 회전 파일 캐시는 같은 것
    name = next(n for n in opens if n != "access.log")
    assert opens[name] == 1
    timer = f.timers[-1]
    assert timer.delay == 3600 and not timer.cancelled
    assert all(t.cancelled for t in f.timers[:-1])
    timer.callback()  # 1시간 아무도 부르지 않음
    await f.ips()
    assert opens[name] == 2


async def test_the_list_holds_no_user_agent_pair_hash_query_or_unmasked_ip(
    tmp_path: Path,
) -> None:
    t = AFTER - 2 * HOUR
    write(
        tmp_path,
        "access.log",
        [
            line(
                t,
                "/?gclid=SECRET",
                ua=IPHONE,
                ip="203.0.113.77",
                referer="https://evil.example/deep?q=secret",
            )
        ],
    )
    f = Feeds(tmp_path, AFTER, urandom=lambda n: KEY[:n])
    text = json.dumps(await f.ips(), ensure_ascii=False)
    for banned in ("203.0.113.77", IPHONE, "Mobile/15E148", "SECRET", "evil", "secret"):
        assert banned not in text, banned
    pair = pair_hash(KEY + b"2026-10-13", "203.0.113.77", IPHONE)
    assert pair.hex() not in text and str(int.from_bytes(pair)) not in text


def test_route_is_no_store_and_unconfigured_without_logs(client: TestClient) -> None:
    resp = client.get("/admin/access/ips?window=1y&limit=abc&sort=x&bots=2")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"
    assert resp.json() == {
        "state": "unconfigured",
        "code": "no_file",
        "window": "24h",
        "total": None,
        "bands": None,
    }


def test_route_reads_the_window_and_the_query(
    client: TestClient, tmp_path: Path
) -> None:
    write(tmp_path, "access.log", [line(AFTER - 60, "/", ip="10.0.1.0")])
    client.app.state.admin_visits = Feeds(tmp_path, AFTER).feeds
    body = client.get("/admin/access/ips?window=7d&q=10.0.1").json()
    assert (body["state"], body["window"], body["total"]) == ("ok", "7d", 1)
    assert list(body["bands"][0]) == [
        "band",
        "country",
        "net",
        "requests",
        "pages",
        "js",
        "ws",
        "pairs",
        "kinds",
        "status",
        "botPct",
        "first",
        "last",
        "spark",
        "topPaths",
        "topAgents",
    ]
    assert client.get("/admin/access/ips?q=10.0.2").json()["total"] == 0
