"""Validation de qualité des séries OHLCV : trous, valeurs aberrantes,
et splits non correctement ajustés.

Ces fonctions ne corrigent rien : elles signalent des anomalies pour
inspection humaine avant que les données ne soient utilisées pour du
backtest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

#: Ratios jour/veille caractéristiques d'un split ou reverse split boursier.
_TYPICAL_SPLIT_RATIOS: tuple[float, ...] = (
    2.0,
    3.0,
    4.0,
    5.0,
    10.0,
    1 / 2,
    1 / 3,
    1 / 4,
    1 / 5,
    1 / 10,
)


def detect_gaps(df: pd.DataFrame, max_gap_calendar_days: int) -> pd.DataFrame:
    """Détecte les trous suspects dans une série de dates de cotation.

    Un trou est un écart entre deux séances consécutives strictement
    supérieur à `max_gap_calendar_days` jours calendaires. Les week-ends et
    jours fériés courts sont normaux et ne sont pas signalés si le seuil
    est fixé en conséquence (5 jours par défaut couvre les longs week-ends).

    Args:
        df: DataFrame avec une colonne `date` (triée ou non).
        max_gap_calendar_days: Écart maximal toléré, en jours calendaires,
            entre deux séances consécutives.

    Returns:
        DataFrame avec les colonnes `gap_start`, `gap_end`,
        `calendar_days`, une ligne par trou détecté, trié par `gap_start`.
        Vide si aucun trou n'est détecté ou si `df` a moins de 2 lignes.
    """
    if len(df) < 2:
        return pd.DataFrame(columns=["gap_start", "gap_end", "calendar_days"])

    dates = pd.to_datetime(df["date"]).sort_values().reset_index(drop=True)
    deltas = dates.diff().dt.days
    suspect = deltas > max_gap_calendar_days

    result = pd.DataFrame(
        {
            "gap_start": dates.shift(1)[suspect].dt.date.reset_index(drop=True),
            "gap_end": dates[suspect].dt.date.reset_index(drop=True),
            "calendar_days": deltas[suspect].astype("int64").reset_index(drop=True),
        }
    )
    return result.sort_values("gap_start").reset_index(drop=True)


def detect_outliers(df: pd.DataFrame, return_threshold: float) -> pd.DataFrame:
    """Détecte les séances avec un rendement close-to-close anormalement fort.

    Args:
        df: DataFrame trié par date avec les colonnes `date` et `adj_close`.
        return_threshold: Seuil absolu de rendement journalier (ex. `0.30`
            pour 30%) au-delà duquel une séance est signalée.

    Returns:
        DataFrame avec les colonnes `date`, `adj_close`, `daily_return`,
        une ligne par séance suspecte, trié par date.
    """
    if len(df) < 2:
        return pd.DataFrame(columns=["date", "adj_close", "daily_return"])

    ordered = df.sort_values("date").reset_index(drop=True)
    returns = ordered["adj_close"].pct_change()
    suspect = returns.abs() > return_threshold

    return pd.DataFrame(
        {
            "date": ordered.loc[suspect, "date"].reset_index(drop=True),
            "adj_close": ordered.loc[suspect, "adj_close"].reset_index(drop=True),
            "daily_return": returns[suspect].reset_index(drop=True),
        }
    )


def detect_unadjusted_splits(df: pd.DataFrame, ratio_tolerance: float) -> pd.DataFrame:
    """Détecte les splits qui n'ont apparemment pas été répercutés sur `adj_close`.

    Principe : lors d'un split correctement ajusté, le prix brut (`close`)
    saute d'un ratio caractéristique (2x, 0.5x, ...) alors que le prix
    ajusté (`adj_close`) reste lissé. Si `adj_close` saute du même ratio
    que `close`, l'ajustement n'a probablement pas été appliqué.

    Args:
        df: DataFrame trié par date avec les colonnes `date`, `close`,
            `adj_close`.
        ratio_tolerance: Tolérance relative pour reconnaître un ratio
            jour/veille comme un ratio de split typique (ex. `0.03` pour
            +/-3%).

    Returns:
        DataFrame avec les colonnes `date`, `close_ratio`, `adj_close_ratio`,
        `matched_split_ratio`, une ligne par anomalie suspectée.
    """
    if len(df) < 2:
        return pd.DataFrame(
            columns=["date", "close_ratio", "adj_close_ratio", "matched_split_ratio"]
        )

    ordered = df.sort_values("date").reset_index(drop=True)
    close_ratio = ordered["close"] / ordered["close"].shift(1)
    adj_ratio = ordered["adj_close"] / ordered["adj_close"].shift(1)

    matched = close_ratio.apply(_match_split_ratio, tolerance=ratio_tolerance)
    adj_also_jumped = pd.Series(
        [
            _match_split_ratio(r, ratio_tolerance) is not None
            for r in adj_ratio
        ]
    )
    suspect = matched.notna() & adj_also_jumped

    return pd.DataFrame(
        {
            "date": ordered.loc[suspect, "date"].reset_index(drop=True),
            "close_ratio": close_ratio[suspect].reset_index(drop=True),
            "adj_close_ratio": adj_ratio[suspect].reset_index(drop=True),
            "matched_split_ratio": matched[suspect].reset_index(drop=True),
        }
    )


def _match_split_ratio(ratio: float, tolerance: float) -> float | None:
    """Renvoie le ratio de split typique le plus proche si dans la tolérance."""
    if pd.isna(ratio):
        return None
    for candidate in _TYPICAL_SPLIT_RATIOS:
        if abs(ratio - candidate) / candidate <= tolerance:
            return candidate
    return None


@dataclass
class ValidationReport:
    """Résultat consolidé des contrôles de qualité pour un ticker."""

    ticker: str
    gaps: pd.DataFrame = field(default_factory=pd.DataFrame)
    outliers: pd.DataFrame = field(default_factory=pd.DataFrame)
    unadjusted_splits: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def has_issues(self) -> bool:
        """`True` si au moins une anomalie a été détectée."""
        return bool(len(self.gaps) or len(self.outliers) or len(self.unadjusted_splits))


def validate_ohlcv(
    df: pd.DataFrame,
    ticker: str,
    max_gap_calendar_days: int,
    outlier_return_threshold: float,
    split_ratio_tolerance: float,
) -> ValidationReport:
    """Exécute l'ensemble des contrôles de qualité sur une série OHLCV.

    Args:
        df: DataFrame OHLCV du ticker (colonnes `date`, `open`, `high`,
            `low`, `close`, `adj_close`, `volume`).
        ticker: Symbole du titre, reporté dans le résultat.
        max_gap_calendar_days: Seuil pour `detect_gaps`.
        outlier_return_threshold: Seuil pour `detect_outliers`.
        split_ratio_tolerance: Tolérance pour `detect_unadjusted_splits`.

    Returns:
        `ValidationReport` consolidant les trois contrôles.
    """
    return ValidationReport(
        ticker=ticker,
        gaps=detect_gaps(df, max_gap_calendar_days),
        outliers=detect_outliers(df, outlier_return_threshold),
        unadjusted_splits=detect_unadjusted_splits(df, split_ratio_tolerance),
    )
