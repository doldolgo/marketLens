"""관측 설정 계약 — caddy 접속 로그·compose·배포·nginx (스펙 027 §3.2·§4).

test_deploy.py 와 같은 방식이다: Docker 없는 CI 에서 설정 파일을 읽어 단언한다. 실제로 caddy 를 띄워
줄을 보는 검증은 027 §5 의 로컬 Docker 명령이다. Caddyfile 은 CI 가 문자열로만 본다 — 문법 오류는
여기서 못 잡으므로 Caddyfile 을 고친 PR 은 로컬 `caddy validate` 결과를 본문에 적는다(§3.8).
"""

import re
import shlex

from tests.test_deploy import (
    PUBLIC_API,
    ROOT,
    _args,
    _deploy_script,
    _locations,
    _public_server,
    _text,
    _yaml,
)

# 기록하지 않는 요청 경로 — 폴링·감시가 방문 기록을 덮는다 (§3.2)
NOLOG_PATHS = {
    "/api/health",
    "/api/health/collect",
    "/api/landing",
    "/api/history/events",
    "/api/history/candles",
}
CANARY_UA = "KimpTrack-Canary/1"

# Caddyfile 지시어 하나 = (인자, 블록 자식 또는 None)
Directive = tuple[list[str], list | None]


def _split(line: str) -> list[str]:
    """공백으로 나누고 큰따옴표만 벗긴다 — 역슬래시는 caddy 처럼 글자 그대로 둔다(정규식의 `\\[`)."""
    lex = shlex.shlex(line, posix=True)
    lex.whitespace_split = True
    lex.commenters = ""
    lex.escape = ""
    return list(lex)


def _caddy_tree(text: str) -> list[Directive]:
    """Caddyfile → 지시어 트리. 줄 단위(여는 { 는 줄 끝), 토큰 앞의 # 만 주석, 큰따옴표는 벗긴다."""
    root: list[Directive] = []
    stack = [root]
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if line == "}":
            stack.pop()
            continue
        args = _split(line)
        if args[-1] == "{":
            children: list[Directive] = []
            stack[-1].append((args[:-1], children))
            stack.append(children)
        else:
            stack[-1].append((args, None))
    assert len(stack) == 1, "Caddyfile 의 중괄호 짝이 안 맞는다"
    return root


def _caddyfile() -> list[Directive]:
    return _caddy_tree(_text("caddy/Caddyfile"))


def _block(tree: list[Directive], *head: str) -> list[Directive]:
    found = [c for args, c in tree if tuple(args) == head and c is not None]
    assert len(found) == 1, head
    return found[0]


def _all(tree: list[Directive], name: str) -> list[list[str]]:
    """트리 전체(중첩 포함)에서 이름이 `name` 인 지시어의 인자."""
    out: list[list[str]] = []
    for args, children in tree:
        if args and args[0] == name:
            out.append(args[1:])
        if children is not None:
            out.extend(_all(children, name))
    return out


def _snippet() -> list[Directive]:
    return _block(_caddyfile(), "(access_log)")


def _domain() -> list[Directive]:
    return _block(_caddyfile(), "kimptrack.com,", "www.kimptrack.com")


# 지우기 규칙 — 접속 로그와 기본 로거가 똑같이 갖는다 (§3.2)
SCRUB = [
    (["wrap", "json"], None),
    (["request>headers", "delete"], None),
    (["resp_headers", "delete"], None),
    (
        ["request>remote_ip", "ip_mask"],
        [(["ipv4", "24"], None), (["ipv6", "48"], None)],
    ),
    (
        ["request>client_ip", "ip_mask"],
        [(["ipv4", "24"], None), (["ipv6", "48"], None)],
    ),
    (
        ["request>uri", "query"],
        [
            (["delete", "s.q"], None),
            (["delete", "g.q"], None),
            (["delete", "p.q"], None),
        ],
    ),
]


def test_access_log_snippet_writes_rotated_0644_file() -> None:
    """파일 `/var/log/caddy/access.log`·0644·50MiB 회전·회전 파일 5개·90일 (§3.2)."""
    log = _block(_snippet(), "log")
    output = _block(log, "output", "file", "/var/log/caddy/access.log")
    assert output == [
        (["mode", "0644"], None),
        (["roll_size", "50MiB"], None),
        (["roll_keep", "5"], None),
        (["roll_keep_for", "90d"], None),
    ]


def test_access_log_and_default_logger_share_the_scrub_rules() -> None:
    """헤더 통째 삭제·IP 두 필드 /24·/48·검색어 세 키 삭제 — 기본 로거(오류 줄)도 같다 (§3.2)."""
    access = _block(_block(_snippet(), "log"), "format", "filter")
    assert access == SCRUB
    default = _block(_block(_block(_caddyfile()), "log", "default"), "format", "filter")
    assert default == SCRUB


def test_access_log_appends_only_ua_and_origin_referer() -> None:
    """헤더 대신 `ua`(User-Agent 그대로)·`referer`(http(s) 스킴+호스트(+포트), 아니면 빈 값) 두 필드 (§3.2)."""
    snippet = _snippet()
    assert _all(snippet, "log_append") == [
        ["ua", "{http.request.header.User-Agent}"],
        ["referer", "{referer_origin}"],
    ]
    mapping = _block(
        snippet, "map", "{http.request.header.Referer}", "{referer_origin}"
    )
    assert len(mapping) == 2 and mapping[1] == (["default", ""], None)
    (pattern, output), _ = mapping[0]
    assert pattern.startswith("~") and output == "${1}"
    # Go RE2 와 파이썬 re 가 같게 읽는 식이다 — 같은 예로 결과를 본다
    origin = re.compile(pattern[1:])
    cases = {
        "https://a.com?q=x": "https://a.com",
        "https://a.com/path?s.q=btc": "https://a.com",
        "http://news.site:8080/a/b": "http://news.site:8080",
        "HTTPS://A.COM/": "HTTPS://A.COM",
        "https://[2001:db8::1]:8443/x": "https://[2001:db8::1]:8443",
        "android-app://x/y": "",
        "https://user:pw@evil.com/": "",
        "": "",
    }
    for referer, expected in cases.items():
        m = origin.match(referer)
        assert (m.group(1) if m else "") == expected, referer


def test_polling_paths_and_canary_are_not_logged() -> None:
    """폴링 다섯 경로(= 공개 허용 목록에서 WebSocket 을 뺀 것)와 canary UA 는 기록하지 않는다 (§3.2)."""
    snippet = _snippet()
    matchers = {args[0]: args[1:] for args, _ in snippet if args[0].startswith("@")}
    assert sorted(a[0] for a in _all(snippet, "log_skip")) == ["@canary", "@nolog"]
    assert matchers["@nolog"][0] == "path"
    assert set(matchers["@nolog"][1:]) == NOLOG_PATHS
    assert NOLOG_PATHS == set(PUBLIC_API) - {"/api/ws/spreads"}
    # WS 는 연결이 끝날 때 101·duration 한 줄 — 대시보드를 열어 둔 시간이라 남긴다
    assert matchers["@canary"] == ["header", "User-Agent", "*KimpTrack-Canary*"]
    assert "KimpTrack-Canary" in CANARY_UA


def test_only_domain_block_imports_access_log_with_referrer_policy() -> None:
    """도메인 블록만 조각을 부르고 `Referrer-Policy: strict-origin`, catch-all 은 기록 없이 그대로 (§3.2, 023)."""
    tree = _caddyfile()
    domain = _domain()
    assert (["import", "access_log"], None) in domain
    assert (["header", "Referrer-Policy", "strict-origin"], None) in domain
    catch_all = _block(tree, "http://")
    assert catch_all == [(["reverse_proxy", "web:80"], None)]
    assert _all(tree, "import") == [["access_log"]]
    # 023 계약 그대로 — 두 블록 모두 nginx(web:80)로
    assert (["reverse_proxy", "web:80"], None) in domain


def test_compose_binds_caddy_dir_and_host_log_dir() -> None:
    """caddy/ 디렉터리째 읽기 전용·접속 로그 호스트 바인드·인증서 볼륨, 루트 Caddyfile 없음, logs/ 는 git 무시 (§3.2)."""
    caddy = _yaml("docker-compose.yml")["services"]["caddy"]
    assert caddy["volumes"] == [
        "./caddy:/etc/caddy:ro",
        "./logs/caddy:/var/log/caddy",
        "caddy-data:/data",
        "caddy-config:/config",
    ]
    assert not (ROOT / "Caddyfile").exists()
    assert "logs/" in _text(".gitignore").splitlines()


# 공개 server 의 location 전부 — 027 은 접속 로그만 끄고 분기는 건드리지 않는다 (§4)
PUBLIC_LOCATIONS = {("=", path) for path in PUBLIC_API} | {
    ("=", "/api"),
    ("/api/",),
    ("=", "/index.html"),
    ("/assets/",),
    ("=", "/"),
    ("=", "/app"),
    ("/app/assets/",),
    ("=", "/app/index.html"),
    ("/app/",),
    ("/",),
}
CADDY_RELOAD = "docker exec marketlens-caddy caddy reload --config /etc/caddy/Caddyfile"


def test_public_nginx_turns_off_access_log_and_keeps_its_locations() -> None:
    """기록은 caddy 한 곳 — nginx 접속 로그는 끄고(오류 로그는 기본 그대로) 새 location 은 없다 (§3.2)."""
    server = _public_server()
    assert _args(server, "access_log") == [["off"]]
    assert not _args(server, "error_log")
    assert set(_locations(server)) == PUBLIC_LOCATIONS


def test_serve_deploy_reloads_caddy_after_up_and_prunes_last() -> None:
    """serve 는 up 뒤에 caddy 설정을 다시 읽히고 prune 이 마지막. data·collect 는 caddy 를 모른다 (§3.2)."""
    script = _deploy_script("serve")
    i_up = next(i for i, ln in enumerate(script) if "up -d --build" in ln)
    assert script.count(CADDY_RELOAD) == 1
    assert i_up < script.index(CADDY_RELOAD) == len(script) - 2
    assert script[-1] == "docker image prune -f"
    for box in ("data", "collect"):
        assert not any("caddy" in ln for ln in _deploy_script(box)), box
