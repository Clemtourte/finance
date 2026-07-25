"""Test d'intégration du CLI (src.engine.cli), sans réseau.

Construit un entrepôt DuckDB temporaire (via `DuckDBLoader` + un cache
Parquet synthétique) et une config de backtest temporaire, pour vérifier
que `main()` s'exécute de bout en bout et produit un CSV de comparaison
exploitable.
"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd
import polars as pl
import pytest

from src.data.cache import ParquetCache
from src.data.duckdb_loader import DuckDBLoader
from src.engine.cli import main


@pytest.fixture
def workspace(tmp_path):
    n = 120
    dates = list(pd.bdate_range("2023-01-01", periods=n).date)
    rng_closes = [100.0 + 0.1 * i for i in range(n)]
    rows = pl.DataFrame(
        {
            "date": dates,
            "open": rng_closes,
            "high": [c + 1 for c in rng_closes],
            "low": [c - 1 for c in rng_closes],
            "close": rng_closes,
            "adj_close": rng_closes,
            "volume": [1_000] * n,
        }
    ).cast({"date": pl.Date, "volume": pl.Int64})

    cache_dir = tmp_path / "cache"
    cache = ParquetCache(cache_dir)
    cache.upsert("AI.PA", "AI.PA", rows)

    db_path = tmp_path / "warehouse.duckdb"
    with DuckDBLoader(db_path, "ohlcv_daily") as loader:
        loader.load_ticker("AI.PA", cache.file_path("AI.PA"))

    universe_path = tmp_path / "universe.yaml"
    universe_path.write_text(
        """
tickers:
  - ticker: AI.PA
    name: Air Liquide
    isin: FR0000120073
    ttf: true
    spread_pct: 0.0003
""",
        encoding="utf-8",
    )

    data_config_path = tmp_path / "data.yaml"
    data_config_path.write_text(
        f"""
universe_file: {universe_path}
period:
  start_date: "2023-01-01"
  end_date: "today"
cache_dir: {cache_dir}
duckdb:
  path: {db_path}
  table: ohlcv_daily
yfinance:
  max_retries: 1
  retry_backoff_seconds: 0.0
  request_pause_seconds: 0.0
validation:
  max_gap_calendar_days: 5
  outlier_return_threshold: 0.30
  split_ratio_tolerance: 0.03
""",
        encoding="utf-8",
    )

    backtest_config_path = tmp_path / "backtest.yaml"
    backtest_config_path.write_text(
        """
initial_capital: 10000.0
trading_days_per_year: 252
risk_free_rate: 0.0
costs:
  brokerage_tiers:
    - max_order_value: 500.0
      fixed_fee: 1.99
    - max_order_value: null
      pct_fee: 0.006
  ttf_pct: 0.004
  base_slippage_pct: 0.0005
""",
        encoding="utf-8",
    )

    strategy_config_path = tmp_path / "sma_crossover.yaml"
    strategy_config_path.write_text("fast_period: 5\nslow_period: 20\n", encoding="utf-8")

    return {
        "data_config": data_config_path,
        "backtest_config": backtest_config_path,
        "strategy_config": strategy_config_path,
        "output_csv": tmp_path / "out.csv",
    }


def test_cli_no_split_runs_end_to_end_and_writes_csv(workspace, monkeypatch, capsys):
    argv = [
        "prog",
        "--ticker",
        "AI.PA",
        "--data-config",
        str(workspace["data_config"]),
        "--backtest-config",
        str(workspace["backtest_config"]),
        "--strategy-config",
        str(workspace["strategy_config"]),
        "--output-csv",
        str(workspace["output_csv"]),
        "--no-split",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    main()

    captured = capsys.readouterr()
    assert "cagr" in captured.out
    assert "Buy & Hold" in captured.out
    assert "AUCUN découpage" in captured.out

    df = pd.read_csv(workspace["output_csv"])
    assert set(df["metric"]) >= {"cagr", "sharpe_ratio", "max_drawdown", "num_trades"}


def test_cli_raises_clear_error_for_unknown_ticker(workspace, monkeypatch):
    argv = [
        "prog",
        "--ticker",
        "UNKNOWN.PA",
        "--data-config",
        str(workspace["data_config"]),
        "--backtest-config",
        str(workspace["backtest_config"]),
        "--strategy-config",
        str(workspace["strategy_config"]),
        "--no-split",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(ValueError, match="Aucune donnée"):
        main()


def test_cli_refuses_to_run_without_split_date_or_no_split(workspace, monkeypatch, capsys):
    argv = [
        "prog",
        "--ticker",
        "AI.PA",
        "--data-config",
        str(workspace["data_config"]),
        "--backtest-config",
        str(workspace["backtest_config"]),
        "--strategy-config",
        str(workspace["strategy_config"]),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit):
        main()

    captured = capsys.readouterr()
    assert "--split-date" in captured.err


def test_cli_with_split_date_prints_is_and_oos_tables_and_writes_two_csvs(
    workspace, monkeypatch, capsys
):
    argv = [
        "prog",
        "--ticker",
        "AI.PA",
        "--data-config",
        str(workspace["data_config"]),
        "--backtest-config",
        str(workspace["backtest_config"]),
        "--strategy-config",
        str(workspace["strategy_config"]),
        "--split-date",
        "2023-06-01",
        "--output-csv",
        str(workspace["output_csv"]),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    main()

    captured = capsys.readouterr()
    assert "In-sample" in captured.out
    assert "Out-of-sample" in captured.out
    assert "AUCUN découpage" not in captured.out

    is_path = workspace["output_csv"].with_name("out_in_sample.csv")
    oos_path = workspace["output_csv"].with_name("out_out_of_sample.csv")
    assert is_path.exists()
    assert oos_path.exists()

    is_df = pd.read_csv(is_path)
    oos_df = pd.read_csv(oos_path)
    assert set(is_df["metric"]) >= {"cagr", "sharpe_ratio"}
    assert set(oos_df["metric"]) >= {"cagr", "sharpe_ratio"}
