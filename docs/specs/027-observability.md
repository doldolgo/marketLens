# 027 — observability

상태: DONE | 의존: 002 web-shell(URL 쿼리 키), 007 deploy(compose·nginx·배포 워크플로), 011 health(`/health/collect` 폴링), 013·014(기록 탭 60초 재조회·`/history/candles`), 016 process-split(api 역할·nginx 분기), 017 spreads-push(허브·프레임), 021 infra-split(박스·IAM), 022 landing(`/app/`·`/api/landing`), 023 domain-tls(caddy), 025 slack-alerts(`/health` 두 역할·Slack 채널)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 두 가지를 안다. 첫째, 사용자 눈에 서비스가 살아 있는지를 10분 안에 알고, 박스의 메모리·디스크·CPU 크레딧 문제를 사용자보다 먼저 안다. 지금은 `/api/health` 를 수집기가 답해서 serve 의 api 나 data 의 Redis 가 죽어도 200 이고, 메모리·디스크 경보가 없다. 둘째, 사람들이 어떤 주소로(외부 출처·UTM·공유 링크) 들어와 대시보드를 얼마나 오래 열어 두는지, 지금 몇 개가 열려 있는지를 서버 기록으로 본다. 들어온 뒤의 탭·필터 조작은 요청을 만들지 않아 서버가 볼 수 없다 — 후속 브라우저 분석 스펙이 맡는다. 기록은 전부 AWS 서울(ap-northeast-2)에 둔다.

## 2. 범위
- 만드는 것: caddy 접속 로그(IP 뒷자리·검색어·헤더를 지우고 남김), api 의 WebSocket 접속 수 게이지, CloudWatch Agent 설정(`ops/cloudwatch/`), canary 스크립트(`ops/canary/`), 런북 `docs/runbooks/cloudwatch.md`, 배포 계약 테스트. `ops/` 는 박스에 올리는 설정(앱 코드 아님)을 두는 최상위 폴더로 이 스펙이 만든다.
- 하지 않는 것:
  - 브라우저 쪽 분석·개인정보처리방침·폰트 자체 호스팅 — 후속 브라우저 분석 스펙.
  - 서버 기록의 국가·도시 해석 — IP 뒷자리를 지운 채 저장한다.
  - Route 53 헬스체크 — 박스 밖 시점은 025 의 uptime 서비스가 덮는다. Sentry·APM·트레이스 — 백엔드 ERROR·500 은 025 가 Slack 으로 보낸다.
  - 수집기의 Influx·Redis 쓰기 실패 알림 개편 — §3.5 3단계(최근 15분 1분 봉 확인)가 대신 잡는다.
  - api 헬스의 공개 경로 — api·Redis·Influx 는 canary 3·4단계가 밖에서 보고, 앱 안쪽 상태는 로그인해야 들어가는 관리자 페이지(029)에서 본다. 밖에 여는 헬스 경로는 지금의 `/api/health` 하나로 둔다.
  - 기록 탭 검색칸의 입력 검증 — status.md 빚으로 남긴다. (`/?쿼리` → `/app/` 301 이 `utm_*` 링크를 대시보드로 보내던 문제는 022 가 옛 대시보드 키일 때만 옮기도록 고쳤다, 2026-10-01)
  - 이상 탐지·복합 경보, 별도 대시보드 도구.
- 바꾸는 기존 것:
  1. 023 — Caddyfile 을 `caddy/Caddyfile` 로 옮겨 디렉터리째 바인드하고, serve 배포가 `up` 뒤에 caddy 설정을 다시 읽힌다. 파일 하나를 바인드하면 git 이 새 파일로 바꿔 써도 컨테이너는 옛 내용을 보고, compose 는 정의가 안 바뀐 caddy 를 다시 만들지 않는다 — 지금은 Caddyfile 을 고쳐 머지해도 운영에 반영되지 않는다. 도메인 블록에 접속 로그·`Referrer-Policy` 가, 기본 로거에 같은 지우기 규칙이 붙는다. 요청 경로는 그대로다.
  2. 016·018 — api 백그라운드 태스크에 게이지 하나(`STATSD_ADDR` 가 있을 때).
  3. 007 — compose(caddy 바인드 둘, api 의 `STATSD_ADDR`·호스트 게이트웨이), 배포 워크플로 serve 스크립트, nginx 접속 로그 끔.
  4. 021 — serve 박스에 스왑 1GB(승격은 측정 뒤 판단), data·serve 에 역할 `marketlens-cwagent`·IMDS 토큰 필수·hop 1, collect 역할에 에이전트 정책(§3.7).
  5. 025 §3.6 — uptime 모니터는 `/api/health` 하나 그대로. 런북의 "`/api/health` 는 api 가 답한다" 설명만 바로잡는다(실제로는 수집기가 답한다).
- 담당: 016·017·018·021·023·025 는 hereokay 담당이다 — 이 PR 은 그 스펙들을 **고치지 않는다**(CLAUDE.md §5). §6 의 "담당자에게 제안" 목록을 PR 본문에 적고 담당자가 반영한다. 007 은 이 레포 주인 담당이라 고친다. CLAUDE.md §5 허용 목록 밖에서 이 스펙이 고치는 것(사람 승인): 루트 `docker-compose.yml`·`.gitignore`·`.github/workflows/deploy.yml`, `Caddyfile` 이동, 신규 `caddy/`·`ops/`, CLAUDE.md §2·§5.

## 3. 동작

### 3.1 무엇이 무엇을 보는가
읽는 계약(025 §3.5 복사): 두 역할 모두 `GET /health` 는 `{"status","version","lastTickAt"}` 를 답하고 `status == "ok"` 만 200, 나머지는 503 이다. collector 는 메모리의 마지막 틱으로 `starting`·`stale`(30초 무틱)을 판정하고 Redis 를 보지 않는다. 밖에서 부르는 `/api/health` 는 nginx 를 거쳐 collector 가 답한다 — serve 의 api 나 data 의 Redis 가 죽어도 200 이다.

| 감시 | 주기 |
|---|---|
| uptime | 3분 |
| canary | 5분 |
| 에이전트 | 1분 |

- uptime 서비스(025)는 `/api/health` 하나로 serve 박스·caddy·nginx·collect 박스·수집 정체를 본다.
- canary(§3.5)는 화면·수집기·api·Redis·Influx·WebSocket 푸시 경로를 본다 — api·Redis·Influx 가 죽은 것은 3·4단계가 10분 안에 잡는다.
- 에이전트(§3.4)는 박스의 메모리·디스크를 본다.
- 새 공개 헬스 경로는 만들지 않는다. api 역할의 `/health`·거래소별 수집 상태 같은 앱 안쪽 상태는 관리자 페이지(029)에서 본다.

### 3.2 접속 로그 — caddy
- 이 기록은 개인정보로 다룬다. 남기는 항목·목적·보존기간(박스 안·CloudWatch 둘 다)을 후속 스펙의 처리방침에 옮기고, CloudWatch 로 보내는 것은 처리방침 게시 뒤다(§3.4).
- 서버 기록이 보는 것: 들어온 주소(첫 로드·새로고침·공유 링크의 경로와 쿼리, `utm_*`), 외부 출처(`Referer` 의 origin), 기기·브라우저(`User-Agent`), 대시보드를 열어 둔 시간(`/api/ws/spreads` 는 연결이 끝날 때 상태 101·`duration` 한 줄), 오류 응답.
- 기록은 caddy 한 곳에서 한다. nginx 접속 로그는 끈다(공개 server — 관리자 server 는 029 가 자기 기록을 남긴다. 오류 로그는 남긴다) — nginx 기본 형식의 마지막 칸이 caddy 가 넣은 원 IP(`X-Forwarded-For`)다.
- 로그 설정은 `caddy/Caddyfile` 안의 이름 있는 조각 `access_log` 하나에 두고 도메인 블록(`kimptrack.com`)만 불러온다(`www` 는 apex 로 301 만 하는 블록이라 기록하지 않는다 — 022·023). `http://` catch-all(탄력 IP 직접 접속·봇 스캔)은 기록하지 않는다.
- 파일: 컨테이너 `/var/log/caddy/access.log`, 호스트 `./logs/caddy/`(레포 루트 기준 바인드, git 무시 — 배포의 `git reset --hard` 는 추적 안 하는 파일을 지우지 않는다). 권한 0644(사람이 호스트에서 읽게). 현재 파일 50MiB 에서 회전, gzip 된 회전 파일 5개까지, 회전 파일은 90일 뒤 지운다(caddy 기본).
- 한 줄에 남기는 것은 허용 목록이다. caddy 기본 JSON 에서
  - 요청 헤더·응답 헤더를 통째로 지운다. Accept-Language·Sec-Ch-Ua·임의 헤더(IP 가 든 것 포함)가 합쳐지면 방문자를 가려낼 수 있고, 응답 `Location` 에는 022 의 301 이 쿼리(검색어 포함)를 그대로 싣는다.
  - 대신 `ua`(`User-Agent` 그대로)와 `referer` 두 필드를 붙인다. `referer` 는 http(s) 스킴+호스트(+포트)만 남기고, 그 모양이 아니면 빈 값이다.
  - `request.remote_ip`·`request.client_ip` 는 IPv4 /24, IPv6 /48 로 자른다. 지금 두 필드는 같은 값이라 하나만 자르면 원본이 남는다.
  - `request.uri` 의 쿼리 키 `s.q`·`g.q`·`p.q`(스프레드·갭·선선갭 검색어)를 지운다. 나머지 키는 남긴다 — 어떤 주소로 들어왔는지를 보기 위해서다.
- caddy 기본 로거(오류 줄 — 예: 업스트림 502)에도 IP 자르기·쿼리 세 키·헤더 지우기를 똑같이 건다. docker 로그에 원 IP 가 남지 않게.
- 기록하지 않는 요청: 경로 `/api/health`·`/api/health/collect`(수집 상태 탭 5초 폴링)·`/api/landing`(랜딩 10초 폴링)·`/api/history/events`·`/api/history/candles`(기록 탭이 보이는 동안 60초마다 부른다 — 013 §3.5·014 §3.7), `User-Agent` 에 `KimpTrack-Canary` 가 든 요청(§3.5). 폴링·감시가 방문 기록을 덮기 때문이다. 이 경로들의 5xx 는 5xx 경보에 안 잡힌다. canary 2~4단계가 같은 백엔드(수집기·api·Influx)를 보지만 `/api/health/collect`·`/api/history/events`·`/api/landing` 자체의 5xx 는 감시하지 않는다.
- 도메인 블록의 모든 응답에 `Referrer-Policy: strict-origin` 을 붙인다. 브라우저 기본값은 같은 출처 요청에 전체 URL 을 `Referer` 로 보낸다.

### 3.3 WebSocket 접속 수
- 접속 수 = 017 허브가 들고 있는 열린 `/ws/spreads` 연결 수(`waiting` 중 포함). 열린 `/app/` 페이지 수이지 사람 수가 아니다 — 숨겨진 탭·부하 시험 연결이 섞인다.
- 게이지는 spreads 기능 안(허브 옆)에 두고 api lifespan 이 태스크 하나로 띄운다. collector 는 띄우지 않는다(허브가 있어도 늘 0 이라 api 값을 덮는다). core 공개 함수는 만들지 않는다.
- 기동 즉시 한 번, 이후 10초마다 `marketlens.ws_clients:<정수>|g` 한 줄을 UDP 로 한 번 보낸다(non-blocking, 재시도 없음). 0명도 보낸다 — 안 보내면 "0명" 과 "api 가 죽음" 이 구별되지 않는다.
- 주소는 env `STATSD_ADDR`(`host:port`)다. 비었거나 없으면 태스크를 띄우지 않는다(로컬·테스트 기본). `host:port` 로 나눌 수 없으면 WARNING 1줄 뒤 끈다(앱은 뜬다). 이름은 태스크가 스레드에서 풀고, 실패하면 다음 회차에 다시 푼다. 전송·풀기 실패는 WARNING 을 10분에 1줄. 라이브러리 추가 없음.
- compose 의 api 는 `STATSD_ADDR=host.docker.internal:8125` 를 받고 그 이름을 호스트 게이트웨이로 잇는다. 받는 쪽은 serve 호스트의 에이전트(§3.4)이고, 없으면 UDP 가 버려질 뿐이다.

### 3.4 CloudWatch Agent — 지표·로그
- 세 박스 호스트에 설치한다(컨테이너 아님, arm64 패키지, systemd, root 로 돈다 — `run_as_user` 를 두지 않는다. 홈 디렉터리가 0750 이라 다른 사용자는 로그 파일에 못 닿는다). systemd 로 메모리 200MB 상한.
- 설정의 진실은 `ops/cloudwatch/collect.json`·`data.json`·`serve.json`(지표)과 `serve-logs.json`(최상위 키 `logs` 하나). 적용은 사람이 한다 — 설정을 바꾼 PR 이 머지되면 그 박스에서 에이전트 설정을 다시 불러온다(런북). serve 는 로그 전송을 켠 뒤로 늘 두 단계다: `serve.json` 을 불러온 다음 `serve-logs.json` 을 덧붙인다. 앞 단계만 하면 로그 설정이 지워진다. 배포 워크플로는 에이전트를 건드리지 않는다.
- 지표 파일 공통: 네임스페이스 `MarketLens`, 전역 추가 차원 `InstanceId` 하나, 호스트명 차원 없음, 디스크는 루트 `/` 만·장치 차원 없음. 플러그인 고유 차원(디스크 `path`·`fstype`, procstat 프로세스 식별자, StatsD `metric_type`)은 남는다 — 지표마다 조합이 하나라 지표 1개 = 과금 1개다. 경보는 그 지표가 처음 보인 뒤, 실제 지표 목록에서 확인한 차원 그대로 만든다.
- collect(주기 300초): 메모리 가용률·디스크 사용률 둘만. 1 vCPU 를 수집기가 80~100% 쓰므로 가볍게 둔다. 에이전트 자체 CPU(systemd `CPUUsageNSec` 10분 차이)가 1% 를 넘으면 에이전트를 지운다(§7). 수집기 컨테이너 CPU 전후 비교로는 가르지 않는다 — 1분 값이 66~100% 로 흔들리고, 호스트의 에이전트를 재지 않는다.
- data(60초): 메모리 가용률, 스왑 사용률(차트용), 디스크 사용률, 프로세스 RSS `influxd`·`redis-server`(실행 파일 이름으로).
- serve(60초): 메모리 가용률, 디스크 사용률, 프로세스 RSS `caddy`(실행 파일)·api(명령줄 `uvicorn app.main:app` — 앱 모듈까지 못박는다), StatsD 수신 `:8125`(60초 집계)로 받은 `marketlens.ws_clients`.
- 로그(`serve-logs.json`): `/home/ubuntu/marketlens/logs/caddy/access.log` → 로그 그룹 `/marketlens/serve/caddy`, 스트림 이름 = 인스턴스 ID, 클래스 STANDARD(IA 는 지표 필터가 안 되고 만든 뒤 못 바꾼다), 보존 90일. **처리방침 게시 전에는 적용하지 않는다** — 그때까지 기록은 박스 안에만 있다.
  - 보존 90일: 분기 단위 비교면 충분하다. 통신비밀보호법 시행령의 3개월 보관 의무는 원 IP 를 요구해 이 기록으로는 채울 수 없다 — 우리에게 해당하는지는 사람이 법률 확인한다(status.md 빚).
- 지표 필터 1개(로그 전송을 켤 때 함께 만든다): `status ≥ 500` 이고 `request.uri` 가 `/api/ws/spreads` 가 아닌 줄 수 → `MarketLens/http_5xx`. WS 를 빼는 이유: serve 배포 때 열린 대시보드가 전부 재접속하며 502 를 낸다. 그 전까지 5xx 는 canary·uptime 으로만 보인다.
- 에이전트는 파일 위치를 기억한다 — caddy 재생성·회전 뒤에도 중복·누락 없이 이어 보낸다.

### 3.5 canary — 5분마다 밖에서 도는 점검
- 일반 Lambda + EventBridge Scheduler, 서울. CloudWatch Synthetics 는 이 계정에서 조직 서비스 제어 정책(SCP)이 막아 쓸 수 없다(2026-09-30 확인, 계정 관리자도 못 푼다).
  - 함수 `marketlens-smoke`: 런타임 `nodejs22.x`, arm64, 메모리 128MB, 제한 60초, handler `index.handler`, 코드 = `ops/canary/index.mjs` 하나를 zip 루트에. 실행 역할 `marketlens-smoke-lambda`(관리형 정책 `AWSLambdaBasicExecutionRole` 하나). 비동기 호출 재시도 0 — 켜 두면 실패 한 번이 세 번으로 센다.
  - 일정 `marketlens-smoke`(EventBridge Scheduler): `rate(5 minutes)`, 유연한 시간 창 끔, 대상 = 그 함수, 재시도 0. 일정 역할 `marketlens-smoke-scheduler`(그 함수 하나의 `lambda:InvokeFunction` 만).
  - 로그 그룹 `/aws/lambda/marketlens-smoke` 보존 30일.
- 스크립트는 `ops/canary/` 에 두고 만들고 올리는 것은 사람이 한다. handler 하나가 표준 `fetch`·`WebSocket` 으로 네 단계를 돌고 실패하면 단계 번호가 든 메시지로 던진다 — Lambda 는 던진 호출과 시간 초과를 `Errors` 로 센다. 같은 파일이 로컬 `node` 로도 돈다. Node.js 22 가 필요하다 — 4단계가 내장 `WebSocket` 을 쓴다(Node 20 에는 없다). 주소는 env `CANARY_BASE_URL`(기본 `https://kimptrack.com`)이고 WebSocket 주소도 여기서 스킴만 바꿔 만든다. 1~3단계는 요청마다 제한 8초, 재시도 없음. 모든 요청의 `User-Agent` 는 `KimpTrack-Canary/1` 이다 — WebSocket 은 표준 API 에 헤더 인자가 없어 Node 내장 WebSocket 의 헤더 옵션으로 붙인다.
- 한 번 실행에 네 단계를 순서대로 돈다. 하나라도 실패하면 그 실행은 실패다.
  1. `GET /` — 200, 본문에 `KimpTrack`.
  2. `GET /api/health` — 200, `status == "ok"`(수집기 틱 30초 이내).
  3. `GET /api/history/candles?base=BTC&res=1m&start=<지금 epoch 초 − 900>&end=<지금 epoch 초>` — 200, `count ≥ 1`. 읽는 계약(014 복사): `start`·`end` 는 epoch 초(0~4,102,444,800, 밖이면 422), 생략한 쿼리는 `dom=upbit`·`fx=binance`·`dir=kimp`, 응답은 `{base,res,dom,fx,dir,startTs,endTs,count,fetchedAt,candles[]}`, 1m 창 상한 86,400초, 진행 중 창은 싣지 않는다, Influx 불달 503. 봉은 분이 닫힌 뒤 쓰이므로 15분 동안 0개면 쓰기가 멈춘 것이다. 이 요청은 nginx 를 거쳐 api 로 가므로 api 생존·api→Influx 읽기도 함께 본다.
  4. `/api/ws/spreads` — 15초 안에 `snapshot`(`rows` 길이 ≥ 1), 이어서 5초 안에 `delta` 를 받으면 끊는다. 읽는 계약(017 복사): 프레임은 바이너리 1개 = gzip 으로 압축한 JSON 1개, `type` 은 `snapshot`·`delta`·`heartbeat`·`waiting`. 접속 직후 표가 있으면 `snapshot`, 없으면 `waiting` 뒤 첫 표에 `snapshot`. 수집은 접속자와 무관하게 매 틱 표를 게시하고, 멈추면 `heartbeat` 만 온다 — `delta` 가 푸시 경로(수집기 → Redis → api 허브) 전체의 증거다.
- 5분보다 자주 돌리지 않는다 — 실행마다 수집기·api·WebSocket 을 한 번씩 부른다.

### 3.6 경보 → Slack
- 모든 경보는 서울 SNS 주제 `marketlens-alerts` 하나로 보내고, Amazon Q Developer in chat applications(구 AWS Chatbot)가 025 와 같은 Slack 채널에 올린다. 풀릴 때(OK)도 보낸다.
- 상태검사(세 박스, 최댓값): `StatusCheckFailed_Instance` 60초 주기 3점 연속 → 재부팅, `StatusCheckFailed_System` 60초 2점 연속 → 복구(recover). 기간을 다르게 두는 것은 AWS 권장이다 — 같으면 두 동작이 경쟁한다. 데이터 없음은 무시(missing).
- 크레딧(data t4g.small — 최대 적립 576, serve t4g.micro — 최대 288): `CPUCreditBalance` 최솟값 300초 주기 3점 연속 최대치의 30%(data 173·serve 86) 미만, `CPUSurplusCreditsCharged` 합계 300초 1점 0 초과(unlimited 잉여 과금 시작). 데이터 없음은 무시. 잔고 경보는 최근 7일 최솟값을 보고 그보다 낮게 둔다(기본 30%).
- 메모리(세 박스, 최솟값): 메모리 가용률 10% 미만이 5분 연속(60초 5점, collect 는 300초 1점). data 근거: Influx 컨테이너 상한 1.3GiB(021 §3.1 — 넘으면 스왑 없이 Influx 만 재시작) + Redis 평소 ≈30MB + OS·docker·에이전트 ≈0.4GiB ≈ 1.73GiB 에서 가용 ≈8% — 10% 미만이면 Influx 가 상한에 가까워진 것이다(Redis 상한 600MB 는 Influx 쓰기가 몇 시간 막혀야 찬다). 데이터 없음은 경보(에이전트가 죽은 것을 알아야 한다).
- 디스크(세 박스): 사용률 최댓값 300초 1점 80% 초과. 데이터 없음은 경보.
- 요청: `http_5xx` 합계 300초 1점 10 이상. 데이터 없음은 정상(요청이 없으면 줄도 없다). 로그 전송을 켤 때 만든다.
- canary: `AWS/Lambda` `Errors`(차원 `FunctionName=marketlens-smoke`) 합계 300초 2점 중 2점 1 이상 — 연속 두 번(10분) 실패. 시간 초과도 `Errors` 다. 배포 중 1회 실패는 울리지 않는다. 데이터 없음은 경보 — 일정이 멈추면 `Errors` 점이 생기지 않는다. 직접 실행이 통과하고 일정을 만든 뒤 만든다.
- 스왑 경보는 두지 않는다 — 한 번 찬 스왑은 압박이 끝나도 잘 안 줄어 경보가 풀리지 않는다.
- 경보는 처리방침 전 17개, 로그 전송 뒤 18개다(상태 6·크레딧 4·메모리 3·디스크 3·canary 1, + 5xx 1). 예산: AWS Budgets 월 $130, 알림 실제 85%·실제 100%·예측 100%(이메일)를 사람이 건다.

### 3.7 박스·IAM·비용 (사람 — 런북)
- 순서: 예산 → 기존 경보·지표 수 확인 → IMDS → 역할 → data 에이전트(RSS 24시간) → collect 에이전트(에이전트 CPU) → serve 스왑 1GB → serve 에이전트 → Slack 연결 → 경보(canary·5xx·잔고 제외 — 메모리·디스크는 세 박스 지표가 보인 뒤, 데이터 없음 = 경보라 먼저 만들면 곧바로 울린다) → canary(Lambda·일정) → canary 경보 → 잔고 경보(최근 7일 최솟값 확인 뒤) → serve 메모리 측정(24시간·배포 1회) → (처리방침 게시 뒤) serve 로그 전송·지표 필터·5xx 경보. 단계마다 확인·되돌리기.
- serve(t4g.micro 1GB)는 올리지 않고 **스왑 1GB** 를 붙인다(data 와 같은 방식, 무료). 스왑 없는 1GB 박스가 배포마다 web 이미지를 직접 빌드하는데(npm ci·vite build) 에이전트가 더해지기 때문이다. 에이전트를 띄운 뒤 24시간과 배포 1회 동안 메모리 가용률 최저·스왑 사용량·에이전트 RSS 를 재서 §7 에 적는다. 가용률이 10% 밑으로 내려가거나 스왑을 계속 쓰면 t4g.small 승격(월 +$7.6, 정지 몇 분)을 사람이 정한다 — 그때 이 스펙을 고치고 021 담당자에게 알린다.
- **역할을 붙이기 전에** data·serve 의 인스턴스 메타데이터를 토큰 필수(IMDSv2)·hop limit 1 로 둔다 — 도커 브리지 안의 컨테이너가 인스턴스 역할 자격증명에 닿지 못하게. data·serve 에 역할 `marketlens-cwagent`(관리형 정책 `CloudWatchAgentServerPolicy`)를 붙인다. collect 는 컨테이너가 S3 에 올리므로 hop 2 그대로(010)이고, 기존 역할 `marketlens-s3-snapshot` 이 붙어 있는지 먼저 확인한 뒤(status.md 남은 작업 — 없으면 붙인다) 같은 정책을 더한다. 컨테이너도 지표·로그 쓰기 권한에 닿지만 받아들인다.
- 콘솔 관리자가 할 일(CLI 사용자는 `iam:PassRole` 이 없다): collect 역할 부착 확인·세 박스 역할·정책, canary 의 Lambda·Scheduler 역할 둘과 함수·일정(만들 때 역할을 넘긴다), Q Developer Slack 채널 구성(채널 역할, Slack 워크스페이스 승인), EC2 동작 경보용 서비스 연결 역할 1회. 런북은 이 넷을 한 절에 묶는다.
- 월 비용(달러, 로그 전송 뒤 기준, 부가세 10% 별도):

| 항목 | 월 |
|---|---|
| canary | 0 |
| 에이전트지표 | 0.9 |
| 경보 | 0.8 |
| 로그 | 0 |
| 합계 | 1.7 |

  canary 는 Lambda·Scheduler·로그 모두 무료 한도 안이다(월 8,640회 × 수 초 × 128MB). Lambda 표준 지표(`Errors` 등)는 무료다. 에이전트 지표는 13개(collect 2·data 5·serve 5·5xx 1) 중 무료 10개를 넘는 3개 × $0.30, 경보는 18개 중 무료 10개를 넘는 8개 × $0.10. 로그는 서울 STANDARD 수집 $0.76/GB·저장 $0.0314/GB-월·Logs Insights $0.0076/GB 이고 각각 월 5GB 무료 — 폴링을 뺀 뒤 수 GB 안쪽으로 추정한다(실측 뒤 §7). 무료 한도는 계정 전체(모든 리전) 기준이다. SNS·Q Developer 는 무료.

### 3.8 엣지
- 에이전트가 죽음: 지표가 끊겨 메모리·디스크 경보가 "데이터 없음" 으로 울린다. StatsD UDP 는 버려지고 api 는 영향이 없다.
- StatsD 가 지표로 안 생김(에이전트 버전의 StatsD 결함 보고가 있다): 설치 직후 serve 에서 `marketlens.ws_clients:0|g` 를 UDP 로 보내 3분 안에 지표가 생기는지 본다. 안 생기면 에이전트 버전과 함께 게이지 지표만 빚으로 남기고 나머지는 진행한다. api 쪽 코드는 그대로 둔다.
- 배포 중: collect 재빌드 동안 `/api/health` 가 503 — canary 가 한 번 실패할 수 있으나 경보는 안 울린다. uptime 서비스는 다운·복구 한 쌍을 보낸다(025 런북대로 정상). serve 재배포의 WS 재접속 502 는 5xx 에 세지 않는다.
- Caddyfile 이 깨짐: 배포의 다시 읽기가 실패해 배포 스크립트가 실패로 끝나고, 돌던 caddy 는 옛 설정으로 계속 돈다. 그러나 깨진 파일은 디스크에 남아 다음 caddy 재시작(박스 재부팅·상태검사 자동 재부팅·재생성)에서 사이트 전체가 내려간다 — 배포 실패를 보면 곧바로 되돌림 커밋을 머지한다. Caddyfile 을 고친 PR 은 본문 테스트 칸에 로컬 `caddy validate` 결과를 적는다.
- caddy 가 기동 때 로그 파일을 못 엶: 설정을 거부해 사이트 전체가 내려간다. `./logs/caddy` 가 없으면 compose 가 root 0755 로 만들고 caddy 도 root 라 보통은 생기지 않는다 — 배포 뒤 `docker logs marketlens-caddy` 로 본다. 운영 중 쓰기 실패(디스크 가득)는 요청 처리를 막지 않고 기록만 빈다(디스크 경보가 먼저 울린다).
- `/api/landing` 은 022 재작업이 머지되기 전 운영에서 404 다 — 제외 목록에 있어도 해가 없다.
- 검색 입력이 늘면 §3.2 의 쿼리 키 목록도 같이 늘린다. 기록 탭 검색칸의 값은 FE 검증 없이 페이지 URL `sym` 에 실리고, 그 URL 로 새로 열거나 공유한 요청 줄에 남는다(빚). API `base` 는 014 가 422 로 막고 그 경로들은 기록하지 않는다. 입출금 레이더 검색은 URL 에 안 실린다.
- 로컬 통합 기동: 요청이 `http://` catch-all 로 들어와 접속 로그가 거의 쌓이지 않는다. serve 에는 지금 IPv6 가 없다 — /48 규칙은 대비용이다.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**
- nginx: server 블록에 접속 로그 끔 / 기존 분기 계약 그대로(새 location 없음).
- Caddyfile(`caddy/Caddyfile`): 조각 `access_log` 에 파일 출력(경로·0644·50MiB·5개), 요청·응답 헤더 삭제, `ua`·`referer` 덧붙임, IP 두 필드 24·48, 쿼리 세 키 삭제, 기록 제외 경로 다섯(공개 허용 목록에서 WS 를 뺀 것)과 canary UA / 도메인 블록에만 import, `http://` 블록엔 없음 / 기본 로거에 같은 지우기 규칙 / `Referrer-Policy strict-origin` / 023 계약(도메인 두 개·`reverse_proxy web:80` 둘·`protocols h1 h2`) 그대로.
- compose: caddy 에 `./caddy:/etc/caddy:ro`·`./logs/caddy:/var/log/caddy`·`caddy-data:/data` / api 에 `STATSD_ADDR`·호스트 게이트웨이 / `.gitignore` 에 `logs/` / 기존 계약(컨테이너 일곱(030)·로그 상한·볼륨 4개) 그대로.
- 배포 워크플로 serve: `up -d --build` 뒤 `docker exec marketlens-caddy caddy reload --config /etc/caddy/Caddyfile`, `docker image prune -f` 가 마지막, `--profile` 은 serve·tunnel 두 줄(030).
- `ops/cloudwatch/`: 네 파일 모두 JSON 으로 읽힌다 / 지표 파일 셋은 전역 추가 차원 `InstanceId` 하나·호스트명 없음·디스크 `/` 만·장치 차원 없음·`run_as_user` 없음, collect 는 주기 300·지표 둘, StatsD 는 serve 에만 / `serve-logs.json` 은 최상위 키가 `logs` 하나, 파일 경로 = `/home/ubuntu/marketlens/` + compose 로그 바인드의 호스트 쪽 + `/access.log`, 보존 90, 클래스 STANDARD.
- 게이지: `STATSD_ADDR` 가 없으면 가짜 UDP 수신기에 아무것도 오지 않는다 / api 역할 앱에만 게이지 태스크가 있다(`tests/test_role.py` 방식) / 접속 0명이면 0, 연결 2개면 2 / `host:port` 가 아니면 WARNING 이고 앱은 뜬다 / 주기는 주입해 짧게.
- 로컬 Docker: `caddy validate` 통과 / Caddyfile 사본 끝에 조각을 import 하는 `http://:8099` 블록(200 응답)을 붙여 띄우고 `/app/?s.q=x&tab=history`·`/?s.q=x`·canary UA·`/api/health`·`Referer: https://a.com?q=x`·`Referer: android-app://x/y` 요청 → 줄마다 IP 끝 `.0`, `s.q` 없음, 헤더는 `ua`·`referer` 뿐, `referer` 는 `https://a.com`·빈 값, canary·헬스 줄 없음 / upstream 이 없는 경로로 오류 줄을 내 docker 로그의 오류 줄도 IP·쿼리가 지워졌는지.
- 로컬 통합 기동(:8080): `CANARY_BASE_URL=http://localhost:8080` 으로 canary 4단계 통과 / `stop api` 동안 canary 가 3단계에서 실패하고(502 또는 504 — 016 실측) `/api/health` 는 200 → `start api` 뒤 통과. 거래소가 막혀 3·4단계가 안 되면 1·2단계만 통과시키고 3·4단계는 배포 뒤 운영 canary 로 넘긴다(§7). 운영 박스에서 통합 기동을 하지 않는다 — 프로젝트 이름·컨테이너 이름·포트가 운영과 같다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`, 023 §4 의 로컬 caddy 200·308(새 경로로).

**배포·런북 뒤 — 사람(완료 조건 아님, status.md 비고에 "EC2 확인 대기" 로 남긴다)**
- serve 스왑 1GB 와 메모리 측정값(가용률 최저·스왑 사용·에이전트 RSS — 승격 판단 근거) / 지표 12개(로그 전송 뒤 13개)와 실제 차원 / `/app/` 탭 2개를 열면 3분 안에 `ws_clients` 최댓값이 2 늘고 닫으면 다음 구간에 준다 / 시험 경보는 EC2 동작이 없는 메모리·디스크 경보 하나로만 `set-alarm-state` → Slack ALARM·OK 한 줄씩(EC2 동작이 걸린 경보에는 쓰지 않는다 — 재부팅된다) / canary 직접 실행 로그의 1~4단계 통과·반환 `"ok"` / 에이전트 RSS 24시간 최댓값·collect 에이전트 자체 CPU / 첫 달 청구의 지표 수 / 로그 그룹 보존(canary 30, 로그 전송 뒤 caddy 90) / 처리방침 게시 뒤 로그 그룹에 지우기 규칙이 지켜진 줄.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 2026-09-29 로컬(Mac, OrbStack Docker, compose v5.1.2, caddy v2.11.4, node v26.4.0). 이 망은 거래소 도메인이 막혀 있다(수집기 로그 ConnectTimeout).
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m pytest -q
#   All checks passed! / 229 files already formatted / 865 passed (시작 전 839 — test_observability.py 14·test_gauge.py 12 추가)
cd web && npm run lint && npm run build        # oxlint 경고 0 / ✓ built
docker run --rm -v ./caddy:/etc/caddy:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile   # Valid configuration
# 시험 사본(도메인 블록이 Let's Encrypt 로 가지 않게 local_certs 만 더함) + 끝에 `http://:8099 { import access_log; handle /bad* { reverse_proxy 127.0.0.1:9 }; respond "ok" 200 }`
docker run -d --name marketlens027-caddytest -p 8099:8099 -p 8443:443 -v <사본>:/etc/caddy:ro -v <빈 폴더>:/var/log/caddy caddy:2-alpine
curl ":8099/app/?s.q=x&tab=history&utm_source=t"  → uri "/app/?tab=history&utm_source=t", remote_ip·client_ip "192.168.215.0"
curl ":8099/?s.q=x"                               → uri "/"
curl -A KimpTrack-Canary/1 …, /api/health·/api/health/collect·/api/landing·/api/history/events·/api/history/candles → 줄 없음
curl -H 'Referer: https://a.com?q=x' ":8099/app/?g.q=y&p.q=z" → uri "/app/", "referer":"https://a.com"
curl -H 'Referer: android-app://x/y' …             → "referer":""   (user:pw@ 가 든 주소도 "", http://news.site:8080/a?q= → "http://news.site:8080")
#   줄마다 키는 request{remote_ip,remote_port,client_ip,proto,method,host,uri}·bytes_read·user_id·duration·size·status·referer·ua 뿐(headers·resp_headers 없음), 파일 -rw-r--r--
curl ":8099/bad?s.q=secret&tab=x" → 502. docker logs 의 http.log.error 줄: uri "/bad?tab=x", IP "192.168.215.0", headers 없음. docker logs·access.log 에 "secret" 0건, 끝이 .0 이 아닌 IP 0건
curl -k --resolve kimptrack.com:8443:127.0.0.1 https://kimptrack.com:8443/app/?s.q=secret  → referrer-policy: strict-origin, access.log(log0) uri "/app/"(s.q 를 지우면 쿼리가 빈다)
docker rm -f marketlens027-caddytest
# canary 스크립트 — 가짜 서버(받은 UA 를 찍는다) 네 경우
CANARY_BASE_URL=http://127.0.0.1:8127 node ops/canary/index.mjs
#   ok → 1~4단계 통과, exit 0 / /api/health 503 → "2단계 실패: … → 503 {"status":"stale"}" exit 1
#   heartbeat 만 → "4단계 실패: snapshot 뒤 5초 안에 delta 가 없다(heartbeat 만 — 수집 정체)" / 빈 snapshot → "4단계 실패: snapshot 의 rows 가 비었다"
#   가짜 서버가 받은 UA: /, /api/health, /api/history/candles, WS 업그레이드 모두 "KimpTrack-Canary/1"
# 로컬 통합 기동 — 사용자 것(marketlens_* 볼륨·marketlens-* 이미지)을 안 건드리게 덮어쓰기 파일(스크래치)로: 프로젝트 marketlens027,
#   컨테이너·이미지 이름 marketlens027-*, env_file 은 server/.env.example + 셸에서 만든 시험 INFLUX_TOKEN, 호스트 포트는 caddy 8080·8443 뿐,
#   caddy 는 Caddyfile 사본(local_certs 만 더함) 디렉터리 바인드. 호스트 UDP 8125 에 받는 스크립트.
COMPOSE_PROFILES=collect,data,serve docker compose -f docker-compose.yml -f compose.027.yml up -d --build   # 6컨테이너 Up
docker exec marketlens027-caddy caddy reload --config /etc/caddy/Caddyfile     # 새로 만든 caddy 에 up 직후 곧바로 — exit 0
curl localhost:8080/ → 200 / curl -H 'Host: kimptrack.com' localhost:8080/ → 308 https://kimptrack.com/ (023)
curl localhost:8080/api/health → 200 {"status":"ok",…} / /api/docs → 404 JSON (028)
# 호스트 UDP 8125: "marketlens.ws_clients:0|g" 10초마다, WS 1개를 연 동안 ":1|g" (api → host.docker.internal → 호스트)
node ws-probe.mjs ws://localhost:8080/api/ws/spreads   # caddy→nginx→api: waiting → heartbeat… (거래소가 막혀 표 없음)
CANARY_BASE_URL=http://localhost:8080 node ops/canary/index.mjs
#   1단계 통과 / 2단계 통과 / 3단계 실패: …/api/history/candles?… → 503 storage_unavailable (Influx 404 — 봉 버킷 없음·봉 없음) — 3·4단계는 배포 뒤 운영 canary 로
docker compose … stop api && node ops/canary/index.mjs
#   1·2단계 통과 / 3단계 실패: … 요청 오류 — TimeoutError (nginx 가 멈춘 api 에 연결을 기다림 — 60초 뒤면 504), /api/health 는 200
docker compose … start api && node ops/canary/index.mjs   # 멈추기 전과 같은 상태(1·2 통과, 3단계 503)로 복구
curl -k --resolve kimptrack.com:8443:127.0.0.1 "https://kimptrack.com:8443/?s.q=btc&utm_source=x"
#   301 location /app/?s.q=btc&utm_source=x · referrer-policy: strict-origin / ./logs/caddy/access.log 줄: uri "/?utm_source=x", IP .0, 헤더 없음
#   /app/ + Referer https://t.co/abc?q=1 → "referer":"https://t.co" / /api/health·canary UA → 줄 없음
docker logs marketlens027-web | grep '"GET '   # 접속 줄 0 — 오류 줄만(client 는 caddy 컨테이너 IP)
# Caddyfile 을 git 처럼 새 파일로 바꿔 쓰기(새 inode) → reload 전엔 옛 설정, `caddy reload` exit 0 뒤 새 설정(Referrer-Policy 값이 바뀜)
# 모르는 지시어가 든 Caddyfile → reload exit 1 "unrecognized directive", 사이트는 옛 설정으로 200 — §3.8
docker compose … down -v && docker rmi marketlens027-server marketlens027-api marketlens027-web && rm -rf logs   # 컨테이너·볼륨·망·이미지 0건 확인
# 검토 반영(같은 날·같은 Mac): ruff·format·pytest 868 passed(+3 test_gauge) / web lint·build / caddy validate Valid configuration
#   사본 caddy 에 https://kimptrack.com:18443/app/?s.q=secret → access.log uri "/app/" (위 :8443 줄의 결과를 이 관측값으로 고침)
#   canary 가짜 서버: 헤더 뒤 본문 정지 → 8초 뒤 "1단계 실패: … 본문 읽기 오류 — TimeoutError" / /api/health 본문 null → "2단계 실패: … JSON 객체가 아니다" / WS 프레임 null → "4단계 실패: 프레임이 JSON 객체가 아니다" / 정상 → 통과
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — 표의 wallet-history 행 뒤 빈 줄을 지워 slack-alerts 행을 표 안으로 넣고, 그 뒤에 `| observability | server: api WS 접속 수 StatsD 게이지(STATSD_ADDR) | - | caddy 접속 로그(IP /24·검색어·헤더 지움, 폴링 제외) → 박스 안, 처리방침 뒤 CloudWatch Logs 서울 90일 · 에이전트 세 박스 · canary 5분 4단계 · 경보 17개(로그 뒤 18) → Slack · EC2 확인 대기 |` 행. deploy 행: serve 에 스왑 1GB, nginx 접속 로그 끔, caddy 에 "`caddy/` 디렉터리 바인드·배포 뒤 reload·도메인 블록 접속 로그 → 호스트 `logs/caddy/`", 남은 사람 작업의 collect 역할 부착은 런북에서 확인했으면 지운다. 알려진 빚: (025) 줄의 첫 문장 "디스크·메모리 알람 없음(CloudWatch Agent 는 후속)." 을 지운다. 추가: `(027) 법정 보관 의무 확인 전 — 해당하면 원 IP 보관 방법을 따로 정한다`, `(027) 022 의 /?쿼리 → /app/ 301 때문에 utm_* 링크가 대시보드로 간다`, `(027) 기록 탭 검색칸 값(URL sym)은 검증 없이 기록된다`, `(027) 쿼리 키 삭제 목록은 검색 입력이 늘 때 손으로 맞춘다`, `(027) 탭·필터 조작은 서버가 못 본다 — 후속 브라우저 분석`, `(027) Caddyfile 은 CI 가 문자열로만 본다 — 깨진 설정은 다음 caddy 재시작에서 사이트를 내린다`, `(027) serve 는 t4g.micro + 스왑 — 메모리 측정 뒤 승격 판단`, `(027) 016·017·018·021·023·025 의 해당 문장이 027 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기`.
- `CLAUDE.md` — 스펙 인덱스 027 행 상태 → DONE. §2 레포 구조에 `caddy/`(Caddyfile)·`ops/`(`cloudwatch/`·`canary/` — 박스에 올리는 설정) 줄, runbooks 목록에 `uptime-monitor.md`·`cloudwatch.md`. §5 수정 가능 목록에 `ops/`·`caddy/`.
- `docs/context/architecture.md` — 16행 '런타임 구성' 의 api 태스크를 "017 구독 태스크 + `SLACK_WEBHOOK_URL` 이 있으면 025 알림 태스크 + `STATSD_ADDR` 가 있으면 027 게이지 태스크(+ 접속마다 보내기 태스크)" 로. 106행 "`/health` 는 프로세스 liveness 만 나타낸다" → "025 이후 마지막 틱이 30초 안에 있었는지를 답한다(200 ok / 503). 밖에서는 `/api/health`(collector)만 열고, api·Redis·Influx 는 canary 가 본다(027)". '배포 토폴로지' 절 152행 collect 줄의 "IAM 프로파일은 이 박스에만" → "`marketlens-s3-snapshot` 은 이 박스에만(세 박스 모두 에이전트 정책, data·serve 는 `marketlens-cwagent` — 027)", 154행 serve 줄에 "스왑 1GB(027)" 와 caddy 설명 "`caddy/Caddyfile` 디렉터리 바인드·배포 뒤 reload·도메인 블록 접속 로그 → 호스트 `logs/caddy/`". "현재 구조" 에 observability 항목(게이지 위치·`ops/` 두 폴더·계약 테스트). 현재 구조 deploy 줄의 "web 만 `${WEB_PORT:-80}:80`·named volume 2개" → 호스트 포트 다섯(caddy 80·443, 박스 간 8000·6379·8086)·볼륨 4개.
- `docs/context/dev-setup.md` — env 절 `ROLE` 설명의 "백그라운드 태스크는 017 구독 하나" → architecture.md 16행과 같은 문구, env 표에 `STATSD_ADDR`(compose 가 api 에만 준다, 비면 끔) 행. 'docker 통합 기동' 절에 "caddy 접속 로그는 `./logs/caddy/access.log`(git 무시, 로컬은 catch-all 이라 거의 비어 있다)" 한 문장, `caddy validate` 명령(`./caddy` 경로).
- `server/.env.example` — `# STATSD_ADDR=` 와 설명 1줄(027 — compose 가 api 에만 `host.docker.internal:8125` 를 준다, 로컬은 비워 둔다).
- `docs/runbooks/uptime-monitor.md` — "왜 `/health` 하나로 되는가" 절을 §3.1 대로 고친다 — `/api/health` 는 수집기가 답해서 api·Redis 사망은 못 잡고, 그건 canary(027)가 잡는다. 모니터 URL 은 그대로. 마지막의 CloudWatch 후속 후보 문장을 지운다.
- `docs/runbooks/cloudwatch.md` — 신규. §3.7 순서대로, 단계마다 확인·되돌리기, 관리자 절, 경보 목록(§3.6 값 그대로), serve 설정 두 단계 불러오기, serve 스왑·메모리 측정·승격 판단 기준, Logs Insights 저장 쿼리 3개(경로별 요청 수, 외부 `referer` 출처별 방문, WS 연결 지속 시간 분포). 300줄을 넘으면 절 단위로 커밋을 나눈다.
- `docs/runbooks/ec2-split.md` — 3단계(data 스왑) 옆에 serve 스왑 1GB 절차 한 줄. `ec2-setup.md` — IAM 절에 `marketlens-cwagent`·collect 정책 추가·IMDS 설정.
- `docs/specs/007-deploy.md` — §3 컨테이너 목록에 `caddy` 한 줄(serve profile, `./caddy:/etc/caddy:ro`·`./logs/caddy:/var/log/caddy`·`caddy-data`·`caddy-config`, 023·027), `api` 줄에 `STATSD_ADDR`·호스트 게이트웨이, `web` 줄에 "nginx 접속 로그는 끈다(기록은 caddy, 027)", 배포 절에 serve 의 caddy reload. §2 하지 않는 것의 "로그 수집·모니터링" 뒤에 "(027 이 CloudWatch 로 한다)". 021·022 뒤 사실과 달라진 문장도 함께 — §3 `server` 줄 "호스트에 노출하지 않는다" → 호스트 8000 공개·보안그룹이 막는다(021), 배포 설정 계약의 "호스트 노출은 web 하나" → test_deploy 가 보는 포트 다섯, §4 "`/foo` 도 index.html" → 루트의 없는 경로 404·SPA fallback 은 `/app/` 아래(022).

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 016 — §3.1 "017 의 구독 태스크 하나뿐이다" → architecture.md 16행과 같은 문구. §3.5 마지막 bullet → "두 역할의 `/health` 는 025 §3.5 판정을 따른다. 밖에는 nginx 의 `/api/health`(`server`)만 열고, api 는 canary(027)가 밖에서 본다".
- 017 — §7 남은 빚의 "외부 헬스체크는 여전히 없다" → "밖에서는 canary 가 WebSocket 까지 본다(027)".
- 018 — §3.4 api 백그라운드 태스크 문장을 architecture.md 16행과 같은 문구로.
- 021 — §3.1 collect 문장의 "이 박스에만" → "S3 역할은 이 박스에만, 세 박스 모두 에이전트 정책(027)", serve 박스 설명에 "스왑 1GB(027)".
- 023 — §2 만드는 것의 "루트 `Caddyfile`" → "`caddy/Caddyfile`(027 이 옮김, 디렉터리 바인드)", §2 하지 않는 것의 "배포 워크플로 변경 없음(…)" → "serve 배포는 `up` 뒤 caddy 설정을 다시 읽는다(027)", §3.3 에 "도메인 블록은 접속 로그(027 §3.2)와 `Referrer-Policy: strict-origin`", §4 명령의 경로.
- 025 — §3.6 의 CloudWatch 후속 후보 문장 삭제(모니터 URL 은 그대로). §2 하지 않는 것의 괄호 "(CloudWatch Agent — 런북에 후속으로만 적는다)" → "(027 — CloudWatch Agent)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `caddy/Caddyfile`(루트에서 옮김 — 조각 `access_log`·기본 로거 지우기·도메인 블록 `import`·`Referrer-Policy`), `docker-compose.yml`(caddy 바인드 둘, api `STATSD_ADDR`·`extra_hosts`), `.gitignore`(`logs/`), `.github/workflows/deploy.yml`(serve `caddy reload`), `web/nginx.conf`(`access_log off`), `server/app/features/spreads/gauge.py`(`WsClientsGauge`·`start_ws_gauge`), `server/app/core/config.py`(`statsd_addr`), `server/app/main.py`(`_api_lifespan` 만 게이지), `server/.env.example`, `ops/cloudwatch/{collect,data,serve,serve-logs}.json`, `ops/canary/index.mjs`, 테스트 `server/tests/test_observability.py`(14)·`server/app/features/spreads/tests/test_gauge.py`(12)·`server/tests/test_deploy.py`(caddy 경로), 런북 `docs/runbooks/cloudwatch.md`. 문서: CLAUDE.md(§2·§4·§5), architecture·dev-setup·status, 007 §2·§3, 런북 uptime-monitor·ec2-split·ec2-setup. 라이브러리 추가 없음.
- 028 호출 경로 대조: `ops/canary/` 가 생기자 028 의 `_canary_paths()` 가 canary 의 실제 경로 셋 `/api/health`·`/api/history/candles`·`/api/ws/spreads` 를 그대로 뽑았다(빈 집합 아님, 전부 허용 목록 안). `test_canary_paths_are_its_three_allowlisted_calls` 가 이 셋을 못박아 추출 규칙이 깨지면 빈 집합으로 조용히 통과하지 않는다. 028 의 테스트·nginx 공개 server 계약은 고치지 않았다(`access_log off` 는 server 수준 지시어라 location 대조에 안 걸린다).
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - `referer` 모양 — 스킴은 대소문자 무관 `http(s)`, 호스트는 영숫자·`.`·`-` 또는 `[IPv6]`, 포트 선택. 사용자 정보(`user:pw@host`)가 든 주소는 빈 값. caddy `map` 정규식(RE2)으로 만든다.
  - 기본 로거의 지우기 규칙은 접속 로그 조각의 `format` 과 똑같은 다섯 줄(`resp_headers` 삭제 포함) — 테스트가 둘의 일치를 본다. `Referrer-Policy` 는 조각이 아니라 도메인 블록에 둔다(조각은 로그 설정만).
  - 회전 파일 보관 `roll_keep_for 90d` 를 적어 둔다(caddy 기본과 같은 값). canary UA 제외는 `header User-Agent *KimpTrack-Canary*`(부분 일치).
  - 게이지 — 이름은 한 번 풀리면 계속 쓰고 실패했을 때만 다음 회차에 다시 푼다. 풀기·전송 실패 WARNING 은 둘이 한 억제(10분)를 나눠 쓴다. `[::1]:8125` 꼴을 받고 포트는 1~65535. 태스크 이름 `ws_clients_gauge`, 로거 `marketlens.ws_gauge`.
  - 에이전트 — "호스트명 차원 없음" 은 `omit_hostname: true`, api RSS 는 procstat `pattern`(명령줄), 나머지 셋은 `exe`. StatsD 는 수집 10초(보내는 주기와 같게)·집계 60초. 에이전트 설정은 이 Mac 에서 에이전트 변환기로 돌려 보지 못했다 — 사람이 `fetch-config` 할 때 검사된다.
  - canary — 파일 `ops/canary/index.mjs`(ESM, handler `index.handler`), 실패 메시지 `N단계 실패: …`, snapshot 전의 `waiting`·`heartbeat` 는 기다리고 delta 는 snapshot 뒤에만 센다, 15초는 연결 시작부터, fetch 는 리다이렉트를 따라간다. 로컬 실행은 같은 파일을 `node` 로(Lambda 는 handler 만 부른다).
  - 테스트 위치 — 027 계약은 `server/tests/test_observability.py`(test_deploy 의 헬퍼·`PUBLIC_API` 재사용, Caddyfile 은 작은 줄 파서). "collector 는 게이지를 안 띄운다" 는 거래소 커넥터·우주·틱 루프의 `start` 만 무동작으로 바꾸고 collector lifespan 을 그대로 돌려 본다.
  - 로컬 통합 기동은 dev-setup 명령 그대로가 아니라 스크래치 덮어쓰기 파일로 했다 — 이 Mac 에 사용자의 `marketlens_*` 볼륨·`marketlens-*` 이미지가 있고 `server/.env` 는 읽지도 만들지도 않으므로(프로젝트 `marketlens027`, `.env.example` + 시험 토큰). caddy 는 Caddyfile 사본에 `local_certs` 만 더했다 — 원본 그대로면 도메인 블록이 Let's Encrypt 로 인증서를 청해 운영 도메인 검증 요청이 나간다. 그래서 023 §4 의 로컬 200·308 도 이 사본으로 봤다.
- 실행 중 함께 고친 스펙 절: 027 §4 — "기록 제외 경로 여섯" → "다섯(공개 허용 목록에서 WS 를 뺀 것)"(§3.2 가 경로 다섯을 이름으로 적고 있어 그쪽을 따랐다). 007 §2·§3 은 §6 목록대로.
- 검토 반영: 게이지 이름 풀기가 `ValueError`(빈 라벨 `a..b` 의 UnicodeError)도 풀기 실패로 받아 WARNING·다음 회차로 가고, `aclose` 는 죽어 있던 태스크의 예외를 WARNING 으로 남기고 던지지 않는다(lifespan 의 허브·버스·Influx 정리가 돈다) — 10분 뒤 WARNING 재출력 테스트로 `clock` 주입을 쓴다. canary 는 본문 읽기(8초 제한 안)와 JSON 이 객체가 아닌 응답·프레임도 `N단계 실패:` 로 던진다. 007 §2·§3·architecture·dev-setup 의 컨테이너 수를 caddy 를 넣은 여섯으로, Caddyfile 주석의 테스트 파일 이름, §5 의 `/app/?s.q=secret` 결과를 실제 관측값으로. 사람 검토에서: 007 §3·§4 와 architecture 현재 구조 deploy 줄의 호스트 포트·볼륨·SPA fallback 문장을 021·022 뒤 사실대로(§6 에 더함).
- PR 본문에 옮길 것 — 담당자에게 제안(이 PR 은 고치지 않는다, §6 그대로):
  - 016 — §3.1 "017 의 구독 태스크 하나뿐이다" → architecture.md 16행과 같은 문구. §3.5 마지막 bullet → "두 역할의 `/health` 는 025 §3.5 판정을 따른다. 밖에는 nginx 의 `/api/health`(`server`)만 열고, api 는 canary(027)가 밖에서 본다".
  - 017 — §7 남은 빚의 "외부 헬스체크는 여전히 없다" → "밖에서는 canary 가 WebSocket 까지 본다(027)".
  - 018 — §3.4 api 백그라운드 태스크 문장을 architecture.md 16행과 같은 문구로.
  - 021 — §3.1 collect 문장의 "이 박스에만" → "S3 역할은 이 박스에만, 세 박스 모두 에이전트 정책(027)", serve 박스 설명에 "스왑 1GB(027)".
  - 023 — §2 만드는 것의 "루트 `Caddyfile`" → "`caddy/Caddyfile`(027 이 옮김, 디렉터리 바인드)", §2 하지 않는 것의 "배포 워크플로 변경 없음(…)" → "serve 배포는 `up` 뒤 caddy 설정을 다시 읽는다(027)", §3.3 에 "도메인 블록은 접속 로그(027 §3.2)와 `Referrer-Policy: strict-origin`", §4 명령의 경로(`./Caddyfile` → `./caddy`).
  - 025 — §3.6 의 CloudWatch 후속 후보 문장 삭제(모니터 URL 은 그대로). §2 하지 않는 것의 괄호 "(CloudWatch Agent — 런북에 후속으로만 적는다)" → "(027 — CloudWatch Agent)".
- 실행 중 발견한 어긋남(파일:절 — 주장 → 실제, 고치지 않음):
  - `docs/context/dev-setup.md:docker 통합 기동` — "`stop api` 뒤 `/api/history/candles`·`/api/landing` 502" → 이번 로컬에서는 nginx 가 멈춘 api 로의 연결을 기다려 8초 안에 답이 없었다(기본 연결 타임아웃 60초 뒤 504). 027 §4 의 "502 또는 504" 와는 맞다.
  - 014(관찰) — 새로 띄운 로컬 스택에서 수집기의 봉 버킷 생성(기동 시 1회·3초 상한)이 Influx 첫 setup 보다 먼저 끝나 `candles_1m` 이 없었다 → `/history/candles` 503(Influx 404). 운영의 Influx 는 이미 떠 있어 해당 없다.
- 배포 뒤 운영 확인(2026-09-29~30, 사람 — 런북대로):
  - canary: CloudShell 에서 `aws synthetics describe-runtime-versions` 가 AccessDenied — 조직 SCP 의 명시적 거부라 계정 관리자도 못 푼다(09-30). 그래서 같은 스크립트를 Lambda(`nodejs22.x`, arm64, 128MB, 60초) + EventBridge Scheduler(5분)로 돌린다(§3.5). 직접 실행 2회(09-30 07:05Z): 1~4단계 1886·100·259·586ms / 558·437·421·634ms, 반환 `"ok"`. 로컬 `node:22-alpine`(v22.23.3)에서 handler 를 import 해 운영 주소로 돌려도 4단계 통과. 일정·로그 그룹 보존 30일·경보 `marketlens-canary`(§3.6)까지 만들었다.
  - 예산 월 $130 — 알림 실제 85%·실제 100%·예측 100%(콘솔 템플릿이 85% 를 더했다). Slack: SNS `marketlens-alerts`(서울) + Q Developer 채널 구성(정책 템플릿 Notification permissions 하나, 가드레일 ReadOnly 계열). 콘솔 편집에서 SNS 리전 기본값이 us-east-1 이라 서울을 골라야 주제가 보인다.
  - IMDS(data·serve 토큰 필수·hop 1)·역할(data·serve `marketlens-cwagent`, collect 역할에 에이전트 정책)·serve 스왑 1GB. collect 인스턴스에 `marketlens-s3-snapshot` 이 붙어 있다(09-30 collect 호스트의 인스턴스 메타데이터 `iam/info` 로 확인, 수집기 로그의 S3 업로드 실패 경고 2시간 0건). 잔고 경보 임계값은 기본값 173·86.
  - 에이전트: 세 박스 설치(collect `collect.json`·data `data.json`·serve `serve.json`). 09-30 `list-metrics` 에 collect 2·data 5·serve 5 가 보인다. collect·serve 에 처음 `data.json` 을 잘못 불러와 `swap_used_percent` 가 한 번씩 생겼다 — `fetch-config` 는 설정을 통째로 바꾸므로 맞는 파일로 다시 불러오면 되고, 새 값이 안 오는 지표는 2주 뒤 목록에서 사라진다(런북 5-2 에 박스 확인 한 줄). StatsD 게이지는 CloudWatch 에서 점이 밑줄로 바뀐 이름 `marketlens_ws_clients` 로 보인다(보내는 줄은 `marketlens.ws_clients:<n>|g` 그대로).
  - 경보 17개 전부 OK(09-30 `describe-alarms`). 경보를 만든 직후 canary 경보가 ALARM → OK 한 번 — 일정의 첫 실행 전이라 데이터 없음을 실패로 셌다(만들 때 한 번 생기는 오탐). 예산 알림도 SNS `marketlens-alerts` 에 이어 Slack 으로 온다(주제 정책에 `budgets.amazonaws.com` Publish 허용). 예산 화면의 비용 그래프는 Cost Explorer(`ce:GetCostAndUsage`)도 조직 SCP 가 막아 안 뜬다 — 예산 평가·알림은 별개로 동작한다.
  - collect CPU: 수집기 컨테이너 `docker stats` 10분 평균이 설치 전 81.6%, 설치 뒤 83.3%, 다시 재니 75.1% — 1분 값이 66~100% 로 흔들려 ±3%p 는 잡음이고, `docker stats` 는 호스트의 에이전트를 재지 않는다. 에이전트 자체 CPU(systemd `CPUUsageNSec` 10분 차이) 0.17% → 유지. 판정을 이 값으로 바꿨다(§3.4, 런북 6단계).
  - 에이전트 메모리(`MemoryPeak`): collect 24MB, serve 최대 71MB(24시간), 상한 200MB.
  - serve 메모리 24시간(09-29 05:02Z~09-30 04:57Z): 가용 최저 ≈378MB/904MB(≈41%, `sar -r`). 스왑 사용 238MB 지만 09-30 하루 `sar -W` 의 pswpin·pswpout 이 전부 0 — 09-29 스왑·에이전트 설치·설정 재적재 때 쓴 뒤 머문 것이다. 배포 1회(09-30 07:34Z, 029·030 — web 이미지 빌드): 04:57Z~07:39Z 사이 pswpout +33,047·pswpin +31,439 페이지(≈130MB 씩, `sar -W` 는 07:30 까지 0 이라 배포 몇 분 동안), OOM 0·컨테이너 재시작 0, 배포 뒤 가용 360MB·스왑 사용 187MB → 스왑은 배포 빌드 때만 쓴다. **t4g.micro + 스왑 1GB 유지, 승격하지 않는다.**
  - 비용(§3.7 표): canary 행 ≈0(Lambda·Scheduler·로그 무료 한도 안, Lambda 표준 지표 무료) → 합계 ≈$1.7/월(부가세 별도). 경보 수는 17(로그 뒤 18) 그대로.
- 남은 빚:
  - canary 3·4단계는 이 망의 로컬 스택에서 못 봤다(거래소 차단 — 봉·표가 없다). 대신 같은 스크립트를 이 Mac 에서 운영 주소로 돌려 네 단계 모두 통과했다(2026-09-29, 028 배포 뒤 — `canary 통과 — https://kimptrack.com`, 4단계 1032ms). Lambda(`nodejs22.x`)에서의 첫 실행은 위 운영 확인에서 통과. 1·2단계와, 3단계가 api 정지·저장소 오류를 실패로 잡는 것만 로컬 확인. 4단계 판정 로직은 가짜 서버로(통과·delta 없음·빈 snapshot).
  - canary 의 WebSocket UA(`headers` 옵션)는 가짜 서버로 로컬 Node v26.4.0·v22.23.3(`node:22-alpine`)에서 확인했다(2026-09-30 — 업그레이드 요청의 `User-Agent` 가 `KimpTrack-Canary/1`). Lambda 런타임의 Node 22 부 버전에서는 따로 보지 않았다 — 헤더가 안 붙으면 canary WS 한 줄이 5분마다 접속 로그에 남을 뿐이다.
  - 사람 대기: data 에이전트 RSS 24시간 최댓값(`MemoryPeak`) 기록, 기존 경보·지표 수(런북 2단계) 기록.
  - 이 PR 의 첫 serve 배포는 caddy 볼륨 정의가 바뀌어 caddy 를 새로 만든다 — 그 직후 `caddy reload` 가 admin 기동보다 먼저 닿으면 배포가 실패로 끝날 수 있다(로컬에선 up 직후 곧바로 불러도 성공했다). 배포가 실패하면 되돌리기 전에 `docker logs marketlens-caddy` 로 설정 오류(`unrecognized …`)인지 기동 경합인지 먼저 본다.
  - `caddy reload` 가 남기는 admin API 줄(`"logger":"admin.api"`, `remote_ip` 127.0.0.1 — 컨테이너 안 reload 명령)은 기본 로거 필터 밖이다 — 방문자 정보가 아니라 두었다.
  - actionlint 미설치 — `deploy.yml` 은 YAML 파싱과 테스트 단언으로 갈음.
  - §4 "배포·런북 뒤 — 사람" 의 나머지(기존 경보·지표 수(런북 2단계)·지표 12/13개와 실제 차원·`ws_clients` 탭 2개·시험 경보·첫 달 청구·처리방침 뒤 caddy 로그 그룹 보존·지우기 규칙) — status.md observability 비고.
