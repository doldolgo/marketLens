"""IP 하나 상세 — ip 검사 400·줄 고르기(IP 전체/가린 대역)·목록 행과 같은 값·줄 표시·partial·60초 캐시 (스펙 069 §3.6·§4)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.features.admin import access_ip
from app.features.admin.tests.access_fakes import (
    AFTER,
    HOUR,
    Feeds,
    line,
    rotated,
    write,
)
from app.main import create_app

T = AFTER - 3 * HOUR
IP = "203.0.113.7"
DETAIL_KEYS = (
    "state code window ip masked country region city asn org net requests pages js ws kinds status first last"
    " spark topPaths topAgents score verdict reasons geo verifiedCrawler hourly agents paths pairs recent partial"
).split()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("ACCESS_LOG_DIR", raising=False)
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "ip",
    [
        None,
        "",
        "2001:DB8::1",
        "2001:db8:0::1",
        "010.0.0.1",
        "1.2.3.4/24",
        "1.2.3.0/25",
        "(없음)",
        "(기타)",
        "9" * 100,
    ],
)
def test_ip_that_is_not_an_address_or_a_listed_band_is_a_400(
    client: TestClient, ip: str | None
) -> None:
    resp = client.get("/admin/access/addr", params={} if ip is None else {"ip": ip})
    assert resp.status_code == 400 and resp.headers["cache-control"] == "no-store"
    assert resp.json()["error"] == {
        "code": "bad_ip",
        "message": "ip 는 IPv4·IPv6 주소(정규 글자) 또는 대역 목록의 글자(IPv4 /24·IPv6 /48)여야 합니다.",
        "detail": None,
    }


@pytest.mark.parametrize("ip", [IP, "2001:db8::1", "203.0.113.0/24", "2001:db8:1::/48"])
def test_a_valid_ip_without_logs_is_unconfigured(client: TestClient, ip: str) -> None:
    resp = client.get("/admin/access/addr", params={"ip": ip, "window": "1y"})
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    body = resp.json()
    assert list(body) == DETAIL_KEYS
    assert (body["state"], body["code"], body["window"], body["ip"]) == (
        "unconfigured",
        "no_file",
        "24h",
        ip,
    )
    assert all(body[k] is None for k in DETAIL_KEYS[4:])


def logs(directory: Path) -> None:
    rotated(
        directory,
        [
            line(T - HOUR, "/", ip="203.0.113.0"),
            line(T - HOUR + 5, "/privacy", ip="203.0.113.0"),
        ],
    )
    write(
        directory,
        "access.log",
        [
            line(T, "/", ip=IP),
            line(T + 1, "/clarity.js", ip=IP),
            line(T + 2, "/assets/a.js", ip=IP),
            line(T + 3, "/.env", status=404, ip=IP),
            line(T + 4, "/api/ws/spreads", status=101, ip=IP, duration=12.0),
            line(T + 5, "/", ip="203.0.113.70"),  # 다른 IP — 같은 앞부분 글자
            line(
                T + 6, "/kimp-chart", ip="203.0.113.0"
            ),  # 시작 시각 뒤의 `.0` — 진짜 주소
        ],
    )


async def test_a_full_ip_detail_matches_its_list_row_and_flags_lines(
    tmp_path: Path,
) -> None:
    logs(tmp_path)
    f = Feeds(tmp_path, AFTER)
    listed = {row["ip"]: row for row in (await f.addrs())["rows"]}[IP]
    body = await f.addr(IP)
    assert list(body) == DETAIL_KEYS
    assert (body["state"], body["ip"], body["masked"], body["partial"]) == (
        "ok",
        IP,
        False,
        False,
    )
    for key in (
        "requests",
        "pages",
        "js",
        "ws",
        "kinds",
        "status",
        "first",
        "last",
        "spark",
        "score",
        "verdict",
    ):
        assert body[key] == listed[key], key
    assert [r["code"] for r in body["reasons"]] == [
        r["code"] for r in listed["reasons"]
    ]
    assert body["requests"] == 5 and sum(h["requests"] for h in body["hourly"]) == 5
    flags = {r["path"]: r["flags"] for r in body["recent"]}
    assert flags == {
        "/": [],
        "/clarity.js": ["js"],
        "/assets/a.js": ["asset"],
        "/.env": ["probe", "404"],
        "/api/ws/spreads": ["ws"],
    }
    assert body["geo"] == {
        "country": None,
        "region": None,
        "city": None,
        "lat": None,
        "lon": None,
        "asn": None,
        "org": None,
        "net": None,
        "ptr": None,
        "approx": True,
    }
    assert body["verifiedCrawler"] is None
    assert len(body["pairs"]) == 1


async def test_a_masked_band_detail_reads_only_lines_before_the_start(
    tmp_path: Path,
) -> None:
    logs(tmp_path)
    f = Feeds(tmp_path, AFTER, effective="2026-09-01")
    band = await f.addr("203.0.113.0/24", "7d")
    assert (band["masked"], band["requests"]) == (True, 2)
    assert {r["path"] for r in band["recent"]} == {"/", "/privacy"}
    assert band["geo"]["ptr"] is None and band["verifiedCrawler"] is None
    real = await f.addr("203.0.113.0", "7d")  # 시작 시각 뒤의 `.0` 은 IP 전체 행
    assert (real["masked"], real["requests"]) == (False, 1)
    assert real["recent"][0]["path"] == "/kimp-chart"


async def test_without_a_list_cache_the_scan_finds_the_start_itself(
    tmp_path: Path,
) -> None:
    logs(tmp_path)
    f = Feeds(tmp_path, AFTER, effective="2026-09-01")
    assert (await f.addr("203.0.113.0", "7d"))["requests"] == 1  # 목록을 부르지 않았다


async def test_partial_when_the_budget_drops_older_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs(tmp_path)
    monkeypatch.setattr(
        access_ip, "BUDGET_BYTES", (tmp_path / "access.log").stat().st_size
    )
    body = await Feeds(tmp_path, AFTER).addr(IP)
    assert body["partial"] is True and body["requests"] == 5


async def test_the_detail_is_cached_60_seconds_per_ip_and_window(
    tmp_path: Path,
) -> None:
    write(tmp_path, "access.log", [line(T, "/", ip=IP)])
    f = Feeds(tmp_path, AFTER)
    assert (await f.addr(IP))["requests"] == 1
    write(tmp_path, "access.log", [line(T, "/", ip=IP), line(T + 1, "/", ip=IP)])
    f.t = 59
    assert (await f.addr(IP))["requests"] == 1
    assert (await f.addr(IP, "7d"))["requests"] == 2  # 창마다 따로
    f.t = 60
    assert (await f.addr(IP))["requests"] == 2
