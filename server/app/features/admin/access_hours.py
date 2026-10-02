"""접속 로그 파일 하나의 세기 — 시(時) 버킷·최근 5xx 20줄·짝 기록 (스펙 038 §3.3~§3.5).

회전 파일은 압축이 끝나면 바뀌지 않아 파일마다 한 번 세어 메모리에 두고, 창은 시 버킷을 더해 만든다(`access_cache.py`).
읽기 시작점 앞 줄은 JSON 을 풀기 전에 `ts` 만 보고 버린다. 시마다 목록 키는 목록마다 30까지(넘는 새 키는 `(기타)`)라
메모리는 줄 수와 무관하다. 짝은 처리방침 v2 시행일(게이트) 뒤 줄에서만 만든다 — 게이트 앞 줄은 해시하지 않는다.
UA 원문·IP 는 세는 동안의 지역 변수에만 있고 남기지 않는다(UA·출처 판정 메모도 파일 읽기가 끝나면 — 깨진 파일로
예외가 나도 — 버린다. UA 메모의 키는 판정이 보는 앞 1,024자, 출처는 512자를 넘으면 메모하지 않는다).
"""

import heapq
import time
from collections.abc import Callable, Iterable
from itertools import islice
from typing import Any, NamedTuple
from urllib.parse import parse_qs

from app.features.admin import access_classes as lines
from app.features.admin import access_traits
from app.features.admin.access_classes import CLASSES, Line
from app.features.admin.access_pairs import (
    DAY_SEC,
    OPERATED,
    PAIR_CAP,
    PROBED,
    DayPairs,
    PairDay,
    PairKeys,
    kst_day,
    pair_hash,
    recorded,
)
from app.features.admin.access_traits import Traits

LIST_CAP = 30  # 시마다 목록마다 — 봇의 무작위 경로가 메모리를 키우지 않게
OTHER = "(기타)"
PATH_LIMIT = 100
REFERRER_LIMIT = 100
UTM_LIMIT = 50
RECENT_5XX = 20
YIELD_EVERY = 5_000  # 줄마다 — 공개 응답(같은 프로세스의 /api/landing·WS 허브)에 GIL 차례를 넘긴다
APP_PATH = "/app/"
TABS = ("spread", "history", "gap", "pp", "health", "flow")  # 대시보드 탭 id(002)
DEFAULT_TAB = "spread"
WS_BUCKETS = (("lt10s", 10.0), ("lt1m", 60.0), ("lt10m", 600.0), ("lt1h", 3600.0))
LISTS = ("paths", "tabs", "referrers", "utmSources", "devices", "browsers")
# 시 버킷의 수 칸 — 이름 = 응답 이름
COUNTS = (
    "requests pages humanPages jsViews probes ws skipped errors wsErrors 2xx 3xx 4xx 5xx ws5xx"
    " lt10s lt1m lt10m lt1h ge1h"
).split() + [f"{k}:{part}" for k in CLASSES for part in ("requests", "pages")]
AT = {name: i for i, name in enumerate(COUNTS)}
# 줄마다 쓰는 칸 번호 — 이름 찾기를 줄마다 하지 않는다
REQ, PAGES, HUMAN, JS, PROBES, WS, SKIP, ERR, WS_ERR, WS_5XX, GE_1H = (
    AT[k]
    for k in "requests pages humanPages jsViews probes ws skipped errors wsErrors ws5xx ge1h".split()
)
STATUS_AT = {n: AT[f"{n}xx"] for n in (2, 3, 4, 5)}
KIND_AT = {k: (AT[f"{k}:requests"], AT[f"{k}:pages"]) for k in CLASSES}
DURATION_AT = tuple((limit, AT[k]) for k, limit in WS_BUCKETS)
MEMO_CAP = (
    4_096  # 파일 하나를 읽는 동안의 UA·출처 판정 메모 — 다 읽으면(깨진 파일도) 버린다
)
# 출처 메모에 넣는 글자 길이 상한 — 긴 출처가 줄마다 달라도 메모가 파일 크기만큼 붙들지 않게(판정은 그대로).
# UA 메모의 키는 판정이 보는 앞 1,024자라 따로 막지 않는다(4,096 × 1,024자 ≈4MB 까지)
MEMO_KEY_MAX = 512
Ref = tuple[str, str] | None  # (출처 키, 호스트)
_NO_REF: tuple[None, bool] = (None, False)
# 어느 줄의 날과도 맞지 않는 빈 자리
_NO_DAY: tuple[int, DayPairs, bytes] = (-DAY_SEC, DayPairs(), b"")
_line_ts, _parse, _probe = lines.line_ts, lines.parse, lines.PROBE.search
UA_JUDGE = lines.UA_JUDGE
JS_PATHS, WS_PATH = lines.JS_PATHS, lines.WS_PATH


class Hour:
    """시 하나 — 수 칸·가장 이른 줄·사람 브라우저 모양 페이지 줄의 목록 여섯(처음 쓰일 때 만든다)."""

    __slots__ = ("counts", "first", "lists")

    def __init__(self) -> None:
        self.counts = [0] * len(COUNTS)
        self.first: float | None = None
        self.lists: list[dict[str, int]] | None = None


class Error5xx(NamedTuple):
    ts: float
    seq: int
    path: str
    status: int


class FileTally:
    """파일 하나의 세기. `read` 가 줄을 흘려 넣고, 결과는 창 조립이 읽기만 한다."""

    def __init__(self, read_start: int, gate_ts: int, keys: PairKeys) -> None:
        self.read_start = read_start
        self.gate_ts = gate_ts
        self.keys = keys
        self.hours: dict[int, Hour] = {}
        self.recent: list[Error5xx] = []  # 최소 힙 — 가장 늦은 20줄(WS 경로 뺌)
        self.days: dict[int, DayPairs] = {}  # KST 날 → 짝 기록(게이트 뒤만)
        # 읽기 시작점 뒤 가장 이른 줄(읽지 못한 줄 포함) — 시작점이 이 앞을 지나면 다시 만든다
        self.earliest: float | None = None
        self.no_ts = 0  # ts 없는 줄
        self.broken = 0  # 깨진 회전 파일이면 1
        self._seq = 0
        self._kinds: dict[str, str] = {}
        self._traits: dict[str, Traits] = {}
        self._refs: dict[str, tuple[Ref, bool]] = {}
        self._day = _NO_DAY  # 마지막 줄의 KST 날·짝 기록·열쇠

    def read(
        self, handle: Iterable[str], pause: Callable[[float], None] = time.sleep
    ) -> None:
        lines_in, line = iter(handle), self.line
        try:
            while True:
                n = 0
                for raw in islice(lines_in, YIELD_EVERY):
                    line(raw)
                    n += 1
                if n < YIELD_EVERY:
                    break
                # 0초 잠들기 — GIL 을 놓아 같은 프로세스의 이벤트 루프가 차례를 얻는다
                pause(0)
        finally:
            # 깨진 회전 파일(읽다 예외)도 이 결과째 캐시된다 — UA·출처 원문 메모와 열쇠는 늘 여기서 버린다
            self._kinds.clear()
            self._traits.clear()
            self._refs.clear()
            self._day = _NO_DAY

    def line(self, raw: str) -> None:
        ts = _line_ts(raw)
        if ts is None:
            if raw.strip():
                self.no_ts += 1
            return
        if ts < self.read_start:
            return  # 읽기 시작점 앞 — JSON 을 풀지 않는다
        if self.earliest is None or ts < self.earliest:
            self.earliest = ts
        rec = _parse(raw)
        its = int(ts)
        start = its - its % 3600
        hour = self.hours.get(start)
        if hour is None:
            hour = self.hours[start] = Hour()
        if rec is None:
            hour.counts[SKIP] += 1  # JSON 이 아니거나 필드가 빠졌다
            return
        self._add(ts, hour, rec)

    def _kind(self, judged: str) -> str:
        """`judged` = UA 앞 1,024자 — 판정·메모의 키."""
        kind = self._kinds.get(judged)
        if kind is None:
            kind = lines.ua_kind(judged)
            if len(self._kinds) < MEMO_CAP:
                self._kinds[judged] = kind
        return kind

    def _ref(self, referer: str) -> tuple[Ref, bool]:
        """출처 → ((출처 키, 호스트) | None, 운영자 흔적)."""
        found = self._refs.get(referer)
        if found is None:
            ref = lines.referrer(referer)
            found = ref, ref is not None and lines.is_operator(ref[1])
            if len(self._refs) < MEMO_CAP and len(referer) <= MEMO_KEY_MAX:
                self._refs[referer] = found
        return found

    def _trait(self, judged: str) -> Traits:
        found = self._traits.get(judged)
        if found is None:
            found = access_traits.traits(judged)
            if len(self._traits) < MEMO_CAP:
                self._traits[judged] = found
        return found

    def _add(self, ts: float, hour: Hour, rec: Line) -> None:
        # 줄마다 도는 길 — 판정을 함수로 나누지 않고 풀어 쓴다(50MiB 파일 하나 ≈95,000줄, §3.7). 페이지·JS 신호는 GET·200·304
        method, uri, status, duration, ua, referer, ip = rec
        c = hour.counts
        path, _, query = uri.partition("?")
        if hour.first is None or ts < hour.first:
            hour.first = ts
        ref, operator = self._ref(referer) if referer else _NO_REF
        probe = _probe(path.lower()) is not None
        # 판정은 앞 1,024자 — 짝 해시는 UA 전체(`_pair` 가 rec 에서)
        judged = ua[:UA_JUDGE]
        ua_kind = self._kind(judged)
        if operator:
            kind = "operator"
        elif probe and ua_kind == "browser":
            kind = "scanner"
        else:
            kind = ua_kind
        fetched = method == "GET" and (status == 200 or status == 304)
        page = fetched and "." not in path[path.rfind("/") + 1 :]
        js_view = fetched and path in JS_PATHS
        ws_path = path == WS_PATH
        upgraded = ws_path and status == 101
        kind_requests, kind_pages = KIND_AT[kind]
        c[REQ] += 1
        c[kind_requests] += 1
        if 200 <= status < 600:
            c[WS_5XX if status >= 500 and ws_path else STATUS_AT[status // 100]] += 1
        if status >= 500:
            if ws_path:
                c[WS_ERR] += 1
            else:
                c[ERR] += 1
                self._seq += 1
                item = Error5xx(ts, self._seq, path[:PATH_LIMIT], status)
                if len(self.recent) < RECENT_5XX:
                    heapq.heappush(self.recent, item)
                else:
                    heapq.heappushpop(self.recent, item)
        if probe:
            c[PROBES] += 1
        if upgraded:
            c[WS] += 1
            c[next((i for limit, i in DURATION_AT if duration < limit), GE_1H)] += 1
        if page:
            c[PAGES] += 1
            c[kind_pages] += 1
        if kind == "browser":
            if js_view:
                c[JS] += 1
            if page:
                c[HUMAN] += 1
                self._lists(hour, judged, path, query, ref)
        if ua_kind == "browser" and ip and ts >= self.gate_ts:
            flags = (PROBED if probe else 0) | (OPERATED if operator else 0)
            signal = js_view or upgraded
            self._pair(ts, rec, judged, path, query, ref, page, signal, upgraded, flags)

    def _lists(self, hour: Hour, judged: str, path: str, query: str, ref: Ref) -> None:
        if hour.lists is None:
            hour.lists = [{} for _ in LISTS]
        paths, tabs, refs, utms, devices, browsers = hour.lists
        keys: list[tuple[dict[str, int], str]] = [(paths, path[:PATH_LIMIT])]
        if query:
            params = parse_qs(query, keep_blank_values=True)
            if path == APP_PATH:
                tab = params["tab"][0] if "tab" in params else DEFAULT_TAB
                keys.append((tabs, tab if tab in TABS else OTHER))
            utm = params.get("utm_source", [""])[0]
            if utm:
                keys.append((utms, utm.lower()[:UTM_LIMIT]))
        elif path == APP_PATH:
            keys.append((tabs, DEFAULT_TAB))
        if ref is not None and ref[1] not in lines.SELF_HOSTS:
            keys.append((refs, ref[0][:REFERRER_LIMIT]))
        traits = self._trait(judged)
        keys += ((devices, traits.device), (browsers, traits.browser))
        for table, key in keys:
            # 시마다 목록마다 30 — 이미 있는 키는 세고 새 키가 넘치면 (기타)
            if key in table:
                table[key] += 1
            elif len(table) < LIST_CAP:
                table[key] = 1
            else:
                table[OTHER] = table.get(OTHER, 0) + 1

    def _pair(
        self,
        ts: float,
        rec: Line,
        judged: str,
        path: str,
        query: str,
        ref: Ref,
        *how: Any,
    ) -> None:
        """짝 하나의 그날 기록 — 해시는 (IP, UA 전체), 특성은 UA 앞 1,024자(`judged`).
        `how` 는 (페이지 줄, JS 신호 줄, 101 줄, 탐색·운영자 흔적 비트)."""
        page, signal, upgraded, flags = how
        its = int(ts)
        day, record, key = self._day
        # 줄은 대개 시각 순 — 날이 바뀔 때만 기록·열쇠를 찾는다
        if not day <= its < day + DAY_SEC:
            day = kst_day(ts)
            record, key = recorded(self.days, day), self.keys.day_key(day)
            self._day = day, record, key
        h = pair_hash(key, rec[6], rec[4])
        pair = record.pairs.get(h)
        if pair is None:
            if len(record.pairs) >= PAIR_CAP:
                record.capped = True  # 넘는 짝은 기록하지 않는다 — 줄 세기는 그대로
                return
            pair = record.pairs[h] = PairDay(self._trait(judged))
        bit = 1 << ((its - day) // 3600)
        pair.flags |= flags
        if signal:
            pair.js |= bit
        if upgraded:
            pair.ws |= bit
        if page:
            pair.pages |= bit
            if pair.first is None or ts < pair.first:
                pair.first = ts
                host = None if ref is None else ref[1]
                pair.channel = access_traits.channel(query, host, pair.traits.inapp)
                # 다시 온 방문 — `/` 는 no-cache 라 캐시를 가진 브라우저의 그날 첫 요청이 304 다(새로고침은 앞에 200 이 있다)
                pair.returning = rec[2] == 304 and path in lines.RETURNING_PATHS
