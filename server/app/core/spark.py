"""spark — `/spreads` 행의 김프(fwd 원값) 추이 링버퍼 (스펙 009 §3.6).

조합 (dom, fx, base) 마다 벽시계 1분 버킷(`ts // 60`)의 마지막 값 30개를 든다. 인계기가 틱마다
갱신하고 LiveStore 에 게시한다 — Redis·Influx 성공과 무관한 메모리 계산이다.
기동 시 Influx 의 최근 30분 집계로 채운다(10초 상한, 실패면 빈 채로 — 조회·채우기는 스레드에서).

게시는 증분이다(2026-09-28). 원값이 직전과 같은 조합(약 80%)은 반올림도 버퍼도 건너뛰고, 값이 바뀐
조합만 목록과 JSON 조각을 새 객체로 갈아 끼운다. 게시 맵은 그 맵의 얕은 사본이라 이미 게시된 목록·조각은
누구도 제자리에서 고치지 않는다. 조각은 017 게시기가 표 JSON 의 spark 자리에 그대로 끼운다 — 표 1장에 든
spark 부동소수 4만여 개를 매초 다시 포맷하지 않기 위해서다.

버퍼는 따로 두지 않는다(2026-10-09). 조합마다 기억하는 것은 끝 버킷(int) 하나와 게시 목록뿐이다 — (버킷, 값)
링버퍼에서 실제로 읽던 버킷은 끝 하나였고 값은 게시 목록과 같았다. 그래서 바뀐 조합마다 튜플 둘·deque 조작을
덜고, 분이 바뀌는 틱에 전 조합의 목록을 버퍼에서 다시 뽑지 않는다(직전 목록에 끝값을 붙인다).
"""

import asyncio
import logging
import math
from collections.abc import Iterable
from typing import Protocol

from app.core.influx import SparkBucketRow
from app.core.live_store import LiveStore, SparkKey
from app.core.models import Tick

logger = logging.getLogger("marketlens.spark")

SPARK_LEN = 30  # 버킷 수 = 최근 30분
BUCKET_SEC = 60
RESTORE_TIMEOUT_SEC = 10.0
# `/spreads` 응답의 `spark` 소수 자리(003 §3.2) — 버퍼에 넣는 순간 이 자리로 줄인다. 응답을 만들 때
# 1,400행 × 30개를 매초 다시 반올림하지 않기 위해서다(틱마다 바뀌는 값은 조합당 1개뿐). 원값은 Influx 에 있다.
SPARK_DIGITS = 3


class SparkReader(Protocol):
    """기동 복원 조회 — 실물은 core.influx.InfluxClient, 테스트는 fake."""

    def query_spark(
        self, *, start: int, stop: int, timeout_sec: float | None = None
    ) -> list[SparkBucketRow]: ...


class SparkBuffer:
    def __init__(self) -> None:
        # 조합 → 게시 목록 끝값의 버킷. 목록(_lists)이 곧 버킷 오래된 → 최신의 값이고 같은 버킷은 마지막 값으로 덮인다
        self._last: dict[SparkKey, int] = {}
        # 조합 → 직전 갱신의 버킷·원값 — 둘 다 같으면 넣어도 끝값이 같은 값으로 덮일 뿐이라 건너뛴다.
        # 튜플 하나로 들지 않고 dict 둘로 나눈다 — 바뀐 조합(틱당 약 600개)마다 새 튜플을 만들지 않게
        self._raw_bucket: dict[SparkKey, int] = {}
        self._raw_value: dict[SparkKey, float] = {}
        # 게시용 — 값이 바뀐 조합만 새 목록·새 조각으로 갈아 끼운다(이미 게시된 객체는 고치지 않는다)
        self._lists: dict[SparkKey, list[float]] = {}
        self._json: dict[SparkKey, str] = {}
        # 조합 → 조각에서 끝값 앞까지("[" 또는 "[a,b,"). 같은 분 안에서는 끝값만 바뀌어 앞부분을 다시 쓰지 않는다
        self._heads: dict[SparkKey, str | None] = {}

    def update(self, tick: Tick) -> None:
        bucket = tick.ts // BUCKET_SEC
        raw_bucket = self._raw_bucket
        raw_value = self._raw_value
        put = self._put
        for row in tick.rows:
            # 맵의 키는 base 대문자다(009 §3.6) — 틱 행은 이미 대문자지만 단위 계약이라 그대로 맞춘다
            key = (row.dom, row.fx, row.base.upper())
            fwd = row.fwd
            # 같은 분·같은 원값 — fwd 는 김프 식이라 −0.0 이 나오지 않으므로 == 가 곧 비트까지 같음이다.
            # 버킷이 같으면 원값도 반드시 기억돼 있다(둘은 늘 함께 쓴다)
            if raw_bucket.get(key) == bucket and raw_value[key] == fwd:
                continue
            raw_bucket[key] = bucket
            raw_value[key] = fwd
            put(key, bucket, fwd)

    def seed(self, rows: Iterable[SparkBucketRow]) -> None:
        """복원 — 버킷 오름차순으로 넣는다(조회 결과 순서에 기대지 않는다). 게시할 목록·조각도 함께 찬다."""
        for r in sorted(rows, key=lambda r: r.bucket_ts):
            key = (r.dom, r.fx, r.base.upper())
            # 복원한 값이 끝자리를 바꿨을 수 있다 — 직전 원값 기억을 지워 다음 틱이 건너뛰지 않게
            self._raw_bucket.pop(key, None)
            self._raw_value.pop(key, None)
            self._put(key, r.bucket_ts // BUCKET_SEC, r.fwd)

    def _put(self, key: SparkKey, bucket: int, value: float) -> None:
        value = round(value, SPARK_DIGITS)
        last = self._last.get(key)
        if last == bucket:
            # 같은 분 — 마지막 값만 바뀐다. 게시한 목록은 고치지 않는다 — 사본의 끝값만 바꿔 새 목록으로 건다
            values = self._lists[key].copy()
            values[-1] = value
            head = self._heads[key]
        elif last is None or last < bucket:
            old = self._lists.get(key)
            if old is None:
                values = [value]
                dropped = False
            elif len(old) == SPARK_LEN:
                # 가득 찼으면 붙이는 순간 맨 앞 값이 빠진다
                values = old[1:]
                values.append(value)
                dropped = True
            else:
                values = old + [value]
                dropped = False
            self._last[key] = bucket
            head = self._next_head(key, values, dropped)
            self._heads[key] = head
        else:
            return  # 이미 지난 버킷의 값은 무시한다 — 틱은 시각 순으로 오므로 정상 경로엔 없다
        self._lists[key] = values
        if head is not None and math.isfinite(value):
            self._json[key] = head + repr(value) + "]"
        else:
            # 유한하지 않은 값이 든 조합은 조각을 두지 않는다 — 게시기가 목록을 그대로 인코딩해 NaN 거부도 같게 난다
            self._json.pop(key, None)

    def _next_head(
        self, key: SparkKey, values: list[float], dropped: bool
    ) -> str | None:
        """새 분이 붙은 뒤의 앞부분 — 직전 조각의 끝 ']' 를 ',' 로 바꾸고, 맨 앞 값이 빠졌으면 첫 값을 뗀다."""
        prev = self._json.get(key)
        if prev is None:
            # 첫 값이거나 직전 조각이 없다(유한하지 않은 값) — 새 목록에서 끝값 앞까지를 다시 쓴다
            before = values[:-1]
            if not all(math.isfinite(v) for v in before):
                return None
            return "[" + "".join(repr(v) + "," for v in before)
        if dropped:
            cut = prev.find(",")
            rest = prev[cut + 1 : -1] if cut >= 0 else ""
            return "[" + rest + "," if rest else "["
        return prev[:-1] + ","

    def snapshot(self) -> dict[SparkKey, list[float]]:
        """게시할 목록 맵 — 얕은 사본. 목록은 값이 바뀐 조합만 새로 만들어지고 그 뒤로 고치지 않는다. 값은 이미 응답 자리(3자리)다."""
        return dict(self._lists)

    def fragments(self) -> dict[SparkKey, str]:
        """게시할 JSON 조각 맵 — 얕은 사본. 조각은 그 조합 목록을 공백 없이 `json.dumps` 한 글자와 같다."""
        return dict(self._json)

    def publish(self, store: LiveStore) -> None:
        """목록 맵과 조각 맵을 한 번에 게시한다 — 둘은 늘 같은 순간의 값이다."""
        store.set_spark(self.snapshot(), self.fragments())

    def _adopt(self, other: "SparkBuffer") -> None:
        """복원 — 스레드에서 채운 새 버퍼의 상태를 통째로 넘겨받는다(루프에서, 틱 루프 시작 전의 빈 버퍼에).

        끝 버킷·직전 원값 둘·목록·조각·앞부분을 빠짐없이 넘긴다 — 하나라도 빠지면 복원 뒤 첫 틱이 원값 기억을
        못 찾거나(KeyError) 끝 버킷을 몰라 목록을 엉뚱하게 이어 붙인다.
        """
        self._last = other._last
        self._raw_bucket = other._raw_bucket
        self._raw_value = other._raw_value
        self._lists = other._lists
        self._json = other._json
        self._heads = other._heads


async def restore_spark(
    reader: SparkReader | None, buffer: SparkBuffer, store: LiveStore, now_sec: int
) -> int:
    """기동 시 1회 — 기동 분을 포함한 30개 버킷을 Influx 에서 읽어 채우고 게시한다.

    조회·채우기·게시 맵 만들기는 한 번의 스레드 호출이고(수집 스트림이 이미 도는 루프를 막지 않는다) 루프에서는
    넘겨받기와 LiveStore 게시만 한다. 스레드는 새 버퍼를 채운다 — 상한을 넘겨 버려진 호출이 뒤늦게 끝나도 틱이
    쓰는 버퍼를 건드리지 않는다. 조회의 HTTP 타임아웃도 상한과 같다(009 §3.6). Influx 가 없거나 실패·10초
    초과면 빈 채로 시작해 회차마다 찬다(경고 1줄). 채운 행 수를 돌려준다.
    """
    if reader is None:
        logger.warning("Influx 가 없어 spark 를 복원하지 않는다 — 빈 채로 시작")
        return 0
    start = (now_sec // BUCKET_SEC - (SPARK_LEN - 1)) * BUCKET_SEC

    def fill() -> tuple[
        int, SparkBuffer, dict[SparkKey, list[float]], dict[SparkKey, str]
    ]:
        rows = reader.query_spark(
            start=start, stop=now_sec, timeout_sec=RESTORE_TIMEOUT_SEC
        )
        fresh = SparkBuffer()
        fresh.seed(rows)
        return len(rows), fresh, fresh.snapshot(), fresh.fragments()

    try:
        n, fresh, lists, fragments = await asyncio.wait_for(
            asyncio.to_thread(fill), timeout=RESTORE_TIMEOUT_SEC
        )
    except Exception as exc:
        logger.warning("spark 복원 실패 — 빈 채로 시작: %r", exc)
        return 0
    buffer._adopt(fresh)
    store.set_spark(lists, fragments)
    logger.info("spark 복원: %d점 (조합 %d개)", n, len(lists))
    return n
