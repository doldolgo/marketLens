"""흘려 읽는 조회 — streaks·bulk 의 `stream_premium` 과 `/history/events` 의 사건 목록 조회 (005 §3.4·013 §3.4, 2026-09-28).

influxdb-client 자리에 헤더 CSV 를 작은 조각으로 흘려 주는 가짜를 꽂는다 — 줄이 조각 경계에서 잘려도 같은 결과,
도중 실패는 저장소 실패, 다 읽은 연결만 풀에 돌려주는지를 본다. 네트워크 없음.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.core.influx as influx_mod
from app.core.influx import EventListRow, InfluxClient, InfluxUnavailableError

T0 = 1_700_000_000


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Resp:
    """urllib3 응답 자리 — 본문을 `chunk` 바이트씩 흘리고, 풀에 돌려줬는지·닫았는지 센다."""

    def __init__(self, text: str, chunk: int) -> None:
        self.data = text.encode()
        self.chunk = chunk
        self.released = 0
        self.closed = 0

    def stream(self, amt: int) -> object:
        return (
            self.data[i : i + self.chunk] for i in range(0, len(self.data), self.chunk)
        )

    def release_conn(self) -> None:
        self.released += 1

    def close(self) -> None:
        self.closed += 1


def _client(
    monkeypatch: pytest.MonkeyPatch, text: str, chunk: int = 3
) -> tuple[InfluxClient, _Resp, list[str]]:
    resp = _Resp(text, chunk)
    fluxes: list[str] = []

    def query_raw(flux: str, dialect: object) -> _Resp:
        fluxes.append(flux)
        return resp

    fake = SimpleNamespace(query_api=lambda: SimpleNamespace(query_raw=query_raw))
    monkeypatch.setattr(influx_mod, "InfluxDBClient", lambda **kw: fake)
    return InfluxClient(url="http://influx.test", token="t"), resp, fluxes


PREMIUM_CSV = (
    ",result,table,_time,_value,_field,base\r\n"
    f",_result,0,{_iso(T0)},0.5,fwd,BTC\r\n"
    f",_result,0,{_iso(T0 + 1)},-0.0000001,fwd,BTC\r\n"
    f",_result,1,{_iso(T0)},-0.6,rev,BTC\r\n"
    "\r\n"
    # 표 모양이 바뀌면 헤더가 다시 온다 — 열 순서가 달라도 이름으로 찾는다. 쉼표가 든 이름은 따옴표
    ",result,table,base,_field,_value,_time\r\n"
    f',_result,2,"A,B",fwd,1e-07,{_iso(T0 + 5)}\r\n'
    # 초 아래가 있는 시각은 잘라서 초로(FluxRecord 의 timestamp() 를 int 로 자른 것과 같다)
    ',_result,2,"A,B",fwd,2.5,2023-11-14T22:13:26.750Z\r\n'
    "\r\n"
)


def test_stream_premium_reads_series_rows_across_tiny_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for chunk in (1, 3, 64 * 1024):
        client, resp, fluxes = _client(monkeypatch, PREMIUM_CSV, chunk)
        got = list(
            client.stream_premium(
                dom="upbit", fx="binance", base=None, start=T0, stop=T0 + 60
            )
        )
        assert got == [
            ("BTC", "fwd", T0, 0.5),
            ("BTC", "fwd", T0 + 1, -1e-07),
            ("BTC", "rev", T0, -0.6),
            ("A,B", "fwd", T0 + 5, 1e-07),
            ("A,B", "fwd", T0 + 6, 2.5),
        ], chunk
        assert (resp.released, resp.closed) == (1, 0)  # 다 읽은 연결만 풀로
    flux = fluxes[0]
    # pivot·group·sort 없이 두 필드의 시리즈 표 그대로, 네 칸만
    assert "pivot(" not in flux and "group(" not in flux and "sort(" not in flux
    assert '(r._field == "fwd" or r._field == "rev")' in flux
    assert 'keep(columns: ["_time", "_value", "_field", "base"])' in flux


def test_stream_premium_error_table_midway_is_a_storage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (
        ",result,table,_time,_value,_field,base\r\n"
        f",_result,0,{_iso(T0)},0.5,fwd,BTC\r\n"
        "\r\n"
        ",error,reference\r\n"
        ",query exceeded memory limit,\r\n"
    )
    client, resp, _ = _client(monkeypatch, text)
    got = []
    with pytest.raises(InfluxUnavailableError):
        for point in client.stream_premium(
            dom="upbit", fx="binance", base="BTC", start=T0, stop=T0 + 60
        ):
            got.append(point)
    assert got == [("BTC", "fwd", T0, 0.5)]  # 받은 것은 호출자가 버린다(라우터는 503)
    assert (resp.released, resp.closed) == (0, 1)  # 덜 읽은 연결은 풀에 돌려주지 않는다


def test_stream_premium_cut_in_the_middle_of_a_line_is_a_storage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = f",result,table,_time,_value,_field,base\r\n,_result,0,{_iso(T0)},0.5,fw"
    client, resp, _ = _client(monkeypatch, text)
    with pytest.raises(InfluxUnavailableError):
        list(
            client.stream_premium(
                dom="upbit", fx="binance", base="BTC", start=T0, stop=T0 + 60
            )
        )
    assert resp.closed == 1


def test_stream_premium_closes_the_connection_when_the_reader_stops_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, resp, _ = _client(monkeypatch, PREMIUM_CSV)
    points = client.stream_premium(
        dom="upbit", fx="binance", base=None, start=T0, stop=T0 + 60
    )
    next(points)
    points.close()  # type: ignore[attr-defined]
    assert (resp.released, resp.closed) == (0, 1)


def test_events_list_query_reads_only_the_listed_fields_without_group_or_sort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = ",result,table,_time,base,dir,dom,fx,end_ts,last_ts,max_percent,max_ts,net_dom,net_fx,samples"
    text = (
        f"{head}\r\n"
        f",_result,0,{_iso(T0)},SOPH,kimp,upbit,binance,{T0 + 90},{T0 + 90},1.5,{T0 + 3},Ethereum,ERC20,91\r\n"
        # 반쪽 점(last_ts 없음) — 싣지 않는다
        f",_result,0,{_iso(T0 + 7)},SOPH,kimp,upbit,binance,0,,1.2,{T0},,,3\r\n"
        # 진행 중(end_ts 0), 쉼표가 든 망 이름, 망 없음 표식
        f',_result,1,{_iso(T0 + 9)},A.B,reverse,bithumb,bybit,0,{T0 + 70},2.25,{T0 + 20},"BNB Smart Chain, BEP20",-,50\r\n'
        "\r\n"
    )
    client, resp, fluxes = _client(monkeypatch, text, chunk=4)
    rows = client.query_premium_events(start=T0, stop=T0 + 100, dir="kimp")
    assert rows == [
        EventListRow(
            dom="upbit", fx="binance", base="SOPH", dir="kimp", start_ts=T0,
            end_ts=T0 + 90, max_percent=1.5, max_ts=T0 + 3, last_ts=T0 + 90,
            samples=91, net_dom="Ethereum", net_fx="ERC20",
        ),
        EventListRow(
            dom="bithumb", fx="bybit", base="A.B", dir="reverse", start_ts=T0 + 9,
            end_ts=0, max_percent=2.25, max_ts=T0 + 20, last_ts=T0 + 70,
            samples=50, net_dom="BNB Smart Chain, BEP20", net_fx=None,
        ),
    ]  # fmt: skip
    assert resp.released == 1
    flux = fluxes[0]
    assert "group(" not in flux and "sort(" not in flux and "pivot(" in flux
    fields = (
        "end_ts",
        "last_ts",
        "max_percent",
        "max_ts",
        "samples",
        "net_dom",
        "net_fx",
    )
    for f in fields:
        assert f'r._field == "{f}"' in flux
    # 응답에 쓰지 않는 필드는 읽지 않는다
    for f in ("duration_seconds", "enter_percent", "exit_percent"):
        assert f not in flux
    assert 'r.dir == "kimp"' in flux
