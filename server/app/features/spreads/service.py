"""스프레드 표 계산 — 순수 계산. 저장소를 인자로 받고 전역을 import 하지 않는다 (스펙 003 §3.2).

이 기능의 고정값(§3.1)도 여기 둔다 — 다른 기능이 쓰게 되는 날 core 로 옮긴다.
"""

import time
from collections.abc import Collection
from datetime import UTC, datetime

from app.core.collect import RefreshSummary
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.networks import wallet_fields
from app.core.orderbook import (
    WalkResult,
    average_price,
    walk_amount,
    walk_levels,
    walk_quantity,
)
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


# 한 표 안에서 "사는 쪽" 걷기 결과를 나누는 메모 — (거래소, 코인) → 그 마켓 asks 를 고정 금액으로 걷은 것
BuyMemo = dict[tuple[str, str], WalkResult]


def _cross_walk(
    buy_levels: list[list[float]],
    sell_levels: list[list[float]],
    buy_amount: float,
    *,
    memo: BuyMemo | None = None,
    memo_key: tuple[str, str] | None = None,
) -> tuple[float, float]:
    """양쪽 다리를 **수량으로 연결해** 건넌 (평균 매수가, 평균 매도가) — 스펙 003 §3.2-4.

    각 다리를 따로 걸으면 사지도 않은 수량을 파는 값이 나온다. 매도측이 소진돼 못 판
    수량이 있으면 판 수량만큼 매수측을 되맞춘다 — 못 판 코인을 0원으로 치면 −50% 대
    쓰레기 값이 나오기 때문이다(004 §3.2·§3.3 과 같은 규칙).

    사는 쪽 걷기는 상대 거래소와 무관하다(같은 마켓·같은 금액) — 한 마켓이 여러 행에 나오므로
    (해외 마켓은 국내 2곳, 국내 마켓은 해외 3곳) `memo` 가 있으면 한 표 안에서 한 번만 걷는다.
    파는 쪽은 산 수량에 달려 있어 행마다 걷는다.
    """
    buy = memo.get(memo_key) if memo is not None else None
    if buy is None:
        buy = walk_amount(buy_levels, buy_amount)
        if memo is not None and memo_key is not None:
            memo[memo_key] = buy
    sell = walk_quantity(sell_levels, buy.quantity)
    if sell.exhausted and sell.quantity < buy.quantity:
        buy = walk_quantity(buy_levels, sell.quantity)
    return average_price(buy), average_price(sell)


def _age_seconds(row: Row, store: LiveStore, now: datetime) -> float:
    """그 거래소 스트림의 마지막 시세 수신 이후 경과 초 (§3.2-4).

    행 자체의 갱신 시각이 아니다 — 조용한 코인은 메시지가 안 와도 호가는 현재값이다.
    행은 스트림 메시지로만 생기므로 행이 있는 거래소의 수신 시각과 행의 갱신 시각은 항상 있다.

    예외: 행 자체가 300초 이상 안 바뀌었으면(거래 정지·심볼 장애 — 스트림은 살아 있는데
    그 코인 프레임만 안 온다) 그 행의 실제 경과 초를 낸다. FE 의 stale 규칙(age ≥ 5)이
    그대로 잡게 하기 위해서다.
    """
    state = store.stream_state(row.exchange)
    assert state is not None and state.last_message_at is not None
    assert row.updated_at is not None
    stream_age = (now.timestamp() * 1000 - state.last_message_at) / 1000
    row_age = (now - row.updated_at).total_seconds()
    if row_age >= ROW_STALE_SEC:
        return max(stream_age, row_age)
    return stream_age


def _build_row(
    base: str,
    dom_row: Row,
    fx_row: Row,
    rate_ask: float,
    rate_bid: float,
    store: LiveStore,
    now: datetime,
    notional: float,
    buy_memo: BuyMemo,
) -> dict[str, object]:
    """행 하나의 규칙 — 스펙 003 §3.2-4.

    응답 키(camelCase)·순서 그대로의 dict 를 만든다 — `SpreadRow` 모델을 거치지 않는다.
    표는 매초 1,400행 넘게 만들어지므로(017) 모델 생성 → model_dump → 키 변환 → json 의
    네 단계가 표 1장 비용의 절반이었다. 키 이름·순서의 진실은 `SpreadRow` 이고, 이 dict 가
    그것과 같은 바이트가 되는지는 테스트가 옛 경로와 비교해 지킨다.
    """
    dom_bid = dom_row.bids[0] if dom_row.bids else None
    dom_ask = dom_row.asks[0] if dom_row.asks else None
    fx_bid = fx_row.bids[0] if fx_row.bids else None
    fx_ask = fx_row.asks[0] if fx_row.asks else None

    # age 는 양측 스트림 중 오래된 쪽 기준(행 자체 300초 미갱신이면 그 행의 경과 초), 0 미만이면 0
    age = max(0.0, _age_seconds(dom_row, store, now), _age_seconds(fx_row, store, now))

    best = (dom_bid, dom_ask, fx_bid, fx_ask)
    failed = any(level is None for level in best) or any(
        # 가격뿐 아니라 잔량도 본다 — 잔량 0 이면 걷어도 체결 수량이 0 이라
        # 평균가가 0 이 되고 순값 계산이 0 으로 나눈다 (§3.2-4)
        level[0] <= 0 or level[1] <= 0
        for level in best
        if level is not None
    )
    if failed:
        # fail 이어도 입출금 값과 age 는 싣는다
        fwd = rev = usd = krw = slip_fwd = slip_rev = 0.0
        status = "fail"
    else:
        assert dom_bid is not None and dom_ask is not None
        assert fx_bid is not None and fx_ask is not None
        # 원값(raw) — 최우선 1단계 기준. 저장 계층(005·009)이 쓰는 값이고 응답에는 안 나간다.
        # 체결되는 쪽 호가: 김프는 해외 ask 에 사서 국내 bid 에 판다, 역프는 반대.
        fwd_raw = premium_percent(buy_krw=fx_ask[0] * rate_ask, sell_krw=dom_bid[0])
        rev_raw = premium_percent(buy_krw=dom_ask[0], sell_krw=fx_bid[0] * rate_bid)

        # 걷기 — 김프는 해외 asks 를 notional(USDT)로, 역프는 국내 asks 를 그 원화 환산액으로
        # 사는 쪽 메모 키 = 마켓 — 해외 asks 는 늘 notional(USDT), 국내 asks 는 그 거래소 환율로
        # 환산한 원화라 같은 마켓이면 금액도 같다
        fx_ask_avg, dom_bid_avg = _cross_walk(
            walk_levels(fx_row, "asks"),
            walk_levels(dom_row, "bids"),
            notional,
            memo=buy_memo,
            memo_key=(fx_row.exchange, base),
        )
        dom_ask_avg, fx_bid_avg = _cross_walk(
            walk_levels(dom_row, "asks"),
            walk_levels(fx_row, "bids"),
            notional * rate_ask,
            memo=buy_memo,
            memo_key=(dom_row.exchange, base),
        )

        # 순값과 차감폭 — 반올림하지 않는다(상한도 없다)
        fwd = premium_percent(buy_krw=fx_ask_avg * rate_ask, sell_krw=dom_bid_avg)
        rev = premium_percent(buy_krw=dom_ask_avg, sell_krw=fx_bid_avg * rate_bid)
        slip_fwd = max(0.0, fwd_raw - fwd)
        slip_rev = max(0.0, rev_raw - rev)

        # 국내 시세 자체라 환율·슬리피지와 무관하다 — FE 가 그대로 표시한다
        krw = dom_bid[0]
        usd = fx_row.price
        status = "stale" if age >= STALE_AFTER_SEC else "ok"

    # 입출금 5필드는 망 판정으로 채운다 — fail 행도 같은 규칙 (006 §3.7)
    # 024 부터 core 공용 함수 — 틱도 같은 판정을 쓴다. 표는 앞 다섯 값만 싣고 `net_fx` 는 싣지 않는다(응답 불변)
    wf = wallet_fields(dom_row, fx_row)

    # float() 는 모델이 하던 int→float 강제와 같다 — 거래소가 정수로 준 가격이 "100" 이 아니라
    # "100.0" 으로 나가야 옛 바이트와 같다
    return {
        "sym": base,
        "dom": dom_row.exchange,
        "fx": fx_row.exchange,
        "fwd": float(fwd),
        "rev": float(rev),
        "usd": float(usd),
        # 009 가 게시한 fwd 추이(1분 버킷 ≤30개) — fail 행도 싣는다, 없으면 빈 배열.
        # 값은 009 가 버퍼에 넣을 때 이미 소수 3자리다(0.001%p = 김프 눈금보다 촘촘하다): 490행 ×
        # 30개를 1초마다 보내므로 배정밀도 그대로면 응답이 gzip 106KB 다. 원값은 Influx 에 남는다.
        "spark": store.spark(dom_row.exchange, fx_row.exchange, base),
        "status": status,
        "age": float(age),
        "slipFwd": float(slip_fwd),
        "slipRev": float(slip_rev),
        "krw": float(krw),
        "netDom": wf.net_dom,
        "depDom": wf.dep_dom,
        "wdDom": wf.wd_dom,
        "depFx": wf.dep_fx,
        "wdFx": wf.wd_fx,
    }


def build_table(
    store: LiveStore,
    *,
    now: datetime | None = None,
    excluded: Collection[str] | None = None,
    notional: float = DEFAULT_NOTIONAL,
) -> dict[str, object]:
    """전 (국내 × 해외 × 코인) 페어의 김프/역프 표 — 스펙 003 §3.2.

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
) -> SpreadsResponse:
    """`build_table` 과 같은 표를 `SpreadsResponse` 모델로 — 테스트·문서용. 뜨거운 경로는 dict 다."""
    return SpreadsResponse.model_validate(
        build_table(store, now=now, excluded=excluded, notional=notional)
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
