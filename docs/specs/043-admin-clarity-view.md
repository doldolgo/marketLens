# 043 — admin-clarity-view

상태: TODO | 의존: **040 clarity-v2·041 admin-explain·042 admin-traffic 가 main 에 머지된 뒤 시작한다**(아니면 멈추고 묻는다). main 에는 fix/036-admin-followup(지표 이름 정규화·차트 빈 칸 '값 없음'·'값 1개뿐'·본문 경과 글자 다시 쓰기·keep-all)이 있어야 한다. 042 는 같은 세 파일(접속 절)을 고치므로 나란히 가지 않는다 — 덩어리 머리 `div.q-head`·`#traffic` 순서를 042 가 만든다(그래서 038·039 도 main 에 있다). 계약을 쓰는 스펙(쓰는 계약은 §3.1 에 복사했다): 040(Clarity 피드 키), 036(화면 규칙 — 이 스펙이 Clarity 덩어리를 바꾼다), 041(설명 틀·탭 이름표), 042(덩어리 머리·배치), 038(접속 `tabs`·`window`), 033(관리자 단언), 002(탭 id).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 관리자 접속 절 맨 아래 Clarity 덩어리에서 "화면에서 무엇을 했나" 를 읽는다 — 동의한 방문자의 스크롤 깊이·머문 시간·불만 신호·나라, 대시보드 탭마다 들어온 수(서버 기록)와 머문 모양(Clarity). 지금(036)은 Clarity 가 준 이름·키 그대로의 글자 목록뿐이다.

## 2. 범위
- 만드는 것: `web/admin/` 세 파일의 Clarity 덩어리(index.html 고정 틀·admin.js·admin.css), `server/tests/test_admin.py` 정적 단언.
- 하지 않는 것: 서버·API(040 응답을 읽기만). 서버 기록 덩어리 ①~⑥·창 줄(042). 절 설명 틀(041 — 이 스펙은 그 틀에 Clarity 글을 넣는다). 대시보드 스크롤 추정(/app/ 은 문서 스크롤이 없다). 히트맵·클릭 좌표(API 에 없다). 랜딩·화면 영역별 도달과 보인 시간은 053(화면 이용 절)이 실제 값으로 보인다 — 평균 스크롤 깊이로 추정하지 않는다. 나라 이름을 ISO 로 바꾸기. web/admin 새 파일·외부 라이브러리·빌드.
- 바꾸는 기존 것: 036 §3.4 접속 절 Clarity 덩어리·§4 정적 단언, 041 이 쓴 접속 절 '이 절 읽는 법' 의 Clarity 줄. 036·041 은 이 레포 주인 담당이다.

## 3. 동작

### 3.1 읽는 계약 (040·038·036·041·042 복사)
- `/svc/api/admin/clarity`(040) = 부분 하나 `{state, code, fetchedAt, refreshSec, nextAt, numOfDays, traffic, summary, countries, metrics, pages}` — 기본 호출 기준 `refreshSec` 14400·`numOfDays` 1. 값 키는 마지막 성공 값이고 7일 뒤 null 이다(`error` 여도 값이 있으면 그린다 — 036 §3.5).
  - `traffic` `{sessions, botSessions, users, pagesPerSession}`. `summary` `{scrollDepth, totalSec, activeSec, signals}` — `signals` 키 여섯 `deadClick`·`rageClick`·`excessiveScroll`·`quickback`·`scriptError`·`errorClick`, 값 `{sessions, sessionPct, pageViews, count}`, 못 찾은 칸 null. `countries` `[[이름, 세션]]` 20행, 행 키를 못 알아보면 null. `metrics` `[{name, rows}]` 받은 그대로 20행.
  - `pages` 하위 부분 `{state, code, fetchedAt, refreshSec(43200), nextAt, numOfDays(3), rowsIn, rowLimitHit, groups}` — 상태 규칙이 같고(값은 마지막 성공, 7일 뒤 null) 바깥 부분을 error 로 만들지 않는다. `groups` ≤40 `{page, device, sessions, scrollDepth, totalSec, activeSec, deadClickPct, rageClickPct, excessiveScrollPct, quickbackPct, scriptErrorPct, errorClickPct}` — page `landing`·`app:spread|history|gap|pp|health|flow|other`·`privacy`·`other`, device `mobile`·`tablet`·`desktop`·`other`, (page, device)마다 한 행이고 평균·비율은 서버가 세션 가중으로 묶었다.
  - 단위: 깊이·비율은 %, `…Sec` 는 초(Clarity 단위를 초로 본다 — 확인 못 함). 창은 부른 때 직전 `numOfDays` × 24시간이다(달력 하루가 아니고 시간대가 없다).
- `/svc/api/admin/access`(038 — 이 덩어리가 쓰는 것만): `window`(실제로 답한 창), `tabs` `[[id, 수]]` — 사람 브라우저 모양 페이지 줄의 `/app/` 진입 탭(id 여섯·`(기타)`, 키 없으면 `spread`).
- 화면 규칙(036 그대로): 부분 상태 칸(`연결 안 됨`·`권한 없음`·`첫 조회 중`·`불러오지 못함 · 마지막 성공 n시간 전`)과 `code`, 머리 경과 "n시간 전 값"(`refreshSec` × 3 을 넘으면 ▲ 오래됨), 본문은 기대는 값이 바뀔 때만 다시 그림, 본문 안 경과 글자는 경과 span(시각을 data 속성에 두고 그리기 끝에 글자만 고침), 서버·방문자 글자는 `textContent`·`title` 로만·양방향 제어문자 뺌, SVG 요소는 `svg`·`g`·`rect`·`line`·`path`·`title` 이고 속성은 허용 목록(기하·`role`·`aria-label`) 그대로, 색은 클래스로만(`style` 없음), 서버 값으로 표를 찾을 때 `Object.hasOwn`. 주기·창 길이 글자는 응답(`refreshSec`·`numOfDays`)으로 만든다 — 상수를 복사하지 않는다.
- 설명 틀(041): 덩어리 끝 접힌 `<details class="terms">`(summary '이 칸 뜻', 절 머리의 `details.explain` 과 다르다) 안 dl 하나 — dt 칸 이름(원래 키를 작게), dd 뜻·계산·한계 한두 문장. 다시 그리는 칸의 형제 자리에 정적 HTML 로 두고, 그 조상 가운데 id 를 가진 요소는 `<section>` 뿐이다(041 단언 — 펼침이 남게). admin.js 는 설명을 만들거나 건드리지 않는다(`explain`·`terms` 글자 없음). 원래 id 는 행에 적지 않고 `title`·dl 에 둔다. 탭 id 여섯의 한국어 이름은 041 이름표를 쓴다.

### 3.2 덩어리 배치 (#traffic 맨 아래, 창 줄 밖)
- 042 의 서버 기록 덩어리 ⑥ 뒤에 카드 하나. Clarity 값은 창과 무관해 창 줄 아래 덩어리와 섞지 않는다. 카드 자체에는 id 를 두지 않고(041 단언), 머리 meta `m-clarity`·본문 `b-clarity` id 는 그대로 둔다(042 의 `#traffic` 순서 단언이 본다).
- 위에서부터: ① 머리 — card-kicker '7 · 화면' + 제목 '화면에서 무엇을 했나', 오른쪽 meta 에 부분 상태·경과·'다음 조회 HH:MM 이후'(`nextAt`, 브라우저 시간대) ② 답 줄(042 와 같은 `p.answer` 꼴, §3.5) ③ 부제 'Clarity 를 부른 때 직전 {numOfDays×24}시간({refreshSec}마다) — 화면 분석에 동의한 방문자만'(036 의 '최근 1일 · UTC 기준 — Clarity 가 준 값' 은 지운다) ④ 요약(§3.3) ⑤ 대시보드 탭별(§3.4) ⑥ '받은 지표' 접힘(036 그대로 + 이름표 §3.6) ⑦ '이 칸 뜻' 접힘(§3.6).
- 머리 꼴은 042 의 덩어리 머리 줄 `div.q-head`('n · 질문' `card-kicker` + 제목, 오른쪽 meta)를 그대로 쓴다.
- 폭(042 와 같은 경계): 1280 은 타일 한 줄·불만 신호 3열 격자와 그 옆 나라. 640px 이하는 타일 2열·신호 한 열(DOM 은 같고 CSS 로만). 이름을 자르지 않고, 페이지 가로 넘침은 360 에서도 0 이다.

### 3.3 요약
- 타일 다섯(값 + 한 줄 부제, null 은 '–'): 세션 `traffic.sessions` — '동의한 방문 · 봇으로 가려낸 n 따로'(`botSessions`) / 사용자 `traffic.users` — '브라우저·기기마다 따로 센다' / 세션당 페이지 `traffic.pagesPerSession`(소수 1자리) — '탭을 바꿔도 한 페이지' / 평균 스크롤 깊이 `summary.scrollDepth`(% 소수 1자리) — '모든 페이지를 합친 값' / 머문 시간 `summary.totalSec`('n분 n초') — '그중 활동 n분 n초'(`activeSec`).
- 불만 신호 여섯 — 행 목록, 순서 고정(데드 클릭·분노 클릭·과도한 스크롤·퀵백·스크립트 오류·오류 클릭). 행마다 한국어 이름 + 한 줄 뜻 + `x% · 세션 a/b`(x = `sessionPct` 소수 1자리, a = 그 신호 `sessions`, b = `traffic.sessions`). 색은 중립이다 — 0 보다 커도 상태색을 쓰지 않는다.
- 나라 — 머리 '나라 · Clarity 이름 · 동의 표본'. `countries` 를 '이름 · 세션 n' 행으로 위 5행만 펴고 나머지는 'n개 더 — 이름…' 접힘(열린 접힘은 JS Set 에 둬 다시 그려도 열린 채 — 036 '정상 n개' 와 같다). `countries` 가 null 이면 `metrics` 에서 이름이 `country`·`countryregion`(§3.6 비교)인 지표의 행을 받은 그대로 한 줄씩(036 받은 지표 꼴), 그것도 없으면 '기록 없음'.
- 세션 0(`traffic.sessions` 가 0 또는 null): 타일은 두고, 신호 여섯과 나라 자리에 상자 하나 '값 없음 — 동의한 세션이 생기면 찬다. 화면 분석 동의는 기본 꺼짐이다.' — 0% 행 여섯을 그리지 않는다.
- 상태색은 상태(오래됨·불러오지 못함·행 상한)에만 쓴다 — 작은 표본에는 쓰지 않는다(중립 tag).

### 3.4 대시보드 탭별
- 표가 아니라 두 줄 행 목록이다(가로 스크롤 없음). 순서 spread·history·gap·pp·health·flow, 그 뒤 '(기타)' 는 서버 `(기타)` 나 `app:other` 세션이 있을 때만.
  - 1줄: 탭 이름(041 이름표) · '처음 들어옴 n' — 접속 `tabs` 의 수(없으면 0, 접속 부분을 그릴 수 없으면 '–').
  - 2줄: 'Clarity 세션 n · 활동 a / 머문 b · 데드 x% · 분노 y%' — 그 탭의 `app:<id>` 행을 기기와 무관하게 합친다(세션 합, 나머지는 세션 가중 평균). 세션 0 이면 'Clarity 세션 0' 만, `pages` 값이 없으면 'Clarity –'.
- 소제목 줄: '대시보드 탭 — 들어온 탭과 머문 탭' + '서버 기록 {24시간·7일·30일} · Clarity 페이지 묶음 직전 {pages.numOfDays×24}시간 · {pages.refreshSec}마다 · 다음 HH:MM 이후(`pages.nextAt`)' + meta 자리에 `pages` 의 상태 배지·경과(036 머리 규칙 — 묶음마다 다시 쓴다) + 부제 '대시보드는 화면 높이에 고정된 틀이라 문서가 스크롤되지 않는다(표는 안쪽 상자) — 스크롤 깊이는 보이지 않는다'. `rowLimitHit` 면 ▲ '받은 행이 1,000 상한에 닿았다 — 세션이 덜 셌을 수 있다'.
- '처음 들어옴' 은 들어올 때 연 탭 주소(서버 기록), Clarity 는 들어온 뒤 바꾼 탭까지 센다(탭을 바꾸면 새 페이지) — 뜻이 달라 나란히 둔다.
- 다시 그리기: 이 칸은 Clarity 나 접속 값이 바뀔 때, 덩어리의 다른 칸은 Clarity 값이 바뀔 때만 — 60초마다 바뀌는 접속 값이 접힘·글자 선택을 날리지 않게.

### 3.5 답 문장 (덩어리 첫 줄, textContent, 아래 틀만)
- 이 스펙의 글 틀에서 `{…}` 는 응답으로 채운다 — `{24}` 는 바깥 부분의 `numOfDays` × 24, 깊이는 §3.3 의 자릿수.
- Clarity 부분을 그릴 수 없음(연결 안 됨 등): 답 문장 없음 — 본문 상태 글이 말한다.
- 세션 0·null: '직전 {24}시간 동의한 Clarity 세션이 없다 — 화면 분석 동의는 기본 꺼짐이라 서버 기록보다 훨씬 적다.'
- 세션 > 0: '동의한 방문 {n}세션(직전 {24}시간)은 평균 {d}%까지 내렸고 {t} 머물렀다.'(d·t 가운데 null 인 조각은 빼고, 둘 다 null 이면 '동의한 방문 {n}세션(직전 {24}시간)이 있었다.')

### 3.6 이름표·설명 글
- 이름 비교는 040 과 같다 — NFKC 뒤 소문자로 바꾸고 영문자·숫자만 남긴다(`Country/Region` → `countryregion`).
- 받은 지표 목록(036 그대로 접힘): 지표마다 한국어 이름, `title` 에 원래 이름과 한 줄 뜻, 모르는 이름은 원래 이름 그대로(이름표 없이). 이름표 열여섯: traffic 방문량·scrolldepth 스크롤 깊이·engagementtime 머문 시간·popularpages 많이 본 페이지·browser 브라우저·device 기기·os OS·country(countryregion) 나라·pagetitle 페이지 제목·referrerurl 들어온 출처·deadclickcount 데드 클릭·rageclickcount 분노 클릭·excessivescroll 과도한 스크롤·quickbackclick 퀵백·scripterrorcount 스크립트 오류·errorclickcount 오류 클릭.
- '이 칸 뜻'(덩어리 끝, 정적 HTML) — dt 와 dd 의 뜻:
  - 세션(`totalSessionCount`·`totalBotSessionCount`): 화면 분석에 동의한 방문의 묶음, 30분 동안 움직임이 없으면 끝난다. 봇 세션은 Clarity 가 가려낸 수(세션 수에 드는지는 문서에 없다).
  - 사용자(`distantUserCount`): 브라우저·기기마다 쿠키로 따로 센다. 세션당 페이지(`pagesPerSessionPercentage`): 이름과 달리 비율이 아니라 평균, 탭을 바꾸면 한 페이지.
  - 평균 스크롤 깊이(`averageScrollDepth`): 페이지를 몇 %까지 내렸나의 평균 — 첫 화면을 넣는지는 Clarity 문서에 없다. 머문 시간·활동(`totalTime`·`activeTime`): 활동은 화면을 실제로 쓴 시간(다른 탭에 가 있던 때는 뺀다), 단위를 초로·값을 세션당 평균으로 본다(둘 다 확인 못 함 — 합으로 확인되면 040 의 묶기와 이 표기를 고치는 후속이다).
  - 불만 신호(`sessionsWithMetricPercentage`): 그 일이 한 번이라도 있었던 세션의 비율과 여섯의 뜻.
  - 나라: Clarity 가 방문자 IP 로 정한 이름(동의 표본) — 서버 기록의 나라·망 종류(IP 앞부분, DB-IP)와 출처가 다르다.
  - 대시보드 탭: 처음 들어옴(서버 기록 — 고른 창)과 Clarity 세션(페이지 묶음 창 — 소제목에 적힌 시간)의 뜻 차이. Clarity 주소에 탭이 실리는지는 확인 못 했다 — 안 실리면 040 이 대시보드 세션을 모두 실시간 스프레드(`app:spread`)로 센다.
  - 지표 열다섯 — dt 는 041 틀대로 받은 지표 목록의 한국어 이름표 + 원래 이름을 작은 고정폭 글자로(예 '스크롤 깊이 `ScrollDepth`'), 원래 이름은 ScrollDepth·EngagementTime·PopularPages·Browser·Device·OS·Country·PageTitle·ReferrerUrl·DeadClickCount·RageClickCount·ExcessiveScroll·QuickbackClick·ScriptErrorCount·ErrorClickCount, dd 한 줄 뜻.
- 041 이 둔 설명의 Clarity 몫을 고쳐 쓴다(서버 기록 몫은 042): 접속 '이 절 읽는 법' 의 '읽는 값' 은 'Clarity 는 요약·페이지 묶음 두 호출, 간격은 칸 머리에 적힌 대로'(간격 숫자는 적지 않는다 — 041 §3.2), '창' 은 'Clarity 는 부른 때 직전 시간 창(요약·페이지 묶음이 따로 — 길이는 덩어리 부제·소제목에 적힌다), 시간대가 없다', 판정 밖·동의 표본 문장은 그대로. 개요 '이 절 읽는 법' 의 시각 dd 에서 'Clarity 칸만 UTC' 를 지운다. 041 의 Clarity 카드 '이 칸 뜻'·타일 부제 넷은 위 목록과 §3.3 타일 다섯으로 바꾼다.
- 글 제약(정적 단언이 부분 문자열로 본다): 설명 글에 `SCRIPT_BANNED` 조각(`.src`·`.style`·`.href`·`eval(`·`EventSource` 등, 042 가 더한 것 포함)·`://`·12자리 숫자·이메일 모양을 쓰지 않는다. `web/admin/*` 에는 033 단언대로 Clarity 를 싣는 `src="…clarity…"` 꼴과 `clarity.ms` 글자가 없다. `clarity.js` 파일 이름 글자는 막지 않는다(042 설명이 '스크립트가 돈 페이지' 를 풀며 쓴다) — 이 덩어리 글은 'Clarity 스크립트' 로 쓰기를 권한다.

### 3.7 엣지
- Clarity 부분 `unconfigured`·`denied`·`pending`·값 없는 `error`, HTTP 수준 실패: 036 규칙대로 본문에 상태 글, 탭 칸과 답 문장은 비우고 받은 지표 접힘은 숨긴다. 다른 덩어리는 그대로다.
- `error` + 마지막 성공 값: 값을 모두 그리고 머리에 '불러오지 못함 · 마지막 성공 n시간 전'. `pages` 도 따로 같은 규칙.
- Clarity 표본: 동의가 기본 꺼짐이라 첫 응답 세션이 0 이었다 — 한동안 '값 없음' 상자와 세션 0 답 문장이 보통이다.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 main 에 fix/036-admin-followup·040·041·042 가 있는지 본다(없으면 멈추고 묻는다). 테스트에 바깥 호출은 없다 — Clarity·접속 응답은 가짜다.
- 정적 단언(`server/tests/test_admin.py` — 036·041·042 단언은 그대로 통과하고 아래를 더한다):
  - 화면: `#traffic` 의 마지막 `details.terms`(Clarity '이 칸 뜻') 의 dt 안에 지표 원래 이름 열다섯이 글자로 있다 / '최근 1일 · UTC 기준' 이 web/admin 어디에도 없다 / SVG 요소(`svg`·`g`·`rect`·`line`·`path`·`title`)·속성 허용 목록 `SVG_ATTRS`(열넷), `fetch(` 1회와 피드 경로 여덟, 화면 파일 셋(`admin.css`·`admin.js`·`index.html`), `SCRIPT_BANNED`(042 가 더한 것 포함), admin.js 의 스킴 주소는 SVG 이름공간 하나, 외부 링크 호스트 `EXTERNAL_HOSTS`(이 PR 은 더하지 않는다), 033 단언(`src="…clarity…"` 꼴·`clarity.ms` 없음), 041 단언(`details.explain`·`details.terms` 자리)이 고치지 않은 채 통과한다.
- `node --check web/admin/admin.js`.
- node 논리 확인(pytest 안 — 033 `test_clarity.py` 처럼 node 로 admin.js 를 가짜 `window`·`document` 와 싣고 그리기가 아닌 계산·글자만 본다. 기대값은 화면 코드가 아니라 실행 세션이 Python 으로 따로 푼 값. node 가 없으면 CI 에서는 실패):
  - 답 문장 두 갈래(세션 0·null, 세션 > 0 — d·t 가 null 인 조각 빼기 포함) 글자가 §3.5 틀과 같다 / 탭 합치기 — `app:spread` 두 기기(세션 4·2) → 'Clarity 세션 6'·가중 평균.
  - 가짜 DOM 이 지나치게 커지는 항목은 아래 설계 세션 확인으로 넘기고 §7 에 적는다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`(dist 에 admin 없음).
- 커밋(각 300줄 이하): ① 덩어리 HTML·CSS·설명 ② 덩어리 JS(요약·탭으로 나눠) ③ 정적 단언·node 논리 확인 ④ §6 문서·§5·§7.

**PR 안 — 설계 세션 확인(머지 전 — 로컬 Docker 또는 Browser pane. 결과와 돌린 세션을 §5 에 적고, 어긋나면 머지 전에 고친다)**:
- 브라우저(036 §4 방식 — 127.0.0.1 에만 게시한 테스트 compose + 가짜 백엔드, Clarity·접속 응답을 바꿔 줄 수 있게):
  - 요약: 세션 0 → 신호 행 0·'값 없음 — …' 상자 하나·세션 0 답 문장 / sessionPct 25 → '25.0% · 세션 1/4', 색 중립 / countries 7행 → 5행 + '2개 더' 접힘, 연 채 다음 묶음 뒤에도 열림 / countries null + metrics `Country` 행 → 받은 그대로 / 받은 지표 `ScrollDepth`·`Country/Region`·모르는 `FooBar` → '스크롤 깊이'·'나라'·'FooBar'.
  - pages: `error` + 값 → 값 + '불러오지 못함 · 마지막 성공' / 값 null → 탭 줄 'Clarity –' / `rowLimitHit` → ▲ 줄 / `fetchedAt` 이 `refreshSec` × 3 을 넘음 → 오래됨. Clarity `unconfigured` → '연결 안 됨 — 토큰 없음 …'·답 문장 없음·탭 칸 빔, 다른 덩어리 그대로.
  - 탭: access `tabs` spread 325·history 112 + `app:spread` 두 기기(세션 4·2) → '처음 들어옴 325'·'Clarity 세션 6'·가중 평균 / access `error` → '처음 들어옴 –' / access `window` `7d` → '서버 기록 7일' / `app:other` 세션 → '(기타)' 행.
  - 답 문장: 두 갈래 글자가 §3.5 틀과 같다. 다시 그리기: Clarity 응답이 같고 접속만 바뀌면 탭 칸만 새 노드이고 요약 노드와 접힘·글자 선택은 그대로다.
  - 안전: countries 이름·지표 이름·행 값·groups 의 모르는 page 값에 `<img src=x onerror=alert(1)>`·`javascript:alert(1)`·U+202E → 글자로, img 0·대화상자 0·title 밖 속성 0.
  - 폭 1280·768·360: `scrollWidth ≤ innerWidth`, 탭·신호 이름이 잘리지 않음, 360 에서 타일 2열, CSP 위반·Uncaught 0. 사진 셋(1280 정상·360 정상·360 세션 0)은 PR 본문에.

**배포 뒤 — 사람·설계 세션(완료 조건 아님, status 비고 "043 운영 확인 대기")**: 동의 세션이 쌓인 뒤 페이지 묶음에 `app:<탭>` 이 갈려 오는지(주소에 탭이 실리는지) 본다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행 web 칸 끝에 "· Clarity 덩어리 v3(043 — 요약 타일·불만 신호·나라, 대시보드 탭별 진입·Clarity 활동)", 비고 끝에 "· 043 운영 확인 대기(동의 세션 뒤)". 알려진 빚에서 `(036) Clarity 칸은 traffic 타일과 받은 지표 목록뿐 — 정규화 값(summary·countries·pages)은 040 응답에 있고 그리는 것은 043` 줄(040 이 고친 글)과 `(041) 접속 절의 Clarity 설명은 035 칸(받은 지표) 기준 — 043 이 고쳐 쓴다` 줄(042 가 고친 글)을 지운다. 더하는 빚 하나: `(043) Clarity 주소에 탭이 실리는지 확인 못 함 — 안 실리면 대시보드 탭별 Clarity 줄이 모두 실시간 스프레드에 몰린다(040 이 탭 없는 주소를 app:spread 로 센다)`.
- `CLAUDE.md` — 스펙 인덱스 043 행 상태 → DONE.
- `docs/context/product.md` — 기능 목록 admin 행의 "Clarity" → "Clarity(스크롤·머문 시간·불만 신호·대시보드 탭별)".
- `docs/specs/036-admin-v2.md` — §3.4 접속 머리 줄의 "Clarity(그대로 — 043 이 바꾼다)"(042 가 쓴 글) → "Clarity(043)", 'Clarity(Clarity 요약)' 줄(041·040 이 고친 글) → "Clarity — 043(요약·나라·대시보드 탭별·받은 지표, 창 줄 밖 맨 아래)". §4 정적 단언 끝에 "Clarity 설명 단언은 043 §4".
- `docs/specs/041-admin-explain.md` — Clarity 쪽 글을 043 으로 넘긴다(서버 기록 쪽은 042 가 이미 고쳤다): §2 의 "접속 절의 서버 기록 설명은 042 §3.6 이 정한다(Clarity 설명은 043 이 고쳐 쓴다)."(042 가 쓴 글) → "접속 절의 서버 기록 설명은 042 §3.6, Clarity 설명은 043 §3.6 이 정한다." §3.3 접속 절의 '읽는 값'·'창' 가운데 Clarity 조각과 개요 '화면 공통' 의 '시각(… Clarity 칸만 UTC)' 을 §3.6 대로, "'Clarity' 카드 끝: 세션, 봇 세션, 사용자, 세션당 페이지, 받은 지표" → "'Clarity' 카드 끝: 043 §3.6". §3.1 타일 부제의 "Clarity 카드의 세션·봇 세션·사용자·세션당 페이지" → "Clarity 카드의 타일 다섯(043 §3.3)". §3.4 대시보드 탭의 '적는 곳' 끝에 "·Clarity 탭별 행(043)". §4 설계 세션 확인 8 의 "Clarity 넷" → "Clarity 다섯(043 §3.3)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
