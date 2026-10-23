"""과거 김프/역프 사건 백필 — Influx `premium` 을 운영 사건 감지기로 재생해 `premium_event` 에 (스펙 084).

실행 (server/ 디렉토리에서, INFLUX_URL·INFLUX_TOKEN — 운영은 084 §3.6 의 data 박스 일회용 컨테이너):
    python -m scripts.premium_events_backfill [BASE ...]                      # dry-run — 사건 수만 센다
    python -m scripts.premium_events_backfill --write [BASE ...]              # 경계 C 전 사건을 쓴다
    python -m scripts.premium_events_backfill --verify START STOP [BASE ...]  # 운영 사건과 맞춰 보기만
코인 목록이 없으면 경계 전에 점이 있는 코인 전부(검증은 그 창에 운영 사건이 많은 30종 + BTC·ETH).

판정은 운영 감지기(`core/premium_events.py`)를 그대로 쓴다. 저장된 점 하나 = 그 초의 틱 행 하나, 앞 틱과
1초보다 벌어지면 1초 전에 행 없는 틱을 넣어 결측 판정을 운영(매초 틱)과 같게 한다(§3.3). 닫힌 사건만 쓰고,
같은 키 덮어쓰기라 다시 돌려도 같은 점이다(§3.5). 실패하면 exit 1 — 다시 돌리면 된다.
"""

import argparse
import asyncio
import logging
import math
import resource
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.influx import (
    EventListRow,
    InfluxClient,
    InfluxPoint,
    premium_event_point,
)
from app.core.models import Tick, TickRow
from app.core.premium_events import MAX_GAP_SEC, PremiumEventDetector

DAY = 86_400
# §3.2 경계 C — 운영 감지기의 첫 틱(2026-09-07T09:20:08Z). 백필 사건은 전부 이 전에 시작해 이 전에 끝난다.
# 고정값이다 — 백필 뒤엔 가장 이른 `premium_event` 가 백필 점이라 다시 셀 수 없다
CUTOFF = 1_788_772_808
# 감지기의 점을 받아 가는 재생 시각 간격 — 미전송 상한(1,000)에 닿지 않게 (§3.3)
DRAIN_SEC = 600
# 한 조회의 fwd 점 수 상한 (§3.6)
CHUNK_POINTS = 200_000
# 쓰기 1회 점 수 (§3.5)
WRITE_BATCH = 5_000
# 쉬기 — 조회가 걸린 만큼(최소 0.2초), 쓰기 1회마다 0.5초 (§3.6)
QUERY_SLEEP_RATIO = 1.0
QUERY_SLEEP_MIN = 0.2
WRITE_SLEEP = 0.5
# 실패는 10초 간격으로 3번 다시 (§3.6)
RETRIES = 3
RETRY_SLEEP = 10.0
# 검증 (§3.7) — 결측 닫힘은 마지막 관측 601초 뒤에 정해지므로 창 끝에서 이만큼 앞의 사건까지만 센다
VERIFY_TAIL_SEC = 610
# 창 시작 전에 열린 운영 사건을 찾는 거리
VERIFY_LOOKBACK_SEC = 30 * DAY
VERIFY_TOP = 30
VERIFY_ALWAYS = ("BTC", "ETH")

Pair = tuple[str, str]  # (dom, fx)
Key = tuple[str, str, str, str, int]  # (dom, fx, base, dir, start_ts)


# ── 순수 계산 (네트워크 없음 — 테스트 대상) ────────────────────────────────


def build_ticks(
    base: str, points: Iterable[tuple[str, str, str, int, float]]
) -> list[Tick]:
    """`(dom, fx, field, ts, value)` 점 → 시각 오름차순 틱. 같은 초의 쌍들이 한 틱의 행이고 반쪽 점은 건너뛴다.

    점이 오는 순서는 상관없다(Influx 는 줄기마다 이어서 준다). 행은 (dom, fx) 순 — 감지기 상태가 조합마다
    따로라 행 순서는 결과를 바꾸지 않지만 같은 입력에 같은 틱이 되게.
    """
    pairs: dict[Pair, Pair] = {}  # 같은 문자열 쌍을 하나로 — 줄마다 새 문자열이 오므로
    by_ts: dict[int, dict[Pair, list[float | None]]] = {}
    for dom, fx, fld, ts, value in points:
        pair = pairs.get((dom, fx))
        if pair is None:
            pair = pairs.setdefault((dom, fx), (dom, fx))
        slot = by_ts.setdefault(ts, {}).setdefault(pair, [None, None])
        slot[0 if fld == "fwd" else 1] = value
    ticks: list[Tick] = []
    for ts in sorted(by_ts):
        rows = tuple(
            TickRow(dom, fx, base, fwd, rev)
            for (dom, fx), (fwd, rev) in sorted(by_ts[ts].items())
            if fwd is not None and rev is not None
        )
        if rows:
            ticks.append(Tick(ts=ts, rows=rows, dw_failed=()))
    return ticks


def plan_chunks(
    days: Iterable[tuple[int, int]], start: int, stop: int, budget: int = CHUNK_POINTS
) -> list[tuple[int, int]]:
    """코인 하나의 날별 점 수 `(날 시작, fwd 점 수)` → 조회 조각 `[a, b)` 들 (§3.6).

    이웃한 날을 `budget` 까지 묶고(사이의 빈 날은 점이 없어 상관없다), 혼자 넘치는 날은 같은 길이로 쪼갠다.
    조각은 시각 오름차순이고 점이 있는 날을 전부 덮는다. 날 시작은 창 집계가 준 값이라 첫날은 `start` 일 수 있다.
    """
    chunks: list[tuple[int, int]] = []
    cur: list[int] | None = None  # [a, b, n]
    for day, n in sorted(days):
        a = max(day, start)
        b = min((day // DAY + 1) * DAY, stop)
        if a >= b or n <= 0:
            continue
        if n > budget:
            if cur is not None:
                chunks.append((cur[0], cur[1]))
                cur = None
            parts = math.ceil(n / budget)
            step = math.ceil((b - a) / parts)
            chunks.extend((s, min(s + step, b)) for s in range(a, b, step))
            continue
        if cur is not None and cur[2] + n <= budget:
            cur[1] = b
            cur[2] += n
        else:
            if cur is not None:
                chunks.append((cur[0], cur[1]))
            cur = [a, b, n]
    if cur is not None:
        chunks.append((cur[0], cur[1]))
    return chunks


@dataclass
class Replay:
    """코인 하나를 운영 감지기로 재생한다 — 감지기가 쓰려는 점 가운데 닫힌 사건만 모은다 (§3.3·§3.4).

    감지기의 쓰기 대상(EventWriter)이 이 객체다. 열린 지 60초·60초 갱신 점(`end_ts 0`)은 버린다.
    """

    closed: list[InfluxPoint] = field(default_factory=list)
    boundary_closed: int = 0  # 경계에서 last_ts 로 닫은 사건
    boundary_dropped: int = 0  # 경계에서 열린 지 60초를 못 넘겨 버린 사건
    ticks: int = 0  # 넣은 틱(빈 틱 제외)
    _det: PremiumEventDetector = field(init=False)
    _last_ts: int | None = field(default=None, init=False)
    _drained_ts: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._det = PremiumEventDetector(writer=self)

    def write(self, points: list[InfluxPoint]) -> None:  # EventWriter
        self.closed.extend(p for p in points if p.fields["end_ts"])

    async def feed(self, ticks: Iterable[Tick]) -> None:
        for t in ticks:
            if self._last_ts is not None and t.ts <= self._last_ts:
                raise ValueError(f"틱 시각이 거꾸로 — {self._last_ts} 뒤 {t.ts}")
            if self._last_ts is not None and t.ts - self._last_ts > 1:
                # 운영은 매초 틱이 돈다 — 빠진 초는 행 없는 틱이다. 다음 틱 1초 전 한 번이면 결측 판정이 같다
                self._observe(Tick(ts=t.ts - 1, rows=(), dw_failed=()))
            self._observe(t)
            self.ticks += 1
            if self._drained_ts is None:
                self._drained_ts = t.ts
            elif t.ts - self._drained_ts >= DRAIN_SEC:
                await self._det.flush()
                self._drained_ts = t.ts

    async def finish(self, boundary: int, *, close_open: bool) -> None:
        """재생 끝 — `boundary − 1` 까지 틱 열을 잇고, 남은 점을 받아 간다.

        `close_open` 이면 아직 열린 사건을 경계 규칙으로 닫는다(§3.4 — 백필). 검증은 열린 사건을 빼고 맞춘다.
        """
        if self._last_ts is not None and self._last_ts < boundary - 1:
            self._observe(Tick(ts=boundary - 1, rows=(), dw_failed=()))
        await self._det.flush()
        if not close_open:
            return
        for ev in self._det.open_events():
            if ev.written:
                # 복원이 600초 넘게 못 본 사건을 닫는 규칙과 같다 — C 전 마지막 관측에서 끝
                self.closed.append(
                    premium_event_point(replace(ev, end_ts=ev.last_ts).to_row())
                )
                self.boundary_closed += 1
            else:
                self.boundary_dropped += 1  # 운영이었다면 Influx 에 점이 생기기 전

    def _observe(self, t: Tick) -> None:
        self._det.observe(t)
        self._last_ts = t.ts


def event_fields(fields: dict[str, float | int | str]) -> dict[str, float | int]:
    return {
        k: fields[k] for k in ("end_ts", "max_percent", "max_ts", "last_ts", "samples")
    }


def point_key(p: InfluxPoint) -> Key:
    t = p.tags
    return (t["dom"], t["fx"], t["base"], t["dir"], p.ts)


@dataclass
class Comparison:
    prod: int = 0
    replay: int = 0
    matched: int = 0  # 키도 다섯 값도 같다
    only_prod: list[Key] = field(default_factory=list)
    only_replay: list[Key] = field(default_factory=list)
    differ: list[tuple[Key, dict[str, tuple[float, float]]]] = field(
        default_factory=list
    )
    field_diffs: Counter[str] = field(default_factory=Counter)
    excluded_combos: int = 0  # 창 시작 전에 열린 운영 사건이 창 끝까지 이어져 뺀 조합

    def add(self, other: "Comparison") -> None:
        self.prod += other.prod
        self.replay += other.replay
        self.matched += other.matched
        self.only_prod += other.only_prod
        self.only_replay += other.only_replay
        self.differ += other.differ
        self.field_diffs.update(other.field_diffs)
        self.excluded_combos += other.excluded_combos


def compare(
    prod: Iterable[EventListRow],
    replay: Iterable[InfluxPoint],
    start: int,
    stop: int,
) -> Comparison:
    """검증(§3.7) — 창 [start, stop) 를 빈 상태로 재생한 사건과 운영 사건을 맞춘다.

    조합마다 비교 시작 = 창 시작, 단 창 시작 전에 열린 운영 사건이 있으면 그 끝(진행 중·고아 점은 `last_ts`)
    + 601초 — 그 안에 돌아온 행은 운영에선 그 사건을 잇고 재생에선 새 사건을 연다. 양쪽 모두 시작이 비교 시작
    이상이고, 닫혔고, 끝이 창 끝 − 610초 이하인 사건만 센다.
    """
    cmp_start: dict[tuple[str, str, str, str], int] = {}
    prod_events: dict[Key, dict[str, float | int]] = {}
    for e in prod:
        combo = (e.dom, e.fx, e.base, e.dir)
        if e.start_ts < start:
            # 진행 중·고아 점은 마지막 관측이 끝이다(013 §3.4) — 지금도 진행 중이면 창 끝을 넘어 조합째 빠진다
            end = e.end_ts or e.last_ts
            if end + MAX_GAP_SEC + 1 > start:
                cmp_start[combo] = max(
                    cmp_start.get(combo, start), end + MAX_GAP_SEC + 1
                )
            continue
        prod_events[(*combo, e.start_ts)] = {
            "end_ts": e.end_ts,
            "max_percent": e.max_percent,
            "max_ts": e.max_ts,
            "last_ts": e.last_ts,
            "samples": e.samples,
        }
    replay_events = {point_key(p): event_fields(p.fields) for p in replay}

    def eligible(key: Key, f: dict[str, float | int]) -> bool:
        end = int(f["end_ts"])
        return (
            key[4] >= cmp_start.get(key[:4], start)
            and 0 < end <= stop - VERIFY_TAIL_SEC
        )

    out = Comparison(excluded_combos=sum(1 for v in cmp_start.values() if v >= stop))
    a = {k: f for k, f in prod_events.items() if eligible(k, f)}
    b = {k: f for k, f in replay_events.items() if eligible(k, f)}
    out.prod, out.replay = len(a), len(b)
    for k in sorted(a.keys() - b.keys()):
        out.only_prod.append(k)
    for k in sorted(b.keys() - a.keys()):
        out.only_replay.append(k)
    for k in sorted(a.keys() & b.keys()):
        diffs = {
            name: (float(a[k][name]), float(b[k][name]))
            for name in a[k]
            if float(a[k][name]) != float(b[k][name])
        }
        if diffs:
            out.differ.append((k, diffs))
            out.field_diffs.update(diffs.keys())
        else:
            out.matched += 1
    return out


# ── Influx 를 쓰는 부분 ──────────────────────────────────────────────────


class _DetectorWarnings(logging.Handler):
    """감지기 경고(미전송 상한 버림 등)를 모은다 — 백필은 경고 하나도 없이 끝나야 한다(§3.3)."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


_warnings = _DetectorWarnings()
logging.getLogger("marketlens.premium_events").addHandler(_warnings)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cpu_sec() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return ru.ru_utime + ru.ru_stime


def _rss_mb() -> float:
    # 리눅스는 KB, macOS 는 바이트
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1024 if sys.platform != "darwin" else rss / 1024 / 1024


async def _retry[T](what: str, fn: Callable[[], T]) -> T:
    """스레드에서 `fn` — 실패는 10초 간격으로 3번 다시, 그래도 실패면 예외 그대로 (§3.6)."""
    for attempt in range(RETRIES + 1):
        try:
            return await asyncio.to_thread(fn)
        except Exception as exc:
            if attempt == RETRIES:
                raise
            print(
                f"  {what} 실패({attempt + 1}/{RETRIES + 1}) — {RETRY_SLEEP:.0f}초 뒤 다시: {exc!r}"
            )
            await asyncio.sleep(RETRY_SLEEP)
    raise AssertionError("도달하지 않는다")


async def _paced_query[T](what: str, fn: Callable[[], T]) -> T:
    """조회 1번 — 걸린 만큼 쉰다(최소 0.2초). Influx 가 쓰는 CPU 가 절반을 넘지 않게 (§3.6)."""
    t0 = time.monotonic()
    out = await _retry(what, fn)
    await asyncio.sleep(
        max(QUERY_SLEEP_MIN, QUERY_SLEEP_RATIO * (time.monotonic() - t0))
    )
    return out


async def replay_base(
    influx: InfluxClient,
    base: str,
    days: list[tuple[int, int]],
    start: int,
    stop: int,
    *,
    close_open: bool,
) -> tuple[Replay, int, int]:
    """코인 하나를 [start, stop) 재생 — (재생 결과, 조각 수, 읽은 fwd 점 수)."""
    rep = Replay()
    chunks = plan_chunks(days, start, stop)
    points = 0
    for a, b in chunks:
        # 조각 하나는 다 받은 뒤 재생한다 — 받다 끊기면 그 조각만 다시(감지기에 반쪽 조각이 들어가지 않게)
        ticks = await _paced_query(
            f"{base} {_iso(a)} 조회",
            lambda a=a, b=b: build_ticks(
                base, influx.stream_premium_base(base=base, start=a, stop=b)
            ),
        )
        points += sum(len(t.rows) for t in ticks)
        await rep.feed(ticks)
    await rep.finish(stop, close_open=close_open)
    if _warnings.messages:
        raise RuntimeError(f"{base}: 감지기 경고 — {_warnings.messages[:3]}")
    return rep, len(chunks), points


async def write_points(influx: InfluxClient, points: list[InfluxPoint]) -> None:
    for i in range(0, len(points), WRITE_BATCH):
        batch = points[i : i + WRITE_BATCH]
        await _retry(f"쓰기 {len(batch)}점", lambda batch=batch: influx.write(batch))
        await asyncio.sleep(WRITE_SLEEP)


def _days_by_base(rows: list[tuple[str, int, int]]) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    for base, day, n in rows:
        out.setdefault(base, []).append((day, n))
    return out


async def run_backfill(influx: InfluxClient, bases: list[str], write: bool) -> int:
    t_all, cpu_all = time.monotonic(), _cpu_sec()
    counts = _days_by_base(
        await _paced_query(
            "날별 점 수", lambda: influx.premium_day_counts(start=0, stop=CUTOFF)
        )
    )
    targets = [b.upper() for b in bases] or sorted(counts)
    mode = "쓰기" if write else "dry-run(쓰지 않음)"
    print(f"사건 백필 {mode} — 경계 {_iso(CUTOFF)} 전, 코인 {len(targets)}종")
    total = Counter[str]()
    first_start: int | None = None
    last_end: int | None = None
    for i, base in enumerate(targets, 1):
        days = counts.get(base, [])
        if not days:
            print(f"[{i}/{len(targets)}] {base}: 경계 전 점 없음")
            continue
        t0, c0 = time.monotonic(), _cpu_sec()
        rep, n_chunks, n_points = await replay_base(
            influx, base, days, 0, CUTOFF, close_open=True
        )
        events = rep.closed
        if any(p.ts >= CUTOFF or int(p.fields["end_ts"]) >= CUTOFF for p in events):
            raise AssertionError(f"{base}: 경계 뒤 사건 — 쓰지 않는다")
        if write:
            await write_points(influx, events)
        dirs = Counter(p.tags["dir"] for p in events)
        total.update(dirs)
        total["boundary_closed"] += rep.boundary_closed
        total["boundary_dropped"] += rep.boundary_dropped
        total["points"] += n_points
        total["ticks"] += rep.ticks
        if events:
            s = min(p.ts for p in events)
            e = max(int(p.fields["end_ts"]) for p in events)
            first_start = s if first_start is None else min(first_start, s)
            last_end = e if last_end is None else max(last_end, e)
        print(
            f"[{i}/{len(targets)}] {base}: 조각 {n_chunks}·점 {n_points:,}·틱 {rep.ticks:,} → "
            f"사건 김프 {dirs['kimp']}·역프 {dirs['reverse']}"
            f"(경계 닫음 {rep.boundary_closed}·버림 {rep.boundary_dropped})"
            f"{' 씀' if write else ''} — {time.monotonic() - t0:.1f}초·CPU {_cpu_sec() - c0:.1f}초"
        )
    print(
        f"합계: 사건 {total['kimp'] + total['reverse']:,}(김프 {total['kimp']:,}·역프 {total['reverse']:,}), "
        f"경계 닫음 {total['boundary_closed']}·버림 {total['boundary_dropped']}, "
        f"점 {total['points']:,}·틱 {total['ticks']:,}, "
        f"사건 기간 {_iso(first_start) if first_start else '-'} ~ {_iso(last_end) if last_end else '-'}, "
        f"{time.monotonic() - t_all:.0f}초·CPU {_cpu_sec() - cpu_all:.0f}초·최대 RSS {_rss_mb():.0f}MB"
        f"{'' if write else ' — 쓰지 않았다(--write 로 쓴다)'}"
    )
    return 0


async def run_verify(
    influx: InfluxClient, bases: list[str], start: int, stop: int
) -> int:
    t_all, cpu_all = time.monotonic(), _cpu_sec()
    if not bases:
        window = await _paced_query(
            "운영 사건", lambda: influx.query_premium_events(start=start, stop=stop)
        )
        busy = Counter(e.base for e in window)
        bases = [b for b, _ in busy.most_common(VERIFY_TOP)]
        bases += [b for b in VERIFY_ALWAYS if b not in bases]
    targets = [b.upper() for b in bases]
    counts = _days_by_base(
        await _paced_query(
            "날별 점 수",
            lambda: influx.premium_day_counts(start=start, stop=stop),
        )
    )
    print(f"검증 — {_iso(start)} ~ {_iso(stop)}, 코인 {len(targets)}종 (쓰지 않음)")
    total = Comparison()
    for i, base in enumerate(targets, 1):
        t0 = time.monotonic()
        rep, n_chunks, n_points = await replay_base(
            influx, base, counts.get(base, []), start, stop, close_open=False
        )
        prod = await _paced_query(
            f"{base} 운영 사건",
            lambda base=base: influx.query_premium_events(
                start=start - VERIFY_LOOKBACK_SEC, stop=stop, base=base
            ),
        )
        cmp = compare(prod, rep.closed, start, stop)
        total.add(cmp)
        print(
            f"[{i}/{len(targets)}] {base}: 점 {n_points:,} → 운영 {cmp.prod}·재생 {cmp.replay}·같음 {cmp.matched}"
            f"·운영만 {len(cmp.only_prod)}·재생만 {len(cmp.only_replay)}·값 다름 {len(cmp.differ)}"
            f"(뺀 조합 {cmp.excluded_combos}) — {time.monotonic() - t0:.1f}초"
        )
    print(
        f"합계: 운영 {total.prod:,}·재생 {total.replay:,}·같음 {total.matched:,}"
        f"·운영만 {len(total.only_prod)}·재생만 {len(total.only_replay)}·값 다름 {len(total.differ)}"
        f"(필드별 {dict(total.field_diffs)}), 뺀 조합 {total.excluded_combos}"
        f" — {time.monotonic() - t_all:.0f}초·CPU {_cpu_sec() - cpu_all:.0f}초·최대 RSS {_rss_mb():.0f}MB"
    )
    for k in total.only_prod[:20]:
        print(f"  운영만 {k} {_iso(k[4])}")
    for k in total.only_replay[:20]:
        print(f"  재생만 {k} {_iso(k[4])}")
    for k, diffs in total.differ[:20]:
        print(f"  값 다름 {k} {_iso(k[4])} {diffs}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="과거 김프/역프 사건 백필 — premium 을 운영 감지기로 재생 (스펙 084)"
    )
    parser.add_argument("bases", nargs="*", help="코인 목록 (기본: 전부)")
    parser.add_argument(
        "--write", action="store_true", help="경계 전 사건을 premium_event 에 쓴다"
    )
    parser.add_argument(
        "--verify",
        nargs=2,
        type=int,
        metavar=("START", "STOP"),
        help="[START, STOP) epoch 초를 재생해 운영 사건과 맞춘다 (쓰지 않음)",
    )
    args = parser.parse_args()
    if args.write and args.verify:
        parser.error("--write 와 --verify 는 같이 쓰지 않는다")
    settings = get_settings()
    if not settings.influx_token:
        print("INFLUX_TOKEN 이 없어 실행할 수 없습니다.")
        return 1
    influx = InfluxClient(url=settings.influx_url, token=settings.influx_token)
    try:
        if args.verify:
            start, stop = args.verify
            if stop <= start or start < CUTOFF:
                parser.error("--verify 창은 경계 뒤 [START, STOP) 여야 한다")
            return asyncio.run(run_verify(influx, args.bases, start, stop))
        return asyncio.run(run_backfill(influx, args.bases, args.write))
    except Exception as exc:
        print(f"멈춤 — 다시 돌리면 된다(같은 키 덮어쓰기): {exc!r}")
        return 1
    finally:
        influx.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n중단됨 — 다시 돌리면 된다(같은 키 덮어쓰기).")
        sys.exit(130)
