# 화면 참조 원본

기존 `marketlens-fe` 레포에서 그대로 복사한 파일. 셸·표·KPI·카드·배지의 **구조와 인라인 스타일의 진실**이다(스펙 002 §3.2). 컴파일·린트 대상이 아니며 실행 세션이 읽고 옮기는 용도다.

import 경로는 원 레포 기준이라 여기서는 깨져 있다. 이 레포 대응:

| 원본 | 이 레포 |
|---|---|
| `../components/ui` | `web/src/shared/ui.tsx` |
| `../lib/format` | `web/src/shared/format.ts` |
| `../config` `POS` `NEG` | `--color-up` `--color-down` 토큰 |
| `../config` `pctColor` | `web/src/shared/format.ts` |
| `../config` 임계값·stale | `web/src/shared/config.ts` |
| `../data/types` | `web/src/shared/types.ts` |
| `../data/mockFeed` | `web/src/shared/mock.ts` |

원본 포맷터 이름(`fmtKRW` `fmtUSDT` `fmtAge`)은 이 레포 이름(`fmtKrw` `fmtUsdt` `fmtAgo`)과 다르다. 이 레포 이름을 쓴다.
