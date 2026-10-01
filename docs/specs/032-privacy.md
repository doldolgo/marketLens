# 032 — privacy

상태: DONE | 의존: 027 observability(caddy 접속 기록·CloudWatch Logs 전송 보류), 029 admin(관리자 접속 기록), 030 admin-tunnel(Cloudflare Access·관리자 기록 회전), 022 landing(바닥 바로 가기 nav·sitemap — 자체 서브셋 글꼴·`www` 301 이후 판, #84), 002 web-shell(헤더 — 팀원 담당, 코드만 고친다), 025 slack-alerts(알림 문구), 028 api-allowlist(공개 nginx 모양), 023 domain-tls(apex·www). **사람 값(§3.6)을 PR 안에서 채운 뒤에만 머지한다 — 머지가 곧 게시다.** 033 clarity 는 이 스펙이 게시된 뒤 시작한다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
방문자가 KimpTrack 이 무엇을 기록하고 어디로 보내며 얼마 동안 두는지를 `https://kimptrack.com/privacy` 한 페이지에서 읽고, 화면 분석(033 Clarity)에 동의하거나 동의를 철회할 수 있게 한다 — 화면 분석은 동의한 방문자에게만 켠다(기본 꺼짐). 지금은 caddy 접속 기록(027)과 관리자 접속 기록(029)이 방침 없이 박스 안에 쌓이고, CloudWatch Logs 전송(027 런북 15단계)과 Clarity(033)가 이 페이지를 기다린다. 이 스펙은 사실(무엇을·어디로·얼마나)과 게시 조건까지 정하고, 법률 판단은 사람이 확인한다(§3.6).

## 2. 범위
- 만드는 것: `web/public/privacy.html`(번들 밖 정적 한 파일 — 022 의 landing.html 과 같은 방식), 공개 nginx 위치 셋(`= /privacy`·`= /privacy.html`·`= /app/privacy.html`), 랜딩 바닥·대시보드 헤더 링크, `sitemap.xml` 한 줄, 분석 동의 저장값 `kt.analytics`(§3.3 — 033 이 읽고 쓴다), 계약 테스트 `server/tests/test_privacy.py`.
- 하지 않는 것:
  - Clarity 스니펫·설정·마스킹·검색어 URL 처리와 랜딩·대시보드의 **동의 안내 띠**(033). 이 페이지는 033 이 켤 도구의 사실(§3.4-2)과 동의 관리(§3.3)를 미리 싣는다.
  - 대시보드 글꼴 자체 호스팅(Google Fonts 를 없애는 일 — 랜딩은 022 가 이미 같은 출처의 자체 서브셋 한 파일을 쓴다) — 방침에 사실대로 적는다. 없애기는 002·`docs/design` 을 함께 바꾸는 별도 결정(사람).
  - CloudWatch Logs 전송 자체(027 런북 15단계 — 게시 뒤 사람), 관리자 화면의 방침 링크(036 — 운영자만 쓴다), 영어판, 연락 메일 주소 만들기(사람).
- 바꾸는 기존 것:
  1. 022 — §3.3-8 바닥의 바로 가기 nav 에 링크 한 칸(§3.2), §3.1 경로 표에 `/privacy`, §3.6 sitemap 에 `/privacy` 한 줄(`server/tests/test_landing_seo.py` 의 'loc 은 `/` 하나' 단언도 함께 고친다).
  2. 027 §3.2 — caddy 회전에 하루 주기(§3.5). 지금은 현재 파일이 50MiB 에 닿기 전까지 회전하지 않아, 방문이 적으면 90일보다 오래된 줄이 남는다 — 방침의 90일이 거짓이 된다. 기본 로거(오류 줄)는 IP 를 자르지 않고 지운다(§3.5) — 그 줄은 docker 로그에 크기로만 회전해 90일보다 오래 남을 수 있다.
  3. 030 §3.4·런북 `admin-access.md` 12단계 — 관리자 기록 회전을 주 1회·13개에서 매일·90개로(§3.5). 지금은 `notifempty` 라 쓰지 않은 주는 회전하지 않아 13개가 90일보다 길게 늘어난다.
  4. 002(코드만) — 대시보드 헤더에 링크 하나(`web/src/App.tsx`).
- 담당: 002 는 팀원, 025 는 hereokay 담당이라 이 PR 은 그 스펙들을 **고치지 않는다** — §6 "담당자에게 제안" 을 PR 본문에 적는다(CLAUDE.md §5). 007·022·027·029·030 은 이 레포 주인 담당이라 고친다.

## 3. 동작

### 3.1 페이지와 경로
| 요청 | 응답 |
|---|---|
| `/privacy` | `privacy.html` |
| `/privacy.html` | `301 /privacy` |
| `/app/privacy.html` | `301 /privacy` |

- 공개 nginx(:80)의 정확 일치 위치 셋. `/app/privacy.html` 을 막는 이유: dist 의 파일이 `location /app/` alias 로 CSP 없이 200 으로 나간다. `= /privacy` 는 `try_files /privacy.html =404`(022 의 `= /` 와 같은 모양 — 빌드 때 만든 `.gz` 를 `gzip_static` 이 준다)이고 머리 둘을 `always` 로 붙인다: `Cache-Control: no-cache`(고치면 다음 방문에 바로 보이고, 안 바뀌었으면 304), `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. CSP 는 "이 페이지는 외부 자원도 서버 호출도 하지 않는다" 를 브라우저가 지키게 한다. `/privacy/` 는 기존 규칙대로 404, 쿼리가 붙어도 같은 페이지다. 정규식 위치는 두지 않는다(028).
- 페이지는 외부 자원을 하나도 부르지 않는다: 글꼴은 시스템 글꼴(Pretendard 도 부르지 않는다), 그림 없음(favicon 만), 스크립트·스타일은 파일 안. Clarity 도 동의 안내 띠도 넣지 않는다(033 도 이 페이지는 뺀다) — 방침을 읽고 고르는 동안 분석이 돌지 않게.
- 서버 기록: caddy 가 다른 페이지처럼 기록한다 — 027 §3.2 제외 목록에 넣지 않는다(방문 한 줄이고, 방침을 읽는 규모를 알 수 있다). 동의·철회 조작은 브라우저 안에서만 일어나 요청을 만들지 않으므로 서버에 남지 않는다. nginx 접속 로그는 이미 꺼져 있다(027).
- 모양: 랜딩(022 §3.4)의 색 토큰 값을 복사한 어두운 화면, 본문 17px·줄간 1.7·한 줄 34em 이하, `lang="ko"`, `<title>` "개인정보 처리방침 — KimpTrack", canonical `https://kimptrack.com/privacy`. 위에서 아래로: 상단 바(워드마크 → `/`, "서비스로 넘어가기" → `/app/`), 제목·시행일, 한눈에 보기 네 줄(가입 없음 / 서버 접속 기록 90일 / 화면 분석 Clarity·동의한 경우만·기본 꺼짐 / 연락처), 화면 분석 동의 관리(§3.3 — 제목 "화면 분석 동의 관리", 앵커 `#consent`), 본문 절(§3.4 순서), 변경 이력. 390px 에서 가로 스크롤이 없다 — 표는 좁은 폭에서 행마다 세로로 쌓는다.
- dev 서버에서는 `http://localhost:5173/app/privacy.html`(public 이 base 아래로 서빙된다 — 022 와 같다). oxlint 대상이 아니다.

### 3.2 링크
- 랜딩 바닥(022 §3.3-8): 바로 가기 nav 의 마지막 칸에 "개인정보 처리방침"(→ `/privacy`, 같은 탭). 방침 링크는 바닥에서 가장 밝고 굵은 글자다 — 작성지침이 다른 고지와 구분해 보이게 하라고 권한다.
- 대시보드 헤더(002, 코드만): 오른쪽 시계 왼쪽에 "개인정보 처리방침"(12px·`neutral-500`, 탭 버튼과 같은 hover) → `/privacy`, 새 탭(`target="_blank"`·`rel="noopener"`) — 열어 둔 대시보드의 WebSocket 을 끊지 않게. 푸터가 아닌 이유: 입출금 레이더 탭은 셸 푸터를 그리지 않는다.
- `sitemap.xml` 에 `https://kimptrack.com/privacy` 한 줄 — `lastmod` 는 시행일(방침을 고쳐 시행일이 바뀔 때만 올린다).

### 3.3 분석 동의 — 저장값 하나 (033 이 읽고 쓰는 계약)
- **사람 결정(2026-10-01): Clarity 는 동의(opt-in) 방식이다.** Microsoft 가 Clarity 데이터를 자기 목적(Microsoft Advertising 등)에도 써서 위탁이 아니라 제3자 제공이고, 제공의 국외 이전에는 제28조의8 제1항 제3호(처리위탁·보관용)를 쓸 수 없다 — 별도 동의(제1호)로 간다.
- 브라우저 `localStorage` 키 `kt.analytics`(출처마다). `granted` = 동의, `denied` = 거부·철회, 없음 = 아직 정하지 않음(**기본 꺼짐**). 그 밖의 값과 저장소를 읽다 난 예외도 끈다. 없음과 그 밖의 값은 둘 다 '정하지 않음' 이고, GPC·저장소 예외가 아니면 033 의 동의 안내 띠가 묻는다.

| 값 | Clarity |
|---|---|
| `granted` | 켬 |
| `denied` | 끔 |
| 없음 | 끔 |
| 그밖 | 끔 |
| 읽기예외 | 끔 |

- GPC: `navigator.globalPrivacyControl === true` 는 어떤 값보다 앞서 끈다 — `granted` 여도 끄고, 동의 안내 띠도 띄우지 않는다. DNT 는 보지 않는다 — Clarity 자신도 DNT 에 답하지 않는다(Microsoft Clarity FAQ, 2026-09-21 갱신판).
- 쓰는 곳은 둘이다: 이 페이지의 동의 관리와 033 의 동의 안내 띠(랜딩·대시보드). 둘 다 `granted`·`denied` 만 쓴다. 띠는 값이 `granted`·`denied` 가 아닐 때(없음·그 밖)만 뜨므로, 한 번 고른 방문자에게는 다시 묻지 않는다 — 바꾸는 곳은 이 페이지다(033 이 랜딩 바닥·대시보드 헤더에 '화면 분석 설정' → `/privacy#consent` 를 둔다).
- 쿠키가 아닌 이유: 서버로 가지 않고 만료가 없다. 저장은 출처(origin)마다지만 `www.kimptrack.com` 은 모든 요청을 경로·쿼리째 apex 로 301 한다(022·023) — 방침 페이지와 선택은 `kimptrack.com` 한 출처에만 있다. 그래서 페이지는 "`kimptrack.com` 에서 고른 선택이 이 브라우저에 저장되고, 브라우저 데이터를 지우면 다시 묻는다" 고 적는다. 한 줄 더: "일부 브라우저(Safari 등)는 이 사이트를 7일 동안 쓰지 않으면 선택을 지웁니다 — 그러면 분석은 꺼진 채로 있고 다음 방문에 다시 묻습니다". Safari(ITP)는 스크립트가 쓴 저장값을 사이트와 7일 동안 상호작용이 없으면 지운다 — 동의 방식에서는 꺼지는 쪽으로 풀린다.
- 동의 전에 알릴 사항(법 제15조 제2항·제17조 제2항·제28조의8 제2항 — 033 의 띠와 같은 사실): 받는 자 Microsoft Corporation(미국)과 연락처(개인정보 문의 링크), 항목(§3.4-2), 국가·일시·방법(미국 — 동의한 뒤 랜딩·대시보드를 보는 동안 수시로, 네트워크로 전송), 받는 자의 목적(KimpTrack 의 화면 이용 분석, Microsoft Advertising 제공 등 Microsoft 자신의 목적)·보유 기간(녹화 30일, 히트맵 등 최대 9개월), 거부 방법과 거부해도 서비스 이용에 불이익이 없다는 것. 동의 관리 상자는 버튼 앞에 이 사실을 여섯 칸(받는 자·항목·이전 국가·일시·방법·목적·보유 기간·거부)으로 적고 4절·6절로 잇는다. 033 의 띠가 이 칸을 그대로 옮기므로 칸은 다른 절을 가리키지 않고 그 자리에서 다 읽힌다 — 항목은 §3.4-2 의 Clarity 항목을 '등' 없이 모두, 이전 국가·일시·방법은 '미국 — …' 으로 시작, 거부 칸은 철회하는 곳(처리방침의 '화면 분석 동의 관리'(`/privacy#consent`)의 [동의 철회] — 법 제28조의8 제2항 제5호의 거부 방법·절차, 제38조 제4항)을 적는다. 다른 동의와 묶지 않는다.
- 반영 시점(033 과 같은 문장 — 페이지가 그대로 적는다): "동의·철회는 그 뒤로 여는 페이지에 곧바로 따릅니다. 같은 브라우저에 이미 열린 `kimptrack.com` 랜딩·대시보드 탭에도 곧바로 반영됩니다 — 철회하면 그 탭이 한 번 새로고침되어 분석을 멈추고, 동의하면 그 탭에서도 분석을 시작합니다."
- "화면 분석 동의 관리" 절: 지금 상태 한 줄 — 동의함(`granted`) / 거부함(`denied`) / 정하지 않음(없음·그 밖의 값) / GPC로 거부 / 저장할 수 없음(읽기 예외) — 과 버튼 둘. 두 버튼 모두 누른 뒤 저장값을 다시 읽어 그린다 — 쓰기가 실패해도 화면은 실제 저장값(033 이 읽을 값)이다.
  - [동의]: `granted` 를 쓴다.
  - [동의 철회]: `denied` 를 쓰고, 이 사이트의 Clarity 1차 쿠키 `_clck`·`_clsk`(Microsoft Clarity 쿠키 목록)를 경로 `/` 로 만료시킨다 — 도메인 속성 없이 한 번, 호스트에서 앞의 `www.` 를 뗀 도메인으로 한 번(어느 쪽으로 심겼든 지워지게). 쓰기에 실패해도 쿠키는 지운다. 쓰기가 예외면(저장 한도 등) 값을 지운다(`removeItem` — 없음도 꺼짐). 그래도 `granted` 가 읽히면(지우기도 예외) 상태는 '동의함' 그대로 두고 "철회를 저장하지 못했습니다 — 이 사이트의 브라우저 데이터를 지우거나 GPC를 켜면 분석을 막을 수 있습니다" 를 보인다. Microsoft 도메인의 제3자 쿠키(`MUID` 등)는 이 페이지가 지울 수 없어 DAA 거부 페이지 `https://optout.aboutads.info/`(Clarity FAQ 가 안내한다)를 함께 안내한다.
  - 두 버튼은 같은 모양·높이·너비(글자 길이가 아닌 공통 최소 너비)로 늘 둘 다 보인다 — 철회는 동의만큼 쉽다(같은 값을 다시 눌러도 같다). GPC 면 버튼 대신 "브라우저가 GPC를 보내는 동안은 분석하지 않습니다".
  - 읽기 예외(사생활 모드·저장소 차단): 버튼을 숨기고 "이 브라우저에는 선택을 저장할 수 없어 화면 분석을 하지 않습니다". 자바스크립트가 꺼지면 `<noscript>` 로 "버튼을 쓸 수 없습니다 — 자바스크립트가 꺼진 브라우저에서는 화면 분석도 돌지 않습니다".
- 033 은 GPC 이거나 값이 `granted` 가 아니면 남은 `_clck`·`_clsk`·`_cltk` 를 지운다(033).

### 3.4 페이지에 적는 사실
절은 개인정보 보호법 제30조 제1항·시행령 제31조 제1항 항목을 따른다(국가법령정보센터 현행, 시행 2026-09-11 판 — 2026-10-01 확인. 작성지침은 2026-04-24 개정판). 문장은 실행 세션이 쓰되 아래 사실만 쓴다 — 하지 않는 조치(저장 암호화·정기 점검 등)를 약속하지 않는다. 말투는 랜딩과 같은 존댓말.

| 기록 | 보관 |
|---|---|
| 서버접속 | 90일 |
| 관리자접속 | 90일 |
| 화면녹화 | 30일 |
| 문의메일 | 1년 |

문의 메일의 1년은 답변을 마친 날부터 센다. Clarity 의 히트맵·즐겨찾기·표본 녹화·라벨은 최대 9개월이다(2 참고). 보관하는 곳은 1~4 에 적는다.

1. **서버 접속 기록**(027 §3.2 복사) — `kimptrack.com` 으로 온 요청마다 caddy 가 JSON 한 줄(`www` 는 apex 로 301 만 하고 기록하지 않는다 — 022·023): 시각, IP 앞부분(IPv4 는 마지막 마디, IPv6 는 뒤 80비트를 0 으로 — /24·/48), 접속 포트, 프로토콜·메서드·호스트, 경로와 쿼리(스프레드·갭·선선갭 검색어 `s.q`·`g.q`·`p.q` 는 지움 — 기록 탭의 코인 칸 `sym`·`utm_*` 는 남는다), 상태 코드·크기·처리 시간(대시보드 WebSocket 은 연결을 연 시간), 이전 페이지의 사이트 주소(도메인까지), 브라우저·기기 정보(`User-Agent`). 헤더·쿠키는 남기지 않고, 폴링 경로 다섯과 감시(canary) 요청은 기록하지 않는다. 목적: 장애·오류 확인, 어떤 경로로 들어와 얼마나 머무는지 규모 파악. 위치: serve 서버(AWS 서울) 파일, 게시 뒤 AWS CloudWatch Logs 서울(027 런북 15단계). 보관 90일(§3.5). 관리자 화면의 24시간 요약을 위해 같은 서버의 api 프로세스가 이 파일을 읽는다(035 — 새로 저장하지 않는다). 컨테이너 로그(docker)에는 방문자 IP 가 없다 — caddy 오류 줄은 IP 를 지우고 검색어를 뺀 경로만 남기며(§3.5, 크기 상한까지 docker 로그에 남는다), nginx·앱 로그의 접속 주소는 앞단 컨테이너다.
2. **화면 이용 기록 — Microsoft Clarity**(033 이 켠다, 랜딩·대시보드에서 동의한 방문자만 — 기본 꺼짐, §3.3). 받는 것: 페이지 주소(검색어는 033 이 뺀다), 이전 페이지 주소(`document.referrer` 그대로 — Clarity 는 참조·클릭 주소를 가리지 않는다, 033 §3.7)·누른 링크 주소, 클릭·스크롤·마우스 움직임·화면 크기, 화면 내용(입력칸은 늘 가림), 033 의 태그 `tab`·`sym` 값과 이벤트 `tab_<id>`·`pivot_history`(033 §3.8), 기기·브라우저·OS, IP 주소(Microsoft 가 받아 대략 위치를 추정 — Clarity FAQ), 1차 쿠키 `_clck`(같은 브라우저를 알아보는 무작위 ID, 1년)·`_clsk`(한 방문의 페이지를 잇는다, 1일 — 기간은 033 의 2026-09-28 실측), sessionStorage `_cltk`(탭 ID — clarity-js `TabKey`). 받는 곳 Microsoft Corporation, 미국(Microsoft Azure — 국가는 게시 직전 약관으로 다시 본다, §3.6). 목적: 어떤 화면·기능을 쓰고 어디서 막히는지. 보관: 녹화 30일, 히트맵·즐겨찾기·무작위 표본 녹화·라벨 최대 9개월(Clarity FAQ 2026-09-21). 한 사람분만 지울 수는 없고 프로젝트를 통째로 지워야 한다(같은 FAQ). Clarity 약관이 방침에 요구하는 것을 싣는다: Microsoft 가 방문자 개인정보를 수집한다는 사실, Microsoft 가 그 정보를 Microsoft Advertising 제공 등 자기 목적에도 쓴다는 사실(Microsoft 처리방침에 따름), Microsoft 처리방침 링크 `https://privacy.microsoft.com/ko-kr/privacystatement`, 동의·철회 방법(§3.3). 우리 쪽은 동의한 방문자에게도 광고용 저장(쿠키 등)에는 거부 신호를 보낸다(033 — `ad_Storage: denied`) — 이 신호는 Microsoft 가 받은 정보를 자기 목적에 쓰는 것까지 막지 않으며, 페이지는 이를 함께 적는다. Microsoft 의 자기 목적 이용은 어느 절에서나 '씁니다' 로 단정한다. 클릭·스크롤 같은 화면 이용 기록은 행태정보다 — 제3자(Microsoft)가 광고 목적에도 쓰므로 7절에 작성지침의 행태정보 항목(수집 항목·수집 방법·목적·보유 기간·수집하는 사업자·거부 방법)을 소항목으로 싣는다.
3. **관리자 접속 기록**(029 §3.2 복사) — 운영자만 해당. `admin.kimptrack.com` 요청마다 시각·로그인 이메일·IP·메서드·경로와 쿼리·상태·처리 시간·Cloudflare 요청 ID·`Sec-Fetch-Site`. serve 서버 파일, 90일(§3.5). 로그인은 Cloudflare Access(허용한 이메일로 받는 일회용 코드)가 하고 Cloudflare, Inc.(미국)가 이메일·IP 를 처리한다(무료 요금제 인증 기록 24시간 — 030).
4. **문의 메일** — 권리 행사·문의로 받은 이메일 주소와 내용. 목적 답변, 보관 답변을 마친 날부터 1년, 메일 서비스 Gmail(Google LLC, 미국 — 위탁·국외 이전 절에 한 줄).
5. **외부 글꼴 요청** — 방문자의 브라우저가 직접 보낸다. 대시보드는 Google Fonts(Google LLC, 미국 — IP·`User-Agent`·요청 주소·이전 페이지 도메인이 가고 쿠키는 없으며 Google 이 요청 기록을 남긴다 — Google Fonts FAQ). 랜딩은 같은 출처의 자체 서브셋 글꼴이라 외부 요청이 없다(022). 관리자 화면의 API 문서(FastAPI 기본 Swagger·ReDoc)는 jsDelivr(Volentio JSD Limited, 영국 — Cloudflare·Fastly 등 CDN 이 전달)와 파비콘 `fastapi.tiangolo.com` 을 부르고, ReDoc 은 Google Fonts(Montserrat·Roboto)도 부른다(운영자만 — `fastapi/openapi/docs.py` 기본값, 2026-10-01 확인). 없애는 일(`with_google_fonts=False`·파비콘 자체 호스팅)은 029 에서 따로 정한다. 방침 페이지는 부르지 않는다.
6. **하지 않는 것** — 회원가입·로그인·이름·전화번호·결제 없음, 나이를 묻지 않음, 맞춤형 광고·판매 없음. 운영 알림(Slack·AWS 경보·uptime 감시)에는 방문자 정보가 가지 않는다: 025 알림 문구는 역할·거래소·실패 종류·상태 코드·거래소 오류 문구·로거 이름과 문장·예외 종류와 문장·요청 메서드와 경로(쿼리 없음)뿐이다(025 §3.3·`server/app/main.py` 처리 안 된 예외 로그 — 2026-10-01 확인). 거래소 원문(S3)·시세 저장(Influx·Redis)에도 방문자 정보가 없다.

절 순서와 내용(해당 없는 민감정보·가명정보·자동화된 결정·아동·국내대리인 절은 두지 않는다. 테스트가 제목 열둘을 본다):
1. 처리 목적. 2. 처리 항목과 보유 기간 — 위 표와 1~4, 처리 근거(Clarity 는 동의 — 제15조 제1항 제1호, 나머지는 §3.6 정한 값). 3. 파기 — 날수 기한을 약속하지 않고 설정이 실제로 지우는 방식을 적는다(§3.5): 서버 접속 기록은 보관 기간이 지난 파일을 다음 회전 때 지운다(방문이 없으면 늦어질 수 있다), 관리자 접속 기록은 매일 회전하며 90개를 넘는 파일을 지운다, CloudWatch 는 AWS 가, 문의 메일은 운영자가 휴지통까지. Clarity 는 Microsoft 가 지운다(시점은 Microsoft 기준). 4. 제3자 제공 — Microsoft, 동의(제17조 제1항 제1호)로 — 같은 조 제2항의 다섯 가지(받는 자·받는 자의 목적·항목·보유 기간·동의를 거부할 권리와 거부해도 불이익이 없다는 것). 5. 처리 위탁 — Amazon Web Services, Inc.(서버·저장·기록, 서울), Cloudflare(관리자 로그인·터널), Google LLC(Gmail — 문의 메일). 6. 국외 이전 — Microsoft·Google(대시보드 글꼴·Gmail)·Cloudflare(운영자만)·API 문서의 jsDelivr·파비콘 서버·Google Fonts(운영자만 — 5)마다 시행령 제31조 제1항 제2호대로 근거와 법 제28조의8 제2항 다섯 가지(항목 / 국가·시기·방법 / 받는 자 이름·연락처 / 목적·보유 기간 / 거부 방법·절차·효과 — 글꼴은 "외부 글꼴을 막으면 기본 글꼴로 보입니다"). Microsoft 의 근거는 국외 이전 별도 동의(제28조의8 제1항 제1호)이고 거부 효과는 "동의하지 않으면 이전되지 않고, 거부·철회해도 서비스 이용에 불이익이 없다". Gmail·Cloudflare 는 처리위탁·보관 공개(같은 항 제3호 가목) 그대로. 7. 자동 수집 장치와 거부 — 쿠키 두 개(동의한 경우에만 생긴다)·sessionStorage `_cltk`·저장값 `kt.analytics`, 거부는 동의하지 않기·§3.3 철회·GPC·브라우저 쿠키 차단·광고 차단기·Microsoft 거부 페이지. 소항목 "행태정보의 수집·이용·제공과 거부": 항목(클릭·스크롤·마우스 움직임·화면 크기·화면 내용·누른 링크·보고 있는 탭) / 수집 방법(동의한 방문자의 브라우저에서 Clarity 스크립트가 모아 Microsoft 로 전송) / 목적(KimpTrack 의 화면 이용 분석 — Microsoft 는 Microsoft Advertising 제공 등 광고를 포함한 자기 목적에도 쓴다) / 보유 기간(2 와 같음) / 수집하는 사업자(Microsoft Corporation) / 거부 방법(위 거부 방법 — KimpTrack 자신은 맞춤형 광고를 하지 않는다). 8. 정보주체의 권리 — 연락 메일로 열람·정정·삭제·처리정지를 요구할 수 있고(화면 분석 동의 철회는 §3.3 버튼으로 바로), 서버 기록은 IP 끝자리를 지워 한 사람을 가려내기 어렵고 Clarity 는 한 사람분만 지울 수 없다는 한계를 그대로 적는다. 9. 안전성 확보 조치 — HTTPS, 기록 최소화(IP 앞부분·검색어·헤더 지움), 관리자 화면 로그인(허용한 이메일의 일회용 코드)과 인바운드 포트 없는 터널, 서버 접근 제한(SSH 키·보안그룹), Clarity 입력칸 가림. 10. 개인정보 보호책임자 — 성명·연락 이메일(전화 없음 — 줄째 뺀다). 11. 권익침해 구제 — 개인정보분쟁조정위원회 1833-6972·개인정보침해신고센터 118·대검찰청 1301·경찰청 182(법제처 처리방침 표기와 같다). 12. 처리방침 변경 — 시행일, 변경 이력.

### 3.5 보관 기간을 지키는 설정
- 파기 기한: 날수로 약속하지 않는다 — 방침 3절은 아래 설정이 실제로 지우는 방식(언제 무엇이 지우는가)만 적는다. caddy 는 기록할 요청이 없으면 회전도 삭제도 하지 않아 고정 기한을 지킨다고 쓸 수 없다(§3.7).
- caddy 접속 로그(027 조각 `access_log` 의 파일 출력): `roll_interval 24h` 를 더하고 `roll_keep` 5 → 100. `roll_size 50MiB`·`roll_keep_for 90d`·`mode 0644` 는 그대로. 하루(또는 50MiB)마다 새 파일이 되고, 보관 기간(90일)이 지난 회전 파일은 다음 회전 때 지운다 — 방문이 없으면 회전이 없어 늦어질 수 있다. 100개는 하루 1개 × 90일에 크기 회전 여유다. `roll_interval` 은 caddy `log` 문서(2026-10-01)에 있다(Caddy 2.11 부터) — 쓰는 이미지의 `caddy validate` 가 거부하면 멈추고 묻는다. 이 회전 설정은 배포의 `caddy reload` 로 들어가지 않는다(파일 writer 를 파일 이름으로 재사용 — 2.11.4 로컬 재현, 지우기 규칙은 reload 로 바뀐다) — 머지 뒤 사람이 serve 에서 caddy 를 한 번 다시 만든다(§4, 몇 초 끊김).
- caddy 기본 로거(`log default` — 오류 줄, docker 로그 50MB×3 으로 크기로만 회전): `request>remote_ip`·`request>client_ip` 를 `ip_mask` 에서 `delete` 로. 오류 줄에는 IP 가 필요 없고, 방문이 적으면 이 줄이 90일보다 오래 남는다. 나머지 지우기 규칙(헤더·검색어)은 접속 로그와 같게 둔다.
- CloudWatch Logs `/marketlens/serve/caddy`: 보존 90일(027 그대로) — 만료된 줄은 보통 72시간 안에 지워진다(AWS CloudWatch Logs 문서).
- 관리자 접속 기록(logrotate): `weekly`·`rotate 13`·`notifempty` → `daily`·`rotate 90`·`ifempty`, `copytruncate`·`missingok` 그대로. 빈 날도 회전해야 90개가 곧 90일이다 — 매일 회전하며 90개를 넘는 파일을 지운다.
- 문의 메일(Gmail, 수동): 답변을 마친 날 1년 뒤 날짜로 운영자 달력 알림을 하나 만들고, 그날 그 메일(주고받은 것 모두)을 지운 뒤 휴지통을 비운다 — Gmail 휴지통은 30일 남는다.
- 서비스를 닫거나 호스팅을 옮길 때(AWS 계정은 2026년 12월에 끝난다): 옮기기 전에 이 방침을 고쳐 새 위탁·보관 위치를 적고, 옛 서버 파일·CloudWatch 로그 그룹·Clarity 프로젝트는 사람이 지운다.

### 3.6 게시 조건 — 사람이 채우는 값
- 페이지의 자리표시자 `〔…〕` 열: 〔운영 주체〕(처리자 표기), 〔책임자〕(개인정보 보호책임자 성명 또는 부서), 〔연락 이메일〕, 〔전화〕(없으면 줄째 뺀다), 〔메일 서비스〕, 〔문의보관〕(기본 "답변을 마친 날부터 1년"), 〔AWS 수탁자〕(계약 당사자 명칭 — 조직 임대 계정), 〔시행일〕, 그리고 법률 확인 결과로 정하는 〔처리 근거〕·〔Clarity 구분〕. 실행 세션은 시작할 때 이 값을 사람에게 받고, 없으면 멈추고 묻는다. `〔` 가 한 글자라도 남으면 계약 테스트가 실패해 머지(= 배포 = 게시)되지 않는다.
- 정한 값(사람, 2026-10-01 — 개인 연락처는 페이지에만 싣고 이 문서·다른 파일에는 적지 않는다): 운영 주체 = 개인 두 명(이름은 페이지), 보호책임자 = 그중 한 명, 연락 이메일 = 사람이 준 주소, 전화 없음(줄째 뺀다), 메일 서비스 Gmail(Google LLC, 미국 — 위탁·국외 이전 절), 문의 보관 기본값, AWS 수탁자 Amazon Web Services, Inc.(서울 리전 호스팅 — 조직 임대 계정의 실제 계약 당사자는 사람이 확인), 시행일 2026년 10월 1일. 법률 판단 두 자리: Clarity 구분 = **사람 결정(2026-10-01) 동의 방식** — Microsoft 가 자기 목적에도 쓰므로 제3자 제공, 수집·제공은 정보주체의 동의(제15조 제1항 제1호·제17조 제1항 제1호), 국외 이전은 별도 동의(제28조의8 제1항 제1호, §3.3). 처리 근거(나머지) = 사람 확인 전까지 보수적인 기본값(설계 세션 위임): 서버·관리자 접속 기록은 서비스 제공·보안·이용 통계의 정당한 이익(개인정보 보호법 제15조 제1항 제6호), 권리 행사 답변은 법령상 의무(같은 항 제2호), 그 밖의 문의는 제6호. 조항은 국가법령정보센터 현행(법 시행 2026-09-11·시행령 시행 2026-09-11)으로 확인했다. 확인할 것은 §7 '사람 확인(법률)'.
- 연락 이메일은 페이지와 공개 레포에 그대로 실린다 — 개인 주소가 아니라 역할 주소를 권한다.
- 법률 확인(사람, 게시 전): (1) Clarity 구분은 사람이 정했다(동의 방식, 2026-10-01). 남은 것: 띠·방침의 버튼 한 번으로 수집·제공·국외 이전 동의를 함께 받는 것이 동의 사항을 구분해 각각 받으라는 규정(법 제22조 제1항)에 맞는지, 만 14세 미만의 동의(제22조의2 — 나이를 묻지 않는다)를 어떻게 다룰지, 동의 기록이 방문자 브라우저에만 있는 것(입증). (2) 동의 없이 처리하는 근거 조항(서버·관리자 접속 기록·문의 메일). (3) 방문자 브라우저가 직접 부르는 외부 자원(대시보드 Google Fonts, 운영자만 보는 API 문서의 jsDelivr·파비콘·Google Fonts)을 국외 이전으로 적는 방식. (4) 통신비밀보호법의 접속 기록 3개월 보관 의무 해당 여부 — 해당하면 원 IP 보관을 별도 스펙으로 정하고 "법령에 따른 보관" 을 방침에 더한다(027 빚). (5) 이메일만으로 연락처 요건이 되는지. Clarity 약관의 방침 문구 요구(2026-09-28 조사 — 원문 4.4(b))와 받는 자·저장 국가는 게시 직전 원문을 다시 본다(약관 페이지가 스크립트로 그려져 2026-10-01 에는 검색 요약으로만 확인했다). jsDelivr 운영사(Volentio JSD Limited, 잉글랜드·웨일스 등록)는 원문 — 방침 페이지가 불러오는 GitHub `jsdelivr/jsdelivr` 의 `Privacy Policy.md`(2023-11-23 판) — 으로 2026-10-01 확인했다.
- 게시 뒤 풀리는 것: 027 런북 15단계(CloudWatch Logs 전송·지표 필터·5xx 경보 — 사람)와 033 시작. 033 배포 전에는 방침의 Clarity 절이 실제보다 앞서 있다 — 033 이 끝내 켜지지 않으면 그 절을 지운다.
- 고칠 때: 기록 항목·목적·보관·받는 곳(외부 스크립트·글꼴 포함)을 바꾸는 PR(033·035·호스팅 이전 등)은 같은 PR 에서 이 페이지를 고친다. 이전 판은 `web/public/privacy-<그 판의 시행일 YYYYMMDD>.html` 로 남겨 변경 이력에서 잇는다 — `location /` 로 CSP 없이 나가므로 스크립트를 뺀 정적 사본으로 두고(동의 관리 절은 "지금 판에서 고른다" 링크 한 줄), 계약 테스트가 외부 자원 0 을 함께 본다. 항목·목적이 바뀌는 변경은 바뀌는 절의 전후 대조를 변경 이력에 싣고 시행일 전에 게시한다(작성지침 2026-04-24 — 중대한 변경은 대조표 등으로 따로 알린다).

### 3.7 엣지
- 자바스크립트 꺼짐: 본문은 다 읽히고 동의 관리 절만 `<noscript>` 안내 — Clarity 도 돌지 않는다.
- Safari 는 스크립트가 쓴 저장값을 사이트와 7일 동안 상호작용이 없으면 지운다 — 동의가 풀려 분석이 꺼지고 다음 방문에 띠가 다시 묻는다(꺼지는 쪽). 페이지가 이 사실을 적는다(§3.3).
- 동의한 뒤 GPC 를 켜면 값이 `granted` 여도 분석하지 않고, 방침의 상태 줄은 "GPC로 거부" 다(§3.3 — GPC 가 앞선다).
- `www.kimptrack.com/privacy` 는 apex 로 301(022·023) — www 출처에는 방침 페이지도 선택도 없다.
- 방침 페이지가 안 뜸(배포 실패): 랜딩·대시보드 링크가 404 — canary 는 이 경로를 보지 않는다. 배포 뒤 사람이 확인(§4).
- caddy 에 기록할 요청이 하루 없으면 그날은 회전도 삭제도 없다(caddy 는 다음 기록 때 회전한다) — 다음 방문 때 지운다.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 §3.6 값을 사람에게 받는다(없으면 멈춘다).
- `server/tests/test_privacy.py`(test_deploy 의 nginx 파서 재사용): 공개 server 에 `= /privacy`(`try_files /privacy.html`, `Cache-Control no-cache` 와 §3.1 CSP 둘 다 `always`)·`= /privacy.html`·`= /app/privacy.html`(둘 다 301 `/privacy`), 정규식 위치 없음·028 허용 목록 그대로 / `privacy.html`: `〔` 없음, `lang="ko"`, §3.4 절 제목 열둘, canonical `https://kimptrack.com/privacy` 있음 / 외부 자원 없음 — `<script src>`·`<img src>` 와 자원을 부르는 `<link rel>`(`stylesheet`·`preload`·`modulepreload`·`preconnect`·`dns-prefetch`·`icon`)의 주소가 `http(s)://`·`//` 로 시작하지 않는다(`rel="canonical"` 과 `<a href>` 는 허용) / 인라인 `<script>` 본문에만 `fetch(`·`new WebSocket(`·`XMLHttpRequest`·`sendBeacon`·`clarity.ms` 가 없다 — 본문 글자는 보지 않는다(요청 자체는 CSP 가 막는다) / `kt.analytics`·`denied`·`granted`·`globalPrivacyControl`·`_clck`·`_clsk`·`_cltk`·`Safari`·`7일`·Microsoft 처리방침·DAA 링크·구제 기관 번호 넷 있음 / 동의 방식(§3.3): 버튼 [동의]·[동의 철회], 기본 꺼짐 문장, 상태 다섯 글자가 스크립트에, 동의 상자의 알릴 사항 여섯 칸(받는 자·항목·이전 국가·일시·방법·목적·보유 기간·거부)이 버튼 앞에 — 칸 안에 '절' 없음, 항목에 '등' 없이 Clarity 항목 모두, 이전 칸은 '미국 — ' 으로 시작, 거부 칸에 `/privacy#consent` 와 [동의 철회] —, `<section id="consent"`(033 띠의 링크 앵커)와 다른 탭 반영 문장(§3.3), 두 버튼의 공통 최소 너비, 근거 조항(2절 제15조 제1항 제1호·4절 제17조 제1항 제1호·6절 Microsoft 제28조의8 제1항 제1호 — 제3호 아님)·불이익 없음, 7절 행태정보 소항목의 여섯 칸, '유럽 시간대'·'허용으로 봅니다'·'5일 안에'·'90일 뒤 지웁니다'·'쓸 수 있어' 없음 / 동의 관리 스크립트를 node 로 돌린다(가짜 document·localStorage·navigator, 라이브러리 없음 — node 가 없으면 건너뛰고 `CI` 환경 변수가 있으면 실패): 값 없음·`granted`·`denied`·그 밖·GPC(`granted` 위)·읽기 예외의 상태 글자와 보이는 요소, [동의]·거부 뒤 [동의]·[동의 철회] 뒤 저장값·쿠키(`_clck`·`_clsk` 만 두 도메인 모양으로 만료), 쓰기 예외의 [동의](실제 값 그대로)·[동의 철회](값을 지움 → '정하지 않음')·지우기도 예외(`granted`·'동의함'·철회 실패 안내), 다른 탭의 storage 이벤트 / `web/public/privacy-*.html` 이 있으면 각각 `〔`·`<script` 없음과 같은 외부 자원 단언 / `landing.html` 바닥에 `/privacy` / `web/src/App.tsx` 에 `/privacy` 와 `noopener` / `sitemap.xml` 에 `/privacy`(`lastmod` = 페이지 시행일). `test_landing_seo.py` 의 sitemap 단언은 `/` 와 `/privacy` 두 줄로, `/` 의 `lastmod` 는 랜딩 고친 날 그대로.
- `server/tests/test_observability.py`: 조각 `access_log` 에 `roll_interval 24h`·`roll_keep 100`. 기본 로거는 `remote_ip`·`client_ip` 가 `delete` 이고, "두 format 같음" 단언은 이 두 줄만 예외로 두고 나머지는 그대로.
- 브라우저(로컬 web 이미지 또는 dev, 1440·390px): 가로 스크롤 없음 / 네트워크 목록에 문서·favicon 외 요청 0 / 콘솔 CSP 위반 0 / 값 없음 → "정하지 않음" / [동의] 뒤 `granted`·"동의함" / 미리 심은 가짜 `_clck`·`_clsk` 가 [동의 철회] 뒤 사라지고 `denied`·"거부함" / 다른 값 → "정하지 않음" / `navigator.globalPrivacyControl` 을 true 로 흉내 내면 "GPC로 거부"·버튼 대신 안내 / `localStorage` 읽기가 예외를 던지게 흉내 내면 "저장할 수 없음" / `granted` 에서 쓰기만 예외면 [동의 철회] 뒤 값이 지워져 "정하지 않음", 쓰기·지우기 둘 다 예외면 "동의함" 그대로에 철회 실패 안내 / 390px 에서 두 버튼 같은 너비 / 자바스크립트 끔 → 본문과 noscript / 랜딩 바닥 링크(같은 탭)·대시보드 헤더 링크(새 탭).
- 로컬 Docker(web 이미지): `nginx -t` / `curl -sI localhost:<포트>/privacy` 200·`Cache-Control: no-cache`·CSP, gzip 요청에 `Content-Encoding: gzip` / `/privacy.html`·`/app/privacy.html` 301 `/privacy` / `/privacy/` 404 / `caddy validate`(바뀐 조각·기본 로거)와 그 이미지의 `caddy version`(2.11 이상). nginx·Caddyfile 을 고쳤으므로 PR 본문 테스트 칸에 `nginx -t`·`caddy validate` 결과를 적는다(027·029 빚).
- 기존 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`.

**배포 전후 — 사람(완료 조건 아님)**: 머지 전에 serve `docker exec marketlens-caddy caddy version` 이 2.11 이상(`caddy:2-alpine` 은 고정 안 한 태그 — 아니면 배포의 reload 가 `roll_interval` 을 거부한다) / `https://kimptrack.com/privacy` 200·`https://www.kimptrack.com/privacy` 301 apex / 머지 뒤 serve 에서 caddy 를 한 번 다시 만든다 — `docker compose --profile serve --env-file .env --env-file server/.env up -d --force-recreate caddy`(몇 초 끊김, 인증서는 볼륨에 있다 — 회전 설정은 `caddy reload` 로 안 들어간다, §3.5. serve caddy 는 v2.11.4 — 2026-10-01 확인) / 이틀 뒤 serve `logs/caddy/` 에 하루 회전 파일 — 본 뒤에 027 런북 15단계 / 런북 12단계 logrotate 다시 적용 뒤 `sudo logrotate -d /etc/logrotate.d/marketlens-admin` / 033 을 시작해도 된다고 알림.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 자동 (2026-10-01)
cd server && ruff check . && ruff format --check . && pytest -q
#   All checks passed! / 263 files already formatted / 1077 passed (test_privacy 10 새로, test_observability·test_landing_seo 단언 고침)
cd web && npm run lint && npm run build
#   oxlint 종료 0 / tsc -b && vite build ✓ — dist 에 privacy.html
uv run web/scripts/subset-landing-font.py
#   landing/fonts/kimptrack-sans-f3ac1cc6.woff2 57,948B · 글자 309개(바닥 링크의 '처'·'침' 더함) · 원본에 없는 글자 없음
# 계약 테스트가 잡는지: privacy.html 에 〔·외부 icon·인라인 fetch(·절 제목·구제 번호를 하나씩 망가뜨리면 test_privacy 가 하나씩 실패
# 로컬 Docker — 이 브랜치 web/Dockerfile 이미지, 업스트림 이름만 푸는 대역 컨테이너, 127.0.0.1:18932 (끝난 뒤 지움)
docker exec web-032 nginx -t                        # syntax is ok · test is successful
curl -sI 127.0.0.1:18932/privacy                    # 200 · Cache-Control: no-cache · CSP §3.1 그대로 (If-None-Match 304 에도 둘 다)
curl -sI -H 'Accept-Encoding: gzip' …/privacy       # 200 · Content-Encoding: gzip — 풀면 레포 파일과 같다
curl …/privacy.html · …/app/privacy.html            # 301 Location: /privacy
curl …/privacy/ · …/privacy?utm_source=x            # 404(한국어 404.html) · 200 같은 페이지
docker run --rm -v ./caddy:/etc/caddy:ro caddy:2-alpine caddy version    # v2.11.4
… caddy validate --config /etc/caddy/Caddyfile     # Valid configuration
… caddy adapt                                       # 접속 로그 roll_interval 24h·roll_keep 100·roll_keep_days 90·roll_size_mb 50, 기본 로거 remote_ip·client_ip = delete
# 브라우저(Claude Browser pane, 같은 컨테이너, 1440·390)
#   가로 스크롤 없음(넘치는 요소 0, 390 에서 표·정의 목록 한 칸) · 요청은 문서뿐(자원 0) · 페이지의 CSP 위반 0(콘솔에서 fetch 를 시도하면 CSP 가 막는 것도 확인)
#   (동의 방식 판 — 로컬 정적 서버 127.0.0.1, Browser pane 390·1009) 값 없음 → "정하지 않음"·버튼 둘 · [동의] → granted·"동의함"
#   가짜 _clck·_clsk·다른 쿠키 → [동의 철회] 뒤 둘만 사라지고 denied·"거부함" · 값 off → "정하지 않음"
#   globalPrivacyControl=true 흉내(granted 위) → "GPC로 거부"·버튼 대신 안내 · setItem·getItem 예외 흉내 → "저장할 수 없음"·안내 · 390 넘침 0·자원 요청 0
#   자바스크립트 끔(스크립트를 빼고 noscript 를 펼친 사본) → 본문·h2 14개 그대로, 상태 줄·버튼 숨김, noscript 안내
#   랜딩 바닥 링크(700·n100 — 다른 칸 400·n300) → 같은 탭 /privacy · 대시보드 헤더 링크 target=_blank·rel=noopener·12px·neutral-500
# 검토 반영 — caddy:2-alpine 2.11.4 임시 컨테이너(지움): 옛 설정(50MiB·5개)으로 띄워 roll_interval 5s·roll_keep 100 으로 reload → 18초·요청 9번에 회전 파일 0,
#   docker restart 뒤 같은 18초에 2개 · format 의 remote_ip ip_mask → delete 는 reload 로 바로 바뀜 / test_privacy 11(6절 받는 곳마다 다섯 가지 — 옛 페이지의 API 문서 묶음에서 실패)
#   재검증: ruff·format 통과 · pytest 1078 passed · oxlint 0 · vite build · nginx -t(nginx:1.27-alpine, 업스트림 대역 이름) ok · caddy validate Valid · 390px 넘침 0·자원 요청 0
# 동의 방식 전환(설계 세션) — ruff·format 통과 · pytest 1081 passed(test_privacy 14) · oxlint 0 · vite build
#   계약 테스트가 잡는지: Microsoft 근거를 제3호로·4절 근거를 제17조 제1항 제2호로·행태정보 칸·알릴 사항 칸·버튼 글자·기본 꺼짐 문장·제공 칸을 하나씩 바꾸면 하나씩 실패
# 동의 방식 검토 반영 — ruff·format 통과 · pytest 1083 passed(test_privacy 16, node 26 으로 동의 스크립트 14경우) · oxlint 0 · vite build · caddy validate Valid(2.11.4, 주석만)
#   계약 테스트가 잡는지(스크립트 10·문구 11 변형, 모두 실패): 값 없음→동의함·[동의]가 denied·철회의 쿠키 지우기 뺌·GPC === false·granted 에서 버튼 숨김·옛 쓰기 실패 그리기·지우기 대신 그대로·철회 실패 안내 뺌·도메인 모양 뺌·storage 무시 / consent 앵커·다른 탭 문장·항목 '등'·2절 가리킴·IP 뺌·옛 칸 이름·국가 뺌·철회 링크 뺌·'90일 뒤'·'쓸 수 있어'·버튼 최소 너비
#   브라우저(로컬 정적 서버, 390): 버튼 둘 113×40·넘침 0 · setItem 예외의 [동의 철회] → 값 지움·"정하지 않음"·쿠키 지움 · setItem·removeItem 예외 → "동의함"·철회 실패 안내
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — 표에 `| privacy | - | web: 정적 /privacy(외부 자원 없음·CSP)·랜딩 바닥·대시보드 헤더 링크·분석 동의 저장값 kt.analytics(동의 관리) | 시행 2026-10-01 · 게시 뒤 027 런북 15단계·033 대기 |` 행. 알려진 빚: (029) "관리자 접속 기록(이메일·IP)은 처리방침 게시 전부터…" 문장에서 처리방침 부분을 지우고 UTF-8 문장만 남긴다. (027) "법정 보관 의무 확인 전" 줄은 §3.6 (4) 결과로 고치거나 지운다. 추가: `(032) 대시보드 글꼴(Google Fonts)이 방문자 IP 를 국외로 보낸다 — 자체 호스팅은 별도 결정`, `(032) 관리자 API 문서(Swagger·ReDoc)가 jsDelivr·fastapi.tiangolo.com·Google Fonts 를 부른다 — 없앨지는 029 에서 정한다`, `(032) Safari 는 7일 뒤 동의 저장값을 지운다 — 분석이 꺼지는 쪽이고 다음 방문에 띠가 다시 묻는다`, `(032) Clarity 기록은 한 사람분만 지울 수 없다 — 삭제 요청이 오면 프로젝트 삭제뿐`, `(032) 002·025 제안 반영 대기`.
- `CLAUDE.md` — 스펙 인덱스 032 행 상태 → DONE.
- `docs/context/architecture.md` — 계약 규칙 절에 "기록 항목·목적·보관·받는 곳(외부 스크립트·글꼴 포함)을 바꾸는 변경은 같은 PR 에서 `web/public/privacy.html` 을 고친다(032)". 런타임 구성 web 항목에 "`/privacy` 는 번들 밖 정적 방침(외부 자원 없음, 032)". 배포 토폴로지 serve 줄의 nginx 설명에 `= /privacy`. 현재 구조에 privacy 항목(파일·nginx 위치 셋·저장값·계약 테스트).
- `docs/context/dev-setup.md` — web 절 랜딩 문단 뒤에 방침 dev 주소 `http://localhost:5173/app/privacy.html` 과 "oxlint 대상 아님".
- `docs/context/product.md` — 기능 목록에 `privacy` 행 "개인정보 처리방침 페이지 + 화면 분석 동의 관리", 용어에 "**분석 동의**: 브라우저 저장값 `kt.analytics` — `granted`(동의)이고 GPC 가 아닐 때만 화면 분석(033)을 부른다. `denied`(거부·철회)·없음(정하지 않음 — 기본 꺼짐)·그 밖은 끈다".
- `docs/specs/022-landing.md` — §3.1 표에 `| /privacy | privacy.html(032) |` 행, §3.3-8 바닥의 바로 가기 목록 끝에 "개인정보 처리방침(→ `/privacy`, 가장 밝고 굵게 — 032)", §3.6 sitemap 줄에 `/privacy`(032 — `lastmod` 는 시행일).
- `docs/specs/027-observability.md` — §2 하지 않는 것 첫 줄 → "개인정보 처리방침(032)·브라우저 분석(033)·폰트 자체 호스팅(별도 결정)". §3.2 첫 bullet 의 "후속 스펙의 처리방침" → "032 처리방침", 파일 bullet 을 "하루(`roll_interval 24h`) 또는 50MiB 에서 회전, 회전 파일 100개까지, 보관 기간(90일)이 지난 회전 파일은 다음 회전 때 삭제 — 방문이 없으면 늦어질 수 있다(032 §3.5)" 로. §3.4 로그 bullet 의 "처리방침 게시 전" → "032 게시 전". §3.2 기본 로거 bullet("caddy 기본 로거(오류 줄 …)에도 IP 자르기·…")을 "caddy 기본 로거(오류 줄 — 예: 업스트림 502)는 IP 두 필드를 지우고(032), 쿼리 세 키·헤더 지우기는 접속 로그와 같게 건다" 로. §4 의 "기본 로거에 같은 지우기 규칙" → "기본 로거는 IP 두 필드 삭제·나머지는 같은 지우기 규칙". §4 Caddyfile 단언의 "(경로·0644·50MiB·5개)" → "(경로·0644·50MiB·24h·100개·90d)", 두 format 같음 단언에 "IP 두 줄 제외".
- `docs/specs/029-admin.md` — §3.2 접속 기록 bullet 끝 "보존·항목은 후속 처리방침 스펙이 옮긴다(status.md 빚)" → "항목·보존(90일)은 032 처리방침에 있다".
- `docs/specs/030-admin-tunnel.md` — §3.4 마지막 줄 "주 1회·13개(≈90일)·copytruncate" → "매일·90개·빈 날도 회전(90일, 032)·copytruncate". `docs/runbooks/admin-access.md` 12단계의 설정 블록·설명을 §3.5 대로, 마지막 줄 "보존 기간은 후속 처리방침 스펙이…" 삭제.
- `docs/runbooks/cloudwatch.md` — 15단계 제목과 첫 줄의 "처리방침 게시" → "032 처리방침(`https://kimptrack.com/privacy`) 게시". 회전 설정을 들이는 명령은 027 §3.2·`caddy/Caddyfile` 주석·status 빚과 함께 §4 의 `--force-recreate caddy` 로.
- `docs/specs/007-deploy.md` — §3 web 줄에 "`/privacy` 는 방침 페이지(032) — `/privacy.html`·`/app/privacy.html` 은 301".

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 002 — §3.5-1 헤더의 "우측 현재 시각" 줄 앞에 "시계 왼쪽에 `개인정보 처리방침` 링크(→ `/privacy`, 새 탭, 032)".
- 025 — §3.3 에 "알림 문구에 방문자 IP·User-Agent·쿼리·쿠키를 싣지 않는다 — 032 방침이 '운영 알림에 방문자 정보 없음' 이라고 적는다" 한 줄.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `web/public/privacy.html`, `web/nginx.conf`(위치 셋), `web/public/landing.html`(바닥 nav 한 칸·글꼴 주소), `web/public/landing/fonts/kimptrack-sans-f3ac1cc6.woff2`(옛 `058ef60e` 지움)·`web/scripts/landing-font-glyphs.txt`, `web/public/sitemap.xml`, `web/src/App.tsx`(헤더 링크), `caddy/Caddyfile`, 테스트 `server/tests/test_privacy.py`(11)·`test_observability.py`·`test_landing_seo.py`. 문서: 이 스펙, CLAUDE.md, context 넷, 스펙 007·022·027·029·030, 런북 `admin-access.md`·`cloudwatch.md`. 라이브러리 추가 없음.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - 설계 세션 허락으로 구현 전에 이 스펙을 #84 사실대로 고쳤다 — 랜딩 글꼴(자체 서브셋, jsDelivr 빠짐), 바닥은 바로 가기 nav 에 한 칸, sitemap 은 lastmod 와 함께, www 는 apex 로 301 이라 §3.3 출처 문단·§3.7 www 엣지·§6 의 023 제안·status 빚 한 줄을 지우고 §3.4-1 은 '`www` 는 기록하지 않는다' 로. §6 의 '022 §3.5 면책 낱말 삭제' 는 #84 뒤 그 낱말이 없어 할 일이 없었다. 사람 값(이메일 제외)과 법률 기본값은 §3.6 '정한 값' 에 적었다.
  - 방침 링크는 바닥 nav 의 마지막 칸. 글자 '처'·'침' 이 랜딩에 처음 나와 글꼴 서브셋을 다시 만들었다(해시 이름이 바뀐다 — 022 §5 의 옛 해시는 그 실행의 기록이라 두었다).
  - 절 제목 열두 개의 글자, 한눈에 보기 네 줄·각 절 문장은 §3.4 사실만으로 썼다. Gmail(Google LLC)은 국외 이전과 함께 위탁 절에도 넣었다(메일 보관도 처리위탁). `fastapi.tiangolo.com` 은 DNS 가 Cloudflare Pages(Cloudflare, Inc., 미국 — 2026-10-01 조회)라 파비콘 받는 곳을 그렇게 적었다. 받는 자 연락처는 회사 주소 + 개인정보 문의 링크(Microsoft 는 처리방침의 문의 양식, Google·Cloudflare 는 처리방침) — 회사 이메일은 적지 않았다.
  - 거부 스크립트: 상태 줄은 스크립트가 그릴 때만 보인다(자바스크립트가 꺼지면 noscript 만), '허용' 은 선택 안 함과 `granted` 를 가르지 않는다, 쓰기가 실패해도 쿠키는 지운다, 다른 탭에서 바꾼 선택은 `storage` 이벤트로 곧바로 보인다. 파비콘은 같은 출처 `/favicon.ico`(랜딩의 절대 주소는 외부 자원 규칙에 걸린다). 상단 바 글자는 스펙의 '서비스로 넘어가기' 그대로(022 의 '목적지를 말하는 글자' 규칙과 다르다).
  - 대시보드 헤더: `marginLeft: auto` 를 링크로 옮기고 시계와의 간격은 헤더 gap(22.4px). 탭 버튼과 같은 hover 클래스 `hv-txt`.
  - 방침은 033(Clarity)·035(관리자 24시간 요약)·CloudWatch Logs 전송(027 런북 15단계)을 스펙대로 미리 적는다 — 그 셋이 켜지기 전에는 방침이 실제보다 앞서 있다.
  - 검토 반영: 페이지에 §3.4-6 의 '방문자 정보가 가지 않는 곳'(운영 알림·S3·Influx·Redis), API 문서 묶음의 받는 자·연락처, Clarity 의 참조·클릭 주소와 033 태그·이벤트, 문의 메일은 휴지통까지(§3.5 절차). `nginx-admin.conf` 주석을 032 로. caddy 회전 설정이 reload 로 안 들어가는 것을 재현해 §4·027 §3.2·런북 15단계에 다시 만들기를 적었다 — 배포 워크플로는 그대로(매 배포 재시작은 순단이 생긴다).
  - 동의 방식 전환(설계 세션, 사람 결정 2026-10-01): §3.3 을 동의 계약으로(값 없음 = 기본 꺼짐, 동의 관리 상자와 [동의]·[동의 철회], 상태 다섯), 페이지의 한눈에 보기·2절 근거·4절 제공 근거(제17조 제1항 제1호)와 불이익 없음·6절 Microsoft 근거(제28조의8 제1항 제1호)·7절 행태정보 소항목·8절 철회, 유럽 시간대 문장 삭제. 3절 파기를 날수 약속 없이 실제 동작(caddy 는 다음 회전 때, 관리자 기록은 매일)으로. 다른 탭 반영 문장은 033 의 즉시 새로고침에 맞췄다.
  - 동의 방식 검토 반영: 동의 상자 여섯 칸을 띠가 그대로 옮길 수 있게(국가·항목 전부·철회하는 곳 — 제38조 제4항은 검토 인용이고 국가법령정보센터가 스크립트로 그려 원문은 다시 못 봤다, 사람 확인 7과 함께), 버튼 공통 최소 너비, 누른 뒤 실제 저장값 다시 읽기·철회 쓰기 실패 때 값 지우기, 동의 스크립트를 node 로 돌리는 테스트(CI 의 server job 은 러너의 node — 없으면 실패), 한눈에 보기·027·Caddyfile 의 파기 문구, 7절·2절 광고 문구, 033 절 번호(§3.7·§3.8).
- 사람 결정 2026-10-01: 동의 방식(Clarity 는 동의한 방문자만 — 기본 꺼짐, §3.3).
- 사람 확인(법률) — 게시 전에. 조항은 국가법령정보센터 현행(법 시행 2026-09-11 법률 제21445호, 시행령 시행 2026-09-11 대통령령 제36671호)으로 확인했다:
  1. **Clarity 구분·국외 이전 근거 — 사람 결정 2026-10-01: 동의 방식.** 위탁이 아니라 제3자 제공이고(Microsoft 가 자기 목적에도 쓴다), 제공의 국외 이전에는 제28조의8 제1항 제3호(처리위탁·보관용)를 쓸 수 없어 별도 동의(제1호)로 간다. 페이지는 수집·제공 = 동의(제15조 제1항 제1호·제17조 제1항 제1호), 국외 이전 = 별도 동의(제28조의8 제1항 제1호), 기본 꺼짐으로 고쳤고 033 이 동의 안내 띠를 만든다. 남은 확인: (가) 버튼 한 번([허용]·[동의])으로 수집·제공·국외 이전 동의를 함께 받는 것이 '동의 사항을 구분해 각각 받는다'(제22조 제1항 — 제15조 제1항 제1호·제17조 제1항 제1호 동의가 그 대상)에 맞는지 — 띠와 동의 관리 상자는 세 가지를 나눠 적지만 버튼은 하나다. 맞지 않으면 033 띠를 고친다. (나) 만 14세 미만의 동의(제22조의2 — 법정대리인 동의, 나이를 묻지 않는다). (다) 동의 기록이 방문자 브라우저에만 있다(서버에 새 기록을 만들지 않으려고) — 입증 방법이 필요한지.
  2. 처리 근거(Clarity 밖): 서버·관리자 접속 기록은 제15조 제1항 제6호(정당한 이익 — '명백하게 정보주체의 권리보다 우선' 요건), 권리 행사 답변은 같은 항 제2호, 그 밖의 문의는 제6호.
  3. 방문자 브라우저가 직접 부르는 자원(대시보드 Google Fonts, 운영자만 보는 API 문서의 jsDelivr·파비콘·Google Fonts)을 '넘기지 않고 알리려고 적는다' 로 쓴 방식. Gmail·Cloudflare 의 국외 이전 근거(제28조의8 제1항 제3호 가목).
  4. 통신비밀보호법 접속 기록 보관 의무 — 해당하면 3절의 '법령에 따라 따로 보존하는 기록은 없습니다' 를 고치고 원 IP 보관을 별도 스펙으로.
  5. 연락처가 이메일뿐(법 제30조 제1항 제6호 '전화번호 등 연락처'), 연락 이메일이 역할 주소가 아닌 개인 주소(§3.6 은 역할 주소를 권한다 — 사람 값 그대로 실었고 공개 레포에 남는다).
  6. 위탁 절의 AWS 수탁자 — 조직 임대 계정의 실제 계약 당사자가 Amazon Web Services, Inc. 인지.
  7. 게시 직전 원문 다시 보기: Clarity 약관의 방침 문구 요구(4.4(b))·받는 자·저장 국가, Microsoft 문의 양식 주소. jsDelivr 운영사는 검토 반영 때 원문(GitHub `jsdelivr/jsdelivr` `Privacy Policy.md`)으로 확인했다 — 연락처는 다른 곳처럼 처리방침 링크.
- 남은 빚:
  - 배포 뒤 serve 적용: caddy 의 지우기 규칙은 배포의 `caddy reload` 가 읽지만 접속 로그 회전 설정(`output file`)은 읽지 않는다 — serve 의 caddy 는 v2.11.4(2026-10-01 확인), 머지 뒤 serve 에서 caddy 를 한 번 다시 만든다 — `docker compose --profile serve --env-file .env --env-file server/.env up -d --force-recreate caddy`(몇 초 끊김, §4 — 027 §3.2·런북 15단계·Caddyfile 주석에도 같은 명령). 관리자 기록 logrotate 는 런북 `admin-access.md` 12단계를 다시 적용하고 `sudo logrotate -d …` 로 확인, 이틀 뒤 `logs/caddy/` 에 하루 회전 파일. 그 뒤 027 런북 15단계와 033 시작.
  - 담당자 제안(PR 본문): 002 헤더 줄, 025 알림 문구 한 줄(§6).
  - caddy 는 기록할 요청이 없는 날 회전·삭제를 하지 않는다 — 방문이 며칠 없으면 90일 지난 파일이 다음 방문 때 지워진다(§3.7).
  - 대시보드 링크의 새 탭은 Browser pane 이 같은 탭으로 열어 속성(`target`·`rel`)으로만 확인했다 — 실제 브라우저에서 새 탭 확인은 배포 뒤 사람.
  - 이전 판 규칙(`privacy-<시행일>.html`)은 첫 개정 때 처음 쓴다 — 테스트는 파일이 생기면 곧바로 본다.
