"""Tests de chargement de la configuration (src.data.config).

Ces tests chargent les fichiers YAML réels du projet (config/data.yaml,
config/universe_cac40.yaml) : ils servent aussi de garde-fou contre une
config invalide ou un univers mal formé.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.data.config import load_data_config, load_universe

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_load_data_config_reads_real_file():
    config = load_data_config(PROJECT_ROOT / "config" / "data.yaml")

    assert config.start_date == date(2010, 1, 1)
    assert config.end_date <= date.today()
    assert config.duckdb_table == "ohlcv_daily"
    assert config.yfinance.max_retries >= 1
    assert config.validation.outlier_return_threshold > 0


def test_load_universe_has_forty_unique_cac40_tickers():
    universe = load_universe(PROJECT_ROOT / "config" / "universe_cac40.yaml")

    assert len(universe) == 40
    tickers = [t.ticker for t in universe]
    assert len(set(tickers)) == 40
    assert all(t.endswith(".PA") for t in tickers)
    assert all(t.isin for t in universe)
