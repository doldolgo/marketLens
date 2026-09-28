# status.md — 현재 상태

> 실행 세션이 스펙을 끝낼 때마다 갱신한다. "무엇이 실제로 돌아가는가" 만 쓴다.
> 표기: `없음` = 만들어야 하는데 아직 없음. `-` = 그 런타임에 해당 기능이 원래 없음.

## 이 레포
**재구축 완료**: CLAUDE.md §4 의 001~014 가 전부 DONE 이라 아래 표는 모두 새 구조(WebSocket 수집·틱 인계·원문 아카이브)의 런타임을 말한다 — 토대(행·틱·계약)·4거래소 스트림·김프 표(`/spreads`·`/refresh`)·단일 종목 분석 6개 API·이력 조회(`/history/*` 3종·백필)·입출금 상태(3거래소 조회·망 판정)·틱 저장 3계층(Redis → Influx `premium`·`dw_fail`)·원문 아카이브(S3 `raw/`)·수집 실패 이력(`/health/collect`·Influx `collect_fail`)·김프/역프 사건(`premium_event`·`/history/events`·기록 탭)·1분 봉 계층(`candles_1m…1d`·`/history/candles`·기록 탭 차트)·배포(compose 5컨테이너·CI·EC2 배포 워크플로). 각 실행 세션이 끝날 때 그 행을 갱신한다.

| 기능 | server | web | 비고 |
|---|---|---|---|
| collect | 업비트·빗썸 WS 실시간 갱신·마켓 목록 REST 매초(우주 = 국내 ∪ ∩ 해외 ∪)·1초 틱·/health(신선도, 025) | - | 바이낸스 스트림은 012, 바이빗은 019, 비트겟은 020 |
| web-shell | - | 셸·KPI·mock 탭 3종(gap·pp·flow) 동작, 탭·심볼·탭별 필터가 URL 쿼리로 복원 | spreads 탭은 003, history 탭은 005 실데이터 |
| landing | server: GET /landing 요약(live 5초·trail·events 60초 캐시, Influx 는 3초까지만 기다리고 늦으면 직전 값, 두 역할) | web: 정적 landing.html — 경로 카드·다음 경로·"표시된 김프와 먹을 수 있는 김프" 두 단(호가 깊이 예시 `depthGap`·입출금)·7일 사건 수와 최근에 끝난 사건 5개·어떻게 만들었나, 보이는 동안 10초 폴링, `/app/` 버튼 글자 "서비스로 넘어가기" | 스크린샷·og.png 는 사람이 갱신 |
| spreads | `/spreads` 는 Redis `spreads:latest` 를 그대로 반환(두 역할 동일, 요청마다 `spreads:want` 갱신, `notional` 쿼리는 400, Redis 불달 503, 018), 표 계산은 017 게시기가 $1,000 으로 — 전 페어 표, `age`·`status` 는 거래소 스트림 수신 시각 기준(행 자체가 300초 이상 안 바뀌면 그 행의 경과 초 → stale), USDT 시세 미갱신 경고, `spark` 는 009 가 게시한 30분 추이. `/refresh` 는 001 즉시 갱신 트리거 노출(공개 주소에서는 404, 028). 017 — `spreads:want` 가 살아 있을 때만 틱 직후 $1,000 표를 Redis 채널 `spreads`·키 `spreads:latest` 로 매초 게시(`features/spreads/push.py`), 구독 허브(`hub.py`, api·로컬 단일 프로세스 모두)가 diff 를 표 1장당 1회 gzip 해 접속자 전원에게 같은 바이트(바이너리 프레임)로 `/ws/spreads` 브로드캐스트, api 컨테이너는 uvicorn permessage-deflate 끔(2026-09-26 부하 실측 뒤) | 실데이터 탭·`/ws/spreads` 구독(snapshot+delta, 10초 무응답 재연결·백오프, 폴링 fallback 없음 — 막힌 망에선 표 안 뜸)·체결 규모 $1,000 고정·행 클릭 → 기록 탭 | 행 19키(`netFx`·`dayChg` 포함 — 026 기준가 장부). 스프레드 탭 열 9개(변동율·해외가격·거래소 2열, 026). 스프레드 탭이 보는 컨테이너는 api 하나(018). 스파크라인 렌더는 후속. 실거래소 확인(행 수 > 100 등)은 EC2 대기 |
| analysis | 6개 엔드포인트 동작 | - | HTTP 계약 camelCase |
| history | Influx 클라이언트(`core/influx.py`)·`/history/premium`·`/history/streaks`·`/history/streaks/bulk`(Influx 만 읽음 — 불달·토큰 없음이면 503, 메모리 조회는 영향 없음, `start` 없으면 최근 7일 창)·백필 스크립트(upbit×binance 캔들, 앞뒤 빈 구간만) — `premium`·`dw_fail` 쓰기는 009 flusher. 사건 감지기(`core/premium_events.py`, 틱마다 1.0% 진입·0.5% 이탈·1분 초과)·`premium_event`(열린 지 60초·60초마다·닫힐 때, 기동 시 7일 복원)·`/history/events`(닫힌 사건 + 진행 중, `start` 기본 7일 창). 1분 집계기(`core/candles.py`, 틱마다 조합별 OHLC·가격·입출금·막힌 초)·버킷 `candles_1m…1d`(7일/30일/90일/365일/무제한, 기동 시 생성)·사슬 롤업 회차(분 닫힘·60초, 5m→1h→4h→1d, 계층당 12창, 아래 계층 완료 지점까지만)·`/history/candles`(`res`, 요청당 1,440점 상한) | 기록 탭 = `/history/events`(김프/역프 서브탭 · 전 코인 사건 표(상위 30·헤더 정렬) · 선택 심볼 요약·타임라인·로그, 60초 재조회, 스프레드 행 클릭 피벗·초기 BTC). 탭 상단 차트 카드 = `/history/candles`(봉 → 계층 `candles.ts`, 쌍×청크 캐시 `useCandles`, 최신 청크 60초 재조회, 계층 안 접기 `rollup.ts`, lightweight-charts) — 심볼 검색·봉 종류 8종·국내 거래소 다중 선택·USDT 기준 가격·불러오는 중/오류/기록 없음 상태. **015 시안**: 해외 거래소 1개 = 카드 1개(세로로 쌓임, 시간축·십자선 연동 `ChartSync`), 입출금 띠는 거래소별(입금·출금 줄 2개, 경로 밖 줄은 흐림). 해외 선택지 Bybit·MEXC 는 **mock**(`mock.ts`, binance 실봉 변형) — 서버 수집은 binance 뿐. 봉 응답은 거래소별 4상태 키(`domDepositOk` 등)를 내려 경로 밖 칸도 채운다 | 사건 클릭 → 구간 차트는 후속. `premium_event` 첫 점·재기동 복원·백필 실행·bulk 100코인 초과는 EC2 확인 대기 |
| wallet-status | 업비트(JWT)·빗썸(public)·바이낸스(HMAC)·바이빗(HMAC 헤더)·비트겟(public) 조회를 틱 루프가 60초마다 병렬 실행·사이 틱은 캐시, 실패 회차는 그 거래소 전 행 `unknown`·`dwFailed`·`/refresh` 경고, 응답 본문은 010 원문 싱크로, `/spreads` 는 국내 망 기준 판정으로 5필드 | - | 표시는 spreads 탭이 담당. 실키 3-true·`netDom` 채움은 EC2 확인 대기(업비트는 허용 IP 필요) |
| deploy | Dockerfile·compose profile 3개(collect=server / data=redis·influxdb / serve=api·web — api 는 `ROLE=api` 로 Influx 조회 + `/ws/spreads` + `GET /spreads`(Redis 키), 016·017·018), EC2 3대, 배포 워크플로 3타깃(data→collect→serve, 박스별 env 가드 → 미러 동기화 → 자기 profile `up -d --build` → prune), 박스 간 주소는 루트 .env 의 DATA_HOST·COLLECT_HOST, Influx 쿼리 메모리 상한 256MB×3(021)·CI(server·web 무필터)·설정 계약 테스트 `tests/test_deploy.py`, server 에 `UVICORN_ROOT_PATH=/api`(029 — 수집기 API 문서가 `/api` 아래 스키마를 부른다) | caddy TLS 앞단(호스트 `WEB_PORT`·443, `kimptrack.com`·`www` 는 Let's Encrypt 자동 발급·갱신, 그 밖 호스트는 평문, 023 · `caddy/` 디렉터리 바인드·배포 뒤 reload·도메인 블록 접속 로그 → 호스트 `logs/caddy/`, 027) → nginx 서빙(내부 :80, 접속 로그 끔(027), `/api` 는 허용 목록 여섯만(028) — 수집기(`COLLECT_HOST:8000`, 021 템플릿 치환)로 `/api/health`·`/api/health/collect`·`/api/history/events`, api 로 `/api/history/candles`·`/api/landing`·`/api/ws/spreads`(WebSocket 업그레이드), 전부 정확 일치·접두 제거, 나머지 `/api` 는 404 JSON·nginx 버전 숨김 / SPA fallback·index no-store·assets immutable), web 에 관리자 server :8081(게시 안 함 — 028 이전의 전체 `/api` 분기·교차 사이트 403·Cookie·Access JWT 비움·ACAO 지움)·접속 기록 `logs/admin/`(029), serve 에 cloudflared(토큰 파일이 있을 때만, 망 admin), 컨테이너 일곱(로컬 여섯)(030) | 로컬 4컨테이너 검증 완료(2026-09-06), 5컨테이너·api 분기·`stop api` 격리·`/api/spreads` api 분기·`stop redis` 503 로컬 검증 완료(2026-09-14, :8090). 021 profile 3개 통합 기동·nginx `COLLECT_HOST` 치환·Influx 상한·3타깃 워크플로 계약 로컬 검증 완료(2026-09-25, :8090). 028 공개 `/api` 허용 목록·404 JSON·정규화 우회 차단은 로컬 Docker(에코 서버) 검증 완료(2026-09-28), 028 운영 확인 완료(2026-09-29 — 허용 다섯 200·WS 는 canary 4단계 통과, 닫힌 경로와 `--path-as-is` 우회 변형 404 JSON). 029 관리자 server(분기·교차 사이트 403·헤더 비움·JSON 접속 기록)는 로컬 Docker(에코 서버)·브라우저 검증 완료(2026-09-29), 운영 확인은 배포 뒤 사람 몫. 030 cloudflared(secret 없이 serve up·tunnel up 이 serve 컨테이너를 안 바꿈·api·caddy 이름 안 풀림)는 로컬 Docker(가짜 토큰 — 엣지 연결 없음) 검증 완료(2026-09-29), Cloudflare 설정·토큰 파일은 런북 admin-access.md(사람). **EC2 3대 전환 완료(2026-09-25 08:10 UTC, 중단 40초)** — collect `i-004484baaca306d88` c7g.medium(탄력 IP 54.116.230.65) / data `i-093fe9266b10c03d3` t4g.small / serve `i-0feb121f158f966fa` t4g.micro(탄력 IP 3.34.104.16, 공개 주소 `https://kimptrack.com` — 023), Deploy 3타깃 success, 구 박스 `i-0ccec33dba9e27017` 정지 보관(3일 뒤 종료). 남은 사람 작업: collect 에 IAM 프로파일 `marketlens-s3-snapshot` 부착(그 전까지 S3 원문 업로드 실패 경고 — `cloudwatch.md` 관리자 절에서 확인), 업비트 허용 IP 에 54.116.230.65 등록, serve 스왑 1GB(027 — `cloudwatch.md` 7단계) |
| tick-store | 틱 인계 큐(600, 종료 시 비우기 5초 상한) → Redis Stream `ticks` → 60초 flusher(1,000건 페이지 단위로 쓰고 지움) → Influx `premium`·`dw_fail`(멱등), spark 30분 링버퍼·기동 복원 | - | Redis·Influx 불달이어도 앱은 뜬다. 실서버(EC2) 수동 확인은 대기 |
| raw-archive | 거래소 원문 S3 적재 — 시세 프레임은 심볼·종류별, 매초 마켓 목록 응답은 거래소별 분당 마지막 1건, 그 외 전량(거래소·분마다 객체 1개 `…HHMM00Z.jsonl.gz`), 매초 닫기 회차 + 업로드 워커(실패 재시도·256MB 상한) | - | 읽기 API·재생 도구 없음, lifecycle 은 사람 몫. `S3_BUCKET` 없으면 비활성. EC2 에서 객체 적재·1분 객체 크기 실측 대기 |
| health | /health/collect·틱 판정(연결·30초 무수신·샤드) → 실패 구간 추적·collect_fail 쓰기/복원 | 실데이터 탭·5초 폴링·KPI 수집 상태 | 백오프는 후속 스펙. EC2 에서 차단·재기동 복원 수동 확인 대기 |
| binance-stream | WS 3샤드 depth20+miniTicker·exchangeInfo 매초(슬림 질의)·샤드 단위 정체 판정 | - | 해외 최대 20단계 |
| bybit | WS 3샤드 orderbook.200(스냅샷+델타 로컬 북, 행 발행 심볼당 500ms 제한)+publicTrade·instruments-info 매초·JSON ping/pong 감시·샤드 단위 정체 판정·입출금(HMAC 헤더)·`/history/*` `fx=bybit`·`/orderbook/bybit` | 표시명 `Bybit`·기록 탭 Bybit 실데이터(선택된 해외만 조회) | 해외 최대 20단계, MEXC 는 mock |
| bitget | WS 3샤드 books15(200ms 스냅샷, 15단계)+trade·symbols 매초·문자열 ping/pong 감시·샤드 단위 정체 판정·입출금(public, 키 없음)·`/history/*` `fx=bitget`·`/orderbook/bitget` | 표시명 `Bitget`·기록 탭 Bitget 실데이터 | 해외 최대 20단계, MEXC 는 mock |
| wallet-history | server: 틱 4상태 = 006 판정값 + net_dom·net_fx, 봉·사건 점 문자열 2필드, /history/candles·events netDom·netFx | web: 읽기 줄·사건 표·로그 망 표시 | 배포 전 점은 망 null·4상태 코인 단위 |
| slack-alerts | server: Slack 웹훅 알림(기동·수집 60초 구간 발생/복구·ERROR 로그·처리 안 된 500, 키별 10분 억제)·심장박동 `collect:heartbeat`·`/health` 신선도(두 역할, 비정상 503) | - | 외부 uptime 은 런북 uptime-monitor.md, 웹훅·모니터 등록은 사람 몫 |
| observability | server: api WS 접속 수 StatsD 게이지(STATSD_ADDR) | - | caddy 접속 로그(IP /24·검색어·헤더 지움, 폴링 제외) → 박스 안, 처리방침 뒤 CloudWatch Logs 서울 90일 · 에이전트 세 박스 · canary 5분 4단계 · 경보 17개(로그 뒤 18) → Slack · EC2 확인 대기 |
| admin | server: api GET /admin/status(WS 접속 수·Redis·Influx·버전), RedisBus.ping | web: 관리자 화면(web/admin, nginx :8081 — 게시 안 함) | admin.kimptrack.com — Cloudflare Access(OTP)·Tunnel(cloudflared, profile tunnel, Protect with Access) · 운영 확인 대기 |

## 알려진 빚
- (027) 법정 보관 의무 확인 전 — 해당하면 원 IP 보관 방법을 따로 정한다
- (027) 022 의 /?쿼리 → /app/ 301 때문에 utm_* 링크가 대시보드로 간다
- (027) 기록 탭 검색칸 값(URL sym)은 검증 없이 기록된다
- (027) 쿼리 키 삭제 목록은 검색 입력이 늘 때 손으로 맞춘다
- (027) 탭·필터 조작은 서버가 못 본다 — 후속 브라우저 분석
- (027) Caddyfile 은 CI 가 문자열로만 본다 — 깨진 설정은 다음 caddy 재시작에서 사이트를 내린다
- (027) serve 는 t4g.micro + 스왑 — 메모리 측정 뒤 승격 판단
- (027) 016·017·018·021·023·025 의 해당 문장이 027 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기
- (028) `/api/history/events` 는 기간 상한 없이 공개 — `?start=0` 이면 사건 전부를 수집기가 읽는다(수집기 부하 위험). 창 상한은 후속. 운영 실측(2026-09-29, 한 번 부를 때): 1일 1.8MB·6.8초, 7일 13MB·17초, 30일 28MB·33초.
- (028) 003·004·016·018·021 의 해당 문장이 028 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기.
- (029) 관리자 접속 기록(이메일·IP)은 처리방침 게시 전부터 박스 안에 쌓인다 — 후속 처리방침 스펙이 항목·보존을 옮긴다. 줄은 UTF-8 이 보장되지 않는다(0x80 이상 바이트를 그대로 쓴다) — 읽는 도구는 `errors='replace'` 로 푼다.
- (029) nginx-admin.conf 는 CI 가 문자열로만 본다 — 고친 PR 은 로컬 nginx -t 결과를 적는다.
- (029) API 문서 화면 스크립트는 jsDelivr 에서 받는다(버전·SRI 고정 없음).
- (029) 003·004·016·018 의 해당 문장이 029 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기.
- (030) Cloudflare 설정(앱·정책·라우트·AUD)은 대시보드에만 — 런북 기록·드리프트 확인으로 맞춘다.
- (030) cloudflared 의 출구는 serve 보안그룹 자격의 사설망 전체 — Cloudflare 계정 탈취 = Redis·Influx·수집기 접근, 출구 제한은 후속.
- (030) Access 인증 로그는 Free 에서 24시간 — 요청 기록은 029 관리자 접속 기록뿐.
- (030) 021·023 의 해당 문장이 030 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기.
- (022) 랜딩 스크린샷은 정적이다 — 화면이 바뀌면 사람이 다시 찍는다.
- (022) `/landing` 의 `events` 는 7일 `premium_event` 전부를 60초에 한 번 읽어 앱에서 센다 — 2026-09-27 운영 기준 58,601점(역프 54,543), 같은 점을 넣은 로컬 Influx 에서 조회 0.95초·파이썬 CPU 0.36초·메모리 +50MB. 느려져도 응답은 3초 규칙으로 늦지 않지만 조회 비용은 그대로다 — 사건이 더 늘어 api(t4g.micro)에 부담이 되면 방향별·진행 중 수와 코인별 마지막으로 끝난 사건을 Flux 에서 접는 core 조회로 옮긴다(후속 스펙 후보).
- (025) 같은 거래소가 발생 알림 뒤 10분 안에 다시 60초 넘게 끊기면 두 번째 발생·복구 알림은 억제된다(단순함 — 억제 예외 없음). 외부 uptime 모니터는 사람이 등록해야 동작한다.
- (021) Redis 인증 없음 — 보안그룹(collect·serve 그룹만 6379)이 유일한 벽. 이미지는 박스별 빌드라 collect(1 vCPU)는 배포 중 수집이 1~2분 느려진다. Influx 쿼리 **기간** 상한은 미적용(메모리 상한만, 기간 상한은 후속 스펙 후보). serve 박스에서 `web` 이 `api` 보다 먼저 뜨면 nginx 가 업스트림 이름을 못 풀어 한 번 죽고 `restart` 로 다시 뜬다(`depends_on` 을 없앤 대가 — 수 초).
- (019) 원문 아카이브의 바이빗 호가는 델타 프레임을 분당 마지막 1건으로 표본화한 것이라 북을 재생할 수 없다 — 재생이 필요해지면 스냅샷을 따로 남기는 별도 스펙. 비트겟 `books` update 도 같다(020).
- (019) 004 analysis 는 `/orderbook/bybit`·`/orderbook/bitget` 만 열려 있고 나머지 5개 API 의 해외는 바이낸스 고정 — `fx` 선택은 후속.
- (019) EC2 실측 대기: instruments-info 본문 크기·파싱 ms, 샤드당 초당 프레임 수와 collector CPU(바이낸스만일 때와 비교), 1분 원문 객체 크기, 구독 요청 0.1초 간격이 거부되지 않는지.
- (020) 비트겟 `books` update 는 이전 seq 를 주지 않아 누락을 감지할 수 없다 — 북이 어긋나도 다음 재연결 스냅샷까지 모른다. 필요해지면 주기 재구독 별도 스펙.
- (020) EC2 실측 대기: symbols 본문 크기·파싱 ms, 샤드당 초당 프레임 수와 collector CPU(해외 2곳일 때와 비교), 1분 원문 객체 크기, 연결당 채널 200개 안팎에서 끊김 여부(권장 50개 미만 대비), 구독 요청 0.2초 간격이 거부되지 않는지.
- (001) `server/build/`(setuptools 산출물 76파일)와 `server/marketlens_server.egg-info/` 가 git 에 추적돼 있다 — `ruff check .` 가 이 사본(76파일)도 검사한다. 별도 chore 로 지울 것. venv 의 패키지는 editable 설치만 허용한다(dev-setup.md) — 비-editable 사본이 남아 있으면 `server/` 밖 cwd 에서 옛 모듈을 import 한다.
- (010) 원문은 분당 마지막 1건 표본화로 하루 0.3~0.5GB(gzip 후) **추정** — EC2 에서 1분 객체 크기를 실측한 뒤 버킷 lifecycle 을 정한다.
- (006) 망 동일 체인 표·별칭은 2026-09-25 S3 원문(업비트·빗썸·바이낸스·비트겟)으로 늘렸다(비트겟 `ERC20`/`BEP20`/`TRC20`, 빗썸 `BASE_ETH`/`ARB_ETH`/`OP_ETH` 등). 남는 unknown 은 빗썸의 코드=이름 망(`APT`·`ADA`…)과 `AVAX`(C-Chain 미확인) — 실서버를 보며 표를 늘린다(규칙을 느슨하게 풀지 않는다). 바이빗은 수집 박스 `.env` 의 `BYBIT_API_KEY`/`BYBIT_SECRET_KEY` 가 비어 있어 입출금 조회가 매분 실패하고 `/spreads` 바이빗 행의 5필드가 전부 null 이다 — 키는 사람이 넣는다.
- (003·005) `/spreads` 의 `fwd`·`rev` 는 슬리피지 차감 후 순값이고 Influx `premium` 은 차감 전 원값이다. 저장 시점에 체결 규모가 정의되지 않기 때문이며, 그 대가로 `/history/streaks?threshold=` 는 화면 값보다 큰 값을 기준으로 구간을 센다. 백필(캔들 기반)도 원값만 만들 수 있어 아카이브 동질성 쪽을 택했다.
- (013) 과거 `premium` 의 사건 일괄 생성 미완 — `premium_event` 는 배포 시점부터만 쌓인다. 과거분은 `premium` 원본을 코인별로 나눠 도는 별도 스펙.
- (013) 사건 점에 입출금 상태 이력이 없다 — 사건 중 이동 가능 여부·막힌 시각은 후속(013 §7 남은 빚에 설계 메모).
- (014) 배포 전 봉의 입출금 4상태·막힌 초는 코인 단위 값이라 표와 다를 수 있다 — 1m 7일 보관이 지나면 사라진다(5m 이상은 남는다).
- (014) `candles_*` 은 배포 시점부터 — 과거분 없음(초 단위 `premium` 엔 가격이 없다). 초 단위 `premium` 의 보존은 여전히 무제한(별도 결정). 위 계층 구멍을 사후에 메우는 도구는 없다(1m 7일·5m 30일 안이면 재료는 있다 — 후속 스펙 후보). web 의 청크 캐시(`useCandles`)는 상한이 없다 — 심볼을 많이 훑으면 탭이 열린 동안 메모리가 늘어난다(청크 1개 ≤ 360봉).
- (005) Influx `premium` 에는 초 단위 백필(BTC)에 더해 구 스택 PostgreSQL 에서 옮겨 온 2026-05-15~08-29 기록 2,759만 점이 함께 있다(옛 하나은행 환율 기준 값은 업비트 `KRW-USDT` 분봉 기준으로 다시 계산해 넣었다). 이 크기 위에서 **전 구간** `/history/streaks` 는 EC2(4GB)의 Influx 를 재시작시킨다(60초+ 후 504, 2026-08-30·09-07 실측). 그래서 `start` 없는 streaks·bulk 는 최근 7일 창이 기본이고(2026-09-07) FE 도 항상 `start`·`end` 를 붙인다(7일 BTC ≈ 2초). `start` 를 옛날로 주면 여전히 전 구간이 돌 수 있다 — 상한은 두지 않았다. 전 구간 조회가 Influx 를 재시작시켜도 수집은 영향 없음(016 — 조회는 api 컨테이너에서 돈다). 후속 스펙 후보: 오래된 데이터 1m 롤업(3달 기간 옵션 복원 조건). nginx read timeout(60초)도 함께 볼 것.
