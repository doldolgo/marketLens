"""`GET /admin/status` 응답 모델 — 스펙 029 §3.4.

상태 응답이라 `{"error":…}` 형식이 아니다. 키는 alias 로 camelCase(`wsConnections`)를 만들고,
라우터가 response_model 로 선언해 OpenAPI 스키마에 이 모양이 실린다.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Health = Literal["ok", "down"]


class AdminStatusOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 017 허브의 열린 /ws/spreads 연결 수(waiting 포함), 허브가 없으면 0
    ws_connections: int
    redis: Health
    influx: Health
    # 앱 버전(APP_VERSION) — /health 의 version 과 같은 값
    version: str
