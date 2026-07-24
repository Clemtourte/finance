"""Tests des wrappers d'indicateurs (src.indicators).

Le test le plus important de ce fichier est le test de non-look-ahead
(`test_*_no_lookahead`) : il vérifie mécaniquement que la valeur d'un
indicateur à la date J, calculée sur la série complète, est identique à
celle obtenue en calculant l'indicateur sur la série tronquée à J. Si un
indicateur utilisait ne serait-ce qu'une observation postérieure à J, ce
test échouerait.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators.momentum import macd, rsi
from src.indicators.trend import ema, sma
from src.indicators.volatility import atr, bollinger_bands

N = 120
_CHECKPOINTS = [40, 60, 80, 100, N - 1]


def _synthetic_ohlc(n: int = N, seed: int = 0) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx)
    high = close + rng.uniform(0.5, 1.5, n)
    low = close - rng.uniform(0.5, 1.5, n)
    return pd.DataFrame({"close": close, "high": high, "low": low})


def _assert_equal_or_both_nan(a: float, b: float, tol: float = 1e-9) -> None:
    if pd.isna(a) and pd.isna(b):
        return
    assert not pd.isna(a) and not pd.isna(b), f"one is NaN, the other isn't: {a} vs {b}"
    assert abs(a - b) <= tol * max(1.0, abs(a)), f"{a} != {b}"


def _assert_no_lookahead_series(compute: callable, close: pd.Series) -> None:
    full = compute(close)
    for j in _CHECKPOINTS:
        truncated = compute(close.iloc[: j + 1])
        checkpoint_date = close.index[j]
        _assert_equal_or_both_nan(full.loc[checkpoint_date], truncated.iloc[-1])


def _assert_no_lookahead_frame(compute: callable, arg, checkpoints: list[int]) -> None:
    full = compute(arg)
    for j in checkpoints:
        truncated = compute(arg.iloc[: j + 1])
        checkpoint_date = arg.index[j]
        full_row = full.loc[checkpoint_date]
        truncated_row = truncated.iloc[-1]
        for col in full.columns:
            _assert_equal_or_both_nan(full_row[col], truncated_row[col])


# --- non-régression look-ahead -----------------------------------------


def test_sma_no_lookahead():
    df = _synthetic_ohlc()
    _assert_no_lookahead_series(lambda s: sma(s, length=20), df["close"])


def test_ema_no_lookahead():
    df = _synthetic_ohlc()
    _assert_no_lookahead_series(lambda s: ema(s, length=20), df["close"])


def test_rsi_no_lookahead():
    df = _synthetic_ohlc()
    _assert_no_lookahead_series(lambda s: rsi(s, length=14), df["close"])


def test_macd_no_lookahead():
    df = _synthetic_ohlc()
    _assert_no_lookahead_frame(lambda s: macd(s, fast=12, slow=26, signal=9), df["close"], _CHECKPOINTS)


def test_atr_no_lookahead():
    df = _synthetic_ohlc()
    _assert_no_lookahead_frame(lambda d: atr(d, length=14).to_frame("atr"), df, _CHECKPOINTS)


def test_bollinger_bands_no_lookahead():
    df = _synthetic_ohlc()
    _assert_no_lookahead_frame(lambda s: bollinger_bands(s, length=20, std=2.0), df["close"], _CHECKPOINTS)


# --- correction / sanité de base ----------------------------------------


def test_sma_matches_manual_rolling_mean():
    close = _synthetic_ohlc()["close"]
    expected = close.rolling(10).mean()
    result = sma(close, length=10)
    pd.testing.assert_series_equal(result, expected, check_names=False)


def test_sma_preserves_index_order():
    close = _synthetic_ohlc()["close"]
    result = sma(close, length=10)
    assert result.index.equals(close.index)


def test_rsi_bounded_between_0_and_100():
    close = _synthetic_ohlc()["close"]
    result = rsi(close, length=14).dropna()
    assert (result >= 0).all()
    assert (result <= 100).all()


def test_bollinger_bands_ordering():
    close = _synthetic_ohlc()["close"]
    bb = bollinger_bands(close, length=20, std=2.0).dropna()
    assert (bb["upper"] >= bb["mid"]).all()
    assert (bb["mid"] >= bb["lower"]).all()


def test_atr_is_non_negative():
    df = _synthetic_ohlc()
    result = atr(df, length=14).dropna()
    assert (result >= 0).all()


def test_macd_hist_equals_macd_minus_signal():
    close = _synthetic_ohlc()["close"]
    result = macd(close).dropna()
    diff = (result["macd"] - result["signal"]) - result["hist"]
    assert (diff.abs() < 1e-9).all()
