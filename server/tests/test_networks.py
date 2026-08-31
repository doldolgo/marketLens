"""망 정규화·판정·tie-break — 스펙 006 §3.6·§4. core 순수 함수라 네트워크 없음."""

from app.core.networks import Network, match_network, normalize_name, pick_domestic


def net(code: str, name: str, dep: bool = True, wd: bool = True) -> Network:
    return Network(code=code, name=name, dep=dep, wd=wd)


# --- 정규화 (§4) ---


def test_normalize_paren_comment_removed() -> None:
    assert normalize_name("Ethereum (ERC20)") == {"ethereum"}


def test_normalize_stopword_removed() -> None:
    assert normalize_name("Polygon POS") == {"polygon"}


def test_normalize_alias_makes_avax_equal_avalanche() -> None:
    assert normalize_name("AVAX C-Chain") == normalize_name("Avalanche C-Chain")
    assert normalize_name("AVAX C-Chain") == {"avalanche", "c"}


# --- 판정 (§4) ---


def test_code_match_is_matched() -> None:
    foreign = [net("ARBITRUM", "Arbitrum One"), net("ETH", "Ethereum (ERC20)")]
    verdict, matched = match_network(net("ETH", "Ethereum"), foreign)
    assert verdict == "matched"
    assert matched is foreign[1]


def test_sei_vs_seievm_is_unknown() -> None:
    # 가장 중요한 케이스 — {sei} ⊂ {sei, evm} 겹침이면 같다고 하지 않는다
    verdict, matched = match_network(net("SEI", "Sei"), [net("SEIEVM", "Sei EVM")])
    assert verdict == "unknown"
    assert matched is None


def test_qkc_vs_eth_is_absent() -> None:
    verdict, _ = match_network(
        net("QKC", "Quarkchain"), [net("ETH", "Ethereum (ERC20)")]
    )
    assert verdict == "absent"


def test_empty_foreign_list_is_unknown() -> None:
    # 정보 없음 ≠ 그 망 없음
    verdict, _ = match_network(net("ETH", "Ethereum"), [])
    assert verdict == "unknown"


def test_token_boundary_ignored_assethub_polkadot() -> None:
    verdict, _ = match_network(
        net("ASSETHUB", "AssetHub Polkadot"), [net("DOTSM", "Asset Hub Polkadot")]
    )
    assert verdict == "matched"


def test_equivalence_table_metal_l2_both_directions() -> None:
    verdict, _ = match_network(net("METALL2", "Metal L2"), [net("X", "Metal DAO L2")])
    assert verdict == "matched"
    verdict, _ = match_network(net("X", "Metal DAO L2"), [net("METALL2", "Metal L2")])
    assert verdict == "matched"


def test_prefix_of_long_token_is_unknown() -> None:
    # kat ↔ katana — 길이 3+ 토큰의 접두사 관계는 absent 로 못 박지 않는다
    verdict, _ = match_network(net("KAT", "Kat"), [net("KATANA", "Katana")])
    assert verdict == "unknown"


# --- tie-break (§4) ---


def test_tiebreak_prefers_transferable_path() -> None:
    # 국내 망 2개 중 두 번째만 "국내 입금 ok + 해외 출금 ok" 이면 두 번째를 고른다
    dom = [net("AAA", "Aaa", dep=True, wd=True), net("BBB", "Bbb", dep=True, wd=False)]
    foreign = [
        net("AAA", "Aaa", dep=True, wd=False),
        net("BBB", "Bbb", dep=True, wd=True),
    ]
    chosen, verdict, matched = pick_domestic(dom, foreign)
    assert chosen is dom[1]
    assert verdict == "matched"
    assert matched is foreign[1]


def test_tiebreak_falls_back_to_first_matched() -> None:
    # 옮길 수 있는 길이 없으면 matched 인 첫 망 — 막혀 있어도 맞는 망을 보여준다
    dom = [net("AAA", "Aaa", dep=False, wd=True), net("BBB", "Bbb", dep=False, wd=True)]
    foreign = [
        net("BBB", "Bbb", dep=True, wd=False),
        net("AAA", "Aaa", dep=True, wd=False),
    ]
    chosen, verdict, matched = pick_domestic(dom, foreign)
    assert chosen is dom[0]
    assert verdict == "matched"
    assert matched is foreign[1]


def test_tiebreak_falls_back_to_first_domestic_verdict() -> None:
    dom = [net("QKC", "Quarkchain"), net("XYZ", "Xyzchain")]
    foreign = [net("ETH", "Ethereum (ERC20)")]
    chosen, verdict, matched = pick_domestic(dom, foreign)
    assert chosen is dom[0]
    assert verdict == "absent"
    assert matched is None
