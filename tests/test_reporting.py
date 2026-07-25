"""Tests du tableau de comparaison (src.reporting.table)."""

from __future__ import annotations

import math

import pandas as pd

from src.metrics.comparison import ComparisonRow
from src.reporting.table import export_comparison_csv, format_comparison_table


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
