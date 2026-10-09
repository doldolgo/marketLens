# 022 — landing

상태: DONE | 의존: 003 spreads(행 계약), 006 wallet-status(5필드), 013 premium-events(`premium_event`), 014 premium-1m(`candles_1m`), 016 process-split(`api` 역할), 017·018(`spreads:latest`), 007 deploy(nginx·web 이미지), 023 domain-tls(절대 주소)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
처음 온 사람이 `https://kimptrack.com/` 을 열면 **지금 실제로 옮길 수 있는 김프 하나**를 실데이터로 먼저 본다 — 어느 거래소에서 사서 어디서 파는지, $1,000 체결 기준 값, 출금·입금이 열린 망, 최근 1시간 추이. 그 아래에서 표시된 김프와 먹을 수 있는 김프가 왜 다른지(호가 깊이·입출금), 지난 7일의 김프 사건, 김프·역프의 뜻과 계산식, KimpTrack 의 계산 방법과 반영하지 않는 것, 자주 묻는 질문을 읽는다.
독자는 **검색이나 링크 미리보기로 처음 온 코인 트레이더**다. 그래서 이 페이지는 검색에 걸리는 문서이기도 하다 — 네이버·구글이 "김프·김치프리미엄·역프" 를 찾는 사람에게 이 페이지를 보여 줄 근거(서비스를 말하는 제목·설명·h1, 뜻·계산식·방법을 담은 본문, 구조화 데이터, 중복 없는 주소)를 정적 HTML 에 둔다(2026-10-01 사람 결정 — 공식 가이드와 경쟁 사이트 조사로 다시 짰다).
검색 엔진·메신저 미리보기가 자바스크립트 없이 본문을 읽도록 페이지는 **정적 HTML** 이고, 실데이터는 이 스펙이 만드는 가벼운 요약 API 하나로만 받는다(지금 `/api/spreads` 는 gzip 170KB 라 랜딩이 매번 받기엔 무겁다).

## 2. 범위
- 만드는 것: server `features/landing/`(`GET /landing`), `web/public/landing.html`(HTML·CSS·스크립트 한 파일), `web/public/landing/`(`og-v3.png` 1200×630, `spreads.png`·`history.png` 와 WebP 두 크기, `fonts/kimptrack-sans.woff2`·`OFL.txt`), 아이콘(`favicon.ico`·`favicon.svg`·`icon-192.png`·`apple-touch-icon.png`·`logo-512.png`), `robots.txt`·`sitemap.xml`·`404.html`, `web/index.html`(대시보드 셸)의 head 메타, 글꼴 서브셋 스크립트 `web/scripts/subset-landing-font.py`, nginx 의 랜딩 경로 규칙, Caddyfile 의 www·catch-all(023 과 함께), 계약 테스트 `server/tests/test_landing_seo.py`.
- 하지 않는 것: 가입·로그인·방문 집계·다국어, 랜딩의 WebSocket, 대시보드 화면(`/app/` 본문) 변경, 코인별·경로별 색인 페이지, 서버가 실데이터를 HTML 에 미리 넣는 렌더링, FAQPage·HowTo·SearchAction 마크업(구글이 리치 결과를 없앴다), `meta keywords`, 검색엔진 계정 등록(사람 몫 — §3.6).
- 바꾸는 기존 것: 016 §3.1·018 §3.4 의 "`api` 가 서빙하는 경로" 목록에 `/landing`. 023 §3.1 의 www·IP 평문 행. 랜딩은 `/api/spreads` 를 부르지 않는다.

## 3. 동작

### 3.1 경로 (nginx·caddy)
| 요청 | 응답 |
|---|---|
| `/` | `landing.html` |
| `/?tab=…`·`/?s.…=` | `301 /app/?…` |
| `/landing.html`·`/app/landing.html` | `301 /` |
| `/app` | `301 /app/` |
| `/app/…` | 대시보드 |
| `/api/landing` | `api:8000/landing` |
| `/privacy` | privacy.html(032) |
| `/kimp-chart`·`/kimp-history` | 검색어 페이지(044) |
| 없는 루트 경로 | `404.html`(404) |
| `www` | `301` apex |

- **옛 대시보드 링크만 옮긴다.** `/` 에 `tab` 이나 스프레드 탭 키(`s.` 로 시작 — 기본 탭이라 `tab` 없이 공유된다)가 있을 때만 쿼리를 들고 `/app/` 로 301 이다(`if ($args ~ "(^|&)(tab|s\.[a-z]+)=")` — location 이 아니라 허용 목록(028)을 우회하지 않는다). utm·광고 클릭 id(`fbclid`·`NaPm` 등) 같은 다른 쿼리로 온 첫 방문자는 랜딩을 그대로 본다 — canonical 이 주소를 `/` 하나로 모은다.
- **같은 본문은 한 주소로.** `/landing.html`·`/app/landing.html` 은 `/` 로 301. `www.kimptrack.com` 은 http·https 모두 경로·쿼리를 들고 `https://kimptrack.com` 으로 301(caddy, 023). 탄력 IP·로컬 같은 그 밖의 호스트는 평문 그대로 주되 `X-Robots-Tag: noindex` 를 붙인다(caddy catch-all) — IP 주소가 사본으로 색인되지 않게.
- **캐시.** 랜딩은 `Cache-Control: no-cache`(매번 다시 확인, 바뀌지 않았으면 304). 스크립트가 `/api/landing` 계약을 따르므로 낡은 사본을 쓰지 않게 하되, no-store 와 달리 뒤로 가기 캐시(bfcache)에는 들어간다. `index.html`·`/app/` 는 no-store 그대로, `assets/` 1년 immutable.
- 없는 루트 경로는 서버 수준 `error_page 404 /404.html` — 상태 404 그대로 한국어 안내(첫 화면·실시간 김프 표 링크, `noindex`). `/api` 의 JSON 404(`return 404 '{…}'`)는 본문이 있어 가로채지 않는다. SPA fallback 은 `/app/` 아래에서만.
- 정적 파일(`/landing/*`·아이콘·`robots.txt`·`sitemap.xml`·`404.html`)은 루트 `location /` 가 준다. 빌드 때 만든 `.gz` 를 `gzip_static` 으로 준다(007 §3 — caddy 뒤에서도 나가게 `gzip_proxied any`).
- `/api/landing` 은 공개 허용 목록(028)의 **정확 일치** location 하나 — 다른 허용 경로와 같은 모양(접두 제거 rewrite·프록시 헤더 4개)에 `X-Robots-Tag: noindex`(JSON 자체는 색인하지 않는다). 배포에서 api 가 죽으면 이 경로만 502 이고 랜딩 본문은 그대로 뜬다.

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
- `coins` = `rows` 의 `sym` 종류 수, `pairs` = `rows` 길이(둘 다 상태 무관). `over1` = `status == "ok"` 인 (행, 방향) 가운데 **원값**(값 + 차감폭)이 1.0 이상인 수, `over1Movable` = 그중 옮길 수 있는 방향인 수. 값도 기준도 013 사건 진입과 같다 — 틱이 사건을 여는 값이 최우선 호가 기준 원값이라, 순값으로 세면 같은 페이지의 "1% 이상 벌어진" 이 두 뜻이 된다(2026-09-28 운영 표 3장에서 순값 62~64건·원값 110~115건).
- `depthGap`: 호가 깊이 예시 하나. `status == "ok"` 행의 옮길 수 있는 방향 가운데 원값(값 + 차감폭)이 1.0 이상이고 차감폭이 0.1%p 이상인 것 중 차감폭이 가장 큰 하나(같으면 `sym` 오름차순). 필드 `sym`·`dom`·`fx`·`dir`·`raw`(원값)·`pct`(순값)·`slip`(차감폭). 후보가 없으면 null. `top[0]` 을 예시로 쓰지 않는 이유: 1위 경로는 대개 호가가 두꺼워 원값과 순값이 0.01~0.1%p 밖에 차이 나지 않는다 — 코인 이름만 바뀌고 요점이 안 보였다(2026-09-28 실측 1위 +1.69→+1.63%, 이 규칙으로 고른 HFT 는 +1.27→−0.04%).
- `null` 조건: Redis 불달, 키 없음, 값이 JSON 이 아니거나 `rows` 가 배열이 아님.

**trail** — `top[0]` 경로의 최근 1시간. Influx 버킷 `candles_1m`(014)에서 그 (dom, fx, sym) 봉을 `[지금−3600, 지금)` 로 읽어, 방향이 `kimp` 면 `fwd_c`, `reverse` 면 `rev_c` 를 `[창 시작 ts, 값]` 으로 ts 오름차순(최대 60점). 봉 값은 **슬리피지 차감 전 원값**이라 `top[0].pct` 보다 크다 — 랜딩은 "맨 위 호가 기준" 이라고 표시한다. `null` 조건: `live` 가 null, `top` 이 빔, Influx 없음(토큰 없음)·실패, 점이 2개 미만.

**events** — Influx `premium_event`(013)를 `start = 지금 − 604800`, `stop = 지금` 으로 읽는다(사건 시작 시각 기준, 진행 중 포함). `count` 전체, `kimp`·`reverse` 방향별, `open` = `endTs == 0` 이고 마지막 관측 `last_ts` 가 `지금 − 600` 이후인 점의 (dom, fx, base, dir) 종류 수 — 013 복원이 고아 점을 닫는 규칙(결측허용 600초)과 같다. 고아 점(재기동 전에 열려 닫히지 못한 점)은 세지 않고, 재기동 뒤 같은 조합이 다시 열려 두 점이 다 `endTs == 0` 이어도 한 번이다. `top` = **닫힌 사건**(`endTs > 0`)만, 코인마다 가장 늦게 끝난 사건 하나(같은 코인에서 끝난 시각이 같으면 늦게 시작한 것, 그것도 같으면 dom·fx·dir 오름차순), `endTs` 내림차순 상위 5개(같으면 `sym` 오름차순) — 필드 `sym`(점의 base)·`dom`·`fx`·`dir`·`maxPercent`·`startTs`·`endTs`·`durationSeconds`·`lastTs`. 최고값 순으로 고르지 않는 이유: 7일 최고값 자리는 입출금이 막혔거나 이름만 같은 다른 코인의 수백 % 값이 차지한다(2026-09-27 실측 코인별 최고 652%·415%·275%, 스테이블코인 106%). 계속 기록하고 있다는 것을 현실적인 값으로 보여 주려고 최근에 끝난 순으로 고른다. 사건 값도 원값이고 입출금 여부와 무관하게 잡힌다. `null` 조건: Influx 없음·실패.
- **사건 점을 올리지 않고 Flux 에서 접는다(2026-09-28 사람 결정).** core 공개 함수 `InfluxClient.query_event_summary(*, start: int, stop: int, open_since: int, top_n: int) -> EventSummary`(`open_since` = 지금 − 600, `top_n` = 5)가 요청 하나에서 방향별 수(`end_ts` 필드 점 수), `end_ts == 0` 인 점과 `last_ts ≥ open_since` 인 점의 키(교집합이 진행 중), 닫힌 점 중 끝난 시각이 늦은 후보 200개(`top`)를 받는다 — 필드를 pivot 하지 않는다. 코인별 동률 규칙은 파이썬이 후보에 적용하고, 고른 5건만 그 코인들로 좁혀 `max_percent`·`duration_seconds`·`last_ts` 를 한 번 더 읽는다(같은 7일 창). 후보 끝자리와 같은 시각에 끝난 사건이 잘려 순위가 확정되지 않으면(가장 이른 후보보다 늦게 끝난 코인이 5개 미만 — 재기동이 고아 수백 건을 한 시각에 닫은 직후) 닫힌 점 전부의 `end_ts` 로 다시 묻는다. 한 필드만 있는 반쪽 점도 방향별 수와 후보에 들고, 후보에서 고른 반쪽 점은 상세 필드가 없어 `top` 에서 빠진다(다음 사건으로 채우지 않는다) — 실데이터에는 없다. 이유: 7일 사건 5.9만 점을 전부 pivot 해 api(t4g.micro)에서 세면 60초마다 파이썬 CPU 0.4초·메모리 +50MB 가 든다.

- **캐시**(프로세스 메모리): `live` 5초, `events` 60초, `trail` 60초 — trail 은 경로(dom·fx·sym·dir)가 바뀌면 만료 전이라도 새로 읽는다. 비었거나 만료된 부분에 요청이 몰리면 **한 번만** 갱신하고 나머지는 그 결과를 쓴다. null 결과도 같은 시간만큼 캐시한다 — 장애 중에 요청마다 Redis·Influx 를 두드리지 않게. Influx 호출은 스레드로 넘긴다(동기 클라이언트 — 016 과 같다).
- **Influx 는 3초까지만 기다린다.** `trail`·`events` 는 갱신이 3초 안에 끝나지 않으면 이번 응답에 그 부분의 직전 값(만료됐어도 그대로, 한 번도 채운 적 없으면 null)을 싣는다. `trail` 의 직전 값은 **같은 경로**의 것만이다 — 1위 경로가 막 바뀌었다면 null(다른 코인의 추이를 새 경로 카드에 싣지 않는다). 조회는 뒤에서 끝까지 돌고, 끝나는 순간 그 결과(실패면 null)로 캐시를 채워 그때부터 TTL 을 센다. 조회가 도는 동안 온 요청은 새 조회를 시작하지 않고 같은 조회를 같은 3초 규칙으로 기다린다. 그래서 Influx 가 느리거나 매달려도 응답은 Redis 읽기 + 3초 안에 오고, 경로 카드는 Influx 때문에 늦지 않는다(Influx 클라이언트 자체 타임아웃은 60초라 이 규칙이 없으면 응답 전체가 그만큼 늦는다).
- `features/landing/` 은 다른 기능을 import 하지 않는다. core 의 Redis·Influx 클라이언트(`spreads:latest` 읽기, 봉 조회, 사건 요약 조회)와 013 의 결측허용 상수(`core.premium_events.MAX_GAP_SEC`)만 쓴다.

### 3.3 화면 — 위에서 아래로
전부 한국어, 왼쪽 정렬, 최대 폭 1120px. 거래소 표시명 `upbit→업비트` `bithumb→빗썸` `binance→Binance` `bybit→Bybit` `bitget→Bitget` `okx→OKX`(경로 카드·표). 글 속에서는 한글로 쓴다(바이낸스·바이비트·비트겟·OKX — OKX 는 한글 표기가 없어 그대로). h1 은 하나, 섹션은 h2, 그 안은 h3.

```
KimpTrack                  김프·역프란  계산 방법  자주 묻는 질문  [실시간 김프 표]
실시간 김프·역프,               ┌ 지금 가장 큰 경로 ────────── 3초 전 값 ┐
실제로 옮길 수 있는지까지       │ VERONA  빗썸에서 사서 Bybit에서 팝니다  │
정의 문단(3문장)                │ +2.26%   역프 · $1,000 체결 기준        │
[실시간 김프 표 보기] 김프 기록과 차트 │ 빗썸 ●━━━━━━━━━━━━━━━━━━━━━● Bybit │
                              │ 출금 가능 · ERC20   입금 가능 · Ethereum │
                              └──────────────────────────────────────┘
                               그다음으로 큰 경로  HNT 빗썸 → Bitget +1.24% (4줄)
```
1. **상단 바** — 워드마크 `KimpTrack`(→ `/`), 이 페이지 안 바로 가기 `김프·역프란`(#kimp)·`계산 방법`(#method)·`자주 묻는 질문`(#faq), 버튼 "실시간 김프 표"(→ `/app/`). 960px 미만에서는 바로 가기를 숨기고 바닥의 바로 가기를 쓴다. `/app/` 로 가는 글자는 목적지를 말한다 — "실시간 김프 표(보기)", 기록 탭은 "김프 기록과 차트".
2. **히어로** — h1 "실시간 김프·역프, 실제로 옮길 수 있는지까지"(검색어 김프·역프·실시간과 차별점을 한 줄에. 옮길 수 있는 경로만 추리는 것은 경로 카드이고 실시간 김프 표는 막힌 경로도 상태와 함께 보여 주므로 '경로만' 이라고 쓰지 않는다). 어느 폭에서나 "실시간 김프·역프," / "실제로 옮길 수 있는지까지" 두 줄이고, 긴 둘째 줄이 한 줄에 들어가게 글자 크기를 폭에 맞춘다(§3.4). 정의 문단(글자 그대로): "KimpTrack(김프트랙)은 업비트·빗썸·바이낸스·바이비트·비트겟·OKX 여섯 거래소, 약 400개 코인의 가격 차이와 입출금 상태를 매초 들여다봅니다. 김프가 벌어진 코인을 찾는 데서 끝나지 않고 지금 그 경로로 코인을 옮길 수 있는지, 실제로 사고팔면 몇 %가 남는지까지 한 화면에 보여 줍니다."(2026-10-08 사람 요구 — 거래소·코인 약 400개·입출금과 가격 차이를 묶어 한 문단, 설명문 투를 뺀다. 2026-10-09 사람 요구 — "$1,000어치"와 "가입 없이 무료입니다."를 뺀다) 버튼 "실시간 김프 표 보기"(→ `/app/`)와 글 링크 "김프 기록과 차트"(→ `/app/?tab=history`). 오른쪽(모바일은 아래)에 **경로 카드**:
   - 값이 오기 전·자바스크립트 없이 보이는 글: 머리 "지금 가장 큰 경로" + "약 1,500개 경로 가운데 출금과 입금이 모두 열린 곳만 골라, 김프·역프가 가장 큰 경로를 이 자리에 보여 줍니다." (`<noscript>` 로 "실시간 값은 자바스크립트를 켜면 보입니다." 한 줄 더). 카드는 값이 온 뒤의 크기에 가깝게 최소 높이를 잡아 둔다(데스크톱 480px·모바일 400px) — 아래 글이 밀리지 않게.
   - 값이 오면: 머리줄 "지금 가장 큰 경로" 와 값의 나이 "N초 전 값"(§3.5), 코인 심볼(크게)과 경로 문장(`kimp` 는 "{fx}에서 사서 {dom}에서 팝니다", `reverse` 는 "{dom}에서 사서 {fx}에서 팝니다"), 값 `pct`(부호·소수 2자리 — 페이지에서 가장 큰 글자)와 "김프"/"역프" · "$1,000 체결 기준".
   - **경로 선** — 출발 거래소 ●━● 도착 거래소. 출발 아래 "출금 가능 · {망}", 도착 아래 "입금 가능 · {망}". `kimp`: 출발 = fx·`netFx`, 도착 = dom·`netDom`. `reverse`: 출발 = dom·`netDom`, 도착 = fx·`netFx`. 망이 null 이면 " · {망}" 만 뺀다.
   - 추이 — `trail` 을 인라인 SVG 선 하나로(축·눈금 없음, 0% 기준선 하나, 끝점 표시). 라벨 "최근 1시간 · 맨 위 호가 기준". trail 이 null 이면 이 줄을 숨긴다.
   - 가격 두 칸 — "{국내 거래소} ₩{krw}", "{해외 거래소} ${usd}". 수식처럼 잇지 않는다: `krw` 는 최우선 매수호가, `usd` 는 마지막 체결가라 둘을 곱해도 `pct` 가 나오지 않는다.
   - 카드 아래 **그다음으로 큰 경로** — `top[1..4]` 를 한 줄씩: 심볼, "{출발} → {도착}", 값. 없으면 줄째 숨긴다.
   - 카드와 각 경로는 링크 `/app/?tab=history&sym={sym}&h.dir={dir}&h.dom={dom}&h.fx={fx}`(기록 탭이 그 코인·방향·거래소로 열린다).
3. **김프 히스토리로 분석하세요**(h2, #analyze) — 전부 정적(실데이터 없음, 2026-10-08 사람 결정으로 "표시된 김프와 실제로 먹을 수 있는 김프" 절을 이 절로 바꿨다 — `depthGap`·`over1`·`over1Movable`·`rate` 는 응답에 남지만 랜딩은 쓰지 않는다). 글자 그대로:
   - 머리글 "김프는 늘 같은 코인, 같은 경로에서 되풀이해 벌어집니다. KimpTrack은 1% 넘게 벌어진 순간을 전부 사건으로 남기고, 그때 입출금이 열려 있었는지까지 같이 기록합니다. 지난 기록을 보면 어디서 기회가 자주 오는지, 그때 실제로 옮길 수 있었는지가 보입니다."
   - 세 칸(데스크톱 가로 셋, 720px 미만 세로) — 소제목·글·링크: "사건 기록" — "어느 코인이 어느 경로로 얼마나, 얼마 동안 벌어졌는지 최근 순으로 남습니다. 자꾸 나오는 코인과 거래소 조합이 눈에 들어옵니다." → `/kimp-history` "김프 히스토리 보기" · "봉 차트" — "1분 봉부터 일봉까지. 상장이나 점검, 급등 때 김프가 어떻게 움직였는지 시간대별로 되짚어 봅니다." → `/kimp-chart` "김프 차트 보는 법" · "입출금 띠" — "벌어졌던 그 시각에 출금과 입금이 열려 있었는지 차트 아래에 붙습니다. 막혀 있던 김프는 보는 값일 뿐이었다는 걸 기록이 알려 줍니다." → `/app/?tab=history` "기록 탭 열기".
4. **지난 7일 김프·역프 사건**(h2, #events) — "맨 위 호가로 1% 이상 벌어져 1분 넘게 이어진 구간을 사건으로 남깁니다. 입출금이 막혔던 경로도 함께 남깁니다."(사건은 입출금과 무관하게 잡힌다) + (`events` 가 있으면) " 지난 7일 동안 {count}건(김프 {kimp}건, 역프 {reverse}건)이 있었고, 지금 {open}건이 진행 중입니다." 그 아래 소제목 "최근에 끝난 사건" 과 `events.top` 표: 코인 / 경로 "{dom} · {fx}" / 방향 / 최고 "+{maxPercent}%" / 지속(`durationSeconds`) / 끝난 시각(`endTs`, KST, "9월 25일 14:03"). 행은 기록 탭 링크(`h.dir` 는 사건 방향). 표 아래 글 링크 "비트코인(BTC) 김프 기록과 차트 보기"(→ `/app/?tab=history&sym=BTC`). 옆에 기록 탭 그림과 캡션 "김프 기록과 차트 — 코인·거래소별 맨 위 호가 기준 김프를 1분에서 1일까지 봉으로 그리고, 입출금이 막혔던 구간을 띠로 함께 보여 줍니다."
5. **김프(김치프리미엄)와 역프란**(h2, #kimp) — 용어와 풀이 두 칸(`<dl>`, 모바일은 한 칸). 글자 그대로:
   - 김프(김치프리미엄) — "같은 코인이 국내 거래소에서 해외 거래소보다 비싸게 거래되는 정도입니다. 국내 원화 가격이 해외 가격을 원화로 바꾼 값보다 몇 % 높은지로 나타냅니다."
   - 역프(역프리미엄) — "반대로 국내 가격이 해외보다 싼 상태입니다. KimpTrack은 두 방향을 따로 계산해, 역프도 국내에서 사서 해외에서 팔 때 얼마나 벌어지는지를 양수로 보여 줍니다."
   - 계산식 — "김프(%) = (국내 원화 가격 ÷ (해외 USDT 가격 × USDT 원화 가격) − 1) × 100", "역프(%) = (해외 USDT 가격 × USDT 원화 가격 ÷ 국내 원화 가격 − 1) × 100"(003 의 두 방향 식) + 예시 "예를 들어 국내에서 10,500원인 코인이 해외에서 7.00 USDT이고 USDT가 1,450원이면, 해외 가격은 10,150원이라 김프는 약 3.45%입니다." (예시로 밝힌 계산이라 §3.5 의 '지어낸 숫자' 가 아니다)
   - USDT(테더) 환산 — "해외 거래소 가격은 USDT 단위라 원화로 바꿔야 비교할 수 있습니다. KimpTrack은 은행 환율 대신 업비트·빗썸의 USDT 원화 호가를 씁니다. 돈이 실제로 원화 → USDT → 해외 거래소 순서로 움직이기 때문이고, 그래서 테더 김프도 값에 들어갑니다."
   - 테더 김프 — "USDT(테더) 자체의 김프입니다. 국내 거래소의 USDT 원화 가격이 달러 환율보다 몇 % 높은지를 뜻하며, 비트코인 김프나 알트코인 김프와 따로 움직입니다. KimpTrack은 은행 환율 대신 이 USDT 원화 호가로 계산하므로, 코인 김프가 은행 환율로 계산한 값보다 테더 김프만큼 작게(테더 역프면 크게) 나옵니다."(2026-10-08 — 검색어 "테더 김프"·"코인 김프"·"비트코인 김프" 를 뜻풀이로 담는다)
   - 김프 차익거래(아비트라지)와 따리 — "해외 거래소에서 코인을 사서 국내 거래소로 옮겨 파는 식으로 두 거래소의 가격 차이를 노리는 거래를 차익거래(아비트라지)라고 합니다. 코인을 들고 거래소 사이를 오간다고 해서 '따리(보따리)'라고도 부르고, 해외에서 미리 사 둔 코인을 국내 상장 때 파는 상장 따리도 같은 원리입니다. 표시된 김프에는 수수료와 전송 시간이 빠져 있어, 그만큼 남는다는 뜻은 아닙니다."(2026-10-08 — 검색어 "김치프리미엄 차익거래"·"아비트라지"·"따리" 를 뜻풀이로 담되, 같은 풀이 안에서 수익이 아니라고 못 박는다)
   - 사건 — "맨 위 호가로 본 김프나 역프가 1% 이상 벌어져 1분 넘게 이어진 구간입니다. 0.5% 이하로 내려오면 끝난 것으로 봅니다."(013 — 진입 1.0% 이상·이탈 0.5% 이하·1분 초과)
6. **KimpTrack이 김프를 계산하는 방법**(h2, #method) — 네 단계를 가로로(모바일은 세로), 번호를 붙인다. 기술 이름(WebSocket·Redis·InfluxDB·S3 등)과 수집 세부 수치는 쓰지 않는다 — 무엇을 하는지만 말한다(2026-09-28 사람 결정).
   1. 수집 — "업비트·빗썸과 해외 거래소 4곳의 호가를 실시간으로 받습니다. 입출금이 열려 있는지도 수시로 확인합니다."
   2. 계산 — "매초 모든 코인의 김프·역프를 다시 계산합니다. 맨 위 호가 하나가 아니라 실제로 사고팔 때의 단가를 씁니다."
   3. 기록 — "맨 위 호가로 계산한 값은 빠짐없이 저장합니다. 크게 벌어졌던 구간은 따로 모아 두어 나중에 다시 볼 수 있습니다."(저장하는 것은 원값이다 — $1,000 순값은 저장하지 않는다)
   4. 전달 — "실시간 김프 표에는 바뀐 값만 골라 곧바로 보냅니다."(이 랜딩은 10초 폴링이다)
   네 단계 아래에 "반영하지 않는 것" 소제목과 문단을 두지 않는다(2026-10-09 사람 요구 — 수수료·전송 시간이 빠졌다는 것은 FAQ "수수료·전송 시간 반영" 답과 바닥 참고값 안내가 말한다).
7. **자주 묻는 질문**(h2, #faq) — 늘 펼친 h3 질문 + 답 9개(데스크톱 두 칸). 질문: 김프 사이트마다 숫자가 다른 이유 / 환율은 무엇을 쓰나 / 옮길 수 없는 경로 판단 / 수수료·전송 시간 반영(아니요 — "표시된 김프만큼 수익이 난다는 뜻이 아닙니다") / 갱신 주기(매초 계산, 이 카드는 보이는 동안 10초) / 가입·비용(아니요, 실시간 김프 표는 PC 화면용) / 매수·매도 신호(아니요) / 역김프와 역프는 같은 말인가(네 — 역프·역김프·역프리미엄, 2026-10-08) / 김프 차트와 과거 기록(네 — 기록 탭 링크 + `/kimp-chart`·`/kimp-history` 안내 링크, 044). "왜 $1,000 기준인가" 문항은 두지 않는다(2026-10-09 사람 요구). 답 문장은 `landing.html` 이 진실이고, 사실은 이 스펙의 다른 절과 같아야 한다.
8. **바닥** — 바로 가기(실시간 김프 표·김프 기록과 차트·김프·역프란·계산 방법·자주 묻는 질문·김프 차트 — `/kimp-chart`·김프 히스토리 — `/kimp-history`(044)·개인정보 처리방침 — `/privacy`, 가장 밝고 굵게, 032 · 화면 분석 설정 — 방침 링크 뒤 마지막 칸, `/privacy#consent`, 같은 탭, 033), 참고값 안내 "KimpTrack의 수치는 거래소 공개 데이터로 자동 계산한 참고값이며 투자 권유가 아닙니다. 거래·출금 수수료와 전송 시간은 반영하지 않았습니다."(최대 폭을 두지 않는다 — 넓은 화면에서 한 줄, 좁은 화면에서는 바닥 폭에서 줄을 바꾼다), "운영 고원규·이진중 · 문의 joseph13ko15@gmail.com, untilduck@gmail.com"(메일은 `mailto:` 링크 — 2026-10-01 사람이 준 값, 032 처리방침과 같은 운영 주체). 고친 날은 화면에 적지 않는다(사람 결정 2026-10-09 — §3.6).
- 수익을 약속하는 말(이득·이익·수익·차익·무위험)은 제목·설명·h1 에 쓰지 않는다. 본문에서는 "수익이 난다는 뜻이 아닙니다" 처럼 부정하는 자리에만. 용어 풀이에서 '차익거래(아비트라지)·따리' 를 정의하는 것은 예외이되, 같은 풀이 안에 수수료·전송 시간이 빠져 있어 그만큼 남는다는 뜻이 아니라는 문장을 둔다(2026-10-08).

### 3.4 시각 규칙
- 색은 `docs/design/theme.css` 토큰 값을 복사해 쓴다(번들 밖 파일이라 import 불가): 배경·표면·글자·회색 단계·보라 강조·상승 빨강·하락 파랑. 부호 색은 대시보드와 같다 — 양수 빨강, 음수 파랑, 0 회색. 보라는 워드마크·버튼·경로 선·포커스에만 쓴다.
- 글꼴은 **KimpTrack Sans** 하나 — Pretendard Variable 을 이 페이지가 쓰는 글자(+ 인쇄 가능한 ASCII)만 남기고 굵기 400~700 으로 자른 woff2 한 파일(약 58KB — 레이아웃 기능은 기본 + `tnum`, 이름에 내용 해시 8자라 글을 고친 배포 뒤 옛 글꼴이 남지 않는다)을 같은 출처 `landing/fonts/` 에서 받는다(`<link rel=preload>` + 인라인 `@font-face`, `font-display: swap`). jsDelivr 동적 서브셋(렌더 차단 외부 CSS 1개 + 조각 16개 약 412KB)을 쓰지 않는다 — 첫 화면이 외부 연결과 큰 글꼴을 기다리지 않게(2026-10-01). Pretendard 는 OFL 1.1 에 예약 이름이 걸려 있어 자른 파일은 다른 이름을 쓰고 `OFL.txt` 를 옆에 둔다. 글을 고치면 `uv run web/scripts/subset-landing-font.py` 로 다시 만든다(새 파일 이름으로 바꾸고 landing.html 의 두 주소도 고친다) — 담은 글자 목록 `web/scripts/landing-font-glyphs.txt` 에 없는 글자가 페이지에 생기면 테스트가 멈춘다. API 가 주는 한글(망 이름 등)이 서브셋 밖이면 시스템 글꼴로 보인다. 라틴 글리프가 Inter 기반이라 대시보드(Inter)의 숫자와 모양이 같다. 모든 숫자는 `tabular-nums`. 크기: h1 은 긴 줄("실제로 옮길 수 있는 경로만")이 그 폭에 들어가는 크기로 최대 48px, 경로 카드 값 80px(모바일 52), h2 30px(모바일 24), 본문 17px·줄간 1.7, 작은 글 13px. 본문 한 줄은 34em 이하.
- 떠 있는 면은 **경로 카드 하나**다(표면색 + 큰 그림자). 나머지 섹션은 카드로 감싸지 않고 여백과 필요한 자리의 가는 구분선으로 나눈다. 스크린샷은 가는 테두리와 둥근 모서리만.
- 쓰지 않는 것: 대문자 라벨, 버튼 글자 끝의 화살표 문자, 섹션마다 나타나는 등장 애니메이션, 장식용 그라데이션.
- 움직임은 하나 — 폴링으로 경로 카드 값이 바뀌면 값 글자가 0.6초 동안 강조됐다 돌아온다. `prefers-reduced-motion` 이면 없다.
- 키보드 포커스는 보라 외곽선으로 보인다. 그림의 alt 는 무엇을 보여 주는지 한 문장, 캡션(`figcaption`)은 그림 속 정보를 글로 한 번 더. 스크린샷은 WebP 1020w·2040w(`<picture>`, `sizes` 는 표시 폭) + PNG 대체, `loading=lazy`(첫 화면 밖이라).

### 3.5 스크립트 규칙
- 로드 직후 `GET /api/landing` 1회, 이후 **페이지가 보이는 동안만** 10초마다. 숨으면(`visibilitychange`) 멈추고, 다시 보이면 즉시 1회 부른 뒤 재개한다. WebSocket·`/api/spreads`·`/api/health*` 는 부르지 않는다.
- 값의 나이 = `(servedAt − live.dataReceivedAt) / 1000` + 응답을 받은 뒤 흐른 초. 1초마다 글자만 고친다. 60초를 넘기면 값과 추이를 부호색 대신 회색으로 바꾸고 나이 자리에 "최신 값을 받지 못하고 있습니다"(투명도로 흐리면 누를 수 있는 링크의 글자 대비가 기준 아래로 떨어진다).
- 10초마다 다시 그릴 때 그린 글이 직전과 같으면 DOM 을 건드리지 않는다 — 포커스가 튀거나 스크린리더가 같은 링크를 다시 읽지 않게.
- 값이 오기 전에는 카드의 정적 설명(§3.3-2)을 그대로 둔다("값을 받는 중" 같은 문구로 바꾸지 않는다 — 검색 로봇이 렌더한 화면에도 그 설명이 남게). 첫 요청이 실패(네트워크·200 아님·JSON 아님)하거나 `live` 가 null 이면 그 설명 아래에 "실시간 값을 불러오지 못했습니다." 한 줄과 "실시간 김프 표 보기" 버튼을 단다. 그 뒤의 실패는 직전 값을 그대로 두고 나이만 는다. 지어낸 숫자·자리표시 숫자는 넣지 않는다.
- 뒤로 가기로 돌아와 bfcache 에서 복원되면(`pageshow` 의 `persisted`) 곧바로 1회 부른다.
- `top` 이 빈 배열이면 카드 안에 머리 "지금 가장 큰 경로" 와 "지금 옮길 수 있는 경로가 없습니다. 출금과 입금이 모두 열린 경로가 생기면 이 자리에 보여 줍니다." 만 두고 그다음 경로를 숨긴다. 부분 null 은 §3.3 규칙대로 그 구역만 숨긴다.
- 자릿수는 대시보드 `web/src/shared/format.ts` 의 `fmtKrw`·`fmtUsdt`·`fmtPct` 규칙을 옮겨 쓴다.
- 자바스크립트가 꺼져도 제목·정의 문단·섹션 글·비교 표·용어·계산 방법·질문과 답·그림은 HTML 에 있다. 실데이터 자리는 카드의 정적 설명과 `<noscript>` 한 줄.
- `<head>` 에 `clarity.js`(033) 한 줄(`defer`, 상대 경로) — 동의 창(모달)도 이 파일이 그린다(시스템 글꼴 — 이 페이지 서브셋 글꼴을 쓰지 않는다, 고정 위치라 카드 배치가 그대로), `<body>` 에 `data-clarity-unmask="true"`.

### 3.6 검색·미리보기·그림
문구는 한 번 정하면 자주 바꾸지 않는다 — 네이버는 메인 제목·설명을 노출을 노려 자주 바꾸면 불이익을 준다. 아래 값은 `server/tests/test_landing_seo.py` 가 글자 그대로 본다.
- `<title>`·`og:title` "김프(김치프리미엄)·역프 실시간, 입출금 확인 | KimpTrack" — 검색어를 앞에, 브랜드는 뒤에(2026-10-08 — 네이버·구글 상위 김프 사이트가 전부 검색어 앞·브랜드 뒤라 따라간다), 차별점(입출금 확인)을 브랜드 앞에(사람 결정 2026-10-09 — 검색어만 있으면 결과 목록에서 다른 김프 사이트와 구별되지 않는다), 40자 이내, '김프' 는 한 번(동명이의 GIMP·유튜버와 가르려고 괄호로 뜻을 붙인다), '김치프리미엄' 은 검색어 표기대로 붙여 쓴다(본문도 같다). 경쟁 사이트 이름('김프가')과 수익을 약속하는 말은 넣지 않는다.
- `description`·`og:description` "김프 사이트 KimpTrack은 국내외 6개 거래소의 김치프리미엄·역프를 매초 계산하고, 입출금이 열렸는지와 지난 기록까지 보여 줍니다." — 80자 이내 한 문장, '김프 사이트' 구절을 한 번(2026-10-08 — 네이버 "김프 사이트" 상위가 전부 이 구절을 설명에 둔다), 거래소 이름은 늘어놓지 않고 "국내외 6개 거래소"로 줄여 입출금·지난 기록 두 차별점에 자리를 준다(사람 결정 2026-10-09 — 거래소 이름은 본문·질문과 답에 있다), 정의 문단을 옮기지 않는다(본문 복사는 네이버 불이익). 서비스 전체가 '열린 경로만' 보여 준다고 쓰지 않는다(§3.3-2).
- `<meta name="robots" content="index, follow, max-image-preview:large">`, canonical·`og:url` `https://kimptrack.com/`, `lang="ko"`, `og:site_name` KimpTrack, `og:locale ko_KR`, `twitter:card summary_large_image`.
- `og:image` `https://kimptrack.com/landing/og-v3.png`(1200×630, `og:image:type`·`width`·`height`·`alt`) — 어두운 배경에 워드마크, h1 두 줄, "$1,000 체결 단가 · 경로마다 출금·입금 확인", 6개 거래소 이름(국내 둘 — 선 — 해외 넷). 원본은 `web/scripts/og-image.html`(헤드리스 크롬으로 1200×630 을 찍는다 — 명령은 파일 머리 주석). 실시간 값은 넣지 않는다(미리보기는 오래 남는다). 그림을 바꾸면 파일 이름을 바꾼다 — 카카오·텔레그램이 같은 주소의 그림을 다시 받지 않는다.
- 아이콘 — 워드마크(두 점과 선)를 어두운 둥근 사각형에 담은 한 모양. `favicon.ico`(16·32·48), `favicon.svg`(대시보드), `icon-192.png`, `apple-touch-icon.png`(180), `logo-512.png`(Organization 로고). 랜딩 head 에는 `rel=icon`(192 PNG)·`rel=apple-touch-icon` 하나씩, **절대 주소**(구글은 SVG 파비콘을 쓰지 않고 네이버는 상대 경로를 읽지 않는다). 주소는 바꾸지 않는다.
- **구조화 데이터** — head 에 JSON-LD 한 블록(`@graph`, 절대 주소, 스크립트로 넣지 않는다): `WebSite`(name KimpTrack, alternateName ["김프트랙"], url, inLanguage ko-KR, publisher) · `Organization`(name, url, logo 512, email 두 개, member 고원규·이진중 — 바닥에 보이는 값과 같다) · `WebPage`(name = title, description = description, isPartOf, about, primaryImageOfPage = og 그림, dateModified) · `WebApplication`(name, url `/app/`, applicationCategory FinanceApplication, operatingSystem Web, isAccessibleForFree, offers 0 KRW, description, screenshot 두 장, publisher). 평점·리뷰는 넣지 않는다(지어낸 평점 금지) — 그래서 구글 리치 결과 테스트·Search Console 'Software apps' 에는 WebApplication 이 invalid 로 뜬다(의도 — 리치 결과 대상이 아닐 뿐 순위 불이익은 없고, 네이버 Software 는 평점 없이 받는다). 보이는 글과 같은 값만 쓴다.
- **설명을 고친 날** — `WebPage.dateModified` 와 `sitemap.xml` 의 `/` 줄 `lastmod` 가 같은 날짜다. 화면(바닥)에는 적지 않는다(사람 결정 2026-10-09). 설명 글을 실제로 고칠 때만 올린다(실시간 값이 바뀌었다고 올리지 않는다).
- **실시간 값은 스니펫에서 뺀다** — 경로 카드와 그다음 경로를 감싼 `div`, 사건 요약 `span`·표 `div` 에 `data-nosnippet` 을 HTML 에 처음부터 둔다(구글은 span·div·section 에서만 읽고, 스크립트로 붙인 속성은 믿지 않는다). 매초 바뀌는 값이 몇 주 뒤 검색 결과·AI 답변에 '지금 값' 처럼 남지 않게. 정의·방법·질문과 답은 스니펫에 열어 둔다.
- `robots.txt` — 규칙 네 줄 `User-agent: *` / `Allow: /api/landing` / `Disallow: /api/` / `Sitemap: https://kimptrack.com/sitemap.xml`(주석 줄 — 다음 PIN — 은 더해도 된다). 더 긴 규칙이 이겨(RFC 9309) 검색 로봇은 랜딩을 그릴 때 실데이터를 받고, 그 밖의 API 는 막힌다. 학습용 봇도 막지 않는다(따로 정할 일).
- `sitemap.xml` — `https://kimptrack.com/`, `https://kimptrack.com/kimp-chart`·`/kimp-history`(044 — `lastmod` 는 그 페이지를 고친 날), `https://kimptrack.com/privacy`(032 — `lastmod` 는 방침 시행일) 네 줄, 각각 `lastmod`. `/app/` 은 넣지 않는다.
- **대시보드 셸**(`web/index.html`) — 정적 `<meta name="robots" content="noindex, follow">`(스크립트로 바꾸지 않는다 — 구글은 noindex 를 보면 렌더링 전에 건너뛴다), description, 공유 미리보기용 og(title "KimpTrack - 실시간 김프 표", 그림은 랜딩과 같은 `og-v3.png`, url `/app/`). `<title>KimpTrack</title>` 은 007 의 스모크 문자열이라 그대로.
- **검색엔진 등록은 사람 몫** — 구글 Search Console 은 도메인 속성(Cloudflare DNS TXT), 네이버 서치어드바이저는 `https://kimptrack.com` 을 등록하고 소유 확인(메타 태그면 head 에 `naver-site-verification` 한 줄, HTML 파일이면 `web/public/` 에), 두 곳 모두 `sitemap.xml` 제출. 다음 웹마스터도구는 `robots.txt` 맨 위에 PIN 주석 줄. 문구·그림을 바꾼 뒤에는 카카오 공유 디버거로 OG 캐시를 지운다.
- `spreads.png`·`history.png`: `https://kimptrack.com/app/` 실화면을 1360×820 @1.5x 로 찍는다(스프레드 탭 기본 화면, 기록 탭 `?tab=history&sym=BTC`). 다시 찍으면 WebP 두 크기(1020·2040)도 다시 만든다.

### 3.7 반응형
- 960px 이상: 히어로 두 단(글 5 : 카드 7), 3·4번은 글 : 그림 두 단, 6번은 네 칸 가로, 7번은 두 칸. 미만: 한 단(글 → 카드 → 그다음 경로), 상단 바의 바로 가기는 숨긴다.
- 320·390px 폭에서 페이지 가로 스크롤이 없다. 사건 표는 720px 미만에서 행마다 두 줄(코인·최고 / 경로·방향·지속·끝난 시각), 비교 표는 항목마다 세 줄, 용어는 한 칸.
- 대시보드는 여전히 데스크톱 전용이다(product.md). 랜딩만 모바일에서 읽힌다.

## 4. 검증
server — `features/landing/tests/`(Redis·Influx 는 fake):
- 옮길 수 있는 방향만 후보다 — `wdFx`·`depDom` 이 true 일 때만 kimp, `wdDom`·`depFx` 가 true 일 때만 reverse, null 은 열림이 아니다
- `stale`·`fail` 행은 `top`·`over1` 에서 빠지고, `coins`·`pairs` 는 상태와 무관하다
- 코인당 1개(방향·거래소 중 값이 큰 것), 값 내림차순 5개, 동률은 `sym` 오름차순, 0 이하 값도 들어간다
- `over1` — 원값(값 + 차감폭)으로 센다: 원값 1.0 은 포함, 순값 0.5·차감폭 0.5 도 포함, 원값 0.75 는 빠진다
- `depthGap` — 옮길 수 있는 방향만, 원값 1.0 이상·차감폭 0.1 이상 가운데 차감폭 최대(동률 `sym` 오름차순), `raw = pct + slip`, 경계값(원값 1.0·차감폭 0.1)은 포함, 옮길 수 없는 방향의 더 큰 차감폭은 무시, 후보가 없으면 null
- trail — kimp 는 `fwd_c`·reverse 는 `rev_c`, 버킷 `candles_1m`·창 1시간, ts 오름차순, 점이 2개 미만이면 null
- events — 방향별·진행 중 수. `top` 은 닫힌 사건만, 코인당 가장 늦게 끝난 1개, `endTs` 내림차순 5개(동률 `sym` 오름차순) — 진행 중 사건과 같은 코인의 더 이른 사건은 빠진다
- events `open` — 마지막 관측 601초 전인 고아 점은 빠지고 600초 전은 든다, 재기동 뒤 같은 조합의 옛 점·새 점은 한 번, 다른 조합은 따로; 요약 조회는 `open_since = 지금 − 600`·`top_n = 5` 로 한 번 (`test_rules.py`)
- 요약 조회 — 점을 전부 올려 센 기준선과 같다(무작위 40벌·끝 시각 동률이 몰린 후보·코인 안 동률은 늦게 시작한 것·시작까지 같으면 dom·fx·dir), 후보 200개가 한 시각 동률로 잘리면 닫힌 점 전부로 다시 묻는다, 고른 반쪽 점은 `top` 에서 빠진다, 첫 요청에 pivot 없음·두 요청 같은 7일 창 (`tests/test_event_summary.py`)
- Redis 불달·키 없음 → `live`·`trail` null, `events` 정상 / Influx 없음·실패 → `events`·`trail` null, `live` 정상 / 둘 다 → 200 에 셋 다 null
- 캐시 — 5초 안의 두 번째 요청은 Redis 를 다시 읽지 않는다, 60초 안에는 사건 조회를 다시 하지 않는다, 동시 요청 10개에 Redis 읽기는 1번, 경로가 바뀌면 trail 을 새로 읽는다
- Influx 3초 — 사건·봉 조회가 3초 넘게 걸리면 응답은 3초 뒤에 오고 그 부분은 직전 값(처음이면 null), `live` 는 정상이다 / 뒤에서 끝난 조회가 캐시를 채워 다음 요청이 새 값을 받는다 / 조회가 도는 동안 온 요청은 조회를 새로 시작하지 않는다
- 200·camelCase·`Cache-Control: no-store`, 두 역할 모두 `/landing` 이 있다
- `server/tests/test_deploy.py` — nginx 에 `location = /api/landing` 이 api 로 간다, `/` 의 301 은 `$arg_tab` 일 때만, Caddyfile 은 apex 블록 + www(http·https) 301 블록 + catch-all
- `server/tests/test_observability.py` — 공개 location 집합에 `= /landing.html`, catch-all 은 `X-Robots-Tag noindex` + `reverse_proxy`, www 블록은 `redir` 한 줄·접속 기록 없음
- `server/tests/test_landing_seo.py` — §3.6 의 제목·설명·og·robots 메타·canonical 글자 그대로, 네이버 한도(제목 40·설명 80자, 제목·설명에 '김프' 한 번씩, '김프가'·수익 약속 없음), 설명 ≠ 정의 문단, h1 하나·h2 다섯, 페이지 안 앵커가 있는 섹션을 가리킴, JSON-LD 네 노드가 보이는 값과 같고 평점 없음·가리키는 파일이 있음, 고친 날이 dateModified·lastmod 에서 같고 바닥에 날짜 줄이 없음, 아이콘 절대 주소·rel 하나씩, PNG 크기, 실시간 자리 `data-nosnippet`·카드 정적 설명, 글꼴 서브셋이 페이지 글자를 모두 담음·외부 글꼴 없음, robots.txt 규칙 네 줄(주석 제외)·옛 링크 정규식(tab·s.* 는 옮기고 utm 등은 두기), 대시보드 셸 noindex·제목 스모크, 404.html noindex·링크, nginx 의 `/landing.html`·`/app/landing.html` 301·`/api/landing` noindex·랜딩 no-cache, 글꼴 주소 두 곳이 해시 이름 파일 하나를 가리킴

수동:
- 로컬 5컨테이너(dev-setup.md)에서 `curl /api/landing` 의 `top[0]` 이 같은 순간 `http://api:8000/spreads`(박스 안 — 공개에서는 028 이 닫는다) 에 §3.2 규칙을 적용해 고른 행과 같다
- 브라우저 1440px·390px·320px: 경로 카드·그다음 경로·사건 표가 찬다 / h1 이 모든 폭에서 두 줄 / 가로 스크롤 없음 / 글꼴이 같은 출처의 KimpTrack Sans 하나로 그려진다 / 탭을 숨기면 요청이 멈추고 다시 보이면 즉시 1회 / api 를 멈추면(502) §3.5 대로 / 자바스크립트를 끄면 본문이 읽힌다 / 네트워크 목록에 `/api/spreads`·`/api/ws/` 가 없다 / 경로 링크가 기록 탭의 그 코인·방향·거래소로 연다
- 로컬 nginx(이 레포의 `nginx.conf` + 새 dist): `/` 200 no-cache · `/?utm_source=…` 200 랜딩 · `/?tab=history`·`/?s.q=xrp` 301 `/app/?…` · `/landing.html`·`/app/landing.html` 301 `/` · `/nope` 404 한국어 · `/api/nope` 404 JSON · `/api/landing` 에 `X-Robots-Tag: noindex`
- 로컬 caddy(`caddy:2-alpine`) + 더미 `web`: `Host: www.kimptrack.com` 301 `https://kimptrack.com/{경로}?{쿼리}` · IP 호스트는 `X-Robots-Tag: noindex`, `caddy validate` 통과
- 배포 뒤(사람): `curl -sI https://www.kimptrack.com/x?a=1` 301 · 네이버 서치어드바이저 '사이트 간단 체크'·schema.org 검사기에 오류 없음, 구글 리치 결과 테스트는 WebSite·Organization 통과·WebApplication 은 평점이 없어 invalid(의도 — §3.6) · 카카오 공유 디버거로 새 미리보기 확인
- `cd web && npm run lint && npm run build`, `cd server && ruff check . && pytest -q` 통과

## 5. 완료 기준 (실행 세션이 채움)
```bash
# 자동 (2026-10-01, 검색 재구성 — server 코드는 그대로, 설정·정적 파일·테스트만)
cd server && ruff check . && ruff format --check . && pytest -q
#   All checks passed! / 262 files already formatted / 1067 passed (test_landing_seo 15 새로)
cd web && npm run lint && npm run build
#   oxlint 종료 0 / tsc -b && vite build ✓ — dist 에 landing.html·404.html·robots.txt·sitemap.xml·아이콘 5개·landing/{og-v2.png, 스크린샷 PNG·WebP, fonts/}
uv run web/scripts/subset-landing-font.py   # landing/fonts/kimptrack-sans-eebfbed5.woff2 57,868B · 글자 308개 · 원본에 없는 글자 없음
docker run --rm -v ./caddy:/etc/caddy:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile   # v2.11.4 Valid configuration

# 수동 (2026-10-01) — 로컬 nginx:1.27-alpine(이 레포 nginx.conf + 새 dist, :8093). /api/landing 은 운영 응답을 넘겨 주는 대역
#   (이 Mac 은 거래소가 막혀 수집을 못 하고, 운영 /api/spreads 는 028 이 닫았다. 운영 top 이 비어 있을 때는 카드 모양만 스펙 예시로 채움)
#   / 200 no-cache · /?utm_source=naver&fbclid=x 200 랜딩 · /?tab=history 301 /app/?tab=history · /?s.q=xrp 301 /app/?s.q=xrp · /landing.html·/app/landing.html 301 /
#   /nope 404 한국어 404.html · /api/nope 404 JSON 그대로 · /api/landing X-Robots-Tag: noindex · /robots.txt text/plain · /favicon.ico 200
#   /landing/fonts/kimptrack-sans-<해시>.woff2 font/woff2 · /app/ 200 no-store(noindex 메타)
# 로컬 caddy:2-alpine + 더미 nginx(web): Host www.kimptrack.com → 301 https://kimptrack.com/x?a=1 · Host 3.34.104.16·localhost → X-Robots-Tag: noindex
# 브라우저(1440·390·320): h1 두 줄 — 1440 43.5px(둘째 줄 423/440px), 390 34.6px(336/350), 320 27.7px(269/280) · 가로 스크롤·넘치는 요소 없음
#   글꼴은 KimpTrack Sans 하나(전송 58.2KB, 외부 요청 없음) · 카드 높이 값 온 뒤 557px(1440)·512px(390) → 최소 높이 480·400 · data-nosnippet 6곳
#   헤드리스 Chrome 전체 화면으로 비교 표·용어·계산 방법·질문과 답·바닥 확인
# 제목·설명·바닥 정리 (2026-10-09, 사람 결정) — 제목 37자·설명 76자, 바닥 날짜 줄 삭제, 참고값 안내 최대 폭(56em) 삭제, dateModified·lastmod 2026-10-09
#   ruff check·format 통과 · pytest 1802 passed(test_landing_seo·test_clarity·test_privacy·test_kimp_pages 63 포함) — 실패 7 은 샌드박스가 소켓을 막은
#   test_gauge·test_raw_archive(PermissionError, 이 변경과 무관) · oxlint 0 · vite build ✓ · 글꼴 kimptrack-sans-8f1c14d0.woff2 59,220B·글자 322개('렸' 더함)
#   Browser pane(정적 서버 :8093, 1440×900): 참고값 안내 글 폭 730px — 예전 최대 폭 728px(56em×13px)에 2px 모자라 두 줄이던 것이 한 줄
# 미리보기 그림 og-v3 (2026-10-09) — 해외 거래소에 OKX 를 더함. 원본이 레포에 없어 web/scripts/og-image.html 로 다시 짰다
#   og-v2 와 같은 글로 찍어 줄마다 위치·폭을 맞춤(워드마크 88–301·h1 89–593/89–844·부제 90–559·거래소 줄 90–1110, 원본과 ±3px)
#   헤드리스 크롬 1200×630 → og-v3.png 47KB · og-v2.png 지움 · 랜딩·대시보드 셸·검색어 페이지 둘의 og:image 와 JSON-LD 주소 교체
```
- 요약 API(§3.2)의 서버 검증 기록은 2026-09-28 판 그대로다(코드가 바뀌지 않았다) — git 기록의 이 절 이전 판.
- 커밋 전 검토(4개 관점 — 검색·문구와 사실·문서 일치·성능과 접근성 — 에 지적마다 반박 검증)에서 확인된 29건을 반영했다: 서비스 전체가 '열린 경로만' 보여 준다는 과장(h1·설명·정의·비교 표·og), 스프레드 탭 옛 링크(`/?s.…`), `/app/landing.html` 사본, 사건 경계(1% 이상·0.5% 이하), 역프 식, USDT 매수·매도 호가 방향, '최우선'→'맨 위' 호가, 원값 저장, 폴링 재그림, 낡은 값의 대비, 글꼴 기능·해시 이름·임시 폴더, 문서 줄들. `gzip_proxied` 는 007 PR(#82) 몫이라 여기서 고치지 않았다.

## 6. 갱신할 문서
- `docs/context/status.md` — `landing` 행의 web 칸에 검색 구성(제목·설명·JSON-LD·data-nosnippet·자체 글꼴·아이콘·robots·sitemap·대시보드 noindex·404)을 더하고, 남은 사람 작업(검색엔진 등록·카카오 캐시·운영 주체)을 알려진 빚에.
- `CLAUDE.md` — 스펙 인덱스 022·023 행.
- `docs/context/architecture.md` — serve 박스 줄의 www 301·catch-all noindex.
- `docs/specs/023-domain-tls.md` §3.1·§4 — www 301, IP 평문에 noindex. `docs/specs/027-observability.md` §3.2 — 기록을 부르는 블록은 apex 하나.
- `docs/specs/016-process-split.md` §3.1, `docs/specs/018-spreads-serve.md` §3.4 — api 가 서빙하는 경로 목록에 `/landing`(이미 반영).
- `docs/context/architecture.md` deploy(007) 줄의 nginx 경로 규칙과 landing 줄의 새 파일, `docs/context/dev-setup.md` 랜딩 dev 문단(절대 주소·글꼴 다시 만들기), `CLAUDE.md` §2 `web/scripts/`.
- `docs/specs/007-deploy.md` §3 web 줄 — 404.html·SPA fallback 범위. `docs/specs/027-observability.md` §2 — utm 301 문구 삭제, status.md 의 그 빚 삭제.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - server: `app/features/landing/`(`models.py` 응답 모델(`DepthGapOut` 포함), `service.py` 순수 계산 `build_live`(top·over1·`depthGap`)·`build_trail`·`build_events` + `LandingService`(부분마다 캐시 한 칸 `_Slot` — 결과·null 을 ttl 동안, 비었거나 만료되면 키마다 조회 태스크 하나, 그동안 온 요청은 같은 태스크를 기다리고 태스크는 끝나는 순간 캐시를 채워 그때부터 ttl, trail 은 경로가 키. Influx 부분은 대기 상한(3초) 뒤 같은 키의 직전 값), `router.py` `GET /landing`), `app/features/landing/tests/`(`helpers.py` FakeBus·FakeInflux(`gate` 로 느린 조회)·손 시계, `test_rules.py` 16개, `test_serving.py` 18개, `test_influx_wait.py` 5개), `app/main.py`(앱마다 `app.state.landing`, 두 역할 모두 라우터), `tests/test_role.py`(api 경로 7개), `tests/test_deploy.py`(`= /api/landing` 테스트 1개 + api 로 가는 `proxy_pass` 수 2→3).
  - web: `nginx.conf`(`location = /api/landing`), `public/landing.html` 전면 재작성, `public/landing/og.png` 신규(2026-10-01 `og-v2.png` 로 바뀜), `spreads.png`·`history.png` 재캡처(운영 `/app/` 1360×820 @1.5x, 스크롤바 숨김).
  - 문서: status·architecture·product·dev-setup·db, 016 §3.1·018 §3.4, CLAUDE.md 인덱스.
- 추측한 지점 / 실행 중 함께 고친 스펙 절:
  - 동률: live 는 코인 안에서 값이 같으면 표에서 먼저 나온 행(김프 먼저). `depthGap` 은 차감폭·`sym` 까지 같으면 표에서 먼저 나온 것. events 는 코인 안에서 끝난 시각이 같으면 나중에 시작한 사건.
  - `depthGap` 문턱(원값 ≥ 1.0·차감폭 ≥ 0.1)은 실수 그대로 비교한다 — `over1` 과 같다(반올림·허용 오차 없음).
  - 3초 규칙: live 도 같은 태스크 구조지만 상한 없이 끝까지 기다린다. 대기 상한은 `LandingService(influx_wait_sec=)` 로 주입한다(테스트는 0.2초).
  - h1: 두 줄을 블록으로 고정하고 크기는 `min(48px, 100cqi / 10.34)` — 글 칸을 컨테이너로 두고, 10.34 는 긴 둘째 줄 "실제로 옮길 수 있는 경로만" 의 폭(KimpTrack Sans 700·자간 −0.035em 에서 9.91em 실측, 시스템 대체 글꼴은 10.04em) + 3% 여유. cqi 를 모르는 브라우저는 27px(320px 폭에서도 둘째 줄이 들어간다).
  - 화면: 첫 응답 전 카드 자리는 정적 설명(§3.3-2). 한 번 그린 뒤 `live` 가 null 인 응답은 실패처럼(직전 카드 유지·나이 증가). `events.top` 이 비면 소제목·표를 숨긴다. 추이의 세로 범위에 0 을 늘 넣는다(0% 기준선이 늘 보이고 작은 흔들림을 부풀리지 않게), 선·끝점 색은 마지막 값의 부호색. 지속은 기록 탭 `fmtDur`(분·시간·일), 끝난 시각은 보는 사람 시간대와 무관하게 KST. 카드 전체가 기록 탭 링크, 사건 행은 코인 칸 링크 + 행 클릭. 10초마다 다시 그려도 키보드 포커스를 같은 링크로 되돌린다.
  - 0 의 색은 스펙대로 회색이다 — 대시보드 `pctColor` 는 0 을 글자색으로 칠해 조금 다르다.
  - 본문 그림·글꼴은 상대 경로(`landing/…`) — 배포 `/` 와 dev `/app/landing.html` 에서 같은 파일을 찾는다. 아이콘·`og:image`·JSON-LD 는 절대 주소(검색엔진 규칙)라 dev 에서는 운영 파일을 가리킨다.
  - 한국어 줄바꿈: 한글 뒤 "·" 앞, ")"·"%" 뒤 조사 앞에서 줄이 바뀌지 않게 그 묶음을 nowrap 으로 감쌌다(문구는 그대로).
  - server: 저장소 불가(Redis 예외·`InfluxUnavailableError`)는 로그 없이 그 부분 null, 그 밖의 계산 예외는 WARNING 1줄 + null(항상 200). `LandingService` 는 I/O 가 없어 lifespan 이 아니라 `create_app` 에서 만든다. events 와 live→trail 을 함께 기다린다. core 는 `RedisBus.latest()`(GET 만)·`query_candles`·`query_event_summary`(사건 요약, 2026-09-28 추가)·`TIER_BY_RES["1m"]`·`MAX_GAP_SEC` 를 쓴다.
  - 함께 고친 절: 016 §3.1·018 §3.4(api 경로·Redis 용도), §6 목록 밖으로 db.md 읽는 쪽("다른 조회 API 는 DB 0회" 가 틀리게 돼서)·status.md deploy 행과 dev-setup 통합 기동의 api 분기 목록. §4 의 "test_deploy 단언 1개" 는 새 테스트 1개에 더해 기존 단언 하나(api 로 가는 `proxy_pass` 수)가 바뀌었다.
  - `open` 은 `end_ts == 0` 이고 마지막 관측이 600초 안인 조합을 센다 — 013 기동 복원이 3초 상한을 넘겨 고아 점이 남으면 `end_ts == 0` 만으로는 운영 7일에서 3,232건(실제 진행 중 255건)이 됐다. 쓰기가 600초 넘게 막히면(Influx 불통) 그동안 진행 중이 적게 보인다.
- 남은 빚:
  - 막 1위가 된 경로는 봉이 없어도 null 을 60초 캐시하므로 최대 60초 추이가 안 보일 수 있다(스펙의 null 캐시 그대로).
  - `landing.html` 인라인 스크립트는 oxlint 대상 밖이다(`node --check` 로 문법만).
  - 확인은 로컬 대역 구성(운영 공개 API 본문을 로컬 Redis·Influx 에)으로 했다 — EC2 배포 뒤 `/api/landing` 응답 시간·api CPU 실측 대기.
- 2026-09-28 성능 개선 — 사건 요약·진행 중·over1: 사건 부분을 core 요약 조회로(§3.2 — Flux 에서 접고 후보 200개를 파이썬이 동률 규칙으로), 진행 중을 600초 규칙과 조합 종류 수로, `over1` 을 원값으로(`landing.html` 문장에 "맨 위 호가로"). 측정(로컬 influxdb:2.7, 운영 사건 7일 58,666건 + 고아·재개 흉내, 기준선 코드와 같은 조건): events 갱신 wall 920 → 126ms, 파이썬 CPU 380 → 5.5ms, Influx CPU 683 → 278ms. 방향별 수·`top` 5건 전 필드가 기준선과 같고, `open` 만 3,262 → 267(같은 점에 600초 규칙을 따로 적용한 값과 같다). 코인 안에서 끝·시작 시각까지 같은 사건은 기준선이 저장소 순서로 골랐고 이제 dom·fx·dir 오름차순이다. 후보가 동률로 잘리는 모양(한 시각에 520건)에서도 고른 코인이 기준선과 같다. 운영 표 3장의 `over1` 62·63·64 → 110·111·115, `over1Movable` 26·27·28 → 68·69·73.
- 2026-10-01 검색 재구성 — 공식 가이드(구글 Search Central·네이버 서치어드바이저·schema.org)와 경쟁 김프 사이트 11곳의 head·본문을 조사해 다시 짰다. 만든 것: `landing.html`(head·정보 구조·문구, 스크립트는 정적 카드 설명·오류 안내·`pageshow` 만 바뀜), `landing/og-v2.png`·WebP 4개·`fonts/`(서브셋·OFL), 아이콘 5개, `404.html`, `robots.txt`·`sitemap.xml`, `web/index.html` 메타, `web/scripts/subset-landing-font.py`·`landing-font-glyphs.txt`, `nginx.conf`(`$arg_tab`·`/landing.html` 301·`error_page`·no-cache·`/api/landing` noindex), `caddy/Caddyfile`(apex·www 301·catch-all noindex), 테스트 `test_landing_seo.py` 새로·`test_deploy.py`·`test_observability.py` 고침.
  - 정한 것(사람이 위임): 제목·설명·h1 을 검색어 중심으로 바꾸고 2026-09-28 사람 문구("김프가 우리에게 이득을 주는 김프가 아니다")는 뺐다 — '김프가' 가 1위 경쟁 사이트 이름이라 제목·h1 에 두 번 들어가 있었고, '이득' 은 수익 약속으로 읽힌다. 같은 뜻은 3번 섹션 제목이 이어 간다. 참고값 안내 한 줄을 바닥에 되살렸다(YMYL 신뢰 — 2026-09-28 의 '면책 섹션 없음' 결정은 섹션이 아닌 한 줄로 바꿨다). 한글 이름 '김프트랙' 을 정의 문단과 WebSite.alternateName 에.
  - 남은 사람 작업: 검색엔진 등록·사이트맵 제출(§3.6), 배포 뒤 카카오 OG 캐시 초기화, 스크린샷을 다시 찍으면 WebP 도.
  - 하지 않은 것(후속 후보): 서버가 최근 사건·상위 경로를 HTML 에 미리 넣는 렌더링(지금 크롤러 본문에는 실데이터가 없다 — 구글은 `/api/landing` 을 열어 렌더링으로 받는다), 코인별 페이지, HSTS(서브도메인·계정 이전 뒤), `/landing/*` 캐시 헤더.
