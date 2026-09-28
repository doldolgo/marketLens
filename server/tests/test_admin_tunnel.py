"""관리자 터널 설정 계약 — compose 의 cloudflared·admin 망·토큰 secret (스펙 030 §3.2·§4).

test_deploy.py 와 같은 방식이다: Docker 없는 CI 에서 설정 파일을 읽어 단언한다. 컨테이너를 실제로 띄워
serve 컨테이너가 안 바뀌는지·cloudflared 가 api 이름을 못 푸는지 보는 검증은 030 §5 의 로컬 Docker 명령이다.
이미지 digest 가 멀티 아키텍처 인덱스인지도 여기서는 못 본다(네트워크 없음) — §7 에 `imagetools inspect` 결과를 남긴다.
"""

import re

from tests.test_deploy import _text, _yaml

METRICS = "127.0.0.1:2000"
SECRET = "cloudflared-token"
TOKEN_PATH = "/run/secrets/cloudflared-token"


def _compose() -> dict:
    return _yaml("docker-compose.yml")


def _cloudflared() -> dict:
    return _compose()["services"]["cloudflared"]


def test_cloudflared_is_a_tunnel_profile_service_without_ports() -> None:
    """이름·재시작·profile tunnel(박스 profile 아님)·게시 포트 없음·메모리 128MB·로그 상한 (§3.2)."""
    svc = _cloudflared()
    assert svc["container_name"] == "marketlens-cloudflared"
    assert svc["restart"] == "unless-stopped"
    assert svc["profiles"] == ["tunnel"]
    assert "ports" not in svc and "expose" not in svc
    assert svc["mem_limit"] == "128m"
    assert svc["logging"] == {
        "driver": "json-file",
        "options": {"max-size": "50m", "max-file": "3"},
    }
    # 빌드하지 않는다 — 공식 이미지를 그대로
    assert "build" not in svc


def test_cloudflared_image_is_pinned_by_tag_and_digest() -> None:
    """태그 + 인덱스 digest, `latest` 아님, 토큰 파일을 읽는 2025.4.0 이상 (§3.2)."""
    image = _cloudflared()["image"]
    name, _, digest = image.partition("@")
    repo, _, tag = name.partition(":")
    assert repo == "cloudflare/cloudflared"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), image
    assert re.fullmatch(r"\d{4}\.\d{1,2}\.\d+", tag), "latest·빈 태그 금지"
    year, month, _ = (int(p) for p in tag.split("."))
    assert (year, month) >= (2025, 4)


def test_cloudflared_runs_the_tunnel_at_info_level_with_loopback_metrics() -> None:
    """명령은 `tunnel --loglevel info --metrics 127.0.0.1:2000 run` 으로 시작한다 — debug 는 헤더를 전부 찍는다 (§3.2)."""
    command = _cloudflared()["command"]
    assert command[:6] == ["tunnel", "--loglevel", "info", "--metrics", METRICS, "run"]
    assert command.count("--loglevel") == 1
    assert "debug" not in command
    # 토큰은 명령 인자에 없다
    assert not any("token" in arg.lower() for arg in command)


def test_cloudflared_healthcheck_is_exec_form_ready() -> None:
    """distroless 라 셸이 없다 — exec 형식(`CMD`)으로 같은 metrics 주소의 ready (§3.2)."""
    test = _cloudflared()["healthcheck"]["test"]
    assert test[0] == "CMD"
    assert test[1:] == ["cloudflared", "tunnel", "--metrics", METRICS, "ready"]


def test_token_reaches_only_cloudflared_as_a_file_secret() -> None:
    """최상위 secrets 파일 하나 → cloudflared 에만, env 는 `TUNNEL_TOKEN_FILE` 뿐. `TUNNEL_TOKEN` 은 어디에도 없다 (§3.2)."""
    compose = _compose()
    assert compose["secrets"] == {SECRET: {"file": "./secrets/cloudflared-token"}}
    svc = compose["services"]["cloudflared"]
    assert svc["secrets"] == [SECRET]
    assert svc["environment"] == {"TUNNEL_TOKEN_FILE": TOKEN_PATH}
    # server/.env 는 api·수집기용 — cloudflared 에 넣지 않는다
    assert "env_file" not in svc
    for name, other in compose["services"].items():
        if name != "cloudflared":
            assert "secrets" not in other, name
        # 환경변수·명령 인자로 토큰을 주지 않는다 — docker inspect·compose config 에 찍힌다
        assert "TUNNEL_TOKEN" not in (other.get("environment") or {}), name
        args = [str(arg) for arg in other.get("command") or []]
        assert not any("--token" in arg for arg in args), name
    assert not re.search(r"\bTUNNEL_TOKEN\b", _text("server/.env.example"))
    assert "secrets/" in _text(".gitignore").splitlines()


def test_admin_network_holds_only_web_and_cloudflared() -> None:
    """cloudflared 는 전용 망 admin 에만, web 은 기본 망과 admin 두 곳 — cloudflared 는 api·caddy 이름을 못 푼다 (§3.1)."""
    compose = _compose()
    assert set(compose["networks"]) == {"admin"}
    # 엣지로 나가야 한다 — internal 망이면 터널이 서지 않는다
    assert not (compose["networks"]["admin"] or {}).get("internal")
    services = compose["services"]
    assert services["cloudflared"]["networks"] == ["admin"]
    assert services["web"]["networks"] == ["default", "admin"]
    on_admin = {
        name for name, svc in services.items() if "admin" in svc.get("networks", [])
    }
    assert on_admin == {"web", "cloudflared"}


def test_local_integrated_run_does_not_start_cloudflared() -> None:
    """문서의 로컬 통합 기동 profile 에 cloudflared 의 profile 이 없다 — 로컬은 cloudflared 를 안 띄운다 (§3.2)."""
    (tunnel,) = _cloudflared()["profiles"]
    for rel in ("README.md", "docs/context/dev-setup.md"):
        runs = re.findall(r"COMPOSE_PROFILES=([a-z,]+)", _text(rel))
        assert runs, rel
        for profiles in runs:
            assert profiles.split(",") == ["collect", "data", "serve"], rel
            assert tunnel not in profiles.split(","), rel
