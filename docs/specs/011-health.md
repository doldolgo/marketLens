# 011 — health

상태: DONE | 의존: 001(collect — 사이클·커넥터·에러 예외), 002(web-shell — 셸·공유 피드·수집 상태 mock 탭), 003(spreads — FE 폴링 패턴), 005(history — Influx 쓰기·읽기)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
수집 상태 탭이 mock 대신 **실제 수집기의 거래소별 실패 이력**을 보여준다. 거래소가 실패하면 "언제, 어느 거래소가, 어떤 종류로(rate limit·차단·타임아웃·거래소 오류…), 거래소가 뭐라고 했는지" 를 카드·24시간 타임라인·로그에서 본다. 이력은 서버를 껐다 켜도 복원된다.
백오프·재시도 정책 같은 **대응**은 이 스펙이 아니다(후속 013). 이 스펙은 관측과 기록까지다.

## 2. 범위
- 만드는 것: `GET /health/collect`(BE, `server/app/features/health/`), 수집 실패 이력 추적(core — 수집기가 쓴다), Influx `collect_fail` 쓰기·기동 시 복원, `web/src/features/health/` 실데이터 탭(api·types 추가).
- 하지 않는 것: `GET /health` 변경(001 계약 "항상 ok" 유지 — 007 배포 헬스체크·005/010 장애 격리 검증이 여기에 걸려 있다). 백오프·`Retry-After` 존중·서킷브레이커(013). 입출금 조회·Influx persist·S3 snapshot 루프의 상태 표시. `collect_fail` 을 읽는 HTTP 이력 조회 API(메모리가 진실, Influx 는 복원용). FE 테스트 러너.
- 바꾸는 기존 것:
  1. 001 커넥터 3개 — 실패 예외에 **종류(kind)** 와 거래소 원문을 싣는다(§3.2). 빗썸은 HTTP 200 + 에러 본문을 실패로 판정한다. 성공 경로·행 조립·`/refresh` 응답 모양은 불변.
  2. 001 수집 사이클 — 거래소별 성공/실패를 매 사이클 이력 추적기에 넘긴다. 사이클 순서·메모리 교체 규칙 불변.
  3. 002 §3.9 수집 상태 mock 탭 → 이 스펙의 탭으로 교체. 002 §3.5 KPI `수집 상태` 블록의 출처를 mock 카드에서 `/health/collect` 로. 002 `shared/` 의 health mock 타입·생성기·피드 필드는 지운다.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001: 사이클은 1초 주기, 거래소 3곳(`upbit`·`bithumb`·`binance`). 거래소 하나의 실패는 사이클을 죽이지 않고 `{exchange, error_code, message}` 로 기록되며, 그 거래소의 직전 스냅샷은 유지된다. 실패 예외는 `ExchangeError` 공통 부모에 `exchange`·`url`·`message`·`status_code`(비-200 이면)·`body`(앞 500자) 를 가진다. 타임아웃은 `exchange_timeout`, 그 외는 `exchange_api_error`.
- 002: 셸은 1.5초 tick 으로 `now` 를 갱신하고, 탭은 언마운트하지 않고 숨긴다. 경과 표기 `N초 전`/`N분 전`/`N시간 전`. 상태색 정상 초록·지연 주황·끊김 빨강.
- 003: FE 폴링은 기능 폴더 안의 훅 하나(`setInterval` + 즉시 1회, 재진입 방지, 실패는 무시하고 직전 데이터 유지). 거래소 표시명 `upbit→업비트` `bithumb→빗썸` `binance→Binance`.
- 005: Influx org·bucket `marketlens`, `INFLUX_TOKEN` 없으면 Influx 비활성. 같은 tag set + 같은 time 은 덮어쓴다. Influx 가 닿지 않아도 앱은 뜬다.
- Influx 2.x 동작(외부 의존): 같은 measurement·tag set·time 으로 다시 쓰면 field set 은 **합집합**이 되고 겹치는 field 는 새 값이 이긴다. 이 스펙의 "열 때 쓰고 닫을 때 덮어쓰기" 가 이 동작에 기댄다.

### 3.2 실패 분류 — 커넥터가 정한다
실패 예외에 `kind` 를 추가한다. 분류는 **각 커넥터가 자기 거래소 규칙으로** 정한다(하류에서 상태코드로 추측하지 않는다 — 거래소마다 규칙이 다르다). 값 8종:

| kind | 뜻 |
|---|---|
| `timeout` | 응답시간초과 |
| `network` | 연결실패 |
| `rate_limit` | 한도초과 |
| `banned` | 차단 |
| `unavailable` | 거래소장애 |
| `bad_request` | 요청오류 |
| `bad_response` | 응답오류 |
| `stale_stream` | 스트림정체 |

`bad_request` 만 재시도 무의미(우리 요청이 틀림)이고 나머지는 일시적이다. 응답에 함께 남기는 것: `status_code`, `body` 앞 500자, `url`, `retry_after_sec`(헤더 `Retry-After` 가 초 단위 정수로 있을 때만, 아니면 null).

거래소별 규칙(공식 문서 2026-09-03 확인. 미확인 응답은 `bad_response` 로 두고 원문 body 를 남겨 분류표를 채운다):
- **업비트**: HTTP 429 → `rate_limit`("다음 초 경계까지 대기 후 재시도"). 418 → `banned`(429 누적 차단, 반복 시 차단 시간 누진). 5xx → `unavailable`(500 이 점검을 겸한다). 그 외 4xx → `bad_request`. 에러 본문은 `{"error":{"name":<int>,"message":…}}`.
- **빗썸**(v1 API): 문서상 에러는 HTTP 상태와 함께 `{"error":{"name":…,"message":…}}` 이지만 **실제로는 HTTP 200 에 이 본문을 준다**(`markets=KRW-XXXX` 실호출로 확인). 그래서 200 이어도 본문이 리스트가 아니고 `error` 키가 있으면 실패다. `error.name` 이 정수면 그 값을 HTTP 상태처럼 위 업비트 규칙으로 분류하고, 아니면 `bad_response`. 429/418 의 실제 응답은 문서에 없다(미확인). 이때 `status_code` 는 실제 HTTP 상태(200)다.
- **바이낸스**: 429 → `rate_limit`, 418 → `banned`(IP 밴, 2분~3일 누진), 둘 다 `Retry-After` 초를 `retry_after_sec` 에. 403 → `banned`(WAF — "rate limit violation or a security block"). 5xx → `unavailable`. 그 외 4xx → `bad_request`. 에러 본문 `{"code":-1003,"msg":…}`.
- 공통: httpx 타임아웃 → `timeout`. 그 외 httpx 전송 예외(DNS·연결 거부) → `network`. JSON 파싱 실패·예상 밖 모양 → `bad_response`.
- **`stale_stream`** 은 HTTP 응답이 아니라 상시 연결이 조용히 멈춘 상태다(FE 유형 칩 라벨 `스트림 정체` — §3.8 의 라벨 표에 함께 있어야 타입이 맞는다) — 012 의 깊이 스트림이 구독 중인데 30초 무수신이면 그 사이클의 바이낸스 수집이 이 종류로 실패를 던진다. `status_code`·`url` 은 null 이고, 이때도 REST 결과는 유효하므로 그 거래소 행은 직전 스냅샷으로 유지된다. 예외를 던지는 주체가 커넥터라 위 경로(collector → 구간 추적 → `collect_fail`)를 그대로 탄다.

### 3.3 실패 이력 — 구간(outage) 단위로만 기록
정상 사이클은 기록하지 않는다. 기록 단위는 **거래소별 연속 실패 구간** 1건이다.
- 구간 1건: `exchange`, `kind`, `started_at`(첫 실패 사이클 시각, epoch ms), `ended_at`(null = 진행 중), `count`(실패 사이클 수), `last_failed_at`(가장 최근 실패 사이클 시각), `status_code`, `message`(거래소 원문 body 가 있으면 body, 없으면 커넥터 message. 300자 상한), `url`, `retry_after_sec`. 유일키 = (`exchange`, `started_at`).
- 열기: 열린 구간이 없는 거래소가 실패하면 연다. 이미 열려 있으면 `count` 를 올리고 `last_failed_at`·`status_code`·`message`·`url`·`retry_after_sec` 는 **최신 실패로 덮어쓴다**.
- `kind` 가 바뀌면(예: `timeout` → `rate_limit`) 현재 구간을 그 시각에 닫고 새 구간을 연다. 원인 전환이 이력에 남아야 한다.
- 닫기: 그 거래소가 **연속 3사이클 성공**하면 닫는다. `ended_at` 은 그 연속 성공의 **첫 성공 사이클 시각**이다(잠깐 성공했다 바로 다시 실패하면 같은 구간이 이어진다 — 플래핑을 한 구간으로 본다).
- 거래소별로 마지막 성공 사이클 시각도 기억한다(카드의 `마지막 수신`·상태 판정용).
- 보관: 메모리에 `ended_at` 이 24시간보다 오래된 구간은 버린다. 진행 중 구간은 길이와 무관하게 남는다.
- 사이클 시각은 그 사이클의 `fetched_at` 을 쓴다.

### 3.4 Influx `collect_fail` — 쓰기와 복원
- measurement `collect_fail`. tag `exchange`·`kind`, time = `started_at`(초 정밀도), field `count`(int)·`last_failed_ts`(int epoch 초)·`status_code`(int, 없으면 0)·`message`(string)·`url`(string)·`retry_after_sec`(int, 없으면 0)·`ended_ts`(int epoch 초, **닫힐 때만** 쓴다). 한 구간 = 점 1개. 복원 시 0 은 null 로 돌린다.
- 구간이 **열릴 때** 1점 쓰고, **닫힐 때** 같은 (tag, time) 으로 다시 써서 `ended_ts`·최종 `count` 등을 합친다. 매초 쓰지 않는다. 진행 중 구간의 `count` 는 메모리에만 있다.
- 쓰기 실패는 로그 1줄 후 무시한다. 열 때 실패했어도 닫을 때의 쓰기가 점을 만든다.
- **기동 시 복원**: 최근 24시간 `collect_fail` 을 읽어 메모리 목록을 채운다. `ended_ts` 없는 점은 진행 중 구간으로 복원한다 — 첫 사이클에서 성공하면 위 닫기 규칙대로 닫히고, 실패하면 이어서 센다. 복원된 진행 중 구간의 `count` 는 열 때 쓴 값에서 이어 센다(재기동 전 실패 횟수는 잃는다). Influx 가 없거나(`INFLUX_TOKEN` 미설정) 닿지 않거나 조회가 **3초**를 넘기면 빈 목록으로 시작하고 경고 로그 1줄. 복원은 수집 루프 시작 **전에** 끝난다.
- 서버 자체가 꺼져 있던 시간은 거래소 실패가 아니므로 구간을 만들지 않는다. 지난 재기동 시각은 기록하지 않는다(현재 기동 시각만 응답에 싣는다).

### 3.5 `GET /health/collect`
메모리만 읽는다. 거래소 호출·Influx 조회 0회. 인증 없음. 항상 200.
```json
{
  "serverStartedAt": 1756900000000,
  "fetchedAt": 1756903600000,
  "successRate1h": 99.8,
  "exchanges": [
    {"exchange": "upbit", "state": "ok", "lastSuccessAt": 1756903599000, "markets": 132,
     "successRate1h": 100.0, "openOutage": null,
     "lastError": {"at": 1756900123000, "kind": "timeout", "statusCode": null, "message": "업비트 응답 시간 초과: ReadTimeout"}}
    /* at = 그 구간의 lastFailedAt */
  ],
  "outages": [
    {"exchange": "binance", "kind": "rate_limit", "startedAt": 1756903000000, "endedAt": 1756903012000, "lastFailedAt": 1756903011000, "count": 12,
     "statusCode": 429, "message": "{\"code\":-1003,\"msg\":\"Too much request weight used; ...\"}",
     "url": "https://api.binance.com/api/v3/ticker/bookTicker", "retryAfterSec": 10}
  ]
}
```
- `exchanges` 는 001 의 거래소 3곳 고정 순서(`upbit`, `bithumb`, `binance`). `state`: 마지막 성공 후 경과 `< 5초` → `ok`, `5초 이상 60초 미만` → `stale`, `60초 이상` 또는 기동 후 성공 0회 → `down`. `markets` = 메모리 스냅샷의 그 거래소 행 수. `openOutage` = 진행 중 구간(모양은 `outages` 항목과 같음), 없으면 null. `lastError` = 가장 최근 구간의 최신 실패(진행 중이면 그것), 24시간 안에 없으면 null.
- `successRate1h` = `(1 − 최근 3600초 창과 겹치는 구간들의 겹친 초 합 / 3600) × 100`, 소수 1자리. 거래소별로 계산하고 최상위는 3곳 평균. 사이클이 1초 고정이라 지속 초 ≈ 실패 사이클 수다. 기동 후 1시간 미만이면 창은 그대로 3600초다(꺼져 있던 시간은 실패로 세지 않는다).
- `outages` = 메모리의 24시간 구간 전부(진행 중 포함), `startedAt` 내림차순. 시각은 전부 epoch ms.

### 3.6 FE — 폴링과 피드
- `features/health/` 안의 훅 하나가 `GET ${API_BASE}/health/collect` 를 **5초**마다 폴링한다(003 패턴: 즉시 1회·재진입 방지·실패 시 직전 유지). 폴링 상수는 셸 config 에 `HEALTH_POLL_MS = 5000` 으로 둔다.
- 공유 피드의 `health`/`healthEvents`(mock) 를 지우고 `health: HealthData | null`(마지막 응답, 첫 응답 전 null) 과 "적용" 동작 하나로 바꾼다. 셸이 훅을 spreads 폴링 옆에서 1회 호출한다.
- 표시명은 003 의 id→표시명 변환을 재사용한다 — 기능 간 import 금지(CLAUDE.md §2)이므로 그 변환은 `shared/format.ts` 로 옮기고 spreads·health 둘 다 거기서 가져온다(두 번째 사용처가 생기면 `shared` 로 승격). `/health/collect` 응답 타입(`HealthData`)도 셸 KPI·공유 피드가 알아야 하므로 003 의 `SpreadRow` 처럼 `shared/types.ts` 에 선언하고, `features/health/types.ts` 는 유형 라벨 같은 표시 전용 상수만 가진다. 경과 시간은 서버 시각 − `now` 를 매 렌더 계산한다(서버 값은 절대 시각이라 tick 이 키우지 않는다).

### 3.7 FE — KPI `수집 상태` 블록 (002 §3.5 개정)
`{3}곳 중 {ok 수}곳 정상` + 보조문: `down` 있으면 `{이름들} 끊김 · {stale 수}곳 지연`, 아니면 `{stale 수}곳 지연` 또는 `전체 정상`. `health` 가 null 이면 값 `–`, 보조문 `수집 상태 조회 전`.

### 3.8 FE — 수집 상태 탭 (002 §3.9 대체)
세로 카드 4개. `health` 가 null 이면 본문 가운데 `수집 상태 조회 전` 한 줄.
1. **요약**. 상태 원(down 있으면 빨강, stale 있으면 주황, 아니면 초록) + 문구 `정상` / `일부 지연 — N곳` / `장애 — {이름들} 끊김`. `총 수집 마켓` = `markets` 합. `최근 1시간 수집 성공률` = 최상위 `successRate1h`(`99` 초과 기본색, 아니면 주황). `HH:mm:ss 기준` = `fetchedAt`. 우측 흐리게 `HH:mm 서버 시작` = `serverStartedAt`.
2. **거래소 카드 3장(3열)**. 상단 상태색 테두리. 이름 + `● 수집 중` / `◌ 지연` / `✕ 끊김`. `마지막 수신` 경과 표기(ok 아니면 주황, 성공 0회면 `–`). `수집 마켓 N`. `성공률 1h`(`99` 이하 주황). `최근 에러` = `lastError` 의 `HH:mm:ss · {유형 라벨} · HTTP {statusCode}`(statusCode null 이면 생략), 없으면 `–`. 진행 중 구간이 있으면 빨강으로 `진행 중 · ×{count}회`.
3. **타임라인 `실패 구간 · 최근 24시간`**. 거래소 3트랙. 막대 = 구간(`startedAt`~`endedAt`, 진행 중이면 `now` 까지), 색은 `banned`·`rate_limit` 빨강, 그 외 주황. 1분 미만 구간도 최소 2px. 호버 `HH:mm – HH:mm · {유형 라벨} · HTTP {statusCode} · ×{count}회`. 축 5눈금은 002 §3.9 와 같다. 좌측 끝에 `serverStartedAt` 위치 세로 점선(24시간 안이면).
4. **로그 `최근 실패 구간`**. 열 `시각 거래소 유형 내용`, `startedAt` 내림차순 최대 50행. 유형 칩 라벨: `timeout` 타임아웃, `network` 연결 실패, `rate_limit` rate limit, `banned` 차단, `unavailable` 거래소 오류, `bad_request` 요청 오류, `bad_response` 응답 오류, `stale_stream` 스트림 정체. 칩 색은 `banned`·`rate_limit` 빨강, 그 외 주황. 라벨 표는 종류 union 을 전부 덮어야 한다(타입이 강제한다) — 종류가 늘면 라벨도 같은 PR 에서 는다. 내용 = `HTTP {statusCode} · {message 앞 120자}` + ` · ×{count}회 · {지속}`(지속 = `endedAt − startedAt` 경과 표기, 진행 중이면 `진행 중`). `retryAfterSec` 있으면 ` · Retry-After {n}s`. 맨 끝(가장 오래된 쪽)에 회색 칩 `서버 시작` 행 1개 = `serverStartedAt`, 내용 `이력 복원 후 수집 시작`. 구간이 없으면 `최근 24시간 실패 없음`.

## 4. 검증

- FE 유형 라벨 표가 실패 종류 8개를 전부 덮는다(`stale_stream` 포함) — 빠지면 타입 검사가 막는다
BE(네트워크 없음, 커넥터·Influx 는 fake):
- 업비트 429 → `rate_limit`, 418 → `banned`, 503 → `unavailable`, 400 → `bad_request`, 타임아웃 → `timeout`, 연결 예외 → `network`, JSON 아님 → `bad_response`. `status_code`·`body`·`url` 이 예외에 남는다.
- 빗썸 HTTP 200 + `{"error":{"name":429,…}}` → 실패이며 `rate_limit`, `status_code` 200. `error.name` 이 문자열이면 `bad_response`.
- 바이낸스 429 + `Retry-After: 10` → `rate_limit`, `retry_after_sec` 10. 403 → `banned`. 헤더 없으면 null.
- 첫 실패에 구간이 열리고 `count` 1, 연속 실패에 `count` 만 오르고 `message` 는 최신으로 바뀐다.
- 연속 성공 2회 뒤 실패면 같은 구간이 이어지고, 연속 성공 3회면 `ended_at` = 첫 성공 시각으로 닫힌다.
- `kind` 가 바뀌면 이전 구간이 닫히고 새 구간이 열린다.
- 24시간 지난 닫힌 구간은 목록에서 빠지고, 진행 중 구간은 남는다.
- 구간 열림·닫힘에 Influx 쓰기가 각 1회씩 호출되고, 연속 실패 중에는 호출되지 않는다. 쓰기 예외는 삼켜진다. 닫힘 쓰기에 `ended_ts`·`last_failed_ts`·최종 `count` 가 실린다.
- 기동 시 fake Influx 의 24시간 점이 메모리로 복원되고 `ended_ts` 없는 점은 진행 중이 된다. Influx 없음/예외/3초 초과면 빈 목록으로 기동한다.
- `GET /health/collect`: 거래소 3곳 고정 순서, `state` 경계(4.9초 ok · 5초 stale · 60초 down · 성공 0회 down), `successRate1h` 가 창과 겹친 초로 계산되고 창 밖 구간은 무시된다, `outages` 내림차순, 진행 중 구간이 `openOutage` 와 `outages` 양쪽에 있다.
- `GET /health` 는 여전히 `{"status":"ok","version":…}` 이다. `POST /refresh` 응답 키는 바뀌지 않는다.
- ruff·pytest 통과. web `npm run lint && npm run build` 통과.

수동(실서버):
- 서버 기동 → `curl -s localhost:8000/health/collect | head -c 400` 에 거래소 3곳·`state: ok`.
- 탭: 카드 3장 `● 수집 중`, 타임라인 빈 트랙, 로그에 `서버 시작` 1행. KPI `3곳 중 3곳 정상`.
- `/etc/hosts` 로 `api.bithumb.com` 을 막고 30초 → 빗썸 카드 `✕ 끊김` + `진행 중 · ×N회`, 로그에 `연결 실패` 1행(행 수가 늘지 않아야 한다), 타임라인 막대가 자란다. 복구 → 구간이 닫히고 지속 시간이 찍힌다. 서버 재기동 → 그 구간이 그대로 보인다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/pytest -q
# All checks passed! / 92 files left unchanged / 265 passed (2026-09-04, 기존 231 + 신규 34)
cd web && npm run lint && npm run build
# oxlint 경고 0 / tsc -b + vite build 성공
# 실서버(로컬, INFLUX_TOKEN 없음 — 이 머신은 :8000 을 소마 캘린더가 쓸 수 있어 :8020)
cd server && .venv/bin/uvicorn app.main:app --port 8020
curl -s localhost:8020/health            # {"status":"ok","version":"0.1.0"}
curl -s localhost:8020/health/collect | head -c 400
# exchanges 3곳 upbit·bithumb·binance 순, 기동 5초 뒤 전부 state:"ok", markets 198/292/293, outages []
curl -s -X POST localhost:8020/refresh | head -c 300   # 응답 키 불변(snapshots·usdkrw·…)
```
수동 항목 중 `/etc/hosts` 빗썸 차단·재기동 복원은 sudo 와 Influx 가 필요해 로컬 실행 세션에서 돌리지 않았다(사람 몫 — EC2 또는 dev compose).

## 6. 갱신할 문서
- `docs/context/status.md` — 행 추가 `| health | /health/collect·실패 구간 추적·collect_fail 쓰기/복원 | 실데이터 탭·5초 폴링·KPI 수집 상태 | 백오프는 013 |`. web-shell 행의 `mock 탭 4종` → `mock 탭 3종(gap·pp·flow)`. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 011 행 상태 → DONE. **항상 포함.**
- `docs/specs/002-web-shell.md` — §1 "mock 데이터로 도는 탭 4개(…수집 상태…)" 에서 수집 상태 제외, §2 기능 폴더 목록에서 `features/health` 를 "→ 011" 로, §3.5 KPI `수집 상태` 줄을 "011 §3.7" 로 교체, §3.9 본문을 "011 §3.8 로 대체" 한 줄로, §3.6 mock 공통의 Hyperliquid 현물 제외 사유("수집 상태 탭과 일치")를 지운다. §4 육안 체크 8번(수집 상태)을 011 §4 로 넘긴다.
- `docs/specs/001-collect.md` — §3.1 에러 형식에 `kind` 7종과 `retry_after_sec` 를 한 줄로 추가하고 "분류는 커넥터가 한다(011 §3.2)" 를 적는다. §3.5 빗썸 quirk 에 "HTTP 200 + `error` 본문은 실패" 를 추가한다. §3.2 사이클 5단계 뒤에 "거래소별 성공/실패를 이력 추적기에 넘긴다(011 §3.3)" 를 추가한다.
- `docs/context/architecture.md` — 데이터 흐름(BE) 그림에 `수집 사이클 → 실패 이력(메모리, 구간 단위) → Influx collect_fail(열림/닫힘 시)·기동 시 복원` 한 줄. "현재 구조" 절에 health 항목(이력 추적기 모듈은 core — 수집기가 쓰므로 기능 폴더가 아니다 / `features/health/` 는 읽기 API / web `features/health/`). `/health` 문장에 "상세는 `/health/collect`(011)" 를 덧붙인다.
- `docs/context/db.md` — measurement 절에 `collect_fail` 정의(§3.4 의 tag·field·유일키·시각 단위), 쓰는 쪽에 "이력 추적기: 구간 열림·닫힘 시 1점, 매초 없음", 읽는 쪽에 "기동 시 24시간 복원 1회 — HTTP 조회 없음".
- `docs/context/product.md` — 기능 목록 `(health) 수집 상태 탭` 행을 `health | 거래소별 실패 구간 이력·상태·성공률(/health/collect). 백오프는 비범위` 로. 용어 절에 `실패 구간(outage)`: "거래소 하나의 연속 실패를 시작·종료·횟수·원문으로 묶은 이력 단위. 연속 성공 3회에 닫힌다."
- `docs/context/dev-setup.md` — "검증용 스모크" 절에 `curl -s localhost:8000/health/collect | head -c 400` 과 기대값(거래소 3곳, `state`).

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server: `core/errors.py`(kind·retry_after_sec·FAIL_KINDS), `core/connectors/{upbit,bithumb,binance}.py`(분류·Retry-After·리스트 아님 → bad_response), `core/influx.py`(int/str 필드 line protocol, `CollectFailRow`·`collect_fail_point`·`query_collect_fail`), `core/outages.py`(추적기 신규), `core/collector.py`(사이클 → 추적기), `main.py`(복원 → 쓰기 태스크 → 수집 루프, `/health/collect` 라우터), `features/health/{models,service,router}.py` + `tests/test_collect_api.py`, `tests/test_outages.py`, 커넥터 테스트 3개에 분류 케이스 추가.
  - web: `shared/format.ts`(exName 승격), `shared/types.ts`(HealthData 계약, mock health 타입 삭제), `shared/feed.ts`(health null + setHealth), `shared/mock.ts`(buildHealth 삭제), `shared/config.ts`(HEALTH_POLL_MS), `features/health/{api,types,Tab}.tsx`, `features/spreads/api.ts`(exName import), `App.tsx`(KPI·폴링 호출).
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - §3.6 을 함께 고쳤다: exName 은 `shared/format.ts` 로, HealthData 는 `shared/types.ts` 로(기능 간 import 금지와 충돌 — 사람 합의).
  - Influx 쓰기는 사이클 안에서 동기 호출하지 않고 **큐 + 별도 태스크**가 순서대로 1점씩 쓴다. 이유: Influx 가 죽으면 쓰기 1회가 클라이언트 타임아웃(60초)까지 매달려 1초 사이클을 막는다. 순서를 지키는 이유는 열림 점이 닫힘 점 뒤에 도착하면 `count` 가 1 로 되돌아가기 때문.
  - 복원 3초 상한은 `asyncio.wait_for` 로 둔다 — 스레드의 실제 조회는 Influx 클라이언트 타임아웃까지 계속 돌 수 있지만 기동은 막지 않는다.
  - 복원 조회 range 는 `-24h`(started_at 기준). 24시간보다 전에 시작해 24시간 안에 끝난 구간은 복원되지 않는다(스펙 "최근 24시간" 문구 그대로).
  - 복원 시 같은 거래소에 진행 중 점이 둘 이상이면(닫힘 쓰기 유실) 최신만 진행 중으로 두고 나머지는 `ended_at = last_failed_at` 으로 닫는다. `count`·`last_failed_ts` 없는 반쪽 점은 건너뛴다.
  - 커넥터 밖의 예상 밖 예외(`internal_error`)는 `kind=bad_response`, `url=null`, `status_code=null` 로 구간에 넣는다. Influx 의 `url` 빈 문자열은 복원 시 null 로 돌린다(스펙은 0→null 만 말한다).
  - 문서에 없는 HTTP 상태(3xx 등)는 `bad_response`. `Retry-After` 는 세 커넥터 모두 파싱한다(스펙은 바이낸스만 명시). `resp.json()` 이 리스트가 아니면 세 커넥터 모두 `bad_response`(빗썸은 `error` 본문 판정 뒤).
  - 응답 키 `successRate1h`: 공용 `camelize_json` 이 `successRate1H` 를 만들어 health 모델은 pydantic alias 로 직접 camelCase 를 만든다.
  - FE: 로그 내용에서 `statusCode` null 이면 `HTTP …` 조각을 생략. 진행 중 구간의 타임라인 호버 종료 시각은 `now`. 유형 칩은 `Chip` 에 색만 덧입힌 outline 형태. `HealthTab` 은 `health` null 이면 본문 가운데 한 줄만.
  - 커밋 `feat(web): replace health mock …` 단독으로는 옛 Tab.tsx 가 tsc 에 걸린다(다음 커밋이 Tab 을 교체). 300줄 규칙 때문에 나눴다.
  - CLAUDE.md 인덱스 002 행의 "mock 탭(…수집상태…)" 도 함께 고쳤다(§6 목록엔 없지만 지금 동작과 달라서).
- 남은 빚:
  - `/etc/hosts` 차단·재기동 복원 수동 검증 미실행(로컬 Influx 없음). EC2 배포 후 확인 필요.
  - 백오프·Retry-After 존중·서킷은 013. 지금은 429 를 받아도 1초마다 재호출한다.
  - Influx 가 느릴 때 쓰기 큐가 무한히 쌓일 수 있다(구간 열림/닫힘 시에만 넣으므로 실제로는 몇 점 수준).
