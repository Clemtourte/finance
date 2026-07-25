.PHONY: install test ingest backtest clean

install:
	uv sync

test:
	uv run pytest

ingest:
	uv run python -m src.data.ingest --config config/data.yaml

# Exemple : make backtest TICKER=AI.PA SPLIT=2020-01-01
backtest:
	uv run python -m src.engine.cli --ticker $(TICKER) --split-date $(SPLIT)

clean:
	rm -rf .pytest_cache data/cache data/*.duckdb
