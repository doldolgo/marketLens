"""이더리움 ERC-20 입출금 감지기 — 판정 규칙·자가 확장·리오그·나노초 시각·쓰기 재시도·공백 재생·재연결 (스펙 050 §4). 네트워크 없음.

작업자(블록 처리·쓰기)는 펌프와 다른 태스크라 "몇 번 양보하면 끝났겠지" 로 단언하지 않는다 — 소켓을 열어 둔 채
`wait_until` 로 마지막 블록·쓰기 횟수·버퍼 같은 조건을 기다리고, 프레임 순서가 중요한 곳은 정착 대기를 표로 막는다.
"""

import logging

import pytest

from app.core import eth_flow
from app.core.eth_flow import HEADS_CHECK_SEC, HEADS_SILENCE_SEC, EthFlowDetector
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
    FlowSleeps,
    build,
    gas_tx,
    head_frame,
    log_frame,
    log_result,
    make_bus,
    run_until,
    run_until_exhausted,
    sub_ack,
    wait_until,
)
from tests.stream_fakes import FakeSocket, GatedSocket, until

B = 26_147_600  # 블록 번호


def node_with(head: int, *blocks: int) -> FakeNode:
    node = FakeNode(head=head)
    for n in blocks:
        node.put_block(n, T0 + (n - head) * 12)
    return node


def at_block(det: EthFlowDetector, block: int):  # noqa: ANN201 — 조건 함수
    """마지막 처리 블록이 `block` 에 닿았는가 — 그 블록의 로그 판정·쓰기 회차가 끝난 뒤에만 참이다."""
    return lambda: det.status().last_block == block


def buffered(det: EthFlowDetector, block: int, count: int):  # noqa: ANN201 — 조건 함수
    """펌프가 그 블록의 로그 `count` 건을 버퍼에 넣었는가(정착 대기를 풀기 전 확인용)."""
    return lambda: len(det._buffered_logs.get(block, [])) == count


async def gated(
    node: FakeNode,
) -> tuple[EthFlowDetector, GatedSocket, FakeNode, object, FlowSleeps, object]:
    """정착 대기를 표로 막는 감지기 + 열린 소켓 — 프레임을 밀어 넣고 버퍼를 확인한 뒤 `release_settle` 로 블록을 처리시킨다."""
    sock = GatedSocket()
    det, _, node, writer, sleeps, clock = build(
        [sock], node=node, sleeps=FlowSleeps(gate_settle=True)
    )
    await det.start()
    await until(sock.subscribed)
    return det, sock, node, writer, sleeps, clock


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
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node)
    await run_until(det, at_block(det, B + 1))
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
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node)
    await run_until(det, at_block(det, B + 1))
    (p,) = writer.points()
    assert (p["dir"], p["addr"], p["counterparty"]) == ("out", HOT_A, OUTSIDER)
    assert (
        det.status().internal == 1 + 2
    )  # HOT_B·DEPOSIT_B 가 내부에 더해졌다(INTERNAL_A 는 이미)


async def test_sweep_from_deposit_address_adds_hot_wallet_without_storing() -> None:
    node = node_with(B, B + 1, B + 2)
    sock = FakeSocket(
        [
            log_frame(sender=DEPOSIT_A, receiver=NEWCOMER, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
            # 새 핫월렛에서 밖으로 — 바로 출금으로 잡힌다
            log_frame(sender=NEWCOMER, receiver=OUTSIDER, amount=10**18, block=B + 2),
            head_frame(B + 2, T0 + 24),
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node)
    await run_until(det, at_block(det, B + 2))
    (p,) = writer.points()
    assert (p["dir"], p["addr"], p["block"]) == ("out", NEWCOMER, B + 2)
    assert det.status().hot_wallets == 3


async def test_gas_wallet_receivers_become_deposit_addresses_and_land_in_redis() -> (
    None
):
    bus, raw = make_bus()
    node = node_with(B)
    node.put_block(
        B + 1,
        T0 + 12,
        txs=[gas_tx(NEWCOMER), gas_tx(HOT_A), gas_tx(OUTSIDER, value=0)],
    )
    node.put_block(B + 2, T0 + 24)
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
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node, bus=bus)
    # Redis 의 마지막 블록 저장이 블록 회차의 맨 끝이다 — 그것이 보이면 집합 추가·쓰기도 끝났다
    await run_until(det, lambda: raw.get(FLOW_LAST_BLOCK_KEY) == str(B + 2).encode())
    assert det.status().deposit_addrs == 3  # 핫월렛·금액 0 수신자는 더하지 않는다
    assert {m.decode() for m in raw.smembers(FLOW_DEPOSIT_ADDRS_KEY)} == {NEWCOMER}
    assert raw.smembers(FLOW_HOT_WALLETS_KEY) == set()
    assert raw.smembers(FLOW_INTERNAL_KEY) == set()
    (p,) = writer.points()
    assert (p["dir"], p["symbol"], p["amount"], p["addr"]) == (
        "in",
        "USDT",
        1.0,
        NEWCOMER,
    )
    assert node.block_calls == [B + 1, B + 2]  # 블록마다 HTTP 1회


async def test_redis_additions_merge_into_seeds_on_start() -> None:
    bus, raw = make_bus()
    raw.sadd(FLOW_HOT_WALLETS_KEY, NEWCOMER)
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(sender=NEWCOMER, receiver=OUTSIDER, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node, bus=bus)
    await run_until(det, at_block(det, B + 1))
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
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node)
    await run_until(det, at_block(det, B + 1))
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
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node)
    # 블록 회차 1회 + 되돌림(늦은 경로) 1회 = 쓰기 2회
    await run_until(det, lambda: len(writer.calls) == 2)
    points = writer.points()
    assert len(points) == 2
    first, second = points[0], points[1]
    assert first["removed"] is False and second["removed"] is True
    for key in ("dir", "symbol", "exchange", "network", "ts_ns", "block", "log_index"):
        assert first[key] == second[key], key  # 같은 유일키


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
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node)
    await run_until(det, at_block(det, B + 1))
    a, b = writer.points()
    assert a["ts_ns"] == (T0 + 12) * 10**9 + 4 and b["ts_ns"] == (T0 + 12) * 10**9 + 9
    assert len(writer.calls) == 1  # 블록 단위로 묶어 쓰기 1회


async def test_late_log_after_its_head_uses_the_block_time_table() -> None:
    det, sock, node, writer, sleeps, _ = await gated(node_with(B, B + 1))
    sock.push(head_frame(B + 1, T0 + 12))
    sock.push(
        log_frame(
            sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1, log_index=2
        )
    )
    await wait_until(buffered(det, B + 1, 1))
    sleeps.release_settle()
    await wait_until(at_block(det, B + 1))
    await det.aclose()
    (p,) = writer.points()  # type: ignore[attr-defined]
    assert p["ts_ns"] == (T0 + 12) * 10**9 + 2
    assert node.block_calls == [B + 1]  # 시각 표에 있어 HTTP 를 더 부르지 않는다


# ── 쓰기 실패·상한 (§3.5) ───────────────────────────────────────────────────


async def test_write_failure_is_retried_with_the_next_block_round() -> None:
    det, sock, node, writer, sleeps, _ = await gated(node_with(B, B + 1, B + 2))
    writer.fail = True  # type: ignore[attr-defined]
    sock.push(
        log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1)
    )
    sock.push(head_frame(B + 1, T0 + 12))
    await wait_until(buffered(det, B + 1, 1))
    sleeps.release_settle()
    await wait_until(at_block(det, B + 1))  # 쓰기 실패 — 미전송에 남는다
    assert writer.calls == []  # type: ignore[attr-defined]
    writer.fail = False  # type: ignore[attr-defined]
    sock.push(
        log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 2)
    )
    sock.push(head_frame(B + 2, T0 + 24))
    await wait_until(buffered(det, B + 2, 1))
    sleeps.release_settle()
    await wait_until(at_block(det, B + 2))
    await det.aclose()
    assert len(writer.calls) == 1  # type: ignore[attr-defined]
    assert [p["block"] for p in writer.points()] == [B + 1, B + 2]  # type: ignore[attr-defined]


async def test_unsent_points_are_capped_dropping_the_oldest(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(eth_flow, "PENDING_LIMIT", 3)
    det, sock, node, writer, sleeps, _ = await gated(node_with(B, B + 1, B + 2))
    writer.fail = True  # type: ignore[attr-defined]
    with caplog.at_level(logging.WARNING, logger="marketlens.eth_flow"):
        for i in range(5):
            sock.push(
                log_frame(
                    sender=OUTSIDER,
                    receiver=DEPOSIT_A,
                    amount=(i + 1) * 10**18,
                    block=B + 1,
                    log_index=i,
                )
            )
        sock.push(head_frame(B + 1, T0 + 12))
        await wait_until(buffered(det, B + 1, 5))
        sleeps.release_settle()
        await wait_until(
            at_block(det, B + 1)
        )  # 5점 쓰기 실패 → 상한 3 — 오래된 2점 버림
        writer.fail = False  # type: ignore[attr-defined]
        sock.push(head_frame(B + 2, T0 + 24))
        sleeps.release_settle()
        await wait_until(at_block(det, B + 2))
        await det.aclose()
    assert [p["amount"] for p in writer.points()] == [3.0, 4.0, 5.0]  # type: ignore[attr-defined]
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
    sock = FakeSocket([sub_ack(1), sub_ack(2)], hold=True)
    det, _, node, writer, _, _ = build([sock], node=node, bus=bus)
    # 재생은 세션이 작업자보다 먼저 직접 돈다 — 마지막 블록의 Redis 저장이 재생의 끝이다
    await run_until(det, lambda: raw.get(FLOW_LAST_BLOCK_KEY) == str(B + 50).encode())
    assert node.get_logs_calls == [(B + 1, B + 20), (B + 21, B + 40), (B + 41, B + 50)]
    (p,) = writer.points()
    assert (p["block"], p["ts_ns"]) == (B + 30, (T0 + 30) * 10**9 + 5)
    assert det.status().last_block == B + 50


async def test_gap_over_limit_starts_from_head_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, raw = make_bus()
    raw.set(FLOW_LAST_BLOCK_KEY, str(B))
    node = FakeNode(head=B + 7_201)
    sock = FakeSocket([], hold=True)
    det, _, node, writer, _, _ = build([sock], node=node, bus=bus)
    with caplog.at_level(logging.WARNING, logger="marketlens.eth_flow"):
        await run_until(det, lambda: det.status().last_block == B + 7_201)
    assert node.get_logs_calls == [] and node.block_calls == []
    assert sum("재생 상한" in r.getMessage() for r in caplog.records) == 1


async def test_without_last_block_key_starts_from_head() -> None:
    bus, _ = make_bus()
    node = node_with(B, B + 1)
    sock = FakeSocket(
        [
            log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node, bus=bus)
    await run_until(det, at_block(det, B + 1))
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
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node, bus=bus)
    await run_until(det, at_block(det, B + 3))
    assert [p["block"] for p in writer.points()] == [B + 1, B + 3]


# ── 재연결 (§3.4) ───────────────────────────────────────────────────────────


def checks_slept(sleeps: FlowSleeps, n: int):  # noqa: ANN201 — 조건 함수
    """무수신 감시가 n 번째 대기에 들어갔는가 — 직전 검사에서 '아직 아니다' 로 판단했다는 뜻."""
    return lambda: sleeps.values.count(HEADS_CHECK_SEC) == n


async def test_silent_heads_for_thirty_seconds_reconnects() -> None:
    first, second = GatedSocket(), GatedSocket()
    node = node_with(B, B + 1)
    det, connector, node, writer, sleeps, clock = build([first, second], node=node)
    await det.start()
    await until(first.subscribed)
    first.push(head_frame(B + 1, T0))
    await wait_until(lambda: det.status().connected)
    clock.now += int(HEADS_SILENCE_SEC * 1000) - 1
    sleeps.release_check()
    await wait_until(checks_slept(sleeps, 2))  # 29.999초 — 검사가 그냥 지나갔다
    assert connector.urls == [WS_URL]
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
    # 첫 newHeads 가 백오프를 1초로 되돌린다(펌프가 그 자리에서) — 그 뒤 끊김은 1초, 다음 실패는 2초
    assert sleeps.backoffs() == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 1.0, 2.0]
    assert connector.urls == [WS_URL] * 10  # 9 결과 + 영원히 기다리는 10번째 시도


async def test_block_fetch_failure_only_skips_extension_when_time_is_known() -> None:
    node = node_with(B, B + 1)
    node.block_failures.add(B + 1)
    sock = FakeSocket(
        [
            log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1),
            head_frame(B + 1, T0 + 12),
        ],
        hold=True,
    )
    det, _, node, writer, _, _ = build([sock], node=node)
    await run_until(det, at_block(det, B + 1))
    assert [p["block"] for p in writer.points()] == [B + 1]


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


# ── 펌프·작업자 분리 (2026-10-08 실측 — 로그는 head 뒤에 온다) ────────────────


async def test_logs_arriving_after_their_head_go_into_the_block_single_write_round() -> (
    None
):
    det, sock, node, writer, sleeps, _ = await gated(node_with(B, B + 1))
    sock.push(head_frame(B + 1, T0 + 12))
    sock.push(
        log_frame(
            sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 1, log_index=1
        )
    )
    sock.push(
        log_frame(
            sender=OUTSIDER,
            receiver=DEPOSIT_B,
            amount=2 * 10**18,
            block=B + 1,
            log_index=2,
        )
    )
    await wait_until(buffered(det, B + 1, 2))
    assert writer.calls == []  # type: ignore[attr-defined] — 정착 대기 중, 아직 쓰지 않았다
    sleeps.release_settle()
    await wait_until(at_block(det, B + 1))
    await det.aclose()
    assert len(writer.calls) == 1  # type: ignore[attr-defined] — 블록 하나 = 쓰기 1회
    assert [p["log_index"] for p in writer.points()] == [1, 2]  # type: ignore[attr-defined]
    assert det.status().late_logs == 0


async def test_slow_block_fetch_does_not_stall_head_bookkeeping() -> None:
    import asyncio

    sock = GatedSocket()
    node = node_with(B, B + 1, B + 2)
    node.block_gate = asyncio.Event()
    det, connector, node, writer, sleeps, clock = build([sock], node=node)
    await det.start()
    await until(sock.subscribed)
    sock.push(head_frame(B + 1, T0 + 12))
    await wait_until(lambda: node.block_calls == [B + 1])  # 작업자가 HTTP 에 걸려 있다
    clock.now += int(HEADS_SILENCE_SEC * 1000) - 1000
    sock.push(head_frame(B + 2, T0 + 24))  # 펌프는 그래도 head 를 읽는다
    await until(sock.delivered)
    clock.now += 5_000
    sleeps.release_check()
    await wait_until(
        checks_slept(sleeps, 2)
    )  # 마지막 head 로부터 5초 — 무수신이 아니다
    assert connector.urls == [WS_URL] and det.status().connected
    node.block_gate.set()
    await wait_until(at_block(det, B + 2))
    assert sleeps.backoffs() == []
    await det.aclose()


async def test_late_log_after_block_processed_writes_once_and_counts(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(eth_flow, "LATE_LOG_EVERY", 1)
    sock = GatedSocket()
    node = node_with(B, B + 1)
    det, connector, node, writer, _, _ = build([sock], node=node)
    with caplog.at_level(logging.INFO, logger="marketlens.eth_flow"):
        await det.start()
        await until(sock.subscribed)
        sock.push(head_frame(B + 1, T0 + 12))
        await wait_until(at_block(det, B + 1))
        assert writer.calls == []  # 로그 없는 블록은 쓰기 요청이 없다
        sock.push(
            log_frame(
                sender=OUTSIDER,
                receiver=DEPOSIT_A,
                amount=10**18,
                block=B + 1,
                log_index=3,
            )
        )
        await wait_until(lambda: len(writer.calls) == 1)
    assert writer.points()[0]["ts_ns"] == (T0 + 12) * 10**9 + 3
    assert det.status().late_logs == 1
    assert any("늦은 로그 누적 1건" in r.getMessage() for r in caplog.records)
    await det.aclose()


async def test_http_status_error_in_replay_skips_to_head_without_reconnect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, raw = make_bus()
    raw.set(FLOW_LAST_BLOCK_KEY, str(B))
    node = node_with(B + 10, B + 11)
    node.get_logs_status = 403
    sock = FakeSocket(
        [
            head_frame(B + 11, T0 + 12),
            log_frame(sender=OUTSIDER, receiver=DEPOSIT_A, amount=10**18, block=B + 11),
        ],
        hold=True,
    )
    det, connector, node, writer, sleeps, _ = build([sock], node=node, bus=bus)
    with caplog.at_level(logging.WARNING, logger="marketlens.eth_flow"):
        await run_until(det, lambda: len(writer.calls) == 1)
    assert node.get_logs_calls == [(B + 1, B + 10)]  # 첫 묶음에서 거부 → 중단
    assert sum("재생 중단(HTTP 403" in r.getMessage() for r in caplog.records) == 1
    assert (
        connector.urls == [WS_URL] and sleeps.backoffs() == []
    )  # 재생 실패로는 끊지 않았다
    assert [p["block"] for p in writer.points()] == [B + 11]
    assert det.status().last_block == B + 11
