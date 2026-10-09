# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""관리자 화면 v3 확인용 가짜 피드 서버 (스펙 064 §4 설계 세션 — 배포 이미지에 들지 않는다).

    uv run web/scripts/admin-fake-feeds.py [--port 18064] [--scenario ok]

- `web/admin` 의 정적 파일을 `/` 에 관리자 nginx 와 같은 헤더(CSP·X-Frame-Options·Cache-Control)로 주고, 화면이 부르는
  피드 아홉(029·011·034·035·038·039·040·062·063 의 응답 꼴)과 `POST /api/refresh` 를 지어낸 값으로 답한다.
  표준 라이브러리만 쓰고 127.0.0.1 에만 붙는다. 실제 서버·AWS·Clarity 를 부르지 않는다.
- 경우(--scenario, 돌던 중에는 `GET /__scenario/<이름>` 로 바꾼다):
  ok(정상) · problem(빨강·주황 칩 여럿) · before(처리방침 시행 전 — 24시간 창만·방문자 시행 전) ·
  missing(062·063 배포 전 — 시계열 404·흐름 키 없음) · empty(연결 안 됨 — AWS·Clarity·접속 로그 없음) ·
  xss(글자 칸마다 `<img onerror>` — 글로만 보여야 한다) · expired(헬스가 401 비JSON — 로그인 만료 새로고침 한 번).
- `/screens.html` 은 065 전이라 없다(404). 시각은 이 컴퓨터 시계다.
"""

import argparse
import json
import math
import mimetypes
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ADMIN = Path(__file__).resolve().parent.parent / "admin"
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-src https://kimptrack.com; frame-ancestors 'none'"
SCENARIOS = ("ok", "problem", "before", "missing", "empty", "xss", "expired")
X = "<img src=x onerror=alert(1)>"
BOXES = ("collect", "data", "serve")
PERIOD = {"6h": (21_600, 300), "24h": (86_400, 300), "7d": (604_800, 3600), "30d": (2_592_000, 10_800)}
SPOT = ("upbit", "bithumb", "binance", "bybit", "bitget", "okx")
PERP = ("binance_perp", "bybit_perp", "bitget_perp", "hyperliquid_perp")
state = {"scenario": "ok"}


def txt(value: str) -> str:
    """xss 경우에는 글자 칸마다 HTML 조각을 붙인다."""
    return f"{value}{X}" if state["scenario"] == "xss" else value


def wave(t: float, base: float, amp: float, day: float = 86_400) -> float:
    """하루 주기의 물결 + 작은 흔들림 — 같은 시각이면 같은 값(새로고침마다 그림이 춤추지 않게)."""
    rng = random.Random(int(t) ^ int(base * 1000))
    return max(0.0, base + amp * math.sin(2 * math.pi * (t % day) / day) + rng.uniform(-amp, amp) * 0.25)


def grid(start: int, end: int, step: int, f) -> list[list]:
    points = [[t, f(t)] for t in range(start, end, step)]
    if points:
        points[-1][1] = None  # CloudWatch 의 늦은 마지막 칸
    return points


def part(refresh: int, **values) -> dict:
    return {"state": "ok", "code": None, "fetchedAt": int(time.time() * 1000), "refreshSec": refresh, **values}


def off(code: str | None = "no_credentials", **keys) -> dict:
    return {"state": "unconfigured", "code": code, "fetchedAt": None, "refreshSec": 0, **keys}


def health() -> tuple[int, object]:
    now = int(time.time() * 1000)
    if state["scenario"] == "expired":
        return 401, "<html>Cloudflare Access login</html>"
    if state["scenario"] == "problem":
        return 503, {"status": "stale", "version": "0.1.0", "lastTickAt": now - 45_000}
    return 200, {"status": "ok", "version": "0.1.0", "lastTickAt": now - 400}


def status() -> dict:
    influx = "down" if state["scenario"] == "problem" else "ok"
    return {"wsConnections": 3, "redis": "ok", "influx": influx, "version": "0.1.0"}


def collect() -> dict:
    now = int(time.time() * 1000)
    problem = state["scenario"] == "problem"
    outages = [
        {"exchange": "bybit", "kind": "timeout", "startedAt": now - 5 * 3_600_000, "endedAt": now - 5 * 3_600_000 + 95_000,
         "lastFailedAt": now - 5 * 3_600_000 + 90_000, "count": 9, "statusCode": None, "message": txt("바이빗 WebSocket 실패: TimeoutError"), "url": "", "retryAfterSec": None},
        {"exchange": "upbit", "kind": "rate_limit", "startedAt": now - 14 * 3_600_000, "endedAt": now - 14 * 3_600_000 + 40_000,
         "lastFailedAt": now - 14 * 3_600_000 + 39_000, "count": 4, "statusCode": 429, "message": txt('{"error":"too many requests"}'), "url": "", "retryAfterSec": 10},
    ]
    if problem:
        outages.insert(0, {"exchange": "binance", "kind": "banned", "startedAt": now - 420_000, "endedAt": None, "lastFailedAt": now - 1000,
                           "count": 418, "statusCode": 418, "message": txt("IP banned"), "url": "", "retryAfterSec": None})
    rows = []
    for ex in SPOT + PERP:
        down = problem and ex == "binance"
        stale = problem and ex == "okx"
        rows.append({
            "exchange": txt(ex) if state["scenario"] == "xss" and ex == "okx" else ex,
            "state": "down" if down else "stale" if stale else "ok",
            "lastSuccessAt": now - (420_000 if down else 9_000 if stale else 700),
            "markets": 0 if down else 180 if ex == "hyperliquid_perp" else 260,
            "successRate1h": 88.4 if down else 97.3 if stale else 100.0,
            "openOutage": outages[0] if down else None,
            "lastError": None,
        })
    rate = round(sum(r["successRate1h"] for r in rows) / len(rows), 1)
    return {"serverStartedAt": now - 30 * 3_600_000, "fetchedAt": now, "successRate1h": rate, "exchanges": rows, "outages": outages}


ALARMS = ("collect-status-instance", "collect-memory", "collect-disk", "data-status-instance", "data-credit-balance",
          "data-credit-surplus", "data-memory", "data-disk", "serve-status-instance", "serve-credit-balance",
          "serve-credit-surplus", "serve-memory", "serve-disk", "canary", "http-5xx", "collect-status-system",
          "data-status-system", "serve-status-system")


def aws() -> dict:
    now = int(time.time() * 1000)
    s = state["scenario"]
    if s == "empty":
        return {"alarms": off(counts=None, items=None), "metrics": off(), "canary": off(ok=None, lastRunAt=None, durationMs=None, lines=None),
                "budget": off(items=None)}
    firing = {"serve-disk", "canary"} if s == "problem" else set()
    items = [{"name": txt(f"marketlens-{a}"), "state": "ALARM" if a in firing else "OK", "changedAt": now - 3_600_000,
              "reason": txt("Threshold Crossed: 1 datapoint [88.2] was greater than the threshold (80.0).")} for a in ALARMS]
    counts = {"ok": len(items) - len(firing), "alarm": len(firing), "insufficientData": 0}
    canary = part(60, ok=s != "problem", lastRunAt=now - 140_000, durationMs=1184, lines=[txt("1단계 통과 (212ms)")])
    budget = part(21_600, items=[{"name": txt("marketlens-monthly"), "unit": "USD", "limit": 130.0,
                                  "actual": 133.4 if s == "problem" else 42.17, "forecast": 151.0 if s == "problem" else 95.3, "timeUnit": "MONTHLY"}])
    return {"alarms": part(60, counts=counts, items=items), "metrics": part(300), "canary": canary, "budget": budget}


def series(range_: str) -> tuple[int, dict]:
    s = state["scenario"]
    if s == "missing":
        return 404, {"detail": "Not Found"}
    if s == "empty":
        return 200, {**off(), "range": range_, "periodSec": None, "startTs": None, "endTs": None, "boxes": None, "wsClients": None, "canary": None}
    window, step = PERIOD.get(range_, PERIOD["24h"])
    end = int(time.time()) // step * step
    start = end - window
    hot = s == "problem"
    base = {"collect": (55, 15, 1.6e6, 4.2e5), "data": (22, 8, 3.1e5, 2.2e5), "serve": (12, 6, 1.5e5, 8.3e5)}
    boxes = []
    for box in BOXES:
        cpu, amp, net_in, net_out = base[box]
        g = lambda f: grid(start, end, step, f)  # noqa: E731
        credit = box != "collect"
        boxes.append({
            "box": txt(box) if s == "xss" and box == "serve" else box,
            "instanceId": "i-0fake",
            "series": {
                "cpu": g(lambda t, c=cpu, a=amp: min(100.0, round(wave(t, c, a) + (40 if hot and box == "collect" and t > end - 3600 else 0), 2))),
                "netIn": g(lambda t, v=net_in: int(wave(t, v, v / 3))),
                "netOut": g(lambda t, v=net_out: int(wave(t, v, v / 3))),
                "ebsRead": g(lambda t: int(wave(t, 2.0e4, 1.5e4))),
                "ebsWrite": g(lambda t, b=box: int(wave(t, 6.0e5 if b == "data" else 4.0e4, 2.0e5 if b == "data" else 2.0e4))),
                "creditBalance": g(lambda t, b=box: round(8.0 if hot and b == "serve" and t > end - 7200 else wave(t, 420 if b == "data" else 150, 30), 2)) if credit else None,
                "creditUsage": g(lambda t: round(wave(t, 0.4, 0.3), 2)) if credit else None,
                "surplusCharged": g(lambda t, b=box: 0.6 if hot and b == "serve" and t > end - 3600 else 0.0) if credit else None,
                "statusFailed": g(lambda t, b=box: 1 if hot and b == "data" and end - 5400 < t < end - 3600 else 0),
                "mem": g(lambda t, b=box: round(wave(t, {"collect": 62, "data": 71, "serve": 56}[b], 4) + (25 if hot and b == "data" else 0), 2)),
                "disk": g(lambda t, b=box: {"collect": 41.2, "data": 63.5, "serve": 88.1 if hot else 72.4}[b]),
                "swap": g(lambda t: round(wave(t, 3, 1), 2)) if box == "data" else None,
            },
        })
    ws = grid(start, end, step, lambda t: int(wave(t, 4, 3)))
    canary = {"errors": grid(start, end, step, lambda t: 1 if hot and t > end - 1800 else 0), "durationMs": grid(start, end, step, lambda t: int(wave(t, 1100, 250)))}
    return 200, part(300 if range_ in ("6h", "24h") else 1800 if range_ == "7d" else 3600, range=range_, periodSec=step, startTs=start, endTs=end,
                     boxes=boxes, wsClients=ws, canary=canary)


def alerts() -> dict:
    now = int(time.time() * 1000)
    if state["scenario"] == "empty":
        return {"items": [], "slack": off(None), "alarms": off()}
    items = []
    for i, (tone, text) in enumerate([("🔴", "수집 실패 60초 — binance banned"), ("🟢", "수집 복구 — binance"), ("⚠", "ERROR 로그 — flusher 쓰기 실패"),
                                      ("🔴", "처리 안 된 500 — /history/events"), ("🟢", "기동 — collector")]):
        at = now - (900_000 if state["scenario"] == "problem" and i == 0 else (i + 1) * 31 * 3_600_000)
        items.append({"at": at, "source": "slack", "text": txt(f"{tone} {text}"), "role": "collector", "key": txt(f"key-{i}"),
                      "delivered": i != 2, "alarm": None, "fromState": None, "toState": None})
    for i, (alarm, to) in enumerate([("serve-disk", "ALARM"), ("serve-disk", "OK"), ("canary", "ALARM"), ("canary", "OK")]):
        items.append({"at": now - (i + 1) * 19 * 3_600_000, "source": "alarm", "text": txt("Threshold Crossed"), "role": None, "key": None,
                      "delivered": None, "alarm": txt(f"marketlens-{alarm}"), "fromState": "OK" if to == "ALARM" else "ALARM", "toState": to})
    items.sort(key=lambda it: -it["at"])
    return {"items": items, "slack": part(0), "alarms": part(60)}
