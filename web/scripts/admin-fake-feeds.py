# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""관리자 화면 v4 확인용 가짜 피드 서버 (스펙 064 §4·066 §4 설계 세션 — 배포 이미지에 들지 않는다).

    uv run web/scripts/admin-fake-feeds.py [--port 18064] [--scenario ok]

- `web/admin` 의 정적 파일을 `/` 에 관리자 nginx 와 같은 헤더(CSP·X-Frame-Options·Cache-Control)로 주고, 화면이 부르는
  피드 열아홉(029·011·034·035·038·039·040·062·063 + 067 대역 둘·068 날별 기록 — 068 은 #133 의 실제 응답 꼴 + 052·061 화면 이용 통계 + 069 IP 둘 + 073 화면 기록 셋
  + 077 Clarity 녹화 목록 — 2026-10-16 실제 응답을 077 대로 다듬은 꼴)과
  `POST /api/refresh` 를 지어낸 값으로 답한다. 표준 라이브러리만 쓰고 127.0.0.1 에만 붙는다. 실제 서버·AWS·Clarity 를 부르지 않는다.
  화면 페이지(065)의 틀은 실제 공개 사이트를 부른다 — 덮어 보기까지 보려면 065 §4 의 시험 사본(kimptrack.com 을 로컬에 묶음)으로.
- 경우(--scenario, 돌던 중에는 `GET /__scenario/<이름>` 로 바꾼다):
  ok(정상) · problem(빨강·주황 칩 여럿, 화면 이용 통계는 Redis 실패 error) · old(067·068 배포 전 — 대역·날별 기록 404, offset 모름,
  Clarity dims 없음) · missing(062·063·067·068 배포 전 — 시계열 404·흐름 키 없음) · empty(연결 안 됨 — AWS·Clarity·접속 로그 없음,
  화면 이용 통계는 시행 전 before_gate) · bands(대역 많음 — 1,200개, 200행씩, 랜딩 요소 300개 + (기타)) · nobands(대역 없음, 화면 이용
  기록 없음) · xss(글자 칸마다 `<img onerror>` — 글로만 보여야 한다, 요소 열쇠 하나도) · expired(헬스가 401 비JSON — 로그인 만료 새로고침 한 번) ·
  noaddrs(069 배포 전 — IP 단위 addrs·addr 만 404, 대역은 그대로) · many(IP 1,200개 — 200행씩, IP 상세는 읽기 상한 partial).
  noclarity(071 — Clarity 는 붙어 있는데 최근 24시간 세션 0, 화면 이용 통계는 그대로 — 운영 2026-10-16 의 모양, 진단 줄이 뜬다).
  069 IP 단위(070): old·missing·noaddrs 는 404, empty 는 연결 안 됨, nobands 는 IP 없음·since 없음, xss 는 근거·도시·망 사업자·PTR 에 `<img onerror>`.
  073·074 화면 기록(live·sessions·session): old·missing·noaddrs 는 404, empty 는 연결 안 됨, problem 은 Redis 실패 error, nobands 는 조각 없음,
  many 는 지금 보는 중 12·지난 조각 200(상한), xss 는 IP·화면·영역 이름에 `<img onerror>`(화면 이름이 틀려 틀은 띄우지 않는다). 그 밖은 지금 보는 중
  하나(시간이 가면 묶음이 늘어난다 — 따라 보기)·랜딩 PC·대시보드 휴대폰 대본 + 지난 조각 9. 없는 sid 는 meta null, 꼴이 틀리면 400 bad_sid.
  077 Clarity 녹화 목록(recordings): old·missing·noaddrs 는 404, empty 는 연결 안 됨, problem 은 error http_429, nobands·noclarity 는 녹화 없음,
  many 는 100(상한), xss 는 제목·클릭 글자에 `<img onerror>`·재생 링크 둘이 Clarity 밖 주소(화면이 링크로 만들지 않아야 한다). 그 밖은 기간마다 몇 개.
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
SCENARIOS = ("ok", "problem", "old", "missing", "empty", "bands", "nobands", "xss", "expired", "noaddrs", "many", "noclarity")
# 067·068 이 없는 서버(배포 전·그보다 앞)
PRE_068 = ("old", "missing")
# 069(IP 단위 addrs·addr)가 없는 서버 — 067·068 전 둘과, 067·068 만 있는 noaddrs
PRE_069 = (*PRE_068, "noaddrs")
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
            "reconnects24h": {"okx": 14, "bitget": 9, "bybit": 5}.get(ex, 0),  # 080 짧은 재연결
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
                     "countries": [[txt("KR"), 30, 90], ["US", 4, 31], ["SG", 0, 12], ["JP", 2, 9], ["DE", 0, 3], ["VN", 1, 2], ["BR", 0, 1], ["(기타)", 0, 2]],
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
    if s == "noclarity":
        zero = {"sessions": 0, "sessionPct": 0.0, "pageViews": 0, "count": 0}
        return part(21_600, nextAt=now + 3 * 3_600_000, numOfDays=1, traffic={"sessions": 0, "botSessions": 0, "users": None, "pagesPerSession": None},
                    summary={"scrollDepth": None, "totalSec": None, "activeSec": None, "signals": {k: dict(zero) for k in _signals(rng)}},
                    countries=[], metrics=[], pages=part(43_200, nextAt=now + 9 * 3_600_000, numOfDays=3, rowsIn=0, rowLimitHit=False, groups=[]),
                    dims={"state": "ok", "code": None, "fetchedAt": now - 5 * 3_600_000, "refreshSec": 86_400, **dict.fromkeys(("device", "browser", "os", "channel", "source", "country"), [])})
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


# 077 — 녹화 하나의 지나간 페이지 대본: (주소, 제목, 머문 ms, [(클릭 글자, 페이지 안 초)…])
REC_PAGES = (
    ("https://kimptrack.com/", "김프 실시간 — KimpTrack", 21_000, [("대시보드 열기", 18)]),
    ("https://kimptrack.com/app/?tab=spread", "KimpTrack", 30_340, [("기록/통계", 30)]),
    ("https://kimptrack.com/app/?tab=history", "KimpTrack", 44_000, [("BTC", 6), ("7일", 15)]),
    ("https://kimptrack.com/kimp-chart", "김프 차트 보는 법 — KimpTrack", 12_000, []),
    ("https://kimptrack.com/privacy", "개인정보 처리방침 — KimpTrack", 9_000, [("동의 철회", 7)]),
    ("https://kimptrack.com/app/?tab=gap", "KimpTrack", 15_000, [("선물–현물 갭", 2)]),
)
REC_SCRIPTS = ((0, 1, 2), (1,), (0, 4), (3, 2), (1, 5, 2), (0,), (1, 2))


def _rec(i: int, start: int) -> dict:
    """녹화 하나 — 077 §3.2 의 행 꼴. xss 는 제목·클릭 글자에 HTML 조각, 0·1 번 링크는 Clarity 밖 주소."""
    rng = random.Random(i)
    at, timeline = 0, []
    for k in REC_SCRIPTS[i % len(REC_SCRIPTS)]:
        url, title, stay, clicks = REC_PAGES[k]
        stay = int(stay * rng.uniform(0.5, 1.6))
        events = [{"type": "Click", "text": txt(t), "atSec": at + min(s, stay // 1000)} for t, s in clicks]
        timeline.append({"url": url, "title": txt(title), "atSec": at, "stayMs": stay, "loadMs": rng.randint(250, 1400),
                         "referrer": "https://www.google.com" if not timeline and i % 3 == 0 else None, "events": events})
        at += stay // 1000
    total = at + rng.randint(0, 20)
    link = f"https://clarity.microsoft.com/player/fakeproject/fakeuser{i}/fakesession{i}"
    if state["scenario"] == "xss" and i < 2:
        link = ("javascript:alert(1)", f'https://clarity.microsoft.com/player/x"><img src=x onerror=alert(1)>')[i]
    return {"startedAt": start, "totalSec": total, "activeSec": max(1, int(total * rng.uniform(0.3, 1.0))),
            "pages": len(timeline), "clicks": sum(len(p["events"]) for p in timeline), "link": link, "timeline": timeline}


def recordings(days: str) -> tuple[int, dict]:
    """077 GET /admin/clarity/recordings — days 1·3·7·30(목록 밖은 3), 시작 최근 순, 창마다 30분 캐시라 받은 때가 조금 전."""
    s = state["scenario"]
    if s in PRE_069:
        return 404, {"detail": "Not Found"}
    n = {"1": 1, "3": 3, "7": 7, "30": 30}.get(days, 3)
    now = int(time.time() * 1000)
    head = {"code": None, "fetchedAt": now - 4 * 60_000, "refreshSec": 1800}
    if s == "empty":
        return 200, {"state": "unconfigured", **head, "fetchedAt": None, "rows": None, "days": n}
    if s == "problem":
        return 200, {"state": "error", **head, "code": "http_429", "fetchedAt": None, "rows": None, "days": n}
    count = 0 if s in ("nobands", "noclarity") else 100 if s == "many" else {1: 3, 3: 7, 7: 12, 30: 26}[n]
    step = n * 86_400_000 // (count + 1) if count else 0
    rows = [_rec(i, now - 4 * 60_000 - (i + 1) * step) for i in range(count)]
    return 200, {"state": "ok", **head, "rows": rows, "days": n}


NETS = ("telecom_kr", "telecom", "cloud", "other")
KINDS = ("browser", "search", "ai", "preview", "tool", "scanner", "operator", "unknown")
BAND_PAGES = ("/", "/app/", "/kimp-chart", "/privacy", "/kimp-history", "/wp-login.php", "/.env")
AGENTS = ("desktop·windows·chrome", "mobile·ios·safari", "(기타)", "mobile·android·samsung", "desktop·macos·safari", "tablet·ios·safari")
WINDOW_SEC = {"24h": 86_400, "7d": 604_800, "30d": 2_592_000}


def _bands(window: str) -> list[dict]:
    """067(#132) 대역 — 경우마다 수(ok 48 · bands 1,200 · nobands 0) + 모음 행 '(없음)'·'(기타)'. 같은 창이면 같은 값(쪽을 넘겨도
    어긋나지 않게). band 는 접미까지 든 글자, IPv6 의 net 은 'ipv6', 모음 행의 나라·망은 null, spark 는 24시간 24칸·7일 8칸·30일 31칸."""
    s = state["scenario"]
    n = {"bands": 1200, "nobands": 0}.get(s, 48)
    now = int(time.time())
    span = WINDOW_SEC.get(window, 86_400)
    slots = {"24h": 24, "7d": 8, "30d": 31}.get(window, 24)
    out = []
    for i in range(n + (2 if n else 0)):
        rng = random.Random(f"{window}:{i}")
        band = f"2001:db8:{i:x}::/48" if i % 17 == 16 else f"{(i * 37) % 223 + 1}.{(i * 11) % 256}.{(i * 53) % 256}.0/24"
        if i >= n:
            band = ("(없음)", "(기타)")[i - n]
        net = "ipv6" if ":" in band else "cloud" if i % 5 == 1 else NETS[i % len(NETS)]
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
            "country": None if i >= n or ":" in band else txt("KR") if s == "xss" and i == 1 else ("KR" if net == "telecom_kr" else ("US", "JP", "SG", "DE", "FR")[i % 5]),
            "net": None if i >= n else net,
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
            "topAgents": [[txt(("desktop·windows·chrome", "mobile·ios·safari", "(기타)")[k]), max(1, requests // (k + 2))] for k in range(3)],
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
    rows = _bands(window)  # 틀린 인자는 오류 없이 기본값
    if pick("net"):
        rows = [b for b in rows if b["net"] == pick("net")]
    if pick("country"):
        rows = [b for b in rows if b["country"] == pick("country")]
    if pick("q"):
        rows = [b for b in rows if b["band"].startswith(pick("q"))]
    if pick("bots") == "0":
        rows = [b for b in rows if b["kinds"]["browser"] > 0]
    key = pick("sort", "requests") if pick("sort") in ("requests", "pages", "last") else "requests"
    rows.sort(key=lambda b: (-b[key], b["band"]))  # 큰 순, 같으면 대역 글자 순
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
    if not re.fullmatch(r"(\d{1,3}\.){3}0/24|[0-9a-f]{1,4}(:[0-9a-f]{1,4}){0,2}::/48", band):
        return 400, {"error": {"code": "bad_band", "message": "band 꼴이 아니다", "detail": None}}
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
    at = now
    recent = []
    for k in range(100):
        at -= rng.randint(30, 400)  # 최근 순
        recent.append({"ts": at, "path": txt(BAND_PAGES[k % len(BAND_PAGES)]), "status": (200, 200, 404, 200, 502)[k % 5], "kind": KINDS[k % 3 * 2],
                       "browser": txt(("chrome", "safari", "")[k % 3]), "os": ("windows", "ios", "")[k % 3], "device": ("desktop", "mobile", "")[k % 3],
                       "referer": ("https://www.google.com", None, "https://t.co")[k % 3]})
    pairs = [{"entry": txt(("landing", "app-spread", "privacy")[k % 3]), "exit": ("app-history", "landing", "kimp-chart")[k % 3], "npages": k % 6 + 1,
              "device": ("desktop", "mobile")[k % 2], "os": ("windows", "ios")[k % 2], "browser": ("chrome", "safari")[k % 2], "channel": ("direct", "search", "social")[k % 3]}
             for k in range(12)]
    return 200, {"state": "ok", "code": None, "window": window, "band": band, "partial": s == "bands", "country": found["country"], "net": found["net"], "hourly": hourly,
                 "kinds": found["kinds"], "status": found["status"], "agents": [[txt(a), 20 - k] for k, a in enumerate(AGENTS)],
                 "paths": [[txt(pth), 30 - k] for k, pth in enumerate(BAND_PAGES)], "pairs": pairs, "recent": recent}


# 069 IP 단위(070 이 그린다) — 줄의 꼴(사람·검색 로봇·스캐너·의심·모름·운영자)마다 근거·점수·판정, 위치·망 사업자(null 섞임)
ARCHETYPES = (
    ("human", "telecom_kr", (("js", "자바스크립트가 돈 페이지 {js}번", -40), ("ws", "실시간 연결(WS) {ws}번", -30), ("assets", "정적 자원 요청 {a}번", -15), ("search_ref", "검색 엔진에서 들어온 페이지 1번", -10))),
    ("googlebot", "cloud", (("ua_bot", "검색 로봇 이름(Googlebot)", 70), ("cloud", "데이터센터 망", 20), ("robots", "robots.txt 1번", 10))),
    ("scanner", "cloud", (("probe", "탐색 경로 12번(/.env·/wp-login.php…)", 60), ("ua_tool", "자동화 도구 이름(python-requests)", 50), ("many_404", "404 가 14번 — 요청의 45%", 15))),
    ("suspect", "cloud", (("no_js", "사람 모양 페이지 5번인데 자바스크립트 신호 0", 30), ("cloud", "데이터센터 망", 20))),
    ("human", "telecom_kr", (("js", "자바스크립트가 돈 페이지 {js}번", -40), ("assets", "정적 자원 요청 {a}번", -15))),
    ("unknown", "other", (("cloud", "데이터센터 망", 20),)),
    ("operator", "telecom_kr", ()),
    ("suspect", "telecom", (("burst", "1분에 요청 72번", 20), ("no_assets", "페이지 4번인데 정적 자원 0", 15), ("ua_many", "서로 다른 브라우저 정보 6개", 10))),
)
PLACES = {
    "telecom_kr": (("KR", "Seoul", "Seoul", 4766, "Korea Telecom"), ("KR", "Gyeonggi-do", "Suwon", 9318, "SK Broadband Co Ltd"), ("KR", "Busan", None, 3786, "LG POWERCOMM")),
    "cloud": (("US", "Virginia", "Ashburn", 16509, "Amazon.com, Inc."), ("US", "California", "Mountain View", 15169, "Google LLC"), ("SG", None, "Singapore", 14061, None)),
    "telecom": (("JP", "Tokyo", "Tokyo", 2516, "KDDI CORPORATION"), ("DE", "Hesse", None, 3320, "Deutsche Telekom AG")),
    "other": (("FR", None, None, None, None),),
}


def _seed(text: str) -> int:
    """글자 → 늘 같은 수(파이썬 hash 는 프로세스마다 바뀐다)."""
    return sum((k + 1) * ord(ch) for k, ch in enumerate(text))


def _verdict(score: int, requests: int, kind: str) -> str:
    if kind == "operator":
        return "operator"
    if requests < 3:
        return "unknown"
    return "bot" if score >= 60 else "suspect" if score >= 30 else "human"


def _addr_ip(i: int) -> str:
    """069 IP 행 i 의 주소(열한 번째마다 IPv6) — 074 화면 기록 조각도 같은 IP 를 쓴다."""
    if i % 11 == 10:
        return f"2001:db8:{i:x}::{i % 7 + 1:x}"
    return f"{(i * 37) % 223 + 1}.{(i * 11) % 256}.{(i * 53) % 256}.{0 if i % 13 == 12 else (i * 7) % 254 + 1}"


def _addrs(window: str) -> tuple[list[dict], int]:
    """069 IP 행 — 경우마다 수(ok 40 · many 1,200 · nobands 0) + 가린 행(전체 저장 전 대역 여섯) + '(없음)'. 같은 창이면 같은 값."""
    s = state["scenario"]
    n = {"many": 1200, "nobands": 0}.get(s, 40)
    now = int(time.time())
    since = now - 3 * 86_400
    span = WINDOW_SEC.get(window, 86_400)
    slots = {"24h": 24, "7d": 8, "30d": 31}.get(window, 24)
    out = []
    for i in range(n + (7 if n else 0)):
        rng = random.Random(f"addr:{window}:{i}")
        kind, net, rules = ARCHETYPES[i % len(ARCHETYPES)]
        masked = i >= n
        ip = _addr_ip(i)
        if masked:
            ip = "(없음)" if i == n + 6 else f"{(i * 29) % 223 + 1}.{(i * 3) % 256}.{(i * 17) % 256}.0/24"
        if ":" in ip:
            net = "ipv6"
        country, region, city, asn, org = rng.choice(PLACES.get(net, PLACES["other"])) if net != "ipv6" else (None, None, None, None, None)
        requests = 2 if kind == "unknown" else int(rng.paretovariate(1.2) * 15) + 3
        pages = 0 if kind in ("scanner", "googlebot") else int(requests * 0.4) + 1
        js = int(pages * 0.7) if kind == "human" else 0
        ws = 1 if kind == "human" and i % 2 == 0 else 0
        reasons = [{"code": c, "text": txt(t.format(js=js, ws=ws, a=pages * 3) + (" (대역)" if masked else "")), "weight": w} for c, t, w in rules if not (c == "ws" and not ws)]
        score = sum(r["weight"] for r in reasons)
        kinds = dict.fromkeys(KINDS, 0)
        kinds[{"googlebot": "search", "scanner": "scanner", "operator": "operator"}.get(kind, "browser")] = requests
        first = (now - rng.randint(span // 2, span)) if masked else max(since, now - rng.randint(60, span))
        last = min(since, first + 3600) if masked else rng.randint(first, now)
        out.append({
            "ip": txt(ip) if s == "xss" and i == 0 else ip,
            "masked": masked and ip != "(없음)", "country": None if ip == "(없음)" else txt(country) if s == "xss" and i == 1 else country,
            "region": region, "city": txt(city) if s == "xss" and city else city, "asn": asn, "org": txt(org) if s == "xss" and org else org, "net": None if ip == "(없음)" else net,
            "requests": requests, "pages": pages, "js": js, "ws": ws, "kinds": kinds,
            "status": {"2xx": requests - requests // 5, "3xx": 0, "4xx": requests // 5, "5xx": 0},
            "first": first, "last": last,
            "spark": [int(wave(now - k * (span // slots), requests / slots, requests / slots / 2)) for k in range(slots - 1, -1, -1)],
            "topPaths": [[txt(BAND_PAGES[(i + k) % len(BAND_PAGES)]), max(1, requests // (k + 2))] for k in range(3)],
            "topAgents": [[txt("Googlebot/2.1" if kind == "googlebot" else AGENTS[k]), max(1, requests // (k + 2))] for k in range(2)],
            "score": None if kind == "operator" else score, "verdict": "unknown" if ip == "(없음)" else _verdict(score, requests, kind), "reasons": reasons,
        })
    return out, since


def addrs(query: dict) -> tuple[int, dict]:
    """069 GET /admin/access/addrs — 거르기(net·country·verdict 쉼표·q 앞부분·masked=0) 뒤 정렬(requests·pages·last·score 큰 순, 같으면 ip)·쪽."""
    s = state["scenario"]
    if s in PRE_069:
        return 404, {"detail": "Not Found"}
    pick = lambda k, d=None: (query.get(k) or [d])[0]  # noqa: E731
    window = pick("window", "24h") if pick("window") in WINDOW_SEC else "24h"
    if s == "empty":
        return 200, {"state": "unconfigured", "code": "no_file", "window": window, "total": None, "since": None, "rows": None}
    rows, since = _addrs(window)
    for key in ("net", "country"):
        if pick(key):
            rows = [r for r in rows if r[key] == pick(key)]
    if pick("verdict"):
        rows = [r for r in rows if r["verdict"] in pick("verdict").split(",")]
    if pick("q"):
        rows = [r for r in rows if r["ip"].startswith(pick("q"))]
    if pick("masked") == "0":
        rows = [r for r in rows if not r["masked"]]
    key = pick("sort") if pick("sort") in ("requests", "pages", "last", "score") else "requests"
    rows.sort(key=lambda r: (-(r[key] if r[key] is not None else -1e9), r["ip"]))
    limit = min(500, max(1, int(pick("limit", "200")))) if str(pick("limit", "200")).isdigit() else 200
    offset = int(pick("offset", "0")) if str(pick("offset", "0")).isdigit() else 0
    return 200, {"state": "ok", "code": None, "window": window, "total": len(rows), "since": since if s != "nobands" else None, "rows": rows[offset:offset + limit]}


def addr(query: dict) -> tuple[int, dict]:
    """069 GET /admin/access/addr — IP 하나(또는 가린 대역): 목록 값 + geo·verifiedCrawler·067 상세 값·recent 의 flags. 꼴이 틀리면 400 bad_ip."""
    s = state["scenario"]
    if s in PRE_069:
        return 404, {"detail": "Not Found"}
    ip_ = (query.get("ip") or [""])[0]
    window = (query.get("window") or ["24h"])[0]
    window = window if window in WINDOW_SEC else "24h"
    if not re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}|[0-9a-f:]*:[0-9a-f:]*|(\d{1,3}\.){3}0/24|[0-9a-f]{1,4}(:[0-9a-f]{1,4}){0,2}::/48", ip_):
        return 400, {"error": {"code": "bad_ip", "message": "ip 꼴이 아니다", "detail": None}}
    rows, _ = _addrs(window)
    found = next((r for r in rows if r["ip"] == ip_), None)
    if found is None:  # 창 안에 없는 IP — 빈 행(수 0)
        found = {"ip": ip_, "masked": "/" in ip_, "country": None, "region": None, "city": None, "asn": None, "org": None, "net": None, "requests": 0, "pages": 0,
                 "js": 0, "ws": 0, "kinds": dict.fromkeys(KINDS, 0), "status": {}, "first": None, "last": None, "spark": [], "topPaths": [], "topAgents": [],
                 "score": 0, "verdict": "unknown", "reasons": []}
    _, band = ip({"band": ["1.2.3.0/24"], "window": [window]})
    google = any(r["code"] == "ua_bot" for r in found["reasons"])
    impostor = google and _seed(ip_) % 2 == 1  # 검색 로봇 이름 행의 절반은 주소 확인 실패
    reasons = [*found["reasons"], {"code": "fake_crawler", "text": txt("검색 로봇 이름을 쓰지만 주소 확인 실패"), "weight": 30}] if impostor else found["reasons"]
    score = None if found["score"] is None else sum(r["weight"] for r in reasons)
    flags = (("probe", "404"), ("asset",), ("js",), ("ws",), (), ("404",))
    recent = [{**r, "flags": list(flags[k % len(flags)])} for k, r in enumerate(band["recent"])]
    lat, lon = {"Seoul": (37.5665, 126.978), "Suwon": (37.2636, 127.0286), "Ashburn": (39.0438, -77.4874), "Tokyo": (35.6895, 139.6917)}.get(found["city"] or "", (None, None))
    ptr = ("crawl-66-249-66-1.googlebot.com" if google and not impostor else None) if google else (None if found["masked"] or found["org"] is None else f"host-{_seed(ip_) % 997}.example.net")
    return 200, {"state": "ok", "code": None, "window": window, **found, "reasons": reasons, "score": score, "partial": s == "many",
                 "geo": {"country": found["country"], "region": found["region"], "city": found["city"], "lat": lat, "lon": lon, "asn": found["asn"],
                         "org": found["org"], "net": found["net"], "ptr": txt(ptr) if s == "xss" and ptr else ptr, "approx": True},
                 "verifiedCrawler": (not impostor) if google else None,
                 **{k: band[k] for k in ("hourly", "agents", "paths", "pairs")}, "recent": recent}


# 052·061 화면 이용 통계 — 화면마다 영역(052 목록)과 요소 열쇠(061 §7 목록 + 자동 열쇠 보기). 대시보드 영역은 공통 셋 + 탭 영역
ATT_AREAS = {
    "landing": ("top", "hero", "analyze", "events", "kimp", "method", "faq", "foot"),
    "privacy": ("top", "changes", "glance", "consent", *(f"s{n}" for n in range(1, 13)), "foot"),
    "kimp-chart": ("top", "what", "read", "coins", "faq", "foot"),
    "kimp-history": ("top", "live", "what", "how", "faq", "foot"),
    "app-spread": ("header", "tabs", "kpi", "filters", "table"),
    "app-history": ("header", "tabs", "kpi", "filters", "table", "summary", "list", "filters-2", "chart"),
    "app-gap": ("header", "tabs", "kpi", "filters", "table"),
    "app-pp": ("header", "tabs", "kpi", "filters", "table"),
    "app-health": ("header", "tabs", "kpi", "summary", "cards", "chart", "list"),
    "app-flow": ("header", "tabs", "kpi", "filters", "table", "table-2"),
}
TABS_EL = tuple(("tabs", f"tab:{t}") for t in ("spread", "history", "gap", "pp", "health", "flow"))
ATT_ELEMENTS = {
    "landing": (("top", "nav:kimp"), ("top", "nav:method"), ("top", "nav:faq"), ("top", "link:/"), ("hero", "app:history"),
                *(("hero", f"live:{n}") for n in range(1, 6)), ("hero", "link:/app/"), ("analyze", "link:/kimp-history"),
                ("analyze", "link:/kimp-chart"), ("events", "row:BTC"), ("events", "row:ETH"), ("faq", "faq:why-differ"),
                ("faq", "faq:why-1000"), ("faq", "faq:rate"), ("faq", "faq:blocked"), ("faq", "faq:cost"), ("faq", "faq:reverse-term"),
                ("faq", "faq:chart"), ("foot", "app:history"), ("foot", "consent-settings"), ("foot", "link:mailto"), ("foot", "link:/privacy")),
    "privacy": (("top", "link:/"), ("consent", "btn:동의 철회"), ("consent", "btn:선택한 대로 저장"), ("consent", "btn:모두 거부"),
                ("consent", "check:checkbox"), ("changes", "link:/privacy-20261009.html")),
    "kimp-chart": (("top", "link:/app/"), *(("coins", f"coin:{c}") for c in ("btc", "eth", "xrp", "sol", "doge", "all")),
                   ("faq", "faq:realtime"), ("faq", "faq:vs-price"), ("faq", "faq:apps"), ("faq", "faq:fees"), ("foot", "nav:kimp")),
    "kimp-history": (("live", "row:BTC"), ("live", "row:SOL"), ("faq", "faq:range"), ("faq", "faq:max-vs-live"), ("faq", "faq:profit"), ("foot", "nav:kimp")),
    "app-spread": (*TABS_EL, ("filters", "search"), ("filters", "dom:all"), ("filters", "dom:upbit"), ("filters", "threshold"), ("filters", "only-over"),
                   ("filters", "fx:binance"), ("filters", "fx:okx"), ("table", "sort:sym"), ("table", "sort:val"), ("table", "row:BTC"),
                   ("table", "row:ETH"), ("table", "row:DOGE")),
    "app-history": (*TABS_EL, ("filters", "dir:kimp"), ("filters", "period:7d"), ("filters", "period:30d"), ("filters", "search"),
                    ("table", "sort:cnt"), ("table", "row:BTC"), ("filters-2", "interval:1h"), ("filters-2", "chart-dom:upbit"),
                    ("chart", "card:binance"), ("chart", "fold:binance")),
    "app-gap": (*TABS_EL, ("filters", "search"), ("filters", "mode:entry"), ("table", "sort:gap")),
    "app-pp": (*TABS_EL, ("filters", "search"), ("filters", "threshold-price"), ("table", "sort:fund")),
    "app-health": TABS_EL,
    "app-flow": (*TABS_EL, ("filters", "window:1h"), ("filters", "dir:in"), ("filters", "search"), ("table", "row:BTC"), ("table-2", "tx")),
}
# (화면, 기기) → 창 7일의 pv — 랜딩 PC 가 가장 크다(기본 화면), 선선갭 PC 는 표본 적음, 선선갭·수집 상태 휴대폰은 기록 없음
ATT_PV = {("landing", "pc"): 240, ("landing", "mobile"): 130, ("app-spread", "pc"): 180, ("app-history", "pc"): 64, ("app-gap", "pc"): 21,
          ("app-pp", "pc"): 3, ("app-health", "pc"): 9, ("app-flow", "pc"): 12, ("app-spread", "mobile"): 14, ("privacy", "pc"): 16,
          ("privacy", "mobile"): 5, ("kimp-chart", "pc"): 35, ("kimp-chart", "mobile"): 22, ("kimp-history", "pc"): 28, ("kimp-history", "mobile"): 11}
ATT_DEPTH_PAGES = ("landing", "privacy", "kimp-chart", "kimp-history")


def _att_row(page: str, device: str, scale: float, with_elements: bool) -> dict | None:
    pv = int(ATT_PV.get((page, device), 0) * scale)
    if pv <= 0:
        return None
    rng = random.Random(f"{page}:{device}")
    areas = [{"id": a, "ms": int(pv * rng.uniform(800, 9000) / (i + 1)), "clicks": int(pv * rng.uniform(0, 0.4)),
              "seen": min(pv, int(pv * rng.uniform(0.05, 1.0)))} for i, a in enumerate(ATT_AREAS[page])]
    areas.sort(key=lambda a: (-a["ms"], a["id"]))
    exits = [[a["id"], max(1, int(pv * 0.5 / (i + 2)))] for i, a in enumerate(areas[:4])]
    depth = None
    if page in ATT_DEPTH_PAGES:
        depth = [int(pv * w) for w in (0.04, 0.08, 0.1, 0.12, 0.12, 0.1, 0.08, 0.07, 0.05, 0.04, 0.1)]
    row = {"page": page, "device": device, "pv": pv, "areas": areas, "exits": exits, "depth": depth}
    if with_elements:
        elements = []
        for area, key in ATT_ELEMENTS[page]:
            clicks = int(pv * rng.uniform(0, 0.3)) if rng.random() > 0.2 else 0
            row_key = key.startswith("row:")
            elements.append({"area": area, "key": key, "clicks": clicks,
                             "hoverMs": 0 if device == "mobile" or row_key else int(pv * rng.uniform(0, 900)),
                             "visMs": 0 if row_key else int(pv * rng.uniform(0, 6000))})
        if state["scenario"] == "bands" and page == "landing":  # 피드 상한 300 — 열쇠 300 을 넘은 짝은 (기타)
            elements += [{"area": "faq", "key": f"btn:질문 {i}", "clicks": 1, "hoverMs": 0, "visMs": 400} for i in range(299 - len(elements))]
            elements.append({"area": "faq", "key": "(기타)", "clicks": 2, "hoverMs": 0, "visMs": 900})
        if state["scenario"] == "xss":
            elements += [{"area": areas[0]["id"], "key": X, "clicks": 9, "hoverMs": 0, "visMs": 100},
                         {"area": X, "key": "btn:x", "clicks": 9, "hoverMs": 0, "visMs": 100}]
        elements.sort(key=lambda e: (-e["clicks"], -e["visMs"], e["area"], e["key"]))
        row["elements"] = elements[:300]
    return row


def attention(query: dict) -> dict:
    """052·061 GET /admin/attention — days(1·7·30·90, 목록 밖은 7)·page(목록 안이면 그 화면 행만 + elements). empty 는 시행 전(before_gate),
    problem 은 Redis 실패(error), nobands 는 기록 없음(rows 빔). 창이 길수록 pv 가 커진다."""
    days = {"1": 1, "7": 7, "30": 30, "90": 90}.get((query.get("days") or ["7"])[0], 7)
    page = (query.get("page") or [None])[0]
    page = page if page in ATT_AREAS else None
    now = int(time.time())
    today = time.strftime("%Y-%m-%d", time.gmtime(now + 32_400))
    body = {"state": "ok", "code": None, "gateAt": (now - 40 * 86_400) * 1000, "days": days,
            "from": time.strftime("%Y-%m-%d", time.gmtime(now + 32_400 - (days - 1) * 86_400)), "to": today, "fetchedAt": now * 1000, "rows": []}
    s = state["scenario"]
    if s == "empty":
        return {**body, "state": "before_gate", "from": today}
    if s == "problem":
        return {**body, "state": "error", "code": "redis"}
    if s == "nobands":
        return body
    scale = {1: 0.2, 7: 1.0, 30: 3.4, 90: 8.0}[days]
    rows = [_att_row(p, d, scale, page is not None) for p in ATT_AREAS for d in ("mobile", "pc") if page is None or p == page]
    return {**body, "rows": [r for r in rows if r]}


# 073·074 화면 기록 — 조각(sid)마다 사건 대본(t = 조각 시작부터 ms)을 짓고 2초 창마다 묶음 하나(받은 시각 at = 창 끝 + 80ms, 빈 창은 보내지
# 않는다). 랜딩 PC(스크롤·누름·20초 숨김·화면 크기 바뀜)·대시보드 휴대폰(손가락 끌기·영역 안 스크롤·탭 바꿈 둘)·지금 보는 중 하나(시간이 가면
# 묶음이 늘어난다 — 따라 보기) + 지난 조각들. IP 는 069 IP 행과 같은 주소.
RP_PAGES = tuple(ATT_AREAS)
RP_WINDOW = 2_000
RP_CACHE: dict[tuple, list] = {}


def _rp_move(ev: list, t0: int, t1: int, a: tuple[int, int], b: tuple[int, int], step: int = 150) -> None:
    for t in range(t0, t1, step):
        f = (t - t0) / (t1 - t0)
        ev.append([t, "m", round(a[0] + (b[0] - a[0]) * f + 18 * math.sin(t / 290)), round(a[1] + (b[1] - a[1]) * f)])


def _rp_scroll(ev: list, t0: int, frm: int, to: int, steps: int) -> None:
    for k in range(steps):
        ev.append([t0 + k * 200, "s", round(frm + (to - frm) * (k + 1) / steps)])


def _rp_swipe(ev: list, t0: int, area: str, top0: int, top1: int, x: int = 200) -> None:
    for k in range(8):
        ev.append([t0 + k * 100, "m", x, 620 - k * 40])
        ev.append([t0 + k * 100 + 50, "e", area, 0, top0 + (top1 - top0) * (k + 1) // 8])


def _rp_landing() -> list:
    """랜딩 PC 95초 — 첫 화면 누름 · 사건 표로 스크롤 · 20초 숨김 · 질문으로 스크롤 · 누름 둘 · 화면 크기 바뀜 · 위로 스크롤."""
    ev: list = [[300, "a", "hero"]]
    _rp_move(ev, 0, 4000, (640, 300), (720, 520))
    ev.append([4100, "c", 720, 520, "hero"])
    _rp_move(ev, 4200, 7000, (720, 520), (900, 400))
    _rp_scroll(ev, 8000, 0, 900, 15)
    ev.append([11200, "a", "events"])
    _rp_move(ev, 11500, 15000, (600, 300), (500, 450))
    ev.append([15200, "c", 500, 450, "events"])
    ev += [[18000, "v", 0], [38000, "v", 1]]
    _rp_move(ev, 38500, 41000, (500, 450), (640, 600))
    _rp_scroll(ev, 42000, 900, 2600, 20)
    ev.append([46200, "a", "faq"])
    _rp_move(ev, 46500, 52000, (640, 600), (300, 350))
    ev.append([52200, "c", 300, 350, "faq"])
    _rp_move(ev, 52400, 58000, (300, 350), (320, 520))
    ev.append([58200, "c", 320, 520, "faq"])
    ev.append([65000, "r", 1280, 720])
    _rp_move(ev, 66000, 72000, (320, 520), (1000, 120))
    _rp_scroll(ev, 80000, 2600, 0, 15)
    ev.append([83200, "a", "hero"])
    _rp_move(ev, 84000, 95000, (1000, 120), (640, 360))
    return ev


def _rp_dash() -> list:
    """대시보드 휴대폰 80초 — 김프 표 끌기 · 탭 누름 → 기록 탭 · 사건 로그 끌기 · 탭 누름 → 갭 탭 · 12초 숨김 · 필터 누름."""
    ev: list = [[200, "a", "table"]]
    _rp_swipe(ev, 2000, "table", 0, 600)
    _rp_swipe(ev, 6000, "table", 600, 1400)
    ev += [[10000, "m", 120, 150], [10050, "c", 120, 150, "tabs"], [10300, "p", "app-history"], [10800, "a", "list"]]
    _rp_swipe(ev, 14000, "list", 0, 900, x=180)
    ev += [[20000, "m", 200, 500], [20050, "c", 200, 500, "table"]]
    _rp_scroll(ev, 24000, 0, 420, 4)
    ev += [[30000, "m", 300, 150], [30050, "c", 300, 150, "tabs"], [30300, "p", "app-gap"], [30800, "a", "table"]]
    _rp_swipe(ev, 36000, "table", 0, 700)
    ev += [[45000, "v", 0], [57000, "v", 1]]
    _rp_swipe(ev, 60000, "table", 700, 200)
    ev += [[70000, "m", 200, 700], [70050, "c", 200, 700, "filters"], [80000, "a", "filters"]]
    return ev


def _rp_wander(sid: str, page: str, dur: int) -> list:
    """그 밖 조각 — 초마다 같은 난수(조각·초)로 포인터가 떠돌고 가끔 누름·스크롤·영역 바뀜, 80초마다 20초 쉼(빈 시간). 대시보드는 5분마다 탭."""
    ev: list = []
    dash = page.startswith("app-")
    tabs = [p for p in RP_PAGES if p.startswith("app-")]
    cur = page
    for sec in range(dur // 1000):
        if (sec // 20) % 4 == 3:
            continue
        rng = random.Random(f"{sid}:{sec}")
        base = sec * 1000
        for k in range(rng.randint(3, 7)):
            t = base + k * 140
            ev.append([t, "m", round(640 + 420 * math.sin(t / 2300)), round(400 + 260 * math.cos(t / 1700))])
        areas = ATT_AREAS[cur]
        if sec % 7 == 3:
            ev.append([base + 900, "c", round(640 + 420 * math.sin(base / 2300)), 400, rng.choice(areas)])
        if sec % 10 == 0:
            ev.append([base + 950, "a", areas[(sec // 10) % len(areas)]])
        if sec % 13 == 5:
            ev.append([base + 500, "s", rng.randint(0, 3000)])
        if dash and sec % 4 == 1:
            ev.append([base + 600, "e", "table", 0, rng.randint(0, 2000)])
        if dash and sec and sec % 300 == 0:
            cur = tabs[(tabs.index(cur) + 1) % len(tabs)]
            ev.append([base + 980, "p", cur])
    return ev


def _rp_specs() -> list[dict]:
    """조각 목록 — 경우마다(ok 셋 + 지난 9 · many 지금 12 + 지난 230 · nobands·empty 없음). 시각은 이 컴퓨터 시계(지금 보는 중은 시작이 25분마다 돈다)."""
    s = state["scenario"]
    if s in ("nobands", "empty"):
        return []
    now = int(time.time() * 1000)
    live_start = now - ((now // 1000) % 1500 + 120) * 1000
    specs = [
        {"sid": "a1b2c3d4e5f60718", "ip": _addr_ip(1), "dev": "pc", "page": "app-spread", "w": 1440, "h": 900, "start": live_start, "dur": None, "script": "wander"},
        {"sid": "c0ffee0000000001", "ip": _addr_ip(1), "dev": "pc", "page": "landing", "w": 1280, "h": 800, "start": now - 3 * 3_600_000, "dur": 95_000, "script": "landing"},
        {"sid": "c0ffee0000000002", "ip": _addr_ip(2), "dev": "mobile", "page": "app-spread", "w": 390, "h": 844, "start": now - 26 * 3_600_000, "dur": 80_000, "script": "dash"},
    ]
    many = s == "many"
    for k in range(230 if many else 9):
        rng = random.Random(f"rp:{k}")
        mobile = rng.random() < 0.4
        specs.append({"sid": f"{0xbeef0000 + k:016x}", "ip": _addr_ip(3 + k % 37), "dev": "mobile" if mobile else "pc", "page": rng.choice(RP_PAGES),
                      "w": 390 if mobile else 1280, "h": 844 if mobile else 800, "start": now - rng.randint(4 * 3_600_000, 13 * 86_400_000),
                      "dur": rng.randint(20, 90 if many else 300) * 1000, "script": "wander"})
    for k in range(11 if many else 0):  # 지금 보는 중 여럿
        specs.append({"sid": f"{0x11ee0000 + k:016x}", "ip": _addr_ip(12 + k), "dev": "pc", "page": RP_PAGES[k % len(RP_PAGES)], "w": 1280, "h": 800,
                      "start": now - (k + 2) * 60_000, "dur": None, "script": "wander"})
    return specs


def _rp_batches(spec: dict) -> list[dict]:
    """조각의 묶음들 — 지금 보는 중은 지금까지 끝난 2초 창만. xss 는 영역·화면 이름에 `<img onerror>`."""
    now = int(time.time() * 1000)
    dur = spec["dur"] if spec["dur"] is not None else now - spec["start"]
    key = (state["scenario"], spec["sid"], spec["start"], dur // RP_WINDOW)
    if key in RP_CACHE:
        return RP_CACHE[key]
    script = spec["script"]
    events = _rp_landing() if script == "landing" else _rp_dash() if script == "dash" else _rp_wander(spec["sid"], spec["page"], dur)
    if state["scenario"] == "xss":
        events = [[*e[:4], X] if e[1] == "c" else [e[0], e[1], X, *e[3:]] if e[1] in ("a", "e") else [e[0], "p", txt(e[2])] if e[1] == "p" else e for e in events]
    events.sort(key=lambda e: e[0])
    batches: list[dict] = []
    for e in events:
        k = e[0] // RP_WINDOW
        if (k + 1) * RP_WINDOW + 80 > dur:
            break
        if not batches or batches[-1]["k"] != k:
            batches.append({"k": k, "at": spec["start"] + (k + 1) * RP_WINDOW + 80, "seq": len(batches), "ev": []})
        batches[-1]["ev"].append(e)
    out = [{"at": b["at"], "seq": b["seq"], "ev": b["ev"]} for b in batches]
    RP_CACHE[key] = out
    return out


def _rp_row(spec: dict) -> dict | None:
    """073 §3.6 행 — 묶음이 하나도 없으면(막 시작) None."""
    batches = _rp_batches(spec)
    if not batches:
        return None
    flat = [e for b in batches for e in b["ev"]]
    pages = [spec["page"]]
    for e in flat:
        if e[1] == "p" and e[2] not in pages:
            pages.append(e[2])
    now_page = next((e[2] for e in reversed(flat) if e[1] == "p"), spec["page"])
    show = txt if state["scenario"] == "xss" else (lambda v: v)
    return {"sid": spec["sid"], "ip": show(spec["ip"]), "dev": spec["dev"], "page": show(spec["page"]), "pages": [show(p) for p in pages],
            "now": show(now_page), "first": batches[0]["at"], "last": batches[-1]["at"], "n": len(batches),
            "clicks": sum(1 for e in flat if e[1] == "c"), "w": spec["w"], "h": spec["h"], "cut": False}


def _rp_state() -> dict | None:
    """경우별 상태 — empty 는 연결 안 됨, problem 은 Redis 실패, 그 밖 ok."""
    s = state["scenario"]
    if s == "empty":
        return {"state": "unconfigured", "code": None}
    if s == "problem":
        return {"state": "error", "code": "redis"}
    return None


def replay_live(query: dict) -> tuple[int, dict]:
    """073 GET /admin/replay/live — last 가 60초 안인 조각, 최근 순 50개."""
    if state["scenario"] in PRE_069:
        return 404, {"detail": "Not Found"}
    now = int(time.time() * 1000)
    off_ = _rp_state()
    if off_:
        return 200, {**off_, "now": now, "rows": []}
    rows = [r for r in (_rp_row(sp) for sp in _rp_specs() if sp["dur"] is None) if r and now - r["last"] <= 60_000]
    rows.sort(key=lambda r: -r["last"])
    return 200, {"state": "ok", "code": None, "now": now, "rows": rows[:50]}


def replay_sessions(query: dict) -> tuple[int, dict]:
    """073 GET /admin/replay/sessions(?ip=) — 그 IP(없으면 모든 IP)의 조각 최근 순 200개, 꼴이 아닌 ip 는 400 bad_ip."""
    if state["scenario"] in PRE_069:
        return 404, {"detail": "Not Found"}
    ip_ = (query.get("ip") or [None])[0]
    if ip_ is not None and not re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}|[0-9a-f:]*:[0-9a-f:]*", ip_):
        return 400, {"error": {"code": "bad_ip", "message": "ip 꼴이 아니다", "detail": None}}
    off_ = _rp_state()
    if off_:
        return 200, {**off_, "ip": ip_, "rows": []}
    specs = [sp for sp in _rp_specs() if ip_ is None or sp["ip"] == ip_]
    rows = [r for r in (_rp_row(sp) for sp in specs) if r]
    rows.sort(key=lambda r: -(r["first"] if ip_ else r["last"]))
    return 200, {"state": "ok", "code": None, "ip": ip_, "rows": rows[:200]}


def replay_session(query: dict) -> tuple[int, dict]:
    """073 GET /admin/replay/session?sid=&after= — 묶음 after 번째부터 600개, next·more. 없는 조각은 meta null, 꼴이 아닌 sid 는 400 bad_sid."""
    if state["scenario"] in PRE_069:
        return 404, {"detail": "Not Found"}
    sid = (query.get("sid") or [""])[0]
    if not re.fullmatch(r"[0-9a-f]{16}", sid):
        return 400, {"error": {"code": "bad_sid", "message": "sid 꼴이 아니다", "detail": None}}
    after = (query.get("after") or ["0"])[0]
    after = int(after) if after.isdigit() else 0
    off_ = _rp_state()
    if off_:
        return 200, {**off_, "meta": None, "batches": [], "next": after, "more": False}
    spec = next((sp for sp in _rp_specs() if sp["sid"] == sid), None)
    row = _rp_row(spec) if spec else None
    if row is None:
        return 200, {"state": "ok", "code": None, "meta": None, "batches": [], "next": after, "more": False}
    batches = _rp_batches(spec)
    part = batches[after:after + 600]
    nxt = after + len(part)
    return 200, {"state": "ok", "code": None, "meta": row, "batches": part, "next": nxt, "more": nxt < len(batches)}


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
        "/svc/api/admin/access/addrs": lambda: addrs(query),
        "/svc/api/admin/access/addr": lambda: addr(query),
        "/svc/api/admin/clarity": lambda: (200, clarity()),
        "/svc/api/admin/clarity/history": lambda: history(pick("days", "30")),
        "/svc/api/admin/clarity/recordings": lambda: recordings(pick("days", "3")),
        "/svc/api/admin/attention": lambda: (200, attention(query)),
        "/svc/api/admin/replay/live": lambda: replay_live(query),
        "/svc/api/admin/replay/sessions": lambda: replay_sessions(query),
        "/svc/api/admin/replay/session": lambda: replay_session(query),
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
