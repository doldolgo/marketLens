"""HTTP 경계 — camelCase 변환(메모)과 앱 전역 GZip 레벨 (001 §3.1·§4, 2026-09-28)."""

import importlib
import json
import zlib

from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from pydantic.alias_generators import to_camel

from app.core.config import GZIP_LEVEL
from app.core.serialization import camelize_json
from app.main import create_app


def test_camelize_json_converts_nested_dict_and_list_keys() -> None:
    assert camelize_json(
        {
            "data_received_at": 1,
            "rows": [{"rate_ask": 1400.0}],
            "error": {"detail": {"available_exchanges": ["upbit"]}},
        }
    ) == {
        "dataReceivedAt": 1,
        "rows": [{"rateAsk": 1400.0}],
        "error": {"detail": {"availableExchanges": ["upbit"]}},
    }


def _model_field_names() -> set[str]:
    names: set[str] = set()
    for feature in (
        "analysis",
        "health",
        "history",
        "landing",
        "spreads",
        "wallet_status",
    ):
        module = importlib.import_module(f"app.features.{feature}.models")
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseModel):
                names.update(obj.model_fields)
    return names


def test_memoized_key_conversion_matches_pydantic_to_camel() -> None:
    """메모한 변환은 같은 키에 같은 결과 — 응답 모델의 모든 필드 이름과 경계 모양 키로, 두 번씩."""
    keys = _model_field_names() | {
        "a",
        "fx_rate",
        "already_camel",
        "alreadyCamel",
        "_leading",
        "trailing_",
        "double__under",
        "x1_y2",
        "net_fx",
        "",
    }
    assert len(keys) > 50
    for _ in range(2):  # 두 번째는 메모에서 나온다
        converted = camelize_json({k: [{k: k}] for k in keys})
        assert converted == {to_camel(k): [{to_camel(k): k}] for k in keys}


def test_gzip_middleware_compresses_at_level_6_and_body_is_unchanged() -> None:
    app = create_app()
    # 미들웨어 최소 크기(500B)를 넘고, 레벨 6·9 의 압축 결과가 다른 본문
    text = json.dumps(
        [
            {"sym": f"C{i}", "fwd": i * 0.137 % 3, "rev": -i * 0.071 % 2}
            for i in range(2000)
        ]
    )
    app.add_api_route("/_big", lambda: PlainTextResponse(text))
    with TestClient(app).stream(
        "GET", "/_big", headers={"Accept-Encoding": "gzip"}
    ) as resp:
        assert resp.headers["content-encoding"] == "gzip"
        raw = b"".join(resp.iter_raw())
    assert GZIP_LEVEL == 6
    expected = zlib.compressobj(GZIP_LEVEL, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    assert raw == expected.compress(text.encode()) + expected.flush()
    assert zlib.decompress(raw, 16 + zlib.MAX_WBITS) == text.encode()
