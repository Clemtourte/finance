# DECISIONS — choix structurants et leur justification

Ce fichier existe pour éviter de refaire dans six mois un débat déjà
tranché. Chaque entrée décrit une décision qui aurait pu être prise
autrement, pourquoi elle ne l'a pas été, et — quand c'est pertinent — le
cas concret qui a motivé le choix. Pour le *comment* (commandes,
formats, codes de sortie), voir `README.md` et `SETUP.md` ; pour
l'automatisation Windows, voir `docs/AUTOMATISATION.md`.

## 1. Pas de suivi de portefeuille réel

D'autres outils le font mieux (TradingView pour le suivi, un tableur
pour l'écart d'allocation). Et le dépôt étant public, aucun montant,
quantité ou prix de revient ne doit s'y trouver. Seuls les symboles et
codes ISIN y figurent (`config/universe_perso.yaml`), qui ne sont pas
des informations sensibles. S'ajoute une limite technique : le moteur
est mono-actif et tout-ou-rien, il ne sait pas modéliser plusieurs
lignes pondérées simultanément — voir « Limites connues » dans
`README.md`.

## 2. Pas d'alerte sur un signal non falsifié

Un signal doit d'abord passer l'épreuve de la période de vérification
(out-of-sample) avant de justifier une alerte. Alerter sur un indicateur
dont on n'a pas mesuré la valeur prédictive, c'est fabriquer un signal,
pas le falsifier — l'inverse de l'objectif du projet (voir l'intro de
`README.md`). La fonction `rsi()` (`src/indicators/momentum.py`) existe
et n'est branchée à aucune stratégie : c'est délibéré, pas un oubli.

## 3. Date de coupure figée dans `config/weekly.yaml`

Si `split_date` avançait à chaque exécution, les résultats changeraient
pour une raison technique (le calendrier a tourné) et non parce que le
marché a bougé — on mesurerait notre propre réglage plutôt qu'une
stratégie. Elle est donc fixée une fois pour toutes (`2022-01-01`), sur
un critère purement matériel : c'est la date qui laisse au moins deux
ans de données de chaque côté au plus grand nombre de titres de
l'univers. Voir « Pourquoi `split_date` est figée » dans `README.md`.

## 4. Pas de relèvement des seuils de détection d'anomalies

Face à 18 anomalies signalées à chaque exécution, la solution facile
aurait été de monter les seuils de `src/data/validation.py` (7 jours de
trou au lieu de 5, 50% de variation au lieu de 30%). On aurait supprimé
le bruit et la capacité de détection avec : un vrai trou de 7 jours ou
une vraie donnée corrompue à 45% seraient devenus invisibles. La ligne
de base (`config/known_anomalies.yaml`) conserve toute la sensibilité
des seuils et ne masque que ce qui a été explicitement examiné et
justifié, anomalie par anomalie.

## 5. Trois niveaux d'anomalies plutôt que deux

Une anomalie non expliquée resterait signalée indéfiniment et le rapport
réclamerait l'attention chaque semaine pour la même chose — donc
cesserait d'être lu (voir « Anomalie nouvelle vs anomalie en attente
d'examen » dans `README.md`). Distinguer « nouvelle » (section
Changements, en tête, code de sortie `1`) de « en attente d'examen »
(plus bas, code `0`) garde la trace des questions ouvertes sans crier au
loup à chaque exécution.

## 6. Univers d'étude séparé de l'univers détenu

`config/universe_etude.yaml` contient des titres analysés mais non
possédés — à ne jamais confondre avec `config/universe_perso.yaml`
(lignes réellement en portefeuille). CW8 y sert de terrain d'essai pour
DCAM (même émetteur, même indice, même devise, 17 ans d'historique
contre 1,4 an pour DCAM, trop court pour être backtesté seul). Mais un
résultat obtenu sur CW8 ne se transpose **pas** tel quel à DCAM : frais
annuels et méthode de réplication diffèrent. Le fichier est séparé, avec
cette mise en garde en tête, précisément pour que cette distinction ne
se perde pas dans un univers unique mélangeant les deux usages.

## 7. `spread_pct` est le demi-spread

Le moteur (`src/engine/costs.py`) applique cette valeur à l'achat et à
la vente. Elle représente donc le coût supporté d'un seul côté de
l'aller-retour, et non l'écart complet entre meilleure offre et
meilleure demande — mesure de référence :
`(vente - achat) / (vente + achat)`. Le code était déjà correct sous
cette convention ; seule la documentation était ambiguë avant d'être
clarifiée. Le coût total d'un aller-retour vaut donc `2 x spread_pct`,
pas `spread_pct`.

## 8. `SURVIT` exige un gain réel

Battre une référence qui s'effondre ne suffit pas à mériter `SURVIT`.
Cas observé sur ALKAL.PA : la stratégie perdait 0,22%/an contre 28,32%/an
pour l'achat simple sur l'out-of-sample considéré, et ressortait pourtant
`SURVIT` sous l'ancienne règle à une seule condition. Sur tout titre en
forte baisse, n'importe quelle règle qui passe du temps hors du marché
gagne mécaniquement cette comparaison — ce n'est pas une performance,
c'est une absence. `SURVIT` exige désormais deux conditions cumulatives :
battre la référence **et** dégager un gain positif elle-même (voir
« SURVIT exige un gain réel » dans `SETUP.md`).

## 9. `NON TESTABLE` plutôt qu'un verdict par défaut

`SURVIT` et `REJETÉ` signifient tous deux « j'ai testé ». Quand une des
deux périodes (in-sample ou out-of-sample) est trop courte, rien n'a été
testé, et rendre quand même un de ces deux verdicts serait trompeur. Cas
observé : avec une coupure au `2018-01-01`, cinq titres sur sept
n'avaient aucune période d'apprentissage, et le batch rendait quand même
des verdicts `SURVIT`/`REJETÉ` sur cette base vide. Le seuil est de 500
séances (environ deux ans) de chaque côté de la coupure, réglable via
`min_bars_per_period` (`config/backtest.yaml`) — voir « Garde-fou :
période trop courte pour juger » dans `README.md`.

## 10. Planificateur Windows plutôt que cron

`cron` ne se déclenche que si WSL tourne à l'heure dite. Sur une machine
fréquemment éteinte, une exécution manquée serait silencieusement
perdue — pas de rattrapage, pas de trace de l'avoir manquée. Le
planificateur de tâches Windows offre le rattrapage au démarrage suivant
(`-StartWhenAvailable`), ce que `cron` ne sait pas faire nativement. Voir
`docs/AUTOMATISATION.md`.

## 11. Refus d'exécution rapprochée (5 jours, pas 7)

Le rattrapage du planificateur (décision 10) peut déclencher plusieurs
exécutions le même jour si la machine a redémarré plusieurs fois. Le
programme refuse donc de tourner (`src.weekly`) si la précédente
exécution date de moins de `min_days_between_runs` jours. Le seuil est à
5 et non 7 (l'espacement hebdomadaire visé) : si une exécution est
rattrapée un mercredi, l'échéance du lundi suivant n'est que 5 jours plus
tard ; un seuil à 7 la refuserait aussi, et le rythme des exécutions
dériverait alors semaine après semaine au lieu de se recaler
naturellement. Voir « Refus d'une exécution trop rapprochée » dans
`SETUP.md`.

## 12. Le système ne décide rien

Aucune connexion à un courtier, aucun ordre, aucune action automatisée
sur un compte réel. Il produit des rapports Markdown ; l'humain qui les
lit décide, ou pas. Cette limite est structurelle et volontaire, pas un
manque de temps — voir « Ce que ce projet fait — et ce qu'il ne fait
pas » dans `README.md`.
