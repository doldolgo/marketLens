# 005 — history

상태: TODO | 의존: 001(collect), 002(web-shell), 003(spreads)

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
메모리에만 있던 김프/역프를 60초마다 InfluxDB 에 한 점씩 쌓는다. 쌓인 기록을 `GET /history/*` 로 구간 조회·통계(streaks) 한다. 업비트 초봉 × 바이낸스 1초봉으로 과거 3개월 김프를 백필하는 스크립트를 제공한다.
엔진·데이터 모델·env·로컬 접속은 `docs/context/db.md` 가 정한다. 이 스펙은 그 모델을 쓰는 동작만 쓴다.
끝나면 dev compose(Influx 하나)를 올리고 서버를 :8000 에 띄우면 1분 뒤 `premium` 에 점이 남고, `/history/premium` 이 그 주의 기록을 돌려준다.

## 2. 범위
- 만드는 것: 공유 인프라의 Influx 클라이언트(연결·읽기/쓰기), 앱 기동이 관리하는 persist 루프, 기능 폴더 `features/history`(`/history/premium` `/history/streaks` `/history/streaks/bulk`), 백필 스크립트(코인 목록·일수 인자), dev compose(Influx 하나, 2.7 — 루트 `docker-compose.dev.yml`. 배포용 `docker-compose.yml` 은 007 몫), `web/src/features/history/`(기록 탭 화면).
- 하지 않는 것: 기록 탭을 `/history/*` 실데이터에 연결하는 것과 `/spreads` 의 `spark` 채우기(둘 다 같은 후속 스펙 몫). 빗썸 페어 백필(빗썸엔 초봉 API 없음 — 빗썸×바이낸스는 실시간 기록으로만 쌓인다). 보존기간 정리(retention 무제한). 배포 compose(스펙 007). 재기동 직후 조회 API 의 DB 폴백 — 메모리가 비면 기존 404 그대로다.
- 바꾸는 기존 것: 001 의 수집 루프는 매 사이클의 입출금 조회 실패 여부(거래소별)를 저장 루프가 읽을 수 있게 남긴다. 002/003 의 앱 셸: history placeholder → 기록 탭, 선택된 심볼 초기값 `null` → `'BTC'`.

## 3. 동작

### 3.1 설정·로컬 DB
env(`INFLUX_URL`·`INFLUX_TOKEN`)·org·bucket 은 `db.md` 대로. 저장 주기 60초는 코드 상수다.
dev compose 는 Influx 2.7 하나만 띄운다. 첫 기동 시 org·bucket `marketlens` 가 만들어지고 admin 토큰은 `.env` 의 `INFLUX_TOKEN` 과 같아야 한다 — 앱이 그 토큰으로 붙기 때문이다. 데이터는 볼륨에 남는다.
컨테이너에는 `DOCKER_INFLUXDB_INIT_*` 환경변수로 전달하되 값은 compose 변수 치환 `${INFLUX_TOKEN}` 으로 `server/.env` 에서 온다 — 기동은 `docker compose --env-file server/.env -f docker-compose.dev.yml up -d`. 토큰을 파일 한 곳에만 두기 위해서다.
**Influx 가 닿지 않아도 앱은 뜬다**: 기동 시 연결 실패는 에러 로그 1줄. 저장 루프가 다음 회차에 재시도하고, 조회는 메모리로 정상 동작한다. `/health` 는 200, `/history/*` 만 503 `storage_unavailable`. `INFLUX_TOKEN` 이 없으면 저장 루프 비활성·`/history/*` 503. 수집·조회가 저장소 장애에 볼모 잡히면 안 되기 때문이다.

### 3.2 measurement
모델은 `db.md` 의 `premium`·`dw_fail` 그대로. 이 스펙이 쓰는 부분:
- `premium` — persist 루프와 백필이 쓰고, `/history/*` 전부가 읽는다. time 은 수집 시각(초 정밀도). 같은 (dom, fx, base, time) 은 덮어쓴다.
- `dw_fail` — persist 루프가 입출금 조회 실패가 관측된 거래소마다 1점 쓴다. 읽는 HTTP 엔드포인트는 없다. 006 전에는 입출금 조회 자체가 없어 실패 관측도 없다 — 이 스펙은 배선만 만들고, 점은 006 이후 쌓인다.

### 3.3 persist 루프 (60초)
- 기동 후 **먼저 60초 잔 뒤** 첫 저장(직후엔 메모리가 비어 쓸 것이 없다). 수집이 아직 한 번도 안 돌았으면 아무것도 쓰지 않는다.
- 저장은 수집과 **같은 락**을 잡는다 — 수집이 메모리를 통째 교체하는 도중 읽으면 반쪽이 남는다. 한 회차의 점은 **쓰기 1번**으로 보낸다(전부 성공 또는 전부 없음).
- 한 회차 순서:
  1. 김프 점 만들기(아래 규칙). 환율이 하나도 없으면 경고 로그 후 이번 회차 생략.
  2. 입출금 조회 실패가 관측된 거래소마다 `dw_fail` 1점(time = 수집 시각).
  3. 모은 점을 한 번에 쓴다.
- **김프 점 규칙**: base 마다 (국내 거래소 × 해외 거래소) 조합. 국내 행은 호가가 있고 **그 거래소의 환율**이 있어야 한다(환율 없는 국내 거래소는 빠진다 — 남의 환율을 빌리지 않는다). 해외 행도 호가 필수.
  수식은 003 의 `core/premium.py` 공개 함수 `premium_percent(*, buy_krw: float, sell_krw: float) -> float` = `(sell/buy − 1) × 100` 을 import 해 쓴다(재정의 금지). `fwd = premium_percent(buy_krw=fx_ask × rate_ask, sell_krw=dom_bid)`, `rev = premium_percent(buy_krw=dom_ask, sell_krw=fx_bid × rate_bid)` — **최우선 1단계 기준의 원값(raw)** 이다. `/spreads` 는 같은 식을 체결 규모만큼 걸은 평균가에 적용해 슬리피지 차감 후 순값을 내므로(003 §3.2-4) 두 값은 다르고, 그 차이가 `/spreads` 의 `slipFwd`·`slipRev` 다 — 저장 시점에는 체결 규모가 정의되지 않아 아카이브는 원값을 쓴다(003 §2). 여섯 값 중 하나라도 ≤ 0 이면 건너뜀. 점 `(dom, fx, base, time=수집 시각 초, fwd, rev)`. 수집이 멈춰 같은 시각이면 같은 점을 덮어쓴다 — 주기가 곧 DB 증가 속도.
- 저장 실패(Influx 다운 등): 로그 `DB 저장 실패 (연속 n회)` 를 남기고 다음 회차에 재시도한다. **메모리와 수집 루프는 영향 없다.** 놓친 회차는 기록에 구멍으로 남는다(소급 안 함).

### 3.4 `GET /history/*`
HTTP JSON 키와 복합어 쿼리 파라미터는 camelCase다. 모든 시각 `*Ts` 는 epoch 초, `fetchedAt` 은 ms.
공통 파라미터: `dom` ∈ {upbit, bithumb}(기본 upbit), `fx` = binance 고정. `maxGap`(기본 600, ≥1) 은 streaks·bulk 만 받는다 — premium 은 구간 전체를 그대로 돌려주므로 gap 개념이 없다. streaks·bulk 의 `start`·`end` 는 0 ≤ 값 ≤ 4,102,444,800(2100-01-01) — 밖이면 422(연도 오버플로 500 방지). `end ≤ 0` 은 400(end ≤ start 의 특수형).

**`/history/premium?base&unit&date`** — `base`·`unit ∈ {week, month}` 필수. `date=YYYY-MM-DD`(정확히 이 형식·연도 1970~2100, 밖이면 400. 없으면 오늘 UTC).
구간 = `date` 가 속한 ISO 주(월 00:00 UTC ~ 다음 월) 또는 달(1일 ~ 다음 달 1일), end exclusive. 구간에 기록 없으면 404. 구간 전체를 한 번에 반환한다. 응답 키:
- `dom`·`fx`·`base`·`unit` 은 요청 그대로. `start`·`end` 는 구간 경계(ISO 8601, UTC). `firstTs` 는 구간 첫 기록 시각, `count` 는 기록 수, `fetchedAt`.
- `summary` = `{firstFwd,lastFwd,minFwd,maxFwd}` — 구간 전체 통계.
- `events` = `[{dt,fwd,rev}…]` 컴팩트 — 절대시각 대신 `dt`=직전 기록으로부터 경과 초(구간 첫 기록은 0).

**`/history/streaks?base&threshold&start&end&maxGap`** — `threshold ≥ 0`(기본 0). `start`/`end` 없으면 그 코인 기록의 첫 ts / 지금+1초. 조회 구간 안에 기록이 0건이면 404(구간 밖 기록 유무는 보지 않는다), `end ≤ start` 면 400. 구간(streak) 규칙:
1. ts 오름차순으로 값이 `threshold` **이상**인 연속 기록을 한 구간으로 묶는다(같은 값 포함).
2. 값이 미만이거나 직전 기록과 `maxGap` 초보다 벌어지면 구간을 닫는다(끊긴 수집을 이어 붙여 "3시간 연속" 을 만들지 않는다).
3. fwd(kimp) 와 rev(reverse) 를 절댓값 없이 **각각** 계산한다.
4. 구간 = `{startTs,endTs,start,end(KST),durationSeconds=end−start(1개면 0),samples,maxPercent,avgPercent}`.
5. 방향 요약 = `{count,maxDurationSeconds,avgDurationSeconds,maxPercent,avgPercent(샘플 수 가중),segments}`.
6. `overall` = `{maxKimpPercent,avgKimpPercent,maxReversePercent,avgReversePercent}` 는 기준치 무관 **전체 행** 기준. `maxDurationSeconds,avgDurationSeconds,segmentCount` 는 두 방향 구간 합집합.
7. 최상위 응답 = `{base,dom,fx,thresholdPercent,maxGapSeconds,startTs,endTs,kimp,reverse,overall,scanned,lastUpdatedTs,lastUpdated,fetchedAt}`. 방향 요약 키 이름은 bulk 와 같은 `kimp`(fwd)·`reverse`(rev). `scanned` 는 전체 행 수, `lastUpdated` 는 KST.
예: 값 `0 1 3 6 29 4 31`(60초 간격), threshold 4 → 구간 1개(samples 4, max 31); threshold 5 → 2개.

**`/history/streaks/bulk?threshold&start&end&maxGap`** — 전 코인 한 번에. `start` 기본 0.
응답 `{dom,fx,thresholdPercent,maxGapSeconds,startTs,endTs,coinCount,coins:[{base,scanned,lastTs,kimp,reverse,overall}…],fetchedAt}`. **기록 없으면 404 가 아니라 빈 `coins`.**
수 MB 응답이라 압축(gzip)해 보낸다.

오류 응답:
- 404 `market_data_not_found`: 구간에 기록 없음(`/premium`), 코인 기록 없음(`/streaks`).
- 400 `invalid_request`: `date` 형식 오류, `end <= start`.
- 422(FastAPI 기본): `threshold<0`, `dom=binance` 등 파라미터 검증 실패.
- 503 `storage_unavailable`: Influx 연결 실패 또는 `INFLUX_TOKEN` 없음.

### 3.5 캔들 백필 — 백필 스크립트(코인 목록·일수 인자)
페어는 upbit×binance 고정. 캔들엔 호가가 없어 김프는 종가로 **대칭** 계산: `ratio = dom_close / (fx_close × rate)`, `fwd=(ratio−1)×100`, `rev=(1/ratio−1)×100`(셋 중 ≤0 이면 건너뜀). 코인 목록 기본값은 `BTC`, 일수 기본값은 92(바이낸스 1초봉 92일 ≈ 코인당 약 8,000 요청 — 기본값이 코인 하나인 이유). 출력은 `premium` 에 쓴다.
- 업비트: `GET /v1/candles/seconds?market=KRW-{BASE}&count=200[&to=YYYY-MM-DDTHH:MM:SSZ]`. `to` 는 exclusive UTC, 최신→과거로 `to` 를 페이지 최소 시각으로 옮기며 진행(전진 없으면 중단). 쓰는 필드 `candle_date_time_utc`·`trade_price`.
  초봉은 **체결 있던 초만** 존재(희소), **롤링 3개월** 보관·상장 이전은 빈 응답 → 중단. 환율은 같은 경로의 `minutes/1?market=KRW-USDT`.
  429·5xx·전송 오류는 1s·2s·3s 대기 후 3회 재시도, 그 외 4xx 즉시 실패. candles 그룹 10 req/s·600/min 을 라이브 수집과 같은 IP 로 나눠 쓰므로 페이지마다 0.2s.
- 바이낸스: 현물 API `GET /api/v3/klines?symbol={BASE}USDT&interval=1s&startTime={ms}&limit=1000`(과거→현재, 다음 `startTime = 마지막 closeTime+1`). 쓰는 필드 `k[0]` open ms·`k[4]` 종가 문자열·`k[6]` closeTime. **모든 초**가 있다(밀집).
  418/429 는 2·4·6s, 5xx 는 1·2·3s 대기 재시도. 페이지마다 0.1s. 가중치 2/호출.
- 합치기: 세 변동 목록(업비트 초봉·바이낸스 1초봉·환율 분봉, 각각 직전과 같은 값은 제거)을 ts 로 병합해 forward-fill. 셋이 다 갖춰지기 전 ts 는 건너뛰고, `fwd` 가 직전과 같으면 기록하지 않는다. 환율 씨앗은 하루 시작 이전 최신 분봉(목표 시작 6시간 전부터 수집).
- **재실행 안전**: 환율은 전체 구간 한 번만 수집(0건이면 중단). base 마다 `premium` 의 (첫 time, 마지막 time) 을 보고 `[목표시작, 첫 time)` 과 `[마지막 time+1, 목표 끝)` 만 채운다(가운데는 건드리지 않는다). 목표 끝 = **오늘 UTC 0시로 내림** — 오늘 치는 persist 루프 몫이라, live 기록과 겹쳐 재실행마다 소량 재수집되는 것을 막는다. 빈 응답 → 중단 규칙은 **완전한 하루 조각에만** 적용한다 — 첫·마지막 기록이나 목표 경계와 맞닿은 부분 조각의 빈 응답은 그 창에 체결이 없었을 뿐이므로 건너뛰고 계속한다(예: [그날 00:00, 첫 기록) 은 정의상 비어 있다). "이미 전부 채워져" 판정은 해당 구간 count 로.
  **UTC 하루 단위로 처리·날마다 쓴다**. 기존 기록 이전 구간은 최신 날부터 거꾸로(중단돼도 미완 구간이 첫 time 밖에 남아 다음 실행이 다시 잡는다). 같은 시각 점은 덮어쓴다.
  Ctrl-C 로 중단하면 exit 130. 다시 실행하면 남은 구간부터 이어진다.

### 3.6 web — 기록 탭 (**mock 유지**)
데이터는 002 가 제공하는 mock 사건 목록(`{sym, type:'kimp'|'rev', dom, start, durMin, peak}`, 1.5초 tick 마다 `now` 갱신)을 쓴다. `/history/*` 는 호출하지 않는다 — 실데이터 연결은 후속 스펙.
- 피벗: 스프레드 탭 행 클릭 → 선택된 심볼 설정 + 기록 탭 전환(003 배선 그대로, 초기값 `'BTC'`). 기록 탭은 선택된 심볼을 받아 우측 요약·로그를 그 심볼로 보여주고, 좌측 표 행 클릭으로 바꾼다.
- 필터바: 기간 `1주/1달/3달`(기본 1달), 유형 `전체/김프만/역프만`, 거래소 `전체/업비트/빗썸`, `사건 기준 스프레드 ≥`(기본 1.0, step 0.1). 우측 설명 `사건 = 스프레드가 기준값 이상으로 출현한 시점부터 소멸까지 · 기간 내 N건`. 필터 = `peak ≥ 기준 && 유형 && 거래소`.
- 좌 카드 "티커별 사건 통계 · {기간} — 열 클릭으로 정렬": 열 `티커|횟수|최대 지속|평균 지속|최대 김프|평균 김프|최대 역프|평균 역프|최신`, 심볼별 집계, 상위 30행. 헤더 클릭 정렬(같은 열 재클릭 시 방향 반전, 기본 횟수 내림차순, null 은 뒤·`–` 표시).
  선택 심볼 행은 accent 배경으로 강조, 김프는 POS·역프는 NEG 색. 비면 `기준을 만족하는 사건이 없습니다 — 임계값을 낮춰보세요`.
- 우 column: 요약 카드(선택된 심볼 제목, 총 사건·김프/역프 수, 평균·최장 지속, 기간 점유율 = Σ지속/기간 %). 타임라인 2줄(김프 accent / 역프 neutral, 위치·폭은 기간 대비 비율, 짧은 사건도 보이게 최소폭 보장, 축 라벨은 기간 시작~지금을 5등분한 날짜 `M/D`).
  "사건 로그 · {선택된 심볼} 최근 20건"(유형|시작|종료(끝나지 않았으면 `진행 중`)|지속|최대 스프레드, 비면 `기간 내 사건 없음`). 색·간격은 `docs/design/theme.css` 토큰, 표 구조는 002 §3.2.

## 4. 검증
- 수집 1회 후 persist → `premium` 점 수 = 메모리의 (dom, fx, base) 조합 수
- 수집 없이 persist 2번 → 같은 시각이라 점 수 불변(덮어쓰기); 수집이 한 번 더 돈 뒤 persist → 점 수 증가
- 환율 없는 국내 거래소는 그 회차 `premium` 에 dom 으로 등장하지 않는다
- `premium` 의 fwd/rev 는 슬리피지 차감 **전** 원값이다 — 같은 호가로 만든 `/spreads` 행의 `fwd + slipFwd`·`rev + slipRev` 와 일치한다(호가를 여러 단계 걷는 시드로 확인해 차감이 0 이 아닌 상태에서 고정한다)
- 입출금 조회 실패 사이클 → 그 거래소 `dw_fail` 1점; 실패 없으면 0점
- 한 회차의 점이 한 번의 쓰기로 나간다
- 수집은 아직 안 돌았는데 persist 호출 → 아무것도 쓰지 않고 0 반환
- Influx 가 닿지 않는 상태로 기동해도 `/health` 200, `/spreads` 가 메모리로 동작하고, 저장 루프는 실패 로그만 남기며, `/history/*` 는 503 `storage_unavailable`
- `INFLUX_TOKEN` 없이 기동 → 저장 루프 비활성, `/history/*` 503
- `/history/premium`: 구간 밖 기록은 안 잡힘, `events[0].dt==0`, `count==len(events)`, `summary` 가 구간 전체 기준, 기록 없으면 404, `date=abc` 400
- 구간 판정 예시: `0 1 3 6 29 4 31` threshold 4 → 1구간(samples 4, max 31), threshold 5 → 2구간; `maxGap` 초과 간격에서 구간이 끊긴다; 방향 avg 는 샘플 가중
- `/history/streaks`: `end<=start` 400, 기록 없는 코인 404, `threshold=-1` 422, `lastUpdated` 가 `+09:00` 으로 끝난다
- `/history/streaks/bulk`: 기록 없으면 200 + 빈 `coins`
- 백필 대상 구간 계산: 기록 없음 → 전체 구간, 기록 있음 → 앞·뒤 빈 구간만(가운데는 건드리지 않음); 주/월 구간 경계가 ISO 주·달력 월과 일치, 잘못된 unit 거부
- 캔들 병합: 세 값이 갖춰지기 전 ts 는 건너뜀, fwd 불변이면 기록 없음, 종가 대칭식 결과
- `/history/streaks/bulk?threshold=0`: `coinCount == len(coins)` 이고 100 을 넘는다(전 코인)
- 수동: dev compose + 서버 기동 후 **기동 약 60초 뒤**(§3.3 — 먼저 60초 잔다) `premium` 에 첫 점이 쌓이고, 75초 시점에 `/history/premium?base=BTC&unit=week` 가 `count ≥ 1`·`events[0].dt == 0` 을 돌려준다. Influx 컨테이너를 내리면 저장 실패 로그가 회차마다 찍히되 `/spreads` 는 계속 갱신, `/history/premium` 은 503. 백필 스크립트 1일 실행 → "구간 완료, 김프 기록 N건" 에서 N > 1000, 재실행 시 "이미 전부 채워져". 스프레드 행 클릭 → 기록 탭에 그 심볼 선택. 마지막으로 서버 테스트·lint, web build·lint 통과.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m pytest -q   # 163 passed (신규 46)
cd web && npm run lint && npm run build
docker compose --env-file server/.env -f docker-compose.dev.yml up -d   # + uvicorn :8020
# 기동 60초 뒤 /history/premium?base=BTC&unit=week → count 1, events[0].dt 0
# influxdb stop → /health 200·/spreads 정상·/history/* 503, 로그 "DB 저장 실패 (연속 1회)"
cd server && .venv/bin/python -m scripts.backfill BTC --days 1   # 55,090건, 재실행 "이미 전부 채워져 있습니다"
```

## 6. 갱신할 문서
- `docs/context/db.md` — measurement·tag/field·시각 단위·쓰는 쪽/읽는 쪽·로컬 접속을 이 스펙 §3.1~3.2 와 일치시킨다.
- `docs/context/status.md` — history 행(server: Influx·persist·3 라우트·bulk / web: mock, `/history/*` 미연결).
- `docs/context/dev-setup.md` — DB 절(compose 기동·Influx UI :8086·Influx 없어도 앱은 뜸). env 표를 `INFLUX_URL`·`INFLUX_TOKEN` 으로, 스모크에 `/history/premium`, 백필 스크립트 실행법.
- `docs/context/architecture.md` — 런타임 절 DB 문구를 InfluxDB 2.7 로. DB 흐름에 2 measurement 반영, casing 예외 목록에 `/history/*`.
- `docs/context/product.md` — 용어 절에 streak(구간) 1줄.
- `CLAUDE.md` 스펙 인덱스 상태.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것: `core/influx.py`(클라이언트)·`core/persist.py`(60초 루프), `features/history/`(models·service·router·tests 24개), `scripts/backfill.py`, 루트 `docker-compose.dev.yml`, `web/features/history/Tab.tsx`. 수정: `collector.py`(락 공개·dw_failed 자리)·`main.py`(lifespan 배선)·`App.tsx`(기록 탭·선택 심볼 `'BTC'`)·`pyproject.toml`(influxdb-client).
- 추측한 지점: `fx` 를 Literal 쿼리로 노출(422 경로), `base` 패턴 검증(Flux 주입 방어), 빈 방향 요약은 0.0·빈 segments, 하루 조각 skip 판정 = count>0, dev compose UI 비밀번호도 `${INFLUX_TOKEN}` 재사용, bulk coins 는 base 오름차순, 기간 내부값 7d/30d/90d.
- 실행 중 함께 고친 스펙 절: §3.5 백필 목표 끝 = 오늘 UTC 0시 내림(live persist 와 겹침 해소), §3.4 streaks 404 = 조회 구간 안 0건 판정.
- 남은 빚: 캔들 수집기·백필 실호출 자동 테스트 없음(순수 계산만 테스트) / 기록 탭 실데이터 연결·spark 채우기(후속 스펙) / bulk 92일 전 코인 응답 성능 미실측
