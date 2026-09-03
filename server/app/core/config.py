"""앱 상수와 설정.

.env 는 pydantic-settings 가 런타임에 읽는다 — 코드·문서에 값이 나타나지 않는다.
스펙 001 은 env 값을 쓰지 않지만, 후속 스펙(005 Influx·006 거래소 키)이 같은 구조를 쓴다.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "MarketLens Backend"
APP_VERSION = "0.1.0"
USER_AGENT = f"marketlens-server/{APP_VERSION}"

# 거래소 호출 타임아웃(초) — 스펙 001 §3.1
EXCHANGE_TIMEOUT_TOTAL = 3.0
EXCHANGE_TIMEOUT_CONNECT = 1.5

# 수집 주기(초) — 한 사이클이 끝난 뒤 이만큼 쉰다. 사이클이 길어져도 겹치지 않는다.
COLLECT_INTERVAL = 1.0


class Settings(BaseSettings):
    """server/.env 의 문서화된 키(dev-setup.md). 전부 선택값이라 없어도 앱은 뜬다."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    influx_url: str = "http://localhost:8086"
    influx_token: str | None = None
    # S3 snapshot(010) — 버킷이 없으면 snapshot 루프 비활성, 앱은 뜬다.
    # AWS 키는 env 에 두지 않는다: SDK 기본 탐색(~/.aws, EC2 IAM 역할)을 쓴다.
    s3_bucket: str | None = None
    s3_region: str = "ap-northeast-2"
    refresh_token: str | None = None
    upbit_api_key: str | None = None
    upbit_secret_key: str | None = None
    binance_api_key: str | None = None
    binance_secret_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
