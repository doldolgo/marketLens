"""가림 — 알림 기록·관리자 피드가 남기거나 답하는 글에서 ARN·계정 ID 를 지운다 (스펙 034 §3.1).

AWS 오류 문장은 `User: arn:aws:sts::<계정>:assumed-role/…` 로 시작하고, 이 문장이 025 알림(ERROR 로그의 예외 문장)과
경보 사유·canary 줄에 섞여 들어올 수 있다. 레포가 공개이고 화면·Redis 기록이 오래 남으므로 두 모양을 `[가림]` 으로
바꾼다 — `arn:aws:` 로 시작하는 공백 없는 덩어리, 앞뒤가 숫자가 아닌 12자리 숫자(계정 ID 모양).
알림기(core)와 admin 기능이 같이 쓰므로 core 에 둔다.
"""

import re

MASK = "[가림]"
_ARN = re.compile(r"arn:aws:\S*")
# \d 는 유니코드 숫자까지 잡는다 — 계정 ID 는 ASCII 숫자뿐이라 [0-9] 로 좁힌다
_ACCOUNT_ID = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")


def redact(text: str) -> str:
    """ARN 덩어리를 먼저 지운다 — ARN 안의 계정 ID 까지 한 번에 사라진다."""
    return _ACCOUNT_ID.sub(MASK, _ARN.sub(MASK, text))
