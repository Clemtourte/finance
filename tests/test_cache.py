"""Tests de la logique de cache Parquet (src.data.cache)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from src.data.cache import DateRange, ParquetCache
from src.data.schema import OHLCV_SCHEMA


def _rows(dates: list[date], base_price: float = 100.0) -> pl.DataFrame:
    """Construit un DataFrame OHLCV synthétique (sans colonne `ticker`)."""
    n = len(dates)
    closes = [base_price + i for i in range(n)]
    return pl.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "adj_close": closes,
            "volume": [1_000 + i for i in range(n)],
        }
    ).cast({"date": pl.Date, "volume": pl.Int64})


def _dates(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_read_missing_ticker_returns_empty_with_schema(tmp_path):
    cache = ParquetCache(tmp_path)
    df = cache.read("AI.PA")
    assert df.is_empty()
    assert df.schema == OHLCV_SCHEMA


def test_missing_ranges_full_range_when_cache_empty(tmp_path):
    cache = ParquetCache(tmp_path)
    ranges = cache.missing_ranges("AI.PA", date(2024, 1, 1), date(2024, 1, 10))
    assert ranges == [DateRange(date(2024, 1, 1), date(2024, 1, 10))]


def test_missing_ranges_empty_when_start_after_end(tmp_path):
    cache = ParquetCache(tmp_path)
    assert cache.missing_ranges("AI.PA", date(2024, 1, 10), date(2024, 1, 1)) == []


def test_upsert_then_read_roundtrip(tmp_path):
    cache = ParquetCache(tmp_path)
    rows = _rows(_dates(date(2024, 1, 1), 5))
    cache.upsert("AI.PA", "AI.PA", rows)

    stored = cache.read("AI.PA")
    assert len(stored) == 5
    assert stored["ticker"].unique().to_list() == ["AI.PA"]
    assert stored["date"].min() == date(2024, 1, 1)
    assert stored["date"].max() == date(2024, 1, 5)


def test_missing_ranges_only_covers_delta_after_cached_max(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 1), 5)))

    ranges = cache.missing_ranges("AI.PA", date(2024, 1, 1), date(2024, 1, 10))
    assert ranges == [DateRange(date(2024, 1, 6), date(2024, 1, 10))]


def test_missing_ranges_covers_delta_before_and_after_cached_range(tmp_path):
    cache = ParquetCache(tmp_path)
    # Cache couvre Jan 6 -> Jan 10.
    cache.upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 6), 5)))

    # Par défaut (backfill=False), la plage antérieure à cached.start n'est
    # jamais renvoyée : seule la plage postérieure est manquante.
    ranges = cache.missing_ranges("AI.PA", date(2024, 1, 1), date(2024, 1, 12))
    assert ranges == [DateRange(date(2024, 1, 11), date(2024, 1, 12))]


def test_missing_ranges_with_backfill_also_covers_delta_before_cached_range(tmp_path):
    cache = ParquetCache(tmp_path)
    # Cache couvre Jan 6 -> Jan 10.
    cache.upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 6), 5)))

    ranges = cache.missing_ranges("AI.PA", date(2024, 1, 1), date(2024, 1, 12), backfill=True)
    assert ranges == [
        DateRange(date(2024, 1, 1), date(2024, 1, 5)),
        DateRange(date(2024, 1, 11), date(2024, 1, 12)),
    ]


def test_missing_ranges_empty_when_fully_covered(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 1), 10)))

    assert cache.missing_ranges("AI.PA", date(2024, 1, 2), date(2024, 1, 5)) == []


def test_upsert_deduplicates_overlapping_dates_keeping_latest(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 1), 5), base_price=100.0))
    # Ré-écriture avec des valeurs différentes sur une plage chevauchante.
    cache.upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 4), 3), base_price=999.0))

    stored = cache.read("AI.PA").sort("date")
    assert len(stored) == 6  # jours 1-3 + jours 4-6 (4 et 5 remplacés, 6 ajouté)
    row_4 = stored.filter(pl.col("date") == date(2024, 1, 4))
    assert row_4["close"].item() == 999.0


def test_upsert_with_empty_rows_is_noop(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 1), 3)))

    empty = pl.DataFrame(schema={k: v for k, v in OHLCV_SCHEMA.items() if k != "ticker"})
    result = cache.upsert("AI.PA", "AI.PA", empty)
    assert len(result) == 3


def test_cache_persists_across_instances(tmp_path):
    ParquetCache(tmp_path).upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 1), 4)))

    reopened = ParquetCache(tmp_path)
    assert len(reopened.read("AI.PA")) == 4


def test_cached_range_none_when_empty(tmp_path):
    cache = ParquetCache(tmp_path)
    assert cache.cached_range("AI.PA") is None


def test_different_tickers_are_isolated(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.upsert("AI.PA", "AI.PA", _rows(_dates(date(2024, 1, 1), 3)))
    cache.upsert("MC.PA", "MC.PA", _rows(_dates(date(2024, 1, 1), 7)))

    assert len(cache.read("AI.PA")) == 3
    assert len(cache.read("MC.PA")) == 7
