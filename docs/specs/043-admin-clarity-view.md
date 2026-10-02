# 043 — admin-clarity-view

상태: TODO | 의존: **040 clarity-v2·041 admin-explain·042 admin-traffic·랜딩 v3(022 — 랜딩 세션이 절 id 를 `<section id>` 로 고정, 히어로 포함)가 main 에 머지된 뒤 시작한다**(아니면 멈추고 묻는다). main 에는 fix/036-admin-followup(지표 이름 정규화·차트 빈 칸 '값 없음'·'값 1개뿐'·본문 경과 글자 다시 쓰기·keep-all)이 있어야 한다. 042 는 같은 세 파일(접속 절)을 고치므로 나란히 가지 않는다 — 덩어리 머리 `div.q-head`·강도 클래스 `h0`~`h5`·`#traffic` 순서를 042 가 만든다(그래서 038·039 도 main 에 있다). 계약을 쓰는 스펙(쓰는 계약은 §3.1 에 복사했다): 040(Clarity 피드 키), 036(화면 규칙 — 이 스펙이 Clarity 덩어리를 바꾼다), 041(설명 틀·탭 이름표), 042(덩어리 머리·강도 클래스·배치), 038(접속 `tabs`·`window`), 022(랜딩 절), 033(관리자 단언), 002(탭 id).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 관리자 접속 절 맨 아래 Clarity 덩어리에서 "화면에서 무엇을 했나" 를 읽는다 — 동의한 방문자의 스크롤 깊이·머문 시간·불만 신호·나라, 랜딩을 기기마다 어느 절까지 내려오나, 대시보드 탭마다 들어온 수(서버 기록)와 머문 모양(Clarity). 지금(036)은 Clarity 가 준 이름·키 그대로의 글자 목록뿐이다. Clarity API 는 히트맵 좌표를 주지 않으므로, 미리 잰 랜딩 절 위치 표와 평균 스크롤 깊이 하나로 절마다 도달 비율을 추정하고, 늘 '추정(모형)'·세션 수·측정 날짜와 커밋을 함께 보인다.

## 2. 범위
- 만드는 것: `web/admin/` 세 파일의 Clarity 덩어리(index.html 고정 틀·admin.js·admin.css), admin.js 상수 `LANDING_SECTIONS`(측정 표)·`LANDING_NAMES`(절 이름), 측정 스크립트 `web/scripts/measure-landing-sections.mjs`, `server/tests/test_admin.py` 정적 단언.
- 하지 않는 것: 서버·API(040 응답을 읽기만). 서버 기록 덩어리 ①~⑥·창 줄(042). 절 설명 틀(041 — 이 스펙은 그 틀에 Clarity 글을 넣는다). 대시보드 스크롤 추정(/app/ 은 문서 스크롤이 없다). 히트맵·클릭 좌표(API 에 없다). 나라 이름을 ISO 로 바꾸기. web/admin 새 파일·외부 라이브러리·빌드. 측정 스크립트를 CI 에서 돌리기. 랜딩 수정(022).
- 바꾸는 기존 것: 036 §3.4 접속 절 Clarity 덩어리·§3.6 차트 종류·§4 정적 단언, 041 이 쓴 접속 절 '이 절 읽는 법' 의 Clarity 줄, CLAUDE.md §2 `web/scripts` 설명. 036·041 은 이 레포 주인 담당이다. 022 는 랜딩 세션 담당 — 고치지 않고 §6 제안을 PR 본문에 적는다.

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
- 위에서부터: ① 머리 — card-kicker '7 · 화면' + 제목 '화면에서 무엇을 했나', 오른쪽 meta 에 부분 상태·경과·'다음 조회 HH:MM 이후'(`nextAt`, 브라우저 시간대) ② 답 줄(042 와 같은 `p.answer` 꼴, §3.6) ③ 부제 'Clarity 를 부른 때 직전 {numOfDays×24}시간({refreshSec}마다) — 화면 분석에 동의한 방문자만'(036 의 '최근 1일 · UTC 기준 — Clarity 가 준 값' 은 지운다) ④ 요약(§3.3) ⑤ 랜딩 어디까지(§3.4) ⑥ 대시보드 탭별(§3.5) ⑦ '받은 지표' 접힘(036 그대로 + 이름표 §3.7) ⑧ '이 칸 뜻' 접힘(§3.7).
- 머리 꼴은 042 의 덩어리 머리 줄 `div.q-head`('n · 질문' `card-kicker` + 제목, 오른쪽 meta)를 그대로 쓴다.
- 폭(042 와 같은 경계): 1280 은 타일 한 줄·불만 신호 3열 격자와 그 옆 나라·랜딩 기기 셋 나란히. 640px 이하는 타일 2열·신호 한 열·기기 카드 세로, 480px 이하는 막대 행이 '이름 … 값' 한 줄 아래 막대 줄(DOM 은 같고 CSS 로만). 이름을 자르지 않고, 페이지 가로 넘침은 360 에서도 0 이다.

### 3.3 요약
- 타일 다섯(값 + 한 줄 부제, null 은 '–'): 세션 `traffic.sessions` — '동의한 방문 · 봇으로 가려낸 n 따로'(`botSessions`) / 사용자 `traffic.users` — '브라우저·기기마다 따로 센다' / 세션당 페이지 `traffic.pagesPerSession`(소수 1자리) — '탭을 바꿔도 한 페이지' / 평균 스크롤 깊이 `summary.scrollDepth`(% 소수 1자리) — '모든 페이지를 합친 값 — 랜딩만은 아래' / 머문 시간 `summary.totalSec`('n분 n초') — '그중 활동 n분 n초'(`activeSec`).
- 불만 신호 여섯 — 행 목록, 순서 고정(데드 클릭·분노 클릭·과도한 스크롤·퀵백·스크립트 오류·오류 클릭). 행마다 한국어 이름 + 한 줄 뜻 + `x% · 세션 a/b`(x = `sessionPct` 소수 1자리, a = 그 신호 `sessions`, b = `traffic.sessions`). 색은 중립이다 — 0 보다 커도 상태색을 쓰지 않는다.
- 나라 — 머리 '나라 · Clarity 이름 · 동의 표본'. `countries` 를 '이름 · 세션 n' 행으로 위 5행만 펴고 나머지는 'n개 더 — 이름…' 접힘(열린 접힘은 JS Set 에 둬 다시 그려도 열린 채 — 036 '정상 n개' 와 같다). `countries` 가 null 이면 `metrics` 에서 이름이 `country`·`countryregion`(§3.7 비교)인 지표의 행을 받은 그대로 한 줄씩(036 받은 지표 꼴), 그것도 없으면 '기록 없음'.
- 세션 0(`traffic.sessions` 가 0 또는 null): 타일은 두고, 신호 여섯과 나라 자리에 상자 하나 '값 없음 — 동의한 세션이 생기면 찬다. 화면 분석 동의는 기본 꺼짐이다.' — 0% 행 여섯을 그리지 않는다.

### 3.4 랜딩 어디까지 — 도달 추정
- 대상: `pages.groups` 의 page `landing` 행을 device `mobile`·`tablet`·`desktop` 별로 `LANDING_SECTIONS.devices` 의 같은 키에 잇는다(`other` 는 쓰지 않는다). 이름은 휴대폰·태블릿·데스크톱(Clarity 의 PC).
- 모형: f = foldPx ÷ docPx × 100(첫 화면 %), d = 그 행의 `scrollDepth`(평균 깊이 %), n = `sessions`.
  - 도달(x) = x ≤ f 면 1, 아니면 e^(−(x − f)/λ) — 첫 화면까지는 모두 보고 그 아래로는 지수로 줄어든다고 놓는다. λ 는 평균이 d 가 되게 정한다: f + λ(1 − e^(−(100 − f)/λ)) = d.
  - λ 는 [10^−6, 10^6] 에서 기하 평균으로 나누는 이분법 200번으로 푼다(좌변은 λ 에 대해 커지고, λ→0 이면 f, λ→∞ 이면 100).
  - 절 도달 = 그 절 위 모서리 s 의 도달 × 100 — '그 절 머리까지 내려온 세션의 비율' 추정이다. 정수 %, 0.5 미만은 '1% 미만'. 분포가 아니라 평균 하나로 그린 모양이다.
- 숨김(위에서 먼저 맞는 사유 하나): 행이 없거나 n = 0 → '세션 없음 — 직전 {72}시간 동의한 {기기} 랜딩 방문이 없다' / n 이 null → '세션 수 없음 — Clarity 가 이 칸의 세션 수를 주지 않았다' / n 1~4 → '세션 n — 5 미만이라 숨겼다. 한두 명이 모양을 정한다'(하한은 상수 `REACH_MIN_SESSIONS = 5`) / d 가 수가 아니거나 0~100 밖 → '평균 깊이 값이 없다' / d ≤ f → '평균 깊이 d% 가 첫 화면(f%) 안이라 추정하지 않는다'. 숨긴 카드도 윤곽 그림의 절 rect·첫 화면 선은 남기고, 도달 %·평균 깊이 선은 그리지 않는다.
- 기기 카드(1280 은 3열, 640px 이하 세로): 머리 = 기기 이름·폭(390·768·1280) + 중립 tag '세션 n'·'추정(모형)'(늘). 본문 = 왼쪽 랜딩 윤곽 그림(랜딩 문서를 세로 막대 하나로 줄여 절을 칸으로 나눈 그림 — 아래 '윤곽') + 오른쪽 절 행 목록. 아래 범례 '첫 화면 f%'(점선 견본)·'평균 깊이 d%'(실선 견본, 숨김이면 '–'). 숨김이면 사유 상자.
- 윤곽 SVG: viewBox `0 0 64 600`·`preserveAspectRatio` none(세로 = % × 6, 절 행 목록과 같은 높이로 늘어난다), 그리는 차례 — 문서 바탕 rect → 절마다 rect(위아래 1.5 여백, 도달 단계 클래스 ⌈도달 × 5⌉ = 1~5, 숨김이면 흐린 클래스) → 첫 화면 점선 line(y = f × 6) → 평균 깊이 실선 line(y = d × 6, 보일 때만) → 절마다 연결선 path(절 rect 오른쪽 위 모서리 y = s × 6 에서 그 절 행 가운데 y = (i + ½) ÷ 절 수 × 600 으로 꺾어 잇는다 — 절 행은 CSS 로 같은 높이). 선 굵기는 늘지 않는다(non-scaling-stroke). `role="img"`·`aria-label` '{기기} 랜딩 윤곽 — 첫 화면 f%, 평균 깊이 d%', 절 rect 마다 `title` '{절 이름} s–e% · 도달 r%'. 글자는 모두 HTML 이다.
- 절 행(HTML): 1줄 = 절 이름(`LANDING_NAMES`) + 's–e% 지점'(작게) … 도달 %(숨김이면 '–'), 2줄 = 도달 막대(rect 둘 — 바탕·값, 숨김이면 바탕만).
- 소제목 줄: '랜딩 — 어디까지 내려오나' + tag '추정(모형)' + 'Clarity 페이지 묶음 직전 {pages.numOfDays×24}시간 · {pages.refreshSec}마다 · 다음 HH:MM 이후(`pages.nextAt`) · 랜딩 측정 {measuredAt} · {commit}' + meta 자리에 `pages` 의 상태 배지·경과(036 머리 규칙 — 묶음마다 다시 쓴다). `rowLimitHit` 면 ▲ '받은 행이 1,000 상한에 닿았다 — 세션이 덜 셌을 수 있다'.
- 세 기기 모두 숨김: 카드 셋 대신 상자 하나 — '숨김 — 기기마다 랜딩 세션이 5 이상이고 평균 깊이가 첫 화면보다 깊을 때 보인다 · 지금 휴대폰 n · 태블릿 n · 데스크톱 n(직전 {72}시간, 동의한 방문만)'(d ≤ f 로 숨긴 기기는 '첫 화면 안' 을 붙인다). `pages` 값이 null(첫 성공 전·7일 지남)이거나 `pages` 를 그릴 수 없으면 상자 '값 없음' + 배지.
- 색: 도달 단계 1~5 = 042 열지도의 강도 클래스 `h1`~`h5`(accent 700·600·500·400·200 — 042 가 만든 클래스를 쓴다), 문서 바탕 neutral-900, 숨긴 절 neutral-800, 첫 화면 점선 accent-300, 평균 깊이 선 글자색, 연결선 neutral-700, 도달 막대 값 accent-400. 상태색은 상태(오래됨·불러오지 못함·행 상한)에만 — 작은 표본에는 쓰지 않는다(중립 tag).

### 3.5 대시보드 탭별
- 표가 아니라 두 줄 행 목록이다(가로 스크롤 없음). 순서 spread·history·gap·pp·health·flow, 그 뒤 '(기타)' 는 서버 `(기타)` 나 `app:other` 세션이 있을 때만.
  - 1줄: 탭 이름(041 이름표) · '처음 들어옴 n' — 접속 `tabs` 의 수(없으면 0, 접속 부분을 그릴 수 없으면 '–').
  - 2줄: 'Clarity 세션 n · 활동 a / 머문 b · 데드 x% · 분노 y%' — 그 탭의 `app:<id>` 행을 기기와 무관하게 합친다(세션 합, 나머지는 세션 가중 평균). 세션 0 이면 'Clarity 세션 0' 만, `pages` 값이 없으면 'Clarity –'.
- 소제목 줄: '대시보드 탭 — 들어온 탭과 머문 탭' + '서버 기록 {24시간·7일·30일} · Clarity 직전 {pages.numOfDays×24}시간' + 부제 '대시보드는 화면 높이에 고정된 틀이라 문서가 스크롤되지 않는다(표는 안쪽 상자) — 스크롤 깊이는 보이지 않는다'.
- '처음 들어옴' 은 들어올 때 연 탭 주소(서버 기록), Clarity 는 들어온 뒤 바꾼 탭까지 센다(탭을 바꾸면 새 페이지) — 뜻이 달라 나란히 둔다.
- 다시 그리기: 이 칸은 Clarity 나 접속 값이 바뀔 때, 덩어리의 다른 칸은 Clarity 값이 바뀔 때만 — 60초마다 바뀌는 접속 값이 접힘·글자 선택을 날리지 않게.

### 3.6 답 문장 (덩어리 첫 줄, textContent, 아래 틀만)
- 이 스펙의 글 틀에서 `{…}` 는 응답으로 채운다 — `{24}`·`{72}` 는 그 부분(바깥·`pages`)의 `numOfDays` × 24, 비율·깊이는 §3.3·§3.4 의 자릿수.
- Clarity 부분을 그릴 수 없음(연결 안 됨 등): 답 문장 없음 — 본문 상태 글이 말한다.
- 세션 0·null: '직전 {24}시간 동의한 Clarity 세션이 없다 — 화면 분석 동의는 기본 꺼짐이라 서버 기록보다 훨씬 적다.'
- 세션 > 0: '동의한 방문 {n}세션(직전 {24}시간)은 평균 {d}%까지 내렸고 {t} 머물렀다.'(d·t 가운데 null 인 조각은 빼고, 둘 다 null 이면 '동의한 방문 {n}세션(직전 {24}시간)이 있었다.') 뒤에 하나 — 도달이 보이는 기기가 있으면(그중 세션이 가장 많은 기기, 같으면 데스크톱·휴대폰·태블릿 순) '랜딩을 연 {기기} 방문의 {a}가 '{절1}' 머리까지, {b}가 '{끝 절}'까지 내려왔다고 추정한다(세션 {m}, 모형).'(절1 = 위 모서리가 첫 화면 밖인 첫 절) / 보이는 기기가 없고 `pages` 값이 있으면 '랜딩 도달은 기기마다 세션이 5 미만이거나 평균 깊이가 첫 화면 안이라 숨겼다.' / `pages` 값이 없으면 둘째 문장 없음.

### 3.7 이름표·설명 글
- 이름 비교는 040 과 같다 — NFKC 뒤 소문자로 바꾸고 영문자·숫자만 남긴다(`Country/Region` → `countryregion`).
- 받은 지표 목록(036 그대로 접힘): 지표마다 한국어 이름, `title` 에 원래 이름과 한 줄 뜻, 모르는 이름은 원래 이름 그대로(이름표 없이). 이름표 열여섯: traffic 방문량·scrolldepth 스크롤 깊이·engagementtime 머문 시간·popularpages 많이 본 페이지·browser 브라우저·device 기기·os OS·country(countryregion) 나라·pagetitle 페이지 제목·referrerurl 들어온 출처·deadclickcount 데드 클릭·rageclickcount 분노 클릭·excessivescroll 과도한 스크롤·quickbackclick 퀵백·scripterrorcount 스크립트 오류·errorclickcount 오류 클릭.
- '이 칸 뜻'(덩어리 끝, 정적 HTML) — dt 와 dd 의 뜻:
  - 세션(`totalSessionCount`·`totalBotSessionCount`): 화면 분석에 동의한 방문의 묶음, 30분 동안 움직임이 없으면 끝난다. 봇 세션은 Clarity 가 가려낸 수(세션 수에 드는지는 문서에 없다).
  - 사용자(`distantUserCount`): 브라우저·기기마다 쿠키로 따로 센다. 세션당 페이지(`pagesPerSessionPercentage`): 이름과 달리 비율이 아니라 평균, 탭을 바꾸면 한 페이지.
  - 평균 스크롤 깊이(`averageScrollDepth`): 페이지를 몇 %까지 내렸나의 평균 — 첫 화면을 넣는지는 Clarity 문서에 없다. 머문 시간·활동(`totalTime`·`activeTime`): 활동은 화면을 실제로 쓴 시간(다른 탭에 가 있던 때는 뺀다), 단위를 초로·값을 세션당 평균으로 본다(둘 다 확인 못 함 — 합으로 확인되면 040 의 묶기와 이 표기를 고치는 후속이다).
  - 불만 신호(`sessionsWithMetricPercentage`): 그 일이 한 번이라도 있었던 세션의 비율과 여섯의 뜻.
  - 나라: Clarity 가 방문자 IP 로 정한 이름(동의 표본) — 서버 기록의 나라·망 종류(IP 앞부분, DB-IP)와 출처가 다르다.
  - 랜딩 도달(모형): §3.4 를 두세 문장으로 — 평균 깊이 하나로 푼 추정이지 분포가 아니다, 세션 5 미만·평균이 첫 화면 안이면 숨긴다, 절 위치는 기기마다 폭 하나에서 잰 랜딩 측정 표(날짜·커밋).
  - 대시보드 탭: 처음 들어옴(서버 기록 — 고른 창)과 Clarity 세션(페이지 묶음 창 — 소제목에 적힌 시간)의 뜻 차이. Clarity 주소에 탭이 실리는지는 확인 못 했다 — 안 실리면 040 이 대시보드 세션을 모두 실시간 스프레드(`app:spread`)로 센다.
  - 지표 열다섯 — dt 는 041 틀대로 받은 지표 목록의 한국어 이름표 + 원래 이름을 작은 고정폭 글자로(예 '스크롤 깊이 `ScrollDepth`'), 원래 이름은 ScrollDepth·EngagementTime·PopularPages·Browser·Device·OS·Country·PageTitle·ReferrerUrl·DeadClickCount·RageClickCount·ExcessiveScroll·QuickbackClick·ScriptErrorCount·ErrorClickCount, dd 한 줄 뜻.
- 041 이 둔 설명의 Clarity 몫을 고쳐 쓴다(서버 기록 몫은 042): 접속 '이 절 읽는 법' 의 '읽는 값' 은 'Clarity 는 요약·페이지 묶음 두 호출, 간격은 칸 머리에 적힌 대로'(간격 숫자는 적지 않는다 — 041 §3.2), '창' 은 'Clarity 는 부른 때 직전 시간 창(요약·페이지 묶음이 따로 — 길이는 덩어리 부제·소제목에 적힌다), 시간대가 없다', 판정 밖·동의 표본 문장은 그대로. 개요 '이 절 읽는 법' 의 시각 dd 에서 'Clarity 칸만 UTC' 를 지운다. 041 의 Clarity 카드 '이 칸 뜻'·타일 부제 넷은 위 목록과 §3.3 타일 다섯으로 바꾼다.
- 글 제약(정적 단언이 부분 문자열로 본다): 설명 글에 `SCRIPT_BANNED` 조각(`.src`·`.style`·`.href`·`eval(`·`EventSource` 등, 042 가 더한 것 포함)·`://`·12자리 숫자·이메일 모양을 쓰지 않는다. `web/admin/*` 에는 033 단언대로 Clarity 를 싣는 `src="…clarity…"` 꼴과 `clarity.ms` 글자가 없다. `clarity.js` 파일 이름 글자는 막지 않는다(042 설명이 '스크립트가 돈 페이지' 를 풀며 쓴다) — 이 덩어리 글은 'Clarity 스크립트' 로 쓰기를 권한다.

### 3.8 랜딩 절 표·측정 스크립트
- 자리: admin.js 의 표시 주석 `// 측정 표 시작`·`// 측정 표 끝` 사이 한 문장 `const LANDING_SECTIONS = <JSON>;`(키 큰따옴표 — 스크립트와 테스트가 JSON 으로 읽고 쓴다). 모양 `{"measuredAt":"YYYY-MM-DD","commit":"<7자>","devices":{"mobile":{"width":390,"docPx":…,"foldPx":844,"sections":[[id, startPct, endPct], …]},"tablet":{"width":768,…,"foldPx":1024,…},"desktop":{"width":1280,…,"foldPx":900,…}}}`. 절은 landing.html 의 `<section id>` 문서 순서(히어로 포함), %는 문서 높이 대비 위·아래 모서리(소수 1자리).
- `LANDING_NAMES`(표시 주석 밖, `const LANDING_NAMES = <JSON>;`): 절 id → 짧은 한국어 이름 — real '표시 김프와 실제 김프'·events '지난 7일 사건'·kimp '김프·역프란'·method '계산 방법'·faq '자주 묻는 질문'·히어로(v3 가 정한 id) '첫 화면 카드'. v3 에서 새로 생기거나 바뀐 절은 그 h2 를 줄인 이름으로 짓고 §7 에 적는다.
- 왜 상수인가: 같은 출처 파일(`web/admin/page-sections.json`)로 두면 test_admin 의 파일 셋·fetch 경로 목록, 036 §3.8, 만료 판정의 '여덟 경로' 셈까지 함께 바꿔야 하는데 얻는 게 없다(표는 수 KB, 배포마다 화면과 같이 나간다). 화면이 랜딩을 직접 잴 길도 없다 — CSP `default-src 'self'` 가 kimptrack.com 을 부르지 못하게 하고, 다른 출처 iframe 의 DOM 은 읽을 수 없으며, 위치가 폭·글꼴·/api/landing 값에 따라 바뀐다.
- 왜 기기마다 높이 하나인가: 랜딩 문서 높이는 폭에만 기대고 화면 높이와 무관하다(실측 1280×800·1280×1080 모두 5,405px). 데스크톱 1280·1440·1920 은 같은 값(.wrap 1120px)이고 360~1920 사이 절 경계 차이는 3%p 안이다. v3 가 화면 높이 단위(vh)로 절 높이를 정했으면 이 가정이 깨진다 — 그때는 멈추고 묻는다.
- 지금 값(2026-10-02, 커밋 507e06d — v3 뒤 다시 잰다, 맞춰 볼 기준): mobile 8,296px·f 10.2%(real 17.2–34.3 · events 34.3–47.8 · kimp 47.8–63.5 · method 63.5–75.4 · faq 75.4–95.8), tablet 7,473px·f 13.7%, desktop 5,405px·f 16.7%(real 19.8–35.8 · events 35.8–49.5 · kimp 49.5–66.1 · method 66.1–78.7 · faq 78.7–95.6). d = 45% 면 mobile real 83·events 54·kimp 38·method 25·faq 18%, desktop 90·53·34·20·13%.
- 측정 스크립트 `web/scripts/measure-landing-sections.mjs` — 레포 뿌리에서 `node web/scripts/measure-landing-sections.mjs`:
  - Node ≥22.4 내장만(`node:` 모듈·전역 `fetch`·`WebSocket` — 전역 WebSocket 은 22.4 부터 플래그 없이 있다. 없으면 Node 버전을 알리고 멈춘다). package.json·npm 의존은 그대로. CI 에서 돌리지 않는다(Chrome 없음) — 랜딩 절을 바꾸는 PR 이 돌린다(022 글꼴 서브셋 스크립트와 같은 자리·성격).
  - 시작 조건: `web/public` 에 커밋 안 된 변경이 있으면 재지 않고 멈춘다(commit 이 잰 판을 가리키게). Chrome 은 env `CHROME`, 없으면 OS 기본 자리 — 못 찾으면 멈춘다.
  - 서버: 127.0.0.1 임의 포트에 `web/public` 을 뿌리로(`/` → landing.html), `/api/landing` 은 스크립트 안의 고정 가짜 응답(022 §3.2 모양, 카드·다음 경로·사건 표가 모두 그려지는 값), 그 밖은 404.
  - Chrome: `--headless=new`·임시 `--user-data-dir`·디버깅 포트 0(임의), `--host-resolver-rules` 로 127.0.0.1 밖 이름 풀이를 막는다(글꼴·Clarity·아이콘 등 바깥 호출 0). CDP 로 본 요청 가운데 127.0.0.1 밖이 하나라도 성공하면 멈춘다.
  - 폭마다(390 만 모바일 흉내 — 2026-10-02 측정과 같은 방법, 높이 = foldPx, 배율 1): 불러온 뒤 `document.fonts.ready` 와 API 값으로 카드가 찬 것을 기다리고, `documentElement.scrollHeight` 를 docPx 로, `section[id]` 마다 문서 좌표의 위·아래 모서리를 잰다.
  - 쓰기: 표시 주석 사이만 바꾸고 그 밖 바이트는 그대로. measuredAt = 오늘(KST), commit = HEAD 7자. 표시 주석이 정확히 한 쌍이 아니면 멈춘다. 기기마다 docPx·첫 화면 %·절과 앞 표에서 바뀐 절을 출력한다.
  - 정리: 성공·실패 모두 Chrome·서버를 끄고 임시 프로필을 지운다.

### 3.9 엣지
- Clarity 부분 `unconfigured`·`denied`·`pending`·값 없는 `error`, HTTP 수준 실패: 036 규칙대로 본문에 상태 글, 랜딩·탭 칸과 답 문장은 비우고 받은 지표 접힘은 숨긴다. 다른 덩어리는 그대로다.
- `error` + 마지막 성공 값: 값을 모두 그리고 머리에 '불러오지 못함 · 마지막 성공 n시간 전'. `pages` 도 따로 같은 규칙.
- 세션 > 0 인데 landing 행이 없음(대시보드만 본 날): 기기마다 '세션 없음'.
- Clarity 표본: 동의가 기본 꺼짐이라 첫 응답 세션이 0 이었다 — 한동안 기기마다 '5 미만 — 숨김' 이 보통이다.
- 랜딩 절이 바뀌었는데 표를 다시 재지 않음: 표의 절 id 가 landing.html 에서 사라지거나 순서가 어긋나면 test_admin 이 멈춘다(§4). 새로 생긴 절은 멈추지 않고 화면에 나오지 않는다(표에 없는 절 = 측정 안 됨), 높이만 바뀐 것도 잡지 못한다 — 화면의 측정 날짜·커밋이 말한다. 다른 담당(022) PR 을 새 절 때문에 막지 않으려는 결정(설계 세션, 2026-10-02).

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 main 에 fix/036-admin-followup·040·041·042·랜딩 v3(landing.html 의 `<section>` 마다 id)가 있는지 본다(없으면 멈추고 묻는다). 측정 스크립트는 쓰되 돌리지 않는다 — 샌드박스는 Chrome 을 띄울 수 없다. 표시 주석 사이에는 실행 프롬프트로 받은 표(설계 세션이 랜딩 v3 머지 뒤 이 Mac 의 Chrome 으로 잰 값, §3.8 모양)를 넣고, 받지 못했으면 멈추고 묻는다. §7 에 표를 잰 세션·날짜·커밋을 적는다. 테스트에 바깥 호출은 없다 — Clarity·접속 응답은 가짜다.
- 정적 단언(`server/tests/test_admin.py` — 036·041·042 단언은 그대로 통과하고 아래를 더한다):
  - 표: admin.js 에 `// 측정 표 시작`·`// 측정 표 끝` 줄이 한 번씩 이 순서이고, 그 사이가 `const LANDING_SECTIONS = ` + JSON + `;` 한 문장(json.loads) / measuredAt 이 YYYY-MM-DD·오늘(KST) 이하, commit 이 16진 7자 / devices 키 mobile·tablet·desktop, width 390·768·1280, foldPx 844·1024·900, docPx 정수 > foldPx / 기기마다 sections 의 id 가 모두 landing.html 의 `<section …>` id 에 있고 그 문서 순서를 따른다(속성 순서·따옴표 꼴 무관 — 표에 없는 절은 허용) / 0 ≤ start < end ≤ 100, start·end 가 앞 절보다 크다 / `LANDING_NAMES`(JSON 한 문장)가 그 id 를 모두 덮는다 / `REACH_MIN_SESSIONS = 5;`.
  - 스크립트: `web/scripts/measure-landing-sections.mjs` 가 있고 import·`import(` 대상이 모두 `node:` 로 시작한다 / `--headless`·`--user-data-dir`·`--host-resolver-rules` 가 있다 / 파일 안의 스킴 주소(`http(s)://`·`ws://`)는 모두 127.0.0.1 이다(가짜 응답에도 바깥 주소를 넣지 않는다) / 표시 주석 두 줄이 admin.js 와 같은 글자다.
  - 화면: `#traffic` 의 마지막 `details.terms`(Clarity '이 칸 뜻') 의 dt 안에 지표 원래 이름 열다섯이 글자로 있다 / '최근 1일 · UTC 기준' 이 web/admin 어디에도 없다 / SVG 요소(`svg`·`g`·`rect`·`line`·`path`·`title`)·속성 허용 목록 `SVG_ATTRS`(열넷), `fetch(` 1회와 피드 경로 여덟, 화면 파일 셋(`admin.css`·`admin.js`·`index.html`), `SCRIPT_BANNED`(042 가 더한 것 포함), admin.js 의 스킴 주소는 SVG 이름공간 하나, 외부 링크 호스트 `EXTERNAL_HOSTS`(이 PR 은 더하지 않는다), 033 단언(`src="…clarity…"` 꼴·`clarity.ms` 없음), 041 단언(`details.explain`·`details.terms` 자리)이 고치지 않은 채 통과한다.
- `node --check web/admin/admin.js`·`node --check web/scripts/measure-landing-sections.mjs`.
- node 논리 확인(pytest 안 — 033 `test_clarity.py` 처럼 node 로 admin.js 를 가짜 `window`·`document` 와 싣고 그리기가 아닌 계산·글자만 본다. 기대값은 화면 코드가 아니라 실행 세션이 Python 으로 따로 푼 값. node 가 없으면 CI 에서는 실패):
  - 도달: f 10·d 30 이면 s 50 의 도달 13.9%, f 20·d 60 이면 s 90 의 도달 24.8%, 표 세 기기·d 45·50·45 의 절마다 % 가 Python 값과 ±1%p, 단계 = ⌈도달 × 5⌉.
  - 숨김 사유: n 0·null·3, d 없음·101, d = f − 1 → §3.4 의 글 차례대로.
  - 답 문장 세 갈래(세션 0·도달 보임·모두 숨김) 글자가 §3.6 틀과 같다 / 탭 합치기 — `app:spread` 두 기기(세션 4·2) → 'Clarity 세션 6'·가중 평균.
  - 가짜 DOM 이 지나치게 커지는 항목은 아래 설계 세션 확인으로 넘기고 §7 에 적는다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`(dist 에 admin 없음).
- 커밋(각 300줄 이하): ① 측정 스크립트 ② 표시 주석과 실행 프롬프트로 받은 표·이름표·모형(admin.js) ③ 덩어리 HTML·CSS·설명 ④ 덩어리 JS(요약·랜딩·탭으로 나눠) ⑤ 정적 단언·node 논리 확인 ⑥ §6 문서·§5·§7.

**PR 안 — 설계 세션 확인(머지 전 — 이 Mac 의 Chrome·로컬 Docker 또는 Browser pane. 결과와 돌린 세션을 §5 에 적고, 어긋나면 머지 전에 고친다)**:
- 측정 스크립트: PR 브랜치에서 `node web/scripts/measure-landing-sections.mjs` 를 돌려 표시 주석 사이가 바뀌면 그 커밋을 더한다(실행 프롬프트로 준 값과 같으면 커밋 없음). 두 번 돌리면 표가 같다 / 끝난 뒤 Chrome 프로세스·임시 프로필·열린 포트 0 / 127.0.0.1 밖 성공 요청 0(출력) / `web/public` 에 변경을 둔 채 돌리면 멈춘다 / 표시 주석 하나를 지운 admin.js 면 멈추고 파일은 그대로다.
- 브라우저(036 §4 방식 — 127.0.0.1 에만 게시한 테스트 compose + 가짜 백엔드, Clarity·접속 응답을 바꿔 줄 수 있게):
  - 도달: 세션 40, landing 휴대폰 12·태블릿 6·데스크톱 20, d 45·50·45 → 카드 셋, 절마다 % 가 node 확인과 같고 rect 단계 클래스 = ⌈도달 × 5⌉, 첫 화면 선 y = f × 6·평균 깊이 선 y = d × 6, 연결선 수 = 절 수, '추정(모형)'·'세션 n'·'랜딩 측정 날짜 · 커밋'.
  - 숨김: 휴대폰 n 0 → '세션 없음 — …휴대폰…' / 태블릿 n 3 → '세션 3 — 5 미만…' / 데스크톱 d = f − 1 → '첫 화면 안' / 숨긴 카드에 윤곽·첫 화면 선은 있고 평균 깊이 선·도달 % 는 없다 / 셋 다 숨김 → 상자 하나·카드 0.
  - 요약: 세션 0 → 신호 행 0·'값 없음 — …' 상자 하나·답 문장 둘째 틀 / sessionPct 25 → '25.0% · 세션 1/4', 색 중립 / countries 7행 → 5행 + '2개 더' 접힘, 연 채 다음 묶음 뒤에도 열림 / countries null + metrics `Country` 행 → 받은 그대로 / 받은 지표 `ScrollDepth`·`Country/Region`·모르는 `FooBar` → '스크롤 깊이'·'나라'·'FooBar'.
  - pages: `error` + 값 → 값 + '불러오지 못함 · 마지막 성공' / 값 null → '값 없음'·탭 줄 'Clarity –' / `rowLimitHit` → ▲ 줄 / `fetchedAt` 이 `refreshSec` × 3 을 넘음 → 오래됨. Clarity `unconfigured` → '연결 안 됨 — 토큰 없음 …'·답 문장 없음·랜딩·탭 칸 빔, 다른 덩어리 그대로.
  - 탭: access `tabs` spread 325·history 112 + `app:spread` 두 기기(세션 4·2) → '처음 들어옴 325'·'Clarity 세션 6'·가중 평균 / access `error` → '처음 들어옴 –' / access `window` `7d` → '서버 기록 7일' / `app:other` 세션 → '(기타)' 행.
  - 답 문장: 세 갈래(세션 0·도달 보임·모두 숨김) 글자가 §3.6 틀과 같다. 다시 그리기: Clarity 응답이 같고 접속만 바뀌면 탭 칸만 새 노드이고 요약·랜딩 노드와 접힘·글자 선택은 그대로다.
  - 안전: countries 이름·지표 이름·행 값·groups 의 모르는 page 값에 `<img src=x onerror=alert(1)>`·`javascript:alert(1)`·U+202E → 글자로, img 0·대화상자 0·title 밖 속성 0.
  - 폭 1280·768·360: `scrollWidth ≤ innerWidth`, 절·신호 이름이 잘리지 않음, 360 에서 기기 카드 세로·타일 2열, CSP 위반·Uncaught 0. 사진 셋(1280 정상·360 정상·360 세션 0 과 셋 다 숨김)은 PR 본문에.

**배포 뒤 — 사람·설계 세션(완료 조건 아님, status 비고 "043 운영 확인 대기")**: 동의 세션이 쌓인 뒤 랜딩 기기별 평균 깊이가 그 기기의 첫 화면 % 보다 큰지 본다 — 작으면 Clarity 깊이가 첫 화면을 넣지 않는다는 뜻이라 모형을 다시 정한다 / 페이지 묶음에 `app:<탭>` 이 갈려 오는지(주소에 탭이 실리는지).

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행 web 칸 끝에 "· Clarity 덩어리 v3(043 — 요약 타일·불만 신호·나라, 랜딩 도달 추정(평균 깊이 하나로 푼 모형·세션 5 미만이나 첫 화면 안이면 숨김)·윤곽 그림, 대시보드 탭별 진입·Clarity 활동, 랜딩 절 표 `LANDING_SECTIONS`)", 비고 끝에 "· 043 운영 확인 대기(동의 세션 뒤)". 알려진 빚에서 `(036) Clarity 칸은 traffic 타일과 받은 지표 목록뿐 — 정규화 값(summary·countries·pages)은 040 응답에 있고 그리는 것은 043` 줄(040 이 고친 글)과 `(041) 접속 절의 Clarity 설명은 035 칸(받은 지표) 기준 — 043 이 고쳐 쓴다` 줄(042 가 고친 글)을 지운다. 더하는 빚 셋: `(043) 랜딩 도달은 평균 깊이 하나로 푼 모형 추정이다(분포 아님) — Clarity 평균 스크롤 깊이에 첫 화면이 드는지 확인 못 함`, `(043) 랜딩 절을 바꾸면 measure-landing-sections 를 다시 돌려야 한다(Node ≥22.4·Chrome — CI·샌드박스 밖) — test_admin 은 절 id·순서만 대조하고 높이만 바뀐 것은 못 잡는다`, `(043) Clarity 주소에 탭이 실리는지 확인 못 함 — 안 실리면 대시보드 탭별 Clarity 줄이 모두 실시간 스프레드에 몰린다(040 이 탭 없는 주소를 app:spread 로 센다)`.
- `CLAUDE.md` — 스펙 인덱스 043 행 상태 → DONE. §2 `scripts/` 줄 → "랜딩 글꼴 서브셋 스크립트·글자 목록(uv run — 022)·랜딩 절 위치 측정 스크립트(node, 로컬 Chrome — 043), 빌드·CI 에 들지 않음" — 실행 세션은 인덱스 상태만 고치지만(CLAUDE.md §5), 043 뒤 §2 scripts 줄이 거짓이 되므로 설계 세션이 허락한 예외다.
- `docs/context/architecture.md` — '현재 구조' admin 항목의 `web/admin/` 설명 끝에 "· Clarity 덩어리 v3(043) — 랜딩 절 표 `LANDING_SECTIONS`(admin.js 표시 주석 사이 — `web/scripts/measure-landing-sections.mjs` 가 고쳐 쓴다, Node 내장·로컬 Chrome CDP·바깥 호출 0)·평균 깊이로 푼 도달 모형·랜딩 윤곽 그림 SVG".
- `docs/context/dev-setup.md` — 랜딩 문단의 글꼴 서브셋 문장 뒤에 "랜딩 절의 구성·순서·높이를 바꾸면 `web/public` 을 커밋한 뒤 `node web/scripts/measure-landing-sections.mjs`(Node ≥22.4·로컬 Chrome, 경로는 env `CHROME` — CI·샌드박스에서는 돌리지 않는다)로 관리자 화면의 랜딩 절 표를 다시 잰다 — 표에 있는 절 id 가 사라지거나 순서가 바뀌었는데 안 하면 `test_admin.py` 가 멈춘다(새 절은 막지 않는다)."
- `docs/context/product.md` — 용어 절에 "**랜딩 도달 추정**(관리자 화면, 043): Clarity 평균 스크롤 깊이와 미리 잰 랜딩 절 위치로 '그 절 머리까지 내려온 세션의 비율' 을 기기마다 푼 모형 값 — 분포가 아니고 화면 분석에 동의한 방문자만." 기능 목록 admin 행의 "Clarity" → "Clarity(스크롤·머문 시간·불만 신호·랜딩 도달 추정)".
- `docs/specs/036-admin-v2.md` — §3.4 접속 머리 줄의 "Clarity(그대로 — 043 이 바꾼다)"(042 가 쓴 글) → "Clarity(043)", 'Clarity(Clarity 요약)' 줄(041·040 이 고친 글) → "Clarity — 043(요약·나라·랜딩 도달 추정·대시보드 탭별·받은 지표, 창 줄 밖 맨 아래)". §3.6 차트 종류 끝에 "랜딩 윤곽 그림·도달 막대(043 §3.4)". §4 정적 단언 끝에 "랜딩 절 표·측정 스크립트·Clarity 설명 단언은 043 §4".
- `docs/specs/041-admin-explain.md` — Clarity 쪽 글을 043 으로 넘긴다(서버 기록 쪽은 042 가 이미 고쳤다): §2 의 "접속 절의 서버 기록 설명은 042 §3.6 이 정한다(Clarity 설명은 043 이 고쳐 쓴다)."(042 가 쓴 글) → "접속 절의 서버 기록 설명은 042 §3.6, Clarity 설명은 043 §3.7 이 정한다." §3.3 접속 절의 '읽는 값'·'창' 가운데 Clarity 조각과 개요 '화면 공통' 의 '시각(… Clarity 칸만 UTC)' 을 §3.7 대로, "'Clarity' 카드 끝: 세션, 봇 세션, 사용자, 세션당 페이지, 받은 지표" → "'Clarity' 카드 끝: 043 §3.7". §3.1 타일 부제의 "Clarity 카드의 세션·봇 세션·사용자·세션당 페이지" → "Clarity 카드의 타일 다섯(043 §3.3)". §3.4 대시보드 탭의 '적는 곳' 끝에 "·Clarity 탭별 행(043)". §4 설계 세션 확인 8 의 "Clarity 넷" → "Clarity 다섯(043 §3.3)".

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 022(랜딩 세션) — §3.3 끝과 §4 에 "절 구성·순서·높이를 바꾸는 PR 은 `node web/scripts/measure-landing-sections.mjs` 를 돌려 관리자 화면(admin.js)의 랜딩 절 표를 고친다 — test_admin 이 표의 절 id 가 landing.html 에 있고 순서가 같은지 본다 — 새 절은 막지 않는다".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
