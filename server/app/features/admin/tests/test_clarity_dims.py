"""`/admin/clarity` 의 `dims` — 기기·유입 차원 호출의 목록 여섯·칸 둘의 기록·한쪽 실패 (스펙 068 §3.1·§3.2·§3.4·§4).

목록은 040 의 나라 목록 규칙이다 — 이름은 글자(주소 꼴은 버림), 세션은 정수, 세션 내림차순·같으면 이름순 20행, 이름 200자.
가짜 Data Export 의 기기(`dimension1=Device`)·유입(`dimension1=Channel`) 응답을 바꿔 가며 피드를 부른다.
"""

import json
import logging
from typing import Any

import pytest

from app.features.admin.tests.clarity_fakes import (
    CHANNEL_EXPORT,
    CHANNEL_LISTS,
    DEVICE_EXPORT,
    DEVICE_LISTS,
    DIMS_KEYS,
    NOW,
    Clarity,
    World,
    channel_row,
    device_row,
)


def traffic(*rows: Any) -> list[dict[str, Any]]:
    return [{"metricName": "Traffic", "information": list(rows)}]


async def dims_of(device: Any = None, channel: Any = None) -> dict[str, Any]:
    clarity = Clarity(
        device=() if device is None else ((200, device),),
        channel=() if channel is None else ((200, channel),),
    )
    return (await World(clarity).get())["dims"]


async def test_first_answer_lists_six_dimensions_in_one_part() -> None:
    w = World(Clarity())
    body = await w.get()
    assert body["dims"] == {
        "state": "ok",
        "code": None,
        "fetchedAt": int(NOW * 1000),
        "refreshSec": 86_400,
        **DEVICE_LISTS,
        **CHANNEL_LISTS,
    }
    assert list(body["dims"])[4:] == DIMS_KEYS
    assert list(body)[-2:] == ["pages", "dims"]


async def test_lists_keep_twenty_by_sessions_then_name_and_cut_long_names() -> None:
    rows = [device_row(i % 5, f"D{i:02d}", "B", "O") for i in range(25)]
    rows.append(device_row(3, "L" * 300, "B", "O"))
    got = await dims_of(device=traffic(*rows))
    names = [name for name, _ in got["device"]]
    assert len(got["device"]) == 20
    assert got["device"][:3] == [["D04", 4], ["D09", 4], ["D14", 4]]
    assert ["L" * 200, 3] in got["device"] and max(map(len, names)) == 200
    assert got["browser"] == [["B", 53]] and got["os"] == [["O", 53]]


@pytest.mark.parametrize("bad", ["2.5", 2.5, "abc", None, True, [3]])
async def test_sessions_take_integers_only_and_names_text_only(bad: Any) -> None:
    got = await dims_of(
        device=traffic(
            device_row(bad, "PC", "Chrome", "Windows"),
            device_row("4", "Mobile", "Safari", "iOS"),
            {"Device": 7, "Browser": None, "OS": "Linux", "totalSessionCount": 2},
        )
    )
    assert got["device"] == [["Mobile", 4]]  # 이름이 숫자인 행은 그 목록에서만 빠진다
    assert got["browser"] == [["Safari", 4]]
    assert got["os"] == [["iOS", 4], ["Linux", 2]]


async def test_address_like_names_are_dropped_and_never_stored() -> None:
    w = World(
        Clarity(
            channel=(
                (
                    200,
                    traffic(
                        channel_row(
                            5, "Referral", "https://evil.example/p?q=SECRET1", "Korea"
                        ),
                        channel_row(3, "Referral", "evil.example/x?SECRET2", "Korea"),
                        channel_row(2, "Referral", "news.example", "Korea"),
                    ),
                ),
            )
        )
    )
    body = await w.get()
    assert body["dims"]["source"] == [["news.example", 2]]
    assert body["dims"]["channel"] == [["Referral", 10]]
    stored = json.dumps(w.stored_dims("channel"))
    for text in (stored, json.dumps(body)):
        assert "SECRET" not in text and "evil" not in text


@pytest.mark.parametrize("key", ["Country/Region", "Country", "countryRegion"])
async def test_country_rows_read_any_spelling_of_the_dimension_key(key: str) -> None:
    rows = [
        {"Channel": "Direct", "Source": "x", key: "Japan", "totalSessionCount": "2"},
        {"Channel": "Direct", "Source": "x", key: "Korea", "totalSessionCount": 5},
    ]
    got = await dims_of(channel=traffic(*rows))
    assert got["country"] == [["Korea", 5], ["Japan", 2]]


async def test_no_traffic_metric_is_null_and_traffic_without_rows_is_empty() -> None:
    other = [{"metricName": "ScrollDepth", "information": [{"Device": "PC", "x": 1}]}]
    got = await dims_of(device=other, channel=traffic())
    assert (got["state"], got["device"], got["os"]) == ("ok", None, None)
    assert got["channel"] == got["source"] == got["country"] == []


async def test_rows_without_any_dimension_key_are_bad_data_and_keep_the_last_success() -> (
    None
):
    """차원 키 철자가 짐작과 다르면 값을 null 로 '성공' 하지 않는다 — 040 의 묶음 규칙과 같게 bad_data, 마지막 성공 그대로."""
    wrong = traffic({"Dev": "PC", "totalSessionCount": 3})
    w = World(Clarity(device=((200, DEVICE_EXPORT), (200, wrong))))
    ok = await w.get()
    w.advance(24)
    body = await w.get()
    assert (body["dims"]["state"], body["dims"]["code"]) == ("error", "bad_data")
    assert body["dims"]["device"] == ok["dims"]["device"] == DEVICE_LISTS["device"]
    assert body["state"] == body["pages"]["state"] == "ok"


async def test_a_failed_dims_call_marks_only_dims_and_keeps_that_slots_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """차원 호출이 실패하면 그 칸만 error — 기본·묶음과 다른 칸은 그대로(068 §3.4). 경과는 두 칸 가운데 이른 성공."""
    caplog.set_level(logging.WARNING, logger="marketlens.admin")
    changed = traffic(channel_row(9, "Social", "t.co", "Japan"))
    w = World(
        Clarity(
            device=((200, DEVICE_EXPORT), (500, {})),
            channel=((200, CHANNEL_EXPORT), (200, changed)),
        )
    )
    await w.get()
    w.advance(24)
    body = await w.get()
    dims = body["dims"]
    assert (dims["state"], dims["code"]) == ("error", "http_500")
    assert {k: dims[k] for k in ("device", "browser", "os")} == DEVICE_LISTS
    assert (dims["channel"], dims["country"]) == ([["Social", 9]], [["Japan", 9]])
    assert dims["fetchedAt"] == int(NOW * 1000)  # 기기 칸의 마지막 성공(24시간 전)
    assert (body["state"], body["pages"]["state"]) == ("ok", "ok")
    assert w.stored_dims("device")["code"] == "http_500"
    assert w.stored_dims("channel")["state"] == "ok"
    assert [r.getMessage() for r in caplog.records] == [
        "관리자 피드 clarity.dims.device 실패 — http_500"
    ]


async def test_the_dims_record_is_two_slots_of_the_040_shape_without_addresses() -> (
    None
):
    w = World(Clarity())
    await w.get()
    now_ms = int(NOW * 1000)
    for field, lists in (("device", DEVICE_LISTS), ("channel", CHANNEL_LISTS)):
        assert w.stored_dims(field) == {
            "attemptAt": now_ms,
            "state": "ok",
            "code": None,
            "successAt": now_ms,
            "values": lists,
        }


async def test_dims_values_drop_seven_days_after_their_last_success() -> None:
    w = World(Clarity(device=((200, DEVICE_EXPORT), (500, {}))))
    ok = await w.get()
    w.advance(7 * 24)  # 마지막 성공에서 정확히 7일 — 이번 시도는 실패, 값은 남는다
    assert (await w.get())["dims"]["device"] == ok["dims"]["device"]
    w.advance(1)
    body = await w.get()
    assert body["dims"]["device"] is None and body["dims"]["browser"] is None
    assert body["dims"]["channel"] == CHANNEL_LISTS["channel"]  # 유입 칸은 따로
    stored = w.stored_dims("device")
    assert stored["values"] is None and stored["successAt"] is None
