# status.md — 현재 상태

> 실행 세션이 스펙을 끝낼 때마다 갱신한다. "무엇이 실제로 돌아가는가" 만 쓴다.
> 표기: `없음` = 만들어야 하는데 아직 없음. `-` = 그 런타임에 해당 기능이 원래 없음.

## 이 레포
**재구축 완료**: CLAUDE.md §4 의 001~012 가 전부 DONE 이라 아래 표는 모두 새 구조(WebSocket 수집·틱 인계·원문 아카이브)의 런타임을 말한다 — 토대(행·틱·계약)·3거래소 스트림·김프 표(`/spreads`·`/refresh`)·단일 종목 분석 6개 API·이력 조회(`/history/*` 3종·백필)·입출금 상태(3거래소 조회·망 판정)·틱 저장 3계층(Redis → Influx `premium`·`dw_fail`)·원문 아카이브(S3 `raw/`)·수집 실패 이력(`/health/collect`·Influx `collect_fail`)·배포(compose 4컨테이너·CI·EC2 배포 워크플로). 각 실행 세션이 끝날 때 그 행을 갱신한다.

| 기능 | server | web | 비고 |
|---|---|---|---|
| collect | 업비트·빗썸 WS 실시간 갱신·마켓 우주 10분·1초 틱·/health | - | 바이낸스 스트림은 012 |
| web-shell | - | 셸·KPI·mock 탭 3종(gap·pp·flow) 동작 | spreads 탭은 003 실데이터, history 탭은 005 의 mock 탭(실데이터 연결은 후속) |
| spreads | `/spreads` 는 메모리(LiveStore)만 읽어 전 페어 표 — `notional` 규모로 호가를 걷어 슬리피지 차감, `age`·`status` 는 거래소 스트림 수신 시각 기준, USDT 시세 미갱신 경고, `spark` 는 009 가 게시한 30분 추이. `/refresh` 는 001 즉시 갱신 트리거 노출 | 실데이터 탭·1초 폴링·규모 세그먼트·행 클릭 → 기록 탭 | 행 17키. 스파크라인 렌더는 후속. 실거래소 확인(행 수 > 100 등)은 EC2 대기 |
| analysis | 6개 엔드포인트 동작 | - | HTTP 계약 camelCase |
| history | Influx 클라이언트(`core/influx.py`)·`/history/premium`·`/history/streaks`·`/history/streaks/bulk`(Influx 만 읽음 — 불달·토큰 없음이면 503, 메모리 조회는 영향 없음)·백필 스크립트(upbit×binance 캔들, 앞뒤 빈 구간만) — `premium`·`dw_fail` 쓰기는 009 flusher | 기록 탭(002 mock 사건, 스프레드 행 클릭 피벗·초기 BTC) | `/history/*` 실데이터 연결은 후속. dev compose 위 첫 점·백필 실행·bulk 100코인 초과는 EC2 확인 대기 |
| wallet-status | 업비트(JWT)·빗썸(public)·바이낸스(HMAC) 조회를 틱 루프가 60초마다 병렬 실행·사이 틱은 캐시, 실패 회차는 그 거래소 전 행 `unknown`·`dwFailed`·`/refresh` 경고, 응답 본문은 010 원문 싱크로, `/spreads` 는 국내 망 기준 판정으로 5필드 | - | 표시는 spreads 탭이 담당. 실키 3-true·`netDom` 채움은 EC2 확인 대기(업비트는 허용 IP 필요) |
| deploy | Dockerfile·compose 4컨테이너(server·web·influxdb·redis, 호스트 노출은 web 하나)·CI(server·web 무필터)·deploy 워크플로(env 가드 → 미러 동기화 → `up -d --build` → prune)·설정 계약 테스트 `tests/test_deploy.py` | nginx 서빙(:${WEB_PORT}, `/api/` 접두 제거·SPA fallback·index no-store·assets immutable) | 로컬 4컨테이너 검증 완료(2026-09-06). EC2 공존·PR check·자동 배포·행이 있는 상태의 Redis 격리·Influx 첫 점은 GitHub 권한·EC2 대기 |
| tick-store | 틱 인계 큐(600, 종료 시 비우기 5초 상한) → Redis Stream `ticks` → 60초 flusher(1,000건 페이지 단위로 쓰고 지움) → Influx `premium`·`dw_fail`(멱등), spark 30분 링버퍼·기동 복원 | - | Redis·Influx 불달이어도 앱은 뜬다. 실서버(EC2) 수동 확인은 대기 |
| raw-archive | 거래소 원문 전량 S3 적재(거래소별 60초·32MB 객체) — WS 프레임·마켓 목록·입출금 REST 응답 | - | 읽기 API·재생 도구 없음, lifecycle 은 사람 몫. `S3_BUCKET` 없으면 비활성. EC2 적재 확인은 대기 |
| health | /health/collect·틱 판정(연결·30초 무수신·샤드) → 실패 구간 추적·collect_fail 쓰기/복원 | 실데이터 탭·5초 폴링·KPI 수집 상태 | 백오프는 013. EC2 에서 차단·재기동 복원 수동 확인 대기 |
| binance-stream | WS 3샤드 depth20+miniTicker·exchangeInfo 10분·샤드 단위 정체 판정 | - | 해외 최대 20단계 |

## 알려진 빚
- (001) `server/build/`(setuptools 산출물 76파일)와 `server/marketlens_server.egg-info/` 가 git 에 추적돼 있다 — `ruff check .` 가 이 사본(76파일)도 검사한다. 별도 chore 로 지울 것. venv 의 패키지는 editable 설치만 허용한다(dev-setup.md) — 비-editable 사본이 남아 있으면 `server/` 밖 cwd 에서 옛 모듈을 import 한다.
- (010) 원문 유입은 하루 10~20GB(gzip 후) **추정** — EC2 에서 1분 객체 크기·초당 줄 수를 실측한 뒤 버킷 lifecycle 을 정한다.
- (006) 망 동일 체인 쌍 표는 `{metal,l2}` ↔ `{metal,dao,l2}` 1쌍뿐이다 — 실서버에서 `unknown` 으로 남는 국내 망을 보며 표를 늘린다(규칙을 느슨하게 풀지 않는다).
- (003·005) `/spreads` 의 `fwd`·`rev` 는 슬리피지 차감 후 순값이고 Influx `premium` 은 차감 전 원값이다. 저장 시점에 체결 규모가 정의되지 않기 때문이며, 그 대가로 `/history/streaks?threshold=` 는 화면 값보다 큰 값을 기준으로 구간을 센다. 백필(캔들 기반)도 원값만 만들 수 있어 아카이브 동질성 쪽을 택했다.
- (005) 초 단위 백필 92일(BTC ≈ 457만 점) 위에서 **전 구간** `/history/streaks` 는 EC2(4GB)의 Influx 를 재시작시킨다(60초+ 후 504, 2026-08-30 실측). `start` 로 범위를 준 조회(7일 ≈ 8초)는 정상. 후속 스펙 후보: 오래된 데이터 1m 롤업 또는 조회 구간 상한. nginx read timeout(60초)도 함께 볼 것.
