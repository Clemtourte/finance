"""Interface abstraite pour les fournisseurs de données de marché.

Toute source de données (yfinance en phase 1, EODHD ou Tiingo plus tard)
doit implémenter :class:`DataProvider` pour rester interchangeable avec le
reste du pipeline (cache, validation, chargement DuckDB).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from src.data.schema import OHLCV_COLUMNS


class DataProvider(ABC):
    """Fournisseur de données OHLCV pour un ticker donné.

    Une implémentation doit renvoyer un DataFrame pandas indexé par date,
    couvrant uniquement l'intervalle demandé, avec les colonnes de
    :data:`src.data.schema.OHLCV_COLUMNS` (hors ``ticker``, ajoutée par
    l'appelant si besoin).
    """

    @abstractmethod
    def get_ohlcv(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Télécharge l'OHLCV ajusté d'un ticker sur l'intervalle demandé.

        Args:
            ticker: Symbole du titre au format attendu par le provider
                (ex. ``"AI.PA"`` pour yfinance).
            start: Première date (incluse) de l'intervalle demandé.
            end: Dernière date (incluse) de l'intervalle demandé.

        Returns:
            DataFrame avec une colonne ``date`` (``datetime.date``) et les
            colonnes ``open``, ``high``, ``low``, ``close``, ``adj_close``,
            ``volume``. Vide (mêmes colonnes, zéro ligne) si aucune donnée
            n'est disponible sur l'intervalle, jamais ``None``.

        Raises:
            DataProviderError: Si la requête échoue après épuisement des
                tentatives de nouvelle connexion.
        """
        raise NotImplementedError


class DataProviderError(RuntimeError):
    """Erreur levée quand un provider ne parvient pas à récupérer les données."""


def empty_ohlcv_frame() -> pd.DataFrame:
    """Construit un DataFrame OHLCV vide avec les colonnes canoniques.

    Returns:
        DataFrame vide (0 ligne) avec les colonnes de
        :data:`src.data.schema.OHLCV_COLUMNS` moins ``ticker``.
    """
    columns = [c for c in OHLCV_COLUMNS if c != "ticker"]
    return pd.DataFrame(columns=columns)
