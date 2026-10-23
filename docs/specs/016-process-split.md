# 016 — process-split

상태: DONE | 의존: 007(deploy — compose·nginx·설정 계약 테스트), 005(history — `/history/*` 가 Influx 만 읽는다는 계약)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
Influx 를 읽는 무거운 조회(`/history/premium`·`/history/streaks`·`/history/streaks/bulk`·`/history/candles`)가 **수집 프로세스 밖의 별도 컨테이너**에서 돈다. 큰 조회는 전 코인 행을 메모리에 올리고 통째로 직렬화한다 — 그 CPU·메모리가 수집 프로세스의 이벤트 루프와 RSS 를 차지하지 않게 하는 것이 목적이다. 조회가 느리거나 조회 프로세스가 죽어도 거래소 스트림·틱 루프·Redis 인계는 그대로 돈다. **Influx 자체가 죽는 문제(전 구간 조회 OOM, status.md 알려진 빚)는 이 스펙이 풀지 않는다** — 같은 박스에 있는 한 프로세스를 나눠도 막을 수 없고, 조회 창 상한·컨테이너 메모리 상한(후속 스펙)의 몫이다. EC2 는 그대로 1대, 이미지도 하나다 — 실행 역할만 갈라진다.

## 2. 범위
- 만드는 것: 서버 설정 `ROLE`(`collector` | `api`), compose 서비스 `api`, nginx 경로 분기, 역할 계약 테스트
- 하지 않는 것: `/spreads`·분석(`/orderbook`·`/slippage`·`/arbitrage`·`/premium`·`/premium/scan`·`/matrix`)·`/refresh`·`/health/collect`·`/history/events` 의 이동(메모리 저장소를 읽으므로 수집 프로세스에 남는다 — 후속 스펙), 메모리 저장소의 외부 공유, api 컨테이너 다중화·워커 2개 이상, EC2 분리, 폴링→WebSocket, **Influx 장애 대응**(죽거나 늦게 뜰 때의 재접속·메모리 상한·조회 창 상한 — 전부 후속)
- 바꾸는 기존 것: 007 의 "컨테이너 4개·네 컨테이너" 문구 전부 → 5개, "server 는 uvicorn 워커 1개" 문장의 근거를 역할 기준으로. `web/nginx.conf` 에 경로 분기 추가. `tests/test_deploy.py` 계약 갱신. **`/history/events` 를 나머지 네 경로와 다른 라우터로 분리**(005 기능 폴더 안, 경로·응답 무변경 — api 역할이 events 만 빼고 include 하기 위해). `README.md` 의 컨테이너 수 문구(`tests/test_deploy.py` 가 검사한다)

## 3. 동작

### 3.1 역할
서버 설정 `ROLE` 은 env 로만 읽는다. 값은 둘, 기본은 `collector`.

| 값 | 뜻 |
|---|---|
| `collector` | 지금의 전체 동작 |
| `api` | Influx 조회 전용 |

- `collector`: 오늘의 `server` 컨테이너와 **완전히 같다.** 스트림·우주·틱 루프·인계·flusher·writer 태스크·원문 아카이브·입출금 조회 전부 돌고, api 에만 있는 경로(아래 관리자 피드·브라우저 수신)를 뺀 모든 엔드포인트를 서빙한다. 수집기에만 있는 관리자 피드 — 034 의 `/admin/aws`·`/admin/alerts`, 063 의 `/admin/aws/series`, 078 의 `/admin/replay/archive` — 도 이 역할이 답한다(AWS 자격증명이 collect 박스 역할에만 있다). 로컬 개발(`uvicorn app.main:app`)은 `ROLE` 을 안 주므로 이 역할이다.
- `api`: 기동 시 저장소에는 **Influx 클라이언트 생성·ping 만** 한다. 거래소 REST·WebSocket 에 연결하지 않고, Redis 는 표 구독(017 `spreads`·048 `gap`), 요청 단위 읽기·쓰기(018 `GET /spreads` 의 `spreads:latest` 읽기·`spreads:want` 쓰기, 022 `GET /landing` 의 `spreads:latest` 읽기, 025 `/health` 의 심장박동 읽기, 029·035·052·073 의 관리자 피드와 073 화면 이동 기록 수신), 052 화면 영역 통계 묶음 쓰기, 보낸 알림 기록(034)에만 쓰며(스트림 `ticks` 는 안 읽는다), S3 를 만지지 않는다. 백그라운드 태스크는 017 구독 태스크(표마다 1개 — `spreads`·`gap`(048), 구독은 각각 접속자가 있을 때만) + 접속마다 보내기 태스크 + 052 화면 영역 통계 10초 묶음 쓰기 태스크 1개(끌 때 한 번 더 보낸다) + `SLACK_WEBHOOK_URL` 이 있으면 025 알림 태스크 + `STATSD_ADDR` 가 있으면 027 게이지 태스크다. 그 밖에 관리자 피드가 필요로 할 때만 뜨는 데몬 스레드(039 DB-IP 받기, 069 도시 판·역방향 DNS — 도시 판은 볼륨에 파일이 있으면 기동 때도 한 번 읽는다)가 있다. 기동 시 복원(수집 실패 이력·사건·봉 버킷·spark)도 하지 않는다 — 이 중 하나라도 하면 두 프로세스가 같은 measurement 를 중복으로 쓰거나(`collect_fail`·`premium_event`·롤업) 거래소를 이중 구독한다.
- `api` 가 서빙하는 경로는 `/health`, `/history/premium`, `/history/streaks`, `/history/streaks/bulk`, `/history/candles` 다섯과 WebSocket 둘(017 의 `/ws/spreads`·048 의 `/ws/gap`), 018 의 `/spreads`(Redis 읽기), 022 의 `/landing`(Redis·Influx 읽기 요약 — 저장소가 안 되면 그 부분만 null, 항상 200), 그리고 api 에만 있는 관리자 피드·브라우저 수신 — 029 의 `/admin/status`, 035 의 `/admin/access`(067·069 의 `/admin/access/ips`·`ip`·`addrs`·`addr`)·`/admin/clarity`(068 의 `history`·077 의 `recordings`), 052 의 `POST /attention`·`/admin/attention`, 073 의 `POST /replay`·`/admin/replay/live`·`sessions`·`session`. `/history/*` 의 응답·파라미터·에러는 005·014 계약 그대로(Influx 불달·토큰 없음이면 503). **그 외 경로는 404**(FastAPI 문서 `/docs`·`/redoc`·`/openapi.json` 은 두 역할 모두 있다). `/history/events` 도 404 다 — 진행 중 사건을 메모리에서 읽는 엔드포인트라 `collector` 만 답할 수 있다.
- 두 역할 모두 기동을 마친 직후(요청을 받기 직전) GC 를 정리한다 — `gc.collect()` → `gc.freeze()` → 세대 임계 (2000, 10, 10)(001 §3.1, 2026-09-28 사람 결정). 프로세스 전역 설정이라 두 lifespan 이 각자 한다.
- `ROLE` 이 둘 중 하나가 아니면 **설정을 읽는 시점에** 실패한다(앱 객체를 만들기 전, lifespan 이 아니다 — 설정 오류 메시지에 허용값 둘을 적는다). 잘못 뜬 채로 조용히 수집이 두 벌 돌지 않게. 설정은 지금처럼 env 에서 읽되 모르는 키는 무시하는 규칙이라, `ROLE` 은 명시적 설정 항목이어야 검사가 된다.

### 3.2 compose
컨테이너 5개. 기존 4개(`server`·`web`·`influxdb`·`redis`)는 이름·설정 그대로 두고 `api` 를 더한다.

| 서비스 | 역할 |
|---|---|
| `server` | `collector` |
| `api` | `api` |

- `api` 는 `server` 와 **같은 빌드 컨텍스트·같은 이미지**다. `ROLE=api` 만 `environment` 로 준다. `server/.env` 를 같은 `env_file` 로 주입하고 `INFLUX_URL`·`REDIS_URL` 을 server 와 같은 `DATA_HOST` 치환식으로 덮는다(021 — 둘 다 data 박스를 본다, 기본값은 서비스 이름). `REDIS_URL` 은 017 의 구독용(안 덮으면 env_file 의 로컬 값을 컨테이너 안에서 쓴다). 컨테이너명 `marketlens-api`, 로그 상한 `json-file` 50MB × 3, `restart: unless-stopped`, 호스트 포트 없음, `depends_on` 없음(021 — 저장소는 다른 박스).
- `server` 에는 `ROLE` 을 주지 않는다(기본값). 주면 `collector` 여야 한다.
- `web` 은 `server`·`api` 둘 다에 `depends_on`.
- 호스트에 여는 포트는 여전히 web 하나.
- dev compose(`docker-compose.dev.yml`)는 안 바뀐다 — 로컬은 프로세스 하나로 돈다.

### 3.3 nginx 경로 분기
공개 server 의 `/api` 는 028 허용 목록만 백엔드로 넘긴다. 공개 분기는 전부 **정확 일치**이고(028), 넘길 때 `/api` 접두를 뗀다(쿼리스트링은 그대로 따라간다). 목적지는 그 경로를 답하는 역할이다.
- `api:8000`(같은 박스): `/api/history/candles`·`/api/landing`(022)·`/api/ws/spreads`(017)·`/api/ws/gap`(048)·`/api/attention`(052)·`/api/replay`(073). 브라우저 비콘 둘(attention·replay)은 POST 만 넘긴다(028).
- 수집기(`${COLLECT_HOST}:8000` — 021, 한 박스면 `server`): `/api/health`·`/api/health/collect`·`/api/history/events`·`/api/flow/netflow`·`/api/flow/recent`(050). `/api/history/events` 는 진행 중 사건을 메모리에서 읽으므로 수집기다.
- 공개 server 의 그 밖의 `/api` 는 028 허용 목록이 정한다 — 목록 밖은 백엔드에 넘기지 않고 404 JSON 이고, `/api` 에 걸리는 정규식 location 은 두지 않는다. `/api/history/premium`·`streaks`·`streaks/bulk` 는 공개에 없다 — 관리자 server(029)가 api 로 넘기고, 박스 안에서는 `http://api:8000/<경로>` 로 부른다.
- 프록시 헤더 4개(`Host`·`X-Real-IP`·`X-Forwarded-For`·`X-Forwarded-Proto`)는 두 목적지에 **기존 값 그대로** 붙인다(`Host` 는 `$http_host`).
- `api` 컨테이너가 죽어 있으면 api 로 가는 공개 경로만 502(프로세스가 죽어 재시작 중이면 즉시, `stop` 으로 정지돼 있으면 nginx 가 기동 시 푼 IP 로 연결을 시도하다 수 초~60초 뒤 502 또는 504), 나머지는 정상. 반대로 `server` 가 죽으면 api 로 가는 공개 경로는 정상이다 — 이게 이 스펙의 존재 이유다.
- `proxy_read_timeout` 은 손대지 않는다(기본 60초). 긴 조회는 005 §3.4 의 창 상한(streaks 7일·bulk 1시간·premium 1주)과 동시 1개 게이트가 막는다.

### 3.4 배포
deploy 워크플로(007)는 안 바뀐다. `up -d --build` 가 `api` 도 같이 빌드·기동한다. 같은 컨텍스트라 빌드 캐시를 공유해 두 번째 빌드는 즉시 끝난다.

### 3.5 엣지
- `api` 에서 Influx 토큰이 없으면 `/history/*` 503 — 005 와 같다. `/health` 는 Influx 와 무관하다(025 §3.5 판정).
- `api` 프로세스에 `S3_BUCKET`·거래소 키가 있어도 무시한다(연결 시도 자체를 안 한다). 로그에 "S3 버킷 접근 실패"·거래소 줄이 **찍히지 않아야** 한다 — 찍히면 역할 분기가 샌 것이다. Redis 줄은 017 구독이 남길 수 있다.
- `collector` 가 재시작해도 `api` 는 영향 없고, `api` 가 재시작해도 수집은 한 틱도 안 빠진다.
- 두 역할의 `/health` 는 025 §3.5 판정을 따른다(본문 `{"status","version","lastTickAt"}` 은 같고, collector 는 메모리의 마지막 틱, api 는 Redis 심장박동을 본다). 밖에는 nginx 의 `/api/health`(`server`)만 열고, api 는 canary(027)가 밖에서 본다(`/api/history/candles`·`/api/ws/spreads`). api 의 `/health` 는 관리자 페이지 `/svc/api/health` 에서도 본다(029).

## 4. 검증
- `ROLE=api` 로 만든 앱의 경로 집합이 §3.1 목록과 같고(WebSocket 은 `/ws/spreads`·`/ws/gap`), `/health` 는 025 판정대로(lifespan 전 503 `starting`), `/history/premium` 이 Influx 계약대로 답하며(토큰 없으면 503), `/refresh`·`/health/collect`·`/history/events`·`/orderbook/upbit`·수집기에만 있는 관리자 피드(`/admin/aws`·`/admin/alerts`·`/admin/aws/series`·`/admin/replay/archive`)가 404
- `ROLE=api` 앱의 기동 로그에 S3·거래소 관련 줄이 없고, 백그라운드 태스크는 표 구독 태스크 둘(017 `spreads`·048 `gap`)과 052 의 10초 묶음 쓰기 태스크 하나(웹훅·StatsD 설정이 없을 때 — `/ws/spreads`·`/ws/gap` 은 두 역할 모두)
- `ROLE` 없음 = `collector` = 오늘과 같은 라우트 집합(기존 테스트 전부 그대로 통과)
- `ROLE=foo` 는 설정을 읽는 순간 실패(앱 객체 생성 전)
- 두 역할 모두 기동을 마치면 GC 세대 임계가 (2000, 10, 10) 이고 기동 객체가 얼려져 있다
- compose 계약(`tests/test_deploy.py` 갱신): 컨테이너 5개·고정 이름, `api` 는 `ROLE=api` + `env_file` + `INFLUX_URL` 덮어쓰기 + `REDIS_URL` 없음 + 로그 상한 + 호스트 포트 없음, `server` 에 `ROLE` 없음, `web` 이 둘 다 `depends_on`, 호스트 노출은 web 하나. README 의 컨테이너 수 문구 검사는 5개 기준으로
- nginx 계약: 공개 허용 경로가 §3.3 대로 `api:8000`·수집기(`${COLLECT_HOST}:8000`)로 나뉘어 가고 접두가 떼지며(`/api/history/events` 는 수집기로), 공개 `/api` 의 나머지는 028 허용 목록 계약(전부 정확 일치·목록 밖 404 JSON·정규식 location 없음)
- `/history/events` 라우터 분리 뒤 005 의 기존 테스트가 전부 그대로 통과(경로·응답 무변경)
- 007 §4 검증 항목 전부 재통과(4컨테이너 → 5컨테이너로 문구만)
- 수동: 로컬 `WEB_PORT=8080 docker compose --env-file server/.env up -d --build` 후 `curl localhost:8080/api/history/candles?base=BTC` 가 `api` 컨테이너 로그에, `curl localhost:8080/api/history/events` 가 `server` 로그에 찍힌다. `docker compose stop api` 뒤 `/api/history/candles` 는 502/504(수 초~60초 뒤), `/api/history/events`·`/api/health` 는 그대로 답한다. `docker compose start api` 후 복구
- 수동(EC2): 배포 후 `docker stats` 에서 `marketlens-api` 가 뜨고 `marketlens-server` CPU 가 배포 전과 같다(수집 부하는 그대로여야 한다)

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# server (2026-09-14)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pytest -q
#   → All checks passed! / 193 files left unchanged / 542 passed
#   tests/test_role.py 4개(api 라우트 집합·404, 태스크 0개·금지 로그 없음, collector 기본, ROLE=foo 실패)
#   tests/test_deploy.py 갱신(5컨테이너, api 서비스 계약, nginx 정규식 분기 5경로 판정, README 문구)
cd web && npm run lint && npm run build      # oxlint 통과, ✓ built
# 로컬 compose (8080 은 다른 컨테이너가 점유 → 8090)
WEB_PORT=8090 docker compose --env-file server/.env up -d --build
docker compose --env-file server/.env ps     # marketlens-api·influxdb·redis·server·web 5개 Up, 호스트 노출은 web :8090 하나
curl -s -o /dev/null -w '%{http_code}' localhost:8090/api/health                              # 200 (server 로그)
curl -s -o /dev/null -w '%{http_code}' 'localhost:8090/api/history/premium?base=BTC&unit=week'  # api 로그에 "GET /history/premium?…" (접두 제거·쿼리 유지)
curl -s -o /dev/null -w '%{http_code}' 'localhost:8090/api/history/streaks/bulk?start=…&end=…' # 200, api 로그
curl -s -o /dev/null -w '%{http_code}' 'localhost:8090/api/history/candles?base=BTC'           # api 로그
curl -s -o /dev/null -w '%{http_code}' localhost:8090/api/history/events                       # 200, server 로그
curl -s -o /dev/null -w '%{http_code}' localhost:8090/api/spreads                              # 200, server 로그
docker logs marketlens-api 2>&1 | grep -Ec 'S3|업비트|빗썸|바이낸스|거래소|우주|스트림'      # 0 (Redis 줄은 017 구독)
docker compose --env-file server/.env stop api
curl … /api/spreads → 200, /api/health → 200, /api/history/candles?base=BTC → 504(첫 시도) / 502(14초 뒤, 재시도)
docker compose --env-file server/.env start api  # 3초 뒤 /api/history/candles 가 다시 api 의 답(503 storage_unavailable — 아래 §7)
docker compose --env-file server/.env down       # 볼륨 유지
```
EC2 `docker stats` 비교(수동, 배포 후)는 사람 몫으로 남김.

## 6. 갱신할 문서
- `docs/context/status.md` — deploy 행의 server 열을 "compose 5컨테이너(server·api·web·influxdb·redis …)" 로, web 열에 "`/api/history/{premium,streaks,candles}` 는 api 로 분기" 추가. "알려진 빚" 의 streaks 항목에 "전 구간 조회가 Influx 를 재시작시켜도 수집은 영향 없음(016)" 을 덧붙이되 상한 없음은 빚으로 유지. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스에 016 행 추가, 상태 DONE. **항상 포함.**
- `docs/context/architecture.md` — 원칙 절 "현재 서버는 uvicorn worker 1개만 사용한다" 문장을 "프로세스 역할은 `ROLE`(collector | api). collector 는 worker 1개 — `live_store` 가 프로세스 메모리라서. api 는 메모리 저장소를 갖지 않고 Influx 만 읽는다" 로. 런타임 절의 상시 태스크 목록 앞에 "collector 역할만" 을 명시. "배포 토폴로지" 절 첫 줄 "EC2 1대. … server·web·influxdb·redis 컨테이너를 실행한다" 를 "EC2 1대, 컨테이너 5개(server=collector·api·web·influxdb·redis)" 로, 같은 절의 "네 컨테이너 모두 compose 가 로그를…" 을 다섯으로, "서버 분리(DB/파싱 분리)는 추후" 문장을 "수집과 Influx 조회는 컨테이너가 다르다(016). 메모리 저장소 공유·EC2 분리는 후속" 으로. "현재 구조" 절의 deploy(007) 항목 "컨테이너 4개 `marketlens-*`" 를 5개로 고치고 process-split 항목 1~2줄 추가.
- `docs/context/dev-setup.md` — "env (server/.env)" 절에 `ROLE` 키(선택, 기본 collector, api 는 compose 전용). "docker 통합 기동" 절의 "네 컨테이너" 를 다섯으로 + `docker compose stop api` 로 분리 확인하는 한 줄.
- `docs/specs/007-deploy.md` — "4컨테이너·컨테이너 4개·네 컨테이너" 가 §2(배포용 4컨테이너)·§3(컨테이너 4개 목록, 테스트 계약 요약)·§4(네 컨테이너, 네 컨테이너 모두) 에 흩어져 있다 — **전부** 5개로. §3 컨테이너 목록에 `api` 한 줄, "server 는 uvicorn 워커 1개" 규칙에 "(collector 역할 기준, 016)" 을.
- `README.md` — "네 컨테이너" 를 다섯으로(`tests/test_deploy.py` 가 이 문구를 검사한다). 40줄 상한 유지.
- `server/.env.example` — `ROLE` 키 주석 1줄.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `server/app/core/config.py`(`role`), `server/app/main.py`(`_open_influx`·`_api_lifespan`·역할별 라우터), `server/app/features/history/router.py`(`events_router` 분리), `server/tests/test_role.py`(신규), `server/tests/test_deploy.py`, `server/.env.example`, `docker-compose.yml`(`api`), `web/nginx.conf`(정규식 location), `README.md`, `docs/context/{architecture,dev-setup,status}.md`, `docs/specs/007-deploy.md`, `CLAUDE.md`.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - `ROLE` 검사는 pydantic `Literal` 로 — `Settings()` 생성 시 `ValidationError`, 메시지에 허용값 둘이 실린다. `create_app()` 은 `FastAPI()` 를 만들기 전에 `get_settings()` 를 부른다.
  - `events_router` 는 새 파일이 아니라 같은 `router.py` 안의 두 번째 `APIRouter` — 경로·응답·헬퍼 무변경.
  - nginx 분기는 정규식 location(`~ ^/api/history/(premium|streaks|candles)`) + `rewrite … break` + URI 없는 `proxy_pass`. 앞부분 일치라 `/api/history/premiumX` 같은 없는 경로도 api 로 가서 404 — 스펙 문구 그대로.
  - 역할 테스트의 "라우트 집합" 은 `app.openapi()["paths"]` 로 본다(FastAPI 0.141 이 include 한 라우터를 중첩 객체로 두어 `app.routes` 를 직접 훑을 수 없다).
  - **§3.3·§4 고침**: `stop api` 뒤 네 경로의 응답을 "502" → "502 또는 504, 수 초~60초 뒤" 로. 정지된 컨테이너는 nginx 가 기동 시 푼 IP 에 연결을 시도하다 실패하므로 즉시 502 가 아니다(실측 504 → 14초 뒤 502). 프로세스가 죽어 재시작 중인 경우(connection refused)만 즉시 502. 격리 자체(`/api/spreads`·`/api/health` 정상)는 확인.
  - 로컬 8080 은 다른 스택이 점유해 8090 으로 검증. dev-setup 의 8080 문구는 그대로.
- 남은 빚:
  - 컨테이너 기동 순서: `depends_on` 은 준비를 기다리지 않아 server·api 모두 Influx 보다 먼저 떠 첫 ping·복원·봉 버킷 생성이 실패한다(로컬 실측 — 그래서 `/history/candles` 가 버킷 없음으로 503). 스펙 §1·§2 가 명시한 후속(Influx 장애 대응) 범위 — 여기서 손대지 않았다.
  - `stop` 된 upstream 에 즉시 502 를 주려면 nginx `resolver` + 변수 upstream 이 필요하다 — 트레이드오프라 후속 인프라 스펙에서.
  - EC2 `docker stats` 비교(§4 마지막 항목)와 `docker compose exec api` 헬스 확인은 배포 후 사람이.
- 2026-09-28 성능 개선: 두 lifespan 이 yield 직전에 GC 를 정리한다(§3.1·001 §3.1). 점검 분석은 (10000, 50, 100) 을 권했지만 자동 전체 수집을 사실상 꺼 오래 살다 버려진 순환 객체가 치워지지 않아(검증 모의 300초 동안 0개 회수) 사람 결정으로 (2000, 10, 10) 을 택했다. 행 교체 3,000/초 모의에서 전체 수집 1회 정지가 로컬 33 → 약 10ms 다(점검 보고서 재현값). 배포 뒤 RSS 추이는 사람이 본다.
