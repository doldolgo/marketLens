"""역방향 DNS·검색 로봇 주소 확인 — 가짜 해석기로 시간 초과·없음·정방향 불일치 → fake_crawler, 24시간·5,000 캐시
(스펙 069 §3.3·§3.4·§3.8·§4 'PTR·확인')."""

import threading
import time
from pathlib import Path

import pytest

from app.features.admin import access_dns
from app.features.admin.access_dns import Dns
from app.features.admin.tests.access_fakes import (
    AFTER,
    GOOGLEBOT,
    HOUR,
    Feeds,
    Resolver,
    line,
    write,
)

IP = "66.249.66.1"
NAME = "crawl-66-249-66-1.googlebot.com"
BING = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
YETI = "Mozilla/5.0 (compatible; Yeti/1.1; +https://naver.me/spd)"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_a_googlebot_whose_name_points_back_is_verified() -> None:
    dns = Dns(Resolver({IP: NAME}, {NAME: {IP}}))
    assert dns.check(IP, ["google"]) == (NAME, True)


@pytest.mark.parametrize(
    ("names", "addresses", "claims", "expected"),
    [
        (
            {IP: NAME},
            {NAME: {"66.249.66.2"}},
            ["google"],
            (NAME, False),
        ),  # 정방향 불일치
        (
            {IP: "host.example.net"},
            {},
            ["google"],
            ("host.example.net", False),
        ),  # 도메인이 아니다
        (
            {IP: "crawl.search.msn.com"},
            {"crawl.search.msn.com": {IP}},
            ["google"],
            ("crawl.search.msn.com", False),
        ),
        (
            {IP: "crawl.search.msn.com"},
            {"crawl.search.msn.com": {IP}},
            ["bing"],
            ("crawl.search.msn.com", True),
        ),
        (
            {IP: "notgooglebot.com"},
            {"notgooglebot.com": {IP}},
            ["google"],
            ("notgooglebot.com", False),
        ),
        ({}, {}, ["naver"], (None, False)),  # PTR 없음
        (
            {IP: "home.example.kr"},
            {},
            [],
            ("home.example.kr", None),
        ),  # 로봇을 주장하지 않음
        ({IP: "error"}, {}, ["google"], (None, None)),  # 해석기 오류 — 모름
    ],
)
def test_the_check_answers(
    names: dict, addresses: dict, claims: list, expected: tuple
) -> None:
    assert Dns(Resolver(names, addresses)).check(IP, claims) == expected


def test_a_slow_resolver_times_out_in_a_second_as_unknown_and_is_not_cached() -> None:
    resolver = Resolver({IP: "slow"})
    dns = Dns(resolver, timeout=0.3)
    began = time.perf_counter()
    assert dns.check(IP, ["google"]) == (None, None)
    assert time.perf_counter() - began < 1.0
    resolver.gate.set()
    resolver.names[IP] = NAME
    resolver.addresses[NAME] = {IP}
    assert dns.check(IP, ["google"]) == (NAME, True)
    assert access_dns.TIMEOUT_SEC == 1.0


def test_a_slow_forward_lookup_is_unknown() -> None:
    resolver = Resolver({IP: NAME}, {NAME: {"slow"}})
    assert Dns(resolver, timeout=0.2).check(IP, ["google"]) == (None, None)
    resolver.gate.set()


def test_results_are_cached_24_hours_and_5000_ips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    resolver = Resolver({IP: NAME}, {NAME: {IP}})
    dns = Dns(resolver, mono=clock)
    dns.check(IP, ["google"])
    dns.check(IP, ["google"])
    assert resolver.asked == [IP, NAME]
    clock.t = 86_399
    dns.check(IP, [])
    assert len(resolver.asked) == 2
    clock.t = 86_400
    dns.check(IP, [])
    assert len(resolver.asked) == 4
    monkeypatch.setattr(access_dns, "CACHE_MAX", 2)
    for ip in ("10.0.0.1", "10.0.0.2"):
        dns.check(ip, [])
    asked = len(resolver.asked)
    dns.check("10.0.0.1", [])
    assert len(resolver.asked) == asked  # 담긴 것
    dns.check(IP, [])
    assert len(resolver.asked) == asked + 2  # 가장 오래된 IP 는 밀려났다


def test_at_most_four_lookups_wait_at_once() -> None:
    resolver = Resolver({f"10.0.0.{i}": "slow" for i in range(6)})
    dns = Dns(resolver, timeout=0.2)
    threads = [
        threading.Thread(target=dns.check, args=(f"10.0.0.{i}", [])) for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    began = time.perf_counter()
    assert dns.check("10.0.0.5", []) == (
        None,
        None,
    )  # 늦은 넷이 아직 돈다 — 기다리지 않고 모름
    assert time.perf_counter() - began < 0.1
    resolver.gate.set()


async def test_the_detail_adds_fake_crawler_when_the_check_fails(
    tmp_path: Path,
) -> None:
    t = AFTER - 2 * HOUR
    lines = [line(t + i, "/", ua=GOOGLEBOT, ip=IP) for i in range(3)]
    lines += [line(t + 10 + i, "/", ua=YETI, ip="203.0.113.8") for i in range(3)]
    lines += [line(t + 20 + i, "/", ua=BING, ip="192.0.2.9") for i in range(3)]
    write(tmp_path, "access.log", lines)
    resolver = Resolver(
        {IP: NAME, "203.0.113.8": "crawl.naver.com", "192.0.2.9": "pool.example.net"},
        {NAME: {IP}, "crawl.naver.com": {"203.0.113.250"}},
    )
    f = Feeds(tmp_path, AFTER, dns=Dns(resolver))
    good = await f.addr(IP)
    assert (good["verifiedCrawler"], good["geo"]["ptr"]) == (True, NAME)
    assert "fake_crawler" not in {r["code"] for r in good["reasons"]}
    listed = {row["ip"]: row for row in (await f.addrs())["rows"]}
    for ip in ("203.0.113.8", "192.0.2.9"):
        body = await f.addr(ip)
        assert body["verifiedCrawler"] is False, ip
        assert body["reasons"][-1]["code"] == "fake_crawler"
        assert body["score"] == listed[ip]["score"] + 30
    assert "fake_crawler" not in str(listed)  # 목록은 확인하지 않는다
