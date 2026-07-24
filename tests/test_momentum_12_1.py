"""Tests de la stratégie momentum 12-1 (src.strategies.momentum_12_1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.strategies.momentum_12_1 import Momentum12_1Strategy, load_momentum_12_1_strategy

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _synthetic_close(n: int = 300, seed: int = 0) -> pd.DataFrame:
    idx = pd.bdate_range("2022-01-01", periods=n)
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({"close": close}, index=idx)


def test_rejects_skip_days_greater_or_equal_lookback():
    with pytest.raises(ValueError):
        Momentum12_1Strategy(lookback_days=21, skip_days=252)
    with pytest.raises(ValueError):
        Momentum12_1Strategy(lookback_days=21, skip_days=21)


def test_rejects_non_positive_lookback():
    with pytest.raises(ValueError):
        Momentum12_1Strategy(lookback_days=0, skip_days=0)


def test_rejects_negative_skip():
    with pytest.raises(ValueError):
        Momentum12_1Strategy(lookback_days=252, skip_days=-1)


def test_generate_signals_only_zero_or_one():
    df = _synthetic_close()
    strategy = Momentum12_1Strategy(lookback_days=100, skip_days=10)
    signals = strategy.generate_signals(df)
    assert set(signals.unique()).issubset({0, 1})


def test_generate_signals_same_index_as_input():
    df = _synthetic_close()
    strategy = Momentum12_1Strategy(lookback_days=100, skip_days=10)
    signals = strategy.generate_signals(df)
    assert signals.index.equals(df.index)


def test_generate_signals_flat_during_warmup():
    df = _synthetic_close()
    strategy = Momentum12_1Strategy(lookback_days=100, skip_days=10)
    signals = strategy.generate_signals(df)
    # formation_return n'est défini qu'à partir de l'index lookback_days (100).
    assert (signals.iloc[:100] == 0).all()


def test_generate_signals_matches_manual_formula():
    df = _synthetic_close()
    strategy = Momentum12_1Strategy(lookback_days=100, skip_days=10)
    signals = strategy.generate_signals(df)

    close = df["close"]
    formation_return = close.shift(10) / close.shift(100) - 1
    expected = (formation_return > 0).astype(int)
    expected[formation_return.isna()] = 0

    pd.testing.assert_series_equal(signals, expected, check_names=False)


def test_generate_signals_no_lookahead():
    df = _synthetic_close(n=300)
    strategy = Momentum12_1Strategy(lookback_days=100, skip_days=10)
    full = strategy.generate_signals(df)

    for j in (150, 200, 250, 299):
        truncated = strategy.generate_signals(df.iloc[: j + 1])
        checkpoint_date = df.index[j]
        assert full.loc[checkpoint_date] == truncated.iloc[-1]


def test_params_property():
    strategy = Momentum12_1Strategy(lookback_days=200, skip_days=15)
    assert strategy.params == {"lookback_days": 200, "skip_days": 15}


def test_load_momentum_12_1_strategy_from_real_config():
    strategy = load_momentum_12_1_strategy(
        PROJECT_ROOT / "config" / "strategies" / "momentum_12_1.yaml"
    )
    assert strategy.skip_days < strategy.lookback_days
    assert strategy.lookback_days > 0
