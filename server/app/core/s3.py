"""S3 업로더 — 거래소 원문 아카이브의 연결·쓰기 (스펙 010 §3.3).

boto3 를 import 하는 곳은 이 모듈뿐이다. 아카이브(core.raw_archive)는 `put(key, body)` 시그니처에만
의존하고, 테스트는 같은 시그니처의 fake 를 꽂는다(실제 S3 를 만지지 않는다).
자격증명은 SDK 기본 탐색(로컬 `~/.aws`, EC2 IAM 역할)뿐이다 — env 에 AWS 키를 두지 않는다.
"""

import logging

import boto3
from botocore.config import Config

logger = logging.getLogger("marketlens.s3")

# 객체가 수십 MB 라 읽기는 길게, 재시도는 SDK 2회(총 3회 시도) — §3.3
CONNECT_TIMEOUT_SEC = 3
READ_TIMEOUT_SEC = 30
MAX_ATTEMPTS = 3
CONTENT_TYPE = "application/x-ndjson"
CONTENT_ENCODING = "gzip"


class S3Uploader:
    def __init__(self, *, bucket: str, region: str) -> None:
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            region_name=region,
            config=Config(
                connect_timeout=CONNECT_TIMEOUT_SEC,
                read_timeout=READ_TIMEOUT_SEC,
                retries={"max_attempts": MAX_ATTEMPTS, "mode": "standard"},
            ),
        )

    def head_bucket(self) -> bool:
        """기동 시 1회 접근 확인 — 실패해도 예외 없이 False (에러 로그는 호출자가 1줄)."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return True
        except Exception as exc:
            logger.error("S3 버킷 접근 실패 %s: %r", self.bucket, exc)
            return False

    def put(self, key: str, body: bytes) -> None:
        """`PutObject` 1회 — gzip JSON Lines. 실패는 예외로 올려 대기열이 다음 회차에 다시 시도한다."""
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=CONTENT_TYPE,
            ContentEncoding=CONTENT_ENCODING,
        )
