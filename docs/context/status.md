# status.md — 현재 상태

> 실행 세션이 스펙을 끝낼 때마다 갱신한다. "무엇이 실제로 돌아가는가" 만 쓴다.
> 표기: `없음` = 만들어야 하는데 아직 없음. `-` = 그 런타임에 해당 기능이 원래 없음.

## 이 레포
| 기능 | server | web | 비고 |
|---|---|---|---|
| collect | 1초 수집 루프·커넥터 3종·`/health` 동작 | - | `/refresh` 노출은 003 몫 |
| web-shell | - | 셸·KPI·mock 탭 3종(gap·pp·flow) 동작 | spreads/history 탭은 placeholder |
| spreads | `/spreads`·`/refresh` 동작, `notional` 규모로 호가를 걷어 슬리피지 차감, USDT 시세 미갱신 경고 | 실데이터 탭·1초 폴링·규모 세그먼트 | 행 17키, `spark` 는 009 몫 |
| analysis | 6개 엔드포인트 동작 | - | HTTP 계약 camelCase |
| history | Influx 영속·persist 60초·`/history/*` 3종·백필 | 기록 탭 (mock) | `/history/*` 실데이터 연결은 후속 |
| wallet-status | 3거래소 조회·60초 캐시·`/spreads` 망 판정 | - | 표시는 spreads 탭이 담당 |
| deploy | Dockerfile·compose 3컨테이너·CI/deploy 워크플로 | nginx 서빙(:${WEB_PORT}) | 로컬 검증 완료 — EC2 반영·PR check 는 GitHub 권한 대기 |
| s3-snapshot | 60초 snapshot 루프·S3 업로드 | - | 읽기 API 없음, 버킷 lifecycle 은 사람 몫 |
| health | /health/collect·실패 구간 추적·collect_fail 쓰기/복원 | 실데이터 탭·5초 폴링·KPI 수집 상태 | 백오프는 013 |
| binance-depth | WS 깊이 스트림 3샤드·깊이 캐시·정체 watchdog | - | 해외 최대 20단계, HTTP 계약 무변경 |

## 알려진 빚
- (003·005) `/spreads` 의 `fwd`·`rev` 는 슬리피지 차감 후 순값이고 Influx `premium` 은 차감 전 원값이다. 저장 시점에 체결 규모가 정의되지 않기 때문이며, 그 대가로 `/history/streaks?threshold=` 는 화면 값보다 큰 값을 기준으로 구간을 센다. 백필(캔들 기반)도 원값만 만들 수 있어 아카이브 동질성 쪽을 택했다.
- (005) 초 단위 백필 92일(BTC ≈ 457만 점) 위에서 **전 구간** `/history/streaks` 는 EC2(4GB)의 Influx 를 재시작시킨다(60초+ 후 504, 2026-08-30 실측). `start` 로 범위를 준 조회(7일 ≈ 8초)는 정상. 후속 스펙 후보: 오래된 데이터 1m 롤업 또는 조회 구간 상한. nginx read timeout(60초)도 함께 볼 것.
