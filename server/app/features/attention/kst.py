"""KST 날짜 계산 — 시행일 게이트·받은 날·해시 만료 (스펙 052 §3.4·§3.5).

게이트 시각 계산은 admin 의 같은 함수를 쓰지 않고 여기 둔다(기능 간 import 금지).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DAY_SEC = 86_400
# 처리방침의 '하루 합계 90일'
KEEP_DAYS = 90


def gate_ts(effective: str) -> int:
    """시행일(KST 날짜) 00:00 Asia/Seoul 의 epoch 초."""
    d = date.fromisoformat(effective)
    return int(datetime(d.year, d.month, d.day, tzinfo=KST).timestamp())


def kst_date(ts: float) -> date:
    return datetime.fromtimestamp(ts, KST).date()


def day_key(d: date) -> str:
    """Redis 해시 이름의 날짜 — `attn:d:<YYYYMMDD>`."""
    return d.strftime("%Y%m%d")


def expire_at(day: str) -> int:
    """그 KST 날의 끝(다음 날 00:00 KST) + 90일, epoch 초 — 피드의 90일 창(오늘 포함)은 모두 살아 있다."""
    d = datetime.strptime(day, "%Y%m%d").date()
    start = int(datetime(d.year, d.month, d.day, tzinfo=KST).timestamp())
    return start + (1 + KEEP_DAYS) * DAY_SEC
