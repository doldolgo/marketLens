"""기동·모듈 수준에서 줄인 비용이 동작을 바꾸지 않는다 — sniffio 표지(main.py 머리)와 씨앗 읽기 스레드(050 §3.2).

- sniffio: 깔려 있지 않을 때만 `sys.modules["sniffio"] = None` 을 둔다. REST 요청의 결과·판별값은 표지가 없을 때와
  같고, 요청마다 하던 import 탐색만 사라진다. 깔려 있으면 진짜 sniffio 를 그대로 쓴다.
- 씨앗: `load_seeds` 를 기본 실행기 스레드에서 읽는다. 읽은 값은 직접 읽은 것과 같고, 실패는 그대로 기동 실패다.
"""

import asyncio
import importlib.metadata
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import httpcore
import pytest
from fastapi.testclient import TestClient
from httpcore._synchronization import current_async_library as httpcore_library

import app.main as _main  # noqa: F401 — 모듈 머리의 sniffio 표지를 이 프로세스에 둔다
from app.core import eth_flow as eth_flow_mod
from tests.test_raw_archive import _boot

SERVER_DIR = Path(__file__).resolve().parents[1]


def _sniffio_installed() -> bool:
    try:
        importlib.metadata.distribution("sniffio")
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


class _SniffioLookups:
    """meta_path 맨 앞에서 `sniffio` 탐색 횟수만 센다 — 찾지는 않으므로 뒤 finder 들의 동작은 그대로다."""

    def __init__(self) -> None:
        self.count = 0

    def find_spec(self, name: str, path: Any = None, target: Any = None) -> None:
        if name == "sniffio":
            self.count += 1
        return None


async def _three_requests() -> list[tuple[int, bytes]]:
    """실제 httpcore 연결 풀(keep-alive) 로 요청 3건 — 소켓 대신 httpcore 의 가짜 망을 쓴다."""
    reply = [b"HTTP/1.1 200 OK\r\n", b"Content-Length: 2\r\n", b"\r\n", b"ok"]
    backend = httpcore.AsyncMockBackend(reply * 3)
    out: list[tuple[int, bytes]] = []
    async with httpcore.AsyncConnectionPool(network_backend=backend) as pool:
        for _ in range(3):
            resp = await pool.request("GET", "https://exchange.test/list")
            out.append((resp.status, resp.content))
    return out


async def _library() -> str:
    return httpcore_library()


@pytest.mark.skipif(
    _sniffio_installed(),
    reason="sniffio 가 깔린 환경 — 표지를 두지 않는 쪽은 아래 하위 프로세스 테스트가 본다",
)
def test_sniffio_marker_keeps_results_and_drops_the_per_request_import_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert sys.modules["sniffio"] is None  # app.main 을 읽은 프로세스
    lookups = _SniffioLookups()
    monkeypatch.setattr(sys, "meta_path", [lookups, *sys.meta_path])

    marked = asyncio.run(_three_requests())
    marked_lookups = lookups.count
    marked_library = asyncio.run(_library())

    # 표지가 없던 기준 상태 — 실패한 import 는 캐시되지 않아 요청마다 다시 찾는다
    monkeypatch.delitem(sys.modules, "sniffio")
    lookups.count = 0
    plain = asyncio.run(_three_requests())
    plain_lookups = lookups.count
    plain_library = asyncio.run(_library())

    assert marked == plain == [(200, b"ok")] * 3
    assert marked_library == plain_library == "asyncio"
    assert plain_lookups >= 3  # 요청마다 적어도 한 번(httpcore 1.0.9 는 4번)
    assert marked_lookups == 0


def _run_boot_import(tmp_path: Path, code: str, extra_path: Path | None = None) -> str:
    """새 인터프리터에서 app.main 을 읽는다 — cwd 를 빈 임시 폴더로 두어 레포의 .env 를 읽지 않는다."""
    paths = (
        [str(SERVER_DIR)] if extra_path is None else [str(extra_path), str(SERVER_DIR)]
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_installed_sniffio_is_left_alone(tmp_path: Path) -> None:
    """깔린 sniffio 는 표지 없이 그대로 남고 httpcore 가 그것으로 판별한다.

    가드는 깔려 있는지(find_spec)만 보므로 누가 먼저 sniffio 를 읽었는지와 상관없다. 지금 import 순서에서는
    fastapi 가 anyio 내부 모듈을 먼저 읽어 진짜 sniffio 가 이미 들어와 있지만, 그 순서에 기대지 않는다.
    """
    fake = tmp_path / "site" / "sniffio"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(
        textwrap.dedent(
            """
            import contextvars

            MARK = "installed-sniffio"
            calls = []
            current_async_library_cvar = contextvars.ContextVar("current_async_library_cvar", default=None)


            class AsyncLibraryNotFoundError(RuntimeError):
                pass


            def current_async_library():
                calls.append(1)
                return "asyncio"
            """
        ),
        encoding="utf-8",
    )
    out = _run_boot_import(
        tmp_path,
        """
        import asyncio, sys
        import app.main
        before = sys.modules.get("sniffio", "absent") is None
        import sniffio
        from httpcore._synchronization import current_async_library

        async def lib():
            return current_async_library()

        n0 = len(sniffio.calls)
        print(before, sniffio.MARK, asyncio.run(lib()), len(sniffio.calls) - n0)
        """,
        extra_path=tmp_path / "site",
    )
    assert out == "False installed-sniffio asyncio 1"


@pytest.mark.skipif(_sniffio_installed(), reason="sniffio 가 깔린 환경")
def test_fresh_process_marks_missing_sniffio(tmp_path: Path) -> None:
    out = _run_boot_import(
        tmp_path,
        """
        import sys
        assert "sniffio" not in sys.modules
        import app.main
        print(repr(sys.modules["sniffio"]))
        """,
    )
    assert out == "None"


# ── 050 씨앗 읽기 ────────────────────────────────────────────────────────────


async def _refuse(url: str) -> object:
    raise OSError("refused")


def test_seeds_are_read_off_the_event_loop_and_equal_a_direct_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = eth_flow_mod.load_seeds
    seen: dict[str, Any] = {}

    def spy() -> eth_flow_mod.Seeds:
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        seen["seeds"] = real()
        return seen["seeds"]

    monkeypatch.setattr("app.core.eth_flow.open_socket", _refuse)
    monkeypatch.setattr("app.main.load_seeds", spy)
    app, _ = _boot(
        monkeypatch,
        eth_ws_url="wss://node.test/ws",
        eth_http_url="https://node.test/rpc",
    )
    with TestClient(app):
        status = app.state.eth_flow.status()
    # 이벤트 루프가 도는 스레드가 아니다 — 읽는 동안 스트림·틱 루프가 멈추지 않는다
    assert seen["on_loop"] is False
    direct = real()
    assert seen["seeds"] == direct
    assert (
        len(direct.contracts),
        len(direct.deposit),
        len(direct.hot),
        len(direct.internal),
    ) == (
        206,
        32_027,
        950,
        6,
    )
    assert (
        status.contracts,
        status.deposit_addrs,
        status.hot_wallets,
        status.internal,
    ) == (206, 32_027, 950, 6)


def test_seed_read_failure_still_fails_the_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real = eth_flow_mod.load_seeds
    started: list[str] = []

    def missing_dir() -> eth_flow_mod.Seeds:
        return real(tmp_path / "no-seeds")

    async def mark_start(self: Any) -> None:
        started.append("eth")

    monkeypatch.setattr("app.core.eth_flow.open_socket", _refuse)
    monkeypatch.setattr("app.main.load_seeds", missing_dir)
    monkeypatch.setattr(eth_flow_mod.EthFlowDetector, "start", mark_start)
    app, _ = _boot(
        monkeypatch,
        eth_ws_url="wss://node.test/ws",
        eth_http_url="https://node.test/rpc",
    )
    with pytest.raises(FileNotFoundError):
        with TestClient(app):
            pass
    assert started == []  # 감지기는 만들지도 시작하지도 않는다
    assert getattr(app.state, "eth_flow", "unset") == "unset"
