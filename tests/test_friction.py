"""Tests de la reconstruction de friction (src.metrics.friction).

Les valeurs attendues sont recalculées à la main, trade par trade, avec
la même arithmétique que `src.engine.costs.build_order_cost_arrays`
(elle-même validée contre le comportement réel de `vectorbt` dans
tests/test_costs.py et tests/test_engine.py) — mais réécrite ici
indépendamment, sans appeler `build_order_cost_arrays`, pour ne jamais
valider la reconstruction de friction contre le code qui a produit les
coûts d'origine.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.engine.backtest import run_backtest
from src.engine.costs import BrokerageTier, CostConfig, select_tier
from src.metrics.friction import compute_friction

_TIERS = (
    BrokerageTier(max_order_value=500.0, fixed_fee=1.99),
    BrokerageTier(max_order_value=None, pct_fee=0.006),
)


def _linear_price_df(n: int, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = pd.Series([start + i * step for i in range(n)], index=idx)
    open_ = close.shift(1).fillna(start - step)
    return pd.DataFrame({"open": open_, "close": close})


def _hand_compute_round_trip(entry_open, exit_open, cash, tiers, ttf_pct, ttf_eligible, total_slip):
    """Réplique indépendante de l'arithmétique de build_order_cost_arrays."""
    entry_tier = select_tier(cash, tiers)
    entry_pct = (entry_tier.pct_fee or 0.0) + (ttf_pct if ttf_eligible else 0.0)
    entry_fixed = entry_tier.fixed_fee or 0.0
    entry_fill = entry_open * (1 + total_slip)
    size = (cash - entry_fixed) / (entry_fill * (1 + entry_pct))
    entry_fees = size * entry_fill * entry_pct + entry_fixed

    if entry_tier.fixed_fee is not None:
        entry_brokerage = entry_fixed
        entry_ttf = entry_fees - entry_brokerage
    else:
        total = entry_tier.pct_fee + (ttf_pct if ttf_eligible else 0.0)
        entry_brokerage = entry_fees * (entry_tier.pct_fee / total) if total else 0.0
        entry_ttf = entry_fees * ((ttf_pct if ttf_eligible else 0.0) / total) if total else 0.0
    entry_slippage = size * abs(entry_fill - entry_open)

    gross_exit = size * exit_open
    exit_tier = select_tier(gross_exit, tiers)
    exit_pct = exit_tier.pct_fee or 0.0
    exit_fixed = exit_tier.fixed_fee or 0.0
    exit_fill = exit_open * (1 - total_slip)
    exit_fees = size * exit_fill * exit_pct + exit_fixed
    exit_slippage = size * abs(exit_fill - exit_open)

    cash_after = size * exit_fill - exit_fees

    return {
        "brokerage": entry_brokerage + exit_fees,
        "ttf": entry_ttf,
        "slippage": entry_slippage + exit_slippage,
        "cash_after": cash_after,
    }


def test_friction_reconstructed_by_hand_on_two_round_trips():
    df = _linear_price_df(n=20)
    # Petit capital de départ -> 1er aller-retour au palier fixe.
    # Prix multiplié par ~4 sur ce premier aller-retour -> le capital
    # récupéré dépasse 500€ -> 2e aller-retour au palier pourcentage.
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 400.0, 400.0, 400.0, 401.0, 402.0, 403.0, 404.0, 405.0],
            "close": [100.0, 400.0, 400.0, 400.0, 400.0, 401.0, 402.0, 403.0, 404.0, 405.0],
        },
        index=pd.bdate_range("2024-01-01", periods=10),
    )
    # Décidé à J=0 (close) -> entrée J=1 (open) ; sortie décidée J=1
    # (close) -> exécutée J=2 (open) ; 2e entrée décidée J=4 -> J=5 ;
    # sortie décidée J=7 -> J=8.
    target = pd.Series([1, 0, 0, 0, 1, 1, 1, 0, 0, 0], index=df.index)

    ttf_pct = 0.004
    base_slippage = 0.0005
    spread_pct = 0.001
    total_slip = base_slippage + spread_pct
    initial_capital = 495.0

    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=ttf_pct, base_slippage_pct=base_slippage)

    pf = run_backtest(
        df, target, costs, initial_capital=initial_capital, ttf_eligible=True, spread_pct=spread_pct
    )
    trades = pf.trades.records_readable
    assert len(trades) == 2

    # --- Calcul indépendant, à la main, trade par trade ---
    trip1 = _hand_compute_round_trip(
        entry_open=df["open"].iloc[1], exit_open=df["open"].iloc[2],
        cash=initial_capital, tiers=_TIERS, ttf_pct=ttf_pct, ttf_eligible=True, total_slip=total_slip,
    )
    trip2 = _hand_compute_round_trip(
        entry_open=df["open"].iloc[5], exit_open=df["open"].iloc[8],
        cash=trip1["cash_after"], tiers=_TIERS, ttf_pct=ttf_pct, ttf_eligible=True, total_slip=total_slip,
    )

    expected_brokerage = trip1["brokerage"] + trip2["brokerage"]
    expected_ttf = trip1["ttf"] + trip2["ttf"]
    expected_slippage = trip1["slippage"] + trip2["slippage"]

    friction = compute_friction(
        trades, df["open"], costs, ttf_eligible=True, spread_pct=spread_pct
    )

    assert friction.brokerage_eur == pytest.approx(expected_brokerage, rel=1e-6)
    assert friction.ttf_eur == pytest.approx(expected_ttf, rel=1e-6)
    assert friction.slippage_eur == pytest.approx(expected_slippage, rel=1e-6)
    assert friction.total_eur == pytest.approx(
        expected_brokerage + expected_ttf + expected_slippage, rel=1e-6
    )


def test_friction_zero_for_no_trades():
    df = _linear_price_df(n=5)
    empty_trades = pd.DataFrame(
        columns=["Status", "Size", "Avg Entry Price", "Entry Fees", "Avg Exit Price", "Exit Fees",
                 "Entry Timestamp", "Exit Timestamp"]
    )
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0005)
    friction = compute_friction(empty_trades, df["open"], costs, ttf_eligible=True, spread_pct=0.001)
    assert friction.total_eur == 0.0


def test_friction_counts_entry_of_open_position_but_not_a_fictitious_exit():
    df = _linear_price_df(n=10)
    target = pd.Series(1, index=df.index)  # jamais soldé -> Status "Open"
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0005)
    total_slip = 0.0005 + 0.001

    pf = run_backtest(df, target, costs, initial_capital=10_000.0, ttf_eligible=True, spread_pct=0.001)
    trades = pf.trades.records_readable
    assert trades.iloc[0]["Status"] == "Open"

    friction = compute_friction(trades, df["open"], costs, ttf_eligible=True, spread_pct=0.001)

    # Calcul indépendant de la seule friction d'entrée (10 000€ -> palier pourcentage).
    entry_open = df["open"].iloc[1]
    entry_tier = select_tier(10_000.0, _TIERS)
    entry_pct = entry_tier.pct_fee + 0.004
    entry_fill = entry_open * (1 + total_slip)
    size = 10_000.0 / (entry_fill * (1 + entry_pct))
    entry_fees = size * entry_fill * entry_pct
    expected_brokerage = entry_fees * (entry_tier.pct_fee / entry_pct)
    expected_ttf = entry_fees * (0.004 / entry_pct)
    expected_slippage = size * abs(entry_fill - entry_open)

    assert friction.brokerage_eur == pytest.approx(expected_brokerage, rel=1e-6)
    assert friction.ttf_eur == pytest.approx(expected_ttf, rel=1e-6)
    assert friction.slippage_eur == pytest.approx(expected_slippage, rel=1e-6)
