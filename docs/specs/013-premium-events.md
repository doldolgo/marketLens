# 013 — premium-events

상태: DONE | 의존: 001(collect — 틱), 005(history — `premium` 원값·`/history/*` 오류 계약·기록 탭), 009(tick-store), 011(health — 구간 추적·Influx 1점·기동 복원 패턴)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
"어느 코인이 언제부터 언제까지 김프(역프)였나"를 **저장 시점에 사건 단위로 감지해 1건 1행으로 남기고**, 기록 탭은 그 표를 읽기만 한다. 지금은 `/history/streaks` 가 요청마다 초 단위 원본을 전부 읽어 구간을 다시 계산하므로 전 코인 조회(`bulk`)는 6시간 창에서도 Influx 를 죽인다(2026-09-07 실측). 끝나면 기록 탭에 심볼을 치지 않아도 김프/역프 서브탭별로 전 코인 1주·1달·3달 사건 표가 즉시 뜨고(진행 중인 코인이 표시된다), 행을 고르면 그 코인의 사건 로그·타임라인이 보인다.

## 2. 범위
- 만드는 것: 사건 감지기(core — 틱 루프가 매초 부른다), Influx measurement `premium_event` 쓰기·60초 갱신·기동 시 복원, `GET /history/events`(`server/app/features/history/`), 기록 탭 화면(`web/src/features/history/` — 티커별 표·요약·타임라인·사건 로그를 이 API 로).
- 하지 않는 것: 지난 기록의 사건 일괄 생성(배포 시점부터 쌓인다 — 과거분은 `premium` 원본을 코인별로 나눠 도는 별도 스펙). 기준값 조정 UI(기준은 고정, §3.2). `/history/streaks`·`bulk`·`/premium` 변경(그대로 남고 화면은 안 쓴다). 사건 클릭 → 그 구간 차트(후속 014 — 코인 1개 × 사건 구간의 `premium` 조회는 가벼워 임의 구간 조회 엔드포인트 하나면 된다, 롤업 불필요). 알림.
- 바꾸는 기존 것:
  1. 001 틱 루프 — 매 틱을 사건 감지기에 넘긴다(011 이 이력 추적기에 넘기는 자리와 같다). 틱 순서·행 규칙 불변.
  2. 005 §3.6 기록 탭 — `/history/streaks` 사건 로그를 이 스펙의 화면으로 교체. 005 §2 의 "후속 스펙" 문구와 §7 남은 빚을 이 스펙으로 돌린다. 005 의 `features/history/api.ts`·`types.ts` 는 이 API 용으로 다시 쓴다.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001·009: 틱은 1초 주기. 틱 `rows` 의 행 = `(dom, fx, base, fwd, rev)`, `dom ∈ {upbit, bithumb}`, `fx = binance`, `fwd`·`rev` 는 **슬리피지 차감 전 원값 %**(005 §3.3). 국내 거래소의 USDT 시세가 없거나 호가가 없으면 그 조합은 그 틱에 **없다**. 틱 시각 `ts` 는 epoch 초.
- 005: `fwd`(kimp) 는 해외 매수→국내 매도 방향, `rev`(reverse) 는 국내 매수→해외 매도 방향. 둘 다 양수일 때 그 방향의 프리미엄이 있다. `/history/*` 오류 계약 — 503 `storage_unavailable`(Influx 불달·`INFLUX_TOKEN` 없음), 400 `invalid_request`(`end ≤ start`), 422(파라미터 검증). `start`·`end` 는 0 ≤ 값 ≤ 4,102,444,800, `start` 없으면 `end − 7일`, `end` 없으면 지금+1초. HTTP JSON 키는 camelCase.
- 011: 구간을 메모리에서 열고 닫으며 Influx 에 **열림·닫힘 시 1점**(같은 tag·time 으로 덮어써 field 를 합친다 — Influx 2.x 는 같은 key 재쓰기 시 field 합집합·새 값 우선). 기동 시 복원은 틱 루프 시작 **전**, 3초 상한, 실패하면 빈 상태로 시작하고 경고 1줄. 쓰기 실패는 로그 후 무시하되 순서를 보장한다.
- 002/003: 셸의 1.5초 `now`, 탭은 숨김만, 스프레드 행 클릭 → 선택 심볼 + 기록 탭 전환(초기 `'BTC'`). 거래소 표시명 `upbit→업비트` `bithumb→빗썸`.

### 3.2 사건 규칙 — 틱이 판정한다
사건은 조합 `(dom, fx, base, dir)` 마다 따로 본다. `dir ∈ {kimp, reverse}`, 값 = `dir` 가 `kimp` 면 `fwd`, `reverse` 면 `rev`. 기준은 고정이다:

| 이름 | 값 |
|---|---|
| 진입 | 1.0 |
| 종료 | 0.5 |
| 최소지속 | 60 |
| 결측허용 | 600 |

- **열기**: 열린 사건이 없는 조합의 값이 진입(1.0%) **이상**이면 그 틱 `ts` 에 연다. `max_percent`·`max_ts` 는 그 값·시각, `samples`=1.
- **유지**: 열린 사건의 값이 종료(0.5%) **초과**이면 이어진다 — 0.5 와 1.0 사이도 이어진다(히스테리시스: 기준선 근처 들락날락을 한 사건으로). `max_percent`·`max_ts` 갱신, `samples`+1, `last_ts`=틱 `ts`.
- **닫기**: 값이 종료(0.5%) **이하**가 된 첫 틱 `ts` 가 `end_ts`. `duration = end_ts − start_ts`.
- **버리기**: `duration ≤ 60` 이면 없던 것으로 한다(Influx 에 쓰지 않고, 열 때 쓴 점이 있으면 지운다 — §3.3). 1분을 못 넘긴 스파이크는 사건이 아니다.
- **결측**: 틱에 그 조합 행이 없으면 값이 없는 것이다(0 이 아니다). 마지막 관측 `last_ts` 로부터 600초 안에 행이 돌아오면 이어지고, 600초를 넘기면 `end_ts = last_ts` 로 닫는다(관측 못 한 시간은 사건에 넣지 않는다). 닫힌 뒤 돌아온 값이 1.0 이상이면 새 사건이다.
- 두 방향은 독립이다(같은 조합에 kimp·reverse 사건이 동시에 열릴 수는 없지만 — 둘 다 양수일 수 없다 — 규칙은 각각 적용한다).
- 메모리에 드는 것은 **열린 사건**뿐(조합 수 ≤ 490 × 2). 닫힌 사건은 Influx 가 진실이다.

### 3.3 Influx `premium_event` — 쓰기·갱신·복원
- measurement `premium_event`. tag `dom`·`fx`·`base`·`dir`, time = `start_ts`(초). field `end_ts`(int 초, **진행 중이면 0**)·`duration_seconds`(int, 진행 중 0)·`max_percent`(float)·`max_ts`(int 초)·`last_ts`(int 초)·`samples`(int)·`enter_percent`(float 1.0)·`exit_percent`(float 0.5). 한 사건 = 점 1개, 유일키 = (dom, fx, base, dir, start_ts). 기준값을 점에 같이 남기는 것은 나중에 기준이 바뀌어도 과거 사건의 의미가 남게 하기 위해서다.
- 쓰는 시점 세 가지 — 매초 쓰지 않는다:
  1. **열린 지 60초를 넘긴 순간** 1점(그 전엔 메모리에만 — 1분 못 넘기고 닫히는 사건은 Influx 를 건드리지 않는다).
  2. 열린 채로 있는 동안 **60초마다** 같은 키로 덮어써 `max_percent`·`max_ts`·`last_ts`·`samples` 를 갱신한다(재기동 복원의 정확도를 위해 — §복원).
  3. **닫힐 때** 같은 키로 `end_ts`·`duration_seconds`·최종값을 쓴다.
- 버리기(§3.2)는 열린 지 60초 전에만 일어나므로 지울 점이 없다 — `duration ≤ 60` 인 사건은 60초 갱신 시점(1)에도 도달하지 못한다. 단 결측으로 닫혀 `end_ts = last_ts` 가 되면 `duration` 이 60 이하로 줄 수 있다(예: 열리고 30초 관측 뒤 끊김). 그 경우 이미 쓴 점을 `end_ts`·`duration_seconds` 를 채워 닫되 응답에서는 `duration ≤ 60` 을 빼지 않는다 — 관측한 만큼은 사실이므로 남긴다. **버리기는 관측 중 닫힌 사건에만 적용한다.**
- 쓰기 실패는 로그 1줄 후 **다음 60초 회차에 재시도**한다(열림·갱신·닫힘 모두 같은 키 덮어쓰기라 재시도가 안전하다). 닫힌 사건의 미전송분은 메모리 재시도 목록에 두고 회차마다 보낸다(상한 1,000건, 넘치면 오래된 것부터 버리고 로그). **실패한 뒤 60초가 지나기 전에는 새 사건이 열리거나 닫혀도 다시 쓰지 않는다** — Influx 쓰기 1회는 동기이고 타임아웃이 60초라, 불통 중에 사건마다 즉시 재시도하면 실패 호출이 쓰기 스레드를 연달아 막아 복구 뒤에도 밀린 쓰기가 한참 이어진다. 60초 안의 지연은 화면(60초 재조회)이 어차피 못 본다.
- **기동 시 복원**(틱 루프 시작 전, 3초 상한): `start_ts` 가 최근 7일이고 `end_ts == 0` 인 점을 읽는다. `지금 − last_ts > 600` 이면 `end_ts = last_ts` 로 닫아 쓰기 큐 맨 앞에 넣고(재기동마다 다시 열리지 않게 — 복원 안에서 동기로 쓰지 않는 것은 쓰기 1회가 최대 60초라 3초 상한이 무의미해지기 때문이고, 쓰기 태스크가 복원 직후 시작하므로 실제 지연은 수 초다), 아니면 진행 중으로 메모리에 올려 첫 틱부터 이어 본다. 600초를 재기동에도 쓰는 이유: 서버가 죽어 있던 시간은 감지기에게 "행을 못 본 시간" 과 같다 — 결측과 다른 숫자를 두면 같은 사건이 죽었다 켜졌는지 스트림이 끊겼는지에 따라 다르게 판정된다. 같은 조합에 `end_ts == 0` 이 둘 이상이면 `start_ts` 가 가장 늦은 것만 살리고 나머지는 `end_ts = last_ts` 로 닫아 쓴다. Influx 가 없으면(`INFLUX_TOKEN` 없음·불달) 빈 상태로 시작하고 경고 1줄 — 감지는 계속하되 쓰기만 실패로 남는다.

### 3.4 `GET /history/events`
Influx 의 닫힌 사건 + 메모리의 진행 중 사건. 거래소 호출 0회. 같은 키가 양쪽에 있으면 메모리가 이긴다(60초 갱신 사이의 최신값). Influx 에 `end_ts == 0` 인데 메모리에 없는 **고아 점**(기동 시 Influx 불통으로 복원을 못 했거나, 죽어 있는 동안 아무도 안 닫은 사건)은 `endTs = last_ts`·`durationSeconds = last_ts − start_ts` 로 **닫힌 사건처럼** 싣는다 — 복원 규칙(§3.3)과 같은 판정이라 복원 전·후의 화면이 같고, 응답에서 빼면 실제 있었던 사건이 사라져 횟수·점유율이 틀리며, 진행 중으로 내면 끝난 사건이 영원히 `진행 중` 으로 남는다.
`?start&end&dom&dir&base` — 전부 선택. `start`/`end` 규칙은 §3.1 복사값(`end − 7일` / 지금+1초, `end ≤ start` 400). `dom` 없으면 두 국내 거래소 다, `dir` 없으면 두 방향 다, `base` 는 `^[A-Za-z0-9]{1,20}$`(대문자 정규화). 구간 판정은 **`start_ts` 기준** — `start ≤ start_ts < end` 인 사건만(구간 전에 시작해 구간 안에서 끝난 사건은 빠진다 — 단순함을 택했다). 진행 중 사건은 `start_ts` 가 구간 안이고 **열린 지 60초를 넘긴 것만** 싣는다.
```json
{
  "startTs": 1788160000, "endTs": 1788764800, "count": 2, "fetchedAt": 1788764800123,
  "events": [
    {"base": "SOPH", "dom": "upbit", "fx": "binance", "dir": "kimp",
     "startTs": 1788760000, "endTs": null, "durationSeconds": 4800, "ongoing": true,
     "maxPercent": 2.31, "maxTs": 1788761200, "samples": 4790},
    {"base": "BONK", "dom": "bithumb", "fx": "binance", "dir": "reverse",
     "startTs": 1788700000, "endTs": 1788700900, "durationSeconds": 900, "ongoing": false,
     "maxPercent": 1.42, "maxTs": 1788700300, "samples": 899}
  ]
}
```
- 진행 중은 `endTs null`·`ongoing true`·`durationSeconds = 지금 − startTs`. 닫힌 사건은 저장값 그대로.
- 정렬 `startTs` 내림차순, 같으면 `base` 오름차순. 기록 없으면 404 가 아니라 빈 `events`(bulk 와 같다 — 전 코인 조회는 "없음" 이 정상 상태다).
- 오류: 503 `storage_unavailable`(Influx 불달·토큰 없음 — 진행 중 사건만으로 200 을 만들지 않는다, 반쪽 답을 주지 않기 위해), 400·422 는 §3.1.
- 응답 크기 감: 전 코인 1주에 수백~수천 건, 1건 ≈ 200B. 압축은 앱 전역 gzip.

### 3.5 web — 기록 탭
데이터는 `GET /history/events` 하나. **방향 서브탭·기간·거래소**가 바뀔 때 1회 조회(디바운스 400ms·진행 중 요청 취소, 005 §3.6 방식), 그리고 **60초마다** 같은 조건으로 다시 불러 진행 중 사건의 지속·최대값을 갱신한다. 심볼 선택은 클라이언트에서 거른다. 항상 `start`·`end`·`dir` 를 붙인다. 셸이 탭을 숨길 뿐 내리지 않으므로(002) 다른 탭을 보는 동안에도 60초 재조회는 계속 돈다 — 60초에 1번·수백 KB 이하라 부담이 없고, 돌아왔을 때 최신 표가 바로 떠 있다.
- 방향 서브탭: 탭 맨 위에 `김프 | 역프`(기본 김프) → `dir=kimp|reverse`. 한 화면은 한 방향만 보여준다 — 한 코인에 김프·역프를 나란히 적지 않는다. 서브탭 색은 김프 = POS 색, 역프 = NEG 색(스프레드 탭 관례).
- 필터바: 기간 `1주/1달/3달`(기본 1주), 거래소 `전체/업비트/빗썸`(전체 = `dom` 생략). 기준 입력은 없다 — 우측 설명 `사건 = 원값 {김프|역프} 1.0% 진입 → 0.5% 이탈, 1분 이하 제외 · 기간 내 N건`.
- 좌 카드 "티커별 {김프|역프} 사건 · {기간} — 열 클릭으로 정렬": 열 `티커|상태|횟수|최대 지속|평균 지속|최대 스프레드|평균 스프레드|최신`, 응답을 심볼별로 집계(거래소 전체면 두 거래소 사건을 합쳐 센다), 상위 30행, 헤더 클릭 정렬(재클릭 반전, 기본 횟수 내림차순). **`상태`** = 그 심볼에 진행 중 사건이 있으면 accent pill `진행 중 · {경과}`(경과 = 지금 − 그 사건 시작, 거래소 전체면 어느 한 거래소라도), 없으면 `끝남`(neutral 색). 상태 열 정렬은 진행 중이 먼저. `최신` = 가장 최근 사건 시작 시각의 경과 표기. 평균 지속은 끝난 사건만으로 낸다(진행 중 사건은 길이가 미정이라 뺀다 — 최대 지속·점유율에는 지금까지로 넣는다). 행 클릭 → 선택 심볼. 선택 행 accent 배경. 비면 `기간 내 사건 없음`.
- 우 column: 요약 카드(선택 심볼 + 상태 pill(`진행 중 · {경과}` / `끝남`), 총 사건·평균 지속(끝난 사건만)·최장 지속·기간 점유율 = Σ지속/기간 — 진행 중 사건의 지속은 지금까지로 센다). 타임라인 **1줄**(현재 방향 색, 위치·폭 = 기간 대비, 최소폭 보장, 진행 중은 지금까지, 축 라벨 5등분 `M/D`). "사건 로그 · {심볼} 최근 20건"(거래소|시작|종료|지속|최대 스프레드 — 현재 방향 색). 진행 중 행은 종료 칸이 accent 색 `진행 중`, 지속은 지금까지, 행 배경을 옅은 accent 로 — 끝난 행과 한눈에 구분되게.
- 선택 심볼: 초기 `'BTC'`, 스프레드 행 클릭 피벗 그대로. 서브탭을 바꿔도 선택 심볼은 유지한다. 선택 심볼에 사건이 없으면 요약은 `–`, 로그는 `기간 내 사건 없음`.
- 상태: 조회 중 `조회 중…`(직전 유지), 오류 `기록을 불러오지 못했습니다 (HTTP n)`. 표 구조·색·간격은 `docs/design/reference/tabs/HistoryTab.tsx`·`theme.css`(김프/역프 열 분리 대신 서브탭 — 이 스펙이 참조 디자인을 개정한다).

## 4. 검증
- 열기: 값 1.0 정확히 → 연다, 0.99 → 안 연다
- 유지·닫기: `1.2 0.8 0.6 0.51 0.5` → 0.5 에서 닫힌다(0.51 까지 이어짐); `1.2 0.4 1.2` → 두 사건
- 버리기: 열린 뒤 60초에 닫힘 → 기록 없음, 61초 → 기록 1건
- 결측: 행이 599초 비었다 돌아오면 이어진다, 601초면 `end_ts = last_ts` 로 닫힌다; 그 뒤 값 1.0 이상이면 새 사건
- 결측으로 닫혀 `duration ≤ 60` 이 된 사건은 (이미 쓴 점이 있으면) 남는다
- `max_percent`·`max_ts` 는 사건 안 최댓값·그 시각, `samples` 는 관측 틱 수
- 조합·방향 독립: 업비트 SOPH kimp 와 빗썸 SOPH kimp 는 별개 사건
- Influx 쓰기: 열린 지 60초 전엔 0점, 넘기면 1점(`end_ts 0`), 열린 채 60초마다 갱신, 닫힐 때 `end_ts`·`duration_seconds` 채움; 같은 키 덮어쓰기
- 쓰기 실패 → 다음 회차 재시도, 재시도 목록 상한 1,000
- 복원: `end_ts 0` 이고 `last_ts` 가 601초 전 → 닫아서 쓴다; 300초 전 → 진행 중으로 올라온다; 같은 조합 둘 → 늦은 것만
- Influx 없이 기동 → 감지는 돌고 `/history/events` 503
- `/history/events`: `start` 없으면 `startTs == endTs − 604800`; `dom`·`dir`·`base` 필터; 진행 중은 `endTs null`·`ongoing true`·60초 미만 사건은 안 실림; 구간 전에 시작한 사건은 빠짐; 정렬; 빈 `events` 200; `end ≤ start` 400; `base=btc` → `BTC`
- web 집계 규칙(수동 또는 순수 함수 테스트): 횟수 = 기간 안 시작한 사건 수(거래소 전체면 합산), 평균 지속은 끝난 사건만, 최대 지속·점유율은 진행 중을 지금까지로 포함, 상태 열은 진행 중 사건이 하나라도 있으면 `진행 중 · 경과`
- 수동(EC2): 배포 후 알트 하나가 1% 를 넘는 사건이 열리고 60초 뒤 Influx UI 에 `premium_event` 1점, 닫힌 뒤 `end_ts` 채워짐. 기록 탭 김프 서브탭 1주 표에 그 코인이 `진행 중 · N분` 상태로 뜨고(닫히면 `끝남`) 행 클릭 → 우측 로그에 시작·종료, 역프 서브탭으로 바꾸면 `dir=reverse` 로 재조회되고 선택 심볼은 유지. 서버 재기동 직후 진행 중이던 사건이 표에 그대로 남는다. 요청 URL 에 `start`·`end`·`dir` 가 있다. 서버 테스트·lint, web build·lint 통과.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
실행 세션 2026-09-07. 아래 전부 통과.
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
# 504 passed (013 추가분: tests/test_premium_events.py 8 · tests/test_premium_events_store.py 10 · features/history/tests/test_events_api.py 11)
cd web && npm run lint && npm run build
# oxlint 0 · tsc -b && vite build ✓
node <레포 밖 일회성 스크립트>.ts   # stats.ts 순수 집계 — §4 web 집계 규칙 13개 단언 통과 (러너 없음, dev-setup.md)
# 로컬 스모크 1 — server/.env 없음(Influx·Redis 없이 기동), 거래소 도메인 200
cd server && .venv/bin/uvicorn app.main:app --port 8020
curl -s localhost:8020/health                          # {"status":"ok"}
curl -s "localhost:8020/history/events?dir=kimp"       # 503 storage_unavailable
curl -s "localhost:8020/history/events?dir=up"         # 422
curl -s localhost:8020/spreads                          # rows 478 — 틱 루프가 감지기를 물고 돈다(예외 로그 없음)
# 기동 로그: "Influx 가 없어 사건을 복원하지 않는다 — 빈 상태로 시작"
# 로컬 스모크 2 — server/.env(INFLUX_TOKEN) + dev compose(Influx 2.7·Redis 7) 위, 실거래소 수집
docker compose --env-file server/.env -f docker-compose.dev.yml up -d
cd server && .venv/bin/uvicorn app.main:app --port 8020        # 로그 "사건 복원: 진행 중 0건, 닫음 0건"
curl -s "localhost:8020/history/events?dir=kimp"                # 기동 61초 뒤 count 4 → ongoing true·endTs null·durationSeconds 61·samples 62
docker compose -f docker-compose.dev.yml exec -T influxdb sh -c 'influx query --org marketlens --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" "from(bucket:\"marketlens\") |> range(start:-1h) |> filter(fn:(r)=>r._measurement==\"premium_event\")"'
# 61초 시점 점 1개/사건(samples 62, end_ts 0) → 60초 뒤 같은 키 갱신(samples 98, last_ts 전진, 점 수 그대로)
# 서버 재기동 → 로그 "사건 복원: 진행 중 37건, 닫음 0건", /history/events 의 사건이 같은 startTs·ongoing true 로 남음
# 닫힘: ESP 빗썸 역프가 0.5% 이하로 내려와 endTs·durationSeconds 117·samples 117 로 응답(Influx 같은 키에 end_ts 채워짐)
# web: VITE_API_BASE=http://localhost:8020 npx vite --port 8030 → Playwright 로 기록 탭 — 김프 서브탭 표 7행·상태 `진행 중 · N분`,
#   행 클릭 → 요약·로그(빗썸·업비트 2행 진행 중), 역프 서브탭 → dir=reverse 재조회·선택 심볼 유지, 요청 URL 에 start·end·dir
```

## 6. 갱신할 문서
- `docs/context/status.md` — history 행 web 칸을 "기록 탭 = `/history/events`(전 코인 사건 표·선택 심볼 로그·타임라인, 60초 재조회)" 로, server 칸에 "사건 감지기·`premium_event`·`/history/events`" 추가. 알려진 빚에 "과거 `premium` 의 사건 일괄 생성 미완(배포 시점부터만)" 추가.
- `CLAUDE.md` — 스펙 인덱스 013 행 상태 → DONE.
- `docs/context/architecture.md` — 데이터 흐름 그림에 "틱 → 사건 감지기 → `premium_event`(열림·60초·닫힘)" 한 줄, "현재 구조" 에 premium-events 항목(감지기는 틱 루프가 쓰므로 core, 읽기 API 는 `features/history`).
- `docs/context/product.md` — 용어 절에 "사건(event): 김프/역프 원값이 1.0% 이상으로 진입해 0.5% 이하로 이탈할 때까지, 1분 초과인 것. 저장 시점에 감지한다" 추가, streak 정의에 "`/history/streaks` 전용(화면은 안 쓴다)" 덧붙임. 기능 목록 history 행에 "사건 표" 추가.
- `docs/context/db.md` — measurement 절에 `premium_event` 정의(§3.3 그대로), 쓰는 쪽에 "사건 감지기(013): 열린 지 60초·60초마다·닫힐 때", 읽는 쪽에 `/history/events` 와 기동 시 복원(7일, 3초 상한).
- `docs/specs/005-history.md` — §2 "하지 않는 것" 의 후속 스펙 문구를 013 으로, §3.6 을 "기록 탭은 013 §3.5. 이 스펙의 web 몫은 없다" 로 줄이고, §7 남은 빚의 화면 항목 삭제.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `server/app/core/premium_events.py` — `PremiumEventDetector`(§3.2 판정·§3.3 쓰기 시점·복원·쓰기 태스크)와 `PremiumEvent` 데이터클래스. 기준값·상한은 모듈 상수. 미전송 점은 `(키, start_ts)` → 점 맵 하나에 모으고(열림·갱신·닫힘·실패분 전부, 같은 키는 최신 상태로 교체) 쓰기 태스크가 점이 생기면 즉시·없어도 60초마다 한 번의 `write` 로 보낸다.
  - `server/app/core/influx.py` — `PremiumEventRow`·`premium_event_point`·`query_premium_events`(닫힌 사건 조회와 복원 조회가 같은 메서드, start_ts 내림차순). `server/app/core/contracts.py` — `EventSink`. `server/app/core/ticks.py` — `TickLoop(events=…)` 가 매 틱 현재 틱을 넘긴다.
  - `server/app/main.py` — 011 복원 다음에 감지기 복원·쓰기 태스크·`app.state.premium_events`, 종료 시 태스크 취소.
  - `server/app/features/history/` — `models.py` `EventOut`·`EventsResponse`, `service.py` `EventReader`·`build_events`, `router.py` `GET /history/events`, `tests/test_events_api.py` + `tests/helpers.py`(`seed_event`·`query_premium_events`·`events` 인자).
  - `server/tests/premium_event_fakes.py`·`test_premium_events.py`·`test_premium_events_store.py` — 감지 규칙 / 쓰기·재시도·복원·틱 루프 연결.
  - `web/src/features/history/types.ts`·`api.ts`(60초 재조회)·`stats.ts`(순수 집계·정렬·요약)·`Tab.tsx`(서브탭·표·요약·타임라인·로그). `web/src/shared/config.ts` — `HISTORY_EVENTS_POLL_MS`.
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - "열린 지 60초를 넘긴 순간" 은 행이 있든 없든 **매 틱의 `ts − start_ts > 60`** 으로 본다. 그래서 열리고 30초 뒤 행이 끊긴 사건도 60초 시점에 점이 생기고, 600초 뒤 결측으로 닫힐 때 `duration 30` 으로 남는다(§3.3 "관측한 만큼은 사실" 과 일치).
  - 60초 갱신은 틱 시각 기준(`ts − 마지막 갱신 틱 ≥ 60`)이라 결정적이고 테스트가 시계를 안 흔든다. 갱신 점은 쓰기 태스크를 깨워 바로 나간다.
  - 미전송 상한 1,000 은 닫힌 사건만이 아니라 맵 전체(갱신 점 포함)에 건다 — 갱신 점은 다음 회차에 다시 만들어지므로 버려도 무해하다.
  - 종료 시 미전송 점을 쓰지 않는다(011 과 같다) — Influx 불통이면 종료가 최대 60초 늘어나기 때문. 못 쓴 닫힘은 다음 기동의 복원(600초 규칙)이나 §3.4 고아 점 규칙이 메운다.
  - `/history/events` 의 `end` 는 `end ≤ start` 만 400 이고, 005 `build_streaks` 의 `end ≤ 0` 특수 가드는 따르지 않았다(`bulk` 와 같다).
  - 복원 조회는 `end_ts` 필터 없이 7일 전부를 읽고 Python 에서 `end_ts == 0` 을 고른다 — Flux 한 줄을 줄이고 조회 메서드를 하나로 쓰기 위해서. 7일에 수천 건이라 가볍다.
  - 웹 `평균 스프레드` 는 사건별 `maxPercent` 의 평균(진행 중 포함 — 지금까지의 최댓값도 사실). 상태 열의 경과는 진행 중 사건 중 가장 이른 시작 기준. 지속 표기에 `일` 단위를 추가했다(3달 창).
  - 방향 서브탭·수치 색은 `pctColor(±1)` 로 김프 = `--color-up`, 역프 = `--color-down`(참조 디자인의 POS/NEG 에 해당).
- 실행 중 함께 고친 스펙 절: §3.3 — 쓰기 실패 뒤 60초 재시도 금지(이유), 복원 시 닫는 점은 쓰기 큐 맨 앞(이유), 재기동에 600초를 쓰는 이유. §3.4 — 같은 키는 메모리 우선, 고아 점은 `last_ts` 로 닫힌 것처럼(이유). §3.5 — 숨김 탭에서도 60초 재조회 계속(이유). 005 §2·§3.6·§4·§7 을 013 으로 돌렸다.
- 남은 빚:
  - §4 수동 항목은 전부 로컬 dev compose + 실거래소 수집으로 확인했다(§5). EC2 에서는 배포 뒤 Influx UI 에서 `premium_event` 가 쌓이는지만 한 번 본다.
  - web 집계 규칙 검증 스크립트는 레포 밖 일회성(러너 미도입 — conventions.md). 러너 스펙이 오면 `stats.ts` 단언 13개를 옮긴다.
  - 사건 클릭 → 구간 차트(014). 과거 `premium` 사건 일괄 생성(별도 스펙, status.md 알려진 빚).
  - 사건 점에 입출금 이력 없음(2026-09-07 논의, 후속으로 미룸): 방향 경로(김프 `wdFx→depDom`·역프 `wdDom→depFx`) 상태가 바뀐 틱의 `시각:상태(1/0/−1)` 를 문자열 필드로 누적 + 막힌 초 합계, 값은 메모리의 006 캐시(±60초), 화면은 로그 열 1개·차트(014)에 겹쳐 그리기. 같은 키 덮어쓰기라 스냅샷만 쓰면 마지막 상태만 남는다 — 그래서 누적 문자열이어야 한다.
