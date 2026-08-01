# AUTOMATISATION — planification Windows de `make weekly`

Ce document décrit tout ce qui fait tourner `make weekly` sans
intervention chaque semaine : la tâche planifiée Windows, le script
PowerShell qui fait le pont vers WSL, et l'alerte Uptime Kuma qui
prévient si le rapport ne s'est pas produit. Rien de tout ça n'est dans
le code Python du dépôt — c'est de la configuration propre à la machine
Windows, reproduite ici pour qu'elle survive à un changement de machine
ou à six mois d'oubli. Pour ce que fait `make weekly` lui-même (garde-
fous, codes de sortie, format du rapport), voir `README.md` et
`SETUP.md` ; ce document-ci ne couvre que ce qui l'entoure.

## Vue d'ensemble

```
Planificateur de tâches Windows (déclenche à heure fixe, avec rattrapage)
  -> script PowerShell (weekly.ps1)
    -> WSL (bash -lc, environnement de connexion complet)
      -> make weekly (src.weekly, voir README.md)
        -> rapport Markdown daté (reports/AAAA-MM-JJ.md, dans WSL)
        -> signal Uptime Kuma (up/down, selon le code de sortie)
```

Le planificateur Windows ne sait pas parler à WSL directement : le
script PowerShell est l'intermédiaire qui lance `make weekly` dans WSL,
capture tout ce qu'il produit dans un journal, puis prévient Uptime Kuma
du résultat. Le seul état qui survit à une exécution est du côté WSL
(`reports/`, `data/last_verdicts.json`) — Windows ne fait que déclencher
et journaliser.

## Le script PowerShell

Emplacement : `C:\Users\<utilisateur>\scripts\weekly.ps1`.

```powershell
$env:WSL_UTF8 = 1
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"

$pushUrl = "http://localhost:3001/api/push/CLE_UPTIME_KUMA"

$logDir = "$env:USERPROFILE\scripts\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stamp   = Get-Date -Format "yyyy-MM-dd_HHmmss"
$logFile = "$logDir\weekly_$stamp.log"

"=== Demarrage : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" |
    Out-File -FilePath $logFile -Encoding utf8

wsl -d Ubuntu -- bash -lc "cd ~/dev/finance && make weekly" *>&1 |
    Out-File -FilePath $logFile -Append -Encoding utf8

$code = $LASTEXITCODE

"=== Fin : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - code $code ===" |
    Out-File -FilePath $logFile -Append -Encoding utf8

if ($code -eq 0) {
    $status = "up"
    $msg    = "OK"
} else {
    $status = "down"
    $msg    = "Echec du rapport hebdomadaire (code $code) - voir $logFile"
}

try {
    $encoded = [System.Uri]::EscapeDataString($msg)
    Invoke-RestMethod -Uri "$pushUrl`?status=$status&msg=$encoded" -TimeoutSec 15 | Out-Null
    "Signal Uptime Kuma envoye : $status" |
        Out-File -FilePath $logFile -Append -Encoding utf8
} catch {
    "Echec envoi signal Uptime Kuma : $_" |
        Out-File -FilePath $logFile -Append -Encoding utf8
}

Get-ChildItem "$logDir\weekly_*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 20 |
    Remove-Item -Force

exit $code
```

`CLE_UPTIME_KUMA` est un remplaçant : la vraie clé Uptime Kuma ne doit
**jamais** être committée dans ce dépôt, qui est public — c'est un
jeton d'écriture équivalent à un mot de passe. Elle vit uniquement dans
la copie locale du script, hors du contrôle de version.

### Points non évidents

- **`$env:WSL_UTF8 = 1` ET `[Console]::OutputEncoding = ...UTF8`** : les
  deux sont nécessaires pour que les accents des messages français
  arrivent lisibles dans le journal — testé : `WSL_UTF8` seul ne
  suffit pas, PowerShell réinterprète quand même la sortie de `wsl`
  avec son encodage console par défaut.
- **`bash -lc` (pas juste `bash -c`)** : le `-l` charge l'environnement
  de connexion complet (`.bashrc`/`.profile`, `PATH`). Sans lui, `uv`
  est introuvable dans le shell non interactif lancé par `wsl`, et la
  tâche échoue systématiquement — pas un échec du programme, un échec
  de l'environnement qui l'entoure.
- **`*>&1`** : redirige tous les flux (sortie normale, erreurs,
  avertissements...) vers le même journal, pas seulement la sortie
  standard. C'est précisément ce qu'on veut pouvoir lire quand quelque
  chose casse — une erreur qui n'atterrirait que dans stderr serait
  autrement invisible dans le fichier.
- **Le code 1 (« des choses à lire ») compte comme un succès.** Seul le
  code 2 (échec technique, voir `SETUP.md`) fait passer `$status` à
  `"down"` et déclenche l'alerte — un rapport avec des changements à
  lire n'est pas une panne, `make weekly` retourne bien `0` ou `1` dans
  ce cas et l'un et l'autre valent `status=up` ici (voir Makefile,
  cible `weekly`, qui absorbe déjà le code 1).
- **L'envoi du signal est enveloppé dans un `try`/`catch`.** Si Uptime
  Kuma lui-même est arrêté ou injoignable, le script continue et écrit
  quand même son journal, avec le code de sortie correct — une sonnette
  cassée ne doit pas empêcher le travail de se faire.
- **Rotation des journaux** : seuls les 20 fichiers les plus récents de
  `logs\weekly_*.log` sont conservés à chaque exécution, pour ne pas
  accumuler indéfiniment.
- **`-ExecutionPolicy Bypass`** (dans les arguments de la tâche
  planifiée, pas dans le script) : Windows bloque l'exécution des
  scripts `.ps1` par une politique d'exécution restrictive par défaut.
  Contourné uniquement pour cette invocation précise, pas globalement
  sur la machine.

## Création de la tâche planifiée

À lancer dans PowerShell **en administrateur**.

```powershell
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$env:USERPROFILE\scripts\weekly.ps1`""

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "09:00"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "pea-backtest weekly" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Rapport hebdomadaire pea-backtest (WSL)"
```

Rôle de chaque réglage :

| Réglage | Rôle |
|---|---|
| `-StartWhenAvailable` | **Le réglage essentiel.** Si la machine était éteinte à l'heure prévue (lundi 09:00), la tâche part au prochain démarrage au lieu d'être simplement sautée. C'est ce qui rend tout le dispositif utilisable sur une machine fréquemment hors tension — sans lui, une semaine éteinte serait une semaine sans rapport, silencieusement. |
| `-AllowStartIfOnBatteries` / `-DontStopIfGoingOnBatteries` | Sans eux, Windows refuse de lancer la tâche (ou l'interrompt en cours) si la machine est sur batterie — comportement par défaut pensé pour économiser l'énergie, inadapté ici. |
| `-ExecutionTimeLimit (New-TimeSpan -Minutes 30)` | Évite qu'un blocage (réseau qui ne répond jamais, etc.) fasse tourner la tâche indéfiniment. |
| `-MultipleInstances IgnoreNew` | Second garde-fou contre les exécutions simultanées, au niveau de Windows : si une instance tourne déjà, une nouvelle invocation est ignorée. Le premier garde-fou est dans le programme lui-même (`min_days_between_runs`, voir README.md/SETUP.md) — celui-ci protège contre le chevauchement de deux exécutions en cours, pas contre deux exécutions trop rapprochées dans le temps. |

Vérification de l'état de la tâche :

```powershell
Get-ScheduledTaskInfo -TaskName "pea-backtest weekly" |
    Select-Object LastRunTime, NextRunTime, LastTaskResult
```

Une tâche jamais encore exécutée affiche `LastRunTime = 30/11/1999` et
un `LastTaskResult` non nul : c'est l'état par défaut de Windows pour
« jamais lancée », pas une erreur.

Déclenchement manuel, pour tester sans attendre lundi 09:00 :

```powershell
Start-ScheduledTask -TaskName "pea-backtest weekly"
```

## Désactiver ou supprimer la tâche

Désactiver temporairement (la tâche reste configurée, ne se déclenche
plus) :

```powershell
Disable-ScheduledTask -TaskName "pea-backtest weekly"
```

Réactiver :

```powershell
Enable-ScheduledTask -TaskName "pea-backtest weekly"
```

Supprimer définitivement :

```powershell
Unregister-ScheduledTask -TaskName "pea-backtest weekly" -Confirm:$false
```

Dans les deux cas, penser à désactiver aussi le moniteur Uptime Kuma
correspondant (Edit → Pause) — sinon il continuera de compter les jours
sans signal jusqu'à déclencher une fausse alerte au bout de 10 jours.

## Uptime Kuma

Moniteur de type **Push** : c'est le script qui signale sa propre
présence, pas Uptime Kuma qui va vérifier activement quoi que ce soit
(il n'y a rien à interroger côté WSL).

| Réglage | Valeur |
|---|---|
| Monitor Type | Push |
| Friendly Name | `pea-backtest weekly` |
| Heartbeat Interval | `864000` secondes (10 jours) |
| Retries | `0` |

Uptime Kuma fournit une URL de la forme
`http://localhost:3001/api/push/<CLE>` ; seule la partie `<CLE>` va dans
le script (variable `$pushUrl`) — les paramètres `?status=...&msg=...`
sont ajoutés par le script lui-même à chaque envoi, pas fixés dans l'URL
de base.

**Pourquoi 10 jours et pas 7** : le rapport tourne une fois par semaine,
mais un décalage d'un ou deux jours est normal (machine éteinte le
lundi, rattrapage le mardi grâce à `-StartWhenAvailable`). Un délai
réglé sur 7 jours produirait de fausses alertes à chaque rattrapage
légèrement tardif. 10 jours absorbe ce décalage habituel tout en
repérant une vraie panne en trois jours de retard sur le rythme normal.

La notification (email, etc.) se configure **depuis le moniteur**
(Edit → bloc Notifications → cocher la notification voulue), pas depuis
la notification elle-même — c'est le sens inverse de ce qu'on pourrait
attendre en découvrant l'interface.

## Tableau des situations

| Situation | Ce qui se passe |
|---|---|
| Tout va bien | Le rapport s'écrit, signal `up` envoyé, silence côté alerte. |
| Machine éteinte le lundi 09:00 | Rattrapage au prochain démarrage (`-StartWhenAvailable`) ; le rapport part en retard mais part. |
| Deux démarrages le même jour | Le programme refuse la seconde exécution (`min_days_between_runs`, voir README.md/SETUP.md) : rien n'est retéléchargé, aucun rapport écrasé — mais le code de sortie reste `0`, donc signal `up` envoyé quand même. |
| Le programme plante (code 2) | Signal `down` envoyé, avec le chemin du journal dans le message — l'alerte Uptime Kuma arrive dans la minute, pas au bout de 10 jours. |
| La tâche ne se déclenche pas du tout | Aucun signal n'arrive (ni `up` ni `down`) — Uptime Kuma ne peut détecter une absence que par l'absence de battement : alerte seulement au bout des 10 jours de `Heartbeat Interval`. |

Ce dernier cas est le seul que ce dispositif ne détecte qu'avec retard :
aucun code de sortie ne peut signaler qu'un programme n'a même pas été
appelé — c'est structurellement le rôle du `Heartbeat Interval`, pas du
script.

## Tester chaque maillon séparément

1. **Le programme seul, dans WSL** — sans passer par Windows ni le
   script :
   ```bash
   make weekly
   ```
2. **Le pont Windows → WSL** — sans passer par le planificateur :
   ```powershell
   powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\scripts\weekly.ps1"
   ```
3. **Le déclenchement Windows** — sans attendre lundi 09:00 :
   ```powershell
   Start-ScheduledTask -TaskName "pea-backtest weekly"
   ```
4. **L'alerte, en conditions réelles** — provoquer un vrai code 2 :
   sauvegarder `config/weekly.yaml`, le rendre invalide (ex. une clé
   obligatoire supprimée), lancer le script, vérifier que l'alerte
   Uptime Kuma arrive bien avec le chemin du journal, **puis restaurer
   le fichier** et vérifier avec `git status` qu'il n'est plus modifié
   avant de continuer à travailler dessus.
