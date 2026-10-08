"""화면 영역 이용 통계 — 받는 비콘의 꼴과 상한 (스펙 052 §3.1·§3.3·§3.4).

화면 이름 목록은 서버 상수 하나다 — 이 목록 밖의 화면은 받지 않는다. `app-*` 의 꼬리는 `web/src/App.tsx` 의 `TabId`
와 같아야 하고(테스트가 묶는다), `web/public/attention.js` 도 같은 목록을 들고 있다.
"""

import re
from dataclasses import dataclass

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

MAX_BODY_BYTES = 4096
MAX_AREAS = 40
# 구간 하나의 영역당 상한 — 브라우저가 구간 전체에서 지킨다. 서버는 비콘 하나가 이 값을 넘으면 400 이다
MAX_MS = 600_000
MAX_CLICKS = 50


@dataclass(frozen=True)
class Beacon:
    """검사를 통과한 비콘 하나 — 영역마다 (보인 ms, 클릭, seen)."""

    page: str
    device: str
    pv: int
    areas: dict[str, tuple[int, int, int]]
