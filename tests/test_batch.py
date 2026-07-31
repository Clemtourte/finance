"""Tests du runner batch (src.engine.batch), sans réseau.

Construit un entrepôt DuckDB temporaire avec des tickers synthétiques :
un qui s'effondre après la date de split, un qui continue de monter, et
un qui plonge puis se redresse largement au-delà de son niveau de départ.
Une stratégie de test toujours flat (rendement net nul, quel que soit le
prix) donne un verdict REJETÉ déterministe des deux côtés (elle ne bat
jamais le buy & hold qui monte, et quand elle bat le buy & hold qui
s'effondre, elle reste elle-même à rendement nul donc non rentable — le
scénario exact que la règle SURVIT>0 est censée rejeter). Une stratégie
scriptée (positions imposées, pas de vraie logique de signal) qui sort
avant le creux puis rachète dedans donne un verdict SURVIT déterministe,
avec un gain réel. Un ticker absent de l'entrepôt vérifie le verdict
ERREUR.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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


@dataclass
class _ScriptedStrategy(Strategy):
    """Double de test : position imposée bar par bar (pas une vraie
    logique de signal), pour obtenir un scénario de gain réel
    déterministe plutôt que dérivé d'un indicateur."""

    positions: list[int]

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(self.positions[: len(df)], index=df.index, dtype=int)

    @property
    def params(self) -> dict[str, object]:
        return {}


#: Position décidée bar par bar pour RECOVER.PA (vaut pour toute la série
#: de 40 barres, split_index=20) : flat sur l'in-sample, investie de
#: J20 à J22 (vendue au pic J23), flat pendant le creux J23-J25, ré-investie
#: de J26 (près du creux) à la fin (l'exécution est décalée d'un jour par
#: `shift_to_execution`, voir `src/engine/backtest.py`).
_RECOVER_SCRIPTED_POSITIONS = [0] * 19 + [1, 1, 1, 0, 0, 0, 1] + [1] * 13 + [0]


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
    # Plonge puis se redresse largement au-delà de son niveau de départ
    # d'out-of-sample (utilisé avec _RECOVER_SCRIPTED_POSITIONS ci-dessous
    # pour un scénario SURVIT déterministe et réellement rentable).
    recover_closes = [100.0 + i for i in range(split_index)] + [
        100.0, 120.0, 140.0, 160.0, 100.0, 70.0, 80.0, 100.0, 130.0, 160.0,
        190.0, 210.0, 225.0, 235.0, 242.0, 246.0, 248.0, 249.0, 249.5, 250.0,
    ]

    cache_dir = tmp_path / "cache"
    cache = ParquetCache(cache_dir)
    db_path = tmp_path / "warehouse.duckdb"
    with DuckDBLoader(db_path, "ohlcv_daily") as loader:
        _write_ticker(cache, loader, "RISE.PA", rising_closes)
        _write_ticker(cache, loader, "CRASH.PA", crash_closes)
        _write_ticker(cache, loader, "RECOVER.PA", recover_closes)

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
        # Bas exprès (20 barres de chaque côté avec split_index=20) pour ne
        # pas rendre NON TESTABLE les scénarios normaux de ce fichier ; les
        # tests dédiés au garde-fou ci-dessous ajustent ce seuil eux-mêmes.
        min_bars_per_period=10,
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
        "dates": pd.bdate_range("2024-01-01", periods=n).date,
    }


def test_run_batch_flat_strategy_beating_crashing_benchmark_is_rejected_not_survit(workspace):
    # La stratégie flat "bat" le buy & hold qui s'effondre (0% > CAGR
    # négatif) mais ne gagne elle-même rien : ce n'est pas SURVIT.
    universe = [TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000")]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"],
        workspace["split_date"],
    )
    assert len(results) == 1
    result = results[0]
    assert result.verdict == "REJETÉ"
    assert result.strategy_cagr_oos == pytest.approx(0.0, abs=1e-9)
    assert result.benchmark_cagr_oos < 0
    assert result.error == (
        f"Bat le buy & hold ({result.benchmark_cagr_oos:.1%}/an) mais perd de "
        f"l'argent ({result.strategy_cagr_oos:.1%}/an)"
    )


def test_run_batch_strategy_cagr_of_exactly_zero_is_rejected_not_survit(workspace):
    # Test dédié au seuil lui-même : la condition SURVIT est
    # strategy_cagr_oos > 0, STRICTEMENT — 0% exact (ni gain ni perte)
    # doit donc être REJETÉ, jamais SURVIT, même quand delta > 0.
    universe = [TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000")]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"],
        workspace["split_date"],
    )
    result = results[0]
    assert result.strategy_cagr_oos == pytest.approx(0.0, abs=1e-9)
    assert result.delta > 0  # bat bien le buy & hold
    assert result.verdict == "REJETÉ"  # ... mais 0% n'est pas strictement > 0


def test_run_batch_survit_requires_a_genuine_gain_not_just_beating_the_benchmark(workspace):
    # Stratégie scriptée : sort avant le creux de RECOVER.PA, rachète
    # dedans, termine avec un gain réel plus élevé que le buy & hold.
    universe = [TickerInfo(ticker="RECOVER.PA", name="Recover", isin="XX0000000003")]
    strategy = _ScriptedStrategy(positions=_RECOVER_SCRIPTED_POSITIONS)
    results = run_batch(
        universe, strategy, workspace["data_config"], workspace["backtest_config"], workspace["split_date"],
    )
    assert len(results) == 1
    result = results[0]
    assert result.verdict == "SURVIT"
    assert result.strategy_cagr_oos > 0
    assert result.strategy_cagr_oos > result.benchmark_cagr_oos
    assert result.error is None


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
    assert by_ticker["CRASH.PA"].verdict == "REJETÉ"  # flat : bat CRASH.PA sans gagner d'argent
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


def test_run_batch_empty_in_sample_gives_non_testable_never_survit(workspace):
    # split_date = première date de la série -> in-sample vide (0 barre).
    split_date = workspace["dates"][0]
    universe = [TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000")]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"], split_date,
    )
    assert len(results) == 1
    assert results[0].verdict == "NON TESTABLE"
    assert results[0].verdict != "SURVIT"


def test_run_batch_empty_out_of_sample_gives_non_testable(workspace):
    # split_date après la dernière date de la série -> out-of-sample vide.
    split_date = pd.bdate_range("2024-01-01", periods=41).date[40]
    universe = [TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000")]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"], split_date,
    )
    assert len(results) == 1
    assert results[0].verdict == "NON TESTABLE"


def test_run_batch_both_periods_just_above_minimum_renders_normal_verdict(workspace):
    # split_index=20 -> 20 barres de chaque côté ; min_bars_per_period=19 :
    # les deux côtés sont juste au-dessus du minimum, verdict normal attendu
    # (pas NON TESTABLE) — REJETÉ ici puisque la stratégie flat ne gagne
    # jamais d'argent.
    backtest_config = replace(workspace["backtest_config"], min_bars_per_period=19)
    universe = [TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000")]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], backtest_config, workspace["split_date"],
    )
    assert len(results) == 1
    assert results[0].verdict == "REJETÉ"


def test_run_batch_non_testable_ticker_excluded_from_survit_and_rejete_counts(workspace):
    split_date = workspace["dates"][0]  # in-sample vide pour les deux tickers
    universe = [
        TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000"),
        TickerInfo(ticker="RISE.PA", name="Rise", isin="XX0000000001"),
    ]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"], split_date,
    )
    assert all(r.verdict == "NON TESTABLE" for r in results)
    n_survives = sum(1 for r in results if r.verdict == "SURVIT")
    n_rejected = sum(1 for r in results if r.verdict == "REJETÉ")
    assert n_survives == 0
    assert n_rejected == 0


def test_run_batch_non_testable_reason_mentions_insufficient_side_and_counts(workspace):
    split_date = workspace["dates"][0]  # in-sample vide (0 barre), min = 10
    universe = [TickerInfo(ticker="CRASH.PA", name="Crash", isin="XX0000000000")]
    results = run_batch(
        universe, _AlwaysFlatStrategy(), workspace["data_config"], workspace["backtest_config"], split_date,
    )
    assert results[0].verdict == "NON TESTABLE"
    assert results[0].error is not None
    assert "in-sample" in results[0].error
    assert "0" in results[0].error
    assert "10" in results[0].error
