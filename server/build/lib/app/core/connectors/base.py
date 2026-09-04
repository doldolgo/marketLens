"""거래소 커넥터 공통 인터페이스.

새 거래소 추가 = 이 클래스 구현체 하나 추가. collector·조회 코드는 바뀌지 않는다.
거래소별 quirk 는 각 구현체 안에서만 흡수한다 — 커넥터끼리 코드 공유 금지.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.models import Row


@dataclass
class FetchResult:
    rows: list[Row]  # 스펙 001 §3.4 규칙으로 조립이 끝난 행들
    calls: int  # 이번 fetch 에서 실제로 나간 HTTP 호출 수


class ExchangeConnector(ABC):
    id: str  # 거래소 id — upbit·bithumb·binance

    @abstractmethod
    async def fetch_rows(self, client: httpx.AsyncClient) -> FetchResult:
        """전 마켓 시세를 일괄 조회해 스냅샷 행으로 돌려준다. 실패는 ExchangeError 로 던진다."""
