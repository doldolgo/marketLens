# 034a — monitoring-ops

상태: TODO | 의존: 025 slack-alerts(알림기 `notify`·보내기 태스크·SlackLogHandler), 027 observability(에이전트 지표 이름·경보 이름·canary 로그), 010 raw-archive(collect 역할 자격증명), 029 admin(관리자 nginx 분기·보호 규칙), 030 admin-tunnel(들어오는 길), 028 api-allowlist(공개 허용 목록), 022 landing(3초 기다림 규칙의 모양). 짝 스펙 034b(api 쪽 피드 둘 — 이 스펙 뒤에 한다)와 공통 규칙(§3.1)을 같은 문장으로 나눠 가진다. 031·032·033 과는 코드가 겹치지 않는다. 화면은 035.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
관리자 페이지(035)가 한 화면에 모을 운영 정보 중 수집기가 만드는 두 가지를 준다 — CloudWatch 경보·24시간 지표·canary·예산(AWS 요약), 그리고 025 가 Slack 으로 보낸 알림에 경보 상태 변경을 합친 알림 기록. 지금은 AWS 콘솔과 Slack 을 따로 열어야 하고, 보낸 알림은 어디에도 남지 않는다. AWS 계정은 2026년 12월에 끝나고 조직 SCP 가 일부 API 를 막는다 — 설정·자격증명·권한이 없으면 그 부분만 상태로 답하고 나머지는 그대로 동작한다.

## 2. 범위
- 만드는 것: 수집기 역할의 관리자 피드 두 경로(기능 폴더 `admin` 에 collector 라우터), 025 알림기의 기록 자리와 core `RedisBus` 공개 메서드 둘, 관리자 nginx 정확 일치 location 둘, compose(server env 하나), 런북 절(IAM 읽기 정책), 계약 테스트.
- 하지 않는 것: 화면(035). 접속 요약·Clarity(034b). Cost Explorer(`ce:GetCostAndUsage` — SCP 가 막는다)·Logs Insights·Synthetics. 예산 알림·Amazon Q 가 Slack 에 올린 메시지의 기록(읽을 API 가 없다 — 예산은 사용액으로 본다). 경보·예산 설정을 바꾸는 조작(전부 읽기). 새 라이브러리(boto3 는 010 이 이미 올렸다). serve 스왑 지표 추가(027 에이전트 설정 — 사람 결정, §3.2).
- 바꾸는 기존 것: 025(알림기가 보낸 결과를 기록 — 보내는 규칙·문구·억제는 그대로), 029(관리자 nginx location 둘·폴링 기록 제외, collector 라우트), 027(collect 역할에 읽기 정책, 경보 이름을 박스 찾기에 씀), 007(compose), 016(역할별 경로 집합).
- 담당: 016·021·025 는 hereokay 담당 — 이 PR 은 그 스펙을 고치지 않고 §6 "담당자에게 제안" 을 PR 본문에 적는다(CLAUDE.md §5). 코드(`core/notify.py`·`core/redis_bus.py`·`main.py`)는 이 스펙대로 고친다(027 과 같은 방식). 007·027·029 는 이 레포 주인 담당이라 고친다. §5 허용 목록 밖(사람 승인): `docker-compose.yml`.

## 3. 동작

### 3.1 경로·공통 규칙 (034b 와 같은 문장)
| 경로 | 역할 |
|---|---|
| `/admin/aws` | collector |
| `/admin/alerts` | collector |

- 수집기에 두는 이유: 자격증명이 collect 박스의 인스턴스 역할(`marketlens-s3-snapshot`, 메타데이터 hop 2 라 컨테이너가 쓴다 — 010)에만 있다. serve 는 027 이 일부러 컨테이너를 역할에서 막았다(토큰 필수·hop 1) — hop 을 올리면 cloudflared(030)까지 닿는다. serve 호스트 cron 은 앱 밖 코드·호스트 CLI·data 와 같이 쓰는 역할 확장이 들고, 정적 키는 오래 사는 비밀이다. 수집기는 페이지가 보이는 동안만 부르고 부담은 §3.4 처럼 작다. api 역할에서는 404 이고, OpenAPI 는 collector 스키마에만 두 경로가 있다.
- 관리자 nginx(029, :8081)에 정확 일치 둘: `= /api/admin/aws`·`= /api/admin/alerts` → 수집기(`${COLLECT_HOST}:8000`, 접두 `/api` 제거 — 029 의 `/api/` 분기와 같은 곳). 따로 두는 이유는 화면의 폴링을 접속 기록에서 빼려는 것이다(`access_log off`). 첫 줄은 029 의 교차 사이트 검사(`Sec-Fetch-Site` 가 same-origin·none·빈 값만 통과, 나머지 403 JSON). server 수준 헤더(`Cookie`·`Cf-Access-Jwt-Assertion` 비움·ACAO 지움·`X-Frame-Options`)는 상속한다 — 자기 `proxy_set_header`·`add_header` 를 두지 않는다. 공개 nginx(028)는 바꾸지 않는다 — 두 경로는 허용 목록 밖이라 028 의 404 JSON 이다.
- 응답은 항상 200 JSON(상태 응답 — `{"error":…}` 형식 아님), 키 camelCase, `…At` 은 epoch ms, `…Ts` 와 점·시간 칸의 `ts` 는 epoch 초(product.md 시각 단위). 한 응답은 **부분**들이고, 부분은 `{state, code, fetchedAt, refreshSec, …값 키}` 객체다.

| state | 뜻 |
|---|---|
| `ok` | 값있음 |
| `unconfigured` | 연결안됨 |
| `denied` | 권한없음 |
| `error` | 실패 |
| `pending` | 첫조회중 |

- `code`: `ok` 면 null, 아니면 짧은 사유 — AWS 오류 코드(`AccessDeniedException` 등)·`no_credentials`·예외 이름·`timeout`·`redis`·`partial`. **오류 문장은 싣지 않는다** — AWS 오류 문장에는 계정 ID 가 든 ARN 이 있다.
- `fetchedAt` = 그 부분의 값을 만든 시각(못 만들었으면 null), `refreshSec` = 갱신 주기(화면이 신선도 판정에 쓴다). `ok` 가 아니면 그 부분의 값 키는 모두 null 이다 — 직전 값을 정상처럼 보이지 않게(029 §3.3 원칙).
- 갱신(022 랜딩의 3초 규칙과 같은 모양): 요청이 왔을 때 그 부분이 비었거나 `refreshSec` 가 지났으면 갱신을 하나만 띄우고 3초까지 기다린다. 끝나면 새 값, 아니면 직전 결과(없으면 `pending`)를 답하고 갱신은 뒤에서 마저 돈다. 그동안 온 요청은 같은 갱신을 기다린다. 실패한 결과도 주기만큼 캐시한다(막힌 API 를 요청마다 두드리지 않게). **요청이 없으면 아무것도 부르지 않는다** — 페이지를 안 보면 AWS 호출 0.
- 스레드: AWS 호출(클라이언트 생성·응답 풀기 포함)은 **전용 실행기(스레드 1개)** 에서 한 번에 하나씩 돈다 — `asyncio.to_thread`(기본 실행기)를 쓰지 않는다. 수집기의 기본 실행기는 c7g.medium(1 vCPU)에서 스레드 5개이고 Influx 틱 쓰기·캔들 롤업·원문 묶기·사건·실패 구간 쓰기가 이것을 쓴다. AWS 가 느리거나 막혀 호출마다 최대 7초(연결 2+읽기 5)를 잡으면 수집 쓰기가 줄을 선다.
- 로그·예외: 피드 처리기는 모든 예외를 잡아 부분 상태로 바꾼다 — 500 까지 올라가지 않는다(025 의 처리 안 된 500 알림은 예외 문장을 싣는다). 외부 호출 실패는 `marketlens.admin` 로거에 **WARNING** 으로 부분 이름과 `code` 만(오류 문장·ARN·헤더 없이) 부분마다 10분에 1줄 남긴다. ERROR 로는 남기지 않는다 — 025 SlackLogHandler 가 `marketlens.*` 의 ERROR 를 `str(exc)` 앞 200자와 함께 Slack 으로 보내고, botocore `AccessDenied` 문장은 `User: arn:aws:sts::<계정>:assumed-role/…` 로 시작한다. SCP 로 늘 막힌 API 가 10분마다 알림을 만들지도 않게.
- 가림: 이 스펙이 기록하거나 응답하는 글(알림 기록 `text`·경보 `reason`·경보 이력 `text`·canary `lines`)에서 `arn:aws:` 로 시작하는 공백 없는 덩어리와 앞뒤가 숫자가 아닌 12자리 숫자를 `[가림]` 으로 바꾼다. ARN·계정 ID 는 어떤 응답에도 없다.

| 부분 | 주기초 |
|---|---|
| alarms | 60 |
| metrics | 300 |
| canary | 60 |
| budget | 21600 |

주기 값은 사람 확인 대상의 기본값이다(요금과 신선도의 교환 — §3.4). 알림 기록의 `slack` 부분은 요청마다 Redis 를 읽는다(`refreshSec` 0).

### 3.2 AWS 요약 — `GET /admin/aws`
- 응답 = 부분 넷 `{alarms, metrics, canary, budget}`.
- 설정: env `ADMIN_AWS_REGION` — 비면 네 부분 모두 `unconfigured`(code null)이고 AWS 를 부르지 않는다(로컬·테스트 기본). compose 가 `server` 에만 `ap-northeast-2` 를 준다. 자격증명은 SDK 기본 탐색(env 에 키를 두지 않는다 — 010). 클라이언트는 첫 요청 때 만든다(기동에 넣지 않는다). 호출마다 연결 2초·읽기 5초, 재시도 없음. 예산은 전역 끝점(`budgets.amazonaws.com`).
- 오류 분류(한 부분의 실패는 다른 부분에 번지지 않는다):
  - `unconfigured` — 자격증명을 얻지 못하거나 쓸 수 없음: botocore `NoCredentialsError`·`CredentialRetrievalError`(code `no_credentials`), AWS 코드 `InvalidClientTokenId`·`UnrecognizedClientException`·`ExpiredToken`(code 는 그 코드). AWS 밖 호스트나 계정 종료 뒤가 이렇다 — compose 의 `ADMIN_AWS_REGION` 을 고치지 않아도 "연결 안 됨" 으로 보이게.
  - `denied` — `AccessDenied`·`AccessDeniedException`·`UnauthorizedOperation`(SCP 명시 거부도 같은 코드).
  - `error` — 네트워크·시간 초과·그 밖.
- **alarms** — `DescribeAlarms`(`AlarmNamePrefix` `marketlens-`, 지표 경보). `items` = 이름 순 `{name, state, changedAt, reason}` — `state` 는 `OK`·`ALARM`·`INSUFFICIENT_DATA`, `changedAt` 은 상태가 바뀐 시각(`StateTransitionedTimestamp`), `reason` 은 `StateReason` 앞 200자. `counts` = `{ok, alarm, insufficientData}`. 027 기준 17개(로그 전송 뒤 18).
- **metrics** — 24시간·5분 격자. `endTs` = 지금을 300초로 내린 값, `startTs = endTs − 86,400`, `periodSec` 300. 시계열은 `[[ts, 값 또는 null], …]` 288점(`ts` 는 구간 시작, 자료 없는 구간 null, 전부 null 이면 시계열 자체가 null). `GetMetricData` 한 번으로 읽는다(16지표 안팎).
  - 박스 찾기: 경보 부분의 결과에 기대지 않는다. metrics 가 자기 `DescribeAlarms`(접두 `marketlens-`)로 경보 `marketlens-<박스>-memory` 의 `InstanceId` 차원을 얻어 1시간 캐시한다(027 경보 이름 규칙 — 그 경보가 없는 박스는 빠진다). 이 호출이 실패하면 metrics 부분도 같은 분류(`unconfigured`·`denied`·`error`, code 그대로)다. 네임스페이스 MarketLens 지표의 차원(에이전트가 붙이는 `path`·`fstype`·`metric_type` 등)은 `ListMetrics` 로 얻어 그대로 쓴다(1시간 캐시).
  - `boxes`(collect·data·serve 순): 박스마다 `box`·`instanceId`·`mem`(`mem_available_percent` 최솟값)·`disk`(`disk_used_percent` 최댓값)·`cpu`(AWS/EC2 `CPUUtilization` 평균)·`credit`(AWS/EC2 `CPUCreditBalance` 최솟값 — c7g 는 null)·`swap`(`swap_used_percent` 최댓값 — 그 지표를 보내는 박스만. 027 설정은 data 만 보내 collect·serve 는 null).
  - `wsClients`: `marketlens_ws_clients` 최댓값(027 — StatsD 가 점을 밑줄로 바꾼 이름). `canary` = `{runs, errors, durationMs}` — AWS/Lambda `FunctionName=marketlens-smoke` 의 `Invocations` 합·`Errors` 합·`Duration` 최댓값.
  - 값은 퍼센트·크레딧 소수 둘째 자리, 개수·ms 는 정수.
- **canary** — 최근에 끝난 실행 하나. `FilterLogEvents`(로그 그룹 `/aws/lambda/marketlens-smoke`, 최근 11분, `startFromHead=false` — 최신부터. startTime 이 2024 이후라 허용된다). 이 API 는 결과가 남아 있어도 빈 쪽이나 덜 찬 쪽을 `nextToken` 과 함께 줄 수 있다(API 문서) — 가장 늦은 `REPORT RequestId: …` 줄과 같은 요청 ID 의 `START` 줄을 찾거나, 3쪽·2초에 닿거나, `nextToken` 이 없을 때까지 따른다. 그 요청 ID 의 줄만 모은다: `lastRunAt`(그 실행의 `START` 시각), `durationMs`(REPORT 의 `Duration`), `ok`(`4단계 통과` 줄이 있으면 true — 027 canary 는 단계마다 `N단계 통과 (ms)` 를, 실패면 `N단계 실패: …` 를 낸다), `lines`(START·END·REPORT·INIT 줄을 뺀 메시지 — Lambda 기본 텍스트 형식 `시각⇥요청ID⇥레벨⇥메시지` 의 메시지, JSON 오류 줄은 `errorMessage` 만, 줄마다 300자·20줄까지).
  - 11분 안의 줄을 끝까지 읽었는데 끝난 실행이 없으면 `lastRunAt` null·빈 `lines`(state `ok` — 일정이 멈춘 것은 `canary.runs` 와 canary 경보가 말한다). 한도(3쪽·2초)에 닿았는데 REPORT 가 없으면 `error`·`partial`. 텍스트 형식은 027 이 로그 형식을 따로 정하지 않아서다 — 사람 확인.
- **budget** — `GetCallerIdentity`(권한 불필요)로 계정을 알고 `DescribeBudgets`. `items` = 비용 예산마다 `{name, unit, limit, actual, forecast, timeUnit}`(금액 소수 둘째 자리, `forecast` 없으면 null) — 027 의 월 $130 예산이 보여야 한다. 계정 ID 는 호출에만 쓰고 응답·로그에 싣지 않는다. AWS 는 예산 값을 하루 몇 번 갱신한다(Budgets API 문서) — 6시간 캐시. Budgets API 가 SCP 로 막혔는지는 모른다(2026-10-01) — 막히면 `denied`.
- IAM(사람 — 런북 `cloudwatch.md`): collect 역할 `marketlens-s3-snapshot` 에 인라인 정책 `marketlens-admin-read` 하나, 쓰기 권한 없음. `cloudwatch:DescribeAlarms`·`DescribeAlarmHistory` 는 먼저 리소스 `arn:aws:cloudwatch:ap-northeast-2:$ACCOUNT:alarm:marketlens-*` 로 붙이고, CloudShell 에서 `describe-alarms --alarm-name-prefix marketlens-` 와 경보 이름 없는 `describe-alarm-history --history-item-type StateUpdate`(§3.3 이 이렇게 부른다)가 되는지 본다. 안 되는 액션만 리소스 `*` 로 넓히고 결과를 §7 에 적는다 — 조직 임대 계정이라 다른 경보가 있으면 `*` 는 그것도 읽는다. `cloudwatch:GetMetricData`·`ListMetrics` 는 리소스 수준 권한이 없어 `*`, `logs:FilterLogEvents` 는 canary 로그 그룹 하나, `budgets:ViewBudget` 은 이 계정의 `budget/*`. ARN 은 런북의 `$ACCOUNT` 변수 방식으로 적는다(레포는 공개). 수집기 컨테이너도 이 읽기 권한에 닿는다 — 받아들인다(027 의 에이전트 정책과 같은 판단).

### 3.3 알림 기록 — `GET /admin/alerts`
- 기록(025 에 더함, 두 역할 모두): 알림기의 보내기 태스크가 알림 하나를 보낸 뒤(2xx 든 실패든) Redis 리스트 `alerts:log` 왼쪽에 JSON 한 줄 `{at, role, key, text, delivered}` 를 넣고 1,000건만 남긴다(넣기·자르기 한 왕복). `at` = `notify()` 가 불린 시각(ms), `text` = `[role] ` 머리를 뺀 문구에 §3.1 가림을 건 것, `delivered` = Slack 이 2xx 로 받았는지. 억제된 알림·큐가 가득 차 버린 알림은 보내지 않았으므로 기록도 없다. `SLACK_WEBHOOK_URL` 이 없으면 알림기가 없어 기록도 없다.
- 무엇이 쌓이나: 운영 알림 문구 최신 1,000건, 만료 없음. 025 알림은 ERROR 로그·처리 안 된 500 의 예외 문장 앞 200자를 싣는다 — 방문자 정보가 섞이지 않는 것은 025 문구 규칙(방문자 IP·User-Agent·쿼리·쿠키를 싣지 않음 — 032 가 025 담당자에게 제안한 문장)에 기댄다. 032 방침에 이 기록을 적을지는 사람 확인.
- 기록 쓰기는 2초 제한, 실패하면 버리고 `marketlens.notify` 로거에 WARNING 10분 1줄(이 로거는 Slack 으로 안 간다 — 순환 없음). 보내기 순서·억제·문구·큐 상한은 025 그대로. 버스가 생기기 전(lifespan 전)의 알림은 기록하지 않는다.
- core 공개 계약: `RedisBus.alert_log_push(line: str) -> None`·`RedisBus.alert_log_recent(limit: int) -> list[str]`(최신순). 알림기는 lifespan 이 버스를 만든 뒤 꽂는 기록 함수 하나(`async (line: str) -> None`)를 받는다. `notify(key, text)` 계약은 그대로.
- 응답 = `{items, slack, alarms}`. `slack`·`alarms` 는 값 키 없이 상태만 있는 부분이고, `items` 는 두 출처를 시각으로 합친 최근 7일·최신순·200건까지다(성공한 쪽 항목만 — 늘 목록, 한쪽이 실패해도 다른 쪽 항목은 싣는다). 항목은 `{at, source, text, role, key, delivered, alarm, fromState, toState}`(해당 없는 키는 null).
  - `source: "slack"` — `alerts:log` 최근 200줄, 7일 밖·깨진 줄은 뺀다. `slack` 부분: `unconfigured` = 이 프로세스에 웹훅이 없음, `error` = Redis 불달.
  - `source: "alarm"` — `DescribeAlarmHistory`(`HistoryItemType` StateUpdate, 최근 7일, 최신순, 100건씩 두 쪽까지 — 이 API 는 이름 접두 조회가 없어 경보 이름 없이 부른다)에서 이름이 `marketlens-` 로 시작하는 것. `alarm`·`fromState`·`toState`(`HistoryData` 의 `oldState`·`newState` 의 `stateValue`)·`text`(새 상태 사유 앞 200자, 가림 뒤). AWS 는 경보 이력을 30일 둔다(2024-05 발표). §3.2 의 설정·오류 분류·전용 실행기와 alarms 주기(60초)를 따른다.

### 3.4 부담 (2026-10-01 측정 — Mac M5 Pro·Python 3.12·botocore 1.43.105, 운영 값 아님)
- 수집기(c7g.medium — 1 vCPU 를 80~100% 쓴다): 클라이언트 셋을 처음 만들 때 RSS +15MB·CPU 40~50ms. CloudWatch 는 이 botocore 에서 smithy-rpc-v2-cbor 프로토콜이다 — `GetMetricData` 16지표×288점 응답(CBOR ≈88KB)을 botocore 파서로 풀면 중앙값 ≈18ms, c7g 는 2배 안팎으로 추정한다. 5분에 한 번 수십 ms 동안 GIL 을 잡고, 틱 루프는 5ms 전환 간격(`sys.getswitchinterval`)마다 끼어든다. 나머지 호출은 응답이 작아 ms 단위다. 페이지가 보이는 동안만이고, 전용 스레드라 기본 실행기의 수집 쓰기와 줄을 서지 않는다. 배포 뒤 collect 에서 한 번 잰다(§4).
- AWS 요금: `GetMetricData` 는 무료 한도 없이 지표 1,000개당 $0.01(CloudWatch 문서) — 16지표·5분 주기라 페이지를 하루 종일 열어 두면 월 약 $1.4, 하루 1시간이면 $0.1 아래. `DescribeAlarms`·`DescribeAlarmHistory`·`ListMetrics` 는 월 100만 요청 무료 한도 안으로 본다(027 에이전트 전송과 같이 센다 — 추정). `FilterLogEvents`·Budgets 조회는 가격표에서 요청 요금을 찾지 못했다(예산은 하루 4회 이하). 첫 달 청구로 사람이 확인.

### 3.5 엣지
- AWS 계정 종료(2026-12)·AWS 밖 호스트: 자격증명을 못 얻어 AWS 네 부분과 알림 기록의 `alarms` 가 `unconfigured`(`no_credentials`). `slack` 은 그대로.
- SCP 가 한 API 만 막음(예: Budgets): 그 부분만 `denied`, WARNING 10분 1줄, Slack 알림 없음.
- AWS 가 느림·막힘: 전용 스레드 하나라 부분 갱신이 차례로 밀리고, 요청은 3초 뒤 직전 결과나 `pending` 을 받는다. 수집(틱·쓰기)은 영향이 없다.
- 수집기 재시작(배포): 캐시가 비어 첫 요청이 3초 안에 못 채우면 `pending`, 다음 폴링에 채워진다. `alerts:log` 는 Redis 라 남는다.
- Redis 불달: `slack` 부분 `error`, 새 알림 기록은 버려지고 Slack 전송은 계속.
- 경보 이름 규칙이 바뀜: 박스가 `boxes` 에서 빠진다 — 027 의 경보 이름을 바꿀 때 이 스펙을 같이 고친다.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 네트워크 없음 — AWS 는 botocore `Stubber`, Redis 는 fakeredis.
- 역할(`tests/test_role.py`): collector 에 `/admin/aws`·`/admin/alerts`, api 에서는 404, collector OpenAPI 에 둘·api OpenAPI 에 없음.
- nginx(`tests/test_admin.py`): 관리자 server 에 정확 일치 둘 — 수집기로·접두 제거, 첫 줄 교차 사이트 검사, `access_log off`, 자기 `proxy_set_header`·`add_header` 없음(상속). 업스트림 둘뿐. 공개 server 는 028 계약 그대로.
- compose: server 에 `ADMIN_AWS_REGION`, 다른 서비스엔 없다. 기존 계약(컨테이너 일곱·profile·로그 상한) 그대로.
- AWS: 설정 없음 → 네 부분 `unconfigured`·호출 0 / 정상 → 모양(경보 순서·counts, 박스는 경보 이름에서·경보 없는 박스 빠짐, 288점·빈 구간 null, c7g `credit` null, `swap` 은 보내는 박스만) / `AccessDenied` 는 그 부분만 `denied` / 예산만 `AccessDeniedException` → budget 만 `denied` / `NoCredentialsError`·`InvalidClientTokenId` → `unconfigured` / 시간 초과 → `error` / 박스 찾기 `DescribeAlarms` 가 `AccessDenied` → metrics 도 `denied`(alarms 부분 결과와 무관) / 60초 안 두 요청은 호출 한 벌, 지표 300초·예산 6시간, 실패도 주기만큼 캐시 / 느린 가짜(3초 넘음) → `pending`, 같은 갱신이 뒤에 채움, 동시 요청 = 갱신 하나.
- canary: `startFromHead=false` 로 부름 / 마지막 REPORT 의 실행 줄만 / `4단계 통과` → ok, 실패 줄, JSON 오류 줄은 `errorMessage` / 빈 첫 쪽 + `nextToken` → 다음 쪽을 따라가 찾음 / 3쪽 모두 비고 토큰이 남음 → `error`·`partial` / 11분 안 실행 없음 → `lastRunAt` null·state ok.
- 스레드: 느린 가짜 AWS 호출(5초)이 도는 동안 `asyncio.to_thread` 작업 5개를 동시에 넣어도 각각 0.5초 안에 끝난다.
- 새지 않음: ARN 이 든 `AccessDenied` 문장·ARN 이 든 Slack 알림·ARN 이 든 경보 사유 → 응답 바이트와 `alerts:log` 에 `arn:aws:`·12자리 숫자가 없다 / budget 이 계속 `denied` 인 채로 10분 폴링 → Slack 알림 0·WARNING 1줄 / 처리기 안 예외 → 500 이 아니라 그 부분 `error`.
- 알림 기록: 보낸 알림 → `alerts:log` 한 줄(`delivered` true), 웹훅 500 → false 로 기록, 억제된 두 번째·큐 초과·웹훅 URL 없음 → 기록 없음, Redis 예외 → 다음 알림 전송 계속·WARNING 10분 1줄·Slack 으로 안 감, 1,001건째에 1,000건. 타임라인: 두 출처 시각순·7일 밖 제외·200건 상한·`marketlens-` 밖 경보 제외·깨진 줄 건너뜀·한쪽 실패에도 다른 쪽 항목.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`. 로컬 Docker: web 이미지 `nginx -t`(029 와 같은 방법).

**배포·런북 뒤 — 사람(완료 조건 아님, status.md 비고 "034a 운영 확인 대기")**: IAM 인라인 정책(런북 — 좁힌 경보 리소스가 되는지부터) 뒤 관리자 페이지에서 `/api/admin/aws` 네 부분 상태를 §7 에 적는다 — 특히 예산이 SCP 로 `denied` 인지 / 박스 셋·경보 17개 / canary 로그가 텍스트 형식인지 / 관리자 접속 기록(029)에 폴링 둘이 없다 / collect 컨테이너에서 §3.4 와 같은 합성 응답 풀기를 한 번 재 §7 에 / 첫 달 청구의 GetMetricData 지표 수.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행 server 칸 끝에 "· 수집기 관리자 피드 둘(034a) — `/admin/aws`(경보·24시간 지표·canary·예산)·`/admin/alerts`(보낸 Slack 알림 + 경보 이력 7일), 부분별 state·보이는 동안만 호출·전용 스레드 1개", 비고에 "034a 운영 확인 대기(IAM)". slack-alerts 행 server 칸 끝에 "· 보낸 알림 기록 `alerts:log` 1,000건(034a)". 알려진 빚에 `(034a) FilterLogEvents·Budgets 요청 요금과 Budgets 의 SCP 여부 미확인 — 첫 달 청구·런북 확인`, `(034a) 수집기 컨테이너가 CloudWatch·예산 읽기 권한에 닿는다`, `(034a) 알림 기록은 웹훅이 있을 때만 — 억제된 알림은 기록도 없다`, `(034a) 알림 기록에 방문자 정보가 없다는 것은 025 문구 규칙에 기댄다`, `(034a) serve 스왑은 모으지 않는다(027 에이전트 설정은 data 만)`, `(034a) 016·021·025 의 해당 문장이 034a 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기`.
- `CLAUDE.md` — 스펙 인덱스 034a 행 상태 → DONE.
- `docs/context/architecture.md` — '핵심 설계 결정' 의 "Redis 를 HTTP 가 만지는 곳" 문장 괄호에 "034a 의 `/admin/alerts` 는 `alerts:log` 읽기". '계약 규칙' 에 한 줄: "관리자 피드(034a)는 부분별 `state`(ok·unconfigured·denied·error·pending)를 담은 200 상태 응답이고, 오류 문장·ARN·계정 ID 를 싣지 않는다 — 외부 호출은 요청이 있을 때만, 실패는 code 만 WARNING". '배포 토폴로지' collect 줄에 "역할에 관리자 읽기 인라인 정책 `marketlens-admin-read`(034a)". '현재 구조' admin 항목에 034a 모듈(collector 라우터·AWS 읽기·전용 실행기·가림·`RedisBus` 메서드 둘·알림기 기록 자리).
- `docs/context/dev-setup.md` — `UVICORN_ROOT_PATH` 설명 옆에 같은 방식으로 `ADMIN_AWS_REGION`(compose 가 server 에만 — `server/.env` 에 두지 않는다). '검증용 스모크' 에 `curl -s localhost:8000/admin/aws` → 로컬은 네 부분 `unconfigured`.
- `docs/context/db.md` — Redis 절에 `alerts:log`(리스트, JSON 줄 최신 1,000건·만료 없음, 두 역할의 알림기가 쓰고 수집기 `/admin/alerts` 가 읽는다, ARN·12자리 숫자는 가려 넣는다).
- `docs/context/product.md` — 기능 목록 admin 행 설명 끝에 "·CloudWatch 경보·지표·예산·알림 기록".
- `docs/specs/007-deploy.md` — §3 server 줄에 `ADMIN_AWS_REGION`(034a).
- `docs/specs/027-observability.md` — §3.6 끝에 "경보 이름 `marketlens-<박스>-memory` 는 034a 가 박스를 찾는 데 쓴다". §3.7 IAM 문장에 "collect 역할에 관리자 읽기 인라인 정책(034a)".
- `docs/specs/029-admin.md` — §3.1 표에 둘(`= /api/admin/aws`·`= /api/admin/alerts` → 수집기)과 "관리자 피드는 034a·034b". §3.2 접속 기록의 "기록하지 않는다" 목록에 둘.
- `docs/runbooks/cloudwatch.md` — 새 절 "관리자 읽기 권한(034a)": 인라인 정책(§3.2 액션·리소스, `$ACCOUNT` 변수, 경보는 좁힌 ARN 부터), 확인(CloudShell 두 명령·관리자 페이지 인프라 칸의 네 `state`·예산 조회가 SCP 에 막히는지), 넓히기(안 되는 액션만 `*`, 결과는 034a §7), 되돌리기(정책 삭제).

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 025 — §2 하지 않는 것의 "알림 이력 저장" → "알림 이력은 034a 가 Redis `alerts:log` 에 남긴다". §3.2 큐 bullet 끝에 "보낸 뒤 결과(2xx 여부)를 기록 함수로 한 줄 남긴다(034a — 보내는 규칙은 그대로)". §4 알림기 목록에 "보낸 알림마다 기록 한 줄, 억제·큐 초과는 기록 없음(034a)".
- 016 — §3.1 collector 경로 목록에 `/admin/aws`·`/admin/alerts`(034a).
- 021 — §3.1 collect 설명에 "역할에 관리자 읽기 정책(034a)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
