"""접속 요약의 줄 분류 표 — 줄 풀기·탐색 경로·운영자 흔적·종류 여덟 (스펙 038 §3.4).

순수 함수와 표만 둔다(줄마다 도는 조립은 `access_hours.py`, 채널·기기·OS·브라우저·인앱은 `access_traits.py`). 종류는 UA 를
소문자로 부분 일치하고 위에서 먼저 맞은 것이 이긴다. 운영자 흔적은 IP 목록 없이 출처로만 가른다 — 운영자 IP 는 설정·코드에
적지 않는다. 페이지 줄 = GET·200 또는 304·마지막 조각에 점 없음, JS 신호 = clarity.js 두 경로의 GET 200·304 와 WS 101.
"""

import ipaddress
import json
import math
import re
from urllib.parse import urlsplit

WS_PATH = "/api/ws/spreads"
JS_PATHS = frozenset(("/clarity.js", "/app/clarity.js"))
RETURNING_PATHS = frozenset(("/", "/privacy"))
SELF_HOSTS = frozenset(("kimptrack.com", "www.kimptrack.com", "admin.kimptrack.com"))
CLASSES = tuple("browser search ai preview tool scanner operator unknown".split())
# 9999-12-30T00:00Z — 이 뒤의 `ts` 는 KST 날짜(짝 열쇠)로 바꿀 수 없다
TS_LIMIT = 253_402_128_000.0
# UA 종류·특성 판정은 앞 1,024자만 본다 — 줄마다 다른 긴 UA 의 판정 비용을 묶는다(짝 해시는 UA 전체, §3.4·§3.5)
UA_JUDGE = 1_024


def _words(text: str) -> re.Pattern[str]:
    return re.compile("|".join(re.escape(w) for w in text.split()))


# 쿼리 뗀 경로(소문자)에 하나라도 들면 탐색 줄 — 우리 경로와 web/public 파일은 어느 낱말에도 걸리지 않는다(테스트가 지킨다)
PROBE = _words(
    "wp- wordpress xmlrpc .php .env .git .aws .ssh cgi-bin phpmyadmin actuator /admin /login /config /vendor/"
    " /boaform /hnap1 /owa/ /autodiscover /server-status /solr /console .ini .sql .bak /backup /shell /setup"
    " /install /debug .ds_store"
)
# 종류 3~7 — 소문자 UA 부분 일치, 이 순서로 먼저 맞은 것. 정규식 대신 `in` 으로 본다 — 낱말 59개의 대안 정규식은
# 브라우저 UA 한 줄에 ≈3.8µs, 부분 문자열 찾기는 ≈1.6µs(2026-10-02 로컬)
UA_KINDS = (
    (
        "search",
        tuple(
            "googlebot google-site-verification yeti daumoa bingbot applebot duckduckbot baiduspider yandex".split()
        ),
    ),
    (
        "ai",
        tuple(
            "gptbot chatgpt-user oai-searchbot claudebot claude-user claude-searchbot perplexitybot perplexity-user"
            " bytespider ccbot amazonbot meta-externalagent".split()
        ),
    ),
    (
        "preview",
        tuple(
            "kakaotalk-scrap slackbot slack-imgproxy telegrambot twitterbot facebookexternalhit discordbot whatsapp"
            " linkedinbot mastodon".split()
        ),
    ),
    (
        "tool",
        tuple(
            "curl wget python go-http axios okhttp node-fetch undici java/ apache-httpclient libwww scrapy headless"
            " phantomjs dalvik postman lighthouse uptimerobot".split()
        ),
    ),
    (
        "scanner",
        tuple("bot crawl spider slurp scan zgrab masscan nmap nuclei censys".split()),
    ),
)
# 3~7 의 낱말 전부 — 대부분의 줄(브라우저 모양)은 이 한 번으로 3~7 을 건너뛴다
ANY_KIND = tuple(w for _, words in UA_KINDS for w in words)
BROWSER_TOKENS = _words(
    "chrome/ crios/ firefox/ fxios/ safari/ edg opr/ samsungbrowser/ whale/ kakaotalk naver(inapp instagram fban"
    " fbav line/"
)

# 한 줄의 쓰는 필드 — (method, uri, status, duration, ua, referer, 가린 IP(없으면 빈 값 — 짝을 만들지 않는다))
Line = tuple[str, str, int, float, str, str, str]
_decode = json.JSONDecoder().raw_decode


def line_ts(line: str) -> float | None:
    """JSON 을 풀지 않고 맨 앞 `"ts":` 의 수를 읽는다 — caddy 는 `ts` 를 두 번째 필드로 쓴다(문자열 안의 따옴표는 이스케이프된다).
    NaN·무한·날짜로 바꿀 수 없는 먼 미래(손상·손으로 고친 줄)는 ts 없는 줄이다 — 한 줄이 요약 전체를 깨지 않게."""
    i = line.find('"ts":')
    if i < 0:
        return None
    end = line.find(",", i + 5)
    try:
        ts = float(line[i + 5 : end])
    except ValueError:
        return None
    # NaN 은 비교가 늘 거짓이라 이 한 번으로 NaN·무한·음수·먼 미래가 함께 빠진다
    return ts if 0 <= ts < TS_LIMIT else None


def parse(line: str) -> Line | None:
    """한 줄 → 쓰는 필드(평범한 튜플 — 줄마다 만들어 가볍게). JSON 이 아니거나 필드가 빠지거나 모양이 틀리면
    (`status` 가 정수가 아님·`duration` 이 유한한 수가 아님) None — 한 줄이 요약 전체를 깨지 않게.
    ua·referer·IP 는 없으면 빈 값. `json.loads` 와 같이 객체 뒤에 공백 밖의 글자가 있으면 None 이다."""
    try:
        # caddy 줄은 `{` 로 시작한다 — 아니면 json.loads 처럼 앞 공백을 건너뛴다
        data, end = _decode(
            line, 0 if line[:1] == "{" else len(line) - len(line.lstrip())
        )
        request = data["request"]
        method, uri = request["method"], request["uri"]
        status, duration = data["status"], data["duration"]
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    if end != len(line) and not line[end:].isspace():
        return None
    if type(method) is not str or type(uri) is not str or type(status) is not int:
        return None
    if type(duration) not in (int, float):
        return None
    try:
        duration = float(duration)  # 실수로 바꿀 수 없는 큰 정수는 OverflowError
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(duration):
        return None  # NaN·무한(JSON 의 `NaN`·`1e999`) — 모양이 틀린 줄
    ua, referer = data.get("ua", ""), data.get("referer", "")
    ip = request.get("client_ip") or request.get("remote_ip") or ""
    return (  # type: ignore[return-value]
        method,
        uri,
        status,
        duration,
        ua if type(ua) is str else "",
        referer if type(referer) is str else "",
        ip if type(ip) is str else "",
    )


def referrer(referer: str) -> tuple[str, str] | None:
    """referer → (출처 키 `스킴://호스트[:포트]`, 호스트) — 소문자·사용자 정보 없음·호스트 끝 점 뗌.
    027 caddy 가 출처만 남기지만 그 계약에 기대지 않는다 — 경로·쿼리(검색어·이메일)는 여기서도 버린다.
    빈 값·http(s) 밖·모양이 아니면 None."""
    if not referer:
        return None
    try:
        parts = urlsplit(referer.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    host = (host or "").rstrip(".")
    if parts.scheme not in ("http", "https") or not host:
        return None
    shown = f"[{host}]" if ":" in host else host  # IPv6
    return f"{parts.scheme}://{shown}{'' if port is None else f':{port}'}", host


def is_operator(host: str) -> bool:
    """운영자 흔적 출처 — 로컬·`.test`·IP 글자 그대로(루프백·사설·탄력 IP 를 직접 연 출처)·Clarity 대시보드."""
    if host in ("localhost", "clarity.microsoft.com") or host.endswith(
        (".localhost", ".test")
    ):
        return True
    if not (host[0].isdigit() or ":" in host):
        return False  # IP 글자일 수 없다 — 대부분의 출처는 여기서 끝난다
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def ua_kind(ua: str) -> str:
    """UA 로만 정한 종류(§3.4 의 2~9) — 운영자 흔적·탐색 줄의 덮어쓰기 전. `browser` 면 짝을 기록하는 브라우저 모양이다.
    앞 1,024자만 본다."""
    if not ua:
        return "unknown"
    low = ua[:UA_JUDGE].lower()
    if any(map(low.__contains__, ANY_KIND)):
        for name, words in UA_KINDS:
            if any(map(low.__contains__, words)):
                return name
    if low.startswith("mozilla/5.0") and BROWSER_TOKENS.search(low) is not None:
        return "browser"
    return (
        "scanner"  # Mozlila 오타·브라우저 토큰 없는 Mozilla·Mozilla 로 시작하지 않는 것
    )
