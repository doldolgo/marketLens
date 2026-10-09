"""틱 루프(1초)와 판정 — 스펙 001 §3.6·§3.8.

매초 경계에 LiveStore 의 최신 시세로 틱을 만들어 슬롯에 넣고 직전 틱을 009 인계 함수에
넘긴다. 틱 생성은 동기다 — 한 틱 안에서 교체 전후 호가가 섞이지 않는 유일한 근거다.
틱 루프는 예외를 밖으로 던지지 않는다(버그 하나로 멈추지 않게 로그 후 다음 초).
단계별 소요와 깨어남 지연은 60초마다 한 줄로 남긴다(§3.6 로그) — 동작은 바꾸지 않는다.
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
    EventSink,
    OutageSink,
    StreamJudge,
    TickHandoff,
    WalletStatusProvider,
    noop_handoff,
)
from app.core.live_store import LiveStore
from app.core.models import Row, StreamError, StreamState, Tick, TickRow
from app.core.networks import WalletMemo, wallet_fields
from app.core.premium import premium_percent

logger = logging.getLogger("marketlens.ticks")

STALE_AFTER_MS = 30_000  # 연결됐는데 이만큼 시세 무수신이면 stale_stream (§3.8)
STAGE_REPORT_SEC = 60  # 단계별 소요 요약 한 줄의 주기(초) — §3.6 로그

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


def build_tick(
    store: LiveStore,
    ts: int,
    dw_failed: Sequence[str],
    memo: WalletMemo | None = None,
) -> Tick:
    """전 조합 중 자격 통과분의 김프 원값 — 순수·동기 (§3.6-2, 003 §3.2-4 raw 규칙).

    자격: 국내×해외 다른 거래소, 양쪽 최우선 호가 존재, 그 국내 거래소 자신의 USDT 시세,
    여섯 값 전부 > 0. 조합을 한 번만 훑는다 — 환율 두 값은 시세를 고를 때 이미 > 0 을 거르므로
    조합마다 호가 넷만 비교하고, 환율 중간값은 국내 거래소마다 한 번 구한다. `memo` 가 있으면
    이 틱이 망 판정 메모의 새 회차를 열고 채운다 — 같은 회차의 표(017)가 그 메모를 읽는다(006 §3.7).
    """
    rates = {ex: r for ex, r in store.rates().items() if r.ask > 0 and r.bid > 0}
    domestic: dict[str, dict[str, Row]] = {}
    foreign: dict[str, dict[str, Row]] = {}
    for row in store.get_all():
        if row.quote == _DOMESTIC_QUOTE:
            domestic.setdefault(row.exchange, {})[row.base.upper()] = row
        elif row.quote == _FOREIGN_QUOTE:
            foreign.setdefault(row.exchange, {})[row.base.upper()] = row

    if memo is not None:
        memo.rotate()
    rows: list[TickRow] = []
    append = rows.append
    for dom_ex, dom_table in sorted(domestic.items()):
        rate = rates.get(dom_ex)
        if rate is None:
            continue  # 남의 시세를 빌리지 않는다 — 시세 없는 국내 거래소는 이 틱에서 빠진다
        rate_ask = rate.ask
        rate_bid = rate.bid
        mid = (rate_ask + rate_bid) / 2  # 014 §3.2 의 `rate` — 국내 거래소마다 한 번
        for fx_ex, fx_table in sorted(foreign.items()):
            if fx_ex == dom_ex:
                continue
            for base in sorted(dom_table.keys() & fx_table.keys()):
                dom_row = dom_table[base]
                fx_row = fx_table[base]
                dom_bids = dom_row.bids
                dom_asks = dom_row.asks
                fx_bids = fx_row.bids
                fx_asks = fx_row.asks
                if not (dom_bids and dom_asks and fx_bids and fx_asks):
                    continue
                dom_bid = dom_bids[0][0]
                dom_ask = dom_asks[0][0]
                fx_bid = fx_bids[0][0]
                fx_ask = fx_asks[0][0]
                if dom_bid <= 0 or dom_ask <= 0 or fx_bid <= 0 or fx_ask <= 0:
                    continue
                # 024 §3.3 — 입출금은 006 §3.7 판정값(spreads 행과 같은 함수). 코인 단위 값을 그대로 실으면
                # 표에서 "출금 불가" 인 코인이 봉·사건에서는 열림으로 남는다
                wf = (
                    wallet_fields(dom_row, fx_row)
                    if memo is None
                    else memo.fields((dom_ex, fx_ex, base), dom_row, fx_row)
                )
                # 위치 인자 — 순서는 TickRow 선언 그대로(dom·fx·base·fwd·rev·가격 3·입출금 4·망 이름 2)
                append(
                    TickRow(
                        dom_ex,
                        fx_ex,
                        base,
                        premium_percent(buy_krw=fx_ask * rate_ask, sell_krw=dom_bid),
                        premium_percent(buy_krw=dom_ask, sell_krw=fx_bid * rate_bid),
                        dom_row.price,
                        fx_row.price,
                        mid,
                        wf.dep_dom,
                        wf.wd_dom,
                        wf.dep_fx,
                        wf.wd_fx,
                        wf.net_dom,
                        wf.net_fx,
                    )
                )
    return Tick(ts=ts, rows=tuple(rows), dw_failed=tuple(dw_failed))


def _summary(stage: str, samples: list[float]) -> str:
    ordered = sorted(samples)
    n = len(ordered)
    p95 = ordered[min(n - 1, n * 95 // 100)]
    return f"{stage} {ordered[n // 2]:.2f}/{p95:.2f}/{ordered[-1]:.2f}"


class _StageTimes:
    """틱 루프 단계별 소요(ms)를 모았다가 60초마다 p50/p95/max 한 줄로 (§3.6 로그).

    운영에서 초 경계 → 표 완성이 100ms 를 넘는데 어느 단계가 먹는지 보이지 않았다 — 줄일 곳을 고르려면
    단계별로 봐야 한다. 로그뿐이라 틱의 값·순서는 그대로다.
    """

    def __init__(self) -> None:
        self._samples: dict[str, list[float]] = {}
        self._since: int | None = None
        self._ticks = 0

    def add(self, stage: str, ms: float) -> None:
        samples = self._samples.get(stage)
        if samples is None:
            self._samples[stage] = [ms]
        else:
            samples.append(ms)

    def tick_done(self, ts: int) -> None:
        self._ticks += 1
        if self._since is None:
            self._since = ts
            return
        if ts - self._since < STAGE_REPORT_SEC:
            return
        logger.info(
            "틱 루프 %d초 요약 — 틱 %d번, 단계별 ms p50/p95/max: %s",
            ts - self._since,
            self._ticks,
            " · ".join(_summary(stage, xs) for stage, xs in self._samples.items()),
        )
        self._samples = {}
        self._since = ts
        self._ticks = 0


class TickLoop:
    def __init__(
        self,
        *,
        store: LiveStore,
        streams: Sequence[StreamJudge],
        client: httpx.AsyncClient,
        handoff: TickHandoff = noop_handoff,
        outages: OutageSink | None = None,
        events: EventSink | None = None,
        candles: EventSink | None = None,
        spreads: EventSink | None = None,
        gap: EventSink | None = None,
        heartbeat: EventSink | None = None,
        wallet: WalletStatusProvider | None = None,
        day_open: EventSink | None = None,
        wallet_memo: WalletMemo | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._streams = list(streams)
        self._client = client
        self._handoff = handoff
        self._outages = outages
        self._events = events
        self._candles = candles
        self._spreads = spreads
        self._gap = gap
        self._heartbeat = heartbeat
        self._day_open = day_open
        self._wallet = wallet
        # 006 §3.7 망 판정 메모 — 틱이 회차를 열고, 같은 메모를 받은 표 게시기(017)가 같은 회차에 읽는다
        self._wallet_memo = wallet_memo if wallet_memo is not None else WalletMemo()
        self._clock = clock
        self._sleep = sleep
        self._times = _StageTimes()
        self._task: asyncio.Task[None] | None = None
        self._wallet_task: asyncio.Task[dict[str, int] | None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """매초 경계마다 tick(). 예외는 로그 후 다음 초."""
        while True:
            now = self._clock()
            target = math.floor(now) + 1
            delay = max(0.0, target - now)
            slept_from = time.perf_counter()
            await self._sleep(delay)
            # 목표 초 경계보다 늦게 깬 만큼 — 그동안 이벤트 루프가 다른 일(수신·GC)에 막혀 있었다는 뜻이다
            self._times.add("깨어남", (time.perf_counter() - slept_from - delay) * 1000)
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
        """§3.6-2~5 — 동기. 틱 생성 → 슬롯·인계 → received_at → 판정. 단계마다 걸린 시간을 모은다."""
        started = lap = time.perf_counter()
        dw_failed: list[str] = []
        if self._wallet is not None:
            # 입출금 캐시를 행에 반영한다 — 메시지로 새로 생긴 행도 1초 안에 3필드를 갖는다
            for ex in EXCHANGES:
                self._wallet.apply(self._store.get_all(exchange=ex), ex)
            dw_failed = self._wallet.failed()
            lap = self._lap("입출금", lap)
        tick = build_tick(self._store, ts, dw_failed, self._wallet_memo)
        lap = self._lap("틱", lap)
        prev = self._store.push_tick(tick)
        if prev is not None:
            self._handoff(prev)
        lap = self._lap("인계", lap)
        self._store.mark_received(ts)
        self._judge_all(ts * 1000)
        lap = self._lap("판정", lap)
        if self._events is not None:
            # 013 — 사건 감지는 현재 틱으로(인계되는 직전 틱이 아니라) — ts 가 곧 판정 시각이다
            self._events.observe(tick)
            lap = self._lap("사건", lap)
        if self._candles is not None:
            # 014 — 1분 집계도 현재 틱으로, 013 감지기 다음 자리
            self._candles.observe(tick)
            lap = self._lap("봉", lap)
        if self._day_open is not None:
            # 026 — 기준가 장부는 표 게시 바로 앞 — 같은 틱의 표가 그 틱에 잡힌 기준가를 본다
            self._day_open.observe(tick)
            lap = self._lap("기준가", lap)
        if self._spreads is not None:
            # 017 — 표 게시는 맨 마지막 자리 — received_at 이 찍힌 뒤라 GET /spreads 와 같은 표가 나온다
            self._spreads.observe(tick)
            lap = self._lap("표", lap)
        if self._gap is not None:
            # 048 — 현선갭 표는 spreads 표 다음 같은 회차에서. 같은 메모리 호가로 두 표가 한 초에 나간다
            self._gap.observe(tick)
            lap = self._lap("갭표", lap)
        if self._heartbeat is not None:
            # 025 — 맨 끝: 여기까지 왔다 = 이 초의 틱이 온전히 끝났다. 위에서 예외가 나면 이 초는 안 쓴다
            self._heartbeat.observe(tick)
            lap = self._lap("심장박동", lap)
        self._times.add("합계", (lap - started) * 1000)
        self._times.tick_done(ts)
        return tick

    def _lap(self, stage: str, since: float) -> float:
        now = time.perf_counter()
        self._times.add(stage, (now - since) * 1000)
        return now

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
                body=err.body,
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
