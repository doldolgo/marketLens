"""배포 설정 계약 — compose·워크플로·Dockerfile·nginx 를 파일로 읽어 단언한다 (스펙 007 §3·§4, 021 §4).

Docker 가 없는 CI 에서 도는 유일한 회귀 장치다. 컨테이너를 실제로 띄우는 검증은 §5 의 명령으로
Docker 가 있는 로컬·EC2 에서 사람이 돈다. 여기서는 설정 파일이 §4 의 조건을 말하는지만 본다.
"""

import logging
import re
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


# --- compose: 컨테이너 6개(016·023), 박스별 profile 3개(021) -------------------------

# 021 §3.2 — 서비스 → profile. 박스마다 자기 profile 만 띄운다.
PROFILE_OF = {
    "server": "collect",
    "redis": "data",
    "influxdb": "data",
    "api": "serve",
    "web": "serve",
    "caddy": "serve",
}


def test_compose_declares_six_containers_with_fixed_names() -> None:
    compose = _yaml("docker-compose.yml")
    services = compose["services"]
    assert set(services) == {"server", "api", "web", "caddy", "influxdb", "redis"}
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


def test_compose_gives_every_service_exactly_one_box_profile() -> None:
    """021 §3.2 — server=collect / redis·influxdb=data / api·web=serve. profile 없이 up 하면 아무것도 안 뜬다."""
    services = _yaml("docker-compose.yml")["services"]
    for name, svc in services.items():
        assert svc.get("profiles") == [PROFILE_OF[name]], name


def test_compose_has_no_depends_on_anywhere() -> None:
    # 의존 대상이 다른 박스에 있다 — 같은 compose 안에서 기다릴 수 없다 (021 §3.2)
    for name, svc in _yaml("docker-compose.yml")["services"].items():
        assert "depends_on" not in svc, f"{name} 에 depends_on 이 있다"


def test_compose_host_ports_are_caddy_and_cross_box_ports_only() -> None:
    """호스트 공개: caddy ${WEB_PORT:-80}·443(023), server 8000, redis 6379, influxdb 8086. web·api 는 없다 (021 §3.2)."""
    services = _yaml("docker-compose.yml")["services"]
    assert services["caddy"]["ports"] == ["${WEB_PORT:-80}:80", "443:443"]
    assert "ports" not in services["web"], "web 은 같은 박스의 caddy 만 부른다 (023)"
    assert services["server"]["ports"] == ["8000:8000"]
    assert services["redis"]["ports"] == ["6379:6379"]
    assert services["influxdb"]["ports"] == ["8086:8086"]
    assert "ports" not in services["api"], "api 는 같은 박스의 nginx 만 부른다"


def test_compose_injects_env_file_and_overrides_service_urls_via_data_host() -> None:
    """server 의 저장소 주소는 DATA_HOST 치환식, 기본값은 서비스 이름(한 박스 기동 그대로) (021 §3.2)."""
    server = _yaml("docker-compose.yml")["services"]["server"]
    assert server["env_file"] == ["./server/.env"]
    assert server["environment"]["INFLUX_URL"] == "http://${DATA_HOST:-influxdb}:8086"
    assert server["environment"]["REDIS_URL"] == "redis://${DATA_HOST:-redis}:6379/0"
    # server 에는 ROLE 을 주지 않는다 — 기본값 collector (016 §3.2)
    assert "ROLE" not in server.get("environment", {})


def test_compose_api_is_same_image_with_role_api_and_same_data_host_urls() -> None:
    """api 는 server 와 같은 빌드 컨텍스트, ROLE=api 만 다르다. 저장소 주소도 같은 DATA_HOST 치환식 (016 §3.2·017 §3.5·021 §3.2)."""
    services = _yaml("docker-compose.yml")["services"]
    server, api = services["server"], services["api"]
    assert api["build"] == server["build"] == "./server"
    assert api["env_file"] == ["./server/.env"]
    assert api["environment"]["ROLE"] == "api"
    assert api["environment"]["INFLUX_URL"] == server["environment"]["INFLUX_URL"]
    assert api["environment"]["REDIS_URL"] == server["environment"]["REDIS_URL"]


def test_compose_web_receives_collect_host_and_limits_envsubst_to_it() -> None:
    """nginx 의 수집 업스트림은 COLLECT_HOST(기본 server). envsubst 는 그 한 변수만 — $http_* 가 안 깨지게 (021 §3.2)."""
    web = _yaml("docker-compose.yml")["services"]["web"]
    assert web["environment"]["COLLECT_HOST"] == "${COLLECT_HOST:-server}"
    # compose 는 $$ 를 $ 하나로 넘긴다 — 컨테이너 안 값은 ^COLLECT_HOST$
    assert web["environment"]["NGINX_ENVSUBST_FILTER"] == "^COLLECT_HOST$$"


def test_compose_caddy_fronts_web_with_domain_tls_and_plain_fallback() -> None:
    """023 §3 — caddy 는 serve profile, Caddyfile 을 읽기 전용으로 마운트, 인증서는 이름 있는 볼륨에 남긴다."""
    caddy = _yaml("docker-compose.yml")["services"]["caddy"]
    assert caddy["image"].startswith("caddy:2")
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    assert "caddy-data:/data" in caddy["volumes"], (
        "볼륨이 없으면 재배포마다 재발급 → Let's Encrypt 한도"
    )
    conf = _text("Caddyfile")
    # 도메인 두 개는 자동 HTTPS, 그 밖의 호스트(IP 직접)는 평문 catch-all — 둘 다 nginx(web:80) 로
    assert "kimptrack.com, www.kimptrack.com {" in conf
    assert "http:// {" in conf
    assert conf.count("reverse_proxy web:80") == 2
    # HTTP/3 은 UDP 443 을 안 열므로 광고하지 않는다
    assert "protocols h1 h2" in conf


def test_compose_influx_caps_query_memory_within_data_box() -> None:
    """data 박스(2GB+스왑 1GB)에서 조회 폭주가 Influx 를 죽이지 않게 — 1개 256MB × 동시 3 = 전체 768MB (021 §3.1)."""
    env = _yaml("docker-compose.yml")["services"]["influxdb"]["environment"]
    per_query = int(env["INFLUXD_QUERY_MEMORY_BYTES"])
    total = int(env["INFLUXD_QUERY_MAX_MEMORY_BYTES"])
    concurrency = int(env["INFLUXD_QUERY_CONCURRENCY"])
    assert per_query == 256 * 1024 * 1024
    assert concurrency == 3
    # Influx 는 max = concurrency × memory 를 요구한다
    assert total == per_query * concurrency
    # 전체 상한 + Redis 상주 30MB + Influx 상주 0.5GB 가 1.5GB 를 넘지 않는다
    assert total + 30 * 1024 * 1024 + 512 * 1024 * 1024 <= 1.5 * 1024 * 1024 * 1024


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
    assert set(compose["volumes"]) == {
        "influxdb-data",
        "redis-data",
        "caddy-data",
        "caddy-config",
    }


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
    # 021 — templates 에 둬야 이미지 entrypoint 가 COLLECT_HOST 를 채워 conf.d 에 써 준다
    copies = [ln for ln in dockerfile.splitlines() if ln.startswith("COPY")]
    assert "COPY nginx.conf /etc/nginx/templates/default.conf.template" in copies
    assert not any("/etc/nginx/conf.d/" in ln for ln in copies)
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile
    assert ".env*" in _text("web/.dockerignore").splitlines()


# --- nginx: /api 접두 제거·SPA fallback·캐시 규칙 ---------------------------


def test_nginx_strips_api_prefix_and_falls_back_to_index() -> None:
    conf = _text("web/nginx.conf")
    assert "location /api/ {" in conf
    # proxy_pass 끝의 / 가 접두 제거를 만든다: /api/health → /health
    # 021 — 업스트림 호스트는 COLLECT_HOST 치환(수집은 다른 박스). 고정 서비스명은 남지 않는다.
    assert "proxy_pass http://${COLLECT_HOST}:8000/;" in conf
    assert "http://server:8000" not in conf
    assert "location = /api { return 404; }" in conf
    # 022 — SPA fallback 은 /app/ 아래에서만, / 는 정적 랜딩
    assert "try_files $uri $uri/ /app/index.html;" in conf
    assert "try_files /landing.html =404;" in conf
    assert "return 301 /app/$is_args$args;" in conf
    assert "try_files $uri $uri/ /index.html;" not in conf


def test_web_bundle_lives_under_app_prefix() -> None:
    """022 — vite base 가 /app/ 라야 /app/assets/… 로 번들을 찾고, nginx alias 가 그걸 dist/assets 로 잇는다."""
    vite = _text("web/vite.config.ts")
    assert "base: '/app/'" in vite
    conf = _text("web/nginx.conf")
    assert "alias /usr/share/nginx/html/assets/;" in conf


def _nginx_api_block() -> tuple[str, str]:
    """api 로 보내는 정규식 location — (패턴, 블록 본문)."""
    conf = _text("web/nginx.conf")
    match = re.search(r"location ~ (\S+) \{(.*?)\n    \}", conf, re.S)
    assert match is not None, "api 로 분기하는 정규식 location 이 없다"
    return match.group(1), match.group(2)


def test_nginx_routes_influx_history_paths_to_api_and_the_rest_to_server() -> None:
    """네 경로만 api:8000 으로, 접두를 떼고 (016 §3.3). /api/history/events 는 server 로."""
    pattern, block = _nginx_api_block()
    to_api = (
        "/api/history/premium",
        "/api/history/streaks",
        "/api/history/streaks/bulk",
        "/api/history/candles",
    )
    for path in to_api:
        assert re.search(pattern, path), path
    for path in ("/api/history/events", "/api/spreads", "/api/health", "/api/refresh"):
        assert not re.search(pattern, path), path
    assert "proxy_pass http://api:8000;" in block
    # proxy_pass 에 URI 가 없으므로 접두 제거는 rewrite 가 한다 — break 라 쿼리스트링은 그대로
    assert "rewrite ^/api/(.*)$ /$1 break;" in block
    for header in (
        "proxy_set_header Host $http_host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
    ):
        assert header in block, header


def test_nginx_routes_spreads_exactly_to_api_and_subpaths_to_server() -> None:
    """`= /api/spreads` 만 api:8000 으로 — 접두 제거·쿼리 유지·헤더 4개, 하위 경로는 server 로 (018 §3.3)."""
    conf = _text("web/nginx.conf")
    block = conf.split("location = /api/spreads {", 1)[1].split("\n    }", 1)[0]
    assert "proxy_pass http://api:8000;" in block
    # 016 과 같은 방식 — rewrite 가 접두를 떼고 break 라 쿼리스트링은 그대로 따라간다
    assert "rewrite ^/api/(.*)$ /$1 break;" in block
    for header in (
        "proxy_set_header Host $http_host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
    ):
        assert header in block, header
    # 정확 일치뿐이라 /api/spreads/… 는 어느 api 분기에도 안 걸리고 접두 location /api/ 가 server 로 보낸다
    assert "location /api/spreads" not in conf
    pattern, _ = _nginx_api_block()
    assert not re.search(pattern, "/api/spreads") and not re.search(
        pattern, "/api/spreads/x"
    )


def test_nginx_upgrades_api_ws_to_api_without_touching_read_timeout() -> None:
    """`/api/ws/` 접두 위치 → `api:8000/ws/`, Upgrade 헤더·HTTP/1.1, read timeout 은 기본 그대로 (017 §3.5)."""
    conf = _text("web/nginx.conf")
    block = conf.split("location /api/ws/ {", 1)[1].split("\n    }", 1)[0]
    assert "proxy_pass http://api:8000/ws/;" in block
    assert "proxy_http_version 1.1;" in block
    assert "proxy_set_header Upgrade $http_upgrade;" in block
    assert 'proxy_set_header Connection "upgrade";' in block
    for header in (
        "proxy_set_header Host $http_host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
    ):
        assert header in block, header
    assert "proxy_read_timeout" not in conf
    # 정규식 location(016)은 /api/ws/ 에 걸리지 않아야 접두 위치가 이긴다
    pattern, _ = _nginx_api_block()
    assert not re.search(pattern, "/api/ws/spreads")


def test_nginx_template_substitutes_only_collect_host() -> None:
    """021 §3.2 — ${…} 꼴 치환 변수는 COLLECT_HOST 하나뿐이고 api 로 가는 세 분기는 서비스명 그대로다.
    nginx 자체 변수는 $name 꼴이라 필터(^COLLECT_HOST$)에 걸리지 않는다."""
    conf = _text("web/nginx.conf")
    assert set(re.findall(r"\$\{(\w+)\}", conf)) == {"COLLECT_HOST"}
    assert conf.count("proxy_pass http://api:8000;") == 2
    assert "proxy_pass http://api:8000/ws/;" in conf
    for var in (
        "$http_host",
        "$remote_addr",
        "$proxy_add_x_forwarded_for",
        "$scheme",
        "$http_upgrade",
    ):
        assert var in conf, var


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
    assert "<title>KimpTrack</title>" in _text("web/index.html")


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


# --- deploy: main 푸시 → 박스 3대(data→collect→serve) 각각 SSH → 가드 → 미러 동기화 → 자기 profile up --build → prune


BOXES = ("data", "collect", "serve")

# 021 §3.3 — 박스별 가드 키. server/.env 의 INFLUX_TOKEN 은 세 박스 공통.
GUARDS = {
    "data": ["grep -q '^INFLUX_TOKEN=.' server/.env"],
    "collect": [
        "grep -q '^INFLUX_TOKEN=.' server/.env",
        "grep -q '^S3_BUCKET=.' server/.env",
        "grep -q '^DATA_HOST=.' .env",
    ],
    "serve": [
        "grep -q '^INFLUX_TOKEN=.' server/.env",
        "grep -q '^DATA_HOST=.' .env",
        "grep -q '^COLLECT_HOST=.' .env",
    ],
}


def _deploy_script(box: str) -> list[str]:
    deploy = _yaml(".github/workflows/deploy.yml")
    assert _on(deploy) == {"push": {"branches": ["main"]}}
    job = deploy["jobs"][box]
    step = job["steps"][0]
    assert step["uses"] == "appleboy/ssh-action@v1"
    assert step["with"]["host"] == "${{ secrets.EC2_HOST_" + box.upper() + " }}"
    assert step["with"]["username"] == "${{ secrets.EC2_USER }}"
    assert step["with"]["key"] == "${{ secrets.EC2_SSH_KEY }}"
    lines = [ln.strip() for ln in step["with"]["script"].splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def test_deploy_runs_three_boxes_in_order_data_collect_serve() -> None:
    """job 3개, needs 로 직렬 — 한 박스가 실패하면 뒤 박스는 돌지 않는다 (021 §3.3)."""
    jobs = _yaml(".github/workflows/deploy.yml")["jobs"]
    assert list(jobs) == list(BOXES)
    assert "needs" not in jobs["data"]
    assert jobs["collect"]["needs"] == "data"
    assert jobs["serve"]["needs"] == "collect"
    # 옛 단일 시크릿은 어디에도 남지 않는다
    assert "secrets.EC2_HOST }}" not in _text(".github/workflows/deploy.yml")


def _index_of(script: list[str], fragment: str) -> int:
    return next(i for i, ln in enumerate(script) if fragment in ln)


def test_deploy_script_per_box_guards_env_then_mirrors_main_then_builds_own_profile() -> (
    None
):
    for box in BOXES:
        script = _deploy_script(box)
        assert script[0] == "set -e", box
        assert script[1] == "cd ~/marketlens", box
        i_file = _index_of(script, "[ ! -f server/.env ]")
        i_guards = [_index_of(script, g) for g in GUARDS[box]]
        i_fetch = _index_of(script, "git fetch origin main")
        i_reset = _index_of(script, "git reset --hard origin/main")
        i_up = _index_of(
            script,
            f"docker compose --profile {box} --env-file .env --env-file server/.env up -d --build",
        )
        i_prune = _index_of(script, "docker image prune -f")
        assert (
            i_file < min(i_guards)
            and max(i_guards) < i_fetch < i_reset < i_up < i_prune
        ), box
        assert i_prune == len(script) - 1, box
        # 자기 가드 키만 — 다른 박스의 키를 요구하면 그 박스에서 배포가 헛되이 막힌다
        for other_box, guards in GUARDS.items():
            for g in guards:
                if g not in GUARDS[box]:
                    assert g not in script, (box, other_box, g)
        # profile 은 자기 것 하나만
        assert sum("--profile" in ln for ln in script) == 1, box


def test_deploy_script_never_prints_env_values() -> None:
    for box in BOXES:
        script = "\n".join(_deploy_script(box))
        assert "cat server/.env" not in script and "cat .env" not in script
        assert "$INFLUX_TOKEN" not in script and "$S3_BUCKET" not in script
        assert "$DATA_HOST" not in script and "$COLLECT_HOST" not in script
        assert "git pull" not in script, (
            "pull 은 갈래가 있으면 실패한다 — 미러 동기화만"
        )


# --- PR 템플릿·README ---------------------------------------------------------


def test_pr_template_is_three_line_skeleton() -> None:
    lines = _text(".github/pull_request_template.md").strip().splitlines()
    assert lines == ["무엇을:", "왜:", "테스트:"]


def test_readme_is_short_and_points_to_claude_md() -> None:
    readme = _text("README.md")
    assert len(readme.strip().splitlines()) <= 40
    assert "CLAUDE.md" in readme
    # 021 — 박스별 profile 기동이 README 의 배포 명령이다
    assert (
        "docker compose --profile <collect|data|serve> --env-file .env --env-file server/.env up -d --build"
        in readme
    )
    assert "여섯 컨테이너" in readme or "6컨테이너" in readme
