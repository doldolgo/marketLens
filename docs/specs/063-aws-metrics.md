# 063 — aws-metrics

상태: DONE | 의존: main(034). 064 admin-v3 가 이 스펙의 피드를 쓴다. 고치는 스펙 034(이 레포 주인 담당).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-09): CPU 점유·네트워크 in/out·CPU 크레딧 사용 같은 AWS 지표를 관리자 화면에서 그래프로, 기간을 골라 본다. 지금 `/admin/aws` 의 `metrics` 는 24시간 고정이고 네트워크·크레딧 사용·초과 과금 크레딧·상태 검사·디스크 입출력이 없다. 기간을 고르는 시계열 피드를 따로 둔다(기존 피드는 그대로).

## 2. 범위
- 만드는 것: 수집기(collector 역할) `GET /admin/aws/series?range=`, 같은 `features/admin` 의 AWS 호출·캐시 재사용, 테스트, 런북 한 줄.
- 하지 않는 것: 화면(064). IAM 정책 변경(이미 `cloudwatch:GetMetricData`·`ListMetrics`·`DescribeAlarms` 가 `*` 로 열려 있다 — 034 런북). EC2 API 호출(인스턴스 종류는 부르지 않는다). 기존 `/admin/aws` 응답(그대로).
- 바꾸는 기존 것: 034 §3(피드 목록에 하나 더).

## 3. 동작

### 3.1 경로·창
- `GET /admin/aws/series?range=6h|24h|7d|30d` — 수집기 역할에만(034 와 같은 라우터·`ADMIN_AWS_REGION` 이 비면 `unconfigured`). 관리자 nginx 는 `/api/` 접두 분기가 이미 수집기로 보낸다 — 새 location 은 두지 않고, test_admin 의 경로 표에 `/api/admin/aws/series` 를 더한다(공개 nginx 는 404 그대로).
- `range` 가 목록 밖·없음이면 `24h`. 주기(periodSec): 6h·24h → 300, 7d → 3600, 30d → 10800. 끝 = 지금을 주기로 내림, 시작 = 끝 − 창. 점은 시작부터 주기 간격의 격자(빈 칸 null) — 034 의 `metrics` 와 같은 꼴.

### 3.2 지표 (상자 셋 `collect`·`data`·`serve` — 034 처럼 경보 `marketlens-<box>-memory` 의 InstanceId 로 찾음, 1시간 캐시 재사용)
| 이름 | 원천 |
|---|---|
| cpu | EC2 CPUUtilization Average (%) |
| netIn | EC2 NetworkIn Sum ÷ 주기 (바이트/초) |
| netOut | EC2 NetworkOut Sum ÷ 주기 |
| ebsRead | EC2 EBSReadBytes Sum ÷ 주기 |
| ebsWrite | EC2 EBSWriteBytes Sum ÷ 주기 |
| creditBalance | EC2 CPUCreditBalance Minimum |
| creditUsage | EC2 CPUCreditUsage Sum |
| surplusCharged | EC2 CPUSurplusCreditsCharged Sum |
| statusFailed | EC2 StatusCheckFailed Maximum (0·1) |
| mem | 100 − MarketLens mem_available_percent Minimum (사용 %) |
| disk | MarketLens disk_used_percent Maximum |
| swap | MarketLens swap_used_percent Maximum |
그리고 상자와 무관한 `wsClients`(serve 의 `marketlens_ws_clients` Maximum), `canary`(`marketlens-smoke` Errors Sum·Duration Maximum ms). MarketLens 지표는 034 처럼 ListMetrics 가 돌려준 차원을 그대로 쓰고, 없으면 그 시리즈는 null(예: serve 의 swap, collect(c7g, 크레딧 없음)의 credit 셋).
- 값: % 와 크레딧은 소수 2자리, 바이트/초는 정수. 모든 점이 null 이면 시리즈 null.
- 한 번의 GetMetricData(필요하면 500 쿼리마다 나눔) + 페이지 넘김. 034 의 전용 스레드·타임아웃·오류 분류(`unconfigured`/`denied`/`error`)를 그대로 쓴다.

### 3.3 응답·캐시
- `{state, code, fetchedAt, refreshSec, range, periodSec, startTs, endTs, boxes:[{box, instanceId, series:{cpu, netIn, netOut, ebsRead, ebsWrite, creditBalance, creditUsage, surplusCharged, statusFailed, mem, disk, swap}}], wsClients, canary:{errors, durationMs}}` — 시리즈마다 `[[tsSec, v|null], …]` 또는 null. 부분 상태 규칙(값 키 null)은 034 그대로.
- 캐시: range 마다 034 의 Slot 하나 — refreshSec 6h·24h 300, 7d 1800, 30d 3600. 요청이 올 때만 갱신, 3초 기다린 뒤 직전 값.
- 비용: 호출당 지표 약 40개(GetMetricData 1,000개당 $0.01) — 관리자 화면이 열린 동안만이라 무시할 만하다(런북에 한 줄).

### 3.4 엣지
- 상자 경보가 없으면 그 상자는 빠진다(034 와 같다). 상자가 0개면 `boxes` 빈 목록·state ok.
- 기본 모니터링 EC2 지표는 5분 간격이라 6h 창도 72점이다.
- 30d 는 3시간 주기 — 5분 해상도 보관(63일) 안이라 끊김 없다.

## 4. 검증
- 가짜 CloudWatch(034 테스트 방식): range 넷·목록 밖 → 24h, 주기·격자·null 채움, 바이트/초 나눗셈, mem = 100 − 가용, ListMetrics 차원 없는 지표 null, 상자 없는 경우, 500 쿼리 나눔, 오류 분류 셋, 캐시 refreshSec, 기존 `/admin/aws` 응답 그대로.
- test_admin 경로 표에 새 경로(관리자 200·공개 404).
- `cd server && ruff check . && ruff format --check . && pytest -q`.
- 설계 세션: 배포 뒤 관리자 nginx 로 `/api/admin/aws/series?range=24h` 200·상자 셋·cpu 점 수 288.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 환경 — 워크트리에 가상환경이 없어 uv 로 만들었다(uv 캐시 권한 때문에 샌드박스 밖). uv 가 만든 server/uv.lock 은 커밋하지 않았다
cd server && uv sync --extra dev    # pytest 9.1.1·pytest-asyncio 1.4.0·ruff 0.16.10·fakeredis 2.39.0·botocore 1.43.110
uv run pytest -q                    # 구현 전 기준선 — 1936 passed, 1 skipped

# 완료 검증 (마지막 코드 커밋 뒤, 문서 커밋 뒤에도 같은 결과)
cd server && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
  # All checks passed! · 374 files already formatted · 1976 passed, 1 skipped

# §4 목록 — 네트워크 없음(AWS 는 botocore Stubber·가짜 클라이언트)
uv run pytest -q app/features/admin/tests/test_aws_series.py app/features/admin/tests/test_aws_series_calls.py \
  app/features/admin/tests/test_series_feed.py app/features/admin/tests/test_series_http.py   # 39 passed
  #   range 넷·목록 밖·없음·빈 값·대문자 → 24h · 주기 300/300/3600/10800·끝은 지금을 주기로 내림·격자 72/288/168/240점·빈 칸 null ·
  #   바이트/초 = 합 ÷ 주기(정수, 창마다) · mem = 100 − 가용 · %·크레딧 소수 둘째 자리 · 상태 검사·WS·canary 정수 · 0 은 null 아님 ·
  #   ListMetrics 차원 없는 에이전트 지표 null·질의 안 함 · c7g 크레딧 셋 null · 상자 경보 없음 → boxes []·ok·WS null·canary 는 읽음 ·
  #   질의 37개 한 번(통계·차원·시작/끝) · 한도를 10 으로 줄여 37 → 10·10·10·7 네 번, 합친 답이 한 번 부른 답과 같음 ·
  #   nextToken 다음 쪽을 Id 마다 이어 붙임 · 쪽 5개 뒤에도 토큰 → error·partial · 034 metrics 와 박스 찾기·차원 1시간 캐시 공유(양쪽) ·
  #   설정 없음 → unconfigured·code null·호출·스레드 0·range 실림 · 창마다 캐시 300/300/1800/3600초(30d 는 박스 찾기도 다시) ·
  #   창끼리 따로·같은 admin-aws_0 스레드 · /admin/aws metrics 는 그대로(24시간·17질의·288점) · 오류 분류 셋 — no_credentials·
  #   InvalidClientTokenId → unconfigured, AccessDenied(박스 찾기·GetMetricData) → denied, 시간 초과·처리기 예외 → error ·
  #   응답에 arn:aws:·12자리 숫자 없음 · 실패도 주기만큼 캐시·WARNING 10분 1줄(aws.series) · 느린 가짜 → pending·같은 갱신이 채움 ·
  #   HTTP 200 JSON 키 열하나·처리기 예외도 200
uv run pytest -q app/features/admin/tests/test_aws_parts.py app/features/admin/tests/test_feeds.py \
  app/features/admin/tests/test_alerts_feed.py app/features/admin/tests/test_feeds_http.py    # 55 passed — 034 그대로
uv run pytest -q tests/test_role.py tests/test_admin.py tests/test_deploy.py                   # 97 passed — collector 에
  #   /admin/aws/series·api 404·OpenAPI · 관리자 nginx 는 `/api/` 접두 분기(첫 줄 교차 사이트 검사)로 수집기 /admin/aws/series ·
  #   공개 nginx 는 028 의 404 JSON

# 돌연변이 확인(스크립트는 레포 밖 scratchpad, 하나씩 바꾸고 git checkout 으로 되돌림) — 새 테스트 39개 중 실패 수:
#   7d 갱신 1800→300: 9 · mem 의 100 − 없앰: 1 · 쪽 상한 partial 없앰: 1 · 500질의 나눔 없앰: 1 · 바이트 ÷ 주기 없앰: 5 ·
#   ok 아닐 때 range 지움: 22 · 경고 이름을 창마다: 1 · 박스 찾기 캐시를 따로: 5 · nextToken 무시: 2

# 부담(레포 밖 scratchpad, Mac·Python 3.12·botocore 1.43.110 — CloudWatch 는 resolved_protocol json·JSONParser.
# 모든 칸에 값이 있는 최악, 운영 값 아님)
#   응답 JSON 크기·인코딩·gzip 6 중앙값: 6h 46KB 0.33·0.98ms / 24h 182KB 1.32·4.60ms / 7d 105KB 0.76·1.96ms / 30d 149KB 1.09·2.64ms
#   GetMetricData 응답 풀기(합성 JSON, 중앙값·최대): 17×288(034) 15.4·21.6ms / 37×288 33.4·40.2ms / 37×240 27.6·33.6ms /
#   37×168 19.2·25.6ms / 37×72 8.3·14.6ms
```

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 server 칸 끝에 "· AWS 시계열(063 — `/admin/aws/series` 6h·24h·7d·30d, CPU·네트워크·EBS·크레딧·상태 검사·메모리·디스크·스왑)".
- `CLAUDE.md` — 스펙 인덱스 063 행 DONE.
- `docs/context/architecture.md` — '현재 구조' admin 항목에 series 피드(같은 스레드·Slot).
- `docs/runbooks/cloudwatch.md` — 관리자 피드 절에 "series 는 GetMetricData 한 번(지표 ≈40개, 관리자 화면이 열린 동안만) — 새 권한 없음".
- `docs/specs/034-monitoring-ops.md` — §3 피드 목록 끝에 "`/admin/aws/series`(063)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `server/app/features/admin/aws.py` — `AwsReader.series(window_sec, period_sec)`(034 metrics 와 같은 박스 찾기·차원 캐시)·`_metric_points`(500질의 묶음·쪽 넘김)·`_series_value`, 표 `SERIES_EC2`·`SERIES_AGENT`, `METRIC_QUERY_LIMIT`·`METRIC_MAX_PAGES`, `PartialRead` 설명.
  - `server/app/features/admin/feeds.py` — `SERIES_RANGES`(창·주기·갱신 주기)·`SERIES_DEFAULT`·`SERIES_KEYS`, 창마다 `Slot`, `AdminFeeds.aws_series`. `router.py` — `collector_router` 의 `GET /admin/aws/series`(쿼리 `range`).
  - 테스트: `features/admin/tests/aws_fakes.py`(시계열 조각·`bounds`·`stub_discovery`·`capture_metric_data`), `test_aws_series.py`·`test_aws_series_calls.py`·`test_series_feed.py`·`test_series_http.py`(새), `tests/test_role.py`(수집기 피드 셋), `tests/test_admin.py`(경로 표 한 줄·접두 분기와 공개 404).
  - 문서: §6 목록 전부, architecture.md 런타임 구성의 AWS 스레드 문장에 이 경로.
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
  - `range` 는 어느 상태에도 싣는다(고른 창 — 목록 밖·없음이면 `24h`, 038 의 `window` 와 같은 대접). ok 가 아니면 null 인 값 키는 `periodSec`·`startTs`·`endTs`·`boxes`·`wsClients`·`canary`(034 metrics 부분과 같다). 창 이름은 글자가 같을 때만(`7D`·빈 값 → 24h).
  - 반올림: §3.2 가 말하지 않은 `statusFailed`·`wsClients`·canary 둘은 034 처럼 정수(개수·ms). 바이트/초는 합 ÷ 주기 뒤, `mem` 은 100 − 가용 뒤에 반올림. 유한하지 않은 값은 null(034), 0 은 자료다.
  - 크레딧 셋은 c7g(collect)에도 질의한다 — 인스턴스 종류를 묻지 않으므로(§2) 자료 없음 → null(034 credit 과 같다). 상자 셋이면 질의 37개(EC2 9×3 + mem·disk 셋씩 + data swap + WS + canary 둘).
  - 쪽 넘김은 묶음마다 5쪽까지 — 한 쪽이 기본 100,800점이라 500질의×288점도 두 쪽이다. 5쪽 뒤에도 토큰이 남으면 `error`·`partial`(빈 칸을 '자료 없음' 처럼 보이지 않게 — 034 canary 와 같은 code). 결과의 `StatusCode`·`Messages` 는 보지 않는다(034).
  - 경고 이름은 창과 무관하게 `aws.series` 하나(창을 오가도 10분에 1줄). 창마다 Slot 은 따로지만 읽기는 034 의 스레드 하나에서 차례로 — 매달린 읽기 뒤에 다른 창·`/admin/aws` 갱신이 선다(034 §3.5).
  - 성능 원칙('요청 경로의 큰 JSON 은 루프에서 인코딩하지 않는다')에서 벗어남: 응답은 034 처럼 루프에서 `JSONResponse` + 전역 GZip 이다 — 관리자 화면이 부를 때만이고, 최악(모든 칸 값) 24h 182KB 가 로컬 인코딩 1.3ms + gzip 4.6ms(§5), 점 수는 034 metrics 의 2.2배다. GetMetricData 풀기(37×288 중앙값 33ms)는 전용 스레드에서 돈다.
  - 실행 중 고친 스펙 절: 이 스펙은 없음. 034 §3.1 — §6 대로 표에 한 줄과 안내 문장, 그리고 "OpenAPI 는 collector 스키마에만 두 경로" 가 셋이 되어 "collector 스키마에만 있다(063 도 같다)" 로.
- 남은 빚:
  - AWS 는 Stubber·가짜로만 확인했다 — 운영 응답(상자 셋·24h cpu 288점·30d 3시간 격자)은 §4 설계 세션 확인.
  - series 는 정확 일치 location 이 없어(§3.1) 관리자 접속 기록(029)에 남는다 — 064 가 폴링하면 줄이 쌓인다. 빼려면 034 와 같은 모양의 `= /api/admin/aws/series` 하나.
  - `creditUsage`·`surplusCharged`·canary `errors` 는 주기당 합이라 창마다 단위가 다르다(5분·1시간·3시간 합) — 064 가 `periodSec` 로 읽는다.
  - ListMetrics 는 2주 안에 자료가 있던 지표만 준다 — 2주 넘게 멈춘 에이전트 지표는 30d 창 앞쪽에 자료가 있어도 null.
  - 24h 창을 하루 종일 열어 두면 GetMetricData 시간당 444지표(월 ≈$3.2, 034 metrics 와 따로) — 첫 달 청구로 확인.
  - push·PR 은 하지 않았다.
