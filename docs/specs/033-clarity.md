# 033 — clarity

상태: DONE | 의존: **032 privacy 가 게시된 뒤에 켠다** — 이 PR 은 032 위에 쌓여(같은 줄기, 032 가 먼저 머지·게시된다) ID 를 넣어 머지한다(§3.1, ID 가 비어 있으면 아무것도 안 한다). 계약을 쓰는 스펙: 032 privacy(처리방침 파일·분석 동의 값·동의 전에 알릴 사항), 002 web-shell(URL 쿼리 상태), 022 landing(정적 랜딩), 027 observability(caddy 쿼리 키 삭제), 007 deploy(nginx 정적 규칙), 013·014(기록 탭 심볼 검색)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 방문자들이 랜딩과 대시보드에서 무엇을 보고 누르는지(탭 전환·표에서 기록 탭으로 넘어가기·스크롤·클릭)를 Microsoft Clarity 의 녹화·히트맵·태그로 본다. 서버 기록(027)은 들어온 주소와 머문 시간까지만 알고, 들어온 뒤의 조작은 요청을 만들지 않아 보지 못한다. **Clarity 는 동의한 방문자에게만 켠다(사람 결정 2026-10-01, 032 §3.3)**: 랜딩·대시보드에 화면을 막지 않는 동의 안내 띠를 띄우고, 수집·이용·Microsoft 제공·미국 이전 세 칸에 모두 동의해 저장한 방문자만 Clarity 스크립트를 받는다(동의는 032 §3.3 대로 셋으로 나눠 받는다 — 법 제22조 제1항). 정하지 않았거나 거부한 방문자, GPC 를 켠 방문자, 관리자·처리방침 페이지는 받지 않는다. 수집은 처리방침(032)이 게시된 뒤에만 켜진다.

## 2. 범위
- 만드는 것: `web/public/clarity.js`(설정 한 곳 — 안내 판 포함 + 동의 안내 띠 + 불러올지 판단), 랜딩·대시보드 HTML 의 그 파일 한 줄과 가림 해제 속성, `web/src/shared/` 의 Clarity 호출 두 개(태그·이벤트), 셸의 탭 태그·이벤트, nginx 캐시 규칙 한 줄, 계약 테스트 `server/tests/test_clarity.py`, 런북 `docs/runbooks/clarity.md`(Clarity 대시보드 설정·켜고 끄기·삭제 요청 처리, 사람용).
- 하지 않는 것:
  - 처리방침 문구·국외 이전 고지·Clarity 약관이 요구하는 고지·동의 관리(지금 상태·세 칸·[선택한 대로 저장]·[모두 거부]·[동의 철회]) — 032. Clarity Data Export API·토큰과 관리자 화면의 Clarity 요약 — 035·036(035 가 토큰 절차를 이 스펙의 런북 `clarity.md` 에 더한다).
  - 동의를 서버에 남기기 — 동의는 방문자 브라우저의 저장값뿐이다(새 개인정보를 만들지 않는다 — 입증 방법은 032 §7 사람 확인).
  - `identify`(방문자 식별) — 계정이 없고 쓸 곳이 없다.
  - 관리자·처리방침·404 페이지의 Clarity 와 띠, Google Fonts·jsDelivr 자체 호스팅, 기록 탭 차트(canvas) 녹화.
- 바꾸는 기존 것:
  1. 002·003 — 스프레드·갭·선선갭 검색어(`s.q`·`g.q`·`p.q`)를 URL 상태에서 뺀다(메모리만 — 기록 탭·입출금 레이더 검색과 같아진다). Clarity 는 전송할 때마다 그때의 페이지 주소를 통째로 싣는다 — 입력칸을 가려도 주소의 검색어는 그대로 간다.
  2. 002 — URL 쓰기를 둘로 나눈다(§3.7). Clarity 가 `history.replaceState` 를 덮어써서 주소가 바뀔 때마다 녹화를 끊고 DOM 전체를 다시 보낸다(스프레드 탭 gzip ≈110KB, 2026-09-28 실측).
  3. 002·013·014 — URL 의 `sym` 과 기록 탭 검색 확정 값을 심볼 형식으로 제한한다(§3.7). 입력한 글자가 가림 해제된 제목·주소·태그로 Clarity 에 가기 때문이다. 027 빚 "기록 탭 검색칸 값(URL sym)은 검증 없이 기록된다" 를 갚는다.
  4. 022 — 랜딩 `<head>` 에 파일 한 줄, `<body>` 에 가림 해제 속성, 바닥 바로 가기 nav 의 방침 링크 뒤에 '화면 분석 설정'(→ `/privacy#consent`, 같은 탭 — 글자 '석' 이 랜딩 글꼴 서브셋에 없어 022 의 글꼴 스크립트로 다시 자른다).
  5. 007 — nginx 가 `/clarity.js` 를 매번 재검증하게 한다.
  6. 002(코드만) — 대시보드 헤더의 방침 링크 옆에 '화면 분석 설정'(→ `/privacy#consent`, 새 탭 `rel="noopener"`, 방침 링크와 같은 모양). 띠는 한 번 고르면 다시 뜨지 않으므로 철회하러 가는 길을 랜딩·대시보드에 늘 둔다 — 철회가 동의보다 어렵지 않게(법 제38조 제4항).
- 담당: 033 은 사용자 담당이다. 002·003(팀원)·013·014(hereokay)는 코드만 바꾸고 스펙 문구는 **고치지 않는다**(CLAUDE.md §5) — §6 "담당자에게 제안" 을 PR 본문에 적는다. 007·022·027 은 이 PR 이 고친다.

## 3. 동작

### 3.1 설정 한 곳 — 켜고 끄기
- 설정은 `web/public/clarity.js` 머리의 세 값이다: Clarity 프로젝트 ID(공개 값 — 페이지 소스에 그대로 보인다), 켤 페이지 목록(`landing`·`app`), 지금 안내 판(032 §3.3 — `privacy.html` 스크립트의 판 값과 같다, 지금 `2026-10-01`). ID 가 빈 문자열이면 파일은 아무것도 하지 않는다 — 물을 도구가 없으니 띠도 띄우지 않는다. **이 PR 은 ID `yqqzmx15ps`(사용자 2026-10-01)로 머지한다** — 032 가 같은 줄기에서 먼저 게시되므로 따로 ID PR 을 두지 않는다. 그래서 이 PR 의 머지가 곧 켜기다 — 머지 전에 사람이 §3.10 대시보드 설정과 032 §7 법률 확인을 끝낸다.
- 켜는 순서(사람, 런북): 032 처리방침 게시 → Clarity 대시보드 설정(§3.10) → ID 가 든 PR 머지(이번은 이 PR — 032 머지 뒤). ID 가 비어 있지 않은데 `web/public/privacy.html`(032)이 없거나, 032 자리표시자 표식 `〔` 가 남았거나, `Clarity`·`kt.analytics`·`제28조의8 제1항 제1호`(동의 방식의 국외 이전 근거) 글자가 없으면 계약 테스트가 실패한다 — 게시 전에, 또는 동의 방식이 아닌 방침으로 켜는 실수를 CI 가 막는다.
- 끄기: ID 를 비우는 PR(배포 뒤 다음 페이지 로드부터). 대시보드만 끄려면 목록에서 `app` 을 뺀다. 파일 응답은 매번 재검증되므로(nginx `Cache-Control: no-cache`, `/app/` 아래는 이미 no-store — 007) 브라우저에 옛 설정이 남지 않는다. Clarity 프로젝트 삭제는 데이터가 전부 사라지고 되돌릴 수 없어 끄는 방법으로 쓰지 않는다 — 삭제 요청에만 쓴다(§3.10).
- 받는 자·항목·목적·보유 기간이 바뀌면 예전 동의로 켜지 않는다 — 그 PR 이 032 의 알릴 사항과 함께 안내 판을 올린다(이 파일·`privacy.html` 스크립트·방침의 보이는 판 글자 세 곳). 예전 판의 `granted` 는 '정하지 않음' 이라(§3.2) 띠가 다시 묻는다. 문장만 다듬는 변경은 판을 올리지 않는다.

### 3.2 불러오는 조건 — 하나라도 걸리면 부르지 않는다

| 확인 | 멈추는값 |
|---|---|
| ID | 빈값 |
| 호스트 | 그밖 |
| 페이지 | 목록밖 |
| GPC | `true` |
| 저장소 | 예외 |
| 동의값 | 그밖 |
| 판 | 다름 |

- 호스트는 www 없는 `kimptrack.com` 하나다. `www.kimptrack.com` 은 apex 로 301(022·023)이라 문서가 열리지 않지만 저장값이 출처마다 따로라 조건으로도 막는다. localhost·탄력 IP 직접 접속·`admin.kimptrack.com` 도 이 조건으로 빠진다.
- 페이지: 경로가 정확히 `/` 면 `landing`, `/app/` 로 시작하면 `app`. 처리방침·관리자·404 파일은 `clarity.js` 를 싣지 않는다(조건과 이중).
- GPC: `navigator.globalPrivacyControl === true` 면 부르지 않고 띠도 띄우지 않는다 — 값이 `granted` 여도. Clarity 도 GPC 면 시작하지 않지만(clarity-js 0.8.71) 스크립트를 받는 순간 방문자 IP 가 Microsoft 로 가므로 아예 받지 않는다. DNT 는 보지 않는다(폐기된 신호이고 Clarity 도 따르지 않는다 — Clarity FAQ).
- 동의값(032 §3.3 계약 복사): localStorage 키 둘 — `kt.analytics` 와 동의한 안내 판 `kt.analytics.v`. **`kt.analytics` 가 `granted` 이고 `kt.analytics.v` 가 지금 판(§3.1)과 같을 때만 부른다.** `denied`(하나라도 빠짐·거부·철회)·없음(아직 정하지 않음 — **기본 꺼짐**)·그 밖의 값·판이 다르거나 없는 `granted` 면 부르지 않는다. 저장소를 읽다 예외가 나면 부르지 않고 띠도 띄우지 않는다(고른 값을 저장할 수 없다). 값을 쓰는 곳은 032 방침의 동의 관리와 이 파일의 띠 둘이고, 둘 다 `granted`(판과 함께)·`denied` 만 쓴다.
- 부르지 않을 때(GPC·켜는 값 아님·저장소 예외)는 예전 방문에서 남은 Clarity 저장값을 지운다: 쿠키 `_clck`·`_clsk`(도메인 `.kimptrack.com` 과 호스트 전용 두 모양 모두), sessionStorage `_cltk`.
- 띠: 앞의 다섯 조건(ID·호스트·페이지·GPC·저장소)을 지나고 **'정하지 않음'**(없음·그 밖의 값·판이 다르거나 없는 `granted` — 032 와 같다)일 때만 띄운다(§3.3). 지금 판의 `granted`·`denied` 면 띠가 없다 — 다시 고르는 곳은 032 방침의 동의 관리다. `denied` 는 판이 바뀌어도 다시 묻지 않는다.

### 3.3 동의 안내 띠
- 어디·언제: 랜딩·대시보드 문서에서 §3.2 의 띠 조건이 맞을 때, `DOMContentLoaded` 뒤 한 번 넣는다. 고른 뒤(지금 판의 `granted`·`denied`)에는 그 브라우저에서 다시 뜨지 않는다(저장값이 지워지거나 — 브라우저 데이터 삭제·Safari 7일 — 그 밖의 값이 되거나, 안내 판이 바뀐 `granted` 면 다시 뜬다). 고른 뒤 바꾸는 길은 랜딩 바닥·대시보드 헤더의 '화면 분석 설정'(§2)이다.
- 모양: 화면을 막지 않는 아래 고정 띠다 — 모달이 아니고 배경을 가리지 않으며 띠 밖의 스크롤·클릭·키보드는 페이지에 그대로 간다. 넓은 화면에서는 가운데 최대 720px 카드, 좁은 화면에서는 좌우 16px 여백(390px 에서 가로 스크롤 없음). **처음엔 접힌 짧은 띠다**(설계 세션 결정 2026-10-01 — 랜딩 세션 요청: 모바일 높이를 작게): 머리 문장(나이 문장을 이어 붙인다), 세 칸(체크 상자·칸 제목, 오른쪽에 '내용 보기'), '처리방침에서 자세히 보기' 링크와 '저장 규칙', 버튼 줄. 칸마다 '내용 보기'(`<details>`)를 누르면 그 동의의 알릴 사항 전문이 펼쳐지고 '저장 규칙'은 셋 모두 규칙 문장을 펼친다 — 알릴 사항은 동의 전에 한 번 눌러 볼 수 있으면 된다(전문 보기 방식). 접힌 띠는 375×812 에서 화면 절반 안이고 띠 안 스크롤이 없다. 펼치면 띠 높이는 화면 높이의 절반까지 — 넘치는 글은 띠 안에서 스크롤하고 버튼 줄은 띠 아래에 늘 보인다(넓은 화면·모바일 폭 같다). 고정 위치라 랜딩 카드(min-height 480/400px)·대시보드 표의 배치를 바꾸지 않는다(레이아웃 이동 없음). 띠 바깥 요소에 `data-nosnippet` — 띠 글이 검색 결과 요약에 쓰이지 않게(022). 색은 랜딩 토큰 복사(032 방침의 동의 상자와 같은 surface·accent), 글꼴은 시스템 글꼴(032 방침 페이지와 같은 목록) — 랜딩 자체 서브셋 글꼴(022)에 없는 글자가 섞이지 않고 대시보드의 Google Fonts 를 기다리지 않게. 애니메이션 없음.
- 보이는 것(위에서 아래로) — 032 방침 동의 상자와 **같은 글자**다(계약 테스트가 `privacy.html` 에서 읽어 맞춘다, §4):
  1. 같은 문장 셋(머리와 나이는 맨 위 한 문단, 셋 모두 규칙은 '저장 규칙' 안): 머리 "랜딩과 대시보드의 화면 이용 기록(클릭·스크롤 등)을 Microsoft Clarity(미국)로 보내 서비스 개선에 써도 될까요? Microsoft는 이 기록을 광고 등 자기 목적에도 씁니다. 동의하지 않아도 모든 기능을 그대로 씁니다." / 셋 모두 규칙 "세 가지에 모두 동의하고 [선택한 대로 저장]을 누른 경우에만 분석합니다 — Clarity는 셋이 다 있어야 돌아가므로 하나라도 빠지면 거부로 저장합니다." / 나이 "만 14세 미만은 동의하지 마세요."(나이는 확인하지 않는다 — 032 §7 사람 확인).
  2. 세 칸 — 칸마다 처음엔 빈 체크 상자와 '(선택) …에 동의합니다' 글자, 그 칸의 '내용 보기'를 펼치면 그 동의의 알릴 사항(032 §3.3): (가) 수집·이용 — 항목·목적·보유 기간·거부 권리·불이익 (나) Microsoft 제공 — 받는 자·받는 자의 목적·항목·받는 자의 보유 기간·거부 권리·불이익 (다) 미국 이전 — 받는 자·연락처·국가·시기·방법·항목·목적·보유 기간·거부 방법·절차·효과. 칸은 다른 절을 가리키지 않고 그 자리에서 다 읽힌다 — 랜딩·대시보드에는 방침의 절이 없다. 거부 칸은 철회하는 곳(처리방침의 '화면 분석 동의 관리'(`/privacy#consent`)의 [동의 철회] — 법 제38조 제4항)을 적는다. 중요한 내용((가) 보유 기간, (나) 받는 자·받는 자의 목적·받는 자의 보유 기간, (다) 받는 자·연락처와 목적·보유 기간 — 법 제22조 제2항·시행령 제17조 제3항)은 032 와 같은 표시로 그린다 — 다른 내용보다 20% 크게(1.2em)·굵게·밑줄(032 §3.3, 고시 요건은 032 §7 사람 확인).
  3. "처리방침에서 자세히 보기" 링크 `/privacy#consent`(세 칸 아래 줄, '저장 규칙' 왼쪽). 띠 안의 모든 링크(이 링크와 칸 안 링크 — 거부 칸 셋의 `/privacy#consent`, (나)의 Microsoft 개인정보처리방침, (다)의 Microsoft 개인정보 문의)는 대시보드에서 새 탭 `target="_blank"` `rel="noopener"`, 랜딩에서 같은 탭이다 — 대시보드의 열어 둔 WebSocket 을 끊지 않게.
  4. 버튼 [선택한 대로 저장]·[모두 거부] — 같은 너비·높이·모양(같은 칸 너비 — 032 동의 관리 버튼과 같은 해석, 둘 다 외곽선, 어느 쪽도 강조하지 않는다). 닫기(×)는 두지 않는다 — 닫기는 '정하지 않음' 과 같아 다음 페이지에 또 뜨고, 고르지 않고 두어도 화면을 막지 않는다.
  - 이 띠는 화면 분석 동의 하나만 받는다 — 다른 동의·안내와 묶지 않는다.
- 누르면:
  - [선택한 대로 저장]: 세 칸이 모두 체크면 `kt.analytics.v` 에 지금 판을 먼저 쓰고(다른 탭이 `granted` 를 볼 때 판이 이미 맞게) `kt.analytics` 에 `granted` 를 쓴 뒤 띠를 지우고 그 자리에서 §3.4 대로 부른다. 쓰기가 예외면(판이든 값이든) 부르지 않고 띠만 지운다(동의를 저장할 수 없으면 이어 갈 수 없다). 하나라도 빠지면 [모두 거부]와 같다.
  - [모두 거부]: `denied` 를 쓰고 판을 지운 뒤 띠를 지운다. 아무것도 부르지 않는다(쓰기 예외여도 띠만 지운다).
- 접근성: 띠는 `role="region"`·`aria-label="화면 분석 동의"` 이고 `<body>` 의 첫 자식으로 넣는다 — 화면에서는 아래지만 키보드·스크린 리더는 페이지보다 먼저 만난다. 초점을 빼앗지 않는다(자동 초점·초점 가둠 없음). 체크 상자는 `<input type="checkbox">` 와 `<label>`, 버튼은 `<button>` — Tab·Space·Enter 로 다 된다. 띠 안 스크롤 영역은 키보드로도 스크롤된다(`tabindex="0"`). 글자 15px 이상·대비 WCAG AA, 버튼 높이 44px 이상, 체크 상자 누르는 곳 24px 이상. 띠를 지우면 초점은 문서 처음으로 간다(브라우저 기본).
- 대시보드 부하: 띠는 `#root` 밖 형제 요소 하나다 — React 트리를 다시 그리지 않고 표 재정렬(스프레드 탭 초당 ≈130행)을 막거나 늦추지 않는다. 타이머·관찰자(observer)·애니메이션이 없고 고정 위치라 표 배치를 바꾸지 않는다(본문에 여백을 더하지 않는다 — 가려진 맨 아래 줄은 스크롤로 본다).
- 외부 자원: 띠는 `clarity.js` 안의 DOM 과 문서에 넣는 `<style>` 하나로만 그린다 — 글꼴·그림·외부 CSS 없음. 띠가 만드는 요청은 0 이다(링크는 누를 때만).

### 3.4 불러오는 방법
- `clarity.js` 는 두 HTML 의 `<head>` 에 `defer` 로 한 번 실리고, 빌드된 `dist/index.html` 에서 앱 모듈 스크립트보다 **앞**이다(둘 다 문서 순서대로 실행된다). 판단은 파일이 실행될 때 한 번 하고, 띠의 [선택한 대로 저장](세 칸 모두)·다른 탭의 동의·뒤로 가기 캐시 복원(§3.5) 때 다시 한다. 한 문서에서 두 번 부르지 않는다.
- 부를 때는 곧바로 Clarity 표준 대기열 함수 `window.clarity`(태그가 오기 전 호출을 모아 둔다)를 만들고, 첫 호출로 `consentv2` 를 두 키를 다 넣어 부른다 — `ad_Storage: "denied"`, `analytics_Storage: "granted"`. `analytics` 의 granted 는 방문자가 띠나 방침에서 세 칸에 모두 체크해 저장한 동의다 — EEA·영국·스위스에 Microsoft 가 요구하는 유효한 동의 신호(Consent API v2 문서, 2025-10-31 부터)이므로 지역으로 나누지 않는다. 광고 저장은 동의한 방문자에게도 계속 거부한다. 옛 API `consent` 는 두 값에 같은 상태를 주고 폐기 예정이라(Clarity 문서), 인자 없는 `consentv2` 는 판마다 기본값이 달라(2026-09-28 관측은 둘 다 granted, 0.8.71 코드는 둘 다 denied) 쓰지 않는다. 광고 거부라 Microsoft 광고 쿠키 동기화 요청(`c.clarity.ms/c.gif`)이 없고 1차 쿠키 `_clck`(1년)·`_clsk`(1일)만 `.kimptrack.com` 에 생긴다(2026-09-28 실측, clarity-js 0.8.71 코드).
- 태그 스크립트 `https://www.clarity.ms/tag/<ID>`(async): 파일이 실행될 때 이미 켜는 값(지금 판의 `granted`)이면 `DOMContentLoaded` 뒤에 넣는다 — 대시보드 셸의 첫 URL 정리(§3.7)가 끝난 뒤 Clarity 가 첫 주소를 읽고, 첫 화면 그리기와 겹치지 않게. 띠의 저장·다른 탭 동의·캐시 복원으로 부를 때는 문서가 이미 그려졌으니 곧바로 넣고, 대기열을 만든 직후 `window` 에 이벤트 `kt:clarity` 를 한 번 보낸다(셸이 지금 화면의 태그를 두게 — §3.8). 대기열이 태그보다 먼저 생기므로 셸이 태그보다 먼저 부른 태그·이벤트도 순서대로 실린다.
- 파일은 예외를 밖으로 던지지 않는다 — 실패하면 Clarity·띠만 없고 페이지는 그대로다. 태그가 막히면(광고 차단) 대기열 함수가 호출을 쌓기만 한다(탭 전환 수만큼 — 무시할 크기).

### 3.5 다른 탭 반영
- §3.2 의 ID·호스트·페이지를 지난 문서는 `storage` 이벤트(키 `kt.analytics`·`kt.analytics.v` 또는 저장소 전체 비우기)를 듣는다. 같은 출처(`kimptrack.com`) 탭끼리만 간다.
  - Clarity 를 부른 문서에서 켜는 값(지금 판의 `granted`)이 아니게 되면(032 방침의 [동의 철회]·[모두 거부] 등) 곧바로 한 번 새로고침한다 — 보이지 않는 탭도 곧바로. 새 문서는 동의가 없어 부르지 않고 남은 저장값을 지운다(§3.2). 숨은 대시보드도 표가 매초 바뀌어 Clarity 가 DOM 변화를 보내므로 다시 볼 때까지 미루지 않는다.
  - 부르지 않은 문서에서 켜는 값이 되면(다른 탭의 띠·방침의 [선택한 대로 저장]) 띠를 지우고 그 자리에서 §3.4 대로 부른다. GPC 면 그대로 둔다.
  - 띠가 떠 있는데 값이 `denied` 가 되면 띠만 지운다.
- 뒤로 가기 캐시(bfcache)에서 돌아온 문서(`pageshow` 의 `persisted`)도 같은 규칙으로 다시 본다 — 캐시에 든 동안 바뀐 값의 `storage` 이벤트는 오지 않거나(Safari) 복원 뒤에 늦게 온다(크롬). 같은 탭의 흐름(랜딩 → 바닥 '화면 분석 설정' → 방침 [동의 철회] → 뒤로 가기)이 이것이다. Clarity 는 복원 때 스스로 녹화를 다시 시작하므로(clarity-js `pageshow` 처리기, non-capture), Clarity 를 부른 문서가 켜는 값이 아니면 같은 이벤트의 capture 단계에서 그 처리기를 막고(`stopImmediatePropagation`) 곧바로 한 번 새로고침한다. 부르지 않은 문서는 켜는 값이면 띠를 지우고 부르고, 고른 값(`denied`)·GPC·저장소 예외면 띠만 지운다.
- 032 방침이 같은 문장을 적는다: "동의·철회는 그 뒤로 여는 페이지에 곧바로 따릅니다. 같은 브라우저에 이미 열린 `kimptrack.com` 랜딩·대시보드 탭에도 곧바로 반영됩니다 — 철회하면 그 탭이 한 번 새로고침되어 분석을 멈추고, 동의하면 그 탭에서도 분석을 시작합니다."

### 3.6 가림
- Clarity 가림 모드는 Balanced(기본 — 숫자·이메일을 가린다)로 둔다. 대시보드 `#root`(`web/index.html`)와 랜딩 `<body>` 에 `data-clarity-unmask="true"` 를 달아 시세·김프 숫자가 녹화에 보이게 한다 — 두 화면의 숫자는 공개 시세다. 입력칸·드롭다운은 모든 모드에서 가려지고 풀 수 없다(Clarity 문서). 새 페이지는 속성을 달지 않는 한 가림이 기본이다.
- 가림 해제 영역 안에 방문자가 입력한 글자를 다시 쓰지 않는다. 검색어는 표를 거르기만 하고 화면에 되쓰지 않는다(지금 그렇다). 기록 탭 제목의 선택 심볼은 §3.7 형식만 들어간다.

### 3.7 URL — 검색어와 녹화 조각
Clarity 동작(clarity-js 0.8.71, 2026-09-25 커밋 코드를 2026-10-01 에 읽음): 태그가 `history.pushState`·`replaceState` 를 **인스턴스 속성**으로 덮어쓰고, 호출 뒤 주소(해시 제외)가 바뀌었으면 녹화를 멈췄다가 250ms 뒤 새 페이지로 다시 시작해 DOM 전체를 다시 보낸다. 멈춘 동안 받은 API 호출은 모았다가 새 페이지에 싣는다. 전송마다 그때의 `location.href`(해시 포함, 2,048자까지)를 싣는다. Clarity 의 URL 매개변수 가림은 지원 요청으로만 되고 페이지 주소에만 적용된다(참조·클릭 주소는 안 가린다 — Clarity FAQ).
- 검색어 세 키 `s.q`·`g.q`·`p.q` 는 URL 에 싣지 않는다 — 새로고침·공유 링크에서 검색어는 복원되지 않는다. 셸은 첫 렌더 전(앱 모듈이 실행될 때) URL 에 남은 세 키를 지우고 값은 버린다(옛 링크). 027 caddy 의 세 키 삭제는 옛 링크 때문에 그대로 둔다.
- `sym` 은 영문 대문자·숫자 1~20자만 URL 에서 읽고 쓴다 — 밖이면 기본값(BTC)이고 URL 에서 빠진다(002 의 "허용 밖 값은 기본값" 과 같다). 기록 탭 검색은 Enter 때 대문자로 바꾼 값이 이 형식일 때만 선택한다 — 아니면 선택하지 않고 입력칸을 그대로 둔다. 표 클릭·랜딩 링크의 심볼은 거래소 심볼이라 이 형식이다.
- URL 쓰기는 둘로 나눈다. `tab` 키를 쓸 때만 `window.history.replaceState`(Clarity 가 덮어쓴 것)를 부른다 — 탭을 바꾸면 Clarity 새 페이지가 되어 탭 주소마다 히트맵이 따로 생기고 전송 한도(§3.9)도 새로 센다. 그 밖의 쓰기(탭 접두어 키·`sym`·`dropParams`·첫 정리)는 원래 함수 `History.prototype.replaceState` 를 `window.history` 에 걸어 부른다 — Clarity 가 모르므로 필터를 바꿔도 녹화가 이어지고, 다음 전송 주소에 새 필터가 실린다. Clarity 가 없으면 둘은 같은 함수다.
- 이 우회는 Clarity 가 인스턴스 속성을 덮어쓰는 구현에 기댄다. 바뀌면 필터마다 녹화가 조각나지만 검색어가 URL 에 없으므로 개인정보 문제는 아니다 — §4 배포 뒤 확인, status 빚.

### 3.8 태그·이벤트
- `shared/` 에 두 함수를 둔다 — 태그 두기(키, 값)와 이벤트 남기기(이름). `window.clarity` 가 함수가 아니면 아무것도 안 한다(동의하지 않은 방문자). 키·이름은 아래 고정 토큰만 쓰고 방문자 입력은 싣지 않는다.

| 이름 | 종류 |
|---|---|
| `tab` | 태그 |
| `sym` | 태그 |
| `tab_<id>` | 이벤트 |
| `pivot_history` | 이벤트 |

- `tab`: 값은 탭 id(`spread`·`history`·`gap`·`pp`·`health`·`flow`). 첫 화면에서, 그리고 탭이 바뀐 뒤마다 `tab` URL 을 쓴 **다음에** 둔다 — Clarity 태그는 페이지마다 새로 시작하므로 새 페이지에 실려야 한다. 문서 중간에 불렸다는 이벤트 `kt:clarity`(§3.4)를 받으면 그때 보이는 탭의 `tab`(기록 탭이면 `sym` 도)을 한 번 둔다.
- `sym`: 기록 탭이 보이는 동안의 선택 심볼(§3.7 형식). 기록 탭에 들어갈 때와 심볼이 바뀔 때.
- `tab_<id>`: 탭 단추로 바꿀 때마다. 첫 화면에는 남기지 않는다. `pivot_history`: 스프레드 표 행을 눌러 기록 탭으로 갈 때(이때는 `tab_history` 대신).
- 한도(Clarity 문서): 키·값 각각 255자 미만, 페이지당 태그 128개 — 위 규칙이면 페이지당 몇 개다. 랜딩은 태그·이벤트가 없다 — 페이지가 하나이고 링크 클릭은 Clarity 가 클릭 주소로 남긴다.

### 3.9 받아들이는 한계
- 대시보드 부하: 스프레드 탭은 DOM ≈8,800요소에 초당 ≈130행이 재정렬돼 Clarity 전송이 ≈1KB/s(시간당 ≈3.4MB)다, 스크립트 25KB(2026-09-28 실측). 방문자 브라우저의 CPU 는 §4 에서 재고, 스프레드 탭 60초 스크립트 시간이 Clarity 를 켠 쪽에서 1.5배를 넘으면 목록에서 `app` 을 뺀다(기준값은 **사람 확인**).
- 녹화 길이(clarity-js 0.8.71): Clarity 페이지 하나가 전송 128회(스프레드 탭 ≈57분 실측) 또는 2시간에 닿으면 멈추고, 그 문서는 새로고침 전까지 다시 시작하지 않는다(탭을 바꿔도). 세션당 페이지 128개. 대시보드를 종일 띄워 두는 사용은 앞 1시간 안팎만 남는다.
- 기록 탭 차트는 canvas 라 녹화에 안 보인다(Clarity 문서). 스크롤 맵은 문서 스크롤 기준이라 탭 본문 안에서 스크롤하는 대시보드에서는 쓸모없고 랜딩만 맞다. 표가 매초 재정렬돼 클릭 히트맵은 행 위치로 모인다 — 탭·피벗은 §3.8 로 본다.
- 빠지는 방문자: 동의하지 않았거나 아직 정하지 않은 방문자(기본 꺼짐 — 가장 크다)·광고 차단(EasyPrivacy·AdGuard 가 `clarity.ms` 를 막는다)·GPC·www. Clarity 의 숫자는 하한이고(동의한 방문자만), 총량은 caddy 기록(027)과 WebSocket 접속 수로 본다. 첫 페이지에서 고르기 전의 조작은 녹화되지 않는다.
- 보존·삭제(Clarity 문서, 2026-10-01 확인): 녹화 재생 데이터 30일, 클릭 데이터와 즐겨찾기·라벨 붙인 녹화 9개월. 한 방문자 것만 지울 수 없다(프로젝트를 지우는 것뿐). 처리방침 문구는 032.

### 3.10 Clarity 대시보드 설정 (사람 — 런북 `clarity.md`)
- Settings → Setup → Advanced: **Cookies 끔**(동의 모드 — 신호 전에는 쿠키가 없고, 사이트가 보낸 `analytics_Storage: granted` 로 1차 쿠키가 생겨 탭 전환 페이지들이 한 세션으로 묶인다), **Bot detection 켬**.
- Masking: Balanced, 요소 규칙 없음(가림은 사이트 속성으로만 정한다 — 설정이 레포에 남게).
- IP blocking: 운영자 고정 IPv4(CIDR 가능, IPv6·모바일·VPN 은 안 된다 — Clarity 문서). 목록은 **사람 확인**.
- 팀: 녹화를 보는 사람은 개인정보를 다루는 사람이다 — 관리자·구성원 범위는 **사람 확인**.
- 선택: Clarity 지원 메일로 URL 매개변수 `s.q`·`g.q`·`p.q` 가림을 요청한다(옛 링크 대비, 페이지 주소에만 적용된다).
- GA·GTM 연동은 켜지 않는다. Data Export API 토큰은 035 몫 — 035 가 같은 런북에 토큰 절을 더한다(Clarity 운영을 한 런북에).
- **삭제 요청 — 프로젝트 기록 통째 삭제**(런북의 한 절, 032 방침 3절·8절의 약속 — 법 제37조 제3항·제36조): 철회한 방문자가 이미 보낸 기록을 바로 지우기를 원하거나 Clarity 기록 삭제를 요청하면, Clarity 는 한 사람분만 지울 수 없으므로 운영자가 — (1) 받은 메일에 접수를 답한다. (2) 새 Clarity 프로젝트를 만들어 이 절의 설정(쿠키·봇·가림·IP 차단·팀)을 그대로 한다. (3) `clarity.js` 의 ID 를 새 ID 로 바꾸는 PR 을 머지·배포한다(안내 판은 올리지 않는다 — 받는 자·항목·목적·보유 기간이 같다). (4) 배포 뒤 옛 프로젝트를 지운다 — 그 프로젝트의 모든 방문자 녹화·히트맵이 함께 사라지고 되돌릴 수 없다(남길 숫자는 개인정보 없는 집계만 지우기 전에 따로 적는다). (5) 035 의 Data Export 토큰이 있으면 새 프로젝트에서 다시 만든다(035 절). (6) 요청자에게 결과를 지체 없이 알린다 — Microsoft 가 자기 목적에 쓰는 정보는 Microsoft 개인정보 문의로 따로 요청할 수 있다고 함께 적는다. 주고받은 메일은 문의 메일로 보관한다(032 — 답변을 마친 날부터 1년). 새 프로젝트를 먼저 켜고 옛 것을 지우는 순서라 그 사이 동의한 방문자의 기록이 갈 곳 없는 시간이 없다.
- 런북은 각 항목의 확인 방법(§4 배포 뒤)과 되돌리기(ID 비우기 PR)를 함께 적는다.

### 3.11 엣지
- 쿠키 도메인: Clarity 는 `.kimptrack.com` 에 쓰므로 `admin.kimptrack.com`·공개 `/api` 요청에도 실려 간다. 관리자 nginx 는 Cookie 를 비우고(029), caddy 기록은 헤더를 지우며(027), 백엔드는 쿠키를 읽지 않는다.
- 공개 페이지에는 지금 CSP 가 없다. 나중에 더하면 띠의 `<style>`(`style-src`)과 `www.clarity.ms`·`scripts.clarity.ms`·`*.clarity.ms`(전송)를 열어야 한다. 관리자 CSP(`default-src 'self'`, 029)·방침 CSP(032)는 그대로 — 거기서는 부르지 않는다.
- 배포 중 옛 `clarity.js` 와 새 HTML 이 섞여도 다음 로드부터 맞는다. canary(027)·uptime 은 스크립트를 실행하지 않아 Clarity 에도 띠에도 안 잡힌다.
- `/?utm_*` 은 022 의 301 로 `/app/?utm_*` 가 된다 — Clarity 는 대시보드 주소에서 유입 경로(utm)를 읽는다. utm 키는 URL 상태가 아니라 그대로 남는다.
- 띠에서 한두 칸만 체크하고 저장: 거부(`denied`)로 저장하고 띠를 지운다 — Clarity 는 셋이 다 있어야 돈다. 다시 고르는 곳은 032 방침의 동의 관리다.
- 자바스크립트가 꺼진 브라우저: 파일이 돌지 않아 띠도 Clarity 도 없다.

## 4. 검증
**PR 안 — 실행 세션 완료 조건**
- server `ruff check . && pytest -q` — `tests/test_clarity.py` 가 파일을 읽어 단언한다:
  - 설정: `clarity.js` 의 ID 는 빈 문자열이거나 영숫자 1~32자이고 페이지 목록은 `landing`·`app` 안에서만. ID 가 비어 있지 않으면 `web/public/privacy.html` 이 있고 `〔` 가 없고 `Clarity`·`kt.analytics`·`제28조의8 제1항 제1호` 가 있다.
  - 있어야 하는 글자: `consentv2`·`ad_Storage`·`analytics_Storage`·`"granted"`·`"denied"`·`globalPrivacyControl`·`kt.analytics`·`kt.analytics.v`·`_clck`·`_clsk`·`_cltk`·`DOMContentLoaded`·`https://www.clarity.ms/tag/`·`storage`·`location.reload`·`region`·`aria-label`·`checkbox`·`label`·`/privacy#consent`·`_blank`·`noopener`·`kt:clarity`·`pageshow`·`persisted`·`stopImmediatePropagation`·`선택한 대로 저장`·`모두 거부`(`web/src` 에도 `kt:clarity`), 띠 스타일의 강조 표시(1.2em·굵게·밑줄). 접는 장치 `<details>`·`<summary>`·`내용 보기`·`저장 규칙`, 띠 바깥 요소의 `data-nosnippet`. 없어야 하는 글자: `[허용]`.
  - 032 와 같은 글자(`privacy.html` 에서 읽는다): `clarity.js` 의 안내 판 값이 `privacy.html` 스크립트의 판 값과 같다, 032 동의 상자의 세 칸의 체크 글자와 알릴 사항(`<dt>`·`<dd>` 원문 HTML — 중요한 내용 표시 `<strong class="key">` 가 같은 칸에 있다)이 `clarity.js` 띠에 그대로 있고, 같은 문장 셋은 띠의 자리에 있다(머리와 나이는 맨 위 한 문단, 셋 모두 규칙은 '저장 규칙' 안 — 태그를 떼고 비교), 띠의 거부 칸들에 `/privacy#consent` 와 `동의 철회` 가 있고, `privacy.html` 에 `id="consent"` 가 있다.
  - 띠가 만들지 않는 것: `setInterval`·`setTimeout`·`requestAnimationFrame`·`Observer` 가 없다(§3.3). `resolvedOptions`·`Europe/`·`'off'`·`"off"`·`'consent'` 호출·`identify`·`www.kimptrack.com` 이 없다. `http(s)://` 리터럴은 태그 주소·Microsoft 처리방침·Microsoft 개인정보 문의 셋뿐(띠는 외부 자원을 부르지 않는다).
  - 싣는 곳: `web/index.html`·`web/public/landing.html` 이 파일을 `defer` 로 한 번씩 싣고 `#root`·랜딩 `<body>` 에 `data-clarity-unmask="true"`. 랜딩 바닥 nav 와 `web/src/App.tsx` 에 '화면 분석 설정' → `/privacy#consent`(대시보드는 `noopener`), 랜딩 글자가 글꼴 서브셋에 모두 있다(022 의 `test_landing_seo.py`). `web/admin/*`·`privacy.html`·`404.html` 은 `clarity.js` 를 싣지 않고(`src`) `clarity.ms` 가 없다 — 방침 동의 스크립트 주석이 판 계약으로 파일 이름을 말하는 것은 괜찮다(관리자 스크립트는 029 단언대로 `admin.js` 하나 — 036 의 Clarity 칸·`/svc/api/admin/clarity`·콘솔 링크는 걸리지 않는다).
  - 그 밖: `nginx.conf` 에 `location = /clarity.js` 와 `no-cache`. `web/src` 에 `useUrlState('s.q'`·`'g.q'`·`'p.q'` 가 없고 `urlState.ts` 에 `History.prototype.replaceState` 가 있다.
- web `npm run lint && npm run build`, `node --check web/public/clarity.js`(oxlint 대상 밖). 빌드된 `dist/index.html` 에서 `clarity.js` 가 앱 모듈보다 앞, `dist/clarity.js` 있음.
- 로컬 브라우저 — docker 통합 기동(:8080)에 헤드리스 크롬 `--host-resolver-rules` 로 `kimptrack.com`·`www.kimptrack.com` 을 로컬에, `*.clarity.ms` 를 닫힌 포트에 묶는다(요청은 시도만 되고 나가지 않는다). 크롬 인자를 줄 수 없는 브라우저면 커밋하지 않는 시험 사본의 `clarity.js` 에서 호스트를 `kimptrack.localhost`(크롬이 루프백으로 푼다 — `www.` 도), 태그 주소를 닫힌 포트로 바꿔 같은 항목을 본다(dev-setup). 시험 ID 는 커밋하지 않는 로컬 사본에만 넣는다. 랜딩·대시보드 각각:
  1. 값 없음: 띠가 `<body>` 첫 자식으로 보이고(접힌 띠 — 칸은 빈 칸, '내용 보기'를 펼치면 알릴 사항 전문이 보이고 중요한 내용은 크게·굵게·밑줄, 버튼 둘 같은 너비·높이), 띠 안의 `<a>` 가 대시보드에서는 모두 `target="_blank"`·`rel="noopener"`(칸 안 링크 포함)이고 랜딩에서는 `target` 이 없다, `clarity.ms` 요청 0, `window.clarity` 없음. 첫 Tab 초점이 띠 안, Space 로 칸·Enter 로 버튼이 된다. 접힌 띠는 화면 높이의 절반 이하이고 띠 안 스크롤이 없으며(375×812), 펼치면 절반까지에서 안에서 스크롤, 버튼 줄은 늘 보인다(1440·375). 375px 가로 스크롤 0.
  2. 세 칸 체크하고 [선택한 대로 저장] → `kt.analytics=granted`·`kt.analytics.v=<지금 판>`·띠 없음·`tag/<시험ID>` 1회·대기열 첫 항목 `consentv2` 와 두 값 그대로(대시보드는 이어서 `set tab <id>`). 새로고침 → 띠 없음, `DOMContentLoaded` 전 요청 0 뒤 태그 1회. 두 칸만 체크하고 저장 → `denied`·띠 없음·요청 0.
  3. [모두 거부] → `denied`·판 없음·띠 없음·요청 0, 새로고침 뒤에도 띠 없음·요청 0. 예전 판의 `granted`(`kt.analytics.v=2026-09-01`) → 띠가 뜨고 요청 0.
  4. GPC 주입(값 없음·`granted` 각각)·localStorage 예외 주입 → 띠 없음·요청 0, 미리 심은 `_clck`·`_clsk`(두 도메인 모양)·`_cltk` 가 지워진다. 그 밖의 값(`off`) → 띠가 뜨고 요청 0, 남은 저장값은 같이 지워진다.
  5. 두 탭: 탭 A `/app/` 가 `granted` 로 Clarity 를 부른 뒤 탭 B `/privacy` 에서 [동의 철회] → A 가 (뒤에 있어도) 곧바로 한 번 새로고침되고 그 뒤 `clarity.ms` 요청 0·띠 없음. A 가 값 없음(띠)일 때 B 에서 세 칸 [선택한 대로 저장] → A 의 띠가 사라지고 태그 1회. 같은 탭: 랜딩(`granted`) → 바닥 '화면 분석 설정' → [동의 철회] → 뒤로 가기 → 캐시에서 복원돼도 Clarity 의 재시작 처리기가 돌지 않고 한 번 새로고침, 그 뒤 `window.clarity` 없음.
  6. 호스트 `www.kimptrack.com`·`localhost:8080` → 띠 없음·요청 0.
  7. 대시보드 부하(결정적 확인): 띠가 떠 있는 동안 스프레드 표 갱신이 이어지고(행 순서·값이 계속 바뀐다), 띠를 넣고 지워도 `#root` 가 다시 마운트되지 않는다(검색칸 입력값 그대로), 띠를 넣고 지우는 동안 `clarity.js` 가 타이머·관찰자를 만들지 않는다(`setTimeout`·`setInterval`·`requestAnimationFrame`·`MutationObserver` 를 감싸 세면 0). 로컬 망이 거래소를 막아 표가 멈추면 이 항목은 EC2 에서 돈다. 숫자 비교는 배포 뒤 사람 항목이다.
  8. `/app/?s.q=btc&s.view=rev` → `DOMContentLoaded` 때 `location.search` 가 `?s.view=rev`, 검색칸은 비어 있고 검색을 입력해도 URL 이 그대로.
  9. (`granted`) 첫 스크립트로 인스턴스 `history.replaceState` 를 감싸 센다: 필터 세 번·기록 탭 심볼 변경 → 0회, 탭 단추 두 번 → 2회, 표 행 클릭 → 1회. 바뀔 때마다 대기열에 `set tab <id>` 뒤 `event tab_<id>`(행 클릭은 `pivot_history`).
  10. `?tab=history&sym=%ED%99%8D` → BTC 이고 URL 에 `sym` 이 없다. 기록 탭 검색 `홍길동` Enter → 선택 안 됨·입력칸 그대로, `eth` Enter → ETH·`sym=ETH`·(`granted` 면) 대기열에 `set sym ETH`.
  11. 랜딩(`granted`): `<body>` 속성, 대기열에 `consentv2` 하나뿐. 002 §4 의 URL 복원 항목(14·18)이 검색어 부분을 빼고 그대로 통과.
- `curl -sI localhost:8080/clarity.js` → `Cache-Control: no-cache`.

**배포·런북 뒤 — 사람**
- 032 게시 확인 → §3.10 설정 → ID PR 머지·배포.
- 실제 크롬(`https://kimptrack.com`, 새 프로필): 띠가 뜨고 `clarity.ms` 요청 0 → 세 칸 [선택한 대로 저장] 뒤 `www.clarity.ms/tag`·`scripts.clarity.ms`·`*.clarity.ms/collect` 요청이 있고 `c.clarity.ms` 는 0건, 쿠키는 `_clck`·`_clsk` 둘뿐(도메인 `.kimptrack.com`), 콘솔 `clarity('metadata', cb, false, true, true)` 의 동의 상태가 ad DENIED·analytics GRANTED.
- 2시간 안 Clarity 녹화: 숫자가 보이고 입력칸은 가려짐, 탭 두 번 → 같은 세션에 페이지 셋과 태그 `tab`, 필터를 여러 번 바꿔도 페이지가 늘지 않음, 이벤트 보임.
- GPC 브라우저(Brave)·[모두 거부]·`www` → 띠(GPC·거부는 없음)와 `clarity.ms` 요청 0. VoiceOver 로 띠의 세 칸·알릴 사항을 읽고 칸을 체크하고 버튼을 누를 수 있다. 실제 휴대폰(390 안팎)에서 띠가 화면 절반을 넘지 않고 띠 밖 페이지를 스크롤·누를 수 있다.
- 같은 기기에서 스프레드 탭 60초 Performance 기록(동의·거부)의 스크립트 시간과 1시간 전송량 → §7 에 적고 §3.9 기준으로 `app` 유지 여부를 사람이 정한다. 띠 있음·없음(값 없음·`denied`)의 스프레드 탭 30초 스크립트 시간도 각 3회 기록의 중앙값으로 비교해 적는다(5% 안이 목표).

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 자동 (2026-10-01)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
#   All checks passed! / 273 files already formatted / 1176 passed (test_clarity 15 — 글자·032 와 같은 글자·싣는 곳·nginx·URL 규칙 + node 로 판단·띠 버튼·다른 탭)
cd web && npm run lint && npm run build && node --check public/clarity.js
#   oxlint 종료 0 / tsc -b && vite build ✓ / 문법 통과
grep -n -E 'clarity.js|type="module"' dist/index.html; ls dist/clarity.js
#   6: <script defer src="/app/clarity.js"> · 25: 앱 모듈 — clarity.js 가 앞 / dist/clarity.js 있음
# 로컬 Docker — nginx:1.27-alpine 둘(127.0.0.1 만 게시, dist·nginx.conf 를 docker cp, 끝난 뒤 지움) + 가짜 api 127.0.0.1:18035(/ws/spreads snapshot·매초 delta)
#   ml033-web      :18033 dist 그대로 / ml033-web-test :18034 시험 사본 — HOST → kimptrack.localhost, 태그 → http://127.0.0.1:18039/tag/(닫힌 포트),
#   두 HTML 머리에 첫 스크립트(clarity.js 가 만든 타이머·관찰자, 인스턴스 replaceState, DOMContentLoaded 때 태그 수를 센다 · #gpc·#lsfail 주입)
curl -sI 127.0.0.1:18033/clarity.js       # 200 · Cache-Control: no-cache
curl -sI 127.0.0.1:18033/app/clarity.js   # 200 · Cache-Control: no-store, must-revalidate (007 의 /app/)
# 브라우저(Claude Browser pane, kimptrack.localhost:18034, 1440×900·375×812) — 랜딩·대시보드 각각
#   1 값 없음: 띠 = body 첫 자식·role region·aria-label·data-nosnippet·칸 셋 빈 칸·버튼 둘 176×44(375: 152×44)·clarity 없음·태그 0
#     링크 6개 — 대시보드 전부 _blank·noopener, 랜딩 target 없음 · 첫 Tab = 띠 안 스크롤 영역 → Tab·Space 로 칸 체크 → Tab·Enter 로 '내용 보기' 펼침
#     접힌 띠 1440: 299px·띠 안 스크롤 없음 / 375 랜딩: 390px ≤ 406(절반)·스크롤 없음·좌우 16px·가로 넘침 0
#     펼침 1440: 450px(=절반)·안에서 스크롤·버튼 줄 아래 그대로 / 375: 406px·안에서 스크롤 · 중요한 내용 18px(=1.2×15)·700·밑줄, 띠 글자 최소 15px
#     대시보드 375 는 셸이 넓어 레이아웃 뷰포트 708px(모바일 비대응) — 접힌 285·펼침 406(50dvh), 가로 넘침 0
#   2 세 칸 [선택한 대로 저장] → granted·2026-10-01·띠 없음·태그 1(ERR_CONNECTION_REFUSED 1건)·대기열 consentv2 {ad denied, analytics granted}(대시보드는 이어서 set tab spread)
#     새로고침 → 띠 없음·DOMContentLoaded 때 태그 0 → 뒤 1 · 두 칸만 저장(키보드 Enter) → denied·판 없음·띠 없음·태그 0
#   3 [모두 거부] → denied·판 없음·띠 없음·태그 0, 새로고침 뒤도 0 · 예전 판 granted(2026-09-01) → 띠·태그 0
#   4 GPC(값 없음·granted)·getItem 예외 → 띠 없음·태그 0, 심은 _clck(호스트)·_clsk(도메인·호스트)·_cltk 지워짐 · off → 띠·태그 0·같이 지워짐
#   5 탭 A /app/(granted, 숨김) · 탭 B /privacy [동의 철회] → A 가 reload(navigation type reload)·clarity 없음·띠 없음
#     A 띠 · B 세 칸 [선택한 대로 저장] → A 새로고침 없이 띠·style 지워짐·태그 1·대기열 consentv2 뒤 set tab spread(kt:clarity) · A 랜딩 띠 · B [모두 거부] → 띠만 지워짐
#   6 www.kimptrack.localhost(값 없음·granted)·localhost:18033(/ ·/app/, granted) → 띠 없음·태그 0 · /privacy 에 clarity 자원 0
#   7 띠가 뜬 동안 표 글자가 2.5초 사이 바뀜 · 검색칸 'btc' 입력 뒤 [모두 거부] → 띠 지워짐·#root 첫 자식 같은 객체·입력값 그대로 · clarity.js 의 타이머·관찰자 0
#   8 /app/?s.q=btc&s.view=rev → DOMContentLoaded 때 ?s.view=rev·검색칸 빈 칸·입력해도 URL 그대로
#   9 (granted) 필터 셋(역프 기준·업비트·임계 초과만) → 인스턴스 replaceState 0회 · 탭 단추 둘(갭·기록) → 2회, 대기열 set tab gap·event tab_gap·set tab history·event tab_history·set sym BTC
#     기록 탭 심볼 eth Enter → 0회·set sym ETH · 스프레드로 돌아가 행 클릭 → 1회, set tab history·event pivot_history·set sym C022
#   10 ?tab=history&sym=%ED%99%8D → ?tab=history·BTC · 홍길동 Enter → 선택 안 됨·입력칸 그대로 · eth Enter → sym=ETH
#   11 랜딩 granted → body data-clarity-unmask·대기열 consentv2 하나 · 필터 URL 복원(s.dom·s.view·s.only·s.thr 새로고침 그대로)
# 검토 반영 (2026-10-01) — origin/main(99975ec) 합침·뒤로 가기 캐시 복원·띠 글자 테스트 강화
git merge-tree --write-tree --name-only origin/main bba7724   # CONFLICT docs/context/status.md → 손으로 푼 합침 aa28fc3
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
#   All checks passed! / 273 files already formatted / 1177 passed (test_clarity 16 — 캐시 복원 1 더함)
cd web && npm run lint && npm run build && node --check public/clarity.js   # 종료 0 / ✓ / 통과, dist 6행 clarity.js · 25행 앱 모듈
# 변이 8개(pageshow 처리기 뺌·non-capture·stopImmediatePropagation 뺌·띠 칸 class="key" 뺌·다른 칸으로 옮김·나이 문장·규칙 문장 자리·복원 때 띠 안 지움) → 각각 test_clarity 실패
# Browser pane(kimptrack.localhost:18034) — 리뷰의 시도 그대로: 랜딩 granted + 나중에 붙인 non-capture pageshow(Clarity 흉내) → 같은 문서에서 denied
#   → PageTransitionEvent('pageshow',{persisted:true}) → 흉내 처리기 안 돎·새로고침·clarity 없음·태그 0 · 띠 + granted → 띠·style 지움·consentv2·태그 1 · 띠 + denied → 띠만 지움
# 실제 bfcache(헤드리스 크롬·임시 프로필·CDP — Browser pane 은 notRestoredReasons masked): 랜딩 granted → 바닥 '화면 분석 설정' → [동의 철회] → 뒤로(BackForwardCacheRestore)
#   고치기 전 clarity.js: 흉내 처리기가 돌고(Clarity 재시작) 늦은 storage 로 새로고침 / 고친 뒤: 흉내 처리기 안 돎 → 곧바로 새로고침 → clarity 없음·태그 0
#   반대(띠 → '처리방침에서 자세히 보기' → 세 칸 저장 → 뒤로): 복원된 문서(새로고침 없음)에서 띠 지움·태그 1
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — 표 admin 행 뒤에 `| clarity | - | web: clarity.js(ID·페이지 목록·안내 판 한 곳, 동의 안내 띠 — 정하지 않음(지금 판의 granted·denied 가 아님)이고 GPC 아닐 때 랜딩·대시보드에 032 와 같은 세 칸·칸마다 알릴 사항 '내용 보기'·[선택한 대로 저장]·[모두 거부], '화면 분석 설정' 링크(랜딩 바닥·대시보드 헤더 → /privacy#consent), 호스트·GPC·동의 값·판 확인, 세 칸 모두 동의한 방문자만 consentv2 광고 거부·분석 허용, DOMContentLoaded 뒤 태그, 다른 탭의 철회는 storage 이벤트로·뒤로 가기 캐시 복원은 pageshow 로 곧바로 새로고침·동의는 그 자리에서 켬), 랜딩·대시보드 가림 해제, 탭 태그·이벤트, 검색어 URL 제외·필터 URL 쓰기 우회·sym 형식 | ID 들어 있음 — 이 PR 머지(032 머지 뒤)가 켜기, 머지 전 §3.10 대시보드 설정·032 §7 법률 확인은 사람 |`. web-shell 행 "탭·심볼·탭별 필터가 URL 쿼리로 복원" 뒤에 "(검색어 제외, 033)". 알려진 빚: `(027) 기록 탭 검색칸 값(URL sym)은 검증 없이 기록된다` 를 지우고, `(027) 탭·필터 조작은 서버가 못 본다 — 후속 브라우저 분석` 을 `(027) 탭·필터 조작은 서버가 못 본다 — Clarity(033)가 본다(동의한 방문자만 — 차단·GPC·www 제외)` 로, `(027) 쿼리 키 삭제 목록은 검색 입력이 늘 때 손으로 맞춘다` 를 `(027·033) 검색어는 033 부터 URL 에 안 실린다 — caddy 의 세 키 삭제는 옛 링크용` 으로. 추가: `(033) 필터 URL 쓰기의 Clarity 우회는 clarity-js 가 인스턴스 replaceState 를 덮어쓰는 구현(0.8.71)에 기댄다`, `(033) Clarity 페이지 하나가 전송 128회(≈57분)·2시간이면 그 문서는 새로고침 전까지 녹화가 멈춘다`, `(033) Clarity 는 동의한 방문자만 본다 — 숫자는 하한(동의율·차단·GPC)`, `(033) 이미 열린 탭·뒤로 가기 캐시에서 돌아온 문서는 철회 뒤 곧바로 한 번 새로고침된다(storage 이벤트 — 같은 출처 탭만, 캐시 복원은 pageshow)`, `(033) 동의 기록은 방문자 브라우저에만 있다 — 입증 방법은 사람 확인(032 §7)`.
- `CLAUDE.md` — 스펙 인덱스 033 행 상태 → DONE. §2 runbooks 목록에 `clarity.md  Clarity 대시보드 설정·켜고 끄기·삭제 요청(프로젝트 통째 삭제) (사람용, 033)`.
- `docs/context/architecture.md` — 런타임 구성 web/ 줄 끝에 "랜딩·대시보드는 `public/clarity.js` 가 동의 안내 띠(세 칸)를 띄우고, 동의(`kt.analytics=granted`·`kt.analytics.v` 가 지금 판)·GPC 아님·호스트·ID 가 맞을 때만 Microsoft Clarity 태그를 부른다(033) — 관리자·처리방침 페이지는 부르지 않는다". 현재 구조 web-shell(002) 줄의 `urlState` 설명에 "`tab` 만 `window.history.replaceState`, 나머지 쓰기는 `History.prototype.replaceState` — Clarity 가 필터 변경을 새 페이지로 보지 않게(033), 검색어는 URL 밖" 을 넣고, 현재 구조에 `clarity (033)` 항목(파일·띠·shared 함수·셸 호출 위치 1~3줄).
- `docs/context/dev-setup.md` — web 절: `public/clarity.js` 는 oxlint 대상 밖(`node --check`), 로컬에서는 호스트가 달라 띠도 Clarity 도 없다, 확인은 §4 의 `--host-resolver-rules` 방법.
- `docs/context/product.md` — 용어 "분석 동의" 끝에 "띠는 '정하지 않음'(없음·그 밖·다른 판의 `granted`)일 때 뜨고, 고른 뒤 바꾸는 길은 랜딩 바닥·대시보드 헤더의 '화면 분석 설정'(033)" — 032 가 쓴 문장과 어긋나지 않게.
- `docs/specs/022-landing.md` — §3.5 스크립트 규칙에 "`<head>` 에 `clarity.js`(033) 한 줄 — 동의 안내 띠도 이 파일이 그린다, `<body>` 에 `data-clarity-unmask`" 한 줄. §3.3-8 바닥 nav 목록의 방침 링크 뒤에 "화면 분석 설정(→ `/privacy#consent`, 033)".
- `docs/specs/007-deploy.md` — nginx 정적 규칙에 `location = /clarity.js` 의 `Cache-Control: no-cache`(033).
- `docs/specs/027-observability.md` — §2 하지 않는 것 줄을 "브라우저 쪽 분석은 033, 처리방침은 032, 폰트 자체 호스팅은 후속" 으로(032 가 고친 줄과 합친다), §3.8 "검색 입력이 늘면 쿼리 키 목록도 같이 늘린다" 문장에 "검색어는 033 부터 URL 에 안 실린다 — 세 키 삭제는 옛 링크용" 을 붙인다.
- `docs/runbooks/clarity.md` — 신규. §3.1 순서, §3.10 설정과 확인·되돌리기, 삭제 요청 절(§3.10 — 새 프로젝트 → ID PR → 옛 프로젝트 삭제 → 요청자 통지), §4 배포 뒤 항목. Data Export 토큰 절은 035 가 더한다.

**담당자에게 제안** (PR 본문에 적는다)
- `002-web-shell.md` §3.5 — "화면 상태는 URL 쿼리에 실린다" 문단: 제외 목록을 "검색 입력 전부(spreads·gap·pp·history·flow)" 로, `sym` 은 영문 대문자·숫자 1~20자, "URL 쓰기는 `tab` 만 `window.history.replaceState`, 나머지는 원래 함수(033 — Clarity)". §4-4 는 그대로(탭 사이 유지는 메모리). 셸 밖의 동의 안내 띠(033 `clarity.js`)가 대시보드 아래에 뜰 수 있다는 한 줄. §3.5-1 헤더 줄에 방침 링크 옆 '화면 분석 설정'(→ `/privacy#consent`, 새 탭, 033).
- `003-spreads.md` §3 필터바 — "심볼 검색은 URL 에 싣지 않는다(033)".
- `013-premium-events.md` §3.5·`014-premium-1m.md` 차트 카드 — "심볼 검색 Enter 는 영문 대문자·숫자 1~20자일 때만 선택(033)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): 새로 — `web/public/clarity.js`(18.8KB), `web/src/shared/clarity.ts`, `server/tests/test_clarity.py`, `docs/runbooks/clarity.md`, `web/public/landing/fonts/kimptrack-sans-54942925.woff2`(글자 '석' 더해 다시 자름 — 옛 `99c0953a` 지움). 고침 — `web/index.html`·`web/public/landing.html`(파일 한 줄·가림 해제·바닥 링크·글꼴 주소), `web/nginx.conf`, `web/scripts/landing-font-glyphs.txt`, `web/src/App.tsx`(검색어 키 버리기·`sym` 형식·태그·이벤트·헤더 링크), `web/src/shared/urlState.ts`(`replaceUrl` 둘·`discardParams`·`symbol`/`isSymbol`), `web/src/features/{spreads,gap,pp}/Tab.tsx`(검색어 메모리), `web/src/features/history/Tab.tsx`(Enter 형식), `server/tests/test_observability.py`(공개 location 목록에 `/clarity.js`), 스펙 007·022·027·033, `docs/context/{status,architecture,dev-setup,product}.md`, `CLAUDE.md`.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - 설계 세션 결정(2026-10-01)으로 구현 전에 이 스펙을 고쳤다 — 머리·§3.1 ID `yqqzmx15ps` 를 이 PR 에 넣는다(032 가 같은 줄기에서 먼저 게시, 테스트의 '게시 표식이 있을 때만 ID' 조건은 그대로), §3.3 접힌 띠(랜딩 세션 요청 — 칸마다 '내용 보기' `<details>`, '저장 규칙', 접힌 띠는 375×812 에서 절반 안, 펼쳐야 띠 안 스크롤, `data-nosnippet`, 시스템 글꼴), §4 의 있어야 하는 글자·브라우저 1번(375·접힌 띠), §6 status 행. §6 대로 007(nginx 캐시)·022(§3.3-8 바닥 칸·§3.5 스크립트)·027(§2·§3.8) 을 고쳤다. 실행 중 §4 로컬 브라우저에 '크롬 인자를 못 주면 `kimptrack.localhost` 시험 사본' 한 문장을 더했다(Browser pane 은 `--host-resolver-rules` 를 줄 수 없다).
  - `defer` 로 실린 파일은 문서 해석이 끝난 뒤(`readyState` 이미 `interactive`)·`DOMContentLoaded` 전에 돈다 — `readyState` 만 보면 태그·띠를 너무 일찍 넣어서 `document.currentScript.defer` 도 본다.
  - 저장값 정리: 쿠키마다 도메인 속성 없이 한 줄(호스트 전용)과 `domain=kimptrack.com` 한 줄(`.kimptrack.com`) — 두 모양 모두 지운다. 띠 저장은 판을 먼저, 값을 나중에 쓴다(스펙 그대로), 둘 중 하나라도 예외면 부르지 않는다.
  - 띠 모양: z-index 2147483000, 599px 아래는 정의 목록 한 칸·여백 축소, '처리방침에서 자세히 보기' 와 '저장 규칙' 은 한 줄(규칙은 오른쪽 `<details>`), 버튼 줄은 최대 404px 두 칸. 글자는 모두 15px 이상 — 방침은 쿠키 이름(`code`)을 0.88em 으로 줄이지만 띠는 1em(테스트가 띠 CSS 의 1em 미만·px 글자 크기를 막는다). 색 대비는 가장 낮은 accent 글자 4.71:1(AA).
  - `data-nosnippet` 은 띠의 가장 바깥 요소(`#kt-consent`)에 단다.
  - 셸: 지금 탭을 다시 누르면 아무것도 안 한다(이벤트·URL 쓰기 없음). 태그는 `tab` URL 을 쓴 effect 다음 effect 에서 — 탭이 바뀐 뒤에만 `set tab` 과 이벤트, 기록 탭이면 심볼이 바뀔 때마다 `set sym`. `kt:clarity` 를 받으면 지금 탭(기록 탭이면 심볼도)을 다시 둔다.
  - `symbol`·`isSymbol`·`discardParams` 는 `shared/urlState.ts` 에 둔다 — 셸과 기록 탭이 같은 형식 규칙을 써야 하고(기능 간 import 금지), §4 테스트가 이 파일의 정규식을 본다. 기록 탭 검색 Enter 는 입력을 `trim()`·대문자로 바꾼 뒤 본다.
  - 계약 테스트의 node 단계는 가짜 window·document·location 으로 `clarity.js` 를 그대로 돌린다(새 라이브러리 없음). node 가 없으면 로컬은 건너뛰고 CI(`CI` 환경 변수)는 실패한다.
  - 검토 반영(2026-10-01): 같은 탭 뒤로 가기 캐시 복원에서 철회가 Clarity 재시작을 못 막던 것을 고쳤다 — §3.5 에 `pageshow`(persisted) 줄, §3.4 판단 시점, §4 있어야 하는 글자·로컬 브라우저 5번. §4 의 032 와 같은 글자는 알릴 사항을 원문 HTML 로(중요한 내용 표시까지)·문장 셋은 자리까지 보게, 싣는 곳은 `src` 로 싣지 않는 것으로(방침 주석의 파일 이름은 괜찮다 — 테스트가 이미 그렇게 봤다, 관리자는 `web/admin` 전부). 런북 3절 6번에 띠 있음·없음 30초 3회 중앙값.
  - 로컬 브라우저: 바인드 마운트한 스크래치 디렉터리를 다시 만들면 OrbStack 컨테이너에서 비어 보여 `docker cp` 로 넣었다. 닫힌 포트를 9번으로 하면 크롬이 `ERR_UNSAFE_PORT` 로 막아 '시도' 를 보기 어려워 18039 로 했다(`ERR_CONNECTION_REFUSED` 1건 = 태그 1회).
- 남은 빚:
  - 머지 전 사람: §3.10 Clarity 대시보드 설정(쿠키 끔·봇 감지·가림 Balanced·IP 차단·팀)과 032 §7 법률 확인 — 이 PR 머지가 곧 켜기다. 032(#90)·034 운영 확인(#89)이 든 origin/main(99975ec)을 이 브랜치에 합쳤다 — status.md 표가 내용 충돌해(main 이 고친 admin 행 바로 뒤에 clarity 행을 넣었다) 손으로 풀었다: admin 행은 main 것(034 운영 확인 완료), 그 뒤 clarity 행, privacy 행 끝은 이 브랜치 문구. 앞선 '충돌 없음' 은 낡은 로컬 `main`(8439448)에 대고 본 것이었다. 합친 뒤 server·web 검증을 다시 돌렸다(§5).
  - 배포 뒤 사람(§4): 실제 크롬의 `clarity.ms` 요청·쿠키 둘·`metadata` 동의 상태, 2시간 안 녹화(숫자 보임·입력칸 가림·탭 태그·필터로 페이지가 늘지 않음), Brave GPC·VoiceOver·실제 휴대폰, 스프레드 탭 Performance(동의·거부·띠 있음·없음)와 1시간 전송량 → `app` 유지 판단. 로컬은 시험 사본·닫힌 포트라 Clarity 태그가 실제로 돈 적이 없다.
  - 뒤로 가기 캐시 복원은 크롬에서만 봤다(헤드리스 CDP·Browser pane 합성 이벤트). Safari·Firefox 도 표준 `pageshow`·capture 단계라 같게 돈다고 보지만 확인하지 않았다.
  - 375×667 처럼 낮은 화면은 접힌 띠(≈390px)도 상한(50dvh)을 넘어 띠 안에서 스크롤한다. 대시보드는 휴대폰 폭에서 셸이 넓어(레이아웃 뷰포트 708px) 띠도 함께 작게 보인다(모바일 비대응).
  - status 빚 그대로: Clarity 의 인스턴스 `replaceState` 덮어쓰기 의존(0.8.71), 전송 128회·2시간 녹화 멈춤, 동의한 방문자만(숫자는 하한), 열린 탭은 철회 뒤 한 번 새로고침, 동의 입증은 사람 확인(032 §7).
  - 담당자 제안(§6 — 002·003·013·014 스펙 문구)은 PR 본문에.
