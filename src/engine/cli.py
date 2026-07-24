"""CLI : lance un backtest complet sur un ticker et affiche stratégie vs buy & hold.

Exemple :

    uv run python -m src.engine.cli --ticker AI.PA

Le ticker doit déjà avoir été ingéré dans l'entrepôt DuckDB (voir
`src.data.ingest`).
"""

from __future__ import annotations

import argparse
from datetime import date

from src.data.config import load_data_config
from src.data.duckdb_loader import read_ohlcv
from src.engine.backtest import run_backtest, run_buy_and_hold
from src.engine.config import load_backtest_config
from src.metrics.comparison import compare
from src.metrics.performance import compute_metrics
from src.reporting.table import export_comparison_csv, format_comparison_table
from src.strategies.sma_crossover import load_sma_crossover_strategy


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


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
    args = parser.parse_args()

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

    strategy_metrics = compute_metrics(
        strategy_pf, backtest_config.trading_days_per_year, backtest_config.risk_free_rate
    )
    benchmark_metrics = compute_metrics(
        benchmark_pf, backtest_config.trading_days_per_year, backtest_config.risk_free_rate
    )
    rows = compare(strategy_metrics, benchmark_metrics)

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
    print()
    print(format_comparison_table(rows))

    if args.output_csv:
        export_comparison_csv(rows, args.output_csv)
        print(f"\nExporté vers {args.output_csv}")


if __name__ == "__main__":
    main()
