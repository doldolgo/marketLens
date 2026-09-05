# status.md — 현재 상태

> 실행 세션이 스펙을 끝낼 때마다 갱신한다. "무엇이 실제로 돌아가는가" 만 쓴다.
> 표기: `없음` = 만들어야 하는데 아직 없음. `-` = 그 런타임에 해당 기능이 원래 없음.

## 이 레포
**재구축 중**: CLAUDE.md §4 에서 IN_PROGRESS·TODO 인 스펙(003·004·005·006·007)은 문서가 목표 구조(WebSocket 수집·틱 인계·원문 아카이브)를 말하고, 아래 표의 그 행들은 아직 **재구축 전 런타임**을 말한다. 각 실행 세션이 끝날 때 그 행을 갱신한다. 001·009·010·011·012 가 끝나 토대(행·틱·계약)·3거래소 스트림·틱 저장 3계층(Redis → Influx `premium`·`dw_fail`)·원문 아카이브(S3 `raw/`)·수집 실패 이력(`/health/collect`·Influx `collect_fail`)은 새 구조다.

| 기능 | server | web | 비고 |
|---|---|---|---|
| collect | 업비트·빗썸 WS 실시간 갱신·마켓 우주 10분·1초 틱·/health | - | 바이낸스 스트림은 012 |
| web-shell | - | 셸·KPI·mock 탭 3종(gap·pp·flow) 동작 | spreads/history 탭은 placeholder |
| spreads | `/spreads`·`/refresh` 동작, `notional` 규모로 호가를 걷어 슬리피지 차감, USDT 시세 미갱신 경고, `spark` 는 009 가 게시한 30분 추이 | 실데이터 탭·1초 폴링·규모 세그먼트 | 행 17키. 스파크라인 렌더는 후속 |
| analysis | 6개 엔드포인트 동작 | - | HTTP 계약 camelCase |
| history | `/history/*` 3종·백필 (Influx `premium`·`dw_fail` 쓰기는 009 flusher) | 기록 탭 (mock) | `/history/*` 실데이터 연결은 후속 |
| wallet-status | 3거래소 조회·60초 캐시·`/spreads` 망 판정 | - | 표시는 spreads 탭이 담당 |
| deploy | Dockerfile·compose 4컨테이너(server·web·influxdb·redis)·CI/deploy 워크플로 | nginx 서빙(:${WEB_PORT}) | 로컬 검증 완료 — EC2 반영·PR check 는 GitHub 권한 대기 |
| tick-store | 틱 인계 큐(600, 종료 시 비우기 5초 상한) → Redis Stream `ticks` → 60초 flusher(1,000건 페이지 단위로 쓰고 지움) → Influx `premium`·`dw_fail`(멱등), spark 30분 링버퍼·기동 복원 | - | Redis·Influx 불달이어도 앱은 뜬다. 실서버(EC2) 수동 확인은 대기 |
| raw-archive | 거래소 원문 전량 S3 적재(거래소별 60초·32MB 객체) | - | 읽기 API·재생 도구 없음, lifecycle 은 사람 몫. `S3_BUCKET` 없으면 비활성. 입출금 REST 응답은 006 이 `record` 를 꽂기 전까지 제외. EC2 적재 확인은 대기 |
| health | /health/collect·틱 판정(연결·30초 무수신·샤드) → 실패 구간 추적·collect_fail 쓰기/복원 | 실데이터 탭·5초 폴링·KPI 수집 상태 | 백오프는 013. EC2 에서 차단·재기동 복원 수동 확인 대기 |
| binance-stream | WS 3샤드 depth20+miniTicker·exchangeInfo 10분·샤드 단위 정체 판정 | - | 해외 최대 20단계 |

## 알려진 빚
- (001) `server/build/`(setuptools 산출물 76파일)와 `server/marketlens_server.egg-info/` 가 git 에 추적돼 있다 — ruff 기본 제외라 검증엔 무해하지만 별도 chore 로 지울 것. venv 의 패키지는 editable 설치만 허용한다(dev-setup.md) — 비-editable 사본이 남아 있으면 `server/` 밖 cwd 에서 옛 모듈을 import 한다.
- (001·006) 입출금 REST 응답 본문은 아직 원문 싱크에 기록되지 않는다 — `WalletStatusService` 에 `record` 주입 자리가 없다. 006 세션 몫.
- (010) 원문 유입은 하루 10~20GB(gzip 후) **추정** — EC2 에서 1분 객체 크기·초당 줄 수를 실측한 뒤 버킷 lifecycle 을 정한다. 입출금 REST 응답 본문은 006 이 `record` 를 주입하기 전까지 아카이브에 없다.
- (003·005) `/spreads` 의 `fwd`·`rev` 는 슬리피지 차감 후 순값이고 Influx `premium` 은 차감 전 원값이다. 저장 시점에 체결 규모가 정의되지 않기 때문이며, 그 대가로 `/history/streaks?threshold=` 는 화면 값보다 큰 값을 기준으로 구간을 센다. 백필(캔들 기반)도 원값만 만들 수 있어 아카이브 동질성 쪽을 택했다.
- (005) 초 단위 백필 92일(BTC ≈ 457만 점) 위에서 **전 구간** `/history/streaks` 는 EC2(4GB)의 Influx 를 재시작시킨다(60초+ 후 504, 2026-08-30 실측). `start` 로 범위를 준 조회(7일 ≈ 8초)는 정상. 후속 스펙 후보: 오래된 데이터 1m 롤업 또는 조회 구간 상한. nginx read timeout(60초)도 함께 볼 것.
