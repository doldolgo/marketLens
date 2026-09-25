# EC2 3대 분리 (사람이 한다) — 스펙 021

스펙 021 의 전환 절차. 콘솔·SSH 로 **사람**이 한다. 박스 공통 준비(도커·env 작성 규칙·IAM 역할·hop limit·GitHub Secrets 형식)는 `ec2-setup.md` 를 그대로 따르고, 여기엔 3대라서 달라지는 것만 적는다.
각 단계에 **확인** 과 **되돌리기** 가 있다. 되돌리기는 언제나 "구 박스(`i-0ccec33dba9e27017`, t4g.medium)를 다시 켜고 탄력 IP 를 돌려붙인다" 로 끝난다 — 구 박스는 11단계 전까지 절대 지우지 않는다.
전체 예상 중단: 6~9단계 사이 **5분 안팎**(볼륨 복사 + 수집 워밍업). 그 사이 틱은 유실된다(구 Redis 의 AOF 도 같이 옮기므로 정지 직전까지는 보존).

## 0. 준비물
- 리전 `ap-northeast-2`, 구 박스와 **같은 VPC·같은 서브넷**(`subnet-02a11e83f5f5d65d1`), 키 페어 `team`.
- 로컬에 `aws` CLI 와 `~/.ssh/team.pem`. 구 박스 SSH 별칭 `team`.
- GitHub 관리자 권한(Secrets 변경).

## 1. 보안그룹 3개 생성
이름은 `marketlens-collect`·`marketlens-data`·`marketlens-serve`. 아웃바운드는 기본(전체 허용) 그대로.

| 그룹 | 인바운드 규칙 |
|---|---|
| collect | TCP 8000 ← `marketlens-serve` 그룹 / TCP 22 ← 0.0.0.0/0 |
| data | TCP 6379 ← `marketlens-collect`·`marketlens-serve` 그룹 / TCP 8086 ← 같은 두 그룹 / TCP 22 ← 0.0.0.0/0 |
| serve | TCP 80 ← 0.0.0.0/0 / TCP 22 ← 0.0.0.0/0 |

소스에 CIDR 대신 **보안그룹 ID** 를 넣는다 — 사설 IP 가 바뀌어도 규칙이 산다. 22 는 내 IP 로 좁히지 못한다 — 배포 워크플로(GitHub Actions 러너, 동적 IP)가 세 박스에 SSH 로 붙기 때문이다. 키 인증만 허용된다.
- 확인: `aws ec2 describe-security-groups --filters Name=group-name,Values='marketlens-*' --query 'SecurityGroups[].[GroupName,GroupId]' --output text` 에 3줄.
- 되돌리기: 그룹 삭제(아직 아무것도 안 붙어 있다).

## 2. 인스턴스 3대 생성
공통: Ubuntu 24.04 **arm64**, 서브넷 위와 같음, 공인 IP 자동 할당 켬, 루트 gp3, 키 `team`, Name 태그.

| 박스 | 종류 | 루트 | 보안그룹 | 추가 |
|---|---|---|---|---|
| collect | c7g.medium | 16GB | marketlens-collect | IAM 프로파일 `marketlens-s3-snapshot`(콘솔 관리자 — CLI 사용자 jin 은 `iam:PassRole` 이 없어 못 붙인다), 탄력 IP 새로 1개 |
| data | t4g.small | 30GB | marketlens-data | 크레딧 unlimited(기본) |
| serve | t4g.micro | 8GB | marketlens-serve | 없음(탄력 IP 는 9단계에서 옮김) |

data 의 루트를 30GB 로 두는 이유: Influx 볼륨 1.8GB + 성장분 + 도커 이미지. collect 는 원문을 S3 로 보내므로 16GB 면 된다.
생성 뒤 세 박스에서 `ec2-setup.md` 1번(도커·compose 플러그인, `ubuntu` 를 `docker` 그룹에)과 3번(`git clone … ~/marketlens`)을 한다. **배포 키도 옮긴다**: GitHub Secret `EC2_SSH_KEY` 는 `team.pem` 이 아니라 별도 키라, 구 박스 `~/.ssh/authorized_keys` 의 줄들을 세 박스의 `authorized_keys` 에 덧붙여야 Deploy 의 SSH 가 붙는다(안 하면 `unable to authenticate`).
- collect 만: `aws ec2 modify-instance-metadata-options --instance-id <collect id> --http-put-response-hop-limit 2 --http-endpoint enabled` (ec2-setup.md 5-2).
- 확인: 세 박스 `docker compose version` 이 답하고, `~/marketlens` 가 main 이다. 사설 IP 3개를 적어 둔다: `aws ec2 describe-instances --filters Name=tag:Name,Values='marketlens-*' --query 'Reservations[].Instances[].[Tags[?Key==`Name`].Value|[0],PrivateIpAddress,PublicIpAddress]' --output text`.
- 되돌리기: 인스턴스 종료(아직 트래픽 없음).

## 3. data 박스 — 스왑
```bash
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
- 확인: `free -m` 의 Swap total 1023.
- 되돌리기: `sudo swapoff /swapfile && sudo rm /swapfile` + fstab 줄 삭제.

## 4. env 파일 3벌
`server/.env` 는 구 박스의 것을 **파일째** 옮긴다(값을 화면에 내지 않는다):
```bash
# 로컬에서 — 구 박스 → 새 박스, 화면에 값이 안 뜬다
ssh team 'cat ~/marketlens/server/.env' | ssh -i ~/.ssh/team.pem ubuntu@<새 박스 공인 IP> 'cat > ~/marketlens/server/.env && chmod 600 ~/marketlens/server/.env'
```
세 박스에 같은 파일을 넣어도 된다(data 는 `INFLUX_TOKEN` 만 쓴다). 루트 `.env` 는 박스마다 다르다:

| 박스 | 루트 `.env` 내용 |
|---|---|
| collect | `DATA_HOST=<data 사설 IP>` |
| data | (비움 — 파일은 있어야 한다: `touch .env`) |
| serve | `WEB_PORT=80` / `DATA_HOST=<data 사설 IP>` / `COLLECT_HOST=<collect 사설 IP>` |

- 확인: 각 박스 `grep -c . ~/marketlens/.env ~/marketlens/server/.env` 가 줄 수만 답한다(값 미출력). serve 는 3줄.
- 되돌리기: 파일 삭제.

## 5. 업비트 허용 IP 등록
collect 의 **탄력 IP** 를 업비트 Open API 키의 허용 IP 에 추가한다(기존 `3.34.104.16` 은 9단계 뒤 serve 로 가므로 남겨도 무해). 등록 전엔 입출금 조회가 401 로 실패하고 업비트 행이 `unknown` 이 된다 — 수집 자체는 된다.
- 확인: 7단계 뒤 collect 로그에 `upbit 입출금 상태 조회 실패` 가 없다.

## 6. 구 박스 정지 + 볼륨 복사 (중단 시작)
Influx 볼륨은 6GB 를 넘어(2026-09-25 실측 6.4GB) 정지 후 통째 복사하면 중단이 10분을 넘는다. **가동 중에 먼저 rsync 로 미리 복사해 두고, 정지 후엔 차분만 맞춘다** — 2026-09-25 실전환에서 차분 25MB, 정지부터 serve 응답까지 40초였다. 아래 tar 절차는 볼륨이 작을 때의 대안으로 남긴다.
```bash
# data 박스에서 볼륨을 먼저 만든다(이름이 compose 프로젝트명+볼륨명이어야 붙는다)
docker volume create marketlens_influxdb-data && docker volume create marketlens_redis-data
# 로컬에서 — 구 박스가 data 박스로 직접 보낸다(에이전트 포워딩으로 인증, 사설 IP). 가동 중 1회 + 정지 후 1회 같은 명령
ssh-add ~/.ssh/team.pem
ssh -A team "sudo --preserve-env=SSH_AUTH_SOCK rsync -a --delete --stats --rsync-path='sudo rsync' /var/lib/docker/volumes/marketlens_influxdb-data/_data/ ubuntu@<data 사설 IP>:/var/lib/docker/volumes/marketlens_influxdb-data/_data/"
ssh -A team "sudo --preserve-env=SSH_AUTH_SOCK rsync -a --delete --stats --rsync-path='sudo rsync' /var/lib/docker/volumes/marketlens_redis-data/_data/ ubuntu@<data 사설 IP>:/var/lib/docker/volumes/marketlens_redis-data/_data/"
```
```bash
# 구 박스에서
cd ~/marketlens && docker compose --env-file .env --env-file server/.env stop server api   # 쓰기 중단
sleep 5 && docker compose --env-file .env --env-file server/.env stop influxdb redis        # 저장소 정지(일관된 복사)
docker run --rm -v marketlens_influxdb-data:/from -v /tmp:/to alpine tar czf /to/influx.tgz -C /from .
docker run --rm -v marketlens_redis-data:/from -v /tmp:/to alpine tar czf /to/redis.tgz -C /from .
ls -la /tmp/influx.tgz /tmp/redis.tgz
```
```bash
# 로컬에서 — 구 박스 → data 박스 (같은 VPC 라 사설 IP 로 직접 보내도 된다: 구 박스에서 scp)
ssh team 'scp -i ~/.ssh/team.pem -o StrictHostKeyChecking=no /tmp/influx.tgz /tmp/redis.tgz ubuntu@<data 사설 IP>:/tmp/'
```
구 박스에 `team.pem` 이 없으면 로컬을 거친다: `scp team:/tmp/influx.tgz . && scp -i ~/.ssh/team.pem influx.tgz ubuntu@<data 공인 IP>:/tmp/`.
```bash
# data 박스에서 — 볼륨 이름은 compose 프로젝트명(marketlens) + 볼륨명. 반드시 이 이름이어야 compose 가 붙인다.
docker volume create marketlens_influxdb-data && docker volume create marketlens_redis-data
docker run --rm -v marketlens_influxdb-data:/to -v /tmp:/from alpine tar xzf /from/influx.tgz -C /to
docker run --rm -v marketlens_redis-data:/to -v /tmp:/from alpine tar xzf /from/redis.tgz -C /to
```
- 확인: data 에서 `docker run --rm -v marketlens_influxdb-data:/v alpine du -sh /v` 가 1.8G 안팎, `ls /v/influxd.bolt` 가 있다.
- 되돌리기: 구 박스에서 `docker compose … start influxdb redis server api` — 복사는 읽기만 했으므로 원본 무손상.

## 7. data 기동
```bash
cd ~/marketlens && docker compose --profile data --env-file .env --env-file server/.env up -d
```
- 확인: `docker ps` 에 `marketlens-influxdb`·`marketlens-redis` 둘, `curl -s localhost:8086/health` 가 `"status":"pass"`, `docker exec marketlens-redis redis-cli XLEN ticks` 가 숫자(구 박스 정지 직전 잔량), Influx env 에 쿼리 상한 3개(`docker inspect marketlens-influxdb --format '{{.Config.Env}}' | tr ' ' '\n' | grep INFLUXD_QUERY`).
- 되돌리기: `docker compose --profile data … down`(볼륨 유지) → 6단계 되돌리기.

## 8. collect 기동
```bash
cd ~/marketlens && docker compose --profile collect --env-file .env --env-file server/.env up -d --build   # 빌드 3~5분(1 vCPU)
```
- 확인(기동 2분 뒤): `docker ps` 에 `marketlens-server` 하나. `curl -s localhost:8000/health/collect | head -c 400` 에 5거래소 `ok`. `docker exec marketlens-redis redis-cli XLEN ticks` 를 data 에서 두 번 재면 60초 안에 줄었다 늘었다 한다(flusher 가 data 의 Influx 에 쓴다). `docker stats --no-stream marketlens-server` CPU 80~100%, `top -bn1 | grep Cpu` 의 `st` 0.0. S3: 2분 뒤 `aws s3 ls s3://marketlens-spreads-snapshot/raw/exchange=upbit/ --recursive | tail -1` 의 시각이 방금.
- 되돌리기: `docker compose --profile collect … down` → 7·6 되돌리기.

## 9. serve 기동 + 탄력 IP 이동
```bash
cd ~/marketlens && docker compose --profile serve --env-file .env --env-file server/.env up -d --build
curl -s localhost/api/health && curl -s -o /dev/null -w '%{http_code} %{size_download}\n' localhost/api/spreads   # 200, 700KB 안팎
```
그 다음 로컬에서 탄력 IP 를 옮긴다(연결이 몇 초 끊긴다):
```bash
ALLOC=$(aws ec2 describe-addresses --public-ips 3.34.104.16 --query 'Addresses[0].AllocationId' --output text)
aws ec2 associate-address --allocation-id $ALLOC --instance-id <serve id> --allow-reassociation
```
- 확인: 브라우저 `http://3.34.104.16/` 스프레드 표 매초 갱신, 기록 탭 차트, 수집 상태 탭 5거래소 ok. `curl /api/spreads` 첫 응답은 404 일 수 있다 — 아무도 안 보면 표 발행이 멈추는 017 동작이라 몇 초 뒤 다시 부르면 200. 탄력 IP 를 옮기면 serve 의 임시 공인 IP 는 사라지고, `ssh team` 은 호스트 키가 바뀌었다고 거부하므로 `ssh-keygen -R 3.34.104.16` 뒤 접속한다. `ssh team` 은 이제 serve 로 붙는다(`~/.ssh/config` 의 `team` 은 그대로 serve 를 뜻하게 된다 — collect·data 별칭을 추가한다).
- 되돌리기: `aws ec2 associate-address --allocation-id $ALLOC --instance-id i-0ccec33dba9e27017 --allow-reassociation` 후 구 박스 `docker compose … up -d`(5컨테이너, 볼륨 그대로).

## 10. GitHub Secrets·워크플로
Secrets: `EC2_HOST_DATA`·`EC2_HOST_COLLECT`·`EC2_HOST_SERVE` 를 각 공인 IP(collect 는 탄력 IP, serve 는 `3.34.104.16`)로 추가, `EC2_HOST` 삭제. `EC2_USER`·`EC2_SSH_KEY` 는 그대로.
021 코드 PR(compose profile·워크플로 3타깃·nginx 템플릿)이 main 에 머지되면 Deploy 가 3타깃으로 돈다.
- 확인: Actions 의 Deploy 가 success 이고 세 박스 `docker ps` 가 자기 컨테이너만(1·2·2).
- 되돌리기: 워크플로를 이전 커밋으로 되돌린 PR + `EC2_HOST` 복구(구 박스).

## 11. 구 박스 정리
전환 확인 뒤 `aws ec2 stop-instances --instance-ids i-0ccec33dba9e27017`. **3일 뒤** 문제 없으면 `terminate-instances`(EBS 는 DeleteOnTermination 이면 같이 사라진다 — 볼륨 스냅샷을 남기고 싶으면 종료 전에 `aws ec2 create-snapshot --volume-id vol-0472106043af3a565`).
- 확인: 정지 뒤에도 9단계 확인이 유지된다.
- 되돌리기: `start-instances` + 9단계 되돌리기.

## 이후 운영 메모
- 사설 IP 가 바뀌는 작업(인스턴스 교체)을 하면 루트 `.env` 의 `DATA_HOST`/`COLLECT_HOST` 를 고치고 해당 박스 `up -d` 로 재기동한다(nginx·server·api 는 기동 시 주소를 푼다).
- 백필(005)은 collect 박스 컨테이너 안에서: `docker compose --profile collect … exec server <백필 명령>` — Influx 는 `DATA_HOST` 로 닿는다.
- Influx UI 는 data 박스에 SSH 터널로만: `ssh -i ~/.ssh/team.pem -L 8086:localhost:8086 ubuntu@<data 공인 IP>`.
