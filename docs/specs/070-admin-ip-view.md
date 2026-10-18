# 070 — admin-ip-view

상태: DONE | 의존: 066(관리자 틀·상세 판)·069(피드 `/admin/access/addrs`·`/admin/access/addr` — 머지 전이면 가짜 피드로 만들고 시험한다).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-14): 관리자 '방문자' 페이지에서 IP 전체를 보고, IP 마다 봇인지 사람인지와 그 근거, 어디(나라·지역·도시·망 사업자)인지 한눈에 본다.

## 2. 범위
- 만드는 것: '방문자' 페이지의 IP 표(069 `addrs`)와 IP 상세 판(069 `addr`), 가짜 피드 서버 응답, 테스트.
- 하지 않는 것: 서버(069). 다른 페이지.
- 바꾸는 기존 것: 066 방문자 페이지 — 대역 표(067)는 'IP 별 | 대역 별' 보기 단추로 남기고 기본을 IP 별로.

## 3. 동작

### 3.1 읽는 값 (069 복사)
- 목록 `/svc/api/admin/access/addrs?window&net&country&verdict&q&masked&sort&limit&offset` → `{state, code, window, total, since, rows:[{ip, masked, country, region, city, asn, org, net, requests, pages, js, ws, kinds, status, first, last, spark, topPaths, topAgents, score, verdict, reasons:[{code, text, weight}]}]}`. verdict `bot`·`suspect`·`human`·`unknown`·`operator`. `masked` 행의 ip 는 대역 글자(전체 저장 전 기록).
- 상세 `/svc/api/admin/access/addr?ip&window` → 목록 값 + `geo{country, region, city, lat, lon, asn, org, net, ptr, approx}`·`verifiedCrawler`·`hourly`·`kinds`·`status`·`agents`·`paths`·`pairs`·`recent[{ts, path, status, kind, browser, os, device, referer, flags[]}]`·`partial`. 잘못된 ip 는 400 `bad_ip`.

### 3.2 방문자 페이지
- 머리 큰 수 넷: IP 수 · 봇 판정 비율(요청 기준 — bot+suspect) · 데이터센터 요청 비율 · 오늘 처음 본 IP. 보기 단추 'IP 별 | 대역 별'(주소 `view=ip|band`, 기본 ip). `since` 가 있으면 머리 아래 흐린 한 줄 'IP 전체는 MM-DD HH:MM 부터 — 그 전 기록은 대역(가림)으로 보인다'.
- 거르기 칩: 판정(봇·의심·사람·모름·운영자 — 여럿), 망 종류, 나라, '가린 기록 빼기', 찾기(`1.2.3.`).
- 표(IP 별): IP(고정폭 글꼴, 가린 행은 흐린 '대역' 표시) · 판정 배지(모양+글 — ■ 봇·▲ 의심·● 사람·○ 모름·◆ 운영자, 상태색은 066 규칙) · 점수 막대 · 위치('🇰🇷 서울 · KT' 꼴 — 나라 국기·도시(없으면 지역)·망 사업자 이름) · 요청 막대 · 브라우저로 연 페이지(082 이름표) · 마지막 시각 · 작은 선. 행 title 에 근거 상위 셋. 정렬은 서버 정렬(요청·페이지·마지막·점수) + 그 밖 열은 불러온 행 안에서, 200행씩.
- 행을 누르면 066 상세 판(`#detail=addr:<ip>`).

### 3.3 IP 상세 판
- 머리: 066 판 머리(여섯 페이지가 같은 글자) 그대로 — 제목 IP · 단위 위치 글 · ◀ ▶(표 차례) · 기간 · CSV(최근 요청 100줄, 신호 칸 포함). 판 맨 위 첫 줄에 IP(복사 단추) · 판정 배지 · 점수.
- **판정 근거** 칸(맨 위): 근거 목록 — 가중치 큰 순, 행마다 '+70 검색 로봇 이름(Googlebot)'·'−40 자바스크립트가 돈 페이지 3번' 꼴(양수는 봇 쪽 빨강 계열 막대, 음수는 사람 쪽 청록 막대 — 폭 = |가중치|/70), 바닥에 '점수 n → 봇(60 이상)'. 확인된 검색 로봇이면 '✓ 주소 확인된 Google 로봇', 실패면 '✕ 이름만 Google — 주소 확인 실패'.
- **어디** 칸: 나라 국기·이름 · 지역 · 도시('대략' 흐린 글 — 통신사 거점일 수 있다) · 망 사업자(org)·AS 번호 · 망 종류 · 역방향 DNS 이름(없으면 '–') · 위도·경도(지도는 넣지 않는다 — 외부 자원 0). 바닥에 DB-IP 표시 링크(039 규칙).
- 그 아래 067 상세와 같은 것: 시간별 요청 막대·종류 띠·상태 띠·브라우저 정보·경로 상위·첫/마지막 페이지·최근 요청 100줄(줄마다 flags 칩 — 탐색·404·자원·JS·WS, '경로' 다음 칸 '페이지·파일' 배지 — 직접 연 페이지·함께 받은 파일·따로 부른 파일, '페이지·파일'·'종류' 칸 머리 '?' — 082 §3.2).
- 400 `bad_ip` 는 판 안 한 줄. `partial` 은 '일부만 읽음' 배지.

### 3.4 엣지
- 069 배포 전(404)·연결 안 됨: 'IP 별' 은 066 부분 상태 배지와 빈 표, '대역 별' 은 그대로 쓸 수 있다.
- 글자는 모두 textContent(066 XSS 규칙) — UA·경로·PTR 이름·org 이름 포함.
- 360px 가로 넘침 없음 — 표는 카드 안 가로 스크롤, 위치 칸은 줄임표.

## 4. 검증
- node(066 방식): 표 행(판정 배지·위치 글·가린 행), 거르기 → 부르는 주소, 보기 단추·주소 왕복, 근거 목록 차례·막대 폭·점수 줄, 확인된/사칭 로봇 줄, 어디 칸(null 섞임), 400·partial, XSS(근거 text·PTR·org 에 `<img onerror>`).
- 정적·가짜 피드 서버(addrs·addr — 경우 ok·old(067·068·069 전)·noaddrs(069 만 전 — 대역 별은 그대로)·xss·many), CSP 그대로.
- 기존 전부: `cd server && ruff check . && ruff format --check . && pytest -q`, `node --check web/admin/*.js`, `cd web && npm run lint && npm run build`.
- 설계 세션(헤드리스 Chrome): 방문자 페이지 1440·1024·390, 상세 판, CSP 위반 0.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && uv sync --extra dev
uv run --extra dev ruff check .            # All checks passed!
uv run --extra dev ruff format --check .   # 431 files already formatted
uv run --extra dev pytest -q               # 2613 passed, 1 skipped (새 test_admin_addrs 7·test_admin_addr_sheet 9, 고친 pages·detail·fake(경우 열하나 × 방문자 대역 별 보기)·xss(IP 별·대역 별)·screen·scripts·contract_visits)
node --check web/admin/*.js                # 31 파일 통과
cd web && npm ci && npm run lint && npm run build   # oxlint 0 · vite build 통과
# 가짜 피드 서버 스모크(127.0.0.1:18070): /visitors.html 200 + CSP·X-Frame-Options DENY·no-store 그대로,
# addrs ok(47행·since)·addr?ip=nope 400 bad_ip·/__scenario/noaddrs 뒤 addrs 404·ips 200, addrs.js·addrdetail.js·addrmodel.js text/javascript
```
설계 세션 몫(헤드리스 Chrome — 방문자 1440·1024·390, IP 판, CSP 위반 0)은 하지 않았다(실행 세션은 브라우저를 쓰지 않는다).

## 6. 갱신할 문서
- `docs/context/status.md`·`architecture.md`(방문자 페이지 IP 별)·`dev-setup.md`(가짜 피드 경우).
- `CLAUDE.md` — 인덱스 070 DONE. `docs/specs/066-admin-renewal.md` §3.3 방문자 줄에 '070 — IP 별 기본'.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것: `web/admin/addrmodel.js`(IP 검사·쪽 요청·큰 수·표 행·근거 차례·점수 줄·검색 로봇 줄·어디 칸 — DOM 없음)·`addrs.js`(IP 표·거르기·큰 수·since 줄)·`addrdetail.js`(IP 판 + 대역 판과 같이 쓰는 시간별 그래프·쓰임 칸·최근 요청 칸), `visitors.html`(보기 단추·IP 큰 수·IP 상자·since 줄·DB-IP 표시 둘)·`visitors.js`(보기 `view=ip|band`·두 보기 카드·판 `addr:`), `common.js`(경로 `addrs`·`addr`), `names.js`(`flagOf`·`countryName`), `ipmodel.js`(쪽 모으기 `loadedPages`), `ui.js`(표 `rowTitle`), `admin.css`(판정 배지 ◆·점수 막대·위치 줄임표·근거 막대·신호 칩·판 맨 위 칸), `web/scripts/admin-fake-feeds.py`(addrs·addr, 경우 noaddrs·many), 시험 `server/tests/test_admin_addrs.py`·`test_admin_addr_sheet.py` + 기존 admin 시험 일곱 고침, 문서(status·architecture·dev-setup·066 §3.3·CLAUDE.md).
- 추측한 지점:
  1. 판 머리 — 066 판 머리는 여섯 페이지가 같은 글자여야 한다(test_admin_screen). 그래서 IP(복사 단추)·판정 배지·점수는 판 맨 위 첫 줄(판정 근거 칸 머리)에 두고, 판 머리는 제목 IP·단위 위치 글·◀ ▶·기간·CSV 그대로. §3.3 글을 고쳤다. 버린 것: 여섯 페이지의 판 틀에 복사 자리 더하기(다른 페이지를 건드린다).
  2. 판정 근거·어디 칸 '맨 위' — 판 몸 격자의 사용자 칸을 `display: contents` 로 풀어 두 칸만 차례 −1(그래프 위), 쓰임 칸은 그래프 아래. 버린 것: 공통 판 틀의 칸 차례 바꾸기.
  3. 근거 차례 '가중치 큰 순' = 수 그대로 내림차순(+70 … −40, 같으면 code 글자 순), 행 title 의 '상위 셋' 도 같은 차례. 버린 것: |가중치| 큰 순.
  4. 점수 막대 폭 = 점수 ÷ 100(0~1 에서 자름 — 음수는 0), 색은 판정 상태색. 운영자 ◆ 는 강조색(`--accent` — 066 상태 4색 밖), 모름 ○ 는 빈 원.
  5. 위치 글 — 국기 + 도시(없으면 지역, 없으면 나라 이름) · 망 사업자(없으면 뺌), 다 없으면 '–'. 어디 칸은 geo 키가 있으면 그 값(null = '–'), geo 에 없는 키만 목록 값. '대략' 은 도시가 있고 `approx` 가 false 가 아닐 때, 위도·경도는 소수 넷째 자리·하나라도 없으면 '–', AS 번호는 수면 'AS' + 수.
  6. 검색 로봇 이름 — 근거 글·브라우저 정보에서 Googlebot → Google, bingbot → Bing, Yeti → 네이버, Daum → Daum, 못 찾으면 '검색'. 버린 것: 069 응답에 이름 키 더하기(응답 키 — 069 몫).
  7. 큰 수 'IP 수' = `total`(가린 대역 행 포함 — 단위 글 '가린 대역 포함'), 봇 판정·데이터센터 비율·오늘 처음 본 IP 는 불러온 행 기준(더 있으면 '≈'·'불러온 n개 기준' — 066 대역 규칙과 같음).
  8. since 줄은 IP 별 보기에서만, KST, 초·ms 둘 다 받는다(first·last 도 067 처럼).
  9. 거르기 — 망 종류·나라는 두 보기가 같은 주소 키로 같이 쓰고, 판정·가린 기록 빼기는 IP 별만, '봇만 있는 대역 빼기' 는 대역 별만(칩 줄에는 모두 보인다). 찾기 칸은 보기마다 따로(주소에 싣지 않음). 판정 여럿은 069 순서로 `verdict=bot,suspect`.
  10. 판을 여는 행 — IPv4·IPv6(소문자 16진·콜론·IPv4 꼬리)·067 대역 꼴(가린 행). '(없음)'·'(기타)'·꼴 아닌 글은 열지 않는다(정규 꼴 검사는 서버 400 — 판 안 한 줄). 판정 열 정렬은 판정 이름 글자 순(불러온 행 안).
  11. 신호 칩 — 069 flags 중 probe '탐색'(빨강)·404(주황)·asset '자원'·js 'JS'·ws 'WS', 목록 밖 값은 버림. CSV 에 '신호' 칸(글).
  12. 복사 — `navigator.clipboard` 가 없거나 실패면 단추 글 '복사 안 됨'. 응답 ok 인데 IP 가 없으면 표 자리에 '자료 없음'.
  13. 가짜 피드 — old·missing 은 addrs·addr 도 404(069 전이기도 하다), 새 경우 noaddrs(069 만 전 — 대역 별은 그대로)·many(IP 1,200·IP 판 partial). §4 를 고쳤다. 버린 것: old 의 뜻 바꾸기(067·068 전 시험이 기댄다).
  14. 069 nginx 정확 일치 location 둘은 069 PR 몫 — test_admin_pages 는 그 둘이 아직 없으면(관리자 server 의 정적 `location /`) 분기 단언을 건너뛰고, 069 머지 뒤에는 저절로 단언한다.
  16. 꼴이 아닌 IP 가 주소에 오면(`#detail=addr:nope`) 판을 열지도 부르지도 않는다(066 §3.4 '목록 밖 id 는 무시' 와 같음). 꼴은 맞는데 서버가 400 `bad_ip` 면 판 안 한 줄('이 IP 는 상세를 볼 수 없다 — IP 꼴이 아니다(bad_ip)'). 가짜 피드로 보려면 화면 검사는 지나고 가짜 서버는 거절하는 `#detail=addr%3A%3A%3Affff%3A1.2.3.4`. 버린 것: 꼴이 아닌 글도 판을 열고 400 줄을 보이기(쓸모없는 호출).
  17. 설계 세션 브라우저 확인(2026-10-14)에서 고친 것 — 판정 근거·어디 칸의 클래스 `top` 이 좁은 화면 머리 줄 `.top { display: none }` 과 겹쳐 실제 브라우저에서 두 칸이 사라졌다(node 가짜 DOM 은 CSS 를 모른다). 클래스를 `addr-top` 으로 바꾸고, IP 판·표가 만드는 클래스를 고르는 admin.css 규칙에 display: none 이 없는지 보는 정적 시험을 더했다.
  15. 069 피드 코드가 아직 없다(PR #136 초안 — 이 세션 끝까지 머지 안 됨) — §3.1 계약과 069 스펙만으로 만들었고, addr 응답에도 067 상세처럼 `state`·`code`·`window` 가 있다고 봤다.
- 남은 빚: 069 머지 뒤 진짜 피드 코드 응답으로 방문자 IP 별·IP 판을 그리는 대조 시험(066 의 test_admin_contract_visits 꼴)과 위 15 확인. 설계 세션 브라우저 확인(1440·1024·390, IP 판, CSP). '봇만 있는 대역 빼기' 칩이 IP 별에서도 칩 줄에 남는 것(동작 없음).
