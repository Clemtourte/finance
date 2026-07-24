"""Tests des contrôles de qualité (src.data.validation)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.data.validation import (
    detect_gaps,
    detect_outliers,
    detect_unadjusted_splits,
    validate_ohlcv,
)


def _business_days(start: date, n: int) -> list[date]:
    return list(pd.bdate_range(start=start, periods=n).date)


def _frame(dates: list[date], closes: list[float], adj_closes: list[float] | None = None) -> pd.DataFrame:
    adj_closes = adj_closes if adj_closes is not None else closes
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "adj_close": adj_closes,
            "volume": [1_000] * len(dates),
        }
    )


# --- detect_gaps ----------------------------------------------------------


def test_detect_gaps_none_on_regular_business_days():
    dates = _business_days(date(2024, 1, 1), 20)
    df = _frame(dates, [100.0] * 20)
    assert detect_gaps(df, max_gap_calendar_days=5).empty


def test_detect_gaps_flags_long_gap():
    dates = _business_days(date(2024, 1, 1), 10)
    # Trou de 30 jours calendaires après la 5e séance.
    dates = dates[:5] + [d + timedelta(days=30) for d in dates[5:]]
    df = _frame(dates, [100.0] * 10)

    gaps = detect_gaps(df, max_gap_calendar_days=5)
    assert len(gaps) == 1
    assert gaps.iloc[0]["gap_start"] == dates[4]
    assert gaps.iloc[0]["gap_end"] == dates[5]
    assert gaps.iloc[0]["calendar_days"] == (dates[5] - dates[4]).days


def test_detect_gaps_empty_for_short_series():
    df = _frame([date(2024, 1, 1)], [100.0])
    assert detect_gaps(df, max_gap_calendar_days=5).empty


# --- detect_outliers --------------------------------------------------------


def test_detect_outliers_none_on_smooth_series():
    dates = _business_days(date(2024, 1, 1), 20)
    closes = [100.0 + i * 0.1 for i in range(20)]
    df = _frame(dates, closes)
    assert detect_outliers(df, return_threshold=0.30).empty


def test_detect_outliers_flags_large_jump():
    dates = _business_days(date(2024, 1, 1), 10)
    closes = [100.0] * 5 + [250.0] + [251.0] * 4  # +150% en un jour
    df = _frame(dates, closes)

    outliers = detect_outliers(df, return_threshold=0.30)
    assert len(outliers) == 1
    assert outliers.iloc[0]["date"] == dates[5]


# --- detect_unadjusted_splits ------------------------------------------------


def test_detect_unadjusted_splits_flags_matching_jump_on_adj_close():
    dates = _business_days(date(2024, 1, 1), 6)
    closes = [100.0] * 3 + [50.0] * 3  # split 2:1 sur le prix brut
    # adj_close saute aussi de 2x : signe que l'ajustement n'a pas été appliqué.
    adj_closes = [100.0] * 3 + [50.0] * 3
    df = _frame(dates, closes, adj_closes)

    flagged = detect_unadjusted_splits(df, ratio_tolerance=0.03)
    assert len(flagged) == 1
    assert flagged.iloc[0]["date"] == dates[3]
    assert flagged.iloc[0]["matched_split_ratio"] == 0.5


def test_detect_unadjusted_splits_silent_when_properly_adjusted():
    dates = _business_days(date(2024, 1, 1), 6)
    closes = [100.0] * 3 + [50.0] * 3  # split 2:1 sur le prix brut
    adj_closes = [50.0] * 6  # adj_close déjà lissé, pas de saut
    df = _frame(dates, closes, adj_closes)

    assert detect_unadjusted_splits(df, ratio_tolerance=0.03).empty


def test_detect_unadjusted_splits_empty_for_short_series():
    df = _frame([date(2024, 1, 1)], [100.0])
    assert detect_unadjusted_splits(df, ratio_tolerance=0.03).empty


# --- validate_ohlcv (agrégation) --------------------------------------------


def test_validate_ohlcv_no_issues_on_clean_series():
    dates = _business_days(date(2024, 1, 1), 30)
    closes = [100.0 + i * 0.1 for i in range(30)]
    df = _frame(dates, closes)

    report = validate_ohlcv(
        df,
        ticker="AI.PA",
        max_gap_calendar_days=5,
        outlier_return_threshold=0.30,
        split_ratio_tolerance=0.03,
    )
    assert report.ticker == "AI.PA"
    assert not report.has_issues


def test_validate_ohlcv_flags_issues():
    dates = _business_days(date(2024, 1, 1), 10)
    dates = dates[:5] + [d + timedelta(days=30) for d in dates[5:]]
    closes = [100.0] * 5 + [300.0] + [301.0] * 4
    df = _frame(dates, closes)

    report = validate_ohlcv(
        df,
        ticker="AI.PA",
        max_gap_calendar_days=5,
        outlier_return_threshold=0.30,
        split_ratio_tolerance=0.03,
    )
    assert report.has_issues
    assert len(report.gaps) == 1
    assert len(report.outliers) == 1
