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

# 5. Commande hebdomadaire unique : ingestion + batch sur l'univers de
#    config/weekly.yaml, rapport daté écrit dans reports/. Voir la
#    section "make weekly" ci-dessous.
make weekly

# 5bis. Comme make weekly, mais contourne le refus d'exécution trop
#       rapprochée de la précédente (voir "Refus d'une exécution trop
#       rapprochée" ci-dessous) — rattrapage manuel volontaire.
make weekly-force

# 6. Lancer la suite de tests.
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
| `config/weekly.yaml` | Univers, date de coupure (`split_date`, FIGÉE — voir ci-dessous), stratégie, répertoires de rapport/état, `min_days_between_runs` (voir "Refus d'une exécution trop rapprochée" ci-dessous) de `make weekly`. |

## make weekly : rapport hebdomadaire daté

`make weekly` (`src.weekly`) est la commande à planifier (cron, ou à la
main) : elle enchaîne ingestion + `make survey` sur l'univers de
`config/weekly.yaml`, puis écrit un rapport Markdown daté au lieu
d'afficher un résultat qui disparaît à la fermeture du terminal.

- **Où atterrit le rapport** : `reports/AAAA-MM-JJ.md` (`reports_dir` de
  `config/weekly.yaml`), un fichier par exécution. `reports/` n'est pas
  versionné (voir `.gitignore`) : c'est une sortie produite, pas une
  décision humaine — contrairement à `config/known_anomalies.yaml`.
- **Comment lire le rapport** : ne lire que la section **"Changements"**,
  en tête. Une semaine normale, elle affiche deux lignes de confirmation
  explicites ("Aucune anomalie nouvelle depuis le AAAA-MM-JJ.", "Aucun
  changement de verdict depuis le AAAA-MM-JJ.") et il n'y a rien d'autre
  à faire. Les sections "Données", "En attente d'examen" (voir
  ci-dessous), "Résultats" (le tableau complet, comme `make survey`) et
  le pied de page ne sont que du matériel de référence, à consulter
  seulement si la section "Changements" pointe vers quelque chose de
  précis (un ticker, une anomalie).
- **Premier lancement** : `data/last_verdicts.json` (`state_file`)
  n'existe pas encore — la section "Changements" le dit explicitement
  ("Première exécution") plutôt que de lister chaque ticker comme un
  faux "changement". L'état écrit à cette occasion sert de référence à
  la prochaine exécution.
- **Anomalie nouvelle vs anomalie en attente** : une anomalie hors ligne
  de base (`config/known_anomalies.yaml`) mais pas encore justifiée
  n'est signalée dans "Changements" (et ne déclenche le code 1) que la
  **première fois** qu'elle apparaît. Aux exécutions suivantes, tant
  qu'elle n'est ni expliquée ni disparue, elle bascule dans la section
  **"En attente d'examen"** — visible avec sa date de première apparition
  et le nombre de jours d'attente, mais sans redéclencher le code 1 à
  chaque fois. Sinon une anomalie non encore examinée réclamerait
  l'attention indéfiniment, jusqu'à ce que le rapport cesse d'être lu —
  exactement ce que la ligne de base sert déjà à éviter côté ingestion
  (voir "Codes de sortie de l'ingestion" ci-dessus). L'ajouter à
  `config/known_anomalies.yaml` (avec une vraie justification) la fait
  disparaître des deux sections au run suivant.

Codes de sortie (même logique que `make update`/`src.data.ingest`, voir
ci-dessus) :

| Code | Signification |
|------|---------------|
| `0`  | Rapport écrit, **aucun changement** à lire (des anomalies peuvent rester en attente d'examen — voir plus bas — sans faire échouer ce code). **Ou** exécution refusée (voir ci-dessous) — pas d'échec non plus. |
| `1`  | Rapport écrit, **au moins un changement** à lire (anomalie vue pour la **première fois** et/ou changement de verdict/apparition/disparition d'un ticker) — rien n'est cassé, `make weekly` invite juste à ouvrir le rapport. |
| `2`  | **Échec technique** : exception, ligne de base illisible, `data/last_verdicts.json` corrompu, ou rapport/état impossible à écrire (un rapport non écrit est un échec, jamais un silence). |

### Refus d'une exécution trop rapprochée

`make weekly` est destinée à être planifiée (ex. planificateur de tâches
Windows) avec rattrapage : si le PC était éteint à l'heure prévue, la
tâche part au démarrage suivant. Plusieurs démarrages le même jour
déclenchent alors plusieurs exécutions, chacune retéléchargeant,
recalculant et réécrivant par-dessus le rapport du jour.

`src.weekly` refuse donc de tourner si `data/last_verdicts.json`
(`state_file`) indique une exécution précédente vieille de **moins de
`min_days_between_runs` jours** (`config/weekly.yaml`, défaut `5`) :
aucun téléchargement, aucun calcul, aucun rapport ni état écrit — juste
un message (date de la dernière exécution, jours écoulés, date de la
prochaine exécution acceptée) et le code de sortie `0`. Le contrôle
s'exécute **avant** l'ingestion : un refus ne provoque donc aucun accès
réseau. Fichier d'état absent (première exécution) : pas de refus
possible, ce n'est pas une erreur.

Seuil à `5` plutôt que `7` (l'espacement hebdomadaire visé) : si une
exécution part en rattrapage au milieu de la semaine, l'échéance
hebdomadaire suivante peut tomber seulement 5 jours plus tard ; un seuil
à `7` la refuserait aussi, et le rythme dériverait d'une semaine à
l'autre au lieu de se recaler.

Pour un rattrapage manuel volontaire malgré ce refus : `make weekly-force`
(`src.weekly --force`). Le rapport produit l'indique dans son pied de
page ("Exécution FORCÉE"), pour qu'on puisse le distinguer plus tard des
rapports issus d'une exécution normale.

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
