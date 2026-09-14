# 019 — bybit

상태: DONE | 의존: 001(collect — 행 계약·마켓 우주·틱 판정·원문 싱크·커넥터 공통 규칙), 006(wallet-status — 입출금 조회기 계약·망 판정), 011(health — 실패 분류·구간 추적), 012(binance-stream — 샤드·판정 규칙의 원형), 005·014(`/history/*` 의 `fx` 파라미터)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
해외 거래소 두 번째로 **바이빗(`bybit`) USDT 현물**을 붙인다. 끝나면 스프레드 표에 `fx = bybit` 행이 생겨 트레이더가 같은 코인의 바이낸스·바이빗 김프를 나란히 비교하고, 바이빗 입출금 상태까지 표에서 본다. 기록 탭의 해외 선택지 Bybit 가 시안(mock)에서 실데이터로 바뀐다.
바이낸스처럼 시세는 WebSocket 만, REST 는 심볼 목록(매초)과 입출금 상태(60초)에만 쓴다. 커넥터는 012 와 **코드를 공유하지 않고** 규칙만 같다(architecture.md 원칙).

## 2. 범위
- 만드는 것: 바이빗 스트림 커넥터(샤드 3개·구독 재조정·호가 북·핑·재연결·정체 판정), 심볼 목록 REST 조회, 바이빗 입출금 조회기(HMAC), 테스트. 라이브러리 추가 없음(`websockets`·`httpx` 기존).
- 바꾸는 기존 것:
  1. **마켓 우주(001 §3.2)** — 해외 심볼 원천이 여러 개가 된다. 우주 = `(업비트 KRW base ∪ 빗썸 KRW base) ∩ (바이낸스 USDT base ∪ 바이빗 USDT base)`. 우주가 확정될 때마다 해외 원천 **각각**에 `set_universe(우주)` 를 넘기고, 자기 맵에 없는 base 는 그 커넥터가 무시한다(012 §3.3 규칙 그대로). 한쪽 해외에만 있는 코인은 그쪽 행만 생긴다 — 003 의 페어 규칙("양쪽 모두 상장된 코인만 행")이 그대로 처리한다.
  2. **거래소 목록 4곳** `upbit·bithumb·binance·bybit` 고정 순서 — 틱 판정(011), `/health/collect` 의 `exchanges[]`, `/refresh` 의 `snapshots[]`, 입출금 반영 순서 전부.
  3. **입출금(006)** — 조회기 4종. env 키 `BYBIT_API_KEY`·`BYBIT_SECRET_KEY`(없으면 바이빗 전 코인 `unknown` + 경고, 앱은 뜬다).
  4. **`/history/premium`·`streaks`·`streaks/bulk`·`candles`** 의 `fx` ∈ {`binance`, `bybit`}(기본 `binance` 그대로). 그 밖의 값은 지금처럼 422. `/history/events` 는 `fx` 필터가 없어 바이빗 사건이 함께 나오고 응답 행의 `fx` 값으로 구분된다.
  5. **web** — 표시명 `bybit → Bybit`(스프레드 표의 "비교 해외 거래소" Bybit 체크박스가 이미 있어 그것과 맞는다), 기록 탭 해외 선택지 Bybit 를 mock 에서 실데이터로(MEXC 는 mock 유지). 실데이터 해외 거래소의 봉은 **선택된 것만** 부르고, mock(MEXC) 이 선택되면 재료인 binance 봉도 부른다.
- 바꾸는 기존 것(작은 것): 004 의 거래소 레지스트리에 `bybit`(표시명 `Bybit`)을 더해 `/orderbook/bybit`·`/slippage/bybit` 가 열린다 — §4 수동 확인이 이 경로를 쓴다.
- 하지 않는 것: 004 analysis 의 나머지 4개 API(`/premium`·`scan`·`matrix`·`/arbitrage`)의 해외 선택(바이낸스 고정 유지 — 빚으로 남긴다). 백필 스크립트(binance 만). `/spreads` 행 계약(키 17개 그대로 — bybit 행은 `fx` 값만 다르다). 사건(013)·1분봉(014)·틱 저장(009)·원문 아카이브(010)·표 푸시(017) — 전부 거래소 id 나 틱 행 단위로 돌아 코드 변경 없이 bybit 가 흘러든다(§3.7). 선물·Bybit 파생.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001 §3.3: 행 = `(exchange, base)` 당 `quote`·`native_symbol`·`price`·`price_timestamp`·`asks`·`bids`(누적 1,000,000 USDT 도달 단계까지, 최소 1단계, 잔량 ≤0 제거)·입출금 3필드(물려받음)·`updated_at`. 거래소별 스트림 상태 `{connected, last_message_at, last_error, subscribed, url, connected_since}`.
- 001 §3.2 + 이 스펙 §2-1: 해외 심볼 원천 계약 `refresh(client) -> int`(REST 1회, 실패는 거래소 예외) / `bases() -> set[str]` / `set_universe(bases)`(매초·`/refresh`·기동, 배정이 같으면 무동작).
- 001 §3.7: 받은 모든 프레임과 REST 본문은 원문 싱크 `record(exchange, source, received_at_ms, payload, key)` 로 — 행·상태 갱신 전에, 텍스트 그대로. 시세 프레임은 `key` = `orderbook:<심볼>`·`trade:<심볼>`(대문자 원본 심볼), 매초 오는 심볼 목록 본문은 `symbols:all`, 구독·핑 응답·깨진 프레임·핸드셰이크 거부 본문·입출금 본문은 `key=None`. 010 이 `key` 있는 줄을 분당 마지막 1건으로 솎는다 — 델타 프레임도 같은 규칙이라 **아카이브로 북을 재생할 수는 없다**(빚, §6 status.md).
- 011 §3.2: 실패 8종 `timeout network rate_limit banned unavailable bad_request bad_response stale_stream`. 매 틱 거래소마다 판정해 추적기에 넘긴다.
- 006 §3.1~3.2: 입출금 조회기는 `refresh_if_due(client, force=False)`(60초 지났으면 조회, 호출 수 반환) / `apply(rows, exchange)` / `availability()` / `warnings()` / `failed()`. 코인 상태는 `open / closed / unknown` 3상태, **`unknown` 을 열림으로 읽지 않는다**. 망 = `{code, name, dep, wd}`, 실패 회차는 직전 값을 지키지 않고 `unknown` 으로 덮는다. 망 판정(국내 망 기준·정규화·동일 체인 쌍)은 006 §3.6~3.7 을 그대로 쓴다.

### 3.2 스트림
- 엔드포인트 `wss://stream.bybit.com/v5/public/spot`. 구독 `{"req_id":"<n>","op":"subscribe","args":[…]}`, 해지는 `op:"unsubscribe"`. **한 요청의 `args` 는 10개 이하**(공식 문서: "Spot can input up to 10 args for each subscription request sent to one connection"), 한 연결의 `args` 문자열 총길이 21,000자 이하. 응답 `{"success":true,"ret_msg":"subscribe","conn_id":…,"req_id":…,"op":"subscribe"}` 은 시세로 세지 않는다. `success:false` 응답은 `bad_request` 로 실패하고 재연결한다(문서에 에러 예시가 없다 — `ret_msg` 를 message 에).
- 심볼마다 두 토픽. **`orderbook.200.<SYMBOL>`** — 구독 직후 `type:"snapshot"`(전체 북), 이후 바뀐 단계만 `type:"delta"` 로 최대 100ms 마다. 페이로드 `{"topic":…,"type":…,"ts":<ms>,"data":{"s":"BTCUSDT","b":[["<가격>","<잔량>"],…],"a":[…],"u":<update id>,"seq":…},"cts":<ms>}` — 가격·잔량 문자열, `b` 내림차순·`a` 오름차순. **`publicTrade.<SYMBOL>`** — 체결 즉시 `{"topic":…,"type":"snapshot","ts":<ms>,"data":[{"T":<체결 ms>,"s":…,"S":"Buy|Sell","v":"<수량>","p":"<가격>","i":…},…]}`, 한 프레임에 최대 1,024건.
- 왜 200단계·publicTrade 인가: 현물 호가 토픽은 1·50·200·1000 단계뿐이고 1단계는 슬리피지(003)에 못 쓴다. 50단계는 최대 20ms(초당 50개)·200단계는 100ms(초당 10개)라 활발한 종목에서 **200 이 메시지 수가 1/5** 이다 — 북은 200단계를 들고 행에는 001 규칙으로 잘라 넣는다. 체결가는 `tickers`(현물 50ms 스냅샷, 초당 최대 20개) 대신 체결이 있을 때만 오는 `publicTrade` 를 쓴다 — 조용한 종목은 0건이고 바이낸스 miniTicker 와 의미(마지막 체결가·체결 시각)가 같다.
- **핑**: 클라이언트가 20초마다 `{"op":"ping"}` 을 보낸다(문서 요구). 응답 `{"success":true,"ret_msg":"pong","op":"ping",…}` 은 시세가 아니다(`key=None`). **보낸 핑의 pong 이 20초 안에 안 오면 끊고 재연결**한다 — 그 샤드의 마지막 오류는 `timeout`(message 에 "pong 없음")이다(조용히 죽은 TCP 감지 — 바이낸스의 라이브러리 keepalive 와 같은 역할. WebSocket 제어 프레임 ping 에 대한 서버 응답은 문서에 없어 쓰지 않는다). 핑·데이터가 10분 없으면 서버가 끊는다. 24시간 강제 종료는 문서에 없다.
- 시세가 아닌 프레임(구독·핑 응답, 깨진 JSON, 맵에 없는 심볼)은 원문 싱크에만 남기고 시세 수신으로 세지 않는다. 응답 프레임의 판별은 `success` 키다 — `op` 가 `ping`/`pong` 이면 pong, `success:false` 면 `op` 와 무관하게 구독 거부. `orderbook` 프레임의 `type` 이 `snapshot`·`delta` 둘 다 아니면 디코드 실패로 센다.
- 연결 한도: 도메인당 5분에 500 연결, IP 당 시세 연결 1,000. 재연결은 지수 백오프 1·2·4…30초, **구독 뒤 그 샤드의 첫 시세 프레임**에서 1초로 리셋(001·012 와 같다). 핸드셰이크 HTTP 거부는 §3.8 규칙.

### 3.3 심볼 목록과 샤딩
- 심볼 목록: `GET https://api.bybit.com/v5/market/instruments-info?category=spot&status=Trading` → `result.list[]` 중 `status == "Trading"` 이고 `quoteCoin == "USDT"` 인 것. base = `baseCoin`, 심볼 = `symbol`(예 `BTCUSDT`). 현물은 페이지네이션이 없다(`limit`·`cursor` 무시). 응답 봉투 `{"retCode":0,"retMsg":"OK","result":{…},"time":…}` — **HTTP 200 이어도 `retCode != 0` 이면 실패**(§3.8). **매초** 갱신(001 §3.2 — IP 한도 5초에 600회의 0.8%), 본문은 질의를 뺀 `rest:/v5/market/instruments-info` 로 원문 싱크(`symbols:all`). 실패 시 직전 목록 유지 + 경고. 파싱 결과가 같으면 아무 일도 하지 않는다. 본문 크기·파싱 시간은 미확인(EC2 실측 항목).
- base 하나에 USDT 심볼이 둘 이상이면 처음 것. 우주 base 중 맵에 없는 것은 구독 대상이 아니다. `USDT` 자신은 우주에 없다(001).
- **3 샤드**, 배정 = `crc32(심볼) % 3`, 한 심볼의 두 토픽은 같은 샤드 — 012 §3.3 과 같은 규칙. 이유가 하나 더 있다: 한 연결의 `args` 총길이 21,000자 한도에 심볼당 약 45자(`orderbook.200.XXXUSDT`+`publicTrade.XXXUSDT`)라 **한 소켓은 약 460 심볼**이 상한인데 우주가 그 근처까지 갈 수 있다.
- 재조정 규칙은 012 §3.3 그대로: 우주 확정마다 `set_universe`, 빠진 심볼의 행은 그 자리에서 삭제, 연결된 샤드마다 원하는 구독과 실제 구독의 **차이만** 전송, 실제 구독 집합은 소켓에 묶인다, 60초마다 한 번 자동 재조정, 배정 0 샤드는 연결하지 않는다. 다른 점은 한 요청 `args` **10개 이하**이고, 요청 사이 **0.1초** 를 쉰다(요청 빈도 한도는 문서에 없다 — 미확인이라 보수값. 배정 150 심볼이면 30요청·3초).

### 3.4 호가 북과 행 갱신
- 심볼마다 **로컬 북**(가격 → 잔량, 매도·매수 각각)을 둔다. `snapshot` → 북을 통째로 교체. `delta` → 잔량 `0` 은 그 가격 삭제, 없는 가격은 삽입, 있으면 잔량 교체(공식 문서 규칙). `u == 1` 은 서비스 재시작 스냅샷이므로 교체(문서상 `type` 도 snapshot 이다). 재연결하면 새 스냅샷이 오므로 소켓이 바뀔 때 그 샤드의 북을 전부 비운다 — 옛 북에 새 델타를 얹지 않는다. **스냅샷 전에 온 델타는 버린다**(북이 없으므로) — 다만 거래소가 보낸 시세 프레임이므로 수신 시각(`last_message_at`)에는 센다.
- 매 호가 메시지 뒤 그 심볼의 행을 다시 만든다: `asks` 오름차순·`bids` 내림차순으로 정렬해 001 규칙(잔량 ≤0 제거·누적 1,000,000 USDT 까지·최소 1단계)으로 자른 것, `updated_at` = 수신 시각, 호가 시각 = 프레임 `ts`. 한쪽이 비면 행을 저장하지 않는다(있던 행은 지운다). 메시지마다 만드는 이유는 001 의 "행은 메시지로만 바뀐다" 를 지키기 위해서다 — 초당 상한 10개/심볼이라 우주 300 종목에 최대 3,000회/초, 실제 비용은 EC2 실측 항목.
- `publicTrade` → `data[]` 중 **`T` 가 가장 큰 원소**의 `p` 가 `price`, 그 `T` 가 `price_timestamp`(배열 순서를 믿지 않는다). 행이 없으면 보류했다가 호가가 오면 함께 싣는다. 체결가가 없으면 mid — 이때 `price_timestamp` 는 수신 시각(012 와 같다).
- 캐시 키·`native_symbol` 은 원본 심볼 대문자(`BTCUSDT`). 토픽의 심볼이 맵에 없으면 그 프레임은 버린다. 입출금 3필드 물려받기·우주 밖 버리기는 001 §3.5.

### 3.5 정체 판정 (매 틱, 샤드 단위)
012 §3.5 와 같다 — 샤드마다 `last_message_at`·배정 수·구독 시각(첫 구독 묶음을 다 보낸 시각), 조용한 시간 = 지금 − max(마지막 시세, 연결 중이면 구독 시각), 배정 0 샤드는 판정 대상 아님, 연결됐는데 **30초** 무수신 → `stale_stream`, 미연결 → `last_error.kind`, 여러 샤드가 나쁘면 가장 오래 조용한 샤드(동률이면 작은 번호), 전부 판정 없음이면 없음. pong 만 오고 시세가 없어도 30초면 정체다(pong 은 시세가 아니다). 실패 `message` = `"바이빗 스트림 정체: 샤드 2 (구독 7종목) 30초 이상 무수신"`, `url` = WS URL. `store.stream("bybit")` 집계도 012 §3.5 와 같다.

### 3.6 입출금 상태 (006 의 네 번째 조회기)
- `GET https://api.bybit.com/v5/asset/coin/query-info`(파라미터 없음 = 전 코인). 인증 헤더 `X-BAPI-API-KEY`·`X-BAPI-TIMESTAMP`(UTC ms)·`X-BAPI-RECV-WINDOW`(`10000`)·`X-BAPI-SIGN`. 서명 = `HMAC-SHA256(secret, timestamp + api_key + recv_window + queryString)` 의 **소문자 hex**, GET 이고 질의가 없으므로 queryString 은 빈 문자열. 타임스탬프 조건 `server_time − recv_window ≤ timestamp < server_time + 1000`. 한도 5회/초(60초 주기라 무관). 키 검사·메시지는 006 바이낸스와 같은 방식: `BYBIT_API_KEY / BYBIT_SECRET_KEY 가 비어 있습니다.`
- 응답 `result.rows[]` 의 `coin`·`chains[]`. 망 = `code = chain`(예 `ETH`·`MANTLE`), `name = chainType`(예 `Ethereum`·`Mantle Network`), `dep = (chainDeposit == "1")`, `wd = (chainWithdraw == "1")`("0" 중단·"1" 정상). `chains` 가 빈 코인은 결과에 없다(바이낸스 `networkList` 빈 코인과 같다). 코인 단위 dep/wd = 한 망이라도 열려 있으면 ok. `withdrawFee` 가 빈 문자열이면 문서상 "출금 미지원"이지만 판정은 `chainWithdraw` 만 본다(두 필드가 어긋난 실례는 미확인 — 어긋나면 원문 본문으로 확인해 규칙을 정한다).
- HTTP 200 + `retCode != 0` 은 실패(§3.8). 실패 회차 처리·`/refresh` 경고·`walletStatusAvailable` 은 006 §3.4 그대로. 본문은 `rest:/v5/asset/coin/query-info` 로 원문 싱크(`key=None`).

### 3.7 다른 기능에 흘러드는 것 (코드 변경 없이)
- 틱(001 §3.6-2)은 국내×해외 **전 조합**이라 `(upbit|bithumb) × bybit` 행이 생긴다 → 003 표·017 푸시·013 사건·014 1분봉·009 Influx `premium` 에 `fx=bybit` 태그 값으로 들어간다. Influx 스키마(db.md)는 그대로고 카디널리티만 대략 2배.
- 010 은 `raw/exchange=bybit/…` 객체를 자동으로 갖는다. 011 추적기는 거래소 문자열 키라 자동.

### 3.8 실패 분류 — 바이빗 REST·핸드셰이크 규칙 (011 §3.2 에 추가하는 행)
- HTTP 403 → `banned`(문서: "access too frequent", 10분 이상 자동 해제). 429 → `rate_limit`. 5xx → `unavailable`. 그 외 4xx → `bad_request`. `Retry-After` 는 문서에 없으니 `retry_after_sec` 는 null.
- HTTP 200 + `retCode != 0`: `retCode == 10006`("Too many visits!") → `rate_limit`, 그 밖은 `bad_response` 에 원문 body(미확인 코드는 body 를 보며 표를 채운다 — 011 원칙). `status_code` 는 200.
- 공통(REST): httpx 타임아웃 → `timeout`, 전송 예외 → `network`, JSON 아님·`result.list`/`rows` 없음 → `bad_response`. WebSocket: 연결 실패 → `network`, 핸드셰이크 타임아웃 5초 → `timeout`, HTTP 거부는 위 규칙, 구독 `success:false` → `bad_request`.

## 4. 검증
네트워크 없음 — 가짜 소켓·가짜 REST·가짜 원문 싱크(001 의 fakes 재사용).
- snapshot → 행의 `asks` 오름차순·`bids` 내림차순 float, 누적 1,000,000 USDT 에서 잘리고 첫 단계가 넘어도 1단계는 남는다. `native_symbol` 은 `BTCUSDT`.
- delta: 잔량 0 은 삭제, 새 가격 삽입, 기존 가격 교체 → 행이 그 결과로 다시 만들어진다. 스냅샷 전 델타는 무시. 새 snapshot(또는 `u=1`)은 북을 통째로 교체. 재연결 뒤 옛 북에 델타가 얹히지 않는다(새 소켓의 첫 델타는 스냅샷 전이므로 버려진다).
- publicTrade 3건 중 `T` 가 가장 큰 것이 `price`·`price_timestamp`. 호가 전에 오면 보류, 호가 뒤에 실린다. 체결가 없으면 mid 이고 `price_timestamp` 는 수신 시각.
- instruments-info 에서 `Trading`·`USDT` 만 심볼 집합에 든다. 요청 URL 에 `category=spot&status=Trading`, 원문 source 는 `rest:/v5/market/instruments-info`, key `symbols:all`. `retCode != 0` 본문(HTTP 200)은 실패이고 직전 목록 유지.
- 우주 = 국내 합집합 ∩ (바이낸스 ∪ 바이빗). 바이빗에만 있는 base 는 바이빗 행만, 바이낸스에만 있는 base 는 바이낸스 행만 생기고, 각 커넥터는 자기 맵에 없는 base 를 구독하지 않는다. 틱에 `(upbit, bybit, BTC)`·`(bithumb, bybit, BTC)` 행이 나온다.
- 심볼 300개 → 3 샤드, 같은 심볼은 항상 같은 샤드(해시 안정성 — 서브프로세스 2회), 두 토픽 같은 샤드. 구독 요청 `args` ≤ 10, 요청 사이 0.1초. 재조정: 새 심볼 구독·빠진 심볼 해지·행 삭제, 같은 우주 재수신은 전송 0.
- 핑: 20초마다 `{"op":"ping"}`, pong 은 시세가 아니고 `key=None`. pong 이 20초 안에 없으면 재연결.
- 정체: 샤드 2만 30초 무수신 → `stale_stream`, message 에 "샤드 2". pong 만 오는 샤드도 30초면 정체. 미연결 샤드 → `last_error.kind`. 둘이 나쁘면 더 오래 조용한 쪽.
- 원문: 모든 프레임이 원문 그대로 기록 — 호가·체결 프레임은 `orderbook:BTCUSDT`·`trade:BTCUSDT`, 심볼 목록은 `symbols:all`, 구독·핑 응답·핸드셰이크 거부·입출금 본문은 `key=None`. 행 갱신 전에 기록된다.
- 분류: 403 → `banned`, 429 → `rate_limit`, 200+`retCode 10006` → `rate_limit`(status_code 200), 200+그 외 retCode → `bad_response`, 구독 `success:false` → `bad_request`. 백오프 1·2·4…30, 구독 뒤 첫 시세에서 1.
- 입출금: 가짜 secret 으로 `timestamp+api_key+10000` 의 HMAC-SHA256 hex 를 직접 계산한 값과 `X-BAPI-SIGN` 이 같고 4개 헤더가 붙는다. `chainDeposit "1"/"0"` → `dep true/false`, 망 `code=chain`·`name=chainType`, `chains` 빈 코인은 결과에 없음, 한 망만 열려도 코인 dep ok. 키 없음 → 호출 0회 실패·경고 1줄·`walletStatusAvailable` false. `retCode != 0` → 실패·전 코인 `unknown`.
- `/health/collect` 의 `exchanges` 가 4곳 순서 `upbit bithumb binance bybit`. `/refresh` 의 `snapshots` 4항목. `/history/candles?fx=bybit` 는 200, `fx=mexc` 는 422.
- 연결 실패 기동(lifespan 실제, 소켓·REST 가짜) → `/health` 200, 샤드마다 경고 1줄, 바이낸스 행은 계속 갱신된다. 종료 시 태스크 취소·소켓 close, 잔여 예외 없음.
- web: `npm run lint && npm run build`. 스프레드 표에서 Bybit 체크를 풀면 `fx = Bybit` 행이 사라진다. 기록 탭 해외 선택 Bybit 에 MOCK 배지가 없고 `/history/candles?fx=bybit` 를 부른다, MEXC 는 여전히 mock.
- 수동(실서버, 개발 망이 막히면 EC2): 기동 10초 뒤 로그에 `바이빗 샤드 N` 연결 실패 경고가 없고 `/health/collect` 의 bybit 가 `ok`, `curl -s "localhost:8000/orderbook/bybit?symbol=BTC/USDT&depth=20"` 이 20단계, `/spreads` 에 `fx:"bybit"` 행, 키를 넣으면 bybit 행의 `depFx/wdFx` 가 null 이 아니다. 실측해 §5 에 적을 것: instruments-info 본문 크기·파싱 ms, 샤드당 초당 프레임 수와 collector CPU(바이낸스만일 때와 비교), 1분 원문 객체 크기.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q
# All checks passed! / 204 files left unchanged / 611 passed, 2 warnings in 7.6s  (2026-09-14 — 이 스펙의 tests/test_stream_bybit.py 46개 + features/wallet_status/tests/test_bybit.py 7개 + test_universe(해외 합집합)·test_candles_api(fx=bybit)·test_orderbook_api(bybit 레지스트리) 각 1개, 거래소 4곳으로 바뀐 기존 기대값 갱신 포함)
cd web && npm run lint && npm run build
# oxlint 문제 0 / tsc·vite built (index-*.js 451KB)

# 실서버 스모크 — 2026-09-14 로컬(거래소 4곳 도메인 열림), 빈 포트 8042, .env 없음(입출금 키 없음), 끝나고 kill
.venv/bin/uvicorn app.main:app --port 8042
curl -s localhost:8042/health                                        # {"status":"ok","version":"0.1.0"}
curl -s localhost:8042/health/collect                                # 기동 20초 뒤: upbit ok 240 · bithumb ok 367 · binance ok 289 · bybit ok 269, outages [] — 2분 뒤에도 4곳 ok·successRate1h 100.0
curl -s "localhost:8042/orderbook/bybit?symbol=BTC/USDT&depth=20"    # asks 20 · bids 20 · quote USDT
curl -s "localhost:8042/slippage/bybit?symbol=BTC/USDT&amount=1000"  # exchange bybit · depthAvailable 153(누적 1,000,000 USDT 까지 저장된 단계)
curl -s localhost:8042/spreads                                       # rows 936 · fx=bybit 455행(dom 은 upbit·bithumb 둘 다) · fx=binance 484행 · warnings [] · bybit 행 status 전부 ok, age 0
# 로그: 바이빗 샤드 연결 실패 경고·트레이스백 없음(키 없음 입출금 경고 3줄만). SIGTERM 뒤 포트 해제
```
EC2 에서 확인 필요(로컬에서 재현 불가·미측정): 네트워크를 끊고 30초 뒤 `/health/collect` 의 bybit 가 `stale_stream`(message 에 샤드 번호)이고 복구하면 닫히는지, pong 없음 재연결이 실제로 일어나는지, instruments-info 본문 크기·파싱 ms, 샤드당 초당 프레임 수와 collector CPU(바이낸스만일 때와 비교), 1분 원문 객체 크기, 구독 요청 0.1초 간격이 거부되지 않는지, 실키로 bybit 입출금 `depFx/wdFx` 가 채워지는지.

## 6. 갱신할 문서
- `docs/context/status.md` — 행 추가 `| bybit | WS 3샤드 orderbook.200(스냅샷+델타 로컬 북)+publicTrade·instruments-info 매초·샤드 단위 정체 판정·입출금(HMAC) | 표시명·기록 탭 Bybit 실데이터 | 해외 최대 20단계, MEXC 는 mock |`. collect 행 비고 `바이낸스 스트림은 012, 바이빗은 019`. wallet-status 행 "3거래소" → 4거래소. 알려진 빚 2건 추가: "(019) 원문 아카이브의 바이빗 호가는 델타 프레임을 분당 1건 표본화한 것이라 북을 재생할 수 없다 — 재생이 필요해지면 스냅샷을 따로 남기는 별도 스펙", "(019) 004 analysis 는 해외가 바이낸스 고정 — `fx` 선택은 후속". **항상 포함.**
- `CLAUDE.md` — 한 줄 정의의 "해외 거래소(바이낸스)" → "(바이낸스·바이빗)", 스펙 인덱스 019 행 → DONE. **항상 포함.**
- `docs/context/architecture.md` — 핵심 설계 결정 1행 "바이낸스는 depth20·miniTicker 스트림(012)" 뒤에 "바이빗은 orderbook.200 스냅샷+델타·publicTrade(019)" 와 "세 거래소" → "네 거래소"; 데이터 흐름 mermaid 에 `BY[바이빗 WS<br/>3샤드]` 노드; "메시지 단위로 바뀐다" 행의 우주 정의 "국내 KRW ∩ 바이낸스 USDT" → "국내 KRW ∩ (바이낸스 ∪ 바이빗) USDT"; core 계약 절 "바이낸스 USDT 현물 심볼 집합" → "해외 USDT 현물 심볼 집합(012·019 커넥터가 각각 구현, 우주는 목록으로 받는다)", 판정 행 "바이낸스는 012 샤드 규칙" → "바이낸스·바이빗은 샤드 규칙(012·019)"; "현재 구조" 에 bybit 항목(모듈·북·판정·조회기).
- `docs/context/dev-setup.md` — env 표에 `BYBIT_API_KEY`·`BYBIT_SECRET_KEY` 2행("없음"), `/health/collect` 기대값 "거래소 3곳" → 4곳(`bybit` 마지막), 스모크에 `/orderbook/bybit … depth=20` 1줄.
- `docs/context/product.md` — 용어 `fx` "해외 거래소(바이낸스 `binance`)" → "(바이낸스 `binance`·바이빗 `bybit`)", 기능 목록 collect "3거래소" → 4거래소, 비범위 "거래소 추가 (현재는 3거래소)" → 4거래소.
- `server/.env.example` — 입출금 키 절에 `BYBIT_API_KEY=`·`BYBIT_SECRET_KEY=`.
- 스펙(같은 PR 에서 문구를 고친다 — CLAUDE.md §4): `001-collect.md` §3.2 우주 정의·"거래소 3곳"·해외 심볼 계약 문장(원천 여러 개), `006-wallet-status.md` 조회기 "3종" → 4종과 §3.3 다음에 바이빗 절(이 스펙 §3.6 요약 + 링크), `011-health.md` §3.1 "거래소 3곳" → 4곳·§3.2 거래소별 규칙에 바이빗 행(§3.8)·`exchanges` 고정 순서 4곳·탭 "3트랙" → 4트랙, `005-history.md` §3.4 "`fx` = binance 고정" → `fx ∈ {binance, bybit}`(기본 binance)·§7 `Literal`, `014-premium-1m.md` `/history/candles` 의 `fx`(값 `binance|bybit`)·틱 행 `fx = binance` → `fx ∈ {binance, bybit}`, `003-spreads.md` §3.5 표시명 목록에 `bybit→Bybit`, `013-premium-events.md` §3.1 틱 행 정의 `fx = binance` → `fx ∈ {binance, bybit}`.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `server/app/core/streams/bybit.py`(`BybitStream`·`shard_of`·`_Book` — 샤드 3개·재조정 루프·샤드별 핑 태스크·로컬 북, instruments-info 로 `ForeignSymbolSource` 구현), `server/tests/test_stream_bybit.py`(북·체결·심볼 목록·샤딩·구독 한도·핑/pong·판정·원문 key·분류·백오프·lifespan 기동·종료, `BybitSleeps`·`ClosableGatedSocket`), `server/app/features/wallet_status/bybit.py`(`fetch_bybit`)·`tests/test_bybit.py`. 바꾼 것: `core/contracts.py`(`ForeignSymbolSource.id`·docstring, `NoForeignSymbols.id`), `core/universe.py`(`foreigns` 목록·합집합·거래소별 실패 보고), `core/config.py`(`EXCHANGES` 4곳·바이빗 키), `main.py`(커넥터·`foreigns`·입출금 키 배선), `features/wallet_status/service.py`(조회기 4종), `features/health/service.py`(4곳), `features/history/router.py`(`fx` Literal 2값), `features/analysis/service.py`(레지스트리 bybit), `.env.example`, 기존 테스트 7파일의 4곳 기대값, `web/src/shared/format.ts`·`features/history/{{candles.ts,mock.ts,Tab.tsx}}`, `docs/context/*` 5개·스펙 001/003/005/006/011/013/014·`CLAUDE.md`, 이 스펙.
- 추측한 지점 (묻지 않고 정한 것 — 전부 §2·§3 에 확정 문구로 적었다):
  1. 해외 심볼 원천 계약에 `id` 를 더했다 — 우주가 목록 실패를 거래소별로 보고하려면 이름이 필요해서(§2-1, architecture 계약 절). 테스트용 `NoForeignSymbols` 의 id 는 `binance`.
  2. 스냅샷 전 델타는 북에 얹지 않지만 수신 시각에는 센다 — 거래소가 보낸 시세 프레임이라 정체 판정에서 살아 있는 연결로 본다(§3.4).
  3. pong 없음으로 끊은 연결의 실패 종류는 `timeout`(§3.2). 핑 태스크는 소켓을 닫기만 하고 재연결은 펌프의 끊김 처리가 한다.
  4. 응답 프레임 판별 = `success` 키, pong = `op` ping/pong, `success:false` = 구독 거부(§3.2). 알 수 없는 `orderbook.type` 은 디코드 실패(§3.2).
  5. 004 레지스트리에 bybit 를 더해 `/orderbook/bybit`·`/slippage/bybit` 를 열었다 — §4 수동 확인이 그 경로를 쓰는데 스펙이 004 를 "하지 않는 것"에 두어 어긋났다(§2). `/arbitrage` 는 이미 메모리 전 행을 보므로 bybit 행이 후보에 든다(표시명만 맞췄다).
  6. 기록 탭은 실데이터 해외를 선택된 것만 부르고 MEXC(mock) 선택 시 binance 도 부른다(§2-5) — 이전 코드는 "실데이터 해외 = binance 하나" 를 전제로 항상 그것만 불렀다.
  7. 북의 가격 키는 float — 바이빗 가격 문자열은 tickSize 자리수로 고정돼 같은 가격은 같은 float 이다. 정렬은 매 메시지 전체 정렬(200단계) — EC2 CPU 실측 뒤 문제면 정렬 컨테이너로 바꾼다.
  8. `instruments-info` 응답이 객체가 아니거나 `result.list` 가 없으면 `bad_response`(§3.8 공통 규칙).
  9. 라이브러리 keepalive 는 `ping_interval=None` 으로 껐다(§3.2 — JSON ping 만).
  10. 테스트의 가짜 sleep 은 핑 주기·pong 대기 값을 표(세마포어)로 막는다 — 즉시 돌아오는 가짜면 핑 루프가 폭주한다.
- 실행 중 함께 고친 스펙 절: §2(004 레지스트리 항목, 기록 탭 조회 범위), §3.2(pong 없음 → `timeout`, 응답 프레임 판별), §3.4(스냅샷 전 델타의 수신 시각).
- 남은 빚:
  - EC2 확인 항목(§5) 전부 — 특히 collector CPU(매 메시지 200단계 정렬)와 구독 요청 간격.
  - 원문 아카이브의 바이빗 호가는 델타 표본이라 재생 불가(status.md 빚).
  - 004 의 `/premium`·`scan`·`matrix`·`/arbitrage` 해외 선택은 바이낸스 고정(status.md 빚).
  - 다른 세션이 같은 시각에 018(spreads-serve)을 쓰고 있어 이 작업은 별도 워크트리(`feat/019-bybit`)에서 했다 — CLAUDE.md 인덱스는 018 행이 머지된 뒤 순서만 맞추면 된다.
