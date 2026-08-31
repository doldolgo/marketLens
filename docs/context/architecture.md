# architecture.md — 구조

> 이 문서는 현재의 핵심 설계 결정과 구현 경계를 쓴다. 기능별 구현 상태는 `status.md` 가 말한다.

## 핵심 설계 결정
- **실시간 시세의 기준은 `live_store`다.** 실시간 조회 API는 메모리만 읽고, `/history/*`만 InfluxDB를 조회한다.
- **거래소 시세 호출은 모두 `Collector`를 통한다.** 백그라운드 루프와 수동 `/refresh`가 같은 수집 경로를 사용한다. 조회 API는 거래소를 직접 호출하지 않는다.
- **시세 커넥터는 공통 인터페이스를 구현한다.** 거래소별 응답 형식과 예외 처리는 각 커넥터 내부에서 처리한다. 새 거래소를 추가할 때 collector의 수집 로직은 수정하지 않는다.
- **도메인 계산은 가능한 한 순수 함수로 작성한다.** 네트워크·DB 같은 I/O 의존성은 인자로 주입하고 변경 가능한 전역 상태를 사용하지 않는다.
- **입출금 상태는 `open(true) / closed(false) / unknown(null)` 세 상태를 구분한다.** `unknown`을 `open`으로 처리하지 않는다.
- **현재 서버는 uvicorn worker 1개만 사용한다.** `live_store`가 프로세스 내부 메모리이기 때문에 다중 worker를 사용하려면 프로세스들이 공유하는 외부 저장소로 먼저 이전해야 한다.

## 런타임 구성
- **server/**: Python 3.12, FastAPI, httpx, influxdb-client, pydantic v2, pydantic-settings. 로컬 포트 8000.
- **web/**: React 19, TypeScript, Vite. 런타임 의존성은 react·react-dom뿐이다. 로컬 포트는 5173이고, 배포 컨테이너의 nginx는 80번 포트를 사용한다. 호스트 포트는 `WEB_PORT`로 정한다.
- **저장소**: InfluxDB 2.7 OSS(org·bucket `marketlens`, Flux). 김프 이력의 시간 버킷 집계에 사용한다. 모델은 `db.md`가 정의하며 테스트에서는 InfluxDB를 띄우지 않는다.

## 데이터 흐름 (BE)
```
[거래소 REST]  ── 1초마다 ──▶  collector  ──▶  live_store (메모리, 통째 교체)
                                                                │
                         실시간 조회 API ◀── 읽기 ───────────┘
                                                                │
                              60초마다 persist 루프 ──▶ InfluxDB
                                (`premium` 에 fwd/rev 쓰기, 입출금 조회 실패 시 `dw_fail` 1점)
```
- 입출금 상태 API는 수집 루프가 60초 주기로만 조회해 캐시한다. 키가 없으면 `null`(모름). 망 판정은 `/spreads`에서 하고, 빗썸은 키가 필요 없다.
- `GET /health`와 수집 루프는 기능 폴더가 아니라 앱 진입점 소관이다. `/health`는 프로세스 liveness만 나타내며 수집 최신성이나 InfluxDB 상태를 보장하지 않는다.
- USDT 시세는 별도 호출 없이 국내 거래소의 KRW 마켓 일괄 호가 중 USDT 항목에서 추출한다. 최우선 매도호가가 rate_ask(USDT 살 때), 최우선 매수호가가 rate_bid. 거래소별 ask/bid. 커넥터는 USDT 를 특별 취급하지 않는다. 은행 환율은 어디에도 쓰지 않는다(product.md 용어).
- 시세 호출 실패 시 `live_store`는 해당 거래소의 직전 시세를 유지하고 `age`가 커진다. 입출금 상태 조회 실패는 `unknown`으로 기록한다. FE는 age로 stale을 표시한다. 재기동 시 InfluxDB에서 되읽는 폴백은 없으며 메모리는 첫 수집으로만 채워진다.

## 데이터 흐름 (FE)
- `fetch` 폴링만 사용. 상태관리·라우터·스타일 라이브러리 없음.
- API base: `VITE_API_BASE` (미설정 시 `/api`). dev 는 vite proxy `/api → http://localhost:8000` (prefix strip), 배포는 nginx `/api/ → server:8000/`.
- 폴링 실패 시 직전 데이터 유지.
- 셸의 공유 피드가 탭 공통 데이터를 들고, 1.5초 tick 은 셸이 돌린다. `/spreads` 1초 폴링은 spreads 기능(003)이 제공한다.

## 계약 규칙 (BE ↔ FE)
- BE 내부는 snake_case를 사용한다. HTTP JSON 키와 복합어 쿼리 파라미터는 모든 엔드포인트에서 camelCase를 사용한다. 정확한 스키마는 각 기능의 모델과 타입이 정의한다.
- 응답 압축: GZip 미들웨어를 앱 전역에 켠다 — `/history/streaks/bulk` 같은 수 MB JSON 때문. 설정은 001 의 앱 골격 소관.
- FE가 소비하는 응답 스키마를 변경할 때는 같은 변경에서 BE 모델과 FE 타입을 함께 수정한다.
- 비즈니스 에러는 `{"error": {"code": str, "message": str, "detail": any}}` 형식이다. 인증 실패와 FastAPI 요청 검증 실패(422)는 `{"detail": ...}` 형식을 사용한다.

## 기능 폴더 기본 구조
기능에 필요한 파일만 둔다. BE 전용 기능은 web 폴더가 없고, API를 호출하지 않는 mock 화면은 `api.ts`·`types.ts`가 없어도 된다.

```
server/app/features/<name>/
  router.py     HTTP API가 있으면 APIRouter를 선언한다.
  service.py    기능 흐름을 조립한다. 계산은 가능한 한 순수 함수로 두고 I/O 의존성은 인자로 받는다.
  models.py     요청·응답 모델이 있으면 둔다.
  tests/        기능 테스트. 외부 네트워크는 fake로 대체한다.
web/src/features/<name>/
  Tab.tsx       해당 기능의 화면.
  api.ts        실제 API를 호출할 때 fetch 함수와 표시 전용 가공을 둔다.
  types.ts      실제 API 응답을 사용할 때 BE 응답과 맞는 타입을 둔다.
```

## 배포 토폴로지
EC2 1대. 루트 `docker compose up -d --build`로 server·web·influxdb 컨테이너를 실행한다. 컨테이너는 compose 기본 네트워크를 사용하며 InfluxDB 포트는 호스트에 공개하지 않는다. PR CI는 server lint·format·pytest와 web lint·build를 실행한다. main push는 EC2에 SSH로 접속해 배포한다. 상세는 스펙 007(deploy).
같은 EC2 에 기존 marketlens-be(:8000)·fe(:80) 가 운영 중이라 이 레포의 web 은 `WEB_PORT=8080` 으로 공존한다. 80 이관·서버 분리(DB/파싱 분리)는 추후 별도 스펙으로 검토한다.
배포 workflow의 성공은 EC2 명령 실행 성공만 뜻한다. 외부 URL 확인과 실패 시 자동 롤백은 아직 없다.

## 현재 구조 (개발 후 갱신 — 실행 세션이 §7 보고와 함께 채운다)
스펙이 DONE 될 때마다 주요 모듈과 역할을 짧게 기록한다. 문서와 코드가 다르면 사람이 올바른 쪽을 결정하고 같은 변경에서 둘을 맞춘다.
- **collect (001)**: `core/collector.py`(사이클 5단계·락·1초 루프), `core/live_store.py`(거래소별 스냅샷·USDT 시세), `core/connectors/`(공통 인터페이스와 거래소별 구현), `core/rows.py`(행 조립), `core/models.py`, `core/errors.py`, `core/config.py`. 앱 골격과 수집 루프 기동은 `app/main.py` lifespan이 담당한다.
- **web-shell (002)**: `shared/`(테마·공유 피드·결정론 mock·포맷·UI 조각), `App.tsx`(헤더·KPI·탭 전환), `features/{gap,pp,health,flow}/Tab.tsx`(mock 탭). spreads와 history는 별도 기능 폴더가 담당한다.
- **spreads (003)**: server `core/premium.py`, `features/spreads/`(계산·API·응답 모델). web `features/spreads/`(1초 폴링·응답 타입·화면).
- **analysis (004)**: `features/analysis/`(호가창 소진 계산·6개 분석 API·응답 모델). web 없음.
- **history (005)**: `core/influx.py`, `core/persist.py`, `features/history/`(이력 조회 API), `scripts/backfill.py`, `docker-compose.dev.yml`. web 기록 탭은 mock 데이터를 사용한다.
- **wallet-status (006)**: `core/networks.py`(망 정규화·판정), `features/wallet_status/`(거래소별 조회·60초 캐시). collector에 Protocol로 주입하고 spreads가 망 단위 상태를 계산한다.
- **deploy (007)**: server·web Dockerfile, 배포 compose, CI·배포 GitHub Actions workflow.
