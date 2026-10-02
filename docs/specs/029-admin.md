# 029 — admin

상태: DONE | 의존: **028 이 main 에 머지된 뒤 시작한다**(아니면 멈추고 묻는다 — §3.5). 계약을 쓰는 스펙: 003 spreads(`/refresh`), 007 deploy(web 이미지·compose), 011 health(`/health/collect`), 016 process-split(api 분기·역할), 017 spreads-push(허브), 018 spreads-serve(`/spreads`), 022 landing(`/landing`), 025 slack-alerts(`/health` 두 역할), 027 observability(nginx 접속 로그 규칙), 028 api-allowlist(공개 허용 목록). 밖에서 들어오는 길(Cloudflare Tunnel·Access)은 030.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 볼 관리자 페이지의 서버 쪽을 만든다 — web nginx 의 내부 포트 8081 에서 앱 안쪽 상태(수집기·api 헬스, 거래소별 수집 상태, WebSocket 접속 수, Redis·Influx)를 보여 주고, 공개에서 닫힌 API(문서·분석 6개·`/refresh`·history 무거운 조회 — 028)를 전부 쓸 수 있게 한다. 8081 은 호스트에 열지 않아 이 스펙만으로는 인터넷에서 닿지 않는다 — 로그인(Cloudflare Access)과 들어오는 길(Tunnel)은 030 이 붙인다.

## 2. 범위
- 만드는 것: web nginx 관리자 server(`web/nginx-admin.conf`, listen 8081), 관리자 화면(`web/admin/` — `index.html`·`admin.js`·`admin.css`), api 역할의 `GET /admin/status`(기능 폴더 `admin`)와 core `RedisBus` 의 PING 공개 메서드, collect 박스 `server` 의 `UVICORN_ROOT_PATH=/api`, 관리자 접속 기록(호스트 바인드), 계약 테스트.
- 하지 않는 것: cloudflared·Cloudflare 설정·런북(030). 분석 API 전용 화면(API 문서의 Try it out 으로 쓴다). 앱 쪽 로그인·JWT 검증. 버전에 git SHA.
- 바꾸는 기존 것: 007(web 이미지에 관리자 설정·화면, compose web 바인드·server env), 016(api 역할 라우트에 `/admin/status`, api 헬스를 관리자 페이지에서도 본다), 018(api 라우트 집합), 027(nginx 접속 로그 끔은 공개 server 에만), 003·004·005(닫힌 경로를 쓰는 곳 = 관리자 페이지).
- 담당: 003·004 는 팀원 담당, 016·018 은 hereokay 담당이다 — 이 PR 은 그 스펙들을 **고치지 않는다**(CLAUDE.md §5). §6 의 "담당자에게 제안" 목록을 PR 본문에 적고 담당자가 반영한다. 005·007·027·028 은 이 레포 주인 담당이라 고친다. §5 허용 목록 밖에서 고치는 것(사람 승인): `docker-compose.yml`·`.gitignore`, CLAUDE.md §2.

## 3. 동작

### 3.1 관리자 server (web nginx :8081)
- 같은 web 컨테이너의 두 번째 server 다. 설정은 별도 템플릿 파일로 두고 공개 server 와 같은 `COLLECT_HOST` 한 변수 치환을 쓴다. 8081 은 어떤 서비스도 호스트에 게시하지 않고 caddy 도 부르지 않는다 — 탄력 IP 로 `Host: admin.kimptrack.com` 을 보내도 공개 server(:80)에 닿는다(nginx 는 listen 포트를 먼저 고른다). 공개 server 에 `server_name` 으로 관리자를 가르거나 caddy 에 admin 사이트를 두는 방식은 Host·SNI 위조로 로그인을 건너뛸 수 있어 쓰지 않는다.
- 경로(가는 곳은 표 밖 문장으로):

| 경로 | 가는곳 |
|---|---|
| `/` | 화면 |
| `= /favicon.ico` | 204 |
| `/api/…` | 백엔드 |
| `= /svc/api/health` | api |
| `= /svc/api/admin/status` | api |
| `= /svc/api/admin/access` | api |
| `= /svc/api/admin/clarity` | api |
| `= /api/admin/aws` | 수집기 |
| `= /api/admin/alerts` | 수집기 |

- `/` 는 관리자 화면 파일(`/usr/share/nginx/admin`, 공개 root 밖 — 공개 `location /`·`/app/` 로 받아 갈 수 없다).
- `= /favicon.ico` 는 본문 없는 204 이고 기록하지 않는다 — 브라우저가 스스로 부르는 아이콘 요청이 화면 root 에서 404·error 로그 한 줄·접속 기록 한 줄이 되지 않게. 화면은 이미지 파일을 쓰지 않고(036 §3.8) CSP `default-src 'self'` 라 `data:` 아이콘도 못 쓴다. 헤더는 server 수준 것(`X-Frame-Options`)을 상속한다 — 자기 `add_header` 를 두지 않는다.
- `/api/…` 는 **028 이전의 전체 분기** 그대로다: api 로 `/api/history/{premium,streaks,candles}`·`= /api/spreads`·`= /api/landing`·`/api/ws/`, 나머지는 전부 수집기(`${COLLECT_HOST}:8000`). 공개(028 허용 목록)와 다르게 허용 목록이 없다 — 로그인한 사람만 온다(030). 접두 제거·헤더 4개는 공개와 같다.
- `/svc/api/health` 는 api 의 `/health`, `/svc/api/admin/status` 는 api 의 `/admin/status`, `/svc/api/admin/access`·`/svc/api/admin/clarity` 는 api 의 `/admin/access`·`/admin/clarity`(035) 로(경로를 통째로 바꾼다). 035 의 둘은 첫 줄이 교차 사이트 검사이고 헤더는 server 수준 것을 상속한다.
- `= /api/admin/aws`·`= /api/admin/alerts` 는 수집기로(접두 제거) — `/api/…` 분기와 같은 곳이지만 화면의 폴링을 기록에서 빼려고 따로 둔다(034). 헤더는 server 수준 것을 상속한다. 관리자 피드는 034·035.
- 업스트림 이름은 `api` 와 `${COLLECT_HOST}` 둘만 쓴다 — 다른 이름을 못 풀면 nginx 기동이 실패해 공개 사이트까지 내려간다(두 server 가 프로세스 하나).
- 읽기 제한은 nginx 기본 60초 그대로 — 60초 넘게 첫 바이트가 없는 조회(기간이 긴 history)는 504 다.
- nginx 가 끝 `/` 없는 요청(`/api`·`/api/ws`)에 주는 301 은 상대 주소다(공개 022 와 같다) — 절대 주소면 listen 포트 8081 이 붙어 Tunnel(030) 뒤에서 닿지 않는 곳으로 간다.

### 3.2 관리자 server 의 보호 규칙
- **교차 사이트 차단**: 백엔드로 넘기는 경로(`/api/…`·`/svc/…`)는 요청 헤더 `Sec-Fetch-Site` 가 `same-origin`·`none`·빈 값일 때만 통과, `same-site`·`cross-site` 는 403 `{"error":{"code":"forbidden","message":"Forbidden","detail":null}}`(JSON). 예외는 `/`·`= /api/docs`·`= /api/redoc`(로그인 뒤 돌아오는 이동이 same-origin 이 아니다). `kimptrack.com` 과 `admin.kimptrack.com` 은 같은 사이트라 쿠키 SameSite 로는 서로의 요청을 못 막는다.
- **넘기지 않는 헤더**: 백엔드로 넘길 때 `Cookie`·`Cf-Access-Jwt-Assertion` 을 비운다(백엔드는 쓰지 않는다 — 사설망 평문으로 흘리지 않는다). `X-Refresh-Token` 은 그대로.
- **응답 헤더**: 모든 프록시 응답(수집기·api 둘 다 — 같은 앱이라 CORS `*` 를 붙인다)에서 `Access-Control-Allow-Origin` 을 지운다. 모든 응답(오류 포함)에 `X-Frame-Options: DENY`. `/`(화면)에는 CSP `default-src 'self'; frame-ancestors 'none'` — 화면은 인라인 스크립트·스타일을 쓰지 않는다. 화면(`/`)은 `Cache-Control: no-store`(공개 `index.html` 과 같은 이유 — 배포가 화면과 API 계약을 함께 바꾼다). nginx 버전 숨김. location 에 `add_header` 를 따로 두면 server 수준 헤더가 상속되지 않으므로 그 location 에 같은 줄을 반복한다.
- **접속 기록**: 요청마다 JSON 한 줄 — `time`·`email`(`Cf-Access-Authenticated-User-Email`)·`ip`(`Cf-Connecting-Ip`)·`method`·`uri`(경로와 쿼리)·`status`·`rt`(처리 초)·`ray`(`Cf-Ray`)·`sfs`(`Sec-Fetch-Site` — 이 헤더가 실제로 도착하는지 보려고). 따옴표·역슬래시·제어문자는 이스케이프하지만 UTF-8 은 보장되지 않는다(0x80 이상 바이트를 그대로 쓴다) — 읽는 도구는 관대하게 푼다(`errors='replace'`). 컨테이너 `/var/log/nginx-admin/access.log`(이미지가 디렉터리를 만든다 — 바인드가 없어도 기동한다), compose web 에 `./logs/admin:/var/log/nginx-admin` 바인드(git 무시). `/var/log/nginx` 를 통째로 바인드하지 않는다 — 이미지의 stdout·stderr 링크가 가려진다. 화면의 10초 폴링(`/svc/…`·`= /api/health`·`= /api/health/collect`)과 관리자 피드(`= /api/admin/aws`·`= /api/admin/alerts`(034), `= /svc/api/admin/access`·`= /svc/api/admin/clarity`(035)), 브라우저의 아이콘 요청(`= /favicon.ico`)은 기록하지 않는다. 회전은 030 런북(호스트 logrotate, copytruncate). 이 기록은 개인정보(이메일·IP)다 — 항목·보존(90일)은 032 처리방침에 있다.

### 3.3 관리자 화면
- 화면 파일 셋(`index.html`·`admin.js`·`admin.css`)이 보이는 것·주기·차트·보안 계약은 036(§3.8 이 이 절의 규칙을 이어받는다).
- 읽는 계약(복사):
  - 025 `/health`(두 역할): `{status, version, lastTickAt}` — `lastTickAt` 은 epoch ms 또는 null, `status == "ok"` 만 200 이고 나머지(`starting`·`stale`·`redis_down`)는 503 이지만 **본문을 읽어 표시한다**(오류가 아니라 상태다). collector 는 메모리 틱, api 는 Redis 심장박동을 본다 — 경로가 같아도 따로 보인다.
  - 011 `/health/collect`: `{serverStartedAt, fetchedAt, successRate1h, exchanges[], outages[]}`, 거래소마다 `exchange`·`state`(`ok`·`stale`·`down`)·`lastSuccessAt`·`markets`·`successRate1h`·`openOutage`(열린 실패 구간 또는 null)·`lastError`(`at`·`kind`·`statusCode`·`message` 또는 null).
  - 003 `POST /refresh`: 헤더 `X-Refresh-Token`(서버에 값이 있을 때 필요), 200 `{snapshots[], usdkrw[], totalSaved, failures[{exchange, errorCode, message}], warnings[], durationMs, fetchedAt}`, 토큰이 틀리면 401 `{"detail": "…"}`.

### 3.4 `GET /admin/status` (api 역할만)
- 응답 200 `{"wsConnections": <정수>, "redis": "ok"|"down", "influx": "ok"|"down", "version": "<앱 버전>"}`. 상태 응답이라 `{"error":…}` 형식이 아니다. OpenAPI 스키마에 넣는다. collector 에는 없다(공개에서도 028 허용 목록 밖이라 404).
- `wsConnections` = 017 허브가 들고 있는 열린 `/ws/spreads` 연결 수(`waiting` 포함) — 허브가 아직 없으면(기동 전) 0.
- `redis` = core `RedisBus` 에 새로 두는 공개 메서드 `async ping() -> None`(실패는 예외)을 부른 결과 — 버스가 없거나 예외·제한 초과면 `"down"`.
- `influx` = Influx ping 결과 — 클라이언트가 없으면(토큰 없음) `"down"`. Influx 클라이언트는 동기라 스레드에서 부르는데 자체 타임아웃이 60초다: **앞선 ping 이 아직 안 끝났으면 새로 띄우지 않고 `"down"`** 을 답한다(매달린 Influx 에서 10초 폴링이 스레드를 쌓아 `/history`·`/landing` 조회를 밀어내지 않게).
- 두 확인은 동시에 돌고 각각 2초 제한 — 전체 응답은 2초 남짓.

### 3.5 API 문서가 관리자 경로에서 동작하게
- collect 박스 `server` 에만 env `UVICORN_ROOT_PATH=/api`(compose). 문서 화면이 `/api/openapi.json` 을 부르고 스키마의 서버 주소가 `/api` 가 된다. nginx 가 접두를 뗀 경로(`/health` 등)는 그대로 라우팅되고, 박스 안 `localhost:8000/…` 도 접두 없이 그대로 부른다. 수집기 로그·025 의 500 알림에 찍히는 경로에는 `/api` 가 붙어 보인다(요청 경로는 그대로). api 에는 주지 않는다.
- **028 없이 이 설정만 들어가면** 지금 깨져 있는 공개 `/api/docs` 가 제대로 떠서 인터넷에서 Try it out 이 된다 — 그래서 028 머지가 시작 조건이고, 계약 테스트가 "`server` 에 이 env 가 있으면 공개 server 의 `location /api/` 에 `proxy_pass` 가 없다" 를 단언한다.
- 앱의 문서는 켠 채로 두고 공개에서 막는 것은 028 이다. 문서 화면의 스크립트(Swagger·ReDoc)는 앱 기본값대로 jsDelivr 에서 받는다 — 관리자 출처에서 돌므로 공급망 위험을 빚으로 남긴다(status.md).

### 3.6 엣지
- 관리자 접속 기록 디렉터리를 못 엶: nginx 가 기동하지 못해 공개 사이트도 내려간다 — 이미지가 디렉터리를 만들고 compose 가 바인드 원본을 만들며 nginx master 가 root 라 보통은 생기지 않는다. 배포 뒤 `docker logs marketlens-web`.
- 관리자 설정 문법 오류: 같은 이유로 공개까지 내려간다. 계약 테스트는 문자열만 보므로, 이 파일을 고친 PR 은 본문 테스트 칸에 로컬 `nginx -t` 결과를 적는다(status.md 빚).
- serve 박스 안의 다른 컨테이너는 `web:8081` 에 직접 닿고 헤더(이메일 등)를 위조할 수 있다 — "serve 박스는 믿는다" 전제(028 과 같다).
- Redis·Influx 가 둘 다 죽음(같은 data 박스): `/admin/status` 는 둘 다 `"down"` 을 2초 남짓에 답한다. 화면의 수집기·api 헬스도 각자 상태를 보인다.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 main 에 028 이 있는지 본다(없으면 멈추고 묻는다).
- compose: web 에 `./logs/admin:/var/log/nginx-admin` / 어떤 서비스도 8081 을 게시하지 않는다 / `server` 에만 `UVICORN_ROOT_PATH=/api` / 그러면 공개 server 의 `location /api/` 에 `proxy_pass` 가 없다(028 가드) / `.gitignore` 에 `logs/`. 기존 계약(컨테이너 수·로그 상한·profile) 그대로.
- web 이미지: 관리자 템플릿이 nginx templates 로 복사되고, 화면 파일은 `/usr/share/nginx/admin` 에 있고 공개 root 에 없다, `/var/log/nginx-admin` 이 있다.
- `web/nginx-admin.conf`: listen 8081 뿐 / `absolute_redirect off` / 업스트림 `api`·`${COLLECT_HOST}` 뿐 / `Sec-Fetch-Site` 검사와 예외 셋 / 모든 프록시 location 에서 `Cookie`·`Cf-Access-Jwt-Assertion` 비움·ACAO 지움 / `X-Frame-Options DENY always`·화면 CSP / JSON 접속 기록 경로와 폴링 제외 / `= /favicon.ico` 는 204·기록 끔·자기 헤더 없음. 공개 `web/nginx.conf` 는 028 계약 그대로(단언은 `listen 80` 블록만). Caddyfile(027 뒤 `caddy/Caddyfile`)에 `8081`·`admin` 이 없다.
- 화면 정적 단언: `admin.js` 에 `X-Requested-With`·`visibilityState` 가 있고 `localStorage`·`sessionStorage`·`innerHTML`·`window.open`·`location.pathname` 이 없다(새로고침 주소 `/` 고정), `index.html` 에 인라인 `<script>` 본문·`style=` 이 없다. (036 §4 가 넓힌다)
- `GET /admin/status`: api 역할에만(`tests/test_role.py` 라우트 집합, collector ⊇ api 단언은 이 경로 예외) / 접속 2개면 2, 허브 없으면 0 / Redis 예외면 `redis: "down"` / Influx 없음이면 `influx: "down"` / Influx 가 2초 넘게 걸리면 `"down"`, 그동안 두 번째 요청은 새 ping 을 띄우지 않고 `"down"` / Redis·Influx 둘 다 멈춤 → 둘 다 down, 3초 안. `RedisBus.ping` 은 fakeredis 로.
- 로컬 Docker: web 이미지에서 `nginx -t` 통과. 028 과 같은 에코 서버 둘(망 별칭 `api`·`server`)로 — 같은 망에서 `web:8081/` 이 화면, `/api/docs`·`/api/premium` 이 수집기 에코로, `/api/history/streaks` 가 api 에코로, `/svc/api/admin/status` 가 api `/admin/status` 로 도착 / 도착한 요청에 `Cookie`·`Cf-Access-Jwt-Assertion` 이 없다 / `Sec-Fetch-Site: cross-site` 는 403 JSON(`/api/openapi.json`·`/api/docs/oauth2-redirect` 포함), `same-origin`·없음은 통과, `/api/docs` 는 cross-site 여도 통과 / 공개 포트로 `Host: admin.kimptrack.com` 은 공개 server 응답 / 기록 파일에 JSON 한 줄, 폴링 경로는 없음. `UVICORN_ROOT_PATH=/api` 로 띄운 수집기의 `/docs` 가 `/api/openapi.json` 을 부른다. 브라우저로 화면을 열어(8081 을 잠시 로컬에만 게시한 테스트 compose) 오류 `message` 에 `<img src=x onerror=alert(1)>` 를 넣은 가짜 응답이 글자로 보이는지.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`.
- 커밋: ① `RedisBus.ping`·`/admin/status`·test_role ② nginx-admin·Dockerfile·compose·test_deploy ③ 화면 ④ §6 문서·§5·§7(각 300줄 이하).

**배포 뒤 — 사람(완료 조건 아님)**: 030 전이라 인터넷에서는 닿지 않는다. serve 박스에서 `docker exec marketlens-caddy wget -qO- http://web:8081/svc/api/admin/status` 가 200 JSON, 공개 `https://kimptrack.com/api/docs` 는 여전히 404.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 시작 조건 — main 에 028(PR #74 머지 커밋)이 있다
git fetch -q origin && git merge-base --is-ancestor f70305b228d9ba7501ed594e05c40eb3b2c98914 origin/main   # 종료 코드 0

# 기존 스펙 재검증 (마지막 커밋 뒤)
cd server && ruff check . && ruff format --check . && pytest -q   # All checks passed! · 236 files already formatted · 893 passed
cd web && npm run lint && npm run build                            # oxlint 종료 코드 0 · ✓ built (dist 에 admin 없음)

# 로컬 Docker — 망 ml029-net, 가짜 백엔드 둘(python:3.12-alpine, 망 별칭 server·api — 받은 요청을 JSON 으로 되돌리고 ACAO * 를 붙인다)
docker build -t ml029-web web
docker run -d --name ml029-web --network ml029-net --network-alias web -e COLLECT_HOST=server \
  -e 'NGINX_ENVSUBST_FILTER=^COLLECT_HOST$' -v <scratch>/logs-admin:/var/log/nginx-admin ml029-web
docker exec ml029-web nginx -t        # syntax is ok · test is successful (바인드 없이 띄운 컨테이너도 같다)
docker exec ml029-web ls /etc/nginx/conf.d /usr/share/nginx/admin   # admin.conf default.conf · admin.css admin.js index.html, html 아래 admin 0개
# 아래는 같은 망의 curl 컨테이너에서
curl -i web:8081/                     # 200 text/html · X-Frame-Options DENY · CSP default-src 'self'; frame-ancestors 'none'
curl web:8081/api/{docs,premium,history/events,refresh}             # 수집기 에코 /docs·/premium·/history/events·/refresh
curl web:8081/api/{history/streaks,history/streaks/bulk,history/candles,spreads,landing,ws/spreads}   # api 에코
curl web:8081/svc/api/admin/status · /svc/api/health                # api 에코 /admin/status · /health
curl -H 'Cookie: …' -H 'Cf-Access-Jwt-Assertion: …' -H 'X-Refresh-Token: t0k' -H 'Origin: …' web:8081/api/premium
  # 도착 헤더에 cookie·cf-access-jwt-assertion 없음, x-refresh-token 있음, 응답에 ACAO 없음(WS location 도 같음)
curl -H "Sec-Fetch-Site: <cross-site|same-site|same-origin|none|없음>" web:8081<경로>
  # /api/openapi.json·/api/docs/oauth2-redirect·/api/premium·/api/history/streaks·/api/ws/spreads·/svc/api/admin/status·/api/health
  #   = 403·403·통과·통과·통과, /api/docs·/api/redoc·/ = 다섯 다 통과
  # 403 = application/json {"error":{"code":"forbidden","message":"Forbidden","detail":null}} + X-Frame-Options DENY
curl -H 'Host: admin.kimptrack.com' web:80/ · /api/docs · /svc/api/admin/status   # 200 랜딩 · 404 JSON(028) · 404 — 공개 server
python3 -c '…json.loads 각 줄…' logs-admin/access.log
  # 58줄 전부 JSON 아홉 키(time email ip method uri status rt ray sfs), email·ip·ray 는 보낸 Cf-* 값
  # 성공한 폴링(/api/health·/api/health/collect·/svc/…) 0줄 — 폴링 경로로 남은 줄은 교차 사이트 403 넷뿐

# 수집기 API 문서 — 레포 밖 cwd 에서(.env 를 읽지 않게) ROLE=api 로 같은 앱을 UVICORN_ROOT_PATH=/api 로
cd <scratch> && env -i ROLE=api INFLUX_TOKEN= REDIS_URL=redis://127.0.0.1:6029/0 UVICORN_ROOT_PATH=/api \
  <repo>/server/.venv/bin/uvicorn app.main:app --app-dir <repo>/server --port 8029
curl localhost:8029/docs              # url: '/api/openapi.json'
curl localhost:8029/openapi.json      # servers [{'url': '/api'}], /admin/status 스키마 있음
curl localhost:8029/health            # 503(Redis 없음 — 접두 없는 경로가 라우팅된다), /api/health 는 404

# 브라우저 — 127.0.0.1:8081 만 게시한 테스트 compose(scratchpad/029/compose.029.yml + fake.py)
docker compose -f compose.029.yml up -d --build   # 가짜 /health/collect 의 bybit lastError.message 와 즉시 갱신 결과에 <img src=x onerror=alert(1)>
  # 표·즉시 갱신 결과에 글자 그대로 보임 · img 요소 0 · alert 없음 · 콘솔 CSP 위반 없음(주입한 인라인 스크립트는 CSP 가 막음)
  # 틀린 토큰 → "401 토큰 오류"(새로고침 없음) · test-token → 200·저장 1234·실패/경고 글자 그대로 · localStorage·sessionStorage 0
  # 401 비JSON 응답 → ?relogin=1 로 한 번 새로고침 · 표시가 있는 채로 fetch 실패 → 알림만 · 403 → "권한·설정 오류 (403)"·표시 지움
docker compose -f compose.029.yml down && docker rmi ml029-web

# 검토 반영 — 망 ml029fix-net, 가짜 백엔드(/ctl/mode 로 ok·all401·mixed — mixed 는 api /admin/status 만 401 text/html), web 8081 은 127.0.0.1:18029 에만
docker exec ml029fix-web nginx -t     # syntax is ok · test is successful
curl -D - -H 'Host: admin.kimptrack.com' web:8081/api · /api/ws   # 301 Location: /api/ · /api/ws/ (상대 — 포트 없음)
  # Cookie·Cf-Access-Jwt-Assertion 도착 없음 · X-Refresh-Token 도착 · 응답 ACAO 없음 · cross-site 403 — 그대로
  # 브라우저 http://127.0.0.1:18029//attacker.localhost/..%2F/ + all401 → 탭 주소 http://127.0.0.1:18029/?relogin=1 (출처 유지)
  # mixed: /?relogin=1 에서 폴링 4번 같은 문서·표시 유지·알림만 / `/` 에서 새로고침 1번 뒤 3번 같은 문서 / ok 로 한 번에 표시 지움
  # 두 백엔드 정지(502): 수집기·api 칸 전부 사유 또는 '-'·거래소 표 비움 · XSS 문자열 글자 그대로 · CSP 위반·Uncaught 0
docker rm -f ml029fix-web ml029fix-server ml029fix-api && docker network rm ml029fix-net && docker rmi ml029fix-web
docker ps -a · docker network ls · docker images | grep 029   # 0건(검토 반영 뒤에도)

# 후속(2026-10-02, 036 운영 확인 반영 — favicon) — 망 fu036-net, nginx:1.27-alpine 에 nginx-admin.conf 템플릿·web/admin, 127.0.0.1:19041
docker exec fu036-web nginx -t        # syntax is ok · test is successful
curl -D - 127.0.0.1:19041/favicon.ico # 204 No Content · X-Frame-Options DENY(server 수준 상속) — 고치기 전 404
  # 고친 뒤 error 로그의 favicon open() 줄 0·접속 기록 줄 0(대조 /nope.png 는 404·두 줄 다 남음), 헤드리스 Chrome 콘솔 자원 오류 0
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — 표에 `| admin | server: api GET /admin/status(WS 접속 수·Redis·Influx·버전), RedisBus.ping | web: 관리자 화면(web/admin, nginx :8081 — 게시 안 함) | 들어오는 길은 030 · 운영 확인 대기 |` 행. deploy 행에 "web 에 관리자 server :8081(게시 안 함)·접속 기록 `logs/admin/`, server 에 `UVICORN_ROOT_PATH=/api`". 알려진 빚에 `(029) 관리자 접속 기록(이메일·IP)은 처리방침 게시 전부터 박스 안에 쌓인다 — 후속 처리방침 스펙이 항목·보존을 옮긴다`, `(029) nginx-admin.conf 는 CI 가 문자열로만 본다 — 고친 PR 은 로컬 nginx -t 결과를 적는다`, `(029) API 문서 화면 스크립트는 jsDelivr 에서 받는다(버전·SRI 고정 없음)`. 030 이 끝날 때 028 의 "(028) 닫힌 API … 029 전까지 박스 안에서만" 빚을 지운다(030 §6). 알려진 빚에 `(029) 003·004·016·018 의 해당 문장이 029 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기` 추가.
- `CLAUDE.md` — 스펙 인덱스 029 행 상태 → DONE. §2 레포 구조에 `web/admin/`(관리자 화면, 정적 — 기능 폴더 규칙의 예외)·`web/nginx-admin.conf`, `server/app/features/admin/`.
- `docs/context/architecture.md` — '배포 토폴로지' serve 줄에 "web nginx 관리자 server :8081(게시 안 함, 들어오는 길은 030)". "현재 구조" 에 admin 항목(api 기능 폴더 `admin`·`RedisBus.ping`·관리자 nginx 설정·화면·계약 테스트). "계약 규칙" 에 "관리자 경로는 공개 server 에 두지 않는다 — 8081 은 게시하지 않는다(029)".
- `docs/context/dev-setup.md` — 'docker 통합 기동' 절에 "관리자 server(:8081)는 호스트에 안 열린다 — `docker exec marketlens-caddy wget -qO- http://web:8081/svc/api/admin/status` 로 확인" 한 문장. env 절에 `UVICORN_ROOT_PATH`(compose 가 server 에만 준다). '## web' 절에 "`web/admin/` 은 oxlint·vite 빌드 대상이 아니다" 한 줄.
- `docs/context/product.md` — 기능 목록에 "관리자 페이지(운영자용 — 헬스·수집 상태·접속 수·닫힌 API)" 한 행. 비범위 "사용자 계정·인증" 은 그대로(로그인은 앱 밖 — 030).
- `docs/specs/005-history.md` — 028 이 더한 "공개 주소에서는 404(028)" 뒤에 "— 관리자 페이지 API 문서에서 부른다(029)".
- `docs/specs/007-deploy.md` — §3 web 줄에 "관리자 server :8081(게시 안 함)·관리자 화면·접속 기록 바인드 `./logs/admin`", server 줄에 `UVICORN_ROOT_PATH=/api`.
- `docs/specs/027-observability.md` — §3.2 "nginx 접속 로그는 끈다" 에 "(공개 server — 관리자 server 는 029 가 자기 기록을 남긴다)".
- `docs/specs/028-api-allowlist.md` — §3.4 제목을 "닫힌 경로를 쓰는 법" 으로, 첫 줄에 "관리자 페이지(029·030)에서 쓴다 — 아래 박스 안 호출은 비상용".

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 003 — §3.3 110행 `POST /refresh` bullet 끝(028 이 더한 문장)을 "…관리자 페이지의 즉시 갱신 버튼으로 부른다(029)" 로.
- 004 — §1(9행)의 028 문장 뒤에 "관리자 페이지 API 문서의 Try it out 으로 부른다(029)".
- 016 — api 역할 라우트 목록(§3.1)에 `/admin/status`(029). §3.5 마지막 bullet(027 이 바꾼 문장) 끝에 "관리자 페이지 `/svc/api/health` 에서도 본다(029)".
- 018 — api 역할 라우트 집합 문장에 `/admin/status`(029).

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server: `app/core/redis_bus.py`(`ping`), `app/features/admin/`(`models.py`·`service.py`·`router.py`, `tests/test_status.py` 9개), `app/main.py`(api 역할에만 `app.state.admin`·라우터), `tests/test_role.py`(여덟 경로·`API_ONLY`), `tests/test_admin.py`(설정·화면 계약 15개).
  - web: `nginx-admin.conf`, `admin/index.html`·`admin.js`·`admin.css`, `Dockerfile`(템플릿·화면·로그 디렉터리). 루트: `docker-compose.yml`(web 바인드·server `UVICORN_ROOT_PATH`), `.gitignore`(주석만 — `logs/` 는 027 에 이미 있다).
  - 문서: `CLAUDE.md`(인덱스·§2), `docs/context/`(status·architecture·dev-setup·product), 스펙 005·007·027·028·029.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - 403 JSON 은 `error_page 403 @forbidden` 이름 있는 location 하나가 만든다(백엔드 location 마다 `types`·`default_type` 을 반복하지 않으려고). 그래서 폴링 경로라도 교차 사이트 403 은 기록된다 — 안 남는 것은 화면의 성공한 폴링뿐.
  - 백엔드로 넘길 때의 헤더(`proxy_set_header` 여섯·`proxy_hide_header` ACAO·`X-Frame-Options`)는 server 수준에 한 번 두고 상속한다 — 자기 것을 두는 WS·화면·`@forbidden` 만 반복. 계약 테스트는 nginx 상속 규칙(자기 것이 있으면 그것만)으로 실효 값을 본다.
  - api 로 가는 history 셋은 028 이전의 정규식 대신 접두 location 셋(`/api/history/premium`·`streaks`·`candles`) — 도착지는 같고 정규식 location 은 없다.
  - 폴링 제외는 정확 일치 location 넷의 `access_log off`. `/svc/` 아래 다른 경로는 화면 404 로 기록된다.
  - "연속 새로고침은 한 번까지" 의 표시는 URL 쿼리 `?relogin=1`(저장소 금지) — 폴링 한 번의 네 호출이 모두 만료 신호 없이 끝나면 `history.replaceState(null, '', '/')` 로 지운다(즉시 갱신 버튼은 지우지 않는다). Access 로그인을 거쳐 돌아와도 원래 URL 이 남는다. 화면 첫 호출도 보이는 동안에만 한다.
  - `/admin/status` 의 Influx — 앞선 ping 이 진행 중이면 동시에 온 요청도 그 결과를 기다리지 않고 down(스펙 문구 그대로). Redis 는 `wait_for` 가 취소한다.
  - 설정 계약 테스트는 새 파일 `tests/test_admin.py` 가 test_deploy 의 파서·상수를 import 한다(027 의 test_observability 와 같은 방식) — §4 ②의 "test_deploy" 를 이렇게 읽었고 test_deploy.py 는 고치지 않았다.
  - 수집기의 `UVICORN_ROOT_PATH` 확인은 ROLE=api 로 띄운 같은 앱으로 했다 — 문서 경로는 역할과 무관하고, 수집기를 로컬에서 띄우면 거래소를 부른다.
  - 커밋은 300줄 규칙으로 ①②③ 을 각각 둘로 나눴다(코드/테스트, 화면 껍데기/스크립트).
  - 함께 고친 절: 028 §3.4 의 "API 문서: `ssh -L …` 뒤 `localhost:8000/docs`" — `UVICORN_ROOT_PATH` 뒤로는 문서가 `/api/openapi.json` 을 불러 터널에서 404 라, 관리자 페이지 경로와 `localhost:8000/openapi.json` 으로 고쳤다(029 §6 의 028 제목·첫 줄 변경과 함께). architecture.md 핵심 설계 결정의 "Redis 를 HTTP 가 만지는 곳" 에 `/admin/status` 의 ping, `/health` 문장에 관리자 페이지를 더했다.
- 검토 반영(2026-09-29):
  - 관리자 server 에 `absolute_redirect off`(§3.1) — `/api`·`/api/ws` 의 자동 301 이 `http://<Host>:8081/…` 였다. 계약 테스트 한 줄.
  - 화면: 새로고침·표시 지우기 주소를 `/` 로 고정(열린 리다이렉트 — `location.pathname` 이 `//다른호스트/…` 일 수 있다), 만료 판단을 폴링 묶음 단위로(한 경로만 만료일 때 두 폴링마다 새로고침되던 것), 실패한 호출은 같은 묶음 칸을 비운다. api 버전 칸은 `/admin/status` 만 채운다(두 호출이 한 칸을 다투지 않게).
  - admin 은 spreads 허브를 모른다 — `main.py` 가 027 게이지와 같은 세는 함수 `ws_connections` 를 서비스에 넘긴다.
  - 화면 `Cache-Control: no-store` 는 유지하고 §3.2 에 적었다. 접속 기록 UTF-8 무보장을 §3.2·status.md 에 적었다.
  - 하지 않은 것: nginx `location /` 를 정확 일치(`= /`·`= /admin.js` …)로 좁히기 — nginx 는 정규화한 경로로 location 을 골라 `//evil.example/..%2F/` 도 `= /` 에 맞는다(로컬 확인). 막는 것은 화면 스크립트의 주소 고정이다.
- 남은 빚:
  - status.md 의 (029) 넷(접속 기록 개인정보·nginx-admin CI 문자열만·jsDelivr·담당자 제안 대기).
  - 관리자 접속 기록 회전은 030 런북(호스트 logrotate) 전까지 없다 — 성공한 폴링은 안 남아 느리게 는다.
  - 배포 뒤 사람 확인(§4): serve 박스 `docker exec marketlens-caddy wget -qO- http://web:8081/svc/api/admin/status` 200 JSON, 공개 `https://kimptrack.com/api/docs` 404.
  - 브라우저 확인은 Chromium 한 종류(Browser pane)다.
  - api 가 내는 끝 `/` 307(`/api/history/streaks/` → `Location: …/history/streaks`)은 `/api` 접두를 잃는다 — api 에는 `UVICORN_ROOT_PATH` 를 주지 않으므로(§3.5) 주소를 직접 칠 때만 생긴다. 수집기 쪽 307 은 접두를 지킨다.
- 후속(2026-10-02, 036 운영 확인 반영): 브라우저가 스스로 부르는 `/favicon.ico` 가 화면 root 에서 404·error 로그 줄·접속 기록 줄이 되던 것(029 때부터 — 036 §7 의 빚)을 정확 일치 location 의 본문 없는 204·기록 끔으로 고쳤다(§3.1 표·문장, §3.2 기록하지 않는 목록, §4). `log_not_found off` 는 두지 않았다 — `return` 은 파일을 열지 않아 error 로그 줄이 생기지 않는다(로컬 확인). 화면은 그대로 이미지 파일을 쓰지 않는다.
- PR 본문에 옮길 것 — 담당자에게 제안(파일:절 — 문서 주장 → 실제). 028 의 제안(같은 자리)이 아직 반영되지 않아, 028 문장과 함께 넣거나 그 뒤에 잇는다:
  - `docs/specs/003-spreads.md`:§3.3 110행 `POST /refresh` — 공개·관리자 언급 없음 → 공개 404(028), "…관리자 페이지의 즉시 갱신 버튼으로 부른다(029)".
  - `docs/specs/004-analysis.md`:§1 9행 "curl/브라우저로 직접 호출하는 BE 전용 도구" → 공개 404(028), "관리자 페이지 API 문서의 Try it out 으로 부른다(029)".
  - `docs/specs/016-process-split.md`:§3.1 28행 api 가 서빙하는 경로 목록 → `/admin/status`(029) 추가. §3.5 마지막 bullet(api 헬스는 박스 안에서 본다) → 끝에 "관리자 페이지 `/svc/api/health` 에서도 본다(029)".
  - `docs/specs/018-spreads-serve.md`:§3 43행 "`api` 가 서빙하는 경로" → `/admin/status`(029) 추가.
