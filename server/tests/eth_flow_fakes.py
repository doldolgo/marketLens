"""이더리움 감지기 테스트 공용 도구 — 노드(WS·HTTP)·Influx·시계를 전부 가짜로 (스펙 050 §4). 네트워크 없음."""

import asyncio
import json
import re
import time
from collections.abc import Callable
from typing import Any

import fakeredis
import httpx

from app.core.eth_flow import (
    BLOCK_SETTLE_SEC,
    GAS_WALLET,
    HEADS_CHECK_SEC,
    TRANSFER_TOPIC,
    Contract,
    EthFlowDetector,
    Seeds,
)
from app.core.redis_bus import RedisBus
from tests.stream_fakes import Clock, FakeConnector, FakeSocket, Sleeps

T0 = 1_759_924_000  # 블록 시각 epoch 초
WS_URL = "wss://node.test/ws"
HTTP_URL = "https://node.test/rpc"

# 예시 주소 — 전부 가짜(홍길동 급 중립 값). 씨앗과 겹치지 않는다
USDT = "0x" + "a1" * 20
SAND = "0x" + "a2" * 20
DEPOSIT_A = "0x" + "d1" * 20
DEPOSIT_B = "0x" + "d2" * 20
HOT_A = "0x" + "e1" * 20
HOT_B = "0x" + "e2" * 20
INTERNAL_A = "0x" + "f1" * 20
OUTSIDER = "0x" + "99" * 20
NEWCOMER = "0x" + "88" * 20


def seeds() -> Seeds:
    return Seeds(
        deposit={DEPOSIT_A, DEPOSIT_B},
        hot={HOT_A, HOT_B},
        internal={INTERNAL_A},
        contracts={USDT: Contract("USDT", 6), SAND: Contract("SAND", 18)},
    )


def _topic(addr: str) -> str:
    return "0x" + addr[2:].rjust(64, "0")


def log_result(
    *,
    contract: str = SAND,
    sender: str,
    receiver: str,
    amount: int,
    block: int,
    log_index: int = 0,
    tx: str = "0x" + "77" * 32,
    removed: bool = False,
    topics: list[str] | None = None,
) -> dict[str, Any]:
    """eth_subscription(logs) 의 result 한 개 — `amount` 는 정수 최소 단위."""
    if topics is None:
        topics = [TRANSFER_TOPIC, _topic(sender), _topic(receiver)]
    return {
        "address": contract,
        "topics": topics,
        "data": hex(amount),
        "blockNumber": hex(block),
        "transactionHash": tx,
        "logIndex": hex(log_index),
        "removed": removed,
    }


def _notify(result: dict[str, Any]) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "eth_subscription",
            "params": {"subscription": "0xabc", "result": result},
        }
    )


def log_frame(**kw: Any) -> str:
    return _notify(log_result(**kw))


def head_frame(number: int, ts: int) -> str:
    return _notify({"number": hex(number), "timestamp": hex(ts), "hash": "0x00"})


def sub_ack(id_: int) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": "0xabc"})


def gas_tx(to: str, value: int = 10**15) -> dict[str, Any]:
    return {"from": GAS_WALLET, "to": to, "value": hex(value)}


class FakeNode:
    """HTTP JSON-RPC 가짜 — eth_blockNumber·eth_getLogs·eth_getBlockByNumber. 호출을 기록한다."""

    def __init__(self, head: int = 0) -> None:
        self.head = head
        self.blocks: dict[int, dict[str, Any]] = {}  # 번호 → {timestamp, transactions}
        self.logs: list[
            dict[str, Any]
        ] = []  # eth_getLogs 가 블록 범위로 거르는 전체 로그
        self.block_failures: set[int] = set()  # 이 블록의 eth_getBlockByNumber 는 실패
        self.get_logs_status: int | None = (
            None  # 있으면 eth_getLogs 가 이 HTTP 상태로 거부(publicnode 403)
        )
        self.block_gate: asyncio.Event | None = (
            None  # 있으면 블록 읽기가 set 될 때까지 멈춘다(느린 HTTP)
        )
        self.get_logs_calls: list[tuple[int, int]] = []
        self.block_calls: list[int] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def put_block(
        self, number: int, ts: int, txs: list[dict[str, Any]] | None = None
    ) -> None:
        self.blocks[number] = {
            "number": hex(number),
            "timestamp": hex(ts),
            "transactions": txs or [],
        }

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        if method == "eth_blockNumber":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": hex(self.head)}
            )
        if method == "eth_getLogs":
            lo, hi = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            self.get_logs_calls.append((lo, hi))
            if self.get_logs_status is not None:
                return httpx.Response(
                    self.get_logs_status,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32602, "message": "Request blocked"},
                    },
                )
            found = [lg for lg in self.logs if lo <= int(lg["blockNumber"], 16) <= hi]
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": found}
            )
        if method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            self.block_calls.append(number)
            if self.block_gate is not None:
                await self.block_gate.wait()
            if number in self.block_failures:
                return httpx.Response(503, text="down")
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": self.blocks.get(number)},
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601}}
        )


_FIELD = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|[^,]+)')


def parse_flow_line(line: str) -> dict[str, Any]:
    """`chain_flow` 줄 → {tag·field 이름: 값, "ts_ns": 시각}. 테스트 도구라 이 줄 모양만 다룬다."""
    rest, ts = line.rsplit(" ", 1)
    head, fields = rest.split(" ", 1)
    out: dict[str, Any] = {"ts_ns": int(ts)}
    measurement, *tags = head.split(",")
    out["measurement"] = measurement
    for tag in tags:
        k, v = tag.split("=", 1)
        out[k] = v
    for k, v in _FIELD.findall(fields):
        if v.startswith('"'):
            out[k] = json.loads(v)
        elif v.endswith("i"):
            out[k] = int(v[:-1])
        elif v in ("true", "false"):
            out[k] = v == "true"
        else:
            out[k] = float(v)
    return out


class FakeWriter:
    """Influx 쓰기 fake — `write_lines(lines, bucket, precision)` 호출을 그대로 남긴다. `fail` 이면 예외."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None, str]] = []
        self.fail = False

    def write_lines(
        self, lines: list[str], bucket: str | None = None, precision: str = "s"
    ) -> None:
        if self.fail:
            raise RuntimeError("쓰기 실패 (테스트)")
        self.calls.append((list(lines), bucket, precision))

    def points(self) -> list[dict[str, Any]]:
        return [parse_flow_line(line) for lines, _, _ in self.calls for line in lines]


class FlowSleeps(Sleeps):
    """무수신 감시 주기(5초)만 표로 막는다 — 가짜 sleep 이 즉시 돌아오면 감시 루프가 폭주한다. 백오프는 즉시.

    `gate_settle` 이면 블록 정착 대기(1.5초)도 표로 막는다 — head 뒤에 오는 로그가 같은 회차에 드는지 볼 때.
    """

    def __init__(self, gate_settle: bool = False) -> None:
        super().__init__()
        self._check = asyncio.Semaphore(0)
        self._settle = asyncio.Semaphore(0)
        self._gate_settle = gate_settle

    def release_check(self, n: int = 1) -> None:
        for _ in range(n):
            self._check.release()

    def release_settle(self, n: int = 1) -> None:
        for _ in range(n):
            self._settle.release()

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)
        if seconds == HEADS_CHECK_SEC:
            await self._check.acquire()
        elif seconds == BLOCK_SETTLE_SEC and self._gate_settle:
            await self._settle.acquire()
        else:
            await asyncio.sleep(0)

    def backoffs(self) -> list[float]:
        return [v for v in self.values if v not in (HEADS_CHECK_SEC, BLOCK_SETTLE_SEC)]


def make_bus() -> tuple[RedisBus, fakeredis.FakeRedis]:
    server = fakeredis.FakeServer()
    return RedisBus(fakeredis.aioredis.FakeRedis(server=server)), fakeredis.FakeRedis(
        server=server
    )


def build(
    outcomes: list[FakeSocket | BaseException],
    *,
    node: FakeNode | None = None,
    bus: RedisBus | None = None,
    writer: FakeWriter | None = None,
    sleeps: FlowSleeps | None = None,
) -> tuple[EthFlowDetector, FakeConnector, FakeNode, FakeWriter, FlowSleeps, Clock]:
    node = node if node is not None else FakeNode()
    writer = writer if writer is not None else FakeWriter()
    connector = FakeConnector(outcomes)
    sleeps = sleeps if sleeps is not None else FlowSleeps()
    clock = Clock(T0 * 1000)
    detector = EthFlowDetector(
        ws_url=WS_URL,
        http_url=HTTP_URL,
        seeds=seeds(),
        writer=writer,
        bus=bus,
        connect=connector,
        sleep=sleeps,
        clock=clock,
        http=node.client,
    )
    return detector, connector, node, writer, sleeps, clock


async def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    """`cond` 가 참이 될 때까지 짧게 양보하며 기다린다 — 루프 회전 수에 기대지 않는다(느린 CI 러너)."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise TimeoutError("조건이 제때 참이 되지 않았다 (테스트)")
        await asyncio.sleep(0.001)


async def run_until(
    detector: EthFlowDetector, cond: Callable[[], bool], timeout: float = 5.0
) -> None:
    """start → 조건이 참이 될 때까지(소켓은 열어 둔 채) → aclose. 작업자가 큐를 다 소화한 뒤에만 단언하게."""
    await detector.start()
    try:
        await wait_until(cond, timeout)
    finally:
        await detector.aclose()


async def run_until_exhausted(
    detector: EthFlowDetector, connector: FakeConnector
) -> None:
    """start → 소켓 결과를 다 쓰고 다음 연결을 영원히 기다리는 시점 → aclose."""
    await detector.start()
    await asyncio.wait_for(connector.exhausted.wait(), 2.0)
    await detector.aclose()
