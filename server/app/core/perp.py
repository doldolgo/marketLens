"""perp 공통 규칙 — 배수 심볼 정규화·perp 우주 수식·메시지 → perp 행 갱신 (스펙 046 §3.2~3.4).

현물의 QuoteSink 와 같은 자리다 — 커넥터 넷(바이낸스·바이빗·비트겟·Hyperliquid)이 자기 형식을 디코드해
`quote`·`funding` 으로 넘기고, 우주 밖 버리기·호가 넷 검사·부분 필드 합치기·순서 뒤바뀜 방어·펀딩 보류·
펀딩 주기 반영이 전부 여기서 일어난다. 전부 동기다. 수신 경로가 초당 수천 번 지나므로 행 객체를 새로 만들지
않고 필드만 바꾼다(§3.4 성능 원칙).
"""

import math
from collections.abc import Iterable
from datetime import UTC, datetime

from app.core.live_store import LiveStore
from app.core.models import PerpRow

# 접두 배수 — 긴 것을 먼저 본다(`10000NEX` 는 10000 이지 1000 이 아니다, §3.3)
_PREFIXES: tuple[tuple[str, int], ...] = (
    ("1000000", 1_000_000),
    ("10000", 10_000),
    ("1000", 1_000),
    ("1M", 1_000_000),  # 바이낸스·비트겟 `1MBABYDOGE`
    ("k", 1_000),  # Hyperliquid `kPEPE` (047)
)
_SUFFIX_1000 = "1000"  # 바이빗 `SHIB1000`
_INF = math.inf


def split_multiplier(raw_base: str) -> tuple[str, int]:
    """원본 base → (코인 이름, 배수) — 스펙 046 §3.3.

    접두는 뒤에 영문 대문자가 와야 배수다 — `1INCH`·`0G`·`2Z`·`4`·`42`·`1000` 은 배수가 아니다
    (숫자로 시작한다고 자르면 오판). 접미 `1000` 은 앞이 비지 않을 때만. 결과가 비면 원본 그대로.
    """
    for prefix, mult in _PREFIXES:
        rest = raw_base[len(prefix) :]
        if raw_base.startswith(prefix) and rest[:1].isupper() and rest[:1].isalpha():
            return rest, mult
    if raw_base.endswith(_SUFFIX_1000):
        rest = raw_base[: -len(_SUFFIX_1000)]
        if rest:
            return rest, 1_000
    return raw_base, 1


def perp_universe(kimp: set[str], per_source: Iterable[set[str]]) -> set[str]:
    """perp 우주 = {2곳 이상} ∪ (김프 우주 ∩ {1곳 이상}) — 스펙 046 §3.2 (2026-10-08 사람 결정)."""
    count: dict[str, int] = {}
    for bases in per_source:
        for base in bases:
            count[base] = count.get(base, 0) + 1
    out = {b for b, n in count.items() if n >= 2}
    out |= {b for b in count if b in kimp}
    return out


def _valid(v: float | None) -> bool:
    # 연쇄 비교 하나로 "있고·0 초과·유한" — None 은 비교 전에 빠지고 NaN 은 모든 비교가 거짓이다
    return v is not None and 0.0 < v < _INF


class PerpSink:
    def __init__(self, store: LiveStore) -> None:
        self._store = store
        self._universe: set[str] = (
            set()
        )  # 대문자 base. 비어 있으면 아무 행도 저장하지 않는다
        # 행이 아직 없을 때 온 펀딩 — (source, BASE) → 부분 필드. 첫 호가 때 함께 실린다 (§3.4)
        self._pending: dict[tuple[str, str], dict[str, object]] = {}
        # 목록·주기 조회가 준 정산 주기 — (source, BASE) → 시간. 행이 생길 때 실린다 (§3.4)
        self._interval: dict[tuple[str, str], int] = {}

    # --- perp 우주 (§3.2) ---

    def set_universe(self, bases: set[str]) -> int:
        """우주를 바꾼다. 빠진 base 의 행·보류 펀딩은 그 자리에서 지운다. 지운 행 수를 돌려준다."""
        self._universe = {b.upper() for b in bases}
        for key in [k for k in self._pending if k[1] not in self._universe]:
            del self._pending[key]
        return self._store.retain_perp_bases(self._universe)

    @property
    def universe(self) -> set[str]:
        return set(self._universe)

    # --- 목록·주기 (§3.4~3.7) ---

    def set_interval(self, source: str, base: str, hours: int | None) -> None:
        """정산 주기 1건 — 지금 행에 바로, 없으면 행이 생길 때. 목록 갱신마다 그 원천의 전 심볼에 부른다."""
        key = base.upper()
        if hours is None:
            self._interval.pop((source, key), None)
        else:
            self._interval[(source, key)] = hours
        row = self._store.perp_row(source, key)
        if row is not None:
            row.funding_interval_h = hours

    # --- 메시지 (§3.4) ---

    def quote(
        self,
        *,
        source: str,
        base: str,
        native_symbol: str,
        multiplier: int,
        bid: float | None,
        ask: float | None,
        bid_size: float | None,
        ask_size: float | None,
        quote_ts: int,
        received_at_ms: int,
    ) -> None:
        """호가 메시지 1건 — 넷을 배수로 나눠·곱해 한 번에 교체. 온 필드만 바꾸고 검사는 합친 결과로.

        None 은 "이 메시지에 안 왔다"(바이빗 delta) — 지금 행의 값을 쓴다. 스냅샷 메시지에 필드가 빠진 것은
        커넥터가 NaN 으로 넘긴다(없음 = 무효 — 지금 행의 값으로 메우지 않는다).

        넷 중 하나라도 없거나 0 이하·NaN·inf 면 호가는 그대로 두고 수신 시각만 센다(행이 없으면 만들지 않는다).
        호가 시각이 지금 행보다 오래된 메시지는 버린다(순서 뒤바뀜 방어). 우주 밖 base 는 버린다.
        """
        key = base.upper()
        if key not in self._universe:
            return
        row = self._store.perp_row(source, key)
        if row is not None and quote_ts < row.quote_ts:
            return
        # 원본 단위에서 검사하고 1코인 단위로 바꾼다 — 안 온 필드는 지금 행의 값(이미 1코인 단위)을 그대로 쓴다
        if _valid(bid):
            n_bid = bid / multiplier  # type: ignore[operator]
        elif bid is None and row is not None:
            n_bid = row.bid
        else:
            n_bid = None
        if _valid(ask):
            n_ask = ask / multiplier  # type: ignore[operator]
        elif ask is None and row is not None:
            n_ask = row.ask
        else:
            n_ask = None
        if _valid(bid_size):
            n_bid_size = bid_size * multiplier  # type: ignore[operator]
        elif bid_size is None and row is not None:
            n_bid_size = row.bid_size
        else:
            n_bid_size = None
        if _valid(ask_size):
            n_ask_size = ask_size * multiplier  # type: ignore[operator]
        elif ask_size is None and row is not None:
            n_ask_size = row.ask_size
        else:
            n_ask_size = None
        now = datetime.fromtimestamp(received_at_ms / 1000, tz=UTC)
        if n_bid is None or n_ask is None or n_bid_size is None or n_ask_size is None:
            if row is not None:
                row.updated_at = now  # 수신은 센다, 호가는 그대로
            return
        if row is None:
            row = PerpRow(
                source=source,
                base=key,
                native_symbol=native_symbol,
                multiplier=multiplier,
                bid=n_bid,
                ask=n_ask,
                bid_size=n_bid_size,
                ask_size=n_ask_size,
                quote_ts=quote_ts,
                funding_interval_h=self._interval.get((source, key)),
                updated_at=now,
            )
            held = self._pending.pop((source, key), None)
            if held is not None:
                # 보류한 펀딩을 첫 호가와 함께 싣는다 — 마크가는 보류 때 이미 배수로 나눠 뒀다
                for name, value in held.items():
                    setattr(row, name, value)
            self._store.put_perp_row(row)
            return
        row.bid = n_bid
        row.ask = n_ask
        row.bid_size = n_bid_size
        row.ask_size = n_ask_size
        row.quote_ts = quote_ts
        row.updated_at = now

    def funding(
        self,
        *,
        source: str,
        base: str,
        multiplier: int,
        received_at_ms: int,
        funding_rate: float | None = None,
        next_funding_ms: int | None = None,
        mark: float | None = None,
        funding_interval_h: int | None = None,
    ) -> None:
        """펀딩 메시지 1건 — 온 것만 갱신(None 은 "안 왔다"). 행이 없으면 보류했다가 첫 호가 때 싣는다.

        숫자가 아닌 값은 커넥터가 None 으로 넘긴다 — 그 필드는 두고 나머지만 바뀐다. 마크가는 배수로 나눈다.
        """
        key = base.upper()
        if key not in self._universe:
            return
        fields: dict[str, object] = {}
        if funding_rate is not None:
            fields["funding_rate"] = funding_rate
        if next_funding_ms is not None:
            fields["next_funding_ms"] = next_funding_ms
        if mark is not None:
            fields["mark"] = mark / multiplier
        if funding_interval_h is not None:
            fields["funding_interval_h"] = funding_interval_h
            self._interval[(source, key)] = funding_interval_h
        if not fields:
            return
        row = self._store.perp_row(source, key)
        if row is None:
            held = self._pending.get((source, key))
            if held is None:
                self._pending[(source, key)] = fields
            else:
                held.update(fields)
            return
        for name, value in fields.items():
            setattr(row, name, value)
        row.updated_at = datetime.fromtimestamp(received_at_ms / 1000, tz=UTC)
