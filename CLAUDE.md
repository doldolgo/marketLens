# MarketLens — CLAUDE.md

> 이 문서는 MarketLens 개발의 **유일한 진입점**이다. 모든 문서는 여기서 파생된다.
> 새 컨텍스트에서 작업을 시작할 때는 이 문서 → `docs/context/*` → 지정된 스펙 1개 순서로 읽는다.

## 1. 한 줄 정의
**제품 표시명은 `KimpTrack`**(도메인 kimptrack.com, 2026-09-25) — 화면·탭 제목·랜딩·미리보기 메타에 쓴다. 레포·컨테이너·코드 식별자·이 문서들의 내부 이름 MarketLens 는 그대로 둔다(배포·시크릿에 얽혀 바꿀 이득이 없다).
한국 거래소(업비트·빗썸)와 해외 거래소(바이낸스·바이빗·비트겟) 간 **김치 프리미엄(김프)·역프를 1초 단위로 계산해 보여주는 차익거래 모니터링 대시보드**.
트레이더가 "지금 어느 코인이, 어느 방향으로, 얼마나 벌어져 있고, 실제로 옮길 수 있는가(입출금 상태)"를 한 화면에서 판단하게 한다.

## 2. 레포 구조 (기능 단위)
```
marketlens/
  CLAUDE.md                   ← 이 문서
  docs/
    context/                  살아있는 문서 (갱신형, 개수 고정, 항상 현재 상태)
      product.md              제품 정의·기능 목록·비범위
      architecture.md         런타임·데이터 흐름·계약 규칙·배포
      dev-setup.md            로컬 기동·테스트·린트 명령·env
      conventions.md          코드/커밋/PR 규칙 + 스펙 완료 조건
      status.md               기능별 현재 상태(구현/미구현/mock)·알려진 빚
      db.md                   저장소 모델 — measurement·tag·field·보존·접속
    specs/                    기능별 스펙 (항상 최신으로 유지한다 — 과거 버전은 git 에 남는다)
      TEMPLATE.md
      NNN-<name>.md
    design/                   디자인 원본 — theme.css·index.css(토큰) + reference/(App.tsx·ui.tsx·tabs/ — 화면 구조). 스펙은 값을 옮겨 적지 않고 이 파일을 복사하게 한다
    runbooks/
      execute-spec.md         실행 세션에 줄 프롬프트
      drift-check.md          문서↔코드 어긋남 점검 절차
      ec2-setup.md            EC2 박스 공통 준비 (사람용 체크리스트)
      ec2-split.md            EC2 3대 분리 전환 절차 (사람용, 021)
      ws-loadtest.md          스프레드 웹소켓 부하 측정 절차 + 2026-09-26 전후 기록 (사람용)
      uptime-monitor.md       외부 uptime 감시 등록 (사람용, 025)
      cloudwatch.md           CloudWatch 에이전트·경보·canary·로그 전송 설치 (사람용, 027)
      admin-access.md         관리자 페이지 Cloudflare Access·Tunnel 설정 기록·토큰 교체·접속 기록 회전 (사람용, 030)
      clarity.md              Clarity 대시보드 설정·켜고 끄기·삭제 요청(프로젝트 통째 삭제) (사람용, 033)
  server/                     FastAPI 앱 (Python 3.12)
    app/
      core/                   공유 인프라 — 스트림 커넥터·메모리 저장소(LiveStore)·틱 루프·김프 계산·망 매칭·Influx·Redis·S3 클라이언트 (모듈 이름은 개발 후 architecture.md "현재 구조"에)
      features/<name>/        기능 1개 = 폴더 1개: router.py service.py models.py tests/ (admin — api 역할의 관리자 상태, 029)
      main.py
    tests/                    기능 폴더 밖의 통합 테스트만
  web/                        Vite + React 19 앱 (TypeScript)
    src/
      shared/                 config·theme·format·ui 조각
      features/<name>/        기능 1개 = 폴더 1개: Tab.tsx api.ts types.ts
      App.tsx main.tsx
    admin/                    관리자 화면(정적 index.html·admin.js·admin.css, 빌드 없음 — 기능 폴더 규칙의 예외, 029·036)
    scripts/                  랜딩 글꼴 서브셋 스크립트·글자 목록(uv run, 빌드에 들지 않음 — 022)
    nginx-admin.conf          관리자 server(:8081, 게시 안 함) 템플릿 — 전체 `/api` 분기·교차 사이트 403·접속 기록(029)
  caddy/                      Caddyfile — serve 박스 TLS 앞단·접속 로그 (디렉터리째 바인드, 023·027)
  ops/                        박스에 올리는 설정(앱 코드 아님) — cloudwatch/(에이전트 JSON)·canary/(Lambda 점검 스크립트) (027)
  secrets/                    터널 토큰 파일 cloudflared-token — git 무시, serve 박스에만 (030)
```
- 화면이 있는 기능만 `web/src/features/<name>` 폴더를 가진다. 이름은 `server/app/features/<name>` 과 같게 한다.
- `collect` 는 기능 폴더가 아니라 `core/` 에 산다. `wallet-status`(Python 패키지는 `wallet_status`) 는 BE 전용이다(화면은 spreads 표에 얹힌다).
- 기능 간 import 금지. 공유는 `core/`·`shared/`를 통해서만.

## 3. 개발 방식 (문서 기반)
1. **설계 세션**: 사람 + Claude가 대화하며 `docs/specs/NNN-<name>.md`를 쓴다. 스펙은 **사람이 끝까지 읽는 문서**다 — 동작·계약·규칙·엣지만 쓰고, 코드 구조는 쓰지 않는다.
2. **실행 세션**: 아무것도 모르는 새 컨텍스트가 `CLAUDE.md` + `docs/context/*` + 스펙 1개만 읽고 구현한다. 테스트도 스펙을 보고 직접 쓴다. (`docs/runbooks/execute-spec.md`)
3. 실행 중 스펙에 없는 결정이 필요한 상황은 **자주 나오고 정상**이다. 그때 실행 세션은 멈추고 묻는다. 사람과 함께 **스펙(또는 context 문서)을 먼저 고치고**, 고친 문서를 기준으로 그 자리에서 이어간다. 롤백·재실행은 하지 않는다. 목적은 "코드에만 있고 문서에 없는 결정"을 남기지 않는 것이다.
4. 스펙 완료 조건에는 **코드 + 테스트 + `docs/context/*` 갱신**이 모두 포함된다.
5. 더 이상 맞지 않는 문서·문구는 지운다. 예전 내용이 궁금하면 git 기록을 본다.

### 문서에 미리 쓰는 것 / 개발하며 정하는 것
기준: **"이 문장이 앞으로의 작업을 구속하는가?"**
| 층 | 예 | 언제 | 어디 |
|---|---|---|---|
| 개념·규칙·툴 | 커넥터 공통 인터페이스, 메모리가 진실, Influx 2.7 | 미리 | `docs/context/architecture.md` 원칙 절 |
| 동작·계약 | API 모양, 수식, 엣지 | 미리 | `docs/specs/` |
| 구현 구조 | 어떤 클래스·모듈, 파일 배치 | **개발 후** | `architecture.md` "현재 구조" 절 + 스펙 §7 |

### 스펙 크기·스타일 규칙
- 스펙 1개 = 실행 세션 1개에서 끝나는 양. 본문 **200줄 이내**, 사람이 10분 안에 읽는 길이.
- 자기완결: 다른 스펙을 읽을 필요 없게 필요한 계약은 복사한다.
- 실행 세션이 **스스로 정하는 것**: 바꿔도 동작이 안 변하고 그 기능 폴더 안에만 영향이 있는 것 — 클래스·함수 분리, 변수명, 파일 내부 배치, 내부 자료구조. CLAUDE.md §2 의 기능 폴더 규약은 지킨다.
- 실행 세션이 **멈추고 사람에게 묻는 것**(§3-3 절차로 스펙에 적고 이어간다):
  1. 다른 기능·스펙이 의존하게 될 것 — `core/`·`shared/` 공개 함수, 저장 데이터 모양, API 응답 키
  2. 라이브러리 추가
  3. 트레이드오프가 있는 것 — 캐싱, 재시도·타임아웃, 동시성, 정밀도, 메모리 vs 속도
  4. 스펙이 말하지 않은 **동작** — 엣지에서 뭘 돌려줄지, 순서, 기본값
  묻는 형식: 선택지 2~3개 + 각 장단 + 추천 1개. 답이 오면 스펙에 먼저 적는다.
- 표는 2열, 셀은 한 토큰. 의미·이유는 표 밖 문장으로 쓴다.

## 4. 스펙 인덱스
| 번호 | 이름 | 상태 | 범위 |
|---|---|---|---|
| 001 | collect | DONE | 업비트·빗썸 WebSocket 실시간 수집 → 메모리, 마켓 우주, USDT 시세, 1초 틱, 원문 싱크·인계·판정 계약, `/health` |
| 002 | web-shell | DONE | 화면 골격·탭·KPI·테마·mock 탭(갭/선선갭/입출금레이더) |
| 003 | spreads | DONE | 김프 표 — `/spreads`(Redis 표 반환, 018) `/refresh`(즉시 갱신 트리거) + 스프레드 탭 |
| 004 | analysis | DONE | 단일 종목 분석 — premium·scan·matrix·orderbook·slippage·arbitrage (BE 전용) |
| 005 | history | DONE | Influx `premium` 점 규칙·`/history/*`(창 기본이자 상한 — streaks 7일·premium 1주·bulk 1시간)·백필 + 기록 탭 사건 로그 실데이터 (쓰기는 009) |
| 006 | wallet-status | DONE | 거래소 입출금 상태·망 기준 판정 → 스프레드 표에 반영 |
| 007 | deploy | DONE | Docker·compose(server·api·web·caddy·influxdb·redis + cloudflared(profile tunnel))·CI·EC2 배포 |
| 008 | usdt-staleness | DONE | `/spreads` USDT 시세 미갱신 경고 (BE 전용) |
| 009 | tick-store | DONE | 3계층 저장 — LiveStore 틱 슬롯 → Redis → 60초마다 Influx 전량 적재·비움, `spark` |
| 010 | raw-archive | DONE | 거래소 원문(WS 프레임·REST 응답)을 S3 `raw/` 에 — 시세 프레임·매초 마켓 목록은 분당 마지막 1건, 그 외 전량, 거래소·분마다 객체 1개 (BE 전용) |
| 011 | health | DONE | 거래소별 수집 실패 구간 이력·분류 → `/health/collect` + 수집 상태 탭 실데이터, Influx `collect_fail` 복원 |
| 012 | binance-stream | DONE | 바이낸스 WS 3샤드 depth20+miniTicker → 해외 호가 최대 20단계, exchangeInfo 심볼 (BE 전용) |
| 013 | premium-events | DONE | 틱에서 김프/역프 사건 감지(1.0% 진입·0.5% 이탈·1분 초과) → Influx `premium_event` 1건 1점 + `/history/events` + 기록 탭 전 코인 사건 표 |
| 014 | premium-1m | DONE | 틱에서 (국내·해외·코인) 1분 OHLC·가격·입출금 집계 → 버킷 `candles_1m…1d`(사슬 롤업, 보관 7일/30일/90일/1년/무제한) + `/history/candles`(`res`, 1,440점 상한) + 기록 탭 차트 실데이터 |
| 016 | process-split | DONE | 서버 `ROLE`(collector | api) — Influx 조회(`/history/premium`·`streaks`·`streaks/bulk`·`candles`, 018 부터 `/spreads` 도)를 별도 컨테이너 `api` 에서, nginx 경로 분기, compose 5컨테이너 (BE·인프라) |
| 017 | spreads-push | DONE | 스프레드 표 WebSocket 푸시 — 수집이 매 틱 $1,000 표를 만들어 Redis 채널로(2026-09-26 부터 접속자와 무관), `api` 가 구독해 바뀐 행만 `/ws/spreads` 로 접속자 전원에 같은 바이트, FE 폴링 대체(fallback 폴링 없음), 체결 규모 $1,000 고정 |
| 018 | spreads-serve | DONE | `GET /spreads` 를 `api` 가 Redis `spreads:latest` 로 답하고 nginx `/api/spreads` 를 `api` 로 — 스프레드 탭이 보는 컨테이너는 api 하나, `notional` 쿼리 삭제, 요청마다 `spreads:want` 갱신 (BE·인프라) |
| 019 | bybit | DONE | 바이빗 USDT 현물 추가 — WS 3샤드 orderbook.200(스냅샷+델타, 행 발행 500ms 제한)+publicTrade, instruments-info 매초, 입출금(HMAC), 우주 = 국내 ∪ ∩ (바이낸스 ∪ 바이빗), `/history/*` `fx=bybit`, web 표시명·기록 탭 Bybit 실데이터 |
| 020 | bitget | DONE | 비트겟 USDT 현물 추가 — WS 3샤드 books15(스냅샷)+trade, symbols 매초, 입출금(public·키 없음), 우주 = 국내 ∪ ∩ (바이낸스 ∪ 바이빗 ∪ 비트겟), `/history/*` `fx=bitget`, web 표시명·기록 탭 Bitget 실데이터 |
| 021 | infra-split | DONE | EC2 3대 분리 — compose profile 3개(collect=server c7g.medium / data=redis·influxdb t4g.small / serve=api·web t4g.micro), 박스 간 사설 IP(루트 .env DATA_HOST·COLLECT_HOST), nginx 업스트림 주입, 배포 3타깃(data→collect→serve), 런북 ec2-split.md (인프라) |
| 022 | landing | DONE | 정적 HTML 랜딩 `/` — 실시간 경로 카드·7일 사건·김프 뜻·계산 방법·질문과 답, 요약 API `GET /landing`(api), 대시보드는 `/app/`. 검색 구성(검색어 제목·설명·h1, JSON-LD, data-nosnippet, 자체 서브셋 글꼴, 아이콘, robots·sitemap, 대시보드 noindex, 404, www 301) |
| 023 | domain-tls | DONE | `kimptrack.com`·`www` HTTPS — serve 박스에 caddy 컨테이너(호스트 80·443, Let's Encrypt 자동 발급·갱신, 볼륨 보존), web 은 호스트 비공개, `www` 는 apex 로 301(022), 탄력 IP 직접 접속은 평문 유지(noindex), DNS 는 Cloudflare(프록시 끔) (인프라·serve 전용) |
| 024 | wallet-history | DONE | 틱 입출금 4상태 = 006 망 판정값 + 망 이름 2개 → 1분봉·사건 점 `net_dom`·`net_fx`, `/history/candles`·`events` 에 `netDom`·`netFx`, 기록 탭 읽기 줄·사건 표·로그에 망 표시 |
| 025 | slack-alerts | DONE | Slack 웹훅 알림 — 기동·수집 실패 구간 60초 발생/복구·ERROR 로그·처리 안 된 500(키별 10분 억제, 새 라이브러리 없음), 수집기 심장박동 `collect:heartbeat`, `/health` 신선도 판정(비정상 503), 외부 uptime 감시 런북 |
| 026 | day-change | DONE | 국내 거래소·코인별 KST 00시 첫 체결가 장부(Redis `dayopen:<날짜>`, 재기동 유지) → `/spreads` 행 `dayChg`(19키) + 스프레드 탭 열 개편(심볼·변동율·국내가격·해외가격·해외거래소·국내거래소·김프·입출금·네트워크) |
| 027 | observability | DONE | caddy 접속 로그(IP /24·검색어·헤더 지움, 폴링 제외, `caddy/` 디렉터리 바인드·배포 뒤 reload) · api WS 접속 수 StatsD 게이지 → CloudWatch 서울(에이전트 세 박스, 로그 90일은 처리방침 뒤, canary 5분 4단계, 경보 17개(로그 뒤 18) → Slack) · serve 스왑 1GB (인프라) |
| 028 | api-allowlist | DONE | 공개 nginx `/api` 를 허용 목록 여섯(정확 일치)으로 — 수집기 `/api/health`·`/api/health/collect`·`/api/history/events`, api `/api/history/candles`·`/api/landing`·`/api/ws/spreads`, 나머지 404 JSON(API 문서·분석 6개·`/refresh`·history 무거운 조회·`/spreads` 닫힘), 정규식 location 없음·정규화 우회 방어, 웹·감시 호출 경로 대조 테스트. 022 재작업 머지 뒤 (인프라) |
| 029 | admin | DONE | 관리자 server — web nginx :8081(게시 안 함)·정적 화면(헬스·수집 상태·WS 접속 수·즉시 갱신), 닫힌 API 전체 분기·교차 사이트 403·JSON 접속 기록, api `GET /admin/status`·`RedisBus.ping`, 수집기 `UVICORN_ROOT_PATH=/api`. 028 머지 뒤 (BE·web·인프라) |
| 030 | admin-tunnel | DONE | `admin.kimptrack.com` — cloudflared(profile tunnel, 전용 망, 토큰 파일 secret, 배포 시 파일이 있을 때만)·Cloudflare Access(이메일 OTP)·Protect with Access, 런북 admin-access.md (인프라) |
| 031 | events-window | TODO | 공개 `/history/events` 창 상한 90일(기록 탭 최장 기간 3달과 같음) — 넘으면 Influx 를 읽기 전에 400(`detail.limitSec`, 014 와 같은 모양), 차트 음영 조회는 최근 90일까지. 기간 버튼 그대로. 요약 응답·속도 제한·api 이관은 후속 (BE·web) |
| 032 | privacy | DONE | 개인정보 처리방침 `/privacy`(번들 밖 정적·외부 자원 0·CSP·no-cache, `/privacy.html`·`/app/privacy.html` 301)·화면 분석 동의 관리(Clarity 는 동의 방식 — 사람 결정 2026-10-01, 수집·이용·제공·국외 이전 세 칸을 따로 받아 셋 다일 때만 `kt.analytics` `granted`+판 `kt.analytics.v`·다른 판은 다시 묻기·없음 = 기본 꺼짐·GPC 우선, [선택한 대로 저장]·[모두 거부]·[동의 철회], 철회 뒤 삭제 요청은 프로젝트 통째 삭제)·랜딩 바닥 nav·대시보드 헤더 링크·sitemap, caddy 하루 회전·100개·오류 줄 IP 삭제(머지 뒤 caddy 다시 만들기)·관리자 기록 매일 90개. 사람 값 채움(2026-10-01) — 법률 확인(§7) 뒤 머지 = 게시 (web·인프라) |
| 033 | clarity | DONE | Microsoft Clarity(동의 방식) — `public/clarity.js` 한 곳(ID 들어 있음 — 032 머지 뒤 이 PR 머지가 켜기), 랜딩·대시보드의 화면을 막지 않는 동의 안내 띠(정하지 않음 — 지금 판의 granted·denied 가 아니고 GPC 아닐 때, 032 와 같은 세 칸·칸마다 알릴 사항 '내용 보기'·접힌 띠·펼쳐도 높이 화면 절반까지·[선택한 대로 저장]·[모두 거부])·'화면 분석 설정' 링크(→ /privacy#consent), 세 칸 모두 동의한 방문자만 consentv2 광고 거부·분석 허용, 안내 판은 privacy.html 과 같은 값, 다른 탭의 철회는 곧바로 새로고침·동의는 그 자리에서 켬, 검색어 URL 제외·필터 URL 쓰기 우회·`sym` 형식, 탭 태그·이벤트, 런북 clarity.md(삭제 요청 = 프로젝트 통째 삭제) (web) |
| 034 | monitoring-ops | DONE | 수집기 관리자 피드 — `/admin/aws`(경보·24시간 지표·canary·예산)·`/admin/alerts`(보낸 Slack 알림 `alerts:log` + 경보 이력 7일), 부분별 state(자격 없음 = 연결 안 됨)·요청 있을 때만·전용 스레드·ARN 가림, IAM 읽기 정책 런북 (BE·인프라) |
| 035 | monitoring-visits | DONE | api 관리자 피드 — `/admin/access`(caddy 로그 요약 — 창·분류·방문자는 038)·`/admin/clarity`(Data Export·Redis 캐시 — 간격·정규화·페이지×기기 묶음은 040), 토큰 런북. 032·033·034 뒤 (BE·인프라) |
| 036 | admin-v2 | DONE | 관리자 화면 v2 — 한 페이지 개요·수집·인프라·알림·접속·비용·도구, 빠른 10초·느린 60초(보이는 동안만), 부분별 상태 칸·SVG 직접·빌드 없음. 034·035 뒤 (web) |
| 037 | privacy-v2 | DONE | 처리방침 v2 — 서버 접속 기록 문단(접속 요약 최근 30일까지·IP 앞부분으로 나라·망 종류 추정(DB-IP Lite)·하루 한 번 세는 되돌릴 수 없는 값, 시행일 전 기록에는 쓰지 않음)·CloudWatch '보낸 때부터 90일', 맨 위 변경 안내·12절 대조표·이전 판 `privacy-20261001.html`(정적·noindex), 시행일 한 곳 `core/config.py` `PRIVACY_V2_EFFECTIVE`(PR 올린 날 + 9일, 머지 마감 시행일 − 8 의 18:00 KST — PR 제목·본문)·test_privacy 대조. 서버 동작·Clarity 동의 판은 그대로 (web·BE) |
| 038 | access-v2 | DONE | api `/admin/access` 의 `window` 24h·7d·30d(7d·30d 는 `PRIVACY_V2_EFFECTIVE` 0시부터 — 그 전·목록 밖은 24h, 시작은 게이트로 자름) — 회전 파일별 메모리 캐시(압축 풀린 100MiB 까지·1시간 안 부르면 비움)·줄 분류(종류 여덟·탐색 경로·운영자 흔적)·(가린 IP, UA) 짝(날마다 바뀌는 열쇠의 BLAKE2b)으로 확인(JS 신호)/브라우저 모양 '날마다 센 방문자'·다시 온·채널·기기·OS·브라우저·인앱 — 짝 처리는 게이트 뒤 KST 날만(그 전 `visitors` 는 `before_gate`, 줄 단위 집계만), WS 5xx 따로, 응답은 더하기만 + admin.js 분모 한 줄. 037 위 (BE) |
| 039 | access-geo | DONE | 접속 요약 `geo` — 게이트 뒤 DB-IP Lite(Country·ASN) 월판의 IPv4 를 api 메모리에만(디스크·새 라이브러리 0, 이번 달 404 면 지난달), 망 종류 telecom_kr·telecom·cloud·other·unknown(ASN 표·조직 이름 낱말 — 번호·이름은 버림), 게이트 뒤 KST 날 짝에만 나라·망 종류, 방문자가 적은(3 미만) 나라는 (기타)로 묶음(그때 국내 통신사 칸도 통신사 칸에 합침), IPv6 는 수만. 038 위 (BE) |
| 040 | clarity-v2 | DONE | api `/admin/clarity` v2 — 기본 요약 4시간(`numOfDays=1`)·페이지×기기 묶음 12시간(직전 72시간, `URL`·`Device` — 어떤 24시간에도 합 8회, 사람 몫 2회), Redis 기록 둘(`admin:clarity`·`admin:clarity:pages` — 페이지 묶음은 주소 없이)·메모리 기록으로 하루 한도 지키기, 정규화 `summary`(스크롤 깊이·머문 시간·불만 신호 여섯)·`countries`·`pages.groups`(페이지 종류×기기, 세션 가중 평균), 대시보드 주소는 `?tab=<id>` 만 남김, 응답은 더하기만. main 위 — 037~039·041 과 나란히 (BE) |
| 041 | admin-explain | DONE | 관리자 화면 통계 설명 — 일곱 절마다 접힌 '이 절 읽는 법'·덩어리 끝 '이 칸 뜻'(정적 HTML, 다시 그리는 칸 밖), 실패 종류·경보 꼬리·대시보드 탭 한국어 이름표, 타일 부제, 오해 문구 고침(억제된 알림·성공률 소수 1자리·버전 칸 삭제·AWS 계정 종료 문구 삭제), 034·036 문장. main 위 — 042 보다 먼저 (web) |
| 042 | admin-traffic | TODO | 관리자 접속 절 v3 — 서버 기록 창 24시간·7일·30일(JS 변수, 바꾸면 접속 경로만 곧바로), 질문 여섯 덩어리(몇 명·누가·어디서·언제·무엇을·문제)와 데이터로 만든 답 문장, '날마다 센 방문자' 확인(하한)/브라우저 모양(상한), 종류 여덟·나라·망 종류·들어온 길·요일×시간 열지도, 처리방침 v2 시행 전 빈 상태, DB-IP 표시 링크. 039 위 — 041 머지 뒤 main 을 받는다 (web) |
| 043 | admin-clarity-view | TODO | 관리자 Clarity 덩어리 v3 — 요약 타일·불만 신호·나라, 랜딩 도달 추정(평균 스크롤 깊이 하나로 푼 모형 — 세션 5 미만·첫 화면 안이면 숨김, 랜딩 윤곽 그림), 대시보드 탭별 진입(서버 기록)·Clarity 활동, 랜딩 절 표 `LANDING_SECTIONS`·측정 스크립트(Node·로컬 Chrome — CI 밖). 040·041·042·랜딩 v3 머지 뒤 (web) |

실행 순서 = 번호 순. 034·035 는 한 설계를 둘로 나눈 짝 스펙이다(034 → 035). 031 은 032~036 의 계약에 기대지 않아 그 앞뒤 어디서 해도 된다. 037~043 은 번호 순이 아니다 — 037 을 가장 먼저 PR 로 올린다(시행일이 PR 을 올린 날에 묶인다). 권장 머지 순서는 037 → 041 → 040 → 038 → 039 → 042 → 043 이다. 038 은 037 위, 039 는 038 위, 042 는 039 위에 쌓고 042 는 041 머지 뒤 main 을 받는다 — 038·039 는 037 의 시행일 상수(`PRIVACY_V2_EFFECTIVE`)가 연 날부터 동작하고, 037 보다 먼저 머지하지 않는다. 040·041 은 main 위에서 나란히 간다. 043 은 040·041·042·랜딩 v3(022 — 절 id) 머지 뒤 main 에서 시작한다. 지금 IN_PROGRESS 인 것: 없음.
상태: TODO(내용은 확정, 아직 구현 전) → IN_PROGRESS(구현 중) → DONE(구현·검증 끝).
**스펙은 항상 지금 동작과 같아야 한다. DONE 이 된 뒤라도 동작을 바꾸고 싶으면 그 기능의 스펙을 그냥 고치면 된다.** 단, 스펙만 고치면 문서와 코드가 어긋나므로 — 같은 PR 에서 코드와 테스트도 스펙에 맞게 고치고, 그 기능의 §4 검증을 다시 통과시켜야 한다(§6). 변경이 여러 기능에 걸치면 관련 스펙을 전부 고친다. "예전에는 ~였다" 같은 설명은 남기지 않는다 — 과거 버전은 git 에서 보면 된다.

## 5. 접근 규칙 (기본 세팅 — 모든 세션 공통)
읽기 순서는 문서 머리의 규칙 그대로: 이 문서 → `docs/context/*` → 지정된 스펙 1개.

접근 금지 (예외 없음):
- **`.env`** — `server/.env` 를 포함한 모든 `.env*` 파일(`.env.example` 제외)을 읽지도, 쓰지도, 출력하지도 않는다. 값을 화면에 내지 않고 프로그램에 넘기는 것(`docker compose --env-file server/.env …`)만 허용. 키 목록의 진실은 `server/.env.example` 이고, 값을 만들고 바꾸는 것은 **사람**이다. 키·토큰·시크릿 값은 코드·문서·로그·커밋 어디에도 적지 않는다.
- **이 레포 밖의 코드** — 기존 marketlens-be·fe 를 포함해 읽지 않는다. 필요한 계약은 스펙 안에 복사돼 있다. 막히면 추측하고 스펙 §7 실행 보고에 적는다.
- **다른 사람 담당 스펙** — 결함을 발견해도 고치지 않는다. `파일:절 — 문서 주장 → 실제` 형식으로 보고만 한다.

허용:
- 수정 가능: `server/` `web/` `ops/` `caddy/` `docs/context/*` `docs/runbooks/*` 담당 스펙(DONE 이어도 — 단 §4 규칙대로 코드·테스트와 함께) `CLAUDE.md` 스펙 인덱스 상태. `docs/design/*` 원본 변경은 사람 합의 후.
- 거래소 실호출은 스트림 커넥터·틱 루프·백필 스크립트 코드가 한다. 세션이 직접 부르는 것은 스펙 §4 가 명시한 실서버 확인 항목뿐이고, 개발 망에서 거래소 도메인이 차단되면 EC2 에서 돌린다(dev-setup.md 로컬 메모).

## 6. 문서 변경 검증 루프 (md 를 바꾸면 반드시 돈다)
문서와 코드는 같은 진실을 가리켜야 한다. `docs/**/*.md`·`CLAUDE.md` 가 바뀌면:
1. **구현 전 기능의 스펙**(TODO): 돌릴 코드가 없다 — 의존 스펙에서 복사한 계약이 원본과 같은지 교차 확인만 한다.
2. **구현이 끝난 기능의 스펙·context 문서**: 스펙을 고친다는 건 동작을 바꾸겠다는 뜻이다. 같은 PR 에서 코드·테스트를 스펙에 맞게 고치고, 그 기능의 §4 검증을 **다시 돌려** 통과한 뒤에만 커밋한다 — server `ruff check . && pytest -q`, web `npm run lint && npm run build`, 해당되면 curl 스모크. 실패 = 문서↔코드 어긋남이므로 옳은 쪽을 사람이 정하고(§3-3) 맞춘다.
3. 구현 중 스펙의 빈틈을 만나면 코드를 먼저 쓰지 않는다 — 스펙을 먼저 고치고, 고친 문구로 테스트를 쓴다(§3-3).
4. 주기 점검은 `docs/runbooks/drift-check.md`(스펙 3개 완료마다).

CI(스펙 007)는 경로 필터 없이 항상 server·web 두 job 을 돌린다 — **문서만 바뀐 PR 도 전체 검증을 통과해야 머지된다.** 레포의 `.claude/settings.json` 훅이 md 변경 시 이 절차를 상기시킨다.
