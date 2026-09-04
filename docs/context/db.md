# db.md — 저장소

> 이 문서는 **목표 상태**를 쓴다. 실제 구현 여부는 `status.md` 가 말한다.

## 엔진
- InfluxDB **2.7 OSS**. org `marketlens`, bucket `marketlens` 하나. 쿼리는 Flux, Python 클라이언트는 `influxdb-client`.
- 이유: 김프 이력은 (거래소쌍·코인) 태그 × 시각 × 수치 2개라는 전형적 시계열이고, 시간 버킷 집계가 엔진 기본 기능이라 앱 코드가 줄어든다. 3 Core 는 기본 쿼리 범위 ~72시간이라 92일 백필·월간 조회에 부적합해 2.7 을 쓴다.
- 두 번째 저장소: **S3** 버킷 `marketlens-spreads-snapshot`(ap-northeast-2) — `/spreads` 행 스냅샷 원본. Influx `premium` 은 fwd/rev 수치 2개만 남기지만 S3 는 표 전체(유동성·stale·입출금 상태 포함)를 남긴다.

## measurement
같은 tag set + 같은 time 은 Influx 가 덮어쓴다. 이것이 유일키 역할이라 별도 중복 방지 코드가 없다.
- **premium** — 김프/역프 한 점. tag `dom`·`fx`·`base`, field `fwd`·`rev`(float, %), time = 수집 시각. 한 점 = (dom, fx, base, time). `/history/*` 전부의 유일한 원천. 값은 최우선 1단계 기준의 **슬리피지 차감 전 원값**이다 — 저장 시점에는 체결 규모가 정의되지 않기 때문이고, `/spreads` 의 순값과는 `fwd + slipFwd` 관계다(003 §2).
- **dw_fail** — 입출금 조회 실패 관측. tag `exchange`, field `v`=1, time = 관측 시각. 한 점 = (exchange, time). 읽는 HTTP 엔드포인트 없음 — 사람이 Influx UI 에서 본다.
- **collect_fail** — 수집 실패 구간 1건(스펙 011 §3.4). tag `exchange`·`kind`, time = `started_at`(초), field `count`(int)·`last_failed_ts`(int 초)·`status_code`(int, 없으면 0)·`message`(string)·`url`(string)·`retry_after_sec`(int, 없으면 0)·`ended_ts`(int 초, 닫힐 때만). 한 점 = (exchange, kind, started_at). 열 때 쓰고 닫을 때 같은 키로 덮어써 필드를 합친다.

## 메모리 저장소 (영속 대상 아님)
`live_store` 는 저장 엔진이 아니지만 여기 적어 둔다 — 어떤 데이터가 **디스크에 남지 않는지**의 경계이기 때문이다.
- 행의 깊이 3필드(`depth_asks`·`depth_bids`·`depth_at`, 스펙 012)는 바이낸스 WS 스트림이 채우는 최대 20단계다. Influx 에도 S3 에도 쓰지 않는다 — `premium` 은 fwd/rev 2개뿐이고 `/spreads` 스냅샷의 행 키에도 없다. 재기동하면 사라지고 다음 스트림 메시지로 복구된다.

## 시각 단위
- Influx time 은 ns 지만 기록 정밀도는 **초**. API 응답의 `*Ts` 는 epoch 초, `fetchedAt` 은 epoch ms. 깊이의 `depth_at` 도 epoch ms.

## 보존
- bucket retention 은 **무제한**. `dw_fail` 의 "최근 24시간" 은 쿼리 range(-24h) 로 처리한다 — retention 을 걸면 `premium` 까지 지워진다.
- S3 는 lifecycle 미설정(무제한). 정하면 버킷 설정으로 — 코드는 관여하지 않는다.

## 쓰는 쪽
- persist 루프(60초, 앱 진입점 소관): 메모리 스프레드에서 (dom, fx, base) 별 fwd/rev 를 `premium` 에. 입출금 실패 관측 시 `dw_fail` 1점. 한 회차 = 쓰기 1번. 실패는 로그 후 다음 회차.
- 백필 스크립트: 업비트 초봉 × 바이낸스 1초봉 → 과거 92일 `premium`. 기존 기록의 앞·뒤 빈 구간만 채운다.
- snapshot 루프(60초, 앱 진입점 소관): `/spreads` 표 전체를 S3 객체 1개(`spreads/dt=YYYY-MM-DD/hh=HH/YYYYMMDDTHHMMSSZ.jsonl.gz`, UTC)로. 직전 객체와 `dataReceivedAt` 이 같으면 생략, 실패는 로그 후 다음 회차, `S3_BUCKET` 없으면 비활성.
- 이력 추적기(011): 구간 열림·닫힘 시 `collect_fail` 1점, 매초 없음. 실패는 로그 후 무시.
- Influx 가 닿지 않아도 앱은 뜬다. `INFLUX_TOKEN` 없으면 저장 루프 비활성.

## 읽는 쪽
- `features/history` 의 `/history/premium`·`/history/streaks`·`/history/streaks/bulk` 만. 다른 조회 API 는 DB 를 0회 접근한다(메모리가 진실). 저장소 불가 시 503 `storage_unavailable`.
- S3 를 읽는 HTTP 엔드포인트 없음 — 사람이 CLI·pandas 로 본다.
- `collect_fail` 은 기동 시 24시간 복원 1회만 읽는다(3초 상한) — HTTP 조회 없음, `/health/collect` 는 메모리만 읽는다.

## 로컬 접속
- dev compose 로 Influx 2.7 하나 띄운다. env 는 `INFLUX_URL`(기본 `http://localhost:8086`)·`INFLUX_TOKEN` 둘.
- UI: `http://localhost:8086` (토큰 = `INFLUX_TOKEN`). 점검은 UI 에서 `premium` 점 수를 세는 정도면 된다.
