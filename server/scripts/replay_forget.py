"""IP 하나의 화면 이동 기록 지우기 — 처리방침 8절의 삭제 요청 (스펙 078 §3.6).

실행 (collect 박스의 수집기 컨테이너 안 — Redis 와 S3 자격이 둘 다 닿는 곳):
    docker exec marketlens-server python -m scripts.replay_forget --ip <IP> [--dry-run]
`rp:ip:<IP>` 의 조각마다 S3 객체를 먼저 지우고(실패하면 Redis 는 그대로 두고 멈춘다 — 다시 실행하면 된다), 그다음 Redis 의
머리·묶음·`rp:recent`·`rp:todo` 항목과 그 집합을 지운다. 찍는 것은 지운 수뿐이다(IP·sid 를 찍지 않는다).
"""

import argparse
import asyncio
import sys
from typing import Protocol

from app.core.config import get_settings
from app.core.redis_bus import RedisBus
from app.core.s3 import S3Uploader
from app.features.replay.archive import object_key
from app.features.replay.models import valid_ip_query

SCAN = 1_000_000


class Deleter(Protocol):
    def delete(self, key: str) -> None: ...


async def forget(
    bus: RedisBus, store: Deleter | None, ip: str, dry_run: bool
) -> tuple[int, int]:
    """→ (조각 수, 지운 S3 객체 수). 옮긴 조각이 있는데 버킷이 없으면 RuntimeError(아무것도 지우지 않는다)."""
    sids = await bus.replay_ip(ip, SCAN)
    metas = await bus.replay_metas(sids)
    keys = [
        object_key(sid, int(meta["first"]))
        for sid, meta in zip(sids, metas, strict=True)
        if "first" in meta and "keep" in meta
    ]
    archived = any("arch" in meta for meta in metas)
    if dry_run:
        return len(sids), len(keys)
    if store is None and archived:
        raise RuntimeError(
            "S3_BUCKET 이 없어 옮긴 기록을 지울 수 없다 — 아무것도 지우지 않았다"
        )
    if store is not None:
        for key in keys:
            await asyncio.to_thread(store.delete, key)
    await bus.replay_forget(ip)
    return len(sids), len(keys) if store is not None else 0


async def _run(ip: str, dry_run: bool) -> int:
    settings = get_settings()
    bus = RedisBus.from_url(settings.redis_url, password=settings.redis_password)
    store = (
        S3Uploader(bucket=settings.s3_bucket, region=settings.s3_region)
        if settings.s3_bucket
        else None
    )
    try:
        sessions, objects = await forget(bus, store, ip, dry_run)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    finally:
        await bus.aclose()
    verb = "지울" if dry_run else "지운"
    print(f"{verb} 조각 {sessions}개 · S3 객체 {objects}개")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="IP 하나의 화면 이동 기록 지우기 (078 §3.6)"
    )
    parser.add_argument("--ip", required=True, help="지울 IP(정규 글자) 또는 (없음)")
    parser.add_argument("--dry-run", action="store_true", help="지우지 않고 셈만")
    args = parser.parse_args()
    if not valid_ip_query(args.ip):
        print("ip 는 IPv4·IPv6 주소(정규 글자) 또는 (없음)이어야 한다", file=sys.stderr)
        return 2
    return asyncio.run(_run(args.ip, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
