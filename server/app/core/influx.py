"""InfluxDB 2.7 클라이언트 — 연결·읽기/쓰기 공유 인프라 (스펙 005 §3.1~3.2).

influxdb-client 를 import 하는 곳은 이 모듈뿐이다. 009 flusher·011 이력 추적기·013 사건 감지기·history 서비스·백필은
`InfluxPoint` 와 아래 메서드 시그니처에만 의존한다 — 테스트는 같은 시그니처의 fake 를 쓴다.
모든 실패는 `InfluxUnavailableError` 하나로 모은다: 호출자는 원인 구분 없이
"저장소 불가"(재시도 또는 503) 로만 다룬다.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from influxdb_client import BucketRetentionRules, InfluxDBClient
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
    # float 는 그대로, int 는 정수형(`i` 접미), str 은 따옴표 문자열로 쓴다 (db.md collect_fail)
    fields: dict[str, float | int | str]
    ts: int  # epoch 초 — 기록 정밀도는 초 (db.md)


@dataclass(frozen=True)
class PremiumRow:
    """`premium` 조회 결과 1행."""

    base: str
    ts: int  # epoch 초
    fwd: float
    rev: float


@dataclass(frozen=True)
class SparkBucketRow:
    """spark 복원 조회 결과 1행 — 1분 버킷의 마지막 fwd (스펙 009 §3.6)."""

    dom: str
    fx: str
    base: str
    bucket_ts: int  # 창의 시작 epoch 초(분 경계) — `ts // 60 * 60`
    fwd: float


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


@dataclass(frozen=True)
class CollectFailRow:
    """`collect_fail` 점 1개 — 수집 실패 구간(스펙 011 §3.4). 값이 없는 필드는 None."""

    exchange: str
    kind: str
    started_ts: int  # epoch 초 = 점의 time
    count: int
    last_failed_ts: int
    status_code: int | None
    message: str
    url: str | None
    retry_after_sec: int | None
    ended_ts: int | None  # None = 진행 중(닫힘 쓰기가 아직 없다)


@dataclass(frozen=True)
class PremiumEventRow:
    """`premium_event` 점 1개 — 김프/역프 사건(스펙 013 §3.3). `end_ts == 0` 은 진행 중."""

    dom: str
    fx: str
    base: str
    dir: str  # kimp | reverse
    start_ts: int  # epoch 초 = 점의 time
    end_ts: int  # 0 = 진행 중
    duration_seconds: int  # 진행 중이면 0
    max_percent: float
    max_ts: int
    last_ts: int
    samples: int
    enter_percent: float
    exit_percent: float


def premium_event_point(row: PremiumEventRow) -> InfluxPoint:
    """사건 1점 — 열림(60초 경과)·60초 갱신·닫힘이 전부 같은 (tag, time) 으로 덮어쓴다 (013 §3.3).

    기준값(enter/exit)을 점에 같이 남기는 것은 나중에 기준이 바뀌어도 과거 사건의 의미가 남게 하기 위해서다.
    """
    return InfluxPoint(
        measurement="premium_event",
        tags={"dom": row.dom, "fx": row.fx, "base": row.base, "dir": row.dir},
        fields={
            "end_ts": row.end_ts,
            "duration_seconds": row.duration_seconds,
            "max_percent": float(row.max_percent),
            "max_ts": row.max_ts,
            "last_ts": row.last_ts,
            "samples": row.samples,
            "enter_percent": float(row.enter_percent),
            "exit_percent": float(row.exit_percent),
        },
        ts=row.start_ts,
    )


@dataclass(frozen=True)
class CandleRow:
    """`candle` 점 1개 — (국내, 해외, 코인) 조합의 창 1개(스펙 014 §3.4). 다섯 계층 버킷이 같은 모양이다.

    입출금 4개는 int 3상태(1 가능·0 불가·−1 모름) — Influx 에 null 이 없어서다.
    """

    dom: str
    fx: str
    base: str
    ts: int  # 창 시작 epoch 초(KST 정렬) = 점의 time
    fwd_o: float
    fwd_h: float
    fwd_l: float
    fwd_c: float
    rev_o: float
    rev_h: float
    rev_l: float
    rev_c: float
    krw: float  # 국내 종가(원)
    usdt: float  # 해외 종가
    rate: float  # USDT 중간값(원)
    dom_dep: int
    dom_wd: int
    fx_dep: int
    fx_wd: int
    blocked_fwd_sec: int
    blocked_rev_sec: int
    samples: int


_CANDLE_FLOAT_FIELDS = (
    "fwd_o",
    "fwd_h",
    "fwd_l",
    "fwd_c",
    "rev_o",
    "rev_h",
    "rev_l",
    "rev_c",
    "krw",
    "usdt",
    "rate",
)
_CANDLE_INT_FIELDS = (
    "dom_dep",
    "dom_wd",
    "fx_dep",
    "fx_wd",
    "blocked_fwd_sec",
    "blocked_rev_sec",
    "samples",
)


def candle_point(row: CandleRow) -> InfluxPoint:
    """봉 1점 — 유일키 (버킷, dom, fx, base, 창 시작). 같은 창을 다시 접으면 덮어쓴다(재시도 안전)."""
    fields: dict[str, float | int | str] = {
        k: float(getattr(row, k)) for k in _CANDLE_FLOAT_FIELDS
    }
    fields.update({k: int(getattr(row, k)) for k in _CANDLE_INT_FIELDS})
    return InfluxPoint(
        measurement="candle",
        tags={"dom": row.dom, "fx": row.fx, "base": row.base},
        fields=fields,
        ts=row.ts,
    )


def collect_fail_point(row: CollectFailRow) -> InfluxPoint:
    """실패 구간 1점 — 열릴 때와 닫힐 때 같은 (tag, time) 으로 써서 필드를 합친다.

    None 은 0·빈 문자열로 쓴다(Influx 에 null 이 없다). `ended_ts` 는 닫힐 때만 실린다.
    """
    fields: dict[str, float | int | str] = {
        "count": row.count,
        "last_failed_ts": row.last_failed_ts,
        "status_code": row.status_code or 0,
        "message": row.message,
        "url": row.url or "",
        "retry_after_sec": row.retry_after_sec or 0,
    }
    if row.ended_ts is not None:
        fields["ended_ts"] = row.ended_ts
    return InfluxPoint(
        measurement="collect_fail",
        tags={"exchange": row.exchange, "kind": row.kind},
        fields=fields,
        ts=row.started_ts,
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


def _esc_field_str(v: str) -> str:
    """line protocol 문자열 필드 이스케이프 — 역슬래시·따옴표."""
    return v.replace("\\", "\\\\").replace('"', '\\"')


def _field_literal(v: float | int | str) -> str:
    if isinstance(v, str):
        return f'"{_esc_field_str(v)}"'
    if isinstance(v, int):
        return f"{v}i"
    return repr(v)


def to_line(p: InfluxPoint) -> str:
    """InfluxPoint → line protocol (초 정밀도)."""
    tags = ",".join(f"{k}={_esc_tag(v)}" for k, v in sorted(p.tags.items()))
    fields = ",".join(f"{k}={_field_literal(v)}" for k, v in sorted(p.fields.items()))
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

    def write(self, points: list[InfluxPoint], bucket: str | None = None) -> None:
        """점 목록을 쓰기 1번으로 보낸다 — 전부 성공 또는 예외(전부 없음). `bucket` 없으면 `marketlens`(014 계층 버킷만 지정)."""
        lines = [to_line(p) for p in points]
        try:
            with self._inner().write_api(write_options=SYNCHRONOUS) as write_api:
                write_api.write(
                    bucket=bucket or self.bucket,
                    record=lines,
                    write_precision=WritePrecision.S,
                )
        except Exception as exc:
            raise InfluxUnavailableError(f"Influx 쓰기 실패: {exc}") from exc

    # --- 버킷 (014 §3.4 — 계층 버킷 5개를 기동 시 만든다. `marketlens` 는 건드리지 않는다) ---

    def list_buckets(self) -> set[str]:
        """org 의 버킷 이름 집합."""
        try:
            found = self._inner().buckets_api().find_buckets(org=self.org, limit=100)
            return {b.name for b in (found.buckets or [])}
        except Exception as exc:
            raise InfluxUnavailableError(f"Influx 버킷 조회 실패: {exc}") from exc

    def create_bucket(self, name: str, retention_sec: int) -> None:
        """버킷 생성 — retention 0 은 무제한(Influx 규약). 이미 있는 버킷의 retention 은 여기서 바꾸지 않는다."""
        try:
            self._inner().buckets_api().create_bucket(
                bucket_name=name,
                retention_rules=BucketRetentionRules(
                    type="expire", every_seconds=retention_sec
                ),
                org=self.org,
            )
        except Exception as exc:
            raise InfluxUnavailableError(
                f"Influx 버킷 생성 실패 {name}: {exc}"
            ) from exc

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

    # --- 읽기 (spark 복원 — 기동 시 1회, HTTP 조회 없음. 스펙 009 §3.6) ---

    def query_spark(self, *, start: int, stop: int) -> list[SparkBucketRow]:
        """[start, stop) 의 `premium.fwd` 를 조합별 1분 버킷 `last` 로 — 버킷 시각은 창의 시작."""
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => r._measurement == "premium" and r._field == "fwd")
  |> aggregateWindow(every: 1m, fn: last, timeSrc: "_start", createEmpty: false)
  |> keep(columns: ["_time", "_value", "dom", "fx", "base"])
"""
        rows: list[SparkBucketRow] = []
        for record in self._records(flux):
            v = record.values
            value = v.get("_value")
            if value is None:
                continue
            rows.append(
                SparkBucketRow(
                    dom=str(v.get("dom", "")),
                    fx=str(v.get("fx", "")),
                    base=str(v.get("base", "")),
                    bucket_ts=int(v["_time"].timestamp()),
                    fwd=float(value),
                )
            )
        return rows

    # --- 읽기 (collect_fail — 기동 시 복원 1회, HTTP 조회 없음. 스펙 011 §3.4) ---

    def query_collect_fail(self, *, start: int) -> list[CollectFailRow]:
        """start(epoch 초) 이후에 시작한 실패 구간 전부 — 진행 중(ended_ts 없음) 포함."""
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)})
  |> filter(fn: (r) => r._measurement == "collect_fail")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"])
"""
        rows: list[CollectFailRow] = []
        for record in self._records(flux):
            v = record.values
            if v.get("count") is None or v.get("last_failed_ts") is None:
                continue  # 열림 쓰기가 유실된 반쪽 점은 복원하지 않는다
            ended = v.get("ended_ts")
            rows.append(
                CollectFailRow(
                    exchange=str(v.get("exchange", "")),
                    kind=str(v.get("kind", "")),
                    started_ts=int(v["_time"].timestamp()),
                    count=int(v["count"]),
                    last_failed_ts=int(v["last_failed_ts"]),
                    status_code=int(v["status_code"]) or None
                    if v.get("status_code") is not None
                    else None,
                    message=str(v.get("message") or ""),
                    url=str(v.get("url") or "") or None,
                    retry_after_sec=int(v["retry_after_sec"]) or None
                    if v.get("retry_after_sec") is not None
                    else None,
                    ended_ts=int(ended) if ended is not None else None,
                )
            )
        return rows

    # --- 읽기 (premium_event — /history/events 와 기동 시 복원. 스펙 013 §3.3~3.4) ---

    def query_premium_events(
        self,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        dir: str | None = None,
        base: str | None = None,
    ) -> list[PremiumEventRow]:
        """`start ≤ start_ts < stop` 인 사건 전부(진행 중 `end_ts 0` 포함) — start_ts 내림차순."""
        conds = ['r._measurement == "premium_event"']
        if dom is not None:
            conds.append(f'r.dom == "{_esc_flux(dom)}"')
        if dir is not None:
            conds.append(f'r.dir == "{_esc_flux(dir)}"')
        if base is not None:
            conds.append(f'r.base == "{_esc_flux(base.upper())}"')
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => {" and ".join(conds)})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"], desc: true)
"""
        rows: list[PremiumEventRow] = []
        for record in self._records(flux):
            v = record.values
            if v.get("last_ts") is None or v.get("max_percent") is None:
                continue  # 반쪽 점은 싣지 않는다
            rows.append(
                PremiumEventRow(
                    dom=str(v.get("dom", "")),
                    fx=str(v.get("fx", "")),
                    base=str(v.get("base", "")),
                    dir=str(v.get("dir", "")),
                    start_ts=int(v["_time"].timestamp()),
                    end_ts=int(v.get("end_ts") or 0),
                    duration_seconds=int(v.get("duration_seconds") or 0),
                    max_percent=float(v["max_percent"]),
                    max_ts=int(v.get("max_ts") or 0),
                    last_ts=int(v["last_ts"]),
                    samples=int(v.get("samples") or 0),
                    enter_percent=float(v.get("enter_percent") or 0.0),
                    exit_percent=float(v.get("exit_percent") or 0.0),
                )
            )
        return rows

    # --- 읽기 (candle — /history/candles·롤업·기동 따라잡기. 스펙 014 §3.5~3.6) ---

    def query_candles(
        self,
        bucket: str,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        fx: str | None = None,
        base: str | None = None,
    ) -> list[CandleRow]:
        """계층 버킷 하나에서 `start ≤ 창 시작 < stop` 인 봉 — ts 오름차순(같은 ts 는 dom·fx·base 순). 필터 없으면 전 조합."""
        conds = ['r._measurement == "candle"']
        if dom is not None:
            conds.append(f'r.dom == "{_esc_flux(dom)}"')
        if fx is not None:
            conds.append(f'r.fx == "{_esc_flux(fx)}"')
        if base is not None:
            conds.append(f'r.base == "{_esc_flux(base.upper())}"')
        flux = f"""
from(bucket: "{_esc_flux(bucket)}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => {" and ".join(conds)})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time", "dom", "fx", "base"])
"""
        rows: list[CandleRow] = []
        for record in self._records(flux):
            v = record.values
            if any(v.get(k) is None for k in _CANDLE_FLOAT_FIELDS + _CANDLE_INT_FIELDS):
                continue  # 반쪽 점은 싣지 않는다 — 봉은 18 필드가 한 번에 쓰인다
            rows.append(
                CandleRow(
                    dom=str(v.get("dom", "")),
                    fx=str(v.get("fx", "")),
                    base=str(v.get("base", "")),
                    ts=int(v["_time"].timestamp()),
                    **{k: float(v[k]) for k in _CANDLE_FLOAT_FIELDS},
                    **{k: int(v[k]) for k in _CANDLE_INT_FIELDS},
                )
            )
        return rows

    def latest_candle_ts(self, bucket: str, *, start: int) -> int | None:
        """`start` 이후 그 버킷의 가장 늦은 봉의 창 시작 — 없으면 None. 시리즈별 last() 는 푸시다운이라 전 구간 정렬이 없다."""
        return self._edge_candle_ts(bucket, start=start, fn="last", desc=True)

    def earliest_candle_ts(self, bucket: str, *, start: int) -> int | None:
        """`start` 이후 그 버킷의 가장 오래된 봉의 창 시작 — 없으면 None."""
        return self._edge_candle_ts(bucket, start=start, fn="first", desc=False)

    def _edge_candle_ts(
        self, bucket: str, *, start: int, fn: str, desc: bool
    ) -> int | None:
        flux = f"""
from(bucket: "{_esc_flux(bucket)}")
  |> range(start: {_rfc3339(start)})
  |> filter(fn: (r) => r._measurement == "candle" and r._field == "samples")
  |> {fn}()
  |> group()
  |> sort(columns: ["_time"], desc: {"true" if desc else "false"})
  |> limit(n: 1)
"""
        for record in self._records(flux):
            return int(record.get_time().timestamp())
        return None

    def _records(self, flux: str):  # noqa: ANN202 — influxdb-client 내부 타입 비노출
        try:
            tables = self._inner().query_api().query(flux)
        except Exception as exc:
            raise InfluxUnavailableError(f"Influx 조회 실패: {exc}") from exc
        for table in tables:
            yield from table.records
