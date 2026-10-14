"""봇 판정 — 근거마다 경계값·점수 → 판정 네 갈래·unknown·operator·가린 행 글 (스펙 069 §3.4·§4 '판정')."""

from typing import Any

import pytest

from app.features.admin.access_classes import CLASSES
from app.features.admin.access_verdict import Signals, judge

IP = "203.0.113.7"


def signals(**kw: Any) -> Signals:
    kinds = dict.fromkeys(CLASSES, 0)
    kinds.update(kw.pop("kinds", {}))
    base: dict[str, Any] = {
        "requests": 10,
        "pages": 0,
        "js": 0,
        "ws": 0,
        "kinds": kinds,
        "probes": 0,
        "e404": 0,
        "assets": 0,
        "robots": 0,
        "search": 0,
        "peak": 1,
        "ua_bot": 0,
        "agents": 1,
        "net": "telecom_kr",
        "names": (),
        "probe_paths": (),
        "org": None,
    }
    base.update(kw)
    return Signals(**base)


def codes(s: Signals, **kw: Any) -> list[str]:
    return [r["code"] for r in judge(s, ip=IP, masked=False, **kw)["reasons"]]


@pytest.mark.parametrize(
    ("kw", "code", "hit"),
    [
        ({"pages": 3}, "no_js", True),
        ({"pages": 2}, "no_js", False),
        ({"pages": 3, "js": 1}, "no_js", False),
        ({"peak": 60}, "burst", True),
        ({"peak": 59}, "burst", False),
        ({"e404": 10, "requests": 33}, "many_404", True),
        ({"e404": 10, "requests": 34}, "many_404", False),  # 30% 아래
        ({"e404": 9, "requests": 10}, "many_404", False),
        ({"pages": 3}, "no_assets", True),
        ({"pages": 3, "assets": 1}, "no_assets", False),
        ({"pages": 2}, "no_assets", False),
        ({"agents": 5}, "ua_many", True),
        ({"agents": 4}, "ua_many", False),
        ({"robots": 1}, "robots", True),
        ({"probes": 1}, "probe", True),
        ({"net": "cloud"}, "cloud", True),
        ({"net": "telecom"}, "cloud", False),
        ({"ua_bot": 1}, "ua_bot", True),
        ({"kinds": {"tool": 1}}, "ua_tool", True),
        ({"kinds": {"scanner": 2}}, "ua_tool", True),
        ({"kinds": {"search": 2}}, "ua_tool", False),
        ({"js": 1}, "js", True),
        ({"ws": 1}, "ws", True),
        ({"assets": 1}, "assets", True),
        ({"search": 1}, "search_ref", True),
    ],
)
def test_each_reason_fires_at_its_boundary(
    kw: dict[str, Any], code: str, hit: bool
) -> None:
    assert (code in codes(signals(**kw))) is hit


def test_weights_add_up_and_the_score_picks_one_of_three_verdicts() -> None:
    cases = [
        ({"probes": 1}, 60, "bot"),  # 60
        ({"probes": 1, "robots": 1, "assets": 1}, 55, "suspect"),  # 60 + 10 − 15
        ({"net": "cloud", "robots": 1}, 30, "suspect"),  # 20 + 10
        (
            {"ua_bot": 1, "js": 1, "assets": 1, "agents": 5},
            25,
            "human",
        ),  # 70 − 40 − 15 + 10
        ({"pages": 5, "js": 2, "assets": 4, "ws": 1}, -85, "human"),  # −40 − 30 − 15
        ({}, 0, "human"),
    ]
    for kw, score, verdict in cases:
        out = judge(signals(**kw), ip=IP, masked=False)
        assert (out["score"], out["verdict"]) == (score, verdict), kw
        assert out["score"] == sum(r["weight"] for r in out["reasons"])


def test_fewer_than_three_requests_or_no_ip_is_unknown_with_reasons() -> None:
    few = judge(signals(requests=2, probes=2), ip=IP, masked=False)
    assert (few["verdict"], few["score"]) == ("unknown", 60)
    assert [r["code"] for r in few["reasons"]] == ["probe"]
    assert judge(signals(requests=3, probes=1), ip=IP, masked=False)["verdict"] == "bot"
    assert judge(signals(probes=5), ip="(없음)", masked=False)["verdict"] == "unknown"


def test_operator_rows_skip_the_score() -> None:
    out = judge(signals(probes=4, kinds={"operator": 1}), ip=IP, masked=False)
    assert out == {"score": None, "verdict": "operator", "reasons": []}


def test_texts_carry_the_numbers_and_masked_rows_end_with_band() -> None:
    s = signals(
        ua_bot=12,
        names=("Googlebot", "bingbot", "AhrefsBot"),
        probes=12,
        probe_paths=("/.env", "/wp-login.php"),
        e404=20,
        requests=40,
        net="cloud",
        org="Amazon.com, Inc.",
    )
    texts = {
        r["code"]: r["text"]
        for r in judge(s, ip="203.0.113.0/24", masked=True)["reasons"]
    }
    assert texts["ua_bot"] == "봇 이름을 밝힌 요청 12번(Googlebot·bingbot…) (대역)"
    assert texts["probe"] == "탐색 경로 12번(/.env·/wp-login.php) (대역)"
    assert texts["many_404"] == "404 20번(요청의 50%) (대역)"
    assert texts["cloud"] == "데이터센터 망(Amazon.com, Inc.) (대역)"
    assert all(r["weight"] for r in judge(s, ip=IP, masked=False)["reasons"])


def test_a_failed_crawler_check_adds_thirty() -> None:
    out = judge(
        signals(ua_bot=3, kinds={"search": 3}), ip=IP, masked=False, fake_crawler=True
    )
    assert out["reasons"][-1] == {
        "code": "fake_crawler",
        "text": "검색 로봇 이름을 쓰지만 주소 확인 실패",
        "weight": 30,
    }
    assert out["score"] == 100
