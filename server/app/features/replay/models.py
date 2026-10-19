"""화면 이동 기록 — 받는 묶음의 꼴·상한과 검사 (스펙 073 §3.2~§3.5).

화면 이름 목록은 052 의 목록과 같아야 한다(기능 간 import 금지라 따로 들고, `tests/test_replay.py` 가 둘을 묶는다).
검사는 몸통 전체를 거절하거나 통째로 받는다 — 모르는 사건 종류·자리 수 틀림·범위 밖이 하나라도 있으면 400 이다.
"""

import ipaddress
import json
import re
from dataclasses import dataclass

# 052 의 화면 이름 — 대시보드 탭 여섯(App.tsx 의 TabId 순서) + 정적 페이지 넷
TABS = ("spread", "history", "gap", "pp", "health", "flow")
# 튜플이라 목록·객체 같은 해시 안 되는 값도 `in` 이 False 로 답한다
PAGES = (
    "landing",
    *(f"app-{tab}" for tab in TABS),
    "privacy",
    "kimp-chart",
    "kimp-history",
)
DEVICES = ("pc", "mobile")
# 052 의 영역 id 꼴 — 전체 일치로 본다(끝의 줄바꿈도 받지 않게)
AREA_ID = re.compile(r"[a-z][a-z0-9-]{0,31}")
SID = re.compile(r"[0-9a-f]{16}")
# IP 를 읽을 수 없는 묶음의 IP 자리 — `rp:ip:(없음)` 으로 모인다
NO_IP = "(없음)"

# 몸통 키 — `v` 는 1 하나. `c`(그 방문의 화면 분석 동의 상태)는 있어도 없어도 된다 — 동의자만 기록하던 예전 replay.js(열린 탭에
# 남은 것)는 `c` 없이 보내고, 그 묶음은 지금 판에 동의한 방문의 것이라 `granted` 로 본다
KEYS = frozenset({"v", "sid", "seq", "page", "dev", "w", "h", "ev"})
CONSENT_KEY = "c"
# replay.js 의 동의 상태 다섯 — 지금 판의 동의·판 있는 거부·하루 동안 안 보기·정하지 않음·GPC
CONSENTS = ("granted", "denied", "later", "unset", "gpc")
LEGACY_CONSENT = "granted"
MAX_BODY_BYTES = 16_384
MAX_SEQ = 3_599
MAX_EVENTS = 600
# 조각은 2시간에서 끝난다 — 사건 시각 t(조각 시작부터 ms)의 상한
MAX_T = 7_200_000
MAX_XY = 20_000
MAX_SCROLL = 1_000_000
# 영역 안 스크롤되는 요소의 문서 순서 번호 — 기록 쪽(replay.js)이 200번째부터는 보내지 않는다
MAX_SCROLLER = 199


def _int_in(value: object, low: int, high: int) -> bool:
    # bool 은 int 의 하위형이라 따로 막는다 — JSON 의 true 는 1 이 아니다
    return type(value) is int and low <= value <= high


def _area(value: object) -> bool:
    return isinstance(value, str) and AREA_ID.fullmatch(value) is not None


def _xy(x: object, y: object) -> bool:
    return _int_in(x, 0, MAX_XY) and _int_in(y, 0, MAX_XY)


def _event(ev: object) -> bool:
    """§3.2 — `[t, 종류, …]` 하나. 종류마다 자리 수가 정확해야 한다."""
    if not isinstance(ev, list) or len(ev) < 3 or not _int_in(ev[0], 0, MAX_T):
        return False
    kind, rest = ev[1], ev[2:]
    if kind == "m":
        return len(rest) == 2 and _xy(*rest)
    if kind == "c":
        return (
            len(rest) == 3
            and _xy(rest[0], rest[1])
            and (rest[2] == "" or _area(rest[2]))
        )
    if kind == "s":
        return len(rest) == 1 and _int_in(rest[0], 0, MAX_SCROLL)
    if kind == "e":
        return (
            len(rest) == 3
            and _area(rest[0])
            and _int_in(rest[1], 0, MAX_SCROLLER)
            and _int_in(rest[2], 0, MAX_SCROLL)
        )
    if kind == "r":
        return (
            len(rest) == 2
            and _int_in(rest[0], 1, MAX_XY)
            and _int_in(rest[1], 1, MAX_XY)
        )
    if kind == "v":
        return len(rest) == 1 and _int_in(rest[0], 0, 1)
    if kind == "p":
        return len(rest) == 1 and rest[0] in PAGES
    if kind == "a":
        return len(rest) == 1 and _area(rest[0])
    return False


@dataclass(frozen=True)
class Batch:
    """검사를 통과한 묶음 하나 — `ev` 는 받은 목록 그대로(저장할 때 다시 직렬화한다)."""

    sid: str
    seq: int
    page: str
    dev: str
    w: int
    h: int
    ev: list[list[object]]
    consent: str = LEGACY_CONSENT


def parse_batch(body: bytes) -> Batch | None:
    """§3.4 ③ — 어긋나면 None(400 `bad_replay`). 키 여덟 그대로(+ 고를 수 있는 `c`)·`v` 1 만 받는다."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        # 깨진 UTF-8·JSON, 몸통 안의 깊은 중첩(파이썬 재귀 한도) 모두 400
        return None
    if not isinstance(data, dict) or set(data) - {CONSENT_KEY} != KEYS:
        return None
    consent = data.get(CONSENT_KEY, LEGACY_CONSENT)
    if not isinstance(consent, str) or consent not in CONSENTS:
        return None
    if type(data["v"]) is not int or data["v"] != 1:
        return None
    sid, seq, page, dev = data["sid"], data["seq"], data["page"], data["dev"]
    w, h, events = data["w"], data["h"], data["ev"]
    if not (isinstance(sid, str) and SID.fullmatch(sid) and _int_in(seq, 0, MAX_SEQ)):
        return None
    if page not in PAGES or dev not in DEVICES:
        return None
    # 머리 w·h 의 0 은 모름 — 숨은 채 열린 페이지는 크기를 못 잰다(잴 수 있게 되면 r 사건이 온다). r 은 1 부터
    if not (_int_in(w, 0, MAX_XY) and _int_in(h, 0, MAX_XY)):
        return None
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        return None
    if not all(_event(ev) for ev in events):
        return None
    return Batch(
        sid=sid, seq=seq, page=page, dev=dev, w=w, h=h, ev=events, consent=consent
    )


def client_ip(header: str | None) -> str:
    """§3.4 — `X-Client-IP` 의 첫 값(쉼표 앞, 공백 제거)을 IPv4·IPv6 정규 글자로. 읽을 수 없으면 `(없음)`."""
    first = (header or "").split(",", 1)[0].strip()
    try:
        return str(ipaddress.ip_address(first))
    except ValueError:
        return NO_IP


def valid_ip_query(ip: str) -> bool:
    """피드 `sessions?ip=` — 저장 키가 될 수 있는 글자(정규 글자 IPv4·IPv6, 또는 `(없음)`)만 (§3.6)."""
    if ip == NO_IP:
        return True
    try:
        return str(ipaddress.ip_address(ip)) == ip
    except ValueError:
        return False
