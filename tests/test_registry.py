"""Tests du registre de stratégies (src.strategies.registry)."""

from __future__ import annotations

import pytest

from src.strategies.momentum_12_1 import Momentum12_1Strategy
from src.strategies.rebalance_bandes import RebalanceBandesStrategy
from src.strategies.registry import STRATEGIES, display_name, load_strategy
from src.strategies.sma_crossover import SmaCrossoverStrategy

_EXPECTED_TYPES = {
    "sma_crossover": SmaCrossoverStrategy,
    "momentum_12_1": Momentum12_1Strategy,
    "rebalance_bandes": RebalanceBandesStrategy,
}


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_load_strategy_returns_instance_of_expected_type(name):
    strategy = load_strategy(name)
    assert isinstance(strategy, _EXPECTED_TYPES[name])


def test_load_strategy_unknown_name_lists_available_names():
    with pytest.raises(ValueError) as exc_info:
        load_strategy("not_a_real_strategy")

    message = str(exc_info.value)
    for name in STRATEGIES:
        assert name in message


def test_load_strategy_dca_raises_explicit_error_mentioning_simulate_dca():
    with pytest.raises(ValueError, match="simulate_dca"):
        load_strategy("dca")


def test_load_strategy_without_config_path_uses_registry_default_config():
    # config/strategies/sma_crossover.yaml (du dépôt) contient fast_period:
    # 20, slow_period: 50 -- valeurs différentes des défauts de la
    # dataclass, donc leur présence prouve que le chemin par défaut du
    # registre a bien été utilisé (pas un simple fallback interne).
    strategy = load_strategy("sma_crossover")
    assert isinstance(strategy, SmaCrossoverStrategy)
    assert strategy.fast_period == 20
    assert strategy.slow_period == 50


def test_display_name_returns_readable_names():
    assert display_name("sma_crossover") == "SMA crossover"
    assert display_name("momentum_12_1") == "Momentum 12-1"
    assert display_name("rebalance_bandes") == "Rebalance par bandes"


def test_display_name_unknown_name_lists_available_names():
    with pytest.raises(ValueError) as exc_info:
        display_name("not_a_real_strategy")

    message = str(exc_info.value)
    for name in STRATEGIES:
        assert name in message
