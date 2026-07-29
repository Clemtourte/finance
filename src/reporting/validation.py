"""Rendu texte des rapports de validation (src.data.validation) : détail
par ticker et synthèse multi-tickers, prêts à être affichés en console."""

from __future__ import annotations

import pandas as pd

from src.data.validation import ValidationReport


def _iso(value: object) -> str:
    """Formate une date (ou tout objet convertible) au format ISO YYYY-MM-DD."""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _format_gaps(gaps: pd.DataFrame) -> list[str]:
    lines = [f"  Trous ({len(gaps)}) :"]
    for _, row in gaps.sort_values("gap_start").iterrows():
        lines.append(
            f"    {_iso(row['gap_start'])} -> {_iso(row['gap_end'])} "
            f"({int(row['calendar_days'])} jours calendaires)"
        )
    return lines


def _format_outliers(outliers: pd.DataFrame) -> list[str]:
    lines = [f"  Valeurs aberrantes ({len(outliers)}) :"]
    for _, row in outliers.sort_values("date").iterrows():
        lines.append(
            f"    {_iso(row['date'])} : adj_close={row['adj_close']:.4f}, "
            f"rendement journalier {row['daily_return']:+.1%}"
        )
    return lines


def _format_unadjusted_splits(splits: pd.DataFrame) -> list[str]:
    lines = [f"  Splits suspects ({len(splits)}) :"]
    for _, row in splits.sort_values("date").iterrows():
        lines.append(
            f"    {_iso(row['date'])} : close_ratio={row['close_ratio']:.4f}, "
            f"adj_close_ratio={row['adj_close_ratio']:.4f}, "
            f"ratio de split reconnu={row['matched_split_ratio']:.4f}"
        )
    return lines


def format_validation_report(report: ValidationReport) -> str:
    """Rendu détaillé du rapport de validation d'un ticker.

    Args:
        report: Rapport produit par `src.data.validation.validate_ohlcv`.

    Returns:
        Texte multi-lignes détaillant chaque anomalie (trous, valeurs
        aberrantes, splits suspects), triées par date croissante. Une
        ligne unique explicite si aucune anomalie n'a été détectée
        (jamais de chaîne vide).
    """
    if not report.has_issues:
        return f"{report.ticker}: aucune anomalie détectée"

    lines = [f"{report.ticker} :"]
    if not report.gaps.empty:
        lines.extend(_format_gaps(report.gaps))
    if not report.outliers.empty:
        lines.extend(_format_outliers(report.outliers))
    if not report.unadjusted_splits.empty:
        lines.extend(_format_unadjusted_splits(report.unadjusted_splits))
    return "\n".join(lines)


def format_validation_summary(reports: dict[str, ValidationReport]) -> str:
    """Synthèse multi-tickers des rapports de validation.

    Args:
        reports: `ticker -> ValidationReport`, dans l'ordre d'affichage
            souhaité (ex. sortie de `src.data.ingest.run_ingestion`).

    Returns:
        Texte multi-lignes : une ligne par ticker sans anomalie, le
        détail via `format_validation_report` pour les autres, terminé
        par un total (nombre de tickers, nombre d'anomalies par
        catégorie). Jamais de chaîne vide, même si `reports` est vide.
    """
    blocks = [format_validation_report(report) for report in reports.values()]

    total_gaps = sum(len(r.gaps) for r in reports.values())
    total_outliers = sum(len(r.outliers) for r in reports.values())
    total_splits = sum(len(r.unadjusted_splits) for r in reports.values())
    blocks.append(
        f"Total : {len(reports)} ticker(s) | {total_gaps} trou(s), "
        f"{total_outliers} valeur(s) aberrante(s), {total_splits} split(s) suspect(s)"
    )
    return "\n\n".join(blocks)
