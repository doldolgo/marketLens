# 022 — landing

상태: DONE | 의존: 003 spreads(행 계약), 006 wallet-status(5필드), 013 premium-events(`premium_event`), 014 premium-1m(`candles_1m`), 016 process-split(`api` 역할), 017·018(`spreads:latest`), 007 deploy(nginx·web 이미지), 023 domain-tls(절대 주소)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
처음 온 사람이 `https://kimptrack.com/` 을 열면 **지금 실제로 옮길 수 있는 김프 하나**를 실데이터로 먼저 본다 — 어느 거래소에서 사서 어디서 파는지, $1,000 체결 기준 값, 출금·입금이 열린 망, 최근 1시간 추이. 그 아래에서 표시된 김프와 먹을 수 있는 김프가 왜 다른지(호가 깊이·입출금)와 지난 7일의 김프 사건을 실데이터로 보고, 맨 아래 "어떻게 만들었나" 에서 수집·저장·전달 구조를 본다. 위쪽은 트레이더, 아래쪽은 심사위원·개발자가 읽는다.
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
          "depthGap": {"sym": "HFT", "dom": "bithumb", "fx": "bybit", "dir": "reverse", "raw": 1.27, "pct": -0.04, "slip": 1.31},
          "top": [{"sym": "VERONA", "dom": "bithumb", "fx": "bybit", "dir": "reverse", "pct": 2.26, "slip": 0.05,
                   "krw": 12.4, "usd": 0.0089, "netDom": "ERC20", "netFx": "Ethereum"}]},
 "trail": {"sym": "VERONA", "dom": "bithumb", "fx": "bybit", "dir": "reverse", "points": [[1790505540, 2.41], [1790505600, 2.38]]},
 "events": {"start": 1789904307, "stop": 1790509107, "count": 58601, "kimp": 4058, "reverse": 54543, "open": 248,
            "top": [{"sym": "CPOOL", "dom": "bithumb", "fx": "bitget", "dir": "reverse", "maxPercent": 1.46,
                     "startTs": 1790507247, "endTs": 1790508987, "durationSeconds": 1740, "lastTs": 1790508986}]}}
```
- `servedAt` 는 이 응답을 만든 시각(epoch ms, 캐시와 무관). `live.dataReceivedAt` 은 표의 값 그대로(ms). 그 밖의 `*Ts`·`start`·`stop` 은 epoch 초.

**live** — Redis 키 `spreads:latest`(017 이 매 틱 쓰는 $1,000 표, TTL 10초)를 읽기만 한다(`spreads:want` 는 쓰지 않는다). 쓰는 표 계약(003·006·018): 최상위 `rate`(업비트 USDT 매도호가, 원)·`dataReceivedAt`, `rows[]` 행마다 `sym`·`dom`·`fx`·`fwd`·`rev`(슬리피지 **차감 후** 순값 %)·`slipFwd`·`slipRev`(차감폭 %p, 원값 = 순값 + 차감폭)·`krw`(국내 최우선 매수호가)·`usd`(해외 마지막 체결가)·`status`(`ok`·`stale`·`fail`)·`depDom`·`wdDom`·`depFx`·`wdFx`(true 열림·false 막힘·null 모름)·`netDom`·`netFx`(망 표시명, null 없음).
- **옮길 수 있는 방향**: 김프 `kimp`(값 `fwd`) 는 `wdFx === true && depDom === true`, 역프 `reverse`(값 `rev`) 는 `wdDom === true && depFx === true`. null 은 열림이 아니다.
- `top`: `status == "ok"` 행의 옮길 수 있는 방향만 후보다. 코인마다 값이 가장 큰 후보 하나를 남기고, 값 내림차순 상위 5개(같으면 `sym` 오름차순). 값이 0 이하여도 들어간다 — 지금 시장이 그렇다는 뜻이므로 숨기지 않는다. `pct` = 그 방향 값, `slip` = 그 방향 차감폭, `krw`·`usd`·`netDom`·`netFx` 는 행 그대로. 후보가 없으면 `[]`.
- `coins` = `rows` 의 `sym` 종류 수, `pairs` = `rows` 길이(둘 다 상태 무관). `over1` = `status == "ok"` 인 (행, 방향) 가운데 값이 1.0 이상인 수, `over1Movable` = 그중 옮길 수 있는 방향인 수. 1.0 은 013 사건 진입 기준과 같다.
- `depthGap`: 호가 깊이 예시 하나. `status == "ok"` 행의 옮길 수 있는 방향 가운데 원값(값 + 차감폭)이 1.0 이상이고 차감폭이 0.1%p 이상인 것 중 차감폭이 가장 큰 하나(같으면 `sym` 오름차순). 필드 `sym`·`dom`·`fx`·`dir`·`raw`(원값)·`pct`(순값)·`slip`(차감폭). 후보가 없으면 null. `top[0]` 을 예시로 쓰지 않는 이유: 1위 경로는 대개 호가가 두꺼워 원값과 순값이 0.01~0.1%p 밖에 차이 나지 않는다 — 코인 이름만 바뀌고 요점이 안 보였다(2026-09-28 실측 1위 +1.69→+1.63%, 이 규칙으로 고른 HFT 는 +1.27→−0.04%).
- `null` 조건: Redis 불달, 키 없음, 값이 JSON 이 아니거나 `rows` 가 배열이 아님.

**trail** — `top[0]` 경로의 최근 1시간. Influx 버킷 `candles_1m`(014)에서 그 (dom, fx, sym) 봉을 `[지금−3600, 지금)` 로 읽어, 방향이 `kimp` 면 `fwd_c`, `reverse` 면 `rev_c` 를 `[창 시작 ts, 값]` 으로 ts 오름차순(최대 60점). 봉 값은 **슬리피지 차감 전 원값**이라 `top[0].pct` 보다 크다 — 랜딩은 "최우선 호가 기준" 이라고 표시한다. `null` 조건: `live` 가 null, `top` 이 빔, Influx 없음(토큰 없음)·실패, 점이 2개 미만.

**events** — Influx `premium_event`(013)를 `start = 지금 − 604800`, `stop = 지금` 으로 읽는다(사건 시작 시각 기준, 진행 중 포함). `count` 전체, `kimp`·`reverse` 방향별, `open` = `endTs == 0` 인 수. `top` = **닫힌 사건**(`endTs > 0`)만, 코인마다 가장 늦게 끝난 사건 하나, `endTs` 내림차순 상위 5개(같으면 `sym` 오름차순) — 필드 `sym`(점의 base)·`dom`·`fx`·`dir`·`maxPercent`·`startTs`·`endTs`·`durationSeconds`·`lastTs`. 최고값 순으로 고르지 않는 이유: 7일 최고값 자리는 입출금이 막혔거나 이름만 같은 다른 코인의 수백 % 값이 차지한다(2026-09-27 실측 코인별 최고 652%·415%·275%, 스테이블코인 106%). 계속 기록하고 있다는 것을 현실적인 값으로 보여 주려고 최근에 끝난 순으로 고른다. 사건 값도 원값이고 입출금 여부와 무관하게 잡힌다. `null` 조건: Influx 없음·실패.

- **캐시**(프로세스 메모리): `live` 5초, `events` 60초, `trail` 60초 — trail 은 경로(dom·fx·sym·dir)가 바뀌면 만료 전이라도 새로 읽는다. 비었거나 만료된 부분에 요청이 몰리면 **한 번만** 갱신하고 나머지는 그 결과를 쓴다. null 결과도 같은 시간만큼 캐시한다 — 장애 중에 요청마다 Redis·Influx 를 두드리지 않게. Influx 호출은 스레드로 넘긴다(동기 클라이언트 — 016 과 같다).
- **Influx 는 3초까지만 기다린다.** `trail`·`events` 는 갱신이 3초 안에 끝나지 않으면 이번 응답에 그 부분의 직전 값(만료됐어도 그대로, 한 번도 채운 적 없으면 null)을 싣는다. `trail` 의 직전 값은 **같은 경로**의 것만이다 — 1위 경로가 막 바뀌었다면 null(다른 코인의 추이를 새 경로 카드에 싣지 않는다). 조회는 뒤에서 끝까지 돌고, 끝나는 순간 그 결과(실패면 null)로 캐시를 채워 그때부터 TTL 을 센다. 조회가 도는 동안 온 요청은 새 조회를 시작하지 않고 같은 조회를 같은 3초 규칙으로 기다린다. 그래서 Influx 가 느리거나 매달려도 응답은 Redis 읽기 + 3초 안에 오고, 경로 카드는 Influx 때문에 늦지 않는다(Influx 클라이언트 자체 타임아웃은 60초라 이 규칙이 없으면 응답 전체가 그만큼 늦는다).
- `features/landing/` 은 다른 기능을 import 하지 않는다. core 의 Redis·Influx 클라이언트만 쓴다(`spreads:latest` 읽기, 봉 조회, 사건 조회).

### 3.3 화면 — 위에서 아래로
전부 한국어, 왼쪽 정렬, 최대 폭 1120px. 거래소 표시명 `upbit→업비트` `bithumb→빗썸` `binance→Binance` `bybit→Bybit` `bitget→Bitget`.

```
KimpTrack                                     [서비스로 넘어가기]
김프가 우리에게              ┌ 지금 가장 큰 경로 ────────── 3초 전 값 ┐
이득을 주는 김프가 아니다    │ VERONA  빗썸에서 사서 Bybit에서 팝니다  │
설명 4문장                   │ +2.26%   역프 · $1,000 체결 기준        │
[서비스로 넘어가기]            │ 빗썸 ●━━━━━━━━━━━━━━━━━━━━━━● Bybit   │
                            │ 출금 가능 · ERC20     입금 가능 · Ethereum│
                            │ ╱╲_╱‾╲_  최근 1시간 · 최우선 호가 기준   │
                            │ 빗썸 ₩12.40            Bybit $0.0089     │
                            └──────────────────────────────────────┘
                             다음 경로   HNT  빗썸 → Bitget  +1.24%  (4줄)
```
1. **상단 바** — 워드마크 `KimpTrack`, 오른쪽 버튼 "서비스로 넘어가기"(→ `/app/`). 이 페이지의 `/app/` 로 가는 버튼·링크 글자는 전부 "서비스로 넘어가기" 하나다.
2. **히어로** — 시스템이 어떻게 도는지가 아니라 서비스가 트레이더에게 무엇인지를 말한다(2026-09-28 사람이 직접 쓴 문구). h1 "김프가 우리에게 이득을 주는 김프가 아니다" — 화면에 뜬 김프가 곧 먹을 수 있는 김프는 아니라는 말이고, 오른쪽 경로 카드의 "$1,000 체결 기준" 값과 "출금 가능 · 입금 가능" 이 그 답이다. h1 은 어느 폭에서나 "김프가 우리에게" / "이득을 주는 김프가 아니다" 두 줄로 끊긴다(og.png 와 같다). 긴 둘째 줄이 한 줄에 들어가도록 글자 크기를 폭에 맞춘다(§3.4). 설명 "기존 김프는 여러 사이트에서 수치를 일일이 확인해야 됩니다. 그런데 확인한 숫자마저도 실제로는 이득이 있는 김프가 아닐 수 있고, 이득이 있더라도 거래를 할 수 없는 경우가 존재합니다. KimpTrack은 출금·입금이 열렸는지 확인하고, 진짜 김프인지 판단해 사용자에게 실질적 이익을 가져다주고자 합니다." — 글자 그대로 쓴다. 버튼 "서비스로 넘어가기". 오른쪽(모바일은 아래)에 **경로 카드**:
   - 머리줄 "지금 가장 큰 경로" 와 값의 나이 "N초 전 값"(§3.5).
   - 코인 심볼(크게)과 경로 문장 — `kimp` 는 "{fx}에서 사서 {dom}에서 팝니다", `reverse` 는 "{dom}에서 사서 {fx}에서 팝니다".
   - 값 `pct`(부호·소수 2자리 — 페이지에서 가장 큰 글자)와 "김프"/"역프" · "$1,000 체결 기준".
   - **경로 선** — 출발 거래소 ●━● 도착 거래소. 출발 아래 "출금 가능 · {망}", 도착 아래 "입금 가능 · {망}". `kimp`: 출발 = fx·`netFx`, 도착 = dom·`netDom`. `reverse`: 출발 = dom·`netDom`, 도착 = fx·`netFx`. 망이 null 이면 " · {망}" 만 뺀다.
   - 추이 — `trail` 을 인라인 SVG 선 하나로(축·눈금 없음, 0% 기준선 하나, 끝점 표시). 라벨 "최근 1시간 · 최우선 호가 기준". trail 이 null 이면 이 줄을 숨긴다.
   - 가격 두 칸 — "{국내 거래소} ₩{krw}", "{해외 거래소} ${usd}". 수식처럼 잇지 않는다: `krw` 는 최우선 매수호가, `usd` 는 마지막 체결가라 둘을 곱해도 `pct` 가 나오지 않는다.
   - 카드 아래 **다음 경로** — `top[1..4]` 를 한 줄씩: 심볼, "{출발} → {도착}", 값. 없으면 줄째 숨긴다.
   - 카드와 다음 경로의 각 경로는 링크 `/app/?tab=history&sym={sym}&h.dir={dir}&h.dom={dom}&h.fx={fx}`(기록 탭이 그 코인·방향·거래소로 열린다).
3. **표시된 김프와 먹을 수 있는 김프는 다릅니다**(h2) — 중간 발표(2026-08)에서 쓴 말 그대로의 틀이다. 두 단의 글이고 카드로 감싸지 않는다. 옆(모바일은 아래)에 `landing/spreads.png`.
   - 소제목 "호가창 깊이" — "흔히 보는 김프는 가격 하나로 계산한 값입니다. 실제로 주문을 넣으면 호가창을 파고들면서 평균 단가가 밀리고, 주문이 클수록 그 차이도 커집니다." + (`depthGap` 이 있으면) "지금 {sym}의 {출발} → {도착} 경로는 맨 위 호가로 보면 {raw}%지만, $1,000어치를 실제로 사고팔면 {pct}%입니다." + "원화와 USDT 환산도 은행 환율 대신 국내 거래소의 실제 USDT 호가로 합니다(지금 업비트 ₩{rate 정수})." 출발·도착은 경로 카드와 같은 규칙(kimp 는 fx → dom, reverse 는 dom → fx), 두 값은 부호·소수 2자리.
   - 소제목 "입출금 상태" — "김프가 아무리 커도 출금이나 입금이 막혀 있으면 코인을 옮길 수 없습니다." + "지금 1% 넘게 벌어진 김프·역프 {over1}건 가운데 실제로 옮길 수 있는 건 {over1Movable}건입니다." + "거래소마다 입출금을 네트워크 단위로 확인해서, 한쪽이라도 막혔거나 네트워크가 맞지 않으면 옮길 수 없는 경로로 표시합니다." `over1` 이 0 이면 가운데 문장 대신 "지금은 1% 넘게 벌어진 곳이 없습니다."
   - 실데이터가 든 문장(`depthGap` 문장·USDT 괄호·`over1` 문장)만 `live` 에 따라 숨고, 나머지 문장은 HTML 에 그대로 있다.
4. **지난 7일, 벌어졌던 순간들**(h2) — "1% 넘게 벌어져 1분 넘게 이어진 순간을 사건으로 남깁니다. 지난 7일 {count}건(김프 {kimp}건, 역프 {reverse}건), 지금 진행 중 {open}건." 그 아래 소제목 "최근에 끝난 사건" 과 `events.top` 표: 코인 / 경로 "{dom} · {fx}" / 방향 / 최고 "+{maxPercent}%" / 지속(`durationSeconds`) / 끝난 시각(`endTs`, KST, "9월 25일 14:03"). 행은 2번의 기록 탭 링크(`h.dir` 는 사건 방향). 표 아래에는 아무 글도 두지 않는다. 옆에 `landing/history.png`. `events` 가 null 이면 첫 문장의 둘째 문장부터와 표를 숨긴다.
5. **어떻게 만들었나**(h2) — 네 단계를 가로로(모바일은 세로). 실제 순서이므로 번호를 붙인다.
   1. 수집 — "업비트·빗썸과 해외 거래소 3곳의 호가를 실시간으로 받습니다. 입출금이 열려 있는지도 수시로 확인합니다."
   2. 계산 — "매초 모든 코인의 김프·역프를 다시 계산합니다. 맨 위 호가 하나가 아니라 실제로 사고팔 때의 단가를 씁니다."
   3. 기록 — "계산한 값은 빠짐없이 저장합니다. 크게 벌어졌던 순간은 따로 모아 두어 나중에 다시 볼 수 있습니다."
   4. 전달 — "바뀐 값만 골라 보고 계신 화면에 곧바로 보냅니다."
   기술 이름(WebSocket·Redis·InfluxDB·S3 등)과 세부 수치는 쓰지 않는다 — 무엇을 하는지만 말한다(2026-09-28 사람 결정). 네 단계 아래에는 서버 사양·부하 측정값 같은 사실 목록을 두지 않는다.
6. **바닥** — "서비스로 넘어가기" 링크 하나(오른쪽 정렬). 소속 문구와 면책 섹션은 두지 않는다(2026-09-28 사람 결정).

### 3.4 시각 규칙
- 색은 `docs/design/theme.css` 토큰 값을 복사해 쓴다(번들 밖 파일이라 import 불가): 배경·표면·글자·회색 단계·보라 강조·상승 빨강·하락 파랑. 부호 색은 대시보드와 같다 — 양수 빨강, 음수 파랑, 0 회색. 보라는 워드마크·버튼·경로 선·포커스에만 쓴다.
- 글꼴은 Pretendard Variable 하나(jsDelivr 의 dynamic subset CSS — 한글 글리프를 쓰는 조각만 받는다). 라틴 글리프가 Inter 기반이라 대시보드(Inter)의 숫자와 모양이 같다. 모든 숫자는 `tabular-nums`. 크기: h1 은 긴 줄("이득을 주는 김프가 아니다")이 그 폭에 들어가는 크기로 최대 48px, 경로 카드 값 80px(모바일 52), h2 30px(모바일 24), 본문 17px·줄간 1.7, 작은 글 13px. 본문 한 줄은 34em 이하.
- 떠 있는 면은 **경로 카드 하나**다(표면색 + 큰 그림자). 나머지 섹션은 카드로 감싸지 않고 여백과 필요한 자리의 가는 구분선으로 나눈다. 스크린샷은 가는 테두리와 둥근 모서리만.
- 쓰지 않는 것: 대문자 라벨, 버튼 글자 끝의 화살표 문자, 섹션마다 나타나는 등장 애니메이션, 장식용 그라데이션.
- 움직임은 하나 — 폴링으로 경로 카드 값이 바뀌면 값 글자가 0.6초 동안 강조됐다 돌아온다. `prefers-reduced-motion` 이면 없다.
- 키보드 포커스는 보라 외곽선으로 보인다. 그림의 alt 는 무엇을 보여 주는지 한 문장.

### 3.5 스크립트 규칙
- 로드 직후 `GET /api/landing` 1회, 이후 **페이지가 보이는 동안만** 10초마다. 숨으면(`visibilitychange`) 멈추고, 다시 보이면 즉시 1회 부른 뒤 재개한다. WebSocket·`/api/spreads`·`/api/health*` 는 부르지 않는다.
- 값의 나이 = `(servedAt − live.dataReceivedAt) / 1000` + 응답을 받은 뒤 흐른 초. 1초마다 글자만 고친다. 60초를 넘기면 카드를 흐리게 하고 나이 자리에 "최신 값을 받지 못하고 있습니다".
- 첫 요청이 실패(네트워크·200 아님·JSON 아님)하거나 `live` 가 null 이면 카드 자리에 "실시간 값을 불러오지 못했습니다" 한 줄과 "서비스로 넘어가기" 버튼만 둔다. 그 뒤의 실패는 직전 값을 그대로 두고 나이만 는다. 지어낸 숫자·자리표시 숫자는 넣지 않는다.
- `top` 이 빈 배열이면 카드 안에 "지금 옮길 수 있는 경로가 없습니다" 만 두고 다음 경로를 숨긴다. 부분 null 은 §3.3 규칙대로 그 구역만 숨긴다.
- 자릿수는 대시보드 `web/src/shared/format.ts` 의 `fmtKrw`·`fmtUsdt`·`fmtPct` 규칙을 옮겨 쓴다.
- 자바스크립트가 꺼져도 제목·설명·섹션 글·그림은 HTML 에 있다. 실데이터 자리는 `<noscript>` 한 줄 "실시간 값은 자바스크립트를 켜면 보입니다".

### 3.6 검색·미리보기·그림
- `<title>`·`og:title` "KimpTrack — 김프가 우리에게 이득을 주는 김프가 아니다", description·`og:description` = 히어로 설명 전체, `lang="ko"`, canonical·`og:url` `https://kimptrack.com/`, `og:image` `https://kimptrack.com/landing/og.png`, `og:locale ko_KR`, `twitter:card summary_large_image`.
- `og.png`(1200×630): 어두운 배경에 워드마크, h1 문장, 5개 거래소 이름. 숫자는 넣지 않는다 — 미리보기는 오래 남으므로 낡은 값이 박히면 안 된다.
- `spreads.png`·`history.png`: `https://kimptrack.com/app/` 실화면을 1360×820 @1.5x 로 새로 찍는다(스프레드 탭 기본 화면, 기록 탭 `?tab=history&sym=BTC`).

### 3.7 반응형
- 960px 이상: 히어로 두 단(글 5 : 카드 7), 3·4번은 글 : 그림 두 단, 5번은 네 칸 가로. 미만: 한 단(글 → 카드 → 다음 경로).
- 390px 폭에서 페이지 가로 스크롤이 없다. 사건 표는 720px 미만에서 행마다 두 줄(코인·최고 / 경로·방향·지속·끝난 시각).
- 대시보드는 여전히 데스크톱 전용이다(product.md). 랜딩만 모바일에서 읽힌다.

## 4. 검증
server — `features/landing/tests/`(Redis·Influx 는 fake):
- 옮길 수 있는 방향만 후보다 — `wdFx`·`depDom` 이 true 일 때만 kimp, `wdDom`·`depFx` 가 true 일 때만 reverse, null 은 열림이 아니다
- `stale`·`fail` 행은 `top`·`over1` 에서 빠지고, `coins`·`pairs` 는 상태와 무관하다
- 코인당 1개(방향·거래소 중 값이 큰 것), 값 내림차순 5개, 동률은 `sym` 오름차순, 0 이하 값도 들어간다
- `over1` 경계 — 값 1.0 은 포함된다
- `depthGap` — 옮길 수 있는 방향만, 원값 1.0 이상·차감폭 0.1 이상 가운데 차감폭 최대(동률 `sym` 오름차순), `raw = pct + slip`, 경계값(원값 1.0·차감폭 0.1)은 포함, 옮길 수 없는 방향의 더 큰 차감폭은 무시, 후보가 없으면 null
- trail — kimp 는 `fwd_c`·reverse 는 `rev_c`, 버킷 `candles_1m`·창 1시간, ts 오름차순, 점이 2개 미만이면 null
- events — 방향별·진행 중 수. `top` 은 닫힌 사건만, 코인당 가장 늦게 끝난 1개, `endTs` 내림차순 5개(동률 `sym` 오름차순) — 진행 중 사건과 같은 코인의 더 이른 사건은 빠진다
- Redis 불달·키 없음 → `live`·`trail` null, `events` 정상 / Influx 없음·실패 → `events`·`trail` null, `live` 정상 / 둘 다 → 200 에 셋 다 null
- 캐시 — 5초 안의 두 번째 요청은 Redis 를 다시 읽지 않는다, 60초 안에는 사건 조회를 다시 하지 않는다, 동시 요청 10개에 Redis 읽기는 1번, 경로가 바뀌면 trail 을 새로 읽는다
- Influx 3초 — 사건·봉 조회가 3초 넘게 걸리면 응답은 3초 뒤에 오고 그 부분은 직전 값(처음이면 null), `live` 는 정상이다 / 뒤에서 끝난 조회가 캐시를 채워 다음 요청이 새 값을 받는다 / 조회가 도는 동안 온 요청은 조회를 새로 시작하지 않는다
- 200·camelCase·`Cache-Control: no-store`, 두 역할 모두 `/landing` 이 있다
- `server/tests/test_deploy.py` — nginx 에 `location = /api/landing` 이 api 로 간다

수동:
- 로컬 5컨테이너(dev-setup.md)에서 `curl /api/landing` 의 `top[0]` 이 같은 순간 `http://api:8000/spreads`(박스 안 — 공개에서는 028 이 닫는다) 에 §3.2 규칙을 적용해 고른 행과 같다
- 브라우저 1440px·390px: 경로 카드·다음 경로·3번 두 단의 실데이터 문장(호가 깊이 예시는 1위와 다른 경로이고 원값과 순값 차이가 0.1%p 이상)·사건 표가 찬다 / "알아둘 점"·사실 목록·표 아래 작은 글이 없다 / `/app/` 로 가는 글자는 전부 "서비스로 넘어가기" / h1 이 1440·960·390·360px 에서 모두 두 줄 / 가로 스크롤 없음 / 탭을 숨기면 요청이 멈추고 다시 보이면 즉시 1회 / api 를 멈추면(502) §3.5 대로 / 자바스크립트를 끄면 본문이 읽힌다 / 네트워크 목록에 `/api/spreads`·`/api/ws/` 가 없다 / 경로 링크가 기록 탭의 그 코인·방향·거래소로 연다
- `cd web && npm run lint && npm run build`, `cd server && ruff check . && pytest -q` 통과

## 5. 완료 기준 (실행 세션이 채움)
```bash
# 자동 (2026-09-28, depthGap·3번 섹션 문구·면책/사실 목록/표 아래 글 삭제·버튼 글자·히어로 문구 반영 — 마지막 수정은 web 만 바뀌어 server 는 같은 결과를 다시 확인)
cd server && ruff check . && ruff format --check . && pytest -q
#   All checks passed! / 226 files already formatted / 836 passed (landing 39 — 계산 16·서빙 18·Influx 3초 5)
cd web && npm run lint && npm run build
#   oxlint 출력 없음·종료 0 / tsc -b && vite build ✓ — dist 에 landing.html·landing/{og,spreads,history}.png
node --check <landing.html 의 <script> 를 뽑은 파일>   # oxlint 는 src 만 본다 → 문법 통과

# 수동 — 5컨테이너 대신 대역 구성(이 Mac 은 거래소 도메인이 막혀 수집이 안 된다: api.upbit.com curl 000)
#   로컬 Redis :6399 에 운영 /api/spreads 본문(4초마다), 임시 Influx :8087 에 운영 /api/history/events 7일·/api/history/candles.
#   2026-09-28 에는 사람에게 보이는 데모(:8000·:8090)가 떠 있어 새 코드를 :8001 과 자체 nginx :8091(설정 사본에서 api 포트만 8001)로 띄웠다 — Redis·Influx 는 읽기만
ROLE=api REDIS_URL=redis://localhost:6399/0 INFLUX_URL=http://localhost:8087 INFLUX_TOKEN=<테스트> SLACK_WEBHOOK_URL= uvicorn app.main:app --port 8001
curl -s localhost:8091/api/landing
#   200 · live 키 dataReceivedAt·rate·coins·pairs·over1·over1Movable·depthGap·top
python3 check_depth.py   # 같은 spreads:latest(dataReceivedAt 이 같은 표)에 §3.2 depthGap 규칙을 서버 코드와 따로 적용
#   같음 — BOBA Bybit→빗썸 김프, 원값 +1.26%·순값 +0.04%·차감폭 1.22%p(1위 2Z 와 다른 경로)
# 헤드리스 Chrome(DevTools 프로토콜), localhost:8091/
#   3번 섹션(1440·390): 스펙 문장이 글자 그대로. 호가 깊이 예시 "지금 CPOOL의 빗썸 → Bybit 경로는 맨 위 호가로 보면 +1.64%지만,
#   $1,000어치를 실제로 사고팔면 -0.56%입니다."(1위 2Z 와 다른 경로, 차이 2.20%p), USDT 괄호·over1 문장 참
#   "알아둘 점"·사실 목록·표 아래 작은 글·"대시보드 열기" 없음 / /app/ 로 가는 글자 4곳(상단 바·히어로·바닥·오류 안내 카드) 모두 "서비스로 넘어가기"
#   히어로(데모 :8090 을 읽기만, 새 dist): h1 "김프가 우리에게" / "이득을 주는 김프가 아니다" 1440·360·320px 모두 두 줄 — 43.5·31.7·27.7px,
#   긴 둘째 줄 422.6/440·307.3/320·268.9/280px(칸 폭), 가로 스크롤 없음 / 폴백 27px 을 강제해도 320px 에서 둘째 줄 262/280px
#   <title>·og:title·description·og:description·설명 문단이 스펙 글자와 같음 / og.png 새 h1 로 1200×630 다시 렌더(숫자 없음), dist 에도 반영
#   폴링: 로드 1회 → 보이는 채 21초 3회 → 숨김 25초 동안 3회 그대로 → 다시 보이고 1초 4회 → 10.5초 뒤 5회 / /api/ 요청은 /api/landing 뿐, WebSocket 0건
#   api(:8001) 중지: 연 채로면 응답 [200, 502, 502] 동안 값 유지·"26초 전 값" → 64초에 "최신 값을 받지 못하고 있습니다"+흐림 /
#             멈춘 채 새로 열면 "실시간 값을 불러오지 못했습니다"+"서비스로 넘어가기", 다음 경로·3번 실데이터 문장·사건 구역 숨김
#   자바스크립트 끔: 제목·설명·3번 섹션 글·그림이 읽히고 카드 자리는 noscript 한 줄

# 앞선 수정에서 확인하고 이번에 코드가 바뀌지 않은 경로 (2026-09-27, 같은 대역 구성 :8000·:8090)
#   nginx: / 200 landing(no-store) · /?tab=… 301 /app/?… · /app 301 /app/ · /api/landing → api · 정적 파일 200 · 없는 경로 404
#   live.top[0]·top 5·over1·coins·pairs·rate, events.top(닫힌 사건만·코인마다 가장 늦게 끝난 것·끝난 순 5) 따로 계산한 값과 같음 · open 234 = 적재한 진행 중 234
#   docker pause 로 Influx 가 매달린 상태: 캐시 만료 뒤 첫 요청 3.02초(live 새 값·events 직전 값·새 경로 trail null) · 도는 동안 3.01초 · unpause 뒤 0.02초에 새 값
#   경로 링크가 운영 기록 탭을 그 코인·방향·거래소로 연다 · 포커스는 10초 재그림 뒤에도 같은 링크 · 값 강조 0.6초, reduced-motion 이면 없음
```

## 6. 갱신할 문서
- `docs/context/status.md` — `landing` 행을 `| landing | server: GET /landing 요약(live 5초·trail·events 60초 캐시, Influx 는 3초까지만 기다리고 늦으면 직전 값, 두 역할) | web: 정적 landing.html — 경로 카드·다음 경로·"표시된 김프와 먹을 수 있는 김프" 두 단(호가 깊이 예시 `depthGap`·입출금)·7일 사건 수와 최근에 끝난 사건 5개·어떻게 만들었나, 보이는 동안 10초 폴링, `/app/` 버튼 글자 "서비스로 넘어가기" | 스크린샷·og.png 는 사람이 갱신 |` 로. 알려진 빚은 "(022) 랜딩 스크린샷은 정적이다 — 화면이 바뀌면 사람이 다시 찍는다" 로(서버 사양·부하 수치 목록은 랜딩에서 뺐으므로 그 빚은 지운다).
- `CLAUDE.md` — 스펙 인덱스 022 행: 범위를 "정적 HTML 랜딩 `/` — 실시간 경로 카드·7일 사건·만든 방식, 요약 API `GET /landing`(api), 대시보드는 `/app/`" 로, 상태 DONE.
- `docs/context/architecture.md` — web 항목의 랜딩 문장(요약 API 를 보이는 동안 10초 폴링), 배포 절 serve 박스의 api 서빙 경로와 nginx 설명에 `= /api/landing → api`, "현재 구조" 절에 landing 항목(`features/landing/` — 부분별 캐시와 한 번만 갱신, Influx 부분은 3초까지만 기다리고 조회는 뒤에서 마저, core 의 Redis·Influx 읽기만 쓴다).
- `docs/context/product.md` — 기능 목록 표에 `landing` 행 "처음 온 사람용 소개 페이지(정적 HTML) + 요약 API".
- `docs/context/dev-setup.md` — web 절에 dev 서버에서 랜딩을 여는 주소(실행 세션이 확인한 것)와 `curl localhost:8000/landing` 스모크 한 줄.
- `docs/specs/016-process-split.md` §3.1, `docs/specs/018-spreads-serve.md` §3.4 — api 가 서빙하는 경로 목록에 `/landing`.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server: `app/features/landing/`(`models.py` 응답 모델(`DepthGapOut` 포함), `service.py` 순수 계산 `build_live`(top·over1·`depthGap`)·`build_trail`·`build_events` + `LandingService`(부분마다 캐시 한 칸 `_Slot` — 결과·null 을 ttl 동안, 비었거나 만료되면 키마다 조회 태스크 하나, 그동안 온 요청은 같은 태스크를 기다리고 태스크는 끝나는 순간 캐시를 채워 그때부터 ttl, trail 은 경로가 키. Influx 부분은 대기 상한(3초) 뒤 같은 키의 직전 값), `router.py` `GET /landing`), `app/features/landing/tests/`(`helpers.py` FakeBus·FakeInflux(`gate` 로 느린 조회)·손 시계, `test_rules.py` 16개, `test_serving.py` 18개, `test_influx_wait.py` 5개), `app/main.py`(앱마다 `app.state.landing`, 두 역할 모두 라우터), `tests/test_role.py`(api 경로 7개), `tests/test_deploy.py`(`= /api/landing` 테스트 1개 + api 로 가는 `proxy_pass` 수 2→3).
  - web: `nginx.conf`(`location = /api/landing`), `public/landing.html` 전면 재작성, `public/landing/og.png` 신규, `spreads.png`·`history.png` 재캡처(운영 `/app/` 1360×820 @1.5x, 스크롤바 숨김).
  - 문서: status·architecture·product·dev-setup·db, 016 §3.1·018 §3.4, CLAUDE.md 인덱스.
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
  - 동률: live 는 코인 안에서 값이 같으면 표에서 먼저 나온 행(김프 먼저). `depthGap` 은 차감폭·`sym` 까지 같으면 표에서 먼저 나온 것. events 는 코인 안에서 끝난 시각이 같으면 나중에 시작한 사건.
  - `depthGap` 문턱(원값 ≥ 1.0·차감폭 ≥ 0.1)은 실수 그대로 비교한다 — `over1` 과 같다(반올림·허용 오차 없음).
  - 3초 규칙: live 도 같은 태스크 구조지만 상한 없이 끝까지 기다린다. 대기 상한은 `LandingService(influx_wait_sec=)` 로 주입한다(테스트는 0.2초).
  - h1: 두 줄을 블록으로 고정하고 크기는 `min(48px, 100cqi / 10.11)` — 글 칸을 컨테이너로 두고, 10.11 은 긴 둘째 줄 "이득을 주는 김프가 아니다" 의 폭(Pretendard 700·자간 −0.035em 에서 9.71em 실측, 시스템 대체 글꼴은 9.81em) + 3% 여유. cqi 를 모르는 브라우저는 27px(320px 폭에서도 둘째 줄이 들어간다). 설명 문단의 "출금·입금이" 는 한 줄에 묶었다(줄머리에 가운뎃점이 오지 않게 — 3번 섹션과 같은 규칙).
  - 3번 섹션: 문장은 스펙 그대로 두고 줄바꿈용 묶음만 감쌌다 — h2 는 "표시된 김프와 / 먹을 수 있는 김프는 / 다릅니다" 구 단위(모바일 두 줄), "합니다(지금 업비트 ₩…)." 과 예시 문장의 "{sym}의"·"{출발} → {도착}"·"{값}%지만,"·"{값}%입니다." 는 한 줄에.
  - 화면: 첫 응답 전 카드 자리 "값을 받는 중입니다". 한 번 그린 뒤 `live` 가 null 인 응답은 실패처럼(직전 카드 유지·나이 증가). `events.top` 이 비면 소제목·표를 숨긴다. 추이의 세로 범위에 0 을 늘 넣는다(0% 기준선이 늘 보이고 작은 흔들림을 부풀리지 않게), 선·끝점 색은 마지막 값의 부호색. 지속은 기록 탭 `fmtDur`(분·시간·일), 끝난 시각은 보는 사람 시간대와 무관하게 KST. 카드 전체가 기록 탭 링크, 사건 행은 코인 칸 링크 + 행 클릭. 10초마다 다시 그려도 키보드 포커스를 같은 링크로 되돌린다.
  - 0 의 색은 스펙대로 회색이다 — 대시보드 `pctColor` 는 0 을 글자색으로 칠해 조금 다르다.
  - 그림·favicon 은 상대 경로(`landing/…`) — 배포 `/` 와 dev `/app/landing.html` 에서 같은 파일을 찾는다. `og:image` 는 절대 주소 그대로.
  - 한국어 줄바꿈: 한글 뒤 "·" 앞, ")"·"%" 뒤 조사 앞에서 줄이 바뀌지 않게 그 묶음을 nowrap 으로 감쌌다(문구는 그대로).
  - server: 저장소 불가(Redis 예외·`InfluxUnavailableError`)는 로그 없이 그 부분 null, 그 밖의 계산 예외는 WARNING 1줄 + null(항상 200). `LandingService` 는 I/O 가 없어 lifespan 이 아니라 `create_app` 에서 만든다. events 와 live→trail 을 함께 기다린다. core 변경 없음 — `RedisBus.latest()`(GET 만)·`query_candles`·`query_premium_events`·`TIER_BY_RES["1m"]` 를 그대로 쓴다.
  - 함께 고친 절: 016 §3.1·018 §3.4(api 경로·Redis 용도), §6 목록 밖으로 db.md 읽는 쪽("다른 조회 API 는 DB 0회" 가 틀리게 돼서)·status.md deploy 행과 dev-setup 통합 기동의 api 분기 목록. §4 의 "test_deploy 단언 1개" 는 새 테스트 1개에 더해 기존 단언 하나(api 로 가는 `proxy_pass` 수)가 바뀌었다.
  - `open` 은 `end_ts == 0` 을 센다 — 평소엔 진행 중 사건 수와 같다(운영 `/api/history/events` 에서 `endTs == 0` 248건 = `ongoing` 248건, 013 기동 복원이 600초 넘게 못 본 점을 닫는다). 수집이 멈춰 있는 동안만 그때 열려 있던 사건이 진행 중으로 남는다.
  - 스펙 문구 보고: §3.5 마지막 줄 "자바스크립트가 꺼져도 제목·설명·섹션 글·면책·그림은 HTML 에 있다" 의 "면책" 은 §3.3-6(면책 섹션을 두지 않는다)과 어긋난다 — 코드는 면책이 없다. 설계 세션이 그 낱말을 지울 것.
- 남은 빚:
  - events 비용 — 7일 `premium_event` 전부(2026-09-27 58,601점)를 60초에 한 번 읽는다. 같은 점을 넣은 로컬 Influx 에서 조회 0.95초·파이썬 CPU 0.36초·메모리 +50MB. 느려져도 응답은 3초 규칙으로 늦지 않지만 비용은 그대로다 — 늘어서 api(t4g.micro)에 부담이 되면 Flux 쪽 집계(core 새 조회)로 옮기는 후속 스펙(status.md 빚).
  - 막 1위가 된 경로는 봉이 없어도 null 을 60초 캐시하므로 최대 60초 추이가 안 보일 수 있다(스펙의 null 캐시 그대로).
  - `landing.html` 인라인 스크립트는 oxlint 대상 밖이다(`node --check` 로 문법만).
  - 확인은 로컬 대역 구성(운영 공개 API 본문을 로컬 Redis·Influx 에)으로 했다 — EC2 배포 뒤 `/api/landing` 응답 시간·api CPU 실측 대기.
