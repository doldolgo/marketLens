# 046 — perp-collect

상태: DONE | 의존: 001(collect — 마켓 우주·원문 싱크·틱 판정·커넥터 공통 규칙), 011(health — 실패 분류·구간 추적·수집 상태 탭), 012·019·020(binance-stream·bybit·bitget — 목록 조회·실패 분류·정체 판정의 원형), 010(raw-archive — 원문 키 규칙)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
해외 거래소의 **USDT 무기한 선물(perp)** 최우선 호가·마크가·펀딩을 메모리에 들인다. 바이낸스·바이빗·비트겟 세 곳이 대상이고(Hyperliquid 는 047), 끝나면 수집 상태 탭에 perp 원천 3개가 실데이터 카드로 생기고 현선갭(048)·선선갭(049) 표가 이 행을 읽는다. 이 스펙만으로 화면에 새 숫자는 없다(수집 상태 카드뿐).
세 원천 모두 **REST 전체 티커를 매초 1회** 받는다 — 바이빗·비트겟은 2026-10-08 사람 결정(심볼별 WebSocket 티커는 바이빗 100ms delta·비트겟 ticker 가 합쳐 초당 약 8,400 프레임이라 수집기 CPU 를 두 배로 만들었다, §5 실측), 바이낸스도 REST 1초 폴링 — 1단계 호가만 쓰는데 WS 가 초당 750 프레임이라, 2026-10-09. 갭 표는 1초 틱이라 호가가 최대 1초 늦는 것은 손해가 아니다. 목록 조회는 012·019·020 과 **코드를 공유하지 않고** 규칙만 같다. 공식 문서와 실호출은 2026-10-08(바이낸스 티커 두 엔드포인트는 10-09)에 확인했다. 인증 없음 — 새 env 키 없다.

## 2. 범위
- 만드는 것: perp 티커 폴링 원천 3개(매초 REST·실패 분류·정체 판정), perp 심볼 목록·펀딩 주기 REST 조회, perp 행 저장소(LiveStore 안의 별도 맵)와 **perp 우주**, 배수 심볼 정규화 공개 함수, 테스트. 라이브러리 추가 없음.
- 바꾸는 기존 것: 거래소 목록이 현물 5곳(045 뒤 6곳) 뒤에 perp 원천 4개(Hyperliquid 는 047)를 더해 길어진다 — 틱 판정(011)·`/health/collect` 의 `exchanges[]`·`/refresh` 의 `snapshots[]`·관리자 화면(036) 수집 카드·수집 상태 탭(011 §3.8) 카드 수. 001 §3.6 틱 루프의 판정 단계가 perp 원천도 판정한다. 010 은 `raw/exchange=<원천 id>/…` 를 자동으로 갖는다. 025 Slack 알림도 거래소 문자열 키라 자동.
- 하지 않는 것: 현물 우주·현물 행·김프 틱 행(`{dom, fx, base, fwd, rev}`)·spreads 표 — **전부 그대로**(perp 는 틱 행에 끼지 않는다). 펀딩 이력·갭 표(048·049). 입출금(perp 에 없다). USDC·코인 마진·기간물·토큰화 주식(바이낸스 `TRADIFI_PERPETUAL`)·HIP-3. 깊은 호가(1단계뿐 — 슬리피지 계산 없음). 004 분석 API. 백필.

## 3. 동작

### 3.1 perp 행 계약 (048·049 가 복사한다)
행 = `(source, base)`:
- `source` — 원천 id, 고정 순서 `binance_perp`·`bybit_perp`·`bitget_perp`·`hyperliquid_perp`. 표시명 `Binance`·`Bybit`·`Bitget`·`Hyperliquid`. 모든 목록·판정·카드에서 현물 거래소 전부 **뒤에** 이 순서로 온다.
- `base` — 현물과 같은 코인 이름(배수 접두·접미를 뗀 것, §3.3). `native_symbol` — 원본 심볼(`1000PEPEUSDT`). `multiplier` — 1·1000·10000·1000000 중 하나.
- `bid`·`ask`·`bid_size`·`ask_size` — 최우선 호가. **가격은 원본 ÷ multiplier, 잔량은 원본 × multiplier**(1코인 단위 — 현물 행과 바로 비교된다). 넷 다 > 0 이고 유한할 때만 갱신한다(§3.4).
- `mark` — 마크가 ÷ multiplier(아직 없으면 null). `funding_rate` — 현재 펀딩률, **한 주기의 비율**(소수 — `0.0001` 이 0.01%, null 가능). `next_funding_ms` — 다음 정산 시각 epoch ms(null 가능). `funding_interval_h` — 정산 주기(정수 시간, 1·2·4·8, null 가능).
- `quote_ts` — 호가의 거래소 시각 epoch ms. `updated_at` — 이 행에 어느 메시지든 마지막으로 반영된 시각(tz-aware UTC).
원천별 **스트림 상태**는 001 §3.3 과 같은 모양 `{connected, last_message_at, last_error, subscribed, url, connected_since}`(폴링 원천의 뜻 — §3.6). 조회(전체·원천별·단건·원천 순서 목록)는 전부 동기이고 쓰기는 행 1개 단위와 우주 밖 행 일괄 삭제뿐이다. 현물 행 맵과 **분리**된 맵이다 — `(exchange, base)` 조회에 perp 행이 섞이지 않는다.

### 3.2 perp 우주
- 매 **10초** 원천 4곳의 목록을 REST 로 받는다(병렬·타임아웃 3초·실패는 직전 목록 유지·같은 원인 60초 1줄 — 001 §3.2 와 같다). 1초가 아닌 이유: perp 상장은 국내 상장 따리와 무관하고, 바이낸스 펀딩 주기 조회가 5분 500회 한도를 다른 호출과 나눠 쓴다. `/refresh` 는 이 목록도 그 자리에서 한 번 더 받는다.
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
- 우주 밖 base 는 버린다. 갱신은 메시지·행 단위다. **수신 경로에서 하는 일은 JSON 파싱과 이 필드 갱신뿐이다** — 행마다 새 객체를 만들지 않는다(성능 원칙). 세 원천이 매초 전체 티커 응답(합쳐 약 1.4MB·3,400 항목, §5 실측)을 훑으므로 이 경로가 collect 박스 CPU 를 정한다.

### 3.5 바이낸스 (`binance_perp`)
- 목록: `GET https://fapi.binance.com/fapi/v1/exchangeInfo`(IP weight 1, 분당 2,400) → `symbols[]` 중 `contractType == "PERPETUAL"`(`TRADIFI_PERPETUAL`·`CURRENT_QUARTER` 등 제외)·`status == "TRADING"`·`quoteAsset == "USDT"`. 원본 base = `baseAsset`(`1000PEPE` 처럼 배수 접두가 **그대로** 들어 있다), 심볼 = `symbol`(`BTCUSDT` — 기간물은 `BTCUSDT_261225` 라 위 필터로 빠진다). 비교용 바이트는 `serverTime` 을 뺀 본문. 본문 약 1MB 급 — 10초 주기라 괜찮다. 원문 `rest:/fapi/v1/exchangeInfo`, key `symbols:perp`.
- 펀딩 주기: `GET https://fapi.binance.com/fapi/v1/fundingInfo`(weight 0, **`/fapi/v1/fundingRate` 와 IP 당 5분 500회를 나눠 쓴다**) — **60초**마다. `[{symbol, fundingIntervalHours, adjustedFundingRateCap, adjustedFundingRateFloor, updateTime}]`. 심볼 → `funding_interval_h`. 응답에 없는 심볼은 **8** 로 둔다(문서에 기본값 명문이 없다 — 실측은 805 심볼이 응답에 있고 4h 467·8h 336·1h 2 라 사실상 전부 온다). 원문 `rest:/fapi/v1/fundingInfo`, key `funding:all`.
- 시세: **WebSocket 을 쓰지 않는다**(§1 결정 2026-10-09). **매초 1회**(회차가 끝난 뒤 1초 대기, 타임아웃 3초 — 001 §3.1) 두 요청을 한 회차에 **병렬**로 받는다 — `GET https://fapi.binance.com/fapi/v1/ticker/bookTicker`(symbol 생략 = 전체, IP 가중치 5)와 `GET https://fapi.binance.com/fapi/v1/premiumIndex`(전체, 가중치 10). 분당 가중치 900 = IP 한도 2,400 의 37.5%(+ exchangeInfo 10초마다 1). bookTicker 항목 `symbol`·`bidPrice`·`bidQty`·`askPrice`·`askQty`·`time`(그 항목의 호가 시각 ms — 문서에 없는 `lastUpdateId` 도 온다. RPI 주문은 빠진 호가다) → 호가 넷. premiumIndex 항목 `symbol`·`markPrice`·`lastFundingRate`·`nextFundingTime` → `mark`·`funding_rate`·`next_funding_ms`(USDC·기간물까지 927 심볼이 섞여 오지만 맵에 없어 빠진다). 둘 다 자기 맵에 있고 우주 안인 심볼만 반영하고, 호가를 먼저 싣고 펀딩을 싣는다. 응답 전체가 각각 원문 1건 — `rest:/fapi/v1/ticker/bookTicker` key `bookTicker:all`, `rest:/fapi/v1/premiumIndex` key `premiumIndex:all`(010 이 분당 마지막 1건만 남긴다). 실측(2026-10-09): bookTicker 791 심볼·약 119KB·0.1~0.2초, premiumIndex 927 심볼·약 206KB.
- 실패: 두 요청 중 **하나라도 실패하면 그 회차는 실패** — 행을 건드리지 않고(성공한 쪽도 싣지 않는다 — 펀딩만 낡은 채 갭 표에 실리지 않게) 다음 초에 다시, 같은 원인 60초에 로그 1줄, `last_error.url` 은 실패한 쪽 URL. 분류(011 §3.2 바이낸스 규칙 그대로): 429 `rate_limit`, 418 `banned`(IP 차단 2분~3일 — 429 뒤에도 계속 부르면 온다. 그래서 실패 회차 뒤에도 바로 다시 부르지 않고 다음 회차까지 기다린다), 403 `banned`(WAF), 5xx `unavailable`, 그 외 4xx `bad_request`, JSON 아님·배열 아님 `bad_response`, 타임아웃 `timeout`·연결 실패 `network`. `Retry-After` 는 문서에 없어 null. bookTicker 응답의 `X-MBX-USED-WEIGHT-1M` 헤더는 문서가 "정확하지 않으니 무시" 라 해 한도는 주기로만 지킨다.
- 상태는 §3.6 과 같은 규칙, `url` = bookTicker REST URL.

### 3.6 바이빗 (`bybit_perp`)
- 목록: `GET https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000`(IP 당 5초 600회 — 현물 목록 매초와 합쳐도 한참 아래). `result.list[]` 중 `contractType == "LinearPerpetual"`·`status == "Trading"`·`quoteCoin == "USDT"`(USDC 무기한은 `…PERP` 심볼이라 빠진다). 원본 base = `baseCoin`(`1000PEPE`·`SHIB1000`), 심볼 = `symbol`. `nextPageCursor` 가 비어 있지 않으면 `cursor=` 로 이어 받는다(실측 893 종이라 지금은 1페이지 — 1,000 을 넘으면 2페이지). **`fundingInterval`(분) ÷ 60 → `funding_interval_h`**(실측 480·240·60분). `retCode != 0` 은 HTTP 200 이어도 실패(`10006` 은 `rate_limit`, 그 밖 `bad_response`). 비교용 바이트는 꼬리 `time` 을 뺀 본문(페이지마다). 원문 `rest:/v5/market/instruments-info`, key `symbols:perp`.
- 시세: **WebSocket 을 쓰지 않는다**(§1 결정 — `tickers.<symbol>` 100ms delta 는 샤드당 초당 약 490 프레임이었다). `GET https://api.bybit.com/v5/market/tickers?category=linear` 를 **매초 1회**(회차가 끝난 뒤 1초 대기, 타임아웃 3초 — 001 §3.1 과 같다, IP 당 5초 600회 한도의 1/120) 받아 `result.list[]` 중 자기 맵에 있고 우주 안인 심볼만 반영한다. 항목의 `bid1Price`·`bid1Size`·`ask1Price`·`ask1Size`(호가), `markPrice`·`fundingRate`·`nextFundingTime`(ms 문자열)·`fundingIntervalHour`(정수 시간 문자열 — 오면 이것으로 갱신). 호가 시각 = 봉투의 `time`(ms). 한 응답이 한 메시지다 — 심볼마다 호가 넷·펀딩을 §3.4 규칙으로 싣는다(빠진 호가 필드는 무효). 응답 전체가 원문 1건(`rest:/v5/market/tickers`, key `tickers:all` — 010 이 분당 마지막 1건만 남긴다). `retCode != 0` 은 실패(`10006` `rate_limit`, 그 밖 `bad_response`), HTTP 는 403 `banned`·429 `rate_limit`·5xx `unavailable`·그 외 4xx `bad_request`, 타임아웃 `timeout`·연결 실패 `network`. 실패한 회차는 행을 건드리지 않고 다음 초에 다시 — 같은 원인 60초에 로그 1줄. 실측(2026-10-08): 본문 669KB·899종, 왕복 0.45초.
- 상태(§3.1 모양): `connected` = 마지막 회차 성공, `last_message_at` = 마지막 성공 회차의 수신 시각, `last_error` = 마지막 실패의 분류, `subscribed` = 우주 안 심볼 수(반영 대상), `url` = 티커 REST URL, `connected_since` = 연속 성공의 첫 시각. 우주에서 빠진 base 의 행은 그 자리에서 지운다. `/refresh` 는 티커를 따로 받지 않는다(매초 오는 중이다).

### 3.7 비트겟 (`bitget_perp`)
- 목록: `GET https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES`(IP 당 초당 20회) → `data[]` 중 `symbolType == "perpetual"`·`symbolStatus == "normal"`·`quoteCoin == "USDT"`. 원본 base = `baseCoin`, 심볼 = `symbol`(`BTCUSDT`). **`fundInterval`(시간 문자열 `"8"`·`"4"`·`"1"`) → `funding_interval_h`**. 봉투 `{"code":"00000","msg":"success","requestTime":…,"data":[…]}` — HTTP 200 이어도 `code != "00000"` 이면 `bad_response`. 비교용 바이트는 `requestTime` 을 뺀 본문. 토큰화 주식·외환(`TSLAUSDT`·`XAUUSDT`) 도 `perpetual`·`normal` 로 오는데 거르지 않는다 — 다른 원천 한 곳에도 있어야 우주에 들고, 그렇다면 정당한 선선갭이다. 원문 `rest:/api/v2/mix/market/contracts`, key `symbols:perp`.
- 시세: **WebSocket 을 쓰지 않는다**(§1 결정 — `ticker` 채널은 심볼당 초당 약 10건, 샤드당 2,300 프레임·1.3MB 였다). `GET https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES` 를 **매초 1회**(회차 뒤 1초 대기, 타임아웃 3초, IP 당 초당 20회 한도) 받아 `data[]` 중 자기 맵에 있고 우주 안인 심볼만 반영한다. 항목의 `bidPr`·`bidSz`·`askPr`·`askSz`(호가), `markPrice`·`fundingRate`, `ts`(그 항목의 호가 시각 ms). **`nextFundingTime` 은 응답에 없다** → 목록의 `fundInterval` 로 계산한다: 다음 정산 = 수신 시각 기준 주기의 다음 배수(`(now ÷ 주기ms 내림 + 1) × 주기ms`, 비트겟 정산은 UTC 00:00 부터 주기 간격). 주기를 모르는 심볼은 `next_funding_ms` null. 봉투 `code != "00000"` 은 `bad_response`, HTTP 는 429 `rate_limit`·403 `banned`·5xx `unavailable`·그 외 4xx `bad_request`, 타임아웃 `timeout`·연결 실패 `network`. 원문 `rest:/api/v2/mix/market/tickers`, key `tickers:all`. 실패 회차·로그·상태는 §3.6 과 같은 규칙. 실측(2026-10-08): 본문 412KB·816종, 왕복 0.12초.

### 3.8 정체 판정·수집 상태 (매 틱)
- 폴링 원천(세 곳 모두)은 샤드가 없다 — 첫 회차 결과 전 = 판정 없음, 마지막 회차가 실패 = 그 분류(`kind`·`status_code`, `url` = 실패한 티커 REST URL), 마지막 성공이 **30초** 넘게 전 = `stale_stream`(`"Bybit perp 티커 정체: 30초 이상 성공한 조회 없음"` — 바이낸스·비트겟도 같은 꼴), 그 밖 = 성공.
- 001 §3.6-5 의 판정 단계가 현물 거래소 뒤에 perp 원천을 같은 추적기(011)로 판정한다 — `collect_fail` 의 `exchange` 태그 값은 원천 id. `/health/collect` 의 `exchanges[]` 는 현물 전부 뒤에 `binance_perp bybit_perp bitget_perp hyperliquid_perp` 순서, `markets` = 구독 심볼 수, 그 밖 필드는 011 그대로. `/refresh` 의 `snapshots[]` 도 같은 순서. 수집 상태 탭(011 §3.8)은 카드 수·타임라인 트랙 수가 `exchanges` 길이를 따른다(3열 격자는 그대로 — 줄이 늘어난다), 표시명은 `Binance perp`·`Bybit perp`·`Bitget perp`·`Hyperliquid`(047). 관리자 화면(036) 수집 카드도 같은 목록.

## 4. 검증
네트워크 없음 — 가짜 소켓·가짜 REST·가짜 원문 싱크(001·012·019·020 의 fakes 재사용).
- `split_multiplier`: `1000PEPE→(PEPE,1000)` `10000NEX→(NEX,10000)` `1000000MOG→(MOG,1000000)` `1MBABYDOGE→(BABYDOGE,1000000)` `SHIB1000→(SHIB,1000)` `kPEPE→(PEPE,1000)`, 그리고 `1INCH`·`0G`·`2Z`·`4`·`42`·`BTC`·`1000`→배수 1. 행의 `bid`·`ask`·`mark` 는 ÷, 잔량은 × 가 되어 있다.
- perp 우주: 원천 2곳 이상인 base 는 국내 상장 없이도 든다, 1곳뿐인 base 는 김프 우주에 있을 때만 든다, 둘 다 아니면 빠지고 행이 지워진다. 목록은 10초 주기·우주 확정은 매초·같은 우주 재수신은 전송 0. 같은 base 심볼 둘이면 multiplier 1 우선. 목록 실패는 직전 목록 유지·판정에 영향 없음. 본문이 같으면 파싱하지 않는다(세 원천 각각의 비교용 바이트 규칙).
- 행 갱신: 호가 넷 중 하나라도 0·NaN·없음이면 호가 불변(수신은 센다), 바이빗 delta 의 부분 필드 합치기, 호가 시각이 오래된 메시지 무시, 펀딩 먼저 오면 보류 뒤 첫 호가에 실림, `funding_interval_h` 는 목록 갱신마다 전 행 반영, 우주 밖 심볼 버림, 현물 행 맵과 분리.
- 바이낸스: bookTicker 항목에서 맵·우주 안 심볼만 호가 넷 반영, 호가 시각 = 항목 `time`; premiumIndex 항목에서 `markPrice`·`lastFundingRate`·`nextFundingTime`(USDC·기간물은 버림); 한 회차의 두 요청이 병렬로 나감; premiumIndex 만 실패해도 회차 실패·행 불변·`url` 은 그쪽; exchangeInfo 에서 `PERPETUAL`·`TRADING`·`USDT` 만(`TRADIFI_PERPETUAL`·`BTCUSDT_261225` 제외); fundingInfo 에 없는 심볼은 8; 60초 주기; 매초 1회·회차 뒤 1초 대기; 418→`banned`, 429→`rate_limit`(행 불변·다음 초 다시); 마지막 성공 30초 경과→`stale_stream`; 원문 key `bookTicker:all`·`premiumIndex:all`·`symbols:perp`·`funding:all`.
- 바이빗: 티커 응답에서 맵·우주 안 심볼만 호가·마크·펀딩·`fundingIntervalHour` 반영(그 밖은 버림), 호가 시각 = 봉투 `time`; `fundingInterval` 480→8; `nextPageCursor` 가 있으면 이어 받음; 매초 1회·회차 뒤 1초 대기; 200+`retCode 10006`→`rate_limit`(행 불변·다음 초 다시), 403→`banned`; 마지막 성공 30초 경과→`stale_stream`; 원문 key `tickers:all`·`symbols:perp`.
- 비트겟: 티커 응답 → 호가·펀딩·마크 동시 갱신, 호가 시각 = 항목 `ts`; `next_funding_ms` = `fundInterval` 의 다음 배수(4h 면 다음 4시간 경계, 주기 모르면 null); `fundInterval "4"`→4; 200+`code != "00000"`→`bad_response`, 429→`rate_limit`; 원문 key `tickers:all`·`symbols:perp`.
- 공통: 원문은 행 갱신 전에 기록(키 `bookTicker:all`·`premiumIndex:all`·`tickers:all`·`symbols:perp`·`funding:all`), 실패 회차는 다음 초 다시. `/health/collect` 의 `exchanges` 가 현물 뒤에 perp 3개 순서, `/refresh` `snapshots` 8항목(045 뒤 9). 연결 실패 기동 → `/health` 200·현물 행은 계속 갱신. 종료 시 태스크 취소.
- web: `npm run lint && npm run build`, 수집 상태 탭 카드 8장(또는 9)·타임라인 트랙 수 같음·표시명 `Binance perp` 등, 관리자 수집 카드 수 같음.
- 수동(실서버 — 개발 네트워크가 막히면 EC2): 기동 10초 뒤 `/health/collect` 에서 perp 3원천 `ok`, `markets` 가 각 500~720, 로그에 티커 조회 실패 경고 없음. **실측해 §5 에 적을 것**(이 스펙에서 가장 중요한 숫자): perp 전부 켠 뒤 collect 박스 CPU(현물만일 때와 비교 — 틱 단계별 소요 로그 포함), 메모리 증가, 1분 원문 객체 크기, perp 우주 크기와 원천별 반영 수, 목록·티커 본문 크기·파싱 ms. CPU 가 지속 80% 를 넘으면 멈추고 사람과 상의한다(§6 빚의 선택지).

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# server (워크트리 marketlens-046rest, uv venv 3.12, 2026-10-09)
cd server && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m pytest -q
# → All checks passed! / 1904 passed, 1 skipped (binance_perp 28·bybit_perp 19·bitget_perp 19, gap·spreads 그대로)
cd web && npm run lint && npm run build          # oxlint 0건 · vite 빌드 통과 (index 249KB, Tab 211KB)
# 실기동 (이 네트워크에서 거래소 REST·WS 모두 열림) — 포트 8050, Redis·Influx 없이
cd server && .venv/bin/uvicorn app.main:app --port 8050
curl -s localhost:8050/health/collect           # 기동 80초 뒤 9원천 전부 ok, 로그에 perp 티커 조회 실패 경고 0
#   markets: upbit 261 bithumb 406 binance 292 bybit 269 bitget 309 okx 224 | binance_perp 499 bybit_perp 713 bitget_perp 680
#   Redis 를 잠깐 붙여 gap:latest 를 읽음 — binance_perp 행 975, BTC×binance 행 funding 0.006278·intervalH 8·nextFundingTs 1791532800(다음 8시간 경계)
```
실측(2026-10-08 로컬 M4, 현물 5 + perp 3 켠 수집기 vs main 의 현물만 수집기를 같은 시각에 나란히 — ps 3초 간격 12회. 바이낸스 perp 는 당시 WS 설계):
- perp 우주 721 base(2곳 이상 공통 721, 1곳 이상 979 — 김프 우주 ∩ 1곳은 전부 2곳 이상에 포함됐다). 원천별 반영 심볼 바이낸스 499·바이빗 712·비트겟 680. 배수 심볼 바이낸스 12·바이빗 17·비트겟 8.
- 목록 본문·파싱: exchangeInfo 1,121KB·6.3ms, fundingInfo 134KB·0.5ms, instruments-info 818KB·3.9ms, contracts 617KB·7.3ms(json.loads 만). 전체 티커: 바이빗 669KB·왕복 0.45초, 비트겟 412KB·0.12초, 바이낸스 bookTicker 119KB·0.1~0.2초 + premiumIndex 206KB(10-09).
- 프레임/초(실 구독 크기로 10초 측정): 바이낸스 depth5 호가 샤드 172심볼 252 프레임·92KB/초(3샤드 합 약 750), 펀딩 연결 2.2 프레임·163KB/초(한 프레임 약 76KB) — 그래서 §1 의 바이낸스 REST 전환 결정(10-09). **심볼별 WebSocket 티커는 바이빗 샤드당 233심볼 487 프레임/초, 비트겟 234심볼 2,314 프레임·1,301KB/초** — 그래서 §1 의 바이빗·비트겟 REST 결정(10-08).
- 수집기 CPU(M4, 1코어 기준 %): 현물만 18~49 → 현물+perp(WS 티커) 27~60(+15~25%p) → 현물+perp(REST 티커) 27~50(현물만과 나란히 잰 12회에서 **+0~10%p, 중앙값 +2~5%p**). RSS +15~20MB(약 115 → 135MB).
- 틱 단계 60초 요약(합계 p50/p95/max ms): 현물만 77/96/123, 현물+perp(REST) 74/93/111 — 같다. 단계 배분만 달라졌다(현물만은 `인계` 57ms·`틱` 4.6ms, perp 는 `틱` 46ms·`인계` 4.5ms — GC 전체 수집이 어느 단계에 걸리느냐 차이로 보이며 합계는 같다).

실측(2026-10-09 로컬 M4, 바이낸스 perp REST 전환 뒤 — 이 브랜치 수집기 vs main 의 바이낸스 perp WS 수집기를 같은 시각에 나란히, top 5초 간격 12회):
- 수집기 CPU(1코어 기준 %): REST 26~32(중앙값 29.3) vs WS 27~34(중앙값 31.0) — **−1~3%p**. RSS 둘 다 180~210MB. 틱 단계 60초 요약(합계 p50 약 75ms)은 같다. M4 는 코어가 빨라 차이가 작게 보인다 — 1 vCPU c7g 의 값은 **배포 뒤 실측**(§6 빚).
- 반영 심볼 binance_perp 499(bookTicker 791 중)·bybit_perp 713·bitget_perp 680. 두 요청 모두 매초 성공, 가중치 분당 900.

실측(2026-10-09 01:46~01:48 KST, 운영 collect 박스 c7g.medium 1 vCPU — **바이낸스 perp 가 WS 이던 전환 전** 배포 c549195 뒤 64분 지난 컨테이너, 읽기 전용 명령만):
- 박스 CPU(CloudWatch `CPUUtilization` 5분 평균, 인스턴스 전체): 현물 5곳만이던 10-07 00시~10-08 21시 45~70(중앙값 56) → OKX 켜진 뒤(10-08 21:49 배포) 62~75 → flow-eth 까지(22:38) 66~81 → **perp 켜진 뒤(00:42) 83~95**. `docker stats` 5초 간격 6회는 74·80·83·89·90·100(중앙값 86). 다만 00:15~00:40 KST(perp 배포 27분 전)에 이미 84~93 이어서 perp 몫이 +15%p 인지 +10%p 인지는 못 가른다(그 시간에 박스에서 무엇이 돌았는지 기록이 없다). 로컬 M4 의 +2~5%p 와는 거리가 멀다 — 코어가 빠른 M4 에서는 같은 일이 작게 보인다.
- 수집기 컨테이너 RSS 647MB(`/proc/1/status` VmRSS, HWM 과 같음 — 기동 뒤 더 올라간 적 없음). 호스트 1.8GB 중 free 170MB·available 702MB. 현물만일 때의 EC2 RSS 기록이 없어 증가분은 모른다(로컬은 +15~20MB).
- 틱 단계 60초 요약(ms p50/p95/max, 01:43~01:47 다섯 줄): 깨어남 0.1~0.6/70~203/406~764 · 틱 10~57/66~79/73~161 · 인계 15~51/76~93/88~172 · 표 39/42~102/43~125 · 합계 117~121/149~220/194~312. 로컬 합계 p50 74 의 1.6배이고, 깨어남 p95 70~200ms(로컬 0)는 이벤트 루프가 초 경계에 바로 못 깨어난다는 뜻이다. 기동 직후(00:46~00:48)엔 깨어남 p50 이 38ms 였다.
- REST 시간 초과: 64분 동안 `ConnectTimeout` 경고 27건 — perp 티커(바이빗 6·비트겟 5)·perp 목록(바이낸스 5·바이빗 1)·현물 목록(바이빗 3·비트겟 2·업비트 2·OKX 2·빗썸 1). 전부 "다음 초 다시"·"직전 목록 유지" 로 끝났고 `successRate1h` 는 bybit_perp 99.7·bitget_perp 99.8. 현물만일 때도 있었는지는 이전 컨테이너 로그가 지워져 모른다 — CPU 포화가 원인이면 047 뒤 늘어날 것이니 그때 다시 센다.
- 1분 원문 객체(gzip, 01:44~01:47 네 개): binance_perp 120~122KB · bybit_perp 152~153KB · bitget_perp 105KB — 셋 합쳐 분당 약 378KB, 하루 약 530MB. 비교: 현물 binance 254~256KB·bybit 95~99KB·okx 121~123KB.
- 컨테이너 수신 3.3MB/초·4,400패킷/초(`/proc/net/dev` 10초 차이), 송신 2.4MB/초. 반영 심볼 binance_perp 499·bybit_perp 713·bitget_perp 680(`/health/collect`).

## 6. 갱신할 문서
- `docs/context/status.md` — 행 추가 `| perp-collect | server: perp 원천 3개(binance_perp bookTicker+premiumIndex / bybit_perp·bitget_perp 전체 티커 — 셋 다 REST 매초 1회)·10초 목록·매초 perp 우주(2곳 이상 ∪ 김프∩1곳)·배수 정규화·펀딩 주기(fundingInfo 60초 / fundingInterval / fundInterval)·`/health/collect` 원천 3개 | web: 수집 상태 탭 카드 | 호가 1단계뿐, 세 원천 호가는 최대 1초 늦음 |`. collect 행 비고에 "perp 는 046". 알려진 빚: "(046) collect 박스 CPU — 바이낸스 perp 가 WS 이던 때 EC2 83~95%, REST 전환 뒤는 배포 뒤 실측. 넘치면 선택지 = perp 수집을 별도 프로세스·박스로(021 의 분리와 같은 방식)", "(046) perp 호가는 세 원천 모두 REST 1초 폴링이라 최대 1초 늦고 체결 단위 변화는 안 보인다 — 갭 표(048·049)는 1초 틱이라 충분하다는 결정(2026-10-08·10-09)", "(046) 바이낸스 fundingInfo 에 없는 심볼의 주기 8시간은 가정이다(문서에 기본값 명문 없음)", "(046) perp 호가는 1단계뿐 — 깊이·슬리피지는 없다", "(046) 비트겟 토큰화 주식·외환 perp 는 거르지 않는다 — 다른 원천에도 있으면 우주에 든다". 019·020 의 "EC2 실측 대기" 항목처럼 "(046) EC2 실측 대기: …" 1줄. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 046 행 → DONE, 2절 레포 구조의 core 설명 "스트림 커넥터·메모리 저장소(LiveStore)" 뒤에 "·perp 행 맵". **항상 포함.**
- `docs/context/architecture.md` — 핵심 설계 결정 둘째 행("거래소 시세는 WebSocket 상시 연결로만 받는다")에 "perp 원천 셋만 예외로 REST 전체 티커를 매초(046)" 와 "REST 는 … 목록·입출금 … 에 perp 목록·펀딩 주기(10초·60초)"; 데이터 흐름 mermaid 에 perp 노드 3개(`BNP[Binance perp REST<br/>bookTicker·premiumIndex 매초]` 등)와 perp 행 맵; "메시지 단위로 바뀐다" 행 뒤에 perp 우주 정의 문장; 판정 행에 "perp 원천은 046 §3.8"; "현재 구조" 에 perp-collect 항목(커넥터 3·저장 맵·우주·`split_multiplier`·판정 — 모듈 이름은 실행 후).
- `docs/context/dev-setup.md` — `/health/collect` 기대값 "거래소 5곳" → "현물 5곳 + perp 원천 3개(`binance_perp` … 순)", 스모크에 perp 행 확인 1줄(`/health/collect` 의 `markets`).
- `docs/context/product.md` — 용어에 **perp 원천** 정의("해외 USDT 무기한 선물 시세 원천 `binance_perp`·`bybit_perp`·`bitget_perp`(·`hyperliquid_perp`). 최우선 호가 1단계·마크가·펀딩률·다음 정산·주기. 배수 심볼은 1코인 단위로 정규화"), 기능 목록 collect 행에 "+ perp 원천 3곳 호가·펀딩", 비범위의 "선물 거래소·선물 갭 (바이낸스 선물, 바이비트 등 미연동)" 줄 삭제.
- `docs/context/db.md` — `collect_fail` 의 `exchange` 태그 값 목록에 perp 원천 id 추가(새 measurement 없음).
- 스펙(같은 PR 에서): `001-collect.md` §3.6-5 판정 문장에 "perp 원천(046)도 같은 추적기로" 한 구절, `011-health.md` §3.1 "거래소 5곳(…)" 뒤에 "+ perp 원천(046 §3.8)"·§3.8 의 거래소 카드 수·"거래소 5트랙" → "`exchanges` 길이만큼", `036-admin-v2.md` 수집 카드 수 문장.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `server/app/core/perp.py`(split_multiplier·perp_universe·PerpSink), `core/perp_universe.py`(PerpUniverse), `core/models.py`(PerpRow), `core/live_store.py`(perp 맵 5메서드), `core/contracts.py`(PerpSymbolSource = ForeignSymbolSource 별칭), `core/config.py`(PERP_SOURCES·COLLECT_SOURCES), `core/universe.py`(perps 훅), `core/collect.py`(perp 목록 먼저·saved 8), `core/streams/binance_perp.py`(소켓 없음 — 2026-10-09 REST 전환)·`bybit_perp.py`·`bitget_perp.py`, `features/health/service.py`(COLLECT_SOURCES·perp markets), `main.py`(배선·종료), 테스트 `tests/test_perp.py`·`test_perp_universe.py`·`test_stream_binance_perp.py`·`test_stream_bybit_perp.py`·`test_stream_bitget_perp.py` + 기존 6개 갱신(8원천 순서), web `src/shared/format.ts`(표시명 3)·`src/features/health/Tab.tsx`(주석)·`admin/admin.js`(목록 3).
- 추측한 지점 (묻지 않고 정한 사소한 것): ① PerpSink 의 `quote` 에서 None = "이 메시지에 안 왔다"(delta), 스냅샷에 빠진 호가 필드는 커넥터가 NaN 으로 넘겨 무효 처리 — §3.4 "없거나" 와 "온 필드만" 을 양립시키는 규약(§3.4 에 한 줄 적음). ② 펀딩 보류는 (원천, base) 당 부분 필드 사전이고 보류끼리는 합친다, 마크가는 보류 때 이미 배수로 나눈다. ③ 바이낸스 한 회차의 두 요청은 둘 다 끝까지 받고 나서 판단한다 — 한쪽이 먼저 실패해도 다른 쪽 요청이 다음 회차까지 떠다니지 않게. ④ 바이낸스 회차의 행 수신 시각은 두 응답이 다 온 뒤 한 번 읽은 시각이다(bookTicker·premiumIndex 항목에 같은 값). ⑤ 폴링 원천의 실패 로그 억제는 원인(kind)별 60초 — 001 §3.2 와 같은 규칙. ⑥ 비트겟 티커의 `symbolType` 은 REST 전체 티커 응답에 없어 보지 않는다(목록에서 `perpetual` 만 맵에 든다). ⑦ `markets` 는 스펙대로 반영 심볼 수(폴링 원천은 우주 안 심볼 수) — 행 수가 아니다. ⑧ PerpSymbolSource 는 모양이 같아 새 Protocol 을 만들지 않고 ForeignSymbolSource 별칭으로 뒀다.
- 실행 중 함께 고친 스펙 절: §1·§2·§3.1·§3.2(규모 실측)·§3.6·§3.7·§3.8·§4·§6 — 바이빗·비트겟을 심볼별 WebSocket 티커에서 **REST 전체 티커 매초 폴링**으로(2026-10-08 사람 결정, 실측 §5). 2026-10-09 에 §1·§2·§3.1·§3.4·§3.5·§3.8·§4·§5·§6 — 바이낸스도 depth5 3샤드 + markPrice 연결에서 **bookTicker + premiumIndex REST 매초 폴링**으로(EC2 1 vCPU 가 perp 뒤 83~95% 였다). architecture.md 의 "시세는 WebSocket 만" 원칙에 perp 세 원천 예외를 적었다.
- 남은 빚: status.md 의 (046) 항목 — collect 박스 CPU(바이낸스 REST 전환 뒤 배포 실측), 세 원천 1초 지연, 바이낸스 fundingInfo 기본 8h 가정, 호가 1단계, 비트겟 토큰화 주식·다음 정산 계산 가정, EC2 실측 대기(원문 객체 크기·RSS). 추가로 `server/marketlens_server.egg-info/` 가 git 에 추적돼 있어 editable 재설치 때마다 바뀐다(001 빚에 이미 있음 — 이 PR 에서는 되돌려 커밋하지 않았다).
