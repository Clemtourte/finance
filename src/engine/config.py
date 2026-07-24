"""Chargement de la configuration YAML du moteur de backtest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CostConfig:
    """Coûts de transaction, appliqués à l'entrée ET à la sortie de chaque position.

    Attributes:
        brokerage_fee_pct: Frais de courtage, fraction de la valeur de la
            transaction (ex. `0.006` pour 0,60%).
        slippage_pct: Glissement estimé, fraction du prix d'exécution
            (ex. `0.0005` pour 0,05%).
    """

    brokerage_fee_pct: float
    slippage_pct: float


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration complète du moteur, issue de `config/backtest.yaml`."""

    initial_capital: float
    trading_days_per_year: int
    risk_free_rate: float
    costs: CostConfig


def load_backtest_config(path: str | Path) -> BacktestConfig:
    """Charge `config/backtest.yaml`.

    Args:
        path: Chemin du fichier YAML de configuration.

    Returns:
        `BacktestConfig` typée.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return BacktestConfig(
        initial_capital=raw["initial_capital"],
        trading_days_per_year=raw["trading_days_per_year"],
        risk_free_rate=raw["risk_free_rate"],
        costs=CostConfig(**raw["costs"]),
    )
