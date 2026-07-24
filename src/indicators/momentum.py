"""Indicateurs de momentum (RSI, MACD)."""

from __future__ import annotations

import pandas_ta as ta
import pandas as pd

from src.indicators._util import column_starting_with


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index.

    Args:
        close: Série de prix, indexée par date croissante.
        length: Fenêtre de calcul, en nombre de séances.

    Returns:
        Series nommée `"rsi"`, même index que `close`, valeurs entre 0 et 100.
    """
    return ta.rsi(close, length=length).rename("rsi")


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Moving Average Convergence Divergence.

    Args:
        close: Série de prix, indexée par date croissante.
        fast: Fenêtre de l'EMA rapide.
        slow: Fenêtre de l'EMA lente.
        signal: Fenêtre de l'EMA de la ligne de signal.

    Returns:
        DataFrame avec les colonnes `"macd"`, `"signal"`, `"hist"`, même
        index que `close`.
    """
    raw = ta.macd(close, fast=fast, slow=slow, signal=signal)
    return pd.DataFrame(
        {
            "macd": raw[column_starting_with(raw, "MACD_")],
            "signal": raw[column_starting_with(raw, "MACDs_")],
            "hist": raw[column_starting_with(raw, "MACDh_")],
        },
        index=raw.index,
    )
