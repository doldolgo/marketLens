"""GET /health/collect 응답 모델 — 스펙 011 §3.5.

키는 alias 로 camelCase 를 만든다(model_dump(by_alias=True)). 공용 camelize_json 을 쓰지
않는 이유: to_camel 이 `success_rate_1h` 를 `successRate1H` 로 만들어 스펙의 `successRate1h` 와 어긋난다.
"""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

RATE_1H = "successRate1h"


class _Out(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class OutageOut(_Out):
    """실패 구간 1건 — openOutage 와 outages 항목이 같은 모양이다. 시각은 epoch ms."""

    exchange: str
    kind: str
    started_at: int
    ended_at: int | None
    last_failed_at: int
    count: int
    status_code: int | None
    message: str
    url: str | None
    retry_after_sec: int | None


class LastErrorOut(_Out):
    """가장 최근 구간의 최신 실패 — at 은 그 구간의 last_failed_at."""

    at: int
    kind: str
    status_code: int | None
    message: str


class ExchangeHealthOut(_Out):
    exchange: str
    state: str  # ok · stale · down
    last_success_at: int | None
    markets: int
    success_rate_1h: float = Field(serialization_alias=RATE_1H)
    open_outage: OutageOut | None
    last_error: LastErrorOut | None


class CollectHealthResponse(_Out):
    server_started_at: int
    fetched_at: int
    success_rate_1h: float = Field(serialization_alias=RATE_1H)
    exchanges: list[ExchangeHealthOut]
    outages: list[OutageOut]
