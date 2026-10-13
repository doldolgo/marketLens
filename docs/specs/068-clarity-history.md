# 068 — clarity-history

상태: DONE | 의존: main(040·063). 066 admin-renewal 의 'Clarity' 페이지·상세 판 비교가 이 스펙의 피드를 쓴다. 고치는 스펙 040·063(이 레포 주인 담당).

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
# 환경 — 워크트리에 가상환경이 없어 uv 로 만들었다(uv 는 샌드박스 밖). 생긴 server/uv.lock 은 커밋하지 않고 지웠다
cd server && uv sync --extra dev
uv run --extra dev pytest -q                       # 구현 전 기준선 — 2401 passed, 1 skipped

# 완료 검증 — main(061 머지)을 받은 뒤 마지막 코드 커밋에서
uv run --extra dev ruff check . && uv run --extra dev ruff format --check . && uv run --extra dev pytest -q
  # All checks passed! · 413 files already formatted · 2485 passed, 1 skipped

# §4 목록 — 네트워크 없음(Clarity 는 httpx MockTransport·Redis 는 fakeredis·AWS 는 botocore Stubber, 시계는 주입)
uv run --extra dev pytest -q app/features/admin/tests/test_clarity_{schedule,dims,history,defer,feed,safety,leaks,values,pages}.py \
  app/features/admin/tests/test_visits.py tests/test_clarity_store.py                                   # 177 passed
  #   일정: 첫 요청 기본 → 묶음 → 기기 → 유입(인자 넷·Bearer·동시 1) · 6시간 −1초 0회/6시간 기본만/12시간 기본·묶음/
  #   24시간 넷(다시 뜬 앱) · 72시간 분마다 요청 → 어떤 24시간에도 기본 4·묶음 2·기기 1·유입 1·합 8, 72시간 12·6·3·3 ·
  #   무작위 시계 다섯 씨앗 30일(절반은 가장 이른 다음 때 +0·1·60초, 나머지는 간격 경계 ±1초, 호출 0~10초) → 합 ≤ 8,
  #   같은 요청에 키 무작위 지우기 → 합 ≤ 10 · 키 셋 매시간 지우기 70시간 → 합 ≤ 10(종류 5·3·2·2 이하), 첫 지움은 기본·묶음만
  #   바로(사람 몫 2), 차원 둘은 기록을 되살림 · `admin:clarity:dims` 지움 → 기기·유입 곧바로 둘 → 그 24시간 기본 지움은
  #   되살림 · 보낸 시각 기록(느린 기본 10초) · Redis 쓰기만 실패 → 다시 안 부름·나중에 넷 다 생김 · 읽기 실패 → 0회·셋 다
  #   error·redis · 기록 잃은 Redis + 새 앱 → 넷 다 곧바로(남는 위험) · 기본 401 → 셋 다 미룸·`dims` 는 denied·http_401,
  #   429 → 기본만, 500·시간 초과 → 셋 다 부름, 기본이 다시 ok 면 미룬 셋
  #   날별: 기본 성공 → 그 KST 날 칸(값·그날 차원·fetchedAt, metrics·주소 없음)·EXPIRETIME = 그날 끝 + 90일 · 같은 날 덮어쓰기·
  #   다음 날은 차원 null 로 시작·같은 날 차원 성공이 채움 · 23:59:50 기본(10초)·00:00:00 기기 → 전날 칸의 18:30 기기 목록
  #   그대로, 다음 날 첫 기본이 새 목록 · 다시 뜬 앱의 첫 기본도 그날 차원(Redis 기록) · 날 칸 쓰기 실패 → 다음 요청이 씀 ·
  #   history: 키 여섯·오름차순·빈 날 빠짐·days 7·30·90/목록 밖·없음 30·60초 캐시(창마다)·모양 틀린 칸 뺌·토큰 없음
  #   unconfigured·Redis 0회 · 읽기 실패 error·redis·WARNING 1줄 · HTTP 200 JSON, Clarity 0회
  #   dims: 목록 여섯 한 부분 · 20행·세션 내림차순·이름순·200자 · 정수 세션만·글자 이름만 · 주소 꼴 이름은 버림(응답·Redis 에
  #   표시 글자 없음) · `Country/Region`·`Country`·`countryRegion` · Traffic 없음 null·행 0 [] · 차원 키 없는 행 → bad_data·
  #   마지막 성공 · 기기 500 → dims error·http_500·기기 목록 그대로·유입 새 값·fetchedAt 이른 쪽·기본·묶음 ok·WARNING
  #   `clarity.dims.device` 1줄 · 칸 둘의 기록 꼴 · 7일 버림 칸마다
uv run --extra dev pytest -q app/features/admin/tests/test_series_offset.py app/features/admin/tests/test_series_feed.py \
  app/features/admin/tests/test_series_http.py app/features/admin/tests/test_aws_series.py \
  app/features/admin/tests/test_aws_series_calls.py                                                      # 53 passed
  #   prev: 창 넷 — 끝 = 지금 창의 시작·격자·점 수 72/288/168/240·GetMetricData StartTime/EndTime·주기 그대로·refreshSec
  #   600/600/3600/7200 · 지금·이전 칸 따로(300초엔 지금만, 600초에 이전도) · offset `next`·`PREV`·빈 값·`prev2` → 지금 창과 같은
  #   칸·null · 설정 없음 → unconfigured 에 range·offset · HTTP `?offset=` 넷 · 063 의 KEYS 에 `offset`(지금 창 null)
uv run --extra dev pytest -q tests/test_admin.py tests/test_role.py tests/test_deploy.py tests/test_admin_contract.py \
  tests/test_admin_fake.py                                                                                # 83 passed
  #   관리자 nginx `= /svc/api/admin/clarity/history` → api `/admin/clarity/history`(교차 사이트 검사·기록 끔·헤더 상속)·
  #   공개 server 는 `/svc` 없음(정적 404) · api 역할 경로 열셋·collector 404 · 화면 계약(실제 시계열 피드에 `offset` 키)

# 돌연변이 확인(하나씩 바꾸고 git checkout 으로 되돌림) — 실패 수
#   전체 바로 부르기 예산 없앰: schedule 2·무작위 5 · 차원 기록에서 Redis 읽은 기록 뺌: history 1 · 다음 날 차원도 날 칸에
#   씀: history 1 · 날 칸 다시 쓰기 없앰: history 1 · history 기본 30 → 7: 4 · 기기·유입 미루지 않음: defer 2 · 기본 4시간: schedule 4 ·
#   차원 bad_data 없앰: dims 1 · dims fetchedAt 늦은 쪽: dims 1 · 때를 1초 일찍: 무작위 2(씨앗 다섯 중)·gaps 1 ·
#   시도 시각을 보낸 때보다 10초 앞: 무작위 5 · 종류별 24시간 가드 없앰: 무작위 0(합 10 은 전체 예산이 지킨다)·direct 1

# nginx -t — 두 템플릿을 이미지 entrypoint(envsubst)로, 업스트림 이름은 --add-host, 망 없음
docker run --rm --network none -v $PWD/web/nginx.conf:/etc/nginx/templates/default.conf.template:ro \
  -v $PWD/web/nginx-admin.conf:/etc/nginx/templates/admin.conf.template:ro -e COLLECT_HOST=server \
  -e 'NGINX_ENVSUBST_FILTER=^COLLECT_HOST$' --add-host api:127.0.0.1 --add-host server:127.0.0.1 \
  --entrypoint sh nginx:1.27-alpine -c 'mkdir -p /var/log/nginx-admin && /docker-entrypoint.sh nginx -t'
  # nginx: the configuration file /etc/nginx/nginx.conf syntax is ok · test is successful
# 분기 — 망 ml068-net, 같은 이미지의 에코 nginx 둘(별칭 api·server, 받은 $request_uri 를 JSON 으로), 끝난 뒤 모두 지움
docker exec ml068-web wget -qO- 'http://127.0.0.1:8081/svc/api/admin/clarity/history?days=7'
  # {"backend":"api","uri":"/admin/clarity/history?days=7"} — 쿼리가 따라간다. /svc/api/admin/clarity → api /admin/clarity,
  # /api/admin/aws/series?range=7d&offset=prev → 수집기 같은 URI · Sec-Fetch-Site: cross-site → 403(관리자 접속 기록에 그 한 줄뿐)
  # 공개 :80 의 /svc/api/admin/clarity/history·/api/admin/clarity/history·/api/admin/aws/series?offset=prev → 404

# 부담(레포 밖 scratchpad, Mac·Python 3.12) — 합성 차원 응답(지표 16 × 1,000행, 차원 값 무작위), parse_dims + check_json 9회
#   기기 2.98MB 중앙값 11.6ms(최대 15.3, serve ×6 ≈ 70ms)·tracemalloc 최고 10.5MB·기록 655B /
#   유입 3.38MB 12.1ms(최대 12.3, ≈ 73ms)·11.2MB·848B — 24시간에 한 번씩, 관리자 화면을 열 때만
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
  - core `server/app/core/redis_bus.py` — 해시 `admin:clarity:dims` 의 `clarity_dims_load(field)`·`clarity_dims_save(field, data)`(HGET·HSET, 만료 없음), 날별 기록 `admin:clarity:day:<YYYYMMDD>` 의 `clarity_day_save(day, data, expire_at)`(SET EXAT)·`clarity_days_load(days)`(MGET 한 번).
  - `server/app/features/admin/` — 새 `clarity_dims.py`(호출 둘의 인자·목록 표 `DIMS_CALLS`·`DIMS_KEYS`, `parse_dims`), 새 `clarity_days.py`(KST 날 `day_of`·`expire_at`·칸 JSON `day_text`·창 `window_days`·행 `history_rows`), `clarity_schedule.py`(`DirectBudget`·`ClarityKind.spend_direct`), `clarity.py`(`is_name` — `clarity_values.py` 에서 옮김, `ClarityStore` 메서드 넷), `visits.py`(호출 넷·미루기·하위 부분 `dims`·날별 기록 쓰기·`clarity_history`), `router.py`(`GET /admin/clarity/history`·series 의 `offset`), `feeds.py`·`aws.py`((창, 이전 기간) Slot·`SERIES_KEYS` 의 `offset`·`AwsReader.series(shift_sec)`).
  - `web/nginx-admin.conf` — `location = /svc/api/admin/clarity/history`.
  - 테스트: `clarity_fakes.py`(기기·유입 가짜 응답·종류 넷·`FlakyBus` 의 dims·day 메서드), 새 `test_clarity_dims.py`·`test_clarity_history.py`·`test_series_offset.py`, 다시 쓴 `test_clarity_schedule.py`, 고친 `test_clarity_defer.py`·`test_clarity_feed.py`·`test_clarity_safety.py`·`test_clarity_leaks.py`·`test_clarity_values.py`·`test_visits.py`·`test_series_feed.py`·`test_series_http.py`, `server/tests/test_clarity_store.py`·`test_role.py`·`test_admin.py`.
  - 문서: `CLAUDE.md`(068 DONE), `docs/context/{status,architecture,db,dev-setup}.md`, `docs/runbooks/clarity.md`·`cloudwatch.md`, `docs/specs/040-clarity-v2.md` §3.2·`063-aws-metrics.md` §3.1·이 문서. Clarity·AWS 실서비스는 부르지 않았다.
- 추측한 지점 (사람이 없어 가장 보수적인 쪽으로 정함):
  - 바로 부르기 — 040 의 '종류마다 24시간에 한 번' 만으로는 종류가 넷이라 키를 모두 지울 때마다 8 + 4 = 12회까지 간다(10회 한도·사람 몫 2회가 깨진다). 그래서 프로세스 전체로도 바로 부르기를 보낸 시각으로 어떤 24시간에도 두 번까지로 막았다 — 넘으면 040 처럼 기록을 되살리고 간격을 따른다(부르는 수가 줄기만 한다). 순서가 기본 → 묶음 → 기기 → 유입이라 키 셋을 한꺼번에 지우면 기본·묶음이 두 번을 쓰고 차원 둘은 되살린다. `admin:clarity:dims` 하나를 지우면 기기·유입이 두 번을 다 쓴다(런북).
  - `admin:clarity:dims` 의 '칸 둘' = Redis 해시 필드 둘(`device` = 기기 호출, `channel` = 유입 호출 — 첫 차원 이름), 값은 040 기록 JSON. 문자열 JSON 하나에 두 칸을 두면 한 칸을 쓸 때 읽고 고쳐 써야 하고 사람이 지운 다른 칸을 되살릴 수 있다. `DEL` 이면 둘이 같이, `HDEL` 이면 하나만 바로 부른다.
  - 차원 목록 — 세션은 차원 셋을 준 응답의 Traffic 행(조합마다 한 행) `totalSessionCount` 를 차원 값마다 더한 것(040 묶음과 같은 원천). 차원 행 키는 정규화한 이름(`device`·`browser`·`os`·`channel`·`source`·`countryregion`, `country` 도). 이름은 040 나라 목록 규칙 그대로 — 글자만, 주소 꼴은 출처로 줄이지 않고 버린다(이름을 그대로 저장하므로 닫힌 쪽). Traffic 지표가 없으면 그 호출의 목록 셋 null, 행 0개면 `[]`, 행은 있는데 그 목록을 하나도 못 알아보면 null. 행이 있는데 차원 키(글자 값)를 가진 행이 하나도 없으면 `bad_data` — 040 §3.4 묶음 규칙과 같게, 철자가 틀린 것을 '성공·null' 로 숨기지 않는다.
  - `dims` 하위 부분 — 칸 둘을 하나로: state·code 는 기기 → 유입 순서로 처음 ok 가 아닌 칸의 것, `fetchedAt` 은 두 칸의 마지막 성공 가운데 이른 쪽(경과를 넉넉히), 목록은 칸마다 그 칸의 마지막 성공(7일 버림은 040 과 같다). 기록이 없고 미뤘으면 기본의 state·code(040 의 `pages` 와 같다). `nextAt`·`numOfDays` 는 싣지 않았다(§3.2 의 키 목록 그대로). WARNING 부분 이름은 호출마다 `clarity.dims.device`·`clarity.dims.channel`, JSON 으로 못 쓰는 값은 `clarity.dims`, 날 칸 쓰기는 `clarity.day`, history 읽기는 `clarity.history`.
  - 날별 기록 쓰기 때 — 기본이 성공하면 같은 갱신 안에서 곧바로(차원 호출보다 먼저) 그 KST 날 칸을 덮어쓰고, 차원이 성공한 때가 기본의 마지막 성공과 같은 KST 날이면 그 칸을 한 번 더 덮어쓴다('그날 마지막 성공한 차원 값' 을 담으려고 — 호출 수와 무관). 차원 값은 그날 성공한 것만(아니면 null): 자정을 넘겨 성공한 차원은 전날 칸에 쓰지 않아 전날 칸의 그날 차원을 null 로 덮지 않는다. 다시 뜬 앱의 첫 갱신은 기본이 차원보다 먼저 불려 메모리가 비어 있으므로, 그 갱신에 Redis 에서 읽은 차원 기록과 메모리 중 늦은 성공을 쓴다. Redis 에 못 쓰면 다음 갱신이 그때의 기억으로 다시 쓴다.
  - 날 칸 JSON — `{fetchedAt, traffic, summary, countries, dims}`, `dims` 는 늘 목록 여섯 키의 객체(그날 성공이 없는 호출의 목록은 null). `metrics`(줄인 주소가 실린다)는 넣지 않았다.
  - history 응답 — §3.2 의 `{state, days, rows}` 에 관리자 피드 부분 공통 키 `code`·`fetchedAt`(읽은 때 ms)·`refreshSec`(60)을 더했다(035 §3.1 부분 꼴, 더하기만). `days` 는 어느 상태에도(038 `window`·063 `range` 와 같다), `rows` 는 ok 가 아니면 null, 행 `date` 는 `YYYY-MM-DD`(KST). 창은 오늘(KST)을 넣어 거꾸로 days 날(052 피드와 같다). 모양이 틀린 칸(JSON 아님·객체 아님)은 기록 없는 날처럼 빼고, 칸 안 값의 꼴이 틀리면 그 값만 null. 토큰이 없으면 `unconfigured` 이고 Redis 도 읽지 않는다(040 과 같다 — 남은 날 칸은 런북 '끄기' 로 지운다). Redis 실패는 `error`·`redis`, 60초 칸이 실패도 든다(034 규칙).
  - AWS `offset` — 응답 `offset` 은 `"prev"` 또는 null(지금 창), 어느 상태에도(`range` 와 같다). 목록 밖은 글자가 정확히 `prev` 가 아닌 모든 값(`PREV`·빈 값 — 063 `range` 규칙과 같다). 경고 이름은 그대로 `aws.series` 하나. 이전 기간 갱신 주기는 6h·24h 600, 7d 3600, 30d 7200(두 배는 이전 기간에만 — 지금 창은 063 그대로).
- 실행 중 함께 고친 절:
  - `docs/context/dev-setup.md` 의 `CLARITY_API_TOKEN` 설명·검증용 스모크 — §6 목록 밖이지만 4시간·14400 이 거짓이 되어 같이 고쳤다.
  - `docs/context/architecture.md` admin '현재 구조' 의 063 문장 "관리자 nginx 는 새 location 없이 `/api/` 접두 분기" — 실제 설정(정확 일치 `= /api/admin/aws/series`·기록 끔, 063 §7 설계 세션 고침)과 달라 offset 을 적으며 같이 고쳤다.
  - `docs/runbooks/cloudwatch.md` series 비용 줄에 이전 기간 한 문장(§6 목록 밖).
  - 기존 040·063 테스트 — 4시간 → 6시간, 첫 요청 호출 둘 → 넷, 키 둘 → 셋, 응답에 `dims`·`offset` 만 이 계약으로 고쳤다. 스펙 본문(§1~§4)은 고치지 않았다.
- 보고만 (이 실행이 고치지 않는 문서 — `파일:절 — 문서 주장 → 실제`):
  - `CLAUDE.md:§4 040 행` — "기본 요약 4시간(`numOfDays=1`)" → 6시간(068). 실행 세션은 인덱스 상태만 고친다.
  - `docs/specs/035-monitoring-visits.md:§3.1 주기 표` — `clarity` 14400 → 21600(+ 하위 부분 `dims` 86400), `§3.3 '호출 규칙'` — "기본 4시간·페이지×기기 12시간" → 6·12·24·24시간(068).
  - `docs/specs/036-admin-v2.md:§3.2` — "`/svc/api/admin/clarity` = 부분 하나(14400초, 040)" → 21600초, 하위 부분 `dims`(068).
  - `web/scripts/admin-fake-feeds.py:clarity()` — 가짜 Clarity 부분이 `refreshSec` 14_400·`dims` 없음, history·`offset` 없음(064 화면 개발 도구 — 066 이 그릴 때 함께).
- 남은 빚:
  - 차원 행 키 철자·값 꼴은 문서 예만 보고 짐작했다 — 배포 뒤 세션이 있는 응답에서 `dims.state` 가 `ok` 이고 목록이 차는지, `bad_data` 면 §3 을 고치는 후속(status "068 운영 확인 대기").
  - 날별 기록이 Clarity 집계를 90일 둔다(마지막 성공 값은 7일 그대로) — 처리방침은 Data Export 집계를 따로 적지 않는다(040 과 같은 판단). 늘어난 보유 기간은 사람 확인(status 빚).
  - 하루 한도는 프로세스 기억에 기댄다 — 기록을 잃고 다시 뜬 api 는 넷 다(4회) 곧바로 부른다(040 남는 위험 그대로, 수가 늘었다).
  - 이전 기간 칸의 끝은 그 칸을 갱신한 때의 지금 창 시작이다 — 지금 창(5분)과 이전 기간(10분) 칸의 갱신 때가 달라 두 응답의 경계가 한두 주기 어긋날 수 있다. 화면(066)은 `startTs`·`endTs` 로 맞춘다.
  - 화면(066)이 아직 `dims`·history·`offset` 을 그리지 않는다. `npm run lint && npm run build` 는 돌리지 않았다(web/src·web/admin 변경 없음 — CI web job 이 본다).
  - push 는 했고 PR 은 만들지 않았다.
