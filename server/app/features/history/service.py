"""`/history/*` 계산 — 순수 계산. Influx 리더를 인자로 받고 전역을 import 하지 않는다.

리더는 `core.influx.InfluxClient` 시그니처의 일부(query_premium·stream_premium·query_premium_events·query_candles)만
쓴다 — 테스트는 같은 시그니처의 fake 를 넣어 Influx 없이 돈다 (architecture.md 원칙).

엔드포인트마다 두 경로가 있다. `encode_*` 는 라우터가 스레드에서 부르는 응답 바이트 경로이고, `build_*` 는 같은
응답을 모델로 만드는 경로다(테스트용 — 두 경로가 같은 바이트인지 테스트가 지킨다, 003 의 build_table 쌍과 같다).
"""

import json
import math
import re
import time
from array import array
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta, timezone
from operator import attrgetter
from typing import Literal, Protocol

from pydantic import BaseModel

from app.core.candles import TIER_BY_RES, Tier, limit_sec
from app.core.influx import CandleRow, EventListRow, PremiumRow
from app.core.premium_events import MIN_DURATION_SEC
from app.core.premium_events import PremiumEvent as OpenEvent
from app.core.serialization import camelize_json
from app.features.history.models import (
    BulkCoin,
    BulkResponse,
    CandleOut,
    CandlesResponse,
    DirectionSummary,
    EventOut,
    EventsResponse,
    Overall,
    PremiumEvent,
    PremiumHistoryResponse,
    PremiumSummary,
    Segment,
    StreaksResponse,
)

KST = timezone(timedelta(hours=9))


class PremiumReader(Protocol):
    """이 기능이 쓰는 저장소 읽기 최소 인터페이스 — 세 경로 모두 흘려 읽기(목록은 모델 경로 `build_premium_history` 만)."""

    def query_premium(
        self, *, dom: str, fx: str, base: str | None, start: int, stop: int
    ) -> list[PremiumRow]: ...

    def stream_premium(
        self, *, dom: str, fx: str, base: str | None, start: int, stop: int
    ) -> Iterator[tuple[str, str, int, float]]: ...


class EventReader(Protocol):
    """`/history/events` 가 쓰는 저장소 읽기 — 013 사건 점."""

    def query_premium_events(
        self,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        dir: str | None = None,
        base: str | None = None,
    ) -> list[EventListRow]: ...


class CandleReader(Protocol):
    """`/history/candles` 가 쓰는 저장소 읽기 — 014 계층 버킷 하나."""

    def query_candles(
        self,
        bucket: str,
        *,
        start: int,
        stop: int,
        dom: str | None = None,
        fx: str | None = None,
        base: str | None = None,
    ) -> list[CandleRow]: ...


class HistoryApiError(Exception):
    """라우터가 `{"error": {code, message, detail}}` 로 변환한다."""

    def __init__(
        self, http_status: int, code: str, message: str, detail: object = None
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.detail = detail


def _kst(ts: int) -> str:
    """epoch 초 → KST ISO 8601 (+09:00 으로 끝난다)."""
    return datetime.fromtimestamp(ts, tz=KST).isoformat()


def _utc_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_ms() -> int:
    return int(time.time() * 1000)


# ── 응답 인코딩 (005 §3.4·013 §3.4·014 §3.6, 2026-09-28) ──────────────────────
# 조회·빌드·camelCase·JSON 인코딩을 한 함수가 끝내 라우터가 통째로 스레드에 넘긴다 — 이벤트 루프에는 bytes 만
# 돌아온다. 큰 목록(사건·premium 컴팩트 events)은 모델 없이 camelCase dict 로 만들고 ENCODE_CHUNK 건씩 인코딩해
# 이어 붙인다 — json.dumps 한 번이 GIL 을 수십~수백 ms 쥐면 그동안 루프(수집 박스는 거래소 수신·틱)가 멈춘다.

ENCODE_CHUNK = 2000


def render_json(content: object) -> bytes:
    """`JSONResponse` 와 같은 인코딩 — 공백 없음·ensure_ascii 끔·NaN 거부가 같아야 모델 경로와 바이트가 같다."""
    return json.dumps(
        content, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _render_with_list(
    head: dict, key: str, items: list[dict], tail: dict | None = None
) -> bytes:
    """`{head…, key: [items…], tail…}` — 키 순서는 모델 필드 순서 그대로, 목록만 조각으로 인코딩한다."""
    parts = [
        render_json(items[i : i + ENCODE_CHUNK])[1:-1]
        for i in range(0, len(items), ENCODE_CHUNK)
    ]
    return _assemble(head, key, parts, tail)


def _assemble(head: dict, key: str, parts: list[bytes], tail: dict | None) -> bytes:
    """이미 인코딩한 목록 조각(각각 `[`·`]` 를 뗀 것)을 머리·꼬리와 한 번에 잇는다 — 응답 크기의 사본을 하나만 만든다."""
    pieces = [render_json(head)[:-1], b',"' + key.encode() + b'":[']
    for i, part in enumerate(parts):
        if i:
            pieces.append(b",")
        pieces.append(part)
    pieces.append(b"]}" if tail is None else b"]," + render_json(tail)[1:])
    return b"".join(pieces)


def encode_model(model: BaseModel) -> bytes:
    """모델 응답(streaks·bulk)의 바이트 — 라우터가 빌드와 함께 스레드에서 부른다."""
    return render_json(camelize_json(model.model_dump()))


# ── /history/premium ───────────────────────────────────────────────────────


def week_bounds(day: date) -> tuple[datetime, datetime]:
    """`day` 가 속한 ISO 주(월 00:00 UTC ~ 다음 월), end exclusive."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(
        days=day.weekday()
    )
    return start, start + timedelta(days=7)


def _premium_window(
    unit: Literal["week", "month"], date_str: str | None
) -> tuple[datetime, datetime]:
    """검증(400)과 구간 — 두 경로 공용. 저장소를 읽기 전에 끝난다."""
    if unit != "week":
        # 한 번에 1주까지 (§3.4, 2026-09-28 사람 결정) — 달 전체(≈250만 점)는 조회 한 번이 api 메모리를 넘긴다
        raise HistoryApiError(
            400,
            "invalid_request",
            f"unit={unit} 는 받지 않습니다 — 한 번에 1주(unit=week)까지입니다.",
        )
    if date_str is None:
        day = datetime.now(UTC).date()
    else:
        # fromisoformat 은 3.11+ 에서 20260828·2026-W35-4 같은 변형도 받으므로 형식을 먼저 고정한다
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            raise HistoryApiError(
                400,
                "invalid_request",
                f"date 형식이 잘못됐습니다: {date_str!r} (YYYY-MM-DD)",
            )
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            raise HistoryApiError(
                400,
                "invalid_request",
                f"date 형식이 잘못됐습니다: {date_str!r} (YYYY-MM-DD)",
            ) from None
        if not 1970 <= day.year <= 2100:
            # week_bounds 의 날짜 연산이 넘치지 않는 안전 범위 — 밖이면 형식 오류와 같은 400
            raise HistoryApiError(
                400,
                "invalid_request",
                f"date 는 1970~2100 범위여야 합니다: {date_str!r}",
            )
    return week_bounds(day)


def _no_premium(
    *, dom: str, fx: str, base: str, unit: str, start_dt: datetime, end_dt: datetime
) -> HistoryApiError:
    return HistoryApiError(
        404,
        "market_data_not_found",
        f"{base} 의 {unit} 구간({_utc_z(start_dt)} ~ {_utc_z(end_dt)})에 기록이 없습니다.",
        {"dom": dom, "fx": fx, "base": base},
    )


def build_premium_history(
    reader: PremiumReader,
    *,
    dom: str,
    fx: str,
    base: str,
    unit: Literal["week", "month"],
    date_str: str | None,
) -> PremiumHistoryResponse:
    """모델 경로 — 점 목록(pivot 한 행, 반쪽 점 제외)으로 만든다. 응답 바이트 경로가 이것과 같은지 테스트가 본다."""
    start_dt, end_dt = _premium_window(unit, date_str)
    rows = reader.query_premium(
        dom=dom,
        fx=fx,
        base=base.upper(),
        start=int(start_dt.timestamp()),
        stop=int(end_dt.timestamp()),
    )
    if not rows:
        raise _no_premium(
            dom=dom,
            fx=fx,
            base=base.upper(),
            unit=unit,
            start_dt=start_dt,
            end_dt=end_dt,
        )
    events: list[PremiumEvent] = []
    prev_ts = rows[0].ts
    for row in rows:
        events.append(PremiumEvent(dt=row.ts - prev_ts, fwd=row.fwd, rev=row.rev))
        prev_ts = row.ts
    fwds = [r.fwd for r in rows]
    return PremiumHistoryResponse(
        dom=dom,
        fx=fx,
        base=base.upper(),
        unit=unit,
        start=_utc_z(start_dt),
        end=_utc_z(end_dt),
        first_ts=rows[0].ts,
        count=len(rows),
        summary=PremiumSummary(
            first_fwd=fwds[0], last_fwd=fwds[-1], min_fwd=min(fwds), max_fwd=max(fwds)
        ),
        events=events,
        fetched_at=_now_ms(),
    )


def encode_premium_history(
    reader: PremiumReader,
    *,
    dom: str,
    fx: str,
    base: str,
    unit: Literal["week", "month"],
    date_str: str | None,
) -> bytes:
    """`build_premium_history` 와 같은 응답 바이트 — 점 목록을 만들지 않고 흘려 읽는다 (005 §3.4, 2026-09-28).

    저장소가 방향(fwd·rev) 줄기마다 시각 오름차순으로 흘려보내는 원값을 방향마다 시각·값 배열(8바이트씩)에만
    담는다 — 줄기 순서는 정해지지 않아 두 줄기를 다 받은 뒤, 시각이 같은 점끼리 이어 컴팩트 events 와 summary 를
    만들며 ENCODE_CHUNK 건씩 인코딩한다. 한 방향에만 있는 시각(반쪽 점)은 건너뛴다 — 목록 경로가 pivot 한 행에서
    반쪽 행을 버리는 것과 같은 결과다. summary 의 최소·최대는 내장 min·max 와 같이 처음 만난 값을 지킨다(음의 0).
    """
    start_dt, end_dt = _premium_window(unit, date_str)
    b = base.upper()
    series = {"fwd": (array("q"), array("d")), "rev": (array("q"), array("d"))}
    for _, field, ts, value in reader.stream_premium(
        dom=dom,
        fx=fx,
        base=b,
        start=int(start_dt.timestamp()),
        stop=int(end_dt.timestamp()),
    ):
        ts_arr, val_arr = series[field]
        ts_arr.append(ts)
        val_arr.append(value)
    fts, fvs = series["fwd"]
    rts, rvs = series["rev"]
    nf, nr = len(fts), len(rts)
    parts: list[bytes] = []
    chunk: list[dict] = []
    append = chunk.append
    count = i = j = 0
    first_ts = prev_ts = 0
    first_fwd = last_fwd = min_fwd = max_fwd = 0.0
    while i < nf and j < nr:
        ts = fts[i]
        rt = rts[j]
        if ts < rt:
            i += 1
            continue
        if rt < ts:
            j += 1
            continue
        fwd = fvs[i]
        rev = rvs[j]
        i += 1
        j += 1
        if count == 0:
            first_ts = prev_ts = ts
            first_fwd = min_fwd = max_fwd = fwd
        elif fwd < min_fwd:
            min_fwd = fwd
        elif fwd > max_fwd:
            max_fwd = fwd
        append({"dt": ts - prev_ts, "fwd": fwd, "rev": rev})
        prev_ts = ts
        last_fwd = fwd
        count += 1
        if len(chunk) == ENCODE_CHUNK:
            parts.append(render_json(chunk)[1:-1])
            chunk.clear()
    if chunk:
        parts.append(render_json(chunk)[1:-1])
    # 배열은 이음이 끝나면 쓸 일이 없다 — 응답 크기 사본을 만드는 마지막 잇기 전에 놓는다
    del series, fts, fvs, rts, rvs
    if count == 0:
        raise _no_premium(
            dom=dom, fx=fx, base=b, unit=unit, start_dt=start_dt, end_dt=end_dt
        )
    head = {
        "dom": dom,
        "fx": fx,
        "base": b,
        "unit": unit,
        "start": _utc_z(start_dt),
        "end": _utc_z(end_dt),
        "firstTs": first_ts,
        "count": count,
        "summary": {
            "firstFwd": first_fwd,
            "lastFwd": last_fwd,
            "minFwd": min_fwd,
            "maxFwd": max_fwd,
        },
    }
    return _assemble(head, "events", parts, {"fetchedAt": _now_ms()})


# ── streaks ────────────────────────────────────────────────────────────────
# 점 목록을 만들지 않는다 — 저장소가 (코인, 방향) 줄기마다 시각 오름차순으로 흘려보내는 점을 줄기별 상태기계가
# 받아 구간을 센다(005 §3.4). 메모리는 구간 수에 비례한다. 평균은 CPython 3.12 의 `sum()`(float 는 Neumaier 보정
# 합)과 한 걸음씩 같은 계산이라, 목록을 만들어 `sum(values) / len(values)` 한 것과 비트까지 같다.


class _Run:
    """한 (코인, 방향) 줄기의 상태 — 열린 구간 하나(시작·끝·표본·최대·보정 합)와 줄기 전체의 수·합·최대·마지막 시각."""

    __slots__ = (
        "threshold",
        "max_gap",
        "segments",
        "prev_ts",
        "open_start",
        "open_end",
        "open_n",
        "open_max",
        "open_sum",
        "open_comp",
        "n",
        "total",
        "comp",
        "peak",
    )

    def __init__(self, threshold: float, max_gap: int) -> None:
        self.threshold = threshold
        self.max_gap = max_gap
        self.segments: list[Segment] = []
        self.prev_ts: int | None = None
        self.open_start: int | None = None
        self.open_end = 0
        self.open_n = 0
        self.open_max = 0.0
        self.open_sum = 0.0
        self.open_comp = 0.0
        self.n = 0
        self.total = 0.0
        self.comp = 0.0
        self.peak = 0.0

    @property
    def last_ts(self) -> int:
        """마지막 점의 시각 — 줄기는 점이 들어온 뒤에만 생긴다."""
        return self.prev_ts or 0

    def feed(self, ts: int, value: float) -> None:
        # 줄기 전체(overall — 기준치 무관): 수·보정 합·최대. 점마다 도는 곳이라 `_neumaier` 를 풀어 쓴다
        if self.n == 0:
            self.total, self.comp, self.peak = 0.0 + value, 0.0, value
        else:
            total = self.total
            t = total + value
            if abs(total) >= abs(value):
                self.comp += (total - t) + value
            else:
                self.comp += (value - t) + total
            self.total = t
            if value > self.peak:
                self.peak = value
        self.n += 1
        # 규칙 2 — 직전 기록(값과 무관)과 maxGap 초보다 벌어지면 닫는다
        if self.prev_ts is not None and ts - self.prev_ts > self.max_gap:
            self.close()
        # 규칙 1 — threshold 이상인 연속 기록을 한 구간으로
        if value >= self.threshold:
            if self.open_start is None:
                self.open_start, self.open_n = ts, 1
                self.open_max, self.open_sum, self.open_comp = value, 0.0 + value, 0.0
            else:
                self.open_n += 1
                self.open_sum, self.open_comp = _neumaier(
                    self.open_sum, self.open_comp, value
                )
                if value > self.open_max:
                    self.open_max = value
            self.open_end = ts
        else:
            self.close()
        self.prev_ts = ts

    def close(self) -> None:
        start_ts = self.open_start
        if start_ts is None:
            return
        end_ts = self.open_end
        self.segments.append(
            Segment(
                start_ts=start_ts,
                end_ts=end_ts,
                start=_kst(start_ts),
                end=_kst(end_ts),
                duration_seconds=end_ts - start_ts,
                samples=self.open_n,
                max_percent=self.open_max,
                avg_percent=_sum_result(self.open_sum, self.open_comp) / self.open_n,
            )
        )
        self.open_start = None

    def mean(self) -> float:
        return _sum_result(self.total, self.comp) / self.n


def _neumaier(total: float, comp: float, value: float) -> tuple[float, float]:
    """CPython 3.12 `sum()` 의 float 한 걸음 — (합, 보정) 을 돌려준다."""
    t = total + value
    if abs(total) >= abs(value):
        comp += (total - t) + value
    else:
        comp += (value - t) + total
    return t, comp


def _sum_result(total: float, comp: float) -> float:
    """`sum()` 의 끝 — 보정이 0 이 아니고 유한할 때만 더한다(음의 결과의 부호·무한대를 지키려고)."""
    return total + comp if comp and math.isfinite(comp) else total


def _stream_runs(
    points: Iterable[tuple[str, str, int, float]], threshold: float, max_gap: int
) -> dict[tuple[str, str], _Run]:
    """`(base, field, ts, value)` 흐름 → (코인, 방향) 줄기마다 상태. 같은 줄기의 점이 이어서 오는 동안은 사전을 찾지 않는다."""
    runs: dict[tuple[str, str], _Run] = {}
    cur_base = cur_field = ""
    run: _Run | None = None
    for base, fld, ts, value in points:
        if run is None or base != cur_base or fld != cur_field:
            cur_base, cur_field = base, fld
            run = runs.get((base, fld))
            if run is None:
                run = runs[(base, fld)] = _Run(threshold, max_gap)
        run.feed(ts, value)
    for r in runs.values():
        r.close()
    return runs


def _direction_summary(segments: list[Segment]) -> DirectionSummary:
    if not segments:
        return DirectionSummary(
            count=0,
            max_duration_seconds=0,
            avg_duration_seconds=0.0,
            max_percent=0.0,
            avg_percent=0.0,
            segments=[],
        )
    total_samples = sum(s.samples for s in segments)
    weighted = sum(s.avg_percent * s.samples for s in segments)
    return DirectionSummary(
        count=len(segments),
        max_duration_seconds=max(s.duration_seconds for s in segments),
        avg_duration_seconds=sum(s.duration_seconds for s in segments) / len(segments),
        max_percent=max(s.max_percent for s in segments),
        avg_percent=weighted / total_samples,
        segments=segments,
    )


def _streak_parts(
    fwd: _Run, rev: _Run
) -> tuple[DirectionSummary, DirectionSummary, Overall]:
    """두 줄기 → (kimp, reverse, overall). fwd/rev 를 절댓값 없이 각각 계산한다."""
    union = fwd.segments + rev.segments
    overall = Overall(
        max_kimp_percent=fwd.peak,
        avg_kimp_percent=fwd.mean(),
        max_reverse_percent=rev.peak,
        avg_reverse_percent=rev.mean(),
        max_duration_seconds=max((s.duration_seconds for s in union), default=0),
        avg_duration_seconds=(
            sum(s.duration_seconds for s in union) / len(union) if union else 0.0
        ),
        segment_count=len(union),
    )
    return _direction_summary(fwd.segments), _direction_summary(rev.segments), overall


# start 미지정 시 조회 창과 창 상한 (§3.4, 2026-09-28 사람 결정) — streaks 7일, bulk 1시간.
# 넘으면 400. start 가 없으면 상한만큼(candles 의 1,440창 상한과 같은 방식)
STREAKS_MAX_WINDOW_SEC = 7 * 86_400
BULK_MAX_WINDOW_SEC = 3_600


def _streaks_window(
    start: int | None, end: int | None, *, limit: int, path: str
) -> tuple[int, int]:
    """(start, end) — `end` 없으면 지금+1초, `start` 없으면 `end − 상한`(음수는 0), `end ≤ start`·상한 초과는 400."""
    end_eff = end if end is not None else int(time.time()) + 1
    if start is not None and end_eff <= start:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"end({end_eff})가 start({start}) 이하입니다.",
        )
    # Flux range 가 epoch 이전을 못 받는다 — 음수는 0 으로
    start_eff = start if start is not None else max(0, end_eff - limit)
    if end_eff - start_eff > limit:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"window exceeds limit: {path} 는 한 번에 {limit}초까지입니다.",
            {"limitSec": limit},
        )
    return start_eff, end_eff


def build_streaks(
    reader: PremiumReader,
    *,
    dom: str,
    fx: str,
    base: str,
    threshold: float,
    start: int | None,
    end: int | None,
    max_gap: int,
) -> StreaksResponse:
    if end is not None and end <= 0:
        # start 기본값(첫 ts ≥ 0)보다 항상 작거나 같다 — end ≤ start 규칙의 특수형
        raise HistoryApiError(400, "invalid_request", f"end({end})가 0 이하입니다.")
    start_eff, end_eff = _streaks_window(
        start, end, limit=STREAKS_MAX_WINDOW_SEC, path="/history/streaks"
    )
    base_u = base.upper()
    runs = _stream_runs(
        reader.stream_premium(
            dom=dom, fx=fx, base=base_u, start=start_eff, stop=end_eff
        ),
        threshold,
        max_gap,
    )
    fwd, rev = runs.get((base_u, "fwd")), runs.get((base_u, "rev"))
    if fwd is None or rev is None:
        raise HistoryApiError(
            404,
            "market_data_not_found",
            f"{base_u} 의 기록이 없습니다.",
            {"dom": dom, "fx": fx, "base": base_u},
        )
    kimp, reverse, overall = _streak_parts(fwd, rev)
    last_ts = fwd.last_ts
    return StreaksResponse(
        base=base_u,
        dom=dom,
        fx=fx,
        threshold_percent=threshold,
        max_gap_seconds=max_gap,
        start_ts=start_eff,
        end_ts=end_eff,
        kimp=kimp,
        reverse=reverse,
        overall=overall,
        scanned=fwd.n,
        last_updated_ts=last_ts,
        last_updated=_kst(last_ts),
        fetched_at=_now_ms(),
    )


def build_bulk(
    reader: PremiumReader,
    *,
    dom: str,
    fx: str,
    threshold: float,
    start: int | None,
    end: int | None,
    max_gap: int,
) -> BulkResponse:
    start_eff, end_eff = _streaks_window(
        start, end, limit=BULK_MAX_WINDOW_SEC, path="/history/streaks/bulk"
    )
    runs = _stream_runs(
        reader.stream_premium(dom=dom, fx=fx, base=None, start=start_eff, stop=end_eff),
        threshold,
        max_gap,
    )
    coins: list[BulkCoin] = []
    for base in sorted({b for b, _ in runs}):
        fwd, rev = runs.get((base, "fwd")), runs.get((base, "rev"))
        if fwd is None or rev is None:
            continue  # 한쪽 방향뿐인 코인은 반쪽 점뿐이다 — 기록 없음과 같다
        kimp, reverse, overall = _streak_parts(fwd, rev)
        coins.append(
            BulkCoin(
                base=base,
                scanned=fwd.n,
                last_ts=fwd.last_ts,
                kimp=kimp,
                reverse=reverse,
                overall=overall,
            )
        )
    # 기록 없으면 404 가 아니라 빈 coins (§3.4)
    return BulkResponse(
        dom=dom,
        fx=fx,
        threshold_percent=threshold,
        max_gap_seconds=max_gap,
        start_ts=start_eff,
        end_ts=end_eff,
        coin_count=len(coins),
        coins=coins,
        fetched_at=_now_ms(),
    )


# /history/events 의 start 미지정 시 조회 창 (013 §3.4) — 최근 7일
DEFAULT_WINDOW_SEC = 7 * 86_400


def default_start(start: int | None, end_eff: int) -> int:
    """start 가 없으면 end − 7일 (음수는 0 으로 — Flux range 가 epoch 이전을 못 받는다)."""
    if start is not None:
        return start
    return max(0, end_eff - DEFAULT_WINDOW_SEC)


# ── /history/events (스펙 013 §3.4) ─────────────────────────────────────────


def build_events(
    reader: EventReader,
    open_events: list[OpenEvent],
    *,
    start: int | None,
    end: int | None,
    dom: str | None,
    dir: str | None,
    base: str | None,
    now_sec: int | None = None,
) -> EventsResponse:
    """Influx 의 닫힌 사건 + 메모리의 진행 중 사건. 구간 판정은 start_ts 기준(`start ≤ start_ts < end`)."""
    now = now_sec if now_sec is not None else int(time.time())
    start_eff, end_eff = _events_window(start, end, now)
    base_u = base.upper() if base is not None else None
    rows = reader.query_premium_events(
        start=start_eff, stop=end_eff, dom=dom, dir=dir, base=base_u
    )
    by_key: dict[tuple[str, str, str, str, int], EventOut] = {}
    for r in rows:
        # 고아 점(end_ts 0 인데 메모리에 없음)은 복원 규칙과 같이 last_ts 에서 끝난 것으로 —
        # 아래에서 메모리의 진행 중 사건이 같은 키를 덮는다
        end_ts = r.end_ts or r.last_ts
        by_key[(r.dom, r.fx, r.base, r.dir, r.start_ts)] = EventOut(
            base=r.base,
            dom=r.dom,
            fx=r.fx,
            dir=r.dir,  # type: ignore[arg-type]
            start_ts=r.start_ts,
            end_ts=end_ts,
            duration_seconds=end_ts - r.start_ts,
            ongoing=False,
            max_percent=r.max_percent,
            max_ts=r.max_ts,
            samples=r.samples,
            net_dom=r.net_dom,
            net_fx=r.net_fx,
        )
    for ev in _open_in_window(open_events, dom, dir, base_u, start_eff, end_eff, now):
        by_key[(ev.dom, ev.fx, ev.base, ev.dir, ev.start_ts)] = EventOut(
            base=ev.base,
            dom=ev.dom,
            fx=ev.fx,
            dir=ev.dir,  # type: ignore[arg-type]
            start_ts=ev.start_ts,
            end_ts=None,
            duration_seconds=now - ev.start_ts,
            ongoing=True,
            max_percent=ev.max_percent,
            max_ts=ev.max_ts,
            samples=ev.samples,
            net_dom=ev.net_dom,
            net_fx=ev.net_fx,
        )
    events = sorted(
        by_key.values(), key=lambda e: (-e.start_ts, e.base, e.dom, e.fx, e.dir)
    )
    return EventsResponse(
        start_ts=start_eff,
        end_ts=end_eff,
        count=len(events),
        fetched_at=_now_ms(),
        events=events,
    )


def _events_window(start: int | None, end: int | None, now: int) -> tuple[int, int]:
    """`end` 없으면 지금+1초, `start` 없으면 `end − 7일`, `end ≤ start` 400 — 두 경로 공용."""
    end_eff = end if end is not None else now + 1
    if start is not None and end_eff <= start:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"end({end_eff})가 start({start}) 이하입니다.",
        )
    return default_start(start, end_eff), end_eff


def _open_in_window(
    open_events: list[OpenEvent],
    dom: str | None,
    dir: str | None,
    base_u: str | None,
    start_eff: int,
    end_eff: int,
    now: int,
) -> Iterator[OpenEvent]:
    """응답에 실을 진행 중 사건 — 필터·구간(start_ts 기준)·열린 지 60초 초과. 두 경로 공용."""
    for ev in open_events:
        if dom is not None and ev.dom != dom:
            continue
        if dir is not None and ev.dir != dir:
            continue
        if base_u is not None and ev.base != base_u:
            continue
        if not (start_eff <= ev.start_ts < end_eff):
            continue
        if now - ev.start_ts <= MIN_DURATION_SEC:
            continue  # 열린 지 60초를 넘긴 것만 — 1분 못 넘길 스파이크는 아직 사건이 아니다
        yield ev


def encode_events(
    reader: EventReader,
    open_events: list[OpenEvent],
    *,
    start: int | None,
    end: int | None,
    dom: str | None,
    dir: str | None,
    base: str | None,
    now_sec: int | None = None,
) -> bytes:
    """`build_events` 와 같은 응답 바이트 — 사건마다 모델을 만들지 않고 camelCase dict 로(역프 7일 5만여 건)."""
    now = now_sec if now_sec is not None else int(time.time())
    start_eff, end_eff = _events_window(start, end, now)
    base_u = base.upper() if base is not None else None
    rows = reader.query_premium_events(
        start=start_eff, stop=end_eff, dom=dom, dir=dir, base=base_u
    )
    by_key: dict[tuple[str, str, str, str, int], dict] = {}
    for r in rows:
        # 고아 점은 build_events 와 같이 last_ts 에서 끝난 것으로 — 진행 중 사건이 같은 키를 덮는다
        end_ts = r.end_ts or r.last_ts
        by_key[(r.dom, r.fx, r.base, r.dir, r.start_ts)] = {
            "base": r.base,
            "dom": r.dom,
            "fx": r.fx,
            "dir": r.dir,
            "startTs": r.start_ts,
            "endTs": end_ts,
            "durationSeconds": end_ts - r.start_ts,
            "ongoing": False,
            "maxPercent": r.max_percent,
            "maxTs": r.max_ts,
            "samples": r.samples,
            "netDom": r.net_dom,
            "netFx": r.net_fx,
        }
    for ev in _open_in_window(open_events, dom, dir, base_u, start_eff, end_eff, now):
        by_key[(ev.dom, ev.fx, ev.base, ev.dir, ev.start_ts)] = {
            "base": ev.base,
            "dom": ev.dom,
            "fx": ev.fx,
            "dir": ev.dir,
            "startTs": ev.start_ts,
            "endTs": None,
            "durationSeconds": now - ev.start_ts,
            "ongoing": True,
            "maxPercent": ev.max_percent,
            "maxTs": ev.max_ts,
            "samples": ev.samples,
            "netDom": ev.net_dom,
            "netFx": ev.net_fx,
        }
    events = sorted(
        by_key.values(),
        key=lambda e: (-e["startTs"], e["base"], e["dom"], e["fx"], e["dir"]),
    )
    head = {
        "startTs": start_eff,
        "endTs": end_eff,
        "count": len(events),
        "fetchedAt": _now_ms(),
    }
    return _render_with_list(head, "events", events)


# ── /history/candles ───────────────────────────────────────────────────────


def _tri_to_bool(v: int) -> bool | None:
    """저장값 1/0/−1 → true/false/null (014 §3.6)."""
    return None if v < 0 else bool(v)


def build_candles(
    reader: CandleReader,
    *,
    base: str,
    res: str,
    dom: str,
    fx: str,
    dir: str,
    start: int | None,
    end: int | None,
    now_sec: int | None = None,
) -> CandlesResponse:
    """계층 버킷 하나에서 `start ≤ 창 시작 < end` 인 봉 — 진행 중인 창은 없다(닫힌 창만 저장되므로).

    상한 = 1,440 × 창 길이. `end − start` 가 상한을 넘으면 400 — 실수로 수십만 점을 읽는 호출이 Influx 를 못 건드리게.
    """
    tier, start_eff, end_eff = _candles_window(res, start, end, now_sec)
    base_u = base.upper()
    rows = reader.query_candles(
        tier.bucket, start=start_eff, stop=end_eff, dom=dom, fx=fx, base=base_u
    )
    kimp = dir == "kimp"
    candles = [
        CandleOut(
            ts=r.ts,
            open=r.fwd_o if kimp else r.rev_o,
            high=r.fwd_h if kimp else r.rev_h,
            low=r.fwd_l if kimp else r.rev_l,
            close=r.fwd_c if kimp else r.rev_c,
            krw=r.krw,
            usdt=r.usdt,
            fx_rate=r.rate,
            # 방향 경로의 두 끝 — 김프는 해외 출금 → 국내 입금, 역프는 국내 출금 → 해외 입금
            deposit_ok=_tri_to_bool(r.dom_dep if kimp else r.fx_dep),
            withdraw_ok=_tri_to_bool(r.fx_wd if kimp else r.dom_wd),
            # 거래소별 4상태 — 차트가 거래소마다 입금·출금 줄을 따로 그린다(015). 경로 밖 칸도 값이 있어야 "모름" 이 안 뜬다
            dom_deposit_ok=_tri_to_bool(r.dom_dep),
            dom_withdraw_ok=_tri_to_bool(r.dom_wd),
            fx_deposit_ok=_tri_to_bool(r.fx_dep),
            fx_withdraw_ok=_tri_to_bool(r.fx_wd),
            blocked_sec=r.blocked_fwd_sec if kimp else r.blocked_rev_sec,
            samples=r.samples,
            net_dom=r.net_dom,
            net_fx=r.net_fx,
        )
        for r in sorted(rows, key=lambda r: r.ts)
    ]
    return CandlesResponse(
        base=base_u,
        res=res,  # type: ignore[arg-type]
        dom=dom,
        fx=fx,
        dir=dir,  # type: ignore[arg-type]
        start_ts=start_eff,
        end_ts=end_eff,
        count=len(candles),
        fetched_at=_now_ms(),
        candles=candles,
    )


def _candles_window(
    res: str, start: int | None, end: int | None, now_sec: int | None
) -> tuple[Tier, int, int]:
    """(계층, start, end) — 상한 = 1,440 × 창 길이, 넘으면·`end ≤ start` 면 400. 두 경로 공용."""
    tier = TIER_BY_RES[res]
    limit = limit_sec(tier)
    end_eff = (
        end
        if end is not None
        else (now_sec if now_sec is not None else int(time.time()))
    )
    start_eff = start if start is not None else max(0, end_eff - limit)
    if end_eff <= start_eff:
        raise HistoryApiError(
            400, "invalid_request", f"end({end_eff})가 start({start_eff}) 이하입니다."
        )
    if end_eff - start_eff > limit:
        raise HistoryApiError(
            400,
            "invalid_request",
            f"window exceeds limit: {res} 은 한 번에 {limit}초(1,440창)까지입니다.",
            {"limitSec": limit},
        )
    return tier, start_eff, end_eff


class _Tri(dict[int, bool | None]):
    """저장값 → 3상태 조회표. −1·0·1 밖의 값은 `_tri_to_bool` 과 같은 규칙(음수 null·양수 true)으로."""

    def __missing__(self, v: int) -> bool | None:
        return _tri_to_bool(v)


_TRI = _Tri({1: True, 0: False, -1: None})

# 방향별로 읽는 필드 — (시가, 고가, 저가, 종가, 경로 입금 끝, 경로 출금 끝, 막힌 초). 김프 경로는 해외 출금 →
# 국내 입금, 역프는 국내 출금 → 해외 입금 (014 §3.6)
_DIR_FIELDS = {
    "kimp": attrgetter(
        "fwd_o", "fwd_h", "fwd_l", "fwd_c", "dom_dep", "fx_wd", "blocked_fwd_sec"
    ),
    "reverse": attrgetter(
        "rev_o", "rev_h", "rev_l", "rev_c", "fx_dep", "dom_wd", "blocked_rev_sec"
    ),
}


def encode_candles(
    reader: CandleReader,
    *,
    base: str,
    res: str,
    dom: str,
    fx: str,
    dir: str,
    start: int | None,
    end: int | None,
    now_sec: int | None = None,
) -> bytes:
    """`build_candles` 와 같은 응답 바이트 — 봉마다 모델을 만들지 않고 방향별 필드를 camelCase dict 로."""
    tier, start_eff, end_eff = _candles_window(res, start, end, now_sec)
    base_u = base.upper()
    rows = reader.query_candles(
        tier.bucket, start=start_eff, stop=end_eff, dom=dom, fx=fx, base=base_u
    )
    pick = _DIR_FIELDS["kimp" if dir == "kimp" else "reverse"]
    tri = _TRI
    candles: list[dict] = []
    append = candles.append
    for r in sorted(rows, key=attrgetter("ts")):
        o, h, lo, c, dep, wd, blocked = pick(r)
        append(
            {
                "ts": r.ts,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "krw": r.krw,
                "usdt": r.usdt,
                "fxRate": r.rate,
                "depositOk": tri[dep],
                "withdrawOk": tri[wd],
                "domDepositOk": tri[r.dom_dep],
                "domWithdrawOk": tri[r.dom_wd],
                "fxDepositOk": tri[r.fx_dep],
                "fxWithdrawOk": tri[r.fx_wd],
                "blockedSec": blocked,
                "samples": r.samples,
                "netDom": r.net_dom,
                "netFx": r.net_fx,
            }
        )
    return render_json(
        {
            "base": base_u,
            "res": res,
            "dom": dom,
            "fx": fx,
            "dir": dir,
            "startTs": start_eff,
            "endTs": end_eff,
            "count": len(candles),
            "fetchedAt": _now_ms(),
            "candles": candles,
        }
    )
