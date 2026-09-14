# 018 — spreads-serve

상태: DONE | 의존: 017(spreads-push — 표 게시·`spreads:latest`·`spreads:want`·`/ws/spreads`), 016(process-split — `api` 역할·nginx 분기), 003(spreads — 표 모양)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
스프레드 탭이 받는 데이터가 **전부 `api` 컨테이너에서** 온다. 017 로 표 푸시(`/ws/spreads`)는 이미 `api` 가 맡았지만, `GET /spreads` 만 아직 수집 프로세스가 메모리에서 답한다. 이 스펙이 끝나면 `GET /spreads` 도 `api` 가 Redis 키 `spreads:latest` 를 읽어 답하고, nginx 는 `/api/spreads` 를 `api` 로 보낸다. 수집 프로세스는 스프레드 탭을 위해 HTTP 를 서빙하지 않는다 — 표를 만들어 Redis 에 넣는 것까지가 일이다.

표 **계산**은 수집 프로세스에 남긴다(2026-09-14 결정). 표 한 장에 488종목 × 3거래소 호가창이 필요하고 그건 수집 메모리에만 있다. 계산을 옮기려면 호가창을 매초 Redis 로 퍼 날라야 하는데 메모리가 두 배가 되고 지연이 붙는다. 계산 자체는 회차당 약 50ms(017 §5) 로 가볍다. 비싼 건 서빙이 아니라 수신이다.

이 스펙 뒤 스프레드 탭의 경로는 하나다: **수집 → Redis → api → 브라우저.** Redis 가 죽으면 표는 안 온다 — 이건 이 스펙이 받아들이는 결정이고, 답은 Redis 의 가용성(인프라 제안서)이지 우회 경로가 아니다.

## 2. 범위
- 만드는 것: `api` 역할의 `GET /spreads`(Redis 읽기), nginx `/api/spreads` → `api` 분기, `GET /spreads` 가 `spreads:want` 를 갱신하는 규칙
- 하지 않는 것: `/refresh`·`/health`·`/health/collect`·`/history/events`·분석 엔드포인트의 이동(스프레드 탭이 안 쓴다 — 수집 프로세스의 나머지 HTTP 를 떼는 건 후속 스펙), 표 계산의 이동, FE 의 구독 규칙(017 §3.4 그대로), `api` 2대(상태는 이미 없지만 LB·compose 분리는 인프라 제안서)
- 바꾸는 기존 것: **017 §3.4 — FE 의 5초 폴링 fallback 을 없앤다**(2026-09-15 결정: WebSocket 이 막히면 백오프 재연결만 반복, 브라우저는 `GET /spreads` 를 부르지 않는다). 003 §3.2 — `GET /spreads` 의 `notional` 쿼리(진단용)를 **없앤다**. 표는 017 이 `$1,000` 으로만 만들고 저장하므로 다른 규모의 표는 존재하지 않는다. 종목 하나의 규모별 슬리피지는 004 의 `/analysis/slippage` 가 있다. 003 §3.2 의 404 조건 1·2 는 HTTP 규칙이 아니라 **"표를 만들지 않는 조건"** 이 된다. 016 §3.1 — `api` 서빙 경로에 `/spreads` 추가, Redis 용도에 "키 읽기" 추가. 017 §2 — "`GET /spreads` 는 진단용으로 남긴다(`notional` 쿼리 유지)" 를 이 스펙으로 교체.

## 3. 동작

### 3.1 `GET /spreads` — 두 역할 모두, Redis 에서 읽는다
- 쿼리 파라미터 **없음.** `notional` 을 주면 값이 무엇이든 **400** `notional_fixed`, message "체결 규모는 $1,000 고정입니다. notional 쿼리는 받지 않습니다." — 옛 호출자(구 번들 탭·스크립트)가 다른 규모의 표를 받았다고 착각하지 않게 하기 위해서다. 에러 포장은 003 과 같은 `{"error":{"code","message","detail"}}`, detail 은 `{"notional": <받은 값>}`. 이 400 은 Redis 를 만지기 전에 나므로 want 도 쓰지 않는다.
- 응답 200: Redis 키 `spreads:latest` 의 값을 **그대로** 돌려준다(017 §3.1 이 저장한 JSON — camelCase, `notional: 1000`, `rows` 17키, `warnings`, `rate`, `dataReceivedAt`, `fetchedAt`). 파싱·재직렬화하지 않는다 — 수집이 만든 바이트가 곧 응답이다. `Content-Type: application/json`, 기존 GZip 미들웨어 적용.
- 키가 없으면 **404** `market_data_not_found`, message "스프레드 표가 아직 없습니다. 수집이 표를 만드는 중이거나 멈춰 있습니다.", detail `{"key": "spreads:latest"}`. 017 의 `waiting` 과 같은 상황이다 — 수집 기동 직후, 첫 접속 뒤 최대 5초, 수집 정지.
- Redis 불달(연결 실패·타임아웃)이면 **503** `redis_unavailable`, message "Redis 에 연결할 수 없습니다.", detail `{"reason": <드라이버 오류 문자열>}` — 016 의 Influx 불달 503 과 같은 톤. 이 요청은 want 도 못 쓴다. 요청마다 새로 시도한다(백오프 없음).
- **`spreads:want` 갱신**: 200·404 어느 쪽이든 요청마다 `SET spreads:want 1 EX 15` 를 같이 한다(읽기와 한 왕복). curl·스크립트처럼 `GET` 만 부르는 호출자가 있는 동안에도 수집이 표를 만들어야 하기 때문이다(브라우저는 부르지 않는다 — 017 §3.4). 017 의 허브가 5초마다 하는 갱신과 같은 키·같은 TTL 이라 서로 방해하지 않는다. 첫 호출은 404 를 받고, 수집이 5초 안에 원함을 읽어 표를 만들면 그 뒤 호출부터 200 이다 — 017 의 "첫 접속자는 최대 5초" 와 같은 대기.
- `collector` 역할(로컬 단일 프로세스·배포의 `server` 컨테이너)도 **같은 핸들러**다 — 메모리를 읽지 않는다. 로컬 개발은 017 부터 dev compose 의 Redis 가 필요했으므로 조건이 늘지 않는다. 배포의 `server` 컨테이너에는 nginx 가 `/api/spreads` 를 보내지 않으므로 이 경로로 요청이 오지 않는다.
- 응답 시간: 017 실측 표 1장 238KB 라 Redis 읽기는 1ms 급이고 비용은 GZip 뿐이다. 016 의 `api` 가 그대로 감당한다.

### 3.2 수집 프로세스 — 표를 만들지 않는 조건 (003 §3.2 의 404 조건이 여기로 옮겨온다)
017 §3.1 의 게시기가 매초 표를 만들 때, 다음이면 그 회차는 **표를 만들지 않고 게시하지 않는다**(경고 없음 — 수집 기동 직후 정상 상태다). 키 `spreads:latest` 는 직전 값이 TTL 10초로 남았다가 사라진다.
- 기준 거래소(`upbit`)의 환율이 없거나 ask/bid 가 0 이하
- 스냅샷을 호가통화로 나눴을 때 국내(`KRW`) 또는 해외(`USDT`) 가 비어 있음
그 외 표 규칙(페어 생성·행 17키·슬리피지·`age`·`status`·`spark`·`warnings`)은 003 §3.2 그대로다.

### 3.3 프록시 — nginx
- `/api/spreads`(정확히 이 경로 — 쿼리는 무관하게 따라간다) 를 `api:8000/spreads` 로 보낸다. nginx 의 **정확 일치 location(`= /api/spreads`)** 하나 — 접두 제거(rewrite)·쿼리 유지·프록시 헤더 4개는 016 의 정규식 위치와 같은 방식. `/api/spreads/…` 같은 하위 경로는 없다 — 정확 일치라 그런 요청은 `location /api/` 가 받아 `server` 로 가고 거기서 404 다.
- `location /api/`(그 외 → `server`)·`/api/ws/`(→ `api`)·`= /api` 404 는 그대로.
- 배포 뒤 `docker compose stop api` 면 `/api/spreads` 는 **502**(016 §7 의 "stop 된 upstream" 과 같다), `/api/health` 는 200. 016 §4 의 격리 확인("stop api 뒤 `/api/spreads` 정상") 은 이 스펙으로 뒤집힌다 — dev-setup 의 그 문장을 고친다(§6).

### 3.4 역할 계약 — 016 §3.1 에 얹는 것
- `api` 가 서빙하는 경로: `/health`, `/history/premium`, `/history/streaks`, `/history/streaks/bulk`, `/history/candles`, `/ws/spreads`, **`/spreads`**. 그 외 404 는 유지.
- `api` 의 Redis 용도: 017 의 채널 구독 + **`spreads:latest` 읽기·`spreads:want` 쓰기**(요청 단위, 연결은 명령마다 lazy — 009 의 클라이언트와 같은 수명). 백그라운드 태스크는 여전히 017 의 구독 태스크 하나.

### 3.5 엣지
- **Redis 만 죽음**: `/ws/spreads` 는 `waiting`, `GET /spreads` 는 503 → FE 는 017 §3.4 규칙대로 직전 표를 지우지 않은 채 재연결을 반복한다. 표 갱신은 Redis 가 돌아올 때까지 멈춘다. 수집 프로세스의 메모리에는 표가 있지만 **꺼내는 길을 두지 않는다**(§1 결정).
- **수집 재시작**: 키가 10초 뒤 만료 → 404 → 첫 게시 뒤 200. 그 사이 FE 는 직전 표 유지(stale 로 감).
- **api 재시작**: 요청 단위라 상태 없음. nginx 가 재시작 중엔 502.
- **want 만 갱신되고 아무도 안 봄**: `GET` 호출자가 사라지면 키가 15초 뒤 만료되고 수집이 다음 5초 읽기에서 멈춘다 — 017 §3.6 과 같다.
- **옛 번들 탭**(`GET /spreads?notional=10000` 을 계속 부르는 브라우저 — 09-14 실측 1건): 400 을 받는다. 새로고침하면 사라진다.

## 4. 검증
- `GET /spreads`: 키가 있으면 200 이고 본문 바이트가 키 값과 같다 / 키가 없으면 404 `market_data_not_found` / Redis 불달이면 503 `redis_unavailable` / `notional` 을 주면 400 `notional_fixed`(값 `1000` 이어도)
- `GET /spreads` 요청마다 `spreads:want` 가 TTL 15초로 쓰인다(200·404 모두) / Redis 불달이면 안 쓰인다
- 두 역할에서 `GET /spreads` 가 메모리를 읽지 않는다 — 메모리에 행이 있고 Redis 키가 없으면 404
- 게시기: 환율 없음 / 국내 또는 해외 스냅샷 없음 → 그 회차 게시 없음, 경고 없음, 다음 회차에 조건이 풀리면 게시
- 역할: `api` 라우트 집합에 `/spreads` 포함, 그 외 404 유지 (`tests/test_role.py`)
- nginx 계약: `/api/spreads` 가 `api:8000` 으로 가고 접두가 제거되며 쿼리가 유지된다, `/api/spreads` 뒤에 붙는 하위 경로는 `server` 로 간다, 프록시 헤더 4개 (`tests/test_deploy.py`)
- 003 의 기존 `GET /spreads` 테스트(`features/spreads/tests/test_spreads_api.py`)는 메모리 기반이라 이 스펙에 맞게 다시 쓴다 — 표 **계산** 규칙 검증(003·006·008·009·005 의 테스트가 `GET /spreads` 로 표를 받아 보던 곳 전부)은 HTTP 대신 게시기와 같은 함수·직렬화로 만든 표 JSON 을 본다. `test_slippage.py` 의 `?notional=` 범위(422) 테스트는 쿼리가 사라졌으므로 삭제하고, 규모별 슬리피지 비교는 계산 함수로 돌린다
- 수동(로컬 5컨테이너): DevTools 에서 `/api/ws/` 만 차단하면 브라우저는 `/api/spreads` 를 **부르지 않고** 백오프(1→30초)로 WebSocket 재연결만 반복한다, 차단을 풀면 다음 재연결에 snapshot 이 와 표가 복구된다. curl `localhost:8090/api/spreads` 는 404 → 5초 안에 200(want 갱신). `docker compose stop api` → `/api/spreads` 502·`/api/health` 200. `stop redis` → `/api/spreads` 503·WS `waiting`·화면은 직전 표 유지. `start redis` 뒤 Redis 가 뜬 뒤 10초 안에 복구(실측 12초 — Redis 기동 포함)
- 수동(EC2): 배포 뒤 `docker logs marketlens-server` 에 `GET /spreads` 접근 로그가 **0건**(nginx 가 안 보낸다), `marketlens-api` 에만 있다

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# server (2026-09-14)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m pytest -q
#   All checks passed! / 200 files already formatted / 559 passed
cd web && npm run lint && npm run build          # oxlint 0 / ✓ built
# 로컬 5컨테이너 (:8090 — 8080 은 다른 스택 점유), 거래소 도달 확인 뒤
WEB_PORT=8090 docker compose --env-file server/.env up -d --build
curl -s -o /dev/null -w '%{http_code}' localhost:8090/api/spreads        # 404 → 404 → 200 (2초 간격, 첫 요청이 want 를 쓴 뒤 4초)
#   200 본문: Content-Type application/json, rows 488·notional 1000·rate 1357, 최상위 6키
curl -s "localhost:8090/api/spreads?notional=1000"                       # 400 notional_fixed
curl -s -o /dev/null -w '%{http_code}' localhost:8090/api/spreads/x      # 404 — marketlens-server 로그에 "GET /spreads/x" (server 로 갔다)
docker logs marketlens-server 2>&1 | grep -c 'GET /spreads HTTP'         # 0
docker logs marketlens-api    2>&1 | grep -c 'GET /spreads HTTP'         # 23
docker compose --env-file server/.env exec -T redis redis-cli TTL spreads:latest   # 7 (폴링 안 한 지 15초 넘어 want 는 -2)
docker compose --env-file server/.env stop api
#   5초 간격 60초: /api/spreads 000(첫 요청 curl 8초 타임아웃) → 502, /api/history/candles 같음, /api/health 전부 200
docker compose --env-file server/.env start api                          # 4초 뒤 /api/spreads 404(키 만료) → 다음 폴링 200
docker compose --env-file server/.env stop redis
#   /api/spreads → 503 {"error":{"code":"redis_unavailable",…,"detail":{"reason":"Error -2 connecting to redis:6379. Name or service not known."}}}
docker compose --env-file server/.env start redis
#   2초 간격: 404 · 503 · 404 · 404 · 404 · 200 — 12초 (Redis 기동 포함)
docker compose --env-file server/.env down
```
미실행: DevTools 로 `/api/ws/` 만 막고 `/api/spreads` 호출 0건·재연결 반복을 눈으로 보는 항목(브라우저 없이 돌린 세션), EC2 항목(배포 뒤).

## 6. 갱신할 문서
- `docs/context/status.md` — spreads 행 server 열의 "`/spreads` 는 메모리(LiveStore)만 읽어 전 페어 표 — `notional` 규모로" 를 "`/spreads` 는 Redis `spreads:latest` 를 그대로 반환(두 역할 동일, 요청마다 `spreads:want` 갱신, 018), 표 계산은 017 게시기가 $1,000 으로" 로. 비고 열에 "스프레드 탭이 보는 컨테이너는 api 하나". deploy 행 web 열의 api 분기 목록에 `/api/spreads` 추가. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 018 행 DONE. 003 행 범위의 "`/spreads`(체결 규모별 서버 슬리피지)" 를 "`/spreads`(Redis 표 반환, 018)" 로, 016 행의 api 경로 목록에 `/spreads`. **항상 포함.**
- `docs/context/architecture.md` — 핵심 설계 결정 절의 "api 는 메모리 저장소를 갖지 않고 Influx 만 읽으며(…)" 에 "`GET /spreads` 는 Redis `spreads:latest` 를 돌려준다(018)" 를 덧붙인다. 배포 토폴로지 절의 "`/api/history/{premium,streaks,candles}` 와 `/api/ws/` 는 api 로, 그 외는 server 로" 에 `/api/spreads` 추가. "현재 구조" 절 spreads (003) 항목의 "router 2 엔드포인트" 설명에서 `/spreads` 를 "Redis 읽기(018)" 로 고치고, spreads-serve (018) 항목 신설(핸들러 위치·want 갱신·nginx 위치 1~3줄).
- `docs/context/dev-setup.md` — 5컨테이너 절의 "분리 확인: `stop api` 뒤 `/api/spreads` 는 정상" 을 "`/api/spreads`·`/api/history/candles` 둘 다 502, `/api/health` 는 200" 으로. 스모크 절의 `curl localhost:8000/spreads` 는 유지하되 "dev compose Redis 가 떠 있고 접속자(또는 이 curl 자체)가 want 를 쓴 뒤 5초 안에 200" 으로, `?notional=500000` 슬리피지 확인 줄은 삭제하고 `/analysis/slippage` 로 안내.
- `docs/context/db.md` — 키 `spreads:latest` 의 읽는 쪽 "api(첫 접속자의 시작 표)" 에 "·`GET /spreads` 응답(018)" 추가. 키 `spreads:want` 의 쓰는 쪽에 "·`GET /spreads` 요청마다" 추가. "읽는 쪽" 절의 "채널 `spreads`·키 `spreads:latest` 는 api 의 구독 허브가" 문장에 `GET /spreads` 도.
- `docs/specs/003-spreads.md` — §3.2 제목을 "표 계산 (017 게시기가 매초 호출한다)" 로, 0번(`notional` 파라미터) 삭제 → "규모는 서버 상수 `1000`(017)", 1·2번의 "**404** `market_data_not_found`" 를 "표를 만들지 않는다(018 §3.2)" 로. §2 범위의 `GET /spreads` 설명을 "HTTP 서빙은 018" 로. §3.4 에 "폴링 fallback 없음 — `GET /spreads` 는 브라우저가 부르지 않는 curl·진단용, api 가 Redis 에서 답한다(018)" 한 줄. §4 검증의 `notional`·404 항목 삭제.
- `docs/specs/016-process-split.md` — §3.1 `api` 서빙 경로 목록에 `/spreads`, Redis 용도 문장에 "`spreads:latest` 읽기·`spreads:want` 쓰기(018)". §4 격리 확인의 "`/api/spreads` 정상" 을 502 로.
- `docs/specs/017-spreads-push.md` — §2 "하지 않는 것" 의 "`GET /spreads` 제거(진단·curl 용으로 남긴다 — `notional` 쿼리·범위 규칙은 그대로)" 를 "`GET /spreads` 는 018 이 Redis 읽기로 바꾼다" 로. §3.1 의 "003 §3.2-0 의 기본값을 이 값으로 바꾼다. 범위 규칙 유지" 삭제. §3.4 의 폴링 fallback 항목을 "폴링 fallback 없음" 으로, §4 FE 항목에서 폴링 검증 삭제.
- `docs/specs/007-deploy.md` — §3 nginx 항목의 api 분기 목록에 `/api/spreads`.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `server/app/core/redis_bus.py` — `latest_and_want()`(GET latest + SET want EX 15 파이프라인 한 왕복), `RedisUnavailableError`(redis 예외 → HTTP 경계).
  - `server/app/features/spreads/router.py` — `router`(`GET /spreads`: notional 400 → Redis 읽기 → 200 바이트 그대로 / 404 / 503)와 `refresh_router`(`POST /refresh`) 분리. `server/app/main.py` — 두 lifespan 에 `app.state.spreads_bus`, `router` 는 두 역할 모두·`refresh_router` 는 collector 만. `service.py` — `MIN_NOTIONAL`·`MAX_NOTIONAL` 삭제(쿼리 검증 전용이었다). `push.py` — 표를 만들지 않는 조건의 근거 주석만.
  - `web/nginx.conf` — `location = /api/spreads` → `api:8000`(rewrite 접두 제거).
  - 테스트: `features/spreads/tests/helpers.py`(`make_bus`·`spreads_json`·`make_client(bus=)`), `test_spreads_api.py`(HTTP 계약 8개 + 계산 규칙은 `spreads_json`), `test_push.py`(게시 전 404 → 게시 뒤 바이트 동일, 표를 만들지 않는 조건), `test_slippage.py`·`test_usdt_staleness.py`·`history/tests/test_point_rules.py`·`tests/test_tick_store.py`·`tests/test_wallet_integration.py`(HTTP → `spreads_json`), `tests/test_role.py`(api 라우트 집합 + Redis 만 읽는 `GET /spreads`), `tests/test_deploy.py`(nginx 정확 일치 블록).
  - 문서: §6 의 9종 + `dev-setup.md` env 절의 `api` 역할 설명 한 줄(`GET /spreads` 추가).
  - **FE 폴링 fallback 삭제(2026-09-15, 사람 결정)**: `web/src/features/spreads/api.ts` 의 `fetchSpreads`·fallback 타이머·폴링 타이머 삭제, `shared/config.ts` 의 `SPREAD_POLL_MS`·`SPREAD_WS_FALLBACK_MS` 삭제. WebSocket 이 막히면 백오프 재연결만 반복한다. 017 §3.4·§4, 003 §3.4, status·architecture·CLAUDE.md 017 행 반영. `GET /spreads` 는 curl·진단용으로 남고 want 갱신 규칙도 그대로.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - `/refresh` 를 별도 `APIRouter` 로 분리 — 두 역할이 `GET /spreads` 를 공유하려면 같은 라우터에 두면 안 됐다(기능 폴더 안 구조).
  - `RedisBus` 에 공개 메서드 하나 추가(`latest_and_want`) — core 공개 함수라 원칙상 묻는 항목이지만 §3.4 가 "요청 단위·한 왕복" 을 이미 정해 그대로 옮겼다. 예외 이름은 005 의 `InfluxUnavailableError` 와 같은 꼴.
  - 400·503 의 `detail` 모양(§3.1 에 적음), 400 은 Redis 를 만지기 전에 거절해 want 를 안 쓴다(§3.1 에 적음).
  - nginx 는 정규식 확장 대신 정확 일치 `=` 하나 — "정확히 이 경로·하위 경로 없음" 을 nginx 문법 그대로 쓴 것(§3.3 고침).
  - §4 의 "`test_slippage.py` 는 손대지 않는다" 는 실제와 달랐다(그 파일이 `GET /spreads?notional=` 을 썼다) — 계산 함수 호출로 바꾸고 422 범위 테스트 2개 삭제, §4 문구 고침. 표 계산 검증 8곳(006·008·009·005 테스트)이 같은 이유로 `spreads_json` 으로 옮겨졌다 — history 테스트가 spreads 의 테스트 헬퍼를 import 한다(테스트 사이의 의존, 앱 코드 아님).
  - Redis 복구 실측 12초(Redis 기동 포함) → §4 "10초 안에" 를 "Redis 가 뜬 뒤 10초 안에" 로. `stop api` 첫 요청은 curl 타임아웃 뒤 502 — 016 §7 과 같은 현상.
- 남은 빚:
  - EC2 확인(§4 마지막 항목 — `marketlens-server` 로그에 `GET /spreads` 0건)은 배포 뒤. DevTools 로 WebSocket 만 막는 눈 확인은 사람 몫.
  - `MarketDataNotFoundError` 의 이름·메시지("POST /refresh 로 수집했는지 확인")는 더 이상 HTTP 로 나가지 않는데 남아 있다 — 다음 spreads 작업 때 "표를 만들지 않는 조건" 이름으로 정리.
  - 수집 프로세스의 나머지 HTTP(`/health/collect`·`/history/events`) 이동은 후속 스펙(범위 밖).
