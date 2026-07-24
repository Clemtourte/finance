"""Indicateurs de volatilité (ATR, bandes de Bollinger)."""

from __future__ import annotations

import pandas_ta as ta
import pandas as pd

from src.indicators._util import column_starting_with


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average True Range.

    Args:
        df: DataFrame avec les colonnes `high`, `low`, `close`, indexé par
            date croissante.
        length: Fenêtre de calcul, en nombre de séances.

    Returns:
        Series nommée `"atr"`, même index que `df`.
    """
    raw = ta.atr(high=df["high"], low=df["low"], close=df["close"], length=length)
    return raw.rename("atr")


def bollinger_bands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Bandes de Bollinger.

    Args:
        close: Série de prix, indexée par date croissante.
        length: Fenêtre de la moyenne mobile centrale, en nombre de séances.
        std: Nombre d'écarts-types définissant la largeur des bandes.

    Returns:
        DataFrame avec les colonnes `"lower"`, `"mid"`, `"upper"`, même
        index que `close`.
    """
    raw = ta.bbands(close, length=length, lower_std=std, upper_std=std)
    return pd.DataFrame(
        {
            "lower": raw[column_starting_with(raw, "BBL_")],
            "mid": raw[column_starting_with(raw, "BBM_")],
            "upper": raw[column_starting_with(raw, "BBU_")],
        },
        index=raw.index,
    )
