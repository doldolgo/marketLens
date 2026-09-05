"""틱 루프(1초)와 판정 — 스펙 001 §3.6·§3.8.

매초 경계에 LiveStore 의 최신 시세로 틱을 만들어 슬롯에 넣고 직전 틱을 009 인계 함수에
넘긴다. 틱 생성은 동기다 — 한 틱 안에서 교체 전후 호가가 섞이지 않는 유일한 근거다.
틱 루프는 예외를 밖으로 던지지 않는다(버그 하나로 멈추지 않게 로그 후 다음 초).
"""

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx

from app.core.config import EXCHANGES
from app.core.contracts import (
    OutageSink,
    StreamJudge,
    TickHandoff,
    WalletStatusProvider,
    noop_handoff,
)
from app.core.live_store import LiveStore
from app.core.models import Row, StreamError, StreamState, Tick, TickRow
from app.core.premium import premium_percent

logger = logging.getLogger("marketlens.ticks")

STALE_AFTER_MS = 30_000  # 연결됐는데 이만큼 시세 무수신이면 stale_stream (§3.8)

# 틱 조합의 페어 분류 — KRW 호가면 국내, USDT 호가면 해외 (003 §3.2)
_DOMESTIC_QUOTE = "KRW"
_FOREIGN_QUOTE = "USDT"


@dataclass(frozen=True)
class StreamVerdict:
    ok: bool
    error: StreamError | None = None


def judge_state(state: StreamState, now_ms: int, url: str) -> StreamVerdict | None:
    """§3.8 — 성공 = 연결 + 마지막 시세 30초 이내. 미연결이면 last_error, 무수신이면 stale_stream.

    첫 연결 시도의 결과가 아직 없으면(미연결·오류 없음·수신 없음) None — 판정 대상이 아니다.
    무수신은 이번 연결의 구독 시각과 마지막 시세 수신 시각 중 최신부터 센다 — 오래 끊겼다가
    재연결한 직후 첫 프레임 전에 직전 연결의 수신 시각으로 정체가 되지 않게.
    """
    if not state.connected:
        if state.last_error is None:
            return None if state.last_message_at is None else _closed(url)
        return StreamVerdict(ok=False, error=state.last_error)
    marks = [m for m in (state.last_message_at, state.connected_since) if m is not None]
    since = max(marks) if marks else None
    if since is not None and now_ms - since >= STALE_AFTER_MS:
        return StreamVerdict(
            ok=False,
            error=StreamError(
                kind="stale_stream",
                message=f"스트림 정체: {STALE_AFTER_MS // 1000}초 이상 시세 무수신",
                status_code=None,
                url=url,
            ),
        )
    return StreamVerdict(ok=True)


def _closed(url: str) -> StreamVerdict:
    # 한 번 시세를 받았던 스트림이 오류 기록 없이 닫힌 상태 — 연결 실패로 본다
    return StreamVerdict(
        ok=False,
        error=StreamError(
            kind="network", message="스트림 연결 끊김", status_code=None, url=url
        ),
    )


def build_tick(store: LiveStore, ts: int, dw_failed: Sequence[str]) -> Tick:
    """전 조합 중 자격 통과분의 김프 원값 — 순수·동기 (§3.6-2, 003 §3.2-4 raw 규칙).

    자격: 국내×해외 다른 거래소, 양쪽 최우선 호가 존재, 그 국내 거래소 자신의 USDT 시세,
    여섯 값 전부 > 0.
    """
    rates = {ex: r for ex, r in store.rates().items() if r.ask > 0 and r.bid > 0}
    domestic: dict[str, dict[str, Row]] = {}
    foreign: dict[str, dict[str, Row]] = {}
    for row in store.get_all():
        if row.quote == _DOMESTIC_QUOTE:
            domestic.setdefault(row.exchange, {})[row.base.upper()] = row
        elif row.quote == _FOREIGN_QUOTE:
            foreign.setdefault(row.exchange, {})[row.base.upper()] = row

    rows: list[TickRow] = []
    for dom_ex, dom_table in sorted(domestic.items()):
        rate = rates.get(dom_ex)
        if rate is None:
            continue  # 남의 시세를 빌리지 않는다 — 시세 없는 국내 거래소는 이 틱에서 빠진다
        for fx_ex, fx_table in sorted(foreign.items()):
            if fx_ex == dom_ex:
                continue
            for base in sorted(dom_table.keys() & fx_table.keys()):
                dom_row, fx_row = dom_table[base], fx_table[base]
                if not (dom_row.bids and dom_row.asks and fx_row.bids and fx_row.asks):
                    continue
                dom_bid, dom_ask = dom_row.bids[0][0], dom_row.asks[0][0]
                fx_bid, fx_ask = fx_row.bids[0][0], fx_row.asks[0][0]
                six = (dom_bid, dom_ask, fx_bid, fx_ask, rate.ask, rate.bid)
                if any(v <= 0 for v in six):
                    continue
                rows.append(
                    TickRow(
                        dom=dom_ex,
                        fx=fx_ex,
                        base=base,
                        fwd=premium_percent(
                            buy_krw=fx_ask * rate.ask, sell_krw=dom_bid
                        ),
                        rev=premium_percent(
                            buy_krw=dom_ask, sell_krw=fx_bid * rate.bid
                        ),
                    )
                )
    return Tick(ts=ts, rows=tuple(rows), dw_failed=tuple(dw_failed))


class TickLoop:
    def __init__(
        self,
        *,
        store: LiveStore,
        streams: Sequence[StreamJudge],
        client: httpx.AsyncClient,
        handoff: TickHandoff = noop_handoff,
        outages: OutageSink | None = None,
        wallet: WalletStatusProvider | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._streams = list(streams)
        self._client = client
        self._handoff = handoff
        self._outages = outages
        self._wallet = wallet
        self._clock = clock
        self._sleep = sleep
        self._task: asyncio.Task[None] | None = None
        self._wallet_task: asyncio.Task[dict[str, int] | None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """매초 경계마다 tick(). 예외는 로그 후 다음 초."""
        while True:
            now = self._clock()
            target = math.floor(now) + 1
            await self._sleep(max(0.0, target - now))
            self._kick_wallet()
            try:
                self.tick(int(target))
            except Exception:
                logger.exception("틱 생성이 예외로 끝났다 — 다음 초에 계속")

    def _kick_wallet(self) -> None:
        # §3.6-1 — 60초에 한 번 실호출. 시세 갱신을 막지 않게 별도 태스크, 겹치지 않게 하나만
        if self._wallet is None:
            return
        if self._wallet_task is not None and not self._wallet_task.done():
            return
        self._wallet_task = asyncio.create_task(
            self._wallet.refresh_if_due(self._client)
        )

    def tick(self, ts: int) -> Tick:
        """§3.6-2~5 — 동기. 틱 생성 → 슬롯·인계 → received_at → 판정."""
        dw_failed: list[str] = []
        if self._wallet is not None:
            # 입출금 캐시를 행에 반영한다 — 메시지로 새로 생긴 행도 1초 안에 3필드를 갖는다
            for ex in EXCHANGES:
                self._wallet.apply(self._store.get_all(exchange=ex), ex)
            dw_failed = self._wallet.failed()
        tick = build_tick(self._store, ts, dw_failed)
        prev = self._store.push_tick(tick)
        if prev is not None:
            self._handoff(prev)
        self._store.mark_received(ts)
        self._judge_all(ts * 1000)
        return tick

    def _judge_all(self, now_ms: int) -> None:
        if self._outages is None:
            return
        for stream in self._streams:
            verdict = stream.judge(now_ms)
            if verdict is None:
                continue
            if verdict.ok:
                self._outages.record_success(stream.id, now_ms)
                continue
            err = verdict.error
            assert err is not None
            self._outages.record_failure(
                stream.id,
                now_ms,
                kind=err.kind,
                message=err.message,
                status_code=err.status_code,
                url=err.url,
                retry_after_sec=err.retry_after_sec,
            )

    async def aclose(self) -> None:
        """루프를 멈추고 슬롯에 남은 마지막 틱을 인계한다 (§3.6)."""
        for task in (self._task, self._wallet_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._task = self._wallet_task = None
        last = self._store.tick
        if last is not None:
            self._handoff(last)
