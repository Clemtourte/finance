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
- **`src/strategies/`** — interface `Strategy` + croisement de SMA
  (référence), momentum 12-1 mono-actif, rééquilibrage par bandes ; DCA
  (apport programmé) via un moteur dédié minimal (pas via `Strategy`,
  voir plus bas).
- **`src/engine/`** — moteur de backtest (`vectorbt`), exécution J+1,
  coûts de transaction obligatoires, rééquilibrage par fréquence, runner
  batch sur un univers entier.
- **`src/metrics/`** — métriques de performance, friction, stratégie vs
  buy & hold, in-sample/out-of-sample.
- **`src/reporting/`** — tableaux de comparaison et récapitulatif batch
  (console + CSV).

Pas encore implémenté : optimisation/balayage de paramètres, walk-forward,
multi-actifs, stratégies short/à levier.

## Ce que ce projet fait — et ce qu'il ne fait pas

**Fait** : télécharge et valide des données OHLCV quotidiennes, teste si
une règle mécanique bat un simple achat-conservation (buy & hold) une
fois les coûts de transaction comptés, sur les mêmes données et dans les
mêmes conditions ; découpe systématiquement la mesure entre apprentissage
(in-sample) et vérification (out-of-sample) ; produit chaque semaine un
rapport daté qui dit ce qui a changé depuis la semaine précédente (voir
`make weekly` plus bas).

**Ne fait pas** :

- **Ne suit AUCUN portefeuille réel.** Aucune position détenue, aucun
  solde de compte, aucun historique d'ordres passés n'est représenté
  nulle part dans ce dépôt — `config/universe_perso.yaml` liste des
  tickers à évaluer, pas un état de compte (voir `docs/DECISIONS.md`).
- **Ne passe aucun ordre.** C'est un outil de mesure, pas d'exécution :
  rien ici ne parle à un courtier, aucune API de trading n'est branchée.
- **Ne décide rien.** `make weekly` produit un verdict par ticker
  (`SURVIT`/`REJETÉ`/`NON TESTABLE`) dans un rapport ; il ne déclenche
  aucune action. Agir sur un verdict — ou pas — reste un choix humain,
  fait ailleurs, après lecture du rapport.

## La chaîne complète

```
données (src/data/, make ingest)
  -> contrôle qualité (src/data/validation.py, ligne de base config/known_anomalies.yaml)
  -> stratégie (src/strategies/, position décidée à J, exécutée à l'ouverture de J+1)
  -> comparaison au buy & hold, mêmes coûts, mêmes dates (src/engine/, src/metrics/)
  -> rapport hebdomadaire daté (src/reporting/weekly_report.py, make weekly)
```

C'est l'enchaînement que `make weekly` exécute réellement chaque semaine
(voir « Rapport hebdomadaire » plus bas) ; chaque maillon est détaillé
dans sa propre section ci-dessous.

## Garde-fous (vue d'ensemble)

Chacun est expliqué en détail dans sa section propre — ce tableau sert de
repère pour quelqu'un qui n'a pas le temps de tout relire.

| Garde-fou | Empêche |
|---|---|
| Exécution décalée à J+1 (« Convention anti-look-ahead bias », ci-dessous) | Qu'un signal calculé avec la clôture du jour J s'exécute au prix de ce même jour J — impossible à reproduire en réalité. |
| Coûts de transaction obligatoires (« Modèle de coûts ») | De mesurer une performance qui n'existe que parce qu'on a oublié le courtage, la taxe ou le spread. |
| Découpage apprentissage / vérification (« In-sample / out-of-sample ») | De se convaincre qu'une stratégie marche en la jugeant sur les données mêmes qui ont servi à la choisir. |
| Verdict `NON TESTABLE` (« Garde-fou : période trop courte pour juger ») | De trancher SURVIT/REJETÉ sur une poignée de séances, où un seul mouvement de marché déciderait de tout. |
| `SURVIT` exige un gain réel (« Backtest sur tout un univers ») | De confondre « bat un buy & hold qui s'effondre » avec une vraie performance. |
| Ligne de base des anomalies (`config/known_anomalies.yaml`) | Qu'une anomalie déjà examinée noie, à chaque ingestion, la vraie nouveauté qu'il faut regarder. |
| Refus d'exécution rapprochée (`min_days_between_runs`, « Rapport hebdomadaire ») | Que le rattrapage du planificateur (PC éteint à l'heure prévue) déclenche plusieurs exécutions le même jour, qui s'écrasent l'une l'autre. |

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
  d'exécution (`base_slippage_pct`). `spread_pct` est le **demi-spread** :
  le coût supporté d'un seul côté de l'aller-retour (mesuré depuis un
  carnet d'ordres via `(vente - achat) / (vente + achat)`) ; appliqué
  symétriquement à l'achat et à la vente, le coût total d'un
  aller-retour vaut donc `2 x spread_pct`.

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

## Stratégies disponibles

Toutes les stratégies ci-dessous, sauf le DCA, implémentent `Strategy`
(`src/strategies/base.py`) et se branchent directement sur `run_backtest` :

- **`sma_crossover.py`** — croisement de deux SMA, long au-dessus, flat
  en-dessous. Stratégie de référence, sert de cas de test du moteur.
- **`momentum_12_1.py`** — long si le rendement sur 12 mois hors dernier
  mois est positif, flat sinon (mono-actif : comparaison à sa propre
  trajectoire passée, pas de classement contre un univers).
- **`rebalance_bandes.py`** — cible 100% investi ; le moteur ne
  supportant que des positions tout-ou-rien (pas de pondération
  fractionnaire multi-actifs), la bande s'applique à la dérive du prix
  depuis une référence : écrêtage (sortie) si la dérive dépasse
  `band_pct`, ré-entrée avec hystérésis quand le prix revient dans la
  bande de la référence d'origine.

**`dca.py`** (apport programmé mensuel) est différent : ce n'est pas une
`Strategy` (elle ne décide pas d'une position cible sur un capital fixe,
elle injecte un flux de capital externe et accumule des parts).
`vectorbt.Portfolio.from_signals` ne modélise pas proprement des dépôts
de cash périodiques ; le DCA est donc simulé par `simulate_dca`, un
moteur minimal dédié qui réutilise le modèle de coûts de
`src.engine.costs` mais ne passe pas par `run_backtest`. Achat au premier
jour coté de chaque mois (fait de calendrier connu à l'avance, pas un
signal), exécuté à l'`open` de ce jour.

## In-sample / out-of-sample

Le CLI (`src.engine.cli`) **exige** `--split-date YYYY-MM-DD` : les
métriques sont alors calculées et affichées séparément pour la période
avant la date de coupure (in-sample) et à partir de cette date
(out-of-sample), chacune avec son propre tableau stratégie vs buy & hold.
Un backtest sur la période complète sans regarder si la performance tient
hors échantillon est la manière la plus facile de se convaincre à tort
qu'une stratégie fonctionne — ce garde-fou empêche de l'oublier.

Le signal continue d'être calculé sur l'historique complet (les
indicateurs ont leur période de chauffe normale avant la coupure, pas un
démarrage à froid artificiel à la date de split) ; seul le **découpage
des métriques** se fait après coup, sur l'équité et le journal de trades
déjà produits par un backtest unique et continu
(`split_portfolio_by_date`). Un trade qui chevauche la date de coupure
est rattaché à la sous-période de sa date d'entrée.

Pour tourner sans découpage (déconseillé hors exploration rapide), passer
`--no-split` explicitement : la sortie l'indique alors clairement en tête
de rapport.

### Garde-fou : période trop courte pour juger

`SURVIT` et `REJETÉ` (voir plus bas) signifient tous deux « la
comparaison in-sample/out-of-sample a été faite ». Si l'in-sample ou
l'out-of-sample compte moins de `min_bars_per_period` séances
(`config/backtest.yaml`, défaut `500` ~ 2 ans), un seul mouvement de
marché isolé suffit à décider du résultat — ce n'est pas une
vérification, et un ticker qui a par exemple commencé à coter après la
date de coupure a un in-sample vide sans que rien ne le signale
autrement.

Le CLI mono-ticker (`--split-date`) reste un outil d'exploration : il
affiche un avertissement visible en tête de rapport, avant les tableaux,
quand l'un des deux côtés est sous le seuil, mais ne bloque pas
l'exécution. Le batch sur univers (`src.engine.batch`, voir plus bas),
lui, refuse de trancher : le verdict devient `NON TESTABLE`.

## Friction (courtage + TTF + spread)

Le CLI affiche, en tête de rapport, la friction cumulée de la stratégie
sur toute la période (`src.metrics.friction.compute_friction`) et le
turnover annualisé (`turnover_annualized`) ; les deux sont aussi ajoutés
au tableau de comparaison stratégie vs buy & hold (`friction_eur`,
`friction_pct_of_gross_gain`, `turnover_annualized`).

Aucune re-simulation n'est nécessaire : chaque euro de friction se
déduit exactement des colonnes du journal de trades `vectorbt` déjà
produit (`Size`, `Avg Entry/Exit Price`, `Entry/Exit Fees`) combinées à
la configuration de coûts d'origine — voir la docstring de
`compute_friction` pour le détail de la reconstruction (comment le
palier de courtage exact et la part TTF/courtage se retrouvent sans état
caché). Une position encore ouverte (buy & hold jamais soldé) a bien payé
sa friction d'entrée, comptée ; aucune friction de sortie fictive ne lui
est imputée. `friction_pct_of_gross_gain` vaut `n/a` quand le gain brut
(gain net + friction) n'est pas positif — le concept de "part du gain"
n'a alors pas de sens. Quand le gain brut est positif mais minuscule, le
ratio est calculable mais explose (plusieurs milliers de %) sans rien
dire de la réalité économique de la friction : au-delà de 1000%,
l'affichage (console uniquement, la valeur brute reste dans les CSV
exportés) passe à `n/s` plutôt que d'afficher un nombre à 5 chiffres qui
décrédibilise le rapport.

Le CLI résout `ttf`/`spread_pct` du ticker demandé depuis
`config/universe_cac40.yaml` (ou `--universe-file`) ; un ticker absent de
ce fichier retombe sur `ttf=False`/`spread_pct=0.0` avec un avertissement.

## Backtest sur tout un univers (batch)

`src/engine/batch.py` lance la même stratégie sur tous les tickers d'un
fichier d'univers et produit un récapitulatif, une ligne par ticker :

```bash
make batch UNIVERSE=config/universe_cac40.yaml SPLIT=2020-01-01
# équivalent :
uv run python -m src.engine.batch --universe-file config/universe_cac40.yaml --split-date 2020-01-01
```

Comme le CLI mono-ticker, `--split-date` est obligatoire (pas de
`--no-split` ici : le verdict n'a de sens que sur l'out-of-sample). Pour
chaque ticker : CAGR net de la stratégie et du buy & hold sur
l'out-of-sample, écart, friction en % du gain brut, et un verdict —
**`SURVIT`** si la stratégie bat le buy & hold net de coûts **et** affiche
elle-même un CAGR strictement positif sur l'out-of-sample, **`REJETÉ`**
sinon (y compris quand la comparaison est indéfinie : posture prudente,
cohérente avec l'objectif de falsification du projet). Un ticker non
encore ingéré (ou dont le backtest échoue pour toute autre raison) est
reporté **`ERREUR`** sans interrompre le run — utile pour lancer le batch
avant d'avoir ingéré tout l'univers.

La seconde condition de `SURVIT` (CAGR strictement positif) n'est pas
redondante avec la première : sur un titre en forte baisse, toute règle
qui passe du temps hors marché bat mécaniquement un buy & hold qui
s'effondre, sans que la stratégie ait généré le moindre gain — battre une
référence qui s'effondre n'est pas une performance, c'est une absence.
Quand la stratégie bat le buy & hold mais reste elle-même perdante,
`REJETÉ` (pas un cinquième verdict) avec le motif dans la colonne `Motif`,
ex. `Bat le buy & hold (-28.3%/an) mais perd de l'argent (-0.2%/an)`. Ce
garde-fou, comme le verdict lui-même, ne porte que sur l'out-of-sample.

Un ticker dont l'in-sample ou l'out-of-sample compte moins de
`min_bars_per_period` séances (voir « Garde-fou : période trop courte
pour juger » ci-dessus) est reporté **`NON TESTABLE`** — jamais `SURVIT`
ni `REJETÉ` — avec la raison dans la colonne `Motif` (quel côté, combien
de séances sur combien exigées) ; les colonnes de performance affichent
`n/a`. La ligne de synthèse compte les quatre verdicts séparément
(`SURVIT: … | REJETÉ: … | NON TESTABLE: … | ERREUR: …`).

## Rapport hebdomadaire (`make weekly` / `src.weekly`)

Lancer l'ingestion puis le batch séparément produit une sortie qui
disparaît à la fermeture du terminal. `make weekly` (`src.weekly`)
enchaîne les deux (ingestion sur l'univers de `config/weekly.yaml`, puis
le même batch que `make survey`) et écrit un rapport Markdown daté dans
`reports/AAAA-MM-JJ.md`, exécutable à la main aujourd'hui et par un
planificateur (cron) demain — voir SETUP.md pour l'usage courant et les
codes de sortie.

`src.weekly` n'implémente aucun calcul : c'est un pur orchestrateur qui
appelle `src.data.ingest.run_ingestion`, `src.data.baseline` et
`src.engine.batch.run_batch`, puis délègue le rendu à
`src.reporting.weekly_report`.

### Comparaison à l'exécution précédente

Même principe que la ligne de base des anomalies (`config/
known_anomalies.yaml`, voir plus haut) : un rapport identique chaque
semaine cesse d'être lu, et le jour où quelque chose change réellement,
personne ne le voit. Le rapport met donc en avant une section
**"Changements"**, en tête, qui liste uniquement ce qui a changé depuis
l'exécution précédente — le reste (données, tableau complet, pied de
page) n'est que du matériel de référence.

Pour savoir ce qui a changé, chaque exécution compare son résultat à
`data/last_verdicts.json` (`state_file`), écrit par la précédente :

- **verdict qui bascule** : un ticker dont le verdict diffère de celui
  enregistré (`SURVIT` -> `REJETÉ`, mais aussi `NON TESTABLE` <->
  testable, qui EST un changement — un ticker qui reste `NON TESTABLE`
  d'une exécution à l'autre n'en est pas un) ;
- **ticker apparu** : présent cette fois-ci, absent de l'état précédent
  (ex. ajouté à l'univers) ;
- **ticker disparu** : présent dans l'état précédent, absent cette
  fois-ci (ex. retiré de l'univers) — signalé explicitement, jamais
  simplement omis du rapport ;
- **anomalie vue pour la première fois** : absente à la fois de
  `config/known_anomalies.yaml` ET de l'état de la précédente exécution
  — voir la sous-section suivante, une anomalie hors ligne de base n'est
  pas automatiquement "nouvelle" indéfiniment.

Fichier absent (`data/last_verdicts.json` n'existe pas encore) =
première exécution : ce n'est pas une erreur, le rapport le dit
explicitement plutôt que de lister chaque ticker comme un faux
"changement" (il n'y a encore rien à comparer). L'état écrit à cette
occasion sert de référence à la prochaine exécution.

Quand il n'y a rien à signaler, le rapport se prononce explicitement sur
les deux sujets plutôt que de rester silencieux (un sujet dont on ne
parle pas se lit comme "pas vérifié", pas comme "rien à signaler") :

```
Aucune anomalie nouvelle depuis le 2026-07-24.
Aucun changement de verdict depuis le 2026-07-24.
```

### Anomalie nouvelle vs anomalie en attente d'examen

Une anomalie absente de `config/known_anomalies.yaml` n'est pas
forcément une anomalie qui vient d'apparaître : `config/
known_anomalies.yaml` n'est mis à jour qu'à la main (`--init-known-
anomalies` puis édition du champ `note`), donc une anomalie déjà
détectée reste hors ligne de base — et continuerait à ressortir comme
"nouvelle" à *chaque* exécution — tant que personne n'a pris le temps de
l'examiner et de la classer. C'est exactement le défaut que la ligne de
base sert déjà à éviter côté ingestion (voir "Codes de sortie de
l'ingestion" dans SETUP.md) ; sans distinction supplémentaire, le rapport
hebdomadaire y retombait telle quelle dès qu'une anomalie mettait plus
d'une semaine à être justifiée.

`data/last_verdicts.json` mémorise donc aussi, par anomalie identifiée
par le triplet `(ticker, kind, date)` (même convention d'identité que
`src.data.baseline.AnomalyKey`), sa date de première apparition :

- **absente de la ligne de base ET absente de l'état précédent** :
  vue pour la première fois -> section **"Changements"**, code de sortie
  `1`.
- **absente de la ligne de base MAIS déjà dans l'état précédent** :
  toujours pas examinée, mais ce n'est plus une nouveauté -> section
  **"En attente d'examen"** (après "Données"), avec la date de première
  apparition et le nombre de jours d'attente ; code de sortie **pas**
  affecté (reste `0` si c'est la seule chose en attente).
- **présente dans la ligne de base** : filtrée en amont par
  `src.data.baseline.filter_known`, comme pour l'ingestion — rien du
  tout dans le rapport.

Une anomalie ajoutée à `config/known_anomalies.yaml` entre deux
exécutions disparaît des deux sections au run suivant : `filter_known`
l'exclut avant même la comparaison, donc rien ne la fait réapparaître
comme "en attente" par erreur.

Le format de `state_file` reste rétrocompatible : la clé `"anomalies"`
est optionnelle — un fichier écrit avant cette fonctionnalité (qui ne la
contient pas) se charge sans erreur, et chaque anomalie hors ligne de
base y est alors simplement considérée comme vue pour la première fois à
l'exécution courante (jamais une erreur de chargement).

### Pourquoi `split_date` est figée dans `config/weekly.yaml`

Contrairement à `end_date: "today"` de `config/data.yaml` (qui avance
volontairement à chaque exécution — c'est la fraîcheur des données),
`split_date` ne bouge JAMAIS toute seule. Si elle avançait automatiquement
(ex. "il y a 2 ans" recalculé chaque semaine), la fenêtre in-sample/
out-of-sample changerait de taille et de contenu à chaque run pour une
raison purement technique (le calendrier a tourné), pas parce que le
marché a bougé. Un verdict qui bascule d'une semaine à l'autre — la seule
chose que la section "Changements" est censée signaler comme
significative — deviendrait alors illisible : impossible de savoir si
c'est le marché ou la fenêtre de mesure qui vient de changer. `split_date`
n'est donc avancée qu'à la main, consciemment, quand la fenêtre doit être
recalée.

### Refus d'une exécution trop rapprochée de la précédente

`make weekly` est prévue pour être planifiée (ex. planificateur de tâches
Windows) avec l'option de rattrapage : si le PC était éteint à l'heure
prévue, la tâche part au prochain démarrage. Plusieurs démarrages le même
jour peuvent alors déclencher plusieurs exécutions, qui retéléchargent,
recalculent et réécrivent chacune par-dessus le rapport du jour — le
rapport perd alors son sens de "point de comparaison hebdomadaire", et
plusieurs accès réseau inutiles sont faits pour rien.

`src.weekly` compare donc, AVANT toute ingestion, la date de la dernière
exécution enregistrée dans `data/last_verdicts.json` (`state_file`) à
aujourd'hui. Si l'écart est strictement inférieur à
`min_days_between_runs` (`config/weekly.yaml`, défaut `5`), l'exécution
est refusée : rien n'est téléchargé, rien n'est recalculé, aucun rapport
ni état n'est écrit, et le code de sortie reste `0` (un refus n'est pas
un échec). Le message affiché donne la date de la dernière exécution, le
nombre de jours écoulés, et la date à partir de laquelle une nouvelle
exécution sera acceptée. Absence de `state_file` (première exécution) ou
écart exactement égal au seuil : exécution normale dans les deux cas.

`5` plutôt que `7` (l'espacement hebdomadaire visé) : une exécution
rattrapée en milieu de semaine peut faire tomber l'échéance hebdomadaire
suivante seulement 5 jours plus tard. Un seuil à `7` la refuserait aussi,
et le rythme des exécutions dériverait alors d'une semaine à l'autre au
lieu de se recaler naturellement sur le jour habituel.

`--force` (`make weekly-force`) contourne entièrement ce contrôle, pour
un rattrapage manuel volontaire (ex. après une longue coupure) : le
rapport produit l'indique explicitement dans son pied de page
("Exécution FORCÉE"), pour qu'on puisse le distinguer plus tard d'un
rapport issu d'une exécution normale.

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
  paramètres de résilience yfinance, seuils de validation. `universe_file`
  y définit l'univers par défaut ; `--universe-file` du CLI d'ingestion
  permet de le remplacer ponctuellement (voir section suivante) sans
  éditer ce fichier, par exemple pour ingérer plusieurs univers l'un
  après l'autre dans une exécution automatisée.

Chaque entrée d'univers porte, en plus de `ticker`/`name`/`isin`, deux
champs consommés par le moteur de coûts (`src.engine.costs`) : `ttf`
(éligibilité à la taxe sur les transactions financières) et `spread_pct`
(demi-spread propre au titre — voir « Modèle de coûts » ci-dessous pour
la convention exacte). Voir les commentaires en tête de
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

Pour ingérer un univers différent de celui de `config/data.yaml` sans
éditer ce fichier (utile pour enchaîner plusieurs univers dans une
exécution automatisée) :

```bash
make ingest UNIVERSE=config/universe_etf_pea.yaml
# équivalent :
uv run python -m src.data.ingest --config config/data.yaml --universe-file config/universe_etf_pea.yaml
```

Par défaut, la plage antérieure à la première date déjà en cache pour un
ticker n'est **jamais** re-sondée : le cache traite sa borne basse comme
"le plus ancien que la source possède". Sans cette règle, un titre dont
l'historique yfinance démarre après `start_date` (ex. une petite
capitalisation introduite récemment) redemanderait cette plage vide à
chaque run, produirait une erreur "possibly delisted" **systématique**
sur ce ticker, et rendrait impossible de distinguer ce bruit attendu
d'une vraie radiation dans un job automatisé. Pour forcer cette
re-vérification (ex. la source a depuis publié un historique plus
profond que lors du premier run) :

```bash
uv run python -m src.data.ingest --config config/data.yaml --backfill
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

En fin d'exécution, un rapport de validation détaillé est affiché sur la
sortie standard (`src/reporting/validation.py`) : pour chaque ticker en
anomalie, la liste des séances concernées (trous avec leurs deux dates et
le nombre de jours calendaires, valeurs aberrantes avec leur date et leur
rendement journalier signé, splits suspects avec leur date et le ratio
reconnu), suivie d'un total par catégorie. Les logs (`logger.warning`) ne
donnent que des cardinalités par ticker ; ce rapport donne le détail
actionnable — quelles séances regarder — sans avoir à écrire un script ad
hoc pour inspecter les `DataFrame` du rapport. Passer `--quiet` pour le
supprimer (les logs restent inchangés) :

```bash
uv run python -m src.data.ingest --config config/data.yaml --quiet
```

Ce rapport ne contient que les anomalies **nouvelles** : `config/known_
anomalies.yaml` (voir `src/data/baseline.py`) tient la ligne de base des
anomalies déjà examinées et jugées authentiques (fermetures de marché,
volatilité réelle d'une small cap, etc.), identifiées par le triplet
`(ticker, kind, date)` — jamais par leurs valeurs (`adj_close`, rendement,
ratios), qui peuvent changer d'une ingestion à l'autre (un détachement de
dividende recalcule tout l'historique `adj_close` antérieur) sans que
l'anomalie elle-même soit nouvelle. Une anomalie à une date absente de la
ligne de base remonte toujours, même sur un ticker déjà largement
couvert. Sans ce filtrage, un job automatisé ne pourrait jamais alerter
sur une vraie nouveauté : l'état normal du pipeline est déjà bruyant
(volatilité small cap, fermetures calendaires), et une vraie radiation
produirait exactement le même type de message.

Pour initialiser la ligne de base depuis l'état courant (à éditer ensuite
à la main : chaque entrée générée porte `note: "À justifier"`) :

```bash
uv run python -m src.data.ingest --config config/data.yaml --init-known-anomalies
# --force pour écraser un fichier existant (sinon refusé, pour ne pas
# perdre des justifications déjà écrites à la main)
```

`--known-anomalies` change le chemin du fichier (défaut
`config/known_anomalies.yaml`). Le CLI se termine avec un code de sortie
distinguant les deux situations qu'un job automatisé ne doit jamais
confondre :

- **`0`** : ingestion réussie, aucune anomalie nouvelle.
- **`1`** : ingestion réussie, mais au moins une anomalie nouvelle
  détectée — il y a quelque chose à lire, rien n'est cassé.
- **`2`** : échec technique (exception pendant l'ingestion, ligne de base
  illisible, écriture impossible) — le pipeline lui-même est cassé.

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

Une fois le ticker ingéré (étape 2), lancez (voir [In-sample /
out-of-sample](#in-sample--out-of-sample) : `--split-date` est requis) :

```bash
make backtest TICKER=AI.PA SPLIT=2020-01-01
# équivalent :
uv run python -m src.engine.cli --ticker AI.PA --split-date 2020-01-01
```

Options utiles : `--strategy-config` (autre fichier de paramètres de
stratégie), `--start` / `--end` (surcharge de la période), `--output-csv`
(export du tableau de comparaison, un fichier par sous-période),
`--rebalance-freq`, `--no-split` (période complète, non recommandé).

Le CLI :

1. charge l'OHLCV du ticker depuis DuckDB ;
2. calcule les positions cibles de la stratégie configurée
   (`config/strategies/sma_crossover.yaml`) ;
3. exécute le backtest de la stratégie **et** un buy & hold de référence,
   avec les mêmes coûts (`config/backtest.yaml`) ;
4. affiche un tableau comparatif par sous-période (CAGR, volatilité,
   Sharpe, Sortino, max drawdown et sa durée, win rate, profit factor,
   nombre de trades, turnover — stratégie, buy & hold, écart).

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
  universe_cac40.yaml          # univers de tickers (CAC 40) + ttf/spread_pct
  universe_etf_pea.yaml        # univers d'ETF PEA (CW8/EWLD, PSP5, ETZ)
  universe_perso.yaml          # lignes réellement détenues (voir docs/DECISIONS.md #1, #6)
  universe_etude.yaml          # titres analysés mais NON détenus (voir docs/DECISIONS.md #6)
  known_anomalies.yaml         # ligne de base des anomalies déjà examinées et acceptées
  backtest.yaml                # capital, coûts par paliers, TTF, glissement, rééquilibrage
  weekly.yaml                  # univers/split_date (FIGÉE)/stratégie/chemins/min_days_between_runs de `make weekly`
  strategies/
    sma_crossover.yaml         # référence
    momentum_12_1.yaml
    rebalance_bandes.yaml
    dca.yaml
docs/
  AUTOMATISATION.md            # planification Windows de make weekly (script, tâche, alerte Uptime Kuma)
  DECISIONS.md                 # choix structurants et leur justification
src/
  data/
    schema.py                  # colonnes/dtypes OHLCV canoniques + convention anti-look-ahead
    provider.py                # interface abstraite DataProvider
    yfinance_provider.py       # implémentation yfinance
    cache.py                   # cache Parquet par ticker, calcul du delta manquant
    validation.py              # détection de trous / outliers / splits non ajustés
    duckdb_loader.py           # chargement (idempotent) + lecture (read_ohlcv) DuckDB
    config.py                  # chargement typé de config/data.yaml (TickerInfo : ttf, spread_pct)
    ingest.py                  # orchestration + CLI (`python -m src.data.ingest`)
  indicators/
    trend.py                   # sma, ema
    momentum.py                # rsi, macd
    volatility.py              # atr, bollinger_bands
  strategies/
    base.py                    # interface Strategy
    sma_crossover.py           # référence (croisement SMA)
    momentum_12_1.py           # long si rendement 12 mois hors dernier mois > 0
    rebalance_bandes.py        # cible 100% investi, écrêtage par bande de dérive
    dca.py                     # apport programmé : moteur dédié (pas une Strategy)
  engine/
    config.py                  # chargement typé de config/backtest.yaml
    costs.py                   # courtage par paliers, TTF, spread -> tableaux fees/vectorbt
    backtest.py                # exécution J+1, rééquilibrage, buy & hold de référence
    cli.py                     # CLI mono-ticker (`python -m src.engine.cli`), IS/OOS obligatoire
    batch.py                   # CLI univers entier (`python -m src.engine.batch`), verdict SURVIT/REJETÉ
  metrics/
    performance.py             # CAGR, vol, Sharpe, Sortino, drawdown, turnover (annualisé), split IS/OOS
    friction.py                 # reconstruction friction courtage/TTF/spread depuis le journal de trades
    comparison.py               # comparaison stratégie vs buy & hold, métrique par métrique
  reporting/
    table.py                   # tableau de comparaison + récapitulatif batch (console + CSV)
    validation.py               # rendu texte des rapports de validation (détail + synthèse)
    weekly_report.py            # rendu Markdown du rapport hebdomadaire (src.weekly)
  weekly.py                    # CLI hebdomadaire (`python -m src.weekly`) : ingestion + batch +
                                #   comparaison à l'exécution précédente + rapport daté
tests/
  test_cache.py, test_validation.py, test_config.py, test_ingest.py
  test_indicators.py           # dont le test de non-look-ahead (le plus important)
  test_strategies.py, test_momentum_12_1.py, test_rebalance_bandes.py, test_dca.py
  test_costs.py                # paliers de courtage, TTF, spread (fonctions pures)
  test_engine.py               # coûts, décalage d'exécution, rendement connu, rééquilibrage
  test_metrics.py, test_friction.py
  test_reporting.py, test_cli.py, test_batch.py, test_weekly.py
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

## Limites connues

Honnêteté délibérée : rien ci-dessous n'est caché ailleurs dans cette
documentation ou dans le code — c'est juste consolidé ici pour ne pas
avoir à le redécouvrir en relisant tout le projet dans six mois.

- **Moteur mono-actif : aucune notion de corrélation entre titres.**
  Chaque ticker de l'univers est backtesté indépendamment des autres
  (`momentum_12_1.py` le documente explicitement : « comparaison à sa
  propre trajectoire passée, pas de classement contre un univers »). Un
  `make survey` sur plusieurs titres ne dit donc rien de ce qui se
  passerait si tous étaient détenus ensemble au même moment (risque de
  concentration sectorielle, corrélation qui monte justement en période
  de stress...).
- **Positions tout ou rien, pas de pondération.** Le moteur
  (`src/engine/backtest.py`) ne connaît que 0% ou 100% investi sur un
  ticker donné — jamais une allocation fractionnaire entre plusieurs
  lignes. `rebalance_bandes.py` le contourne en interprétant sa « bande »
  comme une dérive de prix plutôt qu'un poids de portefeuille, faute de
  mieux (voir « Stratégies disponibles » ci-dessus).
- **TTF appliquée à 0,4% depuis 2010, alors qu'elle n'existait pas avant
  août 2012 et que son taux a évolué depuis.** `config/data.yaml` fixe
  `start_date: 2010-01-01` et `config/backtest.yaml` applique
  `ttf_pct: 0.004` à chaque achat éligible sur toute la période — y
  compris 2010-2012 (la taxe n'existait pas encore) et les années
  suivantes (taux différent de 0,4% à l'époque). L'erreur va dans le sens
  prudent : elle ne fait que pénaliser la stratégie testée, jamais
  l'inverse. Mais elle fausse quand même la comparaison sur les toutes
  premières années de la période — à garder en tête sur un `--split-date`
  ancien.
- **`spread_pct` des petites valeurs : des estimations non mesurées, qui
  décident pourtant du résultat.** `config/universe_perso.yaml` marque
  explicitement plusieurs valeurs `PLACEHOLDER GROSSIER — à mesurer` (ex.
  AL2SI.PA, ALKAL.PA). Sur ces titres à faible liquidité, c'est ce chiffre
  jamais confronté à un vrai carnet d'ordres qui pèse le plus dans le
  coût total — et donc dans le verdict.
- **Un résultat proche de zéro peut basculer de verdict sur quelques
  séances** — cas observé sur ALKAL.PA. Quand l'écart stratégie/buy &
  hold sur l'out-of-sample est de l'ordre de grandeur du bruit (quelques
  séances de plus ou de moins dans la fenêtre mesurée, ou une légère
  variation de `spread_pct`), le verdict SURVIT/REJETÉ peut changer sans
  qu'il se soit rien passé de significatif sur le marché. Un verdict dans
  ce cas est instable et ne devrait pas être pris au sérieux tel quel —
  il mérite de regarder l'écart réel de CAGR dans le tableau détaillé,
  pas seulement le mot du verdict.
- **La grille de courtage doit être revérifiée contre la grille réelle.**
  `config/backtest.yaml` documente la grille BoursoBank « Découverte »
  comme point de départ, explicitement marquée « à vérifier/ajuster » —
  elle n'a jamais été confrontée à la grille tarifaire réellement en
  vigueur au moment de chaque backtest.
- **Aucun test ne couvre l'accès réseau.** `make test` remplace
  systématiquement `YFinanceProvider` par un double en mémoire
  (`FakeProvider`, voir « Tests » ci-dessus) : la suite valide donc la
  logique du pipeline (calculs, garde-fous, comparaisons), jamais le fait
  que yfinance répond, renvoie un format stable dans le temps, ou reste
  disponible. Cette partie-là ne peut être vérifiée qu'en exécutant
  réellement `make ingest` / `make weekly`.
