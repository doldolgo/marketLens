"""관리자 화면 v4 가 067·068 의 진짜 피드 코드 응답을 그리는가 (스펙 066 §4 보강 — 067·068 이 main 에 든 뒤의 계약 대조).

067 의 `VisitFeeds.ips`·`ip`(caddy 접속 줄 — `access_fakes`)와 068 의 `VisitFeeds.clarity`·`clarity_history`(Clarity 가짜
응답·fakeredis — `clarity_fakes`)가 만든 응답을 그대로 방문자·Clarity 페이지에 주고 진짜 ECharts 5.5.1(SSR)로 그린다 — 예외 0·
카드 '표시 오류' 0, 판은 그 값으로 찬다. 화면의 창은 브라우저의 지금 기준이라 두 피드의 시계를 지금에 맞춘다.
"""

import tempfile
import time
from pathlib import Path
from typing import Any

from app.features.admin.tests.access_fakes import Feeds, write
from app.features.admin.tests.clarity_fakes import NOW as CLARITY_NOW
from app.features.admin.tests.clarity_fakes import SESSIONS_EXPORT, Clarity, World
from tests.admin_dom import run_admin
from tests.test_admin_contract import PLAIN, SCENARIO, _lines

NO_FEEDS = {"feeds": PLAIN, "series": None, "access": None}


async def _real_bands() -> tuple[dict[str, Any], dict[str, Any]]:
    """067 — 두 시간 전부터의 줄(대역 셋)로 센 24시간 대역 목록과 대역마다 상세."""
    now = time.time()
    with tempfile.TemporaryDirectory() as directory:
        write(Path(directory), "access.log", _lines(now - 2 * 3600))
        feeds = Feeds(Path(directory), now)
        listing = await feeds.ips("24h", sort="requests", limit="200", offset="0")
        details = {
            b["band"]: await feeds.ip(b["band"], "24h") for b in listing["bands"]
        }
    return listing, details


async def _real_clarity() -> tuple[dict[str, Any], dict[str, Any]]:
    """068 — 사흘 전·이틀 전·지금 기본을 불러 날 칸 셋을 남긴 뒤의 `/admin/clarity`(dims 포함)와 history 셋(7·30·90)."""
    w = World(Clarity((200, SESSIONS_EXPORT)))  # 스크롤·머문 시간·신호가 찬 기본 응답
    base = time.time() - CLARITY_NOW - 3 * 86_400
    for hours in (0, 24, 72):
        w.t = base + hours * 3600
        await w.get()
    body = await w.get()
    return body, {str(d): await w.history(str(d)) for d in (7, 30, 90)}


async def test_real_band_feeds_draw_the_visitors_page_and_band_sheets() -> None:
    """067: 표에 대역 셋(요청 큰 순·같으면 글자순), 큰 수의 대역 수, 행을 누르면 그 대역의 판 — 시간별 요청 그래프·최근 요청 표."""
    listing, details = await _real_bands()
    bands = [b["band"] for b in listing["bands"]]
    assert (listing["state"], listing["total"]) == ("ok", 3)
    assert bands == ["203.0.113.0/24", "192.0.2.0/24", "198.51.100.0/24"]
    payload = {**NO_FEEDS, "pages": [["visitors.js", "?view=band", []]], "ips": listing}
    got = run_admin(SCENARIO, {**payload, "ip": details, "openBands": bands[:2]})
    page = got["visitors.js"]
    assert page["failed"] == [] and page["errors"] == [], page
    assert all(b in page["bands"] for b in bands) and page["texts"]["v-count"] == "3"
    first, second = page["sheets"][bands[0]], page["sheets"][bands[1]]
    assert first["title"] == bands[0] and first["chart"] and first["tag"] == ""
    assert "/api/history/events" in first["raw"] and "502" in first["raw"]
    assert first["count"] == "5 / 5행"  # 이 대역의 줄 다섯 — 최근 요청 표
    assert second["title"] == bands[1] and "/wp-login.php" in second["raw"]


async def test_real_clarity_feeds_draw_the_clarity_page_and_sheets() -> None:
    """068: 날별 기록 셋으로 큰 수의 작은 선·판의 날별 선이, `dims` 로 기기·브라우저·OS 띠와 유입 목록이 찬다('연결 안 됨' 없음)."""
    clarity, history = await _real_clarity()
    assert clarity["dims"]["state"] == "ok"
    assert len(history["30"]["rows"]) == 3
    details = ["sessions", "dims", "refs", "match"]
    payload = {**NO_FEEDS, "pages": [["insight.js", "", details]]}
    page = run_admin(SCENARIO, {**payload, "clarity": clarity, "history": history})[
        "insight.js"
    ]
    assert page["failed"] == [] and page["errors"] == [], page
    assert not {"t-dims", "t-refs", "t-sessions", "t-match"} & set(page["tags"]), page[
        "tags"
    ]
    assert {"c-sessions", "c-users", "c-match"} <= set(page["charts"])
    assert page["texts"]["v-sessions"] not in ("", "–")
    assert page["texts"]["v-scroll"].endswith("%"), page["texts"]
    sheets = page["sheets"]
    assert sheets["sessions"]["chart"] and sheets["sessions"]["count"] == "3 / 3행"
    first = history["30"]["rows"][0]["date"][5:]  # 첫 기록 날(KST) — 판 머리 글
    assert f"기록은 {first} 부터" in sheets["sessions"]["note"], sheets["sessions"]
    assert sheets["dims"]["count"] and sheets["refs"]["count"], sheets
    assert sheets["match"]["chart"], sheets["match"]
