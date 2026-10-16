# 073 — replay-collect

상태: DONE | 의존: 076(처리방침 v7·동의 안내 판 `2026-10-16` — 시행일 상수 `PRIVACY_V7_EFFECTIVE`)·052(동의 판정·비콘 경로 꼴·IP 10분 제한)·028(공개 `/api` 허용 목록)·029(관리자 nginx)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-16): 관리자가 IP 마다 그 방문자가 **어느 화면에서 마우스를 어디에 두고 무엇을 보고 눌렀는지**를 초 단위로 다시 보고(재생, 074), 지금 접속 중이면 몇 초 늦게 따라 본다. Clarity 는 좌표·녹화·IP 를 API 로 주지 않으므로(2026-10-16 확인) 우리 서버가 직접 모은다 — 화면 분석에 동의한 방문자만.

## 2. 범위
- 만드는 것: 공개 정적 스크립트 `public/replay.js`(기록 쪽 — 재생 쪽은 074 가 같은 파일에 더한다), 모든 페이지의 `<script defer src=…replay.js>`, api `POST /replay`(공개 허용 경로 하나), Redis 저장, 관리자 피드 셋 `GET /admin/replay/{live,sessions,session}`(관리자 nginx 정확 일치 셋), 테스트.
- 하지 않는 것: 화면 글자·입력값·눌린 키·선택한 글자·화면 그림은 모으지 않는다. 동의하지 않은 방문자·관리자 틀 안·`kimptrack.com` 밖에서는 아무것도 안 한다. 쿠키·브라우저 저장소에 쓰지 않는다(052 와 같음).
- 바꾸는 기존 것: 028 공개 허용 목록 +1(`/api/replay`, POST 만), caddy 접속 로그 제외 +1, 052 의 '정적 페이지는 attention.js 만' 규칙에 replay.js.

## 3. 동작

### 3.1 언제 기록하나
- `replay.js` 는 attention.js 와 같은 동의 판정(`kt.analytics` = `granted` 이고 `kt.analytics.v` = 지금 안내 판 `2026-10-16`, GPC 아님)이 켜졌을 때만, 호스트가 `kimptrack.com` 이고 틀 안이 아닐 때만 기록한다. 동의가 페이지 중간에 켜지면(같은 탭 `kt:clarity`·다른 탭 `storage`) 그때부터. 보내기 직전마다 동의를 다시 보고, 꺼졌으면 멈추고 남은 것을 버린다.
- 기록 단위 = **조각**(페이지를 한 번 연 것). 조각 id `sid` = 16자 소문자 16진(브라우저 난수), 메모리에만. 대시보드에서 탭을 바꾸면 같은 조각 안의 화면 바뀜 사건이다. 조각은 2시간 또는 묶음 3,600개에서 끝난다(그 뒤로는 보내지 않는다).

### 3.2 사건 — `[t, 종류, …]`, t = 조각 시작부터 ms(정수)
- `m` 포인터 위치 `[t,"m",x,y]` — 화면(뷰포트) 기준 CSS px 정수. 100ms 에 한 번까지, 직전 보낸 위치에서 4px 이상 움직였을 때만. 마우스·손가락(포인터 이벤트) 모두.
- `c` 누름 `[t,"c",x,y,영역]` — click 이벤트의 위치와 가장 가까운 `data-area` id(없으면 `""`).
- `s` 문서 스크롤 `[t,"s",y]` — `scrollY` 정수, 200ms 에 한 번까지(마지막 값은 반드시 보냄).
- `e` 영역 안 스크롤 `[t,"e",영역,n,top]` — 스크롤된 요소가 `data-area` 안이면 그 영역 id, 그 영역 안 스크롤되는 요소(`scrollHeight > clientHeight + 1` 이고 overflow-y 가 auto·scroll) 가운데 문서 순서 번호 n(0부터), `scrollTop` 정수. 요소마다 200ms 에 한 번까지.
- `r` 화면 크기 `[t,"r",w,h]` — `innerWidth`·`innerHeight` 가 바뀔 때(500ms 묶음).
- `v` 보임 `[t,"v",0|1]` — 탭이 숨으면 0, 다시 보이면 1.
- `p` 화면 바뀜 `[t,"p",화면]` — 대시보드 탭이 바뀔 때(`kt:tab`, 052 의 화면 이름 `app-<탭>`).
- `a` 보던 영역 `[t,"a",영역]` — 1초마다 화면에 가장 크게 보이는 `data-area`(보이는 높이 기준)를 보고 바뀌었을 때만.
- 영역 id 는 052 규칙(`^[a-z][a-z0-9-]{0,31}$`), 화면 이름은 052 목록. 좌표는 0~20,000, 스크롤 0~1,000,000 으로 자른다.

### 3.3 보내기
- 2초마다 쌓인 사건이 있으면 한 묶음을 보낸다. 탭이 숨을 때·`pagehide` 에는 곧바로. 사건이 없으면 보내지 않는다.
- 몸통(JSON, text/plain, sendBeacon — 실패하면 fetch keepalive 한 번, 다시 보내지 않음): `{"v":1,"sid","seq","page","dev","w","h","ev":[…]}` — `seq` 0부터 묶음마다 +1, `page` 이 조각의 첫 화면, `dev` `pc`·`mobile`(052 와 같은 768px 기준), `w`·`h` 조각 시작 때 화면 크기. 16,384바이트 이하 — 넘으면 넘는 사건을 다음 묶음으로 미룬다. 한 묶음 사건 600개까지.

### 3.4 `POST /replay` (api — 공개 nginx `= /api/replay`, POST 만, 16k)
- 검사 순서·응답은 052 비콘과 같은 꼴: ① `Sec-Fetch-Site` 가 있고 `same-origin` 이 아니면 403 `cross_site`(몸통을 읽지 않음) ② 16,384바이트 넘으면 413 `too_large` ③ 모양이 틀리면 400 `bad_replay` — 키가 정확히 위 여덟(`v`=1 포함), `sid` 16진 16자, `seq` 0~3,599, `page` 052 화면 이름, `dev` 둘 중 하나, `w`·`h` 1~20,000, `ev` 1~600개이고 사건마다 §3.2 꼴(t 0~7,200,000, 모르는 종류·자리 수 틀림·범위 밖은 몸통 전체 거절).
- 받은 것은 204. 아래는 검사를 통과해도 **버린다**(204): KST `PRIVACY_V7_EFFECTIVE` 00:00 전, IP 10분 창 600묶음 초과(052 의 고정 창 꼴), 그날 저장 바이트 상한 넘음, 그 조각 바이트 상한 넘음.
- IP = `X-Client-IP` 의 첫 값(쉼표 앞, 공백 제거)을 IPv4·IPv6 로 읽은 정규 글자. 읽을 수 없으면 `(없음)`.

### 3.5 저장 (Redis — 데이터 상자. 보관은 078)
- 묶음마다 곧바로(지금 보기가 2~4초 늦게 따라오게) 파이프라인 하나로 쓴다. 보관(078 §3.1): v8 시행일 0시 뒤에 머리를 만든 조각은 머리에 `keep=1` 로 만료 없이 두고 `rp:todo` 에 넣는다(수집기가 S3 로 옮긴다). 그 전 조각의 머리와 모든 조각의 묶음·그날 키는 마지막 쓰기 + 14일.
  - `rp:m:<sid>` 해시 — `ip`·`dev`·`w`·`h`·`page`(첫 화면)·`pages`(지나간 화면 이름, 쉼표, 차례대로 중복 없음)·`first`·`last`(서버가 받은 ms)·`n`(묶음 수)·`bytes`·`clicks`.
  - `rp:e:<sid>` 리스트 — 묶음마다 zlib 로 누른 `{"at":받은 ms,"seq","ev"}` 바이트.
  - `rp:ip:<ip>` 정렬 집합 — sid → `first`. `rp:recent` 정렬 집합 — sid → `last`. 만료·잘라내기 없음(078 — keep 조각이 14일 뒤에도 찾혀야 한다).
  - `rp:b:<YYYYMMDD>`(KST) 정수 — 그날 쓴 누른 바이트 합.
- 상한: 그날 8MB(누른 바이트) — 넘으면 그날 남은 묶음을 버린다. 조각마다 1MB·묶음 3,600개. `seq` 가 이미 받은 것보다 작거나 같으면 버린다(다시 온 비콘). 14일 × 8MB = 112MB 가 최악 — Redis `maxmemory` 600MB 의 시세 키를 밀어내지 않는 몫.
- Redis 가 실패하면 그 묶음은 버리고(204) 30초에 한 번 WARNING. 재생 기록은 빈칸이 될 뿐 서비스 장애가 아니다.

### 3.6 관리자 피드 (api, 관리자 nginx 정확 일치 `/svc/api/admin/replay/{live,sessions,session}` 만, 항상 200 상태 응답·no-store)
- `live` → `{state, now, rows}` — `rp:recent` 에서 `last` 가 60초 안인 조각, 최근 순 50개: `{sid, ip, dev, page, pages, now(지금 화면 = 마지막 p 사건의 화면, 없으면 page), first, last, n, clicks}`.
- `sessions?ip=` → `{state, ip, rows}` — 그 IP 의 조각 최근 순 200개(같은 꼴). `ip` 가 없으면 모든 IP 의 최근 조각 200개. 잘못된 ip 는 400 `bad_ip`(069 꼴).
- `session?sid=&after=` → `{state, meta, batches, next, more}` — `meta` = 위 행 꼴(없거나 만료면 `null`), `batches` = `after`(기본 0)번째 묶음부터 600개까지 `[{at, seq, ev}]`, `next` = 다음에 부를 `after`, `more` = 더 있나. 잘못된 sid 는 400 `bad_sid`.
- Redis 가 없거나 실패하면 `state` `error`·`code` `redis`(052 피드 꼴).

## 4. 검증
- node(`replay.js`, 가짜 window): 동의 아님·판 다름·GPC·틀 안·다른 호스트에서 아무것도 안 보냄 / 포인터 100ms·4px 거르기 / 누름의 영역 / 스크롤 200ms·마지막 값 / 영역 안 스크롤 n / 탭 바뀜 p / 보던 영역 a 는 바뀔 때만 / 2초 묶음·숨을 때 곧바로·빈 묶음 없음 / 16KB·600개 넘으면 다음 묶음 / 동의가 꺼지면 멈춤 / 2시간 끝 / 저장소·쿠키 안 씀 / 보내는 주소는 `/api/replay` 하나.
- server: 검사 순서(403·413·400 경계값 — 키·sid·seq·종류별 자리 수·범위), 게이트 전 버림, IP 600 초과 버림, 그날 8MB·조각 1MB 상한, 같은 seq 다시 옴, Redis 키 모양·만료·`pages` 차례·`clicks`, IP 읽기(쉼표·IPv6·없음), 피드 셋(60초 경계·순서·200개·after/next/more·만료 조각 meta null·bad_ip·bad_sid·redis 오류).
- 정적: 모든 페이지에 replay.js 한 줄, 공개 nginx `= /api/replay` POST 만·16k·`X-Client-IP`, `= /replay.js` no-cache, 관리자 nginx 정확 일치 셋, caddy 로그 제외, 허용 목록 대조 테스트.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 설계 세션 — 합친 브랜치(feat/072-replay: 073·074·076 + main) 2026-10-16
cd server && ruff check . && ruff format --check . && pytest -q   # All checks passed · 467 files · 2866 passed, 1 skipped(소켓 시험은 샌드박스 밖)
cd web && npm run lint && npm run build                          # 경고 0 · 빌드 성공
docker run nginx:1.27-alpine … nginx -t                          # 공개·관리자 템플릿 envsubst 뒤 syntax ok / test is successful
docker run caddy:2-alpine caddy validate                         # Valid configuration
# 끝까지(로컬): redis-server :6472 + 진짜 api(ROLE=api, :8073) + 공개 페이지 시험 사본(kimptrack.localhost:8072 — replay.js 의 HOST·
# ADMIN_ORIGIN 만 바꿈) + 관리자 사본(admin.kimptrack.localhost:18072 — replayframe.js 의 SITE_ORIGIN 만 바꿈, 재생 피드는 진짜 api 로)
#   동의 값을 넣은 랜딩·대시보드에서 움직임 → /admin/replay/live·sessions 에 조각(대시보드는 pages app-spread→app-gap, 탭 단추 누름)
#   → 방문자 페이지 '지금 보는 중'·'화면 기록' 행 → 재생 판: 실제 페이지 틀에 손가락 커서·스크롤 1000px·보던 영역 테두리·숨김 가림막,
#   사건 목록 눌러 옮기기, 콘솔 오류 0. 예전 판(2026-10-09-2) 동의자에게 동의 창이 v7 글로 다시 뜸
# 2026-10-16, worktree marketLens-073srv (feat/073-replay-server) — 서버 쪽만. venv 는 메인 체크아웃의 server/.venv(python 3.12.13)
cd server && python -m ruff check .                 # All checks passed!
cd server && python -m ruff format --check .        # 462 files already formatted
cd server && python -m pytest -q                    # 2799 passed, 1 skipped, 6 failed — 실패 여섯은 test_gauge.py 의 UDP bind(샌드박스가 막음)
cd server && python -m pytest -q app/features/spreads/tests/test_gauge.py   # 샌드박스 밖 15 passed
#   새 테스트: app/features/replay/tests/(test_receive 19·test_store 8·test_feed 23) · tests/test_replay.py(계약 5)
#   고친 계약: tests/test_deploy.py(PUBLIC_API +/api/replay)·test_observability.py(로그 제외 +/api/replay, 공개 location +/replay.js)
#   ·test_admin.py(관리자 피드 셋 — 기록 끔·교차 사이트·rewrite·공개 404)·test_role.py(api 경로 넷)
```
- 하지 않은 것(설계 세션 몫): `nginx -t`(공개·관리자 nginx 를 고쳤다)·`caddy validate`(로그 제외 경로), 로컬 Docker·브라우저 확인. web 쪽(`public/replay.js`·페이지마다 싣는 줄·node 테스트)은 다른 실행 세션이 한다.

## 6. 갱신할 문서
- `docs/context/status.md` — 새 행 `| replay | server: POST /replay(동의한 방문자 조각 묶음 2초)·Redis rp:* 14일·관리자 피드 live·sessions·session | web: public/replay.js 기록 | 동의자만·화면 글자 없음 |`.
- `docs/context/architecture.md` — "현재 구조" 에 replay 기능 폴더 한 줄, 공개 `/api` 허용 목록 문장에 `/api/replay`.
- `docs/context/db.md` — Redis 키 `rp:m:*`·`rp:e:*`·`rp:ip:*`·`rp:recent`·`rp:b:*` 와 보관 14일.
- `CLAUDE.md` — 스펙 인덱스 073 행.

## 7. 실행 보고 (실행 세션이 채움)
서버 쪽 실행 세션(2026-10-16)의 보고다. `public/replay.js` 와 페이지의 `<script>` 줄은 web 쪽 세션이 쓴다.
- 만든 것:
  - 새 기능 폴더 `server/app/features/replay/` — `models.py`(052 와 같은 화면 이름·영역 꼴, 몸통·사건 검사 `parse_batch`, IP 읽기), `service.py`(받기·게이트·IP 10분 600·조각 잠금·상한·Redis 쓰기·30초 WARNING), `feed.py`(피드 셋), `router.py`, `tests/`(conftest·test_receive·test_store·test_feed).
  - 고친 것: `server/app/core/redis_bus.py`(`replay_load`·`replay_append`·`replay_mark_cut`·`replay_recent`·`replay_ip`·`replay_metas`·`replay_batches`, 키 설명), `server/app/main.py`(api 역할에만 서비스·라우터 — 태스크 없음), `web/nginx.conf`(`= /api/replay`·`= /replay.js`, CSP 설명 글), `web/nginx-admin.conf`(정확 일치 셋), `caddy/Caddyfile`(로그 제외 +1), 계약 테스트 넷(§5), 새 `server/tests/test_replay.py`.
  - 대시보드의 `/app/replay.js` 는 `/app/` alias(no-store)가 그대로 준다 — `/app/attention.js` 와 같다(설정 변경 없음).
- 추측한 지점(스펙이 말하지 않아 정한 것 — §1~§4 는 고치지 않았다):
  - 몸통 키는 `v`·`sid`·`seq`·`page`·`dev`·`w`·`h`·`ev` 여덟(§3.4 를 실행 뒤 '여덟' 로 고쳤다), `v` 는 정수 1 만.
  - 사건 `e` 의 n 은 0~199(기록 쪽이 200번째부터 보내지 않는다), `r` 의 w·h 는 몸통 w·h 와 같은 1~20,000, `c` 의 영역은 `""` 또는 영역 id, `e`·`a` 의 영역은 영역 id 만.
  - 머리 해시에 스펙 목록 밖 필드 셋을 더했다 — `seq`(마지막으로 받은 묶음 번호 — 다시 온 비콘 판정), `now`(지금 화면 — `live` 행의 `now` 를 묶음을 풀지 않고 답하려고), `cut`(조각 상한 1MB·3,600 으로 묶음을 버렸으면 `1` — 074 §3.5 '앞부분만' 배지).
  - 피드 행 = `{sid, ip, dev, page, pages, now, first, last, n, clicks, w, h, cut}` — §3.6 꼴에 `w`·`h`(074 의 틀 크기)·`cut` 을 더했고 `pages` 는 JSON 배열. 피드 셋 모두 `code`(null·`redis`)를 늘 싣는다(052 피드 꼴).
  - 순서: `live`·`sessions`(ip 없음)는 last 늦은 순(`rp:recent`), `sessions?ip=` 는 first 늦은 순(`rp:ip:<ip>`). 머리가 만료돼 집합에만 남은 sid 는 행에서 뺀다(그만큼 행이 적을 수 있다).
  - `sessions?ip=` 는 정규 글자 IPv4·IPv6 또는 `(없음)` 만 받는다(빈 값·대문자 IPv6·대역은 400 `bad_ip`). `session?after=` 가 0~9 숫자 1~9자가 아니면 0.
  - IP 10분 제한은 `(없음)` 도 한 키로 센다(052 는 헤더가 없으면 건너뛴다). 조각은 첫 묶음의 IP 집합에만 든다(뒤 묶음의 IP 가 달라도).
  - 상한은 MiB(그날 8×1024×1024·조각 1,048,576) — 쓰면 넘게 되는 묶음을 버린다(상한을 넘지 않는다). 60초 경계는 `now − last ≤ 60,000` 이면 든다.
  - 같은 조각의 묶음 둘이 동시에 오면(숨길 때의 비콘과 2초 묶음) 읽기·쓰기 사이에 섞이지 않게 조각마다 메모리 잠금을 건다.
- 남은 빚:
  - `nginx -t`·`caddy validate`·브라우저·배포 뒤 확인은 하지 않았다(§5). 조각 잠금은 api 워커 1개 안에서만 맞다.
  - web 쪽이 정적 페이지에 `/replay.js` 를 실으면 `tests/test_attention.py` 의 '정적 페이지의 파일 스크립트는 `/attention.js` 하나'·`test_privacy`·`test_kimp_pages` 의 같은 단언을 그 세션이 고친다.
