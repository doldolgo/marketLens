# 028 — api-allowlist

상태: DONE | 의존: **022 재작업이 main 에 머지된 뒤 main 에서 구현한다**(랜딩이 `/api/landing` 을 부르는 판). 계약을 쓰는 스펙: 003 spreads(`/refresh`), 004 analysis(분석 6개), 005 history(`/history/premium`·`streaks`·`bulk`), 007 deploy(nginx), 008 usdt-staleness(`/spreads` 경고 확인), 011 health(`/health/collect`), 013 premium-events(`/history/events`), 014 premium-1m(`/history/candles`), 016 process-split(api 분기), 017 spreads-push(`/ws/spreads`), 018 spreads-serve(`GET /spreads`), 021 infra-split(`COLLECT_HOST`), 022 landing(`/landing`), 025·027(uptime·canary 가 부르는 경로)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
인터넷에는 화면과 감시가 실제로 쓰는 API 만 연다. 지금은 nginx 의 `location /api/` 한 줄이 수집기의 모든 경로를 공개한다 — API 문서(`/api/docs`·`/api/redoc`·`/api/openapi.json`, 2026-09-28 운영 200), 웹이 쓰지 않는 분석 API 6개(1 vCPU 를 80~100% 쓰는 수집기에서 돈다), 즉시 갱신 `POST /api/refresh`, 기간을 크게 주면 Influx·api 메모리를 키우는 `/api/history/premium`·`streaks`·`streaks/bulk`, 022 재작업 뒤로는 부르는 곳이 없는 `/api/spreads`. 끝나면 허용한 경로 밖의 `/api/*` 는 전부 404 이고, 백엔드에 새 경로가 생겨도 기본으로 숨는다. 닫힌 경로는 후속 관리자 페이지(029 — Cloudflare Access 로그인 뒤)에서 쓴다.

## 2. 범위
- 만드는 것: `web/nginx.conf` 공개 server(listen 80)의 `/api` 허용 목록과 JSON 404, 배포 계약 테스트(웹·감시가 부르는 경로와 허용 목록 대조 포함).
- 하지 않는 것:
  - 관리자 페이지·Cloudflare — 029. 앱 코드 변경 — FastAPI 문서는 앱에서 켠 채로 둔다(029 가 관리자 경로로 쓴다). 막는 층은 nginx 하나다.
  - 요청 속도 제한. `/api/history/events` 의 기간 상한 — 공개로 남지만 상한이 없다(`?start=0` 이면 사건 전부를 수집기가 읽는다). 후속 빚으로 남긴다(status.md).
  - 로컬 개발(vite proxy)은 바뀌지 않는다 — `/api/*` 를 그대로 `localhost:8000` 으로 넘긴다.
- 바꾸는 기존 것: 007·016·018·021 의 nginx 분기 문장("그 외 `/api/*` → server", api 로 가는 history 정규식 분기, `/api/spreads` 공개), 003 `/refresh`·004 분석 6개·005 `/history/premium`·`streaks`·`bulk`·018 `GET /spreads` 가 공개 주소에서 404 가 된다(API 자체는 그대로). `/api/spreads` 로 하던 수동 확인(008·018·021·022·런북)은 박스 안 호출로 바꾼다(§3.4).
- 담당: 003·004 는 팀원 담당, 016·017·018·021 은 hereokay 담당이다 — 이 PR 은 그 스펙들을 **고치지 않는다**(CLAUDE.md §5). §6 의 "담당자에게 제안" 목록을 PR 본문에 적고 담당자가 반영한다. 005·007·008·022·027 은 이 레포 주인 담당이라 고친다. CLAUDE.md §5 허용 목록 밖에서 고치는 것(사람 승인): `docker-compose.yml`(주석 한 줄), `web/Dockerfile`(주석 한 줄).

## 3. 동작

### 3.1 공개 허용 목록
공개 server 의 `/api` 는 아래 여섯 경로만 백엔드로 넘긴다. 전부 **정확 일치**다(끝에 `/` 가 붙거나 하위 경로면 404). 매칭은 경로만 보고 쿼리스트링은 그대로 따라간다. 넘길 때 `/api` 접두를 뗀다 — 모양은 018·022 와 같은 "접두 제거 rewrite + URI 없는 `proxy_pass`" 하나로 고정한다. 헤더 4개(`Host`·`X-Real-IP`·`X-Forwarded-For`·`X-Forwarded-Proto`)는 지금과 같다.

| 경로 | 가는곳 |
|---|---|
| `/api/health` | collector |
| `/api/health/collect` | collector |
| `/api/history/events` | collector |
| `/api/history/candles` | api |
| `/api/landing` | api |
| `/api/ws/spreads` | api |

- `/api/ws/spreads` 는 WebSocket 업그레이드 헤더(`Upgrade`·`Connection`, HTTP/1.1)를 지금처럼 붙인다. 접두 location `/api/ws/` 는 없앤다 — 접두면 api 에 새 `/ws/*` 가 생길 때 자동 공개되고, `/api/ws` 가 nginx 의 자동 301 을 받는다.
- 누가 쓰나: 대시보드 — `/api/health/collect`(수집 상태 탭 5초), `/api/history/events`·`/api/history/candles`(기록 탭), `/api/ws/spreads`(스프레드 탭). 랜딩 — `/api/landing`. 감시 — uptime(025)은 `/api/health`, canary(027)는 `/api/health`·`/api/history/candles`·`/api/ws/spreads`.
- 공개 server 에는 `/api` 에 걸리는 **정규식 location 을 두지 않는다** — 정규식은 접두 location(아래 404)보다 먼저 이겨 허용 목록을 우회한다(예: 남겨 둔 `~ ^/api/history/(premium|…)` 가 `premium` 을 api 로 넘긴다).

### 3.2 그 밖의 `/api` — 404
- `/api` 자신과 위 표에 없는 `/api/*` 는 nginx 가 백엔드에 넘기지 않고 404 를 답한다. 메서드와 무관하다(`POST /api/refresh` 도 404).
- 본문은 앱의 404 와 같은 JSON `{"error":{"code":"not_found","message":"Not Found","detail":null}}`, `Content-Type: application/json` — 경로 확장자(`/api/x.html`·`.js`·`.png`)와 무관하게 이 한 줄이다(확장자별 MIME 추정을 끈다). 403 을 쓰지 않는 이유: 경로가 있다는 사실을 드러낸다. 헤더까지 앱과 같지는 않다(앱 404 에는 `vary: Origin`) — 흉내 내지 않는다.
- 공개 server 의 응답 헤더·오류 페이지에서 nginx 버전을 숨긴다(`Server: nginx`).
- nginx 가 location 을 고르기 전에 거절하는 요청(`TRACE` 405, `/api/%00`·루트 밖 `..` 400, 1MB 넘는 본문 413, 요청 줄이 헤더 버퍼 8KB 를 넘으면 414)은 nginx 기본 오류 HTML(버전 없음)이다 — 이 규칙 밖이다.
- 허용 목록에 있는 경로라도 백엔드가 답하는 것은 백엔드 형식이다 — 예: api 에 없는 WebSocket 핸드셰이크는 uvicorn 이 403(본문 없음)으로 거절한다.

### 3.3 우회에 대한 규칙
- nginx 는 location 을 고르기 전에 경로를 정규화한다 — `%XX` 풀기, `.`·`..` 해석, 연속 슬래시 합치기. 그래서 `/api//premium`·`/api/%70remium`·`/api/./premium`·`/api/x/../premium`·`/api/ws/../docs`·`/api/history/candles/../../premium` 은 정규화된 경로로 판정되어 404 다. 대소문자가 다른 경로·세미콜론이 붙은 경로·이중 인코딩(`/api/%2570remium`)은 허용 목록에 없으니 404 다.
- 백엔드로는 정규화된 경로를 넘긴다(§3.1 의 rewrite 모양). 원문 URI 를 넘기는 형태(rewrite 없는 URI 없는 `proxy_pass`)와, 정확 일치 location 에 옛 `proxy_pass http://…:8000/;` 를 옮겨 붙인 형태(경로가 `/` 로 바뀐다)는 쓰지 않는다.

### 3.4 닫힌 경로를 쓰는 법
- 관리자 페이지(029·030)에서 쓴다 — 아래 박스 안 호출은 비상용.
- 분석 API·`/refresh`: collect 박스에서 `curl localhost:8000/<경로>`(수집기 포트는 serve 보안그룹에만 열려 있다). `/refresh` 는 `X-Refresh-Token` 헤더가 필요하다(값은 사람이 안다).
- API 문서: 관리자 페이지의 `/api/docs`. 수집기는 스키마 주소를 `/api` 로 알리므로(029 `UVICORN_ROOT_PATH`) `ssh -L 8000:localhost:8000 <collect>` 의 `localhost:8000/docs` 는 스키마를 못 불러온다 — 스키마 JSON 은 `localhost:8000/openapi.json` 으로 그대로 받는다.
- `/history/premium`·`streaks`·`streaks/bulk`·`/spreads`: serve 박스에서 같은 망 컨테이너로 `docker exec marketlens-web wget -qO- 'http://api:8000/<경로>'`. history 무거운 조회는 수집기도 답하지만 수집 CPU 를 쓰므로 api 로 부른다.
- 로컬 개발은 vite proxy 라 지금처럼 `/api/<경로>` 로 부른다.

### 3.5 엣지
- 백엔드에 새 라우트가 생김: 공개에서 기본으로 404. 웹·감시가 새 경로를 부르게 되면 그 스펙이 §3.1 표와 nginx 에 한 줄을 더한다 — 빠뜨리면 계약 테스트(웹·감시 호출 경로 대조, §4)가 실패한다.
- 수집기 :8000 은 여전히 serve 보안그룹에서만 닿는다. serve 박스 안의 컨테이너는 nginx 를 거치지 않고 두 백엔드의 모든 경로에 닿는다 — nginx 차단은 "serve 박스는 믿는다" 는 전제 위에 있다.
- nginx 설정은 web 이미지에 복사되므로 배포에서 이미지가 다시 빌드되며 반영된다.
- 027 의 caddy 접속 로그에는 도메인으로 들어와 막힌 요청이 404 로 남는다(탄력 IP 직접 스캔은 027 이 기록하지 않는다).

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 단언은 `listen 80` server 블록 안만 본다 — 029 가 같은 파일에 관리자 server 를 더해도 그대로다.
- nginx 계약(`server/tests/test_deploy.py`):
  - 백엔드로 넘기는 location 이 §3.1 표 여섯과 정확히 같다(전부 `=`, 가는 곳 포함). 각 location 이 접두 제거 rewrite + URI 없는 `proxy_pass` 모양이다. `/api/ws/spreads` 에 업그레이드 헤더.
  - `location /api/` 와 `location = /api` 는 404 만 답하고 `proxy_pass` 가 없다. 본문이 §3.2 JSON 이고 MIME 추정이 꺼져 있다.
  - `/api` 에 걸리는 정규식 location 이 없다. 접두 location `/api/ws/` 가 없다.
  - nginx 버전 숨김 설정이 있다.
  - 호출 경로 대조: (1) `web/src/**/*.ts(x)` 에서 `${API_BASE}` 바로 뒤의 리터럴 `/…` 를 `?`·백틱·`$`·따옴표 전까지 뽑아 `/api` 를 붙인다 — `${API_BASE}` 뒤가 리터럴 `/` 로 시작하지 않는 곳이 있으면 실패. (2) `web/public/*.html` 은 `fetch(`·`new WebSocket(` 인자의 따옴표 안 `/api/…` 만(주석 제외). (3) `ops/canary/`(027)의 요청 경로와 `docs/runbooks/uptime-monitor.md` 의 모니터 URL 경로. `ops/canary/` 가 아직 없으면(027 구현 전) 그 출처는 빈 집합이고, 생기면 테스트 수정 없이 대조에 들어간다. 셋의 합집합이 허용 목록의 부분집합이다.
  - 기존 테스트 고칠 것: `_nginx_api_block` 과 016 정규식 단언 → "정규식 location 없음" 단언 하나. `location = /api { return 404; }` 문자열 단언 → JSON 404 단언. `location /api/` 의 `proxy_pass http://${COLLECT_HOST}:8000/;` 단언 → 수집기로 가는 정확 일치 셋이 `${COLLECT_HOST}` 를 쓴다. spreads 테스트(`…_subpaths_to_server`)는 "`/api/spreads` 는 공개에 없다" 로. api `proxy_pass` 개수는 새 모양에 맞춘 값으로. 나머지(`try_files`·캐시 헤더·`COLLECT_HOST` 한 변수 치환)는 그대로.
- 로컬 Docker: `docker network create` → server 이미지의 python 으로 받은 요청 줄을 stdout 에 찍는 에코 서버 둘(망 별칭 `api`·`server`, web 보다 먼저) → 같은 망에 web 이미지(`COLLECT_HOST=server`, `NGINX_ENVSUBST_FILTER=^COLLECT_HOST$`). 요청은 `curl --path-as-is` 로 보내고 도착 여부는 에코 서버의 `docker logs` 로 본다.
  - 허용 여섯(`/api/ws/spreads` 는 업그레이드 헤더)이 맞는 에코 서버에 접두가 떼진 경로·쿼리 그대로 도착한다.
  - 닫힌 경로 — `/api/docs`·`/api/redoc`·`/api/openapi.json`·`/api/docs.html`·`/api/x.js`·`/api/premium`·`/api/premium/scan`·`/api/matrix`·`/api/arbitrage`·`/api/orderbook/upbit`·`/api/slippage/upbit`·`GET`·`POST /api/refresh`·`/api/history/premium`·`/api/history/streaks`·`/api/history/streaks/bulk`·`/api/spreads`·`/api/ws`·`/api/ws/other`·`/api`·`/api/nope` — 가 404 JSON(`Content-Type: application/json`)이고 에코 서버에 도착 0건.
  - §3.3 의 변형 경로와 끝에 `/` 가 붙은 허용 경로가 404, 도착 0건. `Server` 헤더에 버전이 없다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`.
- 커밋: (1) nginx 허용 목록·JSON 404·버전 숨김 + 기존 단언 수정 + 새 location 단언(300줄을 넘으면 기존 테스트 정리를 먼저 따로) (2) 호출 경로 대조 테스트 (3) §6 문서와 §5·§7.

**배포 뒤 — 사람(완료 조건 아님, status.md 비고 "운영 확인 대기")**
- 운영에서 위 닫힌 경로가 404(`curl --path-as-is`), 허용 경로가 200(`/api/ws/spreads` 는 101), 대시보드의 스프레드·기록/통계·수집 상태 탭과 랜딩·uptime·canary 가 정상.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 2026-09-28, worktree feat/028-api-allowlist
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
#   All checks passed! / 226 files already formatted / 839 passed (test_deploy.py 35 — 028 신규 7·수정 2·삭제 4)
cd web && npm run lint && npm run build           # oxlint exit 0 / ✓ built
# 로컬 Docker(OrbStack 29.4.0, nginx 1.27.5). echo.py·verify028.py 는 레포 밖(실행 세션 scratchpad)에 있다 — 다시 돌릴 때는 §4 산문대로 만든다.
# echo.py = python http.server 가 받은 요청 줄·Upgrade·Connection 을 stdout 에 찍고 200. verify028.py = 요청마다 curl --path-as-is -i 후 에코 서버 docker logs 줄 수로 도착을 센다
docker build -t ml028-server ./server && docker build -t ml028-web ./web
docker network create ml028-net
docker run -d --name ml028-echo-api    --network ml028-net --network-alias api    ml028-server python -u -c "$(cat echo.py)" api
docker run -d --name ml028-echo-server --network ml028-net --network-alias server ml028-server python -u -c "$(cat echo.py)" server
docker run -d --name ml028-web --network ml028-net -e COLLECT_HOST=server -e 'NGINX_ENVSUBST_FILTER=^COLLECT_HOST$' -p 18028:80 ml028-web
docker exec ml028-web nginx -t                    # syntax is ok / test is successful
python verify028.py   # 요청마다 curl --path-as-is -i, 도착 = 에코 서버 docker logs 줄 수 차이 → 54 PASS, 0 failure(s)
#   허용 6: /api/health?base=BTC&x=%20a%2Fb → 200, ECHO server GET /health?base=BTC&x=%20a%2Fb (health/collect·history/events 도 server)
#           /api/history/candles·/api/landing → ECHO api, /api/ws/spreads → ECHO api GET /ws/spreads?… upgrade=websocket connection=upgrade
#   닫힘 45: §4 목록 GET 20 + /api/x.png · 변형 16(/api//premium /api/%70remium /api/./premium /api/x/../premium /api/ws/../docs
#           /api/history/candles/../../premium /api/%2e/premium /api/Health /api/health;x /api/landing;/../docs /api/%2570remium
#           /api/ws/spreads%2F..%2F..%2Fdocs …) · 끝에 / 붙은 허용 6 · POST /api/refresh · /api/docs?x=1
#           → 전부 404, Content-Type application/json, 본문 = 앱 404 JSON, arrived=0
#   /API/health → 404 text/html(정적) arrived 0 · /api/%00 → 400 버전 없음 · Server 헤더 = {'nginx'}
docker rm -f ml028-web ml028-echo-api ml028-echo-server && docker network rm ml028-net && docker rmi ml028-web ml028-server
```

## 6. 갱신할 문서
줄 번호는 022 재작업이 머지된 main 기준(이 초안 작성 시 `feat/022-landing-rebuild` 와 같다).
**이 PR 이 고치는 문서**
- `docs/context/status.md` — deploy 행의 "`/api/history/{premium,streaks,candles}`·`/api/ws/`(WebSocket 업그레이드)·`/api/spreads`(정확 일치, 018)·`/api/landing`(정확 일치, 022) 는 api 로 분기" 와 그 앞 "`/api/` 는 `COLLECT_HOST:8000` 으로 접두 제거(021 템플릿 치환)" 를 "`/api` 는 허용 목록 여섯만(028) — 수집기로 `/api/health`·`/api/health/collect`·`/api/history/events`, api 로 `/api/history/candles`·`/api/landing`·`/api/ws/spreads`, 나머지 404 JSON" 으로. 비고에 "028 운영 확인 대기". spreads 행의 "`/refresh` 는 001 즉시 갱신 트리거 노출" 뒤에 "(공개 주소에서는 404, 028)". 알려진 빚에 `(028) /api/history/events 는 기간 상한 없이 공개 — 수집기 부하 위험, 창 상한은 후속`, `(028) 닫힌 API(문서·분석·/refresh·history 무거운 조회·/spreads)는 029 전까지 박스 안에서만 부른다`. 알려진 빚에 `(028) 003·004·016·018·021 의 해당 문장이 028 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기` 추가.
- `CLAUDE.md` — 스펙 인덱스 028 행 상태 → DONE.
- `docs/context/architecture.md` — 113행 "배포는 nginx `/api/ → server:8000/`" → "배포는 nginx 허용 목록(028)만 넘긴다". '배포 토폴로지' serve 줄(154행)의 nginx 분기 문장을 status.md 와 같은 뜻으로. '현재 구조' 168행 deploy 의 "`web/nginx.conf`(`/api/` → `server:8000/` 접두 제거" 와 169행 016 의 "정규식 location(`^/api/history/(premium|streaks|candles)` → `api:8000`…)" 을 허용 목록 정확 일치로, 그리고 api-allowlist 항목 한 줄(공개 server 정확 일치 여섯·나머지 JSON 404·버전 숨김, 계약은 `tests/test_deploy.py` 가 호출 경로와 대조). "계약 규칙" 절에 "공개 `/api` 는 허용 목록이다 — 웹·감시가 새 API 를 부르면 그 스펙이 nginx 허용 목록에 한 줄을 더한다(028)".
- `docs/context/dev-setup.md` — 'docker 통합 기동' 절(72행)의 "`/api/*` 는 nginx 가 server 로 프록시(접두 제거)하되 …" 를 허용 목록 설명으로, 분리 확인의 "`stop api` 뒤 `/api/spreads`·`/api/history/candles?base=BTC` 둘 다 502" → "`/api/history/candles?base=BTC`·`/api/landing` 502", "`stop redis` 면 `/api/spreads` 503" → "`/api/landing` 의 `live` 가 null", 끝에 "닫힌 경로(`/api/docs` 등)는 404". '## web' 절(22행) 코드블록 아래에 "vite proxy 는 허용 목록과 무관하게 모든 `/api/*` 를 넘긴다" 한 줄.
- `docs/runbooks/ec2-split.md` — 118행·125행의 `curl …/api/spreads` 를 `docker exec marketlens-web wget -qO- http://api:8000/spreads` 로.
- `docs/specs/005-history.md` — `/history/premium`·`/history/streaks`·`/history/streaks/bulk` 를 정의하는 절의 첫 문장 끝에 "공개 주소에서는 404(028)".
- `docs/specs/007-deploy.md` — 23행 web 줄의 "nginx 는 `/api/` 를 `server:8000/` 로 프록시하고" → "nginx 는 허용 목록(028)의 `/api` 경로만 프록시하고", 33행 "**`/api/*` 는 web 이 server 로 넘기며 `/api` 접두를 뗀다.**" → "**`/api` 는 허용 목록만 넘기며(028) 접두를 뗀다.**".
- `docs/specs/008-usdt-staleness.md` — 35행·40행의 `/api/spreads` 확인을 `docker exec marketlens-web wget -qO- http://api:8000/spreads` 로.
- `docs/specs/022-landing.md` — §4 144행의 "같은 순간 `/api/spreads`" → "같은 순간 `http://api:8000/spreads`(박스 안 — 공개에서는 028 이 닫는다)".
- `docs/specs/027-observability.md` — §3.1 "nginx `location /api/` 를 거쳐" → "nginx 를 거쳐"(이미 반영돼 있으면 그대로).
- `docker-compose.yml` 80행 주석 "nginx 의 `location /api/` 업스트림" → "nginx 허용 목록 중 수집기로 가는 location 의 업스트림(028)", `web/Dockerfile` 11행 주석 "/api/ → ${COLLECT_HOST}:8000/ 프록시" → "/api 허용 목록 프록시(028)".

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 017 — §3.5 59행 "`location /api/ws/`(앞부분 일치)를 `proxy_pass http://api:8000/ws/;`" → "`= /api/ws/spreads` 정확 일치 + 접두 제거 rewrite(028)", §4 77행 nginx 계약 문장도 같은 뜻으로(그 테스트는 028 이 지웠다). 코드 `server/app/features/spreads/ws.py` 머리 주석 "nginx 가 `/api/ws/` 를 api 로만" 도 같은 뜻으로.
- 016 — §4 78행 "`location /api/` 의 기존 `proxy_pass` 와 `location = /api` 404 유지" → "공개 `/api` 는 028 허용 목록".
- 018 — §1 9행·§2 16행 "nginx `/api/spreads` → `api` 분기" → "공개에는 없다(028) — 박스 안에서 `http://api:8000/spreads`", §3 40행 "`stop api` 면 `/api/spreads` 는 502" → 공개에서는 404.
- 021 — 64행 "serve 의 `/api/*`(수집 경로)만 502" → "수집기로 가는 공개 셋만 502, 그 밖의 `/api/*` 는 404(028)".
- 003 — §3.3 110행 "`POST /refresh` 200" bullet 끝에 "공개 주소에서는 404(028) — 박스 안·관리자 페이지(029)에서 부른다".
- 004 — §1(9행)의 "curl/브라우저로 직접 호출하는 BE 전용 도구" 뒤에 "(공개 주소에서는 닫혀 있다 — 028, 관리자 페이지 029)".
- 016 — §3.3 표의 "그 외 `/api/*` | `server:8000`" 행 삭제·api 로 가는 history 행을 `= /api/history/candles` 로, 57행 → "공개 분기는 전부 정확 일치다(028)", 58행 → "공개 server 의 `/api` 는 028 허용 목록이 정한다", 60행 "위 네 경로만 502" → "api 로 가는 공개 경로(`/api/history/candles`·`/api/landing`·`/api/ws/spreads`)만 502", §4 81행의 `curl localhost:8080/api/history/premium?...` → `…/api/history/candles?base=BTC`.
- 018 — §3.3 38행 "`/api/spreads` … `api:8000/spreads` 로 보낸다" → "`GET /spreads` 는 공개 주소에 없다(028 — 022 재작업 뒤 부르는 곳이 없다). 박스 안에서 `http://api:8000/spreads`", 39행 "`location /api/`(그 외 → `server`)·…" → "공개 `/api` 는 028 허용 목록", §4 59행 nginx 계약을 "`/api/spreads` 는 공개에서 404(028)" 로, 61행의 `/api/spreads` 언급은 그대로(브라우저가 부르지 않는다는 확인).
- 021 — 46행 "`location /api/` 의 업스트림이 `http://<COLLECT_HOST>:8000/`" → "수집기로 가는 정확 일치 세 location 의 업스트림이 `COLLECT_HOST`(028)", 58행 nginx 경로 목록을 status.md 와 같은 뜻으로, 59행 "외부에서는 `/api/...` 로 serve 를 거친다" → "외부에는 `/api/health`·`/api/health/collect`·`/api/history/events` 만 열린다(028)", 85행 "`location /api/` 의 업스트림만" → "수집기로 가는 location 의 업스트림만", 91행 `curl localhost/api/spreads` → `docker exec marketlens-web wget -qO- http://api:8000/spreads`.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `web/nginx.conf` — 공개 server 에 `server_tokens off`, 정확 일치 location 여섯(수집기 셋 `${COLLECT_HOST}`·api 셋, 전부 `rewrite ^/api/(.*)$ /$1 break` + URI 없는 `proxy_pass`, ws 만 업그레이드 헤더), `= /api`·`/api/` 는 `types {}`·`default_type application/json`·`return 404 '<앱 404 JSON>'`. 016 정규식·017 `/api/ws/`·018 `= /api/spreads`·021 `location /api/` 프록시는 지웠다.
  - `server/tests/test_deploy.py` — 작은 nginx 토큰 파서(`listen 80` server 블록만)·허용 표 `PUBLIC_API`, 새 테스트 7개 — nginx 5개(허용 여섯·ws 업그레이드·JSON 404·정규식 없음+버전 숨김·닫힌 경로가 404 location 으로 감)와 호출 경로 대조 2개(대조·canary 추출 자체 확인). 치환 변수 테스트는 개수만 고쳤고, 016·017·018·022 의 옛 분기 테스트 4개는 지웠다.
  - 문서: `docs/context/{status,architecture,dev-setup}.md`, `docs/runbooks/ec2-split.md`, `CLAUDE.md`, 스펙 005·007·008·022·027·028, `docker-compose.yml`·`web/Dockerfile` 주석.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - JSON 404 본문은 테스트가 `create_app()` 의 `/nope` 응답 본문과 문자 단위로 비교한다 — 앱 형식이 바뀌면 nginx 쪽도 따라 고치게 된다.
  - `= /api`·`/api/` 가 같은 세 줄을, 여섯 location 이 프록시 헤더 4개를 반복한다 — nginx 에 상수를 나눌 수단이 `set`(매 요청 실행)뿐이고, 헤더를 server 수준에 두면 ws location 에서 상속이 끊겨 읽기 어렵다.
  - 호출 경로 추출: (1) `web/src/**/*.ts*`. (2) 주석 제거는 `<!-- -->`·`/* */`·공백 뒤 `//`. (3) canary 는 형식을 모르므로 `ops/canary/` 아래 텍스트 파일 전부(숨김·`node_modules`·`__pycache__`·`.md` 제외)에서 따옴표·백틱·`}`·URL 호스트 바로 뒤의 `/api…` 를 뽑고, 확장자별 주석(`.js`·`.ts` 계열 `//`·`/* */`, `.py`·`.sh`·`.yml`·`.toml` `#`)을 뺀다. 추출이 깨져 조용히 통과하지 않게 web/src·web/public·uptime 런북은 빈 집합이면 실패, canary 만 빈 집합 허용.
  - 005 "절의 첫 문장 끝" 은 세 엔드포인트 문단 각각의 첫 문장 끝으로 읽었다.
  - 로컬 Docker 검증은 호스트 포트 18028, 이름 `ml028-*`(끝나고 컨테이너·망·이미지 삭제).
  - 함께 고친 절: 028 §4 (3) canary 빈 집합 규칙(설계 세션 답). 007 §3 web 줄의 괄호 안 분기 목록(016 정규식·017 `/api/ws/`·018 `/api/spreads`) — §6 은 앞 구절만 지정했지만 남기면 틀린 문장이라 허용 여섯으로. architecture.md '현재 구조' 017 의 `location /api/ws/` → `= /api/ws/spreads`, 018 의 `location = /api/spreads` → "공개에 없다"(같은 이유).
- PR 본문에 옮길 것: §6 "담당자에게 제안" 003·004·016·018·021 다섯 항목 그대로. 덧붙여 실행 중 본 어긋남(고치지 않음):
  - `CLAUDE.md` §4 018 행 범위 — "nginx `/api/spreads` 를 `api` 로" → 028 뒤 공개에서 닫힘(018 담당).
  - `docs/runbooks/uptime-monitor.md` "왜 `/health` 하나로" — "`/api/health` 는 serve 박스의 api 가 답하지만" → nginx 가 수집기로 넘긴다(028 §3.1·027 §3.1). 판정(정체 → 503)은 같다.
  - `docs/context/architecture.md` '현재 구조' 017 — compose `api` 의 "`depends_on: redis`" → 021 이 depends_on 을 모두 없앴다(`test_compose_has_no_depends_on_anywhere`). 028 과 무관해 그대로 둠.
- 설계 세션 검토 뒤 고친 것: `test_nginx_template_substitutes_only_collect_host` 의 파일 전체 proxy_pass 개수 두 줄을 지웠다(경로별 업스트림은 허용 여섯 테스트가 고정 — 029 관리자 server 가 같은 파일에 프록시를 더해도 깨지지 않게), read timeout 금지를 공개 블록만 보게, web/src 에서 `${API_BASE}` 밖의 `API_BASE` 사용을 실패로·따옴표 안 리터럴 `/api/…` 도 대조에, canary YAML 의 따옴표 없는 값도 추출. index fallback 테스트는 실행 세션이 이름을 `test_nginx_falls_back_to_index_under_app_only` 로 바꾸고 `/api` 단언 셋을 지웠다(허용 목록 테스트로 옮김). `test_canary_paths_are_picked_up_once_the_directory_exists` 는 추출 규칙의 자기 확인이다 — 027 의 ops/canary 가 오면 실제 대조로 충분한지 보고 정리한다. 022 §3.1 30행(`= /api/spreads` 를 기준으로 삼던 문장)과 `docs/runbooks/uptime-monitor.md` 의 라우팅 설명(수집기가 답한다)도 고쳤다.
- 남은 빚:
  - canary 추출의 JS 블록 주석 제거는 문자열 안의 `/*` 를 가리지 못한다 — 027 코드가 오면 실제 파일로 확인.
  - status.md 알려진 빚 셋 — `/api/history/events` 기간 상한 없음, 닫힌 API 는 029 전까지 박스 안, 003·004·016·018·021 문장 반영 대기.
  - 배포 뒤 운영 확인(사람, §4) 대기 — 실제 WebSocket 101 은 로컬 에코 서버로 보지 않았다(업그레이드 헤더 도착까지만).
  - 호출 경로 대조는 `${API_BASE}` 를 거치지 않는 web/src 호출(`API_BASE + …`·리터럴 `'/api/…'`)과 변수로 넘긴 `fetch(url)` 을 못 잡는다 — 지금 코드엔 없다. canary 추출 규칙은 027 의 실제 코드가 오면 한 번 확인할 것.
