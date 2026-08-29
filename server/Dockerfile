# server 이미지 — python 3.12 slim + uvicorn 워커 1개 (스펙 007 §3)
# .env 는 이미지에 넣지 않는다 — compose 의 env_file 로만 주입 (시크릿이 레이어에 남지 않게)
FROM python:3.12-slim

WORKDIR /app

# 의존성 레이어 분리 — 코드만 바뀌면 pip 설치 레이어를 재사용한다
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app
# 백필은 컨테이너 안에서 돈다 (ec2-setup.md 8단계 — Influx 가 호스트에 비노출이라)
COPY scripts ./scripts

EXPOSE 8000
# 워커 1개 고정 — 메모리가 진실이라 프로세스가 둘이면 진실도 둘 (architecture.md)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
