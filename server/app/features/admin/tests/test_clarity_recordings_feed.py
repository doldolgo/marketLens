"""Clarity 녹화 목록 피드 `/admin/clarity/recordings` (스펙 077 §3.2·§4 'server') — 상태·토큰·창마다 30분 캐시·경고.

바깥 경로는 가짜 전송(httpx.MockTransport)으로만 부른다.
"""

import json
import logging
from typing import Any

import httpx
import pytest

from app.features.admin import clarity_recordings as rec
from app.features.admin.tests.clarity_fakes import NOW, TOKEN
from app.features.admin.tests.test_clarity_recordings import PART, recording
from app.features.admin.visits import VisitFeeds


class Clarity:
    """녹화 경로 가짜 — 부른 요청을 모으고 정해 둔 답을 차례로 준다(마지막 답은 되풀이)."""

    def __init__(self, *answers: tuple[int, Any]) -> None:
        self.answers = list(answers) or [(200, [recording()])]
        self.requests: list[httpx.Request] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(body, BaseException):
            raise body
        content = body if isinstance(body, bytes) else json.dumps(body).encode()
        return httpx.Response(status, content=content)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


class World:
    def __init__(self, clarity: Clarity, *, token: str | None = TOKEN) -> None:
        self.t = 0.0
        self.clarity = clarity
        self.feeds = VisitFeeds(
            access_dir=None,
            clarity_token=token,
            transport=clarity.transport,
            clock=lambda: NOW + self.t,
            mono=lambda: self.t,
            wait_sec=2.0,
        )

    async def get(self, days: str | None = None) -> dict[str, Any]:
        return await self.feeds.clarity_recordings(days)


async def test_feed_answers_rows_with_the_window_and_sends_the_token_only_in_the_header() -> (
    None
):
    w = World(Clarity())
    body = await w.get("7")
    assert list(body) == PART
    assert body["state"] == "ok" and body["code"] is None
    assert body["fetchedAt"] == int(NOW * 1000) and body["refreshSec"] == 1800
    assert body["days"] == 7 and len(body["rows"]) == 1
    (request,) = w.clarity.requests
    assert request.method == "POST" and str(request.url) == rec.RECORDINGS_URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    sent = json.loads(request.content)
    assert sent["count"] == 100 and sent["sortBy"] == 0
    assert sent["filters"]["date"] == {"start": sent["start"], "end": sent["end"]}
    assert TOKEN not in json.dumps(body)


async def test_no_token_is_unconfigured_and_never_calls() -> None:
    w = World(Clarity(), token=None)
    body = await w.get()
    assert body == {
        "state": "unconfigured",
        "code": None,
        "fetchedAt": None,
        "refreshSec": 1800,
        "rows": None,
        "days": 3,
    }
    assert w.clarity.requests == []


async def test_a_window_is_called_once_per_thirty_minutes() -> None:
    w = World(Clarity())
    await w.get("3")
    await w.get("3")
    await w.get(None)  # 기본도 3일 — 같은 칸
    w.t = 1799
    await w.get("3")
    assert len(w.clarity.requests) == 1
    await w.get("1")  # 다른 창은 따로
    assert len(w.clarity.requests) == 2
    w.t = 1800
    await w.get("3")
    assert len(w.clarity.requests) == 3


@pytest.mark.parametrize(
    ("answer", "state", "code"),
    [
        ((401, b""), "denied", "http_401"),
        ((403, b"{}"), "denied", "http_403"),
        ((429, b"too many"), "error", "http_429"),
        ((500, b""), "error", "http_500"),
        ((200, b"<html>"), "error", "bad_data"),
        ((200, b'{"a": 1}'), "error", "bad_data"),
        ((200, httpx.ReadTimeout("slow")), "error", "timeout"),
        ((200, httpx.ConnectError(f"refused {TOKEN}")), "error", "ConnectError"),
    ],
)
async def test_failures_have_a_state_and_code_without_values(
    answer: tuple[int, Any], state: str, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    w = World(Clarity(answer))
    with caplog.at_level(logging.WARNING):
        body = await w.get()
    assert (body["state"], body["code"], body["rows"], body["fetchedAt"]) == (
        state,
        code,
        None,
        None,
    )
    assert body["days"] == 3
    assert "관리자 피드 clarity.recordings 실패" in caplog.text
    assert TOKEN not in caplog.text and TOKEN not in json.dumps(body)


async def test_warning_is_one_line_per_ten_minutes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w = World(Clarity((500, b"")))
    with caplog.at_level(logging.WARNING):
        await w.get("1")
        await w.get("7")  # 다른 창도 같은 이름 — 10분 안이라 줄이 없다
        w.t = 601
        await w.get("30")
    lines = [r for r in caplog.records if "clarity.recordings" in r.getMessage()]
    assert len(lines) == 2 and len(w.clarity.requests) == 3
