"""망 판정 메모·이름 정규화 캐시 (스펙 006 §3.5·§3.6·§3.7, 2026-09-28 성능 개선)."""

from datetime import UTC, datetime

import pytest

from app.core import networks
from app.core.live_store import LiveStore
from app.core.models import Row
from app.core.networks import (
    Network,
    WalletFields,
    WalletMemo,
    normalize_name,
    wallet_fields,
)
from app.core.ticks import build_tick
from app.features.spreads.service import build_table
from tests.conftest import make_row

KEY = ("upbit", "binance", "ETH")


def _rows() -> tuple[Row, Row]:
    dom = make_row("upbit", "ETH")
    fx = make_row("binance", "ETH")
    dom.networks = [Network("ETH", "Ethereum", True, True)]
    fx.networks = [Network("ETH", "Ethereum (ERC20)", True, False)]
    dom.deposit_enabled = dom.withdrawal_enabled = True
    fx.deposit_enabled, fx.withdrawal_enabled = True, False
    return dom, fx


def test_memo_reuses_the_verdict_while_inputs_are_the_same_objects() -> None:
    dom, fx = _rows()
    memo = WalletMemo()
    memo.rotate()
    first = memo.fields(KEY, dom, fx)
    assert first == wallet_fields(dom, fx) and first.wd_fx is False
    # 같은 회차의 표가 읽는다
    assert memo.fields(KEY, dom, fx) is first
    # 행이 새 메시지로 교체돼도 망 목록·입출금 값은 물려받은 같은 객체 — 다음 회차도 다시 판정하지 않는다
    dom2 = make_row("upbit", "ETH")
    dom2.networks = dom.networks
    dom2.deposit_enabled = dom2.withdrawal_enabled = True
    memo.rotate()
    assert memo.fields(KEY, dom2, fx) is first


def test_memo_judges_again_when_a_list_object_or_a_coin_value_changes() -> None:
    dom, fx = _rows()
    memo = WalletMemo()
    memo.rotate()
    first = memo.fields(KEY, dom, fx)
    # 입출금 재조회 — 새 목록 객체(내용이 바뀌었다)
    fx.networks = [Network("ETH", "Ethereum (ERC20)", True, True)]
    second = memo.fields(KEY, dom, fx)
    assert second is not first and second.wd_fx is True
    assert second == wallet_fields(dom, fx)
    # 코인 단위 값만 바뀌어도 다시 판정한다
    fx.deposit_enabled = None
    third = memo.fields(KEY, dom, fx)
    assert third is not second and third == wallet_fields(dom, fx)


def test_memo_keeps_only_combinations_used_in_the_last_two_rounds() -> None:
    dom, fx = _rows()
    memo = WalletMemo()
    memo.rotate()
    first = memo.fields(KEY, dom, fx)
    memo.rotate()
    assert memo.fields(KEY, dom, fx) is first  # 직전 회차에 쓰였다
    memo.rotate()  # 이 회차엔 안 쓰인다
    memo.rotate()
    assert (
        memo.fields(KEY, dom, fx) is not first
    )  # 한 회차를 건너뛰면 빠진다 → 다시 판정


def test_normalize_name_remembers_results_by_name() -> None:
    assert normalize_name.cache_parameters()["maxsize"] == 4096
    tokens = normalize_name("Ethereum (ERC20)")
    assert tokens == frozenset({"ethereum"})
    assert normalize_name("Ethereum (ERC20)") is tokens


def test_table_reads_the_verdicts_the_tick_left_in_the_same_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """틱이 채운 메모를 같은 회차의 표(017)가 읽는다 — 틱 조합은 표에서 다시 판정하지 않는다 (§3.7)."""
    now = datetime.now(UTC)
    store = LiveStore()
    dom, fx = _rows()
    btc_dom, btc_fx = make_row("upbit", "BTC"), make_row("binance", "BTC")
    store.put_rows([dom, fx, btc_dom, btc_fx], now)
    for ex in ("upbit", "binance"):
        store.stream(ex).last_message_at = int(now.timestamp() * 1000)
    store.set_rate("upbit", 1400.0, 1390.0, now)
    calls: list[tuple[str, str]] = []
    real = networks.wallet_fields

    def counting(dom_row: Row, fx_row: Row) -> WalletFields:
        calls.append((dom_row.exchange, dom_row.base))
        return real(dom_row, fx_row)

    monkeypatch.setattr(networks, "wallet_fields", counting)
    memo = WalletMemo()
    tick = build_tick(store, 1_787_000_000, [], memo)
    assert len(tick.rows) == 2 and len(calls) == 2
    table = build_table(store, now=now, wallet_memo=memo)
    assert len(calls) == 2  # 표는 메모에서 읽었다
    listed = table["rows"]
    assert isinstance(listed, list)
    rows = {r["sym"]: r for r in listed}
    assert rows["ETH"]["wdFx"] is False and rows["ETH"]["netFx"] == "Ethereum (ERC20)"
    # 다음 틱 — 입력이 같은 객체라 틱도 다시 판정하지 않는다
    build_tick(store, 1_787_000_001, [], memo)
    assert len(calls) == 2
