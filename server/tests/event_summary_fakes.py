"""랜딩 사건 요약(022 §3.2)의 기준 계산과, 요약 조회가 보내는 Flux 에 CSV 로 답하는 가짜 Influx.

`reference_summary` 는 사건 점을 전부 올려 파이썬에서 세는 기준선이다 — 방향별 수, 진행 중(end_ts 0 이고
last_ts ≥ open_since 인 조합의 종류 수), 코인마다 가장 늦게 끝난 닫힌 사건(같으면 늦게 시작한 것, 그것도 같으면
dom·fx·dir 이 앞서는 것)을 끝난 시각 내림차순(같으면 base 오름차순) top_n. `FluxEvents` 는 influxdb-client 자리에
꽂혀 `query_event_summary` 의 세 가지 Flux(요약·닫힌 사건 전부·상세)를 사건 점 목록으로 흉내 낸다 — `top()` 의
동률 순서는 일부러 base 내림차순으로 줘서, 후보 끝자리에서 잘린 코인을 되묻지 않으면 결과가 틀리게 한다.
"""

import csv
import io
import re
from datetime import UTC, datetime
from types import SimpleNamespace

from app.core.influx import EndedEventRow, EventSummary, PremiumEventRow


def reference_summary(
    rows: list[PremiumEventRow],
    *,
    start: int,
    stop: int,
    open_since: int,
    top_n: int,
) -> EventSummary:
    rows = [r for r in rows if start <= r.start_ts < stop]
    kimp = sum(r.dir == "kimp" for r in rows)
    reverse = sum(r.dir == "reverse" for r in rows)
    open_keys = {
        (r.dom, r.fx, r.base, r.dir)
        for r in rows
        if r.end_ts == 0 and r.last_ts >= open_since
    }
    best: dict[str, PremiumEventRow] = {}
    for r in rows:
        if r.end_ts <= 0:
            continue
        cur = best.get(r.base)
        if (
            cur is None
            or (r.end_ts, r.start_ts) > (cur.end_ts, cur.start_ts)
            or (
                (r.end_ts, r.start_ts) == (cur.end_ts, cur.start_ts)
                and (r.dom, r.fx, r.dir) < (cur.dom, cur.fx, cur.dir)
            )
        ):
            best[r.base] = r
    top = sorted(best.values(), key=lambda r: (-r.end_ts, r.base))[:top_n]
    return EventSummary(
        kimp=kimp,
        reverse=reverse,
        open=len(open_keys),
        latest=[
            EndedEventRow(
                dom=r.dom,
                fx=r.fx,
                base=r.base,
                dir=r.dir,
                start_ts=r.start_ts,
                end_ts=r.end_ts,
                duration_seconds=r.duration_seconds,
                max_percent=r.max_percent,
                last_ts=r.last_ts,
            )
            for r in top
        ],
    )


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def _block(result: str, cols: list[str], rows: list[list[object]]) -> str:
    """헤더 있는 무주석 CSV 한 덩어리 — Influx 처럼 줄 끝은 \\r\\n, 쉼표가 든 값은 따옴표."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["", "result", "table", *cols])
    for i, row in enumerate(rows):
        w.writerow(["", result, i, *row])
    return buf.getvalue()


class FluxEvents:
    """InfluxDBClient 자리 — 받은 Flux 를 `flux` 에 남기고, 사건 점 목록으로 요약·되묻기·상세 CSV 를 만든다."""

    def __init__(self, rows: list[PremiumEventRow], *, chunk: int = 7) -> None:
        self.rows = rows
        self.flux: list[str] = []
        self.chunk = chunk  # 응답 조각 크기 — 작게 두면 줄이 조각 경계에서 잘린다

    def __call__(
        self, **kw: object
    ) -> "FluxEvents":  # InfluxDBClient(url=…, token=…) 흉내
        return self

    def query_api(self) -> SimpleNamespace:
        def query_raw(flux: str, dialect: object) -> SimpleNamespace:
            self.flux.append(flux)
            data = self._answer(flux).encode()
            return SimpleNamespace(
                stream=lambda amt: (
                    data[i : i + self.chunk] for i in range(0, len(data), self.chunk)
                ),
                release_conn=lambda: None,
                close=lambda: None,
            )

        return SimpleNamespace(query_raw=query_raw)

    def _window(self, flux: str) -> list[PremiumEventRow]:
        m = re.search(r"range\(start: (\S+), stop: (\S+)\)", flux)
        assert m is not None
        lo, hi = _epoch(m.group(1)), _epoch(m.group(2))
        return [r for r in self.rows if lo <= r.start_ts < hi]

    def _answer(self, flux: str) -> str:
        rows = self._window(flux)
        tags = ["_time", "dom", "fx", "base", "dir"]

        def key(r: PremiumEventRow) -> list[object]:
            return [_iso(r.start_ts), r.dom, r.fx, r.base, r.dir]

        if 'yield(name: "bydir")' in flux:
            since = int(re.search(r"r\._value >= (-?\d+)", flux).group(1))  # type: ignore[union-attr]
            n = int(re.search(r"top\(n: (\d+)", flux).group(1))  # type: ignore[union-attr]
            by_dir: dict[str, int] = {}
            for r in rows:
                by_dir[r.dir] = by_dir.get(r.dir, 0) + 1
            closed = sorted(
                (r for r in rows if r.end_ts > 0), key=lambda r: r.base, reverse=True
            )
            closed.sort(
                key=lambda r: -r.end_ts
            )  # 안정 정렬 — 같은 끝 시각은 base 내림차순
            return "\r\n".join(
                [
                    _block("recent", tags, [key(r) for r in rows if r.last_ts is not None and r.last_ts >= since]),  # 필드 없는 점은 last_ts 표에 없다
                    _block("latest", [*tags, "_value"], [[*key(r), r.end_ts] for r in closed[:n]]),
                    _block("bydir", ["dir", "_value"], [[d, c] for d, c in by_dir.items()]),
                    _block("zero", tags, [key(r) for r in rows if r.end_ts == 0]),
                ]
            )  # fmt: skip
        if "pivot(" in flux:
            bases = set(re.search(r"=~ /\^\((.*)\)\$/", flux).group(1).split("|"))  # type: ignore[union-attr]
            bases = {re.sub(r"\\(.)", r"\1", b) for b in bases}
            cols = [*tags, "duration_seconds", "last_ts", "max_percent"]
            return _block(
                "_result",
                cols,
                [
                    [*key(r), r.duration_seconds, r.last_ts, r.max_percent]
                    for r in rows
                    if r.base in bases
                ],
            )
        # 닫힌 사건 전부 — 후보 끝자리가 동률로 잘렸을 때 되묻는다
        assert "r._value > 0" in flux and "top(" not in flux
        return _block(
            "_result",
            [*tags, "_value"],
            [[*key(r), r.end_ts] for r in rows if r.end_ts > 0],
        )
