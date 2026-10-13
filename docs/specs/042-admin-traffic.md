# 042 — admin-traffic

상태: DONE | 의존: **038·039·041** — `feat/039-access-geo` 위에 `feat/042-admin-traffic` 을 쌓고, 041 이 main 에 머지된 뒤 main 을 받는다(PR base 는 늘 main, 039·041 보다 먼저 머지하지 않는다). main 에는 fix/036-admin-followup 이 있어야 한다. 계약을 쓰는 스펙(쓰는 계약은 §3.1 에 복사했다): 038(접속 응답·창·짝 게이트), 039(`geo`·DB-IP 표시 의무), 036(화면 규칙·주기·보안), 041(절 머리 설명·이름표), 002(탭 이름), 037(시행일). 043(Clarity 덩어리)은 이 스펙이 main 에 머지된 뒤 시작한다 — 같은 세 파일을 고치므로 나란히 가지 않고, 덩어리 머리·강도 클래스는 이 스펙이 만든다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 관리자 접속 절에서 서버 기록을 24시간·7일·30일로 골라 '몇 명 → 사람인가 봇인가 → 어디서 → 언제 → 무엇을 → 문제는 없나' 를 위에서 아래로 읽는다. 덩어리마다 첫 줄에 데이터로 만든 답 문장이 있고, 숫자·그림은 그 근거다. 사람 수는 늘 확인(하한)과 브라우저 모양(상한)을 함께 보이고, 처리방침 개정 시행 전에 세지 않는 값은 '시행 뒤부터' 로 비워 둔다.

## 2. 범위
- 만드는 것: `web/admin/` 세 파일의 접속 절 서버 기록 부분 — `index.html` 의 창 줄·덩어리 여섯 뼈대·덩어리 끝 설명·DB-IP 표시 링크, `admin.js` 의 창 고르기·덩어리 그리기·답 문장, `admin.css` 의 색·배치 클래스. `server/tests/test_admin.py` 의 정적 단언.
- 하지 않는 것: 서버·nginx·응답 키(038·039). Clarity 덩어리(043 — 036 카드를 그대로 두고 자리만 서버 기록 묶음 뒤로 옮긴다). 다른 절·절 머리 설명 틀(041). 새 파일·새 경로·라이브러리. 지도·원그래프·범주 색 100% 띠. 창을 주소·브라우저 저장소에 두기. SVG 허용 목록 넓히기.
- 바꾸는 기존 것: 036 §3.2·§3.3·§3.4(접속 절 서버 기록)·§3.5·§3.6·§3.8·§4, 038 §3.6 끝 문장, 039 §3.2 링크 문장, 041 의 접속 절 '이 절 읽는 법' 가운데 서버 기록 항목, `test_admin.py` 의 `EXTERNAL_HOSTS`·`SCRIPT_BANNED`. 모두 이 레포 주인 담당이다.
- 파일 크기: `admin.js` 는 한 파일로 둔다(036 §2·test_admin 의 파일 셋). 041~043 뒤 ≈2,500~2,900줄(지금 1,364)·`admin.css` ≈850줄·`index.html` ≈700줄로 짐작한다 — 나누려면 test_admin 의 파일 셋·fetch 경로 목록과 036 §2 를 함께 고치는 별도 스펙이다.
- 화면 지침: 2026-10-02 시안 심사에서 이긴 '운영자 질문' 시안에 휴대폰 배치·확인/모양 표기를 접붙였다. 시안에만 있던 것(주소로 창 고르기·상태 견본 덩어리)은 가져오지 않는다.

## 3. 동작

> 이 화면은 064·065 가 대신한다 — 아래 피드 계약만 지금도 맞다.

### 3.1 읽는 값 (038·039 복사)
- 요청: `/svc/api/admin/access?window=24h|7d|30d`(관리자 nginx 가 쿼리를 넘긴다). 인자 없음·목록 밖·지금 고를 수 없는 창이면 서버가 24시간으로 답한다(오류 없음). 부분 하나 `{state, code, fetchedAt, refreshSec(60), …}` — 036 §3.2 공통 규칙.
- 늘 실림(상태 무관): `window`(실제로 답한 창), `windows`(지금 고를 수 있는 창 — 시행 전 `["24h"]`, 뒤 `["24h","7d","30d"]`), `gateAt`(ms — `PRIVACY_V2_EFFECTIVE` 00:00 KST).
- 줄 단위 값(시행 전에도 있다): `startTs`(7d·30d 는 max(창 시작, 게이트))·`endTs`·`firstTs`(초), `totals{requests, pages, humanPages, jsViews, probes, ws, skipped}`, `hourly[{ts, requests, pages, humanPages, jsViews, errors, wsErrors}]`(시마다 ≤720, `errors` 는 `/api/ws/spreads` 밖 5xx), `status{"2xx","3xx","4xx","5xx","ws5xx"}`, `recent5xx[{ts, path, status}]`(WS 뺌), `ws{count, errors, durations{lt10s, lt1m, lt10m, lt1h, ge1h}}`, `classes`(여덟 `{requests, pages}`), 상위 목록 `paths`·`tabs`·`referrers`·`utmSources`·`devices`·`browsers`(`[[이름, 수]]` — 사람 브라우저 모양 페이지 줄만).
- 짝 단위 값(시행일 KST 날의 줄부터만 — 짝 게이트): `visitors` 하위 부분 `{state, code, sinceTs, confirmed, shaped, returning, capped, days[{ts, confirmed, shaped, returning}], channels, devices, os, browsers, inApp}`(목록은 `[[이름, confirmed, shaped]]`, shaped 내림차순, 0 행 없음)와 `ws.pairs`. 시행 전에는 `visitors` 가 `unconfigured`·`before_gate`(값 null)이고 `ws.pairs` null. 뒤에는 `sinceTs` = max(`startTs`, 게이트 초) — 24시간 창이 시행 전 시간을 품어도 짝 값은 그때부터 센 것이다.
- `geo` 하위 부분(039) `{state, code, month, loadedAt, sinceTs, countries, networks, ipv6}` — `countries` `[[ISO 두 글자, c, s]]` 20행 + `["(기타)", c, s]`(작은 수는 서버가 합친다), `networks` `telecom_kr`·`telecom`·`cloud`·`other`·`unknown`(KR 이 `(기타)` 에 합쳐진 창에서는 `telecom_kr` 이 `telecom` 에 든다). 시행 전 `unconfigured`·`before_gate`, 받는 중 `pending`, 판 없음 `error`(`http_<n>`·`timeout`·`bad_data`). 접속 부분이 ok 가 아니면 `visitors`·`geo` 는 null.
- 낱말: 방문자 수는 KST 하루마다 따로 센 (가린 IP, 브라우저 정보) 짝의 합이다. 화면은 이것을 **'날마다 센 방문자'**(줄여 '방문자(날마다 셈)')라 부르고 '방문자-일' 은 쓰지 않는다. 응답 키 이름은 그대로다.

## 4. 검증
> 아래의 화면 확인(정적 단언·node·브라우저)은 064(화면 분석은 065)가 대신한다 — 이 스펙을 구현할 때의 기록으로만 남긴다.

**PR 안 — 실행 세션(완료 조건)**. 시작 전에 039 브랜치 위이고 041 이 main 에 있는지, 038 이 짝 게이트(시행 전 `visitors` `before_gate`·`sinceTs`) 판인지 본다 — 아니면 멈추고 묻는다. 서버·외부 호출은 없다.
- 정적 단언(`server/tests/test_admin.py` — 네트워크 없음, 기존 단언은 고치지 않고 통과한다. 단 038 이 더한 상위 표 분모 단언은 이 화면에서 사라지는 표를 보므로 아래 '분모' 단언으로 바꾸고, 041 node 하네스의 서버 기록 타일·상위 표 '탭' 단언(지워지는 칸)은 ①·⑥ 타일 일곱·⑤ 대시보드 진입 탭으로 옮긴다 — 하네스 응답에 `gateAt`·`windows` 를 싣는다):
  - 파일 셋: `web/admin` 은 그대로 `admin.css`·`admin.js`·`index.html` 셋.
  - fetch 경로: `fetch(` 1회, 경로 여덟 글자 그대로(`'/svc/api/admin/access'` 포함). `?window=` 는 `admin.js` 에 1회(창을 붙이는 곳 한 곳)이고 창 글자는 `24h`·`7d`·`30d` 셋뿐.
  - `SCRIPT_BANNED` 에 `location.hash`·`pushState`·`location.assign`·`location.search =`·`location =` 를 더하고, `location.search` 는 만료 표시를 보는 두 곳뿐이다(창을 주소에 싣지도 주소에서 읽지도 않는다). 이름표 일곱이 §3.6 과 같고 키가 모두 접속 절 '이 칸 뜻' dl 에 있다.
  - SVG: `SVG_ATTRS` 집합 그대로(열넷), 글자 그대로 부르는 요소 ⊆ svg·g·rect·line·path·title.
  - 외부 링크: `EXTERNAL_HOSTS` 에 `db-ip.com`. `index.html` 에 href `https://db-ip.com`·`rel="noreferrer"`·글자 `IP Geolocation by DB-IP` 인 링크가 정확히 하나.
  - `index.html`: 창 버튼 `type="button"` 셋이 `data-window` 24h·7d·30d 순이고 7d·30d 는 `disabled` / `#traffic` 안 순서 — 041 설명 → 실시간(`b-ws24`) → 창 줄(`window-bar`) → `a-q1`…`a-q6` → Clarity 카드(036 의 `m-clarity`) / `a-qN` 과 그다음 덩어리(⑥ 은 Clarity 카드) 사이에 `b-qN` 하나와 `<details class="terms">` 하나 / 041 단언(절 설명 하나·terms 꼴·id 가진 조상은 section 뿐·admin.js 에 `explain`·`terms` 없음)이 그대로 통과.
  - 분모: 경로·외부 출처·utm 의 % 를 `totals.humanPages` 로 나눈다(단언 글자 꼴은 실행 세션이 정하고 §7 에 적는다).
  - 낱말: `web/admin/*` 에 '방문자-일'·'visitor-day' 없음. `admin.js` 에 '날마다 센 방문자'·'오늘·어제(KST)를 따로 세어 더한 수' 가 있다.
  - `admin.css`: 확인·모양·다시 온·봇 클래스가 §3.7 토큰을 쓰고 강도 클래스 다섯이 차례로 accent 700·600·500·400·200, ⑥ 막대 neutral-600·시행 전 테두리 / `@media (max-width: 480px)`·`@container (max-width: 340px)` 두 줄 행, 960px 의 3열·범위 타일, geo 배지·범위 숫자 줄바꿈, 답 줄 묶음.
- node 논리 확인(pytest 안 — 033 `test_clarity.py` 처럼 node 로 admin.js 를 가짜 `window`·`document`·`fetch`·시계와 싣고, 그리기가 아닌 글자 만들기·부르기 판단만 본다. node 가 없으면 CI 에서는 실패):
  - 답 문장: 틀마다 가짜 값 하나로 글자 그대로(예: 30일 창·`endTs` − `startTs` = 6 × 86,400·confirmed 60·shaped 600·returning 15 → '하루 평균 10.0명'·'100.0명'·'확인의 25%') / '대부분 봇' 경계 — confirmed = shaped × 0.1 이면 붙고 +1 이면 안 붙음 / 분모 0 조각이 빠짐 / 5xx 0·ws5xx 3 → '없었고'·'(배포 때 몇 건은 정상)'.
  - 창 부르기: 7일을 누르면 접속 경로만 1회 곧바로(`?window=7d`)·다른 일곱 경로 0회 / 접속 응답을 5초 늦춘 채 24h→7d→30d 를 빠르게 누름 → 겹친 호출 0·끝에 30d 한 번·7d 응답은 그리지 않음 / 7d 요청에 서버가 24h 로 답함 → 고른 창이 24h.
  - 열지도 강도(⌈값 ÷ 최댓값 × 5⌉): 0 → h0, 최댓값 → h5, 최댓값 100 에서 20 → h1·21 → h2.
  - 그리기(부모 하나인 가짜 DOM — 다른 곳에 붙이면 앞 부모에서 빠진다): geo pending·지난달 판 → 나라·망 두 칸 모두 같은 글, 지난달 판 cloud 단서 '그중 확인 c · ▲…' / 나라 30행 → 21행 / 24시간 마지막 칸 20시 → 눈금 0·6·12시·지금 / 응답 창 `constructor` → 24시간으로 그림 / 답 ⑤ 61자 넘는 경로 → 60자 + '…'·b title 전체 / 값과 뒤 글자 묶음 span. 가짜 DOM 이 지나치게 커지는 항목은 아래 설계 세션 확인으로 넘기고 §7 에 적는다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`(dist 에 admin 없음), `node --check web/admin/admin.js`.
- 커밋(각 300줄 이하): ① index.html 뼈대·설명 ② admin.css ③ admin.js 창·부르기 ④ admin.js 덩어리(나눠서) ⑤ 정적 단언·node 논리 확인 ⑥ §6 문서·§5·§7.

**PR 안 — 설계 세션 확인(머지 전 — 실행 세션의 샌드박스는 Docker·헤드리스 Chrome 을 띄울 수 없다. 결과와 돌린 세션을 §5 에 적고, 어긋나면 머지 전에 고친다)**: 036 §5 방법 — 127.0.0.1 에만 게시한 관리자 nginx + `web/admin`, 가짜 백엔드가 038·039 응답 모양으로 창·시행일·부분 상태를 바꿔 준다, 헤드리스 Chrome CDP 또는 Browser pane.
  - 시행 전: 7일·30일 disabled·안내 글 / §3.8 짝 단위 칸 전부 시행일 빈 상태, 줄 단위 칸(종류 여덟·시간 막대·응답 표·상위 목록)은 값 / ①·③·④ 답이 `전` 틀 글자 그대로.
  - 창: 60초 뒤 느린 묶음도 `?window=7d`, 새로고침하면 24시간 / 서버가 24h 로 답하면 버튼이 24시간 / 화면을 연 채 가짜 시계를 시행일 뒤로 → 다음 응답에 버튼이 열림.
  - 답 문장: 시행 전·24h·30일 각 한 화면에서 node 확인과 같은 글자.
  - 그림: 24시간 눈금이 0·6·12·18시뿐(5등분 HH:mm 없음) / 열지도 rect 168개·최댓값 칸 h5·0 칸 h0, CDP 로 시간대를 UTC 로 바꾸면 열지도 칸이 9시간 옮겨 가고 날 막대 날짜는 그대로 / 시행일 = 오늘 − 5일·30일 창 → 회색 rect 하나·'개정 전 n일' 이름표(n 은 실제 셈)·meta '시행부터 센 6일', 시행일 = 오늘 − 25일 → 이름표 없이 범례.
  - 빈 상태·값: geo pending·error(`http_404`)·지난달 판·ok 글 / `capped` ▲ / 0행 '기록 없음' / 나라 `KR` → '대한민국'+KR, `ZZ` 그대로 / 목록 12행 → 5행 + '7개 더', 펼친 뒤 60초 갱신에도 열림 / 접속 `error`·`unconfigured`·HTTP 404 → 덩어리 여섯 상태 글, 종합 판정 그대로.
  - XSS: 경로·출처·utm·채널·나라·탭 이름에 `<img src=x onerror=alert(1)>`·`javascript:alert(1)`·U+202E → 글자로 보임, img 0, 대화상자 0, `a[href]` 는 index.html 고정 링크뿐(DB-IP 포함), CSP 위반·Uncaught 0.
  - 폭 1280·768·360: 가로 넘침 0, 막대 행 이름 칸이 잘리지 않음(scrollWidth ≤ clientWidth), 360 에서 이름·값 줄과 막대 줄이 나뉨. 사진 넷(1280·360 × 시행 전·뒤 30일)은 PR 본문에.
  - 주기·세션: 036 cadence 그대로(보이는 2분 동안 느린 넷 각 2~3회), 창 바꿈 호출은 느린 묶음 다음 시각을 바꾸지 않음 / 창 바꿈 호출만 401 비JSON → `/?relogin=1` 로 한 번(036 그대로).

**배포 뒤 — 사람(완료 조건 아님, status.md 비고 "042 운영 확인 대기")**: 시행 전 화면이 '시행 뒤부터' 로 비어 있다 / 시행일 뒤 7일·30일이 열리고 나라·망이 찬다 / 휴대폰에서 가로 넘침 0 / DB-IP 링크가 db-ip.com 으로 간다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 의존 — 039 위(b6e3219 가 조상), main 에 041(#99), 038 짝 게이트 판(시행 전 visitors before_gate·sinceTs — access_pairs.before_gate)
git merge-base --is-ancestor b6e3219 HEAD && git log --oneline --first-parent 24b2238^2 | grep '#99'
cd server && ruff check . && ruff format --check . && pytest -q
#   1554 passed · 1 skipped(test_geo_perf — DBIP_DIR 없음) · 6 failed = spreads/test_gauge.py UDP bind PermissionError(샌드박스)
#   그중 tests/test_admin.py 47 passed, tests/test_admin_traffic.py 7 passed(node v26 — 답 틀 열 경우·창 부르기 넷·강도) — 검토 반영 뒤 48·9 passed(이름표 일곱·좁은 폭 CSS·그리기 다섯·긴 경로·{h2})
cd web && npm run lint && npm run build     # oxlint 통과 · 빌드 통과 · dist 에 admin 없음
node --check web/admin/admin.js
#   커밋 11개 각 diff 83~293줄, 커밋마다 test_admin.py(뒤 두 커밋은 test_admin_traffic.py 도) 초록
# 설계 세션 확인(브라우저 — 폭·창 바꿈·빈 상태·XSS·대비·사진): 아직(설계 세션이 여기 적는다). 검토 반영(10-02)은 미리보기 사본(가짜 fetch)을 헤드리스 Chrome CDP 로 — 폭 360~1280 여덟 가로 넘침 0·막대 칸 ≥110px·범위 숫자 넘침 0, geo pending·error·지난달 판·시행 전 ③ 두 칸 글, 시 눈금 겹침 0(360·414 — 마지막 1·13·19·20시), 답 줄 묶음 갈림 0, XSS 사본 img 0
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행 web 칸의 "관리자 화면 v2(…)" 뒤에 "· 접속 절 v3(042 — 창 24시간·7일·30일, 질문 여섯 덩어리·답 문장, 확인/브라우저 모양, 나라·망 종류, 요일×시간 열지도)", 비고에 "042 운영 확인 대기(시행일 뒤 7일·30일)". 알려진 빚에서 `(038) 042 전까지 WS 5xx(배포 때 502)는 관리자 화면에 없다 …` 줄을 지우고(⑥ 이 따로 그린다), 041 이 단 `(041) 접속 절 설명은 035 칸 …` 줄을 `(041) 접속 절의 Clarity 설명은 035 칸(받은 지표) 기준 — 043 이 고쳐 쓴다` 로 바꾼다(043 이 이 줄을 없앤다). `(042) 날 막대는 KST, 열지도·시간 막대는 브라우저 시간대 — 한국 밖에서 열면 날 경계가 다르다` 를 더한다.
- `CLAUDE.md` — 스펙 인덱스 042 행 상태 → DONE.
- `docs/context/architecture.md` — '현재 구조' admin 항목의 `web/admin/` 설명 끝에 "접속 절 v3(042) — 창은 JS 변수, 바꾸면 접속 경로 하나만 곧바로 부르고 고른 창의 응답만 그림, 질문 여섯 덩어리와 답 문장 틀, 열지도·겹친 막대는 rect + 클래스".
- `docs/context/product.md` — 용어 절 '날마다 센 방문자'(038) 끝에 "관리자 화면은 확인(하한)과 브라우저 모양(상한)을 늘 함께 보이고, 24시간 창 값은 오늘·어제(KST)를 따로 세어 더한 수라 사람 수가 아니다(042)." 기능 목록 admin 행의 접속 요약 괄호 끝에 "· 창 고르기·시각화(042)".
- `docs/specs/036-admin-v2.md`:
  - §3.2 접속 피드 복사 끝(039 가 단 줄)의 "이 화면은 아직 그리지 않는다(042)" 를 지운다.
  - §3.3 끝에 "접속 창(042 §3.2): 고른 창은 JS 변수에만, 바꾸면 접속 경로 하나만 곧바로 부른다 — 느린 묶음 주기·여덟 경로 만료 셈은 그대로."
  - §3.4 접속: "세 덩어리" 를 "실시간(그대로) → 창 줄과 서버 기록 덩어리 여섯(042 §3.3~§3.9) → Clarity(그대로 — 043 이 바꾼다)" 로, '서버 기록 24시간' 문단을 "서버 기록: 042 가 정한다(창·덩어리·답 문장·빈 상태·좁은 폭)." 한 줄로.
  - §3.5 끝에 "`unconfigured` 이고 code 가 `before_gate` 인 하위 부분(`visitors`·`geo`)은 '연결 안 됨' 대신 시행일 빈 상태 글(042 §3.8)."
  - §3.6 "네 종류" → "네 종류 + 접속 절의 겹친 두 막대·하한–상한 띠·날 막대·요일×시간 열지도(042 — rect + 클래스)". 눈금 문장 "타임라인·시간대별 막대는 창을 5등분한 눈금(`HH:mm`)과 "지금"." → "타임라인은 창을 5등분한 눈금(`HH:mm`)과 "지금", 접속 시간 막대는 정시(0·6·12·18시) 눈금과 "지금", 날 막대는 KST 날(042)."
  - §3.8 외부 링크 문장의 호스트에 "`db-ip.com`(DB-IP CC BY 표시 — 042)". §3.9 긴 목록의 "표 10행" → "접속 목록 21행(042 §3.9 — 위 5행 + 접힘)".
  - §4 정적 단언 줄 끝에 "(042 §4 가 넓힌다)".
- `docs/specs/038-access-v2.md` — §3.6 마지막 항목('036 화면과의 관계') 전체 → "관리자 화면(042)과의 관계: 화면은 창을 늘 `?window=` 로 부르고 `visitors`·`ws.pairs`·`geo` 를 그린다. 이름은 같고 뜻이 바뀐 키 — `pages`(304 포함), `status["5xx"]`·`hourly.errors`·`recent5xx`(WS 경로 뺌), 상위 목록(사람 브라우저 모양만 — % 분모는 `totals.humanPages`), `devices` 이름(`bot`·`unknown` 없음, `tablet` 있음) — 은 042 가 이 뜻으로 그린다. `ws5xx`·`ws.errors` 는 ⑥ 에 따로, `wsErrors` 는 시간 막대 title 에 보인다(CloudWatch 5xx 경보도 WS 를 뺀다)."
- `docs/specs/039-access-geo.md` — §3.2 "지금 화면(036)은 `geo` 를 그리지 않으므로 링크는 그리는 042 가 단다." → "링크는 관리자 화면 `index.html` 에 고정했다(042 — 접속 절 ③ 바닥)."
- `docs/specs/041-admin-explain.md` — 서버 기록 쪽 글을 042 로 넘긴다(Clarity 쪽은 043 이 고친다):
  - §2: '접속 절 설명은 … 042·043 이 칸을 바꿀 때 같은 틀로 고쳐 쓴다.' → "접속 절의 서버 기록 설명은 042 §3.6 이 정한다(Clarity 설명은 043 이 고쳐 쓴다)." 그 뒤 문장('038(접속 요약 v2)은 … 거짓이 되지 않게 한다.')과 §3.2 의 '038 이 앞뒤 어느 때 머지돼도 맞는 글' 항목은 지운다(042 가 038 의 뜻으로 고쳐 썼다).
  - §3.1 타일 부제 문장의 "서버 기록 카드의 총 요청·페이지, " 를 지우고 문장 끝 마침표 앞에 "(서버 기록 덩어리는 042 §3.4·§3.6)" 을 붙인다.
  - §3.3 접속: '창' 의 "서버 기록은 24시간·이 브라우저 시간대, " → "서버 기록은 042 §3.6, ". "이 칸 뜻은 넷이다" → "이 칸 뜻은 실시간 카드 끝과 Clarity 카드 끝 둘이다(서버 기록 덩어리 여섯은 042 §3.6)" 이고 ''서버 기록' 카드 끝'·'상위 표 여섯 묶음 다음' 두 항목은 지운다.
  - §3.4 대시보드 탭의 "적는 곳: 접속 상위 표 '탭'" → "적는 곳: 접속 ⑤ 대시보드 진입 탭(042)".
  - §4 설계 세션 확인 8 의 "서버 기록 둘·" 을 지운다. §3.1 덩어리 끝 설명 자리의 예 `access-tables` → `b-qN`, §4 node 확인의 "상위 표 '탭' 의 한국어 이름·title id, 타일 부제 여섯" → "⑤ 대시보드 진입 탭의 한국어 이름·title id(042), 타일 부제(서버 기록 ①·⑥ 일곱·Clarity 넷)".

**담당자에게 제안**: 없다 — 관리자 화면과 그 스펙(029·036·038·039·041)은 이 레포 주인 담당이다.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `web/admin/index.html` — `#traffic`: 041 설명(서버 기록 쪽 고쳐 씀) → 실시간 카드(036 그대로) → 묶음(창 줄 + 덩어리 카드 여섯 `card q` — `q-head`·`a-qN`·`b-qN`·'이 칸 뜻') → Clarity 카드. ③ 바닥 `geo-foot`(`#geo-state` + DB-IP 고정 링크), ⑥ 에 036 의 최근 5xx 접힘.
  - `web/admin/admin.css` — 창 줄·`q-head`·`answer`·`tiles`·걸러 내기·막대 행 `brow`·`cols2`/`cols3`·열지도·시 눈금·범례·견본, SVG 클래스(`confirmed`·`shaped`·`returning`·`bot`·`pre`·`h0`~`h5`), 960px·640px·480px·`@container`(340px — `.col`). 036 의 `traffic-top`·`top-table` 지움.
  - `web/admin/admin.js` — 요청: `load` 가 접속 경로에 `?window=` 를 붙이고 결과에 `asked`, `loadAccess`(떠 있는 호출 하나 + 끝나면 한 번 더)·`follow`(응답 창으로 되돌림)·`settle`(묶음 끝 공통). 창: `WINDOW_NAME`·`picked`·`drawWindowBar`·버튼. 답: 이름표 표 일곱·`facts`·`answer1`~`answer6`·`answers`(조각 목록, 값 자리 `{ b }`)·`sentence`·`heatGrid`·`heatLevel`. 덩어리: `q1`~`q6`·`fillTraffic`·`blankTraffic`·`trafficLead` 와 조각 함수(`barRow`·`pairBar`·`longList`·`fold`·`dayBars`·`heatmap`·`hourAxis` 등). 036 의 `fillAccess`·`topTable` 지움. 알림 칩은 `.chip[data-filter]` 만(창 버튼도 chip 모양).
  - `server/tests/test_admin.py` — `EXTERNAL_HOSTS`·`SCRIPT_BANNED` 넓힘, 042 정적 단언 다섯, 041 node 하네스를 `fillTraffic` 으로. `server/tests/test_admin_traffic.py`(새 파일) — node 논리 확인.
  - 분모 단언 꼴(`test_screen_page_line_lists_share_is_over_human_pages`): `q3`·`q5` 본문에 `const hp = n0(f.T.humanPages);` 와 `shareList('…', rows2(f.a.referrers|utmSources|paths), …, hp)` 가 하나씩, `totals.pages`·`T.pages` 없음, `answer5` 에 `pct(paths[0][1], hp)`.
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - ① 24h 답은 본문 → 꼬리 → 평가 순. 시행 뒤 `visitors` 가 ok 가 아니면 ① 답은 비우고 본문이 036 상태 글.
  - ⑤ 경로 절만 남으면 '…가장 많다.' 로 끝낸다.
  - ③ '뿐 — 대부분 봇이다' 는 cloud 모양 > 0 일 때만(① 의 shaped > 0 과 같은 꼴). ② '자동 요청' 상위 둘은 요청 0 인 종류를 뺀다.
  - 5행 + 'n개 더' 접기는 §3.9 의 여섯 밖 목록(경로·탭·기기·페이지 줄 목록)에도 같은 함수로 건다(6행 이상일 때만 접힌다).
  - 접속 부분 `error` 의 덩어리 글은 '불러오지 못함 — <code>'. 페이지 줄 목록 막대는 브라우저 모양 색. 탭 행 title 은 늘 원래 값.
  - 날 막대 이름표의 '부터 N일이 다 찬다' 날짜 = 시행일 + (N−1)시간의 KST 날짜. 덩어리 머리 meta 는 ②·⑥ 에도 '<창> · 날마다 센 방문자'.
  - 041 설명의 '남기는 것' 은 '서버 기록' dt 로 합치고 '대시보드 탭' 은 ⑤ '이 칸 뜻' 으로 옮겼다.
  - node 논리 확인은 새 파일 `test_admin_traffic.py`(시간대 Asia/Seoul 고정) — 정적 단언은 `test_admin.py`.
- 실행 중 함께 고친 스펙 절: 041 §3.3 '대시보드 탭' 항목(⑤ 로 옮김). 그 밖은 §6 그대로.
- 검토 반영(10-02): 응답 `window` 를 `own()` 으로(`winOf`), ③ geo 글을 칸마다 새 노드로(나라 칸이 비던 것), 지난달 판 cloud 단서에 '그중 확인' 유지, 목록 화면 상한 21행, `{h2}` 21 → '21~24시', 답 ⑤ 경로 title·값 묶음 span, '지금' 앞 눈금 비움, 창 meta 한 줄 글, ⑤ '이 칸 뜻' 에 기기·OS·브라우저 키, ②·⑥ dd 를 038 규칙대로(운영자 흔적은 출처·탐색 줄은 스캐너, 4xx 짐작 문장 지움), CSS(geo 배지 줄바꿈·3열 960px·두 줄 행 컨테이너 질의·값 칸 7.5em·범위 숫자 줄바꿈·⑥ 회색 막대·시행 전 테두리·최근 5xx 접힘 `more`), 038 §3.6·036 §3.9·041 §3.1·§4 문장(§6).
- 남은 빚: 화면 확인(§4 설계 세션 — 폭·창 바꿈·빈 상태·XSS·대비·사진)은 아직이다 — 가짜 응답 생성기(게이트 전·뒤, geo 넷, 표본 적음·빈·capped·XSS·긴 목록, 접속 상태·지연)와 미리보기 사본 만들기는 레포 밖에 두고 설계 세션에 경로를 넘겼다(사본의 fake.js 는 IIFE 로 감싼다 — admin.js 와 전역 이름이 겹친다). `admin.js` 2,094줄. 덩어리 여섯은 응답이 바뀔 때(60초)마다 통째로 다시 그린다 — 접힘만 Set 으로 남는다.
