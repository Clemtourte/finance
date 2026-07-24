"""Interface abstraite commune à toute stratégie systématique."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """Une stratégie transforme un historique OHLCV en positions cibles.

    Convention anti-look-ahead bias (voir `src/data/schema.py`) : la
    position cible à l'index J, retournée par `generate_signals`, ne doit
    être calculée qu'à partir des lignes de `df` jusqu'à J inclus (données
    disponibles à la clôture de J). L'exécution de ce signal — à l'open de
    J+1 — est de la responsabilité du moteur (`src.engine`), pas de la
    stratégie.
    """

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Calcule la position cible pour chaque date de `df`.

        Args:
            df: DataFrame OHLCV trié par date croissante (colonnes
                `open`, `high`, `low`, `close`, `adj_close`, `volume`).

        Returns:
            Series indexée comme `df.index`, valeurs dans `{-1, 0, 1}`
            (position cible short/flat/long). Les stratégies long-only
            (le cas par défaut pour un compte PEA, qui interdit la vente à
            découvert) doivent se limiter à `{0, 1}`.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def params(self) -> dict[str, object]:
        """Paramètres de la stratégie, pour traçabilité et balayage de grilles."""
        raise NotImplementedError
