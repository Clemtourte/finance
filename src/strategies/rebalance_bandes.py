"""Stratégie de rééquilibrage par bandes (mono-actif) : cible 100% investi,
écrêtage (sortie) si la dérive de prix depuis la dernière référence
dépasse une bande symétrique.

Le moteur actuel ne supporte que des positions tout-ou-rien (`{0, 1}`,
voir `src.engine.backtest`), pas de pondération fractionnaire au sein
d'un portefeuille multi-actifs. Cette stratégie adapte donc le principe
classique de rééquilibrage par bandes (dérive d'allocation vs cible) au
cas mono-actif binaire : rester investi tant que le prix reste dans une
bande `+/- band_pct` autour de la référence, sortir (écrêter) quand la
bande est franchie, revenir en position quand le prix est repassé dans la
bande de la référence d'origine (hystérésis), puis fixer une nouvelle
référence pour le cycle suivant.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from src.strategies.base import Strategy


@dataclass
class RebalanceBandesStrategy(Strategy):
    """Cible 100% investi, écrêté quand la dérive dépasse `band_pct`.

    Attributes:
        band_pct: Bande de tolérance symétrique (fraction, ex. `0.10`
            pour +/-10%) autour de la référence courante.
    """

    band_pct: float

    def __post_init__(self) -> None:
        if self.band_pct <= 0:
            raise ValueError("band_pct doit être strictement positif")

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Voir `Strategy.generate_signals`.

        Simulation séquentielle : la décision à J ne dépend que de
        `close[J]` et de la référence établie à une date <= J (jamais
        d'une observation future), donc conforme à la convention
        anti-look-ahead. Boucle explicite (pas vectorisée) car la
        décision est path-dependent (hystérésis).
        """
        close = df["close"]
        n = len(close)
        position = pd.Series(0, index=df.index, dtype=int)
        if n == 0:
            return position.rename("position")

        in_position = True
        reference = close.iloc[0]
        position.iloc[0] = 1

        for i in range(1, n):
            drift = close.iloc[i] / reference - 1
            within_band = abs(drift) <= self.band_pct

            if in_position:
                if within_band:
                    position.iloc[i] = 1
                else:
                    in_position = False
                    position.iloc[i] = 0
            else:
                if within_band:
                    in_position = True
                    reference = close.iloc[i]
                    position.iloc[i] = 1
                else:
                    position.iloc[i] = 0

        return position.rename("position")

    @property
    def params(self) -> dict[str, object]:
        return {"band_pct": self.band_pct}


def load_rebalance_bandes_strategy(path: str | Path) -> RebalanceBandesStrategy:
    """Construit une `RebalanceBandesStrategy` depuis un fichier YAML.

    Args:
        path: Chemin du fichier YAML (ex. `config/strategies/rebalance_bandes.yaml`),
            attendu avec la clé `band_pct`.

    Returns:
        `RebalanceBandesStrategy` initialisée avec le paramètre du fichier.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return RebalanceBandesStrategy(band_pct=raw["band_pct"])
