# 020 — bitget

상태: DONE | 의존: 001(collect — 행 계약·마켓 우주·틱 판정·원문 싱크·커넥터 공통 규칙), 006(wallet-status — 입출금 조회기 계약·망 판정), 011(health — 실패 분류·구간 추적), 012·019(binance-stream·bybit — 샤드·판정·로컬 북 규칙의 원형), 005·014(`/history/*` 의 `fx` 파라미터)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
해외 거래소 세 번째로 **비트겟(`bitget`) USDT 현물**을 붙인다. 끝나면 스프레드 표에 `fx = bitget` 행이 생겨 트레이더가 같은 코인의 바이낸스·바이빗·비트겟 김프를 나란히 보고, 비트겟 입출금 상태까지 표에서 본다. 기록 탭의 해외 선택지에 Bitget 이 실데이터로 생긴다.
바이빗처럼 시세는 WebSocket 만, REST 는 심볼 목록(매초)과 입출금 상태(60초)에만 쓴다. 커넥터는 012·019 와 **코드를 공유하지 않고** 규칙만 같다(architecture.md 원칙). 비트겟 공식 문서는 클래식(v2) API 기준이며 2026-09-15 에 확인했다 — UTA(v3) API 는 쓰지 않는다.

## 2. 범위
- 만드는 것: 비트겟 스트림 커넥터(샤드 3개·구독 재조정·호가 북·핑·재연결·정체 판정), 심볼 목록 REST 조회, 비트겟 입출금 조회기(공개 API, 키 없음), 테스트. 라이브러리 추가 없음(`websockets`·`httpx` 기존).
- 바꾸는 기존 것:
  1. **마켓 우주(001 §3.2)** — 해외 원천이 셋이 된다. 우주 = `(업비트 KRW base ∪ 빗썸 KRW base) ∩ (바이낸스 ∪ 바이빗 ∪ 비트겟 USDT base)`. 우주가 확정될 때마다 해외 원천 각각에 `set_universe(우주)` 를 넘기고, 자기 맵에 없는 base 는 그 커넥터가 무시한다(019 §2-1 규칙 그대로).
  2. **거래소 목록 5곳** `upbit·bithumb·binance·bybit·bitget` 고정 순서 — 틱 판정(011), `/health/collect` 의 `exchanges[]`, `/refresh` 의 `snapshots[]`, 입출금 반영 순서 전부.
  3. **입출금(006)** — 조회기 5종. 비트겟은 공개 엔드포인트라 **env 키가 없고** `.env.example` 도 바뀌지 않는다.
  4. **`/history/premium`·`streaks`·`streaks/bulk`·`candles`** 의 `fx` ∈ {`binance`, `bybit`, `bitget`}(기본 `binance` 그대로). 그 밖의 값은 지금처럼 422. `/history/events` 는 `fx` 필터가 없어 비트겟 사건이 함께 나온다.
  5. **web** — 표시명 `bitget → Bitget`(스프레드 표의 "비교 해외 거래소" Bitget 체크박스는 002 부터 있다), 기록 탭 해외 선택지에 Bitget 을 **실데이터**로 추가(Bybit 다음, MEXC 앞. MEXC 는 mock 유지). 실데이터 해외 거래소의 봉은 선택된 것만 부른다(019 §2-5 규칙 그대로).
  6. 004 의 거래소 레지스트리에 `bitget`(표시명 `Bitget`)을 더해 `/orderbook/bitget`·`/slippage/bitget` 이 열린다 — §4 수동 확인이 이 경로를 쓴다.
- 하지 않는 것: 004 의 나머지 API(`/premium`·`scan`·`matrix`·`/arbitrage`)의 해외 선택(바이낸스 고정 — 기존 빚 그대로). 백필 스크립트(binance 만). `/spreads` 행 계약(키 17개 그대로 — bitget 행은 `fx` 값만 다르다). 사건(013)·1분봉(014)·틱 저장(009)·원문 아카이브(010)·표 푸시(017) — 거래소 id 나 틱 행 단위로 돌아 코드 변경 없이 bitget 이 흘러든다(§3.7). 선물·비트겟 파생. 호가 `checksum` 검증(현재 클래식 문서의 현물 `books` 페이로드에 checksum 이 없다).

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001 §3.3: 행 = `(exchange, base)` 당 `quote`·`native_symbol`·`price`·`price_timestamp`·`asks`·`bids`(누적 1,000,000 USDT 도달 단계까지, 최소 1단계, 잔량 ≤0 제거)·입출금 3필드(물려받음)·`updated_at`. 거래소별 스트림 상태 `{connected, last_message_at, last_error, subscribed, url, connected_since}`.
- 001 §3.2: 해외 심볼 원천 계약 `id` / `refresh(client) -> int`(REST 1회, 실패는 거래소 예외) / `bases() -> set[str]` / `set_universe(bases)`(매초·`/refresh`·기동, 배정이 같으면 무동작). `main.py` 가 우주에 목록 `[binance, bybit, bitget]` 으로 꽂는다.
- 001 §3.7: 받은 모든 프레임과 REST 본문은 원문 싱크 `record(exchange, source, received_at_ms, payload, key)` 로 — 행·상태 갱신 전에, 텍스트 그대로. 시세 프레임은 `key` = `orderbook:<심볼>`·`trade:<심볼>`(대문자 원본 심볼), 매초 오는 심볼 목록 본문은 `symbols:all`, 구독 응답·`pong`·깨진 프레임·핸드셰이크 거부 본문·입출금 본문은 `key=None`. 010 이 `key` 있는 줄을 분당 마지막 1건으로 솎는다 — update 프레임도 같은 규칙이라 **아카이브로 북을 재생할 수는 없다**(019 와 같은 빚).
- 011 §3.2: 실패 8종 `timeout network rate_limit banned unavailable bad_request bad_response stale_stream`. 매 틱 거래소마다 판정해 추적기에 넘긴다.
- 006 §3.1·§3.5 + architecture.md core 계약: 입출금 조회기는 `refresh_if_due(client, force=False)` / `apply(rows, exchange)` / `availability()` / `warnings()` / `failed()`. 코인 상태 `open / closed / unknown`, **`unknown` 을 열림으로 읽지 않는다**. 망 = `{code, name, dep, wd}`(code 대문자, name 은 표시명), 실패 회차는 직전 값을 지키지 않고 `unknown` 으로 덮는다. 요청 타임아웃 10초, 본문은 성공·실패 모두 원문 싱크로. 망 판정(국내 망 기준·정규화·동일 체인 쌍)은 006 §3.6~3.7 그대로.

### 3.2 스트림
- 엔드포인트 `wss://ws.bitget.com/v2/ws/public`(공개 채널, 로그인 없음). 구독 `{"op":"subscribe","args":[{"instType":"SPOT","channel":"<채널>","instId":"<SYMBOL>"}, …]}`, 해지는 `op:"unsubscribe"`. 응답 `{"event":"subscribe","arg":{"instType":"SPOT","channel":"books","instId":"BTCUSDT"}}` 은 **arg 하나마다 1개**가 오고 시세로 세지 않는다. 거부는 `{"event":"error","code":"30005","msg":"…"}`(code 는 문자열) → `bad_request`(message 에 `code`·`msg`)로 실패하고 재연결한다.
- 요청 한도(공식 문서): 한 연결에 **초당 메시지 10개**(`ping` 포함), **구독 요청 시간당 240회/연결**, 채널 **1,000개/연결**(권장 50개 미만 — 권장값이라 3샤드로 가고 EC2 실측), 요청 하나의 `args` 총길이 **4,096바이트 이하**, 연결 요청 IP 당 5분에 300회·동시 100연결. 한도를 넘기면 서버가 끊고 반복되면 IP 를 막는다.
- 심볼마다 두 채널. **`books`** — 전체 깊이. 구독 직후 `action:"snapshot"`(전체 북), 이후 바뀐 단계만 `action:"update"` 로 **200ms 마다**. 페이로드 `{"action":…,"arg":{…,"instId":"BTCUSDT"},"data":[{"asks":[["<가격>","<잔량>"],…],"bids":[…],"seq":<정수>,"ts":"<ms 문자열>"}],"ts":<ms>}` — 가격·잔량 문자열, `asks` 오름차순·`bids` 내림차순, `data` 는 원소 1개. **`trade`** — 체결 즉시 `{"action":"snapshot"|"update","arg":{…,"channel":"trade"},"data":[{"ts":"<체결 ms>","price":"…","size":"…","side":"buy|sell","tradeId":"…"},…]}`. 구독 직후 첫 프레임은 **최근 체결 스냅샷**(과거 체결 포함)이고 이후는 새 체결만 온다.
- 왜 `books`·`trade` 인가: 현물 호가 채널은 `books`(전체)·`books1`·`books5`·`books15` 뿐이고 스냅샷형 최대가 15단계라 바이낸스 20단계보다 얕다 — 전체 깊이를 로컬 북으로 들고 행에는 001 규칙으로 잘라 넣는다(019 와 같은 방식). 초당 상한은 심볼당 5개(200ms)로 바이빗의 절반이다. 체결가는 `ticker`(스냅샷 주기 푸시) 대신 체결이 있을 때만 오는 `trade` 를 쓴다 — 바이낸스 miniTicker·바이빗 publicTrade 와 의미가 같다.
- **핑**: 클라이언트가 30초마다 **문자열 `ping`** 을 보낸다(JSON 아님). 응답은 **문자열 `pong`** 이고 시세가 아니다(`key=None`, JSON 디코드 실패로 세지 않는다). **보낸 핑의 pong 이 30초 안에 안 오면 끊고 재연결**한다 — 그 샤드의 마지막 오류는 `timeout`(message 에 "pong 없음"). 핑이 2분 없으면 서버가 끊는다. 라이브러리 keepalive(제어 프레임 ping)는 끈다 — 문서에 없다. 24시간 강제 종료는 문서에 없다.
- 시세가 아닌 프레임(구독 응답·`pong`·깨진 JSON·맵에 없는 심볼)은 원문 싱크에만 남기고 시세 수신으로 세지 않는다. 판별: 텍스트가 `pong` 이면 pong, JSON 에 `event` 키가 있으면 응답(`event == "error"` 면 거부), `action`·`arg` 가 있으면 시세. `action` 이 `snapshot`·`update` 둘 다 아니면 디코드 실패로 센다.
- 재연결은 지수 백오프 1·2·4…30초, **구독 뒤 그 샤드의 첫 시세 프레임**에서 1초로 리셋(001·012·019 와 같다). 핸드셰이크 HTTP 거부는 §3.8 규칙. 재연결은 새 연결이므로 시간당 240회 구독 예산도 새로 시작한다.

### 3.3 심볼 목록과 샤딩
- 심볼 목록: `GET https://api.bitget.com/api/v2/spot/public/symbols`(파라미터 없음 = 전체) → `data[]` 중 `status == "online"` 이고 `quoteCoin == "USDT"` 인 것(`status` 는 `online / offline / gray / halt`). base = `baseCoin`, 심볼 = `symbol`(예 `BTCUSDT`). 페이지네이션 없음. 봉투 `{"code":"00000","msg":"success","requestTime":<ms>,"data":[…]}` — **HTTP 200 이어도 `code != "00000"` 이면 실패**(§3.8). **매초** 갱신(001 §3.2 — IP 한도 초당 20회의 5%), 본문은 `rest:/api/v2/spot/public/symbols` 로 원문 싱크(`symbols:all`). 실패 시 직전 목록 유지 + 경고. 파싱 결과가 같으면 아무 일도 하지 않는다. 본문 크기·파싱 시간은 미확인(EC2 실측 항목).
- base 하나에 USDT 심볼이 둘 이상이면 처음 것. 우주 base 중 맵에 없는 것은 구독 대상이 아니다. `USDT` 자신은 우주에 없다(001).
- **3 샤드**, 배정 = `crc32(심볼) % 3`, 한 심볼의 두 채널은 같은 샤드 — 012·019 와 같은 규칙.
- 재조정 규칙은 019 §3.3 그대로: 우주 확정마다 `set_universe`, 빠진 심볼의 행은 그 자리에서 삭제, 연결된 샤드마다 원하는 구독과 실제 구독의 **차이만** 전송, 실제 구독 집합은 소켓에 묶인다, 60초마다 한 번 자동 재조정, 배정 0 샤드는 연결하지 않는다. 다른 점: 한 요청 `args` **50개 이하**(원소 하나 약 60바이트 → 3,000바이트, 4,096 한도 안), 요청 사이 **0.2초**(초당 5개 — 핑을 더해도 초당 10개 안). 배정 150 심볼이면 6요청·1.2초. 구독 요청 수를 샤드별로 세어 시간당 240회의 80%(192회)를 넘으면 경고 1줄을 남기고 재조정을 다음 회차로 미룬다.

### 3.4 호가 북과 행 갱신
- 심볼마다 **로컬 북**(가격 → 잔량, 매도·매수 각각)과 마지막 `seq` 를 둔다. `snapshot` → 북을 통째로 교체. `update` → 잔량 `0` 은 그 가격 삭제, 없는 가격은 삽입, 있으면 잔량 교체(공식 문서 규칙). **`seq` 가 마지막 `seq` 이하인 update 는 버린다**(중복·순서 뒤바뀜). 누락 감지는 불가능하다 — 문서가 이전 seq 를 주지 않는다(빚, §6 status.md). 재연결하면 새 스냅샷이 오므로 소켓이 바뀔 때 그 샤드의 북을 전부 비운다. **스냅샷 전에 온 update 는 버린다** — 다만 수신 시각(`last_message_at`)에는 센다(019 와 같다).
- 매 호가 메시지 뒤 그 심볼의 행을 다시 만든다: `asks` 오름차순·`bids` 내림차순으로 정렬해 001 규칙으로 자른 것, `updated_at` = 수신 시각, 호가 시각 = `data[0].ts`(문자열 → 정수 ms). 한쪽이 비면 행을 저장하지 않는다(있던 행은 지운다). 초당 상한 5개/심볼이라 우주 300 종목에 최대 1,500회/초, 실제 비용은 EC2 실측 항목.
- `trade` → `data[]` 중 **`ts` 가 가장 큰 원소**의 `price` 가 `price`, 그 `ts` 가 `price_timestamp`(배열 순서를 믿지 않는다). 이미 있는 `price_timestamp` 보다 오래된 체결은 무시한다(첫 스냅샷이 과거 체결을 실어도 값이 뒤로 가지 않는다). 행이 없으면 보류했다가 호가가 오면 함께 싣는다. 체결가가 없으면 mid — 이때 `price_timestamp` 는 수신 시각(012·019 와 같다).
- 캐시 키·`native_symbol` 은 원본 심볼 대문자(`BTCUSDT`). `arg.instId` 가 맵에 없으면 그 프레임은 버린다. 입출금 3필드 물려받기·우주 밖 버리기는 001 §3.5.

### 3.5 정체 판정 (매 틱, 샤드 단위)
019 §3.5 와 같다 — 샤드마다 `last_message_at`·배정 수·구독 시각, 조용한 시간 = 지금 − max(마지막 시세, 연결 중이면 구독 시각), 배정 0 샤드는 판정 대상 아님, 연결됐는데 **30초** 무수신 → `stale_stream`, 미연결 → `last_error.kind`, 여러 샤드가 나쁘면 가장 오래 조용한 샤드(동률이면 작은 번호), 전부 판정 없음이면 없음. `pong` 만 오고 시세가 없어도 30초면 정체다. 실패 `message` = `"비트겟 스트림 정체: 샤드 2 (구독 7종목) 30초 이상 무수신"`, `url` = WS URL. `store.stream("bitget")` 집계도 같다.

### 3.6 입출금 상태 (006 의 다섯 번째 조회기 — 공개 API)
- `GET https://api.bitget.com/api/v2/spot/public/coins`(파라미터 없음 = 전 코인). **인증 없음, 서명 없음, env 키 없음** — 빗썸처럼 항상 조회 가능하다. 한도 IP 당 초당 3회(60초 주기라 무관). 봉투는 §3.3 과 같고 `code != "00000"` 은 실패(§3.8).
- 응답 `data[]` 의 `coin`(대문자 심볼)·`chains[]`. 망 = `code = chain 을 대문자로`(예 `BTC`·`ERC20`·`TRC20`), `name = chain` 원문(비트겟은 망 표시명 필드가 따로 없다), `dep = (rechargeable == "true")`, `wd = (withdrawable == "true")`(둘 다 **문자열** `"true"/"false"`). `chains` 가 빈 코인은 결과에 없다. 코인 단위 dep/wd = 한 망이라도 열려 있으면 ok. `transfer`·`needTag`·`withdrawFee`·`congestion` 은 판정에 쓰지 않는다(`congestion != "normal"` 이어도 열림은 열림 — 어긋난 실례가 보이면 원문 본문으로 확인해 규칙을 정한다).
- 실패 회차 처리·`/refresh` 경고·`walletStatusAvailable` 은 006 §3.5 그대로. 본문은 `rest:/api/v2/spot/public/coins` 로 원문 싱크(`key=None`).

### 3.7 다른 기능에 흘러드는 것 (코드 변경 없이)
- 틱(001 §3.6-2)은 국내×해외 **전 조합**이라 `(upbit|bithumb) × bitget` 행이 생긴다 → 003 표·017 푸시·013 사건·014 1분봉·009 Influx `premium` 에 `fx=bitget` 태그 값으로 들어간다. Influx 스키마(db.md)는 그대로고 카디널리티만 대략 1.5배.
- 010 은 `raw/exchange=bitget/…` 객체를 자동으로 갖는다. 011 추적기는 거래소 문자열 키라 자동.

### 3.8 실패 분류 — 비트겟 REST·핸드셰이크 규칙 (011 §3.2 에 추가하는 행)
- HTTP 429 → `rate_limit`(문서: "the request is too frequent"). 403 → `banned`(문서: "You do not have access to the requested resource" — 반복 초과 시 IP 차단, 해제 시간은 문서에 없다). 5xx → `unavailable`. 그 외 4xx → `bad_request`. `Retry-After` 는 문서에 없으니 `retry_after_sec` 는 null.
- HTTP 200 + `code != "00000"` → `bad_response` 에 원문 body(`status_code` 200). 한도 초과를 본문 code 로 알리는 경우는 문서에 없다 — 보이면 body 를 보며 표를 채운다(011 원칙).
- 공통(REST): httpx 타임아웃 → `timeout`, 전송 예외 → `network`, JSON 아님·`data` 없음 → `bad_response`. WebSocket: 연결 실패 → `network`, 핸드셰이크 타임아웃 5초 → `timeout`, HTTP 거부는 위 규칙, `event:"error"` → `bad_request`.

## 4. 검증
네트워크 없음 — 가짜 소켓·가짜 REST·가짜 원문 싱크(001 의 fakes 재사용).
- snapshot → 행의 `asks` 오름차순·`bids` 내림차순 float, 누적 1,000,000 USDT 에서 잘리고 첫 단계가 넘어도 1단계는 남는다. `native_symbol` 은 `BTCUSDT`, 호가 시각은 `data[0].ts` 를 정수로.
- update: 잔량 0 은 삭제, 새 가격 삽입, 기존 가격 교체 → 행이 그 결과로 다시 만들어진다. `seq` 가 직전 이하인 update 는 무시(원문에는 남고 수신 시각에는 센다). 스냅샷 전 update 는 무시. 새 snapshot 은 북을 통째로 교체. 재연결 뒤 옛 북에 update 가 얹히지 않는다.
- trade 3건 중 `ts` 가 가장 큰 것이 `price`·`price_timestamp`. 현재 값보다 오래된 체결은 무시. 호가 전에 오면 보류, 호가 뒤에 실린다. 체결가 없으면 mid 이고 `price_timestamp` 는 수신 시각.
- symbols 에서 `online`·`USDT` 만 심볼 집합에 든다(`gray`·`halt`·`offline` 제외). 원문 source 는 `rest:/api/v2/spot/public/symbols`, key `symbols:all`. `code != "00000"` 본문(HTTP 200)은 실패이고 직전 목록 유지.
- 우주 = 국내 합집합 ∩ (바이낸스 ∪ 바이빗 ∪ 비트겟). 비트겟에만 있는 base 는 비트겟 행만 생기고, 각 커넥터는 자기 맵에 없는 base 를 구독하지 않는다. 틱에 `(upbit, bitget, BTC)`·`(bithumb, bitget, BTC)` 행이 나온다.
- 심볼 300개 → 3 샤드, 같은 심볼은 항상 같은 샤드(해시 안정성 — 서브프로세스 2회), 두 채널 같은 샤드. 구독 요청 `args` ≤ 50, 요청 사이 0.2초, 요청 직렬화 길이 ≤ 4,096바이트. 재조정: 새 심볼 구독·빠진 심볼 해지·행 삭제, 같은 우주 재수신은 전송 0. 시간당 구독 요청 193회째는 보내지 않고 경고 1줄.
- 구독 응답은 arg 마다 1개 오고 시세로 세지 않는다. `event:"error"` → `bad_request`, message 에 code·msg, 재연결.
- 핑: 30초마다 문자열 `ping`, 문자열 `pong` 은 시세가 아니고 디코드 실패도 아니며 `key=None`. pong 이 30초 안에 없으면 재연결·`timeout`.
- 정체: 샤드 2만 30초 무수신 → `stale_stream`, message 에 "샤드 2". pong 만 오는 샤드도 30초면 정체. 미연결 샤드 → `last_error.kind`. 둘이 나쁘면 더 오래 조용한 쪽.
- 원문: 모든 프레임이 원문 그대로 기록 — 호가·체결 프레임은 `orderbook:BTCUSDT`·`trade:BTCUSDT`, 심볼 목록은 `symbols:all`, 구독 응답·pong·핸드셰이크 거부·입출금 본문은 `key=None`. 행 갱신 전에 기록된다.
- 분류: 429 → `rate_limit`, 403 → `banned`, 200+`code:"40001"` 류 → `bad_response`(status_code 200), `event:"error"` → `bad_request`. 백오프 1·2·4…30, 구독 뒤 첫 시세에서 1.
- 입출금: 요청에 인증 헤더가 없다. `rechargeable "true"/"false"` → `dep true/false`, `withdrawable` → `wd`, 망 `code` 는 `chain` 대문자·`name` 은 원문, `chains` 빈 코인은 결과에 없음, 한 망만 열려도 코인 dep ok. `code != "00000"` → 실패·전 코인 `unknown`·경고 1줄·`walletStatusAvailable` false. 키가 없어도 경고가 나지 않는다.
- `/health/collect` 의 `exchanges` 가 5곳 순서 `upbit bithumb binance bybit bitget`. `/refresh` 의 `snapshots` 5항목. `/history/candles?fx=bitget` 는 200, `fx=mexc` 는 422. `/orderbook/bitget` 이 열린다.
- 연결 실패 기동(lifespan 실제, 소켓·REST 가짜) → `/health` 200, 샤드마다 경고 1줄, 바이낸스·바이빗 행은 계속 갱신된다. 종료 시 태스크 취소·소켓 close, 잔여 예외 없음.
- web: `npm run lint && npm run build`. 스프레드 표에서 Bitget 체크를 풀면 `fx = Bitget` 행이 사라진다. 기록 탭 해외 선택 Bitget 에 MOCK 배지가 없고 `/history/candles?fx=bitget` 를 부른다, MEXC 는 여전히 mock.
- 수동(실서버, 개발 망이 막히면 EC2): 기동 10초 뒤 로그에 `비트겟 샤드 N` 연결 실패 경고가 없고 `/health/collect` 의 bitget 이 `ok`, `curl -s "localhost:8000/orderbook/bitget?symbol=BTC/USDT&depth=20"` 이 20단계, `/spreads` 에 `fx:"bitget"` 행이 있고 그 행의 `depFx/wdFx` 가 키 없이도 null 이 아니다. 실측해 §5 에 적을 것: symbols 본문 크기·파싱 ms, 샤드당 초당 프레임 수와 collector CPU(해외 2곳일 때와 비교), 1분 원문 객체 크기, 연결당 채널 200개 안팎에서 끊김 여부(권장 50개 미만 대비), 구독 요청 0.2초 간격이 거부되지 않는지.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# server (2026-09-15, 로컬 Mac)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check .   # All checks passed / 208 files already formatted
.venv/bin/pytest -q                                                       # 674 passed (신규 tests/test_stream_bitget.py 50 + features/wallet_status/tests/test_bitget.py 7)
# web
cd web && npm run lint && npm run build                                   # oxlint 0 / vite built (index-*.js 451KB)
# 실서버(로컬 :8020, 이 망은 이날 거래소 도메인 열림 — curl api.bitget.com 200)
curl -s localhost:8020/health/collect            # exchanges = upbit ok 255 · bithumb ok 400 · binance ok 292 · bybit ok 269 · bitget ok 310 (기동 60초 뒤), 로그에 "비트겟 샤드" 경고 0줄
curl -s "localhost:8020/orderbook/bitget?symbol=BTC/USDT&depth=20"   # asks 20 · bids 20
curl -s localhost:8020/spreads                   # rows 1463, fx ∈ {binance, bybit, bitget}, bitget 행 520 — depFx/wdFx 가 null 아닌 행 473(키 없음)
curl -s "localhost:8020/history/candles?base=BTC&fx=bitget"  # 200 · fx=mexc → 422
curl -s -X POST localhost:8020/refresh           # snapshots 5항목, bitget walletStatusAvailable true·saved 310
# web 수동(Playwright, vite :8010 → :8020): 스프레드 탭 경로 라벨 "Bitget" 99개 → Bitget 체크 해제 뒤 보이는 표에 0개(URL ?s.fxoff=Bitget), KPI "5곳 중 5곳 정상".
#   기록 탭 ?h.fx=bitget,mexc → /history/candles?fx=bitget 4회 호출, MOCK 배지 1개(MEXC 카드만).
# 실측(로컬): symbols 본문 828,074B(전 마켓 1,761 중 online·USDT 1,690), fetch 186ms, 파싱 2.9ms. 나머지(샤드당 초당 프레임·CPU·원문 객체 크기·채널 200개 끊김·0.2초 간격 거부)는 EC2 실측 대기.
```

## 6. 갱신할 문서
- `docs/context/status.md` — 행 추가 `| bitget | WS 3샤드 books(스냅샷+update 로컬 북, seq 역행 버림)+trade·symbols 매초·문자열 ping/pong 감시·샤드 단위 정체 판정·입출금(public, 키 없음)·`/history/*` `fx=bitget`·`/orderbook/bitget` | 표시명 `Bitget`·기록 탭 Bitget 실데이터 | 해외 최대 20단계, MEXC 는 mock |`. collect 행 비고 "바이낸스 스트림은 012, 바이빗은 019" → "…, 비트겟은 020". wallet-status 행 "업비트(JWT)·빗썸(public)·바이낸스(HMAC)·바이빗(HMAC 헤더)" 뒤에 "·비트겟(public)". 알려진 빚: "(019) 원문 아카이브의 바이빗 호가는 …" 항목에 비트겟도 같다고 덧붙이고, "(019) 004 analysis 는 `/orderbook/bybit` 만 …" 을 `/orderbook/bybit`·`/orderbook/bitget` 으로, 신규 "(020) 비트겟 `books` update 는 이전 seq 를 주지 않아 누락을 감지할 수 없다 — 북이 어긋나도 다음 재연결 스냅샷까지 모른다. 필요해지면 주기 재구독 별도 스펙". **항상 포함.**
- `CLAUDE.md` — 한 줄 정의의 "해외 거래소(바이낸스·바이빗)" → "(바이낸스·바이빗·비트겟)", 스펙 인덱스 020 행 → DONE. **항상 포함.**
- `docs/context/architecture.md` — 핵심 설계 결정 첫 행의 "바이빗은 orderbook.200 스냅샷+델타·publicTrade 스트림(019)" 뒤에 "비트겟은 books 스냅샷+update·trade 스트림(020)"; 데이터 흐름 mermaid 에 `BG[비트겟 WS<br/>3샤드]` 노드; "메시지 단위로 바뀐다" 행의 우주 정의 "(바이낸스 ∪ 바이빗)" → "(바이낸스 ∪ 바이빗 ∪ 비트겟)"; core 계약 절 "012 바이낸스·019 바이빗 커넥터가 각각 구현" → "012·019·020 커넥터가 각각 구현", 판정 행 "바이낸스·바이빗은 샤드 규칙(012·019)" → "바이낸스·바이빗·비트겟은 샤드 규칙(012·019·020)"; 같은 절의 "네 거래소" → "다섯 거래소"; "현재 구조" 에 bitget 항목(모듈·북·핑·판정·조회기).
- `docs/context/dev-setup.md` — `/health/collect` 기대값 "거래소 4곳(`upbit`·`bithumb`·`binance`·`bybit` 순)" → 5곳(`bitget` 마지막), 스모크에 `/orderbook/bitget … depth=20` 1줄과 "비트겟도 같다(020)" 문장. env 표는 그대로(비트겟 키 없음).
- `docs/context/product.md` — 용어 `fx` "(바이낸스 `binance`·바이빗 `bybit`)" → "(바이낸스 `binance`·바이빗 `bybit`·비트겟 `bitget`)", 기능 목록 collect "4거래소" → 5거래소, 비범위 "거래소 추가 (현재는 4거래소)" → 5거래소.
- 스펙(같은 PR 에서 문구를 고친다 — CLAUDE.md §4): `001-collect.md` §3.2 "거래소 4곳" → 5곳(§1 첫 문단 포함)·매초 목록 항목에 비트겟 `GET /api/v2/spot/public/symbols`(`status == "online"`·`quoteCoin == "USDT"`, 초당 20회 중 1회)·계약 구현 "012(바이낸스)·019(바이빗)" → "012·019·020"·우주 정의 "(바이낸스 USDT base ∪ 바이빗 USDT base)" → "(바이낸스 ∪ 바이빗 ∪ 비트겟 USDT base)"·§3.6 "매 틱 세 거래소의 행에 반영" → 다섯 거래소, `006-wallet-status.md` §1 "세 거래소의 입출금" → 다섯 거래소·§2 "(바이빗 조회기는 019 §3.6 … — 조회기 4종)" → "(바이빗은 019 §3.6, 비트겟은 020 §3.6 — 조회기 5종)"·§3.5 "60초마다 세 거래소 병렬 조회" → 다섯 거래소·§5 EC2 문장 "세 거래소 `walletStatusAvailable`" → 키 있는 세 거래소(빗썸·비트겟은 키 없이 true), `011-health.md` §3.1 "거래소 4곳(…)" → 5곳·§3.2 거래소별 규칙에 비트겟 행(§3.8)·`exchanges` 고정 순서 5곳·"4곳 평균" → 5곳·탭 "4트랙"·"카드 4장"·"4곳 중 4곳" → 5, `005-history.md` §3.4 `fx ∈ {binance, bybit}` → `{binance, bybit, bitget}`·§7 `Literal` 3값, `014-premium-1m.md` §3.1 틱 행 `fx ∈ {binance, bybit}` 와 `/history/candles` 의 `fx`(`binance|bybit|bitget`), `003-spreads.md` §3.5 표시명 목록에 `bitget→Bitget`, `013-premium-events.md` §3.1 틱 행 정의 `fx ∈ {binance, bybit, bitget}`.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `server/app/core/streams/bitget.py`(`BitgetStream` — 샤드 3개·`_Book`(seq 포함)·문자열 ping 태스크·구독 예산·symbols 조회·판정), `server/app/features/wallet_status/bitget.py`(`fetch_bitget`), `server/tests/test_stream_bitget.py`(50 테스트, `BitgetSleeps`), `server/app/features/wallet_status/tests/test_bitget.py`(7). 바꾼 것: `core/config.py`(`EXCHANGES` 5곳)·`core/contracts.py`(주석)·`main.py`(스트림 5개·`foreigns` 3개)·`features/health/service.py`(5곳)·`features/analysis/service.py`(레지스트리 `bitget`)·`features/history/router.py`(`fx` Literal 3값)·`features/wallet_status/service.py`(조회기 5종) + 기존 테스트 7파일(5곳 순서·비트겟 public 응답 라우팅). web: `shared/format.ts`(표시명)·`features/history/candles.ts`(`FX_CHOICES` Bitget 실데이터)·`Tab.tsx`·`mock.ts`(주석). 문서: §6 목록 전부 + `005-history.md` §3.4 공통 파라미터 줄의 `fx` 집합(§6 에 없던 같은 계약).
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - 구독 예산은 소켓마다 **최근 1시간 창의 요청 시각 deque** 로 센다(subscribe·unsubscribe 둘 다 1회). 요청 하나를 보낼 때마다 그 묶음의 심볼을 실제 구독 집합에 반영해, 예산에 막혀 중간에 멈춰도 보낸 만큼만 소켓에 묶인 것으로 센다. 경고는 **소진 한 번에 1줄**(60초 회차마다 반복하지 않고, 예산이 돌아왔다가 다시 소진되면 다시 1줄) — "경고 1줄" 을 이렇게 읽었다.
  - 프레임 판별에서 JSON 객체인데 `event` 도 `action`·`arg` 도 아닌 것(예 `{"foo":1}`)은 디코드 실패로 센다. 구독한 적 없는 채널(`ticker` 등)의 시세 모양 프레임은 버리되 세지 않는다. UTF-8 로 못 푸는 바이트 프레임은 텍스트가 없어 원문 싱크에도 남지 않는다(019 와 같다).
  - 체결 무시 규칙의 "오래된" 은 **엄격히 작다**(같은 ts 는 최신으로 실어 값이 갱신된다). 마지막 체결 ts 는 소켓이 아니라 base 단위로 들고, 우주에서 빠질 때 지운다.
  - `update` 의 `seq` 가 직전 이하이면 버리되 그 프레임도 원문에 남고 수신 시각에는 센다(스냅샷 전 update 와 같은 취급). 새 `snapshot` 은 seq 를 새로 시작한다.
  - 시간당 예산 판정의 시계는 커넥터에 주입된 `clock`(수신 시각과 같은 ms 시계)이다 — 테스트가 1시간을 돌릴 수 있게.
  - 핑 주기·pong 대기(30초)와 백오프 상한(30초)이 같은 값이라 가짜 sleep 이 구분 못 한다 — 백오프 테스트만 핑 상수를 20초로 패치한다(코드는 그대로).
  - 함께 고친 스펙 절: 없음(§6 목록 밖의 `005-history.md` §3.4 `fx` 집합 한 줄만 같은 계약이라 함께 고쳤다).
- 남은 빚:
  - **symbols 본문이 매초 828KB** 다(파라미터로 줄일 수 없다 — `symbol` 하나만 받는다). 하루 약 70GB 수신, 파싱은 3ms 라 CPU 보다 대역폭·거래소 예의 문제. 매초 규칙(001 §3.2)을 비트겟만 늦추거나(예 60초) `ETag`/`If-None-Match` 지원 여부를 확인하는 별도 결정이 필요하다 — 이 세션은 스펙대로 매초로 두었다.
  - EC2 실측 대기(§5): 샤드당 초당 프레임 수와 collector CPU(해외 2곳일 때와 비교 — 019 뒤 이미 85~100%), 1분 원문 객체 크기, 연결당 채널 200개 안팎(우주 ~310 심볼 × 2채널 / 3샤드)에서 끊김 여부, 구독 요청 0.2초 간격 거부 여부, 예산 경고가 실제로 뜨는지.
  - 원문 아카이브의 비트겟 호가는 update 표본이라 재생 불가(status.md 에 적음). `books` update 누락 감지 불가(status.md).
  - 004 analysis 나머지 5개 API 의 해외는 여전히 바이낸스 고정.
  - 커밋 300줄 규칙: 커넥터(`bitget.py` ≈ 600줄)와 그 테스트(≈ 1,000줄)는 한 파일이라 쪼개지 못했다 — PR 본문에 명시.
