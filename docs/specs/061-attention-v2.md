# 061 — attention-v2

상태: DONE | 의존: **060 privacy-v4**(상수 `PRIVACY_V4_EFFECTIVE`, 동의 안내 판 `2026-10-09-2` — attention.js 의 판 값). 052 attention 을 넓힌다. 065 admin-screens 가 이 스펙의 피드·요소 열쇠를 쓴다.

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
사람 요청(2026-10-09): 화면을 이루는 **요소마다**(단추·링크·탭·입력칸·정렬 머리·표의 코인 행·질문 항목…) 얼마나 누르고·마우스를 올려 두고·화면에 보였는지, 그리고 **어디서 떠나는지·얼마나 내려오는지**를 본다. Clarity Data Export 는 이런 값을 주지 않는다(평균 스크롤 깊이·불만 신호 같은 요약뿐, 히트맵은 Clarity 대시보드 안에서만). 052 의 영역 통계를 요소 단위로 넓힌다.

## 2. 범위
- 만드는 것: `web/public/attention.js` 수집 v2(요소·마우스 올림·떠난 곳·깊이), 대시보드 컴포넌트와 정적 페이지의 `data-el`, api `POST /attention` v2 몸통·저장, `GET /admin/attention` 의 `page` 인자와 요소·떠난 곳·깊이, 공개 nginx 몸통 상한, 테스트.
- 하지 않는 것: 덮어 보기(오버레이)에 요소를 그리는 것과 관리자 화면(065). 좌표·마우스 경로·입력값·화면 글자의 숫자(가격 등) — 보내지 않는다. 052 의 영역 규칙·동의 판정·게이트·IP 제한(그대로).
- 바꾸는 기존 것: 052 §3.2~§3.6(몸통 v2·상한·저장 필드·피드), 028 의 `/api/attention` 몸통 상한, 002·017·048·049·050·022·044·032 화면의 `data-el` 속성(동작 변화 없음 — 담당자에게 알림).

## 3. 동작

### 3.1 요소와 열쇠
- **요소** = 영역(`[data-area]`) 안에서 사람이 누르거나 보는 낱낱. 요소마다 **열쇠**(문자열, 1~64자, `|` 와 제어 문자 없음) 하나.
- 열쇠 정하기(위에서 먼저 맞는 것):
  1. 그 요소나 가장 가까운 조상(영역 안)의 `data-el` 값.
  2. 대화형 요소(`a[href]`·`button`·`summary`·`input`·`select`·`textarea`·`label`·`[role=button|tab|checkbox|switch|link]`) — `<종류>:<이름>`. 종류는 `link`·`btn`·`faq`(summary)·`input`·`select`·`check`(체크 상자)·`tab`. 이름은 `aria-label` → (링크) `href` 의 경로(같은 출처는 경로만, 다른 출처는 호스트+경로, 쿼리·조각 뺌) → (입력칸) `name`·`placeholder`·`type` → 보이는 글자(앞뒤 공백 빼고 공백 하나로, 앞 24자). **입력칸의 값은 절대 쓰지 않는다.**
  3. 그 밖의 클릭은 영역에만 센다(052 그대로).
- 실행 세션이 붙이는 `data-el`(동작·모양 변화 없음, 값은 영문 소문자·숫자·`-`·`:`):
  - 대시보드 표의 행: `row:<심볼>`(스프레드·기록·입출금 레이더 — 심볼은 대문자 그대로). 행은 **클릭만** 센다(매초 재정렬이라 보인 시간·마우스 올림은 세지 않는다).
  - 대시보드의 모든 조작(탭 단추·검색칸·거래소 고르기·임계값·토글·체크 상자·정렬 머리·기간 단추·차트 도구·카드 접기)과 공통 머리(`tabs` 의 단추 여섯 `tab:<id>`, 처리방침·화면 분석 설정 링크): 화면 글자를 영문 낱말로 옮긴 짧은 값(예 `search`·`dom:upbit`·`threshold`·`only-over`·`sort:kimp`·`period:1w`).
  - 정적 페이지: 자동 열쇠(링크 경로·질문 글자)로 충분하면 붙이지 않는다. 랜딩 실시간 카드처럼 글자가 바뀌는 단추에는 붙인다.
  - 붙인 목록 전체를 §7 '요소 목록'(화면 → 영역 → 열쇠 → 한국어 이름)으로 남긴다 — 065 의 이름표가 쓴다.

### 3.2 브라우저에서 세기 (attention.js — 052 규칙에 더함)
- **추적 요소** = 영역 안에서 열쇠가 있는 요소(행 `row:*` 빼고), 화면당 처음 찾은 200개까지(서로 다른 열쇠 기준 — 같은 열쇠 여럿은 하나로 센다). 052 의 영역 찾기(MutationObserver, 1초에 한 번)와 같이 다시 찾는다.
- **클릭**: 052 처럼 영역에 1, 그리고 열쇠가 있으면 그 요소에 1(행 포함). 요소당 구간 합 상한 50.
- **보인 시간**: 추적 요소마다 052 의 '보임' 규칙(≥ min(요소 높이×0.5, 화면 높이×0.5), ≥ min(40px, 요소 높이))과 같은 조건(문서 보임·5분 안 입력)으로 쌓는다. 같은 열쇠의 요소가 여럿이면 하나라도 보이면 보임. 구간 합 상한 600,000ms.
- **마우스 올림**: 기기가 `pc` 이고 포인터가 마우스일 때만. 추적 요소(행 빼고) 위에 머문 시간을 문서가 보이는 동안만 쌓되, 한 번 머문 시간이 300ms 미만이면 버리고 30초에서 자른다. 구간 합 상한 600,000ms.
- **깊이**(`landing`·`privacy`·`kimp-chart`·`kimp-history` 만): 구간에서 닿은 가장 아래 = (scrollY + innerHeight) / 문서 높이, 0~10(10% 칸, 끝까지 = 10). 대시보드는 보내지 않는다(문서 스크롤이 없다).
- **떠난 곳**: `pagehide` 로 끝날 때만(탭 숨김·대시보드 탭 바꿈은 아님) — 그 순간 보이는 높이가 가장 큰 영역 id. 깊이·떠난 곳은 구간에 한 번만 보낸다.

### 3.3 보내기 — 몸통 v2
`{"v":2,"page","device","pv","a":{…052 그대로…},"e":[["<영역>","<열쇠>",clicks,hoverMs,visMs],…],"d":0~10|null,"x":"<영역>"|null}`
- `e` 는 값이 모두 0 인 요소를 빼고, 많으면 (clicks, visMs) 큰 순 150개까지. 몸통 ≤ 16,384바이트(넘치면 `e` 를 뒤에서부터 줄인다). `d`·`x` 는 해당 없으면 null.
- 보내는 때·방법·동의 판정은 052 그대로(동의 판 값은 060 의 `2026-10-09-2`).

### 3.4 서버가 받기 — `POST /attention`
- v1 몸통도 그대로 받는다(캐시된 옛 스크립트). v2 검사 더함: `e` 는 목록 ≤ 150, 항목은 `[영역 id 꼴, 열쇠(1~64자·|·제어 문자 없음), clicks 0..50, hoverMs 0..600000, visMs 0..600000]`, `d` 는 null 또는 0..10 정수, `x` 는 null 또는 영역 id 꼴. 어긋나면 400(아무것도 더하지 않음).
- 몸통 상한 16,384바이트(앱 413, 공개 nginx `client_max_body_size 16k`).
- 요소·깊이·떠난 곳은 시각이 `PRIVACY_V4_EFFECTIVE` 00:00 KST 뒤일 때만 더한다(영역은 052 게이트 그대로).
- 열쇠 수 제한: (KST 날, 화면, 기기)마다 서로 다른 열쇠 300개까지 api 메모리로 세고, 넘는 새 열쇠는 `(기타)` 로 더한다(그날 처음 본 순서). 프로세스가 다시 뜨면 새로 센다 — 넘칠 수 있지만 Redis 크기 상한 근사로 충분하다.

### 3.5 저장 — 같은 날짜 해시 `attn:d:<YYYYMMDD>`(052, 만료 그대로)
- 요소: `<page>|<device>|<area>|e|<열쇠>|c` · `…|h` · `…|v`. 떠난 곳: `<page>|<device>|<area>|x`. 깊이: `<page>|<device>||d<0~10>`. 0 인 값은 쓰지 않는다. 052 의 필드는 그대로.
- 크기 짐작: (화면 10 × 기기 2 × 열쇠 300 × 3) 상한이면 하루 18,000 필드 — 실제로는 누르거나 보인 것만이라 훨씬 적다.

### 3.6 관리자 피드 — `GET /admin/attention?days=&page=`
- `page` 가 없으면 052 응답에 행마다 `exits`·`depth` 를 더한다: `exits` `[[영역, n]]`(n 큰 순), `depth` 길이 11 정수 목록(칸마다 그 칸까지 닿고 멈춘 pv 수 — 대시보드 화면은 null).
- `page=<화면 이름>`(052 목록 안)이면 그 화면 행만, 그리고 행마다 `elements` `[{area, key, clicks, hoverMs, visMs}]` — (clicks, visMs) 큰 순 300개까지. 목록 밖 `page` 는 없는 것과 같다.
- 캐시: (days, page)마다 60초(052 와 같은 꼴). 응답의 그 밖 키·상태는 052 그대로.

### 3.7 엣지
- 같은 열쇠가 영역 둘에 있으면 영역마다 따로 센다(열쇠는 (영역, 열쇠) 짝으로 유일).
- 글자로 만든 열쇠는 화면 글자가 바뀌면 새 열쇠가 된다 — `data-el` 을 붙인 요소는 안 바뀐다. 바뀐 열쇠의 옛 값은 90일 동안 '지금 화면에 없음'(065).
- 철회·숨김·5분 무입력·구간 합 상한은 052 와 같은 뜻으로 요소에도.

## 4. 검증
- 서버: v2 검사마다 400·더한 것 없음(`e` 151개·열쇠 65자·`|` 든 열쇠·hover 600001·`d` 11·`x` 꼴), v1 그대로 204, 16,384 초과 413, V4 게이트 전 요소·깊이·떠난 곳만 버림(영역은 더함), 열쇠 301번째 `(기타)`, 저장 필드 꼴, 피드 `page` 있음/없음·목록 밖 page·`elements` 순서와 300 상한·`exits`·`depth`(대시보드 null)·60초 캐시.
- attention.js node(052 방식): 열쇠 정하기(data-el·aria·링크 경로·쿼리 뺌·다른 출처·입력칸 값 안 씀·글자 24자), 행은 클릭만, 요소 보임 규칙, 마우스 올림(300ms 미만 버림·30초 자름·mobile 안 셈), 깊이 칸, 떠난 곳은 pagehide 에만, 몸통 16KB 줄이기, 같은 열쇠 여럿 하나로.
- 정적: 대시보드 소스의 `data-el` 값이 꼴에 맞고 화면 안에서 (영역, 열쇠)가 겹치지 않는다(§7 목록과 대조), 공개 nginx `client_max_body_size 16k`.
- 기존 전부: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`, `node --check web/public/attention.js`.
- 설계 세션(헤드리스 Chrome, 052 방식): 랜딩·대시보드에서 누르고 마우스를 올린 뒤 떠나면 v2 몸통의 `e`·`d`·`x`, Redis 필드, 피드 `page=landing` 의 `elements`.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 2026-10-14, worktree marketLens-exec-061 (feat/061-attention-v2 — 0ecbae9 위에서 시작, origin/main e76a350(064)을 merge 커밋 d4e751b 로 받음).
# uv 는 Bash 샌드박스 밖에서(캐시 권한). uv sync 가 만든 server/uv.lock 은 커밋하지 않고 끝에 지웠다. 브라우저·Docker 는 쓰지 않았다(지시)
cd server && uv sync --extra dev
cd server && uv run --extra dev ruff check . && uv run --extra dev ruff format --check . && uv run --extra dev pytest -q -rs
#   All checks passed! / 408 files already formatted / 2429 passed, 1 skipped(test_geo_perf — DBIP_DIR 없음, 061 과 무관)
#   시작 때(merge 뒤) 같은 pytest: 2401 passed, 1 skipped. 새 테스트 28 — features/attention/tests/test_receive_v2.py 6·test_feed_v2.py 5,
#   tests/test_attention_elements.py 3·test_attention_js_elements.py 8·test_attention_js_exit.py 6. node 테스트는 PATH 의 node v18.12.0 으로
#   실제로 돌았다(건너뜀은 test_geo_perf 하나 — -rs)
cd web && PATH=/opt/homebrew/bin:$PATH npm ci && PATH=/opt/homebrew/bin:$PATH npm run lint && PATH=/opt/homebrew/bin:$PATH npm run build
#   oxlint 1.80.0 종료 0 / tsc -b && vite build ✓ (첫 화면 JS 250.81KB·gzip 78.53KB, 기록 탭 청크 211.69KB)
node --check web/public/attention.js     # v18.12.0·v26.4.0 둘 다 통과
# 테스트가 정말 잡는지 — 스크래치 변형 스크립트(파일 하나를 바꾸고 해당 테스트를 돌린 뒤 되돌림), 39 변형 모두 실패
# (처음 돌렸을 때 살아남은 셋 — 단추인 행 빼기·창 밖으로 나감·값이 0 인 요소의 열쇠 자리 — 은 테스트를 더해 잡았다):
#   attention.js — 마우스 올림 300ms·30초·휴대폰·마우스 아닌 포인터, 글자 24자, 숫자 가림, 행(단추) 추적 빼기, 라벨 한 번, isTrusted, 추적 200,
#   e 150·16KB·정렬, 깊이 끝 1px·한 번, 떠난 곳 pagehide 만, 경로에 쿼리, mailto, 요소 클릭 상한, role 먼저, 보낼 때 마우스 올림 끝, aria 먼저,
#   창 밖, 탭 바뀜 새로 찾기 / 서버 — 열쇠 300·e 150·열쇠 64자·같은 짝 둘·대시보드 d·v4 게이트·0 항목 자리, 피드 300·정렬·exits 정렬·
#   대시보드 depth null·목록 밖 page·(days, page) 캐시
# 설계 세션 확인(머지 전 — 이 세션은 브라우저·Docker 를 쓰지 않았다): §4 마지막 줄, nginx -t(nginx.conf 를 고쳤다) — §7 남은 빚
```

## 6. 갱신할 문서
- `docs/context/status.md` — attention 행에 "v2(061 — 요소별 클릭·마우스 올림·보인 시간, 떠난 곳·깊이, 피드 page 인자)".
- `CLAUDE.md` — 스펙 인덱스 061 행 DONE.
- `docs/context/architecture.md` — attention 항목에 요소 열쇠 규칙·v2 몸통·열쇠 300 제한.
- `docs/context/db.md` — `attn:d:*` 필드 꼴에 요소·떠난 곳·깊이.
- `docs/specs/052-attention.md` — §3.3·§3.4·§3.5·§3.6 끝에 "(061 가 v2 로 넓혔다 — 061 §3)" 한 줄씩.
- `docs/specs/028-api-allowlist.md` — `/api/attention` 몸통 상한 16k.
- 담당자에게 제안(PR 본문): 002·017·048·049·050·022·044 — `data-el` 속성(동작 변화 없음), 조작을 더하거나 바꾸면 `data-el` 도.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - web: `web/public/attention.js`(열쇠 정하기·추적 요소 200·요소 클릭·보인 시간·마우스 올림·깊이·떠난 곳·몸통 v2 16KB — 053 덮어 보기 부분은 그대로), `web/src/shared/ui.tsx`(선택 속성 `SegOpt.el`·`segOpt` 넷째 인자·`NumField`/`ToggleBtn` 의 `el`·`Header` 넷째 칸 → `data-el`), `web/src/App.tsx`·`features/{spreads,gap,pp,flow}/Tab.tsx`·`features/history/Tab.tsx`·`Chart.tsx`(속성만), `web/public/landing.html`·`kimp-chart.html`·`kimp-history.html`(속성만 — 랜딩·검색어 페이지 스크립트가 그리는 카드·행 포함).
  - server: `app/features/attention/models.py`(상한 16,384·요소 150·열쇠 64자·깊이·열쇠 300·`(기타)`·`valid_key`·`Beacon` 의 elements·depth·exit), `service.py`(`parse_beacon` v1·v2, `KeyLimiter`, `element_counts`, v4 게이트 `elements_effective`), `feed.py`(`_Row`·`build_rows(days, page)`·(days, page) 캐시), `router.py`(`page` 인자). 새 테스트 `features/attention/tests/test_receive_v2.py`·`test_feed_v2.py`, 고친 `conftest.py`(v4 게이트 날)·`test_receive.py`(413 은 16,384)·`test_feed.py`(행에 exits·depth).
  - 인프라: `web/nginx.conf`(`= /api/attention` 의 `client_max_body_size 16k`).
  - 테스트: 새 `tests/test_attention_elements.py`(화면별 `data-el` 목록·값 꼴·템플릿 값)·`test_attention_js_elements.py`·`test_attention_js_exit.py`, 고친 `tests/test_attention_js.py`(가짜 문서를 부모·자식 나무로 — 선택자·누름·마우스 올림)·`test_attention_js_send.py`(v2 몸통)·`test_attention.py`(16k·textContent 는 이름 한 곳)·`test_clarity.py`(랜딩 설정 링크의 속성 허용).
  - 문서: 이 스펙, `CLAUDE.md`, `docs/context/status.md`·`architecture.md`·`db.md`·`dev-setup.md`, `docs/specs/052-attention.md`(§3.3~§3.6 끝 한 줄씩)·`028-api-allowlist.md`(16k). 라이브러리 추가 없음.
- 요소 목록(화면 → 영역 → 열쇠 → 한국어 이름). `data-el` 로 단 것 — `tests/test_attention_elements.py` 가 같은 목록을 묶는다. 자동 열쇠(괄호)는 지금 페이지에서 생기는 것의 보기:
  - 대시보드 공통(모든 `app-*`): `tabs` — `tab:spread` 실시간 스프레드 탭 · `tab:history` 기록/통계 탭 · `tab:gap` 선물–현물 갭 탭 · `tab:pp` 선선갭 탭 · `tab:health` 수집 상태 탭 · `tab:flow` 입출금 레이더 탭. 영역 밖(052 — 세지 않음): `privacy` 개인정보 처리방침 링크 · `menu` 메뉴 단추(폭 1079px 이하 두 줄 헤더 — 072, 링크는 그 안에 같은 열쇠로). (`consent-settings` 화면 분석 설정 링크는 2026-10-18 에 뺐다 — 033.) `header`·`kpi` 는 조작이 없다.
  - `app-spread`: `filters` — `search` 심볼 검색칸 · `dom:all` 기준 국내 거래소 '모두' · `dom:upbit` '업비트' · `dom:bithumb` '빗썸' · `threshold` 하이라이트 임계값 · `only-over` 임계 초과만 · `only-io` 입출금 열림 · `only-net` 네트워크 같음 · `view:kimp` 김프 기준 · `view:rev` 역프 기준 · `fx:all` 비교 해외 거래소 '모두' · `fx:binance`·`fx:bybit`·`fx:bitget`·`fx:okx`·`fx:mexc`·`fx:gateio`·`fx:hyperliquid` 비교 해외 거래소 체크 상자(Binance·Bybit·Bitget·OKX·MEXC·Gate.io·Hyperliquid) · `filter-toggle` 필터 펼치기(폰 — 072). `table` — `sort:col` 정렬 열 고르기 · `sort:dir` 정렬 방향(둘 다 폰 정렬 줄 — 072) · `sort:sym` 심볼 정렬 · `sort:chg` 변동율 · `sort:price` 국내가격 · `sort:usd` 해외가격 · `sort:fxex` 해외거래소 · `sort:domex` 국내거래소 · `sort:val` 김프(역프) · `sort:io` 입출금 · `sort:net` 네트워크 · `row:<심볼>` 코인 행(클릭만).
  - `app-history`: `filters` — `dir:kimp` 김프 · `dir:reverse` 역프 · `period:1h`·`period:4h`·`period:8h`·`period:12h`·`period:24h`·`period:3d`·`period:7d`·`period:30d`·`period:90d` 기간(1시간·4시간·8시간·12시간·24시간·3일·1주·1달·3달) · `dom:all`·`dom:upbit`·`dom:bithumb` 국내(전체·업비트·빗썸) · `fx:all`·`fx:binance`·`fx:bybit`·`fx:bitget`·`fx:okx` 해외(전체·Binance·Bybit·Bitget·OKX) · `search` 심볼 검색칸 · `filter-toggle` 필터 펼치기(폰 — 072). `table` — `sort:col`·`sort:dir` 폰 정렬 줄(072) · `sort:sym` 티커 · `sort:ongoingsince` 상태 · `sort:cnt` 횟수 · `sort:maxdur` 최대 지속 · `sort:avgdur` 평균 지속 · `sort:maxpct` 최대 스프레드 · `sort:avgpct` 평균 스프레드 · `sort:net` 네트워크 · `sort:last` 최신 · `row:<심볼>` 티커 행(클릭만). `summary`·`list` — 없음. `filters-2` — `interval:1m`·`interval:3m`·`interval:5m`·`interval:15m`·`interval:30m`·`interval:1h`·`interval:4h`·`interval:1d` 봉 종류 · `chart-dom:all`·`chart-dom:upbit`·`chart-dom:bithumb` 차트 국내 체크 상자(전체·업비트·빗썸). `chart` — `card:binance`·`card:bybit`·`card:bitget`·`card:okx`·`card:mexc` 해외 거래소별 차트 카드 · `fold:binance`·`fold:bybit`·`fold:bitget`·`fold:okx`·`fold:mexc` 카드 접기/펼치기.
  - `app-gap`: `filters` — `search` 심볼 검색칸 · `mode:entry` 진입 기준 · `mode:exit` 정리 기준 · `threshold` 하이라이트 임계값 · `only-over` 임계 초과만 · `filter-toggle` 필터 펼치기(폰 — 072). `table` — `sort:col`·`sort:dir` 폰 정렬 줄(072) · `sort:sym` 심볼 · `sort:price` 현물가 USDT · `sort:gap` 갭 · `sort:funding` 펀딩비.
  - `app-pp`: `filters` — `search` 심볼 검색칸 · `threshold-price` 가격갭 임계값 · `threshold-funding` 펀딩갭 임계값 · `only-over` 임계 초과만 · `filter-toggle` 필터 펼치기(폰 — 072). `table` — `sort:col`·`sort:dir` 폰 정렬 줄(072) · `sort:sym` 심볼 · `sort:gap` 가격갭 · `sort:fund` 펀딩갭.
  - `app-health`: 없음(조작이 없다).
  - `app-flow`: `filters` — `window:1h`·`window:6h`·`window:24h` 창(1시간·6시간·24시간) · `dir:all`·`dir:in`·`dir:out` 방향(전체·입금·출금) · `search` 코인 심볼 입력칸. `table` — `row:<심볼>` 코인 행(클릭만 — 코인 이름 단추도 이 열쇠). `table-2` — `tx` 거래 해시 링크(etherscan, 모든 행이 한 열쇠).
  - `landing`: `top` — `nav:kimp` 김프·역프란 · `nav:method` 계산 방법 · `nav:faq` 자주 묻는 질문 (`link:/` 로고 · `link:/app/` 실시간 김프 표). `hero` — `app:history` 김프 기록과 차트 링크 · `live:1` 지금 가장 큰 경로 카드 · `live:2`~`live:5` 그다음으로 큰 경로 2~5위 (`link:/app/` 실시간 김프 표 보기). `analyze` — (`link:/kimp-history`·`link:/kimp-chart`·`link:/app/`). `events` — `row:<심볼>` 최근에 끝난 사건 표의 행(클릭만) (`link:/app/` 비트코인 김프 기록과 차트 보기). `kimp`·`method` — 없음. `faq` — `faq:why-differ` 김프 사이트마다 숫자가 다른 이유 · `faq:why-1000` 왜 $1,000 기준 · `faq:rate` 환율은 무엇을 쓰나 · `faq:blocked` 옮길 수 없는 경로 판단 · `faq:cost` 가입이나 비용 · `faq:reverse-term` 역김프와 역프 · `faq:chart` 김프 차트와 과거 기록 (`link:/app/`·`link:/kimp-chart`·`link:/kimp-history`). `foot` — `app:history` 김프 기록과 차트 · `nav:kimp`·`nav:method`·`nav:faq` (`link:/app/`·`link:/kimp-chart`·`link:/kimp-history`·`link:/privacy`·`link:mailto` 문의 메일 둘).
  - `kimp-chart`: `top` — (`link:KimpTrack 첫 화면` 로고 · `link:/app/` 김프 차트 열기). `what` — (`link:/kimp-history`). `read` — 없음. `coins` — `coin:btc`·`coin:eth`·`coin:xrp`·`coin:sol`·`coin:doge` 코인별 김프 차트 바로 가기(비트코인·이더리움·리플·솔라나·도지코인) · `coin:all` 전체 코인 김프 차트. `faq` — `faq:realtime` 김프 차트는 실시간인가 · `faq:vs-price` 코인 가격 차트와 다른 점 · `faq:apps` 거래소 앱·트레이딩뷰 · `faq:fees` 수수료와 전송 시간 (`link:/app/`). `foot` — `nav:kimp` 김프·역프란 (`link:/`·`link:/app/`·`link:/kimp-history`·`link:/privacy`).
  - `kimp-history`: `top` — (`link:KimpTrack 첫 화면`·`link:/app/`). `live` — `row:<심볼>` 지난 7일 사건 표의 행(클릭만) (`link:/app/` 전체 사건 링크). `what` — (`link:/kimp-chart`). `how` — 없음. `faq` — `faq:range` 얼마나 과거까지 · `faq:max-vs-live` 최고 %가 실시간 표보다 큰 이유 · `faq:profit` 사건이 많으면 돈을 버는지. `foot` — `nav:kimp` (`link:/`·`link:/app/`·`link:/kimp-chart`·`link:/privacy`).
  - `privacy`: `data-el` 없음 — 자동 열쇠만: `top` (`link:/`·`link:/app/`), `changes` (`link:/privacy-20261009.html`·`link:/privacy-20261011.html`·`link:/privacy-20261001.html`·대조 `link:/privacy`), `consent` (`btn:동의 철회`·`btn:선택한 대로 저장`·`btn:모두 거부`·`check:checkbox`(세 칸이 한 열쇠)·`link:/privacy`·외부는 호스트+경로), 그 밖의 절은 링크 경로(같은 페이지 `#…` 는 `link:/privacy`, 메일은 `link:mailto`, 외부는 `go.microsoft.com/fwlink/` 같은 호스트+경로).
- 추측한 지점(사람 없이 가장 보수적인 쪽 — 줄 끝은 버린 대안):
  - 열쇠 300 제한·추적 200 은 (영역, 열쇠) 짝을 센다 — §3.7 '짝으로 유일'·§3.5 크기 짐작(열쇠 300 × 3)과 맞게. 300 이 찬 뒤의 새 짝은 그 영역 아래 `(기타)`, 값이 모두 0 인 항목은 자리를 쓰지 않는다(필드를 만들지 않는다). v4 게이트 전 비콘은 영역·pv 만 더하고 열쇠도 세지 않는다. (버림: 영역과 무관한 열쇠 수 — 같은 열쇠가 영역 둘이면 필드가 상한을 넘는다.)
  - 추적 요소는 구간(화면)마다 새로 찾는다 — 탭이 바뀌면 내려놓고 다시 찾고(첫 200), 영역(052)은 그대로 문서 전체. 대시보드는 탭을 모두 마운트해 숨은 탭의 요소도 찾히지만(크기 0 — 보이지 않고 보내지 않는다) 지금 목록 전체가 200 안이다(약 100). 클릭은 열쇠가 있으면 추적 밖(행·200 넘음)이어도 센다(§3.2). (버림: 문서 전체에 한 번.)
  - 요소 클릭은 사람이 누른 것만 — `isTrusted` 가 false 인 누름(페이지 스크립트의 `.click()` — 랜딩·검색어 페이지 사건 표의 행 누름이 링크를 다시 누른다)은 요소에 세지 않는다. 라벨 안에서 입력칸 밖을 누르면 브라우저가 입력칸을 이어 누르므로 라벨 누름은 요소에 세지 않고 입력칸 누름으로 1 — 라벨의 열쇠는 그 입력칸의 열쇠(라벨에 `data-el` 이 있으면 그것), control 이 없는 라벨은 열쇠 없음. 영역 클릭은 052 그대로(둘 다 센다 — 남은 빚). (버림: 이벤트마다 세기 — 체크 상자·임계값이 단추의 두 배로 보인다.)
  - 종류는 role 이 태그보다 먼저(`<a href role=button>` → `btn`), input 의 type 이 없으면 `text`, select·textarea 의 이름 끝 대안은 태그 이름. (버림: 태그 먼저.)
  - 화면 글자로 만든 이름(aria-label·placeholder·보이는 글자)의 숫자는 `#` 로 가린다(숫자와 그 사이 쉼표·점을 한 덩어리로) — §2 '화면 글자의 숫자(가격 등) 보내지 않는다'·방침 2절. 링크 경로·name·type·`data-el` 은 그대로(화면 글자가 아니다). http(s) 가 아닌 링크는 주소 종류만(`link:mailto` — 메일 주소를 싣지 않는다). 이름은 `|`·제어 문자를 공백으로, 공백 하나로·앞뒤 뺌, 24자는 코드 포인트, 열쇠 전체는 64자(UTF-16 길이 — 서버의 글자 수보다 작거나 같다) 안으로 자른다. 꼴이 틀린 `data-el` 은 열쇠 없음(자동 열쇠로 넘어가지 않는다). (버림: 숫자를 그대로, §3.1 그대로의 메일 주소.)
  - 깊이 = floor((scrollY + innerHeight) / `documentElement.scrollHeight` × 10), 문서 끝 1px 안(소수 스크롤)·문서가 화면보다 짧으면 10. 가장 아래는 그림 회차와 pagehide 순간에 잰다. 깊이·떠난 곳은 pagehide 의 보냄에만(숨김·탭 바뀜 보냄은 null), 첫 pagehide 에 한 번(뒤로 가기 캐시로 돌아와 다시 떠나도 싣지 않는다 — 떠난 곳이 null 이었어도). 떠난 곳은 보이는 높이가 0 보다 큰 영역 중 가장 큰 것, 같으면 문서에서 앞. 보낼 것이 영역·요소·pv·d·x 모두 없을 때만 보내지 않는다(pagehide 는 d·x 만 실은 몸통을 보낸다). (버림: 처음 숨길 때 깊이 — 구간의 '가장 아래' 가 아니다.)
  - 마우스 올림: 머문 추적 요소가 바뀌면(`pointerover`) 끝내고, 창 밖(`pointerout` 의 relatedTarget 없음)·숨김·보냄·탭 바뀜에 끝. 다시 보인 문서는 다음 `pointerover` 부터. 5분 입력 규칙은 쓰지 않는다(§3.2 에 없다 — 30초 자르기가 같은 일을 한다).
  - 몸통 `e` 는 늘 (클릭, 보인 시간) 큰 순(같으면 처음 생긴 차례), 16KB 는 UTF-8 바이트로 잰다.
  - 서버 v2 검사: 키 여덟 그대로(052 의 엄격함 — 모르는 키·빠진 키 400), `e` 안 같은 (영역, 열쇠) 둘이면 400, 대시보드 화면의 `d` 가 null 이 아니면 400(§3.2), 열쇠의 짝 없는 대리 문자 400(Redis 에 UTF-8 로 못 쓴다). 상한 16,384 는 v1 에도. (버림: 모르는 키 무시·중복 합치기·대시보드 d 무시.)
  - 피드: `page` 가 목록 안이면 그 화면의 (기기)별 pv > 0 행에 052 키 + `exits`·`depth` + `elements`, 없거나 목록 밖이면 `elements` 키 없음(요소 필드는 읽고 버린다)·캐시는 (days, 없음) 칸을 같이 쓴다. `elements` 의 같은 값은 (영역, 열쇠) 순, `exits` 의 같은 n 은 영역 id 순, 정적 페이지 행의 `depth` 는 자료가 없어도 11칸 0. 응답 최상위 키는 052 그대로(page 를 되돌려 싣지 않는다).
  - `data-el` 값: §3.1 의 예(`period:1w`·`sort:kimp`) 대신 코드 id — `period:7d`(URL 의 `h.per` 와 같은 값)·`sort:val`(김프/역프 머리가 보기 기준에 따라 바뀌는 같은 열), 정렬 키는 소문자로(`sort:fxex`·`sort:ongoingsince`), 스프레드의 해외 거래소 체크 상자는 표시명을 소문자·영숫자로(`fx:gateio`). 기록 탭의 차트 국내 체크 상자는 `chart-dom:*`(같은 화면의 `dom:*` 와 열쇠가 겹치지 않게 — 계약 테스트가 화면 안 열쇠 하나뿐을 본다).
  - 범위: §3.1 의 조작·행 + 기록 탭 차트 카드 자체 `card:<fx>`(차트 위 누름·드래그·마우스 올림·보인 시간 — 카드의 읽기 줄 숫자가 자동 열쇠가 되지도 않게). 갭·선선갭 표의 행은 누를 수 없어 `row:` 를 달지 않았다(§3.1 은 스프레드·기록·입출금 레이더). 입출금 레이더의 최근 전송 표는 tx 링크마다 `link:etherscan.io/tx/…` 자동 열쇠가 100개 생기므로 `tx` 하나로. 대시보드 머리의 처리방침·설정 링크에도 §3.1 대로 붙였지만 052 영역 밖이라 세지 않는다(영역을 더하면 052 영역 규칙·053 이름표가 바뀐다).
  - 정적 페이지: 자동 열쇠가 같은 영역에서 겹치는 링크(같은 페이지 `#…`·`/app/?…` 둘)와 질문(h3 — summary 가 아니라 자동 열쇠가 없다. .qa 덩어리에 달면 답 안 링크의 열쇠가 질문 열쇠가 된다)에 붙였다. 랜딩·검색어 페이지 사건 표의 행은 `row:<심볼>`(표의 코인 행 — 클릭만), 랜딩 실시간 카드는 자리 `live:1`~`live:5`. 처리방침은 붙이지 않았다 — 032 의 test_privacy 가 동의 칸 마크업을 글자 그대로 묶는다(남은 빚). 검색어 페이지의 `dateModified`·sitemap 은 그대로(글이 안 바뀌었다).
  - 공유 조각: `SegOpt.el`·`segOpt` 넷째 인자·`NumField`/`ToggleBtn` 의 `el`·`Header` 넷째 칸(모두 선택) — 052 의 `TableFrame.area` 와 같은 꼴(주지 않으면 속성 없음, 모양·동작 변화 없음).
  - 계약 테스트는 대시보드를 화면별 열쇠 목록으로 대조한다(영역은 JSX 차례로 정할 수 없다 — 열쇠가 화면 안에서 하나뿐이면 (영역, 열쇠)도 하나뿐), 템플릿 열쇠(`row:*`·`fx:*`·`period:*` 등)는 값 목록을 소스에서 읽어 꼴을 본다.
  - 실행 중 함께 고친 스펙 절: 052 §3.3~§3.6 끝 한 줄씩·028 §3.1 몸통 상한 문장(§6). 다른 기능의 테스트 하나 — 033 의 `test_clarity.py` 설정 링크 정규식이 속성 없는 `<a href="/privacy#consent">` 만 받아 `[^>]*` 를 더했다. 세션 시작 때 main 이 앞서 있어(064) merge 커밋으로 받았다.
- 남은 빚:
  - 설계 세션 확인(머지 전, 헤드리스 Chrome — 052 방식, 이 세션은 브라우저·Docker 를 쓰지 않았다): 랜딩·대시보드에서 누르고 마우스를 올린 뒤 떠나면 v2 몸통의 `e`·`d`·`x`·Redis 필드·피드 `page=landing` 의 `elements`, 대시보드 (영역, 열쇠) 짝이 위 목록과 같은지, 라벨(체크 상자·임계값) 한 번 누름이 요소 1 인지, `nginx -t`(nginx.conf 를 고쳤다 — PR 본문).
  - 052 의 영역 클릭은 누름 이벤트마다라 라벨 누름은 영역 2, 스크립트의 `.click()` 도 영역 1 — 요소와 세는 뜻이 다르다(052 담당 제안: 영역도 사람이 한 번 누른 것만).
  - 처리방침 동의 체크 상자 셋이 `check:checkbox` 하나로 합쳐진다 — 032 의 test_privacy 를 고칠 때 칸 라벨에 `data-el`(예 `consent:collect`)을 붙이면 된다. 대시보드 머리의 처리방침·설정 링크는 영역 밖이라 세지 않는다(052 영역 규칙을 바꿀 때 함께).
  - 열쇠 300 제한은 api 메모리라 다시 뜨면 새로 센다(넘칠 수 있다). 깊이·떠난 곳은 pagehide 에만 가서 그 전에 페이지가 버려지면(휴대폰 배경 등) 빈다(하한). 마우스 올림은 다시 보인 문서에서 마우스가 다른 요소로 옮겨 가야 다시 센다.
  - 담당자 제안(PR 본문): 002·017·048·049·050(대시보드 탭)·022(랜딩)·044(검색어 페이지) — "`data-el` 속성을 붙였다(동작 변화 없음). 조작을 더하거나 바꾸면 `data-el` 도 함께, 065 이름표와 `tests/test_attention_elements.py` 목록도."
  - 065: 이름표는 위 요소 목록을 쓴다. 자동 열쇠는 화면 글자가 바뀌면 새 열쇠가 되어 옛 값은 90일 동안 '지금 화면에 없음'(§3.7).
