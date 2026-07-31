"""Lance une stratégie sur tout un univers et produit un verdict par ticker.

Point d'entrée en ligne de commande :

    uv run python -m src.engine.batch --universe-file config/universe_cac40.yaml --split-date 2020-01-01

Par défaut, la stratégie de référence (SMA crossover) est utilisée. Pour
en choisir une autre parmi celles du registre (`src.strategies.registry`) :

    uv run python -m src.engine.batch --universe-file config/universe_cac40.yaml --split-date 2020-01-01 --strategy momentum_12_1

`--strategy-config` reste disponible pour surcharger le fichier YAML de
paramètres (défaut : celui du registre pour `--strategy`).

`--universe-file` est optionnel : par défaut, l'univers vient de
`universe_file` dans `--data-config` (`config/data.yaml`).

Comme le CLI mono-ticker (`src.engine.cli`), la coupure in-sample/
out-of-sample est obligatoire : le verdict SURVIT/REJETÉ ne porte que sur
l'out-of-sample, jamais sur la période complète (sinon on mesure la
capacité de la stratégie à coller au passé, pas sa capacité à généraliser).

`SURVIT` exige deux conditions cumulatives sur l'out-of-sample : battre le
buy & hold ET avoir un CAGR strictement positif. Battre une référence qui
s'effondre ne suffit pas — sur un titre en forte baisse, toute règle qui
passe du temps hors du marché bat mécaniquement le buy & hold sans avoir
généré le moindre gain ; ce n'est pas une performance, c'est une absence.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.data.config import DataConfig, TickerInfo, load_data_config, load_universe
from src.data.duckdb_loader import read_ohlcv
from src.engine.backtest import run_backtest, run_buy_and_hold
from src.engine.config import BacktestConfig, load_backtest_config
from src.metrics.performance import compute_metrics_from_series, split_portfolio_by_date
from src.reporting.table import export_batch_csv, format_batch_table
from src.strategies.base import Strategy
from src.strategies.registry import STRATEGIES, display_name, load_strategy

logger = logging.getLogger(__name__)

#: Verdict par défaut quand la comparaison in-sample/out-of-sample est
#: indéfinie (ex. trop peu de barres out-of-sample) : posture prudente,
#: cohérente avec l'objectif de falsification du projet — en cas de doute,
#: ne pas prétendre que la stratégie survit.
_UNDEFINED_VERDICT = "REJETÉ"

#: Verdict quand l'in-sample ou l'out-of-sample compte moins de
#: `min_bars_per_period` séances : SURVIT/REJETÉ signifient tous deux
#: "j'ai testé" ; ici rien n'a été testé (un seul mouvement de marché
#: suffirait à décider du résultat), donc ni l'un ni l'autre.
_NOT_TESTABLE_VERDICT = "NON TESTABLE"


def _count_bars_each_side(df: pd.DataFrame, split_date: date) -> tuple[int, int]:
    """Compte les séances strictement avant / à partir de `split_date`.

    Args:
        df: Historique OHLCV du ticker (index de dates croissant).
        split_date: Date de coupure in-sample/out-of-sample.

    Returns:
        `(n_bars_is, n_bars_oos)`.
    """
    split_ts = pd.Timestamp(split_date)
    n_bars_is = int((df.index < split_ts).sum())
    n_bars_oos = int((df.index >= split_ts).sum())
    return n_bars_is, n_bars_oos


def _insufficient_bars_reason(n_bars_is: int, n_bars_oos: int, min_bars_per_period: int) -> str | None:
    """Message lisible si l'in-sample ou l'out-of-sample est trop court, sinon `None`."""
    reasons = []
    if n_bars_is < min_bars_per_period:
        reasons.append(f"in-sample : {n_bars_is} séances sur {min_bars_per_period} exigées")
    if n_bars_oos < min_bars_per_period:
        reasons.append(f"out-of-sample : {n_bars_oos} séances sur {min_bars_per_period} exigées")
    if not reasons:
        return None
    return "Période insuffisante pour juger (" + " ; ".join(reasons) + ")"


@dataclass(frozen=True)
class BatchResult:
    """Résultat du backtest d'un ticker, verdict basé sur l'out-of-sample.

    Attributes:
        ticker: Symbole du titre.
        name: Nom du titre.
        strategy_cagr_oos: CAGR net de la stratégie, out-of-sample.
        benchmark_cagr_oos: CAGR net du buy & hold, out-of-sample.
        delta: `strategy_cagr_oos - benchmark_cagr_oos`.
        friction_pct_oos: Friction de la stratégie en % du gain brut, out-of-sample.
        verdict: `"SURVIT"` si la stratégie bat le buy & hold net de coûts
            ET affiche un CAGR strictement positif sur l'out-of-sample ;
            `"REJETÉ"` sinon (y compris si la comparaison est indéfinie,
            ou si la stratégie bat le buy & hold sans être elle-même
            rentable — voir `error` pour ce dernier cas) ; `"ERREUR"` si
            le backtest de ce ticker a échoué ; `"NON TESTABLE"` si
            l'in-sample ou l'out-of-sample compte moins de
            `min_bars_per_period` séances (aucun verdict rendu, voir
            `error` pour la raison).
        error: Message d'erreur si `verdict == "ERREUR"`, raison de
            l'insuffisance si `verdict == "NON TESTABLE"`, motif si
            `verdict == "REJETÉ"` parce que la stratégie bat le buy & hold
            en perdant elle-même de l'argent, sinon `None`.
    """

    ticker: str
    name: str
    strategy_cagr_oos: float
    benchmark_cagr_oos: float
    delta: float
    friction_pct_oos: float
    verdict: str
    error: str | None = None


def _run_single_ticker(
    info: TickerInfo,
    strategy: Strategy,
    data_config: DataConfig,
    backtest_config: BacktestConfig,
    split_date: date,
) -> BatchResult:
    df = read_ohlcv(
        data_config.duckdb_path,
        data_config.duckdb_table,
        info.ticker,
        start=data_config.start_date,
        end=data_config.end_date,
    )

    n_bars_is, n_bars_oos = _count_bars_each_side(df, split_date)
    reason = _insufficient_bars_reason(n_bars_is, n_bars_oos, backtest_config.min_bars_per_period)
    if reason is not None:
        return BatchResult(
            ticker=info.ticker,
            name=info.name,
            strategy_cagr_oos=float("nan"),
            benchmark_cagr_oos=float("nan"),
            delta=float("nan"),
            friction_pct_oos=float("nan"),
            verdict=_NOT_TESTABLE_VERDICT,
            error=reason,
        )

    target_position = strategy.generate_signals(df)
    strategy_pf = run_backtest(
        df,
        target_position,
        backtest_config.costs,
        backtest_config.initial_capital,
        ttf_eligible=info.ttf,
        spread_pct=info.spread_pct,
        rebalance_freq=backtest_config.rebalance_freq,
    )
    benchmark_pf = run_buy_and_hold(
        df,
        backtest_config.costs,
        backtest_config.initial_capital,
        ttf_eligible=info.ttf,
        spread_pct=info.spread_pct,
    )

    _, _, strategy_equity_oos, strategy_trades_oos = split_portfolio_by_date(strategy_pf, split_date)
    _, _, benchmark_equity_oos, benchmark_trades_oos = split_portfolio_by_date(benchmark_pf, split_date)

    periods_per_year = backtest_config.trading_days_per_year
    risk_free_rate = backtest_config.risk_free_rate

    strategy_metrics_oos = compute_metrics_from_series(
        strategy_equity_oos, strategy_trades_oos, df["open"], backtest_config.costs,
        info.ttf, info.spread_pct, periods_per_year, risk_free_rate,
    )
    benchmark_metrics_oos = compute_metrics_from_series(
        benchmark_equity_oos, benchmark_trades_oos, df["open"], backtest_config.costs,
        info.ttf, info.spread_pct, periods_per_year, risk_free_rate,
    )

    strategy_cagr = strategy_metrics_oos.cagr
    benchmark_cagr = benchmark_metrics_oos.cagr
    delta = strategy_cagr - benchmark_cagr

    error: str | None = None
    if delta != delta:  # NaN check sans dépendre de math/numpy ici
        verdict = _UNDEFINED_VERDICT
    elif delta > 0 and strategy_cagr > 0:
        verdict = "SURVIT"
    elif delta > 0:
        # Bat le buy & hold, mais uniquement parce que celui-ci s'effondre
        # plus vite : la stratégie elle-même perd de l'argent, ce n'est
        # pas une performance. Voir docstring du module.
        verdict = "REJETÉ"
        error = (
            f"Bat le buy & hold ({benchmark_cagr:.1%}/an) mais perd de "
            f"l'argent ({strategy_cagr:.1%}/an)"
        )
    else:
        verdict = "REJETÉ"

    return BatchResult(
        ticker=info.ticker,
        name=info.name,
        strategy_cagr_oos=strategy_cagr,
        benchmark_cagr_oos=benchmark_cagr,
        delta=delta,
        friction_pct_oos=strategy_metrics_oos.friction_pct_of_gross_gain,
        verdict=verdict,
        error=error,
    )


def run_batch(
    universe: list[TickerInfo],
    strategy: Strategy,
    data_config: DataConfig,
    backtest_config: BacktestConfig,
    split_date: date,
) -> list[BatchResult]:
    """Backteste `strategy` sur chaque ticker de `universe`.

    Un ticker dont le backtest échoue (absent de l'entrepôt, historique
    trop court, etc.) n'interrompt pas le run : il est reporté avec le
    verdict `"ERREUR"` et le message d'exception associé.

    Args:
        universe: Tickers à backtester (avec leurs champs `ttf`/`spread_pct`).
        strategy: Stratégie à évaluer, identique pour tout l'univers.
        data_config: Configuration de la couche données (accès DuckDB).
        backtest_config: Configuration de coûts/capital/rééquilibrage.
        split_date: Date de coupure in-sample/out-of-sample ; le verdict
            ne porte que sur l'out-of-sample.

    Returns:
        Une `BatchResult` par ticker, dans l'ordre de `universe`.
    """
    results: list[BatchResult] = []
    for info in universe:
        try:
            results.append(
                _run_single_ticker(info, strategy, data_config, backtest_config, split_date)
            )
        except Exception as exc:  # noqa: BLE001 - un ticker en échec ne doit pas interrompre le run
            logger.warning("Échec du backtest pour %s (%s) : %s", info.ticker, info.name, exc)
            results.append(
                BatchResult(
                    ticker=info.ticker,
                    name=info.name,
                    strategy_cagr_oos=float("nan"),
                    benchmark_cagr_oos=float("nan"),
                    delta=float("nan"),
                    friction_pct_oos=float("nan"),
                    verdict="ERREUR",
                    error=str(exc),
                )
            )
    return results


def main() -> None:
    """CLI : lance une stratégie sur tout un univers et affiche le récapitulatif."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe-file",
        default=None,
        help="Fichier d'univers (ex. config/universe_cac40.yaml) ; défaut : "
        "celui de --data-config (universe_file)",
    )
    parser.add_argument("--data-config", default="config/data.yaml")
    parser.add_argument("--backtest-config", default="config/backtest.yaml")
    parser.add_argument(
        "--strategy",
        default="sma_crossover",
        choices=sorted(STRATEGIES),
        help="Stratégie à backtester (défaut : sma_crossover)",
    )
    parser.add_argument(
        "--strategy-config",
        default=None,
        help="Surcharge le fichier YAML de paramètres de --strategy "
        "(défaut : chemin par défaut du registre pour cette stratégie)",
    )
    parser.add_argument(
        "--split-date",
        required=True,
        type=lambda v: date.fromisoformat(v),
        help="Date de coupure in-sample/out-of-sample (YYYY-MM-DD) ; le verdict "
        "ne porte que sur l'out-of-sample",
    )
    parser.add_argument("--output-csv", default=None, help="Chemin d'export CSV du récapitulatif")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    data_config = load_data_config(args.data_config)
    universe_file = args.universe_file or data_config.universe_file
    universe = load_universe(universe_file)
    backtest_config = load_backtest_config(args.backtest_config)
    strategy = load_strategy(args.strategy, args.strategy_config)

    results = run_batch(universe, strategy, data_config, backtest_config, args.split_date)

    n_survives = sum(1 for r in results if r.verdict == "SURVIT")
    n_rejected = sum(1 for r in results if r.verdict == "REJETÉ")
    n_not_testable = sum(1 for r in results if r.verdict == _NOT_TESTABLE_VERDICT)
    n_errors = sum(1 for r in results if r.verdict == "ERREUR")

    print(
        f"Batch {len(results)} tickers | split: {args.split_date} | stratégie: "
        f"{display_name(args.strategy)} {strategy.params}"
    )
    print(
        f"SURVIT: {n_survives} | REJETÉ: {n_rejected} | NON TESTABLE: {n_not_testable} "
        f"| ERREUR: {n_errors}\n"
    )
    print(format_batch_table(results))

    if args.output_csv:
        export_batch_csv(results, args.output_csv)
        print(f"\nExporté vers {args.output_csv}")


if __name__ == "__main__":
    main()
