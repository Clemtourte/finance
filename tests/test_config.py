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
    # Champs propres au moteur de coûts, chargés avec la couche données.
    assert all(isinstance(t.ttf, bool) for t in universe)
    assert all(t.spread_pct >= 0 for t in universe)
    assert any(t.ttf for t in universe)  # au moins un titre français éligible TTF
    assert any(not t.ttf for t in universe)  # au moins un titre non-FR (Airbus, ArcelorMittal, ...)


def test_load_universe_etf_pea():
    universe = load_universe(PROJECT_ROOT / "config" / "universe_etf_pea.yaml")

    assert len(universe) == 4
    tickers = {t.ticker for t in universe}
    assert tickers == {"CW8.PA", "EWLD.PA", "PSP5.PA", "ETZ.PA"}
    assert all(t.endswith(".PA") for t in tickers)
    assert all(not t.ttf for t in universe)  # les ETF ne sont jamais soumis à la TTF
    assert all(t.spread_pct > 0 for t in universe)


def test_ticker_info_ttf_and_spread_default_when_absent():
    from src.data.config import TickerInfo

    minimal = TickerInfo(ticker="AI.PA", name="Air Liquide", isin="FR0000120073")
    assert minimal.ttf is False
    assert minimal.spread_pct == 0.0
