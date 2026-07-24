"""Tableau de comparaison stratégie vs buy & hold (console + CSV)."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from src.metrics.comparison import ComparisonRow

#: Métriques affichées en pourcentage (ex. `12.34%`).
_PERCENT_METRICS = frozenset({"cagr", "annualized_volatility", "max_drawdown", "win_rate"})
#: Métriques affichées comme un ratio à 2 décimales.
_RATIO_METRICS = frozenset({"sharpe_ratio", "sortino_ratio", "profit_factor"})
#: Métriques entières.
_INT_METRICS = frozenset({"num_trades", "max_drawdown_duration_days"})

_HEADERS = ("Métrique", "Stratégie", "Buy & Hold", "Écart")


def _format_value(metric: str, value: float) -> str:
    """Formate une valeur de métrique pour l'affichage, selon son type."""
    if isinstance(value, float) and math.isnan(value):
        return "n/a"
    if metric in _PERCENT_METRICS:
        return f"{value:.2%}"
    if metric == "turnover":
        return f"{value:.2f}x"
    if metric in _RATIO_METRICS:
        return f"{value:.2f}"
    if metric in _INT_METRICS:
        return f"{int(value)}"
    return f"{value:.4f}"


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
    all_rows = [_HEADERS, *formatted_rows]
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(_HEADERS))]

    def _render(cells: tuple[str, ...]) -> str:
        aligned = [cells[0].ljust(widths[0])] + [
            cell.rjust(widths[i]) for i, cell in enumerate(cells[1:], start=1)
        ]
        return " | ".join(aligned)

    lines = [_render(_HEADERS), "-+-".join("-" * w for w in widths)]
    lines.extend(_render(row) for row in formatted_rows)
    return "\n".join(lines)


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
