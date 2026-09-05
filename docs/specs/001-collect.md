# 001 — collect

상태: DONE | 의존: 없음 (이 스펙이 계약을 **제공**한다: 009 틱 인계, 010 원문 싱크, 011 판정, 012 바이낸스 스트림)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
서버를 띄우면 업비트·빗썸의 호가·체결가가 **WebSocket 상시 연결로 메시지마다** 메모리에 갱신되고(바이낸스는 012 가 같은 방식으로), **매초 틱** 하나가 만들어져 저장 계층(009)으로 흘러간다. 거래소가 주는 모든 페이로드는 해석 전에 원문 싱크(010)에 넘어간다. `GET /health` 로 서버가 살아 있는지 확인한다.
REST 폴링은 마켓 목록 갱신에만 남는다 — 시세를 REST 로 묻지 않는다. 거래소 3곳 모두 시세 WebSocket 을 제공하고 SSE 는 제공하지 않으므로 선택지는 WebSocket 뿐이다.

## 2. 범위
- 만드는 것: `server/` 앱 골격(FastAPI, 에러 형식), `GET /health`, **LiveStore**(최신 시세·USDT 시세·스트림 상태·틱 슬롯·spark 자리), 업비트·빗썸 **스트림 커넥터** 2개(연결·구독·디코딩·핑·재연결), **마켓 우주** 갱신(REST), **틱 루프**(1초), 원문 싱크·틱 인계·판정의 **계약**(구현은 010·009·011) 과 그 호출, 즉시 갱신 트리거(`POST /refresh` 가 부른다 — 003).
- 하지 않는 것: `/health` 외 엔드포인트, Redis·Influx·S3 저장 자체(009·010), 입출금 조회(006 — 자리만), 바이낸스(012), Docker(007).
- 소비자가 지키는 계약(이 스펙이 토대다): ① 003 `POST /refresh` 는 §3.9 트리거를 부른다. ② 003·004 의 걷기는 행의 `asks`/`bids` 를 쓴다(행에 별도 깊이 필드는 없다). ③ 011 추적기는 §3.8 판정을 받는다. ④ Influx·S3 쓰기는 009(틱 인계 → flusher)·010(원문 싱크 → 업로드)만 한다 — 이 스펙에는 쓰기 루프가 없다. ⑤ 바이낸스 시세는 012 의 스트림 커넥터만 준다(REST 시세 호출·깊이 캐시는 없다; 012 전에는 바이낸스 행이 없다). ⑥ 006 조회기의 `refresh_if_due` 는 `force` 인자를 받는다 — §3.9 트리거가 60초 주기와 무관하게 조회시키는 길이다. ⑦ 003 `/spreads` 의 `age` 는 스트림 `last_message_at` 기준이다(003 §3.2-4 그대로).

## 3. 동작

### 3.1 서버 골격
- 앱 이름 `MarketLens Backend`, 버전 `0.1.0`. `/health` 응답과 User-Agent(`marketlens-server/<version>`)에 쓴다.
- REST(마켓 목록) 타임아웃 3.0초, 연결 1.5초. WebSocket 핸드셰이크 타임아웃 5초.
- 국내 호가는 누적 `price×size` 가 10억 KRW 에 도달한 단계까지 저장한다(§3.5). 바이낸스는 1,000,000 USDT(012).
- 국내 마켓 통화 `KRW`, 해외 `USDT`. 거래소 주소는 §3.10.
- 에러 형식은 항상 `{"error": {"code": str, "message": str, "detail": object}}`. 거래소 타임아웃 504 `exchange_timeout`, 그 외 거래소 실패 502 `exchange_api_error`. 실패 예외에는 `kind` 8종(`timeout` `network` `rate_limit` `banned` `unavailable` `bad_request` `bad_response` `stale_stream`)과 `retry_after_sec`(헤더 `Retry-After` 초 정수, 없으면 null)이 실리고 분류는 커넥터가 한다(011 §3.2). HTTP `detail` 에는 `exchange`·`url`, 비-200 이면 `statusCode` 와 본문 앞 500자.
- CORS·GZip·uvicorn 워커 1개(메모리가 진실 — 워커가 둘이면 진실도 둘).
- `GET /health` → `200 {"status": "ok", "version": "0.1.0"}`. 스트림 상태와 무관하게 항상 ok.

### 3.2 마켓 우주(universe)
- 기동 시 REST 로 목록을 받는다: 업비트·빗썸 `GET /v1/market/all` 에서 `KRW-` 로 시작하는 마켓, 바이낸스 USDT 현물 심볼 집합(012 §3.2 가 제공). **10분마다** 갱신하고 `/refresh` 트리거(§3.9)가 즉시 갱신한다.
- 바이낸스 심볼 집합은 core 계약 하나로 받는다 — `refresh(client) -> int`(REST 로 목록을 갱신하고 나간 호출 수, 실패는 거래소 예외)와 `bases() -> set[str]`(현재 아는 USDT 현물 base). 012 가 구현하고 `main.py` 가 꽂는다. 012 전에는 빈 집합을 주는 기본 구현이 꽂혀 우주가 비고 **행이 하나도 저장되지 않는다**(USDT 시세는 구독하므로 갱신된다).
- 우주 = `(업비트 KRW base ∪ 빗썸 KRW base) ∩ 바이낸스 USDT base`. `USDT` 자신은 우주에 없다(바이낸스에 USDT/USDT 가 없다) — 시세 원천으로만 쓴다.
- 구독 대상: 국내 거래소는 **자기 KRW 전 마켓**(`KRW-USDT` 포함). 바이낸스는 우주의 심볼(012). 목록이 바뀌면 그 차이만 추가 구독·해지한다.
- 저장 규칙: 우주 밖 base 의 행은 메모리에 넣지 않는다 — 국내 전용·해외 전용 코인은 메모리에 없다. 갱신으로 우주에서 빠진 base 는 그 시점에 메모리에서 지운다(상폐 소멸).
- 기동 시 목록을 못 받은 거래소는 **그 거래소만** 5초 간격으로 재시도하고 그동안 그 거래소의 구독은 없다(목록이 빈 스트림은 연결하지 않는다). 여기서 거래소는 업비트·빗썸·**바이낸스** 셋이다 — 바이낸스 심볼 집합의 `refresh` 가 한 번도 성공하지 못했으면 바이낸스가 재시도 대상이고, 국내 거래소만 재시도하는 동안에는 바이낸스 심볼 REST 를 부르지 않는다(10분 갱신·`/refresh` 트리거는 셋을 전부 부른다). 갱신 실패는 직전 목록 유지 + 경고 로그, 판정(§3.8)에는 영향 없다. 거래소 예외가 아닌 **예상 밖 예외**가 갱신 중에 나도 같다 — 경고 로그 후 다음 회차(5초 재시도 또는 10분)로 넘어가고 갱신 루프는 멈추지 않는다(틱 루프·스트림과 같은 규칙). 응답 본문은 원문 싱크에 기록한다(`rest:/v1/market/all`).

### 3.3 메모리 저장소 계약 (후속 스펙이 복사해 쓴다)
스냅샷 1행 = `(exchange, base)`:
- `exchange` — `upbit`·`bithumb`·`binance`. `base` — 코인. `quote` — `KRW`/`USDT`. `native_symbol` — 원본 심볼(`KRW-BTC`, `BTCUSDT`).
- `price` — 마지막 체결가. 없으면 `(bid+ask)/2`. `price_timestamp` — 거래소 체결 시각 epoch ms(체결가가 없으면 호가 메시지의 거래소 시각 ms — 빗썸은 µs 를 ms 로 바꾼 값).
- `asks` — `[price, size]` 오름차순, `bids` — 내림차순. 받은 단계 전부(업비트 최대 30·빗썸 최대 15·바이낸스 최대 20)를 누적액 상한까지 담는다. **호가를 걷는 계산은 이 두 목록을 그대로 쓴다** — 별도의 깊이 필드는 없다.
- `deposit_enabled`·`withdrawal_enabled`·`networks` — 006 이 60초마다 채운다. 행이 새 메시지로 교체될 때 이 3필드는 직전 행 값을 물려받는다. 없으면 `null`·빈 목록.
- `updated_at` — 이 행이 마지막으로 갱신된 시각(tz-aware UTC).

거래소별 **스트림 상태**(011·003 이 읽는다): `connected`(bool), `last_message_at`(마지막 **시세** 메시지 수신 epoch ms, 없으면 null), `last_error`(`{kind, message, status_code, url, retry_after_sec}` 또는 null), `subscribed`(구독 심볼 수). 판정용으로 `url`(WebSocket 주소)과 `connected_since`(이번 연결의 구독 시각, 미연결이면 null)도 함께 든다. 바이낸스는 012 가 샤드 3개를 합쳐 같은 모양으로 낸다. 등록되지 않은 거래소의 상태는 없다(null) — 만들지 않는다.
USDT 시세 = 국내 거래소 id 당 `{exchange, ask, bid, updated_at}`. 바이낸스 시세는 없다.
`received_at` = 마지막 틱 시각(epoch 초). **틱 슬롯** = 가장 최근 틱 1개(§3.6). spark 맵 = 009 가 게시하는 `(dom, fx, base) → number[]`.
조회: 전체 목록(거래소·base 필터, base 대소문자 무시), 단건(없으면 `None`), 거래소별 시세·전체 시세 사본, 스트림 상태, `received_at`, 틱 슬롯, spark, 비었는지 여부. 조회는 전부 동기(await 없음)다. 쓰기는 행 1개 단위(`put_row`)와 행 삭제·우주 밖 행 일괄 삭제뿐이다 — 거래소 단위 통째 교체 함수는 없다.

### 3.4 USDT 시세
국내 거래소의 `KRW-USDT` **호가 메시지가 올 때마다** §3.5-1 의 잔량 필터를 거친 뒤 `asks[0].price → ask`, `bids[0].price → bid` 로 그 거래소 시세를 갱신한다(`updated_at` = 수신 시각). 한쪽이 비거나 ≤0 이면 갱신하지 않는다. 추가 호출 없음. 관측이 없으면 직전 값이 남는다(경고는 008).

### 3.5 행 갱신 규칙 (메시지 → 행)
1. **호가 메시지**: 그 `(exchange, base)` 행의 `asks`·`bids` 를 통째 교체하고 `updated_at` 을 수신 시각으로. 받은 순서대로 `[price, size]` 를 담되 **잔량 ≤ 0 단계는 버리고**, 누적 `price×size` 가 상한에 도달한 단계까지 포함하고 자른다. 어느 한쪽이 비면 그 행은 저장하지 않는다(있던 행은 지운다).
2. **체결가 메시지**: `price`·`price_timestamp` 갱신. 행이 아직 없으면(호가 스냅샷 전) 값만 보류했다가 호가가 오면 함께 싣는다. 마지막 체결가는 행이 교체돼도 이어진다. 체결가가 없거나 ≤0 이면 mid 와 호가 메시지의 거래소 시각.
3. 우주 밖 base 는 버린다(§3.2). 입출금 3필드는 물려받는다(§3.3).
4. 갱신은 메시지 단위·행 단위다 — 거래소 단위 통째 교체는 없다.

### 3.6 틱 루프 (1초)
앱 시작과 함께 돌고 종료 시 취소된다. 매초 경계에 순서대로:
1. 006 조회기가 있으면 `refresh_if_due` (60초에 한 번 실호출, 시세 갱신을 막지 않게 별도 태스크 — 직전 태스크가 끝나지 않았으면 이번 초는 건너뛴다). 조회기의 캐시는 **매 틱** 세 거래소의 행에 반영한다 — 메시지로 새로 생긴 행도 1초 안에 3필드를 갖는다.
2. **틱 생성**(동기, `await` 없음 — 한 틱 안에서 교체 전후 호가가 섞이지 않는 근거): `ts` = 이 초(epoch 초), `rows` = 전 조합 중 자격 통과분의 `{dom, fx, base, fwd, rev}`(원값 — 자격·수식은 003 §3.2-4 의 raw 규칙: 국내×해외 다른 거래소, 양쪽 호가 존재, 그 국내 거래소 자신의 USDT 시세, 여섯 값 > 0), `dwFailed` = 조회기의 실패 상태 거래소 목록.
3. 틱 슬롯에 새 틱을 넣고 **직전 틱**을 009 의 인계 함수(`handoff(tick)`, 동기·무예외)에 넘긴다. 슬롯이 비어 있었으면(첫 틱) 인계 없음.
4. `received_at` = `ts`.
5. 스트림이 등록된 거래소를 판정해(§3.8) 011 추적기에 성공/실패로 넘긴다 — 012 전의 바이낸스처럼 스트림이 없는 거래소는 판정하지 않는다.
앱 종료 시 슬롯의 틱도 인계한다. 틱 루프는 예외를 밖으로 던지지 않는다(버그 하나로 멈추지 않게 로그 후 다음 초).
앱 시작 순서: 011 이력 복원 → 009 spark 복원 → 마켓 우주 → 스트림 기동 → 틱 루프. 어느 것이 실패해도 앱은 뜬다.

### 3.7 원문 싱크 계약 (010 이 구현)
core 공개 함수 `record(exchange: str, source: str, received_at_ms: int, payload: str) -> None` — **동기, 예외 없음, 즉시 반환**. 커넥터는 **받은 모든 프레임**(시세·`{"status":"UP"}`·구독 응답·에러 응답 포함)과 **모든 REST 응답 본문**을 해석하기 **전에** 이 함수에 넘긴다. 바이너리 프레임은 UTF-8 디코드한 문자열, 압축 프레임은 라이브러리가 푼 문자열이 원문이다. `source` = `"ws:<경로>"` 또는 `"rest:<경로>"`(예 `ws:/websocket/v1`, `rest:/v1/market/all`). 010 이 없으면(`S3_BUCKET` 미설정) 아무것도 하지 않는 구현이 꽂힌다.

### 3.8 판정 (매 틱, 011 이 기록)
거래소마다: **성공** = 스트림이 연결돼 있고 30초 안에 시세를 받았다. 무수신 30초는 **이번 연결의 구독 시각과 마지막 시세 수신 시각 중 최신**부터 센다 — 연결 뒤 아직 시세가 없으면 구독 시각이 기준이고, 오래 끊겼다가 재연결한 직후 첫 프레임 전에도 구독 시각이 기준이라 직전 연결의 수신 시각 때문에 정체로 판정되지 않는다(스냅샷 한 바퀴가 오기까지 1~2초를 실패 구간으로 남기지 않는다). **실패**는 다음 순서로 종류를 정한다 — 미연결이면 `last_error.kind`(핸드셰이크·연결 실패의 분류: DNS·거부·TLS·연결 끊김 `network`, 핸드셰이크 타임아웃 `timeout`, 핸드셰이크가 HTTP 상태로 거부되면 011 §3.2 의 **그 거래소 REST 규칙** — 업비트·빗썸은 429 `rate_limit`·418 `banned`·5xx `unavailable`·그 외 4xx `bad_request`(403 도 `bad_request` — `banned` 로 보는 403 은 바이낸스 WAF 규칙뿐), 그 밖의 상태 `bad_response`; 구독 에러 응답 `bad_request`; 응답 없는 그 외 예외 `bad_response`); 연결됐는데 30초 무수신이면 `stale_stream`. 첫 연결 시도의 결과가 아직 없으면(미연결·오류 없음·수신 없음) 그 틱은 판정하지 않는다 — 기동 직후 1~2초가 실패 구간으로 남지 않게. 시세를 받았던 스트림이 오류 기록 없이 닫혀 있으면 `network`. 실패에는 `message`·`status_code`(없으면 null)·`url`(WebSocket URL)·`retry_after_sec` 가 실린다. 디코드 실패 프레임은 버리고 셀 뿐 그 자체로 실패가 아니다(무효 프레임만 30초 이어지면 `stale_stream`). 바이낸스는 012 §3.6(샤드 단위).

### 3.9 즉시 갱신 트리거 (`POST /refresh` 가 부른다 — 003)
순서: 마켓 우주 즉시 갱신(REST) → 변경분 재구독 → 006 조회 즉시 실행(`refresh_if_due(client, force=True)` — 60초 주기 무시) → 요약 반환. 요약 = 거래소별 `{saved: 현재 메모리 행 수, calls: 이 트리거로 나간 REST 호출 수}`, 시세가 있는 국내 거래소 목록, `failures[{exchange, error_code, message}]`(트리거 중 REST 실패는 예외의 code — `exchange_timeout`·`exchange_api_error`, 지금 실패 판정인 스트림은 그 실패의 `kind`), `warnings[]`(006 경고 + USDT 시세 없는 국내 거래소 경고 `"KRW-USDT 호가가 없어 USDT 시세를 못 구한 거래소: upbit (해당 국내 거래소의 김프 계산은 빠진다)."`), `duration_ms`, `fetched_at`(epoch ms). 동시 호출은 직렬화한다. 틱 루프와는 독립이다.

### 3.10 외부 의존 (public WebSocket·REST, 인증 없음)
**업비트** WS `wss://api.upbit.com/websocket/v1`, REST `https://api.upbit.com`
- 구독 요청(연결 직후 1회, 텍스트 프레임): `[{"ticket":"<uuid>"},{"type":"orderbook","codes":["KRW-BTC",…]},{"type":"ticker","codes":["KRW-BTC",…]},{"format":"DEFAULT"}]`. `codes` 는 대문자. 스냅샷 + 실시간을 모두 받는다(`is_only_*` 를 주지 않는다) — 재연결 직후 스냅샷 한 바퀴로 전 마켓이 복구된다. 호가 단계는 `codes` 에 `.unit` 을 붙여 정하며(1·5·15·30) 안 붙이면 **30단계**다. `level`(모아보기)은 주지 않는다.
- 호가 메시지: `{"type":"orderbook","code":"KRW-BTC","timestamp":1787727947526,"total_ask_size":…,"total_bid_size":…,"orderbook_units":[{"ask_price":109950000.0,"bid_price":109880000.0,"ask_size":0.0034,"bid_size":0.188},…],"stream_type":"SNAPSHOT|REALTIME","level":0}` — `orderbook_units` 는 같은 단계의 ask/bid 가 한 쌍이고 정렬돼 온다. `timestamp` 는 ms.
- 체결가 메시지: `{"type":"ticker","code":"KRW-BTC","trade_price":109868000.0,"trade_timestamp":1787729040682,"timestamp":1787729042606,"stream_type":"REALTIME",…}` — `trade_price`·`trade_timestamp`(ms) 만 쓴다.
- 프레임은 바이너리(UTF-8 JSON)로 올 수 있다 — bytes 면 디코드한다. 압축(permessage-deflate)은 라이브러리 옵션이며 켜도 원문은 같다.
- 연결 유지: 서버는 **120초** 무송수신이면 끊는다. **30초마다 PING 프레임**을 보낸다(WebSocket 라이브러리의 keepalive 옵션 — 20초 안에 PONG 이 없으면 끊고 재연결). 텍스트 `PING` 을 보내면 `{"status":"UP"}` 이 10초 간격으로 오는데, 이 프레임은 시세 수신으로 세지 않는다.
- 한도: `websocket-connect` IP 당 초당 5회, `websocket-message` 커넥션당 초당 5회·분당 100회. 마켓 목록 REST 는 10분에 1회.
- 에러 응답 `{"error":{"name":"WRONG_FORMAT|NO_TICKET|NO_TYPE|NO_CODES|INVALID_PARAM|INVALID_AUTH","message":…}}` → `bad_request` 로 실패, 재연결 백오프.
- 마켓 목록 `GET /v1/market/all` → `[{"market":"KRW-BTC",…}]`.

**빗썸** WS `wss://ws-api.bithumb.com/websocket/v1`, REST `https://api.bithumb.com`
- 요청·응답 형식이 업비트 v1 과 같다(`ticket`·`type`·`codes`·`format`, 메시지 필드명 동일). **커넥터 코드는 공유하지 않는다.**
- 호가는 응답 자체가 **최대 15단계**. 호가 메시지의 `timestamp` 는 **microseconds** 다 → ms 로 바꿔 쓴다. 체결가 메시지의 `trade_timestamp`·`timestamp` 는 ms.
- **잔량 0 인 유령 호가**가 드물게 섞인다 → §3.5-1 의 필터가 걸러 최우선 호가도 잔량>0 인 단계가 된다.
- `trade_timestamp` 가 현재보다 1시간 이상 미래면 9시간(32,400,000ms)을 뺀다(KST 벽시계 버그 방어), 아니면 그대로.
- 연결 유지: 업비트와 같다(120초 idle, 30초마다 PING, `{"status":"UP"}`). permessage-deflate 를 지원한다(선택).
- 한도: 연결 요청 IP 당 **초당 10회**, 넘으면 429, 지속 시 **10분 차단**. 재연결 백오프(§3.11)가 이 안에 있다.
- 마켓 목록 `GET /v1/market/all` (KRW ~480개). REST 는 HTTP 200 + `{"error":…}` 본문이 실패다(011 §3.2).

**바이낸스** → 012.

### 3.11 스트림 커넥터 공통 규칙 (두 국내 커넥터가 각자 구현한다)
- 연결 → 구독 메시지 1회 → 메시지 펌프. 메시지마다: 원문 싱크 기록(§3.7) → 디코드 → 행 갱신(§3.5)·USDT 시세(§3.4) → `last_message_at` 갱신(시세 메시지만).
- 끊기면 지수 백오프 1·2·4·…초(상한 30초)로 재연결하고 **구독 뒤 첫 시세 프레임을 받으면 1초로 되돌린다**(구독 메시지를 보낸 것만으로는 성공이 아니다 — 에러 응답이 뒤따를 수 있다). 연결 실패 1회 = 경고 로그 1줄 + `last_error`. 구독할 마켓 목록이 비어 있으면 연결하지 않고 1초마다 목록을 다시 본다.
- 마켓 목록이 바뀌면 그 차이만 추가 구독/해지한다 — 업비트·빗썸은 구독 메시지를 다시 보내면 전체가 교체되므로 연결 중이면 그 자리에서 **전 목록으로 재구독**한다(메시지 한도 안). 같은 목록이면 보내지 않는다.
- 종료: 태스크 취소 후 소켓 close, 합계 2초 상한.

## 4. 검증
네트워크 없음 — 가짜 소켓(메시지를 주입하는 async 제너레이터)과 가짜 REST 로 검증한다.
- `GET /health` 가 200 과 `{"status":"ok","version":…}`. 존재하지 않는 경로는 404.
- 업비트 호가 메시지 1건 → 그 행의 `asks/bids` 가 `[price,size]` 목록으로 교체되고 `updated_at` 이 수신 시각. 30단계가 전부 들어온다(상한 전이면).
- 업비트 체결가 메시지 → `price`·`price_timestamp` 갱신, 호가는 그대로. 호가 전에 온 체결가는 보류됐다가 호가와 함께 실린다.
- 빗썸 잔량 0 단계는 빠지고 최우선 호가도 잔량>0 인 단계. 빗썸 호가 `timestamp`(µs) 가 ms 로 바뀐다. `trade_timestamp` 가 1시간 이상 미래면 9시간을 뺀다.
- 우주 밖 base 의 메시지는 저장되지 않는다. 우주에서 base 가 빠지면 그 행이 사라진다. `USDT` 행은 없다.
- `KRW-USDT` 호가 메시지마다 시세 `ask=asks[0].price`·`bid=bids[0].price` 갱신, 한쪽이 비면 직전 시세 유지.
- 국내 호가는 누적 `price×size` 상한에 도달한 단계까지만 저장된다(`inf` 면 전부).
- 행 교체 시 입출금 3필드가 유지된다.
- 매 초 틱이 생기고 `rows` 가 자격 규칙(다른 거래소·양쪽 호가·자기 시세·여섯 값 > 0)과 원값 수식을 따른다. 두 번째 틱에서 첫 틱이 인계되고, 종료 시 마지막 틱이 인계된다.
- `received_at` 이 매 틱 갱신된다. 틱 생성 중 `await` 가 없다(가짜 스토어로 재진입이 없음을 확인 — 틱이 저장소를 읽고 쓰는 사이에 다른 태스크가 한 번도 돌지 않는다).
- 006 조회 태스크가 끝나지 않은 채 다음 초가 오면 그 초는 새 태스크를 만들지 않는다(직전 태스크 1개만).
- 판정: 연결 + 30초 이내 메시지 → 성공. 연결 실패(`network`)·핸드셰이크 429(`rate_limit`)·403/400(`bad_request`)·30초 무수신(`stale_stream`, url = WS URL)이 각각 실패로 기록되고, 정체됐던 실제 스트림에 시세 프레임이 다시 오면 성공으로 돌아온다. 60초 끊겼다가 재연결한 직후 첫 프레임 전의 판정은 성공이다(직전 연결의 수신 시각이 아니라 이번 구독 시각부터 센다). `{"status":"UP"}`·구독 응답은 수신으로 세지 않는다.
- 기동 시 바이낸스 심볼 목록을 못 받으면 바이낸스만 5초 간격으로 재시도하고, 국내 거래소만 재시도하는 동안 바이낸스 심볼 REST 는 호출되지 않는다.
- 마켓 목록 갱신이 거래소 예외가 아닌 예외로 끝나도 갱신 루프는 다음 회차(5초 재시도·10분)를 계속 돈다.
- 재연결 백오프가 1·2·4…30 으로 자라고 **구독 뒤 첫 시세 프레임**을 받은 뒤에만 1로 돌아온다 — 연결 실패 2회 뒤 구독이 거부되면 세 번째 대기는 1 이 아니라 4 다(두 커넥터 모두). 구독 에러 응답은 `bad_request`.
- 종료: 소켓 `close()` 가 돌아오지 않아도 `aclose` 는 2초 상한 안에 끝난다.
- 모든 프레임(시세·UP·에러)과 마켓 목록 응답 본문이 원문 싱크(fake)에 `exchange`·`source`·수신 시각과 함께 원문 그대로 기록된다 — 해석보다 먼저.
- 트리거(§3.9): 우주 갱신 REST 가 호출되고 `calls` 에 반영, `saved` 가 현재 행 수, 실패 중인 스트림이 `failures` 에 담긴다. 동시 호출은 직렬화된다.
- 거래소 타임아웃은 504 `exchange_timeout`, 비-200 은 502 `exchange_api_error`(HTTP `detail` 에 `statusCode`·`body`).
- 테스트 전부 통과, ruff lint·format 위반 0.
- 선택(실 네트워크, 실패해도 완료를 막지 않음 — §7 에 기록): EC2 에서 기동 → 10초 안에 업비트·빗썸 행 각 100 이상, USDT 시세 둘 다, 1분 동안 재연결 0회, 초당 메시지 수와 원문 바이트 수를 §5 에 적는다(010 의 용량 추정 갱신).

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q
# All checks passed! / 170 files left unchanged / 295 passed, 1 warning in 1.51s  (2026-09-05)
# 001 몫: tests/test_store.py test_quotes.py test_stream_upbit.py test_stream_bithumb.py test_ticks.py test_universe.py test_collect_trigger.py test_health.py test_rows.py (§4 항목당 1개 이상)

cd server && .venv/bin/python -m uvicorn app.main:app --port 8041   # 로컬 스모크 (8000 은 다른 프로세스가 점유할 수 있어 빈 포트)
curl -s localhost:8041/health          # {"status":"ok","version":"0.1.0"}
curl -s localhost:8041/no-such         # {"error":{"code":"not_found","message":"Not Found","detail":null}}
curl -s localhost:8041/spreads         # 404 market_data_not_found — 이 망은 거래소 REST·WS 가 막혀 마켓 목록을 못 받는다(로그: ConnectTimeout, 5초 재시도)
curl -s localhost:8041/health/collect  # 거래소 3곳, 기동 직후 state "down"(판정 전) — Influx 없음 경고 후 앱은 뜬다. 종료 로그 깨끗함(태스크 취소·소켓 close 2초 상한)
```
- 선택 항목(EC2 실 네트워크: 기동 10초 안 업비트·빗썸 행 각 100 이상, USDT 시세 둘 다, 1분 재연결 0회, 초당 메시지·원문 바이트) — **EC2 에서 확인 필요**. 이 Mac 망은 거래소 도메인을 막는다(dev-setup.md 로컬 메모). 012 전에는 바이낸스 심볼이 없어 우주가 비므로 "행 100 이상" 은 012 이후에만 성립한다 — USDT 시세·재연결 0회·메시지 수만 먼저 볼 수 있다.

## 6. 갱신할 문서
- `docs/context/status.md` — collect 행을 `| collect | 업비트·빗썸 WS 실시간 갱신·마켓 우주 10분·1초 틱·/health | - | 바이낸스는 012 |` 로. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 001 행 상태 → DONE. **항상 포함.**
- `docs/context/architecture.md` — "현재 구조" 절의 collect 항목을 실제 모듈로(스트림 커넥터 2개·LiveStore·틱 루프·우주 갱신·계약 Protocol 들).
- `docs/context/dev-setup.md` — 스모크의 `/spreads` 확인 시점을 "기동 10초 뒤" 로, 로컬 망에서 WS 도메인 차단 시 EC2 확인 메모.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `server/app/core/models.py` — `Row`(호가는 `asks`/`bids` 뿐)·`Rate`·`StreamError`·`StreamState`·`Tick`·`TickRow`. `core/live_store.py` — 행 단위 쓰기(`put_row`·`put_rows`·`remove_row`·`retain_bases`)·입출금 3필드 물려받기·스트림 상태(`stream`/`stream_state`/`streams`)·틱 슬롯(`push_tick`/`tick`)·spark 맵. `core/rows.py` — `clean_levels`(잔량 필터 + 누적 상한).
  - `core/quotes.py` — `QuoteSink`: 메시지 → 행 규칙(§3.4·§3.5) 전부. 우주 필터·체결가 보류·USDT 시세·빈 호가 삭제.
  - `core/contracts.py` — 원문 싱크 `RawRecorder`/`noop_record`, 틱 인계 `TickHandoff`/`noop_handoff`, `OutageSink`, `StreamJudge`/`Verdict`, `WalletStatusProvider`, `ForeignSymbolSource`/`NoForeignSymbols`.
  - `core/streams/upbit.py`·`core/streams/bithumb.py` — 스트림 커넥터 2개(연결·구독·펌프·분류·백오프·재구독·`fetch_markets`·`judge`). 코드 공유 없음. `core/universe.py` — `UniverseRefresher`(10분·못 받은 거래소만 5초 재시도(바이낸스 심볼 포함)·교집합·구독 목록 배포). `core/ticks.py` — `judge_state`·`build_tick`·`TickLoop`. `core/collect.py` — `CollectService.refresh_now`·`RefreshSummary`. `core/config.py` — `EXCHANGES`·`DOMESTIC_EXCHANGES`·`WS_OPEN_TIMEOUT`.
  - `app/main.py` — lifespan 재배선(이력 복원 → 우주 → 스트림 → 틱 루프, 종료 시 마지막 틱 인계). `app.state.collector` = `CollectService`.
  - 소비자: `core/orderbook.py` `walk_levels` 가 행의 `asks`/`bids` 만 본다. `features/spreads/`(router `refresh_now`, service `age` 스트림 기준·`RefreshSummary`, 테스트 시드 `put_rows`), `features/analysis/tests/`(시드·20단계 테스트 추가), `features/health/tests/`, `features/wallet_status/service.py`(`force`), `tests/test_outages.py`(틱 판정 배선), `tests/test_wallet_integration.py`(틱 루프 기반).
  - 테스트: `tests/conftest.py`(`make_row`·`FakeStream`·`RawLog`), `tests/stream_fakes.py`, `tests/test_store.py` `test_quotes.py` `test_stream_upbit.py` `test_stream_bithumb.py` `test_ticks.py` `test_universe.py` `test_collect_trigger.py`.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - §3.2 바이낸스 심볼 집합의 core 계약(`refresh`/`bases`)과 012 전 기본 구현(빈 집합 → 우주 비어 행 없음). 기동 재시도는 못 받은 거래소만 — 바이낸스 심볼 집합도 같은 규칙(한 번도 성공 못 했으면 재시도 대상, 국내만 재시도할 땐 호출 없음).
  - §3.3 체결가 없을 때 `price_timestamp` = **호가 메시지의 거래소 시각(ms)** — 빗썸 µs→ms 규칙이 관측 가능한 유일한 자리다. `StreamState` 에 판정용 `url`·`connected_since`, `last_error` 에 `retry_after_sec`. 저장소 쓰기 면(`put_row` 등) 명시.
  - §3.4 USDT 시세도 잔량 필터를 거친 최우선 호가. §3.5-2 마지막 체결가는 행 교체 후에도 이어진다.
  - §3.6 입출금 캐시를 매 틱 행에 반영(60초 apply 만으로는 새 행이 1분간 null). 스트림 없는 거래소(012 전 바이낸스)는 판정하지 않는다.
  - §3.8 첫 연결 결과 전 판정 보류, 무수신 기준 = 이번 연결의 구독 시각과 마지막 수신 시각 중 최신(재연결 직후 첫 프레임 전은 구독 시각), 오류 없이 닫힌 스트림 = `network`, 연결 끊김(`ConnectionClosed`) = `network`. 핸드셰이크 HTTP 거부의 분류 주인은 011 §3.2 — 업비트·빗썸 4xx 는 429·418 외 전부 `bad_request`(403 포함), 커넥터의 REST 분류 함수를 핸드셰이크에도 쓴다(같은 파일 안 — 커넥터 간 공유 아님).
  - §3.9 006 즉시 조회 = `refresh_if_due(force=True)`(§2 ⑥). 스트림 실패의 `error_code` = `kind`.
  - §3.11 백오프 리셋 시점 = 구독 뒤 첫 시세 프레임. PING 은 라이브러리 keepalive(`ping_interval=30`)로 — 자체 핑 태스크 없음, 테스트에서 검증 불가. 목록이 비면 연결하지 않음. 같은 목록 재구독 안 함. 종료 2초 상한은 태스크 취소 대기와 소켓 close 를 합친 예산이다 — 남은 예산이 없으면 close 를 기다리지 않는다.
  - 003 §3.2-4 `age` 는 003 문구 그대로 스트림 `last_message_at` 만 쓴다 — 행은 스트림 메시지로만 생기므로 행이 있는 거래소의 수신 시각은 항상 있다. 저장소를 직접 시드하는 테스트는 헬퍼가 그 거래소 스트림의 `last_message_at` 도 함께 시드한다(스펙 문장 없이 해결).
  - 구조: `QuoteSink`(공통 규칙)와 커넥터(거래소 형식) 분리. 판정 규칙(`judge_state`)은 스펙 공통 규칙이라 core `ticks.py` 에 두고 두 커넥터가 호출한다(커넥터 간 코드 공유 아님). `TickLoop.tick` 은 동기 메서드이고 `run` 이 초 경계까지 잔다.
  - 디코드 불가 바이너리 프레임(UTF-8 아님)은 문자열이 없어 원문 싱크에 기록하지 못하고 버린다(무효 프레임 카운트만).
  - §3.2 마켓 우주 갱신 루프는 거래소 예외가 아닌 예외도 로그 후 다음 회차 — 틱 루프·스트림 `run` 과 같은 보호 규칙(태스크가 조용히 죽어 10분 갱신이 영구 정지하는 것을 막는다).
  - `server/build/`(setuptools 산출물 76파일)가 git 에 추적돼 있다 — 범위 밖이라 두었다(ruff 기본 제외). venv 에는 패키지를 **editable 로만** 설치한다(dev-setup.md) — 비-editable 사본이 있으면 다른 cwd 에서 옛 모듈을 import 한다.
- 남은 빚:
  - 실 네트워크 검증(§4 선택 항목)은 EC2 에서: 업비트·빗썸 접속·구독·재연결·초당 메시지·원문 바이트(010 용량 추정). 업비트 `orderbook_units` 30단계 응답·빗썸 µs `timestamp` 가 실제 프레임과 맞는지도 거기서 확인.
  - 012 전에는 바이낸스 심볼이 없어 우주가 비고 `/spreads` 는 404 다(국내 행도 저장되지 않는다). 012 가 `ForeignSymbolSource` 를 꽂으면 풀린다.
  - 입출금 REST 응답 본문(006 §3.5·010 §3.1)은 아직 원문 싱크에 기록되지 않는다 — `WalletStatusService` 가 `record` 를 주입받는 자리가 없다. 006 세션이 `record=` 를 받아 조회 3종에서 부르고 `main.py` 가 꽂는다. 지금 원문 싱크 밖에 남은 REST 경로는 이것뿐이다.
  - 004 스펙 §4 "깊이 반영" 문구는 004 세션 몫으로 남긴다(`docs/specs/004-analysis.md:§7 깊이 반영 세션 — depth_* 우선 서술 → 행의 asks/bids 만 존재`).
  - `docs/specs/012-binance-stream.md:§2 — "core/connectors/ 의 바이낸스 스트림 커넥터" → 실제 디렉터리는 core/streams/ (architecture.md 현재 구조도 core/streams/binance.py)`. 012 담당 세션 몫.
  - `server/build/`·`server/marketlens_server.egg-info/` 추적 정리는 별도 chore(editable 설치가 egg-info 를 다시 쓴다).
