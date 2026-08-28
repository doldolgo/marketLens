# status.md — 현재 상태

> 실행 세션이 스펙을 끝낼 때마다 갱신한다. "무엇이 실제로 돌아가는가" 만 쓴다.
> 표기: `없음` = 만들어야 하는데 아직 없음. `-` = 그 런타임에 해당 기능이 원래 없음.

## 이 레포
| 기능 | server | web | 비고 |
|---|---|---|---|
| collect | 1초 수집 루프·커넥터 3종·`/health` 동작 | - | `/refresh` 노출은 003 몫 |
| web-shell | - | 셸·KPI·mock 탭 4종 동작 | spreads/history 탭은 placeholder |
| spreads | `/spreads`·`/refresh` 동작 (입출금 전부 null) | 실데이터 탭·1초 폴링 | `spark` 빈 배열, 망 판정은 006 |
| analysis | 6개 엔드포인트 동작 | - | BE 전용, snake_case |
| history | Influx 영속·persist 60초·`/history/*` 3종·백필 | 기록 탭 (mock) | `/history/*` 실데이터 연결은 후속 |
| wallet-status | 없음 | - | |
| deploy | 없음 | 없음 | |

## 알려진 빚
(없음)
