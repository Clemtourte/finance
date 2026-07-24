# pea-backtest

Infrastructure de backtest d'analyse technique sur actions éligibles PEA
(Euronext Paris). **Objectif : falsifier des hypothèses de stratégie, pas
en fabriquer.** On mesure si une stratégie bat le buy & hold net de frais —
rien n'est pris pour acquis, et aucun résultat de stratégie n'est présenté
sans son buy & hold de référence calculé dans les mêmes conditions (mêmes
coûts, même période, même capital).

Couches implémentées à ce stade :

- **`src/data/`** — ingestion (yfinance), cache Parquet, validation,
  entrepôt DuckDB.
- **`src/indicators/`** — wrappers `pandas-ta` (SMA, EMA, RSI, MACD, ATR,
  bandes de Bollinger).
- **`src/strategies/`** — interface `Strategy` + stratégie de référence
  (croisement de SMA).
- **`src/engine/`** — moteur de backtest (`vectorbt`), exécution J+1,
  coûts de transaction obligatoires.
- **`src/metrics/`** — métriques de performance, stratégie vs buy & hold.
- **`src/reporting/`** — tableau de comparaison (console + CSV).

Pas encore implémenté : optimisation/balayage de paramètres, walk-forward,
multi-actifs, stratégies short/à levier.

## Convention anti-look-ahead bias

Cette règle s'applique à tout le pipeline et est appliquée en code, pas
seulement documentée :

> Un signal calculé à partir des données du jour J ne peut utiliser que ce
> qui est connu à la **clôture** de J. Toute exécution simulée d'un ordre
> déclenché par ce signal se fait à l'**ouverture de J+1**, jamais au
> close ou à l'open de J.

Comment c'est appliqué concrètement :

- **Indicateurs** (`src/indicators/`) : chaque wrapper `pandas-ta` est une
  fonction causale (moyenne/écart-type glissants) — la valeur à l'index J
  ne dépend que des observations jusqu'à J. Vérifié mécaniquement par
  `tests/test_indicators.py` : la valeur à J calculée sur la série complète
  doit être identique à celle calculée sur la série tronquée à J.
- **Stratégies** (`src/strategies/base.py`) : `Strategy.generate_signals`
  retourne une position *cible* décidée à la clôture de chaque date — elle
  ne matérialise aucun ordre elle-même.
- **Moteur** (`src/engine/backtest.py`) : `shift_to_execution` décale
  cette position cible d'un jour avant de la convertir en ordres
  d'entrée/sortie, exécutés au prix `open` (jamais `close`). Vérifié de
  bout en bout dans `tests/test_engine.py` (timestamps d'ordres réels via
  `vectorbt`).

Voir aussi la docstring de `src/data/schema.py`.

## Modèle de coûts

`src/engine/costs.py` modélise trois composantes cumulables, appliquées à
l'entrée ET à la sortie de chaque position (sauf mention contraire) :

- **Courtage par paliers** (`config/backtest.yaml`, grille BoursoBank
  "Découverte" par défaut : 1,99€ jusqu'à 500€, 0,60% au-delà — **à
  vérifier/ajuster** selon la grille tarifaire réellement en vigueur).
- **TTF** (taxe sur les transactions financières), 0,4% à l'**achat
  uniquement**, seulement si le titre est marqué `ttf: true` dans son
  fichier d'univers (grandes capitalisations françaises).
- **Spread par titre** (champ `spread_pct` de l'univers), modélisé comme
  un glissement additionnel symétrique, cumulé au glissement générique
  d'exécution (`base_slippage_pct`).

Comme le courtage dépend du montant de chaque ordre (pas un taux plat),
`build_order_cost_arrays` simule séquentiellement les allers-retours
pour déterminer, ordre par ordre, le palier applicable et le capital
réellement disponible, puis fournit à `vectorbt` des tableaux `fees` /
`fixed_fees` / `slippage` par barre plutôt qu'un scalaire unique.

## Fréquence de rééquilibrage

Par défaut (`rebalance_freq: daily`), un changement de position cible est
appliqué dès qu'il est décidé. Avec `weekly` ou `monthly`
(`config/backtest.yaml`, ou `--rebalance-freq` en CLI), un changement de
position cible n'est pris en compte qu'à la dernière séance cotée de la
période — le signal reste calculé quotidiennement par la stratégie, seule
son application est échantillonnée (`resample_target_position`). Une
oscillation intra-semaine/mois qui revient à son état de départ à la fin
de la période ne génère donc aucun ordre. La date de fin de période se
déduit uniquement du calendrier de cotation déjà connu, jamais d'une
valeur future : aucun impact sur la convention d'exécution J -> J+1, qui
s'applique ensuite normalement à la position échantillonnée.

## Installation

Prérequis : [uv](https://docs.astral.sh/uv/). `uv sync` télécharge
automatiquement l'interpréteur Python 3.12 fixé dans `.python-version` (le
projet requiert 3.12, cf. compatibilité `pandas-ta` / `vectorbt` plus bas)
et crée un environnement virtuel `.venv`.

```bash
uv sync
```

## Utilisation

### 1. Configurer l'univers et la période

- `config/universe_cac40.yaml` — liste des 40 tickers du CAC 40 (format
  Yahoo Finance, suffixe `.PA`). La composition d'un indice change chaque
  trimestre : à revérifier périodiquement.
- `config/universe_etf_pea.yaml` — 4 ETF PEA (CW8/EWLD monde, PSP5
  S&P 500, ETZ Europe).
- `config/data.yaml` — période d'historique, chemins de cache/DuckDB,
  paramètres de résilience yfinance, seuils de validation. `--config`
  du CLI d'ingestion pointe toujours vers un seul fichier d'univers à la
  fois (`universe_file`) ; pour ingérer les deux univers, ingérer l'un
  puis l'autre en changeant temporairement `universe_file`, ou passer le
  fichier voulu directement à `load_universe`.

Chaque entrée d'univers porte, en plus de `ticker`/`name`/`isin`, deux
champs consommés par le moteur de coûts (`src.engine.costs`) : `ttf`
(éligibilité à la taxe sur les transactions financières) et `spread_pct`
(spread bid/ask propre au titre). Voir les commentaires en tête de
chaque fichier YAML pour la méthode et les limites de ces valeurs — **à
vérifier avant tout usage réel**, ce ne sont que des points de départ.

Aucune valeur n'est codée en dur dans `src/` : tout est lu depuis ces
fichiers.

### 2. Lancer l'ingestion

```bash
make ingest
# équivalent :
uv run python -m src.data.ingest --config config/data.yaml
```

Pour chaque ticker de l'univers :

1. calcule la portion de l'intervalle `[start_date, end_date]` **absente**
   du cache local (`data/cache/<ticker>.parquet`) ;
2. ne télécharge que ce delta via yfinance (un run répété n'effectue aucun
   nouvel appel réseau si le cache est déjà à jour) ;
3. fusionne le delta dans le cache Parquet (dédoublonnage par date) ;
4. exécute les contrôles de qualité (`src/data/validation.py`) et logue
   les anomalies éventuelles ;
5. recharge l'historique complet du ticker dans DuckDB
   (`data/warehouse.duckdb`, table `ohlcv_daily`).

### 3. Interroger l'entrepôt

```python
import duckdb

conn = duckdb.connect("data/warehouse.duckdb")
conn.sql("""
    SELECT ticker, date, close, adj_close
    FROM ohlcv_daily
    WHERE ticker = 'AI.PA'
    ORDER BY date DESC
    LIMIT 5
""").show()
```

### 4. Utiliser la couche données dans du code Python

```python
from datetime import date

from src.data.cache import ParquetCache
from src.data.ingest import sync_ticker
from src.data.provider import DataProvider  # interface, pour brancher un autre provider
from src.data.validation import validate_ohlcv
from src.data.yfinance_provider import YFinanceProvider

provider = YFinanceProvider()
cache = ParquetCache("data/cache")

df = sync_ticker(provider, cache, "AI.PA", date(2020, 1, 1), date.today())
report = validate_ohlcv(
    df.to_pandas(),
    ticker="AI.PA",
    max_gap_calendar_days=5,
    outlier_return_threshold=0.30,
    split_ratio_tolerance=0.03,
)
print(report.has_issues, report.gaps, report.outliers, report.unadjusted_splits)
```

### 5. Lancer un backtest complet sur un ticker

Une fois le ticker ingéré (étape 2), lancez :

```bash
make backtest TICKER=AI.PA
# équivalent :
uv run python -m src.engine.cli --ticker AI.PA
```

Options utiles : `--strategy-config` (autre fichier de paramètres de
stratégie), `--start` / `--end` (surcharge de la période), `--output-csv`
(export du tableau de comparaison).

Le CLI :

1. charge l'OHLCV du ticker depuis DuckDB ;
2. calcule les positions cibles de la stratégie configurée
   (`config/strategies/sma_crossover.yaml`) ;
3. exécute le backtest de la stratégie **et** un buy & hold de référence,
   avec les mêmes coûts (`config/backtest.yaml`) ;
4. affiche un tableau comparatif (CAGR, volatilité, Sharpe, Sortino, max
   drawdown et sa durée, win rate, profit factor, nombre de trades,
   turnover — stratégie, buy & hold, écart).

### 6. Utiliser indicateurs / stratégies / moteur dans du code Python

```python
from src.data.duckdb_loader import read_ohlcv
from src.engine.backtest import run_backtest, run_buy_and_hold
from src.engine.config import load_backtest_config
from src.metrics.comparison import compare
from src.metrics.performance import compute_metrics
from src.reporting.table import format_comparison_table
from src.strategies.sma_crossover import SmaCrossoverStrategy

df = read_ohlcv("data/warehouse.duckdb", "ohlcv_daily", "AI.PA")

strategy = SmaCrossoverStrategy(fast_period=20, slow_period=50)
target_position = strategy.generate_signals(df)

backtest_config = load_backtest_config("config/backtest.yaml")
strategy_pf = run_backtest(df, target_position, backtest_config.costs, backtest_config.initial_capital)
benchmark_pf = run_buy_and_hold(df, backtest_config.costs, backtest_config.initial_capital)

strategy_metrics = compute_metrics(strategy_pf, backtest_config.trading_days_per_year, backtest_config.risk_free_rate)
benchmark_metrics = compute_metrics(benchmark_pf, backtest_config.trading_days_per_year, backtest_config.risk_free_rate)

print(format_comparison_table(compare(strategy_metrics, benchmark_metrics)))
```

Les indicateurs (`src/indicators/trend.py`, `momentum.py`, `volatility.py`)
s'utilisent directement sur une Series/DataFrame OHLCV :

```python
from src.indicators.trend import sma
from src.indicators.momentum import rsi, macd
from src.indicators.volatility import atr, bollinger_bands

df["sma_20"] = sma(df["close"], length=20)
df["rsi_14"] = rsi(df["close"], length=14)
macd_lines = macd(df["close"], fast=12, slow=26, signal=9)  # colonnes: macd, signal, hist
bands = bollinger_bands(df["close"], length=20, std=2.0)     # colonnes: lower, mid, upper
```

## Tests

```bash
make test
# équivalent :
uv run pytest
```

Les tests ne font aucun appel réseau (un `FakeProvider` en mémoire
remplace `YFinanceProvider` dans `tests/test_ingest.py`) et utilisent des
répertoires temporaires (`tmp_path`) pour le cache et DuckDB. Les valeurs
attendues des tests de moteur et de métriques sont calculées à la main
(formules fermées), pas en ré-exécutant le même code sous une autre forme.

> `make` n'est pas installé nativement sur Windows. Sans lui, utilisez
> directement les commandes `uv run ...` listées ci-dessus (ou installez
> GNU Make via `choco install make` / `scoop install make`).

## Architecture

```
config/
  data.yaml                    # période, chemins, seuils de validation
  universe_cac40.yaml          # univers de tickers (CAC 40)
  backtest.yaml                # capital initial, coûts de transaction, annualisation
  strategies/
    sma_crossover.yaml         # paramètres de la stratégie de référence
src/
  data/
    schema.py                  # colonnes/dtypes OHLCV canoniques + convention anti-look-ahead
    provider.py                # interface abstraite DataProvider
    yfinance_provider.py       # implémentation yfinance
    cache.py                   # cache Parquet par ticker, calcul du delta manquant
    validation.py              # détection de trous / outliers / splits non ajustés
    duckdb_loader.py           # chargement (idempotent) + lecture (read_ohlcv) DuckDB
    config.py                  # chargement typé de config/data.yaml
    ingest.py                  # orchestration + CLI (`python -m src.data.ingest`)
  indicators/
    trend.py                   # sma, ema
    momentum.py                # rsi, macd
    volatility.py              # atr, bollinger_bands
  strategies/
    base.py                    # interface Strategy
    sma_crossover.py           # stratégie de référence (croisement SMA)
  engine/
    config.py                  # chargement typé de config/backtest.yaml
    backtest.py                # exécution J+1, coûts obligatoires, buy & hold de référence
    cli.py                     # CLI (`python -m src.engine.cli`)
  metrics/
    performance.py             # CAGR, vol, Sharpe, Sortino, drawdown, win rate, profit factor, turnover
    comparison.py               # comparaison stratégie vs buy & hold, métrique par métrique
  reporting/
    table.py                   # tableau console + export CSV
tests/
  test_cache.py, test_validation.py, test_config.py, test_ingest.py
  test_indicators.py           # dont le test de non-look-ahead (le plus important)
  test_strategies.py
  test_engine.py               # coûts, décalage d'exécution, rendement connu
  test_metrics.py
  test_reporting.py
  test_cli.py
```

## Pourquoi Python 3.12 (et pas 3.11 ou plus récent) ?

`requires-python` est fixé à `>=3.12,<3.13` : `pandas-ta` exige Python
≥3.12, et `vectorbt`/`numba` (moteur de backtest) ne suivent généralement
pas immédiatement les dernières versions de Python. `uv sync` gère le
téléchargement de l'interpréteur 3.12 automatiquement, indépendamment de
la version de Python déjà installée sur la machine.

## Brancher un autre provider (EODHD, Tiingo, ...)

Implémenter `DataProvider.get_ohlcv(ticker, start, end) -> pd.DataFrame`
(voir `src/data/provider.py` et `src/data/yfinance_provider.py` comme
référence), puis passer une instance de ce provider à `sync_ticker` /
`run_ingestion` à la place de `YFinanceProvider`. Rien d'autre dans le
pipeline (cache, validation, DuckDB) n'a besoin de changer.
