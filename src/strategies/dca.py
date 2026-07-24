"""Plan d'investissement programmé (DCA, Dollar-Cost Averaging) : apport
fixe au premier jour coté de chaque mois.

Le DCA n'est **pas** une `Strategy` au sens de `src.strategies.base` : il
ne décide pas d'une position cible `{-1, 0, 1}` sur un capital fixe, il
injecte un flux de capital externe et accumule des parts au fil du temps.
`vectorbt.Portfolio.from_signals` ne modélise pas proprement des apports
de cash périodiques (un seul `init_cash` à l'origine, pas de dépôt en
cours de route) ; le DCA est donc simulé ici par un moteur minimal dédié,
indépendant de `src.engine.backtest`, mais réutilisant le même modèle de
coûts (`src.engine.costs`) que le reste du projet.

Convention anti-look-ahead : la date d'investissement (premier jour coté
du mois) se déduit uniquement du calendrier de cotation déjà connu — pas
d'un signal ou d'un prix futur — et l'achat est exécuté au prix `open` de
ce jour, jamais à son `close` ni à un prix ultérieur.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.engine.costs import CostConfig, select_tier


@dataclass(frozen=True)
class DCAResult:
    """Trace complète d'une simulation DCA, alignée sur l'index des prix.

    Attributes:
        contributions: Apport en cash injecté par barre (0 sauf les jours
            d'investissement).
        shares_bought: Parts achetées par barre (0 sauf les jours
            d'investissement).
        cumulative_shares: Parts cumulées détenues, barre par barre.
        cumulative_contributions: Cash cumulé injecté, barre par barre.
        portfolio_value: Valeur du portefeuille marquée au marché
            (`cumulative_shares * close`), barre par barre.
    """

    contributions: pd.Series
    shares_bought: pd.Series
    cumulative_shares: pd.Series
    cumulative_contributions: pd.Series
    portfolio_value: pd.Series


def simulate_dca(
    df: pd.DataFrame,
    monthly_amount: float,
    costs: CostConfig,
    ttf_eligible: bool = False,
    spread_pct: float = 0.0,
) -> DCAResult:
    """Simule un DCA : achat de `monthly_amount` au premier jour coté de chaque mois.

    Args:
        df: OHLCV trié par date croissante, avec au moins les colonnes
            `open` et `close`.
        monthly_amount: Montant investi à chaque date d'investissement (en euros).
        costs: Coûts de transaction (grille de courtage, TTF, glissement de base).
        ttf_eligible: `True` si le titre est soumis à la TTF (achat uniquement).
        spread_pct: Spread propre au titre, ajouté au glissement de base.

    Returns:
        `DCAResult` avec les séries cumulées d'apports, de parts et de
        valeur de portefeuille.

    Raises:
        ValueError: Si `monthly_amount` n'est pas strictement positif.
    """
    if monthly_amount <= 0:
        raise ValueError("monthly_amount doit être strictement positif")

    idx = df.index
    n = len(idx)
    shares_bought = pd.Series(0.0, index=idx)
    contributions = pd.Series(0.0, index=idx)

    if n == 0:
        cumulative_shares = shares_bought.cumsum()
        cumulative_contributions = contributions.cumsum()
        return DCAResult(
            contributions=contributions,
            shares_bought=shares_bought,
            cumulative_shares=cumulative_shares,
            cumulative_contributions=cumulative_contributions,
            portfolio_value=cumulative_shares,
        )

    period = idx.to_period("M")
    is_investment_day = np.append(True, period[1:] != period[:-1])
    total_slippage = costs.base_slippage_pct + spread_pct
    ttf_rate = costs.ttf_pct if ttf_eligible else 0.0

    for i in np.flatnonzero(is_investment_day):
        tier = select_tier(monthly_amount, costs.brokerage_tiers)
        pct = (tier.pct_fee or 0.0) + ttf_rate
        fixed = tier.fixed_fee or 0.0

        fill_price = df["open"].iloc[i] * (1 + total_slippage)
        shares = (monthly_amount - fixed) / (fill_price * (1 + pct))

        shares_bought.iloc[i] = shares
        contributions.iloc[i] = monthly_amount

    cumulative_shares = shares_bought.cumsum()
    cumulative_contributions = contributions.cumsum()
    portfolio_value = cumulative_shares * df["close"]

    return DCAResult(
        contributions=contributions,
        shares_bought=shares_bought,
        cumulative_shares=cumulative_shares,
        cumulative_contributions=cumulative_contributions,
        portfolio_value=portfolio_value,
    )


@dataclass(frozen=True)
class DCAConfig:
    """Paramètres d'un plan DCA, issus d'un fichier YAML."""

    monthly_amount: float


def load_dca_config(path: str | Path) -> DCAConfig:
    """Charge la configuration d'un plan DCA depuis un fichier YAML.

    Args:
        path: Chemin du fichier YAML (ex. `config/strategies/dca.yaml`),
            attendu avec la clé `monthly_amount`.

    Returns:
        `DCAConfig` typée.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return DCAConfig(monthly_amount=raw["monthly_amount"])
