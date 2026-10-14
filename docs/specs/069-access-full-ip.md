# 069 — access-full-ip

상태: DONE | 의존: main(067 대역 피드·039 DB-IP·038 줄 분류). 070(관리자 화면)이 이 스펙의 피드를 그린다.

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
- **가린 줄 / 전체 줄**: 전체 저장 시작 시각 = 창 안 파일에서 처음 본 '끝 옥텟이 0 이 아닌 IPv4' 줄의 시각(파일 캐시가 함께 기억 — 가린 기록에는 그런 줄이 없다). 그 시각 **전** 줄은 가린 줄(IPv4 /24·IPv6 /48 대역), **뒤** 줄은 전체 줄(끝이 `.0` 이어도 진짜 주소). 시작 시각이 없으면 모두 가린 줄. 끝 옥텟이 0 이 아닌 IPv4 와 뒤 80비트가 0 이 아닌 IPv6 는 가린 줄일 수 없어 늘 전체 줄이다. 시작 시각은 캐시가 읽는 범위(30일)의 파일에서 찾고, 파일 안에서 시작 시각 앞에 읽은 가린 꼴 줄의 행은 그 행의 첫 줄이 캐시 전체의 시작 시각 뒤면 전체 IP 행이다(실행 중 정함 — §7).
- 행 단위: 전체 줄은 IP 하나, 가린 줄은 대역 하나(`masked: true`) — 둘은 다른 행이다.
- 행마다 067 대역과 같은 값(요청·사람 모양 페이지·JS·WS·종류 여덟·상태 넷·짝 수·처음/마지막·spark·경로 상위·브라우저 정보 상위) + 판정용 신호(§3.4): 탐색 경로 줄 수, 404 수, 정적 자원(`/assets/`·`/landing/`·글꼴·아이콘) 수, robots.txt 수, 분당 최대 요청, 서로 다른 UA 수, 검색 유입(referer 가 검색 엔진) 수. 파일당 행 5만 상한(넘으면 `(기타)`, 067 과 같음). 응답 행에는 짝 수·`botPct` 가 없다(§3.5 키 그대로 — 상세의 `pairs` 는 067 의 짝 흐름이다). `pages`·`js` 는 067 처럼 종류 browser 의 페이지·clarity.js, 서로 다른 UA 는 행마다 파일 단위 16개까지, 분당 최대는 시 칸마다(창 안 칸의 최대).
- 메모리: 067 대역 칸과 같은 방식(파일 단위 상위 자르기). 지금 하루 기록(≈수백 IP)에서 30일 창이 +20MiB 를 넘지 않게.

### 3.3 위치·망 (DB-IP)
- **City Lite**(`https://download.db-ip.com/free/dbip-city-lite-YYYY-MM.csv.gz`, CC BY 4.0, 무키): 2026-10 판 gz 85,827,400B·풀면 692,751,220B, 열 `시작,끝,대륙,나라,지역,도시,위도,경도`(머리 줄 없음, 도시에 따옴표 있음), IPv4 3,620,434행, (나라·지역·도시) 166,890개. 설계 세션이 받은 사본 경로는 실행 프롬프트로 넘긴다.
- **ASN Lite** 의 조직 이름(039 가 버리던 것)을 IPv4 구간마다 남긴다(망 사업자 표시). 망 종류 판정(039 §3.4)은 그대로.
- City 는 크기 때문에 039 의 '메모리만' 대신: api 가 달마다 한 번 받아(이벤트 루프 밖·한 번에 하나·종료를 막지 않음 — 039 §3.3 과 같은 규칙) **간결한 이진 파일**로 만들어 named volume(`geo-cache`, serve 의 api 에만 — compose)에 두고, 기동 때는 그 파일을 읽어 올린다(2초 안). 유지 메모리 ≤ 60MB(구간 시작·끝·위치 번호 정수 배열 + 위치 문자열 표). 파일 쓰기는 그 볼륨 안 하나(옛 달 파일은 지운다). 받기 시간 상한 300초·압축 바이트 상한 150MB, 확인(행 수 1만~1,000만·정렬·겹침 없음·나라 두 글자)은 039 꼴. 실패하면 옛 판 그대로, 판이 없으면 위치 칸 null. 캐시 디렉터리 `GEO_CACHE_DIR`(compose 가 api 에만 `/var/lib/geo`)가 없으면 받지도 읽지도 않는다. 기동 때(api lifespan)는 볼륨 파일만 데몬 스레드로 읽고 받지 않는다 — 받기는 IP 피드 둘이 판을 챙길 때(판이 없거나 지난달 판, 다시 받기 6시간·24시간·지난달 대신은 039 와 같다). 같은 위치(나라·지역·도시)의 좌표는 처음 본 값 하나다. 파일 쓰기가 실패해도 만든 판은 메모리에 올린다.
- 역방향 DNS(PTR): **상세(§3.6)에서만**, IP 하나에 한 번, 1초 시간 초과, 결과(이름 또는 없음)를 24시간 메모리 캐시(최대 5,000개). 목록에서는 하지 않는다. 묻기는 데몬 스레드(동시에 넷까지 — 넘으면 null)에서 하고, 시간 초과·해석기 오류는 캐시하지 않는다.
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
- 뜻: `ua_bot` = 038 종류 search·ai·preview 이거나 UA 에 bot/crawler/spider(이름을 text 에). `ua_tool` = 038 종류 tool·scanner. `probe` = 038 탐색 경로 줄 ≥ 1. `no_js` = 사람 모양 페이지 ≥ 3 인데 JS 신호 0. `cloud` = 망 종류 cloud. `burst` = 어느 1분에 요청 ≥ 60. `many_404` = 404 ≥ 10 이고 요청의 30% 이상. `no_assets` = 페이지 ≥ 3 인데 정적 자원 0. `robots` = robots.txt ≥ 1. `ua_many` = 서로 다른 UA ≥ 5. `js` = JS 신호 ≥ 1. `ws` = WS 연결(101) ≥ 1. `assets` = 정적 자원 ≥ 1. `search_ref` = 검색 엔진에서 들어온 페이지 ≥ 1. `no_js`·`no_assets` 의 페이지와 `search_ref` 는 사람 모양(종류 browser) 페이지 줄, 정적 자원 = GET 200·304 인 `/assets/`·`/app/assets/`·`/landing/` 아래·`.woff2`·`.woff`·`.ttf`·`.otf`·`.ico`·`/favicon.svg`·`/apple-touch-icon.png`·`/icon-192.png`·`/logo-512.png`, 검색 엔진 = 038 채널 `search` 의 호스트. 근거 글은 표 차례.
- 확인된 검색 로봇: UA 가 Googlebot·bingbot·Yeti(네이버)·Daum 이면 상세에서 PTR → 정방향 확인(이름이 googlebot.com·google.com·search.msn.com·naver.com·daum.net 로 끝나고 그 이름의 A 가 같은 IP)으로 `verifiedCrawler: true|false`. false 면 근거 `fake_crawler`(가중치 +30, text '검색 로봇 이름을 쓰지만 주소 확인 실패'). 목록에서는 하지 않는다(null). 로봇마다 자기 도메인만 맞는다(Googlebot → googlebot.com·google.com, bingbot → search.msn.com, Yeti → naver.com, Daumoa → daum.net — 038 종류 search 일 때만). PTR 레코드가 없으면 false, 묻기 실패(1초 넘음·오류)는 null.
- 운영자 흔적(038 operator 줄이 하나라도 있는) 행은 `verdict: "operator"`·`score: null`·`reasons: []`(점수 계산 안 함). 판정 차례는 operator → `(없음)`·요청 < 3 은 unknown → 점수.
- 가린 행(`masked`)도 같은 규칙(대역 하나를 한 행으로 — text 끝에 '(대역)').

### 3.5 `GET /admin/access/addrs?window=24h|7d|30d&net=&country=&verdict=&q=&masked=0|1&sort=requests|pages|last|score&limit=200&offset=0`
- api 역할, 관리자 nginx `location = /svc/api/admin/access/addrs` 만(공개 404 — 067 꼴).
- 응답(no-store): `{state, code, window, total, since, rows:[{ip, masked, country, region, city, asn, org, net, requests, pages, js, ws, kinds, status, first, last, spark, topPaths, topAgents, score, verdict, reasons}]}`. `since` = 전체 저장 시작 시각(없으면 null). `ip` 는 전체 IP 또는 가린 행이면 대역 글자(`1.2.3.0/24`). 거르기: net·country·verdict(쉼표 여럿)·q(앞부분 글자)·masked(0 = 전체 IP 행만). 정렬 큰 순·같으면 ip 글자순. 60초 캐시. 067 의 인자 검사 규칙(모양 틀린 값은 기본값). `since`·`first`·`last` 는 epoch 초. `masked=0` 은 가린 행(`/` 가 든 ip)을 뺀다 — 그 밖 값·없음은 모든 행. `verdict` 는 목록 밖 낱말을 버리고 남는 게 없으면 거르지 않는다. 점수 정렬에서 `operator`(null)는 맨 뒤. ok 가 아니면 `total`·`since`·`rows` 는 null(067 꼴).

### 3.6 `GET /admin/access/addr?ip=<IP 또는 대역>&window=`
- 관리자 nginx `location = /svc/api/admin/access/addr`. `ip` 검사: IPv4·IPv6 주소(정규 꼴) 또는 067 대역 글자 — 아니면 400 `bad_ip`(067 `bad_band` 꼴). 400 본문은 `{"error":{"code":"bad_ip","message":…,"detail":null}}`·no-store.
- 응답: 목록 행의 값 전부 + `geo{country, region, city, lat, lon, asn, org, net, ptr, approx}` + `verifiedCrawler` + 067 상세와 같은 `hourly`·`kinds`·`status`·`agents`·`paths`·`pairs`·`recent`(100줄 — 각 줄에 그 줄의 판정 신호 표시 `flags`: probe·404·asset·js·ws) + `partial`. 키 차례는 `state, code, window, ip`, 목록 행 값(ip 뺌), `geo, verifiedCrawler, hourly, agents, paths, pairs, recent, partial` — `kinds`·`status` 는 행 값 자리에 한 번. `flags` 는 낱말 목록(`["probe","404"]`). 가린 행의 `geo.ptr`·`verifiedCrawler` 는 null, 상세 점수는 `fake_crawler` 를 더해 목록과 다를 수 있다.
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
# 2026-10-14 KST, worktree marketLens-exec-069 (feat/069-access-full-ip — 32926a4 위). uv 는 Bash 샌드박스 밖, 만든 server/uv.lock 은 끝에 지웠다
cd server && uv sync --extra dev
cd server && uv run --extra dev ruff check . && uv run --extra dev ruff format --check . && uv run --extra dev pytest -q
#   시작 전(32926a4): All checks passed! · 429 files already formatted · 2594 passed, 1 skipped
#   끝: All checks passed! · 448 files already formatted · 2719 passed, 1 skipped (건너뜀은 039 test_geo_perf — DBIP_DIR 없음)
cd web && npm ci && npm run lint && npm run build   # oxlint 종료 0(지적 없음) · tsc -b && vite build ✓ (dist 에 privacy-20261014.html)
# caddy — OrbStack Docker 29.4, caddy:2-alpine: caddy validate --config caddy/Caddyfile → Valid configuration
#   같은 이미지에 Caddyfile 의 전역·access_log 조각 + http://:8080 블록으로 띄워 한 줄 요청:
#   remote_ip·client_ip = 127.0.0.1(전체), uri /x?tab=gap(s.q 지움), request.headers 없음, ua 남음
# nginx — nginx:1.27-alpine, 두 템플릿을 20-envsubst-on-templates.sh 로 그린 뒤 nginx -t: syntax is ok · test is successful
#   같은 컨테이너에 경로를 돌려주는 가짜 api(:8000)를 더해 관리자 server(:8081):
#   /svc/api/admin/access/addrs?window=7d&verdict=bot,suspect&masked=0 → /admin/access/addrs?window=7d&verdict=bot,suspect&masked=0
#   /svc/api/admin/access/addr?ip=203.0.113.7&window=24h → /admin/access/addr?ip=203.0.113.7&window=24h (IPv6·%2F 대역도 그대로)
#   Sec-Fetch-Site: cross-site → 403 · 공개 :80 /api/admin/access/addrs·addr·/svc/… → 404 · 관리자 접속 기록에는 403 줄만(넘긴 줄 없음)
# 망가뜨려 실패 보기 — 짝 열쇠를 가린 IP 로 바꾸기 전 코드로 test_access_masked_pairs: 3 failed
# City 실측(레포 밖 스크립트, 로컬 macOS arm64·Python 3.12, 받은 2026-10 판 gz 85,827,400B):
#   만들기 5.5초 · 구간 3,620,434·위치 166,890 · 배열+위치 표 50.0MB · 만들 때 최대 RSS 21 → 101MiB
#   파일 쓰기 0.02초·50.0MB · 읽어 올리기 0.01초(페이지 캐시) · 유지 52.8MB(tracemalloc)·읽는 동안 최고 60.6MB · 조회 1.7µs
# IP 칸 부담(레포 밖 bench, tracemalloc 켬, 3회 최솟값 — 끄고/켜고):
#   회전 파일 하나 IP 300(90,000줄·39MiB) 3.34 → 3.69초 · 남는 메모리 0.3 → 3.5MiB
#   줄마다 다른 IP 50,001(21MiB) 1.84 → 2.35초 · 5.7 → 64.5MiB (드문 칸을 늦게 만들기 전 88.0MiB)
#   지금 규모 30일(하루 700줄·IP 150) 첫 채움 0.56 → 0.84초 · 3.2 → 14.8MiB · 다음 목록 0.04초
```

## 6. 갱신할 문서
- `docs/context/status.md`·`architecture.md`(IP 전체 저장·피드 둘·City 캐시 볼륨)·`db.md`(geo-cache 볼륨)·`dev-setup.md`.
- `CLAUDE.md` — 인덱스 069 DONE.
- `docs/specs/027-observability.md`(IP 자르기 문장)·`039-access-geo.md`(조직 이름 남김·City)·`067-access-ips.md`(머리에 069 한 줄).
- `docs/runbooks/cloudwatch.md` — 로그에 IP 전체.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것:
  - caddy: `caddy/Caddyfile`(도메인 접속 로그의 `ip_mask` 둘 삭제 — 기본 로거는 IP 지움 그대로).
  - server `app/features/admin/`: 새 `access_addrs.py`(파일마다 IP 칸)·`access_addrs_rows.py`(행 값·위치·판정)·`access_addrs_page.py`(목록)·`access_verdict.py`(근거·점수)·`access_addr.py`(상세)·`access_dns.py`(PTR·로봇 확인)·`geo_city.py`(City 표·파일)·`geo_city_fetch.py`(City 받기·볼륨), 고침 `access_hours.py`(IP 칸·짝 해시는 가린 IP)·`access_bands.py`(`masked_ip`·`band`)·`access_ip.py`(`BandScan` 고리·줄 표시)·`access_cache.py`(`addrs`·`since`)·`geo_table.py`(ASN 조직 표·`place`)·`geo_kinds.py`(문장)·`visits.py`·`router.py`, `app/core/config.py`(`geo_cache_dir`), `app/main.py`(lifespan `city.boot()`).
  - 인프라: `docker-compose.yml`(api 에 `GEO_CACHE_DIR`·named volume `geo-cache`), `web/nginx-admin.conf`(정확 일치 둘).
  - web: `public/privacy.html`(v6), 새 `public/privacy-20261014.html`(v5 사본).
  - 테스트: 새 `test_access_masked_pairs.py`·`test_geo_place.py`·`city_fakes.py`·`test_geo_city.py`·`test_geo_city_fetch.py`·`test_access_verdict.py`·`test_access_addrs.py`·`test_access_addrs_query.py`·`test_access_addrs_memory.py`·`test_access_addr.py`·`test_access_dns.py`, 고침 `access_fakes.py`(`addrs`·`addr`·가짜 해석기)·`conftest.py`(City 스레드·시스템 DNS 막음)·`tests/test_observability.py`·`test_admin.py`·`test_deploy.py`·`test_role.py`·`test_privacy.py`.
  - 문서: 이 스펙, `CLAUDE.md`, `docs/context/status.md`·`architecture.md`·`db.md`·`dev-setup.md`·`product.md`, `docs/specs/027`·`039`·`067`, `docs/runbooks/cloudwatch.md`.
- 추측한 지점 (사람 없이 가장 보수적인 쪽 — 버린 쪽):
  1. City 올리는 때: 기동 때는 볼륨 파일만 읽고(데몬 스레드 — 기동을 기다리게 하지 않음), 받기는 IP 피드가 판을 챙길 때. 버림: 기동 때 받기(035 '요청이 없으면 부르지 않는다'와 어긋나고 배포마다 85MB·+80MiB).
  2. City 좌표: (나라·지역·도시)마다 처음 본 좌표 하나 — 2026-10 판은 그 셋이 166,890·좌표까지 422,707. 버림: 행마다 좌표(+29MB — 60MB 넘음)·좌표까지 위치 표(만들 때 +45MB).
  3. `GEO_CACHE_DIR` 없음 = City 끔(받지 않음, 위치 칸 null). 버림: 파일 없이 메모리에만 만들기. 파일 쓰기 실패 = 만든 판은 메모리에 올리고 WARNING. 버림: 버리기.
  4. 전체 저장 시작 시각은 캐시가 읽는 범위(30일)의 파일 전체에서, 파일 안 시작 시각 앞 '가린 꼴' 행은 그 행 첫 줄이 캐시 전체 시작 시각 뒤면 전체 IP. 버림: 창 안 파일만(앞 파일이 알려 준 진짜 `.0` 을 대역으로)·시작 시각이 바뀔 때마다 캐시 다시 만들기.
  5. 끝 옥텟이 0 이 아닌 IPv4·뒤 80비트가 0 이 아닌 IPv6 는 시작 시각 앞이어도 전체 줄. 버림: 글자대로 '앞 줄은 모두 가린 줄'(배포 직후 같은 파일의 IPv6 를 대역으로 접는다).
  6. 행 키는 §3.5 그대로 — 짝 수·`botPct` 없음(상세 `pairs` 와 이름이 겹친다). 버림: §3.2 의 '짝 수' 를 `pairs` 로 싣기.
  7. 신호 뜻: `pages`·`js`·`search_ref`·`no_js`·`no_assets` 는 종류 browser 의 페이지 줄(067 `pages` 와 같음), 정적 자원은 종류와 무관한 GET 200·304(목록은 §3.4). `ua_bot` 줄 = 038 종류 search·ai·preview 또는 UA 에 bot·crawler·spider, `ua_tool` = 종류 tool·scanner(글자대로 — bot 낱말 UA 는 038 에서 scanner 라 둘 다 맞는다).
  8. 운영자 = operator 줄이 하나라도 있는 행, `score: null`·`reasons: []`. 버림: 점수 0·과반 규칙. `(없음)`·요청 < 3 은 unknown 이되 점수·근거는 싣는다.
  9. 인자: `masked=0` 만 가린 행 빼기, `verdict` 쉼표 목록(목록 밖 낱말 버림·모두 틀리면 거르지 않음), `sort=score` 에서 operator 는 맨 뒤, `since` 는 epoch 초(067 `first`·`last` 와 같음).
  10. 서로 다른 UA 는 행마다 파일 단위 16개까지(창 근사 — 067 topPaths 와 같은 방식), 1분 최대는 행마다 지금 분의 수(줄이 대개 시각 순)를 시 칸의 최대로.
  11. 검색 로봇 확인은 로봇마다 자기 도메인만, 038 종류 search 일 때만 주장으로 본다(인앱 DaumApps 는 아님). PTR 없음 = false, 1초 넘음·오류 = null(캐시 안 함), 정방향은 getaddrinfo(A·AAAA). 묻기는 데몬 스레드·동시에 넷. 버림: 기본 실행기(종료 때 기다림)·dnspython(새 라이브러리).
  12. 상세 줄 고르기: 시작 시각은 목록 캐시 값(`AccessLog.since`), 없으면 훑으며 처음 본 그런 줄. 가린 행 상세 = 시작 시각 앞 가린 꼴 줄 중 대역이 같은 것, IP 행 상세 = 같은 주소의 전체 줄. 키 차례와 `flags`(낱말 목록)는 §3.6 에 적었다.
  13. 038·067 짝 열쇠·067 상세의 짝 흐름은 줄 IP 를 /24·/48 로 다시 자른 글자(`masked_ip` — caddy `ip_mask` 출력과 같은 글자)로. 다른 줄은 바꾸지 않았다.
  14. 봇 이름 찾기를 `[…]*bot[…]*` 정규식에서 '낱말 찾고 앞뒤 넓히기'로 — 긴 영숫자 UA 에서 되짚기가 제곱으로 늘어(038 의 긴 UA 메모리 테스트가 25초) 공격 UA 가 api 를 붙들 수 있었다.
  15. 처리방침 v6 글: 스펙의 (라)에 '대역 보기는 그대로'·봇 판단 근거 낱말·'위치는 대략, 주소(번지)는 알 수 없음'·역방향 DNS 는 '그 IP 주소만 DNS 에 물어 찾음' 을 넣었다(묻는 것 자체가 IP 를 DNS 로 보낸다 — 숨기지 않음). 스펙에 없던 문장도 거짓이 되어 고쳤다: 한눈에 보기('IP 끝자리 지움'), 2절 첫 문단(항목·두 곳 모두 IP 전체·시행 전 줄은 앞부분만), (나)(요약은 조직 이름을 넣지 않음 — (라)는 보임), 8절('끝자리를 지워 가려내기 어렵다' → 찾는 데 필요한 것), 9절('IP 앞부분만'). 1절은 067 의 '보안(봇·스캐너 대응)' 이 있어 그대로. 대조표 일곱 행, 같은 날 두 번째 판 표기(060 꼴), v5 사본 `privacy-20261014.html`(제목 '(2026년 10월 14일 판)'·'(그날 먼저 게시한 판)', 067 사본 꼴 — 스타일은 v4 사본 것).
  16. ASN AS 번호는 4바이트(2^32−1) 넘으면 `bad_data`(배열 칸). CLAUDE.md 027 행의 'IP /24' 를 'IP 전체' 로 고쳤다(인덱스가 동작과 어긋나지 않게).
  17. 커밋: 첫 커밋에 uv.lock 이 섞여 바로 고쳐 다시 올렸고(force-with-lease), 300줄을 넘은 커밋 둘(City 표·IP 목록)을 나눠 다시 올렸다 — 이 draft 브랜치에서만. v5 사본 커밋(299줄)에서는 방침 테스트가 빨갛다(사본을 잇는 v6 이 다음 커밋).
- 남은 빚:
  - 운영 확인(설계 세션): caddy reload 뒤 첫 IP 전체 줄, 두 피드 200·공개 404, serve 의 City 받기 시간·최대 메모리·유지 메모리(로컬 5.5초·+80MiB·53MB — t4g.micro 는 몇 배 느릴 것), `geo-cache` 볼륨의 파일 하나.
  - 메모리 최악: 067 대역 칸 + 069 IP 칸이 파일 하나에 줄마다 다른 IP 5만이면 +64.5MiB(IP)·+67MiB(대역) — 몰리는 날 serve 에 부담(관리자 화면을 열 때만, 1시간 비움). 줄이려면 상한을 낮추거나 줄 하나뿐인 행을 납작하게.
  - 시작 시각 옮김: 시작 시각을 품은 파일이 읽는 범위(30일) 밖으로 나가면 다음 파일의 첫 그런 줄로 옮겨 가 그 사이 진짜 `.0` 주소 줄은 대역으로 보인다(드묾).
  - 처리방침 v6 시행일 2026-10-14(같은 날 두 번째 판)는 PR 날 짐작 — 다른 날 머지하면 `<time>` 넷·머리 차례·sitemap·test_privacy. IP 전체 저장·IP 별 보기·역방향 DNS(IP 를 DNS 로 묻는 것)의 법률 확인은 사람(게시된 문구로).
  - 화면은 070. 070 이 이 응답 키를 그린다 — 키를 바꾸면 이 스펙부터.
