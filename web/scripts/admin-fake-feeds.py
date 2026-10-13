# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""관리자 화면 v4 확인용 가짜 피드 서버 (스펙 064 §4·066 §4 설계 세션 — 배포 이미지에 들지 않는다).

    uv run web/scripts/admin-fake-feeds.py [--port 18064] [--scenario ok]

- `web/admin` 의 정적 파일을 `/` 에 관리자 nginx 와 같은 헤더(CSP·X-Frame-Options·Cache-Control)로 주고, 화면이 부르는
  피드 열둘(029·011·034·035·038·039·040·062·063 + 067 대역 둘·068 날별 기록 — 068 은 #133 의 실제 응답 꼴)과
  `POST /api/refresh` 를 지어낸 값으로 답한다. 표준 라이브러리만 쓰고 127.0.0.1 에만 붙는다. 실제 서버·AWS·Clarity 를 부르지 않는다.
- 경우(--scenario, 돌던 중에는 `GET /__scenario/<이름>` 로 바꾼다):
  ok(정상) · problem(빨강·주황 칩 여럿) · old(067·068 배포 전 — 지금 운영: 대역·날별 기록 404, offset 모름, Clarity dims 없음) ·
  missing(062·063·067·068 배포 전 — 시계열 404·흐름 키 없음) · empty(연결 안 됨 — AWS·Clarity·접속 로그 없음) ·
  bands(대역 많음 — 1,200개, 200행씩) · nobands(대역 없음) ·
  xss(글자 칸마다 `<img onerror>` — 글로만 보여야 한다) · expired(헬스가 401 비JSON — 로그인 만료 새로고침 한 번).
- 시각은 이 컴퓨터 시계다.
"""

import argparse
import json
import math
import mimetypes
import random
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ADMIN = Path(__file__).resolve().parent.parent / "admin"
CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-src https://kimptrack.com; frame-ancestors 'none'"
SCENARIOS = ("ok", "problem", "old", "missing", "empty", "bands", "nobands", "xss", "expired")
# 067·068 이 없는 서버(지금 운영·그보다 앞)
PRE_068 = ("old", "missing")
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


def series(range_: str, offset: str | None = None) -> tuple[int, dict]:
    s = state["scenario"]
    if s == "missing":
        return 404, {"detail": "Not Found"}
    # 068 — offset=prev 면 바로 앞 같은 길이(끝 = 지금 창의 시작), 그 밖 값은 없는 것과 같다. 068 전 서버(old)는 offset 을 모른다
    shift = offset == "prev" and s not in PRE_068
    extra = {} if s in PRE_068 else {"offset": "prev" if shift else None}
    if s == "empty":
        return 200, {**off(), "range": range_, "periodSec": None, "startTs": None, "endTs": None, "boxes": None, "wsClients": None, "canary": None, **extra}
    window, step = PERIOD.get(range_, PERIOD["24h"])
    end = int(time.time()) // step * step - (window if shift else 0)
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
                     boxes=boxes, wsClients=ws, canary=canary, **extra)


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


PAGES = ("landing", "app-spread", "app-history", "app-gap", "privacy", "kimp-chart", "kimp-history")
CHANNELS = ("direct", "search", "social", "referral", "inapp", "ai", "campaign")


def _visitors(start: int, end: int, flows_too: bool) -> dict:
    day = 86_400
    first = (start + 32_400) // day * day - 32_400
    days = []
    for d in range(first, end, day):
        c = int(wave(d, 14, 6))
        days.append({"ts": d, "confirmed": c, "shaped": c * 4 + 7, "returning": c // 3})
    c, s = sum(d["confirmed"] for d in days), sum(d["shaped"] for d in days)
    rows = lambda names, k=1: [[txt(n), max(1, c // (i + 2) // k), max(2, s // (i + 2) // k)] for i, n in enumerate(names)]  # noqa: E731
    out = {"state": "ok", "code": None, "sinceTs": start, "confirmed": c, "shaped": s, "returning": c // 3, "capped": False, "days": days,
           "channels": rows(CHANNELS), "devices": rows(("desktop", "mobile", "tablet")), "os": rows(("macos", "windows", "ios", "android")),
           "browsers": rows(("chrome", "safari", "whale", "samsung")), "inApp": rows(("kakaotalk", "naver"), 3)}
    if flows_too:
        rng = random.Random(start)
        flows = []
        for ch in CHANNELS:
            for entry in PAGES[:4]:
                exit_ = rng.choice(PAGES)
                flows.append([txt(ch), txt(entry), txt(exit_), rng.randint(0, 3), rng.randint(1, 12)])
        flows.sort(key=lambda r: -r[4])
        flows = flows[:40] + [["(기타)", "(기타)", "(기타)", 2, 9]]
        out |= {"flows": flows, "entries": rows(PAGES), "exits": rows(PAGES[::-1]),
                "depthPages": {"1": [c // 2, s // 2], "2": [c // 4, s // 5], "3-5": [c // 6, s // 9], "6+": [c // 12, s // 20]}}
    return out


def access(window: str) -> dict:
    now = int(time.time())
    s = state["scenario"]
    # 서버 기록 게이트는 첫 기록보다 앞이다(060 — 사람 결정 2026-10-09): 세 창 모두 열림, 방문자·나라는 늘 센다
    gate_ms = (now - 400 * 86_400) * 1000
    windows = ["24h", "7d", "30d"]
    if window not in windows:
        window = "24h"
    if s == "empty":
        return {**off("no_file"), "window": window, "windows": windows, "gateAt": gate_ms}
    n = {"24h": 24, "7d": 168, "30d": 720}[window]
    start = now - now % 3600 - (n - 1) * 3600
    hourly = []
    for t in range(start, now, 3600):
        pages = int(wave(t, 9, 7))
        hourly.append({"ts": t, "requests": pages * 6 + 20, "pages": pages + 4, "humanPages": pages, "jsViews": pages // 3,
                       "errors": 2 if s == "problem" and t > now - 3600 else 0, "wsErrors": 1 if s == "problem" and t > now - 7200 else 0})
    totals = {k: sum(h[k] for h in hourly) for k in ("requests", "pages", "humanPages", "jsViews")}
    totals |= {"probes": totals["requests"] // 9, "ws": 12, "skipped": 0}
    fives = 12 if s == "problem" else 0
    req = totals["requests"]
    classes = {"browser": [0.41, 0.7], "search": [0.12, 0.05], "ai": [0.06, 0], "preview": [0.01, 0.01], "tool": [0.13, 0],
               "scanner": [0.22, 0.2], "operator": [0.03, 0.04], "unknown": [0.02, 0]}
    top = lambda names: [[txt(n), max(1, req // (9 * (i + 1)))] for i, n in enumerate(names)]  # noqa: E731
    return part(60, window=window, windows=windows, gateAt=gate_ms, startTs=start, endTs=now, firstTs=start,
                totals=totals, hourly=hourly,
                status={"2xx": int(req * 0.8), "3xx": int(req * 0.06), "4xx": int(req * 0.13), "5xx": fives, "ws5xx": 2 if s == "problem" else 0},
                recent5xx=[{"ts": now - 60 * i, "path": txt("/api/history/events"), "status": 502} for i in range(fives)],
                ws={"count": 12, "pairs": 5, "errors": 2 if s == "problem" else 0, "durations": {"lt10s": 2, "lt1m": 3, "lt10m": 4, "lt1h": 2, "ge1h": 1}},
                classes={k: {"requests": int(req * a), "pages": int(totals["pages"] * b)} for k, (a, b) in classes.items()},
                visitors=_visitors(start, now, s != "missing"),
                geo={"state": "ok", "code": None, "month": time.strftime("%Y-%m"), "loadedAt": (now - 3600) * 1000, "sinceTs": start,
                     "countries": [[txt("KR"), 30, 90], ["US", 4, 31], ["JP", 2, 9], ["SG", 0, 12], ["(기타)", 1, 14]],
                     "networks": [["telecom_kr", 28, 70], ["cloud", 2, 41], ["telecom", 5, 22], ["unknown", 0, 3]], "ipv6": 2},
                paths=top(("/", "/app/", "/kimp-chart", "/privacy", "/kimp-history")), tabs=top(("spread", "history", "gap", "flow")),
                referrers=top(("https://www.google.com", "https://search.naver.com", "https://t.co")), utmSources=top(("kakao",)),
                devices=top(("desktop", "mobile", "tablet")), browsers=top(("chrome", "safari", "whale")))


def _dims(seed: int) -> dict:
    """068 차원 여섯 — [[이름, 세션]…](20행까지). xss 경우는 이름마다 HTML 조각."""
    rng = random.Random(seed)
    names = {
        "device": ("PC", "Mobile", "Tablet", "Other"),
        "browser": ("Chrome", "Safari", "Edge", "Samsung Internet", "Whale", "Firefox"),
        "os": ("Windows", "MacOSX", "iOS", "Android", "Linux"),
        "channel": ("Direct", "Organic search", "Referral", "Social", "Other"),
        "source": ("google", "naver", "t.co", "kakao", "github.com"),
        "country": ("South Korea", "United States", "Japan", "Singapore"),
    }
    return {k: [[txt(n), max(1, int(40 / (i + 1) * rng.uniform(0.6, 1.4)))] for i, n in enumerate(v)] for k, v in names.items()}


def _signals(rng: random.Random) -> dict:
    signal = lambda pct, n: {"sessions": n, "sessionPct": pct, "pageViews": n * 2, "count": n * 3}  # noqa: E731
    keys = ("deadClick", "rageClick", "excessiveScroll", "quickback", "scriptError", "errorClick")
    base = (6.1, 1.2, 3.4, 9.8, 0.0, 0.8)
    return {k: signal(round(max(0.0, b + rng.uniform(-1.5, 1.5)), 2) if b else 0.0, rng.randint(0, 5)) for k, b in zip(keys, base, strict=True)}


def clarity() -> dict:
    s = state["scenario"]
    if s == "empty":
        return {**off(None), "nextAt": None, "numOfDays": None, "traffic": None, "summary": None, "countries": None, "metrics": None,
                "pages": off(None), "dims": {**off(None), **dict.fromkeys(("device", "browser", "os", "channel", "source", "country"))}}
    rng = random.Random(7)
    summary = {"scrollDepth": 47.3, "totalSec": 212.0, "activeSec": 64.0, "signals": _signals(rng)}
    now = int(time.time() * 1000)
    kinds = ("landing", "app:spread", "app:history", "app:gap", "privacy", "other")
    groups = []
    for i, page in enumerate(kinds):
        for j, device in enumerate(("mobile", "desktop", "tablet")):
            sessions = max(0, int(30 / (i + 1) / (j + 1)) - j)
            if not sessions:
                continue
            groups.append({"page": txt(page), "device": device, "sessions": sessions, "scrollDepth": round(rng.uniform(20, 90), 2), "totalSec": round(rng.uniform(30, 400), 2),
                           "activeSec": round(rng.uniform(10, 120), 2), **{f"{k}Pct": round(rng.uniform(0, 12), 2) for k in ("deadClick", "rageClick", "excessiveScroll", "quickback", "scriptError", "errorClick")}})
    out = part(21_600, nextAt=now + 3 * 3_600_000, numOfDays=1, traffic={"sessions": 51, "botSessions": 2, "users": 40, "pagesPerSession": 1.8},
               summary=summary, countries=[[txt("South Korea"), 44], ["United States", 3], ["Japan", 2]], metrics=[],
               pages=part(43_200, nextAt=now + 9 * 3_600_000, numOfDays=3, rowsIn=412, rowLimitHit=False, groups=groups))
    # 068 — 차원 하위 부분(기기·유입 하루 한 번). 068 전 서버에는 없다
    if s not in PRE_068:
        out["dims"] = {"state": "ok", "code": None, "fetchedAt": now - 5 * 3_600_000, "refreshSec": 86_400, **_dims(11)}
    return out


def history(days: str) -> tuple[int, dict]:
    """068 GET /admin/clarity/history — 날 오름차순, 기록 없는 날은 빠진다. 배포 뒤 20일째라 그만큼만 있고 한 날이 비었다."""
    s = state["scenario"]
    if s in PRE_068:
        return 404, {"detail": "Not Found"}
    n = {"7": 7, "30": 30, "90": 90}.get(days, 30)
    now = int(time.time())
    head = {"code": None, "fetchedAt": now * 1000, "refreshSec": 60, "days": n}
    if s == "empty":
        return 200, {"state": "unconfigured", **head, "rows": None}
    today = (now + 32_400) // 86_400 * 86_400 - 32_400
    rows = []
    for k in range(min(n, 20) - 1, -1, -1):
        if k == 6:
            continue
        day = today - k * 86_400
        rng = random.Random(day)
        sessions = int(wave(day, 48, 14, day=7 * 86_400))
        dims = _dims(day)
        if k == 3:
            dims["channel"] = dims["source"] = None  # 그날 유입 호출은 실패
        rows.append({
            "date": time.strftime("%Y-%m-%d", time.gmtime(day + 32_400)),
            "fetchedAt": (day + 20 * 3600) * 1000,
            "traffic": {"sessions": sessions, "botSessions": rng.randint(0, 4), "users": int(sessions * 0.8), "pagesPerSession": round(rng.uniform(1.3, 2.4), 2)},
            "summary": {"scrollDepth": round(rng.uniform(35, 60), 2), "totalSec": round(rng.uniform(150, 260), 2), "activeSec": round(rng.uniform(40, 90), 2), "signals": _signals(rng)},
            "countries": [[txt("South Korea"), int(sessions * 0.85)], ["United States", 3]],
            "dims": dims,
        })
    return 200, {"state": "ok", **head, "rows": rows}


NETS = ("telecom_kr", "telecom", "cloud", "other", "unknown")
KINDS = ("browser", "search", "ai", "preview", "tool", "scanner", "operator", "unknown")
BAND_PAGES = ("/", "/app/", "/kimp-chart", "/privacy", "/kimp-history", "/wp-login.php", "/.env")
AGENTS = ("Chrome · Windows · 데스크톱", "Safari · iOS · 휴대폰", "curl", "Googlebot", "python-requests", "Samsung Internet · Android · 휴대폰")
WINDOW_SEC = {"24h": 86_400, "7d": 604_800, "30d": 2_592_000}


def _bands(window: str) -> list[dict]:
    """067 대역 — 경우마다 수(ok 48 · bands 1,200 · nobands 0), 같은 창이면 같은 값(쪽을 넘겨도 어긋나지 않게)."""
    s = state["scenario"]
    n = {"bands": 1200, "nobands": 0}.get(s, 48)
    now = int(time.time())
    span = WINDOW_SEC.get(window, 86_400)
    slots = 24 if window == "24h" else span // 86_400
    out = []
    for i in range(n):
        rng = random.Random(f"{window}:{i}")
        band = f"2001:db8:{i:x}::" if i % 17 == 16 else f"{(i * 37) % 223 + 1}.{(i * 11) % 256}.{(i * 53) % 256}.0"
        net = "cloud" if i % 5 == 1 else NETS[i % len(NETS)]
        requests = int(rng.paretovariate(1.1) * 12) + 1
        kinds = dict.fromkeys(KINDS, 0)
        human = 0 if net == "cloud" and i % 3 else int(requests * rng.uniform(0.2, 0.9))
        kinds["browser"] = human
        kinds["scanner" if net == "cloud" else "search"] = requests - human
        pages = int(human * 0.6)
        fives = 1 if state["scenario"] == "problem" and i < 3 else 0
        first = now - rng.randint(60, span)
        out.append({
            "band": txt(band) if s == "xss" and i == 0 else band,
            "country": txt("KR") if s == "xss" and i == 1 else ("KR" if net == "telecom_kr" else ("US", "JP", "SG", "DE", "(기타)")[i % 5]),
            "net": None if ":" in band else net,
            "requests": requests,
            "pages": pages,
            "js": int(pages * 0.3),
            "ws": int(pages * 0.1),
            "pairs": max(1, int(human / 7)),
            "kinds": kinds,
            "status": {"2xx": requests - fives - int(requests * 0.1), "3xx": 0, "4xx": int(requests * 0.1), "5xx": fives},
            "botPct": round(100 * (requests - human) / requests, 1),
            "first": first,
            "last": rng.randint(first, now),
            "spark": [int(wave(now - k * (span // slots), requests / slots, requests / slots / 2)) for k in range(slots - 1, -1, -1)],
            "topPaths": [[txt(BAND_PAGES[(i + k) % len(BAND_PAGES)]), max(1, requests // (k + 2))] for k in range(3)],
            "topAgents": [[txt(AGENTS[(i + k) % len(AGENTS)]), max(1, requests // (k + 2))] for k in range(3)],
        })
    return out


def ips(query: dict) -> tuple[int, dict]:
    """067 GET /admin/access/ips — 거르기(net·country·q 대역 앞부분·bots=0) 뒤 정렬(requests·pages·last 큰 순)·limit(500까지)·offset."""
    s = state["scenario"]
    if s in PRE_068:
        return 404, {"detail": "Not Found"}
    pick = lambda k, d=None: (query.get(k) or [d])[0]  # noqa: E731
    window = pick("window", "24h") if pick("window") in WINDOW_SEC else "24h"
    if s == "empty":
        return 200, {"state": "unconfigured", "code": "no_file", "window": window, "total": None, "bands": None}
    rows = _bands(window)
    if pick("net"):
        rows = [b for b in rows if b["net"] == pick("net")]
    if pick("country"):
        rows = [b for b in rows if b["country"] == pick("country")]
    if pick("q"):
        rows = [b for b in rows if b["band"].startswith(pick("q"))]
    if pick("bots") == "0":
        rows = [b for b in rows if b["kinds"]["browser"] > 0]
    key = pick("sort", "requests") if pick("sort") in ("requests", "pages", "last") else "requests"
    rows.sort(key=lambda b: -b[key])
    limit = min(500, max(1, int(pick("limit", "200")))) if str(pick("limit", "200")).isdigit() else 200
    offset = int(pick("offset", "0")) if str(pick("offset", "0")).isdigit() else 0
    return 200, {"state": "ok", "code": None, "window": window, "total": len(rows), "bands": rows[offset:offset + limit]}


def ip(query: dict) -> tuple[int, dict]:
    """067 GET /admin/access/ip — 대역 하나: 시간별·종류·상태·브라우저 정보·경로·짝의 첫/마지막 페이지·최근 요청 100줄. 꼴이 틀리면 400."""
    s = state["scenario"]
    if s in PRE_068:
        return 404, {"detail": "Not Found"}
    band = (query.get("band") or [""])[0]
    window = (query.get("window") or ["24h"])[0]
    window = window if window in WINDOW_SEC else "24h"
    if not re.fullmatch(r"(\d{1,3}\.){3}0|[0-9a-fA-F]{1,4}(:[0-9a-fA-F]{1,4}){0,2}::", band):
        return 400, {"detail": "band 꼴이 아니다"}
    found = next((b for b in _bands(window) if b["band"] == band), None)
    if found is None:
        found = {"country": None, "net": None, "kinds": dict.fromkeys(KINDS, 0), "status": {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}, "requests": 0}
    now = int(time.time())
    hours = WINDOW_SEC[window] // 3600
    top = now - now % 3600
    rng = random.Random(band)
    hourly = [{"ts": top - k * 3600, "requests": int(wave(top - k * 3600, found["requests"] / hours * 3, 1)), "pages": 0, "js": 0, "errors": 0} for k in range(hours - 1, -1, -1)]
    for h in hourly:
        h["pages"] = int(h["requests"] * 0.5)
        h["js"] = int(h["pages"] * 0.3)
    recent = [{"ts": now - k * rng.randint(30, 400), "path": txt(BAND_PAGES[k % len(BAND_PAGES)]), "status": (200, 200, 404, 200, 502)[k % 5], "kind": KINDS[k % 3 * 2],
               "browser": txt(("Chrome", "Safari", "")[k % 3]), "os": ("Windows", "iOS", "")[k % 3], "device": ("desktop", "mobile", "")[k % 3],
               "referer": ("https://www.google.com", "", "https://t.co")[k % 3]} for k in range(100)]
    pairs = [{"entry": txt(("landing", "app-spread", "privacy")[k % 3]), "exit": ("app-history", "landing", "kimp-chart")[k % 3], "npages": k % 6 + 1,
              "device": ("desktop", "mobile")[k % 2], "os": ("windows", "ios")[k % 2], "browser": ("chrome", "safari")[k % 2], "channel": ("direct", "search", "social")[k % 3]}
             for k in range(12)]
    return 200, {"state": "ok", "window": window, "band": band, "country": found["country"], "net": found["net"], "hourly": hourly,
                 "kinds": found["kinds"], "status": found["status"], "agents": [[txt(a), 20 - k] for k, a in enumerate(AGENTS)],
                 "paths": [[txt(pth), 30 - k] for k, pth in enumerate(BAND_PAGES)], "pairs": pairs, "recent": recent, "partial": s == "bands"}


def feed(method: str, path: str, query: dict, body: dict) -> tuple[int, object] | None:
    if method == "POST" and path == "/api/refresh":
        if not body.get("token"):
            return 401, {"detail": "X-Refresh-Token 이 틀렸다"}
        return 200, {"snapshots": [], "usdkrw": [], "totalSaved": 1458, "failures": [{"exchange": "okx", "errorCode": txt("timeout"), "message": ""}],
                     "warnings": [txt("USDT 시세 미갱신 경고 없음")], "durationMs": 812, "fetchedAt": int(time.time() * 1000)}
    pick = lambda key, default: (query.get(key) or [default])[0]  # noqa: E731
    table = {
        "/api/health": health,
        "/svc/api/health": lambda: (200, {"status": "ok", "version": "0.1.0", "lastTickAt": int(time.time() * 1000) - 900}),
        "/svc/api/admin/status": lambda: (200, status()),
        "/api/health/collect": lambda: (200, collect()),
        "/api/admin/aws": lambda: (200, aws()),
        "/api/admin/aws/series": lambda: series(pick("range", "24h"), pick("offset", None)),
        "/api/admin/alerts": lambda: (200, alerts()),
        "/svc/api/admin/access": lambda: (200, access(pick("window", "24h"))),
        "/svc/api/admin/access/ips": lambda: ips(query),
        "/svc/api/admin/access/ip": lambda: ip(query),
        "/svc/api/admin/clarity": lambda: (200, clarity()),
        "/svc/api/admin/clarity/history": lambda: history(pick("days", "30")),
    }
    return table[path]() if method == "GET" and path in table else None


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: bytes, kind: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(payload)

    def _route(self, method: str) -> None:
        url = urlsplit(self.path)
        if url.path.startswith("/__scenario/"):
            name = url.path.rsplit("/", 1)[1]
            if name in SCENARIOS:
                state["scenario"] = name
            return self._send(200, json.dumps({"scenario": state["scenario"], "all": SCENARIOS}).encode(), "application/json")
        token = self.headers.get("X-Refresh-Token", "")
        answer = feed(method, url.path, parse_qs(url.query), {"token": token})
        if answer is not None:
            status_code, body = answer
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            kind = "text/html" if isinstance(body, str) else "application/json"
            return self._send(status_code, text.encode(), kind)
        name = "index.html" if url.path == "/" else url.path.lstrip("/")
        target = (ADMIN / name).resolve()
        if method != "GET" or not target.is_file() or ADMIN.resolve() not in target.parents:
            return self._send(404, b'{"detail":"Not Found"}', "application/json")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        cache = "private, max-age=31536000, immutable" if name.startswith("vendor/") else "no-store"
        return self._send(200, target.read_bytes(), kind, cache)

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18064)
    parser.add_argument("--scenario", choices=SCENARIOS, default="ok")
    args = parser.parse_args()
    state["scenario"] = args.scenario
    mimetypes.add_type("text/javascript", ".js")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"관리자 화면 가짜 피드: http://127.0.0.1:{args.port}/  (경우 {args.scenario} — /__scenario/<이름> 으로 바꾼다)")
    server.serve_forever()


if __name__ == "__main__":
    main()
