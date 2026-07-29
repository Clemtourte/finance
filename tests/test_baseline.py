"""Tests de la ligne de base des anomalies de validation (src.data.baseline)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.data.baseline import (
    AnomalyKey,
    dump_baseline,
    filter_known,
    load_baseline,
    report_keys,
)
from src.data.validation import ValidationReport


def _report(
    ticker: str,
    gap_dates: list[tuple[date, date, int]] | None = None,
    outlier_dates: list[date] | None = None,
    split_dates: list[date] | None = None,
) -> ValidationReport:
    gaps = pd.DataFrame(
        {
            "gap_start": [g[0] for g in gap_dates or []],
            "gap_end": [g[1] for g in gap_dates or []],
            "calendar_days": [g[2] for g in gap_dates or []],
        }
    )
    outliers = pd.DataFrame(
        {
            "date": outlier_dates or [],
            "adj_close": [100.0] * len(outlier_dates or []),
            "daily_return": [-0.3] * len(outlier_dates or []),
        }
    )
    splits = pd.DataFrame(
        {
            "date": split_dates or [],
            "close_ratio": [2.0] * len(split_dates or []),
            "adj_close_ratio": [2.0] * len(split_dates or []),
            "matched_split_ratio": [2.0] * len(split_dates or []),
        }
    )
    return ValidationReport(ticker=ticker, gaps=gaps, outliers=outliers, unadjusted_splits=splits)


# --- load_baseline -----------------------------------------------------------


def test_load_baseline_missing_file_returns_empty_dict_without_error(tmp_path):
    assert load_baseline(tmp_path / "does_not_exist.yaml") == {}


def test_load_baseline_valid_yaml_returns_keys_and_notes(tmp_path):
    path = tmp_path / "known_anomalies.yaml"
    path.write_text(
        """
anomalies:
  - ticker: ETZ.PA
    kind: gap
    date: 2014-12-23
    note: "Fermeture de Noël 2014."
  - ticker: ALKAL.PA
    kind: outlier
    date: 2024-03-01
    note: "Volatilité small cap."
""",
        encoding="utf-8",
    )

    baseline = load_baseline(path)

    assert baseline == {
        AnomalyKey("ETZ.PA", "gap", date(2014, 12, 23)): "Fermeture de Noël 2014.",
        AnomalyKey("ALKAL.PA", "outlier", date(2024, 3, 1)): "Volatilité small cap.",
    }


def test_load_baseline_unknown_kind_raises_explicit_error(tmp_path):
    path = tmp_path / "known_anomalies.yaml"
    path.write_text(
        """
anomalies:
  - ticker: AI.PA
    kind: earthquake
    date: 2024-01-01
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="kind inconnu"):
        load_baseline(path)


def test_load_baseline_missing_field_raises_explicit_error(tmp_path):
    path = tmp_path / "known_anomalies.yaml"
    path.write_text(
        """
anomalies:
  - ticker: AI.PA
    kind: gap
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manquant"):
        load_baseline(path)


# --- report_keys --------------------------------------------------------------


def test_report_keys_gap_uses_gap_start_as_identity_date():
    report = _report("AI.PA", gap_dates=[(date(2024, 1, 5), date(2024, 2, 4), 30)])

    keys = report_keys(report)

    assert keys == [AnomalyKey("AI.PA", "gap", date(2024, 1, 5))]


# --- filter_known --------------------------------------------------------------


def test_filter_known_discards_known_and_keeps_new_anomalies():
    reports = {
        "AI.PA": _report(
            "AI.PA",
            gap_dates=[(date(2024, 1, 5), date(2024, 2, 4), 30)],
            outlier_dates=[date(2024, 3, 1), date(2024, 3, 15)],
        ),
    }
    baseline = {
        AnomalyKey("AI.PA", "gap", date(2024, 1, 5)): "connu",
        AnomalyKey("AI.PA", "outlier", date(2024, 3, 1)): "connu",
    }

    filtered, n_discarded = filter_known(reports, baseline)

    assert n_discarded == 2
    assert filtered["AI.PA"].gaps.empty
    assert list(filtered["AI.PA"].outliers["date"]) == [date(2024, 3, 15)]


def test_filter_known_does_not_mutate_input_reports():
    original = _report("AI.PA", gap_dates=[(date(2024, 1, 5), date(2024, 2, 4), 30)])
    reports = {"AI.PA": original}
    baseline = {AnomalyKey("AI.PA", "gap", date(2024, 1, 5)): "connu"}

    filter_known(reports, baseline)

    assert len(original.gaps) == 1
    assert reports["AI.PA"] is original


# --- dump_baseline -------------------------------------------------------------


def test_dump_baseline_refuses_to_overwrite_existing_file_without_force(tmp_path):
    path = tmp_path / "known_anomalies.yaml"
    path.write_text("anomalies: []\n", encoding="utf-8")
    original_content = path.read_text(encoding="utf-8")

    reports = {"AI.PA": _report("AI.PA", gap_dates=[(date(2024, 1, 5), date(2024, 2, 4), 30)])}

    with pytest.raises(FileExistsError):
        dump_baseline(reports, path)

    assert path.read_text(encoding="utf-8") == original_content


def test_dump_baseline_with_force_overwrites_existing_file(tmp_path):
    path = tmp_path / "known_anomalies.yaml"
    path.write_text("anomalies: []\n", encoding="utf-8")

    reports = {"AI.PA": _report("AI.PA", gap_dates=[(date(2024, 1, 5), date(2024, 2, 4), 30)])}

    n_written = dump_baseline(reports, path, force=True)

    assert n_written == 1
    assert "AI.PA" in path.read_text(encoding="utf-8")


def test_dump_baseline_then_load_then_filter_leaves_no_new_anomalies(tmp_path):
    path = tmp_path / "known_anomalies.yaml"
    reports = {
        "AI.PA": _report(
            "AI.PA",
            gap_dates=[(date(2024, 1, 5), date(2024, 2, 4), 30)],
            outlier_dates=[date(2024, 3, 1)],
        ),
        "ALKAL.PA": _report("ALKAL.PA", split_dates=[date(2024, 4, 10)]),
    }

    dump_baseline(reports, path)
    baseline = load_baseline(path)
    filtered, n_discarded = filter_known(reports, baseline)

    assert n_discarded == 3
    assert not any(r.has_issues for r in filtered.values())
