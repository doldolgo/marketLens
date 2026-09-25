# MarketLens

한국 거래소(업비트·빗썸)와 해외 거래소(바이낸스) 간 김치 프리미엄(김프)·역프를 1초 단위로 계산해 보여주는 차익거래 모니터링 대시보드.

**문서 진입점은 [`CLAUDE.md`](CLAUDE.md)** — 모든 문서(컨텍스트·스펙·런북)는 거기서 파생된다.

## 퀵스타트 (로컬 개발)

```bash
# 0) env — server/.env.example 을 복사해 server/.env 를 만들고 값을 채운다 (전부 선택값)
# 1) InfluxDB + Redis (선택 — 없어도 앱은 뜬다, /history/* 만 503·인계된 틱만 버려진다)
docker compose --env-file server/.env -f docker-compose.dev.yml up -d
# 2) server (Python 3.12, uv 가상환경 — docs/context/dev-setup.md)
cd server && uvicorn app.main:app --reload   # :8000
# 3) web
cd web && npm ci && npm run dev              # :5173, /api → :8000 프록시
```

## 통합 기동 (Docker)

```bash
# 로컬 — 여섯 컨테이너를 한 망에 (COMPOSE_PROFILES 가 없으면 아무것도 안 뜬다)
COMPOSE_PROFILES=collect,data,serve WEB_PORT=8080 docker compose --env-file server/.env up -d --build
# EC2 — 박스마다 자기 profile 하나만 (collect=server / data=redis·influxdb / serve=api·web·caddy)
docker compose --profile <collect|data|serve> --env-file .env --env-file server/.env up -d --build
```

server·api·web·caddy·influxdb·redis 여섯 컨테이너(api 는 `/history/*` 조회 전용 — 수집과 프로세스가 다르다, caddy 는 `kimptrack.com` TLS 앞단)가 EC2 3대에 나뉘어 뜬다. 박스 간 주소는 루트 `.env` 의 `DATA_HOST`·`COLLECT_HOST`(사설 IP) — 안 주면 서비스 이름이라 로컬은 한 망에서 그대로 돈다. 호스트 포트: caddy `WEB_PORT`(기본 80)·443, 그리고 다른 박스가 붙는 server 8000·redis 6379·influxdb 8086(보안그룹이 막는다).

## 배포

`main` 머지 = 배포. PR 마다 CI(server·web)가 돌고, 머지되면 GitHub Actions 가 EC2 3대에 data → collect → serve 순서로 SSH 해 각 박스의 profile 로 `docker compose up -d --build` 를 실행한다(`.github/workflows/`, 스펙 021).
