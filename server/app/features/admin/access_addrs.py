"""IP 하나 단위 접속 — 파일 하나의 IP 칸 (스펙 069 §3.2).

069 배포(caddy reload) 뒤 줄은 IP 전체, 앞 줄은 027 이 가린 IP(IPv4 /24 → 끝이 `.0`, IPv6 /48 → 뒤 80비트가 0)다. 끝 옥텟이
0 이 아닌 IPv4 는 가린 줄일 수 없어 늘 전체 줄이고, 파일에서 처음 본 그런 줄의 시각이 그 파일의 `since` 다. 가린 꼴과 같은
글자(`.0` IPv4·뒤가 0 인 IPv6)는 그 파일의 `since` 뒤 줄이면 전체 줄, 아니면 '앞 줄' 로 따로 센다 — 창을 만들 때 캐시 전체의
`since` 로 다시 가른다(행의 첫 줄이 그 뒤면 전체 IP, 아니면 가린 대역 — `access_addrs_page.py`).
파일마다 (IP 글자, 앞 줄) → 시 칸(수 스물둘·1분 최대·처음·마지막 줄 시각)·경로 상위 10·브라우저 정보 상위 5(나머지는 `(기타)`)·
서로 다른 UA 해시(16까지)·봇 이름(3까지)·검색 로봇 주장. 067 대역 칸과 같은 방식이다(파일 단위 상위 자르기·파일당 5만 행).
UA 원문·짝 열쇠는 남기지 않는다 — UA 는 이 프로세스에서만 뜻이 있는 해시(`hash`)로만 센다.
"""

import heapq
import ipaddress
import re
from socket import AF_INET, AF_INET6, inet_ntop, inet_pton
from typing import Any

from app.features.admin.access_classes import CLASSES, SELF_HOSTS, UA_KINDS
from app.features.admin.access_traits import channel

NO_IP = "(없음)"
OTHER = "(기타)"
ROW_CAP = 50_000  # 파일 하나의 행 — 넘는 새 행은 (기타) (067 과 같다)
TOP_PATHS = 10
TOP_AGENTS = 5
UA_CAP = 16  # 행마다 서로 다른 UA 해시 — ua_many(5) 를 보기에 넉넉하다
NAME_CAP = 3
NAME_LIMIT = 40
PATH_LIMIT = 100
MEMO_CAP = 4_096
# 시 칸 — 수 스물둘(더하기)과 1분 최대·처음·마지막 줄 시각
REQ, PAGES, JS, WS, KIND, STATUS = 0, 1, 2, 3, 4, 12
PROBE, E404, ASSETS, ROBOTS, SEARCH, UABOT = 16, 17, 18, 19, 20, 21
SUMS = 22
PEAK, FIRST, LAST = 22, 23, 24
KIND_NO = {k: KIND + i for i, k in enumerate(CLASSES)}
NAMED_KINDS = ("search", "ai", "preview")  # ua_bot 의 038 종류
# 정적 자원 — 빌드 산출물·랜딩 조각·글꼴·아이콘 (GET 200·304)
ASSET_DIRS = ("/assets/", "/app/assets/", "/landing/")
ASSET_TAILS = (".woff2", ".woff", ".ttf", ".otf", ".ico")
ICONS = frozenset(
    ("/favicon.svg", "/apple-touch-icon.png", "/icon-192.png", "/logo-512.png")
)
ROBOTS_PATH = "/robots.txt"
# 낱말 찾기는 앞뒤를 넓히는 꼴(`[…]*bot[…]*`)로 쓰지 않는다 — 긴 영숫자 UA 에서 되짚기가 길이의 제곱으로 는다
BOT_WORD = re.compile("bot|crawler|spider")
NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")
# 주소 확인을 하는 검색 로봇 — 038 종류 search 의 UA 낱말 → 이름(§3.4)
CRAWLERS = (
    ("googlebot", "google"),
    ("bingbot", "bing"),
    ("yeti", "naver"),
    ("daumoa", "daum"),
)
_NAMED_WORDS = tuple(
    w for name, words in UA_KINDS if name in NAMED_KINDS for w in words
)


def classify(raw: str) -> tuple[str, bool, bool]:
    """IP 글자 → (정규 글자 또는 `(없음)`·`(기타)`, 가린 꼴과 같은가, 끝 옥텟이 0 이 아닌 IPv4 인가)."""
    if not raw:
        return NO_IP, False, False
    try:
        packed = inet_pton(AF_INET, raw)
        return inet_ntop(AF_INET, packed), packed[3] == 0, packed[3] != 0
    except (OSError, ValueError):
        pass
    try:
        packed = inet_pton(AF_INET6, raw)
    except (OSError, ValueError):
        return OTHER, False, False
    return str(ipaddress.IPv6Address(packed)), packed[6:] == bytes(10), False


def is_asset(path: str) -> bool:
    return path.startswith(ASSET_DIRS) or path.endswith(ASSET_TAILS) or path in ICONS


def bot_name(judged: str, ua_kind: str) -> str | None:
    """봇 이름을 밝힌 UA 면 그 이름 — UA 의 bot·crawler·spider 낱말, 없으면 038 표의 낱말(search·ai·preview)."""
    low = judged.lower()
    found = BOT_WORD.search(low)
    if found is not None:
        # 낱말을 품은 이름 조각 — 앞뒤로 이름 글자를 NAME_LIMIT 까지만 넓힌다(원래 대소문자로 돌려준다)
        start, end = found.span()
        while start > 0 and end - start < NAME_LIMIT and low[start - 1] in NAME_CHARS:
            start -= 1
        while end < len(low) and end - start < NAME_LIMIT and low[end] in NAME_CHARS:
            end += 1
        return judged[start:end]
    if ua_kind in NAMED_KINDS:
        return next((w for w in _NAMED_WORDS if w in low), ua_kind)
    return None


def claims(judged: str, ua_kind: str) -> tuple[str, ...]:
    """주소 확인을 하는 검색 로봇 이름 — 038 종류 search 일 때만(DaumApps 같은 인앱 브라우저는 아니다)."""
    if ua_kind != "search":
        return ()
    low = judged.lower()
    return tuple(name for word, name in CRAWLERS if word in low)


class Addr:
    """파일 하나에서 행 하나."""

    __slots__ = (
        "hours",
        "paths",
        "agents",
        "uas",
        "names",
        "claims",
        "first",
        "minute",
        "count",
    )

    def __init__(self, ts: float) -> None:
        self.hours: dict[int, list[Any]] = {}
        self.paths: dict[str, int] = {}
        self.agents: dict[str, int] = {}
        self.uas: set[int] = set()
        self.names: dict[str, int] = {}
        self.claims: set[str] = set()
        self.first = ts  # 파일 안 첫 줄 — 앞 줄 행을 캐시 전체의 since 로 가를 때
        self.minute = -1
        self.count = 0


class AddrTally:
    """파일 하나의 IP 칸 — `FileTally` 가 줄마다 `add` 하고, 다 읽으면(깨진 파일도) `finish` 가 메모를 버린다."""

    __slots__ = ("rows", "since", "_ips", "_uas", "_refs", "_paths")

    def __init__(self) -> None:
        self.rows: dict[tuple[str, bool], Addr] = {}
        self.since: float | None = None
        self._ips: dict[str, tuple[str, bool, bool]] = {}
        self._uas: dict[str, tuple[str | None, tuple[str, ...]]] = {}
        self._refs: dict[str, bool] = {}
        self._paths: dict[str, str] = {}

    def full(self, ts: float, ip: str) -> tuple[str, bool]:
        """(정규 글자, 전체 줄인가) — 끝 옥텟이 0 이 아닌 IPv4 는 `since` 를 당긴다."""
        found = self._ips.get(ip)
        if found is None:
            found = classify(ip)
            if len(self._ips) < MEMO_CAP:
                self._ips[ip] = found
        text, masked_shape, v4_full = found
        if v4_full and (self.since is None or ts < self.since):
            self.since = ts
        return text, not masked_shape or (self.since is not None and ts >= self.since)

    def add(
        self,
        ts: float,
        hour: int,
        ip: str,
        line: tuple[str, int, str, str, str, str | None, str],
        flags: tuple[bool, bool, bool, bool, bool, bool],
    ) -> None:
        """줄 하나 — `line` = (경로(쿼리 뗌), 상태, 038 종류, UA 로만 정한 종류, UA 앞 1,024자, 출처 호스트, 브라우저 정보 이름),
        `flags` = (사람 모양 페이지, JS 신호, WS 101, WS 경로, 탐색 경로, GET 200·304)."""
        path, status, kind, ua_kind, judged, host, agent = line
        page, js, ws, ws_path, probe, fetched = flags
        text, full = self.full(ts, ip)
        key = (text, not full)
        row = self.rows.get(key)
        if row is None:
            if len(self.rows) >= ROW_CAP:
                key = (OTHER, False)
                row = self.rows.get(key)
            if row is None:
                row = self.rows[key] = Addr(ts)
        rec = row.hours.get(hour)
        if rec is None:
            rec = row.hours[hour] = [0] * SUMS + [0, ts, ts]
        elif ts < rec[FIRST]:
            rec[FIRST] = ts
        elif ts > rec[LAST]:
            rec[LAST] = ts
        if ts < row.first:
            row.first = ts
        rec[REQ] += 1
        rec[KIND_NO[kind]] += 1
        if 200 <= status < 600 and not (status >= 500 and ws_path):
            rec[STATUS + status // 100 - 2] += 1
        rec[PAGES] += page
        rec[JS] += js
        rec[WS] += ws
        rec[PROBE] += probe
        rec[E404] += status == 404
        rec[ROBOTS] += path == ROBOTS_PATH
        if fetched and is_asset(path):
            rec[ASSETS] += 1
        if page and host is not None and self._search(host):
            rec[SEARCH] += 1
        # 1분 최대 — 줄은 대개 시각 순이라 행마다 지금 분의 수만 든다
        minute = int(ts) // 60
        row.count = row.count + 1 if minute == row.minute else 1
        row.minute = minute
        if row.count > rec[PEAK]:
            rec[PEAK] = row.count
        name, said = self._ua(judged, ua_kind)
        if name is not None:
            rec[UABOT] += 1
            if name in row.names or len(row.names) < NAME_CAP:
                row.names[name] = row.names.get(name, 0) + 1
        row.claims.update(said)
        if len(row.uas) < UA_CAP:
            row.uas.add(hash(judged))
        short = path[:PATH_LIMIT]
        paths = row.paths
        if short in paths:
            paths[short] += 1
        else:
            paths[self._paths.setdefault(short, short)] = 1
        row.agents[agent] = row.agents.get(agent, 0) + 1

    def _ua(self, judged: str, ua_kind: str) -> tuple[str | None, tuple[str, ...]]:
        found = self._uas.get(judged)
        if found is None:
            found = bot_name(judged, ua_kind), claims(judged, ua_kind)
            if len(self._uas) < MEMO_CAP:
                self._uas[judged] = found
        return found

    def _search(self, host: str) -> bool:
        found = self._refs.get(host)
        if found is None:
            found = host not in SELF_HOSTS and channel("", host, None) == "search"
            if len(self._refs) < MEMO_CAP:
                self._refs[host] = found
        return found

    def finish(self) -> None:
        """다 읽음 — 행마다 경로·브라우저 정보를 상위만 남기고 읽는 동안의 메모를 버린다."""
        for row in self.rows.values():
            if len(row.paths) > TOP_PATHS:
                row.paths = fold(row.paths, TOP_PATHS)
            if len(row.agents) > TOP_AGENTS:
                row.agents = fold(row.agents, TOP_AGENTS)
        self._ips.clear()
        self._uas.clear()
        self._refs.clear()
        self._paths.clear()


def fold(counts: dict[str, int], n: int) -> dict[str, int]:
    """수 내림차순(같으면 이름순) n 개와 나머지를 합친 `(기타)`."""
    best = heapq.nsmallest(n, counts.items(), key=lambda kv: (-kv[1], kv[0]))
    kept = dict(best)
    kept[OTHER] = kept.get(OTHER, 0) + sum(counts.values()) - sum(kept.values())
    return kept
