# CloudWatch 관측 설치 (사람이 한다) — 스펙 027

리전은 전부 서울 `ap-northeast-2`. 설정의 진실은 레포의 `ops/cloudwatch/*.json`(에이전트)·`ops/canary/index.mjs`(canary)이고, 이 문서는 그걸 박스·계정에 올리는 순서다. 배포 워크플로는 에이전트·경보·canary 를 건드리지 않는다 — 설정 파일을 고친 PR 이 머지되면 그 박스에서 5-2 의 불러오기를 다시 한다.
순서는 027 §3.7 그대로다. 단계마다 **확인** 이 끝나야 다음으로 가고, 문제가 있으면 **되돌리기** 로 그 단계만 되돌린다.

로컬 셸 공통 변수(값은 status.md 의 인스턴스 ID):
```bash
export AWS_REGION=ap-northeast-2
COLLECT=i-004484baaca306d88 DATA=i-093fe9266b10c03d3 SERVE=i-0feb121f158f966fa
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
TOPIC=arn:aws:sns:$AWS_REGION:$ACCOUNT:marketlens-alerts
```

## 관리자가 할 일 (한 번에 묶어 부탁한다)
CLI 사용자(jin)는 `iam:PassRole` 이 없어 역할을 인스턴스·canary 에 못 붙인다. 콘솔 관리자에게 아래 넷을 한 번에 부탁한다.
1. **역할·정책** — collect 에 `marketlens-s3-snapshot` 이 붙어 있는지 확인하고(없으면 붙인다 — status.md 남은 작업), 그 역할에 관리형 정책 `CloudWatchAgentServerPolicy` 를 더한다. 역할 `marketlens-cwagent`(신뢰 = EC2, 정책 = `CloudWatchAgentServerPolicy`)를 만들어 data·serve 에 붙인다 — **3단계(IMDS) 확인 뒤에**.
2. **canary 생성** — 11단계. 실행 역할은 콘솔이 새로 만들게 둔다.
3. **Q Developer Slack 채널** — 9단계. Slack 워크스페이스 승인(Slack 관리자)과 채널 역할이 필요하다.
4. **EC2 동작 경보용 서비스 연결 역할** — 재부팅·복구 동작이 걸린 경보를 처음 만들 때 CloudWatch 가 `AWSServiceRoleForCloudWatchEvents` 를 쓴다. 없으면 관리자가 `aws iam create-service-linked-role --aws-service-name events.amazonaws.com` 을 한 번 하거나 10단계의 상태검사 경보 하나를 콘솔에서 만든다.

## 1. 예산
콘솔 Billing → Budgets → 월 비용 예산 $130, 알림 둘(실제 100%·예측 100%) → 이메일.
- 확인: 예산 목록에 1개, 알림 2개.
- 되돌리기: 예산 삭제.

## 2. 기존 경보·지표 수
무료 한도(사용자 지표 10개·경보 10개)는 **계정 전체(모든 리전)** 기준이다. 027 §3.7 비용표는 지금 0개를 전제로 한다.
```bash
for r in $(aws ec2 describe-regions --query 'Regions[].RegionName' --output text); do
  echo "$r alarms=$(aws cloudwatch describe-alarms --region $r --query 'length(MetricAlarms)')"
done
aws cloudwatch list-metrics --namespace MarketLens --query 'length(Metrics)'
```
- 확인: 결과를 027 §7 에 적는다. 0 이 아니면 비용표를 고쳐 적는다.
- 되돌리기: 없음(읽기만).

## 3. IMDS — data·serve 는 토큰 필수·hop 1
역할을 붙이기 **전에** 한다. 도커 브리지 안의 컨테이너가 인스턴스 역할 자격증명에 닿지 못하게 한다. collect 는 컨테이너가 S3 에 올리므로 hop 2 그대로 둔다(010).
```bash
for id in $DATA $SERVE; do
  aws ec2 modify-instance-metadata-options --instance-id $id --http-tokens required --http-put-response-hop-limit 1 --http-endpoint enabled
done
aws ec2 describe-instances --instance-ids $COLLECT $DATA $SERVE \
  --query 'Reservations[].Instances[].[InstanceId,MetadataOptions.HttpTokens,MetadataOptions.HttpPutResponseHopLimit]' --output text
```
- 확인: data·serve 는 `required 1`, collect 는 hop 2. serve 에서 `docker exec marketlens-api python -c "import urllib.request as u; u.urlopen(u.Request('http://169.254.169.254/latest/api/token', method='PUT', headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'}), timeout=2)"` 이 시간 초과로 실패한다(컨테이너는 못 닿는다).
- 되돌리기: `--http-tokens optional --http-put-response-hop-limit 2`.

## 4. 역할
관리자 절 1번. 붙인 뒤:
```bash
aws ec2 describe-iam-instance-profile-associations --filters Name=instance-id,Values=$COLLECT,$DATA,$SERVE \
  --query 'IamInstanceProfileAssociations[].[InstanceId,IamInstanceProfile.Arn,State]' --output text
```
- 확인: 세 줄 모두 `associated`, collect 는 `marketlens-s3-snapshot`, data·serve 는 `marketlens-cwagent`. collect 의 S3 원문 업로드 실패 경고가 멈춘다(`docker logs marketlens-server 2>&1 | grep -c S3` 가 더 늘지 않는다).
- 되돌리기: `aws ec2 disassociate-iam-instance-profile --association-id <id>`.

## 5. data 에이전트 (RSS 24시간)
### 5-1. 설치 (세 박스 공통 — 각 박스에서)
호스트에 설치한다(컨테이너 아님, arm64). root 로 돈다 — 설정에 `run_as_user` 를 두지 않는다(홈 디렉터리가 0750 이라 다른 사용자는 로그 파일에 못 닿는다). systemd 로 메모리 200MB 상한.
```bash
cd /tmp && wget -q https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/arm64/latest/amazon-cloudwatch-agent.deb
sudo dpkg -i -E ./amazon-cloudwatch-agent.deb
sudo mkdir -p /etc/systemd/system/amazon-cloudwatch-agent.service.d
printf '[Service]\nMemoryMax=200M\n' | sudo tee /etc/systemd/system/amazon-cloudwatch-agent.service.d/memory.conf
sudo systemctl daemon-reload
```
### 5-2. 설정 불러오기 (박스 이름만 바꿔서)
```bash
CTL=/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl
sudo $CTL -a fetch-config -m ec2 -s -c file:/home/ubuntu/marketlens/ops/cloudwatch/data.json
```
serve 는 로그 전송을 켠 뒤로(15단계) **늘 두 단계**다 — `serve.json` 을 불러온 다음 `serve-logs.json` 을 덧붙인다. 앞 단계만 하면 로그 설정이 지워진다.
```bash
sudo $CTL -a fetch-config -m ec2 -s -c file:/home/ubuntu/marketlens/ops/cloudwatch/serve.json
sudo $CTL -a append-config -m ec2 -s -c file:/home/ubuntu/marketlens/ops/cloudwatch/serve-logs.json
```
- 확인: `sudo $CTL -a status` 가 `running`, `systemctl show amazon-cloudwatch-agent -p MemoryMax` 가 `209715200`. 3~5분 뒤 `aws cloudwatch list-metrics --namespace MarketLens --dimensions Name=InstanceId,Value=$DATA` 에 5개(메모리·스왑·디스크·`influxd`·`redis-server` RSS). 경보는 여기 나온 **차원 그대로** 만든다(디스크의 `path`·`fstype`, procstat 의 프로세스 식별자는 남는다). 24시간 뒤 `systemctl show amazon-cloudwatch-agent -p MemoryPeak` 를 027 §7 에 적는다.
- 되돌리기: `sudo $CTL -a stop && sudo dpkg -r amazon-cloudwatch-agent`.

## 6. collect 에이전트 (CPU 전후)
수집기가 1 vCPU 를 80~100% 쓰므로 지표는 둘(메모리·디스크)만, 300초 주기다. 설치 **전** 10분 동안 수집기 CPU 를 잰다:
```bash
for i in $(seq 10); do docker stats --no-stream --format '{{.CPUPerc}}' marketlens-server; sleep 60; done
```
5-1 설치 → 5-2 를 `collect.json` 으로 → 같은 명령으로 10분 더 잰다.
- 확인: 평균이 1%p 넘게 늘지 않았다. 지표 2개. 전후 값을 027 §7 에 적는다.
- 되돌리기(1%p 넘게 늘면): 에이전트를 지운다(5-2 되돌리기) — collect 의 메모리·디스크 경보는 빼고 경보 수를 고쳐 적는다.

## 7. serve 스왑 1GB
t4g.micro(1GB)는 올리지 않고 스왑을 붙인다(data 와 같은 방식). 스왑 없는 1GB 박스가 배포마다 web 이미지를 직접 빌드하는데 에이전트가 더해지기 때문이다.
```bash
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```
- 확인: `free -m` 의 Swap total 1023.
- 되돌리기: `sudo swapoff /swapfile && sudo rm /swapfile` + fstab 줄 삭제.

## 8. serve 에이전트
5-1 설치 → 5-2 를 `serve.json` 으로(로그 전송 전이라 한 단계). StatsD 는 호스트 `:8125` 로 받고, api 컨테이너가 `host.docker.internal`(호스트 게이트웨이)로 10초마다 보낸다.
- 확인: 지표 5개(메모리·디스크·`caddy`·api RSS·`marketlens.ws_clients`). StatsD 가 지표로 생기는지 따로 본다(에이전트 버전의 StatsD 결함 보고가 있다): `echo -n 'marketlens.ws_clients:0|g' | nc -u -w1 127.0.0.1 8125` 뒤 3분 안에 `aws cloudwatch list-metrics --namespace MarketLens --metric-name marketlens.ws_clients`. 안 생기면 `$CTL -a status` 의 버전과 함께 게이지만 빚으로 남기고 진행한다(api 코드는 그대로). `/app/` 탭 2개를 열면 3분 안에 최댓값이 2 늘고 닫으면 다음 구간에 준다.
- 되돌리기: 5-2 되돌리기. 스왑은 7단계에서 따로.

## 9. Slack 연결
```bash
aws sns create-topic --name marketlens-alerts
```
콘솔 Amazon Q Developer in chat applications → Slack 클라이언트 구성(워크스페이스 승인) → 채널 구성: 025 와 같은 채널, 채널 역할은 새로 만들기(알림 권한 템플릿), 알림 = SNS 주제 `marketlens-alerts`(서울). 비공개 채널이면 채널에서 `/invite @Amazon Q` 를 먼저 한다.
- 확인: 채널 구성의 "테스트 메시지 보내기" 가 채널에 온다.
- 되돌리기: 채널 구성 삭제 → `aws sns delete-topic --topic-arn $TOPIC`.
