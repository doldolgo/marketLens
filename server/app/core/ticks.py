"""틱 루프(1초)와 판정 — 스펙 001 §3.6·§3.8.

매초 경계에 LiveStore 의 최신 시세로 틱을 만들어 슬롯에 넣고 직전 틱을 009 인계 함수에
넘긴다. 틱 생성은 동기다 — 한 틱 안에서 교체 전후 호가가 섞이지 않는 유일한 근거다.
틱 루프는 예외를 밖으로 던지지 않는다(버그 하나로 멈추지 않게 로그 후 다음 초).
단계별 소요와 깨어남 지연, 세대별 GC 수집은 60초마다 한 줄로 남긴다(§3.6 로그) — 동작은 바꾸지 않는다.
틱 회차 동안(틱 끝 + GC_HOLD_SEC 까지)은 자동 GC 수집을 보류한다(§3.6, 2026-10-09).
"""

import asyncio
import contextlib
import gc
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
# 틱이 끝난 뒤 자동 GC 수집을 다시 켜기까지 기다리는 초(§3.6, 2026-10-09). 틱 안에서는 TickRow 1,800개·인계
# 행 dict·표 행이 한꺼번에 생겨 0세대 계수가 거의 매 틱 문턱(2000)을 넘는다. 그 자리에서 도는 수집은 직전 수집
# 뒤 교체된 LiveStore 행을 훑느라 틱을 회당 7~11ms(재생, 최대 25ms) 늦추는데, 틱이 끝나면 그 할당 대부분이
# 풀려 거둘 것이 거의 없다. 틱 동안 꺼 두면 수집이 틱 밖으로 옮겨 가고 횟수도 준다. 0.2초는 틱이 만든 표
# PUBLISH·인계 XADD 보내기 태스크가 다시 켜기 전에 돌게 하려는 여유다 — 틱이 초 경계 뒤 수십 ms 에 끝나므로
# 다시 켜는 때는 다음 초 경계보다 한참 앞이다.
GC_HOLD_SEC = 0.2
# 다시 켜기 전에 다음 틱이 와 보류를 이어 간 횟수의 상한 — 넘으면 그 틱은 보류하지 않는다(_hold_gc)
GC_HOLD_CARRY_MAX = 5

# 틱 조합의 페어 분류 — KRW 호가면 국내, USDT 호가면 해외 (003 §3.2)
_DOMESTIC_QUOTE = "KRW"
_FOREIGN_QUOTE = "USDT"
# 틱 행을 namedtuple 의 파이썬 __new__(인자 이름을 붙이는 프레임 + 인자 튜플)를 거치지 않고 튜플에서 바로
# 만든다 — 그 __new__ 도 결국 tuple.__new__(TickRow, 값 튜플)이라 타입·값이 같다. 매초 1,800행이라 행마다
# 프레임 하나가 아깝다. 필드 14개를 전부 위치로 넘기므로 TickRow 필드를 바꾸면 build_tick 의 그 자리도 함께
# 고쳐야 한다(tuple.__new__ 는 개수를 검사하지 않는다 — tests/test_ticks_tick_row_build.py 가 길이를 본다).
_new_tick_row = tuple.__new__


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
        quote = row.quote
        if quote == _DOMESTIC_QUOTE:
            side = domestic
        elif quote == _FOREIGN_QUOTE:
            side = foreign
        else:
            continue
        # setdefault(ex, {}) 는 표가 이미 있어도 행마다 빈 dict 를 새로 만든다 — 매초 1,700행이라 찾고 없을 때만 만든다
        table = side.get(row.exchange)
        if table is None:
            table = side[row.exchange] = {}
        table[row.base.upper()] = row

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
                # 값 14개 전부, 순서는 TickRow 선언 그대로(dom·fx·base·fwd·rev·가격 3·입출금 4·망 이름 2) —
                # _new_tick_row 는 TickRow(...) 와 같은 행을 만든다(모듈 머리 주석)
                append(
                    _new_tick_row(
                        TickRow,
                        (
                            dom_ex,
                            fx_ex,
                            base,
                            premium_percent(
                                buy_krw=fx_ask * rate_ask, sell_krw=dom_bid
                            ),
                            premium_percent(
                                buy_krw=dom_ask, sell_krw=fx_bid * rate_bid
                            ),
                            dom_row.price,
                            fx_row.price,
                            mid,
                            wf.dep_dom,
                            wf.wd_dom,
                            wf.dep_fx,
                            wf.wd_fx,
                            wf.net_dom,
                            wf.net_fx,
                        ),
                    )
                )
    return Tick(ts=ts, rows=tuple(rows), dw_failed=tuple(dw_failed))


def _summary(stage: str, samples: list[float]) -> str:
    ordered = sorted(samples)
    n = len(ordered)
    p95 = ordered[min(n - 1, n * 95 // 100)]
    return f"{stage} {ordered[n // 2]:.2f}/{p95:.2f}/{ordered[-1]:.2f}"


class _GcStats:
    """세대별 GC 수집 횟수·합 ms(틱 안/밖)·거둔 객체 수·마지막 2세대 수집 시각 (§3.6 로그, 2026-10-09).

    세대 임계(001 §3.1)를 사람이 다시 정하려면 운영에서 수집이 어디서 얼마나 도는지, 순환 쓰레기가 실제로
    얼마나 나오는지(거둔 객체 수), 전체 수집이 얼마 만에 한 번 도는지를 봐야 한다 — 재생으로는 운영의 순환
    쓰레기원(웹소켓·httpx 예외·재연결·스레드)을 흉내 낼 수 없다. TickLoop.run 이 도는 동안만 gc.callbacks 에
    걸린다(armed). 콜백은 수집 한가운데서 불리므로 미리 만든 목록의 칸만 올린다 — 객체를 거의 만들지 않는다.
    다른 스레드(S3·Influx 작업)에서 돈 수집도 같은 프로세스 GC 라 함께 센다. 로그뿐이라 동작은 그대로다.
    """

    def __init__(self) -> None:
        self.armed = (
            False  # gc.callbacks 에 걸려 있는 동안만 True — 요약 줄에 GC 칸을 붙일지
        )
        self.in_tick = False  # TickLoop.run 이 tick() 을 부르는 동안 True
        self._started = 0.0
        # 칸 = 세대 × 2 + (0 틱 안, 1 틱 밖)
        self._count = [0] * 6
        self._ms = [0.0] * 6
        self._collected = [0, 0, 0]
        self._last_full: float | None = None  # 마지막 2세대 수집이 끝난 monotonic 시각

    def callback(self, phase: str, info: dict[str, int]) -> None:
        if phase == "start":
            self._started = time.perf_counter()
            return
        gen = info["generation"]
        slot = gen * 2 + (0 if self.in_tick else 1)
        self._count[slot] += 1
        self._ms[slot] += (time.perf_counter() - self._started) * 1000
        self._collected[gen] += info["collected"]
        if gen == 2:
            self._last_full = time.monotonic()

    def summary(self) -> str:
        """요약 줄 끝 칸 — 세대마다 '안/밖 횟수 · 안/밖 ms · 거둔 객체' 와 마지막 2세대 수집 뒤 초. 부르면 창을 비운다."""
        parts = []
        for gen in range(3):
            i = gen * 2
            parts.append(
                f"{gen}세대 {self._count[i]}/{self._count[i + 1]}번 "
                f"{self._ms[i]:.1f}/{self._ms[i + 1]:.1f}ms 거둔 {self._collected[gen]}개"
            )
        if self._last_full is None:
            parts.append("2세대 수집 아직 없음")
        else:
            parts.append(
                f"마지막 2세대 수집 뒤 {time.monotonic() - self._last_full:.0f}초"
            )
        self._count = [0] * 6
        self._ms = [0.0] * 6
        self._collected = [0, 0, 0]
        return "GC 틱 안/밖: " + " · ".join(parts)


class _StageTimes:
    """틱 루프 단계별 소요(ms)를 모았다가 60초마다 p50/p95/max 한 줄로 (§3.6 로그).

    운영에서 초 경계 → 표 완성이 100ms 를 넘는데 어느 단계가 먹는지 보이지 않았다 — 줄일 곳을 고르려면
    단계별로 봐야 한다. 로그뿐이라 틱의 값·순서는 그대로다. TickLoop.run 이 돌면(gc 가 armed) 같은 줄 끝에
    그 60초의 세대별 GC 수집을 붙인다.
    """

    def __init__(self, gc_stats: _GcStats | None = None) -> None:
        self._samples: dict[str, list[float]] = {}
        self._since: int | None = None
        self._ticks = 0
        self._gc = gc_stats

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
        stages = " · ".join(_summary(stage, xs) for stage, xs in self._samples.items())
        if self._gc is not None and self._gc.armed:
            stages += " — " + self._gc.summary()
        logger.info(
            "틱 루프 %d초 요약 — 틱 %d번, 단계별 ms p50/p95/max: %s",
            ts - self._since,
            self._ticks,
            stages,
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
        self._gc = _GcStats()
        # gc.callbacks 에서 뺄 때 같은 객체로 찾도록 바운드 메서드를 한 번만 만든다
        self._gc_callback = self._gc.callback
        self._times = _StageTimes(self._gc)
        # 틱 뒤 자동 GC 를 다시 켤 예약(GC_HOLD_SEC) — 걸려 있는 동안은 이 루프가 GC 를 꺼 둔 상태다
        self._gc_resume: asyncio.TimerHandle | None = None
        self._gc_carried = 0  # 다시 켜기 전에 다음 틱이 와 보류를 잇달아 이어 간 횟수
        # 입출금 캐시를 전 행에 마지막으로 반영한 조회기 세대 — None 이면 다음 틱이 전 행에 반영한다(§3.6-1)
        self._wallet_gen: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._wallet_task: asyncio.Task[dict[str, int] | None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """매초 경계마다 tick(). 예외는 로그 후 다음 초.

        tick() 둘레에서 자동 GC 수집을 보류하고 틱 끝 + GC_HOLD_SEC 에 다시 켠다(§3.6, 2026-10-09). 보류는
        tick() 안이 아니라 여기서 한다 — 테스트가 tick() 을 동기로 직접 부르고, 다시 켜는 예약(call_later)에는
        돌고 있는 이벤트 루프가 필요하다. 세대 임계·얼리기(001 §3.1)는 건드리지 않는다(임계를 바꿔 끄면 임계를
        보는 쪽이 보류 창에 다른 값을 읽는다). 보류 중에도 0세대 계수는 쌓이므로 다시 켠 뒤 첫 할당에서 밀린
        수집이 한 번 돌고, 순환 쓰레기가 치워지는 기준(순증 2000 마다 0세대, 그 10번마다 1세대·2세대)은 그대로다.
        강제 gc.collect 일정은 두지 않는다 — 이 보류만으로 틱 안 수집이 없어지고 수집 총량도 준다(재생 측정).
        """
        gc.callbacks.append(self._gc_callback)
        self._gc.armed = True
        try:
            while True:
                now = self._clock()
                target = math.floor(now) + 1
                delay = max(0.0, target - now)
                slept_from = time.perf_counter()
                await self._sleep(delay)
                # 목표 초 경계보다 늦게 깬 만큼 — 그동안 이벤트 루프가 다른 일(수신·GC)에 막혀 있었다는 뜻이다
                self._times.add(
                    "깨어남", (time.perf_counter() - slept_from - delay) * 1000
                )
                self._kick_wallet()
                held = self._hold_gc()
                self._gc.in_tick = True
                try:
                    self.tick(int(target))
                except Exception:
                    logger.exception("틱 생성이 예외로 끝났다 — 다음 초에 계속")
                finally:
                    self._gc.in_tick = False
                    if held:
                        # 예외로 끝난 틱도 여기서 다시 켜기를 예약한다 — 빠뜨리면 GC 가 꺼진 채로 남는다
                        self._gc_resume = asyncio.get_running_loop().call_later(
                            GC_HOLD_SEC, self._resume_gc
                        )
        finally:
            self._gc.armed = False
            with contextlib.suppress(ValueError):
                gc.callbacks.remove(self._gc_callback)

    def _hold_gc(self) -> bool:
        """이번 틱 동안 자동 GC 를 끈다. 이 루프가 끈 것이면 True — 틱 뒤 다시 켜기를 예약한다."""
        pending = self._gc_resume
        if pending is not None:
            # 직전 틱의 다시 켜기가 아직이다(틱이 늦게 끝나 GC_HOLD_SEC 안에 다음 초가 왔다) — 예약을 거두고
            # 꺼 둔 채로 이어서 보류한다. 여기서 isenabled 만 보면 '남이 껐다' 로 읽혀 영영 다시 켜지 않는다
            pending.cancel()
            self._gc_resume = None
            self._gc_carried += 1
            if self._gc_carried > GC_HOLD_CARRY_MAX:
                # 루프가 포화돼 틱이 매번 다음 초 경계 바로 앞에서 끝나면 다시 켜기가 한 번도 돌지 않는다 —
                # 이번 틱은 보류하지 않고 켠 채로 돌려 밀린 수집이 틱 안에서라도 돌게 한다(순환 쓰레기가 쌓이지 않게)
                self._gc_carried = 0
                gc.enable()
                return False
            gc.disable()
            return True
        self._gc_carried = 0
        if not gc.isenabled():
            return False  # 이 루프 밖에서 이미 꺼 두었다 — 남이 정한 상태는 건드리지 않는다
        gc.disable()
        return True

    def _resume_gc(self) -> None:
        self._gc_resume = None
        gc.enable()

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
        # §3.4-1 — 커넥터가 미뤄 둔 호가 프레임을 먼저 반영한다(읽기 메서드도 하지만 단계 시간을 따로 보려고 여기서)
        self._store.settle()
        lap = self._lap("미룬 호가", lap)
        dw_failed: list[str] = []
        if self._wallet is not None:
            # 입출금 캐시를 행에 반영한다 — 메시지로 새로 생긴 행도 1초 안에 3필드를 갖는다
            self._apply_wallet(self._wallet)
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

    def _apply_wallet(self, wallet: WalletStatusProvider) -> None:
        """§3.6-1 — 조회 결과가 바뀐 틱(세대)에는 여섯 거래소 전 행에, 그 사이 틱에는 직전 행 없이 새로 생긴 행에만.

        결과는 매 틱 전 행에 반영하는 것과 같다. 캐시는 60초에 한 번(거래소별 조회가 끝날 때마다) 바뀌고, 그 사이에
        교체된 행은 put_row 가 직전 행의 3필드를 이미 물려받았기 때문이다(§3.3) — 행의 3필드를 쓰는 곳은 물려받기와
        apply 둘뿐이다. 1,700행 apply 를 매초 되풀이하던 일(재생 ≈0.75ms/틱)이 새 행 몇 개로 준다.
        세대(`generation`)가 없는 제공자(테스트의 가짜)는 바뀐 때를 알 수 없으니 매 틱 전 행에 반영한다.
        """
        gen = getattr(wallet, "generation", None)
        full = gen is None or gen != self._wallet_gen
        # 반영 도중 예외가 나면 세대를 모르는 상태로 남겨 다음 틱이 전 행으로 다시 반영하게 한다 — 새 행 목록은
        # 아래에서 이미 비워 그 행들이 다음 세대까지 빠질 수 있기 때문이다
        self._wallet_gen = None
        fresh = self._store.take_new_rows()
        if full:
            for ex in EXCHANGES:
                wallet.apply(self._store.get_all(exchange=ex), ex)
        else:
            for (
                ex
            ) in EXCHANGES:  # EXCHANGES 밖 거래소의 행은 전 행 경로처럼 건드리지 않는다
                rows = fresh.get(ex)
                if rows:
                    wallet.apply(rows, ex)
        self._wallet_gen = gen

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
        pending = self._gc_resume
        if pending is not None:
            # 루프가 멈춰 예약이 안 돌 수 있다 — 이 루프가 꺼 둔 GC 를 여기서 다시 켠다
            pending.cancel()
            self._gc_resume = None
            gc.enable()
        last = self._store.tick
        if last is not None:
            self._handoff(last)
