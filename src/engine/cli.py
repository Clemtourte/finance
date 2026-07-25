"""CLI : lance un backtest complet sur un ticker et affiche stratégie vs buy & hold.

Exemple :

    uv run python -m src.engine.cli --ticker AI.PA

Le ticker doit déjà avoir été ingéré dans l'entrepôt DuckDB (voir
`src.data.ingest`).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from src.data.config import load_data_config
from src.data.duckdb_loader import read_ohlcv
from src.engine.backtest import run_backtest, run_buy_and_hold
from src.engine.config import load_backtest_config
from src.metrics.comparison import compare
from src.metrics.performance import compute_metrics, compute_metrics_from_series, split_portfolio_by_date
from src.reporting.table import export_comparison_csv, format_comparison_table
from src.strategies.sma_crossover import load_sma_crossover_strategy


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
    parser.add_argument("--strategy-config", default="config/strategies/sma_crossover.yaml")
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
    args = parser.parse_args()

    if args.split_date is None and not args.no_split:
        parser.error(
            "--split-date est requis (métriques in-sample/out-of-sample) ; "
            "passez --no-split explicitement pour tourner sur la période complète sans découpage"
        )

    data_config = load_data_config(args.data_config)
    backtest_config = load_backtest_config(args.backtest_config)
    strategy = load_sma_crossover_strategy(args.strategy_config)
    rebalance_freq = args.rebalance_freq or backtest_config.rebalance_freq

    start = args.start or data_config.start_date
    end = args.end or data_config.end_date

    df = read_ohlcv(data_config.duckdb_path, data_config.duckdb_table, args.ticker, start=start, end=end)

    target_position = strategy.generate_signals(df)
    strategy_pf = run_backtest(
        df,
        target_position,
        backtest_config.costs,
        backtest_config.initial_capital,
        rebalance_freq=rebalance_freq,
    )
    benchmark_pf = run_buy_and_hold(df, backtest_config.costs, backtest_config.initial_capital)

    tiers_desc = ", ".join(
        f"<= {t.max_order_value:.0f}€: {t.fixed_fee:.2f}€"
        if t.fixed_fee is not None
        else f"> seuil précédent: {t.pct_fee:.2%}"
        for t in backtest_config.costs.brokerage_tiers
    )
    print(
        f"Backtest {args.ticker} | {start} -> {end} | stratégie: SMA crossover {strategy.params} "
        f"| rééquilibrage: {rebalance_freq}"
    )
    print(
        f"Coûts : courtage [{tiers_desc}] + TTF {backtest_config.costs.ttf_pct:.2%} (si éligible) + "
        f"glissement de base {backtest_config.costs.base_slippage_pct:.2%} | capital initial : "
        f"{backtest_config.initial_capital:,.0f}"
    )

    if args.no_split:
        print(
            "\n/!\\ AUCUN découpage in-sample/out-of-sample (--no-split) : les "
            "métriques ci-dessous couvrent toute la période, sans contrôle de "
            "surapprentissage.\n"
        )
        strategy_metrics = compute_metrics(
            strategy_pf, backtest_config.trading_days_per_year, backtest_config.risk_free_rate
        )
        benchmark_metrics = compute_metrics(
            benchmark_pf, backtest_config.trading_days_per_year, backtest_config.risk_free_rate
        )
        rows = compare(strategy_metrics, benchmark_metrics)
        print(format_comparison_table(rows))

        if args.output_csv:
            export_comparison_csv(rows, args.output_csv)
            print(f"\nExporté vers {args.output_csv}")
    else:
        split_date = args.split_date
        strategy_equity_is, strategy_trades_is, strategy_equity_oos, strategy_trades_oos = (
            split_portfolio_by_date(strategy_pf, split_date)
        )
        benchmark_equity_is, benchmark_trades_is, benchmark_equity_oos, benchmark_trades_oos = (
            split_portfolio_by_date(benchmark_pf, split_date)
        )

        periods_per_year = backtest_config.trading_days_per_year
        risk_free_rate = backtest_config.risk_free_rate

        strategy_metrics_is = compute_metrics_from_series(
            strategy_equity_is, strategy_trades_is, periods_per_year, risk_free_rate
        )
        benchmark_metrics_is = compute_metrics_from_series(
            benchmark_equity_is, benchmark_trades_is, periods_per_year, risk_free_rate
        )
        strategy_metrics_oos = compute_metrics_from_series(
            strategy_equity_oos, strategy_trades_oos, periods_per_year, risk_free_rate
        )
        benchmark_metrics_oos = compute_metrics_from_series(
            benchmark_equity_oos, benchmark_trades_oos, periods_per_year, risk_free_rate
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
