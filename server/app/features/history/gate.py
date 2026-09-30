"""무거운 조회 게이트 — `/history/premium`·`/history/streaks`·`/history/streaks/bulk` 동시 1개 (005 §3.4, 2026-09-28 결정).

세 경로는 원값을 수십만 점씩 읽는다. Influx 는 쿼리를 동시 2개까지만 돌려서(021 §3.1), 무거운 조회들이 칸을 다
쥐면 가벼운 조회(봉 청크·랜딩·사건)가 그 뒤에 줄을 선다. 그래서 무거운 조회는 한 번에 하나만 받고, 이미 돌고 있으면
기다리게 하지 않고 곧바로 거절한다(라우터가 429 `busy`). 앱마다 하나(`app.state.history_heavy`) — uvicorn 워커가
1개라 프로세스 안에서 세면 된다.
"""

import asyncio
from collections.abc import Callable


class HeavyGate:
    """스레드 작업 하나가 도는 동안 닫혀 있는 문 — 요청이 끊겨도 그 작업이 끝날 때 열린다."""

    def __init__(self) -> None:
        self._running: asyncio.Future[bytes] | None = None

    @property
    def busy(self) -> bool:
        return self._running is not None

    async def run(self, work: Callable[[], bytes]) -> bytes:
        """`work` 를 스레드에서 돌려 결과를 기다린다. 부르기 전에 `busy` 가 아님을 본다(사이에 await 가 없어야 한다).

        기다리던 요청이 끊겨도(취소) 스레드 조회는 멈추지 않으므로, 문은 요청이 아니라 작업이 끝날 때 연다 —
        그러지 않으면 요청을 보내고 끊기를 되풀이해 무거운 조회를 여러 개 겹쳐 돌릴 수 있다.
        """
        task = asyncio.ensure_future(asyncio.to_thread(work))
        self._running = task
        task.add_done_callback(self._open)
        return await asyncio.shield(task)

    def _open(self, task: asyncio.Future[bytes]) -> None:
        self._running = None
        if not task.cancelled():
            task.exception()  # 기다리던 요청이 먼저 떠났어도 "받지 않은 예외" 경고를 남기지 않게
