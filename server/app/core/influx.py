"""InfluxDB 2.7 클라이언트 — 연결·읽기/쓰기 공유 인프라 (스펙 005 §3.1~3.2).

influxdb-client 를 import 하는 곳은 이 모듈뿐이다. 009 flusher·011 이력 추적기·013 사건 감지기·history 서비스·백필은
`InfluxPoint` 와 아래 메서드 시그니처에만 의존한다 — 테스트는 같은 시그니처의 fake 를 쓴다.
모든 실패는 `InfluxUnavailableError` 하나로 모은다: 호출자는 원인 구분 없이
"저장소 불가"(재시도 또는 503) 로만 다룬다.
line protocol 의 규칙(태그 정렬·이스케이프·필드 표기)은 이 모듈 한 곳에만 있다 — 점 객체 없이 줄을 바로 만드는
경로(009 flusher·014 분 닫힘)도 여기의 머리·줄 함수를 쓰고, 테스트가 `to_line` 과 바이트가 같은지 지킨다.
"""

import csv
import functools
import io
import logging
import re
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from influxdb_client import BucketRetentionRules, Dialect, InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS
from influxdb_client.domain.write_precision import WritePrecision

logger = logging.getLogger("marketlens.influx")

# org·bucket 은 marketlens 고정 (db.md)
INFLUX_ORG = "marketlens"
INFLUX_BUCKET = "marketlens"

# bulk 는 수 MB 를 읽을 수 있어 기본 10초보다 길게 잡는다
_TIMEOUT_MS = 60_000
# 봉·사건 점의 망 이름 "없음" 표식 (024 §3.4, 2026-09-28 사람 결정) — 빈 문자열로 쓰면 Influx 2.7 이 조회 창에 따라
# 빈 값을 다른 점에 붙여 읽는다(로컬 재현: 빈 값 15% 인 봉의 43~46% 가 남의 망 이름). 읽을 때는 이것과 옛 빈 문자열 둘 다 없음이다
_NO_NETWORK = "-"
# 헤더 있는 무주석 CSV — FluxRecord 파싱보다 파이썬 CPU 가 훨씬 적다(복원 조회, 009 §3.6·013 §3.3)
_CSV = Dialect(header=True, annotations=[])
# 흘려 읽는 조회(`stream_premium`)가 응답을 받는 조각 크기 — 메모리는 조각 하나와 줄 하나만큼이다 (005 §3.4)
_STREAM_CHUNK = 64 * 1024
# 랜딩 요약의 "최근에 끝난 사건" 후보 — 끝난 시각이 늦은 순으로 이만큼 받아 파이썬이 코인별 동률 규칙을 적용한다.
# 후보 끝자리의 동률 때문에 순위가 확정되지 않으면 닫힌 사건 전부로 다시 묻는다 (022 §3.2)
_SUMMARY_CANDIDATES = 200


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
    # 024 §3.5 — 사건이 옮기는 망의 표시명(국내·해외). None = 모름/없음. 배포 전 점에는 필드가 없다
    net_dom: str | None = None
    net_fx: str | None = None


@dataclass(frozen=True, slots=True)
class EventListRow:
    """`/history/events` 가 읽는 사건 점 1개 — 응답에 싣는 필드만 (013 §3.4). `end_ts == 0` 은 진행 중(또는 고아).

    `PremiumEventRow` 와 따로 두는 것은 이 조회가 `duration_seconds`·기준값 필드를 읽지 않기 때문이다 — 0 을 채운
    가짜 값이 다른 곳으로 새지 않게.
    """

    dom: str
    fx: str
    base: str
    dir: str  # kimp | reverse
    start_ts: int  # epoch 초 = 점의 time
    end_ts: int  # 0 = 진행 중
    max_percent: float
    max_ts: int
    last_ts: int
    samples: int
    net_dom: str | None
    net_fx: str | None


@dataclass(frozen=True, slots=True)
class EndedEventRow:
    """랜딩 요약의 "최근에 끝난 사건" 1건 (022 §3.2) — 닫힌 사건(`end_ts > 0`)."""

    dom: str
    fx: str
    base: str
    dir: str
    start_ts: int
    end_ts: int
    duration_seconds: int
    max_percent: float
    last_ts: int


@dataclass(frozen=True)
class EventSummary:
    """`query_event_summary` 결과 — 창 안 사건 점의 방향별 수, 진행 중 조합 수, 코인마다 가장 늦게 끝난 사건."""

    kimp: int
    reverse: int
    open: int  # end_ts 0 이고 last_ts ≥ open_since 인 (dom, fx, base, dir) 종류 수
    latest: list[
        EndedEventRow
    ]  # 끝난 시각 내림차순(같으면 base 오름차순), top_n 개까지


def _opt_str(v: object) -> str | None:
    """망 이름 필드의 없음 → None — 필드 없음(배포 전 점)·표식 `-`·옛 점의 빈 문자열이 전부 "없음" 이다(024 §3.4)."""
    if v is None or v == "" or v == _NO_NETWORK:
        return None
    return str(v)


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
            # 없음은 표식 `-` — Influx 문자열 필드에 null 이 없고, 빈 문자열은 조회 창에 따라 밀려 읽힌다 (024 §3.5)
            "net_dom": row.net_dom or _NO_NETWORK,
            "net_fx": row.net_fx or _NO_NETWORK,
        },
        ts=row.start_ts,
    )


@dataclass(frozen=True)
class CandleRow:
    """`candle` 점 1개 — (국내, 해외, 코인) 조합의 창 1개(스펙 014 §3.4). 다섯 계층 버킷이 같은 모양이다.

    입출금 4개는 int 3상태(1 가능·0 불가·−1 모름) — Influx 에 null 이 없어서다. 값은 006 §3.7 판정값(024).
    망 이름 2개는 문자열이고 없음은 표식 `-` 로 쓴다. 배포 전 점에는 두 필드가 없다 — 읽을 때 선택이다.
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
    net_dom: str | None = None
    net_fx: str | None = None


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
# 024 §3.4 — 망 이름 2개. 읽을 때 선택(없거나 빈 문자열 = None)이라 18 필드 옛 점을 버리지 않는다
_CANDLE_STR_FIELDS = ("net_dom", "net_fx")


def candle_point(row: CandleRow) -> InfluxPoint:
    """봉 1점(20 필드) — 유일키 (버킷, dom, fx, base, 창 시작). 같은 창을 다시 접으면 덮어쓴다(재시도 안전)."""
    fields: dict[str, float | int | str] = {
        k: float(getattr(row, k)) for k in _CANDLE_FLOAT_FIELDS
    }
    fields.update({k: int(getattr(row, k)) for k in _CANDLE_INT_FIELDS})
    fields.update({k: getattr(row, k) or _NO_NETWORK for k in _CANDLE_STR_FIELDS})
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


def _head(measurement: str, tags: dict[str, str]) -> str:
    """줄의 머리 — measurement, 이름순 태그(이스케이프), 뒤 공백 하나."""
    tag_text = ",".join(f"{k}={_esc_tag(v)}" for k, v in sorted(tags.items()))
    return f"{measurement},{tag_text} "


def to_line(p: InfluxPoint) -> str:
    """InfluxPoint → line protocol (초 정밀도). 필드는 이름순."""
    fields = ",".join(f"{k}={_field_literal(v)}" for k, v in sorted(p.fields.items()))
    return f"{_head(p.measurement, p.tags)}{fields} {p.ts}"


# --- 점 객체 없이 줄 만들기 (009 §3.5 flusher·014 §3.4 분 닫힘) ---
# 머리는 조합마다 한 번 만들어 호출자가 캐시한다. 줄은 같은 값의 `to_line(…_point(…))` 와 바이트가 같다 —
# 필드 순서(이름순)·표기(float 는 repr, int 는 `i` 접미, 문자열은 따옴표·이스케이프)를 그대로 옮겼다.


def premium_head(dom: str, fx: str, base: str) -> str:
    """`premium` 줄의 머리 — `premium_point` 와 같은 태그(코인은 대문자)."""
    return _head("premium", {"dom": dom, "fx": fx, "base": base.upper()})


def premium_line(head: str, fwd: float, rev: float, ts: int) -> str:
    """`premium` 한 줄 — `fwd`·`rev` 는 float 로 넘긴다(`premium_point` 의 필드와 같은 형)."""
    return f"{head}fwd={fwd!r},rev={rev!r} {ts}"


def candle_head(dom: str, fx: str, base: str) -> str:
    """`candle` 줄의 머리 — `candle_point` 와 같은 태그(코인 이름 그대로)."""
    return _head("candle", {"dom": dom, "fx": fx, "base": base})


def candle_line(
    head: str,
    ts: int,
    *,
    fwd_o: float,
    fwd_h: float,
    fwd_l: float,
    fwd_c: float,
    rev_o: float,
    rev_h: float,
    rev_l: float,
    rev_c: float,
    krw: float,
    usdt: float,
    rate: float,
    dom_dep: int,
    dom_wd: int,
    fx_dep: int,
    fx_wd: int,
    blocked_fwd_sec: int,
    blocked_rev_sec: int,
    samples: int,
    net_dom: str | None,
    net_fx: str | None,
) -> str:
    """`candle` 한 줄(20 필드) — 같은 값의 `to_line(candle_point(CandleRow(…)))` 와 같은 바이트."""
    return (
        f"{head}blocked_fwd_sec={int(blocked_fwd_sec)}i,blocked_rev_sec={int(blocked_rev_sec)}i,"
        f"dom_dep={int(dom_dep)}i,dom_wd={int(dom_wd)}i,"
        f"fwd_c={float(fwd_c)!r},fwd_h={float(fwd_h)!r},fwd_l={float(fwd_l)!r},fwd_o={float(fwd_o)!r},"
        f"fx_dep={int(fx_dep)}i,fx_wd={int(fx_wd)}i,krw={float(krw)!r},"
        f"net_dom={_field_literal(net_dom or _NO_NETWORK)},net_fx={_field_literal(net_fx or _NO_NETWORK)},"
        f"rate={float(rate)!r},rev_c={float(rev_c)!r},rev_h={float(rev_h)!r},"
        f"rev_l={float(rev_l)!r},rev_o={float(rev_o)!r},samples={int(samples)}i,usdt={float(usdt)!r} {ts}"
    )


@dataclass(frozen=True, slots=True)
class ChainFlowRow:
    """`chain_flow` 점 1개 — 업비트 이더리움 ERC-20 전송 1건(스펙 050 §3.5). 유일키 = (tag 넷, time).

    `ts` 는 블록 시각(epoch 초)이고 점의 time 은 **나노초** `ts × 10⁹ + log_index` 다 — 같은 블록·코인·방향에 전송이
    여러 건이라 초로는 겹친다. `addr` 는 입금이면 입금주소, 출금이면 핫월렛. `removed` 는 리오그로 되돌린 로그.
    """

    dir: str  # in | out
    symbol: str
    ts: int  # 블록 시각 epoch 초
    log_index: int
    amount: float
    counterparty: str
    addr: str
    tx_hash: str
    block: int
    removed: bool = False
    exchange: str = "upbit"
    network: str = "eth"


@dataclass(frozen=True, slots=True)
class ChainFlowAgg:
    """`query_chain_flow_netflow` 결과 1행 — 창 안 (symbol, dir) 의 건수·수량 합·마지막 블록 시각(초) (050 §3.6)."""

    symbol: str
    dir: str
    count: int
    amount: float
    last_ts: int


def chain_flow_ns(ts: int, log_index: int) -> int:
    """`chain_flow` 점의 time(나노초) — 블록 시각 초 × 10⁹ + log index (050 §3.5)."""
    return ts * 1_000_000_000 + log_index


def chain_flow_line(row: ChainFlowRow) -> str:
    """`chain_flow` 한 줄(7 필드, 나노초 시각) — `write_lines(…, precision="ns")` 로 쓴다 (050 §3.5).

    리오그로 되돌린 로그는 같은 줄을 `removed=true` 로 다시 써 덮는다. 필드는 다른 줄 함수와 같이 이름순이고 bool 은
    line protocol 의 `true`/`false` 다 — `InfluxPoint` 의 필드 형에 bool 이 없어 점 객체를 거치지 않는다.
    """
    head = _head(
        "chain_flow",
        {
            "exchange": row.exchange,
            "network": row.network,
            "dir": row.dir,
            "symbol": row.symbol,
        },
    )
    if row.removed:
        removed = "true"
    else:
        removed = "false"
    return (
        f"{head}addr={_field_literal(row.addr)},amount={float(row.amount)!r},block={int(row.block)}i,"
        f"counterparty={_field_literal(row.counterparty)},log_index={int(row.log_index)}i,"
        f"removed={removed},tx_hash={_field_literal(row.tx_hash)} {chain_flow_ns(row.ts, row.log_index)}"
    )


def _epoch(text: str) -> int:
    """CSV 의 RFC3339 시각 → epoch 초 — FluxRecord 의 `_time.timestamp()` 를 int 로 자른 것과 같다."""
    return int(datetime.fromisoformat(text).timestamp())


@functools.lru_cache(maxsize=64)
def _day_epoch(day: str) -> int:
    """`YYYY-MM-DD` → 그날 00:00 UTC 의 epoch 초. 조회 한 번에 같은 날이 수십만 번 나온다."""
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())


def _epoch_fast(text: str) -> int:
    """`_epoch` 와 같은 값 — 초 정밀도 `…T12:34:56Z` 는 날짜 캐시와 정수 연산으로, 그 밖의 모양은 `_epoch` 로."""
    if len(text) == 20 and text[19] == "Z":
        return (
            _day_epoch(text[:10])
            + int(text[11:13]) * 3600
            + int(text[14:16]) * 60
            + int(text[17:19])
        )
    return _epoch(text)


def _epoch_seconds(text: str) -> int:
    """나노초 점의 CSV 시각 `…T12:34:56.000000123Z` → epoch 초(소수부 버림) — 초 점의 모양이면 `_epoch_fast` 그대로.

    `datetime.fromisoformat` 은 소수 6자리 뒤를 버려 나노초가 사라지므로 소수부는 보지 않는다 — log index 는 필드로 따로 읽는다.
    """
    if len(text) > 20 and text[19] == "." and text[-1] == "Z":
        return _epoch_fast(text[:19] + "Z")
    return _epoch_fast(text)


def _cells(line: str) -> list[str]:
    """CSV 한 줄 → 칸. 따옴표가 든 줄만 csv 모듈로 — 이름에 쉼표가 오면 Influx 가 따옴표로 감싼다."""
    if '"' in line:
        return next(csv.reader((line,)))
    return line.split(",")


# `/history/events` 가 싣는 사건 필드 — `duration_seconds` 는 end − start 로 다시 세고 기준값은 싣지 않는다 (013 §3.4)
_EVENT_LIST_FIELDS = (
    "end_ts",
    "last_ts",
    "max_percent",
    "max_ts",
    "samples",
    "net_dom",
    "net_fx",
)
_EVENT_LIST_COLUMNS = ("dom", "fx", "base", "dir", "_time", *_EVENT_LIST_FIELDS)
# `/flow/recent` 가 읽는 `chain_flow` 칸 — `removed` 는 Flux 가 거른 뒤라 싣지 않는다 (050 §3.7)
_CHAIN_FLOW_COLUMNS = (
    "_time",
    "dir",
    "symbol",
    "amount",
    "addr",
    "counterparty",
    "tx_hash",
    "block",
    "log_index",
)

# 요약 후보 한 건 — (끝난 시각, 시작 시각, dom, fx, base, dir)
_Ended = tuple[int, int, str, str, str, str]


def _latest_per_base(
    cands: list[_Ended], top_n: int, *, complete: bool
) -> list[_Ended] | None:
    """코인마다 가장 늦게 끝난 닫힌 사건 → 끝난 시각 내림차순(같으면 base 오름차순) top_n 개 (022 §3.2).

    같은 코인에서 끝난 시각이 같으면 늦게 시작한 것, 그것도 같으면 (dom, fx, dir) 이 앞서는 것. `complete` 가 아니면
    후보는 끝난 시각이 늦은 순으로 잘린 것이다 — 가장 이른 후보보다 늦게 끝난 코인이 top_n 개 이상일 때만 순위가
    확정된다(그 코인들의 그 시각 사건은 전부 후보에 들었다). 확정되지 않으면 None.
    """
    best: dict[str, _Ended] = {}
    for c in cands:
        cur = best.get(c[4])
        if (
            cur is None
            or c[:2] > cur[:2]
            or (c[:2] == cur[:2] and (c[2], c[3], c[5]) < (cur[2], cur[3], cur[5]))
        ):
            best[c[4]] = c
    if not complete and cands:
        floor = min(c[0] for c in cands)
        if sum(1 for c in best.values() if c[0] > floor) < top_n:
            return None
    return sorted(best.values(), key=lambda c: (-c[0], c[4]))[:top_n]


def _base_regex(bases: Iterable[str]) -> str:
    """코인 이름 정규식 `^(A|B)$` 의 가운데 — 메타문자와 정규식 리터럴의 끝 `/` 를 이스케이프한다.

    조건 수백 개를 or 로 늘어놓으면 Flux 가 "nested too deep" 으로 거부해 정규식 하나로 좁힌다.
    """
    return "|".join(re.escape(b).replace("/", "\\/") for b in sorted(set(bases)))


@dataclass
class InfluxClient:
    """실제 InfluxDB 2.7 접속 — 생성은 연결하지 않는다(lazy). 실패는 전부 InfluxUnavailableError."""

    url: str
    token: str
    org: str = INFLUX_ORG
    bucket: str = INFLUX_BUCKET
    # HTTP 타임아웃(ms) → 클라이언트. 기본 60초 하나와, 기동 복원이 상한과 같은 값으로 부르는 것(3초·10초)뿐이다
    _clients: dict[int, InfluxDBClient] = field(
        default_factory=dict, init=False, repr=False
    )
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def _inner(self, timeout_sec: float | None = None) -> InfluxDBClient:
        """`timeout_sec` 가 있으면 요청마다 그 시간에 끊기는 클라이언트 — 복원 상한에서 스레드·Influx 조회가 함께 끝나게.

        호출은 여러 스레드에서 온다(쓰기 태스크들·복원) — 같은 타임아웃의 클라이언트를 둘 만들지 않게 잠근다.
        """
        timeout_ms = _TIMEOUT_MS if timeout_sec is None else int(timeout_sec * 1000)
        with self._lock:
            client = self._clients.get(timeout_ms)
            if client is None:
                client = InfluxDBClient(
                    url=self.url, token=self.token, org=self.org, timeout=timeout_ms
                )
                self._clients[timeout_ms] = client
        return client

    def ping(self) -> bool:
        """연결 확인 — 실패해도 예외 없이 False (기동 시 에러 로그 1줄용)."""
        try:
            return bool(self._inner().ping())
        except Exception:
            return False

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()

    # --- 쓰기 ---

    def write(self, points: list[InfluxPoint], bucket: str | None = None) -> None:
        """점 목록을 쓰기 1번으로 보낸다 — 전부 성공 또는 예외(전부 없음). `bucket` 없으면 `marketlens`(014 계층 버킷만 지정)."""
        self.write_lines([to_line(p) for p in points], bucket)

    def write_lines(
        self,
        lines: list[str],
        bucket: str | None = None,
        precision: Literal["s", "ns"] = "s",
    ) -> None:
        """line protocol 줄 목록을 쓰기 1번으로 — 본문은 줄을 개행으로 이은 UTF-8 바이트(`write` 와 같은 본문).

        점 객체를 만들지 않는 경로(009 flusher·014 분 닫힘)가 쓴다. 빈 목록은 요청하지 않는다.
        `precision` 은 줄 끝 시각의 단위다 — 기본 초이고, `chain_flow`(050 §3.5)만 나노초로 쓴다(기존 호출은 그대로).
        """
        if not lines:
            return
        if precision == "ns":
            write_precision = WritePrecision.NS
        else:
            write_precision = WritePrecision.S
        try:
            with self._inner().write_api(write_options=SYNCHRONOUS) as write_api:
                write_api.write(
                    bucket=bucket or self.bucket,
                    record="\n".join(lines).encode(),
                    write_precision=write_precision,
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

    def stream_premium(
        self,
        *,
        dom: str,
        fx: str,
        base: str | None,
        start: int,
        stop: int,
    ) -> Iterator[tuple[str, str, int, float]]:
        """[start, stop) 의 premium 을 `(base, field, ts, value)` 로 흘려보낸다 — pivot·group·sort 없음 (005 §3.4).

        같은 (base, field) 의 점은 한 줄기로 시각 오름차순이 이어서 온다(Influx 시리즈 표 그대로). 줄기의 순서는
        정하지 않는다. 응답을 조각으로 받아 줄마다 넘기므로 메모리는 행 수와 무관하다. 도중 실패는
        `InfluxUnavailableError` — 호출자는 받은 것을 버리고 503 으로 답한다.
        """
        base_clause = (
            f' and r.base == "{_esc_flux(base.upper())}"' if base is not None else ""
        )
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => r._measurement == "premium" and r.dom == "{_esc_flux(dom)}" and r.fx == "{_esc_flux(fx)}"{base_clause} and (r._field == "fwd" or r._field == "rev"))
  |> keep(columns: ["_time", "_value", "_field", "base"])
"""
        seen: dict[str, int] | None = None
        ib = ifd = it = iv = 0
        for cols, cells in self._table_rows(flux):
            if cols is not seen:
                seen = cols
                ib, ifd, it, iv = (
                    cols["base"],
                    cols["_field"],
                    cols["_time"],
                    cols["_value"],
                )
            yield cells[ib], cells[ifd], _epoch_fast(cells[it]), float(cells[iv])

    def _table_rows(self, flux: str) -> Iterator[tuple[dict[str, int], list[str]]]:
        """헤더 CSV 를 흘려 읽어 행마다 (열 이름 → 칸 번호, 칸) — 표 모양이 바뀌면 빈 줄 뒤에 헤더가 다시 온다.

        같은 헤더 아래의 행은 같은 사전 객체를 받는다(호출자가 칸 번호를 헤더마다 한 번만 찾게). 응답 도중 난 Flux
        오류는 오류 표 하나로 온다 — 저장소 실패다.
        """
        cols: dict[str, int] | None = None
        for line in self._stream_lines(flux):
            if not line:
                cols = None
                continue
            cells = _cells(line)
            if cols is None:
                if "error" in cells and "result" not in cells:
                    raise InfluxUnavailableError(f"Influx 조회 실패: {line}")
                cols = {name: i for i, name in enumerate(cells)}
                continue
            yield cols, cells

    def _stream_lines(self, flux: str) -> Iterator[str]:
        """헤더 있는 무주석 CSV 응답을 조각으로 받아 줄(끝의 `\\r` 뺌)로 — 받은 조각과 넘기지 못한 반쪽 줄만 든다."""
        try:
            resp = self._inner().query_api().query_raw(flux, dialect=_CSV)
        except Exception as exc:
            raise InfluxUnavailableError(f"Influx 조회 실패: {exc}") from exc
        done = False
        try:
            carry = b""
            try:
                for chunk in resp.stream(_STREAM_CHUNK):
                    buf = carry + chunk
                    cut = buf.rfind(b"\n") + 1
                    carry = buf[cut:]
                    if cut:
                        text = buf[:cut].decode("utf-8")
                        for line in text.split("\n")[:-1]:
                            yield line.rstrip("\r")
            except InfluxUnavailableError:
                raise
            except Exception as exc:
                raise InfluxUnavailableError(f"Influx 조회 실패: {exc}") from exc
            if carry.strip():
                raise InfluxUnavailableError(
                    "Influx 조회 실패: 응답이 줄 중간에서 끝났다"
                )
            done = True
        finally:
            # 다 읽은 연결만 풀에 돌려준다 — 덜 읽은 연결을 돌려주면 다음 요청이 남은 바이트를 읽는다
            if done:
                resp.release_conn()
            else:
                resp.close()

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

    def query_spark(
        self, *, start: int, stop: int, timeout_sec: float | None = None
    ) -> list[SparkBucketRow]:
        """[start, stop) 의 `premium.fwd` 를 조합별 1분 버킷 `last` 로 — 버킷 시각은 창의 시작.

        결과(수만 행)는 헤더 CSV 로 읽는다 — FluxRecord 로 읽은 행과 같다(009 §3.6). `timeout_sec` 는 복원 상한.
        """
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => r._measurement == "premium" and r._field == "fwd")
  |> aggregateWindow(every: 1m, fn: last, timeSrc: "_start", createEmpty: false)
  |> keep(columns: ["_time", "_value", "dom", "fx", "base"])
"""
        rows: list[SparkBucketRow] = []
        for r in self._csv_rows(flux, timeout_sec):
            value = r.get("_value", "")
            if value == "":
                continue
            rows.append(
                SparkBucketRow(
                    dom=r.get("dom", ""),
                    fx=r.get("fx", ""),
                    base=r.get("base", ""),
                    bucket_ts=_epoch(r["_time"]),
                    fwd=float(value),
                )
            )
        return rows

    # --- 읽기 (collect_fail — 기동 시 복원 1회, HTTP 조회 없음. 스펙 011 §3.4) ---

    def query_collect_fail(
        self, *, start: int, timeout_sec: float | None = None
    ) -> list[CollectFailRow]:
        """start(epoch 초) 이후에 시작한 실패 구간 전부 — 진행 중(ended_ts 없음) 포함. `timeout_sec` 는 복원 상한."""
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)})
  |> filter(fn: (r) => r._measurement == "collect_fail")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
  |> sort(columns: ["_time"])
"""
        rows: list[CollectFailRow] = []
        for record in self._records(flux, timeout_sec):
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
    ) -> list[EventListRow]:
        """`start ≤ start_ts < stop` 인 사건 전부(진행 중 `end_ts 0` 포함) — `/history/events` 가 싣는 필드만 (013 §3.4).

        그 필드만 pivot 하고 group·sort 없이 헤더 CSV 로 읽는다 — 순서는 정하지 않는다(응답 정렬은 호출자가 한다).
        """
        conds = ['r._measurement == "premium_event"']
        if dom is not None:
            conds.append(f'r.dom == "{_esc_flux(dom)}"')
        if dir is not None:
            conds.append(f'r.dir == "{_esc_flux(dir)}"')
        if base is not None:
            conds.append(f'r.base == "{_esc_flux(base.upper())}"')
        fields = " or ".join(f'r._field == "{f}"' for f in _EVENT_LIST_FIELDS)
        keep = ", ".join(
            f'"{c}"' for c in ("_time", "dom", "fx", "base", "dir", *_EVENT_LIST_FIELDS)
        )
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => {" and ".join(conds)} and ({fields}))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: [{keep}])
"""
        rows: list[EventListRow] = []
        seen: dict[str, int] | None = None
        idx: tuple[int, ...] = ()
        for cols, cells in self._table_rows(flux):
            if cols is not seen:
                # 표 모양(헤더)이 바뀔 때만 칸 번호를 다시 찾는다 — 옛 점의 표에는 망 칸이 없다(없으면 −1)
                seen = cols
                idx = tuple(cols.get(c, -1) for c in _EVENT_LIST_COLUMNS)
            (
                d,
                f,
                b,
                dr,
                t,
                end_ts,
                last_ts,
                max_pct,
                max_ts,
                samples,
                net_dom,
                net_fx,
            ) = (cells[i] if i >= 0 else "" for i in idx)
            if not last_ts or not max_pct:
                continue  # 반쪽 점은 싣지 않는다
            rows.append(
                EventListRow(
                    dom=d,
                    fx=f,
                    base=b,
                    dir=dr,
                    start_ts=_epoch_fast(t),
                    end_ts=int(end_ts or 0),
                    max_percent=float(max_pct),
                    max_ts=int(max_ts or 0),
                    last_ts=int(last_ts),
                    samples=int(samples or 0),
                    net_dom=_opt_str(net_dom),
                    net_fx=_opt_str(net_fx),
                )
            )
        return rows

    def query_event_summary(
        self, *, start: int, stop: int, open_since: int, top_n: int
    ) -> EventSummary:
        """`start ≤ start_ts < stop` 인 사건 점의 요약 — 랜딩 7일 사건 (022 §3.2). 점을 pivot 하지 않는다.

        한 요청에서 Flux 가 접는다: `end_ts` 필드로 방향별 수, `end_ts == 0` 인 점과 `last_ts ≥ open_since` 인 점의
        키, 닫힌 점 중 끝난 시각이 늦은 후보 `_SUMMARY_CANDIDATES` 개. 진행 중 = 두 키 집합에 모두 든 점의
        (dom, fx, base, dir) 종류 수 — 고아 점(last_ts 가 오래됨)은 빠지고, 재기동 뒤 같은 조합이 다시 열려도 한 번이다.
        코인마다 (끝난 시각, 시작 시각) 이 가장 늦은 사건을 골라 끝난 시각 내림차순(같으면 base 오름차순) top_n 개,
        그 사건들만 한 번 더 좁혀 나머지 필드를 읽는다. 후보 끝자리 동률로 순위가 확정되지 않으면 닫힌 점 전부로 다시 묻는다.
        """
        window = f"range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})"
        source = f'from(bucket: "{self.bucket}")\n  |> {window}'
        tags = '"_time", "dom", "fx", "base", "dir"'
        flux = f"""
data = {source}
  |> filter(fn: (r) => r._measurement == "premium_event" and r._field == "end_ts")
data
  |> group(columns: ["dir"])
  |> count()
  |> keep(columns: ["dir", "_value"])
  |> yield(name: "bydir")
data
  |> filter(fn: (r) => r._value == 0)
  |> keep(columns: [{tags}])
  |> yield(name: "zero")
{source}
  |> filter(fn: (r) => r._measurement == "premium_event" and r._field == "last_ts")
  |> filter(fn: (r) => r._value >= {int(open_since)})
  |> keep(columns: [{tags}])
  |> yield(name: "recent")
data
  |> filter(fn: (r) => r._value > 0)
  |> group()
  |> top(n: {_SUMMARY_CANDIDATES}, columns: ["_value"])
  |> keep(columns: [{tags}, "_value"])
  |> yield(name: "latest")
"""
        counts = {"kimp": 0, "reverse": 0}
        zero: set[tuple[str, str, str, str, int]] = set()
        recent: set[tuple[str, str, str, str, int]] = set()
        cands: list[_Ended] = []
        for cols, cells in self._table_rows(flux):
            result = cells[cols["result"]]
            if result == "bydir":
                counts[cells[cols["dir"]]] = int(cells[cols["_value"]])
                continue
            key = (
                cells[cols["dom"]],
                cells[cols["fx"]],
                cells[cols["base"]],
                cells[cols["dir"]],
                _epoch_fast(cells[cols["_time"]]),
            )
            if result == "zero":
                zero.add(key)
            elif result == "recent":
                recent.add(key)
            else:
                cands.append((int(cells[cols["_value"]]), key[4], *key[:4]))
        picks = _latest_per_base(
            cands, top_n, complete=len(cands) < _SUMMARY_CANDIDATES
        )
        if picks is None:
            # 후보 끝자리와 같은 시각에 끝난 사건이 잘려 순위가 확정되지 않았다(재기동이 고아를 한꺼번에 닫은 직후 등)
            closed = f"""
{source}
  |> filter(fn: (r) => r._measurement == "premium_event" and r._field == "end_ts")
  |> filter(fn: (r) => r._value > 0)
  |> keep(columns: [{tags}, "_value"])
"""
            cands = [
                (
                    int(cells[cols["_value"]]),
                    _epoch_fast(cells[cols["_time"]]),
                    cells[cols["dom"]],
                    cells[cols["fx"]],
                    cells[cols["base"]],
                    cells[cols["dir"]],
                )
                for cols, cells in self._table_rows(closed)
            ]
            picks = _latest_per_base(cands, top_n, complete=True)
        return EventSummary(
            kimp=counts["kimp"],
            reverse=counts["reverse"],
            open=len({k[:4] for k in zero & recent}),
            latest=self._ended_details(window, picks or []),
        )

    def _ended_details(self, window: str, picks: list[_Ended]) -> list[EndedEventRow]:
        """고른 닫힌 사건들의 나머지 필드 — 그 코인만 좁혀 세 필드를 pivot 한다(같은 창)."""
        if not picks:
            return []
        flux = f"""
from(bucket: "{self.bucket}")
  |> {window}
  |> filter(fn: (r) => r._measurement == "premium_event" and r.base =~ /^({_base_regex(p[4] for p in picks)})$/ and (r._field == "duration_seconds" or r._field == "last_ts" or r._field == "max_percent"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "dom", "fx", "base", "dir", "duration_seconds", "last_ts", "max_percent"])
"""
        wanted = {(p[2], p[3], p[4], p[5], p[1]): p for p in picks}
        found: dict[tuple[str, str, str, str, int], EndedEventRow] = {}
        for cols, cells in self._table_rows(flux):
            key = (
                cells[cols["dom"]],
                cells[cols["fx"]],
                cells[cols["base"]],
                cells[cols["dir"]],
                _epoch_fast(cells[cols["_time"]]),
            )
            pick = wanted.get(key)
            if pick is None:
                continue
            dur, last_ts, max_pct = (
                cells[cols[c]] if c in cols else ""
                for c in ("duration_seconds", "last_ts", "max_percent")
            )
            if not last_ts or not max_pct:
                continue  # 반쪽 점은 싣지 않는다
            found[key] = EndedEventRow(
                dom=key[0],
                fx=key[1],
                base=key[2],
                dir=key[3],
                start_ts=key[4],
                end_ts=pick[0],
                duration_seconds=int(dur or 0),
                max_percent=float(max_pct),
                last_ts=int(last_ts),
            )
        # 고른 순서(끝난 시각 내림차순, 같으면 base 오름차순) 그대로
        return [found[k] for k in wanted if k in found]

    def query_ongoing_events(
        self, *, start: int, stop: int, timeout_sec: float | None = None
    ) -> list[PremiumEventRow]:
        """`start ≤ start_ts < stop` 이고 `end_ts == 0` 인 사건만 — 기동 복원용 두 단계 조회 (013 §3.3).

        1단계는 `end_ts` 필드만 읽어 값이 0 인 (dom, fx, base, dir, 시각) 을 찾고, 2단계는 그 코인만 정규식으로 좁혀
        pivot 한 뒤 1단계 키에 든 행만 남긴다. 두 단계 모두 같은 창이다 — 2단계 창을 좁히면 옛 점의 빈 문자열 망 이름이
        창에 따라 다른 점으로 밀려 읽혀 결과가 달라진다. 순서는 정하지 않는다(복원이 정렬한다). `timeout_sec` 는
        요청마다의 HTTP 타임아웃(복원 상한과 같은 값).
        """
        window = f"range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})"
        keys: set[tuple[str, str, str, str, int]] = set()
        for r in self._csv_rows(
            f"""
from(bucket: "{self.bucket}")
  |> {window}
  |> filter(fn: (r) => r._measurement == "premium_event" and r._field == "end_ts")
  |> filter(fn: (r) => r._value == 0)
  |> keep(columns: ["_time", "dom", "fx", "base", "dir"])
""",
            timeout_sec,
        ):
            keys.add((r["dom"], r["fx"], r["base"], r["dir"], _epoch(r["_time"])))
        if not keys:
            return []
        bases = _base_regex(k[2] for k in keys)
        rows: list[PremiumEventRow] = []
        for r in self._csv_rows(
            f"""
from(bucket: "{self.bucket}")
  |> {window}
  |> filter(fn: (r) => r._measurement == "premium_event" and r.base =~ /^({bases})$/)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.end_ts == 0)
""",
            timeout_sec,
        ):
            if not r.get("last_ts") or not r.get("max_percent"):
                continue  # 반쪽 점은 싣지 않는다
            start_ts = _epoch(r["_time"])
            if (r["dom"], r["fx"], r["base"], r["dir"], start_ts) not in keys:
                continue
            rows.append(
                PremiumEventRow(
                    dom=r["dom"],
                    fx=r["fx"],
                    base=r["base"],
                    dir=r["dir"],
                    start_ts=start_ts,
                    end_ts=int(r.get("end_ts") or 0),
                    duration_seconds=int(r.get("duration_seconds") or 0),
                    max_percent=float(r["max_percent"]),
                    max_ts=int(r.get("max_ts") or 0),
                    last_ts=int(r["last_ts"]),
                    samples=int(r.get("samples") or 0),
                    enter_percent=float(r.get("enter_percent") or 0.0),
                    exit_percent=float(r.get("exit_percent") or 0.0),
                    net_dom=_opt_str(r.get("net_dom")),
                    net_fx=_opt_str(r.get("net_fx")),
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
                continue  # 반쪽 점은 싣지 않는다 — 봉은 수치 18 필드가 한 번에 쓰인다(망 2필드는 선택, 024)
            rows.append(
                CandleRow(
                    dom=str(v.get("dom", "")),
                    fx=str(v.get("fx", "")),
                    base=str(v.get("base", "")),
                    ts=int(v["_time"].timestamp()),
                    **{k: float(v[k]) for k in _CANDLE_FLOAT_FIELDS},
                    **{k: int(v[k]) for k in _CANDLE_INT_FIELDS},
                    **{k: _opt_str(v.get(k)) for k in _CANDLE_STR_FIELDS},
                )
            )
        return rows

    def latest_candle_ts(
        self, bucket: str, *, start: int, timeout_sec: float | None = None
    ) -> int | None:
        """`start` 이후 그 버킷의 가장 늦은 봉의 창 시작 — 없으면 None. 시리즈별 last() 는 푸시다운이라 전 구간 정렬이 없다."""
        return self._edge_candle_ts(
            bucket, start=start, fn="last", desc=True, timeout_sec=timeout_sec
        )

    def earliest_candle_ts(
        self, bucket: str, *, start: int, timeout_sec: float | None = None
    ) -> int | None:
        """`start` 이후 그 버킷의 가장 오래된 봉의 창 시작 — 없으면 None."""
        return self._edge_candle_ts(
            bucket, start=start, fn="first", desc=False, timeout_sec=timeout_sec
        )

    def _edge_candle_ts(
        self,
        bucket: str,
        *,
        start: int,
        fn: str,
        desc: bool,
        timeout_sec: float | None,
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
        for record in self._records(flux, timeout_sec):
            return int(record.get_time().timestamp())
        return None

    # --- 읽기 (chain_flow — /flow/netflow·/flow/recent. 스펙 050 §3.6~3.7) ---

    def query_chain_flow_netflow(self, *, start: int, stop: int) -> list[ChainFlowAgg]:
        """`start ≤ 블록 시각 < stop` 의 `chain_flow` 를 (symbol, dir) 마다 건수·수량 합·마지막 블록 시각으로 접는다 (050 §3.6).

        `removed` 가 참인 점은 뺀다 — `amount`·`removed` 두 필드를 pivot 해 걸러낸 뒤 Flux 가 접으므로 점을 올리지 않는다.
        순서는 정하지 않는다(응답 정렬은 호출자가 한다).
        """
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => r._measurement == "chain_flow" and r.exchange == "upbit" and r.network == "eth" and (r._field == "amount" or r._field == "removed"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.removed == false)
  |> group(columns: ["symbol", "dir"])
  |> reduce(
      fn: (r, accumulator) => ({{
        count: accumulator.count + 1,
        amount: accumulator.amount + r.amount,
        last_ns: if int(v: r._time) > accumulator.last_ns then int(v: r._time) else accumulator.last_ns,
      }}),
      identity: {{count: 0, amount: 0.0, last_ns: 0}},
  )
  |> keep(columns: ["symbol", "dir", "count", "amount", "last_ns"])
"""
        rows: list[ChainFlowAgg] = []
        for cols, cells in self._table_rows(flux):
            rows.append(
                ChainFlowAgg(
                    symbol=cells[cols["symbol"]],
                    dir=cells[cols["dir"]],
                    count=int(cells[cols["count"]]),
                    amount=float(cells[cols["amount"]]),
                    last_ts=int(cells[cols["last_ns"]]) // 1_000_000_000,
                )
            )
        return rows

    def query_chain_flow_recent(
        self,
        *,
        start: int,
        stop: int,
        limit: int,
        dir: str | None = None,
        symbol: str | None = None,
    ) -> list[ChainFlowRow]:
        """`start ≤ 블록 시각 < stop` 의 `chain_flow` 를 시각 내림차순(같은 블록은 log index 내림차순 — 나노초 time 이
        그 순서다)으로 `limit` 개까지 (050 §3.7). `removed` 가 참인 점은 뺀다. `dir`·`symbol` 은 태그 필터."""
        conds = [
            'r._measurement == "chain_flow"',
            'r.exchange == "upbit"',
            'r.network == "eth"',
        ]
        if dir is not None:
            conds.append(f'r.dir == "{_esc_flux(dir)}"')
        if symbol is not None:
            conds.append(f'r.symbol == "{_esc_flux(symbol)}"')
        columns = ", ".join(f'"{c}"' for c in _CHAIN_FLOW_COLUMNS)
        flux = f"""
from(bucket: "{self.bucket}")
  |> range(start: {_rfc3339(start)}, stop: {_rfc3339(stop)})
  |> filter(fn: (r) => {" and ".join(conds)})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.removed == false)
  |> group()
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: {int(limit)})
  |> keep(columns: [{columns}])
"""
        rows: list[ChainFlowRow] = []
        for cols, cells in self._table_rows(flux):
            idx = [cols.get(c, -1) for c in _CHAIN_FLOW_COLUMNS]
            if any(i < 0 for i in idx):
                continue  # 반쪽 점은 싣지 않는다 — 전송 1건은 7 필드가 한 번에 쓰인다
            t, d, sym, amount, addr, counterparty, tx_hash, block, log_index = (
                cells[i] for i in idx
            )
            if not amount or not block or not log_index:
                continue
            rows.append(
                ChainFlowRow(
                    dir=d,
                    symbol=sym,
                    ts=_epoch_seconds(t),
                    log_index=int(log_index),
                    amount=float(amount),
                    counterparty=counterparty,
                    addr=addr,
                    tx_hash=tx_hash,
                    block=int(block),
                )
            )
        return rows

    def _records(self, flux: str, timeout_sec: float | None = None):  # noqa: ANN202 — influxdb-client 내부 타입 비노출
        try:
            tables = self._inner(timeout_sec).query_api().query(flux)
        except Exception as exc:
            raise InfluxUnavailableError(f"Influx 조회 실패: {exc}") from exc
        for table in tables:
            yield from table.records

    def _csv_rows(
        self, flux: str, timeout_sec: float | None = None
    ) -> Iterator[dict[str, str]]:
        """헤더 있는 무주석 CSV 를 행마다 {열 이름: 글자} 로. 표 모양이 바뀌면 빈 줄 뒤에 헤더가 다시 온다.

        csv 모듈로 읽는다 — 망 이름·코인 이름에 콤마·따옴표가 와도 칸이 어긋나지 않는다. 값이 없는 칸은 빈 글자다.
        """
        try:
            resp = self._inner(timeout_sec).query_api().query_raw(flux, dialect=_CSV)
            try:
                text = resp.data.decode("utf-8")
            finally:
                resp.release_conn()
        except Exception as exc:
            raise InfluxUnavailableError(f"Influx 조회 실패: {exc}") from exc
        header: list[str] | None = None
        for cells in csv.reader(io.StringIO(text)):
            if not cells:
                header = None
                continue
            if header is None:
                header = cells
                continue
            if "error" in header and "result" not in header:
                # 응답 도중 난 Flux 오류는 오류 표 하나로 온다
                raise InfluxUnavailableError(
                    f"Influx 조회 실패: {dict(zip(header, cells, strict=False))}"
                )
            yield dict(zip(header, cells, strict=False))
