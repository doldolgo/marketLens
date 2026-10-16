"""화면 이동 기록 계약 — 052 와 같은 화면 이름·영역 꼴·몸통 상한, 공개·관리자 nginx, RedisBus 메서드 (스펙 073 §4 정적).

화면 이름·영역 id 꼴은 052 의 목록과 같아야 한다(replay.js 가 attention.js 와 같은 이름을 보낸다). 기능 간 import 금지라
두 기능 폴더가 따로 들고 있고, 여기서 둘을 묶는다. caddy 로그 제외·허용 목록 대조는 test_observability·test_deploy 가 본다.
"""

import inspect

from app.core.redis_bus import RedisBus
from app.features.attention import models as attention
from app.features.replay import models as replay
from tests.test_admin import _admin_route, _forward
from tests.test_deploy import API, DENY, _locations, _public_server, _route


def test_pages_tabs_and_area_pattern_are_the_attention_ones() -> None:
    assert replay.TABS == attention.TABS
    assert replay.PAGES == attention.PAGES
    assert set(replay.DEVICES) == set(attention.DEVICES)
    assert replay.AREA_ID.pattern == attention.AREA_ID.pattern
    assert replay.MAX_BODY_BYTES == attention.MAX_BODY_BYTES == 16 * 1024


def test_public_location_is_the_attention_beacon_shape() -> None:
    """POST 만(405)·16k·X-Client-IP·api 로 — /api/attention 과 지시어가 똑같다."""
    locations = _locations(_public_server())
    assert locations[("=", "/api/replay")] == locations[("=", "/api/attention")]
    assert _route("/api/replay") == ("=", "/api/replay")
    assert _route("/api/replay/") in DENY


def test_replay_script_location_revalidates_like_attention() -> None:
    locations = _locations(_public_server())
    assert locations[("=", "/replay.js")] == locations[("=", "/attention.js")]


def test_admin_feeds_are_three_exact_api_locations_and_closed_publicly() -> None:
    for name in ("live", "sessions", "session"):
        path = f"/svc/api/admin/replay/{name}"
        assert _admin_route(path) == ("=", path)
        assert _forward(path) == (API, f"/admin/replay/{name}")
        assert _route(f"/api/admin/replay/{name}") in DENY
    # 정확 일치라 비슷한 이름은 관리자 화면의 정적 파일 찾기로 떨어진다
    assert _admin_route("/svc/api/admin/replay/sessionx") == ("/",)


def test_redis_bus_replay_methods_are_async_with_these_parameters() -> None:
    expected = {
        "replay_load": ["sid", "day"],
        "replay_append": ["sid", "meta", "blob", "day", "ttl_sec", "cutoff_ms"],
        "replay_mark_cut": ["sid", "ttl_sec"],
        "replay_recent": ["min_ms", "limit"],
        "replay_ip": ["ip", "limit"],
        "replay_metas": ["sids"],
        "replay_batches": ["sid", "start", "count"],
    }
    for name, params in expected.items():
        method = getattr(RedisBus, name)
        assert inspect.iscoroutinefunction(method), name
        assert list(inspect.signature(method).parameters) == ["self", *params], name
