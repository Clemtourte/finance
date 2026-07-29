"""Tests du rendu texte des rapports de validation (src.reporting.validation)."""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.data.validation import ValidationReport
from src.reporting.validation import format_validation_report, format_validation_summary


def _empty_report(ticker: str) -> ValidationReport:
    return ValidationReport(ticker=ticker)


def _report_with_gap(ticker: str) -> ValidationReport:
    gaps = pd.DataFrame(
        {
            "gap_start": [date(2024, 1, 5)],
            "gap_end": [date(2024, 2, 4)],
            "calendar_days": [30],
        }
    )
    return ValidationReport(ticker=ticker, gaps=gaps)


def _report_with_outlier(ticker: str) -> ValidationReport:
    outliers = pd.DataFrame(
        {
            "date": [date(2024, 3, 1)],
            "adj_close": [57.1],
            "daily_return": [-0.429],
        }
    )
    return ValidationReport(ticker=ticker, outliers=outliers)


def _report_with_unadjusted_split(ticker: str) -> ValidationReport:
    splits = pd.DataFrame(
        {
            "date": [date(2024, 4, 10)],
            "close_ratio": [0.5],
            "adj_close_ratio": [0.503],
            "matched_split_ratio": [0.5],
        }
    )
    return ValidationReport(ticker=ticker, unadjusted_splits=splits)


def test_format_validation_report_empty_returns_single_explicit_line():
    result = format_validation_report(_empty_report("AI.PA"))
    assert result != ""
    assert len(result.splitlines()) == 1
    assert "AI.PA" in result
    assert "aucune anomalie" in result.lower()


def test_format_validation_report_gap_shows_both_dates_and_day_count():
    result = format_validation_report(_report_with_gap("AI.PA"))
    assert "2024-01-05" in result
    assert "2024-02-04" in result
    assert "30" in result


def test_format_validation_report_outlier_shows_date_and_signed_pct_return():
    result = format_validation_report(_report_with_outlier("ALKAL.PA"))
    assert "2024-03-01" in result
    assert "-42.9%" in result


def test_format_validation_report_split_shows_date_and_ratio():
    result = format_validation_report(_report_with_unadjusted_split("AI.PA"))
    assert "2024-04-10" in result
    assert "0.5" in result


def test_format_validation_summary_mixes_clean_and_flagged_tickers_with_correct_total():
    reports = {
        "AI.PA": _empty_report("AI.PA"),
        "ALKAL.PA": _report_with_outlier("ALKAL.PA"),
        "MC.PA": _report_with_gap("MC.PA"),
    }
    result = format_validation_summary(reports)

    assert "AI.PA" in result
    assert "aucune anomalie" in result
    assert "-42.9%" in result
    assert "2024-01-05" in result
    assert "Total : 3 ticker(s)" in result
    assert "1 trou(s)" in result
    assert "1 valeur(s) aberrante(s)" in result
    assert "0 split(s) suspect(s)" in result


# --- filtered=True : vocabulaire "nouvelle" (ligne de base active) ------------


def test_format_validation_report_empty_with_filtered_says_nouvelle_not_detectee():
    result = format_validation_report(_empty_report("ETZ.PA"), filtered=True)
    assert result != ""
    assert len(result.splitlines()) == 1
    assert "ETZ.PA" in result
    assert "aucune anomalie nouvelle" in result
    assert "détectée" not in result


def test_format_validation_report_empty_without_filtered_still_says_detectee():
    result = format_validation_report(_empty_report("ETZ.PA"))
    assert "aucune anomalie détectée" in result
    assert "nouvelle" not in result


def test_format_validation_summary_filtered_qualifies_totals_as_nouvelles():
    reports = {
        "ETZ.PA": _empty_report("ETZ.PA"),
        "ALKAL.PA": _report_with_outlier("ALKAL.PA"),
    }
    result = format_validation_summary(reports, filtered=True)

    assert "ETZ.PA: aucune anomalie nouvelle" in result
    assert "trou(s) nouveau(x)" in result
    assert "valeur(s) aberrante(s) nouvelle(s)" in result
    assert "split(s) suspect(s) nouveau(x)" in result


def test_format_validation_summary_without_filtered_keeps_original_wording():
    reports = {"ETZ.PA": _empty_report("ETZ.PA")}
    result = format_validation_summary(reports)

    assert "ETZ.PA: aucune anomalie détectée" in result
    assert "nouveau" not in result
