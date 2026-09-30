"""테스트 전체 공용 — tests/ 와 기능 폴더 tests/ 모두에 걸린다."""

import gc
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _restore_gc() -> Iterator[None]:
    """lifespan 을 돌린 테스트가 프로세스 전역 GC 설정(001 §3.1 의 얼리기·세대 임계)을 뒤 테스트로 넘기지 않게."""
    threshold = gc.get_threshold()
    yield
    gc.unfreeze()
    gc.set_threshold(*threshold)
