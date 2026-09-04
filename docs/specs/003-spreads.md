# 003 — spreads

상태: DONE | 의존: 001(collect — 저장소·Row), 002(web-shell — 셸·공유 타입), 012(binance-depth — 해외 다단계 호가)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
서버 메모리에 수집된 1초 시세만으로 **전 (국내 거래소 × 해외 거래소 × 코인) 페어의 김프/역프 표**를 `GET /spreads` 로 낸다. 표의 김프/역프는 지정한 체결 규모(`notional`, 기본 $10,000)로 **호가창을 실제로 걸어** 얻은 슬리피지 차감 후 순값이다 — 추정식이 아니라 그 규모를 밀었을 때 실제로 남는 값이다.

끝나면 이렇게 보인다.
- 서버 기동 후 수 초 내 `/spreads` 가 수백 행을 반환한다.
- 브라우저에서 김프/역프와 환율이 1초마다 갱신된다.
- 서버가 죽으면 행이 흐려진다(stale).
- 체결 규모를 올리면 김프 값이 작아지고 `슬` 배지의 차감폭이 커진다.

## 2. 범위
만드는 것:
- 기능 폴더 `spreads` (server·web).
- BE 엔드포인트 `GET /spreads`, `POST /refresh`.
- FE 스프레드 탭과 1초 폴링.
- 김프 수식 공유 모듈. 004(analysis)·005(history)도 쓰므로 `server/app/core/premium.py` 에 공개 함수 `premium_percent(*, buy_krw: float, sell_krw: float) -> float` 를 둔다. 값은 `(sell/buy − 1) × 100`. 이 스펙의 fwd/rev 도 이 함수로 계산한다.
- 호가 걷기도 같은 이유로 공유 모듈이다 — 004(analysis)와 이 스펙이 함께 쓰므로 `server/app/core/orderbook.py` 에 둔다(기능 간 import 금지, CLAUDE.md §2). **함수는 전부 동기다** — `GET /spreads` 는 수집 락을 잡지 않고, 그것이 안전한 유일한 근거가 표 계산 전체가 `await` 없이 끝나 단일 이벤트 루프에서 선점될 수 없다는 점이기 때문이다. 걷기를 async 로 만들면 한 응답이 교체 전후 호가를 섞어 읽는다.

하지 않는 것:
- Influx 저장·아카이브. `spark` 는 009(tick-store)가 채운다.
- 저장 계층의 값 변경. 005 Influx `premium` 과 009 틱 레코드는 **슬리피지 차감 전 원값(raw)** 을 쓴다. 순값은 HTTP 응답(과 그것을 그대로 미러하는 010 S3)에만 있다 — 저장 시점에는 체결 규모가 정의되지 않기 때문이다.
- 망 단위 입출금 판정 **규칙**(정규화·매칭·tie-break) — 006 wallet-status 가 정의한다. 이 스펙의 행은 그 규칙의 결과를 싣기만 한다(아래 §3.2-4).
- 분석 엔드포인트(004 analysis).

바꾸는 기존 것:
- 002 의 셸 화면. 스프레드 placeholder 를 진짜 탭으로 교체하고 폴링을 시작한다. 행 클릭 시 기록 탭으로 피벗할 "선택된 심볼" 상태를 추가한다.
- 002 의 `shared/types.ts` 의 `SpreadRow` — `liqDom`·`liqFx` 를 빼고 `slipFwd`·`slipRev`·`krw` 를 넣는다. 002 §3.4 의 `SpreadRow` 계약도 같은 PR 에서 고친다(CLAUDE.md §6). shared → feature import 금지 규칙은 그대로다.
- `features/analysis/walk.py` → `core/orderbook.py` 이관. 004 의 해당 문장도 같은 PR 에서 고친다.

## 3. 동작

### 3.1 읽는 것 — 001 이 제공하는 메모리 저장소 (계약 복사)
저장소에 있는 것:
- **스냅샷**: 거래소, 코인, 호가통화, 마지막 체결가, `asks`(오름차순), `bids`(내림차순, 각 단계 `[price, size]`), 입출금 가능 여부 2개(true/false/null), 갱신 시각(aware UTC).
- **거래소별 KRW-USDT 환율**: `ask`, `bid`, 갱신 시각.

수집기가 1초마다 하는 일:
- 성공한 거래소의 스냅샷을 **통째로 교체**한다. 상폐 코인은 자동 소멸한다. 실패한 거래소는 직전 스냅샷이 `updated_at` 그대로 남아 age 가 자란다.
- 환율은 **거래소별 덮어쓰기**다. 이번에 못 받은 거래소는 직전 값을 유지한다.
- 마지막 수신 시각(epoch 초)을 갱신한다. 저장소는 "스냅샷 0개" 여부와 마지막 수신 시각을 알려준다.

고정값: 기준 거래소는 upbit, 국내 호가통화는 KRW, 해외 호가통화는 USDT, stale 기준은 5.0초, 제외 코인 목록은 기본 비어 있다. 환경변수는 `REFRESH_TOKEN`(기본 빈 문자열) 하나다. 키 목록은 `server/.env.example` 에 있다.

조회 API 는 거래소를 직접 호출하지 않는다. 수집은 001 의 수집 서비스(1회 실행, 결과 = 거래소별 저장 건수·환율·실패·경고)를 통해서만 한다.

### 3.2 `GET /spreads` — 표 계산
0. 파라미터 `notional` — 체결 규모(USD, 기본 `10000`). 표의 모든 행이 이 규모로 호가를 걷는다. 허용 범위 `1 ≤ notional ≤ 10_000_000`, 실수 허용. 범위·타입 위반은 **FastAPI 기본 422**(`{"detail":[…]}` — 이 스펙의 `{"error":{…}}` 포장이 아니다). 다른 파라미터는 없다. 응답 최상위에 쓰인 값을 `notional` 로 되돌려 싣는다 — 010 이 S3 줄을 자기완결로 만들려면 `slipFwd` 가 어느 규모의 값인지 줄 안에 있어야 한다.
1. 기준 거래소(`upbit`)의 환율이 없거나 ask/bid 가 0 이하 → **404** `market_data_not_found`. message: "메모리에 upbit 거래소의 KRW-USDT 환율이 없습니다. POST /refresh 로 수집했는지 확인하세요.", detail `{"exchange": "upbit"}`.
2. 스냅샷을 호가통화로 나눈다. `KRW` → 국내, `USDT` → 해외, 그 외 무시. 어느 한쪽이 비면 **404** `market_data_not_found`. message: "스프레드를 계산할 스냅샷이 부족합니다 (국내 KRW / 해외 USDT). 먼저 POST /refresh 로 수집하세요.", detail 에 양쪽 거래소 목록. 수집 전 빈 메모리는 1 또는 2 에서 404 가 나며 구분하지 않는다.
3. 페어 생성. 국내 거래소마다 **그 거래소 자신의 환율**을 쓴다. 환율이 없는 국내 거래소는 **행 전체가 빠진다** — 남의 환율을 빌리면 테더 프리미엄이 섞이기 때문이다. 해외 거래소마다, 양쪽에 **모두 상장된** 코인만 행이 된다(한쪽 상장은 페어 아님). 제외 코인 목록(대소문자 무시)의 코인은 제외한다. 국내==해외 거래소 조합은 만들지 않는다.
4. 행 하나의 규칙. 이 행 국내 거래소 USDT 시세의 ask 를 `rate_ask`, bid 를 `rate_bid` 라 한다 — **계산에만 쓰고 응답에는 싣지 않는다**(소비자는 최상위 `rate` 와 행의 `krw` 를 쓴다).
   - 최우선 호가 4개: 국내 bid/ask, 해외 bid/ask (각 1단계의 price·size). `status` 판정과 원값 계산에 쓴다.
   - 걷는 호가는 001 §3.3 규칙대로 고른다 — `depth_*` 가 비어 있지 않으면 그것을, 비면 `asks`/`bids` 를 쓴다. 국내는 항상 후자(업비트 30·빗썸 15단계), 해외는 012 스트림이 살아 있으면 전자(최대 20단계)다.
   - 수식은 **체결되는 쪽 호가**를 쓴다(살 때 ask, 팔 때 bid). 환율도 방향별이다. 김프는 원화로 USDT 를 **사서** 해외로 보내므로 `rate_ask`, 역프는 받은 USDT 를 원화로 **팔므로** `rate_bid`. 둘 다 실제 체결보다 유리하게 계산되는 일이 없도록 보수적인 쪽이다.
   - **원값(raw)** — 최우선 1단계 기준. 저장 계층(005·009)이 쓰는 값이고 응답에는 안 나간다.
     ```
     fwdRaw = premium_percent(buy_krw=fx_ask.price × rate_ask, sell_krw=dom_bid.price)
     revRaw = premium_percent(buy_krw=dom_ask.price,           sell_krw=fx_bid.price × rate_bid)
     ```
   - **걷기** — 양쪽 다리를 **수량으로 연결해** 건넌다(한 다리에서 산 수량을 다른 다리에서 판다). 각 다리를 따로 걸으면 사지도 않은 수량을 파는 값이 나온다.
     - fwd: 해외 asks 를 금액 `notional`(USDT)로 걸어 수량 `Qf` 와 평균가 `fx_ask_avg` → 국내 bids 를 **수량 `Qf`** 로 걸어 `dom_bid_avg`.
     - rev: 국내 asks 를 금액 `notional × rate_ask`(KRW)로 걸어 수량 `Qr` 과 `dom_ask_avg` → 해외 bids 를 **수량 `Qr`** 로 걸어 `fx_bid_avg`.
   - **순값과 차감폭.**
     ```
     fwd = premium_percent(buy_krw=fx_ask_avg × rate_ask, sell_krw=dom_bid_avg)
     rev = premium_percent(buy_krw=dom_ask_avg,           sell_krw=fx_bid_avg × rate_bid)
     slipFwd = max(0, fwdRaw − fwd)      # %p, 양수
     slipRev = max(0, revRaw − rev)
     ```
     응답의 `fwd`·`rev` 는 **차감 후 순값**이다. 상한은 없다 — 얕은 호가창에서는 몇 십 %p 도 나온다. 반올림하지 않는다.
   - **호가 소진**: 매수측이 저장 단계를 다 먹고도 규모가 남으면 **실제 체결된 만큼**의 평균가로 순값을 낸다(요청 규모를 채운 척하지 않는다). **매도측이 소진돼 못 판 수량이 있으면 판 수량만큼 매수측을 되맞춘다**(004 §3.2·§3.3 과 같은 규칙) — 못 판 코인을 0원으로 치면 −50% 대 쓰레기 값이 나오기 때문이다. `status` 는 바뀌지 않는다. 소진 여부를 행에 싣지 않는 이유는 키가 늘고 FE 가 쓰지 않기 때문이다 — 대신 §4 가 "같은 호가에서 규모를 키우면 slip 이 줄지 않는다"를 보장한다.
   - `krw` = `dom_bid.price` — 이 행 국내 거래소의 최우선 매수호가(KRW). 국내 시세 자체라 환율·슬리피지와 무관하다 — FE 가 환산하지 않고 그대로 표시한다.
   - `usd` = 해외 스냅샷의 마지막 체결가. `spark` = fwd 추이(009 가 채운다).
   - `age` = 현재 시각 − 양측 스냅샷 갱신 시각 중 **오래된 쪽**. 초 단위, 0 미만이면 0.
   - `status`: `age ≥ 5.0` → `stale`, 아니면 `ok`. 단 최우선 호가 4개 중 하나라도 비었거나, 그 **가격 또는 잔량**이 0 이하면 `fail` 이고 `fwd` `rev` `slipFwd` `slipRev` `krw` `usd` 는 전부 0. 가격 0 을 통과시키면 rev 의 분모가 0 이 되고, 잔량 0 을 통과시키면 걷어도 체결 수량이 0 이라 평균가가 0 이 되어 순값의 분모가 0 이 된다. **fail 이어도 입출금 값과 age 는 싣는다.** `fail` 이 아니면 최우선 4호가의 가격·잔량이 모두 양수이므로 걷기의 체결 수량이 0 이 되는 경우는 없다.
   - 입출금 5필드(`netDom depDom wdDom depFx wdFx`): **006 §3.7 의 망 판정**으로 채운다. 국내 망 정보가 없으면(키 없음 등) 코인 단위 값·`netDom` null 로 강등된다 — 그래서 키 없이 기동해도 5키는 항상 존재한다.
5. 행 정렬: `(sym, dom, fx)` 오름차순 고정.
6. 최상위 `rate` = 기준 거래소(`upbit`) 환율 **ask**(표시용). `notional` = 이 응답에 쓰인 체결 규모(USD). `warnings` = USDT 시세 미갱신 경고 배열(008, 없으면 빈 배열). `dataReceivedAt` = 저장소 마지막 수신 시각(ms), 수신 기록이 아직 없으면 null(스냅샷이 아예 없는 경우는 위 1·2 에서 이미 404 다). `fetchedAt` = 응답 시각(ms).

응답 예시 (모든 HTTP JSON 키는 camelCase. `spark`·`netDom`·입출금의 빈 값 모양을 보인다):
```json
{"rate": 1392.0, "notional": 10000.0,
 "rows": [{"sym": "BTC", "dom": "bithumb", "fx": "binance", "fwd": 0.41, "rev": -0.84, "usd": 64950.3,
           "spark": [], "status": "ok", "age": 0.8,
           "slipFwd": 0.12, "slipRev": 0.12, "krw": 90850000.0,
           "netDom": null, "depDom": null, "wdDom": null, "depFx": null, "wdFx": null}],
 "warnings": [], "dataReceivedAt": 1787139510649, "fetchedAt": 1787139510712}
```
행 키의 의미:
- `sym` 코인. `dom` 국내 거래소 id. `fx` 해외 거래소 id.
- `fwd` 정방향 김프 %. `rev` 역프 %. **둘 다 슬리피지 차감 후 순값**이다. 소수 그대로, 반올림하지 않는다.
- `slipFwd` `slipRev` 그 방향에서 차감된 폭(%p, 양수). 원값이 필요하면 `fwd + slipFwd`.
- `krw` 이 행 국내 거래소의 최우선 매수호가(KRW).
- `usd` 해외 마지막 체결가(USDT).
- `spark` 프리미엄 추이(009 가 채운다).
- `status` `ok`·`stale`·`fail` 중 하나. `age` 오래된 쪽 스냅샷 경과 초.
- `netDom` 판정된 국내 망 이름(문자열, 못 정하면 null). `depDom` `wdDom` 국내 입금/출금. `depFx` `wdFx` 해외 입금/출금. 값은 true 열림, false 막힘, null 모름 — **null 을 열림으로 읽는 코드는 버그다.**

### 3.3 보조 엔드포인트
- `POST /refresh` 200: 001 수집 서비스의 1회 실행 결과를 반환한다 — `snapshots[]`(거래소당 1항목 `{exchange, saved, calls, walletStatusAvailable}`)·`usdkrw[]`(관측된 국내 거래소 `{exchange, ask, bid}`)·`totalSaved`·`durationMs`·`failures[]`(`{exchange, errorCode, message}`)·`warnings[]`·`fetchedAt`. 거래소 일부 실패는 HTTP 에러가 아니라 `failures`/`warnings` 에 담긴다. 수집 루프가 이미 1초마다 도니 **수동 트리거·진단용**이며 동시 호출은 직렬화된다.
  - 토큰: `REFRESH_TOKEN` 이 빈 문자열이면 검사 없음. 설정돼 있으면 헤더 `X-Refresh-Token` 이 없거나 다르면 **401** `{"detail": "X-Refresh-Token 헤더가 없거나 올바르지 않습니다."}` (FastAPI 기본 형식 — `error` 포장 없음). 비교는 타이밍 안전 비교.

### 3.4 FE — 폴링과 002 와의 계약
- 002 가 만든 공유 피드에 "스프레드 적용" 동작이 있다. **행 배열과 환율을 통째로 교체**하고, 코인×거래소표시명 단위의 입출금 조회표를 재구성한다(`net` 은 `netDom`, 없으면 `'–'`). 002 의 1.5초 mock tick 은 각 행의 `age` 를 1.5 씩 올린다. 그래서 폴링이 죽으면 age 가 자라 저절로 stale 이 된다.
- 이 스펙이 1초 폴링을 제공한다. 요청은 `GET ${API_BASE}/spreads?notional=<선택된 규모>` 이고, 규모를 바꾸면 **다음 폴링부터** 그 값으로 나간다(즉시 재요청하지 않는다 — 1초면 갱신되고, 즉시 요청은 폴링과 겹쳐 순서가 뒤집힐 수 있다). 선택된 규모는 **셸이 들고 있다** — 탭의 분절 버튼과 폴링 URL 이 같은 값을 봐야 하기 때문이다(기본값은 $10k 로 서버 기본값과 같다). 성공 시 행의 `dom`/`fx` 를 표시명(`upbit→업비트`, `bithumb→빗썸`, `binance→Binance`, 모르는 id 는 그대로)으로 바꾼 뒤 공유 피드에 적용하고 화면을 갱신한다. **실패(네트워크·비 2xx)는 무시하고 직전 데이터를 유지**한다.
- 폴링은 spreads 기능 폴더 안에 살고, 셸이 공유 피드를 만든 직후 시작된다. 002 의 `shared/` 는 수정하지 않는다(shared → feature import 금지).
- 헤더(002 KPI 스트립)의 "USDT/KRW 암묵환율" 은 `/spreads` 최상위 `rate` 로 채워진다. `rate>0` 이면 `₩1,392.0`(소수 1자리 ko-KR), 0 이면 `–`.
- 셸: 스프레드 placeholder → 스프레드 탭. 행 클릭 → 선택된 심볼을 저장하고 기록 탭으로 전환한다. 기록 탭은 아직 placeholder 이므로 선택된 심볼을 history 탭에 넘겨 `history — ... · BTC` 처럼 보이게 한다(005 가 진짜 탭으로 교체).

### 3.5 FE — 스프레드 탭 화면
- 표 컬럼 순서: **심볼 | 국내가 KRW | 김프(또는 역프) | 입출금 | 네트워크**. 국내가 열만 가변 폭, 나머지는 고정 폭. 코인 1개 = 행 1개(거래소 페어는 코인별로 집계). 색·간격·그림자는 `docs/design/theme.css`, 표 구조는 002 §3.2 를 따른다.
- 집계: "기준 국내 거래소" 필터(모두/업비트/빗썸)와 "비교 해외 거래소" 체크(해제된 거래소의 행은 제외)를 거친 행을 코인별로 모은다. `fail` 아닌 행이 live 다. 코인별로 김프 최대 행과 역프 최대 행을 고른다. 코인 age = live 중 최소. 전부 fail 이면 "전부 fail", live 전부 `age ≥ 5` 면 "전부 stale" 상태다.
- 국내가 = `₩` + KRW 표시 형식의 행 `krw`. fail 이거나 `krw` 가 0 이면 `–`. 서버가 그 행 국내 거래소의 최우선 매수호가를 그대로 주므로 FE 는 환산도 슬리피지 보정도 하지 않는다.
- 체결 규모: `$10k`(기본)·`$50k`·`$100k`·`$500k` 를 고르고, 그 값이 `notional` 로 나간다. **FE 는 슬리피지를 계산하지 않는다** — 응답의 `fwd`·`rev` 가 이미 순값이라 그 값이 그대로 모든 판단(최대 행 선택·강조·임계 필터·정렬·표시)에 쓰인다.
- 김프 셀의 값 왼쪽에 `슬 −N.NN%p` 배지를 보인다. 값은 그 행의 `slipFwd`(역프 기준이면 `slipRev`)를 소수 2자리로 반올림한 것이고, 0 이면 배지를 숨긴다(열 폭은 유지 — 규모를 바꿀 때마다 표가 흔들리지 않게).
- 규모 세그먼트 아래 안내: `호가창 시장가 체결 기준 · 매수·매도 양측 슬리피지 차감`. 가격 기준 세그먼트(`현재가`/`슬리피지 반영`)는 없앤다 — 이제 차감이 항상 적용된다.
- 김프 셀 = `[출발 거래소 태그] → [도착 거래소 태그] [+1.23%]`. 김프 보기면 해외→국내, 역프 보기면 국내→해외. 값 색은 퍼센트 색 규칙(한국식: 양수 빨강·음수 파랑·0 중립). 값 없으면 `–`.
- 입출금 셀 = 태그 2개 `출금 가능|중단|?` `입금 가능|중단|?`. 출금 거래소는 출발, 입금 거래소는 도착. 테두리 규칙: true 강조색 실선, false 중립색 실선, **null 은 점선** — 모름을 열림·막힘과 한눈에 구분하기 위해서다. 이 스펙에선 전부 null 이라 **모든 행이 `?` 점선**이다. 네트워크 셀 = `net`(없으면 `–`).
- **null 은 열림이 아니다.** "입출금 가능만" 필터는 출금·입금 둘 다 true 일 때만 통과한다.
- 강조: fail 아님, stale 아님, 값 ≥ 임계값이면 강조 행이다. 임계값 기본 **1.5%**, 숫자 입력으로 0.1 단위 조정. 강조 행은 배경을 옅은 강조색으로 칠하고 심볼을 강조색 + 점으로 표시한다. `stale` 행은 흐리게(반투명) 보인다.
- 필터바 1행: 심볼 검색(대소문자 무시), 기준 국내 거래소 분절 버튼, 임계값 숫자 입력, "임계 초과만"(전부 fail 아니고 값 ≥ 임계), "입출금 가능만", 우측 `N / M 코인 표시`.
- 필터바 2행: "기준 보기"(김프/역프) · 세로선 · "체결 규모" 분절 버튼(차감이 항상 적용되므로 항상 보인다) · 안내 문구 · 세로선 · "비교 해외 거래소" `모두` 체크박스 · 세로선 · 002 §3.6 의 해외 거래소 6곳 체크박스(기본 전부 체크). `모두` 는 6곳 전체 토글이다. 수집 대상이 아닌 거래소는 행이 없으므로 체크를 풀어도 표가 변하지 않는다.
- 정렬: 헤더 클릭. 기본 김프 내림차순. 전부 fail 인 코인은 항상 맨 뒤, null 값은 뒤. 같은 키 재클릭 = 방향 반전. 새 키는 심볼·네트워크만 오름차순, 나머지 내림차순. 키: 김프/역프(보기에 따라), 국내가, 입출금(가능 > 모름 > 중단), 네트워크, 심볼.
- 빈 상태: 행이 0개면 "백엔드에서 스프레드를 받는 중입니다…". 필터 결과 0개면 "조건에 맞는 코인이 없습니다. 필터를 넓혀 보세요.".

## 4. 검증
BE (네트워크 없음 — 저장소에 직접 시드):
- 빈 메모리에서 `/spreads` 는 404 `market_data_not_found` 다.
- 스냅샷은 있지만 기준 거래소(upbit) 환율이 없으면 404 다.
- 환율이 없는 국내 거래소(예: bithumb)의 행은 전부 빠지고 upbit 행은 남는다.
- 한쪽에만 상장된 코인은 행이 없다.
- 제외 코인 목록에 XRP 가 있으면 XRP 행이 없다(소문자도 동일).
- 원값은 국내 bid·해외 ask·rate_ask(fwd), 해외 bid·국내 ask·rate_bid(rev)로 계산된다(ask≠bid 인 환율로 시드해 수식값과 일치 확인).
- ask=bid 인 환율이면 fwd/rev 가 단일 환율 수식과 같다.
- 거래소마다 환율이 다르면 각 행이 자기 국내 거래소 환율로 계산된다(남의 환율을 빌리지 않는다 — `krw` 와 순값으로 확인).
- 호가가 빈 스냅샷은 `status=fail` 이고 숫자 필드가 0, 입출금 값은 유지된다. 최우선 호가의 **잔량이 0** 인 스냅샷도 같다.
- 갱신 시각이 6초 전인 스냅샷은 `stale`, 0.5초 전이면 `ok`, age 는 오래된 쪽 기준이다.
- `krw` 가 그 행 국내 거래소의 최우선 매수호가와 같다.

**슬리피지 (이 스펙의 핵심)**
- 규모가 1단계 안에서 끝나면 `slipFwd`·`slipRev` 가 0 이고 `fwd` 는 원값과 같다.
- 2단계 이상을 먹으면 `slipFwd > 0` 이고 `fwd + slipFwd` 가 원값과 같다(양방향 동일).
- 양쪽 다리가 수량으로 연결된다 — 해외에서 산 수량 `Qf` 를 국내에서 판 평균가로 순값이 나온다(다리를 따로 걸은 값과 다름을 시드로 고정).
- 같은 호가에서 규모를 키우면 slip 이 **줄지 않는다**(단조성). 호가가 소진돼도 마찬가지다.
- 호가가 소진되면 실제 체결된 만큼의 평균가를 쓴다(요청 규모를 채운 척하지 않고, `status` 도 바뀌지 않는다).
- 해외에 `depth_*` 가 있으면 그것을, 없으면 1단계 `asks`/`bids` 를 쓴다(012 스트림 유무로 시드해 확인).
- `notional` 미지정이면 10000 이 쓰이고 응답 최상위에 그 값이 실린다. 0·음수·상한 초과·문자열은 **422** 다.
- 응답 `fwd`·`rev` 는 저장 계층이 쓰는 원값과 다르다 — 같은 저장소로 만든 `premium` 점의 fwd 는 `fwd + slipFwd` 와 같다.
- 행은 `(sym, dom, fx)` 오름차순이다.
- 응답 행 키가 camelCase(`slipFwd`·`krw`…) 이고 `netDom is None`.
- `REFRESH_TOKEN` 설정 + 헤더 없음 → 401. 올바른 헤더 → 200. 미설정이면 헤더 없이 200.

실서버 확인 (기동 후 수 초 뒤 실제 `/spreads` 호출):
- 최상위 `rate > 1000`, 행 수 > 100.
- 행 키 집합이 정확히 `sym dom fx fwd rev usd spark status age slipFwd slipRev krw netDom depDom wdDom depFx wdFx` **17개**다. 최상위는 `rate notional rows warnings dataReceivedAt fetchedAt` 6개다.
- 첫 행 `status` 는 `ok`·`stale`·`fail` 중 하나, `netDom is None`.
- `?notional=500000` 으로 부르면 같은 행의 `fwd` 가 기본 규모보다 작거나 같고 `slipFwd` 가 크거나 같다.
- 전체 행이 `(sym, dom, fx)` 오름차순이다.
- `POST /refresh` 의 `totalSaved > 100`.
- `REFRESH_TOKEN` 을 설정해 띄운 서버에 헤더 없이 `POST /refresh` → 401.

FE 수동 확인:
- 수백 행 표시, 1초마다 값이 변한다. KPI 환율이 `₩1,3xx.x` 로 보인다.
- 입출금 태그 전부 `?` 점선, 네트워크 `–`.
- 임계값 1.5 이상 코인만 배경 강조·심볼 점. 임계값 바꾸면 즉시 반영된다.
- 서버 kill → 표는 남고 약 5초 뒤 행이 흐려진다. 서버 복구 → 다시 선명해진다.
- 역프 기준 토글 시 화살표 방향·값이 바뀐다. 행 클릭 → 기록 탭으로 전환되며 심볼이 보인다.
- 김프 셀에 `슬 −N.NN%p` 배지가 보이고 값이 그만큼 작다. 규모를 $500k 로 올리면 차감폭이 커지고 김프 값이 더 작아진다. 국내가 KRW 는 변하지 않는다.
- 비교 해외 거래소에서 Binance 를 풀면 표가 비고 "조건에 맞는 코인이 없습니다" 가 보인다. `모두` 로 되돌리면 복구된다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
서버 슬리피지 반영 세션(2026-09-04). 네 명령 모두 통과해야 커밋한다.
```bash
cd server && .venv/bin/ruff check .            # All checks passed!
cd server && .venv/bin/ruff format --check .   # 171 files already formatted
cd server && .venv/bin/python -m pytest -q     # 303 passed, 1 warning in 1.58s
cd web && npm run lint                         # oxlint src — 출력 없음(error 0)
cd web && npm run build                        # tsc -b && vite build — ✓ 44 modules transformed / ✓ built in 282ms
```
착수 시점(깨끗한 main)의 pytest 는 288 passed 였다. 늘어난 15개는 슬리피지 14개(`test_slippage.py`)와 잔량 0 fail 1개다.

FE 육안 확인은 `/spreads` 계약을 그대로 흉내낸 로컬 스텁(17키 행 5개)을 `:8000` 에 띄우고 `npm run dev` 로 봤다 — 이 망에서 거래소 도메인이 막혀 실수집이 안 되기 때문이다(dev-setup.md 로컬 메모). 확인한 것: 규모 세그먼트가 항상 보이고 `가격 기준` 세그먼트가 없다, 김프 셀에 `슬 −0.08%p` 배지, 국내가 열이 행 `krw` 그대로(₩152,000,000), 규모를 $500k 로 올리면 **다음 폴링부터** `?notional=500000` 이 나가고 배지가 `−0.84%p` 로 커지며 김프 값이 `+2.23% → +1.47%` 로 작아지고 국내가는 그대로, 임계 1.5 강조가 순값 기준으로 다시 계산된다.

§4 "실서버 확인"(행 수 > 100·`rate > 1000`·`POST /refresh` 의 `totalSaved > 100`)은 실거래소 수집이 필요해 **EC2 에서 돌려야 한다** — 이 세션에서는 못 돌렸다(§7 남은 빚).

## 6. 갱신할 문서
- `docs/context/status.md` — spreads 행의 server 칸에 `notional` 규모로 호가를 걷어 슬리피지를 차감한다는 것, web 칸에 규모 세그먼트, 비고에 행 17키. 알려진 빚에 저장 계층(원값)과 응답(순값)의 차이. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 003 행 범위에 서버 슬리피지. **항상 포함.**
- `docs/specs/002-web-shell.md` — §3.4 `SpreadRow` 계약에서 `liqDom`·`liqFx` 를 빼고 `slipFwd`·`slipRev`·`krw` 를 넣는다.
- `docs/specs/004-analysis.md` — walk 가 `core/orderbook.py` 로 이관된 것을 반영(§2·§3.1).
- `docs/specs/009-tick-store.md`·`docs/specs/010-s3-snapshot.md` — 복사해 둔 행 계약과 키 수.
- `docs/context/dev-setup.md` — 검증용 스모크를 §4 실서버 기준으로 교체: 최상위 `rate > 1000`·행 수 > 100·행 키 정확히 17개(키 순서는 §3.2 응답 예시와 동일)·`?notional=` 로 규모를 바꾸면 `slipFwd` 가 커지는지.
- `docs/context/architecture.md` — "현재 구조" 절에 spreads 항목: `core/premium.py`(`premium_percent`), `features/spreads/`(service 순수 계산·router 2 엔드포인트·models), `/refresh` 응답 모양(`snapshots[]` 거래소당 1항목 등), web `features/spreads/`(1초 폴링·확장 타입). FE 데이터 흐름 문구는 002 §6 에서 이미 반영됨 — 확인만.

## 7. 실행 보고 (실행 세션이 채움)

### 서버 슬리피지 반영 세션 (2026-09-04)
- 만든 것 (파일 목록):
  - `server/app/features/spreads/service.py` — 걷기 2회(다리를 수량으로 연결)·순값·차감폭, `notional` 인자와 범위 상수. 단계 선택(`depth_*` 우선)과 다리 연결은 이 파일의 사적 헬퍼 2개다.
  - `server/app/features/spreads/models.py` — 행 17키(`slipFwd`·`slipRev`·`krw` 추가, `liqDom`·`liqFx`·`rateAsk`·`rateBid` 제거), 최상위 `notional`.
  - `server/app/features/spreads/router.py` — `notional` 쿼리 파라미터(FastAPI `Query(ge, le)`).
  - `server/app/core/snapshot.py` — S3 줄의 최상위 맥락에 `notional` 추가(010 §3.4).
  - `server/app/features/spreads/tests/test_slippage.py` — **신규**. §4 "슬리피지" 8항목.
  - `server/app/features/spreads/tests/test_spreads_api.py`·`server/tests/test_persist.py`·`server/tests/test_snapshot.py` — 키·관계 갱신.
  - `web/src/shared/types.ts`(002 §3.4 계약)·`web/src/features/spreads/{types.ts,api.ts,Tab.tsx}`·`web/src/App.tsx`.
- 추측한 지점 (묻지 않고 정한 것 — 전부 문서에 반영):
  - **단계 선택 규칙을 `core/` 공개 함수로 빼지 않았다.** §2 가 열거한 공유 모듈은 `premium.py`·`orderbook.py` 둘뿐이고, 004 는 아직 `depth_*` 를 코드로 쓰지 않아 core 에 두면 부르는 곳 없는 공개 함수가 된다(conventions "추측성 코드 금지"). spreads service 안의 사적 헬퍼로 뒀다.
  - **최우선 호가의 잔량 0 도 `fail`** — §3.2-4·§4 에 적었다. "fail 이 아니면 체결 수량이 0 이 되는 경우는 없다"는 스펙의 단언은 가격만 검사해서는 참이 되지 않는다(잔량 0 → 평균가 0 → 순값의 0 나눗셈으로 500).
  - **최상위 키 순서**를 §3.2 예시대로 `rate notional rows warnings dataReceivedAt fetchedAt` 로 맞췄다(직전 코드는 `rows` 다음이 `dataReceivedAt`).
  - **체결 규모 선택값은 셸 상태**다 — 탭의 분절 버튼과 폴링 URL 이 같은 값을 봐야 한다. §3.4 에 한 문장 추가.
- 실행 중 함께 고친 절:
  - 003 §3.2-4(잔량 0)·§3.4(규모 상태 위치)·§3.5 필터바 2행·§4(잔량 0 검증·FE 육안 문구)·§6(status.md 지시). §3.5 본문은 "가격 기준 세그먼트를 없앤다"인데 필터바 2행 목록에는 그 세그먼트가 남아 있어 서로 어긋나 있었다. §6 의 status.md 지시 문구는 006·008 이전 것이라 현재 문서보다 뒤처져 있었다(status.md 본문은 이미 이 변경과 맞아 손대지 않았다).
  - 005 §3.3·§4 — `premium` 이 **차감 전 원값**이라는 것과 `/spreads` 순값과의 관계(`fwd + slipFwd`). "`/spreads` 의 fwd/rev 와 일치한다"는 §4 문장은 이 변경으로 거짓이 되므로 관계식으로 바꾸고, 차감이 0 이 아닌 시드로 고정하도록 적었다.
  - `docs/context/architecture.md`("현재 구조" spreads 항목, BE 흐름의 `premium` 원값 표기)·`docs/context/db.md`(`premium` 원값).
- 남은 빚:
  - §4 "실서버 확인"(행 수 > 100·`rate > 1000`·`totalSaved > 100`·`?notional=500000` 비교)을 못 돌렸다. 이 망에서 거래소 도메인이 막혀 실수집이 안 된다 — **EC2 에서 한 번 돌려야 한다**.
  - `core/orderbook.py` 는 여전히 단독 단위 테스트가 없다(004 §7 이 남긴 빚). 003 이 이 모듈을 실제로 쓰기 시작했지만 검증은 `/spreads`·`/slippage` HTTP 응답을 통해서만 한다.
  - `server/build/lib/` 이 git 에 추적돼 있고 앱 트리의 낡은 사본이다(`liqDom` 시절 `spreads/service.py` 포함). `ruff check .` 가 이 사본까지 본다(171 파일). 삭제는 이 스펙 범위 밖이라 손대지 않았다.
  - 012 의 `test_spreads_payload_is_identical_with_and_without_depth` 는 그 시드의 $10,000 이 1단계 안에서 끝나 통과한다. 시드를 조금만 얕게 잡으면 깨진다 — 012 는 "HTTP 계약 무변경"을 뜻했지 "값 무변경"이 아니므로 담당 스펙이 다시 볼 자리다.
  - 004 analysis 는 `depth_*` 를 아직 쓰지 않는다 — `docs/specs/004-analysis.md` §3.1: 문서 주장 "`depth_*` 가 비어 있지 않으면 그것을 쓴다" → 실제 `server/app/features/analysis/service.py` 는 `row.asks`/`row.bids` 만 본다. 담당 스펙이 아니라 보고만 한다.
