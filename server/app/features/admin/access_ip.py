"""IP 대역 하나의 접속 — 창 안 기록을 그 대역 줄만 골라 다시 훑기 (스펙 067 §3.3).

목록(`access_bands.py`)은 038 의 파일 캐시에 대역 칸을 더해 셀 뿐 줄을 남기지 않는다 — 상세는 요청이 있을 때만 창 안 파일
(`access.log`·수정 시각이 창 시작 뒤인 회전 파일)을 다시 연다. 최근 파일부터 압축 풀린 크기를 더해 100MiB 까지만 읽고, 넘는
오래된 파일은 빼고 `partial`(깨진 회전 파일·목록을 본 뒤 사라진 파일도 `partial`). 줄을 풀기 전에 그 대역의 주소 앞부분 글자가
줄에 있는지 먼저 본다(IPv4 `"a.b.c.`·IPv6 첫 마디) — 있는 줄만 JSON 을 풀고 대역을 다시 맞춘다.
줄 분류(종류·페이지·JS 신호·탐색·운영자 흔적)는 038 §3.4, 페이지 이름·채널은 062·038 §3.5 와 같은 규칙이다. 짝은 (IP, UA,
KST 날)로 세는 동안만 들고(게이트 뒤 줄만 — 038 짝 게이트), 응답에는 그날 첫·마지막 페이지·페이지 수·기기·OS·브라우저·채널만
있다 — UA 원문·짝 열쇠·원래 IP 는 응답에 없다. 동기 함수다 — api 가 `asyncio.to_thread` 에서 한 번에 하나 부른다.
"""

import heapq
from collections import Counter
from collections.abc import Iterable
from typing import Any

from app.features.admin import access_classes as lines
from app.features.admin import access_traits
from app.features.admin.access_bands import (
    STATUS_NAMES,
    agent_name,
    band_of,
    geo_of,
    top,
)
from app.features.admin.access_cache import (
    BROKEN,
    BUDGET_BYTES,
    Current,
    bounds,
    hour_start,
    listing,
    open_log,
    raw_size,
)
from app.features.admin.access_classes import CLASSES
from app.features.admin.access_flows import NPAGES_CAP, page_name
from app.features.admin.access_pairs import kst_day
from app.features.admin.access_traits import Traits
from app.features.admin.geo_table import GeoTable

PATH_LIMIT = 100
TOP = 20  # agents·paths
PAIRS = 50
RECENT = 100
MEMO_CAP = 4_096  # UA 판정 메모 — 다 읽으면 버린다


def needle(band: str) -> str:
    """줄에서 먼저 찾는 글자 — IP 칸은 `"…"` 안에 있어 앞 따옴표를 붙인다. IPv6 가 `::` 로 시작하면 `"::`."""
    address = band.partition("/")[0]
    if ":" not in address:
        return '"' + address.rsplit(".", 1)[0] + "."
    head = address.split(":", 1)[0]
    return '"' + (head + ":" if head else "::")


def choose_files(
    current: Current | None,
    rotated: dict[str, tuple[str, int, float]],
    start: int,
) -> tuple[list[str], bool]:
    """창 안 파일(줄 순서 — 오래된 회전 파일부터, `access.log` 끝)과 100MiB 를 넘어 뺀 파일이 있는지.
    예산은 최근 파일부터 압축 풀린 크기로 센다(`.gz` 는 꼬리의 ISIZE — 038 과 같다)."""
    newest: list[tuple[str, int]] = []
    if current is not None:
        newest.append((current.path, max(current.size, 0)))
    for path, size, mtime in sorted(rotated.values(), key=lambda v: v[2], reverse=True):
        if mtime < start:
            break  # 마지막 줄이 창 시작 앞 — 이 뒤는 더 오래됐다
        newest.append((path, size))
    chosen: list[str] = []
    spent = 0
    for path, size in newest:
        cost = raw_size(path, size)
        if cost is None:
            continue  # 목록을 본 뒤 사라졌다
        if spent + cost > BUDGET_BYTES:
            return chosen[::-1], True
        spent += cost
        chosen.append(path)
    return chosen[::-1], False


class BandScan:
    """대역 하나의 창 — `read` 가 파일마다 줄을 흘려 넣고 `values` 가 응답 값을 만든다."""

    def __init__(self, band: str, start: int, end: int, gate: int) -> None:
        self.band = band
        self.needle = needle(band)
        self.start, self.last, self.gate = start, hour_start(end), gate
        # 시 → [요청, 사람 모양 페이지, JS 신호, 5xx(WS 경로 뺌)]
        self.hours: dict[int, list[int]] = {}
        self.kinds: Counter[str] = Counter()
        self.status = [0, 0, 0, 0]
        self.paths: Counter[str] = Counter()
        self.agents: Counter[str] = Counter()
        # (IP, UA, KST 날) → [첫 페이지 시각, 첫 이름, 채널, 마지막 시각, 마지막 이름, 페이지 수, 특성] — 세는 동안만
        self.flows: dict[tuple[str, str, int], list[Any]] = {}
        self.recent: list[tuple[float, int, str, int, str, Traits, str | None]] = []
        self._seq = 0
        self._bands: dict[str, str] = {}
        self._kinds: dict[str, str] = {}
        self._traits: dict[str, Traits] = {}

    def read(self, handle: Iterable[str]) -> None:
        try:
            for raw in handle:
                if self.needle in raw:
                    self._line(raw)
        finally:
            self._kinds.clear()
            self._traits.clear()

    def _line(self, raw: str) -> None:
        ts = lines.line_ts(raw)
        if ts is None or not self.start <= ts < self.last + 3600:
            return
        rec = lines.parse(raw)
        if rec is None:
            return
        method, uri, status, _, ua, referer, ip = rec
        band = self._bands.get(ip)
        if band is None:
            band = self._bands[ip] = band_of(ip)
        if band != self.band:
            return
        path, _, query = uri.partition("?")
        ref = lines.referrer(referer) if referer else None
        operator = ref is not None and lines.is_operator(ref[1])
        judged = ua[: lines.UA_JUDGE]
        ua_kind = self._kinds.get(judged) or lines.ua_kind(judged)
        traits = self._traits.get(judged) or access_traits.traits(judged)
        if len(self._kinds) < MEMO_CAP:
            self._kinds[judged], self._traits[judged] = ua_kind, traits
        # 038 §3.4 — 운영자 흔적 > 탐색한 브라우저 모양(scanner) > UA 로 정한 종류
        if operator:
            kind = "operator"
        elif ua_kind == "browser" and lines.PROBE.search(path.lower()) is not None:
            kind = "scanner"
        else:
            kind = ua_kind
        fetched = method == "GET" and status in (200, 304)
        page = fetched and "." not in path[path.rfind("/") + 1 :]
        ws_path = path == lines.WS_PATH
        its = int(ts)
        cell = self.hours.get(its - its % 3600)
        if cell is None:
            cell = self.hours[its - its % 3600] = [0, 0, 0, 0]
        cell[0] += 1
        if kind == "browser":
            cell[1] += 1 if page else 0
            cell[2] += 1 if fetched and path in lines.JS_PATHS else 0
        if 200 <= status < 600 and not (status >= 500 and ws_path):
            self.status[status // 100 - 2] += 1
            cell[3] += 1 if status >= 500 else 0
        self.kinds[kind] += 1
        short = path[:PATH_LIMIT]
        self.paths[short] += 1
        self.agents[agent_name(traits)] += 1
        self._seq += 1
        item = (
            ts,
            self._seq,
            short,
            status,
            kind,
            traits,
            None if ref is None else ref[0],
        )
        if len(self.recent) < RECENT:
            heapq.heappush(self.recent, item)
        else:
            heapq.heappushpop(self.recent, item)
        if page and ip and ts >= self.gate:
            self._flow(ts, ip, ua, path, query, ref, traits)

    def _flow(
        self,
        ts: float,
        ip: str,
        ua: str,
        path: str,
        query: str,
        ref: tuple[str, str] | None,
        traits: Traits,
    ) -> None:
        """짝의 그날 페이지 줄 — 첫(같은 시각이면 앞 줄)·마지막(같은 시각이면 뒤 줄) 이름, 수(255 에서 멈춤), 첫 줄의 채널(062)."""
        name = page_name(path, query)
        key = (ip, ua, kst_day(ts))
        flow = self.flows.get(key)
        if flow is None or ts < flow[0]:
            host = None if ref is None else ref[1]
            channel = access_traits.channel(query, host, traits.inapp)
            if flow is None:
                self.flows[key] = [ts, name, channel, ts, name, 1, traits]
                return
            flow[0], flow[1], flow[2] = ts, name, channel
        if ts >= flow[3]:
            flow[3], flow[4] = ts, name
        flow[5] = min(flow[5] + 1, NPAGES_CAP)

    def values(self) -> dict[str, Any]:
        hourly = []
        for ts in range(self.start, self.last + 1, 3600):
            requests, pages, js, errors = self.hours.get(ts) or (0, 0, 0, 0)
            hourly.append(
                {
                    "ts": ts,
                    "requests": requests,
                    "pages": pages,
                    "js": js,
                    "errors": errors,
                }
            )
        # 최근 짝 먼저 — 마지막 페이지 줄 시각이 늦은 순(같으면 첫·마지막 이름순)
        flows = sorted(self.flows.values(), key=lambda f: (-f[3], f[1], f[4]))
        recent = sorted(self.recent, reverse=True)
        return {
            "hourly": hourly,
            "kinds": {k: self.kinds[k] for k in CLASSES},
            "status": dict(zip(STATUS_NAMES, self.status, strict=True)),
            "agents": top(self.agents, TOP),
            "paths": top(self.paths, TOP),
            "pairs": [
                {
                    "entry": f[1],
                    "exit": f[4],
                    "npages": f[5],
                    "device": f[6].device,
                    "os": f[6].os,
                    "browser": f[6].browser,
                    "channel": f[2],
                }
                for f in flows[:PAIRS]
            ],
            "recent": [
                {
                    "ts": int(ts),
                    "path": path,
                    "status": status,
                    "kind": kind,
                    "browser": t.browser,
                    "os": t.os,
                    "device": t.device,
                    "referer": origin,
                }
                for ts, _, path, status, kind, t, origin in recent
            ],
        }


def scan(
    directory: str | None,
    band: str,
    name: str,
    now: float,
    gate: int,
    table: GeoTable | None,
) -> tuple[dict[str, Any], list[str]]:
    """창 `name` 의 대역 `band` 값과 깨진 회전 파일의 예외 이름들. 파일이 없으면 `NoLogFile`, `access.log` 읽기 실패는 예외."""
    start, end = bounds(name, now, gate)
    current, rotated = listing(directory)
    files, partial = choose_files(current, rotated, start)
    tally = BandScan(band, start, end, gate)
    broken: list[str] = []
    for path in files:
        try:
            with open_log(path) as handle:
                tally.read(handle)
        except FileNotFoundError:
            partial = True  # 목록을 본 뒤 압축·보관 삭제로 사라졌다
        except BROKEN as exc:
            if current is not None and path == current.path:
                raise
            partial = True  # 깨진 회전 파일 — 읽은 데까지 센다
            broken.append(type(exc).__name__)
    country, net = geo_of(table, band)
    return {
        "partial": partial,
        "country": country,
        "net": net,
        **tally.values(),
    }, broken
