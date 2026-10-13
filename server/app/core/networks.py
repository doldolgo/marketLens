"""망(network) 모델과 망 맞추기 규칙 — 스펙 006 §3.6.

wallet_status(조회)와 spreads(행 판정)가 같이 쓰므로 core 에 둔다.
원칙: 국내 거래소는 대부분 코인이 망 하나라 국내 망이 기준이다.
다른 망을 같다고 하는 미탐은 돈이 나가므로 **애매하면 unknown**.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, NamedTuple, Protocol

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
    # 비트겟은 망 코드를 규격명·붙여 쓴 이름으로 준다 (S3 원문 2026-09-25) — 국내 토큰과 같게
    "erc20": "ethereum",
    "bep20": "bsc",
    "trc20": "tron",
    "cap20": "chiliz",
    "arbitrumone": "arbitrum",
    # avalanche 로 풀지 않는다 — 이름이 AVAX 뿐인 국내 망은 C-Chain 인지 몰라 접두사 규칙으로 unknown 에 둔다
    "avaxc": "avalanchec",
}

# 확인된 동일 체인 표 — 한 묶음 = 같은 체인을 뜻하는 토큰 집합들. 규칙을 느슨하게 푸는 대신 이 표를 늘린다 (§3.6-3)
_EQUIV_CLASSES: tuple[tuple[frozenset[str], ...], ...] = (
    (frozenset({"metal", "l2"}), frozenset({"metal", "dao", "l2"})),
    # 빗썸은 L2 를 `<체인>_ETH` 코드로 주고 표시명이 없다 → {체인, ethereum}
    (frozenset({"base"}), frozenset({"base", "ethereum"})),
    (
        frozenset({"arbitrum"}),
        frozenset({"arbitrum", "one"}),
        frozenset({"arbitrum", "ethereum"}),
    ),
    (frozenset({"optimism"}), frozenset({"optimism", "ethereum"})),
    # 빗썸 BSC · 비트겟 BEP20(별칭) ↔ 업비트·바이낸스 "BNB Smart Chain"
    (frozenset({"bsc"}), frozenset({"bnb", "smart"})),
    # 업비트·바이낸스 "Avalanche C-Chain" ↔ 비트겟 "AVAXC-Chain"(별칭)
    (frozenset({"avalanche", "c"}), frozenset({"avalanchec"}), frozenset({"cavax"})),
    # 업비트·바이낸스 "NEO N3" ↔ 비트겟 "NEO3"
    (frozenset({"neo", "n3"}), frozenset({"neo3"})),
    # 약칭 ↔ 풀네임 — 국내(주로 빗썸)는 코드를 그대로 이름으로 주고 해외는 체인 이름을 준다 (S3 원문 2026-09-27)
    (frozenset({"allo"}), frozenset({"allora"})),
    (frozenset({"apt"}), frozenset({"aptos"})),
    (frozenset({"kat"}), frozenset({"katana"})),
    (frozenset({"mon"}), frozenset({"monad"})),
    (frozenset({"inj"}), frozenset({"injective"})),
    (frozenset({"waxp"}), frozenset({"wax"})),
    (frozenset({"baby"}), frozenset({"babylon"}), frozenset({"babylon", "genesis"})),
    (frozenset({"core"}), frozenset({"coredao"})),
    (frozenset({"near"}), frozenset({"nearprotocol"})),
    (frozenset({"ont"}), frozenset({"ontology"})),
    (frozenset({"stellar"}), frozenset({"stellarlumens"}), frozenset({"xlm"})),
    (
        frozenset({"zksync", "era"}),
        frozenset({"zksyncera"}),
        frozenset({"zk", "ethereum"}),
    ),
    (
        frozenset({"assethub", "polkadot"}),
        frozenset({"asset", "hub", "polkadot"}),
        frozenset({"polkadotassethub"}),
        frozenset({"dot"}),
    ),
    # 2026-09-27 배포 뒤 "네트워크 다름"(absent) 59행 대조 — 토큰이 하나도 안 겹쳐 absent 로 떨어지던 약칭 ↔ 체인 이름
    (frozenset({"0g"}), frozenset({"zero", "gravity"})),
    (frozenset({"ada"}), frozenset({"cardano"})),
    (frozenset({"ar"}), frozenset({"arweave"})),
    (frozenset({"cc"}), frozenset({"canton"})),
    (
        frozenset({"ksm"}),
        frozenset({"asset", "hub", "kusama"}),
        frozenset({"assethubkusama"}),
    ),
    (frozenset({"polyx"}), frozenset({"polymesh"})),
    (frozenset({"pros"}), frozenset({"pharos"})),
    (frozenset({"s"}), frozenset({"sonic"})),
    (frozenset({"somi"}), frozenset({"somnia"})),
    (frozenset({"stx"}), frozenset({"stacks"})),
    (frozenset({"tia"}), frozenset({"celestia"})),
    (frozenset({"vet"}), frozenset({"vechain"})),
    (frozenset({"xpl"}), frozenset({"plasma"})),
    # 빗썸 L2 코드 `<체인>_ETH`·`<체인>_BTC`·`<체인>_USDT` ↔ 해외 체인 이름 — 모체인 토큰이 붙어 겹침 규칙(4)에 걸리므로 표로 못 박는다
    (
        frozenset({"manta", "ethereum"}),
        frozenset({"manta"}),
        frozenset({"manta", "pacific"}),
        frozenset({"mantanetwork"}),
    ),
    (frozenset({"mega", "ethereum"}), frozenset({"mega"}), frozenset({"megaeth"})),
    (frozenset({"scroll", "ethereum"}), frozenset({"scroll"})),
    (frozenset({"blast", "ethereum"}), frozenset({"blast"})),
    (frozenset({"taiko", "ethereum"}), frozenset({"taiko"})),
    (frozenset({"merl", "bitcoin"}), frozenset({"merlin"})),
    (frozenset({"stable", "usdt"}), frozenset({"stable"})),
)
# 토큰 집합 → 묶음 번호 (조회용)
_EQUIV_INDEX: dict[frozenset[str], int] = {
    tokens: i for i, cls in enumerate(_EQUIV_CLASSES) for tokens in cls
}

_PAREN_RE = re.compile(r"\([^)]*\)")
_SPLIT_RE = re.compile(r"[^0-9a-z]+")
# 정규화 결과를 이름별로 기억하는 칸 수 — 망 이름은 다섯 거래소를 합쳐 수백 종이고 60초에 한 번만 바뀐다
_NAME_CACHE_SIZE = 4096


@lru_cache(maxsize=_NAME_CACHE_SIZE)
def normalize_name(name: str) -> frozenset[str]:
    """망 이름 → 토큰 집합 — 스펙 006 §3.6 정규화 6단계. 토큰 순서는 무시한다.

    결과를 이름별로 기억한다 — 순수 함수라 무효화가 없고(별칭·불용어는 상수), 틱과 표가 매초 같은 이름을
    수천 번 정규화한다. 결과는 불변 집합이라 나눠 써도 안전하다.
    """
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
    # 국내 이름이 전부 불용어(예 Mainnet)면 정보가 없다 — 빈 집합끼리의 "완전 일치"는
    # 아무 망이나 맞다는 뜻이 되고, absent 라고 말할 근거도 없으니 unknown (§3.6-2)
    if not dom_tokens:
        return "unknown", None
    foreign_tokens = [(f, normalize_name(f.name)) for f in foreign]

    # 2. 토큰 집합 완전 일치
    for f, ft in foreign_tokens:
        if ft == dom_tokens:
            return "matched", f
    # 3. 토큰 정렬-결합 문자열 일치 (`AssetHub Polkadot` ↔ `Asset Hub Polkadot`) + 동일 체인 표
    dom_joined = "".join(sorted(dom_tokens))
    dom_class = _EQUIV_INDEX.get(dom_tokens)
    for f, ft in foreign_tokens:
        if not ft:
            continue
        if "".join(sorted(ft)) == dom_joined:
            return "matched", f
        if dom_class is not None and _EQUIV_INDEX.get(ft) == dom_class:
            return "matched", f

    # 4. 못 찾음 — 토큰이 하나라도 겹치거나 길이 3+ 토큰의 접두사 관계(enj↔enjin)면 unknown
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


# --- 행 1개의 입출금 판정 (스펙 006 §3.7 + 024 §3.2) ---


class WalletRow(Protocol):
    """판정이 읽는 행의 일부 — core.models.Row 가 만족한다. models 가 이 모듈을 import 하므로 역참조는 Protocol 로."""

    deposit_enabled: bool | None
    withdrawal_enabled: bool | None
    networks: list[Network]


class WalletFields(NamedTuple):
    """행 1개의 판정 6값 — 006 §3.7 의 다섯 + 해외 망 이름(024 §3.2). 앞 다섯이 `/spreads` 5필드 순서 그대로다."""

    net_dom: str | None
    net_fx: str | None
    dep_dom: bool | None
    wd_dom: bool | None
    dep_fx: bool | None
    wd_fx: bool | None


def wallet_fields(dom_row: WalletRow, fx_row: WalletRow) -> WalletFields:
    """국내 망 기준 입출금 판정 — 스펙 006 §3.7. spreads 행과 틱(024)이 같은 함수를 부른다.

    국내 망이 기준이다. status=fail 행도 같은 규칙.
    """
    if not dom_row.networks:
        # 1. 국내 망 목록이 비면(키 없음·망 정보 없는 과도기) 코인 단위 값 그대로
        return WalletFields(
            None,
            None,
            dom_row.deposit_enabled,
            dom_row.withdrawal_enabled,
            fx_row.deposit_enabled,
            fx_row.withdrawal_enabled,
        )
    # 2. 국내 망·판정·해외 망을 고른다 (§3.6 tie-break)
    dom_net, verdict, fx_net = pick_domestic(dom_row.networks, fx_row.networks)
    net_fx: str | None = None
    dep_fx: bool | None
    wd_fx: bool | None
    if verdict == "matched" and fx_net is not None:
        # 3. 맞춘 해외 망의 값 — 해외 망 이름은 이 경우에만 안다
        net_fx = fx_net.name
        dep_fx, wd_fx = fx_net.dep, fx_net.wd
    elif verdict == "absent":
        # 4. 해외가 그 망을 안 다룸 = 옮길 길 없음
        dep_fx = wd_fx = False
    elif fx_row.networks:
        # 5. 해외 망이 있는데 못 맞춤 = 모른다고 말한다 — 코인 단위로 접으면 낙관 편향
        dep_fx = wd_fx = None
    else:
        # 5. 해외 망 정보가 아예 없으면 해외 코인 단위 값
        dep_fx = fx_row.deposit_enabled
        wd_fx = fx_row.withdrawal_enabled
    return WalletFields(dom_net.name, net_fx, dom_net.dep, dom_net.wd, dep_fx, wd_fx)


MemoKey = tuple[str, str, str]  # (dom, fx, base 대문자)
# 입력 여섯(국내·해외 망 목록 객체, 국내 입금·출금, 해외 입금·출금) + 그때의 판정
_MemoEntry = tuple[
    list[Network],
    list[Network],
    bool | None,
    bool | None,
    bool | None,
    bool | None,
    WalletFields,
]


# 회차가 맞지 않는 표가 받는 조회 함수의 자리 — 늘 None 이라 모든 조합이 입력 비교(`fields`)로 간다
_NO_ENTRIES: dict[MemoKey, _MemoEntry] = {}


class WalletMemo:
    """망 판정 조합 메모 — 006 §3.7. 틱과 같은 회차의 표(017)가 하나를 나눠 쓴다.

    (국내, 해외, 코인) 마다 직전 판정과 그 입력(두 행의 망 목록 객체와 코인 단위 입출금 4값)을 든다.
    입력이 전부 **같은 객체**면 다시 판정하지 않는다 — 입출금 캐시는 60초에 한 번 바뀌고 006 조회기는
    행에 캐시의 망 목록을 사본 없이 걸기 때문이다. 이 메모가 맞는 전제는 "행의 망 목록과 그 안의 망은
    제자리에서 고치지 않는다"(006 §3.5)이다. 회차(`rotate`)마다 직전·이번 회차에 쓰인 조합만 남는다 —
    상폐 조합은 저절로 빠진다.

    같은 회차의 표는 틱이 채운 칸을 **입력 비교 없이** 읽는다(`current_getter`). 틱(`build_tick`)과 표 사이에
    await 가 없고(TickLoop.tick 은 동기다) 입출금 반영(apply)은 틱 앞이라, 그 사이 행의 망 목록·코인 값이
    바뀌지 않기 때문이다. 틱이 칸을 안 채운 조합(빈 호가·0 가격으로 틱 자격이 없는 fail 행)만 `fields` 로
    입력을 비교해 판정한다. 표 1,840행마다 입력 여섯을 다시 견주지 않으려는 것이다(2026-10-09).
    """

    def __init__(self) -> None:
        self._prev: dict[MemoKey, _MemoEntry] = {}
        self._cur: dict[MemoKey, _MemoEntry] = {}
        self._round = 0

    def rotate(self) -> None:
        """새 회차 — 틱이 판정을 시작할 때 부른다. 회차 번호가 1 늘어난다."""
        self._prev = self._cur
        self._cur = {}
        self._round += 1

    @property
    def round(self) -> int:
        """지금 회차 번호 — 표(017)가 "이 회차를 연 틱 뒤의 첫 표인가" 를 가리는 표식이다."""
        return self._round

    def current_getter(
        self, round_id: int | None
    ) -> Callable[[MemoKey], _MemoEntry | None]:
        """이번 회차에 틱이 채운 칸을 읽는 함수 — 표 1장에 한 번 꺼내 행마다 부른다(행마다 메서드를 부르지 않게).

        `round_id` 가 지금 회차와 같을 때만 이번 회차 칸을 그대로 읽는다. 다르거나 None 이면(회차를 모르는
        호출 — 테스트·`build_spreads`, 같은 회차의 두 번째 표) 늘 None 을 돌려 모든 조합이 `fields` 의 입력
        비교로 간다 — 틱 없이 입출금이 바뀐 뒤 부른 표가 낡은 판정을 읽지 않게 하는 표당 비교 1번이다.
        돌려받은 함수의 칸 끝자리(`entry[6]`)가 판정이다.
        """
        if round_id is not None and round_id == self._round:
            return self._cur.get
        return _NO_ENTRIES.get

    def fields(
        self, key: MemoKey, dom_row: WalletRow, fx_row: WalletRow
    ) -> WalletFields:
        """`wallet_fields(dom_row, fx_row)` 와 같은 값 — 입력이 직전과 같은 객체면 기억한 결과를 돌려준다."""
        entry = self._cur.get(key)
        if entry is None:
            entry = self._prev.get(key)
        dom_nets = dom_row.networks
        fx_nets = fx_row.networks
        dom_dep = dom_row.deposit_enabled
        dom_wd = dom_row.withdrawal_enabled
        fx_dep = fx_row.deposit_enabled
        fx_wd = fx_row.withdrawal_enabled
        if not (
            entry is not None
            and entry[0] is dom_nets
            and entry[1] is fx_nets
            and entry[2] is dom_dep
            and entry[3] is dom_wd
            and entry[4] is fx_dep
            and entry[5] is fx_wd
        ):
            entry = (
                dom_nets,
                fx_nets,
                dom_dep,
                dom_wd,
                fx_dep,
                fx_wd,
                wallet_fields(dom_row, fx_row),
            )
        self._cur[key] = entry
        return entry[6]
