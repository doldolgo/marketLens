# 022 — landing

상태: TODO | 의존: 003 spreads(행 계약), 006 wallet-status(5필드), 013 premium-events(`premium_event`), 014 premium-1m(`candles_1m`), 016 process-split(`api` 역할), 017·018(`spreads:latest`), 007 deploy(nginx·web 이미지), 023 domain-tls(절대 주소)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
처음 온 사람이 `https://kimptrack.com/` 을 열면 **지금 실제로 옮길 수 있는 김프 하나**를 실데이터로 먼저 본다 — 어느 거래소에서 사서 어디서 파는지, $1,000 체결 기준 값, 출금·입금이 열린 망, 최근 1시간 추이. 그 아래에서 숫자만 보고 옮기면 안 되는 이유(호가 깊이·입출금)와 지난 7일의 김프 사건을 실데이터로 보고, 맨 아래 "어떻게 만들었나" 에서 수집·저장·전달 구조를 본다. 위쪽은 트레이더, 아래쪽은 심사위원·개발자가 읽는다.
검색 엔진·메신저 미리보기가 자바스크립트 없이 본문을 읽도록 페이지는 **정적 HTML** 이고, 실데이터는 이 스펙이 만드는 가벼운 요약 API 하나로만 받는다(지금 `/api/spreads` 는 gzip 170KB 라 랜딩이 매번 받기엔 무겁다).

## 2. 범위
- 만드는 것: server `features/landing/`(`GET /landing`), `web/public/landing.html` 전면 재작성(HTML·CSS·스크립트 한 파일), `web/public/landing/og.png`(1200×630) 신규, `web/public/landing/spreads.png`·`history.png` 새로 캡처, nginx `= /api/landing` 위치, `server/tests/test_deploy.py` 단언 1개.
- 하지 않는 것: 가입·로그인·방문 집계·다국어, 랜딩의 WebSocket, 대시보드(`/app/`) 변경, `robots.txt`·`sitemap.xml`·§3.1 의 기존 경로 규칙 변경, 새 저장 데이터·Influx 쓰기.
- 바꾸는 기존 것: 016 §3.1·018 §3.4 의 "`api` 가 서빙하는 경로" 목록에 `/landing` 을 더한다(두 스펙 문구도 이 PR 에서 고친다). 랜딩은 더 이상 `/api/spreads` 를 부르지 않는다.

## 3. 동작

### 3.1 경로 (nginx) — 기존 규칙에 한 줄 추가
| 요청 | 응답 |
|---|---|
| `/` | `landing.html` |
| `/?…` | `301 /app/?…` |
| `/app` | `301 /app/` |
| `/app/…` | 대시보드 |
| `/api/landing` | `api:8000/landing` |
| 그 밖의 루트 경로 | 정적 파일 또는 404 |

- 기존 규칙(022 이전 판)은 그대로다: 쿼리 붙은 `/` 는 쿼리를 들고 301, SPA fallback 은 `/app/` 아래에서만, `landing.html`·`index.html` no-store, `assets/` 1년 immutable, `/landing/*`·`/robots.txt`·`/sitemap.xml`·`/favicon.svg` 는 정적 파일.
- `/api/landing` 은 `= /api/spreads` 와 같은 모양의 **정확 일치** location — 접두 제거 rewrite·프록시 헤더 4개. 배포에서 api 가 죽으면 이 경로만 502 이고 랜딩 본문은 그대로 뜬다.

### 3.2 `GET /landing` — 요약 API
- 두 역할(`api`·`collector`) 모두 포함한다 — 로컬 단일 프로세스에서도 뜨게(018 의 `/spreads` 와 같은 이유). 배포에선 nginx 가 api 로만 보낸다. 쿼리 파라미터는 없다(와도 무시).
- 응답은 **항상 200**, `Cache-Control: no-store`, camelCase. 세 부분(`live`·`trail`·`events`)은 각자 실패하면 그 부분만 `null` 이다 — 랜딩은 그 구역만 숨긴다.

```json
{"servedAt": 1790509107900,
 "live": {"dataReceivedAt": 1790509107000, "rate": 1360.0, "coins": 399, "pairs": 1458, "over1": 18, "over1Movable": 5,
          "top": [{"sym": "VERONA", "dom": "bithumb", "fx": "bybit", "dir": "reverse", "pct": 2.26, "slip": 0.05,
                   "krw": 12.4, "usd": 0.0089, "netDom": "ERC20", "netFx": "Ethereum"}]},
 "trail": {"sym": "VERONA", "dom": "bithumb", "fx": "bybit", "dir": "reverse", "points": [[1790505540, 2.41], [1790505600, 2.38]]},
 "events": {"start": 1789904307, "stop": 1790509107, "count": 812, "kimp": 120, "reverse": 692, "open": 14,
            "top": [{"sym": "CUDIS", "dom": "bithumb", "fx": "bitget", "dir": "kimp", "maxPercent": 231.4,
                     "startTs": 1790400000, "endTs": 0, "durationSeconds": 0, "lastTs": 1790509000}]}}
```
- `servedAt` 는 이 응답을 만든 시각(epoch ms, 캐시와 무관). `live.dataReceivedAt` 은 표의 값 그대로(ms). 그 밖의 `*Ts`·`start`·`stop` 은 epoch 초.

**live** — Redis 키 `spreads:latest`(017 이 매 틱 쓰는 $1,000 표, TTL 10초)를 읽기만 한다(`spreads:want` 는 쓰지 않는다). 쓰는 표 계약(003·006·018): 최상위 `rate`(업비트 USDT 매도호가, 원)·`dataReceivedAt`, `rows[]` 행마다 `sym`·`dom`·`fx`·`fwd`·`rev`(슬리피지 **차감 후** 순값 %)·`slipFwd`·`slipRev`(차감폭 %p, 원값 = 순값 + 차감폭)·`krw`(국내 최우선 매수호가)·`usd`(해외 마지막 체결가)·`status`(`ok`·`stale`·`fail`)·`depDom`·`wdDom`·`depFx`·`wdFx`(true 열림·false 막힘·null 모름)·`netDom`·`netFx`(망 표시명, null 없음).
- **옮길 수 있는 방향**: 김프 `kimp`(값 `fwd`) 는 `wdFx === true && depDom === true`, 역프 `reverse`(값 `rev`) 는 `wdDom === true && depFx === true`. null 은 열림이 아니다.
- `top`: `status == "ok"` 행의 옮길 수 있는 방향만 후보다. 코인마다 값이 가장 큰 후보 하나를 남기고, 값 내림차순 상위 5개(같으면 `sym` 오름차순). 값이 0 이하여도 들어간다 — 지금 시장이 그렇다는 뜻이므로 숨기지 않는다. `pct` = 그 방향 값, `slip` = 그 방향 차감폭, `krw`·`usd`·`netDom`·`netFx` 는 행 그대로. 후보가 없으면 `[]`.
- `coins` = `rows` 의 `sym` 종류 수, `pairs` = `rows` 길이(둘 다 상태 무관). `over1` = `status == "ok"` 인 (행, 방향) 가운데 값이 1.0 이상인 수, `over1Movable` = 그중 옮길 수 있는 방향인 수. 1.0 은 013 사건 진입 기준과 같다.
- `null` 조건: Redis 불달, 키 없음, 값이 JSON 이 아니거나 `rows` 가 배열이 아님.

**trail** — `top[0]` 경로의 최근 1시간. Influx 버킷 `candles_1m`(014)에서 그 (dom, fx, sym) 봉을 `[지금−3600, 지금)` 로 읽어, 방향이 `kimp` 면 `fwd_c`, `reverse` 면 `rev_c` 를 `[창 시작 ts, 값]` 으로 ts 오름차순(최대 60점). 봉 값은 **슬리피지 차감 전 원값**이라 `top[0].pct` 보다 크다 — 랜딩은 "최우선 호가 기준" 이라고 표시한다. `null` 조건: `live` 가 null, `top` 이 빔, Influx 없음(토큰 없음)·실패, 점이 2개 미만.

**events** — Influx `premium_event`(013)를 `start = 지금 − 604800`, `stop = 지금` 으로 읽는다(사건 시작 시각 기준, 진행 중 포함). `count` 전체, `kimp`·`reverse` 방향별, `open` = `endTs == 0` 인 수. `top` = 코인마다 `maxPercent` 가 가장 큰 사건 하나, `maxPercent` 내림차순 상위 5개 — 필드 `sym`(점의 base)·`dom`·`fx`·`dir`·`maxPercent`·`startTs`·`endTs`(0 = 진행 중)·`durationSeconds`·`lastTs`. 사건 값도 원값이고 입출금 여부와 무관하게 잡힌다. `null` 조건: Influx 없음·실패.

- **캐시**(프로세스 메모리): `live` 5초, `events` 60초, `trail` 60초 — trail 은 경로(dom·fx·sym·dir)가 바뀌면 만료 전이라도 새로 읽는다. 비었거나 만료된 부분에 요청이 몰리면 **한 번만** 갱신하고 나머지는 그 결과를 쓴다. null 결과도 같은 시간만큼 캐시한다 — 장애 중에 요청마다 Redis·Influx 를 두드리지 않게. Influx 호출은 스레드로 넘긴다(동기 클라이언트 — 016 과 같다).
- `features/landing/` 은 다른 기능을 import 하지 않는다. core 의 Redis·Influx 클라이언트만 쓴다(`spreads:latest` 읽기, 봉 조회, 사건 조회).

### 3.3 화면 — 위에서 아래로
전부 한국어, 왼쪽 정렬, 최대 폭 1120px. 거래소 표시명 `upbit→업비트` `bithumb→빗썸` `binance→Binance` `bybit→Bybit` `bitget→Bitget`.

```
KimpTrack                                          [대시보드 열기]
지금 옮길 수 있는 김프를      ┌ 지금 가장 큰 경로 ────────── 3초 전 값 ┐
1초마다 찾습니다             │ VERONA  빗썸에서 사서 Bybit에서 팝니다  │
설명 2문장                   │ +2.26%   역프 · $1,000 체결 기준        │
[대시보드 열기]               │ 빗썸 ●━━━━━━━━━━━━━━━━━━━━━━● Bybit   │
                            │ 출금 가능 · ERC20     입금 가능 · Ethereum│
                            │ ╱╲_╱‾╲_  최근 1시간 · 최우선 호가 기준   │
                            │ 빗썸 ₩12.40            Bybit $0.0089     │
                            └──────────────────────────────────────┘
                             다음 경로   HNT  빗썸 → Bitget  +1.24%  (4줄)
```
1. **상단 바** — 워드마크 `KimpTrack`, 오른쪽 버튼 "대시보드 열기"(→ `/app/`).
2. **히어로** — h1 "지금 옮길 수 있는 김프를 1초마다 찾습니다". 설명 "업비트·빗썸과 바이낸스·바이빗·비트겟의 호가를 매초 맞대어 김프와 역프를 계산합니다. $1,000를 실제로 사고팔 때의 값과, 지금 입출금이 열려 있는지까지 함께 봅니다." 버튼 "대시보드 열기". 오른쪽(모바일은 아래)에 **경로 카드**:
   - 머리줄 "지금 가장 큰 경로" 와 값의 나이 "N초 전 값"(§3.5).
   - 코인 심볼(크게)과 경로 문장 — `kimp` 는 "{fx}에서 사서 {dom}에서 팝니다", `reverse` 는 "{dom}에서 사서 {fx}에서 팝니다".
   - 값 `pct`(부호·소수 2자리 — 페이지에서 가장 큰 글자)와 "김프"/"역프" · "$1,000 체결 기준".
   - **경로 선** — 출발 거래소 ●━● 도착 거래소. 출발 아래 "출금 가능 · {망}", 도착 아래 "입금 가능 · {망}". `kimp`: 출발 = fx·`netFx`, 도착 = dom·`netDom`. `reverse`: 출발 = dom·`netDom`, 도착 = fx·`netFx`. 망이 null 이면 " · {망}" 만 뺀다.
   - 추이 — `trail` 을 인라인 SVG 선 하나로(축·눈금 없음, 0% 기준선 하나, 끝점 표시). 라벨 "최근 1시간 · 최우선 호가 기준". trail 이 null 이면 이 줄을 숨긴다.
   - 가격 두 칸 — "{국내 거래소} ₩{krw}", "{해외 거래소} ${usd}". 수식처럼 잇지 않는다: `krw` 는 최우선 매수호가, `usd` 는 마지막 체결가라 둘을 곱해도 `pct` 가 나오지 않는다.
   - 카드 아래 **다음 경로** — `top[1..4]` 를 한 줄씩: 심볼, "{출발} → {도착}", 값. 없으면 줄째 숨긴다.
   - 카드와 다음 경로의 각 경로는 링크 `/app/?tab=history&sym={sym}&h.dir={dir}&h.dom={dom}&h.fx={fx}`(기록 탭이 그 코인·방향·거래소로 열린다).
3. **숫자만 보고 옮기면 안 되는 이유**(h2) — 두 단의 글이고 카드로 감싸지 않는다. 옆(모바일은 아래)에 `landing/spreads.png`.
   - "호가창을 걷은 값" — "{top[0].sym}의 최우선 호가 차이는 {pct + slip}%지만, $1,000를 실제로 사고팔면 {pct}%입니다." + "표의 모든 숫자는 호가창을 걷어 낸 평균 체결가로 계산하고, 원화와 USDT 사이도 은행 환율이 아니라 국내 거래소의 USDT 호가(지금 업비트 ₩{rate 정수})로 바꿉니다." `top` 이 비면 첫 문장만 뺀다.
   - "입출금이 열린 길" — "지금 1% 넘게 벌어진 김프·역프 {over1}건 가운데 실제로 옮길 수 있는 것은 {over1Movable}건입니다." + "거래소마다 입금·출금을 망 단위로 확인하고, 한쪽이라도 막혔거나 망이 맞지 않으면 옮길 수 없는 경로로 봅니다." `over1` 이 0 이면 첫 문장 대신 "지금은 1% 넘게 벌어진 곳이 없습니다."
4. **지난 7일, 벌어졌던 순간들**(h2) — "1% 넘게 벌어져 1분 넘게 이어진 순간을 사건으로 남깁니다. 지난 7일 {count}건(김프 {kimp}건, 역프 {reverse}건), 지금 진행 중 {open}건." 그 아래 `events.top` 표: 코인 / 경로 "{dom} · {fx}" / 방향 / 최고 "+{maxPercent}%" / 지속(닫힌 사건은 `durationSeconds`, 진행 중이면 "진행 중 {lastTs − startTs}") / 시작(KST, "9월 25일 14:03"). 행은 2번의 기록 탭 링크(`h.dir` 는 사건 방향). 표 아래 작은 글 "사건 값은 최우선 호가 기준이며, 입출금이 막힌 경로도 들어갑니다." 옆에 `landing/history.png`. `events` 가 null 이면 첫 문장의 둘째 문장부터와 표를 숨긴다.
5. **어떻게 만들었나**(h2) — 네 단계를 가로로(모바일은 세로). 실제 순서이므로 번호를 붙인다.
   1. 수집 — "5개 거래소를 WebSocket 으로 받습니다(업비트·빗썸 호가와 현재가, 바이낸스 depth20, 바이빗 orderbook.200, 비트겟 books15). 마켓 목록은 매초, 입출금은 60초마다 확인합니다."
   2. 계산 — "매초 전 페어의 호가창을 $1,000 만큼 걷어 김프·역프 표 한 장을 만들고 Redis 로 넘깁니다."
   3. 기록 — "1초 원값은 InfluxDB 에, 사건(1.0% 진입·0.5% 이탈)과 1분 봉(5분·1시간·4시간·1일로 접음)은 따로 남기고, 거래소 원문은 S3 에 보관합니다."
   4. 전달 — "조회 서버가 표에서 바뀐 행만 모아 한 번만 압축하고, 접속자 모두에게 같은 바이트를 WebSocket 으로 보냅니다."
   그 아래 사실 네 줄: "서버 3대 — 수집 c7g.medium, 데이터 t4g.small, 조회 t4g.micro (2026-09-25부터)", "WebSocket 동시 접속 400명에서 조회 서버 CPU 13~20% (2026-09-26 측정)", "2026년 5월부터 쌓인 김프 기록", "HTTPS 인증서 자동 발급·갱신(Let's Encrypt)". 근거는 `docs/context/status.md`·`docs/runbooks/ws-loadtest.md` — 이 두 문서에 없는 수치는 만들지 않는다.
6. **알아둘 점**(h2) — 세 문장: "KimpTrack은 가격 차이를 관측하는 도구입니다. 주문을 내지 않고, 투자를 권하지 않습니다." / "가격과 입출금 상태는 거래소 공개 API 기준이라 실제와 어긋날 수 있습니다. 옮기기 전에 거래소에서 직접 확인하세요." / "출금 수수료, 전송 시간, 세금은 계산에 들어 있지 않습니다."
7. **바닥** — "소프트웨어 마에스트로 17기 프로젝트" 와 "대시보드 열기" 링크.

### 3.4 시각 규칙
- 색은 `docs/design/theme.css` 토큰 값을 복사해 쓴다(번들 밖 파일이라 import 불가): 배경·표면·글자·회색 단계·보라 강조·상승 빨강·하락 파랑. 부호 색은 대시보드와 같다 — 양수 빨강, 음수 파랑, 0 회색. 보라는 워드마크·버튼·경로 선·포커스에만 쓴다.
- 글꼴은 Pretendard Variable 하나(jsDelivr 의 dynamic subset CSS — 한글 글리프를 쓰는 조각만 받는다). 라틴 글리프가 Inter 기반이라 대시보드(Inter)의 숫자와 모양이 같다. 모든 숫자는 `tabular-nums`. 크기: h1 52px(모바일 34), 경로 카드 값 80px(모바일 52), h2 30px(모바일 24), 본문 17px·줄간 1.7, 작은 글 13px. 본문 한 줄은 34em 이하.
- 떠 있는 면은 **경로 카드 하나**다(표면색 + 큰 그림자). 나머지 섹션은 카드로 감싸지 않고 여백과 필요한 자리의 가는 구분선으로 나눈다. 스크린샷은 가는 테두리와 둥근 모서리만.
- 쓰지 않는 것: 대문자 라벨, 버튼 글자 끝의 화살표 문자, 섹션마다 나타나는 등장 애니메이션, 장식용 그라데이션.
- 움직임은 하나 — 폴링으로 경로 카드 값이 바뀌면 값 글자가 0.6초 동안 강조됐다 돌아온다. `prefers-reduced-motion` 이면 없다.
- 키보드 포커스는 보라 외곽선으로 보인다. 그림의 alt 는 무엇을 보여 주는지 한 문장.

### 3.5 스크립트 규칙
- 로드 직후 `GET /api/landing` 1회, 이후 **페이지가 보이는 동안만** 10초마다. 숨으면(`visibilitychange`) 멈추고, 다시 보이면 즉시 1회 부른 뒤 재개한다. WebSocket·`/api/spreads`·`/api/health*` 는 부르지 않는다.
- 값의 나이 = `(servedAt − live.dataReceivedAt) / 1000` + 응답을 받은 뒤 흐른 초. 1초마다 글자만 고친다. 60초를 넘기면 카드를 흐리게 하고 나이 자리에 "최신 값을 받지 못하고 있습니다".
- 첫 요청이 실패(네트워크·200 아님·JSON 아님)하거나 `live` 가 null 이면 카드 자리에 "실시간 값을 불러오지 못했습니다" 한 줄과 대시보드 버튼만 둔다. 그 뒤의 실패는 직전 값을 그대로 두고 나이만 는다. 지어낸 숫자·자리표시 숫자는 넣지 않는다.
- `top` 이 빈 배열이면 카드 안에 "지금 옮길 수 있는 경로가 없습니다" 만 두고 다음 경로를 숨긴다. 부분 null 은 §3.3 규칙대로 그 구역만 숨긴다.
- 자릿수는 대시보드 `web/src/shared/format.ts` 의 `fmtKrw`·`fmtUsdt`·`fmtPct` 규칙을 옮겨 쓴다.
- 자바스크립트가 꺼져도 제목·설명·섹션 글·면책·그림은 HTML 에 있다. 실데이터 자리는 `<noscript>` 한 줄 "실시간 값은 자바스크립트를 켜면 보입니다".

### 3.6 검색·미리보기·그림
- `<title>` "KimpTrack — 옮길 수 있는 김프를 1초마다", description = 히어로 설명 2문장, `lang="ko"`, canonical·`og:url` `https://kimptrack.com/`, `og:image` `https://kimptrack.com/landing/og.png`, `og:locale ko_KR`, `twitter:card summary_large_image`.
- `og.png`(1200×630): 어두운 배경에 워드마크, h1 문장, 5개 거래소 이름. 숫자는 넣지 않는다 — 미리보기는 오래 남으므로 낡은 값이 박히면 안 된다.
- `spreads.png`·`history.png`: `https://kimptrack.com/app/` 실화면을 1360×820 @1.5x 로 새로 찍는다(스프레드 탭 기본 화면, 기록 탭 `?tab=history&sym=BTC`).

### 3.7 반응형
- 960px 이상: 히어로 두 단(글 5 : 카드 7), 3·4번은 글 : 그림 두 단, 5번은 네 칸 가로. 미만: 한 단(글 → 카드 → 다음 경로).
- 390px 폭에서 페이지 가로 스크롤이 없다. 사건 표는 720px 미만에서 행마다 두 줄(코인·최고 / 경로·방향·지속·시작).
- 대시보드는 여전히 데스크톱 전용이다(product.md). 랜딩만 모바일에서 읽힌다.

## 4. 검증
server — `features/landing/tests/`(Redis·Influx 는 fake):
- 옮길 수 있는 방향만 후보다 — `wdFx`·`depDom` 이 true 일 때만 kimp, `wdDom`·`depFx` 가 true 일 때만 reverse, null 은 열림이 아니다
- `stale`·`fail` 행은 `top`·`over1` 에서 빠지고, `coins`·`pairs` 는 상태와 무관하다
- 코인당 1개(방향·거래소 중 값이 큰 것), 값 내림차순 5개, 동률은 `sym` 오름차순, 0 이하 값도 들어간다
- `over1` 경계 — 값 1.0 은 포함된다
- trail — kimp 는 `fwd_c`·reverse 는 `rev_c`, 버킷 `candles_1m`·창 1시간, ts 오름차순, 점이 2개 미만이면 null
- events — 방향별·진행 중 수, 코인당 1개로 `maxPercent` 내림차순 5개
- Redis 불달·키 없음 → `live`·`trail` null, `events` 정상 / Influx 없음·실패 → `events`·`trail` null, `live` 정상 / 둘 다 → 200 에 셋 다 null
- 캐시 — 5초 안의 두 번째 요청은 Redis 를 다시 읽지 않는다, 60초 안에는 사건 조회를 다시 하지 않는다, 동시 요청 10개에 Redis 읽기는 1번, 경로가 바뀌면 trail 을 새로 읽는다
- 200·camelCase·`Cache-Control: no-store`, 두 역할 모두 `/landing` 이 있다
- `server/tests/test_deploy.py` — nginx 에 `location = /api/landing` 이 api 로 간다

수동:
- 로컬 5컨테이너(dev-setup.md)에서 `curl /api/landing` 의 `top[0]` 이 같은 순간 `/api/spreads` 에 §3.2 규칙을 적용해 고른 행과 같다
- 브라우저 1440px·390px: 경로 카드·다음 경로·3번 두 문장·사건 표가 실데이터로 찬다 / 가로 스크롤 없음 / 탭을 숨기면 요청이 멈추고 다시 보이면 즉시 1회 / api 를 멈추면(502) §3.5 대로 / 자바스크립트를 끄면 본문이 읽힌다 / 네트워크 목록에 `/api/spreads`·`/api/ws/` 가 없다 / 경로 링크가 기록 탭의 그 코인·방향·거래소로 연다
- `cd web && npm run lint && npm run build`, `cd server && ruff check . && pytest -q` 통과

## 5. 완료 기준 (실행 세션이 채움)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — `landing` 행을 `| landing | server: GET /landing 요약(live 5초·trail·events 60초 캐시, 두 역할) | web: 정적 landing.html — 경로 카드·다음 경로·호가/입출금 두 단·7일 사건 표·어떻게 만들었나, 보이는 동안 10초 폴링 | 스크린샷·og.png·"어떻게 만들었나" 수치는 사람이 갱신 |` 로. 알려진 빚에 "(022) 랜딩의 '어떻게 만들었나' 수치와 스크린샷은 정적이다 — 인프라·부하 수치가 바뀌면 사람이 고친다" 추가.
- `CLAUDE.md` — 스펙 인덱스 022 행: 범위를 "정적 HTML 랜딩 `/` — 실시간 경로 카드·7일 사건·만든 방식, 요약 API `GET /landing`(api), 대시보드는 `/app/`" 로, 상태 DONE.
- `docs/context/architecture.md` — web 항목의 랜딩 문장(요약 API 를 보이는 동안 10초 폴링), 배포 절 serve 박스의 api 서빙 경로와 nginx 설명에 `= /api/landing → api`, "현재 구조" 절에 landing 항목(`features/landing/` — 부분별 캐시와 한 번만 갱신, core 의 Redis·Influx 읽기만 쓴다).
- `docs/context/product.md` — 기능 목록 표에 `landing` 행 "처음 온 사람용 소개 페이지(정적 HTML) + 요약 API".
- `docs/context/dev-setup.md` — web 절에 dev 서버에서 랜딩을 여는 주소(실행 세션이 확인한 것)와 `curl localhost:8000/landing` 스모크 한 줄.
- `docs/specs/016-process-split.md` §3.1, `docs/specs/018-spreads-serve.md` §3.4 — api 가 서빙하는 경로 목록에 `/landing`.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
- 남은 빚:
