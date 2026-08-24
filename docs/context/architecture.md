# architecture.md — 구조

> 이 문서는 **목표 상태**를 쓴다. 실제 구현 여부는 `status.md` 가 말한다.

## 원칙 (바뀌지 않는 것 — 코드가 이것을 따른다)
- **메모리가 진실.** 조회 API 는 전부 메모리(live_store)를 읽는다. 저장소는 이력 전용이며 조회 경로에 끼지 않는다.
- **수집 경로는 하나.** 거래소 호출은 collector 루프만 한다. 조회 API 가 거래소를 직접 부르지 않는다.
- **거래소 커넥터는 공통 인터페이스 하나를 구현한다.** 추상 클래스(또는 Protocol) 하나에 "전 마켓 호가 일괄 조회" 등 필요한 동작을 선언하고, 업비트·빗썸·바이낸스가 각자 구현한다. 새 거래소 추가 = 구현체 하나 추가이며 collector·조회 코드는 바뀌지 않는다.
- **거래소별 quirk 는 자기 커넥터 안에서 흡수한다.** 커넥터끼리 코드 공유 없음 — 한 거래소 수정이 다른 거래소를 깨지 않게.
- **기능 폴더 격리.** 기능 간 import 금지, 공유는 `core/`·`shared/` 로만.
- **계산은 순수 함수.** service 는 저장소·클라이언트를 인자로 받고 전역을 import 하지 않는다 — 테스트가 네트워크·DB 없이 돈다.
- **입출금 상태는 3-state.** `null`(확인불가)을 열림으로 해석하는 코드는 버그다.
- **uvicorn 워커 1개.** 메모리가 진실이므로 프로세스가 둘이면 진실도 둘이 된다.

## 런타임 2개
- **server/**: Python 3.12, FastAPI, httpx, influxdb-client, pydantic v2, pydantic-settings. 로컬 포트 8000.
  - 저장소는 InfluxDB 2.7 OSS(org·bucket `marketlens`, Flux) — 김프 이력은 (거래소쌍·코인) 태그 × 시각 × 수치 2개인 전형적 시계열이고 시간 버킷 집계가 엔진 기본이라 앱 코드가 준다. 모델은 `db.md`. 테스트는 Influx 를 띄우지 않는다.
- **web/**: React 19, TypeScript, Vite. 의존성은 react·react-dom 뿐. 로컬 포트 5173(dev), 배포는 nginx 80. DB 없음.

## 데이터 흐름 (BE)
```
[거래소 REST]  ── 1초마다 ──▶  collector  ──▶  live_store (메모리, 통째 교체)
                                                                │
                              조회 API 전부 ◀── 읽기 ───────────┘
                                                                │
                              60초마다 persist 루프 ──▶ InfluxDB
                                (`premium` 에 fwd/rev 쓰기, 입출금 조회 실패 시 `dw_fail` 1점)
```
- 입출금 상태(거래소 private API)는 수집 루프가 60초 주기로만 조회해 캐시한다. 키가 없으면 `null`(모름).
- `GET /health` 와 수집 루프는 기능 폴더가 아니라 앱 진입점 소관(시스템).
- 환율은 별도 호출 없이 국내 거래소의 KRW 마켓 일괄 호가 중 USDT 호가에서 추출한다. 최우선 매도호가가 rate_ask(USDT 살 때), 최우선 매수호가가 rate_bid. 거래소별 ask/bid. 커넥터는 USDT 를 특별 취급하지 않는다.
- 외부 호출 실패 시 live_store 는 직전 값을 유지하고 `age` 가 커진다. FE 는 age 로 stale 표시. 재기동 시 Influx 에서 되읽는 폴백은 없다 — 메모리는 첫 수집으로만 채워진다.

## 데이터 흐름 (FE)
- `fetch` 폴링만 사용. 상태관리·라우터·스타일 라이브러리 없음.
- API base: `VITE_API_BASE` (미설정 시 `/api`). dev 는 vite proxy `/api → http://localhost:8000` (prefix strip), 배포는 nginx `/api/ → server:8000/`.
- 폴링 실패 시 직전 데이터 유지.

## 계약 규칙 (BE ↔ FE)
- BE 내부는 snake_case. 응답 JSON 은 **행(row) 객체 키는 camelCase, 최상위 키는 snake_case**. FE types 도 같은 모양.
- 응답 스키마 변경 = 같은 스펙 안에서 `features/<name>/models.py` 와 `web/src/features/<name>/types.ts` 를 동시에 바꾼다.
- 에러 형식 고정: `{"error": {"code": str, "message": str, "detail": any}}`.
- CORS `*`.

## 기능 폴더 내부 규약
server/app/features/<name>/
  router.py     APIRouter. prefix 는 여기서 선언. 앱 진입점이 include.
  service.py    순수 계산. live_store/influx 클라이언트를 인자로 받는다 (전역 import 금지 → 테스트 용이).
  models.py     pydantic 응답 모델.
  tests/        이 기능의 테스트. 네트워크 호출 없음.
web/src/features/<name>/
  Tab.tsx       화면. api.ts 호출 + types.ts 타입.
  api.ts        fetch 함수. 거래소 id→표시명 변환 등 표시 전용 가공.
  types.ts      BE models.py 와 1:1.

## 배포 토폴로지
EC2 1대. 루트 `docker compose up -d --build` 로 server·web·influxdb 컨테이너(compose 기본 네트워크, Influx 포트 비공개). CI(GitHub Actions): PR → pytest + web build. main push → SSH 배포. 상세는 스펙 007(deploy).

## 현재 구조 (개발 후 갱신 — 실행 세션이 §7 보고와 함께 채운다)
스펙이 DONE 될 때마다 그 기능의 실제 구조를 여기 짧게 적는다: 주요 클래스·모듈과 역할, 왜 그렇게 나눴는지. 위 "원칙"과 어긋나면 원칙을 바꾸지 말고 코드를 고친다.
- (아직 없음)


## AWS 배포 서버
- DB 서버 (용량떄문에) - 100GB 시작 
- Front, Backend 서버 
- Parsing 서버 (CPU 사용량) 

사용량 체크해보고 낮추기 
