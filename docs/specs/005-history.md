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
- 시세가 멈춰도 틱은 매초 생기므로 같은 값이 초마다 새 점으로 남는다 — 주기가 곧 DB 증가 속도(하루 약 4,200만 점, 009 §3.5).

### 3.4 `GET /history/*`
HTTP JSON 키와 복합어 쿼리 파라미터는 camelCase다. 모든 시각 `*Ts` 는 epoch 초, `fetchedAt` 은 ms.
공통 파라미터: `dom` ∈ {upbit, bithumb}(기본 upbit), `fx` = binance 고정. `maxGap`(기본 600, ≥1) 은 streaks·bulk 만 받는다 — premium 은 구간 전체를 그대로 돌려주므로 gap 개념이 없다. streaks·bulk 의 `start`·`end` 는 0 ≤ 값 ≤ 4,102,444,800(2100-01-01) — 밖이면 422(연도 오버플로 500 방지). `end ≤ 0` 은 400(end ≤ start 의 특수형).

**`/history/premium?base&unit&date`** — `base`·`unit ∈ {week, month}` 필수. `date=YYYY-MM-DD`(정확히 이 형식·연도 1970~2100, 밖이면 400. 없으면 오늘 UTC).
구간 = `date` 가 속한 ISO 주(월 00:00 UTC ~ 다음 월) 또는 달(1일 ~ 다음 달 1일), end exclusive. 구간에 기록 없으면 404. 구간 전체를 한 번에 반환한다. 응답 키:
- `dom`·`fx`·`base`·`unit` 은 요청 그대로. `start`·`end` 는 구간 경계(ISO 8601, UTC). `firstTs` 는 구간 첫 기록 시각, `count` 는 기록 수, `fetchedAt`.
- `summary` = `{firstFwd,lastFwd,minFwd,maxFwd}` — 구간 전체 통계.
- `events` = `[{dt,fwd,rev}…]` 컴팩트 — 절대시각 대신 `dt`=직전 기록으로부터 경과 초(구간 첫 기록은 0).

**`/history/streaks?base&threshold&start&end&maxGap`** — `threshold ≥ 0`(기본 0). `end` 없으면 지금+1초, **`start` 없으면 `end − 7일`(604,800초)** — 전 구간 조회를 막기 위해서다(2,700만 점 위에서 `start` 없는 조회는 Influx 를 죽인다, status.md 알려진 빚). 응답 `startTs` 는 실제로 쓴 값. 조회 구간 안에 기록이 0건이면 404(구간 밖 기록 유무는 보지 않는다), `end ≤ start` 면 400. 구간(streak) 규칙:
1. ts 오름차순으로 값이 `threshold` **이상**인 연속 기록을 한 구간으로 묶는다(같은 값 포함).
2. 값이 미만이거나 직전 기록과 `maxGap` 초보다 벌어지면 구간을 닫는다(끊긴 수집을 이어 붙여 "3시간 연속" 을 만들지 않는다).
3. fwd(kimp) 와 rev(reverse) 를 절댓값 없이 **각각** 계산한다.
4. 구간 = `{startTs,endTs,start,end(KST),durationSeconds=end−start(1개면 0),samples,maxPercent,avgPercent}`.
5. 방향 요약 = `{count,maxDurationSeconds,avgDurationSeconds,maxPercent,avgPercent(샘플 수 가중),segments}`.
6. `overall` = `{maxKimpPercent,avgKimpPercent,maxReversePercent,avgReversePercent}` 는 기준치 무관 **전체 행** 기준. `maxDurationSeconds,avgDurationSeconds,segmentCount` 는 두 방향 구간 합집합.
7. 최상위 응답 = `{base,dom,fx,thresholdPercent,maxGapSeconds,startTs,endTs,kimp,reverse,overall,scanned,lastUpdatedTs,lastUpdated,fetchedAt}`. 방향 요약 키 이름은 bulk 와 같은 `kimp`(fwd)·`reverse`(rev). `scanned` 는 전체 행 수, `lastUpdated` 는 KST.
예: 값 `0 1 3 6 29 4 31`(60초 간격), threshold 4 → 구간 1개(samples 4, max 31); threshold 5 → 2개.

**`/history/streaks/bulk?threshold&start&end&maxGap`** — 전 코인 한 번에. `start`·`end` 기본값은 streaks 와 같다(`end − 7일` / 지금+1초).
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
- `/history/streaks`·`bulk`: `start` 없으면 `startTs == endTs − 604800` 이고 그보다 오래된 기록은 `scanned` 에 안 잡힌다; `end` 만 주면 `start = end − 7일`
- `/history/streaks/bulk`: 기록 없으면 200 + 빈 `coins`
- 백필 대상 구간 계산: 기록 없음 → 전체 구간, 기록 있음 → 앞·뒤 빈 구간만(가운데는 건드리지 않음); 주/월 구간 경계가 ISO 주·달력 월과 일치, 잘못된 unit 거부
- 캔들 병합: 세 값이 갖춰지기 전 ts 는 건너뜀, fwd 불변이면 기록 없음, 종가 대칭식 결과
- `/history/streaks/bulk?threshold=0`: `coinCount == len(coins)` 이고 100 을 넘는다(전 코인)
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
  - `server/app/features/history/service.py` — 순수 계산: 주/월 구간 경계, `/premium` 의 컴팩트 events·summary, streak 구간 판정(threshold 이상·maxGap)·방향 요약(샘플 가중)·overall(전체 행 + 두 방향 합집합), bulk 코인별 집계. 리더는 `query_premium` 하나만 쓰는 Protocol 로 받는다.
  - `server/app/features/history/router.py` — 3 엔드포인트. 파라미터 검증은 FastAPI `Query`(422), 업무 오류는 `{"error":…}`(400·404), 저장소 없음·실패는 503. Influx 클라이언트는 동기라 스레드에서 돌린다.
  - `server/app/features/history/models.py` — 응답 모델(snake_case → 라우터에서 camelCase).
  - `server/app/features/history/tests/` — `helpers.py`(fake 리더 + lifespan 없는 앱, 선택적 LiveStore), `test_premium_api.py`·`test_streaks_api.py`·`test_bulk_api.py`(§3.4 계약·오류 4종·경계값), `test_point_rules.py`(§3.3 점 규칙·원값·`dw_fail`·저장소 장애 격리·bulk 100코인 초과).
  - `server/scripts/backfill.py` — §3.5 그대로. 순수 계산(`plan_day_slices`·`is_full_day`·`dedup_changes`·`merge_premiums`·`rates_for_slice`)과 거래소 호출(거래소별 재시도 정책·페이지 간격)을 나눈다. 테스트 `server/tests/test_backfill.py` 는 순수 계산만.
  - 루트 `docker-compose.dev.yml` — Influx 2.7 + Redis 7(009), 토큰은 `${INFLUX_TOKEN}` 치환.
  - `web/src/App.tsx` 가 선택 심볼(초기 `'BTC'`)과 탭 전환을 든다. 002 의 mock 사건 목록(`feed.events`)은 함께 지웠다. 기록 탭 파일들은 013 이 `/history/events` 용으로 다시 썼다.
  - `server/app/main.py` — 토큰이 있을 때만 `InfluxClient` 를 만들어 `app.state.influx` 에 두고 ping 실패는 에러 1줄. flusher(009)·이력 복원(011)·spark 복원(009)이 같은 클라이언트를 쓴다.
- 추측한 지점 (묻지 않고 정한 것 — 전부 본문에 반영):
  - `fx` 는 `Literal["binance"]` 쿼리로 노출한다 — 다른 값은 FastAPI 422(§3.4 오류 표의 "파라미터 검증 실패").
  - `base` 는 `^[A-Za-z0-9]{1,20}$` 패턴으로 검증한다(422) — Flux 문자열에 들어가므로 이스케이프와 함께 이중 방어.
  - 빈 방향 요약은 `count 0`·수치 0.0·빈 `segments`, bulk 의 `coins` 는 base 오름차순.
  - 백필의 "이미 채워진 날" 판정은 그 조각의 `count > 0`. dev compose 의 UI 비밀번호도 `${INFLUX_TOKEN}` 재사용.
  - 기록 탭의 기간 내부값은 `7d/30d`. 심볼 입력은 영숫자만 받아 대문자로 보낸다(서버 `base` 패턴과 같다).
  - 저장소 장애 검증은 lifespan 없이 앱 상태에 fake 리더(실패)·빈 리더를 꽂아 본다 — 실제 기동 경로는 §5 의 :8041 스모크 두 번이 대신한다.
- 실행 중 함께 고친 절: §2 — `/spreads` 의 `spark` 는 009 가 채운다(이 스펙의 후속 몫이 아니다), 앱 셸 접점을 지금 모양(기록 탭 = 이 기능의 Tab, 선택 심볼 초기값 `'BTC'`)으로. §6 — architecture.md 항목을 지금 문서 구조(계약 규칙에 casing 예외 목록 없음)로.
- 남은 빚:
  - §4 수동 항목 전부(첫 점·`count` 구멍·백필 1일·재실행 문구)와 `bulk` 실데이터 100코인 초과 — **EC2 에서 확인 필요**.
  - 캔들 수집기(`fetch_*`)·백필 실호출의 자동 테스트 없음(순수 계산만).
