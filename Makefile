.PHONY: install test ingest backtest clean

install:
	uv sync

test:
	uv run pytest

ingest:
	uv run python -m src.data.ingest --config config/data.yaml

# Exemple : make backtest TICKER=AI.PA
backtest:
	uv run python -m src.engine.cli --ticker $(TICKER)

clean:
	rm -rf .pytest_cache data/cache data/*.duckdb
