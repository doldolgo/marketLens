# db.md — 저장소

> 이 문서는 **목표 상태**를 쓴다. 실제 구현 여부는 `status.md` 가 말한다.

## 엔진 셋
- **InfluxDB 2.7 OSS** — 영구 역사. org `marketlens`. 버킷 `marketlens`(초 단위 원값·사건·실패 구간, 무제한) + `candles_1m…1d`(봉 계층 — retention 이 곧 유통기한 7일/30일/90일/365일/무제한, 기동 시 없으면 만들고 있으면 안 건드린다, 스펙 014). 쿼리는 Flux, Python 클라이언트는 `influxdb-client`. 이유: 김프 이력은 (거래소쌍·코인) 태그 × 시각 × 수치 2개라는 전형적 시계열이고, 시간 버킷 집계가 엔진 기본 기능이라 앱 코드가 줄어든다. 3 Core 는 기본 쿼리 범위 ~72시간이라 92일 백필·월간 조회에 부적합해 2.7 을 쓴다.
- **Redis 7** — Influx 로 아직 옮기지 못한 틱의 **버퍼**(스펙 009) + 스프레드 표의 **채널·키**(스펙 017 — 게시 즉시 소비, TTL 10/15초) + 열린 사건의 **복원용 사본**(스펙 013). 원문이 아니라 Influx 가 저장할 모양 그대로를 들고, 60초마다 전량이 옮겨진 뒤 비워진다. AOF(`appendonly yes`)라 재기동해도 안 옮긴 틱이 남는다. 배포는 `maxmemory 600mb`·`noeviction` — 넘치면 키를 지우지 않고 쓰기만 거부한다(021 §3.1).
- **S3** 버킷 `marketlens-spreads-snapshot`(ap-northeast-2), 접두사 `raw/` — 거래소 **원문 아카이브**(스펙 010). 거래소가 준 WebSocket 프레임·REST 응답 본문을 받은 그대로 남긴다 — 시세 프레임은 심볼·종류별, 매초 오는 마켓·심볼 목록 응답은 거래소별 분당 마지막 1건, 나머지(입출금·핸드셰이크 거부 본문·구독 응답)는 전량. 가공값은 없다.

## InfluxDB measurement
같은 tag set + 같은 time 은 Influx 가 덮어쓴다. 이것이 유일키 역할이라 별도 중복 방지 코드가 없고, flusher 의 재시도가 여기 기댄다.
- **premium** — 김프/역프 한 점. tag `dom`·`fx`·`base`, field `fwd`·`rev`(float, %), time = 틱 시각(초). 한 점 = (dom, fx, base, time). `/history/*` 전부의 유일한 원천. 값은 최우선 1단계 기준의 **슬리피지 차감 전 원값**이다 — 저장 시점에는 체결 규모가 정의되지 않기 때문이고, `/spreads` 의 순값과는 `fwd + slipFwd` 관계다(003 §2). 매초 전 조합(≈1,458, 2026-09-28)이 한 점씩 — 하루 약 1.26억 점.
- **dw_fail** — 입출금 조회 실패 관측. tag `exchange`, field `v`=1, time = 틱 시각. 한 점 = (exchange, time). 읽는 HTTP 엔드포인트 없음 — 사람이 Influx UI 에서 본다.
- **collect_fail** — 수집 실패 구간 1건(스펙 011 §3.4). tag `exchange`(현물 `upbit`·`bithumb`·`binance`·`bybit`·`bitget` + perp 원천 `binance_perp`·`bybit_perp`·`bitget_perp`·`hyperliquid_perp`, 046·047)·`kind`, time = `started_at`(초), field `count`(int)·`last_failed_ts`(int 초)·`status_code`(int, 없으면 0)·`message`(string)·`url`(string)·`retry_after_sec`(int, 없으면 0)·`ended_ts`(int 초, 닫힐 때만). 한 점 = (exchange, kind, started_at). 열 때 쓰고 닫을 때 같은 키로 덮어써 필드를 합친다.
- **premium_event** — 김프/역프 사건 1건(스펙 013 §3.3). tag `dom`·`fx`·`base`·`dir`(kimp|reverse), time = `start_ts`(초), field `end_ts`(int 초, **진행 중이면 0**)·`duration_seconds`(int, 진행 중 0)·`max_percent`(float)·`max_ts`(int 초)·`last_ts`(int 초)·`samples`(int)·`enter_percent`(float 1.0)·`exit_percent`(float 0.5)·`net_dom`·`net_fx`(string, `-` = 없음 — 옛 점의 빈 문자열도 없음으로 읽는다, 배포 전 점엔 없음 — 024). 한 점 = (dom, fx, base, dir, start_ts). 기준값을 점에 같이 남기는 것은 나중에 기준이 바뀌어도 과거 사건의 의미가 남게 하기 위해서다. 매초 쓰지 않는다 — 열린 지 60초를 넘긴 순간, 열린 채 60초마다, 닫힐 때 같은 키로 덮어쓴다.
- **chain_flow** — 업비트 이더리움 ERC-20 입출금 전송 1건(스펙 050 §3.5). tag `exchange`(upbit)·`network`(eth)·`dir`(in|out)·`symbol`, time = **블록 시각 초 × 10⁹ + log index(나노초)** — 같은 블록·코인·방향에 전송 여러 건이라 초로는 겹친다, field `amount`(float)·`counterparty`(string)·`addr`(string — 입금이면 입금주소, 출금이면 핫월렛)·`tx_hash`(string)·`block`(int)·`log_index`(int)·`removed`(bool — 리오그로 되돌린 점, 조회는 뺀다). 유일키 = (tag 넷, time). 기본 버킷, 하루 약 6,000 점.

- **candle** — 봉 1개(스펙 014 §3.4). 다섯 계층 버킷 `candles_1m`·`candles_5m`·`candles_1h`·`candles_4h`·`candles_1d` 모두 같은 모양. tag `dom`·`fx`·`base`, time = 창 시작(초, **KST 벽시계 정렬** `(ts + 32400) // W * W − 32400` — 4h·1d 가 UTC 정렬과 다르다). field `fwd_o fwd_h fwd_l fwd_c rev_o rev_h rev_l rev_c`(float %, 원값)·`krw`(float, 국내 종가 원)·`usdt`(float, 해외 종가)·`rate`(float, USDT 중간값 원)·`dom_dep dom_wd fx_dep fx_wd`(int: 1 가능·0 불가·−1 모름)·`blocked_fwd_sec blocked_rev_sec`(int, 창 길이 이하)·`samples`(int, 창에 든 틱 수)·`net_dom`·`net_fx`(string, `-` = 없음 — 옛 점의 빈 문자열도 없음으로 읽는다, 배포 전 점엔 없음 — 024). `dom_dep dom_wd fx_dep fx_wd` 는 006 §3.7 판정값이다. 유일키 = (버킷, dom, fx, base, 창 시작). 1m 하루 ≈ 70만 점.

## Redis
- Stream **`ticks`**(009). 엔트리 = 틱 1개 — 필드 `ts`(epoch 초), `data`(틱 레코드 `{ts, rows:[{dom,fx,base,fwd,rev}], dwFailed:[…]}` 를 gzip 레벨 6·머리 시각 0 으로 압축한 JSON, 1,458조합 ≈36KB — Redis 안에서 엔트리당 ≈41KB).
- 쓰는 쪽: 009 의 인계기 — LiveStore 틱 슬롯에서 물러난 직전 틱을 `XADD ticks MAXLEN ~ 10800`. 평상시 길이 60 안팎.
- 읽고 지우는 쪽: 009 의 flusher — 60초마다 전량을 `XRANGE` 1,000건 페이지로 잘라 한 페이지씩 Influx 에 쓰고, **그 페이지의 모든 배치가 성공한 뒤에만** 그 페이지의 ID 를 `XDEL` 한다. 틱을 하나씩 풀어 점 객체 없이 줄을 바로 만들고 5,000줄마다 쓰므로 메모리는 배치 크기에 비례한다. 페이지가 실패하면 그 페이지부터는 지우지 않고 다음 회차가 같은 구간을 다시 보낸다(Influx 덮어쓰기라 무해).
- `MAXLEN ~ 10800`(3시간, 상한에서 ≈0.44GB — Redis 상한 600MB 안)은 Influx 가 3시간 넘게 막혔을 때만 작동하는 안전 상한이다. 잘리면 유실이고 flusher 가 다음 회차 로그로 알린다.
- 채널 **`spreads`**(017) — `GET /spreads` 와 같은 표 JSON(camelCase, ≈790KB — 2026-09-28 1,458행) 1장. 쓰는 쪽 수집(틱 직후 매초), 읽는 쪽 api 의 구독 허브(접속자가 있을 때만 구독하고 0명 30초면 닫는다 — 2026-09-28).
- 키 **`spreads:latest`**(017) — 같은 JSON, TTL 10초. 쓰는 쪽 수집(게시와 한 왕복), 읽는 쪽 api(첫 접속자의 시작 표·`GET /spreads` 응답(018)·`GET /landing` 요약(022)). 만료 = 수집이 표를 안 만들거나 멈춤.
- 키 **`spreads:want`**(017) — 값 `1`, TTL 15초. 쓰는 쪽 api(접속자가 있는 동안 5초마다·첫 접속 즉시·`GET /spreads` 요청마다(018)), 읽는 쪽 없음(2026-09-26 부터 수집은 접속자와 무관하게 매 틱 표를 만든다).
- 채널 **`gap`**·키 **`gap:latest`**(048) — 현선갭 표 JSON(11키 행, 로컬 실측 2,923행 ≈650KB) 1장, 같은 규칙(수집이 틱마다 spreads 다음에 게시, TTL 10초, 읽는 쪽 api 의 gap 허브 — 접속자가 있을 때만 구독). want 키는 없다.
- 해시 **`dayopen:<YYYY-MM-DD>`**(026) — field `<국내 거래소>:<코인>`, value KST 00시 이후 첫 체결가(문자열), TTL 48시간. 쓰는 쪽 수집(새로 못 박힌 항목을 틱 뒤 한 번에), 읽는 쪽 수집 자신(기동·자정 직후 HGETALL 로 복원). 사라지면 그날 기준 = 첫 관측 시각 가격.
- 키 **`premium_events:open`**(013) — 열린 사건 전체의 JSON 배열(사건마다 `dom fx base dir start_ts max_percent max_ts last_ts samples net_dom net_fx written`), TTL 600초(다음 저장이 덮는다 — 결측 허용과 같아, 수집이 10분 넘게 멈췄거나 옛 코드로 되돌렸다 온 기동은 사본 대신 Influx 로 복원한다). 쓰는 쪽 수집의 사건 쓰기 태스크(60초 갱신 회차·닫힘 점을 쓴 회차·종료 때), 읽는 쪽 수집 자신(기동 복원 — 없거나 실패하면 Influx). 닫힌 사건의 진실은 Influx 이고 이것은 복원용 사본이다. ≈255건이면 ≈55KB.
- 리스트 **`alerts:log`**(034) — 보낸 Slack 알림 JSON 줄 `{at, role, key, text, delivered}`(`at` = `notify()` 가 불린 ms, `text` 는 `[role] ` 머리를 뺀 문구, `delivered` = Slack 2xx) 최신 1,000건, 만료 없음. 쓰는 쪽 두 역할의 알림기(보낸 뒤 `LPUSH`+`LTRIM 0 999` 한 왕복, 2초 제한 — 억제·큐 초과로 안 보낸 알림과 웹훅이 없는 프로세스는 기록이 없다), 읽는 쪽 수집기 `/admin/alerts`(최신 200줄). ARN·12자리 숫자는 `[가림]` 으로 가려 넣는다.
- 키 **`admin:clarity`**(035) — 문자열 JSON `{attemptAt, state, code, successAt, values}`(시각은 epoch ms — 마지막 Clarity Data Export 시도의 시각·결과(`state`·`code`)와 마지막 성공의 시각·값(`numOfDays`·`traffic`·`summary`·`countries`·`metrics`, 주소는 쿼리·해시를 뗀 뒤 — 대시보드 주소는 `?tab=<id>` 만, 040)), 만료 없음. 쓰는 쪽·읽는 쪽 모두 api 의 `/admin/clarity`(요청마다 읽고, 4시간이 지났을 때만 불러 쓴다 — 재시작이 하루 10회 한도를 쓰지 않게). 값은 마지막 성공에서 7일이 지나면 버린다(시도 시각·결과는 남긴다 — 버림은 요청 때). 지우면 다음 요청이 바로 부른다 — 프로세스마다 24시간에 한 번, 그 밖에는 api 가 메모리 기록으로 다시 쓴다(040, 런북 `clarity.md`). 토큰은 들어 있지 않다.
- 키 **`admin:clarity:pages`**(040) — 같은 모양의 문자열 JSON, 값은 `numOfDays`(3)·`rowsIn`·`rowLimitHit`·`groups`(페이지 종류×기기 칸 40개 이하 — 주소 없음), 만료 없음. 쓰는 쪽·읽는 쪽 모두 api 의 `/admin/clarity`(마지막 시도에서 12시간 — 기본의 마지막 시도가 401·403·429 면 미룬다). 7일 버림·지우기는 `admin:clarity` 와 같다.
- 키 **`collect:heartbeat`**(025) — 값 = 마지막 틱 시각(epoch ms 문자열), TTL 30초. 쓰는 쪽 수집(매 틱 끝, 직전 쓰기가 안 끝났으면 건너뜀), 읽는 쪽 api 의 `/health`. 만료 = 수집이 30초 넘게 틱을 못 만듦(또는 죽음).
- 키 **`flow:eth:last_block`**(050) — 마지막으로 처리한 이더리움 블록 번호 문자열, 만료 없음. 쓰는 쪽 수집의 전송 감지기(블록마다), 읽는 쪽 같은 감지기의 (재)연결 직후 공백 재생(7,200블록까지)
- 집합 **`flow:eth:deposit_addrs`**·**`flow:eth:hot_wallets`**·**`flow:eth:internal`**(050) — 씨앗 CSV 에 더한 주소(소문자), 만료 없음. 쓰는 쪽 감지기(가스 지갑 수신자·sweep 목적지·내부 이동 목적지를 발견 즉시), 읽는 쪽 감지기 기동 시 씨앗과 합침
- 해시 **`attn:d:<YYYYMMDD>`**(052) — 화면 영역 이용 통계의 KST 하루 합계. 필드 `<page>|<device>|<area>|<m>`(`m` = `ms`·`clicks`·`seen`)와 페이지뷰 `<page>|<device>||pv`, 값은 정수 합(HINCRBY). 만료는 `EXPIREAT` 그 KST 날의 끝(다음 날 00:00 KST) + 90일 — 쓸 때마다 같은 값이라 밀리지 않고, 관리자 피드의 90일 창(오늘 포함)은 모두 살아 있다. 쓰는 쪽 api(받은 비콘을 메모리에 더해 두고 10초마다 날마다 한 파이프라인 HINCRBY 묶음 + EXPIREAT, 실패하면 메모리에 두고 다음 회차 — 쌓인 필드가 2만을 넘으면 버리고 WARNING, 끌 때 한 번 더), 읽는 쪽 api 의 `GET /admin/attention`(창 안 날마다 HGETALL 한 파이프라인, days 마다 60초 캐시). 하루 ≈900 필드(화면 10 × 기기 2 × 영역 ≈15 × 3 + pv) — 90일 ≈8만 필드·수 MB(noeviction). IP 는 Redis 에 없다(api 메모리의 10분 셈 표뿐).
- env `REDIS_URL`(기본 `redis://localhost:6379/0`, compose 안에서는 `redis://redis:6379/0` — server·api 둘 다). Redis 가 없어도 앱은 뜬다 — 인계된 틱은 버려지고(경고 로그) 원문은 S3 에 있어 재생 가능하다.

## S3 원문 아카이브
- 객체 키 `raw/exchange=<id>/dt=YYYY-MM-DD/hh=HH/YYYYMMDDTHHMM00Z.jsonl.gz`(UTC, 시각 = 분 창의 시작). 거래소·분마다 객체 1개. 시세 프레임·마켓 목록 응답은 그 분의 `(source, key)` 별 마지막 1건만(목록의 key 는 `markets:all`·`symbols:all`), 나머지(입출금·핸드셰이크 거부 본문·구독 응답)는 전량.
- 한 줄 = `{"exchange":…,"source":"ws:<경로>|rest:<경로>","receivedAt":<epoch ms>,"raw":<페이로드 원문 그대로>}`. `raw` 는 JSON 페이로드면 바이트 그대로 이어 붙이고(재직렬화 금지), JSON 이 아니면 문자열로 감싼다. 이 4키 외의 키는 없다.
- 실패는 대기열에 두고 재시도, 압축 후 256MB 를 넘으면 오래된 객체부터 버리고 로그. 읽는 HTTP 엔드포인트 없음 — 사람이 CLI·pandas·Athena 로 본다. 재생 도구는 후속 스펙.
- lifecycle 미설정(무제한). 분당 마지막 1건 표본화로 하루 0.3~0.5GB(gzip 후) 추정 — 실측 후 보존 기간을 정한다. 분 안의 중간 변동은 남지 않으며 초 단위 값은 Influx `premium` 이 맡는다.

## 메모리 저장소 (영속 대상 아님)
`live_store`(LiveStore) 는 저장 엔진이 아니지만 여기 적어 둔다 — 어떤 데이터가 **디스크에 남지 않는지**의 경계이기 때문이다.
- 최신 시세 행(`(exchange, base)` 당 1행, `asks`/`bids` 는 업비트 최대 30·빗썸 최대 15·바이낸스 최대 20단계), USDT 시세, 거래소별 스트림 상태(`connected`·`last_message_at`·`last_error`·`subscribed`), 틱 슬롯(최신 틱 1장), spark 맵(조합별 최근 30분)과 같은 게시의 JSON 조각 맵(017 게시기가 표 JSON 에 끼운다).
- 호가 자체는 어디에도 가공 저장하지 않는다 — 원문은 S3 에, 김프 원값은 Influx 에 있다. 재기동하면 스트림 스냅샷으로 수 초 안에 복구된다.

## 시각 단위
- Influx time 은 ns 지만 기록 정밀도는 **초** — `chain_flow` 만 나노초(050 §3.5). 틱 `ts` 는 epoch 초. API 응답의 `*Ts` 는 epoch 초, `fetchedAt`·`updatedAt` 은 epoch ms. 원문 아카이브 `receivedAt` 은 epoch ms(서버 시각 — 거래소 시각은 `raw` 안에 있다).

## 보존
- Influx bucket retention 은 **무제한**. `dw_fail` 의 "최근 24시간" 은 쿼리 range(-24h) 로 처리한다 — retention 을 걸면 `premium` 까지 지워진다. 봉 계층은 버킷 retention 이 유통기한이다(1m 7일·5m 30일·1h 90일·4h 365일·1d 무제한). 초 단위 `premium` 은 아직 무제한.
- Redis 는 옮기면 비운다(위). S3 는 lifecycle 미설정.

## 쓰는 쪽
- 틱 루프(001, 매초): 틱 생성 → LiveStore 슬롯 → 직전 틱을 009 인계기로.
- 전송 감지기(050, 블록마다 약 12초): 이더리움 WS 로그 → 판정 → `chain_flow` 줄을 블록 단위로 1회(ns). 실패는 미전송 목록(상한 10,000)에 두고 다음 블록 회차에 같이.
- flusher(009, 60초): Redis 전량 → `premium`(조합별)·`dw_fail`(dwFailed) 줄을 5,000줄씩 → 성공 시 Redis 에서 삭제.
- 백필 스크립트(005): 업비트 초봉 × 바이낸스 1초봉 → 과거 92일 `premium`. 기존 기록의 앞·뒤 빈 구간만 채운다.
- 이력 추적기(011): 구간 열림·닫힘 시 `collect_fail` 1점, 매초 없음. 실패는 로그 후 무시.
- 사건 감지기(013): 열린 지 60초·60초마다·닫힐 때 `premium_event` 1점(같은 키 덮어쓰기). 실패는 미전송 맵(상한 1,000)에 두고 다음 60초 회차에 재시도. 복원이 닫은 점은 상한 없이 첫 회차에 전부. 60초 갱신 회차와 닫힘 점을 다 쓴 회차(버린 스파이크는 다음 회차)마다 Redis `premium_events:open` 사본 — 닫힘 쓰기가 먼저다, 종료 때 사본 저장·미전송 쓰기 1회(3초 상한).
- 1분 집계기·롤업(014): 분이 닫힐 때 조합 전부(≈1,458)의 줄을 미전송 맵(상한 30,000줄 ≈20분, 넘치면 오래된 분부터)에 두고 쓰기 태스크가 `candles_1m` 에 5,000줄씩 → 같은 회차에 5m→1h→4h→1d 순으로 아래 계층을 읽어 위 버킷에(계층당 최대 12창, 접는 창의 끝 ≤ 아래 계층 완료 지점). 실패 뒤 60초 안엔 재시도 없음. 종료 시 미전송분은 쓰지 않는다.
- 원문 싱크(010, 수신 경로가 동기 호출): 받은 원문을 `(거래소, UTC 분 창)` 버퍼에 참조로 붙인다 — 시세 프레임·매초 마켓 목록 응답(`key` 있음)은 창 안에서 `(source, key)` 당 마지막 1건만, 그 외는 전량. 닫기 회차(매초) + 업로드 워커(스레드 1개): 창이 지나면 살아남은 원문만 줄로 조립해 객체 1개로 닫아 gzip·업로드(객체 바이트는 기록 시점에 조립한 것과 같다). 실패는 대기열 머리에 두고 1초 뒤 재시도, 압축 후 256MB 를 넘으면 오래된 객체부터 버리고 로그.
- Influx·Redis·S3 어느 것이 닿지 않아도 앱은 뜬다. `INFLUX_TOKEN` 없으면 flusher 비활성, `S3_BUCKET` 없으면 아카이브 비활성.

## 읽는 쪽
- `features/history` 의 `/history/premium`(1주만)·`/history/streaks`(창 7일 이하)·`/history/streaks/bulk`(창 1시간 이하)·`/history/events`(`premium_event` 닫힌 사건(응답 필드 7개만 pivot) + 메모리의 진행 중)·`/history/candles`(`res` 의 계층 버킷 하나, 요청당 1,440창 상한, 진행 중 창 없음) — 앞의 셋은 동시 1개(돌고 있으면 429)이고 셋 모두 `premium` 을 pivot 없이 (코인, 방향) 줄기로 흘려 읽는다(005 §3.4 — premium 은 두 줄기를 시각으로 잇는다), 사건·봉은 같은 조회 키를 공유 캐시로 한 번만 읽는다(사건 60초·64MB, 봉 청크 30초·끝난 청크 600초·256키·32MB, 2026-09-28) — 그리고 `features/landing` 의 `/landing`(022 — `candles_1m` 에서 1위 경로 1시간·`premium_event` 7일 요약(점을 올리지 않고 Flux 가 접은 수백 행), 둘 다 60초 캐시) 만. 다른 조회 API 는 DB 를 0회 접근한다(메모리가 진실). 저장소 불가 시 `/history/*` 는 503 `storage_unavailable`, `/landing` 은 200 에 그 부분만 null.
- `features/flow` 의 `/flow/netflow`(창 1h·6h·24h 코인별 합산)·`/flow/recent`(24시간 안 최신 500행까지) — `chain_flow`, `removed` 제외(050).
- 기동 시 1회: `collect_fail` 24시간 복원(011, 3초 상한), 열린 사건 복원(013 — Redis `premium_events:open` 먼저, 없거나 실패하면 `premium_event` 7일 안 `end_ts 0` 두 단계 조회(두 단계 모두 7일 창·CSV), 3초 상한 — 600초 넘게 못 본 사건은 `last_ts` 로 닫아 쓴다), spark 용 `premium` 최근 30분 1분 버킷 집계(009, 10초 상한·CSV), 롤업 따라잡기 기준점 — 계층마다 위 버킷 가장 늦은 점·아래 계층들 가장 오래된 점(014, 계층당 3초 상한). 복원 조회는 모두 HTTP 요청 타임아웃을 자기 상한과 같게 걸어 상한에서 끊긴다. 롤업 회차는 아래 계층 버킷을 창 단위로 읽는다.
- Redis 스트림 `ticks` 는 flusher 만 읽는다. 키 `premium_events:open` 은 수집의 기동 복원만 읽는다. 채널 `spreads`·키 `spreads:latest` 는 api 의 구독 허브(채널은 접속자가 있을 때만)와 `GET /spreads`(두 역할, 018)·`GET /landing`(두 역할, 5초 캐시, 022)이, 키 `spreads:want` 는 api 만 쓰고 아무도 읽지 않는다(017, 2026-09-26). `collect:heartbeat` 는 collector 가 매초 쓰고 api 의 `/health` 가 읽는다(025). `alerts:log` 는 두 역할의 알림기가 쓰고 수집기 `/admin/alerts` 가 읽는다(034). `admin:clarity`·`admin:clarity:pages` 는 api 의 `/admin/clarity` 만 읽고 쓴다(035·040). S3 를 읽는 코드는 없다.

## 로컬 접속
- dev compose 로 Influx 2.7 과 Redis 7 을 띄운다. env 는 `INFLUX_URL`(기본 `http://localhost:8086`)·`INFLUX_TOKEN`·`REDIS_URL`.
- UI: `http://localhost:8086` (토큰 = `INFLUX_TOKEN`). 점검은 UI 에서 `premium` 점 수를 세는 정도면 된다. Redis 는 `docker compose -f docker-compose.dev.yml exec redis redis-cli XLEN ticks`.
