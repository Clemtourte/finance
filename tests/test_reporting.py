"""Tests du tableau de comparaison (src.reporting.table)."""

from __future__ import annotations

import math

import pandas as pd

from src.engine.batch import BatchResult
from src.metrics.comparison import ComparisonRow
from src.reporting.table import (
    export_batch_csv,
    export_comparison_csv,
    format_batch_table,
    format_comparison_table,
    format_friction_pct,
)


def _sample_rows() -> list[ComparisonRow]:
    return [
        ComparisonRow(metric="cagr", strategy=0.08, buy_and_hold=0.10, delta=-0.02),
        ComparisonRow(metric="sharpe_ratio", strategy=0.9, buy_and_hold=1.1, delta=-0.2),
        ComparisonRow(metric="num_trades", strategy=12, buy_and_hold=0, delta=12),
        ComparisonRow(metric="win_rate", strategy=float("nan"), buy_and_hold=float("nan"), delta=float("nan")),
        ComparisonRow(metric="profit_factor", strategy=float("inf"), buy_and_hold=float("nan"), delta=float("inf")),
        ComparisonRow(metric="turnover", strategy=2.5, buy_and_hold=0.0, delta=2.5),
        ComparisonRow(metric="turnover_annualized", strategy=1.25, buy_and_hold=0.0, delta=1.25),
        ComparisonRow(metric="friction_eur", strategy=123.456, buy_and_hold=1.99, delta=121.466),
        ComparisonRow(
            metric="friction_pct_of_gross_gain", strategy=0.15, buy_and_hold=0.002, delta=0.148
        ),
    ]


def test_format_comparison_table_contains_all_metrics():
    table = format_comparison_table(_sample_rows())
    for row in _sample_rows():
        assert row.metric in table


def test_format_comparison_table_formats_percentages():
    table = format_comparison_table(_sample_rows())
    assert "8.00%" in table
    assert "10.00%" in table
    assert "-2.00%" in table


def test_format_comparison_table_formats_ratios_and_ints():
    table = format_comparison_table(_sample_rows())
    assert "0.90" in table  # sharpe stratégie
    assert "12" in table  # num_trades


def test_format_comparison_table_handles_nan_and_inf():
    table = format_comparison_table(_sample_rows())
    assert "n/a" in table
    assert "inf" in table


def test_format_comparison_table_formats_turnover_and_friction():
    table = format_comparison_table(_sample_rows())
    assert "2.50x" in table  # turnover stratégie
    assert "1.25x" in table  # turnover_annualized stratégie
    assert "123.46€" in table  # friction_eur stratégie
    assert "15.00%" in table  # friction_pct_of_gross_gain stratégie


def test_format_friction_pct_shows_ns_above_threshold():
    assert format_friction_pct(204.0582) == "n/s"  # 20405.82%, gain brut minuscule


def test_format_friction_pct_shows_percentage_at_and_below_threshold():
    assert format_friction_pct(10.0) == "1000.00%"  # pile au seuil : pas encore "n/s"
    assert format_friction_pct(0.15) == "15.00%"


def test_format_friction_pct_shows_na_for_nan():
    assert format_friction_pct(float("nan")) == "n/a"


def test_format_comparison_table_shows_ns_for_absurd_friction_ratio():
    rows = [
        ComparisonRow(metric="friction_pct_of_gross_gain", strategy=204.0582, buy_and_hold=0.01, delta=204.0482),
    ]
    table = format_comparison_table(rows)
    assert "n/s" in table
    assert "20405.82%" not in table


def test_format_comparison_table_has_header_and_separator():
    table = format_comparison_table(_sample_rows())
    lines = table.splitlines()
    assert lines[0].startswith("Métrique")
    assert set(lines[1]) <= {"-", "+"}


def test_export_comparison_csv_roundtrip(tmp_path):
    rows = _sample_rows()
    out_path = tmp_path / "comparison.csv"
    export_comparison_csv(rows, out_path)

    df = pd.read_csv(out_path)
    assert list(df.columns) == ["metric", "strategy", "buy_and_hold", "delta"]
    assert df.loc[df["metric"] == "cagr", "strategy"].item() == 0.08
    assert df.loc[df["metric"] == "num_trades", "strategy"].item() == 12
    assert math.isnan(df.loc[df["metric"] == "win_rate", "strategy"].item())


def _sample_batch_results() -> list[BatchResult]:
    return [
        BatchResult(
            ticker="AI.PA", name="Air Liquide", strategy_cagr_oos=0.05, benchmark_cagr_oos=0.03,
            delta=0.02, friction_pct_oos=0.01, verdict="SURVIT",
        ),
        BatchResult(
            ticker="MC.PA", name="LVMH", strategy_cagr_oos=-0.02, benchmark_cagr_oos=0.08,
            delta=-0.10, friction_pct_oos=0.15, verdict="REJETÉ",
        ),
        BatchResult(
            ticker="XX.PA", name="Inconnu", strategy_cagr_oos=float("nan"),
            benchmark_cagr_oos=float("nan"), delta=float("nan"), friction_pct_oos=float("nan"),
            verdict="ERREUR", error="Aucune donnée pour XX.PA",
        ),
    ]


def test_format_batch_table_contains_all_tickers_and_verdicts():
    table = format_batch_table(_sample_batch_results())
    assert "AI.PA" in table
    assert "SURVIT" in table
    assert "MC.PA" in table
    assert "REJETÉ" in table
    assert "XX.PA" in table
    assert "ERREUR" in table


def test_format_batch_table_formats_percentages():
    table = format_batch_table(_sample_batch_results())
    assert "5.00%" in table  # strategy_cagr_oos de AI.PA
    assert "-10.00%" in table  # delta de MC.PA


def test_format_batch_table_shows_na_for_error_rows():
    table = format_batch_table(_sample_batch_results())
    lines = [line for line in table.splitlines() if "XX.PA" in line]
    assert len(lines) == 1
    assert "n/a" in lines[0]
    assert "nan" not in lines[0]


def test_format_batch_table_shows_ns_for_absurd_friction_ratio():
    results = [
        BatchResult(
            ticker="DG.PA", name="Vinci", strategy_cagr_oos=0.001, benchmark_cagr_oos=0.0005,
            delta=0.0005, friction_pct_oos=204.0582, verdict="SURVIT",
        ),
    ]
    table = format_batch_table(results)
    assert "n/s" in table
    assert "20405.82%" not in table


def test_export_batch_csv_roundtrip(tmp_path):
    results = _sample_batch_results()
    out_path = tmp_path / "batch.csv"
    export_batch_csv(results, out_path)

    df = pd.read_csv(out_path)
    assert list(df.columns) == [
        "ticker", "name", "strategy_cagr_oos", "benchmark_cagr_oos", "delta",
        "friction_pct_oos", "verdict", "error",
    ]
    assert df.loc[df["ticker"] == "AI.PA", "verdict"].item() == "SURVIT"
    assert df.loc[df["ticker"] == "XX.PA", "error"].item() == "Aucune donnée pour XX.PA"
