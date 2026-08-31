# CamelCase API Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모든 HTTP JSON 키와 underscore가 포함된 쿼리 파라미터를 camelCase로 통일하되 Python 내부 이름은 snake_case로 유지한다.

**Architecture:** `app.core.serialization.camelize_json()`이 Pydantic `model_dump()` 결과와 수동 에러 dict를 재귀적으로 변환한다. 라우터는 HTTP 응답 직전에만 이 함수를 호출하고 서비스·DB 모델은 바꾸지 않는다. `amountKrw`·`maxGap`은 FastAPI `Query(alias=...)`로 외부 이름만 바꾼다.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, React 19, TypeScript

---

### Task 1: CamelCase JSON contract tests

**Files:**
- Create: `server/tests/test_serialization.py`
- Modify: `server/app/features/spreads/tests/test_spreads_api.py`
- Modify: `server/app/features/spreads/tests/test_refresh_api.py`
- Modify: `server/app/features/analysis/tests/test_contract.py`
- Modify: `server/app/features/history/tests/test_premium_api.py`

- [x] **Step 1: Write the failing recursive conversion test**

```python
from app.core.serialization import camelize_json


def test_camelize_json_converts_nested_dict_and_list_keys() -> None:
    assert camelize_json(
        {"data_received_at": 1, "rows": [{"rate_ask": 1400.0}]}
    ) == {"dataReceivedAt": 1, "rows": [{"rateAsk": 1400.0}]}
```

- [x] **Step 2: Change representative API assertions to camelCase**

```python
assert body["dataReceivedAt"] == 1_787_000_000_000
assert body["fetchedAt"] > 1_700_000_000_000
assert body["snapshots"][0]["walletStatusAvailable"] is True
assert body["failures"][0]["errorCode"] == "exchange_timeout"
assert body["dataReceivedAt"] is not None
assert body["firstTs"] == expected_first_ts
```

- [x] **Step 3: Run focused tests and verify RED**

Run: `pytest -q tests/test_serialization.py app/features/spreads/tests/test_spreads_api.py app/features/spreads/tests/test_refresh_api.py app/features/analysis/tests/test_contract.py app/features/history/tests/test_premium_api.py`

Expected: import error for missing `app.core.serialization` and snake_case response assertion failures.

### Task 2: Shared response serialization

**Files:**
- Create: `server/app/core/serialization.py`
- Modify: `server/app/features/spreads/models.py`
- Modify: `server/app/features/spreads/router.py`
- Modify: `server/app/features/analysis/models.py`
- Modify: `server/app/features/analysis/router.py`
- Modify: `server/app/features/history/models.py`
- Modify: `server/app/features/history/router.py`
- Modify: `server/app/main.py`

- [x] **Step 1: Implement the minimal recursive converter**

```python
from pydantic.alias_generators import to_camel


def camelize_json(value: object) -> object:
    if isinstance(value, dict):
        return {
            to_camel(key) if isinstance(key, str) else key: camelize_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [camelize_json(item) for item in value]
    return value
```

- [x] **Step 2: Serialize every router response through `camelize_json`**

```python
return JSONResponse(content=camelize_json(payload.model_dump()))
```

Apply the same conversion to success payloads and manually assembled error bodies in `main.py`, spreads, analysis, and history routers. Remove the spreads-only Pydantic alias configuration.

- [x] **Step 3: Run the focused tests and verify GREEN**

Run: `pytest -q tests/test_serialization.py app/features/spreads/tests app/features/analysis/tests app/features/history/tests`

Expected: all selected tests pass after remaining expected-key assertions are migrated.

### Task 3: CamelCase query parameters

**Files:**
- Modify: `server/app/features/analysis/router.py`
- Modify: `server/app/features/analysis/tests/test_matrix_api.py`
- Modify: `server/app/features/history/router.py`
- Modify: `server/app/features/history/tests/test_streaks_api.py`
- Modify: `server/app/features/history/tests/test_bulk_api.py`

- [x] **Step 1: Change request tests to camelCase and verify RED**

```python
assert client.get("/matrix", params={"amountKrw": -1}).status_code == 422
assert client.get("/history/streaks", params={"base": "BTC", "maxGap": 1}).status_code == 200
```

Run: `pytest -q app/features/analysis/tests/test_matrix_api.py app/features/history/tests/test_streaks_api.py app/features/history/tests/test_bulk_api.py`

Expected: `amountKrw` is ignored as the old default and `maxGap` is ignored as `max_gap`, so contract assertions fail.

- [x] **Step 2: Add external aliases while keeping Python names**

```python
amount_krw: float = Query(10_000_000, gt=0, alias="amountKrw")
max_gap: int = Query(600, ge=1, alias="maxGap")
```

- [x] **Step 3: Run the focused tests and verify GREEN**

Run: `pytest -q app/features/analysis/tests/test_matrix_api.py app/features/history/tests/test_streaks_api.py app/features/history/tests/test_bulk_api.py`

Expected: all selected tests pass.

### Task 4: React API contract

**Files:**
- Modify: `web/src/features/spreads/types.ts`

- [x] **Step 1: Update the top-level response type**

```typescript
export interface SpreadsResponse {
  rate: number
  rows: ApiSpreadRow[]
  dataReceivedAt: number | null
  fetchedAt: number
}
```

The row type already uses camelCase, so no UI mapping or shared view-model change is needed.

- [x] **Step 2: Verify frontend compilation**

Run: `npm run lint && npm run build`

Expected: lint and TypeScript build pass.

### Task 5: Living API documentation

**Files:**
- Modify: `docs/context/architecture.md`
- Modify: `docs/context/product.md`
- Modify: `docs/context/db.md`
- Modify: `docs/context/dev-setup.md`
- Modify: `docs/context/status.md`
- Modify: `docs/specs/003-spreads.md`
- Modify: `docs/specs/004-analysis.md`
- Modify: `docs/specs/005-history.md`
- Modify: `docs/specs/006-wallet-status.md`
- Modify: `docs/specs/008-usdt-staleness.md`

- [x] **Step 1: State the boundary rule**

```markdown
- Python 내부와 DB 필드는 snake_case, HTTP JSON 키와 쿼리 파라미터는 camelCase를 사용한다.
```

- [x] **Step 2: Migrate only HTTP-facing examples and names**

Change response/query examples such as `data_received_at`→`dataReceivedAt`, `fetched_at`→`fetchedAt`, `wallet_status_available`→`walletStatusAvailable`, `amount_krw`→`amountKrw`, and `max_gap`→`maxGap`. Keep Python identifiers, DB fields, and formulas in snake_case.

- [x] **Step 3: Search for stale contract text**

Run: `rg -n '응답.*snake_case|최상위.*snake_case|amount_krw|max_gap|data_received_at|fetched_at|wallet_status_available' docs`

Expected: remaining matches describe Python/DB internals only; no HTTP contract remains snake_case.

### Task 6: Full verification and local commit

**Files:**
- Verify all modified files

- [x] **Step 1: Run server checks**

Run: `ruff check . && ruff format --check . && pytest -q`

Expected: all checks and at least 216 tests pass.

- [x] **Step 2: Run web checks**

Run: `npm run lint && npm run build`

Expected: both commands pass.

- [x] **Step 3: Verify scope and commit**

Run: `git diff --check && git status --short && git diff --stat`

Expected: only API contract, its tests, React type, and related living documents changed. Amend the unpushed architecture commit into one coherent local commit.
