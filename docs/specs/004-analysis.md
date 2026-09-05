# 004 — analysis

상태: DONE | 의존: 001(collect — 메모리 스냅샷·USDT 시세·호가 단계), 003(spreads — `core/premium.py` 의 `premium_percent`)

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
스프레드 표에서 고른 **코인 1개를 깊이 파는** 분석 API 6개를 만든다. 트레이더가 "이 코인을 이 금액만큼 실제로 옮기면 호가를 얼마나 먹고, 얼마가 남는가"를 호가창 기준으로 확인하고, 전종목 스캔·매트릭스로 후보를 찾는다. 화면 없음 — curl/브라우저로 직접 호출하는 BE 전용 도구.

## 2. 범위
- 만드는 것: 기능 폴더 `analysis` (server 만). 엔드포인트 6개 — `GET /orderbook/{exchange}` `GET /slippage/{exchange}` `GET /arbitrage` `GET /premium` `GET /premium/scan` `GET /matrix`.
- 호가창 소진(walk) 계산은 `core/orderbook.py` 에 산다 — 003(spreads)과 이 스펙이 함께 쓰는 공유 모듈이다(기능 간 import 금지, CLAUDE.md §2). **함수는 전부 동기다**(003 §2 의 근거). 이 스펙의 걷기는 전부 스냅샷의 `asks`/`bids` 를 그대로 입력으로 쓴다(§3.1). `core/premium.py` 도 003 이 만든 것을 **import 해서 쓴다**(복사·재정의 금지).
- 하지 않는 것: FE 없음. Influx 읽기(005). 입출금 상태 수집(006) — 여기서는 스냅샷에 들어 있는 값을 **읽기만** 한다. 거래소 REST 호출 0회.
- 바꾸는 기존 것: 라우터 등록뿐.

## 3. 동작

### 3.0 공통 규칙
- 모든 응답은 메모리 스냅샷(001 — WebSocket 으로 실시간 교체)만 읽는다. 스냅샷 1개 = `(exchange, base)` 당 `quote`(KRW 또는 USDT)·`price`(최근 체결가)·`asks`(오름차순)·`bids`(내림차순 — 단계 수는 업비트 최대 30·빗썸 최대 15·바이낸스 최대 20)·`deposit_enabled`·`withdrawal_enabled`(각 `true/false/null`)·`updated_at`.
- 환율 = 국내 거래소별 KRW-USDT `ask`(USDT 살 때)·`bid`(USDT 팔 때). 기준 국내 거래소 = `upbit`. 해외 거래소는 `binance` 1곳.
- **HTTP JSON 키와 복합어 쿼리 파라미터는 camelCase**다. Python 내부 필드는 snake_case를 유지한다.
- 공통 꼬리 필드: `dataReceivedAt`(마지막 틱 시각 ms — 001 의 `received_at`, 비었으면 `null`). `fetchedAt`(응답 생성 시각 ms).
- 에러는 `{"error": {"code", "message", "detail"}}`. 쿼리 타입/범위 위반은 FastAPI 기본 422. 코드: `invalid_symbol`(400). `invalid_request`(400). `unsupported_exchange`(404, 레지스트리에 없는 거래소 id). `no_arbitrage_opportunity`(409).
- `market_data_not_found`(404) = 스냅샷/환율/호가가 메모리에 없음. 의미는 "아직 수집 안 됨 또는 미상장". message 에 "스트림이 첫 스냅샷을 받았는지 확인" 안내.
- `sym`/`symbol` 은 대소문자 무관(대문자로 정규화). `symbol` 형식은 `BASE/QUOTE`(`-`·`_` 구분자도 허용, 조각 2개가 아니면 `invalid_symbol`). 요청한 quote 가 저장된 quote 와 다르면 `market_data_not_found` 에 "`BASE/<저장 quote>` 로 다시 요청하세요".
- 모든 계산은 **수수료·출금 수수료·전송 시간 미반영 이론값**이다. slippage·arbitrage·scan 은 `warnings` 마지막에 항상 그 문장을 넣는다 — matrix 만 §3.2 순서대로 그 문장 **뒤에** 입출금 경고가 온다. warnings 는 `list[str]`, 순서 고정(각 절 참고).
- `depth` 파라미터(orderbook·slippage·arbitrage)는 ≥1 이고 상한은 **저장된 단계 수** — 넘기면 저장분 전부를 쓴다.
- 스캔·매트릭스 제외 코인: 현재 `AI`·`PROS`·`MANTRA` — 서로 다른 코인이 같은 티커를 써서 국내·해외 매칭이 틀린다(MANTRA 는 2026-08-30 실측에서 스캔 1위 +40.8% 로 확인).

### 3.1 계산 규칙
호가창 소진(walk). levels 는 체결되는 쪽 호가(살 때 asks, 팔 때 bids), 최우선부터. 금액(quote 통화) 기준으로 사거나, 수량 기준으로 판다. 결과 = 체결 수량·체결 금액·먹은 단계 수·소진 여부.

**어느 목록을 걷는가.** 걷기의 입력은 스냅샷의 `asks`/`bids` 그 자체다 — 001 §3.3 대로 세 거래소 모두 WebSocket 호가를 받아 업비트 최대 30·빗썸 최대 15·바이낸스 최대 20단계가 들어 있다. 표면값(최우선 1단계 = `asks[0]`·`bids[0]`)과 걷기가 같은 목록을 보므로 한 응답 안에서 출처가 갈리지 않는다.
1. 단계를 최우선부터 순서대로 먹는다.
2. 금액 기준 — 한 단계의 `price×size` 가 남은 금액 이상이면 그 단계에서 `남은 금액/price` 만큼 **부분 체결**하고 끝(`exhausted=false`). 모든 단계를 먹어도 남으면 `exhausted=true` 이고 `amount` 는 요청액이 아니라 **실제 체결액**.
3. 수량 기준도 대칭(부족하면 `quantity` = 실제 체결량).
4. 입력 ≤ 0 또는 빈 호가 → 체결 0. `average_price = amount/quantity`(수량 0 이면 0).
5. `slippage_percent = max(0, (average − best)/best × 100)`. 매도는 부호 반전(불리한 쪽이 양수). best ≤ 0 이면 0.
김프 수식(`core/premium.py` 의 `premium_percent(*, buy_krw, sell_krw)` = `(sell_krw/buy_krw − 1) × 100`):
- 해외 가격을 원화로 바꿀 때 fwd(해외 매수→국내 매도)는 **환율 ask**(원화로 USDT 를 산다), rev(국내 매수→해외 매도)는 **환율 bid**. 호가 측은 "살 때 ask, 팔 때 bid".
- fwd = `premium_percent(buy_krw=fx_ask×rate_ask, sell_krw=dom_bid)`. rev = `premium_percent(buy_krw=dom_ask, sell_krw=fx_bid×rate_bid)`.
- 두 방향은 부호 반전이 아니다(해외 100×1000 vs 국내 105,000 → fwd +5%, rev −4.762%).

### 3.2 엔드포인트

#### `GET /orderbook/{exchange}`
- 파라미터: `symbol`(필수, `BASE/QUOTE`). `depth` 기본 10.
- 오류: 미등록 거래소 404. 형식 400 `invalid_symbol`. 스냅샷 없음·quote 불일치 404.
1. 저장된 호가(`asks`/`bids`, §3.1)를 `depth` 단계까지 잘라 돌려준다(자르기만, 계산 없음) — 슬리피지가 실제로 소비하는 호가를 그대로 보여주는 것이 이 엔드포인트의 쓸모다. 응답 키: `exchange·symbol·base·quote`, `bids/asks[{price,size}]`, `timestamp`(스냅샷의 내부 `price_timestamp` = 거래소 시세 시각 ms — 001 계약에 호가 전용 시각은 없다), `dataUpdatedAt`.
#### `GET /slippage/{exchange}`
- 파라미터: `symbol`(필수). `side` 는 `buy`|`sell`, 기본 buy. `amount` **또는** `quantity`(정확히 하나, >0). `depth` 기본 100.
- 오류: amount·quantity 둘 다/둘 다 없음/≤0 → 400 `invalid_request`(스냅샷 조회보다 먼저). 호가 비어 있음 404. 최소 단위도 체결 안 됨 400.
1. 한 거래소·한 방향을 `depth` 단계 호가로 walk 한다(살 때 asks, 팔 때 bids). `depthAvailable` 은 저장된 단계 수이고 `bestPrice` 는 최우선이다. 응답 키: `exchange·name·symbol·quoteCurrency·side`, `requestedAmount`/`requestedQuantity`(안 준 쪽은 `null`), `bestPrice`(최우선), `averagePrice`, `quantity`/`amount`(실제 체결량/액), `slippagePercent`, `levelsConsumed`, `depthExhausted`, `depthAvailable`(저장된 단계 수), `dataUpdatedAt`, 공통 꼬리 필드, `warnings`.
2. 예: asks `[(100,1),(120,10)]`, `amount=220` → 수량 2.0, 평균 110, 슬리피지 10%, 2단계.
3. warnings 순서: (a) 1단계 안에서 끝나면 "슬리피지 0, 규모를 키우면 생김" (b) 항상 "메모리 스냅샷 기준, 타이밍 슬리피지 미반영".
#### `GET /arbitrage`
- 파라미터: `sym`(필수). `amount`(필수, KRW, >0). `depth` 기본 100.
- 오류: 스냅샷 0개/기준 환율 없음 404. 후보 <2 또는 매수처=매도처 409.
1. `sym` 스냅샷 전부 수집(0개면 404) → 기준 환율(upbit) 필수(없으면 404).
2. 후보 풀: quote 가 KRW/USDT 가 아니면 제외. 호가 한쪽이라도 비면 `failures[]` 후 제외.
3. 각 후보의 호가를 **KRW 로 환산**한다. 국내 거래소는 자기 환율, 해외는 기준 환율. 환전도 체결되는 쪽 호가를 쓴다: USDT 가격→KRW 표시는 살 때 rate ask / 팔 때 rate bid. `candidates[]` 는 후보마다 `exchange·name·bestBidKrw·bestAskKrw·depthLevels` 를 싼 순(best ask)으로.
4. 후보 <2 → 409(detail 에 성공/실패 목록). 매수처 = 최저 ask, 매도처 = 최고 bid, 같은 거래소면 409.
5. 매수처 asks 를 `amount` 만큼 금액 walk(체결 0 → 400). 그 **체결 수량**으로 매도처 bids 를 수량 walk. **매도측이 소진돼 못 판 수량이 있으면 판 수량만큼 매수측을 되맞춘다**(matrix 와 동일). 못 판 코인을 0원으로 치면 −50% 대 쓰레기 값이 나오기 때문이다.
6. `buy`/`sell` 각각 `exchange·name·averagePriceKrw·amountKrw·slippagePercent·levelsConsumed·depthExhausted·dataUpdatedAt`(슬리피지는 환산 호가 최우선가 대비). 최상위에 `sym·quantity`(실제 판 수량)·`usdKrwRate`(기준 환율, 표시용).
7. `profitKrw` = 매도 수취 − 매수 지불(KRW). `profitPercent` = 이익/지불×100. `premiumPercent` = 환산 최우선가 기준 (매도 bid/매수 ask − 1)×100. `premiumCapturePercent` = profitPercent/premiumPercent×100(분모 0 → 0). `inputAmountKrw` = `amount`.
8. `withdrawalAvailable` = 매수처 출금 상태, `depositAvailable` = 매도처 입금 상태. **`null` 은 "모름"** — 숨기지 않고 경고한다.
9. warnings 순서: (a) 매수측 소진 "투입 금액 중 X원만 체결" (b) 매도측 소진 "매도 가능 수량만큼 매수를 되맞춤" (c) 손해면 "가장 유리한 조합조차 손해" (d) 출금 `false` → "막혀 있음, 실행 불가", `null` → "확인 못 함, 열려 있다고 가정하지 말 것" (e) 입금 동일 (f) `inputAmountKrw` > 호가 저장 한도 10억원 → "슬리피지가 실제보다 작게 계산됐을 수 있음" (g) 항상 수수료 미반영 문구.
#### `GET /premium`
- 파라미터: `sym`(필수). `dom` 기본 upbit.
- 오류: `dom` 이 해외 거래소 400 `invalid_request`(message 에 선택 가능 목록). 국내 KRW 스냅샷/환율/국내 호가 없음 404. binance USDT 스냅샷/호가 없음 404.
1. 국내 거래소 1곳(`dom`) 대 binance 를 **최우선 호가 1단계**만으로 §3.1 수식으로 fwd·rev 양방향 계산한다. `dom` 은 원화 거래소여야 한다(아니면 400). 국내 스냅샷은 KRW 마켓이어야 하고(아니면 404) 그 거래소의 환율이 있어야 한다(없으면 404).
2. 최상위 `sym·dom·domPrice·fx`. `fwd`·`rev` 항목: `usd`(해외 가격)·`usdKrwRate`(fwd ask / rev bid)·`rateUpdatedAt`·`premiumPercent·premiumKrw`(원화 차액)·`profitable`(>0)·`dataUpdatedAt`.
3. `bestDirection` = `premiumPercent` 가 큰 쪽. 둘 다 손해면 덜 나쁜 쪽. `bestPremiumPercent` 는 그 값.
#### `GET /premium/scan`
- 파라미터: `dom` 기본 upbit. `limit` 기본 10, 1~100.
- 오류: 환율 없음 404(스냅샷 검사보다 먼저). 국내 스냅샷 0개 404. binance 스냅샷 0개 404.
1. 국내 = `dom` 의 KRW 스냅샷. 해외 = binance 의 USDT 스냅샷.
2. 코인 순으로 국내 상장 코인만 짝짓고(`scannedCoins`=코인 수, `scannedPairs`=짝 수) 방향별 §3.1 수식을 1단계 호가로 계산한다.
3. 제외 코인(§3.0)은 건너뛰고 `excludedBases` 에 표시한다. 항목: `sym·direction·dom·domPrice·fx·fxName·usd·premiumPercent·premiumKrw·liquidityKrw·suspicious·suspicionReason`. `liquidityKrw` = 양쪽 1단계 체결 가능 금액 중 작은 쪽(원화).
4. **`|premiumPercent| ≥ 5%` 면 `suspicious=true`** — 이유 문구: 이름만 같은 다른 코인이거나 한쪽 입출금 중단 가능성, 거래 전 확인.
5. `bestFwd`/`bestRev` = 방향별 최대. `topFwd`/`topRev` = 수익률 내림차순 상위 `limit` 개. `suspiciousCount` 는 양방향 합. 최상위 `dom·fx·usdKrwRate`(표시용 ask)·`rateUpdatedAt`.
6. warnings 순서: 1위가 의심이면 "김프/역김프 1위 X 는 의심 항목" → 1위 유동성 < 100만원이면 "체결 가능 금액이 N원뿐" → 항상 "1단계만 보므로 금액 기준은 /matrix 나 /arbitrage 로".
#### `GET /matrix`
- 파라미터: `amountKrw` 기본 10,000,000, >0.
- 오류: 스냅샷 0개 404. 환율 0개 404.
1. 국내(KRW)와 해외(USDT) 양쪽에 있는 모든 코인 × (국내 거래소 × 해외 거래소) 격자에서 코인별 **최대 김프 조합·최대 역프 조합** 을 `amountKrw` 로 walk 한다.
2. 환율은 그 국내 거래소 것. 없으면 **그 국내 거래소 조합은 건너뛴다** — 남의 테더 프리미엄을 빌리지 않는다. 제외 코인은 scan 과 동일.
3. 조합마다 해외 호가를 원화로 환산한다(asks 는 rate ask, bids 는 rate bid). `premiumPercent` = 1단계 표면 김프(금액 무관). 이것이 최대 조합 선정 기준이다.
4. 매수 asks 금액 walk(체결 0 이면 조합 없음) → 체결 수량으로 매도 bids 수량 walk. **매도측이 소진돼 못 판 수량이 있으면 판 수량만큼 매수측을 되맞춘다**(arbitrage 와 동일). 못 판 코인을 0원으로 치면 −50% 대 쓰레기 값이 나오기 때문이다.
5. `totalSlippagePercent` = 표면 김프 − 실효 수익률((매도액/매수액 − 1)×100).
6. 방향 항목: `buyExchange·sellExchange·premiumPercent·totalSlippagePercent·withdrawalAvailable`(매수처 출금)·`depositAvailable`(매도처 입금)·`depthExhausted`.
7. 코인 행(`coins[]`): `sym·fwd·rev(없으면 null)·suspicious`(fwd ≥ 5%). 둘 다 없으면 행 제외. 정렬 = fwd 김프 내림차순(fwd 없는 행은 맨 뒤). 최상위 `amountKrw`, `scannedCoins`=행 수, `scannedCombinations`=걸어본 조합 수, `domList`/`fxList` 정렬.
8. warnings 순서: 한도 10억원 초과 → 항상 수수료 미반영 → 어느 방향이든 출금·입금이 둘 다 `true` 가 아닌 조합이 있으면 "입출금 막힘 표시 조합 있음 — 실제 중단일 수도, 확인 못 한 것일 수도(`null`)".

### 3.3 엣지 모음
- 스트림이 아직 안 붙었거나(메모리 빔) 미상장 코인 → 전부 `market_data_not_found` 404. 스냅샷은 있는데 호가가 빈 경우: 단일 대상(orderbook·slippage·premium)은 404. 다수 후보(arbitrage)는 `failures[]` 로 내리고 계속. scan·matrix 는 그 짝/조합만 조용히 건너뛴다.
- `amount` 가 저장 깊이를 넘으면 오류가 아니라 `depthExhausted=true` + 실제 체결분 계산 + 경고. 호가 저장 한도(10억원)를 넘는 금액은 경고만.
- 입출금 상태 `null` = 모름. 응답에서 `null` 그대로 내보내고 경고한다. 절대 `true` 로 가정하지 않는다.
- 환율 ask=bid 인 거래소는 단일 환율 계산과 동일한 결과가 나온다.

## 4. 검증

**깊이 반영 (012 스트림)**
- 바이낸스 행의 `asks` 가 20단계면 `/orderbook/binance` 의 `asks` 도(`depth` 로 자르기 전) 20개이고, `/slippage/binance` 의 `depthAvailable` 이 그 길이와 같다
- 다단계 바이낸스 행에서 규모를 키우면 `slippagePercent` 가 0 에서 양수가 된다 — 1단계뿐이면 평균가가 곧 최우선가라 어떤 규모에도 0 이다(이 항목이 회귀를 잡는다)
- `/arbitrage`·`/matrix` 의 해외 다리가 바이낸스 20단계를 걷는다 — 1단계 시드와 20단계 시드의 실효 수익률이 다르다

테스트 입력을 스펙이 고정하는 의도적 예외 — 수식 검증 가능한 기대값을 주기 위해.

표준 시드 (아래 검증 항목의 입력값, 메모리 스냅샷에 심는 값). 시각은 모두 `1700000000000`(ms).
- 가격(`price`): upbit(KRW) BTC 100,000,000 / ETH 5,000,000 / XRP 1,400. bithumb(KRW) BTC 100,100,000 / XRP 1,402. binance(USDT) BTC 71,000 / ETH 3,550 / XRP 0.99 / SOL 150.
- SOL 은 국내 미상장(격자에서 빠지는지 확인용). 환율: upbit·bithumb 모두 ask = bid = 1,400. 단일 환율 시절과 결과가 같아야 하므로 일부러 벌리지 않는다. 방향별 환율 분리를 확인하는 항목만 ask/bid 를 따로 심는다.
- 호가: 각 스냅샷 5단계. i단계(1~5) ask = 가격×(1+0.0005×i), bid = 가격×(1−0.0005×i). 단계마다 size 는 원화 환산 체결 가능 금액이 300만원이 되게 둔다(USDT 마켓은 가격×1,400 으로 환산). 슬리피지 기대값을 손으로 계산하기 쉽게 한 고정값이다.
- 입출금: upbit·binance 는 입금·출금 `true`. bithumb 은 입금·출금 모두 `false`(막힌 상황). `null` 은 이 시드로 덮지 않고 해당 항목이 직접 행을 만든다.
- 시드로부터 나오는 수치: BTC fwd(upbit↔binance) = (99,950,000 / 99,449,700 − 1)×100 = **+0.503%**. BTC rev = (99,350,300 / 100,050,000 − 1)×100 = **−0.699%**. binance 매수→bithumb 매도 표면 김프 = **+0.604%**(정확값 0.60357%). XRP fwd 는 bithumb 쪽이 **+1.053%** 로 시드 최대.
검증 항목:
- 금액 walk: 금액이 단계 중간에서 끝나면 부분 체결·`exhausted=false`; 전 단계를 넘으면 `exhausted=true` 이고 `amount` 는 실제 체결액
- 수량 walk: 수량 부족 시 `quantity` 는 실제 체결량
- asks `[(100,1),(120,10)]` 에 `amount=220` → 수량 2.0, 평균 110, slippage 10%, 2단계
- 매도 슬리피지는 평균가가 최우선가보다 낮을 때 양수, 0 미만이면 0
- `/orderbook` 은 `depth` 만큼만 자르고 저장 순서를 유지한다; 저장 단계 수를 넘는 `depth` 는 저장분 전부; quote 불일치는 404 에 저장 quote 안내
- `/slippage` 에 amount·quantity 둘 다 또는 둘 다 없음 → 400(빈 메모리여도 400이 먼저)
- `/slippage` 1단계 안에서 끝나면 slippage 0 + "규모를 키우면" 경고; 소진 시 `depthExhausted=true`
- `/arbitrage` 자동 선택: 표준 시드에서 binance 매수 → bithumb 매도, `premiumCapturePercent=100`
- `/arbitrage` 후보 1곳(해외만 상장) → 409
- `/arbitrage` 입금 `false` 인 매도처(표준 시드 bithumb) → `depositAvailable=false` + 경고, `null` → `null` + "확인 못 함" 경고
- `/arbitrage` 매수측 환율은 거래소별(국내 자기 환율, 해외 기준 환율), ask≠bid 면 각 다리에 맞는 쪽을 쓴다
- `/arbitrage` 매도측 소진 시 매수측을 되맞춰 실효 수익률이 −50% 대로 떨어지지 않는다(`quantity` = 실제 판 수량)
- `/premium` fwd 는 rate ask, rev 는 rate bid; 표준 시드 BTC fwd +0.503%, rev −0.699% (부호 반전 아님)
- `/premium?dom=binance` → 400; 국내 스냅샷 없음·환율 없음·binance 스냅샷 없음 → 404
- `/premium` 의 `bestDirection` 은 둘 다 손해여도 덜 나쁜 쪽
- `/premium/scan` BTC 항목의 수치가 `/premium` fwd 와 같다; `|premium| ≥ 5%` 는 `suspicious=true`; 제외 코인은 빠지고 `excludedBases` 에 보인다
- `/premium/scan?limit=N` 은 수익률 내림차순 상위 N 개
- `/matrix` 표준 시드: 행은 BTC·ETH·XRP(SOL 제외), BTC fwd = binance→bithumb, `depositAvailable=false`, 첫 행 XRP, 조합 5개(BTC 2×1 + ETH 1×1 + XRP 2×1); `amountKrw=50,000,000` 이면 `depthExhausted=true`(한쪽 5단계 합 1,500만원)
- `/matrix` 매도측 소진 시 매수측을 되맞춰 실효 수익률이 −50% 대로 떨어지지 않는다; 환율 없는 국내 거래소 조합은 빠진다
- 모든 분석 응답 키는 camelCase이고 에러 본문은 `{"error":{code,message,detail}}`

실서버 확인 (기동 후 스트림 스냅샷이 온 뒤 — 수 초 — 실제 호출):
- `/orderbook/upbit` BTC/KRW `depth=3` → `quote=="KRW"`, asks 3단계
- `/slippage/upbit` BTC/KRW `amount=1,000,000` → `slippagePercent ≥ 0`, `levelsConsumed ≥ 1`
- `/slippage/upbit` BTC/KRW amount·quantity 없이 → 에러 코드 `invalid_request`
- `/arbitrage` BTC `amount=1,000,000` → `profitKrw` 있음, `buy.exchange ≠ sell.exchange`
- `/premium` BTC → `fwd`·`rev` 둘 다 `premiumPercent` 있음
- `/premium/scan?limit=5` → `topFwd` 5개 이하, `scannedCoins > 0`
- `/matrix` `amountKrw=1,000,000` → `scannedCoins == len(coins)`
- `/premium` BTC `dom=binance` → 400. `/orderbook/coinbase` → 404. `/matrix?amountKrw=-1` → 422

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)

```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q
```
- `All checks passed!`
- `181 files left unchanged`
- `419 passed, 1 warning in 4.71s` — 이 브랜치 기준선은 `417 passed`, 늘어난 2개가 §4 "깊이 반영" 셋째 항목(`/arbitrage`·`/matrix` 해외 다리 20단계)이다. analysis 폴더만은 `56 passed`.

회귀를 실제로 잡는지 확인했다 — `core/orderbook.py` 의 걷기 목록을 최우선 1단계로 잠깐 자르자 `test_twenty_levels.py` 4개가 전부 깨졌고, 원상 복구 후 다시 통과.

```bash
cd server && .venv/bin/python -m uvicorn app.main:app --port 8041   # 거래소 도메인이 막힌 로컬 망 — 스냅샷 없는 상태의 오류 경로만
curl -s localhost:8041/health                                       # {"status":"ok","version":"0.1.0"}
curl -s 'localhost:8041/premium?sym=BTC&dom=binance'                # 400 invalid_request (선택 가능: upbit, bithumb)
curl -s 'localhost:8041/orderbook/coinbase?symbol=BTC/KRW'          # 404 unsupported_exchange
curl -s -o /dev/null -w '%{http_code}' 'localhost:8041/matrix?amountKrw=-1'   # 422
curl -s 'localhost:8041/slippage/upbit?symbol=BTC/KRW'              # 400 invalid_request (amount 또는 quantity 중 정확히 하나)
curl -s 'localhost:8041/orderbook/upbit?symbol=BTC/KRW&depth=3'     # 404 market_data_not_found — message 에 "스트림이 첫 스냅샷을 받았는지 확인하세요"
```
프로세스는 확인 뒤 종료했다. web 은 건드리지 않았다(lint·build 생략).

**EC2 에서 확인 필요** — §4 "실서버 확인" 중 스냅샷이 있어야 하는 항목(`/orderbook/upbit` depth=3 asks 3단계, `/slippage/upbit` amount=1,000,000 의 `slippagePercent ≥ 0`·`levelsConsumed ≥ 1`, `/arbitrage` BTC 의 `profitKrw`·`buy.exchange ≠ sell.exchange`, `/premium` BTC 의 fwd·rev, `/premium/scan?limit=5`, `/matrix?amountKrw=1000000` 의 `scannedCoins == len(coins)`)은 이 망에서 거래소 WebSocket 이 막혀 돌리지 못했다(dev-setup.md 로컬 메모). 응답 키·타입은 이 세션에서 바뀌지 않았다.

## 6. 갱신할 문서
- `docs/context/status.md` — analysis 행을 `| analysis | 6개 엔드포인트 동작 | - | HTTP 계약 camelCase |` 로. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 004 행 상태 → DONE. **항상 포함.**
- `docs/context/architecture.md` — 계약 규칙의 camelCase 원칙과 이 스펙 6개 경로(`/premium` `/premium/scan` `/matrix` `/orderbook/*` `/slippage/*` `/arbitrage`)가 일치하는지 확인. + "현재 구조" 절에 analysis 항목: `core/orderbook.py`(호가창 소진 순수 계산 — 003 과 공용) + `features/analysis/` — `service.py`(6개 빌더·거래소 레지스트리)·`router.py`·`models.py`, web 없음.
- `docs/context/dev-setup.md` — 검증용 스모크에 `/slippage` 1줄: `curl "localhost:8000/slippage/upbit?symbol=BTC/KRW&amount=1000000"` → `slippagePercent ≥ 0`·`levelsConsumed ≥ 1`.

## 7. 실행 보고 (실행 세션이 채움)

재구축(001 스트림 파이프라인) 뒤 이 스펙을 다시 확인한 세션이다. 6개 엔드포인트·모델·라우터는 이미 스트림 스냅샷(`asks`/`bids` 그대로)을 걷고 있어 코드 변경은 문구 하나와 테스트 2개뿐이다.

- 만든 것 (파일 목록):
  - `server/app/features/analysis/service.py` — `market_data_not_found` 의 안내 문구를 §3.0 대로 "스트림이 첫 스냅샷을 받았는지 확인하세요" 로. 모듈 docstring 의 "1초 수집" 도 "WebSocket 으로 실시간 교체" 로.
  - `server/app/features/analysis/models.py` — `data_received_at` 주석을 "마지막 틱 시각(001 의 `received_at`)" 으로.
  - `server/app/features/analysis/tests/test_orderbook_api.py` — 옛 문구를 단언하던 1줄을 새 문구로.
  - `server/app/features/analysis/tests/test_twenty_levels.py` — §4 "깊이 반영" 셋째 항목 테스트 2개 추가(`/arbitrage`·`/matrix`). 같은 코인을 바이낸스 1단계 시드와 20단계 시드로 두 번 심고, 국내(upbit) 쪽은 1단계에 10 BTC 를 둬 해외 다리만 여러 단계를 먹게 했다 — 1단계 시드는 `depthExhausted=true`·슬리피지 0, 20단계 시드는 `depthExhausted=false`·슬리피지 양수·실효 수익률이 더 낮다. matrix 는 fwd(asks)·rev(bids) 양쪽을 단언하고 표면 김프는 두 시드에서 같음을 함께 확인한다.
  - `docs/context/status.md`(재구축 안내 문단에서 004 제외)·`CLAUDE.md`(004 DONE, 재구축 순서 005 → 006 → 007)·`docs/context/architecture.md`("현재 구조" analysis 항목), 이 문서 머리·§5·§7.
- 추측한 지점 (묻지 않고 정한 것) / 실행 중 함께 고친 스펙 절:
  - **`core/orderbook.py` 의 `walk_levels(row, side)` 를 남겼다.** §2 는 이제 이 함수를 이름으로 부르지 않고 "걷기는 스냅샷의 `asks`/`bids` 그대로" 라고만 말한다. 함수는 정확히 그것(`row.asks` 또는 `row.bids`)을 돌려주는 통과 함수이고 003 의 spreads service 가 import 하고 있어, 지우면 003 코드를 건드리게 된다(범위 밖). 동작 차이가 없으므로 스펙 본문에는 적지 않았다.
  - **실서버 확인을 두 갈래로 나눴다.** 스냅샷이 필요 없는 오류 경로 4개(+빈 저장소의 404)는 빈 포트 8041 에 띄워 로컬에서 돌렸고, 스냅샷이 필요한 항목은 §5 에 "EC2 에서 확인 필요" 로 남겼다.
  - **테스트 파일 이름은 `test_twenty_levels.py` 그대로.** 이전 보고가 부르던 `test_depth_stream.py` 는 트리에 없다 — 001 재구축 때 정리된 이름이고, 지금 파일이 §4 "깊이 반영" 세 항목을 전부 담는다.
  - **§7 의 이전 세션 서술(이관 세션·깊이 반영 세션)을 지웠다.** `depth_*`·"표면값은 REST" 등 지금 스펙·코드에 없는 구조를 말하고 있어 남기면 다음 세션이 잘못 읽는다. 과거 판단은 git 에 있다(CLAUDE.md §4).
- 보고만 하는 어긋남 (담당 아닌 스펙 — CLAUDE.md §5): 없음. 003 스펙은 `walk_levels` 를 더 이상 언급하지 않아 코드와 어긋나지 않는다.
- 남은 빚:
  - `core/orderbook.py` 는 `server/tests/` 직접 단위 테스트가 없다 — 걷기 4함수와 `walk_levels` 는 `/orderbook`·`/slippage`·`/arbitrage`·`/matrix`·`/spreads` HTTP 응답으로만 검증한다.
  - §4 실서버 확인의 스냅샷 필요 항목은 EC2 대기(§5).
  - `/matrix` 의 `totalSlippagePercent` 는 표면 김프(1단계) − 실효 수익률이라, 매수측이 1단계 안에서 소진되면 실효 = 표면이 되어 0 이다 — 소진은 `depthExhausted` 로만 드러난다(새 테스트의 1단계 시드가 이 경우다). 스펙이 의도한 정의이고 경고는 두지 않았다.
