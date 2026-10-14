# 070 — admin-ip-view

상태: TODO | 의존: 066(관리자 틀·상세 판)·069(피드 `/admin/access/addrs`·`/admin/access/addr` — 머지 전이면 가짜 피드로 만들고 시험한다).

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
- 표(IP 별): IP(고정폭 글꼴, 가린 행은 흐린 '대역' 표시) · 판정 배지(모양+글 — ■ 봇·▲ 의심·● 사람·○ 모름·◆ 운영자, 상태색은 066 규칙) · 점수 막대 · 위치('🇰🇷 서울 · KT' 꼴 — 나라 국기·도시(없으면 지역)·망 사업자 이름) · 요청 막대 · 사람 모양 페이지 · 마지막 시각 · 작은 선. 행 title 에 근거 상위 셋. 정렬은 서버 정렬(요청·페이지·마지막·점수) + 그 밖 열은 불러온 행 안에서, 200행씩.
- 행을 누르면 066 상세 판(`#detail=addr:<ip>`).

### 3.3 IP 상세 판
- 머리: IP(복사 단추) · 판정 배지 · 점수 · ◀ ▶(표 차례) · 기간 · CSV.
- **판정 근거** 칸(맨 위): 근거 목록 — 가중치 큰 순, 행마다 '+70 검색 로봇 이름(Googlebot)'·'−40 자바스크립트가 돈 페이지 3번' 꼴(양수는 봇 쪽 빨강 계열 막대, 음수는 사람 쪽 청록 막대 — 폭 = |가중치|/70), 바닥에 '점수 n → 봇(60 이상)'. 확인된 검색 로봇이면 '✓ 주소 확인된 Google 로봇', 실패면 '✕ 이름만 Google — 주소 확인 실패'.
- **어디** 칸: 나라 국기·이름 · 지역 · 도시('대략' 흐린 글 — 통신사 거점일 수 있다) · 망 사업자(org)·AS 번호 · 망 종류 · 역방향 DNS 이름(없으면 '–') · 위도·경도(지도는 넣지 않는다 — 외부 자원 0). 바닥에 DB-IP 표시 링크(039 규칙).
- 그 아래 067 상세와 같은 것: 시간별 요청 막대·종류 띠·상태 띠·브라우저 정보·경로 상위·첫/마지막 페이지·최근 요청 100줄(줄마다 flags 칩 — 탐색·404·자원·JS·WS).
- 400 `bad_ip` 는 판 안 한 줄. `partial` 은 '일부만 읽음' 배지.

### 3.4 엣지
- 069 배포 전(404)·연결 안 됨: 'IP 별' 은 066 부분 상태 배지와 빈 표, '대역 별' 은 그대로 쓸 수 있다.
- 글자는 모두 textContent(066 XSS 규칙) — UA·경로·PTR 이름·org 이름 포함.
- 360px 가로 넘침 없음 — 표는 카드 안 가로 스크롤, 위치 칸은 줄임표.

## 4. 검증
- node(066 방식): 표 행(판정 배지·위치 글·가린 행), 거르기 → 부르는 주소, 보기 단추·주소 왕복, 근거 목록 차례·막대 폭·점수 줄, 확인된/사칭 로봇 줄, 어디 칸(null 섞임), 400·partial, XSS(근거 text·PTR·org 에 `<img onerror>`).
- 정적·가짜 피드 서버(addrs·addr — 경우 ok·old(069 전)·xss·many), CSP 그대로.
- 기존 전부: `cd server && ruff check . && ruff format --check . && pytest -q`, `node --check web/admin/*.js`, `cd web && npm run lint && npm run build`.
- 설계 세션(헤드리스 Chrome): 방문자 페이지 1440·1024·390, 상세 판, CSP 위반 0.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md`·`architecture.md`(방문자 페이지 IP 별)·`dev-setup.md`(가짜 피드 경우).
- `CLAUDE.md` — 인덱스 070 DONE. `docs/specs/066-admin-renewal.md` §3.3 방문자 줄에 '070 — IP 별 기본'.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것:
- 추측한 지점:
- 남은 빚:
