"""스프레드 표 계산 — 순수 계산. 저장소를 인자로 받고 전역을 import 하지 않는다 (스펙 003 §3.2).

이 기능의 고정값(§3.1)도 여기 둔다 — 다른 기능이 쓰게 되는 날 core 로 옮긴다.
"""

import time
from collections.abc import Collection
from datetime import UTC, datetime

from app.core.collector import CycleResult
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.networks import pick_domestic
from app.core.premium import premium_percent
from app.features.spreads.models import (
    RefreshFailure,
    RefreshRate,
    RefreshResponse,
    RefreshSnapshot,
    SpreadRow,
    SpreadsResponse,
)

# 고정값 — 스펙 003 §3.1
BASE_EXCHANGE = "upbit"
DOMESTIC_QUOTE = "KRW"
FOREIGN_QUOTE = "USDT"
STALE_AFTER_SEC = 5.0
# USDT 는 매 사이클(1초) 관측이 정상 — 60초 무관측은 구조적 문제다 (스펙 008 §3.2)
USDT_STALE_WARN_SEC = 60.0
EXCLUDED_COINS: frozenset[str] = frozenset()


class MarketDataNotFoundError(Exception):
    """메모리에 계산 재료가 없다 → 404 `market_data_not_found` (라우터가 변환)."""

    def __init__(self, message: str, detail: dict[str, object]) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


def _wallet_fields(
    dom_row: Row, fx_row: Row
) -> tuple[str | None, bool | None, bool | None, bool | None, bool | None]:
    """행의 입출금 5필드 (netDom, depDom, wdDom, depFx, wdFx) — 스펙 006 §3.7.

    국내 망이 기준이다. status=fail 행도 같은 규칙.
    """
    if not dom_row.networks:
        # 1. 국내 망 목록이 비면(키 없음·망 정보 없는 과도기) 코인 단위 값 그대로
        return (
            None,
            dom_row.deposit_enabled,
            dom_row.withdrawal_enabled,
            fx_row.deposit_enabled,
            fx_row.withdrawal_enabled,
        )
    # 2. 국내 망·판정·해외 망을 고른다 (§3.6 tie-break)
    dom_net, verdict, fx_net = pick_domestic(dom_row.networks, fx_row.networks)
    dep_fx: bool | None
    wd_fx: bool | None
    if verdict == "matched" and fx_net is not None:
        # 3. 맞춘 해외 망의 값
        dep_fx, wd_fx = fx_net.dep, fx_net.wd
    elif verdict == "absent":
        # 4. 해외가 그 망을 안 다룸 = 옮길 길 없음
        dep_fx = wd_fx = False
    elif fx_row.networks:
        # 5. 해외 망이 있는데 못 맞춤 = 모른다고 말한다 — 코인 단위로 접으면 낙관 편향
        dep_fx = wd_fx = None
    else:
        # 5. 해외 망 정보가 아예 없으면 해외 코인 단위 값
        dep_fx = fx_row.deposit_enabled
        wd_fx = fx_row.withdrawal_enabled
    return (dom_net.name, dom_net.dep, dom_net.wd, dep_fx, wd_fx)


def _age_seconds(row: Row, now: datetime) -> float:
    """스냅샷 경과 초. updated_at 은 저장소 적재 시각이라 항상 채워져 있다."""
    assert row.updated_at is not None
    return (now - row.updated_at).total_seconds()


def _build_row(
    base: str,
    dom_row: Row,
    fx_row: Row,
    rate_ask: float,
    rate_bid: float,
    now: datetime,
) -> SpreadRow:
    """행 하나의 규칙 — 스펙 003 §3.2-4."""
    dom_bid = dom_row.bids[0] if dom_row.bids else None
    dom_ask = dom_row.asks[0] if dom_row.asks else None
    fx_bid = fx_row.bids[0] if fx_row.bids else None
    fx_ask = fx_row.asks[0] if fx_row.asks else None

    # age 는 양측 중 오래된 쪽 기준, 0 미만이면 0
    age = max(0.0, _age_seconds(dom_row, now), _age_seconds(fx_row, now))

    failed = (
        dom_bid is None
        or dom_ask is None
        or fx_bid is None
        or fx_ask is None
        or fx_ask[0] <= 0
        # 국내 호가 0 은 스펙 문면엔 없지만 rev 의 분모라 0 나눗셈 500 을 만든다 — fail 로 방어
        or dom_ask[0] <= 0
        or dom_bid[0] <= 0
    )
    if failed:
        # fail 이어도 입출금 값과 age 는 싣는다
        fwd = rev = usd = liq_dom = liq_fx = 0.0
        row_rate_ask = row_rate_bid = 0.0
        status = "fail"
    else:
        # 체결되는 쪽 호가: 김프는 해외 ask 에 사서 국내 bid 에 판다, 역프는 반대
        fwd = premium_percent(buy_krw=fx_ask[0] * rate_ask, sell_krw=dom_bid[0])
        rev = premium_percent(buy_krw=dom_ask[0], sell_krw=fx_bid[0] * rate_bid)
        # 유동성은 최우선 1단계의 매수·매도 금액 중 작은 쪽
        liq_dom = min(dom_bid[0] * dom_bid[1], dom_ask[0] * dom_ask[1]) / rate_ask
        liq_fx = min(fx_bid[0] * fx_bid[1], fx_ask[0] * fx_ask[1])
        usd = fx_row.price
        row_rate_ask = rate_ask
        row_rate_bid = rate_bid
        status = "stale" if age >= STALE_AFTER_SEC else "ok"

    # 입출금 5필드는 망 판정으로 채운다 — fail 행도 같은 규칙 (006 §3.7)
    net_dom, dep_dom, wd_dom, dep_fx, wd_fx = _wallet_fields(dom_row, fx_row)

    return SpreadRow(
        sym=base,
        dom=dom_row.exchange,
        fx=fx_row.exchange,
        fwd=fwd,
        rev=rev,
        usd=usd,
        spark=[],  # 항상 빈 배열 — 005(history) 몫
        status=status,
        age=age,
        liq_dom=liq_dom,
        liq_fx=liq_fx,
        rate_ask=row_rate_ask,
        rate_bid=row_rate_bid,
        net_dom=net_dom,
        dep_dom=dep_dom,
        wd_dom=wd_dom,
        dep_fx=dep_fx,
        wd_fx=wd_fx,
    )


def build_spreads(
    store: LiveStore,
    *,
    now: datetime | None = None,
    excluded: Collection[str] | None = None,
) -> SpreadsResponse:
    """전 (국내 × 해외 × 코인) 페어의 김프/역프 표 — 스펙 003 §3.2."""
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
    rows_out: list[SpreadRow] = []
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
                        base, dom_table[base], fx_table[base], rate.ask, rate.bid, now
                    )
                )

    # 5. 정렬 고정
    rows_out.sort(key=lambda r: (r.sym, r.dom, r.fx))

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
    return SpreadsResponse(
        rate=base_rate.ask,
        rows=rows_out,
        data_received_at=received * 1000 if received is not None else None,
        warnings=warnings,
        fetched_at=int(time.time() * 1000),
    )


def build_refresh(result: CycleResult, store: LiveStore) -> RefreshResponse:
    """001 수집 사이클 결과 → POST /refresh 응답 — 스펙 003 §3.3.

    `snapshots[]` 는 거래소당 1항목이고 001 요약의 호출 수도 여기 싣는다(006 이 원소를 확장).
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
