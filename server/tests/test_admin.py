"""관리자 server 설정 계약 — nginx-admin.conf·web 이미지·compose (스펙 029 §3.1·§3.2·§3.5·§4).

test_deploy.py 와 같은 방식이다: Docker 없는 CI 에서 설정 파일을 읽어 단언한다. 실제로 nginx 를 띄워
분기·403·기록을 보는 검증은 029 §5 의 로컬 Docker 명령이다. 문법은 여기서 못 잡는다 — nginx-admin.conf 를
고친 PR 은 로컬 `nginx -t` 결과를 본문에 적는다(§3.6).
"""

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
# 화면의 10초 폴링 — 기록하지 않는다 (§3.2)
POLLING = {
    ("=", "/api/health"),
    ("=", "/api/health/collect"),
    ("=", "/svc/api/health"),
    ("=", "/svc/api/admin/status"),
}
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


def test_frames_denied_on_every_response_and_csp_on_the_screen() -> None:
    server = _admin_server()
    assert XFO in _args(server, "add_header")
    for key, children in _locations(server).items():
        assert XFO in _effective(children, "add_header"), key
    screen = _args(_locations(server)[("/",)], "add_header")
    csp = [
        "Content-Security-Policy",
        "default-src 'self'; frame-ancestors 'none'",
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
    assert off == POLLING


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


# --- 관리자 화면 정적 단언 (§3.3) ---------------------------------------------------------

SCREEN = ROOT / "web/admin"


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
    assert "visibilityState" in js
    for banned in ("localStorage", "sessionStorage", "innerHTML", "window.open"):
        assert banned not in js, banned
    # 모든 요청이 한 함수를 지난다 — 헤더가 빠진 fetch 가 없게
    assert js.count("fetch(") == 1
    # 새로고침·표시 지우기 주소는 화면 주소 `/` 로 고정 — `//다른호스트/..%2F/` 로 열린 화면에서 pathname 을 쓰면
    # 프로토콜 상대 URL 이 되어 다른 출처로 간다(열린 리다이렉트)
    assert "location.pathname" not in js and "location.href" not in js
    assert js.count("location.replace(") == 1
    assert "location.replace(`/?${RELOAD_MARK}=1`)" in js
    assert js.count("history.replaceState(") == 1
    assert "history.replaceState(null, '', '/')" in js


def test_screen_page_has_no_inline_script_or_style() -> None:
    html = _text("web/admin/index.html")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S)
    assert scripts == [(' src="admin.js" defer', "")]
    assert "<style" not in html and "style=" not in html
    assert re.search(r'<input id="token" type="password" autocomplete="off"', html)
    assert "<form" not in html  # 제출 없음 — 토큰이 URL·기록으로 새지 않게
    for href in ("/api/docs", "/api/redoc", "/cdn-cgi/access/logout"):
        assert f'href="{href}"' in html, href
    assert 'target="' not in html  # 같은 탭 이동
