"""망(network) 모델과 망 맞추기 규칙 — 스펙 006 §3.6.

wallet_status(조회)와 spreads(행 판정)가 같이 쓰므로 core 에 둔다.
원칙: 국내 거래소는 대부분 코인이 망 하나라 국내 망이 기준이다.
다른 망을 같다고 하는 미탐은 돈이 나가므로 **애매하면 unknown**.
"""

import re
from dataclasses import dataclass
from typing import Literal

Verdict = Literal["matched", "unknown", "absent"]


@dataclass
class Network:
    """거래소 응답의 망 1개. 망 코드가 빈 행은 목록에 넣지 않는다 (스펙 006 §3.1)."""

    code: str  # 망 코드, 대문자 (예 ETH)
    name: str  # 표시명 — 응답에 없으면 code
    dep: bool  # 이 망의 입금 가능
    wd: bool  # 이 망의 출금 가능


# 정규화 불용어·별칭 — 스펙 006 §3.6 순서 4·5
_STOPWORDS = frozenset(
    {"network", "networks", "chain", "mainnet", "protocol", "pos", "token", "coin"}
)
_ALIASES = {
    "avax": "avalanche",
    "eth": "ethereum",
    "btc": "bitcoin",
    "matic": "polygon",
    "pol": "polygon",
    "sol": "solana",
    "trx": "tron",
    "arb": "arbitrum",
    "op": "optimism",
}

# 확인된 동일 체인 쌍 표 (양방향) — 규칙을 느슨하게 푸는 대신 이 표를 늘린다 (§3.6-3)
_EQUIV_PAIRS: frozenset[frozenset[frozenset[str]]] = frozenset(
    {
        frozenset({frozenset({"metal", "l2"}), frozenset({"metal", "dao", "l2"})}),
    }
)

_PAREN_RE = re.compile(r"\([^)]*\)")
_SPLIT_RE = re.compile(r"[^0-9a-z]+")


def normalize_name(name: str) -> frozenset[str]:
    """망 이름 → 토큰 집합 — 스펙 006 §3.6 정규화 6단계. 토큰 순서는 무시한다."""
    lowered = name.lower()
    without_paren = _PAREN_RE.sub(" ", lowered)  # 괄호 주석 제거는 분리보다 먼저
    tokens = [t for t in _SPLIT_RE.split(without_paren) if t]
    kept = [t for t in tokens if t not in _STOPWORDS]
    return frozenset(_ALIASES.get(t, t) for t in kept)


def match_network(
    dom: Network, foreign: list[Network]
) -> tuple[Verdict, Network | None]:
    """국내 망 1개 vs 해외 망 목록 판정 — 순서대로 첫 히트 (스펙 006 §3.6)."""
    # 0. 해외 망 목록이 비면 unknown — 정보 없음 ≠ 그 망 없음
    if not foreign:
        return "unknown", None

    # 1. 코드 대문자 일치. 국내 코드가 비면 건너뛴다.
    if dom.code:
        for f in foreign:
            if f.code and f.code == dom.code:
                return "matched", f

    dom_tokens = normalize_name(dom.name)
    foreign_tokens = [(f, normalize_name(f.name)) for f in foreign]

    if dom_tokens:  # 빈 토큰 집합끼리의 "완전 일치"는 아무 망이나 맞다는 뜻이 된다
        # 2. 토큰 집합 완전 일치
        for f, ft in foreign_tokens:
            if ft == dom_tokens:
                return "matched", f
        # 3. 토큰 정렬-결합 문자열 일치 (`AssetHub Polkadot` ↔ `Asset Hub Polkadot`) + 동일 체인 쌍 표
        dom_joined = "".join(sorted(dom_tokens))
        for f, ft in foreign_tokens:
            if not ft:
                continue
            if "".join(sorted(ft)) == dom_joined:
                return "matched", f
            if frozenset({dom_tokens, ft}) in _EQUIV_PAIRS:
                return "matched", f

    # 4. 못 찾음 — 토큰이 하나라도 겹치거나 길이 3+ 토큰의 접두사 관계(kat↔katana)면 unknown
    for _, ft in foreign_tokens:
        if dom_tokens & ft:
            return "unknown", None
        for a in dom_tokens:
            for b in ft:
                if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
                    return "unknown", None
    return "absent", None


def pick_domestic(
    dom_networks: list[Network], foreign_networks: list[Network]
) -> tuple[Network, Verdict, Network | None]:
    """국내 망이 여럿일 때 tie-break — 스펙 006 §3.6 마지막.

    (고른 국내 망, 판정, matched 면 맞춘 해외 망) 을 돌려준다.
    호출 전제: dom_networks 는 비어 있지 않다 (§3.7-1 이 빈 경우를 먼저 처리한다).
    """
    judged = [(dn, *match_network(dn, foreign_networks)) for dn in dom_networks]
    # 1. matched 이고 국내 입금 ok + 해외 출금 ok (해외→국내로 실제 옮길 수 있는 길)인 첫 망
    for dn, verdict, fn in judged:
        if verdict == "matched" and dn.dep and fn is not None and fn.wd:
            return dn, verdict, fn
    # 2. matched 인 첫 망 — 막혀 있어도 맞는 망을 보여준다
    for dn, verdict, fn in judged:
        if verdict == "matched":
            return dn, verdict, fn
    # 3. 첫 국내 망의 판정
    return judged[0]
