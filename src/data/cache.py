"""Cache local Parquet, partitionné par ticker (un fichier par ticker).

Le cache ne connaît que des dates de bourse déjà téléchargées : il permet
de calculer la portion manquante d'un intervalle demandé afin que
l'ingestion ne re-télécharge jamais que le delta, jamais l'historique
complet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from src.data.schema import OHLCV_SCHEMA


@dataclass(frozen=True)
class DateRange:
    """Intervalle de dates inclusif des deux côtés."""

    start: date
    end: date


class ParquetCache:
    """Cache Parquet local pour les séries OHLCV, un fichier par ticker."""

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def file_path(self, ticker: str) -> Path:
        """Chemin du fichier Parquet de cache pour ce ticker."""
        return self.cache_dir / f"{ticker}.parquet"

    def read(self, ticker: str) -> pl.DataFrame:
        """Lit le cache d'un ticker.

        Args:
            ticker: Symbole du titre.

        Returns:
            DataFrame Polars trié par date, avec le schéma OHLCV. Vide (0
            ligne) si aucun cache n'existe encore pour ce ticker.
        """
        path = self.file_path(ticker)
        if not path.exists():
            return pl.DataFrame(schema=OHLCV_SCHEMA)
        return pl.read_parquet(path).sort("date")

    def cached_range(self, ticker: str) -> DateRange | None:
        """Renvoie la plage de dates déjà en cache pour un ticker.

        Returns:
            `DateRange` du min au max de la colonne `date`, ou `None` si le
            cache est vide ou absent.
        """
        df = self.read(ticker)
        if df.is_empty():
            return None
        return DateRange(start=df["date"].min(), end=df["date"].max())

    def missing_ranges(self, ticker: str, start: date, end: date) -> list[DateRange]:
        """Calcule les sous-intervalles de `[start, end]` absents du cache.

        Args:
            ticker: Symbole du titre.
            start: Première date (incluse) souhaitée.
            end: Dernière date (incluse) souhaitée.

        Returns:
            Liste de `DateRange` à télécharger. Vide si l'intervalle demandé
            est déjà entièrement couvert par le cache.

        Note:
            Le cache ne connaît que le min/max des dates déjà stockées, pas
            un calendrier de bourse. Si `start` tombe un jour non coté (ex.
            1er janvier), la plage manquante avant le premier jour coté en
            cache sera recalculée (et re-sondée, sans effet, réponse vide)
            à chaque appel. Sans conséquence sur les données (aucun
            doublon), juste un appel réseau superflu occasionnel.
        """
        if start > end:
            return []

        cached = self.cached_range(ticker)
        if cached is None:
            return [DateRange(start, end)]

        ranges: list[DateRange] = []
        if start < cached.start:
            ranges.append(DateRange(start, cached.start - timedelta(days=1)))
        if end > cached.end:
            ranges.append(DateRange(cached.end + timedelta(days=1), end))
        return ranges

    def upsert(self, ticker: str, ticker_col: str, new_rows: pl.DataFrame) -> pl.DataFrame:
        """Fusionne de nouvelles lignes dans le cache et réécrit le fichier.

        Args:
            ticker: Symbole du titre (nom du fichier de cache).
            ticker_col: Valeur à écrire dans la colonne `ticker` du schéma
                stocké (généralement identique à `ticker`).
            new_rows: Nouvelles lignes OHLCV (sans colonne `ticker`, ou avec
                — elle sera écrasée par `ticker_col`).

        Returns:
            Le DataFrame complet, fusionné et dédoublonné, tel qu'écrit sur
            disque.
        """
        if new_rows.is_empty():
            return self.read(ticker)

        rows = new_rows.with_columns(pl.lit(ticker_col).alias("ticker")).select(
            list(OHLCV_SCHEMA.keys())
        )
        existing = self.read(ticker)
        merged = (
            pl.concat([existing, rows], how="vertical_relaxed")
            .unique(subset=["date"], keep="last")
            .sort("date")
        )
        merged.write_parquet(self.file_path(ticker))
        return merged
