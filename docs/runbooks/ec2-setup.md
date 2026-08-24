# EC2 최초 1회 설정 (사람이 한다)

스펙 007 이 전제하는 서버 상태. 한 번만 하고, 이후 배포는 GitHub Actions 가 한다.

1. Docker Engine + compose plugin 설치. 배포 계정을 `docker` 그룹에 넣는다.
2. 인바운드는 80(HTTP)과 22(SSH, 내 IP 만).
3. `git clone <repo> ~/marketlens`
4. `~/marketlens/server/.env` 작성 (git 에 없음):
   - `INFLUX_URL` — compose 안 Influx 서비스 주소(서비스명으로 접근, localhost 아님)
   - `INFLUX_TOKEN`, `REFRESH_TOKEN` — `openssl rand -hex 32`. `INFLUX_TOKEN` 은 Influx **첫 기동 전**에 정해야 한다 (setup 이 그 값을 admin 토큰으로 쓴다).
   - 거래소 키는 선택. 업비트는 EC2 IP 를 Open API 허용 목록에 등록.
5. `:80` 을 쓰는 컨테이너가 있으면 내린다. crontab 에 `/refresh` 를 부르는 항목이 있으면 지운다 (앱이 1초마다 수집하므로 이중 수집).
6. GitHub Secrets `EC2_HOST`·`EC2_USER`·`EC2_SSH_KEY` 등록 → main 푸시 → Actions 로그 → `curl http://<EC2_HOST>/api/health`.
7. 배포 직후 백필 1회 (005 §3.5). 업비트 초봉이 3개월 롤링이라 미룰수록 과거를 잃는다.
