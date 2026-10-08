# 047 — perp-hyperliquid

상태: DONE | 의존: 046(perp-collect — perp 행 계약·perp 우주·배수 정규화·판정·원문 키 규칙), 001(collect — 원문 싱크·커넥터 공통 규칙), 011(health — 실패 분류)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
배포 뒤 collect 박스 CPU 를 같은 시간대와 비교해 적는다(바이낸스 perp REST 전환 뒤, 2026-10-09). 넘치면 PR revert.
REST `metaAndAssetCtxs` 폴링은 안 한다 — 호가 잔량·시각이 없고 얇은 코인은 impact 호가가 최우선 호가와 수십 bp 어긋나며 가중치 20 이라 1초 폴링이 한도(분당 1,200)의 100% 다(2026-10-09 실호출).
perp 원천 네 번째로 **Hyperliquid(`hyperliquid_perp`)** 무기한 선물을 붙인다. 끝나면 perp 우주에 Hyperliquid 가 세어지고(046 의 "2곳 이상" 에 포함), 현선갭·선선갭 표에 Hyperliquid 행이 생기며 수집 상태 탭에 카드가 하나 는다. 탈중앙 거래소라 API 모양이 세 곳과 전혀 다르고(`coin` 이름 구독·`/info` POST·시간당 펀딩), 그래서 스펙을 따로 뗐다. 공식 문서와 실호출은 2026-10-08 에 확인했다. 인증 없음.

## 2. 범위
- 만드는 것: Hyperliquid 스트림 커넥터(샤드 3개·구독 재조정·핑·재연결·정체 판정), 목록 조회(`meta`), 테스트. 라이브러리 추가 없음.
- 바꾸는 기존 것: 046 의 원천 목록 넷째 자리에 `hyperliquid_perp`(모든 목록·판정·카드의 마지막). 046 §3.3 의 `k` 접두 규칙은 이미 있다.
- 하지 않는 것: Hyperliquid 현물(`@N`)·HIP-3 빌더 perp(`dex:COIN`)·예측시장(`#N`), `l2Book`(호가 1단계면 된다), 유저 계정 구독, 펀딩 예측.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 046 §3.1 perp 행 = `(source, base)` 당 `native_symbol`·`multiplier`·`bid`·`ask`·`bid_size`·`ask_size`(1코인 단위)·`mark`·`funding_rate`(한 주기의 비율)·`next_funding_ms`·`funding_interval_h`·`quote_ts`·`updated_at`. 046 §3.4 행 갱신(호가 넷 동시 검사·오래된 호가 무시·펀딩 보류·우주 밖 버림), §3.2 목록 계약(`id`/`refresh`/`bases`/`set_universe`, 10초 주기, 본문 같으면 파싱 안 함), §3.3 `split_multiplier`. 판정은 019 §3.5 샤드 단위 30초(046 의 세 원천은 REST 폴링이라 샤드 판정이 없다).
- 001 §3.7 원문 싱크 `record(exchange, source, received_at_ms, payload, key)` — 행 갱신 전에, 텍스트 그대로. 011 §3.2 실패 8종.

### 3.2 목록 (`meta`)
- `POST https://api.hyperliquid.xyz/info`, 헤더 `Content-Type: application/json`, 본문 `{"type":"meta"}`(`dex` 생략 = 기본 perp dex — HIP-3 이름 `xyz:TSLA` 는 **여기 없다**, 실측 234종 중 `:` 포함 0). 응답 `{"universe":[{"name":"BTC","szDecimals":5,"maxLeverage":40,"marginTableId":56, "isDelisted":true?, …}], "marginTables":[…]}`. 쓰는 것 = `name` 과 `isDelisted` — **`isDelisted` 가 true 인 것은 뺀다**(실측 234 중 56 — 구독은 되지만 `funding "0.0"`·`midPx null` 만 온다). 원본 base = `name`(`kPEPE` 처럼 접두 `k` 가 그대로), 심볼 = `name`. 배수는 046 §3.3 의 `k` 규칙(실측 7종 `kPEPE kSHIB kBONK kLUNC kFLOKI kDOGS kNEIRO` — `k` = 1,000 이라는 문서 문장은 없고 가격 정황(kPEPE 0.004 ≈ PEPE × 1,000)뿐이다 — §4 수동 확인으로 못 박는다).
- 한도: `/info` 는 IP 당 **분당 가중치 1,200**, `meta` 는 20 — 10초 주기면 분당 120. 시각 필드가 없어 비교용 바이트는 본문 전체. 원문 `rest:/info#meta`, key `symbols:perp`. `funding_interval_h` 는 목록에 없다 — **모든 코인 1**(§3.4).
- 실패: HTTP 429 → `rate_limit`, 5xx → `unavailable`, 그 외 4xx → `bad_request`, 200 인데 JSON 이 아니거나 `universe` 가 없으면 `bad_response`(존재하지 않는 요청에 200·`null` 을 주는 API 다). IP 차단 규칙은 문서에 없다.

### 3.3 스트림
- `wss://api.hyperliquid.xyz/ws`. 구독 `{"method":"subscribe","subscription":{"type":"bbo","coin":"BTC"}}` **한 메시지에 구독 하나**(묶음 형식이 없다). 응답 `{"channel":"subscriptionResponse","data":{"method":"subscribe","subscription":{…}}}` 가 구독마다 하나(시세 아님). 해지는 `"method":"unsubscribe"` + 같은 `subscription`.
- 한도(문서 원문): IP 당 **연결 10개**, 분당 새 연결 30개, **구독 1,000개**, 분당 보내는 메시지 2,000개. 코인마다 구독 2개(§3.4)라 우주 450 코인이면 900 — **상한에 가깝다**. 그래서 구독 대상은 perp 우주 ∩ Hyperliquid 목록(실측 약 180 코인 → 360 구독)이고, 그래도 두 구독의 합이 **900** 을 넘으면 목록 순서(`universe` 배열 순) 앞쪽만 구독하고 경고 1줄(빚 §6). 구독 요청은 **초당 20개**(분당 1,200 — 메시지 한도의 60%)로 보낸다.
- **코인 검증을 구독 전에 한다**(가장 중요한 quirk): 없는 코인·소문자 코인을 구독하면 **에러 프레임 없이 서버가 소켓을 닫는다**(실측) — 그 소켓의 다른 구독까지 함께 끊긴다. 그래서 `meta` 의 `name` 에 정확히(대소문자 그대로) 있는 코인만 구독하고, 목록에서 빠진 코인은 다음 재조정에서 해지한다. 깨진 요청은 `{"channel":"error","data":"Error parsing JSON into valid websocket request: …"}` 로 온다 → `bad_request` 기록(연결 유지).
- 핑 `{"method":"ping"}` → `{"channel":"pong"}`(시세 아님, `key=None`). 서버는 **자기가 60초 동안 아무것도 보내지 않은 연결을 닫는다** — 샤드마다 마지막 수신에서 **30초**가 지나면 ping 을 보내고, pong 이 10초 안에 없으면 재연결·`timeout`. 시세가 흐르는 샤드는 ping 을 보내지 않는다.
- 3 샤드(연결 한도 10 안), 배정 `crc32(name) % 3`, 한 코인의 두 구독은 같은 샤드. 재조정은 019 §3.3 그대로(차이만·60초 자동·배정 0 샤드 미연결). 재연결 백오프 1·2·4…30초, 구독 뒤 첫 시세에서 1초. 새 연결 분당 30개 한도 — 3 샤드가 30초 백오프까지 가도 분당 6개다. 끊김은 "periodically and without announcement" 라 보통의 재연결이다.

### 3.4 코인마다 구독 둘 → 행
- **`bbo`**(`{"type":"bbo","coin":"BTC"}`): 최우선 호가가 **바뀐 블록에만** `{"channel":"bbo","data":{"coin":"BTC","time":<ms>,"bbo":[{"px":"82326.0","sz":"12.18814","n":48},{"px":"82327.0","sz":"10.0","n":1}]}}` — `bbo[0]` 이 bid, `bbo[1]` 이 ask, **원소가 null 일 수 있다**(한쪽 호가 없음 → 호가 불변). `px`·`sz` 문자열 → 046 §3.4 로 넷 동시 갱신(`÷`·`× multiplier`), `quote_ts` = `time`. `n` 은 쓰지 않는다.
- **`activeAssetCtx`**(`{"type":"activeAssetCtx","coin":"BTC"}`): 약 1초마다 `{"channel":"activeAssetCtx","data":{"coin":"BTC","ctx":{"funding":"0.0000125","openInterest":…,"prevDayPx":…,"dayNtlVlm":…,"premium":…,"oraclePx":…,"markPx":"82429.0","midPx":"82429.5","impactPxs":[…],"dayBaseVlm":…}}}`. `funding` → `funding_rate`(**시간당 비율** — 문서 "The funding rate on Hyperliquid is paid every hour", 이자율 0.00125%/h 가 실측 `0.0000125` 와 맞는다), `markPx` → `mark`. `midPx`·`premium`·`impactPxs` 가 `null` 인 코인(상장폐지·거래 없음)은 그 필드만 건너뛴다. 이 메시지도 수신 시각·`updated_at` 을 올린다 — `bbo` 는 조용한 코인에 안 오므로 이것이 행을 살아 있게 한다.
- `funding_interval_h` = **1**, `next_funding_ms` = **다음 정시(UTC 시 경계) epoch ms** — 거래소가 주지 않으므로 커넥터가 수신 시각에서 계산한다(문서 "funding is paid every hour"). 8시간 환산은 하지 않는다 — 048·049 가 `funding / interval` 로 시간당을 쓴다.
- `allMids`(약 5초 주기 전 코인 mid)·`l2Book` 은 쓰지 않는다 — bbo 가 더 정확하고 더 가볍다.
- 원문 키: `bbo:BTC`·`activeAssetCtx:BTC`(원본 `name`), `subscriptionResponse`·`pong`·`error` 는 `key=None`. source `ws:/ws`. 판별: JSON 의 `channel` 이 `bbo`·`activeAssetCtx` 면 시세, `subscriptionResponse`·`pong` 은 응답, `error` 는 거부, 그 밖·JSON 아님은 디코드 실패.

### 3.5 정체 판정·목록·상태
019 §3.5 와 같은 샤드 단위 판정 — 조용한 시간 = 지금 − max(마지막 시세, 연결 중이면 구독 시각), 배정 0 샤드 제외, 연결됐는데 **30초** 무수신 → `stale_stream`, 미연결 → `last_error.kind`, 여러 샤드가 나쁘면 가장 오래 조용한 샤드. 메시지 `"Hyperliquid 스트림 정체: 샤드 0 (구독 60종목) 30초 이상 무수신"`, `url` = WS URL. `/health/collect`·`/refresh`·수집 상태 탭·관리자 카드의 마지막 자리, 표시명 `Hyperliquid`. `markets` = 구독한 코인 수(구독 수의 절반). 046 의 perp 우주 계산에 이 원천의 `bases()` 가 든다 — Hyperliquid 에만 있는 코인(실측 `HYPE` 등)은 다른 원천 한 곳과 겹쳐야 우주에 든다.

## 4. 검증
네트워크 없음 — 가짜 소켓·가짜 REST·가짜 원문 싱크.
- `meta` 에서 `isDelisted` true 제외, `name` 그대로 심볼·`kPEPE → (PEPE, 1000)`, 본문 같으면 파싱 안 함, `universe` 없는 200 → `bad_response`, 429 → `rate_limit`.
- 구독은 한 메시지 하나·초당 20개, `meta` 에 없는 코인·소문자 코인은 **보내지 않는다**, 구독 합이 900 을 넘으면 앞쪽만 + 경고 1줄, 한 코인의 두 구독은 같은 샤드, `subscriptionResponse` 는 시세로 안 셈·`key=None`.
- `bbo`: `bbo[0]`→bid·`bbo[1]`→ask·`sz`·`time`, 한쪽 null 이면 호가 불변(수신은 셈), 오래된 `time` 무시. `activeAssetCtx`: `funding`→`funding_rate`·`markPx`→`mark`, null 필드 건너뜀, 행이 없으면 보류 뒤 첫 bbo 에 실림, `updated_at` 갱신. `funding_interval_h` 1, `next_funding_ms` 가 수신 시각의 다음 정시(경계 정각에 받은 메시지는 그다음 정시).
- 핑: 마지막 수신 30초 뒤 `{"method":"ping"}`, `{"channel":"pong"}` 은 시세 아님, pong 10초 없으면 재연결·`timeout`, 시세가 흐르면 핑 없음. `error` 채널 → `bad_request` 기록·연결 유지. 소켓이 말없이 닫히면 `network` 로 재연결·백오프.
- 원문 키 `bbo:BTC`·`activeAssetCtx:BTC`·`symbols:perp`, 행 갱신 전에 기록. 정체 30초 샤드 단위. `/health/collect` `exchanges` 마지막이 `hyperliquid_perp`, `/refresh` `snapshots` +1.
- web: `npm run lint && npm run build`, 수집 상태 탭·관리자 카드 +1(`Hyperliquid`).
- 수동(실서버): 기동 10초 뒤 `hyperliquid_perp` 가 `ok`·`markets` 150 안팎, BTC 행의 `bid`·`ask` 가 app.hyperliquid.xyz 화면 호가와 맞고 `funding_rate` 가 화면의 시간당 펀딩과 같다(화면이 8시간 환산을 보이면 ÷8 로 맞는지), **kPEPE 행의 `bid` 가 현물 PEPE 가격과 같은 자릿수**(배수 1,000 확인 — 다르면 046 §3.3 의 `k` 규칙을 고친다), 선선갭 표에서 BTC 의 Binance↔Hyperliquid 행이 보인다. 실측해 §5 에: 구독 수, 샤드당 초당 프레임, collect CPU 증가분(046 뒤 누적), 구독 1,000 한도까지의 여유.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# server (워크트리 marketlens-047, uv venv 3.12, 2026-10-09)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m pytest -q
# → All checks passed! / 1841 passed, 1 skipped (047 신규 test_stream_hyperliquid_perp 32, 기존 7개 갱신 — 10원천 순서·snapshots 10)
cd web && npm run lint && npm run build          # oxlint 0건 · vite 빌드 통과 (index 246KB, Tab 211KB)
# 실기동 (이 네트워크에서 api.hyperliquid.xyz REST·WS 열림) — 포트 8041, Redis·Influx 없이, main 수집기(:8050)와 나란히
cd server && .venv/bin/uvicorn app.main:app --port 8041
curl -s localhost:8041/health/collect           # 기동 50초 뒤 10원천 전부 ok, 로그에 Hyperliquid 경고 0줄
#   markets: … binance_perp 500 bybit_perp 715 bitget_perp 680 | hyperliquid_perp 178
curl -s -X POST localhost:8041/refresh          # snapshots 10항목, hyperliquid_perp saved 177, failures []
curl -s -X POST https://api.hyperliquid.xyz/info -d '{"type":"allMids"}'   # kPEPE 0.003715 ↔ 바이낸스 현물 PEPE 3.72e-06 — 배수 1,000 확인
```
실측(2026-10-09 로컬 M4):
- `meta` 234종 중 `isDelisted` 56 → 178 코인이 전부 perp 우주에 들어 구독 356개(한도 900 까지 544 여유, 1,000 까지 644). 접두 `k` 7종(`kPEPE kSHIB kBONK kLUNC kFLOKI kDOGS kNEIRO`). HIP-3(`:` 포함) 0.
- 배수 확인: `allMids` kPEPE 0.003715 ↔ 바이낸스 현물 PEPE 최우선 bid 3.72e-06 → **정확히 1,000 배**. 펀딩 `BTC "0.0000089259"`·`kPEPE "-0.0000436026"` 은 8시간 비율(약 0.0001)의 1/8 크기 — 시간당 비율이 맞다.
- 프레임/초(코인 60 = 샤드 하나 크기, 10초): `bbo` 526·`activeAssetCtx` 79, 약 100KB/초. 178 코인 3샤드면 합계 약 **1,800 프레임/초**(옛 바이낸스 perp WS 호가 750 의 2.4배 — 바이낸스도 10-09 에 REST 로 바뀌어 perp WS 는 이제 Hyperliquid 뿐; `bbo` 는 "바뀐 블록에만" 이지만 상위 코인은 거의 매 블록 바뀐다).
- 수집기 CPU(M4, 1코어 기준 %, ps 3초 간격 12회, main 의 현물 6 + perp 3 수집기와 나란히): 28~43 vs 27~40 — **+1~3%p**, RSS 약 +15MB(150~185MB vs 140~177MB). 틱 단계 60초 로그 차이 없음.
- WS 실측 모양: `bbo` 의 `bbo[0]`·`bbo[1]` 은 `{"px","sz","n"}` 객체, `activeAssetCtx` 의 `premium`·`midPx`·`impactPxs` 는 거래 없는 코인에서 null, `subscriptionResponse` 는 구독·해지마다 하나, `pong` 은 `{"channel":"pong"}`.

## 6. 갱신할 문서
- `docs/context/status.md` — 행 추가 `| perp-hyperliquid | server: Hyperliquid perp — `meta` 10초(isDelisted 제외)·코인마다 `bbo`+`activeAssetCtx` 3샤드·구독 전 코인 검증·ping 30초·펀딩 시간당(주기 1, 다음 정시 계산) | web: 수집 상태 카드 | 구독 1,000 한도 |`. perp-collect 행의 "perp 원천 3개" → 4개. 알려진 빚: "(047) Hyperliquid 구독은 IP 당 1,000 — 우주가 450 코인을 넘으면 앞쪽만 구독한다(경고). 연결 10개 한도라 샤드로 못 푼다", "(047) `k` 접두 = 1,000 과 `funding` 이 시간당 비율이라는 것은 문서 명문이 없고 실측 정황이다(§4 수동 확인 뒤 이 줄을 지운다)", "(047) Hyperliquid 의 다음 정산 시각은 계산값(정시)이다". **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 047 행 → DONE. **항상 포함.**
- `docs/context/architecture.md` — 핵심 설계 결정 둘째 행의 perp 문장에 "Hyperliquid 는 bbo+activeAssetCtx(047)"; mermaid 에 `HL[Hyperliquid WS<br/>3샤드]`; "현재 구조" 에 perp-hyperliquid 항목(커넥터·코인 검증·정시 계산).
- `docs/context/dev-setup.md` — `/health/collect` 기대값 perp 원천 "3개" → "4개(`hyperliquid_perp` 마지막)".
- `docs/context/product.md` — 용어 **perp 원천** 의 괄호 "(·`hyperliquid_perp`)" 를 본문으로, "Hyperliquid 펀딩은 매시간(주기 1)" 한 구절.
- 스펙(같은 PR 에서): `046-perp-collect.md` §3.1 원천 순서 문장의 "(047)" 표기 삭제·§3.2 "원천 3곳(047 뒤 4곳)" → 4곳·§3.8 `exchanges` 순서에 `hyperliquid_perp`, `011-health.md` §3.1 perp 원천 수.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `server/app/core/streams/hyperliquid_perp.py`(`HyperliquidPerpStream`), `tests/test_stream_hyperliquid_perp.py`(32), `core/config.py`(`PERP_SOURCES` 넷째), `main.py`(배선), 주석만 `core/collect.py`·`core/perp_universe.py`·`core/models.py`·`features/health/service.py`, 기존 테스트 7개(10원천 순서·snapshots 10·health 평균 분모 10), web `src/shared/format.ts`(표시명 `Hyperliquid`)·`admin/admin.js`(목록 +1). 문서는 §6 전부.
- 추측한 지점 (묻지 않고 정한 사소한 것): ① 핑 타이머는 OKX(045)와 같은 꼴 — 5초 간격으로 조용한 시간을 보므로 ping 은 30~35초 사이에 나간다(서버 60초 안). ② `error` 채널은 그 샤드 상태의 `last_error` 에 `bad_request` 로 적고 경고 1줄, 연결·판정은 그대로(연결 중인 샤드는 `last_error` 를 보지 않는다). ③ 한도 초과 경고는 제외 수가 바뀔 때만 1줄(매초 set_universe 가 불리므로). ④ 같은 base 로 정규화되는 코인이 둘이면 046 §3.2 규칙(배수 1 우선, 없으면 목록 첫 것) — 실측엔 없다. ⑤ `activeAssetCtx` 의 `data` 가 객체가 아니거나 `ctx` 가 객체가 아니면 디코드 실패로 센다. ⑥ 샤드 배정 해시는 코인 이름 바이트 그대로(`kPEPE` 소문자 포함). 실행 중 함께 고친 스펙 절: §1(배포 뒤 CPU 비교·REST 폴링 안 함 결정), §3.1·§3.5(판정 근거를 019 로 — 046 세 원천이 REST 로 바뀌어 샤드 판정이 거기 없다), §5.
- 남은 빚: status.md 의 (047) 항목 3개 — 배포 뒤 CPU 비교(넘치면 PR revert), 구독 1,000 한도(450 코인 넘으면 앞쪽만), 다음 정산 시각은 계산값. `k` = 1,000·시간당 펀딩은 §5 실측으로 확인돼 빚에서 뺐다. 프레임 수가 세 곳보다 많아(약 1,800/초) 배포 때 CPU 를 다시 잰다.
