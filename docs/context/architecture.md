# architecture.md — 구조

> 이 문서는 현재의 핵심 설계 결정과 구현 경계를 쓴다. 기능별 구현 상태는 `status.md` 가 말한다.

## 핵심 설계 결정
- **실시간 시세의 기준은 `live_store`(LiveStore)다.** 실시간 조회 API 는 메모리만 읽고, `/history/*` 만 InfluxDB 를 조회한다. 조회 경로에 Redis·S3 호출은 없다.
- **거래소 시세는 WebSocket 상시 연결로만 받는다.** 업비트·빗썸은 호가·현재가 스트림(001), 바이낸스는 depth20·miniTicker 스트림(012). REST 는 마켓 목록·심볼 목록(10분)과 입출금 상태(60초)에만 쓴다. 세 거래소 모두 SSE 는 제공하지 않는다 — 선택지는 WebSocket 뿐이다.
- **저장은 세 계층을 순서대로 흐른다(009).** LiveStore 는 최신 시세와 **최신 틱 1장**을 들고, 새 틱이 만들어지는 순간 직전 틱이 Redis 로 인계된다. Redis 는 Influx 로 아직 옮기지 못한 틱만 들고(원문이 아니라 Influx 가 저장할 모양 그대로), 60초마다 전량이 Influx 로 옮겨진 뒤 비워진다.
- **원문은 S3 에 남긴다(010).** 거래소가 준 WebSocket 프레임·REST 응답 본문을 가공하지 않은 텍스트 그대로 원문 싱크에 넘기고, 거래소별로 **분마다 객체 1개**를 S3 `raw/` 에 쌓는다. 시세 프레임은 심볼·종류마다 그 분의 마지막 1건만 남기고(용량 — 하루 0.3~0.5GB), 마켓 목록·입출금·핸드셰이크 거부 본문 같은 비시세 응답은 전량 남긴다. 쓰는 필드가 바뀌어도 재수집 없이 분 단위로 재생하기 위한 저장소다.
- **시세 커넥터는 공통 인터페이스를 구현하고 코드를 공유하지 않는다.** 거래소별 메시지 형식·quirk 는 각 커넥터 안에서만 흡수한다. 새 거래소 추가는 커넥터 하나 추가다.
- **도메인 계산은 가능한 한 순수 함수로 작성한다.** 네트워크·DB 같은 I/O 의존성은 인자로 주입하고 변경 가능한 전역 상태를 사용하지 않는다. 틱 생성과 표 계산은 `await` 없이 끝난다 — 한 계산 안에서 교체 전후 호가가 섞이지 않는 유일한 근거다.
- **입출금 상태는 `open(true) / closed(false) / unknown(null)` 세 상태를 구분한다.** `unknown` 을 `open` 으로 처리하지 않는다.
- **현재 서버는 uvicorn worker 1개만 사용한다.** `live_store` 가 프로세스 내부 메모리이기 때문에 다중 worker 를 쓰려면 프로세스들이 공유하는 외부 저장소로 먼저 이전해야 한다.

## 런타임 구성
- **server/**: Python 3.12, FastAPI, httpx, websockets, redis(asyncio), influxdb-client, boto3, pyjwt, pydantic v2, pydantic-settings. 로컬 포트 8000. 상시 태스크: 업비트·빗썸 스트림 각 1, 바이낸스 샤드 3 + 재조정 루프(60초), 마켓 우주 갱신 루프(10분, 못 받은 거래소는 5초 재시도), 틱 루프(1초), Redis 인계 큐, flusher(60초), 원문 닫기 회차(1초, 업로드는 데몬 워커 스레드 1개), 입출금 조회(60초), `collect_fail` 쓰기 큐 태스크.
- **web/**: React 19, TypeScript, Vite. 런타임 의존성은 react·react-dom 뿐이다. 로컬 포트는 5173 이고, 배포 컨테이너의 nginx 는 80번 포트를 사용한다. 호스트 포트는 `WEB_PORT` 로 정한다.
- **저장소**: InfluxDB 2.7 OSS(org·bucket `marketlens`, Flux) — 김프 이력. Redis 7 — 틱 버퍼(AOF, Influx 로 옮기기 전까지만). S3(`marketlens-spreads-snapshot`, ap-northeast-2, 접두사 `raw/`) — 거래소 원문 아카이브. 모델은 `db.md`. 테스트에서는 셋 다 띄우지 않는다(fake·fakeredis). S3 자격증명은 SDK 기본 탐색(로컬 `~/.aws`, EC2 IAM 역할).

## 데이터 흐름 (BE)
```
[업비트 WS] [빗썸 WS] [바이낸스 WS 3샤드]   [REST: 마켓 목록 10분 · 입출금 60초]
      │           │            │                          │
      └───── 모든 프레임·응답 본문 → 원문 싱크 ──▶ S3 raw/ (거래소별 분당 객체 1개 · 시세는 분당 마지막 1건)  ─ 010
      │           │            │
      ▼           ▼            ▼   메시지마다 (exchange, base) 행 교체 · KRW-USDT → USDT 시세
                LiveStore (최신 시세 · 스트림 상태 · 틱 슬롯 · spark)
                    │                         ▲
   실시간 조회 API ◀─┘ 읽기                    │ 매초: 틱 T 생성 → 슬롯에 넣고 T−1 을 인계
                                              ▼
                                   Redis Stream `ticks` (아직 안 옮긴 틱만)          ─ 009
                                              │ 60초마다 전량 → Influx → 성공 시 비움
                                              ▼
                                   InfluxDB `premium`·`dw_fail`  ◀── /history/* 읽기  ─ 005
매 틱 거래소별 판정(연결·30초 무수신) → 실패 이력(메모리, 구간 단위) → Influx collect_fail(열림/닫힘 시)·기동 시 복원  ─ 011
```
- 행은 거래소 단위 통째 교체가 아니라 **메시지 단위**로 바뀐다. 상장·상폐는 10분마다 갱신되는 **마켓 우주**(국내 KRW ∩ 바이낸스 USDT)가 반영한다 — 우주 밖 행은 없다.
- 스트림이 끊기면 행은 남고 그 거래소의 `last_message_at` 이 멈춘다. `/spreads` 의 `age`·`status` 는 행이 아니라 **거래소 스트림의 마지막 수신 시각** 기준이다(조용한 코인의 호가는 안 바뀌어도 현재값이다).
- 입출금 상태 API 는 틱 루프가 60초 주기로만 조회해 캐시하고, 행이 새 메시지로 교체돼도 그 3필드는 물려받는다. 키가 없으면 `null`(모름). 망 판정은 `/spreads` 에서 하고, 빗썸은 키가 필요 없다.
- `GET /health` 와 틱 루프는 기능 폴더가 아니라 앱 진입점 소관이다. `/health` 는 프로세스 liveness 만 나타낸다. 상세는 `/health/collect`(011).
- USDT 시세는 별도 호출 없이 국내 거래소의 `KRW-USDT` 호가 메시지에서 추출한다. 최우선 매도호가가 rate_ask(USDT 살 때), 최우선 매수호가가 rate_bid. 거래소별 ask/bid. 은행 환율은 어디에도 쓰지 않는다(product.md 용어).
- `POST /refresh` 는 시세를 묻지 않는다 — 마켓 우주 갱신·재구독·입출금 재조회를 즉시 시키는 진단용 트리거다.
- 재기동 시 시세는 스트림 스냅샷으로 수 초 안에 복구되고, 이력 24시간(011)과 spark 30분(009)은 Influx 에서 되읽는다. Redis 에 남아 있던 틱은 다음 flusher 회차에 옮겨진다.

## 데이터 흐름 (FE)
- `fetch` 폴링만 사용. 상태관리·라우터·스타일 라이브러리 없음.
- API base: `VITE_API_BASE` (미설정 시 `/api`). dev 는 vite proxy `/api → http://localhost:8000` (prefix strip), 배포는 nginx `/api/ → server:8000/`.
- 폴링 실패 시 직전 데이터 유지.
- 셸의 공유 피드가 탭 공통 데이터를 들고, 1.5초 tick 은 셸이 돌린다. `/spreads` 1초 폴링은 spreads 기능(003)이, `/health/collect` 5초 폴링은 health 기능(011)이 제공한다.

## 계약 규칙 (BE ↔ FE)
- BE 내부는 snake_case 를 사용한다. HTTP JSON 키와 복합어 쿼리 파라미터는 모든 엔드포인트에서 camelCase 를 사용한다. 정확한 스키마는 각 기능의 모델과 타입이 정의한다.
- `/spreads` 최상위 `warnings: list[str]` — USDT 시세 60초 미갱신 경고. 없으면 빈 배열(키는 항상 존재). (스펙 008)
- 응답 압축: GZip 미들웨어를 앱 전역에 켠다 — `/history/streaks/bulk` 같은 수 MB JSON 때문. 설정은 001 의 앱 골격 소관.
- FE 가 소비하는 응답 스키마를 변경할 때는 같은 변경에서 BE 모델과 FE 타입을 함께 수정한다.
- 비즈니스 에러는 `{"error": {"code": str, "message": str, "detail": any}}` 형식이다. 인증 실패와 FastAPI 요청 검증 실패(422)는 `{"detail": ...}` 형식을 사용한다.

## core 가 제공하는 계약 (기능·스펙이 공유하는 Protocol)
- 원문 싱크 `record(exchange, source, received_at_ms, payload)` — 동기·무예외(010 구현, 수신 경로가 호출).
- 틱 인계 `handoff(tick)` — 동기·무예외(009 구현, 틱 루프가 호출).
- 판정 결과 전달 — 틱 루프가 이력 추적기에 거래소별 성공/실패를 넘긴다(011 구현).
- 입출금 조회기 `refresh_if_due(client, force=False)` / `apply` / `failed` / `warnings` / `availability`(006 구현, 틱 루프가 호출 — `force` 는 `/refresh` 트리거가 쓴다).
- 바이낸스 USDT 현물 심볼 집합 `refresh(client) -> int` / `bases() -> set[str]` / `set_universe(bases)`(012 의 커넥터가 구현, 마켓 우주가 호출 — 우주가 확정될 때마다 `set_universe` 로 구독 대상을 넘긴다). 커넥터를 꽂지 않는 테스트에는 빈 집합을 주는 기본 구현.
- 스트림 판정 `judge(now_ms) -> Verdict | None`(거래소 스트림마다 — 국내는 core 공통 규칙, 바이낸스는 012 샤드 규칙. None = 아직 판정 대상 아님).
core 는 features 를 import 하지 않는다 — 구조적 타입(Protocol)으로만 알고 배선은 `main.py` lifespan 이 한다.

## 기능 폴더 기본 구조
기능에 필요한 파일만 둔다. BE 전용 기능은 web 폴더가 없고, API 를 호출하지 않는 mock 화면은 `api.ts`·`types.ts` 가 없어도 된다.

```
server/app/features/<name>/
  router.py     HTTP API가 있으면 APIRouter를 선언한다.
  service.py    기능 흐름을 조립한다. 계산은 가능한 한 순수 함수로 두고 I/O 의존성은 인자로 받는다.
  models.py     요청·응답 모델이 있으면 둔다.
  tests/        기능 테스트. 외부 네트워크는 fake로 대체한다.
web/src/features/<name>/
  Tab.tsx       해당 기능의 화면.
  api.ts        실제 API를 호출할 때 fetch 함수와 표시 전용 가공을 둔다.
  types.ts      실제 API 응답을 사용할 때 BE 응답과 맞는 타입을 둔다.
```

## 배포 토폴로지
EC2 1대. 루트 `docker compose up -d --build` 로 server·web·influxdb·redis 컨테이너를 실행한다. 컨테이너는 compose 기본 네트워크를 사용하며 InfluxDB·Redis 포트는 호스트에 공개하지 않는다. PR CI 는 server lint·format·pytest 와 web lint·build 를 실행한다. main push 는 EC2 에 SSH 로 접속해 배포한다. 상세는 스펙 007(deploy).
같은 EC2 에 기존 marketlens-be(:8000)·fe(:80) 가 운영 중이라 이 레포의 web 은 `WEB_PORT=8080` 으로 공존한다. 80 이관·서버 분리(DB/파싱 분리)는 추후 별도 스펙으로 검토한다.
배포 workflow 의 성공은 EC2 명령 실행 성공만 뜻한다. 외부 URL 확인과 실패 시 자동 롤백은 아직 없다.

## 현재 구조 (개발 후 갱신 — 실행 세션이 §7 보고와 함께 채운다)
스펙이 DONE 될 때마다 주요 모듈과 역할을 짧게 기록한다. 문서와 코드가 다르면 사람이 올바른 쪽을 결정하고 같은 변경에서 둘을 맞춘다.
- **collect (001)**: `core/models.py`(`Row`·`Rate`·`StreamState`·`StreamError`·`Tick`·`TickRow`), `core/live_store.py`(행 단위 쓰기 `put_row`·`remove_row`·`retain_bases`, 입출금 3필드 물려받기, 스트림 상태 `stream`/`stream_state`, 틱 슬롯 `push_tick`, spark 맵), `core/rows.py`(`clean_levels` — 잔량 필터·누적 상한), `core/quotes.py`(`QuoteSink` — 메시지 → 행 규칙·우주 필터·체결가 보류·USDT 시세), `core/streams/upbit.py`·`core/streams/bithumb.py`(스트림 커넥터 2개 — 연결·구독·펌프·분류·백오프·재구독·`fetch_markets`·`judge`; 코드 공유 없음), `core/universe.py`(`UniverseRefresher` — 10분·못 받은 거래소만 5초 재시도(바이낸스 심볼 포함)·교집합·구독 목록 배포), `core/ticks.py`(`judge_state`·`build_tick`·`TickLoop`), `core/collect.py`(`CollectService.refresh_now` — `/refresh` 트리거·`RefreshSummary`), `core/contracts.py`(원문 싱크·틱 인계·판정·입출금·바이낸스 심볼 Protocol 과 무동작 기본 구현), `core/config.py`(`EXCHANGES`·타임아웃), `core/errors.py`(`ExchangeError`·`FAIL_KINDS` — REST 실패 예외와 실패 종류 8종), `core/serialization.py`(`camelize_json` — 모든 라우터의 HTTP 경계 camelCase). `main.py` lifespan: Influx·Redis 연결 확인 → 이력 복원 → spark 복원 → 우주 → 스트림 → 틱 루프 → 인계 보내기·flusher, 종료 시 마지막 틱 인계·큐 비우기. 테스트는 `server/tests/`(`stream_fakes.py` 의 가짜 소켓·연결기).
- **web-shell (002)**: `shared/`(테마·공유 피드·결정론 mock·포맷·UI 조각), `App.tsx`(헤더·KPI·탭 전환), `features/{gap,pp,flow}/Tab.tsx`(mock 탭). spreads 와 history 는 별도 기능 폴더가 담당한다.
- **spreads (003)**: server `core/premium.py`(`premium_percent`), `core/orderbook.py`(호가 걷기 — 004 와 공용, 전부 동기), `features/spreads/`(service 순수 계산·router 2 엔드포인트·models). 표 계산 함수는 저장소와 체결 규모(`notional`, 기본 $10,000)를 받아 행 17키를 만들고, 두 다리를 수량으로 연결해 걸어 슬리피지 차감 후 순값과 차감폭(`slipFwd`·`slipRev`)을 함께 싣는다. web `features/spreads/`(1초 폴링·응답 타입·화면). `age` 는 양측 거래소 스트림의 `last_message_at` 기준이고, `/refresh` 는 001 의 `RefreshSummary` 를 노출한다.
- **analysis (004)**: `core/orderbook.py`(호가창 소진 순수 계산 — `walk_amount`·`walk_quantity`·`average_price`·`slippage_percent`·`walk_levels`, 003 과 공용, 전부 동기), `features/analysis/` — `service.py`(6개 빌더·거래소 레지스트리·`AnalysisApiError`)·`router.py`(루트 경로 6개, 오류를 `{"error":…}` 로 변환)·`models.py`(응답 모델, snake_case → 라우터에서 camelCase). 모든 응답은 LiveStore 만 읽고 걷기는 행의 `asks`/`bids` 그대로다. web 없음. 테스트는 `features/analysis/tests/`(표준 시드 `helpers.py`).
- **history (005)**: `core/influx.py`(`InfluxClient` — influxdb-client 를 import 하는 유일한 곳, lazy 연결·`ping`·`write`·`premium` 조회 3종 + 009 `query_spark`·011 `collect_fail`, 점 생성 `premium_point`·`dw_fail_point`·`collect_fail_point` 와 line protocol 직렬화, 실패는 전부 `InfluxUnavailableError`), `features/history/`(`service.py` 순수 계산 — 주/월 경계·컴팩트 events·streak 구간·샘플 가중 요약·bulk 집계, 리더는 `query_premium` Protocol 로 주입 / `router.py` 3 엔드포인트 — 검증 422·업무 오류 400/404·저장소 503, 동기 클라이언트는 스레드에서 / `models.py`), `scripts/backfill.py`(순수 계산과 거래소 호출 분리, UTC 하루 단위·앞뒤 빈 구간만), 루트 `docker-compose.dev.yml`(Influx 2.7 + Redis 7). `main.py` 는 `INFLUX_TOKEN` 이 있을 때만 클라이언트를 만들어 `app.state.influx` 에 두고 ping 실패는 에러 1줄. web `features/history/Tab.tsx` 는 002 의 mock 사건을 그린다. Influx 쓰기는 009 의 flusher 가 한다. 테스트는 `features/history/tests/`(fake 리더)·`server/tests/test_backfill.py`(순수 계산).
- **wallet-status (006)**: `core/networks.py`(`Network` 모델, `normalize_name` 정규화·`match_network` 판정·`pick_domestic` tie-break·동일 체인 쌍 표 — spreads 와 공용), `features/wallet_status/`(`upbit.py` JWT HS256·`binance.py` HMAC-SHA256·`bithumb.py` public 조회기 3개 — 코드 공유 없음, 각각 응답을 받는 즉시 010 `record` 로 본문을 남긴다 / `service.py` `WalletStatusService` — `WalletStatusProvider` 계약 구현: 60초 캐시·3거래소 병렬 조회·실패 거래소 `unknown` 덮기·경고·`failed` / `models.py` `CoinStatus`·`WalletStatusError`). `main.py` 가 키 4개와 원문 기록 함수를 주입해 틱 루프(별도 태스크로 조회, `apply` 로 행에 반영)와 `/refresh` 트리거(`force`)에 꽂고, spreads 의 `_wallet_fields` 가 §3.7 규칙으로 행 5필드를 만든다. 테스트는 `features/wallet_status/tests/`(MockTransport·`FakeRecorder`)·`server/tests/test_networks.py`·`test_wallet_integration.py`.
- **deploy (007)**: `server/Dockerfile`(python 3.12 slim, `pip install .`, `COPY` 는 pyproject·app·scripts 만 — `.dockerignore` 가 `.env` 를 컨텍스트에서 뺀다, uvicorn 워커 1개), `web/Dockerfile`(node 22 빌드 → nginx 1.27 정적 서빙)·`web/nginx.conf`(`/api/` → `server:8000/` 접두 제거, SPA fallback, `index.html` no-store·`/assets/` immutable), 루트 `docker-compose.yml`(name `marketlens`, 컨테이너 4개 `marketlens-*`, web 만 `${WEB_PORT:-80}:80`, server 는 `env_file: server/.env` + `INFLUX_URL`·`REDIS_URL` 서비스명 덮어쓰기, named volume 2개), `.github/workflows/ci.yml`(PR → `server`·`web` job, 경로 필터 없음)·`deploy.yml`(main push → SSH → `server/.env`·`INFLUX_TOKEN`·`S3_BUCKET` 가드 → `git reset --hard origin/main` → `compose --env-file .env --env-file server/.env up -d --build` → `image prune`), PR 템플릿 3줄, README. 설정 계약은 `server/tests/test_deploy.py` 가 파일을 읽어 단언한다(Docker 없는 CI 에서 도는 회귀 장치).
- **tick-store (009)**: `core/redis_stream.py`(`RedisTickStream` — redis 를 import 하는 유일한 곳. `ticks` XADD/XRANGE 페이지/XDEL, 재시도 없음, 연결 2초·명령 5초), `core/tick_store.py`(`encode_tick`/`decode_tick` gzip JSON, `TickRelay` — `handoff` 구현: spark 갱신·게시 → 600 큐 → 보내기 태스크가 순서대로 XADD, 실패 틱은 버림, 종료 시 큐 비우기 총 5초 상한; `Flusher` — 60초마다 1,000건 페이지 단위로 읽기 → 스레드에서 5,000점 배치 쓰기 → 그 페이지만 XDEL → 다음 페이지, 연속 실패 수·잘림 감지(지우지 못한 첫 ID 기준, XDEL 실패는 제외)), `core/spark.py`(`SparkBuffer` 1분 버킷 last 30개 링버퍼, `restore_spark` 기동 복원 10초 상한), `core/influx.py` 의 `query_spark`(aggregateWindow 1m last). `INFLUX_TOKEN` 없으면 flusher 를 띄우지 않는다. 테스트는 `server/tests/test_tick_store.py`·`test_spark.py`·`test_tick_store_history.py`(fakeredis + `tests/conftest.py` 의 `FakeInflux`).
- **raw-archive (010)**: `core/s3.py`(`S3Uploader` — boto3 를 import 하는 유일한 곳, `put`·`head_bucket`, 로그 없음), `core/raw_archive.py`(`format_line`·`object_key`·`pack` 순수 함수, `RawArchive` — 001 의 `record(…, key)` 계약 구현: `(거래소, UTC 분 창)` 버퍼에 줄을 붙이고 `key` 있는 줄은 `(source, key)` 당 마지막 1건만, 매초 닫기 회차가 지난 창을 닫아 스레드에서 gzip → 거래소 합산 단일 FIFO 대기열 → 데몬 워커 스레드 1개가 순서대로 `PutObject`(실패는 머리에 두고 1초 뒤 재시도, 압축 후 256MB 초과 시 오래된 객체부터 버림), 종료 5초 상한). 수신 경로와 분리한 이유 — 기록 함수는 메모리 붙이기뿐이라 어떤 S3 장애도 스트림·틱 루프를 한 줄도 막지 않는다. `main.py` 가 `S3_BUCKET` 이 있을 때만 만들어 스트림 3개와 006 조회기에 `record` 를 주입하고, 없으면 `noop_record`. 테스트는 `server/tests/test_raw_archive.py`(fake S3·주입 시계).
- **health (011)**: `core/outages.py`(실패 구간 추적기 — 틱 루프가 쓰므로 core. 열림/닫힘 시 `collect_fail` 1점을 순서 보장 큐로 쓰고, 기동 시 24시간 복원), `features/health/`(읽기 API `/health/collect`), web `features/health/`(5초 폴링·탭). 응답 타입 `HealthData` 와 거래소 표시명 `exName` 은 `shared/` 에 있다.
- **binance-stream (012)**: `core/streams/binance.py`(`BinanceStream` 하나 — 샤드 3개 각각 소켓·시계·백오프·구독 집합, `shard_of` = crc32 % 3, 재조정 루프 1개(`set_universe` 가 깨우거나 60초), exchangeInfo 심볼 맵으로 `ForeignSymbolSource` 구현, `judge` 는 샤드별 판정 후 가장 조용한 샤드를 고른다). 001 의 `QuoteSink.orderbook/trade` 와 `store.stream("binance")`(샤드 집계) 를 쓰고 `StreamJudge` 로 틱 루프·`/refresh` 트리거에 꽂힌다. 테스트는 `server/tests/test_stream_binance.py`(001 의 `stream_fakes.py` 재사용).
