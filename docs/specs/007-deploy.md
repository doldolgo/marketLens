# 007 — deploy

상태: TODO | 의존: 001(collect), 002(web-shell), 005(history)

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
`docker compose up -d --build` 한 번으로 전체가 뜬다. PR 마다 CI 가 검사하고, `main` 에 머지되면 EC2 에 자동 배포된다.
**같은 EC2 에서 기존 marketlens-be(:8000)·fe(:80) 가 운영 중이므로, 이 레포는 그것을 건드리지 않고 공존한다.**

## 2. 범위
- 만드는 것: server·web Dockerfile, 루트 `docker-compose.yml`(배포용 3컨테이너 — 005 의 dev compose 는 Influx 하나로 그대로 둔다), GitHub Actions 워크플로 2개(CI·deploy), PR 템플릿, 루트 README
- 하지 않는 것: EC2 생성 자동화, HTTPS·도메인, 컨테이너 레지스트리, 로그·모니터링, 기존 be·fe 스택의 변경·중단

## 3. 정해진 것

### 툴
- CI/CD 는 **GitHub Actions**. 배포 단위는 **docker compose**. 서버는 **EC2 1대**(기존 스택과 같은 서버), 이미지는 EC2 에서 직접 빌드한다.
- 컨테이너 3개:
  - `server` — FastAPI + uvicorn 워커 1개(python 3.12 slim). 컨테이너 포트 8000, **호스트에 노출하지 않는다**(compose 내부 네트워크만).
  - `web` — 멀티스테이지 빌드(Node 22 로 `npm run build` → nginx 가 정적 파일 서빙). nginx 는 `/api/` 를 `server:8000/` 로 프록시하고, 없는 경로는 index.html 을 준다(SPA).
  - `influxdb` — 2.7, dev compose 와 같은 첫 기동 설정(org·bucket `marketlens`, admin 토큰 = `INFLUX_TOKEN`). named volume, 호스트 비노출.

### 규칙 (왜 가 있는 것)
- **호스트에 여는 포트는 web 하나.** server 는 compose 안에서만, Influx 는 비공개. 공격면을 하나로.
- **호스트 포트는 compose 변수 `WEB_PORT`(기본 80).** 현 EC2 는 기존 fe 가 80, be 가 8000 을 점유하므로 **`WEB_PORT=8080` 으로 공존**한다. 기존 컨테이너·crontab 은 이 레포 소관이 아니다 — 절대 내리거나 수정하지 않는다. 기존 스택을 이관·폐기하는 날 80 으로 바꾸는 것은 별도 스펙.
- **`/api/*` 는 web 이 server 로 넘기며 `/api` 접두를 뗀다.** `/api/health` → server `/health`. dev 의 vite proxy 와 같은 규칙이라 FE 코드는 환경을 모른다.
- **server 는 uvicorn 워커 1개.** 수집 루프와 메모리 저장소가 프로세스 안에 있어 워커가 둘이면 진실도 둘이 된다.
- **`.env` 는 이미지에 넣지 않는다.** `server/.env` 는 compose 의 `env_file` 로만 주입(시크릿이 이미지 레이어에 남지 않게). `WEB_PORT` 는 compose 변수라 루트 `.env` 에 둔다 — 시크릿과 포트 설정을 섞지 않는다.
- **CI 는 server·web 두 job 을 항상 둘 다 돌린다.** 경로 필터로 건너뛰면 required check 가 비어 branch protection 이 꼬인다.
  - `server` job: Python 3.12 → 의존성 설치 → `ruff check .` → `ruff format --check .` → `pytest -q` (작업 디렉토리 `server/`)
  - `web` job: Node 22 → `npm ci` → `npm run lint` → `npm run build` (작업 디렉토리 `web/`)
  - 트리거는 `pull_request`(대상 main). 액션 세대는 기존 be·fe 레포와 같게(checkout@v5·setup-python@v5·setup-node@v5, 의존성 캐시 켬).
- **main 은 PR 로만 머지한다.** main 푸시 = 배포이므로 CI 를 우회할 길을 막는다. branch protection(§사람이 하는 것)으로 강제한다.
- **deploy 워크플로**: `push`(main) 트리거, `appleboy/ssh-action@v1` 로 EC2 에 SSH. 스크립트 순서:
  1. `cd ~/marketlens` (기존 be·fe 폴더와 다른 폴더)
  2. `server/.env` 가 없거나 `INFLUX_TOKEN` 이 비어 있으면 **배포 실패**. 토큰 없이 뜨면 저장 루프가 꺼진 채 조용히 데이터를 잃는다.
  3. `git pull origin main`
  4. `docker compose up -d --build`
  5. `docker image prune -f` — 오래된 레이어가 EC2 디스크를 채우지 않게.
- Secrets 는 `EC2_HOST`·`EC2_USER`·`EC2_SSH_KEY` 셋(기존 be·fe 레포와 같은 값). 값은 어디에도 적지 않는다.
- PR 템플릿은 conventions.md 규칙 그대로 3줄 골격: 무엇을 / 왜 / 테스트.

### 사람이 하는 것
- EC2 최초 설정은 `docs/runbooks/ec2-setup.md`.
- GitHub 설정(관리자): Secrets 3개 등록, main branch protection — PR 필수, required checks `server`·`web`, force-push 금지.
- README 는 30줄 안팎: 한 줄 정의, "문서 진입점은 CLAUDE.md", 퀵스타트, 배포 한 줄. 협업 규칙은 conventions.md 에만.

## 4. 검증
- env 파일(없으면 env 예시 파일에서 만든다)을 둔 채 `WEB_PORT=8080 docker compose up -d --build` 하면 세 컨테이너가 살아 있다.
- `curl localhost:8080/` 에 `트레이딩룸 · MarketLens` 가 있고, `curl localhost:8080/foo` 도 index.html 을 준다.
- `curl localhost:8080/api/health` 가 server 의 `/health` 응답을 그대로 준다(`status == "ok"`).
- server 컨테이너 env 에 `.env` 값이 있고, 이미지 안에는 `.env` 파일이 없다.
- 호스트에 8000·8086 이 **이 스택 때문에 새로 열리지 않는다**(server·Influx 비노출).
- Influx 컨테이너를 내려도 `/health` 는 200, `/history/*` 만 503.
- EC2 에서: 배포 후에도 기존 컨테이너 `market-lens-fe`·`market-lens-be`(기존 스택의 실제 컨테이너 이름 — 폴더명 `~/marketlens-be` 와 다르다)가 그대로 Up 이고 `curl localhost:80` 이 기존 fe 를 준다(공존).
- PR 을 올리면 `server`·`web` check 가 green. main 머지 → Actions deploy 성공 → EC2 안에서 `curl localhost:8080/api/health` 가 ok.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/architecture.md` — 배포 토폴로지 절(공존 포트 포함) + "현재 구조" 에 실제 파일 구성.
- `docs/context/dev-setup.md` — "docker 통합 기동" 절.
- `docs/context/conventions.md` — "CI 통과 필수, main 머지 = 자동 배포".
- `docs/context/status.md` — deploy 행. `CLAUDE.md` 인덱스 → DONE.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
