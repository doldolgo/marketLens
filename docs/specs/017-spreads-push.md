# 017 — spreads-push

상태: DONE | 의존: 016(process-split — `api` 역할·nginx 분기), 003(spreads — 표 계산·응답 모양), 009(tick-store — 틱 루프·Redis)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
스프레드 표가 1초 폴링이 아니라 **WebSocket 푸시**로 온다. 표는 수집 프로세스가 매초 만들고, `api` 컨테이너가 **바뀐 행만** 접속자 전원에게 같은 바이트로 뿌린다. 접속자가 늘어도 수집 프로세스의 일은 늘지 않고, 브라우저는 안 바뀐 행을 매초 다시 받지 않는다(실측 2026-09-14: 폴링 1명당 54KB/s·요청의 50%, 페이지 이동 시 499).

체결 규모는 **$1,000 하나로 고정**한다(2026-09-14 결정 — 규모 선택지는 없앤다). 표가 한 장이라 게시·구독·diff 상태도 하나다.

한계를 먼저 적는다. 김프는 모든 행이 환율(`rate`)로 나뉘어 계산되므로 **환율이 한 틱 움직이면 약 490행 전부가 바뀐 행**이 되어 전체 크기 프레임이 나간다. `spark` 도 벽시계 1분 버킷이라 **매분 롤오버 때 전 행이 바뀐다.** 실측(§5)으로는 평상시에도 매초 약 35% 의 행이 바뀐다 — 절감은 나머지 65% 뿐이고, 브라우저 대역폭은 프레임 압축(§2)이 맡는다.

## 2. 범위
- 만드는 것: 수집 프로세스의 표 게시(Redis), `api` 의 `/ws/spreads`, FE 의 WebSocket 구독(폴링 대체), nginx·vite 의 WebSocket 프록시, 체결 규모 고정(`$1,000`) — 규모 세그먼트·URL `n` 삭제
- 하지 않는 것: `GET /spreads` 의 서빙 방식(018 이 Redis 읽기로 바꾼다), 분석 엔드포인트·`/health/collect` 의 푸시화, 호가 원본 공유, 인증, 접속자 수 집계 화면, 티커 기반 김프의 브라우저 계산, 수집 프로세스의 페이로드 압축(수집 → Redis → api 는 로컬 도커망이라 이득이 없고 수집 CPU 만 쓴다 — 압축 없이 게시한다. api → 브라우저 프레임은 uvicorn 기본 permessage-deflate 가 압축한다 — api 쪽 CPU 이고 접속자 수에 비례하며 별도 설정 없음. 실측(§5) 바뀐 행 35% × 238KB ≈ 83KB/s 라 압축 없이는 폴링(gzip 54KB/s)보다 브라우저 대역폭이 커진다. HTTP 는 기존 GZip 미들웨어)
- 바꾸는 기존 것: 016 §3.1 — `api` 역할이 Redis 에 **구독 목적으로만** 연결하고(스트림 `ticks` 는 안 읽는다) 백그라운드 태스크를 **구독 태스크 하나** 가진다, 서빙 경로에 `/ws/spreads` 추가, compose `api` 에 `REDIS_URL`. 003 §3.4 — FE 1초 폴링 절을 이 스펙의 구독으로 교체, §3.5 규모 선택지 삭제. 002 §3.5 — URL 쿼리 `n` 삭제. 007 — nginx 에 WebSocket 위치 추가.

## 3. 동작

### 3.1 수집 프로세스 — 표 게시
- 규모는 서버 상수 **`1000`**(USD) — 표는 이 한 장뿐이다(003 §3.2-0).
- **누가 볼 때만 만든다.** 서빙 프로세스(3.2)는 접속자가 1명 이상이면 Redis 키 `spreads:want` 를 **5초마다 `SET … EX 15`** 로 갱신한다(첫 접속자가 생기는 순간에도 1회). 수집 프로세스는 이 키를 **5초마다** 읽어 메모리에 "원함" 으로 들고, 틱 루프는 그 메모리 값만 본다(틱 안에서 Redis 를 부르지 않는다 — 003 §2 의 "표 계산은 await 없음" 유지). 아무도 안 보면 표를 안 만든다 — 접속자 0명일 때 수집 프로세스의 일이 오늘과 같아야 한다. 첫 접속자는 키 갱신 → 수집 반영 → 첫 게시까지 **최대 5초** 기다린다(그동안 `waiting`, 3.3).
- 틱 루프가 틱을 만든 직후, 같은 회차에서 표를 003 §3 규칙 그대로 만든다(`GET /spreads` 응답과 **같은 JSON**, camelCase, `fetchedAt` 은 만든 시각). 만든 표는 틱 인계(009)와 같은 방식으로 큐에 넣고 별도 태스크가 Redis 로 보낸다 — 게시 실패가 틱을 막지 않는다. 큐는 2장까지 — 밀린 표는 값이 없으므로 오래된 것부터 버린다.
- 게시: `PUBLISH spreads <JSON>` + `SET spreads:latest <JSON> EX 10`. 채널은 실시간, 키는 늦게 붙은 구독자(api 재기동·첫 접속자)의 첫 표다. TTL 10초 — 수집이 멈추면 키도 사라져 "없음"이 곧 신호다.
- Redis 불달이면 이 회차 게시는 건너뛰고 경고 1줄, 같은 원인은 60초에 1줄(009 의 인계 실패 로그와 같은 톤). `spreads:want` 를 못 읽으면 직전 값을 유지한다.
- 표 생성(JSON 직렬화 포함)이 한 회차 **300ms** 를 넘으면 경고 1줄. 실측(§5): 표 1장 = 로컬 M4 약 16ms(계산 5ms + camelCase 변환 7ms + JSON 2.5ms), EC2 는 코어당 3배쯤 느려 약 50ms 추정. 이 시간만큼 틱 루프가 멈추므로(거래소 메시지 처리가 밀린다) 상한을 넘으면 사람이 알아야 한다.

### 3.2 서빙 프로세스 — 구독·diff·브로드캐스트
`api` 역할이 한다. 로컬 개발은 프로세스 하나(`collector` 역할)라 그 프로세스가 자기 게시를 자기 구독해 같은 일을 한다. 배포의 `server` 컨테이너도 `collector` 역할이라 같은 코드가 뜨지만, nginx 가 `api` 로만 보내므로 접속자가 없고 아래 규칙대로 아무 일도 안 한다.
- 기동 시 Redis 에 연결해 채널 `spreads` 를 구독한다. 이 구독 연결은 오래 살아 있으므로 끊기면 **1→2→4→…→30초** 백오프로 다시 연결한다(기존 Redis 클라이언트는 명령마다 lazy 연결이라 백오프가 없다 — 이 스펙이 새로 만드는 동작이다). Redis 불달이어도 `/ws/spreads` 는 열리되 표가 없다. 구독 태스크가 `spreads:want` 갱신(5초)도 맡는다 — 태스크는 하나다.
- **접속자가 1명 이상일 때만** 상태를 든다: **직전 표 1장**. 첫 접속자가 생기면 `spreads:latest` 를 읽어 시작 표로 삼고(없으면 `waiting`), 접속자가 0명이 되면 직전 표를 버린다. 접속자 0명일 때 채널 메시지는 **파싱하지 않고 버린다** — 배포의 `server` 컨테이너에 비용이 없게.
- 새 표가 오면 diff 를 1회 만들고, 접속자 전원에게 **같은 직렬화 결과**를 보낸다. 접속자별 직렬화는 없다.
- diff 규칙: 행의 키는 `(sym, dom, fx)`. 행이 **바뀜** = `age` 를 제외한 어느 필드라도 직전과 다름(`spark` 배열 포함). `age` 만 다른 행은 안 보낸다 — 매초 모든 행의 `age` 가 늘어 diff 가 무의미해지기 때문이고, 브라우저는 안 실린 행을 서버 값 그대로 두다가 delta 가 끊길 때만 스스로 키운다(3.4). 직전에 있었는데 사라진 키는 `removed` 에 넣는다. `status` 가 바뀌면(ok→stale 등) 그 행은 바뀐 행이라 현재 `age` 와 함께 나간다. 환율이 바뀐 초와 매분 롤오버 초에는 사실상 전 행이 실린다(§1) — 그래도 delta 로 보낸다, 특별 취급 없음.
- **하트비트**: 어느 연결에든 **1초 넘게 아무것도 안 보냈으면** `{"type":"heartbeat"}` 를 보낸다. 표가 매초 오면 delta 가 그 자리를 채우고, 수집이 멈추면 heartbeat 만 간다. 브라우저 JavaScript 는 WebSocket ping/pong 프레임을 볼 수 없으므로 살아 있음은 **메시지로만** 알린다 — 따로 ping 프레임을 보내지 않는다. nginx 유휴 타임아웃(기본 60초)도 이 1초 메시지로 넘긴다.
- **느린 클라이언트**: 연결마다 보내기 대기열을 따로 두고 서로 기다리지 않는다. 대기열에 **5개**(약 5초분) 이상 쌓이면 그 연결을 코드 1008 로 닫는다. 한 명이 전원을 늦추지 않게. (전송 시간으로 자르지 않는 이유: 순차 전송이면 한 명이 제한 시간만큼 전원을 막는다.)

### 3.3 외부 계약 — `GET /ws/spreads` (WebSocket 업그레이드)
- 인증 없음(`GET /spreads` 와 같다). 서브프로토콜 없음. 메시지는 텍스트 JSON. 클라이언트 → 서버 메시지는 없다 — 보내도 무시한다.
- 서버 → 클라이언트:
  - `{"type":"snapshot", "notional":1000, "rate":…, "rows":[…17키 행 전부…], "warnings":[…], "dataReceivedAt":…, "fetchedAt":…}` — 접속 직후(표가 있으면), 그리고 `waiting` 뒤 첫 표가 왔을 때. 행은 003 §3.2 그대로.
  - `{"type":"delta", "notional":1000, "rate":…, "rows":[바뀐 행 전부 17키], "removed":["BTC|upbit|binance", …], "warnings":[…], "dataReceivedAt":…, "fetchedAt":…}` — 새 표마다. `removed` 의 원소는 `sym|dom|fx`. 바뀐 행이 없으면 `rows: []` 로 보낸다(`rate`·`warnings`·시각은 갱신돼야 하므로).
  - `{"type":"heartbeat"}` — 1초 넘게 보낸 게 없을 때(3.2).
  - `{"type":"waiting"}` — 접속했는데 표가 아직 없을 때(수집 기동 직후·Redis 불달·첫 접속). 첫 표가 오면 snapshot.
- 닫기 코드: 1008 = 느린 클라이언트, 1001 = 서버 종료. 클라이언트는 어느 코드든 재연결한다.

### 3.4 FE — 구독
- 셸이 공유 피드를 만든 직후 **연결 하나**를 연다(폴링이 살던 자리). 주소는 `API_BASE` 가 상대경로(`/api`)이므로 **페이지 origin 기준 절대 URL** 로 만든 뒤 스킴만 `http→ws`·`https→wss` 로 바꾼다 — 결과는 `ws(s)://<host>/api/ws/spreads`.
- `snapshot` → 행의 `dom`/`fx` 를 표시명으로 바꿔 공유 피드에 통째 교체(003 과 같다). `delta` → 키로 행을 갱신·추가, `removed` 를 삭제, `rate` 갱신. **안 실린 행의 `age` 는 서버가 마지막에 준 값으로 되돌린다** — delta 가 안 실었다는 것은 서버 판정(`status`·`age` 기준 stale 여부)이 안 바뀌었다는 뜻이므로(조용한 코인의 호가는 안 바뀌어도 현재값, 003). 셸의 1.5초 tick 은 그대로 `age` 를 키우므로 **delta 가 끊기면**(수집 정지·연결 반쯤 죽음) 5초 뒤 전 행이 stale 로 간다. 스트림이 조용해져 서버 `age` 가 5 를 넘으면 `status` 가 바뀌어 그 행이 delta 에 실린다. `heartbeat`·`waiting` 은 표를 건드리지 않는다.
- **무응답 감지**: 어떤 종류든 서버 메시지가 **10초** 동안 없으면 연결을 닫고 재연결한다. 반쯤 죽은 연결(절전·망 전환)은 이것으로만 잡힌다.
- 끊기면 1→2→4→…→30초 백오프로 재연결한다(서버 메시지를 하나라도 받으면 백오프는 1초로 돌아간다). 재연결 대기 중에도 직전 표를 지우지 않는다.
- **폴링 fallback 없음**(2026-09-15 결정): WebSocket 이 막힌 망에서는 위 백오프 재연결만 반복하고 표는 뜨지 않는다. 브라우저는 `GET /spreads` 를 부르지 않는다 — 그 엔드포인트는 curl·진단용이다(018).
- 탭을 옮겨도 연결은 유지한다(헤더 KPI 의 환율이 이 연결에서 온다). 페이지를 닫으면 브라우저가 연결을 닫는다 — 499 는 생기지 않는다.
- 스프레드 탭의 "체결 규모" 세그먼트는 없다 — 안내 문구가 `$1,000` 기준임을 말한다. URL 쿼리 `n` 도 없다(002 §3.5).
- `/health/collect` 5초 폴링(011)은 그대로.

### 3.5 프록시
- nginx: `location /api/ws/` (앞부분 일치) 를 `proxy_pass http://api:8000/ws/;` 로 넘긴다 — 접두 위치라 016 의 정규식 rewrite 는 필요 없다. `proxy_http_version 1.1` + `Upgrade`·`Connection "upgrade"` 헤더, 기존 프록시 헤더 4개 동일. `proxy_read_timeout` 은 **손대지 않는다**(기본 60초 — 1초 하트비트가 있어 안 끊긴다, 016 과 같은 입장).
- compose: `api` 에 `REDIS_URL: redis://redis:6379/0` 과 `depends_on: redis` — 없으면 `server/.env` 의 로컬 값(localhost)을 컨테이너 안에서 쓰게 된다.
- vite dev 프록시: `/api` 항목에 `ws: true`.

### 3.6 엣지
- 수집 프로세스 재시작: 채널이 잠시 조용 → api 는 heartbeat 만 → 브라우저 표가 stale 로 감 → 첫 표가 오면 delta(대부분 행이 바뀜). snapshot 을 다시 보내진 않는다.
- api 재시작: 접속자 전원 끊김 → 재연결 → 첫 접속이 `spreads:latest` 를 읽어 즉시 snapshot(키가 만료됐으면 `waiting`).
- 수집 프로세스가 `spreads:want` 를 읽는 사이 접속자가 사라지면 키가 15초 뒤 만료되고 다음 읽기에서 생성이 멈춘다 — 최대 20초 더 만든다, 문제 없음.
- 같은 초에 표가 2장 오면(재게시) 나중 것 기준으로 diff.

## 4. 검증
- **실측(구현 전, EC2)**: 연속 두 `GET /spreads` 응답을 1초 간격으로 5분간 비교해 (1) `age` 제외 바뀐 행 비율의 중앙값, (2) `rate` 가 바뀐 초의 비율, (3) 표 1장 생성 시간을 잰다. 결과를 §5 에 기록한다. (1) 이 절반을 넘거나 (3) 이 300ms 를 넘으면 실행 세션은 멈추고 사람과 3.1 의 상수를 다시 정한다.
- 수집: `spreads:want` 가 없으면 표를 만들지 않는다 / 있으면 채널·키가 쓰인다 / 채널 페이로드는 **같은 틱 안에서** 만든 `GET /spreads` 응답과 JSON 이 같다(시각 필드 `fetchedAt`·`dataReceivedAt`·`age` 는 같은 틱에서 비교)
- 수집: Redis 불달이면 게시만 건너뛰고 틱 인계는 계속
- diff: `age` 만 바뀐 행은 안 실린다 / `status` 가 바뀐 행은 실린다 / 사라진 행은 `removed` / 바뀐 행 0개여도 delta 1개
- diff: 접속자 3명에게 간 바이트가 동일하다 / 접속자 0명일 때 채널 메시지는 파싱되지 않는다
- `/ws/spreads`: 접속 → snapshot, 표 없음 → waiting → 첫 표에 snapshot, 1초 무전송 → heartbeat, 클라이언트 메시지는 무시
- 서빙: 첫 접속자가 `spreads:latest` 로 즉시 snapshot 을 받는다 / 접속자가 있으면 `spreads:want` 가 5초마다 갱신된다 / 0명이면 갱신하지 않는다
- 느린 클라이언트 대기열 5개 → 1008, 다른 접속자는 지연 없음
- nginx·compose 계약: `/api/ws/` 위치의 Upgrade 헤더·`proxy_http_version 1.1`·목적지 `api:8000/ws/`·`proxy_read_timeout` 없음, `api` 의 `REDIS_URL`·`depends_on` (`tests/test_deploy.py`)
- 역할: `api` 역할의 백그라운드 태스크는 구독 태스크 1개, `/ws/spreads` 는 두 역할 모두 (`tests/test_role.py`)
- FE: snapshot 교체·delta 병합·removed 삭제·age 자체 증가·10초 무응답 재연결·백오프·WebSocket 차단 시 `GET /spreads` 호출 0건·재연결만 반복 (러너 없음 — 수동)
- 수동: 브라우저 DevTools 에서 20초 관찰 시 `/spreads` 요청 0건, WS 프레임 초당 1개(delta 또는 heartbeat). 탭 이동 후에도 프레임 계속. `docker compose stop server` 후 heartbeat 만 오고 표가 stale 로 가고, 재기동 후 복구
- 수동(EC2): 접속자 5명을 열어 두고 `docker stats` 의 `marketlens-server` CPU 가 접속자 1명일 때와 같다 / 접속자 0명이면 오늘과 같다

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
**§4 실측 (2026-09-14, EC2 `ssh team`, 구현 전)** — `GET localhost:80/api/spreads?notional=100` 을 1초 간격 5분(299회 비교, 488행):
| 항목 | 값 |
|---|---|
| age 제외 바뀐 행 비율 중앙값 | 35% (평균 39%, p90 58%) |
| rate 가 바뀐 초 비율 | 2.7% |
| 바뀐 행 10% 미만인 초 | 0% |
| 분 롤오버 초의 바뀐 행 비율 | 33~51% |
| 표 1장 크기(압축 전) | 238KB |
| HTTP 응답 시간(nginx 경유·gzip) | 중앙값 100ms, p90 210ms |
| HTTP 응답 시간(컨테이너 안·압축 없음) | 중앙값 90ms, p90 171ms, 최대 307ms |

HTTP 응답 시간은 생성 시간의 상한이다 — 수집 프로세스 CPU 50% 상태에서 이벤트 루프 대기가 얹힌다. 로컬 M4 분해(500행·실제 호가 깊이): `build_spreads` 5.4ms · model_dump 0.6ms · camelize 7.0ms · json.dumps 2.5ms(242KB) · gzip 17.5ms. → (1) 통과, (3) 은 틱 안 실제 비용 기준 약 50ms 로 판단해 300ms 유지. 같은 날 **규모를 $1,000 하나로 고정**하기로 했다(사람 결정).

```bash
# server (2026-09-14)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check .   # All checks passed · 199 files already formatted
.venv/bin/pytest -q                                                        # 555 passed — 신규 12(test_push.py 9·test_ws.py 3), test_role·test_deploy·test_slippage·test_spreads_api 갱신
# web
cd web && npm run lint && npm run build                                    # oxlint 경고 0 · 451KB(gzip 143KB)
# 로컬 단일 프로세스(uvicorn :8020 + dev compose Redis, 실거래소) — websockets 클라이언트 8초 관찰
#   0.03s waiting → 1~4s heartbeat → 4.10s snapshot(488행 242KB) → 매초 delta(173~232행 86~116KB) · 빈 초 heartbeat
#   접속 중 TTL spreads:want=11 · spreads:latest=10 · STRLEN latest=242301 / 접속 종료 21초 뒤 want=-2(만료) · "표 생성"·"게시 실패" 경고 0줄
# 도커 5컨테이너: WEB_PORT=8090 docker compose --env-file server/.env up -d --build  + Playwright(chromium)
#   WebSocket 1개 ws://localhost:8090/api/ws/spreads, 20초 동안 GET /spreads 0건, 프레임 28개(waiting 1·snapshot 1·delta 23·heartbeat 14 ≈ 초당 1개)
#   기록 탭으로 이동한 5초 동안 프레임 7개 지속 · 화면에 "체결 규모 $1,000 …" 안내 1개 · "$10k" 세그먼트 0개
#   가동 중 흐린(stale) 코인 행 0/292 · docker compose stop server → 12초 동안 heartbeat 14개만 → 292/292 stale → start server → 2.5초 뒤 첫 delta(228행) → 20초 뒤 stale 0개
#   docker logs marketlens-api: "WebSocket /ws/spreads [accepted]" 뿐, 경고·오류 0
# EC2: §4 실측(위 표) 완료. 접속자 5명 vs 1명 vs 0명 CPU 비교는 배포 뒤 사람이 본다(§7 남은 빚)
```

## 6. 갱신할 문서
- `docs/context/status.md` — spreads 행 web 열 "1초 폴링·규모 세그먼트" → "`/ws/spreads` 구독(snapshot+delta, 5초 fallback 폴링)", server 열에 "누가 볼 때만 $1,000 표 매초 Redis 게시·api diff 브로드캐스트". **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 017 행 DONE. **항상 포함.**
- `docs/context/architecture.md` — 데이터 흐름 절에 "틱 → 표($1,000) → Redis pub/sub → api → WebSocket" 경로 추가. 원칙 절의 "실시간 조회 API 는 메모리만 읽고" 문장에 "표 푸시는 api 가 Redis 구독으로" 를 덧붙이고, 같은 절의 **"조회 경로에 Redis·S3 호출은 없다"** 를 "HTTP 조회 경로에는 없다, WebSocket 푸시만 Redis 구독" 으로. 런타임 절의 **"`fetch` 폴링만 사용"** 문장과 FE 폴링 목록에서 `/spreads` 1초를 빼고 WebSocket 1개로, api 태스크 0개 → 1개. "현재 구조" 절에 spreads-push 항목. spreads 항목의 "기본 $10,000" → "$1,000 고정".
- `docs/context/db.md` — Redis 절 "키 하나: Stream `ticks`" 를 고치고 채널 `spreads`·키 `spreads:latest`(JSON, TTL 10초, 쓰는 쪽 수집·읽는 쪽 api)·키 `spreads:want`(TTL 15초, 쓰는 쪽 api·읽는 쪽 수집) 추가. **"Redis 는 flusher 만 읽는다"** 문장 수정.
- `docs/context/dev-setup.md` — 스모크 절 `notional == 10000` → `1000`, `/ws/spreads` 접속→snapshot 확인 한 줄. vite `ws: true`. api 역할 설명 "백그라운드 태스크 없음" → "구독 태스크 1개".
- `docs/specs/003-spreads.md` — §3.4 폴링 절을 "구독은 017" 로 교체, §1 의 "1초 폴링" 문구 **2곳**(한 줄 정의·범위 목록) 삭제. §3.2-0 기본 `10000` → `1000`, §3.5 규모 선택지(`$10k·$50k·$100k·$500k` 세그먼트) 삭제 → 고정 `$1,000` 안내 문구. §1 "기본 $10,000" 도 같이.
- `docs/specs/002-web-shell.md` — §3.5 URL 쿼리에서 `n`(체결 규모) 삭제.
- `docs/specs/009-tick-store.md` — §2 의 **"폴링 경로에 Redis·Influx 호출이 없다"** 를 "HTTP 폴링 경로에는 없다(WebSocket 푸시는 017)" 로.
- `docs/specs/016-process-split.md` — §3.1 `api` 역할의 "Redis 에 연결하지 않고" 를 "Redis 는 017 의 구독 목적으로만" 으로, "어떤 백그라운드 태스크도 만들지 않는다" 를 "017 의 구독 태스크 하나뿐" 으로, 서빙 경로 목록에 `/ws/spreads` 추가(**"그 외 경로는 404"** 는 유지). §3.2 compose 의 "REDIS_URL 을 덮지 않는다" 를 덮는 것으로. §3.5 엣지와 §4 검증의 "Redis 줄 없음·백그라운드 태스크 0개" 를 그에 맞게.
- `docs/specs/007-deploy.md` — §3 nginx 항목에 WebSocket 위치 한 줄, compose `api` 의 `REDIS_URL`.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): server `app/core/redis_bus.py`(신규), `app/features/spreads/push.py`·`hub.py`·`ws.py`(신규), `tests/test_push.py`·`tests/test_ws.py`(신규), `app/core/ticks.py`(틱 루프 `spreads` 자리), `app/features/spreads/service.py`(기본 규모 1000), `app/main.py`(두 lifespan 배선·`/ws/spreads` 포함), `tests/test_role.py`·`tests/test_deploy.py`, `features/spreads/tests/test_slippage.py`·`test_spreads_api.py`(기본값). 루트 `docker-compose.yml`(api `REDIS_URL`·`depends_on: redis`), `web/nginx.conf`(`location /api/ws/`), `web/vite.config.ts`(`ws: true`). web `src/features/spreads/api.ts`(구독 훅)·`types.ts`(메시지 타입)·`Tab.tsx`(세그먼트 삭제), `src/App.tsx`(규모 상태·URL `n` 삭제), `src/shared/config.ts`(상수)·`feed.ts`·`types.ts`(주석). 문서 — 이 스펙, `docs/context/{architecture,db,dev-setup,status}.md`, `docs/specs/{002,003,007,009,016}`, `CLAUDE.md`.
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - Redis 클라이언트를 009 의 `redis_stream.py` 에 합치지 않고 `core/redis_bus.py` 를 새로 뒀다 — 연결 수명(명령마다 lazy vs 오래 사는 구독)과 쓰는 프로세스가 달라서. "redis 를 import 하는 곳 하나" 는 둘이 됐다(architecture.md 현재 구조에 적음).
  - 게시기는 틱 루프의 013·014 다음 자리(`spreads`, 기존 `EventSink` 계약 재사용)에서 돈다 — `received_at` 이 찍힌 뒤라 `GET /spreads` 와 같은 표가 나온다. 게시 큐는 2장(밀린 표는 값이 없다), 종료 시 남은 표는 버린다.
  - 허브의 heartbeat 는 접속별 보내기 태스크가 대기열을 1초 기다리다 비면 보내는 방식. 구독 확인 같은 Redis 제어 메시지는 `Subscription.get` 이 건너뛴다. 라우터는 `receive_text` 루프로 클라이언트 메시지를 버리고, 서버가 먼저 닫은 뒤의 `RuntimeError` 도 정상 종료로 본다.
  - 역할 테스트: WebSocket 경로는 OpenAPI 에 안 실려 라우트 표를 재귀로 훑고, api 태스크는 이름 `spreads_hub` 하나로 단언. "Redis 줄 금지" 단언은 제거(구독 실패 경고가 날 수 있다).
  - `test_slippage.py` 의 3개 테스트는 $10,000 시드 기준 수식이라 `notional=10_000` 을 명시했다.
  - FE 테스트 러너가 없어 §4 FE 항목은 Playwright 스크립트(레포 밖 스크래치)로 확인했다. 개발 모드 StrictMode 는 연결을 2번 열고 첫 것을 바로 닫는다(정상).
- 실행 중 함께 고친 스펙 절(사람 합의): §1·§2·§3 전체 — 체결 규모 `$1,000` 고정(규모 세그먼트·URL `n`·`subscribe` 메시지·30초 타이머 삭제). §2 — api → 브라우저는 uvicorn 기본 permessage-deflate(압축 없이는 폴링보다 대역폭이 커지는 실측). §3.1 — 300ms 유지 근거·첫 접속 시 want 즉시 1회. §3.2 — 구독 태스크 하나가 want 갱신도. §3.4 — **안 실린 행의 age 는 서버 값으로 되돌린다**(실측: 매초 바뀌는 행 35% 라 조용한 코인이 5초마다 흐려졌다 밝아지는 깜빡임), 백오프 리셋은 서버 메시지 수신. §3.5 — compose `api` 의 `REDIS_URL`. 다른 스펙: 003 §1·§3.2-0·§3.4·§3.5·§4·§7, 002 §3.5, 009 §2, 016 §3.1·§3.2·§3.3·§3.5·§4·§5, 007 §3.
- 남은 빚:
  - 표 생성은 틱 루프 안 동기 — 해외 3거래소(1,463행) 뒤 EC2 실측 450ms 로 상한 300ms 를 매초 넘겨, 게시기는 모델·`camelize_json` 을 거치지 않는 dict 경로(`build_table`)·spark 삽입 시 반올림·마켓별 사는 쪽 걷기 메모로 줄였다(로컬 M4 66→19ms). 그래도 경고가 남으면 표 생성을 별도 프로세스로 옮기는 것을 검토한다("await 없음" 원칙은 유지). 다른 라우터의 `camelize_json`(004·005) 은 그대로다.
  - EC2 수동 확인 대기: 접속자 5명 vs 1명 vs 0명의 `marketlens-server` CPU, 브라우저 DevTools 에서 permessage-deflate 협상·프레임 크기.
  - 배포의 `server` 컨테이너도 허브를 띄워 채널을 구독한다(접속자 0 → 파싱 없음, 비용은 구독 연결 1개). 역할별 끄기 스위치는 두지 않았다.
  - api 프로세스가 멈추면 브라우저는 10초 무응답으로 먼저 재연결하고 nginx 는 60초 뒤 끊는다 — 둘 다 스펙대로지만 외부 헬스체크는 여전히 없다(016 남은 빚 그대로).
  - FE 테스트 러너 부재 — §4 FE 항목은 수동(Playwright)뿐이다.
