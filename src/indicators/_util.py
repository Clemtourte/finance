"""Utilitaires internes partagés par les wrappers d'indicateurs."""

from __future__ import annotations

import pandas as pd


def column_starting_with(df: pd.DataFrame, prefix: str) -> str:
    """Trouve l'unique colonne de `df` commençant par `prefix`.

    Utilisé plutôt qu'un nom de colonne exact car `pandas_ta` encode ses
    paramètres dans le nom de colonne (ex. `BBL_20_2.0_2.0`,
    `MACDs_12_26_9`) avec un formatage qui peut varier selon le type des
    arguments ; ne dépendre que du préfixe évite de recoder ce formatage.

    Args:
        df: DataFrame renvoyé par une fonction `pandas_ta`.
        prefix: Préfixe attendu du nom de colonne (ex. `"BBL_"`).

    Returns:
        Le nom de colonne trouvé.

    Raises:
        ValueError: Si zéro ou plusieurs colonnes correspondent.
    """
    matches = [c for c in df.columns if c.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"Attendu une colonne préfixée par {prefix!r}, trouvé {matches}")
    return matches[0]
