"""날마다 센 방문자 — (가린 IP, UA) 짝 기록과 창 안 세기 (스펙 038 §3.5).

짝은 메모리에서 BLAKE2b(8바이트) 값으로만 든다 — 열쇠는 처음 짝을 만들 때 만든 `os.urandom(16)` 에 그 줄의 KST
날짜를 섞은 값이라, 같은 짝도 날이 다르면 다른 값(날을 넘어 이을 수 없다)이고 같은 날을 여러 파일이 나눠도 같은
값이다(합칠 수 있다). 짝 값·IP·UA 원문은 응답·로그 어디에도 내지 않는다. 짝 하나가 그날 갖는 것은 KST 시 비트
셋(페이지·JS 신호·101)·첫 페이지 줄의 시각과 채널·다시 온 여부·탐색/운영자 흔적·UA 로 정한 기기·OS·브라우저·인앱,
DB-IP 판이 올라 있을 때 처음 기록하며 찾은 나라·망 종류(039 — IP 는 남기지 않는다), 첫·마지막 페이지 줄의 페이지
이름과 마지막 페이지 줄의 시각·페이지 줄 수(062)뿐이다.
"""

import hashlib
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, NamedTuple

from app.features.admin.access_flows import NPAGES_CAP
from app.features.admin.access_traits import Traits
from app.features.admin.geo_kinds import NET_TELECOM_ALL, TELECOM, TELECOM_KR
from app.features.admin.geo_table import IPV6

KST_SEC = 9 * 3600
DAY_SEC = 86_400
PAIR_CAP = 1_000  # (파일, KST 날)마다 — 위장 브라우저 폭주가 메모리를 키우지 않게
PROBED = 1
OPERATED = 2


def kst_day(ts: float) -> int:
    """그 시각이 든 KST 날의 00:00(epoch 초)."""
    return (int(ts) + KST_SEC) // DAY_SEC * DAY_SEC - KST_SEC


def pair_hash(key: bytes, ip: str, ua: str) -> bytes:
    # IP 에는 NUL 이 없어 (ip, ua) 가 한 값으로만 풀린다. JSON 이 준 짝 없는 서로게이트도 바이트로 바꿀 수 있게 surrogatepass
    data = f"{ip}\0{ua}".encode("utf-8", "surrogatepass")
    return hashlib.blake2b(data, digest_size=8, key=key).digest()


class PairKeys:
    """날마다 바뀌는 열쇠 — 바탕 값은 처음 짝을 만들 때 하나 만들고, 캐시를 통째로 버리면(§3.3) 이 객체째 버린다."""

    def __init__(self, urandom: Callable[[int], bytes] = os.urandom) -> None:
        self._urandom = urandom
        self._base: bytes | None = None
        self._days: dict[int, bytes] = {}

    def day_key(self, day: int) -> bytes:
        key = self._days.get(day)
        if key is None:
            if self._base is None:
                self._base = self._urandom(16)
            date = datetime.fromtimestamp(day + KST_SEC, UTC).date().isoformat()
            key = self._days[day] = self._base + date.encode()
        return key


class PairDay:
    """짝 하나의 그날 — 시는 그날 00시부터의 비트(0~23)."""

    __slots__ = (
        "pages",
        "js",
        "ws",
        "first",
        "channel",
        "returning",
        "flags",
        "traits",
        "country",
        "net",
        "entry",
        "exit",
        "last",
        "npages",
    )

    def __init__(
        self, traits: Traits, country: str | None = None, net: str | None = None
    ) -> None:
        self.pages = self.js = self.ws = self.flags = 0
        self.first: float | None = None  # 그날 첫 페이지 줄의 시각
        self.channel = "unknown"
        self.returning = False
        self.traits = traits
        # 039 — 나라 두 글자 또는 `(기타)`·망 종류. 판 없이 기록한 짝은 둘 다 None, IPv6 는 망 칸만 `ipv6`
        self.country = country
        self.net = net
        # 062 — 첫·마지막 페이지 줄의 페이지 이름(고정 글자), 마지막 페이지 줄의 시각(파일을 합칠 때 고른다)·페이지 줄 수.
        # 페이지 줄이 없는 짝(JS·WS 줄만)은 None·None·None·0 이라 흐름 셈에서 빠진다
        self.entry: str | None = None
        self.exit: str | None = None
        self.last: float | None = None
        self.npages = 0


class DayPairs:
    """(파일, KST 날) 하나의 짝 기록 — 처음 나온 순서로 1,000개까지."""

    __slots__ = ("pairs", "capped")

    def __init__(self) -> None:
        self.pairs: dict[bytes, PairDay] = {}
        self.capped = False


def _merge(a: PairDay, b: PairDay) -> PairDay:
    """같은 날을 두 파일이 나눔 — 시는 합집합, 첫 페이지 줄은 이른 쪽, 마지막 페이지 줄은 늦은 쪽, 페이지 수는 합(255 에서
    멈춤), 흔적은 하나라도 있으면. a 가 앞 파일이라 같은 시각이면 첫 페이지는 a·마지막 페이지는 b 다(줄 순서, 062).
    캐시 기록은 바꾸지 않는다."""
    early = a if b.first is None or (a.first is not None and a.first <= b.first) else b
    late = b if a.last is None or (b.last is not None and b.last >= a.last) else a
    out = PairDay(a.traits, a.country, a.net)  # 같은 짝 = 같은 /24·같은 판
    out.pages, out.js, out.ws = a.pages | b.pages, a.js | b.js, a.ws | b.ws
    out.flags = a.flags | b.flags
    out.first, out.channel, out.returning = early.first, early.channel, early.returning
    out.entry, out.exit, out.last = early.entry, late.exit, late.last
    out.npages = min(a.npages + b.npages, NPAGES_CAP)
    return out


def _day_mask(day: int, start_ts: int, end_hour: int) -> int:
    """그날 시 중 창 [start_ts, end_hour] 에 드는 것의 비트."""
    mask = 0
    for h in range(24):
        hour = day + h * 3600
        if start_ts <= hour <= end_hour:
            mask |= 1 << h
    return mask


def _rows(table: dict[str, list[int]]) -> list[list[Any]]:
    """`[[이름, confirmed, shaped], …]` — shaped 내림차순·같으면 이름순, 0 행은 뺀다."""
    rows = [[name, c, s] for name, (c, s) in table.items() if s]
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows


class GeoCounts(NamedTuple):
    """창의 나라·망 종류별 날마다 센 방문자(039) — 이름 → [confirmed, shaped], IPv6 짝은 shaped 수만.
    `peaks` 는 나라 → 창 안 KST 하루 가운데 그 나라의 짝(shaped)이 가장 많았던 날의 수 — 적은 나라 묶기의 기준.
    `net_peaks` 는 망 종류마다 같은 하루 최대, `NET_TELECOM_ALL` 은 telecom·telecom_kr 을 합친 하루 최대(접을 때 쓴다)."""

    countries: dict[str, list[int]]
    networks: dict[str, list[int]]
    ipv6: int
    peaks: dict[str, int]
    net_peaks: dict[str, int]


def visitors(
    files: Iterable[dict[int, DayPairs]], since_ts: int, end_ts: int
) -> tuple[dict[str, Any], int, GeoCounts]:
    """창 [since_ts, end_ts] 의 날마다 센 방문자 — (visitors 값 키, ws.pairs, 나라·망 종류).
    since_ts 는 시 경계이고 게이트 뒤다."""
    by_day: dict[int, list[DayPairs]] = {}
    for days in files:
        for day, record in days.items():
            by_day.setdefault(day, []).append(record)
    end_hour = int(end_ts) - int(end_ts) % 3600
    tables: dict[str, dict[str, list[int]]] = {
        k: {} for k in ("channels", "devices", "os", "browsers", "inApp")
    }
    total = [0, 0, 0]  # confirmed·shaped·returning
    ws_pairs = ipv6 = 0
    countries: dict[str, list[int]] = {}
    networks: dict[str, list[int]] = {}
    peaks: dict[str, int] = {}
    net_peaks: dict[str, int] = {}
    capped = False
    day_rows = []
    for day in range(kst_day(since_ts), kst_day(end_ts) + 1, DAY_SEC):
        records = by_day.get(day, [])
        capped = capped or any(r.capped for r in records)
        merged: dict[bytes, PairDay] = dict(records[0].pairs) if records else {}
        for record in records[1:]:
            for h, pair in record.pairs.items():
                seen = merged.get(h)
                merged[h] = pair if seen is None else _merge(seen, pair)
        mask = _day_mask(day, since_ts, end_hour)
        counts = [0, 0, 0]
        day_countries: dict[
            str, int
        ] = {}  # 그날 나라마다 짝 수 — 날을 넘어 같은 짝인지 알 수 없다
        day_networks: dict[str, int] = {}
        for pair in merged.values():
            if pair.flags or not (pair.pages | pair.js) & mask:
                continue  # 그날 탐색·운영자 흔적이 있거나 창 안에 페이지·JS 신호가 없다
            confirmed = 1 if pair.js & mask else 0
            counts[0] += confirmed
            counts[1] += 1
            counts[2] += 1 if confirmed and pair.returning else 0
            ws_pairs += 1 if pair.ws & mask else 0
            t = pair.traits
            names = (pair.channel, t.device, t.os, t.browser, t.inapp)
            for table, name in zip(tables.values(), names, strict=True):
                if name is not None:
                    row = table.setdefault(name, [0, 0])
                    row[0] += confirmed
                    row[1] += 1
            if pair.net == IPV6:
                ipv6 += 1
            elif pair.net is not None and pair.country is not None:
                for table, name in ((countries, pair.country), (networks, pair.net)):
                    row = table.setdefault(name, [0, 0])
                    row[0] += confirmed
                    row[1] += 1
                day_countries[pair.country] = day_countries.get(pair.country, 0) + 1
                day_networks[pair.net] = day_networks.get(pair.net, 0) + 1
        for name, n in day_countries.items():
            if n > peaks.get(name, 0):
                peaks[name] = n
        day_networks[NET_TELECOM_ALL] = day_networks.get(TELECOM, 0) + day_networks.get(
            TELECOM_KR, 0
        )
        for name, n in day_networks.items():
            if n > net_peaks.get(name, 0):
                net_peaks[name] = n
        day_rows.append(
            {
                "ts": day,
                "confirmed": counts[0],
                "shaped": counts[1],
                "returning": counts[2],
            }
        )
        for i in range(3):
            total[i] += counts[i]
    values = {
        "state": "ok",
        "code": None,
        "sinceTs": since_ts,
        "confirmed": total[0],
        "shaped": total[1],
        "returning": total[2],
        "capped": capped,
        "days": day_rows,
        **{key: _rows(table) for key, table in tables.items()},
    }
    return values, ws_pairs, GeoCounts(countries, networks, ipv6, peaks, net_peaks)


def before_gate() -> dict[str, Any]:
    """처리방침 v2 시행일 전 — 짝을 하나도 만들지 않았다."""
    keys = ("sinceTs", "confirmed", "shaped", "returning", "capped", "days")
    return {
        "state": "unconfigured",
        "code": "before_gate",
        **dict.fromkeys(keys),
        **dict.fromkeys(("channels", "devices", "os", "browsers", "inApp")),
    }


def recorded(days: dict[int, DayPairs], day: int) -> DayPairs:
    record = days.get(day)
    if record is None:
        record = days[day] = DayPairs()
    return record
