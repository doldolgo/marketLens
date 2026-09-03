# 010 — s3-snapshot

상태: DONE | 의존: 001(collect — 수집 시각·락), 003(spreads — `/spreads` 행 계약), 005(history — persist 루프 패턴), 007(deploy — env 주입·EC2)

> 이 문서는 이 기능이 **지금 어떻게 동작해야 하는지**를 적는다. 동작이 바뀌면 이 문서를 직접 고치고, 같은 PR 에서 코드·테스트도 맞춘다(CLAUDE.md §4·§6). 사람이 끝까지 읽는 문서다 — 코드를 산문으로 옮기지 않는다.
> 구현 구조(클래스·함수·파일 내부)는 실행 세션의 몫이다. 여기엔 **무엇이 어떻게 동작해야 하는가**만 쓴다.

## 1. 목적
`/spreads` 가 매 순간 계산하는 **행 단위 표 전체**(코인 × 국내 × 해외 페어의 김프/역프·호가 유동성·시세·입출금 상태)를 60초마다 S3 에 한 객체씩 남긴다. 005 의 Influx `premium` 은 fwd/rev 두 수치만 남기므로, "그 시각에 표가 정확히 어떻게 보였는가"(유동성·stale·망 상태 포함)는 재구성할 수 없다 — 이 스펙이 그 원본 스냅샷을 쌓는다. 끝나면 버킷에 1분마다 `.jsonl.gz` 가 생기고, 한 줄이 곧 `/spreads` 의 한 행이라 pandas·Athena 로 바로 읽힌다.

## 2. 범위
- 만드는 것: 공유 인프라의 S3 업로더(연결·쓰기), 앱 기동이 관리하는 **snapshot 루프**(60초), env `S3_BUCKET`·`S3_REGION`, 테스트.
- 하지 않는 것: 읽는 HTTP 엔드포인트(사람이 S3 콘솔·CLI·pandas 로 본다). 보존기간·lifecycle(버킷 설정은 사람 몫, 코드는 관여하지 않는다). 백필·소급 업로드. FE 표시. 005 persist 루프 변경 — **두 루프는 독립**이며 한쪽 장애가 다른 쪽에 번지지 않는다.
- 바꾸는 기존 것: ① `pyproject.toml` — `boto3` 추가(라이브러리 추가, 설계 세션 결정). ② 007 deploy 워크플로의 env 가드에 `S3_BUCKET` 존재·비어있지 않음 검사 추가(`INFLUX_TOKEN` 가드와 같은 형식, 값은 출력하지 않는다). ③ `server/.env.example` 에 두 키 추가.

## 3. 동작

### 3.1 읽는 계약 (복사)
- 001: 수집 루프는 1초마다 거래소 호가를 메모리에 **통째 교체**하며 락을 잡는다. 저장소는 마지막 수신 시각(epoch 초)을 갖는다. 수집이 한 번도 안 돌았으면 수신 시각은 없음.
- 003: `/spreads` 표 계산은 저장소를 받아 응답 모양 `{rate, rows, dataReceivedAt, warnings, fetchedAt}` 를 돌려주는 공개 함수 하나로 이뤄진다. 행은 다음 18키(camelCase)이고 `(sym, dom, fx)` 오름차순으로 고정돼 있다:
  `sym, dom, fx, fwd, rev, usd, spark, status, age, liqDom, liqFx, rateAsk, rateBid, netDom, depDom, wdDom, depFx, wdFx`
  기준 거래소(업비트) 시세가 없거나 국내·해외 스냅샷이 비면 계산은 "시장 데이터 없음" 예외를 던진다(라우터는 404).
- 005: persist 루프는 기동 후 먼저 60초 잔 뒤 회차를 반복하고, 수집과 같은 락을 잡고 메모리를 읽으며, 저장 실패는 로그 후 다음 회차 재시도, 놓친 회차는 구멍으로 남긴다. 켜는 조건은 시크릿 존재(`INFLUX_TOKEN`). 이 스펙의 루프는 **같은 규칙**을 따른다.

### 3.2 설정·인증
- env 두 개(둘 다 `server/.env`, 사람이 채운다):
  - `S3_BUCKET` — 버킷 이름. **없으면 snapshot 루프 비활성**, 앱은 뜬다(경고 로그 1줄). 켜고 끄는 스위치는 이 값의 존재 여부다.
  - `S3_REGION` — 기본값 `ap-northeast-2`.
- **AWS 액세스 키는 어디에도 적지 않는다.** SDK 의 기본 자격증명 탐색 순서를 그대로 쓴다 — 로컬은 `~/.aws/credentials`(사람이 `aws configure` 로 만든 것), EC2 는 인스턴스에 붙인 IAM 역할. `.env`·compose·코드에 `AWS_ACCESS_KEY_ID` 류 키가 등장하면 안 된다.
- 버킷: **`marketlens-spreads-snapshot`**, 리전 `ap-northeast-2` — 2026-09-03 생성 완료(퍼블릭 액세스 전부 차단, 버전 관리 끔 — 매 분 새 키라 필요 없다).
- 저장 주기 60초는 코드 상수다(005 와 같다).

### 3.3 외부 의존 — S3
- 호출은 `PutObject` 하나(기동 시 `HeadBucket` 1회는 아래). 요청당 타임아웃 연결 3초·읽기 10초, SDK 재시도 2회 — 한 회차가 다음 회차(60초)를 넘기지 않게 한다.
- EC2 IAM 역할에 필요한 최소 권한: 버킷 `spreads/*` 접두사에 `s3:PutObject`, 버킷에 `s3:ListBucket`(HeadBucket 용). 그 외 없음.
- **컨테이너에서 IAM 역할 쓰기(알려진 quirk)**: 인스턴스 메타데이터(IMDSv2) 는 기본 hop limit 1 이라 docker 브리지 네트워크 안의 컨테이너에서 닿지 않는다. 인스턴스의 metadata hop limit 을 **2** 로 올려야 한다(`aws ec2 modify-instance-metadata-options --http-put-response-hop-limit 2 --http-endpoint enabled`). 이건 사람이 하는 인프라 작업이고 ec2-setup 런북에 적는다.
- 기동 시 `HeadBucket` 으로 접근을 한 번 확인한다. 실패해도 앱은 뜬다 — 에러 로그 1줄, 루프는 회차마다 다시 시도한다(005 의 Influx ping 과 같다).

### 3.4 객체 — 키·내용
- 한 회차 = 객체 1개 = `PutObject` 1번(전부 성공 또는 전부 없음).
- 키: `spreads/dt=YYYY-MM-DD/hh=HH/YYYYMMDDTHHMMSSZ.jsonl.gz` — 전부 **UTC**, 시각은 `dataReceivedAt`(수집 시각, 초 정밀도). `dt=`·`hh=` 는 Athena/Glue 의 Hive 파티션 관례라 나중에 테이블을 얹을 수 있고, 콘솔에서도 날짜·시간 폴더로 보인다. 예: `spreads/dt=2026-09-02/hh=01/20260902T011507Z.jsonl.gz`.
- 내용: gzip 압축한 JSON Lines. **한 줄 = `/spreads` 의 한 행**(18키, camelCase, 값·순서 API 와 동일) 에 최상위 세 값을 그대로 붙인 **21키**: `rate`(업비트 USDT ask, `/spreads` 의 `rate`), `dataReceivedAt`(epoch ms), `warnings`(문자열 배열, 보통 빈 배열). 같은 객체 안 모든 줄의 이 세 값은 같다 — 줄 하나만 읽어도 맥락이 완결되게 하기 위해서다. `fetchedAt` 은 싣지 않는다(응답 시각이지 데이터 시각이 아니다). `spark` 는 API 값 그대로 싣는다(009 tick-store 구현 전까지는 빈 배열).
- 줄 순서 = API 행 순서 `(sym, dom, fx)`. 같은 시각 스냅샷을 다시 만들면 바이트까지 같아야 한다(결정적).
- 메타데이터: `Content-Type: application/x-ndjson`, `Content-Encoding: gzip`.
- 크기 감: 행 ~150 × 21키 ≈ 압축 전 30~50KB, gzip 후 5~10KB. 하루 1,440 객체.

### 3.5 snapshot 루프 (60초)
- 기동 후 **먼저 60초 잔 뒤** 첫 회차(직후엔 메모리가 비어 있다).
- 한 회차 순서:
  1. 수집과 **같은 락**을 잡고 003 의 표 계산 함수로 응답을 만든다(락 밖에서 만들면 통째 교체 도중 반쪽을 읽는다). 수집이 아직 안 돌았거나 계산이 "시장 데이터 없음" 예외를 내면 경고 로그 후 **생략**.
  2. `dataReceivedAt` 이 **직전에 올린 객체와 같으면 생략**(수집이 멈춘 동안 같은 표를 매 분 다시 올리지 않는다 — 005 는 같은 시각 점을 덮어쓰지만 S3 는 덮어쓸 이유가 없다). 프로세스 재기동 시 "직전" 은 비어 있으므로 첫 회차는 올린다.
  3. 직렬화·gzip 후 `PutObject` 1번. 락은 1 단계에서만 잡고 네트워크 I/O 는 락 밖에서 한다.
- 실패(자격증명 없음·권한 거부·네트워크·타임아웃): 로그 `S3 저장 실패 (연속 n회)` 후 다음 회차 재시도. **수집·`/spreads`·Influx persist 루프는 영향 없다.** 놓친 회차는 구멍으로 남고 소급하지 않는다. 실패한 회차의 `dataReceivedAt` 은 "직전 올린 것" 으로 기록하지 않는다(다음 회차에 같은 시각이라도 다시 시도).
- 회차 안 예외는 밖으로 던지지 않는다 — 버그 하나로 루프가 영구 정지하면 안 된다(005 와 같다).
- 종료: 앱 shutdown 이 루프를 취소하고 SDK 클라이언트를 닫는다.

## 4. 검증
네트워크 없음 — S3 는 `put(key, body, ...)` 시그니처의 fake 로 대체한다(005 의 FakeInflux 와 같은 방식, moto 안 씀). CI 에 AWS 자격증명이 없으므로 테스트가 실제 S3 를 만지면 실패해야 정상이다.
- 수집 1회 후 회차 → 객체 1개, 키가 `spreads/dt=YYYY-MM-DD/hh=HH/…Z.jsonl.gz` 형식이고 시각이 `dataReceivedAt` 의 UTC 와 일치한다
- 객체를 gunzip 해 줄 수를 세면 같은 저장소로 만든 `/spreads` 의 행 수와 같고, 각 줄이 정확히 21키(18 + `rate`·`dataReceivedAt`·`warnings`)이며 같은 행의 값이 API 응답과 동일하다
- 줄 순서가 `(sym, dom, fx)` 오름차순이고, 같은 저장소로 두 번 만든 바이트가 같다
- 수집 없이 회차 2번 → 객체 1개(두 번째는 `dataReceivedAt` 같아 생략); 수집이 한 번 더 돈 뒤 회차 → 객체 2개
- 수집이 아직 안 돌았을 때 회차 → 아무것도 안 올리고 0 반환
- 기준 거래소 시세가 없을 때 회차 → 생략·경고 로그, 예외 없음
- 업로드 실패 회차 → 0 반환·실패 로그(연속 횟수 포함)·다음 회차에 같은 `dataReceivedAt` 을 다시 올린다; 성공하면 연속 횟수가 0 으로 돌아간다
- fake 가 예외를 던져도 루프가 다음 회차를 이어간다
- `S3_BUCKET` 없이 기동 → 루프 비활성·경고 로그, `/health` 200, `/spreads` 정상
- `S3_BUCKET` 은 있는데 자격증명이 없어도 기동 성공(`/health` 200) — 실패는 회차 로그로만
- 수동: 로컬 `~/.aws` 자격증명 + `S3_BUCKET` 설정 후 서버 기동 → 약 60초 뒤 버킷에 첫 객체. `aws s3 cp s3://<bucket>/<key> - | gunzip | head -2` 로 두 줄이 `/spreads` 행과 같은지 눈으로 대조. `S3_BUCKET` 을 없는 버킷으로 바꿔 기동하면 회차마다 실패 로그가 찍히되 `/spreads` 는 계속 갱신. EC2 배포 후 hop limit 2 상태에서 객체가 쌓이는지 확인. 서버 테스트·lint 통과.

## 5. 완료 기준 (실행 세션이 채움 — 실제로 돌린 명령)
```bash
cd server && .venv/bin/python -m pytest -q            # 231 passed (test_snapshot.py 10개 포함)
cd server && .venv/bin/ruff check . && .venv/bin/ruff format .   # All checks passed
cd web && npm run lint && npm run build               # 문서 동시 변경 — 전체 검증 (CLAUDE.md §6)

# 실서버 스모크 (2026-09-03, 로컬 ~/.aws 자격증명, :8000 점유라 :8020 사용)
S3_BUCKET=marketlens-spreads-snapshot S3_REGION=ap-northeast-2 .venv/bin/uvicorn app.main:app --port 8020
aws s3 ls s3://marketlens-spreads-snapshot/spreads/ --recursive | tail -3
# → 기동 60초 뒤 객체 1개: spreads/dt=2026-09-03/hh=10/20260903T103352Z.jsonl.gz (23,659B)
aws s3 cp s3://marketlens-spreads-snapshot/spreads/dt=2026-09-03/hh=10/20260903T103352Z.jsonl.gz - | gunzip | head -2
# → 두 줄 모두 21키(camelCase, API 순서), (sym,dom,fx) 오름차순, /spreads 행과 값 일치.
#   줄 수 490 = /spreads 행 수 490.
S3_BUCKET=marketlens-no-such-bucket-zzz … uvicorn …   # 없는 버킷 기동
# → /health 200, 기동 시 "S3 버킷 접근 확인 실패" 1줄,
#   회차마다 "S3 저장 실패 (연속 1회): … NoSuchBucket", /spreads 는 계속 갱신(490행)
```

## 6. 갱신할 문서
- `docs/context/status.md` — 표에 `| s3-snapshot | 60초 snapshot 루프·S3 업로드 | - | 읽기 API 없음, 버킷 lifecycle 은 사람 몫 |` 행 추가. **항상 포함.**
- `CLAUDE.md` — 스펙 인덱스 010 행 상태 → DONE. **항상 포함.**
- `docs/context/db.md` — "엔진" 절에 두 번째 저장소로 S3 1줄(버킷·리전·용도 = `/spreads` 행 스냅샷, Influx 와의 차이 = 수치 2개 vs 표 전체). "쓰는 쪽" 절에 snapshot 루프 1줄(60초·객체 1개·키 규칙·생략 규칙·실패는 로그 후 다음 회차·`S3_BUCKET` 없으면 비활성). "읽는 쪽" 에 "S3 를 읽는 HTTP 엔드포인트 없음 — 사람이 CLI·pandas 로 본다" 1줄. "보존" 절에 "S3 는 lifecycle 미설정(무제한), 정하면 버킷 설정으로" 1줄.
- `docs/context/architecture.md` — "런타임 구성" server 줄 의존성에 `boto3`, "저장소" 항목에 S3 1줄. "데이터 흐름 (BE)" 그림의 persist 루프 옆에 `60초마다 snapshot 루프 ──▶ S3 (/spreads 행 전체를 .jsonl.gz 로)` 가지 추가. "현재 구조" 절에 s3-snapshot 항목(모듈·역할·Influx 루프와 분리한 이유 1~2줄).
- `docs/context/dev-setup.md` — "env (server/.env)" 표에 `S3_BUCKET`·`S3_REGION` 행과 설명 1줄씩(없으면 루프 비활성, 자격증명은 `~/.aws`). "검증용 스모크" 에 `aws s3 ls s3://<bucket>/spreads/ --recursive | tail -1` 과 기대값(기동 60초 뒤 객체 1개) 추가.
- `docs/runbooks/ec2-setup.md` — 4단계 `.env` 목록에 `S3_BUCKET`. 새 단계로 ① 버킷 생성(이름·리전·퍼블릭 전부 차단·버전 관리 끔 — §3.2 기준, 현 버킷은 생성돼 있고 재구축 시를 위한 기록) ② IAM 역할 생성·인스턴스 부착(정책 = §3.3 최소 권한)과 metadata hop limit 2 설정 명령.
- `server/.env.example` — `S3_BUCKET=`·`S3_REGION=ap-northeast-2` 와 "AWS 키는 넣지 않는다, 로컬은 `aws configure`·EC2 는 IAM 역할" 주석.

## 7. 실행 보고 (실행 세션이 채움)
- 만든 것 (파일 목록):
  - `server/app/core/s3.py` — S3 업로더(`S3Uploader.put/head_bucket/close`, 실패는 `S3UnavailableError` 하나로). boto3 import 는 이 모듈뿐.
  - `server/app/core/snapshot.py` — `build_object`(순수: 응답 → 키·gzip 본문)·`SnapshotLoop`(60초 회차, 005 persist 패턴 미러).
  - `server/tests/test_snapshot.py` — §4 목록 전부 (fake S3, 기동 테스트는 수집 루프 no-op 패치).
  - 수정: `core/config.py`(`s3_bucket`·`s3_region`), `app/main.py`(lifespan 기동·종료), `pyproject.toml`(boto3), `.github/workflows/deploy.yml`(S3_BUCKET 가드), `server/.env.example`, `docs/context/{status,db,architecture,dev-setup}.md`, `docs/runbooks/ec2-setup.md`, `CLAUDE.md` 인덱스.
- 추측한 지점 (묻지 않고 정한 사소한 것) / 실행 중 함께 고친 스펙 절:
  - `core/snapshot.py` 가 `features/spreads/service` 를 직접 import 한다(표 계산 함수·예외·응답 타입). 규약 문면은 기능 간 import 만 금지고, 006 도 collector(core)가 wallet_status 를 쓰는 선례가 있어 주입 대신 직접 import 를 택했다(순환 없음).
  - 직렬화: `json.dumps(…, ensure_ascii=False, separators=(",", ":"))` + `gzip.compress(mtime=0)` — mtime 을 고정하지 않으면 같은 스냅샷의 바이트가 매번 달라져 §3.4 결정성 위반.
  - "SDK 재시도 2회" → botocore `retries={"max_attempts": 3, "mode": "standard"}`(첫 시도 1 + 재시도 2).
  - 회차 시작 시 `received_at is None` 을 락 안에서 먼저 확인해 "수집 전" 과 "계산 예외" 생략을 구분(전자는 로그 없음, 후자는 경고 로그 — §4 문면 그대로).
  - 스펙 절 수정은 없음.
- 남은 빚:
  - 스모크로 실버킷에 객체 1개(`dt=2026-09-03/hh=10`)가 남아 있다 — 실데이터라 삭제하지 않았다.
