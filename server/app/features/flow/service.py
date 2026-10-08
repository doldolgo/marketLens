"""순유입·최근 전송 조립 — Influx `chain_flow` 조회 결과 + 감지기 상태 + 업비트 KRW 현재가 (스펙 050 §3.6·§3.7).

현재가는 수집 프로세스의 메모리 저장소(LiveStore)를 **읽기만** 한다(§3.1) — 없는 심볼은 null.
"""

from collections.abc import Callable
from typing import Protocol

from app.core.eth_flow import FlowStatus
from app.core.influx import ChainFlowAgg, ChainFlowRow
from app.core.live_store import LiveStore
from app.features.flow.models import (
    FeedOut,
    NetflowResponse,
    NetflowRowOut,
    RecentResponse,
    RecentRowOut,
)

# `window` 값 → 초 (§3.6)
WINDOWS = {"1h": 3_600, "6h": 21_600, "24h": 86_400}
RECENT_WINDOW_SEC = 86_400  # `/flow/recent` 는 최근 24시간 안에서 (§3.7)
CONFIRM_DEPTH = 2  # 확정 = head − block ≥ 2 (§3.3)

PriceOf = Callable[[str], float | None]


class FlowReader(Protocol):
    """core.influx.InfluxClient 의 chain_flow 조회 둘 — 테스트는 같은 시그니처의 fake 를 쓴다."""

    def query_chain_flow_netflow(
        self, *, start: int, stop: int
    ) -> list[ChainFlowAgg]: ...

    def query_chain_flow_recent(
        self,
        *,
        start: int,
        stop: int,
        limit: int,
        dir: str | None = None,
        symbol: str | None = None,
    ) -> list[ChainFlowRow]: ...


def upbit_krw_price(store: LiveStore) -> PriceOf:
    """업비트 KRW 마켓의 최근 체결가(001 행의 `price`) — 행이 없으면 None."""

    def price(symbol: str) -> float | None:
        row = store.get("upbit", symbol)
        if row is None or row.quote != "KRW":
            return None
        return row.price

    return price


def feed_out(status: FlowStatus | None, now: int) -> FeedOut:
    if status is None:
        return FeedOut(
            connected=False,
            last_block=None,
            lag_sec=None,
            deposit_addrs=0,
            hot_wallets=0,
            contracts=0,
        )
    if status.last_block_ts is None:
        lag_sec = None
    else:
        lag_sec = max(0, now - status.last_block_ts)
    return FeedOut(
        connected=status.connected,
        last_block=status.last_block,
        lag_sec=lag_sec,
        deposit_addrs=status.deposit_addrs,
        hot_wallets=status.hot_wallets,
        contracts=status.contracts,
    )


def _krw(amount: float, price: float | None) -> float | None:
    if price is None:
        return None
    return amount * price


def _sort_key(row: NetflowRowOut) -> tuple[int, float, float]:
    """`|netKrw|` 내림차순, null 은 뒤에서 `|netAmount|` 내림차순 (§3.6)."""
    if row.net_krw is None:
        return (1, 0.0, -abs(row.net_amount))
    return (0, -abs(row.net_krw), -abs(row.net_amount))


def build_netflow(
    reader: FlowReader,
    status: FlowStatus | None,
    price_of: PriceOf,
    *,
    window: str,
    now: int,
) -> NetflowResponse:
    aggs = reader.query_chain_flow_netflow(start=now - WINDOWS[window], stop=now + 1)
    by_symbol: dict[str, dict[str, ChainFlowAgg]] = {}
    for agg in aggs:
        by_symbol.setdefault(agg.symbol, {})[agg.dir] = agg
    rows: list[NetflowRowOut] = []
    for symbol, dirs in by_symbol.items():
        inflow = dirs.get("in")
        outflow = dirs.get("out")
        in_count, in_amount, in_ts = 0, 0.0, 0
        if inflow is not None:
            in_count, in_amount, in_ts = inflow.count, inflow.amount, inflow.last_ts
        out_count, out_amount, out_ts = 0, 0.0, 0
        if outflow is not None:
            out_count, out_amount, out_ts = (
                outflow.count,
                outflow.amount,
                outflow.last_ts,
            )
        net_amount = in_amount - out_amount
        rows.append(
            NetflowRowOut(
                symbol=symbol,
                in_count=in_count,
                in_amount=in_amount,
                out_count=out_count,
                out_amount=out_amount,
                net_amount=net_amount,
                net_krw=_krw(net_amount, price_of(symbol)),
                last_ts=max(in_ts, out_ts),
            )
        )
    rows.sort(key=_sort_key)
    return NetflowResponse(
        window=window, as_of=now, feed=feed_out(status, now), rows=rows
    )


def build_recent(
    reader: FlowReader,
    status: FlowStatus | None,
    price_of: PriceOf,
    *,
    limit: int,
    dir: str,
    symbol: str | None,
    now: int,
) -> RecentResponse:
    if dir == "all":
        dir_filter = None
    else:
        dir_filter = dir
    found = reader.query_chain_flow_recent(
        start=now - RECENT_WINDOW_SEC,
        stop=now + 1,
        limit=limit,
        dir=dir_filter,
        symbol=symbol,
    )
    head: int | None = None
    if status is not None:
        if status.head is not None:
            head = status.head
        else:
            head = status.last_block
    rows: list[RecentRowOut] = []
    for r in found:
        rows.append(
            RecentRowOut(
                ts=r.ts,
                block=r.block,
                confirmed=head is not None and head - r.block >= CONFIRM_DEPTH,
                dir=r.dir,
                symbol=r.symbol,
                amount=r.amount,
                krw=_krw(r.amount, price_of(r.symbol)),
                addr=r.addr,
                counterparty=r.counterparty,
                tx_hash=r.tx_hash,
            )
        )
    return RecentResponse(as_of=now, head=head, rows=rows)
