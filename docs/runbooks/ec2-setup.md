# EC2 최초 1회 설정 (사람이 한다)

스펙 007 이 전제하는 서버 상태. 한 번만 하고, 이후 배포는 GitHub Actions 가 한다.
**이 EC2 에는 기존 marketlens-be(:8000)·fe(:80) 가 운영 중이다. 기존 컨테이너·crontab·폴더는 건드리지 않는다.**

1. Docker Engine + compose plugin 은 이미 있다(기존 스택이 쓴다). 배포 계정이 `docker` 그룹인지 확인.
2. 인바운드는 기존 그대로(80 HTTP, 22 SSH 내 IP). 새 스택을 밖에서 보려면 **8080** 을 추가로 연다. EC2 안에서 `curl localhost:8080` 으로만 확인할 거면 불필요.
3. `git clone <repo> ~/marketlens` — 기존 `~/marketlens-be`·`~/marketlens-fe` 와 별개 폴더.
4. `~/marketlens/server/.env` 작성 (git 에 없음):
   - `INFLUX_URL` — compose 안 Influx 서비스 주소(서비스명으로 접근, localhost 아님)
   - `INFLUX_TOKEN`, `REFRESH_TOKEN` — `openssl rand -hex 32`. `INFLUX_TOKEN` 은 Influx **첫 기동 전**에 정해야 한다 (setup 이 그 값을 admin 토큰으로 쓴다).
   - 거래소 키는 선택 — 같은 계정을 쓰면 기존 `~/marketlens-be/.env` 의 `UPBIT_*`·`BINANCE_*` 값을 복사해도 된다(업비트 허용 목록에 EC2 IP 는 이미 등록돼 있다).
5. `~/marketlens/.env` (루트, compose 변수용) 에 `WEB_PORT=8080` — 기존 fe 가 80 을 쓰기 때문.
6. GitHub 설정(관리자): Secrets `EC2_HOST`·`EC2_USER`·`EC2_SSH_KEY`(기존 be·fe 레포와 같은 값), main branch protection(PR 필수, required checks `server`·`web`, force-push 금지).
7. main 푸시 → Actions 로그 확인 → EC2 안에서 `curl localhost:8080/api/health`.
8. 배포 직후 백필 1회 (005 §3.5) — Influx 가 호스트에 안 열려 있으므로 **컨테이너 안에서** 돌린다: `cd ~/marketlens && docker compose exec server <백필 실행 명령 — dev-setup.md 백필 절>`. 업비트 초봉이 3개월 롤링이라 미룰수록 과거를 잃는다.
