"""Tests du moteur DCA minimal (src.strategies.dca).

Les valeurs attendues (parts achetées, frais) sont recalculées à la main
avec la même formule que `src.engine.costs` (déjà validée contre le
comportement réel de vectorbt dans tests/test_costs.py) : ce module ne
dépend pas de vectorbt, donc ces tests ne le valident pas contre lui-même.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.engine.costs import BrokerageTier, CostConfig, select_tier
from src.strategies.dca import DCAResult, load_dca_config, simulate_dca

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_TIERS = (
    BrokerageTier(max_order_value=500.0, fixed_fee=1.99),
    BrokerageTier(max_order_value=None, pct_fee=0.006),
)


def _monthly_df(n_months: int, start: str = "2024-01-01") -> pd.DataFrame:
    """Un DataFrame avec plusieurs séances par mois (prix croissant)."""
    dates = []
    closes = []
    price = 100.0
    for m in range(n_months):
        month_start = pd.Timestamp(start) + pd.DateOffset(months=m)
        month_days = pd.bdate_range(month_start, periods=15)  # ~3 semaines / mois
        for d in month_days:
            dates.append(d)
            closes.append(price)
            price += 0.5
    idx = pd.DatetimeIndex(dates)
    return pd.DataFrame({"open": closes, "close": closes}, index=idx)


def test_rejects_non_positive_monthly_amount():
    df = _monthly_df(1)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.0, base_slippage_pct=0.0)
    with pytest.raises(ValueError):
        simulate_dca(df, monthly_amount=0.0, costs=costs)


def test_invests_only_on_first_trading_day_of_each_month():
    df = _monthly_df(3)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.0, base_slippage_pct=0.0)
    result = simulate_dca(df, monthly_amount=200.0, costs=costs)

    investment_days = result.contributions[result.contributions > 0].index
    assert len(investment_days) == 3
    for d in investment_days:
        period = d.to_period("M")
        month_bars = df.index[df.index.to_period("M") == period]
        assert d == month_bars.min()


def test_shares_bought_matches_hand_calculation():
    df = _monthly_df(1)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0005)

    result = simulate_dca(df, monthly_amount=200.0, costs=costs, ttf_eligible=True, spread_pct=0.001)

    first_open = df["open"].iloc[0]
    tier = select_tier(200.0, _TIERS)  # 200 <= 500 -> palier fixe
    total_slippage = 0.0005 + 0.001
    fill_price = first_open * (1 + total_slippage)
    # La TTF s'ajoute au taux même sur le palier fixe : c'est une
    # composante additive indépendante du type de palier de courtage.
    expected_shares = (200.0 - tier.fixed_fee) / (fill_price * (1 + 0.004))

    assert result.shares_bought.iloc[0] == pytest.approx(expected_shares, rel=1e-9)
    assert result.contributions.iloc[0] == 200.0


def test_shares_bought_pct_tier_includes_ttf():
    df = _monthly_df(1)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0)
    # Montant > 500€ -> palier pourcentage, la TTF s'ajoute alors au taux.
    result = simulate_dca(df, monthly_amount=1000.0, costs=costs, ttf_eligible=True)

    first_open = df["open"].iloc[0]
    tier = select_tier(1000.0, _TIERS)
    pct = tier.pct_fee + 0.004
    expected_shares = 1000.0 / (first_open * (1 + pct))

    assert result.shares_bought.iloc[0] == pytest.approx(expected_shares, rel=1e-9)


def test_cumulative_shares_and_contributions_accumulate_across_months():
    df = _monthly_df(3)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.0, base_slippage_pct=0.0)
    result = simulate_dca(df, monthly_amount=200.0, costs=costs)

    assert result.cumulative_contributions.iloc[-1] == pytest.approx(600.0)
    assert result.cumulative_shares.iloc[-1] == pytest.approx(result.shares_bought.sum())
    # La série cumulée est monotone croissante (aucun retrait dans ce modèle).
    assert (result.cumulative_shares.diff().dropna() >= 0).all()


def test_portfolio_value_marks_to_market():
    df = _monthly_df(2)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.0, base_slippage_pct=0.0)
    result = simulate_dca(df, monthly_amount=200.0, costs=costs)

    expected_last_value = result.cumulative_shares.iloc[-1] * df["close"].iloc[-1]
    assert result.portfolio_value.iloc[-1] == pytest.approx(expected_last_value)


def test_no_lookahead_truncated_matches_full_up_to_checkpoint():
    df = _monthly_df(4)
    costs = CostConfig(brokerage_tiers=_TIERS, ttf_pct=0.004, base_slippage_pct=0.0005)

    full = simulate_dca(df, monthly_amount=300.0, costs=costs, ttf_eligible=True, spread_pct=0.0005)

    # Checkpoint : dernière séance du 2e mois (10 séances/mois * 2 dans ce
    # test -> on prend un index à l'intérieur du mois 3 pour rester après
    # la 3e date d'investissement, avant la 4e).
    month_periods = df.index.to_period("M")
    third_investment_date = df.index[month_periods == sorted(set(month_periods))[2]].min()
    checkpoint_pos = df.index.get_loc(third_investment_date) + 5  # quelques séances après

    truncated_df = df.iloc[: checkpoint_pos + 1]
    truncated = simulate_dca(
        truncated_df, monthly_amount=300.0, costs=costs, ttf_eligible=True, spread_pct=0.0005
    )

    checkpoint_date = df.index[checkpoint_pos]
    assert full.cumulative_shares.loc[checkpoint_date] == pytest.approx(
        truncated.cumulative_shares.iloc[-1]
    )
    assert full.cumulative_contributions.loc[checkpoint_date] == pytest.approx(
        truncated.cumulative_contributions.iloc[-1]
    )


def test_load_dca_config_from_real_file():
    config = load_dca_config(PROJECT_ROOT / "config" / "strategies" / "dca.yaml")
    assert config.monthly_amount > 0
