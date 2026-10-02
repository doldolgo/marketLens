# 관리자 페이지 접속 — Cloudflare Access + Tunnel (사람이 한다) — 스펙 030

`https://admin.kimptrack.com` → Cloudflare 엣지 → Access 로그인(이메일 일회용 코드) → Tunnel → serve 박스 `marketlens-cloudflared` → `http://web:8081`(029 관리자 server). serve 는 밖으로만 연결하고 인바운드 포트를 열지 않는다.
레포에 있는 것은 compose 서비스(`cloudflared`, profile `tunnel`)와 배포 줄뿐이다. **Cloudflare 설정(앱·정책·그룹·라우트·AUD)은 대시보드에만 있다** — 이 문서가 그 기록이고, 바꾸면 여기도 고친다.
레포는 공개다. **이메일 주소·팀 이름·AUD·토큰 값은 여기에 적지 않는다** — 자리표시자(`<…>`)와 "어디서 보는지"만 적는다. 단계마다 **확인** 이 끝나야 다음으로 간다.
관리자 페이지의 Clarity 요약 토큰(`CLARITY_API_TOKEN`)은 이 문서가 아니라 `clarity.md` 의 Data Export 토큰 절(035)이다.

## 기록 — 대시보드에만 있는 상태
대시보드 메뉴 이름은 바뀔 수 있다. 아래 "보는 곳" 은 2026-09 기준이다.
- 팀 이름: `<팀>` — 레포에 안 적는다. 보는 곳: Zero Trust → Settings → Team name and domain(`<팀>.cloudflareaccess.com`). **정한 뒤 바꾸지 않는다** — 바꾸면 라우트의 Protect with Access(팀 이름 검증)가 전부 실패한다.
- 호스트명: `admin.kimptrack.com` — DNS `admin` CNAME(프록시, 주황)은 라우트가 만든다. 루트·www 는 DNS only(회색) 그대로(023).
- 터널: 이름 `marketlens-serve`, 원격 관리(토큰), 커넥터는 serve 박스 하나.
- 라우트: `admin.kimptrack.com` → Service `http://web:8081` 하나 + catch-all `http_status:404`. private network(CIDR)·WARP 라우트 없음.
- Protect with Access: 켬 — required · 팀 이름 · Access 앱 AUD. 2026-10 화면에서는 라우트의 "Access JSON 웹 토큰(JWT) 유효성 검사 적용" 스위치를 켜고 Access 앱(`admin`)을 고르면 팀 이름·AUD 가 따라 들어간다. 적용 확인: `docker logs marketlens-cloudflared 2>&1 | grep 'Updated to new configuration'` 의 ingress 에 `"access":{…"required":true,"teamName":…}` (AUD 는 출력에서 가린다).
- Access 앱: 이름 `admin`(대시보드가 대상 호스트 이름에서 채운 기본값), 자체 호스팅 · 공개 DNS, 대상 `admin.kimptrack.com` **전체**(경로 없음).
- 정책: 재사용 정책 `marketlens-admins` 하나 — Allow, Include → **Emails**(관리자 이메일을 정책에 직접), 정책 세션 12시간. Bypass·Everyone·Service Auth 정책 없음. 이메일 목록은 대시보드 정책 화면에서만 본다(인원 `<N>`명).
- 그룹: 쓰지 않는다 — 2026-10 한국어 화면에서 Access 그룹 메뉴가 안 보여 정책에 이메일을 직접 넣었다. 사람을 넣고 뺄 때는 이 정책의 Emails 를 고친다.
- 로그인 방식: 조직의 ID 공급자 목록에는 기본 항목과 One-time PIN 이 있고, 앱은 One-time PIN 하나만 고르고 Instant Auth(선택 화면 없이 코드 입력으로).
- 세션: 전역(Settings → Authentication → Global session timeout)·앱 모두 12시간.
- 쿠키(앱 → Settings → Cookie settings): SameSite=Lax · HTTP Only 켬 · Binding Cookie 켬. CORS 의 "Bypass OPTIONS requests to origin" 끔.
- AUD 위치: Access 앱 → 앱 편집 → Overview(Basic information)의 Application Audience (AUD) Tag. 레포에 안 적는다.
- 대시보드 구성원: `<N>`명(1~2명), 전원 2FA.
- 알림: Tunnel 상태 알림 → 메일.
- 기록한 날: 2026-10-01

## 준비 순서
2026-10 한국어 대시보드의 메뉴 이름(번역이 어색하다): Access → **접근통제**(Applications 가 "에", Policies 가 "규약"), Tunnels → **네트워크 → 커넥터**, Login methods → **통합 → ID 공급자**, 라우트 → 터널의 **게시된 애플리케이션 경로**. 언어를 English 로 바꾸면 이 문서의 영문 이름과 맞는다.
### 1. 조직
Zero Trust 가입 — 플랜 Free(50명). 결제수단 등록 단계가 있지만 청구는 없다. 팀 이름을 정한다(위 — 바꾸지 않는다).
- 확인: Settings 에 팀 도메인 `<팀>.cloudflareaccess.com`.

### 2. 구성원 2FA
계정 Members 는 최소(1~2명). 각자 My Profile → Authentication 에서 2FA 를 켠다. 대시보드 권한자는 라우트 하나로 serve 가 닿는 사설 주소(인증 없는 Redis 포함)를 공개할 수 있다(030 §3.5) — 이 두 줄이 벽이다.
- 확인: Members 목록 전원 2FA 표시.

### 3. 로그인 방식
Settings → Authentication → Login methods → One-time PIN 추가. 다른 방식은 두지 않는다. 전역 세션 12시간.

### 4. 그룹
Access → Access Groups(또는 Reusable components → Groups) → `marketlens-admins`, Include → Emails 에 관리자 이메일.

### 5. Access 앱 — 라우트(9)보다 먼저
앱이 없는 동안 라우트가 생기면 그 호스트는 누구에게나 열린다. Access → Applications → Add → Self-hosted: 위 "기록" 의 앱·정책·로그인 방식(One-time PIN 만, Instant Auth)·세션·쿠키 값 그대로.
저장 뒤 AUD 를 복사해 둔다(9단계에서 쓴다 — 레포·채팅에 붙이지 않는다).
- 확인: 앱 목록에 `admin.kimptrack.com`, 정책 1개(Allow · 그룹 하나).

### 6. 터널
Networks → Tunnels → Create → Cloudflared → 이름 `marketlens-serve`. 설치 안내 화면의 명령(`… --token eyJ…`)은 **실행하지 않는다** — 토큰이 명령 인자·셸 기록에 남는다. 토큰 문자열만 7단계에서 파일로 옮긴다. 라우트 단계는 건너뛴다(9단계).
- 설치 명령을 어느 기기에서든 실행하면 **그 기기가 커넥터가 된다**(2026-10-01 맥북에서 macOS 명령 `sudo cloudflared service install <토큰>` 이 한 번 실행돼 맥북이 커넥터가 됐다). 되돌리기: 그 기기에서 `sudo cloudflared service uninstall`. 토큰이 셸 기록에 남았으므로 그 터널은 지우고 새로 만든다(지우면 옛 토큰은 쓸모없어진다).

### 7. 토큰 파일·권한 (serve 박스)
```bash
cd ~/marketlens && mkdir -p secrets && chmod 700 secrets
(umask 077; read -rs T && printf '%s' "$T" > secrets/cloudflared-token)   # 붙여넣고 Enter — 화면·기록에 안 남는다
sudo chown 65532:65532 secrets/cloudflared-token && sudo chmod 0400 secrets/cloudflared-token
ls -l secrets/          # -r-------- 1 65532 65532 <크기 > 0> … cloudflared-token
git status --short      # 빈 출력 — secrets/ 는 git 무시(배포의 reset --hard 도 안 지운다)
```
소유 65532 인 이유: 컨테이너가 UID 65532 로 돌고, compose 파일 secret 은 바인드라 uid·mode 지정이 무시된다. `.env`·`server/.env` 에 토큰을 두지 않는다.

### 8. 배포
토큰 파일이 있으면 다음 main 배포의 serve 단계가 `--profile tunnel up -d` 를 한다(없으면 로그에 "tunnel 건너뜀"). 기다리지 않으려면 serve 에서:
```bash
cd ~/marketlens && docker compose --profile tunnel --env-file .env --env-file server/.env up -d
docker compose --profile tunnel --env-file .env --env-file server/.env ps   # cloudflared healthy (1분 안)
docker logs marketlens-cloudflared 2>&1 | grep -c 'Registered tunnel connection'   # 1 이상
```
- 확인: 대시보드 터널 커넥터 Healthy·serve 하나. 나가는 연결은 7844(UDP·TCP)·443 — serve 보안그룹 아웃바운드가 기본(전부 허용)이면 할 일 없다.

### 9. 라우트
터널 → Public hostname(Published application routes) → Add: subdomain `admin`, domain `kimptrack.com`, path 비움, Service `HTTP` · `web:8081`. Additional application settings → Access → **Protect with Access** 켬: required, Team name `<팀>`, AUD `<5단계 값>`. catch-all 은 `http_status:404` 그대로. 계정 전체 "Require Access protection" 이 Free 에서 켜지면 켠다.
- 확인: `curl -sI https://admin.kimptrack.com/` 이 `302` 이고 `location` 이 `<팀>.cloudflareaccess.com` 로그인.
- "이 이름의 DNS 레코드가 이미 있습니다": 터널을 지워도 라우트가 만든 DNS `admin` CNAME(`…cfargotunnel.com`, 프록시)은 남는다. 도메인 → DNS → 레코드에서 **`admin` 한 줄만** 지우고 다시 저장한다 — 루트·www 는 건드리지 않는다(운영 사이트).

### 10. 알림
Notifications → Add → Tunnel 상태 알림(Tunnel Health) → 메일. 등록 직후 시험 메일.

### 11. Universal SSL·회색 확인
SSL/TLS → Edge Certificates 의 Universal SSL 이 Active(`*.kimptrack.com` 을 덮는다). DNS 에서 루트·www 는 DNS only(회색), `admin` 만 Proxied.
- 확인: `curl -sI https://kimptrack.com/` 의 인증서가 여전히 Let's Encrypt(caddy, 023).

### 12. 관리자 접속 기록 회전 (serve 박스, 029 기록)
nginx 가 컨테이너 안에서 파일을 연 채 쓰므로 `copytruncate`. 매일·90개 = 90일(032 처리방침의 보관 기간) — 빈 날도 회전해야(`ifempty`) 90개가 곧 90일이다. 바꾼 뒤에는 아래 블록을 다시 적용한다.
```bash
sudo tee /etc/logrotate.d/marketlens-admin >/dev/null <<'EOF'
/home/ubuntu/marketlens/logs/admin/access.log {
    daily
    rotate 90
    copytruncate
    missingok
    ifempty
    su root root
}
EOF
sudo logrotate -d /etc/logrotate.d/marketlens-admin   # 오류 없이 rotating pattern … 한 줄
```

## 배포·런북 뒤 확인 (030 §4 사람 항목 — 결과는 status.md admin 행에)
- OTP 로그인 뒤 화면·API 문서·Try it out·즉시 갱신 동작 / 허용 안 된 이메일은 코드를 못 받는다 / 응답 `Set-Cookie` 에 `SameSite=Lax`·`HttpOnly`.
- 원점 검증: Access 앱 AUD 를 잠깐 틀리게(라우트의 AUD 칸) 넣으면 403 → 되돌린다.
- `https://kimptrack.com` 페이지 콘솔에서 `fetch('https://admin.kimptrack.com/api/health',{credentials:'include',mode:'no-cors'})` → `logs/admin/access.log` 에 `"sfs":"same-site"`·403.
- 위조 `Cf-Access-Authenticated-User-Email` 헤더를 붙인 요청의 기록에 실제 이메일이 남는다.
- 탄력 IP + `Host: admin.kimptrack.com`(`curl -H 'Host: admin.kimptrack.com' http://3.34.104.16/`)은 공개 사이트(랜딩)다.
- `docker stats --no-stream marketlens-cloudflared` 메모리(상한 128MB·스왑 없음) / 상태 알림 시험 메일 / 125초 응답 제한이 터널 경로에 그대로인지.

## 드리프트 확인 (분기마다·설정을 바꾼 뒤)
대시보드: 터널 라우트가 위 "기록" 그대로(호스트 하나 + catch-all 404, Protect with Access 켬), Private networks 비어 있음, 커넥터 하나. Zero Trust → Logs → Admin 에 모르는 변경이 없다.
API 로 전체를 본다 — 읽기 전용 토큰(Cloudflare Tunnel Read·Zero Trust Read)은 사람이 만들어 로컬 셸에만 둔다:
```bash
CF="https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID"; H="Authorization: Bearer $CF_API_TOKEN"
curl -s -H "$H" "$CF/cfd_tunnel/$TUNNEL_ID/configurations" | jq '.result.config.ingress'
#   기대: [{hostname:"admin.kimptrack.com", service:"http://web:8081", originRequest.access.required:true}, {service:"http_status:404"}]
curl -s -H "$H" "$CF/teamnet/routes?is_deleted=false" | jq '.result | length'   # 0 — private 라우트 없음
curl -s -H "$H" "$CF/cfd_tunnel/$TUNNEL_ID/connections" | jq '[.result[].id] | length'   # 1 — 커넥터 serve 하나
```

## 토큰 교체 (유출 의심 포함 — 030 §3.5 순서)
그동안 관리자 페이지만 끊긴다(공개 무관).
1. 대시보드 터널 화면에서 토큰 갱신(Refresh token). 기존 연결 끊기: `curl -s -X DELETE -H "$H" "$CF/cfd_tunnel/$TUNNEL_ID/connections"`(편집 권한 토큰).
2. 새 파일·권한 — 7단계를 그대로(기존 파일은 `sudo rm` 뒤).
3. cloudflared 재생성: `docker compose --profile tunnel --env-file .env --env-file server/.env up -d --force-recreate cloudflared`.
4. 커넥터 목록에 serve 하나만인지(위 connections 가 1).
5. Access 앱 → Revoke existing tokens(기존 로그인 세션 끊기).
6. `REFRESH_TOKEN` 교체(collect·serve 의 `server/.env` — 사람) — 관리자 화면의 즉시 갱신이 쓰는 값이다.
7. Zero Trust → Logs → Admin 에서 그 사이 라우트·앱 변경이 없는지.

## AUD 갱신 (Access 앱을 다시 만들었을 때 — 모든 요청 403)
새 앱의 AUD 복사(위 "AUD 위치") → 터널 라우트의 Protect with Access AUD 를 교체·저장 → 9단계 확인(302, 로그인 뒤 200). 컨테이너는 건드리지 않는다(설정은 원격).

## 팀원 이탈 (030 §3.5 순서)
그룹 `marketlens-admins` 에서 제거 → My Team → Users 에서 그 사용자 Revoke(기존 세션 끊기) → Remove users(좌석 반납) → 계정 Members 였으면 거기서도 제거 → 그 사람이 알던 토큰 교체(`REFRESH_TOKEN`, 터널 권한이 있었으면 위 "토큰 교체"). 위 "기록" 의 인원수를 고친다.

## 되돌리기 (공개 사이트와 무관)
- 관리자 페이지만 닫기: 대시보드에서 라우트 삭제, 또는 serve 에서 `docker compose --profile tunnel --env-file .env --env-file server/.env rm -sf cloudflared`. `stop` 이 아니라 지운다 — 멈춘 컨테이너는 옛 `admin` 망을 가리킨 채 남아, 그사이 serve 에 `down` 이 돌면 다음 배포의 tunnel `up` 이 "network … not found" 로 실패한다. 다음 배포가 토큰 파일을 보고 새로 만들므로 오래 닫으려면 파일을 치운다(`sudo mv secrets/cloudflared-token ~/cloudflared-token.off` — 배포가 "tunnel 건너뜀").
- serve 에서 `docker compose --profile serve … down` 을 돌렸을 때 cloudflared 가 멈춰 있었거나 재시작을 되풀이하던 중이었다면 `admin` 망이 지워졌다 — `docker compose --profile tunnel --env-file .env --env-file server/.env up -d --force-recreate cloudflared` 로 다시 만든다. 돌고 있던 cloudflared 는 망을 쥐고 있어 `down` 이 망을 남긴다("Resource is still in use"). 배포 줄에는 `--force-recreate` 를 넣지 않는다(배포마다 터널이 끊긴다).
- 걷어내기: 라우트 삭제 → 터널 삭제 → serve 에서 `docker compose --profile tunnel --env-file .env --env-file server/.env rm -sf cloudflared` → 토큰 파일 삭제 → Access 앱 삭제.

## 이미지 갱신 (분기마다 — 다음 2026-12)
지금 `cloudflare/cloudflared:2026.9.3`(2026-09-29 확인, 인덱스 digest `sha256:072c067d…ff92c`). `latest` 는 쓰지 않는다. 최신 안정 태그의 릴리스 노트를 읽고:
```bash
docker buildx imagetools inspect cloudflare/cloudflared:<새 태그>   # 맨 위 Digest(인덱스) + Platform 에 linux/arm64·linux/amd64
```
compose `image:` 줄의 태그와 **인덱스** digest(맨 위 Digest — 플랫폼별 digest 아님, serve 는 arm64)를 함께 바꾸는 PR → 머지 = 배포가 cloudflared 를 다시 만든다(관리자 페이지 수 초 끊김) → 8단계 확인.
