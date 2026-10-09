"""OKX instruments 매초 경로 (스펙 045 §3.3) — 맵을 만든 본문을 최근 2개까지 기억해도 기준 구현과 결과가 같은지.

OKX 는 같은 목록을 행 순서만 다른 두 본문으로 번갈아 준다(2026-10-09 캡처). 커넥터는 맵을 만든 200 본문을 최근 것부터
2개까지 기억하고, 바이트가 둘 중 하나와 같으면 파싱 없이 그 본문으로 만든 맵을 쓴다. 여기서는 9b13b2c 의 refresh·
set_universe(직전 본문 하나만 기억, 매 응답 디코드, 매번 배정 재계산)를 그대로 옮긴 대조용과 나란히 돌려
반환값·예외·맵(키 순서까지)·배정·행·원문 기록이 매 단계 같은지 본다.
"""

import json
import random
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.errors import ExchangeApiError, ExchangeTimeoutError
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.quotes import QuoteSink
from app.core.streams import okx
from app.core.streams.okx import INSTRUMENTS_PATH, OkxStream, shard_of
from tests.conftest import RawLog

REST_SOURCE = f"rest:{INSTRUMENTS_PATH}"
NOW = 1_787_727_947_000
NOW_DT = datetime.fromtimestamp(NOW / 1000, tz=UTC)


class _OkxBefore(OkxStream):
    """9b13b2c 의 refresh·set_universe 를 그대로 옮긴 대조용 — 직전에 맵을 만든 본문 하나만 기억한다."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self._symbols_body: bytes | None = None

    async def refresh(self, client: httpx.AsyncClient) -> int:
        url = okx.INSTRUMENTS_URL
        try:
            resp = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ExchangeTimeoutError(
                self.id, url, f"OKX 응답 시간 초과: {type(exc).__name__}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeApiError(
                self.id,
                url,
                f"OKX 연결 실패: {type(exc).__name__}: {exc}",
                kind="network",
            ) from exc
        self._record(
            self.id,
            f"rest:{okx.INSTRUMENTS_PATH}",
            self._clock(),
            resp.text,
            okx.SYMBOLS_KEY,
        )
        if resp.status_code == 200 and resp.content == self._symbols_body:
            return 1
        if resp.status_code != 200:
            raise ExchangeApiError(
                self.id,
                url,
                f"OKX 비-200 응답: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
                kind=okx._classify_rest_status(resp.status_code),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ExchangeApiError(
                self.id, url, f"OKX JSON 파싱 실패: {exc}", kind="bad_response"
            ) from exc
        if not isinstance(data, dict):
            raise ExchangeApiError(
                self.id, url, "OKX 응답이 객체가 아니다", body=resp.text
            )
        code = data.get("code")
        if code != okx._OK_CODE:
            raise ExchangeApiError(
                self.id,
                url,
                f"OKX code {code}: {data.get('msg', '')}",
                status_code=resp.status_code,
                body=resp.text,
                kind=okx._classify_rest_code(str(code)),
            )
        items = data.get("data")
        if not isinstance(items, list):
            raise ExchangeApiError(
                self.id, url, "OKX instruments 에 data 가 없다", body=resp.text
            )
        symbol_of: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("state") != okx._LIVE or item.get("quoteCcy") != okx._QUOTE:
                continue
            base = str(item.get("baseCcy") or "").upper()
            symbol = str(item.get("instId") or "")
            if not base or not symbol:
                continue
            symbol_of.setdefault(base, symbol)
        self._symbol_of = symbol_of
        self._base_of = {symbol: base for base, symbol in symbol_of.items()}
        self._symbols_body = resp.content
        return 1

    def set_universe(self, bases: set[str]) -> None:
        desired = {
            self._symbol_of[b]
            for b in (x.upper() for x in bases)
            if b in self._symbol_of
        }
        changed = False
        for shard in self._shards:
            mine = {s for s in desired if shard_of(s) == shard.index}
            if mine == shard.assigned:
                continue
            changed = True
            for symbol in shard.assigned - mine:
                base = self._base_of.get(symbol, symbol)
                self._store.remove_row(self.id, base)
                self._last_trade_ts.pop(base, None)
                self._book_ts.pop(symbol, None)
            shard.assigned = mine
            if mine:
                shard.has_work.set()
            else:
                shard.has_work.clear()
        if changed:
            self._wake.set()


# --- 본문 재료 ---


def _row(inst: str, base: str, state: str = "live", quote: str = "USDT") -> dict:  # type: ignore[type-arg]
    return {
        "instId": inst,
        "baseCcy": base,
        "quoteCcy": quote,
        "state": state,
        "instType": "SPOT",
    }


def _rows() -> list[dict]:  # type: ignore[type-arg]
    """여러 샤드에 걸친 USDT 심볼 + 걸러질 행들. FOO 는 instId 가 둘이라 순서가 바뀌면 맵의 값이 바뀐다."""
    rows = [_row(f"C{i:02d}-USDT", f"C{i:02d}") for i in range(14)]
    rows += [
        _row("FOO-USDT", "FOO"),
        _row("FOO2-USDT", "FOO"),  # 같은 base 의 둘째 — 처음 것이 이긴다
        _row("SUS-USDT", "SUS", state="suspend"),
        _row("PRE-USDT", "PRE", state="preopen"),
        _row("ETH-BTC", "ETH", quote="BTC"),
        _row("NOB-USDT", ""),  # 빈 baseCcy
        _row("low-usdt", "low"),  # 소문자 base 는 대문자로
    ]
    return rows


def _body(rows: list[dict], code: str = "0") -> bytes:  # type: ignore[type-arg]
    # 실제 응답처럼 ASCII 밖 글자를 둔다 — charset 에 따라 텍스트가 달라진다
    return json.dumps(
        {"code": code, "msg": "정상", "data": rows}, ensure_ascii=False
    ).encode("utf-8")


ROWS = _rows()
A = _body(ROWS)
B = _body(ROWS[::-1])  # 같은 목록, 행 순서만 반대 — FOO 의 값도 FOO2 로 바뀐다
C = _body(ROWS[1:])  # C00 이 빠진 목록
D = _body(ROWS[:5] + ROWS[7:])  # C05·C06 이 빠진 목록
LATIN = {"content-type": "application/json; charset=latin-1"}

POOL: list[tuple[str, Any]] = [
    ("A", lambda: httpx.Response(200, content=A)),
    ("A", lambda: httpx.Response(200, content=A)),
    ("B", lambda: httpx.Response(200, content=B)),
    ("B", lambda: httpx.Response(200, content=B)),
    ("C", lambda: httpx.Response(200, content=C)),
    ("D", lambda: httpx.Response(200, content=D)),
    ("A-latin", lambda: httpx.Response(200, content=A, headers=LATIN)),
    ("B-latin", lambda: httpx.Response(200, content=B, headers=LATIN)),
    ("A-429", lambda: httpx.Response(429, content=A)),  # 바이트는 A 여도 200 이 아니면 실패
    ("500", lambda: httpx.Response(500, content=b"oops")),
    ("code", lambda: httpx.Response(200, content=_body(ROWS, code="50011"))),
    ("html", lambda: httpx.Response(200, content=b"<html>busy</html>")),
    ("list", lambda: httpx.Response(200, content=b"[]")),
    ("nodata", lambda: httpx.Response(200, content=b'{"code":"0","msg":""}')),
]
ALL_BASES = sorted({str(r["baseCcy"]).upper() for r in ROWS if r["baseCcy"]}) + [
    "ZZZ"
]


def _client_for(make: Any) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if make is None:
            raise httpx.ReadTimeout("slow", request=request)
        return make()  # type: ignore[no-any-return]

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _world(cls: type[OkxStream]) -> tuple[OkxStream, LiveStore, RawLog]:
    store = LiveStore()
    sink = QuoteSink(store)
    sink.set_universe(set(ALL_BASES))
    raw = RawLog()
    ticks = iter(range(NOW, NOW + 1_000_000))
    stream = cls(store=store, sink=sink, record=raw, clock=lambda: next(ticks))
    return stream, store, raw


async def _outcome(stream: OkxStream, make: Any) -> tuple[Any, ...]:
    async with _client_for(make) as client:
        try:
            return ("ok", await stream.refresh(client))
        except (ExchangeApiError, ExchangeTimeoutError) as exc:
            return (
                type(exc).__name__,
                exc.kind,
                getattr(exc, "status_code", None),
                getattr(exc, "body", None),
                str(exc),
            )


def _state(stream: OkxStream, store: LiveStore) -> tuple[Any, ...]:
    shards = stream._shards
    return (
        list(stream._symbol_of.items()),  # 키 순서까지
        list(stream._base_of.items()),
        sorted(stream.bases()),
        [sorted(s.assigned) for s in shards],
        [s.has_work.is_set() for s in shards],
        stream._wake.is_set(),
        sorted((r.exchange, r.base) for r in store.get_all()),
    )


def _fill_rows(stream: OkxStream, store: LiveStore) -> None:
    """배정된 심볼마다 행을 둔다 — set_universe 가 빠진 심볼의 행을 지우는지 보이게."""
    for shard in stream._shards:
        for symbol in shard.assigned:
            base = stream._base_of.get(symbol, symbol)
            store.put_row(
                Row("okx", base, "USDT", symbol, 1.0, [[1.0, 1.0]], [[0.9, 1.0]], NOW),
                NOW_DT,
            )


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
async def test_random_rounds_match_the_reference_step_by_step(seed: int) -> None:
    rng = random.Random(seed)
    new, new_store, new_raw = _world(OkxStream)
    ref, ref_store, ref_raw = _world(_OkxBefore)
    for step in range(400):
        roll = rng.random()
        if roll < 0.6:
            name, make = rng.choice(POOL)
            got = await _outcome(new, make)
            want = await _outcome(ref, make)
        elif roll < 0.65:
            name = "timeout"
            got = await _outcome(new, None)
            want = await _outcome(ref, None)
        else:
            picked = {b for b in ALL_BASES if rng.random() < 0.7}
            if rng.random() < 0.2:
                picked = {b.lower() for b in picked}  # 대소문자만 다른 우주
            name = f"universe {len(picked)}"
            got = new.set_universe(picked)
            want = ref.set_universe(picked)
            _fill_rows(new, new_store)
            _fill_rows(ref, ref_store)
        assert got == want, (seed, step, name)
        assert _state(new, new_store) == _state(ref, ref_store), (seed, step, name)
        new._wake.clear()
        ref._wake.clear()
    # 원문 기록 — 다섯 인자(시각은 호출마다 1씩 오르는 시계)가 전부 같다. 텍스트는 같은 값의 str 이다
    assert new_raw.entries == ref_raw.entries
    assert all(type(e[3]) is str for e in new_raw.entries)


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    real = httpx.Response.json

    def counting(self: httpx.Response, **kw: Any) -> Any:
        calls[0] += 1
        return real(self, **kw)

    monkeypatch.setattr(httpx.Response, "json", counting)
    return calls


async def _run(stream: OkxStream, bodies: list[bytes]) -> list[list[tuple[str, str]]]:
    maps = []
    for body in bodies:
        async with _client_for(lambda b=body: httpx.Response(200, content=b)) as c:
            assert await stream.refresh(c) == 1
        maps.append(list(stream._symbol_of.items()))
    return maps


async def test_two_row_orders_alternating_are_parsed_once_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A·B 가 번갈아 와도 파싱은 처음 한 번씩 — 맵은 매번 그 본문을 파싱한 것과 같다(키 순서·FOO 값까지)."""
    order = [A, B, A, B, B, A, A, B, A]
    parses = _counting(monkeypatch)
    new, _, _ = _world(OkxStream)
    got = await _run(new, order)
    assert parses[0] == 2
    parses[0] = 0
    ref, _, _ = _world(_OkxBefore)
    want = await _run(ref, order)
    assert parses[0] == 7  # 기준은 순서가 바뀔 때마다 다시 파싱했다
    assert got == want
    assert dict(got[0])["FOO"] == "FOO-USDT" and dict(got[1])["FOO"] == "FOO2-USDT"


@pytest.mark.parametrize(
    ("order", "parsed"),
    [
        ([A, B, C, A], 4),  # 셋째(C)가 들어오며 A 는 잊힌다 — 기억은 최근 2개까지
        ([A, B, A, C, B], 4),  # A 를 다시 쓰면 A 가 최근이 되고, C 가 들어오며 B 가 잊힌다
        ([A, B, A, C, A], 3),  # 최근에 쓴 A 는 남는다
        ([C, A, B, C], 4),
    ],
)
async def test_only_the_two_most_recently_used_bodies_are_remembered(
    monkeypatch: pytest.MonkeyPatch, order: list[bytes], parsed: int
) -> None:
    parses = _counting(monkeypatch)
    new, _, new_raw = _world(OkxStream)
    got = await _run(new, order)
    assert parses[0] == parsed
    ref, _, ref_raw = _world(_OkxBefore)
    assert await _run(ref, order) == got
    assert new_raw.entries == ref_raw.entries


async def test_a_failed_body_between_two_orders_does_not_evict_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """맵을 못 만든 응답(code 실패·500·429)은 기억하지 않는다 — 앞서 기억한 두 본문이 그대로 남는다."""
    parses = _counting(monkeypatch)
    new, _, _ = _world(OkxStream)
    seq = [
        lambda: httpx.Response(200, content=A),
        lambda: httpx.Response(200, content=B),
        lambda: httpx.Response(200, content=_body(ROWS, code="50013")),
        lambda: httpx.Response(500, content=b"oops"),
        lambda: httpx.Response(429, content=B),
        lambda: httpx.Response(200, content=A),
        lambda: httpx.Response(200, content=B),
    ]
    outcomes = [(await _outcome(new, make))[0] for make in seq]
    fail = "ExchangeApiError"
    assert outcomes == ["ok", "ok", fail, fail, fail, "ok", "ok"]
    assert parses[0] == 3  # A·B·code 실패 본문 — 마지막 A·B 는 파싱하지 않는다
    assert dict(new._symbol_of)["FOO"] == "FOO2-USDT"  # 마지막이 B
