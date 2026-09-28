# 027 — observability

상태: TODO | 의존: 002 web-shell(URL 쿼리 키), 007 deploy(compose·nginx·배포 워크플로), 011 health(`/health/collect` 폴링), 013·014(기록 탭 60초 재조회·`/history/candles`), 016 process-split(api 역할·nginx 분기), 017 spreads-push(허브·프레임), 021 infra-split(박스·IAM), 022 landing(`/app/`·`/api/landing`), 023 domain-tls(caddy), 025 slack-alerts(`/health` 두 역할·Slack 채널)

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
  - 022 의 `/?쿼리` → `/app/` 301(이미 운영 중 — `utm_*` 링크가 대시보드로 간다)과 기록 탭 검색칸의 입력 검증 — status.md 빚으로 남긴다.
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
- 기록은 caddy 한 곳에서 한다. nginx 접속 로그는 끈다(오류 로그는 남긴다) — nginx 기본 형식의 마지막 칸이 caddy 가 넣은 원 IP(`X-Forwarded-For`)다.
- 로그 설정은 `caddy/Caddyfile` 안의 이름 있는 조각 `access_log` 하나에 두고 도메인 블록(`kimptrack.com`·`www`)만 불러온다. `http://` catch-all(탄력 IP 직접 접속·봇 스캔)은 기록하지 않는다.
- 파일: 컨테이너 `/var/log/caddy/access.log`, 호스트 `./logs/caddy/`(레포 루트 기준 바인드, git 무시 — 배포의 `git reset --hard` 는 추적 안 하는 파일을 지우지 않는다). 권한 0644(사람이 호스트에서 읽게). 현재 파일 50MiB 에서 회전, gzip 된 회전 파일 5개까지, 회전 파일은 90일 뒤 지운다(caddy 기본).
- 한 줄에 남기는 것은 허용 목록이다. caddy 기본 JSON 에서
  - 요청 헤더·응답 헤더를 통째로 지운다. Accept-Language·Sec-Ch-Ua·임의 헤더(IP 가 든 것 포함)가 합쳐지면 방문자를 가려낼 수 있고, 응답 `Location` 에는 022 의 301 이 쿼리(검색어 포함)를 그대로 싣는다.
  - 대신 `ua`(`User-Agent` 그대로)와 `referer` 두 필드를 붙인다. `referer` 는 http(s) 스킴+호스트(+포트)만 남기고, 그 모양이 아니면 빈 값이다.
  - `request.remote_ip`·`request.client_ip` 는 IPv4 /24, IPv6 /48 로 자른다. 지금 두 필드는 같은 값이라 하나만 자르면 원본이 남는다.
  - `request.uri` 의 쿼리 키 `s.q`·`g.q`·`p.q`(스프레드·갭·선선갭 검색어)를 지운다. 나머지 키는 남긴다 — 어떤 주소로 들어왔는지를 보기 위해서다.
- caddy 기본 로거(오류 줄 — 예: 업스트림 502)에도 IP 자르기·쿼리 세 키·헤더 지우기를 똑같이 건다. docker 로그에 원 IP 가 남지 않게.
- 기록하지 않는 요청: 경로 `/api/health`·`/api/health/collect`(수집 상태 탭 5초 폴링)·`/api/landing`(랜딩 10초 폴링)·`/api/history/events`·`/api/history/candles`(탭이 전부 마운트돼 기록 탭을 안 열어도 60초마다 부른다), `User-Agent` 에 `KimpTrack-Canary` 가 든 요청(§3.5). 폴링·감시가 방문 기록을 덮기 때문이다. 이 경로들의 5xx 는 5xx 경보에 안 잡힌다. canary 2~4단계가 같은 백엔드(수집기·api·Influx)를 보지만 `/api/health/collect`·`/api/history/events`·`/api/landing` 자체의 5xx 는 감시하지 않는다.
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
- collect(주기 300초): 메모리 가용률·디스크 사용률 둘만. 1 vCPU 를 수집기가 80~100% 쓰므로 가볍게 둔다. 설치 전후 수집기 CPU 를 재서 1%p 넘게 늘면 에이전트를 지운다(§7).
- data(60초): 메모리 가용률, 스왑 사용률(차트용), 디스크 사용률, 프로세스 RSS `influxd`·`redis-server`(실행 파일 이름으로).
- serve(60초): 메모리 가용률, 디스크 사용률, 프로세스 RSS `caddy`(실행 파일)·api(명령줄 `uvicorn app.main:app` — 앱 모듈까지 못박는다), StatsD 수신 `:8125`(60초 집계)로 받은 `marketlens.ws_clients`.
- 로그(`serve-logs.json`): `/home/ubuntu/marketlens/logs/caddy/access.log` → 로그 그룹 `/marketlens/serve/caddy`, 스트림 이름 = 인스턴스 ID, 클래스 STANDARD(IA 는 지표 필터가 안 되고 만든 뒤 못 바꾼다), 보존 90일. **처리방침 게시 전에는 적용하지 않는다** — 그때까지 기록은 박스 안에만 있다.
  - 보존 90일: 분기 단위 비교면 충분하다. 통신비밀보호법 시행령의 3개월 보관 의무는 원 IP 를 요구해 이 기록으로는 채울 수 없다 — 우리에게 해당하는지는 사람이 법률 확인한다(status.md 빚).
- 지표 필터 1개(로그 전송을 켤 때 함께 만든다): `status ≥ 500` 이고 `request.uri` 가 `/api/ws/spreads` 가 아닌 줄 수 → `MarketLens/http_5xx`. WS 를 빼는 이유: serve 배포 때 열린 대시보드가 전부 재접속하며 502 를 낸다. 그 전까지 5xx 는 canary·uptime 으로만 보인다.
- 에이전트는 파일 위치를 기억한다 — caddy 재생성·회전 뒤에도 중복·누락 없이 이어 보낸다.

### 3.5 canary — 5분마다 밖에서 도는 점검
- CloudWatch Synthetics, 서울, 이름 `marketlens-smoke`, 5분마다, 실행 제한 90초, 브라우저 없는 Node.js 런타임(`syn-nodejs-*` 계열, Node 22) 중 만들 때 최신(이름은 §7). 결과 버킷은 Synthetics 기본 버킷(원문 버킷과 따로, 수명주기 30일), canary 의 Lambda 로그 그룹 보존 30일.
- 스크립트는 `ops/canary/` 에 두고 만들고 올리는 것은 사람이 한다. Synthetics 모듈은 부르지 않는다 — handler 하나가 표준 `fetch`·`WebSocket` 으로 네 단계를 돌고 실패하면 단계 번호가 든 메시지로 던진다. 그래서 로컬 Node 22 로도 돈다. 실행 단위 지표(`SuccessPercent`·`Duration`·실패·HTTP 상태 수)는 런타임이 늘 올리고 끌 수 없다. 주소는 env `CANARY_BASE_URL`(기본 `https://kimptrack.com`)이고 WebSocket 주소도 여기서 스킴만 바꿔 만든다. 1~3단계는 요청마다 제한 8초, 재시도 없음. 모든 요청의 `User-Agent` 는 `KimpTrack-Canary/1` 이다 — WebSocket 은 표준 API 에 헤더 인자가 없어 Node 내장 WebSocket 의 헤더 옵션으로 붙인다.
- 한 번 실행에 네 단계를 순서대로 돈다. 하나라도 실패하면 그 실행은 실패다.
  1. `GET /` — 200, 본문에 `KimpTrack`.
  2. `GET /api/health` — 200, `status == "ok"`(수집기 틱 30초 이내).
  3. `GET /api/history/candles?base=BTC&res=1m&start=<지금 epoch 초 − 900>&end=<지금 epoch 초>` — 200, `count ≥ 1`. 읽는 계약(014 복사): `start`·`end` 는 epoch 초(0~4,102,444,800, 밖이면 422), 생략한 쿼리는 `dom=upbit`·`fx=binance`·`dir=kimp`, 응답은 `{base,res,dom,fx,dir,startTs,endTs,count,fetchedAt,candles[]}`, 1m 창 상한 86,400초, 진행 중 창은 싣지 않는다, Influx 불달 503. 봉은 분이 닫힌 뒤 쓰이므로 15분 동안 0개면 쓰기가 멈춘 것이다. 이 요청은 nginx 를 거쳐 api 로 가므로 api 생존·api→Influx 읽기도 함께 본다.
  4. `/api/ws/spreads` — 15초 안에 `snapshot`(`rows` 길이 ≥ 1), 이어서 5초 안에 `delta` 를 받으면 끊는다. 읽는 계약(017 복사): 프레임은 바이너리 1개 = gzip 으로 압축한 JSON 1개, `type` 은 `snapshot`·`delta`·`heartbeat`·`waiting`. 접속 직후 표가 있으면 `snapshot`, 없으면 `waiting` 뒤 첫 표에 `snapshot`. 수집은 접속자와 무관하게 매 틱 표를 게시하고, 멈추면 `heartbeat` 만 온다 — `delta` 가 푸시 경로(수집기 → Redis → api 허브) 전체의 증거다.
- 5분보다 자주 돌리지 않는다 — 1분이면 월 ≈$82 다.

### 3.6 경보 → Slack
- 모든 경보는 서울 SNS 주제 `marketlens-alerts` 하나로 보내고, Amazon Q Developer in chat applications(구 AWS Chatbot)가 025 와 같은 Slack 채널에 올린다. 풀릴 때(OK)도 보낸다.
- 상태검사(세 박스, 최댓값): `StatusCheckFailed_Instance` 60초 주기 3점 연속 → 재부팅, `StatusCheckFailed_System` 60초 2점 연속 → 복구(recover). 기간을 다르게 두는 것은 AWS 권장이다 — 같으면 두 동작이 경쟁한다. 데이터 없음은 무시(missing).
- 크레딧(data t4g.small — 최대 적립 576, serve t4g.micro — 최대 288): `CPUCreditBalance` 최솟값 300초 주기 3점 연속 최대치의 30%(data 173·serve 86) 미만, `CPUSurplusCreditsCharged` 합계 300초 1점 0 초과(unlimited 잉여 과금 시작). 데이터 없음은 무시. 잔고 경보는 최근 7일 최솟값을 보고 그보다 낮게 둔다(기본 30%).
- 메모리(세 박스, 최솟값): 메모리 가용률 10% 미만이 5분 연속(60초 5점, collect 는 300초 1점). data 근거: 021 설계 상한 1.3GiB + OS·docker·에이전트 ≈0.4GiB = 1.7GiB 에서 가용 ≈8% — 10% 미만이면 설계 상한에 가까워진 것이다. 데이터 없음은 경보(에이전트가 죽은 것을 알아야 한다).
- 디스크(세 박스): 사용률 최댓값 300초 1점 80% 초과. 데이터 없음은 경보.
- 요청: `http_5xx` 합계 300초 1점 10 이상. 데이터 없음은 정상(요청이 없으면 줄도 없다). 로그 전송을 켤 때 만든다.
- canary: `SuccessPercent` 평균 600초 1점 50 미만 — 10분 안의 실행이 모두 실패. 배포 중 1회 실패는 50 이라 울리지 않는다. 데이터 없음은 경보. canary 가 첫 실행을 끝낸 뒤 만든다.
- 스왑 경보는 두지 않는다 — 한 번 찬 스왑은 압박이 끝나도 잘 안 줄어 경보가 풀리지 않는다.
- 경보는 처리방침 전 17개, 로그 전송 뒤 18개다(상태 6·크레딧 4·메모리 3·디스크 3·canary 1, + 5xx 1). 예산: AWS Budgets 월 알림(실제·예측 $130, 이메일)을 사람이 건다.

### 3.7 박스·IAM·비용 (사람 — 런북)
- 순서: 예산 → 기존 경보·지표 수 확인 → IMDS → 역할 → data 에이전트(RSS 24시간) → collect 에이전트(CPU 전후) → serve 스왑 1GB → serve 에이전트 → Slack 연결 → 경보(canary·5xx·잔고 제외) → canary → canary 경보 → 잔고 경보(최근 7일 최솟값 확인 뒤) → serve 메모리 측정(24시간·배포 1회) → (처리방침 게시 뒤) serve 로그 전송·지표 필터·5xx 경보. 단계마다 확인·되돌리기.
- serve(t4g.micro 1GB)는 올리지 않고 **스왑 1GB** 를 붙인다(data 와 같은 방식, 무료). 스왑 없는 1GB 박스가 배포마다 web 이미지를 직접 빌드하는데(npm ci·vite build) 에이전트가 더해지기 때문이다. 에이전트를 띄운 뒤 24시간과 배포 1회 동안 메모리 가용률 최저·스왑 사용량·에이전트 RSS 를 재서 §7 에 적는다. 가용률이 10% 밑으로 내려가거나 스왑을 계속 쓰면 t4g.small 승격(월 +$7.6, 정지 몇 분)을 사람이 정한다 — 그때 이 스펙을 고치고 021 담당자에게 알린다.
- **역할을 붙이기 전에** data·serve 의 인스턴스 메타데이터를 토큰 필수(IMDSv2)·hop limit 1 로 둔다 — 도커 브리지 안의 컨테이너가 인스턴스 역할 자격증명에 닿지 못하게. data·serve 에 역할 `marketlens-cwagent`(관리형 정책 `CloudWatchAgentServerPolicy`)를 붙인다. collect 는 컨테이너가 S3 에 올리므로 hop 2 그대로(010)이고, 기존 역할 `marketlens-s3-snapshot` 이 붙어 있는지 먼저 확인한 뒤(status.md 남은 작업 — 없으면 붙인다) 같은 정책을 더한다. 컨테이너도 지표·로그 쓰기 권한에 닿지만 받아들인다.
- 콘솔 관리자가 할 일(CLI 사용자는 `iam:PassRole` 이 없다): collect 역할 부착 확인·세 박스 역할·정책, canary 생성(실행 역할 포함), Q Developer Slack 채널 구성(채널 역할, Slack 워크스페이스 승인), EC2 동작 경보용 서비스 연결 역할 1회. 런북은 이 넷을 한 절에 묶는다.
- 월 비용(달러, 로그 전송 뒤 기준, 부가세 10% 별도):

| 항목 | 월 |
|---|---|
| canary | 16.2 |
| canary지표 | 2.1 |
| 에이전트지표 | 0.9 |
| 경보 | 0.8 |
| 로그 | 0 |
| 합계 | 20.0 |

  canary 는 (8,640 − 무료 100)회 × $0.0019. canary 지표는 실행 단위 지표 ≈7개 × $0.30 로 잡은 최댓값이다 — 과금되는지와 실제 개수는 첫 달 청구로 확인한다(§7). 에이전트 지표는 13개(collect 2·data 5·serve 5·5xx 1) 중 무료 10개를 넘는 3개 × $0.30, 경보는 18개 중 무료 10개를 넘는 8개 × $0.10. 로그는 서울 STANDARD 수집 $0.76/GB·저장 $0.0314/GB-월·Logs Insights $0.0076/GB 이고 각각 월 5GB 무료 — 폴링을 뺀 뒤 수 GB 안쪽으로 추정한다(실측 뒤 §7). 무료 한도는 계정 전체(모든 리전) 기준이다. canary 의 Lambda 는 무료 한도 안, S3 는 월 $0.1 미만, SNS·Q Developer 는 무료.

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
- Caddyfile(`caddy/Caddyfile`): 조각 `access_log` 에 파일 출력(경로·0644·50MiB·5개), 요청·응답 헤더 삭제, `ua`·`referer` 덧붙임, IP 두 필드 24·48, 쿼리 세 키 삭제, 기록 제외 경로 여섯과 canary UA / 도메인 블록에만 import, `http://` 블록엔 없음 / 기본 로거에 같은 지우기 규칙 / `Referrer-Policy strict-origin` / 023 계약(도메인 두 개·`reverse_proxy web:80` 둘·`protocols h1 h2`) 그대로.
- compose: caddy 에 `./caddy:/etc/caddy:ro`·`./logs/caddy:/var/log/caddy`·`caddy-data:/data` / api 에 `STATSD_ADDR`·호스트 게이트웨이 / `.gitignore` 에 `logs/` / 기존 계약(컨테이너 6개·로그 상한·볼륨 4개) 그대로.
- 배포 워크플로 serve: `up -d --build` 뒤 `docker exec marketlens-caddy caddy reload --config /etc/caddy/Caddyfile`, `docker image prune -f` 가 마지막, `--profile` 은 한 줄 그대로.
- `ops/cloudwatch/`: 네 파일 모두 JSON 으로 읽힌다 / 지표 파일 셋은 전역 추가 차원 `InstanceId` 하나·호스트명 없음·디스크 `/` 만·장치 차원 없음·`run_as_user` 없음, collect 는 주기 300·지표 둘, StatsD 는 serve 에만 / `serve-logs.json` 은 최상위 키가 `logs` 하나, 파일 경로 = `/home/ubuntu/marketlens/` + compose 로그 바인드의 호스트 쪽 + `/access.log`, 보존 90, 클래스 STANDARD.
- 게이지: `STATSD_ADDR` 가 없으면 가짜 UDP 수신기에 아무것도 오지 않는다 / api 역할 앱에만 게이지 태스크가 있다(`tests/test_role.py` 방식) / 접속 0명이면 0, 연결 2개면 2 / `host:port` 가 아니면 WARNING 이고 앱은 뜬다 / 주기는 주입해 짧게.
- 로컬 Docker: `caddy validate` 통과 / Caddyfile 사본 끝에 조각을 import 하는 `http://:8099` 블록(200 응답)을 붙여 띄우고 `/app/?s.q=x&tab=history`·`/?s.q=x`·canary UA·`/api/health`·`Referer: https://a.com?q=x`·`Referer: android-app://x/y` 요청 → 줄마다 IP 끝 `.0`, `s.q` 없음, 헤더는 `ua`·`referer` 뿐, `referer` 는 `https://a.com`·빈 값, canary·헬스 줄 없음 / upstream 이 없는 경로로 오류 줄을 내 docker 로그의 오류 줄도 IP·쿼리가 지워졌는지.
- 로컬 통합 기동(:8080): `CANARY_BASE_URL=http://localhost:8080` 으로 canary 4단계 통과 / `stop api` 동안 canary 가 3단계에서 실패하고(502 또는 504 — 016 실측) `/api/health` 는 200 → `start api` 뒤 통과. 거래소가 막혀 3·4단계가 안 되면 1·2단계만 통과시키고 3·4단계는 배포 뒤 운영 canary 로 넘긴다(§7). 운영 박스에서 통합 기동을 하지 않는다 — 프로젝트 이름·컨테이너 이름·포트가 운영과 같다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`, 023 §4 의 로컬 caddy 200·308(새 경로로).

**배포·런북 뒤 — 사람(완료 조건 아님, status.md 비고에 "EC2 확인 대기" 로 남긴다)**
- serve 스왑 1GB 와 메모리 측정값(가용률 최저·스왑 사용·에이전트 RSS — 승격 판단 근거) / 지표 12개(로그 전송 뒤 13개)와 실제 차원 / `/app/` 탭 2개를 열면 3분 안에 `ws_clients` 최댓값이 2 늘고 닫으면 다음 구간에 준다 / 시험 경보는 EC2 동작이 없는 메모리·디스크 경보 하나로만 `set-alarm-state` → Slack ALARM·OK 한 줄씩(EC2 동작이 걸린 경보에는 쓰지 않는다 — 재부팅된다) / canary 첫 실행 성공·실행 단위 지표 개수 / 에이전트 RSS 24시간 최댓값·collect CPU 전후 / 첫 달 청구의 지표 수 / 로그 그룹 보존(canary 30, 로그 전송 뒤 caddy 90) / 처리방침 게시 뒤 로그 그룹에 지우기 규칙이 지켜진 줄.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — 표의 wallet-history 행 뒤 빈 줄을 지워 slack-alerts 행을 표 안으로 넣고, 그 뒤에 `| observability | server: api WS 접속 수 StatsD 게이지(STATSD_ADDR) | - | caddy 접속 로그(IP /24·검색어·헤더 지움, 폴링 제외) → 박스 안, 처리방침 뒤 CloudWatch Logs 서울 90일 · 에이전트 세 박스 · canary 5분 4단계 · 경보 17개(로그 뒤 18) → Slack · EC2 확인 대기 |` 행. deploy 행: serve 에 스왑 1GB, nginx 접속 로그 끔, caddy 에 "`caddy/` 디렉터리 바인드·배포 뒤 reload·도메인 블록 접속 로그 → 호스트 `logs/caddy/`", 남은 사람 작업의 collect 역할 부착은 런북에서 확인했으면 지운다. 알려진 빚: (025) 줄의 첫 문장 "디스크·메모리 알람 없음(CloudWatch Agent 는 후속)." 을 지운다. 추가: `(027) 법정 보관 의무 확인 전 — 해당하면 원 IP 보관 방법을 따로 정한다`, `(027) 022 의 /?쿼리 → /app/ 301 때문에 utm_* 링크가 대시보드로 간다`, `(027) 기록 탭 검색칸 값(URL sym)은 검증 없이 기록된다`, `(027) 쿼리 키 삭제 목록은 검색 입력이 늘 때 손으로 맞춘다`, `(027) 탭·필터 조작은 서버가 못 본다 — 후속 브라우저 분석`, `(027) Caddyfile 은 CI 가 문자열로만 본다 — 깨진 설정은 다음 caddy 재시작에서 사이트를 내린다`, `(027) serve 는 t4g.micro + 스왑 — 메모리 측정 뒤 승격 판단`, `(027) 016·017·018·021·023·025 의 해당 문장이 027 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기`.
- `CLAUDE.md` — 스펙 인덱스 027 행 상태 → DONE. §2 레포 구조에 `caddy/`(Caddyfile)·`ops/`(`cloudwatch/`·`canary/` — 박스에 올리는 설정) 줄, runbooks 목록에 `uptime-monitor.md`·`cloudwatch.md`. §5 수정 가능 목록에 `ops/`·`caddy/`.
- `docs/context/architecture.md` — 16행 '런타임 구성' 의 api 태스크를 "017 구독 태스크 + `SLACK_WEBHOOK_URL` 이 있으면 025 알림 태스크 + `STATSD_ADDR` 가 있으면 027 게이지 태스크(+ 접속마다 보내기 태스크)" 로. 106행 "`/health` 는 프로세스 liveness 만 나타낸다" → "025 이후 마지막 틱이 30초 안에 있었는지를 답한다(200 ok / 503). 밖에서는 `/api/health`(collector)만 열고, api·Redis·Influx 는 canary 가 본다(027)". '배포 토폴로지' 절 152행 collect 줄의 "IAM 프로파일은 이 박스에만" → "`marketlens-s3-snapshot` 은 이 박스에만(세 박스 모두 에이전트 정책, data·serve 는 `marketlens-cwagent` — 027)", 154행 serve 줄에 "스왑 1GB(027)" 와 caddy 설명 "`caddy/Caddyfile` 디렉터리 바인드·배포 뒤 reload·도메인 블록 접속 로그 → 호스트 `logs/caddy/`". "현재 구조" 에 observability 항목(게이지 위치·`ops/` 두 폴더·계약 테스트).
- `docs/context/dev-setup.md` — env 절 `ROLE` 설명의 "백그라운드 태스크는 017 구독 하나" → architecture.md 16행과 같은 문구, env 표에 `STATSD_ADDR`(compose 가 api 에만 준다, 비면 끔) 행. 'docker 통합 기동' 절에 "caddy 접속 로그는 `./logs/caddy/access.log`(git 무시, 로컬은 catch-all 이라 거의 비어 있다)" 한 문장, `caddy validate` 명령(`./caddy` 경로).
- `server/.env.example` — `# STATSD_ADDR=` 와 설명 1줄(027 — compose 가 api 에만 `host.docker.internal:8125` 를 준다, 로컬은 비워 둔다).
- `docs/runbooks/uptime-monitor.md` — "왜 `/health` 하나로 되는가" 절을 §3.1 대로 고친다 — `/api/health` 는 수집기가 답해서 api·Redis 사망은 못 잡고, 그건 canary(027)가 잡는다. 모니터 URL 은 그대로. 마지막의 CloudWatch 후속 후보 문장을 지운다.
- `docs/runbooks/cloudwatch.md` — 신규. §3.7 순서대로, 단계마다 확인·되돌리기, 관리자 절, 경보 목록(§3.6 값 그대로), serve 설정 두 단계 불러오기, serve 스왑·메모리 측정·승격 판단 기준, Logs Insights 저장 쿼리 3개(경로별 요청 수, 외부 `referer` 출처별 방문, WS 연결 지속 시간 분포). 300줄을 넘으면 절 단위로 커밋을 나눈다.
- `docs/runbooks/ec2-split.md` — 3단계(data 스왑) 옆에 serve 스왑 1GB 절차 한 줄. `ec2-setup.md` — IAM 절에 `marketlens-cwagent`·collect 정책 추가·IMDS 설정.
- `docs/specs/007-deploy.md` — §3 컨테이너 목록에 `caddy` 한 줄(serve profile, `./caddy:/etc/caddy:ro`·`./logs/caddy:/var/log/caddy`·`caddy-data`·`caddy-config`, 023·027), `api` 줄에 `STATSD_ADDR`·호스트 게이트웨이, `web` 줄에 "nginx 접속 로그는 끈다(기록은 caddy, 027)", 배포 절에 serve 의 caddy reload. §2 하지 않는 것의 "로그 수집·모니터링" 뒤에 "(027 이 CloudWatch 로 한다)".

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 016 — §3.1 "017 의 구독 태스크 하나뿐이다" → architecture.md 16행과 같은 문구. §3.5 마지막 bullet → "두 역할의 `/health` 는 025 §3.5 판정을 따른다. 밖에는 nginx 의 `/api/health`(`server`)만 열고, api 는 canary(027)가 밖에서 본다".
- 017 — §7 남은 빚의 "외부 헬스체크는 여전히 없다" → "밖에서는 canary 가 WebSocket 까지 본다(027)".
- 018 — §3.4 api 백그라운드 태스크 문장을 architecture.md 16행과 같은 문구로.
- 021 — §3.1 collect 문장의 "이 박스에만" → "S3 역할은 이 박스에만, 세 박스 모두 에이전트 정책(027)", serve 박스 설명에 "스왑 1GB(027)".
- 023 — §2 만드는 것의 "루트 `Caddyfile`" → "`caddy/Caddyfile`(027 이 옮김, 디렉터리 바인드)", §2 하지 않는 것의 "배포 워크플로 변경 없음(…)" → "serve 배포는 `up` 뒤 caddy 설정을 다시 읽는다(027)", §3.3 에 "도메인 블록은 접속 로그(027 §3.2)와 `Referrer-Policy: strict-origin`", §4 명령의 경로.
- 025 — §3.6 의 CloudWatch 후속 후보 문장 삭제(모니터 URL 은 그대로). §2 하지 않는 것의 괄호 "(CloudWatch Agent — 런북에 후속으로만 적는다)" → "(027 — CloudWatch Agent)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
