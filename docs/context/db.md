# db.md — 저장소

> 이 문서는 **목표 상태**를 쓴다. 실제 구현 여부는 `status.md` 가 말한다.

## 엔진 셋
- **InfluxDB 2.7 OSS** — 영구 역사. org `marketlens`, bucket `marketlens` 하나. 쿼리는 Flux, Python 클라이언트는 `influxdb-client`. 이유: 김프 이력은 (거래소쌍·코인) 태그 × 시각 × 수치 2개라는 전형적 시계열이고, 시간 버킷 집계가 엔진 기본 기능이라 앱 코드가 줄어든다. 3 Core 는 기본 쿼리 범위 ~72시간이라 92일 백필·월간 조회에 부적합해 2.7 을 쓴다.
- **Redis 7** — Influx 로 아직 옮기지 못한 틱의 **버퍼**(스펙 009). 원문이 아니라 Influx 가 저장할 모양 그대로를 들고, 60초마다 전량이 옮겨진 뒤 비워진다. AOF(`appendonly yes`)라 재기동해도 안 옮긴 틱이 남는다.
- **S3** 버킷 `marketlens-spreads-snapshot`(ap-northeast-2), 접두사 `raw/` — 거래소 **원문 아카이브**(스펙 010). 거래소가 준 모든 WebSocket 프레임·REST 응답 본문을 받은 그대로 남긴다. 가공값은 없다.

## InfluxDB measurement
같은 tag set + 같은 time 은 Influx 가 덮어쓴다. 이것이 유일키 역할이라 별도 중복 방지 코드가 없고, flusher 의 재시도가 여기 기댄다.
- **premium** — 김프/역프 한 점. tag `dom`·`fx`·`base`, field `fwd`·`rev`(float, %), time = 틱 시각(초). 한 점 = (dom, fx, base, time). `/history/*` 전부의 유일한 원천. 값은 최우선 1단계 기준의 **슬리피지 차감 전 원값**이다 — 저장 시점에는 체결 규모가 정의되지 않기 때문이고, `/spreads` 의 순값과는 `fwd + slipFwd` 관계다(003 §2). 매초 전 조합(≈490)이 한 점씩 — 하루 약 4,200만 점.
- **dw_fail** — 입출금 조회 실패 관측. tag `exchange`, field `v`=1, time = 틱 시각. 한 점 = (exchange, time). 읽는 HTTP 엔드포인트 없음 — 사람이 Influx UI 에서 본다.
- **collect_fail** — 수집 실패 구간 1건(스펙 011 §3.4). tag `exchange`·`kind`, time = `started_at`(초), field `count`(int)·`last_failed_ts`(int 초)·`status_code`(int, 없으면 0)·`message`(string)·`url`(string)·`retry_after_sec`(int, 없으면 0)·`ended_ts`(int 초, 닫힐 때만). 한 점 = (exchange, kind, started_at). 열 때 쓰고 닫을 때 같은 키로 덮어써 필드를 합친다.

## Redis
- 키 하나: Stream **`ticks`**. 엔트리 = 틱 1개 — 필드 `ts`(epoch 초), `data`(틱 레코드 `{ts, rows:[{dom,fx,base,fwd,rev}], dwFailed:[…]}` 를 gzip 한 JSON, ≈3KB).
- 쓰는 쪽: 009 의 인계기 — LiveStore 틱 슬롯에서 물러난 직전 틱을 `XADD ticks MAXLEN ~ 86400`. 평상시 길이 60 안팎.
- 읽고 지우는 쪽: 009 의 flusher — 60초마다 전량을 `XRANGE` 1,000건 페이지로 잘라 한 페이지씩 Influx 에 쓰고, **그 페이지의 모든 배치가 성공한 뒤에만** 그 페이지의 ID 를 `XDEL` 한다(메모리는 페이지 크기에 비례). 페이지가 실패하면 그 페이지부터는 지우지 않고 다음 회차가 같은 구간을 다시 보낸다(Influx 덮어쓰기라 무해).
- `MAXLEN ~ 86400`(24시간)은 Influx 가 하루 넘게 막혔을 때만 작동하는 안전 상한이다. 잘리면 유실이고 flusher 가 다음 회차 로그로 알린다.
- env `REDIS_URL`(기본 `redis://localhost:6379/0`, compose 안에서는 `redis://redis:6379/0`). Redis 가 없어도 앱은 뜬다 — 인계된 틱은 버려지고(경고 로그) 원문은 S3 에 있어 재생 가능하다.

## S3 원문 아카이브
- 객체 키 `raw/exchange=<id>/dt=YYYY-MM-DD/hh=HH/YYYYMMDDTHHMM00Z.jsonl.gz`(UTC, 시각 = 분 창의 시작). 거래소·분마다 객체 1개. 시세 프레임은 그 분의 `(source, key)` 별 마지막 1건만, 비시세 응답(마켓 목록·입출금·핸드셰이크 거부 본문·구독 응답)은 전량.
- 한 줄 = `{"exchange":…,"source":"ws:<경로>|rest:<경로>","receivedAt":<epoch ms>,"raw":<페이로드 원문 그대로>}`. `raw` 는 JSON 페이로드면 바이트 그대로 이어 붙이고(재직렬화 금지), JSON 이 아니면 문자열로 감싼다. 이 4키 외의 키는 없다.
- 실패는 대기열에 두고 재시도, 압축 후 256MB 를 넘으면 오래된 객체부터 버리고 로그. 읽는 HTTP 엔드포인트 없음 — 사람이 CLI·pandas·Athena 로 본다. 재생 도구는 후속 스펙.
- lifecycle 미설정(무제한). 분당 마지막 1건 표본화로 하루 0.3~0.5GB(gzip 후) 추정 — 실측 후 보존 기간을 정한다. 분 안의 중간 변동은 남지 않으며 초 단위 값은 Influx `premium` 이 맡는다.

## 메모리 저장소 (영속 대상 아님)
`live_store`(LiveStore) 는 저장 엔진이 아니지만 여기 적어 둔다 — 어떤 데이터가 **디스크에 남지 않는지**의 경계이기 때문이다.
- 최신 시세 행(`(exchange, base)` 당 1행, `asks`/`bids` 는 업비트 최대 30·빗썸 최대 15·바이낸스 최대 20단계), USDT 시세, 거래소별 스트림 상태(`connected`·`last_message_at`·`last_error`·`subscribed`), 틱 슬롯(최신 틱 1장), spark 맵(코인별 최근 30분).
- 호가 자체는 어디에도 가공 저장하지 않는다 — 원문은 S3 에, 김프 원값은 Influx 에 있다. 재기동하면 스트림 스냅샷으로 수 초 안에 복구된다.

## 시각 단위
- Influx time 은 ns 지만 기록 정밀도는 **초**. 틱 `ts` 는 epoch 초. API 응답의 `*Ts` 는 epoch 초, `fetchedAt`·`updatedAt` 은 epoch ms. 원문 아카이브 `receivedAt` 은 epoch ms(서버 시각 — 거래소 시각은 `raw` 안에 있다).

## 보존
- Influx bucket retention 은 **무제한**. `dw_fail` 의 "최근 24시간" 은 쿼리 range(-24h) 로 처리한다 — retention 을 걸면 `premium` 까지 지워진다. 초단위 적재의 보존·롤업은 과부하 실측 후 별도 스펙.
- Redis 는 옮기면 비운다(위). S3 는 lifecycle 미설정.

## 쓰는 쪽
- 틱 루프(001, 매초): 틱 생성 → LiveStore 슬롯 → 직전 틱을 009 인계기로.
- flusher(009, 60초): Redis 전량 → `premium`(조합별)·`dw_fail`(dwFailed) → 성공 시 Redis 에서 삭제.
- 백필 스크립트(005): 업비트 초봉 × 바이낸스 1초봉 → 과거 92일 `premium`. 기존 기록의 앞·뒤 빈 구간만 채운다.
- 이력 추적기(011): 구간 열림·닫힘 시 `collect_fail` 1점, 매초 없음. 실패는 로그 후 무시.
- 원문 싱크(010, 수신 경로가 동기 호출): 줄을 `(거래소, UTC 분 창)` 버퍼에 붙인다 — 시세 프레임(`key` 있음)은 창 안에서 `(source, key)` 당 마지막 1건만, 그 외는 전량. 닫기 회차(매초) + 업로드 워커(스레드 1개): 창이 지나면 객체 1개로 닫아 gzip·업로드. 실패는 대기열 머리에 두고 1초 뒤 재시도, 압축 후 256MB 를 넘으면 오래된 객체부터 버리고 로그.
- Influx·Redis·S3 어느 것이 닿지 않아도 앱은 뜬다. `INFLUX_TOKEN` 없으면 flusher 비활성, `S3_BUCKET` 없으면 아카이브 비활성.

## 읽는 쪽
- `features/history` 의 `/history/premium`·`/history/streaks`·`/history/streaks/bulk` 만. 다른 조회 API 는 DB 를 0회 접근한다(메모리가 진실). 저장소 불가 시 503 `storage_unavailable`.
- 기동 시 1회: `collect_fail` 24시간 복원(011, 3초 상한), spark 용 `premium` 최근 30분 1분 버킷 집계(009, 10초 상한).
- Redis 는 flusher 만 읽는다. S3 를 읽는 코드는 없다.

## 로컬 접속
- dev compose 로 Influx 2.7 과 Redis 7 을 띄운다. env 는 `INFLUX_URL`(기본 `http://localhost:8086`)·`INFLUX_TOKEN`·`REDIS_URL`.
- UI: `http://localhost:8086` (토큰 = `INFLUX_TOKEN`). 점검은 UI 에서 `premium` 점 수를 세는 정도면 된다. Redis 는 `docker compose -f docker-compose.dev.yml exec redis redis-cli XLEN ticks`.
