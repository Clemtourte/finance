# SETUP — aide-mémoire opérationnel

Pense-bête pour quelqu'un qui a oublié les commandes. Pour le *pourquoi*
des choix de conception (modèle de coûts, in-sample/out-of-sample,
convention spread_pct...), voir le `README.md`.

## Les commandes du quotidien

```bash
# 1. Mettre à jour les données (delta uniquement, pas de re-téléchargement
#    de l'historique complet). Univers lu depuis config/data.yaml.
make update

# 2. Backtester UN ticker avec découpage in-sample/out-of-sample.
make test-one TICKER=AI.PA

# 3. Backtester une autre stratégie / un autre split sur ce ticker.
make test-one TICKER=AI.PA STRATEGY=momentum_12_1 SPLIT=2020-01-01

# 4. Backtester toute la config/universe_perso.yaml (ou l'univers de
#    config/data.yaml par défaut) et obtenir un verdict SURVIT/REJETÉ par ticker.
make survey

# 5. Lancer la suite de tests.
make check
```

Toutes les commandes ci-dessus sont des raccourcis `make` ; la commande
`uv run python -m ...` équivalente s'affiche avec `make -n <cible>` si tu
veux voir/adapter les arguments bruts (utile pour ajouter `--output-csv`,
`--no-split`, `--backfill`, etc., non exposés en variables Makefile).

## Codes de sortie de l'ingestion (`make update` / `src.data.ingest`)

| Code | Signification |
|------|---------------|
| `0`  | Ingestion réussie, **aucune anomalie nouvelle**. |
| `1`  | Ingestion réussie, mais **au moins une anomalie nouvelle** a été détectée — rien n'est cassé, il y a juste quelque chose à lire dans le rapport affiché. |
| `2`  | **Échec technique** : exception pendant l'ingestion, ligne de base des anomalies illisible, écriture impossible. |

Un job automatisé (cron, CI) doit alerter sur `2` toujours, et sur `1`
seulement s'il veut être notifié des nouvelles anomalies (elles ne
bloquent rien).

`make` traite tout code de sortie non nul comme un échec ("Error 1"), ce
qui casserait n'importe quelle cible enchaînant l'ingestion et autre
chose à chaque fois qu'il y a simplement quelque chose à signaler. Les
cibles `make ingest` / `make update` masquent donc volontairement le
code `1` : elles se terminent en succès et affichent une ligne indiquant
que des anomalies nouvelles ont été signalées et qu'il faut lire le
rapport affiché juste au-dessus. Le code `2` (échec technique), lui,
reste propagé tel quel et fait échouer `make`.

## Stratégies disponibles (`--strategy` / `STRATEGY=`)

| Nom (`--strategy`)  | Nom affiché          | Fichier de paramètres                          | Paramètres réglables |
|----------------------|----------------------|-------------------------------------------------|-----------------------|
| `sma_crossover`       | SMA crossover         | `config/strategies/sma_crossover.yaml`           | `fast_period`, `slow_period` |
| `momentum_12_1`       | Momentum 12-1         | `config/strategies/momentum_12_1.yaml`           | `lookback_days`, `skip_days` |
| `rebalance_bandes`    | Rebalance par bandes  | `config/strategies/rebalance_bandes.yaml`        | `band_pct` |

`dca` (`config/strategies/dca.yaml`, paramètre `monthly_amount`) n'est
**pas** utilisable via `test-one`/`survey` : ce n'est pas une `Strategy`
(pas de position cible sur capital fixe), elle a son propre moteur
(`src.strategies.dca.simulate_dca`), à invoquer directement en Python.

`--strategy-config` (CLI) surcharge le chemin du fichier YAML si tu veux
tester des paramètres sans toucher au fichier par défaut.

## Où éditer quoi

| Fichier | Sert à |
|---|---|
| `config/data.yaml` | Période d'historique, chemins de cache/DuckDB, univers par défaut (`universe_file`). |
| `config/universe_*.yaml` (ex. `universe_perso.yaml`) | Liste des tickers, `ttf` (éligibilité taxe transactions financières) et `spread_pct` (demi-spread, voir README) par titre. |
| `config/backtest.yaml` | Modèle de coûts (grille de courtage, TTF, glissement de base), capital initial, fréquence de rééquilibrage, `min_bars_per_period` (garde-fou NON TESTABLE, voir ci-dessous). |
| `config/strategies/*.yaml` | Paramètres de chaque stratégie (voir tableau ci-dessus). |
| `config/known_anomalies.yaml` | Ligne de base des anomalies de validation déjà examinées et acceptées (trous, valeurs aberrantes, splits suspects) — évite qu'elles ressortent comme "nouvelles" à chaque ingestion. Générer/mettre à jour avec `--init-known-anomalies` (voir README), puis éditer le champ `note` à la main. |

## Verdict NON TESTABLE (période insuffisante)

`SURVIT` et `REJETÉ` veulent tous deux dire « j'ai testé ». Si l'in-sample
ou l'out-of-sample d'un ticker compte moins de `min_bars_per_period`
séances (défaut : `500`, ~2 ans, réglable dans `config/backtest.yaml`), un
seul mouvement de marché suffit à décider du résultat : ce n'est pas une
vérification. Dans ce cas :

- `make survey` (`src.engine.batch`) rend un verdict **`NON TESTABLE`**
  pour ce ticker — jamais `SURVIT` ni `REJETÉ` — avec la raison (quel
  côté, combien de séances sur combien exigées) dans la colonne `Motif`
  du tableau ; les colonnes de performance affichent `n/a`. Un ticker
  `NON TESTABLE` n'entre dans aucun des deux autres compteurs de la ligne
  de synthèse (`SURVIT: … | REJETÉ: … | NON TESTABLE: … | ERREUR: …`).
- `make test-one` (`src.engine.cli`) affiche un avertissement visible en
  tête de rapport, avant les tableaux in-sample/out-of-sample, mais ne
  bloque pas l'exécution : c'est un outil d'exploration, pas un
  garde-fou de production.

Typiquement rencontré quand un ticker de l'univers a commencé à coter
après la date de coupure (`SPLIT=`) : son in-sample est vide ou très
court.

## SURVIT exige un gain réel, pas seulement "moins pire" que le buy & hold

Battre le buy & hold sur l'out-of-sample ne suffit pas à `SURVIT` : sur un
titre en forte baisse, toute règle qui passe du temps hors du marché bat
mécaniquement une référence qui s'effondre, sans avoir généré le moindre
gain — ce n'est pas une performance, c'est une absence. `src.engine.batch`
exige donc deux conditions cumulatives sur l'out-of-sample :

1. `strategy_cagr_oos > benchmark_cagr_oos` (condition historique) ;
2. `strategy_cagr_oos > 0` (la stratégie doit elle-même être rentable).

Si (1) est vraie mais (2) fausse, le verdict est **`REJETÉ`** (pas un
cinquième verdict) et la colonne `Motif` explique pourquoi, avec les deux
CAGR réels, ex. :

```
Bat le buy & hold (-28.3%/an) mais perd de l'argent (-0.2%/an)
```

## Friction non significative (`n/s`)

`friction_pct_of_gross_gain` (colonne `Friction %` de `make survey`, ligne
"Friction totale" de `make test-one`) peut afficher deux valeurs non
numériques, à ne pas confondre :

- `n/a` : non calculable — le gain brut (gain net + friction) n'est pas
  positif, le ratio "part du gain" n'a pas de sens.
- `n/s` : calculable, mais non significatif — le gain brut est positif
  mais minuscule, donc le ratio explose (ex. `20405.82%`) sans rien dire
  de la réalité économique de la friction. Affiché dès que le ratio
  dépasse 1000% (`src.reporting.table.format_friction_pct`). La valeur
  numérique brute reste inchangée dans les CSV exportés (`--output-csv`) ;
  seul l'affichage console est concerné.

## Avertissement : ne pas régler les paramètres sur l'out-of-sample

Ajuster `fast_period`/`slow_period`, `band_pct`, etc. en regardant si
l'out-of-sample s'améliore détruit la validité du découpage in-sample/
out-of-sample : l'out-of-sample devient alors un second in-sample déguisé,
et le verdict SURVIT/REJETÉ ne veut plus rien dire. Régler les paramètres
sur l'in-sample uniquement, ne consulter l'out-of-sample qu'une fois les
paramètres figés.
