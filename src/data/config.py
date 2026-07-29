"""Chargement de la configuration YAML de la couche données."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml


@dataclass(frozen=True)
class TickerInfo:
    """Un titre de l'univers d'investissement.

    `ttf` et `spread_pct` sont des champs propres au moteur de coûts
    (`src.engine.costs`) : la couche données ne les utilise pas, ils ont
    donc une valeur par défaut neutre pour ne pas casser un fichier
    d'univers minimal (`ticker`/`name`/`isin` seulement).

    Attributes:
        ticker: Symbole au format yfinance (ex. `"AI.PA"`).
        name: Nom du titre.
        isin: Code ISIN.
        ttf: `True` si le titre est soumis à la taxe française sur les
            transactions financières (grandes capitalisations françaises).
        spread_pct: DEMI-spread propre au titre (fraction) : le coût
            supporté d'un seul côté de l'aller-retour. Le moteur de coûts
            (`src.engine.costs`) l'applique symétriquement à l'achat et à
            la vente, donc le coût total d'un aller-retour vaut
            `2 * spread_pct`. Mesure depuis un carnet d'ordres :
            `(vente - achat) / (vente + achat)`.
    """

    ticker: str
    name: str
    isin: str
    ttf: bool = False
    spread_pct: float = 0.0


@dataclass(frozen=True)
class ValidationConfig:
    """Seuils des contrôles de qualité (voir `src.data.validation`)."""

    max_gap_calendar_days: int
    outlier_return_threshold: float
    split_ratio_tolerance: float


@dataclass(frozen=True)
class YFinanceConfig:
    """Paramètres de résilience du provider yfinance."""

    max_retries: int
    retry_backoff_seconds: float
    request_pause_seconds: float


@dataclass(frozen=True)
class DataConfig:
    """Configuration complète de la couche données, issue de `config/data.yaml`."""

    universe_file: Path
    start_date: date
    end_date: date
    cache_dir: Path
    duckdb_path: Path
    duckdb_table: str
    yfinance: YFinanceConfig
    validation: ValidationConfig


def _parse_date(value: str) -> date:
    """Parse une date de config, avec le mot-clé spécial `"today"`."""
    if value == "today":
        return datetime.now().date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_data_config(path: str | Path) -> DataConfig:
    """Charge `config/data.yaml`.

    Args:
        path: Chemin du fichier YAML de configuration.

    Returns:
        `DataConfig` typée et résolue (dates parsées, chemins en `Path`).
    """
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    base_dir = config_path.parent.parent  # racine du projet, config/ est à la racine
    period = raw["period"]

    return DataConfig(
        universe_file=base_dir / raw["universe_file"],
        start_date=_parse_date(period["start_date"]),
        end_date=_parse_date(period["end_date"]),
        cache_dir=base_dir / raw["cache_dir"],
        duckdb_path=base_dir / raw["duckdb"]["path"],
        duckdb_table=raw["duckdb"]["table"],
        yfinance=YFinanceConfig(**raw["yfinance"]),
        validation=ValidationConfig(**raw["validation"]),
    )


def load_universe(path: str | Path) -> list[TickerInfo]:
    """Charge la liste de tickers d'un fichier d'univers YAML.

    Args:
        path: Chemin du fichier YAML (ex. `config/universe_cac40.yaml`).

    Returns:
        Liste de `TickerInfo`, dans l'ordre du fichier.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [TickerInfo(**entry) for entry in raw["tickers"]]
