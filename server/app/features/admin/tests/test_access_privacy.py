"""접속 요약이 내지 않는 것 — 가린 IP·UA 원문·쿼리·짝 해시, 예외 문장의 IP·UA 는 로그·Slack 에도 없음 (스펙 038 §3.1·§3.5·§4)."""

import asyncio
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.notify import SlackLogHandler
from app.features.admin import access_hours
from app.features.admin.access_cache import AccessLog
from app.features.admin.access_pairs import pair_hash
from app.features.admin.tests.access_fakes import (
    AFTER,
    GATE,
    HOUR,
    IP,
    IPHONE,
    KAKAO,
    Feeds,
    line,
    rotated,
    write,
)
from app.main import create_app

KEY = b"\x07" * 16
SECRETS = ("fbclid", "SECRETCLICK", "gclid", "q=secret", "/deep/path")


@pytest.fixture
def slack() -> Iterator[list[str]]:
    """025 의 ERROR → Slack 처리기를 가짜 받는 곳에 단다."""
    sent: list[str] = []
    handler = SlackLogHandler(lambda key, text: sent.append(f"{key} {text}"))
    root = logging.getLogger()
    root.addHandler(handler)
    yield sent
    root.removeHandler(handler)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ROLE", "api")
    monkeypatch.setenv("INFLUX_TOKEN", "")
    monkeypatch.delenv("CLARITY_API_TOKEN", raising=False)
    get_settings.cache_clear()
    yield TestClient(create_app())
    get_settings.cache_clear()


def test_response_and_logs_hold_no_ip_user_agent_query_or_pair_hash(
    client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    t0 = AFTER - 2 * HOUR
    rotated(
        tmp_path, [line(t0 - HOUR, "/app/?tab=history&fbclid=SECRETCLICK", ua=KAKAO)]
    )
    write(
        tmp_path,
        "access.log",
        [
            line(
                t0,
                "/?utm_source=x&gclid=AD1",
                ua=IPHONE,
                referer="https://evil.example/deep/path?q=secret",
            ),
            line(t0 + 1, "/clarity.js", ua=IPHONE),
            line(t0 + 2, "/wp-login.php", ua="curl/8.7.1", status=500),
        ],
    )
    client.app.state.admin_visits = Feeds(
        tmp_path, AFTER, urandom=lambda n: KEY[:n]
    ).feeds
    for window in ("24h", "7d", "30d"):
        resp = client.get(f"/admin/access?window={window}")
        assert resp.json()["visitors"]["shaped"] == 2
        day_key = KEY + b"2026-10-13"
        hashes = [pair_hash(day_key, IP, ua) for ua in (IPHONE, KAKAO)]
        for text in (resp.text, caplog.text):
            for banned in (IP, IPHONE, KAKAO, "Mobile/15E148", "curl", *SECRETS):
                assert banned not in text, banned
            for h in hashes:
                assert h.hex() not in text and str(int.from_bytes(h)) not in text
        assert not any(h in resp.content for h in hashes)


@pytest.mark.parametrize("slow", [False, True])
async def test_parser_exceptions_leave_only_the_kind_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    slack: list[str],
    slow: bool,
) -> None:
    def leaky(raw: str) -> None:
        if slow:
            time.sleep(0.2)  # 3초(여기선 0.05초) 뒤 — 뒤에서 마저 도는 갱신의 예외
        raise ValueError(f"bad line from {IP} {IPHONE}")

    monkeypatch.setattr(access_hours, "_parse", leaky)
    caplog.set_level(logging.DEBUG)
    write(tmp_path, "access.log", [line(AFTER - 60, "/", ua=IPHONE)])
    f = Feeds(tmp_path, AFTER, wait_sec=0.05)
    first = await f.get("7d")
    if slow:
        assert first["state"] == "pending"
        await asyncio.sleep(0.4)
        first = await f.get("7d")
    assert (first["state"], first["code"]) == ("error", "ValueError")
    assert first["window"] == "7d" and first["totals"] is None
    records = [r for r in caplog.records if r.name == "marketlens.admin"]
    assert [(r.levelname, r.getMessage()) for r in records] == [
        ("WARNING", "관리자 피드 access 실패 — ValueError")
    ]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR] and slack == []
    for banned in (IP, IPHONE, "bad line"):
        assert banned not in caplog.text


def _strings(obj: Any, seen: set[int] | None = None) -> Iterator[str]:
    """객체에서 닿는 모든 글자 — dict 키·값, 목록, `__slots__`·`__dict__` 속성."""
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k, seen)
            yield from _strings(v, seen)
    elif isinstance(obj, list | tuple | set | frozenset):
        for v in obj:
            yield from _strings(v, seen)
    elif hasattr(obj, "__dict__") or hasattr(obj, "__slots__"):
        names = list(getattr(obj, "__dict__", {}))
        names += [n for c in type(obj).__mro__ for n in getattr(c, "__slots__", ())]
        for n in names:
            yield from _strings(getattr(obj, n, None), seen)


def test_a_broken_rotated_file_keeps_no_user_agent_or_referrer_memo(
    tmp_path: Path,
) -> None:
    # 꼬리(CRC·크기)를 자른 회전 gz — 읽다 EOFError 가 나도 그 결과째 캐시되므로 UA·출처 원문 메모는 버려져야 한다
    t0 = AFTER - 5 * HOUR
    lines = [
        line(t0 + i, "/", ua=f"{IPHONE} uniq{i}", referer="https://evil.example/")
        for i in range(50)
    ]
    whole = rotated(tmp_path, lines)
    whole.write_bytes(whole.read_bytes()[:-12])
    os.utime(whole, (t0 + 49, t0 + 49))
    write(tmp_path, "access.log", [line(AFTER - 60, "/", ua=KAKAO)])
    log = AccessLog(str(tmp_path), GATE)
    values, broken = log.summary("24h", AFTER)
    assert broken == ["EOFError"] and values["totals"]["skipped"] == 1
    assert values["visitors"]["shaped"] == 51  # 읽은 데까지는 센다
    tallies = [e.tally for e in log._files.values()] + [log._current]
    assert len(tallies) == 2
    for tally in tallies:
        assert (tally._kinds, tally._traits, tally._refs) == ({}, {}, {})
        assert tally._day[2] == b""  # 날 열쇠도 남기지 않는다
        found = [x for x in _strings(tally) if "uniq" in x or "iPhone" in x]
        assert found == []
