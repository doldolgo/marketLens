"""Clarity Data Export — 호출 하나·기록·이름 비교·주소 줄이기 (스펙 035 §3.3·040 §3.1~§3.3).

외부 계약(Microsoft Learn "Clarity Data Export API", 2025-12-05 갱신): `GET …/project-live-insights`, 인자 `numOfDays`
1~3·`dimension1`~`3`, 헤더 `Authorization: Bearer <토큰>`, 응답 `[{metricName, information: [행…]}]`(지표마다 1,000행까지),
**프로젝트당 하루 10회**(넘으면 429). 호출은 둘이다 — 기본 요약(차원 없음)과 페이지×기기 묶음(`URL`·`Device`, 72시간).
일정·한도 지키기는 `visits.py`, 값 만들기는 `clarity_values.py`(기본)·`clarity_pages.py`(묶음)가 한다.
기록은 Redis 에 `{attemptAt, state, code, successAt, values}` 로 둔다 — api 재시작(배포)이 한도를 쓰지 않게.
주소 값은 쿼리·해시를 떼고(출처 지표는 출처로) 저장·응답한다.
지표 이름·행 키는 정규화해 비교한다 — 실제 응답은 `ReferrerUrl`·`ScrollDepth` 처럼 CamelCase 이고 문서는 띄어 쓴다.
출처 지표는 이름에 `referr`·`referer` 가 들면 — 철자가 또 바뀌어도 경로가 남지 않는 쪽(닫힌 쪽)으로.
JSON 은 다시 쓸 수 있는 값만 남긴다 — 표준 밖 `NaN`·`Infinity`·넘치는 실수는 null, 짝 없는 서로게이트는 `?`.
응답 JSON 풀기·만들기는 `asyncio.to_thread`(기본 실행기)에서 한다.
토큰은 헤더에만 싣는다 — 로그·Redis·응답·예외 code 어디에도 없다.
"""

import asyncio
import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.core.config import USER_AGENT

EXPORT_URL = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
TIMEOUT_SEC = 10.0  # 재시도 없음
KEEP_MS = 7 * 86_400 * 1000  # 마지막 성공 값은 7일 뒤 버린다
ROW_LIMIT = 20
TEXT_LIMIT = 200
# 출처 지표 — 실제 `ReferrerUrl`(2026-10-02 첫 응답), 문서 `Referrer URL`. 조각 포함으로 고른다:
# `Referrer`·`Referer Url`·`Referring URL`·`Referral…` 처럼 철자가 바뀌어도 출처로 줄인다(잘못 맞으면 경로만 잃는다)
REFERRER_PARTS = ("referr", "referer")
_NOT_ALNUM = re.compile(r"[^a-z0-9]")
_AUTHORITY_END = re.compile(r"[/?#]")
# 주소 꼴 — `<스킴>://`(대소문자 무관) 또는 스킴 없는 `호스트.이름` 바로 뒤에 `/`·`?`·`#`
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_HOST_LIKE = re.compile(
    r"(?:[a-z0-9-]+\.)+[a-z]{2,}(?::[0-9]+)?(?=[/?#])", re.IGNORECASE
)


@lru_cache(maxsize=4096)
def metric_key(name: str) -> str:
    """지표 이름·행 키 비교용 — NFKC(전각 → 반각) 뒤 소문자로 바꾸고 영문자·숫자만 남긴다.
    `ReferrerUrl`·`Referrer URL` → `referrerurl`. 행 키는 몇 가지가 수천 행에 되풀이되어 결과를 기억해 둔다."""
    return _NOT_ALNUM.sub("", unicodedata.normalize("NFKC", name).lower())


def is_referrer(key: str) -> bool:
    """정규화한 이름이 출처 지표인지 — `referr`·`referer` 조각이 들어 있으면."""
    return any(part in key for part in REFERRER_PARTS)


def loads(raw: str | bytes) -> Any:
    """JSON 풀기 — 표준 밖 `NaN`·`Infinity` 와 넘치는 실수(`1e400`)는 null. 풀린 값은 다시 JSON 으로 쓸 수 있다."""
    return json.loads(raw, parse_constant=lambda _: None, parse_float=_finite)


def _finite(text: str) -> float | None:
    number = float(text)
    return number if math.isfinite(number) else None


def check_json(value: Any) -> None:
    """응답·Redis 로 다시 쓸 수 있는지 — 아니면 ValueError(UnicodeEncodeError 포함)·TypeError."""
    json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def read_number(value: Any) -> float | None:
    """숫자나 숫자 글자(`"120"`) → 유한한 수. 참거짓·그 밖 글자·NaN·무한은 None (040 §3.3 '수 읽기')."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def read_int(value: Any) -> int | None:
    """정수 칸 — 정수일 때만(`"3"`·`3.0` 은 3, `2.5` 는 None)."""
    number = read_number(value)
    return int(number) if number is not None and number.is_integer() else None


def pick(row: Mapping[str, Any], names: frozenset[str]) -> dict[str, Any]:
    """행에서 정규화한 키가 `names` 안인 값만 — 같은 이름이 둘이면 앞의 것."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        name = metric_key(key) if isinstance(key, str) else ""
        if name in names and name not in out:
            out[name] = value
    return out


def metric_items(payload: Any) -> list[tuple[str, str, list[Any]]]:
    """응답 → `(받은 이름, 정규화한 이름, 행 목록)`. 목록이 아니거나, 비지 않았는데
    `{metricName: 글자, information: 목록}` 이 하나도 없으면 ValueError — 받은 자료가 틀림(`bad_data`)."""
    if not isinstance(payload, list):
        raise ValueError("응답이 목록이 아니다")
    items = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name, rows = item.get("metricName"), item.get("information")
        if isinstance(name, str) and isinstance(rows, list):
            items.append((name, metric_key(name), rows))
    if payload and not items:
        raise ValueError("지표가 하나도 없다")  # 빈 목록(자료 없음)만 성공이다
    return items


class ClarityStore(Protocol):
    """core `RedisBus` 의 `admin:clarity`(기본)·`admin:clarity:pages`(묶음) 읽기·쓰기 (035 §3.3·040 §3.2)."""

    async def clarity_load(self) -> str | None: ...

    async def clarity_save(self, data: str) -> None: ...

    async def clarity_pages_load(self) -> str | None: ...

    async def clarity_pages_save(self, data: str) -> None: ...


@dataclass(frozen=True)
class Record:
    """기록 한 값(`admin:clarity`·`admin:clarity:pages`) — 마지막 시도(시각·결과)와 마지막 성공(시각·값). 시각은 epoch ms."""

    attempt_at: int | None = None
    state: str | None = None
    code: str | None = None
    success_at: int | None = None
    values: dict[str, Any] | None = None

    def dump(self) -> str:
        """JSON 문자열 — NaN·Infinity·짝 없는 서로게이트가 있으면 ValueError(Redis 에 쓰지 않는다)."""
        text = json.dumps(
            {
                "attemptAt": self.attempt_at,
                "state": self.state,
                "code": self.code,
                "successAt": self.success_at,
                "values": self.values,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        text.encode("utf-8")
        return text

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
        data = loads(text)
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
    token: str,
    transport: httpx.AsyncBaseTransport | None,
    *,
    params: Mapping[str, str],
    parse: Callable[[bytes], dict[str, Any]],
) -> tuple[str, str | None, dict[str, Any] | None]:
    """한 번 부른다 → (state, code, 값). 401·403 은 denied, 429·5xx·그 밖은 error, 받은 자료가 틀리면 `bad_data`.
    오류 문장은 버린다. 리다이렉트는 따르지 않는다(httpx 기본)."""
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_SEC
        ) as client:
            resp = await asyncio.wait_for(
                client.get(
                    EXPORT_URL,
                    params=dict(params),
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
        # 기본 수백 KB·묶음 ≈3MB — 풀기·만들기는 기본 실행기에서 (§3.1)
        values = await asyncio.to_thread(_parse_checked, parse, resp.content)
    except (ValueError, TypeError, RecursionError):
        return "error", "bad_data", None  # JSON 이 아님·모양 틀림·다시 쓸 수 없는 값
    except Exception as exc:
        return "error", type(exc).__name__, None
    return "ok", None, values


def _parse_checked(
    parse: Callable[[bytes], dict[str, Any]], content: bytes
) -> dict[str, Any]:
    values = parse(content)
    check_json(
        values
    )  # 다시 JSON 으로 쓸 수 없으면 ValueError — 실패로 세고 마지막 성공 값을 둔다
    return values


def reduce_value(value: Any, referrer: bool) -> Any:
    """주소 줄이기 — 문자열은 `_reduce_text`, 유한하지 않은 실수는 null. 목록·객체는 안까지(객체 키도) 같은 규칙.
    출처 지표(`referrer`)면 주소를 출처로."""
    if isinstance(value, str):
        return _reduce_text(value, referrer)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [reduce_value(v, referrer) for v in value]
    if isinstance(value, dict):
        return {
            _reduce_text(str(k), referrer): reduce_value(v, referrer)
            for k, v in value.items()
        }
    return value


def _reduce_text(value: str, referrer: bool) -> str:
    """주소 꼴(`<스킴>://`·`/`·스킴 없는 `호스트.이름/…`)은 앞뒤 공백·쿼리·해시·사용자 정보를 떼고
    (`Referrer URL` 은 출처로), 그 밖은 200자에서 자른다."""
    text = utf8(value)
    s = text.strip()
    if _SCHEME.match(s):
        scheme, authority, rest = _split(s)
        if referrer:
            return f"{scheme}://{authority}"
        return f"{scheme}://{authority}{_strip(rest)}"
    if s.startswith("/"):
        return _strip(s)
    host = None if any(c.isspace() for c in s) else _HOST_LIKE.match(s)
    if host is not None:
        return host.group(0) if referrer else _strip(s)
    return text[:TEXT_LIMIT]


def utf8(text: str) -> str:
    """짝 없는 서로게이트(이모지 중간에서 잘린 제목 등) → `?` — UTF-8 로 쓸 수 있게."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")
    return text


def _strip(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0]


def _split(url: str) -> tuple[str, str, str]:
    """`<스킴>://사용자@호스트:포트/경로?쿼리` → (소문자 스킴, `호스트:포트`, `/경로?쿼리`)."""
    scheme, _, rest = url.partition("://")
    end = _AUTHORITY_END.search(rest)
    cut = len(rest) if end is None else end.start()
    return scheme.lower(), rest[:cut].rsplit("@", 1)[-1], rest[cut:]
