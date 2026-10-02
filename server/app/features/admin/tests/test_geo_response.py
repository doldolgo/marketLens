"""`/admin/access` 의 `geo` — 적은 나라 묶기·국내 통신사 칸·모름·망 종류 정렬·셈·IPv6·두 파일·게이트 걸침 (스펙 039 §3.5·§3.6·§4 '응답')."""

from pathlib import Path
from typing import Any

import pytest

from app.features.admin import access_hours
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    GATE,
    HOUR,
    Feeds,
    line,
    rotated,
    write,
)
from app.features.admin.tests.geo_fakes import (
    DE_TELECOM,
    KR_TELECOM,
    MONTH,
    PLACES,
    DbIp,
    inline,
)

T0 = AFTER - 2 * HOUR  # 오늘(KST) 08:30
PLAIN = "Plain Org"
Places = dict[str, tuple[str, int | None, str]]


def place(
    i: int, cc: str, asn: int | None = 64_501
) -> tuple[str, tuple[str, int | None, str]]:
    return f"10.0.{i}.0", (cc, asn, PLAIN)


def visits(ip: str, shaped: int, confirmed: int = 0, *, tag: str = "") -> list[str]:
    """그 IP 에서 서로 다른 UA 짝 `shaped` 개 — 앞의 `confirmed` 개는 clarity.js 도 받았다(JS 신호)."""
    lines = []
    for i in range(shaped):
        ua = f"{CHROME} {tag}{ip}-{i}"
        lines.append(line(T0 + i, "/", ua=ua, ip=ip))
        if i < confirmed:
            lines.append(line(T0 + i + 0.5, "/clarity.js", ua=ua, ip=ip))
    return lines


async def geo(
    tmp_path: Path,
    lines: list[str],
    places: Places | None = None,
    *,
    now: float = AFTER,
    window: str = "24h",
) -> dict[str, Any]:
    """판을 먼저 올린 피드로 한 번 — 응답 전체."""
    write(tmp_path, "access.log", lines)
    dbip = DbIp().serve(MONTH, {**PLACES, **(places or {})})
    f = Feeds(tmp_path, now, geo_transport=dbip.transport, geo_start=inline)
    f.feeds.geo.ensure()
    body = await f.get(window)
    assert body["geo"]["state"] == "ok", body["geo"]
    return body


async def test_the_ok_part_has_its_eight_keys_and_the_visitors_since(
    tmp_path: Path,
) -> None:
    body = await geo(tmp_path, visits(DE_TELECOM, 3))
    g = body["geo"]
    assert (
        list(g) == "state code month loadedAt sinceTs countries networks ipv6".split()
    )
    assert (g["code"], g["month"], g["loadedAt"]) == (None, MONTH, int(AFTER * 1000))
    assert g["sinceTs"] == body["visitors"]["sinceTs"] == body["startTs"]
    assert (g["countries"], g["networks"], g["ipv6"]) == (
        [["DE", 0, 3]],
        [["telecom", 0, 3]],
        0,
    )


async def test_countries_under_three_or_with_one_or_two_confirmed_are_other(
    tmp_path: Path,
) -> None:
    places = dict(
        [place(1, "JP"), place(2, "FR"), place(3, "IT"), place(4, "ES"), place(5, "NL")]
    )
    lines = visits("10.0.1.0", 2) + visits("10.0.2.0", 3)  # JP 2·FR 3
    lines += (
        visits("10.0.3.0", 5, 1) + visits("10.0.4.0", 5, 3) + visits("10.0.5.0", 4, 0)
    )
    g = (await geo(tmp_path, lines, places))["geo"]
    assert g["countries"] == [
        ["ES", 3, 5],
        ["NL", 0, 4],
        ["FR", 0, 3],
        ["(기타)", 1, 7],  # JP 2 + IT 5(confirmed 1)
    ]


async def test_25_countries_keep_20_rows_and_sum_the_rest(tmp_path: Path) -> None:
    codes = [a + b for a in "ABCDE" for b in "ABCDE"]
    places = dict(place(i, cc) for i, cc in enumerate(codes))
    lines = [ln for i in range(25) for ln in visits(f"10.0.{i}.0", 30 - i)]
    g = (await geo(tmp_path, lines, places))["geo"]
    assert g["countries"][:20] == [[codes[i], 0, 30 - i] for i in range(20)]
    assert g["countries"][20:] == [["(기타)", 0, 10 + 9 + 8 + 7 + 6]]
    assert sum(r[2] for r in g["countries"]) == sum(range(6, 31))


@pytest.mark.parametrize(
    ("shaped", "confirmed", "countries", "networks"),
    [
        (2, 0, [["FR", 0, 3], ["(기타)", 0, 2]], [["other", 0, 3], ["telecom", 0, 2]]),
        (3, 1, [["FR", 0, 3], ["(기타)", 1, 3]], [["other", 0, 3], ["telecom", 1, 3]]),
        (3, 0, [["FR", 0, 3], ["KR", 0, 3]], [["other", 0, 3], ["telecom_kr", 0, 3]]),
    ],
)
async def test_telecom_kr_folds_into_telecom_when_kr_is_hidden(
    tmp_path: Path, shaped: int, confirmed: int, countries: list, networks: list
) -> None:
    lines = visits(KR_TELECOM, shaped, confirmed) + visits("10.0.2.0", 3)
    g = (await geo(tmp_path, lines, dict([place(2, "FR")])))["geo"]
    assert (g["countries"], g["networks"]) == (countries, networks)


async def test_unknown_country_and_out_of_range_are_other(tmp_path: Path) -> None:
    lines = visits("10.0.5.0", 3) + visits("10.0.6.0", 3)  # ZZ · 두 자료 밖
    g = (await geo(tmp_path, lines, dict([place(5, "ZZ")])))["geo"]
    assert g["countries"] == [["(기타)", 0, 6]]
    assert g["networks"] == [["other", 0, 3], ["unknown", 0, 3]]


async def test_networks_are_sorted_and_have_no_zero_rows(tmp_path: Path) -> None:
    places = dict([place(7, "US", 16509), place(8, "US")])
    lines = visits(DE_TELECOM, 5, 5) + visits("10.0.8.0", 3) + visits("10.0.7.0", 3, 3)
    g = (await geo(tmp_path, lines, places))["geo"]
    assert g["networks"] == [["telecom", 5, 5], ["cloud", 3, 3], ["other", 0, 3]]
    assert g["countries"] == [["US", 3, 6], ["DE", 5, 5]]


async def test_ws_signal_confirms_and_ipv6_counts_only_in_ipv6(tmp_path: Path) -> None:
    # DE — 페이지만 본 짝 셋(브라우저 모양)과 WS 101 만 남긴 짝 셋(확인 — JS 신호)
    lines = visits(DE_TELECOM, 3)
    lines += [
        line(
            T0 + 9 + i,
            "/api/ws/spreads",
            status=101,
            ua=f"{CHROME} ws{i}",
            ip=DE_TELECOM,
        )
        for i in range(3)
    ]
    lines += [
        line(T0 + i, "/", ua=f"{CHROME} six{i}", ip="2001:db8:5::") for i in range(4)
    ]
    body = await geo(tmp_path, lines)
    g = body["geo"]
    assert g["countries"] == [["DE", 3, 6]] and g["networks"] == [["telecom", 3, 6]]
    assert g["ipv6"] == 4 and body["visitors"]["shaped"] == 10


async def test_one_pair_on_one_day_in_two_files_counts_once(tmp_path: Path) -> None:
    rotated(tmp_path, visits(DE_TELECOM, 3))
    g = (await geo(tmp_path, visits(DE_TELECOM, 3)))["geo"]
    assert g["countries"] == [["DE", 0, 3]]


async def test_a_24h_window_over_the_gate_looks_up_only_lines_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked: list[str] = []
    real = access_hours.locate

    def watched(table: Any, ip: str) -> tuple[str | None, str]:
        looked.append(ip)
        return real(table, ip)

    monkeypatch.setattr(access_hours, "locate", watched)
    before = [
        line(GATE - 3 * HOUR + i, "/", ua=f"{CHROME} {i}", ip=ip)
        for i, ip in enumerate(["10.9.9.0", DE_TELECOM])
    ]
    after = [
        line(GATE + HOUR + i, "/", ua=f"{CHROME} {i + 1}", ip=DE_TELECOM)
        for i in range(3)
    ]
    body = await geo(tmp_path, before + after, now=GATE + 5 * HOUR)
    g = body["geo"]
    assert "10.9.9.0" not in looked and looked.count(DE_TELECOM) == 3
    assert g["sinceTs"] == GATE == body["visitors"]["sinceTs"]
    assert g["countries"] == [
        ["DE", 0, 3]
    ]  # 게이트 앞 같은 짝(UA 1)은 게이트 뒤 날로만 하나


@pytest.mark.parametrize(
    ("ip", "country", "network"),
    [("203.0.113.77", "KR", "telecom_kr"), ("not-an-ip", "(기타)", "unknown")],
)
async def test_ip_text_is_cut_to_24_or_counted_as_other(
    tmp_path: Path, ip: str, country: str, network: str
) -> None:
    g = (await geo(tmp_path, visits(ip, 3)))["geo"]
    assert (g["countries"], g["networks"]) == ([[country, 0, 3]], [[network, 0, 3]])


async def test_no_log_file_has_a_null_geo_and_fetches_nothing() -> None:
    dbip = DbIp().serve()
    f = Feeds(None, AFTER, geo_transport=dbip.transport, geo_start=inline)
    body = await f.get("7d")
    assert (body["state"], body["geo"]) == ("unconfigured", None)
    assert dbip.requests == [] and f.feeds.geo.table is None
