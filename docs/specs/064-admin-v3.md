# 064 — admin-v3

상태: DONE | 의존: main(060 머지 뒤). 계약은 062(`visitors.flows` 등)·063(`/admin/aws/series`) 스펙에서 복사 — 그 둘이 아직 머지 전이면 가짜 응답으로 만들고 시험한다(부분 상태 규칙대로 '연결 안 됨'). 065 admin-screens 가 이 스펙의 틀(공통 모듈·머리·색)을 쓴다. 036·041·042·053 의 **화면** 절을 대신한다(그 스펙들의 서버·피드 계약은 그대로).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-09): 지금 관리자 화면은 한 페이지에 다 몰려 글이 많고 잘 안 보인다. **창(페이지)을 나누고, 그림 위주·글 최소로**, 딱 봐서 무엇이 문제인지·서버와 트래픽 상태·어디서 들어오고 나가는지·AWS 지표(CPU·네트워크·크레딧)가 보이게 다시 짠다.

## 2. 범위
- 만드는 것: `web/admin/` 을 여러 페이지로 — `index.html`(개요)·`server.html`(서버)·`traffic.html`(트래픽), 공통 `common.js`·`charts.js`·`admin.css`, 페이지 스크립트 셋(ES 모듈), 차트 라이브러리 로컬 사본 `vendor/echarts-5.5.1.min.js`(+ 라이선스 파일), 관리자 nginx 의 `vendor/` 캐시·CSP, 테스트. 기존 `admin.js` 와 그 시험은 새것으로 바꾼다(화면 이용 절은 065 가 `screens.html` 로 옮긴다 — 이 스펙은 머리의 '화면' 링크만 둔다).
- 하지 않는 것: 서버·피드(그대로 읽기만, 062·063 은 그 스펙). 공개 사이트. 빌드 도구(관리자는 빌드 없음 그대로 — 029·036).
- 라이브러리(설계 세션 결정): Apache ECharts 5.5.1 `dist/echarts.min.js` 를 npm 꾸러미에서 꺼내 그대로 둔다(Apache-2.0, `vendor/ECHARTS-LICENSE.txt` 에 원문). 지도·흐름(sankey)·열지도·게이지를 손 SVG 로 만드는 것보다 낫다. 외부 CDN 은 쓰지 않는다(CSP `'self'`).

## 3. 동작

### 3.1 공통 틀
- 모든 페이지 맨 위 머리 줄(고정): 'KimpTrack 관리자' · 링크 넷 **개요·서버·트래픽·화면**(지금 페이지 강조, 화면은 `screens.html`) · 오른쪽에 전체 상태 점(초록 정상/주황 주의/빨강 문제 — §3.2 규칙을 그 페이지가 가진 피드로 셈, 개요 링크) · 마지막 갱신 'HH:MM:SS' · 로그아웃(`/cdn-cgi/access/logout`, 지금처럼).
- 글: 기본 15px, 큰 수 32px 굵게, 카드 제목 15px. 카드 = 제목 한 줄(2~6낱말) + 그림. 뜻 풀이는 제목 옆 '?' 하나(`title` 툴팁 한두 문장)만 — 긴 설명 문단·답 문장·'이 절 읽는 법' 은 두지 않는다.
- 색(토큰, 어두운 바탕만): 바탕 #121420, 카드 #1c1f2b, 선 #2c3040, 글 #ececf1, 흐린 글 #9a9cab, 강조 #9184d9, 정상 #5fbf8f, 주의 #e0a458, 문제 #e0697d, 보조 #6f9bee. 차트 색 순서: 강조·보조·정상·주의·문제·#c792ea·#7fdbca·#f78c6c. 숫자 색은 상태에만 쓴다.
- 배치: 최대 폭 1600px, 카드 격자 `minmax(420px, 1fr)`, 큰 수 칸 `minmax(180px, 1fr)`, 640px 아래 한 줄. 차트는 창 크기가 바뀌면 다시 맞춘다. 가로 넘침 없음.
- 데이터 부르기(`common.js` — 029·036 규칙 옮김): 요청마다 `X-Requested-With`, 401 비JSON·fetch 실패는 만료 신호 → `?relogin=1` 표시로 한 번만 새로고침, 페이지가 보이는 동안만 주기(빠른 10초: `/api/health`·`/svc/api/health`·`/svc/api/admin/status`·`/api/health/collect`, 느린 60초: 그 페이지가 쓰는 나머지). 부분 상태(`state`·`code`)는 카드 오른쪽 위 작은 표(연결 안 됨·권한 없음·불러오지 못함·오래됨)로만.
- 보안: 피드의 글자(경로·출처·나라·경보 이름…)는 DOM 에는 `textContent` 로만, 차트 툴팁 서식에는 `echarts.format.encodeHTML` 을 거쳐서만. 관리자 CSP 에 `style-src 'self' 'unsafe-inline'` 을 더한다(ECharts 툴팁이 인라인 스타일을 쓴다 — 스크립트는 그대로 `'self'` 만).

### 3.2 개요 (`index.html`) — "지금 문제 있나"
1. **상태 띠**: 문제가 없으면 큰 초록 '정상' 한 줄. 있으면 빨강·주황 칩을 심각한 순으로(칩 = 짧은 이름 + 값, 누르면 그 페이지의 해당 카드로). 규칙(빨강 / 주황):
   - 수집기 `/api/health` 가 ok 아님 → 빨강 '수집 멈춤'; api `/svc/api/health` ok 아님 → 빨강 'API 이상'; `status.redis`·`influx` down → 빨강.
   - `/api/health/collect` 거래소마다 `openOutage` → 빨강 '<거래소> 끊김 n분'; `successRate1h` < 99% → 주황.
   - 경보 `counts.alarm` > 0 → 빨강 '경보 n'; canary `ok` false → 빨강 '점검 실패'.
   - 상자마다(최근 15분 평균 — 시계열 주기가 15분보다 길면 마지막 칸): CPU ≥ 90 빨강·≥ 75 주황, 메모리 ≥ 90·≥ 80, 디스크 ≥ 85·≥ 75, 크레딧 잔량 < 10 빨강·< 30 주황(값 있을 때만), 초과 과금 크레딧 > 0 주황, 상태 검사 실패 → 빨강.
   - 접속 최근 1시간 5xx(`recent5xx` 의 `ts` 로 셈) ≥ 10 빨강·≥ 1 주황; WS 재접속 실패(`status.ws5xx` — 7·30일 창은 마지막 24시간 `hourly.wsErrors` 합) ≥ 1 주황.
   - 예산 실제 ≥ 100% 빨강, 예측 ≥ 100% 주황. 최근 1시간 Slack 알림 ≥ 1 주황.
2. **큰 수 다섯**(작은 24시간 선 그림 곁들임): 지금 접속(WS)·오늘 방문자(확인~브라우저 모양)·수집 성공률(1h)·경보(OK/ALARM)·이번 달 비용/예산(게이지).
3. **수집 원천 줄**: `/health/collect` 의 원천 전부를 그 순서대로(현물 6 + perp 4) 알약 — 초록 수집 중·주황 지연(`stale`)·빨강 끊김(`down` 또는 열린 실패 구간), 글자는 마지막 성공 n초 전.
4. **상자 셋**(collect·data·serve): 카드마다 CPU·메모리·디스크 반원 게이지 셋과 24시간 CPU 선.
5. **24시간 트래픽**: 시간별 사람 모양 페이지 막대 + 5xx 빨강 막대 + WS 접속 선(오른쪽 축).

### 3.3 서버 (`server.html`) — AWS·수집·경보·비용
- 위: 기간 단추 **6시간·24시간·7일·30일**(기본 24시간, JS 변수 — 바꾸면 `/api/admin/aws/series?range=` 하나만 곧바로).
- 카드(시간 축 공유 — 한 카드를 확대·이동하면 같은 페이지의 시간 차트가 함께, ECharts `connect`): **CPU %**(상자 셋 선) · **네트워크**(상자마다 들어옴 위·나감 아래로 뒤집은 면적, 바이트/초 단위 자동 B·KB·MB) · **메모리 %** · **디스크 %**(+ 75·85 기준선) · **CPU 크레딧**(값 있는 상자만 — 잔량 선, 사용 막대, 초과 과금 빨강 막대) · **디스크 입출력**(읽기 위·쓰기 아래) · **WS 접속 수** · **점검(canary)**(시간·오류 점) · **상태 검사**(실패 구간 빨강 띠).
- **수집 상태**: 거래소마다 24시간 가로 띠(정상 초록·끊김 빨강 구간 — `outages`)와 성공률.
- **경보**: 경보 이름 알약(상태 색), 그 아래 7일 알림 시각표(점 — Slack/경보 모양 다르게, 툴팁에 글).
- **비용**: 예산 게이지(실제·예측 두 바늘)와 금액.
- **도구**(접힌 카드 하나): 즉시 갱신(토큰 입력 → `POST /api/refresh`, 029 그대로), 바깥 콘솔 링크(Clarity·CloudWatch·Lambda·Cloudflare·GitHub — 지금 것).

### 3.4 트래픽 (`traffic.html`) — 누가·어디서·언제·무엇을
- 위: 창 단추 **24시간·7일·30일**(`/svc/api/admin/access?window=`, 042 의 고르기 규칙 — 떠 있으면 끝난 뒤 한 번·응답 창으로 되돌림). 단추는 응답의 `windows` 에 없는 창만 끈다(응답 전엔 셋 다 열림). 서버 기록 게이트(처리방침 시행일) 문구는 어느 페이지에도 두지 않는다 — 사람 결정 2026-10-09(060 이 게이트를 첫 기록 앞으로 옮긴다). 오른쪽에 '화면 분석 열기 ↗'(`screens.html`, 새 창).
- 큰 수: 방문자(확인~모양 — 창 안 날마다 센 수의 합)·사람 모양 페이지·지금 접속·봇 비율(스캐너 + 자동화 도구 요청 ÷ 전체 — 탐색 경로 줄은 대부분 이 둘에 들어 더하면 두 번 센다)·5xx.
- **흐름**(sankey): 들어온 길(channel) → 첫 페이지 → 마지막 페이지, 굵기 = 브라우저 모양 짝 수(`visitors.flows`, (기타) 포함). 062 이 없으면 '연결 안 됨'.
- **방문 추이**: 24시간은 시간별, 7·30일은 날별 쌓은 막대(확인/모양 — `visitors.days`) + 다시 온 사람 선.
- **요일×시간 열지도**(042 데이터 그대로).
- **어디서**: 들어온 길 도넛(channel), 출처 상위 10 막대(`referrers`), 나라 상위 막대(국기 그림 문자 + 이름, `geo.countries`), 망 종류 도넛(`geo.networks` — 국내 통신사·해외 통신사·클라우드·기타·모름), utm 막대(있을 때만).
- **들어오고 나간 곳**: 첫 페이지·마지막 페이지 나란한 막대(`entries`·`exits`), 페이지 수 분포(`depthPages`).
- **누가**: 요청 종류 여덟 100% 띠(`classes`), 기기·OS·브라우저 도넛 셋.
- **무엇을**: 페이지 상위 막대(`paths`), 대시보드 탭 막대(`tabs`).
- **문제**: 응답 상태 도넛(`status`), WS 연결 시간 막대(`ws.durations` — 대시보드를 열어 둔 시간이지 머문 시간이 아니다, 038), 최근 5xx 표(작게).
- **Clarity**(`/svc/api/admin/clarity`): 세션·평균 스크롤 깊이·활성 시간 큰 수 + 불만 신호 여섯 막대 + 'Clarity 열기 ↗'.

### 3.5 엣지
- 피드가 연결 안 됨·실패면 그 카드만 비우고 상태 표. 빈 데이터면 그림 자리에 '자료 없음' 한 줄. 하위 부분이 `unconfigured`(code `before_gate` 포함)여도 다른 부분과 같은 '연결 안 됨' 이다 — 정책·시행일 글은 없다(§3.4).
- 숫자 0 은 그대로 보이고 null 은 '–'. 큰 값은 천 단위 쉼표, 바이트는 단위 자동.
- 차트 라이브러리를 못 불러오면 큰 수·상태 띠·표만 보인다(그림 칸 '차트를 불러오지 못함').

## 4. 검증
- 정적(`server/tests/test_admin.py` 를 새 구조로): 페이지 넷의 머리 링크·현재 강조, 각 페이지가 부르는 경로 목록 = 관리자 nginx 의 분기와 대조, `vendor/echarts-5.5.1.min.js` 와 라이선스, 인라인 스크립트 없음, `innerHTML` 은 정해진 도움 함수 안에서만(피드 글자는 들어가지 않음), CSP 값(관리자 `/` 와 `vendor/`).
- node(가짜 DOM·가짜 echarts — 042 방식): §3.2 상태 띠 규칙 경우마다(빨강·주황·경계값·값 없음), 큰 수 계산, flows → sankey 마디·고리 변환((기타) 포함, 같은 이름 들어온/나간 페이지 마디 구분), 바이트 단위, 기간 단추가 부르는 경로 하나, 만료 신호 한 번 새로고침, 툴팁 서식이 `<img onerror>` 글자를 그대로 글로 내는지.
- `cd server && ruff check . && ruff format --check . && pytest -q`, `node --check` 모든 관리자 스크립트, `cd web && npm run lint && npm run build`.
- 설계 세션(브라우저, 가짜 피드): 세 페이지 1440·1024·390 폭 가로 넘침 0, 상태 띠 정상/문제 모양, 차트가 그려지고 툴팁·시간 연결 동작, CSP 위반 0.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 시작 — worktree feat/064-admin-v3(faa9af5 — origin/main 9b13b2c 위). 작업 중 origin/main 이 062·063·#121·#126 으로 나아가 병합했다
#   (729d10e — 충돌: CLAUDE.md 062·063 행, status.md admin 행·빚, architecture.md admin 항목, 062·063 스펙 add/add → 스펙은 main 쪽)
# 환경 — node v26.4.0(/opt/homebrew/bin). server venv 는 uv(샌드박스 밖 — 안에서는 uv 가 실패), web 은 npm ci(샌드박스 밖)
# ECharts 사본 — 빈 scratch 디렉터리에서 npm pack echarts@5.5.1(샌드박스 밖, shasum 8dc9c68d…) → package/dist/echarts.min.js
#   1,030,855B sha256 e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf('version="5.5.1"') + LICENSE·NOTICE·licenses/LICENSE-d3
cd server && uv run --extra dev ruff check . && uv run --extra dev ruff format --check . && uv run --extra dev pytest -q
#   All checks passed! · 382 files already formatted · 1999 passed, 1 skipped(test_geo_perf — DBIP_DIR 없음)
cd web && npm ci && npm run lint && npm run build      # oxlint src exit 0 · tsc -b && vite build ✓(dist 에 admin 없음)
for f in web/admin/*.js web/admin/vendor/echarts-5.5.1.min.js; do node --check "$f"; done   # 여섯 모두 통과
# 064 시험 — node 는 가짜 DOM·fetch·location·history(tests/admin_dom.py), 경우마다 node 하나:
#   test_admin.py 31(nginx·compose·화면 정적) · test_admin_rules.py 2(띠 규칙 경우 35·15분 평균) · test_admin_calc.py 5(큰 수·흐름·바이트·툴팁 글자)
#   test_admin_pages.py 7(페이지별 경로 = 관리자 nginx 분기·기간·창·만료·빠진 피드·띠 칩·즉시 갱신) · test_admin_xss.py 3(모든 툴팁 서식에 <img onerror>)
#   test_admin_fake.py 6(가짜 피드 서버 경우마다 세 페이지 '표시 오류' 0) · test_admin_render.py 4(진짜 ECharts 5.5.1 서버 그리기 — 그림 전부 예외 0)
#   test_admin_contract.py 2(진짜 063 AwsReader.series·038/039/062 VisitFeeds 응답으로 그림 — 띠 칩까지)
# 가짜 피드 서버 — uv run web/scripts/admin-fake-feeds.py --scenario problem(샌드박스 밖 127.0.0.1:18064, 확인 뒤 내림): / 200 text/html·
#   CSP(style-src 'unsafe-inline' 포함)·X-Frame-Options DENY·no-store, /vendor/echarts-5.5.1.min.js text/javascript·private 1년,
#   /api/health 503 stale, /screens.html 404, 디렉터리 밖 경로 404, POST /api/refresh 200, /__scenario/ok
# dataviz 검사기(validate_palette.js) — 차트 색 순서 여덟을 카드 #1c1f2b 위에서: 대비 통과, 색각 이상 인접 쌍 #6f9bee↔#9184d9 ΔE 3.6 등 실패(§7 빚)
# 하지 않은 것(설계 세션 몫 — 이 세션은 브라우저·Docker 를 쓰지 않았다): 세 페이지 1440·1024·390 폭 가로 넘침·상태 띠 모양·툴팁·시간 연결·
#   CSP 위반 0, nginx -t(nginx-admin.conf 를 고쳤다 — location 둘)
```

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 web 칸을 "관리자 화면 v3(064 — 개요·서버·트래픽 페이지, ECharts, 상태 띠·AWS 시계열·흐름 그림) · 화면 분석 창(065)" 로.
- `CLAUDE.md` — 스펙 인덱스 064 행 DONE, §2 레포 구조의 `web/admin/` 설명("여러 페이지·공통 모듈·vendor/ECharts, 빌드 없음").
- `docs/context/architecture.md` — '현재 구조' admin 항목의 `web/admin/` 설명을 새 구조로.
- `docs/context/dev-setup.md` — 관리자 화면 확인 방법(정적 파일 + 가짜 피드), ECharts 사본 갱신 방법(npm pack 뒤 dist 한 파일).
- `docs/specs/036-admin-v2.md`·`041-admin-explain.md`·`042-admin-traffic.md`·`053-admin-attention.md` — 화면 절(§3 의 화면 부분) 머리에 "이 화면은 064·065 가 대신한다 — 아래 피드 계약만 지금도 맞다" 한 줄, 그 아래 화면 묘사는 지운다.
- `docs/specs/043-admin-clarity-view.md` — 지운다(Clarity 칸은 064 §3.4). CLAUDE.md 인덱스의 043 행도 지운다.
- `docs/specs/029-admin.md` — 관리자 CSP 문장(`style-src` 더함, `vendor/` 캐시).

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `web/admin/`: `index.html`(개요)·`server.html`(서버)·`traffic.html`(트래픽), `common.js`(경로 표 `PATHS`·`call`·`start` — 요청·세션·두 주기·고른 값 피드·카드별 그리기, 부분 상태 표 `tagOf`, 상태 띠 규칙 `problems`·`recentAvg`, 머리 점, `jump`, `once`, 글자·수·바이트·금액 도구), `charts.js`(칸 id 마다 인스턴스 하나·`draw`/`blank`·툴팁 `tip`(encodeHTML)·`spark`·`lines`·`bars`·`donut`·`gauges`·`budgetGauge`·`sparkBars`·`lanes`·`mirror`·`unzoom`), `overview.js`·`server.js`·`traffic.js`(카드 그리기 + 계산 `todayVisitors`·`hourlySuccess`·`alertHours`·`runs`·`collectLanes`·`alertPoints`·`flowGraph`·`heatGrid`·`botShare`·`countryLabel`), `admin.css`(§3.1 토큰), `vendor/echarts-5.5.1.min.js`·`ECHARTS-LICENSE.txt`·`ECHARTS-NOTICE.txt`·`ECHARTS-LICENSE-d3.txt`. 지운 것: v2 의 `admin.js`·`index.html`·`admin.css`.
  - `web/nginx-admin.conf`(`/` CSP 에 `style-src 'self' 'unsafe-inline'`, `location /vendor/` — 같은 CSP·`X-Frame-Options`·`private, max-age=31536000, immutable`), `web/Dockerfile`(주석), `web/scripts/admin-fake-feeds.py`(가짜 피드 서버 — 이미지에 들지 않는다).
  - `server/tests/`: `admin_dom.py`(node 바탕), `test_admin.py`(화면 단언을 v3 로·CSP·`/vendor/`), `test_admin_rules.py`·`test_admin_calc.py`·`test_admin_pages.py`·`test_admin_xss.py`·`test_admin_fake.py`·`test_admin_render.py`·`test_admin_contract.py`, `test_clarity.py`(관리자 파일 이름만). 지운 것: `test_admin_traffic.py`·`test_admin_attention.py`(v2 화면 node 시험).
  - 문서: 이 스펙 §3·§5·§7, 029 §3.1~§3.3, 036·041·042 §3(화면 → 한 줄)·§4 머리 한 줄, 053 §3(관리자 화면 쪽만 지움)·§4 머리 한 줄, 043 지움, `CLAUDE.md`(§2·§4), context 넷(status·architecture·dev-setup·product).
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
  1. 사람 결정(2026-10-09, 설계 세션 전달 — 060): 서버 기록 게이트·처리방침 시행일 글은 어느 페이지에도 없다. `before_gate` 하위 부분도 '연결 안 됨'(code 는 title 에도 싣지 않음), 창 단추는 응답 `windows` 에 없는 창만 끈다 — §3.4·§3.5 에 적었다.
  2. §3.2 3 'perp 3' → 피드의 원천 전부(현물 6 + perp 4 — 047 뒤)를 그 순서로, 지연(`stale`)은 주황 알약 — 스펙 고침.
  3. 상태 띠: 15분 평균 = [끝 − max(900초, 주기), 끝) 의 값 평균(7·30일 기간은 마지막 칸), 최근 1시간 5xx = `recent5xx`(20줄, WS 뺌)의 ts 로, WS 재접속 실패 = 24시간 창이면 `status.ws5xx`·7·30일 창이면 마지막 24시간 `hourly.wsErrors` 합(머리 점이 창에 따라 바뀌지 않게) — 스펙 고침. 열린 실패 구간이 있는 원천은 성공률 주황을 따로 내지 않는다. 헬스 호출 자체 실패도 빨강('응답 없음'). 예산 규칙은 한도가 있는 예산 모두(카드·게이지는 가장 나쁜 월 예산). 칩은 서버 페이지 카드(`#collect`·`#alarms`·`#canary`·`#cpu`·`#mem`·`#disk`·`#credit`·`#checks`·`#cost`)와 트래픽 `#problems` 로 간다.
  4. 앱의 404 JSON(그 경로를 모르는 서버 — 062·063 배포 전)은 '연결 안 됨'. 표는 넷에 '첫 조회 중'(pending)·'불러오는 중'(첫 응답 전)·'표시 오류'(그 카드를 그리다 예외)를 더했다.
  5. 큰 수 곁 그림: 지금 접속 = 063 `wsClients` 24시간, 오늘 방문자 = 오늘(KST — 응답 `endTs`) `visitors.days` 의 확인~모양 + 시간별 사람 모양 페이지 선, 수집 성공률 = 011 의 1시간 식을 24시간 칸마다 `outages` 로 다시 센 선(원천 평균), 경보 = 'OK' 또는 'ALARM n'(/ 전체) + 시간별 알림 막대(Slack + ALARM 으로 바뀐 경보), 비용 = 가장 나쁜 월 예산의 게이지.
  6. 봇 비율 = (scanner + tool) ÷ 전체 요청 — 탐색 줄은 UA 종류와 겹쳐(브라우저 모양 탐색은 scanner, curl 은 tool) 더하면 두 번 센다. 빈 UA 의 탐색 줄(unknown)은 빠진다 — 스펙 고침.
  7. 트래픽 방문자 큰 수 = 창 안 날마다 센 수의 합(하루 평균 아님). 방문 추이 24시간은 시간별 페이지(스크립트가 돈 페이지 + 그 밖 사람 모양 페이지 — 시간별 방문자 값은 피드에 없다). 요일×시간 열지도는 창과 무관하게 늘.
  8. 'WS 머문 시간' → 'WS 연결 시간'(038 — 열어 둔 시간이지 머문 시간이 아니다) — 스펙 고침.
  9. 그림 꼴: 네트워크·디스크 입출력은 상자마다 작은 그림(세로 눈금 따로 — 상자끼리 수십 배 차이), CPU 크레딧은 위 잔량·아래 사용 + 초과 과금(빨강, 상자끼리 쌓음) 두 칸, 점검은 걸린 시간 선 + 그 칸 위의 빨간 오류 점, 상태 검사·수집 상태는 가로 띠(사용자 정의 계열 — 실패 종류와 무관하게 빨강, 이름 옆 1시간 성공률), 비용 게이지는 실제(상태 색 + 호)·예측(흐린 짧은 바늘) 두 바늘.
  10. 확대: 안쪽 확대(Ctrl+휠·트랙패드 핀치·끌기 — 그냥 휠은 페이지를 내린다) + '확대 풀기' 단추. 시간 차트의 확대 부품 id 를 모두 'zoom' 으로(connect 가 넘기는 id 가 다른 칸에서도 맞게).
  11. 색: 상자 색은 이름에 고정(collect 강조·data 보조·serve 정상 — 차트 색 순서의 앞 셋), 도넛 색은 키 표 순서, 응답 상태 도넛만 상태 색. 둘 이상의 선은 범례 + 선 끝 이름표. 바이트는 1024 단위(10 미만 소수 1자리), 시간 축 글자는 `{M}/{d}`·`{HH}:{mm}`(영어 달 이름 없음).
  12. `/vendor/` 캐시는 `private`(공유 캐시에 두지 않음)·1년·immutable, 접속 기록은 다른 정적 파일처럼 남긴다. ECharts 의 NOTICE·d3 BSD 라이선스도 옆에 둔다(사본에 d3 코드가 들어 재배포 고지).
  13. '화면 분석 열기 ↗' 만 새 창(`rel="noopener"`), 'Clarity 열기 ↗'·콘솔 링크는 지금처럼 같은 탭·noreferrer.
  14. 원천 이름은 한국어 표(`EX_NAME`), 나라는 국기 그림 문자 + `Intl.DisplayNames` 한국어 이름. 기기·OS·브라우저 도넛은 방문자 값이 없으면 기기·브라우저만 사람 모양 페이지 줄로. 개요 상자 게이지 값 = 띠 규칙과 같은 15분 평균·같은 문턱 색. 062 의 `entries`·`exits` 는 0 인 이름까지 실려 첫·마지막 페이지 그림은 사람이 있는 페이지만.
  15. 가짜 피드 서버(설계 세션 확인용 — 표준 라이브러리, 경우 여섯, 실행 중 바꾸기)와 그 시험을 더했다(dev-setup.md).
  16. 함께 고친 다른 문서: 029(CSP·`/vendor/`·화면 파일 문장), 036·041·042·053(§6 대로 — 053 은 공개 페이지 쪽 덮어 보기·헤더 계약이 살아 있어 관리자 화면 쪽만 지우고 머리 줄을 그 뜻으로, 041 은 피드 계약이 없어 그렇게 적음), `CLAUDE.md` §4 실행 순서 문단의 043, product.md admin 줄, architecture.md 046 항목의 `admin.js` 한 마디, status.md 의 063 빚 한 줄('정확 일치 location 이 없어 기록에 남는다' — main 의 063 이 location 을 더해 이미 풀렸다), test_clarity.py 의 관리자 파일 이름.
  17. 커밋 300줄 예외 둘: ECharts 사본 커밋(299줄로 들었다)과 v2 화면을 통째로 지운 커밋(지우기만 5,728줄) — 고친 줄이 없는 커밋이다. 작업 중 origin/main 병합 커밋 하나(062·063·#121·#126).
- 남은 빚:
  - 설계 세션 브라우저 확인(§4 마지막 줄) — 세 페이지 1440·1024·390 폭 가로 넘침·상태 띠 정상/문제 모양·툴팁·시간 연결·CSP 위반 0. 실행 세션은 node 가짜 DOM·진짜 ECharts 서버 그리기(SVG)·가짜 피드 서버 응답으로만 봤다. `nginx -t` 도 아직(location 둘을 고쳤다).
  - `screens.html` 은 065 전까지 없다(머리의 '화면' 링크 404) — 그 사이 관리자에서 화면 영역 덮어 보기를 못 본다.
  - 차트 색 순서(§3.1)의 앞 두 색이 색각 이상에서 가깝다(검사기 ΔE 3.6, 정상 시각 7.0) — 범례·선 끝 이름표로 보완, 색은 설계 결정. 개요 '24시간 트래픽' 은 두 축 그림(§3.2 5 결정).
  - 다른 스펙에 남은 043 언급(040 §2·§3.5·§3.7 '화면(043)', 041·042 의 기록 절)과 039 §3.2 '링크는 관리자 화면 index.html 에 고정'(→ traffic.html) — §6 밖이라 두었다(주인 담당 정리 제안).
  - 063 시계열은 요청마다 인코딩한다(063 빚) — 서버·개요 페이지가 열린 동안 60초마다 부른다(서버 캐시 300초).
