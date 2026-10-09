# 041 — admin-explain

상태: DONE | 의존: **fix/036-admin-followup(PR #95)이 머지된 main** 에서 시작한다(아니면 멈추고 묻는다 — 그 PR 의 본문 경과 글자 `retick`·차트 빈 칸 '값 없음'·'값 1개뿐'·`keep-all`·favicon 204·Clarity 지표 이름 정규화를 전제로 한다). 037~040 과 나란히 가고 042 보다 먼저 머지한다(042·043 이 이 스펙의 설명 틀을 쓴다). 계약을 읽기만 하는 스펙(쓰는 것은 §3.4·§3.5 에 복사했다): 002(대시보드 탭 id·이름), 011(실패 종류 이름표·성공률 정밀도), 025(알림 10분 억제), 027(경보 이름·임계), 034·035(관리자 피드 넷). 고치는 스펙: 036 admin-v2(화면), 034 monitoring-ops(문장만).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 관리자 화면의 숫자마다 "어디서 온 값인지, 어떻게 셌는지, 무엇을 뜻하지 않는지" 를 그 자리에서 읽는다. 여덟 절에 접힌 설명을 달고, 영어 id 로만 보이던 실패 종류·경보·대시보드 탭을 한국어 이름으로 적으며, 잘못 읽히는 문구 다섯을 고친다. 화면 구조·피드·주기는 그대로다.

## 2. 범위
- 만드는 것: `web/admin/index.html` 의 정적 설명(절마다 '이 절 읽는 법' 하나, 덩어리 끝 '이 칸 뜻'), `admin.js` 의 이름표 표 셋·타일 부제·문구 고침, `admin.css` 의 설명 모양, `server/tests/test_admin.py` 정적 단언.
- 하지 않는 것:
  - 새 절·새 파일·새 피드·새 경로·새 외부 링크.
  - 칸 위에 뜨는 풍선(popover·'?' 단추) — 360 폭에서 옆 값을 가리고 JS 코드가 늘어난다.
  - JS 로 만드는 설명.
  - 개요 칸 여섯(`.vital` — 누르면 절로 가는 링크) 안에 무엇을 넣는 것. 링크 안에 누르는 요소를 둘 수 없고, 개요 설명은 절 설명이 맡는다.
  - 답 문장(덩어리 첫 줄에 데이터로 만든 답 — 042·043). 접속 절의 새 칸(042·043). 서버 동작.
- 바꾸는 기존 것: 036 §1·§3.4·§3.5·§3.9·§4(설명·이름표·문구), 034 §1·§3.2·§3.5 의 AWS 계정 종료 문장(동작 같음 — 문장만). 사용자 결정(2026-10-02): AWS 계정 종료일을 화면·문서의 근거로 쓰지 않는다 — 계정이 지워져도 운영을 이어 간다.
- 담당: 034·036 은 이 레포 주인 담당이라 고친다. 002·011·025·027 의 이름·규칙은 읽기만 한다.
- 접속 절의 서버 기록 설명은 042 §3.6 이 정한다(Clarity 설명은 043 이 고쳐 쓴다).

## 3. 동작

> 이 화면은 064·065 가 대신한다 — 이 스펙에는 피드 계약이 없다(관리자 화면의 설명 층만 정했고, 064 는 긴 설명 대신 제목 옆 '?' 하나를 둔다).

## 4. 검증
> 아래의 화면 확인(정적 단언·node·브라우저)은 064(화면 분석은 065)가 대신한다 — 이 스펙을 구현할 때의 기록으로만 남긴다.

**PR 안 — 실행 세션(완료 조건)**. 시작 전에 main 에 PR #95 가 있는지 본다 — admin.js 에 `function retick(` 과 `'값 1개뿐'` 이 있어야 한다(없으면 멈추고 묻는다). 바깥 호출은 없다.
- 정적 단언(`server/tests/test_admin.py`). 036 의 화면 단언은 그대로 통과하고 고치지 않는다 — 파일 셋(`admin.css`·`admin.js`·`index.html`), `fetch(` 1회와 여덟 경로, `SCRIPT_BANNED`, SVG 요소(`svg`·`g`·`line`·`rect`·`path`·`title`)와 속성 허용 목록 `SVG_ATTRS`, 외부 링크 호스트 `EXTERNAL_HOSTS`(042 가 db-ip.com 을 더한다 — 이 PR 은 그대로). 더하는 것:
  - 절 설명: SECTIONS 여덟마다 그 `<section>` 안에 `<details class="explain">` 이 글자 그대로(`open` 등 다른 속성 없음) 정확히 하나다. 개요 밖 일곱은 `.sec-head` 를 닫는 `</div>` 바로 뒤(공백만 사이)이고, 개요는 `.vitals` 를 닫는 `</div>` 바로 뒤이며 그 details 뒤가 곧 `</section>` 이다. summary 는 '이 절 읽는 법' 으로 시작하고, `dl` 하나에 dt 수 = dd 수 ≥ 3, 첫 dt 둘의 글자가 '읽는 값'·'판정' 이다.
  - 덩어리 끝 설명: 모든 `<details class="terms">` 가 글자 그대로 그 꼴이고 summary '이 칸 뜻'·`dl` 하나·dt 수 = dd 수 ≥ 2 다. 수는 고정하지 않는다(042·043 이 바꾼다). 수집·인프라·접속·비용 절에 하나 이상 있다.
  - 다시 그리기 밖: index.html 을 표준 라이브러리 `html.parser` 로 읽는다. 두 details 의 조상 가운데 id 를 가진 요소는 그 `<section>` 과 `infra-parts`(admin.js 가 `hidden` 만 바꾼다)뿐이다 — admin.js 가 내용을 바꾸는 칸은 모두 id 를 가지므로, id 없는 묶음 안에 두면 다시 그리기가 닿지 않는다. admin.js 에 `explain`·`terms` 글자가 없다(설명을 만들거나 건드리지 않는다).
  - 이름표: admin.js 의 표 셋(테스트는 실행 세션이 정한 상수 이름으로 찾는다) — 실패 종류 여덟·경보 꼬리 여덟·탭 여섯의 키 집합과 한국어 이름, 경보 꼬리의 조건 글이 §3.4 와 같다. 키 스물둘이 모두 index.html 의 explain·terms 안 dl 에 글자로 있다.
  - 문구: admin.js 에 '10분 억제로 보내지 않은 알림은 기록에도 없다' 가 있고 '억제된 알림은 없다' 가 없다 / `successRate1h.toFixed(2)` 없음 / `'버전'` 글자와 `.version` 읽기 없음 / `aws: 'AWS 자격 없음',` / web/admin 세 파일에 '계정 종료'·'AWS 종료'·'종료 예정'·'방문자-일'·'visitor-day' 없음(날짜 글자만으로는 막지 않는다 — 043 의 측정 날짜가 admin.js 에 든다).
  - 개요 칸: `<a class="vital"` 여섯 각각의 안이 span 셋(이름·값·아래 줄)뿐이다 — details·summary·button 없음. 세 파일에 `popover`(대소문자 무관) 없음.
  - 설명 글 안전: explain·terms 안에 `<a`·`<img`·`<script`·`<svg`·`style=`·`://` 가 없다. 12자리 숫자·이메일 모양·32자 이상 hex 는 036 단언이 HTML 전체를 본다.
- node 논리 확인(pytest 안 — 033 `test_clarity.py` 처럼 node 로 admin.js 를 가짜 `window`·`document`·`fetch` 와 싣고, 그리기가 아닌 이름표 찾기·글자 만들기만 부른다. node 가 없으면 CI 에서는 실패): kind 여덟·`zzz`·`constructor`·`__proto__` → 이름표 또는 원래 글자 / 아래 5 의 경보 이름 아홉 → 꼬리 이름표·조건(foo 는 이름표 없음) / 탭 여섯·`(기타)` / 성공률 99.8·97.5 → '99.8%'·'97.5%' / 접속 절을 가짜 응답으로 채우면 ⑤ 대시보드 진입 탭의 한국어 이름·title id(042), 타일 부제(서버 기록 ①·⑥ 일곱·Clarity 넷) / 수집 요약 줄·절 요약의 성공률 소수 1자리·버전 없음(응답에 `version` 이 있어도). 가짜 DOM 이 지나치게 커지는 항목은 아래 설계 세션 확인으로 넘기고 §7 에 적는다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`(dist 에 admin 없음), `node --check web/admin/admin.js`.
- 커밋(각 300줄 이하): ① CSS + 개요·수집·인프라 설명 HTML ② 알림·접속·비용·도구 설명 HTML ③ JS 이름표 표·타일 부제·문구 고침 ④ 정적 단언·node 논리 확인 ⑤ §6 문서·§5·§7.

**PR 안 — 설계 세션 확인(머지 전 — 실행 세션의 샌드박스는 Docker·헤드리스 Chrome 을 띄울 수 없다. 결과와 돌린 세션을 §5 에 적고, 어긋나면 머지 전에 고친다)**. 036 §5 와 같은 구성(가짜 백엔드·확인 스크립트는 scratchpad, 커밋하지 않는다) — 망 하나, 가짜 백엔드 둘(python:3.12-alpine, 망 별칭 server·api, 여덟 경로의 응답과 부분 상태를 파일로 바꿀 수 있게), nginx:1.27-alpine 에 `nginx-admin.conf` 템플릿과 `web/admin` 을 붙여 127.0.0.1 에만 게시, 헤드리스 Chrome CDP 또는 Browser pane. 바깥 요청 0(요청 기록이 같은 출처뿐).
  1. 처음 열면 explain 여덟·terms 전부가 접혀 있다. 폭 1280·768·360 마다 모두 접은 상태와 모두 편 상태에서 `documentElement.scrollWidth ≤ innerWidth`, 한글 음절 중간 줄바꿈 0(036 후속 wrap 확인과 같은 방법), CSP 위반·Uncaught 0.
  2. 펼침 유지: 접속 explain, '서버 기록'·'경보'·'거래소 다섯' terms 를 편 채 가짜 값을 바꿔 가며 75초를 둔다(빠른 7·느린 1회 이상). 넷 다 열려 있고 같은 요소(노드 비교)이며 문서 스크롤 위치가 그대로다.
  3. 키보드: Tab 으로 summary 마다 닿고 Enter·Space 로 열고 닫는다. 초점 외곽선이 보인다.
  4. 이름표 — 실패 종류: 가짜 `outages`·`openOutage`·`lastError` 에 kind 여덟 + `zzz` + `constructor` 를 넣는다. 거래소 표·타임라인 title·아래 다섯 줄에 한국어 이름이 나오고, `zzz`·`constructor` 는 원래 글자다.
  5. 이름표 — 경보: 꼬리 여덟을 단 이름(`marketlens-collect-status-instance`·`-serve-status-system`·`-data-credit-balance`·`-data-credit-surplus`·`-serve-memory`·`-data-disk`·`marketlens-canary`·`marketlens-http-5xx`)과 `marketlens-foo` 를 넣는다. 이름 뒤 이름표·title 조건이 나오고 foo 는 이름표가 없다. ALARM 행·정상 n개 묶음·알림 경보 행이 다 같다.
  6. 이름표 — 탭: 접속 탭 표에 여섯 id + `(기타)` 를 넣으면 한국어 이름이 나오고 title 은 id 다.
  7. 문구: `successRate1h` 99.8·거래소 97.5 → '99.8%'·'97.5%' / 요약 줄에 '버전' 없음 / 비용 카드 고정 글 하나 / AWS 다섯 부분 `unconfigured` → 칸마다 '연결 안 됨' + 'AWS 자격 없음' / 알림 머리 줄이 새 글.
  8. 타일 부제: Clarity 넷 아래 한 줄씩 나오고, 360 에서 잘리지 않는다.
  9. 036 의 XSS 확인(방문자·서버 글자 칸 전부에 `<img src=x onerror=alert(1)>`·`javascript:alert(1)`·U+202E)을 다시 돌려 그대로 통과한다(버전 칸은 빼고). img 0·대화상자 0·title 밖 속성에 0.
  10. 대비: 설명 글·summary 꼬리·타일 부제·dt 의 id 글자가 4.5:1 이상.
  11. 사진 셋 — 360·1280 에서 접속 explain 을 편 것, 360 에서 '거래소 다섯' terms 를 편 것. PR 본문에 붙인다.

**배포 뒤 — 사람(완료 조건 아님, status.md 비고 "041 운영 확인 대기")**: 휴대폰에서 설명 몇 개를 펴 보고 가로 스크롤이 없는지 / 경보 18개가 모두 이름표를 다는지(이름이 027 꼴과 다른 경보가 없는지) / 설명 글이 실제 값과 어긋나는 곳이 없는지.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 시작 조건 — 이 브랜치는 스펙 PR #96(docs/specs-037-040) 위에 PR #95(fix/036-admin-followup)를 합친 3d9471d
#   (둘 다 main 머지 전 — 설계 세션이 의존 충족으로 봄)
grep -n "function retick(\|'값 1개뿐'" web/admin/admin.js   # 90: function retick() · 378: '값 1개뿐'
cd server && .venv/bin/pytest -q    # 시작 전 기준선: 1242 passed, 6 failed(아래와 같은 test_gauge UDP bind 6건)

# 기존 스펙 재검증 (마지막 코드 커밋 01222be 뒤 — 이 세션 Bash 는 샌드박스)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
#   All checks passed! · 286 files already formatted · 1253 passed, 6 failed — 6건은 모두
#   app/features/spreads/tests/test_gauge.py 의 UDP bind PermissionError(샌드박스, 기준선과 같음). 뺀 결과 1253 passed
cd web && npm run lint && npm run build   # oxlint 종료 코드 0 · ✓ built · dist 에 admin* 0개
node --check web/admin/admin.js           # 종료 코드 0
# 커밋마다 tests/test_admin.py·test_clarity.py·test_deploy.py — 83 passed(①②③) · 90 passed(④ 정적) · 94 passed(④ node·고침·⑤)
#   커밋당 diff 4~216줄
# 새 단언이 틀린 화면을 잡는지 — <scratch>/041/mutate.py(테스트의 파일 읽기를 바꿔치기한 어긋남 아홉):
#   open 속성 · 머리 줄 바로 아래가 아님 · 다시 그리는 칸(b-canary) 안의 설명 · dl 에 <code>pp</code> 빠짐 · 이름표 오타 ·
#   옛 억제 문구 · 설명 안 링크 · 개요 칸 안 button · dt/dd 수 어긋남 → 아홉 모두 실패로 잡힘

# 검토 반영(설명 글 고침·CSS 간격·단언 보강 — 이 세션 Bash 는 샌드박스)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
#   All checks passed! · 286 files already formatted · 1254 passed, 6 failed(같은 test_gauge UDP bind 6건)
cd web && npm run lint && npm run build   # 종료 코드 0 · ✓ built · dist 에 admin* 0개 / node --check 0
# <scratch>/041/mutate2.py(admin.js 를 바꿔 node 하네스·정적 단언을 다시 돌림) — 앞서 놓치던 여섯(경보 조건 오타·
#   서버 기록 부제 지움·Clarity 부제 지움·탭 이름표 연결 끊음·요약 줄 fixed(…, 2)·버전 b['version'])과
#   stat 부제 줄 지움·부제 글 오타까지 여덟 모두 실패로 잡힘. mutate.py 아홉도 그대로 잡힘

# 설계 세션 확인(§4 의 1~11) — 2026-10-02 설계 세션(5f7df1f, 샌드박스 밖). 망 fu041-net, 가짜 백엔드 둘(python:3.12-alpine +
#   <scratch>/041chk/fake.py — 036 후속 fake 사본, 별칭 server·api), nginx:1.27-alpine 에 nginx.conf·nginx-admin.conf 템플릿·web/admin, 127.0.0.1:19141
docker exec fu041-web nginx -t     # test is successful
# Claude 브라우저 창(127.0.0.1:19141)
#  1 폭 1280·768·360 × 모두 접음·모두 폄(details 21개): scrollWidth == innerWidth, 카드 밖 넘침 0, 한글 음절 중간 줄바꿈 0 — 여섯 경우 모두
#  2 접속 explain·접속 첫 terms·인프라 첫 terms·수집 첫 terms 를 편 채 78초(빠른 묶음 10회·느린 2회, 값이 바뀌는 기본 가짜): 넷 다 열림·같은 요소·스크롤 1500 그대로
#  3 summary 21개 모두 tabIndex 0, :focus-visible 외곽선 2px(admin.css) — Enter·Space 는 브라우저 details 기본 동작
#  4~6 이름표(가짜 override — kind 여덟+zzz+constructor, 경보 아홉, 탭 여섯+(기타)): 한국어 이름 모두 나옴(접힌 칸·title 포함), zzz·constructor·foo 는 원래 글자, 행 예 'canary 바깥 점검'·'collect-status-instance 인스턴스 상태검사'
#  7 99.8%·97.5%, '버전' 없음, '계정 종료' 없음, AWS 다섯 부분 unconfigured → '연결 안 됨' 14곳·'AWS 자격 없음', 비용 고정 글 하나
#  9 xss 가짜: <img onerror> 글자로 70곳, img 0, title 밖 속성에 0, javascript: 링크 0, U+202E 0
# 10 대비(편 상태): explain dd 10.43·dt 17.03·summary 10.43, terms dd 7.55·dt 12.33·summary 7.55, dt 안 id 5.25, 타일 부제 5.25 — 모두 4.5 이상
#  콘솔 오류·CSP 위반 0(브라우저 창·헤드리스 둘 다)
# 11 사진 셋(헤드리스 Chrome CDP, <scratch>/041chk/shot041.py): 접속 explain 편 1280·360, 수집 '이 칸 뜻' 편 360 — PR 본문에
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md`
  - admin 행 web 칸 끝에 "· 설명(041 — 절마다 접힌 '이 절 읽는 법'·덩어리 끝 '이 칸 뜻', 실패 종류·경보·탭 한국어 이름표, 타일 부제)", 비고 끝에 "· 041 운영 확인 대기(휴대폰 펼침·경보 이름표)".
  - 알려진 빚 `(036) AWS 계정 종료(2026-12) 뒤 인프라·비용 절과 AWS 링크는 '연결 안 됨' — 이전이 정해지면 화면을 고친다` → `(036) AWS 자격이 없거나 AWS 밖으로 옮기면 인프라·비용 절과 개요의 AWS 칸은 '연결 안 됨' 이고 도구 절의 AWS 링크는 남는다 — 옮길 곳이 정해지면 화면을 고친다`.
  - 알려진 빚에 더한다: `(041) 접속 절 설명은 035 칸(24시간 요약·Clarity 받은 지표) 기준 — 042·043 이 칸을 바꿀 때 고쳐 쓴다. 038 이 바꾸는 뜻(5xx 의 WS 502·페이지의 304·상위 표의 봇·기기 이름·비율 분모)은 그때까지 설명에 없다`.
- `CLAUDE.md` — 스펙 인덱스 041 행 상태 → DONE.
- `docs/context/architecture.md` — '현재 구조' admin 항목의 `web/admin/` 설명 끝에 "설명(041) — 정적 HTML details 두 가지(절 '이 절 읽는 법'·덩어리 끝 '이 칸 뜻')라 다시 그리는 칸 밖에 있고, 이름표 표 셋(실패 종류·경보 꼬리·대시보드 탭)은 admin.js 상수".
- `docs/specs/036-admin-v2.md` (DONE — 이 PR 의 코드·테스트와 함께)
  - §1: "AWS 계정은 2026년 12월에 끝난다 — " 를 지운다(뒤 문장은 그대로).
  - §3.2 알림 피드 복사 줄의 "(전송 실패도 기록된다, 억제된 알림은 없다)" → "(전송 실패도 기록된다, 10분 억제로 보내지 않은 알림은 기록에도 없다)". §3.4 개요 판정의 "AWS 가 끝나거나 AWS 밖으로 옮겨도(`unconfigured`)" → "AWS 자격이 없거나 AWS 밖으로 옮겨도(`unconfigured`)".
  - §3.4 머리에 한 줄: "설명 — 절마다 머리 줄 바로 아래 접힌 '이 절 읽는 법'(개요는 칸 여섯 아래), 덩어리 끝 '이 칸 뜻', 행 안 한국어 이름표(실패 종류·경보 꼬리·탭), 타일 부제. 자리·문구 규칙·항목은 041 이고 042·043 도 같은 틀을 쓴다."
  - §3.4 수집: 요약 줄의 "·두 역할 버전" 을 지우고 "1시간 성공률은 소수 1자리(011 의 서버 정밀도)" 를 더한다. 거래소 표의 "(`kind · n회 · n분째`)" → "(실패 종류 이름표 · n회 · n분째)", 마지막 오류의 "kind" → "실패 종류 이름표". 타임라인 "`HH:mm–HH:mm · kind · ×count`" → "`HH:mm–HH:mm · 실패 종류 이름표 · ×count`".
  - §3.4 인프라 경보 표: "이름(`marketlens-` 접두를 뗀다," 뒤에 "이름 뒤에 경보 꼬리 이름표(041) — title 은 전체 이름과 조건," 을 넣는다. 알림 행의 경보 글 "`<이름> <이전> → <새>`" → "`<이름> <꼬리 이름표> <이전> → <새>`".
  - §3.4 알림 머리 줄: "— 억제된 알림은 없다" → "— 10분 억제로 보내지 않은 알림은 기록에도 없다".
  - §3.4 접속: 서버 기록에 "총 요청·페이지 타일은 부제 한 줄(041)", 표 여섯의 탭 표는 "한국어 탭 이름(002 — title 은 id)". Clarity 에 "타일 넷은 부제 한 줄(041)".
  - §3.4 비용: "고정 문구 둘: …, "AWS 계정 종료 예정 2026-12"(날짜는 사람 확인)." → "고정 문구 하나: "서비스별 내역은 조직 SCP 가 Cost Explorer 를 막아 여기 없다 — 결제 콘솔에서 본다"."
  - §3.5: AWS 원인 "AWS 자격 없음 또는 계정 종료" → "AWS 자격 없음".
  - §3.9: "AWS 계정 종료(2026-12)·AWS 밖으로 옮김:" → "AWS 자격이 없거나 AWS 밖으로 옮김:". 끝 문장 "이전이 정해지면 사람이 이 스펙을 고친다" → "옮길 곳이 정해지면 사람이 이 스펙을 고친다".
  - §4 정적 단언 줄 끝에 "설명·이름표·문구 고침 단언은 041 §4 가 더한다".
- `docs/specs/034-monitoring-ops.md` (DONE — 문장만, 동작이 같아 server 코드·테스트는 그대로)
  - §1: "AWS 계정은 2026년 12월에 끝나고 조직 SCP 가 일부 API 를 막는다" → "조직 SCP 가 일부 API 를 막는다".
  - §3.2 `unconfigured`: "AWS 밖 호스트나 계정 종료 뒤가 이렇다" → "AWS 밖 호스트나 자격이 없어진 뒤가 이렇다".
  - §3.5: "AWS 계정 종료(2026-12)·AWS 밖 호스트:" → "AWS 자격이 없음·AWS 밖 호스트:".

담당자에게 제안: 없다 — 002·011·025·027 의 이름과 규칙을 읽기만 한다.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `web/admin/index.html`(절 설명 일곱 + 덩어리 끝 설명 열 — 수집 둘(거래소 다섯·실패 구간), 인프라 셋(경보·canary·박스), 접속 넷(실시간·서버 기록·상위 표 여섯 다음·Clarity), 비용 하나 — 와 비용 고정 글 하나 지움·인프라 주석 옮김), `web/admin/admin.css`(설명 모양·타일 부제·경보 꼬리 글자), `web/admin/admin.js`(이름표 표 `KIND_NAME`·`ALARM_TAIL`·`TAB_NAME` 과 찾기 `kindName`·`alarmTail`·`alarmNamed`·`tabName`, `stat` 의 부제, `pctFmt` 를 공통 도구로, 문구 고침 다섯), `server/tests/test_admin.py`(정적 단언 일곱 + node 확인 넷). 문서: 이 스펙, `036-admin-v2.md`·`034-monitoring-ops.md`(§6 대로), `docs/context/status.md`·`architecture.md`, `CLAUDE.md`(인덱스).
- 설명 글의 사실은 스펙·context 문서에 더해 레포 코드와 맞춰 봤다 — 1시간 성공률 식(`features/health/service.py`), 정상 판정 = 연결 + 30초 안의 시세(`core/ticks.py`), n회 = 실패로 판정된 틱 수·연속 성공 3번에 닫힘·60초 알림(`core/outages.py`), 실패 종류 뜻(`core/errors.py`·커넥터 분류), 경보 조건(런북 `cloudwatch.md` 의 경보 줄 = §3.4), canary 네 단계(`ops/canary/index.mjs`), 서버 기록의 창·페이지·끝난 WS·읽지 못한 줄(`features/admin/access.py`·`access_tally.py`), caddy 가 기록하지 않는 요청(`caddy/Caddyfile`), 10분 억제·대기열이 넘친 알림 버림·머리 그림(`core/notify.py`·`outages.py`·`main.py`), 에이전트 지표(`ops/cloudwatch/*.json` — 디스크는 루트).
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - '데이터 부족': 메모리·디스크·canary 경보는 데이터 없음을 울림으로 다뤄 점이 없으면 데이터 부족이 아니라 ALARM 이 된다(AWS 동작) — §3.3 의 "'데이터 부족' 의 뜻과 그것도 울림으로 다루는 경보" 를 이렇게 적었다.
  - 접속 '남기는 것': 요약은 60초마다 caddy 기록 파일을 다시 읽어 세므로 "api 를 다시 시작하면 기록 파일을 처음부터 다시 읽어 센다" 로 적었다(0 부터 다시 쌓는다고 읽히지 않게).
  - Clarity 세션·사용자·세션당 페이지의 셈 규칙은 근거 문서에 없어 '확인하지 못했다'·'모른다' 로 적었다(`PagesPerSessionPercentage` 가 평균인지 비율인지 포함).
  - 타일 부제: 총 요청 '파일·봇·스캔까지 기록된 모든 줄', 페이지 '화면 주소 요청 · 봇 섞임', 세션 '동의한 방문자만', 봇 세션 'Clarity 가 봇으로 본 세션', 사용자 '동의한 방문자 · Clarity 기준', 세션당 페이지 'Clarity 값 그대로'.
  - title: 열린 실패 구간·아래 다섯 줄은 원래 id, 마지막 오류는 `<id> — <거래소 글>`(전체 글을 보이던 title 에 id 를 붙임). 타임라인 막대 title 은 036 꼴대로 이름표만(id 는 설명 dl 에). 탭 표는 표에 없는 값도 title 에 원래 글자.
  - 경보 꼬리는 표의 꼬리를 차례로 보고 처음 맞는 것(겹치는 꼬리 없음). 표 키를 훑는 것은 서버 값 조회가 아니라 `Object.keys(상수)` 다.
  - 개요 '판정' dd 의 재료 목록은 가운뎃점으로만 이으면 keep-all 에서 끊을 곳이 없어 360 폭 dd(어림 300px)를 넘으므로 쉼표로 나눴다. dt 열은 5.5~9.5em(768 폭의 두 칸 카드에서 dd 가 좁아지지 않게).
  - 수집 '이 칸 뜻' 에 '수집기 시작' 을 더했다(버전 칸을 지워 배포·재시작은 이 칸으로 본다).
  - 설명 층 CSS: 절 설명은 accent 6% 바탕·왼쪽 줄, 덩어리 끝 설명은 카드 안이면 위 구분선, 카드 밖(상위 표 여섯 다음·박스)이면 카드 바탕의 접힌 칸. 글자 neutral-200(dt)·400(dd·summary)·500(꼬리·id·부제) — 계산상 바탕 셋(절 바탕·카드·표 행 hover)에서 neutral-500 이 4.72:1 이상.
  - node 확인은 가짜 DOM(요소 = 글자·자식·title 만)에 `visibilityState: hidden` 으로 싣는다 — 묶음이 돌지 않아 요청 0 이고, 이름표 찾기와 행 글자(`exchangeRow`·`timeline`·`alarmRow`·`alertRow`)·title, 가짜 응답으로 부른 채우기(`fillAccess`·`fillClarity`·`drawCollect`)의 탭 표·타일 부제·요약 줄까지 본다.
- 검토 반영(커밋 ⑥ dfd454c): 설명 글을 코드와 다시 맞췄다 — 페이지는 GET 으로 열어 오류·리다이렉트가 아닌 응답(035 200·038 200·304 — 숫자는 적지 않음), 총 요청은 읽은 줄 전부(읽지 못한 줄 제외, 부제도), 상태 코드 넷 밖의 101, 상위 표 % 는 여섯 표가 같은 분모라 일부만 세는 표는 합이 100% 아님(분모 이름은 038 이 바꿔 적지 않음), 마지막 오류 글은 거래소 응답 본문이 있으면 그 글·없으면 수집기 글, 열린 구간은 실패 종류가 바뀌면 닫고 새로 열림, 타임라인 최소 폭 ≈6분(1000 중 4), 판정 재료는 위치 말 없이(640px 이하는 띠 아래 줄), 수집기의 경보·canary 읽기 오류(주의)와 이 화면의 호출 실패(판정 밖)를 나눔, 연결 안 됨에 알림 절 경보 이력, 보낸 알림도 Redis 기록 실패·기록을 붙이기 전이면 빠짐. CSS `.grid + .terms` 위 간격을 블록 부모에만(flex `.stack` 에서 gap 과 겹쳐 28px). 단언은 경보 조건 여덟을 §3.4 복사와 대조하고, node 하네스가 `fillAccess`·`fillClarity`·`drawCollect` 를 가짜 응답으로 불러 탭 표 이름표·부제 여섯·요약 줄 성공률·버전 없음을 본다.
- 실행 중 함께 고친 스펙 절: §3.3 인프라 판정·알림 항목의 괄호(검토 반영), §4 이름표·node 확인 줄(검토 반영), §3.2·§3.3 의 Clarity 간격 — '칸 머리' 가 아니라 칸 본문('다음 조회 … — n 간격')에 있어 'Clarity 칸에 적힌 간격' 으로 고쳤다(화면 글도 같다). §6 대로 036 §1·§3.2·§3.4·§3.5·§3.9·§4, 034 §1·§3.2·§3.5.
- 설계 세션 확인(§4 의 1~11)은 설계 세션이 샌드박스 밖에서 돌렸다(2026-10-02, §5) — 모두 통과. 검토 반영 뒤 설계 세션이 고친 것: 개요 판정의 흐린 ○ 까닭 '이 화면이 그 값을 부르지 못함', 경보 canary 꼬리 이름표를 '바깥 점검' 으로(이름 canary 와 겹치지 않게 — §3.4).
- 남은 빚:
  - 설계 세션 확인 1~11(머지 전 — 결과는 §5 에).
  - 배포 뒤 사람 확인(status 비고 "041 운영 확인 대기").
  - 접속 절 설명은 035 칸 기준(status 빚) — 038 이 바꾸는 뜻과 042·043 의 새 칸은 그때 고쳐 쓴다. Clarity 셈 규칙은 세션이 있는 응답·040 정규화 뒤 고쳐 쓴다.
  - 다른 스펙(보고만): `docs/specs/032-privacy.md:§3.5 — "AWS 계정은 2026년 12월에 끝난다" → 사용자 결정(2026-10-02)으로 근거에서 뺀다`(037 이 고친다). `docs/specs/036-admin-v2.md:§5` 의 지난 XSS 확인 목록에 지운 '버전' 칸이 남아 있다(실행 기록이라 그대로 둠).
