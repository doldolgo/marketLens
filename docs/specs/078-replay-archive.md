# 078 — replay-archive

상태: DONE | 의존: 073(화면 이동 기록 받기·Redis 키·피드 셋)·074(관리자 재생 판)·076(처리방침 v7 — 지금 판)·010(S3 버킷·collect 박스 자격)·025(ERROR → Slack)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 결정(2026-10-16): 화면 이동 기록(073)을 14일이 아니라 **서비스를 끝낼 때까지** 둔다 — 움직임과 IP 모두. Redis(상한 600MB)는 하루 8MB 면 약 75일에 차므로, 지난 기록은 **S3** 로 옮긴다(사람 선택). 처리방침은 **보관 기간·저장 장소만** 고친다(사람 결정 — 받는 것은 그대로라 글자·입력값 문구를 더하지 않는다).

## 2. 범위
- 만드는 것: 조각의 보관 표시, 수집기의 S3 옮기기(10분마다), 수집기 피드 `GET /admin/replay/archive`(관리자 nginx 정확 일치 하나), api `session` 의 `archived`, 관리자 재생 판이 옮긴 조각을 여는 길, IP 로 지우는 스크립트, 처리방침 v8, 테스트.
- 하지 않는 것: 받는 것·검사·상한(073 그대로 — 하루 8MB·조각 1MB), 동의 칸 구조, serve 박스 AWS 자격(2026-10-16 확인 — serve 엔 자격이 없고 collect 박스 역할만 버킷에 닿는다. 그래서 옮기기·읽기는 수집기가 한다).

## 3. 동작

### 3.1 보관 표시 (api — 073 §3.5 를 바꾼다)
- 조각 머리를 처음 만들 때(첫 묶음) 받은 시각이 `PRIVACY_V8_EFFECTIVE` 00:00 KST 이후면 머리에 `keep=1`. keep 조각 = 서비스를 끝낼 때까지 둔다. 머리를 만든 뒤에는 바꾸지 않는다 — v8 을 배포하기 전 코드가 만든 조각(v7 동의)은 keep 이 없어 073 그대로 14일 뒤 사라진다.
- keep 조각: 머리 `rp:m:<sid>` 만료 없음, 묶음 `rp:e:<sid>` 는 마지막 쓰기 + 14일(최근 재생을 Redis 에서 빨리), 옮길 차례 `rp:todo`(정렬 집합 sid → last)에 넣는다. 조각 상한 표시(`cut`)도 머리를 만료시키지 않는다.
- 모든 조각: 집합 `rp:ip:<ip>`·`rp:recent` 는 만료·잘라내기를 하지 않는다(keep 조각이 14일 뒤에도 IP 로 찾혀야 한다). 머리가 만료된 sid 는 피드가 행에서 뺀다(073 그대로). 그날 바이트 `rp:b:<day>` 는 14일 그대로.

### 3.2 S3 옮기기 (수집기, 10분마다)
- `rp:todo` 에서 last 가 3시간보다 오래된 조각을 오래된 순 20개씩(3시간 = replay.js 조각 상한 2시간 + 여유). 조각마다 머리와 Redis 묶음 전부를 한 객체로 쓴다.
- 객체: 키 `replay/<YYYY>/<MM>/<DD>/<sid>.ndjson.gz`(first 의 KST 날), gzip JSON Lines — 첫 줄 머리(해시 그대로 — 글자 값), 다음 줄부터 묶음마다 `{"at","seq","ev"}`(073 이 저장한 JSON 그대로, 다시 직렬화하지 않음). 같은 키로 다시 쓰면 덮어쓴다(멱등).
- 성공하면 머리에 `arch=<쓴 묶음 수>` 를 쓰고 `rp:todo` 에서 뺀다 — 단, 점수가 읽은 last 와 같을 때만(그 사이 묶음이 오면 다음 차례에 다시 통째로). 머리가 없으면 `rp:todo` 에서 빼기만. Redis 묶음이 머리 `n` 보다 적으면(만료로 잃음) 있는 만큼 쓰고 WARNING.
- 실패(S3·Redis)면 그대로 두고 다음 차례에 다시. WARNING 은 10분에 1줄(종류 이름만). `rp:todo` 의 가장 오래된 last 가 7일을 넘으면 ERROR 1시간에 1줄 → Slack(025) — 14일이 지나면 Redis 묶음이 사라진다.
- `S3_BUCKET` 이 없으면 옮기지 않는다(기동 때 WARNING 1줄) — 묶음은 14일 뒤 사라지고 머리·목록만 남는다.

### 3.3 api `GET /admin/replay/session` (073 §3.6 에 더함)
- 응답에 `archived`(불) — 머리에 `arch` 가 있고 Redis 묶음이 비었으면 `true`(묶음 `[]`, `next` = after, `more` false), 그 밖은 `false`. 나머지 키·모양은 그대로.

### 3.4 수집기 `GET /admin/replay/archive?sid=&after=` (관리자 nginx `= /api/admin/replay/archive`)
- sid 꼴(16자 소문자 16진수)이 아니면 400 `bad_sid`(073 과 같은 오류 모양). 공개 nginx 는 028 허용 목록 밖이라 404.
- 머리를 Redis 에서 읽어 키를 셈하고 객체를 받아 줄로 나눈다 → `{state, code, meta, batches, next, more, archived: true}` — 073 `session` 과 같은 꼴(`meta` 는 같은 행 꼴, `after`(기본 0)번째 묶음부터 600개).
- 머리가 없거나 `arch` 가 없거나 객체가 없으면 `meta` null·묶음 []. Redis 실패 `error`·`redis`, S3 실패 `error`·`s3`, 버킷 없음 `error`·`s3_off`. 항상 200 상태 응답·no-store.
- 받은 객체는 메모리에 최근 2개를 10분 둔다(재생 판이 600개씩 이어 부르는 동안 다시 받지 않게). S3 호출은 스레드에서.

### 3.5 관리자 재생 판 (074 에 더함)
- `session` 응답의 `archived` 가 true 면 같은 조각을 `archive` 경로로 처음(after 0)부터 다시 받는다. 404(배포 전 수집기)는 '연결 안 됨'. 옮긴 조각은 끝난 조각이라 따라 보기는 없다.

### 3.6 지우기
- 처리방침 8절(IP 를 알려 주면 그 IP 의 기록을 지운다): 수집기 컨테이너에서 `python -m scripts.replay_forget --ip <IP>` — `rp:ip:<ip>` 의 sid 마다 S3 객체·머리·묶음·`rp:recent`·`rp:todo` 항목과 그 집합을 지우고 지운 조각 수만 찍는다. `--dry-run` 은 셈만.
- 서비스를 끝낼 때: S3 `replay/` 접두와 Redis `rp:*` 를 지운다(런북 clarity.md).

### 3.7 처리방침 v8
- 시행일 = PR 올린 날 KST `2026-10-16`(v7 과 같은 날 두 번째 판 — 060 꼴, 다른 날 머지하면 그날로). 상수 `PRIVACY_V8_EFFECTIVE`(§3.1 의 keep 기준). 안내 판 `2026-10-16` → `2026-10-16-2`(clarity.js·attention.js·replay.js·privacy.html 네 곳 — 지금 동의한 방문자도 다시 묻는다), 이전 판 사본 `public/privacy-20261016.html`(v7).
- 바꾸는 것은 화면 이동 기록의 **보관 기간과 저장 장소뿐**: '14일' → '서비스를 끝낼 때까지 — 끝내면 지운다', 저장 장소 'AWS 서울 리전의 KimpTrack 서버 저장소(Redis)' → '같은 리전의 KimpTrack 서버 저장소(Redis — 최근 14일 몫)와 파일 저장소(Amazon S3)'. 받는 것·받지 않는 것·쓰임·처리 근거·동의 칸 구조는 그대로. 국외 이전 없음(서울 리전) 그대로.
- 고칠 곳: 머리 시행일·변경 안내(바뀌는 것 = 위 둘, 다시 묻는다, 이전 판 링크), 한눈에 보기, 동의 상자·동의 창(같은 글 — 질문 문장과 수집·이용 칸 보유 기간), 2절 화면 이동 기록의 보관 문장과 표 행·'방문자 정보가 가지 않는 곳' 의 저장 장소 문장, 3절 파기, 7절 행태정보의 수집 방법·보유 기간, 12절 시행 문장·이력·대조표(9행). 9절(안전성)·5절(위탁)·8절은 그대로 — 새 안전 조치 문장을 더하지 않는다.
- 머지 = 게시. **법률 확인(사람)을 마친 뒤 머지한다.** 머지가 늦으면 시행일을 그날로(안내 판도 그날 값으로).

### 3.8 엣지
- 2026-10-16 운영 Redis 의 `rp:*` 는 0개 — 옮길 것이 없다.
- 저장량: 하루 8MB 상한 그대로 → S3 는 많아야 연 2.9GB. Redis 에 남는 것은 keep 머리(조각마다 수백 바이트)와 두 집합 — 조각 1,000/일이면 연 약 200MB 라, 그 전에 머리도 옮길지 다시 본다(status 빚).
- Redis 를 잃으면(data 박스 다시 만듦) 목록·머리가 사라진다. S3 객체는 첫 줄에 머리를 담아 다시 세울 수 있다(다시 세우는 도구는 빚).

## 4. 검증
- server(api): 첫 묶음 시각 경계로 keep(전 0시 ‑1ms 없음·0시 있음), keep 머리 만료 없음·묶음 14일·`rp:todo`, keep 아닌 조각은 073 그대로 14일, 두 집합 만료·잘라내기 없음, `cut` 이 keep 머리를 만료시키지 않음, `session` 의 `archived`(arch + 빈 묶음만 true).
- server(수집기): 3시간 경계·20개·오래된 순, 객체 키(KST 날)·첫 줄 머리·묶음 줄 그대로·gzip, 성공 시 arch·todo 빼기, 점수가 바뀌었으면 안 뺌, 머리 없음, 묶음 일부 잃음 WARNING, S3 실패는 그대로 둠·WARNING 10분, 7일 ERROR 1시간, 버킷 없음.
- server(archive 피드): bad_sid 400, 쪽 나누기(after·next·more), 머리 없음·arch 없음·객체 없음 → meta null, redis·s3·s3_off, 캐시 2개·10분, 관리자 nginx 정확 일치·공개 404·라우터는 collector 에만.
- 지우기 스크립트: 그 IP 의 키·객체만 지움, dry-run.
- web(관리자): `archived` 면 archive 경로로 처음부터, 404 → 연결 안 됨.
- 처리방침: test_privacy 꼴(`<time>` 넷·sitemap·상수·판 값 네 곳·이전 판 사본·대조표 행), 화면 이동 기록 문장에 '14일' 이 남지 않음, '서비스를 끝낼 때까지'·'S3' 있음, 동의 창 글 = 방침 동의 상자 글.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && ruff check . && ruff format --check . && pytest -q   # 2969 passed, 1 skipped
cd web && npm run lint && npm run build                              # Node 22
# 운영 사실 확인(2026-10-16, 읽기만): data Redis rp:* 0개·maxmemory 600MB noeviction·AOF 켬, serve api 컨테이너 AWS 자격 없음
#   (NoCredentialsError), collect 역할 marketlens-s3-snapshot 이 버킷의 raw/·replay/ 목록을 읽음(쓰기 권한은 배포 뒤 첫 옮기기로 확인)
```

## 6. 갱신할 문서
- `docs/specs/073-replay-collect.md` §3.5 — 보관은 078(keep·두 집합 만료 없음), `docs/specs/074-admin-replay.md` — 옮긴 조각 열기.
- `docs/context/db.md` — `rp:*` 보관 규칙·`rp:todo`·S3 `replay/` 접두.
- `docs/context/status.md` — replay 행(서비스 종료까지·S3 옮기기·빚 둘), privacy 비고 v8.
- `docs/context/architecture.md` 현재 구조 — 수집기 옮기기·archive 피드.
- `docs/runbooks/clarity.md` — IP 로 지우기·서비스를 끝낼 때.
- `CLAUDE.md` — 스펙 인덱스 078 행.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것: api `service.py`(첫 묶음 시각으로 keep)·`feed.py`(`archived`, 줄 잇기 `page_body` 를 archive 와 나눠 씀)·`core/redis_bus.py`(append 의 keep·PERSIST, `rp:todo`·옮기기·지우기 메서드), 수집기 `features/replay/archive.py`(`Archiver`·`ArchiveFeed`)·`router.py` 의 `collector_router`·`main.py`(원문 아카이브와 같은 `S3Uploader`), `core/s3.py` `get`·`delete`, `web/nginx-admin.conf` 정확 일치, `scripts/replay_forget.py`, 관리자 `common.js` `PATHS.replayArchive`·`replaymodel.js` `archiveReq`·`replaysheet.js`(archived 면 archive 로 처음부터)·'14일 보관' 문구 넷, 처리방침 v8(`privacy.html`·`privacy-20261016.html`·`clarity.js`·`attention.js`·`replay.js` 판 값), 시험(`features/replay/tests/test_keep.py`·`test_archive.py`, `tests/test_replay_forget.py`, test_privacy·test_replay_js·관리자 재생 시험 고침).
- 추측한 지점 / 실행 중 함께 고친 스펙 절: §3.7 고칠 곳 — 7절 행태정보에도 14일이 있어 고쳤고, 9절에 'S3 공개 접근 차단' 을 더하지 않았다(사람 결정 '보관 기간·저장 장소만', 그리고 운영 역할로 버킷 공개 차단 설정을 읽을 수 없어 확인하지 못함 — 010·ec2-setup 런북엔 '퍼블릭 차단'). 5절 위탁(AWS) 의 맡기는 일에 S3 저장을 적을지는 법률 확인 몫. 관리자 판의 '기록이 없거나 지났다(14일 보관)' → '기록이 없거나 지났다'.
- 남은 빚: collect 역할의 `replay/` 쓰기 권한은 배포 뒤 첫 옮기기에서 확인(안 되면 런북 clarity.md 7 — 사람이 역할 정책에 더함). keep 머리·집합이 Redis 에 쌓임(조각 1,000/일이면 연 약 200MB). Redis 를 잃으면 S3 첫 줄로 다시 세우는 도구 없음. 법률 확인(사람) — 머지 = 게시, 늦게 머지하면 시행일·판 값을 그날로.
