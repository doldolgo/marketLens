"""Clarity 요약 — Data Export API 호출·응답 줄이기·마지막 시도 기록 (스펙 035 §3.3).

외부 계약(Microsoft Learn "Clarity Data Export API", 2026-10-01 확인): `GET …/project-live-insights?numOfDays=1`,
헤더 `Authorization: Bearer <토큰>`, 응답 `[{metricName, information: [행…]}]`, **프로젝트당 하루 10회**(넘으면 429).
그래서 시도 사이를 3시간(성공·실패 모두) 띄우고, 마지막 시도 시각·결과·마지막 성공 값을 Redis `admin:clarity` 에 둔다 —
api 재시작(배포)이 한도를 쓰지 않게. 주소 값은 쿼리·해시를 떼고(`Referrer URL` 은 출처로) 저장·응답한다.
토큰은 헤더에만 싣는다 — 로그·Redis·응답·예외 code 어디에도 없다.
"""

import asyncio
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import USER_AGENT

EXPORT_URL = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
NUM_OF_DAYS = 1
TIMEOUT_SEC = 10.0  # 재시도 없음
GAP_MS = 3 * 3600 * 1000  # 어떤 24시간에도 8회 이하 — 사람이 손으로 부를 2회가 남는다
KEEP_MS = 7 * 86_400 * 1000  # 마지막 성공 값은 7일 뒤 버린다
ROW_LIMIT = 20
TEXT_LIMIT = 200
TRAFFIC = "Traffic"
REFERRER = "Referrer URL"
# 문서가 행 모양을 적은 지표는 Traffic 하나 — 응답 키 → (값 키, 정수 여부)
TRAFFIC_KEYS = (
    ("sessions", "totalSessionCount", True),
    ("botSessions", "totalBotSessionCount", True),
    ("users", "distantUserCount", True),
    ("pagesPerSession", "PagesPerSessionPercentage", False),
)
_AUTHORITY_END = re.compile(r"[/?#]")


class ClarityStore(Protocol):
    """core `RedisBus` 의 `admin:clarity` 읽기·쓰기 (035 §3.3)."""

    async def clarity_load(self) -> str | None: ...

    async def clarity_save(self, data: str) -> None: ...


@dataclass(frozen=True)
class Record:
    """`admin:clarity` 한 값 — 마지막 시도(시각·결과)와 마지막 성공(시각·값). 시각은 epoch ms."""

    attempt_at: int | None = None
    state: str | None = None
    code: str | None = None
    success_at: int | None = None
    values: dict[str, Any] | None = None

    def dump(self) -> str:
        return json.dumps(
            {
                "attemptAt": self.attempt_at,
                "state": self.state,
                "code": self.code,
                "successAt": self.success_at,
                "values": self.values,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def fresh(self, now_ms: int) -> "Record":
        """마지막 성공에서 7일이 지났으면 값 부분을 버린다 — 시도 시각·결과는 남긴다."""
        if self.success_at is not None and now_ms - self.success_at <= KEEP_MS:
            return self
        return Record(self.attempt_at, self.state, self.code)


def load_record(text: str | None) -> Record:
    """Redis 값 → Record. 없거나 모양이 틀리면 빈 기록(시도 없음)."""
    if not text:
        return Record()
    try:
        data = json.loads(text)
    except ValueError:
        return Record()
    if not isinstance(data, dict):
        return Record()

    def ms(key: str) -> int | None:
        value = data.get(key)
        return value if type(value) is int else None

    def text_of(key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) else None

    values = data.get("values")
    success_at = ms("successAt")
    if not isinstance(values, dict) or success_at is None:
        values, success_at = None, None
    return Record(
        ms("attemptAt"), text_of("state"), text_of("code"), success_at, values
    )


async def fetch(
    token: str, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, str | None, dict[str, Any] | None]:
    """한 번 부른다 → (state, code, 값). 401·403 은 denied, 429·5xx·그 밖은 error. 오류 문장은 버린다."""
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_SEC
        ) as client:
            resp = await asyncio.wait_for(
                client.get(
                    EXPORT_URL,
                    params={"numOfDays": str(NUM_OF_DAYS)},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": USER_AGENT,
                    },
                ),
                TIMEOUT_SEC,
            )
    except (TimeoutError, httpx.TimeoutException):
        return "error", "timeout", None
    except Exception as exc:
        return "error", type(exc).__name__, None
    if resp.status_code in (401, 403):
        return "denied", f"http_{resp.status_code}", None
    if resp.status_code != 200:
        return "error", f"http_{resp.status_code}", None
    try:
        return "ok", None, shape(resp.json())
    except Exception as exc:
        return "error", type(exc).__name__, None


def shape(payload: Any) -> dict[str, Any]:
    """응답 → 값 `{numOfDays, traffic, metrics}`. Traffic 밖 지표는 받은 이름·키 그대로(행 20개, 주소 줄인 뒤)."""
    if not isinstance(payload, list):
        raise ValueError("응답이 목록이 아니다")
    traffic: dict[str, Any] | None = None
    seen_traffic = False
    metrics: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name, rows = item.get("metricName"), item.get("information")
        if not isinstance(name, str) or not isinstance(rows, list):
            continue
        if name == TRAFFIC:
            if not seen_traffic:
                seen_traffic = True
                first = rows[0] if rows and isinstance(rows[0], dict) else None
                traffic = None if first is None else _traffic(first)
            continue
        referrer = name == REFERRER
        metrics.append(
            {
                "name": name[:TEXT_LIMIT],
                "rows": [_reduce(row, referrer) for row in rows[:ROW_LIMIT]],
            }
        )
    return {"numOfDays": NUM_OF_DAYS, "traffic": traffic, "metrics": metrics}


def _traffic(row: dict[str, Any]) -> dict[str, Any]:
    """`Traffic` 첫 행 → 숫자(문자열 숫자도). 없거나 숫자가 아니면 그 칸만 null."""
    out: dict[str, Any] = {}
    for key, source, integer in TRAFFIC_KEYS:
        number = _number(row.get(source))
        if integer and number is not None:
            out[key] = int(number) if number.is_integer() else None
        else:
            out[key] = number
    return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _reduce(value: Any, referrer: bool) -> Any:
    """주소 줄이기 — `http(s)://`·`/` 로 시작하는 문자열은 쿼리·해시를 떼고(`Referrer URL` 의 주소는 출처로),
    그 밖의 문자열은 200자에서 자른다. 목록·객체는 안까지 같은 규칙."""
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return _origin(value) if referrer else _strip(value)
        if value.startswith("/"):
            return _strip(value)
        return value[:TEXT_LIMIT]
    if isinstance(value, list):
        return [_reduce(v, referrer) for v in value]
    if isinstance(value, dict):
        return {k: _reduce(v, referrer) for k, v in value.items()}
    return value


def _strip(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0]


def _origin(url: str) -> str:
    """`https://사용자@호스트:포트/경로?쿼리` → `https://호스트:포트`."""
    scheme, _, rest = url.partition("://")
    authority = _AUTHORITY_END.split(rest, maxsplit=1)[0]
    return f"{scheme.lower()}://{authority.rsplit('@', 1)[-1]}"
