"""GET /flow/netflow·/flow/recent — 창·정렬·원화·feed·confirmed·인자 오류·503 (스펙 050 §3.6·§3.7·§4). 네트워크 없음."""

import logging
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.eth_flow import FlowStatus
from app.core.influx import ChainFlowAgg, ChainFlowRow, InfluxUnavailableError
from app.core.live_store import LiveStore
from app.features.history.tests.helpers import make_client as history_client
from app.main import create_app
from tests.conftest import make_row
from tests.test_raw_archive import _boot

NOW = int(time.time())
HEAD = 26_147_699
ADDR = "0x" + "4b" * 20
OTHER = "0x" + "21" * 20
TX = "0x" + "7d" * 32


class FakeFlowReader:
    """core.influx.InfluxClient 의 chain_flow 조회 둘 — 점 목록을 들고 `removed` 를 뺀 채 접거나 거른다."""

    def __init__(self) -> None:
        self.rows: list[ChainFlowRow] = []
        self.fail = False
        self.netflow_calls: list[tuple[int, int]] = []
        self.recent_calls: list[dict[str, object]] = []

    def seed(
        self,
        dir: str,
        symbol: str,
        amount: float,
        *,
        ts: int,
        block: int = HEAD,
        log_index: int = 0,
        removed: bool = False,
    ) -> None:
        self.rows.append(
            ChainFlowRow(
                dir=dir,
                symbol=symbol,
                ts=ts,
                log_index=log_index,
                amount=amount,
                counterparty=OTHER,
                addr=ADDR,
                tx_hash=TX,
                block=block,
                removed=removed,
            )
        )

    def _live(self, start: int, stop: int) -> list[ChainFlowRow]:
        if self.fail:
            raise InfluxUnavailableError("연결 실패 (테스트)")
        return [r for r in self.rows if not r.removed and start <= r.ts < stop]

    def query_chain_flow_netflow(self, *, start: int, stop: int) -> list[ChainFlowAgg]:
        self.netflow_calls.append((start, stop))
        groups: dict[tuple[str, str], list[ChainFlowRow]] = {}
        for r in self._live(start, stop):
            groups.setdefault((r.symbol, r.dir), []).append(r)
        return [
            ChainFlowAgg(
                symbol=s,
                dir=d,
                count=len(rs),
                amount=sum(r.amount for r in rs),
                last_ts=max(r.ts for r in rs),
            )
            for (s, d), rs in groups.items()
        ]

    def query_chain_flow_recent(
        self,
        *,
        start: int,
        stop: int,
        limit: int,
        dir: str | None = None,
        symbol: str | None = None,
    ) -> list[ChainFlowRow]:
        self.recent_calls.append(
            {"start": start, "stop": stop, "limit": limit, "dir": dir, "symbol": symbol}
        )
        rows = [
            r
            for r in self._live(start, stop)
            if (dir is None or r.dir == dir) and (symbol is None or r.symbol == symbol)
        ]
        rows.sort(key=lambda r: (r.ts, r.log_index), reverse=True)
        return rows[:limit]


class FakeDetector:
    def __init__(self, status: FlowStatus) -> None:
        self._status = status

    def status(self) -> FlowStatus:
        return self._status


def live_status(**kw: object) -> FlowStatus:
    base = dict(
        connected=True,
        head=HEAD,
        last_block=HEAD,
        last_block_ts=NOW - 3,
        deposit_addrs=32_031,
        hot_wallets=951,
        internal=6,
        contracts=206,
        late_logs=0,
    )
    base.update(kw)
    return FlowStatus(**base)  # type: ignore[arg-type]


def make_client(
    reader: FakeFlowReader | None,
    *,
    status: FlowStatus | None = None,
    prices: dict[str, float] | None = None,
) -> TestClient:
    """lifespan 없이 — Influx 자리·감지기 자리·업비트 현재가(LiveStore 행)를 직접 채운다."""
    app: FastAPI = create_app()
    store = LiveStore()
    from datetime import UTC, datetime

    for symbol, price in (prices or {}).items():
        store.put_row(make_row("upbit", symbol, price=price), datetime.now(UTC))
    app.state.live_store = store
    app.state.settings = SimpleNamespace(refresh_token=None)
    app.state.influx = reader
    if status is not None:
        app.state.eth_flow = FakeDetector(status)
    return TestClient(app)


# ── /flow/netflow (§3.6) ─────────────────────────────────────────────────────


def test_netflow_sums_per_symbol_sorts_by_abs_krw_and_puts_null_krw_last() -> None:
    reader = FakeFlowReader()
    reader.seed("in", "SAND", 300.0, ts=NOW - 100, log_index=1)
    reader.seed("in", "SAND", 23.5, ts=NOW - 50, log_index=2)
    reader.seed("out", "SAND", 12.0, ts=NOW - 10)
    reader.seed("out", "XRP", 5000.0, ts=NOW - 20)  # 큰 수량·현재가 있음 → 원화 1위
    reader.seed("in", "NOPRICE", 99_999.0, ts=NOW - 30)  # 현재가 없음 → 뒤
    reader.seed("in", "GONE", 1.0, ts=NOW - 40, removed=True)  # 되돌린 점은 뺀다
    reader.seed("in", "OLD", 1.0, ts=NOW - 7200)  # 창 밖
    client = make_client(
        reader, status=live_status(), prices={"SAND": 500.0, "XRP": 3_000.0}
    )
    res = client.get("/flow/netflow")
    assert res.status_code == 200
    body = res.json()
    assert body["window"] == "1h" and body["asOf"] >= NOW
    assert body["feed"] == {
        "connected": True,
        "lastBlock": HEAD,
        "lagSec": body["asOf"] - (NOW - 3),
        "depositAddrs": 32_031,
        "hotWallets": 951,
        "contracts": 206,
    }
    assert [r["symbol"] for r in body["rows"]] == ["XRP", "SAND", "NOPRICE"]
    sand = body["rows"][1]
    assert sand == {
        "symbol": "SAND",
        "inCount": 2,
        "inAmount": 323.5,
        "outCount": 1,
        "outAmount": 12.0,
        "netAmount": 311.5,
        "netKrw": 311.5 * 500.0,
        "lastTs": NOW - 10,
    }
    assert (
        body["rows"][0]["netKrw"] == -5000.0 * 3_000.0
        and body["rows"][0]["inCount"] == 0
    )
    assert body["rows"][2]["netKrw"] is None
    start, stop = reader.netflow_calls[0]
    assert stop - start == 3_600 + 1


@pytest.mark.parametrize(
    ("window", "sec"), [("1h", 3_600), ("6h", 21_600), ("24h", 86_400)]
)
def test_netflow_window_values(window: str, sec: int) -> None:
    reader = FakeFlowReader()
    res = make_client(reader, status=live_status()).get(
        "/flow/netflow", params={"window": window}
    )
    assert (
        res.status_code == 200
        and res.json()["window"] == window
        and res.json()["rows"] == []
    )
    start, stop = reader.netflow_calls[0]
    assert stop - start == sec + 1


def test_netflow_rejects_unknown_window_with_400() -> None:
    res = make_client(FakeFlowReader(), status=live_status()).get(
        "/flow/netflow", params={"window": "2h"}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "invalid_request"


def test_netflow_without_detector_reports_feed_off() -> None:
    res = make_client(FakeFlowReader()).get("/flow/netflow")
    assert res.status_code == 200
    assert res.json()["feed"] == {
        "connected": False,
        "lastBlock": None,
        "lagSec": None,
        "depositAddrs": 0,
        "hotWallets": 0,
        "contracts": 0,
    }


def test_netflow_503_without_token_and_on_influx_failure() -> None:
    assert (
        make_client(None, status=live_status()).get("/flow/netflow").status_code == 503
    )
    reader = FakeFlowReader()
    reader.fail = True
    res = make_client(reader, status=live_status()).get("/flow/netflow")
    assert (
        res.status_code == 503 and res.json()["error"]["code"] == "storage_unavailable"
    )


# ── /flow/recent (§3.7) ──────────────────────────────────────────────────────


def test_recent_rows_newest_first_with_krw_confirmed_and_full_addresses() -> None:
    reader = FakeFlowReader()
    reader.seed("in", "SAND", 10.0, ts=NOW - 30, block=HEAD - 3, log_index=5)
    reader.seed("in", "SAND", 20.0, ts=NOW - 6, block=HEAD - 1, log_index=2)
    reader.seed("out", "XRP", 30.0, ts=NOW - 6, block=HEAD - 1, log_index=9)
    reader.seed("in", "GONE", 1.0, ts=NOW - 1, removed=True)
    client = make_client(reader, status=live_status(), prices={"SAND": 500.0})
    res = client.get("/flow/recent")
    assert res.status_code == 200
    body = res.json()
    assert body["head"] == HEAD and body["asOf"] >= NOW
    assert [(r["symbol"], r["amount"]) for r in body["rows"]] == [
        ("XRP", 30.0),
        ("SAND", 20.0),
        ("SAND", 10.0),
    ]
    assert body["rows"][2] == {
        "ts": NOW - 30,
        "block": HEAD - 3,
        "confirmed": True,
        "dir": "in",
        "symbol": "SAND",
        "amount": 10.0,
        "krw": 5_000.0,
        "addr": ADDR,
        "counterparty": OTHER,
        "txHash": TX,
    }
    assert body["rows"][0]["confirmed"] is False and body["rows"][0]["krw"] is None
    call = reader.recent_calls[0]
    assert call["stop"] - call["start"] == 86_400 + 1  # type: ignore[operator]
    assert (call["limit"], call["dir"], call["symbol"]) == (100, None, None)


def test_recent_applies_limit_dir_and_symbol() -> None:
    reader = FakeFlowReader()
    for i in range(5):
        reader.seed("in", "SAND", float(i), ts=NOW - 100 + i, log_index=i)
    reader.seed("out", "SAND", 99.0, ts=NOW - 50)
    reader.seed("in", "XRP", 7.0, ts=NOW - 40)
    client = make_client(reader, status=live_status())
    res = client.get(
        "/flow/recent", params={"limit": "2", "dir": "in", "symbol": "sand"}
    )
    assert res.status_code == 200
    assert [r["amount"] for r in res.json()["rows"]] == [4.0, 3.0]
    assert (
        reader.recent_calls[-1]["limit"],
        reader.recent_calls[-1]["dir"],
        reader.recent_calls[-1]["symbol"],
    ) == (2, "in", "SAND")
    res = client.get("/flow/recent", params={"dir": "out", "symbol": ""})
    assert [r["amount"] for r in res.json()["rows"]] == [99.0]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "501"},
        {"limit": "ten"},
        {"dir": "both"},
        {"symbol": "SA ND"},
        {"symbol": "x" * 21},
    ],
)
def test_recent_rejects_out_of_range_arguments_with_400(params: dict[str, str]) -> None:
    res = make_client(FakeFlowReader(), status=live_status()).get(
        "/flow/recent", params=params
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_request"


def test_recent_503_on_influx_failure_and_without_token() -> None:
    reader = FakeFlowReader()
    reader.fail = True
    res = make_client(reader, status=live_status()).get("/flow/recent")
    assert (
        res.status_code == 503 and res.json()["error"]["code"] == "storage_unavailable"
    )
    assert (
        make_client(None, status=live_status()).get("/flow/recent").status_code == 503
    )


def test_recent_without_detector_has_null_head_and_unconfirmed_rows() -> None:
    reader = FakeFlowReader()
    reader.seed("in", "SAND", 1.0, ts=NOW - 10)
    body = make_client(reader).get("/flow/recent").json()
    assert body["head"] is None and body["rows"][0]["confirmed"] is False


def test_flow_routes_are_collector_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """감지기 상태·현재가가 수집기 메모리라 api 역할에는 없다 (016 의 역할 분리 그대로)."""
    assert (
        history_client(None).get("/flow/netflow").status_code == 503
    )  # collector 앱 — 경로가 있다
    monkeypatch.setattr(
        "app.main.get_settings", lambda: Settings(_env_file=None, role="api")
    )
    api = TestClient(create_app())
    assert api.get("/flow/netflow").status_code == 404
    assert api.get("/flow/recent").status_code == 404


# ── 기동 (§3.4 — env 둘 다 있어야 켠다) ────────────────────────────────────


def test_detector_is_skipped_with_one_warning_unless_both_urls_are_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    app, _ = _boot(monkeypatch, eth_ws_url="wss://node.test/ws")
    with caplog.at_level(logging.WARNING, logger="marketlens.main"):
        with TestClient(app) as client:
            assert app.state.eth_flow is None
            # Influx 토큰이 없는 기동이라 503 — 감지기 없는 feed 모양은 위의 라우터 테스트가 본다
            assert client.get("/flow/netflow").status_code == 503
    assert sum("ETH_WS_URL·ETH_HTTP_URL" in r.getMessage() for r in caplog.records) == 1


def test_detector_starts_when_both_urls_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(url: str) -> object:
        raise OSError("refused")

    monkeypatch.setattr("app.core.eth_flow.open_socket", refuse)
    app, _ = _boot(
        monkeypatch,
        eth_ws_url="wss://node.test/ws",
        eth_http_url="https://node.test/rpc",
    )
    with TestClient(app):
        assert app.state.eth_flow is not None
        status = app.state.eth_flow.status()
        assert (
            status.contracts == 206
            and status.deposit_addrs == 32_027
            and status.hot_wallets == 950
        )
        assert status.internal == 6
