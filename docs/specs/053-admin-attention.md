# 053 — admin-attention

상태: TODO | 의존: **052 attention·042 admin-traffic(PR #107) 이 main 에 머지된 뒤**(피드 `/admin/attention`·`attention.js`·`data-area`, 042 의 답 줄 `p.answer` 꼴·node 확인 방식 `test_admin_traffic.py`). 041 의 절 설명 틀·036 의 부분 상태 칸을 쓴다. 043 과 따로 간다(§2 바꾸는 것).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 관리자 화면에서 기간·화면·기기를 고르면, **실제 우리 화면** 위에 영역마다 얼마나 보였는지가 겹쳐 그려진다 — 많이 본 영역은 진한 색·큰 글씨, 적게 본 영역은 옅은 색·작은 글씨, 거의 안 닿은 영역은 빗금. 옆 목록은 같은 값을 순위로 보인다.

## 2. 범위
- 만드는 것: `web/admin/` 세 파일의 새 절 `#screens`('화면 이용'), `web/public/attention.js` 의 덮어 보기 모드, clarity.js 의 덮어 보기 건너뛰기 한 줄, 공개 nginx 의 다섯 페이지 `frame-ancestors`, 관리자 nginx `/` 의 `frame-src`, 038 운영자 흔적 출처에 `admin.kimptrack.com`, 테스트.
- 하지 않는 것: 서버·피드(052 그대로 읽기만). 스크린숏·미리 잰 좌표 표(실제 페이지를 띄워 그 자리에서 잰다). 녹화·좌표 히트맵.
- 바꾸는 기존 것: 038 §3.x 운영자 흔적 출처 목록(덮어 보기로 띄운 페이지를 방문으로 세지 않게 — 줄 분류 코드와 그 테스트도), 036 §3.x 절 목록(일곱 → 여덟)·nav, 041 의 절마다 '이 절 읽는 법'(새 절 하나), 029·036 관리자 CSP, 032·044·022 정적 페이지의 `frame-ancestors 'none'`. 043 §3.4 랜딩 도달 추정(평균 스크롤 깊이 모형)은 이 절이 실제 값을 보이므로 **043 에서 지운다**(설계 세션이 이 스펙 PR 에서 함께 고친다 — 043 은 아직 TODO).

## 3. 동작

### 3.1 읽는 값 (052 복사)
`/svc/api/admin/attention?days=1|7|30|90` → `{state, code, gateAt, days, from, to, fetchedAt, rows}`. `state` `ok`·`before_gate`(rows 빔)·`error`(code `redis`). `rows` = `[{page, device, pv, areas:[{id, ms, clicks, seen}]}]` — 창 안 날의 합, ms 큰 순. 화면 이름: `landing`·`app-spread`·`app-history`·`app-gap`·`app-pp`·`app-health`·`app-flow`·`privacy`·`kimp-chart`·`kimp-history`. 기기 `mobile`(폭 < 768)·`pc`.

### 3.2 절 배치·고르기
- 새 절 `#screens` 는 `#traffic` 다음, `#cost` 앞. nav 에 '화면'. 머리 아래 041 꼴 '이 절 읽는 법'(접힘): 동의한 방문자만 센다·영역이 화면 높이 절반(작은 영역은 자기 절반) 넘게 보이고 문서가 보이며 5분 안에 입력이 있었던 시간만·하루 합계라 한 사람을 따라갈 수 없다·D 전은 비어 있다.
- 고르는 줄(HTML 고정): 기간 `<select>`(오늘·7일·30일·90일 — 기본 7일), 화면 `<select>`(한국어 이름 — 랜딩·대시보드 스프레드·기록·갭·선선갭·수집 상태·입출금 레이더·처리방침·김프 차트·김프 기록. 고른 기간에 pv 가 있는 화면은 이름 뒤 '(n)', 없으면 '(0)'), 기기 단추 둘(PC·휴대폰 — `aria-pressed`). 기본 화면 = 고른 기간·기기에서 pv 가 가장 큰 화면.
- 고른 값은 JS 변수에만(주소·저장소에 쓰지 않는다). 기간을 바꾸면 피드 하나만 곧바로 부른다(떠 있으면 끝난 뒤 한 번 — 마지막 값). 화면·기기를 바꾸면 부르지 않고 다시 그린다. 그 밖에는 절이 보이는 동안 5분마다.

### 3.3 값 계산 (화면 하나 × 기기 하나)
- pv = 그 행의 pv. 영역마다: 평균 보인 시간 `t = ms / pv`(초, 소수 1자리), 도달률 `r = seen / pv`(%, 정수 — 100 넘으면 100), 클릭 `c = clicks / pv × 100`(100뷰당, 소수 1자리).
- 강도 단계 L: `t` 가 그 화면에서 가장 큰 영역의 t 대비 몫 `s = t / tmax` — L = ⌈s × 5⌉(1~5), t = 0 이면 0. 순위 = t 큰 순.
- 거의 안 닿음 = r < 20%. 표본 적음 = pv < 5 — 이때 단계·순위는 그리되 모든 표시에 '표본 적음' 을 붙인다.
- 데이터에 없는데 페이지에 있는 영역 = '기록 없음'(L 0). 데이터에 있는데 페이지에 없는 영역(바뀐 화면) = 목록 끝 '지금 화면에 없음'.

### 3.4 실제 화면 위에 그리기
- 틀: 절 안 왼쪽(넓은 화면) 또는 위(좁은 화면)에 `<iframe>` 하나 — 주소 `https://kimptrack.com<경로>?kt-overlay=1`(대시보드는 `/app/?tab=<id>&kt-overlay=1`), 폭 PC 1280·휴대폰 390, 높이 PC 800·휴대폰 844 로 놓고 CSS `transform: scale()` 로 칸 폭에 맞춘다(휴대폰은 1배 이하). `sandbox="allow-scripts allow-same-origin"`, `referrerpolicy="strict-origin"`, `loading="lazy"`. 화면·기기를 바꾸면 주소를 바꾼다.
- 출처 상수: admin.js 의 `SITE_ORIGIN = "https://kimptrack.com"`, attention.js 의 `ADMIN_ORIGIN = "https://admin.kimptrack.com"` — 각 파일에 한 번. 로컬 확인은 커밋하지 않는 시험 사본에서 둘을 `http://kimptrack.localhost:8080`·`http://admin.kimptrack.localhost:8081` 로 바꾸고 attention.js 의 호스트 검사도 같은 사본에서 바꾼다(033 §4 의 시험 사본과 같은 방식).
- 주고받기(postMessage, 둘 다 상대 출처·창을 확인 — 다르면 무시):
  - 페이지 → 관리자(`ADMIN_ORIGIN`): `{type:"kt-attention-ready", v:1, page, areas:[id…]}`(지금 페이지에 크기 있는 영역 id 들 — §3.3 '기록 없음'·'지금 화면에 없음' 판정에 쓴다). 문서를 읽은 뒤 한 번, 그 뒤 영역 모임이 바뀔 때마다 다시(MutationObserver, 1초에 한 번까지) — 대시보드는 React 가 defer 스크립트 뒤에 그리고 기록 탭은 늦게 붙어 첫 목록이 빌 수 있다. 관리자는 받을 때마다 값을 다시 보낸다.
  - 관리자 → 페이지(`https://kimptrack.com`): ready 를 받은 뒤와 값이 바뀔 때마다 `{type:"kt-attention", v:1, label, areas:[{id, level, rank, t, r, c, low, small}]}`, 목록 행을 누르면 `{type:"kt-attention-focus", id}`.
- 덮어 보기 모드(`attention.js`): 주소에 `kt-overlay=1` 이 있고 `window.top !== window` 일 때만. 이 모드에서는 세기·보내기를 하지 않고, clarity.js 도 Clarity 를 부르지 않고 띠도 띄우지 않는다(같은 조건 한 줄). 받은 값으로:
  - 누름 막기: window 캡처 단계에서 `click`·`auxclick`·`submit`·`dblclick` 을 `preventDefault`+`stopPropagation` — 링크·탭 단추·버튼이 동작하지 않아 화면이 바뀌지 않는다(화면은 관리자 고르기로만 바꾼다). 휠·터치 스크롤은 그대로(표 안 스크롤 포함).
  - 페이지 맨 위에 고정 층 하나(`pointer-events: none`, 가장 위 z) — 영역마다 상자 하나를 그 요소의 `getBoundingClientRect` 자리에 둔다. 스크롤·크기 바뀜·DOM 바뀜에 맞춰 다시 놓는다(프레임당 한 번까지). 숨은 요소(크기 0)는 그리지 않는다.
  - 상자: 단계 1~5 의 채움 색(옅은 파랑 → 노랑 → 주황 → 빨강, 불투명도 0.18~0.5)과 2~4px 테두리, 단계 0 은 회색 점선 테두리만. 거의 안 닿음은 회색 빗금 채움을 더한다.
  - 꼬리표: 상자 왼쪽 위 '#순위 · 평균 n초 · 도달 n% · 클릭 n' — **글씨 크기는 단계에 따라 12·14·16·19·22px**(많이 본 곳이 크게). 표본 적음은 꼬리표 끝 '· 표본 적음', 기록 없음은 '기록 없음'. 글자는 `textContent` 로만.
  - focus 를 받으면 그 영역을 `scrollIntoView({block:'center'})` 하고 상자 테두리를 2초 굵게.
  - 값이 오지 않으면(5초) 아무것도 그리지 않는다 — 관리자 쪽이 '페이지가 응답하지 않음' 을 보인다.
- 덮어 보기로 띄운 페이지의 요청은 038 운영자 흔적이라 방문으로 세지 않는다(줄 분류 `is_operator` 의 출처 목록 — 채널용 `SELF_HOSTS` 가 아니다) — 문서 요청의 `referer` 출처가 `admin.kimptrack.com` 이고(틀은 `strict-origin`), 그 줄이 있는 짝은 그날 방문에서 빠진다(038 그대로). 출처 목록에 이 호스트를 더하는 것이 이 스펙의 일이다. 대시보드는 실시간 WS 를 연다 — 절이 보이는 동안만 틀을 두고, 절이 화면 밖으로 나가거나 관리자 탭이 숨으면 틀의 주소를 `about:blank` 로 바꾼다.

### 3.5 옆 목록·답 문장
- 답 문장(042 `p.answer` 꼴): '{기간} {화면} {기기} — 페이지뷰 {pv}. 가장 오래 본 곳은 {이름}(평균 {t}초), 가장 덜 닿은 곳은 {이름}(도달 {r}%).' pv 0 이면 '이 기간 이 화면·기기의 동의한 방문 기록이 없다.'
- 목록(HTML 표, 순위 순): 순위 · 영역 이름 · 평균 보인 시간(막대 — 단계 색) · 도달률 · 100뷰당 클릭. 행을 누르면 focus. '지금 화면에 없음' 행은 끝에 회색.
- 영역 이름표: admin.js 상수 `AREA_NAMES` — 052 §7 에 적힌 화면별 영역 id 전부의 한국어 이름(없으면 id 그대로). 덩어리 끝 '이 칸 뜻'(041 꼴): 평균 보인 시간·도달률·100뷰당 클릭·단계·표본 적음의 정의.

### 3.6 상태·빈 칸
- 036 부분 상태 칸 그대로(연결 안 됨·불러오지 못함·첫 조회 중). `before_gate`: 'D 부터 모읍니다(처리방침 v3 시행일)' — D 는 `gateAt` 의 KST 날짜. 틀은 띄우지 않는다.
- 피드 실패에 직전 값이 있으면 그대로 그리고 머리에 '불러오지 못함'.

### 3.7 헤더
- 공개 nginx: `/`·`/app/`(index.html 을 주는 location 들)·`/privacy`·`/kimp-chart`·`/kimp-history` 에 `frame-ancestors 'self' https://admin.kimptrack.com` — CSP 가 있는 세 정적 페이지는 052 가 고친 문자열의 `frame-ancestors 'none'` 만 바꾸고(test_privacy·test_kimp_pages 기대값도), 없는 곳은 이 지시어 하나짜리 CSP 를 더한다. `X-Frame-Options` 는 두지 않는다(있으면 지운다 — CSP 와 다르면 브라우저가 막는다).
- 관리자 nginx `location /` CSP: `default-src 'self'; frame-src https://kimptrack.com; frame-ancestors 'none'`.

## 4. 검증
- `server/tests/test_admin.py`(정적): `#screens` 가 `#traffic` 뒤·`#cost` 앞, nav, 고르는 줄 셋, 041 설명·'이 칸 뜻', `AREA_NAMES` 가 052 의 영역 id(정적 페이지 HTML·web/src 의 `data-area`)를 모두 덮는다, iframe 속성(sandbox·referrerpolicy), postMessage 대상 출처가 글자 그대로 `https://kimptrack.com`.
- `test_admin_attention.py`(node, 042 방식): §3.3 계산(t·r·c·L·순위·low·small·100 넘는 r), 기본 화면 고르기, 기간 바꿈만 부르기·떠 있으면 한 번, 출처가 다른 메시지 무시, 답 문장 세 갈래.
- `attention.js` 덮어 보기(node 가짜 DOM): 조건 둘 다일 때만 켜짐·세기 리스너 0, 누름 넷이 막힘, 영역 모임이 바뀌면 ready 다시, 다른 출처 메시지 무시, 상자 수 = 크기 있는 영역 수, 단계별 글씨 크기, low 빗금·small 꼬리표, focus 스크롤, ready 메시지에 영역 id. clarity.js: `kt-overlay=1`+틀 안이면 Clarity·띠 없음.
- 038 줄 분류: `referer` 가 `https://admin.kimptrack.com` 인 줄은 `operator`, 그 짝은 그날 방문에서 빠진다(기존 운영자 흔적 테스트에 한 경우).
- nginx 계약 테스트: 다섯 location 의 frame-ancestors 값, `X-Frame-Options` 없음, 관리자 CSP 값.
- 기존: `cd server && ruff check . && ruff format --check . && pytest -q`, `node --check web/admin/admin.js web/public/attention.js`, `cd web && npm run lint && npm run build`.
- 설계 세션(브라우저): 로컬 관리자 화면 + 가짜 피드 + `kimptrack.com` 을 로컬에 묶은 페이지로 — 랜딩 PC·휴대폰, 대시보드 스프레드·기록 탭에서 상자가 영역에 맞고 스크롤에 따라가며, 단계별 크기·색이 보이고, 행 누름이 그 영역으로 간다. 폭 360~1440 가로 넘침 0. 배포 뒤 admin.kimptrack.com 에서 틀이 막히지 않는다(CSP 위반 0).

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 web 칸 끝에 "· 화면 이용 절(053 — 기간·화면·기기, 실제 페이지 위 영역 강도 덮어 보기·순위 목록)".
- `CLAUDE.md` — 스펙 인덱스 053 행 상태 → DONE.
- `docs/context/architecture.md` — '현재 구조' admin 항목 `web/admin/` 설명 끝에 "화면 이용 절(053) — 실제 페이지를 iframe 으로 띄우고 postMessage 로 값을 넘겨 페이지의 `attention.js` 덮어 보기 모드가 영역 위에 그린다", 계약 규칙 절에 "공개 페이지는 `admin.kimptrack.com` 안에서만 틀에 들어간다".
- `docs/context/product.md` — 용어 절에 "**덮어 보기**(053): 관리자 화면이 실제 페이지 위에 영역별 강도를 그리는 것 — 평균 보인 시간이 가장 큰 영역 대비 다섯 단계".
- `docs/specs/036-admin-v2.md` — 절 목록·nav 에 '화면'(053), 관리자 CSP 문장.
- `docs/specs/041-admin-explain.md` — '일곱 절' → '여덟 절', 새 절의 '이 절 읽는 법' 은 053 §3.2 가 정한다.
- `docs/specs/029-admin.md` — 관리자 `location /` CSP 문장.
- `docs/specs/038-access-v2.md` — §3.x 운영자 흔적 정의의 출처 목록에 `admin.kimptrack.com`(관리자 덮어 보기 — 053).
- `docs/specs/032-privacy.md`·`docs/specs/022-landing.md`(담당 확인 — 022 가 이 레포 주인 담당이 아니면 제안만)·`docs/specs/044-kimp-pages.md`(hereokay — 제안만): CSP `frame-ancestors` 문장.
- 담당자에게 제안(PR 본문): 044·022(담당이 아니면) — "`frame-ancestors` 를 `'self' https://admin.kimptrack.com` 으로 바꿨다(관리자 덮어 보기)."

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
- 남은 빚:
