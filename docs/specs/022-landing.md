# 022 — landing

상태: DONE | 의존: 003 spreads(`GET /spreads` 응답 행), 007 deploy(nginx·web 이미지)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.

## 1. 목적
처음 온 사람이 주소를 열면 대시보드 대신 **이게 무엇인지 3초 안에 알 수 있는 한 페이지**를 먼저 본다. 검색 엔진과 메신저 미리보기가 자바스크립트 없이도 제목·설명·본문을 읽을 수 있어야 하므로 **정적 HTML** 이다. 지금 실제로 벌어진 김프 몇 개를 실데이터로 보여 주고, 버튼 하나로 대시보드에 들어간다.

## 2. 범위
- 만드는 것: 정적 파일 `web/public/landing.html`(HTML·CSS·티저 스크립트 한 파일), `web/public/robots.txt`, 스크린샷 `web/public/landing/*.png`. 서버 코드 없음. React 안 씀.
- 하지 않는 것: 가입·로그인·대기자 명단·방문 집계·다국어·사이트맵. 대시보드 자체는 그대로 누구나 연다.
- 바꾸는 기존 것: 대시보드가 `/` 에서 `/app/` 로 옮겨 간다(Vite `base`, nginx 경로). 배포 워크플로·compose 는 그대로 — nginx 설정은 web 이미지에 들어 있어 main 머지 = 배포다.

## 3. 동작

### 3.1 경로 (nginx)
| 요청 | 응답 |
|---|---|
| `/` | `landing.html` |
| `/?tab=…` | `301 /app/?tab=…` |
| `/app` | `301 /app/` |
| `/app/…` | 대시보드 |
| `/landing/*.png` `/robots.txt` `/favicon.svg` | 정적 파일 |
| 그 밖의 루트 경로 | 404 |

- `/` 에 쿼리가 붙어 오면(옛 링크 `/?tab=history&sym=BTC`) 쿼리를 그대로 들고 `/app/` 로 301 한다. 랜딩은 쿼리를 쓰지 않는다.
- SPA fallback(없는 경로 → `index.html`)은 **`/app/` 아래에서만**. 루트의 없는 경로는 404 다 — 검색 엔진이 아무 경로나 랜딩 사본으로 색인하지 않게.
- 번들·favicon 은 `/app/assets/…`·`/app/favicon.svg` 로 요청된다(Vite `base: '/app/'`). 빌드 결과는 지금처럼 이미지 루트에 놓고 nginx 가 `/app/` 접두를 벗겨 같은 파일을 준다. 캐시 규칙은 그대로: `index.html`·`landing.html` no-store, `assets/` 1년 immutable.
- 대시보드의 URL 상태(탭·심볼·차트 옵션)는 지금처럼 쿼리에만 실린다. dev 서버 주소는 `http://localhost:5173/app/` 이다.

### 3.2 화면 구성 (위에서 아래로)
1. **상단 바** — 워드마크 "MarketLens" 와 오른쪽 "대시보드 열기" 버튼(→ `/app/`).
2. **히어로** — 제목 "김치 프리미엄, 1초마다." 와 두 줄 설명: 업비트·빗썸과 바이낸스·바이빗·비트겟 사이의 김프·역프를 1초 단위로 계산하고, 슬리피지를 뺀 순값과 입출금 가능 여부까지 한 화면에 보여 준다는 것. 그 아래 큰 "대시보드 열기" 버튼.
3. **실시간 티저** — "지금 열려 있는 김프" 소제목과 카드 최대 5장. 카드 1장 = 코인 심볼, 경로(해외 → 국내 또는 국내 → 해외, 거래소 표시명), 김프 퍼센트(대시보드와 같은 색 규약: 양수 빨강·음수 파랑·0 은 회색), 방향 라벨(김프/역프). 카드 아래 한 줄: 코인 수·페어 수·USDT 환율. 규칙은 §3.3.
4. **화면 소개** — 대시보드 스크린샷 2장(스프레드 표·기록 탭 차트)을 카드 틀에 넣어 보여 준다. 정적 파일 `web/public/landing/spreads.png`·`history.png`(EC2 실화면 캡처, 1360×820 @1.5x). 새 캡처로 갈아 끼우는 건 사람이 한다.
5. **기능 3장** — 실시간 스프레드 표 / 기록과 봉 차트 / 입출금 상태. 각 카드는 제목 + 두 문장.
6. **작동 방식 3단계** — 수집(5거래소 WebSocket, 1초 틱) → 계산(`$1,000` 체결 기준으로 호가를 걷어 슬리피지를 뺀 순값) → 기록(1% 진입·0.5% 이탈 사건, 1분 봉을 1일까지 롤업). 숫자는 스펙 001·003·013·014 의 값이다.
7. **바닥** — "소프트웨어 마에스트로 17기 · MarketLens" 한 줄과 "대시보드 열기" 링크.

전부 한국어. 색·글꼴·둥글기·그림자 값은 `docs/design/theme.css` 의 토큰과 **같은 값**을 `landing.html` 안에 복사해 쓴다(번들 밖 파일이라 CSS 변수를 import 하지 못한다). 테마 값이 바뀌면 이 파일도 맞춘다.

### 3.3 검색·미리보기 메타
- `<title>` "MarketLens — 김치 프리미엄 실시간 모니터링", `<meta name="description">` 두 문장, `<link rel="canonical" href="https://kimptrack.com/">`(절대 주소 — IP 로 들어와도 검색엔진이 도메인을 원본으로 본다), `lang="ko"`.
- Open Graph: `og:title`·`og:description`·`og:url`(`https://kimptrack.com/`)·`og:image`(`https://kimptrack.com/landing/spreads.png` — 절대 주소여야 한다)·`og:locale ko_KR`, `twitter:card summary_large_image`. 도메인은 023 부터(그 전엔 EC2 IP 가 박혀 있었다).
- `robots.txt`: 전부 허용, `/api/` 만 불허.
- 본문(제목·설명·기능·작동 방식·스크린샷 alt)은 전부 HTML 에 있다. 자바스크립트가 꺼져도 티저 구역만 "불러오는 중…" 으로 남고 나머지는 그대로 읽힌다.

### 3.4 실시간 티저 규칙
- 페이지를 열 때 `GET /api/spreads` 를 **한 번만** 부른다. 재조회·WebSocket 없음 — 방문자가 수집 서버에 부담을 주지 않게.
- 응답 계약(003·018): `rows[]` 의 행마다 `sym`·`dom`·`fx`·`fwd`·`rev`·`status`·`depDom`·`wdDom`·`depFx`·`wdFx`, 최상위 `rate`. 입출금 값은 열림 true / 막힘 false / 모름 null.
- 후보 = `status` 가 `fail` 이 아닌 행에서, **경로가 실제로 열린 방향만**:
  - 김프(해외 매수 → 국내 매도): `wdFx === true && depDom === true` 일 때 값 `fwd`
  - 역프(국내 매수 → 해외 매도): `wdDom === true && depFx === true` 일 때 값 `rev`
  - 모름(null)은 열림으로 치지 않는다.
- 코인마다 값이 가장 큰 후보 1개만 남기고, 값 내림차순 상위 5개. 값이 0 이하여도 상위 5 안이면 보여 준다(지금 시장이 그렇다는 뜻이므로 숨기지 않는다).
- 후보가 0개면 카드 대신 "지금 열린 경로가 없습니다" 한 줄.
- 요청 실패(404·503·네트워크)·비정상 응답이면 티저 구역 전체를 숨긴다(지어낸 숫자·플레이스홀더 숫자 금지). 응답 전에는 "불러오는 중…" 한 줄.
- 카드 아래 줄: `코인 N개 · 페어 M개 · USDT ₩R` — N = `rows` 의 `sym` 종류 수, M = `rows` 길이, R = `rate` 를 정수 원으로. `rate` 가 0 이면 환율 항목은 뺀다.

### 3.5 반응형
- 720px 미만: 한 열. 티저 카드는 가로 스크롤, 스크린샷은 폭 100%, 기능 3장은 세로로 쌓인다. 페이지 가로 스크롤은 없어야 한다.
- 대시보드는 여전히 데스크톱 전용이다(product.md). 랜딩만 모바일에서 읽힌다.

## 4. 검증
- `server/tests/test_deploy.py`: nginx 에 `/app/` fallback·`/` 랜딩·쿼리 301 이 있고 루트 `index.html` fallback 이 없다, Vite `base` 가 `/app/` 이고 nginx 가 `assets/` 를 alias 로 잇는다.
- 수동(web 이미지를 로컬에서 띄워 curl): `/` 200 HTML(제목 포함) · `/?tab=history` 301 `/app/?tab=history` · `/app` 301 `/app/` · `/app/` 200 index · `/app/assets/<번들>.js` 200 immutable · `/app/아무거나` 200 index · `/landing/spreads.png`·`/robots.txt` 200 · `/없는경로` 404.
- 수동(브라우저): 티저에 뜬 코인·경로·퍼센트가 같은 시각 대시보드 표의 행과 같고 입출금이 막힌 행은 없다 · `/api/spreads` 503 이면 티저 구역이 사라진다 · 랜딩이 `/api/ws/`·`/api/health/` 를 부르지 않는다 · 390px 폭에서 가로 스크롤 없음 · 자바스크립트 끄고도 본문이 보인다.
- `cd web && npm run lint && npm run build`, `cd server && ruff check . && pytest -q` 통과.

## 5. 완료 기준
```bash
cd web && npm run lint && npm run build
cd server && ruff check . && pytest -q tests/test_deploy.py
docker build -t marketlens-web-local web && docker run --rm -d -p 8091:80 --add-host server:127.0.0.1 --add-host api:127.0.0.1 marketlens-web-local
curl -sI http://localhost:8091/ ; curl -sI 'http://localhost:8091/?tab=history' ; curl -sI http://localhost:8091/app/ ; curl -sI http://localhost:8091/nope
# Playwright(스크래치패드): / 데스크톱·390px 스크린샷, 티저 카드 수, 네트워크 목록, /api 503 시 티저 숨김
```

## 6. 갱신할 문서
- `docs/context/status.md` — `landing` 행 추가(§3.1 경로·§3.2 구성·정적 파일 갱신은 사람).
- `CLAUDE.md` — 스펙 인덱스 022 행 DONE.
- `docs/context/product.md` — "사용자" 절: 대시보드는 개발자 본인이 데스크톱에서, 랜딩은 처음 온 사람이 모바일에서도.
- `docs/context/architecture.md` — web 항목에 `/` 정적 랜딩·`/app/` 대시보드·Vite base 한 줄, deploy(007) 항목의 nginx 설명에 `/app/` alias·`/` 랜딩.
- `docs/context/dev-setup.md` — `npm run dev` 주소를 `http://localhost:5173/app/` 로.

## 7. 실행 보고
- 만든 것: `web/public/landing.html`·`robots.txt`·`landing/spreads.png`·`history.png`, `web/vite.config.ts` base, `web/nginx.conf` 루트·`/app/` 블록, `server/tests/test_deploy.py` 단언 2개.
- 추측한 지점: 루트의 없는 경로를 404 로 한 것(검색 엔진 중복 색인 방지). 티저의 "열린 경로" 판정은 스프레드 표의 입출금 칸 값을 그대로 썼다. Vite `base` 때문에 dev 주소가 `/app/` 로 바뀐 것은 설정 한 줄의 결과라 그대로 받아들였다.
- 남은 빚: `og:image` 절대 주소에 EC2 IP 가 박혀 있다(도메인 생기면 교체). 스크린샷은 정적 파일이라 화면이 바뀌면 낡는다. 랜딩의 색 값이 theme.css 와 복사본 관계다.
