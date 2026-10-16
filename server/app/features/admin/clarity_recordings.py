"""Clarity 녹화 목록 — 부르기 하나·값 다듬기 (스펙 077 §3.1·§3.2).

바깥 계약: 공식 Clarity MCP 서버(`microsoft/clarity-mcp-server`)가 쓰는 `POST https://clarity.microsoft.com/mcp/recordings/sample`
(Microsoft Learn 문서에는 없다 — 2026-10-16 운영 토큰으로 응답 모양을 확인). 인증은 040 과 같은 Data Export 토큰.
응답은 녹화 목록이고, 녹화마다 재생 링크·시작 시각·길이 글자·페이지·클릭 수·페이지별 시간선을 준다. 문서가 없는 경로라
모르는 키는 버리고 틀린 값은 null 로 둔다. 주소는 040 의 주소 줄이기(쿼리·해시 뗌, 우리 대시보드는 `?tab=<id>` 만)를 거치고,
재생 링크는 Clarity 출처·허용 글자만일 때만 싣는다 — 피드 글자가 그대로 관리자 화면의 주소가 되지 않게.
토큰은 헤더에만 싣는다 — 로그·응답·예외 code 어디에도 없다. 오류 문장은 버린다. 리다이렉트는 따르지 않는다(httpx 기본).
"""

import asyncio
import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import USER_AGENT
from app.features.admin.clarity import check_json, reduce_value, utf8

RECORDINGS_URL = "https://clarity.microsoft.com/mcp/recordings/sample"
TIMEOUT_SEC = 10.0  # 재시도 없음
COUNT = 100  # 한 번에 받는 녹화 — 경로의 상한은 250
REFRESH_SEC = 1800  # 같은 창은 30분에 한 번까지 — 하루 한도가 문서에 없다
DAYS = (1, 3, 7, 30)
DEFAULT_DAYS = 3
KEYS = ("rows",)
MAX_PAGES = 30
MAX_EVENTS = 50
TITLE_LIMIT = 100
TEXT_LIMIT = 60
TYPE_LIMIT = 30
LINK_LIMIT = 500
# 재생 링크 — Clarity 출처, 경로는 영문·숫자·`/_.~%-`, 쿼리는 영문·숫자·`=&_.~%:-` 만
_LINK = re.compile(
    r"https://clarity\.microsoft\.com/[A-Za-z0-9/_.~%-]*(?:\?[A-Za-z0-9=&_.~%:-]*)?"
)
_PART = re.compile(r"(\d+)\s*(hour|minute|second)s?", re.IGNORECASE)
_CLOCK = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})")
_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def days_of(value: str | None) -> int:
    """`days` 인자 → 1·3·7·30, 목록 밖·없음은 3."""
    try:
        n = int(value or "")
    except ValueError:
        return DEFAULT_DAYS
    return n if n in DAYS else DEFAULT_DAYS


def request_body(now_ms: int, days: int) -> dict[str, Any]:
    """창(지금 − days … 지금, UTC ISO ms)·최근 순·100개."""
    end = datetime.fromtimestamp(now_ms / 1000, UTC)
    start = end - timedelta(days=days)
    iso = [
        t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
        for t in (start, end)
    ]
    return {
        "sortBy": 0,
        "start": iso[0],
        "end": iso[1],
        "filters": {"date": {"start": iso[0], "end": iso[1]}},
        "count": COUNT,
    }


def seconds_of(value: Any) -> int | None:
    """`52 seconds`·`1 minute 3 seconds`·`2 hours 5 minutes` → 초. 모르는 꼴·빈 글자는 None."""
    if not isinstance(value, str):
        return None
    parts = _PART.findall(value)
    rest = _PART.sub("", value).strip(" ,")
    if not parts or rest:
        return None
    unit = {"hour": 3600, "minute": 60, "second": 1}
    return sum(int(n) * unit[u.lower()] for n, u in parts)


def clock_of(value: Any) -> int | None:
    """`mm:ss`·`h:mm:ss` → 초."""
    if not isinstance(value, str):
        return None
    m = _CLOCK.fullmatch(value.strip())
    if m is None:
        return None
    h, mm, ss = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return None if ss >= 60 else h * 3600 + mm * 60 + ss


def stamp_ms(value: Any) -> int | None:
    """`YYYY-MM-DD HH:MM:SS`(UTC) → epoch ms."""
    if not isinstance(value, str):
        return None
    m = _STAMP.fullmatch(value.strip())
    if m is None:
        return None
    try:
        t = datetime(*(int(g) for g in m.groups()), tzinfo=UTC)
    except ValueError:
        return None
    return int(t.timestamp() * 1000)


def link_of(value: Any) -> str | None:
    """재생 링크 — Clarity 출처와 허용 글자만, 500자 이하일 때만."""
    if not isinstance(value, str) or len(value) > LINK_LIMIT:
        return None
    return value if _LINK.fullmatch(value) else None


def whole(value: Any, low: int = 0, high: int = 10**9) -> int | None:
    """정수(불 아님)이고 범위 안이면 그대로."""
    return value if type(value) is int and low <= value <= high else None


def text_of(value: Any, limit: int) -> str | None:
    """글자 — 제어 문자를 공백으로, 공백은 하나로, `limit` 자에서 자른다. 빈 글자·글자 아님은 None."""
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        " " if unicodedata.category(c).startswith("C") else c for c in utf8(value)
    )
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit] or None


def address_of(value: Any, referrer: bool) -> str | None:
    """040 의 주소 줄이기 — 쿼리·해시 뗌, 우리 대시보드는 `?tab=<id>` 만, 출처면 출처만."""
    if not isinstance(value, str) or not value.strip():
        return None
    return text_of(reduce_value(value, referrer), 300)


def event_of(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return {
        "type": text_of(raw.get("eventtype"), TYPE_LIMIT),
        "text": text_of(raw.get("text"), TEXT_LIMIT),
        "atSec": clock_of(raw.get("start")),
    }


def page_of(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    events = raw.get("timelineEvents")
    return {
        "url": address_of(raw.get("url"), False),
        "title": text_of(raw.get("displayTitle"), TITLE_LIMIT),
        "atSec": clock_of(raw.get("start")),
        "stayMs": whole(raw.get("duration")),
        "loadMs": whole(raw.get("pageLoadTime")),
        "referrer": address_of(raw.get("referrerUrl"), True),
        "events": [
            e
            for e in (
                event_of(x)
                for x in (events if isinstance(events, list) else [])[:MAX_EVENTS]
            )
            if e
        ],
    }


def row_of(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    timeline = raw.get("timeline")
    return {
        "startedAt": stamp_ms(raw.get("timestamp")),
        "totalSec": seconds_of(raw.get("totalDuration")),
        "activeSec": seconds_of(raw.get("activeDuration")),
        "pages": whole(raw.get("pagesCount")),
        "clicks": whole(raw.get("sessionClickCount")),
        "link": link_of(raw.get("link")),
        "timeline": [
            p
            for p in (
                page_of(x)
                for x in (timeline if isinstance(timeline, list) else [])[:MAX_PAGES]
            )
            if p
        ],
    }


def parse(content: bytes) -> dict[str, Any]:
    """응답 → `{rows}`. 목록이 아니면 ValueError(`bad_data`). 행은 시작 최근 순 100개까지 — 시작을 모르는 행은 뒤로."""
    data = json.loads(content)
    # 감싼 꼴(`{"recordings":[…]}` 등)이 와도 목록 하나면 그것
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if len(lists) == 1 else None
    if not isinstance(data, list):
        raise ValueError("not a list")
    rows = [r for r in (row_of(x) for x in data) if r]
    rows.sort(key=lambda r: -(r["startedAt"] or 0))
    values = {"rows": rows[:COUNT]}
    check_json(values)
    return values


async def fetch(
    token: str, transport: httpx.AsyncBaseTransport | None, body: dict[str, Any]
) -> tuple[str, str | None, dict[str, Any] | None]:
    """한 번 부른다 → (state, code, 값). 401·403 은 denied, 그 밖 비 200·시간 초과는 error, 받은 자료가 틀리면 `bad_data`."""
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_SEC
        ) as client:
            resp = await asyncio.wait_for(
                client.post(
                    RECORDINGS_URL,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": USER_AGENT,
                    },
                ),
                TIMEOUT_SEC,
            )
    except (TimeoutError, httpx.TimeoutException):
        return "error", "timeout", None
    except Exception as exc:
        return "error", type(exc).__name__, None
    if resp.status_code in (401, 403):
        return "denied", f"http_{resp.status_code}", None
    if resp.status_code != 200:
        return "error", f"http_{resp.status_code}", None
    try:
        values = await asyncio.to_thread(parse, resp.content)
    except (ValueError, TypeError, RecursionError):
        return "error", "bad_data", None
    except Exception as exc:
        return "error", type(exc).__name__, None
    return "ok", None, values
