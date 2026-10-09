"""관리자 server 설정 계약 — nginx-admin.conf·web 이미지·compose (스펙 029 §3.1·§3.2·§3.5·§4, 034·035 의 피드 넷)
와 관리자 화면 v3 정적 단언(064 §4 — 페이지 셋의 머리·스크립트·차트 사본·링크·CSP·색). 계산·부르기는 node 시험
(test_admin_rules.py·test_admin_calc.py·test_admin_pages.py)이 본다.

test_deploy.py 와 같은 방식이다: Docker 없는 CI 에서 설정 파일을 읽어 단언한다. 실제로 nginx 를 띄워
분기·403·기록을 보는 검증은 029 §5 의 로컬 Docker 명령이다. 문법은 여기서 못 잡는다 — nginx-admin.conf 를
고친 PR 은 로컬 `nginx -t` 결과를 본문에 적는다(§3.6).
"""

import hashlib
import json
import re

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


# 064 §3.1 — 스크립트는 자기 출처만(default-src), 스타일만 인라인을 연다(ECharts 툴팁이 인라인 스타일을 쓴다).
# frame-src 는 화면 분석 페이지(053·065)가 띄우는 공개 사이트 하나
ADMIN_CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-src https://kimptrack.com; frame-ancestors 'none'"
SCREEN_LOCATIONS = {("/",), ("/vendor/",)}


def test_frames_denied_on_every_response_and_csp_on_the_screen() -> None:
    """모든 응답에 액자 금지, 화면(/)과 차트 라이브러리 사본(/vendor/)에만 CSP — 화면은 캐시하지 않고, 판 번호가 든
    사본은 브라우저에만 오래(private·immutable). 둘 다 공개 root 밖의 정적 파일이다(064 §2)."""
    server = _admin_server()
    assert XFO in _args(server, "add_header")
    locations = _locations(server)
    for key, children in locations.items():
        assert XFO in _effective(children, "add_header"), key
    csp = ["Content-Security-Policy", ADMIN_CSP, "always"]
    for key in SCREEN_LOCATIONS:
        assert csp in _args(locations[key], "add_header"), key
        assert _args(locations[key], "root") == [["/usr/share/nginx/admin"]], key
        assert not _args(locations[key], "proxy_pass"), key
    assert ["Cache-Control", "no-store", "always"] in _args(
        locations[("/",)], "add_header"
    )
    vendor = _args(locations[("/vendor/",)], "add_header")
    assert ["Cache-Control", "private, max-age=31536000, immutable", "always"] in vendor
    assert _admin_route("/vendor/echarts-5.5.1.min.js") == ("/vendor/",)
    csp_elsewhere = [
        k
        for k, c in locations.items()
        if k not in SCREEN_LOCATIONS
        and any(h[0] == csp[0] for h in _args(c, "add_header"))
    ]
    assert not csp_elsewhere
    # 스크립트는 그대로 'self' 만 — 인라인·eval 을 여는 글자가 없다
    assert "script-src" not in ADMIN_CSP and "unsafe-eval" not in ADMIN_CSP
    assert (
        ADMIN_CSP.count("'unsafe-inline'") == 1
        and "style-src 'self' 'unsafe-inline'" in ADMIN_CSP
    )


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


# --- 관리자 화면 v3 정적 단언 (064 §4) -------------------------------------------------------------

SCREEN = ROOT / "web/admin"
PAGES = {
    "index.html": ("/", "overview.js"),
    "server.html": ("/server.html", "server.js"),
    "traffic.html": ("/traffic.html", "traffic.js"),
}
NAV = [
    ("/", "개요"),
    ("/server.html", "서버"),
    ("/traffic.html", "트래픽"),
    ("/screens.html", "화면"),
]
SCRIPTS = ("common.js", "charts.js", "overview.js", "server.js", "traffic.js")
VENDOR = "vendor/echarts-5.5.1.min.js"
# npm 꾸러미 echarts@5.5.1 의 dist/echarts.min.js 그대로(고치지 않는다 — §2)
VENDOR_SHA256 = "e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf"
# 밖으로 나가는 링크의 호스트(고정 https 주소뿐). db-ip.com 은 DB-IP CC BY 표시(039)
EXTERNAL_HOSTS = {
    "clarity.microsoft.com",
    "dash.cloudflare.com",
    "one.dash.cloudflare.com",
    "github.com",
    "db-ip.com",
}
# 화면 스크립트에 없어야 하는 것: 브라우저 저장소·HTML 해석·코드 실행·새 창·주소 읽기·style·링크·주소 쓰기·fetch 밖 요청
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
    "setAttribute('src'",
    ".src",
    "setAttributeNS",
    "xlink:href",
    "new XMLHttpRequest",
    "sendBeacon",
    "new WebSocket",
    "EventSource",
    "location.hash",
    "pushState",
    "location.assign",
    "location.search =",
    "location =",
    "postMessage",
)


def _script(name: str) -> str:
    return _text(f"web/admin/{name}")


def _all_scripts() -> str:
    return "".join(_script(name) for name in SCRIPTS)


def test_screen_is_three_pages_shared_modules_and_one_vendored_library() -> None:
    """§2 — 페이지 셋 + 공통 모듈 둘 + 페이지 스크립트 셋 + 스타일 + vendor(ECharts 5.5.1 사본·라이선스). 공개 root 밖."""
    files = sorted(str(p.relative_to(SCREEN)) for p in SCREEN.rglob("*") if p.is_file())
    assert files == sorted(
        [
            *PAGES,
            *SCRIPTS,
            "admin.css",
            VENDOR,
            "vendor/ECHARTS-LICENSE.txt",
            "vendor/ECHARTS-NOTICE.txt",
            "vendor/ECHARTS-LICENSE-d3.txt",
        ]
    )
    assert "COPY admin /usr/share/nginx/admin" in _text("web/Dockerfile").splitlines()
    public = {p.name for p in (ROOT / "web/public").rglob("*")}
    assert not {"admin", *SCRIPTS, "admin.css"} & public


def test_vendored_echarts_is_the_untouched_5_5_1_build_with_its_license() -> None:
    """§2 — dist/echarts.min.js 를 고치지 않고(체크섬), Apache-2.0 원문과 NOTICE·d3 BSD 를 함께 둔다. node 시험의 가짜
    encodeHTML 은 이 사본의 다섯 글자 바꿈과 같다."""
    data = (SCREEN / VENDOR).read_bytes()
    assert hashlib.sha256(data).hexdigest() == VENDOR_SHA256
    text = data.decode("utf-8")
    assert 'version="5.5.1"' in text
    assert re.search(
        r"""/\(\[&<>"'\]\)/g,\w+=\{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"\}""",
        text,
    )
    license_text = _text("web/admin/vendor/ECHARTS-LICENSE.txt")
    assert (
        license_text.lstrip().startswith("Apache License")
        and "Version 2.0, January 2004" in license_text
    )
    assert "Apache ECharts" in _text("web/admin/vendor/ECHARTS-NOTICE.txt")
    assert "Mike Bostock" in _text("web/admin/vendor/ECHARTS-LICENSE-d3.txt")
    harness = _text("server/tests/admin_dom.py")
    assert "replace(/([&<>\"'])/g" in harness and "'&#39;'" in harness


def test_every_page_has_the_same_header_with_its_own_link_marked() -> None:
    """§3.1 — 머리 줄: 이름, 링크 넷(개요·서버·트래픽·화면 — 지금 페이지 강조), 상태 점(개요 링크), 마지막 갱신, 로그아웃."""
    for page, (path, _) in PAGES.items():
        html = _text(f"web/admin/{page}")
        nav = re.search(r'<nav class="nav"[^>]*>(.*?)</nav>', html, flags=re.S)
        assert nav, page
        links = re.findall(
            r'<a href="([^"]+)"( aria-current="page")?>([^<]+)</a>', nav.group(1)
        )
        assert [(href, label) for href, _, label in links] == NAV, page
        assert [href for href, current, _ in links if current] == [path], page
        assert (
            '<a class="state wait" id="hdr-state" href="/"><span id="hdr-word">확인 중</span></a>'
            in html
        ), page
        assert (
            '<span class="stamp">마지막 갱신 <span id="updated">--:--:--</span></span>'
            in html
        ), page
        assert '<a class="logout" href="/cdn-cgi/access/logout">로그아웃</a>' in html, (
            page
        )
        assert '<p id="notice" class="notice" role="alert" hidden></p>' in html, page
        assert (
            html.count('class="brand"') == 1 and "KimpTrack <span>관리자</span>" in html
        ), page


def test_pages_load_the_library_then_their_module_without_inline_code() -> None:
    """§3.1·§4 — 인라인 스크립트·스타일 없음(CSP 'self'), 라이브러리(defer) 뒤에 그 페이지의 ES 모듈 하나."""
    for page, (_, module) in PAGES.items():
        html = _text(f"web/admin/{page}")
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S)
        assert scripts == [
            (f' src="{VENDOR}" defer', ""),
            (f' type="module" src="{module}"', ""),
        ], page
        assert "<style" not in html and "style=" not in html, page
        assert re.search(r"\son\w+\s*=", html) is None, page
        assert "<form" not in html, page
        assert '<link rel="stylesheet" href="admin.css" />' in html, page
        # 긴 설명 문단·'이 절 읽는 법' 없이 제목 옆 '?' 하나(title 한두 문장)만
        assert (
            "이 절 읽는 법" not in html
            and "이 칸 뜻" not in html
            and '<details class="explain"' not in html
        ), page
        for title in re.findall(r'<span class="q" title="([^"]*)">\?</span>', html):
            assert 0 < len(title) <= 110 and title.count(". ") <= 1, (page, title)


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


def test_links_are_fixed_addresses_and_only_the_screens_link_opens_a_new_window() -> (
    None
):
    """036 §3.8 그대로 — 밖으로 나가는 링크는 고정 https·noreferrer·같은 탭, 계정·팀·프로젝트 ID 없음(레포 공개).
    §3.4 — '화면 분석 열기 ↗'(screens.html)만 새 창(noopener). DB-IP 표시(039)는 나라를 그리는 트래픽 페이지에 하나."""
    for page in PAGES:
        html = _text(f"web/admin/{page}")
        for a in _anchors(html):
            href = a["href"]
            if href.startswith("/") and not href.startswith("//"):
                if "target" in a:
                    assert (href, a["target"], a.get("rel")) == (
                        "/screens.html",
                        "_blank",
                        "noopener",
                    ), page
                continue
            assert (
                href.startswith("https://")
                and a.get("rel") == "noreferrer"
                and "target" not in a
            ), href
            host = href.split("/")[2]
            assert host in EXTERNAL_HOSTS or host.endswith(".console.aws.amazon.com"), (
                href
            )
            if "cloudflare.com" in host:
                assert href == f"https://{host}/", (
                    href
                )  # 대시보드 주소에는 계정 ID 가 든다 — 루트만
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", html), page
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", html), page
        assert not re.search(r"[0-9a-f]{32,}", html), page
        assert "cloudflareaccess.com" not in html and "/projects/view/" not in html, (
            page
        )
    traffic = _text("web/admin/traffic.html")
    assert (
        traffic.count(
            '<a href="https://db-ip.com" rel="noreferrer">IP Geolocation by DB-IP</a>'
        )
        == 1
    )
    assert traffic.count('target="_blank"') == 1
    server = _text("web/admin/server.html")
    assert '<a href="/api/docs">' in server and '<a href="/api/redoc">' in server
    assert re.search(r'<input id="token" type="password" autocomplete="off"', server)
    for page in ("index.html", "traffic.html"):
        assert "target=" not in _text(f"web/admin/{page}").replace(
            'target="_blank" rel="noopener"', ""
        )


def test_scripts_never_parse_html_store_data_or_write_addresses() -> None:
    """§3.1·§4 — 피드 글자는 textContent·title 로만(innerHTML 없음), 저장소·새 창·주소 쓰기 없음. 요청은 call() 하나의
    fetch, 새로고침·표시 지우기 주소는 그 페이지의 고정 주소(page.home), 링크 주소는 jump() 하나가 고정 표에서만."""
    for name in SCRIPTS:
        js = _script(name)
        for banned in SCRIPT_BANNED:
            assert banned not in js, (name, banned)
        assert not re.search(r"https?://", js), name
    every = _all_scripts()
    common = _script("common.js")
    assert (
        every.count("fetch(") == 1
        and "resp = await fetch(path, { ...init, headers: { ...XHR, ...init.headers }"
        in common
    )
    assert every.count("'X-Requested-With': 'XMLHttpRequest'") == 1
    assert (
        every.count("location.replace(") == 1
        and "location.replace(`${page.home}?${RELOAD_MARK}=1`);" in common
    )
    assert (
        every.count("history.replaceState(") == 1
        and "history.replaceState(null, '', page.home);" in common
    )
    assert (
        every.count("location.search")
        == 2
        == common.count("new URLSearchParams(location.search).has(RELOAD_MARK)")
    )
    assert (
        every.count("setAttribute('href'") == 1
        and "if (href !== undefined) node.setAttribute('href', href);" in common
    )
    assert "const href = own(JUMP, to);" in common
    for page, (path, module) in PAGES.items():
        assert _script(module).count(f"home: '{path}',") == 1, page
    # 툴팁 HTML 을 만드는 곳은 charts.js 의 tip() 하나 — 끼우는 값은 encodeHTML 을 지난 글자와 허용 목록의 클래스뿐
    charts = _script("charts.js")
    tip = re.search(
        r"\nexport function tip\(head, rows = \[\]\) \{\n(.*?)\n\}\n",
        charts,
        flags=re.S,
    )
    assert tip, "tip()"
    for name in SCRIPTS:
        literals = len(re.findall(r"""['"`]<""", _script(name)))
        assert literals == (
            len(re.findall(r"""['"`]<""", tip.group(1))) if name == "charts.js" else 0
        ), name
    assert set(re.findall(r"\$\{([^}]*)\}", tip.group(1))) == {
        "esc(head)",
        "mark",
        "key",
        "esc(name)",
        "esc(value)",
    }
    assert "const esc = (v) => lib().format.encodeHTML(clean(v));" in charts
    assert "MARKS.has(mark)" in tip.group(1)


def test_paths_table_polling_periods_and_no_copied_feed_periods() -> None:
    """§3.1 — 부르는 경로는 common.js 표 하나(즉시 갱신만 server.js), 빠른 10초·느린 60초, 피드 주기는 refreshSec 로만."""
    common = _script("common.js")
    table = re.search(
        r"export const PATHS = Object\.freeze\(\{(.*?)\}\);", common, flags=re.S
    )
    assert table, "PATHS"
    assert dict(re.findall(r"(\w+): '([^']+)'", table.group(1))) == {
        "collector": "/api/health",
        "api": "/svc/api/health",
        "status": "/svc/api/admin/status",
        "collect": "/api/health/collect",
        "aws": "/api/admin/aws",
        "series": "/api/admin/aws/series",
        "alerts": "/api/admin/alerts",
        "access": "/svc/api/admin/access",
        "clarity": "/svc/api/admin/clarity",
    }
    assert re.search(r"\bFAST_MS = 10_000;", common) and re.search(
        r"\bSLOW_MS = 60_000;", common
    )
    assert (
        "export const FAST = Object.freeze(['collector', 'api', 'status', 'collect']);"
        in common
    )
    for name in SCRIPTS:
        if name != "common.js":
            expected = ["/api/refresh"] if name == "server.js" else []
            assert re.findall(r"'(/(?:api|svc)/[^']*)'", _script(name)) == expected, (
                name
            )
    every = _all_scripts()
    for number in (
        "10800",
        "10_800",
        "14400",
        "14_400",
        "43200",
        "43_200",
        "21600",
        "21_600",
    ):
        assert not re.search(rf"(?<![\w.]){number}(?![\w.])", every), number


def _root_tokens(css: str) -> dict[str, str]:
    block = re.search(r":root\s*\{(.*?)\}", css, flags=re.S)
    assert block, ":root 블록"
    return {
        k: v.strip()
        for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block.group(1))
    }


def _rule(css: str, selector: str) -> str:
    found = re.findall(rf"(?m)^{re.escape(selector)} \{{([^}}]*)\}}", css)
    assert len(found) == 1, selector
    return found[0]


SPEC_COLORS = {
    "--bg": "#121420",
    "--card": "#1c1f2b",
    "--line": "#2c3040",
    "--text": "#ececf1",
    "--muted": "#9a9cab",
    "--accent": "#9184d9",
    "--ok": "#5fbf8f",
    "--warn": "#e0a458",
    "--bad": "#e0697d",
    "--sub": "#6f9bee",
}


def test_styles_and_chart_colors_follow_the_spec_tokens_and_layout() -> None:
    """§3.1 — 어두운 바탕 토큰, 글 15px·큰 수 32px 굵게·카드 제목 15px, 최대 폭 1600px·카드 420px·큰 수 180px 격자,
    640px 아래 한 줄, 외부 자원 없음. 차트 색 순서는 강조·보조·정상·주의·문제·#c792ea·#7fdbca·#f78c6c."""
    css = re.sub(r"/\*.*?\*/", "", _text("web/admin/admin.css"), flags=re.S)
    assert "@import" not in css and "url(" not in css
    tokens = _root_tokens(css)
    assert {k: tokens[k] for k in SPEC_COLORS} == SPEC_COLORS
    assert (
        "color-scheme: dark;" in _rule(css, ":root")
        and "prefers-color-scheme" not in css
    )
    body = _rule(css, "body")
    for decl in (
        "background: var(--bg);",
        "word-break: keep-all;",
        "overflow-wrap: break-word;",
    ):
        assert decl in body, decl
    assert re.search(r"font: 15px/", body)
    big = _rule(css, ".big")
    assert "font-size: 32px;" in big and "font-weight: 700;" in big
    assert "font-size: 15px;" in css.split(".card-head h3 {", 1)[1].split("}", 1)[0]
    assert "max-width: 1600px;" in _rule(css, ".wrap")
    assert "repeat(auto-fit, minmax(min(420px, 100%), 1fr))" in _rule(css, ".grid")
    assert "repeat(auto-fit, minmax(min(180px, 100%), 1fr))" in _rule(css, ".kpis")
    narrow = re.search(r"@media \(max-width: 640px\) \{(.*)\}\s*$", css, flags=re.S)
    assert narrow and re.search(
        r"\.grid,\s*\.kpis \{\s*grid-template-columns: minmax\(0, 1fr\);",
        narrow.group(1),
    )
    charts = _script("charts.js")
    colors = dict(
        re.findall(
            r"  (\w+): '(#[0-9a-f]{6})',",
            charts.split("export const COLOR", 1)[1].split("});", 1)[0],
        )
    )
    assert {
        f"--{k}": v for k, v in colors.items() if f"--{k}" in SPEC_COLORS
    } == SPEC_COLORS
    order = re.search(r"export const SERIES = Object\.freeze\(\[(.*?)\]\);", charts)
    assert order and [x.strip() for x in order.group(1).split(",")] == [
        "COLOR.accent",
        "COLOR.sub",
        "COLOR.ok",
        "COLOR.warn",
        "COLOR.bad",
        "'#c792ea'",
        "'#7fdbca'",
        "'#f78c6c'",
    ]
