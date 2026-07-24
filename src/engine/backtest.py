"""Moteur de backtest : applique une position cible à un historique de prix.

Convention d'exécution (voir aussi `src/data/schema.py`) : la position
cible à l'index J, telle que retournée par `Strategy.generate_signals`,
est calculée à partir des données disponibles à la clôture de J. Ce
module matérialise cette convention en décalant la position d'un jour
avant de la transformer en ordres, puis en exécutant ces ordres au prix
`open` du jour d'application — jamais au `close`.

Les coûts de transaction (`CostConfig`) sont un paramètre obligatoire de
`run_backtest` et `run_buy_and_hold` : il n'existe pas de chemin de code
qui exécute un backtest sans coûts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt

from src.engine.costs import CostConfig, build_order_cost_arrays

#: Positions non supportées par le moteur (v1 : long-only, tout-ou-rien).
_SUPPORTED_POSITIONS = frozenset({0, 1})

#: Fréquences de rééquilibrage supportées et leur code de période pandas.
_REBALANCE_PERIOD_CODES = {"weekly": "W", "monthly": "M"}


def resample_target_position(target_position: pd.Series, rebalance_freq: str) -> pd.Series:
    """Échantillonne la position cible aux dates de rééquilibrage.

    Le signal reste calculé quotidiennement par la stratégie (aucun
    changement côté `Strategy.generate_signals`) ; seule son
    *application* est échantillonnée ici : un changement de position
    cible ne compte que s'il est décidé à la dernière séance d'une
    période (semaine ou mois). Entre deux dates de rééquilibrage, la
    position appliquée reste celle décidée à la dernière date de
    rééquilibrage déjà passée, même si le signal quotidien a varié
    entre-temps.

    Aucun look-ahead : la date de fin de période (dernière séance
    effectivement cotée d'une semaine/d'un mois) se déduit uniquement du
    calendrier de cotation déjà connu (l'index de `target_position`),
    jamais d'une valeur de prix ou de signal future. Le masquage/`ffill`
    ci-dessous ne propage que des valeurs déjà observées, jamais une
    valeur qui ne sera décidée que plus tard dans la période en cours.

    Args:
        target_position: Position cible quotidienne (sortie de
            `Strategy.generate_signals`).
        rebalance_freq: `"daily"` (aucun échantillonnage), `"weekly"` ou
            `"monthly"`.

    Returns:
        Position cible échantillonnée, même index que `target_position`.
        `0` avant la première date de rééquilibrage (aucune décision
        antérieure disponible).

    Raises:
        ValueError: Si `rebalance_freq` n'est pas une valeur supportée.
    """
    if rebalance_freq == "daily":
        return target_position

    if rebalance_freq not in _REBALANCE_PERIOD_CODES:
        raise ValueError(
            f"rebalance_freq={rebalance_freq!r} non supporté : "
            f"attendu 'daily', 'weekly' ou 'monthly'"
        )

    period = target_position.index.to_period(_REBALANCE_PERIOD_CODES[rebalance_freq])
    is_period_end = np.append(period[:-1] != period[1:], True)  # dernière barre = fin de série

    masked = target_position.where(pd.Series(is_period_end, index=target_position.index))
    return masked.ffill().fillna(0).astype(int)


def shift_to_execution(target_position: pd.Series) -> pd.Series:
    """Décale une position cible de la clôture de J vers son application en J+1.

    Args:
        target_position: Position cible décidée à la clôture de chaque
            date (sortie de `Strategy.generate_signals`).

    Returns:
        Series entière (`int`), même index que `target_position` : la
        position que le portefeuille doit détenir à partir de chaque date
        (0 pour la première date, faute de signal antérieur disponible).
    """
    return target_position.shift(1).fillna(0).astype(int)


def positions_to_entries_exits(execution_position: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Convertit une série de positions détenues en signaux d'entrée/sortie.

    Args:
        execution_position: Position réellement détenue à chaque date
            (sortie de `shift_to_execution`), valeurs dans `{0, 1}`.

    Returns:
        Tuple `(entries, exits)` de Series booléennes, même index. `entries`
        est vraie le jour où la position passe de 0 à 1, `exits` le jour où
        elle repasse de 1 à 0.
    """
    previous = execution_position.shift(1).fillna(0).astype(int)
    entries = (execution_position == 1) & (previous == 0)
    exits = (execution_position == 0) & (previous == 1)
    return entries, exits


def _validate_positions(target_position: pd.Series) -> None:
    observed = set(target_position.dropna().unique().tolist())
    if not observed.issubset(_SUPPORTED_POSITIONS):
        unsupported = observed - _SUPPORTED_POSITIONS
        raise ValueError(
            f"Positions non supportées {unsupported} : le moteur v1 ne gère "
            "que le long-only tout-ou-rien (positions 0 ou 1)"
        )


def run_backtest(
    df: pd.DataFrame,
    target_position: pd.Series,
    costs: CostConfig,
    initial_capital: float,
    ttf_eligible: bool = False,
    spread_pct: float = 0.0,
    rebalance_freq: str = "daily",
    freq: str = "1D",
) -> vbt.Portfolio:
    """Exécute un backtest à partir d'une position cible et d'une config de coûts.

    Args:
        df: OHLCV trié par date croissante, avec au moins les colonnes
            `open` et `close`. Même index que `target_position`.
        target_position: Position cible décidée à la clôture de chaque
            date (sortie de `Strategy.generate_signals`), valeurs dans
            `{0, 1}` (long-only tout-ou-rien).
        costs: Coûts de transaction (grille de courtage, TTF, glissement
            de base), appliqués à l'entrée et à la sortie.
        initial_capital: Capital de départ.
        ttf_eligible: `True` si le titre est soumis à la taxe sur les
            transactions financières (champ `ttf` de l'univers).
        spread_pct: Spread propre au titre (champ `spread_pct` de
            l'univers), ajouté au glissement de base.
        rebalance_freq: `"daily"` (défaut), `"weekly"` ou `"monthly"` —
            voir `resample_target_position`.
        freq: Fréquence des barres, transmise à vectorbt.

    Returns:
        `vectorbt.Portfolio` du backtest, exécuté à l'open de J+1 pour un
        signal décidé à la clôture de J (dernière séance de la période de
        rééquilibrage).

    Raises:
        ValueError: Si `target_position` contient des valeurs hors `{0, 1}`.
    """
    _validate_positions(target_position)
    sampled_position = resample_target_position(target_position, rebalance_freq)
    execution_position = shift_to_execution(sampled_position)
    entries, exits = positions_to_entries_exits(execution_position)

    fees, fixed_fees, slippage = build_order_cost_arrays(
        df["open"], entries, exits, initial_capital, costs, ttf_eligible, spread_pct
    )

    return vbt.Portfolio.from_signals(
        close=df["close"],
        entries=entries,
        exits=exits,
        price=df["open"],
        fees=fees,
        fixed_fees=fixed_fees,
        slippage=slippage,
        init_cash=initial_capital,
        freq=freq,
    )


def run_buy_and_hold(
    df: pd.DataFrame,
    costs: CostConfig,
    initial_capital: float,
    ttf_eligible: bool = False,
    spread_pct: float = 0.0,
    freq: str = "1D",
) -> vbt.Portfolio:
    """Backtest de référence : achat en tout début de série, conservé jusqu'à la fin.

    Sert de comparaison systématique à toute stratégie (voir `src.metrics`
    et `src.reporting`) : mêmes coûts, même capital initial, même période.

    Args:
        df: OHLCV trié par date croissante, avec au moins les colonnes
            `open` et `close`.
        costs: Coûts de transaction (appliqués une seule fois, à l'achat).
        initial_capital: Capital de départ.
        ttf_eligible: `True` si le titre est soumis à la taxe sur les
            transactions financières (champ `ttf` de l'univers).
        spread_pct: Spread propre au titre (champ `spread_pct` de
            l'univers), ajouté au glissement de base.
        freq: Fréquence des barres, transmise à vectorbt.

    Returns:
        `vectorbt.Portfolio` achetant au premier `open` disponible et
        conservant la position (marquée au marché) jusqu'à la fin de la
        période.
    """
    entries = pd.Series(False, index=df.index)
    exits = pd.Series(False, index=df.index)
    entries.iloc[0] = True

    fees, fixed_fees, slippage = build_order_cost_arrays(
        df["open"], entries, exits, initial_capital, costs, ttf_eligible, spread_pct
    )

    return vbt.Portfolio.from_signals(
        close=df["close"],
        entries=entries,
        exits=exits,
        price=df["open"],
        fees=fees,
        fixed_fees=fixed_fees,
        slippage=slippage,
        init_cash=initial_capital,
        freq=freq,
    )
