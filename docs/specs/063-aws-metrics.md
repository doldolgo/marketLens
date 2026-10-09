# 063 — aws-metrics

상태: TODO | 의존: main(034). 064 admin-v3 가 이 스펙의 피드를 쓴다. 고치는 스펙 034(이 레포 주인 담당).

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
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 server 칸 끝에 "· AWS 시계열(063 — `/admin/aws/series` 6h·24h·7d·30d, CPU·네트워크·EBS·크레딧·상태 검사·메모리·디스크·스왑)".
- `CLAUDE.md` — 스펙 인덱스 063 행 DONE.
- `docs/context/architecture.md` — '현재 구조' admin 항목에 series 피드(같은 스레드·Slot).
- `docs/runbooks/cloudwatch.md` — 관리자 피드 절에 "series 는 GetMetricData 한 번(지표 ≈40개, 관리자 화면이 열린 동안만) — 새 권한 없음".
- `docs/specs/034-monitoring-ops.md` — §3 피드 목록 끝에 "`/admin/aws/series`(063)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
- 남은 빚:
