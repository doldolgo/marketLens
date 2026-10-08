"""GET /flow/netflow·/flow/recent 응답 모델 — 스펙 050 §3.6·§3.7. 키는 alias 로 camelCase (health 와 같은 방식)."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _Out(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class FeedOut(_Out):
    """감지기 상태 — 꺼져 있으면 connected false·lastBlock null·lagSec null·집합·컨트랙트 수 0 (§3.6)."""

    connected: bool
    last_block: int | None
    lag_sec: int | None
    deposit_addrs: int
    hot_wallets: int
    contracts: int


class NetflowRowOut(_Out):
    """코인 1개 — 창 안 입금·출금 건수·수량, 순유입, 원화(현재가 없으면 null), 마지막 전송 블록 시각(초)."""

    symbol: str
    in_count: int
    in_amount: float
    out_count: int
    out_amount: float
    net_amount: float
    net_krw: float | None
    last_ts: int


class NetflowResponse(_Out):
    window: str
    as_of: int
    feed: FeedOut
    rows: list[NetflowRowOut]


class RecentRowOut(_Out):
    """전송 1건 — 주소는 전체 42자(축약은 화면), confirmed 는 head − block ≥ 2 (§3.3)."""

    ts: int
    block: int
    confirmed: bool
    dir: str
    symbol: str
    amount: float
    krw: float | None
    addr: str
    counterparty: str
    tx_hash: str


class RecentResponse(_Out):
    as_of: int
    head: int | None
    rows: list[RecentRowOut]
