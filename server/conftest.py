"""테스트 전체 공용 — tests/ 와 기능 폴더 tests/ 모두에 걸린다."""

import gc
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _restore_gc() -> Iterator[None]:
    """lifespan·틱 루프를 돌린 테스트가 프로세스 전역 GC 설정을 뒤 테스트로 넘기지 않게.

    되돌리는 것: 얼리기·세대 임계(001 §3.1), 자동 수집 켜짐 여부(001 §3.6 — 틱 루프가 틱 동안 끄고 틱 끝
    + 0.2초에 다시 켠다. 그 예약이 돌기 전에 이벤트 루프가 닫히면 꺼진 채로 남는다).
    """
    threshold = gc.get_threshold()
    enabled = gc.isenabled()
    yield
    gc.unfreeze()
    gc.set_threshold(*threshold)
    if enabled:
        gc.enable()
    else:
        gc.disable()
