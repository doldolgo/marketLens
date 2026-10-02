"""caddy 접속 로그 흉내 — 027·032 의 한 줄 모양(caddy 2.11.4 로컬 출력과 같은 필드 순서)과 파일 쓰기."""

import gzip
import json
import os
from pathlib import Path

# 2026-10-01T09:30:00Z — 시 경계에서 30분 지난 시각. 창 = 시작 − 23시간 ~ 지금
NOW = 1_790_847_000.0
HOUR = 3600
END_TS = int(NOW)
START_TS = END_TS - END_TS % HOUR - 23 * HOUR

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
EDGE = CHROME + " Edg/129.0"
SAMSUNG = "Mozilla/5.0 (Linux; Android 14; SM-S918N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0 Mobile Safari/537.36"
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:131.0) Gecko/20100101 Firefox/131.0"
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
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
) -> str:
    return json.dumps(
        {
            "level": "info",
            "ts": ts,
            "logger": "http.log.access.log0",
            "msg": "handled request",
            "request": {
                "remote_ip": IP,
                "remote_port": "51234",
                "client_ip": IP,
                "proto": "HTTP/2.0",
                "method": method,
                "host": "kimptrack.com",
                "uri": uri,
            },
            "bytes_read": 0,
            "user_id": "",
            "duration": duration,
            "size": 1234,
            "status": status,
            "referer": referer,
            "ua": ua,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


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
