"""이더리움 ERC-20 입출금 감지기 — 업비트 주소 집합 대조 → Influx `chain_flow` (스펙 050 §3.2~3.5).

수집 프로세스 안의 상시 태스크다(별도 프로세스 없음). 소켓 하나에 구독 둘 — 컨트랙트 206개의 Transfer 로그와
newHeads — 을 걸고, 블록마다 로그를 입금·출금·sweep·내부 이동으로 가르며 주소 집합 셋(입금주소·핫월렛·내부)을
스스로 넓힌다. 로그에는 블록 번호뿐이라 newHeads 가 준 번호→시각 표로 시각을 매기고, 표에 없으면 HTTP 로 블록
하나를 읽는다. 다른 커넥터(001·012·019·020)와 백오프 숫자만 같고 코드는 공유하지 않는다 — quirk 가 섞이면 디버깅 불가.
core 에 사는 이유: 쓰는 쪽이 수집 수명주기라 기능 폴더가 될 수 없다. 읽기 API 는 features/flow.
"""

import asyncio
import contextlib
import csv
import functools
import gzip
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from websockets.asyncio.client import connect as ws_connect

from app.core.config import WS_OPEN_TIMEOUT
from app.core.influx import ChainFlowRow, chain_flow_line
from app.core.redis_bus import (
    FLOW_DEPOSIT_ADDRS_KEY,
    FLOW_HOT_WALLETS_KEY,
    FLOW_INTERNAL_KEY,
)

logger = logging.getLogger("marketlens.eth_flow")

# ERC-20 Transfer(address,address,uint256) 시그니처 — 로그 구독의 topics[0] (§3.3)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# 업비트 가스 지갑 — 이 지갑이 ETH 를 보낸 수신자가 새 입금주소다 (§3.2 자가 확장 1)
GAS_WALLET = "0x3e0a91dc5848e17765e3167249a2cb018cbb60ee"
SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "upbit_eth"
# 연결 직후 공백 재생 — 이 블록 수(약 1일)를 넘으면 head 부터, 20블록씩(100블록은 응답 상한 1만 건을 넘긴다) (§3.4)
REPLAY_MAX_BLOCKS = 7_200
REPLAY_CHUNK = 20
HEADS_SILENCE_SEC = 30.0  # 이만큼 newHeads 가 없으면 닫고 재연결 (§3.4)
HEADS_CHECK_SEC = 5.0  # 무수신 감시 주기 — 감지는 30~35초 사이
# 블록 N 의 로그는 head N 뒤 약 1초 안에 온다(2026-10-08 publicnode 실측) — head 를 받고 이만큼 기다린 뒤 블록을 한 회차로 처리
BLOCK_SETTLE_SEC = 1.5
LATE_LOG_EVERY = 100  # 처리가 끝난 블록에 뒤늦게 온 로그 — 이만큼마다 INFO 1줄
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
PENDING_LIMIT = 10_000  # 미전송 점 상한 — 넘치면 오래된 것부터 버린다 (§3.5)
HTTP_TIMEOUT_SEC = 10.0
CLOSE_TIMEOUT = 2.0
# 블록 번호→시각 표의 크기 — 리오그 되돌림·늦게 온 로그가 찾는다(≈50분)
_BLOCK_TS_KEEP = 256


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class Contract:
    symbol: str
    decimals: int


@dataclass
class Seeds:
    """씨앗 넷(§3.2) — 주소는 소문자. 컨트랙트는 주소 → (심볼, 소수 자릿수)."""

    deposit: set[str]
    hot: set[str]
    internal: set[str]
    contracts: dict[str, Contract]


def _read_addresses(path: Path) -> set[str]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return {
            row["address"].strip().lower()
            for row in csv.DictReader(f)
            if row["address"].strip()
        }


def load_seeds(directory: Path = SEED_DIR) -> Seeds:
    """gzip CSV 넷 → 집합 셋·컨트랙트 표. 실패는 예외 그대로 — 감지기 없이 뜨면 거짓 0 이 쌓이므로 기동 실패다 (§3.2)."""
    contracts: dict[str, Contract] = {}
    with gzip.open(
        directory / "contracts.csv.gz", "rt", encoding="utf-8", newline=""
    ) as f:
        for row in csv.DictReader(f):
            contracts[row["contract"].strip().lower()] = Contract(
                symbol=row["symbol"].strip(), decimals=int(row["decimals"])
            )
    return Seeds(
        deposit=_read_addresses(directory / "deposit_addresses.csv.gz"),
        hot=_read_addresses(directory / "hot_wallets.csv.gz"),
        internal=_read_addresses(directory / "internal.csv.gz"),
        contracts=contracts,
    )


@dataclass(frozen=True, slots=True)
class FlowStatus:
    """API 가 싣는 감지기 상태 (§3.6 `feed`·§3.7 `head`)."""

    connected: bool  # 소켓이 열려 있고 30초 안에 newHeads 를 받았다
    head: int | None  # 소켓이 알려준 최신 블록 번호
    last_block: int | None  # 마지막으로 처리한 블록
    last_block_ts: int | None  # 그 블록의 시각(초)
    deposit_addrs: int
    hot_wallets: int
    internal: int
    contracts: int
    late_logs: int  # 처리가 끝난 블록에 뒤늦게 와 따로 쓴 로그 수(API 에 싣지 않는다)


class FlowWriter(Protocol):
    """Influx 쓰기 자리 — core.influx.InfluxClient.write_lines 와 같은 시그니처."""

    def write_lines(
        self,
        lines: list[str],
        bucket: str | None = None,
        precision: Literal["s", "ns"] = "s",
    ) -> None: ...


class FlowStore(Protocol):
    """Redis 자리 — core.redis_bus.RedisBus 의 flow_* 네 메서드."""

    async def flow_last_block_load(self) -> int | None: ...

    async def flow_last_block_save(self, block: int) -> None: ...

    async def flow_set_load(self, key: str) -> set[str]: ...

    async def flow_set_add(self, key: str, addrs: Iterable[str]) -> None: ...


async def open_socket(url: str) -> Any:
    """실 소켓 열기. 테스트는 이 자리를 가짜 연결기로 바꾼다."""
    return await ws_connect(url, open_timeout=WS_OPEN_TIMEOUT)


class _HeadsSilent(Exception):
    """30초 newHeads 무수신 — 닫고 재연결한다 (§3.4)."""


class _BlockUnavailable(Exception):
    """시각을 모르는 블록의 HTTP 읽기 실패 — 점을 만들 수 없어 세션을 끊고 재생이 메우게 한다."""


def _topic_addr(topic: str) -> str:
    """indexed address 토픽(32바이트) → 주소 — 뒤 20바이트 (§3.3)."""
    return "0x" + topic[-40:].lower()


def _hex_int(value: object) -> int:
    return int(str(value), 16)


class EthFlowDetector:
    def __init__(
        self,
        *,
        ws_url: str,
        http_url: str,
        seeds: Seeds,
        writer: FlowWriter | None,
        bus: FlowStore | None,
        connect: Callable[[str], Awaitable[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], int] = _now_ms,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._ws_url = ws_url
        self._http_url = http_url
        self._deposit = set(seeds.deposit)
        self._hot = set(seeds.hot)
        self._internal = set(seeds.internal)
        self._contracts = dict(seeds.contracts)
        self._writer = writer
        self._bus = bus
        self._connect = connect
        self._sleep = sleep
        self._clock = clock
        self._http = http
        self._owns_http = http is None
        self._task: asyncio.Task[None] | None = None
        self._ws: Any | None = None
        self._connected = False
        self._backoff = BACKOFF_START
        self._silence_from: int | None = (
            None  # 무수신 감시 기준(ms) — 연결 시각 또는 마지막 newHeads
        )
        self._last_head_at: int | None = (
            None  # 마지막 newHeads 수신(ms) — 상태의 connected 가 본다
        )
        self._head: int | None = None
        self._last_block: int | None = None
        self._last_block_ts: int | None = None
        self._block_ts: dict[int, int] = {}
        self._buffered_logs: dict[
            int, list[dict[str, Any]]
        ] = {}  # 아직 처리하지 않은 블록의 로그 — 펌프가 바로 넣는다
        # 펌프 → 작업자: ("head", newHeads result) 와 ("late", 되돌림·처리 끝난 블록의 로그). 펌프는 I/O 를 하지 않는다
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._late_logs = 0
        self._pending: list[str] = []  # 이번 블록 회차에 쓸 줄
        self._unsent: list[
            str
        ] = []  # 실패한 줄 — 다음 회차에 같이 (상한 PENDING_LIMIT)

    # --- 수명 ---

    async def start(self) -> None:
        """Redis 추가분을 씨앗에 합치고 마지막 블록을 읽은 뒤 상시 태스크를 띄운다. Redis 실패는 경고 1줄 — 씨앗만으로 시작한다."""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC)
        if self._bus is not None:
            try:
                self._deposit |= await self._bus.flow_set_load(FLOW_DEPOSIT_ADDRS_KEY)
                self._hot |= await self._bus.flow_set_load(FLOW_HOT_WALLETS_KEY)
                self._internal |= await self._bus.flow_set_load(FLOW_INTERNAL_KEY)
                self._last_block = await self._bus.flow_last_block_load()
            except Exception as exc:
                logger.warning(
                    "Redis 에서 ETH 주소 집합·마지막 블록을 못 읽었다 — 씨앗만으로 시작한다: %r",
                    exc,
                )
        self._task = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, CLOSE_TIMEOUT)
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    def status(self) -> FlowStatus:
        fresh = (
            self._last_head_at is not None
            and self._clock() - self._last_head_at < HEADS_SILENCE_SEC * 1000
        )
        return FlowStatus(
            connected=self._connected and fresh,
            head=self._head,
            last_block=self._last_block,
            last_block_ts=self._last_block_ts,
            deposit_addrs=len(self._deposit),
            hot_wallets=len(self._hot),
            internal=len(self._internal),
            contracts=len(self._contracts),
            late_logs=self._late_logs,
        )

    # --- 연결 (§3.4) ---

    async def _run(self) -> None:
        while True:
            try:
                ws = await self._open()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "ETH 노드 연결 실패 — %.0f초 뒤 재시도: %r", self._backoff, exc
                )
                await self._sleep(self._backoff)
                self._backoff = min(self._backoff * 2, BACKOFF_MAX)
                continue
            self._ws = ws
            self._connected = True
            self._silence_from = self._clock()
            reason = ""
            try:
                await self._session(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = repr(exc)
            finally:
                self._connected = False
                self._ws = None
                with contextlib.suppress(Exception):
                    await ws.close()
            logger.warning(
                "ETH 노드 스트림이 끊겼다 — %.0f초 뒤 재연결: %s", self._backoff, reason
            )
            await self._sleep(self._backoff)
            self._backoff = min(self._backoff * 2, BACKOFF_MAX)

    async def _open(self) -> Any:
        if self._connect is not None:
            return await self._connect(self._ws_url)
        return await open_socket(self._ws_url)

    async def _session(self, ws: Any) -> None:
        """구독 둘 → 공백 재생(그동안 펌프가 받은 head·로그는 큐·버퍼에 쌓인다) → 작업자 시작 → 펌프·감시·작업자 중 먼저 끝나는 쪽까지.

        펌프는 수신·분류만 하고(I/O 없음) 작업자가 HTTP·Influx·Redis 를 한다 — 블록당 로그 수백 건을 수신 루프 안에서
        쓰면 head 를 30초 넘게 못 읽어 무수신으로 끊긴다(2026-10-08 실측).
        """
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": [
                        "logs",
                        {
                            "address": sorted(self._contracts),
                            "topics": [TRANSFER_TOPIC],
                        },
                    ],
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "eth_subscribe",
                    "params": ["newHeads"],
                }
            )
        )
        self._queue = asyncio.Queue()  # 지난 세션의 head 는 재생이 덮는다 — 새로 시작
        tasks = [
            asyncio.create_task(self._pump(ws)),
            asyncio.create_task(self._watch_heads()),
        ]
        try:
            await self._replay()
            tasks.append(asyncio.create_task(self._work()))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for finished in done:
                finished.result()  # 펌프의 끊김·작업자의 블록 읽기 실패는 여기서 예외로
            raise _HeadsSilent(
                f"{HEADS_SILENCE_SEC:.0f}초 안에 newHeads 가 없어 끊었다"
            )
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _watch_heads(self) -> None:
        """newHeads 가 30초 없으면 돌아온다 — 세션이 그것을 끊김으로 본다 (§3.4)."""
        while True:
            await self._sleep(HEADS_CHECK_SEC)
            if (
                self._silence_from is not None
                and self._clock() - self._silence_from >= HEADS_SILENCE_SEC * 1000
            ):
                return

    async def _pump(self, ws: Any) -> None:
        while True:
            raw = await ws.recv()
            if isinstance(raw, bytes | bytearray):
                try:
                    text = bytes(raw).decode("utf-8")
                except UnicodeDecodeError:
                    continue
            else:
                text = str(raw)
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("method") != "eth_subscription":
                if "error" in msg:
                    raise RuntimeError(f"구독 거부: {msg['error']}")
                continue  # 구독 응답(id·result) — 구독 id 는 쓰지 않는다, 프레임 모양으로 가른다
            result = (msg.get("params") or {}).get("result")
            if not isinstance(result, dict):
                continue
            if "topics" in result:
                self._route_log(result)
            elif "number" in result and "timestamp" in result:
                now = self._clock()
                self._silence_from = now
                self._last_head_at = now
                self._backoff = BACKOFF_START  # 첫 데이터 프레임에 초기화 (§3.4)
                number = _hex_int(result["number"])
                self._remember_ts(number, _hex_int(result["timestamp"]))
                if self._head is None or number > self._head:
                    self._head = number
                self._queue.put_nowait(("head", result))

    def _route_log(self, log: dict[str, Any]) -> None:
        """아직 처리하지 않은 블록의 로그는 버퍼에(그 블록 회차가 쓴다), 되돌림·처리 끝난 블록의 로그는 작업자 큐에."""
        number = _hex_int(log["blockNumber"])
        if not log.get("removed") and (
            self._last_block is None or number > self._last_block
        ):
            self._buffered_logs.setdefault(number, []).append(log)
            return
        self._queue.put_nowait(("late", log))

    async def _work(self) -> None:
        """작업자 — head 하나를 받으면 로그가 따라오길 기다렸다(BLOCK_SETTLE_SEC) 밀린 블록을 전부 한 회차씩 처리한다.

        기다리는 동안 큐에 더 들어온 head 는 다시 기다리지 않고 같은 묶음에 넣는다. 그 사이 들어온 늦은 로그는 묶음 뒤에.
        """
        while True:
            kind, item = await self._queue.get()
            if kind == "late":
                await self._on_log(item)
                continue
            target = _hex_int(item["number"])
            if self._last_block is not None and target <= self._last_block:
                continue  # 재생이 이미 덮은 블록
            await self._sleep(BLOCK_SETTLE_SEC)
            late: list[dict[str, Any]] = []
            while True:
                try:
                    kind, item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if kind == "head":
                    target = max(target, _hex_int(item["number"]))
                else:
                    late.append(item)
            if self._last_block is None:
                self._last_block = (
                    target - 1
                )  # 재생이 head 를 못 정한 기동(HTTP 거부) — 첫 head 부터
            # 놓친 newHeads 가 있어도 블록은 빠짐없이 — 시각은 HTTP 블록에서
            for block in range(self._last_block + 1, target + 1):
                await self._process_block(block)
            for log in late:
                await self._on_log(log)

    # --- 공백 재생 (§3.4) ---

    async def _replay(self) -> None:
        """연결 직후 — `head − last ≤ 7,200` 이면 20블록씩 eth_getLogs 로 재생, 넘거나 키가 없으면 head 부터.

        HTTP 상태 오류(403·429 — publicnode 는 eth_getLogs 를 막는다, 2026-10-08 실측)는 소켓과 무관하다 — 경고 1줄 후
        head 부터 시작하고 재연결하지 않는다. 네트워크 오류는 예외 그대로 → 세션 실패 → 백오프 재연결.
        """
        try:
            head = _hex_int(await self._rpc("eth_blockNumber", []))
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "ETH 공백 재생 불가(HTTP %d) — 첫 head 부터 시작한다",
                exc.response.status_code,
            )
            self._last_block = None
            return
        if self._head is None or head > self._head:
            self._head = head
        last = self._last_block
        if last is None:
            self._last_block = head
            return
        if last >= head:
            return
        if head - last > REPLAY_MAX_BLOCKS:
            logger.warning(
                "ETH 공백 %d 블록이 재생 상한 %d 을 넘는다 — head %d 부터 시작한다",
                head - last,
                REPLAY_MAX_BLOCKS,
                head,
            )
            self._last_block = head
            return
        logger.info("ETH 공백 재생 %d → %d (%d 블록)", last + 1, head, head - last)
        for start in range(last + 1, head + 1, REPLAY_CHUNK):
            end = min(start + REPLAY_CHUNK - 1, head)
            try:
                logs = await self._rpc(
                    "eth_getLogs",
                    [
                        {
                            "fromBlock": hex(start),
                            "toBlock": hex(end),
                            "address": sorted(self._contracts),
                            "topics": [TRANSFER_TOPIC],
                        }
                    ],
                )
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "ETH 공백 재생 중단(HTTP %d, 블록 %d~%d) — head %d 부터 시작한다",
                    exc.response.status_code,
                    start,
                    end,
                    head,
                )
                self._buffered_logs = {
                    k: v for k, v in self._buffered_logs.items() if k > head
                }
                self._last_block = head
                return
            for log in logs or []:
                if isinstance(log, dict) and not log.get("removed"):
                    self._buffered_logs.setdefault(
                        _hex_int(log["blockNumber"]), []
                    ).append(log)
            for number in range(start, end + 1):
                await self._process_block(number)

    # --- 블록 (§3.2~3.3) ---

    async def _process_block(self, number: int) -> None:
        """블록 1개 — HTTP 블록 1회(가스 지갑 수신자·시각) → 그 블록 로그 판정 → 쓰기 1회 → 마지막 블록 저장."""
        ts = self._block_ts.get(number)
        block = await self._fetch_block(number)
        if block is not None:
            if ts is None:
                ts = _hex_int(block["timestamp"])
                self._remember_ts(number, ts)
            await self._extend_from_gas(block)
        elif ts is None:
            # 실패해도 확장만 건너뛰는 것은 시각을 알 때뿐이다 — 시각이 없으면 점을 못 만들어 세션을 끊고 재생이 메운다
            raise _BlockUnavailable(f"블록 {number} 를 HTTP 로 읽지 못했다")
        logs = self._buffered_logs.pop(number, [])
        logs.sort(key=lambda log: _hex_int(log["logIndex"]))
        for log in logs:
            await self._apply_log(log, ts)
        await self._write_round()
        if self._last_block is None or number > self._last_block:
            self._last_block = number
            self._last_block_ts = ts
        await self._save_last_block(number)

    async def _on_log(self, log: dict[str, Any]) -> None:
        """작업자 — 리오그 되돌림, 또는 이미 처리한 블록에 늦게 온 로그 1건을 따로 쓴다. 시각은 표, 없으면 HTTP 블록 하나 (§3.3)."""
        number = _hex_int(log["blockNumber"])
        if not log.get("removed"):
            self._late_logs += 1
            if self._late_logs % LATE_LOG_EVERY == 0:
                logger.info(
                    "ETH 늦은 로그 누적 %d건 — 블록 처리 뒤에 온 로그는 건마다 따로 쓴다",
                    self._late_logs,
                )
        ts = self._block_ts.get(number)
        if ts is None:
            block = await self._fetch_block(number)
            if block is None:
                logger.warning(
                    "블록 %d 시각을 못 읽어 로그 1건을 버린다 (tx %s)",
                    number,
                    log.get("transactionHash"),
                )
                return
            ts = _hex_int(block["timestamp"])
            self._remember_ts(number, ts)
        await self._apply_log(log, ts)
        await self._write_round()

    def _remember_ts(self, number: int, ts: int) -> None:
        self._block_ts[number] = ts
        while len(self._block_ts) > _BLOCK_TS_KEEP:
            del self._block_ts[min(self._block_ts)]

    async def _apply_log(self, log: dict[str, Any], ts: int) -> None:
        """판정 규칙 (§3.3) — 입금·출금은 줄을 만들고, sweep·내부 이동은 집합만 넓힌다. 되돌린 로그는 집합을 건드리지 않는다."""
        topics = log.get("topics") or []
        if len(topics) < 3:
            return  # ERC-20 전송이 아니다
        contract = self._contracts.get(str(log.get("address", "")).lower())
        if contract is None:
            return
        amount_raw = _hex_int(log.get("data") or "0x0")
        if amount_raw == 0:
            return  # 금액 0 — 주소를 오염시키는 스캠 transferFrom
        sender = _topic_addr(topics[1])
        receiver = _topic_addr(topics[2])
        removed = bool(log.get("removed"))
        if sender in self._deposit:
            if not removed:
                await self._learn(FLOW_HOT_WALLETS_KEY, self._hot, receiver)  # sweep
            return
        if sender in self._hot:
            if (
                receiver in self._deposit
                or receiver in self._hot
                or receiver in self._internal
            ):
                if not removed:
                    await self._learn(FLOW_INTERNAL_KEY, self._internal, receiver)
                return
            direction, addr, counterparty = "out", sender, receiver
        elif receiver in self._deposit:
            direction, addr, counterparty = "in", receiver, sender
        else:
            return
        row = ChainFlowRow(
            dir=direction,
            symbol=contract.symbol,
            ts=ts,
            log_index=_hex_int(log["logIndex"]),
            amount=amount_raw / 10**contract.decimals,
            counterparty=counterparty,
            addr=addr,
            tx_hash=str(log.get("transactionHash", "")),
            block=_hex_int(log["blockNumber"]),
            removed=removed,
        )
        self._pending.append(chain_flow_line(row))

    async def _extend_from_gas(self, block: dict[str, Any]) -> None:
        """가스 지갑이 ETH 를 보낸 수신자 → 입금주소 (§3.2 자가 확장 1). 핫월렛·내부면 더하지 않는다."""
        for tx in block.get("transactions") or []:
            if not isinstance(tx, dict):
                continue
            if str(tx.get("from", "")).lower() != GAS_WALLET:
                continue
            to = tx.get("to")
            if not to or _hex_int(tx.get("value") or "0x0") == 0:
                continue
            receiver = str(to).lower()
            if receiver in self._hot or receiver in self._internal:
                continue
            await self._learn(FLOW_DEPOSIT_ADDRS_KEY, self._deposit, receiver)

    async def _learn(self, key: str, target: set[str], addr: str) -> None:
        """새 주소를 메모리 집합과 Redis 집합에 즉시 — Redis 실패는 경고 1줄(메모리에는 있다)."""
        if addr in target:
            return
        target.add(addr)
        if self._bus is None:
            return
        try:
            await self._bus.flow_set_add(key, [addr])
        except Exception as exc:
            logger.warning(
                "Redis 집합 %s 에 주소를 못 더했다(메모리에는 있다): %r", key, exc
            )

    # --- HTTP JSON-RPC ---

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        assert self._http is not None
        resp = await self._http.post(
            self._http_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict) or "error" in body:
            raise RuntimeError(f"{method} 실패: {body}")
        return body.get("result")

    async def _fetch_block(self, number: int) -> dict[str, Any] | None:
        """`eth_getBlockByNumber(번호, true)` — 실패는 None(경고 없음, §3.4)."""
        try:
            block = await self._rpc("eth_getBlockByNumber", [hex(number), True])
        except Exception:
            return None
        if not isinstance(block, dict):
            return None
        return block

    # --- 쓰기 (§3.5) ---

    async def _write_round(self) -> None:
        """미전송 + 이번 회차 줄을 쓰기 1번으로(스레드). 실패는 로그 1줄 후 미전송에 — 상한 10,000, 넘치면 오래된 것부터."""
        lines = self._unsent + self._pending
        self._pending = []
        self._unsent = []
        if not lines or self._writer is None:
            return
        try:
            await asyncio.to_thread(
                functools.partial(self._writer.write_lines, lines, precision="ns")
            )
        except Exception as exc:
            logger.warning(
                "chain_flow 쓰기 실패(%d점) — 다음 블록 회차에 재시도: %r",
                len(lines),
                exc,
            )
            overflow = len(lines) - PENDING_LIMIT
            if overflow > 0:
                logger.warning(
                    "chain_flow 미전송 %d점 초과 — 오래된 %d점 버림",
                    PENDING_LIMIT,
                    overflow,
                )
                lines = lines[overflow:]
            self._unsent = lines

    async def _save_last_block(self, number: int) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.flow_last_block_save(number)
        except Exception as exc:
            logger.warning("Redis flow:eth:last_block 저장 실패(%d): %r", number, exc)
