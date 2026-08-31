# 003 — spreads

상태: TODO | 의존: 001(collect), 002(web-shell)

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
서버 메모리에 수집된 1초 시세만으로 **전 (국내 거래소 × 해외 거래소 × 코인) 페어의 김프/역프 표**를 `GET /spreads` 로 낸다. 웹의 스프레드 탭(002 의 placeholder)을 실데이터로 바꾼다.

끝나면 이렇게 보인다.
- 서버 기동 후 수 초 내 `/spreads` 가 수백 행을 반환한다.
- 브라우저에서 김프/역프와 환율이 1초마다 갱신된다.
- 서버가 죽으면 행이 흐려진다(stale).
- 입출금 열은 아직 전부 `?` 다.

## 2. 범위
만드는 것:
- 기능 폴더 `spreads` (server·web).
- BE 엔드포인트 `GET /spreads`, `POST /refresh`.
- FE 스프레드 탭과 1초 폴링.
- 김프 수식 공유 모듈. 004(analysis)·005(history)도 쓰므로 `server/app/core/premium.py` 에 공개 함수 `premium_percent(*, buy_krw: float, sell_krw: float) -> float` 를 둔다. 값은 `(sell/buy − 1) × 100`. 이 스펙의 fwd/rev 도 이 함수로 계산한다.

하지 않는 것:
- Influx 저장·아카이브. `spark` 는 항상 빈 배열이다 — 채우는 스펙은 아직 없다(후속 스펙 몫).
- 망 단위 입출금 판정 **규칙**(정규화·매칭·tie-break) — 006 wallet-status 가 정의한다. 이 스펙의 행은 그 규칙의 결과를 싣기만 한다(아래 §3.2-4).
- 분석 엔드포인트(004 analysis).

바꾸는 기존 것:
- 002 의 셸 화면. 스프레드 placeholder 를 진짜 탭으로 교체하고 폴링을 시작한다. 행 클릭 시 기록 탭으로 피벗할 "선택된 심볼" 상태를 추가한다.
- 002 의 `shared/` 파일은 수정하지 않는다.

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
1. 기준 거래소(`upbit`)의 환율이 없거나 ask/bid 가 0 이하 → **404** `market_data_not_found`. message: "메모리에 upbit 거래소의 KRW-USDT 환율이 없습니다. POST /refresh 로 수집했는지 확인하세요.", detail `{"exchange": "upbit"}`.
2. 스냅샷을 호가통화로 나눈다. `KRW` → 국내, `USDT` → 해외, 그 외 무시. 어느 한쪽이 비면 **404** `market_data_not_found`. message: "스프레드를 계산할 스냅샷이 부족합니다 (국내 KRW / 해외 USDT). 먼저 POST /refresh 로 수집하세요.", detail 에 양쪽 거래소 목록. 수집 전 빈 메모리는 1 또는 2 에서 404 가 나며 구분하지 않는다.
3. 페어 생성. 국내 거래소마다 **그 거래소 자신의 환율**을 쓴다. 환율이 없는 국내 거래소는 **행 전체가 빠진다** — 남의 환율을 빌리면 테더 프리미엄이 섞이기 때문이다. 해외 거래소마다, 양쪽에 **모두 상장된** 코인만 행이 된다(한쪽 상장은 페어 아님). 제외 코인 목록(대소문자 무시)의 코인은 제외한다. 국내==해외 거래소 조합은 만들지 않는다.
4. 행 하나의 규칙. `rateAsk` = 국내 거래소 환율 ask, `rateBid` = bid.
   - 최우선 호가 4개: 국내 bid/ask, 해외 bid/ask (각 1단계의 price·size).
   - 수식은 **체결되는 쪽 호가**를 쓴다(살 때 ask, 팔 때 bid). 환율도 방향별이다. 김프는 원화로 USDT 를 **사서** 해외로 보내므로 `rateAsk`, 역프는 받은 USDT 를 원화로 **팔므로** `rateBid`. 둘 다 실제 체결보다 유리하게 계산되는 일이 없도록 보수적인 쪽이다.
     ```
     fwd = (dom_bid.price / (fx_ask.price × rateAsk) − 1) × 100   # 해외 ask 에 사서 국내 bid 에 판다 (정방향 김프 %)
     rev = (fx_bid.price × rateBid / dom_ask.price − 1) × 100     # 국내 ask 에 사서 해외 bid 에 판다 (역프 %)
     ```
   - 유동성(USD)은 **최우선 1단계만** 본다. 호가 걷기 없음. 매수·매도 중 **작은 쪽** 금액이다.
     ```
     liqDom = min(dom_bid.price×size, dom_ask.price×size) / rateAsk   # KRW→USD, 방향 없는 표시값이라 ask 로 통일
     liqFx  = min(fx_bid.price×size,  fx_ask.price×size)               # 이미 USDT
     ```
   - `usd` = 해외 스냅샷의 마지막 체결가. `spark` = `[]`(항상).
   - `age` = 현재 시각 − 양측 스냅샷 갱신 시각 중 **오래된 쪽**. 초 단위, 0 미만이면 0.
   - `status`: `age ≥ 5.0` → `stale`, 아니면 `ok`. 단 호가 4개 중 하나라도 비었거나, 해외 ask·국내 bid/ask 가격이 0 이하면 `fail` 이고(국내 0 을 통과시키면 rev 분모가 0 이 된다) `fwd` `rev` `usd` `liqDom` `liqFx` `rateAsk` `rateBid` 는 전부 0. **fail 이어도 입출금 값과 age 는 싣는다.**
   - 입출금 5필드(`netDom depDom wdDom depFx wdFx`): **006 §3.7 의 망 판정**으로 채운다. 국내 망 정보가 없으면(키 없음 등) 코인 단위 값·`netDom` null 로 강등된다 — 그래서 키 없이 기동해도 5키는 항상 존재한다.
5. 행 정렬: `(sym, dom, fx)` 오름차순 고정.
6. 최상위 `rate` = 기준 거래소(`upbit`) 환율 **ask**(표시용). `data_received_at` = 저장소 마지막 수신 시각(ms), 수신 기록이 아직 없으면 null(스냅샷이 아예 없는 경우는 위 1·2 에서 이미 404 다). `fetched_at` = 응답 시각(ms).

응답 예시 (행 키는 camelCase, 최상위 키는 snake_case. `spark`·`netDom`·입출금의 빈 값 모양을 보인다):
```json
{"rate": 1392.0,
 "rows": [{"sym": "BTC", "dom": "bithumb", "fx": "binance", "fwd": 0.53, "rev": -0.72, "usd": 64950.3,
           "spark": [], "status": "ok", "age": 0.8, "liqDom": 2140.25, "liqFx": 2141.78,
           "rateAsk": 1392.0, "rateBid": 1391.0,
           "netDom": null, "depDom": null, "wdDom": null, "depFx": null, "wdFx": null}],
 "data_received_at": 1787139510649, "fetched_at": 1787139510712}
```
행 키의 의미:
- `sym` 코인. `dom` 국내 거래소 id. `fx` 해외 거래소 id.
- `fwd` 정방향 김프 %. `rev` 역프 %. 소수 그대로, 반올림하지 않는다.
- `usd` 해외 마지막 체결가(USDT).
- `spark` 프리미엄 추이. 이 스펙에선 항상 `[]`.
- `status` `ok`·`stale`·`fail` 중 하나. `age` 오래된 쪽 스냅샷 경과 초.
- `liqDom` `liqFx` 최우선 호가 체결 가능 금액(USD), 작은 쪽.
- `rateAsk` `rateBid` 이 행 국내 거래소의 USDT 매수/매도 환율.
- `netDom` 판정된 국내 망 이름(문자열, 못 정하면 null). `depDom` `wdDom` 국내 입금/출금. `depFx` `wdFx` 해외 입금/출금. 값은 true 열림, false 막힘, null 모름 — **null 을 열림으로 읽는 코드는 버그다.**

### 3.3 보조 엔드포인트
- `POST /refresh` 200: 001 수집 서비스의 1회 실행 결과를 반환한다 — `snapshots[]`(거래소당 1항목 `{exchange, saved, calls, wallet_status_available}`)·`usdkrw[]`(관측된 국내 거래소 `{exchange, ask, bid}`)·`total_saved`·`duration_ms`·`failures[]`(`{exchange, error_code, message}`)·`warnings[]`·`fetched_at`. 거래소 일부 실패는 HTTP 에러가 아니라 `failures`/`warnings` 에 담긴다. 수집 루프가 이미 1초마다 도니 **수동 트리거·진단용**이며 동시 호출은 직렬화된다.
  - 토큰: `REFRESH_TOKEN` 이 빈 문자열이면 검사 없음. 설정돼 있으면 헤더 `X-Refresh-Token` 이 없거나 다르면 **401** `{"detail": "X-Refresh-Token 헤더가 없거나 올바르지 않습니다."}` (FastAPI 기본 형식 — `error` 포장 없음). 비교는 타이밍 안전 비교.

### 3.4 FE — 폴링과 002 와의 계약
- 002 가 만든 공유 피드에 "스프레드 적용" 동작이 있다. **행 배열과 환율을 통째로 교체**하고, 코인×거래소표시명 단위의 입출금 조회표를 재구성한다(`net` 은 `netDom`, 없으면 `'–'`). 002 의 1.5초 mock tick 은 각 행의 `age` 를 1.5 씩 올린다. 그래서 폴링이 죽으면 age 가 자라 저절로 stale 이 된다.
- 이 스펙이 1초 폴링을 제공한다. `GET ${API_BASE}/spreads` 성공 시 행의 `dom`/`fx` 를 표시명(`upbit→업비트`, `bithumb→빗썸`, `binance→Binance`, 모르는 id 는 그대로)으로 바꾼 뒤 공유 피드에 적용하고 화면을 갱신한다. **실패(네트워크·비 2xx)는 무시하고 직전 데이터를 유지**한다.
- 폴링은 spreads 기능 폴더 안에 살고, 셸이 공유 피드를 만든 직후 시작된다. 002 의 `shared/` 는 수정하지 않는다(shared → feature import 금지).
- 헤더(002 KPI 스트립)의 "USDT/KRW 암묵환율" 은 `/spreads` 최상위 `rate` 로 채워진다. `rate>0` 이면 `₩1,392.0`(소수 1자리 ko-KR), 0 이면 `–`.
- 셸: 스프레드 placeholder → 스프레드 탭. 행 클릭 → 선택된 심볼을 저장하고 기록 탭으로 전환한다. 기록 탭은 아직 placeholder 이므로 선택된 심볼을 history 탭에 넘겨 `history — ... · BTC` 처럼 보이게 한다(005 가 진짜 탭으로 교체).

### 3.5 FE — 스프레드 탭 화면
- 표 컬럼 순서: **심볼 | 국내가 KRW | 김프(또는 역프) | 입출금 | 네트워크**. 국내가 열만 가변 폭, 나머지는 고정 폭. 코인 1개 = 행 1개(거래소 페어는 코인별로 집계). 색·간격·그림자는 `docs/design/theme.css` 를 쓴다.
- 집계: "기준 국내 거래소" 필터(모두/업비트/빗썸)를 거친 행을 코인별로 모은다. `fail` 아닌 행이 live 다. 코인별로 김프 최대 행과 역프 최대 행을 고른다. 코인 age = live 중 최소. 전부 fail 이면 "전부 fail", live 전부 `age ≥ 5` 면 "전부 stale" 상태다.
- 국내가 = `₩` + KRW 표시 형식의 `usd × 환율 × (1 + fwd/100)`. fail 이거나 usd 없으면 `–`.
- 김프 셀 = `[출발 거래소 태그] → [도착 거래소 태그] [+1.23%]`. 김프 보기면 해외→국내, 역프 보기면 국내→해외. 값 색은 퍼센트 색 규칙(한국식: 양수 빨강·음수 파랑·0 중립). 값 없으면 `–`.
- 입출금 셀 = 태그 2개 `출금 가능|중단|?` `입금 가능|중단|?`. 출금 거래소는 출발, 입금 거래소는 도착. 테두리 규칙: true 강조색 실선, false 중립색 실선, **null 은 점선** — 모름을 열림·막힘과 한눈에 구분하기 위해서다. 이 스펙에선 전부 null 이라 **모든 행이 `?` 점선**이다. 네트워크 셀 = `net`(없으면 `–`).
- **null 은 열림이 아니다.** "입출금 가능만" 필터는 출금·입금 둘 다 true 일 때만 통과한다.
- 강조: fail 아님, stale 아님, 값 ≥ 임계값이면 강조 행이다. 임계값 기본 **1.5%**, 숫자 입력으로 0.1 단위 조정. 강조 행은 배경을 옅은 강조색으로 칠하고 심볼을 강조색 + 점으로 표시한다. `stale` 행은 흐리게(반투명) 보인다.
- 필터바 1행: 심볼 검색(대소문자 무시), 기준 국내 거래소 분절 버튼, 임계값 숫자 입력, "임계 초과만"(전부 fail 아니고 값 ≥ 임계), "입출금 가능만", 우측 `N / M 코인 표시`.
- 필터바 2행: "기준 보기"(김프/역프).
- 정렬: 헤더 클릭. 기본 김프 내림차순. 전부 fail 인 코인은 항상 맨 뒤, null 값은 뒤. 같은 키 재클릭 = 방향 반전. 새 키는 심볼·네트워크만 오름차순, 나머지 내림차순. 키: 김프/역프(보기에 따라), 국내가, 입출금(가능 > 모름 > 중단), 네트워크, 심볼.
- 빈 상태: 행이 0개면 "백엔드에서 스프레드를 받는 중입니다…". 필터 결과 0개면 "조건에 맞는 코인이 없습니다. 필터를 넓혀 보세요.".

## 4. 검증
BE (네트워크 없음 — 저장소에 직접 시드):
- 빈 메모리에서 `/spreads` 는 404 `market_data_not_found` 다.
- 스냅샷은 있지만 기준 거래소(upbit) 환율이 없으면 404 다.
- 환율이 없는 국내 거래소(예: bithumb)의 행은 전부 빠지고 upbit 행은 남는다.
- 한쪽에만 상장된 코인은 행이 없다.
- 제외 코인 목록에 XRP 가 있으면 XRP 행이 없다(소문자도 동일).
- fwd 는 국내 bid·해외 ask·rateAsk 로, rev 는 해외 bid·국내 ask·rateBid 로 계산된다(ask≠bid 인 환율로 시드해 수식값과 일치 확인).
- ask=bid 인 환율이면 fwd/rev 가 단일 환율 수식과 같다.
- 거래소마다 환율이 다르면 각 행의 `rateAsk`/`rateBid` 가 자기 국내 거래소 값이다.
- 호가가 빈 스냅샷은 `status=fail` 이고 숫자 필드가 0, 입출금 값은 유지된다.
- 갱신 시각이 6초 전인 스냅샷은 `stale`, 0.5초 전이면 `ok`, age 는 오래된 쪽 기준이다.
- liqDom 은 최우선 매수·매도 금액 중 작은 쪽을 rateAsk 로 나눈 값이다.
- 행은 `(sym, dom, fx)` 오름차순이다.
- 응답 행 키가 camelCase(`liqDom`·`rateAsk`…) 이고 `spark==[]`, `netDom is None`.
- `REFRESH_TOKEN` 설정 + 헤더 없음 → 401. 올바른 헤더 → 200. 미설정이면 헤더 없이 200.

실서버 확인 (기동 후 수 초 뒤 실제 `/spreads` 호출):
- 최상위 `rate > 1000`, 행 수 > 100.
- 행 키 집합이 정확히 `sym dom fx fwd rev usd spark status age liqDom liqFx rateAsk rateBid depDom wdDom depFx wdFx netDom` 18개다.
- 첫 행 `status` 는 `ok`·`stale`·`fail` 중 하나, `spark==[]`, `netDom is None`.
- 전체 행이 `(sym, dom, fx)` 오름차순이다.
- `POST /refresh` 의 `total_saved > 100`.
- `REFRESH_TOKEN` 을 설정해 띄운 서버에 헤더 없이 `POST /refresh` → 401.

FE 수동 확인:
- 수백 행 표시, 1초마다 값이 변한다. KPI 환율이 `₩1,3xx.x` 로 보인다.
- 입출금 태그 전부 `?` 점선, 네트워크 `–`.
- 임계값 1.5 이상 코인만 배경 강조·심볼 점. 임계값 바꾸면 즉시 반영된다.
- 서버 kill → 표는 남고 약 5초 뒤 행이 흐려진다. 서버 복구 → 다시 선명해진다.
- 역프 기준 토글 시 화살표 방향·값이 바뀐다. 행 클릭 → 기록 탭으로 전환되며 심볼이 보인다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
(실행 후 기록)

## 6. 갱신할 문서
- `docs/context/status.md` — spreads 행을 `| spreads | /spreads·/refresh 동작 (입출금 전부 null) | 실데이터 탭·1초 폴링 | spark 빈 배열, 망 판정은 006 |` 로. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 003 행 상태 → DONE. **항상 포함.**
- `docs/context/dev-setup.md` — 검증용 스모크를 §4 실서버 기준으로 교체: 최상위 `rate > 1000`·행 수 > 100·행 키 정확히 18개(키 순서는 §3.2 응답 예시와 동일).
- `docs/context/architecture.md` — "현재 구조" 절에 spreads 항목: `core/premium.py`(`premium_percent`), `features/spreads/`(service 순수 계산·router 2 엔드포인트·models), `/refresh` 응답 모양(`snapshots[]` 거래소당 1항목 등), web `features/spreads/`(1초 폴링·확장 타입). FE 데이터 흐름 문구는 002 §6 에서 이미 반영됨 — 확인만.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
