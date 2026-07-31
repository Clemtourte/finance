.PHONY: install test check ingest update backtest test-one batch survey weekly clean

# Défauts partagés par les cibles d'usage courant (survey, test-one) :
# aucun argument n'est obligatoire pour un run standard.
SPLIT ?= 2018-01-01
STRATEGY ?= sma_crossover

install:
	uv sync

test:
	uv run pytest

check: test

# Le code 1 de src.data.ingest ("anomalies nouvelles à examiner") n'est
# pas un échec : il ne doit ni arrêter make ni empêcher une cible qui
# enchaîne ingestion + autre chose. Seul le code 2 (échec technique) est
# propagé. Voir SETUP.md, section « Codes de sortie ».
ingest:
	@uv run python -m src.data.ingest --config config/data.yaml $(if $(UNIVERSE),--universe-file $(UNIVERSE),); \
	code=$$?; \
	if [ $$code -eq 2 ]; then \
		exit 2; \
	elif [ $$code -eq 1 ]; then \
		echo ""; \
		echo "make : anomalies nouvelles signalées (code 1) - rien n'est cassé, lire le rapport ci-dessus."; \
	fi

# Alias mémorisable de `ingest` : l'univers vient de config/data.yaml,
# UNIVERSE reste une surcharge optionnelle (voir la cible ingest).
update: ingest

# Exemple : make backtest TICKER=AI.PA SPLIT=2020-01-01
backtest:
	uv run python -m src.engine.cli --ticker $(TICKER) --split-date $(SPLIT)

# Backtest mono-ticker sans argument superflu : univers résolu depuis
# config/data.yaml par le CLI lui-même (--universe-file n'est pas requis).
# Exemple : make test-one TICKER=AI.PA
# Exemple : make test-one TICKER=AI.PA SPLIT=2020-01-01 STRATEGY=momentum_12_1 FREQ=weekly
test-one:
	@if [ -z "$(TICKER)" ]; then \
		echo "Usage : make test-one TICKER=<ticker> [SPLIT=YYYY-MM-DD] [STRATEGY=nom] [FREQ=daily|weekly|monthly]"; \
		echo "Exemple : make test-one TICKER=AI.PA"; \
		exit 1; \
	fi
	uv run python -m src.engine.cli --ticker $(TICKER) --split-date $(SPLIT) --strategy $(STRATEGY) $(if $(FREQ),--rebalance-freq $(FREQ),)

# Exemple : make batch UNIVERSE=config/universe_cac40.yaml SPLIT=2020-01-01
batch:
	uv run python -m src.engine.batch --universe-file $(UNIVERSE) --split-date $(SPLIT)

# Backtest sur l'univers entier de config/data.yaml, sans argument
# superflu : --universe-file n'est pas requis (src.engine.batch retombe
# sur universe_file de config/data.yaml).
# Exemple : make survey
# Exemple : make survey SPLIT=2020-01-01 STRATEGY=momentum_12_1
survey:
	uv run python -m src.engine.batch --split-date $(SPLIT) --strategy $(STRATEGY)

# Commande hebdomadaire unique : ingestion + batch sur config/weekly.yaml,
# rapport daté dans reports/, comparaison à l'exécution précédente. Même
# traitement du code 1 que la cible ingest (voir plus haut) : ni un
# échec ni un silence, juste "il y a quelque chose de nouveau à lire" —
# ici dans le rapport plutôt que dans la sortie de la commande.
weekly:
	@uv run python -m src.weekly; \
	code=$$?; \
	if [ $$code -eq 2 ]; then \
		exit 2; \
	elif [ $$code -eq 1 ]; then \
		echo ""; \
		echo "make : changements à lire (code 1) - voir la section Changements du rapport ci-dessus."; \
	fi

clean:
	rm -rf .pytest_cache data/cache data/*.duckdb
