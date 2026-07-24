"""Chargement de la configuration YAML du moteur de backtest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from src.engine.costs import BrokerageTier, CostConfig


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration complète du moteur, issue de `config/backtest.yaml`."""

    initial_capital: float
    trading_days_per_year: int
    risk_free_rate: float
    rebalance_freq: str
    costs: CostConfig


def _parse_brokerage_tiers(raw_tiers: list[dict]) -> tuple[BrokerageTier, ...]:
    return tuple(
        BrokerageTier(
            max_order_value=tier["max_order_value"],
            fixed_fee=tier.get("fixed_fee"),
            pct_fee=tier.get("pct_fee"),
        )
        for tier in raw_tiers
    )


def load_backtest_config(path: str | Path) -> BacktestConfig:
    """Charge `config/backtest.yaml`.

    Args:
        path: Chemin du fichier YAML de configuration.

    Returns:
        `BacktestConfig` typée.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw_costs = raw["costs"]
    costs = CostConfig(
        brokerage_tiers=_parse_brokerage_tiers(raw_costs["brokerage_tiers"]),
        ttf_pct=raw_costs["ttf_pct"],
        base_slippage_pct=raw_costs["base_slippage_pct"],
    )
    return BacktestConfig(
        initial_capital=raw["initial_capital"],
        trading_days_per_year=raw["trading_days_per_year"],
        risk_free_rate=raw["risk_free_rate"],
        rebalance_freq=raw.get("rebalance_freq", "daily"),
        costs=costs,
    )
