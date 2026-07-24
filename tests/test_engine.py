"""Tests du moteur de backtest (src.engine.backtest).

La convention d'exécution (signal décidé à la clôture de J, ordre exécuté
à l'ouverture de J+1) et l'application obligatoire des coûts de
transaction sont les deux propriétés les plus critiques de ce module ;
elles sont vérifiées ici à la fois unitairement (fonctions pures) et de
bout en bout (via `vectorbt.Portfolio`), avec des valeurs attendues
calculées analytiquement plutôt que simplement "avant/après" pour éviter
de valider un moteur contre lui-même.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.engine.backtest import (
    positions_to_entries_exits,
    run_backtest,
    run_buy_and_hold,
    shift_to_execution,
)
from src.engine.config import CostConfig


def _expected_closed_trade_value(
    entry_open: float, exit_open: float, fees: float, slippage: float, init_cash: float
) -> float:
    """Valeur finale attendue d'un aller-retour long unique, tout-en capital.

    Reproduit le modèle de remplissage de vectorbt : prix d'entrée majoré
    du glissement, prix de sortie minoré du glissement, taille de position
    telle que `taille * prix_entrée * (1 + frais) == capital_initial`,
    frais prélevés à l'entrée ET à la sortie.
    """
    entry_fill = entry_open * (1 + slippage)
    exit_fill = exit_open * (1 - slippage)
    size = init_cash / (entry_fill * (1 + fees))
    entry_fee = size * entry_fill * fees
    exit_fee = size * exit_fill * fees
    pnl = (size * exit_fill - exit_fee) - (size * entry_fill + entry_fee)
    return init_cash + pnl


def _expected_open_position_value(
    entry_open: float, last_close: float, fees: float, slippage: float, init_cash: float
) -> float:
    """Valeur finale attendue d'une position jamais soldée (marquée au marché)."""
    entry_fill = entry_open * (1 + slippage)
    size = init_cash / (entry_fill * (1 + fees))
    return size * last_close


def _linear_price_df(n: int = 20, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    close = pd.Series([start + i * step for i in range(n)], index=idx)
    open_ = close.shift(1).fillna(start - step)
    return pd.DataFrame({"open": open_, "close": close})


# --- fonctions pures : décalage et conversion en entrées/sorties -----------


def test_shift_to_execution_delays_by_one_bar():
    idx = pd.bdate_range("2024-01-01", periods=5)
    target = pd.Series([0, 1, 1, 0, 0], index=idx)
    shifted = shift_to_execution(target)
    assert shifted.tolist() == [0, 0, 1, 1, 0]


def test_shift_to_execution_first_bar_is_always_flat():
    idx = pd.bdate_range("2024-01-01", periods=3)
    target = pd.Series([1, 1, 1], index=idx)
    shifted = shift_to_execution(target)
    assert shifted.iloc[0] == 0


def test_positions_to_entries_exits_marks_transitions_only():
    idx = pd.bdate_range("2024-01-01", periods=6)
    execution_position = pd.Series([0, 0, 1, 1, 1, 0], index=idx)
    entries, exits = positions_to_entries_exits(execution_position)
    assert entries.tolist() == [False, False, True, False, False, False]
    assert exits.tolist() == [False, False, False, False, False, True]


# --- run_backtest : rendement connu sur série synthétique ------------------


def test_always_long_zero_cost_matches_price_appreciation_exactly():
    df = _linear_price_df(n=20)
    target = pd.Series(1, index=df.index)  # toujours long
    zero_costs = CostConfig(brokerage_fee_pct=0.0, slippage_pct=0.0)

    pf = run_backtest(df, target, zero_costs, initial_capital=10_000.0)

    # Décidé à la clôture du jour 0 -> exécuté à l'open du jour 1 ; jamais
    # sorti -> valeur marquée au marché sur le dernier close.
    entry_open = df["open"].iloc[1]
    last_close = df["close"].iloc[-1]
    expected = 10_000.0 / entry_open * last_close
    assert pf.value().iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_closed_round_trip_matches_analytical_cost_formula():
    df = _linear_price_df(n=20)
    target = pd.Series(1, index=df.index)
    # Décision de sortie prise à la clôture de l'avant-avant-dernier jour,
    # pour que l'ordre de sortie (J+1) tombe sur le tout dernier bar visible.
    target.iloc[-2] = 0
    target.iloc[-1] = 0
    costs = CostConfig(brokerage_fee_pct=0.006, slippage_pct=0.0005)

    pf = run_backtest(df, target, costs, initial_capital=10_000.0)

    entry_open = df["open"].iloc[1]
    exit_open = df["open"].iloc[-1]
    expected = _expected_closed_trade_value(
        entry_open, exit_open, costs.brokerage_fee_pct, costs.slippage_pct, 10_000.0
    )
    assert pf.value().iloc[-1] == pytest.approx(expected, rel=1e-9)


# --- coûts : paramètre obligatoire, sensibilité vérifiée -------------------


def test_zero_cost_beats_nonzero_cost_by_expected_amount():
    df = _linear_price_df(n=20)
    target = pd.Series(1, index=df.index)
    target.iloc[-2] = 0
    target.iloc[-1] = 0
    entry_open = df["open"].iloc[1]
    exit_open = df["open"].iloc[-1]

    zero = CostConfig(brokerage_fee_pct=0.0, slippage_pct=0.0)
    one_pct = CostConfig(brokerage_fee_pct=0.01, slippage_pct=0.0)

    value_zero = run_backtest(df, target, zero, initial_capital=10_000.0).value().iloc[-1]
    value_one_pct = run_backtest(df, target, one_pct, initial_capital=10_000.0).value().iloc[-1]

    expected_zero = _expected_closed_trade_value(entry_open, exit_open, 0.0, 0.0, 10_000.0)
    expected_one_pct = _expected_closed_trade_value(entry_open, exit_open, 0.01, 0.0, 10_000.0)

    assert value_zero == pytest.approx(expected_zero, rel=1e-9)
    assert value_one_pct == pytest.approx(expected_one_pct, rel=1e-9)
    assert value_one_pct < value_zero


def test_default_cost_config_produces_lower_value_than_zero_cost():
    df = _linear_price_df(n=30)
    target = pd.Series(1, index=df.index)
    default_costs = CostConfig(brokerage_fee_pct=0.006, slippage_pct=0.0005)
    zero_costs = CostConfig(brokerage_fee_pct=0.0, slippage_pct=0.0)

    with_costs = run_backtest(df, target, default_costs, initial_capital=10_000.0).value().iloc[-1]
    without_costs = run_backtest(df, target, zero_costs, initial_capital=10_000.0).value().iloc[-1]

    assert with_costs < without_costs


# --- validation des positions supportées ------------------------------------


def test_run_backtest_rejects_short_positions():
    df = _linear_price_df(n=10)
    target = pd.Series(-1, index=df.index)
    costs = CostConfig(brokerage_fee_pct=0.006, slippage_pct=0.0005)
    with pytest.raises(ValueError):
        run_backtest(df, target, costs, initial_capital=10_000.0)


# --- run_buy_and_hold --------------------------------------------------------


def test_buy_and_hold_enters_on_first_bar_open():
    df = _linear_price_df(n=15)
    costs = CostConfig(brokerage_fee_pct=0.006, slippage_pct=0.0005)

    pf = run_buy_and_hold(df, costs, initial_capital=10_000.0)

    expected = _expected_open_position_value(
        df["open"].iloc[0], df["close"].iloc[-1], costs.brokerage_fee_pct, costs.slippage_pct, 10_000.0
    )
    assert pf.value().iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_buy_and_hold_applies_costs_by_default():
    df = _linear_price_df(n=15)
    costs = CostConfig(brokerage_fee_pct=0.006, slippage_pct=0.0005)
    zero_costs = CostConfig(brokerage_fee_pct=0.0, slippage_pct=0.0)

    with_costs = run_buy_and_hold(df, costs, initial_capital=10_000.0).value().iloc[-1]
    without_costs = run_buy_and_hold(df, zero_costs, initial_capital=10_000.0).value().iloc[-1]

    assert with_costs < without_costs


# --- décalage d'exécution vérifié de bout en bout via vectorbt -------------


def test_execution_lag_end_to_end_via_order_timestamps():
    df = _linear_price_df(n=8)
    # Position longue décidée à la clôture de l'index 3, sortie décidée à
    # la clôture de l'index 5.
    target = pd.Series([0, 0, 0, 1, 1, 0, 0, 0], index=df.index)
    zero_costs = CostConfig(brokerage_fee_pct=0.0, slippage_pct=0.0)

    pf = run_backtest(df, target, zero_costs, initial_capital=10_000.0)
    orders = pf.orders.records_readable.sort_values("Timestamp")

    assert len(orders) == 2
    buy, sell = orders.iloc[0], orders.iloc[1]
    assert buy["Side"] == "Buy"
    assert buy["Timestamp"] == df.index[4]  # J=3 (close) -> J+1=4 (open)
    assert buy["Price"] == pytest.approx(df["open"].iloc[4])
    assert sell["Side"] == "Sell"
    assert sell["Timestamp"] == df.index[6]  # J=5 (close) -> J+1=6 (open)
    assert sell["Price"] == pytest.approx(df["open"].iloc[6])
