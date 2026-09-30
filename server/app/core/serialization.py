"""HTTP 경계의 camelCase 변환 — 모든 라우터가 쓴다 (architecture.md 계약 규칙)."""

from functools import lru_cache

from pydantic.alias_generators import to_camel

# 키 이름 변환을 메모한다(스펙 001 §3.1, 2026-09-28). 키 종류는 모델 필드 수십 개뿐인데 변환은 키마다 불려
# (사건 5.9만 건이면 76만 번) 정규식이 응답 직렬화 시간의 대부분이었다. 1,024 는 키 종류보다 넉넉한 상한이다.
_camel = lru_cache(maxsize=1024)(to_camel)


def camelize_json(value: object) -> object:
    if isinstance(value, dict):
        return {
            _camel(key) if isinstance(key, str) else key: camelize_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [camelize_json(item) for item in value]
    return value
