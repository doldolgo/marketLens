# 067 — access-ips

상태: DONE | 의존: main(038·039·062·060). 066 admin-renewal 의 '방문자' 페이지가 이 스펙의 피드를 쓴다. 고치는 스펙 038·060(이 레포 주인 담당).

> 069(2026-10-14): caddy 가 IP 전체를 남긴다 — 대역 피드는 그대로(줄의 IP 를 /24·/48 로 다시 잘라 대역 글자, 짝 열쇠는 가린 IP), IP 하나 단위 보기는 069 의 `/admin/access/addrs`·`addr`. 처리방침은 v6(069)이 v5 를 바꿨다(사본 `privacy-20261014.html`).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-14): 방문자를 **IP별로 상세히** 본다. Clarity 는 IP·개인 단위 자료를 주지 않으므로(Data Export 에 IP 칸이 없다) 우리 서버 접속 기록(027 — IP 끝자리를 가린 줄)으로 **가린 IP 대역**(IPv4 /24, IPv6 /48 — 기록에 남은 그대로)마다 요청·사람/봇·경로·브라우저·나라·망을 모으고, 대역 하나의 접속을 시간순으로 본다. 사람 결정: 모든 대역을 보인다(데이터센터만으로 줄이지 않음). 처리방침에 이 쓰임을 적는다.

## 2. 범위
- 만드는 것: api `features/admin` 의 대역 집계·대역 상세, `GET /admin/access/ips`·`GET /admin/access/ip`, 관리자 nginx 정확 일치 둘, 처리방침 v5(한 문단·대조표·이전 판 사본), 테스트.
- 하지 않는 것: 화면(066). 원래 IP 복원(기록에 없다 — 가린 그대로). 대역을 공개 응답·Slack·로그에 내는 것. 짝 열쇠·날마다 센 방문자 규칙(038 그대로).
- 바꾸는 기존 것: 038 §3(파일 캐시에 대역 칸), 060 이 정한 처리방침 v4 → v5(2절 서버 접속 기록에 (라) 한 문단).

## 3. 동작

### 3.1 대역
- 대역 = 접속 기록 줄의 가린 IP 글자 그대로(027 이 `ip_mask` 로 IPv4 /24·IPv6 /48 을 남긴다 — 예 `203.0.113.0`, `2001:db8:1::`). 표시용으로 IPv4 는 `/24`, IPv6 는 `/48` 을 붙인다. IP 칸이 없는 줄은 대역 `(없음)`.
- 대역마다 038 의 줄 분류(종류 여덟·탐색 경로·운영자 흔적)와 039 의 나라·망 종류(DB-IP — 대역의 첫 주소로 찾음)를 쓴다.

### 3.2 `GET /admin/access/ips?window=24h|7d|30d&net=&country=&q=&bots=1|0&sort=requests|pages|last&limit=200&offset=0`
- api 역할, 관리자만(공개 404 — 028 목록 밖). 관리자 nginx `location = /svc/api/admin/access/ips`(038 의 `/svc/api/admin/access` 와 같은 꼴 — 교차 사이트 검사·기록 끔·`rewrite ^ /admin/access/ips break`).
- 창·파일 읽기·캐시는 038 의 창·회전 파일 캐시를 그대로 쓴다 — 파일마다 대역 칸을 더한다(대역마다: 요청·사람 모양 페이지·JS 신호·WS·종류 여덟 수·상태 2xx/3xx/4xx/5xx·짝 수(그날 (대역, UA) 서로 다른 수 — 038 의 날 열쇠로 센 수, 원 UA·짝 열쇠는 응답에 없다)·처음/마지막 시각·시간 칸별 요청(창 24시간 = 시간 24칸, 7d·30d = 날 칸)·경로 상위 10·브라우저 정보 상위 5(038 의 기기·OS·브라우저 이름 — UA 원문 아님)). 파일 하나에 대역 5만까지(넘는 대역은 `(기타)`).
- 응답(200, no-store): `{state, code, window, total, bands:[{band, country, net, requests, pages, js, ws, pairs, kinds:{…여덟}, status:{2xx,3xx,4xx,5xx}, botPct, first, last, spark:[…], topPaths:[[path,n]…3], topAgents:[[이름,n]…3]}]}` — 거르기(`net` 망 종류·`country` ISO·`q` 대역 앞부분 글자·`bots=0` 이면 사람 브라우저 모양 요청이 0 인 대역 빼기) 뒤 정렬·`limit`(최대 500)·`offset`. `botPct` = (scanner+tool+ai+search 요청) ÷ 요청 × 100. 경로는 038 처럼 쿼리를 뗀 것.
- 60초 캐시(창·거르기·정렬·쪽마다).

### 3.3 `GET /admin/access/ip?band=<대역>&window=`
- 관리자 nginx `location = /svc/api/admin/access/ip`. `band` 는 응답의 `band` 글자 그대로(검사: IPv4 /24 꼴 또는 IPv6 /48 꼴, 아니면 400).
- 응답: `{state, code, window, band, partial, country, net, hourly:[{ts, requests, pages, js, errors}], kinds, status, agents:[[이름, n]…20], paths:[[path, n]…20], pairs:[{entry, exit, npages, device, os, browser, channel}]…50(062 의 첫·마지막 페이지 — 짝 열쇠 없이)], recent:[{ts, path, status, kind, browser, os, device, referer}]…100(최근 순 — referer 는 027 이 남긴 출처 origin 만)}` — `code`·`partial` 은 실행 세션이 더함(§7).
- 읽기: 창 안 회전 파일을 그 대역 줄만 골라 다시 훑는다(요청이 있을 때만, 대역·창마다 60초 캐시, 압축 풀린 100MiB 상한 — 넘으면 최근 파일부터 상한까지만 읽고 `partial: true`).

### 3.4 처리방침 v5 (060 의 v4 를 고침)
- 2절 서버 접속 기록에 새 문단 (라): "운영자는 보안(봇·스캐너 대응)과 장애 확인을 위해 관리자 화면에서 끝자리를 가린 IP 대역별로 접속 기록(시각·연 경로(쿼리 뺌)·응답·요청을 보낸 쪽의 종류·브라우저 정보에서 뽑은 기기·OS·브라우저 이름·들어온 출처의 사이트 주소)을 모아 보거나 시간순으로 볼 수 있습니다. 이 화면은 관리자 로그인 뒤에만 열리고, 가린 IP 를 되돌리거나 밖으로 보내지 않습니다."
- 1절 목적 서버 접속 기록 줄 끝에 "· 보안(봇·스캐너 대응)". 한눈에 보기 서버 접속 기록 줄은 그대로.
- 머리 시행일 = PR 을 올리는 날(KST), '(같은 날 여러 판이면 n번째 판)' 표기는 060 꼴. 변경 안내 상자 교체(바뀌는 것 하나 + 이전 판 링크 + 대조). 12절 시행 문장·이력 최신 위 항목·대조표(바뀐 문장마다 한 행).
- 이전 판 사본: v4 → `privacy-20261009-2.html`(060 §7 의 이름 규칙 제안 — 같은 날 둘째 판은 `-2`), 037·051·060 사본 꼴. 사본 이름 정규식·'이력 항목마다 그 판 사본 링크' 테스트를 `-2` 를 받게 고친다.
- 동의 안내 판·동의 창 글은 그대로(서버 기록은 동의 대상이 아니다 — 다시 묻지 않는다).

### 3.5 엣지
- 대역이 집·작은 사무실 하나일 수 있다 — 관리자 화면에서만, 가린 그대로. 원 IP·짝 열쇠·UA 원문은 응답에 없다.
- 운영자 흔적 줄(038)은 대역 표에 넣되 종류 `operator` 로 센다(운영자 자신의 대역이 보인다).
- 회전 파일이 지워진 날(100개 넘음)은 창에서 빠진다(038 그대로).

## 4. 검증
- 대역 집계: IPv4·IPv6·IP 없음 줄, 종류·상태·짝 수(같은 UA 두 줄 = 1, 다른 날 같은 UA = 날마다 따로), spark 칸(24h 시간·7d/30d 날), 경로 쿼리 뗌, 상위 자르기, 파일당 5만 상한 `(기타)`, 거르기 넷·정렬 셋·쪽·limit 500, botPct.
- 대역 상세: band 검사 400, recent 순서·100 상한·필드에 원 UA·짝 열쇠 없음, pairs 의 062 값, partial 상한, 60초 캐시.
- 관리자 nginx 경로 둘(관리자 200·공개 404 — test_admin 경로 표), `nginx -t`.
- 처리방침 v5: test_privacy(시행일·변경 안내·(라) 글·1절·이력·대조표·사본 `-2` 이름과 단언), 동의 판 그대로(test_clarity 통과).
- `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 2026-10-14 KST, worktree marketLens-exec-067 (feat/067-access-ips — 35d1684 위에서 시작, 끝에 origin/main d9675fc(068·022) 를 merge 3189100)
# uv 는 Bash 샌드박스 밖에서. uv 가 만든 server/uv.lock 은 커밋하지 않고 끝에 지웠다. 브라우저는 쓰지 않았다(지시)
cd server && uv sync --extra dev
cd server && uv run --extra dev ruff check . && uv run --extra dev ruff format --check . && uv run --extra dev pytest -q
#   시작 전(35d1684): All checks passed! · 403 files already formatted · 2401 passed, 1 skipped
#   끝(merge 뒤): All checks passed! · 419 files already formatted · 2528 passed, 1 skipped (건너뜀은 test_geo_perf — DBIP_DIR 없음)
#   새 테스트: test_access_bands 10·test_access_bands_query 8·test_access_ip 8·test_access_ip_http 14, test_admin 1 더함·test_role·test_privacy·test_access_privacy 고침
cd web && npm ci && npm run lint && npm run build   # oxlint 종료 0(지적 없음) · tsc -b && vite build ✓ (dist 에 privacy-20261009-2.html)
node --check web/public/clarity.js web/public/attention.js   # ok (바꾸지 않음)
# nginx — Docker(OrbStack 29.4) nginx:1.27-alpine, 두 템플릿 envsubst 뒤 nginx -t: syntax is ok · test is successful (merge 뒤 다시 — 같음)
#   같은 컨테이너에 경로를 돌려주는 가짜 api(:8000)를 더해 관리자 server(:8081)로 넘김 확인:
#   /svc/api/admin/access/ips?window=7d&q=203.0&bots=0 → /admin/access/ips?window=7d&q=203.0&bots=0
#   /svc/api/admin/access/ip?band=203.0.113.0%2F24&window=24h → /admin/access/ip?band=203.0.113.0%2F24&window=24h
#   /svc/api/admin/access/ip?band=2001:db8:1::/48 → /admin/access/ip?band=2001:db8:1::/48 · Sec-Fetch-Site: cross-site → 403
#   공개 server(:80) /api/admin/access/ips → 404 · /svc/api/admin/access/ip → 404 · 관리자 접속 기록에 넘긴 두 경로의 줄 없음
# 망가뜨려 실패 보기 — 레포 밖 스크립트(파일을 하나씩 바꿔 해당 테스트를 돌리고 되돌림), 모두 실패했다:
#   목록 10 — 파일당 상한·짝의 창 비트·WS 5xx·q 대소문자·bots·limit 500·날 칸 KST·상위 접기·창 밖 시 칸·botPct 분자
#   상세 10 — 대역 다시 맞추기·recent 순서·페이지 없는 짝·예산·WS 5xx·첫 페이지 채널·짝 50·recent 100·창·깨진 파일 partial
#   방침 9 — (라) 지움·1절·이력의 사본 링크·머리 차례·변경 안내 다시 묻기·대조표 글자·사본 한 글자·sitemap·동의 문장
# 부담 — 레포 밖 bench(35d1684 와 이 브랜치를 같은 venv 로, 로컬 macOS arm64·Python 3.12.13, 5회 최솟값)
#   회전 파일 하나(90,000줄·47MiB, UA 310 가지 무리·무작위 경로, FileTally.read) — 읽기 · 남는 메모리(tracemalloc)
#     대역 300: 0.301 → 0.397초 · 0.3 → 9.4MiB / 대역 5,000: 0.290 → 0.436초 · 0.3 → 33.0MiB
#     줄마다 다른 대역(파일당 5만 상한 — 대역 50,001): 0.294 → 0.474초 · 0.3 → 67.1MiB
#     (첫 구현 — 경로·브라우저 정보를 시 칸마다 — 은 대역 300 에서 14.1MiB·5,000 에서 52.7MiB 라 §3.2 대로 파일 단위로 바꿨다)
#   지금 규모 30일(하루 700줄·대역 150, 회전 gz 30 + access.log): 첫 채움(tracemalloc 켬) 0.509 → 0.727초
#     · 남는 메모리 3.2 → 12.3MiB · 창 셋 회차(요약 셋 + 목록 셋) 0.010 → 0.022초
```

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 server 칸에 "· IP 대역(067 — `/admin/access/ips`·`/admin/access/ip`, 가린 대역 그대로·관리자만)", privacy 행에 "v5(067 — 대역별 조회 (라))".
- `CLAUDE.md` — 스펙 인덱스 067 행 DONE.
- `docs/context/architecture.md` — admin 항목에 대역 칸·두 경로.
- `docs/specs/038-access-v2.md` — §3 파일 캐시 문장 끝 "(067 — 대역 칸)".
- `docs/specs/060-privacy-v4.md` — §7 남은 빚의 사본 이름 줄에 "(067 이 `-2` 로 정함)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server `app/features/admin/`: 새 `access_bands.py`(대역 글자 `band_of`·상세 검사 `is_band`, 파일마다 대역 칸 `BandTally` — 시 칸(수 열여섯·처음·마지막)·경로 상위 10·브라우저 정보 상위 5 + `(기타)`·짝 해시 → KST 날 번호·시 비트, 파일당 5만 상한, 창 조립 `page`·`IpsQuery`·`ips_query`·`geo_of`·`spark_cells`), 새 `access_ip.py`(상세 다시 읽기 `scan` — `choose_files`·`needle`·`BandScan`), `access_hours.py`(줄마다 대역 칸, 짝 해시 한 번 — 038 짝 기록과 나눠 씀, 날 열쇠 칸 `_key`), `access_cache.py`(`listing` 을 모듈 함수로·`AccessLog.ips`), `visits.py`(`ips`·`ip`·칸 32·상세 잠금·1시간 비움), `router.py`(두 경로·400 `bad_band`·`no-store`).
  - web: `nginx-admin.conf`(정확 일치 둘), `public/privacy.html`(v5), 새 `public/privacy-20261009-2.html`(v4 사본), `public/sitemap.xml`(privacy lastmod).
  - 테스트: 새 `app/features/admin/tests/test_access_bands.py`·`test_access_bands_query.py`·`test_access_ip.py`·`test_access_ip_http.py`, `access_fakes.py`(`Feeds.ips`·`ip`), `test_access_privacy.py`, `tests/test_role.py`·`test_admin.py`·`test_privacy.py`.
  - 문서: 이 스펙, `CLAUDE.md`, `docs/context/status.md`·`architecture.md`·`product.md`, `docs/specs/038-access-v2.md` §3.3, `060-privacy-v4.md` §7.
- 추측한 지점 / 실행 중 함께 고친 스펙 절 (사람에게 묻지 않고 스펙과 맞는 가장 보수적인 쪽으로 정함):
  1. 대역 글자: 응답 `band` 에 서버가 `/24`·`/48` 을 붙이고(표시용 — 상세는 이 글자를 그대로 받는다), 줄의 IP 를 그 길이로 다시 잘라 만든다 — 가린 줄은 글자 그대로이고, 가려지지 않은 줄(손으로 고친 기록)도 같은 대역으로 접혀 원래 IP 가 응답에 나가지 않는다. IP 로 풀 수 없는 글자는 `(기타)`(5만 넘음과 같은 칸). 버림: 줄 글자에 접미만 붙이기(가려지지 않은 IP 가 그대로 나간다).
  2. 상세 `band` 검사: `ipaddress.ip_network(strict)` 가 IPv4 /24·IPv6 /48 이고 그 정규 글자와 같을 때만 — 대문자 IPv6·호스트 비트·`(없음)`·`(기타)`·64자 넘음·`band` 없음은 400 `{"error":{"code":"bad_band",…,"detail":null}}`(앱 오류 모양, 받은 글자를 되돌려 싣지 않는다).
  3. §3.3 응답에 `code`(035 부분 공통 — ok 면 null, 그 밖 `no_file`·예외 이름)와 `partial`(§3.3 본문에 있던 키)을 넣었다 — 응답 줄을 고쳤다. ok 가 아니면 값 키는 null 이고 `window`·`band` 는 늘 싣는다. 목록도 ok 가 아니면 `total`·`bands` null·`window` 는 늘. 두 응답 모두 `fetchedAt`·`refreshSec` 은 없다(§3.2·§3.3 모양 그대로). 모든 응답(400 포함)에 `Cache-Control: no-store`.
  4. 수의 뜻: `pages` = 038 `humanPages`(종류 browser 의 페이지 줄), `js` = 038 `jsViews`(종류 browser 의 clarity.js 200·304), `ws` = 101 줄(종류 무관 — 038 `ws`). `status` 넷은 038 `status` 와 같다 — `5xx` 는 `/api/ws/spreads` 를 뺀 5xx(WS 5xx 는 어느 칸에도 없다), 101·600 같은 칸 밖은 세지 않는다. 상세 `hourly.errors` 도 038 `errors`.
  5. 짝 수: 모든 종류의 (가린 IP, UA 전체)를 038 의 날 열쇠로 해시하고(줄마다 한 번 — 038 의 브라우저 짝 기록과 같은 값을 나눠 쓴다) KST 날 번호·그날 시 비트를 붙여, 창 안 시에 줄이 있는 짝만 센다. 같은 날을 두 파일이 나눠도 한 번, 날이 다르면 따로. IP 없는 줄(`(없음)`)과 게이트 앞 줄은 짝이 없다(038 짝 게이트 — 060 뒤로는 실제로 걸리지 않는다). `(기타)` 칸의 짝은 실제 대역으로 해시해 (대역, UA) 수를 센다.
  6. spark: 24h 는 038 `hourly` 와 같은 시 24칸, 7d·30d 는 창과 겹치는 KST 날마다 한 칸(038 `visitors.days` 와 같은 날 — 창 시작이 KST 자정이 아니면 8칸·31칸). 버림: 창 시작부터 24시간씩 7·30칸(다른 날 그림의 KST 날 경계와 어긋난다).
  7. 정밀도와 메모리: 수·처음·마지막·짝은 시 칸으로 들어 창과 정확히 맞고, 경로·브라우저 정보는 §3.2 대로 파일 단위 상위 10·5(다 읽은 뒤 나머지는 `(기타)` 하나 — 대역마다 수의 합이 남는다). 그래서 목록의 `topPaths`·`topAgents` 는 창 안 줄이 있는 파일의 것을 더한 근사값이다(창 경계 회전 파일은 창 밖 줄도 든다 — 정확한 상위는 상세가 다시 읽는다). 시 칸마다 상위를 두던 첫 구현은 활동 시간이 긴 대역에서 메모리가 컸다(§5).
  8. 브라우저 정보 이름: 038 의 `기기·OS·브라우저` 를 가운데 점으로 이은 글자(예 `mobile·ios·safari`, 인앱은 038 대로 `inapp`) — UA 원문이 아니다.
  9. `botPct` = (scanner + tool + ai + search 요청) ÷ 요청 × 100 을 소수 1자리로 반올림.
  10. 목록 인자: 모양이 틀린 값은 422·400 없이 기본값(038 의 창 규칙) — `limit` 은 정수가 아니면 200, 1~500 으로 자른다(0 → 1·9999 → 500), `offset` 은 정수가 아니거나 음수면 0, `sort` 는 목록 밖이면 requests, `bots` 는 `0` 일 때만 사람 브라우저 모양(종류 browser) 요청이 0 인 대역을 뺀다, `net` 은 소문자로 정확히 같음(`ipv6` 도), `country` 는 대문자로 정확히 같음, `q` 는 소문자·앞 64자·대역 글자 앞부분. 정렬은 큰 순·같으면 대역 글자순. `total` 은 거른 뒤·쪽 전.
  11. 나라·망 종류: 응답을 만들 때 지금 올린 DB-IP 판으로 대역 첫 주소를 찾는다(039 `locate` — KR + telecom 은 `telecom_kr`, 모름·구간 밖 `(기타)`), IPv6 대역은 망 칸만 `ipv6`(판과 무관), 판이 없거나 `(없음)`·`(기타)` 는 둘 다 null. 039 의 k=3 묶기는 대역 목록에 쓰지 않는다(사람 결정 '모든 대역을 보인다'). 두 피드도 게이트 뒤 ok 면 DB-IP 받기를 챙긴다(039 와 같은 `ensure` — 기다리지 않음).
  12. 캐시·동시성: 목록은 (창, 거르기·정렬·쪽)마다, 상세는 (대역, 창)마다 60초 칸이고 칸은 종류마다 32개까지(가장 오래 안 부른 것부터 버림). 목록은 요약(038)과 같은 잠금·파일 캐시를 쓴다(다시 읽는 때는 038 규칙 — access.log 60초·새 회전 파일). 목록은 두 번에 만든다 — 모든 대역을 거르기·정렬 열쇠로만 세고 고른 쪽(≤500)만 나머지 값을 모은다. 상세 다시 읽기는 프로세스에서 한 번에 하나(잠금). 대역 요청도 1시간 비움 타이머를 다시 걸고, 비울 때 대역 칸·상세 칸도 버린다. 깨진 회전 파일은 WARNING 1줄(부분 이름 `access.ips`·`access.ip`, 예외 이름만).
  13. 상세 읽기: 창 안 파일 = `access.log` + 수정 시각이 창 시작 뒤인 회전 파일, 최근 파일부터 압축 풀린 크기(`access.log` 포함, `.gz` 는 ISIZE)를 더해 100MiB 를 넘는 첫 파일에서 멈추고 `partial`. 깨진 회전 파일·목록을 본 뒤 사라진 파일도 `partial`, `access.log` 읽기 실패는 `error`. 줄은 대역 앞부분 글자(IPv4 `"a.b.c.`·IPv6 `"첫 마디:`)가 있을 때만 풀고 대역을 다시 맞춘다.
  14. 상세 `pairs`: 그날 페이지 줄이 있는 (IP, UA, KST 날) — 종류·운영자·탐색과 무관하게(대역 표는 모두 보인다 §3.5), 마지막 페이지 줄이 늦은 순(같으면 첫·마지막 이름순) 50개. 채널은 그날 첫 페이지 줄로(038), 페이지 수는 255 에서 멈춤(062). `recent` 는 (시각, 줄 순서) 늦은 순 100줄, `ts` 는 정수 초, `referer` 는 038 `referrer` 의 origin(없으면 null). `agents`·`paths` 는 창 안 그 대역 줄 전부를 센 상위 20(경로는 쿼리 뗀 100자).
  15. 처리방침 v5 시행일 = 2026-10-14(이 브랜치를 PR 로 올릴 날로 짐작, KST). 같은 날 판이 하나라 머리에 '(이날 n번째 판)' 이 없다. (라) 글은 스펙 글 그대로이되 페이지 맞춤법대로 'IP 를' → 'IP를', 1절은 줄 끝(기록 이름 괄호 뒤)에 ' · 보안(봇·스캐너 대응)'. 변경 안내는 바뀌는 것 한 문단(v3 꼴 '바뀌는 것:')과 '동의 안내는 바뀌지 않아 다시 묻지 않습니다', 링크는 바로 앞 판 사본과 대조 둘(그 앞 판들은 12절 이력에서 잇는다). 12절 시행 문장 '…부터 시행하며, 그 전날까지는 2026년 10월 9일 판(이날 두 번째 판)을 따릅니다.'(v3 꼴), 이력 첫 항목 '서버 접속 기록: 관리자 화면에서 가린 IP 대역별로 보기(보안·장애 확인)', v4 항목은 `<time>` 없이 + '그 판 보기'. 대조표는 두 행(1절 목적·2절 (라) — 새 문단은 '이전 판' 칸 `(없음)`), 시행 문장은 행이 없다(051 꼴).
  16. 사본 `privacy-20261009-2.html`: 060 사본 꼴(앞 사본의 가운데 칸 머리 스타일, 스크립트·`data-area` 없음, 동의 절은 세 칸 알릴 사항만), 제목 '개인정보 처리방침(2026년 10월 9일 두 번째 판) — KimpTrack', 사본 안내 '(그날 두 번째 판)', 대조표 행은 한 줄씩(보이는 글은 그대로 — 얼린 해시는 글 기준). test_privacy: 사본 이름 `privacy-<날짜>(-<n>)?.html`, 제목·이력 항목의 차례 낱말(두·세·네 번째), 이력 항목마다 그 판 사본 링크('(두 번째 판)' 이면 `-2`), '7일 전 약속' 대조는 v4 사본의 대조표에서 본다, `PREVIOUS_CONSENT_TEXT` 는 비었다(v5 는 동의 상자를 바꾸지 않았다).
  17. 038 테스트 하나(`test_access_privacy.py`)를 고쳤다 — 날 열쇠 칸이 `_day[2]` 에서 `_key[1]` 로 옮겨졌다(짝 해시를 038·067 이 나눠 쓰려고). 대역 메모(IP → 대역)가 다 읽은 뒤 비는지도 함께 본다.
  18. 함께 고친 스펙 절: 이 스펙 §3.3 응답 줄(`code`·`partial`)·상태, 038 §3.3, 060 §7. origin/main(068·022)을 merge 커밋으로 받았다 — 충돌은 CLAUDE.md 인덱스(067·068 행)·status.md·architecture.md admin 덩어리·router·visits 머리 글·test_role·nginx-admin.conf(068 의 `= /svc/api/admin/clarity/history` 와 나란히)로, 둘 다 살렸다.
- 남은 빚:
  - 부담(§5): 대역 칸이 038 파일 읽기에 더해진다 — 47MiB 회전 파일 읽기 0.29 → 0.40~0.47초(serve ×6 ≈2.4~2.8초 — 038 §4 의 0.4초 한도를 대역이 많은 파일에서 넘는다), 남는 캐시 +9.4MiB(대역 300)·+33MiB(5,000)·+67MiB(줄마다 다른 대역, 파일당 5만 상한). 몰리는 날 회전 파일 둘 + access.log 가 모두 '줄마다 다른 대역' 이면 합 ≈200MiB 로 serve(가용 ≈360~420MB)에 부담 — 관리자 화면을 열 때만이고 1시간 안 부르면 비운다. 줄이려면 대역 상한을 낮추거나 줄 하나뿐인 대역의 칸을 납작하게(사람 결정). 지금 규모 30일은 3.2 → 12.3MiB.
  - 목록 `topPaths`·`topAgents` 의 창 경계 근사값(추측 7).
  - 시행일 2026-10-14 는 PR 날 짐작 — 다른 날 PR 을 올리면 `<time>` 넷·sitemap·test_privacy `EFFECTIVE` 를 그날로. (라) 대역별 조회의 법률 확인은 사람(게시된 문구로).
  - 화면은 066. 운영 확인(배포 뒤 관리자 로그인 → `/svc/api/admin/access/ips`·`/svc/api/admin/access/ip?band=…` 200·공개 404)은 사람.
  - 커밋 크기: `0cc2f90`(상세 테스트 338줄)은 300줄을 넘었다(실수 — 이미 push 해 다시 쓰지 않음). `997c906`(v4 사본 315줄 + 테스트 4줄)은 허락된 사본 예외이고, 그 커밋에서는 방침 테스트가 빨갛다(사본을 잇는 v5 페이지가 다음 커밋 `cecea5a`).
