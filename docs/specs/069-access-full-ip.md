# 069 — access-full-ip

상태: TODO | 의존: main(067 대역 피드·039 DB-IP·038 줄 분류). 070(관리자 화면)이 이 스펙의 피드를 그린다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-14): 관리자 화면에서 **IP 전체**를 보고, IP 마다 **봇인지 사람인지 판단과 그 근거**, **어디(나라·지역·도시·망 사업자)** 인지 본다. "관리자 페이지에서는 IP 를 가리지 않아도 된다"(사람 결정).
지금까지 caddy 접속 기록은 IP 를 /24·/48 로 잘라 저장했다(027, 2026-09-29~). 그 전 기록에서 IP 전체는 되살릴 수 없다 — 이 스펙은 **배포 뒤 들어온 요청부터** IP 전체를 남긴다.

## 2. 범위
- 만드는 것: caddy 접속 기록의 IP 전체 저장, api 피드 `/admin/access/addrs`·`/admin/access/addr`(IP 하나 단위), 봇 판정 규칙과 근거, DB-IP City Lite 위치(도시)·ASN 조직 이름·역방향 DNS, 관리자 nginx 경로 둘, 처리방침 v6, 테스트.
- 하지 않는 것: 화면(070). 공개 쪽 응답에 IP 를 싣는 것(관리자 피드만). 038 요약·방문자 세기·067 대역 값의 뜻 바꾸기(그대로 — §3.6).
- 바꾸는 기존 것: 027 caddy `access_log` 조각의 `ip_mask`, 039 의 '조직 이름은 버린다'(관리자 표시용으로 남긴다), 032·060·067 처리방침 문장.

## 3. 동작

### 3.1 저장 (caddy)
- 도메인 접속 기록(`/var/log/caddy/access.log`, 회전 100개·90일 그대로)의 `request>remote_ip`·`request>client_ip` 를 **자르지 않는다**. 검색어·헤더 지우기·폴링 제외·`log_skip` 은 그대로. 기본 로거(docker 로그)의 IP 두 필드 지우기(032)도 그대로.
- CloudWatch Logs 로 보내는 access.log(027)에도 IP 전체가 간다(보관 90일 그대로) — 처리방침 v6 에 적는다.
- 배포 뒤 `caddy reload`(027 워크플로 그대로)로 적용. 그 시각 앞 줄은 가린 IP, 뒤 줄은 IP 전체 — 한 파일 안에 섞일 수 있다.

### 3.2 IP 단위 집계 (038 파일 캐시에 더함)
- **가린 줄 / 전체 줄**: 전체 저장 시작 시각 = 창 안 파일에서 처음 본 '끝 옥텟이 0 이 아닌 IPv4' 줄의 시각(파일 캐시가 함께 기억 — 가린 기록에는 그런 줄이 없다). 그 시각 **전** 줄은 가린 줄(IPv4 /24·IPv6 /48 대역), **뒤** 줄은 전체 줄(끝이 `.0` 이어도 진짜 주소). 시작 시각이 없으면 모두 가린 줄.
- 행 단위: 전체 줄은 IP 하나, 가린 줄은 대역 하나(`masked: true`) — 둘은 다른 행이다.
- 행마다 067 대역과 같은 값(요청·사람 모양 페이지·JS·WS·종류 여덟·상태 넷·짝 수·처음/마지막·spark·경로 상위·브라우저 정보 상위) + 판정용 신호(§3.4): 탐색 경로 줄 수, 404 수, 정적 자원(`/assets/`·`/landing/`·글꼴·아이콘) 수, robots.txt 수, 분당 최대 요청, 서로 다른 UA 수, 검색 유입(referer 가 검색 엔진) 수. 파일당 행 5만 상한(넘으면 `(기타)`, 067 과 같음).
- 메모리: 067 대역 칸과 같은 방식(파일 단위 상위 자르기). 지금 하루 기록(≈수백 IP)에서 30일 창이 +20MiB 를 넘지 않게.

### 3.3 위치·망 (DB-IP)
- **City Lite**(`https://download.db-ip.com/free/dbip-city-lite-YYYY-MM.csv.gz`, CC BY 4.0, 무키): 2026-10 판 gz 85,827,400B·풀면 692,751,220B, 열 `시작,끝,대륙,나라,지역,도시,위도,경도`(머리 줄 없음, 도시에 따옴표 있음), IPv4 3,620,434행, (나라·지역·도시) 166,890개. 설계 세션이 받은 사본 경로는 실행 프롬프트로 넘긴다.
- **ASN Lite** 의 조직 이름(039 가 버리던 것)을 IPv4 구간마다 남긴다(망 사업자 표시). 망 종류 판정(039 §3.4)은 그대로.
- City 는 크기 때문에 039 의 '메모리만' 대신: api 가 달마다 한 번 받아(이벤트 루프 밖·한 번에 하나·종료를 막지 않음 — 039 §3.3 과 같은 규칙) **간결한 이진 파일**로 만들어 named volume(`geo-cache`, serve 의 api 에만 — compose)에 두고, 기동 때는 그 파일을 읽어 올린다(2초 안). 유지 메모리 ≤ 60MB(구간 시작·끝·위치 번호 정수 배열 + 위치 문자열 표). 파일 쓰기는 그 볼륨 안 하나(옛 달 파일은 지운다). 받기 시간 상한 300초·압축 바이트 상한 150MB, 확인(행 수 1만~1,000만·정렬·겹침 없음·나라 두 글자)은 039 꼴. 실패하면 옛 판 그대로, 판이 없으면 위치 칸 null.
- 역방향 DNS(PTR): **상세(§3.6)에서만**, IP 하나에 한 번, 1초 시간 초과, 결과(이름 또는 없음)를 24시간 메모리 캐시(최대 5,000개). 목록에서는 하지 않는다.
- 위치는 근사다(통신사 거점 도시일 수 있다) — 응답 `geo.approx: true`, 화면이 '대략' 으로 적는다. 주소(번지)는 IP 로 알 수 없다.

### 3.4 봇 판정 (IP 행마다 — 창 안 값으로)
점수 = 근거 가중치 합. `verdict`: 점수 ≥ 60 → `bot`, 30~59 → `suspect`, < 30 → `human`, 요청 < 3 → `unknown`(근거만). 근거마다 `{code, text, weight}` — text 는 숫자를 넣은 한국어 한 줄(예 '탐색 경로 12번(/.env·/wp-login.php…)').
| code | 가중치 |
|---|---|
| ua_bot | 70 |
| probe | 60 |
| ua_tool | 50 |
| no_js | 30 |
| cloud | 20 |
| burst | 20 |
| many_404 | 15 |
| no_assets | 15 |
| robots | 10 |
| ua_many | 10 |
| js | -40 |
| ws | -30 |
| assets | -15 |
| search_ref | -10 |
- 뜻: `ua_bot` = 038 종류 search·ai·preview 이거나 UA 에 bot/crawler/spider(이름을 text 에). `ua_tool` = 038 종류 tool·scanner. `probe` = 038 탐색 경로 줄 ≥ 1. `no_js` = 사람 모양 페이지 ≥ 3 인데 JS 신호 0. `cloud` = 망 종류 cloud. `burst` = 어느 1분에 요청 ≥ 60. `many_404` = 404 ≥ 10 이고 요청의 30% 이상. `no_assets` = 페이지 ≥ 3 인데 정적 자원 0. `robots` = robots.txt ≥ 1. `ua_many` = 서로 다른 UA ≥ 5. `js` = JS 신호 ≥ 1. `ws` = WS 연결(101) ≥ 1. `assets` = 정적 자원 ≥ 1. `search_ref` = 검색 엔진에서 들어온 페이지 ≥ 1.
- 확인된 검색 로봇: UA 가 Googlebot·bingbot·Yeti(네이버)·Daum 이면 상세에서 PTR → 정방향 확인(이름이 googlebot.com·google.com·search.msn.com·naver.com·daum.net 로 끝나고 그 이름의 A 가 같은 IP)으로 `verifiedCrawler: true|false`. false 면 근거 `fake_crawler`(가중치 +30, text '검색 로봇 이름을 쓰지만 주소 확인 실패'). 목록에서는 하지 않는다(null).
- 운영자 흔적(038 operator) 행은 `verdict: "operator"`(점수 계산 안 함).
- 가린 행(`masked`)도 같은 규칙(대역 하나를 한 행으로 — text 끝에 '(대역)').

### 3.5 `GET /admin/access/addrs?window=24h|7d|30d&net=&country=&verdict=&q=&masked=0|1&sort=requests|pages|last|score&limit=200&offset=0`
- api 역할, 관리자 nginx `location = /svc/api/admin/access/addrs` 만(공개 404 — 067 꼴).
- 응답(no-store): `{state, code, window, total, since, rows:[{ip, masked, country, region, city, asn, org, net, requests, pages, js, ws, kinds, status, first, last, spark, topPaths, topAgents, score, verdict, reasons}]}`. `since` = 전체 저장 시작 시각(없으면 null). `ip` 는 전체 IP 또는 가린 행이면 대역 글자(`1.2.3.0/24`). 거르기: net·country·verdict(쉼표 여럿)·q(앞부분 글자)·masked(0 = 전체 IP 행만). 정렬 큰 순·같으면 ip 글자순. 60초 캐시. 067 의 인자 검사 규칙(모양 틀린 값은 기본값).

### 3.6 `GET /admin/access/addr?ip=<IP 또는 대역>&window=`
- 관리자 nginx `location = /svc/api/admin/access/addr`. `ip` 검사: IPv4·IPv6 주소(정규 꼴) 또는 067 대역 글자 — 아니면 400 `bad_ip`(067 `bad_band` 꼴).
- 응답: 목록 행의 값 전부 + `geo{country, region, city, lat, lon, asn, org, net, ptr, approx}` + `verifiedCrawler` + 067 상세와 같은 `hourly`·`kinds`·`status`·`agents`·`paths`·`pairs`·`recent`(100줄 — 각 줄에 그 줄의 판정 신호 표시 `flags`: probe·404·asset·js·ws) + `partial`.
- 067 대역 피드·038 요약·방문자 세기는 그대로 — 짝 열쇠는 여전히 가린 IP(/24·/48)로 만든다(전체 저장 전후 숫자가 이어지게).

### 3.7 처리방침 v6 (시행일 = PR 올린 날 KST, 060 꼴)
- 2절 서버 접속 기록: 'IP(끝자리를 가린)' → 'IP 주소 전체'. (라): 운영자가 관리자 화면에서 IP 주소별로 접속 기록을 모아 보고, 자동 요청(봇) 여부를 판단하며, 공개 IP 위치 자료(DB-IP)로 대략의 나라·지역·도시와 망 사업자, 역방향 DNS 이름을 확인할 수 있다 — 관리자 로그인 뒤에만, 밖으로 보내지 않는다. 보유 90일 그대로. CloudWatch(AWS 서울) 로 보내는 기록에도 IP 전체가 담긴다.
- 1절 목적에 '자동 요청(봇)·공격 대응' 이 있는지 확인(067 이 '보안(봇·스캐너 대응)' 을 넣었다). 한눈에 보기·변경 안내·12절 시행 문장·이력·대조표, 같은 날(10-14) 두 번째 판이면 머리에 '(이날 두 번째 판)', 이전 판(v5) 사본 `privacy-20261014.html`(067·060 사본 꼴). 동의 안내 판은 그대로(서버 기록은 동의 대상이 아니다).

### 3.8 엣지
- 같은 IP 가 IPv4·IPv6 둘이면 다른 행. IP 없는 줄은 `(없음)` 한 행(판정 unknown).
- City 판이 없으면 지역·도시 null, ASN 이름이 없으면 org null — 판정은 망 종류만 쓰고 계속.
- PTR·확인 실패는 null(오류 아님).

## 4. 검증
- caddy: Caddyfile 계약 테스트(027 test_observability)를 '도메인 기록은 IP 자르지 않음·기본 로거는 지움'으로, 그 밖의 필터는 그대로.
- 집계: 가린 줄/전체 줄 판별(시작 시각 전후·`.0` 진짜 주소), 행 값, 신호 수, 상한.
- 판정: 근거마다 경계값(no_js 페이지 3·burst 60·many_404 10/30%·ua_many 5), 점수 → verdict 네 갈래, unknown·operator, 가린 행 text.
- 위치: City 이진 파일 만들기·읽기(가짜 작은 CSV), 조회 경계(구간 시작·끝·구간 사이 빈칸), 메모리 상한 근사, 실패 시 옛 판, 볼륨 안 한 파일.
- PTR·확인: 가짜 해석기(시간 초과·없음·정방향 불일치 → fake_crawler).
- 피드 둘: 인자·400·캐시·no-store·관리자 nginx 경로(공개 404), `nginx -t`.
- 처리방침 v6: test_privacy 꼴.
- 기존 전부: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md`·`architecture.md`(IP 전체 저장·피드 둘·City 캐시 볼륨)·`db.md`(geo-cache 볼륨)·`dev-setup.md`.
- `CLAUDE.md` — 인덱스 069 DONE.
- `docs/specs/027-observability.md`(IP 자르기 문장)·`039-access-geo.md`(조직 이름 남김·City)·`067-access-ips.md`(머리에 069 한 줄).
- `docs/runbooks/cloudwatch.md` — 로그에 IP 전체.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것:
- 추측한 지점:
- 남은 빚:
