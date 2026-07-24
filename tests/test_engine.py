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
    resample_target_position,
    run_backtest,
    run_buy_and_hold,
    shift_to_execution,
)
from src.engine.costs import BrokerageTier, CostConfig, select_tier

#: Grille à un seul palier pourcentage, pratique pour les tests qui ne
#: portent pas spécifiquement sur les paliers de courtage.
def _flat_costs(pct_fee: float, slippage_pct: float = 0.0, ttf_pct: float = 0.0) -> CostConfig:
    return CostConfig(
        brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=pct_fee),),
        ttf_pct=ttf_pct,
        base_slippage_pct=slippage_pct,
    )


_ZERO_COSTS = _flat_costs(pct_fee=0.0)
_DEFAULT_COSTS = _flat_costs(pct_fee=0.006, slippage_pct=0.0005)


def _expected_closed_trade_value(
    entry_open: float, exit_open: float, costs: CostConfig, init_cash: float,
    ttf_eligible: bool = False, spread_pct: float = 0.0,
) -> float:
    """Valeur finale attendue d'un aller-retour long unique, tout-en capital.

    Reproduit le modèle de remplissage de vectorbt et la sélection de
    palier de `src.engine.costs` : prix d'entrée majoré du glissement,
    prix de sortie minoré du glissement, taille de position telle que
    `taille * prix_entrée * (1 + taux) + frais_fixe == capital_initial`,
    frais (courtage par palier + TTF à l'achat) prélevés à l'entrée ET à
    la sortie.
    """
    slippage = costs.base_slippage_pct + spread_pct
    entry_tier = select_tier(init_cash, costs.brokerage_tiers)
    entry_pct = (entry_tier.pct_fee or 0.0) + (costs.ttf_pct if ttf_eligible else 0.0)
    entry_fixed = entry_tier.fixed_fee or 0.0

    entry_fill = entry_open * (1 + slippage)
    size = (init_cash - entry_fixed) / (entry_fill * (1 + entry_pct))

    exit_tier = select_tier(size * exit_open, costs.brokerage_tiers)
    exit_pct = exit_tier.pct_fee or 0.0
    exit_fixed = exit_tier.fixed_fee or 0.0
    exit_fill = exit_open * (1 - slippage)

    return size * exit_fill - (size * exit_fill * exit_pct + exit_fixed)


def _expected_open_position_value(
    entry_open: float, last_close: float, costs: CostConfig, init_cash: float,
    ttf_eligible: bool = False, spread_pct: float = 0.0,
) -> float:
    """Valeur finale attendue d'une position jamais soldée (marquée au marché)."""
    slippage = costs.base_slippage_pct + spread_pct
    entry_tier = select_tier(init_cash, costs.brokerage_tiers)
    entry_pct = (entry_tier.pct_fee or 0.0) + (costs.ttf_pct if ttf_eligible else 0.0)
    entry_fixed = entry_tier.fixed_fee or 0.0

    entry_fill = entry_open * (1 + slippage)
    size = (init_cash - entry_fixed) / (entry_fill * (1 + entry_pct))
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


# --- resample_target_position : échantillonnage du rééquilibrage -----------


def test_resample_daily_is_a_noop():
    idx = pd.bdate_range("2024-01-01", periods=5)
    target = pd.Series([0, 1, 0, 1, 1], index=idx)
    resampled = resample_target_position(target, "daily")
    assert resampled.tolist() == target.tolist()


def test_resample_rejects_unsupported_frequency():
    idx = pd.bdate_range("2024-01-01", periods=5)
    target = pd.Series(1, index=idx)
    with pytest.raises(ValueError):
        resample_target_position(target, "hourly")


def test_resample_weekly_ignores_intraweek_oscillation():
    # 2024-01-01 est un lundi -> deux semaines pleines Lun-Ven.
    idx = pd.bdate_range("2024-01-01", periods=10)
    # Les deux vendredis (fin de semaine, index 4 et 9) valent 0 ; le
    # signal quotidien oscille en semaine mais ne doit jamais compter.
    target = pd.Series([0, 1, 0, 1, 0, 0, 1, 1, 0, 0], index=idx)

    resampled = resample_target_position(target, "weekly")

    assert (resampled == 0).all()


def test_resample_weekly_applies_change_decided_at_friday_close():
    idx = pd.bdate_range("2024-01-01", periods=10)
    # Vendredi de la semaine 1 (index 4) décide 1 ; le reste de la
    # semaine 2 confirme 1 (vendredi de la semaine 2, index 9, aussi 1).
    target = pd.Series([0, 0, 0, 0, 1, 1, 1, 1, 1, 1], index=idx)

    resampled = resample_target_position(target, "weekly")

    assert resampled.tolist() == [0, 0, 0, 0, 1, 1, 1, 1, 1, 1]


def test_resample_weekly_oscillation_generates_no_order_end_to_end():
    idx = pd.bdate_range("2024-01-01", periods=10)
    target = pd.Series([0, 1, 0, 1, 0, 0, 1, 1, 0, 0], index=idx)
    df = _linear_price_df(n=10)

    pf = run_backtest(df, target, _ZERO_COSTS, initial_capital=10_000.0, rebalance_freq="weekly")

    assert len(pf.orders.records_readable) == 0


def test_resample_weekly_end_to_end_respects_execution_lag():
    idx = pd.bdate_range("2024-01-01", periods=10)
    target = pd.Series([0, 0, 0, 0, 1, 1, 1, 1, 1, 1], index=idx)
    df = _linear_price_df(n=10)

    pf = run_backtest(df, target, _ZERO_COSTS, initial_capital=10_000.0, rebalance_freq="weekly")
    orders = pf.orders.records_readable

    assert len(orders) == 1
    assert orders.iloc[0]["Side"] == "Buy"
    # Décidé au vendredi de la semaine 1 (index 4) -> exécuté au lundi de
    # la semaine 2 (index 5), pas avant.
    assert orders.iloc[0]["Timestamp"] == df.index[5]
    assert orders.iloc[0]["Price"] == pytest.approx(df["open"].iloc[5])


# --- run_backtest : rendement connu sur série synthétique ------------------


def test_always_long_zero_cost_matches_price_appreciation_exactly():
    df = _linear_price_df(n=20)
    target = pd.Series(1, index=df.index)  # toujours long

    pf = run_backtest(df, target, _ZERO_COSTS, initial_capital=10_000.0)

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

    pf = run_backtest(df, target, _DEFAULT_COSTS, initial_capital=10_000.0)

    entry_open = df["open"].iloc[1]
    exit_open = df["open"].iloc[-1]
    expected = _expected_closed_trade_value(entry_open, exit_open, _DEFAULT_COSTS, 10_000.0)
    assert pf.value().iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_closed_round_trip_with_realistic_tiered_costs_and_ttf():
    """Reproduit config/backtest.yaml (paliers + TTF) sur un aller-retour, avec spread."""
    df = _linear_price_df(n=20)
    target = pd.Series(1, index=df.index)
    target.iloc[-2] = 0
    target.iloc[-1] = 0
    costs = CostConfig(
        brokerage_tiers=(
            BrokerageTier(max_order_value=500.0, fixed_fee=1.99),
            BrokerageTier(max_order_value=None, pct_fee=0.006),
        ),
        ttf_pct=0.004,
        base_slippage_pct=0.0005,
    )

    pf = run_backtest(
        df, target, costs, initial_capital=10_000.0, ttf_eligible=True, spread_pct=0.001
    )

    entry_open = df["open"].iloc[1]
    exit_open = df["open"].iloc[-1]
    expected = _expected_closed_trade_value(
        entry_open, exit_open, costs, 10_000.0, ttf_eligible=True, spread_pct=0.001
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

    one_pct = _flat_costs(pct_fee=0.01)

    value_zero = run_backtest(df, target, _ZERO_COSTS, initial_capital=10_000.0).value().iloc[-1]
    value_one_pct = run_backtest(df, target, one_pct, initial_capital=10_000.0).value().iloc[-1]

    expected_zero = _expected_closed_trade_value(entry_open, exit_open, _ZERO_COSTS, 10_000.0)
    expected_one_pct = _expected_closed_trade_value(entry_open, exit_open, one_pct, 10_000.0)

    assert value_zero == pytest.approx(expected_zero, rel=1e-9)
    assert value_one_pct == pytest.approx(expected_one_pct, rel=1e-9)
    assert value_one_pct < value_zero


def test_default_cost_config_produces_lower_value_than_zero_cost():
    df = _linear_price_df(n=30)
    target = pd.Series(1, index=df.index)

    with_costs = run_backtest(df, target, _DEFAULT_COSTS, initial_capital=10_000.0).value().iloc[-1]
    without_costs = run_backtest(df, target, _ZERO_COSTS, initial_capital=10_000.0).value().iloc[-1]

    assert with_costs < without_costs


def test_ttf_reduces_value_only_for_eligible_ticker():
    df = _linear_price_df(n=15)
    target = pd.Series(1, index=df.index)
    costs = _flat_costs(pct_fee=0.006, ttf_pct=0.004)

    eligible = run_backtest(
        df, target, costs, initial_capital=10_000.0, ttf_eligible=True
    ).value().iloc[-1]
    not_eligible = run_backtest(
        df, target, costs, initial_capital=10_000.0, ttf_eligible=False
    ).value().iloc[-1]

    assert eligible < not_eligible

    entry_open = df["open"].iloc[1]
    last_close = df["close"].iloc[-1]
    expected_eligible = _expected_open_position_value(
        entry_open, last_close, costs, 10_000.0, ttf_eligible=True
    )
    assert eligible == pytest.approx(expected_eligible, rel=1e-9)


def test_spread_pct_widens_slippage_symmetrically():
    df = _linear_price_df(n=20)
    target = pd.Series(1, index=df.index)
    target.iloc[-2] = 0
    target.iloc[-1] = 0

    no_spread = run_backtest(
        df, target, _DEFAULT_COSTS, initial_capital=10_000.0, spread_pct=0.0
    ).value().iloc[-1]
    with_spread = run_backtest(
        df, target, _DEFAULT_COSTS, initial_capital=10_000.0, spread_pct=0.01
    ).value().iloc[-1]

    assert with_spread < no_spread

    entry_open = df["open"].iloc[1]
    exit_open = df["open"].iloc[-1]
    expected = _expected_closed_trade_value(
        entry_open, exit_open, _DEFAULT_COSTS, 10_000.0, spread_pct=0.01
    )
    assert with_spread == pytest.approx(expected, rel=1e-9)


# --- validation des positions supportées ------------------------------------


def test_run_backtest_rejects_short_positions():
    df = _linear_price_df(n=10)
    target = pd.Series(-1, index=df.index)
    with pytest.raises(ValueError):
        run_backtest(df, target, _DEFAULT_COSTS, initial_capital=10_000.0)


# --- run_buy_and_hold --------------------------------------------------------


def test_buy_and_hold_enters_on_first_bar_open():
    df = _linear_price_df(n=15)

    pf = run_buy_and_hold(df, _DEFAULT_COSTS, initial_capital=10_000.0)

    expected = _expected_open_position_value(
        df["open"].iloc[0], df["close"].iloc[-1], _DEFAULT_COSTS, 10_000.0
    )
    assert pf.value().iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_buy_and_hold_applies_costs_by_default():
    df = _linear_price_df(n=15)

    with_costs = run_buy_and_hold(df, _DEFAULT_COSTS, initial_capital=10_000.0).value().iloc[-1]
    without_costs = run_buy_and_hold(df, _ZERO_COSTS, initial_capital=10_000.0).value().iloc[-1]

    assert with_costs < without_costs


# --- décalage d'exécution vérifié de bout en bout via vectorbt -------------


def test_execution_lag_end_to_end_via_order_timestamps():
    df = _linear_price_df(n=8)
    # Position longue décidée à la clôture de l'index 3, sortie décidée à
    # la clôture de l'index 5.
    target = pd.Series([0, 0, 0, 1, 1, 0, 0, 0], index=df.index)

    pf = run_backtest(df, target, _ZERO_COSTS, initial_capital=10_000.0)
    orders = pf.orders.records_readable.sort_values("Timestamp")

    assert len(orders) == 2
    buy, sell = orders.iloc[0], orders.iloc[1]
    assert buy["Side"] == "Buy"
    assert buy["Timestamp"] == df.index[4]  # J=3 (close) -> J+1=4 (open)
    assert buy["Price"] == pytest.approx(df["open"].iloc[4])
    assert sell["Side"] == "Sell"
    assert sell["Timestamp"] == df.index[6]  # J=5 (close) -> J+1=6 (open)
    assert sell["Price"] == pytest.approx(df["open"].iloc[6])
