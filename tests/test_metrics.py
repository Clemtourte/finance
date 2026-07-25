"""Tests des métriques de performance (src.metrics).

Les valeurs attendues sont calculées à la main (formules fermées) plutôt
que par ré-implémentation du code testé, pour détecter de vraies erreurs
de formule (mauvais ddof, oubli de racine carrée, etc.).
"""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from src.engine.backtest import run_backtest, run_buy_and_hold
from src.engine.costs import BrokerageTier, CostConfig
from src.metrics.comparison import compare
from src.metrics.performance import (
    annualize_turnover,
    annualized_volatility,
    cagr,
    compute_metrics,
    drawdown_series,
    friction_pct_of_gross_gain,
    max_drawdown,
    max_drawdown_duration,
    num_trades,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    split_portfolio_by_date,
    turnover,
    win_rate,
)


# --- cagr --------------------------------------------------------------------


def test_cagr_known_value():
    equity = pd.Series([100.0, 121.0])
    assert cagr(equity, periods_per_year=1) == pytest.approx(0.21)


def test_cagr_nan_for_short_series():
    assert math.isnan(cagr(pd.Series([100.0]), periods_per_year=252))


def test_cagr_compounds_over_multiple_years():
    # +10%/an composé sur 2 ans -> facteur 1.21, périodes_per_year=1 pour
    # raisonner directement en années.
    equity = pd.Series([100.0, 110.0, 121.0])
    assert cagr(equity, periods_per_year=1) == pytest.approx(0.10, rel=1e-9)


# --- volatilité / sharpe / sortino -------------------------------------------


def test_annualized_volatility_zero_for_constant_returns():
    returns = pd.Series([0.01, 0.01, 0.01, 0.01])
    assert annualized_volatility(returns, periods_per_year=252) == 0.0


def test_sharpe_ratio_known_value():
    returns = pd.Series([0.1, 0.3, -0.1])
    # mean=0.1, std(ddof=1)=0.2 -> sharpe = 0.1/0.2 = 0.5 (rf=0, periods_per_year=1)
    assert sharpe_ratio(returns, risk_free_rate=0.0, periods_per_year=1) == pytest.approx(0.5)


def test_sharpe_ratio_nan_when_volatility_zero():
    returns = pd.Series([0.02, 0.02, 0.02])
    assert math.isnan(sharpe_ratio(returns, risk_free_rate=0.0, periods_per_year=252))


def test_sortino_ratio_known_value():
    returns = pd.Series([0.1, 0.3, -0.1])
    # downside = {-0.1} -> downside_dev = 0.1 -> sortino = 0.1/0.1 = 1.0
    assert sortino_ratio(returns, risk_free_rate=0.0, periods_per_year=1) == pytest.approx(1.0)


def test_sortino_ratio_nan_when_no_downside():
    returns = pd.Series([0.05, 0.1, 0.02])
    assert math.isnan(sortino_ratio(returns, risk_free_rate=0.0, periods_per_year=252))


# --- drawdown -----------------------------------------------------------------


def test_max_drawdown_and_duration_known_values():
    equity = pd.Series([100.0, 90.0, 95.0, 80.0, 85.0, 90.0, 100.0, 110.0])
    assert max_drawdown(equity) == pytest.approx(-0.2)
    assert max_drawdown_duration(equity) == 5


def test_max_drawdown_zero_for_monotonic_rise():
    equity = pd.Series([100.0, 110.0, 120.0])
    assert max_drawdown(equity) == 0.0
    assert max_drawdown_duration(equity) == 0


def test_drawdown_series_never_positive():
    equity = pd.Series([100.0, 90.0, 95.0, 105.0, 95.0])
    assert (drawdown_series(equity) <= 0).all()


# --- trades : win rate / profit factor / num trades / turnover --------------


def test_win_rate_and_profit_factor_known_values():
    pnl = pd.Series([100.0, -50.0, 200.0, -25.0, -25.0])
    assert win_rate(pnl) == pytest.approx(0.4)
    assert profit_factor(pnl) == pytest.approx(3.0)
    assert num_trades(pnl) == 5


def test_profit_factor_infinite_when_no_losses():
    pnl = pd.Series([100.0, 50.0])
    assert profit_factor(pnl) == float("inf")


def test_win_rate_profit_factor_nan_when_no_trades():
    empty = pd.Series(dtype=float)
    assert math.isnan(win_rate(empty))
    assert math.isnan(profit_factor(empty))
    assert num_trades(empty) == 0


def test_turnover_known_value():
    assert turnover(traded_notional=20_000.0, average_equity=10_000.0) == pytest.approx(2.0)


def test_turnover_nan_when_average_equity_zero():
    assert math.isnan(turnover(traded_notional=1_000.0, average_equity=0.0))


def test_annualize_turnover_known_value():
    # Turnover de 4.0 sur 504 barres (252/an -> 2 ans) -> 2.0/an.
    assert annualize_turnover(4.0, n_bars=504, periods_per_year=252) == pytest.approx(2.0)


def test_annualize_turnover_nan_for_nan_input():
    assert math.isnan(annualize_turnover(float("nan"), n_bars=252, periods_per_year=252))


def test_annualize_turnover_nan_for_zero_bars():
    assert math.isnan(annualize_turnover(1.0, n_bars=0, periods_per_year=252))


def test_friction_pct_of_gross_gain_known_value():
    # Gain net 900, friction 100 -> gain brut 1000 -> friction = 10%.
    assert friction_pct_of_gross_gain(friction_eur=100.0, net_gain=900.0) == pytest.approx(0.10)


def test_friction_pct_of_gross_gain_nan_when_gross_gain_not_positive():
    assert math.isnan(friction_pct_of_gross_gain(friction_eur=100.0, net_gain=-200.0))
    assert math.isnan(friction_pct_of_gross_gain(friction_eur=0.0, net_gain=0.0))


# --- compute_metrics : intégration avec vectorbt.Portfolio -------------------


def _linear_price_df(n: int = 30, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = pd.Series([start + i * step for i in range(n)], index=idx)
    open_ = close.shift(1).fillna(start - step)
    return pd.DataFrame({"open": open_, "close": close})


def test_compute_metrics_open_position_counts_zero_closed_trades():
    df = _linear_price_df()
    costs = CostConfig(brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=0.006),), ttf_pct=0.0, base_slippage_pct=0.0005)
    pf = run_buy_and_hold(df, costs, initial_capital=10_000.0)

    result = compute_metrics(
        pf, df["open"], costs, ttf_eligible=False, spread_pct=0.0, periods_per_year=252, risk_free_rate=0.0
    )

    assert result.num_trades == 0  # position jamais clôturée
    assert math.isnan(result.win_rate)
    assert result.cagr > 0  # prix monte linéairement
    assert result.friction_eur > 0  # l'entrée a bien payé une friction


def test_compute_metrics_closed_trade_counts_one():
    df = _linear_price_df(n=10)
    target = pd.Series(1, index=df.index)
    target.iloc[-2] = 0
    target.iloc[-1] = 0
    costs = CostConfig(brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=0.006),), ttf_pct=0.0, base_slippage_pct=0.0005)
    pf = run_backtest(df, target, costs, initial_capital=10_000.0)

    result = compute_metrics(
        pf, df["open"], costs, ttf_eligible=False, spread_pct=0.0, periods_per_year=252, risk_free_rate=0.0
    )

    assert result.num_trades == 1
    assert result.win_rate == 1.0  # prix monte, trade gagnant
    assert result.profit_factor == float("inf")


# --- comparison ----------------------------------------------------------------


def test_compare_produces_one_row_per_field_with_correct_delta():
    df = _linear_price_df(n=15)
    costs = CostConfig(brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=0.006),), ttf_pct=0.0, base_slippage_pct=0.0005)
    target = pd.Series(1, index=df.index)

    strategy_pf = run_backtest(df, target, costs, initial_capital=10_000.0)
    benchmark_pf = run_buy_and_hold(df, costs, initial_capital=10_000.0)

    strategy_metrics = compute_metrics(
        strategy_pf, df["open"], costs, ttf_eligible=False, spread_pct=0.0,
        periods_per_year=252, risk_free_rate=0.0,
    )
    benchmark_metrics = compute_metrics(
        benchmark_pf, df["open"], costs, ttf_eligible=False, spread_pct=0.0,
        periods_per_year=252, risk_free_rate=0.0,
    )

    rows = compare(strategy_metrics, benchmark_metrics)

    assert len(rows) == len(strategy_metrics.__dataclass_fields__)
    row_by_metric = {r.metric: r for r in rows}
    assert row_by_metric["cagr"].strategy == pytest.approx(strategy_metrics.cagr, nan_ok=True)
    assert row_by_metric["cagr"].buy_and_hold == pytest.approx(benchmark_metrics.cagr, nan_ok=True)
    assert row_by_metric["cagr"].delta == pytest.approx(
        strategy_metrics.cagr - benchmark_metrics.cagr, nan_ok=True
    )


# --- split_portfolio_by_date (in-sample / out-of-sample) --------------------


def test_split_portfolio_by_date_partitions_equity_by_index():
    df = _linear_price_df(n=40)
    costs = CostConfig(brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=0.0),), ttf_pct=0.0, base_slippage_pct=0.0)
    pf = run_buy_and_hold(df, costs, initial_capital=10_000.0)

    split_date = df.index[20].date()
    equity_is, _, equity_oos, _ = split_portfolio_by_date(pf, split_date)

    assert (equity_is.index < pd.Timestamp(split_date)).all()
    assert (equity_oos.index >= pd.Timestamp(split_date)).all()
    assert len(equity_is) + len(equity_oos) == len(pf.value())
    assert equity_is.index[-1] == df.index[19]
    assert equity_oos.index[0] == df.index[20]


def test_split_portfolio_by_date_assigns_trades_by_entry_timestamp():
    df = _linear_price_df(n=40)
    costs = CostConfig(brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=0.0),), ttf_pct=0.0, base_slippage_pct=0.0)
    # Une position ouverte tôt et soldée tôt (in-sample), une autre
    # ouverte et soldée tard (out-of-sample).
    target = pd.Series(0, index=df.index)
    target.iloc[2:8] = 1  # aller-retour in-sample
    target.iloc[25:35] = 1  # aller-retour out-of-sample

    pf = run_backtest(df, target, costs, initial_capital=10_000.0)
    split_date = df.index[20].date()

    _, trades_is, _, trades_oos = split_portfolio_by_date(pf, split_date)

    assert len(trades_is) == 1
    assert len(trades_oos) == 1
    assert pd.Timestamp(trades_is.iloc[0]["Entry Timestamp"]) < pd.Timestamp(split_date)
    assert pd.Timestamp(trades_oos.iloc[0]["Entry Timestamp"]) >= pd.Timestamp(split_date)


def test_split_portfolio_metrics_differ_from_combined():
    df = _linear_price_df(n=40, start=100.0, step=1.0)
    costs = CostConfig(brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=0.0),), ttf_pct=0.0, base_slippage_pct=0.0)
    pf = run_buy_and_hold(df, costs, initial_capital=10_000.0)

    split_date = df.index[20].date()
    equity_is, trades_is, equity_oos, trades_oos = split_portfolio_by_date(pf, split_date)

    from src.metrics.performance import compute_metrics_from_series

    metrics_is = compute_metrics_from_series(
        equity_is, trades_is, df["open"], costs, ttf_eligible=False, spread_pct=0.0,
        periods_per_year=252, risk_free_rate=0.0,
    )
    metrics_oos = compute_metrics_from_series(
        equity_oos, trades_oos, df["open"], costs, ttf_eligible=False, spread_pct=0.0,
        periods_per_year=252, risk_free_rate=0.0,
    )
    metrics_full = compute_metrics(
        pf, df["open"], costs, ttf_eligible=False, spread_pct=0.0, periods_per_year=252, risk_free_rate=0.0
    )

    # CAGR se rebase sur le premier point de chaque sous-période -> les
    # trois valeurs doivent être distinctes sur une trajectoire non
    # linéaire en %  (le prix est linéaire en valeur absolue, donc le
    # rendement % ralentit avec le temps).
    assert metrics_is.cagr != pytest.approx(metrics_oos.cagr)
    assert metrics_full.cagr != pytest.approx(metrics_is.cagr)
