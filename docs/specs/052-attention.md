# 052 — attention

상태: TODO | 의존: **051 privacy-v3**(상수 `PRIVACY_V3_EFFECTIVE`·동의 안내 판 D). 053 admin-attention 이 이 스펙의 관리자 피드와 `attention.js` 를 쓴다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
화면 분석에 동의한 방문자의 브라우저가 화면마다 **영역별로 화면에 보인 시간·1초 이상 보였는지·클릭 수**를 세어 서버로 보내고, 서버는 하루(KST) 합계로만 90일 둔다. 관리자는 기간을 골라 그 합계를 받는다(화면은 053). Clarity 는 이런 값을 API 로 주지 않아(히트맵은 Clarity 대시보드의 손 내려받기뿐) 우리가 직접 센다.

## 2. 범위
- 만드는 것: 정적 스크립트 `web/public/attention.js`(번들 밖 한 파일, 세기·보내기 — 053 이 같은 파일에 덮어 보기 모드를 더한다), 모든 화면의 영역 표시 `data-area`, App.tsx 의 `kt:tab` 이벤트 한 줄, api 기능 폴더 `attention`(받기 `POST /attention`·관리자 피드 `GET /admin/attention`), `RedisBus` 공개 메서드 둘, 공개 nginx(허용 경로 하나·스크립트 location·정적 페이지 CSP), 관리자 nginx 경로 하나, caddy 로그 제외 하나, 테스트.
- 하지 않는 것: 관리자 화면(053). 마우스 위치·좌표·스크롤 경로·글자·입력값(보내지 않는다). 동의 판정 바꾸기(033 그대로 — 지금 판의 `granted` 일 때만). 방문자 식별(쿠키·저장소·ID 없음). 038 의 JS 신호 경로(`JS_PATHS`)에 attention.js 를 넣는 것(방문자 세기 뜻이 바뀐다).
- 바꾸는 기존 것: 028 허용 목록(공개 `/api` 경로 하나), 029 관리자 경로(정확 일치 하나), 027 caddy 로그 제외(경로 하나), 032·044 정적 페이지 규칙('스크립트는 인라인만' → `/attention.js` 하나는 예외, CSP 에 `'self'`), 002·022·044·032 화면에 `data-area` 속성(동작 변화 없음). 028·027·029·032 는 이 레포 주인 담당, 002·022·044 와 탭 기능(017·048·049·050 등 hereokay·팀원)은 속성만 붙이고 §6 에서 담당자에게 알린다.

## 3. 동작

### 3.1 화면과 영역
- **화면 이름**(서버 상수 하나 — 이 목록 밖은 받지 않는다): `landing`(`/`), `app-spread`·`app-history`·`app-gap`·`app-pp`·`app-health`·`app-flow`(경로가 `/app/` 로 시작 — SPA 가 받는 모든 경로. 탭은 `?tab=` 을 App 과 같은 규칙으로 읽는다: 없거나 목록 밖이면 spread), `privacy`(`/privacy`), `kimp-chart`, `kimp-history`. 탭 id 는 `web/src/App.tsx` 의 `TabId` 와 같아야 한다(테스트가 묶는다).
- **영역** = `data-area="<id>"` 를 단 요소. id 는 `^[a-z][a-z0-9-]{0,31}$`, 한 화면 안에서 겹치지 않고 서로 품지 않는다(영역 안에 영역 없음). 한 화면 영역 수 ≤ 40. `display: contents` 요소(App.tsx 의 탭 자리 Pane 등)에는 달지 않는다 — 상자가 없어 늘 안 보인다.
- 정하는 규칙(실행 세션이 실제 DOM 을 보고 붙이고, 붙인 목록을 §7 에 화면별로 적는다 — 053 의 이름표가 이 목록을 쓴다):
  - 랜딩: `top`(머리 막대), `hero`(첫 화면 글·실시간 카드), 절마다 그 `<section id>`(`analyze`·`events`·`kimp`·`method`·`faq`), `foot`.
  - 정적 페이지 셋: `top`, 본문 절마다(처리방침은 `changes`·`glance`·`consent`·`s1`~`s12`, 검색어 페이지는 h2 절 — id 가 없으면 붙인다), `foot`.
  - 대시보드: 모든 탭 공통 `header`(로고·상태 덩어리만 — 탭 단추와 같은 줄이라 줄 전체에 달면 품게 된다)·`tabs`(탭 단추 묶음)·`kpi`(공통 KPI 줄), 탭마다 2~6개 — 사용자가 따로 보고 누르는 덩어리(필터 줄·표·차트·옆 목록·카드 묶음·요약 줄). 탭 영역 id 낱말은 `filters`·`table`·`chart`·`list`·`cards`·`summary`·`detail`·`legend` 에서 고르고 같은 낱말이 둘이면 `-2`. 표의 행·코인마다 영역을 두지 않는다(매초 재정렬).

### 3.2 브라우저에서 세기 (`attention.js`)
- 켜지는 조건(모두): 호스트가 `kimptrack.com`, 틀 안이 아님(`window.top === window` — 틀 안이면 053 덮어 보기 말고는 아무것도 하지 않는다), 주소에 `kt-overlay=1` 없음, 지금 화면이 §3.1 목록 안, 동의 상태 '켬'(`localStorage` `kt.analytics` = `granted` 이고 `kt.analytics.v` = 지금 판 D, GPC 없음 — clarity.js 와 같은 판정, `NOTICE_VERSION` 값을 복사하고 test_clarity 가 묶는다). 저장소를 읽다 예외면 끔.
- 동의 상태만 '켬' 이 아니면 리스너 둘만 단다 — 같은 탭의 띠 저장(`kt:clarity` 이벤트)과 다른 탭의 저장(`storage`). 그때 다시 판정해 '켬' 이면 그 순간부터 센다. 나머지 조건이 아니면 아무것도 하지 않는다.
- 싣는 곳: 랜딩·대시보드는 clarity.js 바로 다음 `<script defer src="/attention.js">`(대시보드는 vite base 로 `/app/attention.js` 가 된다 — clarity.js 와 같다), 처리방침·검색어 페이지 둘은 `<head>` 에 같은 줄 하나(이 셋은 clarity.js 를 싣지 않는다 — 그대로). 공개 nginx: `location = /attention.js` 를 clarity.js 의 location 과 같은 꼴(no-cache)로, `/app/attention.js` 는 기존 `location /app/` 그대로.
- 정적 페이지 CSP(032·044): 세 페이지 CSP 의 `script-src` 에 `'self'`, `connect-src 'self'`(없으면 더함 — 비콘은 connect-src 를 따른다). 053 이 같은 문자열의 `frame-ancestors` 를 바꾼다.
- **구간(한 번 본 화면)**: 페이지를 연 때(또는 동의가 켜진 때) 시작, 대시보드 탭이 바뀌면 끝내고 새 탭 이름으로 새 구간. App.tsx 는 사용자가 탭을 바꾼 뒤(첫 그리기에는 내지 않는다) `window` 에 `kt:tab` 이벤트(`detail` = 탭 id)를 낸다. 이 스크립트는 그 이벤트만 듣고, 지금과 같은 id 면 무시한다(history 를 덮어쓰지 않는다).
- **보임**: 영역마다 IntersectionObserver(문턱 0)로 화면 근처에 들어온 영역만 고르고, 그 영역들은 스크롤·크기 바뀜·DOM 바뀜 때(애니메이션 프레임당 한 번) `getBoundingClientRect` 로 다시 잰다. 보이는 높이 ≥ min(영역 높이 × 0.5, 화면 높이 × 0.5) 이고 ≥ 40px 이면 '보임'(긴 표·긴 절도 화면 절반을 채우면 보임). 늦게 생기는 영역(대시보드 lazy 탭)은 MutationObserver 로 잡는다(1초에 한 번까지).
- **보인 시간**: 보임이면서 문서가 보이는 동안(`visibilityState` visible)이면서 마지막 입력(페이지 연 때·포인터 움직임·누름·휠·스크롤·키·터치) 뒤 5분 안일 때만 쌓는다.
- **클릭**: 문서 `click`(캡처)에서 대상의 가장 가까운 `[data-area]` 하나에 1. 영역 밖 클릭은 버린다.
- 상한은 **구간 전체**(여러 번 보내도 합)에서 영역당 시간 600,000ms·클릭 50. '1초 이상 보임'(seen)은 구간에서 영역의 누적 시간이 1,000ms 를 넘은 첫 순간 1(구간당 한 번).
- **기기**: 구간 시작 때 `innerWidth < 768` 이면 `mobile`, 아니면 `pc`. 구간 중 폭이 바뀌어도 그대로.

### 3.3 보내기
- 때: 문서가 숨겨질 때(`visibilitychange` → hidden)·`pagehide`·구간 끝. 보내기 직전에 동의를 다시 판정해 '켬' 이 아니면 버리고 멈춘다(같은 탭·다른 탭 철회 모두). 보낸 뒤 보낸 몫을 0 으로(구간 합 상한·이미 보낸 seen 은 기억) 다시 보이면 같은 구간을 이어 센다. 페이지뷰(pv)는 구간의 첫 보냄에만 1.
- 몸통(JSON 글자 — sendBeacon 에 문자열을 넘겨 `text/plain`, 미리 묻기 없음): `{"v":1,"page":"app-spread","device":"pc","pv":1,"a":{"table":[ms,clicks,seen],…}}`. 값이 모두 0 인 영역은 빼고, `a` 가 비고 pv 가 0 이면 보내지 않는다. 몸통 ≤ 4,096바이트.
- 방법: `navigator.sendBeacon('/api/attention', 몸통)` — 없거나 false 면 `fetch(…, {method:'POST', keepalive:true, credentials:'omit'})` 한 번. 실패해도 다시 보내지 않는다.

### 3.4 서버가 받기 — `POST /attention` (api 역할)
- 공개 nginx `location = /api/attention`: POST 가 아니면 405(`if ($request_method != POST) { return 405; }` — location 안 `return` 만), `client_max_body_size 4k`(넘으면 nginx 기본 413 — 비콘은 응답을 읽지 않는다), `proxy_set_header X-Client-IP $http_x_forwarded_for`(caddy 에 trusted_proxies 가 없어 caddy 가 넣은 값이 방문자 IP 하나다), api 로. 접속 기록은 server 수준에서 이미 꺼져 있다. caddy 는 이 경로를 로그에서 뺀다(027 의 폴링 제외와 같은 꼴).
- 순서: ① `Sec-Fetch-Site` 가 있고 `same-origin` 이 아니면 403 ② 몸통 4,096바이트 초과 413 ③ 검사 — JSON 객체, `v`=1, `page` 가 §3.1 목록, `device` ∈ {mobile, pc}, `pv` ∈ {0,1}, `a` 객체·키 ≤ 40·키가 영역 id 꼴, 값은 정수 셋 `[ms 0..600000, clicks 0..50, seen 0|1]`. 어긋나면 400 이고 아무것도 더하지 않는다 ④ 시각이 D 00:00 KST 전이면 204·버림 ⑤ 같은 `X-Client-IP` 의 10분 고정 창 안 121번째부터 204·버림(메모리 표 — 창이 바뀌면 비움, 키 5만 넘으면 새 키는 세지 않고 받음) ⑥ 받은 날(KST)의 합계에 더함 → 204. 오류 응답은 앱 공통 오류 모양(`{"error":{code,…}}` — code `cross_site`·`too_large`·`bad_beacon`).
- IP 는 ⑤ 의 메모리 표에만 있고 로그·Redis·응답에 쓰지 않는다. `X-Client-IP` 가 없으면 ⑤ 를 건너뛴다.
- 게이트 시각 계산(D 00:00 Asia/Seoul)은 이 기능 폴더 안에 둔다 — admin 의 같은 함수를 import 하지 않는다(기능 간 import 금지).

### 3.5 저장 — Redis
- 키 `attn:d:<YYYYMMDD>`(KST 날짜) 해시 하나. 필드 `<page>|<device>|<area>|<m>` (m = `ms`·`clicks`·`seen`), 페이지뷰는 `<page>|<device>||pv`. 값은 정수 합. 만료는 `EXPIREAT` 그 KST 날의 끝 + 90일(쓸 때마다 같은 값으로) — 처리방침의 '하루 합계 90일' 과 같고, 피드의 90일 창(오늘 포함)은 모두 살아 있다.
- 쓰기: api 가 메모리에 더해 두고 10초마다 한 번 파이프라인(HINCRBY 묶음 + EXPIREAT)으로 보낸다. 실패하면 메모리에 두고 다음에 다시 — 쌓인 필드가 2만을 넘으면 그 묶음을 버리고 WARNING 한 줄. api lifespan 이 이 주기 작업을 띄우고 끌 때 한 번 보낸다(`main.py` `_api_lifespan` 과 그 설명 글을 고친다 — 지금은 'Redis 는 구독 목적으로만').
- `RedisBus` 공개 메서드(core 계약, 기존 것처럼 async): `async attention_add(day: str, counts: Mapping[str, int], expire_at: int) -> None`(한 파이프라인), `async attention_days(days: Sequence[str]) -> dict[str, dict[str, int]]`(키 없으면 빈 dict).
- 크기: 화면 10 × 기기 2 × 영역 ≈ 15 × 3 + pv ≈ 900 필드/일 — 90일 ≈ 8만 필드, 수 MB(Redis 는 noeviction).

### 3.6 관리자 피드 — `GET /admin/attention?days=1|7|30|90`
- api 역할. 관리자 nginx 에 `location = /svc/api/admin/attention` 을 `/svc/api/admin/access` 와 같은 꼴(교차 사이트 검사·`rewrite ^ /admin/attention break`·폴링 접속 기록 끔)로 더한다 — 정확 일치 목록이라 저절로 열리지 않는다. 공개 nginx 는 404 그대로(028 목록 밖).
- `days` 가 목록 밖·없음이면 7. 창: 오늘(KST)을 넣어 거꾸로 `days` 날, 시작이 D 보다 앞이면 D 로 자른다.
- 응답(200, `Cache-Control: no-store`): `{state, code, gateAt, days, from, to, fetchedAt, rows}` — `state` `ok`·`before_gate`(오늘이 D 전 — rows 빈 목록)·`error`(Redis 실패 — code `redis`), `gateAt` D 00:00 KST epoch ms, `from`·`to` `YYYY-MM-DD`, `rows` = `[{page, device, pv, areas:[{id, ms, clicks, seen}]}]` — 창 안 날을 더한 값, pv 0 인 행 없음, areas 는 ms 큰 순. 같은 `days` 결과를 60초 메모리 캐시.

### 3.7 엣지
- 철회: 보냄마다 다시 판정하므로 철회 뒤 첫 보냄부터 멈춘다(clarity.js 의 다른 탭 새로고침은 랜딩·대시보드에서 Clarity 가 켜졌을 때뿐이라 기대지 않는다).
- 봇·가짜 비콘: 동의 저장값이 없는 봇은 보내지 않는다. 손으로 만든 비콘은 상한(영역당 10분·50클릭, IP 10분 120번)까지만 더해진다 — 053 은 표본 수와 함께 본다.
- 바뀐 화면: 영역 id 를 바꾸거나 지우면 옛 id 합계는 90일 동안 남고 053 은 '지금 화면에 없음' 으로 따로 보인다.
- Redis 가 오래 죽으면: 2만 필드 넘는 묶음은 버린다 — 통계 빈칸이지 서비스 장애가 아니다.

## 4. 검증
- 서버(`features/attention/tests/`): 검사 ③ 어긋남마다 400·더한 것 없음 / 403 cross_site / 413 / D 전 204·더한 것 없음(시계 바꿔서) / 같은 IP 121번째 204·더한 것 없음, 10분 뒤 다시 받음 / X-Client-IP 없으면 제한 없음 / 10초 묶음 보냄·실패 뒤 다시·2만 넘으면 버림·끌 때 보냄(가짜 Redis) / 날짜 경계 23:59:59·00:00:00 KST 의 키와 EXPIREAT 값 / 피드 days 1·7·30·90·잘못된 값(→7)·D 로 자름·before_gate·redis 실패·ms 큰 순·60초 캐시.
- 계약: 화면 이름 목록 = `TabId` 여섯 + 정적 넷(App.tsx 를 읽어 대조), 공개 nginx 허용 목록 테스트에 `/api/attention`(POST 만·405), `location = /attention.js`, 관리자 nginx 의 `= /svc/api/admin/attention`, caddy 로그 제외에 경로, 정적 페이지 CSP 문자열(test_privacy·test_kimp_pages 의 기대값을 새 문자열로), `RedisBus` 두 메서드 시그니처.
- 웹 정적: 랜딩·대시보드는 attention.js 가 clarity.js 바로 뒤, 세 정적 페이지는 `<script src>` 가 `/attention.js` 하나뿐(나머지는 인라인 — 032·044 규칙), 정적 페이지 HTML 의 `data-area` 가 id 꼴·겹침 없음·품음 없음·≤ 40, App.tsx 가 `kt:tab` 을 낸다, `NOTICE_VERSION` 값이 clarity.js 와 같다.
- `attention.js` node 실행(가짜 window·document·IntersectionObserver·시계 — test_clarity 와 같은 방식): 다른 호스트·틀 안·`kt-overlay=1` 이면 리스너 0 / 동의 아님이면 리스너 둘, `kt:clarity` 뒤 켜짐 / 보임 규칙(작은 영역 50%·3,000px 영역이 화면 절반·40px) / 숨김·5분 무입력 동안 안 쌓임 / 구간 합 상한(두 번 보내도) / seen 한 번 / 탭 바뀜이 구간을 끊고 pv 1·같은 id 무시 / 두 번째 보냄 pv 0 / 보내기 직전 철회면 안 보냄 / 빈 구간 안 보냄 / sendBeacon false 면 fetch 한 번 / 몸통 4,096바이트 안 / `/app/xyz?tab=bad` → `app-spread`.
- 기존: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`, `node --check web/public/attention.js`.
- 설계 세션(머지 전, 로컬 Docker — 033 §4 처럼 `kimptrack.com` 을 로컬에 묶은 헤드리스 Chrome): 동의 저장값을 넣고 랜딩을 스크롤·대시보드 탭을 바꾼 뒤 탭을 숨기면 `/api/attention` 204 와 Redis 필드가 늘고, 동의를 지우면 요청 0, 처리방침·검색어 페이지에서 CSP 위반 0. 배포 뒤: D 전 비콘은 204·Redis 빈칸, D 뒤 첫 동의 방문의 필드.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — 새 행 `| attention | server: POST /attention(동의 비콘 → Redis 하루 합계 90일)·GET /admin/attention(1·7·30·90일) | web: public/attention.js(영역별 보인 시간·seen·클릭, 숨길 때 보냄)·모든 화면 data-area | D 전 버림 |`.
- `CLAUDE.md` — 스펙 인덱스 052 행 상태 → DONE.
- `docs/context/architecture.md` — '현재 구조' 에 attention 항목(api 받기·10초 묶음 쓰기·피드 캐시, 정적 스크립트, 영역 규칙), 데이터 흐름 절에 "동의한 브라우저 → `/api/attention` → api 메모리 → Redis `attn:d:*`" 한 줄, api lifespan 설명.
- `docs/context/db.md` — Redis 절에 `attn:d:<YYYYMMDD>` 키(필드 꼴·정수 합·EXPIREAT 그날 끝 + 90일·쓰는 쪽 api 10초 묶음·읽는 쪽 `/admin/attention`).
- `docs/context/product.md` — 기능 목록 admin 행에 "화면 영역 이용 통계 수집(052)".
- `docs/specs/028-api-allowlist.md` — 허용 목록에 `/api/attention`(api, POST 만, 052) 한 줄, 목록 개수 문장.
- `docs/specs/029-admin.md` — 관리자 정확 일치 경로 목록에 `/svc/api/admin/attention`(052).
- `docs/specs/027-observability.md` — 로그 제외 경로 목록에 `/api/attention`(052 — 비콘은 접속 기록에 남기지 않는다).
- `docs/specs/032-privacy.md` — §3.1 페이지 모양: '스크립트는 인라인만' 에 "`/attention.js` 하나는 예외(052)", CSP 문자열, "절마다 `data-area`(052)".
- 담당자에게 제안(PR 본문): 002·017·048·049·050(대시보드 탭)·022(랜딩)·044(검색어 페이지 — 스크립트 예외·CSP 도) — "`data-area` 속성을 붙였다(동작 변화 없음). 덩어리를 바꾸면 영역 id 도 함께, 053 이름표도."

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 붙인 영역 목록(화면마다 id):
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
- 남은 빚:
