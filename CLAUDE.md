# MarketLens — CLAUDE.md

> 이 문서는 MarketLens 개발의 **유일한 진입점**이다. 모든 문서는 여기서 파생된다.
> 새 컨텍스트에서 작업을 시작할 때는 이 문서 → `docs/context/*` → 지정된 스펙 1개 순서로 읽는다.

## 1. 한 줄 정의
한국 거래소(업비트·빗썸)와 해외 거래소(바이낸스) 간 **김치 프리미엄(김프)·역프를 1초 단위로 계산해 보여주는 차익거래 모니터링 대시보드**.
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
    specs/                    개발 단위 문서 (누적형, 완료 후 불변)
      TEMPLATE.md
      NNN-<name>.md
    design/                   디자인 파일 원본 (theme.css·index.css) — 스펙은 값을 옮겨 적지 않고 이 파일을 복사하게 한다
    runbooks/
      execute-spec.md         실행 세션에 줄 프롬프트
      drift-check.md          문서↔코드 어긋남 점검 절차
      ec2-setup.md            EC2 최초 1회 설정 (사람용 체크리스트)
  server/                     FastAPI 앱 (Python 3.12)
    app/
      core/                   공유 인프라 — 커넥터·메모리 저장소·수집 루프·김프 계산·망 매칭·Influx 클라이언트 (모듈 이름은 개발 후 architecture.md "현재 구조"에)
      features/<name>/        기능 1개 = 폴더 1개: router.py service.py models.py tests/
      main.py
    tests/                    기능 폴더 밖의 통합 테스트만
  web/                        Vite + React 19 앱 (TypeScript)
    src/
      shared/                 config·theme·format·ui 조각
      features/<name>/        기능 1개 = 폴더 1개: Tab.tsx api.ts types.ts
      App.tsx main.tsx
```
- 화면이 있는 기능만 `web/src/features/<name>` 폴더를 가진다. 이름은 `server/app/features/<name>` 과 같게 한다.
- `collect` 는 기능 폴더가 아니라 `core/collector` 에 산다. `wallet-status`(Python 패키지는 `wallet_status`) 는 BE 전용이다(화면은 spreads 표에 얹힌다).
- 기능 간 import 금지. 공유는 `core/`·`shared/`를 통해서만.

## 3. 개발 방식 (문서 기반)
1. **설계 세션**: 사람 + Claude가 대화하며 `docs/specs/NNN-<name>.md`를 쓴다. 스펙은 **사람이 끝까지 읽는 문서**다 — 동작·계약·규칙·엣지만 쓰고, 코드 구조는 쓰지 않는다.
2. **실행 세션**: 아무것도 모르는 새 컨텍스트가 `CLAUDE.md` + `docs/context/*` + 스펙 1개만 읽고 구현한다. 테스트도 스펙을 보고 직접 쓴다. (`docs/runbooks/execute-spec.md`)
3. 실행 중 스펙에 없는 결정이 필요한 상황은 **자주 나오고 정상**이다. 그때 실행 세션은 멈추고 묻는다. 사람과 함께 **스펙(또는 context 문서)을 먼저 고치고**, 고친 문서를 기준으로 그 자리에서 이어간다. 롤백·재실행은 하지 않는다. 목적은 "코드에만 있고 문서에 없는 결정"을 남기지 않는 것이다.
4. 스펙 완료 조건에는 **코드 + 테스트 + `docs/context/*` 갱신**이 모두 포함된다.
5. 죽은 문서는 지운다. 역사는 스펙(누적형)에 있다.

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
| 001 | collect | DONE | 업비트·빗썸·바이낸스 1초 수집 → 메모리, 환율 추출, `/health` |
| 002 | web-shell | DONE | 화면 골격·탭·KPI·테마·mock 탭(갭/선선갭/수집상태/입출금레이더) |
| 003 | spreads | DONE | 김프 표 — `/spreads` `/refresh` + 스프레드 탭 |
| 004 | analysis | DONE | 단일 종목 분석 — premium·matrix·orderbook·slippage·arbitrage (BE 전용) |
| 005 | history | TODO | Influx 영속·김프 아카이브·`/history/*`·백필 + history 탭 |
| 006 | wallet-status | TODO | 거래소 입출금 상태·망 기준 판정 → 스프레드 표에 반영 |
| 007 | deploy | TODO | Docker·compose·CI·EC2 배포 |

실행 순서 = 번호 순.
상태: TODO → IN_PROGRESS → DONE. DONE 이후 스펙 파일은 수정하지 않는다.

## 5. 접근 규칙 (기본 세팅 — 모든 세션 공통)
읽기 순서는 문서 머리의 규칙 그대로: 이 문서 → `docs/context/*` → 지정된 스펙 1개.

접근 금지 (예외 없음):
- **`.env`** — `server/.env` 를 포함한 모든 `.env*` 파일(`.env.example` 제외)을 읽지도, 쓰지도, 출력하지도 않는다. 값을 화면에 내지 않고 프로그램에 넘기는 것(`docker compose --env-file server/.env …`)만 허용. 키 목록의 진실은 `server/.env.example` 이고, 값을 만들고 바꾸는 것은 **사람**이다. 키·토큰·시크릿 값은 코드·문서·로그·커밋 어디에도 적지 않는다.
- **이 레포 밖의 코드** — 기존 marketlens-be·fe 를 포함해 읽지 않는다. 필요한 계약은 스펙 안에 복사돼 있다. 막히면 추측하고 스펙 §7 실행 보고에 적는다.
- **DONE 스펙** — 수정하지 않는다(§4). 고칠 게 있으면 새 fix 스펙.
- **다른 사람 담당 스펙** — 결함을 발견해도 고치지 않는다. `파일:절 — 문서 주장 → 실제` 형식으로 보고만 한다.

허용:
- 수정 가능: `server/` `web/` `docs/context/*` `docs/runbooks/*` 담당 스펙(TODO·IN_PROGRESS) `CLAUDE.md` 스펙 인덱스 상태. `docs/design/*` 원본 변경은 사람 합의 후.
- 거래소 실호출은 수집 루프·백필 스크립트 코드가 한다. 세션이 직접 부르는 것은 스펙 §4 가 명시한 실서버 확인 항목뿐이고, 개발 망에서 거래소 도메인이 차단되면 EC2 에서 돌린다(dev-setup.md 로컬 메모).

## 6. 문서 변경 검증 루프 (md 를 바꾸면 반드시 돈다)
문서와 코드는 같은 진실을 가리켜야 한다. `docs/**/*.md`·`CLAUDE.md` 가 바뀌면:
1. **구현 전 기능의 스펙**(TODO): 돌릴 코드가 없다 — 의존 스펙에서 복사한 계약이 원본과 같은지 교차 확인만 한다.
2. **구현된 기능의 스펙·context 문서**: 그 기능의 §4 검증을 **다시 실행**해 통과를 확인한 뒤에만 커밋한다 — server `ruff check . && pytest -q`, web `npm run lint && npm run build`, 해당되면 curl 스모크. 실패 = 문서↔코드 어긋남이므로 옳은 쪽을 사람이 정하고(§3-3) 맞춘다.
3. 구현 중 스펙의 빈틈을 만나면 코드를 먼저 쓰지 않는다 — 스펙을 먼저 고치고, 고친 문구로 테스트를 쓴다(§3-3).
4. 주기 점검은 `docs/runbooks/drift-check.md`(스펙 3개 완료마다).

CI(스펙 007)는 경로 필터 없이 항상 server·web 두 job 을 돌린다 — **문서만 바뀐 PR 도 전체 검증을 통과해야 머지된다.** 레포의 `.claude/settings.json` 훅이 md 변경 시 이 절차를 상기시킨다.
