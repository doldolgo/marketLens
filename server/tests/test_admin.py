"""관리자 server 설정 계약 — nginx-admin.conf·web 이미지·compose (스펙 029 §3.1·§3.2·§3.5·§4, 034·035 의 피드 넷)
와 관리자 화면 정적 단언(036 §4, 설명·이름표·문구는 041 §4).

test_deploy.py 와 같은 방식이다: Docker 없는 CI 에서 설정 파일을 읽어 단언한다. 실제로 nginx 를 띄워
분기·403·기록을 보는 검증은 029 §5 의 로컬 Docker 명령이다. 문법은 여기서 못 잡는다 — nginx-admin.conf 를
고친 PR 은 로컬 `nginx -t` 결과를 본문에 적는다(§3.6).
"""

import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from typing import Any

import pytest

from tests.test_deploy import (
    API,
    COLLECTOR,
    DENY,
    ROOT,
    Directive,
    _args,
    _locations,
    _nginx_block,
    _nginx_tokens,
    _public_server,
    _route,
    _text,
    _yaml,
)

ADMIN_CONF = "web/nginx-admin.conf"
FORBIDDEN = '{"error":{"code":"forbidden","message":"Forbidden","detail":null}}'
SFS_CHECK = (["if", "($admin_sfs_ok", "=", "0)"], [(["return", "403"], None)])
# 교차 사이트 검사 예외 — 화면(/)과 문서 두 쪽 (§3.2)
SFS_EXEMPT = {("=", "/api/docs"), ("=", "/api/redoc")}
# 화면의 10초 폴링 — 기록하지 않는다 (§3.2). 034 의 수집기 관리자 피드 둘·035 의 api 피드 둘도 화면의 폴링이다
FEEDS = {("=", "/api/admin/aws"), ("=", "/api/admin/alerts")}
API_FEEDS = {
    ("=", "/svc/api/admin/access"),
    ("=", "/svc/api/admin/clarity"),
    ("=", "/svc/api/admin/attention"),  # 052 — 화면 영역 이용 통계
}
POLLING = (
    {
        ("=", "/api/health"),
        ("=", "/api/health/collect"),
        ("=", "/svc/api/health"),
        ("=", "/svc/api/admin/status"),
    }
    | FEEDS
    | API_FEEDS
)
# 브라우저가 스스로 부르는 아이콘 — 본문 없는 204, 기록하지 않는다 (§3.1·§3.2)
FAVICON = ("=", "/favicon.ico")
BASE_HEADERS = [
    ["Host", "$http_host"],
    ["X-Real-IP", "$remote_addr"],
    ["X-Forwarded-For", "$proxy_add_x_forwarded_for"],
    ["X-Forwarded-Proto", "$scheme"],
    ["Cookie", ""],
    ["Cf-Access-Jwt-Assertion", ""],
]
XFO = ["X-Frame-Options", "DENY", "always"]


def _admin_tree() -> list[Directive]:
    tree, _ = _nginx_block(_nginx_tokens(_text(ADMIN_CONF)))
    return tree


def _admin_server() -> list[Directive]:
    servers = [c for args, c in _admin_tree() if args == ["server"]]
    assert len(servers) == 1, "관리자 파일의 server 는 하나"
    return servers[0]


def _effective(location: list[Directive], name: str) -> list[list[str]]:
    """nginx 상속 규칙 — location 에 같은 이름이 하나라도 있으면 그것만, 없으면 server 수준 것."""
    return _args(location, name) or _args(_admin_server(), name)


def _proxied() -> dict[tuple[str, ...], list[Directive]]:
    return {
        k: c for k, c in _locations(_admin_server()).items() if _args(c, "proxy_pass")
    }


def _admin_route(path: str) -> tuple[str, ...]:
    """정규식이 없는 관리자 server 에서 nginx 가 고르는 location — 정확 일치, 없으면 가장 긴 접두."""
    locations = _locations(_admin_server())
    if ("=", path) in locations:
        return ("=", path)
    prefixes = [k for k in locations if len(k) == 1 and path.startswith(k[0])]
    return max(prefixes, key=lambda key: len(key[0]))


def _forward(path: str) -> tuple[str, str]:
    """요청 경로 → (업스트림, 백엔드가 받는 경로). rewrite 는 URI 전체를 바꾼다($1 은 첫 캡처)."""
    children = _locations(_admin_server())[_admin_route(path)]
    ((upstream,),) = _args(children, "proxy_pass")
    for pattern, replacement, flag in _args(children, "rewrite"):
        assert flag == "break"
        m = re.search(pattern, path)
        if m:
            path = m.expand(re.sub(r"\$(\d)", r"\\\1", replacement))
            break
    return upstream, path


# --- 관리자 server: 포트·업스트림·분기 (§3.1) -------------------------------------------


def test_admin_server_listens_on_8081_only_and_public_stays_on_80() -> None:
    server = _admin_server()
    assert _args(server, "listen") == [["8081"]]
    assert _args(_public_server(), "listen") == [["80"]]
    assert "8081" not in _text("web/nginx.conf")
    assert _args(server, "server_tokens") == [["off"]]


def test_admin_redirects_are_relative() -> None:
    """`/api`·`/api/ws` 의 nginx 자동 301 이 `http://<Host>:8081/…` 로 가지 않게 — Tunnel(030) 뒤에서 닿지 않는다."""
    assert _args(_admin_server(), "absolute_redirect") == [["off"]]


def test_admin_upstreams_are_api_and_collect_host_only() -> None:
    """다른 이름을 못 풀면 nginx 기동이 실패해 공개 사이트까지 내려간다 (§3.1). URI 없는 proxy_pass 하나 모양."""
    for key, children in _proxied().items():
        assert _args(children, "proxy_pass") in ([[API]], [[COLLECTOR]]), key
    text = _text(ADMIN_CONF)
    assert set(re.findall(r"\$\{(\w+)\}", text)) == {"COLLECT_HOST"}
    assert set(re.findall(r"proxy_pass\s+(\S+);", text)) == {API, COLLECTOR}


def test_admin_routes_every_api_path_like_before_the_allowlist() -> None:
    """028 이전의 전체 분기 — api 로 history 무거운 셋·spreads·landing·ws, 나머지는 전부 수집기 (§3.1)."""
    expected = {
        "/api/docs": (COLLECTOR, "/docs"),
        "/api/redoc": (COLLECTOR, "/redoc"),
        "/api/openapi.json": (COLLECTOR, "/openapi.json"),
        "/api/health": (COLLECTOR, "/health"),
        "/api/health/collect": (COLLECTOR, "/health/collect"),
        "/api/premium": (COLLECTOR, "/premium"),
        "/api/orderbook/upbit": (COLLECTOR, "/orderbook/upbit"),
        "/api/refresh": (COLLECTOR, "/refresh"),
        "/api/history/events": (COLLECTOR, "/history/events"),
        "/api/history/premium": (API, "/history/premium"),
        "/api/history/streaks": (API, "/history/streaks"),
        "/api/history/streaks/bulk": (API, "/history/streaks/bulk"),
        "/api/history/candles": (API, "/history/candles"),
        "/api/spreads": (API, "/spreads"),
        "/api/landing": (API, "/landing"),
        "/api/ws/spreads": (API, "/ws/spreads"),
        "/svc/api/health": (API, "/health"),
        "/svc/api/admin/status": (API, "/admin/status"),
        "/api/admin/aws": (COLLECTOR, "/admin/aws"),
        "/api/admin/alerts": (COLLECTOR, "/admin/alerts"),
        "/svc/api/admin/access": (API, "/admin/access"),
        "/svc/api/admin/clarity": (API, "/admin/clarity"),
        "/svc/api/admin/attention": (API, "/admin/attention"),
    }
    for path, target in expected.items():
        assert _forward(path) == target, path
    locations = _locations(_admin_server())
    assert not [k for k in locations if k[0] in ("~", "~*")]
    # 화면은 공개 root 밖의 정적 파일 — 백엔드로 넘기지 않는다
    screen = locations[_admin_route("/")]
    assert not _args(screen, "proxy_pass")
    assert _args(screen, "root") == [["/usr/share/nginx/admin"]]
    # 읽기 제한은 기본 60초 그대로
    assert "proxy_read_timeout" not in _text(ADMIN_CONF)


def test_monitoring_feeds_are_exact_collector_locations_that_inherit_server_headers() -> (
    None
):
    """034 §3.1 — 정확 일치 둘, 첫 줄 교차 사이트 검사, 기록 끔, 자기 헤더 없이 server 수준을 상속."""
    locations = _locations(_admin_server())
    for key in FEEDS:
        children = locations[key]
        assert children[0] == SFS_CHECK, key
        assert _args(children, "access_log") == [["off"]], key
        assert _args(children, "proxy_pass") == [[COLLECTOR]], key
        assert not _args(children, "proxy_set_header"), key
        assert not _args(children, "add_header"), key
        assert not _args(children, "proxy_hide_header"), key


def test_api_feeds_are_exact_api_locations_that_inherit_server_headers() -> None:
    """035 §3.1 — 정확 일치 둘이 api 의 경로로(통째로 바꿈), 첫 줄 교차 사이트 검사, 기록 끔, 자기 헤더 없음."""
    locations = _locations(_admin_server())
    for key in API_FEEDS:
        children = locations[key]
        assert children[0] == SFS_CHECK, key
        assert _args(children, "access_log") == [["off"]], key
        assert _args(children, "proxy_pass") == [[API]], key
        assert _args(children, "rewrite") == [
            ["^", key[1].removeprefix("/svc/api"), "break"]
        ], key
        assert not _args(children, "proxy_set_header"), key
        assert not _args(children, "add_header"), key
        assert not _args(children, "proxy_hide_header"), key


def test_public_server_has_no_svc_branch_so_api_feeds_are_static_404() -> None:
    """공개 nginx(028)는 그대로 — `/svc/` 위치가 없어 두 경로는 `location /` 의 정적 파일 찾기(없으면 404)다."""
    public = _locations(_public_server())
    assert not [k for k in public if k[-1].startswith("/svc")]
    for path in (
        "/svc/api/admin/access",
        "/svc/api/admin/clarity",
        "/svc/api/admin/attention",
    ):
        assert _route(path) == ("/",), path
        assert not _args(public[("/",)], "proxy_pass")


def test_admin_ws_location_upgrades_and_repeats_the_base_headers() -> None:
    for key, children in _proxied().items():
        headers = _args(children, "proxy_set_header")
        upgrades = key == ("/api/ws/",)
        assert (["Upgrade", "$http_upgrade"] in headers) == upgrades, key
        assert (_args(children, "proxy_http_version") == [["1.1"]]) == upgrades, key


# --- 보호 규칙 (§3.2) ----------------------------------------------------------------------


def test_backend_paths_block_cross_site_except_screen_and_docs() -> None:
    """same-origin·none·빈 값만 통과 — 검사는 location 첫 줄, 예외는 화면·/api/docs·/api/redoc."""
    maps = [c for args, c in _admin_tree() if args[:1] == ["map"]]
    assert [args for args, _ in _admin_tree() if args[:1] == ["map"]] == [
        ["map", "$http_sec_fetch_site", "$admin_sfs_ok"]
    ]
    assert maps[0] == [
        (["default", "0"], None),
        (["", "1"], None),
        (["same-origin", "1"], None),
        (["none", "1"], None),
    ]
    for key, children in _proxied().items():
        assert (children[0] == SFS_CHECK) == (key not in SFS_EXEMPT), key
    assert SFS_CHECK not in _locations(_admin_server())[("/",)]


def test_forbidden_is_app_shaped_json_with_frame_deny() -> None:
    server = _admin_server()
    assert _args(server, "error_page") == [["403", "@forbidden"]]
    named = _locations(server)[("@forbidden",)]
    assert [c for args, c in named if args[0] == "types"] == [[]]
    assert _args(named, "default_type") == [["application/json"]]
    assert _args(named, "return") == [["403", FORBIDDEN]]
    assert json.loads(FORBIDDEN)["error"]["code"] == "forbidden"


def test_every_proxy_clears_cookie_and_access_jwt_and_hides_cors() -> None:
    """Cookie·Cf-Access-Jwt-Assertion 은 비우고 X-Refresh-Token 은 그대로, ACAO 는 응답에서 지운다 (§3.2)."""
    for key, children in _proxied().items():
        headers = _effective(children, "proxy_set_header")
        for header in BASE_HEADERS:
            assert header in headers, (key, header)
        assert not [h for h in headers if h[0].lower() == "x-refresh-token"], key
        hidden = _effective(children, "proxy_hide_header")
        assert ["Access-Control-Allow-Origin"] in hidden, key


# 053 §3.7 — 공개 페이지는 자기 출처와 관리자 화면의 덮어 보기 틀 안에서만. 랜딩·대시보드(index.html 을 주는 location)는
# 이 지시어 하나짜리 CSP, 세 정적 페이지는 052 문자열 끝의 frame-ancestors 만 이 값이다
FRAME_ANCESTORS = "frame-ancestors 'self' https://admin.kimptrack.com"
FRAMED_ONLY = {("=", "/"), ("=", "/index.html"), ("=", "/app/index.html"), ("/app/",)}
FRAMED_STATIC = {("=", "/privacy"), ("=", "/kimp-chart"), ("=", "/kimp-history")}


def test_public_pages_can_be_framed_only_by_the_admin_overlay() -> None:
    public = _locations(_public_server())
    for key in FRAMED_ONLY | FRAMED_STATIC:
        csps = [
            h
            for h in _args(public[key], "add_header")
            if h[0] == "Content-Security-Policy"
        ]
        assert len(csps) == 1, key
        _, value, always = csps[0]
        assert always == "always", key
        found = [d.strip() for d in value.split(";") if "frame-ancestors" in d]
        assert found == [FRAME_ANCESTORS], key
        if key in FRAMED_ONLY:
            assert value == FRAME_ANCESTORS, key
    # 대시보드 주소(/app/·?tab=)는 index.html 을 주는 location 으로 간다
    assert _route("/app/") == ("/app/",)
    # X-Frame-Options 는 두지 않는다 — CSP 와 다르면 브라우저가 관리자 틀을 막는다(공개 nginx·caddy 어디에도)
    for key, children in public.items():
        names = [h[0].lower() for h in _args(children, "add_header")]
        assert "x-frame-options" not in names, key
    assert "x-frame-options" not in [
        h[0].lower() for h in _args(_public_server(), "add_header")
    ]
    caddy = re.sub(r"#[^\n]*", "", _text("caddy/Caddyfile"))
    assert "x-frame-options" not in caddy.lower()


def test_frames_denied_on_every_response_and_csp_on_the_screen() -> None:
    server = _admin_server()
    assert XFO in _args(server, "add_header")
    for key, children in _locations(server).items():
        assert XFO in _effective(children, "add_header"), key
    screen = _args(_locations(server)[("/",)], "add_header")
    csp = [
        "Content-Security-Policy",
        "default-src 'self'; frame-src https://kimptrack.com; frame-ancestors 'none'",
        "always",
    ]
    assert csp in screen
    # 화면 스크립트가 API 계약을 따른다 — 공개 index.html 과 같은 이유로 캐시하지 않는다
    assert ["Cache-Control", "no-store", "always"] in screen
    csp_elsewhere = [
        k
        for k, c in _locations(server).items()
        if k != ("/",) and any(h[0] == csp[0] for h in _args(c, "add_header"))
    ]
    assert not csp_elsewhere


def test_access_log_is_one_json_line_per_request_without_polling() -> None:
    """`time email ip method uri status rt ray sfs` 한 줄 — 컨테이너 /var/log/nginx-admin, 폴링 넷은 끔 (§3.2)."""
    (fmt,) = [args[1:] for args, _ in _admin_tree() if args[:1] == ["log_format"]]
    name, escape, *parts = fmt
    assert (name, escape) == ("admin_json", "escape=json")
    # nginx 변수를 숫자·문자 자리표시로 바꿔 JSON 으로 읽는다 — 키 순서와 헤더 출처를 본다
    template = "".join(parts)
    sample = json.loads(re.sub(r"\$(status|request_time)\b", "0", template))
    assert list(sample) == [
        "time",
        "email",
        "ip",
        "method",
        "uri",
        "status",
        "rt",
        "ray",
        "sfs",
    ]
    assert sample["email"] == "$http_cf_access_authenticated_user_email"
    assert sample["ip"] == "$http_cf_connecting_ip"
    assert sample["uri"] == "$request_uri"
    assert sample["ray"] == "$http_cf_ray"
    assert sample["sfs"] == "$http_sec_fetch_site"
    server = _admin_server()
    assert _args(server, "access_log") == [
        ["/var/log/nginx-admin/access.log", "admin_json"]
    ]
    off = {
        k for k, c in _locations(server).items() if _args(c, "access_log") == [["off"]]
    }
    assert off == POLLING | {FAVICON}


def test_favicon_is_an_empty_204_that_inherits_server_headers() -> None:
    """브라우저의 아이콘 요청이 화면 root 의 404·error 로그 줄이 되지 않게 — 본문 없는 204, 기록 끔, 자기 헤더 없음."""
    locations = _locations(_admin_server())
    assert _admin_route("/favicon.ico") == FAVICON
    assert locations[FAVICON] == [
        (["access_log", "off"], None),
        (["return", "204"], None),
    ]
    assert XFO in _effective(locations[FAVICON], "add_header")


# --- web 이미지·compose·caddy (§3.1·§3.2·§3.5) ------------------------------------------


def test_web_image_ships_the_admin_template_and_log_dir() -> None:
    dockerfile = _text("web/Dockerfile")
    lines = dockerfile.splitlines()
    assert "COPY nginx-admin.conf /etc/nginx/templates/admin.conf.template" in lines
    assert "RUN mkdir -p /var/log/nginx-admin" in lines
    # /var/log/nginx 는 이미지의 stdout·stderr 링크 — 바인드도 새 디렉터리도 거기 두지 않는다
    web = _yaml("docker-compose.yml")["services"]["web"]
    assert web["volumes"] == ["./logs/admin:/var/log/nginx-admin"]
    assert "logs/" in _text(".gitignore").splitlines()


def test_no_service_publishes_8081_and_caddy_never_calls_admin() -> None:
    for name, svc in _yaml("docker-compose.yml")["services"].items():
        assert not any("8081" in str(p) for p in svc.get("ports", [])), name
        assert "expose" not in svc, name
    caddyfile = _text("caddy/Caddyfile")
    assert "8081" not in caddyfile and "admin" not in caddyfile.lower()
    # EXPOSE 도 80 만 — `docker run -P` 가 8081 을 게시하지 않게
    exposes = [
        ln for ln in _text("web/Dockerfile").splitlines() if ln.startswith("EXPOSE")
    ]
    assert exposes == ["EXPOSE 80"]


def test_admin_aws_region_only_on_the_collector_service() -> None:
    """034 §3.2 — compose 가 server 에만 리전을 준다(server/.env 에 두지 않는다 — 로컬·api 는 AWS 를 안 부른다)."""
    services = _yaml("docker-compose.yml")["services"]
    assert services["server"]["environment"]["ADMIN_AWS_REGION"] == "ap-northeast-2"
    for name, svc in services.items():
        if name != "server":
            assert "ADMIN_AWS_REGION" not in (svc.get("environment") or {}), name
    assert "ADMIN_AWS_REGION" not in _text("server/.env.example")


def test_api_reads_caddy_logs_read_only_and_only_api_gets_the_dir() -> None:
    """035 §3.2 — api 에 caddy 로그 읽기 전용 바인드·`ACCESS_LOG_DIR`. 다른 서비스엔 없다(caddy 는 027 의 쓰기 바인드 그대로)."""
    services = _yaml("docker-compose.yml")["services"]
    api = services["api"]
    assert api["volumes"] == ["./logs/caddy:/var/log/caddy:ro"]
    assert api["environment"]["ACCESS_LOG_DIR"] == "/var/log/caddy"
    for name, svc in services.items():
        if name != "api":
            assert "ACCESS_LOG_DIR" not in (svc.get("environment") or {}), name
        if name not in ("api", "caddy"):
            assert not any("logs/caddy" in str(v) for v in svc.get("volumes", [])), name
    assert "./logs/caddy:/var/log/caddy" in services["caddy"]["volumes"]
    example = _text("server/.env.example")
    assert "ACCESS_LOG_DIR" not in example
    # 토큰은 사람이 serve 의 server/.env 에 넣는다 — 예시에는 주석 처리한 빈 키만
    assert [ln for ln in example.splitlines() if "CLARITY_API_TOKEN" in ln] == [
        "# CLARITY_API_TOKEN="
    ]


def test_root_path_only_on_collector_and_public_api_docs_stay_closed() -> None:
    """수집기만 `/api` 아래에 문서를 연다 — 그러면 공개 /api/ 는 백엔드로 넘기지 않아야 한다(028 가드, §3.5)."""
    services = _yaml("docker-compose.yml")["services"]
    assert services["server"]["environment"]["UVICORN_ROOT_PATH"] == "/api"
    for name, svc in services.items():
        if name != "server":
            assert "UVICORN_ROOT_PATH" not in (svc.get("environment") or {}), name
    if services["server"]["environment"].get("UVICORN_ROOT_PATH"):
        public = _locations(_public_server())
        assert not _args(public[("/api/",)], "proxy_pass")
        for path in ("/api/docs", "/api/redoc", "/api/openapi.json", "/api/refresh"):
            assert _route(path) in DENY, path
    assert (ROOT / ADMIN_CONF).is_file()


# --- 관리자 화면 정적 단언 (029 §3.3 → 036 §3.8·§4) -------------------------------------------

SCREEN = ROOT / "web/admin"
# 036 §3.1 — 한 페이지 절 일곱, 이 순서
SECTIONS = ["overview", "collect", "infra", "alerts", "traffic", "cost", "tools"]
# 036 §3.8 — 밖으로 나가는 링크의 호스트(고정 https 주소뿐). db-ip.com 은 DB-IP CC BY 표시(042 §3.4 ③ 바닥)
EXTERNAL_HOSTS = {
    "clarity.microsoft.com",
    "dash.cloudflare.com",
    "one.dash.cloudflare.com",
    "github.com",
    "db-ip.com",
}
# 036 §4 — 화면 스크립트에 없어야 하는 것: 브라우저 저장소·HTML 해석·코드 실행·새 창·주소 읽기·style 속성·링크 쓰기·
# fetch 밖의 요청 길(헤더를 붙이는 한 함수를 우회한다)
SCRIPT_BANNED = (
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "document.cookie",
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
    "window.open",
    "location.pathname",
    "location.href",
    ".style",
    "setAttribute('style'",
    ".href",
    "setAttribute('href'",
    "setAttribute('src'",
    ".src",
    "setAttributeNS",
    "xlink:href",
    "new XMLHttpRequest",
    "sendBeacon",
    "new WebSocket",
    "EventSource",
    # 042 §4 — 서버 기록 창을 주소에 싣지 않는다(주소를 바꾸는 다른 길도)
    "location.hash",
    "pushState",
    "location.assign",
    "location.search =",
    "location =",
)
# 036 §3.6·§3.8 — svg() 가 받는 속성 이름은 기하·이름표뿐(href·style·on… 은 svg() 가 던진다)
SVG_ATTRS = {
    "viewBox",
    "preserveAspectRatio",
    "role",
    "aria-label",
    "x",
    "y",
    "width",
    "height",
    "x1",
    "x2",
    "y1",
    "y2",
    "d",
    "points",
}
# theme.css 와 같은 값이어야 하는 토큰 (036 §3.7)
TOKENS = ("--color-bg", "--color-surface", "--color-ok", "--color-warn", "--color-up")


def _root_tokens(css: str) -> dict[str, str]:
    """첫 `:root { … }` 안의 `--이름: 값;` — 주석은 지운다."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    block = re.search(r":root\s*\{(.*?)\}", css, flags=re.S)
    assert block, ":root 블록"
    return {
        k: v.strip()
        for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block.group(1))
    }


def _object_keys(literal: str) -> list[str]:
    """JS 객체 글자 `{ … }` 안의 키 — 맨 위 수준의 쉼표로 나누고, 콜론 앞(없으면 줄임 표기 그 이름)."""
    pieces, depth, cur = [], 0, ""
    for ch in literal:
        depth += ch in "([{"
        depth -= ch in ")]}"
        if ch == "," and depth == 0:
            pieces.append(cur)
            cur = ""
        else:
            cur += ch
    pieces.append(cur)
    return [p.split(":", 1)[0].strip().strip("'\"") for p in pieces if p.strip()]


def _anchors(html: str) -> list[dict[str, str]]:
    """`<a …>` 의 속성 — 큰따옴표·작은따옴표·따옴표 없는 값 모두. href 를 못 읽는 a 는 실패시킨다."""
    out = []
    for attrs in re.findall(r"<a\b([^>]*)>", html):
        pairs = re.findall(
            r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", attrs
        )
        found = {k.lower(): a or b or c for k, a, b, c in pairs}
        assert "href" in found, f"href 를 못 읽는 링크: <a{attrs}>"
        out.append(found)
    return out


def test_screen_is_three_static_files_outside_the_public_root() -> None:
    assert sorted(p.name for p in SCREEN.iterdir()) == [
        "admin.css",
        "admin.js",
        "index.html",
    ]
    lines = _text("web/Dockerfile").splitlines()
    assert "COPY admin /usr/share/nginx/admin" in lines
    # 공개 root(dist ← public/)에 섞이지 않는다 — 공개 location / 로 받아 갈 수 없다
    public = {p.name for p in (ROOT / "web/public").rglob("*")}
    assert not {"admin", "admin.js", "admin.css"} & public


def test_screen_script_sends_xhr_header_polls_while_visible_and_never_parses_html() -> (
    None
):
    js = _text("web/admin/admin.js")
    assert "'X-Requested-With': 'XMLHttpRequest'" in js
    for needed in ("visibilityState", "createElementNS", "refreshSec"):
        assert needed in js, needed
    for banned in SCRIPT_BANNED:
        assert banned not in js, banned
    # SVG 속성은 허용 목록 하나를 지난다 — 목록이 기하·이름표 밖으로 늘지 않게
    allow = re.search(r"const SVG_ATTRS = new Set\(\[([^\]]*)\]\);", js)
    assert allow, "svg() 의 속성 허용 목록"
    assert set(re.findall(r"'([\w-]+)'", allow.group(1))) == SVG_ATTRS
    assert "if (!SVG_ATTRS.has(k)) throw" in js
    assert js.count(".setAttribute(k,") == 1
    # 만드는 SVG 요소는 그림 요소뿐 — a·image·use·foreignObject(누를 수 있는 링크·외부 자원)가 없다.
    # 글자 그대로 적은 속성 키도 허용 목록 안이다(런타임 검사 앞에서 한 번 더)
    assert set(re.findall(r"\bsvg\('(\w+)'", js)) <= {
        "svg",
        "title",
        "line",
        "rect",
        "path",
        "g",
    }
    for literal in re.findall(r"\bsvg\('\w+',\s*\{([^}]*)\}", js):
        assert set(_object_keys(literal)) <= SVG_ATTRS, literal
    # 모든 요청이 한 함수를 지난다 — 헤더가 빠진 요청이 없게
    assert js.count("fetch(") == 1
    # 새로고침·표시 지우기 주소는 화면 주소 `/` 로 고정 — `//다른호스트/..%2F/` 로 열린 화면에서 현재 경로를 쓰면
    # 프로토콜 상대 URL 이 되어 다른 출처로 간다(열린 리다이렉트)
    assert js.count("location.replace(") == 1
    assert "location.replace(`/?${RELOAD_MARK}=1`)" in js
    assert js.count("history.replaceState(") == 1
    assert "history.replaceState(null, '', '/')" in js


def test_screen_script_has_two_polling_bundles_and_no_outside_address() -> None:
    """036 §3.3 — 빠른 10초·느린 60초, §4 — 파일 안의 주소는 SVG 이름공간 하나뿐."""
    js = _text("web/admin/admin.js")
    assert re.search(r"\bFAST_MS = 10_000;", js)
    assert re.search(r"\bSLOW_MS = 60_000;", js)
    assert re.findall(r"https?://[^\s'\"`]*", js) == ["http://www.w3.org/2000/svg"]
    # 피드 넷과 029 의 네 경로 — 같은 출처 상대 경로
    for path in (
        "/api/health",
        "/svc/api/health",
        "/svc/api/admin/status",
        "/api/health/collect",
        "/api/admin/aws",
        "/api/admin/alerts",
        "/svc/api/admin/access",
        "/svc/api/admin/clarity",
    ):
        assert f"'{path}'" in js, path


def test_screen_script_never_copies_the_clarity_periods() -> None:
    """040 §4 — Clarity 주기(035 의 3시간·040 의 4시간·12시간)는 서버의 `refreshSec` 로만 적는다(036 §3.3)."""
    js = _text("web/admin/admin.js")
    for number in ("10800", "10_800", "14400", "14_400", "43200", "43_200"):
        assert number not in js, number


def test_screen_chart_empty_words_differ_from_the_alarm_state_name() -> None:
    """036 §3.5 — 점 2개 미만 차트는 "값 없음"·"값 1개뿐", "데이터 부족" 은 경보 INSUFFICIENT_DATA 만."""
    js = _text("web/admin/admin.js")
    assert "s.count ? '값 1개뿐' : '값 없음'" in js
    assert "el('p', 'empty', '데이터 부족')" not in js
    assert "INSUFFICIENT_DATA: ['dim', '데이터 부족']" in js


def _js_function(js: str, name: str) -> str:
    """`function 이름(…) {` 부터 맨 앞 칸의 `}` 까지 — 파일의 함수는 맨 위 수준에만 있다."""
    found = re.search(rf"\nfunction {name}\([^)]*\) \{{\n(.*?)\n\}}\n", js, flags=re.S)
    assert found, name
    return found.group(1)


def test_screen_page_line_lists_share_is_over_human_pages() -> None:
    """042 §4 분모 — 경로(⑤)·외부 출처·utm(③)의 % 는 사람 브라우저 모양 페이지 줄 `totals.humanPages` 로 나눈다(038 §3.6 —
    상위 목록은 그 줄만 센다). 답 문장 ⑤ 의 경로 % 도 같은 분모다."""
    js = _text("web/admin/admin.js")
    for name, keys in (("q3", ("referrers", "utmSources")), ("q5", ("paths",))):
        body = _js_function(js, name)
        assert "const hp = n0(f.T.humanPages);" in body, name
        for key in keys:
            found = re.findall(
                rf"shareList\('[\w-]+', rows2\(f\.a\.{key}\), .*?, hp\)", body
            )
            assert len(found) == 1, (name, key)
        assert "totals.pages" not in body and "T.pages" not in body, name
    answer = _js_function(js, "answer5")
    assert "const hp = n0(f.T.humanPages);" in answer
    assert "pct(paths[0][1], hp)" in answer


def test_screen_body_elapsed_words_are_retold_after_every_paint() -> None:
    """036 §3.3 — 본문 안 경과 글자는 시각을 data-at 에 둔 span 이고, 그리기 끝에 글자만 고친다(본문은 그대로)."""
    js = _text("web/admin/admin.js")
    assert "span.dataset.at = String(ms)" in _js_function(js, "agoSpan")
    retick = _js_function(js, "retick")
    assert "document.querySelectorAll('span[data-at]')" in retick
    # 글자가 바뀔 때만 쓴다 — 같은 글자를 다시 쓰면 텍스트 노드가 바뀌어 글자 선택이 풀린다
    assert "const text = ago(Number(span.dataset.at));" in retick
    assert "if (span.textContent !== text) span.textContent = text;" in retick
    assert retick.count("span.textContent =") == 1
    assert _js_function(js, "paint").rstrip().endswith("retick();")
    # 값이 바뀔 때만 다시 그리는 본문의 경과는 모두 그 span 으로
    for name in ("alarmRow", "fillCanary"):
        body = _js_function(js, name)
        assert "agoSpan(" in body and "ago(" not in body.replace("agoSpan(", ""), name


def test_screen_page_has_no_inline_script_or_style() -> None:
    html = _text("web/admin/index.html")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S)
    assert scripts == [(' src="admin.js" defer', "")]
    assert "<style" not in html and "style=" not in html
    assert re.search(r'<input id="token" type="password" autocomplete="off"', html)
    assert "<form" not in html  # 제출 없음 — 토큰이 URL·기록으로 새지 않게
    for href in ("/api/docs", "/api/redoc", "/cdn-cgi/access/logout"):
        assert f'href="{href}"' in html, href
    assert not re.search(r"\btarget\s*=", html, flags=re.I)  # 같은 탭 이동


def test_screen_page_has_seven_sections_in_order_and_jump_links() -> None:
    html = _text("web/admin/index.html")
    assert re.findall(r'<section id="([\w-]+)"', html) == SECTIONS
    nav = re.search(r'<nav class="jump"[^>]*>(.*?)</nav>', html, flags=re.S)
    assert nav, "머리의 절 이동"
    assert re.findall(r'href="#([\w-]+)"', nav.group(1)) == SECTIONS


def test_screen_outside_links_are_fixed_https_with_noreferrer_and_no_ids() -> None:
    """036 §3.8 — 밖으로 나가는 링크는 고정 https 주소·noreferrer, 계정·팀·프로젝트 ID·이메일·토큰 없음(레포 공개)."""
    html = _text("web/admin/index.html")
    # `//호스트` 는 프로토콜 상대 주소 — 밖으로 나가는 링크로 본다(그리고 https:// 가 아니라 실패한다)
    outside = [
        a
        for a in _anchors(html)
        if a["href"].startswith("//") or not a["href"].startswith(("/", "#"))
    ]
    assert outside, "도구 절의 콘솔 링크"
    for a in outside:
        href = a["href"]
        assert href.startswith("https://"), href
        assert a.get("rel") == "noreferrer", href
        host = href.split("/")[2]
        assert host in EXTERNAL_HOSTS or host.endswith(".console.aws.amazon.com"), href
        if "cloudflare.com" in host:
            assert href == f"https://{host}/", (
                href
            )  # 대시보드 주소에는 계정 ID 가 든다 — 루트만
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", html)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", html)
    assert not re.search(r"[0-9a-f]{32,}", html)
    assert "cloudflareaccess.com" not in html
    assert "/projects/view/" not in html


def test_screen_styles_copy_theme_tokens_without_outside_resources() -> None:
    css = _text("web/admin/admin.css")
    assert "@import" not in css and "url(" not in css
    ours = _root_tokens(css)
    theme = _root_tokens(_text("docs/design/theme.css"))
    for token in TOKENS:
        assert ours[token] == theme[token], token
    assert "@media (max-width: 640px)" in css
    # 한국어는 낱말 단위로 줄을 바꾸고, 칸보다 긴 낱말만 넘칠 때 끊는다 (§3.7)
    body = re.search(r"\nbody \{([^}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    assert body, "body 규칙"
    assert "word-break: keep-all;" in body.group(1)
    assert "overflow-wrap: break-word;" in body.group(1)


# --- 설명·이름표·문구 고침 (041 §4) ----------------------------------------------------------

EXPLAIN = '<details class="explain">'
TERMS = '<details class="terms">'
SCREEN_FILES = ("admin.css", "admin.js", "index.html")
# 041 §3.4 — 실패 종류(011 유형 칩)·경보 꼬리(027 경보 이름)·대시보드 탭(002) 이름표
KIND_NAMES = {
    "timeout": "타임아웃",
    "network": "연결 실패",
    "rate_limit": "rate limit",
    "banned": "차단",
    "unavailable": "거래소 오류",
    "bad_request": "요청 오류",
    "bad_response": "응답 오류",
    "stale_stream": "스트림 정체",
}
ALARM_TAIL_NAMES = {
    "status-instance": "인스턴스 상태검사",
    "status-system": "시스템 상태검사",
    "credit-balance": "CPU 크레딧 잔고",
    "credit-surplus": "잉여 크레딧 과금",
    "memory": "메모리",
    "disk": "디스크",
    "canary": "바깥 점검",
    "http-5xx": "사이트 5xx",
}
# 041 §3.4 경보 꼬리의 울리는 조건(계약 복사 — 끝 마침표·백틱만 뺐다)
ALARM_TAIL_CONDITIONS = {
    "status-instance": "60초 3점 연속 실패면 AWS 가 재부팅",
    "status-system": "60초 2점 연속 실패면 AWS 가 복구(recover)",
    "credit-balance": "5분 3점 연속 최대 적립의 30%(data 173·serve 86) 미만",
    "credit-surplus": "5분 1점 0 초과(unlimited 과금 시작)",
    "memory": "가용률 10% 미만 5분 연속(collect 는 5분 1점), 데이터 없음도 울린다",
    "disk": "사용률 80% 초과 5분 1점, 데이터 없음도 울린다",
    "canary": "Lambda 실패가 5분 2점 연속(10분), 데이터 없음도 울린다",
    "http-5xx": "5분 합 10 이상(/api/ws/spreads 는 세지 않는다), 데이터 없음은 정상",
}
TAB_NAMES = {
    "spread": "실시간 스프레드",
    "history": "기록/통계",
    "gap": "선물–현물 갭",
    "pp": "선선갭",
    "health": "수집 상태",
    "flow": "입출금 레이더",
}


def _section_bodies(html: str) -> dict[str, str]:
    return dict(
        re.findall(r'<section id="([\w-]+)"[^>]*>(.*?)</section>', html, flags=re.S)
    )


def _folded(html: str, opening: str) -> list[str]:
    """`opening` 으로 여는 설명 details 의 안 — 설명 안에는 details 가 없다."""
    return re.findall(re.escape(opening) + r"(.*?)</details>", html, flags=re.S)


def _plain(fragment: str) -> str:
    return re.sub(r"<[^>]+>", "", fragment).strip()


def _dl(block: str) -> tuple[list[str], list[str]]:
    """설명 details 하나 → dl 하나의 (dt 글자들, dd 글자들)."""
    assert block.count("<dl") == 1, block[:80]
    (dl,) = re.findall(r"<dl>(.*?)</dl>", block, flags=re.S)
    dts = [_plain(x) for x in re.findall(r"<dt>(.*?)</dt>", dl, flags=re.S)]
    dds = [_plain(x) for x in re.findall(r"<dd>(.*?)</dd>", dl, flags=re.S)]
    return dts, dds


def _summary(block: str) -> str:
    found = re.search(r"<summary>(.*?)</summary>", block, flags=re.S)
    assert found, block[:80]
    return _plain(found.group(1))


def test_every_section_has_one_folded_explain_right_under_its_head() -> None:
    """041 §3.1 — 절 일곱마다 접힌 '이 절 읽는 법' 하나, 머리 줄 바로 다음(개요는 칸 여섯 다음이고 절의 끝)."""
    html = _text("web/admin/index.html")
    # 설명 details 는 글자 그대로 — open 같은 다른 속성이 없다
    for tag in re.findall(r"<details\b[^>]*>", html):
        if "explain" in tag or "terms" in tag:
            assert tag in (EXPLAIN, TERMS), tag
    bodies = _section_bodies(html)
    assert list(bodies) == SECTIONS
    no_div = r"(?:(?!</div>).)*</div>\s*"
    for sid, body in bodies.items():
        assert body.count(EXPLAIN) == 1, sid
        if sid == "overview":
            tail = re.escape(EXPLAIN) + r"(?:(?!</details>).)*</details>\s*$"
            assert re.search(r'<div class="vitals">' + no_div + tail, body, flags=re.S)
        else:
            head = r'<div class="sec-head">' + no_div + re.escape(EXPLAIN)
            assert re.search(head, body, flags=re.S), sid
        (block,) = _folded(body, EXPLAIN)
        assert _summary(block).startswith("이 절 읽는 법"), sid
        dts, dds = _dl(block)
        assert len(dts) == len(dds) >= 3, sid
        assert dts[:2] == ["읽는 값", "판정"], sid


def test_block_terms_are_folded_lists_in_collect_infra_traffic_and_cost() -> None:
    """041 §3.1 — 덩어리 끝 '이 칸 뜻' 은 dl 하나·dt 수 = dd 수 ≥ 2. 수는 고정하지 않는다(042·043 이 바꾼다)."""
    html = _text("web/admin/index.html")
    blocks = _folded(html, TERMS)
    assert blocks
    for block in blocks:
        assert _summary(block) == "이 칸 뜻"
        dts, dds = _dl(block)
        assert len(dts) == len(dds) >= 2, dts
    with_terms = {sid for sid, body in _section_bodies(html).items() if TERMS in body}
    assert {"collect", "infra", "traffic", "cost"} <= with_terms


class _Ancestors(HTMLParser):
    """설명 details 마다 조상 가운데 id 를 가진 요소의 id — 빈 요소(input·meta 등)는 쌓지 않는다."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str | None]] = []
        self.found: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "details" and found.get("class") in ("explain", "terms"):
            self.found.append([i for _, i in self.stack if i])
        if tag not in self.VOID:
            self.stack.append((tag, found.get("id")))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def test_explanations_sit_outside_every_redrawn_box() -> None:
    """041 §3.1 — admin.js 가 내용을 바꾸는 칸은 모두 id 를 가진다. 설명의 id 있는 조상은 그 절과 `infra-parts`
    (admin.js 가 hidden 만 바꾼다)뿐이라 다시 그리기가 닿지 않는다. admin.js 는 설명을 만들지도 건드리지도 않는다."""
    html = _text("web/admin/index.html")
    parser = _Ancestors()
    parser.feed(html)
    parser.close()
    assert parser.stack == []
    assert len(parser.found) == html.count(EXPLAIN) + html.count(TERMS)
    for ids in parser.found:
        assert ids[0] in SECTIONS and set(ids[1:]) <= {"infra-parts"}, ids
    js = _text("web/admin/admin.js")
    assert "explain" not in js and "terms" not in js


def _js_table(js: str, name: str) -> dict[str, list[str]]:
    """admin.js 의 `const 이름 = { 키: '글', … };`(값은 글 하나 또는 글 둘의 배열) → 키 → 글 목록."""
    block = re.search(rf"\nconst {name} = \{{\n(.*?)\n\}};\n", js, flags=re.S)
    assert block, name
    table: dict[str, list[str]] = {}
    for line in block.group(1).splitlines():
        row = re.fullmatch(
            r"  '?([\w-]+)'?: (?:'([^']*)'|\['([^']*)', '([^']*)'\]),", line
        )
        assert row, line
        table[row[1]] = [v for v in row.groups()[1:] if v is not None]
    return table


def test_name_tables_match_the_spec_and_every_key_is_explained() -> None:
    """041 §3.4 — 이름표 표 셋의 키·한국어 이름, 그리고 키 스물둘이 모두 설명 dl 안에 원래 id 로 있다."""
    js = _text("web/admin/admin.js")
    kinds = _js_table(js, "KIND_NAME")
    assert {k: v[0] for k, v in kinds.items()} == KIND_NAMES
    tails = _js_table(js, "ALARM_TAIL")
    assert {k: v[0] for k, v in tails.items()} == ALARM_TAIL_NAMES
    assert {k: v[1] for k, v in tails.items()} == ALARM_TAIL_CONDITIONS
    tabs = _js_table(js, "TAB_NAME")
    assert {k: v[0] for k, v in tabs.items()} == TAB_NAMES
    html = _text("web/admin/index.html")
    folded = "".join(_folded(html, EXPLAIN) + _folded(html, TERMS))
    explained = "".join(re.findall(r"<dl>(.*?)</dl>", folded, flags=re.S))
    for key in [*KIND_NAMES, *ALARM_TAIL_NAMES, *TAB_NAMES]:
        assert f"<code>{key}</code>" in explained, key


def test_misleading_words_are_fixed() -> None:
    """041 §3.5 — 억제 문구·성공률 소수 1자리·버전 칸 지움·AWS 계정 종료 문구 지움(동작은 같다)."""
    js = _text("web/admin/admin.js")
    assert "10분 억제로 보내지 않은 알림은 기록에도 없다" in js
    assert "억제된 알림은 없다" not in js
    assert "successRate1h.toFixed(2)" not in js
    assert "'버전'" not in js and not re.search(r"\.version\b", js)
    assert "\n  aws: 'AWS 자격 없음',\n" in js
    for name in SCREEN_FILES:
        text = _text(f"web/admin/{name}")
        for word in ("계정 종료", "AWS 종료", "종료 예정", "방문자-일", "visitor-day"):
            assert word not in text, (name, word)
    notes = re.search(
        r'<ul class="notes">(.*?)</ul>', _text("web/admin/index.html"), flags=re.S
    )
    assert notes and notes.group(1).count("<li>") == 1  # SCP 글 하나


def test_overview_tiles_stay_plain_links_and_there_is_no_popover() -> None:
    """041 §2 — 개요 칸(링크) 안에는 span 셋뿐(누르는 요소를 넣지 않는다), 칸 위에 뜨는 풍선 없음."""
    html = _text("web/admin/index.html")
    tiles = re.findall(r'<a class="vital"[^>]*>(.*?)</a>', html, flags=re.S)
    assert len(tiles) == 6
    span = r'<span class="[\w-]+"(?: id="[\w-]+")?>[^<]*</span>'
    for inner in tiles:
        assert re.fullmatch(f"(?:{span}){{3}}", inner), inner
    for name in SCREEN_FILES:
        assert "popover" not in _text(f"web/admin/{name}").lower(), name


def test_explanation_text_has_no_links_media_scripts_or_addresses() -> None:
    """041 §3.2 — 설명 글 안에는 링크·이미지·스크립트·SVG·style·스킴 주소가 없다(12자리 숫자·이메일은 036 단언)."""
    html = _text("web/admin/index.html")
    for block in _folded(html, EXPLAIN) + _folded(html, TERMS):
        for banned in ("<a", "<img", "<script", "<svg", "style=", "://"):
            assert banned not in block, (banned, _summary(block))


# admin.js 를 가짜 document 와 싣고(보이지 않는 탭 — 묶음이 돌지 않아 요청 0) 이름표 찾기·행 글자 만들기만 부른다.
# 그리기 전체·펼침 유지·폭은 설계 세션이 브라우저로 본다(041 §4).
ADMIN_HARNESS = r"""
const [script, kinds, alarms, tabs] = JSON.parse(require('fs').readFileSync(0, 'utf8'))
class Node {
  constructor(tag) { Object.assign(this, { tag, kids: [], dataset: {}, attrs: {}, textContent: '', title: '', className: '', hidden: false }) }
  get classList() { return { add() {}, remove() {} } }
  get lastChild() { return this.kids[this.kids.length - 1] }
  append(...k) { this.kids.push(...k) }
  prepend(...k) { this.kids.unshift(...k) }
  replaceChildren(...k) { this.kids = k }
  setAttribute(k, v) { this.attrs[k] = String(v) }
  addEventListener() {}
}
const byId = new Map()
const calls = []
const document = {
  visibilityState: 'hidden',
  getElementById: (id) => byId.get(id) || byId.set(id, new Node('div')).get(id),
  createElement: (tag) => new Node(tag),
  createElementNS: (ns, tag) => new Node(tag),
  createDocumentFragment: () => new Node('#fragment'),
  querySelectorAll: () => [],
  addEventListener() {},
}
const fetch = (...a) => { calls.push('fetch'); return new Promise(() => {}) }
const location = { search: '', replace() { calls.push('replace') } }
const history = { replaceState() { calls.push('replaceState') } }
const api = new Function('document', 'fetch', 'location', 'history', 'Node',
  script + '\n;return { kindName, alarmTail, tabName, pctFmt, exchangeRow, timeline, alarmRow, alertRow, fillTraffic, fillClarity, drawCollect, got, P }')(
  document, fetch, location, history, Node)
const text = (n) => (typeof n === 'string' ? n : n.textContent + n.kids.map(text).join(''))
const titles = (n) => (typeof n === 'string' ? [] : [
  ...(n.title ? [n.title] : []), ...(n.tag === 'title' ? [n.textContent] : []), ...n.kids.flatMap(titles)])
const show = (n) => ({ text: text(n), titles: titles(n) })
const find = (n, cls) => (typeof n === 'string' ? [] : [...(n.className === cls ? [n] : []), ...n.kids.flatMap((k) => find(k, cls))])
// 타일마다 [이름, 부제…] — 부제가 없으면 이름만
const tiles = (n) => find(n, 'stat').map((t) => [text(t.kids[0]), ...find(t, 'stat-sub').map(text)])
const now = Date.now()
const sec = Math.floor(now / 1000)
const access = { state: 'ok', windows: ['24h'], gateAt: Date.UTC(2026, 9, 10, 15), startTs: sec - 86400, firstTs: sec - 3600, totals: { requests: 100, pages: 40, ws: 2, skipped: 1 },
  hourly: [{ ts: sec - 3600, requests: 60, pages: 25, errors: 1 }, { ts: sec, requests: 40, pages: 15, errors: 0 }],
  status: { '2xx': 80, '3xx': 10, '4xx': 7, '5xx': 1 }, ws: { count: 2, durations: { lt10s: 1, ge1h: 1 } },
  paths: [['/', 30]], tabs: tabs.map((t, i) => [t, i + 1]), referrers: [], utmSources: [['x', 2]], devices: [['desktop', 30]],
  browsers: [['chrome', 20]], recent5xx: [{ ts: sec - 60, path: '/', status: 502 }] }
api.fillTraffic(access)
const clarityBox = new Node('div')
api.fillClarity(clarityBox, { state: 'ok', refreshSec: 10800, nextAt: now + 60000, metrics: [],
  traffic: { sessions: 3, botSessions: 1, users: 2, pagesPerSession: 1.5 } })
// 수집 요약 줄·절 요약 — 성공률 99.8 과 앱 버전(그리지 않는다)
api.got.set(api.P.collect, { text: 'collect', body: { fetchedAt: now, successRate1h: 99.8, serverStartedAt: now - 3600000,
  version: '0.1.0', outages: [], exchanges: [{ exchange: 'upbit', state: 'ok', lastSuccessAt: now, successRate1h: 99.8, markets: 255 }] } })
api.drawCollect()
const exchange = (kind) => ({ exchange: 'upbit', state: 'ok', lastSuccessAt: now, successRate1h: 97.5, markets: 255,
  openOutage: { kind, count: 3, startedAt: now - 120000 }, lastError: { at: now, kind, statusCode: 418, message: 'm' } })
const outages = kinds.map((kind, i) => ({ exchange: 'bybit', kind, startedAt: now - (i + 2) * 60000, endedAt: now - 60000, count: i + 1 }))
process.stdout.write(JSON.stringify({
  kinds: Object.fromEntries(kinds.map((k) => [k, api.kindName(k)])),
  alarms: Object.fromEntries(alarms.map((a) => [a, api.alarmTail(a) ?? null])),
  tabs: Object.fromEntries(tabs.map((t) => [t, api.tabName(t)])),
  rates: [99.8, 97.5, null].map(api.pctFmt),
  rows: kinds.map((k) => show(api.exchangeRow(exchange(k)))),
  timeline: show(api.timeline(outages)),
  alarmRows: alarms.map((name) => show(api.alarmRow({ name, state: 'ALARM', changedAt: now, reason: 'r' }))),
  alertRows: alarms.map((alarm) => show(api.alertRow({ source: 'alarm', alarm, fromState: 'OK', toState: 'ALARM', at: now }))),
  accessTiles: [1, 6].flatMap((q) => tiles(byId.get(`b-q${q}`))),
  tabsBlock: show(byId.get('b-q5')),
  clarityTiles: tiles(clarityBox),
  collect: { sum: text(byId.get('collect-sum')), head: text(byId.get('s-collect')) },
  calls,
}))
"""

# §4 설계 세션 확인 5 의 이름 아홉 — 꼬리 여덟 순서 + 이름표가 없는 foo
ALARM_NAMES = [
    "marketlens-collect-status-instance",
    "marketlens-serve-status-system",
    "marketlens-data-credit-balance",
    "marketlens-data-credit-surplus",
    "marketlens-serve-memory",
    "marketlens-data-disk",
    "marketlens-canary",
    "marketlens-http-5xx",
    "marketlens-foo",
]
# 표에 없는 값 — Object 의 것을 집지 않고 원래 글자로
ODD = ["zzz", "constructor", "__proto__"]
KINDS = [*KIND_NAMES, *ODD]
TABS = [*TAB_NAMES, "(기타)", "constructor"]
# 041 §3.1·§7 타일 부제 — [이름, 부제]. 서버 기록 덩어리의 타일은 042 §3.4 ①·⑥ (시행 전 — windows ["24h"]·시행일 10-11)
ACCESS_TILES = [
    ("확인 ~ 브라우저 모양", "방문자(날마다 셈)"),
    ("다시 온", "시행 뒤부터"),
    ("스크립트가 돈 페이지", "사람 모양 페이지 0 중"),
    ("사람 모양 페이지", "위장 봇 섞임"),
    ("끝난 연결", "연결 수 — 사람 수가 아니다"),
    ("연결한 방문자", "시행(10-11 00:00) 뒤부터"),
    ("재접속 실패", "대시보드 WebSocket 5xx"),
]
CLARITY_TILES = [
    ("세션", "동의한 방문자만"),
    ("봇 세션", "Clarity 가 봇으로 본 세션"),
    ("사용자", "동의한 방문자 · Clarity 기준"),
    ("세션당 페이지", "Clarity 값 그대로"),
]


@pytest.fixture(scope="module")
def admin_js() -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node 가 없다 — CI 러너(ubuntu-latest)에는 있어야 한다")
        pytest.skip("node 가 없어 admin.js 를 돌리지 못한다")
    done = subprocess.run(
        [node, "-e", ADMIN_HARNESS],
        input=json.dumps([_text("web/admin/admin.js"), KINDS, ALARM_NAMES, TABS]),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_labels_fall_back_to_the_raw_text_for_unknown_values(
    admin_js: dict[str, Any],
) -> None:
    """041 §3.4·§3.6 — 표에 없는 값(constructor·__proto__ 포함)은 원래 글자, 성공률은 소수 1자리. 요청 0."""
    assert admin_js["kinds"] == {**KIND_NAMES, **{k: k for k in ODD}}
    assert admin_js["tabs"] == {
        **TAB_NAMES,
        "(기타)": "(기타)",
        "constructor": "constructor",
    }
    alarms = dict(admin_js["alarms"])
    assert alarms.pop("marketlens-foo") is None
    expected = dict(zip(ALARM_NAMES, ALARM_TAIL_NAMES.values(), strict=False))
    assert {name: tail[0] for name, tail in alarms.items()} == expected
    assert admin_js["rates"] == ["99.8%", "97.5%", "–"]
    assert admin_js["calls"] == []


def test_collect_rows_show_kind_labels_and_keep_the_raw_id_in_title(
    admin_js: dict[str, Any],
) -> None:
    """041 §3.4 — 거래소 표(열린 구간·마지막 오류)·타임라인 막대 title·아래 다섯 줄에 이름표, title 은 원래 id."""
    for kind, row in zip(KINDS, admin_js["rows"], strict=True):
        label = admin_js["kinds"][kind]
        assert f"{label} · 3회 · 2분째" in row["text"], kind
        assert f"{label} · HTTP 418 · m" in row["text"], kind
        assert "97.5%" in row["text"]
        assert kind in row["titles"] and f"{kind} — m" in row["titles"], kind
    timeline = admin_js["timeline"]
    for i, kind in enumerate(KINDS):
        label = admin_js["kinds"][kind]
        assert any(t.endswith(f" · {label} · ×{i + 1}") for t in timeline["titles"]), (
            kind
        )
    for i, kind in enumerate(KINDS[:5]):  # 아래 다섯 줄 = 시작이 가장 늦은 다섯
        assert f"{admin_js['kinds'][kind]} · ×{i + 1}" in timeline["text"], kind
        assert kind in timeline["titles"], kind


def test_alarm_rows_show_tail_labels_and_conditions_in_title(
    admin_js: dict[str, Any],
) -> None:
    """041 §3.4 — 경보 표·알림 경보 행의 이름 뒤 꼬리 이름표, 이름의 title 은 '전체 이름 — 조건'. foo 는 이름표 없음."""
    keys = dict(zip(ALARM_NAMES, ALARM_TAIL_NAMES, strict=False))  # foo 는 꼬리 없음
    rows = zip(ALARM_NAMES, admin_js["alarmRows"], admin_js["alertRows"], strict=True)
    for name, row, alert in rows:
        short = name.removeprefix("marketlens-")
        tail = admin_js["alarms"][name]
        if name not in keys:
            assert tail is None
            assert row["titles"] == [name] and alert["titles"] == [name]
            assert not any(
                label in row["text"]
                for label in ALARM_TAIL_NAMES.values()
                if label != "canary"
            )
            assert f"{short} OK → " in alert["text"]
            continue
        label = ALARM_TAIL_NAMES[keys[name]]
        condition = ALARM_TAIL_CONDITIONS[keys[name]]
        assert tail == [label, condition], name
        assert f"{short} {label}" in row["text"], name
        assert row["titles"] == [f"{name} — {condition}"], name
        assert f"{short} {label} OK → " in alert["text"], name
        assert alert["titles"] == [f"{name} — {condition}"], name


def test_tab_table_and_tiles_show_names_and_subtitles(
    admin_js: dict[str, Any],
) -> None:
    """041 §3.4·§3.1 — 접속을 실제로 채우면 ⑤ 대시보드 진입 탭은 한국어 탭 이름(title 은 id, (기타)는 그대로), 서버
    기록 덩어리(042 ①·⑥)와 Clarity 넷 타일은 값 아래 부제 한 줄이다."""
    table = admin_js["tabsBlock"]
    assert "대시보드 진입 탭" in table["text"]
    for tab in TABS:
        assert admin_js["tabs"][tab] in table["text"], tab
        assert tab in table["titles"], tab
    assert admin_js["accessTiles"] == [[label, *sub] for label, *sub in ACCESS_TILES]
    assert admin_js["clarityTiles"] == [list(t) for t in CLARITY_TILES]


def test_collect_summary_shows_one_decimal_rate_and_no_version(
    admin_js: dict[str, Any],
) -> None:
    """041 §3.5 — 수집 요약 줄·절 요약의 1시간 성공률은 소수 1자리, 버전 칸은 없다(응답에 version 이 있어도)."""
    collect = admin_js["collect"]
    assert "전체 1시간 99.8%" in collect["sum"], collect
    assert collect["head"].endswith(" · 1시간 99.8%"), collect
    for text in collect.values():
        assert "버전" not in text and "0.1.0" not in text, text


# --- 접속 절 v3 — 창 줄·질문 여섯 덩어리 (042 §4 정적 단언) -----------------------------------------

WINDOWS = ["24h", "7d", "30d"]
DBIP = '<a href="https://db-ip.com" rel="noreferrer">IP Geolocation by DB-IP</a>'


def test_traffic_window_is_one_query_with_three_window_words() -> None:
    """042 §3.2 — `?window=` 는 창을 붙이는 한 곳뿐이고 창 글자는 24h·7d·30d 셋, 창은 주소·저장소에 싣지 않는다."""
    js = _text("web/admin/admin.js")
    assert js.count("?window=") == 1
    assert "`${path}?window=${asked}`" in js
    assert set(re.findall(r"'(\d+[a-z])'", js)) == set(WINDOWS)
    assert _object_keys(
        re.search(r"const WINDOW_NAME = \{([^}]*)\};", js).group(1)
    ) == (WINDOWS)
    for banned in ("location.hash", "pushState", "localStorage", "sessionStorage"):
        assert banned not in js, banned
    # 주소는 만료 표시(RELOAD_MARK)를 볼 때만 읽는다 — 주소로 창을 고르지 않는다(§2)
    assert js.count("location.search") == 2
    assert js.count("new URLSearchParams(location.search).has(RELOAD_MARK)") == 2


def test_traffic_window_buttons_and_block_order_in_the_page() -> None:
    """042 §4 — 창 버튼 셋(type=button·data-window 순서·7d·30d disabled), `#traffic` 안 순서 — 041 설명 → 실시간 →
    창 줄 → 답 줄 여섯 → Clarity 카드, 답 줄과 다음 덩어리 사이에 본문 하나·'이 칸 뜻' 하나."""
    html = _text("web/admin/index.html")
    body = _section_bodies(html)["traffic"]
    buttons = re.findall(r"<button\b[^>]*\bdata-window=[^>]*>", body)
    assert [re.search(r'data-window="([^"]+)"', b).group(1) for b in buttons] == (
        WINDOWS
    )
    for button, window in zip(buttons, WINDOWS, strict=True):
        assert 'type="button"' in button, button
        assert (" disabled" in button) == (window != "24h"), button
    marks = [EXPLAIN, 'id="b-ws24"', 'class="window-bar"']
    marks += [f'id="a-q{n}"' for n in range(1, 7)] + ['id="m-clarity"']
    at = [body.index(m) for m in marks]
    assert at == sorted(at) and all(body.count(m) == 1 for m in marks)
    for n in range(1, 7):
        start = body.index(f'id="a-q{n}"')
        end = body.index(f'id="a-q{n + 1}"' if n < 6 else 'id="m-clarity"')
        between = body[start:end]
        assert re.findall(r'id="b-q\d"', between) == [f'id="b-q{n}"'], n
        assert between.count(TERMS) == 1, n
        assert between.index(f'id="b-q{n}"') < between.index(TERMS), n
        assert f'<p class="answer" id="a-q{n}"></p>' in body, n
        assert body.index(f'id="m-q{n}"') < start, n
    # 묶음·덩어리 카드에는 id 가 없다(041 — 설명의 id 가진 조상은 section 뿐)
    for card in re.findall(r'<div class="card q"[^>]*>', body):
        assert "id=" not in card, card


def test_traffic_dbip_attribution_is_one_fixed_link_outside_the_terms() -> None:
    """039 §3.2·042 §3.4 ③ — DB-IP CC BY 표시는 index.html 의 고정 링크 하나(③ 바닥 — 설명 dl 밖)."""
    html = _text("web/admin/index.html")
    assert html.count("db-ip.com") == 1 and html.count(DBIP) == 1
    dbip = [a for a in _anchors(html) if "db-ip" in a["href"]]
    assert dbip == [{"href": "https://db-ip.com", "rel": "noreferrer"}]
    for block in _folded(html, TERMS):
        assert "db-ip.com" not in block
    body = _section_bodies(html)["traffic"]
    assert body.index('id="b-q3"') < body.index(DBIP) < body.index('id="a-q4"')


def test_traffic_words_name_daily_counted_visitors() -> None:
    """042 §3.1 — 화면 낱말은 '날마다 센 방문자'(방문자-일 아님), 24시간 값은 사람 수가 아니라고 적는다."""
    js = _text("web/admin/admin.js")
    assert "날마다 센 방문자" in js
    assert "오늘·어제(KST)를 따로 세어 더한 수" in js
    for name in SCREEN_FILES:
        text = _text(f"web/admin/{name}")
        for word in ("방문자-일", "visitor-day", "visitor day"):
            assert word not in text, (name, word)


# 042 §3.6 이름표 일곱(계약 복사) — 행에는 한국어 이름, 원래 키는 title 과 '이 칸 뜻' 에만
TRAFFIC_NAMES = {
    "CLASS_NAME": {
        "browser": "사람 브라우저 모양",
        "search": "검색엔진",
        "ai": "AI 수집기",
        "preview": "링크 미리보기",
        "tool": "자동화 도구",
        "scanner": "스캐너",
        "operator": "운영자 흔적",
        "unknown": "이름 없음",
    },
    "CHANNEL_NAME": {
        "direct": "직접",
        "search": "검색",
        "inapp": "앱 안 브라우저",
        "social": "소셜·커뮤니티",
        "ai": "AI 답변",
        "referral": "다른 사이트 링크",
        "campaign": "캠페인(utm)",
        "internal": "사이트 안 이동",
        "unknown": "첫 페이지 기록 없음",
    },
    "NET_NAME": {
        "telecom_kr": "국내 통신사",
        "telecom": "해외 통신사",
        "cloud": "데이터센터·클라우드",
        "other": "기업·학교·기관",
        "unknown": "자료에 없음",
    },
    "APP_NAME": {
        "kakaotalk": "카카오톡",
        "naver": "네이버 앱",
        "instagram": "인스타그램",
        "facebook": "페이스북",
        "line": "라인",
        "daum": "다음 앱",
        "band": "밴드",
        "other": "그 밖 앱",
    },
    "DEVICE_NAME": {"mobile": "휴대폰", "tablet": "태블릿", "desktop": "데스크톱"},
    "OS_NAME": {
        "ios": "iOS",
        "android": "Android",
        "windows": "Windows",
        "macos": "macOS",
        "linux": "Linux",
        "chromeos": "ChromeOS",
        "other": "그 밖",
    },
    "BROWSER_NAME": {
        "chrome": "Chrome",
        "safari": "Safari",
        "samsung": "삼성 인터넷",
        "whale": "웨일",
        "edge": "Edge",
        "firefox": "Firefox",
        "opera": "Opera",
        "inapp": "앱 안 브라우저",
        "other": "그 밖",
    },
}


def test_traffic_name_tables_match_the_spec_and_every_key_is_explained() -> None:
    """042 §3.6·041 §3.1 — 이름표 일곱(한 줄 객체)의 키·이름이 §3.6 과 같고, 키는 모두 접속 절 '이 칸 뜻' dl 안에
    `<code>키</code>` 로 있다(행에는 쓰지 않아 휴대폰에서 원래 키를 보는 곳은 그 dl 뿐이다)."""
    js = _text("web/admin/admin.js")
    body = _section_bodies(_text("web/admin/index.html"))["traffic"]
    explained = "".join(
        re.findall(r"<dl>(.*?)</dl>", "".join(_folded(body, TERMS)), flags=re.S)
    )
    for name, expected in TRAFFIC_NAMES.items():
        found = re.search(rf"\nconst {name} = \{{ (.*?) \}};\n", js)
        assert found, name
        assert dict(re.findall(r"(\w+): '([^']*)'", found.group(1))) == expected, name
        for key in expected:
            assert f"<code>{key}</code>" in explained, (name, key)


def _css_rule(css: str, selector: str) -> str:
    found = re.findall(rf"(?m)^{re.escape(selector)}[^{{]*\{{([^}}]*)\}}", css)
    assert len(found) == 1, selector
    return found[0]


def _css_exact(css: str, selector: str) -> str:
    """맨 앞 칸에서 `선택자 {` 로 시작하는 규칙 하나(뒤에 다른 글자가 붙은 선택자는 다른 규칙)."""
    found = re.findall(rf"(?m)^{re.escape(selector)} \{{([^}}]*)\}}", css)
    assert len(found) == 1, selector
    return found[0]


def test_traffic_colors_use_the_spec_tokens_and_narrow_rows_stack() -> None:
    """042 §3.7·§3.9 — 확인 accent-400·모양 neutral-700·다시 온 accent-600·봇 neutral-600, 강도 h1~h5 = accent
    700·600·500·400·200(h0 은 막대 바탕), 480px 이하는 막대 행이 두 줄."""
    css = re.sub(r"/\*.*?\*/", "", _text("web/admin/admin.css"), flags=re.S)
    for selector, token in (
        (".chart .confirmed", "--color-accent-400"),
        (".chart .shaped", "--color-neutral-700"),
        (".chart .returning", "--color-accent-600"),
        (".chart .bot", "--color-neutral-600"),
        (".chart .h0", "--color-neutral-900"),
    ):
        assert f"var({token})" in _css_rule(css, selector), selector
    steps = [
        re.search(r"fill: var\((--color-accent-\d+)\)", _css_rule(css, f".chart .h{i}"))
        for i in range(1, 6)
    ]
    assert [m.group(1) for m in steps] == [
        f"--color-accent-{n}" for n in (700, 600, 500, 400, 200)
    ]
    narrow = re.search(r"@media \(max-width: 480px\) \{(.*?)\n\}", css, flags=re.S)
    assert narrow and ".brow" in narrow.group(1) and "'bar bar'" in narrow.group(1)
    # §3.9 — 행이 든 열이 340px 이하여도 두 줄, ③·⑤ 3열과 ① 범위 타일은 960px 이하에서 한 줄을 다 쓴다
    tight = re.search(r"@container \(max-width: 340px\) \{(.*?)\n\}", css, flags=re.S)
    assert tight and ".brow" in tight.group(1) and "'bar bar'" in tight.group(1)
    assert "container-type: inline-size" in _css_exact(css, ".col")
    mid = re.search(r"@media \(max-width: 960px\) \{(.*?)\n\}", css, flags=re.S)
    assert mid and ".cols3 { grid-template-columns: minmax(0, 1fr); }" in mid.group(1)
    assert ".tiles > :has(> .range-num) { grid-column: 1 / -1; }" in mid.group(1)
    # 가로 넘침 0 — geo 배지·범위 숫자는 줄을 바꾸고, 답 줄의 값은 바로 뒤 글자와 붙어 있다
    assert "white-space: normal" in _css_exact(css, ".geo-foot .tag")
    assert "nowrap" not in _css_exact(css, ".range-num")
    assert "white-space: nowrap" in _css_exact(css, ".answer .nb")
    # §3.7 — ⑥ 의 응답·지속 막대는 회색(장애색은 5xx 만), 시행 전 덩어리는 범례처럼 테두리
    assert "var(--color-neutral-600)" in _css_exact(css, ".q .chart .fill")
    assert "stroke: var(--color-neutral-700)" in _css_exact(css, ".chart .pre")
