"""원문 아카이브 성능 개선(기록 참조 튜플·열린 창 기억·정렬을 스레드로·트리 없는 JSON 검사)의 동작 불변 — 스펙 010 §3.4·§3.5.

바뀐 것은 구현뿐이라 S3 객체(키·gzip 본문 바이트)는 기준과 같아야 한다. 여기서는 바뀐 경로가 만드는 출력을
고정 기대값과, JSON 검사는 기준 구현(값 트리를 만드는 판정)과 대조한다.
"""

import gzip
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest

from app.core import raw_archive
from app.core.raw_archive import pack
from tests.test_raw_archive import (
    MINUTE,
    NEXT,
    OB_BTC,
    T0,
    WS,
    build,
    lines_of,
    settled,
)

OKX = "ws:/ws/v5/public"


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def tree_reference(payload: str) -> bool:
    """기준 구현(9b13b2c)의 판정 그대로 — 값 트리를 만들어 최상위 종류를 본다."""
    if "\n" in payload or "\r" in payload:
        return False
    try:
        value = json.loads(payload, parse_constant=_reject_constant)
    except ValueError:
        return False
    return isinstance(value, dict | list)


def outcome(judge: Callable[[str], bool], payload: str) -> bool | str:
    """판정 결과, 예외면 예외 종류 이름 — 기준이 예외를 밖으로 내는 입력은 같은 예외여야 한다."""
    try:
        return judge(payload)
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


# --- JSON 검사 (§3.4 조건 ①~③) ---


@pytest.mark.parametrize(
    ("payload", "verbatim"),
    [
        ('{"a":1}', True),
        (' {"a":1}', True),  # 앞 공백 — 파싱은 되고 최상위는 객체다
        ("\t[1]", True),
        (' \t {"a":[{}]}', True),
        ("{} ", True),
        ("{}\t", True),
        ('{"a":1,"a":2}', True),  # 중복 키 — 표준 파서가 받는다
        ("[1e400]", True),  # 범위를 넘는 실수도 기준처럼 받는다
        ('{"a":"\\ud800"}', True),  # 짝 없는 서로게이트 이스케이프
        ('{"a": 1.50, "b":[1.0, 2.00e3]}', True),
        ("true", False),  # 스칼라 JSON
        ("null", False),
        (" 1", False),
        (' "s"', False),
        ('"{}"', False),
        ("1.5", False),
        ('{"a":[1,{"b":NaN}]}', False),  # 객체 안 깊이 든 비표준 상수
        ("[-Infinity]", False),
        ("", False),
        (" ", False),
        ("﻿{}", False),  # BOM 은 표준 파서가 거부한다
        ('{"a":1}x', False),  # 꼬리 쓰레기
        ('{"a":1', False),
        ("<html><body>502</body></html>", False),
        ('{\n "pretty": 1\n}', False),
        ("[" + "9" * 5000 + "]", False),  # 정수 자릿수 한도(4,300) — 기준도 ValueError
    ],
)
def test_verbatim_judgment_is_fixed_and_matches_the_tree_building_reference(
    payload: str, verbatim: bool
) -> None:
    assert raw_archive._is_verbatim_json(payload) is verbatim
    assert tree_reference(payload) is verbatim


def _first_recursion_depth(make: Callable[[int], str]) -> int | None:
    """기준 판정이 RecursionError 를 내기 시작하는 가장 얕은 중첩 깊이 — 환경(스택 깊이)마다 다르므로 찾는다."""
    lo, hi = 1, 200_000
    if outcome(tree_reference, make(hi)) != "RecursionError":
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if outcome(tree_reference, make(mid)) == "RecursionError":
            hi = mid
        else:
            lo = mid + 1
    return lo


@pytest.mark.parametrize(
    "make",
    [
        lambda d: '{"a":' * d + "1" + "}" * d,
        lambda d: '[{"a":' * (d // 2) + "1" + "}]" * (d // 2),
        lambda d: "[" * d + "]" * d,
    ],
    ids=["objects", "objects-in-arrays", "arrays"],
)
def test_deep_nesting_near_the_recursion_limit_gives_the_reference_outcome(
    make: Callable[[int], str],
) -> None:
    """훅은 객체가 끝날 때 파이썬 함수를 한 번 더 불러 재귀 한도 직전(약 1만 단)에서 먼저 넘친다 —
    그 깊이에서도 기준과 같은 판정(넘치면 같은 RecursionError)이어야 한다."""
    edge = _first_recursion_depth(make)
    depths = [1, 2, 50, 1000] if edge is None else range(max(1, edge - 8), edge + 3)
    for depth in depths:
        payload = make(depth)
        assert outcome(raw_archive._is_verbatim_json, payload) == outcome(
            tree_reference, payload
        ), depth


# --- 분 창 버퍼와 열린 창 기억 (§3.5) ---


async def test_closed_object_lines_are_fixed_for_mixed_keyed_and_plain_records() -> None:
    """같은 receivedAt 이 keyed·plain 에 섞이고 keyed 가 교체돼도 줄 순서 = (receivedAt, 기록 순) — 바이트로 고정."""
    archive, s3, clock = build()
    records: list[tuple[int, str, str | None]] = [
        (T0 + 5, '{"k":1}', "orderbook:BTC-USDT"),  # 4번째 기록이 교체한다
        (T0 + 5, "[1]", None),
        (T0 + 3, '{"k":2}', "tickers:BTC-USDT"),  # 7번째 기록이 교체한다
        (T0 + 5, '{"k":3}', "orderbook:BTC-USDT"),
        (T0 + 3, "pong", None),  # JSON 이 아니다 — 문자열로 감싼다
        (T0 + 9, ' {"x": 1.50}', None),  # 앞 공백이 있는 JSON — 바이트 그대로 붙는다
        (T0 + 3, '{"k":4}', "tickers:BTC-USDT"),
        (T0 + 9, "true", "status:all"),  # 스칼라 JSON — 문자열로 감싼다
    ]
    for at, payload, key in records:
        archive.record("okx", OKX, at, payload, key)
    assert archive.buffered("okx") == 6
    clock.now = NEXT
    assert await archive.run_once() == 1
    await settled(lambda: archive.pending == 0)
    [(key, body)] = s3.puts
    head = '{"exchange":"okx","source":"ws:/ws/v5/public","receivedAt":'
    expected = [
        f'{head}{T0 + 3},"raw":"pong"}}\n',
        f'{head}{T0 + 3},"raw":{{"k":4}}}}\n',
        f'{head}{T0 + 5},"raw":[1]}}\n',
        f'{head}{T0 + 5},"raw":{{"k":3}}}}\n',
        f'{head}{T0 + 9},"raw": {{"x": 1.50}}}}\n',
        f'{head}{T0 + 9},"raw":"true"}}\n',
    ]
    assert key == "raw/exchange=okx/dt=2026-09-05/hh=09/20260905T092000Z.jsonl.gz"
    assert gzip.decompress(body) == "".join(expected).encode()
    assert body == pack([ln.encode() for ln in expected])
    await archive.aclose()


async def test_late_line_for_an_already_closed_window_is_uploaded_not_lost() -> None:
    """닫힌 창을 열린 창 기억이 계속 가리키면 늦게 온 원문이 떼어 낸 버퍼에 붙어 사라진다 —
    시계가 뒤로 간 경우 그 창은 새 버퍼로 다시 열려 다음 회차에 같은 키로 올라가야 한다 (§3.5)."""
    archive, s3, clock = build()
    archive.record("upbit", WS, T0, '{"n":0}', OB_BTC)
    clock.now = NEXT
    assert await archive.run_once() == 1
    archive.record("upbit", WS, T0 + 1, '{"n":1}', OB_BTC)  # 닫힌 창에 늦게 온 원문
    assert archive.buffered("upbit") == 1
    assert await archive.run_once() == 1
    await settled(lambda: len(s3.puts) == 2)
    assert s3.puts[0][0] == s3.puts[1][0]
    assert [[json.loads(ln)["receivedAt"] for ln in lines_of(b)] for _, b in s3.puts] == [
        [T0],
        [T0 + 1],
    ]
    await archive.aclose()


async def test_record_after_an_emptied_buffer_was_dropped_lands_in_a_live_buffer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """기록이 예외로 끝나 빈 버퍼만 남고(해시 안 되는 key) 회차가 그 빈 버퍼를 지운 뒤에도,
    같은 창의 다음 원문은 살아 있는 버퍼에 붙어 올라가야 한다."""
    archive, s3, clock = build()
    with caplog.at_level(logging.ERROR, logger="marketlens.raw_archive"):
        archive.record("bithumb", WS, T0, "{}", ["unhashable"])  # type: ignore[arg-type]
    assert [r.getMessage() for r in caplog.records] == [
        f"원문 기록 중 예외 — 이 원문은 버린다 bithumb {WS}"
    ]
    assert await archive.run_once() == 0  # 같은 창 — 빈 버퍼만 지운다
    archive.record("bithumb", WS, T0 + 2, '{"n":2}')
    assert archive.buffered("bithumb") == 1
    clock.now = NEXT
    assert await archive.run_once() == 1
    await settled(lambda: archive.pending == 0)
    [(_, body)] = s3.puts
    assert [json.loads(ln)["raw"] for ln in lines_of(body)] == [{"n": 2}]
    await archive.aclose()


async def test_exchange_alternating_between_two_open_windows_keeps_each_line_in_its_window() -> (
    None
):
    """다음 분의 첫 원문이 닫기 회차보다 먼저 와 창 둘이 열린 사이 두 창을 번갈아 써도, 원문은 자기 창으로 간다.
    앞 창을 닫아도 다음 창의 기억은 그대로라 그 뒤 기록도 같은 버퍼에 붙는다."""
    archive, s3, clock = build()
    archive.record("upbit", WS, T0, '{"w":0,"i":0}')
    archive.record("upbit", WS, NEXT + 1, '{"w":1,"i":0}')
    archive.record("bithumb", WS, T0 + 1, '{"b":0}')
    archive.record("upbit", WS, T0 + 2, '{"w":0,"i":1}', OB_BTC)
    archive.record("upbit", WS, NEXT + 3, '{"w":1,"i":1}', OB_BTC)
    archive.record("upbit", WS, T0 + 4, '{"w":0,"i":2}', OB_BTC)  # 같은 창의 같은 키 — 앞 것을 교체
    assert archive.buffered("upbit") == 4
    clock.now = NEXT
    assert await archive.run_once() == 2  # 앞 창(upbit·bithumb)만
    archive.record("upbit", WS, NEXT + 5, '{"w":1,"i":2}')
    clock.now = NEXT + MINUTE
    assert await archive.run_once() == 1
    await settled(lambda: len(s3.puts) == 3)
    got = {
        key[-17:] + key.split("/")[1]: [json.loads(ln)["raw"] for ln in lines_of(body)]
        for key, body in s3.puts
    }
    assert got == {
        "T092000Z.jsonl.gzexchange=bithumb": [{"b": 0}],
        "T092000Z.jsonl.gzexchange=upbit": [{"w": 0, "i": 0}, {"w": 0, "i": 2}],
        "T092100Z.jsonl.gzexchange=upbit": [
            {"w": 1, "i": 0},
            {"w": 1, "i": 1},
            {"w": 1, "i": 2},
        ],
    }
    # 열린 창 기억은 버퍼 목록에 살아 있는 버퍼만 가리킨다(떼어 낸 버퍼를 가리키면 늦은 원문을 잃는다)
    live = [id(buf) for buf in archive._buffers.values()]
    assert all(id(buf) in live for _, buf in archive._open.values())
    await archive.aclose()
    assert archive._buffers == {} and archive._open == {}
