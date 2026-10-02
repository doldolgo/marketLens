# 036 — admin-v2

상태: DONE | 의존: **034·035 가 main 에 머지된 뒤 시작한다**(아니면 멈추고 묻는다). 계약을 쓰는 스펙(쓰는 계약은 §3.2 에 복사했다): 003 spreads(`/refresh`), 011 health(`/health/collect`), 025 slack-alerts(`/health` 두 역할), 027 observability(지표·경보 이름과 임계), 029 admin(화면·보안 계약 — 이 스펙이 화면을 바꾼다), 030 admin-tunnel(들어오는 길), 033 clarity(관리자 단언), 034·035(관리자 피드 넷). Clarity 칸은 033·035 가 켜진 뒤에 찬다 — 그 전에는 "연결 안 됨" 이다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 `admin.kimptrack.com` 한 페이지를 위에서 아래로 읽으며 "지금 괜찮은가 → 어디가 문제인가 → 누가 얼마나 쓰는가 → 돈은 얼마나 나가는가" 를 안다. 029 의 앱 안쪽 상태(헬스·거래소 수집 상태·접속 수·즉시 갱신)에 034·035 가 모은 CloudWatch 경보·지표·canary·예산, Slack·경보 알림 기록, 서버 접속 요약, Clarity 요약을 더해 AWS·Clarity·Cloudflare·Slack 콘솔을 돌지 않게 한다. AWS 계정은 2026년 12월에 끝난다 — AWS·Clarity 가 없거나 막혀도 그 칸만 "연결 안 됨"·"권한 없음" 이고 나머지는 그대로 동작한다.

## 2. 범위
- 만드는 것: `web/admin/` 세 파일(`index.html`·`admin.js`·`admin.css`) 전면 재작성, `server/tests/test_admin.py` 의 화면 정적 단언 확장.
- 하지 않는 것: 서버·nginx·관리자 경로(피드 경로·관리자 nginx 분기·접속 기록 제외는 034·035). 새 API. 빌드 도입·외부 라이브러리(차트 포함). 화면에서 AWS·Cloudflare·Clarity 를 바꾸는 조작 — 읽기 전용이고, 쓰는 버튼은 029 의 즉시 갱신 하나다. 브라우저에 값 쌓기(추이 누적 포함). 밝은 테마. 알림 규칙 변경(025·027).
- 바꾸는 기존 것: 029 §3.3(화면이 보이는 것·주기 — 보안 계약은 §3.8 로 옮겨 온다), 029 §2 하지 않는 것의 "CloudWatch 링크(027 배포 뒤 따로)", 029 §4 화면 정적 단언.
- 담당: 029 는 이 레포 주인 담당이라 고친다. 011·025 는 hereokay 담당 — 계약을 읽기만 한다.
- **빌드 없음을 유지한다.** 이유: Vite 다중 페이지에 넣으면 화면이 공개 root(dist)에 섞인다(029 가 막은 것) / CSP `default-src 'self'` 라 번들러 없이도 외부 코드가 없다 / 정적 단언이 소스 그대로를 본다. 스크립트가 길어져도 파일을 나누지 않는다 — 나누면 로드 순서·전역 이름이 계약이 되고 단언이 파일마다 는다.

## 3. 동작

### 3.1 한 페이지 구조
| id | 절 |
|---|---|
| `overview` | 개요 |
| `collect` | 수집 |
| `infra` | 인프라 |
| `alerts` | 알림 |
| `traffic` | 접속 |
| `cost` | 비용 |
| `tools` | 도구 |

- 절은 이 순서로 한 페이지에 쌓는다(탭 전환 없음 — 스크롤과 절 이동 링크).
- 머리(화면 위에 붙어 있음): `KimpTrack 관리자`, 종합 배지(§3.4 개요와 같은 값), `마지막 갱신 HH:MM:SS`, 절 이동 링크 일곱(`#id`), 로그아웃. 로그인 만료 알림 줄(029)은 머리 바로 아래.

### 3.2 읽는 값
**빠른 묶음 — 029 그대로(복사)**
- 025 `/health`(수집기 `/api/health`, api `/svc/api/health`): `{status, version, lastTickAt}` — `lastTickAt` 은 epoch ms 또는 null, `ok` 만 200 이고 `starting`·`stale`·`redis_down` 은 503 이지만 본문을 읽어 상태로 보인다.
- 029 `/svc/api/admin/status`: `{wsConnections, redis, influx, version}` — `redis`·`influx` 는 `ok`·`down`.
- 011 `/api/health/collect`: `{serverStartedAt, fetchedAt, successRate1h, exchanges[], outages[]}`. 거래소는 `upbit`·`bithumb`·`binance`·`bybit`·`bitget` 고정 순서, 항목마다 `exchange`·`state`(`ok`·`stale`·`down`)·`lastSuccessAt`·`markets`·`successRate1h`·`openOutage`·`lastError`(`at`·`kind`·`statusCode`·`message`). `outages` 는 24시간 구간 전부(`exchange`·`kind`·`startedAt`·`endedAt`(진행 중 null)·`count`·`statusCode`·`message`), 시각은 epoch ms.
- 003 `POST /api/refresh`: 헤더 `X-Refresh-Token`, 200 `{totalSaved, failures[{exchange, errorCode}], warnings[], …}`, 토큰이 틀리면 401 `{"detail"}`.

**느린 묶음 — 034·035 피드 넷(복사)**

| 경로 | 스펙 |
|---|---|
| `/api/admin/aws` | 034 |
| `/api/admin/alerts` | 034 |
| `/svc/api/admin/access` | 035 |
| `/svc/api/admin/clarity` | 040 |

- 공통: 늘 200 JSON, 키 camelCase. **시각 단위** — `…At`(`fetchedAt`·`changedAt`·`lastRunAt`·`nextAt`·알림 `at`)은 epoch **ms**, `…Ts`(`startTs`·`endTs`·`firstTs`)와 점·시간 칸·`recent5xx` 의 `ts` 는 epoch **초**다. 차트 가로축은 `ts × 1000` 으로 ms 에 맞춘다. 응답은 **부분**들이고 부분은 `{state, code, fetchedAt, refreshSec, …값 키}` 다. `state` 는 `ok`·`unconfigured`·`denied`·`error`·`pending`(첫 조회가 3초 안에 안 끝남), `code` 는 `ok` 면 null·아니면 짧은 사유(AWS 오류 코드·`no_credentials`·`http_<상태>`·`timeout`·`redis`·`no_file`·`partial`·`bad_data` — 오류 문장·ARN 없음), `fetchedAt` 은 값을 만든 시각(없으면 null), `refreshSec` 는 서버 갱신 주기. `ok` 가 아니면 값 키는 null — 예외는 Clarity(값 = 마지막 성공, 7일 뒤 null).
- `/api/admin/aws` = 부분 넷 `{alarms, metrics, canary, budget}`:
  - `alarms`(60초): `items[{name, state, changedAt, reason}]`(이름 순, `state` 는 `OK`·`ALARM`·`INSUFFICIENT_DATA`, `reason` 200자)·`counts{ok, alarm, insufficientData}`.
  - `metrics`(300초): `startTs`·`endTs`·`periodSec`(300)·`boxes[{box, instanceId, mem, disk, cpu, credit, swap}]`(collect·data·serve 순, 경보가 없는 박스는 빠진다)·`wsClients`·`canary{runs, errors, durationMs}`. 시계열은 모두 `[[ts, 값|null], …]` 288점(5분 구간 시작), 전부 비면 null. `mem` = 메모리 가용률 최솟값, `disk` = 디스크 사용률 최댓값, `cpu` = CPU 평균, `swap` = 스왑 사용률 최댓값(%, 027 설정상 data 만 — collect·serve 는 null), `credit` = CPU 크레딧 잔고 최솟값(t4g 만 — collect c7g 는 null). `canary` 의 `runs`·`errors` 는 5분 합, `durationMs` 는 최댓값.
  - `canary`(60초): `lastRunAt`·`durationMs`·`ok`·`lines[]`(300자·20줄 — "N단계 통과 (ms)" 또는 실패 메시지). 11분 안에 끝난 실행이 없으면 `lastRunAt`·`durationMs`·`ok` null·빈 `lines`(state `ok`).
  - `budget`(6시간): `items[{name, unit, limit, actual, forecast, timeUnit}]` — 비용 예산 전부(`timeUnit` 은 `MONTHLY` 등), 금액 소수 둘째 자리, `forecast` 없으면 null.
- `/api/admin/alerts` = `{items, slack, alarms}` — `slack`·`alarms` 는 상태만 있는 부분(`slack` 은 `refreshSec` 0), `items` 는 늘 목록(성공한 쪽만). 항목 `{at, source, text, role, key, delivered, alarm, fromState, toState}`(해당 없는 키 null), 최근 7일·최신순·200건. `source: "slack"` 은 025 알림 — `role` `collector`·`api`, `text` 는 🔴·🟢·⚠️ 머리 그대로, `delivered` 는 Slack 이 받았는지(전송 실패도 기록된다, 억제된 알림은 없다). `source: "alarm"` 은 경보 상태 변경 — `alarm`·`fromState`·`toState`·`text`(사유). ARN·12자리 숫자는 `[가림]` 으로 와 있다.
- `/svc/api/admin/access` = 부분 하나(60초): `startTs`·`endTs`·`firstTs`(읽은 가장 이른 줄, 없으면 null)·`totals{requests, pages, ws, skipped}`·`hourly[{ts, requests, pages, errors}]` 24개(`errors` = 5xx)·상위 목록 여섯 `paths`·`tabs`·`referrers`·`utmSources`·`devices`·`browsers`(각 `[[이름, 수], …]` 20개까지 — `tabs` 는 탭 id 여섯과 `(기타)`, `devices` 는 `bot`·`mobile`·`desktop`·`unknown`)·`status{"2xx","3xx","4xx","5xx"}`·`recent5xx[{ts, path, status}]` 20줄(경로는 쿼리 없음)·`ws{count, durations{lt10s, lt1m, lt10m, lt1h, ge1h}}`. IP·UA 원문 없음, 폴링·canary 제외.
- `/svc/api/admin/clarity` = 부분 하나(14400초, 040) + `nextAt`·`numOfDays`(1)·`traffic{sessions, botSessions, users, pagesPerSession}`·`summary{scrollDepth, totalSec, activeSec, signals}`(signals 키 여섯 `deadClick`·`rageClick`·`excessiveScroll`·`quickback`·`scriptError`·`errorClick`, 값 `{sessions, sessionPct, pageViews, count}`)·`countries[[이름, 세션]]`(20행, 못 알아보면 null)·`metrics[{name, rows}]`(받은 이름·키 그대로, 행 20개, 주소는 쿼리를 떼되 대시보드 주소는 `?tab=<id>` 만)·`pages`(하위 부분 `{state, code, fetchedAt, refreshSec 43200, nextAt, numOfDays 3, rowsIn, rowLimitHit, groups}` — `groups[{page, device, sessions, scrollDepth, totalSec, activeSec, deadClickPct, rageClickPct, excessiveScrollPct, quickbackPct, scriptErrorPct, errorClickPct}]` 40개 이하, 주소 없음). 못 알아본 칸은 null. 창은 부른 때 직전 24시간(기본)·72시간(`pages`)이고 시간대가 없다(Clarity 문서는 결과를 UTC 로 적는다).

### 3.3 갱신 주기
- 빠른 묶음(경로 넷) 10초, 느린 묶음(피드 넷) 60초 — 둘 다 **보이는 동안만**(`visibilityState`). 두 묶음은 따로 돈다 — 느린 쪽이 늦어도 빠른 쪽 주기를 막지 않는다. 같은 묶음은 앞선 호출이 끝나기 전에 다시 부르지 않는다.
- 숨었다가 보이면 빠른 묶음은 곧바로, 느린 묶음은 마지막 호출에서 60초가 지났을 때만 곧바로 부른다(탭을 오가도 몰리지 않게).
- 느린 값의 신선도는 서버 캐시가 정한다. 화면은 부분마다 머리에 `fetchedAt` 경과("4분 전 값")를 늘 적고, 경과가 응답에 온 그 부분의 `refreshSec` × 3 을 넘으면 주의색으로 칠한다(`refreshSec` 0 인 `slack` 은 보지 않는다). 상수를 복사해 두지 않는다 — 서버 주기가 바뀌어도 화면을 고치지 않게.
- 브라우저에 쌓는 값은 없다. 추이는 전부 피드가 준 점으로 그린다 — 며칠 열어 둬도 메모리가 늘지 않게.
- 다시 그리기: 머리의 경과·절 요약·개요는 묶음이 끝날 때마다, 칸의 본문(목록·표·차트)은 그 칸의 값이 바뀌었을 때만 — 빠른 묶음이 느린 칸의 목록 안 스크롤·초점·펼침·툴팁을 날리지 않게. 알림 목록은 필터를 바꿀 때만 맨 위로.
- 본문 안 경과 글자("n분 전" — 경보 행의 바뀐 지·canary 최근 실행)는 묶음이 끝날 때마다 그 글자만 고친다 — 본문을 다시 만들지 않으면서 개요 칸·절 요약과 같은 경과를 보인다. 글자가 달라졌을 때만 쓴다 — 같은 글자를 다시 쓰면 텍스트 노드가 바뀌어 그 안에 걸친 글자 선택이 풀린다.

### 3.4 절별 내용
**개요** — 타일 일곱: 종합·수집기·api·지금 접속·경보·canary·이번 달 비용. 타일을 누르면 그 절로 간다.
- 종합 판정(위에서부터 먼저 맞는 것):
  - 알 수 없음: 수집기 또는 api `/health` 호출 자체가 실패(403·JSON 아님·만료).
  - 장애: 수집기 또는 api 상태가 `ok` 아님, Redis·Influx 중 `down`, `ALARM` 경보 1개 이상, 거래소 하나라도 `down`.
  - 주의: 거래소 `stale`, canary 최근 실행 실패(`ok` false), `alarms`·`canary` 부분의 `error`.
  - 정상: 그 밖. `unconfigured`·`denied`·`pending` 과 그 밖 부분(`metrics`·`budget`·알림 `slack`·접속·Clarity)의 `error` 는 판정에 넣지 않는다 — 그 칸에만 보인다. AWS 가 끝나거나 AWS 밖으로 옮겨도(`unconfigured`) 개요가 늘 주의가 되지 않게.
- 종합 타일 아래에 사유를 최대 셋 한 줄로("경보 ALARM collect-memory · bybit 끊김" — `ALARM` 경보는 둘까지 이름, 셋 이상이면 "경보 3개 ALARM"). 판정 재료를 못 읽었으면(`/health/collect`·`/admin/status` 호출 실패) 판정은 그대로 두고 사유에 "거래소 상태 모름"·"Redis·Influx 상태 모름". 다른 타일 값: 수집기(상태·마지막 틱 n초 전), api(상태·Redis·Influx), 지금 접속(`wsConnections`), 경보(`ALARM` 수 / 전체), canary(통과·실패·n분 전), 비용(월 예산 중 한도 대비 실제 비율이 가장 큰 것의 실제 / 한도).

**수집** — 029 화면에서 옮기고 넓힌다.
- 요약 줄: 전체 1시간 성공률·마켓 수 합·수집기 시작 시각(`serverStartedAt`)·두 역할 버전.
- 거래소 표: 거래소·상태 배지·열린 실패 구간(`kind · n회 · n분째`)·마지막 성공(n초 전)·1시간 성공률·마켓 수·마지막 오류(시각·kind·HTTP(있을 때)·`message` 앞 300자 한 줄, 전체는 `title` — 열린 구간이 없는 거래소는 흐림).
- 실패 구간 24시간 타임라인: 거래소 다섯 줄, `outages` 를 막대로(진행 중은 지금까지), `banned`·`rate_limit` 은 장애색·꽉 찬 높이, 그 밖은 주의색·낮은 막대(색만으로 가르지 않는다), 1분 미만도 최소 폭, 막대마다 `HH:mm–HH:mm · kind · ×count`, 아래에 최신 다섯 구간을 같은 글자로(휴대폰은 `title` 을 못 본다). 구간이 없으면 "최근 24시간 실패 없음". 011 의 수집 상태 탭과 같은 색 규칙이다.
- 즉시 갱신: 029 그대로 — 토큰칸(`type=password`·자동완성 끔)·버튼·결과(HTTP 상태·`totalSaved`·`failures` 의 거래소·`errorCode`·`warnings`).

**인프라** — `metrics`·`alarms`·`canary`. 순서는 경보·canary → 박스 카드. 세 부분이 같은 이유로 비면(연결 안 됨·권한 없음·같은 호출 실패) 절 전체를 카드 하나로 접는다.
- 박스 카드 셋(collect 수집기 / data Influx·Redis / serve caddy·web·api, `instanceId` 는 `title`): 메모리 가용률·디스크 사용률·CPU 사용률 선 차트와 각각 지금 값·24시간 최저(메모리)·최고(디스크·CPU). 기준선은 027 경보 임계(메모리 10%·디스크 80%). 값 색: 메모리 10% 미만 장애·20% 미만 주의, 디스크 80% 초과 장애·70% 초과 주의(주의 기준은 기본값 — 사람 확인), CPU 는 색 없음. t4g 박스(data·serve)는 크레딧 잔고 선과 지금·24시간 최저 — 027 잔고 경보 임계(최대 적립의 30% — data 173·serve 86) 미만이면 장애색. 카드 머리에 지금 값 중 가장 나쁜 주의·장애 배지, 축 글자는 카드 맨 아래 한 번. 스왑은 값이 있는 박스만(지금은 data) — serve 스왑은 027 에이전트가 모으지 않아 카드에 "스왑 지표 없음" 한 줄.
- 경보 표: 이름(`marketlens-` 접두를 뗀다, 그 아래 둘째 줄에 사유 앞 160자·전체 `title`)·상태 배지·바뀐 지(n분 전). 정렬 `ALARM` → `INSUFFICIENT_DATA` → `OK`, 같으면 이름순. `OK` 행은 접힌 묶음("정상 n개") 안에 두고, 다시 그려도 펼침 상태를 유지한다.
- canary 카드: 최근 실행(통과·실패·n분 전), 24시간 실행·오류 수(`metrics.canary` 합), 실행 시간(ms) 선 차트, 최근 실행 로그 줄(고정폭 글꼴, 최대 10줄).

**알림** — 알림 기록.
- 필터 버튼 셋: 전체·앱(Slack)·경보(CloudWatch). 고른 값은 JS 변수에만 둔다.
- 행: 시각(오늘이면 `HH:mm:ss`, 아니면 `MM-DD HH:mm`)·출처 배지(`collector`·`api`·`경보`)·글. Slack 글은 🔴 로 시작하면 장애색, 🟢 정상색, ⚠️ 주의색, 그 밖 기본색. `delivered` 가 false 면 "전송 실패" 배지(주의색 — Slack 에는 없는 알림이다). 경보 행의 글은 `<이름> <이전> → <새>`, 새 상태 `ALARM` 장애색·`OK` 정상색·`INSUFFICIENT_DATA` 흐림. `key` 는 `title`.
- 머리 줄 "n건 · 보낸 Slack 알림(전송 실패 포함)과 경보 상태 변경 — 억제된 알림은 없다". 최신순, 목록 높이 상한을 넘으면 목록 안에서 스크롤.

**접속** — 세 덩어리.
- 실시간: 지금 WebSocket 접속 수(빠른 묶음 `wsConnections`) 큰 숫자 + 24시간 `wsClients` 선. 부제 "열린 대시보드 수 — 사람 수가 아니다".
- 서버 기록 24시간(접속 요약): 총 요청·페이지·시간대별 막대 24개(5xx 는 장애색으로 겹침 — 최고 n/시간과 5xx 가 난 시간은 글자로도)·상태 코드 대분류, WebSocket 연결 수·지속 시간 구간 막대(구간 이름 아래 수), 표 여섯(경로·탭·외부 출처·`utm_source`·기기·브라우저 — 이름·수·비율 막대, 상위 10), 최근 5xx 표(시각·경로·상태, 20행). `firstTs` 가 `startTs` 보다 늦으면 부제에 "기록 시작 HH:mm". 부제 "폴링·canary 제외, IP 없음 · 읽지 못한 줄 n"(`skipped`).
- Clarity(Clarity 요약): 타일 넷(세션·봇 세션·사용자·세션당 페이지)과 "받은 지표" 목록 — 지표마다 이름 한 줄과 행마다 `키:값 · 키:값` 글자 한 줄(20행, 한 줄 200자에서 자르고 전체는 `title`). 부제 "최근 1일 · UTC 기준 — Clarity 가 준 값". 정규화 값(`summary`·`countries`·`pages`)은 040 응답에 있다 — 그리는 것은 043.

**비용** — `budget`.
- 월 단위(`timeUnit` `MONTHLY`) 비용 예산 전부를 한 줄씩: 이름·실제·한도·예측(달러 소수 2자리), 가로 막대 하나(실제 사용액, 85%·100% 표시선 — 027 예산 알림 기준)와 이번 달이 지난 비율 표시선(한도 × 지난 비율 자리 — 실제 막대가 넘으면 한도보다 빠르게 쓰는 중). 예측이 한도를 넘으면 주의색. 월 단위가 아닌 예산은 막대 없이 이름·실제·한도만. 예산이 0개면 "예산 없음".
- 고정 문구 둘: "서비스별 내역은 조직 SCP 가 Cost Explorer 를 막아 여기 없다 — 결제 콘솔에서 본다", "AWS 계정 종료 예정 2026-12"(날짜는 사람 확인).

**도구** — 링크 묶음. 앱: API 문서 `/api/docs`·ReDoc `/api/redoc`(029). AWS 서울: CloudWatch 경보, CloudWatch 지표(네임스페이스 `MarketLens`), Lambda `marketlens-smoke`, 로그 그룹 `/aws/lambda/marketlens-smoke`, 예산(결제 콘솔). Clarity: 프로젝트 목록 `https://clarity.microsoft.com/projects`. Cloudflare: 루트 둘만 — `https://one.dash.cloudflare.com/`(Zero Trust)·`https://dash.cloudflare.com/`(DNS). GitHub: 레포 Actions. 로그아웃은 머리에 있다.

### 3.5 칸의 상태
| 상태 | 보이는글 |
|---|---|
| 첫호출전 | `…` |
| `pending` | `…` |
| `ok` | 값 |
| 빈목록 | 기록없음 |
| `unconfigured` | 연결안됨 |
| `denied` | 권한없음 |
| `error` | 불러오지못함 |

- 부분의 상태는 그 부분 칸에만 보인다 — AWS 예산만 `denied` 면 비용 절과 비용 타일만 "권한 없음" 이고 경보·지표는 그대로다.
- `unconfigured`: 흐린 배지 "연결 안 됨" + 한 줄 원인(AWS "AWS 자격 없음 또는 계정 종료", 접속 "로그 파일 없음", Clarity "토큰 없음 또는 033 전"). `denied`: 흐린 배지 "권한 없음" + "IAM 정책 또는 조직 SCP — 콘솔에서 본다". `pending`: `…` 와 "첫 조회 중", 판정 제외. 배지 옆에 `code` 를 작게 적는다.
- 값 규칙: **값이 null 이면 칸을 비운다.** `error` 인데 값이 있으면(Clarity — 마지막 성공 값) 값을 그대로 보이고 장애색 배지 "불러오지 못함 · 마지막 성공 n시간 전" 을 단다. `error` 에 값이 없으면 장애색 "불러오지 못함" 과 빈칸.
- HTTP 수준 실패(403·JSON 아닌 응답·만료 신호)는 029 규칙 그대로 — 그 호출이 채우는 칸을 모두 비우고 사유("권한·설정 오류 (403)"·"응답 오류 (HTTP n)"·"로그인 만료·연결 끊김")를 적는다. 직전 값이 정상으로 읽히지 않게.
- 값은 왔는데 목록이 0개면 "지난 24시간 기록 없음" 처럼 그 칸의 빈 문구. 차트는 값 있는 점이 2개 미만이면 차트 대신 "값 없음"(0개)·"값 1개뿐"(1개) — 경보 상태 `INSUFFICIENT_DATA` 의 "데이터 부족"(AWS 한국어 이름)과 겹치지 않게.

### 3.6 차트
- 외부 라이브러리 없이 SVG 요소를 DOM 으로 만든다. 모양은 기하 속성(`viewBox`·`points`·`d`·`x`·`y`·`width`·`height`)으로만, 색·선 굵기는 CSS 클래스(토큰)로 준다. `style` 속성·`element.style`·`cssText` 는 쓰지 않는다 — CSP 가 `style` 속성과 `cssText` 를 막는다(MDN style-src, 2026-10-01 확인). `element.style.속성` 은 허용되지만 한 규칙으로 둔다.
- 네 종류: 선(24시간 추이), 세로 막대(시간대별 요청·지속 시간 구간), 가로 막대(표의 비율·예산), 타임라인(실패 구간).
- 선: 가로는 칸 폭을 따라 늘고 선 굵기는 그대로다(`non-scaling-stroke`). null 점에서 선을 끊는다 — 보간하지 않아 빈 구간이 비어 보인다. 비율 지표는 세로 0~100 고정, 개수·ms·크레딧은 0~최댓값. 기준선은 점선. 축 대신 글자로 "24시간 전·지금" 과 최저·최고·지금 값. 타임라인·시간대별 막대는 창을 5등분한 눈금(`HH:mm`)과 "지금".
- SVG 속성은 허용 목록(기하·`role`·`aria-label`)만 — 그 밖의 이름(`href`·`style`·`on…`)은 만들다 멈춘다. 그림 요소는 `svg`·`g`·`line`·`rect`·`path`·`title` 뿐.
- 모든 SVG 에 `role="img"` 와 요약 `aria-label`(예 "serve 메모리 가용률 24시간 — 최저 31%, 지금 42%"), 막대·구간마다 `<title>`(글자로) — 마우스를 올리면 값이 보인다. 애니메이션 없음.

### 3.7 디자인
- 토큰의 진실은 `docs/design/theme.css` 다. `admin.css` 는 그 `:root` 토큰(색·램프·간격·반경·그림자)과 컴포넌트 클래스 `.card`·`.card-kicker`·`.tag`·`.table`·`.btn`·`.btn-primary`·`.btn-secondary`·`.input`·`.hr` 를 복사한다(번들 밖이라 `web/src/shared` 를 못 쓴다 — 029 와 같다). 첫 줄 `@import`(Google Fonts)와 Inter 는 뺀다 — CSP 가 막고, 불러오면 운영자 IP 가 국외로 나간다. 글꼴은 시스템 글꼴.
- 상태색: 정상 `--color-ok`, 주의 `--color-warn`, 장애 `--color-up`, 흐림 `--color-neutral-500`. 색만으로 가르지 않는다 — 배지에 글자(정상·주의·장애·알 수 없음, `OK`·`ALARM`). ✕·장애색은 §3.4 판정의 장애 재료(와 그 임계 값·5xx)에만 — 판정 밖 부분의 호출 실패·`error` 는 개요 칸·절 요약에서 ▲. 작은 글자는 WCAG AA 4.5:1 이상(배지의 주의·장애 글자는 상태색을 흰색 쪽으로 70% 섞어 밝힌다).
- 절 라벨은 `.card-kicker` 모양, 숫자는 `tabular-nums`, 표 행 구분은 theme.css `.table` 의 양끝이 옅어지는 선. 참조 화면은 `docs/design/reference/tabs/HealthTab.tsx`(요약 줄·카드·24시간 타임라인·로그 표).
- 폭: 본문 최대 1200px, 카드 격자는 칸 폭 260px 이상으로 자동 줄바꿈. 640px 이하는 한 줄 배치·좌우 여백 16px·머리의 절 이동은 옆으로 미는 한 줄·표는 카드 안에서 가로 스크롤. **페이지 전체 가로 스크롤 없음**(360px 에서도). 한국어는 낱말 단위로 줄을 바꾸고(`word-break: keep-all` — 랜딩·처리방침·404·동의 띠와 같다) 칸보다 긴 낱말만 넘칠 때 끊는다(`overflow-wrap: break-word`). 누르는 곳은 36px 이상. 어두운 테마 하나(`color-scheme: dark`).

### 3.8 보안 계약 (029 에서 옮김 + 더함)
- 029 그대로: 비밀값 없음. 서버·방문자가 정하는 글자는 `textContent` 로만 넣는다(HTML 해석 금지). 즉시 갱신 토큰은 입력칸과 JS 변수에만(`localStorage`·`sessionStorage`·IndexedDB·쿠키 금지, `form` 없음). 모든 요청은 한 함수를 지나 `X-Requested-With: XMLHttpRequest` 를 붙이고 같은 출처 상대 경로(`/api/…`·`/svc/…`)만 부른다. 링크는 같은 탭(`target`·`window.open` 없음). CSP 는 029 그대로(`default-src 'self'; frame-ancestors 'none'`).
- 세션 판별(029 그대로): (1) 401 + 앱 JSON(`detail`)은 토큰 오류 — 새로고침하지 않는다. (2) 401 인데 JSON 이 아니거나 fetch 자체가 실패하면 로그인 만료 신호 — 표시 `?relogin=1` 이 없으면 `/?relogin=1` 로 한 번 새로고침, 있으면 알림 줄만. 주소는 `/` 로 고정한다. (3) 403 은 "권한·설정 오류".
- **표시 지우기(바뀜)**: 마지막 만료 신호(없으면 이 화면을 연 때) 뒤 여덟 경로(빠른 넷·느린 넷)가 모두 한 번 이상 만료 신호 없이 끝났을 때만 지운다 — 만료 신호가 오면 센 것을 처음부터 다시 센다. 묶음이 둘이라, 한 묶음만 보고 지우면 다른 묶음에만 있는 만료가 60초마다 새로고침을 되풀이한다. 즉시 갱신 버튼은 지우지 않는다(029).
- **더함**: 방문자가 정하는 값(경로·탭·외부 출처·`utm_source`·기기·브라우저 이름·Clarity 행)과 서버 글(경보 사유·Slack 글·canary 로그·거래소 오류)은 `title` 말고는 어떤 속성(특히 `href`·`src`)에도 쓰지 않는다 — 방문자가 정한 출처나 Clarity 주소가 누를 수 있는 링크가 되면 운영자를 낚는 길이 된다. 보이는 글자에서 양방향 제어문자(U+202A–U+202E·U+2066–U+2069)를 뺀다. 길면 자르고 전체는 `title`.
- **더함**: 외부 링크는 `index.html` 의 고정 `https://` 주소뿐이고 모두 `rel="noreferrer"`(관리자 주소를 콘솔 쪽에 넘기지 않는다). 주소에 계정·영역 ID·Access AUD·팀 도메인(`*.cloudflareaccess.com`)·이메일·Clarity 프로젝트 ID(`/projects/view/<ID>`)·토큰을 넣지 않는다(레포 공개) — 대시보드에서 복사한 Cloudflare 주소에는 계정 ID 가 들어 있으니 루트 주소만 쓴다. 화면은 이미지 파일을 쓰지 않는다.

### 3.9 엣지
- 034·035 전 배포(피드 경로 없음): 느린 묶음이 404 → 인프라·알림·접속(서버 기록·Clarity)·비용이 "응답 오류 (HTTP 404)", 빠른 묶음·즉시 갱신은 그대로 — 그래서 두 스펙 머지가 시작 조건이다.
- AWS 계정 종료(2026-12)·AWS 밖으로 옮김: AWS 요약 네 부분과 알림의 `alarms` 가 `unconfigured`(자격 없음)가 되고, 인프라·비용 절과 개요의 경보·canary·비용 타일·접속의 24시간 선만 "연결 안 됨" 이다. 종합 판정은 §3.4 대로 앱 쪽 값과 `alarms`·`canary` 의 `error` 로만 한다 — `unconfigured` 는 판정 밖이다. AWS 링크는 남는다 — 이전이 정해지면 사람이 이 스펙을 고친다.
- Clarity 하루 호출 한도(프로젝트당 10회 — 035): 실패해도 마지막 성공 값과 "마지막 성공 n시간 전" 배지가 보이고, 판정은 바뀌지 않는다.
- 브라우저 시계가 서버보다 빠름: 경과가 음수면 0초(029 와 같다). 시각은 브라우저 시간대.
- 긴 목록: 피드 상한을 믿지 않고 화면도 자른다 — 알림 200행, 표 10행, 최근 5xx 20행, canary 로그 10줄, Clarity 지표마다 20행.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 main 에 034·035 가 있는지 본다(없으면 멈추고 묻는다).
- 정적 단언(`server/tests/test_admin.py` — 029 의 화면 단언은 유지하고 아래를 더한다):
  - `admin.js`: `X-Requested-With`·`visibilityState`·`createElementNS`·`refreshSec` 가 있다 / `fetch(` 는 1회 / `location.replace(`·`history.replaceState(` 각 1회, 주소 `/` 고정(029) / 10초·60초 주기 상수 / 본문 안 경과 글자는 시각을 data 속성에 둔 span 이고 그리기 끝에 글자만 고치는 함수가 돈다(글자가 다를 때만 쓴다) / 금지: `localStorage`·`sessionStorage`·`indexedDB`·`document.cookie`·`innerHTML`·`outerHTML`·`insertAdjacentHTML`·`document.write`·`eval(`·`new Function`·`window.open`·`location.pathname`·`location.href`·`.style`·`setAttribute('style'`·`.href`·`setAttribute('href'`·`setAttribute('src'`·`.src`·`setAttributeNS`·`xlink:href`·`new XMLHttpRequest`·`sendBeacon`·`new WebSocket`·`EventSource` / SVG 속성 허용 목록이 §3.6 그대로이고 `svg()` 가 그 밖을 던진다·글자 그대로 부르는 SVG 요소와 속성 키가 목록 안 / 파일 안의 `http://`·`https://` 는 SVG 이름공간 하나뿐.
  - `index.html`: 인라인 스크립트 본문·`style=`·`<form`·`target=` 없음(029) / 절 id 일곱이 §3.1 순서, 머리의 이동 링크 일곱 / 토큰칸·API 문서·ReDoc·로그아웃 링크(029) / 외부 링크(`//` 로 시작하는 것 포함, 따옴표 꼴 무관)는 전부 `https://` 이고 `rel="noreferrer"`, 호스트는 AWS 콘솔(`*.console.aws.amazon.com`)·`clarity.microsoft.com`·`dash.cloudflare.com`·`one.dash.cloudflare.com`·`github.com` 안이고 Cloudflare 링크는 경로가 `/` / 12자리 숫자·이메일 모양·`[0-9a-f]{32,}`·`cloudflareaccess.com`·`/projects/view/` 없음.
  - `admin.css`: `@import`·`url(` 없음 / `--color-bg`·`--color-surface`·`--color-ok`·`--color-warn`·`--color-up` 값이 `docs/design/theme.css` 와 같다 / 640px 미디어 쿼리 / `body` 에 `word-break: keep-all`·`overflow-wrap: break-word`.
  - 033 의 관리자 단언(`web/admin/*` 에 `clarity.js`·`clarity.ms` 없음)은 그대로 통과한다.
- 브라우저(Chromium, 029 §5 처럼 127.0.0.1 에만 게시한 테스트 compose + 가짜 백엔드 — 피드 넷을 `ok`·`unconfigured`·`denied`·`error`·`pending`·빈 목록·오래된 `fetchedAt`·null 섞인 점으로 바꿔 줄 수 있게):
  - 일곱 절이 다 채워지고 콘솔에 CSP 위반·Uncaught 0. 차트 가로축이 초 단위 `ts` 를 ms 로 옮겨 "24시간 전·지금" 이 맞다.
  - 방문자·서버 글자 칸 전부에 `<img src=x onerror=alert(1)>`·`javascript:alert(1)`·U+202E 를 넣은 응답 → 글자로 보이고 img 요소 0·alert 없음·U+202E 빠짐·그 글자가 링크가 아니다(`a[href]` 는 `index.html` 의 고정 링크뿐).
  - 부분 상태: AWS 예산만 `denied` → 비용만 "권한 없음" / AWS 전부 `unconfigured` → 판정은 앱 값으로만(정상) / 접속 `error` → 그 칸만, 판정 그대로 / `alarms` `error` → 주의 / `pending` → `…`·판정 제외 / Clarity 429(`error`) + 직전 값 → 값이 보이고 "불러오지 못함 · 마지막 성공 n시간 전" 배지 / `fetchedAt` 이 `refreshSec` × 3 보다 오래됨 → 주의색 / 빈 목록 → 빈 문구 / null 섞인 점 → 선이 끊김 / 점 1개 → "값 1개뿐".
  - 값: `delivered` false → "전송 실패" 배지 / `firstTs` 가 창 시작보다 늦음 → "기록 시작" / 예산 둘(월·연) → 두 줄, 월만 막대 / serve `swap` null → "스왑 지표 없음".
  - 종합 판정: 경보 1개 `ALARM` → 장애, 거래소 `stale` → 주의, 수집기 헬스 403 → 알 수 없음.
  - 주기: 보이는 탭 2분 동안 빠른 경로 각 12±1회·느린 경로 각 2~3회 / 숨긴 탭 2분 동안 0회 / 다시 보이면 빠른 묶음 곧바로 / 느린 가짜 경로를 30초 늦춰도 빠른 묶음은 10초 주기.
  - 세션: 느린 경로 하나만 401 비JSON → `/?relogin=1` 로 한 번, 그 뒤는 알림만(60초마다 새로고침하지 않는다) / 전부 정상으로 돌리면 표시 지움 / 즉시 갱신은 029 동작 그대로(틀린 토큰 → "401 토큰 오류").
  - 폭 1280·768·360 에서 `documentElement.scrollWidth ≤ innerWidth`, 표는 카드 안에서 스크롤 — 스크린샷 세 장을 §7 에.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`(dist 에 admin 없음).
- 커밋: ① HTML·CSS ② JS(절 단위로 나눠 각 300줄 이하) ③ 정적 단언 ④ §6 문서·§5·§7.

**배포 뒤 — 사람(완료 조건 아님, status.md 비고 "운영 확인 대기")**: 로그인 뒤 일곱 절이 실제 값 또는 정직한 상태("권한 없음" 등)로 보인다 / 예산이 SCP 로 막혔는지 비용 절에서 확인 / 휴대폰에서 가로 스크롤 없음 / 도구 링크가 맞는 콘솔 화면으로 간다 / 경보 수·canary 로그 줄이 콘솔과 같다 / 관리자 접속 기록(029)에 60초 폴링 경로가 없다(034·035).

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 시작 조건 — 이 브랜치는 035(PR #92, 머지 전) 위에 쌓였고 034 는 origin/main 에 있다(설계 세션이 이 쌓임으로 의존 충족으로 봄)
git fetch -q origin && git merge-base --is-ancestor origin/main HEAD   # 종료 코드 0

# 기존 스펙 재검증 (마지막 코드 커밋 뒤)
cd server && ruff check . && ruff format --check . && pytest -q   # All checks passed! · 286 files already formatted · 1236 passed
cd web && npm run lint && npm run build                            # oxlint 종료 코드 0 · ✓ built · dist 에 admin* 0개
node --check web/admin/admin.js                                    # 종료 코드 0
# 커밋마다 git archive → 임시 트리에서 pytest tests/test_admin.py tests/test_clarity.py tests/test_deploy.py
#   76 passed ×8 · 80 passed(정적 단언 커밋) · node --check 통과 · 커밋당 diff 122~294줄

# 로컬 Docker — 망 ml036-net, 가짜 백엔드 둘(python:3.12-alpine + <scratch>/036/fake.py, 망 별칭 server·api —
# <scratch>/036/ctl/mode.json 으로 부분 상태를 바꾼다, 기본값은 2026-10-01 운영 034 값), web :8081 은 127.0.0.1:18036 에만
docker build -t ml036-web web
docker run -d --name ml036-web --network ml036-net -e COLLECT_HOST=server -e 'NGINX_ENVSUBST_FILTER=^COLLECT_HOST$' \
  -v ./web/admin:/usr/share/nginx/admin:ro -p 127.0.0.1:18036:8081 ml036-web
docker exec ml036-web nginx -t     # syntax is ok · test is successful (nginx-admin.conf 는 고치지 않았다)

# 브라우저 — 헤드리스 Chrome 을 CDP 로(<scratch>/036/cdp.py·scenarios.py, scratch 프로필) + Claude 브라우저 창(1440·375, 어두운·밝은)
cdp.py looks       # 1440·1280·768·375·360(1440·375 는 밝은 모드도): scrollWidth == innerWidth, 카드 밖 가로 넘침 0, img 0,
                   #   securitypolicyviolation 0, Uncaught 0 — 콘솔은 브라우저가 스스로 부르는 /favicon.ico 404 한 줄뿐
cdp.py xss         # 방문자·서버 글자 칸 전부(거래소 오류·구간 url·경보 이름·사유·Slack 글·key·canary 줄·경로·출처·utm·브라우저·
                   #   최근 5xx·Clarity 이름·행·예산 이름·instanceId·버전·즉시 갱신 결과)에 <img onerror>·javascript:·U+202E
                   #   → 글자로 49곳, img 0, 대화상자 0, U+202E 0, title 밖 속성에 0, [href]·[src] 는 index.html 고정 링크뿐
states.sh          # 예산만 denied → 비용 칸·타일만 "권한 없음" / AWS 전부 unconfigured(+ 알림 alarms) → 정상(앱 값으로만)
                   # 접속 error → 그 칸만·판정 정상 / alarms error → 주의 / pending → "첫 조회 중"·판정 밖
                   # Clarity http_429 + 직전 값 → 값 + "불러오지 못함 · 마지막 성공 5시간 전" / fetchedAt > refreshSec×3 → "▲ … 오래됨"
                   # 빈 목록 → "경보 없음"·"지난 7일 기록 없음"·"지난 24시간 기록 없음"·"최근 24시간 실패 없음"·"예산 없음"
                   # null 섞인 점 → 선 조각 3개 / 점 1개 → "데이터 부족" / delivered false → "전송 실패" / firstTs 늦음 → "기록 시작 03:02"
                   # 예산 월·연 → 두 줄, 월만 막대 / serve swap null → "스왑 지표 없음" / 경보 1개 ALARM → 장애
                   # upbit stale → 주의 / canary 실패 → 주의 / 수집기 /health 403 → 알 수 없음 / 피드 넷 404 → 그 칸들 "응답 오류 (HTTP 404)"
cdp.py axes        # 첫 점 x 0·마지막 점 x 287(viewBox 288 — ts×1000 을 startTs..endTs 에), "24시간 전·지금"
cdp.py cadence     # 보이는 2분: 빠른 넷 각 12·느린 넷 각 2 / 숨긴 2분: 0 / 다시 보임 2초 안: 빠른 넷 각 1(느린 넷은 120초 지나 각 1)
cdp.py flip        # 5초 뒤 3초 숨김 → 보임: 빠른 넷만 곧바로
cdp.py slowpath    # /admin/aws 30초 지연 70초: 빠른 넷 각 7·느린 넷 각 2
cdp.py session     # /admin/clarity 만 401 text/html → /?relogin=1 로 한 번, 그 뒤 65초 같은 문서·알림만 → 전부 정상으로 돌리면
                   #   62초 안에 표시 지움(주소 /) · 틀린 토큰 "401 토큰 오류"(새로고침 없음) · test-token 200·저장 1234 · 브라우저 저장소 0
cdp.py keyboard    # Tab: 절 이동 일곱 → 로그아웃 → 개요 칸 여섯 → 펼침·필터 → 알림 목록 → 도구 링크, 초점 2px 외곽선 /
                   #   Enter 절 이동·필터(aria-pressed) / Space 로 연 '정상 17개' 가 다시 그려도 열림
# Claude 브라우저 창: 창이 숨은 동안 document.visibilityState hidden → 요청 0(css·js 두 개뿐), 밝은 모드 에뮬레이션에서도 바탕 #161826
docker rm -f ml036-web ml036-server ml036-api && docker network rm ml036-net && docker rmi ml036-web   # 036 자원 0건

# 검토 반영(같은 날) — 망 ml036fix-net, 가짜 백엔드 둘 + nginx:1.27-alpine 에 nginx-admin.conf 템플릿·web/admin 을 붙여 127.0.0.1:18036
#   (커밋마다 git checkout-index 트리를 :18037 에 따로 띄워 같은 확인), 헤드리스 Chrome CDP <scratch>/036/fix/chk.py
docker exec ml036fix-web nginx -t   # test is successful
chk.py scroll       # 알림 목록 scrollTop 250·초점 → 빠른 묶음 뒤 같은 요소·250·초점 그대로, 느린 묶음 뒤에도 250 / 표·title 노드 유지
chk.py keyboard     # Tab 으로 목록에 들어가 ↓ 여섯 번 → scrollTop 240, 빠른 묶음 뒤 초점·240 그대로
chk.py heads        # 알림 머리 'Slack 1초 전 값 · 경보 이력 ▲30분 전 값 · 오래됨'(alarms 30분 전) / Clarity '3시간 간격' / 스왑 줄은 data 값·serve 만 '없음'
                    #   / 예산 점선 x = 한도×경과 자리 / 상위 표·상태 코드 비율 막대 26+4개 모두 title
chk.py verdicts     # /health/collect 502 → 정상 '거래소 상태 모름 · …' / status 502 → 'Redis·Influx 상태 모름' / api starting → 띠·칸 모두 ✕
                    #   / ALARM 1개 → '경보 ALARM collect-memory' / 피드 넷 404·AWS 전부 unconfigured → 인프라 카드 하나로 접힘, 요약·칸 ▲·○
chk.py xss          # 예산 unit 에 <img onerror>·javascript:·U+202E → title 밖 속성 0, img 0, 대화상자 0, CSP 0 (1280·360)
chk.py layout       # 1440·1280·768·375·360 × 정상·장애 섞음: 가로 넘침 0, 예산 이름 306~970px 폭·한 줄, 경보 행 카드 안(375 에서 321px),
                    #   열린 실패 구간 열이 375 첫 화면 안, 인프라는 경보가 박스보다 위, 상위 표 행 36px, 박스 카드 축 1개·머리 배지
chk.py contrast     # 배지 주의 5.48·장애 6.17·정상 5.0, 축·code·흐림 5.25 이상
chk.py charts       # 타임라인 눈금 4개+지금·글자 목록·높이 12/6, 시간대별 '최고 n/시간'·5xx 시간, 지속 시간 수, '$1,200.00'·'연', 표기 통일
chk.py transitions  # 한 화면에서 정상 → AWS 연결 안 됨·접속 error·빈 알림 → 섞임 → 정상: 본문이 매번 따라 바뀜
chk.py session      # 느린 경로 하나 401 비JSON → /?relogin=1 한 번·65초 같은 문서 → 정상으로 돌리면 62초 안에 지움(주소 /)
chk.py cadence      # 70초: 빠른 넷 각 7·느린 넷 각 2
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q   # All checks passed! · 286 files · 1236 passed
cd web && npm run lint && npm run build   # oxlint 종료 코드 0 · ✓ built · dist 에 admin* 0개
docker rm -f ml036fix-web ml036fix-web1 ml036fix-server ml036fix-api && docker network rm ml036fix-net   # 036 자원 0건

# 후속(2026-10-02, 배포 뒤 운영 확인 반영) — 망 fu036-net, 가짜 백엔드 둘(python:3.12-alpine + <scratch>/followup/fake.py —
#   036/fix/fake.py 에 FREEZE(시계 멈춤 — 응답 글자가 매번 같아 본문을 다시 그리지 않는다)), nginx:1.27-alpine 에 nginx-admin.conf
#   템플릿·web/admin 을 붙여 127.0.0.1:19041(고친 것)·19042(후속 전 507e06d 의 화면·설정), 헤드리스 Chrome CDP <scratch>/followup/chk.py
docker exec fu036-web nginx -t   # syntax is ok · test is successful
chk.py elapsed   # 75초 뒤: 경보 ALARM 행 '5분 전'→'6분 전'·canary 카드 '3분 전'→'4분 전' = 개요 칸·절 요약, 같은 요소·details 열림·초점 그대로,
                 #   span[data-at] 18 / 후속 전: 본문 '6분 전'·'4분 전' 그대로인데 개요 칸은 '5분 전'
chk.py wrap      # 한글 음절 중간 줄바꿈 360·375·768·1280 = 후속 전 6·2·0·0('5분 최댓|값' 등) → 0·0·0·0, 가로 넘침 0(XSS·장애 섞은 값도)
chk.py favicon   # /favicon.ico 후속 전 404·콘솔 자원 오류 한 줄 → 204·콘솔 0 · curl: 204·X-Frame-Options DENY, error 로그·접속 기록 줄 0
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q   # All checks passed! · 286 files · 1241 passed
cd web && npm run lint && npm run build   # oxlint 종료 코드 0 · ✓ built · dist 에 admin* 0개
docker rm -f fu036-web fu036-web-old fu036-server fu036-api && docker network rm fu036-net   # fu036 자원 0건

# 검토 반영(같은 날) — 같은 구성(망 fu036apply-net, FREEZE, 127.0.0.1:19044 고친 것·19045 직전 커밋의 화면), <scratch>/followup/apply/sel.py
sel.py   # 경보 행 '4분 전' 글자 선택 → 빠른 묶음 뒤: 전 '' 로 풀림·텍스트 노드 바뀜 / 후 그대로·같은 노드, 행을 끌어 경과 글자 가운데까지 고른 것도 유지
         #   65초 뒤 분이 바뀌면 글자는 따라 바뀐다('6분 전'→'7분 전'·canary 본문 = 개요 칸) · CSP·Uncaught 0
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q   # All checks passed! · 286 files · 1248 passed
cd web && npm run lint && npm run build   # oxlint 종료 코드 0 · ✓ built · dist 에 admin* 0개 · node --check web/admin/admin.js 종료 코드 0
docker rm -f fu036apply-web fu036apply-web-old fu036apply-server fu036apply-api && docker network rm fu036apply-net   # fu036apply 자원 0건
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행 web 칸을 "관리자 화면 v2(web/admin — 한 페이지 개요·수집·인프라·알림·접속·비용·도구, 빠른 10초·느린 60초 보이는 동안만, SVG 직접, 빌드 없음, 피드 부분별 상태)" 로, 비고에 "036 운영 확인 대기". 알려진 빚에 `(036) AWS 계정 종료(2026-12) 뒤 인프라·비용 절과 AWS 링크는 '연결 안 됨' — 이전이 정해지면 화면을 고친다`, `(036) Clarity 칸은 traffic 타일과 받은 지표 목록뿐 — 정규화 타일은 035 첫 응답 뒤`, `(036) 화면 브라우저 확인은 Chromium 한 종류`.
- `CLAUDE.md` — 스펙 인덱스 036 행 상태 → DONE. §2 `web/admin/` 설명 끝을 "(029·036)".
- `docs/context/architecture.md` — "현재 구조" admin 항목의 `web/admin/` 설명에 "화면 v2(036) — 빠른·느린 두 폴링 묶음, 만료 표시는 여덟 경로가 모두 성공한 뒤 지움, SVG 차트를 DOM 으로 직접, 피드의 부분별 상태 표시".
- `docs/context/product.md` — 기능 목록 admin 행을 "관리자 페이지(운영자용 — 헬스·수집 상태·CloudWatch 경보·지표·canary·알림 기록·접속 요약·Clarity·비용·닫힌 API)" 로.
- `docs/specs/029-admin.md` — §2 하지 않는 것의 "CloudWatch 링크(027 배포 뒤 따로)" 를 지운다. §3.3 을 "화면이 보이는 것·주기·차트·보안 계약은 036(§3.8 이 이 절의 규칙을 이어받는다)" 한 줄과 읽는 계약 셋으로 줄인다. §4 의 화면 정적 단언 줄 끝에 "(036 §4 가 넓힌다)".

담당자에게 제안: 없다 — 011·025 의 계약을 읽기만 한다.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `web/admin/index.html`·`admin.css`·`admin.js`(전면 재작성 — 한 파일, 절 순서 공통 도구 → 요청·세션·주기 → 부분 상태·차트 → 개요·수집 → 인프라 → 알림·접속 → 비용·그리기·시작), `server/tests/test_admin.py`(화면 정적 단언 넷 → 일곱). 문서: 이 스펙, `029-admin.md`(§2·§3.3·§4), `docs/context/status.md`·`architecture.md`·`product.md`, `CLAUDE.md`(인덱스·§2).
- 화면 사진: §4 의 세 장(1280·768·360, 정상 데이터, 검토 반영 뒤 다시 찍음)은 **PR 본문에 첨부**한다 — 레포 밖 파일이라 이 문서에 경로를 두지 않는다(아래 'PR 본문에 옮길 것').
- 디자인: 맨 위 종합 띠(정상·주의·장애·알 수 없음 — 색 + 모양 ●▲✕? + 글자, 사유 셋, 오른쪽에 판정 재료 일곱 수집기·api·Redis·Influx·거래소·경보·canary) → 그 아래 개요 칸 여섯 → 절마다 머리 줄 요약(모양 + 글자) → 카드. 자세한 것(즉시 갱신·정상 경보·최근 5xx·Clarity 받은 지표)은 HTML 의 `details` 라 다시 그려도 펼침이 남는다. theme.css 토큰(어두운 바탕·카드·태그·표·버튼·입력) + 시스템 글꼴·tabular-nums, 차트 선은 accent-400·기준선은 장애색 점선.
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - 밝은 테마: §2·§3.7 대로 어두운 테마 하나 — theme.css 에 밝은 토큰이 없다. 밝은 모드 브라우저에서도 같은 어두운 화면(`color-scheme: dark`).
  - 응답 상태: 200(헬스 둘은 503 도)이 아니면 본문이 JSON 이어도 "응답 오류 (HTTP n)" — 034·035 전 배포의 FastAPI 404 `{"detail"}` 를 값으로 그리지 않게(§3.9).
  - 판정 재료 일곱(띠 오른쪽)과 절 요약 줄은 화면 장식이 아니라 §3.4 판정과 같은 입력을 칸마다 보인 것 — 판정 밖(연결 안 됨·권한 없음·호출 실패)은 흐림. 절 요약·개요 칸의 판정 밖 호출 실패·`error` 는 ▲(§3.7), 헬스 칸은 판정과 같게 ok 밖 ✕·호출 실패 ?. 칸 머리 배지는 §3.5 대로(`error` 장애색). 5xx 는 §3.4 가 막대 겹침을 장애색으로 정해 수치·표·절 요약의 5xx 조각도 장애색(판정 밖이라 절 요약 줄 전체는 칠하지 않는다).
  - 개요 칸 값 자리의 상태 글("불러오는 중"·"첫 조회 중"·"연결 안 됨"·"호출 실패")은 작은 글자. api 칸은 헬스가 ok 여도 Redis·Influx 중 하나가 ok 가 아니면 "저장소 끊김".
  - 값이 없는 `error` 는 본문을 비우고 머리 배지("불러오지 못함" + code)만. HTTP 수준 실패는 본문에 "사유 — 이 칸의 값은 비웠다".
  - 스왑: 값이 있는 박스만 줄을 두고, null 이면 serve 만 "스왑 지표 없음"(collect 는 스왑이 없다 — 027). 크레딧 선은 data·serve 만(임계 173·86). CPU 는 색 없음.
  - 차트: 선 viewBox 288×48·`preserveAspectRatio none`, 개수·ms·크레딧 세로 상한은 최댓값(기준선 포함)×1.1, 앞뒤가 빈 점 하나는 길이 0 선(둥근 끝 = 점). 시간대별 막대의 5xx 는 같은 눈금으로 겹치고 최소 높이 1.5. 실패 구간은 창 앞에서 시작한 구간을 창 시작에 자르고(title 은 실제 시각), 최소 폭 1000 중 4.
  - aria-label 에는 서버·방문자 글을 싣지 않는다(§3.8 — title 밖 속성 금지): 박스 이름은 아는 셋만, 표의 비율 막대·예산 막대는 이름 없이, 예산 금액은 단위 없이(USD 만 `$`). 서버 값으로 표를 찾을 때 `Object.hasOwn`(`constructor` 같은 이름).
  - 상위 표 여섯의 비율 = 그 행 수 ÷ `totals.pages`(여섯 모두 페이지 요청만 센다). 경로·출처 이름은 60자, 최근 5xx 경로 120자, Slack 글 300자, 경보·이력 사유 160자, Clarity 지표 이름 120자에서 자르고 전체는 title.
  - 알림: 경보 행은 사유를 둘째 줄(흐림)로, Slack 행은 `key` 를 행 title 로. 출처 배지는 `role`(collector·api) 또는 "경보". 이전·새 상태는 경보 표와 같은 이름(ALARM·데이터 부족·OK). 절대 시각은 화면 전체가 한 꼴(오늘 HH:mm:ss, 아니면 MM-DD HH:mm), 금액은 `$1,200.00`, 예산 주기는 월·분기·연.
  - 예산 막대 눈금 = max(한도×1.15, 실제, 예측), 이번 달 지난 비율 점선은 한도 × 비율 자리(UTC 달 — AWS 예산의 달), 예측 표시선은 두지 않고 글자("▲ 예측 … 한도 넘음"). 개요 비용 칸의 색은 실제/한도 ≥1 장애·≥0.85 또는 예측 > 한도 주의이고, 아래 글이 그 이유를 말한다("예측 $141.20 · 한도 $130.00 넘음"). 지금 접속 칸은 상태 없는 수라 모양 없이.
  - 같은 카드에 '지금' 값이 따로 있는 CloudWatch 계열(실시간 카드의 `wsClients`, canary 카드의 실행 시간)은 굵은 값을 24시간 최고로, 마지막 5분 구간 값은 작게.
  - "기록 시작 HH:mm" 은 글자 그대로 `firstTs > startTs` 일 때 — 창 시작 몇 초 뒤의 첫 줄에도 뜬다.
  - 머리의 "마지막 갱신" 글자는 640px 이하에서 빼고 시각만(머리를 두 줄로). 마지막 갱신 = 어느 묶음이든 끝난 시각.
  - canary 로그 줄은 펼침 없이 카드에(최대 10줄), 즉시 갱신은 접힌 `details` 안.
- 검토 반영(2026-10-01, 같은 브랜치): 빠른 묶음마다 모든 본문을 새로 만들어 알림 목록 안 스크롤·초점이 10초마다 날아가던 것을 고쳤다 — 본문은 그 칸의 값이 바뀔 때만(§3.3), 같은 응답 글자면 직전 값 객체를 그대로 둔다, 알림 목록은 HTML 고정 `ul`(`tabindex=0`)에 자식만 바꾸고 스크롤을 되살린다. 알림 머리의 출처 둘에 경과·오래됨, 예산 점선 위치(한도 기준), 비율 막대 title, Clarity 간격을 `refreshSec` 로, 판정 사유의 "상태 모름"·경보 이름, 헬스 칸 색, SVG 속성 허용 목록·정적 단언 보강, 좁은 폭의 예산 이름·경보 사유 칸, 대비, 인프라 순서·접기, 타임라인 모양·눈금·글자 목록, 거래소 열 순서, 표기 통일. §3.3·§3.4·§3.6·§3.7·§4 문구를 함께 고쳤다.
- 실행 중 함께 고친 스펙 절: §3.2 canary — 끝난 실행이 없으면 `durationMs`·`ok` 도 null(034 코드·§7 이 진실). §3.8 표시 지우기 — "마지막 만료 신호(없으면 화면을 연 때) 뒤 여덟 경로가 모두 만료 신호 없이 끝났을 때만" 으로 문구를 좁혔다(§4 의 "전부 정상으로 돌리면 표시 지움" 과 같은 문서 안에서 맞도록 — 만료 신호마다 센 것을 처음부터). §6 대로 029 §2·§3.3·§4.
- 배포 뒤 운영 확인(2026-10-02, 설계 세션 — 반박 검증까지): 관리자 여덟 경로 모두 200·계약 모양, `/admin/access` 를 caddy 로그로 따로 세어 정확히 일치, 배포된 화면 파일 sha256 이 main 과 같음, 관리자 접속 기록에 60초 폴링 경로 없음, 공개 쪽 `/api/admin/*` 404, 교차 사이트 403. collect 카드에 스왑 0% 줄이 보인다(피드가 collect 스왑을 0.0 시계열로 준다 — 034 §7).
- 후속 PR(같은 날, 운영 확인에서 찾은 것): ① 차트 빈 칸 문구를 "값 없음"·"값 1개뿐" 으로(§3.5 — 경보 "데이터 부족" 과 겹침). ② 본문 안 경과 글자가 그린 때에 멈춰 개요 칸과 달랐다 — 시각을 `data-at` 에 둔 span 으로 만들고 그리기 끝에 글자만 고친다(§3.3·§4). ③ 360 폭에서 한국어가 음절 중간에서 줄바꿈 — `body` 에 `word-break: keep-all`(§3.7·§4). ④ 브라우저의 `/favicon.ico` 가 관리자 server 에서 404·error 로그·접속 기록 줄 — 204·기록 끔(029 §3.1·§3.2). ⑤ Clarity 지표 이름이 실제로는 CamelCase(`ReferrerUrl`)라 출처 줄이기가 빠졌다 — 정규화 비교(035 §3.3). 화면은 그대로다.
- 후속 PR 검토 반영: 경과 글자를 묶음마다 같은 글자로 다시 써 그 안의 글자 선택이 10초마다 풀렸다 — 글자가 다를 때만 쓴다(§3.3·§4). 배포 뒤 확인에서 남은 사람 몫 셋을 '남은 빚'·status 비고에 이름으로 적었다.
- 남은 빚:
  - 배포 뒤 사람 확인(2026-10-02, 위)에서 남은 셋(status 비고): 도구 링크 중 CloudWatch 지표(`#metricsV2:graph=~();namespace=MarketLens`)·로그 그룹(`$252F` 인코딩) 주소 꼴을 콘솔에서 확인한 기록이 없다 / 실제 휴대폰의 가로 스크롤 — 360 폭은 헤드리스 Chrome 으로만 봤다 / 경보 수·canary 로그 줄을 콘솔과 맞춘 기록이 없다.
  - 브라우저 확인은 Chromium(헤드리스 Chrome·Claude 브라우저 창) 한 종류(status 빚). 숨은 탭은 CDP 에서 `visibilityState` 를 바꿔 흉내 냈고, 실제 숨은 창(Claude 브라우저 창)에서도 요청 0 을 봤다.
  - Clarity 정규화 타일·AWS 이전 뒤 화면은 status 빚 그대로.
- PR 본문에 옮길 것:
  - §4 의 화면 사진 세 장(1280·768·360) 첨부 — 검토 반영 뒤 찍은 것.
  - 후속 PR: 360 폭 canary 카드 전·후 사진 두 장(줄바꿈 — '5분 최댓|값' → '5분 / 최댓값' — 사진은 PR 에 올리고 로컬 경로는 적지 않는다), 관리자 nginx 의 로컬 `nginx -t` 결과(029 규칙).
  - 경보 상태 `INSUFFICIENT_DATA` 와 차트 빈 칸이 같은 낱말 "데이터 부족" 을 쓰던 것: 설계 세션이 정하고 사용자에게 알렸다(2026-10-02, 후속 PR) — 경보 쪽은 AWS 한국어 문서·콘솔과 같은 "데이터 부족" 그대로, 차트 쪽을 "값 없음"·"값 1개뿐" 으로 바꿨다(§3.5).
