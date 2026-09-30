# 032 — privacy

상태: TODO | 의존: 027 observability(caddy 접속 기록·CloudWatch Logs 전송 보류), 029 admin(관리자 접속 기록), 030 admin-tunnel(Cloudflare Access·관리자 기록 회전), 022 landing(바닥), 002 web-shell(헤더 — 팀원 담당, 코드만 고친다), 025 slack-alerts(알림 문구), 028 api-allowlist(공개 nginx 모양), 023 domain-tls(apex·www). **사람 값(§3.6)을 PR 안에서 채운 뒤에만 머지한다 — 머지가 곧 게시다.** 033 clarity 는 이 스펙이 게시된 뒤 시작한다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
방문자가 KimpTrack 이 무엇을 기록하고 어디로 보내며 얼마 동안 두는지를 `https://kimptrack.com/privacy` 한 페이지에서 읽고, 화면 분석(033 Clarity)을 거부할 수 있게 한다. 지금은 caddy 접속 기록(027)과 관리자 접속 기록(029)이 방침 없이 박스 안에 쌓이고, CloudWatch Logs 전송(027 런북 15단계)과 Clarity(033)가 이 페이지를 기다린다. 이 스펙은 사실(무엇을·어디로·얼마나)과 게시 조건까지 정하고, 법률 판단은 사람이 확인한다(§3.6).

## 2. 범위
- 만드는 것: `web/public/privacy.html`(번들 밖 정적 한 파일 — 022 의 landing.html 과 같은 방식), 공개 nginx 위치 셋(`= /privacy`·`= /privacy.html`·`= /app/privacy.html`), 랜딩 바닥·대시보드 헤더 링크, `sitemap.xml` 한 줄, 분석 거부 저장값 `kt.analytics`(§3.3 — 033 이 읽는다), 계약 테스트 `server/tests/test_privacy.py`.
- 하지 않는 것:
  - Clarity 스니펫·설정·마스킹·검색어 URL 처리(033). 이 페이지는 033 이 켤 도구의 사실(§3.4-2)을 미리 싣는다.
  - 동의 배너(opt-in). 거부 방식으로 시작한다 — §3.6 법률 확인이 동의가 필요하다고 하면 이 스펙과 033 을 먼저 고친다.
  - 글꼴 자체 호스팅(Google Fonts·jsDelivr 를 없애는 일) — 방침에 사실대로 적는다. 없애기는 002·022·`docs/design` 을 함께 바꾸는 별도 결정(사람).
  - CloudWatch Logs 전송 자체(027 런북 15단계 — 게시 뒤 사람), 관리자 화면의 방침 링크(035 — 운영자만 쓴다), 영어판, 연락 메일 주소 만들기(사람).
- 바꾸는 기존 것:
  1. 022 — §3.3-6 바닥에 링크 하나(§3.2), §3.1 경로 표에 `/privacy`.
  2. 027 §3.2 — caddy 회전에 하루 주기(§3.5). 지금은 현재 파일이 50MiB 에 닿기 전까지 회전하지 않아, 방문이 적으면 90일보다 오래된 줄이 남는다 — 방침의 90일이 거짓이 된다. 기본 로거(오류 줄)는 IP 를 자르지 않고 지운다(§3.5) — 그 줄은 docker 로그에 크기로만 회전해 90일보다 오래 남을 수 있다.
  3. 030 §3.4·런북 `admin-access.md` 12단계 — 관리자 기록 회전을 주 1회·13개에서 매일·90개로(§3.5). 지금은 `notifempty` 라 쓰지 않은 주는 회전하지 않아 13개가 90일보다 길게 늘어난다.
  4. 002(코드만) — 대시보드 헤더에 링크 하나(`web/src/App.tsx`).
- 담당: 002 는 팀원, 023·025 는 hereokay 담당이라 이 PR 은 그 스펙들을 **고치지 않는다** — §6 "담당자에게 제안" 을 PR 본문에 적는다(CLAUDE.md §5). 007·022·027·029·030 은 이 레포 주인 담당이라 고친다.

## 3. 동작

### 3.1 페이지와 경로
| 요청 | 응답 |
|---|---|
| `/privacy` | `privacy.html` |
| `/privacy.html` | `301 /privacy` |
| `/app/privacy.html` | `301 /privacy` |

- 공개 nginx(:80)의 정확 일치 위치 셋. `/app/privacy.html` 을 막는 이유: dist 의 파일이 `location /app/` alias 로 CSP 없이 200 으로 나간다. `= /privacy` 는 `try_files /privacy.html =404`(022 의 `= /` 와 같은 모양 — 빌드 때 만든 `.gz` 를 `gzip_static` 이 준다)이고 머리 둘을 `always` 로 붙인다: `Cache-Control: no-cache`(고치면 다음 방문에 바로 보이고, 안 바뀌었으면 304), `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. CSP 는 "이 페이지는 외부 자원도 서버 호출도 하지 않는다" 를 브라우저가 지키게 한다. `/privacy/` 는 기존 규칙대로 404, 쿼리가 붙어도 같은 페이지다. 정규식 위치는 두지 않는다(028).
- 페이지는 외부 자원을 하나도 부르지 않는다: 글꼴은 시스템 글꼴(Pretendard 도 부르지 않는다), 그림 없음(favicon 만), 스크립트·스타일은 파일 안. Clarity 를 넣지 않는다(033 도 이 페이지는 뺀다) — 방침을 읽고 거부하는 동안 분석이 돌지 않게.
- 서버 기록: caddy 가 다른 페이지처럼 기록한다 — 027 §3.2 제외 목록에 넣지 않는다(방문 한 줄이고, 방침을 읽는 규모를 알 수 있다). 분석 거부 조작은 브라우저 안에서만 일어나 요청을 만들지 않으므로 서버에 남지 않는다. nginx 접속 로그는 이미 꺼져 있다(027).
- 모양: 랜딩(022 §3.4)의 색 토큰 값을 복사한 어두운 화면, 본문 17px·줄간 1.7·한 줄 34em 이하, `lang="ko"`, `<title>` "개인정보 처리방침 — KimpTrack", canonical `https://kimptrack.com/privacy`. 위에서 아래로: 상단 바(워드마크 → `/`, "서비스로 넘어가기" → `/app/`), 제목·시행일, 한눈에 보기 네 줄(가입 없음 / 서버 접속 기록 90일 / 화면 분석 Clarity·거부 가능 / 연락처), 분석 거부(§3.3), 본문 절(§3.4 순서), 변경 이력. 390px 에서 가로 스크롤이 없다 — 표는 좁은 폭에서 행마다 세로로 쌓는다.
- dev 서버에서는 `http://localhost:5173/app/privacy.html`(public 이 base 아래로 서빙된다 — 022 와 같다). oxlint 대상이 아니다.

### 3.2 링크
- 랜딩 바닥(022): 왼쪽 "개인정보 처리방침"(→ `/privacy`, 같은 탭), 오른쪽 기존 "서비스로 넘어가기". 방침 링크는 바닥에서 가장 밝고 굵은 글자다 — 작성지침이 다른 고지와 구분해 보이게 하라고 권한다.
- 대시보드 헤더(002, 코드만): 오른쪽 시계 왼쪽에 "개인정보 처리방침"(12px·`neutral-500`, 탭 버튼과 같은 hover) → `/privacy`, 새 탭(`target="_blank"`·`rel="noopener"`) — 열어 둔 대시보드의 WebSocket 을 끊지 않게. 푸터가 아닌 이유: 입출금 레이더 탭은 셸 푸터를 그리지 않는다.
- `sitemap.xml` 에 `https://kimptrack.com/privacy` 한 줄.

### 3.3 분석 거부 — 저장값 하나 (033 이 읽는 계약)
- 브라우저 `localStorage` 키 `kt.analytics`, 값 `denied`(거부) 또는 `granted`(다시 허용). 키가 없거나 다른 값이면 "선택 안 함" = 허용(거부 방식). 두 값을 다 두는 이유: 법률 확인이 동의 방식을 요구하면 "선택 안 함" 의 뜻만 거부로 바꾸면 되고 저장값의 뜻은 그대로다.
- 읽는 쪽 규칙(033 이 지킨다): `denied` 이거나, `navigator.globalPrivacyControl === true` 이거나, 저장소를 읽다 예외가 나면 Clarity 를 부르지 않는다. GPC 는 `granted` 보다 앞선다. DNT 는 보지 않는다 — Clarity 자신도 DNT 에 답하지 않는다(Microsoft Clarity FAQ, 2026-09-21 갱신판).
- 쿠키가 아닌 이유: 서버로 가지 않고 만료가 없다. 저장은 출처(origin)마다라 `kimptrack.com` 과 `www.kimptrack.com` 의 선택은 따로다 — 023 은 www 를 apex 로 보내지 않고 같은 페이지를 준다(§6 제안). Clarity 는 www 없는 주소에서만 돈다(033)는 것까지 합쳐, 페이지는 "`kimptrack.com` 에서 고른 선택이 이 브라우저에 저장되고, 브라우저 데이터를 지우면 다시 골라야 한다" 고 적는다. 한 줄 더: "일부 브라우저(Safari 등)는 이 사이트를 7일 동안 쓰지 않으면 선택을 지웁니다 — 계속 거부하려면 GPC 나 광고 차단기를 쓰세요". Safari(ITP)는 스크립트가 쓴 저장값을 사이트와 7일 동안 상호작용이 없으면 지운다 — 사용자가 지우지 않아도 거부가 풀린다.
- 반영 시점(033 과 같은 문장): 새로 여는 페이지는 거부를 곧바로 따른다. 같은 브라우저에 이미 열린 `kimptrack.com` 랜딩·대시보드 탭은 거부를 누르는 순간 Clarity 에 거부 신호를 보내 쿠키를 지우고, 그 탭을 다시 볼 때 한 번 새로고침되어 그때부터 분석하지 않는다(033). 페이지가 이 문장을 그대로 적는다.
- 방침 페이지의 "분석 거부" 절: 지금 상태 한 줄(허용 / 거부 — 이 브라우저의 선택 / 거부 — 브라우저가 GPC 를 보냄 / 저장할 수 없음)과 버튼 둘.
  - [분석 거부]: `denied` 를 쓰고, 이 사이트의 Clarity 1차 쿠키 `_clck`·`_clsk`(Microsoft Clarity 쿠키 목록)를 경로 `/` 로 만료시킨다 — 도메인 속성 없이 한 번, 호스트에서 앞의 `www.` 를 뗀 도메인으로 한 번(어느 쪽으로 심겼든 지워지게). Microsoft 도메인의 제3자 쿠키(`MUID` 등)는 이 페이지가 지울 수 없어 Microsoft 거부 페이지 `https://optout.aboutads.info/`(Clarity FAQ 가 안내하는 DAA)를 함께 안내한다.
  - [다시 허용]: `granted` 를 쓴다. GPC 가 켜져 있으면 버튼 대신 "브라우저가 GPC 를 보내는 동안은 분석하지 않습니다".
  - 쓰기 예외(사생활 모드·저장소 차단): 상태를 "저장할 수 없음" 으로 두고 "GPC·쿠키 차단·광고 차단기로도 거부할 수 있습니다". 자바스크립트가 꺼지면 `<noscript>` 로 같은 안내.
- 쓰는 곳은 이 페이지 하나다. 033 은 읽기만 하고(거부·GPC 면 남은 `_clck`·`_clsk`·`_cltk` 를 지운다 — 033), 다른 페이지에 거부 버튼을 두지 않는다.

### 3.4 페이지에 적는 사실
절은 개인정보 보호법 제30조 제1항·시행령 제31조 제1항 항목을 따른다(국가법령정보센터 현행, 시행 2026-09-11 판 — 2026-10-01 확인. 작성지침은 2026-04-24 개정판). 문장은 실행 세션이 쓰되 아래 사실만 쓴다 — 하지 않는 조치(저장 암호화·정기 점검 등)를 약속하지 않는다. 말투는 랜딩과 같은 존댓말.

| 기록 | 보관 |
|---|---|
| 서버접속 | 90일 |
| 관리자접속 | 90일 |
| 화면녹화 | 30일 |
| 문의메일 | 〔문의보관〕 |

Clarity 의 히트맵·즐겨찾기·표본 녹화·라벨은 최대 9개월이다(2 참고). 보관하는 곳은 1~4 에 적는다.

1. **서버 접속 기록**(027 §3.2 복사) — `kimptrack.com`·`www` 로 온 요청마다 caddy 가 JSON 한 줄: 시각, IP 앞부분(IPv4 는 마지막 마디, IPv6 는 뒤 80비트를 0 으로 — /24·/48), 접속 포트, 프로토콜·메서드·호스트, 경로와 쿼리(스프레드·갭·선선갭 검색어 `s.q`·`g.q`·`p.q` 는 지움 — 기록 탭의 코인 칸 `sym`·`utm_*` 는 남는다), 상태 코드·크기·처리 시간(대시보드 WebSocket 은 연결을 연 시간), 이전 페이지의 사이트 주소(도메인까지), 브라우저·기기 정보(`User-Agent`). 헤더·쿠키는 남기지 않고, 폴링 경로 다섯과 감시(canary) 요청은 기록하지 않는다. 목적: 장애·오류 확인, 어떤 경로로 들어와 얼마나 머무는지 규모 파악. 위치: serve 서버(AWS 서울) 파일, 게시 뒤 AWS CloudWatch Logs 서울(027 런북 15단계). 보관 90일(§3.5). 관리자 화면의 24시간 요약을 위해 같은 서버의 api 프로세스가 이 파일을 읽는다(034b — 새로 저장하지 않는다). 컨테이너 로그(docker)에는 방문자 IP 가 없다 — caddy 오류 줄은 IP 를 지우고 검색어를 뺀 경로만 남기며(§3.5, 크기 상한까지 docker 로그에 남는다), nginx·앱 로그의 접속 주소는 앞단 컨테이너다.
2. **화면 이용 기록 — Microsoft Clarity**(033 이 켠다, 랜딩·대시보드만, 유럽 시간대 브라우저는 제외). 받는 것: 페이지 주소(검색어는 033 이 뺀다), 클릭·스크롤·마우스 움직임·화면 크기, 화면 내용(입력칸은 늘 가림), 기기·브라우저·OS, IP 주소(Microsoft 가 받아 대략 위치를 추정 — Clarity FAQ), 1차 쿠키 `_clck`(같은 브라우저를 알아보는 무작위 ID, 1년)·`_clsk`(한 방문의 페이지를 잇는다, 1일 — 기간은 033 의 2026-09-28 실측), sessionStorage `_cltk`(탭 ID — clarity-js `TabKey`). 받는 곳 Microsoft Corporation, 미국(Microsoft Azure — 국가는 게시 직전 약관으로 다시 본다, §3.6). 목적: 어떤 화면·기능을 쓰고 어디서 막히는지. 보관: 녹화 30일, 히트맵·즐겨찾기·무작위 표본 녹화·라벨 최대 9개월(Clarity FAQ 2026-09-21). 한 사람분만 지울 수는 없고 프로젝트를 통째로 지워야 한다(같은 FAQ). Clarity 약관이 방침에 요구하는 것을 싣는다: Microsoft 가 방문자 개인정보를 수집한다는 사실, Microsoft 가 그 정보를 Microsoft Advertising 제공 등 자기 목적에도 쓴다는 사실(Microsoft 처리방침에 따름), Microsoft 처리방침 링크 `https://privacy.microsoft.com/ko-kr/privacystatement`, 거부 방법(§3.3). 우리 쪽은 광고 목적 저장에 거부 신호를 보낸다(033 — `ad_Storage: denied`).
3. **관리자 접속 기록**(029 §3.2 복사) — 운영자만 해당. `admin.kimptrack.com` 요청마다 시각·로그인 이메일·IP·메서드·경로와 쿼리·상태·처리 시간·Cloudflare 요청 ID·`Sec-Fetch-Site`. serve 서버 파일, 90일(§3.5). 로그인은 Cloudflare Access(허용한 이메일로 받는 일회용 코드)가 하고 Cloudflare, Inc.(미국)가 이메일·IP 를 처리한다(무료 요금제 인증 기록 24시간 — 030).
4. **문의 메일** — 권리 행사·문의로 받은 이메일 주소와 내용. 목적 답변, 보관 〔문의보관〕, 메일 서비스 〔메일 서비스〕(사업자·국가 — 국외면 국외 이전 절에 한 줄).
5. **외부 글꼴 요청** — 방문자의 브라우저가 직접 보낸다. 대시보드는 Google Fonts(Google LLC, 미국 — IP·`User-Agent`·요청 주소·이전 페이지 도메인이 가고 쿠키는 없으며 Google 이 요청 기록을 남긴다 — Google Fonts FAQ), 랜딩은 jsDelivr(Volentio JSD Limited, 영국 — Cloudflare·Fastly 등 CDN 이 전달). 관리자 화면의 API 문서(FastAPI 기본 Swagger·ReDoc)는 jsDelivr 와 파비콘 `fastapi.tiangolo.com` 을 부르고, ReDoc 은 Google Fonts(Montserrat·Roboto)도 부른다(운영자만 — `fastapi/openapi/docs.py` 기본값, 2026-10-01 확인). 없애는 일(`with_google_fonts=False`·파비콘 자체 호스팅)은 029 에서 따로 정한다. 방침 페이지는 부르지 않는다.
6. **하지 않는 것** — 회원가입·로그인·이름·전화번호·결제 없음, 나이를 묻지 않음, 맞춤형 광고·판매 없음. 운영 알림(Slack·AWS 경보·uptime 감시)에는 방문자 정보가 가지 않는다: 025 알림 문구는 역할·거래소·실패 종류·상태 코드·거래소 오류 문구·로거 이름과 문장·예외 종류와 문장·요청 메서드와 경로(쿼리 없음)뿐이다(025 §3.3·`server/app/main.py` 처리 안 된 예외 로그 — 2026-10-01 확인). 거래소 원문(S3)·시세 저장(Influx·Redis)에도 방문자 정보가 없다.

절 순서와 내용(해당 없는 민감정보·가명정보·자동화된 결정·아동·국내대리인 절은 두지 않는다. 테스트가 제목 열둘을 본다):
1. 처리 목적. 2. 처리 항목과 보유 기간 — 위 표와 1~4, 동의 없이 처리하는 근거 〔처리 근거〕. 3. 파기 — 기간이 끝나면 자동으로 파일째 지운다(§3.5). 4. 제3자 제공 — 〔Clarity 구분〕 이 제공이면 Microsoft 를 여기에, 아니면 "제공하지 않습니다". 5. 처리 위탁 — 〔AWS 수탁자〕(서버·저장·기록, 서울), Cloudflare(관리자 로그인·터널). 6. 국외 이전 — Microsoft·Google·jsDelivr·Cloudflare(운영자만)·API 문서의 파비콘 서버와 Google Fonts(운영자만 — 5)·(국외면) 메일 서비스마다 시행령 제31조 제1항 제2호대로 근거와 법 제28조의8 제2항 다섯 가지(항목 / 국가·시기·방법 / 받는 자 이름·연락처 / 목적·보유 기간 / 거부 방법·절차·효과 — 글꼴은 "외부 글꼴을 막으면 기본 글꼴로 보입니다"). 7. 자동 수집 장치와 거부 — 쿠키 두 개·sessionStorage `_cltk`·저장값 `kt.analytics`, 거부는 §3.3·브라우저 쿠키 차단·광고 차단기·Microsoft 거부 페이지. 8. 정보주체의 권리 — 연락 메일로 열람·정정·삭제·처리정지를 요구할 수 있고, 서버 기록은 IP 끝자리를 지워 한 사람을 가려내기 어렵고 Clarity 는 한 사람분만 지울 수 없다는 한계를 그대로 적는다. 9. 안전성 확보 조치 — HTTPS, 기록 최소화(IP 앞부분·검색어·헤더 지움), 관리자 화면 로그인(허용한 이메일의 일회용 코드)과 인바운드 포트 없는 터널, 서버 접근 제한(SSH 키·보안그룹), Clarity 입력칸 가림. 10. 개인정보 보호책임자 — 〔책임자〕·〔연락 이메일〕·〔전화〕. 11. 권익침해 구제 — 개인정보분쟁조정위원회 1833-6972·개인정보침해신고센터 118·대검찰청 1301·경찰청 182(법제처 처리방침 표기와 같다). 12. 처리방침 변경 — 시행일 〔시행일〕, 변경 이력.

### 3.5 보관 기간을 지키는 설정
- 파기 기한: 보관 기간이 끝난 날부터 5일 안(개인정보 포털 처리방침과 같은 기준). 아래 접속 로그·CloudWatch Logs·관리자 접속 기록은 모두 그 안이다.
- caddy 접속 로그(027 조각 `access_log` 의 파일 출력): `roll_interval 24h` 를 더하고 `roll_keep` 5 → 100. `roll_size 50MiB`·`roll_keep_for 90d`·`mode 0644` 는 그대로. 하루(또는 50MiB)마다 새 파일이 되고 회전 파일은 90일 뒤 지운다 — 지우기는 회전 때 돌아 최대 2일 늦다. 100개는 하루 1개 × 90일에 크기 회전 여유다. `roll_interval` 은 caddy `log` 문서(2026-10-01)에 있다(Caddy 2.11 부터) — 쓰는 이미지의 `caddy validate` 가 거부하면 멈추고 묻는다.
- caddy 기본 로거(`log default` — 오류 줄, docker 로그 50MB×3 으로 크기로만 회전): `request>remote_ip`·`request>client_ip` 를 `ip_mask` 에서 `delete` 로. 오류 줄에는 IP 가 필요 없고, 방문이 적으면 이 줄이 90일보다 오래 남는다. 나머지 지우기 규칙(헤더·검색어)은 접속 로그와 같게 둔다.
- CloudWatch Logs `/marketlens/serve/caddy`: 보존 90일(027 그대로) — 만료된 줄은 보통 72시간 안에 지워진다(AWS CloudWatch Logs 문서).
- 관리자 접속 기록(logrotate): `weekly`·`rotate 13`·`notifempty` → `daily`·`rotate 90`·`ifempty`, `copytruncate`·`missingok` 그대로. 빈 날도 회전해야 90개가 곧 90일이다.
- 서비스를 닫거나 호스팅을 옮길 때(AWS 계정은 2026년 12월에 끝난다): 옮기기 전에 이 방침을 고쳐 새 위탁·보관 위치를 적고, 옛 서버 파일·CloudWatch 로그 그룹·Clarity 프로젝트는 사람이 지운다.

### 3.6 게시 조건 — 사람이 채우는 값
- 페이지의 자리표시자 `〔…〕` 열: 〔운영 주체〕(처리자 표기), 〔책임자〕(개인정보 보호책임자 성명 또는 부서), 〔연락 이메일〕, 〔전화〕(없으면 줄째 뺀다), 〔메일 서비스〕, 〔문의보관〕(기본 "답변을 마친 날부터 1년"), 〔AWS 수탁자〕(계약 당사자 명칭 — 조직 임대 계정), 〔시행일〕, 그리고 법률 확인 결과로 정하는 〔처리 근거〕·〔Clarity 구분〕. 실행 세션은 시작할 때 이 값을 사람에게 받고, 없으면 멈추고 묻는다. `〔` 가 한 글자라도 남으면 계약 테스트가 실패해 머지(= 배포 = 게시)되지 않는다.
- 연락 이메일은 페이지와 공개 레포에 그대로 실린다 — 개인 주소가 아니라 역할 주소를 권한다.
- 법률 확인(사람, 게시 전): (1) Microsoft 가 자기 목적에도 쓰는 Clarity 가 국외 "제공" 인지 위탁인지, 동의가 필요한지(법 제17조·제28조의8) — 필요하면 동의 방식으로 이 스펙·033 을 먼저 고친다. (2) 동의 없이 처리하는 근거 조항. (3) 방문자 브라우저가 직접 부르는 글꼴 두 곳을 국외 이전으로 적는 방식. (4) 통신비밀보호법의 접속 기록 3개월 보관 의무 해당 여부 — 해당하면 원 IP 보관을 별도 스펙으로 정하고 "법령에 따른 보관" 을 방침에 더한다(027 빚). (5) 이메일만으로 연락처 요건이 되는지. Clarity 약관의 방침 문구 요구(2026-09-28 조사 — 원문 4.4(b))와 받는 자·저장 국가, jsDelivr 운영사는 게시 직전 원문을 다시 본다(약관 페이지가 스크립트로 그려져 2026-10-01 에는 검색 요약으로만 확인했다).
- 게시 뒤 풀리는 것: 027 런북 15단계(CloudWatch Logs 전송·지표 필터·5xx 경보 — 사람)와 033 시작. 033 배포 전에는 방침의 Clarity 절이 실제보다 앞서 있다 — 033 이 끝내 켜지지 않으면 그 절을 지운다.
- 고칠 때: 기록 항목·목적·보관·받는 곳(외부 스크립트·글꼴 포함)을 바꾸는 PR(033·034b·호스팅 이전 등)은 같은 PR 에서 이 페이지를 고친다. 이전 판은 `web/public/privacy-<그 판의 시행일 YYYYMMDD>.html` 로 남겨 변경 이력에서 잇는다 — `location /` 로 CSP 없이 나가므로 스크립트를 뺀 정적 사본으로 두고(거부 절은 "지금 판에서 고른다" 링크 한 줄), 계약 테스트가 외부 자원 0 을 함께 본다. 항목·목적이 바뀌는 변경은 바뀌는 절의 전후 대조를 변경 이력에 싣고 시행일 전에 게시한다(작성지침 2026-04-24 — 중대한 변경은 대조표 등으로 따로 알린다).

### 3.7 엣지
- 자바스크립트 꺼짐: 본문은 다 읽히고 거부 절만 `<noscript>` 안내.
- Safari 는 스크립트가 쓴 저장값을 사이트와 7일 동안 상호작용이 없으면 지운다 — 거부가 풀리고 다음 방문에 Clarity 가 켜진다. 페이지가 이 사실과 GPC·광고 차단기 안내를 적는다(§3.3).
- www 에서 거부를 눌러도 apex 에는 선택이 없다(§3.3) — Clarity 는 apex 에서만 돌므로 apex 방침 페이지에서 고른 것만 효과가 있다. www 의 방침 페이지도 같은 버튼을 보이되 이 사실을 한 줄 적는다(023 제안이 반영되면 지운다).
- 방침 페이지가 안 뜸(배포 실패): 랜딩·대시보드 링크가 404 — canary 는 이 경로를 보지 않는다. 배포 뒤 사람이 확인(§4).
- caddy 에 기록할 요청이 하루 없으면 그날은 회전도 삭제도 없다(caddy 는 다음 기록 때 회전한다) — 다음 방문 때 지운다.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 §3.6 값을 사람에게 받는다(없으면 멈춘다).
- `server/tests/test_privacy.py`(test_deploy 의 nginx 파서 재사용): 공개 server 에 `= /privacy`(`try_files /privacy.html`, `Cache-Control no-cache` 와 §3.1 CSP 둘 다 `always`)·`= /privacy.html`·`= /app/privacy.html`(둘 다 301 `/privacy`), 정규식 위치 없음·028 허용 목록 그대로 / `privacy.html`: `〔` 없음, `lang="ko"`, §3.4 절 제목 열둘, canonical `https://kimptrack.com/privacy` 있음 / 외부 자원 없음 — `<script src>`·`<img src>` 와 자원을 부르는 `<link rel>`(`stylesheet`·`preload`·`modulepreload`·`preconnect`·`dns-prefetch`·`icon`)의 주소가 `http(s)://`·`//` 로 시작하지 않는다(`rel="canonical"` 과 `<a href>` 는 허용) / 인라인 `<script>` 본문에만 `fetch(`·`new WebSocket(`·`XMLHttpRequest`·`sendBeacon`·`clarity.ms` 가 없다 — 본문 글자는 보지 않는다(요청 자체는 CSP 가 막는다) / `kt.analytics`·`denied`·`granted`·`globalPrivacyControl`·`_clck`·`_clsk`·`_cltk`·`Safari`·`7일`·Microsoft 처리방침·DAA 링크·구제 기관 번호 넷 있음 / `web/public/privacy-*.html` 이 있으면 각각 `〔`·`<script` 없음과 같은 외부 자원 단언 / `landing.html` 바닥에 `/privacy` / `web/src/App.tsx` 에 `/privacy` 와 `noopener` / `sitemap.xml` 에 `/privacy`.
- `server/tests/test_observability.py`: 조각 `access_log` 에 `roll_interval 24h`·`roll_keep 100`. 기본 로거는 `remote_ip`·`client_ip` 가 `delete` 이고, "두 format 같음" 단언은 이 두 줄만 예외로 두고 나머지는 그대로.
- 브라우저(로컬 web 이미지 또는 dev, 1440·390px): 가로 스크롤 없음 / 네트워크 목록에 문서·favicon 외 요청 0 / 콘솔 CSP 위반 0 / 미리 심은 가짜 `_clck`·`_clsk` 가 [분석 거부] 뒤 사라지고 `kt.analytics` 가 `denied` / [다시 허용] 뒤 `granted` / `navigator.globalPrivacyControl` 을 true 로 흉내 내면 GPC 안내 / `localStorage` 쓰기가 예외를 던지게 흉내 내면 "저장할 수 없음" / 자바스크립트 끔 → 본문과 noscript / 랜딩 바닥 링크(같은 탭)·대시보드 헤더 링크(새 탭).
- 로컬 Docker(web 이미지): `nginx -t` / `curl -sI localhost:<포트>/privacy` 200·`Cache-Control: no-cache`·CSP, gzip 요청에 `Content-Encoding: gzip` / `/privacy.html`·`/app/privacy.html` 301 `/privacy` / `/privacy/` 404 / `caddy validate`(바뀐 조각·기본 로거)와 그 이미지의 `caddy version`(2.11 이상). nginx·Caddyfile 을 고쳤으므로 PR 본문 테스트 칸에 `nginx -t`·`caddy validate` 결과를 적는다(027·029 빚).
- 기존 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`.

**배포 뒤 — 사람(완료 조건 아님)**: `https://kimptrack.com/privacy`·`https://www.kimptrack.com/privacy` 200 / 이틀 뒤 serve `logs/caddy/` 에 하루 회전 파일 / 런북 12단계 logrotate 다시 적용 뒤 `sudo logrotate -d /etc/logrotate.d/marketlens-admin` / 027 런북 15단계 / 033 을 시작해도 된다고 알림.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — 표에 `| privacy | - | web: 정적 /privacy(외부 자원 없음·CSP)·랜딩 바닥·대시보드 헤더 링크·분석 거부 저장값 kt.analytics | 시행 〔시행일〕 · 게시 뒤 027 런북 15단계·033 대기 |` 행. 알려진 빚: (029) "관리자 접속 기록(이메일·IP)은 처리방침 게시 전부터…" 문장에서 처리방침 부분을 지우고 UTF-8 문장만 남긴다. (027) "법정 보관 의무 확인 전" 줄은 §3.6 (4) 결과로 고치거나 지운다. 추가: `(032) 분석 거부 저장값은 출처마다 — www 와 apex 가 따로(023 제안 대기)`, `(032) 글꼴 두 곳(Google Fonts·jsDelivr)이 방문자 IP 를 국외로 보낸다 — 자체 호스팅은 별도 결정`, `(032) 관리자 API 문서(Swagger·ReDoc)가 jsDelivr·fastapi.tiangolo.com·Google Fonts 를 부른다 — 없앨지는 029 에서 정한다`, `(032) Safari 는 7일 뒤 거부 저장값을 지운다 — 방침에 GPC·광고 차단기 안내`, `(032) Clarity 기록은 한 사람분만 지울 수 없다 — 삭제 요청이 오면 프로젝트 삭제뿐`, `(032) 002·023·025 제안 반영 대기`.
- `CLAUDE.md` — 스펙 인덱스 032 행 상태 → DONE.
- `docs/context/architecture.md` — 계약 규칙 절에 "기록 항목·목적·보관·받는 곳(외부 스크립트·글꼴 포함)을 바꾸는 변경은 같은 PR 에서 `web/public/privacy.html` 을 고친다(032)". 런타임 구성 web 항목에 "`/privacy` 는 번들 밖 정적 방침(외부 자원 없음, 032)". 배포 토폴로지 serve 줄의 nginx 설명에 `= /privacy`. 현재 구조에 privacy 항목(파일·nginx 위치 셋·저장값·계약 테스트).
- `docs/context/dev-setup.md` — web 절 랜딩 문단 뒤에 방침 dev 주소 `http://localhost:5173/app/privacy.html` 과 "oxlint 대상 아님".
- `docs/context/product.md` — 기능 목록에 `privacy` 행 "개인정보 처리방침 페이지 + 분석 거부", 용어에 "**분석 거부**: 브라우저 저장값 `kt.analytics`(`denied`·`granted`) — `denied` 이거나 GPC 면 화면 분석(033)을 부르지 않는다".
- `docs/specs/022-landing.md` — §3.1 표에 `| /privacy | privacy.html(032) |` 행, §3.3-6 바닥을 "왼쪽 '개인정보 처리방침'(→ `/privacy`, 가장 밝고 굵게 — 032) · 오른쪽 '서비스로 넘어가기'" 로, §3.5 마지막 줄의 "면책" 낱말 삭제(022 §7 보고).
- `docs/specs/027-observability.md` — §2 하지 않는 것 첫 줄 → "개인정보 처리방침(032)·브라우저 분석(033)·폰트 자체 호스팅(별도 결정)". §3.2 첫 bullet 의 "후속 스펙의 처리방침" → "032 처리방침", 파일 bullet 을 "하루(`roll_interval 24h`) 또는 50MiB 에서 회전, 회전 파일 100개까지, 90일 뒤 삭제(032)" 로. §3.4 로그 bullet 의 "처리방침 게시 전" → "032 게시 전". §3.2 기본 로거 bullet("caddy 기본 로거(오류 줄 …)에도 IP 자르기·…")을 "caddy 기본 로거(오류 줄 — 예: 업스트림 502)는 IP 두 필드를 지우고(032), 쿼리 세 키·헤더 지우기는 접속 로그와 같게 건다" 로. §4 의 "기본 로거에 같은 지우기 규칙" → "기본 로거는 IP 두 필드 삭제·나머지는 같은 지우기 규칙". §4 Caddyfile 단언의 "(경로·0644·50MiB·5개)" → "(경로·0644·50MiB·24h·100개·90d)", 두 format 같음 단언에 "IP 두 줄 제외".
- `docs/specs/029-admin.md` — §3.2 접속 기록 bullet 끝 "보존·항목은 후속 처리방침 스펙이 옮긴다(status.md 빚)" → "항목·보존(90일)은 032 처리방침에 있다".
- `docs/specs/030-admin-tunnel.md` — §3.4 마지막 줄 "주 1회·13개(≈90일)·copytruncate" → "매일·90개·빈 날도 회전(90일, 032)·copytruncate". `docs/runbooks/admin-access.md` 12단계의 설정 블록·설명을 §3.5 대로, 마지막 줄 "보존 기간은 후속 처리방침 스펙이…" 삭제.
- `docs/runbooks/cloudwatch.md` — 15단계 제목과 첫 줄의 "처리방침 게시" → "032 처리방침(`https://kimptrack.com/privacy`) 게시".
- `docs/specs/007-deploy.md` — §3 web 줄에 "`/privacy` 는 방침 페이지(032) — `/privacy.html`·`/app/privacy.html` 은 301".

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 002 — §3.5-1 헤더의 "우측 현재 시각" 줄 앞에 "시계 왼쪽에 `개인정보 처리방침` 링크(→ `/privacy`, 새 탭, 032)".
- 023 — `www.kimptrack.com` 을 apex 로 308(경로·쿼리 유지)하는 안. 분석 거부 저장값과 Clarity 쿠키가 한 출처에 모인다(선택 — 담당자 결정).
- 025 — §3.3 에 "알림 문구에 방문자 IP·User-Agent·쿼리·쿠키를 싣지 않는다 — 032 방침이 '운영 알림에 방문자 정보 없음' 이라고 적는다" 한 줄.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
