"""스프레드 표 계산 — 순수 계산. 저장소를 인자로 받고 전역을 import 하지 않는다 (스펙 003 §3.2).

이 기능의 고정값(§3.1)도 여기 둔다 — 다른 기능이 쓰게 되는 날 core 로 옮긴다.
"""

import json
import time
from collections.abc import Collection, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.collect import RefreshSummary
from app.core.live_store import LiveStore, SparkKey
from app.core.models import Row
from app.core.networks import MemoKey, WalletFields, WalletMemo, wallet_fields
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


# 표 JSON 의 인코더 설정 — 017 `encode_table` 과 같다(camelCase·공백 없음·NaN 금지). 글 갈래가 값 하나씩 쓸 때 쓴다
_JSON = json.JSONEncoder(
    ensure_ascii=False, allow_nan=False, separators=(",", ":")
).encode
# 이름 글 기억 상한 — 코인 약 600·거래소·망 이름이라 닿지 않는다. 닿으면 비우고 다시 채운다(값은 같다)
_NAME_TEXTS_MAX = 8192


class TableTexts:
    """표 글 갈래(017 게시기)가 표와 표 사이에 들고 가는 글 — 게시기 인스턴스 하나가 든다 (§3.2-7).

    - 이름 글: 코인·거래소 이름 → JSON 문자열 글(따옴표·이스케이프 포함). 이름은 바뀌지 않으니 매초 다시 쓰지 않는다.
    - 입출금 글: (국내, 해외, 코인) → (판정 객체, 입출금 6필드 글). 망 판정 메모(006 §3.7)는 입력이 그대로면
      **같은 판정 객체**를 돌려주므로 `is` 가 같으면 글도 같다 — 6필드는 60초에 한 번 바뀌는데 1,840행을 매초
      다시 쓰지 않으려는 것이다. 표 1장의 행을 끝까지 만들었을 때만 이번 표의 dict 로 갈아 끼운다 — 표에 안 나온
      조합은 저절로 빠지고(망 판정 메모의 회차 규칙과 같다), 예외로 멈춘 표는 직전 것을 그대로 둔다.
    """

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.wallet: dict[MemoKey, tuple[WalletFields, str]] = {}

    def name(self, s: str) -> str:
        text = self.names.get(s)
        if text is None:
            if len(self.names) >= _NAME_TEXTS_MAX:
                self.names.clear()
            text = self.names[s] = _JSON(s)
        return text


def _wallet_text(wf: WalletFields) -> str:
    """입출금 6필드의 글 — 키 순서·값 표기가 행 dict 를 통째로 인코딩한 것과 같다(끝에 쉼표). 판정이 바뀐 조합에만 부른다."""
    return (
        _JSON(
            {
                "netDom": wf.net_dom,
                "depDom": wf.dep_dom,
                "wdDom": wf.wd_dom,
                "depFx": wf.dep_fx,
                "wdFx": wf.wd_fx,
                "netFx": wf.net_fx,
            }
        )[1:-1]
        + ","
    )


def _float_text(v: object) -> str:
    """국내 시장 단위 값(dayChg)의 글 — 행 dict 를 인코딩할 때와 같은 글. 늘 float 라 repr 이고, 아니면 인코더로."""
    if type(v) is float:
        return repr(v)
    return _JSON(v)


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
    wallet_memo: WalletMemo | None = None,
    memo_round: int | None = None,
    spark_json: Mapping[SparkKey, str] | None = None,
    texts: TableTexts | None = None,
) -> dict[str, object]:
    """전 (국내 × 해외 × 코인) 페어의 김프/역프 표 — 스펙 003 §3.2.

    `day_open` 은 026 의 기준가 장부((국내 거래소, 코인) → KST 00시 가격) — 없으면 `dayChg` 는 전부 null.
    `wallet_memo` 는 같은 회차의 틱이 채운 망 판정 메모(006 §3.7) — 없으면 행마다 판정한다. `memo_round` 가
    메모의 지금 회차와 같으면 틱이 채운 칸을 입력 비교 없이 읽는다(`WalletMemo.current_getter` — 017 게시기가
    그 회차를 연 틱 바로 뒤에서만 넘긴다). 틱이 안 채운 조합과 회차를 모르는 호출은 입력을 비교해 판정한다.

    응답 모양(camelCase 키·순서) 그대로의 dict 를 돌려준다. 모델이 필요하면 `build_spreads` (테스트·문서용,
    같은 계산). 키 이름·순서의 진실은 `SpreadRow` 이고 이 dict 가 그것과 같은 바이트가 되는지는 테스트가 지킨다.

    글 갈래(`spark_json` 을 주면 — 017 게시기 전용, 2026-10-09): `rows` 에 행 dict 대신 **행 JSON 글**을 담는다.
    행 계산은 한 벌이고 행을 내보내는 자리만 갈린다. 글은 행 dict 를 `encode_table` 로 인코딩한 것과 같은
    바이트다 — 키는 글자 상수, 부동소수는 `repr(float(x))`(json 의 float 표기와 같다), 이름은 `TableTexts` 가
    기억한 JSON 문자열, spark 자리는 009 가 게시한 조각(`spark_json` — 이 동기 구간에서 읽은 것), 조각이 없으면
    목록을 인코딩한다. 국내가·dayChg 는 국내 시장 재료를, 해외가는 해외 시장 재료를 만들 때 한 번 글로 쓰고, age 는
    표 1장 안에서 같은 값의 글을 다시 쓴다. 입출금 6필드 글은 `texts` 가 판정 객체와 함께 든다. 행 1,840개마다
    키 19개짜리 dict 를 만들고 C 인코더가 다시 훑던 일을 덜려는 것이다. 유한하지 않은 값이 든 행을 만나면
    `ValueError` 를 낸다 — 게시기는 그때 dict 갈래로 다시 만들어 기준과 같은 결과(같은 예외)를 낸다.

    표 계산 경로(§3.2 끝): 거래소 단위 값(스트림 age)과 시장 단위 값(최우선 검사·age·dayChg·사는 쪽
    걷기)은 처음 쓰일 때 한 번만 구하고, 행 루프는 원값·파는 쪽 걷기·되맞추기·평균가·순값만 한다.
    연산과 그 순서는 행 하나의 규칙(§3.2-4) 그대로라 결과 바이트가 같다.

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

    # 3. 국내 거래소마다 그 거래소 자신의 환율 — 환율 없는 국내 거래소는 행 전체가 빠진다
    #    (남의 환율을 빌리면 테더 프리미엄이 섞인다). 역프의 사는 금액(notional × rate_ask)도 여기서 한 번.
    doms: list[tuple[str, dict[str, Row], float, float, float]] = []
    for dom_ex in sorted(domestic):
        rate = store.get_rate(dom_ex)
        if rate is None or rate.ask <= 0 or rate.bid <= 0:
            continue
        doms.append((dom_ex, domestic[dom_ex], rate.ask, rate.bid, notional * rate.ask))
    fxs = sorted(foreign.items())
    bases: set[str] = set()
    for _, dom_table, _, _, _ in doms:
        bases.update(dom_table)

    # 4~5. 코인 → 국내 거래소 → 해외 거래소 순으로 돌며 만든다 — 만든 순서가 곧 (sym, dom, fx)
    #      오름차순이라 따로 정렬하지 않는다. 거래소·시장 단위 값은 처음 쓰일 때 한 번만 구한다.
    now_ms = now.timestamp() * 1000
    cutoff = now - _ROW_STALE
    opens = day_open if day_open is not None else {}
    stream_ages: dict[str, float] = {}
    rows_out: list[Any] = []
    append = rows_out.append
    # 006 §3.7 — 이번 회차에 틱이 채운 칸을 읽는 함수는 표 1장에 한 번 꺼낸다(행마다 메서드를 부르지 않게)
    cur_get = (
        wallet_memo.current_getter(memo_round) if wallet_memo is not None else None
    )
    text = spark_json is not None
    if spark_json is not None:
        frags = spark_json
        memo_texts = texts if texts is not None else TableTexts()
        name = memo_texts.name
        wt_prev = memo_texts.wallet
        wt_cur: dict[MemoKey, tuple[WalletFields, str]] = {}
        age_texts: dict[
            float, str
        ] = {}  # 표 1장 안 — age 는 스트림 쌍 단위 값이라 고유값이 몇 개뿐이다
        ex_texts = {ex: name(ex) for ex in (*domestic, *foreign)}
    for base in sorted(bases):
        if base in excluded_upper:
            continue
        fx_markets: dict[
            str, list[Any]
        ] = {}  # 이 코인의 해외 시장 — 국내 거래소들이 나눠 쓴다
        if text:
            sym_text = name(base)
        for dom_ex, dom_table, rate_ask, rate_bid, notional_krw in doms:
            dom_row = dom_table.get(base)
            if dom_row is None:
                continue
            dm: list[Any] | None = None
            day_chg: float | None = None
            for fx_ex, fx_table in fxs:
                if fx_ex == dom_ex:
                    continue
                fx_row = fx_table.get(base)
                if fx_row is None:
                    continue
                if dm is None:
                    dm = _market(dom_row, store, stream_ages, now_ms, now, cutoff)
                    # 026 §3.2 — KST 00시 기준가 대비 국내 체결가. fail 행도 계산(호가와 무관). 기준가 없으면 null 이지 0 이 아니다
                    ref = opens.get((dom_ex, base))
                    if ref is not None and ref > 0 and dom_row.price > 0:
                        day_chg = (dom_row.price / ref - 1) * 100
                    if text:
                        # 글 갈래 — 국내 시장 단위 값의 글을 시장마다 한 번: [7] 국내가(ok 행의 krw = 최우선 bid), [8] dayChg
                        dm.append(repr(float(dm[1][0])) if dm[3] else "0.0")
                        dm.append("null" if day_chg is None else _float_text(day_chg))
                fm = fx_markets.get(fx_ex)
                if fm is None:
                    fm = fx_markets[fx_ex] = _market(
                        fx_row, store, stream_ages, now_ms, now, cutoff
                    )
                    if text:
                        fm.append(
                            repr(float(fx_row.price))
                        )  # [7] 해외가(ok 행의 usd) 글

                # age 는 양측 스트림 중 오래된 쪽 — max(0.0, 국내, 해외) 와 같은 값(앞에서부터 더 큰 것만 바꾼다)
                age = 0.0
                if dm[0] > age:
                    age = dm[0]
                if fm[0] > age:
                    age = fm[0]

                if dm[3] and fm[3]:
                    dom_bid = dm[1][0]
                    dom_ask = dm[2][0]
                    fx_bid = fm[1][0]
                    fx_ask = fm[2][0]
                    # 원값(raw) — 최우선 1단계 기준. 저장 계층(005·009)이 쓰는 값이고 응답에는 안 나간다.
                    # 체결되는 쪽 호가: 김프는 해외 ask 에 사서 국내 bid 에 판다, 역프는 반대.
                    fwd_raw = premium_percent(
                        buy_krw=fx_ask * rate_ask, sell_krw=dom_bid
                    )
                    rev_raw = premium_percent(
                        buy_krw=dom_ask, sell_krw=fx_bid * rate_bid
                    )

                    # 김프 — 해외 asks 를 notional(USDT)로 산 걷기(해외 시장당 한 번) → 그 수량을 국내 bids 에 판다
                    buy = fm[4]
                    if buy is None:
                        buy = fm[4] = _walk_amount(fm[5], notional)
                    bought = buy[0]
                    left = bought
                    sold = earned = 0.0
                    if bought <= 0:
                        left = 0.0  # 걷기의 "수량 ≤ 0 이면 체결 0·소진 아님" 과 같다
                    else:
                        for level in dm[6]:
                            price = level[0]
                            size = level[1]
                            if size >= left:
                                sold += left
                                earned += left * price
                                left = 0.0
                                break
                            sold += size
                            earned += size * price
                            left -= size
                    if left > WALK_EPSILON and sold < bought:
                        # 국내 bids 가 소진돼 못 판 수량 — 판 수량만큼 해외 매수를 되맞춘다(§3.2-4)
                        quantity, amount = _walk_quantity(fm[5], sold)
                        fx_ask_avg = amount / quantity if quantity > 0 else 0.0
                    else:
                        fx_ask_avg = buy[1] / bought if bought > 0 else 0.0
                    dom_bid_avg = earned / sold if sold > 0 else 0.0

                    # 역프 — 국내 asks 를 notional × rate_ask(원)로 산 걷기(국내 시장당 한 번) → 그 수량을 해외 bids 에 판다
                    buy = dm[4]
                    if buy is None:
                        buy = dm[4] = _walk_amount(dm[5], notional_krw)
                    bought = buy[0]
                    left = bought
                    sold = earned = 0.0
                    if bought <= 0:
                        left = 0.0
                    else:
                        for level in fm[6]:
                            price = level[0]
                            size = level[1]
                            if size >= left:
                                sold += left
                                earned += left * price
                                left = 0.0
                                break
                            sold += size
                            earned += size * price
                            left -= size
                    if left > WALK_EPSILON and sold < bought:
                        quantity, amount = _walk_quantity(dm[5], sold)
                        dom_ask_avg = amount / quantity if quantity > 0 else 0.0
                    else:
                        dom_ask_avg = buy[1] / bought if bought > 0 else 0.0
                    fx_bid_avg = earned / sold if sold > 0 else 0.0

                    # 순값과 차감폭 — 반올림하지 않는다(상한도 없다). 차감폭은 max(0, 원값 − 순값) 과 같은 값
                    fwd = premium_percent(
                        buy_krw=fx_ask_avg * rate_ask, sell_krw=dom_bid_avg
                    )
                    rev = premium_percent(
                        buy_krw=dom_ask_avg, sell_krw=fx_bid_avg * rate_bid
                    )
                    slip = fwd_raw - fwd
                    slip_fwd = slip if slip > 0.0 else 0.0
                    slip = rev_raw - rev
                    slip_rev = slip if slip > 0.0 else 0.0

                    # 국내 시세 자체라 환율·슬리피지와 무관하다 — FE 가 그대로 표시한다
                    krw = dom_bid
                    usd = fx_row.price
                    status = "stale" if age >= STALE_AFTER_SEC else "ok"
                else:
                    # fail 이어도 입출금 값과 age 는 싣는다
                    fwd = rev = usd = krw = slip_fwd = slip_rev = 0.0
                    status = "fail"

                # 입출금 6필드는 망 판정으로 채운다 — fail 행도 같은 규칙 (006 §3.7)
                # 024 부터 core 공용 함수 — 틱도 같은 판정을 쓴다. `net_fx` 는 FE 의 "네트워크 같음/다름" 판단 재료다
                key = (dom_ex, fx_ex, base)
                if cur_get is None:
                    wf = wallet_fields(dom_row, fx_row)
                else:
                    # 이번 회차에 틱이 채운 칸이면 그대로, 없으면(틱 자격이 없는 fail 행·회차를 모르는 호출) 입력 비교
                    entry = cur_get(key)
                    wf = (
                        entry[6]
                        if entry is not None
                        else wallet_memo.fields(key, dom_row, fx_row)  # type: ignore[union-attr]
                    )

                if not text:
                    # float() 는 모델이 하던 int→float 강제와 같다 — 거래소가 정수로 준 가격이 "100" 이 아니라
                    # "100.0" 으로 나가야 옛 바이트와 같다
                    append(
                        {
                            "sym": base,
                            "dom": dom_ex,
                            "fx": fx_ex,
                            "fwd": float(fwd),
                            "rev": float(rev),
                            "usd": float(usd),
                            # 009 가 게시한 fwd 추이(1분 버킷 ≤30개) — fail 행도 싣는다, 없으면 빈 배열.
                            # 값은 009 가 버퍼에 넣을 때 이미 소수 3자리다(0.001%p = 김프 눈금보다 촘촘하다): 490행 ×
                            # 30개를 1초마다 보내므로 배정밀도 그대로면 응답이 gzip 106KB 다. 원값은 Influx 에 남는다.
                            "spark": store.spark(dom_ex, fx_ex, base),
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
                            "netFx": wf.net_fx,
                            "dayChg": day_chg,
                        }
                    )
                    continue

                # --- 글 갈래 — 위 dict 를 `encode_table` 로 인코딩한 것과 같은 글 ---
                # float() 강제는 dict 갈래와 같다. 유한성은 행 부동소수의 합 하나로 본다 — 하나라도 NaN·±inf 면 합이
                # NaN·±inf 라 `chk - chk` 가 0 이 아니다(유한한 값끼리 합이 넘쳐도 걸린다 — 그때는 게시기가 dict
                # 갈래로 다시 만들어 기준 바이트를 낸다). dict 갈래라면 인코더가 거부했을 행이다
                fwd = float(fwd)
                rev = float(rev)
                usd = float(usd)
                age = float(age)
                slip_fwd = float(slip_fwd)
                slip_rev = float(slip_rev)
                krw = float(krw)
                chk = fwd + rev + usd + age + slip_fwd + slip_rev + krw
                if day_chg is not None:
                    chk += day_chg
                if chk - chk != 0.0:
                    raise ValueError(
                        f"표 행에 유한하지 않은 값 — {base} {dom_ex}/{fx_ex}"
                    )
                frag = frags.get(key)
                if frag is None:
                    # 조각이 없는 조합(유한하지 않은 값이 든 추이·조각 없이 게시된 맵) — 목록을 인코딩한다(NaN 거부도 같다)
                    frag = _JSON(store.spark(dom_ex, fx_ex, base))
                # age 는 0.0 에서 큰 값으로만 바뀌므로(위의 비교) −0.0·NaN 이 키로 오지 않는다 — 같은 키 = 같은 글
                age_text = age_texts.get(age)
                if age_text is None:
                    age_text = age_texts[age] = repr(age)
                wt = wt_prev.get(key)
                if wt is None or wt[0] is not wf:
                    wt = (wf, _wallet_text(wf))
                wt_cur[key] = wt
                if status == "fail":
                    usd_text = krw_text = "0.0"
                else:
                    usd_text = fm[7]
                    krw_text = dm[7]
                append(
                    f'{{"sym":{sym_text},"dom":{ex_texts[dom_ex]},"fx":{ex_texts[fx_ex]},'
                    f'"fwd":{fwd!r},"rev":{rev!r},"usd":{usd_text},"spark":{frag},'
                    f'"status":"{status}","age":{age_text},"slipFwd":{slip_fwd!r},'
                    f'"slipRev":{slip_rev!r},"krw":{krw_text},{wt[1]}"dayChg":{dm[8]}}}'
                )

    if text:
        # 행을 끝까지 만들었다 — 이번 표의 입출금 글로 갈아 끼운다(표에 안 나온 조합은 빠진다)
        memo_texts.wallet = wt_cur

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
    """`build_table` 과 같은 표를 `SpreadsResponse` 모델로 — 테스트·문서용. 뜨거운 경로(017 게시기)는 글 갈래다."""
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
