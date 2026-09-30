"""스프레드 표 계산 — 순수 계산. 저장소를 인자로 받고 전역을 import 하지 않는다 (스펙 003 §3.2).

이 기능의 고정값(§3.1)도 여기 둔다 — 다른 기능이 쓰게 되는 날 core 로 옮긴다.
"""

import time
from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.collect import RefreshSummary
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.networks import WalletMemo, wallet_fields
from app.core.orderbook import WALK_EPSILON, walk_levels
from app.core.premium import premium_percent
from app.features.spreads.models import (
    RefreshFailure,
    RefreshRate,
    RefreshResponse,
    RefreshSnapshot,
    SpreadsResponse,
)

# 고정값 — 스펙 003 §3.1
BASE_EXCHANGE = "upbit"
DOMESTIC_QUOTE = "KRW"
FOREIGN_QUOTE = "USDT"
STALE_AFTER_SEC = 5.0
# 행 자체가 이만큼 안 바뀌면 스트림이 살아 있어도 그 행의 실제 경과 초를 age 로 낸다 (§3.2-4)
ROW_STALE_SEC = 300.0
_ROW_STALE = timedelta(seconds=ROW_STALE_SEC)
# USDT 는 매 사이클(1초) 관측이 정상 — 60초 무관측은 구조적 문제다 (스펙 008 §3.2)
USDT_STALE_WARN_SEC = 60.0
EXCLUDED_COINS: frozenset[str] = frozenset()

# 체결 규모(USD) — 표의 모든 행이 이 규모로 호가를 걷는다 (스펙 003 §3.2-0)
# 화면·푸시(017)는 이 기본값 하나로 고정이고, 쿼리 `notional` 은 진단용으로만 남는다
DEFAULT_NOTIONAL = 1_000.0


class MarketDataNotFoundError(Exception):
    """메모리에 계산 재료가 없다 → 404 `market_data_not_found` (라우터가 변환)."""

    def __init__(self, message: str, detail: dict[str, object]) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


def _walk_amount(levels: list[list[float]], amount: float) -> tuple[float, float]:
    """금액 기준 걷기의 (체결 수량, 체결 금액) — core `walk_amount` 와 같은 연산·같은 순서 (004 §3.1).

    표 한 장에 걷기가 수천 번이라 결과 객체·제너레이터를 만들지 않는다. 소진 여부는 사는 쪽에선 쓰지 않는다.
    """
    if amount <= 0 or not levels:
        return 0.0, 0.0
    remaining = amount
    quantity = 0.0
    filled = 0.0
    for level in levels:
        price = level[0]
        size = level[1]
        level_amount = price * size
        if level_amount >= remaining:
            quantity += remaining / price
            filled += remaining
            break
        quantity += size
        filled += level_amount
        remaining -= level_amount
    return quantity, filled


def _walk_quantity(levels: list[list[float]], quantity: float) -> tuple[float, float]:
    """수량 기준 걷기의 (체결 수량, 체결 금액) — core `walk_quantity` 와 같은 연산·같은 순서."""
    if quantity <= 0 or not levels:
        return 0.0, 0.0
    remaining = quantity
    filled_qty = 0.0
    filled_amount = 0.0
    for level in levels:
        price = level[0]
        size = level[1]
        if size >= remaining:
            filled_qty += remaining
            filled_amount += remaining * price
            break
        filled_qty += size
        filled_amount += size * price
        remaining -= size
    return filled_qty, filled_amount


def _market(
    row: Row,
    store: LiveStore,
    stream_ages: dict[str, float],
    now_ms: float,
    now: datetime,
    cutoff: datetime,
) -> list[Any]:
    """시장(거래소, 코인) 하나의 표 재료 — 표 한 장에서 시장마다 한 번만 만든다 (§3.2 표 계산 경로).

    한 시장이 여러 행에 나온다(해외 시장은 국내 두 곳, 국내 시장은 해외 세 곳). 자리는
    [age, 최우선 bid, 최우선 ask, 최우선 4값이 전부 양수인가, 사는 쪽 걷기(처음 쓸 때 채운다), asks, bids].
    age 는 그 거래소 스트림의 마지막 시세 수신 이후 경과 초이고(거래소마다 한 번 구한다), 행 자체가
    300초 넘게 안 바뀌었으면 둘 중 큰 값이다(§3.2-4 — 조용한 코인의 호가는 안 바뀌어도 현재값이다).
    """
    exchange = row.exchange
    age = stream_ages.get(exchange)
    if age is None:
        state = store.stream_state(exchange)
        # 행은 스트림 메시지로만 생기므로 행이 있는 거래소의 수신 시각은 항상 있다
        assert state is not None and state.last_message_at is not None
        age = stream_ages[exchange] = (now_ms - state.last_message_at) / 1000
    updated = row.updated_at
    assert updated is not None
    if updated <= cutoff:
        # 거래 정지·심볼 장애 — 스트림은 살아 있는데 이 코인 프레임만 안 온다. timedelta 는 정수 µs 라
        # 이 비교는 "행 경과 초 ≥ 300" 과 같다
        age = max(age, (now - updated).total_seconds())
    asks = walk_levels(row, "asks")
    bids = walk_levels(row, "bids")
    bid = bids[0] if bids else None
    ask = asks[0] if asks else None
    # 가격뿐 아니라 잔량도 본다 — 잔량 0 이면 걷어도 체결 수량이 0 이라 평균가가 0 이 되고 순값이 0 으로 나눈다
    ok = not (
        bid is None
        or ask is None
        or bid[0] <= 0
        or bid[1] <= 0
        or ask[0] <= 0
        or ask[1] <= 0
    )
    return [age, bid, ask, ok, None, asks, bids]


def build_table(
    store: LiveStore,
    *,
    now: datetime | None = None,
    excluded: Collection[str] | None = None,
    notional: float = DEFAULT_NOTIONAL,
    day_open: Mapping[tuple[str, str], float] | None = None,
) -> dict[str, object]:
    """전 (국내 × 해외 × 코인) 페어의 김프/역프 표 — 스펙 003 §3.2.

    `day_open` 은 026 의 기준가 장부((국내 거래소, 코인) → KST 00시 가격) — 없으면 `dayChg` 는 전부 null.

    응답 모양(camelCase 키·순서) 그대로의 dict 를 돌려준다 — 017 게시기가 이걸 바로 json 으로
    만든다. 모델이 필요하면 `build_spreads` (테스트·문서용, 같은 계산).

    표 조립 전체가 `await` 없이 끝난다 — 그것이 이 함수가 수집 락 없이도 한 응답 안에서
    스냅샷 교체 전·후 호가를 섞지 않는 유일한 근거다(§2). 걷기를 async 로 만들지 않는다.
    """
    now = now if now is not None else datetime.now(UTC)
    excluded_upper = {
        c.upper() for c in (excluded if excluded is not None else EXCLUDED_COINS)
    }

    # 1. 기준 거래소 환율 확인
    base_rate = store.get_rate(BASE_EXCHANGE)
    if base_rate is None or base_rate.ask <= 0 or base_rate.bid <= 0:
        raise MarketDataNotFoundError(
            f"메모리에 {BASE_EXCHANGE} 거래소의 KRW-USDT 환율이 없습니다. POST /refresh 로 수집했는지 확인하세요.",
            {"exchange": BASE_EXCHANGE},
        )

    # 2. 스냅샷을 호가통화로 나눈다 — KRW → 국내, USDT → 해외, 그 외 무시
    domestic: dict[str, dict[str, Row]] = {}
    foreign: dict[str, dict[str, Row]] = {}
    for row in store.get_all():
        if row.quote == DOMESTIC_QUOTE:
            domestic.setdefault(row.exchange, {})[row.base.upper()] = row
        elif row.quote == FOREIGN_QUOTE:
            foreign.setdefault(row.exchange, {})[row.base.upper()] = row
    if not domestic or not foreign:
        raise MarketDataNotFoundError(
            "스프레드를 계산할 스냅샷이 부족합니다 (국내 KRW / 해외 USDT). 먼저 POST /refresh 로 수집하세요.",
            {"domestic": sorted(domestic), "foreign": sorted(foreign)},
        )

    # 3~4. 페어 생성 — 국내 거래소마다 자기 환율, 환율 없는 국내 거래소는 행 전체가 빠진다
    rows_out: list[dict[str, object]] = []
    buy_memo: BuyMemo = {}  # 이 표 한 장의 수명 — 다음 회차는 새 호가로 새로 걷는다
    for dom_ex, dom_table in domestic.items():
        rate = store.get_rate(dom_ex)
        if rate is None or rate.ask <= 0 or rate.bid <= 0:
            continue  # 남의 환율을 빌리면 테더 프리미엄이 섞인다
        for fx_ex, fx_table in foreign.items():
            if fx_ex == dom_ex:
                continue
            for base in dom_table.keys() & fx_table.keys():
                if base in excluded_upper:
                    continue
                rows_out.append(
                    _build_row(
                        base,
                        dom_table[base],
                        fx_table[base],
                        rate.ask,
                        rate.bid,
                        store,
                        now,
                        notional,
                        buy_memo,
                        day_open if day_open is not None else {},
                    )
                )

    # 5. 정렬 고정
    rows_out.sort(key=lambda r: (r["sym"], r["dom"], r["fx"]))

    # 6. 최상위 값 + USDT 시세 미갱신 경고 — 시세가 "있긴 한데 낡은" 거래소만 (스펙 008 §3.2)
    warnings: list[str] = []
    for ex in sorted(store.rates()):
        rate = store.get_rate(ex)
        if rate is None or rate.ask <= 0 or rate.bid <= 0:
            continue
        rate_age = (now - rate.updated_at).total_seconds()
        if rate_age > USDT_STALE_WARN_SEC:
            warnings.append(
                f"{ex} USDT 시세가 {int(rate_age)}초째 갱신되지 않았습니다 — "
                "이 거래소 행의 김프/역프는 낡은 시세 기준입니다."
            )

    received = store.received_at
    return {
        "rate": float(base_rate.ask),
        "notional": float(notional),
        "rows": rows_out,
        "warnings": warnings,
        "dataReceivedAt": received * 1000 if received is not None else None,
        "fetchedAt": int(time.time() * 1000),
    }


def build_spreads(
    store: LiveStore,
    *,
    now: datetime | None = None,
    excluded: Collection[str] | None = None,
    notional: float = DEFAULT_NOTIONAL,
    day_open: Mapping[tuple[str, str], float] | None = None,
) -> SpreadsResponse:
    """`build_table` 과 같은 표를 `SpreadsResponse` 모델로 — 테스트·문서용. 뜨거운 경로는 dict 다."""
    return SpreadsResponse.model_validate(
        build_table(
            store, now=now, excluded=excluded, notional=notional, day_open=day_open
        )
    )


def build_refresh(result: RefreshSummary, store: LiveStore) -> RefreshResponse:
    """001 즉시 갱신 트리거 요약 → POST /refresh 응답 — 스펙 003 §3.3.

    `snapshots[]` 는 거래소당 1항목이고 001 요약의 REST 호출 수도 여기 싣는다(006 이 원소를 확장).
    """
    snapshots = [
        RefreshSnapshot(
            exchange=ex,
            saved=n,
            calls=result.calls.get(ex, 0),
            # 입출금 조회 성공 여부 — 조회 자체가 없으면(006 배선 전 테스트 등) false (006 §3.5)
            wallet_status_available=result.wallet_status_available.get(ex, False),
        )
        for ex, n in result.saved.items()
    ]
    usdkrw: list[RefreshRate] = []
    for ex in result.rates_observed:
        rate = store.get_rate(ex)
        if rate is not None:
            usdkrw.append(RefreshRate(exchange=ex, ask=rate.ask, bid=rate.bid))
    return RefreshResponse(
        snapshots=snapshots,
        usdkrw=usdkrw,
        total_saved=sum(result.saved.values()),
        failures=[RefreshFailure(**f) for f in result.failures],
        warnings=result.warnings,
        duration_ms=result.duration_ms,
        fetched_at=result.fetched_at,
    )
