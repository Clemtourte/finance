"""Tests de la commande hebdomadaire (src.weekly), sans réseau.

Réutilise `FakeProvider` de `tests/test_ingest.py`. Le seuil `NON
TESTABLE` (`min_bars_per_period`) sert de levier déterministe pour faire
basculer un verdict d'une exécution à l'autre : la stratégie
`rebalance_bandes` (bande 10%, aucun warm-up) sort définitivement de
position dès que le prix — qui monte de 1 par séance dans ces fixtures —
s'éloigne de plus de 10% de sa référence d'entrée, soit après une
dizaine de séances ; l'out-of-sample est donc plat (~0% de CAGR) dans
tous les scénarios ci-dessous, contre un buy & hold qui continue de
monter : REJETÉ dès que la période est testable, NON TESTABLE sinon. Le
verdict précis importe peu, seul le fait qu'il change (ou non) entre deux
exécutions compte pour ces tests.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
import pytest

from src.data.provider import DataProvider, empty_ohlcv_frame
from src.engine.config import BacktestConfig
from src.engine.costs import BrokerageTier, CostConfig
from src.weekly import WeeklyConfig, load_weekly_state, run_weekly
from tests.test_ingest import FakeProvider

_EPOCH = date(2024, 1, 1)


@dataclass
class _ContinuousProvider(DataProvider):
    """Prix ancré sur la date calendaire absolue (`100 + séances depuis _EPOCH`).

    Contrairement à `FakeProvider` (dont les prix dépendent de la LONGUEUR
    de la plage demandée, pas de la date), ce double reste cohérent d'une
    exécution hebdomadaire à l'autre même quand l'ingestion ne (re)demande
    que le delta manquant : sans ça, une deuxième exécution qui étend
    `end_date` verrait le nouveau delta repartir de 100 au lieu de
    continuer la série, un décrochage de prix qui fausserait le scénario
    (et déclencherait une fausse valeur aberrante).
    """

    calls: list[tuple[str, date, date]] = field(default_factory=list)

    def get_ohlcv(self, ticker: str, start: date, end: date):
        self.calls.append((ticker, start, end))
        if start > end:
            return empty_ohlcv_frame()
        dates = list(pd.bdate_range(start=start, end=end).date)
        if not dates:
            return empty_ohlcv_frame()
        offset = {d.date(): i for i, d in enumerate(pd.bdate_range(start=_EPOCH, end=end))}
        closes = [100.0 + offset[d] for d in dates]
        return pd.DataFrame(
            {
                "date": dates,
                "open": closes,
                "high": [c + 1 for c in closes],
                "low": [c - 1 for c in closes],
                "close": closes,
                "adj_close": closes,
                "volume": [1_000] * len(dates),
            }
        )


_ANOMALY_SHOCK_INDEX = 5


@dataclass
class _AnomalousProvider(FakeProvider):
    """Injecte un choc de prix déterministe (voir `test_ingest._AnomalousFakeYFinanceProvider`).

    Comme `FakeProvider`, dont les prix dépendent de la longueur de la
    plage demandée : sûr à réutiliser sur plusieurs exécutions
    hebdomadaires TANT QUE `end_date` ne change pas d'un run à l'autre
    (aucune nouvelle plage n'est alors demandée au provider, voir
    `_ContinuousProvider` sinon).
    """

    shock_index: int = _ANOMALY_SHOCK_INDEX
    shock_multiplier: float = 2.2

    def get_ohlcv(self, ticker, start, end):
        df = super().get_ohlcv(ticker, start, end)
        if len(df) > self.shock_index:
            df = df.copy()
            mask = df.index >= df.index[self.shock_index]
            df.loc[mask, ["close", "adj_close"]] *= self.shock_multiplier
        return df


def _write_universe(path, tickers: list[tuple[str, str]]) -> None:
    lines = ["tickers:"]
    for i, (ticker, name) in enumerate(tickers):
        lines.append(f"  - ticker: {ticker}\n    name: {name}\n    isin: XX000000000{i}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _end_date_for_oos_bars(split_date: date, n_bars: int) -> date:
    """Dernière séance telle que `[split_date, dernière séance]` compte exactement `n_bars` jours ouvrés."""
    bdays = pd.bdate_range(start=split_date, periods=n_bars)
    return bdays[-1].date()


_START_DATE = date(2024, 1, 1)
_SPLIT_DATE = date(2024, 4, 1)  # ~60 séances après _START_DATE : in-sample toujours suffisant
_MIN_BARS = 15


@pytest.fixture
def workspace(tmp_path):
    from src.data.config import DataConfig, ValidationConfig, YFinanceConfig

    universe_path = tmp_path / "universe.yaml"
    _write_universe(universe_path, [("AAA.PA", "Alpha"), ("BBB.PA", "Beta")])

    weekly_config = WeeklyConfig(
        universe_file=universe_path,
        split_date=_SPLIT_DATE,
        strategy="rebalance_bandes",
        reports_dir=tmp_path / "reports",
        state_file=tmp_path / "state.json",
    )
    backtest_config = BacktestConfig(
        initial_capital=10_000.0,
        trading_days_per_year=252,
        risk_free_rate=0.0,
        rebalance_freq="daily",
        min_bars_per_period=_MIN_BARS,
        costs=CostConfig(
            brokerage_tiers=(BrokerageTier(max_order_value=None, pct_fee=0.006),),
            ttf_pct=0.0,
            base_slippage_pct=0.0005,
        ),
    )
    known_anomalies = tmp_path / "known_anomalies.yaml"  # absent par défaut

    def make_data_config(end_date: date):
        return DataConfig(
            universe_file=tmp_path / "unused.yaml",
            start_date=_START_DATE,
            end_date=end_date,
            cache_dir=tmp_path / "cache",
            duckdb_path=tmp_path / "warehouse.duckdb",
            duckdb_table="ohlcv_daily",
            yfinance=YFinanceConfig(max_retries=1, retry_backoff_seconds=0.0, request_pause_seconds=0.0),
            validation=ValidationConfig(
                max_gap_calendar_days=5, outlier_return_threshold=0.30, split_ratio_tolerance=0.03
            ),
        )

    return {
        "tmp_path": tmp_path,
        "universe_path": universe_path,
        "weekly_config": weekly_config,
        "backtest_config": backtest_config,
        "make_data_config": make_data_config,
        "known_anomalies": known_anomalies,
    }


# --- Première exécution --------------------------------------------------------


def test_first_run_writes_report_mentioning_first_run_not_fake_changes(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)  # < _MIN_BARS -> NON TESTABLE
    data_config = workspace["make_data_config"](end_date)
    run_date = date(2024, 6, 1)

    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=run_date,
    )

    assert result.exit_code == 0
    assert result.changes.is_first_run is True
    assert "Première exécution" in result.report_text
    # Pas de liste de faux changements au premier lancement.
    assert "Nouveaux tickers dans l'univers" not in result.report_text
    assert "Changements de verdict" not in result.report_text
    assert "Tickers disparus" not in result.report_text

    expected_path = workspace["weekly_config"].reports_dir / "2024-06-01.md"
    assert result.report_path == expected_path
    assert expected_path.exists()
    assert expected_path.read_text(encoding="utf-8") == result.report_text

    state = load_weekly_state(workspace["weekly_config"].state_file)
    assert set(state.tickers) == {"AAA.PA", "BBB.PA"}
    assert state.tickers["AAA.PA"].verdict == "NON TESTABLE"
    assert state.anomalies == {}


# --- Deuxième exécution identique ---------------------------------------------


def test_second_identical_run_exits_0_with_both_confirmation_lines(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)

    run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=date(2024, 6, 1),
    )
    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=date(2024, 6, 8),
    )

    assert result.exit_code == 0
    assert result.changes.is_first_run is False
    assert result.changes.verdict_changes == []
    assert result.changes.appeared == []
    assert result.changes.disappeared == []
    # Les deux sujets se prononcent explicitement, pas une seule ligne
    # générique : rien à comparer ne doit pas se lire comme "pas vérifié".
    assert "Aucune anomalie nouvelle depuis le 2024-06-01." in result.report_text
    assert "Aucun changement de verdict depuis le 2024-06-01." in result.report_text


# --- Un verdict qui bascule -----------------------------------------------------


def test_verdict_flip_exits_1_and_names_ticker_with_old_and_new_verdict(workspace):
    data_config = workspace["make_data_config"]
    # _ContinuousProvider (pas FakeProvider) : la 2e exécution étend
    # end_date, donc l'ingestion ne (re)demande que le delta manquant —
    # il faut que son prix continue la série plutôt que de repartir de
    # 100 (voir docstring de _ContinuousProvider).
    provider = _ContinuousProvider()

    run_weekly(
        workspace["weekly_config"], data_config(_end_date_for_oos_bars(_SPLIT_DATE, 10)),
        workspace["backtest_config"], workspace["known_anomalies"], provider=provider,
        run_date=date(2024, 6, 1),
    )
    result = run_weekly(
        workspace["weekly_config"], data_config(_end_date_for_oos_bars(_SPLIT_DATE, 25)),
        workspace["backtest_config"], workspace["known_anomalies"], provider=provider,
        run_date=date(2024, 6, 8),
    )

    # Le changement de verdict seul déclenche le code 1, sans aucune
    # anomalie nouvelle ou en attente dans ce scénario.
    assert result.exit_code == 1
    assert result.new_anomaly_reports == {}
    assert result.pending_anomalies == []
    changed_tickers = {c.ticker: (c.old_verdict, c.new_verdict) for c in result.changes.verdict_changes}
    assert changed_tickers["AAA.PA"] == ("NON TESTABLE", "REJETÉ")
    assert changed_tickers["BBB.PA"] == ("NON TESTABLE", "REJETÉ")
    assert "AAA.PA" in result.report_text
    assert "NON TESTABLE" in result.report_text
    assert "REJETÉ" in result.report_text
    assert "Changements de verdict" in result.report_text


# --- Une anomalie nouvelle, puis en attente, puis expliquée --------------------


def test_new_anomaly_exits_1(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)

    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=_AnomalousProvider(), run_date=date(2024, 6, 1),
    )

    assert result.exit_code == 1
    assert result.new_anomaly_reports  # au moins un ticker avec anomalie nouvelle
    assert result.pending_anomalies == []  # rien à avoir vu avant cette exécution
    assert "Anomalies nouvelles" in result.report_text


def test_same_anomaly_next_run_is_pending_not_new_and_exits_0(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)

    r1 = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=_AnomalousProvider(), run_date=date(2024, 6, 1),
    )
    assert r1.exit_code == 1

    # Même end_date -> aucune nouvelle plage à (re)demander au provider :
    # les mêmes données (donc la même anomalie, à la même date) sont
    # simplement re-détectées depuis le cache.
    r2 = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=_AnomalousProvider(), run_date=date(2024, 6, 8),
    )

    assert r2.exit_code == 0  # en attente, pas nouvelle : ne redéclenche pas le code 1
    assert r2.new_anomaly_reports == {}
    assert r2.pending_anomalies
    assert "Anomalies nouvelles" not in r2.report_text
    assert "En attente d'examen" in r2.report_text

    pending_aaa = next(p for p in r2.pending_anomalies if p.ticker == "AAA.PA")
    assert pending_aaa.first_seen == date(2024, 6, 1)
    assert pending_aaa.days_waiting == 7  # 2024-06-01 -> 2024-06-08
    assert "AAA.PA" in r2.report_text
    assert "en attente depuis 7 jour" in r2.report_text


def test_pending_anomaly_added_to_baseline_disappears_from_both_sections(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)

    run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=_AnomalousProvider(), run_date=date(2024, 6, 1),
    )
    run_weekly(  # devient "en attente"
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=_AnomalousProvider(), run_date=date(2024, 6, 8),
    )

    anomaly_date = pd.bdate_range(start=_START_DATE, end=end_date)[_ANOMALY_SHOCK_INDEX].date()
    workspace["known_anomalies"].write_text(
        f"""
anomalies:
  - ticker: AAA.PA
    kind: outlier
    date: {anomaly_date.isoformat()}
    note: "Expliquée."
  - ticker: BBB.PA
    kind: outlier
    date: {anomaly_date.isoformat()}
    note: "Expliquée."
""",
        encoding="utf-8",
    )

    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=_AnomalousProvider(), run_date=date(2024, 6, 15),
    )

    assert result.exit_code == 0
    assert result.new_anomaly_reports == {}
    assert result.pending_anomalies == []
    assert "Anomalies nouvelles" not in result.report_text
    assert "Aucune anomalie en attente d'examen." in result.report_text

    state = load_weekly_state(workspace["weekly_config"].state_file)
    assert state.anomalies == {}


def test_old_format_state_file_loads_without_error_and_anomaly_is_treated_as_new(workspace):
    import json

    state_path = workspace["weekly_config"].state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "tickers": {
                    "AAA.PA": {
                        "verdict": "NON TESTABLE", "run_date": "2024-05-01",
                        "strategy_cagr_oos": None, "benchmark_cagr_oos": None,
                    },
                    "BBB.PA": {
                        "verdict": "NON TESTABLE", "run_date": "2024-05-01",
                        "strategy_cagr_oos": None, "benchmark_cagr_oos": None,
                    },
                }
                # Pas de clé "anomalies" : format d'avant cette fonctionnalité.
            }
        ),
        encoding="utf-8",
    )

    state = load_weekly_state(state_path)  # ne lève pas
    assert state.anomalies == {}
    assert set(state.tickers) == {"AAA.PA", "BBB.PA"}

    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)
    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=_AnomalousProvider(), run_date=date(2024, 6, 1),
    )

    assert result.exit_code == 1
    assert result.new_anomaly_reports  # absente de l'état -> vue pour la 1re fois, pas une erreur
    assert result.pending_anomalies == []
    assert result.changes.verdict_changes == []  # verdict inchangé, isolé de l'effet anomalie


# --- Un ticker retiré de l'univers ------------------------------------------------


def test_ticker_removed_from_universe_is_reported_as_disappeared(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)

    run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=date(2024, 6, 1),
    )

    _write_universe(workspace["universe_path"], [("AAA.PA", "Alpha")])  # BBB.PA retiré

    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=date(2024, 6, 8),
    )

    assert result.exit_code == 1
    assert result.changes.disappeared == ["BBB.PA"]
    assert "BBB.PA" in result.report_text
    assert "disparus" in result.report_text.lower()


# --- Fichier d'état illisible ou corrompu -----------------------------------------


@pytest.fixture
def weekly_cli_workspace(tmp_path):
    universe_path = tmp_path / "universe.yaml"
    _write_universe(universe_path, [("AAA.PA", "Alpha")])

    db_path = tmp_path / "warehouse.duckdb"
    data_config_path = tmp_path / "data.yaml"
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config_path.write_text(
        f"""
universe_file: {universe_path}
period:
  start_date: "{_START_DATE.isoformat()}"
  end_date: "{end_date.isoformat()}"
cache_dir: {tmp_path / "cache"}
duckdb:
  path: {db_path}
  table: ohlcv_daily
yfinance:
  max_retries: 1
  retry_backoff_seconds: 0.0
  request_pause_seconds: 0.0
validation:
  max_gap_calendar_days: 5
  outlier_return_threshold: 0.30
  split_ratio_tolerance: 0.03
""",
        encoding="utf-8",
    )

    backtest_config_path = tmp_path / "backtest.yaml"
    backtest_config_path.write_text(
        f"""
initial_capital: 10000.0
trading_days_per_year: 252
risk_free_rate: 0.0
rebalance_freq: daily
min_bars_per_period: {_MIN_BARS}
costs:
  brokerage_tiers:
    - max_order_value: null
      pct_fee: 0.006
  ttf_pct: 0.0
  base_slippage_pct: 0.0005
""",
        encoding="utf-8",
    )

    weekly_config_path = tmp_path / "weekly.yaml"
    state_file = tmp_path / "state.json"
    weekly_config_path.write_text(
        f"""
universe_file: {universe_path}
split_date: {_SPLIT_DATE.isoformat()}
strategy: rebalance_bandes
reports_dir: {tmp_path / "reports"}
state_file: {state_file}
""",
        encoding="utf-8",
    )

    return {
        "weekly_config": weekly_config_path,
        "data_config": data_config_path,
        "backtest_config": backtest_config_path,
        "known_anomalies": tmp_path / "known_anomalies.yaml",
        "state_file": state_file,
    }


class _FakeYFinanceProvider(FakeProvider):
    """Double de `YFinanceProvider` acceptant les mêmes kwargs de résilience."""

    def __init__(self, max_retries=3, retry_backoff_seconds=2.0, request_pause_seconds=0.3):
        super().__init__()


def test_main_exits_2_when_state_file_is_corrupted(weekly_cli_workspace, monkeypatch):
    from src.weekly import main

    weekly_cli_workspace["state_file"].parent.mkdir(parents=True, exist_ok=True)
    weekly_cli_workspace["state_file"].write_text("{ceci n'est pas du JSON", encoding="utf-8")

    monkeypatch.setattr("src.data.ingest.YFinanceProvider", _FakeYFinanceProvider)
    argv = [
        "prog",
        "--weekly-config", str(weekly_cli_workspace["weekly_config"]),
        "--data-config", str(weekly_cli_workspace["data_config"]),
        "--backtest-config", str(weekly_cli_workspace["backtest_config"]),
        "--known-anomalies", str(weekly_cli_workspace["known_anomalies"]),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2


def test_main_succeeds_and_writes_report_on_first_run(weekly_cli_workspace, monkeypatch, capsys):
    from src.weekly import main

    monkeypatch.setattr("src.data.ingest.YFinanceProvider", _FakeYFinanceProvider)
    argv = [
        "prog",
        "--weekly-config", str(weekly_cli_workspace["weekly_config"]),
        "--data-config", str(weekly_cli_workspace["data_config"]),
        "--backtest-config", str(weekly_cli_workspace["backtest_config"]),
        "--known-anomalies", str(weekly_cli_workspace["known_anomalies"]),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Rapport :" in captured.out
    assert len(captured.out.strip().splitlines()) <= 3


# --- Refus d'exécution trop rapprochée de la précédente (min_days_between_runs) ---


def test_run_same_day_as_previous_is_refused_without_report_write_or_provider_calls(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)
    run_date = date(2024, 6, 1)

    run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=run_date,
    )
    report_path = workspace["weekly_config"].reports_dir / "2024-06-01.md"
    assert report_path.exists()
    report_path.unlink()  # supprimé pour distinguer "réécrit" de "jamais réécrit" ci-dessous
    state_before = workspace["weekly_config"].state_file.read_text(encoding="utf-8")

    provider = FakeProvider()
    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=provider, run_date=run_date,
    )

    assert result.exit_code == 0
    assert result.skipped is True
    assert provider.calls == []  # aucun appel au provider -> aucun accès réseau
    assert not report_path.exists()  # le refus n'a rien écrit
    assert workspace["weekly_config"].state_file.read_text(encoding="utf-8") == state_before
    # Le message cite la date de la dernière exécution et celle de la prochaine acceptée
    # (min_days_between_runs=5, défaut de WeeklyConfig -> 2024-06-01 + 5j).
    assert "2024-06-01" in result.skip_message
    assert "2024-06-06" in result.skip_message


def test_run_ten_days_after_previous_executes_normally(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)

    run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=date(2024, 6, 1),
    )
    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=date(2024, 6, 11),  # 10 jours plus tard
    )

    assert result.skipped is False
    assert result.report_path.exists()
    # La pipeline complète a bien tourné (pas juste "pas refusée") : l'état
    # a été réécrit avec la nouvelle date d'exécution.
    state = load_weekly_state(workspace["weekly_config"].state_file)
    assert state.last_run_date == date(2024, 6, 11)


def test_run_exactly_min_days_after_previous_executes_normally(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)
    run_date = date(2024, 6, 1)
    min_days = workspace["weekly_config"].min_days_between_runs
    second_run_date = run_date + timedelta(days=min_days)  # écart == seuil, pas < seuil

    run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=run_date,
    )
    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=second_run_date,
    )

    assert result.skipped is False
    assert result.report_path.exists()
    state = load_weekly_state(workspace["weekly_config"].state_file)
    assert state.last_run_date == second_run_date


def test_force_bypasses_refusal_and_report_footer_mentions_forced(workspace):
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)
    run_date = date(2024, 6, 1)

    run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=run_date,
    )
    report_path = workspace["weekly_config"].reports_dir / "2024-06-01.md"
    report_path.unlink()  # supprimé pour vérifier que --force le réécrit bien (même run_date)

    result = run_weekly(  # même jour que la précédente -> refusée sans --force
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=FakeProvider(), run_date=run_date, force=True,
    )

    assert result.skipped is False
    assert report_path.exists()  # --force a bien laissé la pipeline s'exécuter jusqu'au bout
    assert "FORCÉE" in result.report_text
    assert "--force" in result.report_text


def test_no_state_file_executes_normally_without_error(workspace):
    assert not workspace["weekly_config"].state_file.exists()
    end_date = _end_date_for_oos_bars(_SPLIT_DATE, 10)
    data_config = workspace["make_data_config"](end_date)
    provider = FakeProvider()

    result = run_weekly(
        workspace["weekly_config"], data_config, workspace["backtest_config"], workspace["known_anomalies"],
        provider=provider, run_date=date(2024, 6, 1),
    )

    assert result.skipped is False
    assert result.exit_code == 0
    assert provider.calls  # absence de fichier d'état != refus
