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
    """023 §3 — caddy 는 serve profile, 설정 디렉터리를 읽기 전용으로 마운트(027 — `caddy/Caddyfile`),
    인증서는 이름 있는 볼륨에 남긴다."""
    caddy = _yaml("docker-compose.yml")["services"]["caddy"]
    assert caddy["image"].startswith("caddy:2")
    assert "./caddy:/etc/caddy:ro" in caddy["volumes"]
    assert "caddy-data:/data" in caddy["volumes"], (
        "볼륨이 없으면 재배포마다 재발급 → Let's Encrypt 한도"
    )
    conf = _text("caddy/Caddyfile")
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


# --- nginx: 공개 /api 허용 목록(028)·SPA fallback·캐시 규칙 ------------------


def _nginx_tokens(text: str) -> list[str]:
    """nginx 설정 → 토큰. 주석은 버리고, 따옴표 문자열은 따옴표째 한 토큰, `${VAR}` 는 단어의 일부."""
    tokens: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif ch in "{};":
            tokens.append(ch)
            i += 1
        elif ch in "'\"":
            j = i + 1
            while text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            tokens.append(text[i : j + 1])
            i = j + 1
        else:
            j = i
            while j < len(text) and not text[j].isspace() and text[j] not in ";{}":
                j = text.index("}", j) + 1 if text.startswith("${", j) else j + 1
            tokens.append(text[i:j])
            i = j
    return tokens


# 지시어 하나 = (인자 — 첫 칸이 이름, 따옴표는 벗김, 블록 자식 또는 None)
Directive = tuple[list[str], list | None]


def _nginx_block(tokens: list[str], pos: int = 0) -> tuple[list[Directive], int]:
    items: list[Directive] = []
    args: list[str] = []
    while pos < len(tokens):
        tok = tokens[pos]
        if tok == ";":
            items.append((args, None))
            args, pos = [], pos + 1
        elif tok == "{":
            children, pos = _nginx_block(tokens, pos + 1)
            items.append((args, children))
            args = []
        elif tok == "}":
            return items, pos + 1
        else:
            quoted = len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "'\""
            args.append(tok[1:-1] if quoted else tok)
            pos += 1
    return items, pos


def _public_server() -> list[Directive]:
    """`listen 80` server 블록 — 029 가 같은 파일에 관리자 server 를 더해도 공개 쪽만 본다 (028 §4)."""
    tree, _ = _nginx_block(_nginx_tokens(_text("web/nginx.conf")))
    servers = [children for args, children in tree if args == ["server"]]
    public = [s for s in servers if (["listen", "80"], None) in s]
    assert len(public) == 1, "listen 80 server 가 하나여야 한다"
    return public[0]


def _args(block: list[Directive], name: str) -> list[list[str]]:
    return [args[1:] for args, _ in block if args[0] == name]


def _locations(block: list[Directive]) -> dict[tuple[str, ...], list[Directive]]:
    """location 전부(중첩 포함) — 키는 수식어와 경로 (`("=", "/api")`·`("/api/",)`·`("~", 패턴)`)."""
    found: dict[tuple[str, ...], list[Directive]] = {}
    for args, children in block:
        if args[0] == "location" and children is not None:
            found[tuple(args[1:])] = children
            found.update(_locations(children))
    return found


def _route(path: str) -> tuple[str, ...]:
    """정규식이 없는 공개 server 에서 nginx 가 고르는 location — 정확 일치, 없으면 가장 긴 접두."""
    locations = _locations(_public_server())
    if ("=", path) in locations:
        return ("=", path)
    prefixes = [key for key in locations if len(key) == 1 and path.startswith(key[0])]
    return max(prefixes, key=lambda key: len(key[0]))


COLLECTOR = "http://${COLLECT_HOST}:8000"
API = "http://api:8000"
# 028 §3.1 — 공개 허용 목록: 경로 → 업스트림. 웹·감시가 새 경로를 부르면 그 스펙이 여기와 nginx 에 한 줄을 더한다.
PUBLIC_API = {
    "/api/health": COLLECTOR,
    "/api/health/collect": COLLECTOR,
    "/api/history/events": COLLECTOR,
    "/api/history/candles": API,
    "/api/landing": API,
    "/api/ws/spreads": API,
}
PROXY_HEADERS = (
    ["Host", "$http_host"],
    ["X-Real-IP", "$remote_addr"],
    ["X-Forwarded-For", "$proxy_add_x_forwarded_for"],
    ["X-Forwarded-Proto", "$scheme"],
)
DENY = {("=", "/api"), ("/api/",)}


def test_nginx_falls_back_to_index_under_app_only() -> None:
    conf = _text("web/nginx.conf")
    assert "http://server:8000" not in conf
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


def test_public_api_forwards_exactly_the_six_allowlisted_paths() -> None:
    """백엔드로 넘기는 location = 허용 여섯(전부 =), 모양은 접두 제거 rewrite + URI 없는 proxy_pass (028 §3.1·§3.3)."""
    server = _public_server()
    assert not _args(server, "proxy_pass")
    proxied = {k: c for k, c in _locations(server).items() if _args(c, "proxy_pass")}
    assert set(proxied) == {("=", path) for path in PUBLIC_API}
    for (_, path), children in proxied.items():
        assert _args(children, "proxy_pass") == [[PUBLIC_API[path]]], path
        # 정규화된 경로에서 /api 를 뗀다 — break 라 쿼리스트링은 그대로 따라간다
        assert _args(children, "rewrite") == [["^/api/(.*)$", "/$1", "break"]], path
        headers = _args(children, "proxy_set_header")
        for header in PROXY_HEADERS:
            assert header in headers, (path, header)


def test_public_api_ws_spreads_upgrades_without_touching_read_timeout() -> None:
    """017 — 업그레이드 헤더·HTTP/1.1 은 `= /api/ws/spreads` 에만, read timeout 은 기본 그대로."""
    for key, children in _locations(_public_server()).items():
        headers = _args(children, "proxy_set_header")
        upgrades = key == ("=", "/api/ws/spreads")
        assert (["Upgrade", "$http_upgrade"] in headers) == upgrades, key
        assert (["Connection", "upgrade"] in headers) == upgrades, key
        assert (_args(children, "proxy_http_version") == [["1.1"]]) == upgrades, key
    server = _public_server()
    assert not _args(server, "proxy_read_timeout")
    for children in _locations(server).values():
        assert not _args(children, "proxy_read_timeout")


def test_public_api_rest_answers_app_shaped_json_404_without_proxy() -> None:
    """`/api` 와 그 밖의 `/api/*` 는 앱 404 와 같은 JSON, 확장자별 MIME 추정 끔, proxy_pass 없음 (028 §3.2)."""
    app_404 = TestClient(create_app()).get("/nope")
    assert app_404.status_code == 404
    locations = _locations(_public_server())
    for key in DENY:
        children = locations[key]
        assert sorted(args[0] for args, _ in children) == [
            "default_type",
            "return",
            "types",
        ], key
        assert [c for args, c in children if args[0] == "types"] == [[]], key
        assert _args(children, "default_type") == [["application/json"]], key
        assert _args(children, "return") == [["404", app_404.text]], key


def test_public_server_has_no_regex_location_and_hides_version() -> None:
    """정규식 location 은 접두 /api/ 보다 먼저 이겨 허용 목록을 우회한다 — 하나도 두지 않는다 (028 §3.1)."""
    server = _public_server()
    keys = set(_locations(server))
    assert not [k for k in keys if k[0] in ("~", "~*")]
    assert ("/api/ws/",) not in keys
    assert {k for k in keys if k[-1].startswith("/api")} == DENY | {
        ("=", path) for path in PUBLIC_API
    }
    assert _args(server, "server_tokens") == [["off"]]


def test_closed_api_paths_route_to_the_json_404() -> None:
    """API 문서·분석 6개·/refresh·history 무거운 조회·/spreads·끝에 / 붙은 허용 경로는 공개에 없다 (028 §3.2·§4)."""
    closed = [
        "/api",
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
        "/api/premium",
        "/api/premium/scan",
        "/api/matrix",
        "/api/arbitrage",
        "/api/orderbook/upbit",
        "/api/slippage/upbit",
        "/api/refresh",
        "/api/history/premium",
        "/api/history/streaks",
        "/api/history/streaks/bulk",
        "/api/spreads",
        "/api/ws",
        "/api/ws/other",
        "/api/nope",
    ]
    for path in closed + [path + "/" for path in PUBLIC_API]:
        assert _route(path) in DENY, path
    for path in PUBLIC_API:
        assert _route(path) == ("=", path), path


def test_nginx_template_substitutes_only_collect_host() -> None:
    """021 §3.2 — ${…} 꼴 치환 변수는 COLLECT_HOST 하나뿐이고 api 로 가는 세 분기는 서비스명 그대로다.
    nginx 자체 변수는 $name 꼴이라 필터(^COLLECT_HOST$)에 걸리지 않는다."""
    conf = _text("web/nginx.conf")
    assert set(re.findall(r"\$\{(\w+)\}", conf)) == {"COLLECT_HOST"}
    # 경로별 업스트림 개수는 세지 않는다 — 공개 쪽은 허용 여섯 테스트가 고정하고, 029 의 관리자 server 가
    # 같은 파일에 프록시를 더해도 이 테스트가 깨지지 않게(028 §4 — 단언은 listen 80 블록만)
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


# --- 호출 경로 대조: 웹·감시가 부르는 /api 경로 ⊂ 공개 허용 목록 (028 §3.5·§4) -------

# 경로 끝 — 쿼리·템플릿 보간·따옴표 앞에서 자른다
_TAIL = r"[^?`$'\"]*"
# 요청 경로로 쓰인 /api… — 따옴표·백틱·보간 닫는 } 바로 뒤이거나 URL 의 호스트 뒤. /apix 는 아니다
_API_PATH = re.compile(
    r"(?:['\"`}]|(?:https?|wss?)://[^/\s'\"`]+)(/api(?:/[\w.~%-]*)*)(?![\w.~%-])"
)


def _strip_comments(text: str, suffix: str) -> str:
    """주석에 적힌 경로(‘/api/refresh 는 부르지 않는다’)가 호출로 잡히지 않게 뺀다."""
    if suffix in (".html", ".js", ".mjs", ".cjs", ".ts"):
        text = re.sub(r"<!--.*?-->|/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"(^|\s)//[^\n]*", r"\1", text)
    elif suffix in (".py", ".sh", ".yml", ".yaml", ".toml"):
        text = re.sub(r"(^|\s)#[^\n]*", r"\1", text)
    return text


def _web_src_paths() -> set[str]:
    """(1) `${API_BASE}` 바로 뒤 리터럴 경로 — 리터럴 / 로 시작하지 않으면 대조할 수 없으니 실패.
    `API_BASE + …` 처럼 템플릿 밖에서 쓰면 경로를 못 뽑으므로 실패하고(정의·import 줄과 주석은 예외),
    따옴표 안의 리터럴 `/api/…` 는 그대로 대조에 넣는다."""
    paths: set[str] = set()
    for file in sorted((ROOT / "web/src").rglob("*.ts*")):
        text = _strip_comments(file.read_text("utf-8"), ".ts")
        for tail in re.findall(r"\$\{API_BASE\}(" + _TAIL + ")", text):
            assert tail.startswith("/"), f"{file.name}: ${{API_BASE}} 뒤 {tail!r}"
            paths.add("/api" + tail)
        for line in text.splitlines():
            bare = re.sub(r"\$\{API_BASE\}", "", line)
            if "API_BASE" in bare and not re.match(
                r"\s*(import\b|export const API_BASE\b|API_BASE,|\})", line
            ):
                raise AssertionError(
                    f"{file.name}: 템플릿 밖의 API_BASE — {line.strip()}"
                )
        paths |= set(re.findall(r"['\"`](/api/" + _TAIL + ")", text))
    return paths


def _public_html_paths() -> set[str]:
    """(2) 정적 페이지(랜딩)의 `fetch(`·`new WebSocket(` 첫 인자 따옴표 안 `/api/…` (주석 제외)."""
    call = re.compile(r"\b(?:fetch|new\s+WebSocket)\(\s*['\"`](/api/" + _TAIL + ")")
    paths: set[str] = set()
    for file in sorted((ROOT / "web/public").glob("*.html")):
        paths |= set(call.findall(_strip_comments(file.read_text("utf-8"), ".html")))
    return paths


def _canary_paths(root: Path = ROOT / "ops/canary") -> set[str]:
    """(3) canary(027) 코드·설정의 요청 경로. 디렉터리가 없으면(027 구현 전) 빈 집합이고, 생기면 테스트를
    고치지 않아도 대조에 들어간다. 문서(.md)·숨김·의존성 폴더는 요청이 아니라 뺀다."""
    paths: set[str] = set()
    if not root.is_dir():
        return paths
    for file in sorted(root.rglob("*")):
        parts = file.relative_to(root).parts
        skip = any(
            p.startswith(".") or p in ("node_modules", "__pycache__") for p in parts
        )
        if skip or not file.is_file() or file.suffix == ".md":
            continue
        try:
            text = file.read_text("utf-8")
        except UnicodeDecodeError:
            continue
        text = _strip_comments(text, file.suffix)
        paths |= set(_API_PATH.findall(text))
        if file.suffix in (".yml", ".yaml"):  # `path: /api/health` 처럼 따옴표 없는 값
            paths |= set(re.findall(r":\s*(/api(?:/[\w.~%-]*)*)", text))
    return paths


def _uptime_paths() -> set[str]:
    """(3) 외부 uptime 모니터(025)에 등록하는 URL 의 경로."""
    text = _text("docs/runbooks/uptime-monitor.md")
    return set(re.findall(r"https?://[^/\s`'\")]+(/api(?:/[\w.~%-]*)*)", text))


def test_web_and_monitor_call_paths_are_all_allowlisted() -> None:
    """웹·감시가 새 경로를 부르면서 허용 목록(nginx·PUBLIC_API)에 한 줄을 더하지 않으면 여기서 실패한다."""
    sources = {
        "web/src": _web_src_paths(),
        "web/public": _public_html_paths(),
        "ops/canary": _canary_paths(),
        "uptime-monitor.md": _uptime_paths(),
    }
    for name in ("web/src", "web/public", "uptime-monitor.md"):
        assert sources[name], f"{name} 에서 호출 경로를 못 찾았다 — 추출 규칙이 깨졌다"
    for name, paths in sources.items():
        assert paths <= set(PUBLIC_API), (name, sorted(paths - set(PUBLIC_API)))


def test_canary_paths_are_picked_up_once_the_directory_exists(tmp_path: Path) -> None:
    """027 이 ops/canary/ 를 만들면 그 요청 경로가 그대로 대조에 들어간다 — 주석·문서는 빼고."""
    assert _canary_paths(tmp_path / "missing") == set()
    (tmp_path / "canary.js").write_text(
        "// /api/refresh 는 부르지 않는다\n"
        "const WS = 'wss://kimptrack.com/api/ws/spreads';\n"
        "await get(`${BASE}/api/history/candles?base=BTC`);\n",
        "utf-8",
    )
    (tmp_path / "steps.py").write_text(
        '# /api/docs\nURL = BASE + "/api/health"\n', "utf-8"
    )
    (tmp_path / "README.md").write_text("`/api/spreads` 는 닫혀 있다\n", "utf-8")
    (tmp_path / "checks.yml").write_text(
        "steps:\n  - path: /api/landing  # /api/docs\n", "utf-8"
    )
    assert _canary_paths(tmp_path) == {
        "/api/ws/spreads",
        "/api/history/candles",
        "/api/health",
        "/api/landing",
    }


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
    # 025 — lifespan 없이는 틱이 없어 starting·503. 앱이 답하는 것 자체가 "떠 있다" 다
    assert health.status_code == 503
    assert health.json()["status"] == "starting"
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
