"""Registre des stratégies utilisables par les CLI (`src.engine.cli`,
`src.engine.batch`) : associe un nom court, son loader YAML, un nom
affichable et un chemin de configuration par défaut.

Le DCA (`src.strategies.dca`) n'apparaît volontairement PAS dans
`STRATEGIES` : ce n'est pas une `Strategy` (voir son docstring de module),
il n'est donc pas exécutable par ces CLI. `load_strategy` le signale
explicitement plutôt que de laisser échouer un attribut manquant plus
loin dans le moteur.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.strategies.base import Strategy
from src.strategies.momentum_12_1 import load_momentum_12_1_strategy
from src.strategies.rebalance_bandes import load_rebalance_bandes_strategy
from src.strategies.sma_crossover import load_sma_crossover_strategy


@dataclass(frozen=True)
class _StrategyEntry:
    """Une entrée du registre : comment charger une stratégie et l'afficher."""

    loader: Callable[[str | Path], Strategy]
    display_name: str
    default_config: str


#: Nom court (utilisé en CLI, ex. `--strategy momentum_12_1`) -> entrée de registre.
STRATEGIES: dict[str, _StrategyEntry] = {
    "sma_crossover": _StrategyEntry(
        loader=load_sma_crossover_strategy,
        display_name="SMA crossover",
        default_config="config/strategies/sma_crossover.yaml",
    ),
    "momentum_12_1": _StrategyEntry(
        loader=load_momentum_12_1_strategy,
        display_name="Momentum 12-1",
        default_config="config/strategies/momentum_12_1.yaml",
    ),
    "rebalance_bandes": _StrategyEntry(
        loader=load_rebalance_bandes_strategy,
        display_name="Rebalance par bandes",
        default_config="config/strategies/rebalance_bandes.yaml",
    ),
}

_DCA_ERROR = (
    "dca n'implémente pas Strategy (src.strategies.base) : c'est un plan "
    "d'apport périodique sur flux de cash externe, pas une position cible sur "
    "capital fixe. Il dispose de son propre moteur (src.strategies.dca."
    "simulate_dca) et n'est pas utilisable via ce CLI."
)


def _unknown_strategy_error(name: str) -> ValueError:
    available = ", ".join(sorted(STRATEGIES))
    return ValueError(f"Stratégie inconnue : {name!r}. Disponibles : {available}")


def load_strategy(name: str, config_path: str | Path | None = None) -> Strategy:
    """Charge une stratégie du registre depuis son fichier YAML.

    Args:
        name: Nom court de la stratégie (clé de `STRATEGIES`).
        config_path: Chemin du fichier YAML de paramètres. `None` (défaut)
            utilise le chemin par défaut du registre pour `name`.

    Returns:
        Instance de `Strategy` construite par le loader correspondant.

    Raises:
        ValueError: Si `name` vaut `"dca"` (n'implémente pas `Strategy`,
            voir le docstring de module), ou si `name` est inconnu du
            registre (le message liste alors les noms disponibles).
    """
    if name == "dca":
        raise ValueError(_DCA_ERROR)

    entry = STRATEGIES.get(name)
    if entry is None:
        raise _unknown_strategy_error(name)

    path = entry.default_config if config_path is None else config_path
    return entry.loader(path)


def display_name(name: str) -> str:
    """Nom lisible d'une stratégie du registre, pour l'affichage en CLI.

    Args:
        name: Nom court de la stratégie (clé de `STRATEGIES`).

    Returns:
        Nom affichable (ex. `"SMA crossover"`, `"Momentum 12-1"`,
        `"Rebalance par bandes"`).

    Raises:
        ValueError: Si `name` est inconnu du registre.
    """
    entry = STRATEGIES.get(name)
    if entry is None:
        raise _unknown_strategy_error(name)
    return entry.display_name
