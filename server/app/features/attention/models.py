"""화면 이용 통계 — 받는 비콘의 꼴과 상한 (스펙 052 §3.1·§3.3·§3.4, 061 §3.3·§3.4).

화면 이름 목록은 서버 상수 하나다 — 이 목록 밖의 화면은 받지 않는다. `app-*` 의 꼬리는 `web/src/App.tsx` 의 `TabId`
와 같아야 하고(테스트가 묶는다), `web/public/attention.js` 도 같은 목록을 들고 있다.
"""

import re
from dataclasses import dataclass, field

# 대시보드 탭 id — App.tsx 의 TabId 순서
TABS = ("spread", "history", "gap", "pp", "health", "flow")
PAGES = (
    "landing",
    *(f"app-{tab}" for tab in TABS),
    "privacy",
    "kimp-chart",
    "kimp-history",
)
DEVICES = ("mobile", "pc")
METRICS = ("ms", "clicks", "seen")
# 영역 id 꼴 — 전체 일치로 본다(끝의 줄바꿈도 받지 않게)
AREA_ID = re.compile(r"[a-z][a-z0-9-]{0,31}")

# 몸통 상한 — v1·v2 모두(061 — 공개 nginx 도 16k)
MAX_BODY_BYTES = 16_384
MAX_AREAS = 40
# 구간 하나의 영역·요소당 상한 — 브라우저가 구간 전체에서 지킨다. 서버는 비콘 하나가 이 값을 넘으면 400 이다
MAX_MS = 600_000
MAX_CLICKS = 50

# 061 — 요소(e)·깊이(d)·떠난 곳(x)
MAX_ELEMENTS = 150
MAX_KEY_CHARS = 64
# 열쇠에 못 쓰는 글자 — 필드 구분자 |, 제어 문자, 짝 없는 대리 문자(UTF-8 로 Redis 에 적지 못한다)
BAD_KEY = re.compile(r"[|\x00-\x1f\x7f-\x9f\ud800-\udfff]")
DEPTH_MAX = 10
# 깊이는 문서가 스크롤되는 정적 페이지만 — 대시보드 화면의 d 는 null 이어야 한다
DEPTH_PAGES = ("landing", "privacy", "kimp-chart", "kimp-history")
# (KST 날, 화면, 기기)마다 서로 다른 (영역, 열쇠) 상한 — 넘는 새 열쇠는 OTHER_KEY 로 더한다
KEY_LIMIT = 300
OTHER_KEY = "(기타)"
# 요소 필드의 지표 — 클릭·마우스 올림·보인 시간
ELEMENT_METRICS = ("c", "h", "v")


def valid_key(key: object) -> bool:
    return (
        isinstance(key, str)
        and 1 <= len(key) <= MAX_KEY_CHARS
        and BAD_KEY.search(key) is None
    )


@dataclass(frozen=True)
class Beacon:
    """검사를 통과한 비콘 하나 — 영역마다 (보인 ms, 클릭, seen), v2 면 요소마다 (클릭, 마우스 올림 ms, 보인 ms)·깊이·떠난 곳."""

    page: str
    device: str
    pv: int
    areas: dict[str, tuple[int, int, int]]
    elements: dict[tuple[str, str], tuple[int, int, int]] = field(default_factory=dict)
    depth: int | None = None
    exit: str | None = None
