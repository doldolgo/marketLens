# 050 — flow-eth

상태: TODO | 의존: 002(web-shell — flow 탭 골격·URL 키 `f.`), 005(history — Influx 클라이언트), 009(tick-store — Redis), 001(collect — 업비트 현재가), 028(access-guard — 공개 경로 허용 목록)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
업비트로 들어오고 나가는 이더리움 네트워크 ERC-20 전송을 블록이 생기자마자(약 2초) 온체인에서 잡아, 입출금 레이더 탭에서 코인별 순유입과 최근 전송을 본다. 상장·김프 플레이에서 "지금 어느 코인이 업비트로 몰리는가"를 거래소 공지 없이 안다. 002 의 mock 탭을 실데이터로 바꾼다.

## 2. 범위
- 만드는 것: 이더리움 전송 감지기(core — 수집 프로세스 안의 상시 태스크, 별도 프로세스 없음), Influx measurement `chain_flow`, `GET /flow/netflow`·`GET /flow/recent`(`server/app/features/flow/`), 입출금 레이더 탭 실데이터 화면(`web/src/features/flow/`), 씨앗 데이터(`server/app/data/upbit_eth/`, gzip CSV 넷), 테스트. 라이브러리 추가 없음(`websockets`·`httpx`).
- 하지 않는 것: ETH 네이티브 전송(ETH 핫월렛 미추출), 다른 네트워크(Solana·Arbitrum·Tron…), 빗썸, 멤풀(블록 담기기 전), 상대 주소의 entity 라벨, 기동 이전 과거 백필, 011 실패 구간 통합, Slack 알림.
- 바꾸는 기존 것: 1. 002 §3.10 mock(`shared/mock.ts`·`feed.ts`·`types.ts` 의 flow 부분) 삭제. 2. `core/influx.py` 줄 쓰기에 정밀도 인자(초·나노초). 3. Redis 키 넷. 4. nginx·공개 허용 목록(028)·caddy 기록 제외 목록(027)에 경로 둘. 5. `.env.example`·dev-setup 에 키 둘.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 005: `write_lines(lines, bucket=None)` 은 라인 프로토콜 줄 목록을 **초 정밀도**로 쓴다. 이 스펙이 `precision: "s" | "ns"`(기본 `"s"`) 인자를 더한다 — 기존 호출은 그대로. 조회는 Flux 문자열, 불통은 `InfluxUnavailableError`.
- 001: 수집 프로세스의 메모리 저장소가 업비트 KRW 마켓의 최근 체결가를 심볼별로 든다. 이 스펙은 그 값을 **읽기만** 한다(없는 심볼은 null).
- 009·017: Redis 명령 자동 재시도 없음, 실패는 호출자가 처리. 키마다 메서드 하나·모듈 docstring 한 줄·db.md 한 줄.
- 028: 웹이 부르는 새 경로는 `web/nginx.conf` 의 `location =` 블록(수집 업스트림)과 `server/tests/test_deploy.py` 의 `PUBLIC_API` 에 각각 한 줄.

### 3.2 업비트 주소 모형과 씨앗 (2026-10-08 실측, 원천 `~/Documents/upbit-wallets`)
- 유저 입금주소 = ETH 네트워크 전 코인 공용 EOA 1개. 토큰이 들어오면 업비트 가스 지갑이 ETH 를 소액 충전하고 1~6분 뒤 **코인별 핫월렛**으로 sweep 한다. 핫월렛이 출금도 한다. 핫월렛은 코인당 여러 개일 수 있다.
- 씨앗 넷(gzip CSV, 헤더 1줄, 주소는 소문자):

| 파일 | 행 |
|---|---|
| deposit_addresses.csv.gz | 32,027 |
| hot_wallets.csv.gz | 950 |
| contracts.csv.gz | 206 |
| internal.csv.gz | 6 |

  입금주소는 2025-11-27 사고 뒤 재발급돼 토큰 입금 기록이 있는 주소, 핫월렛은 sweep 목적지 전체(2025-12-01 이후·그 전 포함), 컨트랙트는 `contract,symbol,decimals`(정상 판정 토큰만 — 가짜 토큰 2,960개는 뺐다. 같은 심볼이 컨트랙트 둘일 수 있다, KITE), 내부는 가스 지갑 셋(`0x3e0a91dc5848e17765e3167249a2cb018cbb60ee`·`0x012259c7510e65b3acad37cbb4fd67eaac24937c`·`0x01f05d51b90510e2c01437d5144ba4fa74d3e8b5`)·가스 자금원 둘(`0x4d10231f0294e271ac3b45f6f09fc0feb4de865e`·`0x77196970c9ca0c0864968e7710b9be09c560fb30`)·USDT 본지갑(`0x1d791f12bb6808dc08ab365e2ec8913273c00193`).
- 메모리 집합 셋: **입금주소·핫월렛·내부**. 기동 시 씨앗 + Redis 추가분(아래)을 합친다. 씨앗 읽기 실패는 기동 실패(감지기 없이 뜨면 거짓 0 이 쌓인다).
- 자가 확장 — 블록마다 다음을 적용하고 새 주소는 Redis 집합에 **즉시** 더한다(만료 없음):
  1. 가스 지갑 `0x3e0a…` 가 ETH 를 보낸 수신자 → 입금주소. (블록 전체 트랜잭션에서 본다 — ERC-20 로그엔 없다.)
  2. from 이 입금주소인 전송의 to → 핫월렛(sweep).
  3. from 이 핫월렛이고 to 가 핫월렛∪내부인 전송의 to → 내부(집계 제외). 가스 지갑 수신자가 핫월렛이면 그 주소는 입금주소로 더하지 않는다.
- 한계(빚): 신규 유저의 **첫** 입금은 가스 충전이 입금 뒤에 오므로 놓친다(그 다음부터 잡힌다). 모르는 본지갑으로 나가는 내부 이동은 출금으로 보인다 — 발견하면 사람이 internal 씨앗에 한 줄 더한다.

### 3.3 감지 규칙
- 구독 둘(한 소켓): `eth_subscribe("logs", {address: 컨트랙트 206, topics: [Transfer 시그니처]})`, `eth_subscribe("newHeads")`. Transfer 시그니처 = `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`. 토픽이 셋 미만인 로그는 ERC-20 전송이 아니다 — 버린다. 주소는 토픽 뒤 20바이트.
- **입금** = to 가 입금주소. 상대 = from, 주소 = to.
- **출금** = from 이 핫월렛이고 to 가 입금주소∪핫월렛∪내부 **밖**. 상대 = to, 주소 = from.
- sweep(from 이 입금주소)과 내부 이동은 집합 갱신에만 쓰고 저장하지 않는다. 금액 0 도 저장하지 않는다(진짜 USDT 에 금액 0 `transferFrom` 으로 주소를 오염시키는 스캠이 있다).
- 금액 = data ÷ 10^decimals. 시각 = 블록 시각(초, newHeads 가 준다 — 로그엔 블록 번호뿐이라 번호→시각 표를 들고, 없으면 HTTP 로 블록 하나를 읽는다). 블록 번호·log index 도 남긴다.
- 블록 처리는 **head 를 받고 1.5초 뒤 1회**(가스 지갑 확장 → 그 블록 로그 판정 → 쓰기 1회 → 마지막 블록 저장) — 로그는 head **뒤에** 온다(2026-10-08 publicnode 실측, 블록당 200~400건이 head 뒤 약 1초 안에). 그 뒤 늦게 온 로그는 한 건씩 쓴다(수를 세어 100건마다 로그 1줄). 소켓 수신 루프는 I/O 를 하지 않는다 — 받은 프레임을 쌓기만 하고 처리는 다른 태스크가 한다(수신이 막히면 newHeads 무수신으로 오판한다).
- 리오그: 구독이 `removed: true` 로 되돌린 로그는 같은 점에 `removed` 를 참으로 덮어쓴다. 확정 = `head − block ≥ 2`. 화면은 그 전을 "확정 전"으로 표시한다.
- 지연 목표 약 4초 = 소켓 수신(블록 시각 대비 1.5~3.5초, 2026-10-08 publicnode 실측) + 1.5초 + HTTP 블록 1회. 입금 판정은 2026-10-08 에 1시간(블록 26137380~26137677) 605건을 독립 인덱스와 건별 대조해 누락·오탐 0 이었다.

### 3.4 연결·공백 복구·외부 의존
- env `ETH_WS_URL`(wss)·`ETH_HTTP_URL`(https). **둘 다 있어야** 감지기를 켠다. 하나라도 없으면 기동 때 경고 1줄, 감지기 없이 뜬다(API 는 Influx 의 기존 점을 그대로 준다). WS 는 공개 노드 `wss://ethereum-rpc.publicnode.com`(키 없음·무료, rate limit 미공개). HTTP 는 Ankr `https://rpc.ankr.com/eth/<키>`(키는 사람이 `server/.env` 에) — publicnode HTTP 는 `eth_getLogs` 를 거부하고(403 `Request blocked`, 2026-10-08 실측) Ankr 은 이 플랜에서 WS 가 401 이라, 둘을 섞어 쓴다.
- 재연결: 끊김·열기 실패·**30초 newHeads 무수신**이면 닫고 1초부터 2배·최대 30초 백오프(다른 커넥터와 숫자만 같고 코드는 공유하지 않는다). 첫 데이터 프레임에 백오프 초기화. 상태 전환마다 WARNING 1줄.
- 마지막 처리 블록을 Redis `flow:eth:last_block` 에 블록마다 쓴다. 연결(재연결 포함) 직후 `head − last ≤ 7,200`(약 1일)이면 HTTP `eth_getLogs` 로 **20블록씩** 공백을 재생한다(100블록은 응답 상한 1만 건을 넘긴다 — 2026-10-08 Ankr 실측, publicnode 도 같은 묶음). 재생은 같은 판정·저장 규칙이고 같은 점 덮어쓰기라 중복이 안전하다. 넘으면 경고 1줄 후 head 부터. 키가 없으면(첫 기동) head 부터. 재생 중 실시간 로그는 큐에 두었다가 재생 뒤 순서대로 처리한다. 재생의 HTTP 호출이 상태 오류(403·429 등)로 실패하면 소켓을 다시 열지 않고 경고 1줄 후 head 부터 간다(네트워크 오류는 재연결).
- 블록마다 HTTP `eth_getBlockByNumber(번호, true)` 1회 — 가스 지갑 수신자(§3.2 자가 확장 1). 실패하면 그 블록의 확장만 건너뛰고 경고 없이 다음 블록(입출금 판정엔 영향 없다).
- 부하: 블록당 소켓 프레임 수십~수백 건 + HTTP 1회, 메모리 집합 약 33,000 주소. 수집 박스(c7g.medium)에 측정 가능한 부하가 아니다.

### 3.5 Influx `chain_flow`
- measurement `chain_flow`, 기본 버킷. tag `exchange`(upbit)·`network`(eth)·`dir`(in|out)·`symbol`. time = **블록 시각 초 × 10⁹ + log index**(나노초 — 같은 블록·같은 코인·같은 방향에 전송 여러 건이 흔해 초로는 겹친다). field `amount`(float)·`counterparty`(string)·`addr`(string — 입금이면 입금주소, 출금이면 핫월렛)·`tx_hash`(string)·`block`(int)·`log_index`(int)·`removed`(bool). 한 전송 = 점 1개, 유일키 = (tag 넷, time).
- 쓰기: 블록 단위로 묶어 즉시 1회(동기 쓰기는 스레드로). 실패는 로그 1줄 후 미전송 목록에 두고 다음 블록 회차에 같이 보낸다 — 상한 10,000 점, 넘치면 오래된 것부터 버리고 버린 수를 1줄로 알린다.
- 보존: 기본 버킷 그대로. 하루 약 6,000 점.

### 3.6 `GET /flow/netflow?window=1h|6h|24h`
- `window` 기본 `1h`, 그 밖의 값은 400 `invalid_request`. `removed` 점은 뺀다.
- 응답(camelCase):
```json
{"window":"1h","asOf":1759924800,
 "feed":{"connected":true,"lastBlock":26147699,"lagSec":3,"depositAddrs":32031,"hotWallets":951,"contracts":206},
 "rows":[{"symbol":"SAND","inCount":3,"inAmount":323679.07,"outCount":1,"outAmount":12000.0,
          "netAmount":311679.07,"netKrw":145000000,"lastTs":1759924631}]}
```
  `feed.connected` 는 소켓이 열려 있고 30초 안에 newHeads 를 받았는가, `lagSec` = 지금 − 마지막 블록 시각, `depositAddrs`·`hotWallets` 는 집합 크기(씨앗 + 확장), `contracts` 는 구독하는 컨트랙트 수(씨앗 행 수). 감지기가 꺼져 있으면 `feed` 는 `connected:false`·`lastBlock:null`·`lagSec:null`·집합·컨트랙트 수 0.
- `netKrw` = `netAmount` × 업비트 KRW 현재가(§3.1), 현재가 없으면 null. 정렬 `|netKrw|` 내림차순, null 은 뒤에서 `|netAmount|` 내림차순. `lastTs` = 그 코인의 마지막 전송 블록 시각(초).
- 503 `storage_unavailable`(Influx 불통·토큰 없음).

### 3.7 `GET /flow/recent?limit=100&dir=all&symbol=`
- `limit` 1~500 기본 100, `dir` `all|in|out` 기본 `all`, `symbol` 대문자 심볼(없으면 전체). 범위 밖은 400. 최근 24시간 안에서 최신순(시각 내림차순, 같은 블록은 log index 내림차순). `removed` 점은 뺀다.
- 행: `{"ts":1759924631,"block":26147676,"confirmed":true,"dir":"in","symbol":"SAND","amount":323679.07,"krw":145000000,"addr":"0x4bf9…","counterparty":"0x21a3…","txHash":"0x7d47…"}` — 주소는 **전체** 42자(축약은 화면), `krw` 는 수량 × 현재가, 없으면 null, `confirmed` 는 §3.3. 응답 `{"asOf":…,"head":26147699,"rows":[…]}`. 500행 약 150KB.
- 오류는 §3.6 과 같다.

### 3.8 web — 입출금 레이더 탭
- 002 §3.10 의 mock 화면·데이터·`f.miss`·`f.region`·`f.ex` 키는 없앤다. 탭 id·라벨·전용 푸터 자리는 그대로.
- 바 1: 창 분절 `1시간 / 6시간 / 24시간`(URL `f.win`, 기본 `1h`), 방향 분절 `전체 / 입금 / 출금`(`f.dir`, 기본 all — 최근 전송 표에만 적용), 코인 검색(`코인 심볼 입력 후 Enter`, URL 제외 — 두 표 모두 그 코인만). 우측 피드 상태 `블록 26,147,699 · 3초 전`, `connected:false` 면 warn 색 `피드 끊김`.
- 순유입 표(위): 열 `코인 | 입금 | 출금 | 순유입 | 원화 | 최근`. 입금·출금 칸 = `건수 · 수량`. 순유입 양수 accent, 음수 회색, 원화 null 은 `–` 흐리게. 코인 클릭 → 검색값으로 쓴다. 빈 결과 `이 창에 업비트 ERC-20 입출금 없음`.
- 최근 전송 표(아래): 열 `시각 | 방향 | 코인 | 수량 | 원화 | 주소 | 상대 | tx`. 방향 칩 입금=accent·출금=회색, 원화 ≥ 1억 accent 굵게, 주소·상대는 `앞6…뒤4`(hover 로 전체), tx 는 `https://etherscan.io/tx/{hash}` 새 창, 확정 전 행은 `확정 전` 칩(neutral). 100행. 빈 결과 `해당 조건의 전송 없음`.
- 조회: 탭이 보이는 동안 두 경로를 **5초마다**(상수 `FLOW_POLL_MS`, `shared/config.ts`) 다시 부른다 — 013 과 같이 다른 탭·가려진 브라우저 탭에서는 멈춘다. 실패하면 직전 표를 유지하고 바 1 우측에 `불러오지 못했습니다 (HTTP n)`.
- 푸터: 좌 `업비트 · 이더리움 네트워크 ERC-20 {feed.contracts}종 · 블록 2개 확정 전은 '확정 전'`, 우 `입금주소 {depositAddrs} · 핫월렛 {hotWallets} · 자동 확장`. mock 경고 문구는 없앤다.

## 4. 검증
- to 가 입금주소인 전송은 `in`, 상대 = from, 주소 = to 로 저장된다.
- from 이 핫월렛이고 to 가 밖이면 `out`; to 가 입금주소·핫월렛·내부면 저장하지 않고 내부 집합이 는다.
- from 이 입금주소인 전송은 저장하지 않고 to 가 핫월렛에 더해진다.
- 가스 지갑 수신자가 입금주소에 더해지고 Redis 집합에도 쓰인다. 수신자가 핫월렛이면 더하지 않는다.
- 토픽이 셋 미만이거나 금액 0 인 로그는 버린다.
- `removed: true` 로그는 같은 점을 `removed=true` 로 덮어쓰고 두 API 모두 빼고 준다.
- 시각은 나노초 = 블록 시각 × 10⁹ + log index, 같은 블록 두 전송이 서로 다른 점이 된다.
- 쓰기 실패는 다음 블록 회차에 재시도되고 상한 10,000 을 넘기면 오래된 것부터 버린다.
- 연결 직후 공백이 7,200 블록 이하면 20블록씩 재생하고, 넘으면 head 부터, 키 없으면 head 부터.
- 30초 newHeads 무수신이면 재연결하고 백오프가 1·2·4…30 초로 는다.
- head 뒤에 온 로그가 그 블록의 쓰기 1회에 같이 들어가고, HTTP 블록 조회가 느려도 수신 루프의 head 기록은 늦지 않는다.
- 블록 처리 뒤 늦게 온 로그는 한 건 쓰고 늦은 수가 는다. 재생의 HTTP 상태 오류는 재연결 없이 head 부터.
- env 둘 중 하나가 없으면 감지기가 켜지지 않고 `/flow/netflow` 의 `feed.connected` 가 false·`lastBlock` null.
- `/flow/netflow` 가 창 안 점을 코인별로 합산하고 `|netKrw|` 내림차순·null 뒤로 정렬한다, 현재가 없는 코인은 `netKrw` null.
- `/flow/recent` 가 `limit`·`dir`·`symbol` 을 적용하고 범위 밖은 400, Influx 불통은 503.
- `write_lines` 의 기존 호출(초 정밀도)이 바뀌지 않는다.
- 공개 허용 목록 테스트가 두 경로를 통과한다.
- 수동: 로컬 dev compose + 수집 기동 → 탭에서 수 분 안에 실제 입금이 뜨고 `확정 전` 칩이 2블록 뒤 사라진다. 본인 입금주소로 소액 입금해 행이 뜨는지 본다.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — 행 추가 `| flow-eth | server: 이더리움 ERC-20 Transfer WS 구독(publicnode)→업비트 입금주소·핫월렛 집합 대조→Influx chain_flow(ns)·Redis 집합 자가 확장·공백 재생 7,200블록, /flow/netflow·/flow/recent | web: 입출금 레이더 탭 실데이터(순유입 표·최근 전송 표·5초 폴링) | ETH 네트워크·업비트 한정 |`. web-shell 행의 "mock 탭 3종(gap·pp·flow)" → "mock 탭 2종(gap·pp)". 알려진 빚에 `- (050) ETH 네이티브·다른 네트워크·빗썸 미지원, 신규 주소 첫 입금 누락, 모르는 본지갑 이동이 출금으로 보임(internal 씨앗에 사람이 추가)`. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스에 050 행, 상태 DONE. **항상 포함.**
- `docs/context/architecture.md` — "현재 구조" 절에 flow-eth 항목(감지기는 core — 수집 수명주기가 띄우는 상시 태스크, 집합 셋·확장 규칙, Influx ns 쓰기·Redis 집합, 기능 폴더 flow 의 API 둘). "데이터 흐름 (BE)" 그림에 `ETH 노드 WS → 감지기 → chain_flow` 가지 추가.
- `docs/context/dev-setup.md` — env 표에 `ETH_WS_URL`·`ETH_HTTP_URL` 두 줄(WS publicnode·HTTP Ankr, 둘 다 있어야 감지기 켬).
- `server/.env.example` — 같은 두 키와 설명 3줄.
- `docs/context/product.md` — 용어 절에 `입금주소`(업비트가 유저마다 준 ETH 네트워크 공용 수신 주소)·`핫월렛`(입금을 모으고 출금을 내보내는 업비트 코인별 지갑)·`sweep`(입금주소→핫월렛 이동, 집계 제외)·`순유입`(창 안 입금 수량 − 출금 수량). 기능 목록의 입출금 레이더 행을 실데이터 설명으로.
- `docs/context/db.md` — measurement 절에 `chain_flow` 한 줄(§3.5 그대로), Redis 절에 `flow:eth:last_block`(문자열, 만료 없음)·`flow:eth:deposit_addrs`·`flow:eth:hot_wallets`·`flow:eth:internal`(집합, 만료 없음 — 씨앗에 더한 주소) 네 줄, 쓰는 쪽·읽는 쪽에 감지기·`/flow/*` 한 줄씩, 시각 단위 절에 "chain_flow 만 나노초".
- `docs/specs/002-web-shell.md` — §3.10 제목 뒤에 `(050 이 실데이터로 교체 — 이 절은 2026-10-08 까지의 mock 기록)` 한 줄.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
