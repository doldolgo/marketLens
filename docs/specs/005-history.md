# 005 — history

상태: DONE | 의존: 001(collect — 틱), 002(web-shell), 003(spreads — 원값 수식), 009(tick-store — Influx 쓰기)

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
매초의 김프/역프 원값이 InfluxDB `premium` 에 초 단위로 쌓인다 — 점을 만드는 것은 001 의 틱, 쓰는 것은 009 의 flusher 다. 쌓인 기록을 `GET /history/*` 로 구간 조회·통계(streaks) 한다. 업비트 초봉 × 바이낸스 1초봉으로 과거 3개월 김프를 백필하는 스크립트를 제공한다.
엔진·데이터 모델·env·로컬 접속은 `docs/context/db.md` 가 정한다. 이 스펙은 그 모델을 쓰는 동작만 쓴다.
끝나면 dev compose(Influx·Redis)를 올리고 서버를 :8000 에 띄우면 1분 뒤 `premium` 에 점이 남고, `/history/premium` 이 그 주의 기록을 돌려준다.

## 2. 범위
- 만드는 것: 공유 인프라의 Influx 클라이언트(연결·읽기/쓰기), 기능 폴더 `features/history`(`/history/premium` `/history/streaks` `/history/streaks/bulk`), 백필 스크립트(코인 목록·일수 인자), dev compose(Influx 2.7 + Redis 7 — 루트 `docker-compose.dev.yml`. 배포용 `docker-compose.yml` 은 007 몫).
- 하지 않는 것: Influx 쓰기 루프 — `premium`·`dw_fail` 은 009 의 flusher 가 60초마다 Redis 전량을 옮겨 쓴다. `/spreads` 의 `spark` 도 009 가 채운다. 기록 탭 화면(013 — 저장 시점에 감지한 `premium_event` 로 그린다. 이 스펙의 web 몫은 없다). 빗썸 페어 백필(빗썸엔 초봉 API 없음 — 빗썸×바이낸스는 실시간 기록으로만 쌓인다). 보존기간 정리(retention 무제한). 배포 compose(스펙 007). 재기동 직후 조회 API 의 DB 폴백 — 메모리가 비면 기존 404 그대로다.
- 앱 셸(002/003)과의 접점: 셸의 기록 탭 자리는 이 기능의 `web/src/features/history/Tab.tsx` 이고, 스프레드 행 클릭이 넘기는 선택된 심볼의 초기값은 `'BTC'` 다.

## 3. 동작

### 3.1 설정·로컬 DB
env(`INFLUX_URL`·`INFLUX_TOKEN`)·org·bucket 은 `db.md` 대로.
dev compose 는 Influx 2.7 과 Redis(009) 를 띄운다. 첫 기동 시 org·bucket `marketlens` 가 만들어지고 admin 토큰은 `.env` 의 `INFLUX_TOKEN` 과 같아야 한다 — 앱이 그 토큰으로 붙기 때문이다. 데이터는 볼륨에 남는다.
컨테이너에는 `DOCKER_INFLUXDB_INIT_*` 환경변수로 전달하되 값은 compose 변수 치환 `${INFLUX_TOKEN}` 으로 `server/.env` 에서 온다 — 기동은 `docker compose --env-file server/.env -f docker-compose.dev.yml up -d`. 토큰을 파일 한 곳에만 두기 위해서다.
**Influx 가 닿지 않아도 앱은 뜬다**: 기동 시 연결 실패는 에러 로그 1줄. 009 의 flusher 가 다음 회차에 재시도하고(그동안 틱은 Redis 에 쌓인다), 조회는 메모리로 정상 동작한다. `/health` 는 200, `/history/*` 만 503 `storage_unavailable`. `INFLUX_TOKEN` 이 없으면 flusher 비활성·`/history/*` 503. 수집·조회가 저장소 장애에 볼모 잡히면 안 되기 때문이다.

### 3.2 measurement
모델은 `db.md` 의 `premium`·`dw_fail` 그대로. 이 스펙이 쓰는 부분:
- `premium` — 009 의 flusher 와 백필이 쓰고, `/history/*` 전부가 읽는다. time 은 틱 시각(초 정밀도). 같은 (dom, fx, base, time) 은 덮어쓴다.
- `dw_fail` — 틱의 `dwFailed` 에 실린 거래소마다 1점(009 의 flusher 가 쓴다, time = 틱 시각). 읽는 HTTP 엔드포인트는 없다.

### 3.3 김프 점 규칙 — 틱이 만들고 flusher 가 쓴다
점을 만드는 것은 001 의 틱 루프(매초, LiveStore 의 최신 시세 기준)이고 Influx 에 쓰는 것은 009 의 flusher(60초마다 Redis 전량, 멱등·재시도)다. 이 스펙은 점의 **규칙**만 정한다.
- **김프 점 규칙**: base 마다 (국내 거래소 × 해외 거래소) 조합. 국내 행은 호가가 있고 **그 거래소의 USDT 시세**가 있어야 한다(시세 없는 국내 거래소는 빠진다 — 남의 시세를 빌리지 않는다). 해외 행도 호가 필수.
  수식은 003 의 `core/premium.py` 공개 함수 `premium_percent(*, buy_krw: float, sell_krw: float) -> float` = `(sell/buy − 1) × 100` 을 import 해 쓴다(재정의 금지). `fwd = premium_percent(buy_krw=fx_ask × rate_ask, sell_krw=dom_bid)`, `rev = premium_percent(buy_krw=dom_ask, sell_krw=fx_bid × rate_bid)` — **최우선 1단계 기준의 원값(raw)** 이다. `/spreads` 는 같은 식을 체결 규모만큼 걸은 평균가에 적용해 슬리피지 차감 후 순값을 내므로(003 §3.2-4) 두 값은 다르고, 그 차이가 `/spreads` 의 `slipFwd`·`slipRev` 다 — 저장 시점에는 체결 규모가 정의되지 않아 아카이브는 원값을 쓴다(003 §2). 여섯 값 중 하나라도 ≤ 0 이면 건너뜀. 점 `(dom, fx, base, time=틱 시각 초, fwd, rev)`.
- 입출금 조회가 실패 상태인 거래소마다 `dw_fail` 1점(time = 틱 시각).
- 시세가 멈춰도 틱은 매초 생기므로 같은 값이 초마다 새 점으로 남는다 — 주기가 곧 DB 증가 속도(하루 약 1.26억 점, 009 §3.5).

### 3.4 `GET /history/*`
HTTP JSON 키와 복합어 쿼리 파라미터는 camelCase다. 모든 시각 `*Ts` 는 epoch 초, `fetchedAt` 은 ms.
**응답은 조회부터 JSON 바이트까지 스레드에서 끝내고 이벤트 루프에는 바이트만 넘긴다(2026-09-28).** `/history/*` 모든 경로(013 `events`·014 `candles` 포함)가 Influx 조회·빌드·camelCase·JSON 인코딩을 한 스레드 작업으로 하고, 루프는 받은 바이트를 내보내기만 한다(압축은 전역 GZip 미들웨어, 001 §3.1 — 013·014 의 공유 캐시 경로는 미리 압축해 둔 바이트를 준다). 수집 박스의 루프는 거래소 수신·틱과, `api` 의 루프는 `/ws/spreads` 허브와 같은 루프라서다 — premium 1주(57만 점)를 루프에서 직렬화하면 1초 가까이 멈췄다. 큰 목록(`/history/premium` 의 `events`)은 점마다 모델을 만들지 않고 camelCase dict 를 바로 만들어 **2,000건씩** 인코딩해 이어 붙인다 — JSON 인코딩 한 번이 GIL 을 수백 ms 쥐지 않게. streaks·bulk 는 모델을 거치되 같은 스레드에서 인코딩한다. 두 경로(모델·dict)는 같은 바이트다(키 순서·숫자 표기·`fetchedAt` 위치까지). 오류 응답(400·404·503)의 상태·모양·문구도 같다.
공통 파라미터: `dom` ∈ {upbit, bithumb}(기본 upbit), `fx` ∈ {binance, bybit, bitget, okx}(기본 binance — 045). `maxGap`(기본 600, ≥1) 은 streaks·bulk 만 받는다 — premium 은 구간 전체를 그대로 돌려주므로 gap 개념이 없다. streaks·bulk 의 `start`·`end` 는 0 ≤ 값 ≤ 4,102,444,800(2100-01-01) — 밖이면 422(연도 오버플로 500 방지). `end ≤ 0` 은 400(end ≤ start 의 특수형).
**세 경로는 무거운 조회라 한 번에 읽는 양과 동시 수를 묶는다(2026-09-28 사람 결정).** 창 상한은 streaks 604,800초(7일), bulk 3,600초(1시간), premium 은 `unit=week` 만이다 — 넘으면 400 `invalid_request`(streaks·bulk 는 메시지 `window exceeds limit: …`, `detail` `{"limitSec": 상한}`, premium `month` 는 저장소를 읽기 전에). streaks·bulk 의 `start` 가 없으면 `end − 상한`이다(`/history/candles` 의 1,440창 상한과 같은 방식). 세 경로는 앱 안의 게이트 하나를 같이 쓴다: 한 번에 하나만 돌고, 이미 돌고 있으면 기다리게 하지 않고 곧바로 429 `{"error":{"code":"busy","message":…,"detail":null}}` 이고 머리 `Retry-After: 1` 을 싣는다(조회 하나가 끝나기를 기다렸다 다시 부르면 된다). 혼잡 판정은 인자 검증(400·404)보다 먼저다. 게이트는 요청이 아니라 스레드의 조회·계산이 끝날 때 열린다 — 요청이 끊겨도 조회 스레드는 돌기 때문이다. 봉·사건 조회(013·014)는 게이트를 거치지 않는다. 이유: Influx 는 쿼리를 동시 2개까지만 돌려(021 §3.1) 무거운 조회들이 칸을 다 쥐면 차트·랜딩 조회가 수 초씩 줄을 섰고(동시 3칸 로컬 측정 3.5초 → 게이트 뒤 29ms), 상한 없는 조회 한 번이 api(t4g.micro 1GB, `/ws/spreads` 허브와 같은 프로세스)에 +0.5~1.3GB 를 잡았다. 공개 주소에서는 세 경로가 닫혀 있지만(028) 관리자 화면(029)과 박스 안 호출은 그대로 부르므로 상한·게이트를 둔다.
**streaks·bulk 는 원값을 흘려 받으며 센다(2026-09-28 사람 결정).** 저장소는 core 공개 함수 `InfluxClient.stream_premium(*, dom: str, fx: str, base: str | None, start: int, stop: int) -> Iterator[tuple[str, str, int, float]]` 로 `(base, field, ts, value)` 를 (코인, 방향) 줄기마다 시각 오름차순으로 흘려보낸다 — pivot·group·sort 없이 두 필드의 시리즈 표 그대로, 응답은 64KB 조각으로 받아 헤더 CSV 를 줄마다 읽는다. 서비스는 줄기마다 상태 하나(열린 구간의 시작·끝·표본·최대·보정 합과 줄기 전체의 수·보정 합·최대)로 아래 규칙을 적용하고 점 목록을 만들지 않는다 — 메모리는 점 수가 아니라 구간 수에 비례한다. 평균은 CPython 3.12 `sum()` 과 같은 보정 합(Neumaier)으로 구해 점 목록으로 `sum()/len()` 한 결과와 바이트까지 같다. 흘리는 도중 실패하면 받은 것을 버리고 503 이다. 같은 시각에 fwd·rev 중 한 필드만 있는 반쪽 점은 방향마다 따로 센다 — `scanned`·`lastUpdatedTs`(bulk 는 `lastTs`)는 fwd 줄기 기준이고, 한 방향 줄기뿐인 코인은 기록 없음(streaks 404·bulk 에서 빠짐)이다. 두 필드는 늘 같이 쓰이므로(db.md) 실제 데이터에서는 차이가 없다.
**premium 도 점 목록 없이 흘려 받는다(2026-09-28 사람 결정).** 같은 `stream_premium` 으로 fwd·rev 두 줄기를 받아 방향마다 시각·값 배열(점 하나 방향당 16바이트)에만 담고, 두 줄기를 다 받은 뒤(줄기 순서는 정해지지 않는다) 시각이 같은 점끼리 이어 컴팩트 `events`·`summary` 를 만들며 2,000건씩 인코딩한다(스레드 안). 한 방향에만 있는 시각(반쪽 점)은 건너뛴다 — 저장소에서 pivot 한 행 가운데 반쪽 행을 버리던 목록 경로와 같은 결과라 응답 바이트가 같고(`fetchedAt` 제외, streaks 와 달리 반쪽 점 처리 차이가 없다), 반쪽 점만 있는 주는 404 다. `summary` 의 최소·최대는 목록의 `min`·`max` 처럼 처음 만난 값을 지킨다(음의 0). 이유: 목록 경로는 1주(57만 점) 한 요청에 파이썬 메모리 최고치 440MB·프로세스 RSS +620MB 를 잡았다(행 객체·점 dict) — api(t4g.micro 1GB)가 한 요청으로 감당할 양이 아니다.

**`/history/premium?base&unit&date`** — `base`·`unit` 필수, `unit` 은 `week` 만 받는다(`month` 는 400, 그 밖의 값은 422). 공개 주소에서는 404(028) — 관리자 페이지 API 문서에서 부른다(029). `date=YYYY-MM-DD`(정확히 이 형식·연도 1970~2100, 밖이면 400. 없으면 오늘 UTC).
구간 = `date` 가 속한 ISO 주(월 00:00 UTC ~ 다음 월), end exclusive. 구간에 기록 없으면 404. 구간 전체를 한 번에 반환한다. 응답 키:
- `dom`·`fx`·`base`·`unit` 은 요청 그대로. `start`·`end` 는 구간 경계(ISO 8601, UTC). `firstTs` 는 구간 첫 기록 시각, `count` 는 기록 수, `fetchedAt`.
- `summary` = `{firstFwd,lastFwd,minFwd,maxFwd}` — 구간 전체 통계.
- `events` = `[{dt,fwd,rev}…]` 컴팩트 — 절대시각 대신 `dt`=직전 기록으로부터 경과 초(구간 첫 기록은 0).

**`/history/streaks?base&threshold&start&end&maxGap`** — `threshold ≥ 0`(기본 0). 공개 주소에서는 404(028) — 관리자 페이지 API 문서에서 부른다(029). `end` 없으면 지금+1초, **`start` 없으면 `end − 7일`(604,800초)**, 창이 7일을 넘으면 400 — 전 구간 조회를 막기 위해서다(2,700만 점 위에서 전 구간 조회는 Influx 를 죽인다, status.md 알려진 빚). 응답 `startTs` 는 실제로 쓴 값. 조회 구간 안에 기록이 0건이면 404(구간 밖 기록 유무는 보지 않는다), `end ≤ start` 면 400. 구간(streak) 규칙:
1. ts 오름차순으로 값이 `threshold` **이상**인 연속 기록을 한 구간으로 묶는다(같은 값 포함).
2. 값이 미만이거나 직전 기록과 `maxGap` 초보다 벌어지면 구간을 닫는다(끊긴 수집을 이어 붙여 "3시간 연속" 을 만들지 않는다).
3. fwd(kimp) 와 rev(reverse) 를 절댓값 없이 **각각** 계산한다.
4. 구간 = `{startTs,endTs,start,end(KST),durationSeconds=end−start(1개면 0),samples,maxPercent,avgPercent}`.
5. 방향 요약 = `{count,maxDurationSeconds,avgDurationSeconds,maxPercent,avgPercent(샘플 수 가중),segments}`.
6. `overall` = `{maxKimpPercent,avgKimpPercent,maxReversePercent,avgReversePercent}` 는 기준치 무관 **전체 행** 기준. `maxDurationSeconds,avgDurationSeconds,segmentCount` 는 두 방향 구간 합집합.
7. 최상위 응답 = `{base,dom,fx,thresholdPercent,maxGapSeconds,startTs,endTs,kimp,reverse,overall,scanned,lastUpdatedTs,lastUpdated,fetchedAt}`. 방향 요약 키 이름은 bulk 와 같은 `kimp`(fwd)·`reverse`(rev). `scanned` 는 전체 행 수, `lastUpdated` 는 KST.
예: 값 `0 1 3 6 29 4 31`(60초 간격), threshold 4 → 구간 1개(samples 4, max 31); threshold 5 → 2개.

**`/history/streaks/bulk?threshold&start&end&maxGap`** — 전 코인 한 번에. 공개 주소에서는 404(028) — 관리자 페이지 API 문서에서 부른다(029). `end` 없으면 지금+1초, `start` 없으면 `end − 1시간`(3,600초), 창이 1시간을 넘으면 400.
응답 `{dom,fx,thresholdPercent,maxGapSeconds,startTs,endTs,coinCount,coins:[{base,scanned,lastTs,kimp,reverse,overall}…],fetchedAt}`. **기록 없으면 404 가 아니라 빈 `coins`.**
수 MB 응답이라 압축(gzip)해 보낸다.

오류 응답:
- 404 `market_data_not_found`: 구간에 기록 없음(`/premium`), 코인 기록 없음(`/streaks`).
- 400 `invalid_request`: `date` 형식 오류, `end <= start`, 창 상한 초과(streaks 7일·bulk 1시간), premium `unit=month`.
- 429 `busy`(머리 `Retry-After: 1`): 무거운 세 경로 중 하나가 이미 돌고 있음.
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
- **재실행 안전**: 환율은 전체 구간 한 번만 수집(0건이면 중단). base 마다 `premium` 의 (첫 time, 마지막 time) 을 보고 `[목표시작, 첫 time)` 과 `[마지막 time+1, 목표 끝)` 만 채운다(가운데는 건드리지 않는다). 목표 끝 = **오늘 UTC 0시로 내림** — 오늘 치는 실시간 틱(009) 몫이라, live 기록과 겹쳐 재실행마다 소량 재수집되는 것을 막는다. 빈 응답 → 중단 규칙은 **완전한 하루 조각에만** 적용한다 — 첫·마지막 기록이나 목표 경계와 맞닿은 부분 조각의 빈 응답은 그 창에 체결이 없었을 뿐이므로 건너뛰고 계속한다(예: [그날 00:00, 첫 기록) 은 정의상 비어 있다). "이미 전부 채워져" 판정은 해당 구간 count 로.
  **UTC 하루 단위로 처리·날마다 쓴다**. 기존 기록 이전 구간은 최신 날부터 거꾸로(중단돼도 미완 구간이 첫 time 밖에 남아 다음 실행이 다시 잡는다). 같은 시각 점은 덮어쓴다.
  Ctrl-C 로 중단하면 exit 130. 다시 실행하면 남은 구간부터 이어진다.

### 3.6 web — 기록 탭
기록 탭은 013 §3.5(`/history/events` 사건 표). 이 스펙의 web 몫은 없다 — `/history/streaks` 는 API 로만 남고 화면은 안 쓴다.

## 4. 검증
- 점의 수·시각·값은 009 §4 가 검증한다(틱 → flusher). 이 스펙은 점 규칙만 본다: USDT 시세 없는 국내 거래소는 틱의 `rows` 에 dom 으로 등장하지 않는다
- `premium` 의 fwd/rev 는 슬리피지 차감 **전** 원값이다 — 같은 호가로 만든 `/spreads` 행의 `fwd + slipFwd`·`rev + slipRev` 와 일치한다(호가를 여러 단계 걷는 시드로 확인해 차감이 0 이 아닌 상태에서 고정한다)
- 입출금 조회 실패 상태인 거래소는 틱의 `dwFailed` 에 담기고 flusher 가 그 `ts` 로 `dw_fail` 1점을 쓴다; 실패 없으면 0점
- Influx 가 닿지 않는 상태로 기동해도 `/health` 200, `/spreads` 가 메모리로 동작하고, `/history/*` 는 503 `storage_unavailable`
- `INFLUX_TOKEN` 없이 기동 → `/history/*` 503
- `/history/premium`: 구간 밖 기록은 안 잡힘, `events[0].dt==0`, `count==len(events)`, `summary` 가 구간 전체 기준, 기록 없으면 404, `date=abc` 400
- 구간 판정 예시: `0 1 3 6 29 4 31` threshold 4 → 1구간(samples 4, max 31), threshold 5 → 2구간; `maxGap` 초과 간격에서 구간이 끊긴다; 방향 avg 는 샘플 가중
- `/history/streaks`: `end<=start` 400, 기록 없는 코인 404, `threshold=-1` 422, `lastUpdated` 가 `+09:00` 으로 끝난다
- `/history/streaks`: `start` 없으면 `startTs == endTs − 604800` 이고 그보다 오래된 기록은 `scanned` 에 안 잡힌다; `end` 만 주면 `start = end − 7일` / `bulk`: `start` 없으면 `startTs == endTs − 3600`
- 창 상한: streaks 604,801초 400(`detail.limitSec` 604800)·604,800초 200·오래된 `start` 만 주면 400, bulk 3,601초 400(`limitSec` 3600)·3,600초 200, premium `unit=month` 400 이고 저장소를 읽지 않는다 (`test_streaks_api.py`·`test_bulk_api.py`·`test_premium_api.py`)
- 게이트: 무거운 조회가 도는 동안 세 경로는 429 `busy`(머리 `Retry-After: 1`, 200 에는 없다), 봉·사건 조회는 200; 요청을 끊어도 조회 스레드가 끝날 때까지 429; 실패한 조회도 게이트를 연다 (`test_heavy_gate.py`)
- 흘려 세기: 수집 공백·음의 0·1e15 가 섞인 무작위 원값에서 streaks(문턱 0·0.5·1·1.3)·bulk(0·0.5·1)가 점 목록으로 센 계산과 같은 바이트, 10만 점 streaks 의 파이썬 메모리 최고치 2MB 미만, 도중 끊김 503, 반쪽 점은 방향마다 (`test_streaks_stream.py`) / `stream_premium` 은 pivot·group·sort 없이 네 칸, 1바이트 조각에서도 같은 점, 오류 표·줄 중간에서 끝난 응답은 저장소 실패, 덜 읽은 연결은 닫는다 (`tests/test_influx_stream.py`)
- `/history/streaks/bulk`: 기록 없으면 200 + 빈 `coins`
- 백필 대상 구간 계산: 기록 없음 → 전체 구간, 기록 있음 → 앞·뒤 빈 구간만(가운데는 건드리지 않음); 주 구간 경계가 ISO 주와 일치, `unit=month` 400·그 밖의 unit 422
- 캔들 병합: 세 값이 갖춰지기 전 ts 는 건너뜀, fwd 불변이면 기록 없음, 종가 대칭식 결과
- `/history/streaks/bulk?threshold=0`: `coinCount == len(coins)` 이고 100 을 넘는다(전 코인)
- premium 흘려 읽기: 반쪽 점(구간 앞·가운데·끝)과 구간 밖 점이 섞인 두 줄기(rev 가 먼저)에서 응답이 점 목록 경로와 같은 바이트이고(`fetchedAt` 제외) 점 목록을 읽지 않는다, 반쪽 점만 있는 주는 두 경로 모두 같은 404 (`test_encoding.py`)
- 응답 인코딩: `/history/premium` 이 모델 경로와 같은 바이트다(`fetchedAt` 제외 — 2,000건 경계 앞뒤·4,500점, 음의 0·지수 표기 포함), streaks·bulk 도 같다 / 400·404·503 이 모델 경로의 예외를 옮긴 것과 같은 상태·바이트다 / JSON 인코딩이 루프 스레드에서 한 번도 돌지 않는다 (`features/history/tests/test_encoding.py`)
- 수동: dev compose + 서버 기동 후 **기동 약 60초 뒤**(009 flusher 첫 회차) `premium` 에 첫 점이 쌓이고, 75초 시점에 `/history/premium?base=BTC&unit=week` 가 `count ≥ 1`·`events[0].dt == 0` 을 돌려준다. Influx 컨테이너를 내리면 flusher 실패 로그가 회차마다 찍히되 `/spreads` 는 계속 갱신, `/history/premium` 은 503. 다시 올리면 밀린 구간이 한 회차에 들어가 `count` 에 구멍이 없다. 백필 스크립트 1일 실행 → "구간 완료, 김프 기록 N건" 에서 N > 1000, 재실행 시 "이미 전부 채워져". 기록 탭 확인은 013 §4. 마지막으로 서버 테스트·lint, web build·lint 통과.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
기록 탭 실데이터·7일 기본 창 세션(2026-09-07). 다섯 명령 모두 통과해야 커밋한다.
```bash
cd server && .venv/bin/ruff check .            # All checks passed!
cd server && .venv/bin/ruff format .           # 183 files left unchanged
cd server && .venv/bin/python -m pytest -q     # 475 passed, 1 warning in 5.14s (history 37)
cd web && npm run lint                         # oxlint src — 출력 없음(error 0)
cd web && npm run build                        # tsc -b && vite build — ✓ built in 397ms
```
기록 탭은 로컬 vite(:8010) 를 SSH 포워딩으로 EC2 서버 컨테이너에 붙여 실데이터로 확인했다 — 요청에 `start`·`end`·`threshold`·`dom` 이 항상 붙고, BTC 7일 조회 ≈ 2초, 기준 0.1 에서 김프 35·역프 28 건, 방향별 마지막 구간만 `진행 중`, 빗썸 전환 시 재조회. 서버의 7일 기본 창은 EC2 에 아직 배포 전(§4 의 `start` 없는 호출 실측은 배포 후).
§4 의 BE 항목마다 `server/app/features/history/tests/` 에 최소 1개가 있다(§7 파일 목록). 틱 → Redis → flusher → `/history/*` 전 경로는 `server/tests/test_tick_store_history.py`(009) 가 fake Influx 로 본다.

기동 스모크(Influx·Redis·거래소 없이 — 이 망은 거래소 도메인을 막는다): `.venv/bin/python -m uvicorn app.main:app --port 8041` 을 env 만 바꿔 두 번 띄웠다.
- `INFLUX_TOKEN=`(없음): `GET /health` → 200 `{"status":"ok"}`, `GET /spreads` → 404 `market_data_not_found`(메모리 경로 정상 — 시세가 없을 뿐), `/history/premium?base=BTC&unit=week`·`/history/streaks?base=BTC`·`/history/streaks/bulk` → 503 `storage_unavailable`("INFLUX_TOKEN 이 설정되지 않았습니다"). 로그 `INFLUX_TOKEN 이 없어 Influx 를 쓰지 않는다 — /history/* 는 503` 1줄.
- `INFLUX_TOKEN=x INFLUX_URL=http://127.0.0.1:1`(닿지 않음): 기동 로그 `InfluxDB 연결 실패: … — 회차마다 재시도한다` 1줄(이력·spark 복원은 빈 채로 시작), `/health` 200, `/history/*` 3종 → 503 `storage_unavailable`("저장소 조회에 실패했습니다: …"), `date=abc` → 400 `invalid_request`, `threshold=-1` → 422 `{"detail":[…]}`.
확인 후 프로세스를 죽였다(`lsof -i :8041` 비어 있음).

§4 의 수동 항목(dev compose 위 첫 점 ≈60초·75초 시점 `count ≥ 1`·Influx 를 내렸다 올리면 `count` 에 구멍 없음·백필 1일 N > 1000·재실행 "이미 전부 채워져")과 `bulk?threshold=0` 의 실데이터 100코인 초과는 실거래소 수집·Influx 가 필요해 **EC2 에서 확인 필요**(§7 남은 빚). 스프레드 행 클릭 → 기록 탭 심볼 선택은 003 §5 의 스텁 FE 확인에 포함돼 있다.

## 6. 갱신할 문서
- `docs/context/db.md` — measurement·tag/field·시각 단위·쓰는 쪽/읽는 쪽·로컬 접속을 이 스펙 §3.1~3.2 와 일치시킨다.
- `docs/context/status.md` — history 행(server: Influx·flusher(009)·3 라우트·bulk·7일 기본 창 / web: 사건 로그 실데이터, 통계·차트는 후속) 과 알려진 빚 (005) 의 전 구간 조회 문구.
- `docs/context/dev-setup.md` — DB 절(compose 기동·Influx UI :8086·Influx 없어도 앱은 뜸). env 표를 `INFLUX_URL`·`INFLUX_TOKEN` 으로, 스모크에 `/history/premium`, 백필 스크립트 실행법.
- `docs/context/architecture.md` — 런타임 절의 저장소 문구(InfluxDB 2.7), BE 흐름의 `premium`·`dw_fail`, "현재 구조" 의 history 항목. 계약 규칙은 전 엔드포인트 camelCase 라 `/history/*` 예외 목록은 없다.
- `docs/context/product.md` — 용어 절에 streak(구간) 1줄.
- `CLAUDE.md` 스펙 인덱스 상태.

## 7. 실행 보고 (실행 세션이 채움)
001(틱)·009(flusher)·012(바이낸스 스트림) 위에서 돌아가는 현재 구현의 보고다.
- 만든 것 (파일 목록):
  - `server/app/core/influx.py` — influxdb-client 를 import 하는 유일한 곳. `InfluxPoint`·`premium_point`·`dw_fail_point`(모델은 db.md), line protocol 직렬화(초 정밀도), `InfluxClient`(lazy 연결·`ping`·`write`·`query_premium`·`count_premium`·`first_last_premium`, 009 의 `query_spark`, 011 의 `collect_fail` 점·조회). 모든 실패는 `InfluxUnavailableError` 하나 — 호출자는 "재시도 또는 503" 으로만 다룬다.
  - `server/app/features/history/service.py` — 순수 계산: 주 구간 경계, `/premium` 의 컴팩트 events·summary(두 방향 줄기를 시각으로 잇기), streak 구간 판정(threshold 이상·maxGap — 줄기별 상태기계)·방향 요약(샘플 가중)·overall(전체 점 + 두 방향 합집합), bulk 코인별 집계, 창 상한. 리더는 `stream_premium`(응답 경로) Protocol 로 받고, `query_premium`(점 목록)은 premium 의 모델 경로가 테스트 짝으로만 쓴다.
  - `server/app/features/history/router.py` — 3 엔드포인트. 파라미터 검증은 FastAPI `Query`(422), 업무 오류는 `{"error":…}`(400·404), 혼잡은 429(`gate.py` 의 게이트), 저장소 없음·실패는 503. Influx 클라이언트는 동기라 스레드에서 돌린다.
  - `server/app/features/history/models.py` — 응답 모델(snake_case → 라우터에서 camelCase).
  - `server/app/features/history/tests/` — `helpers.py`(fake 리더 + lifespan 없는 앱, 선택적 LiveStore), `test_premium_api.py`·`test_streaks_api.py`·`test_bulk_api.py`(§3.4 계약·오류 4종·경계값), `test_point_rules.py`(§3.3 점 규칙·원값·`dw_fail`·저장소 장애 격리·bulk 100코인 초과).
  - `server/scripts/backfill.py` — §3.5 그대로. 순수 계산(`plan_day_slices`·`is_full_day`·`dedup_changes`·`merge_premiums`·`rates_for_slice`)과 거래소 호출(거래소별 재시도 정책·페이지 간격)을 나눈다. 테스트 `server/tests/test_backfill.py` 는 순수 계산만.
  - 루트 `docker-compose.dev.yml` — Influx 2.7 + Redis 7(009), 토큰은 `${INFLUX_TOKEN}` 치환.
  - `web/src/App.tsx` 가 선택 심볼(초기 `'BTC'`)과 탭 전환을 든다. 002 의 mock 사건 목록(`feed.events`)은 함께 지웠다. 기록 탭 파일들은 013 이 `/history/events` 용으로 다시 썼다.
  - `server/app/main.py` — 토큰이 있을 때만 `InfluxClient` 를 만들어 `app.state.influx` 에 두고 ping 실패는 에러 1줄. flusher(009)·이력 복원(011)·spark 복원(009)이 같은 클라이언트를 쓴다.
- 추측한 지점 (묻지 않고 정한 것 — 전부 본문에 반영):
  - `fx` 는 `Literal["binance", "bybit", "bitget", "okx"]` 쿼리로 노출한다 — 다른 값은 FastAPI 422(§3.4 오류 표의 "파라미터 검증 실패").
  - `base` 는 `^[A-Za-z0-9]{1,20}$` 패턴으로 검증한다(422) — Flux 문자열에 들어가므로 이스케이프와 함께 이중 방어.
  - 빈 방향 요약은 `count 0`·수치 0.0·빈 `segments`, bulk 의 `coins` 는 base 오름차순.
  - 백필의 "이미 채워진 날" 판정은 그 조각의 `count > 0`. dev compose 의 UI 비밀번호도 `${INFLUX_TOKEN}` 재사용.
  - 기록 탭의 기간 내부값은 `7d/30d`. 심볼 입력은 영숫자만 받아 대문자로 보낸다(서버 `base` 패턴과 같다).
  - 저장소 장애 검증은 lifespan 없이 앱 상태에 fake 리더(실패)·빈 리더를 꽂아 본다 — 실제 기동 경로는 §5 의 :8041 스모크 두 번이 대신한다.
- 실행 중 함께 고친 절: §2 — `/spreads` 의 `spark` 는 009 가 채운다(이 스펙의 후속 몫이 아니다), 앱 셸 접점을 지금 모양(기록 탭 = 이 기능의 Tab, 선택 심볼 초기값 `'BTC'`)으로. §6 — architecture.md 항목을 지금 문서 구조(계약 규칙에 casing 예외 목록 없음)로.
- 남은 빚:
  - §4 수동 항목 전부(첫 점·`count` 구멍·백필 1일·재실행 문구)와 `bulk` 실데이터 100코인 초과 — **EC2 에서 확인 필요**.
  - 캔들 수집기(`fetch_*`)·백필 실호출의 자동 테스트 없음(순수 계산만).
- 2026-09-28 성능 개선: 응답 인코딩을 스레드로(§3.4 — premium 은 dict 로 2,000건씩, streaks·bulk 는 모델 인코딩만 옮김). 이유: 스레드는 조회·빌드만 하고 model_dump·camelCase·JSON 렌더는 루프에서 돌았다. 측정(로컬, 가짜 리더 570,569점): premium 1주 루프 최대 정지 996 → 14ms, 요청 wall 3,019 → 435ms(camelCase 메모 001 §3.1 포함). 바이트 동일.
- 2026-09-28 성능 개선 — 조회 상한·흘려 세기: §3.4 의 창 상한(streaks 7일·bulk 1시간·premium 주)·무거운 세 경로 게이트(동시 1개, 429 `busy`)와 streaks·bulk 흘려 세기(`stream_premium`). 이유: 상한 없는 GET 한 번이 api 메모리를 +0.5~1.3GB 잡았고(행 1개 ≈840B), 무거운 조회들이 Influx 의 동시 칸(021 은 2개)을 다 쥐면 가벼운 조회가 수 초 줄을 섰다. 측정(로컬 influxdb:2.7, 기준선 코드와 같은 데이터·같은 조건): BTC 7일 streaks(59.8만 점) wall 2,817 → 834ms, 파이썬 CPU 1,778 → 812ms, Influx CPU 1,429 → 383ms, 파이썬 메모리 최고치 458 → 5.1MB; bulk 20코인 1시간 wall 307 → 102ms, 51 → 1.0MB. 문턱 0·0.5·1 의 streaks·bulk 응답이 기준선과 바이트까지 같다. `bulk` 의 `start` 기본값은 창 상한(1시간)으로 바꿨다 — 7일 기본값을 두면 `start` 없는 요청이 늘 400 이 된다.
- 2026-09-28 성능 개선 — premium 흘려 읽기·429 머리: `/history/premium` 을 `stream_premium` 두 줄기의 배열 이음으로(§3.4 — 점 목록·점 dict 목록 없음, 인코딩 조각은 응답 크기 사본 하나로 잇는다), 429 `busy` 에 `Retry-After: 1`. 이유: 1주 한 요청이 api 메모리를 수백 MB 잡았고, 429 를 받은 호출자가 언제 다시 부를지 알 길이 없었다. 측정(로컬 influxdb:2.7, BTC 한 ISO 주 576,248점·응답 36.1MB, 전후 바이트 동일): 파이썬 메모리 최고치(tracemalloc) 440 → 82MB, 프로세스 RSS 최고치 증가 620 → 102MB, wall 2,801 → 1,200ms, 파이썬 CPU 1,908 → 1,166ms. 남는 최고치는 인코딩 조각과 이은 응답 바이트(각 36MB)다. 같은 로컬 Influx 에 반쪽 점(주 첫 초 fwd 만·가운데 rev 만·끝 초 rev 만)과 음의 0 을 섞은 주도 두 경로가 같은 바이트다. 서버 검증 984 passed.
