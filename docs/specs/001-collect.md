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
- 소비자가 지키는 계약(이 스펙이 토대다): ① 003 `POST /refresh` 는 §3.9 트리거를 부른다. ② 003·004 의 걷기는 행의 `asks`/`bids` 를 쓴다(행에 별도 깊이 필드는 없다). ③ 011 추적기는 §3.8 판정을 받는다. ④ Influx·S3 쓰기는 009(틱 인계 → flusher)·010(원문 싱크 → 업로드)만 한다 — 이 스펙에는 쓰기 루프가 없다. ⑤ 바이낸스 시세는 012 의 스트림 커넥터만 준다(REST 시세 호출·깊이 캐시는 없다; 바이낸스 심볼 집합이 비면 바이낸스 행이 없다). ⑥ 006 조회기의 `refresh_if_due` 는 `force` 인자를 받는다 — §3.9 트리거가 60초 주기와 무관하게 조회시키는 길이다. ⑦ 003 `/spreads` 의 `age` 는 스트림 `last_message_at` 기준이되, 행 `updated_at` 이 300초 이상 오래됐으면 그 행의 경과 초다(둘 중 오래된 쪽 — 003 §3.2-4 그대로).

## 3. 동작

### 3.1 서버 골격
- 앱 이름 `MarketLens Backend`, 버전 `0.1.0`. `/health` 응답과 User-Agent(`marketlens-server/<version>`)에 쓴다.
- REST(마켓 목록) 타임아웃 3.0초, 연결 1.5초. WebSocket 핸드셰이크 타임아웃 5초.
- 국내 호가는 누적 `price×size` 가 10억 KRW 에 도달한 단계까지 저장한다(§3.5). 바이낸스는 1,000,000 USDT(012).
- 국내 마켓 통화 `KRW`, 해외 `USDT`. 거래소 주소는 §3.10.
- 에러 형식은 항상 `{"error": {"code": str, "message": str, "detail": object}}`. 거래소 타임아웃 504 `exchange_timeout`, 그 외 거래소 실패 502 `exchange_api_error`. 실패 예외에는 `kind` 8종(`timeout` `network` `rate_limit` `banned` `unavailable` `bad_request` `bad_response` `stale_stream`)과 `retry_after_sec`(헤더 `Retry-After` 초 정수, 없으면 null)이 실리고 분류는 커넥터가 한다(011 §3.2). HTTP `detail` 에는 `exchange`·`url`, 비-200 이면 `statusCode` 와 본문 앞 500자.
- CORS·GZip·uvicorn 워커 1개(메모리가 진실 — 워커가 둘이면 진실도 둘).
- `GET /health` → `200 {"status": "ok", "version": "0.1.0"}`. 스트림 상태와 무관하게 항상 ok.

### 3.2 마켓 우주(universe) — REST 는 "지금 어떤 코인이 있는가" 에만 쓴다
- **매초** 거래소 3곳의 목록을 REST 로 받는다(병렬): 업비트·빗썸 `GET /v1/market/all` 에서 `KRW-` 로 시작하는 마켓, 바이낸스 `GET /api/v3/exchangeInfo` 에서 `status == "TRADING"`·`quoteAsset == "USDT"` 인 심볼(012 §3.3 이 제공). 이 호출의 용도는 **코인 목록 하나**다 — 시세는 절대 REST 로 묻지 않는다. 한도 안이다: 업비트 market 그룹 초당 10회 중 1회, 빗썸 초당 150회 중 1회, 바이낸스 exchangeInfo weight 20 → 분당 1,200(한도 6,000). `/refresh` 트리거(§3.9)는 같은 갱신을 그 자리에서 한 번 더 한다.
- 바이낸스 심볼 집합은 core 계약 하나로 받는다 — `refresh(client) -> int`(REST 로 목록을 갱신하고 나간 호출 수, 실패는 거래소 예외)와 `bases() -> set[str]`(현재 아는 USDT 현물 base). 012 가 구현하고 `main.py` 가 꽂는다. 빈 집합을 주는 기본 구현(테스트용)이 꽂히면 우주가 비고 **행이 하나도 저장되지 않는다**(USDT 시세는 구독하므로 갱신된다).
- 우주 = `(업비트 KRW base ∪ 빗썸 KRW base) ∩ 바이낸스 USDT base`. `USDT` 자신은 우주에 없다(바이낸스에 USDT/USDT 가 없다) — 시세 원천으로만 쓴다.
- 구독 대상: 국내 거래소는 **자기 KRW 전 마켓**(`KRW-USDT` 포함). 바이낸스는 우주의 심볼(012). **목록이 바뀐 그 초에** 차이만 추가 구독·해지한다 — 새 상장은 1초 안에 구독되고, 상폐는 1초 안에 해지된다. 목록이 같으면 아무것도 하지 않는다(재구독 없음).
- 저장 규칙: 우주 밖 base 의 행은 메모리에 넣지 않는다 — 국내 전용·해외 전용 코인은 메모리에 없다. 목록에서 빠진 base 는 그 초에 메모리에서 지운다(상폐 소멸). 그래서 이후의 모든 계산(틱·표·분석)은 항상 "지금 거래소가 말한 코인 목록" 위에서 돈다.
- 주기: 한 회차 = 세 목록을 **동시에**(병렬) 받아 우주를 확정하는 것이고, 회차가 끝난 뒤 1초를 쉬고 다음 회차를 시작한다 — 호출이 오래 걸리면(타임아웃 3초) 그만큼 주기가 늘어날 뿐 초당 1회를 넘지 않는다. `/refresh` 트리거의 회차는 이 주기와 독립이다(같은 초에 2회가 나갈 수 있다 — 한도 안).
- 실패: 한 거래소의 목록 호출이 실패하면(타임아웃·비-200·형식 오류·예상 밖 예외) 그 거래소의 직전 목록을 유지하고 다음 초에 다시 부른다 — 다른 거래소의 목록·구독은 그 회차에 정상 반영된다. 로그는 초당 폭주하지 않게 **같은 거래소·같은 원인은 60초에 1줄**이고, 그 줄에 억눌린 동안의 누적 횟수를 적는다. 원인 = 실패 종류(`kind` 8종 — 거래소 예외가 아닌 예상 밖 예외는 예외 클래스 이름이 원인이고, 트리거 요약(§3.9)에는 `exchange_api_error`·`bad_response` 로 실린다). 판정(§3.8)에는 영향 없다 — 목록 실패는 수집 실패가 아니다. 기동 직후 아직 한 번도 못 받은 거래소는 구독이 없다(목록이 빈 스트림은 연결하지 않는다).
- 응답 본문은 매초 원문 싱크에 기록하되 `key`(업비트·빗썸 `markets:all`, 바이낸스 `symbols:all`)를 붙인다 — 010 이 시세 프레임처럼 분당 마지막 1건만 남긴다(§3.7).

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
2. **틱 생성**(동기, `await` 없음 — 한 틱 안에서 교체 전후 호가가 섞이지 않는 근거): `ts` = 이 초(epoch 초), `rows` = 전 조합 중 자격 통과분의 `{dom, fx, base, fwd, rev}`, `dwFailed` = 조회기의 실패 상태 거래소 목록. `fwd`·`rev` 는 **최우선 1단계 기준의 원값**(슬리피지 차감 전 — 003 §3.2-4 의 raw 규칙과 같은 수식, 009 가 Influx `premium` 에 쓰는 값)이다:
   - 조합 = (국내 거래소 `dom`, 해외 거래소 `fx`, 양쪽에 행이 있는 `base`). 자격: `dom ≠ fx`, 양쪽 행에 `asks[0]`·`bids[0]` 존재, **그 국내 거래소 자신의** USDT 시세(`rate_ask`·`rate_bid`) 존재, 여섯 값(`dom_bid`·`dom_ask`·`fx_bid`·`fx_ask`·`rate_ask`·`rate_bid`) 전부 > 0. 하나라도 빠지면 그 조합은 이 틱에 없다(남의 시세를 빌리지 않는다).
     ```
     premium_percent(buy_krw, sell_krw) = (sell_krw / buy_krw − 1) × 100      # core/premium.py, 003 이 제공
     fwd = premium_percent(buy_krw=fx_ask × rate_ask, sell_krw=dom_bid)       # 원화로 USDT 를 사서(rate_ask) 해외 ask 에 사고 국내 bid 에 판다
     rev = premium_percent(buy_krw=dom_ask,           sell_krw=fx_bid × rate_bid)   # 국내 ask 에 사서 해외 bid 에 팔고 USDT 를 원화로 판다(rate_bid)
     ```
     `dom_bid`=`bids[0].price`, `dom_ask`=`asks[0].price`(국내 행), `fx_bid`·`fx_ask` 도 같은 자리(해외 행). 반올림하지 않는다. 행 정렬은 `(dom, fx, base)` 오름차순.
3. 틱 슬롯에 새 틱을 넣고 **직전 틱**을 009 의 인계 함수(`handoff(tick)`, 동기·무예외)에 넘긴다. 슬롯이 비어 있었으면(첫 틱) 인계 없음.
4. `received_at` = `ts`.
5. 스트림이 등록된 거래소를 판정해(§3.8) 011 추적기에 성공/실패로 넘긴다 — 스트림이 등록되지 않은 거래소는 판정하지 않는다.
앱 종료 시 슬롯의 틱도 인계한다. 틱 루프는 예외를 밖으로 던지지 않는다(버그 하나로 멈추지 않게 로그 후 다음 초).
앱 시작 순서: 011 이력 복원 → 009 spark 복원 → 마켓 우주 → 스트림 기동 → 틱 루프. 어느 것이 실패해도 앱은 뜬다.

### 3.7 원문 싱크 계약 (010 이 구현)
core 공개 함수 `record(exchange: str, source: str, received_at_ms: int, payload: str, key: str | None = None) -> None` — **동기, 예외 없음, 즉시 반환**. 커넥터는 **받은 모든 프레임**(시세·`{"status":"UP"}`·구독 응답·에러 응답 포함)과 **모든 REST 응답 본문**을 이 함수에 넘긴다. `payload` 는 받은 텍스트 그대로(디코드 결과가 아니다). `key` 는 시세 프레임이면 `"<종류>:<원본 심볼>"`(`orderbook:KRW-BTC`·`ticker:KRW-BTC`, 바이낸스는 012 의 `depth20:BTCUSDT`·`miniTicker:BTCUSDT`), 매초 반복되는 마켓 목록 응답은 `markets:all`(업비트·빗썸)·`symbols:all`(바이낸스), 그 밖(입출금 REST·핸드셰이크 거부 본문·비시세 프레임·디코드 실패)은 `None` — 010 이 `key` 있는 줄을 분당 마지막 1건으로 솎는 데 쓴다. 키를 정하려면 프레임을 읽어야 하므로 기록 시점은 "디코드 뒤, **행·시세·상태 갱신 전**" 이다. 디코드에 실패한 프레임은 `key=None` 으로 기록한다. 바이너리 프레임은 UTF-8 디코드한 문자열, 압축 프레임은 라이브러리가 푼 문자열이 원문이다. `source` = `"ws:<경로>"`(스트림 프레임) · `"ws-handshake:<경로>"`(핸드셰이크를 거부한 HTTP 응답 본문 — 거래소가 준 것이라 이것도 원문이다) · `"rest:<경로>"`(예 `ws:/websocket/v1`, `ws-handshake:/websocket/v1`, `rest:/v1/market/all`). 010 이 없으면(`S3_BUCKET` 미설정) 아무것도 하지 않는 구현이 꽂힌다.

### 3.8 판정 (매 틱, 011 이 기록)
거래소마다: **성공** = 스트림이 연결돼 있고 30초 안에 시세를 받았다. 무수신 30초는 **이번 연결의 구독 시각과 마지막 시세 수신 시각 중 최신**부터 센다 — 연결 뒤 아직 시세가 없으면 구독 시각이 기준이고, 오래 끊겼다가 재연결한 직후 첫 프레임 전에도 구독 시각이 기준이라 직전 연결의 수신 시각 때문에 정체로 판정되지 않는다(스냅샷 한 바퀴가 오기까지 1~2초를 실패 구간으로 남기지 않는다). **실패**는 다음 순서로 종류를 정한다 — 미연결이면 `last_error.kind`(핸드셰이크·연결 실패의 분류: DNS·거부·TLS·연결 끊김 `network`, 핸드셰이크 타임아웃 `timeout`, 핸드셰이크가 HTTP 상태로 거부되면 011 §3.2 의 **그 거래소 REST 규칙** — 업비트·빗썸은 429 `rate_limit`·418 `banned`·5xx `unavailable`·그 외 4xx `bad_request`(403 도 `bad_request` — `banned` 로 보는 403 은 바이낸스 WAF 규칙뿐), 그 밖의 상태 `bad_response`; 구독 에러 응답 `bad_request`; 응답 없는 그 외 예외 `bad_response`); 연결됐는데 30초 무수신이면 `stale_stream`. 첫 연결 시도의 결과가 아직 없으면(미연결·오류 없음·수신 없음) 그 틱은 판정하지 않는다 — 기동 직후 1~2초가 실패 구간으로 남지 않게. 시세를 받았던 스트림이 오류 기록 없이 닫혀 있으면 `network`. 실패에는 `message`·`status_code`(없으면 null)·`url`(WebSocket URL)·`body`(핸드셰이크 거부 응답 본문 앞 500자, 없으면 null)·`retry_after_sec` 가 실린다. 디코드 실패 프레임은 버리고 셀 뿐 그 자체로 실패가 아니다(무효 프레임만 30초 이어지면 `stale_stream`). 바이낸스는 012 §3.5(샤드 단위).

### 3.9 즉시 갱신 트리거 (`POST /refresh` 가 부른다 — 003)
순서: 마켓 우주 즉시 갱신(REST) → 변경분 재구독 → 006 조회 즉시 실행(`refresh_if_due(client, force=True)` — 60초 주기 무시) → 요약 반환. 요약 = 거래소별 `{saved: 현재 메모리 행 수, calls: 이 트리거로 나간 REST 호출 수}`, 시세가 있는 국내 거래소 목록, `failures[{exchange, error_code, message}]`(트리거 중 REST 실패는 예외의 code — `exchange_timeout`·`exchange_api_error`, 지금 실패 판정인 스트림은 그 실패의 `kind`), `warnings[]`(006 경고 + USDT 시세 없는 국내 거래소 경고 `"KRW-USDT 호가가 없어 USDT 시세를 못 구한 거래소: upbit (해당 국내 거래소의 김프 계산은 빠진다)."`), `duration_ms`, `fetched_at`(epoch ms). 동시 호출은 직렬화한다. 틱 루프와는 독립이다.

### 3.10 외부 의존 (public WebSocket·REST, 인증 없음)
**업비트** WS `wss://api.upbit.com/websocket/v1`, REST `https://api.upbit.com`
- 구독 요청(연결 직후 1회, 텍스트 프레임): `[{"ticket":"<uuid>"},{"type":"orderbook","codes":["KRW-BTC",…]},{"type":"ticker","codes":["KRW-BTC",…]},{"format":"DEFAULT"}]`. `codes` 는 대문자. 스냅샷 + 실시간을 모두 받는다(`is_only_*` 를 주지 않는다) — 재연결 직후 스냅샷 한 바퀴로 전 마켓이 복구된다. 호가 단계는 `codes` 에 `.unit` 을 붙여 정하며(1·5·15·30) 안 붙이면 **30단계**다. `level`(모아보기)은 주지 않는다.
- 호가 메시지: `{"type":"orderbook","code":"KRW-BTC","timestamp":1787727947526,"total_ask_size":…,"total_bid_size":…,"orderbook_units":[{"ask_price":109950000.0,"bid_price":109880000.0,"ask_size":0.0034,"bid_size":0.188},…],"stream_type":"SNAPSHOT|REALTIME","level":0}` — `orderbook_units` 는 같은 단계의 ask/bid 가 한 쌍이고 정렬돼 온다. `timestamp` 는 ms.
- 체결가 메시지: `{"type":"ticker","code":"KRW-BTC","trade_price":109868000.0,"trade_timestamp":1787729040682,"timestamp":1787729042606,"stream_type":"REALTIME",…}` — `trade_price`·`trade_timestamp`(ms) 만 쓴다.
- 프레임은 바이너리(UTF-8 JSON)로 올 수 있다 — bytes 면 디코드한다. 압축(permessage-deflate)은 라이브러리 옵션이며 켜도 원문은 같다.
- 연결 유지: 서버는 **120초** 무송수신이면 끊는다. **30초마다 PING 프레임**을 보낸다(WebSocket 라이브러리의 keepalive 옵션 — 20초 안에 PONG 이 없으면 끊고 재연결). 텍스트 `PING` 을 보내면 `{"status":"UP"}` 이 10초 간격으로 오는데, 이 프레임은 시세 수신으로 세지 않는다.
- 한도: `websocket-connect` IP 당 초당 5회, `websocket-message` 커넥션당 초당 5회·분당 100회. 마켓 목록 REST 는 매초 1회(market 그룹 초당 10회 한도 안).
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
- 연결 → 구독 메시지 1회 → 메시지 펌프. 메시지마다: 디코드(종류·심볼) → 원문 싱크 기록(§3.7, 받은 텍스트 + `key`) → 행 갱신(§3.5)·USDT 시세(§3.4) → `last_message_at` 갱신(시세 메시지만).
- 끊기면 지수 백오프 1·2·4·…초(상한 30초)로 재연결하고 **구독 뒤 첫 시세 프레임을 받으면 1초로 되돌린다**(구독 메시지를 보낸 것만으로는 성공이 아니다 — 에러 응답이 뒤따를 수 있다). 연결 실패 1회 = 경고 로그 1줄 + `last_error`. 구독할 마켓 목록이 비어 있으면 연결하지 않고 1초마다 목록을 다시 본다.
- 마켓 목록이 바뀌면 그 차이만 추가 구독/해지한다 — 업비트·빗썸은 구독 메시지를 다시 보내면 전체가 교체되므로 연결 중이면 그 자리에서 **전 목록으로 재구독**한다(메시지 한도 안). 같은 목록이면 보내지 않는다.
- 종료: 태스크 취소 후 소켓 close, 합계 2초 상한.

## 4. 검증
네트워크 없음 — 가짜 소켓(메시지를 주입하는 async 제너레이터)과 가짜 REST 로 검증한다.
- `GET /health` 가 200 과 `{"status":"ok","version":…}`. 존재하지 않는 경로는 404.
- 업비트 호가 메시지 1건 → 그 행의 `asks/bids` 가 `[price,size]` 목록으로 교체되고 `updated_at` 이 수신 시각. 30단계가 전부 들어온다(상한 전이면).
- 업비트 체결가 메시지 → `price`·`price_timestamp` 갱신, 호가는 그대로. 호가 전에 온 체결가는 보류됐다가 호가와 함께 실린다.
- 빗썸 잔량 0 단계는 빠지고 최우선 호가도 잔량>0 인 단계. 빗썸 호가 `timestamp`(µs) 가 ms 로 바뀐다. `trade_timestamp` 가 1시간 이상 미래면 9시간을 뺀다.
- 우주 밖 base 의 메시지는 저장되지 않는다. 목록 응답에서 base 가 빠지면 **다음 갱신(1초)에** 그 행이 사라지고 구독이 해지된다. 새 base 가 나타나면 다음 갱신에 구독이 추가된다. 목록이 같으면 재구독이 없다. `USDT` 행은 없다.
- 목록 호출이 매초 나가고(가짜 REST 호출 수 — 회차마다 세 거래소 각 1회, 세 호출은 동시에 진행), 실패한 초에는 직전 목록이 유지되며 다음 초에 다시 부른다. 같은 거래소·같은 원인의 실패 로그는 60초에 1줄이고 그 줄에 억눌린 횟수가 있다 — 원인이 바뀌면 바로 1줄.
- 목록 응답 본문이 원문 싱크에 `key`(`markets:all`)와 함께 기록된다.
- `KRW-USDT` 호가 메시지마다 시세 `ask=asks[0].price`·`bid=bids[0].price` 갱신, 한쪽이 비면 직전 시세 유지.
- 국내 호가는 누적 `price×size` 상한에 도달한 단계까지만 저장된다(`inf` 면 전부).
- 행 교체 시 입출금 3필드가 유지된다.
- 매 초 틱이 생기고 `rows` 가 자격 규칙(다른 거래소·양쪽 호가·자기 시세·여섯 값 > 0)과 원값 수식을 따른다. 두 번째 틱에서 첫 틱이 인계되고, 종료 시 마지막 틱이 인계된다.
- `received_at` 이 매 틱 갱신된다. 틱 생성 중 `await` 가 없다(가짜 스토어로 재진입이 없음을 확인 — 틱이 저장소를 읽고 쓰는 사이에 다른 태스크가 한 번도 돌지 않는다).
- 006 조회 태스크가 끝나지 않은 채 다음 초가 오면 그 초는 새 태스크를 만들지 않는다(직전 태스크 1개만).
- 판정: 연결 + 30초 이내 메시지 → 성공. 연결 실패(`network`)·핸드셰이크 429(`rate_limit`)·403/400(`bad_request`)·30초 무수신(`stale_stream`, url = WS URL)이 각각 실패로 기록되고, 정체됐던 실제 스트림에 시세 프레임이 다시 오면 성공으로 돌아온다. 60초 끊겼다가 재연결한 직후 첫 프레임 전의 판정은 성공이다(직전 연결의 수신 시각이 아니라 이번 구독 시각부터 센다). `{"status":"UP"}`·구독 응답은 수신으로 세지 않는다.
- 기동 시 한 거래소의 목록을 못 받으면 그 거래소는 구독 없이 다음 초에 다시 불리고(다른 거래소의 목록·구독은 첫 회차에 이미 반영), 받는 순간 그 거래소 구독이 시작된다.
- 마켓 목록 갱신이 거래소 예외가 아닌 예외로 끝나도 갱신 루프는 다음 회차(매초)를 계속 돈다.
- 재연결 백오프가 1·2·4…30 으로 자라고 **구독 뒤 첫 시세 프레임**을 받은 뒤에만 1로 돌아온다 — 연결 실패 2회 뒤 구독이 거부되면 세 번째 대기는 1 이 아니라 4 다(두 커넥터 모두). 구독 에러 응답은 `bad_request`.
- 종료: 소켓 `close()` 가 돌아오지 않아도 `aclose` 는 2초 상한 안에 끝난다.
- 모든 프레임(시세·UP·에러)과 마켓 목록 응답 본문이 원문 싱크(fake)에 `exchange`·`source`·수신 시각과 함께 받은 텍스트 그대로 기록된다 — 행 갱신보다 먼저. 시세 프레임은 `key`(`orderbook:KRW-BTC`·`ticker:KRW-BTC`)와 함께, 마켓 목록 응답 본문은 `markets:all` 로, UP·구독 응답·에러·입출금 REST 본문(006)·디코드 실패 프레임은 `key=None` 으로. 핸드셰이크가 HTTP 본문과 함께 거부되면 그 본문 전문이 `ws-handshake:<경로>` 로 기록된다(본문 없는 거부는 기록 없음).
- 트리거(§3.9): 우주 갱신 REST 가 호출되고 `calls` 에 반영, `saved` 가 현재 행 수, 실패 중인 스트림이 `failures` 에 담긴다. 동시 호출은 직렬화된다.
- 거래소 타임아웃은 504 `exchange_timeout`, 비-200 은 502 `exchange_api_error`(HTTP `detail` 에 `statusCode`·`body`).
- 테스트 전부 통과, ruff lint·format 위반 0.
- 선택(실 네트워크, 실패해도 완료를 막지 않음 — §7 에 기록): EC2 에서 기동 → 10초 안에 업비트·빗썸 행 각 100 이상, USDT 시세 둘 다, 1분 동안 재연결 0회, 초당 메시지 수와 원문 바이트 수를 §5 에 적는다(010 의 용량 추정 갱신).

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q
# All checks passed! / 183 files left unchanged / 468 passed, 1 warning in 4.9s  (2026-09-06, drift 검수 반영 뒤 3회 연속 통과)
# 001 몫: tests/test_store.py test_quotes.py test_stream_upbit.py test_stream_bithumb.py test_ticks.py test_universe.py(11) test_collect_trigger.py test_health.py test_rows.py (§4 항목당 1개 이상)
# 우주 항목(§4): test_universe.py — 매초 회차·세 목록 동시 진행·실패한 초는 직전 목록 유지·다음 초 재호출·같은 원인 60초 1줄(억제 횟수)·원인/거래소가 다르면 즉시·예상 밖 예외도 그 거래소 실패로 루프 계속
# 원문 항목(§4): 시세 프레임은 `key`(`orderbook:KRW-BTC`·`ticker:KRW-BTC`)와 함께 행 갱신 전에, 목록 응답 본문은 `markets:all` 로, UP·구독 응답·핸드셰이크 거부 본문·디코드 실패 프레임은 `key=None` — test_stream_upbit.py `test_fetch_markets_filters_krw_and_records_body`, test_stream_bithumb.py `test_fetch_markets_krw_filter_and_raw_record`

cd server && S3_BUCKET= .venv/bin/python -m uvicorn app.main:app --port 8044   # 로컬 스모크 (빈 포트, 끝나고 kill)
curl -s localhost:8044/health          # {"status":"ok","version":"0.1.0"}
curl -s localhost:8044/spreads         # 망이 막힌 상태(2026-09-06): 404 market_data_not_found(detail {"exchange":"upbit"}) — 시세가 없다. 망이 열리면 기동 15초 뒤 행 461(012 §5)
curl -s localhost:8044/health/collect  # 거래소 3곳 state "down"·outages [] (망 차단). 망이 열리면 셋 다 "ok"(012 §5)
curl -s -X POST localhost:8044/refresh # calls upbit 1·bithumb 1·binance 1, failures 셋 다 exchange_timeout(ConnectTimeout), 11.5초(목록 3초 + 입출금 3종 타임아웃)
# 로그: 기동 직후 거래소별 "마켓 목록 갱신 실패 — 직전 목록 유지" 1줄씩, 60초 뒤 "(직전 60초 동안 같은 원인 25회 억제)" 1줄씩 — 회차가 연결 타임아웃 1.5초 + 1초 휴식이라 60초에 25회. SIGTERM 뒤 "Application shutdown complete", 트레이스백 0, 포트 해제
```
- 선택 항목(실 네트워크 — 이 Mac 망이 거래소를 막아 이번 세션은 못 쟀다, 직전 실측 2026-09-05 로컬):
  - 마켓 목록 REST 0.26초(업비트 287·빗썸 479 마켓). t+10s 행 upbit 286·bithumb 478, USDT 시세 둘 다(1367/1366), 판정 둘 다 ok, decode 실패 0.
  - 메시지·원문(비압축): 업비트 ws 175 msg/s·442 KB/s, 빗썸 ws 765 msg/s·1,018 KB/s → 국내 둘 ≈1.4 MB/s ≈ **130 GB/일**(010 용량 추정의 비압축 기준값). 실프레임 형식은 §3.10 과 일치.
  - 앱 기준(우주 = 교집합) 행 수는 012 §5 — 기동 15초 뒤 upbit 198·bithumb 292·binance 273.
  - 매초 목록 호출을 실거래소에 60초 이상 돌린 실측(한도·응답 시간·재구독 0회)은 EC2 확인 필요.

## 6. 갱신할 문서
- `docs/context/status.md` — collect 행을 `| collect | 업비트·빗썸 WS 실시간 갱신·마켓 목록 REST 매초(우주)·1초 틱·/health | - | 바이낸스는 012 |` 로. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 001 행 상태 → DONE. **항상 포함.**
- `docs/context/architecture.md` — "현재 구조" 절의 collect 항목을 실제 모듈로(스트림 커넥터 2개·LiveStore·틱 루프·우주 갱신·계약 Protocol 들).
- `docs/context/dev-setup.md` — 스모크의 `/spreads` 확인 시점을 "기동 10초 뒤" 로, 로컬 망에서 WS 도메인 차단 시 EC2 확인 메모.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `server/app/core/models.py` — `Row`(호가는 `asks`/`bids` 뿐)·`Rate`·`StreamError`·`StreamState`·`Tick`·`TickRow`. `core/live_store.py` — 행 단위 쓰기(`put_row`·`put_rows`·`remove_row`·`retain_bases`)·입출금 3필드 물려받기·스트림 상태(`stream`/`stream_state`/`streams`)·틱 슬롯(`push_tick`/`tick`)·spark 맵. `core/rows.py` — `clean_levels`(잔량 필터 + 누적 상한).
  - `core/quotes.py` — `QuoteSink`: 메시지 → 행 규칙(§3.4·§3.5) 전부. 우주 필터·체결가 보류·USDT 시세·빈 호가 삭제.
  - `core/contracts.py` — 원문 싱크 `RawRecorder`/`noop_record`, 틱 인계 `TickHandoff`/`noop_handoff`, `OutageSink`, `StreamJudge`/`Verdict`, `WalletStatusProvider`, `ForeignSymbolSource`/`NoForeignSymbols`.
  - `core/streams/upbit.py`·`core/streams/bithumb.py` — 스트림 커넥터 2개(연결·구독·펌프·분류·백오프·재구독·`fetch_markets`(본문을 `markets:all` 로 기록)·`judge`). 코드 공유 없음. `core/universe.py` — `UniverseRefresher`(매초 회차·세 목록 `gather` 병렬·실패한 거래소는 직전 목록 유지·`(거래소, 원인)` 당 60초 로그 억제·교집합·구독 목록 배포). `core/ticks.py` — `judge_state`·`build_tick`·`TickLoop`. `core/collect.py` — `CollectService.refresh_now`·`RefreshSummary`. `core/config.py` — `EXCHANGES`·`DOMESTIC_EXCHANGES`·`WS_OPEN_TIMEOUT`.
  - `app/main.py` — lifespan 배선(이력 복원 → 우주 → 스트림 → 틱 루프, 종료 시 마지막 틱 인계). `app.state.collector` = `CollectService`.
  - 소비자: `core/orderbook.py` `walk_levels` 가 행의 `asks`/`bids` 만 본다. `features/spreads/`(router `refresh_now`, service `age` 스트림 기준·`RefreshSummary`), `features/analysis/tests/`, `features/health/tests/`, `features/wallet_status/service.py`(`force`), `tests/test_outages.py`, `tests/test_wallet_integration.py`.
  - 테스트: `tests/conftest.py`(`make_row`·`FakeStream`·`RawLog`), `tests/stream_fakes.py`, `tests/test_store.py` `test_quotes.py` `test_stream_upbit.py` `test_stream_bithumb.py` `test_ticks.py` `test_universe.py`(매초·병렬·억제 로그 — 주입 시계 `monotonic`) `test_collect_trigger.py`.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - §3.2 주기의 뜻: "매초" 를 초 경계 정렬이 아니라 **회차 끝 + 1초 휴식** 으로 확정. 선택지는 ① 초 경계에 맞춰 시작(호출이 1초를 넘기면 같은 초에 두 회차가 겹쳐 한도 계산이 흔들린다) ② 회차 끝난 뒤 1초 휴식(추천 — 초당 1회를 절대 넘지 않고, 타임아웃 중엔 주기가 늘어날 뿐이다). 스모크에서 연결 타임아웃 1.5초 + 1초 = 60초에 25회.
  - §3.2 로그 억제의 "원인" = 실패 종류 `kind`(거래소 예외) 또는 예외 클래스 이름(예상 밖 예외). 선택지는 메시지 전문(타임아웃 메시지에 예외 세부가 섞여 매초 다른 줄이 될 수 있다)·`(kind, status_code)`·`kind` — `kind` 를 택했다. 60초 뒤의 줄에 억눌린 횟수를 적는다.
  - §3.2 예상 밖 예외(거래소 예외가 아닌 것)는 그 거래소의 목록 실패로 다룬다 — 직전 목록 유지, 트리거 요약에는 `exchange_api_error`/`bad_response` 로 실린다.
  - §3.2 세 호출은 `asyncio.gather` 로 동시에 — 한 거래소가 3초 타임아웃이어도 나머지 목록은 그 회차에 반영된다. `/refresh` 트리거는 같은 `refresh()` 를 따로 한 번 더 부른다(루프와 겹쳐도 초당 2회 — 한도 안).
  - §3.7 목록 응답 `key` = `markets:all`(업비트·빗썸)·`symbols:all`(바이낸스) — 매초 1~2MB 가 전량 남지 않게 010 이 분당 마지막 1건으로 솎는다. 입출금 본문(006)은 60초 1회라 그대로 `None`.
  - 012 §3.3 `set_universe` 는 매초 불리므로 샤드 배정이 하나도 안 바뀌면 행 삭제·재조정 깨우기 없이 돌아온다(001 은 확정된 우주를 매초 그대로 넘긴다 — 무동작 판단은 커넥터 몫).
  - §4 "기동 시 한 거래소의 목록을 못 받으면" 문구를 매초 전체 회차에 맞게 고쳤다(그 거래소는 구독 없이 다음 초에 다시, 다른 거래소는 첫 회차에 반영).
  - §3.3 체결가 없을 때 `price_timestamp` = 호가 메시지의 거래소 시각(ms). §3.4 USDT 시세도 잔량 필터를 거친 최우선 호가. §3.6 입출금 캐시를 매 틱 행에 반영, 스트림 없는 거래소는 판정하지 않는다. §3.8 첫 연결 결과 전 판정 보류, 무수신 기준 = 이번 연결의 구독 시각과 마지막 수신 시각 중 최신, 오류 없이 닫힌 스트림 = `network`. §3.9 006 즉시 조회 = `refresh_if_due(force=True)`, 스트림 실패의 `error_code` = `kind`. §3.11 백오프 리셋 시점 = 구독 뒤 첫 시세 프레임, PING 은 라이브러리 keepalive, 종료 2초 상한은 취소 대기와 close 를 합친 예산.
- 남은 빚:
  - §4 선택 항목(실 네트워크)은 망 차단으로 이번 세션에 못 쟀다 — EC2 에서 매초 목록 호출을 60초 이상 돌려 한도·응답 시간·재구독 0회를 확인해야 한다.
  - 우주 확정(`_apply`) 자체가 예외로 끝나면 루프의 보호 로그가 초당 1줄이다(거래소·원인 억제 밖) — 버그 경로라 두었다.
  - `server/build/`·`server/marketlens_server.egg-info/` 추적 정리는 별도 chore(editable 설치가 egg-info 를 다시 쓴다).
