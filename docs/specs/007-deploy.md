# 007 — deploy

상태: TODO | 의존: 001(collect), 002(web-shell), 005(history)

## 1. 목적
`docker compose up -d --build` 한 번으로 전체가 뜬다. PR 마다 CI 가 검사하고, `main` 에 머지되면 EC2 에 자동 배포된다.

## 2. 범위
- 만드는 것: server·web Dockerfile, 루트 `docker-compose.yml`, GitHub Actions 워크플로(CI·deploy), PR 템플릿, 루트 README
- 하지 않는 것: EC2 생성 자동화, HTTPS·도메인, 컨테이너 레지스트리, 로그·모니터링

## 3. 정해진 것
### 툴
- CI/CD 는 **GitHub Actions**. 배포 단위는 **docker compose**. 서버는 **EC2 1대**, 이미지는 EC2 에서 직접 빌드한다.
- 컨테이너 3개: `server`(FastAPI), `web`(nginx + 빌드된 정적 파일), `influxdb`(2.7, dev compose 와 같은 설정).

### 규칙 (왜 가 있는 것)
- **외부에 열리는 포트는 web 의 :80 하나.** server 는 EC2 안(loopback)에서만, Influx 는 비공개. 공격면을 하나로.
- **`/api/*` 는 web 이 server 로 넘기며 `/api` 접두를 뗀다.** `/api/health` → server `/health`.
- **server 는 uvicorn 워커 1개.** 수집 루프와 메모리 저장소가 프로세스 안에 있어 워커가 둘이면 진실도 둘이 된다.
- **`.env` 는 이미지에 넣지 않는다.** compose 의 `env_file` 로만 주입. 시크릿이 이미지에 남지 않게.
- **CI 는 server(ruff+pytest)·web(lint+build) 두 job 을 항상 둘 다 돌린다.** 경로 필터로 건너뛰면 required check 가 비어 branch protection 이 꼬인다.
- **main 은 PR 로만 머지한다.** main 푸시 = 배포이므로 CI 를 우회할 길을 막는다.
- **`server/.env` 가 없거나 `INFLUX_TOKEN` 이 비어 있으면 배포를 실패시킨다.** 토큰 없이 뜨면 저장 루프가 꺼진 채 조용히 데이터를 잃는다.
- Secrets 는 `EC2_HOST`·`EC2_USER`·`EC2_SSH_KEY` 셋. 값은 어디에도 적지 않는다.

### 사람이 하는 것
- EC2 최초 설정은 `docs/runbooks/ec2-setup.md`.
- README 는 30줄 안팎: 한 줄 정의, "문서 진입점은 CLAUDE.md", 퀵스타트, 배포 한 줄. 협업 규칙은 conventions.md 에만.

## 4. 검증
- env 파일(없으면 env 예시 파일에서 만든다)을 둔 채 `docker compose up -d --build` 하면 세 컨테이너가 살아 있다.
- `curl localhost/` 에 `트레이딩룸 · MarketLens` 가 있고, `curl localhost/foo` 도 index.html 을 준다.
- `curl localhost/api/health` 가 server 의 `/health` 응답을 그대로 준다(`status == "ok"`).
- server 컨테이너 env 에 `.env` 값이 있고, 이미지 안에는 `.env` 파일이 없다.
- Influx 를 내려도 `/health` 는 200, `/history/*` 만 503.
- PR 을 올리면 `server`·`web` check 가 green.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/architecture.md` — 배포 토폴로지 절 + "현재 구조" 에 실제 파일 구성.
- `docs/context/dev-setup.md` — "docker 통합 기동" 절.
- `docs/context/conventions.md` — "CI 통과 필수, main 머지 = 자동 배포".
- `docs/context/status.md` — deploy 행. `CLAUDE.md` 인덱스 → DONE.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
