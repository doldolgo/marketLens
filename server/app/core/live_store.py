"""메모리 저장소 — 실시간 조회 API 의 유일한 진실 (스펙 001 §3.3).

uvicorn 워커 1개 + 단일 이벤트 루프 전제라 잠금이 없다. 쓰기는 행 단위다 —
거래소 단위 통째 교체는 없다(§3.5-4). 조회는 전부 동기(await 없음)다.
스냅샷 호가 커넥터는 프레임 반영을 미룰 수 있다(§3.5-5) — 행·시세를 읽는 메서드가 읽기 직전에 미룬 것을 반영한다.
"""

from collections.abc import Callable
from datetime import datetime

from app.core.models import PerpRow, Rate, Row, StreamState, Tick

SparkKey = tuple[str, str, str]  # (dom, fx, base)
_NO_SPARK: list[
    float
] = []  # 조합에 추이가 없을 때 돌려주는 빈 목록 — 공유되므로 고치지 않는다


class LiveStore:
    def __init__(self) -> None:
        # 거래소 id → (base 대문자 → Row). base 조회는 대소문자를 무시한다.
        self._snapshots: dict[str, dict[str, Row]] = {}
        self._rates: dict[str, Rate] = {}
        self._streams: dict[str, StreamState] = {}
        self._received_at: int | None = None
        self._tick: Tick | None = None  # 틱 슬롯 — 가장 최근 틱 1개 (§3.6)
        self._spark: dict[SparkKey, list[float]] = {}  # 009 가 게시한다
        # 같은 게시의 조합별 JSON 조각 — 017 게시기가 표 JSON 의 spark 자리에 끼운다
        self._spark_json: dict[SparkKey, str] = {}
        # perp 행 맵(046 §3.1) — 원천 id → (base 대문자 → PerpRow). 현물 행 맵과 분리돼 (exchange, base) 조회에 섞이지 않는다
        self._perp: dict[str, dict[str, PerpRow]] = {}
        # 직전 행 없이 새로 생긴 (거래소, base 대문자) — 틱이 take_new_rows 로 가져가 입출금 캐시를 그 행에만
        # 반영한다(001 §3.6-1). 교체된 행은 물려받기로 이미 같은 3필드라 넣지 않는다. 키 수(≈2천)를 넘지 않는다
        self._new_keys: set[tuple[str, str]] = set()
        # 커넥터가 미뤄 둔 시세 프레임을 반영하는 함수들 — 행·시세를 읽는 메서드가 읽기 전에 모두 부른다(001 §3.5-5).
        # 커넥터는 미룬 프레임이 처음 생길 때 한 번만 등록하고, 부르면 목록에서 빠진다
        self._deferred: list[Callable[[], None]] = []

    # --- 쓰기 (수집 경로만 부른다) ---

    def put_row(self, row: Row, now: datetime) -> None:
        """행 1개 교체. 입출금 3필드는 직전 행에서 물려받는다 (§3.3·§3.5-3)."""
        key = row.base.upper()
        table = self._snapshots.setdefault(row.exchange, {})
        prev = table.get(key)
        if prev is not None:
            row.deposit_enabled = prev.deposit_enabled
            row.withdrawal_enabled = prev.withdrawal_enabled
            row.networks = prev.networks
        else:
            # 물려받을 값이 없는 행만 — 흔한 경로(교체)는 비용이 없다
            self._new_keys.add((row.exchange, key))
        row.updated_at = now
        table[key] = row

    def put_rows(self, rows: list[Row], now: datetime) -> None:
        """행 여러 개를 순서대로 put_row — 시드·테스트용 편의. 다른 행은 건드리지 않는다."""
        for row in rows:
            self.put_row(row, now)

    def defer(self, settle: Callable[[], None]) -> None:
        """커넥터가 시세 프레임 반영을 미뤘다 — 다음 읽기 전에 `settle` 을 부른다 (001 §3.5-5).

        커넥터는 미룬 프레임이 없다가 처음 생길 때 한 번만 등록한다(같은 함수를 두 번 넣지 않는다). 수신 경로가
        초당 수천 번 지나는 자리라 여기서는 목록에 붙이기만 한다.
        """
        self._deferred.append(settle)

    def settle(self) -> None:
        """미뤄 둔 시세 프레임을 지금 전부 반영한다 — 읽기 메서드가 먼저 부르고, 우주가 바뀌기 직전에도 부른다.

        반영 함수가 도는 동안 새로 등록된 것까지 비울 때까지 돈다. 반영은 동기라(await 없음) 그 사이 수신이 끼지 않는다.
        """
        while self._deferred:
            pending, self._deferred = self._deferred, []
            for settle in pending:
                settle()

    def take_new_rows(self) -> dict[str, list[Row]]:
        """직전 호출 뒤 직전 행 없이 새로 생긴 행 — {거래소: [지금 행]} 을 돌려주고 비운다 (001 §3.6-1).

        행은 지금 표에서 키로 다시 찾는다 — 그 사이 교체됐으면 지금 행, 지워졌으면 빠진다. 틱 루프만 부른다.
        미뤄 둔 프레임을 먼저 반영한다 — 그 반영이 새 행을 만들 수 있다.
        """
        if self._deferred:
            self.settle()
        keys = self._new_keys
        if not keys:
            return {}
        self._new_keys = set()
        out: dict[str, list[Row]] = {}
        snapshots = self._snapshots
        for exchange, key in keys:
            table = snapshots.get(exchange)
            row = table.get(key) if table is not None else None
            if row is not None:
                rows = out.get(exchange)
                if rows is None:
                    out[exchange] = [row]
                else:
                    rows.append(row)
        return out

    def remove_row(self, exchange: str, base: str) -> None:
        self._snapshots.get(exchange, {}).pop(base.upper(), None)

    def retain_bases(self, bases: set[str]) -> int:
        """우주(§3.2) 밖 base 의 행을 전부 지운다 — 상폐 소멸. 지운 행 수를 돌려준다."""
        allowed = {b.upper() for b in bases}
        removed = 0
        for table in self._snapshots.values():
            for key in [k for k in table if k not in allowed]:
                del table[key]
                removed += 1
        return removed

    # --- perp 행 (046 §3.1) — 쓰기는 행 1개 단위와 우주 밖 일괄 삭제뿐, 필드 갱신은 행 객체 제자리에서 ---

    def put_perp_row(self, row: PerpRow) -> None:
        # 원천 표는 여기서 한 번만 만들고 바꿔 끼우지 않는다 — PerpSink 가 perp_table 로 붙잡고 있다
        self._perp.setdefault(row.source, {})[row.base.upper()] = row

    def remove_perp_row(self, source: str, base: str) -> None:
        self._perp.get(source, {}).pop(base.upper(), None)

    def retain_perp_bases(self, bases: set[str]) -> int:
        """perp 우주(046 §3.2) 밖 base 의 perp 행을 전부 지운다. 지운 행 수를 돌려준다."""
        allowed = {b.upper() for b in bases}
        removed = 0
        for table in self._perp.values():
            for key in [k for k in table if k not in allowed]:
                del table[key]
                removed += 1
        return removed

    def set_rate(self, exchange: str, ask: float, bid: float, now: datetime) -> None:
        self._rates[exchange] = Rate(
            exchange=exchange, ask=ask, bid=bid, updated_at=now
        )

    def stream(self, exchange: str) -> StreamState:
        """거래소 스트림 상태 — 없으면 만든다. 커넥터가 이 객체를 직접 갱신한다."""
        state = self._streams.get(exchange)
        if state is None:
            state = StreamState()
            self._streams[exchange] = state
        return state

    def push_tick(self, tick: Tick) -> Tick | None:
        """새 틱을 슬롯에 넣고 직전 틱을 돌려준다 — 그것이 인계 대상이다 (009 §3.3)."""
        prev, self._tick = self._tick, tick
        return prev

    def mark_received(self, ts: int) -> None:
        """마지막 틱 시각(epoch 초)."""
        self._received_at = ts

    def set_spark(
        self,
        spark: dict[SparkKey, list[float]],
        fragments: dict[SparkKey, str] | None = None,
    ) -> None:
        """spark 목록 맵과 그 JSON 조각 맵을 함께 바꾼다(009). 조각이 없는 조합은 게시기가 목록을 인코딩한다."""
        self._spark = spark
        self._spark_json = fragments if fragments is not None else {}

    # --- 조회 (전부 동기) ---
    # 행·시세를 읽는 메서드는 미뤄 둔 시세 프레임을 먼저 반영한다(001 §3.5-5) — 읽는 쪽(틱·표·분석 API·헬스)은 받은
    # 프레임이 모두 반영된 상태를 본다. 싱크가 수신 경로에서 행을 찾는 `row` 만 반영하지 않는다(아래).

    def get_all(
        self, exchange: str | None = None, base: str | None = None
    ) -> list[Row]:
        if self._deferred:
            self.settle()
        base_key = base.upper() if base is not None else None
        out: list[Row] = []
        for ex, table in self._snapshots.items():
            if exchange is not None and ex != exchange:
                continue
            for key, row in table.items():
                if base_key is not None and key != base_key:
                    continue
                out.append(row)
        return out

    def get(self, exchange: str, base: str) -> Row | None:
        if self._deferred:
            self.settle()
        return self._snapshots.get(exchange, {}).get(base.upper())

    def row(self, exchange: str, key: str) -> Row | None:
        """수신 경로 전용 — 미룬 프레임을 반영하지 않고 지금 행을 찾는다. `key` 는 이미 대문자.

        싱크의 체결가(`QuoteSink.trade`)가 부른다. 여기서 반영하면 체결 프레임마다 미룬 호가를 전부 파싱하게 되어 미루는
        뜻이 없어진다. 반영 전 행에 체결가를 실어도 결과가 같은 근거: 미룬 호가가 나중에 새 행을 만들 때 싱크가 기억한
        마지막 체결가를 싣는다(001 §3.5-2).
        """
        table = self._snapshots.get(exchange)
        return table.get(key) if table is not None else None

    def perp_row(self, source: str, base: str) -> PerpRow | None:
        return self._perp.get(source, {}).get(base.upper())

    def perp_table(self, source: str) -> dict[str, PerpRow] | None:
        """원천 하나의 perp 행 표 그 자체(BASE 대문자 → 행) — 아직 행이 한 번도 없었으면 None, 만들지 않는다.

        PerpSink 가 원천마다 이 표를 붙잡고 초당 수천 번 `table.get(BASE)` 로 찾는다 — perp_row 처럼 매번 빈 dict
        기본값을 만들고 upper 를 한 번 더 하지 않으려고. 그래서 원천 표는 한 번 만들면 바꿔 끼우지 않는다: put 은
        setdefault, remove·retain 은 제자리에서 지운다. 없을 때 만들지 않는 것은 perp_rows 의 원천 순서가 첫 행이
        생긴 순서 그대로여야 해서다. 호출자는 이 표를 고치지 않는다(조회 전용).
        """
        return self._perp.get(source)

    def perp_rows(self, source: str | None = None) -> list[PerpRow]:
        """perp 행 전체 또는 원천 하나의 행 — 현물 get_all 과 같은 꼴, 섞이지 않는다."""
        out: list[PerpRow] = []
        for src, table in self._perp.items():
            if source is not None and src != source:
                continue
            out.extend(table.values())
        return out

    def get_rate(self, exchange: str) -> Rate | None:
        if self._deferred:
            self.settle()
        return self._rates.get(exchange)

    def rates(self) -> dict[str, Rate]:
        """전체 시세 사본."""
        if self._deferred:
            self.settle()
        return dict(self._rates)

    def stream_state(self, exchange: str) -> StreamState | None:
        """읽기 전용 조회 — 등록된 스트림이 없으면 None(만들지 않는다)."""
        return self._streams.get(exchange)

    def streams(self) -> dict[str, StreamState]:
        return dict(self._streams)

    @property
    def tick(self) -> Tick | None:
        return self._tick

    @property
    def received_at(self) -> int | None:
        return self._received_at

    def spark(self, dom: str, fx: str, base: str) -> list[float]:
        """게시된 목록 그 자체 — 호출자는 고치지 않는다. 맵은 틱마다 얕은 사본으로 교체되고 값이 바뀐 조합의
        목록만 새로 만들어진다(009) — 게시된 목록은 누구도 제자리에서 고치지 않으므로 복사할 이유가 없고,
        표 1,400행이 매초 읽는 자리라 복사 비용을 안 낸다."""
        return self._spark.get((dom, fx, base.upper()), _NO_SPARK)

    def spark_json(self) -> dict[SparkKey, str]:
        """같은 게시의 JSON 조각 맵 그 자체 — 호출자는 고치지 않는다. 조각은 그 조합 목록을 공백 없이
        `json.dumps` 한 글자와 같고, 값이 유한하지 않은 조합은 조각이 없다(017 게시기가 목록을 인코딩한다)."""
        return self._spark_json

    def is_empty(self) -> bool:
        if self._deferred:
            self.settle()
        return not any(self._snapshots.values())
