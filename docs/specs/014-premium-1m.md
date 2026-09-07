# 014 — premium-1m

상태: TODO | 의존: 001(collect — 틱), 005(history — `/history/*` 오류 계약), 006(wallet-status — 행의 입출금 3상태), 009(tick-store — Redis 틱 레코드 모양 불변), 013(premium-events — Influx 1점 쓰기·재시도 패턴, 기록 탭 차트 카드 시안)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
기록 탭의 차트 카드(013 §3.5 에서 mock 으로 그린 시안)를 **실데이터**로 바꾼다. 서버가 틱에서 (국내, 해외, 코인) 조합마다 **1분 1점**(김프·역프 OHLC, 가격 종가 3개, 입출금 상태, 막힌 초)을 만들어 Influx 에 쌓고, 화면은 그 점을 **임의 구간(최대 하루)** 으로 읽어 그린다. 초 단위 `premium` 은 조회에 쓰지 않는다 — 초 단위 원본을 요청마다 접는 방식은 6시간 창에서 Influx 를 죽인 전적이 있다(013 §1). 끝나면 심볼을 치면 그 코인의 김프 캔들·USDT 기준 가격 선·입출금 띠가 배포 시점부터의 실제 값으로 보이고, 왼쪽으로 끌면 하루씩 과거가 붙는다.

## 2. 범위
- 만드는 것: 1분 집계기(core — 틱 루프가 매초 부른다), Influx measurement `premium_1m` 쓰기·재시도, `GET /history/candles`(`server/app/features/history/`), 기록 탭 차트 카드의 실데이터 연결(`web/src/features/history/`).
- 하지 않는 것: 과거분 생성(배포 시점부터 쌓인다 — 초 단위 `premium` 에는 가격이 없어 완전한 1분 점을 만들 수 없다). 상위봉의 서버 롤업(1m 을 클라이언트가 접는다 — 013 시안 그대로). 사건 로그 행 클릭 → 차트 이동(후속). 1초 가격 저장. 해외 거래소 추가(서버가 수집하는 해외 거래소는 binance 뿐 — 화면의 선택지도 binance 만 남긴다). `/history/premium`·`streaks`·`bulk`·`events` 변경.
- 바꾸는 기존 것:
  1. 001 §3.6-2 틱 행 — `{dom, fx, base, fwd, rev}` 에 **가격 3개·입출금 4상태**를 더한다(§3.2). 009 의 Redis 틱 레코드·Influx `premium` 은 **그대로**(새 필드는 직렬화하지 않는다) — 1분 점은 메모리에서 만들고, 초 단위 원본에는 가격을 남기지 않기로 했다(용량 5배).
  2. 001 틱 루프 — 매 틱을 집계기에 넘긴다(013 감지기 다음 자리).
  3. 013 §3.5 마지막 항목 — 차트 카드의 mock·`MOCK` 배지를 없애고 이 스펙의 API 로.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001·009: 틱은 1초 주기, `ts` 는 epoch 초. 틱 `rows` 의 행 = 자격(국내×해외 다른 거래소, 양쪽 최우선 호가, 그 국내 거래소 자신의 USDT 시세, 여섯 값 > 0)을 통과한 조합만. `dom ∈ {upbit, bithumb}`, `fx = binance`. 자격 미달 조합은 그 틱에 **없다**. 국내 행의 `price` = 마지막 체결가(없으면 최우선 호가 중간값), 해외 행도 같다. 국내 거래소의 USDT 시세 = `rate.ask`(KRW-USDT 최우선 매도호가)·`rate.bid`(최우선 매수호가).
- 006: 행의 `deposit_enabled`·`withdrawal_enabled` 는 3상태(`true`/`false`/`None`=모름 — 조회 실패 시). 60초마다 갱신, 국내·해외 세 거래소 모두.
- 005·013: `/history/*` 오류 계약 — 503 `storage_unavailable`(Influx 불달·`INFLUX_TOKEN` 없음), 400 `invalid_request`, 422(파라미터 검증). `start`·`end` 는 0 ≤ 값 ≤ 4,102,444,800. HTTP JSON 키는 camelCase. Influx 는 같은 (measurement, tag, time) 을 덮어쓴다 — 재시도 안전성의 근거. 쓰기 1회는 동기·타임아웃 60초.
- 013: 쓰기 실패는 미전송 맵에 두고 다음 60초 회차에 재시도, **실패 뒤 60초 안에는 다시 쓰지 않는다**(불통 중 쓰기 스레드가 연달아 막히는 것을 피하기 위해).
- 013 §3.5 차트 카드 시안: 심볼 검색 → Enter, 봉 `1m 3m 5m 15m 30m 1h 4h 1d`(1m 을 클라이언트가 KST 정렬로 접는다 — `%` 는 OHLC, 가격·입출금은 마지막 분 값, 막힌 초는 합), 국내·해외 거래소 체크박스 다중 선택(쌍 1개 = 캔들, 여럿 = 쌍별 종가 선), 진입 1.0·이탈 0.5·0 기준선, 사건 음영(`/history/events`), USDT 기준 가격 선(국내는 원화 ÷ 환율), 입출금 띠, 처음 360봉·왼쪽 끌면 하루씩 과거 로드(상한 30일).

### 3.2 틱 행 확장 — 집계기가 읽는 값
틱 행에 다음을 더한다. 값은 틱을 만드는 그 순간의 메모리 행에서 온다(추가 조회 없음).

| 이름 | 값 |
|---|---|
| dom_price | 국내 `price` |
| fx_price | 해외 `price` |
| rate | (ask+bid)/2 |
| dom_dep | 국내 입금 |
| dom_wd | 국내 출금 |
| fx_dep | 해외 입금 |
| fx_wd | 해외 출금 |

`rate` 는 그 국내 거래소의 USDT 중간값(원). 입출금 4개는 3상태 그대로(`None` 유지). Redis 레코드(009 §3.3)·`premium` 점에는 넣지 않는다.

### 3.3 1분 집계 규칙
- 분 = `ts // 60 * 60`(UTC 벽시계). 조합 `(dom, fx, base)` 마다 **그 분에 행이 있던 틱만** 모은다.
- `fwd`·`rev` 각각 OHLC: 시가 = 그 분 첫 행, 고·저 = 최대·최소, 종가 = 마지막 행. 가격 3개·입출금 4상태 = 마지막 행 값. `samples` = 그 분의 행 수(1~60).
- **막힌 초** 두 개: `blocked_fwd_sec` = `fx_wd == False` 또는 `dom_dep == False` 인 행 수, `blocked_rev_sec` = `dom_wd == False` 또는 `fx_dep == False` 인 행 수. `None`(모름)은 막힘으로 세지 않는다 — 조회 실패를 막힘으로 그리면 거짓 경고가 된다. 김프는 해외 출금 → 국내 입금 경로, 역프는 국내 출금 → 해외 입금 경로(013 §7).
- **분 닫힘**: 어느 틱의 `ts` 가 열린 분 + 60 이상이 되는 순간 그 분의 조합 전부를 점으로 만든다(조합 ≈ 490, 한 번의 `write`). 그 분에 행이 한 번도 없던 조합은 점이 없다. 틱이 건너뛰어 여러 분이 지나면 열려 있던 분만 닫는다(빈 분은 없는 것).
- **재기동**: 진행 중이던 분은 버린다(재기동 후 첫 분은 `samples` 가 60 미만인 채로 남는다 — 관측한 만큼만 사실). 복원 없음.

### 3.4 Influx `premium_1m` — 쓰기·재시도
- measurement `premium_1m`. tag `dom`·`fx`·`base`, time = 분 시작(초). field `fwd_o fwd_h fwd_l fwd_c rev_o rev_h rev_l rev_c`(float %, 원값)·`krw`(float, 국내 종가 원)·`usdt`(float, 해외 종가)·`rate`(float, USDT 중간값 원)·`dom_dep dom_wd fx_dep fx_wd`(int: 1 가능·0 불가·−1 모름)·`blocked_fwd_sec blocked_rev_sec`(int 0~60)·`samples`(int). 유일키 = (dom, fx, base, 분). 하루 ≈ 70만 점(초 단위 `premium` 의 1/60).
- 쓰기: 분이 닫힐 때 점 전부를 미전송 맵(키 = 유일키)에 넣고 쓰기 태스크가 한 번에 쓴다(013 과 같은 태스크 모양 — 점이 생기면 즉시, 없어도 60초 회차). 실패는 로그 1줄, 맵에 남겨 다음 회차 재시도, **실패 뒤 60초 안엔 안 쓴다**. 맵 상한 **10,000점**(≈20분치), 넘치면 오래된 분부터 버리고 로그. 종료 시 미전송분은 쓰지 않는다(013 과 같다).
- Influx 없이(`INFLUX_TOKEN` 없음·불통) 기동하면 집계는 돌고 쓰기만 실패로 남는다 — 경고 1줄.

### 3.5 `GET /history/candles`
Influx `premium_1m` 만 읽는다. 진행 중인 분(메모리)은 싣지 않는다 — 화면은 어차피 현재 분을 그리지 않는다. 거래소 호출 0회.
`?base&dom&fx&dir&start&end` — `base` 필수(`^[A-Za-z0-9]{1,20}$`, 대문자 정규화), `dom` 기본 `upbit`(`upbit|bithumb`), `fx` 기본 `binance`(값도 `binance` 만), `dir` 기본 `kimp`(`kimp|reverse`), `end` 없으면 지금, `start` 없으면 `end − 86400`. **`end − start > 86400` 이면 400 `invalid_request`**("window exceeds 1 day") — 화면은 하루 청크로만 부르고, 상한이 있어야 실수로 30일을 한 번에 읽는 호출이 Influx 를 못 건드린다. `end ≤ start` 400. 구간 판정 `start ≤ 분 < end`.
```json
{
  "base": "BTC", "dom": "upbit", "fx": "binance", "dir": "kimp",
  "startTs": 1788739200, "endTs": 1788825600, "count": 1, "fetchedAt": 1788825600123,
  "candles": [
    {"ts": 1788739200, "open": 0.62, "high": 0.71, "low": 0.58, "close": 0.66,
     "krw": 168450000, "usdt": 112010.5, "fxRate": 1502.5,
     "depositOk": true, "withdrawOk": null, "blockedSec": 0, "samples": 60}
  ]
}
```
- `open high low close` = `dir` 가 `kimp` 면 `fwd_*`, `reverse` 면 `rev_*`. `fxRate` = `rate`.
- `depositOk`·`withdrawOk` 는 **방향 경로**의 두 끝: `kimp` → `withdrawOk = fx_wd`·`depositOk = dom_dep`, `reverse` → `withdrawOk = dom_wd`·`depositOk = fx_dep`. 저장값 1 → `true`, 0 → `false`, −1 → `null`. `blockedSec` = 방향의 `blocked_*_sec`.
- 정렬 `ts` 오름차순. 기록 없으면 404 가 아니라 빈 `candles`(배포 전 날짜·상장 전 코인은 "없음" 이 정상).
- 오류: 503 `storage_unavailable`, 400·422 는 §3.1. 응답 크기 감: 하루 1,440건 × ≈150B, 앱 전역 gzip.

### 3.6 web — 차트 카드 실데이터
- 데이터 = 선택된 (국내 × 해외) 쌍마다 `GET /history/candles` 를 **UTC 하루 청크**(`start = 자정, end = 자정 + 86400`) 단위로 부른다. 청크는 `(dom, fx, base, dir, 자정)` 키로 메모리에 들고 있어 심볼·방향·쌍·봉을 바꿔도 **없는 청크만** 새로 부른다. 처음엔 `INITIAL_BARS × 봉 초`를 덮는 일수(013 시안 규칙), 왼쪽으로 끌면 하루 추가(상한 30일 그대로). 요청은 디바운스 400ms·직전 요청 취소(013 §3.5 방식). 하루 청크 안의 봉만 요청하므로 400 은 나올 수 없다.
- **오늘 청크는 60초마다 다시 부른다**(`HISTORY_EVENTS_POLL_MS` 와 같은 주기의 별도 상수) — 분이 닫힐 때마다 새 봉이 붙는다. 과거 청크는 다시 부르지 않는다. 숨김 탭에서도 돈다(013 과 같은 이유).
- `Candle1m` 의 `depositOk`·`withdrawOk` 는 `boolean | null`. 입출금 띠: 둘 다 `true` 면 열림 색, 어느 하나 `false` 면 막힘 색(`blockedSec ≥ 60` 진하게, 아니면 옅게 — 시안 그대로), **`null` 이 섞이고 `false` 가 없으면 회색(모름)**. 띠 라벨은 방향 경로로: 김프 `{해외} 출금 → {국내} 입금`, 역프 `{국내} 출금 → {해외} 입금`. 상위봉으로 접을 때 입출금은 마지막 분 값, `blockedSec` 은 합(시안 그대로).
- 해외 거래소 선택지는 `binance` 하나(나머지 5개 삭제 — 서버에 없는 거래소를 고를 수 있으면 빈 차트만 나온다). mock 파일과 `MOCK` 배지를 지운다.
- 상태: 청크 로딩 중이면 카드 헤더에 `불러오는 중…`(직전 봉 유지), 오류 `차트를 불러오지 못했습니다 (HTTP n)`, 어느 쌍도 봉이 없으면 판에 `기간 내 기록 없음`. 심볼이 서버에 없는 코인이어도(빈 `candles`) 오류가 아니다.
- 사건 음영·기준선·가격 선·봉 종류·범위 유지 규칙은 013 시안 그대로.

## 4. 검증
- 틱 행: 자격 통과 조합의 행에 가격 3개·입출금 4상태가 실린다, `rate` 는 (ask+bid)/2, Redis 레코드 JSON 키는 `dom fx base fwd rev` 그대로
- 집계: 한 분에 행 3개(fwd 0.5, 0.9, 0.7) → `fwd_o 0.5 h 0.9 l 0.5 c 0.7`·`samples 3`, 가격·입출금은 마지막 행 값
- 막힌 초: `fx_wd False` 인 행 10개 → `blocked_fwd_sec 10`, `blocked_rev_sec 0`; `None` 은 세지 않는다; 입출금 저장값 `True→1 False→0 None→−1`
- 분 닫힘: `ts` 가 분 + 60 이 되는 틱에서 직전 분 점이 생기고 그 분에 없던 조합은 점이 없다; 틱이 120초 건너뛰면 열려 있던 분 하나만 닫힌다
- 쓰기: 한 분의 점 전부가 한 번의 `write`; 실패 → 맵에 남아 다음 회차; 실패 뒤 60초 안엔 재시도 없음; 맵 상한 10,000 넘치면 오래된 분부터
- Influx 없이 기동 → 집계는 돌고 `/history/candles` 503
- `/history/candles`: `start` 없으면 `startTs == endTs − 86400`; `end − start == 86400` 200, `86401` 400; `end ≤ start` 400; `dir=reverse` 면 `rev_*`·`blocked_rev_sec`·`withdrawOk = dom_wd`·`depositOk = fx_dep`; `−1 → null`; `base=btc → BTC`; `fx=bybit` 422; 빈 `candles` 200; `ts` 오름차순
- web(수동 또는 순수 함수): 청크 캐시 — 봉 종류를 1m → 5m 으로 바꿔도 이미 받은 날은 재요청 없음; 오늘 청크만 60초 재조회; 띠 색 규칙(`null` 회색·`false` 막힘)
- 수동(로컬 dev compose + 실거래소): 기동 61초 뒤 Influx UI 에 `premium_1m` 점 ≈ 490 개(분 1개), 2분 뒤 두 배. `curl "…/history/candles?base=BTC"` 에 봉이 붙고 60초 뒤 1개 늘어남. 기록 탭 차트에 BTC 1m 캔들·업비트/빗썸 가격 선·입출금 띠가 실값으로 그려지고 `MOCK` 배지 없음, 해외 선택지는 Binance 만, 요청 URL 에 `start`·`end`·`dir`. 서버 재기동 → 그 분의 `samples < 60`, 그 다음 분은 60. 서버 테스트·lint, web build·lint 통과.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — history 행 server 칸에 "1분 집계기·`premium_1m`(분 닫힐 때 ≈490점 한 번에)·`/history/candles`(하루 상한)" 추가, web 칸의 "차트 카드는 mock(…)" 을 "차트 카드 = `/history/candles`(쌍×UTC 하루 청크 캐시, 오늘 청크 60초 재조회, 1m 클라이언트 접기)" 로, 비고의 "차트 실데이터 = …(014, 스펙 미작성)" 삭제. 알려진 빚에 "(014) `premium_1m` 은 배포 시점부터 — 과거분 없음(초 단위 `premium` 엔 가격이 없다). 1d 봉 360개 = 30일 상한에 걸려 화면이 짧다 — 장기 봉은 서버 롤업 후속" 추가.
- `CLAUDE.md` — 스펙 인덱스에 `| 014 | premium-1m | DONE | 틱에서 (국내·해외·코인) 1분 OHLC·가격·입출금 집계 → Influx `premium_1m` + `/history/candles`(하루 상한) + 기록 탭 차트 실데이터 |`, "지금 IN_PROGRESS 인 것" 줄 정리.
- `docs/context/architecture.md` — §데이터 흐름(BE) mermaid 에 "틱 → 1분 집계기 → `premium_1m`(분 닫힐 때)" 노드·간선, 테두리 색 범례에 014 추가. §데이터 흐름(FE) 기록 탭에 `/history/candles`. "현재 구조" 에 premium-1m 항목(집계기는 틱 루프가 쓰므로 core, 읽기 API 는 `features/history`). 상시 태스크 목록에 `premium_1m` 쓰기 태스크.
- `docs/context/product.md` — 용어 절에 "1분봉(candle): (국내, 해외, 코인) 조합의 1분 김프·역프 OHLC + 가격 종가 + 입출금 상태·막힌 초. 저장 시점에 틱을 접는다" 추가, 기능 목록 history 행 "김프 1초/1분봉 아카이브" 를 "김프 1초 원값·1분봉 아카이브" 로.
- `docs/context/db.md` — measurement 절에 `premium_1m` 정의(§3.4 그대로), 쓰는 쪽에 "1분 집계기(014): 분 닫힐 때 조합 전부 한 번에, 실패는 미전송 맵(상한 10,000)", 읽는 쪽 목록에 `/history/candles`.
- `docs/specs/001-collect.md` — §3.6-2 행 정의에 §3.2 의 7개 값을 덧붙이고 "Redis 레코드·`premium` 점에는 넣지 않는다(014)".
- `docs/specs/013-premium-events.md` — §3.5 마지막 항목을 "차트 카드는 014 §3.6" 으로, §7 남은 빚의 "사건 클릭 → 구간 차트(014)" 를 "(후속)" 으로.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
