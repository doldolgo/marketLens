# 011 — health

상태: DONE | 의존: 001(collect — 틱 판정·스트림 상태·에러 예외), 012(binance-stream — 샤드 단위 판정), 002(web-shell — 셸·공유 피드·수집 상태 mock 탭), 003(spreads — FE 폴링 패턴), 005(history — Influx 쓰기·읽기)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
수집 상태 탭이 mock 대신 **실제 수집기의 거래소별 실패 이력**을 보여준다. 거래소가 실패하면 "언제, 어느 거래소가, 어떤 종류로(rate limit·차단·타임아웃·거래소 오류…), 거래소가 뭐라고 했는지" 를 카드·24시간 타임라인·로그에서 본다. 이력은 서버를 껐다 켜도 복원된다.
백오프·재시도 정책 같은 **대응**은 이 스펙이 아니다(후속 013). 이 스펙은 관측과 기록까지다.

## 2. 범위
- 만드는 것: `GET /health/collect`(BE, `server/app/features/health/`), 수집 실패 이력 추적(core — 틱 루프가 쓴다), Influx `collect_fail` 쓰기·기동 시 복원, `web/src/features/health/` 실데이터 탭(api·types 추가).
- 하지 않는 것: `GET /health` 변경(001 계약 "항상 ok" 유지 — 007 배포 헬스체크·005/010 장애 격리 검증이 여기에 걸려 있다). 백오프·`Retry-After` 존중·서킷브레이커(013). 입출금 조회·Redis/Influx flusher·S3 원문 업로드의 상태 표시. `collect_fail` 을 읽는 HTTP 이력 조회 API(메모리가 진실, Influx 는 복원용). FE 테스트 러너.
- 바꾸는 기존 것:
  1. 001·012 수신 경로(스트림 커넥터·마켓 목록 REST) — 실패에 **종류(kind)** 와 거래소 원문을 싣는다(§3.2). 빗썸 REST 는 HTTP 200 + 에러 본문을 실패로 판정한다. 성공 경로·행 갱신·`/refresh` 응답 모양은 불변.
  2. 001 틱 루프 — 매 틱 거래소별 성공/실패 판정을 이력 추적기에 넘긴다(001 §3.8). 틱 순서·행 갱신 규칙 불변.
  3. 002 §3.9 수집 상태 mock 탭 → 이 스펙의 탭으로 교체. 002 §3.5 KPI `수집 상태` 블록의 출처를 mock 카드에서 `/health/collect` 로. 002 `shared/` 의 health mock 타입·생성기·피드 필드는 지운다.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001: 틱은 1초 주기, 거래소 3곳(`upbit`·`bithumb`·`binance`). 매 틱 거래소마다 판정한다 — 성공 = 스트림 연결 + 마지막 시세 메시지 30초 이내, 실패 = 미연결(그 연결 오류의 kind) 또는 30초 무수신(`stale_stream`). 실패에는 `kind`·`message`·`status_code`(핸드셰이크의 HTTP 상태, 없으면 null)·`url`(WebSocket URL)·`body`(핸드셰이크 거부 응답 본문 앞 500자, 없으면 null)·`retry_after_sec` 가 실린다. 스트림이 끊겨도 행은 그대로 남는다(행은 메시지로만 바뀐다). 실패 예외는 `ExchangeError` 공통 부모에 `exchange`·`url`·`message`·`status_code`·`body`(앞 500자) 를 가진다. 타임아웃은 `exchange_timeout`, 그 외는 `exchange_api_error`.
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

거래소별 HTTP 규칙 — REST(마켓 목록·입출금)와 WebSocket 핸드셰이크 거부 응답에 적용한다(공식 문서 2026-09-03 확인. 미확인 응답은 `bad_response` 로 두고 원문 body 를 남겨 분류표를 채운다). REST 쪽 분류는 예외(`ExchangeError.kind`)와 `/refresh` 응답의 `failures` 에만 쓰인다 — 이력(§3.3)에 들어가는 실패는 WebSocket 판정뿐이다:
- **업비트**: HTTP 429 → `rate_limit`("다음 초 경계까지 대기 후 재시도"). 418 → `banned`(429 누적 차단, 반복 시 차단 시간 누진). 5xx → `unavailable`(500 이 점검을 겸한다). 그 외 4xx → `bad_request`. 에러 본문은 `{"error":{"name":<int>,"message":…}}`.
- **빗썸**(v1 API): 문서상 에러는 HTTP 상태와 함께 `{"error":{"name":…,"message":…}}` 이지만 **실제로는 HTTP 200 에 이 본문을 준다**(`markets=KRW-XXXX` 실호출로 확인). 그래서 200 이어도 본문이 리스트가 아니고 `error` 키가 있으면 실패다. `error.name` 이 정수면 그 값을 HTTP 상태처럼 위 업비트 규칙으로 분류하고, 아니면 `bad_response`. 429/418 의 실제 응답은 문서에 없다(미확인). 이때 `status_code` 는 실제 HTTP 상태(200)다.
- **바이낸스**: 429 → `rate_limit`, 418 → `banned`(IP 밴, 2분~3일 누진), 둘 다 `Retry-After` 초를 `retry_after_sec` 에. 403 → `banned`(WAF — "rate limit violation or a security block"). 5xx → `unavailable`. 그 외 4xx → `bad_request`. 에러 본문 `{"code":-1003,"msg":…}`.
- 공통(REST): httpx 타임아웃 → `timeout`. 그 외 httpx 전송 예외(DNS·연결 거부) → `network`. JSON 파싱 실패·예상 밖 모양 → `bad_response`.
- **WebSocket 공통**: 연결 실패(DNS·거부·TLS) → `network`. 핸드셰이크 타임아웃(5초) → `timeout`. 핸드셰이크가 HTTP 상태로 거부되면 그 상태를 위 거래소 규칙으로 분류한다(업비트 초당 5회·빗썸 초당 10회 초과의 429 → `rate_limit`, 빗썸 10분 차단·바이낸스 418/403 → `banned`). 구독 요청에 에러 응답(`{"error":{"name":…}}`) → `bad_request`. 시세 프레임 디코드 실패는 그 프레임을 버릴 뿐이고, 유효한 시세 프레임이 30초 동안 없으면 `stale_stream`.
- **`stale_stream`** 은 HTTP 응답이 아니라 상시 연결이 조용히 멈춘 상태다 — 구독 중인데 30초 동안 시세 프레임이 없으면 그 틱의 그 거래소가 이 종류로 실패한다. `url` 은 WebSocket URL, `status_code` 는 null. 바이낸스는 샤드 단위로 판정하고 message 에 샤드 번호가 실린다(012 §3.5). 행은 그대로 남는다 — 001 은 행을 메시지로만 바꾼다.

### 3.3 실패 이력 — 구간(outage) 단위로만 기록
정상 틱은 기록하지 않는다. 기록 단위는 **거래소별 연속 실패 구간** 1건이다.
- 입력은 매 틱의 스트림 판정(§3.1)뿐이다. REST(마켓 목록·입출금) 실패는 이력에 넣지 않는다 — 시세는 WebSocket 으로만 오고 REST 는 목록·상태 보조라, 그 실패는 로그와 `/refresh` 응답의 `failures` 로만 드러난다.
- 구간 1건: `exchange`, `kind`, `started_at`(첫 실패 틱 시각, epoch ms), `ended_at`(null = 진행 중), `count`(실패 틱 수), `last_failed_at`(가장 최근 실패 틱 시각), `status_code`, `message`(핸드셰이크 거부 응답 `body` 가 있으면 그 body, 없으면 커넥터 message. 줄바꿈(`\r\n`·`\n`·`\r`)은 공백 하나로 바꾼 뒤 300자로 자른다 — Influx line protocol 은 필드 값의 개행을 받지 않고, 로그 행도 한 줄이다. WAF 차단 페이지 같은 HTML 본문이 전형이다), `url`, `retry_after_sec`. 유일키 = (`exchange`, `started_at`).
- 열기: 열린 구간이 없는 거래소가 실패하면 연다. 이미 열려 있으면 `count` 를 올리고 `last_failed_at`·`status_code`·`message`·`url`·`retry_after_sec` 는 **최신 실패로 덮어쓴다**.
- `kind` 가 바뀌면(예: `timeout` → `rate_limit`) 현재 구간을 그 시각에 닫고 새 구간을 연다. 원인 전환이 이력에 남아야 한다.
- 닫기: 그 거래소가 **연속 3틱 성공**하면 닫는다. `ended_at` 은 그 연속 성공의 **첫 성공 틱 시각**이다(잠깐 성공했다 바로 다시 실패하면 같은 구간이 이어진다 — 플래핑을 한 구간으로 본다).
- 거래소별로 마지막 성공 틱 시각도 기억한다(카드의 `마지막 수신`·상태 판정용).
- 보관: 메모리에 `ended_at` 이 24시간보다 오래된 구간은 버린다. 진행 중 구간은 길이와 무관하게 남는다.
- 틱 시각은 그 틱의 `ts` 를 ms 로 환산한 값이다.

### 3.4 Influx `collect_fail` — 쓰기와 복원
- measurement `collect_fail`. tag `exchange`·`kind`, time = `started_at`(초 정밀도), field `count`(int)·`last_failed_ts`(int epoch 초)·`status_code`(int, 없으면 0)·`message`(string)·`url`(string)·`retry_after_sec`(int, 없으면 0)·`ended_ts`(int epoch 초, **닫힐 때만** 쓴다). 한 구간 = 점 1개. 복원 시 0 은 null 로 돌린다.
- 구간이 **열릴 때** 1점 쓰고, **닫힐 때** 같은 (tag, time) 으로 다시 써서 `ended_ts`·최종 `count` 등을 합친다. 매초 쓰지 않는다. 진행 중 구간의 `count` 는 메모리에만 있다.
- 쓰기 실패는 로그 1줄 후 무시한다. 열 때 실패했어도 닫을 때의 쓰기가 점을 만든다.
- **기동 시 복원**: `started_at` 이 최근 24시간인 `collect_fail` 점을 읽어 메모리 목록을 채운다(조회 range 의 기준은 점의 time = `started_at` — 24시간보다 전에 시작해 24시간 안에 끝난 구간은 복원되지 않는다). `ended_ts` 없는 점은 진행 중 구간으로 복원한다 — 첫 틱에서 성공하면 위 닫기 규칙대로 닫히고, 실패하면 이어서 센다. 같은 거래소에 `ended_ts` 없는 점이 둘 이상이면(닫힘 쓰기 유실) `started_at` 이 가장 늦은 것만 진행 중으로 두고 나머지는 `ended_at = last_failed_at` 으로 닫으며, 그 닫힘 점을 Influx 에 쓴다(안 쓰면 재기동마다 다시 진행 중으로 복원된다). 복원된 진행 중 구간은 서버가 **꺼져 있던 시간을 포함해** 하나로 이어진다(`started_at` 부터 지금까지가 실패 구간이고 타임라인·성공률도 그렇게 본다) — 열 때 쓴 점에는 재기동 전 마지막 실패 시각이 없고(`last_failed_ts` 는 열 때·닫을 때만 쓴다) 꺼지기 전까지 실패 중이었으며 복구를 관측한 적도 없으므로 그 사이를 잘라내지 않는다. 복원된 진행 중 구간의 `count` 는 열 때 쓴 값에서 이어 센다(재기동 전 실패 횟수는 잃는다). Influx 가 없거나(`INFLUX_TOKEN` 미설정) 닿지 않거나 조회가 **3초**를 넘기면 빈 목록으로 시작하고 경고 로그 1줄. 복원은 틱 루프 시작 **전에** 끝난다.
- 서버 자체가 꺼져 있던 시간만으로는 구간을 **만들지** 않는다 — 기동 시 그 거래소에 복원된 진행 중 구간이 없으면 첫 실패 틱부터 센다. 지난 재기동 시각은 기록하지 않는다(현재 기동 시각만 응답에 싣는다).

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
     "lastError": {"at": 1756900123000, "kind": "timeout", "statusCode": null, "message": "업비트 WebSocket 실패: TimeoutError"}}
    /* at = 그 구간의 lastFailedAt */
  ],
  "outages": [
    {"exchange": "binance", "kind": "rate_limit", "startedAt": 1756903000000, "endedAt": 1756903012000, "lastFailedAt": 1756903011000, "count": 12,
     "statusCode": 429, "message": "{\"code\":-1003,\"msg\":\"Too much request weight used; ...\"}",
     "url": "wss://data-stream.binance.vision/stream", "retryAfterSec": 10}
    /* 핸드셰이크 429 거부 — message 는 그 거부 응답의 body */
  ]
}
```
- `exchanges` 는 001 의 거래소 3곳 고정 순서(`upbit`, `bithumb`, `binance`). `state`: 마지막 성공 후 경과 `< 5초` → `ok`, `5초 이상 60초 미만` → `stale`, `60초 이상` 또는 기동 후 성공 0회 → `down`. `markets` = 메모리 스냅샷의 그 거래소 행 수. `openOutage` = 진행 중 구간(모양은 `outages` 항목과 같음), 없으면 null. `lastError` = 가장 최근 구간의 최신 실패(진행 중이면 그것), 24시간 안에 없으면 null.
- `successRate1h` = `(1 − 최근 3600초 창과 겹치는 구간들의 겹친 초 합 / 3600) × 100`, 소수 1자리. 거래소별로 계산하고 최상위는 3곳 평균. 틱이 1초 고정이라 지속 초 ≈ 실패 틱 수다. 기동 후 1시간 미만이어도 창은 그대로 3600초다 — 꺼져 있던 시간은 복원된 진행 중 구간(§3.4)과 겹치는 만큼만 실패로 센다.
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
3. **타임라인 `실패 구간 · 최근 24시간`**. 거래소 3트랙. 막대 = 구간(`startedAt`~`endedAt`, 진행 중이면 `now` 까지), 색은 `banned`·`rate_limit` 빨강, 그 외 주황. 1분 미만 구간도 최소 2px. 호버 `HH:mm – HH:mm · {유형 라벨} · HTTP {statusCode} · ×{count}회`. 축 눈금 5개 — 24시간 창(`now − 24h` ~ `now`)을 5등분한 1/5·2/5·3/5·4/5 지점에 그 지점 시각의 `HH:00`(분은 버림), 우측 끝에 `지금`. 좌측 끝에 `serverStartedAt` 위치 세로 점선(24시간 안이면).
4. **로그 `최근 실패 구간`**. 열 `시각 거래소 유형 내용`, `startedAt` 내림차순 최대 50행. 유형 칩 라벨: `timeout` 타임아웃, `network` 연결 실패, `rate_limit` rate limit, `banned` 차단, `unavailable` 거래소 오류, `bad_request` 요청 오류, `bad_response` 응답 오류, `stale_stream` 스트림 정체. 칩 색은 `banned`·`rate_limit` 빨강, 그 외 주황. 내용 = `HTTP {statusCode} · {message 앞 120자}` + ` · ×{count}회 · {지속}`(지속 = `endedAt − startedAt` 경과 표기, 진행 중이면 `진행 중`). `retryAfterSec` 있으면 ` · Retry-After {n}s`. 맨 끝(가장 오래된 쪽)에 회색 칩 `서버 시작` 행 1개 = `serverStartedAt`, 내용 `이력 복원 후 수집 시작`. 구간이 없으면 `최근 24시간 실패 없음`.

## 4. 검증
BE(네트워크 없음, 커넥터·Influx 는 fake):
- 업비트 429 → `rate_limit`, 418 → `banned`, 503 → `unavailable`, 400 → `bad_request`, 타임아웃 → `timeout`, 연결 예외 → `network`, JSON 아님 → `bad_response`. `status_code`·`body`·`url` 이 예외에 남는다(연결 예외는 `status_code` null 에 `url` 은 REST URL). 빗썸·바이낸스 REST 의 연결 예외도 같다.
- 빗썸 HTTP 200 + `{"error":{"name":429,…}}` → 실패이며 `rate_limit`, `status_code` 200. `error.name` 이 문자열이면 `bad_response`.
- 바이낸스 429 + `Retry-After: 10` → `rate_limit`, `retry_after_sec` 10. 403 → `banned`. 헤더 없으면 null.
- 스트림 판정: 연결 + 최근 시세 메시지 → 성공. 핸드셰이크 429 → `rate_limit`, 연결 실패 → `network`, 30초 무수신 → `stale_stream`(url = WS URL, status_code null). 바이낸스 샤드 하나만 정체 → `stale_stream` 이고 message 에 샤드 번호.
- 핸드셰이크 거부 응답의 본문이 실패의 `body`(앞 500자)에 실린다(세 거래소 모두). 본문이 없으면 null.
- 추적기의 구간 `message` 는 `body` 가 있으면 body, 없으면 커넥터 message 다. 마켓 목록 REST 실패는 구간을 만들지 않는다(`/refresh` 의 `failures` 로만 보인다).
- 줄바꿈이 든 거부 본문(HTML 차단 페이지)으로 구간이 열리고 닫히면 `message` 의 줄바꿈은 공백 하나이고, 열림·닫힘 두 점의 line protocol 출력에 개행이 없다.
- 첫 실패에 구간이 열리고 `count` 1, 연속 실패에 `count` 만 오르고 `message` 는 최신으로 바뀐다.
- 연속 성공 2회 뒤 실패면 같은 구간이 이어지고, 연속 성공 3회면 `ended_at` = 첫 성공 시각으로 닫힌다.
- `kind` 가 바뀌면 이전 구간이 닫히고 새 구간이 열린다.
- 24시간 지난 닫힌 구간은 목록에서 빠지고, 진행 중 구간은 남는다.
- 구간 열림·닫힘에 Influx 쓰기가 각 1회씩 호출되고, 연속 실패 중에는 호출되지 않는다. 쓰기 예외는 삼켜진다. 닫힘 쓰기에 `ended_ts`·`last_failed_ts`·최종 `count` 가 실린다.
- 기동 시 fake Influx 의 24시간 점이 메모리로 복원되고 `ended_ts` 없는 점은 진행 중이 된다. Influx 없음/예외/3초 초과면 빈 목록으로 기동한다. 기동 전에 시작한 복원된 진행 중 구간은 `started_at` 부터 now 까지 성공률 창과 겹치고(꺼져 있던 시간 포함), 기동 후 닫히면 `endedAt` 은 기동 후의 첫 성공 틱이다. 같은 거래소에 진행 중 점이 둘이면 최신만 진행 중이고 옛 점은 `ended_at = last_failed_at` 으로 닫혀 그 닫힘 점(`ended_ts`)이 Influx 에 쓰인다.
- `GET /health/collect`: 거래소 3곳 고정 순서, `state` 경계(4.9초 ok · 5초 stale · 60초 down · 성공 0회 down), `successRate1h` 가 창과 겹친 초로 계산되고 창 밖 구간은 무시된다, `outages` 내림차순, 진행 중 구간이 `openOutage` 와 `outages` 양쪽에 있다.
- `GET /health` 는 여전히 `{"status":"ok","version":…}` 이다. `POST /refresh` 응답 키는 바뀌지 않는다.
- 수집 상태 탭의 유형 칩 라벨에 `stale_stream`(`스트림 정체`)이 있다 — FE `OutageKind` 유니온에 값이 있어야 빌드된다.
- ruff·pytest 통과. web `npm run lint && npm run build` 통과.

수동(실서버):
- 서버 기동 → `curl -s localhost:8000/health/collect | head -c 400` 에 거래소 3곳·`state: ok`.
- 탭: 카드 3장 `● 수집 중`, 타임라인 빈 트랙, 로그에 `서버 시작` 1행. KPI `3곳 중 3곳 정상`.
- `/etc/hosts` 로 `api.bithumb.com` 을 막고 30초 → 빗썸 카드 `✕ 끊김` + `진행 중 · ×N회`, 로그에 `연결 실패` 1행(행 수가 늘지 않아야 한다), 타임라인 막대가 자란다. 복구 → 구간이 닫히고 지속 시간이 찍힌다. 서버 재기동 → 그 구간이 그대로 보인다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q
# All checks passed! / 181 files left unchanged / 416 passed (2026-09-05 — 핸드셰이크 거부 body 전파 8건·추적기 body 우선 message 2건·REST 실패 무구간 1건·줄바꿈 본문 한 줄화 1건·복원 중복 진행 중 점 닫힘 쓰기 1건 포함)
cd web && npm run lint && npm run build
# oxlint 경고 0 / tsc -b + vite build 성공 (index-*.js 255 kB)
# 실서버(로컬, INFLUX_TOKEN·S3_BUCKET 없음, Redis 없음 — :8000 대신 빈 포트 :8041, 끝나고 kill)
cd server && .venv/bin/uvicorn app.main:app --port 8041
curl -s localhost:8041/health              # {"status":"ok","version":"0.1.0"}
curl -s localhost:8041/health/collect | head -c 900
# exchanges 3곳 upbit·bithumb·binance 순, 200, outages [] — 이 망은 거래소 도메인을 막아 마켓 목록을
# 못 받고 스트림이 연결되지 않으므로 세 곳 모두 state:"down"·markets 0·lastSuccessAt null (판정 보류 → 구간 없음)
curl -s -X POST localhost:8041/refresh | head -c 400   # 응답 키 불변(snapshots·usdkrw·totalSaved·failures·…)
```
EC2 에서 확인 필요(이 망에서 못 돌린 수동 항목): 기동 몇 초 뒤 `state:"ok"` 3곳과 `markets` > 0, 탭의 카드 3장 `● 수집 중`·로그 `서버 시작` 1행, `/etc/hosts` 로 `api.bithumb.com` 차단 30초 → 빗썸 `✕ 끊김` + `진행 중 · ×N회`(로그 행 수 불변·타임라인 막대 성장) → 복구 시 닫힘 → 재기동 후 Influx `collect_fail` 복원으로 같은 구간이 보이는지.

## 6. 갱신할 문서
- `docs/context/status.md` — 행 추가 `| health | /health/collect·실패 구간 추적·collect_fail 쓰기/복원 | 실데이터 탭·5초 폴링·KPI 수집 상태 | 백오프는 013 |`. web-shell 행의 `mock 탭 4종` → `mock 탭 3종(gap·pp·flow)`. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 011 행 상태 → DONE. **항상 포함.**
- `docs/specs/002-web-shell.md` — §1 "mock 데이터로 도는 탭 4개(…수집 상태…)" 에서 수집 상태 제외, §2 기능 폴더 목록에서 `features/health` 를 "→ 011" 로, §3.5 KPI `수집 상태` 줄을 "011 §3.7" 로 교체, §3.9 본문을 "011 §3.8 로 대체" 한 줄로, §3.6 mock 공통의 Hyperliquid 현물 제외 사유("수집 상태 탭과 일치")를 지운다. §4 육안 체크 8번(수집 상태)을 011 §4 로 넘긴다.
- `docs/specs/001-collect.md` — §3.1 에러 형식에 `kind` 8종과 `retry_after_sec` 를 한 줄로 추가하고 "분류는 커넥터가 한다(011 §3.2)" 를 적는다. §3.5 빗썸 quirk 에 "HTTP 200 + `error` 본문은 실패" 를 추가한다. §3.2 사이클 5단계 뒤에 "거래소별 성공/실패를 이력 추적기에 넘긴다(011 §3.3)" 를 추가한다.
- `docs/context/architecture.md` — 데이터 흐름(BE) 그림에 `수집 사이클 → 실패 이력(메모리, 구간 단위) → Influx collect_fail(열림/닫힘 시)·기동 시 복원` 한 줄. "현재 구조" 절에 health 항목(이력 추적기 모듈은 core — 수집기가 쓰므로 기능 폴더가 아니다 / `features/health/` 는 읽기 API / web `features/health/`). `/health` 문장에 "상세는 `/health/collect`(011)" 를 덧붙인다.
- `docs/context/db.md` — measurement 절에 `collect_fail` 정의(§3.4 의 tag·field·유일키·시각 단위), 쓰는 쪽에 "이력 추적기: 구간 열림·닫힘 시 1점, 매초 없음", 읽는 쪽에 "기동 시 24시간 복원 1회 — HTTP 조회 없음".
- `docs/context/product.md` — 기능 목록 `(health) 수집 상태 탭` 행을 `health | 거래소별 실패 구간 이력·상태·성공률(/health/collect). 백오프는 비범위` 로. 용어 절에 `실패 구간(outage)`: "거래소 하나의 연속 실패를 시작·종료·횟수·원문으로 묶은 이력 단위. 연속 성공 3회에 닫힌다."
- `docs/context/dev-setup.md` — "검증용 스모크" 절에 `curl -s localhost:8000/health/collect | head -c 400` 과 기대값(거래소 3곳, `state`).

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server: `core/errors.py`(`FAIL_KINDS` 8종·`kind`·`retry_after_sec`), `core/models.py`(`StreamError` — `body` 포함·`StreamState.url/connected_since`), `core/streams/{upbit,bithumb,binance}.py`(REST·핸드셰이크 분류·거부 응답 `body`·`Retry-After`·빗썸 200+`error` 판정·구독 거부 `bad_request`, 바이낸스는 샤드별 `stale_stream` 판정과 message 의 샤드 번호), `core/ticks.py`(`judge_state` + `TickLoop._judge_all` — 매 틱 거래소별 판정을 추적기에 넘긴다), `core/contracts.py`(`OutageSink`·`Verdict`·`StreamJudge`), `core/outages.py`(`OutageTracker` — 구간 열기·세기·닫기·kind 전환·`body` 우선 message·24시간 보관·`collect_fail` 쓰기 큐·기동 복원 3초 상한), `core/influx.py`(`CollectFailRow`·`collect_fail_point`·`query_collect_fail`), `main.py`(복원 → 쓰기 태스크 → 틱 루프 배선, `/health/collect` 라우터), `features/health/{models,service,router}.py` + `tests/test_collect_api.py`, `tests/test_outages.py`(추적기·쓰기·복원·틱→추적기), 커넥터 테스트 3개의 분류·`body` 케이스.
  - web: `shared/types.ts`(`HealthData` 계약, `OutageKind` 8종), `shared/format.ts`(`exName`), `shared/feed.ts`(`health` + `setHealth`), `shared/config.ts`(`HEALTH_POLL_MS`), `features/health/{api,types,Tab}.tsx`, `App.tsx`(KPI·폴링 호출).
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - 이력의 입력은 스트림 판정뿐이고 REST 실패는 넣지 않는다(§3.2·§3.3 에 확정): 선택지는 (a) 마켓 목록·입출금 REST 실패도 구간으로 기록, (b) 스트림 판정만 기록. 시세는 WebSocket 으로만 오고 REST 는 마켓 목록(매초)·입출금(60초) 보조라 (a) 는 1초 틱 단위 구간 모델(`count` ≈ 지속 초)과 맞지 않고 같은 거래소에 성격이 다른 실패가 한 구간에 섞인다 — (b) 를 택했다. 거래소 원문은 핸드셰이크 거부 응답의 `body` 로 싣는다.
  - 복원된 진행 중 구간과 꺼져 있던 시간(§3.4·§3.5 에 확정): 선택지는 (a) 성공률에서 꺼진 시간을 잘라내기, (b) 복원 시 `last_failed_ts` 로 닫고 기동 후 첫 실패에 새로 열기, (c) 꺼진 시간을 포함해 잇기. 열 때 쓴 점의 `last_failed_ts` 는 `started_at` 과 같아 (a)(b) 는 재기동 전 지속 시간을 통째로 잃고 타임라인과 성공률이 서로 다른 구간을 보게 된다 — (c) 를 택했다.
  - §3.6 의 결정 그대로: `exName` 은 `shared/format.ts`, `HealthData` 는 `shared/types.ts`(기능 간 import 금지).
  - Influx 쓰기는 틱 안에서 동기 호출하지 않고 **큐 + 별도 태스크**가 순서대로 1점씩 쓴다 — Influx 가 죽으면 쓰기 1회가 클라이언트 타임아웃까지 매달려 1초 틱을 막기 때문. 순서를 지키는 이유는 열림 점이 닫힘 점 뒤에 도착하면 `count` 가 1 로 되돌아가기 때문.
  - 복원 3초 상한은 `asyncio.wait_for` — 스레드의 실제 조회는 Influx 클라이언트 타임아웃까지 돌 수 있지만 기동은 막지 않는다. 복원 range 는 §3.4 대로 `started_at` 기준 `-24h` 다.
  - 복원 시 같은 거래소에 진행 중 점이 둘 이상이면(닫힘 쓰기 유실) 최신만 진행 중으로 두고 나머지는 `ended_at = last_failed_at` 으로 닫아 그 닫힘 점을 쓴다(§3.4 에 확정): 선택지는 (a) 메모리에서만 닫기, (b) 닫힘 점도 쓰기. (a) 는 Influx 에 `ended_ts` 없는 점이 남아 24시간 안의 재기동마다 다시 진행 중으로 복원된다 — (b) 를 택했다. Influx 의 `url` 빈 문자열은 복원 시 null 로 돌린다.
  - 구간 `message` 의 줄바꿈은 공백 하나로 바꾼다(§3.3 에 확정): 선택지는 (a) line protocol 직렬화에서 개행을 `\n` 두 글자로 이스케이프, (b) 추적기에서 한 줄로 정규화. Influx 는 문자열 필드의 `\n` 을 되돌리지 않아 (a) 는 복원된 message 가 메모리와 달라지고, 로그 행은 어차피 한 줄이다 — (b) 를 택했다. 개행이 그대로 가면 Influx 가 그 배치를 400 으로 거부해 열림·닫힘 점이 둘 다 사라진다.
  - 문서에 없는 HTTP 상태(3xx 등)는 `bad_response`. `Retry-After` 는 세 커넥터 모두 파싱한다(스펙은 바이낸스만 명시). 업비트·빗썸 핸드셰이크 403 은 `bad_request`(`banned` 로 보는 403 은 바이낸스 WAF 뿐 — 001 §3.8 과 일치).
  - 첫 연결 시도의 결과가 아직 없는 스트림은 판정하지 않는다(001 §3.8) — 이 망처럼 마켓 목록을 못 받아 구독이 없으면 구간이 생기지 않고 `state` 만 `down` 이다.
  - 응답 키 `successRate1h`: 공용 `camelize_json` 이 `successRate1H` 를 만들어 health 모델은 pydantic alias 로 직접 camelCase 를 만든다.
  - FE: `statusCode` null 이면 `HTTP …` 조각 생략. 진행 중 구간의 타임라인 종료 시각은 `now`. `stale_stream` 칩·막대 색은 "그 외" 규칙대로 주황.
  - FE 흐린 글자는 002 §3.2 대로 `--color-neutral-*` 램프만 쓴다(`shared/ui.tsx` 에 `color-mix` 글자색 조각을 두지 않는다): 선택지는 (a) 글자색을 램프로 바꾸기, (b) 002 §3.2 를 `color-mix` 허용으로 완화. `theme.css` 의 램프는 같은 명도 축에서 생성돼 다른 역할과 시각적 값이 맞고 참조 `HealthTab.tsx` 도 램프만 쓴다 — (a) 를 택했다. 대응은 참조 원본을 따른다: 카드 라벨·축 눈금·`기준` 문구 `-600`, 타임라인 거래소명 `-400`, 로그 시각·서버 시작 행·빈 문구 `-500`, 로그 내용 `-300`. 배경·구분선의 `color-mix`(타임라인 트랙 등)는 002 §3.2 의 글자색 예외 그대로 둔다.
- 남은 빚:
  - `/etc/hosts` 차단·재기동 복원 수동 검증 미실행(이 망은 거래소 도메인 차단, 로컬 Influx 없음) — §5 의 "EC2 에서 확인 필요".
  - 백오프·Retry-After 존중·서킷은 013. 지금은 429 를 받아도 재연결 백오프(1→30초)만 있고 `Retry-After` 값은 기록만 한다.
  - Influx 가 느릴 때 쓰기 큐가 무한히 쌓일 수 있다(구간 열림/닫힘 시에만 넣으므로 실제로는 몇 점 수준).
