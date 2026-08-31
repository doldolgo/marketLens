# 001 — collect

상태: TODO | 의존: 없음

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
서버를 띄우면 업비트·빗썸·바이낸스의 시세가 **1초마다 메모리에 쌓인다**. `GET /health` 로 서버가 살아 있는지 확인할 수 있다.
이후 스펙(003 spreads 등)은 거래소를 직접 부르지 않고 이 메모리만 읽는다. DB 없음, 화면 없음.

## 2. 범위
- 만드는 것: `server/` 앱 골격(FastAPI, 에러 응답 형식), `GET /health`, 수집 루프, 메모리 저장소, 거래소 커넥터 3개(public API 만).
- 하지 않는 것: `/health` 외 엔드포인트, DB·영속(005), 입출금 상태 조회(006), 바이낸스 선물, Docker(007).
- 입출금 상태 조회는 이 스펙에선 비워두고 필드를 `null` 로 둔다. 006이 조회기를 끼워 넣고, 실패 시 경고 1줄을 `/refresh` warnings에 넣는다.
- 바꾸는 기존 것: 없음.

## 3. 동작

### 3.1 서버 골격
- 앱 이름은 `MarketLens Backend`, 버전은 `0.1.0`. `/health` 응답과 User-Agent(`marketlens-server/<version>`)에 쓴다.
- 거래소 호출 전체 타임아웃은 3.0초, 연결 타임아웃은 1.5초.
- 수집 주기는 1초다. 한 사이클이 **끝난 뒤** 이만큼 쉰다. 사이클이 길어져도 겹치지 않는다.
- 호가는 누적 `price×size` 가 10억 KRW 에 도달한 단계까지만 저장한다(§3.4 행 조립 규칙).
- 국내 마켓 통화는 `KRW`, 해외 마켓 통화는 `USDT`.
- 거래소 주소는 §3.5 의 공식 URL 이다.
- 커넥터는 `docs/context/architecture.md` 원칙대로 공통 인터페이스를 구현한다.

에러 형식:
- 앱 에러 응답 형식은 항상 `{"error": {"code": str, "message": str, "detail": object}}`.
- 거래소 타임아웃은 504 `exchange_timeout`. 그 외 거래소 호출 실패(비-200, JSON 아님, 연결 실패)는 502 `exchange_api_error`.
- `detail` 에는 `exchange`·`url` 을 담는다. 비-200 이면 `status_code` 와 본문 앞 500자도 담는다.
- CORS·GZip·uvicorn 워커 1개는 `docs/context/architecture.md` 규칙대로(메모리가 진실이므로 워커가 둘이면 서로 다른 메모리를 본다).
- `GET /health` → `200 {"status": "ok", "version": "0.1.0"}`. 수집 루프 상태와 무관하게 항상 ok.

### 3.2 수집 루프
앱 시작과 함께 백그라운드로 돌고 종료 시 취소된다. 한 사이클은 순서대로:
1. **국내(업비트·빗썸) 동시 수집**: 거래소마다 KRW 전 마켓의 호가(30단계 요청)와 마지막 체결가를 받는다.
2. **환율 추출**: 국내 거래소마다 KRW 호가 결과 중 base 가 `USDT` 인 항목에서 `asks[0].price` → `rate.ask`(USDT 살 때), `bids[0].price` → `rate.bid`(USDT 팔 때). 추가 HTTP 호출 없음.
   USDT 항목이 없거나 한쪽 호가가 비었거나 0 이하면 그 거래소 환율은 이번 사이클에 **관측 없음**.
3. **바이낸스 수집**: USDT 현물 전 종목의 마지막 가격과 최우선 호가(1단계)를 받되, **국내 어느 거래소든 KRW 마켓에 있는 코인만** 남긴다.
4. **행 조립**(§3.4) 후 **교집합 필터**: base 가 `(국내 거래소 합집합) ∩ (바이낸스)` 에 드는 행만 남긴다. 국내 전용 코인·바이낸스 전용 코인은 메모리에 없다.
   `USDT` 자신도 바이낸스에 USDT/USDT 가 없으므로 빠진다(환율로만 쓰인다).
5. **메모리 교체**: 아래 규칙대로 스냅샷과 환율을 갱신하고, 저장소의 `received_at`(epoch 초)을 지금으로 갱신한다.

메모리 교체 규칙:
1. 스냅샷은 **거래소 단위로 통째 교체**한다.
2. 이번 사이클에 성공한 거래소는 직전 세트를 버리고 이번 세트로 바꾼다. 상폐 코인은 자동 소멸한다.
3. 실패한 거래소는 직전 세트를 그대로 유지한다. `updated_at` 이 안 바뀌어 age 가 자라고, 조회 쪽에서 stale 로 드러난다.
4. 환율은 **이번에 관측된 거래소만 덮어쓰고** 못 받은 거래소는 직전 값을 유지한다. 환율은 초 단위로 급변하지 않아 낡은 값이 없는 것보다 낫다.

부분 실패·장애:
- 한 거래소 호출이 실패(타임아웃·비-200·형식 오류 등 어떤 예외든)하면 그 거래소는 **이번 사이클 결과가 빈 것**으로 취급하고 실패 목록에 `{exchange, error_code, message}` 로 기록한다. 다른 거래소 결과는 정상 반영한다. 사이클은 예외를 밖으로 던지지 않는다.
- 실패한 거래소의 스냅샷은 **직전 값이 남는다**. 사라지게 두면 바이낸스가 1초 끊길 때마다 표 전체가 빈다.
- 교집합 필터는 "이번 사이클 성공 거래소 + 유지된 거래소" 전체로 계산한다. 거래소가 **한 번도** 성공한 적 없으면 당연히 비어 있다.
- 환율을 못 구한 국내 거래소가 있으면 경고 문구를 남긴다: `"KRW-USDT 호가가 없어 환율을 못 구한 거래소: upbit, bithumb (해당 국내 거래소의 김프 계산은 이번 회차에 빠진다)."`
- 사이클 한 번의 결과 요약(거래소별 저장 수, 관측된 환율 목록, 실패·경고 목록, 거래소별 호출 수, 소요 ms, `fetched_at` epoch ms)은 호출자에게 돌려준다. 스펙 003 이 `POST /refresh` 응답으로 노출한다.
- 사이클은 동시에 두 개 돌지 않는다. 루프와 수동 트리거가 겹치면 뒤의 것이 기다린다.

### 3.3 메모리 저장소 계약 (후속 스펙이 복사해 쓴다)
스냅샷 1개 = `(exchange, base)` 당 1행. 필드:
- `exchange` — `upbit`·`bithumb`·`binance` 중 하나. `base` — 코인(예 `BTC`). `quote` — 국내 `KRW`, 해외 `USDT`. `native_symbol` — 거래소 원본 심볼(예 `KRW-BTC`, `BTCUSDT`).
- `price` — 마지막 체결가. 없으면 `(bid+ask)/2`.
- `asks` — `[price, size]` 목록, 오름차순, 누적액 상한까지(바이낸스는 1단계만). `bids` — 같은 모양, 내림차순.
- `price_timestamp` — 거래소 시세 시각 epoch ms(바이낸스는 수집 시각).
- `deposit_enabled`·`withdrawal_enabled`·`networks` — wallet-status(006) 조회가 60초 주기로 채운다. 키 없음·조회 실패면 `null`·빈 목록.
- `updated_at` — 이번 사이클 적재 시각, tz-aware UTC. `age = now − updated_at`.
환율 = 국내 거래소 id 당 1개 `{exchange, ask, bid, updated_at}`. 바이낸스 환율은 없다.

조회 기능:
- 전체 목록. 거래소·base 로 필터할 수 있고 base 는 대소문자를 무시한다.
- 단건. 없으면 `None`.
- 거래소별 환율, 전체 환율 사본, `received_at`, 비었는지 여부.

### 3.4 행 조립 규칙
1. `price` = 마지막 체결가. 없거나 0 이하면 `(bid+ask)/2`. 그것도 없으면 **그 코인은 건너뜀**.
2. 국내 호가는 받은 순서대로 `[price, size]` 를 담는다.
3. 국내 호가는 누적 `price×size` 가 10억 KRW 에 **도달한 단계까지 포함**하고 자른다.
4. 국내는 bid·ask 어느 한쪽이라도 비면 그 코인을 건너뛴다.
5. 바이낸스는 `bid`·`ask` 가 없거나 0 이하면 건너뛴다.
6. 바이낸스는 `asks=[[ask, ask_size]]`, `bids=[[bid, bid_size]]` 로 1단계만 담는다. size 없으면 0.

### 3.5 외부 의존 (public API, 인증 없음)
**업비트** `https://api.upbit.com`
- `GET /v1/market/all` → `[{"market":"KRW-BTC","korean_name":"비트코인",...}]`. `KRW-` 로 시작하는 `market` 만. 10분 캐시.
- `GET /v1/orderbook?markets=KRW-BTC,KRW-ETH,...` / `GET /v1/ticker?markets=...`. 마켓 **100개씩 청크**로 나눠 동시 호출(URI 길이 414 방지). KRW ~280개 → 청크 3개.
- rate limit: 엔드포인트 그룹별 **초당 10회**. 한 사이클 호가 3 + 티커 3 = 6회.
- 호가 raw: 마켓당 1원소. `orderbook_units` 는 같은 단계의 bid/ask 가 한 쌍이고 정렬돼 온다. 깊이 파라미터 없이 **항상 30단계**.
```json
[{"market":"KRW-BTC","timestamp":1700000000000,"orderbook_units":[{"ask_price":101.0,"bid_price":99.0,"ask_size":1.0,"bid_size":2.0}]}]
```
- 티커 raw: `[{"market":"KRW-BTC","trade_price":90916000.0,"trade_timestamp":1786074475649,...}]`. `trade_price`·`trade_timestamp` 만 쓴다.

**빗썸** `https://api.bithumb.com`
- 경로·응답 형태가 업비트 v1 과 같다(`/v1/market/all`, `/v1/orderbook`, `/v1/ticker`, `markets=` 파라미터, 100개 청크). **커넥터 코드는 공유하지 않는다.**
- KRW 마켓 ~480개 → 청크 5개, 사이클 10회. rate limit 초당 150회.
- 호가는 응답 자체가 **최대 15단계**.
- **잔량 0 인 유령 호가**가 드물게 섞인다 → `size<=0` 단계는 버린다. 최우선 호가도 잔량>0 인 첫 단계다.
- `ticker.trade_timestamp` 가 **KST 벽시계를 epoch 처럼 찍어 정확히 9시간(32,400,000ms) 미래**로 온다(호가의 `timestamp` 는 정상).
  현재보다 1시간 이상 미래면 9시간을 뺀다, 아니면 그대로(빗썸이 고치면 자동 통과).
```json
[{"market":"KRW-BTC","trade_price":90900000.0,"trade_timestamp":1786106875649}]
```

**바이낸스** `https://api.binance.com`
- `GET /api/v3/ticker/price`(마지막 가격) / `GET /api/v3/ticker/bookTicker`(최우선 호가). 심볼 파라미터 없이 **전 종목 한 번에**(~3,700개, weight 4). 사이클 2회.
  단일 심볼이 weight 2 라 2종목만 넘어도 전체가 싸다.
- `symbol` 이 `USDT` 로 **끝나는** 것만, base = 접미사 뗀 나머지. 가격·수량은 **문자열** → float. `0` 은 거래 없음이므로 건너뜀.
- 일괄 호가 깊이 조회는 **없다**(이 스펙에선 1단계만 저장).
- **`ticker/24hr` 금지** — `closeTime` 은 윈도우 끝이지 체결 시각이 아니다.
```json
[{"symbol":"BTCUSDT","bidPrice":"99.0","bidQty":"2.0","askPrice":"101.0","askQty":"3.0"}]
```

## 4. 검증
- `GET /health` 가 200 과 `{"status":"ok","version":...}` 을 돌려준다.
- 존재하지 않는 경로는 404 다.
- 업비트 raw 호가·티커가 스냅샷 모양(`asks/bids` 의 `[price,size]`, `price`=체결가, `price_timestamp`)으로 바뀐다.
- 업비트 마켓 101개는 2개 청크로 나뉘어 호출된다.
- 빗썸 잔량 0 단계는 호가에서 빠지고 최우선 호가도 잔량>0 인 단계가 된다.
- 빗썸 `trade_timestamp` 가 1시간 이상 미래면 9시간을 빼고, 정상 값이면 그대로 둔다.
- 바이낸스 문자열 가격이 float 가 되고, `USDT` 로 끝나지 않는 심볼과 가격 0 인 심볼은 빠진다.
- 바이낸스 행은 국내에 상장된 코인만 남는다.
- 메모리에는 국내∩바이낸스 교집합만 들어가고, `USDT` 는 들어가지 않는다.
- 성공한 거래소에서 직전 사이클에 있던 코인이 이번 결과에 없으면 메모리에서 사라진다(거래소 단위 통째 교체).
- 한 거래소가 실패한 사이클에는 그 거래소의 직전 스냅샷이 `updated_at` 그대로 남고, 다른 거래소는 갱신된다.
- 환율은 국내 거래소별로 `ask=asks[0].price`, `bid=bids[0].price` 가 되며, USDT 호가를 못 받은 거래소는 직전 환율을 유지하고 경고가 남는다.
- 국내 호가는 누적 `price×size` 가 상한에 도달한 단계까지만 저장된다(`inf` 면 전부, 빈 입력은 빈 목록).
- 체결가가 없으면 mid 로 대체하고, 둘 다 없으면 그 코인은 저장되지 않는다.
- 한 거래소 호출이 예외를 던져도 사이클은 끝까지 돌고, 그 거래소는 실패 목록에 `error_code` 와 함께 기록되며 나머지 거래소는 저장된다.
- 거래소 타임아웃은 504 `exchange_timeout`, 비-200 은 502 `exchange_api_error` 로 변환되고 `detail` 에 `status_code`·`body` 가 담긴다.
- 스냅샷 `updated_at` 은 tz-aware UTC 다.
- 테스트 전부 통과, ruff lint·format 위반 0. 서버를 :8000 에 띄우면 `/health` 가 위 응답을 준다.
- 선택(실 네트워크, 실패해도 완료를 막지 않음 — 결과를 §7 에 붙인다): 한 사이클을 직접 돌려 저장 수 100 이상, 환율에 upbit·bithumb 둘 다 관측, 한 사이클 거래소 호출 수 20회 미만. 1초 주기로 1분 돌려도 rate limit 실패 없음.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — collect 행을 `| collect | 1초 수집 루프·커넥터 3종·/health 동작 | - | /refresh 노출은 003 몫 |` 로. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 001 행 상태 → DONE. **항상 포함.**
- `docs/context/architecture.md` — "현재 구조" 절에 collect 항목: `core/collector.py`(사이클 5단계+락+1초 루프)·`core/live_store.py`(통째 교체)·`core/connectors/{base,upbit,bithumb,binance}.py`(quirk 격리)·`core/rows.py`·`core/errors.py`·`core/config.py`, 앱 골격·GZip·CORS 는 `main.py` lifespan. 데이터 흐름(BE)·USDT 시세 추출·quirk 서술은 구현과 일치해 본문 변경 없음.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
