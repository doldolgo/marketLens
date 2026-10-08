"""앱 상수와 설정.

.env 는 pydantic-settings 가 런타임에 읽는다 — 코드·문서에 값이 나타나지 않는다.
스펙 001 은 env 값을 쓰지 않지만, 후속 스펙(005 Influx·006 거래소 키)이 같은 구조를 쓴다.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "MarketLens Backend"
APP_VERSION = "0.1.0"
USER_AGENT = f"marketlens-server/{APP_VERSION}"

# 거래소 5곳 고정 순서 — 스펙 001 §3.3·020 §2. 국내 둘은 KRW, 바이낸스·바이빗·비트겟은 USDT 마켓
EXCHANGES = ("upbit", "bithumb", "binance", "bybit", "bitget")
DOMESTIC_EXCHANGES = ("upbit", "bithumb")
# perp 원천 고정 순서 — 스펙 046 §3.1. 모든 목록·판정·카드에서 현물 거래소 전부 뒤에 이 순서로 온다 (047 뒤 hyperliquid_perp)
PERP_SOURCES = ("binance_perp", "bybit_perp", "bitget_perp")
# 틱 판정·/health/collect·/refresh 가 도는 전체 목록 — 현물 5곳 뒤 perp 원천 3개 (046 §3.8)
COLLECT_SOURCES = EXCHANGES + PERP_SOURCES

# 거래소 REST(마켓 목록) 타임아웃(초)·WebSocket 핸드셰이크 타임아웃(초) — 스펙 001 §3.1
EXCHANGE_TIMEOUT_TOTAL = 3.0
EXCHANGE_TIMEOUT_CONNECT = 1.5
WS_OPEN_TIMEOUT = 5.0

# 기동을 마친 뒤의 순환 GC 세대 임계 — 스펙 001 §3.1·016 §3.1 (2026-09-28 결정). 기본 (700, 10, 10) 이면 행 교체가
# 초당 수천 번인 수집에서 전체 수집이 거의 매초 돌아 루프를 수십 ms 씩 멈춘다. 첫 값을 1만대로 올리면
# 자동 전체 수집이 사실상 멈춰 오래 살다 버려진 순환 객체가 영영 안 치워지므로 이 값에서 멈춘다.
GC_THRESHOLDS: tuple[int, int, int] = (2000, 10, 10)

# HTTP 응답 gzip 레벨 — 앱 전역 미들웨어와 미리 압축해 두는 응답(018 GET /spreads·013 사건·014 봉)이 같이 쓴다
# (스펙 001 §3.1, 2026-09-28 결정). 9 는 6 보다 압축 CPU 가 2.5배인데 크기는 1~5% 작을 뿐이고, 수집 박스는 코어가
# 1개라 그 CPU 를 틱·수신과 나눠 쓴다. 허브 프레임(017)·원문 객체(010)도 같은 이유로 6 이다.
GZIP_LEVEL = 6

# 처리방침 v2 가 시행되는 날(KST 날짜) — 스펙 037 §3.1. 접속 요약의 30일 창과 (가린 IP, 브라우저 정보) 짝을 쓰는 처리
# (하루 한 번 세기·나라·망 종류)는 이 날 00:00 Asia/Seoul 부터의 기록에만 한다(038·039 의 날짜 게이트) — 개정 전 기록에
# 새 목적을 붙이지 않으려는 것이다. 게이트가 여럿이라 한 곳에 두고, test_privacy 가 방침 HTML 의 시행일(<time>·이력)·
# sitemap 과 묶는다. 다음 개정 때도 이 값은 그대로 둔다 — v2 기능이 열린 날이고, 새 판의 시행일은 HTML·sitemap·테스트만 바꾼다
PRIVACY_V2_EFFECTIVE = "2026-10-11"


class Settings(BaseSettings):
    """server/.env 의 문서화된 키(dev-setup.md). 전부 선택값이라 없어도 앱은 뜬다."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 프로세스 역할(016 §3.1) — collector 는 오늘의 전체 동작, api 는 Influx 조회 전용(compose 가 준다).
    # 허용값 밖이면 설정을 읽는 순간(앱 객체 생성 전) 실패한다 — 잘못 뜬 채로 수집이 두 벌 돌지 않게.
    role: Literal["collector", "api"] = "collector"
    influx_url: str = "http://localhost:8086"
    influx_token: str | None = None
    # 틱 버퍼 Redis(009) — compose 안에서는 redis://redis:6379/0 으로 덮는다. 불달이어도 앱은 뜬다.
    redis_url: str = "redis://localhost:6379/0"
    # S3 원문 아카이브(010) — 버킷이 없으면 원문 싱크가 무동작이고 앱은 뜬다.
    # AWS 키는 env 에 두지 않는다: SDK 기본 탐색(~/.aws, EC2 IAM 역할)을 쓴다.
    s3_bucket: str | None = None
    s3_region: str = "ap-northeast-2"
    refresh_token: str | None = None
    upbit_api_key: str | None = None
    upbit_secret_key: str | None = None
    binance_api_key: str | None = None
    binance_secret_key: str | None = None
    bybit_api_key: str | None = None
    bybit_secret_key: str | None = None
    # Slack Incoming Webhook(025). 없으면 알림 기능 전체가 꺼진다 — 로컬·테스트 기본
    slack_webhook_url: str | None = None
    # StatsD 수신 주소 `host:port`(027) — api 역할만 WS 접속 수 게이지를 보낸다. 비면 끔(로컬·테스트 기본)
    statsd_addr: str | None = None
    # 관리자 AWS 요약의 리전(034) — compose 가 server 에만 준다(server/.env 에 두지 않는다). 비면 AWS 를 부르지 않는다
    admin_aws_region: str | None = None
    # 접속 요약이 읽는 caddy 로그 디렉터리(035) — compose 가 api 에만 준다(server/.env 에 두지 않는다). 비면 unconfigured
    access_log_dir: str | None = None
    # Clarity Data Export 토큰(035) — 사람이 serve 의 server/.env 에 넣는 비밀. 비면 Clarity 부분은 unconfigured·호출 0
    clarity_api_token: str | None = None

    @field_validator("s3_region", mode="before")
    @classmethod
    def _blank_region_is_default(cls, value: object) -> object:
        """`S3_REGION=` 처럼 비워 두면 기본 리전이다 — 빈 문자열은 boto3 가 즉시 거부한다 (010 §3.2)."""
        if isinstance(value, str) and not value.strip():
            return "ap-northeast-2"
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
