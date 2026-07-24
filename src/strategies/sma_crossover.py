"""Stratégie de référence : croisement de deux moyennes mobiles simples.

Sert de cas de test pour le moteur de backtest, pas de stratégie destinée
à être jugée rentable en soi.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from src.indicators.trend import sma
from src.strategies.base import Strategy


@dataclass
class SmaCrossoverStrategy(Strategy):
    """Long quand la SMA rapide est au-dessus de la SMA lente, flat sinon.

    Long-only (positions dans `{0, 1}`), cohérent avec un compte PEA qui
    interdit la vente à découvert.

    Attributes:
        fast_period: Fenêtre de la moyenne mobile rapide.
        slow_period: Fenêtre de la moyenne mobile lente (doit être > `fast_period`).
    """

    fast_period: int
    slow_period: int

    def __post_init__(self) -> None:
        if self.fast_period <= 0 or self.slow_period <= 0:
            raise ValueError("fast_period et slow_period doivent être strictement positifs")
        if self.fast_period >= self.slow_period:
            raise ValueError(
                f"fast_period ({self.fast_period}) doit être < slow_period ({self.slow_period})"
            )

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Voir `Strategy.generate_signals`.

        Position cible à J = 1 si `SMA(fast)[J] > SMA(slow)[J]`, sinon 0.
        Ces deux SMA n'utilisant que des données jusqu'à J inclus, la
        position à J respecte la convention anti-look-ahead.
        """
        fast_sma = sma(df["close"], length=self.fast_period)
        slow_sma = sma(df["close"], length=self.slow_period)

        position = (fast_sma > slow_sma).astype(int)
        position[fast_sma.isna() | slow_sma.isna()] = 0
        return position.rename("position")

    @property
    def params(self) -> dict[str, object]:
        return {"fast_period": self.fast_period, "slow_period": self.slow_period}


def load_sma_crossover_strategy(path: str | Path) -> SmaCrossoverStrategy:
    """Construit une `SmaCrossoverStrategy` depuis un fichier YAML.

    Args:
        path: Chemin du fichier YAML (ex. `config/strategies/sma_crossover.yaml`),
            attendu avec les clés `fast_period` et `slow_period`.

    Returns:
        `SmaCrossoverStrategy` initialisée avec les paramètres du fichier.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return SmaCrossoverStrategy(fast_period=raw["fast_period"], slow_period=raw["slow_period"])
