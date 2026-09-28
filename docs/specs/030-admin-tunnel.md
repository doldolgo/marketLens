# 030 — admin-tunnel

상태: TODO | 의존: **029 가 main 에 머지·배포된 뒤**. 계약을 쓰는 스펙: 007 deploy(compose·배포 워크플로), 021 infra-split(serve 박스·보안그룹), 023 domain-tls(DNS·caddy), 027 observability(serve 배포 순서의 caddy reload·`caddy/Caddyfile`), 029 admin(관리자 server `web:8081`)

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
(실행 후 기록)
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
- 만든 것 (파일 목록):
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
- 남은 빚:
