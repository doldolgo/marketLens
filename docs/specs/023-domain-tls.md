# 023 — domain-tls

상태: DONE | 의존: 007 deploy(compose·배포 워크플로), 021 infra-split(serve 박스·보안그룹), 022 landing(nginx 경로)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.

## 1. 목적
`https://kimptrack.com` 으로 연다. 지금은 탄력 IP 를 평문으로 치는데, 도메인이 없으면 공유가 안 되고 HTTPS 가 없으면 브라우저가 "안전하지 않음" 을 띄우고 클립보드·알림 같은 API 가 막힌다. 인증서 발급·갱신은 사람 손을 타지 않아야 한다 — 3개월마다 만료되는 인증서를 사람이 갱신하면 언젠가 잊는다.

## 2. 범위
- 만드는 것: serve 박스 compose 에 `caddy` 컨테이너 하나(호스트 80·443), 루트 `Caddyfile`, 배포 계약 테스트. DNS 레코드는 Cloudflare 에 이미 있다(A `kimptrack.com`·`www` → `3.34.104.16`, 프록시 끔 — 인증서 발급을 원본이 직접 받도록).
- 하지 않는 것: Cloudflare 프록시(주황 구름)·HSTS·HTTP/3·ALB·ACM. 백엔드 코드 변경 없음. 배포 워크플로 변경 없음(`--profile serve` 가 caddy 도 띄운다).
- 바꾸는 기존 것: 호스트 포트 `WEB_PORT` 를 잡는 컨테이너가 web(nginx) 에서 caddy 로 바뀐다. web 은 호스트에 열지 않고 caddy 만 부른다. serve 보안그룹에 443 이 열린다(2026-09-25 열어 둠).

## 3. 동작

### 3.1 요청 경로
| 요청 | 응답 |
|---|---|
| `https://kimptrack.com/…` `https://www.kimptrack.com/…` | caddy 가 TLS 종단 → `web:80`(nginx) → 022 의 경로 그대로 |
| `http://kimptrack.com/…` `http://www.kimptrack.com/…` | caddy 가 `308` 로 같은 경로의 https 로 보낸다(Caddy 기본 동작) |
| `http://3.34.104.16/…`·로컬 `http://localhost:WEB_PORT/…` | 평문 그대로 `web:80` 으로 — 리다이렉트 없음 |
| `https://3.34.104.16/…` | 인증서 없음 — 브라우저 오류(의도한 것, IP 로는 HTTPS 를 주지 않는다) |
| `wss://kimptrack.com/api/ws/…` | caddy 의 `reverse_proxy` 가 WebSocket 업그레이드를 그대로 넘긴다. FE 는 `location.protocol` 로 `wss:` 를 고른다(이미 그렇게 돼 있다) |

### 3.2 인증서
- Let's Encrypt, HTTP-01(80) 또는 TLS-ALPN-01(443). 두 도메인이 모두 이 박스로 풀려야 한다 — DNS 가 안 퍼진 상태에서 caddy 를 띄우면 실패를 반복하며 시간당 한도(같은 이름 5회)에 걸린다. **순서 = DNS → 배포.**
- 인증서·ACME 계정은 볼륨 `caddy-data` 에 남는다. 재배포(`up -d --build`)·재기동에도 재발급하지 않는다. 볼륨을 지우면 재발급이 일어난다(주간 한도 = 같은 이름 5장).
- 갱신은 caddy 가 만료 30일 전에 스스로 한다. 사람이 볼 것은 없다.
- 첫 발급은 컨테이너 기동 뒤 백그라운드 — 그 몇 초 동안 https 는 안 열리지만 평문 경로는 바로 산다.

### 3.3 헤더
- caddy 는 `X-Forwarded-For`·`X-Forwarded-Proto: https` 를 붙여 nginx 로 넘기지만, nginx 가 자기 `$scheme`(http)·`$remote_addr`(caddy 의 컨테이너 IP) 로 **덮어쓴다**. 백엔드는 이 두 헤더를 읽지 않으므로 지금은 문제가 없다(nginx 가 `absolute_redirect off` 라 리다이렉트 주소도 스킴을 안 탄다). 백엔드가 접속 IP 나 스킴을 쓰게 되는 날 nginx 쪽을 고친다 — 이 스펙은 안 건드린다.

### 3.4 로컬
- `COMPOSE_PROFILES=collect,data,serve WEB_PORT=8090 …` 통합 기동은 그대로 된다. 다만 caddy 가 `kimptrack.com` 인증서를 받으려다 실패하는 로그가 몇 분 간격으로 남는다(로컬로는 도메인이 안 풀리므로). 평문 `http://localhost:8090/` 은 영향 없다. 호스트 443 도 잡으므로 로컬에서 443 을 쓰는 다른 것이 있으면 충돌한다.

## 4. 검증
- `pytest server/tests/test_deploy.py` — 컨테이너 여섯, caddy 가 serve profile·호스트 `WEB_PORT`·443, web 은 호스트 포트 없음, Caddyfile 에 도메인 두 개 + `http://` catch-all + `reverse_proxy web:80` 둘.
- `docker run --rm -v ./Caddyfile:/etc/caddy/Caddyfile:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile` 이 통과한다.
- 로컬 caddy + 더미 nginx(`web`) 로: `curl -s -o /dev/null -w '%{http_code}' localhost:8090/` = 200, `curl -sI -H 'Host: kimptrack.com' localhost:8090/` = 308 + `Location: https://kimptrack.com/`.
- 배포 뒤(EC2): `curl -sI https://kimptrack.com/` 200, `curl -sI http://kimptrack.com/` 308, `curl -sI http://3.34.104.16/` 200, 대시보드 `/app/` 스프레드 표가 wss 로 갱신된다(개발자 도구 Network → WS 에 `wss://kimptrack.com/api/ws/spreads`).

## 5. 완료 기준
- `cd server && .venv/bin/python -m pytest -q` → 687 passed
- `docker run --rm -v ./Caddyfile:/etc/caddy/Caddyfile:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile` → Valid configuration
- 로컬 caddy + `nginx:alpine`(이름 `web`) : `localhost:8090/` 200, `Host: kimptrack.com` 308 → `https://kimptrack.com/`, `www` 도 동일
- PR #51 머지(2026-09-25 11:15 UTC) → Deploy data·collect·serve 모두 success

## 6. 갱신할 문서
- `docs/context/status.md` deploy 행 — caddy 앞단 (반영함)
- `docs/context/architecture.md` serve 박스 줄 (반영함)
- `docs/specs/007-deploy.md` 포트·컨테이너 수 (반영함), `021-infra-split.md` 포트 줄 (반영함)
- `docs/runbooks/ec2-split.md`·`ec2-setup.md` serve 보안그룹 443 (반영함)
- `README.md` 컨테이너 수·포트 (반영함)
- `CLAUDE.md` 스펙 인덱스 023 행 (반영함)

## 7. 실행 보고
- 배포 뒤 EC2 실측(2026-09-25 11:2x UTC): `https://kimptrack.com/` HTTP/2 200, `https://www.kimptrack.com/` 200, `http://kimptrack.com/` 308 → https, `http://3.34.104.16/` 200, `https://kimptrack.com/api/health/collect` 정상, `wss://kimptrack.com/api/ws/spreads` 에서 표 메시지 수신. 인증서 발급자 Let's Encrypt, 유효 2026-09-25 ~ 2026-12-24(자동 갱신).
- caddy 로그: 기동 7초 뒤 두 도메인 "certificate obtained successfully". 컨테이너 caddy(80·443)·web(호스트 미공개)·api.
- 사전에 한 것: Cloudflare A 레코드 apex·www(프록시 끔), serve 보안그룹 443(CLI).
- 남긴 것: nginx 가 `X-Forwarded-Proto`·`X-Real-IP` 를 덮어쓴다(3.3). Cloudflare 프록시는 안 켰다.
