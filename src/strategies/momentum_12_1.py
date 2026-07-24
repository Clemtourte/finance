"""Stratégie momentum 12-1 (mono-actif) : long si le rendement des 12
derniers mois hors dernier mois est positif, flat sinon.

Formation classique (Jegadeesh & Titman) : la fenêtre de calcul exclut le
mois le plus récent pour éviter l'effet de réversion à court terme.
Approximation en jours de bourse : 12 mois ~ 252 séances, 1 mois ~ 21
séances.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from src.strategies.base import Strategy


@dataclass
class Momentum12_1Strategy(Strategy):
    """Long si `close[J-skip] / close[J-lookback] - 1 > 0`, flat sinon.

    Long-only (positions dans `{0, 1}`). Version mono-actif : un seul
    titre à la fois (comparaison à sa propre trajectoire passée, pas de
    classement relatif contre un univers).

    Attributes:
        lookback_days: Fenêtre totale de formation, en séances (défaut
            252, ~12 mois).
        skip_days: Nombre de séances les plus récentes exclues de la
            fenêtre de formation (défaut 21, ~1 mois).
    """

    lookback_days: int = 252
    skip_days: int = 21

    def __post_init__(self) -> None:
        if self.lookback_days <= 0 or self.skip_days < 0:
            raise ValueError("lookback_days doit être > 0 et skip_days doit être >= 0")
        if self.skip_days >= self.lookback_days:
            raise ValueError(
                f"skip_days ({self.skip_days}) doit être < lookback_days ({self.lookback_days})"
            )

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Voir `Strategy.generate_signals`.

        Le rendement à J n'utilise que `close[J-skip_days]` et
        `close[J-lookback_days]`, deux observations strictement
        antérieures ou égales à J (jamais postérieures), donc conforme à
        la convention anti-look-ahead.
        """
        close = df["close"]
        formation_return = close.shift(self.skip_days) / close.shift(self.lookback_days) - 1

        position = (formation_return > 0).astype(int)
        position[formation_return.isna()] = 0
        return position.rename("position")

    @property
    def params(self) -> dict[str, object]:
        return {"lookback_days": self.lookback_days, "skip_days": self.skip_days}


def load_momentum_12_1_strategy(path: str | Path) -> Momentum12_1Strategy:
    """Construit une `Momentum12_1Strategy` depuis un fichier YAML.

    Args:
        path: Chemin du fichier YAML (ex. `config/strategies/momentum_12_1.yaml`),
            attendu avec les clés `lookback_days` et `skip_days`.

    Returns:
        `Momentum12_1Strategy` initialisée avec les paramètres du fichier.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Momentum12_1Strategy(lookback_days=raw["lookback_days"], skip_days=raw["skip_days"])
