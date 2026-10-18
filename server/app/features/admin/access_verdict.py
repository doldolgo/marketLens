"""IP 행 하나의 봇 판정 — 근거·가중치·점수·판정 (스펙 069 §3.4).

점수 = 맞은 근거의 가중치 합. 판정은 운영자 흔적(038 operator 줄이 하나라도 있음) → `operator`(점수 없음·근거 없음), IP 없는
줄의 행·요청 3 미만 → `unknown`(근거·점수는 싣는다), 그 밖 점수 ≥ 60 `bot` · 30~59 `suspect` · < 30 `human`.
근거 글은 숫자를 넣은 한국어 한 줄이고 가린 행이면 끝에 '(대역)' 을 붙인다. 순서는 아래 표 차례.
"""

from typing import Any, NamedTuple

WEIGHTS = {
    "ua_bot": 70,
    "probe": 60,
    "ua_tool": 50,
    "no_js": 30,
    "cloud": 20,
    "burst": 20,
    "many_404": 15,
    "no_assets": 15,
    "robots": 10,
    "ua_many": 10,
    "js": -40,
    "ws": -30,
    "assets": -15,
    "search_ref": -10,
    "fake_crawler": 30,
}
BOT, SUSPECT = 60, 30
MIN_REQUESTS = 3
PAGES_MIN = 3  # no_js·no_assets
BURST = 60  # 어느 1분에 요청
MANY_404, SHARE_404 = 10, 0.3
UA_MANY = 5
NO_IP = "(없음)"


class Signals(NamedTuple):
    """창 안 값 — 판정이 보는 것만."""

    requests: int
    pages: int
    js: int
    ws: int
    kinds: dict[str, int]
    probes: int
    e404: int
    assets: int
    robots: int
    search: int
    peak: int
    ua_bot: int  # 봇 이름을 밝힌 줄(038 종류 search·ai·preview 또는 UA 에 bot·crawler·spider)
    agents: int  # 서로 다른 UA 수(상한 있음)
    net: str | None
    names: tuple[str, ...]  # 봇 이름(UA 에서)
    probe_paths: tuple[str, ...]  # 근거 글에 보일 탐색 경로 예
    org: str | None


def _clip(items: tuple[str, ...], n: int = 2) -> str:
    shown = "·".join(items[:n])
    return shown + ("…" if len(items) > n else "")


def reasons(s: Signals, *, fake_crawler: bool = False) -> list[tuple[str, str]]:
    """맞은 근거의 (code, 글) — 표 차례."""
    out: list[tuple[str, str]] = []
    k = s.kinds
    if s.ua_bot:
        named = f"({_clip(s.names)})" if s.names else ""
        out.append(("ua_bot", f"봇 이름을 밝힌 요청 {s.ua_bot}번{named}"))
    if s.probes:
        shown = f"({_clip(s.probe_paths)})" if s.probe_paths else ""
        out.append(("probe", f"탐색 경로 {s.probes}번{shown}"))
    tools = k.get("tool", 0) + k.get("scanner", 0)
    if tools:
        out.append(("ua_tool", f"자동화 도구·봇·스캐너 요청 {tools}번"))
    if s.pages >= PAGES_MIN and not s.js:
        out.append(("no_js", f"페이지 {s.pages}번인데 JS 신호 0"))
    if s.net == "cloud":
        out.append(("cloud", f"데이터센터 망({s.org})" if s.org else "데이터센터 망"))
    if s.peak >= BURST:
        out.append(("burst", f"1분에 최대 {s.peak}번 요청"))
    if s.e404 >= MANY_404 and s.e404 >= SHARE_404 * s.requests:
        pct = round(s.e404 * 100 / s.requests)
        out.append(("many_404", f"404 {s.e404}번(요청의 {pct}%)"))
    if s.pages >= PAGES_MIN and not s.assets:
        out.append(("no_assets", f"페이지 {s.pages}번인데 정적 자원 0"))
    if s.robots:
        out.append(("robots", f"robots.txt {s.robots}번"))
    if s.agents >= UA_MANY:
        out.append(("ua_many", f"서로 다른 브라우저 정보 {s.agents}개"))
    if s.js:
        out.append(("js", f"JS 신호 {s.js}번"))
    if s.ws:
        out.append(("ws", f"실시간 연결(WS) {s.ws}번"))
    if s.assets:
        out.append(("assets", f"정적 자원 {s.assets}번"))
    if s.search:
        out.append(("search_ref", f"검색 엔진에서 들어온 페이지 {s.search}번"))
    if fake_crawler:
        out.append(("fake_crawler", "검색 로봇 이름을 쓰지만 주소 확인 실패"))
    return out


def judge(
    s: Signals, *, ip: str, masked: bool, fake_crawler: bool = False
) -> dict[str, Any]:
    """`{score, verdict, reasons:[{code, text, weight}]}`."""
    if s.kinds.get("operator", 0):
        return {"score": None, "verdict": "operator", "reasons": []}
    tail = " (대역)" if masked else ""
    found = [
        {"code": code, "text": text + tail, "weight": WEIGHTS[code]}
        for code, text in reasons(s, fake_crawler=fake_crawler)
    ]
    score = sum(r["weight"] for r in found)
    if ip == NO_IP or s.requests < MIN_REQUESTS:
        verdict = "unknown"
    elif score >= BOT:
        verdict = "bot"
    elif score >= SUSPECT:
        verdict = "suspect"
    else:
        verdict = "human"
    return {"score": score, "verdict": verdict, "reasons": found}
