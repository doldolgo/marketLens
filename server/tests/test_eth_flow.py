"""이더리움 ERC-20 입출금 감지기 — 판정 규칙·자가 확장·리오그·나노초 시각·쓰기 재시도·공백 재생·재연결 (스펙 050 §4). 네트워크 없음."""

import asyncio
import logging

import pytest

from app.core import eth_flow
from app.core.eth_flow import HEADS_SILENCE_SEC, EthFlowDetector
from app.core.redis_bus import (
    FLOW_DEPOSIT_ADDRS_KEY,
    FLOW_HOT_WALLETS_KEY,
    FLOW_INTERNAL_KEY,
    FLOW_LAST_BLOCK_KEY,
)
from tests.eth_flow_fakes import (
    DEPOSIT_A,
    DEPOSIT_B,
    HOT_A,
    HOT_B,
    HTTP_URL,
    INTERNAL_A,
    NEWCOMER,
    OUTSIDER,
    SAND,
    T0,
    USDT,
    WS_URL,
    FakeNode,
    build,
    gas_tx,
    head_frame,
    log_frame,
    log_result,
    make_bus,
    run_until_exhausted,
    sub_ack,
)
from tests.stream_fakes import FakeSocket, GatedSocket, until

B = 26_147_600  # 블록 번호


def node_with(head: int, *blocks: int) -> FakeNode:
    node = FakeNode(head=head)
    for n in blocks:
        node.put_block(n, T0 + (n - head) * 12)
    return node


# ── 판정 규칙 (§3.3) ─────────────────────────────────────────────────────────


async def test_transfer_to_deposit_address_is_stored_as_in() -> None:
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            sub_ack(1),
            sub_ack(2),
            log_frame(
                sender=OUTSIDER,
                receiver=DEPOSIT_A,
                amount=5 * 10**18,
                block=B + 1,
                log_index=7,
            ),
            head_frame(B + 1, T0 + 12),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    (p,) = writer.points()
    assert (p["dir"], p["symbol"], p["addr"], p["counterparty"]) == (
        "in",
        "SAND",
        DEPOSIT_A,
        OUTSIDER,
    )
    assert (p["amount"], p["block"], p["log_index"], p["removed"]) == (
        5.0,
        B + 1,
        7,
        False,
    )
    assert p["exchange"] == "upbit" and p["network"] == "eth"
    assert writer.calls[0][2] == "ns"


async def test_hot_wallet_to_outside_is_out_and_to_own_addresses_grows_internal() -> (
    None
):
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(
                sender=HOT_A, receiver=OUTSIDER, amount=10**18, block=B + 1, log_index=1
            ),
            log_frame(
                sender=HOT_A, receiver=HOT_B, amount=10**18, block=B + 1, log_index=2
            ),
            log_frame(
                sender=HOT_A,
                receiver=INTERNAL_A,
                amount=10**18,
                block=B + 1,
                log_index=3,
            ),
            log_frame(
                sender=HOT_A,
                receiver=DEPOSIT_B,
                amount=10**18,
                block=B + 1,
                log_index=4,
            ),
            head_frame(B + 1, T0 + 12),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    (p,) = writer.points()
    assert (p["dir"], p["addr"], p["counterparty"]) == ("out", HOT_A, OUTSIDER)
    status = det.status()
    assert (
        status.internal == 1 + 2
    )  # HOT_B·DEPOSIT_B 가 내부에 더해졌다(INTERNAL_A 는 이미)


async def test_sweep_from_deposit_address_adds_hot_wallet_without_storing() -> None:
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(sender=DEPOSIT_A, receiver=NEWCOMER, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
            # 새 핫월렛에서 밖으로 — 바로 출금으로 잡힌다
            log_frame(sender=NEWCOMER, receiver=OUTSIDER, amount=10**18, block=B + 2),
            head_frame(B + 2, T0 + 24),
        ]
    )
    node.put_block(B + 2, T0 + 24)
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    (p,) = writer.points()
    assert (p["dir"], p["addr"], p["block"]) == ("out", NEWCOMER, B + 2)
    assert det.status().hot_wallets == 3


async def test_gas_wallet_receivers_become_deposit_addresses_and_land_in_redis() -> (
    None
):
    bus, raw = make_bus()
    node = node_with(B)
    node.put_block(
        B + 1, T0 + 12, txs=[gas_tx(NEWCOMER), gas_tx(HOT_A), gas_tx(OUTSIDER, value=0)]
    )
    sock = FakeSocket(
        [
            head_frame(B + 1, T0 + 12),
            # 같은 세션 안의 다음 블록 — 방금 배운 입금주소로의 전송이 입금이다
            log_frame(
                sender=OUTSIDER,
                receiver=NEWCOMER,
                amount=10**6,
                contract=USDT,
                block=B + 2,
            ),
            head_frame(B + 2, T0 + 24),
        ]
    )
    node.put_block(B + 2, T0 + 24)
    det, connector, node, writer, _, _ = build([sock], node=node, bus=bus)
    await run_until_exhausted(det, connector)
    assert det.status().deposit_addrs == 3  # 핫월렛·금액 0 수신자는 더하지 않는다
    assert {m.decode() for m in raw.smembers(FLOW_DEPOSIT_ADDRS_KEY)} == {NEWCOMER}
    assert (
        raw.smembers(FLOW_HOT_WALLETS_KEY) == set()
        and raw.smembers(FLOW_INTERNAL_KEY) == set()
    )
    (p,) = writer.points()
    assert (p["dir"], p["symbol"], p["amount"], p["addr"]) == (
        "in",
        "USDT",
        1.0,
        NEWCOMER,
    )
    assert raw.get(FLOW_LAST_BLOCK_KEY) == str(B + 2).encode()
    assert node.block_calls == [B + 1, B + 2]  # 블록마다 HTTP 1회


async def test_redis_additions_merge_into_seeds_on_start() -> None:
    bus, raw = make_bus()
    raw.sadd(FLOW_HOT_WALLETS_KEY, NEWCOMER)
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(sender=NEWCOMER, receiver=OUTSIDER, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node, bus=bus)
    await run_until_exhausted(det, connector)
    assert det.status().hot_wallets == 3
    assert [p["dir"] for p in writer.points()] == ["out"]


async def test_logs_with_fewer_than_three_topics_or_zero_amount_are_dropped() -> None:
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(
                sender=OUTSIDER,
                receiver=DEPOSIT_A,
                amount=10**18,
                block=B + 1,
                topics=[eth_flow.TRANSFER_TOPIC],
            ),
            log_frame(
                sender=OUTSIDER, receiver=DEPOSIT_A, amount=0, block=B + 1, log_index=1
            ),
            # 금액 0 의 sweep 모양도 집합을 넓히지 않는다
            log_frame(
                sender=DEPOSIT_A, receiver=NEWCOMER, amount=0, block=B + 1, log_index=2
            ),
            head_frame(B + 1, T0 + 12),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    assert writer.points() == []
    assert det.status().hot_wallets == 2


async def test_removed_log_overwrites_the_same_point_with_removed_true() -> None:
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(
                sender=OUTSIDER,
                receiver=DEPOSIT_A,
                amount=10**18,
                block=B + 1,
                log_index=3,
            ),
            head_frame(B + 1, T0 + 12),
            log_frame(
                sender=OUTSIDER,
                receiver=DEPOSIT_A,
                amount=10**18,
                block=B + 1,
                log_index=3,
                removed=True,
            ),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    first, second = writer.points()
    assert first["removed"] is False and second["removed"] is True
    same = ("dir", "symbol", "exchange", "network", "ts_ns")
    assert [first[k] for k in same] == [second[k] for k in same]  # 같은 유일키


async def test_time_is_block_ts_nanoseconds_plus_log_index_so_same_block_points_differ() -> (
    None
):
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(
                sender=OUTSIDER,
                receiver=DEPOSIT_A,
                amount=10**18,
                block=B + 1,
                log_index=4,
            ),
            log_frame(
                sender=OUTSIDER,
                receiver=DEPOSIT_A,
                amount=2 * 10**18,
                block=B + 1,
                log_index=9,
            ),
            head_frame(B + 1, T0 + 12),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    a, b = writer.points()
    assert a["ts_ns"] == (T0 + 12) * 10**9 + 4 and b["ts_ns"] == (T0 + 12) * 10**9 + 9
    assert len(writer.calls) == 1  # 블록 단위로 묶어 쓰기 1회


async def test_late_log_after_its_head_uses_the_block_time_table() -> None:
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            head_frame(B + 1, T0 + 12),
            log_frame(
                sender=OUTSIDER,
                receiver=DEPOSIT_A,
                amount=10**18,
                block=B + 1,
                log_index=2,
            ),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    (p,) = writer.points()
    assert p["ts_ns"] == (T0 + 12) * 10**9 + 2
    assert node.block_calls == [B + 1]  # 시각 표에 있어 HTTP 를 더 부르지 않는다


# ── 쓰기 실패·상한 (§3.5) ───────────────────────────────────────────────────


async def test_write_failure_is_retried_with_the_next_block_round() -> None:
    node = node_with(B, B + 1, B + 2)
    sock = FakeSocket(
        [
            log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
            log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 2),
            head_frame(B + 2, T0 + 24),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    writer.fail = True
    await det.start()
    await asyncio.wait_for(_written_blocks(det, B + 1), 2.0)
    writer.fail = False
    await asyncio.wait_for(connector.exhausted.wait(), 2.0)
    await det.aclose()
    assert len(writer.calls) == 1
    assert [p["block"] for p in writer.points()] == [B + 1, B + 2]


async def _written_blocks(det: EthFlowDetector, block: int) -> None:
    while det.status().last_block is None or det.status().last_block < block:
        await asyncio.sleep(0)


async def test_unsent_points_are_capped_dropping_the_oldest(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(eth_flow, "PENDING_LIMIT", 3)
    node = node_with(B, B + 1, B + 2)
    sock = FakeSocket(
        [
            *[
                log_frame(
                    sender=OUTSIDER,
                    receiver=DEPOSIT_A,
                    amount=(i + 1) * 10**18,
                    block=B + 1,
                    log_index=i,
                )
                for i in range(5)
            ],
            head_frame(B + 1, T0 + 12),
            head_frame(B + 2, T0 + 24),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    writer.fail = True
    with caplog.at_level(logging.WARNING, logger="marketlens.eth_flow"):
        await det.start()
        await asyncio.wait_for(_written_blocks(det, B + 1), 2.0)
        writer.fail = False
        await asyncio.wait_for(connector.exhausted.wait(), 2.0)
        await det.aclose()
    assert [p["amount"] for p in writer.points()] == [3.0, 4.0, 5.0]
    assert any("오래된 2점 버림" in r.getMessage() for r in caplog.records)


# ── 공백 재생 (§3.4) ─────────────────────────────────────────────────────────


async def test_gap_within_limit_is_replayed_in_chunks_of_twenty() -> None:
    bus, raw = make_bus()
    raw.set(FLOW_LAST_BLOCK_KEY, str(B))
    node = FakeNode(head=B + 50)
    for n in range(B + 1, B + 51):
        node.put_block(n, T0 + n - B)
    node.logs.append(
        log_result(
            sender=OUTSIDER,
            receiver=DEPOSIT_A,
            amount=10**18,
            block=B + 30,
            log_index=5,
        )
    )
    sock = FakeSocket([sub_ack(1), sub_ack(2)])
    det, connector, node, writer, _, _ = build([sock], node=node, bus=bus)
    await run_until_exhausted(det, connector)
    assert node.get_logs_calls == [(B + 1, B + 20), (B + 21, B + 40), (B + 41, B + 50)]
    (p,) = writer.points()
    assert (p["block"], p["ts_ns"]) == (B + 30, (T0 + 30) * 10**9 + 5)
    assert det.status().last_block == B + 50
    assert raw.get(FLOW_LAST_BLOCK_KEY) == str(B + 50).encode()


async def test_gap_over_limit_starts_from_head_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, raw = make_bus()
    raw.set(FLOW_LAST_BLOCK_KEY, str(B))
    node = FakeNode(head=B + 7_201)
    sock = FakeSocket([])
    det, connector, node, writer, _, _ = build([sock], node=node, bus=bus)
    with caplog.at_level(logging.WARNING, logger="marketlens.eth_flow"):
        await run_until_exhausted(det, connector)
    assert node.get_logs_calls == [] and node.block_calls == []
    assert det.status().last_block == B + 7_201
    assert sum("재생 상한" in r.getMessage() for r in caplog.records) == 1


async def test_without_last_block_key_starts_from_head() -> None:
    bus, _ = make_bus()
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node, bus=bus)
    await run_until_exhausted(det, connector)
    assert node.get_logs_calls == []
    assert [p["block"] for p in writer.points()] == [B + 1]


async def test_live_frames_during_replay_are_queued_then_processed_in_order() -> None:
    bus, raw = make_bus()
    raw.set(FLOW_LAST_BLOCK_KEY, str(B))
    node = FakeNode(head=B + 2)
    for n in (B + 1, B + 2, B + 3):
        node.put_block(n, T0 + n - B)
    node.logs.append(
        log_result(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1)
    )
    sock = FakeSocket(
        [
            log_frame(
                sender=OUTSIDER, receiver=DEPOSIT_A, amount=3 * 10**18, block=B + 3
            ),
            head_frame(B + 3, T0 + 3),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node, bus=bus)
    await run_until_exhausted(det, connector)
    assert [p["block"] for p in writer.points()] == [B + 1, B + 3]
    assert det.status().last_block == B + 3


# ── 재연결 (§3.4) ───────────────────────────────────────────────────────────


async def test_silent_heads_for_thirty_seconds_reconnects() -> None:
    first, second = GatedSocket(), GatedSocket()
    node = FakeNode(head=B)
    det, connector, node, writer, sleeps, clock = build([first, second], node=node)
    await det.start()
    await until(first.subscribed)
    first.push(head_frame(B + 1, T0))
    node.put_block(B + 1, T0)
    await until(first.delivered)
    await asyncio.sleep(0.01)
    assert det.status().connected
    clock.now += int(HEADS_SILENCE_SEC * 1000) - 1
    sleeps.release_check()
    await asyncio.sleep(0.01)
    assert connector.urls == [WS_URL]  # 29.999초 — 아직
    clock.now += 1
    sleeps.release_check()
    await until(second.subscribed)
    assert first.closed and connector.urls == [WS_URL, WS_URL]
    assert not det.status().connected  # 새 소켓은 아직 newHeads 를 못 받았다
    assert sleeps.backoffs() == [1.0]
    await det.aclose()


async def test_backoff_doubles_from_one_to_thirty_seconds_and_resets_on_first_head() -> (
    None
):
    node = node_with(B, B + 1)
    sock = FakeSocket([head_frame(B + 1, T0 + 12)])
    outcomes: list[FakeSocket | BaseException] = [OSError("refused")] * 7 + [
        sock,
        OSError("refused"),
    ]
    det, connector, node, writer, sleeps, clock = build(outcomes, node=node)
    await run_until_exhausted(det, connector)
    # 첫 newHeads 가 백오프를 1초로 되돌린다 — 그 뒤 끊김은 1초, 다음 실패는 2초
    assert sleeps.backoffs() == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 1.0, 2.0]
    assert connector.urls == [WS_URL] * 10  # 9 결과 + 영원히 기다리는 10번째 시도


async def test_block_fetch_failure_only_skips_extension_when_time_is_known() -> None:
    node = node_with(B, B + 1)
    node.block_failures.add(B + 1)
    sock = FakeSocket(
        [
            log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
        ]
    )
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    assert [p["block"] for p in writer.points()] == [B + 1]
    assert det.status().last_block == B + 1


async def test_http_url_is_used_for_rpc_and_subscriptions_are_two() -> None:
    node = node_with(B)
    sock = FakeSocket([])
    det, connector, node, writer, _, _ = build([sock], node=node)
    await run_until_exhausted(det, connector)
    subs = sock.subscriptions()
    assert [m["method"] for m in subs] == ["eth_subscribe", "eth_subscribe"]
    assert subs[0]["params"][0] == "logs"
    assert subs[0]["params"][1]["topics"] == [eth_flow.TRANSFER_TOPIC]
    assert set(subs[0]["params"][1]["address"]) == {USDT, SAND}
    assert subs[1]["params"] == ["newHeads"]
    assert det.status().contracts == 2
    assert HTTP_URL.startswith("https://")
