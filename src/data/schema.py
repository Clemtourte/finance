"""Schéma commun des données OHLCV utilisé par tout le pipeline de données.

Convention anti-look-ahead bias (voir aussi README) : les données stockées
ici sont des données de clôture. Un signal calculé sur la ligne du jour J
(quel que soit l'indicateur) ne doit utiliser que les colonnes de cette
ligne (disponibles à la clôture de J). Toute exécution simulée doit se
faire sur l'`open` du jour J+1, jamais sur le `close` ou l'`open` de J.
Cette règle est documentée ici car c'est le point de départ de toute
stratégie future ; elle sera appliquée dans `src/engine`.
"""

from __future__ import annotations

import polars as pl

#: Ordre et noms canoniques des colonnes OHLCV stockées en cache et en base.
OHLCV_COLUMNS: list[str] = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
]

#: Schéma Polars associé, utilisé pour valider les DataFrames avant écriture.
OHLCV_SCHEMA: dict[str, pl.PolarsDataType] = {
    "ticker": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "adj_close": pl.Float64,
    "volume": pl.Int64,
}
