"""core 가 제공하는 계약 — 후속 스펙이 구현하고 main.py lifespan 이 배선한다 (architecture.md).

core 는 features 를 import 하지 않는다. 구현이 아직 없는 계약에는 아무것도 하지 않는
기본 구현이 꽂힌다(원문 싱크 010, 틱 인계 009, 바이낸스 심볼 012).
"""

from collections.abc import Callable
from typing import Protocol

import httpx

from app.core.models import Row, StreamError, Tick

# --- 원문 싱크 (스펙 001 §3.7, 구현은 010) ---
# record(exchange, source, received_at_ms, payload) — 동기·예외 없음·즉시 반환.
RawRecorder = Callable[[str, str, int, str], None]


def noop_record(exchange: str, source: str, received_at_ms: int, payload: str) -> None:
    """S3_BUCKET 이 없거나 010 이 아직 없을 때 꽂히는 구현 — 아무것도 하지 않는다."""


# --- 틱 인계 (스펙 001 §3.6, 구현은 009) ---
# handoff(tick) — 동기·예외 없음. 슬롯에서 물러난 직전 틱을 받는다.
TickHandoff = Callable[[Tick], None]


def noop_handoff(tick: Tick) -> None:
    """009 가 아직 없을 때 꽂히는 구현 — 틱을 버린다."""


# --- 판정 결과 전달 (스펙 001 §3.8, 구현은 011 core/outages.py) ---


class OutageSink(Protocol):
    def record_success(self, exchange: str, at_ms: int) -> None: ...

    def record_failure(
        self,
        exchange: str,
        at_ms: int,
        *,
        kind: str,
        message: str,
        status_code: int | None,
        url: str | None,
        retry_after_sec: int | None,
    ) -> None: ...


class Verdict(Protocol):
    """스트림 판정 1건 — ok 면 성공, 아니면 error 에 실패 내용 (§3.8)."""

    @property
    def ok(self) -> bool: ...

    @property
    def error(self) -> StreamError | None: ...


class StreamJudge(Protocol):
    """틱 루프가 매초 부르는 거래소 스트림 판정 — 국내는 core.ticks.judge_state, 바이낸스는 012.

    None 은 "아직 판정 대상이 아니다"(첫 연결 시도 결과가 나오기 전).
    """

    @property
    def id(self) -> str: ...

    def judge(self, now_ms: int) -> Verdict | None: ...


# --- 입출금 조회기 (구현은 006 features/wallet_status) ---


class WalletStatusProvider(Protocol):
    async def refresh_if_due(
        self, client: httpx.AsyncClient, *, force: bool = False
    ) -> dict[str, int] | None:
        """60초가 지났으면(force 면 무조건) 조회 후 거래소별 호출 수, 캐시면 None."""
        ...

    def apply(self, rows: list[Row], exchange: str) -> None:
        """캐시를 행(dep/wd/networks)에 반영한다."""
        ...

    def availability(self) -> dict[str, bool]: ...

    def warnings(self) -> list[str]: ...

    def failed(self) -> list[str]: ...


# --- 바이낸스 USDT 현물 심볼 집합 (스펙 001 §3.2, 구현은 012) ---


class ForeignSymbolSource(Protocol):
    async def refresh(self, client: httpx.AsyncClient) -> int:
        """심볼 목록을 REST 로 갱신하고 나간 호출 수를 돌려준다. 실패는 ExchangeError."""
        ...

    def bases(self) -> set[str]:
        """현재 알고 있는 USDT 현물 base 집합(대문자). 아직 없으면 빈 집합."""
        ...


class NoForeignSymbols:
    """012 가 아직 없을 때 꽂히는 구현 — 심볼이 없어 우주가 비고 행이 저장되지 않는다."""

    async def refresh(self, client: httpx.AsyncClient) -> int:
        return 0

    def bases(self) -> set[str]:
        return set()
