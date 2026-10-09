# 062 — access-flows

상태: DONE | 의존: main(038·039). 064 admin-v3 가 이 스펙의 응답 키를 쓴다. 고치는 스펙 038(이 레포 주인 담당).

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-09): "어디서 많이 들어오고 나가는지" 를 한눈에. 서버 접속 기록의 (가린 IP, 브라우저 정보) 짝(038)마다 그날 **처음 본 페이지(들어온 곳)**·**마지막 페이지(나간 곳)**·본 페이지 수를 더 기록해, 관리자 화면(064)이 '들어온 길 → 첫 페이지 → 마지막 페이지' 흐름 그림을 그리게 한다.

## 2. 범위
- 만드는 것: api `features/admin` 의 짝 기록에 세 값, `/admin/access` 응답 `visitors` 의 키 넷(`flows`·`entries`·`exits`·`depthPages`), 테스트.
- 하지 않는 것: 화면(064). 짝 기록의 열쇠·게이트·날 경계·1,000 상한·운영자 흔적·탐색 경로 규칙(038 그대로). 바깥 사이트로 나간 링크(줄에 없다 — 화면 요소 클릭은 061).
- 바꾸는 기존 것: 038 §3.5·§3.6(짝이 갖는 것·응답 키).

## 3. 동작

### 3.1 페이지 이름 (061·052 의 화면 이름과 같은 낱말)
사람 모양 페이지 줄(038 의 '페이지 줄' — 브라우저 모양 UA 의 문서 요청)의 경로로 정한다: `/` → `landing`, `/app/`(그 아래 SPA 경로 포함) → `app-<tab>`(쿼리 `tab` 이 052 의 탭 여섯이면 그 값, 없거나 목록 밖이면 `spread`), `/privacy` → `privacy`, `/kimp-chart` → `kimp-chart`, `/kimp-history` → `kimp-history`, 그 밖 → `(기타)`. 탭 바꿈은 SPA 안이라 줄에 없다 — 대시보드의 이름은 '들어올 때의 탭' 이다.

### 3.2 짝이 더 갖는 것 (게이트 뒤 KST 날 짝만 — 038 그대로)
- `entry` — 그날 그 짝의 첫 페이지 줄의 페이지 이름(같은 시각이면 줄 순서).
- `exit` — 그날 그 짝의 마지막 페이지 줄의 페이지 이름(같은 시각이면 뒤 줄).
- `npages` — 그날 페이지 줄 수(255 에서 멈춤).
- 페이지 줄이 없는 짝(JS·WS 줄만)은 셋 다 없음 — 아래 집계에서 빠진다.
- 같은 날을 여러 파일이 나누면(038 처럼 해시로 합친다) entry 는 이른 쪽, exit 는 늦은 쪽 — 같은 시각이면 줄 순서(앞 파일의 entry·뒤 파일의 exit), npages 는 합(255 에서 멈춤).

### 3.3 응답 — `visitors` 에 더하는 키 (창 W 안 짝 — 038 의 '창에서 세는 짝' 과 같은 집합, 확인/모양 두 수)
- `entries` `[[페이지, confirmed, shaped]]` — shaped 큰 순(같으면 이름순), §3.1 의 페이지 이름 열 + `(기타)` 모두(0 인 이름도).
- `exits` 같은 꼴.
- `flows` `[[channel, entry, exit, confirmed, shaped]]` — channel 은 038 의 짝 channel. shaped 큰 순(같으면 channel·entry·exit 이름순) 40줄 + 나머지를 합친 `["(기타)","(기타)","(기타)",c,s]`(나머지가 있을 때만).
- `depthPages` `{"1":[c,s],"2":[c,s],"3-5":[c,s],"6+":[c,s]}` — 짝의 `npages` 칸.
- 게이트 전·`visitors` 가 ok 가 아니면 이 키들은 없다(038 의 하위 부분 규칙 그대로). 응답은 더하기만 한다 — 기존 키는 그대로.

### 3.4 엣지
- 한 짝이 하루에 여러 화면을 오가도 entry·exit 는 하나씩. 날을 넘으면 새 짝(038 열쇠가 날마다 바뀐다)이라 자정을 넘긴 방문은 둘로 센다.
- 탐색 경로·운영자 흔적이 있는 짝은 038 처럼 그날 세지 않는다.
- 메모리: 짝마다 이름 둘(고정 글자 참조)·작은 정수 하나·파일을 합칠 때 쓰는 마지막 페이지 줄 시각 하나(≈56바이트, §5) — 파일·날마다 1,000 짝 상한이라 무시할 만하다.

## 4. 검증
- 페이지 이름 표(경로·쿼리 경우마다 — `/app/?tab=history`·`/app/?tab=bad`·`/app/x`·`/privacy`·`/robots.txt` → `(기타)`).
- 한 짝의 줄 순서 → entry·exit·npages(1·2·5·6·300→255), 페이지 줄 없는 짝 제외, 운영자 흔적 짝 제외.
- `flows` 40줄 + (기타) 합, 정렬, `entries`·`exits` 합 = 창에서 세는 짝 가운데 그날 페이지 줄이 있는 짝 수, `depthPages` 칸 경계.
- 게이트 전 키 없음, 기존 키·값 그대로(기존 테스트 통과).
- `cd server && ruff check . && ruff format --check . && pytest -q`.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
git log --oneline -5   # 의존 확인 — c6e9d45(이 스펙) 아래 9b13b2c = origin/main(038·039 DONE)
# uv 는 샌드박스 밖(캐시 권한). `uv run` 만으로는 dev 도구가 venv 에 없어 전역 ruff 0.12·pytest 를 집는다 — dev 를 넣고 --extra dev 로
cd server && uv sync --extra dev   # ruff 0.16.10·pytest 9.1.1, 만든 uv.lock 은 커밋하지 않고 지움
cd server && uv run --extra dev ruff check . && uv run --extra dev ruff format --check . && uv run --extra dev pytest -q
#   시작 전(c6e9d45): All checks passed · 370 files already formatted · 1936 passed, 1 skipped
#   끝: All checks passed · 372 files already formatted · 1969 passed, 1 skipped (새 test_access_flows.py 33)
#   새 테스트가 가르는지 — 창 조립의 파일 정렬(§7 3)을 잠깐 빼면 test_a_day_split_over_files… 만 실패(되돌림)
# 부담(레포 밖 bench.py — git archive c6e9d45 와 HEAD 를 같은 venv 로, 로컬 macOS arm64·Python 3.12.13, 5회 중 최솟값)
#   한 파일 62,000줄 = 31일 × 날마다 짝 1,000(상한) × 페이지 줄 둘(`/` → `/app/?tab=history` — 모든 줄이 페이지 줄인 최악)
#   전: 읽기 0.234초·남은 메모리 6.37MiB → 뒤: 0.260초·8.03MiB(짝당 ≈56바이트 — 칸 넷과 마지막 시각 float, 038 §3.7 최악 62,000짝 ≈+3.3MiB)
# web 은 고치지 않았다(화면은 064) — npm lint·build 는 돌리지 않음
```

## 6. 갱신할 문서
- `docs/context/status.md` — admin 행 server 칸 끝에 "· 접속 흐름(062 — 짝의 첫·마지막 페이지·페이지 수, `visitors.flows`·`entries`·`exits`·`depthPages`)".
- `CLAUDE.md` — 스펙 인덱스 062 행 DONE.
- `docs/context/architecture.md` — '현재 구조' admin 항목의 짝 기록 문장에 entry·exit·npages.
- `docs/specs/038-access-v2.md` — §3.5 짝이 갖는 것 끝에 "(062 — entry·exit·npages)", §3.6 응답 키 목록 끝에 "`visitors.flows`·`entries`·`exits`·`depthPages`(062)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server `app/features/admin/`: 새 `access_flows.py`(페이지 이름 `page_name`·이름 목록 `PAGES`·`FlowCounts` — entries·exits·flows·depthPages), `access_pairs.py`(`PairDay` 칸 `entry`·`exit`·`last`·`npages`, 파일 합치기, `visitors` 가 흐름을 더함), `access_hours.py`(짝의 페이지 줄마다 갱신), `access_classes.py`(탭 id 여섯 한 곳 — 038 상위 목록과 같이 씀), `access_cache.py`(창 조립이 파일을 줄 순서로 넘김).
  - 테스트: 새 `app/features/admin/tests/test_access_flows.py`(33).
  - 문서: status·architecture, 038 §3.5·§3.6, `CLAUDE.md` 인덱스, 이 스펙.
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
  1. §3.3 "페이지 이름 7종" — §3.1 의 표가 이름 열(052 `PAGES` 와 같다)이라 열 + `(기타)` 로 읽고 문구를 고쳤다. '모두' 는 0 인 이름도 싣는 것으로 정했다 — `entries`·`exits` 는 늘 11행(038 의 다른 목록과 달리 0 행을 빼지 않는다).
  2. 같은 값의 순서: `entries`·`exits` 는 이름순(038 과 같다 — `(기타)` 도 따로 끝에 두지 않는다), `flows` 는 channel·entry·exit 이름순. §3.3 에 적었다.
  3. exit 의 같은 시각은 뒤 줄(entry 의 앞 줄과 대칭). 같은 날을 여러 파일이 나누면 entry 는 038 의 첫 페이지 줄과 같은 쪽, exit 는 마지막 페이지 줄이 늦은 쪽, 같은 시각이면 줄 순서 — 그래서 창 조립이 회전 파일을 수정 시각 순(`access.log` 끝)으로 넘긴다. 038 의 채널·다시 온도 같은 시각이면 앞 파일 것이 된다(전에는 캐시 사전 순서 — 첫 채움에선 새 파일이 앞). 같은 짝의 페이지 줄 둘이 두 파일에 같은 시각으로 있을 때만 다르다. §3.2 에 적었다.
  4. 짝 기록에 마지막 페이지 줄의 시각(float)을 하나 더 둔다 — 파일을 합칠 때 exit 를 고르는 데 필요하다(§3.4 의 메모리 문장을 고쳤다 — 짝당 ≈56바이트, §5). 이름은 고정 글자 참조, npages 는 255 이하라 새 객체가 없다. npages 는 파일마다 255 에서 멈추고 합칠 때 합을 255 로 자른다.
  5. 창의 집합은 §3.3 대로 038 의 '창에서 세는 짝' 가운데 그날 페이지 줄이 있는 짝이다. entry·exit·npages 는 그날 전체 값이라(038 의 채널과 같다) 창 앞 시의 페이지 줄로 정해질 수 있다 — 창 안에는 JS·WS 줄만 있는 짝(어제 09시에 연 대시보드의 WS 가 창 안 12시에 끝남). §4 의 "창 안 페이지 줄 있는 짝 수" 를 이 뜻으로 고쳤다. 그래서 `entries` 의 shaped 합 = `visitors.shaped` − 채널 `unknown` 의 shaped.
  6. 페이지 이름: `/app/` 으로 시작하는 경로만 대시보드 — `/app` 은 nginx 가 301 이라 페이지 줄이 아니고 이름표로는 `(기타)`. 그 밖은 경로 글자가 정확히 같을 때만(`/privacy/` 는 `(기타)`). `tab` 은 038 상위 목록처럼 쿼리를 풀어(빈 값 유지) 첫 값, 대소문자 그대로(`History` → `app-spread`).
  7. 052 의 화면 이름과 같은 낱말인지는 테스트가 `attention.models.PAGES` 와 대조한다(기능 간 import 는 테스트에서만 — `tests/test_admin.py` 와 같은 꼴).
  8. 키 순서: `visitors` 의 기존 열세 키 뒤에 `flows`·`entries`·`exits`·`depthPages`. 게이트 전(`before_gate`)에는 키가 없다(null 이 아니다) — §3.3 "이 키들은 없다" 그대로, 기존 단언이 그대로 통과한다.
  9. §2·§6 의 "038 §3.4" 는 038 에서 짝이 갖는 것이 §3.5 에 있어 §3.5 로 고쳤다.
- 남은 빚:
  - 화면은 064. 036 §3.2 의 접속 피드 복사(036 화면이 읽는 키)에는 새 키를 더하지 않았다 — 064 가 자기 복사에 싣는다.
  - 파일을 합친 뒤의 npages(255 자름)는 응답 칸(`6+`)으로만 보여 테스트는 파일 하나의 255 까지만 단언한다.
  - 운영 확인: 게이트(2026-10-11) 뒤 실제 로그로 `flows` 가 차는지는 배포 뒤 사람이 `/svc/api/admin/access` 로 본다.
