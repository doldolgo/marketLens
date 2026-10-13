"""Clarity 호출 한 종류의 때 정하기 — Redis 기록과 메모리 기록으로 하루 한도 지키기 (스펙 040 §3.2·068 §3.1).

종류는 넷이다 — 기본 요약(6시간, `admin:clarity`)·페이지×기기 묶음(12시간, `admin:clarity:pages`)·기기·유입 차원(24시간씩,
`admin:clarity:dims` 의 칸 둘). 간격은 시도 사이다(성공·실패 모두, 시도 시각은 보낸 때) — 어떤 24시간에도 4 + 2 + 1 + 1 = 8회.
프로세스는 종류마다 마지막 기록을 메모리에도 두고 Redis 에 썼는지 표시한다(api 는 워커 하나라 프로세스가 하나다). 때는
Redis 와 메모리 가운데 마지막 시도가 늦은 쪽으로 정하고, 메모리 쪽이 늦거나 아직 쓰이지 않았으면 Redis 에 다시 쓴다.
Redis 기록이 사라졌는데(사람이 지움·잃음) 메모리에 간격 안 시도가 있고 그것이 이미 쓰인 것이면 곧바로 부른다(바로
부르기) — 이 프로세스에서 종류마다 24시간에 한 번, 프로세스 전체로도 24시간에 두 번(사람 몫)뿐이고, 그 밖에는 메모리
기록을 다시 써 간격을 따른다. 그래서 키를 몇 번 지워도 한 프로세스의 어떤 24시간에도 8 + 2 = 10회를 넘지 않는다.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.features.admin.clarity import ClarityStore, Record
from app.features.admin.parts import Result

DAY_MS = 86_400_000
# 프로세스 전체의 바로 부르기 — 어떤 24시간에도 사람 몫 2회. 종류마다 24시간에 한 번(040)만이면 종류가 넷이라 키를
# 모두 지울 때마다 8 + 4 = 12회까지 갈 수 있다
DIRECT_PER_DAY = 2


class DirectBudget:
    """바로 부르기를 보낸 시각 — 어떤 24시간에도 `limit` 번까지(종류 넷이 나눠 쓴다)."""

    def __init__(self, limit: int = DIRECT_PER_DAY) -> None:
        self._limit = limit
        self._sent: list[int] = []

    def allows(self, now_ms: int) -> bool:
        return sum(1 for sent in self._sent if now_ms - sent < DAY_MS) < self._limit

    def spend(self, sent_ms: int) -> None:
        self._sent = [s for s in self._sent if sent_ms - s < DAY_MS] + [sent_ms]


class ClarityKind:
    """호출 한 종류 — 인자·값 만들기·Redis 자리와 이 프로세스의 기억(마지막 기록·썼는지·바로 부른 시각)."""

    def __init__(
        self,
        *,
        name: str,
        refresh_sec: int,
        params: Mapping[str, str],
        parse: Callable[[bytes], dict[str, Any]],
        load: Callable[[ClarityStore], Awaitable[str | None]],
        save: Callable[[ClarityStore, str], Awaitable[None]],
        budget: DirectBudget,
    ) -> None:
        self.name = name  # WARNING 의 부분 이름
        self.refresh_sec = refresh_sec
        self.gap_ms = refresh_sec * 1000
        self.params = params
        self.parse = parse
        self.load = load
        self.save = save
        self.record: Record | None = None  # 이 프로세스가 아는 마지막 기록
        self.saved = False  # 그 기록이 Redis 에 쓰였는가
        self.dirty = False  # 간격을 따르는 동안 Redis 에 다시 쓸 기록인가
        self.direct_at: int | None = None  # 바로 부르기를 한 마지막 시각
        self.budget = budget  # 프로세스 전체 바로 부르기 — 종류 넷이 같이 쓴다

    def settle(self, stored: Record, now_ms: int) -> tuple[bool, bool]:
        """Redis 에서 읽은 기록(없거나 모양이 틀리면 시도 없음)으로 때를 정한다 → (부를 때인가, 바로 부르기인가)."""
        mine = self.record
        if stored.attempt_at is not None:
            later = mine is not None and (mine.attempt_at or 0) > stored.attempt_at
            unsaved = (
                mine is not None
                and not self.saved
                and mine.attempt_at == stored.attempt_at
            )
            if later or unsaved:
                self.dirty = True  # 메모리 쪽이 늦거나 아직 쓰이지 않았다 — 다시 쓴다
            else:
                self.record, self.saved, self.dirty = stored, True, False
        elif mine is not None and mine.attempt_at is not None:
            within = now_ms - mine.attempt_at < self.gap_ms
            if (
                within
                and self.saved
                and (self.direct_at is None or now_ms - self.direct_at >= DAY_MS)
                and self.budget.allows(now_ms)
            ):
                return True, True  # 쓴 기록이 사라졌다 — 사람이 지웠거나 잃었다
            self.dirty = True
        record = self.record
        due = (
            record is None
            or record.attempt_at is None
            or now_ms - record.attempt_at >= self.gap_ms
        )
        return due, False

    def spend_direct(self, sent_ms: int) -> None:
        """바로 부르기를 보냈다 — 종류의 24시간 한 번과 프로세스 전체 두 번에서 센다."""
        self.direct_at = sent_ms
        self.budget.spend(sent_ms)

    def expire(self, now_ms: int) -> bool:
        """마지막 성공에서 7일 지난 값을 메모리 기록에서 버린다 → 버렸으면 True(Redis 에도 다시 쓸 것).
        시도 시각·결과는 그대로라 시도로 세지 않는다."""
        record = self.record
        if record is None or record.success_at is None:
            return False
        kept = record.fresh(now_ms)
        if kept is record:
            return False
        self.record = kept
        return True

    def blocks_pages(self) -> bool:
        """기본의 마지막 시도가 401·403·429 면 묶음 호출을 미룬다 — 토큰과 하루 한도를 둘이 같이 쓴다."""
        record = self.record
        return record is not None and (
            record.state == "denied" or record.code == "http_429"
        )

    def part(self, now_ms: int, fault: tuple[str, str | None] | None = None) -> Result:
        """기억 → 부분. 값은 마지막 성공에서 7일 안일 때만(state 와 무관), `nextAt` = 마지막 시도 + 간격.
        `fault` 는 이번 갱신의 실패(Redis 를 못 읽음·처리기 예외) — 값은 기억 그대로 둔다."""
        kept = self.record.fresh(now_ms) if self.record is not None else Record()
        values = dict(kept.values or {})
        values["nextAt"] = (
            None if kept.attempt_at is None else kept.attempt_at + self.gap_ms
        )
        if fault is not None:
            state, code = fault
        elif kept.attempt_at is None:
            state, code = "pending", None
        else:
            state, code = kept.state or "error", kept.code
        return Result(state, code, kept.success_at, values)
