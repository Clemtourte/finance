"""Tests du modèle de coûts réaliste (src.engine.costs), fonctions pures.

Toutes les valeurs attendues sont recalculées à la main ; ces tests ne
valident jamais `build_order_cost_arrays` contre lui-même.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.engine.costs import (
    BrokerageTier,
    CostConfig,
    brokerage_fee_amount,
    build_order_cost_arrays,
    select_tier,
)

_TIERS = (
    BrokerageTier(max_order_value=500.0, fixed_fee=1.99),
    BrokerageTier(max_order_value=None, pct_fee=0.006),
)


# --- BrokerageTier / CostConfig : validation --------------------------------


def test_brokerage_tier_rejects_both_fixed_and_pct():
    with pytest.raises(ValueError):
        BrokerageTier(max_order_value=500.0, fixed_fee=1.99, pct_fee=0.006)


def test_brokerage_tier_rejects_neither_fixed_nor_pct():
    with pytest.raises(ValueError):
        BrokerageTier(max_order_value=500.0)


def test_cost_config_rejects_bounded_last_tier():
    with pytest.raises(ValueError):
        CostConfig(
            brokerage_tiers=(BrokerageTier(max_order_value=500.0, fixed_fee=1.99),),
            ttf_pct=0.0,
            base_slippage_pct=0.0,
        )


def test_cost_config_rejects_unbounded_middle_tier():
    with pytest.raises(ValueError):
        CostConfig(
            brokerage_tiers=(
                BrokerageTier(max_order_value=None, pct_fee=0.006),
                BrokerageTier(max_order_value=None, pct_fee=0.01),
            ),
            ttf_pct=0.0,
            base_slippage_pct=0.0,
        )


def test_cost_config_rejects_empty_tiers():
    with pytest.raises(ValueError):
        CostConfig(brokerage_tiers=(), ttf_pct=0.0, base_slippage_pct=0.0)


# --- select_tier / brokerage_fee_amount -------------------------------------


def test_select_tier_picks_fixed_tier_below_threshold():
    assert select_tier(300.0, _TIERS).fixed_fee == 1.99


def test_select_tier_picks_fixed_tier_at_exact_threshold():
    assert select_tier(500.0, _TIERS).fixed_fee == 1.99


def test_select_tier_picks_pct_tier_above_threshold():
    assert select_tier(500.01, _TIERS).pct_fee == 0.006


def test_brokerage_fee_amount_fixed_tier():
    assert brokerage_fee_amount(300.0, _TIERS) == 1.99


def test_brokerage_fee_amount_pct_tier():
    assert brokerage_fee_amount(10_000.0, _TIERS) == pytest.approx(60.0)


# --- build_order_cost_arrays -------------------------------------------------


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-01", periods=n)


def test_single_round_trip_populates_only_order_bars():
    idx = _idx(6)
    open_prices = pd.Series([100.0, 101, 102, 103, 104, 105], index=idx)
    entries = pd.Series([False, True, False, False, False, False], index=idx)
    exits = pd.Series([False, False, False, False, True, False], index=idx)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0005)

    fees, fixed_fees, slippage = build_order_cost_arrays(
        open_prices, entries, exits, initial_capital=10_000.0, costs=costs,
        ttf_eligible=False, spread_pct=0.001,
    )

    non_order_bars = [0, 2, 3, 5]
    for i in non_order_bars:
        assert fees.iloc[i] == 0.0
        assert fixed_fees.iloc[i] == 0.0
        assert slippage.iloc[i] == 0.0

    # Ordre de 10 000€ -> palier pourcentage (0,6%), pas de TTF (non éligible).
    assert fees.iloc[1] == pytest.approx(0.006)
    assert fixed_fees.iloc[1] == 0.0
    assert slippage.iloc[1] == pytest.approx(0.0005 + 0.001)
    assert fees.iloc[4] == pytest.approx(0.006)
    assert slippage.iloc[4] == pytest.approx(0.0005 + 0.001)


def test_ttf_applies_only_at_entry_not_exit():
    idx = _idx(4)
    open_prices = pd.Series([100.0, 101, 102, 103], index=idx)
    entries = pd.Series([False, True, False, False], index=idx)
    exits = pd.Series([False, False, False, True], index=idx)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0)

    fees, _, _ = build_order_cost_arrays(
        open_prices, entries, exits, initial_capital=10_000.0, costs=costs,
        ttf_eligible=True, spread_pct=0.0,
    )

    assert fees.iloc[1] == pytest.approx(0.006 + 0.004)  # courtage + TTF à l'achat
    assert fees.iloc[3] == pytest.approx(0.006)  # courtage seul à la vente


def test_small_order_uses_fixed_fee_tier():
    idx = _idx(4)
    open_prices = pd.Series([100.0, 101, 102, 103], index=idx)
    entries = pd.Series([False, True, False, False], index=idx)
    exits = pd.Series([False, False, False, True], index=idx)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0)

    fees, fixed_fees, _ = build_order_cost_arrays(
        open_prices, entries, exits, initial_capital=300.0, costs=costs,
        ttf_eligible=False, spread_pct=0.0,
    )

    assert fixed_fees.iloc[1] == pytest.approx(1.99)
    assert fees.iloc[1] == 0.0  # pas de composante TTF (non éligible), pas de pct_fee sur ce palier


def test_no_exit_leaves_only_entry_populated():
    idx = _idx(5)
    open_prices = pd.Series([100.0, 101, 102, 103, 104], index=idx)
    entries = pd.Series([False, True, False, False, False], index=idx)
    exits = pd.Series(False, index=idx)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0)

    fees, fixed_fees, slippage = build_order_cost_arrays(
        open_prices, entries, exits, initial_capital=10_000.0, costs=costs,
        ttf_eligible=False, spread_pct=0.0,
    )

    assert fees.iloc[1] > 0.0
    assert (fees.iloc[2:] == 0.0).all()
    assert (fixed_fees == 0.0).all()
    assert (slippage.iloc[2:] == 0.0).all()


def test_two_sequential_trades_compound_cash_between_tiers():
    # Capital de départ tout juste sous 500€ -> 1er ordre au tarif fixe.
    # Le prix quadruple sur le premier aller-retour -> le capital
    # récupéré dépasse largement 500€ -> 2e ordre au tarif pourcentage.
    idx = _idx(8)
    open_prices = pd.Series([100.0, 100.0, 100.0, 400.0, 400.0, 400.0, 401.0, 402.0], index=idx)
    entries = pd.Series(
        [False, True, False, False, False, True, False, False], index=idx
    )
    exits = pd.Series(
        [False, False, False, True, False, False, False, True], index=idx
    )
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.0, base_slippage_pct=0.0)

    fees, fixed_fees, _ = build_order_cost_arrays(
        open_prices, entries, exits, initial_capital=495.0, costs=costs,
        ttf_eligible=False, spread_pct=0.0,
    )

    # 1er ordre : 495€ <= 500€ -> palier fixe.
    assert fixed_fees.iloc[1] == pytest.approx(1.99)
    assert fees.iloc[1] == 0.0

    # Capital recalculé à la main après le 1er aller-retour (prix x4, sans frais/glissement) :
    size_1 = (495.0 - 1.99) / 100.0
    gross_exit_1 = size_1 * 400.0
    exit_fee_1 = brokerage_fee_amount(gross_exit_1, _TIERS)
    cash_after_1 = size_1 * 400.0 - exit_fee_1
    assert cash_after_1 > 500.0  # confirme qu'on a bien changé de palier

    # 2e ordre : capital > 500€ -> palier pourcentage.
    assert fees.iloc[5] == pytest.approx(0.006)
    assert fixed_fees.iloc[5] == 0.0
