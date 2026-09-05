# 009 — tick-store

상태: DONE | 의존: 001(collect — 틱·LiveStore 틱 슬롯·인계 계약), 003(spreads — 김프 원값 수식·`spark`), 005(history — Influx 모델·`/history/*`), 007(deploy — compose)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
매초 만들어지는 **틱 레코드**(전 조합의 김프 원값)를 세 계층에 순서대로 흘려 한 초도 빠짐없이 InfluxDB 에 남긴다.

```
매초  001 틱 루프 ─→ 틱 레코드 T
                       │  ① LiveStore 틱 슬롯에 T 를 넣고, 슬롯에 있던 T−1 을 꺼낸다
                       ▼
                 ② Redis Stream `ticks`  ←── T−1 을 여기로 인계
                       │  60초마다: 스트림의 **전량**을 읽어
                       ▼
                 ③ InfluxDB `premium`·`dw_fail`  ←── 양식 그대로 적재, 성공하면 읽은 만큼 Redis 를 비운다
```

- **① LiveStore** — "지금"을 답한다. 최신 시세와 **최신 틱 1장**을 든다. `/spreads` 와 FE 가 읽는 유일한 곳이며 폴링 경로에 Redis·Influx 호출이 없다.
- **② Redis** — "Influx 로 아직 옮기지 못한 틱"만 든다. 원문이 아니라 **Influx 가 저장할 모양 그대로**(조합별 fwd/rev)를 담는다. 옮기고 나면 비운다.
- **③ InfluxDB** — 영구 역사. `/history/*` 가 읽는다.

②→③ 에서 고르거나 솎아내지 않는다. Redis 에 들어간 틱은 전부 DB 에 들어간다.

## 2. 범위
- 만드는 것: core 의 Redis 클라이언트(연결·스트림 쓰기/읽기/삭제), **틱 인계기**(LiveStore 슬롯에서 나온 틱을 Redis 에 넣는 것), **flusher**(60초마다 Redis 전량 → Influx → Redis 비우기), **spark**(코인별 최근 30분 링버퍼 + 기동 시 Influx 복원), dev·배포 compose 의 `redis` 컨테이너, env `REDIS_URL`, 테스트.
- 자리: Influx `premium`·`dw_fail` 쓰기는 이 스펙의 flusher 가 한다(005 는 점 규칙·조회만 정한다). Influx 스키마·수식·실패 격리 규칙은 005 그대로다.
- 값이 채워지는 것: `/spreads` 행의 `spark`. 키·타입은 불변이라 FE 는 하위호환.
- 하지 않는 것: FE 스파크라인 렌더(후속). 초단위 조회 API(`/history/*` 계약 그대로). Influx 보존·롤업(과부하 실측 후 별도 스펙). `/spreads` 응답 계약 변경. 원문 저장(010 의 몫 — Redis 에 원문을 넣지 않는다).

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001: 틱 루프는 **매초** LiveStore 의 최신 시세로 틱 레코드를 만든다. LiveStore 는 **틱 슬롯** 하나를 가지며, 새 틱을 넣으면 슬롯에 있던 직전 틱을 돌려준다. 틱 루프는 그 직전 틱을 이 스펙의 인계 함수(core 공개 계약 `handoff(tick) -> None`, 동기, 예외 없음)에 넘긴다. 앱 종료 시에는 슬롯에 남은 마지막 틱도 같은 함수로 넘긴다. 틱 루프는 그 초의 입출금 조회 실패 거래소 목록을 틱에 싣는다.
- 003: 김프 **원값** 수식 — `fwdRaw = premium_percent(buy_krw=fx_ask×rate_ask, sell_krw=dom_bid)`, `revRaw = premium_percent(buy_krw=dom_ask, sell_krw=fx_bid×rate_bid)`, 최우선 1단계 기준. 조합 자격: 국내×해외가 서로 다른 거래소, 양쪽 호가 존재, 그 국내 거래소 **자신의** USDT 시세 존재, 여섯 값 중 ≤0 이면 제외. `/spreads` 의 `fwd`·`rev` 는 슬리피지 차감 후 순값이라 저장값과 다르다.
- 005: Influx `premium` = tag(dom·fx·base) × time(초) × field(fwd·rev). **같은 태그+시각은 덮어쓴다** — 재시도 안전성이 여기 기댄다. `dw_fail` = 입출금 조회 실패 거래소마다 1점(tag `exchange`, field `v`=1). 쓰기 실패 로그 형식은 `DB 저장 실패 (연속 n회)`.
- 007: 배포 compose 는 server·web·influxdb 3컨테이너, 호스트 노출은 web 하나. server 의 컨테이너 내부 주소는 `environment` 로 덮어쓴다(`INFLUX_URL` 패턴).

### 3.2 틱 레코드
세 계층을 흐르는 단위이고 모양은 하나다. Influx 점의 모양과 같아 계층을 넘을 때 변환이 없다.
```
{ ts, rows, dwFailed }
```
- `ts` — 틱 시각(epoch 초). Influx 점의 `time`.
- `rows` — §3.1 자격을 통과한 조합 배열 `[{dom, fx, base, fwd, rev}…]`(원값). 현재 약 490개.
- `dwFailed` — 그 초에 입출금 조회가 실패 상태인 거래소 이름 배열. `dw_fail` 점의 원천.
틱 레코드는 001 이 만든다. 이 스펙은 받아서 옮기기만 한다.

### 3.3 계층 ① — LiveStore 틱 슬롯과 인계
- 슬롯의 틱은 "가장 최근 값" 이다. 새 틱이 오면 **그때** 직전 틱이 Redis 로 간다 — 값이 최신 자리에서 물러나는 순간이 곧 인계 시점이다.
- 인계 함수는 틱을 §3.4 모양으로 Redis 에 `XADD` 한다. `rows`·`dwFailed` 가 **둘 다 비면** 넣지 않는다(실을 값이 없다 — 대표적 경우: 어느 국내 거래소에서도 USDT 시세를 못 받은 초).
- 인계는 틱 루프를 막지 않는다 — 실제 Redis 쓰기는 큐에 넣고 별도 태스크가 순서대로 보낸다. Redis 가 안 닿으면 그 틱은 **버리고** 경고 로그 1줄(원문은 010 에 남아 재생 가능하므로 서버 메모리에 무한히 쌓지 않는다). 큐 상한 600틱(10분, 코드 상수) — 넘치면 오래된 것부터 버린다.
- 인계 함수는 어떤 경우에도 예외를 던지지 않는다 — 인코딩·spark 갱신·큐 삽입에서 난 예외는 로그 1줄 후 그 틱을 버린다.
- 앱 종료 시: 틱 루프가 마지막 틱을 인계한 뒤, 큐에 남은 틱을 Redis 로 보내려 **한 번씩** 시도하고(불달이면 §3.3 규칙대로 버림) 보내기 태스크를 닫는다. 종료가 Redis 를 기다리는 시간은 명령당 소켓 타임아웃(§3.4)이 상한이다.

### 3.4 계층 ② — Redis
- 컨테이너 `redis`(`redis:7-alpine`, `--appendonly yes`, named volume, 호스트 비노출). 재기동해도 아직 안 옮긴 틱이 남는다.
- env `REDIS_URL`(기본 `redis://localhost:6379/0`). compose 는 `redis://redis:6379/0` 을 `environment` 로 덮는다. `server/.env.example` 에 키 추가.
- 키는 하나, **Stream `ticks`**. 틱당 엔트리 1건:
  ```
  XADD ticks MAXLEN ~ 86400 * ts <ts> data <gzip JSON>
  ```
  `data` 는 §3.2 를 gzip 한 JSON(조합 490개 ≈ 원본 30KB → 3KB) — 키는 `ts`·`rows`(원소 `dom`·`fx`·`base`·`fwd`·`rev`)·`dwFailed` 세 개뿐이다. `MAXLEN ~ 86400`(24시간)은 **안전 상한**이다 — 평상시 스트림은 60건 안팎이고, Influx 가 하루 넘게 막혔을 때만 오래된 틱부터 잘린다(메모리 보호). 잘리면 유실이며, 잘린 사실은 flusher 가 다음 회차 로그로 알린다(§3.5 잘림 감지).
- 클라이언트는 `redis`(asyncio) 라이브러리. 연결 타임아웃 2초·명령 타임아웃 5초(코드 상수), 명령 자동 재시도 없음 — 실패는 그 자리에서 §3.3·§3.5 규칙으로 처리한다. 기동 시 `PING` 1회로 연결을 확인하고 실패면 경고 로그 1줄, 이후 명령은 매번 다시 연결을 시도한다.
- 테스트는 Redis 를 띄우지 않고 `fakeredis`(dev 의존성)로 Stream 명령을 흉내낸다.

### 3.5 계층 ③ — flusher (60초)
주기 60초는 코드 상수. 기동 후 먼저 60초 잔 뒤 첫 회차. 회차마다:
1. `XRANGE ticks - +` 로 **스트림 전량**을 읽는다(1,000건씩 페이지). 비면 이번 회차 생략.
2. 읽은 모든 틱의 `rows` 를 `premium` 점으로, `dwFailed` 를 `dw_fail` 점으로 바꾼다. 각 점의 `time` 은 그 틱의 `ts`. 5,000점 배치(코드 상수)로 나눠 쓰고, **모든 배치가 성공해야 회차 성공**.
3. 회차 성공 → 읽은 엔트리 ID 를 전부 `XDEL`(1,000개씩) 한다. **Redis 를 비우는 시점은 Influx 쓰기가 끝난 뒤뿐이다.** 1단계 이후 새로 들어온 엔트리는 지우지 않는다(ID 로만 지운다).
4. 회차 실패 → 아무것도 지우지 않고 `DB 저장 실패 (연속 n회)` 로그, 다음 회차가 같은 구간부터 다시 보낸다. Influx 가 같은 (태그, 시각) 을 덮어쓰므로 중복 적재는 무해하다 — 실패는 구멍이 아니라 지연이다.
- flusher 는 LiveStore 도 틱 루프 상태도 읽지 않는다. 원천이 Redis 뿐이라 수집 락이 없다.
- 회차 안 예외는 밖으로 던지지 않는다. Redis 읽기 실패도 회차 실패다(같은 `DB 저장 실패` 로그, 연속 횟수 이어 셈).
- **잘림 감지**: 회차가 실패해 지우지 못한 구간은 다음 회차에 **같은 첫 ID** 부터 다시 읽혀야 한다. 실패 직후 회차의 첫 ID 가 실패한 회차의 첫 ID 와 다르면 오래된 쪽이 `MAXLEN` 에 잘린 것이므로 경고 로그 1줄(`Redis 스트림 잘림`). 성공한 회차는 읽은 구간을 전부 지웠으므로 비교할 기준이 없다.
- 연속 실패 뒤 성공하면 밀린 틱 수를 info 로그 1줄로 남기고 연속 횟수를 0 으로.
- `INFLUX_TOKEN` 이 없으면 flusher 를 띄우지 않는다 — Redis 에는 `MAXLEN` 까지 쌓인다(db.md).
- 적재량: 490조합 × 86,400초 ≈ 하루 **4,200만 점**. 전 구간 streaks 조회가 Influx 를 재시작시킨 실측(status.md)이 있으므로 조회는 범위를 좁혀 쓰고, 보존·롤업은 별도 스펙.

### 3.6 spark — `/spreads` 행의 김프 추이
- 정의: 행(dom, fx, base)마다 **fwd 원값의 최근 30개, 벽시계 1분 버킷(`ts // 60`)마다 그 버킷의 마지막 값**, 오래된 → 최신. 30개 미만이면 있는 만큼.
- 인계 함수가 틱을 받을 때 링버퍼를 갱신한다(Redis 쓰기 성공과 무관 — 메모리 계산이다). 완성된 맵은 LiveStore 에 게시되고 `/spreads` 가 행을 조립할 때 읽는다.
- 맵의 키는 `(dom, fx, base 대문자)`. 게시는 인계 때마다 맵 전체를 새로 만들어 통째로 바꾼다(≈490 × ≤30 값 — 매초 무시할 양). 틱에 없는 조합(자격 미달·`fail` 행)은 직전 값이 남는다.
- **재기동 복원**: 기동 시 Influx `premium` 최근 30분을 1분 버킷 `last` 로 집계해 읽어(조합당 ≤30점, 전체 ≈ 15,000점) 링버퍼를 채운다. 상한 10초, Influx 가 없거나 실패·초과면 빈 채로 시작해 회차마다 찬다(경고 로그 1줄). 복원은 틱 루프 시작 전에 끝난다.
  - 조회 구간은 기동 시각이 속한 분을 포함해 **30개 버킷** — `[ (now//60 − 29)×60, now )`. 집계는 Flux `aggregateWindow(every: 1m, fn: last)` 를 `fwd` 필드에 걸고 버킷 시각은 창의 **시작**(`timeSrc: "_start"`)이라 `ts // 60` 과 같은 버킷이다. 빈 창은 만들지 않는다.
  - Influx 클라이언트에 이 조회(`query_spark`) 를 추가한다 — `premium` 을 읽는 두 번째 경로지만 HTTP 엔드포인트는 아니다(db.md 읽는 쪽).
- `status` 가 `fail` 인 행도 spark 는 싣는다 — 추이는 추이다.

### 3.7 장애 격리
- **Redis 불달**: 기동 시 연결 실패는 경고 로그 1줄, 앱은 뜬다. 수집·`/spreads`·`spark` 정상. 인계는 §3.3 대로 버리고 로그, flusher 는 회차마다 다시 시도. 복구되면 그 뒤 틱부터 흐른다.
- **Influx 불달**: Redis 에 계속 쌓이고, 복구되면 밀린 구간이 한 회차에 들어간다(24시간 상한 안에서 무유실).
- 어느 쪽 장애도 틱 루프와 조회 경로를 세우지 않는다.
- 기동 순서(001 의 순서에 끼워 넣는다): Influx → Redis 연결 확인(`PING`) → 011 이력 복원 → **spark 복원** → 마켓 우주·스트림 → 틱 루프 → Redis 보내기 태스크 → flusher. 종료는 역순 — 틱 루프(마지막 틱 인계) → 보내기 태스크(큐 비우기) → flusher 취소 → Redis 연결 닫기.

### 3.8 compose
- dev(`docker-compose.dev.yml`)·배포(`docker-compose.yml`) 둘 다 `redis` 서비스: `redis:7-alpine`, `--appendonly yes`, named volume. 배포 compose 는 호스트 비노출 + server 에 `REDIS_URL: redis://redis:6379/0` 오버라이드·`depends_on`. dev compose 는 `6379` 를 호스트에 연다 — 서버가 compose 밖 호스트(:8000)에서 돌아 `REDIS_URL` 기본값으로 붙기 때문(Influx `8086` 과 같은 이유). 배포 가드·기존 컨테이너 무접촉 규칙(007)은 그대로.

## 4. 검증
네트워크 없음(Redis 는 fakeredis 로 흉내 — dev 의존성 추가 허용, Influx 는 fake writer).

**인계**
- 틱 슬롯에 T1 을 넣으면 인계 없음, T2 를 넣으면 T1 이 인계된다. 종료 시 T2 도 인계된다.
- 인계된 틱을 gunzip 하면 `ts`·`rows`·`dwFailed` 가 같은 시드의 원값 계산과 일치한다(같은 호가로 만든 `/spreads` 행의 `fwd + slipFwd` 와 같다).
- `rows`·`dwFailed` 둘 다 빈 틱 → 엔트리 없음. `dwFailed` 만 있으면 엔트리 있음.
- Redis 불달 → 틱은 버려지고 경고 1줄, 틱 루프·`/spreads` 정상. 큐 상한을 넘기면 오래된 틱부터 버려진다.
- 인계 함수가 예외를 던지지 않는다(fake 가 예외를 내도).

**flusher**
- 스트림이 비면 회차 생략(Influx 쓰기 0회, XDEL 0회).
- 틱 N건 → `premium` 점 수 = N건의 조합 수 합, 각 점의 `time` 이 그 틱의 `ts`. `dwFailed` 가 있는 틱 → 그 거래소·그 `ts` 로 `dw_fail` 1점.
- 회차 성공 후 스트림 길이 0. 1단계 뒤에 들어온 엔트리는 남는다.
- 회차 실패 → 스트림 그대로, 다음 회차가 같은 구간을 다시 보내고 그때 비운다. 실패 로그에 연속 횟수.
- 한 배치가 실패하면 회차 실패(아무것도 지우지 않는다). 배치 상수보다 큰 구간도 전부 적재된다.
- 같은 구간을 두 번 적재해도 점 수·값이 불변(멱등).
- 잘림 감지: 회차 실패 뒤 다음 회차의 첫 ID 가 실패한 회차의 첫 ID 와 다르면 경고 로그(`Redis 스트림 잘림`). 같으면 경고 없음.

**spark**
- 서로 다른 분 버킷의 틱 2건 → 길이 2, 오래된 → 최신. 같은 버킷 여러 건 → 마지막 값 1개.
- 31개 버킷이 지나도 길이 30. `fail` 행도 유지. Redis 불달이어도 spark 는 찬다.
- 기동 시 fake Influx 의 30분치 집계로 복원된다. Influx 없음·예외·초과면 빈 배열로 기동한다.

**응답·회귀**
- `/spreads` 행 17키·최상위 키 불변(`spark` 만 값이 찬다). 기존 테스트 전부 통과.
- `/history/*` 가 flusher 가 쓴 점으로 동작한다(005 의 조회 테스트가 이 경로로 통과).

**수동**: dev compose(redis+influx) 기동 → 2~3분 뒤 `/spreads` 행에 spark 1~3개. `docker compose exec redis redis-cli XLEN ticks` 가 초당 1씩 늘다가 매분 0 근처로 떨어진다. `/history/premium?base=BTC` 의 `count` 가 회차마다 약 60씩 증가. Influx 컨테이너를 1분 내렸다 올리면 XLEN 이 120 까지 갔다가 비워지고 `count` 에 구멍이 없다. Redis 컨테이너를 내려도 `/spreads` 는 계속 갱신되고 올리면 적재가 재개된다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q
# All checks passed!
# 178 files left unchanged
# 366 passed, 1 warning in 2.61s   (009 추가분 27개: test_tick_store 19 · test_spark 6 · test_tick_store_history 2, spreads +1)

# 스모크 — Redis·Influx 없이(둘 다 연결 거부) 기동, 빈 포트 8041
cd server && .venv/bin/uvicorn app.main:app --port 8041 --log-level warning &
curl -s localhost:8041/health      # {"status":"ok","version":"0.1.0"}
curl -s localhost:8041/spreads     # 404 market_data_not_found — 이 망은 거래소 도메인을 막는다(dev-setup.md 로컬 메모)
# 로그: "Redis 연결 실패: redis://localhost:6379/0 — 인계된 틱은 버려진다 (명령마다 재시도)" 1줄,
#       "spark 복원 실패 — 빈 채로 시작: InfluxUnavailableError(...)" 1줄. 예외·트레이스백 없음. 종료 후 포트 비움 확인.
```
§4 **수동** 항목(dev compose redis+influx → spark 1~3개, `XLEN ticks` 증감, `/history/premium` count 회차마다 +60, Influx 1분 정지 후 XLEN 120 → 0·구멍 없음, Redis 정지 중 `/spreads` 갱신·재개)은 이 망에서 거래소 스트림이 막혀 틱 `rows` 가 비므로 **EC2 에서 확인 필요**. `query_spark` 의 Flux(`aggregateWindow(every: 1m, fn: last, timeSrc: "_start")`)도 실 Influx 로 확인 필요.

## 6. 갱신할 문서
- `docs/context/architecture.md` — 저장 3계층(LiveStore 틱 슬롯·Redis·Influx)과 인계 규칙, 데이터 흐름(BE) 그림의 Redis 층·flusher, "현재 구조" 에 009 항목·history 항목에서 persist 루프 제거. **이 스펙의 핵심.**
- `docs/context/db.md` — "Redis" 절(키 `ticks`·엔트리 모양·MAXLEN 24h 안전 상한·AOF·`REDIS_URL`·읽는 쪽은 flusher, 비우는 시점). Influx "쓰는 쪽" 을 "flusher 가 60초마다 Redis 전량 적재 후 비움(초단위, 멱등)" 으로.
- `docs/context/dev-setup.md` — env 표에 `REDIS_URL`, dev compose 문구(redis+influx), 스모크에 `XLEN ticks`·spark 확인.
- `docs/context/status.md` — spreads 행에 `spark` 채워짐, history 행에 persist → flusher. **항상 포함.**
- `docs/specs/005-history.md` — §2·§3.3 의 persist 루프 문장을 "쓰기는 009 flusher" 로.
- `CLAUDE.md` — §2 core 설명에 Redis 클라이언트, 스펙 인덱스 009 행 → DONE. **항상 포함.**
- `server/.env.example` — `REDIS_URL` 행.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `server/app/core/redis_stream.py`(Redis Stream 클라이언트)·`core/spark.py`(링버퍼·복원)·`core/tick_store.py`(인코딩·`TickRelay`·`Flusher`). 수정: `core/influx.py`(`SparkBucketRow`·`query_spark`)·`core/config.py`(`redis_url`)·`main.py`(배선·종료 순서)·`features/spreads/service.py`(`spark` 를 저장소에서 읽음)·`pyproject.toml`(`redis`, dev `fakeredis`)·`docker-compose.dev.yml`·`docker-compose.yml`(redis 서비스). 테스트: `tests/conftest.py`(`FakeInflux`)·`tests/test_tick_store.py`·`tests/test_spark.py`·`tests/test_tick_store_history.py`·`features/spreads/tests/test_spreads_api.py`(+1). 문서: 이 스펙·`CLAUDE.md`·`status.md`·`architecture.md`. `server/.env.example`·`db.md`·`dev-setup.md`·스펙 005 §2·§3.3 은 이미 009 의 동작을 적고 있어 손대지 않았다.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - §3.3 인계 함수는 인코딩·spark·큐 삽입 예외도 삼킨다(로그 1줄). 종료 시 큐에 남은 틱을 한 번씩 보내 본 뒤 닫는다.
  - §3.4 Redis 클라이언트 타임아웃(연결 2초·명령 5초)·자동 재시도 없음·기동 `PING` 1회. 엔트리 JSON 키는 `ts`·`rows`·`dwFailed` 세 개.
  - §3.5 잘림 감지 규칙을 구체화 — 실패 회차의 첫 ID 를 기억해 다음 회차의 첫 ID 와 비교(빈 스트림도 "다름"). 빈 회차는 읽기 성공이라 연속 실패를 0 으로. Redis 읽기·XDEL 실패도 회차 실패로 같은 로그. 연속 실패 뒤 성공은 info 1줄. `INFLUX_TOKEN` 없으면 flusher 미기동.
  - §3.6 맵 키 `(dom, fx, base 대문자)`, 인계마다 맵 전체 게시. 복원 구간 = 기동 분 포함 30버킷, Flux `aggregateWindow(1m, last, timeSrc: "_start")`, 클라이언트 메서드 `query_spark`.
  - §3.7 기동·종료 순서(Redis PING → 이력 복원 → spark 복원 → … → 틱 루프 → 보내기 태스크 → flusher, 종료는 역순).
  - §3.8 dev compose 는 `6379` 를 호스트에 연다(서버가 호스트에서 돌기 때문). 호스트 비노출은 배포 compose 만.
  - §4 잘림 감지 문구를 §3.5 규칙에 맞춤.
  - 구조: 모듈 3개(`redis_stream`·`spark`·`tick_store`) 로 나눔. `RedisTickStream.length()` 는 테스트·스모크용 보조. `FakeInflux` 는 `tests/conftest.py`(공용 fake 자리). 012 의 기동 테스트는 기본 `REDIS_URL`(localhost:6379, 거부)을 그대로 쓴다 — 틱 `rows` 가 비어 XADD 가 없으므로 실 Redis 가 떠 있어도 쓰지 않는다.
- 남은 빚:
  - §4 수동 항목과 `query_spark` Flux 는 EC2 에서 확인 필요(§5).
  - FE 스파크라인 렌더는 후속(§2). `/spreads` 는 값만 싣는다.
  - Redis 불달 동안 초당 경고 1줄(§3.3 그대로) — 시끄러우면 스펙에서 묶음 로그로 바꿀 것.
  - spark 복원이 10초를 넘기면 조회 스레드는 뒤에서 끝난다(취소 불가, 011 복원과 같은 패턴).
  - `server/marketlens_server.egg-info/` 가 editable 재설치로 다시 바뀌었다 — 커밋에서 되돌렸다(001 의 알려진 빚 그대로).
