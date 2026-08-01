"""Rendu Markdown du rapport hebdomadaire (`src.weekly`).

Structure imposée : titre daté, puis "Changements" en tête — la SEULE
section lue une semaine normale, qui liste uniquement ce qui a changé
depuis l'exécution précédente (anomalies vues pour la première fois,
verdicts qui basculent, tickers apparus/disparus) — puis "Données", "En
attente d'examen" (anomalies déjà signalées mais pas encore expliquées,
qui ne redéclenchent plus le code de sortie 1), "Résultats" et un pied
de page, qui ne sont que du matériel de référence.
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
    from src.weekly import PendingAnomaly, WeeklyChanges


@dataclass(frozen=True)
class WeeklyReportContext:
    """Tout ce dont `render_weekly_report` a besoin, assemblé par `src.weekly.run_weekly`."""

    run_date: date
    changes: "WeeklyChanges"
    new_anomaly_reports: dict[str, ValidationReport]
    pending_anomalies: list["PendingAnomaly"]
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
    forced: bool = False


def _since(previous_run_date: date | None) -> str:
    if previous_run_date is not None:
        return f"depuis le {previous_run_date.isoformat()}"
    return "depuis la dernière exécution"


def _render_changes_section(ctx: WeeklyReportContext) -> str:
    if ctx.changes.is_first_run and not ctx.new_anomaly_reports:
        return (
            "Première exécution : aucun état antérieur pour comparer les verdicts. "
            "L'état de référence pour les prochaines comparaisons vient d'être écrit "
            "— le prochain rapport pourra dire ce qui a changé."
        )

    parts: list[str] = []
    if ctx.changes.is_first_run:
        parts.append(
            "Première exécution : aucun état antérieur pour comparer les verdicts "
            "(l'état de référence vient d'être écrit)."
        )

    since = _since(ctx.changes.previous_run_date)

    # Ces deux sujets se prononcent TOUJOURS explicitement (une ligne de
    # confirmation s'il n'y a rien de neuf) : un rapport qui reste
    # silencieux sur un sujet ne se distingue pas d'un rapport qui a
    # oublié de le vérifier. Seulement au premier run (pas de date de
    # référence à citer) ces confirmations n'ont pas de sens et sont omises.
    if ctx.new_anomaly_reports:
        lines = ["**Anomalies nouvelles** (absentes de la ligne de base) :", "", "```"]
        lines.extend(format_validation_report(report, filtered=True) for report in ctx.new_anomaly_reports.values())
        lines.append("```")
        parts.append("\n".join(lines))
    elif not ctx.changes.is_first_run:
        parts.append(f"Aucune anomalie nouvelle {since}.")

    if ctx.changes.verdict_changes:
        lines = ["**Changements de verdict :**"]
        lines.extend(
            f"- `{c.ticker}` ({c.name}) : {c.old_verdict} → {c.new_verdict}"
            for c in ctx.changes.verdict_changes
        )
        parts.append("\n".join(lines))
    elif not ctx.changes.is_first_run:
        parts.append(f"Aucun changement de verdict {since}.")

    if ctx.changes.appeared:
        lines = ["**Nouveaux tickers dans l'univers :**"]
        lines.extend(f"- `{ticker}`" for ticker in ctx.changes.appeared)
        parts.append("\n".join(lines))
    if ctx.changes.disappeared:
        lines = ["**Tickers disparus de l'univers :**"]
        lines.extend(f"- `{ticker}`" for ticker in ctx.changes.disappeared)
        parts.append("\n".join(lines))

    if ctx.pending_anomalies:
        n = len(ctx.pending_anomalies)
        wording = "1 anomalie reste" if n == 1 else f"{n} anomalies restent"
        parts.append(f"{wording} en attente d'examen, voir plus bas.")

    return "\n\n".join(parts)


def _render_data_section(ctx: WeeklyReportContext) -> str:
    added = {ticker: n for ticker, n in ctx.bars_added.items() if n > 0}
    total_added = sum(ctx.bars_added.values())
    if total_added == 0:
        added_line = "Séances ajoutées : aucune (tous les titres étaient déjà à jour)"
    else:
        detail = ", ".join(f"{ticker}: +{n}" for ticker, n in sorted(added.items()))
        added_line = f"Séances ajoutées : {total_added} au total ({detail})"
    return (
        f"- Titres : {ctx.n_tickers}\n"
        f"- {added_line}\n"
        f"- Anomalies connues écartées (ligne de base) : {ctx.n_known_discarded}"
    )


def _render_pending_section(ctx: WeeklyReportContext) -> str:
    if not ctx.pending_anomalies:
        return "Aucune anomalie en attente d'examen."
    lines = []
    for p in sorted(ctx.pending_anomalies, key=lambda p: (p.ticker, p.date, p.kind)):
        day_word = "jour" if p.days_waiting == 1 else "jours"
        lines.append(
            f"- `{p.ticker}` ({p.kind}, {p.date.isoformat()}) : vue pour la première fois "
            f"le {p.first_seen.isoformat()}, en attente depuis {p.days_waiting} {day_word} — "
            "à expliquer dans config/known_anomalies.yaml ou à laisser réapparaître si ce n'en est pas une."
        )
    return "\n".join(lines)


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
    lines = [
        f"- Durée d'exécution : {ctx.elapsed_seconds:.1f}s",
        f"- Univers : {ctx.universe_file}",
        f"- Date de coupure (in-sample/out-of-sample) : {ctx.split_date.isoformat()}",
        f"- Stratégie : {ctx.strategy_name}",
        f"- Données utilisées : {ctx.data_start.isoformat()} → {ctx.data_end.isoformat()}",
    ]
    if ctx.forced:
        lines.append(
            "- Exécution FORCÉE (--force) : contrôle d'espacement minimal entre "
            "exécutions contourné, voir min_days_between_runs (config/weekly.yaml)"
        )
    return "\n".join(lines)


def render_weekly_report(ctx: WeeklyReportContext) -> str:
    """Rend le rapport hebdomadaire Markdown complet.

    Args:
        ctx: Contexte assemblé par `src.weekly.run_weekly`.

    Returns:
        Texte Markdown complet, prêt à être écrit dans
        `reports_dir/AAAA-MM-JJ.md`. Ordre des sections imposé : titre
        daté, "Changements" (seule section lue une semaine normale),
        "Données", "En attente d'examen", "Résultats", pied de page.
    """
    return (
        f"# Rapport hebdomadaire — {ctx.run_date.isoformat()}\n\n"
        f"## Changements\n\n{_render_changes_section(ctx)}\n\n"
        f"## Données\n\n{_render_data_section(ctx)}\n\n"
        f"## En attente d'examen\n\n{_render_pending_section(ctx)}\n\n"
        f"## Résultats\n\n{_render_results_section(ctx)}\n\n"
        f"## Pied de page\n\n{_render_footer_section(ctx)}\n"
    )
