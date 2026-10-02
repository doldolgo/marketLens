# 035 — monitoring-visits

상태: DONE | 의존: **032 privacy·033 clarity·034 monitoring-ops 가 main 에 머지된 뒤 시작한다**(caddy 회전 계약·방침 문장은 032, 런북 `clarity.md` 는 033, 공통 규칙의 문서 자리·`ADMIN_AWS_REGION` 설명은 034 가 만든다). 계약을 쓰는 스펙: 027 observability(caddy 접속 로그 형식), 029 admin(관리자 nginx 분기·보호 규칙), 030 admin-tunnel(들어오는 길), 028 api-allowlist(공개 nginx 모양), 022 landing(3초 기다림 규칙의 모양), 002 web-shell(탭 id). 짝 스펙 034(수집기 쪽 피드 둘)와 공통 규칙(§3.1)을 같은 문장으로 나눠 가진다. 화면은 036. Clarity 부분은 033 이 켜지고 사람이 토큰을 넣기 전까지 "연결 안 됨" 이다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
관리자 페이지(036)가 한 화면에 모을 운영 정보 중 api 가 만드는 두 가지를 준다 — caddy 접속 로그의 요약(창·분류는 038)과 Clarity 요약. 지금은 serve 박스 셸에서 로그를 직접 읽거나 Clarity 대시보드를 따로 연다. 로그 파일이나 토큰이 없으면 그 부분만 상태로 답한다.

## 2. 범위
- 만드는 것: api 역할의 관리자 피드 두 경로(기능 폴더 `admin`), core `RedisBus` 공개 메서드 둘, 관리자 nginx 정확 일치 location 둘, compose(api 의 caddy 로그 읽기 전용 바인드·env), env 키 둘, 런북 절(`clarity.md` 의 Data Export 토큰), 계약 테스트.
- 하지 않는 것: 화면(036). AWS·알림 기록(034). 관리자 접속 기록(029)의 요약. 방문자 IP·UA 원문 내보내기. Clarity 설정을 바꾸는 조작. 새 라이브러리(httpx 는 이미 있다). 요약 전용 caddy 출력(§3.2 — 받아들인 위험).
- 처리방침(032): 새로 모으거나 밖으로 보내는 방문자 정보가 없다. 접속 요약은 caddy 기록을 읽어 메모리에서 셀 뿐이고(창은 처리방침 v2 시행일 전 24시간, 뒤 최근 30일까지 — 038 §3.2, 방침 문장은 037), Clarity 에서는 집계 숫자(페이지 종류×기기 묶음 포함)와 쿼리를 뗀 페이지 주소(대시보드 주소는 허용된 탭 id 하나만 남긴다)·출처만 받아 7일 뒤 버린다(§3.3·040). 둘 다 운영자만 본다.
- 바꾸는 기존 것: 029(관리자 nginx location 둘·폴링 기록 제외, api 라우트), 027(serve 의 접속 로그를 api 가 읽는다), 033(런북 `clarity.md` 에 토큰 절), 030(런북 `admin-access.md` 에 가리키는 한 줄), 007(compose), 016·018(역할별 경로 집합).
- 담당: 016·018·021 은 hereokay 담당 — 이 PR 은 그 스펙을 고치지 않고 §6 "담당자에게 제안" 을 PR 본문에 적는다(CLAUDE.md §5). 007·027·029·030·033 은 이 레포 주인 담당이라 고친다. §5 허용 목록 밖(사람 승인): `docker-compose.yml`.

## 3. 동작

### 3.1 경로·공통 규칙 (034 와 같은 문장)
| 경로 | 역할 |
|---|---|
| `/admin/access` | api |
| `/admin/clarity` | api |

- api 에 두는 이유: 접속 로그가 serve 박스에 있고 Clarity 토큰을 serve 에 둔다. collector 역할에서는 404 이고, OpenAPI 는 api 스키마에만 두 경로가 있다.
- 관리자 nginx(029, :8081)에 정확 일치 둘: `= /svc/api/admin/access`·`= /svc/api/admin/clarity` → api 의 `/admin/access`·`/admin/clarity`(경로를 통째로 바꾼다 — 029 의 `= /svc/api/admin/status` 와 같은 모양). 첫 줄은 029 의 교차 사이트 검사(`Sec-Fetch-Site` 가 same-origin·none·빈 값만 통과, 나머지 403 JSON)이고, 화면이 폴링하므로 접속 기록을 남기지 않는다(`access_log off`). server 수준 헤더(`Cookie`·`Cf-Access-Jwt-Assertion` 비움·ACAO 지움·`X-Frame-Options`)는 상속한다 — 자기 `proxy_set_header`·`add_header` 를 두지 않는다. 공개 nginx(028)는 바꾸지 않는다 — 공개 server 에는 `/svc/` 분기가 없어 두 경로는 `location /`(`try_files $uri =404`)의 정적 404 다.
- 응답은 항상 200 JSON(상태 응답 — `{"error":…}` 형식 아님), 키 camelCase, `…At` 은 epoch ms, `…Ts` 와 점·시간 칸의 `ts` 는 epoch 초(product.md 시각 단위). 응답은 **부분** 하나이고(`/admin/clarity` 는 040 부터 하위 부분 `pages` 를 더 싣는다), 부분은 `{state, code, fetchedAt, refreshSec, …값 키}` 객체다.

| state | 뜻 |
|---|---|
| `ok` | 값있음 |
| `unconfigured` | 연결안됨 |
| `denied` | 권한없음 |
| `error` | 실패 |
| `pending` | 첫조회중 |

- `code`: `ok` 면 null, 아니면 짧은 사유 — `http_<상태>`·예외 이름·`timeout`·`redis`·`no_file`·`bad_data`(040 — 응답 모양 틀림). **오류 문장은 싣지 않는다.**
- `fetchedAt` = 그 부분의 값을 만든 시각(못 만들었으면 null), `refreshSec` = 갱신 주기(화면이 신선도 판정에 쓴다). `ok` 가 아니면 값 키는 모두 null 이다 — 직전 값을 정상처럼 보이지 않게(029 §3.3 원칙). 예외는 Clarity(§3.3)와 접속 요약의 `window`·`windows`·`gateAt`(038 — 늘 싣는다).
- 갱신(022 랜딩의 3초 규칙과 같은 모양): 요청이 왔을 때 그 부분이 비었거나 `refreshSec` 가 지났으면 갱신을 하나만 띄우고 3초까지 기다린다. 끝나면 새 값, 아니면 직전 결과(없으면 `pending`)를 답하고 갱신은 뒤에서 마저 돈다. 그동안 온 요청은 같은 갱신을 기다린다. **요청이 없으면 아무것도 부르지 않는다** — 페이지를 안 보면 파일 읽기·Clarity 호출 0.
- 스레드: 파일 읽기·JSON 풀기(Clarity 응답·`admin:clarity`)는 `asyncio.to_thread`(기본 실행기)에서 한다 — api 에는 수집 쓰기가 없어 기본 실행기로 충분하다.
- 로그·예외: 피드 처리기는 모든 예외를 잡아 부분 상태로 바꾼다 — 500 까지 올라가지 않는다(025 의 처리 안 된 500 알림은 예외 문장을 싣는다). 외부 호출·파일 읽기 실패는 `marketlens.admin` 로거에 **WARNING** 으로 부분 이름과 `code` 만(오류 문장·헤더·토큰 없이) 부분마다 10분에 1줄 남기고, ERROR 로는 남기지 않는다(025 SlackLogHandler 가 ERROR 를 예외 문장과 함께 Slack 으로 보낸다). Clarity 토큰은 로그·Redis·응답 어디에도 없다.

| 부분 | 주기초 |
|---|---|
| access | 60 |
| clarity | 14400 |
| clarity.pages | 43200 |

주기 값은 사람 확인 대상의 기본값이다(한도·부담과 신선도의 교환 — §3.4). access 는 창(038 — 24h·7d·30d)마다 따로 60초 칸이고 `access.log` 다시 읽기는 창 셋이 나눠 쓴다. Clarity 두 호출의 간격과 한도 셈은 040 §3.2.

### 3.2 접속 요약 — `GET /admin/access`
- 읽는 계약(027·032 복사): caddy 가 도메인 요청마다 JSON 한 줄을 serve 호스트 `logs/caddy/access.log` 에 쓴다. 쓰는 필드 — `ts`(epoch 초, 실수), `request.method`·`request.host`·`request.uri`(경로+쿼리, 검색어 `s.q`·`g.q`·`p.q` 는 지워져 있다), `status`, `duration`(초), `referer`(출처 `https://호스트[:포트]` 또는 빈 값), `ua`. IP 는 /24·/48 로 잘려 있다. 폴링 다섯 경로(`/api/health`·`/api/health/collect`·`/api/landing`·`/api/history/events`·`/api/history/candles`)와 canary UA 는 기록되지 않는다. `/api/ws/spreads` 는 연결이 끝날 때 상태 101·`duration` 한 줄. 파일은 하루(`roll_interval 24h`) 또는 50MiB 에서 회전하고, gzip 된 회전 파일을 100개까지·90일 동안 같은 디렉터리에 둔다(032). 회전 파일 이름 모양은 §4 에서 로컬 caddy 로 확인해 §7 에 적는다.
- compose: api 에 `./logs/caddy:/var/log/caddy:ro`·env `ACCESS_LOG_DIR=/var/log/caddy`. env 가 비거나 `access.log`·회전 파일이 다 없으면 `unconfigured`(`no_file`). 읽기 실패는 `error` — 단 회전 파일 하나가 깨졌으면 그 파일만 건너뛴다(§3.5).
- 읽는 범위(받아들인 위험): 인터넷 요청을 받고 root 로 도는 api 컨테이너가 디렉터리 전체 — 032 뒤 90일치 /24 IP·UA·출처·쿼리 — 를 읽을 수 있다. 요약에는 최근 30일까지만. IP 는 방문을 세는 되돌릴 수 없는 값(038)과 나라·망 종류 찾기(039)에만 — 처리방침 v2 시행일 뒤 KST 날의 줄만 — 메모리에서 쓰고 응답·로그에는 내지 않는다. IP 를 지운 요약 전용 두 번째 caddy 출력(회전 1일·2개)을 두면 드러나는 범위가 2일로 줄지만 caddy 설정과 방침 판이 하나씩 늘어 이번에는 두지 않는다 — status 빚, 032 1절이 "api 가 읽는다" 를 적는다.
- 창·줄 분류·집계·응답은 038 이 정한다(`GET /admin/access?window=…`).

### 3.3 Clarity 요약 — `GET /admin/clarity`
- 외부 계약(Microsoft Learn "Clarity Data Export API", 2026-10-01 확인 — 문서 갱신 2025-12-05): `GET https://www.clarity.ms/export-data/api/v1/project-live-insights?numOfDays=1`, 헤더 `Authorization: Bearer <토큰>`. `numOfDays` 1·2·3 = 최근 24·48·72시간(UTC), 차원 셋까지(040 의 페이지 호출이 `URL`·`Device` 둘을 쓴다). 응답은 `[{metricName, information: [행…]}]`, 1,000행까지·페이지 없음. **프로젝트당 하루 10회** — 넘으면 429 "Exceeded daily limit". 401 = 토큰 없음·틀림·만료, 403 = 권한 없음, 400 = 인자 오류. 토큰은 프로젝트 관리자가 Settings → Data Export 에서 만든다. 지표 이름(문서 철자): Scroll Depth·Engagement Time·Traffic·Popular Pages·Browser·Device·OS·Country/Region·Page Title·Referrer URL·Dead Click Count·Excessive Scroll·Rage Click Count·Quickback Click·Script Error Count·Error Click Count. **실제 응답의 `metricName` 은 공백·빗금 없는 CamelCase 다**(2026-10-02 첫 응답, §7): `Traffic`·`ScrollDepth`·`EngagementTime`·`PopularPages`·`Browser`·`Device`·`OS`·`Country`·`PageTitle`·`ReferrerUrl`·`DeadClickCount`·`ExcessiveScroll`·`RageClickCount`·`QuickbackClick`·`ScriptErrorCount`·`ErrorClickCount`. 문서가 행 모양을 적은 지표는 `Traffic` 하나(`totalSessionCount`·`totalBotSessionCount`·`distantUserCount` — 문자열 숫자, `PagesPerSessionPercentage` — 실수)다.
- 이름 비교: 이름에 따라 다르게 다루는 곳(`Traffic` 고르기·`Referrer URL` 의 출처 줄이기)은 이름을 NFKC 로 바꾼 뒤(전각 → 반각) 소문자로, 영문자·숫자만 남겨 비교한다 — `ReferrerUrl`·`Referrer URL` 은 둘 다 `referrerurl`, `Traffic` 은 `traffic`. 실제 철자와 문서 철자가 모두 맞는다. `Traffic` 은 정확히 `traffic` 일 때, 출처 줄이기는 `referr`·`referer` 조각이 들어 있을 때 — 철자가 또 바뀌어도(`Referrer`·`Referer Url`·`Referring URL`) 경로가 남지 않는 쪽(닫힌 쪽)이고, 잘못 맞으면 그 지표의 경로만 잃는다. 응답의 `name` 은 받은 그대로 싣는다.
- 설정: env `CLARITY_API_TOKEN`(serve 의 `server/.env`, 사람이 넣는 비밀). 없으면 `unconfigured`·호출 0. 프로젝트 ID 는 쓰지 않는다(토큰이 프로젝트에 묶여 있다).
- 호출 규칙·기록·한도 지키기는 040 §3.2(기본 4시간·페이지×기기 12시간, Redis `admin:clarity`·`admin:clarity:pages`). core 공개 계약 `RedisBus.clarity_load() -> str | None`·`clarity_save(data: str) -> None` 은 그대로이고 040 이 페이지 기록 메서드 둘을 더한다.
- 주소 줄이기(저장·응답 전): 행의 값과 키 중 주소 꼴 — 앞뒤 공백을 뗀 뒤 `<스킴>://`(대소문자 무관)·`/` 로 시작하거나, 공백 없이 `호스트.이름` 바로 뒤에 `/`·`?`·`#` — 은 지표 이름과 무관하게 쿼리·해시·사용자 정보를 뗀다 — 인기 페이지·페이지 주소에 광고 클릭 ID·utm·제3자 참조 주소의 쿼리가 실릴 수 있다. `Referrer URL`(실제 `ReferrerUrl`) 지표의 주소는 출처(`<스킴>://호스트[:포트]`, 스킴 없는 꼴은 `호스트[:포트]`)로 줄인다. 그 밖의 문자열 값은 200자에서 자른다. 표준 밖 `NaN`·`Infinity`·넘치는 실수는 null, 짝 없는 서로게이트는 `?` 로 — 응답·Redis 에 다시 쓸 수 있게. 대시보드 주소의 `tab` 남기기는 040 §3.3.
- 응답(값 키·state·정규화·`pages`)은 040 §3.3~§3.5.
- 토큰 절차(사람 — 033 의 런북 `clarity.md` 에 절 "Data Export 토큰(035)"): 032 게시·033 설치 뒤 발급(Settings → Data Export, 프로젝트 관리자) → serve `server/.env` 의 `CLARITY_API_TOKEN` → api 다시 띄우기 → 관리자 화면에서 확인. 교체(관리자 이탈 — Clarity 권장)·바로 부르기(키 하나 지우기·하루 한 번 — 040)·하루 10회 한도를 함께 적는다. `admin-access.md` 에는 그 절을 가리키는 한 줄만 둔다.

### 3.4 부담 (2026-10-01 측정 — Mac M5 Pro·Python 3.12, 운영 값 아님)
- serve(t4g.micro — 가용 최저 ≈378MB, 027): 접속 요약의 부담은 038 §3.7. Clarity 의 부담은 040 §3.6.

### 3.5 엣지
- 접속 요약의 엣지는 038 §3.8.
- Redis 불달: 접속 요약은 그대로.
- Clarity 의 엣지는 040 §3.7.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 네트워크 없음 — Clarity 는 httpx MockTransport, Redis 는 fakeredis, 접속 로그는 테스트가 만든 파일.
- 역할(`tests/test_role.py`): api 에 `/admin/access`·`/admin/clarity`, collector 에서는 404, api OpenAPI 에 둘·collector OpenAPI 에 없음.
- nginx(`tests/test_admin.py`): 관리자 server 에 정확 일치 둘 — api 로·경로 바꿈, 첫 줄 교차 사이트 검사, `access_log off`, 자기 `proxy_set_header`·`add_header` 없음(상속). 업스트림 둘뿐. 공개 server 는 028 계약 그대로이고 `/svc/` 위치가 없다.
- compose: api 에 caddy 로그 읽기 전용 바인드·`ACCESS_LOG_DIR`, 다른 서비스엔 없다. 기존 계약(컨테이너 일곱·profile·로그 상한) 그대로.
- 접속 요약은 038 §4.
- Clarity 는 040 §4.
- 예외: 처리기 안 예외·JSON 으로 다시 쓸 수 없는 답 → 500 이 아니라 그 부분 `error`·WARNING 1줄(ERROR 없음).
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`. 로컬 Docker: web 이미지 `nginx -t`(029 와 같은 방법), caddy 이미지의 `caddy version`(2.11 이상)과 작은 `roll_size` 로 회전 파일 이름 모양 확인 — 둘 다 §7 에.

**배포·런북 뒤 — 사람(완료 조건 아님, status.md 비고 "035 운영 확인 대기")**: `/svc/api/admin/access` 가 실제 로그로 차는지·`firstTs` / Clarity(032·033 뒤): 토큰을 넣고 첫 응답의 `metricName` 목록과 행 키를 §7 에 옮긴다 / 관리자 접속 기록(029)에 폴링 둘이 없다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 시작 조건 — 033 PR(feat/033-clarity, #91 머지 전) 위의 브랜치. 그 아래 main 에 032(#90)·034(#86) 머지 — 설계 세션이 의존 충족으로 정한 쌓임
git log --oneline -1   # 시작 e41a7f4 (033 줄기 마지막 커밋)

# 기존 스펙 재검증 (마지막 코드 커밋 뒤)
cd server && ruff check . && ruff format --check . && pytest -q   # All checks passed! · 286 files already formatted · 1232 passed
cd web && npm run lint && npm run build   # oxlint 종료 0(출력 없음) · ✓ built — index 253.01 kB(gzip 78.58 kB)
pytest -q app/features/admin tests/test_role.py tests/test_admin.py tests/test_clarity_store.py   # 142 passed (035 새 테스트 52 포함)

# 로컬 Docker — caddy 회전 파일 이름(scratchpad/035/caddy-roll: 레포 Caddyfile 의 access_log 조각 그대로 + `:8080 { import access_log; respond "ok" }`, roll_size 만 1MiB)
docker run --rm caddy:2-alpine caddy version   # v2.11.4
docker run -d --name ml035-caddy -v <scratch>/caddy-roll/conf:/etc/caddy:ro -v <scratch>/caddy-roll/logs:/var/log/caddy caddy:2-alpine
docker exec ml035-caddy sh -c 'for i in $(seq 1 900); do wget -q -O /dev/null -U "Mozilla/5.0 (iPhone) Safari" --header "Referer: https://www.google.com/search?q=1" "http://localhost:8080/app/?tab=history&utm_source=X&pad=<1500자>&s.q=secret$i"; done'
ls <scratch>/caddy-roll/logs   # access-2026-10-01T08-41-59.574-size.log.gz · access.log
#   roll_interval 1m 로 다시 띄워 1분 뒤 요청 → access-2026-10-01T08-43-48.568-time.log.gz
#   회전 파일 mtime(1790844119) = 마지막 줄 ts · 줄의 둘째 필드가 "ts" · uri 의 s.q 지워짐 · referer 는 출처만
docker rm -f ml035-caddy

# 로컬 Docker — 관리자 nginx(029 와 같은 방법). 망 ml035-net, 가짜 백엔드 둘(caddy:2-alpine respond — 망 별칭 api·server, 받은 경로·Cookie·JWT 를 되돌린다)
docker build -q -t ml035-web web/
docker run -d --name ml035-webc --network ml035-net --network-alias web -e COLLECT_HOST=server -e 'NGINX_ENVSUBST_FILTER=^COLLECT_HOST$' ml035-web
docker exec ml035-webc nginx -t   # nginx: the configuration file /etc/nginx/nginx.conf syntax is ok · test is successful
docker exec ml035-webc wget -qO- --header 'Cookie: sid=abc' --header 'Cf-Access-Jwt-Assertion: jwt123' http://127.0.0.1:8081/svc/api/admin/access
#   api /admin/access cookie=[] jwt=[] · /svc/api/admin/clarity → api /admin/clarity · /svc/api/admin/status → api · /api/admin/aws → collector
#   Sec-Fetch-Site cross-site·same-site → 403 application/json {"error":{"code":"forbidden",…}} + X-Frame-Options DENY · same-origin·none → 통과
#   기록 파일: /api/premium 한 줄 + 403 다섯 줄(@forbidden 은 server 수준 기록 — 029 그대로), 200 인 두 피드 0줄 · 공개 :80 의 /svc/api/admin/access → 404
docker rm -f ml035-webc ml035-api ml035-server && docker network rm ml035-net && docker rmi ml035-web
docker ps -a · docker network ls · docker images | grep 035   # 0건

# api 역할 스모크 — 레포 밖 cwd(.env 를 읽지 않게), Redis 는 닿지 않는 주소
ROLE=api INFLUX_TOKEN= ACCESS_LOG_DIR=<scratch>/caddy-roll/logs REDIS_URL=redis://127.0.0.1:1/0 uvicorn app.main:app --port 18935
curl localhost:18935/admin/access    # ok · requests 902(실제 caddy 줄 — 회전 gz 둘 + access.log) · skipped 0 · tabs [["history",900]] · referrers [["https://www.google.com",900]] · utmSources [["x",900]] · devices mobile 900·bot 2(busybox wget)
curl localhost:18935/admin/clarity   # unconfigured · code null · 값 null · refreshSec 10800
curl -o /dev/null -w '%{http_code}' localhost:18935/admin/aws   # 404 (수집기 전용)
#   같은 명령에 CLARITY_API_TOKEN=local-dummy·ACCESS_LOG_DIR 비움 → /admin/clarity error·redis(Clarity 호출 0) · /admin/access unconfigured·no_file · WARNING 한 줄, 로그에 토큰 0회

# 부담(§3.4) — 합성 50MiB access.log 한 파일, 로컬 Mac·Python 3.12
#   창 안 67,401줄: 0.44초·최대 RSS 변화 없음(20.1MiB — import 뒤 그대로) / 전부 창 밖: 0.03초
#   테스트: 44MB 파일을 tracemalloc 최고 0.37MB 로(상한 8MB 단언 — test_access_files.py)

# 검토 반영 — 같은 탐침을 고친 뒤 다시(scratchpad 035/nan.py·surr.py·trunc.py·clar.py, TestClient·fakeredis·MockTransport)
#   본문 NaN·Infinity / 제목 "\ud83d" → GET /admin/clarity 두 번 200 ok(값 null·`?`), Redis 는 표준 JSON (전: 두 번 500)
#   잘린 gz·틀린 머리 gz·디렉터리·권한 0 회전 파일 → ok·skipped 1 (전: error EOFError·BadGzipFile·IsADirectoryError·PermissionError)
#   200 ["a","b"]·Traffic information 문자열 → error ValueError·직전 값 유지, [] → ok / HTTPS://·앞 공백·스킴 없는 주소·키·android-app 출처의 쿼리 0
#   새 테스트 11개는 고치기 전 코드에서 모두 실패 · nginx·Caddyfile 은 바꾸지 않아 nginx -t·caddy validate 는 다시 돌리지 않음

# 후속(2026-10-02, 운영 확인 반영 — 지표 이름 정규화)
pytest -q app/features/admin/tests/test_clarity_safety.py   # 고치기 전: 실제 철자 1 failed(ReferrerUrl 행이 출처로 안 줄어듦)·문서 철자 통과(11 passed) → 고친 뒤 12 passed
cd server && ruff check . && ruff format --check . && pytest -q   # All checks passed! · 286 files already formatted · 1239 passed
# 검토 반영(같은 날) — 출처 판정을 닫힌 쪽으로. <scratch>/followup/apply/ref_probe.py(shape() 에 철자 14개)
#   고치기 전: Referrers·Referrer·ReferrerUrls·Referer Url·Referring URL·전각·키릴 e 일곱이 경로를 남김 → 고친 뒤 키릴 e 하나만
pytest -q app/features/admin/tests/test_clarity_safety.py   # 고치기 전 7 failed·12 passed(철자 변형 일곱) → 고친 뒤 19 passed
cd server && ruff check . && ruff format --check . && pytest -q   # All checks passed! · 286 files already formatted · 1248 passed
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행 server 칸 끝에 "· api 관리자 피드 둘(035) — `/admin/access`(caddy 로그 24시간 요약·탭별 진입)·`/admin/clarity`(3시간 간격·Redis 캐시·주소 쿼리 뗌·7일 뒤 버림)", 비고에 "035 운영 확인 대기(Clarity 토큰)". 알려진 빚에 `(035) Clarity 응답의 Traffic 밖 행 모양은 1차 문서에 없다 — 첫 응답 뒤 035·036 를 함께 정규화`, `(035) api 컨테이너가 caddy 로그 디렉터리 전체(90일)를 읽을 수 있다 — 요약 전용 출력은 두지 않았다`, `(035) 016·018·021 의 해당 문장이 035 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기`.
- `CLAUDE.md` — 스펙 인덱스 035 행 상태 → DONE.
- `docs/context/architecture.md` — '핵심 설계 결정' 의 "Redis 를 HTTP 가 만지는 곳" 문장 괄호에 "035 의 `/admin/clarity` 는 `admin:clarity` 읽기·쓰기". '계약 규칙' 의 관리자 피드 줄 "(034)" → "(034·035)". '배포 토폴로지' serve 줄에 "api 가 `logs/caddy` 를 읽기 전용으로 읽는다(035)". '현재 구조' admin 항목에 035 모듈(api 라우터·접속 요약·Clarity·`RedisBus` 메서드 둘).
- `docs/context/dev-setup.md` — env 표에 `CLARITY_API_TOKEN`(없음) 행과 설명, `ADMIN_AWS_REGION` 설명 옆에 같은 방식으로 `ACCESS_LOG_DIR`(compose 가 api 에만 — `server/.env` 에 두지 않는다). '검증용 스모크' 에 `curl -s localhost:<api 포트>/admin/access` → 로컬은 `unconfigured`.
- `docs/context/db.md` — Redis 절에 `admin:clarity`(문자열 JSON, 만료 없음, api 가 읽고 쓴다 — 마지막 시도·결과·마지막 성공 값, 값은 7일 뒤 버린다).
- `docs/context/product.md` — 기능 목록 admin 행 설명 끝에 "·접속 요약·Clarity 요약".
- `server/.env.example` — `# CLARITY_API_TOKEN=` 과 설명 1줄(serve 의 api 만, 비면 Clarity 부분 꺼짐).
- `docs/specs/007-deploy.md` — §3 api 줄에 caddy 로그 읽기 전용 바인드·`ACCESS_LOG_DIR`(035).
- `docs/specs/027-observability.md` — §3.2 끝에 "api 가 이 디렉터리를 읽기 전용으로 읽어 관리자 페이지에 24시간 요약을 준다(035) — 새 저장은 없다".
- `docs/specs/029-admin.md` — §3.1 표에 둘(`= /svc/api/admin/access`·`= /svc/api/admin/clarity` → api). §3.2 접속 기록의 "기록하지 않는다" 목록에 둘.
- `docs/runbooks/clarity.md` — 새 절 "Data Export 토큰(035)"(§3.3).
- `docs/runbooks/admin-access.md` — "Clarity 토큰은 `clarity.md` 의 Data Export 토큰 절(035)" 한 줄.

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 016 — §3.1 api 경로 목록에 `/admin/access`·`/admin/clarity`(035).
- 018 — §3 api 라우트 집합 문장에 `/admin/access`·`/admin/clarity`(035).
- 021 — §3.1 serve 설명에 "api 가 caddy 로그 디렉터리를 읽기 전용으로(035)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server: `app/features/admin/parts.py`(034 `feeds.py` 에서 옮긴 공통 — `Result`·`render`·`Slot`·`Warner`, 동작 같음), `feeds.py`(공통을 가져온다), `access.py`(파일 고르기·흘려 읽기), `access_tally.py`(한 줄 세기), `clarity.py`(호출·응답 줄이기·`admin:clarity` 기록), `visits.py`(`VisitFeeds`), `router.py`(두 경로), `app/main.py`(api 에만 `app.state.admin_visits`), `app/core/config.py`(`access_log_dir`·`clarity_api_token`), `app/core/redis_bus.py`(`clarity_load`·`clarity_save`), `.env.example`(`# CLARITY_API_TOKEN=`).
  - 테스트: `features/admin/tests/`(`access_fakes.py`·`clarity_fakes.py`·`test_access_summary.py`·`test_access_files.py`·`test_clarity_feed.py`·`test_clarity_safety.py`·`test_visits.py`), `tests/test_clarity_store.py`, 고친 `tests/test_role.py`·`tests/test_admin.py`.
  - 인프라: `web/nginx-admin.conf`(정확 일치 둘), `docker-compose.yml`(api 의 읽기 전용 바인드·`ACCESS_LOG_DIR`).
  - 문서: context 다섯(status·architecture·dev-setup·db·product), 런북 `clarity.md`(6절)·`admin-access.md`(한 줄), 스펙 007·027·029, `CLAUDE.md` 인덱스.
- 확인한 사실: caddy 2.11.4 회전 파일 이름은 `access-<UTC YYYY-MM-DDTHH-MM-SS.mmm>-<size|time>.log.gz`(크기·시간 회전의 꼬리가 다르다). 압축 중엔 같은 이름의 `.log` 가 잠깐 함께 있어 짝이면 `.log` 하나만 읽는다. 회전 파일의 수정 시각은 마지막 줄 `ts` 와 같았다. 줄의 둘째 필드가 `ts` 라 JSON 을 풀기 전에 창 밖을 버릴 수 있다.
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - Clarity 는 요청마다(한 번에 하나·3초 기다림) Redis 기록을 읽어 3시간 지났을 때만 부른다 — 메모리에 3시간 묵히지 않아 런북의 '`admin:clarity` 지우면 바로 부르기' 가 재시작 없이 된다. 불렀는데 Redis 쓰기가 실패하면 그 시각을 메모리에 두고 3시간 동안 다시 부르지 않는다(한도 보호). Redis 읽기 실패 때는 메모리의 마지막 기록 값을 `error`·`redis` 와 함께 싣는다.
  - Clarity 토큰 없음의 `code` 는 null(034 의 리전 없음과 같다). 값이 7일 지나 비면 `fetchedAt` 도 null(값을 만든 시각이 없다), 값 키 `numOfDays` 도 값이 있을 때만 1. `nextAt` 은 시도 기록이 없으면 null.
  - Clarity 응답이 200 밖이면 401·403 → `denied`, 그 밖(3xx·4xx·5xx·429) → `error`·`http_<상태>`. 본문 모양이 틀리면 `error`·예외 이름. Traffic 의 정수 칸이 정수가 아니면 그 칸만 null. 지표 이름도 200자. 전체 10초는 httpx 제한과 `wait_for` 둘로. User-Agent 는 앱 공통 값. `admin:clarity` JSON 키는 `{attemptAt, state, code, successAt, values}`(db.md).
  - 실패 WARNING 은 부분 이름 `access`·`clarity` 둘(Clarity 의 호출 실패·Redis 실패가 같은 10분 칸을 쓴다). `unconfigured`(파일·토큰 없음)는 실패가 아니라 남기지 않는다.
  - 접속 요약: 창은 [지금이 든 시의 시작 − 23시간, 그 시의 끝) — 시계가 앞선 줄도 마지막 시에. `tabs` 는 경로가 정확히 `/app/` 인 페이지만(SPA 대체로 200 인 `/app/<x>` 는 세지 않는다), `tab=` 빈 값은 `(기타)`. 빈 `utm_source` 는 세지 않는다. 출처 키도 경로처럼 100자에서 자른다(키 상한과 함께 메모리를 묶으려고). 브라우저도 대소문자 무관. `status` 네 칸 밖(101 등)은 세지 않는다. 상위 목록은 같은 수면 이름순. `recent5xx` 는 최신이 앞. 빈 줄은 skipped 로 세지 않는다. 목록을 본 뒤 사라진 파일(회전·보관 삭제)은 건너뛴다.
  - 034 공통을 `parts.py` 로 옮겼다 — `Slot` 의 예외 분류만 인자로(034 는 `aws.classify`, 035 는 예외 이름).
  - 검토 반영: Clarity 의 `NaN`·`Infinity`·넘치는 실수는 거부하지 않고 null(한 지표의 빈 값이 피드를 3시간 막지 않게), 그래도 다시 쓸 수 없는 답(손으로 넣은 Redis 값 등)은 응답 직전 확인에서 `error`·예외 이름. 줄인 행 키가 겹치면 뒤의 값이 남는다. 깨진 회전 파일은 응답 키를 늘리지 않으려고 `skipped` 1 로 센다. 접속 요약의 출처 키는 `urlsplit` 으로 다시 만든다(끝 점 뗌·IPv6 는 대괄호) — 027 Caddyfile 의 출처 줄이기와 이중.
- 실행 중 함께 고친 스펙 절: §6 목록대로 007 §3(api)·027 §3.2(끝 한 줄)·029 §3.1(표 둘·문장)·§3.2(기록하지 않는 목록). 035 본문은 고치지 않았다.
- 담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로): 016 — §3.1 api 경로 목록에 `/admin/access`·`/admin/clarity`(035). 018 — §3 api 라우트 집합 문장에 `/admin/access`·`/admin/clarity`(035). 021 — §3.1 serve 설명에 "api 가 caddy 로그 디렉터리를 읽기 전용으로(035)".
- 후속 PR 검토 반영(2026-10-02): 출처 판정을 조각 포함(닫힌 쪽)으로(§3.3 '이름 비교'·§4). §3.3 `metrics` 끝 문장과 런북 `clarity.md` 6절 5단계를 '정규화 키는 세션이 있는 응답 뒤' 에 맞추고, 런북에 세션 0 이면 Traffic 두 칸이 null 일 수 있다고 적었다.
- 남은 빚:
  - 배포 뒤 사람 확인(2026-10-02, 아래 운영 확인): `/svc/api/admin/access` 가 실제 로그로 차는지 / Clarity 토큰(런북 `clarity.md` 6절)을 넣고 첫 응답의 `metricName` 목록과 행 키를 이 절에 옮긴다 / 관리자 접속 기록에 폴링 둘이 없는지 — 셋은 끝났다. `firstTs` 를 본 기록은 없다 — 남긴다(status 비고).
  - Clarity 의 Traffic 밖 행 모양은 1차 문서에 없다 — 받은 이름·키 그대로 싣는다(status 빚).
  - 운영 확인(2026-10-02, 설계 세션 — 배포 뒤 반박 검증까지): 관리자 여덟 경로 모두 200·계약 모양. `/admin/access` 의 수를 serve 의 caddy 로그로 따로 세어 정확히 일치. 관리자 접속 기록에 두 피드의 폴링 줄 없음, 공개 쪽 `/api/admin/*` 404. Clarity 는 토큰을 넣고 api 를 다시 만든 뒤(00:44Z) 00:45:31Z 첫 조회 `ok`(세션 0). 이때 `ReferrerUrl` 이 문서 철자(`Referrer URL`)와 정확 일치 비교라 출처 줄이기에서 빠져 쿼리·해시만 떼고 경로가 남는 것을 찾았다 — 세션 0 이라 행 0, 실제로 남은 경로는 없다. 후속 PR 에서 이름 비교를 정규화로 고쳤다(§3.3 '이름 비교', 테스트는 실제·문서 철자 둘 다).
  - Traffic 의 `users`·`pagesPerSession` 이 첫 응답에서 null 이다 — 세션 0 이라 빈 것인지 키(`distantUserCount`·`PagesPerSessionPercentage`)가 실제와 다른 것인지는 세션이 있는 응답을 본 뒤 정한다. 그 전엔 Traffic 키를 바꾸지 않는다(status 빚).
  - Clarity 첫 실제 응답(2026-10-02 00:45Z, 토큰을 넣고 api 를 다시 만든 직후 — 033 배포 18분 뒤라 동의한 방문자가 없어 세션 0): `Traffic` 밖 `metricName` 15개. 행 하나짜리 여섯 `DeadClickCount`·`ExcessiveScroll`·`RageClickCount`·`QuickbackClick`·`ScriptErrorCount`·`ErrorClickCount` — 키 `sessionsCount`·`sessionsWithMetricPercentage`·`sessionsWithoutMetricPercentage`·`pagesViews`·`subTotal`. `ScrollDepth` — `averageScrollDepth`. `EngagementTime` — `totalTime`·`activeTime`. 행 0개 일곱 `Browser`·`Device`·`OS`·`Country`·`PageTitle`·`ReferrerUrl`·`PopularPages`(키 모름). 세션 0 이라 값의 꼴(정수·실수·글자)과 차원 지표의 키를 아직 모른다 — 정규화 키(§3.3·036 타일)는 세션이 있는 응답을 본 뒤로 미룬다(status 빚).
  - 출처 판정은 조각 포함(`referr`·`referer`, NFKC 뒤)이라 철자 변형·전각에는 닫혀 있지만, 다른 문자 체계가 섞인 이름(키릴 `е` 등)과 낱말이 아예 다른 이름(예: `Source`)은 출처로 줄이지 않는다 — 지표 이름은 Clarity 서버가 정해 방문자가 고를 수 없다. 이름이 바뀌면 다음 응답을 볼 때 §7 이름 목록과 대조한다.
  - api 가 caddy 로그 디렉터리 전체(90일)를 읽을 수 있다 — 요약 전용 출력은 두지 않았다(status 빚, 받아들인 위험).
  - Clarity 값의 7일 버림은 요청 때 한다 — 페이지를 7일 넘게 안 열거나 토큰을 지운 뒤에는 Redis 에 값이 남는다(런북 '끄기' 에 `DEL admin:clarity`, status 빚).
  - 016·018·021 제안 반영 대기(status 빚).
