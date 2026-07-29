.PHONY: install test ingest backtest batch clean

install:
	uv sync

test:
	uv run pytest

# Exemple : make ingest UNIVERSE=config/universe_etf_pea.yaml
ingest:
	uv run python -m src.data.ingest --config config/data.yaml $(if $(UNIVERSE),--universe-file $(UNIVERSE),)

# Exemple : make backtest TICKER=AI.PA SPLIT=2020-01-01
backtest:
	uv run python -m src.engine.cli --ticker $(TICKER) --split-date $(SPLIT)

# Exemple : make batch UNIVERSE=config/universe_cac40.yaml SPLIT=2020-01-01
batch:
	uv run python -m src.engine.batch --universe-file $(UNIVERSE) --split-date $(SPLIT)

clean:
	rm -rf .pytest_cache data/cache data/*.duckdb
