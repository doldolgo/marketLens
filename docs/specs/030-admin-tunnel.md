# 030 — admin-tunnel

상태: DONE | 의존: **029 가 main 에 머지·배포된 뒤**. 계약을 쓰는 스펙: 007 deploy(compose·배포 워크플로), 021 infra-split(serve 박스·보안그룹), 023 domain-tls(DNS·caddy), 027 observability(serve 배포 순서의 caddy reload·`caddy/Caddyfile`), 029 admin(관리자 server `web:8081`)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
029 의 관리자 페이지를 `https://admin.kimptrack.com` 으로 연다. 로그인은 Cloudflare Access(허용한 이메일로 받는 일회용 코드)가 하고, 서버로 들어오는 길은 serve 박스가 밖으로 연 Cloudflare Tunnel 하나뿐이다 — 인바운드 포트를 열지 않고, 앱에 로그인 코드를 넣지 않는다(product.md 비범위 "사용자 계정·인증" 유지). 비용 0원(Zero Trust Free — 50명).

## 2. 범위
- 만드는 것: compose 서비스 `cloudflared`(profile `tunnel`, 전용 망), 배포 serve 스크립트의 터널 줄, 토큰 파일 규칙(`secrets/`), 런북 `docs/runbooks/admin-access.md`, 계약 테스트.
- 하지 않는 것: Google 등 다른 로그인 방식. Cloudflare 설정의 코드 관리(Terraform 등) — 대시보드에만 있고 런북에 기록한다. 루트·www 의 Cloudflare 프록시(023 그대로 회색). cloudflared 출구 제한(방화벽) — 위험만 빚으로 적는다.
- 바꾸는 기존 것: 007(컨테이너 일곱 — 배포 정의 기준, 로컬 통합 기동은 여섯), 021(serve 에 cloudflared — 밖으로만 나가는 연결), 023(`admin.kimptrack.com` 만 터널용 프록시 CNAME), 027 §4(serve 배포 `--profile` 줄 수·컨테이너 수 단언).
- 담당: 021·023 은 hereokay 담당이다 — 이 PR 은 그 스펙들을 **고치지 않는다**(CLAUDE.md §5). §6 의 "담당자에게 제안" 목록을 PR 본문에 적고 담당자가 반영한다. 007·027 은 이 레포 주인 담당이라 고친다. §5 허용 목록 밖: `docker-compose.yml`·`.gitignore`·`.github/workflows/deploy.yml`·`README.md`(사람 승인).

## 3. 동작

### 3.1 들어오는 길
관리자 브라우저 → `admin.kimptrack.com`(Cloudflare 엣지 — 인증서는 Universal SSL) → Access 로그인 → Tunnel → serve 박스 `cloudflared` → `http://web:8081`(029 관리자 server). cloudflared 는 전용 compose 망 `admin` 에만 있고, web 이 기본 망과 `admin` 두 곳에 붙는다 — cloudflared 는 compose 이름으로 api·caddy 에 닿지 못한다. 보안그룹상 사설 주소(collect:8000·data:6379·8086)로는 여전히 나갈 수 있다(§3.5).

### 3.2 cloudflared 서비스
- 컨테이너 이름 `marketlens-cloudflared`, `restart: unless-stopped`, profile **`tunnel`**(박스 profile 이 아니다 — serve 배포가 따로 띄운다), 망 `admin` 만, 게시 포트 없음, 메모리 상한 128MB, 로그 상한은 다른 서비스와 같다.
- 이미지 `cloudflare/cloudflared` 를 태그와 **멀티 아키텍처 인덱스 digest** 로 고정(serve 는 arm64 — 한 플랫폼 digest 를 박으면 exec format error). 2025.4.0 이상(토큰 파일 지원), 실행 시점 최신 안정 태그를 §7 에 기록, `latest` 금지, 분기마다 올린다.
- 이미지 ENTRYPOINT 가 `cloudflared --no-autoupdate` 이므로 명령은 `tunnel --loglevel info --metrics 127.0.0.1:2000 run` 으로 시작한다. `--loglevel` 은 info 고정 — debug 는 요청 헤더 전부(쿠키·Access JWT·`X-Refresh-Token`)를 로그로 낸다.
- 헬스체크는 **exec 형식**(`CMD`) `cloudflared tunnel --metrics 127.0.0.1:2000 ready` — 이미지가 distroless 라 셸 형식은 늘 unhealthy 다.
- 토큰: compose 최상위 `secrets`(파일 `./secrets/cloudflared-token`)로 이 서비스에만 넘기고 env `TUNNEL_TOKEN_FILE` 이 그 경로를 가리킨다. `TUNNEL_TOKEN` 환경변수·명령 인자·`.env`·`server/.env` 에 두지 않는다(환경변수는 `docker inspect`·`compose config` 에 찍히고 `server/.env` 는 api·수집기에 들어간다). 파일은 serve 박스에만, 소유 `65532:65532`·권한 `0400`(컨테이너가 UID 65532 로 돌고 compose 파일 secret 은 바인드라 uid·mode 지정이 무시된다). `secrets/` 는 git 무시.
- 로컬 통합 기동(`COMPOSE_PROFILES=collect,data,serve`)은 cloudflared 를 띄우지 않는다.

### 3.3 배포 serve 스크립트
순서: 가드 → 미러 → `up --profile serve` → caddy 다시 읽기(027) → **`secrets/cloudflared-token` 이 비어 있지 않을 때만** `docker compose --profile tunnel … up -d`, 없으면 "tunnel 건너뜀" 한 줄 → prune(마지막). tunnel `up` 이 실패하면 배포 실패로 끝난다(공개 서비스는 이미 떠 있다). 공개 `up` 과 따로 돌리는 이유: 같은 `up` 에서 secret 오류가 나면 함께 다시 만들던 web·caddy·api 가 기동 안 된 채 남을 수 있다. `--profile tunnel up` 은 serve 컨테이너를 건드리지 않는다.

### 3.4 Cloudflare 쪽 (사람 — 런북)
- 조직: Zero Trust Free(50명, 결제수단 등록 단계는 있고 청구 없음). 팀 이름은 정한 뒤 바꾸지 않는다(바꾸면 원점 검증이 전부 실패 — 아래). 대시보드 구성원은 최소(1~2명)·2FA.
- 로그인: 이메일 일회용 코드(OTP) 하나 + instant auth. 그룹 `marketlens-admins`(Include → Emails) — 이메일 목록은 레포에 적지 않고 런북에는 인원수만.
- Access 앱: Self-hosted, `admin.kimptrack.com` **전체**(경로 제한 없음), 정책 Allow + 그룹 하나(Bypass·Everyone·Service Auth 없음), 전역·앱 세션 12시간, 쿠키 SameSite=Lax·HttpOnly·Binding cookie, OPTIONS 우회 끔. **라우트보다 먼저 만든다** — 앱이 없는 동안 그 호스트는 누구에게나 열린다.
- 터널: 원격 관리(토큰). 라우트는 `admin.kimptrack.com → http://web:8081` 하나, **Protect with Access**(required·팀 이름·Access 앱 AUD). cloudflared 가 요청마다 Access JWT 를 확인해 없거나 AUD 가 다르면 403, 서명·만료 실패는 엣지 5xx — 원점으로 넘기지 않는다. 이를 위해 serve 에서 `<팀>.cloudflareaccess.com:443` 으로 나가는 연결이 필요하다. catch-all 은 `http_status:404`. private network(CIDR)·WARP 라우트는 만들지 않는다. 계정 전체 "Require Access protection" 은 Free 에서 되면 켠다.
- Universal SSL 이 Active 인지, 루트·www 가 여전히 DNS only(회색)인지 확인. Tunnel 상태 알림(메일)을 켠다.
- 관리자 접속 기록(029) 회전: 호스트 logrotate — 주 1회·13개(≈90일)·copytruncate.

### 3.5 엣지
- 터널이 끊김·cloudflared 가 죽음: 관리자 페이지만 Cloudflare 오류 화면(공개 사이트 무관), 상태 알림 메일.
- Access 앱을 다시 만들어 AUD 가 바뀜: 모든 요청이 403 — 런북 "AUD 갱신" 순서.
- Cloudflare 계정 탈취 = VPC 입구: 대시보드 권한자는 라우트 하나로 serve 가 닿는 사설 주소(인증 없는 Redis 포함)를 공개할 수 있다 — 구성원 최소·2FA·private 라우트 없음·드리프트 확인(대시보드·API 로 전체 ingress 조회)이 벽이다(status.md 빚).
- 터널 토큰 유출: 대시보드 토큰 갱신 → 기존 연결 끊기(API) → 새 파일(권한) → cloudflared 재생성 → 커넥터 목록에 serve 하나만인지 → Access 앱 Revoke existing tokens → REFRESH_TOKEN 교체 → Admin logs 에서 라우트 변경 확인. 그동안 관리자 페이지만 끊긴다.
- 팀원 이탈: 그룹에서 제거 → 사용자 Revoke(기존 세션 끊기) → Remove users(좌석 반납) → 계정 구성원이었으면 거기서도 제거 → 그 사람이 알던 토큰(REFRESH_TOKEN, 터널 권한이 있었으면 터널 토큰) 교체.
- 응답 125초 제한: Cloudflare 프록시는 125초에 524(무료, 조정 불가)지만 관리자 server 의 nginx 읽기 제한 60초(029)가 먼저 걸린다. 터널 경로에 125초가 그대로인지는 배포 뒤 확인.

## 4. 검증
**PR 안 — 실행 세션(완료 조건)**
- compose: cloudflared 가 §3.2 대로 — 이름·restart·profile `tunnel`·망 `admin` 만·포트 없음·메모리·로그 상한·이미지 태그+digest(`latest` 아님)·명령이 `tunnel` 로 시작·`--loglevel info`·헬스체크 `test[0] == "CMD"`·`secrets` 와 `TUNNEL_TOKEN_FILE`(`TUNNEL_TOKEN` 없음) / web 이 기본 망과 `admin` 둘 / 컨테이너 수 일곱 / profile 단언은 `tunnel` 을 serve 박스 부속으로 허용 / `.gitignore` 에 `secrets/`.
- 배포 워크플로 serve: §3.3 순서, 토큰 파일 확인은 비어 있지 않음(`-s`), `--profile` 줄은 serve·tunnel 두 줄(다른 박스는 한 줄 그대로), prune 마지막.
- README 단언: 로컬 여섯·배포 정의 일곱.
- 로컬 Docker(compose): secret 파일 없이 `--profile serve up` 성공 / 가짜 토큰 파일로 `--profile tunnel up` 이 serve 컨테이너의 시작 시각을 바꾸지 않는다 / cloudflared 컨테이너에서 `api:8000` 이름이 안 풀린다.
- 기존 스펙 재검증: `cd server && ruff check . && ruff format --check . && pytest -q`, `cd web && npm run lint && npm run build`.

**배포·런북 뒤 — 사람(완료 조건 아님, status.md 비고 "운영 확인 대기")**
- `docker compose ps` 에서 cloudflared healthy, 대시보드 커넥터 Healthy·serve 하나 / `curl -sI https://admin.kimptrack.com/` 이 Access 로그인으로 302 / OTP 로그인 뒤 화면·API 문서·Try it out·즉시 갱신 동작 / 허용 안 된 이메일은 코드를 못 받는다 / `Set-Cookie` 속성(Lax·HttpOnly) / 원점 검증: Access 앱 AUD 를 잠깐 틀리게 넣으면 403 → 되돌림 / `https://kimptrack.com` 페이지에서 `fetch('https://admin.kimptrack.com/api/health',{credentials:'include',mode:'no-cors'})` 가 관리자 기록에 `sfs: same-site`·403 으로 남는다(헤더가 도착하는지) / 위조 `Cf-Access-Authenticated-User-Email` 을 붙인 요청의 기록에 실제 이메일 / 탄력 IP + `Host: admin.kimptrack.com` 은 공개 사이트 / 루트·www 회색 그대로·caddy 인증서 / cloudflared 메모리(`docker stats`) / 상태 알림 메일 시험.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
# 2026-09-29 로컬(Mac, OrbStack Docker 29.4.0, compose v5.1.2). 브랜치 feat/030-admin-tunnel(029 구현 위)
# 이미지 — 태그와 인덱스 digest
docker buildx imagetools inspect cloudflare/cloudflared:2026.9.3
#   MediaType manifest.list.v2 · Digest sha256:072c067d25ccbe61d46e18f0d0723255f2bb5304f7317caa95b27031520ff92c
#   Platform linux/amd64 (sha256:2fa795d0…0091) · linux/arm64 (sha256:b1b4c98a…c7b0)
# Docker Hub 태그 목록(last_updated 순): 2026.9.3 = latest = 같은 인덱스 digest (2026-09-24 게시)
docker run --rm --network none <이미지> tunnel run --help   # --token-file [$TUNNEL_TOKEN_FILE] 있음 · tunnel ready 있음

# 기존 스펙 재검증 (마지막 코드 커밋 뒤)
cd server && ruff check . && ruff format --check . && pytest -q   # All checks passed! · 237 files already formatted · 902 passed (시작 전 893 — test_admin_tunnel.py 9 추가)
cd web && npm run lint && npm run build                            # oxlint 종료 코드 0 · ✓ built

# 로컬 Docker — 덮어쓰기 파일(스크래치 compose.030.yml): 프로젝트 marketlens030, 컨테이너 이름 marketlens030-*,
#   env_file 은 .env.example 사본, caddy 는 127.0.0.1:18030 만 게시·Caddyfile 사본(local_certs 만 더함), 접속 로그 바인드는 스크래치.
#   secret 경로(./secrets/cloudflared-token)는 원본 그대로. 셸 COLLECT_HOST=127.0.0.1 DATA_HOST=127.0.0.1(collect·data 박스 없음)
docker compose -p marketlens030 -f docker-compose.yml -f compose.030.yml --profile serve up -d --build   # secrets/ 없음 → exit 0, api·web·caddy Up
#   망 marketlens030_default·marketlens030_admin 생성, web 만 두 망, api·caddy 는 default 만
docker exec marketlens030-caddy caddy reload --config /etc/caddy/Caddyfile   # exit 0 · curl 127.0.0.1:18030/ → 200
# 가짜 토큰(무작위 62바이트) → 레포 secrets/cloudflared-token (0444 — Mac 에서 65532 로 chown 불가). git status --short 빈 출력(git 무시)
docker compose -p marketlens030 -f docker-compose.yml -f compose.030.yml --profile tunnel up -d   # cloudflared Created·Started 만
#   api·web·caddy 의 컨테이너 ID·StartedAt·RestartCount 가 전후 같다 (diff 빈 출력)
#   cloudflared: user 65532:65532 · Memory 134217728 · 망 marketlens030_admin 만 · PortBindings {} · env 에 TUNNEL_TOKEN_FILE 만(TUNNEL_TOKEN 없음)
#   · 바인드 /run/secrets/cloudflared-token 읽기 전용 · 헬스체크 ["CMD","cloudflared","tunnel","--metrics","127.0.0.1:2000","ready"]
#   · 로그 "Provided Tunnel token is not valid." 만 되풀이(exit 255 → 재시작) — 토큰을 읽고 곧바로 끝나 엣지 연결 시도 없음
docker compose … --profile tunnel run --rm --no-deps -T cloudflared access curl http://api:8000/health
#   lookup api on 127.0.0.11:53: no such host   (http://caddy:80/ 도 no such host, http://web:8081/ 은 닿음 — "failed to find Access application")
docker run --rm --network marketlens030_admin busybox:1.36 nslookup api·caddy·web   # api·caddy NXDOMAIN, web 주소 · wget web:8081/ → <title>KimpTrack 관리자</title>
COMPOSE_PROFILES=collect,data,serve docker compose … config --services   # influxdb redis server web api caddy (cloudflared 없음) · --profile tunnel → cloudflared 만
# 두 번째 배포 흉내: serve up -d --build → api·web Recreate(같은 이미지여도 --build 면 compose v5 가 다시 만든다 — 030 과 무관, --build 없는 up 은 전부 Running)
#   → caddy reload → tunnel up: cloudflared 컨테이너 ID 그대로
docker compose … --profile serve --profile tunnel down -v --rmi local && rm -rf secrets && docker rmi <cloudflared 인덱스>
docker ps -a · docker network ls · docker images · docker volume ls   # 시작 전 목록과 diff 0 (030 이름 0건)
# compose 는 file secret 을 읽지 않는다: 모드 000 인 secret 파일로 busybox 서비스 up → Created·Started (운영의 65532·0400 파일을 배포 사용자가 못 읽어도 된다)
# 배포 serve 꼬리(caddy reload 뒤 ~ prune)를 가짜 docker 함수로 bash 실행: 파일 없음·빈 파일 → "tunnel 건너뜀"·prune / 있음 → tunnel up·prune / tunnel up 실패 → exit 1(prune 안 감)
```

## 6. 갱신할 문서
**이 PR 이 고치는 문서**
- `docs/context/status.md` — admin 행(029)의 비고를 "admin.kimptrack.com — Cloudflare Access(OTP)·Tunnel(cloudflared, profile tunnel, Protect with Access) · 운영 확인 대기" 로. deploy 행에 "serve 에 cloudflared(토큰 파일이 있을 때만, 망 admin), 컨테이너 일곱(로컬 여섯)". 알려진 빚: 028 의 "(028) 닫힌 API … 029 전까지 박스 안에서만" 줄을 지우고, `(030) Cloudflare 설정(앱·정책·라우트·AUD)은 대시보드에만 — 런북 기록·드리프트 확인으로 맞춘다`, `(030) cloudflared 의 출구는 serve 보안그룹 자격의 사설망 전체 — Cloudflare 계정 탈취 = Redis·Influx·수집기 접근, 출구 제한은 후속`, `(030) Access 인증 로그는 Free 에서 24시간 — 요청 기록은 029 관리자 접속 기록뿐` 추가. 알려진 빚에 `(030) 021·023 의 해당 문장이 030 동작과 다르다 — PR 에 담당자 제안으로 남김, 반영 대기` 추가.
- `CLAUDE.md` — 스펙 인덱스 030 행 상태 → DONE. §2 레포 구조에 `secrets/`(git 무시, serve 박스에만), runbooks 목록에 `admin-access.md`.
- `docs/context/architecture.md` — '배포 토폴로지' serve 줄에 "cloudflared(profile tunnel, 망 admin) → web 관리자 server :8081 — 밖으로만 나가는 연결".
- `docs/context/dev-setup.md` — 'docker 통합 기동' 절에 "cloudflared 는 로컬에서 띄우지 않는다(profile tunnel)" 한 문장.
- `README.md` — 30행 EC2 설명에 "cloudflared(serve, 토큰 파일이 있을 때만 — 관리자 페이지 터널)" 와 컨테이너 일곱, 24행 로컬 여섯은 그대로.
- `docs/runbooks/admin-access.md` — 신규: 대시보드에만 있는 상태의 기록(팀 이름·호스트명·Service URL·앱·그룹 이름·로그인 방식·세션·쿠키·Protect with Access·AUD 위치·인원수), 준비 순서(조직 → 구성원 2FA → OTP → 그룹 → 앱 → 터널 → 토큰 파일·권한 → 배포 → 라우트 → 알림 → Universal SSL·회색 확인), 드리프트 확인(전체 ingress·catch-all·private 라우트), 토큰 교체·AUD 갱신·팀원 이탈(§3.5 순서), logrotate, 되돌리기(라우트 삭제 또는 `docker compose stop cloudflared` — 공개 무관), 이미지 갱신 주기.
- `docs/specs/007-deploy.md` — §3 컨테이너 목록에 cloudflared(profile tunnel, 망 admin, 토큰 파일 secret), 배포 절에 serve 의 터널 줄.
- `docs/specs/027-observability.md` — §4 배포 워크플로 serve 줄의 "`--profile` 은 한 줄 그대로" → "`--profile` 은 serve·tunnel 두 줄(030)", compose 줄의 "컨테이너 6개" → "일곱(030)".

**담당자에게 제안 — 이 PR 에서 고치지 않는다(PR 본문에 그대로 적는다)**
- 021 — serve 박스 설명에 "cloudflared — 밖으로만 나가는 연결(030)".
- 023 — §2 하지 않는 것의 "Cloudflare 프록시(주황 구름)" 뒤에 "(루트·www — `admin.kimptrack.com` 만 터널용 프록시 CNAME, 030)".

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록): `docker-compose.yml`(서비스 `cloudflared` — 태그 `2026.9.3` + 인덱스 digest, profile `tunnel`, 망 `admin` 만, `mem_limit 128m`, 명령·exec 헬스체크, `TUNNEL_TOKEN_FILE` + 최상위 `secrets` / web 망 `default`·`admin` / 최상위 `networks.admin` / 머리 주석), `.gitignore`(`secrets/`), `.github/workflows/deploy.yml`(serve 의 caddy reload 뒤 `[ -s secrets/cloudflared-token ]` 일 때만 `--profile tunnel … up -d`, 아니면 "tunnel 건너뜀" 한 줄, prune 마지막), `README.md`(30행 일곱 컨테이너·cloudflared), 테스트 `server/tests/test_admin_tunnel.py`(신규 9 — compose 7·배포 2)·`test_deploy.py`(서비스 일곱·`PROFILE_OF` 에 tunnel·`--profile` 은 serve 만 serve·tunnel 두 줄·README 로컬 여섯/배포 일곱)·`test_observability.py`(caddy reload 는 up 뒤·prune 앞 — 끝에서 둘째 자리 단언을 풂), 런북 `docs/runbooks/admin-access.md`(신규). 문서: CLAUDE.md(§2 `secrets/`·런북 목록·인덱스 DONE), architecture(계약 규칙 관리자 경로·배포 토폴로지·현재 구조), dev-setup, status, 007 §2·§3, 027 §4. 앱 코드·라이브러리 변경 없음.
- 의존 판단: 의존 줄 "029 가 main 에 머지·배포된 뒤" 는 운영에서 터널을 켜는 조건으로 읽었다 — 구현은 029 브랜치(feat/029-admin) 위에 쌓아 지금 했다. 머지 순서는 027 → 029 → 030 으로 고정되고, 터널은 serve 박스에 토큰 파일이 생겨야 뜨는데 그 파일은 사람이 런북(Cloudflare 설정) 뒤에 둔다. 의존 줄은 그대로 뒀다.
- 이미지: `cloudflare/cloudflared:2026.9.3`, 인덱스 digest `sha256:072c067d25ccbe61d46e18f0d0723255f2bb5304f7317caa95b27031520ff92c`(linux/amd64·linux/arm64) — 2026-09-29 `docker buildx imagetools inspect` 로 확인, 설계 세션 값과 같다. Docker Hub 에서 이 태그가 `latest` 와 같은 digest(2026-09-24 게시)라 실행 시점 최신 안정 태그다. 다음 갱신 2026-12(런북).
- 추측한 지점 (묻지 않고 정한 사소한 것):
  - 메모리 상한은 `mem_limit: 128m`(compose config 가 134217728 로 풂) — `deploy.resources` 대신 한 줄. 헬스체크 주기는 30초·제한 5초·재시도 3·시작 유예 30초(스펙은 exec 형식만 정함 — 평범한 docker 는 unhealthy 로 재시작하지 않으니 표시용이다).
  - tunnel `up` 줄은 박스 `up` 과 같은 `--env-file .env --env-file server/.env`(compose 는 파일 전체를 치환한다 — 빼면 `INFLUX_TOKEN` 미설정 경고), `--build`·서비스 인자 없음. 건너뜀 문구 "tunnel 건너뜀 — secrets/cloudflared-token 이 없거나 비었다".
  - 망 `admin` 은 기본 bridge(`internal` 아님 — 엣지로 나가야 한다). 테스트가 internal 이 아님을 단언한다.
  - 런북: 터널 이름 `marketlens-serve`·Access 앱 이름 `marketlens-admin` 은 스펙에 없어 정했다(그룹 이름은 스펙 값). logrotate 에 `missingok`·`notifempty`·`su root root` 를 더했다(스펙은 주 1회·13개·copytruncate). 토큰 파일은 `umask 077` + `read -rs` 로 만든다(셸 기록·화면에 안 남게), `secrets/` 는 700.
  - compose 머리 주석의 옛 개수("5개")를 여섯으로 고치고 cloudflared 줄을 더했다.
  - 로컬 검증: 사용자 컨테이너·볼륨과 안 겹치게 스크래치 덮어쓰기(프로젝트·컨테이너 이름에 030, `.env.example` 사본, caddy 는 `127.0.0.1:18030` 만·Caddyfile 사본에 `local_certs` — 원본이면 운영 도메인 인증서를 Let's Encrypt 에 청한다). secret 경로는 원본 그대로라 레포에 `secrets/cloudflared-token` 을 만들었다가 지웠다(0444 — Mac 에서 65532 로 chown 불가). 가짜 토큰은 무작위 문자열이라 cloudflared 가 형식 검사에서 곧바로 끝난다 — **Cloudflare 엣지 연결 시도 없음**. 그 대신 컨테이너가 재시작을 되풀이해 그 안에서 이름을 풀 수 없어서, "cloudflared 컨테이너에서 api 가 안 풀린다" 는 같은 서비스 정의로 만든 컨테이너(`compose run cloudflared access curl http://api:8000/health` → no such host)와 `admin` 망의 busybox `nslookup`, cloudflared 의 망 목록(`admin` 하나)으로 봤다.
  - 관측: compose v5 의 `up -d --build` 는 이미지가 같아도 빌드한 서비스(api·web)를 매번 다시 만든다(배포가 원래 그렇다 — 030 과 무관). tunnel `up` 은 cloudflared 만 만들고 두 번째엔 그대로 둔다. compose 는 file secret 을 읽지 않는다(모드 000 파일로 확인) — 운영 파일(65532·0400)을 배포 사용자가 못 읽어도 되고, 스크립트도 `-s`(크기)만 본다.
- 실행 중 함께 고친 스펙 절: 007 §2(배포용 7컨테이너)·§3(컨테이너 7개 — `cloudflared` 줄, 로그 일곱, 배포 4-2 serve 터널, 계약 테스트 괄호), 027 §4(컨테이너 일곱·`--profile` serve·tunnel 두 줄). architecture 는 §6 의 serve 줄에 더해 "서비스 6개"·"여섯 컨테이너 모두" 를 일곱으로, 계약 규칙의 관리자 경로 문장에 들어오는 길, 현재 구조에 admin-tunnel 줄 — 안 고치면 문서끼리 어긋난다.
- PR 본문에 옮길 것 (담당자 제안 — 이 PR 에서 고치지 않는다):
  - 021 — serve 박스 설명에 "cloudflared — 밖으로만 나가는 연결(030)".
  - 023 — §2 하지 않는 것의 "Cloudflare 프록시(주황 구름)" 뒤에 "(루트·www — `admin.kimptrack.com` 만 터널용 프록시 CNAME, 030)".
  - `docs/specs/021-infra-split.md:§3.2` — "서비스마다 profile 하나: `server` → `collect`, `redis`·`influxdb` → `data`, `api`·`web` → `serve`" → 실제는 caddy 도 `serve`(023)이고 cloudflared 는 박스 profile 이 아닌 `tunnel`(serve 부속, 배포가 토큰 파일이 있을 때만 — 030).
  - `docs/specs/023-domain-tls.md:§4` — "`test_deploy.py` — 컨테이너 여섯" → 일곱(030).
  - `docs/specs/023-domain-tls.md:§2` — "배포 워크플로 변경 없음(`--profile serve` 가 caddy 도 띄운다)" → serve 배포는 up 뒤 caddy reload(027)와 토큰 파일이 있을 때 `--profile tunnel up`(030).
- 남은 빚:
  - §4 "배포·런북 뒤 — 사람" 항목 전부(status.md "운영 확인 대기") — 진짜 토큰이 없어 헬스체크 `ready`·엣지 연결·Protect with Access 는 로컬에서 못 봤다. 첫 확인은 런북 8·9단계.
  - cloudflared 의 원격 관리(대시보드 실시간 로그·진단 — 이 버전의 `--management-diagnostics` 기본 켬)는 기본값 그대로다. 대시보드 권한자만 쓰지만 끄려면 후속.
  - status.md 에 적은 030 빚 넷(대시보드에만 있는 설정·출구 제한 없음·Access 로그 24시간·021·023 제안 반영 대기).
