"""Indicateurs de tendance (moyennes mobiles).

Chaque fonction est un fin wrapper autour de `pandas_ta`, ne modifiant ni
l'ordre ni l'index de la série d'entrée : la valeur à l'index J n'est
calculée qu'à partir des observations jusqu'à J inclus (propriété causale
des moyennes mobiles glissantes), condition nécessaire à l'absence de
look-ahead bias documentée dans `src/data/schema.py`.
"""

from __future__ import annotations

import pandas_ta as ta
import pandas as pd


def sma(close: pd.Series, length: int) -> pd.Series:
    """Moyenne mobile simple.

    Args:
        close: Série de prix (typiquement `close` ou `adj_close`), indexée
            par date croissante.
        length: Fenêtre de la moyenne mobile, en nombre de séances.

    Returns:
        Series nommée `"sma"`, même index que `close`. `NaN` pour les
        `length - 1` premières observations (période de chauffe).
    """
    return ta.sma(close, length=length).rename("sma")


def ema(close: pd.Series, length: int) -> pd.Series:
    """Moyenne mobile exponentielle.

    Args:
        close: Série de prix, indexée par date croissante.
        length: Fenêtre de la moyenne mobile, en nombre de séances.

    Returns:
        Series nommée `"ema"`, même index que `close`.
    """
    return ta.ema(close, length=length).rename("ema")
