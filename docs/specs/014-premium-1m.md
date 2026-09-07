# 014 — premium-1m

상태: DONE | 의존: 001(collect — 틱), 005(history — `/history/*` 오류 계약), 006(wallet-status — 행의 입출금 3상태), 009(tick-store — Redis 틱 레코드 모양 불변), 013(premium-events — Influx 1점 쓰기·재시도 패턴, 기록 탭 차트 카드 시안)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
기록 탭의 차트 카드(013 §3.5 에서 mock 으로 그린 시안)를 **실데이터**로 바꾼다. 서버가 틱에서 (국내, 해외, 코인) 조합마다 **1분 1점**(김프·역프 OHLC, 가격 종가 3개, 입출금 상태, 막힌 초)을 만들고, 그것을 5분·1시간·4시간·1일로 **사슬 롤업**해 계층마다 다른 유통기한으로 보관한다(1분 7일 … 1일 무제한). 화면은 봉 종류에 맞는 계층을 **임의 구간(요청당 1,440점 상한)** 으로 읽어 그린다. 초 단위 `premium` 은 조회에 쓰지 않는다 — 초 단위 원본을 요청마다 접는 방식은 6시간 창에서 Influx 를 죽인 전적이 있다(013 §1). 끝나면 심볼을 치면 그 코인의 김프 캔들·USDT 기준 가격 선·입출금 띠가 배포 시점부터의 실제 값으로 보이고, 왼쪽으로 끌면 그 계층의 보관 기간까지 과거가 붙는다.

## 2. 범위
- 만드는 것: 1분 집계기(core — 틱 루프가 매초 부른다), Influx 버킷 5개(`candles_1m` … `candles_1d`, 기동 시 생성)·measurement `candle` 쓰기·재시도, 롤업 회차(60초, 사슬 5m→1h→4h→1d, 재기동 따라잡기), `GET /history/candles`(`server/app/features/history/`), 기록 탭 차트 카드의 실데이터 연결(`web/src/features/history/`).
- 하지 않는 것: 과거분 생성(배포 시점부터 쌓인다 — 초 단위 `premium` 에는 가격이 없어 완전한 1분 점을 만들 수 없다). 초 단위 `premium`(버킷 `marketlens`, 무제한)의 보존 변경. 사건 로그 행 클릭 → 차트 이동(후속). 1초 가격 저장. 해외 거래소 추가(서버가 수집하는 해외 거래소는 binance 뿐 — 화면의 선택지도 binance 만 남긴다). `/history/premium`·`streaks`·`bulk`·`events` 변경.
- 바꾸는 기존 것:
  1. 001 §3.6-2 틱 행 — `{dom, fx, base, fwd, rev}` 에 **가격 3개·입출금 4상태**를 더한다(§3.2). 009 의 Redis 틱 레코드·Influx `premium` 은 **그대로**(새 필드는 직렬화하지 않는다) — 1분 점은 메모리에서 만들고, 초 단위 원본에는 가격을 남기지 않기로 했다(용량 5배).
  2. 001 틱 루프 — 매 틱을 집계기에 넘긴다(013 감지기 다음 자리).
  3. 013 §3.5 마지막 항목 — 차트 카드의 mock·`MOCK` 배지를 없애고 이 스펙의 API 로.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001·009: 틱은 1초 주기, `ts` 는 epoch 초. 틱 `rows` 의 행 = 자격(국내×해외 다른 거래소, 양쪽 최우선 호가, 그 국내 거래소 자신의 USDT 시세, 여섯 값 > 0)을 통과한 조합만. `dom ∈ {upbit, bithumb}`, `fx = binance`. 자격 미달 조합은 그 틱에 **없다**. 국내 행의 `price` = 마지막 체결가(없으면 최우선 호가 중간값), 해외 행도 같다. 국내 거래소의 USDT 시세 = `rate.ask`(KRW-USDT 최우선 매도호가)·`rate.bid`(최우선 매수호가).
- 006: 행의 `deposit_enabled`·`withdrawal_enabled` 는 3상태(`true`/`false`/`None`=모름 — 조회 실패 시). 60초마다 갱신, 국내·해외 세 거래소 모두.
- 005·013: `/history/*` 오류 계약 — 503 `storage_unavailable`(Influx 불달·`INFLUX_TOKEN` 없음), 400 `invalid_request`, 422(파라미터 검증). `start`·`end` 는 0 ≤ 값 ≤ 4,102,444,800. HTTP JSON 키는 camelCase. Influx 는 같은 (measurement, tag, time) 을 덮어쓴다 — 재시도 안전성의 근거. 쓰기 1회는 동기·타임아웃 60초. Influx org `marketlens`, `INFLUX_TOKEN` 은 compose 의 초기 관리 토큰(버킷 생성 권한이 있다).
- 013: 쓰기 실패는 미전송 맵에 두고 다음 60초 회차에 재시도, **실패 뒤 60초 안에는 다시 쓰지 않는다**(불통 중 쓰기 스레드가 연달아 막히는 것을 피하기 위해).
- 013 §3.5 차트 카드 시안: 심볼 검색 → Enter, 봉 `1m 3m 5m 15m 30m 1h 4h 1d`(아래 계층을 클라이언트가 KST 정렬로 접는다 — `%` 는 OHLC, 가격·입출금은 마지막 값, 막힌 초는 합), 국내·해외 거래소 체크박스 다중 선택(쌍 1개 = 캔들, 여럿 = 쌍별 종가 선), 진입 1.0·이탈 0.5·0 기준선, 사건 음영(`/history/events`), USDT 기준 가격 선(국내는 원화 ÷ 환율), 입출금 띠, 처음 360봉·왼쪽 끌면 과거 로드.

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
- 분 = `ts // 60 * 60`. 조합 `(dom, fx, base)` 마다 **그 분에 행이 있던 틱만** 모은다.
- `fwd`·`rev` 각각 OHLC: 시가 = 그 분 첫 행, 고·저 = 최대·최소, 종가 = 마지막 행. 가격 3개·입출금 4상태 = 마지막 행 값. `samples` = 그 분의 행 수(1~60).
- **막힌 초** 두 개: `blocked_fwd_sec` = `fx_wd == False` 또는 `dom_dep == False` 인 행 수, `blocked_rev_sec` = `dom_wd == False` 또는 `fx_dep == False` 인 행 수. `None`(모름)은 막힘으로 세지 않는다 — 조회 실패를 막힘으로 그리면 거짓 경고가 된다. 김프는 해외 출금 → 국내 입금 경로, 역프는 국내 출금 → 해외 입금 경로(013 §7).
- **분 닫힘**: 어느 틱의 `ts` 가 열린 분 + 60 이상이 되는 순간 그 분의 조합 전부를 점으로 만든다(조합 ≈ 490, 한 번의 `write`). 그 분에 행이 한 번도 없던 조합은 점이 없다. 틱이 건너뛰어 여러 분이 지나면 열려 있던 분만 닫는다(빈 분은 없는 것).
- **재기동**: 진행 중이던 분은 버린다(재기동 후 첫 분은 `samples` 가 60 미만인 채로 남는다 — 관측한 만큼만 사실). 복원 없음.

### 3.4 Influx — 버킷·점·쓰기
- **버킷 5개**, 계층마다 하나. retention 이 곧 유통기한이다(Influx 2.7 에서 싼 삭제는 버킷 retention 뿐 — 조건 삭제 API 는 큰 시리즈에서 무겁고 EC2 Influx 는 OOM 전력이 있다). 기동 시 없는 버킷을 만든다(org `marketlens`, 3초 상한, 실패하면 경고 1줄 후 계속 — 쓰기가 실패로 남는다). 이미 있으면 retention 을 바꾸지 않는다(사람이 바꾼 값을 존중). 초 단위 `premium` 이 든 `marketlens` 버킷은 건드리지 않는다.

| 계층 | 버킷 | 보관 |
|---|---|---|
| 1m | candles_1m | 7일 |
| 5m | candles_5m | 30일 |
| 1h | candles_1h | 90일 |
| 4h | candles_4h | 365일 |
| 1d | candles_1d | 무제한 |

- measurement 는 다섯 버킷 모두 `candle`. tag `dom`·`fx`·`base`, time = 창 시작(초). field `fwd_o fwd_h fwd_l fwd_c rev_o rev_h rev_l rev_c`(float %, 원값)·`krw`(float, 국내 종가 원)·`usdt`(float, 해외 종가)·`rate`(float, USDT 중간값 원)·`dom_dep dom_wd fx_dep fx_wd`(int: 1 가능·0 불가·−1 모름)·`blocked_fwd_sec blocked_rev_sec`(int, 창 길이 초 이하)·`samples`(int, 그 창에 든 틱 수). 유일키 = (버킷, dom, fx, base, 창 시작). 1m 하루 ≈ 70만 점, 계층 전체 합쳐도 초 단위 `premium` 의 1/40 이하.
- **창 정렬은 KST 벽시계**: 창 시작 = `(ts + 32400) // W * W − 32400`(W = 창 길이 초). 1m·5m·1h 는 UTC 정렬과 같고 4h·1d 가 다르다 — 4h 를 UTC 로 자르면 KST 자정이 4h 경계에 안 걸려 일봉을 못 만든다. 화면의 접기(013 시안)도 KST 정렬이라 일치한다.
- 쓰기: 1m 은 분이 닫힐 때 점 전부를 미전송 맵(키 = 유일키)에 넣고 쓰기 태스크가 한 번에 쓴다(013 과 같은 태스크 모양 — 점이 생기면 즉시, 없어도 60초 회차). 실패는 로그 1줄, 맵에 남겨 다음 회차 재시도, **실패 뒤 60초 안엔 안 쓴다**. 맵 상한 **10,000점**(≈20분치), 넘치면 오래된 분부터 버리고 로그. 종료 시 미전송분은 쓰지 않는다(013 과 같다).
- Influx 없이(`INFLUX_TOKEN` 없음·불통) 기동하면 집계는 돌고 쓰기·롤업만 실패로 남는다 — 경고 1줄.

### 3.5 롤업 — 사슬·회차·따라잡기
- 사슬: 5m ← 1m ×5, 1h ← 5m ×12, 4h ← 1h ×4, 1d ← 4h ×6. 한 계층은 **바로 아래 계층만** 읽는다(4h 창 하나를 1m 에서 접으면 12만 점, 1h 에서 접으면 2천 점).
- 접기 규칙: 시가 = 첫 아래 점의 시가, 고·저 = 최대·최소, 종가 = 마지막 점의 종가(fwd·rev 각각). 가격 3개·입출금 4상태 = 마지막 점 값. `blocked_*_sec`·`samples` = 합. 아래 점이 일부만 있으면 있는 것으로 접고, 하나도 없으면 위 점도 없다.
- **회차**: 1m 쓰기 태스크의 매 회차(분 닫힘 직후·60초) 안에서, 미전송 1m 을 쓴 **다음** 5m → 1h → 4h → 1d 순으로 돈다. 순서가 곧 정합성이다 — 방금 닫힌 분이 먼저 들어가고, 5m 이 써진 뒤 1h 가 그것을 읽는다. Influx 가 불통이면 1m 쓰기가 실패한 자리에서 그 회차의 롤업도 멈춘다.
- 계층마다 **마지막으로 접은 창**을 메모리에 든다. 회차마다 그 다음 창부터 차례로 접어 쓴다(같은 키 덮어쓰기라 재시도 안전). 한 회차에 계층당 **최대 12창**, 나머지는 다음 회차 — 재기동 따라잡기가 아래 계층을 한꺼번에 읽어 Influx 를 누르지 않게.
- **위 계층은 아래 계층이 다 쓴 곳까지만 접는다**: 접는 창의 끝 ≤ min(지금, 아래 계층 완료 시각). 5m 의 아래 완료 시각 = 1m 이 Influx 에 다 들어간 분(집계기가 지금 열어 둔 분의 시작 — 그 전 분은 전부 썼거나 상한에서 버린 것), 1h·4h·1d 의 아래 완료 시각 = 아래 계층이 마지막으로 접은 창의 끝. 위 계층이 아래를 앞질러 "아래 점이 없다" 로 빈 창을 확정하면 그 창은 다시 보지 않아 영구 구멍이 되기 때문이다 — 대가는 밀린 구간의 따라잡기 속도(밀린 1시간당 1회차).
- **재기동 따라잡기**: 기동 시 계층마다 위 버킷의 가장 늦은 점을 "마지막으로 접은 창" 으로 삼는다(계층당 3초 상한, 실패하면 지금 창으로 시작하고 경고 1줄). 위 버킷이 비어 있으면(첫 배포·긴 공백) **아래 계층들 중** 가장 오래된 점이 든 창의 직전부터 — 첫 배포에는 1m 만 있고 5m·1h 는 비어 있으므로 바로 아래 한 계층만 보면 지금 창에 앵커를 잡아 첫 시간을 접지 못한다. 각 버킷은 자기 보관 기간 안에서만 찾는다(전 구간 스캔 없음). 아래 계층의 보관 기간을 넘긴 빈틈은 메울 수 없다 — 7일 안에 다시 켜지면 1m 부터 전 계층이 빈틈 없이 복구된다.
- 1m 미전송 맵이 상한을 넘겨 버린 분은 위 계층에도 빠진다(1m 이 진실이고 없는 것은 접지 않는다).

### 3.6 `GET /history/candles`
계층 버킷 하나만 읽는다. 진행 중인 창(메모리)은 싣지 않는다 — 화면은 어차피 현재 창을 그리지 않는다. 거래소 호출 0회.
`?base&res&dom&fx&dir&start&end` — `base` 필수(`^[A-Za-z0-9]{1,20}$`, 대문자 정규화), `res` 기본 `1m`(`1m|5m|1h|4h|1d`), `dom` 기본 `upbit`(`upbit|bithumb`), `fx` 기본 `binance`(값도 `binance` 만), `dir` 기본 `kimp`(`kimp|reverse`), `end` 없으면 지금, `start` 없으면 `end − 상한`. **상한 = 1,440 × 창 길이**(1m 하루·5m 5일·1h 60일·4h 240일·1d 1,440일). `end − start > 상한` 이면 400 `invalid_request`("window exceeds limit") — 상한이 있어야 실수로 한 번에 수십만 점을 읽는 호출이 Influx 를 못 건드린다. `end ≤ start` 400. 구간 판정 `start ≤ 창 시작 < end`.
```json
{
  "base": "BTC", "res": "1m", "dom": "upbit", "fx": "binance", "dir": "kimp",
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
- 정렬 `ts` 오름차순. 기록 없으면 404 가 아니라 빈 `candles`(배포 전 날짜·상장 전 코인·보관 기간 밖은 "없음" 이 정상).
- 오류: 503 `storage_unavailable`, 400·422 는 §3.1. 응답 크기 감: 최대 1,440건 × ≈150B, 앱 전역 gzip.

### 3.7 web — 차트 카드 실데이터
- 봉 종류 → 계층: `1m 3m → 1m`, `5m 15m 30m → 5m`, `1h → 1h`, `4h → 4h`, `1d → 1d`. 계층 안에서만 접는다(013 시안의 접기 그대로, KST 정렬).
- 데이터 = 선택된 (국내 × 해외) 쌍마다 `GET /history/candles` 를 **청크** 단위로 부른다. 청크 = 그 계층의 상한 길이(§3.6), KST 자정 정렬(`(ts + 32400) // 상한 * 상한 − 32400`). 청크는 `(dom, fx, base, dir, res, 청크 시작)` 키로 메모리에 들고 있어 심볼·방향·쌍·봉을 바꿔도 **없는 청크만** 새로 부른다. 처음엔 `INITIAL_BARS × 봉 초`를 덮는 청크 수, 왼쪽으로 끌면 청크 하나 추가, **과거 상한 = 그 계층의 보관 기간**(1d 는 상한 없음). 요청은 디바운스 400ms·직전 요청 취소(013 §3.5 방식). 청크가 상한 길이라 400 은 나올 수 없다.
- **최신 청크는 60초마다 다시 부른다**(`HISTORY_EVENTS_POLL_MS` 와 같은 주기의 별도 상수) — 창이 닫힐 때마다 새 봉이 붙는다. 과거 청크는 다시 부르지 않는다. 숨김 탭에서도 돈다(013 과 같은 이유).
- `Candle1m` 의 `depositOk`·`withdrawOk` 는 `boolean | null`. 입출금 띠: 둘 다 `true` 면 열림 색, 어느 하나 `false` 면 막힘 색(`blockedSec ≥ 창 길이` 진하게, 아니면 옅게 — 시안 그대로), **`null` 이 섞이고 `false` 가 없으면 회색(모름)**. 띠 라벨은 방향 경로로: 김프 `{해외} 출금 → {국내} 입금`, 역프 `{국내} 출금 → {해외} 입금`. 접을 때 입출금은 마지막 값, `blockedSec` 은 합(시안 그대로).
- 해외 거래소 선택지는 `binance` 하나(나머지 5개 삭제 — 서버에 없는 거래소를 고를 수 있으면 빈 차트만 나온다). mock 파일과 `MOCK` 배지를 지운다.
- 상태: 청크 로딩 중이면 카드 헤더에 `불러오는 중…`(직전 봉 유지), 오류 `차트를 불러오지 못했습니다 (HTTP n)`, 어느 쌍도 봉이 없으면 판에 `기간 내 기록 없음`. 심볼이 서버에 없는 코인이어도(빈 `candles`) 오류가 아니다.
- 사건 음영·기준선·가격 선·범위 유지 규칙은 013 시안 그대로.

## 4. 검증
- 틱 행: 자격 통과 조합의 행에 가격 3개·입출금 4상태가 실린다, `rate` 는 (ask+bid)/2, Redis 레코드 JSON 키는 `dom fx base fwd rev` 그대로
- 집계: 한 분에 행 3개(fwd 0.5, 0.9, 0.7) → `fwd_o 0.5 h 0.9 l 0.5 c 0.7`·`samples 3`, 가격·입출금은 마지막 행 값
- 막힌 초: `fx_wd False` 인 행 10개 → `blocked_fwd_sec 10`, `blocked_rev_sec 0`; `None` 은 세지 않는다; 입출금 저장값 `True→1 False→0 None→−1`
- 분 닫힘: `ts` 가 분 + 60 이 되는 틱에서 직전 분 점이 생기고 그 분에 없던 조합은 점이 없다; 틱이 120초 건너뛰면 열려 있던 분 하나만 닫힌다
- 쓰기: 한 분의 점 전부가 한 번의 `write`(버킷 `candles_1m`); 실패 → 맵에 남아 다음 회차; 실패 뒤 60초 안엔 재시도 없음; 맵 상한 10,000 넘치면 오래된 분부터
- 버킷: 없으면 5개를 retention(604800·2592000·7776000·31536000·0)으로 만든다; 있으면 retention 을 안 바꾼다; 생성 실패해도 기동한다
- 창 정렬: 4h 창 시작이 KST 00·04·…·20시, 1d 창 시작이 KST 자정 (epoch 로 단언)
- 롤업: 1m 5개(시가 a·종가 e·고 max·저 min·samples 합·blocked 합) → 5m 1점, 마지막 점 값이 가격·입출금; 1m 이 3개뿐이면 그것으로 접는다, 0개면 5m 점 없음; 순서 = 1m 쓰기 → 5m → 1h → 4h → 1d(1h 는 같은 회차에 쓴 5m 을 읽는다); 한 회차 계층당 12창까지, 13번째는 다음 회차; 끝이 지금 이후인 창은 접지 않는다; 밀린 3시간에서 첫 회차 1h 는 5m 이 접힌 첫 시간 창만 접고 나머지는 다음 회차들에서(구멍 없음); 5m 은 집계기가 열어 둔 분 전까지만 접는다
- 따라잡기: 기동 시 위 버킷 마지막 점 다음 창부터; 위 버킷이 비면 아래 계층들 중 가장 오래된 점의 창부터(1m 만 있어도 1h·1d 가 그 시각에 앵커); 조회 실패 시 지금 창부터 + 경고
- Influx 없이 기동 → 집계는 돌고 `/history/candles` 503
- `/history/candles`: `start` 없으면 `startTs == endTs − 상한`; `res=5m` 에서 `end − start == 432000` 200, `432001` 400; `end ≤ start` 400; `res` 별로 다른 버킷을 읽는다; `dir=reverse` 면 `rev_*`·`blocked_rev_sec`·`withdrawOk = dom_wd`·`depositOk = fx_dep`; `−1 → null`; `base=btc → BTC`; `fx=bybit`·`res=3m` 422; 빈 `candles` 200; `ts` 오름차순
- web(수동 또는 순수 함수): 봉 → 계층 매핑; 청크 캐시 — 봉 종류를 1m → 3m 으로 바꿔도 이미 받은 청크는 재요청 없음, 5m 으로 바꾸면 `res=5m` 청크를 새로 부른다; 최신 청크만 60초 재조회; 과거 로드가 계층 보관 기간에서 멈춘다; 띠 색 규칙(`null` 회색·`false` 막힘)
- 수동(로컬 dev compose + 실거래소): 기동 로그에 버킷 5개 생성, 61초 뒤 Influx UI `candles_1m` 에 점 ≈ 490 개(분 1개), 5분 뒤 `candles_5m` 에 ≈ 490 개. `curl "…/history/candles?base=BTC"` 에 봉이 붙고 60초 뒤 1개 늘어남, `res=5m` 도 5분 뒤 1개. 기록 탭 차트에 BTC 1m 캔들·업비트/빗썸 가격 선·입출금 띠가 실값으로 그려지고 `MOCK` 배지 없음, 해외 선택지는 Binance 만, 봉을 5m 으로 바꾸면 요청 URL 에 `res=5m`. 서버 재기동 → 그 분의 `samples < 60`, 그 다음 분은 60, 재기동 중 못 접은 5m 창이 기동 뒤 회차에 채워진다. 서버 테스트·lint, web build·lint 통과.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 2026-09-07 로컬(Mac mini). 이 망은 이날 거래소 도메인이 열려 있었다(api.upbit.com 200).
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m pytest -q
#   All checks passed! / 114 files already formatted / 536 passed
cd web && npm run lint && npm run build
#   oxlint 경고 0 / tsc -b + vite build 성공 (index-*.js 444 kB)
docker compose --env-file server/.env -f docker-compose.dev.yml up -d
cd server && .venv/bin/uvicorn app.main:app --port 8020          # :8000 은 다른 프로세스가 점유
#   21:49:15 로그 "봉 버킷 생성: candles_1m, candles_5m, candles_1h, candles_4h, candles_1d"
curl -s "localhost:8020/history/candles?base=BTC"
#   61초 뒤 count 1 — 21:49 봉 samples 42(기동 분은 관측한 만큼), depositOk/withdrawOk null(키 없음), 이후 분마다 +1
#   Influx(query_candles): candles_1m 분당 489점(upbit+bithumb, /spreads 행 489 와 같다), candles_5m 21:45 창 489점
curl -s "localhost:8020/history/candles?base=BTC&res=5m"          # 5분 뒤 count 2 (21:45 → 21:50)
# 기록 탭 — 헤드리스 브라우저(playwright)로 :8010(VITE_API_BASE=http://localhost:8020) 열어 탭 클릭·스크린샷:
#   BTC 1분 캔들·업비트/Binance 가격 선·입출금 띠(회색=모름) 실값, MOCK 배지 없음, 해외 선택지 Binance 만,
#   요청 res=1m 청크 2개(오늘·어제 KST 하루), "5분" 클릭 → res=5m&start=…&end=start+432000 요청
# 재기동 21:53:51 → 21:53 봉 samples 6(<60), 21:54 봉 60, 5m 21:50 창(재기동 전 미접힘)이 기동 뒤 회차에 samples 246 으로 채워짐
#   두 번째 기동 로그에 버킷 생성 없음(있으면 안 건드린다), candle 경고 0
```

## 6. 갱신할 문서
- `docs/context/status.md` — history 행 server 칸에 "1분 집계기·버킷 `candles_1m…1d`(7일/30일/90일/365일/무제한)·사슬 롤업 회차·`/history/candles`(`res`, 요청당 1,440점 상한)" 추가, web 칸의 "차트 카드는 mock(…)" 을 "차트 카드 = `/history/candles`(봉 → 계층, 쌍×청크 캐시, 최신 청크 60초 재조회, 계층 안 접기)" 로, 비고의 "차트 실데이터 = …(014, 스펙 TODO)" 삭제. 알려진 빚에 "(014) `candles_*` 은 배포 시점부터 — 과거분 없음(초 단위 `premium` 엔 가격이 없다). 초 단위 `premium` 의 보존은 여전히 무제한(별도 결정)" 추가.
- `CLAUDE.md` — 스펙 인덱스 014 행 상태 → DONE, "지금 IN_PROGRESS 인 것" 줄 정리.
- `docs/context/architecture.md` — §데이터 흐름(BE) mermaid 에 "틱 → 1분 집계기 → `candles_1m`(분 닫힐 때) → 롤업 5m/1h/4h/1d" 노드·간선, 테두리 색 범례에 014 추가. §데이터 흐름(FE) 기록 탭에 `/history/candles`. "현재 구조" 에 premium-1m 항목(집계기·롤업은 틱 루프·쓰기 태스크가 쓰므로 core, 읽기 API 는 `features/history`). 상시 태스크 목록에 `candle` 쓰기·롤업 태스크.
- `docs/context/product.md` — 용어 절에 "봉(candle): (국내, 해외, 코인) 조합의 창(1m·5m·1h·4h·1d, KST 정렬) 김프·역프 OHLC + 가격 종가 + 입출금 상태·막힌 초. 1m 은 틱에서, 위 계층은 아래 계층을 접어 만든다. 보관 1m 7일·5m 30일·1h 90일·4h 1년·1d 무제한" 추가, 기능 목록 history 행 "김프 1초/1분봉 아카이브" 를 "김프 1초 원값·계층 봉 아카이브" 로.
- `docs/context/db.md` — 엔진 셋 Influx 항목에 "버킷 `marketlens`(초 단위, 무제한) + `candles_1m…1d`(retention = 유통기한)" 로, measurement 절에 `candle` 정의(§3.4 그대로)·KST 창 정렬, 쓰는 쪽에 "1분 집계기·롤업(014): 분 닫힐 때 조합 전부 한 번에 → 같은 회차에 5m→1h→4h→1d, 계층당 12창, 실패는 미전송 맵(상한 10,000)", 읽는 쪽 목록에 `/history/candles` 와 기동 시 따라잡기 기준점 조회(3초 상한), 보존 절의 "초단위 적재의 보존·롤업은 … 별도 스펙" 을 "봉 계층은 버킷 retention, 초 단위는 아직 무제한" 으로.
- `docs/context/dev-setup.md` — 로컬 점검 절에 "Influx UI 에서 `candles_1m` 버킷 점 수 확인" 한 줄.
- `docs/specs/001-collect.md` — §3.6-2 행 정의에 §3.2 의 7개 값을 덧붙이고 "Redis 레코드·`premium` 점에는 넣지 않는다(014)".
- `docs/specs/013-premium-events.md` — §3.5 마지막 항목을 "차트 카드는 014 §3.7" 으로, §7 남은 빚의 "사건 클릭 → 구간 차트(014)" 를 "(후속)" 으로.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server: `app/core/models.py`(TickRow 7필드) · `app/core/ticks.py`(build_tick 채움·`candles` 싱크 배선) · `app/core/influx.py`(`CandleRow`·`candle_point`·`write(bucket)`·`list_buckets`·`create_bucket`·`query_candles`·`latest_candle_ts`·`earliest_candle_ts`) · **`app/core/candles.py`**(`TIERS`·`window_start`·`limit_sec`·`ensure_candle_buckets`·`fold_candles`·`fold_window`·`Rollup`·`CandleAggregator`) · `app/main.py`(버킷 → 기준점 복원 → 쓰기 태스크 → 틱 루프 배선) · `app/features/history/{models,service,router}.py`(`CandleOut`·`CandlesResponse`·`CandleReader`·`build_candles`·`GET /history/candles`) · 테스트 `tests/candle_fakes.py`·`tests/test_candles.py`(21)·`tests/test_ticks.py`(+1)·`features/history/tests/helpers.py`(`seed_candle`·`query_candles`)·`features/history/tests/test_candles_api.py`(10)
  - web: `shared/config.ts`(`HISTORY_CANDLES_POLL_MS`) · `features/history/types.ts`(`Res`·`CandlesResponse`·`Candle1m` 3상태) · **`features/history/candles.ts`**(봉→계층·청크·보관 상한·띠 색 규칙·`FX_CHOICES`·`INITIAL_BARS`) · `api.ts`(`fetchCandles`·`useCandles`) · `rollup.ts`(KST 고정·`samples` 합·계층 base) · `Tab.tsx`·`Chart.tsx`(실데이터·상태·경로 라벨·모름 회색) · `mock.ts` 삭제
  - docs: `docs/context/{status,architecture,product,db,dev-setup}.md` · `docs/specs/001-collect.md` §3.6-2 · `docs/specs/013-premium-events.md` §3.5·§7 · `CLAUDE.md` 인덱스
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - **§3.5 함께 고침(사람 합의)**: 위 계층은 아래 계층 완료 지점까지만 접는다(접는 창의 끝 ≤ min(지금, 아래 완료 시각); 1m 완료 시각 = 집계기가 열어 둔 분의 시작). 원래 문구대로면 재기동·불통 뒤 밀린 구간에서 위 계층이 아래를 앞질러 빈 창을 확정해 영구 구멍이 생겼다. 따라잡기 기준점도 "아래 버킷" → "아래 계층들 중 가장 오래된 점"(첫 배포에는 1m 만 있어 1h·1d 가 5m·4h 만 보면 지금 창에 앵커를 잡는다). §4 에 검증 항목 추가.
  - 첫 틱 전 회차(1m 완료 시각 미상)에는 롤업을 돌리지 않는다. 롤업 실패도 1m 쓰기와 같은 60초 게이트를 공유한다(Influx 불통 한 가지 원인이라). 롤업은 창 1개당 `write` 1번(12창 = 12번).
  - 기준점 복원 3초 상한은 **계층당**. `latest/earliest_candle_ts` 는 시리즈별 `last()/first()` 푸시다운 뒤 group·sort — 전 구간 정렬을 피했고(EC2 4GB Influx OOM 전력), 조회 범위는 아래 계층 보관 기간 안으로 한정.
  - `TickRow` 7필드는 기본값을 둔다 — Redis 에서 되읽은 틱(`decode_tick`)과 옛 테스트가 다섯 값만으로 행을 만들기 때문. `InfluxClient.write` 에 선택 인자 `bucket` 추가(기본은 `marketlens`).
  - web: `rollup.ts` 의 접기 정렬을 브라우저 로컬 오프셋에서 **KST 고정 상수**로 바꿈(서버 창 정렬과 같아야 1d 경계가 맞는다 — 013 시안은 로컬). 접기에 `samples` 합 추가. 최신 청크 재조회는 60초 회차에서만(봉 종류·쌍 변경 시에는 없는 청크만 — "1m→3m 재요청 없음" 규칙). 청크 캐시는 컴포넌트 ref(탭이 마운트된 동안 유지, 셸은 탭을 내리지 않는다). `INITIAL_BARS` 를 `Chart.tsx` → `candles.ts` 로. 읽기 줄의 입출금 `null` 은 "모름"(회색). 청크 하나라도 실패하면 오류 배지 + 받은 청크만 그린다.
  - 커밋 1개(`feat(history): add 1m candle aggregator …`)가 320줄로 300줄 규약을 조금 넘는다 — 모듈 하나를 더 쪼개면 컴파일 단위가 깨져 그대로 두었다. `actionlint` 미설치·워크플로 변경 없음.
  - 로컬 검증 중 이전 세션의 vite(:8010, 프록시 :8000)를 내리고 `VITE_API_BASE=http://localhost:8020` 로 다시 띄웠다 — 서버는 :8020(:8000 점유). 둘 다 켜 둔 상태로 끝냈다.
- 남은 빚:
  - `candles_*` 은 배포 시점부터(과거분 없음). 위 계층 구멍을 사후에 메우는 도구 없음(1m 7일·5m 30일 안이면 재료는 있다 — 후속 스펙 후보). web 청크 캐시 상한 없음(status.md 알려진 빚).
  - EC2 확인 대기: 실 키로 입출금 값(`depositOk`/`withdrawOk` true/false·막힌 초), 4h·1d 계층 첫 점(KST 04시·자정 경계), 7일 이상 운용 뒤 1m retention 삭제, 긴 공백 따라잡기 속도 실측(밀린 1시간당 1회차), 1m 분당 ≈490점 × 하루 적재량.
  - 사건 클릭 → 구간 차트(후속). web 테스트 러너 없음 — `candles.ts` 순수 함수는 수동·빌드로만 확인.
