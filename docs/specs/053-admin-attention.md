# 053 — admin-attention

상태: DONE | 의존: **052 attention·042 admin-traffic(PR #107) 이 main 에 머지된 뒤**(피드 `/admin/attention`·`attention.js`·`data-area`, 042 의 답 줄 `p.answer` 꼴·node 확인 방식 `test_admin_traffic.py`). 041 의 절 설명 틀·036 의 부분 상태 칸을 쓴다. 043 과 따로 간다(§2 바꾸는 것).

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
cd server && uv run ruff check .             # All checks passed!
cd server && uv run ruff format --check .    # 358 files already formatted
cd server && uv run pytest -q                # 1889 passed, 1 skipped(test_geo_perf — DBIP_DIR 없음, 기존) — node 확인 넷 모두 돎:
                                             #   test_admin_attention.py 8 · test_attention_overlay.py 8 · test_clarity.py(덮어 보기 1 더함) · test_admin_traffic.py
node --check web/admin/admin.js web/public/attention.js web/public/clarity.js   # 셋 다 문법 통과
cd web && npm ci && npm run lint && npm run build   # oxlint 경고·오류 0, vite build 통과(index 246.50 kB · gzip 77.09 kB)
```
- §4 의 서버·node·nginx 계약 항목은 위 pytest 안에서 돈다: `test_admin.py`(절 여덟·nav·고르는 줄 셋·설명·`AREA_NAMES` 가 052 영역 전부·틀 속성·postMessage 대상 출처·공개 다섯 페이지 `frame-ancestors`·`X-Frame-Options` 없음·관리자 CSP), `test_admin_attention.py`(§3.3 계산·기본 화면·답 세 갈래·기간 바꿈만 부르기·떠 있으면 한 번·출처/창이 다른 메시지 무시·값과 focus 보내기·5초 무응답·before_gate·직전 값), `test_attention_overlay.py`(두 조건·세기 리스너 0·누름 넷·ready 다시·다른 출처·상자 수·단계별 글씨·빗금·표본 적음·focus), `test_clarity.py`(틀 안 `kt-overlay=1` 이면 Clarity·동의 창 없음), `test_access_classes.py`(admin 출처 = operator·짝 빠짐), `test_privacy.py`·`test_kimp_pages.py`(CSP 문자열).
- 돌리지 않은 것: 브라우저·Docker(§4 마지막 줄 — 설계 세션), `nginx -t`(Docker 없음 — PR 본문에 적을 것).

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
  - web: `web/admin/index.html`(절 `#screens`·nav '화면'·고르는 줄·틀·'이 절 읽는 법'·'이 칸 뜻', 038 운영자 흔적 dd 에 관리자 화면), `web/admin/admin.css`(고르는 줄·틀 칸 `--fit`·목록·단계 색 lv0~lv5), `web/admin/admin.js`(값 계산 `screenValues`·기본 화면 `busiest`·답 `screenAnswer`·이름표 `AREA_NAMES`, 고른 값 `scr`, 접속 창과 같은 `loadLatest`, 틀 `aimFrame`·`fitFrame`·`onFrameMessage`, 목록·빈 칸·머리, 5분 주기·절 보임 `IntersectionObserver`), `web/public/attention.js`(덮어 보기 `overlay()` — 파일 끝, `ADMIN_ORIGIN`), `web/public/clarity.js`(덮어 보기 건너뛰기 한 줄), `web/nginx.conf`(다섯 페이지 `frame-ancestors`), `web/nginx-admin.conf`(`frame-src`).
  - server: `app/features/admin/access_classes.py`(`is_operator` 에 `admin.kimptrack.com`).
  - 테스트: 새 `tests/test_admin_attention.py`·`tests/test_attention_overlay.py`, 고친 `tests/test_admin.py`(절 여덟·053 정적 셋·nginx 셋·`.style`/`.src`/주소 예외)·`tests/test_admin_traffic.py`(가짜 `window`·`IntersectionObserver` 만)·`tests/test_attention.py`(덮어 보기 부분은 관리자 출처 하나·textContent 허용)·`tests/test_clarity.py`(틀·search 가짜 + 한 경우)·`tests/test_privacy.py`(CSP 문자열)·`app/features/admin/tests/test_access_classes.py`(operator 한 경우 + 전용 시험).
  - 문서: context 셋(status·architecture·product), 스펙 029·032·036·038·041, CLAUDE.md 인덱스.
- 추측한 지점 / 실행 중 함께 고친 스펙 절 (사람이 없는 실행이라 가장 보수적인 쪽을 골랐다 — 각 줄 끝은 버린 대안):
  - 덮어 보기도 호스트가 `kimptrack.com` 일 때만 켠다(§3.4 의 '시험 사본에서 호스트 검사도 바꾼다' 를 그렇게 읽었다). (버림: 호스트 무관.)
  - 화면 고르기의 '(n)' 은 고른 기간 × **고른 기기**의 pv — 기본 화면 규칙·틀이 보이는 값과 같게. (버림: 두 기기 합.)
  - 기기 기본값은 PC. 화면을 한 번 고르면 새로고침 전까지 그 화면(기간·기기를 바꿔도), 고르기 전에는 기본 화면을 따른다. (버림: 바꿀 때마다 기본 화면으로.)
  - 단계·순위는 지금 페이지에 있는(첫 ready 전에는 기록 있는) 영역끼리, 단계는 반올림 전 ms 로(⌈ms×5/최대⌉). 같은 ms 는 피드 순서(ms 큰 순·id 순). '지금 화면에 없음' 은 순위 없이 회색으로 끝에(단계는 같은 최대 대비, 5 에서 멈춤). t·c 는 소수 1자리 반올림, r 은 정수 반올림. (버림: 반올림한 t 로 단계.)
  - 목록에 페이지에는 있는데 기록 없는 영역도 '기록 없음' 행으로 넣는다(순위 행 뒤·'지금 화면에 없음' 앞). (버림: 덮어 보기에만.)
  - 답 문장의 '가장 덜 닿은 곳' 은 순위 있는 영역 중 도달률이 가장 낮은 곳(같으면 뒤 순위). 갈래: pv 0 / 보통 / pv 는 있는데 영역 기록 없음('영역 기록은 없다.') / 표본 적음은 끝에 '표본 적음 — 페이지뷰가 5보다 적어 단계·순위가 흔들린다.' 한 문장. {기간} 은 '오늘'·'최근 n일'.
  - '표본 적음' 표시: 답 문장·목록 위 주의 한 줄·덮어 보기 꼬리표마다(기록 없음에도). 목록 행마다는 붙이지 않았다.
  - 덮어 보기 꼬리표 '클릭 n' 의 n 은 100뷰당 클릭(소수 1자리) — §3.5 와 같은 값('이 칸 뜻' 에 적음). 꼬리표는 상자 안에서 잘리고(상자 `overflow: hidden`), 상자 위쪽이 화면 위로 나가면 화면 맨 위에 붙는다. 상자·꼬리표 모양은 CSSOM 속성으로만(`<style>`·`cssText` 없음 — CSP 무관). focus 의 '굵게' 는 테두리 6px.
  - 받은 값은 꼴을 검사한다(단계 0~5 정수·순위 1 이상 정수 또는 null·수 셋) — 어긋나면 그 영역은 '기록 없음'. focus 메시지는 §3.4 글자 그대로 `{type, id}`(v 없음)라 페이지는 값 메시지에만 `v: 1` 을 본다.
  - 틀: 값이 있을 때(ok, 또는 같은 기간의 직전 값이 있는 실패)만 띄운다 — 시행 전·값 없는 실패는 칸을 숨긴다. 기간을 바꿔 새 값을 기다리는 동안은 띄운 페이지를 그대로 둔다(다시 띄우지 않음). 같은 주소라도 기기가 바뀌면 다시 띄운다. 절이 처음 보이기 전에는 about:blank 이고 절 요약은 '보이면 부른다'. ready 는 지금 고른 화면의 것만, 영역 id 는 꼴 검사·중복 제거·40개까지.
  - 부르기: 기간 바꿈 호출은 5분 주기의 다음 시각을 건드리지 않는다(042 와 같다). 화면 이용 경로는 036 의 '여덟 경로' 만료 표시 지우기 셈에 넣지 않는다(셈은 그대로 여덟 — `FAST`·`SLOW` 모두 성공 확인으로 바꿈).
  - D 는 `gateAt` 의 KST 날짜를 `YYYY-MM-DD` 로.
  - 공개 nginx `frame-ancestors` 는 `= /`·`= /app/index.html` 에 더해 `= /index.html`·`/app/` 접두에도 — nginx 가 어느 쪽으로 index.html 을 주어도 붙게. (버림: `= /app/index.html` 하나.)
  - 036 의 `.style`·`.src` 금지: 예외를 `fitFrame`(CSS 변수 `--fit` 을 `style.setProperty` 한 줄)·`aimFrame`(틀 `src` 한 줄 — `SITE_ORIGIN` 으로 만든 주소나 about:blank) 두 함수로 묶고 정적 시험이 지킨다. (버림: 몫마다 CSS 클래스 — 칸 폭에 정확히 안 맞음, `contentWindow.location.replace` — 알아보기 어려움.)
  - `AREA_NAMES` 는 화면별 표 + 대시보드 공통 `"app"`, JSON 꼴(시험이 읽는다). 이름은 화면의 제목·문구에서 짧게.
  - 함께 고친 스펙 절: 029 §3.2 응답 헤더·§4 curl 줄(관리자 CSP), 032 §3.1(CSP `frame-ancestors`), 036 §3.1 표·머리 링크 여덟·§3.6 style 예외·§3.8 CSP·§4 정적 단언(절 여덟·주소 둘·예외 두 함수), 038 §3.4 운영자 흔적 출처, 041 §1·§3.1·§4(여덟 절). 다른 기능의 시험(041·042 의 node 가짜 문서)은 `window`·`IntersectionObserver` 가짜만 더했다.
  - 022(랜딩, hereokay 작성)·044(hereokay) 스펙은 고치지 않았다 — 담당자 제안(PR 본문): "`frame-ancestors` 를 `'self' https://admin.kimptrack.com` 으로 바꿨다(관리자 덮어 보기). 랜딩(`= /`)은 이 지시어 하나짜리 CSP 가 새로 붙는다."
- 남은 빚:
  - 브라우저·Docker 확인 전부(§4 마지막 줄 — 설계 세션)와 `nginx -t`(PR 본문). 로컬 확인은 커밋하지 않는 시험 사본에서 `SITE_ORIGIN`·`ADMIN_ORIGIN`·attention.js 의 `HOST` 를 바꾼다.
  - 같은 틀 창은 주소를 바꿔도 같은 창이라, 기기를 바꿔 다시 띄우는 짧은 틈에 앞 문서가 보낸 ready 가 받아질 수 있다(같은 화면이라 영역 모임은 같다).
  - 시행일(2026-10-18) 전에는 피드가 `before_gate` 라 틀이 뜨지 않는다 — 설계 세션 확인은 가짜 피드로.
  - 043 §3.4 랜딩 도달 추정 지우기는 설계 세션 몫(§2) — 이 세션은 043 을 건드리지 않았다.
