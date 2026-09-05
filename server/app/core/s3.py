"""S3 업로더 — 거래소 원문 아카이브의 연결·쓰기 (스펙 010 §3.3).

boto3 를 import 하는 곳은 이 모듈뿐이다. 아카이브(core.raw_archive)는 `put(key, body)` 시그니처에만
의존하고, 테스트는 같은 시그니처의 fake 를 꽂는다(실제 S3 를 만지지 않는다).
자격증명은 SDK 기본 탐색(로컬 `~/.aws`, EC2 IAM 역할)뿐이다 — env 에 AWS 키를 두지 않는다.
이 모듈은 로그를 찍지 않는다 — 실패는 예외로 올리고 호출자가 1줄로 남긴다.
"""

import boto3
from botocore.config import Config

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

    def head_bucket(self) -> None:
        """기동 시 1회 접근 확인 — 실패는 예외로 올린다(호출자가 에러 로그 1줄)."""
        self._client.head_bucket(Bucket=self.bucket)

    def put(self, key: str, body: bytes) -> None:
        """`PutObject` 1회 — gzip JSON Lines. 실패는 예외로 올려 워커가 같은 객체를 다시 시도한다."""
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=CONTENT_TYPE,
            ContentEncoding=CONTENT_ENCODING,
        )
