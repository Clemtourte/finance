"""Commande hebdomadaire unique : ingestion + batch sur un même univers,
rapport Markdown daté, comparaison à l'exécution précédente.

Point d'entrée en ligne de commande :

    uv run python -m src.weekly

Tout est piloté par `config/weekly.yaml` (univers, date de coupure
FIGÉE, stratégie, répertoire de rapports, fichier d'état) ; voir
`WeeklyConfig`. `--data-config`/`--backtest-config`/`--known-anomalies`
restent surchargeables comme pour les autres CLI (défauts :
`config/data.yaml`, `config/backtest.yaml`, `config/known_anomalies.yaml`).

Ce module n'implémente AUCUN calcul : il appelle exclusivement
`src.data.ingest.run_ingestion`, `src.data.baseline`,
`src.engine.batch.run_batch` et `src.reporting.weekly_report.
render_weekly_report`. Sa seule responsabilité propre est la
comparaison à l'exécution précédente (quel ticker a changé de verdict,
est apparu, ou a disparu de l'univers) et la persistance de cet état.

Principe directeur du rapport (voir aussi `config/known_anomalies.yaml`
et `src.data.baseline`, même logique) : mettre en avant CE QUI A CHANGÉ,
jamais un tableau identique chaque semaine — sinon le rapport cesse
d'être lu, et le jour où quelque chose change réellement, personne ne le
voit.

Codes de sortie : `0` (rapport écrit, aucun changement), `1` (rapport
écrit, au moins un changement à lire : anomalie nouvelle ou changement
de verdict/apparition/disparition), `2` (échec technique — y compris une
erreur d'écriture du rapport ou de l'état : un rapport non écrit est un
échec, pas un silence).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from src.data.baseline import filter_known, load_baseline
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
    """

    universe_file: Path
    split_date: date
    strategy: str
    reports_dir: Path
    state_file: Path


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
    )


# --- État persisté (verdict par ticker, pour comparaison au run suivant) ----


@dataclass(frozen=True)
class TickerState:
    """État persisté d'un ticker après une exécution, pour comparaison à la suivante."""

    verdict: str
    run_date: date
    strategy_cagr_oos: float | None
    benchmark_cagr_oos: float | None


def _nan_to_none(value: float) -> float | None:
    return None if value != value else value  # NaN != NaN, sans dépendre de math/numpy ici


def load_weekly_state(path: str | Path) -> dict[str, TickerState]:
    """Charge l'état de la dernière exécution (verdict par ticker).

    Args:
        path: Chemin du fichier d'état JSON (`state_file` de `WeeklyConfig`).

    Returns:
        Dictionnaire `ticker -> TickerState`. Dictionnaire vide si le
        fichier n'existe pas encore : ce n'est pas une erreur, c'est la
        première exécution.

    Raises:
        ValueError: Si le fichier existe mais est illisible (JSON mal
            formé) ou mal structuré (clé attendue manquante).
    """
    file_path = Path(path)
    if not file_path.exists():
        return {}

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{file_path}: JSON mal formé : {exc}") from exc

    try:
        tickers_raw = raw["tickers"]
        return {
            ticker: TickerState(
                verdict=entry["verdict"],
                run_date=date.fromisoformat(entry["run_date"]),
                strategy_cagr_oos=entry["strategy_cagr_oos"],
                benchmark_cagr_oos=entry["benchmark_cagr_oos"],
            )
            for ticker, entry in tickers_raw.items()
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{file_path}: structure inattendue ({exc})") from exc


def save_weekly_state(path: str | Path, results: list[BatchResult], run_date: date) -> None:
    """Écrit l'état de l'exécution courante (verdict par ticker), pour la prochaine comparaison.

    Args:
        path: Chemin du fichier d'état JSON.
        results: Résultats du batch courant (une entrée par ticker).
        run_date: Date de l'exécution courante.
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
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        json.dumps({"tickers": tickers}, indent=2, ensure_ascii=False, sort_keys=True),
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
        previous: État chargé par `load_weekly_state` (vide si premier run).
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


# --- Orchestration ------------------------------------------------------------


@dataclass(frozen=True)
class WeeklyResult:
    """Résultat d'une exécution de `run_weekly`."""

    report_path: Path
    report_text: str
    exit_code: int
    changes: WeeklyChanges
    new_anomaly_reports: dict[str, ValidationReport]
    batch_results: list[BatchResult]
    elapsed_seconds: float


def run_weekly(
    weekly_config: WeeklyConfig,
    data_config: DataConfig,
    backtest_config: BacktestConfig,
    known_anomalies_path: str | Path,
    provider: DataProvider | None = None,
    *,
    run_date: date | None = None,
) -> WeeklyResult:
    """Orchestre ingestion + batch + comparaison + rapport, pour tout l'univers configuré.

    N'implémente aucun calcul : délègue à `src.data.ingest.run_ingestion`,
    `src.data.baseline` (ligne de base des anomalies), `src.engine.batch.
    run_batch` et `src.reporting.weekly_report.render_weekly_report`.

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

    Returns:
        `WeeklyResult` : chemin et contenu du rapport écrit, code de
        sortie composé, et détail de la comparaison à l'exécution
        précédente.
    """
    run_date = run_date or date.today()
    started = time.monotonic()

    universe = load_universe(weekly_config.universe_file)

    cache = ParquetCache(data_config.cache_dir)
    bars_before = {info.ticker: len(cache.read(info.ticker)) for info in universe}
    reports = run_ingestion(data_config, universe, provider=provider)
    bars_added = {info.ticker: len(cache.read(info.ticker)) - bars_before[info.ticker] for info in universe}

    baseline = load_baseline(known_anomalies_path)
    filtered_reports, n_known_discarded = filter_known(reports, baseline)
    new_anomaly_reports = {ticker: r for ticker, r in filtered_reports.items() if r.has_issues}

    strategy = load_strategy(weekly_config.strategy)
    batch_results = run_batch(universe, strategy, data_config, backtest_config, weekly_config.split_date)

    state_existed = Path(weekly_config.state_file).exists()
    previous_state = load_weekly_state(weekly_config.state_file)
    changes = _compare_verdicts(batch_results, previous_state, is_first_run=not state_existed)

    elapsed_seconds = time.monotonic() - started

    ctx = WeeklyReportContext(
        run_date=run_date,
        changes=changes,
        new_anomaly_reports=new_anomaly_reports,
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
    )
    report_text = render_weekly_report(ctx)

    report_path = Path(weekly_config.reports_dir) / f"{run_date.isoformat()}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    save_weekly_state(weekly_config.state_file, batch_results, run_date)

    has_new_anomalies = bool(new_anomaly_reports)
    exit_code = 1 if (has_new_anomalies or changes.has_verdict_activity) else 0

    return WeeklyResult(
        report_path=report_path,
        report_text=report_text,
        exit_code=exit_code,
        changes=changes,
        new_anomaly_reports=new_anomaly_reports,
        batch_results=batch_results,
        elapsed_seconds=elapsed_seconds,
    )


def main() -> None:
    """CLI : lance la commande hebdomadaire complète (ingestion + batch + rapport).

    Code de sortie : `0` (rapport écrit, aucun changement), `1` (rapport
    écrit, au moins un changement à lire), `2` (échec technique — y
    compris une erreur d'écriture du rapport ou de l'état). Voir le
    docstring de module.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weekly-config", default=_DEFAULT_WEEKLY_CONFIG, help="Chemin de config/weekly.yaml")
    parser.add_argument("--data-config", default=_DEFAULT_DATA_CONFIG)
    parser.add_argument("--backtest-config", default=_DEFAULT_BACKTEST_CONFIG)
    parser.add_argument(
        "--known-anomalies", default=_DEFAULT_KNOWN_ANOMALIES,
        help="Ligne de base des anomalies déjà examinées et acceptées",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        weekly_config = load_weekly_config(args.weekly_config)
        data_config = load_data_config(args.data_config)
        backtest_config = load_backtest_config(args.backtest_config)
        result = run_weekly(weekly_config, data_config, backtest_config, args.known_anomalies)
    except Exception:
        logger.exception("Échec de l'exécution hebdomadaire")
        sys.exit(2)

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
