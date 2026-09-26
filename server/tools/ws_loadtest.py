"""`/api/ws/spreads` 웹소켓 부하 측정기.

브라우저와 같은 조건(permessage-deflate 협상, 끊기면 1초 뒤 재접속)으로 가짜 접속자 N 명을 붙이고,
계단식으로 늘려가며 "매초 1건이 제때 오는가" 를 본다. 서버 쪽 CPU 는 별도로 `docker stats` 로 본다.

    python ws_loadtest.py wss://kimptrack.com/api/ws/spreads --stages 10,50,100,200 --hold 30

k6 대신 websockets 라이브러리를 쓰는 이유: k6 웹소켓 모듈은 압축을 협상하지 않아서 서버가 압축 없이
보내버리고, 접속자당 압축 비용이 측정에서 빠진다.
"""

import argparse
import asyncio
import gzip
import statistics
import time
from collections import Counter

import websockets

RECONNECT_SEC = 1.0  # 프론트 재접속 백오프 시작값과 같게
CONNECT_GAP_SEC = 0.05  # 접속을 한꺼번에 쏘지 않고 20/s 로 붙인다 — 접속 폭주가 아니라 유지 부하를 재는 것


class Stats:
    """한 단계(stage) 동안의 집계. 단계가 바뀌면 새로 만든다."""

    def __init__(self) -> None:
        self.msgs = Counter()  # type → 건수
        self.bytes = 0
        self.closes = Counter()  # close code → 건수
        self.errors = Counter()  # 예외 클래스 이름 → 건수
        self.gaps: list[float] = []  # 같은 접속에서 연속 메시지 사이 간격(초). 서버가 밀리면 커진다
        self.first_snapshot: list[float] = []  # 접속 시작 → 스냅샷 도착까지(초)


class Runner:
    def __init__(self, url: str) -> None:
        self.url = url
        self.stats = Stats()
        self.alive = 0
        self.extension = None  # 협상된 Sec-WebSocket-Extensions (압축 확인용)
        self.tasks: list[asyncio.Task] = []

    async def client(self) -> None:
        """접속자 1명. 끊기면 1초 뒤 다시 붙는다(프론트와 같은 동작)."""
        while True:
            started = time.monotonic()
            try:
                # max_size=None: 스냅샷이 압축 해제 후 1MiB 를 넘을 수 있어 기본 상한을 푼다
                async with websockets.connect(self.url, max_size=None) as ws:
                    if self.extension is None:
                        self.extension = ws.response.headers.get(
                            "Sec-WebSocket-Extensions", "(없음)"
                        )
                    self.alive += 1
                    try:
                        await self.recv_loop(ws, started)
                    finally:
                        self.alive -= 1
            except websockets.ConnectionClosed as e:
                self.stats.closes[e.rcvd.code if e.rcvd else "no-frame"] += 1
            except Exception as e:  # noqa: BLE001 — 부하기라서 종류만 세고 계속 간다
                self.stats.errors[type(e).__name__] += 1
            await asyncio.sleep(RECONNECT_SEC)

    async def recv_loop(self, ws, started: float) -> None:
        last = None
        async for raw in ws:
            now = time.monotonic()
            # 개선 후 서버는 gzip 바이너리 프레임을 보낸다 — 브라우저처럼 풀어서 같은 기준(압축 해제 크기)으로 센다.
            # 개선 전(텍스트 프레임)도 그대로 받아 전후를 같은 스크립트로 잰다.
            if isinstance(raw, bytes):
                raw = gzip.decompress(raw).decode()
            self.stats.bytes += len(raw)
            # 파싱 비용을 아끼려고 앞부분 문자열만 본다 — 메시지는 항상 {"type":"..."} 로 시작
            kind = raw[9 : raw.index('"', 9)]
            self.stats.msgs[kind] += 1
            if kind == "snapshot":
                self.stats.first_snapshot.append(now - started)
            if last is not None:
                self.stats.gaps.append(now - last)
            last = now

    async def scale_to(self, n: int) -> None:
        while len(self.tasks) < n:
            self.tasks.append(asyncio.create_task(self.client()))
            await asyncio.sleep(CONNECT_GAP_SEC)

    def report(self, target: int, hold: float) -> None:
        s = self.stats
        total = sum(s.msgs.values())
        line = [
            f"[목표 {target}명] 살아있음 {self.alive}",
            f"msg/s/접속자 {total / max(hold, 1) / max(target, 1):.2f}",
            f"수신 {s.bytes / hold / 1e6:.1f} MB/s(압축해제)",
        ]
        if s.gaps:
            q = statistics.quantiles(s.gaps, n=20)
            line.append(f"간격 p50 {statistics.median(s.gaps):.2f}s p95 {q[18]:.2f}s max {max(s.gaps):.2f}s")
        if s.first_snapshot:
            line.append(f"스냅샷 도착 p50 {statistics.median(s.first_snapshot):.2f}s max {max(s.first_snapshot):.2f}s")
        line.append(f"종류 {dict(s.msgs)}")
        if s.closes:
            line.append(f"끊김 {dict(s.closes)}")
        if s.errors:
            line.append(f"에러 {dict(s.errors)}")
        print(" | ".join(line), flush=True)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--stages", default="10,50,100,200", help="단계별 접속자 수, 쉼표 구분")
    p.add_argument("--hold", type=float, default=30.0, help="단계마다 유지·측정하는 시간(초)")
    a = p.parse_args()

    r = Runner(a.url)
    for target in [int(x) for x in a.stages.split(",")]:
        await r.scale_to(target)
        r.stats = Stats()  # 접속 램프 구간은 버리고 유지 구간만 잰다
        await asyncio.sleep(a.hold)
        r.report(target, a.hold)
    print(f"압축 협상: {r.extension}")
    for t in r.tasks:
        t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
