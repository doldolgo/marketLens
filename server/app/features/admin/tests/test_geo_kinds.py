"""망 종류 — 표 ASN 스물셋·조직 이름 낱말·맞추기 규칙·국내 통신사·ASN 자료 밖 (스펙 039 §3.4·§4 '망 종류')."""

import pytest

from app.features.admin.geo_fetch import GeoLoader
from app.features.admin.geo_kinds import network_kind
from app.features.admin.geo_table import locate
from app.features.admin.tests.access_fakes import AFTER
from app.features.admin.tests.geo_fakes import (
    DE_TELECOM,
    KR_CLOUD,
    KR_TELECOM,
    MONTH,
    PLACES,
    DbIp,
    inline,
)

PLAIN = "Plain Org"  # 어느 낱말에도 걸리지 않는 이름
NOT_LISTED = 64_500

TABLE = {
    **dict.fromkeys(
        (16509, 14618, 15169, 396982, 8075, 13335, 14061, 16276, 24940, 63949, 20473)
        + (31898, 45102, 132203, 51167, 12876, 60781, 9009),
        "cloud",
    ),
    **dict.fromkeys((4766, 9318, 9644, 3786, 17858), "telecom"),
}


def test_the_23_listed_asns_have_their_kind_whatever_the_name() -> None:
    assert len(TABLE) == 23
    for asn, kind in TABLE.items():
        assert network_kind(asn, PLAIN) == kind, asn
    # 표가 먼저 — 이름 낱말보다 앞선다
    assert network_kind(4766, "Example Hosting") == "telecom"
    assert network_kind(16509, "Example Telecom") == "cloud"


@pytest.mark.parametrize(
    ("org", "kind"),
    [
        ("Example Hosting LLC", "cloud"),
        ("Foo Datacenters", "cloud"),
        ("Data Center Bar", "cloud"),
        ("Acme Servers", "cloud"),
        ("Cloudy Skies Inc", "cloud"),
        ("Colo Fabric", "cloud"),
        ("Big-IDC Ltd", "cloud"),
        ("Dedicated Boxes", "cloud"),
        ("Deutsche Telekom AG", "telecom"),
        ("X Telecommunications", "telecom"),
        ("Y Broadband", "telecom"),
        ("Z Communications Inc", "telecom"),
        ("T-Mobile US", "telecom"),
        ("Rural Wireless Co", "telecom"),
        ("Cablevision Systems", "telecom"),
        ("Small ISP", "telecom"),
        ("Acme Telecom Hosting", "cloud"),  # cloud 낱말이 먼저
        ("Colombia Movil", "other"),
        ("Dispatch Inc", "other"),
        ("Colorado State University", "other"),
        ("VPSX Ltd", "other"),
        ("Bigdata Centers", "other"),  # data 는 낱말 전체가 같아야
        ("Metadata Corp", "other"),
        ("", "other"),
    ],
)
def test_name_words_decide_the_kind_of_unlisted_asns(org: str, kind: str) -> None:
    assert network_kind(NOT_LISTED, org) == kind


def test_lookups_add_telecom_kr_and_unknown() -> None:
    dbip = DbIp().serve(MONTH, {**PLACES, "10.3.3.0": ("FR", None, "")})
    loader = GeoLoader(
        clock=lambda: AFTER,
        warn=lambda name, code: None,
        transport=dbip.transport,
        start=inline,
    )
    loader.ensure()
    table = loader.table
    assert table is not None
    assert locate(table, KR_TELECOM) == ("KR", "telecom_kr")
    assert locate(table, KR_CLOUD) == ("KR", "cloud")
    assert locate(table, DE_TELECOM) == ("DE", "telecom")
    assert locate(table, "100.0.7.0") == ("US", "other")  # 채움 — 따옴표·쉼표 든 이름
    assert locate(table, "10.3.3.0") == ("FR", "unknown")  # ASN 자료 밖
    assert locate(table, "10.1.2.0") == ("(기타)", "unknown")  # 두 자료 밖
