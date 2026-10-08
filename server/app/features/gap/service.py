"""현선갭 표 계산 — 틱마다 (해외 현물 거래소 × perp 원천 × 양쪽에 행이 있는 base) 1장 (스펙 048 §3.2).

갭은 체결 가능한 최우선 호가로 잰다(2026-10-08 사람 결정) — 현물은 ask 에 사고 perp 는 bid 에 숏하는 값이라
체결가·mark 기준보다 보수적이고, 양수면 그 순간 실제로 벌어져 있는 것이다. 슬리피지 차감은 없다(최우선 1단계만).
틱 루프 안에서 동기로 끝난다 — `await` 가 없어 한 표 안에서 교체 전후 호가가 섞이지 않는다.

표 계산 경로 규칙(003 §3.2-7 과 같다): 스트림 age 는 거래소·원천마다 한 번, 시장(거래소, 코인) 재료(age·최우선
검사)는 처음 쓰일 때 한 번 만들고, 행은 코인 → 현물 거래소 순서 → perp 원천 순서로 만들어 정렬하지 않는다.
행은 dict 다 — `json.dumps` 가 NamedTuple 을 객체가 아니라 배열로 내보내서, 스펙의 "불변 튜플형 레코드" 대신
003 의 뜨거운 경로와 같은 모양(키 순서 고정 dict)을 쓴다.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import DOMESTIC_EXCHANGES, EXCHANGES, PERP_SOURCES
from app.core.live_store import LiveStore
from app.core.models import PerpRow, Row
from app.core.premium import premium_percent

# 해외 현물 거래소 = 001 §3.2 의 해외 원천 전부, 그 순서 (048 §3.1). 국내 거래소는 현선갭에 끼지 않는다
SPOT_EXCHANGES = tuple(ex for ex in EXCHANGES if ex not in DOMESTIC_EXCHANGES)
# 003 §3.2 와 같은 규칙 — age 5초면 stale, 행 자체가 300초 안 바뀌면 그 행의 실제 경과 초
STALE_AGE_SEC = 5.0
ROW_STALE_SEC = 300.0
_ROW_STALE = timedelta(seconds=ROW_STALE_SEC)


def _stream_age(
    source: str, store: LiveStore, stream_ages: dict[str, float], now_ms: float
) -> float:
    """거래소·원천 스트림의 마지막 시세 수신 이후 경과 초 — 표 한 장에서 원천마다 한 번만 구한다."""
    age = stream_ages.get(source)
    if age is None:
        state = store.stream_state(source)
        # 행은 스트림 메시지(·폴링 성공 회차)로만 생기므로 행이 있는 원천의 수신 시각은 항상 있다
        assert state is not None and state.last_message_at is not None
        age = stream_ages[source] = (now_ms - state.last_message_at) / 1000
    return age


def _spot_market(
    row: Row,
    store: LiveStore,
    stream_ages: dict[str, float],
    now_ms: float,
    now: datetime,
    cutoff: datetime,
) -> list[Any]:
    """현물 시장(거래소, 코인) 하나의 재료 — [age, bid 가격, ask 가격, 최우선 4값이 전부 양수인가, 체결가]."""
    age = _stream_age(row.exchange, store, stream_ages, now_ms)
    updated = row.updated_at
    assert updated is not None
    if updated <= cutoff:
        # 거래 정지·심볼 장애 — 스트림은 살아 있는데 이 코인 프레임만 안 온다 (003 §3.2-4 와 같다)
        age = max(age, (now - updated).total_seconds())
    bid = row.bids[0] if row.bids else None
    ask = row.asks[0] if row.asks else None
    # 잔량도 본다 — 잔량 0 인 호가는 체결할 수 없으니 갭이 아니다
    ok = not (
        bid is None
        or ask is None
        or bid[0] <= 0
        or bid[1] <= 0
        or ask[0] <= 0
        or ask[1] <= 0
    )
    if ok:
        return [age, bid[0], ask[0], True, row.price]
    return [age, 0.0, 0.0, False, row.price]


def _perp_market(
    row: PerpRow,
    store: LiveStore,
    stream_ages: dict[str, float],
    now_ms: float,
    now: datetime,
    cutoff: datetime,
) -> list[Any]:
    """perp 시장(원천, 코인) 하나의 재료 — [age, bid, ask, 호가 4값 양수인가, funding %, intervalH, nextFundingTs]."""
    age = _stream_age(row.source, store, stream_ages, now_ms)
    updated = row.updated_at
    assert updated is not None
    if updated <= cutoff:
        age = max(age, (now - updated).total_seconds())
    ok = row.bid > 0 and row.bid_size > 0 and row.ask > 0 and row.ask_size > 0
    # 펀딩 세 값은 fail 행에도 그대로 싣는다 — 호가와 무관한 값이다 (§3.2)
    if row.funding_rate is None:
        funding = None
    else:
        funding = row.funding_rate * 100  # 한 주기의 비율 → 퍼센트
    if row.next_funding_ms is None:
        next_ts = None
    else:
        next_ts = (
            row.next_funding_ms // 1000
        )  # product.md 시각 규칙 — `*Ts` 는 epoch 초
    return [age, row.bid, row.ask, ok, funding, row.funding_interval_h, next_ts]


def build_gap_table(
    store: LiveStore, *, now: datetime | None = None
) -> dict[str, object]:
    """현선갭 표 1장 — `{"rows", "warnings", "dataReceivedAt", "fetchedAt"}`, 행 11키 (§3.2).

    조합 = 해외 현물 거래소 × perp 원천 × 양쪽에 행이 있는 base. 같은 회사의 현물·perp(`binance` × `binance_perp`)도
    한 행이다 — 한 거래소 안의 베이시스 거래가 가장 흔하다. 행 정렬은 (sym, spot, perp) — spot 은 해외 현물 거래소 순서,
    perp 는 046 원천 순서(사전순이 아니다). `warnings` 는 지금 항상 빈 목록이다(017 과 모양을 맞추기 위해 둔다).
    """
    if now is None:
        now = datetime.now(UTC)
    now_ms = now.timestamp() * 1000
    cutoff = now - _ROW_STALE

    spot_tables: list[tuple[str, dict[str, Row]]] = []
    for ex in SPOT_EXCHANGES:
        rows = store.get_all(exchange=ex)
        if rows:
            spot_tables.append((ex, {r.base.upper(): r for r in rows}))
    perp_tables: list[tuple[str, dict[str, PerpRow]]] = []
    for source in PERP_SOURCES:
        prows = store.perp_rows(source=source)
        if prows:
            perp_tables.append((source, {r.base.upper(): r for r in prows}))
    # 현선갭의 코인 범위 = 김프 우주(현물 행이 있는 코인) ∩ perp 우주 — 한쪽만 있는 코인은 행이 없다 (§3.7)
    spot_bases: set[str] = set()
    for _, table in spot_tables:
        spot_bases.update(table)
    perp_bases: set[str] = set()
    for _, table in perp_tables:
        perp_bases.update(table)

    stream_ages: dict[str, float] = {}
    out: list[dict[str, object]] = []
    # 한 코인의 perp 재료 — 현물 거래소마다 다시 만들지 않는다. 코인마다 비우고 다시 채운다
    perp_markets: list[tuple[str, list[Any]]] = []
    for base in sorted(spot_bases & perp_bases):
        perp_markets.clear()
        for source, table in perp_tables:
            prow = table.get(base)
            if prow is None:
                continue
            perp_markets.append(
                (source, _perp_market(prow, store, stream_ages, now_ms, now, cutoff))
            )
        for spot, table in spot_tables:
            srow = table.get(base)
            if srow is None:
                continue
            sm = _spot_market(srow, store, stream_ages, now_ms, now, cutoff)
            for perp, pm in perp_markets:
                # age 는 양측 스트림 중 오래된 쪽 — max(0.0, 현물, perp) 와 같은 값
                age = 0.0
                if sm[0] > age:
                    age = sm[0]
                if pm[0] > age:
                    age = pm[0]
                if sm[3] and pm[3]:
                    # 진입: 현물 ask 에 사고 perp bid 에 숏 — 클수록 좋다. 정리: 현물 bid 에 팔고 perp ask 에 숏 청산 — 작을수록 좋다.
                    # 식은 김프와 같은 (sell / buy − 1) × 100 이라 core 함수를 그대로 쓴다(인자 이름의 krw 는 단위가 아니라 자리)
                    entry = premium_percent(buy_krw=sm[2], sell_krw=pm[1])
                    exit_ = premium_percent(buy_krw=sm[1], sell_krw=pm[2])
                    spot_price = sm[4]
                    if age >= STALE_AGE_SEC:
                        status = "stale"
                    else:
                        status = "ok"
                else:
                    # 호가 4개 중 하나라도 없거나 0 이하 — 숫자 셋은 0, 펀딩은 그대로 (§3.2)
                    entry = 0.0
                    exit_ = 0.0
                    spot_price = 0.0
                    status = "fail"
                out.append(
                    {
                        "sym": base,
                        "spot": spot,
                        "perp": perp,
                        "spotPrice": spot_price,
                        "entry": entry,
                        "exit": exit_,
                        "funding": pm[4],
                        "intervalH": pm[5],
                        "nextFundingTs": pm[6],
                        "status": status,
                        "age": age,
                    }
                )
    return {
        "rows": out,
        "warnings": [],
        "dataReceivedAt": store.received_at,
        "fetchedAt": int(time.time() * 1000),
    }
