"""망 정규화·판정·tie-break — 스펙 006 §3.6·§4. core 순수 함수라 네트워크 없음."""

from dataclasses import dataclass

import pytest

from app.core.networks import (
    Network,
    match_network,
    normalize_name,
    pick_domestic,
    wallet_fields,
)


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
    # enj ↔ enjin — 길이 3+ 토큰의 접두사 관계는 absent 로 못 박지 않는다
    verdict, _ = match_network(net("ENJ", "ENJ"), [net("ENJIN", "Enjin")])
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


# 2026-09-27 실서버 `?` 56행 — S3 원문으로 대조해 동일 체인 표에 넣은 쌍 (§3.6-3)
@pytest.mark.parametrize(
    ("dom", "fx"),
    [
        (("ALLO", "ALLO"), ("ALLORA", "Allora")),
        (("APT", "APT"), ("APTOS", "APTOS")),
        (("KAT", "KAT"), ("KATANA", "Katana")),
        (("MON", "MON"), ("MONAD", "Monad")),
        (("INJ", "INJ"), ("INJECTIVE", "INJECTIVE")),
        (("WAXP", "WAXP"), ("WAX", "WAX")),
        (("BABY", "BABY"), ("BABYLON", "Babylon")),
        (("BABY", "Babylon Genesis"), ("BABYLON", "Babylon")),
        (("CORE", "CORE"), ("COREDAO", "CoreDAO")),
        (("NEAR", "NEAR Protocol"), ("NEARPROTOCOL", "NEARProtocol")),
        (("ONT", "ONT"), ("ONTOLOGY", "Ontology")),
        (("XLM", "Stellar Network"), ("STELLARLUMENS", "StellarLumens")),
        (("ZKSYNC", "ZKsync Era"), ("ZKSYNCERA", "zkSyncEra")),
        (("DOT", "AssetHub Polkadot"), ("POLKADOTASSETHUB", "PolkadotAssetHub")),
        (("AVAX", "Avalanche C-Chain"), ("CAVAX", "CAVAX")),
        (("MANTA_ETH", "MANTA_ETH"), ("MANTA", "Manta Network")),
        (("MANTA_ETH", "MANTA_ETH"), ("MANTA", "Manta Pacific Mainnet")),
        (("MANTA_ETH", "MANTA_ETH"), ("MANTANETWORK", "MantaNetWork")),
        (("MEGA_ETH", "MEGA_ETH"), ("MEGAETH", "MegaETH")),
        (("MEGA_ETH", "MEGA_ETH"), ("MEGA", "MEGA")),
        (("SCROLL_ETH", "SCROLL_ETH"), ("SCROLL", "Scroll")),
        (("BLAST_ETH", "BLAST_ETH"), ("BLAST", "BLAST")),
        (("TAIKO_ETH", "TAIKO_ETH"), ("TAIKO", "Taiko Chain")),
        (("MERL_BTC", "MERL_BTC"), ("MERLIN", "Merlin Chain")),
        (("STABLE_USDT", "STABLE_USDT"), ("STABLE", "STABLE")),
    ],
)
def test_equivalence_table_pairs_confirmed_2026_09_27(
    dom: tuple[str, str], fx: tuple[str, str]
) -> None:
    verdict, matched = match_network(net(*dom), [net(*fx)])
    assert verdict == "matched"
    assert matched is not None and matched.code == fx[0]


# 2026-09-27 배포 뒤 "네트워크 다름" 59행 대조 — absent 로 떨어지던 약칭 ↔ 체인 이름 (§3.6-3)
@pytest.mark.parametrize(
    ("dom", "fx"),
    [
        (("0G", "0G"), ("ZEROGRAVITY", "Zero Gravity")),
        (("0G", "0G Chain"), ("ZEROGRAVITY", "Zero Gravity")),
        (("ADA", "ADA"), ("CARDANO", "Cardano")),
        (("AR", "AR"), ("ARWEAVE", "Arweave")),
        (("CC", "CC"), ("CANTON", "Canton")),
        (("DOT", "DOT"), ("STATEMINT", "Asset Hub Polkadot")),
        (("DOT", "DOT"), ("POLKADOTASSETHUB", "PolkadotAssetHub")),
        (("DOT", "DOT"), ("DOTAH", "Polkadot AssetHub ")),
        (("KSM", "KSM"), ("KUSAMA", "Asset Hub Kusama")),
        (("KSM", "KSM"), ("ASSETHUBKUSAMA", "AssetHubKusama")),
        (("KSM", "KSM"), ("KSMAH", "Kusama Asset Hub")),
        (("POLYX", "POLYX"), ("POLYMESH", "Polymesh")),
        (("PROS", "PROS"), ("PHAROS", "Pharos")),
        (("S", "S"), ("SONIC", "Sonic Network")),
        (("SOMI", "SOMI"), ("SOMNIA", "Somnia")),
        (("STX", "STX"), ("STACKS", "stacks")),
        (("TIA", "TIA"), ("CELESTIA", "Celestia")),
        (("VET", "VET"), ("VECHAIN", "VeChain")),
        (("XLM", "XLM"), ("STELLARLUMENS", "StellarLumens")),
        (("XPL", "XPL"), ("PLASMA", "Plasma")),
        (("ZK_ETH", "ZK_ETH"), ("ZKSYNCERA", "zkSync Era")),
        (("ZK_ETH", "ZK_ETH"), ("ZKV2", "ZKsync Era")),
    ],
)
def test_equivalence_table_pairs_from_absent_audit_2026_09_27(
    dom: tuple[str, str], fx: tuple[str, str]
) -> None:
    verdict, matched = match_network(net(*dom), [net(*fx)])
    assert verdict == "matched"
    assert matched is not None and matched.code == fx[0]


def test_bithumb_dot_vs_binance_picks_asset_hub_not_bsc_or_eth() -> None:
    foreign = [
        net("BSC", "BNB Smart Chain (BEP20)"),
        net("STATEMINT", "Asset Hub Polkadot"),
        net("ETH", "Ethereum (ERC20)"),
    ]
    verdict, matched = match_network(net("DOT", "DOT"), foreign)
    assert verdict == "matched" and matched is foreign[1]


def test_bithumb_taiko_eth_vs_bybit_picks_taiko_chain_not_erc20() -> None:
    # 바이빗은 ETH(ERC20)·Taiko Chain 둘 다 준다 — 빗썸 TAIKO_ETH 는 L2 쪽에 맞아야 하고, 그 망의 출금 막힘이 그대로 보인다
    foreign = [net("ETH", "Ethereum"), net("TAIKO", "Taiko Chain", wd=False)]
    verdict, matched = match_network(net("TAIKO_ETH", "TAIKO_ETH"), foreign)
    assert verdict == "matched"
    assert matched is foreign[1]


def test_sol_vs_bitget_bep20_only_is_still_absent() -> None:
    assert match_network(net("SOL", "SOL"), [net("BEP20", "BEP20")])[0] == "absent"


# --- 행 판정 6값 — 006 §3.7 다섯 경우 + 해외 망 이름 (024 §3.2·§4) ---


@dataclass
class _Row:
    """판정 입력 — core.models.Row 중 wallet_fields 가 읽는 세 값."""

    deposit_enabled: bool | None
    withdrawal_enabled: bool | None
    networks: list[Network]


def test_wallet_fields_case1_no_domestic_networks_uses_coin_values() -> None:
    fx = _Row(False, True, [net("ETH", "Ethereum")])
    assert wallet_fields(_Row(True, None, []), fx) == (
        None,
        None,
        True,
        None,
        False,
        True,
    )


def test_wallet_fields_matched_takes_matched_network_values_and_both_names() -> None:
    # GRT 실사례 — 코인 단위로는 출금 가능이지만 맞춘 ETH 망은 출금 중단
    dom = _Row(None, None, [net("ETH", "Ethereum")])
    fx = _Row(
        True,
        True,
        [net("ARBITRUM", "Arbitrum One"), net("ETH", "Ethereum (ERC20)", wd=False)],
    )
    assert wallet_fields(dom, fx) == (
        "Ethereum",
        "Ethereum (ERC20)",
        True,
        True,
        True,
        False,
    )


def test_wallet_fields_absent_blocks_foreign_and_has_no_foreign_name() -> None:
    dom = _Row(True, True, [net("SOL", "Solana")])
    fx = _Row(True, True, [net("BSC", "BNB Smart Chain")])
    assert wallet_fields(dom, fx) == ("Solana", None, True, True, False, False)


def test_wallet_fields_unknown_with_foreign_networks_is_null_null() -> None:
    dom = _Row(True, True, [net("AVAX", "AVAX")])
    fx = _Row(True, True, [net("AVAXC", "AVAX C-Chain")])
    assert wallet_fields(dom, fx) == ("AVAX", None, True, True, None, None)


def test_wallet_fields_unknown_without_foreign_networks_uses_foreign_coin_values() -> (
    None
):
    # 국내 두 값은 코인 값이 아니라 고른 망의 값, 해외 두 값만 코인 단위
    dom = _Row(None, None, [net("ETH", "Ethereum", wd=False)])
    fx = _Row(False, True, [])
    assert wallet_fields(dom, fx) == ("Ethereum", None, True, False, False, True)


def test_okx_chain_names_match_domestic_networks() -> None:
    """OKX `chain` 은 `<ccy>-<이름>` — 뗀 이름이 006 정규화·별칭으로 국내 기준 네트워크와 짝이 맞는다 (045 §3.6)."""
    pairs = [
        (net("BTC", "Bitcoin"), net("BITCOIN", "Bitcoin")),
        (net("ETH", "Ethereum"), net("ERC20", "ERC20")),
        (net("ETH", "ETH"), net("ERC20", "ERC20")),
        (net("TRX", "Tron"), net("TRC20", "TRC20")),
        (net("ARBITRUM", "Arbitrum One"), net("ARBITRUMONE", "Arbitrum One")),
        (net("ARB_ETH", "ARB_ETH"), net("ARBITRUMONE", "Arbitrum One")),
        (net("SOL", "Solana"), net("SOLANA", "Solana")),
    ]
    for dom, okx in pairs:
        verdict, matched = match_network(dom, [okx, net("BEP20", "BSC")])
        assert verdict == "matched", (dom, okx)
        assert matched is okx
