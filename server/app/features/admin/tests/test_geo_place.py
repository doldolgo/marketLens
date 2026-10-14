"""DB-IP ASN 조직 이름·AS 번호를 남기고 IP 하나를 그대로 찾는다 (스펙 069 §3.3 — 039 의 '조직 이름은 버린다' 를 바꿈)."""

import csv
import io

import pytest

from app.features.admin.geo_table import (
    BadData,
    GeoTable,
    locate,
    place,
    read_asn,
    read_country,
)
from app.features.admin.tests.geo_fakes import (
    FILL_ASN,
    FILL_ORG,
    asn_rows,
    country_rows,
    dotted,
    number,
)

# 한 /24 의 앞 절반은 KR, 뒤 절반은 JP — /24 로 자르면 늘 앞 절반 값이다
SPLIT = "10.9.8.0"
PLACES: dict[str, tuple[str, int | None, str]] = {
    "203.0.113.0": ("KR", 4766, "Korea Telecom"),
    # 같은 AS 번호의 뒤 구간 — 이름은 파일에서 처음 본 것(10.1.0.0 의 이름)
    "198.51.100.0": ("KR", 16509, "Amazon.com, Inc. (second name)"),
    "192.0.2.0": ("DE", None, ""),  # ASN 자료 밖
    "10.1.0.0": ("US", 16509, "Amazon.com, Inc."),
}


def rows(data: list[list[object]]) -> list[list[str]]:
    text = io.StringIO()
    csv.writer(text, lineterminator="\n").writerows(data)
    return list(csv.reader(io.StringIO(text.getvalue())))


def table() -> GeoTable:
    country = country_rows(PLACES)
    half = number(SPLIT)
    country += [
        [SPLIT, dotted(half + 127), "KR"],
        [dotted(half + 128), dotted(half + 255), "JP"],
    ]
    ipv4 = sorted(
        (r for r in country if ":" not in str(r[0])), key=lambda r: number(r[0])
    )
    country = ipv4 + [r for r in country if ":" in str(r[0])]
    return GeoTable(
        "2026-10", 0, 1, read_country(rows(country)), read_asn(rows(asn_rows(PLACES)))
    )


def test_a_full_ip_finds_its_country_kind_as_number_and_org_name() -> None:
    t = table()
    assert place(t, "203.0.113.77") == ("KR", "telecom_kr", 4766, "Korea Telecom")
    assert place(t, "198.51.100.200") == ("KR", "cloud", 16509, "Amazon.com, Inc.")
    # 채움 구간 — 따옴표·쉼표가 든 이름도 글자 그대로
    assert place(t, "100.0.0.9") == ("US", "other", FILL_ASN, FILL_ORG)


def test_one_as_number_keeps_the_first_name_for_every_range() -> None:
    t = table()
    assert place(t, "198.51.100.5").org == "Amazon.com, Inc."
    assert t.asn.org_names.count("Amazon.com, Inc.") == 1
    assert len(t.asn.org_names) == len(set(t.asn.org_asns))


def test_outside_the_asn_ranges_the_org_is_none_and_the_kind_unknown() -> None:
    assert place(table(), "192.0.2.10") == ("DE", "unknown", None, None)


def test_the_exact_address_is_used_not_its_24() -> None:
    t = table()
    assert place(t, "10.9.8.200").country == "JP"
    assert locate(t, "10.9.8.200")[0] == "KR"  # 067 대역 규칙은 그대로 — /24 의 첫 주소


def test_ipv6_and_unparsable_text_keep_the_039_answers() -> None:
    t = table()
    assert place(t, "2001:db8::1") == (None, "ipv6", None, None)
    assert place(t, "not-an-ip") == ("(기타)", "unknown", None, None)


def test_as_numbers_above_four_bytes_are_bad_data() -> None:
    data = asn_rows(PLACES)
    data[0][2] = 2**32
    with pytest.raises(BadData):
        read_asn(rows(data))
