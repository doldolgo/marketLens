# 008 — usdt-staleness

상태: DONE | 의존: 001(collect — USDT 시세 계약), 003(spreads — `/spreads` 응답)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.

## 1. 목적
USDT 시세는 이 시스템의 **유일한 원화↔USDT 변환값**이다(은행 환율 없음 — product.md 용어). 그런데 001 규칙상 관측이 끊기면 직전 값을 **조용히** 유지하므로, KRW-USDT 호가가 오래 안 들어오면 전 종목 김프/역프가 낡은 시세로 계산되는데 응답엔 아무 표시가 없다. 이 스펙은 그 침묵을 없앤다: 시세가 오래 갱신되지 않으면 `/spreads` 응답에 경고를 실어, 소비자(FE·모니터링·curl)가 알아챌 수 있게 한다.

## 2. 범위
- 만드는 것: `/spreads` 응답 최상위 `warnings` 배열 — USDT 시세 미갱신 경고.
- 바꾸는 기존 것: 003 `/spreads` — 최상위 키 `warnings` 추가. 기존 키·행 스키마 불변(모르는 키를 무시하는 FE 폴링에 하위호환).
- 하지 않는 것: **FE 표시·공용 문구 조각의 경고 레벨·화면 문구 정리 — 2026-08-31 사용자 결정으로 범위 제외**(FE 는 이 경고를 아직 소비하지 않는다. 필요해지면 이 스펙에 다시 넣고 코드와 함께 구현한다). 김프 계산 중단·행 제외 없음(직전 값 유지 규칙은 그대로 — 경고만 얹는다). API 키 이름 변경 없음. `/refresh`·`/history/*` 변경 없음.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001: USDT 시세는 국내 거래소별 `{exchange, ask, bid, updated_at(aware UTC)}`. 매 사이클 KRW 호가에서 추출하고, 관측 실패 시 직전 값을 유지한다(`updated_at` 이 안 바뀜).
- 003: `GET /spreads` 최상위는 `rate`·`rows`·`dataReceivedAt`·`fetchedAt`(camelCase). 행 계산은 각 국내 거래소의 자기 시세를 쓴다.

### 3.2 staleness 판정 (`/spreads` 계산 시)
- 임계 **60초** — 코드 상수. 왜 60초인가: 스냅샷 stale 기준(5초)과 달리 USDT 는 유동성이 높아 매 사이클(1초) 관측되는 게 정상이다. 60초 동안 한 번도 관측이 없으면 일시 결측이 아니라 구조적 문제(KRW-USDT 마켓 중단·응답 형식 변화·수집 회귀)로 본다.
- 시세가 있는(ask·bid > 0) 국내 거래소마다 `now − rate.updated_at > 60` 이면 `warnings` 에 1줄:
  `"{거래소id} USDT 시세가 {N}초째 갱신되지 않았습니다 — 이 거래소 행의 김프/역프는 낡은 시세 기준입니다."` (N 은 정수 초)
- 경고가 없으면 `warnings` 는 **빈 배열**(키는 항상 존재). 순서는 거래소 id 오름차순(결정적 응답).
- 시세가 아예 없는 거래소는 기존 규칙대로 행이 빠지므로 경고 대상이 아니다 — 경고는 "있긴 한데 낡은" 상태 전용.

## 4. 검증
네트워크 없음 — 저장소 직접 시드:
- 시세 `updated_at` 이 61초 전인 거래소 → `warnings` 에 그 거래소 1줄, 초 수(≥61)가 메시지에 있다
- 59초 전 → 빈 배열
- 두 국내 거래소 다 61초 전 → 2줄, 거래소 id 오름차순
- `warnings` 키는 경고가 없어도 항상 존재(빈 배열)하고, 기존 최상위 키(`rate`·`rows`·`dataReceivedAt`·`fetchedAt`)·행 18키는 불변이다
- 시세를 시드하지 않은 거래소는 경고에 나타나지 않는다
수동: 배포 후 `/api/spreads` 에 `warnings` 키가 있고 평상시 빈 배열이다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff format -q . && .venv/bin/ruff check -q . && .venv/bin/python -m pytest -q   # 221 passed (신규 4)
# 배포 후 EC2: curl localhost:8080/api/spreads → warnings 키 존재·빈 배열 확인
```

## 6. 갱신할 문서
- `docs/context/status.md` — spreads 행 server 칸에 "USDT 시세 미갱신 경고(`warnings`)" 추가, 낡은 "(입출금 전부 null)" 문구 제거. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 008 행 상태 → DONE, 범위 설명을 "`/spreads` USDT 시세 미갱신 경고 (BE 전용)" 로. **항상 포함.**
- `docs/context/architecture.md` — 계약 규칙 절에 한 줄 추가: "`/spreads` 최상위 `warnings: list[str]` — USDT 시세 60초 미갱신 경고, 없으면 빈 배열(키는 항상 존재)."
- `docs/context/dev-setup.md` — 검증용 스모크의 `/spreads` 설명에 "최상위 `warnings` 는 평상시 빈 배열" 1줄 추가.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것: `features/spreads/service.py` 상수 `USDT_STALE_WARN_SEC=60`·경고 조립, `models.py` `warnings` 필드, `tests/test_usdt_staleness.py`(4개).
- 추측한 지점: 경고 순서를 거래소 id 오름차순으로(§3.2 에 명시함), 판정 시각은 응답 계산 시각(now)과 동일.
- 실행 중 함께 고친 스펙 절: 설계 세션에서 FE 범위 제외로 §2 축소(사용자 결정), status.md 의 낡은 "(입출금 전부 null)" 문구도 §6 에서 함께 정리.
- 남은 빚: FE 표시(KPI 경고색·warning 줄·문구 조각 레벨)는 범위 제외 — 소비 시점에 이 스펙에 다시 넣는다.
