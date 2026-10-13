# 065 — admin-screens

상태: DONE | 의존: **061 attention-v2**(DONE — 요소 열쇠·피드 `page` 인자·`elements`·`exits`·`depth`)·**066 admin-renewal**(왼쪽 내비·페이지 머리·토큰·상세 판·공통 모듈 — 머지 뒤 main 에서 시작). 053 의 덮어 보기(attention.js)를 요소·깊이로 넓히고, 066 의 '준비 중' `screens.html` 을 실제 화면 분석 페이지로 바꾼다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-09): 어디를 보는지 **창을 나눠** 크게, 화면을 이루는 **요소마다** 얼마나 누르고 보는지. 관리자 '화면' 페이지가 실제 우리 페이지를 크게 띄우고 그 위에 영역·요소·스크롤 깊이를 겹쳐 그리며, 옆에 요소 순위·떠난 곳·내려온 깊이를 그림으로 보인다. Clarity Data Export 는 이 값을 주지 않는다(061 §1) — 값은 우리 수집(052·061)이다.

## 2. 범위
- 만드는 것: `web/admin/screens.html`·`screens.js`(066 의 틀·공통 모듈·토큰·상세 판을 쓴다), `web/public/attention.js` 덮어 보기의 요소·깊이 그리기, 가짜 피드 서버(`web/scripts/admin-fake-feeds.py`)의 attention 응답, 테스트.
- 하지 않는 것: 수집·피드(061 그대로 읽기만). 066 의 다른 페이지. 화면의 `data-el`(061 몫).
- 바꾸는 기존 것: 066 의 '준비 중' `screens.html`, 053 덮어 보기 메시지(키를 더한다 — 053 꼴 메시지는 그대로 받는다).

## 3. 동작

### 3.1 읽는 값 (052·061 복사)
`/svc/api/admin/attention?days=1|7|30|90[&page=<화면>]` → `{state, code, gateAt, days, from, to, fetchedAt, rows}`. `state` `ok`·`before_gate`(rows 빔)·`error`. `rows` = `[{page, device, pv, areas:[{id, ms, clicks, seen}], exits, depth, elements?}]`.
- `elements` `[{area, key, clicks, hoverMs, visMs}]` 는 `page` 를 줄 때만 — (clicks, visMs) 큰 순 300개까지. 열쇠 `(기타)` = 그날 열쇠 300 을 넘어 묶인 것.
- `exits` `[[영역, n]]`(n 큰 순) — 떠날 때(pagehide) 가장 크게 보이던 영역. `depth` 길이 11 — 칸 k 는 가장 아래가 k×10% 칸(10 = 끝)에서 멈춘 pv 수, 대시보드 화면은 null. 깊이·떠난 곳은 pagehide 로 끝난 pv 만이라 합이 pv 보다 작다.
- 마우스 올림은 PC·마우스만 센다(휴대폰 hoverMs 0). 행(`row:*`)은 클릭만(보인 시간 0).
- 화면: `landing`·`privacy`·`kimp-chart`·`kimp-history`·`app-spread`·`app-history`·`app-gap`·`app-pp`·`app-health`·`app-flow`. 기기 `pc`·`mobile`.

### 3.2 배치 (066 틀 — 내비 '화면' 현재 표시)
- **페이지 머리**(066 꼴): 제목 '화면' · 기간 프리셋 오늘·7일·30일·90일(주소 `range=1d|7d|30d|90d` = days 1·7·30·90, 기본 7d) · 비교 단추 꺼짐(title '이 자료는 비교 없음' — 피드가 이전 기간을 주지 않는다) · 갱신 시각 · '새 창으로 ↗'(지금 주소 그대로 새 창, `noopener`).
- **고르기 줄**(머리 아래, 좁으면 줄바꿈): 화면 칩 열 개(이름 + 고른 기간·기기 pv + pv/최대 폭의 옅은 막대, pv 0 은 흐리게) · 기기(PC·휴대폰) · 보기(요소·영역·깊이) · 지표(클릭·마우스 올림·보인 시간 — 보기가 요소일 때만). 주소 `page=&device=&mode=&metric=` — 목록 밖이면 기본값(066 규칙). 기본: 화면 = 고른 기간·기기에서 pv 가장 큰 화면(한 번 고르면 그대로), 기기 pc, 보기 요소, 지표 클릭.
- **몸**: 틀 칸(남은 폭 전부) | 오른쪽 판(360px — 1279px 이하에서는 틀 아래로).
  - 틀: 실제 페이지 iframe — `https://kimptrack.com` + 정적 `/`·`/privacy`·`/kimp-chart`·`/kimp-history` 에 `?kt-overlay=1`, 대시보드 `/app/?tab=<id>&kt-overlay=1`. 속성 053 그대로(`sandbox="allow-scripts allow-same-origin"`·`referrerpolicy="strict-origin"`). 폭 PC 1280·휴대폰 390 을 칸 폭에 맞춰 줄이고(늘리지 않음, CSS 변수 몫 하나), 높이는 창 높이에서 머리·고르기 줄을 뺀 만큼(최소 480px). 틀 위 한 줄 '<화면> · <기기> · <기간> — pv n'(+ ▲ 표본 적음).
  - 오른쪽 판: 탭 셋(066 분할 단추) — ① 요소 ② 영역 ③ 떠난 곳·깊이. 판마다 ⤢ → 066 상세 판(표 전체·정렬·찾기·CSV — 그래프 없음).

### 3.3 값 계산 (고른 화면 × 기기 행)
- 표본 적음 = pv < 5 — 머리 줄·판·꼬리표 모두에 '표본 적음'.
- 요소: 클릭 c = clicks, 100뷰당 cRate = clicks / pv × 100(소수 1자리), 평균 마우스 올림 h = hoverMs / pv / 1000초(소수 1자리 — 휴대폰은 '–'), 평균 보인 시간 v = visMs / pv / 1000초(소수 1자리 — `row:*` 는 '–').
- 요소 단계 L: m = 고른 지표 값(clicks·hoverMs·visMs), 그 화면 요소 중 m 최댓값 대비 L = ⌈m / max × 5⌉(1~5). m = 0 이고 visMs > 0 이면 L 0(보이기만). 그 밖의 m = 0 은 그리지 않는다. 순위 = m 큰 순(같으면 피드 순서). `(기타)` 는 순위·그리기에서 빼고 표 끝에.
- 영역(053 §3.3 그대로): t = ms / pv(초), 도달 r = seen / pv(%, 100 에서 멈춤), c = clicks / pv × 100, L = ⌈t / tmax × 5⌉(t 0 이면 0), 순위 t 큰 순, 거의 안 닿음 r < 20%.
- 떠난 곳: 영역마다 n / Σn(%). 바탕 줄 'pagehide 로 끝난 pv Σn — 전체 pv 의 x%'.
- 깊이: 도달[j] = Σ depth[j..10] / Σ depth × 100(j = 0 은 100) — 'j×10% 지점까지 내려온 pv 비율'. 바탕 줄 'Σ depth pv 기준'. Σ depth = 0 이면 '깊이 기록 없음'.

### 3.4 이름표
- 영역: `AREA_NAMES` — 053 의 표를 그대로 옮긴다(064 가 지운 `git show 0c9e9b9^:web/admin/admin.js` 의 상수 — 화면별 표 + 대시보드 공통 `app`). 없으면 id.
- 요소: `EL_NAMES`(화면 페이지의 이름표 상수 — `AREA_NAMES` 와 함께) — 061 §7 '요소 목록' 의 열쇠 → 한국어 이름. 화면마다 뜻이 다르면 `<화면>|<열쇠>` 를 먼저 찾는다. 틀 열쇠(`row:*`·`period:*`·`fx:*`·`dom:*`·`sort:*`·`interval:*`·`card:*`·`fold:*`·`coin:*`·`live:*`·`window:*`·`dir:*`)는 061 §7 의 이름 꼴로 만든다(예 `row:BTC` → '코인 행 BTC', `period:7d` → '기간 1주').
- 목록에 없는 자동 열쇠: `link:/x` → '링크 /x', `link:mailto` → '메일 링크', `btn:<글>` → '단추 <글>', `faq:<글>` → '질문 <글>', `input:<이름>` → '입력칸 <이름>', `select:<이름>` → '고르기 <이름>', `check:<이름>` → '체크 상자 <이름>', `tab:<id>` → '탭 <이름>'. 그 밖은 열쇠 그대로. `(기타)` → '(기타 — 열쇠 300 넘음)'.
- 글자는 모두 `textContent`(066 의 XSS 규칙).

### 3.5 덮어 보기 (attention.js — 053 §3.4 위에 더함)
- 관리자 → 페이지 `{type:"kt-attention", v:1, label, areas, …}`(053) 에 키 셋을 더한다(없으면 053 동작 그대로):
  - `mode`: `"area"`·`"element"`·`"depth"`.
  - `elements`: `[{area, key, level, rank, tag, small}]` 300개까지 — area 는 영역 id 꼴, key 는 061 열쇠 꼴(1~64자·`|`·제어 문자 없음), level 0~5 정수, rank 1 이상 정수 또는 null, tag 는 꼬리표 글(관리자가 만든다 — 80자에서 자름), small 참거짓. 꼴이 어긋난 항목은 버린다.
  - `depth`: 길이 11 수 목록(0~100 — §3.3 도달) 또는 null.
- 페이지가 그리는 것:
  - `area`: 053 그대로.
  - `element`: 영역 상자는 옅은 회색 테두리만(꼬리표 없음). 요소 상자 = 그 (영역, 열쇠)를 가진 요소마다 — 열쇠는 061 수집과 **같은 함수**로 셈한다(`data-el` 이 있으면 그 요소, 없으면 대화형 요소 자신). 테두리는 053 의 단계 색·굵기, 채움은 같은 색의 옅은 칠(불투명도 0.10 이하 — 큰 요소의 내용을 가리지 않게, 단계 0 은 회색 점선만), 꼬리표 '#순위 값'(값 = tag — 고른 지표 값: 클릭 수·평균 마우스 올림 n.n초·평균 보인 시간 n.n초, 표본 적음이면 끝에 '· 표본 적음' — 이름은 옆 표에)은 순위 20 안만, (영역, 열쇠)마다 하나 — 그 열쇠의 화면에 보이는 첫 요소 위(위가 모자라면 상자 안 위). 순위 차례로 놓고 이미 놓은 꼬리표와 겹치면 꼬리표 높이만큼 아래로 세 번까지, 그래도 겹치면 그 꼬리표만 버린다(상자는 둔다). 글씨 단계별 11·12·13·15·17·17px(0~5). 같은 열쇠 요소 여럿은 상자를 모두. `row:*` 는 지금 DOM 에 있는 행에만. 화면 밖 요소는 건너뛴다.
  - `depth`(정적 페이지만 — 대시보드 화면에서는 무시): 문서 높이를 10칸으로 나눈 가로 띠. 칸 j(j×10%~(j+1)×10%)의 어둡기 = (100 − 도달[j+1]) / 100 × 0.55(회색 — 안 내려온 만큼 어둡게), 칸 위 경계 왼쪽에 꼬리표 'j×10% · 도달 n%', 문서 끝에 '끝까지 n%'. 스크롤·문서 높이 바뀜에 다시.
- 페이지 → 관리자 `kt-attention-ready`(053) 에 `elements`: 지금 크기 있는 요소의 `[[영역, 열쇠], …]`(행 `row:*` 빼고, 300개까지). 모임이 바뀔 때만 다시(053 의 1초 규칙).
- 관리자 → 페이지 `{type:"kt-attention-focus", id}`(053) 에 `key` 를 더하면 그 영역 안 그 열쇠의 첫 요소를 가운데로 옮기고 2초 굵게. `key` 가 없으면 053 그대로(영역). 옮기기는 틀 문서 안에서만 — 요소의 스크롤 상자 조상(overflow auto·scroll 이고 넘치는 것)의 scrollTop·scrollLeft 를 안쪽부터, 마지막에 틀 창의 `window.scrollTo`(세로 가운데·가로는 가까운 쪽). `scrollIntoView` 는 쓰지 않는다 — 관리자와 공개 사이트가 같은 사이트라 관리자 문서까지 굴린다(2026-10-14 브라우저 확인).
- 열쇠 찾기는 DOM 바뀜에 1초에 한 번(053 의 영역 찾기와 같이), 그리기는 프레임당 한 번. 누름 막기·세지 않음·Clarity 끔·출처·창 확인은 053 그대로.

### 3.6 오른쪽 판
- **요소**: 표(순위·이름·영역·지표 막대(값/최대 폭)·클릭·100뷰당·마우스 올림·보인 시간) — 고른 지표 큰 순, 위에 영역 칩(전체·영역별)으로 거르기. 행을 누르면 focus(`id`·`key`)와 그 행 강조. 끝에 흐리게: '지금 화면에 없음'(ready 의 elements 에 없는 열쇠 — `row:*` 는 늘 있는 것으로, ready 를 받기 전에는 판정하지 않음), `(기타)`, 피드가 300 을 채웠으면 '그 밖은 피드 상한 300'. 판 바닥 '열쇠 n개 · 지금 화면에 없음 m개'.
- **영역**: 053 순위 목록(이름·평균 보인 시간 막대·도달률·100뷰당 클릭·거의 안 닿음 ▲) — 행 → focus(`id`). '기록 없음'·'지금 화면에 없음' 규칙은 053 §3.3.
- **떠난 곳·깊이**: 떠난 곳 상위 목록(066 상위 목록 꼴 — 영역 이름·%·n), 깊이 계단 막대(가로 11줄 0%~100% 지점, 막대 = 도달[j], 도달이 처음 50% 밑으로 가는 줄에 '절반이 여기 전에 떠남'). 대시보드 화면은 깊이 자리에 '탭 화면은 깊이 없음'.
- 상세 판(⤢): 판의 표 전체 — 정렬·찾기·CSV(`kimptrack-screens-<화면>-<기기>-<range>.csv`, 066 CSV 규칙). 요소 표는 열쇠 원문 칸을 더한다.

### 3.7 부르기·주기
- 화면·기간을 바꿀 때만 곧바로 `days=&page=` 하나. 화면 칩의 pv 는 `page` 없는 같은 기간 응답 하나(기간을 바꿀 때만). 그 밖에는 보이는 동안 5분마다(066 공통 규칙 — 만료·401 한 번 새로고침·실패면 마지막 값 흐리게).
- 틀 주소는 화면·기기를 바꿀 때만 바꾼다. 값 메시지는 ready 를 받을 때마다·보기·지표·값이 바뀔 때마다. 5초 안에 ready 가 없으면 틀 칸에 '페이지가 응답하지 않음'.

### 3.8 엣지
- `before_gate`·`error`: 066 부분 상태 배지('연결 안 됨'·'불러오지 못함'), 틀은 띄우되 값은 보내지 않는다.
- 기록 없는 화면·기기: 틀은 띄우고 판 자리에 '이 기간 기록 없음'.
- 휴대폰: 마우스 올림 칸 '–', 지표 '마우스 올림' 꺼짐(주소에 있으면 클릭으로).
- 대시보드 화면: 보기 '깊이' 꺼짐(title '탭 화면은 깊이 없음' — 주소에 있으면 요소로).
- 캐시된 옛 attention.js 는 새 키를 모른다 — 영역만 그린다(오류 없음).

## 4. 검증
- node(066 방식 — 가짜 DOM·가짜 echarts): §3.3 계산(cRate·h·v·L·L 0·`(기타)`·small·휴대폰 h·행 v), 지표 바꿈 → 단계·순위, 이름표(EL_NAMES·틀 열쇠·자동 열쇠 여덟·`(기타)`), 주소 인자 → 처음 값·목록 밖 기본값·대시보드 mode=depth·휴대폰 metric=hover, 화면·기간 바꿈이 부르는 경로, 떠난 곳 %·깊이 도달·'절반' 줄, ready 의 elements → '지금 화면에 없음'(행 제외·ready 전 판정 없음), 5초 무응답, CSV 글자, 표·꼬리표 글자 XSS(`<img onerror>` 가 글로만).
- attention.js node(053 방식): 새 키 꼴 검사(어긋난 요소 버림·tag 80자), mode 셋, 요소 상자 = (영역, 열쇠) 맞는 요소 모두(data-el·자동 열쇠·행), 상위 20 꼬리표·단계별 글씨, depth 띠 10·꼬리표·대시보드 무시, ready 의 elements(행 빼고·300), focus 의 key, 새 키 없는 메시지 → 053 그대로(기존 test_attention_overlay 통과).
- 정적: `screens.html` 이 066 내비·머리·공통 모듈을 쓰고 '준비 중' 없음, 오른쪽 탭 셋, `EL_NAMES` 가 `tests/test_attention_elements.py` 의 화면별 열쇠(틀 열쇠는 규칙)를 모두 덮는다, `AREA_NAMES` 가 052 영역 id 를 모두 덮는다, 틀 속성·postMessage 대상 출처 글자 그대로 `https://kimptrack.com`, 인라인 스크립트 없음·CSP 그대로.
- 가짜 피드 서버: attention 응답(page 있음/없음·요소 300 + `(기타)`·휴대폰·before_gate·XSS 열쇠).
- 기존 전부: `cd server && ruff check . && ruff format --check . && pytest -q`, `node --check web/admin/*.js web/public/attention.js`, `cd web && npm run lint && npm run build`.
- 설계 세션(헤드리스 Chrome — 053 의 시험 사본·가짜 피드): 랜딩·대시보드 스프레드·기록에서 요소 상자가 요소에 맞고 상위 꼬리표, 랜딩 깊이 띠, 표 행 누름 → 그 요소, 새 창 주소가 같은 화면, 1440·1024·390 가로 넘침 0, CSP 위반 0.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 2026-10-14, worktree marketLens-exec-065 (feat/065-admin-screens — 스펙 7d74c32 위, origin/feat/066-admin-renewal 4190a21 을 merge e6ae537,
#   066 이 main 에 들어온 뒤(#131, 9102fa6) origin/main 을 merge — 둘 다 충돌 없음). uv 는 Bash 샌드박스 밖(캐시 권한), uv 가 만든
#   server/uv.lock 은 커밋하지 않고 끝에 지웠다. 브라우저·Docker 는 쓰지 않았다(지시 — nginx 설정은 고치지 않았다)
cd server && uv sync --extra dev
cd server && uv run --extra dev ruff check . && uv run --extra dev ruff format --check . && uv run --extra dev pytest -q -rs
#   All checks passed! · 429 files already formatted · 2591 passed, 1 skipped(test_geo_perf — DBIP_DIR 없음, 065 와 무관)
#   새 시험 27 — test_attention_overlay_v2.py 8 · test_admin_screens_calc.py 7 · test_admin_screens_page.py 7 · test_admin_screens_static.py 4 ·
#   test_admin_fake.py 1(attention 응답), 고친 066 시험의 화면 경우(test_admin_xss·fake·pages·screen·scripts). 053 의 test_attention_overlay.py 8 은 손대지 않고 통과
for f in web/admin/*.js web/public/attention.js; do node --check "$f"; done   # 29개 — node 18.12.0·26.4.0 둘 다 통과
cd web && npm ci && npm run lint && npm run build   # oxlint 종료 0 · tsc -b && vite build ✓ (index 250.81 kB · gzip 78.53 kB — web/admin 은 빌드 밖)
uv run web/scripts/admin-fake-feeds.py --port 18999  # /screens.html·/screens.js 200(CSP·X-Frame-Options DENY·no-store), attention days=7 15행,
#   page=landing 26요소 × 기기 둘, days=5&page=nope → 7일·15행(요소 없음), /__scenario/empty → before_gate, problem → error·redis,
#   bands → 랜딩 요소 300(그중 (기타) 1), xss → `<img onerror>` 열쇠와 영역 id 꼴이 아닌 항목
# 시험이 정말 잡는지 — 스크래치 변형(파일 하나를 바꾸고 그 시험을 돌린 뒤 되돌림) 26개 모두 실패:
#   attention.js — 상위 20 꼬리표·요소 글씨·ready 의 행 빼기·깊이 어둡기 칸·대시보드 깊이·tag 80자·focus 의 첫 요소·ready 300·화면 밖 건너뜀 /
#   screensmodel·names — 단계 올림·m = 0 순위·행은 늘 있음·휴대폰 h·그릴 것만 싣기·거의 안 닿음 20%·깊이 j = 0·화면별 이름표 /
#   screens.js — ready 전 보내지 않음·기기 바꿈 다시 띄우기·출처·창 검사·5초 무응답·대시보드 깊이 → 요소·숨김 about:blank·같은 값 다시 보내지 않음·다른 화면의 ready
```
- 돌리지 않은 것: §4 마지막 줄(설계 세션 — 헤드리스 Chrome·053 의 시험 사본·가짜 피드). nginx 를 고치지 않아 `nginx -t` 는 없다.

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 web 칸 끝에 "· 화면 분석 페이지(065 — screens.html, 영역·요소·깊이 덮어 보기, 요소 순위·떠난 곳·깊이)".
- `CLAUDE.md` — 스펙 인덱스 065 행 DONE.
- `docs/context/architecture.md` — 덮어 보기 메시지에 `mode`·`elements`·`depth`·focus `key`.
- `docs/context/product.md` — 용어 '덮어 보기' 에 요소·깊이.
- `docs/specs/053-admin-attention.md` — 머리에 "화면은 065 — 덮어 보기 메시지는 065 §3.5 가 넓혔다".
- `docs/specs/066-admin-renewal.md` — §3.3 '화면' 줄을 "065 가 만든다" 로.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - web: `web/admin/screens.html`('준비 중' 대신 — 066 틀·상세 판 + 고르기 줄·틀 칸·오른쪽 판, ECharts 는 싣지 않음), `screens.js`(페이지 — 고르기·틀 띄우기·ready·값 보내기), `screensmodel.js`(§3.3 계산·주소 인자·지금 보기 한 장·값 메시지), `screensnames.js`(`AREA_NAMES`·`EL_NAMES`·틀/자동 열쇠 규칙), `screenspanel.js`(오른쪽 판 셋), `screensdetail.js`(상세 판 셋), `admin.css`(화면 절), 066 틀에 더한 것 — `app.js`(`params`·`setParams`·`slowMs`·받은 값으로 생긴 열쇠 곧바로 부르기·'새 창으로' href), `view.js`(페이지 주소 인자), `common.js`(경로 `attention`·틀 칸 CSS 변수 `frameVars`), `detail.js`(표의 CSV 파일 이름 `file`). `web/public/attention.js`(덮어 보기 `mode`·`elements`·`depth`·focus `key`·ready `elements` — 열쇠는 세기와 같은 `elementKey`, 켜는 조건을 열쇠 함수 뒤로). `web/scripts/admin-fake-feeds.py`(attention 응답).
  - 시험(`server/tests/`): 새 `test_attention_overlay_v2.py`·`test_admin_screens_calc.py`·`test_admin_screens_page.py`·`test_admin_screens_static.py`, 고친 `admin_dom.py`(style.setProperty·clientWidth·getBoundingClientRect·window.innerHeight)·`test_admin_screen.py`·`test_admin_scripts.py`·`test_admin_pages.py`·`test_admin_xss.py`·`test_admin_fake.py`.
  - 문서: 이 스펙, `CLAUDE.md`(065 DONE), `docs/context/status.md`·`architecture.md`·`product.md`·`dev-setup.md`, `docs/specs/053-admin-attention.md`(머리)·`066-admin-renewal.md`(§3.3 화면 줄).
- 추측한 지점 / 실행 중 함께 고친 스펙 절 (사람 없이 가장 보수적인 쪽 — 줄 끝은 버린 대안):
  1. §3.4 `EL_NAMES` 자리 문구를 '(screens.js 상수)' → '(화면 페이지의 이름표 상수 — `AREA_NAMES` 와 함께)' 로 고쳤다 — `screensnames.js` 하나에 JSON 꼴로 둘(정적 시험이 읽는다). (버림: screens.js 안 — 싣는 순간 페이지가 시작돼 node 시험이 이름표만 부를 수 없다.)
  2. ready 의 `elements` 는 크기 있는 요소가 하나라도 있을 때만 싣는다(053 꼴 그대로 — 053 시험이 ready 를 글자 그대로 본다). 없으면 관리자는 '지금 화면에 없음' 을 판정하지 않는다(옛 attention.js 와 같다). 요소 찾기는 문서 전체(`data-el` + 대화형)에서 부모의 가장 가까운 영역(세기는 영역마다 찾지만 영역이 겹치지 않아 같다)·1초 회차에 다시, 그리기는 그 목록에 `getBoundingClientRect` 만. (버림: 늘 빈 목록도 싣기.)
  3. 단계·순위는 지금 화면에 있는 요소끼리(ready 전에는 모두 — 053 §7 의 영역 규칙과 같다), 순위는 m > 0 인 것만(m = 0 은 '–', 단계 0 은 회색 점선만·꼬리표 없음). 지금 화면에 없는 요소는 같은 최대 대비 단계(5 에서 멈춤)·순위 없이 끝에. h 는 행에도 센다(문구대로 — 0.0초), v 만 행 '–'. (버림: 피드 전체로 순위 — 바뀐 옛 열쇠가 1위면 틀에 #1 이 없다; m = 0 에도 순위 — '#15 · 클릭 0'.)
  4. 꼬리표 글 = '<이름> · 클릭 n · 100뷰당 x'·'<이름> · 마우스 올림 평균 n초'·'<이름> · 보인 시간 평균 n초'(페이지가 앞에 '#순위 · ', 표본 적음이면 뒤에 ' · 표본 적음'). 요소 꼬리표는 상자 위(위가 모자라면 상자 안 위), 상자는 넘침을 자르지 않는다. focus 받은 요소는 값이 없어도 2초 굵은 테두리. 값 꼴: mode 목록 밖 → area, elements 가 배열이 아니면 없음, depth 꼴이 틀리면 null, small 은 참거짓만.
  5. 깊이 보기: 영역 상자 없이 띠만(rgba(17,24,39,α)·칸 위 1px 점선), '끝까지 n%' 는 마지막 띠 아래 왼쪽, 도달은 소수 1자리로 보내고 꼬리표는 정수 %. 대시보드·꼴이 틀림·Σ = 0 이면 아무것도 그리지 않는다. 문서 높이 바뀜은 `ResizeObserver(documentElement)`(있을 때).
  6. 주소: `page` 기본 null(기본 화면을 따름 — 고르면 주소에 남아 그대로), `metric` 값 `clicks`·`hover`·`vis`, `mode` `element`·`area`·`depth`. 대시보드의 depth·휴대폰의 hover 는 그리면서 주소를 고쳐 쓴다(새로 부르지 않는다 — `setParams(…, quiet)`). 화면 칩 차례는 §3.1(정적 넷 → 탭 여섯), 같은 pv 면 앞, 응답이 없거나 실패·시행 전·모두 0 이면 랜딩, 응답을 기다리는 동안은 직전 화면.
  7. 부르기: 066 refresh 규칙 그대로 — 이미 받은 (기간, 화면)은 되돌아와도 곧바로 다시 부르지 않고 5분 회차가 부른다. 기본 화면이 page 없는 응답 뒤에 정해져 app.js 에 '받은 값으로 새 열쇠가 생기면 곧바로'(settle 뒤 — 새로고침을 기다리는 동안은 안 함)를 더했다, 느린 주기는 페이지가 `slowMs`(화면 5분).
  8. 틀: 이 문서가 숨은 동안 about:blank, 다시 보이면 그 화면(053 그대로 — 숨은 탭의 대시보드 틀 WebSocket 이 계속 받지 않게). 기기를 바꾸면 같은 주소도 다시 띄우고, 다시 띄운 틀은 새 ready 전에는 값을 받지 않는다. 같은 값 메시지는 다시 보내지 않고 ready 마다 다시. 틀 속성은 053 그대로(`loading="lazy"` 포함). (버림: 숨어도 그대로 둠.)
  9. 틀 칸: 폭 몫 `--fit` 에 더해 높이 `--stage-h`(창 높이 − 칸 위쪽 − 24px, 최소 480px)도 screens.js 가 준다 — 둘 다 common.js `frameVars` 한 곳(화면이 style 을 만지는 셋째 자리). 휴대폰 틀은 390 × 몫 폭으로 가운데. (버림: CSS 만 — 머리 높이가 줄바꿈으로 바뀐다.)
  10. '새 창으로 ↗' 은 `a`(target=_blank·rel=noopener) — app.js 가 그릴 때마다 href 를 지금 보기의 `urlOf`(relogin 표시 뺌)로. 066 정적 시험의 '새 창 없음'·href 자리에 이 하나만 예외. (버림: window.open 단추 — 링크 복사·가운데 누름이 안 된다.)
  11. 상세 판: 그래프가 없어 ECharts 를 싣지 않는다(정적 시험의 라이브러리 줄 예외). CSV 파일 이름은 스펙 그대로 판 셋이 같다. 떠난 곳·깊이 판은 한 표(구분·항목·영역 id·pv·몫·도달 — 깊이 줄 pv = 그 칸에서 멈춘 pv). 순위 칸은 CSV 에서 수(066 규칙).
  12. 오른쪽 판: 탭은 066 분할 단추(`.seg`)·주소에 싣지 않음(기본 요소). 영역 칩은 표만 거른다(틀은 그대로) — 차례는 행의 영역(ms 큰 순) 뒤 요소에만 있는 영역, 바닥 줄 수는 거른 표 기준((기타) 뺌), 행 title 은 열쇠 원문, 강조는 마지막으로 누른 행. 깊이 판 문구: 대시보드 '탭 화면은 깊이 없음', 정적 페이지의 자료 없음·Σ = 0 '깊이 기록 없음', 계단 줄 이름 '0%'~'100%', '절반' 은 그 줄 아래 한 줄. 표본 적음은 머리 줄 ▲(0 < pv < 5)·판 바탕 한 줄·꼬리표.
  13. 피드의 `before_gate` 는 상태라 066 배지 규칙대로 unconfigured·before_gate 로 읽는다('연결 안 됨', code 없음). 기록 없는 화면·기기도 ok 면 값을 보낸다(영역 '기록 없음' 상자).
  14. 가짜 피드: ok·old·missing·expired 는 정상, problem 은 error(redis), empty 는 before_gate, nobands 는 기록 없음, bands 는 랜딩 요소 300(그중 (기타) 1), xss 는 `<img onerror>` 열쇠 + 영역 id 꼴이 아닌 항목. pv 는 랜딩 PC 가 가장 크게(기본 화면), 선선갭 PC 3(표본 적음), 휴대폰 몇 화면은 0.
  15. 함께 고친 다른 기능의 시험: 066 의 test_admin_screen(화면 페이지 상세 판·라이브러리 없음·기간 넷·새 창 예외)·test_admin_scripts(경로 표 attention·style/href 자리·틀 예외 둘·'처리방침' 이름표 하나)·test_admin_pages(부르는 경로·관리자 분기)·test_admin_xss·test_admin_fake(화면 경우), admin_dom 가짜. 053 의 test_attention_overlay.py 는 손대지 않았다.
- 남은 빚:
  - 브라우저 확인(설계 세션 — §4 마지막 줄): 랜딩·대시보드 스프레드·기록에서 요소 상자가 요소에 맞는지·상위 꼬리표 자리와 글씨·겹침, 랜딩 깊이 띠, 표 행 누름 → 그 요소, 새 창 주소가 같은 화면, 1440·1024·390 가로 넘침 0, CSP 위반 0. 로컬은 053 의 시험 사본(attention.js 의 `HOST`·`ADMIN_ORIGIN`, screens.js 의 `SITE_ORIGIN` 을 바꾼 커밋하지 않는 사본)으로.
  - 가까운 요소가 여럿이면 상위 20 꼬리표가 겹칠 수 있다. 행이 많은 대시보드(스프레드)는 요소 찾기가 1초마다 문서 전체를 돈다 — 프레임 시간은 브라우저 확인에서.
  - ready 의 elements 가 비면 판정하지 않는다 — 요소가 없는 화면(수집 상태 탭 등)은 피드의 옛 열쇠를 '지금 화면에 없음' 으로 가르지 못한다.
  - CSV 파일 이름이 판 셋에서 같다(스펙 그대로 — 브라우저가 (1) 을 붙인다).
