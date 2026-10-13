# 068 — clarity-history

상태: TODO | 의존: main(040·063). 066 admin-renewal 의 'Clarity' 페이지·상세 판 비교가 이 스펙의 피드를 쓴다. 고치는 스펙 040·063(이 레포 주인 담당).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-14): Clarity 분석을 더 자세히, 추이로 본다. 지금(040)은 마지막 성공 값 하나만 있어 7일·30일 추이·전날 대비가 없고, 기기·브라우저·OS·유입 분해도 없다. 날마다 기록을 쌓고 하루 10회 호출 한도 안에서 차원 분해를 더 받는다. 같이, AWS 시계열(063)에 **이전 기간**을 더해 상세 판의 비교선을 그리게 한다.

## 2. 범위
- 만드는 것: api `features/admin` 의 Clarity 날별 기록·차원 호출·일정 바꿈, `GET /admin/clarity/history`, `/admin/clarity` 응답의 `dims` 하위 부분, 수집기 `/admin/aws/series` 의 `offset` 인자, 관리자 nginx 정확 일치 하나, 테스트·런북 줄.
- 하지 않는 것: 화면(066). Clarity 녹화·사용자·IP 단위 자료(API 에 없다). 하루 10회 한도 넘기기.
- 바꾸는 기존 것: 040 §3(호출 일정·Redis 기록), 063 §3(offset).

## 3. 동작

### 3.1 호출 일정 (프로젝트당 하루 10회 — 040 의 메모리·Redis 시도 기록 규칙 그대로)
| 호출 | 간격·인자 |
|---|---|
| 기본 | 6시간 · `numOfDays=1` |
| 페이지 | 12시간 · `numOfDays=3`·`URL`·`Device` |
| 기기 | 24시간 · `numOfDays=1`·`Device`·`Browser`·`OS` |
| 유입 | 24시간 · `numOfDays=1`·`Channel`·`Source`·`Country/Region` |
어떤 24시간에도 4 + 2 + 1 + 1 = 8회 — 사람 몫 2회. 기본이 401·403·429 면 나머지는 미룬다(040 규칙). 새 둘의 Redis 기록 `admin:clarity:dims`(기기·유입 두 칸, 040 과 같은 `{attemptAt,state,code,successAt,values}` 꼴, 주소 없음)·메모리 기록으로 바로 부르기 가드(040 과 같다).

### 3.2 날별 기록
- 기본 호출이 성공할 때마다 그 KST 날의 칸 `admin:clarity:day:<YYYYMMDD>` 를 덮어쓴다 — 값은 040 정규화 `traffic`·`summary`·`countries` + 그날 마지막 성공한 차원 값(`dims`) + `fetchedAt`. 만료 그날 끝 + 90일. 뜻: '그날 마지막으로 받은 직전 24시간 값'(달력 하루 합이 아니다 — 화면 '?' 에 적는다).
- `GET /admin/clarity/history?days=7|30|90`(목록 밖·없음 = 30): `{state, days, rows:[{date, fetchedAt, traffic, summary, countries, dims}]}` — 날 오름차순, 기록 없는 날은 빠진다. 관리자 nginx `location = /svc/api/admin/clarity/history`(040 꼴). 60초 캐시.
- `/admin/clarity` 응답에 하위 부분 `dims` `{state, code, fetchedAt, refreshSec 86400, device:[[이름, 세션]…], browser, os, channel, source, country}` — 040 의 정규화 규칙(이름 글자·세션 정수·20행).

### 3.3 AWS 이전 기간 (063)
- `/admin/aws/series?range=…&offset=prev` — 창을 그 길이만큼 앞으로(끝 = 지금 창의 시작). 응답 꼴·주기·격자는 063 그대로, `offset` 키를 더한다. 캐시는 (range, offset) 마다 Slot(갱신 주기는 063 의 2배 — 지난 구간이라 덜 바뀐다). 목록 밖 offset 은 없는 것과 같다.

### 3.4 엣지
- 처음 배포 뒤 날별 기록은 그날부터 쌓인다 — 7일·30일 추이는 쌓인 만큼(화면은 '자료 없음 · MM-DD 부터 모음').
- 차원 호출이 실패하면 그 칸만 `error` — 기본·페이지는 그대로.
- 한도(429): 040 규칙대로 그날 남은 호출을 미루고 다음 간격에.

## 4. 검증
- 일정: 어떤 24시간에도 8회 이하(시계 돌리며), 기본 실패 시 미룸, 바로 부르기 가드, 차원 두 호출의 인자, Redis 기록 꼴.
- 날별: 기본 성공 → 그날 칸 덮어쓰기·만료, history days 셋·목록 밖, 오름차순·빈 날 빠짐, 60초 캐시, `/admin/clarity` 의 `dims` 꼴.
- AWS offset: 창 시작·끝·격자, 캐시 따로, 목록 밖 offset.
- 관리자 nginx 경로(관리자 200·공개 404), `nginx -t`.
- `cd server && ruff check . && ruff format --check . && pytest -q`.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 server 칸에 "· Clarity 날별 기록·기기/유입 차원(068 — 하루 8회)·AWS 이전 기간(068)".
- `CLAUDE.md` — 스펙 인덱스 068 행 DONE.
- `docs/context/architecture.md`·`docs/context/db.md` — Redis `admin:clarity:dims`·`admin:clarity:day:*`, 호출 일정.
- `docs/runbooks/clarity.md` — 하루 호출 표(8회 + 사람 2회), 바로 부르기 키 셋.
- `docs/specs/040-clarity-v2.md` — §3 일정 문장 끝 "(068 이 6·12·24·24시간으로 바꿈)".
- `docs/specs/063-aws-metrics.md` — §3.1 끝 "(068 — `offset=prev`)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
- 남은 빚:
