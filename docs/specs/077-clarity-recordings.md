# 077 — clarity-recordings

상태: DONE | 의존: 040·068(Clarity 토큰·주소 줄이기·부분 상태 꼴)·066(관리자 Clarity 페이지·상세 판)·071('?' 두 줄)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-16): Clarity 의 녹화(recordings — 실제 화면에서 마우스를 어떻게 움직였는지)를 관리자 화면에서도 본다. Clarity 는 녹화 영상을 내보내지 않지만, 공식 Clarity MCP 서버가 쓰는 녹화 목록 경로는 녹화마다 **재생 링크·길이·페이지별 시간선·클릭한 곳**을 준다(2026-10-16 운영 토큰으로 5개 받아 확인). 관리자 Clarity 페이지에 그 목록과 시간선을 두고, 재생은 링크로 Clarity 에서 한다.

## 2. 범위
- 만드는 것: api `GET /admin/clarity/recordings?days=`(관리자 nginx 정확 일치 하나), 관리자 Clarity 페이지 카드 '녹화' 와 상세 판(`#detail=rec:<번호>`), 가짜 피드 응답, 테스트.
- 하지 않는 것: 녹화 영상 받기·우리 화면에서 재생(Clarity 가 주지 않는다), 녹화를 IP·우리 방문자와 잇기(Clarity 가 IP 를 주지 않는다 — 073·074 의 우리 화면 재생이 그 몫), Redis·파일 저장(메모리 30분 캐시만), 새 수집(방문자 쪽은 그대로 — Clarity 가 이미 모은 것을 운영자가 보는 길만 바꾼다).

## 3. 동작

### 3.1 바깥 계약 — Clarity 녹화 목록(공식 MCP 서버 `microsoft/clarity-mcp-server` 소스, Microsoft Learn 문서에는 없음)
- `POST https://clarity.microsoft.com/mcp/recordings/sample`, 헤더 `Authorization: Bearer <CLARITY_API_TOKEN>`(040 과 같은 Data Export 토큰)·`Content-Type: application/json`, 몸통 `{"sortBy":0,"start":<UTC ISO ms>,"end":<UTC ISO ms>,"filters":{"date":{"start":…,"end":…}},"count":100}` — `sortBy` 0 = 시작 최근 순, `count` 최대 250.
- 응답(2026-10-16 실측) = 녹화 목록. 녹화 = `link`(Clarity 재생 주소)·`timestamp`(`YYYY-MM-DD HH:MM:SS`, UTC 로 읽는다)·`totalDuration`·`activeDuration`(`52 seconds` 꼴 글자)·`pagesCount`·`sessionClickCount`·`timeline`(페이지마다 `url`·`displayTitle`·`duration` ms·`start` `mm:ss`·`pageLoadTime` ms·`referrerUrl`·`timelineEvents`[`eventtype`·`text`·`start` `mm:ss`…])·그 밖. 공식 문서가 아니라 언제든 바뀔 수 있다 — 모르는 키는 버리고 틀린 값은 null 로.
- 하루 호출 한도는 문서가 없다 → 같은 창은 30분에 한 번까지만 부른다(§3.2).

### 3.2 `GET /admin/clarity/recordings?days=` (api — 관리자 nginx `= /svc/api/admin/clarity/recordings`)
- `days` 1·3·7·30(목록 밖·없음은 3). 창 = 지금 − days … 지금(UTC). 창마다 캐시 한 칸 — 30분, 요청이 있을 때만 부르고(관리자 Clarity 페이지를 열 때), 부르는 동안 3초 기다린 뒤 늦으면 직전 결과(없으면 pending)(034 의 칸 규칙). 실패도 그 칸에 30분 남는다 — 한도를 모르는 경로를 거듭 부르지 않게.
- 응답 = 부분 상태 `{state, code, fetchedAt, refreshSec, days, rows}`. 토큰이 없으면 `unconfigured`. 401·403 → `denied`, 그 밖 비 200·시간 초과(10초)·JSON 아님 → `error`(code `http_<n>`·`timeout`·`bad_data`). 토큰은 헤더에만 — 로그·응답·예외 code 에 없다. 오류 문장은 버리고 WARNING 은 10분에 1줄(034).
- `rows` = 시작 최근 순 100개까지. 행 = `{startedAt(ms|null), totalSec, activeSec, pages, clicks, link, timeline}`:
  - 길이 글자는 `N hour(s) N minute(s) N second(s)` 의 조합을 초로(모르는 꼴 null).
  - `link` 는 `https://clarity.microsoft.com/` 로 시작하고 영문·숫자·`/_.~%-`·쿼리 `?…`(영문·숫자·`=&_.~%:-`)만이며 500자 이하일 때만, 아니면 null — 피드 글자가 그대로 주소가 되지 않게.
  - `timeline` = 페이지 30개까지 `{url, title, atSec, stayMs, loadMs, referrer, events}` — `url` 은 040 의 주소 줄이기(쿼리·해시 뗌, 우리 대시보드는 `?tab=<id>` 만), `referrer` 는 출처만, `title` 100자, `events` 50개까지 `{type(30자), text(60자 — 클릭한 글자, 제어 문자 뺌), atSec}`.

### 3.3 관리자 Clarity 페이지 카드 '녹화'
- 카드 머리 `녹화` · 단위 `Clarity · 최근 순 100` · 탭 `1일 3일 7일 30일`(기본 3일 — 주소에 싣지 않는다) · '?' 는 071 칸 넷 — 동의한 방문마다 Clarity 가 남긴 녹화 목록이고 재생은 링크로 Clarity 에서(로그인 필요), 행을 누르면 페이지·클릭 시간선, IP 는 Clarity 가 주지 않아 방문자 페이지와 잇지 못한다는 것을 담는다.
- 표(최근 10행): 시작(KST, 초까지) · 길이(활성 / 전체) · 페이지 · 클릭 · 지나간 페이지(이름 — 랜딩·대시보드 탭·처리방침·검색어 페이지, 그 밖은 줄인 주소, `→` 로 이음) · `재생 ↗`(링크가 있을 때만 — 같은 탭, `rel=noreferrer`, 눌러도 행은 움직이지 않는다, 없으면 `–`). 비면 '이 기간 녹화 없음 — 동의한 방문만 녹화된다'.
- 카드 제목·⤢ 는 목록 판 `#detail=recs` — 받은 행 전부(100까지)를 같은 열로, 찾기(지나간 페이지)·정렬·CSV, 행을 누르면 그 녹화 판으로 바꾼다.
- 행을 누르면 상세 판 `#detail=rec:<행 번호>`(행 번호 = 지금 고른 기간 목록의 순서, 0~999) — 머리 `Clarity 녹화` · 단위 `시작 KST · 길이 · 페이지 · 클릭`, ◀ ▶ 로 앞뒤 녹화, 위에 `Clarity 에서 재생 ↗ · Clarity 로그인이 필요하다`(링크가 없으면 '이 녹화는 재생 링크가 없다'), 아래 시간선 표(시각 `mm:ss` · 종류(페이지 열림 / 클릭 / 스크롤 …, 모르는 종류는 글자 그대로) · 페이지 · 글자(페이지 열림은 제목, 클릭은 클릭한 곳) · 머문 시간·로딩·들어온 곳), 관련 '녹화'. 그 번호의 행이 없으면 '이 녹화는 지금 목록에 없다' 만.
- 링크는 관리자 화면의 주소 쓰기 규칙(064 — 고정 표·Blob·이 페이지 주소)에 하나를 더한다: Clarity 출처로 시작하고 §3.2 의 글자만인 주소. 화면도 같은 검사를 한 번 더 한다.

## 4. 검증
- server: 길이 글자 꼴(초·분·시간 섞임·모름), timestamp → ms(UTC)·틀림 null, link 허용·거절(다른 호스트·http·따옴표·공백·`javascript:`·501자), 주소 줄이기(쿼리 뗌·탭 남김·출처), 상한(행 100·페이지 30·사건 50·글자 자르기), 모르는 키 버림, 401·403 denied·429·500 error·timeout·JSON 아님, 토큰 없음 unconfigured(부르지 않음), `days` 목록 밖 → 3, 30분 캐시(같은 창 두 번째 요청은 부르지 않음), 요청 몸통 모양(start·end·count 100·sortBy 0), 관리자 nginx 정확 일치·라우터 등록.
- node(관리자): 카드 행·빈 문구·탭 바꿈 → `days`, 링크 검사(허용·거절), 상세 판 시간선 행, XSS(제목·글자가 글로만).
- 브라우저: 가짜 피드로 카드·상세 판.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && ruff check . && ruff format --check . && pytest -q   # 2940 passed, 1 skipped
cd web && npm run lint && npm run build                              # Node 22
# 브라우저: .claude/launch.json 의 replay-admin-fake(:18074 — 이 브랜치의 web/scripts/admin-fake-feeds.py)
#   /__scenario/ok → /clarity.html 녹화 카드 7행·탭·재생 링크, 행 → #detail=rec%3A0 시간선 7행·Clarity 에서 재생 ↗
#   /__scenario/xss → #detail=recs 링크 둘 '–'·img 0, 콘솔 오류 0
```

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 server 칸 끝에 `· /admin/clarity/recordings(Clarity 녹화 목록 — MCP 경로, 창마다 30분 메모리 캐시)(077)`, web 칸 끝에 `· Clarity 페이지 녹화 카드·시간선 판(077)`.
- `CLAUDE.md` — 스펙 인덱스 077 행.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것: server `features/admin/clarity_recordings.py`(부르기·다듬기·링크 검사)·`visits.py` `clarity_recordings`(창마다 `Slot`)·`router.py` 경로·`web/nginx-admin.conf` 정확 일치, 시험 `features/admin/tests/test_clarity_recordings.py`·`test_clarity_recordings_feed.py`(65); web `admin/insightrec.js`(카드·판 둘)·`common.js` `PATHS.recordings`·`clarityLink`(출처 상수 `CLARITY_ORIGIN` — 관리자 스크립트의 바깥 주소 예외 하나 더)·`clarity.html` 카드, 시험 `tests/test_admin_recordings.py`(7) + 경로·모듈·XSS·그림 표에 한 줄씩, 가짜 피드 `recordings`(경우별).
- 추측한 지점 / 실행 중 함께 고친 스펙 절: 응답 모양은 2026-10-16 운영 토큰으로 5개 받아 본 것 하나뿐이다(Click 사건만 봤다 — 다른 `eventtype` 이름은 모름, 글자 그대로 보인다). §3.2 실패도 30분 캐시, §3.3 카드 10행·목록 판 `recs`·판 ◀ ▶·없는 행 문구를 더했다.
- 남은 빚: 녹화 경로는 문서 없는 경로라 바뀌면 `bad_data`·`http_<n>` 으로만 보인다. 하루 한도는 운영에서 지켜본다(창 넷 × 30분 = 하루 최대 192번 — 페이지를 띄워 둔 동안만).
