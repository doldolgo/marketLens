"""수집 루프 — 스펙 001 §3.2.

한 사이클: 국내 동시 수집 → 환율 추출 → 바이낸스 수집 → 행 교집합 필터 → 메모리 교체.
사이클은 예외를 밖으로 던지지 않고, 거래소별 실패를 결과 요약에 담는다.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.core.config import COLLECT_INTERVAL
from app.core.connectors.base import ExchangeConnector, FetchResult
from app.core.errors import ExchangeError
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.outages import OutageTracker

logger = logging.getLogger("marketlens.collector")


@dataclass
class CycleResult:
    """한 사이클 결과 요약 — 스펙 003 이 POST /refresh 응답으로 노출한다."""

    saved: dict[str, int]  # 거래소별 저장 수 (실패 거래소는 0)
    rates_observed: list[str]  # 이번 사이클에 환율이 관측된 국내 거래소 id
    failures: list[dict[str, str]]  # {exchange, error_code, message}
    warnings: list[str]
    calls: dict[str, int]  # 거래소별 호출 수 (실패 거래소는 빠짐)
    duration_ms: float
    fetched_at: int  # epoch ms
    # 거래소별 입출금 조회 성공 여부 — /refresh 에서는 walletStatusAvailable 로 노출 (006 §3.5)
    wallet_status_available: dict[str, bool] = field(default_factory=dict)


@dataclass
class _Failure:
    error_code: str
    message: str
    # 실패 이력(011)에 넘길 분류·원문 — 커넥터 예외가 아닌 예상 밖 예외면 None
    error: ExchangeError | None = None


class WalletStatusProvider(Protocol):
    """입출금 상태 조회기 묶음의 계약 — 실물은 features/wallet_status (006), 배선은 main.py.

    core 가 features 를 import 하지 않도록 구조적 타입으로만 안다.
    """

    async def refresh_if_due(self, client: httpx.AsyncClient) -> dict[str, int] | None:
        """60초가 지났으면 병렬 조회 후 거래소별 호출 수, 캐시 사이클은 None."""
        ...

    def apply(self, rows: list[Row], exchange: str) -> None:
        """캐시를 스냅샷 행(dep/wd/networks)에 반영한다."""
        ...

    def availability(self) -> dict[str, bool]: ...

    def warnings(self) -> list[str]: ...

    def failed(self) -> list[str]: ...


class Collector:
    def __init__(
        self,
        store: LiveStore,
        domestic: list[ExchangeConnector],
        foreign: ExchangeConnector,
        client: httpx.AsyncClient,
        interval: float = COLLECT_INTERVAL,
        wallet: WalletStatusProvider | None = None,
        outages: OutageTracker | None = None,
    ) -> None:
        self._store = store
        self._domestic = domestic
        self._foreign = foreign
        self._client = client
        self._interval = interval
        self._wallet = wallet
        self._outages = outages
        # 사이클은 동시에 두 개 돌지 않는다 — 루프와 수동 트리거(003 /refresh)가 겹치면 뒤가 기다린다
        self._lock = asyncio.Lock()
        # 입출금 조회가 실패 상태인 거래소 id — 저장 루프(005)가 읽어 dw_fail 점을 쓴다.
        # 다음 성공 조회까지 유지된다(60초 캐시라 사이클마다 조회하지 않는다 — 006 §3.5).
        self.dw_failed: list[str] = []

    @property
    def lock(self) -> asyncio.Lock:
        """수집과 저장(005 persist)이 같은 락을 잡는다 — 교체 도중 읽으면 반쪽이 남는다."""
        return self._lock

    async def run_loop(self) -> None:
        """앱 시작과 함께 돌고 종료 시 취소된다. 한 사이클이 끝난 뒤 interval 만큼 쉰다."""
        while True:
            try:
                await self.run_cycle()
            except Exception:
                # 사이클은 예외를 던지지 않는 계약이지만, 버그 하나로 수집이 영구 중단되는 것을 막는다
                logger.exception("수집 사이클이 예외로 끝났다")
            await asyncio.sleep(self._interval)

    async def run_cycle(self) -> CycleResult:
        async with self._lock:
            return await self._cycle()

    async def _cycle(self) -> CycleResult:
        started = time.monotonic()
        fetched_at = int(time.time() * 1000)
        failures: list[dict[str, str]] = []
        warnings: list[str] = []
        calls: dict[str, int] = {}
        saved: dict[str, int] = {}

        # 입출금 상태(006)는 시세 수집과 겹쳐서 진행한다 — 60초에 한 번만 실제 호출이 나간다
        wallet_task: asyncio.Task[dict[str, int] | None] | None = None
        if self._wallet is not None:
            wallet_task = asyncio.create_task(self._wallet.refresh_if_due(self._client))

        try:
            return await self._cycle_body(
                wallet_task, started, fetched_at, failures, warnings, calls, saved
            )
        except BaseException:
            # 사이클이 취소·실패하면 지갑 태스크를 고아로 남기지 않는다 — 닫힌
            # httpx 클라이언트를 뒤늦게 쓰다 종료 로그를 오염시키는 것을 막는다
            if wallet_task is not None and not wallet_task.done():
                wallet_task.cancel()
                with contextlib.suppress(BaseException):
                    await wallet_task
            raise

    async def _cycle_body(
        self,
        wallet_task: asyncio.Task[dict[str, int] | None] | None,
        started: float,
        fetched_at: int,
        failures: list[dict[str, str]],
        warnings: list[str],
        calls: dict[str, int],
        saved: dict[str, int],
    ) -> CycleResult:
        # 1. 국내(업비트·빗썸) 동시 수집
        domestic_outcomes = await asyncio.gather(
            *[self._safe_fetch(c) for c in self._domestic]
        )
        domestic_rows: dict[str, list[Row]] = {}
        for conn, outcome in zip(self._domestic, domestic_outcomes, strict=True):
            if isinstance(outcome, _Failure):
                failures.append(
                    {
                        "exchange": conn.id,
                        "error_code": outcome.error_code,
                        "message": outcome.message,
                    }
                )
            else:
                domestic_rows[conn.id] = outcome.rows
                calls[conn.id] = outcome.calls

        # 2. 환율 추출 — 추가 HTTP 호출 없이 KRW 호가 결과의 USDT 항목에서 뽑는다
        observed: dict[str, tuple[float, float]] = {}
        missing_rate: list[str] = []
        for conn in self._domestic:
            if conn.id not in domestic_rows:
                continue  # 수집 실패는 failures 에 이미 있고, 환율은 직전 값을 유지한다
            usdt = next(
                (r for r in domestic_rows[conn.id] if r.base.upper() == "USDT"), None
            )
            ask = usdt.asks[0][0] if usdt and usdt.asks else 0.0
            bid = usdt.bids[0][0] if usdt and usdt.bids else 0.0
            if ask > 0 and bid > 0:
                observed[conn.id] = (ask, bid)
            else:
                missing_rate.append(conn.id)
        if missing_rate:
            warnings.append(
                "KRW-USDT 호가가 없어 환율을 못 구한 거래소: "
                + ", ".join(missing_rate)
                + " (해당 국내 거래소의 김프 계산은 이번 회차에 빠진다)."
            )

        # 3. 바이낸스 수집
        foreign_outcome = await self._safe_fetch(self._foreign)
        foreign_rows: list[Row] | None
        if isinstance(foreign_outcome, _Failure):
            failures.append(
                {
                    "exchange": self._foreign.id,
                    "error_code": foreign_outcome.error_code,
                    "message": foreign_outcome.message,
                }
            )
            foreign_rows = None
        else:
            foreign_rows = foreign_outcome.rows
            calls[self._foreign.id] = foreign_outcome.calls

        # 거래소별 성공/실패를 실패 이력 추적기에 넘긴다 (011 §3.3). 사이클 시각 = fetched_at.
        if self._outages is not None:
            for conn, outcome in [
                *zip(self._domestic, domestic_outcomes, strict=True),
                (self._foreign, foreign_outcome),
            ]:
                self._track(self._outages, conn.id, outcome, fetched_at)

        # 4. 교집합 필터 — "이번 사이클 성공 거래소 + 유지된 거래소" 전체로 계산한다
        domestic_union: set[str] = set()
        for conn in self._domestic:
            if conn.id in domestic_rows:
                domestic_union |= {r.base.upper() for r in domestic_rows[conn.id]}
            else:
                domestic_union |= {
                    r.base.upper() for r in self._store.get_all(exchange=conn.id)
                }
        if foreign_rows is not None:
            foreign_bases = {r.base.upper() for r in foreign_rows}
        else:
            foreign_bases = {
                r.base.upper() for r in self._store.get_all(exchange=self._foreign.id)
            }
        allowed = (
            domestic_union & foreign_bases
        )  # USDT 는 바이낸스에 USDT/USDT 가 없어 자연히 빠진다

        # 5. 메모리 교체 — 성공한 거래소만 통째 교체, 실패한 거래소는 직전 세트 유지
        now = datetime.now(UTC)
        for conn in self._domestic:
            if conn.id in domestic_rows:
                kept = [r for r in domestic_rows[conn.id] if r.base.upper() in allowed]
                self._store.replace_exchange(conn.id, kept, now)
                saved[conn.id] = len(kept)
            else:
                saved[conn.id] = 0
        if foreign_rows is not None:
            kept = [r for r in foreign_rows if r.base.upper() in allowed]
            self._store.replace_exchange(self._foreign.id, kept, now)
            saved[self._foreign.id] = len(kept)
        else:
            saved[self._foreign.id] = 0
        # 환율은 이번에 관측된 거래소만 덮어쓴다 — 낡은 값이 없는 것보다 낫다
        for exchange, (ask, bid) in observed.items():
            self._store.set_rate(exchange, ask, bid, now)
        self._store.mark_received(int(time.time()))

        # 입출금 조회 합류 — 시세 교체(4~5단계) **뒤**에 기다린다. 지갑 API 가 10초까지
        # 매달려도 시세 신선도(age)와 메모리 교체는 영향받지 않는다 (006 §3.5).
        # 실제 호출이 나간 사이클만 거래소별 호출 카운트를 올린다.
        if wallet_task is not None:
            wallet_calls = await wallet_task
            if wallet_calls:
                for ex, n in wallet_calls.items():
                    calls[ex] = calls.get(ex, 0) + n

        # 6. 입출금 캐시 반영 — 유지된(이번 사이클 실패) 거래소의 행에도 덮어써야
        #    "실패 사이클은 직전 성공값을 유지하지 않는다"(006 §3.5)가 성립한다
        wallet_available: dict[str, bool] = {}
        if self._wallet is not None:
            for ex in [*(c.id for c in self._domestic), self._foreign.id]:
                self._wallet.apply(self._store.get_all(exchange=ex), ex)
            warnings.extend(self._wallet.warnings())
            self.dw_failed = self._wallet.failed()
            wallet_available = self._wallet.availability()
        else:
            self.dw_failed = []

        return CycleResult(
            saved=saved,
            rates_observed=list(observed),
            failures=failures,
            warnings=warnings,
            calls=calls,
            duration_ms=(time.monotonic() - started) * 1000,
            fetched_at=fetched_at,
            wallet_status_available=wallet_available,
        )

    @staticmethod
    def _track(
        tracker: OutageTracker,
        exchange: str,
        outcome: FetchResult | _Failure,
        fetched_at: int,
    ) -> None:
        if not isinstance(outcome, _Failure):
            tracker.record_success(exchange, fetched_at)
            return
        exc = outcome.error
        if exc is None:
            # 커넥터 밖의 예상 밖 예외(버그) — 거래소 응답을 해석 못 한 것으로 본다
            tracker.record_failure(
                exchange,
                fetched_at,
                kind="bad_response",
                message=outcome.message,
                status_code=None,
                url=None,
                retry_after_sec=None,
            )
            return
        tracker.record_failure(
            exchange,
            fetched_at,
            kind=exc.kind,
            # 거래소 원문 body 가 있으면 그것, 없으면 커넥터 message (011 §3.3)
            message=exc.body if exc.body else exc.message,
            status_code=exc.status_code,
            url=exc.url,
            retry_after_sec=exc.retry_after_sec,
        )

    async def _safe_fetch(self, conn: ExchangeConnector) -> FetchResult | _Failure:
        """거래소 하나의 실패가 사이클을 죽이지 않게 예외를 실패 기록으로 바꾼다."""
        try:
            return await conn.fetch_rows(self._client)
        except ExchangeError as exc:
            logger.warning("%s 수집 실패: %s", conn.id, exc)
            return _Failure(error_code=exc.code, message=str(exc), error=exc)
        except Exception as exc:
            logger.exception("%s 수집 중 예상 밖 예외", conn.id)
            return _Failure(
                error_code="internal_error", message=f"{type(exc).__name__}: {exc}"
            )
