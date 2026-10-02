"""DB-IP 다시 받기 — 같은 달 0회·새 달 1회·500 은 1시간·404 는 24시간·성공하면 캐시된 회전 파일도 새 판으로 (스펙 039 §3.3·§3.5·§4 '다시 받기')."""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.features.admin.tests.access_fakes import (
    AFTER,
    CHROME,
    DAY,
    HOUR,
    Feeds,
    line,
    rotated,
    watch_opens,
    write,
)
from app.features.admin.tests.geo_fakes import KR_TELECOM, MONTH, DbIp, inline, url

NOV = "2026-11"
# 2026-11-02 01:30Z — 첫 판(10월)을 올린 때(AFTER)에서 20일 뒤
TO_NOV = datetime(2026, 11, 2, 1, 30, tzinfo=UTC).timestamp() - AFTER
T0 = AFTER - 2 * HOUR


async def loaded(tmp_path: Path, dbip: DbIp) -> Feeds:
    """10월 판을 올리고(첫 갱신) 다음 갱신이 캐시를 그 판으로 다시 만든 피드."""
    write(tmp_path, "access.log", [line(AFTER - 60, "/", ip=None)])  # 짝 없는 줄
    f = Feeds(tmp_path, AFTER, geo_transport=dbip.transport, geo_start=inline)
    assert (await f.get("30d"))["geo"]["state"] == "pending"
    f.t = 60
    geo = (await f.get("30d"))["geo"]
    assert (geo["state"], geo["month"]) == ("ok", MONTH)
    return f


async def test_the_same_month_is_not_fetched_again(tmp_path: Path) -> None:
    dbip = DbIp().serve()
    f = await loaded(tmp_path, dbip)
    for day in range(1, 15):  # 10-27 까지
        f.t = day * DAY
        assert (await f.get("30d"))["geo"]["month"] == MONTH
    assert (dbip.count("country"), dbip.count("asn")) == (1, 1)


@pytest.mark.parametrize(("status", "wait"), [(500, HOUR), (404, DAY)])
async def test_a_failed_new_month_keeps_the_old_table_and_waits(
    tmp_path: Path, status: int, wait: int
) -> None:
    dbip = DbIp().serve()
    f = await loaded(tmp_path, dbip)
    dbip.files[url("country", NOV)] = status
    f.t = TO_NOV
    geo = (await f.get("30d"))["geo"]
    assert (geo["state"], geo["code"], geo["month"]) == ("ok", None, MONTH)
    assert dbip.count("country", NOV) == 1
    # 창마다 60초 칸이 따로라 1초 차이의 두 갱신은 다른 창으로 부른다
    f.t = TO_NOV + wait - 1
    await f.get("7d")
    assert dbip.count("country", NOV) == 1  # 아직
    f.t = TO_NOV + wait
    await f.get("24h")
    assert dbip.count("country", NOV) == 2
    assert dbip.count("country", "2026-09") == 0  # 판이 있으면 지난달로 가지 않는다


async def test_a_new_table_recounts_the_cached_rotated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 같은 /24 를 10월 판은 JP, 11월 판은 KR 로 둔다 — 회전 파일은 캐시돼 있어 새 판이면 다시 읽어야 바뀐다
    pages = [line(T0 + i, "/", ua=f"{CHROME} v{i}", ip=KR_TELECOM) for i in range(3)]
    old = rotated(tmp_path, pages)
    opened: Counter[str] = watch_opens(monkeypatch)
    dbip = DbIp().serve(MONTH, {KR_TELECOM: ("JP", 4766, "KT")})
    dbip.serve(NOV, {KR_TELECOM: ("KR", 4766, "KT")})
    f = await loaded(tmp_path, dbip)
    geo = (await f.get("30d"))["geo"]
    assert geo["countries"] == [["JP", 0, 3]] and geo["networks"] == [["telecom", 0, 3]]
    reads = opened[old.name]
    f.t = TO_NOV
    geo = (await f.get("30d"))["geo"]
    assert (geo["month"], geo["countries"]) == (
        MONTH,
        [["JP", 0, 3]],
    )  # 갈아 끼운 회차는 옛 판으로 셌다
    f.t = TO_NOV + 60
    geo = (await f.get("30d"))["geo"]
    assert (geo["month"], geo["countries"]) == (NOV, [["KR", 0, 3]])
    assert geo["networks"] == [["telecom_kr", 0, 3]]
    assert opened[old.name] == reads + 1
