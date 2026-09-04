# 004 — analysis

상태: DONE | 의존: 001(collect — 메모리 스냅샷·환율), 003(spreads — `core/premium.py` 의 `premium_percent`)

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
스프레드 표에서 고른 **코인 1개를 깊이 파는** 분석 API 6개를 만든다. 트레이더가 "이 코인을 이 금액만큼 실제로 옮기면 호가를 얼마나 먹고, 얼마가 남는가"를 호가창 기준으로 확인하고, 전종목 스캔·매트릭스로 후보를 찾는다. 화면 없음 — curl/브라우저로 직접 호출하는 BE 전용 도구.

## 2. 범위
- 만드는 것: 기능 폴더 `analysis` (server 만). 엔드포인트 6개 — `GET /orderbook/{exchange}` `GET /slippage/{exchange}` `GET /arbitrage` `GET /premium` `GET /premium/scan` `GET /matrix`.
- 호가창 소진(walk) 계산은 `core/orderbook.py` 에 산다 — 003(spreads)과 이 스펙이 함께 쓰는 공유 모듈이다(기능 간 import 금지, CLAUDE.md §2). **함수는 전부 동기다**(003 §2 의 근거). 단계 목록을 고르는 `walk_levels(row, side)` 도 이 모듈의 공개 함수이고, 이 스펙의 **모든 걷기가 그것을 거친다**(§3.1). `core/premium.py` 도 003 이 만든 것을 **import 해서 쓴다**(복사·재정의 금지).
- 하지 않는 것: FE 없음. Influx 읽기(005). 입출금 상태 수집(006) — 여기서는 스냅샷에 들어 있는 값을 **읽기만** 한다. 거래소 REST 호출 0회.
- 바꾸는 기존 것: 라우터 등록. 003 이 spreads 서비스 안에 두고 쓰던 단계 선택 로직을 `core/orderbook.py` 의 `walk_levels` 로 올리고, 003 도 그것을 쓰게 한다(같은 규칙이 두 벌 존재하지 않게).

## 3. 동작

### 3.0 공통 규칙
- 모든 응답은 메모리 스냅샷(1초 수집)만 읽는다. 스냅샷 1개 = `(exchange, base)` 당 `quote`(KRW 또는 USDT)·`price`(최근 체결가)·`asks`(오름차순)·`bids`(내림차순)·`deposit_enabled`·`withdrawal_enabled`(각 `true/false/null`)·`updated_at`.
- 환율 = 국내 거래소별 KRW-USDT `ask`(USDT 살 때)·`bid`(USDT 팔 때). 기준 국내 거래소 = `upbit`. 해외 거래소는 `binance` 1곳.
- **HTTP JSON 키와 복합어 쿼리 파라미터는 camelCase**다. Python 내부 필드는 snake_case를 유지한다.
- 공통 꼬리 필드: `dataReceivedAt`(수집 루프가 마지막으로 교체한 시각 ms, 비었으면 `null`). `fetchedAt`(응답 생성 시각 ms).
- 에러는 `{"error": {"code", "message", "detail"}}`. 쿼리 타입/범위 위반은 FastAPI 기본 422. 코드: `invalid_symbol`(400). `invalid_request`(400). `unsupported_exchange`(404, 레지스트리에 없는 거래소 id). `no_arbitrage_opportunity`(409).
- `market_data_not_found`(404) = 스냅샷/환율/호가가 메모리에 없음. 의미는 "아직 수집 안 됨 또는 미상장". message 에 "수집 루프가 한 사이클 돌았는지 확인" 안내.
- `sym`/`symbol` 은 대소문자 무관(대문자로 정규화). `symbol` 형식은 `BASE/QUOTE`(`-`·`_` 구분자도 허용, 조각 2개가 아니면 `invalid_symbol`). 요청한 quote 가 저장된 quote 와 다르면 `market_data_not_found` 에 "`BASE/<저장 quote>` 로 다시 요청하세요".
- 모든 계산은 **수수료·출금 수수료·전송 시간 미반영 이론값**이다. slippage·arbitrage·scan 은 `warnings` 마지막에 항상 그 문장을 넣는다 — matrix 만 §3.2 순서대로 그 문장 **뒤에** 입출금 경고가 온다. warnings 는 `list[str]`, 순서 고정(각 절 참고).
- `depth` 파라미터(orderbook·slippage·arbitrage)는 ≥1 이고 상한은 **저장된 단계 수** — 넘기면 저장분 전부를 쓴다.
- 스캔·매트릭스 제외 코인: 현재 `AI`·`PROS`·`MANTRA` — 서로 다른 코인이 같은 티커를 써서 국내·해외 매칭이 틀린다(MANTRA 는 2026-08-30 실측에서 스캔 1위 +40.8% 로 확인).

### 3.1 계산 규칙
호가창 소진(walk). levels 는 체결되는 쪽 호가(살 때 asks, 팔 때 bids), 최우선부터. 금액(quote 통화) 기준으로 사거나, 수량 기준으로 판다. 결과 = 체결 수량·체결 금액·먹은 단계 수·소진 여부.

**어느 목록을 걷는가.** 걷기의 입력은 항상 `core/orderbook.py` 의 `walk_levels(row, side)` 가 돌려주는 목록이다(003 §2). 그 방향의 `depth_*` 가 있으면 그것(해외는 012 스트림이 살아 있으면 최대 20단계), 없으면 `asks`/`bids` 다. 국내 행은 `depth_*` 가 항상 비어 있어 자기 `asks`/`bids`(업비트 30·빗썸 15단계)를 쓴다.

**최우선 1단계만 읽는 곳은 `walk_levels` 를 쓰지 않는다** — `row.asks[0]`·`row.bids[0]` 를 직접 읽는다. `/premium`·`/premium/scan`·`/matrix` 의 표면 김프(`premium_percent` 입력)와 호가 유무 판정이 여기 해당한다. 012 가 REST 최우선 호가를 그대로 남긴 이유가 조용한 종목의 헤드라인이 스트림 정체로 낡지 않게 하는 것이라, 표면값은 REST 기준으로 고정한다.

즉 한 응답 안에서 **표면값은 REST 최우선, 체결 비용은 스트림 깊이**를 본다. 이 둘의 출처가 다르다는 것은 의도된 설계다.
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
1. **걷는 목록**(`walk_levels`, §3.1)을 `depth` 단계까지 잘라 돌려준다(자르기만, 계산 없음) — 슬리피지가 실제로 소비하는 호가를 그대로 보여주는 것이 이 엔드포인트의 쓸모다. 응답 키: `exchange·symbol·base·quote`, `bids/asks[{price,size}]`, `timestamp`(스냅샷의 내부 `price_timestamp` = 거래소 시세 시각 ms — 001 계약에 호가 전용 시각은 없다), `dataUpdatedAt`. 응답 키·타입은 불변이다. 바이낸스는 스트림이 살아 있으면 단계 수가 1 에서 최대 20 으로 늘고, 그 최우선 단계는 REST 최우선 호가와 1초 안쪽으로 다를 수 있다(출처가 다르다 — §3.1).
#### `GET /slippage/{exchange}`
- 파라미터: `symbol`(필수). `side` 는 `buy`|`sell`, 기본 buy. `amount` **또는** `quantity`(정확히 하나, >0). `depth` 기본 100.
- 오류: amount·quantity 둘 다/둘 다 없음/≤0 → 400 `invalid_request`(스냅샷 조회보다 먼저). 호가 비어 있음 404. 최소 단위도 체결 안 됨 400.
1. 한 거래소·한 방향을 `depth` 단계 호가로 walk 한다(살 때 asks, 팔 때 bids — 목록은 `walk_levels`, §3.1). `depthAvailable` 은 **걷는 목록의 단계 수**이고 `bestPrice` 는 그 목록의 최우선이다. 응답 키: `exchange·name·symbol·quoteCurrency·side`, `requestedAmount`/`requestedQuantity`(안 준 쪽은 `null`), `bestPrice`(최우선), `averagePrice`, `quantity`/`amount`(실제 체결량/액), `slippagePercent`, `levelsConsumed`, `depthExhausted`, `depthAvailable`(걷는 목록의 단계 수), `dataUpdatedAt`, 공통 꼬리 필드, `warnings`.
2. 예: asks `[(100,1),(120,10)]`, `amount=220` → 수량 2.0, 평균 110, 슬리피지 10%, 2단계.
3. warnings 순서: (a) 1단계 안에서 끝나면 "슬리피지 0, 규모를 키우면 생김" (b) 항상 "메모리 스냅샷 기준, 타이밍 슬리피지 미반영".
#### `GET /arbitrage`
- 파라미터: `sym`(필수). `amount`(필수, KRW, >0). `depth` 기본 100.
- 오류: 스냅샷 0개/기준 환율 없음 404. 후보 <2 또는 매수처=매도처 409.
1. `sym` 스냅샷 전부 수집(0개면 404) → 기준 환율(upbit) 필수(없으면 404).
2. 후보 풀: quote 가 KRW/USDT 가 아니면 제외. 호가 한쪽이라도 비면 `failures[]` 후 제외.
3. 각 후보의 호가를 **KRW 로 환산**한다. 국내 거래소는 자기 환율, 해외는 기준 환율. 환전도 체결되는 쪽 호가를 쓴다: USDT 가격→KRW 표시는 살 때 rate ask / 팔 때 rate bid. `candidates[]` 는 후보마다 `exchange·name·bestBidKrw·bestAskKrw·depthLevels` 를 싼 순(best ask)으로. 환산 대상은 걷는 목록(`walk_levels`, §3.1)이라 `bestBidKrw`·`bestAskKrw`·`depthLevels` 와 매수·매도처 선정도 그 목록 기준이다 — arbitrage 에는 REST 로 고정하는 표면값이 없다.
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
- 수집 루프가 아직 안 돌았거나(메모리 빔) 미상장 코인 → 전부 `market_data_not_found` 404. 스냅샷은 있는데 호가가 빈 경우: 단일 대상(orderbook·slippage·premium)은 404. 다수 후보(arbitrage)는 `failures[]` 로 내리고 계속. scan·matrix 는 그 짝/조합만 조용히 건너뛴다.
- **호가가 비었는지는 그 절이 보는 목록으로 판정한다** — 걷는 곳(orderbook·slippage·arbitrage)은 `walk_levels` 결과가, 표면값만 보는 곳(premium·scan·matrix)은 REST `asks`/`bids` 가 기준이다. 응답에 실리는 목록과 비었는지 판정하는 목록이 어긋나면 안 되기 때문이다.
- `amount` 가 저장 깊이를 넘으면 오류가 아니라 `depthExhausted=true` + 실제 체결분 계산 + 경고. 호가 저장 한도(10억원)를 넘는 금액은 경고만.
- 입출금 상태 `null` = 모름. 응답에서 `null` 그대로 내보내고 경고한다. 절대 `true` 로 가정하지 않는다.
- 환율 ask=bid 인 거래소는 단일 환율 계산과 동일한 결과가 나온다.

## 4. 검증

**깊이 반영 (012 스트림)**
- `depth_asks` 가 3단계인 바이낸스 행 → `/orderbook/binance` 의 `asks` 가 3개, `depth_*` 가 비면 `asks` 는 REST 1단계
- 같은 행에서 `/slippage/binance` 의 `depthAvailable` 이 `depth_asks` 길이와 같고, `bestPrice` 는 `depth_asks[0][0]` 이다
- `depth_*` 가 있는 행에서 규모를 키우면 `slippagePercent` 가 0 에서 양수가 된다 — 1단계뿐이면 평균가가 곧 최우선가라 어떤 규모에도 0 이다(이 항목이 회귀를 잡는다)
- 국내 거래소 행은 `depth_*` 가 비어 있어 `asks`/`bids` 를 걷는다(단계 수가 REST 그대로)
- **표면값은 REST 를 쓴다**: `depth_asks[0]` 을 REST `asks[0]` 과 다르게 시드해도 `/premium`·`/matrix` 의 표면 김프는 REST 최우선으로 계산된다
- `/arbitrage`·`/matrix` 의 해외 다리가 `depth_*` 를 걷는다 — 깊이를 준 시드와 안 준 시드의 실효 수익률이 다르다

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

실서버 확인 (기동 후 수집 루프 한 사이클 뒤 실제 호출):
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
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m pytest -q
```
- `All checks passed!`
- `172 files already formatted`
- `311 passed, 1 warning in 1.74s` — 깊이 반영 전 이 브랜치 기준선은 `303 passed`, 늘어난 8개가 §4 "깊이 반영" 항목이다.

```bash
cd web && npm run lint && npm run build
```
- `oxlint src` — 출력 없음(exit 0)
- `✓ 44 modules transformed.` / `✓ built in 318ms`

회귀를 실제로 잡는지 확인했다 — `walk_levels` 가 `depth_*` 를 무시하도록 잠깐 되돌리자 새 테스트 5개와 003 의 `test_depth_levels_are_used_when_present` 가 깨졌고, 반대로 `/premium`·`/matrix` 의 표면 김프를 `walk_levels` 로 바꾸자 표면값 항목 2개가 깨졌다. 원상 복구 후 다시 전부 통과.

venv 는 `uv` 로 만든 Python 3.12.13 (`server/.venv`, dev-setup.md §server-3).
실서버 확인(§4 아래 curl 목록)은 이 세션에서 돌리지 않았다 — 로컬 망에서 거래소 호출이 막힌다(dev-setup.md 로컬 메모). HTTP 계약(응답 키·타입)은 이 변경으로 바뀌지 않아 스모크 문장도 그대로다.

## 6. 갱신할 문서
- `docs/context/status.md` — analysis 행을 `| analysis | 6개 엔드포인트 동작 | - | HTTP 계약 camelCase |` 로. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 004 행 상태 → DONE. **항상 포함.**
- `docs/context/architecture.md` — 계약 규칙의 camelCase 원칙과 이 스펙 6개 경로(`/premium` `/premium/scan` `/matrix` `/orderbook/*` `/slippage/*` `/arbitrage`)가 일치하는지 확인. + "현재 구조" 절에 analysis 항목: `core/orderbook.py`(호가창 소진 순수 계산 — 003 과 공용) + `features/analysis/` — `service.py`(6개 빌더·거래소 레지스트리)·`router.py`·`models.py`, web 없음.
- `docs/context/dev-setup.md` — 검증용 스모크에 `/slippage` 1줄: `curl "localhost:8000/slippage/upbit?symbol=BTC/KRW&amount=1000000"` → `slippagePercent ≥ 0`·`levelsConsumed ≥ 1`.

## 7. 실행 보고 (실행 세션이 채움)

### walk → `core/orderbook.py` 이관 세션 (§2)
- 만든 것 (파일 목록):
  - `server/app/core/orderbook.py` — `server/app/features/analysis/walk.py` 를 `git mv` 로 옮겼다. `WalkResult`·`_EPSILON`·함수 4개(`walk_amount`·`walk_quantity`·`average_price`·`slippage_percent`)의 본문은 한 글자도 안 바꿨다. 모듈 docstring 만 고쳐 (a) 003·004 공용 core 모듈이라는 것과 (b) **함수를 async 로 바꾸지 말라**는 근거를 적었다 — `GET /spreads` 가 수집 락 없이 안전한 이유가 "표 조립 전체에 await 가 없다" 하나뿐이라, await 지점이 생기면 응답 하나가 스냅샷 교체 전·후 호가를 섞는다.
  - `server/app/features/analysis/service.py` — import 를 `app.core.orderbook` 으로. 서버 트리에서 이 모듈을 쓰는 유일한 곳이었다.
  - `server/app/features/analysis/tests/test_slippage_api.py` — 모듈 위치를 말하던 docstring 1줄만 갱신.
  - `docs/context/architecture.md` "현재 구조" analysis 항목, 이 문서 §5·§6.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - **테스트 파일은 옮기지 않았다.** walk 를 직접 부르는 테스트가 없었다 — 소진 계산의 검증 항목(§4 의 금액·수량 walk, 부분 체결, 매도 슬리피지, 2단계 예시)은 전부 `/slippage` **HTTP 응답**으로 확인하는 004 §4 항목이라 `features/analysis/tests/` 가 맞는 자리다(conventions.md "기능 테스트는 기능 폴더"). core 모듈이라고 다 `server/tests/` 에 단위 테스트가 있는 것도 아니다 — `core/premium.py` 는 기능 테스트(`features/spreads/tests/`)와 `tests/test_backfill.py` 를 통해서만 검증한다. `core/rows.py`·`core/networks.py` 처럼 `server/tests/test_orderbook.py` 를 새로 만들면 테스트 수가 늘어 "이관 전후 같은 수(265)" 확인이 깨지므로 이번 변경에는 넣지 않았다(남은 빚).
  - `server/.venv` 가 이미 있어서(Python 3.12.13, 의존성 설치 완료 — 동시 진행 중인 012 세션이 만든 것으로 보인다) `--clear` 로 다시 만들지 않고 그대로 썼다. 남이 쓰는 venv 를 지우면 그 세션이 깨진다.
  - 작업 트리에 012 세션의 커밋 안 된 `server/pyproject.toml` 수정(websockets 의존)이 있었다. 읽기만 하고 손대지 않았으며 커밋에도 넣지 않았다(파일을 지정해 add).
  - 003 §2 는 이 이관을 003 이 할 일로 적어 두었다. 004 담당이라 003 문장은 건드리지 않았다 — 003 세션이 정리하면 된다.
  - **커밋이 둘로 갈렸다.** 이 클론의 pre-commit 훅이 `docs/`·`CLAUDE.md` 밖의 경로를 막는다("이 폴더는 docs 만 커밋합니다. 코드 변경은 marketlens-space 에서"). 훅을 우회하지 않았다 — `refactor/walk-to-core-impl` 브랜치에는 문서만 커밋했고, 검증을 끝낸 코드 3파일은 작업 트리에 staged 로 남겼다. 코드 커밋은 marketlens-space 에서 한다.
- 남은 빚:
  - `core/orderbook.py` 에 `server/tests/` 직접 단위 테스트가 없다. 003 이 이 모듈을 실제로 import 할 때(spreads 슬리피지) 같이 만드는 것이 자연스럽다.
  - `docs/context/status.md` 는 이번 변경으로 고칠 것이 없었다 — 이관은 엔드포인트·응답 모양을 안 바꿔서 analysis 행 문구가 그대로 맞다.
  - §7 의 나머지(6개 엔드포인트 구현 당시의 판단)는 원래 구현 세션이 비워 둔 채라 git 기록에만 있다. 여기 적은 것은 이관 세션 몫뿐이다.

### 깊이 반영 세션 (§2·§3.1 — 모든 걷기가 `walk_levels` 를 거치게)
- 만든 것 (파일 목록):
  - `server/app/core/orderbook.py` — `walk_levels(row, side)` 를 공개 함수로 추가했다(003 §2 가 이 모듈의 공개 면으로 예고한 그 함수다). 본문은 003 이 spreads service 안에 두고 쓰던 사적 헬퍼를 그대로 옮긴 것이라 동작이 같다. docstring 에 (a) 003·004 의 모든 걷기가 이 함수를 거친다는 것과 (b) 표면값은 이 함수를 쓰지 않는다는 것을 적었다. 모듈의 "전부 동기" 조항은 그대로다 — `walk_levels` 도 목록을 고르기만 하고 `await` 가 없다.
  - `server/app/features/spreads/service.py` — 사적 헬퍼 `_walk_levels` 를 지우고 `core` 의 것을 import 한다. 호출 4곳의 인자·결과가 같아 003 의 테스트는 한 줄도 고치지 않았다.
  - `server/app/features/analysis/service.py` — 걷는 4곳을 `walk_levels` 로 돌렸다: `/orderbook` 이 돌려주는 목록, `/slippage` 의 walk 대상(`depthAvailable`·`bestPrice` 포함), `/arbitrage` 후보의 KRW 환산 호가, `/matrix` 의 양쪽 다리. `/premium`·`/premium/scan`·`/matrix` 의 표면 김프와 그쪽 호가 유무 판정은 `row.asks[0]`·`row.bids[0]` 직접 읽기 그대로 뒀다(§3.1).
  - `server/app/features/analysis/models.py` — `depth_available` 주석을 "걷는 목록의 단계 수"로.
  - `server/app/features/analysis/tests/test_depth_stream.py` (새 파일) — §4 "깊이 반영" 6항목을 8개 테스트로. 회귀를 잡는 항목은 **여러 단계를 심은** 깊이로 규모를 키워 `slippagePercent` 가 0 → 양수가 되는지 보고, 같은 규모에서 1단계짜리 REST 행은 0 이 나오는 것을 나란히 단언한다 — 1단계만 심으면 평균가 = 최우선가라 되돌려도 통과하기 때문이다.
  - `docs/context/architecture.md`·`docs/context/status.md`, 이 문서 §3.2·§3.3·§5.
- 추측한 지점 (묻지 않고 정한 것) / 실행 중 함께 고친 스펙 절:
  - **빈 호가 판정을 어느 목록으로 하는지**를 스펙이 말하지 않았다. 걷는 절(orderbook·slippage·arbitrage)은 `walk_levels` 결과로, 표면값만 보는 절(premium·scan·matrix)은 REST 로 판정하기로 하고 §3.3 에 한 줄 넣었다 — 응답에 실리는 목록과 "비었다"고 판정하는 목록이 다르면 REST 가 비고 스트림만 살아 있는 행에서 404 와 응답이 엇갈린다. §3.1 이 REST 직접 읽기의 예외를 세 절로만 한정한 것과 같은 결론이다.
  - **arbitrage 후보의 최우선가**도 걷는 목록 기준으로 뒀다(§3.2-3 에 한 문장 추가). arbitrage 는 §3.1 의 REST 고정 목록에 없고, 매수·매도처 선정과 `slippagePercent` 의 기준가가 실제로 먹는 호가와 달라지면 슬리피지가 음수로도 나온다.
  - **§3.2 `/slippage` 응답 키 목록의 `depthAvailable`(저장된 단계 수)** 는 같은 항목 앞 문장·§4 와 어긋나 있었다. "걷는 목록의 단계 수"로 고쳤다 — 앞 문장과 §4 검증이 둘 다 걷는 목록을 가리키므로 괄호 쪽이 낡은 표현이다.
  - **문서 머리 `상태: TODO`** 를 `DONE` 으로 고쳤다. CLAUDE.md 인덱스와 어긋난 채였고(이관 세션이 범위 밖이라 남겨 둔 것), 이번엔 004 가 담당 스펙이라 고칠 자리다.
  - **§5 를 이번 세션 기록으로 갈아 끼웠다.** 이관 세션의 `265 passed` 는 지금 트리와 맞지 않는 수치라 남기면 다음 세션이 기준선을 잘못 읽는다. 옛 문구는 git 에 있다(CLAUDE.md §4).
  - 표준 시드(§4)에는 깊이를 얹지 않았다. 깊이가 붙은 시드는 새 테스트 파일 안에서만 만든다 — 표준 시드가 바뀌면 기존 검증 항목의 손계산 기대값(+0.503% 등)이 전부 흔들린다.
- 보고만 하는 어긋남 (담당 아닌 스펙 — CLAUDE.md §5):
  - `docs/specs/003-spreads.md` §7 — 문서 주장 "단계 선택 규칙을 `core/` 공개 함수로 빼지 않았다 … spreads service 안의 사적 헬퍼로 뒀다" → 실제 `server/app/features/spreads/service.py` 는 `app.core.orderbook.walk_levels` 를 부른다. 003 §2 의 계약(공개 면에 `walk_levels`)은 이제 코드와 맞는다.
  - `docs/specs/003-spreads.md` §7 — 문서 주장 "004 analysis 는 `depth_*` 를 아직 쓰지 않는다" → 실제 004 의 걷는 4곳이 전부 쓴다. 이 세션이 해소한 항목이다.
- 남은 빚:
  - `core/orderbook.py` 는 아직 `server/tests/` 직접 단위 테스트가 없다(이관 세션이 남긴 빚 그대로). `walk_levels` 도 `/orderbook`·`/slippage`·`/spreads` HTTP 응답으로만 검증한다.
  - 깊이가 붙은 바이낸스 행에서는 `/matrix` 의 `totalSlippagePercent` = (REST 표면) − (스트림 기준 실효)라 두 출처가 섞인다. §3.1 이 의도한 설계지만, 스트림 최우선가가 REST 와 크게 벌어진 순간에는 이 값이 음수가 될 수 있다. 지금은 경고도 상한도 두지 않았다 — 실운영에서 관측되면 별도 스펙 거리다.
