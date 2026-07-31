"""CLI : lance un backtest complet sur un ticker et affiche stratégie vs buy & hold.

Exemple :

    uv run python -m src.engine.cli --ticker AI.PA

Par défaut, la stratégie de référence (SMA crossover) est utilisée.
Pour en choisir une autre parmi celles du registre
(`src.strategies.registry`) :

    uv run python -m src.engine.cli --ticker AI.PA --strategy momentum_12_1

`--strategy-config` reste disponible pour surcharger le fichier YAML de
paramètres (défaut : celui du registre pour `--strategy`).

Le ticker doit déjà avoir été ingéré dans l'entrepôt DuckDB (voir
`src.data.ingest`).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from src.data.config import load_data_config, load_universe
from src.data.duckdb_loader import read_ohlcv
from src.engine.backtest import run_backtest, run_buy_and_hold
from src.engine.config import load_backtest_config
from src.metrics.comparison import compare
from src.metrics.performance import compute_metrics, compute_metrics_from_series, split_portfolio_by_date
from src.reporting.table import export_comparison_csv, format_comparison_table, format_friction_pct
from src.strategies.registry import STRATEGIES, display_name, load_strategy


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _suffixed_path(path: str, suffix: str) -> str:
    p = Path(path)
    return str(p.with_name(f"{p.stem}_{suffix}{p.suffix}"))


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True, help="Ticker à backtester (ex. AI.PA)")
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
    parser.add_argument("--start", type=_parse_date, default=None, help="Surcharge la date de début (YYYY-MM-DD)")
    parser.add_argument("--end", type=_parse_date, default=None, help="Surcharge la date de fin (YYYY-MM-DD)")
    parser.add_argument("--output-csv", default=None, help="Chemin d'export CSV du tableau de comparaison")
    parser.add_argument(
        "--rebalance-freq",
        default=None,
        choices=["daily", "weekly", "monthly"],
        help="Surcharge la fréquence de rééquilibrage du fichier de config",
    )
    parser.add_argument(
        "--split-date",
        type=_parse_date,
        default=None,
        help="Date de coupure in-sample/out-of-sample (YYYY-MM-DD) : les métriques "
        "sont calculées séparément avant/à partir de cette date",
    )
    parser.add_argument(
        "--no-split",
        action="store_true",
        help="Autorise à tourner sans --split-date, sur la période complète "
        "(la sortie le signale explicitement)",
    )
    parser.add_argument(
        "--universe-file",
        default=None,
        help="Fichier d'univers pour l'éligibilité TTF/spread du ticker "
        "(défaut : celui de --data-config)",
    )
    args = parser.parse_args()

    if args.split_date is None and not args.no_split:
        parser.error(
            "--split-date est requis (métriques in-sample/out-of-sample) ; "
            "passez --no-split explicitement pour tourner sur la période complète sans découpage"
        )

    data_config = load_data_config(args.data_config)
    backtest_config = load_backtest_config(args.backtest_config)
    strategy = load_strategy(args.strategy, args.strategy_config)
    rebalance_freq = args.rebalance_freq or backtest_config.rebalance_freq

    universe_file = args.universe_file or data_config.universe_file
    universe = load_universe(universe_file)
    ticker_info = next((t for t in universe if t.ticker == args.ticker), None)
    if ticker_info is None:
        print(f"/!\\ {args.ticker} absent de {universe_file} : ttf=False, spread_pct=0.0 par défaut")
        ttf_eligible, spread_pct = False, 0.0
    else:
        ttf_eligible, spread_pct = ticker_info.ttf, ticker_info.spread_pct

    start = args.start or data_config.start_date
    end = args.end or data_config.end_date

    df = read_ohlcv(data_config.duckdb_path, data_config.duckdb_table, args.ticker, start=start, end=end)

    periods_per_year = backtest_config.trading_days_per_year
    risk_free_rate = backtest_config.risk_free_rate

    target_position = strategy.generate_signals(df)
    strategy_pf = run_backtest(
        df,
        target_position,
        backtest_config.costs,
        backtest_config.initial_capital,
        ttf_eligible=ttf_eligible,
        spread_pct=spread_pct,
        rebalance_freq=rebalance_freq,
    )
    benchmark_pf = run_buy_and_hold(
        df, backtest_config.costs, backtest_config.initial_capital,
        ttf_eligible=ttf_eligible, spread_pct=spread_pct,
    )

    tiers_desc = ", ".join(
        f"<= {t.max_order_value:.0f}€: {t.fixed_fee:.2f}€"
        if t.fixed_fee is not None
        else f"> seuil précédent: {t.pct_fee:.2%}"
        for t in backtest_config.costs.brokerage_tiers
    )
    print(
        f"Backtest {args.ticker} | {start} -> {end} | stratégie: {display_name(args.strategy)} "
        f"{strategy.params} | rééquilibrage: {rebalance_freq}"
    )
    print(
        f"Coûts : courtage [{tiers_desc}] + TTF {backtest_config.costs.ttf_pct:.2%} "
        f"({'éligible' if ttf_eligible else 'non éligible'}) + glissement "
        f"{backtest_config.costs.base_slippage_pct:.2%} de base + {spread_pct:.2%} de spread | "
        f"capital initial : {backtest_config.initial_capital:,.0f}"
    )

    full_strategy_metrics = compute_metrics(
        strategy_pf, df["open"], backtest_config.costs, ttf_eligible, spread_pct,
        periods_per_year, risk_free_rate,
    )
    friction_pct_display = format_friction_pct(full_strategy_metrics.friction_pct_of_gross_gain)
    print(
        f"Friction totale (stratégie, période complète) : "
        f"{full_strategy_metrics.friction_eur:,.2f}€ "
        f"({friction_pct_display} du gain brut) | "
        f"Turnover annualisé : {full_strategy_metrics.turnover_annualized:.2f}x"
    )

    if args.no_split:
        print(
            "\n/!\\ AUCUN découpage in-sample/out-of-sample (--no-split) : les "
            "métriques ci-dessous couvrent toute la période, sans contrôle de "
            "surapprentissage.\n"
        )
        benchmark_metrics = compute_metrics(
            benchmark_pf, df["open"], backtest_config.costs, ttf_eligible, spread_pct,
            periods_per_year, risk_free_rate,
        )
        rows = compare(full_strategy_metrics, benchmark_metrics)
        print(format_comparison_table(rows))

        if args.output_csv:
            export_comparison_csv(rows, args.output_csv)
            print(f"\nExporté vers {args.output_csv}")
    else:
        split_date = args.split_date
        min_bars = backtest_config.min_bars_per_period
        split_ts = pd.Timestamp(split_date)
        n_bars_is = int((df.index < split_ts).sum())
        n_bars_oos = int((df.index >= split_ts).sum())
        if n_bars_is < min_bars or n_bars_oos < min_bars:
            print(
                "\n/!\\ PÉRIODE INSUFFISANTE : ce résultat n'est PAS une vérification "
                "valable, juste de l'exploration. In-sample : "
                f"{n_bars_is} séances (minimum {min_bars}) | Out-of-sample : "
                f"{n_bars_oos} séances (minimum {min_bars}). En dessous de ce seuil, "
                "un seul mouvement de marché suffit à décider du résultat.\n"
            )

        strategy_equity_is, strategy_trades_is, strategy_equity_oos, strategy_trades_oos = (
            split_portfolio_by_date(strategy_pf, split_date)
        )
        benchmark_equity_is, benchmark_trades_is, benchmark_equity_oos, benchmark_trades_oos = (
            split_portfolio_by_date(benchmark_pf, split_date)
        )

        costs = backtest_config.costs

        strategy_metrics_is = compute_metrics_from_series(
            strategy_equity_is, strategy_trades_is, df["open"], costs, ttf_eligible, spread_pct,
            periods_per_year, risk_free_rate,
        )
        benchmark_metrics_is = compute_metrics_from_series(
            benchmark_equity_is, benchmark_trades_is, df["open"], costs, ttf_eligible, spread_pct,
            periods_per_year, risk_free_rate,
        )
        strategy_metrics_oos = compute_metrics_from_series(
            strategy_equity_oos, strategy_trades_oos, df["open"], costs, ttf_eligible, spread_pct,
            periods_per_year, risk_free_rate,
        )
        benchmark_metrics_oos = compute_metrics_from_series(
            benchmark_equity_oos, benchmark_trades_oos, df["open"], costs, ttf_eligible, spread_pct,
            periods_per_year, risk_free_rate,
        )

        rows_is = compare(strategy_metrics_is, benchmark_metrics_is)
        rows_oos = compare(strategy_metrics_oos, benchmark_metrics_oos)

        print(f"\n=== In-sample ({start} -> {split_date}, exclu) ===")
        print(format_comparison_table(rows_is))
        print(f"\n=== Out-of-sample ({split_date} -> {end}) ===")
        print(format_comparison_table(rows_oos))

        if args.output_csv:
            is_path = _suffixed_path(args.output_csv, "in_sample")
            oos_path = _suffixed_path(args.output_csv, "out_of_sample")
            export_comparison_csv(rows_is, is_path)
            export_comparison_csv(rows_oos, oos_path)
            print(f"\nExporté vers {is_path} et {oos_path}")


if __name__ == "__main__":
    main()
