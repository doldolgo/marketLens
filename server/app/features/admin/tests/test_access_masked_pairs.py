"""IP 전체 기록 뒤에도 짝 열쇠는 가린 IP(/24·/48) — 038 방문자·067 대역 짝 수·067 상세 짝 흐름이 전체 저장 전후로 이어진다
(스펙 069 §3.6)."""

from pathlib import Path

from app.features.admin import access_ip
from app.features.admin.access_bands import ips_query
from app.features.admin.access_cache import AccessLog, bounds
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    GATE,
    HOUR,
    line,
    summary,
    write,
)

T0 = AFTER - 3 * HOUR
UA = f"{CHROME} same"


def lines() -> list[str]:
    """같은 KST 날·같은 UA — 가린 줄 하나(배포 전)와 같은 /24 의 전체 줄 둘(배포 뒤), 다른 /24 의 전체 줄 하나.
    IPv6 도 가린 /48 줄과 같은 /48 의 전체 줄."""
    return [
        line(T0, "/", ua=UA, ip="203.0.113.0"),
        line(T0 + 60, "/", ua=UA, ip="203.0.113.77"),
        line(T0 + 120, "/", ua=UA, ip="203.0.113.5"),
        line(T0 + 180, "/", ua=UA, ip="198.51.100.9"),
        line(T0 + 240, "/", ua=UA, ip="2001:db8:1::"),
        line(T0 + 300, "/", ua=UA, ip="2001:db8:1:42::9"),
    ]


def test_visitors_count_one_pair_per_masked_band_and_ua(tmp_path: Path) -> None:
    write(tmp_path, "access.log", lines())
    people = summary(tmp_path, AFTER)["visitors"]
    assert people["shaped"] == 3  # 203.0.113/24 · 198.51.100/24 · 2001:db8:1::/48


def test_band_rows_count_one_pair_across_masked_and_full_lines(tmp_path: Path) -> None:
    write(tmp_path, "access.log", lines())
    body = AccessLog(str(tmp_path), GATE).ips("24h", AFTER, ips_query())[0]
    rows = {row["band"]: row for row in body["bands"]}
    assert rows["203.0.113.0/24"]["requests"] == 3
    assert rows["203.0.113.0/24"]["pairs"] == 1
    assert rows["2001:db8:1::/48"]["pairs"] == 1
    assert rows["198.51.100.0/24"]["pairs"] == 1


def test_band_detail_folds_full_ips_into_one_pair_flow(tmp_path: Path) -> None:
    write(tmp_path, "access.log", lines())
    values, _ = access_ip.scan(
        str(tmp_path), "203.0.113.0/24", "24h", AFTER, GATE, None
    )
    assert len(values["pairs"]) == 1
    assert values["pairs"][0]["npages"] == 3
    start, _ = bounds("24h", AFTER, GATE)
    assert sum(h["requests"] for h in values["hourly"] if h["ts"] >= start) == 3
