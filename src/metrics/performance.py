"""Métriques de performance calculées à partir d'une courbe d'équité et
d'un journal de trades.

Chaque fonction est une formule autonome et testable indépendamment de
`vectorbt` ; `compute_metrics` est le seul point de couplage avec
`vectorbt.Portfolio`, ce qui permet de tester les formules sur des séries
synthétiques sans dépendre du moteur de backtest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd
import vectorbt as vbt


def periodic_returns(equity: pd.Series) -> pd.Series:
    """Rendements période à période d'une courbe d'équité.

    Args:
        equity: Valeur du portefeuille, indexée par date croissante.

    Returns:
        Series des rendements simples (`equity[t] / equity[t-1] - 1`),
        sans la première date (pas de rendement défini).
    """
    return equity.pct_change().dropna()


def cagr(equity: pd.Series, periods_per_year: int) -> float:
    """Taux de croissance annuel composé (Compound Annual Growth Rate).

    Args:
        equity: Valeur du portefeuille, indexée par date croissante.
        periods_per_year: Nombre de périodes (séances) par an.

    Returns:
        CAGR en fraction (ex. `0.08` pour +8%/an). `NaN` si `equity` a
        moins de 2 points ou une valeur de départ non positive.
    """
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return float("nan")
    n_periods = len(equity) - 1
    years = n_periods / periods_per_year
    if years <= 0:
        return float("nan")
    total_growth = equity.iloc[-1] / equity.iloc[0]
    if total_growth <= 0:
        return float("nan")
    return total_growth ** (1 / years) - 1


def annualized_volatility(returns: pd.Series, periods_per_year: int) -> float:
    """Écart-type annualisé des rendements périodiques.

    Args:
        returns: Rendements périodiques (sortie de `periodic_returns`).
        periods_per_year: Nombre de périodes (séances) par an.

    Returns:
        Volatilité annualisée en fraction. `NaN` si `returns` a moins de 2 points.
    """
    if len(returns) < 2:
        return float("nan")
    return returns.std(ddof=1) * math.sqrt(periods_per_year)


def sharpe_ratio(returns: pd.Series, risk_free_rate: float, periods_per_year: int) -> float:
    """Ratio de Sharpe annualisé.

    Args:
        returns: Rendements périodiques (sortie de `periodic_returns`).
        risk_free_rate: Taux sans risque annuel (fraction, ex. `0.02`).
        periods_per_year: Nombre de périodes (séances) par an.

    Returns:
        Ratio de Sharpe annualisé. `NaN` si la volatilité des rendements
        excédentaires est nulle ou indéfinie.
    """
    if len(returns) < 2:
        return float("nan")
    rf_per_period = (1 + risk_free_rate) ** (1 / periods_per_year) - 1
    excess = returns - rf_per_period
    vol = excess.std(ddof=1)
    if not vol or pd.isna(vol):
        return float("nan")
    return excess.mean() / vol * math.sqrt(periods_per_year)


def sortino_ratio(returns: pd.Series, risk_free_rate: float, periods_per_year: int) -> float:
    """Ratio de Sortino annualisé (ne pénalise que la volatilité à la baisse).

    Args:
        returns: Rendements périodiques (sortie de `periodic_returns`).
        risk_free_rate: Taux sans risque annuel (fraction).
        periods_per_year: Nombre de périodes (séances) par an.

    Returns:
        Ratio de Sortino annualisé. `NaN` si aucun rendement excédentaire
        n'est négatif (downside deviation nulle) ou si `returns` est trop court.
    """
    if len(returns) < 2:
        return float("nan")
    rf_per_period = (1 + risk_free_rate) ** (1 / periods_per_year) - 1
    excess = returns - rf_per_period
    downside = excess[excess < 0]
    if downside.empty:
        return float("nan")
    downside_deviation = math.sqrt((downside**2).mean())
    if downside_deviation == 0:
        return float("nan")
    return excess.mean() / downside_deviation * math.sqrt(periods_per_year)


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Série de drawdown (perte relative depuis le plus haut historique).

    Args:
        equity: Valeur du portefeuille, indexée par date croissante.

    Returns:
        Series de valeurs `<= 0` (0 = plus haut historique).
    """
    running_max = equity.cummax()
    return equity / running_max - 1


def max_drawdown(equity: pd.Series) -> float:
    """Drawdown maximal (valeur négative, ex. `-0.25` pour -25%).

    Args:
        equity: Valeur du portefeuille, indexée par date croissante.

    Returns:
        Drawdown maximal en fraction. `0.0` si `equity` est vide.
    """
    if equity.empty:
        return 0.0
    return float(drawdown_series(equity).min())


def max_drawdown_duration(equity: pd.Series) -> int:
    """Durée du plus long drawdown, en nombre de barres.

    Args:
        equity: Valeur du portefeuille, indexée par date croissante.

    Returns:
        Nombre de barres consécutives sous le plus haut historique
        (0 si `equity` ne descend jamais sous son plus haut).
    """
    underwater = drawdown_series(equity) < 0
    if not underwater.any():
        return 0
    groups = (~underwater).cumsum()
    run_lengths = underwater.groupby(groups).sum()
    return int(run_lengths.max())


def win_rate(trade_pnls: pd.Series) -> float:
    """Proportion de trades clôturés gagnants.

    Args:
        trade_pnls: PnL réalisé de chaque trade clôturé.

    Returns:
        Fraction entre 0 et 1. `NaN` si `trade_pnls` est vide.
    """
    if trade_pnls.empty:
        return float("nan")
    return float((trade_pnls > 0).mean())


def profit_factor(trade_pnls: pd.Series) -> float:
    """Somme des gains / somme des pertes (en valeur absolue) sur les trades clôturés.

    Args:
        trade_pnls: PnL réalisé de chaque trade clôturé.

    Returns:
        Profit factor. `inf` si aucune perte et au moins un gain, `NaN` si
        `trade_pnls` est vide ou sans aucun gain ni perte.
    """
    if trade_pnls.empty:
        return float("nan")
    gains = trade_pnls[trade_pnls > 0].sum()
    losses = -trade_pnls[trade_pnls < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else float("nan")
    return float(gains / losses)


def num_trades(trade_pnls: pd.Series) -> int:
    """Nombre de trades clôturés.

    Args:
        trade_pnls: PnL réalisé de chaque trade clôturé.

    Returns:
        Nombre de trades.
    """
    return int(len(trade_pnls))


def turnover(traded_notional: float, average_equity: float) -> float:
    """Turnover : valeur totale échangée (achats + ventes) / capital moyen.

    Args:
        traded_notional: Somme des valeurs (entrée + sortie) de tous les
            trades clôturés sur la période.
        average_equity: Valeur moyenne du portefeuille sur la période.

    Returns:
        Turnover en fraction (ex. `2.0` = l'équivalent de 2x le capital
        moyen a été échangé sur la période). `NaN` si `average_equity` est nul.
    """
    if not average_equity:
        return float("nan")
    return traded_notional / average_equity


@dataclass(frozen=True)
class MetricsResult:
    """Ensemble de métriques de performance pour un portefeuille."""

    cagr: float
    annualized_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_duration_bars: int
    win_rate: float
    profit_factor: float
    num_trades: int
    turnover: float


def compute_metrics_from_series(
    equity: pd.Series,
    trades: pd.DataFrame,
    periods_per_year: int,
    risk_free_rate: float,
) -> MetricsResult:
    """Calcule l'ensemble des métriques à partir d'une équité et d'un journal de trades.

    Fonction de bas niveau utilisée par `compute_metrics` (sur un
    portefeuille entier) et par `split_portfolio_by_date` (sur une
    sous-période in-sample/out-of-sample) : toutes deux se ramènent à une
    Series d'équité et un DataFrame de trades, sur lesquels les mêmes
    formules s'appliquent sans changement (`cagr`, `max_drawdown`, etc.
    sont relatifs à la série passée, jamais à une référence globale).

    Args:
        equity: Valeur du portefeuille, indexée par date croissante.
        trades: `portfolio.trades.records_readable` (ou un sous-ensemble
            de ses lignes), avec au moins les colonnes `Status`, `PnL`,
            `Size`, `Avg Entry Price`, `Avg Exit Price`.
        periods_per_year: Nombre de périodes (séances) par an.
        risk_free_rate: Taux sans risque annuel (fraction).

    Returns:
        `MetricsResult` consolidé. Les métriques basées sur les trades
        (`win_rate`, `profit_factor`, `num_trades`, `turnover`) ne
        comptent que les trades clôturés (une position encore ouverte à
        la fin de la période, comme un buy & hold jamais soldé, est
        valorisée au marché dans l'équité mais n'est pas comptée comme un
        trade réalisé).
    """
    returns = periodic_returns(equity)

    closed = trades[trades["Status"] == "Closed"] if len(trades) else trades
    pnl = closed["PnL"] if "PnL" in closed else pd.Series(dtype=float)

    if len(closed):
        traded_notional = float(
            (closed["Size"] * closed["Avg Entry Price"] + closed["Size"] * closed["Avg Exit Price"]).sum()
        )
    else:
        traded_notional = 0.0

    return MetricsResult(
        cagr=cagr(equity, periods_per_year),
        annualized_volatility=annualized_volatility(returns, periods_per_year),
        sharpe_ratio=sharpe_ratio(returns, risk_free_rate, periods_per_year),
        sortino_ratio=sortino_ratio(returns, risk_free_rate, periods_per_year),
        max_drawdown=max_drawdown(equity),
        max_drawdown_duration_bars=max_drawdown_duration(equity),
        win_rate=win_rate(pnl),
        profit_factor=profit_factor(pnl),
        num_trades=num_trades(pnl),
        turnover=turnover(traded_notional, float(equity.mean())),
    )


def compute_metrics(
    portfolio: vbt.Portfolio,
    periods_per_year: int,
    risk_free_rate: float,
) -> MetricsResult:
    """Calcule l'ensemble des métriques pour un `vectorbt.Portfolio`.

    Args:
        portfolio: Portefeuille issu de `src.engine.backtest.run_backtest`
            ou `run_buy_and_hold`.
        periods_per_year: Nombre de périodes (séances) par an.
        risk_free_rate: Taux sans risque annuel (fraction).

    Returns:
        `MetricsResult` consolidé (voir `compute_metrics_from_series`).
    """
    return compute_metrics_from_series(
        portfolio.value(), portfolio.trades.records_readable, periods_per_year, risk_free_rate
    )


def split_portfolio_by_date(
    portfolio: vbt.Portfolio, split_date: date
) -> tuple[pd.Series, pd.DataFrame, pd.Series, pd.DataFrame]:
    """Découpe l'équité et les trades d'un portefeuille en in-sample / out-of-sample.

    Un trade est affecté à la sous-période de sa date d'**entrée**
    (convention simple pour les trades qui chevauchent la date de
    coupure). L'équité de chaque sous-période est reprise telle quelle
    (pas re-basée à 100) : `compute_metrics_from_series` calcule déjà ses
    métriques relativement au premier point de la série passée.

    Args:
        portfolio: Portefeuille complet (`run_backtest` ou `run_buy_and_hold`).
        split_date: Date de coupure ; in-sample = dates < `split_date`,
            out-of-sample = dates >= `split_date`.

    Returns:
        Tuple `(equity_is, trades_is, equity_oos, trades_oos)`.
    """
    equity = portfolio.value()
    trades = portfolio.trades.records_readable
    split_ts = pd.Timestamp(split_date)

    equity_is = equity[equity.index < split_ts]
    equity_oos = equity[equity.index >= split_ts]

    if len(trades):
        entry_ts = pd.to_datetime(trades["Entry Timestamp"])
        trades_is = trades[entry_ts < split_ts]
        trades_oos = trades[entry_ts >= split_ts]
    else:
        trades_is = trades
        trades_oos = trades

    return equity_is, trades_is, equity_oos, trades_oos
