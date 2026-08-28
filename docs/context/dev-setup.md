# dev-setup.md — 로컬 실행

## 사전 준비
- Python 3.12, Node 22(CI·배포 고정 — 로컬은 v26 도 동작 확인), Docker Desktop
- `:8000` `:5173` `:8086` 비어 있어야 함.

## server
1. env 파일을 만든다(env 예시 파일 복사). 전부 선택값. `INFLUX_TOKEN` 은 dev compose 가 admin 토큰으로 쓴다.
2. dev compose(Influx 하나) 기동 → InfluxDB 2.7 `:8086` (005 이후). 첫 기동(setup)이 org/bucket `marketlens` 생성.
3. 가상환경(uv, Python 3.12) 만들고 의존성 설치.
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
| REFRESH_TOKEN | 없음 |
| UPBIT_API_KEY | 없음 |
| UPBIT_SECRET_KEY | 없음 |
| BINANCE_API_KEY | 없음 |
| BINANCE_SECRET_KEY | 없음 |

- `INFLUX_URL`·`INFLUX_TOKEN`: InfluxDB 2.7 접속(org·bucket 은 `marketlens` 고정). 토큰이 없으면 저장 루프 비활성·`/history/*` 503 — 앱은 뜬다. 사람용 UI 는 `http://localhost:8086`(같은 토큰).
- `REFRESH_TOKEN`: 설정 시 `POST /refresh` 에 `X-Refresh-Token` 헤더가 필요하다.
- 거래소 API 키 4개: 입출금 상태 조회용. 없으면 상태는 `null`(모름).

**API 키는 .env 에만. 코드·문서·커밋에 절대 넣지 않는다.**

## 검증용 스모크
```bash
curl -s localhost:8000/spreads | head -c 600
```
최상위 `rate > 1000`, 행 수 > 100, 각 행의 키가 정확히 다음 18개면 정상 (003 §4 기준):
`sym, dom, fx, fwd, rev, usd, spark, status, age, liqDom, liqFx, rateAsk, rateBid, netDom, depDom, wdDom, depFx, wdFx`
```bash
curl -s "localhost:8000/slippage/upbit?symbol=BTC/KRW&amount=1000000" | head -c 300
```
`slippage_percent ≥ 0`, `levels_consumed ≥ 1` 이면 정상 (004).

## 로컬 메모 (개인)
- `:8000` 은 이 머신에서 소마 캘린더가 점유할 수 있다. `lsof -i :8000` 으로 확인 후 정리하거나, `--port 8020` 으로 띄우고 curl 포트도 8020 으로 맞춘다.
- 이 머신엔 `python3`=3.9 뿐이다. `python3 -m venv` 금지. 가상환경(uv, Python 3.12)으로 만들고 의존성 설치도 uv pip 로(`--python` 에 그 venv 의 파이썬 지정).
- `actionlint` 미설치. 워크플로 lint 는 건너뛰고 실행 보고에 기록한다.
- 이 망(통신사 필터)에서 거래소·금융 도메인이 차단될 수 있다. 실거래소 호출 검증이 안 되면 EC2 에서 돌린다.
