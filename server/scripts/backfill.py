"""캔들 백필 — 업비트 초봉 × 바이낸스 1초봉 → 과거 김프를 `premium` 에 (스펙 005 §3.5).

실행 (server/ 디렉토리에서, .env 의 INFLUX_URL·INFLUX_TOKEN 사용):
    python -m scripts.backfill [BASE ...] [--days N]
코인 목록 기본값 BTC, 일수 기본값 92 — 바이낸스 1초봉 92일 ≈ 코인당 약 8,000 요청이라
기본값이 코인 하나다. 페어는 upbit×binance 고정(빗썸엔 초봉 API 가 없다).

캔들엔 호가가 없어 김프는 종가로 대칭 계산한다. UTC 하루 단위로 처리·날마다 쓰고,
기존 기록의 앞·뒤 빈 구간만 채운다(가운데는 건드리지 않는다) — 재실행 안전.
Ctrl-C 중단 시 exit 130, 다시 실행하면 남은 구간부터 이어진다.
"""

import argparse
import sys
import time
from bisect import bisect_left
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.influx import InfluxClient, premium_point
from app.core.premium import premium_percent

DAY = 86_400
DOM = "upbit"
FX = "binance"

UPBIT_HOST = "https://api.upbit.com"
BINANCE_HOST = "https://api.binance.com"

# candles 그룹 10 req/s·600/min 을 라이브 수집과 같은 IP 로 나눠 쓴다 (§3.5)
UPBIT_PAGE_SLEEP = 0.2
# 가중치 2/호출
BINANCE_PAGE_SLEEP = 0.1


# ── 순수 계산 (네트워크 없음 — 테스트 대상) ────────────────────────────────


def day_slices(start: int, end: int, *, newest_first: bool) -> list[tuple[int, int]]:
    """[start, end) 를 UTC 하루 경계로 자른 조각들 — end exclusive."""
    slices: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        day_end = (cursor // DAY + 1) * DAY
        slices.append((cursor, min(day_end, end)))
        cursor = day_end
    return list(reversed(slices)) if newest_first else slices


def plan_day_slices(
    target_start: int, target_end: int, first_last: tuple[int, int] | None
) -> list[tuple[int, int]]:
    """백필 대상 구간 계산 — 스펙 005 §3.5 재실행 안전 규칙.

    기록 없음 → 전체 구간. 기록 있음 → [목표시작, 첫 time) 과 [마지막 time+1, 목표끝)
    만(가운데는 건드리지 않는다). 기존 기록 이전 구간은 최신 날부터 거꾸로 —
    중단돼도 미완 구간이 첫 time 밖에 남아 다음 실행이 다시 잡는다.
    """
    if first_last is None:
        return day_slices(target_start, target_end, newest_first=True)
    first, last = first_last
    front = day_slices(target_start, min(first, target_end), newest_first=True)
    back = day_slices(max(last + 1, target_start), target_end, newest_first=False)
    return front + back


def dedup_changes(points: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """ts 오름차순 정렬 후 직전과 같은 값을 제거한 변동 목록 (§3.5 합치기 1단계)."""
    out: list[tuple[int, float]] = []
    prev: float | None = None
    for ts, value in sorted(points):
        if value == prev:
            continue
        out.append((ts, value))
        prev = value
    return out


def merge_premiums(
    dom_changes: list[tuple[int, float]],
    fx_changes: list[tuple[int, float]],
    rate_changes: list[tuple[int, float]],
    *,
    start: int,
    stop: int,
) -> list[tuple[int, float, float]]:
    """세 변동 목록을 ts 로 병합해 forward-fill 한 (ts, fwd, rev) — 스펙 005 §3.5.

    `start` 이전 ts 는 씨앗(값만 채우고 기록하지 않는다) — 환율 씨앗이 여기로 들어온다.
    셋이 다 갖춰지기 전 ts 는 건너뛰고, 셋 중 ≤0 이 있으면 건너뛰고,
    fwd 가 직전 기록과 같으면 기록하지 않는다.
    """
    events: list[tuple[int, int, float]] = []  # (ts, 시리즈 번호, 값)
    for series, changes in enumerate((dom_changes, fx_changes, rate_changes)):
        events.extend((ts, series, value) for ts, value in changes)
    events.sort()

    current: list[float | None] = [None, None, None]
    out: list[tuple[int, float, float]] = []
    prev_fwd: float | None = None
    i = 0
    while i < len(events):
        ts = events[i][0]
        # 같은 ts 의 변동은 전부 반영한 뒤 한 번만 평가한다
        while i < len(events) and events[i][0] == ts:
            current[events[i][1]] = events[i][2]
            i += 1
        if ts < start or ts >= stop:
            continue
        dom_close, fx_close, rate = current
        if dom_close is None or fx_close is None or rate is None:
            continue
        if dom_close <= 0 or fx_close <= 0 or rate <= 0:
            continue
        # 종가 대칭식: ratio = dom/(fx×rate), fwd=(ratio−1)×100, rev=(1/ratio−1)×100
        fwd = premium_percent(buy_krw=fx_close * rate, sell_krw=dom_close)
        if fwd == prev_fwd:
            continue
        rev = premium_percent(buy_krw=dom_close, sell_krw=fx_close * rate)
        out.append((ts, fwd, rev))
        prev_fwd = fwd
    return out


def rates_for_slice(
    rate_changes: list[tuple[int, float]], start: int, stop: int
) -> list[tuple[int, float]]:
    """씨앗(하루 시작 이전 최신 분봉) 1개 + 구간 안 변동 — merge 입력용."""
    idx = bisect_left(rate_changes, (start, float("-inf")))
    seed = [rate_changes[idx - 1]] if idx > 0 else []
    in_range = [p for p in rate_changes[idx:] if p[0] < stop]
    return seed + in_range


# ── 거래소 호출 (재시도 정책은 거래소별 — §3.5) ────────────────────────────


def _upbit_get(client: httpx.Client, path: str, params: dict[str, object]) -> list:
    """429·5xx·전송 오류는 1s·2s·3s 대기 후 3회 재시도, 그 외 4xx 즉시 실패."""
    delays = [1.0, 2.0, 3.0]
    for attempt in range(len(delays) + 1):
        try:
            res = client.get(f"{UPBIT_HOST}{path}", params=params)
        except httpx.HTTPError as exc:
            if attempt < len(delays):
                time.sleep(delays[attempt])
                continue
            raise RuntimeError(f"업비트 전송 오류: {exc}") from exc
        if res.status_code == 200:
            return res.json()
        if res.status_code == 429 or res.status_code >= 500:
            if attempt < len(delays):
                time.sleep(delays[attempt])
                continue
        raise RuntimeError(f"업비트 {res.status_code}: {res.text[:200]}")
    raise RuntimeError("업비트 재시도 소진")  # 도달 불가 — 타입 체커용


def _binance_get(client: httpx.Client, path: str, params: dict[str, object]) -> list:
    """418/429 는 2·4·6s, 5xx(와 전송 오류)는 1·2·3s 대기 재시도."""
    slow = [2.0, 4.0, 6.0]
    fast = [1.0, 2.0, 3.0]
    for attempt in range(4):
        try:
            res = client.get(f"{BINANCE_HOST}{path}", params=params)
        except httpx.HTTPError as exc:
            if attempt < 3:
                time.sleep(fast[attempt])
                continue
            raise RuntimeError(f"바이낸스 전송 오류: {exc}") from exc
        if res.status_code == 200:
            return res.json()
        if res.status_code in (418, 429) and attempt < 3:
            time.sleep(slow[attempt])
            continue
        if res.status_code >= 500 and attempt < 3:
            time.sleep(fast[attempt])
            continue
        raise RuntimeError(f"바이낸스 {res.status_code}: {res.text[:200]}")
    raise RuntimeError("바이낸스 재시도 소진")  # 도달 불가 — 타입 체커용


def _iso_z(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _upbit_candles_backward(
    client: httpx.Client, path: str, market: str, start: int, stop: int
) -> list[tuple[int, float]]:
    """업비트 캔들 페이지네이션 — `to`(exclusive) 를 페이지 최소 시각으로 옮기며 최신→과거."""
    out: dict[int, float] = {}
    to = stop
    while True:
        page = _upbit_get(
            client, path, {"market": market, "count": 200, "to": _iso_z(to)}
        )
        if not page:
            break
        min_ts: int | None = None
        for candle in page:
            ts = int(
                datetime.fromisoformat(candle["candle_date_time_utc"])
                .replace(tzinfo=UTC)
                .timestamp()
            )
            min_ts = ts if min_ts is None else min(min_ts, ts)
            if start <= ts < stop:
                out[ts] = float(candle["trade_price"])
        if min_ts is None or min_ts <= start:
            break
        if min_ts >= to:
            break  # 전진 없음 → 중단 (§3.5)
        to = min_ts
        time.sleep(UPBIT_PAGE_SLEEP)
    return sorted(out.items())


def fetch_upbit_seconds(
    client: httpx.Client, base: str, start: int, stop: int
) -> list[tuple[int, float]]:
    """업비트 초봉 — 체결 있던 초만 존재(희소). 빈 결과 = 보관 범위 밖/상장 이전."""
    return _upbit_candles_backward(
        client, "/v1/candles/seconds", f"KRW-{base}", start, stop
    )


def fetch_usdkrw_minutes(
    client: httpx.Client, start: int, stop: int
) -> list[tuple[int, float]]:
    """환율 — 같은 경로의 분봉 KRW-USDT (§3.5)."""
    return _upbit_candles_backward(
        client, "/v1/candles/minutes/1", "KRW-USDT", start, stop
    )


def fetch_binance_1s(
    client: httpx.Client, base: str, start: int, stop: int
) -> list[tuple[int, float]]:
    """바이낸스 현물 1초봉 — 과거→현재, 다음 startTime = 마지막 closeTime+1. 모든 초가 있다(밀집)."""
    out: list[tuple[int, float]] = []
    start_ms = start * 1000
    stop_ms = stop * 1000
    while start_ms < stop_ms:
        page = _binance_get(
            client,
            "/api/v3/klines",
            {
                "symbol": f"{base}USDT",
                "interval": "1s",
                "startTime": start_ms,
                "limit": 1000,
            },
        )
        if not page:
            break
        for k in page:
            open_ms = int(k[0])
            if open_ms >= stop_ms:
                break
            out.append((open_ms // 1000, float(k[4])))
        next_start = int(page[-1][6]) + 1
        if next_start <= start_ms:
            break  # 전진 없음
        start_ms = next_start
        time.sleep(BINANCE_PAGE_SLEEP)
    return out


# ── 실행 ───────────────────────────────────────────────────────────────────


def _day_label(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def run(coins: list[str], days: int) -> int:
    settings = get_settings()
    if not settings.influx_token:
        print("INFLUX_TOKEN 이 없어 백필을 실행할 수 없습니다 (server/.env).")
        return 1
    influx = InfluxClient(url=settings.influx_url, token=settings.influx_token)

    # UTC 하루 단위로 처리한다 — 목표 끝은 오늘 0시(오늘은 라이브 persist 루프 몫)
    target_end = int(time.time()) // DAY * DAY
    target_start = target_end - days * DAY
    print(
        f"대상: {', '.join(coins)} · {_day_label(target_start)} ~ {_day_label(target_end)} (UTC, {days}일)"
    )

    with httpx.Client(timeout=10.0) as client:
        # 재실행 안전 — 환율은 전체 구간 한 번만 수집, 씨앗용으로 목표 시작 6시간 전부터
        print("환율 수집 중 (업비트 KRW-USDT 분봉)…")
        rate_changes = dedup_changes(
            fetch_usdkrw_minutes(client, target_start - 6 * 3600, target_end)
        )
        if not rate_changes:
            print("환율 분봉이 0건 — 중단합니다.")
            return 1
        print(f"환율 변동 {len(rate_changes)}건 확보")

        for raw in coins:
            base = raw.upper()
            first_last = influx.first_last_premium(dom=DOM, fx=FX, base=base)
            slices = plan_day_slices(target_start, target_end, first_last)
            total = 0
            wrote = False
            aborted = False
            for slice_start, slice_stop in slices:
                # 이미 채워진 날인지 count 로 판정 (§3.5)
                if (
                    influx.count_premium(
                        dom=DOM, fx=FX, base=base, start=slice_start, stop=slice_stop
                    )
                    > 0
                ):
                    continue
                dom_changes = dedup_changes(
                    fetch_upbit_seconds(client, base, slice_start, slice_stop)
                )
                if not dom_changes:
                    # 초봉은 롤링 3개월 보관·상장 이전은 빈 응답 → 이 코인은 여기서 중단
                    print(
                        f"{base}: {_day_label(slice_start)} 업비트 초봉 없음(보관/상장 범위 밖) — 이 코인 중단"
                    )
                    aborted = True
                    break
                fx_changes = dedup_changes(
                    fetch_binance_1s(client, base, slice_start, slice_stop)
                )
                if not fx_changes:
                    print(
                        f"{base}: {_day_label(slice_start)} 바이낸스 1초봉 없음 — 이 코인 중단"
                    )
                    aborted = True
                    break
                points = merge_premiums(
                    dom_changes,
                    fx_changes,
                    rates_for_slice(rate_changes, slice_start, slice_stop),
                    start=slice_start,
                    stop=slice_stop,
                )
                if points:
                    # 날마다 쓴다 — 같은 시각 점은 Influx 가 덮어쓴다
                    influx.write(
                        [
                            premium_point(
                                dom=DOM, fx=FX, base=base, ts=ts, fwd=fwd, rev=rev
                            )
                            for ts, fwd, rev in points
                        ]
                    )
                total += len(points)
                wrote = True
                print(f"{base}: {_day_label(slice_start)} — {len(points)}건 기록")
            if wrote:
                print(f"{base}: 구간 완료, 김프 기록 {total}건")
            elif not aborted:
                print(f"{base}: 이미 전부 채워져 있습니다")
    influx.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="업비트×바이낸스 캔들 김프 백필 (스펙 005 §3.5)"
    )
    parser.add_argument(
        "coins", nargs="*", default=["BTC"], help="코인 목록 (기본 BTC)"
    )
    parser.add_argument("--days", type=int, default=92, help="백필 일수 (기본 92)")
    args = parser.parse_args()
    return run(args.coins, args.days)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n중단됨 — 다시 실행하면 남은 구간부터 이어집니다.")
        sys.exit(130)
