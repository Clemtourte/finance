"""Orchestration de l'ingestion : provider -> cache -> validation -> DuckDB.

Point d'entrée en ligne de commande :

    uv run python -m src.data.ingest --config config/data.yaml

Pour ingérer un univers différent de celui de `--config` sans éditer le
fichier de configuration (ex. exécution automatisée) :

    uv run python -m src.data.ingest --config config/data.yaml --universe-file config/universe_etf_pea.yaml

Par défaut, la plage antérieure à la première date déjà en cache pour un
ticker n'est jamais re-sondée (voir `src.data.cache.ParquetCache.
missing_ranges`) : un titre dont l'historique disponible est plus court
que `start_date` produirait sinon une erreur "possibly delisted" à
chaque exécution, bruit qui masque une vraie radiation. Pour forcer
cette re-sonde (ex. la source a depuis publié un historique plus
profond) :

    uv run python -m src.data.ingest --config config/data.yaml --backfill

En fin d'exécution, un rapport de validation détaillé (trous, valeurs
aberrantes, splits suspects, séance par séance) est affiché sur la sortie
standard (voir `src.reporting.validation.format_validation_summary`).
Passer `--quiet` pour le supprimer ; les logs restent inchangés.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict
from datetime import date

import polars as pl

from src.data.cache import ParquetCache
from src.data.config import DataConfig, TickerInfo, load_data_config, load_universe
from src.data.duckdb_loader import DuckDBLoader
from src.data.provider import DataProvider
from src.data.validation import ValidationReport, validate_ohlcv
from src.data.yfinance_provider import YFinanceProvider
from src.reporting.validation import format_validation_summary

logger = logging.getLogger(__name__)


def sync_ticker(
    provider: DataProvider,
    cache: ParquetCache,
    ticker: str,
    start: date,
    end: date,
    *,
    backfill: bool = False,
) -> pl.DataFrame:
    """Met à jour le cache d'un ticker en ne téléchargeant que le delta manquant.

    Args:
        provider: Source de données à interroger pour le delta manquant.
        cache: Cache Parquet local du ticker.
        ticker: Symbole du titre.
        start: Première date (incluse) souhaitée.
        end: Dernière date (incluse) souhaitée.
        backfill: Transmis à `ParquetCache.missing_ranges` : si `True`,
            re-sonde aussi la plage antérieure à la première date déjà en
            cache (voir sa docstring).

    Returns:
        DataFrame Polars couvrant `[start, end]`, servi depuis le cache
        après mise à jour.
    """
    for missing in cache.missing_ranges(ticker, start, end, backfill=backfill):
        raw = provider.get_ohlcv(ticker, missing.start, missing.end)
        if not raw.empty:
            cache.upsert(ticker, ticker, pl.from_pandas(raw))

    full = cache.read(ticker)
    return full.filter((pl.col("date") >= start) & (pl.col("date") <= end))


def run_ingestion(
    config: DataConfig,
    universe: list[TickerInfo],
    provider: DataProvider | None = None,
    *,
    backfill: bool = False,
) -> dict[str, ValidationReport]:
    """Exécute l'ingestion complète pour tout un univers de tickers.

    Args:
        config: Configuration de la couche données.
        universe: Liste des titres à ingérer.
        provider: Provider à utiliser ; par défaut `YFinanceProvider`
            construit depuis `config.yfinance`. Injecter un autre provider
            (ex. un double de test) pour éviter les appels réseau.
        backfill: Transmis à `sync_ticker`/`ParquetCache.missing_ranges` :
            si `True`, re-sonde aussi la plage antérieure à la première
            date déjà en cache pour chaque ticker.

    Returns:
        Dictionnaire `ticker -> ValidationReport` pour inspection.
    """
    provider = provider or YFinanceProvider(**asdict(config.yfinance))
    cache = ParquetCache(config.cache_dir)
    reports: dict[str, ValidationReport] = {}

    with DuckDBLoader(config.duckdb_path, config.duckdb_table) as loader:
        for info in universe:
            logger.info("Synchronisation %s (%s)", info.ticker, info.name)
            df = sync_ticker(
                provider, cache, info.ticker, config.start_date, config.end_date, backfill=backfill
            )
            df_pd = df.to_pandas()

            report = validate_ohlcv(
                df_pd,
                ticker=info.ticker,
                max_gap_calendar_days=config.validation.max_gap_calendar_days,
                outlier_return_threshold=config.validation.outlier_return_threshold,
                split_ratio_tolerance=config.validation.split_ratio_tolerance,
            )
            reports[info.ticker] = report
            if report.has_issues:
                logger.warning(
                    "%s: %d trou(s), %d valeur(s) aberrante(s), %d split(s) suspect(s)",
                    info.ticker,
                    len(report.gaps),
                    len(report.outliers),
                    len(report.unadjusted_splits),
                )

            row_count = loader.load_ticker(info.ticker, cache.file_path(info.ticker))
            logger.info("%s: %d lignes chargées dans DuckDB", info.ticker, row_count)

    return reports


def main() -> None:
    """CLI : lance l'ingestion complète depuis un fichier de configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/data.yaml", help="Chemin du fichier de configuration")
    parser.add_argument(
        "--universe-file", default=None, help="Fichier d'univers (ex. config/universe_cac40.yaml)"
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Re-sonde aussi la plage antérieure à la première date déjà en cache "
        "pour chaque ticker (désactivé par défaut, voir docstring de module)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Supprime le rapport de validation détaillé affiché en fin d'exécution "
        "(les logs restent inchangés)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_data_config(args.config)
    universe_file = args.universe_file or config.universe_file
    universe = load_universe(universe_file)
    reports = run_ingestion(config, universe, backfill=args.backfill)

    tickers_with_issues = [t for t, r in reports.items() if r.has_issues]
    if tickers_with_issues:
        logger.warning("Tickers avec anomalies détectées : %s", ", ".join(tickers_with_issues))
    logger.info("Ingestion terminée : %d tickers traités", len(reports))

    if not args.quiet:
        print(format_validation_summary(reports))


if __name__ == "__main__":
    main()
