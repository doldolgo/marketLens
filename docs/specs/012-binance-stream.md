# 012 — binance-stream

상태: DONE | 의존: 001(collect — 행 계약·마켓 우주·틱 판정·원문 싱크·커넥터 공통 규칙), 011(health — 실패 분류·구간 추적), 007(deploy — lifespan)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.

## 1. 목적
바이낸스 시세를 **WebSocket 만으로** 받는다 — 심볼마다 상위 20단계 호가(1초)와 마지막 체결가(1초). 메모리의 바이낸스 행은 `asks`/`bids` 에 최대 20단계가 들어 있어 003 의 슬리피지와 004 의 `/slippage`·`/arbitrage`·`/matrix` 가 해외 쪽에서도 성립한다. REST 는 심볼 목록(`exchangeInfo`)에만 쓴다.
REST 로 깊이를 받을 수는 없다 — `GET /api/v3/depth` 는 심볼당 1회·weight 5 라 300 종목을 1초 주기로 돌리면 분당 90,000 으로 IP 한도 6,000 의 15배다.

## 2. 범위
- 만드는 것: `core/streams/binance.py` 의 바이낸스 스트림 커넥터(샤드 3개·구독 재조정·디코딩·핑·재연결·정체 판정), 심볼 목록 REST 조회, 001 스트림 상태 계약의 바이낸스 구현, 테스트. 라이브러리 `websockets`(001 이 이미 쓴다 — 추가 의존 없음).
- 바꾸는 기존 것: 001 의 바이낸스 심볼 집합 계약(`core/contracts.py` `ForeignSymbolSource`)에 `set_universe(bases)` 를 더하고, 마켓 우주(`core/universe.py`)가 우주를 확정할 때마다 그것을 부른다(§3.3). `main.py` lifespan 이 커넥터를 우주·틱 루프·`/refresh` 트리거에 꽂는다.
- 하지 않는 것: REST 시세 호출(없다). diff depth 와 로컬 북 재구성(§3.2). 깊이를 쓰는 계산(003·004). HTTP 계약 변경. web.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001 §3.3: 행 = `(exchange, base)` 당 `quote`·`native_symbol`·`price`·`price_timestamp`·`asks`·`bids`(누적 1,000,000 USDT 도달 단계까지, 최소 1단계)·입출금 3필드(물려받음)·`updated_at`. 거래소별 스트림 상태 `{connected, last_message_at, last_error, subscribed}`.
- 001 §3.2: 마켓 우주 = 국내 KRW base ∩ 바이낸스 USDT base. 이 스펙은 **바이낸스 USDT 현물 심볼 집합**을 제공하고, 우주의 심볼만 구독한다. 10분 갱신·`/refresh` 즉시 갱신.
- 001 §3.7: 받은 모든 프레임과 REST 응답 본문은 해석 전에 원문 싱크 `record(exchange, source, received_at_ms, payload)` 로.
- 001 §3.8·011: 매 틱 성공/실패를 판정해 추적기에 넘긴다. 실패 종류 8종.

### 3.2 스트림
- 엔드포인트 `wss://data-stream.binance.vision/stream`(시세 전용 호스트, 결합 스트림). 메시지는 `{"stream":"<이름>","data":<페이로드>}` 로 온다.
- 심볼마다 두 스트림: **`<symbol>@depth20`** — 상위 20단계를 **1000ms 마다 통째로** 보내는 자기완결 스냅샷(`{"lastUpdateId":…,"bids":[["0.0024","10"],…],"asks":[["0.0026","100"],…]}`, 가격·수량은 문자열, bids 내림차순·asks 오름차순). **`<symbol>@miniTicker`** — 1000ms 마다 `{"e":"24hrMiniTicker","E":<event ms>,"s":"BTCUSDT","c":"<마지막 가격>",…}`. 스트림 이름은 소문자.
- 자기완결이 선택의 이유다 — 끊겼다 붙어도 다음 메시지 하나로 북이 복구되어 REST 재동기화·시퀀스·갭 감지가 필요 없다. `<symbol>@depth`(diff)는 쓰지 않는다(심볼마다 `depth?limit=5000` 초기화가 weight 250 이라 300 종목이면 75,000).
- 전 종목 배열 스트림(`!miniTicker@arr`)은 쓰지 않는다 — 바뀐 심볼 전부(수천 개)를 매초 실어 우주 300 종목의 개별 스트림보다 무겁다. `!bookTicker` 는 2022-12 에 폐지됐다.
- 깊이·체결가가 아닌 프레임(구독 응답 `{"result":null,"id":1}`, `serverShutdown`, 깨진 JSON)은 원문 싱크에는 기록하되 시세 수신으로 세지 않는다. `{"stream":"!serverShutdown"}` 을 받으면 즉시 재연결한다.
- 대역폭 감: depth20 ~1.3KB + miniTicker ~0.2KB, 300 종목 × 1/s ≈ 450KB/s.

### 3.3 심볼 목록과 샤딩
- 심볼 목록: `GET https://api.binance.com/api/v3/exchangeInfo`(weight 20) 의 `symbols[]` 중 `status == "TRADING"` 이고 `quoteAsset == "USDT"` 인 것. base = `baseAsset`. 10분마다 갱신, 응답 본문은 원문 싱크(`rest:/api/v3/exchangeInfo`). 실패 시 직전 목록 유지 + 경고.
- 심볼 집합 계약은 커넥터 자신이 구현한다 — `refresh(client) -> int`(exchangeInfo 1회, 실패는 거래소 예외), `bases() -> set[str]`, `set_universe(bases)`. base↔symbol 은 exchangeInfo 의 `baseAsset`→`symbol` 맵 하나이고, 한 base 에 USDT 심볼이 둘 이상이면 처음 것을 쓴다. 우주 base 중 맵에 없는 것은 구독 대상이 아니다.
- 구독 대상 = 001 의 마켓 우주 심볼. 001 의 우주 갱신(기동·10분·`/refresh`)이 우주를 확정할 때마다 `set_universe(bases)` 를 부르고, 커넥터는 그 자리에서 빠진 심볼의 행을 메모리에서 지운 뒤 재조정을 깨운다. 재조정은 연결된 샤드마다 원하는 구독과 실제 구독의 차이만 보낸다 — 새 심볼은 SUBSCRIBE, 빠진 심볼은 UNSUBSCRIBE. 실제 구독 집합은 **소켓에 묶인다** — 소켓이 바뀌면 빈 집합에서 시작해 연결 직후 배정 전체를 구독하고, 보내는 도중 소켓이 바뀐 재조정은 결과를 남기지 않는다(죽은 소켓에 보낸 구독을 새 소켓 것으로 세면 새 소켓은 차이가 없다고 보고 아무것도 구독하지 않는다). `set_universe` 가 깨우지 않아도 **60초마다** 한 번 돈다(재연결 뒤 등 어긋남을 맞춘다). 기동 직후 우주가 비어 있으면 구독이 없다 — 배정 심볼이 0 인 샤드는 연결하지 않고 배정이 생길 때까지 기다린다.
- **3 샤드**(소켓 3개). 배정은 **심볼 문자열의 안정 해시(crc32) % 3** — 상장·상폐가 나머지 심볼의 배정을 흔들지 않고 재기동해도 같다. 한 소켓이 죽어도 1/3 만 잃고, 24시간 강제 종료가 샤드마다 다른 시각에 걸린다. 심볼의 두 스트림은 같은 샤드에 둔다. 샤드당 ≈ 200 스트림(한도 1,024).
- 구독은 연결 후 `{"method":"SUBSCRIBE","params":["btcusdt@depth20","btcusdt@miniTicker",…],"id":<int>}` 로 한다. 한 메시지의 `params` 는 **100 개 이하**, 제어 메시지는 **초당 4개 이하**로 보낸다(한도: 연결당 수신 메시지 5개/초 — PING·PONG 포함) — 제어 메시지 하나를 보낼 때마다 **0.25초** 를 쉰다. `id` 는 샤드별로 1부터 올라가는 정수. 응답 `{"result":null,"id":…}` 은 시세로 세지 않는다. `error` 키가 있는 응답(구독 거부)은 `bad_request` 로 실패하고 재연결한다.
- 연결 시도는 IP 당 5분에 300회 한도 — 재연결은 지수 백오프(1·2·4…30초 상한), **구독까지 성공하면 1초로 리셋** — 성공의 증거는 구독 뒤 그 샤드의 **첫 시세 프레임**이다(001 과 같은 규칙 — 구독 메시지를 보낸 것만으로는 성공이 아니다). 서버는 20초마다 ping 프레임을 보내고 1분 안에 pong 이 없으면 끊는다 — 라이브러리의 자동 pong 을 쓴다. 클라이언트도 20초 간격의 라이브러리 keepalive ping 을 보낸다(20초 안에 pong 이 없으면 끊고 재연결 — 조용히 죽은 TCP 를 40초 안에 감지한다). 연결은 24시간에 한 번 서버가 끊으므로 재연결이 정상 경로다. `!serverShutdown` 뒤 재연결은 백오프 없이 즉시이고 백오프 값은 그대로 둔다.
- 핸드셰이크가 HTTP 상태로 거부되면 011 §3.2 의 **바이낸스 REST 규칙**으로 분류한다: 429 `rate_limit`, 418·403 `banned`, 5xx `unavailable`, 그 외 4xx `bad_request`, 그 밖의 상태 `bad_response`. `Retry-After` 가 초 정수면 `retry_after_sec`. exchangeInfo 의 비-200 도 같은 규칙이다. 실패 `message` 는 샤드 번호를 말한다.

### 3.4 행 갱신
- depth20 메시지 → 그 심볼 행의 `asks`/`bids` 교체(문자열 → float, 잔량 ≤0 단계 제거, 누적 1,000,000 USDT 도달 단계까지 — 첫 단계가 이미 넘어도 1단계는 남긴다), `updated_at` = 수신 시각. 한쪽이 비면 행을 저장하지 않는다(있던 행은 지운다).
- miniTicker 메시지 → `price = c`, `price_timestamp = E`. 행이 없으면 보류했다가 depth20 이 오면 함께 싣는다. 체결가가 없으면 mid — 이때 `price_timestamp` 는 **수신 시각**이다(depth20 페이로드에는 거래소 시각이 없다).
- 캐시 키는 원본 심볼 대문자(`BTCUSDT`) — 스트림 이름(`btcusdt@…`)과 `native_symbol` 이 같은 값으로 이어져 base↔symbol 변환이 한 곳(`baseAsset`)에만 있다. 스트림 이름의 심볼이 맵에 없으면 그 프레임은 버린다(시세로 세지 않는다).
- 입출금 3필드 물려받기·우주 밖 버리기는 001 §3.5.

### 3.5 정체 판정 (매 틱, 샤드 단위)
- 샤드마다 시계를 따로 둔다: `last_message_at`(그 샤드가 마지막으로 시세 프레임을 받은 시각), 배정 심볼 수, 이번 연결의 구독 시각. 구독 시각 = 그 연결의 첫 SUBSCRIBE 묶음을 **모두 보낸** 시각(`connected_since`). 소켓이 열리고 그 묶음을 보내는 동안(≤0.75초)은 소켓이 열린 시각을 임시로 둔다 — 직전 연결의 수신 시각으로 정체가 되지 않게. **배정 심볼이 0 인 샤드는 판정 대상이 아니다**(미연결이어도 — 배정이 없으면 연결하지 않는다). 샤드의 조용한 시간 = 지금 − max(`last_message_at`, 연결 중이면 구독 시각). 한 번도 시세를 못 받은 미연결 샤드가 가장 조용하다.
- 샤드 하나의 판정은 001 §3.8 과 같다: 미연결이면 `last_error.kind`(오류 기록 없이 첫 시도 결과도 없으면 판정 없음, 시세를 받았다가 오류 없이 닫혔으면 `network`), 연결됐는데 조용한 시간 **30초** 이상이면 `stale_stream`.
- 매 틱 바이낸스는 **셋 다 정상**이어야 성공이다. 판정 대상 샤드 중 하나라도 실패면 실패 — 여러 샤드가 동시에 나쁘면 **가장 오래 조용한** 샤드를 고른다(동률이면 작은 번호). 실패가 없고 판정 대상 전부가 아직 판정 없음이면 바이낸스도 판정 없음, 그 밖(일부 판정 없음 + 나머지 성공 포함)은 성공. 실패 `message` 는 샤드를 말한다: `"바이낸스 스트림 정체: 샤드 2 (구독 7종목) 30초 이상 무수신"`(종목 수 = 그 샤드 배정 수). `url` = WS URL, `status_code` 는 핸드셰이크 실패일 때만.
- 정체 중에도 행은 그대로 남는다(001 — 행은 메시지로만 바뀐다). 스트림이 돌아오면 다음 틱이 성공으로 기록돼 구간이 닫힌다.
- 001 스트림 상태 계약의 바이낸스 값(`store.stream("binance")` 하나로 집계): `connected` = 배정이 있는 샤드가 전부 연결(그런 샤드가 없으면 false), `last_message_at` = 샤드 중 최신, `last_error` = 마지막 판정에 쓴 샤드의 것(성공이면 null), `subscribed` = 열린 소켓에 실제 구독된 심볼 수의 합, `connected_since` = 연결된 샤드 중 가장 이른 구독 시각, `url` = WS URL. 시세 프레임마다 갱신되므로 003 의 `age` 는 바이낸스 행에서도 성립한다.

### 3.6 장애 격리
- 한 번도 못 붙어도 앱은 뜬다. 연결 실패 1회 = 그 샤드의 경고 로그 1줄 + `last_error`, 백오프 재시도. 바이낸스 행은 비어 003 이 404 또는 행 없음으로 동작한다. 샤드 하나가 죽어도 다른 샤드의 행은 계속 갱신된다.
- 종료: 샤드 태스크·재조정 태스크 취소 후 소켓 3개를 **동시에** 닫고 합계 2초 상한(상대가 close 프레임을 안 주면 하나당 20초를 기다려 `docker stop` 10초를 넘긴다). 남은 TCP 는 OS 가 정리한다.

## 4. 검증
네트워크 없음 — 가짜 소켓(메시지 주입 async 제너레이터)·가짜 REST·가짜 원문 싱크.
- depth20 메시지 → 행의 `asks`/`bids` 가 float 20단계, `asks` 오름차순·`bids` 내림차순, `updated_at` 갱신. 누적 1,000,000 USDT 에서 잘리고 첫 단계가 넘어도 1단계는 남는다.
- miniTicker 메시지 → `price`·`price_timestamp`. depth 전에 오면 보류, depth 뒤에 실린다. 체결가 없으면 mid.
- `exchangeInfo` 에서 `TRADING`·`USDT` 만 심볼 집합에 든다. 우주 밖 심볼 메시지는 버려진다.
- 심볼 300개 → 3 샤드에 분산, 같은 심볼은 항상 같은 샤드(서브프로세스 2개로 해시 안정성 확인), 두 스트림이 같은 샤드. 추가·삭제가 나머지 배정을 안 바꾼다.
- SUBSCRIBE 한 메시지 ≤ 100 스트림, 초당 ≤ 4 메시지. 재조정: 새 심볼 구독·빠진 심볼 해지·행 삭제. 재조정을 보내는 도중 `!serverShutdown` 으로 재연결되면 새 소켓에 배정 전체를 구독하고 `subscribed` 는 새 소켓 기준이다.
- `connected_since` 는 첫 SUBSCRIBE 묶음을 다 보낸 시각이고 정체 30초는 거기서부터 센다(보내는 동안은 소켓이 열린 시각).
- 정체: 샤드 2만 30초 무수신(0·1 은 수신) → 그 틱이 `stale_stream` 실패이고 message 에 "샤드 2". 30초 미만은 성공. 구독 0 샤드는 무시. 메시지가 오면 다음 틱 성공.
- 미연결 샤드 → 그 샤드 `last_error.kind` 로 실패. 셋 중 둘이 나쁘면 더 오래 조용한 쪽.
- 모든 프레임(시세·구독 응답·serverShutdown)과 exchangeInfo 본문이 원문 싱크에 원문 그대로 기록된다.
- `!serverShutdown` 수신 → 재연결. 연결 실패 백오프 1·2·4…30, 구독 성공 후 1.
- 연결 실패 기동(lifespan 을 실제로 돌리되 소켓·REST 는 가짜) → `/health` 200, 앱 정상, 샤드마다 경고 1줄. 샤드 1개 실패 시 나머지 2샤드 행은 계속 갱신된다.
- 앱 종료 시 태스크 취소·소켓 close, 잔여 예외 없음.
- 수동(실서버): 기동 10초 뒤(우주 확정 → 샤드 3개 구독 → 첫 depth20) 로그에 샤드 연결 실패 경고가 없고 `/health/collect` 의 바이낸스가 `ok`, `/spreads` 바이낸스 행의 호가가 `curl -s "localhost:8000/orderbook/binance?symbol=BTC/USDT&depth=20"` 에서 20단계. 네트워크를 끊으면 30초 뒤 `/health/collect` 의 바이낸스가 `stale_stream`, 복구하면 닫힌다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q
# All checks passed! / 172 files left unchanged / 339 passed, 1 warning in 2.15s
# (이 스펙의 tests/test_stream_binance.py 42개 포함 — 6회 반복 실행 모두 42 passed)

# 실서버 스모크 — 2026-09-05 로컬(이 시각 api.upbit.com·api.bithumb.com·api.binance.com 이 200 으로 열려 있어 로컬에서 돌렸다), 빈 포트 8041, 끝나고 kill
.venv/bin/uvicorn app.main:app --port 8041
curl -s localhost:8041/health                                         # {"status":"ok","version":"0.1.0"}
curl -s localhost:8041/health/collect                                 # 기동 15초 뒤: upbit ok 198 · bithumb ok 292 · binance ok 273, outages []
curl -s "localhost:8041/orderbook/binance?symbol=BTC/USDT&depth=20"   # asks 20 · bids 20 (기동 2초 뒤에 이미 20단계)
curl -s localhost:8041/spreads                                        # rows 461 · warnings [] · notional 10000 · rate > 1000 · 행 17키
curl -s "localhost:8041/spreads?notional=500000"                      # BTC slipFwd 0.0056 → 0.0237 (규모가 커지면 슬리피지 증가)
# SIGTERM → 0.1초 뒤 포트 해제. 로그에 "바이낸스 샤드 N" 연결 실패 경고·트레이스백 없음
# 재검증(구독 집합 소켓 결속·구독 시각 수정 뒤, 이 Mac 망이 거래소를 막은 상태): 8041 기동 6초 뒤 /health 200, /health/collect 200(3거래소 down — 목록 REST 가 ConnectTimeout), 트레이스백 없음, kill 뒤 포트 해제
```
EC2 에서 확인 필요(로컬에서 재현 불가): 네트워크를 끊고 30초 뒤 `/health/collect` 의 바이낸스가 `stale_stream`(message 에 샤드 번호)이고 복구하면 닫히는지, 24시간 강제 종료 뒤 샤드가 각자 재연결하는지, 우주 ≈300 종목의 실제 대역폭.

## 6. 갱신할 문서
- `docs/context/status.md` — binance-stream 행 `| binance-stream | WS 3샤드 depth20+miniTicker·exchangeInfo 10분·샤드 단위 정체 판정 | - | 해외 최대 20단계 |`. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 012 행 → DONE. **항상 포함.**
- `docs/context/architecture.md` — "현재 구조" 의 binance-stream 항목(모듈·샤드·판정).
- `docs/context/dev-setup.md` — 스모크에 `/orderbook/binance … depth=20` 확인 1줄.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `server/app/core/streams/binance.py`(커넥터 — `BinanceStream`·`shard_of`), `server/tests/test_stream_binance.py`(42 테스트, 001 의 `tests/stream_fakes.py` 재사용 + 제어 메시지 간격을 표로 막는 `TicketSleeps`). 바꾼 것: `server/app/core/contracts.py`(`ForeignSymbolSource.set_universe` + `NoForeignSymbols`), `server/app/core/universe.py`(우주 확정 시 `set_universe` 호출), `server/tests/test_universe.py`(FakeForeign 에 `set_universe`), `server/app/main.py`(커넥터를 우주 `foreign`·스트림·틱 루프·`/refresh` 에 배선), `docs/context/status.md`·`architecture.md`·`dev-setup.md`, `CLAUDE.md` 인덱스, 이 스펙.
- 추측한 지점 (묻지 않고 정한 것 — 전부 §3 에 확정 문구로 적었다):
  1. 모듈 경로는 architecture.md·001 과 같은 `core/streams/binance.py`(§2).
  2. 우주 → 커넥터 전달. 선택지: (a) 커넥터가 `QuoteSink.universe` 를 60초마다 읽는다 — `/refresh` 즉시 반영이 안 된다, (b) `UniverseRefresher` 에 콜백 인자 — 심볼 집합과 구독 대상이 두 계약으로 갈린다, (c) `ForeignSymbolSource` 에 `set_universe(bases)` 추가하고 우주가 확정될 때마다 부른다 — 채택(§2·§3.3). 커넥터 하나가 `refresh`·`bases`·`set_universe` 를 전부 구현한다.
  3. 재조정 = `set_universe` 가 깨우거나 60초마다, 연결된 샤드의 차이만 전송. 빠진 심볼의 행 삭제는 `set_universe` 에서 동기로(§3.3). 배정 0 인 샤드는 폴링 대신 이벤트 대기(§3.3).
  4. 제어 메시지마다 0.25초 대기(초당 4개), `id` 는 샤드별 1부터, ack 는 시세 아님, `error` 키 응답은 `bad_request`(§3.3).
  5. 백오프 리셋의 증거 = 구독 뒤 첫 시세 프레임(001 과 동일). `!serverShutdown` 은 백오프 없이 즉시 재연결하되 백오프 값은 유지(§3.3).
  6. 클라이언트 keepalive ping 20초(라이브러리) — 조용히 죽은 TCP 감지용(§3.3).
  7. 핸드셰이크 HTTP 거부·exchangeInfo 비-200 은 011 의 바이낸스 규칙(403 `banned`)(§3.3).
  8. depth20 에 거래소 시각이 없어 체결가 없을 때 `price_timestamp` = 수신 시각. 맵에 없는 심볼 프레임은 버린다(§3.4). base 하나에 USDT 심볼이 둘이면 처음 것(§3.3).
  9. 판정 집계: 판정 대상 = 배정 있는 샤드, 실패 우선 → 전부 판정 없음이면 없음 → 그 밖 성공. 조용한 시간 = 지금 − max(마지막 시세, 연결 중이면 구독 시각), 한 번도 못 받은 미연결 샤드가 가장 조용하다(§3.5). 집계 `StreamState`: `connected` = 배정 있는 샤드 전부 연결, `last_error` 는 성공 판정 시 null, `subscribed` = 실제 구독 합, `connected_since` = 가장 이른 값(§3.5).
  10. §4 "서브프로세스 2개" 는 `PYTHONHASHSEED` 를 달리한 2회 실행. "연결 실패 기동 → /health 200" 은 lifespan 을 실제로 돌리는 테스트 — 커넥터 3종의 `open_socket` 을 거부하는 가짜로, `httpx.AsyncClient` 를 마켓 목록·exchangeInfo 만 200 을 주는 가짜 전송으로, 설정을 `.env` 없는 `Settings` 로 바꿔 `TestClient(app)` 안에서 `/health` 200 과 BTC 샤드의 경고 1줄을 본다. 집계 `last_error` 는 틱의 `judge` 가 채우므로 그 테스트는 단언하지 않는다(커넥터 단위 테스트가 본다).
  11. 구독 집합은 소켓에 묶는다(§3.3). 선택지: (a) `_run_shard` 가 소켓을 바꾸기 전에 샤드 잠금을 잡아 진행 중인 재조정이 끝나길 기다린다 — 재연결이 최대 0.75초 늦고 죽은 소켓의 결과가 잠깐 집계에 남는다, (b) 재조정이 보내기 전에 잡은 소켓과 보낸 뒤의 소켓이 다르면 결과를 버린다 — 채택. 새 소켓의 첫 `_sync_shard` 는 빈 집합과 비교해 배정 전체를 보낸다. 재조정 전송 실패(죽은 소켓)는 경고 1줄이고 펌프가 끊김을 재연결로 처리한다.
  12. `connected_since` 는 첫 SUBSCRIBE 묶음을 다 보낸 시각(§3.5, 001 §3.3 의 "구독 시각" 그대로). 보내는 동안(≤0.75초) 소켓이 열린 시각을 임시로 두는 이유는 판정 규칙이 001 과 같아야 해서다 — 그 값이 없으면 직전 연결의 수신 시각으로 정체가 되거나(재연결) 무한 조용함이 된다(첫 연결).
- 실행 중 함께 고친 스펙 절: §2(경로·바꾸는 기존 것), §3.3(계약·재조정·소켓에 묶인 구독 집합·간격·백오프·keepalive·분류), §3.4(시각·맵 밖), §3.5(샤드 판정·구독 시각의 정의·집계), §3.6(재조정 태스크 포함 종료), §4(재조정 도중 재연결·구독 시각·lifespan 기동).
- 남은 빚:
  - EC2 확인 항목(§5): 네트워크 차단 → `stale_stream` → 복구, 24시간 강제 종료 재연결, 실제 대역폭.
  - 로컬 스모크에서 업비트 입출금 API 가 401 — 키·허용 IP 문제(006 소관), 이 스펙과 무관.
