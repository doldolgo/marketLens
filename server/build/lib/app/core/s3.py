"""S3 업로더 — 연결·쓰기 공유 인프라 (스펙 010 §3.3).

boto3 를 import 하는 곳은 이 모듈뿐이다. snapshot 루프는 `put(key, body)` 시그니처에만
의존한다 — 테스트는 같은 시그니처의 fake 를 쓴다. 자격증명은 코드·env 에 두지 않고
SDK 기본 탐색 순서(로컬 ~/.aws, EC2 는 IAM 역할)를 그대로 쓴다.
모든 실패는 `S3UnavailableError` 하나로 모은다: 호출자는 원인 구분 없이
"저장 실패"(로그 후 다음 회차 재시도)로만 다룬다.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger("marketlens.s3")

# 요청당 타임아웃·재시도 — 한 회차(재시도 포함)가 다음 회차 60초를 넘기지 않게 (§3.3)
_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 10.0
_MAX_ATTEMPTS = 3  # 첫 시도 1 + SDK 재시도 2회

# 객체 메타데이터 — gzip 압축한 JSON Lines (§3.4)
_CONTENT_TYPE = "application/x-ndjson"
_CONTENT_ENCODING = "gzip"


class S3UnavailableError(Exception):
    """S3 연결·업로드 실패 → snapshot 루프는 로그 후 다음 회차 재시도."""


@dataclass
class S3Uploader:
    """실제 S3 접속 — 생성은 연결하지 않는다(lazy). 실패는 전부 S3UnavailableError."""

    bucket: str
    region: str
    _client: Any = field(default=None, init=False, repr=False)

    def _inner(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "s3",
                region_name=self.region,
                config=BotoConfig(
                    connect_timeout=_CONNECT_TIMEOUT,
                    read_timeout=_READ_TIMEOUT,
                    retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
                ),
            )
        return self._client

    def head_bucket(self) -> bool:
        """접근 확인 — 실패해도 예외 없이 False (기동 시 에러 로그 1줄용, §3.3)."""
        try:
            self._inner().head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False

    def put(self, key: str, body: bytes) -> None:
        """객체 1개 업로드 — 전부 성공 또는 예외(전부 없음, §3.4)."""
        try:
            self._inner().put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=_CONTENT_TYPE,
                ContentEncoding=_CONTENT_ENCODING,
            )
        except Exception as exc:
            raise S3UnavailableError(f"S3 업로드 실패: {exc}") from exc

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
