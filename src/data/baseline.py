"""Ligne de base des anomalies de validation déjà examinées et acceptées.

Une anomalie de `src.data.validation` (trou, valeur aberrante, split
suspect) est identifiée par le triplet `(ticker, kind, date)` — jamais par
ses valeurs (`adj_close`, rendement, ratios), qui peuvent changer d'une
ingestion à l'autre sans que l'anomalie elle-même soit nouvelle (ex. un
détachement de dividende recalcule tout l'historique `adj_close`
antérieur). La ligne de base ne masque que les dates exactes qu'elle
contient : une anomalie à une date nouvelle remonte toujours, même sur un
ticker déjà largement représenté dans le fichier.

Format de `config/known_anomalies.yaml` :

    anomalies:
      - ticker: ETZ.PA
        kind: gap
        date: 2014-12-23
        note: "Fermeture de Noël 2014. Faux positif structurel."

`kind` vaut `"gap"`, `"outlier"` ou `"split"`. Pour un trou, la date
d'identité est `gap_start`. `note` est un champ libre destiné à l'humain
: il documente pourquoi l'anomalie est acceptée.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from src.data.validation import ValidationReport

#: Valeurs valides du champ `kind` d'une entrée de ligne de base.
_KNOWN_KINDS = frozenset({"gap", "outlier", "split"})

#: Note placée par `dump_baseline` sur chaque entrée générée : une entrée
#: dont la note vaut encore ceci n'a pas été examinée par un humain.
PLACEHOLDER_NOTE = "À justifier"

_REQUIRED_FIELDS = ("ticker", "kind", "date")


@dataclass(frozen=True)
class AnomalyKey:
    """Identité d'une anomalie de validation, indépendante de ses valeurs.

    Attributes:
        ticker: Symbole du titre.
        kind: `"gap"`, `"outlier"` ou `"split"`.
        date: Date d'identité de l'anomalie (`gap_start` pour un trou,
            `date` pour une valeur aberrante ou un split suspect).
    """

    ticker: str
    kind: str
    date: date


def _to_date(value: object) -> date:
    """Normalise une valeur de date (`date`, `Timestamp`, str ISO...) en `date`."""
    return pd.Timestamp(value).date()


def load_baseline(path: str | Path) -> dict[AnomalyKey, str]:
    """Charge la ligne de base des anomalies déjà examinées.

    Args:
        path: Chemin du fichier YAML de ligne de base.

    Returns:
        Dictionnaire `AnomalyKey -> note`. Dictionnaire vide si le fichier
        n'existe pas encore : ce n'est pas une erreur, le premier usage se
        fait sans fichier de ligne de base.

    Raises:
        ValueError: Si le fichier existe mais est mal formé : YAML
            invalide, `anomalies` n'est pas une liste, une entrée n'est
            pas un mapping, un champ obligatoire (`ticker`/`kind`/`date`)
            manque, ou `kind` n'est pas l'une des valeurs reconnues.
    """
    file_path = Path(path)
    if not file_path.exists():
        return {}

    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{file_path}: YAML mal formé : {exc}") from exc

    entries = (raw or {}).get("anomalies") or []
    if not isinstance(entries, list):
        raise ValueError(f"{file_path}: la clé 'anomalies' doit être une liste")

    baseline: dict[AnomalyKey, str] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{file_path}: entrée #{i} doit être un mapping ticker/kind/date/note, "
                f"reçu {entry!r}"
            )
        missing = [field for field in _REQUIRED_FIELDS if field not in entry]
        if missing:
            raise ValueError(f"{file_path}: entrée #{i} : champ(s) manquant(s) {missing}")

        kind = entry["kind"]
        if kind not in _KNOWN_KINDS:
            raise ValueError(
                f"{file_path}: entrée #{i} : kind inconnu {kind!r} "
                f"(attendu parmi {sorted(_KNOWN_KINDS)})"
            )

        key = AnomalyKey(ticker=entry["ticker"], kind=kind, date=_to_date(entry["date"]))
        baseline[key] = entry.get("note") or ""
    return baseline


def report_keys(report: ValidationReport) -> list[AnomalyKey]:
    """Extrait les clés d'identité de toutes les anomalies d'un rapport.

    Args:
        report: Rapport de validation d'un ticker.

    Returns:
        Liste de `AnomalyKey`, une par anomalie détectée (gaps, puis
        outliers, puis splits, dans cet ordre).
    """
    keys: list[AnomalyKey] = []
    for _, row in report.gaps.iterrows():
        keys.append(AnomalyKey(report.ticker, "gap", _to_date(row["gap_start"])))
    for _, row in report.outliers.iterrows():
        keys.append(AnomalyKey(report.ticker, "outlier", _to_date(row["date"])))
    for _, row in report.unadjusted_splits.iterrows():
        keys.append(AnomalyKey(report.ticker, "split", _to_date(row["date"])))
    return keys


def _filter_df(df: pd.DataFrame, ticker: str, kind: str, date_col: str, baseline: dict[AnomalyKey, str]):
    """Retire d'un DataFrame d'anomalies les lignes dont la clé est connue."""
    if df.empty:
        return df.copy(), 0

    keep_mask = [
        AnomalyKey(ticker, kind, _to_date(value)) not in baseline for value in df[date_col]
    ]
    kept = df.loc[keep_mask].reset_index(drop=True)
    return kept, len(df) - len(kept)


def filter_known(
    reports: dict[str, ValidationReport],
    baseline: dict[AnomalyKey, str],
) -> tuple[dict[str, ValidationReport], int]:
    """Écarte d'un ensemble de rapports les anomalies déjà connues.

    Args:
        reports: `ticker -> ValidationReport`, tel que produit par
            `src.data.ingest.run_ingestion`. N'est pas modifié.
        baseline: Ligne de base chargée par `load_baseline`.

    Returns:
        Tuple `(rapports filtrés, nombre d'anomalies connues écartées)`.
        Les rapports filtrés couvrent les mêmes tickers, avec les mêmes
        colonnes de DataFrame que `reports` (compatibles avec
        `src.reporting.validation`) ; seules les lignes dont la clé
        `(ticker, kind, date)` figure dans `baseline` sont retirées. Une
        anomalie à une date absente de `baseline` est toujours conservée,
        même sur un ticker par ailleurs largement couvert.
    """
    filtered: dict[str, ValidationReport] = {}
    total_discarded = 0

    for ticker, report in reports.items():
        gaps, d_gaps = _filter_df(report.gaps, ticker, "gap", "gap_start", baseline)
        outliers, d_outliers = _filter_df(report.outliers, ticker, "outlier", "date", baseline)
        splits, d_splits = _filter_df(report.unadjusted_splits, ticker, "split", "date", baseline)

        filtered[ticker] = ValidationReport(
            ticker=ticker, gaps=gaps, outliers=outliers, unadjusted_splits=splits
        )
        total_discarded += d_gaps + d_outliers + d_splits

    return filtered, total_discarded


def dump_baseline(
    reports: dict[str, ValidationReport],
    path: str | Path,
    force: bool = False,
) -> int:
    """Écrit une ligne de base couvrant toutes les anomalies des rapports fournis.

    Chaque entrée est écrite avec `note: "À justifier"` : cette fonction
    ne fait qu'inventorier l'état courant, elle ne juge jamais qu'une
    anomalie est acceptable — c'est un humain qui édite ensuite le fichier
    pour remplacer ce placeholder par une vraie justification.

    Args:
        reports: `ticker -> ValidationReport` dont on veut baseliner
            toutes les anomalies actuelles.
        path: Chemin du fichier YAML à écrire.
        force: Si `False` (défaut) et que `path` existe déjà, lève une
            erreur sans rien écrire. Passer `True` pour écraser malgré
            tout.

    Returns:
        Nombre d'entrées écrites.

    Raises:
        FileExistsError: Si `path` existe déjà et `force` est `False` : ce
            fichier peut contenir des justifications rédigées à la main,
            les écraser par accident serait une perte sèche.
    """
    file_path = Path(path)
    if file_path.exists() and not force:
        raise FileExistsError(
            f"{file_path} existe déjà : passez force=True (--force en CLI) pour l'écraser. "
            "Ce fichier peut contenir des justifications écrites à la main."
        )

    keys: list[AnomalyKey] = []
    for report in reports.values():
        keys.extend(report_keys(report))
    keys.sort(key=lambda k: (k.ticker, k.date, k.kind))

    entries = [
        {"ticker": key.ticker, "kind": key.kind, "date": key.date, "note": PLACEHOLDER_NOTE}
        for key in keys
    ]

    header = (
        "# Ligne de base des anomalies de validation déjà examinées et acceptées.\n"
        "# Générée par --init-known-anomalies : chaque entrée porte encore\n"
        f'# note: "{PLACEHOLDER_NOTE}" et doit être remplacée à la main par une vraie\n'
        "# justification avant d'être considérée comme acceptée. Voir le docstring de\n"
        "# src/data/baseline.py pour la convention d'identité (ticker, kind, date).\n"
    )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        header + yaml.safe_dump({"anomalies": entries}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return len(entries)
