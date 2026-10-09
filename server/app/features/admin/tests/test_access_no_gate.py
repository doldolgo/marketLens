"""게이트 없음 — 상수 그대로(`PRIVACY_V2_EFFECTIVE`)의 접속 요약은 남아 있는 기록 전체를 쓴다 (스펙 060 §3.1·§4).

사람 결정(2026-10-09): 접속 기록 쓰임새의 날짜 게이트를 없앤다. 상수는 038·039 가 읽으므로 두되 첫 접속 기록(2026-09-29)보다
앞선 날이라, 배포한 날부터 세 창이 열리고 7일·30일 시작이 잘리지 않으며 짝(날마다 센 방문자)·나라와 망 종류가 게이트 전
상태가 아니다. 게이트 동작 자체(앞뒤 시계)는 038·039 의 테스트가 주입한 날짜로 본다.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.core import config
from app.features.admin.tests.access_fakes import (
    CHROME,
    HOUR,
    IPHONE,
    Feeds,
    line,
    rotated,
    write,
)

KST = 9 * HOUR
# 접속 기록을 남기기 시작한 날(027 — caddy 접속 로그, 2026-09-29) 오전 10시 KST
FIRST_LOG = datetime(2026, 9, 29, 1, tzinfo=UTC).timestamp()
# 배포한 날(v4 — 2026-10-09) 정오 KST 와 그 뒤 — 30일 창이 첫 기록을 넘는 날
NOWS = (
    datetime(2026, 10, 9, 3, tzinfo=UTC).timestamp(),
    datetime(2026, 11, 20, 3, tzinfo=UTC).timestamp(),
)
WINDOW_HOURS = {"24h": 24, "7d": 168, "30d": 720}


def test_the_constant_is_before_the_first_log_line() -> None:
    gate = date.fromisoformat(config.PRIVACY_V2_EFFECTIVE)
    assert gate < datetime.fromtimestamp(FIRST_LOG + KST, UTC).date()


@pytest.mark.parametrize("now", NOWS)
@pytest.mark.parametrize("window", ["7d", "30d"])
async def test_windows_open_unclipped_with_pairs_and_geo_from_the_first_record(
    tmp_path: Path, now: float, window: str
) -> None:
    rotated(tmp_path, [line(FIRST_LOG, "/", ua=IPHONE)])
    write(tmp_path, "access.log", [line(now - 2 * HOUR, "/", ua=CHROME)])
    body = await Feeds(tmp_path, now, effective=config.PRIVACY_V2_EFFECTIVE).get(window)
    assert (body["state"], body["window"]) == ("ok", window)
    assert body["windows"] == ["24h", "7d", "30d"]
    end = int(now)
    natural = end - end % HOUR - (WINDOW_HOURS[window] - 1) * HOUR
    assert body["startTs"] == natural  # 시작 날짜로 자르지 않는다
    assert body["gateAt"] / 1000 < FIRST_LOG
    visitors = body["visitors"]
    assert visitors["state"] == "ok" and visitors["code"] is None
    assert visitors["sinceTs"] == body["startTs"]
    assert body["geo"]["code"] != "before_gate"
    # 창이 첫 기록을 품으면 그 줄도 센다 — 게이트로 빠지지 않는다
    inside = natural <= FIRST_LOG
    assert body["totals"]["requests"] == (2 if inside else 1)
    assert visitors["shaped"] == (2 if inside else 1)
