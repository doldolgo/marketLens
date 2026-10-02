"""방문자 짝의 날 속성 — 유입 채널·인앱·기기·OS·브라우저 (스펙 038 §3.5).

UA 토큰은 적힌 대소문자 그대로 보고(§3.4 의 종류와 다르다) 위에서 먼저 맞은 것이 이긴다. 채널은 그날 첫 페이지 줄 하나로
정하고, 출처 호스트가 이름과 같거나 `.<이름>` 으로 끝나면 맞는다(`spacex.com` 은 `x.com` 이 아니다).
"""

from typing import NamedTuple
from urllib.parse import parse_qs

from app.features.admin.access_classes import SELF_HOSTS

# 채널 — `*` 은 도메인 끝(최상위 도메인 — `com`·`co.kr`·`de` 등)
AI_SITES = tuple(
    "chatgpt.com chat.openai.com perplexity.ai claude.ai gemini.google.com copilot.microsoft.com".split()
)
SOCIAL_SITES = tuple(
    "x.com t.co twitter.com facebook.com instagram.com threads.net youtube.com reddit.com kakao.com band.us"
    " blog.naver.com cafe.naver.com dcinside.com fmkorea.com clien.net ppomppu.co.kr coinpan.com t.me".split()
)
SEARCH_SITES = tuple(
    "google.* naver.com daum.net bing.com duckduckgo.com yahoo.* baidu.com yandex.* ecosia.org".split()
)
# `co.kr`·`com.au` 같은 두 마디 최상위 도메인의 앞 마디 — 뒤 마디는 나라 두 글자
SECOND_LEVEL = frozenset("co com ne or net org ac go gov edu".split())

# 인앱(§3.5) — 대소문자 그대로, 위에서 먼저
IN_APPS = (
    ("KAKAOTALK", "kakaotalk"),
    ("NAVER(inapp", "naver"),
    ("Instagram", "instagram"),
    ("FBAN", "facebook"),
    ("FBAV", "facebook"),
    ("Line/", "line"),
    ("DaumApps", "daum"),
    ("BAND", "band"),
    ("; wv)", "other"),
)
OSES = (
    (("iPhone", "iPad", "iPod"), "ios"),
    (("Android",), "android"),
    (("Windows",), "windows"),
    (("Mac OS X", "Macintosh"), "macos"),
    (("CrOS",), "chromeos"),
    (("Linux",), "linux"),
)
BROWSERS = (
    (("Whale/",), "whale"),
    (("SamsungBrowser/",), "samsung"),
    (("Edg",), "edge"),
    (("OPR/", "Opera"), "opera"),
    (("Firefox", "FxiOS"), "firefox"),
    (("Chrome", "CriOS"), "chrome"),
    (("Safari",), "safari"),
)


class Traits(NamedTuple):
    device: str
    os: str
    browser: str
    inapp: str | None


def traits(ua: str) -> Traits:
    # 줄마다 부를 수 있어(UA 가 모두 다른 날) 생성식 없이 앞에서 맞은 것에서 멈춘다
    inapp = None
    for token, name in IN_APPS:
        if token in ua:
            inapp = name
            break
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua):
        device = "tablet"
    elif "Mobi" in ua or "iPhone" in ua or "Android" in ua:
        device = "mobile"
    else:
        device = "desktop"
    return Traits(
        device, _first(ua, OSES), "inapp" if inapp else _first(ua, BROWSERS), inapp
    )


def _first(ua: str, table: tuple[tuple[tuple[str, ...], str], ...]) -> str:
    for tokens, name in table:
        for token in tokens:
            if token in ua:
                return name
    return "other"


def _site(host: str, name: str) -> bool:
    if name.endswith(".*"):
        base, labels = name[:-2], host.split(".")
        for i, label in enumerate(labels[:-1]):
            if label == base:
                rest = labels[i + 1 :]
                return len(rest) == 1 or (
                    len(rest) == 2 and rest[0] in SECOND_LEVEL and len(rest[1]) == 2
                )
        return False
    return host == name or host.endswith(f".{name}")


def channel(query: str, host: str | None, inapp: str | None) -> str:
    """그날 첫 페이지 줄의 유입 채널(§3.5) — 운영자 흔적 짝은 이미 빠졌다. 페이지 줄이 없는 짝은 부르는 쪽이 `unknown`."""
    if query and "utm_source" in parse_qs(query, keep_blank_values=True):
        return "campaign"
    if host is None:
        return "direct" if inapp is None else "inapp"
    if host in SELF_HOSTS:
        return "internal"
    for name, sites in (
        ("ai", AI_SITES),
        ("social", SOCIAL_SITES),
        ("search", SEARCH_SITES),
    ):
        if any(_site(host, site) for site in sites):
            return name
    return "referral"
