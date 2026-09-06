# 007 — deploy

상태: DONE | 의존: 001(collect), 002(web-shell), 005(history), 009(tick-store — redis 컨테이너)

> 이 문서는 **사람이 끝까지 읽는** 문서다. 코드를 산문으로 옮기지 않는다.
> 구현 구조(파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
`docker compose up -d --build` 한 번으로 전체가 뜬다. PR 마다 CI 가 검사하고, `main` 에 머지되면 EC2 에 자동 배포된다.
**같은 EC2 에서 기존 marketlens-be(:8000)·fe(:80) 가 운영 중이므로, 이 레포는 그것을 건드리지 않고 공존한다.**

## 2. 범위
- 만드는 것: server·web Dockerfile, 루트 `docker-compose.yml`(배포용 4컨테이너 — dev compose 는 Influx·Redis 둘), GitHub Actions 워크플로 2개(CI·deploy), PR 템플릿, 루트 README
- 하지 않는 것: EC2 생성 자동화, HTTPS·도메인, 컨테이너 레지스트리, 로그 수집·모니터링(컨테이너 로그 **상한**은 compose 가 두지만 수집·대시보드는 없다), 기존 be·fe 스택의 변경·중단

## 3. 정해진 것

### 툴
- CI/CD 는 **GitHub Actions**. 배포 단위는 **docker compose**. 서버는 **EC2 1대**(기존 스택과 같은 서버), 이미지는 EC2 에서 직접 빌드한다.
- 컨테이너 4개:
  - `server` — FastAPI + uvicorn 워커 1개(python 3.12 slim). 컨테이너 포트 8000, **호스트에 노출하지 않는다**(compose 내부 네트워크만).
  - `web` — 멀티스테이지 빌드(Node 22 로 `npm run build` → nginx 가 정적 파일 서빙). nginx 는 `/api/` 를 `server:8000/` 로 프록시하고, 없는 경로는 index.html 을 준다(SPA).
    캐시 규칙: `index.html` 은 `no-store, must-revalidate` **+ `always`** — 배포가 FE·BE 를 함께 바꾸므로 캐시된 셸이 남으면 열려 있던 탭이 구 번들로 새 API 계약을 계속 친다. `/assets/` 의 해시 박힌 파일은 `max-age=31536000, immutable` 이되 **`always` 는 붙이지 않는다** — 붙이면 404 에도 1년 immutable 이 실려, 배포 직전 셸을 든 브라우저가 사라진 번들의 404 를 1년간 캐시한다(재배포로도 되돌릴 수 없다). `always` 없이도 200·304 는 헤더를 받는다.
  - `influxdb` — 2.7, dev compose 와 같은 첫 기동 설정(org·bucket `marketlens`, admin 토큰 = `INFLUX_TOKEN`). named volume, 호스트 비노출.
  - `redis` — `redis:7-alpine`, `--appendonly yes`, named volume, 호스트 비노출(009 의 틱 버퍼 — Influx 로 옮기기 전 틱만 든다).

### 규칙 (왜 가 있는 것)
- **컨테이너 로그는 네 서비스 모두 `json-file` 50MB × 3 으로 묶는다.** docker 기본값은 무한이고, 회전 없는 로그가 디스크를 채우면 Influx 가 쓰기를 거부한다 — 그 거부는 **공간을 되찾아도 컨테이너를 재시작하기 전까지 풀리지 않는다**(열지 못한 shard 를 캐시한다). 데몬 설정(`/etc/docker/daemon.json`)이 아니라 compose 에 두는 이유는 이 스택이 자기 한도를 들고 다니게 하기 위해서다.
- **호스트에 여는 포트는 web 하나.** server 는 compose 안에서만, Influx 는 비공개. 공격면을 하나로.
- **호스트 포트는 compose 변수 `WEB_PORT`(기본 80).** 현 EC2 는 기존 fe 가 80, be 가 8000 을 점유하므로 **`WEB_PORT=8080` 으로 공존**한다. 기존 컨테이너·crontab 은 이 레포 소관이 아니다 — 절대 내리거나 수정하지 않는다. 기존 스택을 이관·폐기하는 날 80 으로 바꾸는 것은 별도 스펙.
- **`/api/*` 는 web 이 server 로 넘기며 `/api` 접두를 뗀다.** `/api/health` → server `/health`. dev 의 vite proxy 와 같은 규칙이라 FE 코드는 환경을 모른다.
- **server 는 uvicorn 워커 1개.** 틱 루프·스트림과 메모리 저장소가 프로세스 안에 있어 워커가 둘이면 진실도 둘이 된다.
- **`.env` 는 이미지에 넣지 않는다.** `server/.env` 는 compose 의 `env_file` 로만 주입(시크릿이 이미지 레이어에 남지 않게). `WEB_PORT` 는 compose 변수라 루트 `.env` 에 둔다 — 시크릿과 포트 설정을 섞지 않는다. 단 `INFLUX_URL`·`REDIS_URL` 은 compose 가 `environment` 로 `http://influxdb:8086`·`redis://redis:6379/0` 을 **덮어쓴다** — `server/.env` 의 값은 로컬(호스트) 기준이라 컨테이너 안에서 닿지 않기 때문.
- **CI 는 server·web 두 job 을 항상 둘 다 돌린다.** 경로 필터로 건너뛰면 required check 가 비어 branch protection 이 꼬인다.
  - `server` job: Python 3.12 → 의존성 설치 → `ruff check .` → `ruff format --check .` → `pytest -q` (작업 디렉토리 `server/`)
  - `web` job: Node 22 → `npm ci` → `npm run lint` → `npm run build` (작업 디렉토리 `web/`)
  - 트리거는 `pull_request`(대상 main). 액션 세대는 기존 be·fe 레포와 같게(checkout@v5·setup-python@v5·setup-node@v5, 의존성 캐시 켬).
- **main 은 PR 로만 머지한다.** main 푸시 = 배포이므로 CI 를 우회할 길을 막는다. branch protection(§사람이 하는 것)으로 강제한다.
- **deploy 워크플로**: `push`(main) 트리거, `appleboy/ssh-action@v1` 로 EC2 에 SSH. 스크립트 순서:
  1. `cd ~/marketlens` (기존 be·fe 폴더와 다른 폴더)
  2. `server/.env` 가 없거나 `INFLUX_TOKEN`·`S3_BUCKET` 중 하나라도 비어 있으면 **배포 실패**(값은 출력하지 않는다 — 존재·비어있지 않음만 `grep -q '^KEY=.'` 로 본다). 토큰 없이 뜨면 저장 루프가, 버킷 없이 뜨면 원문 아카이브(010)가 꺼진 채 조용히 데이터를 잃는다. S3 자격증명은 env 가 아니라 EC2 인스턴스의 IAM 역할(`docs/runbooks/ec2-setup.md` 5-2)이므로 가드 대상이 아니다. 루트 `.env` 는 가드하지 않는다 — 없으면 4단계 `--env-file .env` 가 어차피 시끄럽게 실패한다.
  3. `git fetch origin main && git reset --hard origin/main` — pull 이 아니라 **미러 동기화**. 배포 트리는 main 의 사본일 뿐이므로, 서버 쪽 로컬 커밋·갈래가 있어도 항상 main 을 그대로 따른다(첫 배포에서 pull 이 갈래 때문에 실패한 실사례).
  4. `docker compose --env-file .env --env-file server/.env up -d --build` — `WEB_PORT` 는 루트 `.env`, Influx 첫 기동 admin 토큰(`DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=${INFLUX_TOKEN}`)은 `server/.env` 에서 치환한다. `--env-file` 을 명시하면 기본 `./.env` 자동 로드가 꺼지므로 둘 다 적는다.
  5. `docker image prune -f` — 오래된 레이어가 EC2 디스크를 채우지 않게.
- Secrets 는 `EC2_HOST`·`EC2_USER`·`EC2_SSH_KEY` 셋(기존 be·fe 레포와 같은 값). 값은 어디에도 적지 않는다.
- PR 템플릿은 conventions.md 규칙 그대로 3줄 골격: 무엇을 / 왜 / 테스트.
- **배포 설정의 계약은 테스트가 지킨다.** `server/tests/test_deploy.py` 가 루트 `docker-compose.yml`·워크플로 2개·Dockerfile·`.dockerignore`·`nginx.conf`·PR 템플릿·README 를 **파일로 읽어** §4 의 조건(컨테이너 4개, 호스트 노출은 web 하나, `.env` 는 `env_file` 로만·이미지 제외, `INFLUX_URL`·`REDIS_URL` 덮어쓰기, CI job 2개 무필터, deploy 스크립트의 가드·미러 동기화·`up -d --build`·prune 순서, nginx 접두 제거·캐시 규칙, 워커 1개)을 단언한다. Docker 가 없는 CI 에서 도는 유일한 회귀 장치라 pytest 안에 둔다. YAML 파싱은 dev 의존성 `pyyaml`(설치가 이미 전이 의존으로 들어오지만 명시해야 두 설치 경로가 같다). 워크플로의 `docker compose config`·컨테이너 기동 검증은 Docker 가 있는 로컬·EC2 에서 사람이 §5 명령으로 돈다.

### 사람이 하는 것
- EC2 최초 설정은 `docs/runbooks/ec2-setup.md`.
- GitHub 설정(관리자): Secrets 3개 등록, main branch protection — PR 필수, required checks `server`·`web`, force-push 금지.
- README 는 30줄 안팎: 한 줄 정의, "문서 진입점은 CLAUDE.md", 퀵스타트, 배포 한 줄. 협업 규칙은 conventions.md 에만.

## 4. 검증
- env 파일(없으면 env 예시 파일에서 만든다)을 둔 채 `WEB_PORT=8080 docker compose --env-file server/.env up -d --build` 하면 네 컨테이너가 살아 있다(compose 변수 치환은 셸 env 와 `--env-file` 만 읽으므로 `INFLUX_TOKEN` 을 위해 `server/.env` 를 명시한다).
- `curl localhost:8080/` 에 `트레이딩룸 · MarketLens` 가 있고, `curl localhost:8080/foo` 도 index.html 을 준다.
- `curl localhost:8080/api/health` 가 server 의 `/health` 응답을 그대로 준다(`status == "ok"`).
- server 컨테이너 env 에 `.env` 값이 있고, 이미지 안에는 `.env` 파일이 없다.
- 호스트에 8000·8086 이 **이 스택 때문에 새로 열리지 않는다**(server·Influx 비노출).
- `docker inspect --format '{{.HostConfig.LogConfig}}' marketlens-server` 가 `json-file` 과 `max-size:50m`·`max-file:3` 을 말한다(네 컨테이너 모두).
- Influx 컨테이너를 내려도 `/health` 는 200, `/history/*` 만 503. Redis 컨테이너를 내려도 `/health` 200·`/spreads` 정상(009 격리).
- EC2 에서: 배포 후에도 기존 컨테이너 `market-lens-fe`·`market-lens-be`(기존 스택의 실제 컨테이너 이름 — 폴더명 `~/marketlens-be` 와 다르다)가 그대로 Up 이고 `curl localhost:80` 이 기존 fe 를 준다(공존).
- PR 을 올리면 `server`·`web` check 가 green. main 머지 → Actions deploy 성공 → EC2 안에서 `curl localhost:8080/api/health` 가 ok.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 로컬(Mac, OrbStack Docker 29 / compose v5) — 2026-09-06. 거래소 도메인은 이 망에서 차단(REST·WS 모두 ConnectTimeout).
WEB_PORT=8080 docker compose --env-file server/.env up -d --build
#   → marketlens-influxdb·redis·server·web 4컨테이너 Up, 호스트 노출은 web 의 :8080 하나(ps Ports 열)
curl localhost:8080/            # <title>트레이딩룸 · MarketLens</title>  /foo → 200, 같은 index.html
curl -sI localhost:8080/index.html          # Cache-Control: no-store, must-revalidate
curl -sI localhost:8080/assets/index-*.js   # Cache-Control: public, max-age=31536000, immutable
curl -sI localhost:8080/assets/none.js       # 404 이고 Cache-Control 이 없다
curl localhost:8080/api/health  # {"status":"ok","version":"0.1.0"} 200 — 접두 제거 확인, /api 자체는 404
# server 컨테이너 env: server/.env 의 키가 이름만으로 확인됨(값 미출력), INFLUX_URL=http://influxdb:8086·REDIS_URL=redis://redis:6379/0 로 덮임
# 이미지 안 .env: marketlens-server 0건, marketlens-web 0건 (find / -xdev -name .env)
# 호스트 8000·8086 LISTEN 0건(lsof)
docker stop marketlens-influxdb   # → /api/health 200, /api/history/premium 503 storage_unavailable
docker stop marketlens-redis      # → /api/health 200, /api/spreads 는 내리기 전과 같은 응답(009 격리)
#   ※ 이 망은 거래소가 막혀 우주가 비어 /spreads 는 전후 모두 404 market_data_not_found — 행이 있는 상태의 확인은 EC2 에서
# influx 재기동 65초 뒤 /api/history/premium → 404 "기록이 없습니다"(저장소 조회 성공, 틱 행이 없어 점 0) — 첫 점 왕복은 EC2 에서
docker compose --env-file server/.env config --quiet   # OK
python -c "import yaml; ..."   # ci.yml·deploy.yml·compose 2개 파싱 OK (actionlint 미설치 — 생략)
docker compose --env-file server/.env down               # 검증 후 정리(볼륨 유지)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format . && .venv/bin/python -m pytest -q   # 452 passed (test_deploy.py 17)
# EC2 에서 확인 필요: 기존 market-lens-fe·be 공존(curl localhost:80)·PR check green·main 머지 자동 배포·EC2 안 curl localhost:8080/api/health·행이 있는 /spreads 의 Redis 격리·Influx 첫 점 왕복
```

## 6. 갱신할 문서
- `docs/context/architecture.md` — 배포 토폴로지 절(공존 포트 포함) + "현재 구조" 에 실제 파일 구성.
- `docs/context/dev-setup.md` — "docker 통합 기동" 절.
- `docs/context/conventions.md` — "CI 통과 필수, main 머지 = 자동 배포".
- `docs/context/status.md` — deploy 행. `CLAUDE.md` 인덱스 → DONE.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것: `server/Dockerfile`(+.dockerignore), `web/Dockerfile`·`nginx.conf`(+.dockerignore), 루트 `docker-compose.yml`(name: marketlens, restart, container_name 고정 — 기존 market-lens-* 와 무충돌), `.github/workflows/ci.yml`·`deploy.yml`, PR 템플릿, README(29줄), `server/tests/test_deploy.py`(설정 계약 17개 — §4 항목마다 1개 이상), `pyproject` dev 의존성 `pyyaml`.
- 추측한 지점: compose 프로젝트명 고정(dev compose 와 컨테이너 재생성 충돌 방지), `.dockerignore` 2개(.env 원천 차단), server 의존성은 pyproject 범위로 `pip install .`, 가드는 `grep -q '^KEY=.'` 로 존재·비어있지 않음만(§3 에 `S3_BUCKET` 가드·IAM 역할 전제를 확정 문구로 적음 — 010 의 켜는 조건이 `S3_BUCKET` 존재라 버킷 없이 뜨면 원문 아카이브가 조용히 꺼지고, 그 가드의 계약은 배포를 소유하는 이 스펙에 있어야 010 이 복사만 하면 되기 때문), nginx 프록시 헤더 4종·`/api` 자체는 404, 배포 계약 테스트는 파일을 읽는 방식(§3 — Docker 없는 CI 에서 도는 유일한 회귀 장치)이고 YAML 파싱에 `pyyaml` 을 dev 의존성으로 추가, 앱 수준 격리(저장소 없이 `/health` 200·`/history` 503)는 lifespan 없는 `create_app()` 으로 단언.
- 실행 중 함께 고친 스펙 절: §3 배포 가드(`S3_BUCKET`·IAM 역할), §3 배포 설정 계약 테스트 항목, §4 기동 명령에 `--env-file server/.env`, §5 를 4컨테이너·Redis 격리·캐시 헤더 확인으로. 010 §2·§3.1 — `S3_BUCKET` 배포 가드의 원본을 007 §3 으로 가리킨다(가드 계약이 두 스펙에 서로 다르게 적히지 않도록). 010 §7 의 다른 스펙 보고(007 §3 가드·003 의 가공 표 S3 저장 문장)는 두 스펙 모두 해소돼 지웠다. `docs/runbooks/ec2-setup.md` 4 — `INFLUX_URL`·`REDIS_URL` 은 compose 가 서비스명으로 덮어쓰므로 `.env.example` 기본값 그대로.
- 남은 빚: GitHub 권한 후 — Secrets 3개·branch protection·실 PR CI green / EC2 — `~/marketlens` 클론·env 2개 작성(사람)·자동 배포·공존 확인·EC2 안 `curl localhost:8080/api/health`·행이 있는 `/spreads` 의 Redis 격리·Influx 첫 점 왕복. 워크플로 lint(actionlint)는 미설치라 YAML 파싱 + 테스트 단언으로 갈음.
