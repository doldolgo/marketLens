"""caddy 접속 로그 흉내 — 027·032 의 한 줄 모양(caddy 2.11.4 로컬 출력과 같은 필드 순서)과 파일 쓰기 (035·038).

게이트(처리방침 v2 시행일 00:00 KST) 앞 시계 `NOW` 와 뒤 시계 `AFTER` 둘을 둔다. 회전 파일은 `rotated` 로 쓴다 —
이름 `access-<UTC>-time.log.gz`, 수정 시각 = 마지막 줄 `ts`.
"""

import gzip
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.features.admin import access_cache
from app.features.admin.access_cache import AccessLog
from app.features.admin.visits import VisitFeeds

# 2026-10-01T09:30:00Z — 시 경계에서 30분 지난 시각, 게이트 전. 창 = 시작 − 23시간 ~ 지금
NOW = 1_790_847_000.0
HOUR = 3600
DAY = 86_400
END_TS = int(NOW)
START_TS = END_TS - END_TS % HOUR - 23 * HOUR
EFFECTIVE = "2026-10-11"
GATE = 1_791_644_400  # 2026-10-10T15:00Z = 2026-10-11 00:00 KST
# 게이트 + 2일 + 10시간 30분 = 2026-10-13 10:30 KST(01:30Z) — 24시간 창은 KST 어제·오늘 둘에 걸친다
AFTER = GATE + 2 * DAY + 10 * HOUR + 1800.0

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
EDGE = CHROME + " Edg/129.0"
SAMSUNG = "Mozilla/5.0 (Linux; Android 14; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0 Mobile Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:131.0) Gecko/20100101 Firefox/131.0"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
KAKAO = IPHONE + " KAKAOTALK 10.4.5"
IP = "203.0.113.0"


def line(
    ts: float,
    uri: str = "/",
    *,
    status: int = 200,
    method: str = "GET",
    ua: str = CHROME,
    referer: str = "",
    duration: float = 0.012,
    ip: str | None = IP,
) -> str:
    """`ua=None` 이면 ua 키가 없고, `ip=None` 이면 IP 두 칸이 없다."""
    request: dict[str, Any] = {"remote_port": "51234"}
    if ip is not None:
        request = {"remote_ip": ip, **request, "client_ip": ip}
    request |= {
        "proto": "HTTP/2.0",
        "method": method,
        "host": "kimptrack.com",
        "uri": uri,
    }
    record: dict[str, Any] = {
        "level": "info",
        "ts": ts,
        "logger": "http.log.access.log0",
        "msg": "handled request",
        "request": request,
        "bytes_read": 0,
        "user_id": "",
        "duration": duration,
        "size": 1234,
        "status": status,
        "referer": referer,
    }
    if ua is not None:
        record["ua"] = ua
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def write(
    directory: Path, name: str, lines: list[str], *, mtime: float | None = None
) -> Path:
    path = directory / name
    text = "".join(f"{ln}\n" for ln in lines)
    if name.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def at(hour: int, sec: float = 0.0) -> float:
    """창의 `hour` 번째 시(0~23) 안의 시각."""
    return START_TS + hour * HOUR + sec


def rotated(directory: Path, lines: list[str], *, gz: bool = True) -> Path:
    """회전 파일 — 이름은 마지막 줄의 UTC 시각, 수정 시각 = 마지막 줄 `ts`."""
    last = json.loads(lines[-1])["ts"]
    stamp = datetime.fromtimestamp(last, UTC).strftime("%Y-%m-%dT%H-%M-%S.%f")[:-3]
    name = f"access-{stamp}-time.log{'.gz' if gz else ''}"
    return write(directory, name, lines, mtime=last)


def summary(directory: Path, now: float, window: str = "24h") -> dict[str, Any]:
    """새 캐시 하나로 창 하나 — 게이트는 `EFFECTIVE`."""
    return AccessLog(str(directory), GATE).summary(window, now)[0]


def watch_opens(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """파일 이름별로 연 횟수 — 여는 함수를 감시한다."""
    counts: Counter[str] = Counter()
    real = access_cache.open_log

    def watched(path: str) -> Any:
        counts[Path(path).name] += 1
        return real(path)

    monkeypatch.setattr(access_cache, "open_log", watched)
    return counts


class Timer:
    def __init__(self, delay: float, callback: Any) -> None:
        self.delay, self.callback, self.cancelled = delay, callback, False

    def cancel(self) -> None:
        self.cancelled = True


class Feeds:
    """시계(초)·mono·타이머를 손으로 움직이는 VisitFeeds."""

    def __init__(self, directory: Path | None, t0: float, **kw: Any) -> None:
        self.t = 0.0
        self.timers: list[Timer] = []
        self.feeds = VisitFeeds(
            access_dir=None if directory is None else str(directory),
            clarity_token=None,
            clock=lambda: t0 + self.t,
            mono=lambda: self.t,
            privacy_effective=kw.pop("effective", EFFECTIVE),
            schedule=self._schedule,
            **kw,
        )

    def _schedule(self, delay: float, callback: Any) -> Timer:
        self.timers.append(Timer(delay, callback))
        return self.timers[-1]

    async def get(self, window: str | None = None) -> dict[str, Any]:
        return await self.feeds.access(window)
