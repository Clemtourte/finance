"""Modèle de coûts de transaction réaliste (courtage par paliers, TTF, spread).

Toutes les fonctions de ce module sont pures (aucun état, aucun accès
I/O) : elles ne font que transformer des prix/positions en tableaux de
frais à fournir à `vectorbt`. Elles sont testées analytiquement (valeurs
recalculées à la main), jamais validées contre elles-mêmes.

Trois composantes, cumulables :

- **Courtage** : grille BoursoBank par paliers (`brokerage_tiers`), un
  palier étant soit un montant fixe, soit un pourcentage de la valeur de
  l'ordre. Appliqué à l'entrée ET à la sortie.
- **TTF** (taxe sur les transactions financières) : taux fixe
  (`ttf_pct`), appliqué à l'**achat uniquement**, et seulement pour les
  titres marqués éligibles (`ttf: true` dans le fichier d'univers —
  grandes capitalisations françaises).
- **Spread** : modélisé comme un glissement (slippage) additionnel,
  propre à chaque titre (`spread_pct`, champ de l'univers), qui s'ajoute
  au glissement générique d'exécution (`base_slippage_pct`). Appliqué
  symétriquement à l'entrée ET à la sortie.

`vectorbt.Portfolio.from_signals` accepte `fees`, `fixed_fees` et
`slippage` sous forme de Series alignées sur l'index des prix (un taux
par barre) plutôt que d'un seul scalaire pour tout le backtest : c'est ce
que construit `build_order_cost_arrays`, en simulant séquentiellement les
allers-retours (peu nombreux : quelques dizaines à quelques centaines
d'ordres sur quinze ans) pour déterminer, à chaque ordre, le palier de
courtage applicable et le capital disponible à ce moment.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BrokerageTier:
    """Un palier de la grille de courtage.

    Un palier définit soit un montant fixe, soit un pourcentage — jamais
    les deux, jamais aucun des deux.

    Attributes:
        max_order_value: Valeur maximale (incluse) de l'ordre pour laquelle
            ce palier s'applique. `None` pour le dernier palier de la
            grille (aucune limite supérieure).
        fixed_fee: Frais fixe en euros pour ce palier.
        pct_fee: Frais proportionnel à la valeur de l'ordre (fraction,
            ex. `0.006` pour 0,60%).
    """

    max_order_value: float | None
    fixed_fee: float | None = None
    pct_fee: float | None = None

    def __post_init__(self) -> None:
        has_fixed = self.fixed_fee is not None
        has_pct = self.pct_fee is not None
        if has_fixed == has_pct:
            raise ValueError(
                "Un palier de courtage doit définir exactement un des deux : "
                "fixed_fee ou pct_fee"
            )


@dataclass(frozen=True)
class CostConfig:
    """Coûts de transaction complets, appliqués à l'entrée ET à la sortie.

    Attributes:
        brokerage_tiers: Grille de courtage par paliers, triée par
            `max_order_value` croissant. Le dernier palier doit avoir
            `max_order_value=None` (couvre tout montant au-delà des
            paliers précédents).
        ttf_pct: Taux de la taxe sur les transactions financières
            (fraction, ex. `0.004` pour 0,4%), appliqué à l'achat
            uniquement, pour les titres éligibles.
        base_slippage_pct: Glissement générique d'exécution (fraction),
            indépendant du spread propre à chaque titre.
    """

    brokerage_tiers: tuple[BrokerageTier, ...]
    ttf_pct: float
    base_slippage_pct: float

    def __post_init__(self) -> None:
        if not self.brokerage_tiers:
            raise ValueError("brokerage_tiers ne peut pas être vide")
        if self.brokerage_tiers[-1].max_order_value is not None:
            raise ValueError(
                "Le dernier palier de courtage doit avoir max_order_value=null "
                "(aucune limite supérieure)"
            )
        for tier in self.brokerage_tiers[:-1]:
            if tier.max_order_value is None:
                raise ValueError(
                    "Seul le dernier palier de courtage peut avoir max_order_value=null"
                )


def select_tier(order_value: float, tiers: tuple[BrokerageTier, ...]) -> BrokerageTier:
    """Sélectionne le palier de courtage applicable à un ordre.

    Args:
        order_value: Valeur notionnelle de l'ordre (en euros, positive).
        tiers: Grille de paliers, triée par `max_order_value` croissant.

    Returns:
        Le premier palier dont `max_order_value` couvre `order_value`.
    """
    for tier in tiers:
        if tier.max_order_value is None or order_value <= tier.max_order_value:
            return tier
    raise AssertionError(
        "unreachable : le dernier palier doit avoir max_order_value=None"
    )  # pragma: no cover


def brokerage_fee_amount(order_value: float, tiers: tuple[BrokerageTier, ...]) -> float:
    """Montant du courtage (en euros) pour un ordre donné.

    Args:
        order_value: Valeur notionnelle de l'ordre (en euros, positive).
        tiers: Grille de paliers.

    Returns:
        Montant du courtage en euros.
    """
    tier = select_tier(order_value, tiers)
    if tier.fixed_fee is not None:
        return tier.fixed_fee
    return order_value * tier.pct_fee


def build_order_cost_arrays(
    open_prices: pd.Series,
    entries: pd.Series,
    exits: pd.Series,
    initial_capital: float,
    costs: CostConfig,
    ttf_eligible: bool,
    spread_pct: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Construit les tableaux `fees`/`fixed_fees`/`slippage` par barre pour vectorbt.

    Simule séquentiellement chaque aller-retour (entrée -> sortie) pour
    déterminer, ordre par ordre, le palier de courtage applicable et le
    capital compounding réellement disponible — un scalaire unique ne
    peut pas représenter une grille de courtage par paliers, qui dépend
    du montant de chaque ordre.

    Le palier à l'entrée est déterminé sur la base du capital disponible
    avant frais (`cash`), une approximation du montant réel de l'ordre
    (`cash` moins les frais fixes, divisé par `1 + taux`) sans incidence
    pratique sauf à quelques centimes d'un seuil de palier. Le palier à
    la sortie est en revanche exact : il est déterminé sur la valeur
    brute de la position (`taille * prix de sortie`), connue avec
    certitude puisque la taille a été fixée à l'entrée.

    Args:
        open_prices: Prix d'exécution (open) de chaque barre.
        entries: Signaux d'entrée (sortie de `positions_to_entries_exits`).
        exits: Signaux de sortie (sortie de `positions_to_entries_exits`).
        initial_capital: Capital de départ.
        costs: Coûts de transaction (grille de courtage, TTF, glissement de base).
        ttf_eligible: Si `True`, la TTF s'applique à chaque achat.
        spread_pct: Spread propre au titre (fraction), ajouté au
            glissement de base, symétriquement à l'entrée et à la sortie.

    Returns:
        Tuple `(fees, fixed_fees, slippage)` de Series alignées sur
        `open_prices.index`, valant `0.0` sur les barres sans ordre.
    """
    idx = open_prices.index
    fees = pd.Series(0.0, index=idx)
    fixed_fees = pd.Series(0.0, index=idx)
    slippage = pd.Series(0.0, index=idx)

    total_slippage_rate = costs.base_slippage_pct + spread_pct
    ttf_rate = costs.ttf_pct if ttf_eligible else 0.0

    entry_positions = list(np.flatnonzero(entries.to_numpy()))
    exit_positions = list(np.flatnonzero(exits.to_numpy()))

    cash = initial_capital
    remaining_exits = list(exit_positions)

    for entry_pos in entry_positions:
        entry_tier = select_tier(cash, costs.brokerage_tiers)
        entry_pct = (entry_tier.pct_fee or 0.0) + ttf_rate
        entry_fixed = entry_tier.fixed_fee or 0.0

        fees.iloc[entry_pos] = entry_pct
        fixed_fees.iloc[entry_pos] = entry_fixed
        slippage.iloc[entry_pos] = total_slippage_rate

        entry_fill = open_prices.iloc[entry_pos] * (1 + total_slippage_rate)
        size = (cash - entry_fixed) / (entry_fill * (1 + entry_pct))

        matching_exit = next((e for e in remaining_exits if e > entry_pos), None)
        if matching_exit is None:
            break  # position encore ouverte à la fin de la période (type buy & hold)
        remaining_exits.remove(matching_exit)

        exit_open = open_prices.iloc[matching_exit]
        gross_exit_value = size * exit_open  # valeur brute, avant frais/glissement
        exit_tier = select_tier(gross_exit_value, costs.brokerage_tiers)
        exit_pct = exit_tier.pct_fee or 0.0
        exit_fixed = exit_tier.fixed_fee or 0.0

        fees.iloc[matching_exit] = exit_pct
        fixed_fees.iloc[matching_exit] = exit_fixed
        slippage.iloc[matching_exit] = total_slippage_rate

        exit_fill = exit_open * (1 - total_slippage_rate)
        cash = size * exit_fill - (size * exit_fill * exit_pct + exit_fixed)

    return fees, fixed_fees, slippage
