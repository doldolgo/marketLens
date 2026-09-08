# status.md — 현재 상태

> 실행 세션이 스펙을 끝낼 때마다 갱신한다. "무엇이 실제로 돌아가는가" 만 쓴다.
> 표기: `없음` = 만들어야 하는데 아직 없음. `-` = 그 런타임에 해당 기능이 원래 없음.

## 이 레포
**재구축 완료**: CLAUDE.md §4 의 001~014 가 전부 DONE 이라 아래 표는 모두 새 구조(WebSocket 수집·틱 인계·원문 아카이브)의 런타임을 말한다 — 토대(행·틱·계약)·3거래소 스트림·김프 표(`/spreads`·`/refresh`)·단일 종목 분석 6개 API·이력 조회(`/history/*` 3종·백필)·입출금 상태(3거래소 조회·망 판정)·틱 저장 3계층(Redis → Influx `premium`·`dw_fail`)·원문 아카이브(S3 `raw/`)·수집 실패 이력(`/health/collect`·Influx `collect_fail`)·김프/역프 사건(`premium_event`·`/history/events`·기록 탭)·1분 봉 계층(`candles_1m…1d`·`/history/candles`·기록 탭 차트)·배포(compose 4컨테이너·CI·EC2 배포 워크플로). 각 실행 세션이 끝날 때 그 행을 갱신한다.

| 기능 | server | web | 비고 |
|---|---|---|---|
| collect | 업비트·빗썸 WS 실시간 갱신·마켓 목록 REST 매초(우주)·1초 틱·/health | - | 바이낸스 스트림은 012 |
| web-shell | - | 셸·KPI·mock 탭 3종(gap·pp·flow) 동작, 탭·심볼·탭별 필터가 URL 쿼리로 복원 | spreads 탭은 003, history 탭은 005 실데이터 |
| spreads | `/spreads` 는 메모리(LiveStore)만 읽어 전 페어 표 — `notional` 규모로 호가를 걷어 슬리피지 차감, `age`·`status` 는 거래소 스트림 수신 시각 기준(행 자체가 300초 이상 안 바뀌면 그 행의 경과 초 → stale), USDT 시세 미갱신 경고, `spark` 는 009 가 게시한 30분 추이. `/refresh` 는 001 즉시 갱신 트리거 노출 | 실데이터 탭·1초 폴링·규모 세그먼트·행 클릭 → 기록 탭 | 행 17키. 스파크라인 렌더는 후속. 실거래소 확인(행 수 > 100 등)은 EC2 대기 |
| analysis | 6개 엔드포인트 동작 | - | HTTP 계약 camelCase |
| history | Influx 클라이언트(`core/influx.py`)·`/history/premium`·`/history/streaks`·`/history/streaks/bulk`(Influx 만 읽음 — 불달·토큰 없음이면 503, 메모리 조회는 영향 없음, `start` 없으면 최근 7일 창)·백필 스크립트(upbit×binance 캔들, 앞뒤 빈 구간만) — `premium`·`dw_fail` 쓰기는 009 flusher. 사건 감지기(`core/premium_events.py`, 틱마다 1.0% 진입·0.5% 이탈·1분 초과)·`premium_event`(열린 지 60초·60초마다·닫힐 때, 기동 시 7일 복원)·`/history/events`(닫힌 사건 + 진행 중, `start` 기본 7일 창). 1분 집계기(`core/candles.py`, 틱마다 조합별 OHLC·가격·입출금·막힌 초)·버킷 `candles_1m…1d`(7일/30일/90일/365일/무제한, 기동 시 생성)·사슬 롤업 회차(분 닫힘·60초, 5m→1h→4h→1d, 계층당 12창, 아래 계층 완료 지점까지만)·`/history/candles`(`res`, 요청당 1,440점 상한) | 기록 탭 = `/history/events`(김프/역프 서브탭 · 전 코인 사건 표(상위 30·헤더 정렬) · 선택 심볼 요약·타임라인·로그, 60초 재조회, 스프레드 행 클릭 피벗·초기 BTC). 탭 상단 차트 카드 = `/history/candles`(봉 → 계층 `candles.ts`, 쌍×청크 캐시 `useCandles`, 최신 청크 60초 재조회, 계층 안 접기 `rollup.ts`, lightweight-charts) — 심볼 검색·봉 종류 8종·국내 거래소 다중 선택·USDT 기준 가격·불러오는 중/오류/기록 없음 상태. **015 시안**: 해외 거래소 1개 = 카드 1개(세로로 쌓임, 시간축·십자선 연동 `ChartSync`), 입출금 띠는 거래소별(입금·출금 줄 2개, 경로 밖 줄은 흐림). 해외 선택지 Bybit·MEXC 는 **mock**(`mock.ts`, binance 실봉 변형) — 서버 수집은 binance 뿐, 봉 응답의 거래소별 4상태 키도 아직 없음(경로 2상태에서 채움) | 사건 클릭 → 구간 차트는 후속. `premium_event` 첫 점·재기동 복원·백필 실행·bulk 100코인 초과는 EC2 확인 대기 |
| wallet-status | 업비트(JWT)·빗썸(public)·바이낸스(HMAC) 조회를 틱 루프가 60초마다 병렬 실행·사이 틱은 캐시, 실패 회차는 그 거래소 전 행 `unknown`·`dwFailed`·`/refresh` 경고, 응답 본문은 010 원문 싱크로, `/spreads` 는 국내 망 기준 판정으로 5필드 | - | 표시는 spreads 탭이 담당. 실키 3-true·`netDom` 채움은 EC2 확인 대기(업비트는 허용 IP 필요) |
| deploy | Dockerfile·compose 4컨테이너(server·web·influxdb·redis, 호스트 노출은 web 하나)·CI(server·web 무필터)·deploy 워크플로(env 가드 → 미러 동기화 → `up -d --build` → prune)·설정 계약 테스트 `tests/test_deploy.py` | nginx 서빙(:${WEB_PORT}, `/api/` 접두 제거·SPA fallback·index no-store·assets immutable) | 로컬 4컨테이너 검증 완료(2026-09-06). EC2 공존·PR check·자동 배포·행이 있는 상태의 Redis 격리·Influx 첫 점은 GitHub 권한·EC2 대기 |
| tick-store | 틱 인계 큐(600, 종료 시 비우기 5초 상한) → Redis Stream `ticks` → 60초 flusher(1,000건 페이지 단위로 쓰고 지움) → Influx `premium`·`dw_fail`(멱등), spark 30분 링버퍼·기동 복원 | - | Redis·Influx 불달이어도 앱은 뜬다. 실서버(EC2) 수동 확인은 대기 |
| raw-archive | 거래소 원문 S3 적재 — 시세 프레임은 심볼·종류별, 매초 마켓 목록 응답은 거래소별 분당 마지막 1건, 그 외 전량(거래소·분마다 객체 1개 `…HHMM00Z.jsonl.gz`), 매초 닫기 회차 + 업로드 워커(실패 재시도·256MB 상한) | - | 읽기 API·재생 도구 없음, lifecycle 은 사람 몫. `S3_BUCKET` 없으면 비활성. EC2 에서 객체 적재·1분 객체 크기 실측 대기 |
| health | /health/collect·틱 판정(연결·30초 무수신·샤드) → 실패 구간 추적·collect_fail 쓰기/복원 | 실데이터 탭·5초 폴링·KPI 수집 상태 | 백오프는 후속 스펙. EC2 에서 차단·재기동 복원 수동 확인 대기 |
| binance-stream | WS 3샤드 depth20+miniTicker·exchangeInfo 매초(슬림 질의)·샤드 단위 정체 판정 | - | 해외 최대 20단계 |

## 알려진 빚
- (001) `server/build/`(setuptools 산출물 76파일)와 `server/marketlens_server.egg-info/` 가 git 에 추적돼 있다 — `ruff check .` 가 이 사본(76파일)도 검사한다. 별도 chore 로 지울 것. venv 의 패키지는 editable 설치만 허용한다(dev-setup.md) — 비-editable 사본이 남아 있으면 `server/` 밖 cwd 에서 옛 모듈을 import 한다.
- (010) 원문은 분당 마지막 1건 표본화로 하루 0.3~0.5GB(gzip 후) **추정** — EC2 에서 1분 객체 크기를 실측한 뒤 버킷 lifecycle 을 정한다.
- (006) 망 동일 체인 쌍 표는 `{metal,l2}` ↔ `{metal,dao,l2}` 1쌍뿐이다 — 실서버에서 `unknown` 으로 남는 국내 망을 보며 표를 늘린다(규칙을 느슨하게 풀지 않는다).
- (003·005) `/spreads` 의 `fwd`·`rev` 는 슬리피지 차감 후 순값이고 Influx `premium` 은 차감 전 원값이다. 저장 시점에 체결 규모가 정의되지 않기 때문이며, 그 대가로 `/history/streaks?threshold=` 는 화면 값보다 큰 값을 기준으로 구간을 센다. 백필(캔들 기반)도 원값만 만들 수 있어 아카이브 동질성 쪽을 택했다.
- (013) 과거 `premium` 의 사건 일괄 생성 미완 — `premium_event` 는 배포 시점부터만 쌓인다. 과거분은 `premium` 원본을 코인별로 나눠 도는 별도 스펙.
- (013) 사건 점에 입출금 상태 이력이 없다 — 사건 중 이동 가능 여부·막힌 시각은 후속(013 §7 남은 빚에 설계 메모).
- (014) `candles_*` 은 배포 시점부터 — 과거분 없음(초 단위 `premium` 엔 가격이 없다). 초 단위 `premium` 의 보존은 여전히 무제한(별도 결정). 위 계층 구멍을 사후에 메우는 도구는 없다(1m 7일·5m 30일 안이면 재료는 있다 — 후속 스펙 후보). web 의 청크 캐시(`useCandles`)는 상한이 없다 — 심볼을 많이 훑으면 탭이 열린 동안 메모리가 늘어난다(청크 1개 ≤ 1,440봉).
- (005) Influx `premium` 에는 초 단위 백필(BTC)에 더해 구 스택 PostgreSQL 에서 옮겨 온 2026-05-15~08-29 기록 2,759만 점이 함께 있다(옛 하나은행 환율 기준 값은 업비트 `KRW-USDT` 분봉 기준으로 다시 계산해 넣었다). 이 크기 위에서 **전 구간** `/history/streaks` 는 EC2(4GB)의 Influx 를 재시작시킨다(60초+ 후 504, 2026-08-30·09-07 실측). 그래서 `start` 없는 streaks·bulk 는 최근 7일 창이 기본이고(2026-09-07) FE 도 항상 `start`·`end` 를 붙인다(7일 BTC ≈ 2초). `start` 를 옛날로 주면 여전히 전 구간이 돌 수 있다 — 상한은 두지 않았다. 후속 스펙 후보: 오래된 데이터 1m 롤업(3달 기간 옵션 복원 조건). nginx read timeout(60초)도 함께 볼 것.
