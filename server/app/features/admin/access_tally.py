"""접속 요약의 한 줄 세기 — 창·페이지 판정·상위 목록·탭·기기·브라우저·WS·5xx (스펙 035 §3.2).

파일 읽기는 `access.py` 가 하고 줄마다 `Tally.line` 을 부른다. 창 밖 줄은 JSON 을 풀기 전에 `ts` 만 보고 버리고,
집계 키(경로·출처·utm) 종류는 각 5,000까지라 메모리는 줄 수와 무관하다. IP·UA 원문·쿼리는 결과에 싣지 않는다.
"""

import heapq
import json
from collections import Counter
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlsplit

HOURS = 24
TOP = 20
KEY_CAP = 5_000
OTHER = "(기타)"
PATH_LIMIT = 100
REFERRER_LIMIT = 100
UTM_LIMIT = 50
RECENT_5XX = 20
WS_PATH = "/api/ws/spreads"
APP_PATH = "/app/"
# 대시보드 탭 id(002) — 키가 없으면 기본 탭
TABS = ("spread", "history", "gap", "pp", "health", "flow")
DEFAULT_TAB = "spread"
SELF_HOSTS = {"kimptrack.com", "www.kimptrack.com", "admin.kimptrack.com"}
# 대소문자 무관, 앞에서 맞은 것이 이긴다
BOT_WORDS = tuple(
    "bot crawl spider slurp curl wget python go-http headless preview".split()
)
MOBILE_WORDS = ("mobi", "android", "iphone", "ipad")
BROWSERS = (
    ("edge", ("edg/",)),
    ("samsung", ("samsungbrowser",)),
    ("firefox", ("firefox", "fxios")),
    ("chrome", ("chrome", "crios")),
    ("safari", ("safari",)),
)
WS_BUCKETS = (("lt10s", 10.0), ("lt1m", 60.0), ("lt10m", 600.0), ("lt1h", 3600.0))


class Line(NamedTuple):
    method: str
    uri: str
    status: int
    duration: float
    ua: str
    referer: str


def _line_ts(line: str) -> float | None:
    """JSON 을 풀지 않고 맨 앞 `"ts":` 의 수를 읽는다 — caddy 는 `ts` 를 두 번째 필드로 쓴다(문자열 안의 따옴표는 이스케이프된다)."""
    i = line.find('"ts":')
    if i < 0:
        return None
    end = line.find(",", i + 5)
    try:
        return float(line[i + 5 : end])
    except ValueError:
        return None


def _count(counter: Counter[str], key: str) -> None:
    """키 종류는 5,000까지 — 넘으면 `(기타)` 로 센다(봇의 무작위 경로가 메모리를 키우지 않게)."""
    if key in counter or len(counter) < KEY_CAP:
        counter[key] += 1
    else:
        counter[OTHER] += 1


def _device(ua: str) -> str:
    if not ua:
        return "unknown"
    low = ua.lower()
    if any(w in low for w in BOT_WORDS):
        return "bot"
    if any(w in low for w in MOBILE_WORDS):
        return "mobile"
    return "desktop"


def _browser(ua: str) -> str:
    low = ua.lower()
    for name, words in BROWSERS:
        if any(w in low for w in words):
            return name
    return "other"


def _top(counter: Counter[str]) -> list[list[Any]]:
    """수 내림차순(같으면 이름순) 20개 — `[[이름, 수], …]`."""
    best = heapq.nsmallest(TOP, counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [[name, n] for name, n in best]


class Tally:
    def __init__(self, start_ts: int) -> None:
        self.start = start_ts
        self.stop = start_ts + HOURS * 3600
        self.requests = self.pages = self.ws = self.skipped = 0
        self.first: float | None = None
        self.hourly = [[0, 0, 0] for _ in range(HOURS)]  # requests·pages·errors
        self.paths: Counter[str] = Counter()
        self.tabs: Counter[str] = Counter()
        self.referrers: Counter[str] = Counter()
        self.utm: Counter[str] = Counter()
        self.devices: Counter[str] = Counter()
        self.browsers: Counter[str] = Counter()
        self.status = dict.fromkeys(("2xx", "3xx", "4xx", "5xx"), 0)
        self.ws_durations = dict.fromkeys((*(k for k, _ in WS_BUCKETS), "ge1h"), 0)
        self.recent: list[tuple[float, int, str, int]] = []  # 최소 힙 — 가장 늦은 20줄
        self.seq = 0

    def line(self, line: str) -> None:
        if not line.strip():
            return
        ts = _line_ts(line)
        if ts is not None and not self.start <= ts < self.stop:
            return  # 창 밖 — JSON 을 풀지 않는다
        rec = None if ts is None else _parse(line)
        if ts is None or rec is None:
            self.skipped += 1  # JSON 이 아니거나 필드가 빠졌다
            return
        self.add(ts, rec)

    def add(self, ts: float, rec: Line) -> None:
        status = rec.status
        path, _, query = rec.uri.partition("?")
        hour = self.hourly[int(ts - self.start) // 3600]
        self.requests += 1
        hour[0] += 1
        if self.first is None or ts < self.first:
            self.first = ts
        if 200 <= status < 600:
            self.status[f"{status // 100}xx"] += 1
        if status >= 500:
            hour[2] += 1
            self.seq += 1
            item = (ts, self.seq, path[:PATH_LIMIT], status)
            if len(self.recent) < RECENT_5XX:
                heapq.heappush(self.recent, item)
            else:
                heapq.heappushpop(self.recent, item)
        if path == WS_PATH and status == 101:
            self.ws += 1
            bucket = next(
                (k for k, limit in WS_BUCKETS if rec.duration < limit), "ge1h"
            )
            self.ws_durations[bucket] += 1
        # 페이지 요청 = GET·200·마지막 조각에 점 없음 — 자산·`.php` 스캔·301 은 세지 않는다
        if rec.method != "GET" or status != 200 or "." in path.rsplit("/", 1)[-1]:
            return
        self.pages += 1
        hour[1] += 1
        _count(self.paths, path[:PATH_LIMIT])
        params = parse_qs(query, keep_blank_values=True) if query else {}
        if path == APP_PATH:
            tab = params["tab"][0] if "tab" in params else DEFAULT_TAB
            self.tabs[tab if tab in TABS else OTHER] += 1
        utm = params.get("utm_source", [""])[0]
        if utm:
            _count(self.utm, utm.lower()[:UTM_LIMIT])
        ref = _referrer(rec.referer)
        if ref is not None and ref[1] not in SELF_HOSTS:
            _count(self.referrers, ref[0][:REFERRER_LIMIT])
        device = _device(rec.ua)
        self.devices[device] += 1
        if device != "bot":
            self.browsers[_browser(rec.ua)] += 1

    def result(self, end_ts: int) -> dict[str, Any]:
        recent = sorted(self.recent, reverse=True)
        return {
            "startTs": self.start,
            "endTs": end_ts,
            "firstTs": None if self.first is None else int(self.first),
            "totals": {
                "requests": self.requests,
                "pages": self.pages,
                "ws": self.ws,
                "skipped": self.skipped,
            },
            "hourly": [
                {"ts": self.start + i * 3600, "requests": r, "pages": p, "errors": e}
                for i, (r, p, e) in enumerate(self.hourly)
            ],
            "paths": _top(self.paths),
            "tabs": _top(self.tabs),
            "referrers": _top(self.referrers),
            "utmSources": _top(self.utm),
            "devices": _top(self.devices),
            "browsers": _top(self.browsers),
            "status": dict(self.status),
            "recent5xx": [
                {"ts": int(ts), "path": path, "status": status}
                for ts, _, path, status in recent
            ],
            "ws": {"count": self.ws, "durations": dict(self.ws_durations)},
        }


def _parse(line: str) -> Line | None:
    """한 줄 → 쓰는 필드. JSON 이 아니거나 필드가 빠지면 None. ua·referer 는 없으면 빈 값."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    request = data.get("request")
    if not isinstance(request, dict):
        return None
    method, uri = request.get("method"), request.get("uri")
    status, duration = data.get("status"), data.get("duration")
    if not (isinstance(method, str) and isinstance(uri, str)):
        return None
    if type(status) is not int or type(duration) not in (int, float):
        return None
    ua, referer = data.get("ua"), data.get("referer")
    ua = ua if isinstance(ua, str) else ""
    return Line(
        method,
        uri,
        status,
        float(duration),
        ua,
        referer if isinstance(referer, str) else "",
    )


def _referrer(referer: str) -> tuple[str, str] | None:
    """referer → (출처 키 `스킴://호스트[:포트]`, 호스트) — 소문자·사용자 정보 없음·호스트 끝 점 뗌.
    027 caddy 가 출처만 남기지만 그 계약에 기대지 않는다 — 경로·쿼리(검색어·이메일)는 여기서도 버린다.
    빈 값·http(s) 밖·모양이 아니면 None(세지 않는다)."""
    if not referer:
        return None
    try:
        parts = urlsplit(referer.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    host = (host or "").rstrip(".")
    if parts.scheme not in ("http", "https") or not host:
        return None
    shown = f"[{host}]" if ":" in host else host  # IPv6
    return f"{parts.scheme}://{shown}{'' if port is None else f':{port}'}", host
