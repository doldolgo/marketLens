# 025 — slack-alerts

상태: DONE | 의존: 011(health — 거래소별 실패 구간·kind), 016(role — collector/api), 018(spreads-latest — Redis `spreads:latest`·RedisBus)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 **사용자보다 먼저** 장애를 안다. 지금은 수집이 멈추거나 컨테이너가 죽어도 아무도 모른다(2026-09-12 장애 2건 모두 "나중에 알았다"). 끝나면 거래소 수집이 60초 이상 끊기거나, 처리 안 된 예외가 나거나, 프로세스가 반복 재시작하면 Slack 채널에 메시지가 오고, 복구되면 복구 메시지가 짝으로 온다. 박스가 통째로 죽는 경우는 외부 uptime 서비스가 `/health` 를 찔러 잡는다 — 그러려면 `/health` 가 "프로세스 살아있음" 이 아니라 "**데이터가 흐르고 있음**" 을 답해야 한다.

## 2. 범위
- 만드는 것: core 의 Slack 알림기(웹훅·억제·큐). 수집 실패 구간의 발생/복구 알림. ERROR 이상 로그의 Slack 전달. 처리 안 된 예외의 500 응답 형식 통일. 기동 알림. 수집기 심장박동(Redis) 과 그것을 반영하는 `/health`. 외부 uptime 감시 런북.
- 하지 않는 것: Sentry 등 외부 SDK(라이브러리 추가 없음 — httpx 로 웹훅만 부른다). 프런트엔드 에러 수집. 디스크·메모리 알람(CloudWatch Agent — 런북에 후속으로만 적는다). 일간 요약. 알림 이력 저장. Slack 외 채널.
- 바꾸는 기존 것:
  1. 001 의 `/health` — 항상 `ok` 였던 것을 §3.5 의 신선도 판정으로 바꾼다. 응답 키가 는다(`lastTickAt`).
  2. 011 의 실패 구간 — 구간이 60초를 넘길 때와 닫힐 때 알림이 붙는다. `/health/collect` 응답·`collect_fail` 점은 그대로.
  3. 001 의 에러 형식 — 처리 안 된 예외도 `{"error":{code,message,detail}}` 로 나간다(지금은 uvicorn 기본 500 HTML/텍스트).

## 3. 동작

### 3.1 읽는 계약 (복사)
- 011: 매 틱(1초) 거래소 5곳(`upbit`·`bithumb`·`binance`·`bybit`·`bitget`)을 판정한다. 실패는 kind 8종(`network`·`timeout`·`rate_limit`·`banned`·`unavailable`·`bad_request`·`bad_response`·`stale_stream`) 과 `message`(300자 상한)·`status_code`·`url`·`retry_after_sec` 를 갖고, 같은 거래소·같은 kind 의 연속 실패는 **한 구간**(`started_at`·`ended_at`·`count`)이다. kind 가 바뀌면 구간을 닫고 새로 연다. 연속 성공 3사이클이면 닫히고 종료 시각은 그 연속의 첫 성공 사이클이다(플래핑은 한 구간).
- 016: 같은 이미지가 `ROLE=collector|api` 로 나뉜다. collector 는 수집 전체, api 는 Influx 조회·`/ws/spreads`·`GET /spreads` 만. 두 역할 모두 `/health` 를 연다.
- 018: RedisBus 는 collector 가 표를 `PUBLISH` 하고 `spreads:latest`(TTL 10초) 에 두며, api 는 요청마다 `spreads:latest` 를 읽는다. 표는 구독자가 `spreads:want` 를 켠 동안만 만들어지므로 **표 키는 수집 생존의 증거가 못 된다** — 그래서 §3.4 의 심장박동 키를 따로 둔다.
- 001: 앱 로그는 표준 `logging` 으로 stderr 에 `%(asctime)s %(levelname)s %(name)s %(message)s`. `marketlens.*` 로거만 INFO. 모든 루프는 예외를 `logger.exception`/`logger.error` 로 남기고 계속 돈다.

### 3.2 알림기 — 웹훅·억제·큐
- 설정: `server/.env` 의 `SLACK_WEBHOOK_URL`(Slack Incoming Webhook URL, 사람이 만든다). **없으면 알림 기능 전체가 꺼지고** 앱은 그대로 뜬다(로컬·테스트 기본). 값은 로그·응답 어디에도 찍지 않는다.
- 보내는 것: `POST <url>` JSON `{"text": "<메시지>"}`. 타임아웃 5초. 응답이 2xx 가 아니거나 예외면 **버린다**(재시도 없음 — 알림 실패로 수집이 느려지면 안 된다). 실패는 `marketlens.notify` 로거에 WARNING 1줄, 같은 실패는 10분에 1줄.
- 메시지 앞머리는 항상 `[<role>]` — 어느 박스에서 났는지 한눈에 알기 위해서다. 예 `[collector] 🔴 upbit 수집 실패 …`.
- **억제**: 알림마다 문자열 **키**가 있다. 같은 키는 **600초에 1회**만 나간다(메모리, 프로세스 재시작이면 초기화). 억제된 알림은 세지도 않는다 — "n건 억제됨" 같은 집계는 하지 않는다(단순함).
- **큐**: 알림 요청은 호출한 자리에서 기다리지 않고 큐에 넣고 돌아온다(동기 함수, 즉시 반환). 별도 태스크 하나가 순서대로 보낸다. 큐 상한 100 — 가득 차면 새 알림을 버리고 WARNING 1줄(10분 억제). 태스크는 두 역할 모두 lifespan 에서 시작·종료한다.
- 공개 함수: core 가 제공하는 알림기는 `notify(key: str, text: str) -> None` 하나만 노출한다(다른 스펙이 붙일 수 있게).

### 3.3 무엇을 알리는가
| 상황 | 키 |
|---|---|
| 기동 | `startup` |
| 수집구간 | `outage:<exchange>` |
| ERROR로그 | `log:<logger>:<msg>` |
| 500응답 | `log:marketlens.main:unhandled` |

- **기동**: lifespan 시작 직후 `🟢 <role> 기동 (v<version>)`. 억제 덕에 `restart: unless-stopped` 재시작 루프는 10분에 1줄로 보인다 — 반복 재시작을 알아채는 용도다.
- **수집 구간 발생**: 011 의 진행 중 구간이 **열린 지 60,000ms 이상** 이 되는 첫 틱에 1회. 60초 미만에 닫힌 구간(재연결 몇 초)은 알리지 않는다. 문구: `🔴 <exchange> 수집 실패 <kind> <경과초>초째 · <status_code 있으면> · <message 앞 120자>`.
- **수집 구간 복구**: 발생 알림을 **보낸 구간**이 닫힐 때 1회. 문구: `🟢 <exchange> 복구 · <지속 m분 s초> · 실패 <count>회`. 발생 알림 없이 닫힌 구간은 복구도 없다. kind 가 바뀌어 새 구간이 열리면 새 구간의 60초부터 다시 센다(이전 구간의 복구는 kind 변경 시각에 나간다). 복구 키는 `outage:<exchange>:closed` — 발생 키와 다르므로 억제에 걸리지 않는다.
- **ERROR 로그**: 루트 로거에 핸들러 하나를 더 단다. `ERROR` 이상이고 로거 이름이 `marketlens.` 로 시작하는 레코드만(라이브러리·uvicorn 제외). `marketlens.notify` 자신은 제외(순환 방지). 키는 `log:<로거명>:<record.msg 앞 80자>` — **포맷 전 템플릿**이라 같은 코드 자리는 같은 키다(매초 도는 `logger.exception("… 다음 초에 계속")` 이 10분에 1줄이 된다). 문구: `⚠️ <로거명> <포맷된 메시지 앞 300자>` + 예외가 있으면 ` — <ExcType>: <str(exc) 앞 200자>`. 트레이스백 전체는 보내지 않는다(docker 로그에 있다).
- **처리 안 된 예외 → 500**: FastAPI 에 일반 `Exception` 핸들러를 더한다. 응답 `500 {"error":{"code":"internal_error","message":"internal error","detail":null}}`, 그리고 `marketlens.main` 로거에 `logger.exception("unhandled: %s %s", method, path)` 1줄 — 이것이 위 ERROR 규칙으로 Slack 에 간다. 예외 내용은 응답에 싣지 않는다.

### 3.4 수집기 심장박동 (Redis)
- collector 의 틱 루프가 **매 틱 끝에** Redis 키 `collect:heartbeat` 에 그 틱 시각(ms, 정수 문자열)을 `SET … EX 30` 으로 쓴다. 틱 루프 안의 예외로 틱이 건너뛰면 그 초는 안 쓴다 — 그것이 곧 신호다.
- 쓰기 실패는 틱을 막지 않는다. WARNING 1줄(10분 억제), 다음 틱에 다시 시도.
- api 는 이 키를 **읽기만** 한다. TTL 30초라 수집이 멈추면 키가 사라진다.

### 3.5 `/health` — 신선도
두 역할 모두 `GET /health`. 응답은 `{"status": <값>, "version": <버전>, "lastTickAt": <ms 또는 null>}`. `status` 가 `ok` 면 HTTP 200, 그 밖은 **503**(uptime 서비스가 상태코드만 보고 판정하게).

| status | 뜻 |
|---|---|
| ok | 흐름정상 |
| starting | 첫틱전 |
| stale | 30초무틱 |
| redis_down | Redis실패 |

- collector: 메모리의 마지막 틱 시각으로 판정. 기동 후 첫 틱 전 `starting`, 마지막 틱이 30,000ms 보다 오래면 `stale`. Redis 를 보지 않는다.
- api: `collect:heartbeat` 를 읽는다. 키 없음 → `stale`(`lastTickAt null`). 있고 `now − ts < 30,000` → `ok`, 아니면 `stale`. Redis 읽기 예외 → `redis_down`. 요청마다 Redis 1왕복(uptime 은 분당 1회라 부담 없음).
- 이 503 은 `{"error":…}` 형식이 아니다 — 상태 응답이지 에러가 아니다(001 계약 규칙의 예외로 architecture.md 에 적는다).

### 3.6 외부 감시 (사람이 한다 — 런북)
`docs/runbooks/uptime-monitor.md` 에 적는다: 무료 uptime 서비스(UptimeRobot 또는 Better Stack, Slack 연동 내장) 에 `https://kimptrack.com/api/health` 를 HTTP 모니터로 등록, 기대 상태 200, 가능한 최소 간격, 알림 대상 = 같은 Slack 채널. 503 도 다운으로 취급된다(수집 정체 = 장애). 배포 중 1~2분 다운 알림은 정상이라고 적는다. 후속 후보로 CloudWatch Agent 디스크 80% 알람을 남긴다. 무료 티어의 확인 간격·모니터 수는 가입 시점에 확인한다(문서에 숫자를 박지 않는다).

### 3.7 엣지
- `SLACK_WEBHOOK_URL` 없음: `notify()` 는 즉시 반환, 큐·태스크·로깅 핸들러 모두 만들지 않는다.
- 웹훅이 계속 실패: 알림은 버려지고 WARNING 이 10분에 1줄. 큐는 실패한 항목을 빼므로 차지 않는다.
- 같은 거래소가 60초 넘게 실패 → 복구 → 5분 뒤 다시 60초 넘게 실패: 두 번째 발생은 첫 발생에서 10분이 안 지나 **억제된다**. 그 구간의 복구도 "발생 알림을 보낸 구간" 이 아니므로 안 나간다. 10분 안 재발은 첫 알림이 아직 유효하다고 본다(단순함 — 억제 예외를 두지 않는다).
- 기동 직후 5거래소가 동시에 60초 실패: 거래소마다 키가 달라 5줄이 나간다(의도 — 어느 거래소인지 알아야 한다).
- ERROR 로그가 서로 다른 자리에서 초당 수십 개: 자리마다 10분 1줄, 큐 상한 100 이 마지막 벽.
- 알림 큐 종료: lifespan 종료 시 남은 항목은 최대 5초만 기다리고 버린다.

## 4. 검증
- 알림기: URL 없으면 `notify()` 가 아무것도 하지 않고 큐가 없다 / 같은 키 두 번째는 600초 안에 안 나간다 / 600초 지나면 다시 나간다 / 다른 키는 각각 나간다 / 큐 100 초과는 버린다 / 웹훅 5xx·예외에도 호출 쪽 예외 없음 / 본문이 `{"text": "[<role>] …"}` 이다.
- 로그 핸들러: WARNING 은 안 가고 ERROR 는 간다 / `httpx` 등 `marketlens.` 밖 로거는 안 간다 / `marketlens.notify` 는 안 간다 / 키가 포맷 전 템플릿이라 인자만 다른 두 레코드는 한 번만 간다 / 예외 타입·메시지가 문구에 붙는다.
- 수집 구간: 59초에 닫히면 알림 없음 / 60초 도달 틱에 발생 1회, 이후 틱은 더 안 감 / 발생 보낸 구간이 닫히면 복구 1회 / 발생 없이 닫히면 복구 없음 / kind 변경 시 이전 구간 복구 + 새 구간은 60초부터.
- 500 핸들러: 라우트가 `RuntimeError` 를 던지면 응답이 500 `internal_error` 형식이고 `marketlens.main` ERROR 1건.
- 심장박동: 틱마다 `collect:heartbeat` 가 틱 시각으로 `EX 30` / Redis 예외에도 틱 루프 계속(fakeredis).
- `/health`: collector 첫 틱 전 503 `starting` / 틱 후 200 `ok` / 31초 무틱 503 `stale`. api 키 없음 503 `stale` / 키 최신 200 `ok` / Redis 예외 503 `redis_down`.
- 기동: lifespan 시작 시 `startup` 키로 1회.
- 수동: `.env` 에 실제 웹훅을 넣고 로컬 기동 → 채널에 `[collector] 🟢 collector 기동` 1줄. 런북대로 uptime 모니터 등록 후 테스트 알림.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check .   # All checks passed / 214 files formatted
cd server && .venv/bin/pytest -q                                            # 737 passed
# 스모크(로컬 dev Redis + 가짜 웹훅 서버, ROLE=api): 기동 알림 본문 `{"text":"[api] 🟢 api 기동 (v0.1.0)"}` 수신,
# /health = 키 없음 503 stale → SET collect:heartbeat(now) 200 ok → 45초 전 값 503 stale, 404 는 알림 없음
# 수동(사람): .env 에 실제 웹훅 → 기동 알림 1줄 확인, 런북대로 uptime 모니터 등록 — 미실행
```

## 6. 갱신할 문서
- `docs/context/status.md` — 표에 `| slack-alerts | server: Slack 웹훅 알림(기동·수집 60초 구간 발생/복구·ERROR 로그·500)·심장박동 `collect:heartbeat`·`/health` 신선도(503) | - | 외부 uptime 은 런북 uptime-monitor.md, 사람이 등록 |` 행 추가. collect 행의 `/health` 를 `/health(신선도)` 로. "알려진 빚" 에 `(025) 디스크·메모리 알람 없음(CloudWatch Agent 후속) · 10분 안 재발 구간은 알림 억제됨` 추가.
- `CLAUDE.md` — 스펙 인덱스 025 행 상태 → DONE.
- `docs/context/architecture.md` — "현재 구조" 에 slack-alerts 항목(알림기 core 모듈·로그 핸들러·심장박동 키·`/health` 두 역할 판정, 2~3줄). "계약 규칙" 절에 "`/health` 의 503 은 상태 응답이라 `{"error":…}` 형식이 아니다" 한 줄.
- `docs/context/dev-setup.md` — "env (server/.env)" 절에 `SLACK_WEBHOOK_URL` 행(없으면 알림 꺼짐). "검증용 스모크" 절에 `curl -i localhost:8020/health` 기대값(200 ok / 503 stale).
- `docs/context/db.md` — "Redis" 절에 키 `collect:heartbeat`(값 = 틱 ms 문자열, TTL 30초) 한 줄, "읽는/쓰는 쪽" 문장에 "`collect:heartbeat` 는 collector 가 매초 쓰고 api 의 `/health` 가 읽는다" 추가.
- `server/.env.example` — `SLACK_WEBHOOK_URL=` 과 설명 2줄.
- `docs/runbooks/uptime-monitor.md` — 신규(§3.6).

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `server/app/core/notify.py`(신규)·`server/app/core/heartbeat.py`(신규)·`server/app/core/outages.py`(alerts 콜백·60초 임계·`notified`)·`server/app/core/redis_bus.py`(`set_heartbeat`·`heartbeat`)·`server/app/core/ticks.py`(`heartbeat` 싱크 자리)·`server/app/core/config.py`(`slack_webhook_url`)·`server/app/main.py`(알림기 설치·기동 알림·일반 `Exception` 핸들러·`/health` 신선도)·`server/.env.example`·`docs/runbooks/uptime-monitor.md`(신규)·테스트 `server/tests/test_notify.py`·`test_heartbeat.py`(신규)·`test_outages.py`·`test_health.py`. 기존 테스트 12곳의 `/health` 200 단언을 새 계약(첫 틱 전 `starting`·503)에 맞췄다.
- 추측한 지점: lifespan 전(테스트·기동 직전)의 api 역할은 Redis 자리가 없어 `starting` 으로 답한다(§3.5 에는 없던 경우). 심장박동은 직전 쓰기가 안 끝났으면 그 초를 건너뛴다(태스크가 쌓이지 않게). 발생 알림 `notified` 표시는 콜백이 없어도 찍힌다(동작 차이 없음). 로그 핸들러는 `create_app` 마다 루트에서 이전 것을 떼고 다시 단다(테스트가 앱을 여러 번 만든다).
- 남은 빚: 디스크·메모리 알람(CloudWatch Agent) 없음. 10분 안 재발 구간의 알림 억제(§3.7). 실제 웹훅·uptime 등록은 사람 몫이라 미실행.
