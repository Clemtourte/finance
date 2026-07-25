"""Tableaux de reporting (console + CSV) : comparaison stratégie vs buy &
hold, et récapitulatif de backtest sur un univers entier."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from src.metrics.comparison import ComparisonRow

if TYPE_CHECKING:
    # Import différé (type-checking uniquement) pour éviter un cycle avec
    # src.engine.batch, qui importe ce module pour formater sa sortie.
    from src.engine.batch import BatchResult

#: Métriques affichées en pourcentage (ex. `12.34%`).
_PERCENT_METRICS = frozenset(
    {"cagr", "annualized_volatility", "max_drawdown", "win_rate", "friction_pct_of_gross_gain"}
)
#: Métriques affichées comme un ratio à 2 décimales.
_RATIO_METRICS = frozenset({"sharpe_ratio", "sortino_ratio", "profit_factor"})
#: Métriques entières.
_INT_METRICS = frozenset({"num_trades", "max_drawdown_duration_bars"})
#: Métriques de turnover, affichées comme un multiple ("x").
_TURNOVER_METRICS = frozenset({"turnover", "turnover_annualized"})
#: Métriques monétaires, affichées en euros.
_EUR_METRICS = frozenset({"friction_eur"})

_HEADERS = ("Métrique", "Stratégie", "Buy & Hold", "Écart")


def _format_value(metric: str, value: float) -> str:
    """Formate une valeur de métrique pour l'affichage, selon son type."""
    if isinstance(value, float) and math.isnan(value):
        return "n/a"
    if metric in _PERCENT_METRICS:
        return f"{value:.2%}"
    if metric in _TURNOVER_METRICS:
        return f"{value:.2f}x"
    if metric in _EUR_METRICS:
        return f"{value:,.2f}€"
    if metric in _RATIO_METRICS:
        return f"{value:.2f}"
    if metric in _INT_METRICS:
        return f"{int(value)}"
    return f"{value:.4f}"


def _render_table(headers: tuple[str, ...], formatted_rows: list[tuple[str, ...]]) -> str:
    """Rend un tableau texte aligné (1re colonne à gauche, le reste à droite)."""
    all_rows = [headers, *formatted_rows]
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]

    def _render_row(cells: tuple[str, ...]) -> str:
        aligned = [cells[0].ljust(widths[0])] + [
            cell.rjust(widths[i]) for i, cell in enumerate(cells[1:], start=1)
        ]
        return " | ".join(aligned)

    lines = [_render_row(headers), "-+-".join("-" * w for w in widths)]
    lines.extend(_render_row(row) for row in formatted_rows)
    return "\n".join(lines)


def format_comparison_table(rows: list[ComparisonRow]) -> str:
    """Formate un tableau texte aligné, une ligne par métrique.

    Args:
        rows: Sortie de `src.metrics.comparison.compare`.

    Returns:
        Tableau multi-lignes, prêt à être affiché en console.
    """
    formatted_rows = [
        (
            row.metric,
            _format_value(row.metric, row.strategy),
            _format_value(row.metric, row.buy_and_hold),
            _format_value(row.metric, row.delta),
        )
        for row in rows
    ]
    return _render_table(_HEADERS, formatted_rows)


_BATCH_HEADERS = ("Ticker", "Nom", "CAGR strat. (OOS)", "CAGR B&H (OOS)", "Écart", "Friction %", "Verdict")


def format_batch_table(results: list["BatchResult"]) -> str:
    """Formate le récapitulatif d'un backtest sur un univers entier.

    Args:
        results: Sortie de `src.engine.batch.run_batch`, une ligne par ticker.

    Returns:
        Tableau multi-lignes, prêt à être affiché en console.
    """

    def _pct_or_error(value: float, verdict: str) -> str:
        if verdict == "ERREUR":
            return "n/a"
        return "n/a" if math.isnan(value) else f"{value:.2%}"

    formatted_rows = [
        (
            r.ticker,
            r.name,
            _pct_or_error(r.strategy_cagr_oos, r.verdict),
            _pct_or_error(r.benchmark_cagr_oos, r.verdict),
            _pct_or_error(r.delta, r.verdict),
            _pct_or_error(r.friction_pct_oos, r.verdict),
            r.verdict,
        )
        for r in results
    ]
    return _render_table(_BATCH_HEADERS, formatted_rows)


def export_batch_csv(results: list["BatchResult"], path: str | Path) -> None:
    """Exporte le récapitulatif de backtest sur univers en CSV (valeurs brutes).

    Args:
        results: Sortie de `src.engine.batch.run_batch`.
        path: Chemin du fichier CSV de sortie.
    """
    df = pd.DataFrame(
        [
            {
                "ticker": r.ticker,
                "name": r.name,
                "strategy_cagr_oos": r.strategy_cagr_oos,
                "benchmark_cagr_oos": r.benchmark_cagr_oos,
                "delta": r.delta,
                "friction_pct_oos": r.friction_pct_oos,
                "verdict": r.verdict,
                "error": r.error,
            }
            for r in results
        ]
    )
    df.to_csv(path, index=False)


def export_comparison_csv(rows: list[ComparisonRow], path: str | Path) -> None:
    """Exporte le tableau de comparaison en CSV (valeurs brutes, non formatées).

    Args:
        rows: Sortie de `src.metrics.comparison.compare`.
        path: Chemin du fichier CSV de sortie.
    """
    df = pd.DataFrame(
        [
            {
                "metric": row.metric,
                "strategy": row.strategy,
                "buy_and_hold": row.buy_and_hold,
                "delta": row.delta,
            }
            for row in rows
        ]
    )
    df.to_csv(path, index=False)
