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


def test_normalize_bitget_standard_names_alias_to_chain() -> None:
    # 비트겟은 망 코드를 규격명으로 준다 (S3 원문 2026-09-25) — 국내 코드·이름과 같은 토큰으로
    assert normalize_name("ERC20") == normalize_name("Ethereum") == {"ethereum"}
    assert normalize_name("BEP20") == normalize_name("BSC") == {"bsc"}
    assert normalize_name("TRC20") == normalize_name("Tron") == {"tron"}
    assert normalize_name("CAP20") == normalize_name("Chiliz Chain") == {"chiliz"}


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


def test_all_stopword_domestic_name_is_unknown() -> None:
    # 국내 이름이 전부 불용어면 정보가 없다 — 코드가 안 맞으면 absent 로 못 박지 않고 unknown
    for foreign_name in ("Ethereum", "Network"):
        verdict, matched = match_network(
            net("MAIN", "Mainnet"), [net("ETH", foreign_name)]
        )
        assert verdict == "unknown"
        assert matched is None
    # 코드가 맞으면 이름과 무관하게 matched (규칙 1 이 먼저)
    verdict, matched = match_network(net("ETH", "Mainnet"), [net("ETH", "Ethereum")])
    assert verdict == "matched"
    assert matched is not None


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


# --- 실서버 원문 쌍 (2026-09-25 S3 raw — 스펙 006 §3.6 별칭·동일 체인 표) ---


def test_bithumb_eth_vs_bitget_erc20_is_matched() -> None:
    verdict, matched = match_network(
        net("ETH", "ETH"), [net("ERC20", "ERC20"), net("BEP20", "BEP20")]
    )
    assert verdict == "matched"
    assert matched is not None and matched.code == "ERC20"


def test_upbit_ethereum_vs_bitget_erc20_is_matched() -> None:
    verdict, _ = match_network(net("ETH", "Ethereum"), [net("ERC20", "ERC20")])
    assert verdict == "matched"


def test_bsc_vs_bitget_bep20_is_matched_both_domestic_spellings() -> None:
    foreign = [net("ERC20", "ERC20"), net("BEP20", "BEP20")]
    for dom in (net("BSC", "BSC"), net("BSC", "BNB Smart Chain")):
        verdict, matched = match_network(dom, foreign)
        assert verdict == "matched", dom
        assert matched is not None and matched.code == "BEP20"


def test_trx_vs_bitget_trc20_is_matched() -> None:
    for dom in (net("TRX", "TRX"), net("TRX", "Tron")):
        assert match_network(dom, [net("TRC20", "TRC20")])[0] == "matched"


def test_bithumb_base_eth_vs_base_is_matched() -> None:
    assert (
        match_network(net("BASE_ETH", "BASE_ETH"), [net("BASE", "Base")])[0]
        == "matched"
    )  # 바이낸스
    assert (
        match_network(net("BASE_ETH", "BASE_ETH"), [net("BASE", "BASE")])[0]
        == "matched"
    )  # 비트겟


def test_bithumb_arb_eth_vs_arbitrum_one_is_matched() -> None:
    dom = net("ARB_ETH", "ARB_ETH")
    binance = [net("ARBITRUM", "Arbitrum One"), net("ETH", "Ethereum (ERC20)")]
    verdict, matched = match_network(dom, binance)
    assert verdict == "matched"
    assert matched is binance[0]
    bitget = [net("ERC20", "ERC20"), net("ARBITRUMONE", "ArbitrumOne")]
    verdict, matched = match_network(dom, bitget)
    assert verdict == "matched"
    assert matched is bitget[1]


def test_bithumb_op_eth_vs_optimism_is_matched() -> None:
    assert (
        match_network(net("OP_ETH", "OP_ETH"), [net("OPTIMISM", "Optimism")])[0]
        == "matched"
    )


def test_upbit_avalanche_c_chain_vs_bitget_avaxc_chain_is_matched() -> None:
    foreign = [net("AVAXX-CHAIN", "AVAXX-Chain"), net("AVAXC-CHAIN", "AVAXC-Chain")]
    verdict, matched = match_network(net("AVAX", "Avalanche C-Chain"), foreign)
    assert verdict == "matched"
    assert matched is foreign[1]


def test_bithumb_avax_without_chain_name_vs_bitget_avaxc_is_unknown() -> None:
    # 이름이 AVAX 뿐이면 C-Chain 인지 모른다 — absent(옮길 길 없음)라고도 못 하므로 unknown
    foreign = [net("AVAXX-CHAIN", "AVAXX-Chain"), net("AVAXC-CHAIN", "AVAXC-Chain")]
    assert match_network(net("AVAX", "AVAX"), foreign)[0] == "unknown"


def test_upbit_neo_n3_vs_bitget_neo3_is_matched() -> None:
    assert match_network(net("NEO", "NEO N3"), [net("NEO3", "NEO3")])[0] == "matched"


def test_bithumb_neo_without_chain_name_stays_unknown() -> None:
    assert match_network(net("NEO", "NEO"), [net("NEO3", "NEO N3")])[0] == "unknown"


def test_upbit_chiliz_chain_vs_bitget_cap20_is_matched() -> None:
    foreign = [net("ERC20", "ERC20"), net("CAP20", "CAP20")]
    verdict, matched = match_network(net("CHZ", "Chiliz Chain"), foreign)
    assert verdict == "matched"
    assert matched is foreign[1]


def test_sol_vs_bitget_bep20_only_is_still_absent() -> None:
    assert match_network(net("SOL", "SOL"), [net("BEP20", "BEP20")])[0] == "absent"
