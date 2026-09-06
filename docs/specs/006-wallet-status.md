# 006 — wallet-status

상태: DONE | 의존: 001(collect — 틱 루프·행 교체 규칙), 003(spreads), 004(analysis), 010(raw-archive — 응답 원문 기록)

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
세 거래소의 **입출금 가능 여부를 망(network) 단위로** 거래소 API 에서 받아 60초마다 캐시한다.
`/spreads` 의 각 행은 **국내 거래소 망을 기준으로 바이낸스의 같은 망을 찾아** `netDom / depDom / wdDom / depFx / wdFx` 를 채운다.
끝나면 트레이더는 "GRT 가 김프 3% 인데 바이낸스 ETH 망 출금이 막혀 못 옮긴다" 를 표에서 바로 본다.
API 키가 없어도 서버는 해당 값을 `unknown` 으로 두고 정상 동작한다.

## 2. 범위
- 만드는 것: `server/app/features/wallet_status/` — 업비트·빗썸·바이낸스 입출금 조회 3종과 틱 루프 등록. 망 맞추기 규칙은 spreads 도 쓰므로 `server/app/core/` 에 둔다.
- 이 기능은 **BE 전용**이다. 새 엔드포인트·화면이 없으므로 `web/` 쪽 폴더는 만들지 않는다.
- 하지 않는 것: 입출금 수수료·최소 출금량, 지갑 상태의 영속(저장하지 않는다), FE 변경(spreads 탭이 이미 `netDom ?? '–'` 와 `depDom/wdDom/depFx/wdFx` 를 그린다), 입출금 레이더 탭(온체인 mock, 무관).

**바꾸는 기존 것**
1. 스펙 003 `/spreads` — 행의 입출금 5필드는 **§3.6 망 판정** 으로 채운다(§3.7). 다른 필드·정렬·수식은 손대지 않는다.
2. 스펙 001 틱 루프 — 매초 도는 틱 루프가 이 조회기를 부른다(60초에 한 번 실호출, 사이 틱은 캐시 — §3.5). 실패 시 경고 1줄을 `/refresh` 의 `warnings` 에 넣는다.
3. 스펙 003 `POST /refresh` — 응답 `snapshots[]`(거래소당 1항목)의 각 원소에 `walletStatusAvailable: bool` 을 추가한다. 바이낸스 항목에도 붙는다 — `usdkrw[]` 는 국내 전용이라 쓸 수 없다.

## 3. 동작

### 3.1 3-state 와 데이터 모양
- 상태는 `ok`(확인했고 열림), `stopped`(확인했고 막힘), `unknown`(확인 불가) 셋이다. JSON 으로는 `true / false / null`. **`null` 을 열림으로 읽는 코드는 버그다.**
- `unknown` 이 되는 경우: 키 없음, API 실패, 응답에 그 코인이 없음, 망 매칭 불확실(§3.6).
- 거래소 조회 결과는 "코인 심볼(대문자) → 코인 단위 deposit/withdrawal + 망 목록" 이다. 망 1개 = `{code, name, dep, wd}`. code 는 대문자, name 은 표시명이며 없으면 code.
- 같은 코인이 여러 행(망마다 1행)으로 오면 코인 단위 값은 **망별 OR** 이고, 망 목록은 **응답 순서대로 전부 보존**한다. 어느 망이 열렸는지를 §3.6 tie-break 가 쓰기 때문이다. 망 코드가 빈 행은 코인 값에만 반영하고 망 목록엔 넣지 않는다.
- 이 데이터는 스펙 001 의 live_store 스냅샷에 `deposit_enabled / withdrawal_enabled (bool|null)` 와 `networks (list, 빈 리스트 = 망 정보 없음)` 로 들어간다. 001 이 새 시세 메시지로 행을 교체할 때 이 3필드는 직전 행의 값을 물려받고, 조회기가 60초마다 그 거래소의 전 행에 덮어쓴다.

### 3.2 업비트 — JWT 인증 (env `UPBIT_API_KEY` / `UPBIT_SECRET_KEY`)
- `GET {업비트 base}/v1/status/wallet`. 쿼리 파라미터 없음.
- 인증: 페이로드 `{"access_key": <API 키>, "nonce": <요청마다 새 UUID4 문자열>}` 을 **secret 키로 HS256 서명한 JWT** 로 만들어 `Authorization: Bearer <토큰>` 헤더로 보낸다. 쿼리가 없으므로 query_hash 는 넣지 않는다. PyJWT 의존성 추가 허용.
- 키 둘 중 하나라도 비면 호출하지 않고 실패로 끝낸다. 메시지 `UPBIT_API_KEY / UPBIT_SECRET_KEY 가 비어 있습니다.`
- 응답: JSON 배열. 쓰는 필드는 `currency`, `wallet_state`, `net_type`(예 `"BASENET"`), `network_name`(없을 수 있음). `currency` 나 `wallet_state` 가 비면 그 행은 건너뛴다.
- `wallet_state` 해석:
  - `working` → dep ok, wd ok
  - `withdraw_only` → dep stopped, wd ok
  - `deposit_only` → dep ok, wd stopped
  - `paused`, `unsupported`, 그 외 문자열 → dep stopped, wd stopped
- 망: code = `net_type` 대문자. name = `network_name`, 없으면 code.

### 3.3 바이낸스 — HMAC-SHA256 서명 (env `BINANCE_API_KEY` / `BINANCE_SECRET_KEY`)
- `GET {바이낸스 spot base}/sapi/v1/capital/config/getall`.
- 인증: 쿼리 문자열을 **정확히 `timestamp=<현재 ms>&recvWindow=10000` 순서·이 두 키만** 으로 만든다. 그 문자열 그대로(재정렬·재인코딩 금지)를 secret 키로 HMAC-SHA256 한 hex 를 `&signature=<hex>` 로 뒤에 붙인다. 헤더 `X-MBX-APIKEY: <API 키>`.
- 키 검사·메시지는 업비트와 같은 방식: `BINANCE_API_KEY / BINANCE_SECRET_KEY 가 비어 있습니다.`
- 응답: JSON 배열. 행별 `coin`, `networkList`. `coin` 이 비거나 `networkList` 가 비면 건너뛴다.
- 코인 레벨 `depositAllEnable / withdrawAllEnable` 은 **쓰지 않는다**. 모든 망이 열려야 true 가 되는 값이라 비관 편향이다.
- 망별 필드: `network`(코드, 예 `"ETH"`, `"ARBITRUM"`), `name`(예 `"Ethereum (ERC20)"`), `depositEnable`, `withdrawEnable`. 코인 단위 dep/wd = 망별 OR. `network` 가 빈 항목은 망 목록에서 제외.

### 3.4 빗썸 — public, 키 불필요
- `GET {빗썸 base}/public/assetsstatus/multichain/ALL`. 응답 예: `{"status":"0000","data":[{"currency":"ETH","net_type":"ARB_ETH","deposit_status":1,"withdrawal_status":1}, …]}`.
- `status != "0000"` 이거나 `data` 가 배열이 아니면 실패. 메시지 `빗썸 자산 상태 응답 형식이 올바르지 않습니다.`
- dep = `deposit_status == 1`, wd = `withdrawal_status == 1`. **정수 비교**라 문자열 `"1"` 은 stopped. `currency` 비면 건너뜀. 망 code = `net_type` 대문자.
- 코인 단위 엔드포인트 `/public/assetsstatus/ALL` 은 망을 모르므로 쓰지 않는다.

### 3.5 공통 호출 규칙, 60초 캐시, 실패
- 요청별 타임아웃 10초. 시세용 3초보다 길다.
- 호출마다 그 거래소의 REST 호출 수를 1 올린다(`/refresh` 의 `calls`). 응답 본문은 성공·실패 모두 받은 그대로 010 의 원문 싱크에 기록한다(`source` = `rest:<경로>`, `received_at_ms` = 응답을 받은 서버 시각) — 요청 헤더·서명은 기록하지 않는다. 기록은 상태 코드를 해석하기 전에 하고, 응답 자체가 없는 실패(타임아웃·연결 오류)는 기록할 본문이 없으므로 아무것도 남기지 않는다. 기록 함수는 배선 시 조회기 묶음에 주입하며, 주입하지 않으면 무동작이다.
- HTTP 200 이 아니면 실패: `<거래소 표시명> 지갑 상태 API 가 <status> 를 반환했습니다.` + detail `{exchange, body 앞 500자}`.
- 조회 3종은 예외를 삼키지 않는다. 삼키는 건 틱 루프(001)다. 실패한 거래소만 경고 1줄 `"<거래소id> 입출금 상태 조회 실패 — <메시지> (해당 거래소의 deposit_enabled / withdrawal_enabled 는 null)"` 을 `/refresh` 의 `warnings` 에 넣고, 그 거래소 전 코인을 `unknown`·망 목록 빈 리스트로 둔다.
- `/refresh` 의 거래소별 `walletStatusAvailable` 은 조회 성공이면 true.
- 주기: 60초마다 세 거래소 **병렬** 조회, 사이 틱은 캐시. 기동 첫 틱은 캐시가 비어 1초 안에 호출한다(키 없는 거래소는 즉시 실패 → 경고).
- 한 거래소 실패는 그 거래소만 영향. 시세 수집은 무관. 재시도는 다음 60초 회차(별도 백오프 없음).
- 실패한 거래소는 `/refresh` 응답에 표시된다(`walletStatusAvailable=false`). 틱 루프가 실패 상태인 거래소 목록을 틱의 `dwFailed` 에 싣고, 009 가 그것으로 `dw_fail` 점을 쓴다.
- **실패한 회차는 직전 성공값을 유지하지 않고 `unknown` 으로 덮는다.** 오래된 "열림" 을 보여주는 쪽이 더 위험하다.
- 실패 상태(내부 `dw_failed`, `/refresh`의 경고·`walletStatusAvailable=false`)는 조회가 일어난 틱만이 아니라 **다음 성공 조회까지 매 틱 유지**한다 — 틱·`/refresh` 가 조회 회차와 어긋나도 실패가 관측되게.
- 키·토큰·서명값은 로그·에러 detail 에 절대 남기지 않는다.

### 3.6 망 맞추기 (국내 망 기준)
원칙: 국내 거래소는 대부분 코인이 망 하나라 **국내 망이 기준**이다. 다른 망을 같다고 하는 미탐은 돈이 나가므로 **애매하면 unknown**.

**정규화** (순서대로):
1. 이름을 소문자로.
2. 괄호 주석 제거 (`(ERC20)`).
3. 영숫자 외 문자로 토큰 분리.
4. 불용어 제거: `network networks chain mainnet protocol pos token coin`.
5. 별칭 치환: `avax→avalanche, eth→ethereum, btc→bitcoin, matic→polygon, pol→polygon, sol→solana, trx→tron, arb→arbitrum, op→optimism`.
6. 토큰 순서는 무시한다.

예: `Ethereum (ERC20)`→{ethereum}, `Polygon POS`→{polygon}, `Avalanche C-Chain` = `AVAX C-Chain`→{avalanche, c}.

**국내 망 1개 vs 해외 망 목록 판정** (순서대로 첫 히트):
0. 해외 망 목록이 비면 `unknown`. 정보 없음 ≠ 그 망 없음.
1. 코드 대문자 일치 → matched. 국내 코드가 비면 이 규칙은 건너뛴다.
2. 토큰 집합 완전 일치 → matched. 국내 망의 토큰 집합이 비면(이름이 전부 불용어 — 예 `Mainnet`) 규칙 2~4 를 보지 않고 **`unknown`** 으로 끝낸다 — 빈 집합끼리의 완전 일치는 아무 망이나 맞다는 뜻이 되고, 이름에 정보가 없으니 `absent`(해외가 그 망을 안 다룸) 라고도 말할 수 없다.
3. 토큰을 정렬해 붙인 문자열 일치 (`AssetHub Polkadot` ↔ `Asset Hub Polkadot`) → matched. 확인된 동일 체인 쌍 표(초기값 `{metal,l2}` ↔ `{metal,dao,l2}`, 양방향)도 matched. 규칙을 느슨하게 푸는 대신 이 표를 늘린다.
4. 못 찾음: 어느 해외 망과든 토큰이 하나라도 겹치거나, 길이 3 이상 토큰끼리 한쪽이 다른 쪽 접두사(`kat` ↔ `katana`)면 `unknown`. 아니면 `absent`.

예: 업비트 `SEI "Sei"` vs 바이낸스 `SEIEVM "Sei EVM"` → {sei} ⊂ {sei, evm} 겹침 → **unknown**. 가장 중요한 케이스다. `QKC "Quarkchain"` vs `ETH "Ethereum (ERC20)"` → absent.

**국내 망이 여럿일 때 tie-break**: 국내 망을 응답 순서대로 판정한다.
1. matched 이고 **국내 입금 ok 이면서 해외 출금 ok**(해외→국내로 실제 옮길 수 있는 길)인 첫 망을 즉시 채택.
2. 없으면 matched 인 첫 망. 막혀 있어도 맞는 망을 보여준다.
3. 그것도 없으면 첫 국내 망의 판정.
4. 국내 망 목록이 비는 경우는 여기까지 오지 않는다 — §3.7-1 이 먼저 코인 단위 값으로 처리한다.

### 3.7 `/spreads` 행의 5필드 (스펙 003 문단 대체)
행의 국내 스냅샷 망 목록을 D, 해외(바이낸스) 망 목록을 F 라 할 때:
1. D 비면(키 없음·망 정보 없는 과도기) → 5필드는 **코인 단위 값 그대로**. `depDom/wdDom` = 국내 코인 값, `depFx/wdFx` = 해외 코인 값, `netDom = null`.
2. D 있으면 §3.6 으로 국내 망·판정·해외 망을 고른다. `netDom` = 고른 국내 망 name, `depDom/wdDom` = 그 망의 dep/wd.
3. matched → `depFx/wdFx` = 맞춘 해외 망의 dep/wd.
4. absent → `depFx = wdFx = false`. 해외가 그 망을 안 다룸 = 옮길 길 없음.
5. unknown → F 비면 해외 **코인 단위** 값. F 있으면 **`null, null`**. 있는데 못 맞춤 = 모른다고 말한다. 코인 단위로 접으면 낙관 편향이 돌아온다.

`status=fail` 행도 같은 규칙.
예(GRT 실사례): 업비트 `[ETH "Ethereum" ok/ok]`, 바이낸스 `[ARBITRUM "Arbitrum One" ok/ok, ETH "Ethereum (ERC20)" ok/stopped]` → 코드 일치 ETH →
```json
{"sym":"GRT","dom":"upbit","fx":"binance","netDom":"Ethereum","depDom":true,"wdDom":true,"depFx":true,"wdFx":false}
```
코인 단위로는 바이낸스 출금 가능이지만 실제로는 못 옮긴다.

### 3.8 스펙 004 `/matrix` `/arbitrage` 와의 관계
변경 없음. 두 엔드포인트의 `depositAvailable / withdrawalAvailable` 은 **코인 단위 값 그대로**다. `null` 이면 기존 경고(`/matrix` "입출금이 막힌 것으로 표시된 조합이 있습니다…", `/arbitrage` "… 확인하지 못했습니다 …")가 그대로 나와야 한다. 망 판정은 `/spreads` 만 한다.

## 4. 검증
네트워크 호출 없이 검증한다. **서명 테스트를 최소 1개 반드시 넣는다.** 서명은 결정적이라 고정 키로 기대값을 직접 계산할 수 있다.
- 바이낸스 서명: 가짜 secret 으로 `timestamp=…&recvWindow=10000` 의 HMAC-SHA256 hex 를 직접 계산한 값과 요청 URL 의 `signature` 가 같고, `X-MBX-APIKEY` 헤더가 붙는다.
- 업비트 JWT: `Authorization: Bearer` 토큰을 같은 secret 으로 HS256 디코드하면 `access_key` 가 가짜 키와 같고 `nonce` 가 있으며, 두 번 호출하면 nonce 가 다르다.
- 업비트 `withdraw_only` → dep stopped, wd ok. `paused`·미정의 문자열 → stopped/stopped.
- 업비트 같은 코인 2행(망 2개, 한쪽만 출금 ok) → 코인 단위 wd ok, 망 목록 2개 순서 보존.
- 바이낸스 `networkList` 빈 코인은 결과에 없다. `depositAllEnable=false` 여도 한 망이 열려 있으면 코인 단위 dep ok.
- 빗썸 `deposit_status: "1"`(문자열) → stopped. `status != "0000"` → 실패.
- 키 없는 업비트·바이낸스 → 호출 0회로 실패, `/refresh` 의 `warnings` 에 각 1줄, `walletStatusAvailable` false. 빗썸은 키 없이 true.
- HTTP 500 응답 → 실패 메시지에 상태 코드, detail body 500자 이하, secret 미포함.
- 정규화: `Ethereum (ERC20)` = {ethereum}, `Polygon POS` = {polygon}, `AVAX C-Chain` = `Avalanche C-Chain`.
- 판정: 코드 일치 matched, SEI vs SEIEVM unknown, QKC vs ETH absent, 해외 망 빈 목록 unknown, AssetHub Polkadot 경계 무시 matched, 국내 망 이름이 전부 불용어(`Mainnet`)이고 코드가 안 맞으면 unknown(해외 이름이 `Ethereum` 이든 `Network` 든).
- tie-break: 국내 망 2개 중 두 번째만 "국내 입금 ok + 해외 출금 ok" 이면 두 번째를 고른다.
- `/spreads` 5케이스(§3.7 1~5): 빈 D → 코인 값·`netDom null`. GRT → wdFx false. QKC → depFx·wdFx false. SEI → `null, null`. unknown + F 빈 목록 → 해외 코인 값.
- 실패한 회차 뒤 `/spreads` 의 해당 거래소 행은 전부 `null`(직전 성공값 미유지).
- 조회 응답 본문(성공·HTTP 500 실패 모두)이 원문 싱크(fake)에 `exchange`·`rest:<경로>`·수신 시각과 함께 그대로 기록되고, 요청 헤더·서명은 기록되지 않는다.
- 001 이 같은 코인의 시세 행을 새 메시지로 교체해도 입출금 3필드가 유지된다.
- 키 없이 기동: `/spreads` 모든 행에 `netDom depDom wdDom depFx wdFx` 5키가 있다. `depDom wdDom depFx wdFx` 값은 `true/false/null` 뿐이고 `netDom` 은 문자열 또는 null. 빗썸 행 중 `depDom` 이 null 이 아닌 행이 있다(키 불필요). `/refresh` 의 빗썸 `walletStatusAvailable` true, 업비트·바이낸스 false + 입출금 경고 2줄.
- 수동: 실키를 env 파일에 넣고(키 투입은 사람이 한다 — CLAUDE.md §5 접근 규칙) 기동 → `/refresh` 경고 없음·세 거래소 모두 true, `/spreads` 에 `netDom` 이 채워진 행이 다수.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q
# All checks passed! / 182 files left unchanged / 435 passed, 1 warning in 4.78s
#   (006 몫 53개: features/wallet_status/tests 4파일 31 + tests/test_networks.py 14 + tests/test_wallet_integration.py 2
#    + features/spreads/tests/test_wallet_fields.py 6 — 서명 테스트 2개(업비트 JWT·바이낸스 HMAC)와 원문 싱크 기록 8개 포함)
# 키 없이 기동(:8041, 거래소 도메인 차단 망): /health → {"status":"ok","version":"0.1.0"}, 트레이스백 0.
#   /spreads 는 404(마켓 목록을 못 받아 우주가 빔 — dev-setup.md 로컬 메모의 정상 동작). 키 없는 /spreads 5키·빗썸 depDom 비-null·/refresh 경고 2줄은 tests/test_wallet_integration.py 가 같은 배선으로 단언한다.
```
EC2 에서 확인 필요(이 망은 거래소 REST 를 막는다): 실키 기동 후 `/refresh` 경고 없음·세 거래소 `walletStatusAvailable` true(업비트는 EC2 IP 가 허용 목록에 있어야 한다), `/spreads` 에 `netDom` 채워진 행 다수, S3 `raw/exchange=<id>/` 객체에 `rest:/v1/status/wallet`·`rest:/public/assetsstatus/multichain/ALL`·`rest:/sapi/v1/capital/config/getall` 줄이 60초마다 1개씩.

## 6. 갱신할 문서
- `docs/context/status.md` — wallet-status 행. server: 3거래소 조회·`/spreads` 망 판정. web: 없음(spreads 탭이 표시).
- `docs/context/architecture.md` — 수집 경로 문단의 입출금 설명에 "망 판정은 `/spreads` 에서, 빗썸은 키 불필요" 추가.
- `docs/context/dev-setup.md` — env 표 API 키 행: "없으면 해당 거래소 `unknown`, 빗썸은 키 불필요".
- `CLAUDE.md` 스펙 인덱스 상태.

## 7. 실행 보고 (실행 세션이 채움)
- 소요 파일: `server/app/core/networks.py`(`Network`·`normalize_name`·`match_network`·`pick_domestic`·동일 체인 쌍 표), `server/app/core/contracts.py`(`WalletStatusProvider` Protocol), `server/app/features/wallet_status/`(`upbit.py`·`binance.py`·`bithumb.py` 조회기 — 코드 공유 없음, `service.py` `WalletStatusService`, `models.py`, `tests/` 4파일 + `helpers.py` 의 `Capture`·`FakeRecorder`), `server/app/core/ticks.py`(틱마다 `apply`·`failed`, 조회는 별도 태스크), `server/app/core/collect.py`(`force=True` 조회·`wallet_status_available`), `server/app/features/spreads/service.py`(`_wallet_fields` §3.7)·`models.py`(`walletStatusAvailable`), `server/app/main.py`(키 4개·`record` 주입), `server/tests/test_networks.py`·`test_wallet_integration.py`, `server/pyproject.toml`(PyJWT).
- 추측한 지점(스펙에 확정 문구로 적음): (1) 원문 기록 시점·범위 — §3.5: 응답을 받는 즉시 상태 코드 해석 전에 본문을 남기고, 응답이 없는 실패(타임아웃·연결 오류)는 남기지 않으며, 기록 함수는 배선 시 조회기 묶음에 주입(미주입이면 무동작). 바이낸스 `source` 는 경로만 — 쿼리의 timestamp·서명은 요청 쪽이라 제외. (2) 입출금 조회는 틱 루프와 별도 태스크(10초 타임아웃이 시세를 막지 않게, 겹치지 않게 하나만). (3) core↔feature 결합은 Protocol 로, 배선은 `main.py`. (4) 전부 불용어라 토큰 집합이 빈 국내 망 이름은 코드가 안 맞으면 `unknown` — §3.6-2 에 확정 문구. (5) `/refresh` 의 `calls` 는 실호출이 나간 조회만 더한다(키 없음은 0).
- 실행 중 함께 고친 절: §3.5 원문 기록 규칙 문장(위 1번).
- 남은 빚: 실키 3-true·`netDom` 채움·S3 `rest:` 줄 확인은 EC2(§5) / 동일 체인 쌍 표는 1쌍뿐 — 실서버 `unknown` 을 보며 늘린다(status.md 빚).
