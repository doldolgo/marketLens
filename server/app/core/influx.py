"""InfluxDB 2.7 클라이언트 — 연결·읽기/쓰기 공유 인프라 (스펙 005 §3.1~3.2).

influxdb-client 를 import 하는 곳은 이 모듈뿐이다. persist 루프·history 서비스·백필은
`InfluxPoint` 와 아래 메서드 시그니처에만 의존한다 — 테스트는 같은 시그니처의 fake 를 쓴다.
모든 실패는 `InfluxUnavailableError` 하나로 모은다: 호출자는 원인 구분 없이
"저장소 불가"(재시도 또는 503) 로만 다룬다.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS
from influxdb_client.domain.write_precision import WritePrecision

logger = logging.getLogger("marketlens.influx")

# org·bucket 은 marketlens 고정 (db.md)
INFLUX_ORG = "marketlens"
INFLUX_BUCKET = "marketlens"

# bulk 는 수 MB 를 읽을 수 있어 기본 10초보다 길게 잡는다
_TIMEOUT_MS = 60_000


class InfluxUnavailableError(Exception):
    """Influx 연결·쓰기·읽기 실패 → 저장 루프는 다음 회차 재시도, /history/* 는 503."""


@dataclass(frozen=True)
class InfluxPoint:
    """점 1개 — (measurement, tags, time) 이 유일키, 같으면 Influx 가 덮어쓴다 (db.md)."""

    measurement: str
    tags: dict[str, str]
    fields: dict[str, float]
    ts: int  # epoch 초 — 기록 정밀도는 초 (db.md)


@dataclass(frozen=True)
class PremiumRow:
    """`premium` 조회 결과 1행."""

    base: str
    ts: int  # epoch 초
    fwd: float
    rev: float


def premium_point(
    *, dom: str, fx: str, base: str, ts: int, fwd: float, rev: float
) -> InfluxPoint:
    """김프 점 — 모델은 db.md `premium` 그대로."""
    return InfluxPoint(
        measurement="premium",
        tags={"dom": dom, "fx": fx, "base": base.upper()},
        fields={"fwd": fwd, "rev": rev},
        ts=ts,
    )


def dw_fail_point(*, exchange: str, ts: int) -> InfluxPoint:
    """입출금 조회 실패 관측 1점 — 모델은 db.md `dw_fail` 그대로."""
    return InfluxPoint(
        measurement="dw_fail", tags={"exchange": exchange}, fields={"v": 1.0}, ts=ts
    )


def _esc_tag(v: str) -> str:
    """line protocol 태그 값 이스케이프 — 콤마·공백·등호."""
    return (
        v.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(" ", "\\ ")
        .replace("=", "\\=")
    )


def _esc_flux(v: str) -> str:
    """Flux 문자열 리터럴 이스케이프 — 파라미터는 라우터가 이미 검증하지만 이중 방어."""
    return v.replace("\\", "\\\\").replace('"', '\\"')


def _rfc3339(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_line(p: InfluxPoint) -> str:
    """InfluxPoint → line protocol (초 정밀도)."""
    tags = ",".join(f"{k}={_esc_tag(v)}" for k, v in sorted(p.tags.items()))
    fields = ",".join(f"{k}={v!r}" for k, v in sorted(p.fields.items()))
    return f"{p.measurement},{tags} {fields} {p.ts}"


@dataclass
class InfluxClient:
    """실제 InfluxDB 2.7 접속 — 생성은 연결하지 않는다(lazy). 실패는 전부 InfluxUnavailableError."""

    url: str
    token: str
    org: str = INFLUX_ORG
    bucket: str = INFLUX_BUCKET
    _client: InfluxDBClient | None = field(default=None, init=False, repr=False)

    def _inner(self) -> InfluxDBClient:
        if self._client is None:
            self._client = InfluxDBClient(
                url=self.url, token=self.token, org=self.org, timeout=_TIMEOUT_MS
            )
        return self._client

    def ping(self) -> bool:
        """연결 확인 — 실패해도 예외 없이 False (기동 시 에러 로그 1줄용)."""
        try:
            return bool(self._inner().ping())
        except Exception:
            return False

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --- 쓰기 ---

    def write(self, points: list[InfluxPoint]) -> None:
        """점 목록을 쓰기 1번으로 보낸다 — 전부 성공 또는 예외(전부 없음)."""
        lines = [to_line(p) for p in points]
        try:
            with self._inner().write_api(write_options=SYNCHRONOUS) as write_api:
                write_api.write(
                    bucket=self.bucket,
                    record=lines,
                    write_precision=WritePrecision.S,
                )
        except Exception as exc:
            raise InfluxUnavailableError(f"Influx 쓰기 실패: {exc}") from exc

    # --- 읽기 (premium 전용 — 읽는 HTTP 엔드포인트는 /history/* 뿐, db.md) ---

    def query_premium(
        self,
        *,
        dom: str,
        fx: str,
        base: str | None,
        start: int,
        stop: int,
    ) -> list[PremiumRow]:
        """[start, stop) 구간의 premium 행 — ts 오름차순. base=None 이면 전 코인."""
        base_clause = (
            f' and r.base == "{_esc_flux(base.upper())}"' if base is not None else ""
        )
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => r._measurement == "premium" and r.dom == "{_esc_flux(dom)}" and r.fx == "{_esc_flux(fx)}"{base_clause})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"])
  |> keep(columns: ["_time", "base", "fwd", "rev"])
"""
        rows: list[PremiumRow] = []
        for record in self._records(flux):
            fwd = record.values.get("fwd")
            rev = record.values.get("rev")
            if fwd is None or rev is None:
                continue  # 두 필드는 항상 같이 쓰므로 반쪽 점은 정상 데이터가 아니다
            rows.append(
                PremiumRow(
                    base=str(record.values.get("base", "")),
                    ts=int(record.values["_time"].timestamp()),
                    fwd=float(fwd),
                    rev=float(rev),
                )
            )
        return rows

    def count_premium(
        self, *, dom: str, fx: str, base: str, start: int, stop: int
    ) -> int:
        """[start, stop) 구간의 점 수 — fwd 필드 기준 (fwd/rev 는 항상 같이 쓴다)."""
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => r._measurement == "premium" and r.dom == "{_esc_flux(dom)}" and r.fx == "{_esc_flux(fx)}" and r.base == "{_esc_flux(base.upper())}" and r._field == "fwd")
  |> group()
  |> count()
"""
        for record in self._records(flux):
            return int(record.get_value())
        return 0

    def first_last_premium(
        self, *, dom: str, fx: str, base: str
    ) -> tuple[int, int] | None:
        """그 코인 기록의 (첫 time, 마지막 time) epoch 초 — 없으면 None (백필 대상 구간 계산용)."""
        out: list[int] = []
        for fn in ("first", "last"):
            flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "premium" and r.dom == "{_esc_flux(dom)}" and r.fx == "{_esc_flux(fx)}" and r.base == "{_esc_flux(base.upper())}" and r._field == "fwd")
  |> group()
  |> {fn}()
"""
            found = False
            for record in self._records(flux):
                out.append(int(record.get_time().timestamp()))
                found = True
                break
            if not found:
                return None
        return (out[0], out[1])

    def _records(self, flux: str):  # noqa: ANN202 — influxdb-client 내부 타입 비노출
        try:
            tables = self._inner().query_api().query(flux)
        except Exception as exc:
            raise InfluxUnavailableError(f"Influx 조회 실패: {exc}") from exc
        for table in tables:
            yield from table.records
