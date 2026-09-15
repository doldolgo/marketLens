# 021 — infra-split

상태: TODO | 의존: 007(deploy — compose·워크플로·설정 계약 테스트), 016(process-split — `ROLE`·nginx 분기), 017(spreads-push — Redis 키·채널), 018(spreads-serve — `GET /spreads` 가 Redis 키)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
EC2 1대에 몰린 컨테이너 5개를 **역할별 EC2 3대**(수집 · 데이터 · 조회)로 나눈다. 수집기가 코어 1개를 온전히 쓰고, 무거운 이력 조회나 Influx 메모리 폭주가 수집을 굳히지 못하며, 조회 박스는 나중에 복제만 하면 늘어난다. 사용자·화면·API 계약은 하나도 안 바뀐다.

## 2. 범위
- 만드는 것: compose **profile 3개**(`collect`·`data`·`serve`), 박스별 배포 워크플로(SSH 3타깃), web 의 수집 업스트림 주소 주입, 사람용 런북 `docs/runbooks/ec2-split.md`(보안그룹·볼륨 이전·탄력 IP 이동·구 박스 정지)
- 하지 않는 것: 로드밸런서·HTTPS·도메인, 수집기 이중화, 컨테이너 레지스트리(이미지는 각 박스에서 빌드), Redis 인증, 외부 헬스체크·자동 롤백, 새 `ROLE` 값(수집 프로세스 자체는 016 의 `collector` 그대로), Influx 쿼리 기간 상한(022 후보)
- 바꾸는 기존 것: 007 의 compose(서비스 5개 그대로, profile 부여·`depends_on` 제거·박스 간 포트 공개)와 deploy 워크플로(1타깃 → 3타깃), 016 §3.2 의 "`api` 는 같은 compose 망에서 `server` 와 같은 Influx 주소" 문장, `web/nginx.conf` 의 `server:8000` 업스트림을 주입값으로, `tests/test_deploy.py` 계약(§4), 런북 `ec2-setup.md`(1대 전제 → 3대 참조)

## 3. 동작

### 3.1 박스와 배치
같은 VPC·같은 서브넷, 박스끼리는 **사설 IP** 로만 통신한다. 인스턴스 종류는 실측 근거(아래)로 정했고 바꾸려면 이 표를 고친다.

| 박스 | 인스턴스 |
|---|---|
| collect | c7g.medium |
| data | t4g.small |
| serve | t4g.micro |

- **collect** — 컨테이너 `server`(016 의 `collector` 역할) 하나. 거래소 WebSocket·마켓 우주·틱 루프·Redis 인계·flusher·원문 S3 아카이브·입출금 조회를 전부 맡는다. IAM 인스턴스 프로파일 `marketlens-s3-snapshot`(010 의 원문 업로드 주체)은 **이 박스에만** 붙이고 IMDS hop limit 2(ec2-setup.md 5-2). 컨테이너 포트 8000 을 호스트에 공개한다 — serve 의 nginx 가 사설 IP 로 붙기 위해서다. 탄력 IP 를 하나 붙인다 — 업비트 API 키가 IP 허용 목록이라 공인 IP 가 고정돼야 한다(t4g.small 실측에서 새 IP 는 401).
- **data** — `redis` + `influxdb`. 6379·8086 을 호스트에 공개한다. 스왑 1GB. Influx 는 쿼리 메모리 상한을 env 로 건다: 쿼리 1개 상한·전체 상한·동시 쿼리 수 — 값은 실행 세션이 Influx 2.7 문서에서 정하되 **전체 상한 + Redis 상주(30MB) + Influx 상주(0.5GB) 가 1.5GB 를 넘지 않게**. 근거: 2026-09-07 기간 지정 없는 `/history/streaks/bulk` 가 Influx 를 2.4GB 까지 키워 OOM 4회. 상한을 넘는 쿼리는 Influx 가 거부하고(503 으로 전파) 프로세스는 살아야 한다.
- **serve** — `api`(016 의 `api` 역할) + `web`(nginx). 기존 탄력 IP `3.34.104.16` 을 이 박스로 옮긴다. 호스트에 여는 포트는 `WEB_PORT`(80) 하나.

보안그룹은 3개, 규칙은 최소다. 셀 한 토큰, 의미는 아래 문장으로.

| 박스 | 인바운드 |
|---|---|
| collect | 8000·22 |
| data | 6379·8086·22 |
| serve | 80·22 |

8000 은 serve 보안그룹에서만, 6379·8086 은 collect·serve 보안그룹에서만, 22 는 사람 IP 에서만, 80 은 전체. 아웃바운드는 전부 열어 둔다(거래소·S3·apt·Docker Hub).

### 3.2 compose
파일은 루트 `docker-compose.yml` 하나 그대로이고 서비스 5개·컨테이너 이름·이미지·로그 상한(`json-file` 50MB×3)·`restart: unless-stopped`·볼륨 이름(`influxdb-data`·`redis-data`, 프로젝트명 `marketlens`)도 그대로다. 바뀌는 것:
- 서비스마다 profile 하나: `server` → `collect`, `redis`·`influxdb` → `data`, `api`·`web` → `serve`. **`depends_on` 은 전부 없앤다** — 의존 대상이 다른 박스에 있다. 각 박스는 `docker compose --profile <역할> --env-file .env --env-file server/.env up -d --build` 로 자기 profile 만 띄운다. profile 을 안 주면 아무것도 안 뜬다(실수로 5개가 한 박스에 뜨지 않게).
- 박스 간 주소는 **루트 `.env`(비밀 아님)** 의 두 키로 준다. `DATA_HOST` = data 박스 사설 IP, `COLLECT_HOST` = collect 박스 사설 IP. compose 는 `server`·`api` 의 `INFLUX_URL` 을 `http://${DATA_HOST:-influxdb}:8086`, `REDIS_URL` 을 `redis://${DATA_HOST:-redis}:6379/0` 으로 덮고, `web` 에 `COLLECT_HOST`(기본 `server`)를 준다. 기본값이 서비스 이름이라 **키를 안 주면 007 의 한 박스 동작 그대로** 다 — 로컬 통합 기동은 `COMPOSE_PROFILES=collect,data,serve` 로 예전처럼 5개가 한 망에 뜬다. `server/.env` 는 007 대로 비밀만 든다(`INFLUX_URL`·`REDIS_URL` 의 localhost 값은 컨테이너 안에서 계속 덮인다).
- 호스트 포트: `server` 8000, `redis` 6379, `influxdb` 8086 을 공개한다(위 보안그룹이 막는다). `web` 은 `${WEB_PORT:-80}:80` 그대로, `api` 는 비공개.
- `web` 은 nginx 설정을 템플릿으로 두고 기동 시 **`COLLECT_HOST` 하나만** 치환한다 — `location /api/` 의 업스트림이 `http://<COLLECT_HOST>:8000/` 이 된다. nginx 자체 변수(`$http_host`·`$http_upgrade` 등)는 치환 대상이 아니어야 한다. 016·018 의 분기(`/api/history/{premium,streaks,candles}`·`= /api/spreads`·`/api/ws/` → `api:8000`)는 같은 박스라 그대로다.
- Influx 첫 기동 설정(`DOCKER_INFLUXDB_INIT_*`)은 그대로 둔다 — 이전한 볼륨이 있으면 setup 이 건너뛰고, 빈 볼륨이면 새로 만든다.

### 3.3 배포
main push → 워크플로가 **data → collect → serve** 순서로 SSH 3번, 각 박스에서 007 §3 의 스크립트를 자기 profile 로 돈다. 순서의 이유: 저장소가 먼저 떠야 수집이 붙고, 수집이 떠야 nginx 가 업스트림을 푼다(둘 다 없어도 뜨긴 하지만 첫 표가 늦어진다).
- Secrets: `EC2_HOST_DATA`·`EC2_HOST_COLLECT`·`EC2_HOST_SERVE`(공인 IP 또는 탄력 IP) + 공용 `EC2_USER`·`EC2_SSH_KEY`. 기존 `EC2_HOST` 는 지운다.
- 가드(007 §3-2 의 확장, 값은 출력하지 않는다): 세 박스 모두 `server/.env` 의 `INFLUX_TOKEN`; collect 는 추가로 `S3_BUCKET` 과 루트 `.env` 의 `DATA_HOST`; serve 는 `DATA_HOST`·`COLLECT_HOST`. 하나라도 비면 그 박스에서 중단하고 워크플로 실패.
- 한 박스가 실패하면 뒤 박스는 돌지 않는다. 자동 롤백은 없다(007 그대로) — 사람이 그 박스에 들어가 직전 커밋으로 `git reset --hard` 후 같은 명령.
- collect 는 1 vCPU 라 `--build` 동안 수집이 느려진다(표 1~2분 지연·틱 판정 stale 가능). 감수한다. CI 에서 이미지를 빌드해 받아오는 것은 후속.

### 3.4 유지되는 계약 (복사)
- Redis 키·채널(017·018): 채널 `spreads`, `spreads:latest`(TTL 10초), `spreads:want`(TTL 15초), 스트림 `ticks`(009). 주소만 바뀌고 이름·의미는 그대로.
- nginx 경로(016·018): `/api/history/{premium,streaks,candles}`·`= /api/spreads`·`/api/ws/` → api, `= /api` 404, 그 외 `/api/*` → 수집(접두 제거), `index.html` no-store, `/assets/` immutable, SPA fallback.
- `/health`(001)·`/health/collect`(011)·`/history/events`(013)·`/refresh`(003)·analysis(004)는 수집 프로세스가 답한다 — 외부에서는 `/api/...` 로 serve 를 거친다.
- `ROLE` 은 `collector` | `api` 둘뿐(016). `server` 컨테이너에 `ROLE` 을 주지 않는다.

### 3.5 엣지
- data 박스 불통: collect 는 뜨고 수집을 계속한다 — Redis 없으면 인계된 틱이 버려지고 표 발행이 멈추며(009·017), Influx 없으면 flusher 가 재시도한다. serve 는 `spreads:latest` 를 못 읽어 `GET /spreads` 404, `/ws/spreads` 는 `waiting`(018). 복구되면 사람 개입 없이 이어진다.
- collect 불통: serve 의 `/api/*`(수집 경로)만 502, 스프레드 표는 `spreads:latest` TTL 10초 뒤 사라져 404/`waiting`. 이력 조회는 정상(016 의 존재 이유).
- serve 불통: 화면만 죽고 수집·저장은 계속.
- collect 의 사설 IP 가 바뀌면(인스턴스 교체) 루트 `.env` 의 `COLLECT_HOST` 를 고치고 web 을 재기동해야 한다 — nginx 는 기동 시 주소를 푼다. data 도 같다(`DATA_HOST`, server·api 재기동).
- 구 박스(t4g.medium, `i-0ccec33dba9e27017`)는 전환 확인 뒤 **정지 상태로 3일 보관**하고 종료한다. 정지 중엔 EBS 요금만 든다.

### 3.6 비용과 선택 근거
| 박스 | 월(약) |
|---|---|
| collect | $30 |
| data | $15 |
| serve | $7 |
| 공인 IPv4 3개 | $11 |

합계 약 $63/월(미국 동부 기준 온디맨드, 서울 리전 원문 미확인 — 10% 안팎 높다). 지금 t4g.medium 1대는 $30 + CPU 크레딧 초과 약 $12. 수집 박스를 c7g 로 두는 이유: 수집기는 단일 스레드라 코어 1개를 상시 쓰는데, 2026-09-15 t4g.small 실측에서 크레딧 standard 는 시작부터 기준선(인스턴스 20%) 으로 제한돼 수집이 무너졌고(steal 43~53%), unlimited 로 사면 초과분이 c7g.medium 값과 같아진다. 2026-09-15 c7g.medium 실험이 12분 만에 실패한 원인은 그 위에 Redis·Influx·api 까지 올려 2GB 를 넘긴 것이지 수집기가 아니다(수집기 단독 0.35GB).

## 4. 검증
실행 세션이 쓰는 테스트(`tests/test_deploy.py` 갱신 — Docker 없는 CI 의 유일한 회귀 장치):
- 서비스 5개·컨테이너 이름·로그 상한·`restart` 는 007 그대로이고, profile 이 `server`=collect / `redis`·`influxdb`=data / `api`·`web`=serve 로 하나씩 있다
- 어떤 서비스에도 `depends_on` 이 없다
- 호스트 공개 포트: `server` 8000, `redis` 6379, `influxdb` 8086, `web` `${WEB_PORT:-80}:80`, `api` 없음
- `server`·`api` 의 `INFLUX_URL`·`REDIS_URL` 이 `DATA_HOST` 치환식이고 기본값이 서비스 이름이다; `web` 이 `COLLECT_HOST` 를 받는다
- nginx 템플릿에서 `location /api/` 의 업스트림만 `COLLECT_HOST` 를 쓰고, api 로 가는 세 분기와 `$http_*` 변수는 그대로다
- deploy 워크플로가 3타깃을 data → collect → serve 순서로 돌고, 각 스크립트가 자기 profile 과 자기 가드 키만 검사한다; `EC2_HOST` 단독 시크릿 참조가 없다
- `COMPOSE_PROFILES=collect,data,serve` + 주소 키 없음 = 007 §4 의 한 박스 기동이 그대로 통과한다(로컬 수동)

수동(EC2, 런북 순서대로 한 뒤):
1. 각 박스 `docker ps` 에 자기 profile 의 컨테이너만 있다(collect 1, data 2, serve 2).
2. serve 에서 `curl localhost/api/spreads` 가 200 이고 행 1,400 이상, `curl localhost/api/health/collect` 가 5거래소 `ok`, 성공률 1시간 99% 이상.
3. collect `docker stats` 의 server CPU 가 코어 1개 기준 80~100% 이고 `top` 의 steal 이 0%.
4. data `free -m` 에 스왑 1GB, Influx 컨테이너 env 에 쿼리 상한 세 값이 있고 `curl localhost:8086/health` 200.
5. main 에 빈 커밋 없이 실제 PR 하나로 워크플로 3타깃 success 1회.
6. 브라우저에서 `http://3.34.104.16/` 스프레드 표가 매초 갱신되고 기록 탭 차트가 뜬다(탄력 IP 가 serve 에 붙음).
7. 구 박스를 정지한 뒤에도 2·6 이 유지된다. 정지 3일 뒤 종료.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
(실행 후 기록)
```

## 6. 갱신할 문서
- `docs/context/status.md` — deploy 행 server 열을 "compose profile 3개(collect=server / data=redis·influxdb / serve=api·web), EC2 3대, 배포 워크플로 3타깃(data→collect→serve), 박스 간 주소는 루트 .env 의 DATA_HOST·COLLECT_HOST" 로. 알려진 빚에 "Redis 인증 없음(보안그룹만) / 이미지 박스별 빌드(collect 1 vCPU 라 배포 중 수집 지연) / Influx 쿼리 기간 상한 미적용(022 후보)" 추가.
- `CLAUDE.md` — 스펙 인덱스 021 행 상태 → DONE.
- `docs/context/architecture.md` — "배포 토폴로지" 절을 EC2 3대(역할·인스턴스·포트·보안그룹 한 줄씩)로 다시 쓰고 "메모리 저장소 공유·EC2 분리는 후속" 문장을 지운다. "런타임 구성" 의 저장소 문단에 "Redis·Influx 는 data 박스, 사설 IP" 한 줄. mermaid 데이터 흐름에 박스 경계가 없으면 그대로 둔다.
- `docs/context/dev-setup.md` — "docker 통합 기동" 절의 명령을 `COMPOSE_PROFILES=collect,data,serve WEB_PORT=8080 docker compose --env-file server/.env up -d --build` 로, env 절에 루트 `.env` 의 `DATA_HOST`·`COLLECT_HOST`(배포 전용, 로컬은 비움) 두 줄.
- `docs/specs/007-deploy.md` — §3 "서버는 EC2 1대" 와 deploy 워크플로 단락을 "EC2 3대·profile·3타깃(021)" 로, §3 "호스트에 여는 포트는 web 하나" 를 "serve 는 web 하나, collect 8000·data 6379/8086 은 보안그룹으로(021)" 로, §4 의 "호스트에 8000·8086 이 새로 열리지 않는다" 항목을 profile 별 표현으로.
- `docs/specs/016-process-split.md` — §3.2 의 "`api` 는 같은 compose 망…`INFLUX_URL` 을 같은 값으로 덮는다" 를 "둘 다 `DATA_HOST` 치환식(021)" 로. §3.5 "`api` 의 헬스는 compose 안에서만" 은 serve 박스 안에서만으로.
- `docs/runbooks/ec2-setup.md` — 머리에 "3대 구성은 `ec2-split.md`, 이 문서는 박스 공통 준비(도커·env·IAM·Secrets)" 한 줄, 2번(인바운드) 을 박스별 표 참조로, 6번 Secrets 를 5개로.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
