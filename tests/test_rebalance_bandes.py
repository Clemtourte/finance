"""Tests de la stratégie de rééquilibrage par bandes
(src.strategies.rebalance_bandes)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.strategies.rebalance_bandes import (
    RebalanceBandesStrategy,
    load_rebalance_bandes_strategy,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _df(closes: list[float]) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"close": closes}, index=idx)


def test_rejects_non_positive_band():
    with pytest.raises(ValueError):
        RebalanceBandesStrategy(band_pct=0.0)
    with pytest.raises(ValueError):
        RebalanceBandesStrategy(band_pct=-0.1)


def test_first_bar_is_always_in_position():
    df = _df([100.0, 100.0, 100.0])
    strategy = RebalanceBandesStrategy(band_pct=0.1)
    signals = strategy.generate_signals(df)
    assert signals.iloc[0] == 1


def test_stays_in_position_while_within_band():
    df = _df([100.0, 105.0, 95.0, 108.0])  # toujours dans +/-10% de 100
    strategy = RebalanceBandesStrategy(band_pct=0.1)
    signals = strategy.generate_signals(df)
    assert (signals == 1).all()


def test_exits_when_drift_exceeds_band():
    df = _df([100.0, 100.0, 130.0, 130.0])  # +30% au-delà de la bande de 10%
    strategy = RebalanceBandesStrategy(band_pct=0.1)
    signals = strategy.generate_signals(df)
    assert signals.tolist() == [1, 1, 0, 0]


def test_reenters_after_reverting_within_band_of_original_reference():
    # référence = 100 ; sortie à 130 (+30%) ; reste dehors tant que le
    # prix ne revient pas dans [90, 110] (bande de la référence d'origine).
    df = _df([100.0, 130.0, 120.0, 105.0, 106.0])
    strategy = RebalanceBandesStrategy(band_pct=0.1)
    signals = strategy.generate_signals(df)
    assert signals.tolist() == [1, 0, 0, 1, 1]


def test_generate_signals_only_zero_or_one():
    rng = np.random.default_rng(0)
    closes = list(100 + np.cumsum(rng.normal(0, 2, 100)))
    df = _df(closes)
    strategy = RebalanceBandesStrategy(band_pct=0.1)
    signals = strategy.generate_signals(df)
    assert set(signals.unique()).issubset({0, 1})


def test_generate_signals_same_index_as_input():
    df = _df([100.0, 101.0, 102.0])
    strategy = RebalanceBandesStrategy(band_pct=0.1)
    signals = strategy.generate_signals(df)
    assert signals.index.equals(df.index)


def test_generate_signals_no_lookahead():
    rng = np.random.default_rng(1)
    closes = list(100 + np.cumsum(rng.normal(0, 3, 200)))
    df = _df(closes)
    strategy = RebalanceBandesStrategy(band_pct=0.1)
    full = strategy.generate_signals(df)

    for j in (50, 100, 150, 199):
        truncated = strategy.generate_signals(df.iloc[: j + 1])
        checkpoint_date = df.index[j]
        assert full.loc[checkpoint_date] == truncated.iloc[-1]


def test_params_property():
    strategy = RebalanceBandesStrategy(band_pct=0.15)
    assert strategy.params == {"band_pct": 0.15}


def test_load_rebalance_bandes_strategy_from_real_config():
    strategy = load_rebalance_bandes_strategy(
        PROJECT_ROOT / "config" / "strategies" / "rebalance_bandes.yaml"
    )
    assert strategy.band_pct > 0
