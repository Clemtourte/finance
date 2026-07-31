"""Rendu Markdown du rapport hebdomadaire (`src.weekly`).

Structure imposée : titre daté, puis "Changements" en tête — la SEULE
section lue une semaine normale, qui liste uniquement ce qui a changé
depuis l'exécution précédente (anomalies nouvelles, verdicts qui
basculent, tickers apparus/disparus) — puis "Données", "Résultats" et un
pied de page, qui ne sont que du matériel de référence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from src.data.validation import ValidationReport
from src.reporting.table import format_batch_table
from src.reporting.validation import format_validation_report

if TYPE_CHECKING:
    # Import différé (type-checking uniquement) pour éviter un cycle avec
    # src.weekly, qui importe ce module pour produire son rapport.
    from src.engine.batch import BatchResult
    from src.weekly import WeeklyChanges


@dataclass(frozen=True)
class WeeklyReportContext:
    """Tout ce dont `render_weekly_report` a besoin, assemblé par `src.weekly.run_weekly`."""

    run_date: date
    changes: "WeeklyChanges"
    new_anomaly_reports: dict[str, ValidationReport]
    n_tickers: int
    bars_added: dict[str, int]
    n_known_discarded: int
    batch_results: list["BatchResult"]
    elapsed_seconds: float
    universe_file: Path
    split_date: date
    strategy_name: str
    data_start: date
    data_end: date


def _render_changes_section(ctx: WeeklyReportContext) -> str:
    if ctx.changes.is_first_run and not ctx.new_anomaly_reports:
        return (
            "Première exécution : aucun état antérieur pour comparer les verdicts. "
            "L'état de référence pour les prochaines comparaisons vient d'être écrit "
            "— le prochain rapport pourra dire ce qui a changé."
        )

    if not ctx.new_anomaly_reports and not ctx.changes.has_verdict_activity and not ctx.changes.is_first_run:
        if ctx.changes.previous_run_date is not None:
            return f"Rien de nouveau depuis le {ctx.changes.previous_run_date.isoformat()}."
        return "Rien de nouveau depuis la dernière exécution."

    parts: list[str] = []
    if ctx.changes.is_first_run:
        parts.append(
            "Première exécution : aucun état antérieur pour comparer les verdicts "
            "(l'état de référence vient d'être écrit)."
        )
    if ctx.new_anomaly_reports:
        lines = ["**Anomalies nouvelles** (absentes de la ligne de base) :", "", "```"]
        lines.extend(format_validation_report(report, filtered=True) for report in ctx.new_anomaly_reports.values())
        lines.append("```")
        parts.append("\n".join(lines))
    if ctx.changes.verdict_changes:
        lines = ["**Changements de verdict :**"]
        lines.extend(
            f"- `{c.ticker}` ({c.name}) : {c.old_verdict} → {c.new_verdict}"
            for c in ctx.changes.verdict_changes
        )
        parts.append("\n".join(lines))
    if ctx.changes.appeared:
        lines = ["**Nouveaux tickers dans l'univers :**"]
        lines.extend(f"- `{ticker}`" for ticker in ctx.changes.appeared)
        parts.append("\n".join(lines))
    if ctx.changes.disappeared:
        lines = ["**Tickers disparus de l'univers :**"]
        lines.extend(f"- `{ticker}`" for ticker in ctx.changes.disappeared)
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _render_data_section(ctx: WeeklyReportContext) -> str:
    added = {ticker: n for ticker, n in ctx.bars_added.items() if n > 0}
    if added:
        added_desc = ", ".join(f"{ticker}: +{n}" for ticker, n in sorted(added.items()))
    else:
        added_desc = "aucune (tous les titres étaient déjà à jour)"
    total_added = sum(ctx.bars_added.values())
    return (
        f"- Titres : {ctx.n_tickers}\n"
        f"- Séances ajoutées : {total_added} au total ({added_desc})\n"
        f"- Anomalies connues écartées (ligne de base) : {ctx.n_known_discarded}"
    )


def _render_results_section(ctx: WeeklyReportContext) -> str:
    n_survives = sum(1 for r in ctx.batch_results if r.verdict == "SURVIT")
    n_rejected = sum(1 for r in ctx.batch_results if r.verdict == "REJETÉ")
    n_not_testable = sum(1 for r in ctx.batch_results if r.verdict == "NON TESTABLE")
    n_errors = sum(1 for r in ctx.batch_results if r.verdict == "ERREUR")
    summary = (
        f"SURVIT: {n_survives} | REJETÉ: {n_rejected} | NON TESTABLE: {n_not_testable} "
        f"| ERREUR: {n_errors}"
    )
    table = format_batch_table(ctx.batch_results)
    return f"{summary}\n\nTableau complet (référence) :\n\n```\n{table}\n```"


def _render_footer_section(ctx: WeeklyReportContext) -> str:
    return (
        f"- Durée d'exécution : {ctx.elapsed_seconds:.1f}s\n"
        f"- Univers : {ctx.universe_file}\n"
        f"- Date de coupure (in-sample/out-of-sample) : {ctx.split_date.isoformat()}\n"
        f"- Stratégie : {ctx.strategy_name}\n"
        f"- Données utilisées : {ctx.data_start.isoformat()} → {ctx.data_end.isoformat()}"
    )


def render_weekly_report(ctx: WeeklyReportContext) -> str:
    """Rend le rapport hebdomadaire Markdown complet.

    Args:
        ctx: Contexte assemblé par `src.weekly.run_weekly`.

    Returns:
        Texte Markdown complet, prêt à être écrit dans
        `reports_dir/AAAA-MM-JJ.md`. Ordre des sections imposé : titre
        daté, "Changements" (seule section lue une semaine normale),
        "Données", "Résultats", pied de page.
    """
    return (
        f"# Rapport hebdomadaire — {ctx.run_date.isoformat()}\n\n"
        f"## Changements\n\n{_render_changes_section(ctx)}\n\n"
        f"## Données\n\n{_render_data_section(ctx)}\n\n"
        f"## Résultats\n\n{_render_results_section(ctx)}\n\n"
        f"## Pied de page\n\n{_render_footer_section(ctx)}\n"
    )
