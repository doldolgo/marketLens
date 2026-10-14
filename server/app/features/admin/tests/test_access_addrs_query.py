"""IP 하나 단위 목록 피드 — 거르기·정렬 넷·쪽·60초 캐시·1시간 비움·위치(DB-IP·City)·경로 no-store (스펙 069 §3.5·§4 '피드 둘')."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.features.admin import geo_city
from app.features.admin.geo_fetch import utc_month
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    GOOGLEBOT,
    HOUR,
    Feeds,
    line,
    rotated,
    watch_opens,
    write,
)
from app.features.admin.tests.city_fakes import city_rows, parsed
from app.features.admin.tests.geo_fakes import DbIp, inline
from app.main import create_app

T = AFTER - 3 * HOUR
CURL = "curl/8.7.1"
ROW_KEYS = (
    "ip masked country region city asn org net requests pages js ws kinds status first last spark topPaths"
    " topAgents score verdict reasons"
).split()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("ACCESS_LOG_DIR", raising=False)
    monkeypatch.delenv("GEO_CACHE_DIR", raising=False)
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def logs(directory: Path) -> None:
    """가린 줄 하나(앞 파일)와 전체 줄 — 사람(KR 통신사), 탐색 봇(KR 클라우드), 검색 로봇(DE), 운영자, IPv6."""
    rotated(directory, [line(T - HOUR, "/", ip="203.0.113.0")])
    human = [
        line(T + i, p, ip="203.0.113.7")
        for i, p in enumerate(("/", "/clarity.js", "/assets/a.js", "/app/"))
    ]
    probe = [
        line(T + 10 + i, "/.env", status=404, ua=CURL, ip="198.51.100.9")
        for i in range(5)
    ]
    crawler = [
        line(T + 20 + i, f"/p{i}", ua=GOOGLEBOT, ip="192.0.2.5") for i in range(3)
    ]
    operator = [
        line(T + 30 + i, "/", ip="203.0.113.99", referer="https://admin.kimptrack.com")
        for i in range(2)
    ]
    six = [line(T + 40, "/", ip="2001:db8:1:42::9")]
    write(directory, "access.log", human + probe + crawler + operator + six)


def feeds(directory: Path, cache: Path | None = None) -> Feeds:
    dbip = DbIp().serve(utc_month(AFTER))
    f = Feeds(
        directory,
        AFTER,
        geo_transport=dbip.transport,
        geo_start=inline,
        geo_cache_dir=None if cache is None else str(cache),
        city_start=inline,
    )
    f.feeds.geo.ensure()
    return f


async def test_rows_carry_place_verdict_and_the_full_ip(tmp_path: Path) -> None:
    cache = tmp_path / "geo"
    cache.mkdir()
    geo_city.write(
        geo_city.read_city(parsed(city_rows()), utc_month(AFTER)), str(cache)
    )
    logs(tmp_path)
    f = feeds(tmp_path, cache)
    f.feeds.city.boot()
    body = await f.addrs()
    assert (body["state"], body["window"]) == ("ok", "24h")
    rows = {row["ip"]: row for row in body["rows"]}
    human = rows["203.0.113.7"]
    assert list(human) == ROW_KEYS
    assert (human["country"], human["net"], human["asn"], human["org"]) == (
        "KR",
        "telecom_kr",
        4766,
        "Korea Telecom",
    )
    assert (human["region"], human["city"]) == ("Seoul", 'Gangnam-gu, "Yeoksam"')
    assert human["verdict"] == "human"
    probe = rows["198.51.100.9"]
    assert (probe["net"], probe["org"], probe["verdict"]) == (
        "cloud",
        "Amazon.com, Inc.",
        "bot",
    )
    assert (
        rows["203.0.113.99"]["verdict"] == "operator"
        and rows["203.0.113.99"]["score"] is None
    )
    assert rows["192.0.2.5"]["verdict"] == "bot"
    assert (rows["2001:db8:1:42::9"]["net"], rows["2001:db8:1:42::9"]["city"]) == (
        "ipv6",
        None,
    )
    band = rows["203.0.113.0/24"]
    assert band["masked"] is True and band["region"] == "Seoul" and band["asn"] == 4766
    assert band["verdict"] == "unknown"  # 요청 1
    assert body["since"] == int(T)


async def test_filters_by_net_country_verdict_prefix_and_masked(tmp_path: Path) -> None:
    logs(tmp_path)
    f = feeds(tmp_path)

    async def ips(**params: str) -> list[str]:
        return sorted(row["ip"] for row in (await f.addrs(**params))["rows"])

    assert await ips(net="cloud") == ["198.51.100.9"]
    assert await ips(country="kr") == [
        "198.51.100.9",
        "203.0.113.0/24",
        "203.0.113.7",
        "203.0.113.99",
    ]
    assert await ips(verdict="bot") == ["192.0.2.5", "198.51.100.9"]
    assert await ips(verdict="human,operator") == ["203.0.113.7", "203.0.113.99"]
    assert (
        await ips(verdict="nonsense") == await ips()
    )  # 모양 틀린 값은 기본값(거르지 않음)
    assert await ips(q="203.0.113") == ["203.0.113.0/24", "203.0.113.7", "203.0.113.99"]
    assert await ips(q="2001:DB8") == ["2001:db8:1:42::9"]
    assert "203.0.113.0/24" not in await ips(masked="0")
    assert len(await ips(masked="1")) == len(await ips()) == 6
    body = await f.addrs(verdict="bot", limit="1")
    assert (body["total"], len(body["rows"])) == (2, 1)


async def test_sorts_big_first_ties_by_ip_text_and_operator_last_by_score(
    tmp_path: Path,
) -> None:
    logs(tmp_path)
    f = feeds(tmp_path)

    async def order(sort: str) -> list[str]:
        return [row["ip"] for row in (await f.addrs(sort=sort))["rows"]]

    assert (await order("requests"))[:2] == ["198.51.100.9", "203.0.113.7"]  # 5·4
    assert (await order("pages"))[0] == "203.0.113.7"
    assert (await order("last"))[0] == "2001:db8:1:42::9"
    by_score = await order("score")
    assert by_score[0] == "198.51.100.9" and by_score[-1] == "203.0.113.99"
    assert await order("bogus") == await order("requests")


async def test_pages_by_offset_and_limit_up_to_500(tmp_path: Path) -> None:
    write(
        tmp_path,
        "access.log",
        [line(T + i, "/", ip=f"10.0.{i // 250}.{i % 250 + 1}") for i in range(600)],
    )
    f = feeds(tmp_path)
    assert len((await f.addrs(limit="9999"))["rows"]) == 500
    assert len((await f.addrs(limit="0"))["rows"]) == 1
    assert len((await f.addrs(limit="abc"))["rows"]) == 200
    tail = await f.addrs(sort="last", offset="590", limit="50")
    assert (tail["total"], len(tail["rows"])) == (600, 10)
    assert (await f.addrs(offset="-1", limit="1"))["rows"] == (
        await f.addrs(limit="1")
    )["rows"]


async def test_the_list_is_cached_60_seconds_and_rearms_the_hour_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rotated(tmp_path, [line(T - HOUR, "/", ip="10.0.0.1")])
    write(tmp_path, "access.log", [line(T, "/", ip="10.0.0.1")])
    opens = watch_opens(monkeypatch)
    f = Feeds(tmp_path, AFTER)
    assert (await f.addrs())["rows"][0]["requests"] == 2
    write(
        tmp_path,
        "access.log",
        [line(T, "/", ip="10.0.0.1"), line(T + 1, "/", ip="10.0.0.1")],
    )
    f.t = 59
    assert (await f.addrs())["rows"][0]["requests"] == 2
    f.t = 60
    assert (await f.addrs())["rows"][0]["requests"] == 3
    await f.ips()  # 067 목록과 같은 회전 파일 캐시
    name = next(n for n in opens if n != "access.log")
    assert opens[name] == 1
    f.timers[-1].callback()  # 1시간 아무도 부르지 않음
    await f.addrs()
    assert opens[name] == 2


def test_route_is_no_store_and_unconfigured_without_logs(client: TestClient) -> None:
    resp = client.get(
        "/admin/access/addrs?window=1y&limit=abc&sort=x&masked=2&verdict=?"
    )
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    assert resp.json() == {
        "state": "unconfigured",
        "code": "no_file",
        "window": "24h",
        "total": None,
        "since": None,
        "rows": None,
    }


def test_route_reads_the_window_and_the_query(
    client: TestClient, tmp_path: Path
) -> None:
    write(tmp_path, "access.log", [line(AFTER - 60, "/", ip="10.0.1.7", ua=CHROME)])
    client.app.state.admin_visits = Feeds(tmp_path, AFTER).feeds
    body = client.get("/admin/access/addrs?window=7d&q=10.0.1&verdict=unknown").json()
    assert (body["state"], body["window"], body["total"]) == ("ok", "7d", 1)
    assert body["rows"][0]["ip"] == "10.0.1.7"
    assert client.get("/admin/access/addrs?q=10.0.2").json()["total"] == 0
