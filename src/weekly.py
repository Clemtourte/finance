"""Commande hebdomadaire unique : ingestion + batch sur un même univers,
rapport Markdown daté, comparaison à l'exécution précédente.

Point d'entrée en ligne de commande :

    uv run python -m src.weekly

Tout est piloté par `config/weekly.yaml` (univers, date de coupure
FIGÉE, stratégie, répertoire de rapports, fichier d'état, espacement
minimal entre exécutions) ; voir `WeeklyConfig`.
`--data-config`/`--backtest-config`/`--known-anomalies` restent
surchargeables comme pour les autres CLI (défauts : `config/data.yaml`,
`config/backtest.yaml`, `config/known_anomalies.yaml`).

Refus de ré-exécution rapprochée : AVANT toute ingestion, si la
précédente exécution (`state_file`) date de moins de
`min_days_between_runs` jours, l'exécution est refusée (aucun
téléchargement, aucun calcul, aucun rapport ni état écrit) — garde-fou
contre le rattrapage d'un planificateur de tâches (ex. Task Scheduler
Windows) qui redéclenche une tâche manquée au démarrage suivant, ce qui
peut produire plusieurs exécutions le même jour. `--force` contourne ce
contrôle ; le rapport produit l'indique alors dans son pied de page.
Voir `run_weekly` et `_too_soon_message`.

Ce module n'implémente AUCUN calcul : il appelle exclusivement
`src.data.ingest.run_ingestion`, `src.data.baseline`,
`src.engine.batch.run_batch` et `src.reporting.weekly_report.
render_weekly_report`. Sa seule responsabilité propre est la
comparaison à l'exécution précédente (quel ticker a changé de verdict,
est apparu, ou a disparu de l'univers ; quelle anomalie hors ligne de
base est vue pour la première fois ou déjà en attente d'examen) et la
persistance de cet état.

Principe directeur du rapport (voir aussi `config/known_anomalies.yaml`
et `src.data.baseline`, même logique) : mettre en avant CE QUI A CHANGÉ,
jamais un tableau identique chaque semaine — sinon le rapport cesse
d'être lu, et le jour où quelque chose change réellement, personne ne le
voit. Une anomalie hors ligne de base mais pas encore examinée ne doit
pas non plus réclamer l'attention indéfiniment à chaque exécution : elle
n'est un "changement" (section "Changements", code 1) que la première
fois qu'elle apparaît, puis bascule en "En attente d'examen" (visible
dans le rapport, mais n'affecte plus le code de sortie) tant qu'elle
n'est ni expliquée (ajoutée à `config/known_anomalies.yaml`) ni
disparue.

Codes de sortie : `0` (rapport écrit, aucun changement), `1` (rapport
écrit, au moins un changement à lire : anomalie VUE POUR LA PREMIÈRE FOIS
ou changement de verdict/apparition/disparition — une anomalie déjà en
attente ne compte pas), `2` (échec technique — y compris une erreur
d'écriture du rapport ou de l'état : un rapport non écrit est un échec,
pas un silence).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

from src.data.baseline import AnomalyKey, filter_known, load_baseline, report_keys
from src.data.cache import ParquetCache
from src.data.config import DataConfig, load_data_config, load_universe
from src.data.ingest import run_ingestion
from src.data.provider import DataProvider
from src.data.validation import ValidationReport
from src.engine.batch import BatchResult, run_batch
from src.engine.config import BacktestConfig, load_backtest_config
from src.reporting.weekly_report import WeeklyReportContext, render_weekly_report
from src.strategies.registry import display_name, load_strategy

logger = logging.getLogger(__name__)

_DEFAULT_WEEKLY_CONFIG = "config/weekly.yaml"
_DEFAULT_DATA_CONFIG = "config/data.yaml"
_DEFAULT_BACKTEST_CONFIG = "config/backtest.yaml"
_DEFAULT_KNOWN_ANOMALIES = "config/known_anomalies.yaml"

_REQUIRED_WEEKLY_KEYS = ("universe_file", "split_date", "strategy", "reports_dir", "state_file")


# --- Configuration (config/weekly.yaml) -------------------------------------


@dataclass(frozen=True)
class WeeklyConfig:
    """Configuration de `make weekly` / `src.weekly`, issue de `config/weekly.yaml`.

    Attributes:
        universe_file: Univers ingéré puis backtesté par cette commande.
        split_date: Date de coupure in-sample/out-of-sample, FIGÉE
            délibérément (voir docstring de `config/weekly.yaml`) : elle
            n'est jamais recalculée à l'exécution, contrairement à
            `end_date: "today"` de `config/data.yaml`.
        strategy: Nom court de la stratégie évaluée (registre
            `src.strategies.registry`).
        reports_dir: Répertoire où chaque exécution écrit son rapport
            daté (`reports_dir/AAAA-MM-JJ.md`).
        state_file: Fichier où chaque exécution enregistre le verdict de
            chaque ticker, pour que la suivante détecte ce qui a changé.
        min_days_between_runs: Nombre minimal de jours devant séparer
            deux exécutions ; en dessous, `run_weekly` refuse de tourner
            (voir `_refusal_message` et docstring de module). Défaut `5`
            si absent de `config/weekly.yaml` (rétrocompatible, voir
            `load_weekly_config`).
    """

    universe_file: Path
    split_date: date
    strategy: str
    reports_dir: Path
    state_file: Path
    min_days_between_runs: int = 5


def load_weekly_config(path: str | Path) -> WeeklyConfig:
    """Charge `config/weekly.yaml`.

    Args:
        path: Chemin du fichier YAML de configuration.

    Returns:
        `WeeklyConfig` typée et résolue (chemins en `Path`, date parsée).

    Raises:
        ValueError: Si une clé obligatoire manque.
    """
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    missing = [key for key in _REQUIRED_WEEKLY_KEYS if key not in raw]
    if missing:
        raise ValueError(f"{config_path}: clé(s) manquante(s) {missing}")

    base_dir = config_path.parent.parent  # racine du projet, config/ est à la racine (comme load_data_config)
    split_date_raw = raw["split_date"]
    split_date = (
        split_date_raw if isinstance(split_date_raw, date) else date.fromisoformat(str(split_date_raw))
    )

    return WeeklyConfig(
        universe_file=base_dir / raw["universe_file"],
        split_date=split_date,
        strategy=raw["strategy"],
        reports_dir=base_dir / raw["reports_dir"],
        state_file=base_dir / raw["state_file"],
        min_days_between_runs=raw.get("min_days_between_runs", 5),
    )


# --- État persisté (verdict par ticker + anomalies en attente) --------------


@dataclass(frozen=True)
class TickerState:
    """État persisté d'un ticker après une exécution, pour comparaison à la suivante."""

    verdict: str
    run_date: date
    strategy_cagr_oos: float | None
    benchmark_cagr_oos: float | None


@dataclass(frozen=True)
class WeeklyState:
    """État complet chargé depuis `state_file` : verdicts + anomalies en attente.

    Attributes:
        tickers: `ticker -> TickerState` de la précédente exécution.
        anomalies: `AnomalyKey -> date de première apparition`, pour les
            anomalies hors ligne de base déjà vues lors d'une exécution
            précédente. Vide si `state_file` vient d'un ancien format qui
            ne les enregistrait pas encore (rétrocompatible, voir
            `load_weekly_state`) : elles seront alors simplement
            considérées comme vues pour la première fois à l'exécution
            courante, pas comme une erreur.
        last_run_date: Date de la précédente exécution, utilisée par
            `run_weekly` pour refuser une exécution trop rapprochée (voir
            `WeeklyConfig.min_days_between_runs`). `None` si `state_file`
            n'existe pas encore (première exécution), ou reconstruite
            depuis `tickers` si `state_file` vient d'un ancien format qui
            n'enregistrait pas encore cette date (rétrocompatible, voir
            `load_weekly_state`).
    """

    tickers: dict[str, TickerState]
    anomalies: dict[AnomalyKey, date]
    last_run_date: date | None


def _nan_to_none(value: float) -> float | None:
    return None if value != value else value  # NaN != NaN, sans dépendre de math/numpy ici


def load_weekly_state(path: str | Path) -> WeeklyState:
    """Charge l'état de la dernière exécution (verdicts + anomalies en attente).

    Args:
        path: Chemin du fichier d'état JSON (`state_file` de `WeeklyConfig`).

    Returns:
        `WeeklyState` vide (`tickers={}`, `anomalies={}`, `last_run_date=None`)
        si le fichier n'existe pas encore : ce n'est pas une erreur, c'est
        la première exécution. La clé `"anomalies"` est optionnelle dans
        le JSON (absente = fichier écrit par une version antérieure :
        rétrocompatible, voir docstring de `WeeklyState`) ; de même pour
        `"last_run_date"`, alors reconstruite depuis `tickers` (`None` si
        `tickers` est aussi vide).

    Raises:
        ValueError: Si le fichier existe mais est illisible (JSON mal
            formé) ou mal structuré (clé attendue manquante).
    """
    file_path = Path(path)
    if not file_path.exists():
        return WeeklyState(tickers={}, anomalies={}, last_run_date=None)

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{file_path}: JSON mal formé : {exc}") from exc

    try:
        tickers_raw = raw["tickers"]
        tickers = {
            ticker: TickerState(
                verdict=entry["verdict"],
                run_date=date.fromisoformat(entry["run_date"]),
                strategy_cagr_oos=entry["strategy_cagr_oos"],
                benchmark_cagr_oos=entry["benchmark_cagr_oos"],
            )
            for ticker, entry in tickers_raw.items()
        }
        anomalies = {
            AnomalyKey(ticker=entry["ticker"], kind=entry["kind"], date=date.fromisoformat(entry["date"])): (
                date.fromisoformat(entry["first_seen"])
            )
            for entry in raw.get("anomalies", [])  # absent = ancien format, voir WeeklyState
        }
        last_run_date_raw = raw.get("last_run_date")  # absent = ancien format, voir WeeklyState
        last_run_date = (
            date.fromisoformat(last_run_date_raw)
            if last_run_date_raw is not None
            else (next(iter(tickers.values())).run_date if tickers else None)
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{file_path}: structure inattendue ({exc})") from exc

    return WeeklyState(tickers=tickers, anomalies=anomalies, last_run_date=last_run_date)


def save_weekly_state(
    path: str | Path,
    results: list[BatchResult],
    run_date: date,
    anomaly_first_seen: dict[AnomalyKey, date],
) -> None:
    """Écrit l'état de l'exécution courante, pour la prochaine comparaison.

    Args:
        path: Chemin du fichier d'état JSON.
        results: Résultats du batch courant (une entrée par ticker).
        run_date: Date de l'exécution courante.
        anomaly_first_seen: `AnomalyKey -> date de première apparition` pour
            TOUTES les anomalies hors ligne de base actuellement actives
            (nouvelles de cette exécution incluses, avec `run_date` comme
            date de première apparition ; voir `_categorize_anomalies`).
            Une anomalie qui a disparu depuis (ajoutée à la ligne de base,
            ou plus détectée) n'y figure plus : elle disparaît donc aussi
            de l'état à cette écriture, pas seulement du rapport.
    """
    file_path = Path(path)
    tickers = {
        r.ticker: {
            "verdict": r.verdict,
            "run_date": run_date.isoformat(),
            "strategy_cagr_oos": _nan_to_none(r.strategy_cagr_oos),
            "benchmark_cagr_oos": _nan_to_none(r.benchmark_cagr_oos),
        }
        for r in results
    }
    anomalies = [
        {
            "ticker": key.ticker,
            "kind": key.kind,
            "date": key.date.isoformat(),
            "first_seen": first_seen.isoformat(),
        }
        for key, first_seen in sorted(anomaly_first_seen.items(), key=lambda kv: (kv[0].ticker, kv[0].date, kv[0].kind))
    ]
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        json.dumps(
            {"tickers": tickers, "anomalies": anomalies, "last_run_date": run_date.isoformat()},
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


# --- Comparaison à l'exécution précédente ------------------------------------


@dataclass(frozen=True)
class VerdictChange:
    """Un ticker dont le verdict a changé entre deux exécutions."""

    ticker: str
    name: str
    old_verdict: str
    new_verdict: str


@dataclass(frozen=True)
class WeeklyChanges:
    """Différence entre l'exécution courante et l'état de la précédente.

    `is_first_run=True` signifie qu'il n'existait aucun état antérieur à
    comparer : `verdict_changes`/`appeared`/`disappeared` sont alors
    toujours vides par construction (pas de faux "changements" au
    premier lancement — voir `_compare_verdicts`), et `previous_run_date`
    vaut `None`.
    """

    is_first_run: bool
    verdict_changes: list[VerdictChange]
    appeared: list[str]
    disappeared: list[str]
    previous_run_date: date | None

    @property
    def has_verdict_activity(self) -> bool:
        """`True` si au moins un changement de verdict, apparition ou disparition.

        Toujours `False` au premier run (rien à comparer, voir
        `is_first_run`).
        """
        return bool(self.verdict_changes or self.appeared or self.disappeared)


def _compare_verdicts(
    results: list[BatchResult], previous: dict[str, TickerState], is_first_run: bool
) -> WeeklyChanges:
    """Compare les verdicts courants à l'état de la précédente exécution.

    Un ticker `NON TESTABLE` qui le reste n'est pas un changement (même
    verdict des deux côtés) ; un ticker qui passe de testable à `NON
    TESTABLE`, ou l'inverse, EST un changement — les deux découlent
    naturellement de la simple comparaison de chaînes ci-dessous, aucun
    cas spécial n'est nécessaire.

    Args:
        results: Résultats du batch courant.
        previous: `WeeklyState.tickers` de la précédente exécution (vide
            si premier run).
        is_first_run: `True` si aucun fichier d'état n'existait avant
            cette exécution (voir docstring de `WeeklyChanges`).

    Returns:
        `WeeklyChanges` consolidée.
    """
    if is_first_run:
        return WeeklyChanges(
            is_first_run=True, verdict_changes=[], appeared=[], disappeared=[], previous_run_date=None
        )

    current_tickers = {r.ticker: r for r in results}
    verdict_changes = [
        VerdictChange(ticker=r.ticker, name=r.name, old_verdict=previous[r.ticker].verdict, new_verdict=r.verdict)
        for r in results
        if r.ticker in previous and previous[r.ticker].verdict != r.verdict
    ]
    appeared = sorted(ticker for ticker in current_tickers if ticker not in previous)
    disappeared = sorted(ticker for ticker in previous if ticker not in current_tickers)
    previous_run_date = next(iter(previous.values())).run_date if previous else None

    return WeeklyChanges(
        is_first_run=False,
        verdict_changes=verdict_changes,
        appeared=appeared,
        disappeared=disappeared,
        previous_run_date=previous_run_date,
    )


# --- Anomalies : nouvelles vs déjà en attente d'examen -----------------------


@dataclass(frozen=True)
class PendingAnomaly:
    """Anomalie hors ligne de base déjà vue lors d'une exécution précédente."""

    ticker: str
    kind: str
    date: date
    first_seen: date
    days_waiting: int


def _row_date(value: object) -> date:
    """Normalise une valeur de date de ligne d'anomalie (`date`, `Timestamp`...) en `date`."""
    return pd.Timestamp(value).date()


def _split_anomaly_rows(
    df: pd.DataFrame, ticker: str, kind: str, date_col: str, previously_seen: dict[AnomalyKey, date]
) -> tuple[pd.DataFrame, list[AnomalyKey]]:
    """Sépare les lignes d'un DataFrame d'anomalies (déjà hors ligne de base) entre
    nouvelles (absentes de `previously_seen`) et déjà vues (présentes).

    Returns:
        `(lignes nouvelles, clés déjà vues)`.
    """
    if df.empty:
        return df.copy(), []

    keys = [AnomalyKey(ticker, kind, _row_date(value)) for value in df[date_col]]
    is_pending = [key in previously_seen for key in keys]
    new_rows = df.loc[[not p for p in is_pending]].reset_index(drop=True)
    pending_keys = [key for key, p in zip(keys, is_pending) if p]
    return new_rows, pending_keys


def _categorize_anomalies(
    filtered_reports: dict[str, ValidationReport],
    previously_seen: dict[AnomalyKey, date],
    run_date: date,
) -> tuple[dict[str, ValidationReport], list[PendingAnomaly], dict[AnomalyKey, date]]:
    """Sépare les anomalies déjà hors ligne de base entre nouvelles et en attente.

    Une anomalie identifiée par `(ticker, kind, date)` (voir `src.data.
    baseline.AnomalyKey`) qui n'est ni dans la ligne de base ni dans
    `previously_seen` est vue pour la première fois à cette exécution :
    c'est un CHANGEMENT (déclenche le code 1). Si elle est déjà dans
    `previously_seen`, elle est simplement EN ATTENTE d'être expliquée
    (ajoutée à `config/known_anomalies.yaml`) : toujours visible dans le
    rapport, mais ne redéclenche plus le code 1 à chaque exécution — sinon
    un rapport hebdomadaire réclamerait indéfiniment l'attention pour la
    même anomalie non encore examinée, jusqu'à cesser d'être lu.

    Args:
        filtered_reports: Rapports déjà filtrés par la ligne de base
            (`src.data.baseline.filter_known`) : ne couvrent que les
            anomalies absentes de `config/known_anomalies.yaml`.
        previously_seen: `WeeklyState.anomalies` de la précédente
            exécution.
        run_date: Date de l'exécution courante (date de première
            apparition des anomalies nouvelles).

    Returns:
        `(new_reports, pending, first_seen_for_state)` :
        - `new_reports` : mêmes tickers que `filtered_reports`, réduits
          aux lignes absentes de `previously_seen`.
        - `pending` : les anomalies présentes dans `previously_seen`,
          avec leur date de première apparition et le nombre de jours
          d'attente jusqu'à `run_date`.
        - `first_seen_for_state` : `AnomalyKey -> date de première
          apparition` pour TOUTES les anomalies actuellement actives
          (nouvelles incluses, `run_date` comme première apparition) — à
          persister tel quel dans `state_file` (voir `save_weekly_state`).
          Une anomalie disparue de `filtered_reports` (ajoutée à la ligne
          de base entre-temps, ou plus détectée) n'y figure plus.
    """
    new_reports: dict[str, ValidationReport] = {}
    pending: list[PendingAnomaly] = []
    first_seen_for_state: dict[AnomalyKey, date] = {}

    for ticker, report in filtered_reports.items():
        gaps_new, gaps_pending = _split_anomaly_rows(report.gaps, ticker, "gap", "gap_start", previously_seen)
        outliers_new, outliers_pending = _split_anomaly_rows(
            report.outliers, ticker, "outlier", "date", previously_seen
        )
        splits_new, splits_pending = _split_anomaly_rows(
            report.unadjusted_splits, ticker, "split", "date", previously_seen
        )

        new_report = ValidationReport(
            ticker=ticker, gaps=gaps_new, outliers=outliers_new, unadjusted_splits=splits_new
        )
        if new_report.has_issues:
            new_reports[ticker] = new_report
        for key in report_keys(new_report):
            first_seen_for_state[key] = run_date

        for key in (*gaps_pending, *outliers_pending, *splits_pending):
            first_seen = previously_seen[key]
            first_seen_for_state[key] = first_seen
            pending.append(
                PendingAnomaly(
                    ticker=key.ticker,
                    kind=key.kind,
                    date=key.date,
                    first_seen=first_seen,
                    days_waiting=(run_date - first_seen).days,
                )
            )

    return new_reports, pending, first_seen_for_state


# --- Orchestration ------------------------------------------------------------


@dataclass(frozen=True)
class WeeklyResult:
    """Résultat d'une exécution de `run_weekly`.

    `skipped=True` signifie que l'exécution a été refusée avant toute
    ingestion (voir `_too_soon_message`) : aucun rapport ni état n'a été
    écrit, `report_path`/`report_text`/`changes` valent `None` et les
    autres champs de calcul restent vides — seuls `exit_code` (toujours
    `0`) et `skip_message` (le message affiché à l'utilisateur) sont
    significatifs.
    """

    report_path: Path | None
    report_text: str | None
    exit_code: int
    changes: WeeklyChanges | None
    new_anomaly_reports: dict[str, ValidationReport]
    pending_anomalies: list[PendingAnomaly]
    batch_results: list[BatchResult]
    elapsed_seconds: float
    skipped: bool = False
    skip_message: str = ""


def _too_soon_message(previous_run_date: date, days_elapsed: int, min_days_between_runs: int) -> str:
    """Message expliquant le refus d'une exécution trop rapprochée de la précédente."""
    next_allowed = previous_run_date + timedelta(days=min_days_between_runs)
    day_word = "jour" if days_elapsed == 1 else "jours"
    return (
        f"Exécution refusée : la précédente date du {previous_run_date.isoformat()} "
        f"({days_elapsed} {day_word} plus tôt), en dessous du minimum de "
        f"{min_days_between_runs} jours entre deux exécutions (min_days_between_runs, "
        "config/weekly.yaml). Rien n'a été téléchargé ni recalculé, aucun rapport ni "
        f"état n'a été écrit. Prochaine exécution acceptée à partir du "
        f"{next_allowed.isoformat()} (ou relancer avec --force pour contourner ce contrôle)."
    )


def run_weekly(
    weekly_config: WeeklyConfig,
    data_config: DataConfig,
    backtest_config: BacktestConfig,
    known_anomalies_path: str | Path,
    provider: DataProvider | None = None,
    *,
    run_date: date | None = None,
    force: bool = False,
) -> WeeklyResult:
    """Orchestre ingestion + batch + comparaison + rapport, pour tout l'univers configuré.

    N'implémente aucun calcul : délègue à `src.data.ingest.run_ingestion`,
    `src.data.baseline` (ligne de base des anomalies), `src.engine.batch.
    run_batch` et `src.reporting.weekly_report.render_weekly_report`.

    AVANT toute ingestion (donc avant tout accès réseau), refuse de
    tourner si `state_file` indique une exécution précédente vieille de
    moins de `weekly_config.min_days_between_runs` jours — garde-fou
    contre le rattrapage du planificateur de tâches (plusieurs démarrages
    le même jour ne doivent pas déclencher plusieurs exécutions qui se
    réécrivent l'une l'autre). Voir `_too_soon_message` et `force`.

    Args:
        weekly_config: Univers, date de coupure, stratégie, chemins de
            rapport/état (`config/weekly.yaml`).
        data_config: Configuration de la couche données (`config/data.yaml`).
        backtest_config: Configuration du moteur de backtest
            (`config/backtest.yaml`).
        known_anomalies_path: Ligne de base des anomalies déjà examinées
            (`config/known_anomalies.yaml`).
        provider: Provider de données à utiliser pour l'ingestion ;
            défaut `YFinanceProvider` (voir `run_ingestion`). Injecter un
            double de test pour éviter les appels réseau.
        run_date: Date de l'exécution, utilisée pour le nom du fichier de
            rapport et l'état écrit. Défaut `date.today()`.
        force: Contourne entièrement le contrôle d'espacement minimal
            (utile pour un rattrapage manuel volontaire). Le rapport
            produit l'indique alors dans son pied de page.

    Returns:
        `WeeklyResult` : chemin et contenu du rapport écrit, code de
        sortie composé, et détail de la comparaison à l'exécution
        précédente. Voir docstring de `WeeklyResult` si `skipped=True`.
    """
    run_date = run_date or date.today()
    started = time.monotonic()

    state_existed = Path(weekly_config.state_file).exists()
    previous_state = load_weekly_state(weekly_config.state_file)

    if not force and previous_state.last_run_date is not None:
        days_elapsed = (run_date - previous_state.last_run_date).days
        if days_elapsed < weekly_config.min_days_between_runs:
            message = _too_soon_message(
                previous_state.last_run_date, days_elapsed, weekly_config.min_days_between_runs
            )
            return WeeklyResult(
                report_path=None,
                report_text=None,
                exit_code=0,
                changes=None,
                new_anomaly_reports={},
                pending_anomalies=[],
                batch_results=[],
                elapsed_seconds=time.monotonic() - started,
                skipped=True,
                skip_message=message,
            )

    universe = load_universe(weekly_config.universe_file)

    cache = ParquetCache(data_config.cache_dir)
    bars_before = {info.ticker: len(cache.read(info.ticker)) for info in universe}
    reports = run_ingestion(data_config, universe, provider=provider)
    bars_added = {info.ticker: len(cache.read(info.ticker)) - bars_before[info.ticker] for info in universe}

    baseline = load_baseline(known_anomalies_path)
    filtered_reports, n_known_discarded = filter_known(reports, baseline)

    strategy = load_strategy(weekly_config.strategy)
    batch_results = run_batch(universe, strategy, data_config, backtest_config, weekly_config.split_date)

    changes = _compare_verdicts(batch_results, previous_state.tickers, is_first_run=not state_existed)
    new_anomaly_reports, pending_anomalies, anomaly_first_seen = _categorize_anomalies(
        filtered_reports, previous_state.anomalies, run_date
    )

    elapsed_seconds = time.monotonic() - started

    ctx = WeeklyReportContext(
        run_date=run_date,
        changes=changes,
        new_anomaly_reports=new_anomaly_reports,
        pending_anomalies=pending_anomalies,
        n_tickers=len(universe),
        bars_added=bars_added,
        n_known_discarded=n_known_discarded,
        batch_results=batch_results,
        elapsed_seconds=elapsed_seconds,
        universe_file=weekly_config.universe_file,
        split_date=weekly_config.split_date,
        strategy_name=display_name(weekly_config.strategy),
        data_start=data_config.start_date,
        data_end=data_config.end_date,
        forced=force,
    )
    report_text = render_weekly_report(ctx)

    report_path = Path(weekly_config.reports_dir) / f"{run_date.isoformat()}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    save_weekly_state(weekly_config.state_file, batch_results, run_date, anomaly_first_seen)

    has_new_anomalies = bool(new_anomaly_reports)
    exit_code = 1 if (has_new_anomalies or changes.has_verdict_activity) else 0

    return WeeklyResult(
        report_path=report_path,
        report_text=report_text,
        exit_code=exit_code,
        changes=changes,
        new_anomaly_reports=new_anomaly_reports,
        pending_anomalies=pending_anomalies,
        batch_results=batch_results,
        elapsed_seconds=elapsed_seconds,
    )


def main() -> None:
    """CLI : lance la commande hebdomadaire complète (ingestion + batch + rapport).

    Code de sortie : `0` (rapport écrit, aucun changement — ou exécution
    refusée, voir `--force`), `1` (rapport écrit, au moins un changement à
    lire), `2` (échec technique — y compris une erreur d'écriture du
    rapport ou de l'état). Voir le docstring de module.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weekly-config", default=_DEFAULT_WEEKLY_CONFIG, help="Chemin de config/weekly.yaml")
    parser.add_argument("--data-config", default=_DEFAULT_DATA_CONFIG)
    parser.add_argument("--backtest-config", default=_DEFAULT_BACKTEST_CONFIG)
    parser.add_argument(
        "--known-anomalies", default=_DEFAULT_KNOWN_ANOMALIES,
        help="Ligne de base des anomalies déjà examinées et acceptées",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Contourne le contrôle d'espacement minimal entre deux exécutions (min_days_between_runs)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        weekly_config = load_weekly_config(args.weekly_config)
        data_config = load_data_config(args.data_config)
        backtest_config = load_backtest_config(args.backtest_config)
        result = run_weekly(
            weekly_config, data_config, backtest_config, args.known_anomalies, force=args.force
        )
    except Exception:
        logger.exception("Échec de l'exécution hebdomadaire")
        sys.exit(2)

    if result.skipped:
        print(result.skip_message)
        sys.exit(result.exit_code)

    n_survives = sum(1 for r in result.batch_results if r.verdict == "SURVIT")
    n_rejected = sum(1 for r in result.batch_results if r.verdict == "REJETÉ")
    print(
        f"Hebdo {weekly_config.split_date.isoformat()} | {len(result.batch_results)} titre(s) "
        f"| SURVIT: {n_survives} | REJETÉ: {n_rejected}"
    )
    if result.exit_code == 1:
        print('Changements à lire : voir la section "Changements" du rapport.')
    print(f"Rapport : {result.report_path}")

    sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
