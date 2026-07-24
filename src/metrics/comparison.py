"""Comparaison stratégie vs buy & hold, métrique par métrique.

Objectif du projet : ne jamais présenter un résultat de stratégie sans son
buy & hold de référence (voir README). Ce module matérialise cette règle :
`compare` prend systématiquement les deux `MetricsResult` en entrée.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from src.metrics.performance import MetricsResult


@dataclass(frozen=True)
class ComparisonRow:
    """Une ligne du tableau de comparaison, pour une métrique donnée."""

    metric: str
    strategy: float
    buy_and_hold: float
    delta: float


def compare(strategy: MetricsResult, buy_and_hold: MetricsResult) -> list[ComparisonRow]:
    """Compare deux `MetricsResult`, métrique par métrique.

    Args:
        strategy: Métriques de la stratégie évaluée.
        buy_and_hold: Métriques du buy & hold de référence, calculé sur le
            même actif, la même période et les mêmes coûts.

    Returns:
        Liste de `ComparisonRow`, une par champ de `MetricsResult`, dans
        l'ordre de déclaration des champs. `delta = strategy - buy_and_hold`.
    """
    rows = []
    for field in dataclasses.fields(strategy):
        strategy_value = getattr(strategy, field.name)
        benchmark_value = getattr(buy_and_hold, field.name)
        rows.append(
            ComparisonRow(
                metric=field.name,
                strategy=strategy_value,
                buy_and_hold=benchmark_value,
                delta=strategy_value - benchmark_value,
            )
        )
    return rows
