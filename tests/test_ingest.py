"""Tests de l'orchestration d'ingestion (src.data.ingest), sans réseau.

Un `FakeProvider` en mémoire remplace `YFinanceProvider` pour vérifier :
- que seul le delta manquant est demandé au provider (pas de re-téléchargement) ;
- que le pipeline complet (provider -> cache -> DuckDB) fonctionne de bout en bout.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date

import duckdb
import pandas as pd
import pytest

from src.data.cache import ParquetCache
from src.data.config import DataConfig, TickerInfo, ValidationConfig, YFinanceConfig
from src.data.ingest import main, run_ingestion, sync_ticker
from src.data.provider import DataProvider, empty_ohlcv_frame


@dataclass
class FakeProvider(DataProvider):
    """Provider en mémoire : sert des prix synthétiques et journalise les appels."""

    calls: list[tuple[str, date, date]] = field(default_factory=list)

    def get_ohlcv(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append((ticker, start, end))
        if start > end:
            return empty_ohlcv_frame()
        dates = list(pd.bdate_range(start=start, end=end).date)
        if not dates:
            return empty_ohlcv_frame()
        closes = [100.0 + i for i in range(len(dates))]
        return pd.DataFrame(
            {
                "date": dates,
                "open": closes,
                "high": [c + 1 for c in closes],
                "low": [c - 1 for c in closes],
                "close": closes,
                "adj_close": closes,
                "volume": [1_000] * len(dates),
            }
        )


def test_sync_ticker_fetches_full_range_on_first_call(tmp_path):
    cache = ParquetCache(tmp_path)
    provider = FakeProvider()

    df = sync_ticker(provider, cache, "AI.PA", date(2024, 1, 1), date(2024, 1, 10))

    assert len(provider.calls) == 1
    assert not df.is_empty()


def test_sync_ticker_only_refetches_delta_on_second_call(tmp_path):
    cache = ParquetCache(tmp_path)
    provider = FakeProvider()

    sync_ticker(provider, cache, "AI.PA", date(2024, 1, 1), date(2024, 1, 10))
    provider.calls.clear()

    sync_ticker(provider, cache, "AI.PA", date(2024, 1, 1), date(2024, 1, 20))

    assert len(provider.calls) == 1
    _, requested_start, requested_end = provider.calls[0]
    assert requested_start == date(2024, 1, 11)
    assert requested_end == date(2024, 1, 20)


def test_sync_ticker_noop_when_already_fully_cached(tmp_path):
    cache = ParquetCache(tmp_path)
    provider = FakeProvider()

    sync_ticker(provider, cache, "AI.PA", date(2024, 1, 1), date(2024, 1, 10))
    provider.calls.clear()

    sync_ticker(provider, cache, "AI.PA", date(2024, 1, 2), date(2024, 1, 5))

    assert provider.calls == []


def test_sync_ticker_does_not_refetch_range_before_cached_start_by_default(tmp_path):
    cache = ParquetCache(tmp_path)
    provider = FakeProvider()

    sync_ticker(provider, cache, "AI.PA", date(2024, 1, 6), date(2024, 1, 10))
    provider.calls.clear()

    sync_ticker(provider, cache, "AI.PA", date(2024, 1, 1), date(2024, 1, 10))

    assert provider.calls == []


@pytest.fixture
def data_config(tmp_path) -> DataConfig:
    return DataConfig(
        universe_file=tmp_path / "unused.yaml",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 10),
        cache_dir=tmp_path / "cache",
        duckdb_path=tmp_path / "warehouse.duckdb",
        duckdb_table="ohlcv_daily",
        yfinance=YFinanceConfig(max_retries=1, retry_backoff_seconds=0.0, request_pause_seconds=0.0),
        validation=ValidationConfig(
            max_gap_calendar_days=5, outlier_return_threshold=0.30, split_ratio_tolerance=0.03
        ),
    )


def test_run_ingestion_loads_all_tickers_into_duckdb(data_config):
    universe = [
        TickerInfo(ticker="AI.PA", name="Air Liquide", isin="FR0000120073"),
        TickerInfo(ticker="MC.PA", name="LVMH", isin="FR0000121014"),
    ]
    provider = FakeProvider()

    reports = run_ingestion(data_config, universe, provider=provider)

    assert set(reports) == {"AI.PA", "MC.PA"}
    assert not any(r.has_issues for r in reports.values())

    with duckdb.connect(str(data_config.duckdb_path)) as conn:
        (count,) = conn.execute(
            f"SELECT count(*) FROM {data_config.duckdb_table} WHERE ticker = 'AI.PA'"
        ).fetchone()
        (n_tickers,) = conn.execute(
            f"SELECT count(DISTINCT ticker) FROM {data_config.duckdb_table}"
        ).fetchone()

    assert count > 0
    assert n_tickers == 2


def test_run_ingestion_second_run_does_not_duplicate_rows(data_config):
    universe = [TickerInfo(ticker="AI.PA", name="Air Liquide", isin="FR0000120073")]
    provider = FakeProvider()

    run_ingestion(data_config, universe, provider=provider)
    run_ingestion(data_config, universe, provider=provider)

    with duckdb.connect(str(data_config.duckdb_path)) as conn:
        (count,) = conn.execute(
            f"SELECT count(*) FROM {data_config.duckdb_table} WHERE ticker = 'AI.PA'"
        ).fetchone()

    expected_days = len(pd.bdate_range(start=date(2024, 1, 1), end=date(2024, 1, 10)))
    assert count == expected_days


class _FakeYFinanceProvider(FakeProvider):
    """Double de `YFinanceProvider` acceptant les mêmes kwargs de résilience."""

    def __init__(
        self,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        request_pause_seconds: float = 0.3,
    ) -> None:
        super().__init__()


@pytest.fixture
def ingest_cli_workspace(tmp_path):
    default_universe_path = tmp_path / "universe_default.yaml"
    default_universe_path.write_text(
        """
tickers:
  - ticker: MC.PA
    name: LVMH
    isin: FR0000121014
""",
        encoding="utf-8",
    )

    override_universe_path = tmp_path / "universe_override.yaml"
    override_universe_path.write_text(
        """
tickers:
  - ticker: AI.PA
    name: Air Liquide
    isin: FR0000120073
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "warehouse.duckdb"
    data_config_path = tmp_path / "data.yaml"
    data_config_path.write_text(
        f"""
universe_file: {default_universe_path}
period:
  start_date: "2024-01-01"
  end_date: "2024-01-10"
cache_dir: {tmp_path / "cache"}
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

    return {
        "data_config": data_config_path,
        "override_universe": override_universe_path,
        "db_path": db_path,
    }


def _tickers_in_duckdb(db_path) -> set[str]:
    with duckdb.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT DISTINCT ticker FROM ohlcv_daily").fetchall()}


def test_main_universe_file_argument_overrides_config_default(ingest_cli_workspace, monkeypatch):
    monkeypatch.setattr("src.data.ingest.YFinanceProvider", _FakeYFinanceProvider)
    argv = [
        "prog",
        "--config",
        str(ingest_cli_workspace["data_config"]),
        "--universe-file",
        str(ingest_cli_workspace["override_universe"]),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    main()

    assert _tickers_in_duckdb(ingest_cli_workspace["db_path"]) == {"AI.PA"}


def test_main_without_universe_file_argument_uses_config_default(ingest_cli_workspace, monkeypatch):
    monkeypatch.setattr("src.data.ingest.YFinanceProvider", _FakeYFinanceProvider)
    argv = [
        "prog",
        "--config",
        str(ingest_cli_workspace["data_config"]),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    main()

    assert _tickers_in_duckdb(ingest_cli_workspace["db_path"]) == {"MC.PA"}
