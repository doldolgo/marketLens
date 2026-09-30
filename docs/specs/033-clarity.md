# 033 — clarity

상태: TODO | 의존: **032 privacy 가 게시된 뒤에 켠다**(코드는 먼저 머지해도 된다 — ID 가 비어 있으면 아무것도 안 한다, §3.1). 계약을 쓰는 스펙: 032 privacy(처리방침 파일·분석 거부 값), 002 web-shell(URL 쿼리 상태), 022 landing(정적 랜딩), 027 observability(caddy 쿼리 키 삭제), 007 deploy(nginx 정적 규칙), 013·014(기록 탭 심볼 검색)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 방문자들이 랜딩과 대시보드에서 무엇을 보고 누르는지(탭 전환·표에서 기록 탭으로 넘어가기·스크롤·클릭)를 Microsoft Clarity 의 녹화·히트맵·태그로 본다. 서버 기록(027)은 들어온 주소와 머문 시간까지만 알고, 들어온 뒤의 조작은 요청을 만들지 않아 보지 못한다. 수집은 처리방침(032)이 게시된 뒤에만 켜지고, 분석을 거부했거나 GPC 를 켠 방문자, 유럽 시간대 브라우저, 관리자 페이지는 Clarity 스크립트를 받지도 않는다.

## 2. 범위
- 만드는 것: `web/public/clarity.js`(설정 한 곳 + 불러올지 판단), 랜딩·대시보드 HTML 의 그 파일 한 줄과 가림 해제 속성, `web/src/shared/` 의 Clarity 호출 두 개(태그·이벤트), 셸의 탭 태그·이벤트, nginx 캐시 규칙 한 줄, 계약 테스트 `server/tests/test_clarity.py`, 런북 `docs/runbooks/clarity.md`(Clarity 대시보드 설정·켜고 끄기, 사람용).
- 하지 않는 것:
  - 처리방침 문구·국외 이전·Clarity 약관이 요구하는 고지·거부 버튼 — 032. Clarity Data Export API·토큰과 관리자 화면의 Clarity 요약 — 034b·035(034b 가 토큰 절차를 이 스펙의 런북 `clarity.md` 에 더한다).
  - 동의 배너(opt-in). 기본은 거부 방식(opt-out)이다 — 법률 확인은 사람(§3.3).
  - `identify`(방문자 식별) — 계정이 없고 쓸 곳이 없다.
  - 관리자 페이지·처리방침 페이지의 Clarity, Google Fonts·jsDelivr 자체 호스팅, 기록 탭 차트(canvas) 녹화.
- 바꾸는 기존 것:
  1. 002·003 — 스프레드·갭·선선갭 검색어(`s.q`·`g.q`·`p.q`)를 URL 상태에서 뺀다(메모리만 — 기록 탭·입출금 레이더 검색과 같아진다). Clarity 는 전송할 때마다 그때의 페이지 주소를 통째로 싣는다 — 입력칸을 가려도 주소의 검색어는 그대로 간다.
  2. 002 — URL 쓰기를 둘로 나눈다(§3.5). Clarity 가 `history.replaceState` 를 덮어써서 주소가 바뀔 때마다 녹화를 끊고 DOM 전체를 다시 보낸다(스프레드 탭 gzip ≈110KB, 2026-09-28 실측).
  3. 002·013·014 — URL 의 `sym` 과 기록 탭 검색 확정 값을 심볼 형식으로 제한한다(§3.5). 입력한 글자가 가림 해제된 제목·주소·태그로 Clarity 에 가기 때문이다. 027 빚 "기록 탭 검색칸 값(URL sym)은 검증 없이 기록된다" 를 갚는다.
  4. 022 — 랜딩 `<head>` 에 파일 한 줄, `<body>` 에 가림 해제 속성.
  5. 007 — nginx 가 `/clarity.js` 를 매번 재검증하게 한다.
- 담당: 033 은 사용자 담당이다. 002·003(팀원)·013·014(hereokay)는 코드만 바꾸고 스펙 문구는 **고치지 않는다**(CLAUDE.md §5) — §6 "담당자에게 제안" 을 PR 본문에 적는다. 007·022·027 은 이 PR 이 고친다.

## 3. 동작

### 3.1 설정 한 곳 — 켜고 끄기
- 설정은 `web/public/clarity.js` 머리의 세 값이다: Clarity 프로젝트 ID(공개 값 — 페이지 소스에 그대로 보인다), 켤 페이지 목록(`landing`·`app`), 유럽 시간대 제외 스위치(기본 켬 — §3.2). ID 가 빈 문자열이면 파일은 아무것도 하지 않는다. **이 PR 은 빈 값으로 머지한다.**
- 켜는 순서(사람, 런북): 032 처리방침 게시 → Clarity 대시보드 설정(§3.8) → ID 를 넣는 PR. ID 가 비어 있지 않은데 처리방침 파일 `web/public/privacy.html`(032)이 없거나, 032 자리표시자 표식 `〔` 가 한 글자라도 남아 있거나, `Clarity`·`kt.analytics` 글자가 없으면 계약 테스트가 실패한다 — 게시 전에 켜는 실수를 CI 가 막는다(032 §3.6 과 같은 표식).
- 끄기: ID 를 비우는 PR(배포 뒤 다음 페이지 로드부터). 대시보드만 끄려면 목록에서 `app` 을 뺀다. 파일 응답은 매번 재검증되므로(nginx `Cache-Control: no-cache`, `/app/` 아래는 이미 no-store — 007) 브라우저에 옛 설정이 남지 않는다. Clarity 프로젝트 삭제는 데이터가 전부 사라지고 되돌릴 수 없어 끄는 방법으로 쓰지 않는다.

### 3.2 불러오는 조건 — 하나라도 걸리면 부르지 않는다

| 확인 | 멈추는값 |
|---|---|
| ID | 빈값 |
| 호스트 | 그밖 |
| 페이지 | 목록밖 |
| 시간대 | 유럽 |
| GPC | `true` |
| 거부값 | `denied` |
| 저장소 | 예외 |

- 호스트는 www 없는 `kimptrack.com` 하나다. `www.kimptrack.com` 은 부르지 않는다 — 분석 거부 값은 브라우저 저장이라 출처마다 따로여서, www 없는 주소에서 거부한 방문자가 www 로 오면 거부가 보이지 않는다. localhost·탄력 IP 직접 접속·`admin.kimptrack.com` 도 이 조건으로 빠진다.
- 페이지: 경로가 정확히 `/` 면 `landing`, `/app/` 로 시작하면 `app`. 처리방침·관리자 화면 파일은 `clarity.js` 를 싣지 않는다(조건과 이중).
- GPC: `navigator.globalPrivacyControl === true` 면 부르지 않는다. Clarity 도 GPC 면 시작하지 않지만(clarity-js 0.8.71) 스크립트를 받는 순간 방문자 IP 가 Microsoft 로 가므로 아예 받지 않는다. DNT 는 보지 않는다(폐기된 신호이고 Clarity 도 따르지 않는다 — Clarity FAQ).
- 거부 값(032 §3.3 계약 복사): localStorage 키 `kt.analytics` — `denied` 면 거부, 키 없음·`granted`·그 밖의 값이면 허용(거부 방식). GPC 가 `granted` 보다 앞선다(`granted` 여도 GPC 면 부르지 않는다). 저장소를 읽다 예외가 나면 거부로 본다 — 거부를 저장할 수 없는 브라우저에서는 켜지 않는다. 값을 쓰는 곳은 032 방침 페이지 하나이고 이 파일은 읽기만 한다.
- 시간대: 스위치가 켜져 있으면 `Intl.DateTimeFormat().resolvedOptions().timeZone` 이 `Europe/` 로 시작하거나 `Atlantic/Reykjavik`·`Atlantic/Canary`·`Atlantic/Madeira`·`Atlantic/Azores`·`Asia/Nicosia`·`Asia/Famagusta` 이면, 또는 시간대를 읽다 예외가 나면 부르지 않는다. EEA·영국·스위스 방문자에게 받지 않은 동의 신호를 보내지 않으려는 것이다 — Microsoft 는 그 지역에 유효한 동의 신호를 요구한다(Consent API v2 문서, 2025-10-31 부터). 시간대는 추정이다: 유럽에서 한국 시간대로 둔 기기는 불러오고, 유럽 밖 해외 영토(레위니옹 등)는 빠지지 않으며, `Europe/` 는 EEA 밖(러시아·튀르키예 등)도 빼지만 안전한 쪽으로 틀린다. 스위치는 사람이 법률 확인 뒤에만 끈다(§3.3).
- GPC·거부·시간대로 부르지 않을 때는 예전 방문에서 남은 Clarity 저장값을 지운다: 쿠키 `_clck`·`_clsk`(도메인 `.kimptrack.com` 과 호스트 전용 두 모양 모두), sessionStorage `_cltk`.
- 이미 열린 탭(032 가 처리방침에 같은 문장을 적는다): Clarity 를 부른 페이지는 `storage` 이벤트를 듣는다. 다른 탭(032 방침 페이지 — 대시보드 헤더 링크는 새 탭으로 연다)이 `kt.analytics` 를 `denied` 로 바꾸면 곧바로 `consentv2` 를 `ad_Storage: "denied"`·`analytics_Storage: "denied"` 로 부른다 — Microsoft 문서대로 쿠키를 지우고 세션을 끝낸 뒤 쿠키 없는 모드로 수집을 이어가므로, 그 탭이 보이는 상태면 곧바로, 숨어 있으면 다시 보일 때(`visibilitychange`) 한 번 새로고침한다. 새로 열린 문서는 거부 값을 읽어 Clarity 를 부르지 않는다. 같은 출처(`kimptrack.com`) 탭끼리만 이벤트가 간다 — www 에는 Clarity 가 없으므로 빈틈이 아니다.

### 3.3 불러오는 방법
- `clarity.js` 는 두 HTML 의 `<head>` 에 `defer` 로 한 번 실리고, 빌드된 `dist/index.html` 에서 앱 모듈 스크립트보다 **앞**이다(둘 다 문서 순서대로 실행된다). 판단은 파일이 실행될 때 한 번 한다.
- 조건이 맞으면 곧바로 Clarity 표준 대기열 함수 `window.clarity`(태그가 오기 전 호출을 모아 둔다)를 만들고, 첫 호출로 `consentv2` 를 두 키를 다 넣어 부른다 — `ad_Storage: "denied"`, `analytics_Storage: "granted"`. 옛 API `consent` 는 두 값에 같은 상태를 주고 폐기 예정이라(Clarity 문서), 인자 없는 `consentv2` 는 판마다 기본값이 달라(2026-09-28 관측은 둘 다 granted, 0.8.71 코드는 둘 다 denied) 쓰지 않는다. 광고 거부라 Microsoft 광고 쿠키 동기화 요청(`c.clarity.ms/c.gif`)이 없고 1차 쿠키 `_clck`(1년)·`_clsk`(1일)만 `.kimptrack.com` 에 생긴다(2026-09-28 실측, clarity-js 0.8.71 코드).
- 태그 스크립트 `https://www.clarity.ms/tag/<ID>`(async)는 `DOMContentLoaded` 뒤에 넣는다 — 대시보드 셸의 첫 URL 정리(§3.5)가 끝난 뒤 Clarity 가 첫 주소를 읽고, 첫 화면 그리기와 겹치지 않게. 대기열이 앱보다 먼저 생기므로 셸이 태그보다 먼저 부른 태그·이벤트도 순서대로 실린다.
- 파일은 예외를 밖으로 던지지 않는다 — 실패하면 Clarity 만 없고 페이지는 그대로다. 태그가 막히면(광고 차단) 대기열 함수가 호출을 쌓기만 한다(탭 전환 수만큼 — 무시할 크기).
- 부르는 방문자에게는 모두 같은 신호를 보낸다. 이 `analytics_Storage: "granted"` 는 받은 동의가 아니라 "거부하지 않음" 이다 — 그래서 동의 신호가 필요한 지역(EEA·영국·스위스)으로 보이는 브라우저는 §3.2 시간대 확인으로 아예 부르지 않는다. 스위치를 끄는 것(유럽 방문자에게도 이 신호를 보내는 것)은 **사람 확인**(법률) 뒤에만 한다.

### 3.4 가림
- Clarity 가림 모드는 Balanced(기본 — 숫자·이메일을 가린다)로 둔다. 대시보드 `#root`(`web/index.html`)와 랜딩 `<body>` 에 `data-clarity-unmask="true"` 를 달아 시세·김프 숫자가 녹화에 보이게 한다 — 두 화면의 숫자는 공개 시세다. 입력칸·드롭다운은 모든 모드에서 가려지고 풀 수 없다(Clarity 문서). 새 페이지는 속성을 달지 않는 한 가림이 기본이다.
- 가림 해제 영역 안에 방문자가 입력한 글자를 다시 쓰지 않는다. 검색어는 표를 거르기만 하고 화면에 되쓰지 않는다(지금 그렇다). 기록 탭 제목의 선택 심볼은 §3.5 형식만 들어간다.

### 3.5 URL — 검색어와 녹화 조각
Clarity 동작(clarity-js 0.8.71, 2026-09-25 커밋 코드를 2026-10-01 에 읽음): 태그가 `history.pushState`·`replaceState` 를 **인스턴스 속성**으로 덮어쓰고, 호출 뒤 주소(해시 제외)가 바뀌었으면 녹화를 멈췄다가 250ms 뒤 새 페이지로 다시 시작해 DOM 전체를 다시 보낸다. 멈춘 동안 받은 API 호출은 모았다가 새 페이지에 싣는다. 전송마다 그때의 `location.href`(해시 포함, 2,048자까지)를 싣는다. Clarity 의 URL 매개변수 가림은 지원 요청으로만 되고 페이지 주소에만 적용된다(참조·클릭 주소는 안 가린다 — Clarity FAQ).
- 검색어 세 키 `s.q`·`g.q`·`p.q` 는 URL 에 싣지 않는다 — 새로고침·공유 링크에서 검색어는 복원되지 않는다. 셸은 첫 렌더 전(앱 모듈이 실행될 때) URL 에 남은 세 키를 지우고 값은 버린다(옛 링크). 027 caddy 의 세 키 삭제는 옛 링크 때문에 그대로 둔다.
- `sym` 은 영문 대문자·숫자 1~20자만 URL 에서 읽고 쓴다 — 밖이면 기본값(BTC)이고 URL 에서 빠진다(002 의 "허용 밖 값은 기본값" 과 같다). 기록 탭 검색은 Enter 때 대문자로 바꾼 값이 이 형식일 때만 선택한다 — 아니면 선택하지 않고 입력칸을 그대로 둔다. 표 클릭·랜딩 링크의 심볼은 거래소 심볼이라 이 형식이다.
- URL 쓰기는 둘로 나눈다. `tab` 키를 쓸 때만 `window.history.replaceState`(Clarity 가 덮어쓴 것)를 부른다 — 탭을 바꾸면 Clarity 새 페이지가 되어 탭 주소마다 히트맵이 따로 생기고 전송 한도(§3.7)도 새로 센다. 그 밖의 쓰기(탭 접두어 키·`sym`·`dropParams`·첫 정리)는 원래 함수 `History.prototype.replaceState` 를 `window.history` 에 걸어 부른다 — Clarity 가 모르므로 필터를 바꿔도 녹화가 이어지고, 다음 전송 주소에 새 필터가 실린다. Clarity 가 없으면 둘은 같은 함수다.
- 이 우회는 Clarity 가 인스턴스 속성을 덮어쓰는 구현에 기댄다. 바뀌면 필터마다 녹화가 조각나지만 검색어가 URL 에 없으므로 개인정보 문제는 아니다 — §4 배포 뒤 확인, status 빚.

### 3.6 태그·이벤트
- `shared/` 에 두 함수를 둔다 — 태그 두기(키, 값)와 이벤트 남기기(이름). `window.clarity` 가 함수가 아니면 아무것도 안 한다. 키·이름은 아래 고정 토큰만 쓰고 방문자 입력은 싣지 않는다.

| 이름 | 종류 |
|---|---|
| `tab` | 태그 |
| `sym` | 태그 |
| `tab_<id>` | 이벤트 |
| `pivot_history` | 이벤트 |

- `tab`: 값은 탭 id(`spread`·`history`·`gap`·`pp`·`health`·`flow`). 첫 화면에서, 그리고 탭이 바뀐 뒤마다 `tab` URL 을 쓴 **다음에** 둔다 — Clarity 태그는 페이지마다 새로 시작하므로 새 페이지에 실려야 한다.
- `sym`: 기록 탭이 보이는 동안의 선택 심볼(§3.5 형식). 기록 탭에 들어갈 때와 심볼이 바뀔 때.
- `tab_<id>`: 탭 단추로 바꿀 때마다. 첫 화면에는 남기지 않는다. `pivot_history`: 스프레드 표 행을 눌러 기록 탭으로 갈 때(이때는 `tab_history` 대신).
- 한도(Clarity 문서): 키·값 각각 255자 미만, 페이지당 태그 128개 — 위 규칙이면 페이지당 몇 개다. 랜딩은 태그·이벤트가 없다 — 페이지가 하나이고 링크 클릭은 Clarity 가 클릭 주소로 남긴다.

### 3.7 받아들이는 한계
- 대시보드 부하: 스프레드 탭은 DOM ≈8,800요소에 초당 ≈130행이 재정렬돼 Clarity 전송이 ≈1KB/s(시간당 ≈3.4MB)다, 스크립트 25KB(2026-09-28 실측). 방문자 브라우저의 CPU 는 §4 에서 재고, 스프레드 탭 60초 스크립트 시간이 Clarity 를 켠 쪽에서 1.5배를 넘으면 목록에서 `app` 을 뺀다(기준값은 **사람 확인**).
- 녹화 길이(clarity-js 0.8.71): Clarity 페이지 하나가 전송 128회(스프레드 탭 ≈57분 실측) 또는 2시간에 닿으면 멈추고, 그 문서는 새로고침 전까지 다시 시작하지 않는다(탭을 바꿔도). 세션당 페이지 128개. 대시보드를 종일 띄워 두는 사용은 앞 1시간 안팎만 남는다.
- 기록 탭 차트는 canvas 라 녹화에 안 보인다(Clarity 문서). 스크롤 맵은 문서 스크롤 기준이라 탭 본문 안에서 스크롤하는 대시보드에서는 쓸모없고 랜딩만 맞다. 표가 매초 재정렬돼 클릭 히트맵은 행 위치로 모인다 — 탭·피벗은 §3.6 으로 본다.
- 빠지는 방문자: 광고 차단(EasyPrivacy·AdGuard 가 `clarity.ms` 를 막는다)·GPC·거부·유럽 시간대·www. Clarity 의 숫자는 하한이고, 총량은 caddy 기록(027)과 WebSocket 접속 수로 본다.
- 보존·삭제(Clarity 문서, 2026-10-01 확인): 녹화 재생 데이터 30일, 클릭 데이터와 즐겨찾기·라벨 붙인 녹화 9개월. 한 방문자 것만 지울 수 없다(프로젝트를 지우는 것뿐). 처리방침 문구는 032.

### 3.8 Clarity 대시보드 설정 (사람 — 런북 `clarity.md`)
- Settings → Setup → Advanced: **Cookies 끔**(동의 모드 — 신호 전에는 쿠키가 없고, 사이트가 보낸 `analytics_Storage: granted` 로 1차 쿠키가 생겨 탭 전환 페이지들이 한 세션으로 묶인다), **Bot detection 켬**.
- Masking: Balanced, 요소 규칙 없음(가림은 사이트 속성으로만 정한다 — 설정이 레포에 남게).
- IP blocking: 운영자 고정 IPv4(CIDR 가능, IPv6·모바일·VPN 은 안 된다 — Clarity 문서). 목록은 **사람 확인**.
- 팀: 녹화를 보는 사람은 개인정보를 다루는 사람이다 — 관리자·구성원 범위는 **사람 확인**.
- 선택: Clarity 지원 메일로 URL 매개변수 `s.q`·`g.q`·`p.q` 가림을 요청한다(옛 링크 대비, 페이지 주소에만 적용된다).
- GA·GTM 연동은 켜지 않는다. Data Export API 토큰은 034b 몫 — 034b 가 같은 런북에 토큰 절을 더한다(Clarity 운영을 한 런북에).
- 런북은 각 항목의 확인 방법(§4 배포 뒤)과 되돌리기(ID 비우기 PR)를 함께 적는다.

### 3.9 엣지
- 쿠키 도메인: Clarity 는 `.kimptrack.com` 에 쓰므로 `admin.kimptrack.com`·공개 `/api` 요청에도 실려 간다. 관리자 nginx 는 Cookie 를 비우고(029), caddy 기록은 헤더를 지우며(027), 백엔드는 쿠키를 읽지 않는다.
- 공개 페이지에는 지금 CSP 가 없다. 나중에 더하면 `www.clarity.ms`·`scripts.clarity.ms`·`*.clarity.ms`(전송)를 열어야 한다. 관리자 CSP(`default-src 'self'`, 029)는 그대로 — 거기서는 부르지 않는다.
- 배포 중 옛 `clarity.js` 와 새 HTML 이 섞여도 다음 로드부터 맞는다. canary(027)·uptime 은 스크립트를 실행하지 않아 Clarity 에 안 잡힌다.
- `/?utm_*` 은 022 의 301 로 `/app/?utm_*` 가 된다 — Clarity 는 대시보드 주소에서 유입 경로(utm)를 읽는다. utm 키는 URL 상태가 아니라 그대로 남는다.

## 4. 검증
**PR 안 — 실행 세션 완료 조건**
- server `ruff check . && pytest -q` — `tests/test_clarity.py` 가 파일을 읽어 단언한다: `clarity.js` 의 ID 는 빈 문자열이거나 영숫자 1~32자이고 페이지 목록은 `landing`·`app` 안에서만 / 유럽 시간대 스위치가 불리언 / ID 가 비어 있지 않으면 `web/public/privacy.html` 이 있고 `〔` 가 없고 `Clarity`·`kt.analytics` 가 있다 / 파일에 `consentv2`·`ad_Storage`·`analytics_Storage`·`"granted"`·`globalPrivacyControl`·`kt.analytics`·`_clck`·`_clsk`·`_cltk`·`DOMContentLoaded`·`https://www.clarity.ms/tag/`·`resolvedOptions`·`Europe/`·`storage`·`visibilitychange`·`location.reload` 가 있고, 거부 값 리터럴 `'denied'`(또는 `"denied"`)가 있고 `'off'`·`"off"` 가 없으며, `'consent'`·`identify` 호출과 `www.kimptrack.com` 이 없다 / `web/index.html`·`web/public/landing.html` 이 파일을 `defer` 로 한 번씩 싣고 `#root`·랜딩 `<body>` 에 `data-clarity-unmask="true"` / `web/admin/*` 에 `clarity.js`·`clarity.ms` 가 없다(관리자 스크립트는 029 단언대로 `admin.js` 하나 — 035 의 Clarity 칸·`/svc/api/admin/clarity`·콘솔 링크는 걸리지 않는다) / `nginx.conf` 에 `location = /clarity.js` 와 `no-cache` / `web/src` 에 `useUrlState('s.q'`·`'g.q'`·`'p.q'` 가 없고 `urlState.ts` 에 `History.prototype.replaceState` 가 있다.
- web `npm run lint && npm run build`, `node --check web/public/clarity.js`(oxlint 대상 밖). 빌드된 `dist/index.html` 에서 `clarity.js` 가 앱 모듈보다 앞, `dist/clarity.js` 있음.
- 로컬 브라우저 — docker 통합 기동(:8080)에 헤드리스 크롬 `--host-resolver-rules` 로 `kimptrack.com`·`www.kimptrack.com` 을 로컬에, `*.clarity.ms` 를 닫힌 포트에 묶는다(요청은 시도만 되고 나가지 않는다). 시험 ID 는 커밋하지 않는 로컬 사본에만 넣는다:
  1. 기본: `DOMContentLoaded` 전에는 `clarity.ms` 요청이 없고 뒤에 `tag/<시험ID>` 1회, `window.clarity.q` 첫 항목이 `consentv2` 와 두 값 그대로.
  2. GPC 주입·`kt.analytics=denied`·`kt.analytics=granted` + GPC 주입·localStorage 예외 주입·시간대 `Europe/Berlin`(CDP `Emulation.setTimezoneOverride`) → 요청 0, `window.clarity` 없음, 미리 심은 `_clck`·`_clsk`(두 도메인 모양)·`_cltk` 가 지워진다. `kt.analytics=granted`·GPC 없음·`Asia/Seoul` → 태그 1회.
  3. 두 탭: 탭 A `/app/` 에서 Clarity 가 돈 뒤 탭 B `/privacy` 에서 [분석 거부] → 탭 A 대기열에 `consentv2` 두 값 `denied`, A 를 다시 보이게 하면 한 번 새로고침되고 그 뒤 `clarity.ms` 요청 0.
  4. 호스트 `www.kimptrack.com`·`localhost:8080` → 요청 0.
  5. `/app/?s.q=btc&s.view=rev` → `DOMContentLoaded` 때 `location.search` 가 `?s.view=rev`, 검색칸은 비어 있고 검색을 입력해도 URL 이 그대로.
  6. 첫 스크립트로 인스턴스 `history.replaceState` 를 감싸 센다: 필터 세 번·기록 탭 심볼 변경 → 0회, 탭 단추 두 번 → 2회, 표 행 클릭 → 1회. 바뀔 때마다 대기열에 `set tab <id>` 뒤 `event tab_<id>`(행 클릭은 `pivot_history`).
  7. `?tab=history&sym=%ED%99%8D` → BTC 이고 URL 에 `sym` 이 없다. 기록 탭 검색 `홍길동` Enter → 선택 안 됨·입력칸 그대로, `eth` Enter → ETH·`sym=ETH`·대기열에 `set sym ETH`.
  8. 랜딩: `<body>` 속성, 대기열에 `consentv2` 하나뿐. 002 §4 의 URL 복원 항목(14·18)이 검색어 부분을 빼고 그대로 통과.
- `curl -sI localhost:8080/clarity.js` → `Cache-Control: no-cache`.

**배포·런북 뒤 — 사람**
- 032 게시 확인 → §3.8 설정 → ID PR 머지·배포.
- 실제 크롬(`https://kimptrack.com`): `www.clarity.ms/tag`·`scripts.clarity.ms`·`*.clarity.ms/collect` 요청이 있고 `c.clarity.ms` 는 0건, 쿠키는 `_clck`·`_clsk` 둘뿐(도메인 `.kimptrack.com`), 콘솔 `clarity('metadata', cb, false, true, true)` 의 동의 상태가 ad DENIED·analytics GRANTED.
- 2시간 안 Clarity 녹화: 숫자가 보이고 입력칸은 가려짐, 탭 두 번 → 같은 세션에 페이지 셋과 태그 `tab`, 필터를 여러 번 바꿔도 페이지가 늘지 않음, 이벤트 보임.
- GPC 브라우저(Brave)·거부 값·`www` → `clarity.ms` 요청 0.
- 같은 기기에서 스프레드 탭 60초 Performance 기록(켬·거부)의 스크립트 시간과 1시간 전송량 → §7 에 적고 §3.7 기준으로 `app` 유지 여부를 사람이 정한다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — 표 admin 행 뒤에 `| clarity | - | web: clarity.js(ID·페이지 목록 한 곳, 호스트·유럽 시간대·GPC·거부 값 확인, consentv2 광고 거부·분석 허용, DOMContentLoaded 뒤 태그, 다른 탭의 거부를 storage 이벤트로 받아 새로고침), 랜딩·대시보드 가림 해제, 탭 태그·이벤트, 검색어 URL 제외·필터 URL 쓰기 우회·sym 형식 | ID 비어 있음 — 032 게시 뒤 사람이 켠다 |`. web-shell 행 "탭·심볼·탭별 필터가 URL 쿼리로 복원" 뒤에 "(검색어 제외, 033)". 알려진 빚: `(027) 기록 탭 검색칸 값(URL sym)은 검증 없이 기록된다` 를 지우고, `(027) 탭·필터 조작은 서버가 못 본다 — 후속 브라우저 분석` 을 `(027) 탭·필터 조작은 서버가 못 본다 — Clarity(033)가 본다(차단·GPC·거부·www 방문자 제외)` 로, `(027) 쿼리 키 삭제 목록은 검색 입력이 늘 때 손으로 맞춘다` 를 `(027·033) 검색어는 033 부터 URL 에 안 실린다 — caddy 의 세 키 삭제는 옛 링크용` 으로. 추가: `(033) 필터 URL 쓰기의 Clarity 우회는 clarity-js 가 인스턴스 replaceState 를 덮어쓰는 구현(0.8.71)에 기댄다`, `(033) Clarity 페이지 하나가 전송 128회(≈57분)·2시간이면 그 문서는 새로고침 전까지 녹화가 멈춘다`, `(033) www·광고 차단·GPC·거부·유럽 시간대 방문자는 Clarity 에 없다 — 숫자는 하한`, `(033) 이미 열린 탭은 거부 뒤 다시 보일 때 한 번 새로고침된다(storage 이벤트 — 같은 출처 탭만)`, `(033) 유럽 시간대는 Clarity 를 부르지 않는다 — 시간대 추정이라 빈틈이 있고, 스위치는 법률 확인 뒤 사람이 끈다`.
- `CLAUDE.md` — 스펙 인덱스 033 행 상태 → DONE. §2 runbooks 목록에 `clarity.md  Clarity 대시보드 설정·켜고 끄기 (사람용, 033)`.
- `docs/context/architecture.md` — 런타임 구성 web/ 줄 끝에 "랜딩·대시보드는 `public/clarity.js` 가 조건(ID·호스트·GPC·거부 값)이 맞을 때 Microsoft Clarity 태그를 부른다(033) — 관리자·처리방침 페이지는 부르지 않는다". 현재 구조 web-shell(002) 줄의 `urlState` 설명에 "`tab` 만 `window.history.replaceState`, 나머지 쓰기는 `History.prototype.replaceState` — Clarity 가 필터 변경을 새 페이지로 보지 않게(033), 검색어는 URL 밖" 을 넣고, 현재 구조에 `clarity (033)` 항목(파일·shared 함수·셸 호출 위치 1~3줄).
- `docs/context/dev-setup.md` — web 절: `public/clarity.js` 는 oxlint 대상 밖(`node --check`), 로컬에서는 호스트가 달라 Clarity 를 부르지 않는다, 확인은 §4 의 `--host-resolver-rules` 방법.
- `docs/specs/022-landing.md` — §3.5 스크립트 규칙에 "`<head>` 에 `clarity.js`(033) 한 줄, `<body>` 에 `data-clarity-unmask`" 한 줄.
- `docs/specs/007-deploy.md` — nginx 정적 규칙에 `location = /clarity.js` 의 `Cache-Control: no-cache`(033).
- `docs/specs/027-observability.md` — §2 하지 않는 것의 "브라우저 쪽 분석·개인정보처리방침·폰트 자체 호스팅 — 후속 브라우저 분석 스펙" 을 "브라우저 쪽 분석은 033, 처리방침은 032, 폰트 자체 호스팅은 후속" 으로(032 가 같은 줄을 고치면 합친다), §3.8 "검색 입력이 늘면 쿼리 키 목록도 같이 늘린다" 문장에 "검색어는 033 부터 URL 에 안 실린다 — 세 키 삭제는 옛 링크용" 을 붙인다.
- `docs/runbooks/clarity.md` — 신규. §3.1 순서, §3.8 설정과 확인·되돌리기, 유럽 시간대 스위치(끄는 조건 = 법률 확인), §4 배포 뒤 항목. Data Export 토큰 절은 034b 가 더한다.

**담당자에게 제안** (PR 본문에 적는다)
- `002-web-shell.md` §3.5 — "화면 상태는 URL 쿼리에 실린다" 문단: 제외 목록을 "검색 입력 전부(spreads·gap·pp·history·flow)" 로, `sym` 은 영문 대문자·숫자 1~20자, "URL 쓰기는 `tab` 만 `window.history.replaceState`, 나머지는 원래 함수(033 — Clarity)". §4-4 는 그대로(탭 사이 유지는 메모리).
- `003-spreads.md` §3 필터바 — "심볼 검색은 URL 에 싣지 않는다(033)".
- `013-premium-events.md` §3.5·`014-premium-1m.md` 차트 카드 — "심볼 검색 Enter 는 영문 대문자·숫자 1~20자일 때만 선택(033)".
- `023-domain-tls.md` — `www.kimptrack.com` 을 www 없는 주소로 301 하면 www 방문자도 한 출처(거부 값·Clarity)에 모인다 — 결정은 담당자.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
