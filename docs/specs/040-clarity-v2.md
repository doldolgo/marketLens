# 040 — clarity-v2

상태: DONE | 의존: **main** — fix/036-admin-followup 이 머지돼 있어야 한다(035 §3.3 '이름 비교'와 `ReferrerUrl` 출처 줄이기). 없으면 멈추고 묻는다. 037~039·041 과 나란히 간다(권장 머지 순서 037 → 041 → 040 → 038). 계약을 쓰는 스펙(쓰는 계약은 §3.1 에 복사했다): 035(경로·부분 공통 규칙·Clarity 외부 계약·주소 줄이기·이름 비교·토큰), 036(화면이 읽는 키, 간격을 `refreshSec` 로 적는 규칙), 033(대시보드 주소의 `tab` 쓰기), 002(탭 id 여섯). 이 응답의 `summary`·`countries`·`pages` 를 그리는 것은 043 이다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
운영자가 Clarity 로 '동의한 방문자가 화면 어디를 보고 어디서 막히는가' 를 보려면 페이지·기기별 값과 이름 붙은 요약이 필요하다. 지금(035)은 차원 없이 3시간마다 불러 받은 이름·키 그대로만 싣고, 주소의 쿼리를 떼서 대시보드 탭 여섯이 모두 `/app/` 로 합쳐진다. 이 스펙이 끝나면 api 가 기본 요약을 4시간마다, 페이지×기기 묶음(직전 72시간)을 12시간마다 따로 부르고(어떤 24시간에도 합 8회), 스크롤 깊이·머문 시간·불만 신호를 이름 있는 키로 준다. 랜딩·대시보드 탭·처리방침은 기기별로 묶는다. 036 화면은 그대로 돈다.

## 2. 범위
- 만드는 것: api `GET /admin/clarity` v2(기능 폴더 `admin`) — 두 호출의 일정·기록·한도 지키기(§3.2), 정규화 `summary`·`countries` 와 대시보드 주소의 `tab` 남기기(§3.3), 페이지×기기 묶음 `pages`(§3.4). core `RedisBus.clarity_pages_load() -> str | None`·`clarity_pages_save(data: str) -> None`(Redis `admin:clarity:pages`). 테스트. 런북 `clarity.md` 5·6절.
- 하지 않는 것: 화면(043 — 이 스펙 뒤에도 036 화면은 `traffic` 타일과 받은 지표 목록을 그린다). 경로·nginx·compose·env 변경. 새 라이브러리. 문서에 없는 Clarity 엔드포인트(MCP 의 `clarity.microsoft.com/mcp/…` — 한도·계약이 없고 세션 단위 자료다). 셋째 차원. 페이지 주소 저장. 나라 이름을 ISO 로 바꾸기.
- 처리방침(032·037): 고치지 않는다. Clarity 에서 받는 것은 동의한 방문자의 집계 숫자(페이지 종류×기기 묶음 포함)와 쿼리를 뗀 주소·출처뿐이고, 7일 뒤 버린다. 대시보드 주소에 남기는 `tab` 은 허용된 탭 id 여섯 중 하나다. 방침 2절 Clarity 항목의 '보고 있는 탭' 과 같은 값이고 사람을 가리키지 않는다.
- 바꾸는 기존 것: 035 §2·§3.1·§3.3·§3.4·§3.5·§4, 036 §3.2·§3.4, 런북 `clarity.md`, context 넷(문장은 §6). 모두 이 레포 주인 담당이다. 경로·nginx·compose 가 그대로라 016·018·021 에는 고칠 문장이 없다.

## 3. 동작

### 3.1 읽는 계약 (035 복사)
- 경로: api 역할의 `GET /admin/clarity`(collector 404). 관리자 nginx 의 `= /svc/api/admin/clarity` 가 경로를 통째로 바꿔 넘긴다(029·035 — 고치지 않는다). 화면(036)은 보이는 동안 60초마다 부른다.
- 부분 공통(035 §3.1): 늘 200 JSON·camelCase, `…At` 은 epoch ms. 부분 = `{state, code, fetchedAt, refreshSec, …값 키}`. `state` 는 `ok`·`unconfigured`·`denied`·`error`·`pending`, `code` 는 ok 면 null, 아니면 `http_<상태>`·`timeout`·`redis`·`bad_data`(받은 자료가 틀림)·예외 이름이다 — 오류 문장은 없다. Clarity 는 예외로 값 = 마지막 성공이다(state 와 무관, 7일 뒤 null). 하위 부분(`pages`)은 바깥 부분 안에서 같은 모양의 자기 state 를 갖고 바깥 부분을 바꾸지 않는다 — 이 스펙이 정하는 꼴이다(034 알림의 `slack`·`alarms` 처럼 상태를 따로 갖는다).
- 갱신(035 §3.1): 요청이 왔을 때 할 일이 있으면 갱신 하나를 띄워 3초까지 기다린다. 끝나면 새 값, 아니면 직전 결과(없으면 `pending`)를 답하고 갱신은 뒤에서 마저 돈다. 그동안 온 요청은 같은 갱신을 기다린다. 요청이 없으면 아무것도 부르지 않는다. JSON 풀기·묶기는 `asyncio.to_thread` 에서 한다. 처리기 안 예외는 그 부분 `error` 이고 500 은 없다. 실패는 `marketlens.admin` WARNING 에 부분 이름(`clarity`·`clarity.pages`)과 `code` 만, 부분마다 10분에 1줄 남긴다. ERROR 는 남기지 않는다(025 가 ERROR 를 예외 문장과 함께 Slack 으로 보낸다). 토큰은 로그·Redis·응답 어디에도 없다.
- 설정: env `CLARITY_API_TOKEN`(serve 의 `server/.env`, 사람이 넣는다). 비면 `unconfigured` 이고 Redis·Clarity 를 부르지 않는다.
- 외부 계약(Microsoft Learn "Clarity Data Export API", 문서 갱신 2025-12-05): `GET https://www.clarity.ms/export-data/api/v1/project-live-insights`, 헤더 `Authorization: Bearer <토큰>`. 인자는 `numOfDays` 1·2·3(부른 때부터 거꾸로 24·48·72시간, 결과는 UTC)과 `dimension1`~`dimension3`(문서 값 `Browser`·`Device`·`Country/Region`·`OS`·`Source`·`Medium`·`Campaign`·`Channel`·`URL` — 시간 차원은 없다). 응답은 `[{metricName, information: [행…]}]`, 지표마다 1,000행까지·페이지 없음. 차원을 주면 행마다 차원 이름이 키로 붙는다(문서 예 `"OS": "Android"`). **프로젝트당 하루 10회** — 넘으면 429. 401 = 토큰 없음·틀림·만료, 403 = 권한 없음, 400 = 인자 오류. 제한 10초, 재시도 없음, 리다이렉트를 따르지 않는다, User-Agent 는 앱 공통 값.
- 실제 응답(2026-10-02 첫 응답, 세션 0 — 035 §7): `metricName` 은 CamelCase 다 — `Traffic`·`ScrollDepth`·`EngagementTime`·`PopularPages`·`Browser`·`Device`·`OS`·`Country`·`PageTitle`·`ReferrerUrl`·`DeadClickCount`·`ExcessiveScroll`·`RageClickCount`·`QuickbackClick`·`ScriptErrorCount`·`ErrorClickCount`(문서는 띄어 쓴 `Scroll Depth`·`Country/Region` 등).
  - 행 키: 불만 신호 여섯은 `sessionsCount`·`sessionsWithMetricPercentage`·`sessionsWithoutMetricPercentage`·`pagesViews`·`subTotal`, `ScrollDepth` 는 `averageScrollDepth`(0~100 %), `EngagementTime` 은 `totalTime`·`activeTime`(초로 짐작 — 세션당 평균인지 합인지 문서에 없다), `Traffic` 은 `totalSessionCount`·`totalBotSessionCount`·`distantUserCount`(문자열 숫자)·`PagesPerSessionPercentage`(이름과 달리 세션당 페이지 수). Device 값은 `Mobile`·`Tablet`·`PC`·`Email`·`Other` 다(Microsoft 의 Clarity MCP 녹화 필터 목록 — Data Export 문서에는 없다).
- 이름 비교(035 §3.3): 지표 이름·행 키는 NFKC 뒤 소문자로 바꾸고 영문자·숫자만 남겨 비교한다(`ReferrerUrl`·`Referrer URL` → `referrerurl`). 출처 지표 = 그렇게 바꾼 이름에 `referr`·`referer` 가 든 지표. 이 스펙의 지표·행 키 이름(`traffic`·`scrolldepth`·`url` …)은 모두 바꾼 꼴이다.
- 확인 못 한 것: 세션이 있는 응답의 값 꼴, 차원 행 키의 철자, `Country` 행 키, `totalTime` 단위, 스크롤 깊이가 첫 화면을 넣는지, URL 값에 `?tab` 이 실리는지. 그래서 못 알아본 칸은 null 로 두고, 기본 호출의 받은 그대로(`metrics`)를 남겨 나중에 대조한다(§4 배포 뒤).

### 3.2 호출 둘 — 일정·기록·한도
| 호출 | 간격초 |
|---|---|
| 기본 | 14400 |
| 페이지 | 43200 |

- 기본 = `numOfDays=1`, 차원 없음 → `traffic`·`summary`·`countries`·`metrics`. 페이지 = `numOfDays=3`·`dimension1=URL`·`dimension2=Device` → `pages`. 페이지는 동의한 방문자만이라 표본이 작아 72시간을 쓴다.
- 간격은 시도 사이다(성공·실패 모두 센다). 시도 시각은 그 호출을 보낸 때다 — 갱신을 시작한 때가 아니다(페이지는 기본 뒤에 나가므로 시작 시각을 적으면 다음 페이지가 12시간보다 일찍 나간다). 그래서 보낸 시각으로 어떤 24시간에도 기본 6 + 페이지 2 = 8회 이하라 사람 몫 2회가 남는다.
- 순서: 한 갱신 안에서 기본(때가 됐으면) → 페이지(때가 됐으면)를 하나씩 부른다 — Clarity 호출이 동시에 둘 나가지 않는다. 두 호출은 기록·state·값을 따로 갖는다. 페이지가 늦거나 실패해도 기본의 값·state·간격은 그대로다.
- 페이지를 미루는 때: 기본의 마지막 시도가 `denied`(401·403)이거나 `http_429` 면 페이지는 부르지 않고 시도로도 적지 않는다 — 토큰과 하루 한도는 둘이 같이 쓴다. 기본이 그 밖의 결과를 얻은 갱신에서 부른다.
- 기록 — Redis 문자열 JSON `{attemptAt, state, code, successAt, values}`(035 와 같은 모양, 시각 ms, 만료 없음). 기본 `admin:clarity` 의 값은 `numOfDays`·`traffic`·`summary`·`countries`·`metrics` 다 — 035 의 옛 기록(`summary`·`countries` 없음)은 그 키를 null 로 읽고 다음 성공이 덮는다. 페이지 `admin:clarity:pages` 의 값은 `numOfDays`·`rowsIn`·`rowLimitHit`·`groups` 이고 주소는 없다.
  - 요청마다 둘을 읽어 때를 정한다 — 그래서 api 재시작(배포)이 한도를 쓰지 않고, 키를 지우면 재시작 없이 바로 부른다. 값은 마지막 성공에서 7일이 지나면 버린다(요청 때 — 시도 시각·결과는 남긴다).
- core 공개 계약: `RedisBus.clarity_load() -> str | None`·`clarity_save(data: str) -> None`(035 그대로)과 `RedisBus.clarity_pages_load() -> str | None`·`clarity_pages_save(data: str) -> None`(같은 꼴, 키 `admin:clarity:pages`, 만료 없음).
- **한도 지키기 — Redis 기록과 어긋날 때**(api 는 uvicorn 워커 하나라 프로세스가 하나다):
  - 프로세스는 종류마다 마지막 기록을 메모리에도 두고, 그 기록을 Redis 에 썼는지 표시한다. 때는 Redis 기록과 메모리 기록 가운데 마지막 시도가 늦은 쪽으로 정한다. 메모리 쪽이 늦거나 아직 쓰이지 않았으면(부른 뒤 Redis 쓰기가 실패했다) Redis 에 다시 쓴다 — 035 의 '못 쓴 시각을 메모리에 둔다' 를 넓힌 것이다.
  - Redis 에 기록이 없거나 읽은 값이 JSON·모양이 틀리면 메모리 기록으로 때를 본다. 메모리에 간격 안 시도가 있고 그 기록이 Redis 에 쓰인 것이면(쓴 뒤 키가 사라졌다 — 사람이 지웠거나 잃었다: data 박스를 다시 만듦·FLUSH) 그 종류를 곧바로 부르되(바로 부르기) 이 프로세스에서 종류마다 24시간에 한 번뿐이다. 그 기록이 아직 쓰이지 않았거나 이미 24시간 안에 바로 불렀으면 메모리 기록을 Redis 에 다시 쓰고 간격을 따른다. 그래서 키를 몇 번 지우거나 잃어도 한 프로세스의 어떤 24시간에도 기본 7 + 페이지 3 = 10회를 넘지 않는다.
  - Redis 를 읽지 못하면 부르지 않는다 — 두 부분 `error`·`redis`, 값은 메모리의 마지막 기록(035 그대로).
  - 남는 위험: 기록을 잃은 채 api 가 다시 뜨면 메모리도 비어 둘을 곧바로 부른다(첫 설치와 같다). 그런 재시작이 하루에 여러 번이면 한도를 넘을 수 있다 — 그날 호출이 429 로 끝나고 간격대로 다시 부를 뿐이다. Redis 는 AOF 라 다시 떠도 기록이 남는다(db.md).

### 3.3 기본 응답 — 정규화
- `traffic` = `{sessions, botSessions, users, pagesPerSession}` — `traffic` 지표 첫 행(035 그대로).
- `summary` = `{scrollDepth, totalSec, activeSec, signals}` — 지표마다 첫 행에서 읽는다. `scrollDepth` ← `scrolldepth` 의 `averagescrolldepth`(%), `totalSec`·`activeSec` ← `engagementtime` 의 `totaltime`·`activetime`. `signals` 는 늘 키 여섯이다 — `deadClick` ← `deadclickcount`, `rageClick` ← `rageclickcount`, `excessiveScroll` ← `excessivescroll`, `quickback` ← `quickbackclick`, `scriptError` ← `scripterrorcount`, `errorClick` ← `errorclickcount`. 값 `{sessions, sessionPct, pageViews, count}` 는 그 지표의 `sessionscount`·`sessionswithmetricpercentage`·`pagesviews`·`subtotal` 이다.
- 수 읽기: 숫자나 숫자 글자를 받는다. 정수 칸(`sessions`·`pageViews`·`count`)은 정수일 때만, 실수 칸은 유한할 때만 쓰고 아니면 그 칸은 null 이다. 지표·행 키를 못 찾은 칸도 null 이고 객체 모양은 그대로다. `summary` 의 실수는 소수 둘째 자리까지 반올림한다.
- `countries` = `[[이름, 세션], …]` — 이름이 `country`·`countryregion` 인 지표의 행에서 만든다. 이름 = 행 키 `country`·`countryregion`·`name` 중 처음 있는 글자 값(셋 다 없으면 그 행에 글자 값이 하나뿐일 때 그 값), 세션 = 행 키 `sessionscount`·`totalsessioncount`·`sessions`·`count` 중 처음 있는 정수. 알아본 행만 세션 내림차순·같으면 이름순으로 20행, 이름은 Clarity 가 준 그대로 200자까지. 지표가 없거나 한 행도 못 알아보면 null, 행이 0개면 `[]`.
- `metrics` = 035 그대로 — `traffic` 밖 지표를 받은 이름·키 그대로 행 20개, 주소 줄이기 뒤. 정규화한 지표도 남긴다(세션이 있는 응답에서 짐작을 대조하려고).
- 주소 줄이기(035 §3.3 그대로 + 하나): 주소 꼴 값과 키는 쿼리·해시·사용자 정보를 떼고, 출처 지표는 출처(`스킴://호스트[:포트]`)로 줄인다. **더함**: 출처 지표 밖에서, 호스트가 `kimptrack.com`·`www.kimptrack.com`(대소문자 무관, 끝 점 무시, 스킴 없는 `호스트/…` 꼴 포함)이고 경로가 정확히 `/app/` 인 주소는 `?tab=<id>` 를 남길 수 있다. 쿼리의 첫 `tab` 값이 `spread`·`history`·`gap`·`pp`·`health`·`flow` 중 하나와 글자가 같을 때만이다. 다른 쿼리 키·해시는 늘 뗀다. 탭을 바꾸면 Clarity 가 새 페이지로 센다(033 — `tab` 쓰기만 Clarity 가 덮어쓴 `replaceState` 를 지난다). 탭마다 값이 갈리게 하려는 것이다.

### 3.4 페이지×기기 묶음 — `pages`
- 묶는 지표: `traffic`·`scrolldepth`·`engagementtime`·불만 신호 여섯. 그 밖 지표(인기 페이지·제목·출처·브라우저 등)는 행 수만 세고 버린다. 행의 주소 = 행 키 `url` 의 글자 값, 기기 = 행 키 `device` 의 글자 값이다. 둘 중 하나라도 없는 행은 묶지 않는다.
- 페이지 종류는 주소를 보고 메모리에서만 정한다 — 주소는 남기지 않는다. 호스트가 `kimptrack.com`·`www.kimptrack.com`(대소문자 무관·끝 점 무시·스킴 없는 꼴 포함)이거나 호스트 없이 `/` 로 시작하는 주소에서:

| 경로 | 종류 |
|---|---|
| `/` | `landing` |
| `/app/` | `app:<탭>` |
| `/privacy` | `privacy` |

- `app:<탭>` 은 쿼리 `tab` 이 없으면 `app:spread`(기본 탭 — 002 는 기본값이면 키를 지운다), 여섯 id 중 하나면 `app:<id>`, 그 밖(빈 값 포함)은 `app:other` 다. 다른 경로·다른 호스트·주소로 풀리지 않는 값은 `other` 다.
- 기기(바꾼 꼴로 비교): `mobile` → `mobile`, `tablet` → `tablet`, `pc`·`desktop` → `desktop`, 그 밖(`Email`·`Other`·빈 값) → `other`.
- 칸 = (페이지 종류, 기기). `sessions` = 그 칸 `traffic` 행의 `totalsessioncount` 합(정수, `traffic` 행이 없으면 null). `scrollDepth`(`averagescrolldepth`)·`totalSec`(`totaltime`)·`activeSec`(`activetime`)과 `deadClickPct`·`rageClickPct`·`excessiveScrollPct`·`quickbackPct`·`scriptErrorPct`·`errorClickPct`(신호 여섯의 `sessionswithmetricpercentage`) = 칸 안 행들의 가중 평균(소수 둘째 자리까지 반올림).
  - 행의 가중치 = 같은 (주소, 기기) 글자의 `traffic` 행 세션이다. 없으면 그 행의 `sessionscount`, 그것도 없으면 1. 값이 없는 행은 빼고, 가중치 합이 0 이면 null.
- `groups` = 값이 하나라도 있는 칸만, 종류 순서(`landing`·`app:spread`·`app:history`·`app:gap`·`app:pp`·`app:health`·`app:flow`·`app:other`·`privacy`·`other`) → 기기 순서(`mobile`·`tablet`·`desktop`·`other`). 그래서 40개 이하다. 칸 키는 `{page, device, sessions, scrollDepth, totalSec, activeSec, deadClickPct, rageClickPct, excessiveScrollPct, quickbackPct, scriptErrorPct, errorClickPct}`.
- `rowsIn` = 받은 모든 지표의 행 수 합. `rowLimitHit` = 어느 지표든 1,000행에 닿음 — 그 지표는 잘렸을 수 있다(주소에 다른 쿼리가 많이 실리면 닿는다).
- 행이 있는데 `url`·`device` 를 함께 가진 행이 하나도 없으면 `error`·`bad_data`(값은 마지막 성공) — 차원 키 철자가 짐작과 다르다는 뜻이다. 빈 목록 `[]`·행 0개는 성공이다(`groups` `[]`).

### 3.5 응답 — 값 키 (035 키는 이름·모양 그대로, 더하기만)
| 키 | 형 |
|---|---|
| `nextAt` | ms |
| `numOfDays` | 수 |
| `traffic` | 객체 |
| `summary` | 객체 |
| `countries` | 목록 |
| `metrics` | 목록 |
| `pages` | 객체 |

- 바깥 부분은 기본 호출 기준이다. `fetchedAt` = 마지막 성공, `refreshSec` 14400, `nextAt` = 마지막 시도 + 4시간(없으면 null), `numOfDays` 1. `state` = 기본의 마지막 시도 결과 — 401·403 → `denied`, 429 와 그 밖 200 아닌 상태 → `error`·`http_<상태>`, 시간 초과 → `timeout`. 본문이 JSON 이 아니거나, 목록이 아니거나, 비지 않았는데 `{metricName: 글자, information: 목록}` 이 하나도 없으면 `error`·`bad_data`(035 의 예외 이름 `ValueError` 등을 바꾼다).
- `pages` 는 늘 객체(하위 부분)다 — `{state, code, fetchedAt, refreshSec, nextAt, numOfDays, rowsIn, rowLimitHit, groups}`, `refreshSec` 43200, `nextAt` = 마지막 시도 + 12시간, `numOfDays` 3. state 는 바깥과 같은 규칙이고 셋이 더 있다: 토큰 없음 → `unconfigured`, 기록이 없고 부르는 중(기본 뒤를 기다림 포함) → `pending`, 기록이 없고 §3.2 로 미뤘다 → 기본의 `state`·`code`. 값 키는 마지막 성공이고, 없거나 7일 지나면 null.
- 토큰 없음 → 바깥 `unconfigured`·`code` null·값 키 null, `pages` 도 `unconfigured`. Redis 를 못 읽음 → 둘 다 `error`·`redis`(값은 메모리 기록).
- 036 화면과의 관계: 키는 더하기만이고 바뀌는 값은 `refreshSec`(10800 → 14400) 하나다. 화면은 간격을 `refreshSec` 로 적고("4시간 간격") 오래됨을 그 세 배로 보므로(12시간) 고칠 것이 없다. `pages`·`summary`·`countries` 는 아직 그리지 않는다(043).

### 3.6 부담
- 기본 응답은 수백 KB, 4시간에 한 번. 페이지 응답은 지표 16 × 1,000행이면 ≈3MB 다. 합성 자료로 풀기·묶기에 로컬 0.05초·tracemalloc 최고 11MB(2026-10-02 설계 세션 측정)이고, serve 는 ≈6배 느려 ≈0.3초다. 12시간에 한 번, 화면을 열 때만 일어난다. `to_thread` 라도 api 워커 하나의 GIL 을 공개 응답과 잠깐 나눠 쓴다(038·039 와 같은 받아들인 위험). `admin:clarity:pages` 는 칸 40개 이하라 수 KB 다.

### 3.7 엣지
- 배포 직후(035 기록만 있음): 기본은 035 의 마지막 시도 + 4시간에 부르고, 페이지는 기록이 없어 첫 요청에 기본 뒤로 부른다. 035 의 3시간 간격 시도와 겹친 24시간도 9회 이하다.
- 세션 0(동의한 방문자 없음): 받은 값 그대로(0 또는 null), `countries` `[]`, `groups` `[]`·`rowsIn` 0 — 성공이다.
- 기본 429·5xx·시간 초과: 기본 `error`, 값 유지, `nextAt` 4시간 뒤. 429 면 페이지도 미룬다(401·403 과 같다).
- 페이지만 실패: `pages` `error`·값 유지·`nextAt` 12시간 뒤, 바깥은 그대로.
- 토큰 교체: 새 토큰을 넣고 api 를 다시 만든다. 두 간격은 Redis 값이라 유지된다 — 바로 보려면 키 하나를 지운다(런북 6절, 하루 한 번).
- `rowLimitHit`: 묶음은 받은 행으로만 센다 — 화면(043)이 알린다.
- 기록을 잃음·지움, Redis 쓰기·읽기 실패: §3.2.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**. 시작 전에 main 에 fix/036-admin-followup(035 §3.3 '이름 비교')이 있는지 본다 — 없으면 멈추고 묻는다. 테스트는 네트워크를 쓰지 않는다.
- 가짜: Clarity 는 httpx MockTransport 다. 쿼리로 기본·페이지를 가르고, 부른 시각·인자·동시 호출 수를 적는다. 가짜 주입이 빠져 실제 transport 가 쓰이면 테스트가 실패하게 한다. Redis 는 fakeredis(쓰기만·읽기만 실패하게 바꿀 수 있게), 시계는 주입한다. 토큰은 테스트 글자다(`.env` 를 읽지 않는다).
- 가짜 응답은 035 의 `clarity_fakes.py` 를 넓힌다 — 실제 CamelCase 이름과 §3.1 의 행 키, 문서 철자(`Scroll Depth`·`Country/Region`·`Dead Click Count`), 세션이 있는 값(숫자·숫자 글자 섞음), 차원 행(`URL`·`Device` 키, `Url`·`device` 철자도).
- 일정·한도:
  - 첫 요청 → 기본 1회(`numOfDays=1`, 차원 인자 없음, Bearer) 뒤 페이지 1회(`numOfDays=3`·`dimension1=URL`·`dimension2=Device`), 동시 호출 수 최댓값 1.
  - 4시간 안 재요청 0회 / 4시간 뒤 기본만 / 12시간 뒤 기본 → 페이지 / 새로 만든 앱(재시작 흉내)도 두 간격을 지킨다.
  - 72시간 동안 60초마다 요청 → 어떤 24시간 창에도 기본 ≤ 6·페이지 ≤ 2.
  - 같은 72시간에 매시간 두 키를 지움 → 기본 ≤ 7·페이지 ≤ 3·합 ≤ 10. 지운 뒤 첫 요청은 곧바로 부르고, 24시간 안 두 번째 지움 뒤에는 부르지 않으며 Redis 에 기록이 다시 생긴다.
  - Redis 쓰기만 실패 → 간격 동안 다시 부르지 않음(빈 Redis 에서 첫 쓰기가 실패해도 두 번째 요청의 호출 0), 쓰기가 돌아오면 다음 요청에 메모리 기록이 Redis 에 생김 / Redis 읽기 실패 → 호출 0·두 부분 `error`·`redis` / 기록을 잃은 Redis + 새 앱 → 둘 다 곧바로(남는 위험을 테스트로 적어 둔다).
  - 기본 401 → 페이지 호출 0, `pages` 는 기록이 없으면 `denied`·`http_401` / 기본 429 → 페이지 0 / 기본 500·시간 초과 → 페이지는 부른다 / 기본이 다시 ok 인 갱신에서 미룬 페이지를 부른다.
  - 느린 가짜 기본(5초) → 요청이 3초 안에 답하고 바깥·`pages` 가 `pending`, 다음 요청에 둘 다 값.
  - 페이지 500 → `pages` `error`·직전 값·`nextAt` 12시간 뒤, 바깥 `ok` 그대로.
  - 토큰 없음 → Redis·Clarity 호출 0, 바깥·`pages` 모두 `unconfigured`.
  - 마지막 성공에서 7일 → 그 기록의 값만 null(두 기록이 따로), 시도 기록은 남음 / 035 모양 옛 `admin:clarity` → 200·`summary`·`countries` null·`traffic`·`metrics` 그대로.
- 정규화:
  - 실제 이름과 문서 이름이 같은 `summary` / 숫자 글자 → 수 / 지표 하나 빠짐 → 그 칸 null·`signals` 키 여섯 그대로 / 정수 칸의 `2.5`·`NaN`·`"abc"` → null / 본문 `{}`·`"x"`·JSON 아님 → `bad_data`·직전 값.
  - `countries`: 키 `Country`·`countryRegion`·`name` 각각 / 키를 모르는데 글자 값 하나 → 그 값, 글자 값 둘 → 그 행 버림 / 다 못 알아봄 → null / 행 0 → `[]` / 25행 → 20행·정렬.
  - `metrics` 에 정규화한 지표도 남음.
- 주소(`metrics`):
  - `https://kimptrack.com/app/?tab=history&h.dir=reverse&sym=ETH#x` → `https://kimptrack.com/app/?tab=history` / 대문자 스킴·`WWW.KimpTrack.com.`·`?sym=BTC&tab=gap` → `?tab=gap` 만.
  - `?tab=zzz`·`?tab=`·`?TAB=gap`·`?tab=Gap` → 쿼리 없음 / `https://evil.example/app/?tab=history`·`https://kimptrack.com/app/index.html?tab=pp`·`https://kimptrack.com/?tab=gap` → 쿼리 없음.
  - 스킴 없는 `kimptrack.com/app/?tab=flow&x=1` → `kimptrack.com/app/?tab=flow` / 출처 지표의 `https://kimptrack.com/app/?tab=history` → `https://kimptrack.com` / 035·036b 의 주소 줄이기·출처 테스트는 그대로 통과 — 단 가짜 응답의 대시보드 주소에 허용된 `tab=history` 가 든 두 행은 위 규칙대로 기대값이 `…/app/?tab=history` 로 바뀐다.
- 묶음:
  - 종류 표 전부 — www·대문자·끝 점, 경로만 `/app/?tab=gap` → `app:gap`, `/app/` 탭 없음 → `app:spread`, `?tab=` → `app:other`, `/privacy`, `/privacy-20261001.html`·`/app/index.html` → `other`, 다른 호스트 → `other`.
  - 기기 `Mobile`·`Tablet`·`PC`·`Email`·`Other`·빈 값.
  - 같은 칸 두 주소(세션 3·1, 깊이 40·80) → `sessions` 4·`scrollDepth` 50 / `traffic` 없는 주소는 `sessionsCount` 로, 그것도 없으면 단순 평균 / 가중치 합 0 → null.
  - 칸 순서·40개 이하 / `rowsIn`·`rowLimitHit`(한 지표 1,000행) / `url`·`device` 를 가진 행 없음 → `error`·`bad_data`·직전 값 / `Url`·`DEVICE` 철자 → 묶임.
  - 주소를 남기지 않음: 페이지 호출 가짜에만 표시 글자를 든 주소(`/app/?tab=gap&sym=PGMARK`·`/privacy?x=PGMARK`)를 쓴다 — `admin:clarity:pages` 값과 응답 `pages` 의 바이트에 `PGMARK`·`kimptrack.com`·`/app/` 이 없고, 응답 전체에 `PGMARK` 가 없다(`metrics` 는 기본 호출의 줄인 주소를 정당하게 싣는다).
- 새지 않음: 토큰 글자가 `caplog`·두 Redis 값·응답 바이트에 없다 / WARNING 은 부분 이름(`clarity`·`clarity.pages`)과 `code` 만, ERROR 0.
- 성능 — pytest 밖 임시 스크립트로 잰다(레포에 넣지 않는다), 숫자는 §5. 합성 페이지 응답(지표 16 × 1,000행, 주소마다 다른 쿼리 — ≈3MB)의 풀기·묶기가 로컬 ≤ 0.2초(serve 환산 ×6 을 함께 적는다), tracemalloc 최고 ≤ 32MB. 넘으면 멈추고 묻는다.
- 화면·nginx: `web/admin/*`·`web/nginx-admin.conf` 는 바꾸지 않는다. `server/tests/test_admin.py` 의 기존 단언은 고치지 않고 통과한다 — 화면 파일 셋(`index.html`·`admin.js`·`admin.css`), `fetch(` 1회와 느린 묶음 경로 넷(`/svc/api/admin/clarity` 포함), `SCRIPT_BANNED`, `SVG_ATTRS` 허용 목록과 `svg()` 요소, 외부 링크 `EXTERNAL_HOSTS`, nginx 정확 일치 위치. 더하는 단언 하나: `admin.js` 에 Clarity 주기 숫자(`10800`·`10_800`·`14400`·`14_400`·`43200`·`43_200`)가 없다 — 화면은 `refreshSec` 로 적는다(036 §3.3).
- 035 회귀: 035 의 Clarity 테스트 중 이 스펙이 바꾼 것(3시간 → 4시간, 모양 틀림 `code` → `bad_data`, `refreshSec`, 더한 값 키와 `pages`, 묶음 호출이 더해져 호출 수는 기본만 세기·HTTP 테스트의 호출 2회·묶음 기록 자리)만 이 계약으로 고친다. `tests/test_clarity_store.py` 에 새 메서드 둘(키 이름·만료 없음)을 더한다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`. 커밋은 각 diff 300줄 이하 — ① core 메서드 ② 일정·기록·한도 ③ 정규화·주소 ④ 페이지 묶음 ⑤ 테스트(나눠서) ⑥ §6 문서·§5·§7.

**배포 뒤 — 운영 확인(완료 조건 아님, status 비고 "040 운영 확인 대기")**:
- serve 에서 `docker exec marketlens-caddy wget -qO- http://web:8081/svc/api/admin/clarity` → `refreshSec` 14400·`pages.refreshSec` 43200, `pages.state` `ok`(첫 요청은 `pending` — 60초 뒤 다시).
- 동의한 세션이 생긴 뒤 처음 본 응답에서 §3.1 '확인 못 한 것' 을 §7 에 적는다. 값은 옮기지 않고 키 이름과 꼴만 적는다.
  - `pages` 가 `ok` 이고 `groups` 가 차면 차원 키가 맞다. `bad_data` 면 다르다 — §3.4 를 고치는 후속이다.
  - `metrics` 의 값 꼴(정수·실수·글자), `Country` 행 키(`countries` 가 null 이면 다르다), 대시보드 주소에 `?tab=` 이 실리는지(`PopularPages`).
  - `totalTime`·`activeTime` 의 단위와 세션당 평균인지 합인지는 사람이 Clarity 대시보드의 같은 날 값과 대조한다 — 합이면 §3.4 묶기(가중 평균)와 043 의 '머문 시간' 표기를 고치는 후속이다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 시작 조건 — 스펙 PR #96(docs/specs-037-040) 위에 후속 PR #95(fix/036-admin-followup)를 합친 4db0c04.
# 둘 다 main 머지 전이지만 설계 세션이 의존 충족으로 정했다. 035 §3.3 '이름 비교'(metric_key)·ReferrerUrl 출처 줄이기(is_referrer)가 있다
git log --oneline -1   # 4db0c04

# 환경 — 샌드박스가 ~/.cache/uv 쓰기를 막아 UV_CACHE_DIR 를 scratchpad 에 두었다
cd server && uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[dev]" && git checkout server/marketlens_server.egg-info
cd web && npm ci

# 기존 스펙 재검증 (마지막 코드 커밋 뒤)
cd server && ruff check . && ruff format --check . && pytest -q
#   All checks passed! · 295 files already formatted · 1332 passed, 6 failed
#   — 실패 6건은 모두 app/features/spreads/tests/test_gauge.py 의 UDP bind PermissionError(샌드박스). 그 6건을 뺀 1332 통과
cd web && npm run lint && npm run build   # oxlint 종료 0(출력 없음) · ✓ built — index 253.01 kB(gzip 78.58 kB)
pytest -q app/features/admin tests/test_admin.py tests/test_clarity_store.py tests/test_role.py   # 248 passed
pytest -q app/features/admin/tests/test_clarity_{values,schedule,defer,pages,leaks}.py tests/test_clarity_store.py   # 90 passed (040 새 테스트)

# 가짜 주입이 빠지면 실패하는지 — 임시 테스트(커밋 안 함)에서 transport 없이 VisitFeeds 를 부르면
#   conftest 의 막기가 "가짜 transport 없이 실제 transport 를 썼다: ['www.clarity.ms', 'www.clarity.ms']" 로 실패시켰다

# 성능 — pytest 밖 임시 스크립트(scratchpad/040/perf_pages.py, 레포에 넣지 않음).
# 합성 묶음 응답 2.99MB(지표 16 × 1,000행, 주소마다 다른 쿼리, 기기 넷 무작위), parse_pages + check_json, 9회
.venv/bin/python perf_pages.py
#   첫 실행 16.5 ms · 중앙값 15.7 ms(최소 15.5·최대 19.0) — serve ×6 ≈ 94 ms (상한 0.2초)
#   tracemalloc 최고 10.9 MB (상한 32MB) · 칸 20개 · rowsIn 16000 · rowLimitHit true · 기록 5.4 KB
#   (같은 스크립트를 기기와 주소 종류가 겹친 첫 판으로 돌렸을 때 중앙값 23.5 ms·10.9 MB)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md`:
  - admin 행 server 칸의 "`/admin/clarity`(3시간 간격·Redis 캐시·주소 쿼리 뗌·7일 뒤 버림)" → "`/admin/clarity`(기본 4시간·페이지×기기 12시간(직전 72시간)을 따로 — 040, Redis 기록 둘·주소 쿼리 뗌(대시보드는 탭 id 만)·정규화 summary·countries·pages.groups·7일 뒤 버림)". 비고 끝에 "· 040 운영 확인 대기(세션이 있는 응답의 차원 키·값 꼴)".
  - 알려진 빚 `(035) Clarity 응답의 Traffic 밖 행 모양은 1차 문서에 없다 …` 줄 → `(040) Clarity 정규화는 세션 0 첫 응답(2026-10-02)의 키와 문서에 기댄다 — 값 꼴·차원 행 키·Country 행 키·totalTime 단위(초 짐작)와 세션당 평균인지 합인지·스크롤 깊이 정의는 세션이 있는 응답 뒤 040 §7 에서 확인. 못 알아본 칸은 null 이고 받은 그대로(metrics)를 남긴다`.
  - `(036) Clarity 칸은 traffic 타일과 받은 지표 목록뿐 — 정규화 타일은 …` 줄 → `(036) Clarity 칸은 traffic 타일과 받은 지표 목록뿐 — 정규화 값(summary·countries·pages)은 040 응답에 있고 그리는 것은 043`.
  - `(035) Clarity 값의 7일 버림은 요청 때 한다 …` 줄의 "Redis `admin:clarity` 에" → "Redis `admin:clarity`·`admin:clarity:pages` 에".
  - 더함: `(040) Redis 의 Clarity 기록을 잃은 채 api 가 하루에 여러 번 다시 뜨면 하루 한도(10회)를 넘을 수 있다 — 그날 호출만 429 로 끝난다(받아들인 위험)`.
- `CLAUDE.md` — 스펙 인덱스 040 행 상태 → DONE. 035 행 범위의 "`/admin/clarity`(Data Export 3시간 간격·Redis 캐시·주소 쿼리 뗌)" → "`/admin/clarity`(Data Export·Redis 캐시 — 간격·정규화·페이지×기기 묶음은 040)". 실행 세션은 인덱스의 상태만 고치지만(CLAUDE.md §5), 040 뒤 035 행 범위가 거짓이 되므로 설계 세션이 허락한 예외다.
- `docs/context/architecture.md`:
  - '핵심 설계 결정' 의 Redis 문장 괄호 "035 의 `/admin/clarity` 는 `admin:clarity` 읽기·쓰기" → "035·040 의 `/admin/clarity` 는 `admin:clarity`·`admin:clarity:pages` 읽기·쓰기".
  - '현재 구조' admin 항목의 035 Clarity 설명("Clarity 는 주기 0 `Slot`(…) 안에서 요청마다 `admin:clarity` 를 읽어 마지막 시도 3시간 뒤에만 부르고, Redis 에 못 쓴 시도는 메모리 시각으로 3시간 막는다")과 `clarity.py` 설명을 040 으로 바꾼다. 담을 것: 기본 4시간·페이지 12시간 일정(한 갱신 안 기본 → 페이지, 기록 둘, 메모리 기록·바로 부르기 24시간 한 번), 정규화·대시보드 `tab` 남기기, 페이지×기기 묶음 모듈(실제 이름으로), core `RedisBus.clarity_pages_load`·`clarity_pages_save`, 테스트 파일.
- `docs/context/dev-setup.md`:
  - env 설명 `CLARITY_API_TOKEN` 의 "마지막 시도에서 3시간이 지났으면 한 번 부른다(프로젝트당 하루 10회 한도 — 시도 기록은 Redis `admin:clarity`)" → "기본 요약은 마지막 시도에서 4시간, 페이지×기기 묶음은 12시간이 지났으면 한 번씩 부른다(프로젝트당 하루 10회 한도 — 시도 기록은 Redis `admin:clarity`·`admin:clarity:pages`, 040)".
  - '검증용 스모크' 035 문단의 "`/admin/clarity` 는 토큰이 없으면 `unconfigured`·`code: null` 이고" 뒤에 "(`pages` 도 `unconfigured`, `refreshSec` 14400)".
- `docs/context/db.md`:
  - Redis 절 `admin:clarity` 줄: "(`numOfDays`·`traffic`·`metrics`, 주소는 쿼리·해시를 뗀 뒤)" → "(`numOfDays`·`traffic`·`summary`·`countries`·`metrics`, 주소는 쿼리·해시를 뗀 뒤 — 대시보드 주소는 `?tab=<id>` 만, 040)". "마지막 시도에서 3시간이 지났을 때만" → "4시간이 지났을 때만". "지우면 다음 요청이 바로 부른다(런북 `clarity.md`)" → "지우면 다음 요청이 바로 부른다 — 프로세스마다 24시간에 한 번, 그 밖에는 api 가 메모리 기록으로 다시 쓴다(040, 런북 `clarity.md`)".
  - 그 아래 새 줄: "키 **`admin:clarity:pages`**(040) — 같은 모양의 문자열 JSON, 값은 `numOfDays`(3)·`rowsIn`·`rowLimitHit`·`groups`(페이지 종류×기기 칸 40개 이하 — 주소 없음), 만료 없음. 쓰는 쪽·읽는 쪽 모두 api 의 `/admin/clarity`(마지막 시도에서 12시간 — 기본의 마지막 시도가 401·403·429 면 미룬다). 7일 버림·지우기는 `admin:clarity` 와 같다."
  - 읽고 쓰는 쪽 문단의 "`admin:clarity` 는 api 의 `/admin/clarity` 만 읽고 쓴다(035)" → "`admin:clarity`·`admin:clarity:pages` 는 api 의 `/admin/clarity` 만 읽고 쓴다(035·040)".
- `docs/runbooks/clarity.md`:
  - 제목의 "(스펙 033·035, 사람용)" → "(스펙 033·035·040, 사람용)".
  - 5절 5단계 "옛 기록을 지운다(6절 '교체'·'바로 부르기')" → "옛 집계를 지운다 — `DEL admin:clarity admin:clarity:pages`(api 를 다시 만든 뒤라 둘 다 곧바로 부른다 — 그날 한도를 2회 더 쓴다)".
  - 6절 첫 문단 "(`project-live-insights`, 최근 24시간)" → "(`project-live-insights` — 기본 요약은 부른 때 직전 24시간, 페이지×기기 묶음은 직전 72시간)".
  - '한도' 줄 → "프로젝트당 하루 10회(넘으면 429). api 는 기본 호출을 4시간, 페이지×기기 호출을 12시간 띄운다(성공·실패 모두 센다 — 어떤 24시간에도 6 + 2 = 8회라 사람 몫 2회가 남는다). 둘이 같이 때가 되면 기본 먼저, 기본이 401·403·429 면 페이지는 미룬다. 마지막 시도·결과·마지막 성공 값은 Redis `admin:clarity`(기본)·`admin:clarity:pages`(페이지 — 주소 없이 묶은 수만)에 있어 api 를 다시 띄워도(배포) 한도를 쓰지 않는다. 관리자 페이지를 열 때만 부른다."
  - 5단계 '확인' 끝에 "`pages.state` 도 `ok` 인지 본다(첫 요청은 `pending` — 60초 뒤). 동의한 세션이 생긴 뒤 처음 본 응답으로 040 §4 '배포 뒤' 확인을 한다."
  - '상태 읽기' 끝에 "`pages` 는 따로 읽는다 — 기본과 같은 `denied`·`http_429` 면 기본 때문에 미룬 것이고, `error`·`bad_data` 면 차원 키가 040 의 짐작과 달라 묶지 못했다(040 을 고친다)."
  - '바로 부르기' 줄 → "(토큰을 바꿨거나 간격을 기다리지 않을 때) 키 **하나만**, **하루 한 번** 지운다 — 기본 요약은 data 박스에서 `docker exec marketlens-redis redis-cli DEL admin:clarity`, 페이지×기기 묶음은 `… DEL admin:clarity:pages`. 다음 관리자 페이지 요청이 곧바로 부른다(하루 10회 중 1회). api 는 키마다 24시간에 한 번만 곧바로 부르고, 그 안에 다시 지우면 기록을 되살리고 간격을 따른다 — 둘 다 지우면 사람 몫 2회를 다 쓴다."
  - '교체' 끝 "3시간 간격은 Redis 값이라 교체해도 유지된다" → "두 간격은 Redis 값이라 교체해도 유지된다".
  - '끄기' 의 "남은 집계(최대 7일치 숫자와 쿼리를 뗀 페이지 주소·출처)는 data 박스에서 `DEL admin:clarity` 로 지운다" → "남은 집계(최대 7일치 숫자와 쿼리를 뗀 페이지 주소·출처, 페이지 종류×기기 묶음)는 data 박스에서 `DEL admin:clarity admin:clarity:pages` 로 지운다".
- `docs/specs/035-monitoring-visits.md`:
  - §2 처리방침 줄 "Clarity 에서는 집계 숫자와 쿼리를 뗀 페이지 주소·출처만 받아 7일 뒤 버린다(§3.3)" → "Clarity 에서는 집계 숫자(페이지 종류×기기 묶음 포함)와 쿼리를 뗀 페이지 주소(대시보드 주소는 허용된 탭 id 하나만 남긴다)·출처만 받아 7일 뒤 버린다(§3.3·040)".
  - §3.1 주기 표 `clarity` 10800 → 14400, 행 `clarity.pages` 43200 을 더한다. 표 아래 문장 끝에 "Clarity 두 호출의 간격과 한도 셈은 040 §3.2".
  - §3.3: 외부 계약의 "차원 셋까지(쓰지 않는다)" → "차원 셋까지(040 의 페이지 호출이 `URL`·`Device` 둘을 쓴다)". '호출 규칙' 줄 → "호출 규칙·기록·한도 지키기는 040 §3.2(기본 4시간·페이지×기기 12시간, Redis `admin:clarity`·`admin:clarity:pages`). core 공개 계약 `RedisBus.clarity_load() -> str | None`·`clarity_save(data: str) -> None` 은 그대로이고 040 이 페이지 기록 메서드 둘을 더한다." 주소 줄이기 줄 끝에 "대시보드 주소의 `tab` 남기기는 040 §3.3". '응답(부분 하나)' 줄과 그 아래 두 줄 → "응답(값 키·state·정규화·`pages`)은 040 §3.3~§3.5". 토큰 절차 줄의 "바로 부르기(`admin:clarity` 지우기)" → "바로 부르기(키 하나 지우기·하루 한 번 — 040)".
  - §3.4 끝 문장 "Clarity 응답은 1,000행 상한이라 수백 KB, 3시간에 한 번." → "Clarity 의 부담은 040 §3.6."
  - §3.5 의 Clarity 엣지 — "Redis 불달" 줄의 Clarity 문장, "Clarity 토큰 교체", "Clarity 429", "Clarity 200 인데 …" — 를 "Clarity 의 엣지는 040 §3.7" 한 줄로(“접속 요약은 그대로” 는 남긴다).
  - §4 'Clarity:' 줄 → "Clarity 는 040 §4". §5·§7 기록은 그대로 둔다.
- `docs/specs/036-admin-v2.md`:
  - §3.2 느린 묶음 표의 `/svc/api/admin/clarity` 행 `035` → `040`.
  - Clarity 피드 복사 줄 → "`/svc/api/admin/clarity` = 부분 하나(14400초, 040) + `nextAt`·`numOfDays`(1)·`traffic{sessions, botSessions, users, pagesPerSession}`·`summary{scrollDepth, totalSec, activeSec, signals}`(signals 키 여섯 `deadClick`·`rageClick`·`excessiveScroll`·`quickback`·`scriptError`·`errorClick`, 값 `{sessions, sessionPct, pageViews, count}`)·`countries[[이름, 세션]]`(20행, 못 알아보면 null)·`metrics[{name, rows}]`(받은 이름·키 그대로, 행 20개, 주소는 쿼리를 떼되 대시보드 주소는 `?tab=<id>` 만)·`pages`(하위 부분 `{state, code, fetchedAt, refreshSec 43200, nextAt, numOfDays 3, rowsIn, rowLimitHit, groups}` — `groups[{page, device, sessions, scrollDepth, totalSec, activeSec, deadClickPct, rageClickPct, excessiveScrollPct, quickbackPct, scriptErrorPct, errorClickPct}]` 40개 이하, 주소 없음). 못 알아본 칸은 null. 창은 부른 때 직전 24시간(기본)·72시간(`pages`)이고 시간대가 없다(Clarity 문서는 결과를 UTC 로 적는다)."
  - §3.4 Clarity 끝 문장 "스크롤 깊이·참여 시간·dead·rage click 같은 정규화 타일은 …" → "정규화 값(`summary`·`countries`·`pages`)은 040 응답에 있다 — 그리는 것은 043."

038(접속 요약 v2)과 나란히 고치는 곳 — 035 §2 처리방침 줄·§3.1 주기 표 아래 문장·§3.4 serve 부담 문장·§3.5·§4, 036 §3.2 느린 묶음 표, CLAUDE.md 035 행 — 은 권장 머지 순서(040 → 038)대로 늦게 머지하는 쪽이 main 을 받아 두 고침을 합친다. 036 §3.4 의 Clarity 줄은 041(먼저 머지 — '타일 넷은 부제 한 줄(041)')·043(뒤에 줄을 통째로 바꾼다)도 고친다 — 이 PR 은 041 의 글을 남기고 끝 문장만 바꾼다.

**담당자에게 제안**: 없다 — 경로·nginx·compose 가 그대로라 016·018·021 의 문장이 맞다.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - core: `server/app/core/redis_bus.py` — 키 `admin:clarity:pages`, `clarity_pages_load`·`clarity_pages_save`(만료 없음).
  - `server/app/features/admin/clarity.py` — 호출 하나(`fetch` 가 종류별 인자·값 만들기를 받는다, 모양 틀림 → `bad_data`)·기록 `Record`·이름 비교 `metric_key`(결과 4,096개 기억)·수 읽기(`read_number`·`read_int`)·행 키 고르기 `pick`·지표 목록 `metric_items`·주소 줄이기(대시보드 `tab` 남기기 — `is_ours`·`first_tab`·`split_path`, 묶음 판정용 `address_parts`).
  - 새 파일: `clarity_values.py`(`parse_base` — `traffic`·`summary`·`countries`·`metrics`), `clarity_pages.py`(`parse_pages`·`page_kind`·`device_kind` — 칸 40개 이하, 주소는 메모리에서만), `clarity_schedule.py`(`ClarityKind` — 종류마다 인자·Redis 자리·프로세스 기억, `settle` 이 §3.2 의 때 정하기·다시 쓰기·바로 부르기 24시간 한 번).
  - `visits.py` — 한 갱신 안에서 기본 → 묶음, 미루기, 하위 부분 `pages`, 부분마다 JSON 으로 쓸 수 있는지 따로.
  - 테스트: `features/admin/tests/conftest.py`(실제 transport 를 쓰면 실패), `clarity_fakes.py`(세션이 있는 기본·묶음 응답, 종류별 응답 줄·부른 시각·동시 호출 수, 읽기·쓰기를 따로 깨는 `FlakyBus`), `test_clarity_values.py`·`test_clarity_schedule.py`·`test_clarity_defer.py`·`test_clarity_pages.py`·`test_clarity_leaks.py`, `server/tests/test_clarity_store.py`(새 메서드 둘)·`server/tests/test_admin.py`(admin.js 에 Clarity 주기 숫자 없음). 035 테스트 고침: `test_clarity_feed.py`·`test_clarity_safety.py`·`test_visits.py`.
  - 문서: `CLAUDE.md`(040 DONE·035 범위), `docs/context/{status,architecture,dev-setup,db}.md`, `docs/runbooks/clarity.md`, `docs/specs/035-monitoring-visits.md`·`036-admin-v2.md`·이 문서. web·nginx·compose·env 는 바꾸지 않았다. Clarity 실서비스는 부르지 않았다(테스트는 가짜).
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - `countries` 의 "글자 값이 하나뿐" 에서 숫자로 읽히는 글자(`"5"`)는 수로 보아 세지 않는다(§3.3 '수 읽기' 와 같은 기준) — 세면 세션 칸이 글자라서 대부분 행이 '글자 값 둘' 로 버려진다. 아는 이름 키가 있어도 값이 글자가 아니면 다음 키·이 규칙으로 넘어간다.
  - 같은 정규화 이름의 지표가 둘이면 앞의 것을 쓴다(기본·묶음 모두). `countries` 는 `country`·`countryregion` 중 응답에서 먼저 온 지표 하나. 행 키 비교(`pick`)도 정규화한 꼴이고 같은 이름이 둘이면 앞의 것 — Traffic 행도 이 비교를 쓴다(035 는 정확한 키였다, 같은 '이름 비교' 규칙).
  - 대시보드 `tab` 남기기: 호스트 비교는 사용자 정보·포트를 뗀 뒤(`https://u:p@kimptrack.com:443/app/?tab=health` → `https://kimptrack.com:443/app/?tab=health`), 해시 뒤의 `?` 는 쿼리가 아니다, `tab` 이 둘이면 첫 값만 본다. 호스트 없는 `/app/?tab=gap` 은 `metrics` 에서 탭을 남기지 않는다(§3.3 은 우리 호스트의 주소만 말한다 — §3.4 종류 판정은 호스트 없는 꼴도 받는다).
  - 스킴 없는 주소 꼴의 호스트 끝 점 하나를 허용했다(`www.kimptrack.com./app/…`) — 035 의 꼴 판정은 이것을 주소로 못 알아봐 쿼리째 200자 글자로 남겼다. 닫힌 쪽(더 많이 뗀다)으로 넓힘.
  - 페이지 종류: 호스트 뒤 빈 경로(`https://kimptrack.com`·`kimptrack.com?x`)는 `/` 로 보아 `landing`. `?TAB=gap` 은 키가 글자 그대로 `tab` 이 아니므로 '탭 없음' → `app:spread`(§3.3 의 키 비교와 같게).
  - 묶음 칸 `sessions` 는 Traffic 행의 `totalsessioncount` 중 정수로 읽히는 것만 더한다(하나도 없으면 null). 같은 (주소, 기기) 글자의 Traffic 행이 둘이면 그 합을 가중치로. `rowsIn` 은 `{metricName: 글자, information: 목록}` 꼴 지표의 행만 세고, `url`·`device` 를 함께 가진 행 찾기는 묶지 않는 지표까지 모든 행에서 본다.
  - Redis 읽기 실패·기록 풀기 예외는 원인이 하나라 WARNING 을 `clarity` 한 줄로만 남긴다(두 부분 모두 `error`) — 035 테스트의 '한 줄' 단언 그대로. 묶음 호출·묶음 기록 쓰기·묶음 처리 예외는 `clarity.pages`.
  - 묶음이 때가 됐는데 미뤄진 갱신에서는 메모리 기록을 Redis 에 다시 쓰지 않는다 — 사람이 지운 키를 되살려 '바로 부르기' 를 잃지 않게. 간격 안이라 부르지 않을 때만 다시 쓴다. 바로 부르기 시각은 실제로 부른 때만 적는다.
  - `pending`: 갱신 하나가 3초 안에 안 끝나면(기본이 끝나고 묶음을 기다리는 중 포함) 직전 결과, 없으면 바깥·`pages` 둘 다 `pending` — 갱신 중간 값을 따로 내지 않는다(§3.1 갱신 규칙 그대로).
  - `bad_data` 로 바꾸는 예외는 풀기·만들기의 ValueError(JSON 아님·UTF-8 아님·다시 쓸 수 없는 값 포함)·TypeError·RecursionError, 그 밖 예외는 035 처럼 예외 이름.
  - JSON 으로 못 쓰는 값이 든 답(손으로 넣은 기록 등): `pages` 만 못 쓰면 `pages` 만 `error`·예외 이름(값 null), 바깥이 못 쓰면 바깥 값 키만 null 이고 `pages` 는 그대로 객체.
  - 테스트의 '느린 가짜 기본(5초)' 은 실제 0.5초 지연 + 기다림 0.1초로 흉내 냈다(같은 비율 — 테스트 시간). '72시간 60초마다'·'매시간 두 키 지움' 은 4,320·4,200 요청을 그대로 돈다(약 5초).
- 실행 중 함께 고친 스펙 절: §4 '주소(`metrics`)' 의 "035·036b … 그대로 통과" 에 `tab=history` 가 든 두 행의 기대값이 §3.3 규칙대로 바뀐다는 말을 더했다(035 `test_addresses_lose_query_and_hash_and_referrers_become_origins`·036b `test_named_metrics_match_in_real_camel_case_and_documented_spelling` 의 대시보드 행 — 나머지 단언은 그대로). §4 '035 회귀' 에 묶음 호출이 더해져 바뀐 것(호출 수는 기본만 세기·HTTP 테스트의 호출 2회·`test_http_unwritable_record_in_redis_is_an_error_part_not_500` 에 12시간 안 묶음 기록·`SaveFails` 가짜에 묶음 메서드·응답 dict 에 더한 키)을 더했다.
- 남은 빚:
  - §4 '배포 뒤' 확인 전부 — 세션이 있는 응답의 차원 행 키 철자·값 꼴·`Country` 행 키·`totalTime` 단위와 평균/합·스크롤 깊이 정의·`PopularPages` 주소에 `?tab` 이 실리는지(status "040 운영 확인 대기").
  - 미뤄진 묶음에 기록이 있으면 그 기록의 state(예: `ok`)와 지난 `nextAt` 이 그대로 보인다 — '미뤘다' 표시는 기록이 없을 때만(§3.5 그대로). 화면(043)이 `nextAt` 이 지났는데 바깥이 `denied`·`http_429` 인 것으로 읽어야 한다.
  - 035 §3.1 '스레드' 줄은 JSON 풀기 대상으로 `admin:clarity` 만 적는다(§6 목록 밖이라 두었다 — 묶음 기록도 같은 스레드에서 푼다).
  - 샌드박스라 Docker·로컬 포트 검증은 하지 않았다(경로·nginx·compose 변경 없음 — §4 가 요구하지 않는다). `test_gauge.py` 6건은 UDP bind 가 막혀 실패한다(샌드박스, 이 변경과 무관).
