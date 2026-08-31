# MarketLens

한국 거래소(업비트·빗썸)와 해외 거래소(바이낸스) 간 김치 프리미엄(김프)·역프를 1초 단위로 계산해 보여주는 차익거래 모니터링 대시보드.

**문서 진입점은 [`CLAUDE.md`](CLAUDE.md)** — 모든 문서(컨텍스트·스펙·런북)는 거기서 파생된다.

## 퀵스타트 (로컬 개발)

```bash
# 0) env — server/.env.example 을 복사해 server/.env 를 만들고 값을 채운다 (전부 선택값)
# 1) InfluxDB (선택 — 없어도 앱은 뜬다, /history/* 만 503)
docker compose --env-file server/.env -f docker-compose.dev.yml up -d
# 2) server (Python 3.12, uv 가상환경 — docs/context/dev-setup.md)
cd server && uvicorn app.main:app --reload   # :8000
# 3) web
cd web && npm ci && npm run dev              # :5173, /api → :8000 프록시
```

## 통합 기동 (Docker)

```bash
docker compose --env-file .env --env-file server/.env up -d --build
```

server·web·influxdb 세 컨테이너가 뜨고, 호스트에는 web 하나만 열린다(`WEB_PORT`, 기본 80).

## 배포

`main` 머지 = 배포. PR 마다 CI(server·web)가 돌고, 머지되면 GitHub Actions 가 EC2 에 SSH 로 `docker compose up -d --build` 를 실행한다(`.github/workflows/`).
