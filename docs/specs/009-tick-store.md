# 009 — tick-store

상태: TODO | 의존: 001(collect — 사이클·LiveStore), 003(spreads — 김프 수식·`/spreads`), 005(history — persist·Influx 모델), 007(deploy — compose)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.

## 1. 목적
LiveStore 는 최신 1장만 들고 1초마다 교체되므로 초단위 역사가 버려지고, Influx 는 60초 샘플이라 그 사이 스파이크가 기록에 남지 않는다. 이 스펙은 저장을 3계층으로 나눈다: **최신 1장은 지금처럼 메모리(LiveStore, 핫 경로 무변경)**, **초단위 틱은 Redis 스트림에 rolling 1시간**, **장기 역사는 기존대로 Influx(1분)**. 부수 효과로 `/spreads` 의 `spark`(김프 추이 미니 그래프 데이터)가 처음으로 채워진다.

## 2. 범위
- 만드는 것: core 의 Redis 클라이언트(연결·스트림 쓰기/읽기), 수집 사이클 끝의 **틱 적재**, persist 루프 개편(**flusher** — 스트림에서 읽어 Influx 로), **spark 캐시**(코인별 최근 30개, 재기동 복원), dev·배포 compose 의 `redis` 컨테이너.
- 바꾸는 기존 것: ① 001 수집 사이클 — 6단계 뒤에 틱 적재 1단계 추가(Redis 실패는 로그 후 계속). ② 005 persist 루프 — 김프 점의 원천을 "LiveStore 직접 읽기(락 필요)"에서 "스트림 최신 틱(락 불필요)"으로 변경. 수식·Influx 모델·1분 해상도·쓰기 1회·실패 격리 규칙은 그대로. ③ 003 `/spreads` — 행의 `spark` 가 빈 배열에서 실값이 된다(키·타입 불변, FE 하위호환). ④ architecture.md — "메모리가 진실" 원칙을 3계층으로 개정.
- 하지 않는 것: FE 스파크라인 렌더(후속 — FE 는 이미 `spark: number[]` 타입 보유). 초단위 조회 API(후속). **Influx 적재 해상도 변경 없음 — 1분 유지**(분단위 457만 점에서도 전 구간 streaks 가 4GB Influx 를 재시작시킨 실측 때문. 인프라 확장·과부하 테스트 후 재검토). 핫 경로 변경 없음(`/spreads` 는 여전히 LiveStore·워커 1개). 수집기/API 프로세스 분리(후속 — 그때 핫 층 이관을 함께 설계).

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001: 수집 사이클은 1초, 끝에 LiveStore 를 통째 교체한다. 사이클은 예외를 밖으로 던지지 않는다.
- 003: 김프 수식 — `fwd = premium_percent(buy_krw=fx_ask×rate_ask, sell_krw=dom_bid)`, `rev = premium_percent(buy_krw=dom_ask, sell_krw=fx_bid×rate_bid)`. 조합 자격: 호가 있고 그 국내 거래소 자신의 USDT 시세가 있어야 하며, 여섯 값 중 ≤0 이면 건너뜀.
- 005: Influx `premium` = tag(dom·fx·base) × time(초) × field(fwd·rev). 같은 태그+시각은 덮어씀. 쓰기 실패는 로그 후 다음 회차.

### 3.2 Redis 구성
- 컨테이너 `redis`(7-alpine), compose 내부 전용(호스트 비노출), `appendonly yes`, named volume — 재기동 시 창이 보존된다.
- env `REDIS_URL`(기본 `redis://localhost:6379/0`). compose 는 007 의 `INFLUX_URL` 패턴대로 `redis://redis:6379/0` 을 `environment` 로 오버라이드한다. `server/.env.example` 에 키 추가.
- 키는 하나: **`hist:ticks` Stream**. 사이클당 1엔트리 `XADD MAXLEN ~ 3600`(approximate — rolling 약 1시간. 창 길이는 코드 상수이며 인프라 확장 후 늘린다).
- 엔트리 필드: `ts`(수집 시각 epoch 초), `data`(gzip 압축 JSON — 그 사이클의 김프 조합 배열 `[{dom,fx,base,fwd,rev}…]`).

### 3.3 틱 적재 (수집 사이클 끝, 매 1초)
- 메모리 교체가 끝난 뒤, §3.1 의 수식·자격으로 전 조합의 fwd/rev 를 계산해 엔트리 1건을 XADD 한다. USDT 시세가 하나도 없으면 이번 틱은 적재하지 않는다.
- **Redis 가 닿지 않아도 앱은 뜬다**: 기동 시 연결 실패는 경고 로그 1줄, 틱 적재는 회차마다 재시도, 수집·조회는 무영향. `REDIS_URL` 성격상 비정상 값이면 틱 적재·spark 비활성(`/spreads` 의 `spark` 는 빈 배열) — Influx 장애 격리와 같은 원칙이다.

### 3.4 flusher (60초 — 기존 persist 루프의 개편)
- 기동 후 먼저 60초 잔 뒤 첫 회차(기존과 동일). 회차마다:
  1. 스트림의 **최신 엔트리 1건**을 읽는다(없으면 이번 회차 생략 — 수집 전이거나 Redis 다운).
  2. 그 틱의 조합들을 Influx `premium` 점으로 변환해 **쓰기 1번**으로 보낸다. `dw_fail` 은 기존대로 collector 의 관측 목록에서.
- LiveStore 를 더 읽지 않으므로 **수집 락을 잡지 않는다** — persist 가 수집을 1초라도 세울 일이 사라진다.
- Influx 실패 규칙은 005 그대로: `DB 저장 실패 (연속 n회)` 로그, 다음 회차 재시도, 놓친 회차는 구멍.

### 3.5 spark — `/spreads` 행의 김프 추이
- 정의: 행(dom,fx,base)마다 **fwd 의 최근 30개, 1분 간격, 오래된 → 최신**. 30개 미만이면 있는 만큼.
- flusher 회차마다 그 틱의 fwd 를 코인별 링버퍼(최대 30)에 밀어 넣고, 결과 맵을 LiveStore 에 게시한다. `/spreads` 는 행 조립 때 그 맵을 읽는다(없는 조합은 빈 배열) — 폴링 경로에 Redis 호출이 생기지 않는다.
- **재기동 복원**: 기동 시 스트림에서 최근 30분을 60초 간격으로 샘플해 링버퍼를 채운다(스트림이 비면 빈 채로 시작해 60초마다 참). fail 행도 spark 는 유지한다 — 추이는 추이다.

### 3.6 compose
- dev(`docker-compose.dev.yml`)와 배포(`docker-compose.yml`) 둘 다에 `redis` 서비스 추가 — 이미지 `redis:7-alpine`, `--appendonly yes`, named volume, 호스트 비노출. server 의 `REDIS_URL` 오버라이드. 배포 가드·기존 컨테이너 무접촉 규칙(007)은 그대로.

## 4. 검증
네트워크 없음(fakeredis 로 Redis 를 흉내낸다 — dev 의존성 추가 허용):
- 사이클 1회 → 스트림 엔트리 1건, 압축 해제 시 조합 수·fwd/rev 값이 같은 시드의 `/spreads` 계산과 일치
- USDT 시세가 하나도 없는 사이클 → 엔트리 없음
- Redis 불달로 기동 → 앱 뜨고 수집·`/spreads` 정상(`spark` 빈 배열), 경고 로그 1줄, 사이클은 계속 돈다
- flusher: 스트림이 비면 회차 생략(Influx 쓰기 0회); 틱이 있으면 Influx 점 수 = 조합 수, 값 일치; Influx 실패 시 로그 후 다음 회차
- spark: flusher 2회 뒤 행 spark 길이 2·값이 각 회차 fwd·오래된→최신; 31회 뒤에도 30 유지; 스트림을 시드하고 재기동하면 spark 가 복원된다; Redis 없으면 빈 배열
- `/spreads` 응답: 행 18키·최상위 키 불변(spark 만 값이 참), 기존 테스트 전부 통과
수동: dev compose(redis+influx) 기동 → 2~3분 뒤 `/spreads` 행에 spark 1~3개, `docker compose exec redis redis-cli XLEN hist:ticks` 증가, `/history/premium` count 증가. Redis 컨테이너를 내리면 경고 로그 후에도 `/spreads` 정상.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/architecture.md` — 원칙 "메모리가 진실" 을 3계층(최신=메모리 · 최근 1시간=Redis 스트림 · 역사=Influx 1분)으로 개정, 데이터 흐름(BE) 다이어그램에 Redis 층 추가, "현재 구조" 에 009 항목. **항상 포함 아님이지만 이 스펙의 핵심.**
- `docs/context/db.md` — "Redis" 절 신설: 키(`hist:ticks` Stream)·엔트리 모양·MAXLEN 1시간·AOF·접속(`REDIS_URL`)·"읽는 쪽 = flusher·spark 복원뿐".
- `docs/context/dev-setup.md` — env 표에 `REDIS_URL`, dev compose 문구(redis+influx), 스모크에 spark 확인 1줄.
- `docs/context/status.md` — spreads 행에 "spark 채워짐", history 행에 "persist → flusher(스트림 원천)". **항상 포함.**
- `CLAUDE.md` — §2 구조의 core 설명에 Redis 클라이언트, 스펙 인덱스 009 행 → DONE. **항상 포함.**
- `server/.env.example` — `REDIS_URL` 행.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
