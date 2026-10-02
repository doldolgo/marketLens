"""`/admin/access` 의 `geo` — 적은 나라·망 종류 묶기(창 안 KST 하루 최대 짝 수)·국내 통신사 칸·모름·망 종류 정렬·셈·IPv6·두 파일·게이트 걸침 (스펙 039 §3.5·§3.6·§4 '응답')."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.features.admin import access_hours
from app.features.admin.geo_fetch import utc_month
from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    DAY,
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
    PLACES,
    DbIp,
    inline,
)

T0 = AFTER - 2 * HOUR  # 오늘(KST) 08:30
YESTERDAY = AFTER - 20 * HOUR  # 어제(KST) 14:30 — 24시간 창 안
LATE = GATE + 31 * DAY + 10 * HOUR + 1800  # 30일 창이 게이트 뒤로 꽉 찬다(2026-11-11)
PLAIN = "Plain Org"
Places = dict[str, tuple[str, int | None, str]]


def place(
    i: int, cc: str, asn: int | None = 64_501
) -> tuple[str, tuple[str, int | None, str]]:
    return f"10.0.{i}.0", (cc, asn, PLAIN)


def visits(
    ip: str, shaped: int, confirmed: int = 0, *, tag: str = "", t0: float = T0
) -> list[str]:
    """그 IP 에서 서로 다른 UA 짝 `shaped` 개(`t0` 의 KST 하루) — 앞의 `confirmed` 개는 clarity.js 도 받았다(JS 신호).
    같은 `tag` 면 다른 날에도 같은 짝이다."""
    lines = []
    for i in range(shaped):
        ua = f"{CHROME} {tag}{ip}-{i}"
        lines.append(line(t0 + i, "/", ua=ua, ip=ip))
        if i < confirmed:
            lines.append(line(t0 + i + 0.5, "/clarity.js", ua=ua, ip=ip))
    return lines


def by_time(lines: list[str]) -> list[str]:
    return sorted(lines, key=lambda ln: json.loads(ln)["ts"])


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
    dbip = DbIp().serve(utc_month(now), {**PLACES, **(places or {})})
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
    assert (g["code"], g["month"], g["loadedAt"]) == (
        None,
        "2026-10",
        int(AFTER * 1000),
    )
    assert g["sinceTs"] == body["visitors"]["sinceTs"] == body["startTs"]
    assert (g["countries"], g["networks"], g["ipv6"]) == (
        [["DE", 0, 3]],
        [["telecom", 0, 3]],
        0,
    )


async def test_a_country_with_under_three_pairs_on_its_day_is_other(
    tmp_path: Path,
) -> None:
    # 판정은 모양(상한)의 하루 최대 하나 — confirmed 가 1·2 여도 그날 짝이 셋이면 이름으로 둔다
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
        ["IT", 1, 5],
        ["NL", 0, 4],
        ["FR", 0, 3],
        ["(기타)", 0, 2],  # JP
    ]


async def test_one_pair_every_day_for_30_days_is_other_but_three_on_one_day_is_named(
    tmp_path: Path,
) -> None:
    # 짝 열쇠가 날마다 바뀌어 같은 사람인지 알 수 없다 — 한 짝이 날마다 오면 합 30 이어도 하루 1 이라 (기타)(망 종류도)
    alone = [
        line(LATE - HOUR - d * DAY, "/", ua=f"{CHROME} alone", ip=DE_TELECOM)
        for d in range(30)
    ]
    # FR — 어느 하루 셋, 다른 열흘은 그 가운데 한 짝만
    crowd = visits("10.0.2.0", 3, t0=LATE - HOUR - 5 * DAY)
    crowd += [
        line(LATE - 2 * HOUR - d * DAY, "/", ua=f"{CHROME} 10.0.2.0-0", ip="10.0.2.0")
        for d in range(10, 20)
    ]
    body = await geo(
        tmp_path, by_time(alone + crowd), dict([place(2, "FR")]), now=LATE, window="30d"
    )
    g = body["geo"]
    assert (body["window"], g["month"], body["visitors"]["shaped"]) == (
        "30d",
        "2026-11",
        43,
    )
    assert g["countries"] == [["FR", 0, 13], ["(기타)", 0, 30]]
    assert g["networks"] == [["other", 0, 13], ["(기타)", 0, 30]]  # telecom 하루 1


async def test_a_24h_window_judges_yesterday_and_today_apart(tmp_path: Path) -> None:
    # 24시간 창은 KST 어제·오늘 둘 — 같은 둘이 어제·오늘 오면 합 4 여도 하루 2 라 (기타), 오늘 셋이면 이름
    lines = visits(DE_TELECOM, 2, t0=YESTERDAY) + visits(DE_TELECOM, 2)
    lines += visits("10.0.2.0", 3)
    g = (await geo(tmp_path, by_time(lines), dict([place(2, "FR")])))["geo"]
    assert g["countries"] == [["FR", 0, 3], ["(기타)", 0, 4]]


async def test_25_countries_keep_20_rows_and_sum_the_rest(tmp_path: Path) -> None:
    codes = [a + b for a in "ABCDE" for b in "ABCDE"]
    places = dict(place(i, cc) for i, cc in enumerate(codes))
    lines = [ln for i in range(25) for ln in visits(f"10.0.{i}.0", 30 - i)]
    g = (await geo(tmp_path, lines, places))["geo"]
    assert g["countries"][:20] == [[codes[i], 0, 30 - i] for i in range(20)]
    assert g["countries"][20:] == [["(기타)", 0, 10 + 9 + 8 + 7 + 6]]
    assert sum(r[2] for r in g["countries"]) == sum(range(6, 31))


@pytest.mark.parametrize(
    ("yesterday", "shaped", "confirmed", "countries", "networks"),
    [
        (
            0,
            2,
            0,
            [["FR", 0, 3], ["(기타)", 0, 2]],
            [["other", 0, 3], ["(기타)", 0, 2]],
        ),
        (
            2,
            2,
            0,
            [["FR", 0, 3], ["(기타)", 0, 4]],
            [["other", 0, 3], ["(기타)", 0, 4]],
        ),
        (
            0,
            3,
            1,
            [["FR", 0, 3], ["KR", 1, 3]],
            [["other", 0, 3], ["telecom_kr", 1, 3]],
        ),
        (
            0,
            3,
            0,
            [["FR", 0, 3], ["KR", 0, 3]],
            [["other", 0, 3], ["telecom_kr", 0, 3]],
        ),
    ],
)
async def test_telecom_kr_folds_into_telecom_when_kr_is_hidden(
    tmp_path: Path,
    yesterday: int,
    shaped: int,
    confirmed: int,
    countries: list,
    networks: list,
) -> None:
    # 어제 둘·오늘 둘(같은 짝)은 하루 2 라 KR 이 숨고 국내 통신사 칸도 통신사 칸에 든다 — 접은 통신사 칸도 하루 2 라 (기타)
    lines = visits(KR_TELECOM, yesterday, t0=YESTERDAY)
    lines += visits(KR_TELECOM, shaped, confirmed) + visits("10.0.2.0", 3)
    g = (await geo(tmp_path, by_time(lines), dict([place(2, "FR")])))["geo"]
    assert (g["countries"], g["networks"]) == (countries, networks)


US_CLOUD, US_PLAIN = place(7, "US", 16509), place(8, "US")  # cloud · other


async def test_a_network_kind_from_one_pair_every_day_for_30_days_is_other(
    tmp_path: Path,
) -> None:
    # 망 종류도 나라처럼 하루로 잰다 — cloud 짝 하나가 30일 내내 와도 하루 1 이라 (기타). 나라 US 는 다른 날 하루 넷이라 이름
    alone = [
        line(LATE - HOUR - d * DAY, "/", ua=f"{CHROME} alone", ip="10.0.7.0")
        for d in range(30)
    ]
    crowd = visits("10.0.8.0", 3, t0=LATE - HOUR - 5 * DAY)
    body = await geo(
        tmp_path,
        by_time(alone + crowd),
        dict([US_CLOUD, US_PLAIN]),
        now=LATE,
        window="30d",
    )
    g = body["geo"]
    assert g["countries"] == [["US", 0, 33]]
    assert g["networks"] == [["other", 0, 3], ["(기타)", 0, 30]]
    assert "cloud" not in [row[0] for row in g["networks"]]


async def test_a_network_kind_with_three_pairs_on_one_day_is_named(
    tmp_path: Path,
) -> None:
    # cloud — 어느 하루 서로 다른 셋, 다른 열흘은 그 가운데 한 짝만 → 이름·합 13. 같은 창에서 하루 하나뿐인 other 는 (기타)
    crowd = visits("10.0.7.0", 3, t0=LATE - HOUR - 5 * DAY)
    crowd += [
        line(LATE - 2 * HOUR - d * DAY, "/", ua=f"{CHROME} 10.0.7.0-0", ip="10.0.7.0")
        for d in range(10, 20)
    ]
    lone = visits("10.0.8.0", 1, t0=LATE - HOUR - 3 * DAY)
    body = await geo(
        tmp_path,
        by_time(crowd + lone),
        dict([US_CLOUD, US_PLAIN]),
        now=LATE,
        window="30d",
    )
    g = body["geo"]
    assert g["countries"] == [["US", 0, 14]]
    assert g["networks"] == [["cloud", 0, 13], ["(기타)", 0, 1]]


@pytest.mark.parametrize(
    ("kr_t0", "networks"),
    [
        # 오늘 telecom 2 + telecom_kr 1 = 하루 3 → 접은 telecom 이 이름
        (T0, [["telecom", 0, 3], ["(기타)", 0, 1]]),
        # telecom_kr 은 어제 1·telecom 은 오늘 2 — 창 합은 3 이어도 하루 최대 2 라 (기타)
        (YESTERDAY, [["(기타)", 0, 4]]),
    ],
    ids=["same_day", "split_days"],
)
async def test_folded_telecom_is_judged_by_its_day_sum_with_telecom_kr(
    tmp_path: Path, kr_t0: float, networks: list
) -> None:
    # KR 짝 하나라 KR 이 숨어 telecom_kr 이 telecom 에 접힌다 — 접은 칸은 그날 telecom + telecom_kr 의 합으로 잰다
    lines = visits(DE_TELECOM, 2) + visits(KR_TELECOM, 1, t0=kr_t0)
    lines += visits("10.0.7.0", 1)  # cloud 하나 — 하루 1 이라 (기타)
    g = (await geo(tmp_path, by_time(lines), dict([US_CLOUD])))["geo"]
    assert (g["countries"], g["networks"]) == ([["(기타)", 0, 4]], networks)


async def test_hidden_network_kinds_are_one_other_row_at_the_end(
    tmp_path: Path,
) -> None:
    # cloud 2(confirmed 1)·other 2(confirmed 2)·unknown 2 — 모두 하루 2 라 끝의 (기타) 한 행, 이름 행보다 커도 끝
    lines = visits(DE_TELECOM, 3) + visits("10.0.7.0", 2, 1)
    lines += visits("10.0.2.0", 2, 2) + visits("10.0.6.0", 2)  # FR other · 두 자료 밖
    places = dict([US_CLOUD, place(2, "FR")])
    g = (await geo(tmp_path, by_time(lines), places))["geo"]
    assert g["networks"] == [["telecom", 0, 3], ["(기타)", 3, 6]]
    assert [row[0] for row in g["networks"]].count("(기타)") == 1
    assert g["countries"] == [["DE", 0, 3], ["(기타)", 3, 6]]


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
