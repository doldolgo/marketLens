"""배포 설정 계약 — compose·워크플로·Dockerfile·nginx 를 파일로 읽어 단언한다 (스펙 007 §3·§4).

Docker 가 없는 CI 에서 도는 유일한 회귀 장치다. 컨테이너를 실제로 띄우는 검증은 §5 의 명령으로
Docker 가 있는 로컬·EC2 에서 사람이 돈다. 여기서는 설정 파일이 §4 의 조건을 말하는지만 본다.
"""

import logging
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def _yaml(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _on(workflow: dict) -> dict:
    # PyYAML 은 `on:` 키를 불리언 True 로 읽는다
    return workflow.get("on") or workflow[True]


# --- compose: 컨테이너 4개, 호스트 노출은 web 하나 ---------------------------


def test_compose_declares_four_containers_with_fixed_names() -> None:
    compose = _yaml("docker-compose.yml")
    services = compose["services"]
    assert set(services) == {"server", "web", "influxdb", "redis"}
    # 프로젝트명 고정 — dev compose(marketlens-dev)와 컨테이너·볼륨을 나눈다
    assert compose["name"] == "marketlens"
    assert _yaml("docker-compose.dev.yml")["name"] != compose["name"]
    for name, svc in services.items():
        assert svc["container_name"] == f"marketlens-{name}"
        assert svc["restart"] == "unless-stopped"
    # 기존 스택(market-lens-fe·market-lens-be)과 이름이 겹치지 않는다
    names = {svc["container_name"] for svc in services.values()}
    assert names.isdisjoint({"market-lens-fe", "market-lens-be"})


def test_compose_caps_container_logs_on_every_service() -> None:
    # 회전 없는 json 로그가 디스크를 채우면 Influx 가 쓰기를 거부한다 (007 §3)
    for name, svc in _yaml("docker-compose.yml")["services"].items():
        logging = svc.get("logging")
        assert logging is not None, f"{name} 에 로그 상한이 없다"
        assert logging["driver"] == "json-file"
        assert logging["options"] == {"max-size": "50m", "max-file": "3"}


def test_compose_exposes_only_web_on_host_via_web_port() -> None:
    services = _yaml("docker-compose.yml")["services"]
    assert services["web"]["ports"] == ["${WEB_PORT:-80}:80"]
    for name in ("server", "influxdb", "redis"):
        assert "ports" not in services[name], f"{name} 는 호스트에 열리면 안 된다"


def test_compose_injects_env_file_and_overrides_service_urls() -> None:
    server = _yaml("docker-compose.yml")["services"]["server"]
    assert server["env_file"] == ["./server/.env"]
    assert server["environment"]["INFLUX_URL"] == "http://influxdb:8086"
    assert server["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert set(server["depends_on"]) == {"influxdb", "redis"}


def test_compose_storage_containers_persist_and_match_dev_setup() -> None:
    compose = _yaml("docker-compose.yml")
    influx = compose["services"]["influxdb"]
    redis = compose["services"]["redis"]
    assert influx["image"] == "influxdb:2.7"
    env = influx["environment"]
    assert env["DOCKER_INFLUXDB_INIT_MODE"] == "setup"
    assert env["DOCKER_INFLUXDB_INIT_ORG"] == "marketlens"
    assert env["DOCKER_INFLUXDB_INIT_BUCKET"] == "marketlens"
    assert env["DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"] == "${INFLUX_TOKEN}"
    assert redis["image"] == "redis:7-alpine"
    assert redis["command"] == ["redis-server", "--appendonly", "yes"]
    assert influx["volumes"] == ["influxdb-data:/var/lib/influxdb2"]
    assert redis["volumes"] == ["redis-data:/data"]
    assert set(compose["volumes"]) == {"influxdb-data", "redis-data"}


# --- 이미지: .env 는 들어가지 않고, 워커는 1개 -------------------------------


def test_server_image_excludes_env_and_runs_one_worker() -> None:
    dockerfile = _text("server/Dockerfile")
    assert "FROM python:3.12-slim" in dockerfile
    copies = [ln for ln in dockerfile.splitlines() if ln.startswith("COPY")]
    # 컨텍스트 통째 복사(COPY . )는 .env 가 섞일 길이라 명시 경로만 복사한다
    assert copies == [
        "COPY pyproject.toml ./",
        "COPY app ./app",
        "COPY scripts ./scripts",
    ]
    assert '"--workers", "1"' in dockerfile
    assert '"--port", "8000"' in dockerfile
    ignore = _text("server/.dockerignore").splitlines()
    assert ".env" in ignore


def test_web_image_is_multistage_node22_to_nginx() -> None:
    dockerfile = _text("web/Dockerfile")
    assert "FROM node:22-alpine AS build" in dockerfile
    assert "RUN npm ci" in dockerfile and "RUN npm run build" in dockerfile
    assert "FROM nginx:" in dockerfile
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in dockerfile
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile
    assert ".env*" in _text("web/.dockerignore").splitlines()


# --- nginx: /api 접두 제거·SPA fallback·캐시 규칙 ---------------------------


def test_nginx_strips_api_prefix_and_falls_back_to_index() -> None:
    conf = _text("web/nginx.conf")
    assert "location /api/ {" in conf
    # proxy_pass 끝의 / 가 접두 제거를 만든다: /api/health → /health
    assert "proxy_pass http://server:8000/;" in conf
    assert "try_files $uri $uri/ /index.html;" in conf


def test_nginx_cache_rules_for_index_and_hashed_assets() -> None:
    conf = _text("web/nginx.conf")
    index_block = conf.split("location = /index.html", 1)[1].split("}", 1)[0]
    assert '"no-store, must-revalidate"' in index_block
    assert "always" in index_block  # 셸은 오류 응답에도 캐시 금지가 붙어야 한다
    assets_block = conf.split("location /assets/", 1)[1].split("}", 1)[0]
    assert '"public, max-age=31536000, immutable"' in assets_block
    # `always` 가 붙으면 404 에도 1년 immutable 이 실려 되돌릴 수 없게 캐시된다
    assert "always" not in assets_block


def test_app_logging_puts_marketlens_info_on_a_timestamped_handler() -> None:
    """설정이 없으면 lastResort 가 WARNING 이상만, 시각 없이 낸다 — 복구 신호가 안 보인다 (007 §3)."""
    create_app()
    create_app()  # 두 번 만들어도 handler 는 하나여야 한다(로그 중복 금지)
    root = logging.getLogger()
    mine = [h for h in root.handlers if getattr(h, "_marketlens", False)]
    assert len(mine) == 1
    assert "%(asctime)s" in (mine[0].formatter._fmt or "")
    assert logging.getLogger("marketlens").getEffectiveLevel() == logging.INFO
    # 라이브러리 INFO 는 루트의 WARNING 에 막힌다
    assert root.level >= logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_web_shell_title_is_the_smoke_string() -> None:
    assert "<title>트레이딩룸 · MarketLens</title>" in _text("web/index.html")


# --- 앱: 저장소 없이도 /health 200, /history 만 503 ----------------------------


def test_health_stays_up_without_storage_and_history_is_503() -> None:
    # lifespan 없이 띄우면 Influx·Redis 가 없는 상태 — Influx 컨테이너를 내린 것과 같다
    client = TestClient(create_app())
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    resp = client.get("/history/premium", params={"base": "BTC", "unit": "week"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "storage_unavailable"


# --- CI: job 2개, 경로 필터 없음, 순서 --------------------------------------


def test_ci_runs_both_jobs_on_every_pull_request() -> None:
    ci = _yaml(".github/workflows/ci.yml")
    on = _on(ci)
    assert set(on) == {"pull_request"}
    assert on["pull_request"] == {"branches": ["main"]}, (
        "경로 필터가 있으면 required check 가 빈다"
    )
    assert set(ci["jobs"]) == {"server", "web"}


def test_ci_server_job_lints_formats_and_tests() -> None:
    job = _yaml(".github/workflows/ci.yml")["jobs"]["server"]
    assert job["defaults"]["run"]["working-directory"] == "server"
    uses = [s["uses"] for s in job["steps"] if "uses" in s]
    assert uses == ["actions/checkout@v5", "actions/setup-python@v5"]
    setup = next(s for s in job["steps"] if s.get("uses") == "actions/setup-python@v5")
    assert setup["with"]["python-version"] == "3.12" and setup["with"]["cache"] == "pip"
    runs = [s["run"] for s in job["steps"] if "run" in s]
    assert runs[-3:] == ["ruff check .", "ruff format --check .", "pytest -q"]
    assert '"[dev]"' in runs[0] or "[dev]" in runs[0]


def test_ci_web_job_installs_lints_and_builds() -> None:
    job = _yaml(".github/workflows/ci.yml")["jobs"]["web"]
    assert job["defaults"]["run"]["working-directory"] == "web"
    uses = [s["uses"] for s in job["steps"] if "uses" in s]
    assert uses == ["actions/checkout@v5", "actions/setup-node@v5"]
    setup = next(s for s in job["steps"] if s.get("uses") == "actions/setup-node@v5")
    assert setup["with"]["node-version"] == "22" and setup["with"]["cache"] == "npm"
    runs = [s["run"] for s in job["steps"] if "run" in s]
    assert runs == ["npm ci", "npm run lint", "npm run build"]


# --- deploy: main 푸시 → SSH → 가드 → 미러 동기화 → up --build → prune -----------


def _deploy_script() -> list[str]:
    deploy = _yaml(".github/workflows/deploy.yml")
    assert _on(deploy) == {"push": {"branches": ["main"]}}
    step = deploy["jobs"]["deploy"]["steps"][0]
    assert step["uses"] == "appleboy/ssh-action@v1"
    assert step["with"]["host"] == "${{ secrets.EC2_HOST }}"
    assert step["with"]["username"] == "${{ secrets.EC2_USER }}"
    assert step["with"]["key"] == "${{ secrets.EC2_SSH_KEY }}"
    lines = [ln.strip() for ln in step["with"]["script"].splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def test_deploy_script_guards_env_then_mirrors_main_then_builds() -> None:
    script = _deploy_script()
    assert script[0] == "set -e"
    assert script[1] == "cd ~/marketlens"

    def index_of(fragment: str) -> int:
        return next(i for i, ln in enumerate(script) if fragment in ln)

    i_file = index_of("[ ! -f server/.env ]")
    i_token = index_of("grep -q '^INFLUX_TOKEN=.' server/.env")
    i_bucket = index_of("grep -q '^S3_BUCKET=.' server/.env")
    i_fetch = index_of("git fetch origin main")
    i_reset = index_of("git reset --hard origin/main")
    i_up = index_of(
        "docker compose --env-file .env --env-file server/.env up -d --build"
    )
    i_prune = index_of("docker image prune -f")
    assert i_file < i_token < i_bucket < i_fetch < i_reset < i_up < i_prune
    assert i_prune == len(script) - 1


def test_deploy_script_never_prints_env_values() -> None:
    script = "\n".join(_deploy_script())
    assert "cat server/.env" not in script
    assert "$INFLUX_TOKEN" not in script and "$S3_BUCKET" not in script
    assert "git pull" not in script, "pull 은 갈래가 있으면 실패한다 — 미러 동기화만"


# --- PR 템플릿·README ---------------------------------------------------------


def test_pr_template_is_three_line_skeleton() -> None:
    lines = _text(".github/pull_request_template.md").strip().splitlines()
    assert lines == ["무엇을:", "왜:", "테스트:"]


def test_readme_is_short_and_points_to_claude_md() -> None:
    readme = _text("README.md")
    assert len(readme.strip().splitlines()) <= 40
    assert "CLAUDE.md" in readme
    assert (
        "docker compose --env-file .env --env-file server/.env up -d --build" in readme
    )
    assert "네 컨테이너" in readme or "4컨테이너" in readme
