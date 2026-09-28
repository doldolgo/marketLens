"""관측 설정 계약 — caddy 접속 로그·compose·배포·nginx (스펙 027 §3.2·§4).

test_deploy.py 와 같은 방식이다: Docker 없는 CI 에서 설정 파일을 읽어 단언한다. 실제로 caddy 를 띄워
줄을 보는 검증은 027 §5 의 로컬 Docker 명령이다. Caddyfile 은 CI 가 문자열로만 본다 — 문법 오류는
여기서 못 잡으므로 Caddyfile 을 고친 PR 은 로컬 `caddy validate` 결과를 본문에 적는다(§3.8).
"""

from tests.test_deploy import ROOT, _text, _yaml


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
