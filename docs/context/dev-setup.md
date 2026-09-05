# dev-setup.md — 로컬 실행

## 사전 준비
- Python 3.12, Node 22(CI·배포 고정 — 로컬은 v26 도 동작 확인), Docker Desktop
- `:8000` `:5173` `:8086` `:6379` 비어 있어야 함.

## server
1. env 파일을 만든다(env 예시 파일 복사). 전부 선택값. `INFLUX_TOKEN` 은 dev compose 가 admin 토큰으로 쓴다.
2. dev compose 기동: `docker compose --env-file server/.env -f docker-compose.dev.yml up -d` → InfluxDB 2.7 `:8086` + Redis 7 `:6379`(009). 첫 기동(setup)이 org/bucket `marketlens` 를 만들고 admin 토큰 = `INFLUX_TOKEN`. Influx 가 없어도 앱은 뜬다(`/history/*` 만 503). Redis 가 없어도 앱은 뜬다(인계된 틱만 버려진다).
   백필: `cd server && .venv/bin/python -m scripts.backfill BTC ETH --days 92` (재실행 안전 — 앞뒤 빈 구간만 채움).
3. 가상환경(uv, Python 3.12) 만들고 의존성 설치 — **editable 로만**: `uv pip install --python .venv/bin/python -e ".[dev]"`. 비-editable 설치(`-e` 없음)는 금지다 — site-packages 에 앱 사본이 생겨 `server/` 밖 cwd 에서 그 사본(옛 모듈)을 import 한다. 사본이 있으면 `uv pip uninstall --python .venv/bin/python marketlens-server` 뒤 editable 로 다시 설치한다.
4. 서버를 `:8000` 에 띄운다(reload).
```bash
curl -s localhost:8000/health                       # 확인
```
테스트·린트 (커밋 전 필수):
```bash
pytest -q                    # Influx 없이, 네트워크 없음
ruff check . && ruff format .
```

## web
```bash
cd web
npm ci
npm run dev        # http://localhost:5173 , /api → localhost:8000 프록시
npm run build      # tsc -b && vite build
npm run lint       # oxlint
```
테스트 러너 없음 (현재). 스펙에서 도입하기 전까지 FE 검증은 `build` + `lint` + 수동 확인.

## env (server/.env)
| 키 | 기본 |
|---|---|
| INFLUX_URL | `http://localhost:8086` |
| INFLUX_TOKEN | 없음 |
| REDIS_URL | `redis://localhost:6379/0` |
| REFRESH_TOKEN | 없음 |
| UPBIT_API_KEY | 없음 |
| UPBIT_SECRET_KEY | 없음 |
| BINANCE_API_KEY | 없음 |
| BINANCE_SECRET_KEY | 없음 |
| S3_BUCKET | 없음 |
| S3_REGION | `ap-northeast-2` |

- `INFLUX_URL`·`INFLUX_TOKEN`: InfluxDB 2.7 접속(org·bucket 은 `marketlens` 고정). 토큰이 없으면 flusher 비활성·`/history/*` 503 — 앱은 뜬다. 사람용 UI 는 `http://localhost:8086`(같은 토큰).
- `REDIS_URL`: Redis 7 접속(009 틱 버퍼). compose 안에서는 `redis://redis:6379/0` 으로 덮어쓴다. 없으면 인계된 틱이 버려진다(앱은 뜬다).
- `REFRESH_TOKEN`: 설정 시 `POST /refresh` 에 `X-Refresh-Token` 헤더가 필요하다.
- 거래소 API 키 4개: 입출금 상태 조회용. 없으면 해당 거래소 상태는 `null`(모름). 빗썸은 키 불필요. 업비트는 호출 IP 가 Open API 허용 목록에 있어야 한다.
- `S3_BUCKET`: 거래소 원문 아카이브 S3 저장(010, 접두사 `raw/`). 없으면 아카이브 비활성, 앱은 뜬다.
- `S3_REGION`: 버킷 리전. AWS 자격증명은 env 가 아니라 `~/.aws`(로컬, `aws configure`)·IAM 역할(EC2)이다.

**API 키는 .env 에만. 코드·문서·커밋에 절대 넣지 않는다.**

## docker 통합 기동 (배포와 같은 구성)
```bash
WEB_PORT=8080 docker compose --env-file server/.env up -d --build
```
`localhost:8080` 에 화면, `/api/*` 는 nginx 가 server 로 프록시(접두 제거). 내릴 때 `docker compose down`(볼륨 유지).

## 검증용 스모크
```bash
curl -s localhost:8000/spreads | head -c 600
```
(기동 10초 뒤 — 마켓 목록 REST 1회 + 스트림 스냅샷 한 바퀴. 012 전에는 바이낸스 심볼이 없어 우주가 비므로 404 가 정상) 최상위 `rate > 1000`·`notional == 10000`, 행 수 > 100, `warnings` 는 평상시 빈 배열(008), 각 행의 키가 정확히 다음 17개면 정상 (003 §4 기준):
`sym, dom, fx, fwd, rev, usd, spark, status, age, slipFwd, slipRev, krw, netDom, depDom, wdDom, depFx, wdFx`
체결 규모를 바꿔 슬리피지가 커지는지 본다 — `curl -s "localhost:8000/spreads?notional=500000"` 의 같은 행 `slipFwd` 가 기본값보다 크거나 같아야 한다.
```bash
curl -s "localhost:8000/slippage/upbit?symbol=BTC/KRW&amount=1000000" | head -c 300
```
`slippagePercent ≥ 0`, `levelsConsumed ≥ 1` 이면 정상 (004).
```bash
curl -s "localhost:8000/history/premium?base=BTC&unit=week" | head -c 300
```
(dev compose 기동 + 60초 뒤) `count ≥ 1` 이면 정상, Influx 없으면 503 `storage_unavailable` (005).
```bash
aws s3 ls s3://<bucket>/raw/ --recursive | tail -3
```
(`S3_BUCKET` 설정 + 기동 60초 뒤) 거래소 3곳 객체(`raw/exchange=…/dt=…/hh=…/….jsonl.gz`)면 정상 (010). `aws s3 cp <key> - | gunzip | head -1` 의 줄이 `exchange`·`source`·`receivedAt`·`raw` 4키면 정상.
```bash
docker compose -f docker-compose.dev.yml exec redis redis-cli XLEN ticks
```
초당 1씩 늘다가 매분 0 근처로 떨어지면 정상 (009). 2~3분 뒤 `/spreads` 행의 `spark` 에 값 1~3개.
```bash
curl -s localhost:8000/health/collect | head -c 400
```
`exchanges` 에 거래소 3곳(`upbit`·`bithumb`·`binance` 순), 기동 몇 초 뒤 각 `state: "ok"` 면 정상 (011).
```bash
curl -s "localhost:8000/orderbook/binance?symbol=BTC/USDT&depth=20" | head -c 400
```
기동 60초 뒤(첫 재조정) `asks` 가 20단계면 정상 (012). 스트림이 안 붙으면 바이낸스 행이 없어 404 이고, 30초 무수신이면 `/health/collect` 의 바이낸스가 `stale_stream` 구간을 보인다.

## 로컬 메모 (개인)
- `:8000` 은 이 머신에서 소마 캘린더가 점유할 수 있다. `lsof -i :8000` 으로 확인 후 정리하거나, `--port 8020` 으로 띄우고 curl 포트도 8020 으로 맞춘다.
- 이 머신엔 `python3`=3.9 뿐이다. `python3 -m venv` 금지. 가상환경(uv, Python 3.12)으로 만들고 의존성 설치도 uv pip 로(`--python` 에 그 venv 의 파이썬 지정).
- `actionlint` 미설치. 워크플로 lint 는 건너뛰고 실행 보고에 기록한다.
- 이 망(통신사 필터)에서 거래소·금융 도메인이 차단될 수 있다(REST·WebSocket 모두 — 2026-09-05 실측: `api.upbit.com`·`api.bithumb.com` ConnectTimeout). 마켓 목록을 못 받으면 서버는 5초마다 재시도하며 뜨고 `/spreads` 는 404 다. 실거래소 검증(001 §4 선택 항목 포함)은 EC2 에서 돌린다.
