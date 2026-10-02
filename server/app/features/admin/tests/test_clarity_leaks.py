"""`/admin/clarity` 가 남기지 않는 것 — 묶음 호출의 주소·토큰, WARNING 은 부분 이름과 code 만 (스펙 040 §3.1·§3.4·§4 '새지 않음').

묶음 호출 가짜에만 표시 글자(`PGMARK`)를 든 주소를 써서, 묶음 기록·응답에 주소 조각이 없음을 바이트로 본다.
"""

import json
import logging
from typing import Any

import fakeredis
import pytest

from app.features.admin.tests.clarity_fakes import (
    EXPORT,
    TOKEN,
    Clarity,
    World,
    dims,
)


def metric(name: str, *rows: dict[str, Any]) -> dict[str, Any]:
    return {"metricName": name, "information": list(rows)}


def cell(page: str, device: str, sessions: int) -> dict[str, Any]:
    averages = (
        "scrollDepth",
        "totalSec",
        "activeSec",
        "deadClickPct",
        "rageClickPct",
        "excessiveScrollPct",
        "quickbackPct",
        "scriptErrorPct",
        "errorClickPct",
    )
    return {
        "page": page,
        "device": device,
        "sessions": sessions,
        **dict.fromkeys(averages),
    }


PG_EXPORT = [
    metric(
        "Traffic",
        dims("/app/?tab=gap&sym=PGMARK", "Mobile", totalSessionCount=2),
        dims(
            "https://kimptrack.com/app/?tab=gap&sym=PGMARK#x", "PC", totalSessionCount=1
        ),
        dims("/privacy?x=PGMARK", "PC", totalSessionCount=1),
    ),
    metric("PopularPages", dims("https://kimptrack.com/privacy?x=PGMARK", "PC")),
    metric("PageTitle", dims("/app/?tab=gap&sym=PGMARK", "PC", title="PGMARK 김프")),
]


async def test_page_addresses_are_never_kept(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    w = World(Clarity(pages=((200, PG_EXPORT),)))
    body = await w.get()
    assert body["pages"]["groups"] == [
        cell("app:gap", "mobile", 2),
        cell("app:gap", "desktop", 1),
        cell("privacy", "desktop", 1),
    ]
    raw = fakeredis.FakeRedis(server=w.server).get("admin:clarity:pages").decode()
    for text in (raw, json.dumps(body["pages"], ensure_ascii=False)):
        for leak in ("PGMARK", "kimptrack.com", "/app/", "/privacy"):
            assert leak not in text, leak
    assert "PGMARK" not in json.dumps(body, ensure_ascii=False)
    assert "PGMARK" not in caplog.text


async def test_token_stays_out_and_warnings_name_only_the_part_and_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    leak = {"message": f"bad token {TOKEN}"}
    w = World(Clarity((200, EXPORT), (401, leak), pages=((500, leak),)))
    first = await w.get()
    w.advance(4)
    second = await w.get()
    server = fakeredis.FakeRedis(server=w.server)
    texts = [
        json.dumps(first),
        json.dumps(second),
        server.get("admin:clarity").decode(),
        server.get("admin:clarity:pages").decode(),
        caplog.text,
    ]
    for text in texts:
        assert TOKEN not in text
    records = [r for r in caplog.records if r.name == "marketlens.admin"]
    assert [(r.levelname, r.getMessage()) for r in records] == [
        ("WARNING", "관리자 피드 clarity.pages 실패 — http_500"),
        ("WARNING", "관리자 피드 clarity 실패 — http_401"),
    ]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
