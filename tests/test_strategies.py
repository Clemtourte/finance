"""Tests de la stratégie de référence SMA crossover (src.strategies)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.strategies.sma_crossover import SmaCrossoverStrategy, load_sma_crossover_strategy

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _synthetic_close(n: int = 100, seed: int = 0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({"close": close}, index=idx)


def test_rejects_fast_period_greater_or_equal_to_slow():
    with pytest.raises(ValueError):
        SmaCrossoverStrategy(fast_period=50, slow_period=20)
    with pytest.raises(ValueError):
        SmaCrossoverStrategy(fast_period=20, slow_period=20)


def test_rejects_non_positive_periods():
    with pytest.raises(ValueError):
        SmaCrossoverStrategy(fast_period=0, slow_period=20)


def test_generate_signals_only_zero_or_one():
    df = _synthetic_close()
    strategy = SmaCrossoverStrategy(fast_period=5, slow_period=20)
    signals = strategy.generate_signals(df)
    assert set(signals.unique()).issubset({0, 1})


def test_generate_signals_same_index_as_input():
    df = _synthetic_close()
    strategy = SmaCrossoverStrategy(fast_period=5, slow_period=20)
    signals = strategy.generate_signals(df)
    assert signals.index.equals(df.index)


def test_generate_signals_flat_during_warmup():
    df = _synthetic_close()
    strategy = SmaCrossoverStrategy(fast_period=5, slow_period=20)
    signals = strategy.generate_signals(df)
    assert (signals.iloc[:19] == 0).all()  # slow SMA (20) pas encore défini avant l'index 19


def test_generate_signals_matches_manual_crossover_logic():
    df = _synthetic_close()
    strategy = SmaCrossoverStrategy(fast_period=5, slow_period=20)
    signals = strategy.generate_signals(df)

    fast = df["close"].rolling(5).mean()
    slow = df["close"].rolling(20).mean()
    expected = (fast > slow).astype(int)
    expected[fast.isna() | slow.isna()] = 0

    pd.testing.assert_series_equal(signals, expected, check_names=False)


def test_params_property_exposes_periods():
    strategy = SmaCrossoverStrategy(fast_period=10, slow_period=30)
    assert strategy.params == {"fast_period": 10, "slow_period": 30}


def test_load_sma_crossover_strategy_from_real_config():
    strategy = load_sma_crossover_strategy(PROJECT_ROOT / "config" / "strategies" / "sma_crossover.yaml")
    assert strategy.fast_period < strategy.slow_period
    assert strategy.fast_period > 0
