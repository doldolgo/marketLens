"""/spreads 행의 입출금 5필드 — 스펙 006 §3.7 의 5케이스 (네트워크 없음, 저장소 직접 시드)."""

from datetime import UTC, datetime

from app.core.live_store import LiveStore
from app.core.networks import Network
from app.features.spreads.service import build_spreads
from app.features.spreads.tests.helpers import make_row

NOW = datetime.now(UTC)


def seed(
    store: LiveStore,
    *,
    dom_networks: list[Network] | None = None,
    fx_networks: list[Network] | None = None,
    dom_dep: bool | None = None,
    dom_wd: bool | None = None,
    fx_dep: bool | None = None,
    fx_wd: bool | None = None,
    base: str = "GRT",
) -> None:
    store.replace_exchange(
        "upbit",
        [make_row("upbit", base, dep=dom_dep, wd=dom_wd, networks=dom_networks)],
        NOW,
    )
    store.replace_exchange(
        "binance",
        [make_row("binance", base, dep=fx_dep, wd=fx_wd, networks=fx_networks)],
        NOW,
    )
    store.set_rate("upbit", 1400.0, 1390.0, NOW)
    store.mark_received(1_787_000_000)


def only_row(store: LiveStore) -> dict[str, object]:
    rows = build_spreads(store, now=NOW).model_dump()["rows"]
    assert len(rows) == 1
    return rows[0]


def test_case1_empty_domestic_networks_uses_coin_values() -> None:
    # 키 없음·망 정보 없는 과도기 → 코인 단위 값 그대로, netDom null (기존 동작)
    store = LiveStore()
    seed(store, dom_dep=True, dom_wd=None, fx_dep=False, fx_wd=True)
    row = only_row(store)
    assert row["net_dom"] is None
    assert row["dep_dom"] is True
    assert row["wd_dom"] is None
    assert row["dep_fx"] is False
    assert row["wd_fx"] is True


def test_case3_grt_matched_shows_matched_network_values() -> None:
    # 스펙 §3.7 GRT 실사례 — 코인 단위로는 출금 가능이지만 맞춘 ETH 망은 출금 중단
    store = LiveStore()
    seed(
        store,
        dom_networks=[Network("ETH", "Ethereum", dep=True, wd=True)],
        fx_networks=[
            Network("ARBITRUM", "Arbitrum One", dep=True, wd=True),
            Network("ETH", "Ethereum (ERC20)", dep=True, wd=False),
        ],
        dom_dep=True,
        dom_wd=True,
        fx_dep=True,
        fx_wd=True,
    )
    row = only_row(store)
    assert row["net_dom"] == "Ethereum"
    assert row["dep_dom"] is True
    assert row["wd_dom"] is True
    assert row["dep_fx"] is True
    assert row["wd_fx"] is False


def test_case4_qkc_absent_means_no_transfer_path() -> None:
    # 해외가 그 망을 안 다룸 = 옮길 길 없음 → depFx/wdFx false
    store = LiveStore()
    seed(
        store,
        base="QKC",
        dom_networks=[Network("QKC", "Quarkchain", dep=True, wd=True)],
        fx_networks=[Network("ETH", "Ethereum (ERC20)", dep=True, wd=True)],
        fx_dep=True,
        fx_wd=True,
    )
    row = only_row(store)
    assert row["net_dom"] == "Quarkchain"
    assert row["dep_fx"] is False
    assert row["wd_fx"] is False


def test_case5_sei_unknown_with_foreign_networks_is_null() -> None:
    # 해외 망이 있는데 못 맞춤 = 모른다고 말한다 — 코인 단위로 접으면 낙관 편향
    store = LiveStore()
    seed(
        store,
        base="SEI",
        dom_networks=[Network("SEI", "Sei", dep=True, wd=True)],
        fx_networks=[Network("SEIEVM", "Sei EVM", dep=True, wd=True)],
        fx_dep=True,
        fx_wd=True,
    )
    row = only_row(store)
    assert row["net_dom"] == "Sei"
    assert row["dep_dom"] is True
    assert row["dep_fx"] is None
    assert row["wd_fx"] is None


def test_case5_unknown_with_empty_foreign_networks_uses_fx_coin_values() -> None:
    # 해외 망 정보가 아예 없으면(빈 목록 = unknown) 해외 코인 단위 값
    store = LiveStore()
    seed(
        store,
        dom_networks=[Network("ETH", "Ethereum", dep=True, wd=False)],
        fx_networks=[],
        fx_dep=True,
        fx_wd=True,
    )
    row = only_row(store)
    assert row["net_dom"] == "Ethereum"
    assert row["wd_dom"] is False  # 고른 국내 망의 값 — 코인 값이 아니다
    assert row["dep_fx"] is True
    assert row["wd_fx"] is True


def test_fail_row_still_applies_network_verdict() -> None:
    # status=fail 행도 같은 규칙 (§3.7)
    store = LiveStore()
    store.replace_exchange(
        "upbit",
        [
            make_row(
                "upbit",
                "GRT",
                networks=[Network("ETH", "Ethereum", dep=True, wd=True)],
            )
        ],
        NOW,
    )
    store.replace_exchange(
        "binance",
        [
            make_row(
                "binance",
                "GRT",
                asks=[],  # 호가 없음 → fail
                networks=[Network("ETH", "Ethereum (ERC20)", dep=True, wd=False)],
            )
        ],
        NOW,
    )
    store.set_rate("upbit", 1400.0, 1390.0, NOW)
    store.mark_received(1_787_000_000)
    row = only_row(store)
    assert row["status"] == "fail"
    assert row["net_dom"] == "Ethereum"
    assert row["dep_fx"] is True
    assert row["wd_fx"] is False
