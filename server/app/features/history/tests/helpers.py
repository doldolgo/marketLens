"""history 테스트 공용 도구 — Influx 를 띄우지 않고 fake 리더로 (architecture.md 원칙)."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.influx import InfluxUnavailableError, PremiumRow
from app.core.live_store import LiveStore
from app.main import create_app


class FakeInfluxReader:
    """core.influx.InfluxClient 의 query_premium 시그니처를 그대로 흉내낸다."""

    def __init__(self) -> None:
        # (dom, fx, base) → [(ts, fwd, rev)] — seed 순서 무관, 조회는 ts 오름차순
        self._rows: dict[tuple[str, str, str], list[tuple[int, float, float]]] = {}
        self.fail = False

    def seed(
        self, dom: str, fx: str, base: str, rows: list[tuple[int, float, float]]
    ) -> None:
        self._rows.setdefault((dom, fx, base.upper()), []).extend(rows)

    def query_premium(
        self, *, dom: str, fx: str, base: str | None, start: int, stop: int
    ) -> list[PremiumRow]:
        if self.fail:
            raise InfluxUnavailableError("연결 실패 (테스트)")
        out: list[PremiumRow] = []
        for (d, f, b), rows in self._rows.items():
            if d != dom or f != fx:
                continue
            if base is not None and b != base.upper():
                continue
            for ts, fwd, rev in rows:
                if start <= ts < stop:
                    out.append(PremiumRow(base=b, ts=ts, fwd=fwd, rev=rev))
        out.sort(key=lambda r: r.ts)
        return out


def make_client(
    reader: FakeInfluxReader | None, store: LiveStore | None = None
) -> TestClient:
    """lifespan 없이 앱 상태를 직접 채운다 — 스트림·틱 루프·네트워크가 돌지 않는다.

    `reader` 는 Influx 자리(None = INFLUX_TOKEN 없음), `store` 는 메모리 시세 자리다 —
    저장소 장애 검증은 둘을 따로 채워 메모리 조회가 살아 있는지 본다 (§3.1).
    """
    app: FastAPI = create_app()
    app.state.live_store = store if store is not None else LiveStore()
    app.state.settings = SimpleNamespace(refresh_token=None)
    app.state.influx = reader
    return TestClient(app)
