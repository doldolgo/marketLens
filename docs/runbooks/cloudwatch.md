# CloudWatch 관측 설치 (사람이 한다) — 스펙 027

리전은 전부 서울 `ap-northeast-2`. 설정의 진실은 레포의 `ops/cloudwatch/*.json`(에이전트)·`ops/canary/index.mjs`(canary)이고, 이 문서는 그걸 박스·계정에 올리는 순서다. 배포 워크플로는 에이전트·경보·canary 를 건드리지 않는다 — 설정 파일을 고친 PR 이 머지되면 그 박스에서 5-2 의 불러오기를 다시 하고, canary 스크립트를 고친 PR 이 머지되면 11-1 로 다시 묶어 `aws lambda update-function-code --function-name marketlens-smoke --zip-file fileb://canary.zip` 로 올린다.
순서는 027 §3.7 그대로다. 단계마다 **확인** 이 끝나야 다음으로 가고, 문제가 있으면 **되돌리기** 로 그 단계만 되돌린다.

공통 변수(로컬 셸·CloudShell, 값은 status.md 의 인스턴스 ID):
```bash
export AWS_REGION=ap-northeast-2
COLLECT=i-004484baaca306d88 DATA=i-093fe9266b10c03d3 SERVE=i-0feb121f158f966fa
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
TOPIC=arn:aws:sns:$AWS_REGION:$ACCOUNT:marketlens-alerts
```
CloudShell 에서 할 때:
- 세션이 새로 열리면 변수가 사라진다 — 차원 값이 비어 `ParamValidation` 오류가 나면 위 변수 줄부터 다시.
- CloudShell 은 bash 다 — `${=BOXES}` 대신 `$BOXES`, `date -v-7d`·`-v-1d` 대신 `date -d '7 days ago'`·`-d '1 day ago'`.
- 메모리·디스크 경보(10-2)는 세 박스 지표가 보인 뒤에 만든다 — 데이터 없음 = 경보라 먼저 만들면 곧바로 울린다.

## 관리자가 할 일 (한 번에 묶어 부탁한다)
CLI 사용자(jin)는 `iam:PassRole` 이 없어 역할을 인스턴스·Lambda·일정에 못 넘긴다. 콘솔 관리자에게 아래 넷을 한 번에 부탁한다.
1. **역할·정책** — collect 에 `marketlens-s3-snapshot` 이 붙어 있는지 확인하고(없으면 붙인다 — status.md 남은 작업), 그 역할에 관리형 정책 `CloudWatchAgentServerPolicy` 를 더한다. 역할 `marketlens-cwagent`(신뢰 = EC2, 정책 = `CloudWatchAgentServerPolicy`)를 만들어 data·serve 에 붙인다 — **3단계(IMDS) 확인 뒤에**.
2. **canary — Lambda·일정** — 11단계. 역할 둘(`marketlens-smoke-lambda`·`marketlens-smoke-scheduler`)을 만들고 함수·일정을 만들 때 넘긴다. CloudShell(서울)에서 명령 그대로 한다.
3. **Q Developer Slack 채널** — 9단계. Slack 워크스페이스 승인(Slack 관리자)과 채널 역할이 필요하다.
4. **EC2 동작 경보용 서비스 연결 역할** — 재부팅·복구 동작이 걸린 경보를 처음 만들 때 CloudWatch 가 `AWSServiceRoleForCloudWatchEvents` 를 쓴다. 없으면 관리자가 `aws iam create-service-linked-role --aws-service-name events.amazonaws.com` 을 한 번 하거나 10단계의 상태검사 경보 하나를 콘솔에서 만든다.

## 1. 예산
콘솔 Billing → Budgets → 월 비용 예산 $130, 알림 셋(실제 85%·실제 100%·예측 100% — 85% 는 콘솔 템플릿이 더한다) → 이메일.
- 확인: 예산 목록에 1개, 알림 3개.
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
먼저 접속한 박스가 어느 것인지 `docker ps --format '{{.Names}}'` 로 확인한다 — collect 는 `marketlens-server`, data 는 `marketlens-influxdb`·`marketlens-redis`, serve 는 `marketlens-api`·`marketlens-web`·`marketlens-caddy`.
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
- 다른 박스의 파일을 불러왔으면(예: serve 에 `data.json` → `swap_used_percent` 가 생긴다): 그 박스의 파일로 다시 `fetch-config` 하면 된다 — 설정을 통째로 바꾼다. 잘못 생긴 지표는 새 값이 안 오면 2주 뒤 목록에서 사라진다.
- 되돌리기: `sudo $CTL -a stop && sudo dpkg -r amazon-cloudwatch-agent`.

## 6. collect 에이전트 (에이전트 CPU)
수집기가 1 vCPU 를 80~100% 쓰므로 지표는 둘(메모리·디스크)만, 300초 주기다. 5-1 설치 → 5-2 를 `collect.json` 으로 → 에이전트 자체 CPU 를 10분 잰다(systemd 가 센 CPU 시간의 10분 차이):
```bash
a=$(systemctl show amazon-cloudwatch-agent -p CPUUsageNSec --value); sleep 600; b=$(systemctl show amazon-cloudwatch-agent -p CPUUsageNSec --value); awk -v a=$a -v b=$b 'BEGIN{printf "에이전트 CPU %.2f%%\n",(b-a)/600e9*100}'
```
수집기 컨테이너의 `docker stats` 전후 비교로는 가르지 않는다 — 1분 값이 66~100% 로 흔들려 몇 %p 차이는 잡음이고, 호스트에서 도는 에이전트는 재지 않는다.
- 확인: 에이전트 CPU 가 1% 이하. 지표 2개. 값을 027 §7 에 적는다.
- 되돌리기(1% 를 넘으면): 에이전트를 지운다(5-2 되돌리기) — collect 의 메모리·디스크 경보는 빼고 경보 수를 고쳐 적는다.

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
- 확인: 지표 5개(메모리·디스크·`caddy`·api RSS·`marketlens_ws_clients` — 에이전트가 점을 밑줄로 바꿔 올린다). StatsD 가 지표로 생기는지 따로 본다(에이전트 버전의 StatsD 결함 보고가 있다): `echo -n 'marketlens.ws_clients:0|g' | nc -u -w1 127.0.0.1 8125` 뒤 3분 안에 `aws cloudwatch list-metrics --namespace MarketLens --metric-name marketlens_ws_clients`. 안 생기면 `sudo $CTL -a status` 의 버전과 함께 게이지만 빚으로 남기고 진행한다(api 코드는 그대로). `/app/` 탭 2개를 열면 3분 안에 최댓값이 2 늘고 닫으면 다음 구간에 준다.
- 되돌리기: 5-2 되돌리기. 스왑은 7단계에서 따로.

## 9. Slack 연결
```bash
aws sns create-topic --name marketlens-alerts
```
콘솔 Amazon Q Developer in chat applications → Slack 클라이언트 구성(워크스페이스 승인) → 채널 구성: 025 와 같은 채널, 채널 역할은 새로 만들기(알림 권한 템플릿), 알림 = SNS 주제 `marketlens-alerts`(서울). 비공개 채널이면 채널에서 `/invite @Amazon Q` 를 먼저 한다.
- 확인: 채널 구성의 "테스트 메시지 보내기" 가 채널에 온다.
- 되돌리기: 채널 구성 삭제 → `aws sns delete-topic --topic-arn $TOPIC`.

## 10. 경보 (canary·5xx·잔고 제외 — 14개)
모든 경보는 `marketlens-alerts` 로 보내고 풀릴 때(OK)도 보낸다. 둘로 나눠 만든다 — 10-1(상태·잉여 크레딧)은 지금, 10-2(메모리·디스크)는 세 박스의 에이전트 지표가 보인 뒤. 메모리·디스크는 데이터 없음 = 경보라 지표보다 먼저 만들면 곧바로 울린다.
두 절 공통(새 셸이면 공통 변수 줄부터):
```bash
BOXES="collect:$COLLECT data:$DATA serve:$SERVE"
put() { aws cloudwatch put-metric-alarm --alarm-actions "${ACTIONS[@]}" --ok-actions $TOPIC "$@"; }
```
(zsh 기준 — bash(CloudShell)면 아래 `${=BOXES}` 를 `$BOXES` 로.)

### 10-1. 지금 — 상태검사 6·잉여 크레딧 2
```bash
# 상태검사 — 인스턴스 3점 연속 → 재부팅, 시스템 2점 연속 → 복구. 기간이 다른 것은 AWS 권장(같으면 두 동작이 경쟁한다)
for pair in ${=BOXES}; do box=${pair%%:*} id=${pair#*:}
  ACTIONS=(arn:aws:automate:$AWS_REGION:ec2:reboot $TOPIC)
  put --alarm-name marketlens-$box-status-instance --namespace AWS/EC2 --metric-name StatusCheckFailed_Instance \
    --dimensions Name=InstanceId,Value=$id --statistic Maximum --period 60 --evaluation-periods 3 --datapoints-to-alarm 3 \
    --comparison-operator GreaterThanOrEqualToThreshold --threshold 1 --treat-missing-data missing
  ACTIONS=(arn:aws:automate:$AWS_REGION:ec2:recover $TOPIC)
  put --alarm-name marketlens-$box-status-system --namespace AWS/EC2 --metric-name StatusCheckFailed_System \
    --dimensions Name=InstanceId,Value=$id --statistic Maximum --period 60 --evaluation-periods 2 --datapoints-to-alarm 2 \
    --comparison-operator GreaterThanOrEqualToThreshold --threshold 1 --treat-missing-data missing
done
ACTIONS=($TOPIC)
# unlimited 잉여 과금 시작 — data·serve
for pair in data:$DATA serve:$SERVE; do box=${pair%%:*} id=${pair#*:}
  put --alarm-name marketlens-$box-credit-surplus --namespace AWS/EC2 --metric-name CPUSurplusCreditsCharged \
    --dimensions Name=InstanceId,Value=$id --statistic Sum --period 300 --evaluation-periods 1 \
    --comparison-operator GreaterThanThreshold --threshold 0 --treat-missing-data missing
done
```
- 확인: `aws cloudwatch describe-alarms --alarm-name-prefix marketlens- --query 'MetricAlarms[].[AlarmName,StateValue]' --output text` 에 8줄, 몇 분 뒤 전부 `OK`. 상태검사 경보에는 `set-alarm-state` 시험을 하지 않는다(재부팅·복구가 실제로 돈다).
- 되돌리기: `aws cloudwatch delete-alarms --alarm-names <이름…>`.

### 10-2. 에이전트 지표가 보인 뒤 — 메모리 3·디스크 3
세 박스 모두 5-2 확인의 지표가 `list-metrics` 에 보인 뒤. 5-2 확인에서 본 **실제 차원 그대로** 만든다. 디스크의 `fstype` 은 `list-metrics` 에 나온 값이다(보통 `ext4`).
```bash
ACTIONS=($TOPIC)
# 메모리 가용률 10% 미만 5분 연속(collect 는 300초 1점) — 데이터 없음 = 경보(에이전트가 죽은 것)
for spec in collect:$COLLECT:300:1 data:$DATA:60:5 serve:$SERVE:60:5; do IFS=: read box id period n <<< "$spec"
  put --alarm-name marketlens-$box-memory --namespace MarketLens --metric-name mem_available_percent \
    --dimensions Name=InstanceId,Value=$id --statistic Minimum --period $period --evaluation-periods $n --datapoints-to-alarm $n \
    --comparison-operator LessThanThreshold --threshold 10 --treat-missing-data breaching
done
# 디스크 사용률 80% 초과 — 데이터 없음 = 경보
FSTYPE=ext4
for pair in ${=BOXES}; do box=${pair%%:*} id=${pair#*:}
  put --alarm-name marketlens-$box-disk --namespace MarketLens --metric-name disk_used_percent \
    --dimensions Name=InstanceId,Value=$id Name=path,Value=/ Name=fstype,Value=$FSTYPE --statistic Maximum --period 300 \
    --evaluation-periods 1 --comparison-operator GreaterThanThreshold --threshold 80 --treat-missing-data breaching
done
```
- 확인: `aws cloudwatch describe-alarms --alarm-names marketlens-{collect,data,serve}-{memory,disk} --query 'MetricAlarms[].[AlarmName,StateValue]' --output text` 에 6줄, 몇 분 뒤 전부 `OK`. 10-2 는 세 박스 지표를 기다리느라 12·13단계보다 늦을 수 있어 `--alarm-name-prefix marketlens-` 전체 줄 수는 그때그때 다르다(12·13 까지 끝났으면 17줄). 시험은 **EC2 동작이 없는** 경보 하나로만 한다 — `aws cloudwatch set-alarm-state --alarm-name marketlens-data-disk --state-value ALARM --state-reason "027 시험"` → Slack 에 ALARM 한 줄, 다음 평가에 OK 한 줄.
- 되돌리기: `aws cloudwatch delete-alarms --alarm-names <이름…>`.

## 11. canary — Lambda + 일정 (관리자, CloudShell)
CloudWatch Synthetics 는 이 계정에서 조직 SCP 가 막는다(027 §3.5) — 같은 스크립트를 일반 Lambda 로 만들고 EventBridge Scheduler 가 5분마다 부른다. 함수·일정을 만들 때 역할을 넘기므로(`iam:PassRole`) 관리자가 CloudShell(서울)에서 한다. 공통 변수 줄(`AWS_REGION`·`ACCOUNT`·`TOPIC`)부터.
- 로컬 확인(만들기 전): `node ops/canary/index.mjs` 가 `canary 통과` — 실패하면 단계 번호가 든 한 줄을 낸다. Node 22 이상(4단계가 내장 WebSocket 을 쓴다).
- 5분보다 자주 돌리지 않는다 — 실행마다 수집기·api·WebSocket 을 한 번씩 부른다.

### 11-1. 코드 묶기
```bash
cd ~ && curl -sfL https://raw.githubusercontent.com/doldolgo/marketLens/main/ops/canary/index.mjs -o index.mjs && zip -q canary.zip index.mjs
```
- 확인: `unzip -l canary.zip` 에 `index.mjs` 하나(zip 루트). zip 은 레포에 커밋하지 않는다.

### 11-2. 실행 역할
```bash
aws iam create-role --role-name marketlens-smoke-lambda --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' --query Role.Arn --output text
aws iam attach-role-policy --role-name marketlens-smoke-lambda --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
```
- 확인: `aws iam list-attached-role-policies --role-name marketlens-smoke-lambda` 에 `AWSLambdaBasicExecutionRole` 하나(로그 쓰기만).
- 되돌리기: `aws iam detach-role-policy --role-name marketlens-smoke-lambda --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole && aws iam delete-role --role-name marketlens-smoke-lambda`.

### 11-3. 함수
```bash
aws lambda create-function --function-name marketlens-smoke --runtime nodejs22.x --architectures arm64 --handler index.handler \
  --zip-file fileb://canary.zip --role arn:aws:iam::$ACCOUNT:role/marketlens-smoke-lambda --timeout 60 --memory-size 128 \
  --query FunctionArn --output text
aws lambda wait function-active-v2 --function-name marketlens-smoke
# 비동기 호출 재시도 0 — 켜 두면 실패 한 번이 세 번으로 센다
aws lambda put-function-event-invoke-config --function-name marketlens-smoke --maximum-retry-attempts 0
```
역할을 막 만들었으면 "cannot be assumed by Lambda" 로 실패할 수 있다 — 전파 전이다, 10초 뒤 다시. 환경 변수는 두지 않는다(기본 `https://kimptrack.com`). 직접 실행해 본다:
```bash
aws lambda invoke --function-name marketlens-smoke --log-type Tail --query LogResult --output text /tmp/out.json | base64 -d | grep -E "단계|Error"; cat /tmp/out.json; echo
```
- 확인: `1단계 통과 (…ms)` ~ `4단계 통과 (…ms)` 네 줄과 `"ok"`. 실패면 `Error` 줄과 `out.json` 에 `N단계 실패: …`. 이 호출로 로그 그룹 `/aws/lambda/marketlens-smoke` 가 생긴다.
- 되돌리기: `aws lambda delete-function --function-name marketlens-smoke`.

### 11-4. 일정 역할
```bash
aws iam create-role --role-name marketlens-smoke-scheduler --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole"}]}' --query Role.Arn --output text
aws iam put-role-policy --role-name marketlens-smoke-scheduler --policy-name invoke-smoke --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":\"arn:aws:lambda:$AWS_REGION:$ACCOUNT:function:marketlens-smoke\"}]}"
```
정책은 셸 변수를 풀어야 해서 큰따옴표 안에 따옴표를 이스케이프한다.
- 확인: `aws iam get-role-policy --role-name marketlens-smoke-scheduler --policy-name invoke-smoke --query PolicyDocument` 의 `Resource` 가 그 함수 하나.
- 되돌리기: `aws iam delete-role-policy --role-name marketlens-smoke-scheduler --policy-name invoke-smoke && aws iam delete-role --role-name marketlens-smoke-scheduler`.

### 11-5. 일정 (5분)
```bash
sleep 10   # 새 역할 전파
aws scheduler create-schedule --name marketlens-smoke --schedule-expression 'rate(5 minutes)' --flexible-time-window Mode=OFF \
  --target "{\"Arn\":\"arn:aws:lambda:$AWS_REGION:$ACCOUNT:function:marketlens-smoke\",\"RoleArn\":\"arn:aws:iam::$ACCOUNT:role/marketlens-smoke-scheduler\",\"RetryPolicy\":{\"MaximumRetryAttempts\":0}}" \
  --query ScheduleArn --output text
```
- 확인: `aws scheduler get-schedule --name marketlens-smoke --query State` 가 `ENABLED`, 10분 뒤 `aws logs tail /aws/lambda/marketlens-smoke --since 10m | grep 단계` 에 `4단계 통과` 줄.
- 되돌리기: `aws scheduler delete-schedule --name marketlens-smoke`.

### 11-6. 로그 보존 30일
```bash
aws logs put-retention-policy --log-group-name /aws/lambda/marketlens-smoke --retention-in-days 30
```
- 확인: `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/marketlens-smoke --query 'logGroups[].retentionInDays'` 가 `30`.

## 12. canary 경보
일정이 돈 뒤. 5분 구간 둘이 연속으로 실패를 담으면(10분) 울린다 — 배포 중 1회 실패는 울리지 않는다. 시간 초과(60초)도 `Errors` 로 센다. 데이터 없음 = 경보 — 일정이 멈추면 `Errors` 점이 생기지 않는다.
```bash
aws cloudwatch put-metric-alarm --alarm-name marketlens-canary --namespace AWS/Lambda --metric-name Errors \
  --dimensions Name=FunctionName,Value=marketlens-smoke --statistic Sum --period 300 --evaluation-periods 2 --datapoints-to-alarm 2 \
  --comparison-operator GreaterThanOrEqualToThreshold --threshold 1 --treat-missing-data breaching --alarm-actions $TOPIC --ok-actions $TOPIC
```
- 확인: 10분 뒤 `OK`. 되돌리기: 경보 삭제.
- canary 전체 되돌리기(이 순서로): 일정 삭제(11-5) → 함수 삭제(11-3) → 역할 둘(정책을 뗀 뒤 — 11-4·11-2) → 경보 삭제 → `aws logs delete-log-group --log-group-name /aws/lambda/marketlens-smoke`. 일정을 지우면 `Errors` 점이 끊겨(데이터 없음 = 경보) 마지막 실행에서 10분쯤 뒤 `marketlens-canary` 가 울린다 — 경보 삭제까지 쉬지 않고 이어서 한다(늦으면 Slack 에 ALARM 한 줄, 경보를 지우면 끝).

## 13. 잔고 경보 (최근 7일 최솟값 확인 뒤)
기본 임계는 최대 적립의 30%(data t4g.small 576 → 173, serve t4g.micro 288 → 86)이고, 최근 7일 최솟값이 그보다 낮으면 그 최솟값보다 낮게 둔다 — 평상시에 울리지 않게.
```bash
for id in $DATA $SERVE; do
  aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name CPUCreditBalance --dimensions Name=InstanceId,Value=$id \
    --start-time $(date -u -v-7d +%FT%TZ) --end-time $(date -u +%FT%TZ) --period 3600 --statistics Minimum \
    --query 'sort_by(Datapoints,&Minimum)[0].Minimum'
done   # 리눅스 date 는 -d '7 days ago'
aws cloudwatch put-metric-alarm --alarm-name marketlens-data-credit-balance --namespace AWS/EC2 --metric-name CPUCreditBalance \
  --dimensions Name=InstanceId,Value=$DATA --statistic Minimum --period 300 --evaluation-periods 3 --datapoints-to-alarm 3 \
  --comparison-operator LessThanThreshold --threshold 173 --treat-missing-data missing --alarm-actions $TOPIC --ok-actions $TOPIC
```
serve 도 같게(`marketlens-serve-credit-balance`, `$SERVE`, 86). 10-2 까지 합쳐 경보는 17개다.
- 확인: 7일 최솟값과 고른 임계를 027 §7 에 적는다. 되돌리기: 경보 삭제.

## 14. serve 메모리 측정 (24시간·배포 1회)
serve 에이전트를 띄운 뒤 24시간과 main 배포 1회를 지나며 잰다. serve 에서 시작과 끝에 한 번씩:
```bash
grep -E '^pswp(in|out) ' /proc/vmstat; free -m; systemctl show amazon-cloudwatch-agent -p MemoryPeak
```
로컬에서 가용률 최저: `aws cloudwatch get-metric-statistics --namespace MarketLens --metric-name mem_available_percent --dimensions Name=InstanceId,Value=$SERVE --start-time $(date -u -v-1d +%FT%TZ) --end-time $(date -u +%FT%TZ) --period 60 --statistics Minimum --query 'sort_by(Datapoints,&Minimum)[0].Minimum'`.
- 판단: 가용률 최저가 10% 밑이거나 `pswpin`·`pswpout` 이 배포 밖에서도 계속 늘면(스왑을 계속 쓴다) t4g.small 승격(월 +$7.6, 정지 몇 분)을 사람이 정한다 — 그때 027 을 고치고 021 담당자에게 알린다. 아니면 t4g.micro + 스왑 그대로.
- 확인: 가용률 최저·스왑 사용(pswp 차이·Swap used)·에이전트 RSS 최대를 027 §7 에 적는다.

## 15. (032 처리방침(`https://kimptrack.com/privacy`) 게시 뒤) 로그 전송·지표 필터·5xx 경보
**032 처리방침(`https://kimptrack.com/privacy`) 게시 전에는 하지 않는다** — 그때까지 접속 로그는 serve 박스 `~/marketlens/logs/caddy/` 에만 있다. 켜면 5-2 의 serve 두 단계가 이후 늘 쓰는 절차다.
**게시 뒤에도 `logs/caddy/` 에 하루 회전 파일(`access-<날짜>…-time.log.gz`)이 생긴 것을 본 뒤에 한다** — 032 의 회전 설정은 배포의 `caddy reload` 로 들어가지 않고 serve 에서 caddy 를 다시 만든 뒤(`docker compose --profile serve --env-file .env --env-file server/.env up -d --force-recreate caddy`, 몇 초 끊김)에야 돈다(032 §4). 그 전의 `access.log` 에는 며칠치 줄이 쌓여 있고, 에이전트는 줄의 시각이 아니라 읽은 시각을 찍으므로(`serve-logs.json` 에 `timestamp_format` 없음) 그 줄이 읽은 날부터 90일 더 남을 수 있다.
```bash
aws logs put-metric-filter --log-group-name /marketlens/serve/caddy --filter-name marketlens-http-5xx \
  --filter-pattern '{ ($.status >= 500) && ($.request.uri != "/api/ws/spreads") }' \
  --metric-transformations metricName=http_5xx,metricNamespace=MarketLens,metricValue=1
aws cloudwatch put-metric-alarm --alarm-name marketlens-http-5xx --namespace MarketLens --metric-name http_5xx \
  --statistic Sum --period 300 --evaluation-periods 1 --comparison-operator GreaterThanOrEqualToThreshold --threshold 10 \
  --treat-missing-data notBreaching --alarm-actions $TOPIC --ok-actions $TOPIC
```
WS 를 빼는 이유: serve 배포 때 열린 대시보드가 전부 재접속하며 502 를 낸다. 요청이 없으면 줄도 없으므로 데이터 없음은 정상이다.
- 확인: `aws logs describe-log-groups --log-group-name-prefix /marketlens/serve/caddy --query 'logGroups[].[retentionInDays,logGroupClass]'` 가 `90 STANDARD`(IA 는 지표 필터가 안 되고 만든 뒤 못 바꾼다), 스트림 이름 = serve 인스턴스 ID, `aws logs tail /marketlens/serve/caddy --since 10m` 의 줄이 IP 끝 `.0`·헤더 없음·`s.q` 없음. 에이전트는 파일 위치를 기억해 caddy 재생성·회전 뒤에도 중복·누락 없이 이어 보낸다. 경보는 18개.
- 되돌리기: 5-2 의 `fetch-config serve.json` 만 다시(로그 설정이 빠진다) → 지표 필터·경보 삭제. 로그 그룹을 지우려면 `aws logs delete-log-group`.

Logs Insights 저장 쿼리 3개(로그 그룹 `/marketlens/serve/caddy`, `aws logs put-query-definition --name <이름> --log-group-names /marketlens/serve/caddy --query-string '<쿼리>'`):
- `marketlens/경로별 요청 수`:
  `parse request.uri /^(?<path>[^?]*)/ | stats count(*) as requests by path | sort requests desc | limit 50`
- `marketlens/외부 출처별 방문`:
  `filter referer != "" and referer != "https://kimptrack.com" and referer != "https://www.kimptrack.com" | stats count(*) as visits by referer | sort visits desc | limit 50`
- `marketlens/WS 연결 지속 시간`:
  `filter request.uri = "/api/ws/spreads" and status = 101 | stats count(*) as connections, pct(duration, 50) as p50_sec, pct(duration, 90) as p90_sec, max(duration) as max_sec by bin(1d)`

## 경보 목록 (027 §3.6)
- 상태검사 6 — 세 박스 × `StatusCheckFailed_Instance`(최댓값 60초 3점 → 재부팅)·`StatusCheckFailed_System`(최댓값 60초 2점 → 복구), 데이터 없음 무시.
- 크레딧 4 — data·serve × `CPUCreditBalance`(최솟값 300초 3점, 173·86 미만 — 7일 최솟값보다 낮게)·`CPUSurplusCreditsCharged`(합계 300초 1점 0 초과), 데이터 없음 무시.
- 메모리 3 — 세 박스 `mem_available_percent` 최솟값 10% 미만 5분 연속(60초 5점, collect 300초 1점), 데이터 없음 = 경보.
- 디스크 3 — 세 박스 `disk_used_percent` 최댓값 300초 1점 80% 초과, 데이터 없음 = 경보.
- canary 1 — `AWS/Lambda` `Errors`(`FunctionName=marketlens-smoke`) 합계 300초 2점 중 2점 1 이상, 데이터 없음 = 경보.
- 5xx 1(로그 전송 뒤) — `http_5xx` 합계 300초 1점 10 이상, 데이터 없음 = 정상.
스왑 경보는 두지 않는다 — 한 번 찬 스왑은 압박이 끝나도 잘 안 줄어 경보가 풀리지 않는다.
