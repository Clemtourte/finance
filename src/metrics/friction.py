"""Reconstruction de la friction cumulée (courtage, TTF, spread/glissement)
payée sur un ensemble de trades, à partir du journal `vectorbt` déjà
produit par un backtest.

Aucune re-simulation n'est nécessaire : chaque composante se déduit
exactement des colonnes du journal de trades (`Size`, `Avg Entry Price`,
`Entry Fees`, `Avg Exit Price`, `Exit Fees`) combinées à la configuration
de coûts déjà utilisée pour le backtest (`CostConfig`, éligibilité TTF,
spread du titre) :

- Le capital engagé avant frais à l'entrée se retrouve exactement par
  `notional_entrée + Entry Fees` (défini ainsi par construction, quel que
  soit le palier appliqué) : `select_tier` sur cette valeur retrouve le
  palier exact utilisé au moment du backtest (voir
  `src.engine.costs.build_order_cost_arrays`, qui utilise la même valeur
  pour sélectionner le palier).
- Une fois le palier connu, `Entry Fees` se répartit entre courtage et
  TTF (palier fixe : la totalité est du courtage, le reste est de la
  TTF ; palier pourcentage : répartition au prorata des taux courtage/TTF).
- Le coût de glissement (spread + glissement de base) se lit comme
  l'écart entre le prix d'exécution rapporté (`Avg Entry/Exit Price`,
  déjà glissé) et le prix brut (`open`) de la barre d'entrée/sortie.

Un trade encore ouvert (`Status == "Open"`, ex. un buy & hold jamais
soldé) a bien payé sa friction d'entrée : elle est comptée. Sa "sortie"
n'étant qu'une valorisation au marché, pas une transaction réelle,
aucune friction de sortie ne lui est imputée.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.engine.costs import CostConfig, select_tier


@dataclass(frozen=True)
class FrictionBreakdown:
    """Friction cumulée payée sur un ensemble de trades, par composante.

    Attributes:
        brokerage_eur: Courtage cumulé (paliers de `CostConfig`), entrée + sortie.
        ttf_eur: Taxe sur les transactions financières cumulée (achat uniquement).
        slippage_eur: Coût de glissement cumulé (spread du titre + glissement
            de base), entrée + sortie.
    """

    brokerage_eur: float
    ttf_eur: float
    slippage_eur: float

    @property
    def total_eur(self) -> float:
        """Friction totale, toutes composantes confondues."""
        return self.brokerage_eur + self.ttf_eur + self.slippage_eur


def compute_friction(
    trades: pd.DataFrame,
    open_prices: pd.Series,
    costs: CostConfig,
    ttf_eligible: bool,
    spread_pct: float,
) -> FrictionBreakdown:
    """Reconstruit la friction cumulée (courtage/TTF/glissement) d'un journal de trades.

    Args:
        trades: `portfolio.trades.records_readable` (trades ouverts et
            clôturés), colonnes `Status`, `Size`, `Avg Entry Price`,
            `Entry Fees`, `Avg Exit Price`, `Exit Fees`, `Entry Timestamp`,
            `Exit Timestamp`.
        open_prices: Prix `open` bruts (non glissés), indexés par date —
            typiquement `df["open"]` du backtest d'origine, non tronqué
            (les timestamps des trades y sont recherchés par valeur).
        costs: Configuration de coûts utilisée pour le backtest d'origine.
        ttf_eligible: Éligibilité TTF utilisée pour le backtest d'origine.
        spread_pct: Spread du titre utilisé pour le backtest d'origine.

    Returns:
        `FrictionBreakdown` cumulé sur l'ensemble des trades fournis.
    """
    if not len(trades):
        return FrictionBreakdown(0.0, 0.0, 0.0)

    ttf_rate = costs.ttf_pct if ttf_eligible else 0.0

    brokerage_total = 0.0
    ttf_total = 0.0
    slippage_total = 0.0

    for _, trade in trades.iterrows():
        size = trade["Size"]

        # --- Entrée : toujours payée, même pour un trade encore ouvert. ---
        entry_fill = trade["Avg Entry Price"]
        entry_fees = trade["Entry Fees"]
        entry_notional = size * entry_fill
        cash_before_entry = entry_notional + entry_fees

        entry_tier = select_tier(cash_before_entry, costs.brokerage_tiers)
        if entry_tier.fixed_fee is not None:
            entry_brokerage = entry_tier.fixed_fee
            entry_ttf = entry_fees - entry_brokerage
        else:
            total_pct = entry_tier.pct_fee + ttf_rate
            entry_brokerage = entry_fees * (entry_tier.pct_fee / total_pct) if total_pct else 0.0
            entry_ttf = entry_fees * (ttf_rate / total_pct) if total_pct else 0.0

        brokerage_total += entry_brokerage
        ttf_total += entry_ttf

        entry_raw_price = open_prices.loc[trade["Entry Timestamp"]]
        slippage_total += size * abs(entry_fill - entry_raw_price)

        # --- Sortie : uniquement si le trade est réellement clôturé. ---
        if trade["Status"] == "Closed":
            exit_fees = trade["Exit Fees"]
            brokerage_total += exit_fees  # pas de TTF à la vente

            exit_fill = trade["Avg Exit Price"]
            exit_raw_price = open_prices.loc[trade["Exit Timestamp"]]
            slippage_total += size * abs(exit_fill - exit_raw_price)

    return FrictionBreakdown(brokerage_total, ttf_total, slippage_total)
