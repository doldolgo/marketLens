# 046 — perp-collect

상태: IN_PROGRESS | 의존: 001(collect — 마켓 우주·원문 싱크·틱 판정·커넥터 공통 규칙), 011(health — 실패 분류·구간 추적·수집 상태 탭), 012·019·020(binance-stream·bybit·bitget — 샤드·재조정·핑·정체 판정의 원형), 010(raw-archive — 원문 키 규칙)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
해외 거래소의 **USDT 무기한 선물(perp)** 최우선 호가·마크가·펀딩을 메모리에 들인다. 바이낸스·바이빗·비트겟 세 곳이 대상이고(Hyperliquid 는 047), 끝나면 수집 상태 탭에 perp 원천 3개가 실데이터 카드로 생기고 현선갭(048)·선선갭(049) 표가 이 행을 읽는다. 이 스펙만으로 화면에 새 숫자는 없다(수집 상태 카드뿐).
바이낸스는 현물과 같은 원칙(시세는 WebSocket, REST 는 심볼 목록·펀딩 주기)이고, 바이빗·비트겟은 **REST 전체 티커를 매초 1회** 받는다(2026-10-08 사람 결정 — 심볼별 WebSocket 티커는 바이빗 100ms delta·비트겟 ticker 가 합쳐 초당 약 8,400 프레임이라 수집기 CPU 를 두 배로 만들었다, §5 실측. 갭 표는 1초 틱이라 호가가 최대 1초 늦는 것은 손해가 아니다). 커넥터는 012·019·020 과 **코드를 공유하지 않고** 규칙만 같다. 공식 문서와 실호출은 2026-10-08 에 확인했다. 인증 없음 — 새 env 키 없다.

## 2. 범위
- 만드는 것: 바이낸스 perp 스트림 커넥터(샤드·구독 재조정·재연결·정체 판정), 바이빗·비트겟 perp 티커 폴링 원천 2개(매초 REST·실패 분류·정체 판정), perp 심볼 목록·펀딩 주기 REST 조회, perp 행 저장소(LiveStore 안의 별도 맵)와 **perp 우주**, 배수 심볼 정규화 공개 함수, 테스트. 라이브러리 추가 없음.
- 바꾸는 기존 것: 거래소 목록이 현물 5곳(045 뒤 6곳) 뒤에 perp 원천 3개(047 뒤 4개)를 더해 길어진다 — 틱 판정(011)·`/health/collect` 의 `exchanges[]`·`/refresh` 의 `snapshots[]`·관리자 화면(036) 수집 카드·수집 상태 탭(011 §3.8) 카드 수. 001 §3.6 틱 루프의 판정 단계가 perp 원천도 판정한다. 010 은 `raw/exchange=<원천 id>/…` 를 자동으로 갖는다. 025 Slack 알림도 거래소 문자열 키라 자동.
- 하지 않는 것: 현물 우주·현물 행·김프 틱 행(`{dom, fx, base, fwd, rev}`)·spreads 표 — **전부 그대로**(perp 는 틱 행에 끼지 않는다). 펀딩 이력·갭 표(048·049). 입출금(perp 에 없다). USDC·코인 마진·기간물·토큰화 주식(바이낸스 `TRADIFI_PERPETUAL`)·HIP-3. 깊은 호가(1단계뿐 — 슬리피지 계산 없음). 004 분석 API. 백필.

## 3. 동작

### 3.1 perp 행 계약 (048·049 가 복사한다)
행 = `(source, base)`:
- `source` — 원천 id, 고정 순서 `binance_perp`·`bybit_perp`·`bitget_perp`·`hyperliquid_perp`(047). 표시명 `Binance`·`Bybit`·`Bitget`·`Hyperliquid`. 모든 목록·판정·카드에서 현물 거래소 전부 **뒤에** 이 순서로 온다.
- `base` — 현물과 같은 코인 이름(배수 접두·접미를 뗀 것, §3.3). `native_symbol` — 원본 심볼(`1000PEPEUSDT`). `multiplier` — 1·1000·10000·1000000 중 하나.
- `bid`·`ask`·`bid_size`·`ask_size` — 최우선 호가. **가격은 원본 ÷ multiplier, 잔량은 원본 × multiplier**(1코인 단위 — 현물 행과 바로 비교된다). 넷 다 > 0 이고 유한할 때만 갱신한다(§3.4).
- `mark` — 마크가 ÷ multiplier(아직 없으면 null). `funding_rate` — 현재 펀딩률, **한 주기의 비율**(소수 — `0.0001` 이 0.01%, null 가능). `next_funding_ms` — 다음 정산 시각 epoch ms(null 가능). `funding_interval_h` — 정산 주기(정수 시간, 1·2·4·8, null 가능).
- `quote_ts` — 호가의 거래소 시각 epoch ms. `updated_at` — 이 행에 어느 메시지든 마지막으로 반영된 시각(tz-aware UTC).
원천별 **스트림 상태**는 001 §3.3 과 같은 모양 `{connected, last_message_at, last_error, subscribed, url, connected_since}`(바이낸스는 샤드 합산 — 012 와 같다. 폴링 원천은 §3.6 의 뜻). 조회(전체·원천별·단건·원천 순서 목록)는 전부 동기이고 쓰기는 행 1개 단위와 우주 밖 행 일괄 삭제뿐이다. 현물 행 맵과 **분리**된 맵이다 — `(exchange, base)` 조회에 perp 행이 섞이지 않는다.

### 3.2 perp 우주
- 매 **10초** 원천 3곳(047 뒤 4곳)의 목록을 REST 로 받는다(병렬·타임아웃 3초·실패는 직전 목록 유지·같은 원인 60초 1줄 — 001 §3.2 와 같다). 1초가 아닌 이유: perp 상장은 국내 상장 따리와 무관하고, 바이낸스 펀딩 주기 조회가 5분 500회 한도를 다른 호출과 나눠 쓴다. `/refresh` 는 이 목록도 그 자리에서 한 번 더 받는다.
- 각 원천은 core 계약 하나를 구현한다 — `id` / `refresh(client) -> int`(목록·주기 갱신, 나간 호출 수) / `bases() -> set[str]`(정규화한 base) / `set_universe(bases)`(perp 우주 전체 — 자기 맵에 없는 base 는 무시, 매초 부르며 같으면 무동작). **본문이 직전과 같으면 파싱하지 않는다**(001 §3.2 결정 — 비교용 바이트는 바이낸스 `serverTime`·바이빗 꼬리 `time`·비트겟 `requestTime` 을 뺀 본문).
- **perp 우주** = `{base : 원천 2곳 이상에 있다}` ∪ `(김프 우주 ∩ {base : 원천 1곳 이상에 있다})` (2026-10-08 사람 결정 — 선선갭은 쌍이 필요하고 현선갭은 국내 상장 코인의 현물 행이 있으면 된다). 김프 우주(001 §3.2)는 매초 확정되므로 perp 우주도 **매초** 다시 확정해 `set_universe` 를 넘긴다 — 목록이 10초마다 바뀌어도 우주 계산은 1초다. 현물 우주와 현물 구독은 이 스펙으로 바뀌지 않는다.
- 저장 규칙: 우주 밖 base 의 perp 행은 넣지 않고, 빠진 base 는 그 초에 지운다. 한 원천에서 같은 base 로 정규화되는 심볼이 둘이면(`PEPEUSDT`·`1000PEPEUSDT`) multiplier 1 을, 없으면 목록 순서의 첫 것을 쓴다.
- 규모(2026-10-08 실측): 바이낸스 USDT 무기한 525, 바이빗 785, 비트겟 815, Hyperliquid 약 180 → 2곳 이상 공통 **721 base**, 1곳 이상 979(§5).

### 3.3 배수 심볼 정규화 (core 공개 함수 — 네 커넥터가 같은 곳을 부른다)
`split_multiplier(raw_base: str) -> tuple[str, int]`: 접두 `1000000`·`10000`·`1000` 뒤에 영문 대문자가 오면 그 수가 배수(`1000PEPE → (PEPE, 1000)`, `1000000MOG → (MOG, 1000000)`), 접두 `1M` 뒤에 대문자면 1,000,000(`1MBABYDOGE`·`1MCHEEMS`), 접미 `1000`(바이빗 `SHIB1000 → (SHIB, 1000)`), 접두 소문자 `k` 뒤에 대문자면 1,000(Hyperliquid `kPEPE` — 047). 그 밖은 `(raw, 1)` — `1INCH`·`0G`·`2Z`·`4`·`42` 는 배수가 아니다(숫자로 시작한다고 자르면 오판). 긴 접두를 먼저 본다(`10000NEX` 는 10000). 결과 base 가 빈 문자열이면 `(raw, 1)`.
근거 실측(2026-10-08): 바이낸스 `1000PEPE`·`1000SHIB`·`1MBABYDOGE`·`1000000MOG`·`1000SATS`, 바이빗 `1000PEPE`·`SHIB1000`·`10000SATS`·`1000000BABYDOGE`, 비트겟 `1000BONK`·`1MBABYDOGE`·`10000NEX`(비트겟은 PEPE·SHIB 를 배수 없이 `PEPEUSDT`·`SHIBUSDT` 로 낸다).

### 3.4 행 갱신 (메시지 → 행, 로컬 북 없음)
- 호가 메시지: `bid`·`ask`·`bid_size`·`ask_size` 넷을 배수로 나눠·곱해 한 번에 교체, `quote_ts` = 메시지의 거래소 시각, `updated_at` = 수신 시각. 넷 중 하나라도 없거나 0 이하·NaN·inf 면 **호가는 건드리지 않는다**(수신 시각에는 센다 — 행이 아직 없으면 만들지 않는다). 바이빗 delta 처럼 일부 필드만 오면 온 필드만 바꾸되, 가격·잔량 넷의 검사는 합친 결과로 한다. 호가 시각이 지금 행보다 오래된 메시지는 버린다(순서 뒤바뀜 방어).
- 펀딩 메시지: `funding_rate`·`next_funding_ms`·`mark`(÷ multiplier) 를 온 것만 갱신. 비율·시각이 숫자가 아니면 그 필드는 두고, 행이 없으면 보류했다가 첫 호가 때 함께 싣는다(001 §3.5-2 의 체결가 보류와 같다). `funding_interval_h` 는 §3.5~3.7 의 목록·주기 조회에서 온다 — 목록 갱신마다 그 원천의 모든 행에 반영(행이 생길 때도).
- 우주 밖 base 는 버린다. 갱신은 메시지·행 단위다. **수신 경로에서 하는 일은 JSON 파싱과 이 필드 갱신뿐이다** — 행마다 새 객체를 만들지 않는다(성능 원칙). 세 원천을 합치면 호가 메시지가 **초당 수천 건**(§5 실측)이라 이 경로가 collect 박스 CPU 를 정한다.

### 3.5 바이낸스 (`binance_perp`)
- 목록: `GET https://fapi.binance.com/fapi/v1/exchangeInfo`(IP weight 1, 분당 2,400) → `symbols[]` 중 `contractType == "PERPETUAL"`(`TRADIFI_PERPETUAL`·`CURRENT_QUARTER` 등 제외)·`status == "TRADING"`·`quoteAsset == "USDT"`. 원본 base = `baseAsset`(`1000PEPE` 처럼 배수 접두가 **그대로** 들어 있다), 심볼 = `symbol`(`BTCUSDT` — 기간물은 `BTCUSDT_261225` 라 위 필터로 빠진다). 비교용 바이트는 `serverTime` 을 뺀 본문. 본문 약 1MB 급 — 10초 주기라 괜찮다. 원문 `rest:/fapi/v1/exchangeInfo`, key `symbols:perp`.
- 펀딩 주기: `GET https://fapi.binance.com/fapi/v1/fundingInfo`(weight 0, **`/fapi/v1/fundingRate` 와 IP 당 5분 500회를 나눠 쓴다**) — **60초**마다. `[{symbol, fundingIntervalHours, adjustedFundingRateCap, adjustedFundingRateFloor, updateTime}]`. 심볼 → `funding_interval_h`. 응답에 없는 심볼은 **8** 로 둔다(문서에 기본값 명문이 없다 — 실측은 805 심볼이 응답에 있고 4h 467·8h 336·1h 2 라 사실상 전부 온다). 원문 `rest:/fapi/v1/fundingInfo`, key `funding:all`.
- WebSocket 은 **경로가 둘**이다(문서: 경로 없는 옛 URL 은 `/public` 스트림만 받고, 2026-04-23 뒤 폐기 예정이라 했다 — 실제 폐기 여부는 미확인이라 경로 있는 URL 만 쓴다): 호가 `wss://fstream.binance.com/public/ws`, 마크가·펀딩 `wss://fstream.binance.com/market/ws`. 빈 연결 뒤 `{"method":"SUBSCRIBE","params":["btcusdt@depth5@500ms", …],"id":<정수>}`(응답 `{"result":null,"id":…}`, 거부 `{"code":…,"msg":…}` → `bad_request`) — 012 와 같은 방식. 빈 경로 연결이 거부되면 첫 스트림 하나를 `/stream?streams=` 에 붙여 열고 나머지는 SUBSCRIBE(실측 항목 §4 — 그때 프레임은 `{"stream":…,"data":…}` 로 싸여 온다). 심볼은 소문자.
- 한도(문서): 연결당 스트림 **1,024**, 연결당 **초당 수신 메시지 10개**(클라이언트 → 서버 — SUBSCRIBE 요청 수), 24시간마다 강제 종료(끊기면 보통의 재연결), 서버가 3분마다 ping 프레임·10분 안에 pong 없으면 종료(라이브러리 자동 pong — 끄지 않는다). 그래서 구독 요청은 **50 스트림씩, 0.2초 간격**(초당 5요청).
- 호가 **3 샤드**(`/public`), 배정 `crc32(symbol) % 3`, 심볼마다 `<symbol>@depth5@500ms` 하나 — 500ms 마다 위 5단계 스냅샷 `{"e":"depthUpdate","E":<ms>,"T":<ms>,"s":"BTCUSDT","U":…,"u":…,"pu":…,"b":[["<가격>","<잔량>"],…],"a":[…]}`. `b[0]`·`a[0]` 가 최우선, `T` 가 호가 시각. `U`·`u`·`pu` 는 쓰지 않는다(로컬 북 없음). `bookTicker` 는 실시간이라 쓰지 않는다(심볼당 초당 수십 건).
- 펀딩 **연결 1개**(`/market`, 샤드 번호 3): `!markPrice@arr@1s` 하나 — 매초 전 심볼 배열 `[{"e":"markPriceUpdate","E":<ms>,"s":"BTCUSDT","p":"<마크가>","i":"<인덱스>","P":"…","r":"<펀딩률>","T":<다음 정산 ms>}, …]`. 우주 안 심볼만 반영, 나머지는 버린다(한 프레임이 900 심볼). `r` → `funding_rate`, `T` → `next_funding_ms`, `p` → `mark`. 이 연결의 구독은 우주와 무관하게 늘 하나다.
- 원문 키: `depth5:BTCUSDT`(원본 심볼 대문자), `markPrice:all`(배열 프레임 1개 — 010 이 분당 마지막 1건만 남긴다). source `ws:/public/ws`·`ws:/market/ws`.
- 분류(011 §3.2 바이낸스 규칙 그대로): 429 `rate_limit`, 418 `banned`(IP 차단 2분~3일), 403 `banned`(WAF), 5xx `unavailable`, 그 외 4xx `bad_request`. `Retry-After` 는 문서에 없어 null. 재연결 백오프 1·2·4…30초, 구독 뒤 첫 시세에서 1초.

### 3.6 바이빗 (`bybit_perp`)
- 목록: `GET https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000`(IP 당 5초 600회 — 현물 목록 매초와 합쳐도 한참 아래). `result.list[]` 중 `contractType == "LinearPerpetual"`·`status == "Trading"`·`quoteCoin == "USDT"`(USDC 무기한은 `…PERP` 심볼이라 빠진다). 원본 base = `baseCoin`(`1000PEPE`·`SHIB1000`), 심볼 = `symbol`. `nextPageCursor` 가 비어 있지 않으면 `cursor=` 로 이어 받는다(실측 893 종이라 지금은 1페이지 — 1,000 을 넘으면 2페이지). **`fundingInterval`(분) ÷ 60 → `funding_interval_h`**(실측 480·240·60분). `retCode != 0` 은 HTTP 200 이어도 실패(`10006` 은 `rate_limit`, 그 밖 `bad_response`). 비교용 바이트는 꼬리 `time` 을 뺀 본문(페이지마다). 원문 `rest:/v5/market/instruments-info`, key `symbols:perp`.
- 시세: **WebSocket 을 쓰지 않는다**(§1 결정 — `tickers.<symbol>` 100ms delta 는 샤드당 초당 약 490 프레임이었다). `GET https://api.bybit.com/v5/market/tickers?category=linear` 를 **매초 1회**(회차가 끝난 뒤 1초 대기, 타임아웃 3초 — 001 §3.1 과 같다, IP 당 5초 600회 한도의 1/120) 받아 `result.list[]` 중 자기 맵에 있고 우주 안인 심볼만 반영한다. 항목의 `bid1Price`·`bid1Size`·`ask1Price`·`ask1Size`(호가), `markPrice`·`fundingRate`·`nextFundingTime`(ms 문자열)·`fundingIntervalHour`(정수 시간 문자열 — 오면 이것으로 갱신). 호가 시각 = 봉투의 `time`(ms). 한 응답이 한 메시지다 — 심볼마다 호가 넷·펀딩을 §3.4 규칙으로 싣는다(빠진 호가 필드는 무효). 응답 전체가 원문 1건(`rest:/v5/market/tickers`, key `tickers:all` — 010 이 분당 마지막 1건만 남긴다). `retCode != 0` 은 실패(`10006` `rate_limit`, 그 밖 `bad_response`), HTTP 는 403 `banned`·429 `rate_limit`·5xx `unavailable`·그 외 4xx `bad_request`, 타임아웃 `timeout`·연결 실패 `network`. 실패한 회차는 행을 건드리지 않고 다음 초에 다시 — 같은 원인 60초에 로그 1줄. 실측(2026-10-08): 본문 669KB·899종, 왕복 0.45초.
- 상태(§3.1 모양): `connected` = 마지막 회차 성공, `last_message_at` = 마지막 성공 회차의 수신 시각, `last_error` = 마지막 실패의 분류, `subscribed` = 우주 안 심볼 수(반영 대상), `url` = 티커 REST URL, `connected_since` = 연속 성공의 첫 시각. 우주에서 빠진 base 의 행은 그 자리에서 지운다. `/refresh` 는 티커를 따로 받지 않는다(매초 오는 중이다).

### 3.7 비트겟 (`bitget_perp`)
- 목록: `GET https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES`(IP 당 초당 20회) → `data[]` 중 `symbolType == "perpetual"`·`symbolStatus == "normal"`·`quoteCoin == "USDT"`. 원본 base = `baseCoin`, 심볼 = `symbol`(`BTCUSDT`). **`fundInterval`(시간 문자열 `"8"`·`"4"`·`"1"`) → `funding_interval_h`**. 봉투 `{"code":"00000","msg":"success","requestTime":…,"data":[…]}` — HTTP 200 이어도 `code != "00000"` 이면 `bad_response`. 비교용 바이트는 `requestTime` 을 뺀 본문. 토큰화 주식·외환(`TSLAUSDT`·`XAUUSDT`) 도 `perpetual`·`normal` 로 오는데 거르지 않는다 — 다른 원천 한 곳에도 있어야 우주에 들고, 그렇다면 정당한 선선갭이다. 원문 `rest:/api/v2/mix/market/contracts`, key `symbols:perp`.
- 시세: **WebSocket 을 쓰지 않는다**(§1 결정 — `ticker` 채널은 심볼당 초당 약 10건, 샤드당 2,300 프레임·1.3MB 였다). `GET https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES` 를 **매초 1회**(회차 뒤 1초 대기, 타임아웃 3초, IP 당 초당 20회 한도) 받아 `data[]` 중 자기 맵에 있고 우주 안인 심볼만 반영한다. 항목의 `bidPr`·`bidSz`·`askPr`·`askSz`(호가), `markPrice`·`fundingRate`, `ts`(그 항목의 호가 시각 ms). **`nextFundingTime` 은 응답에 없다** → 목록의 `fundInterval` 로 계산한다: 다음 정산 = 수신 시각 기준 주기의 다음 배수(`(now ÷ 주기ms 내림 + 1) × 주기ms`, 비트겟 정산은 UTC 00:00 부터 주기 간격). 주기를 모르는 심볼은 `next_funding_ms` null. 봉투 `code != "00000"` 은 `bad_response`, HTTP 는 429 `rate_limit`·403 `banned`·5xx `unavailable`·그 외 4xx `bad_request`, 타임아웃 `timeout`·연결 실패 `network`. 원문 `rest:/api/v2/mix/market/tickers`, key `tickers:all`. 실패 회차·로그·상태는 §3.6 과 같은 규칙. 실측(2026-10-08): 본문 412KB·816종, 왕복 0.12초.

### 3.8 정체 판정·수집 상태 (매 틱)
- 바이낸스는 019 §3.5 와 같은 샤드 단위 판정 — 조용한 시간 = 지금 − max(마지막 시세, 연결 중이면 구독 시각), 배정 0 샤드 제외, 연결됐는데 **30초** 무수신 → `stale_stream`, 미연결 → `last_error.kind`, 여러 샤드가 나쁘면 가장 오래 조용한 샤드. **호가 샤드 3개 + 펀딩 연결(샤드 3)을 함께 판정한다** — 펀딩 연결만 죽어도 `binance_perp` 는 실패(펀딩이 낡은 채 갭 표에 실리지 않게). 호가 샤드에 배정이 하나도 없으면(우주 확정 전) 판정하지 않는다. 메시지 예 `"Binance perp 스트림 정체: 샤드 1 (구독 160종목) 30초 이상 무수신"`, 펀딩은 `"… 샤드 3 (펀딩) …"`.
- 폴링 원천(바이빗·비트겟)은 샤드가 없다 — 첫 회차 결과 전 = 판정 없음, 마지막 회차가 실패 = 그 분류(`kind`·`status_code`, `url` = 티커 REST URL), 마지막 성공이 **30초** 넘게 전 = `stale_stream`(`"Bybit perp 티커 정체: 30초 이상 성공한 조회 없음"`), 그 밖 = 성공.
- 001 §3.6-5 의 판정 단계가 현물 거래소 뒤에 perp 원천을 같은 추적기(011)로 판정한다 — `collect_fail` 의 `exchange` 태그 값은 원천 id. `/health/collect` 의 `exchanges[]` 는 현물 전부 뒤에 `binance_perp bybit_perp bitget_perp`(047 뒤 `hyperliquid_perp`) 순서, `markets` = 구독 심볼 수, 그 밖 필드는 011 그대로. `/refresh` 의 `snapshots[]` 도 같은 순서. 수집 상태 탭(011 §3.8)은 카드 수·타임라인 트랙 수가 `exchanges` 길이를 따른다(3열 격자는 그대로 — 줄이 늘어난다), 표시명은 `Binance perp`·`Bybit perp`·`Bitget perp`. 관리자 화면(036) 수집 카드도 같은 목록.

## 4. 검증
네트워크 없음 — 가짜 소켓·가짜 REST·가짜 원문 싱크(001·012·019·020 의 fakes 재사용).
- `split_multiplier`: `1000PEPE→(PEPE,1000)` `10000NEX→(NEX,10000)` `1000000MOG→(MOG,1000000)` `1MBABYDOGE→(BABYDOGE,1000000)` `SHIB1000→(SHIB,1000)` `kPEPE→(PEPE,1000)`, 그리고 `1INCH`·`0G`·`2Z`·`4`·`42`·`BTC`·`1000`→배수 1. 행의 `bid`·`ask`·`mark` 는 ÷, 잔량은 × 가 되어 있다.
- perp 우주: 원천 2곳 이상인 base 는 국내 상장 없이도 든다, 1곳뿐인 base 는 김프 우주에 있을 때만 든다, 둘 다 아니면 빠지고 행이 지워진다. 목록은 10초 주기·우주 확정은 매초·같은 우주 재수신은 전송 0. 같은 base 심볼 둘이면 multiplier 1 우선. 목록 실패는 직전 목록 유지·판정에 영향 없음. 본문이 같으면 파싱하지 않는다(세 원천 각각의 비교용 바이트 규칙).
- 행 갱신: 호가 넷 중 하나라도 0·NaN·없음이면 호가 불변(수신은 센다), 바이빗 delta 의 부분 필드 합치기, 호가 시각이 오래된 메시지 무시, 펀딩 먼저 오면 보류 뒤 첫 호가에 실림, `funding_interval_h` 는 목록 갱신마다 전 행 반영, 우주 밖 심볼 버림, 현물 행 맵과 분리.
- 바이낸스: `depth5` 프레임 → `b[0]`·`a[0]`·`T`; `!markPrice@arr@1s` 배열에서 우주 안 심볼만 `r`·`T`·`p` 반영; exchangeInfo 에서 `PERPETUAL`·`TRADING`·`USDT` 만(`TRADIFI_PERPETUAL`·`BTCUSDT_261225` 제외); fundingInfo 에 없는 심볼은 8; 60초 주기; SUBSCRIBE 50개·0.2초; 418→`banned`; 펀딩 연결이 죽으면 원천 실패.
- 바이빗: 티커 응답에서 맵·우주 안 심볼만 호가·마크·펀딩·`fundingIntervalHour` 반영(그 밖은 버림), 호가 시각 = 봉투 `time`; `fundingInterval` 480→8; `nextPageCursor` 가 있으면 이어 받음; 매초 1회·회차 뒤 1초 대기; 200+`retCode 10006`→`rate_limit`(행 불변·다음 초 다시), 403→`banned`; 마지막 성공 30초 경과→`stale_stream`; 원문 key `tickers:all`·`symbols:perp`.
- 비트겟: 티커 응답 → 호가·펀딩·마크 동시 갱신, 호가 시각 = 항목 `ts`; `next_funding_ms` = `fundInterval` 의 다음 배수(4h 면 다음 4시간 경계, 주기 모르면 null); `fundInterval "4"`→4; 200+`code != "00000"`→`bad_response`, 429→`rate_limit`; 원문 key `tickers:all`·`symbols:perp`.
- 공통: 바이낸스 샤드 3·같은 심볼 같은 샤드(서브프로세스 2회), 구독 응답·깨진 프레임은 시세로 안 세고 `key=None`, 원문은 행 갱신 전에 기록(키 `depth5:`·`markPrice:all`·`tickers:all`·`symbols:perp`·`funding:all`), 바이낸스 정체 30초 샤드 단위·백오프 1·2·4…30·첫 시세에서 1, 폴링 원천 실패 회차는 다음 초 다시. `/health/collect` 의 `exchanges` 가 현물 뒤에 perp 3개 순서, `/refresh` `snapshots` 8항목(045 뒤 9). 연결 실패 기동 → `/health` 200·현물 행은 계속 갱신. 종료 시 태스크 취소·소켓 close.
- web: `npm run lint && npm run build`, 수집 상태 탭 카드 8장(또는 9)·타임라인 트랙 수 같음·표시명 `Binance perp` 등, 관리자 수집 카드 수 같음.
- 수동(실서버 — 개발 네트워크가 막히면 EC2): 기동 10초 뒤 `/health/collect` 에서 perp 3원천 `ok`, `markets` 가 각 500~720, 로그에 샤드 연결 실패·티커 조회 실패 경고 없음, 바이낸스 `/public/ws` 빈 연결이 받아들여지는지(2026-10-08 로컬 확인 — 수락, 프레임은 래핑 없이 온다). **실측해 §5 에 적을 것**(이 스펙에서 가장 중요한 숫자): 원천별 샤드당 초당 프레임 수, perp 전부 켠 뒤 collect 박스 CPU(현물만일 때와 비교 — 틱 단계별 소요 로그 포함), 메모리 증가, 1분 원문 객체 크기, perp 우주 크기와 원천별 구독 수, 목록 본문 크기·파싱 ms. CPU 가 지속 80% 를 넘으면 멈추고 사람과 상의한다(§6 빚의 선택지).

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — 행 추가 `| perp-collect | server: perp 원천 3개(binance_perp depth5@500ms+!markPrice@arr@1s 두 경로 3샤드 / bybit_perp·bitget_perp REST 전체 티커 매초 1회)·10초 목록·매초 perp 우주(2곳 이상 ∪ 김프∩1곳)·배수 정규화·펀딩 주기(fundingInfo 60초 / fundingInterval / fundInterval)·`/health/collect` 원천 3개 | web: 수집 상태 탭 카드 | 호가 1단계뿐, 바이빗·비트겟 호가는 최대 1초 늦음 |`. collect 행 비고에 "perp 는 046". 알려진 빚: "(046) collect 박스 CPU — 바이낸스 depth5 가 초당 약 750 프레임이라 1 vCPU 수집기의 여유를 EC2 에서 다시 잰다(§5 로컬 실측). 넘치면 선택지 = perp 수집을 별도 프로세스·박스로(021 의 분리와 같은 방식)", "(046) 바이빗·비트겟 perp 호가는 REST 1초 폴링이라 최대 1초 늦고 체결 단위 변화는 안 보인다 — 갭 표(048·049)는 1초 틱이라 충분하다는 결정(2026-10-08)", "(046) 바이낸스 fundingInfo 에 없는 심볼의 주기 8시간은 가정이다(문서에 기본값 명문 없음)", "(046) perp 호가는 1단계뿐 — 깊이·슬리피지는 없다", "(046) 비트겟 토큰화 주식·외환 perp 는 거르지 않는다 — 다른 원천에도 있으면 우주에 든다". 019·020 의 "EC2 실측 대기" 항목처럼 "(046) EC2 실측 대기: …" 1줄. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 046 행 → DONE, 2절 레포 구조의 core 설명 "스트림 커넥터·메모리 저장소(LiveStore)" 뒤에 "·perp 행 맵". **항상 포함.**
- `docs/context/architecture.md` — 핵심 설계 결정 둘째 행("거래소 시세는 WebSocket 상시 연결로만 받는다")에 "perp 는 바이낸스 depth5@500ms(`/public`)+!markPrice@arr@1s(`/market`)·바이빗 tickers·비트겟 ticker 스트림(046)" 과 "REST 는 … 목록·입출금 … 에 perp 목록·펀딩 주기(10초·60초)"; 데이터 흐름 mermaid 에 perp 노드 3개(`BNP[Binance perp WS<br/>3샤드+펀딩]` 등)와 perp 행 맵; "메시지 단위로 바뀐다" 행 뒤에 perp 우주 정의 문장; 판정 행에 "perp 원천은 046 §3.8"; "현재 구조" 에 perp-collect 항목(커넥터 3·저장 맵·우주·`split_multiplier`·판정 — 모듈 이름은 실행 후).
- `docs/context/dev-setup.md` — `/health/collect` 기대값 "거래소 5곳" → "현물 5곳 + perp 원천 3개(`binance_perp` … 순)", 스모크에 perp 행 확인 1줄(`/health/collect` 의 `markets`).
- `docs/context/product.md` — 용어에 **perp 원천** 정의("해외 USDT 무기한 선물 시세 원천 `binance_perp`·`bybit_perp`·`bitget_perp`(·`hyperliquid_perp`). 최우선 호가 1단계·마크가·펀딩률·다음 정산·주기. 배수 심볼은 1코인 단위로 정규화"), 기능 목록 collect 행에 "+ perp 원천 3곳 호가·펀딩", 비범위의 "선물 거래소·선물 갭 (바이낸스 선물, 바이비트 등 미연동)" 줄 삭제.
- `docs/context/db.md` — `collect_fail` 의 `exchange` 태그 값 목록에 perp 원천 id 추가(새 measurement 없음).
- 스펙(같은 PR 에서): `001-collect.md` §3.6-5 판정 문장에 "perp 원천(046)도 같은 추적기로" 한 구절, `011-health.md` §3.1 "거래소 5곳(…)" 뒤에 "+ perp 원천(046 §3.8)"·§3.8 의 거래소 카드 수·"거래소 5트랙" → "`exchanges` 길이만큼", `036-admin-v2.md` 수집 카드 수 문장.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
