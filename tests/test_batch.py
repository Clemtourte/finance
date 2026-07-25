"""Tests du runner batch (src.engine.batch), sans réseau.

Construit un entrepôt DuckDB temporaire avec deux tickers synthétiques
(un qui s'effondre après la date de split, un qui continue de monter) et
une stratégie de test toujours flat (rendement net nul, quel que soit le
prix) pour obtenir un verdict SURVIT et un verdict REJETÉ de façon
déterministe, plus un ticker absent de l'entrepôt pour vérifier le
verdict ERREUR.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import polars as pl
import pytest

from src.data.cache import ParquetCache
from src.data.config import DataConfig, TickerInfo, ValidationConfig, YFinanceConfig
from src.data.duckdb_loader import DuckDBLoader
from src.engine.batch import run_batch
from src.engine.config import BacktestConfig
from src.engine.costs import BrokerageTier, CostConfig
from src.strategies.base import Strategy


@dataclass
class _AlwaysFlatStrategy(Strategy):
    """Double de test : jamais investie, rendement net toujours nul."""

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(0, index=df.index, dtype=int)

    @property
    def params(self) -> dict[str, object]:
        return {}


def _write_ticker(cache: ParquetCache, loader: DuckDBLoader, ticker: str, closes: list[float]) -> None:
    n = len(closes)
    dates = pd.bdate_range("2024-01-01", periods=n).date
    rows = pl.DataFrame(
        {
            "date": list(dates),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "adj_close": closes,
            "volume": [1_000] * n,
        }
    ).cast({"date": pl.Date, "volume": pl.Int64})
    cache.upsert(ticker, ticker, rows)
    loader.load_ticker(ticker, cache.file_path(ticker))


@pytest.fixture
def workspace(tmp_path):
    n = 40
    split_index = 20  # date de coupure = milieu de la série

    rising_closes = [100.0 + i for i in range(n)]  # monte tout du long
    crash_closes = [100.0 + i for i in range(split_index)] + [
        120.0 - 5 * (i - split_index) for i in range(split_index, n)
    ]  # monte puis s'effondre après la coupure

    cache_dir = tmp_path / "cache"
    cache = ParquetCache(cache_dir)
    db_path = tmp_path / "warehouse.duckdb"
    with DuckDBLoader(db_path, "ohlcv_daily") as loader:
        _write_ticker(cache, loader, "RISE.PA", rising_closes)
        _write_ticker(cache, loader, "CRASH.PA", crash_closes)

    split_date = pd.bdate_range("2024-01-01", periods=n).date[split_index]

    data_config = DataConfig(
        universe_file=tmp_path / "unused.yaml",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        cache_dir=cache_dir,
        duckdb_path=db_path,
        duckdb_table="ohlcv_daily",
        yfinance=YFinanceConfig(max_retries=1, retry_backoff_seconds=0.0, request_pause_seconds=0.0),
        validation=ValidationConfig(max_gap_calendar_days=5, outlier_return_threshold=0.30, split_ratio_tolerance=0.03),
    )
    backtest_config = BacktestConfig(
        initial_capital=10_000.0,
        trading_days_per_year=252,
        risk_free_rate=0.0,
        rebalance_freq="daily",
        costs=CostConfig(
            brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=0.006),),
            ttf_pct=0.0,
            base_slippage_pct=0.0005,
        ),
    )

    return {
        "data_config": data_config,
        "backtest_config": backtest_config,
        "split_date": split_date,
    }


def test_run_batch_produces_survit_for_flat_strategy_against_crashing_benchmark(workspace):
    universe = [TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000")]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"],
        workspace["split_date"],
    )
    assert len(results) == 1
    assert results[0].verdict == "SURVIT"
    assert results[0].strategy_cagr_oos == pytest.approx(0.0, abs=1e-9)
    assert results[0].benchmark_cagr_oos < 0


def test_run_batch_produces_rejete_for_flat_strategy_against_rising_benchmark(workspace):
    universe = [TickerInfo(ticker="RISE.PA", name="Rise", isin="XX0000000001")]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"],
        workspace["split_date"],
    )
    assert len(results) == 1
    assert results[0].verdict == "REJETÉ"
    assert results[0].benchmark_cagr_oos > 0


def test_run_batch_reports_erreur_for_missing_ticker_without_crashing(workspace):
    universe = [
        TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000"),
        TickerInfo(ticker="MISSING.PA", name="Absent", isin="XX0000000002"),
    ]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"],
        workspace["split_date"],
    )
    assert len(results) == 2
    by_ticker = {r.ticker: r for r in results}
    assert by_ticker["CRASH.PA"].verdict == "SURVIT"
    assert by_ticker["MISSING.PA"].verdict == "ERREUR"
    assert by_ticker["MISSING.PA"].error is not None


def test_run_batch_preserves_universe_order(workspace):
    universe = [
        TickerInfo(ticker="RISE.PA", name="Rise", isin="XX0000000001"),
        TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000"),
    ]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"],
        workspace["split_date"],
    )
    assert [r.ticker for r in results] == ["RISE.PA", "CRASH.PA"]
