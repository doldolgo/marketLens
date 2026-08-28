"""입출금 상태 자료구조와 실패 예외 — 스펙 006 §3.1·§3.5.

거래소 조회 결과는 "코인 심볼(대문자) → 코인 단위 dep/wd + 망 목록" 이다.
망 모델(Network)은 spreads 도 쓰므로 core.networks 에 있다.
"""

from dataclasses import dataclass, field

from app.core.networks import Network


@dataclass
class CoinStatus:
    """코인 1개의 입출금 상태. 코인 단위 값은 망별 OR, 망 목록은 응답 순서 보존."""

    deposit_enabled: bool
    withdrawal_enabled: bool
    networks: list[Network] = field(default_factory=list)


class WalletStatusError(Exception):
    """입출금 조회 실패. 조회기는 예외를 삼키지 않는다 — 삼키는 건 수집 루프 쪽(service)이다.

    키·토큰·서명값은 message·detail 에 절대 넣지 않는다 (스펙 006 §3.5).
    """

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, object] | None = None,
        calls: int = 1,  # 이 실패까지 나간 HTTP 호출 수 — 키 없음은 0
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        self.calls = calls
