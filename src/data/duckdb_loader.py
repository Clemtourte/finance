"""Chargement des données OHLCV validées dans un entrepôt DuckDB."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import TracebackType

import duckdb
import pandas as pd

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    ticker     VARCHAR NOT NULL,
    date       DATE    NOT NULL,
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    adj_close  DOUBLE,
    volume     BIGINT,
    PRIMARY KEY (ticker, date)
)
"""


class DuckDBLoader:
    """Charge les fichiers Parquet du cache dans une table DuckDB unique.

    Le chargement d'un ticker est idempotent : les lignes existantes pour
    ce ticker sont supprimées puis remplacées par le contenu courant de son
    fichier Parquet (qui contient déjà l'historique complet fusionné, voir
    `src.data.cache.ParquetCache`).
    """

    def __init__(self, db_path: str | Path, table: str = "ohlcv_daily") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.table = table
        self._conn = duckdb.connect(str(self.db_path))
        self._conn.execute(_CREATE_TABLE_SQL.format(table=self.table))

    def load_ticker(self, ticker: str, parquet_path: str | Path) -> int:
        """Charge (ou recharge) l'historique complet d'un ticker.

        Args:
            ticker: Symbole du titre à (re)charger.
            parquet_path: Chemin du fichier Parquet en cache pour ce ticker.

        Returns:
            Nombre de lignes chargées pour ce ticker.

        Raises:
            FileNotFoundError: Si `parquet_path` n'existe pas.
        """
        parquet_path = Path(parquet_path)
        if not parquet_path.exists():
            raise FileNotFoundError(f"Cache Parquet introuvable : {parquet_path}")

        self._conn.execute(f"DELETE FROM {self.table} WHERE ticker = ?", [ticker])
        self._conn.execute(
            f"""
            INSERT INTO {self.table}
            SELECT ticker, date, open, high, low, close, adj_close, volume
            FROM read_parquet(?)
            """,
            [str(parquet_path)],
        )
        (count,) = self._conn.execute(
            f"SELECT count(*) FROM {self.table} WHERE ticker = ?", [ticker]
        ).fetchone()
        return count

    def close(self) -> None:
        """Ferme la connexion DuckDB sous-jacente."""
        self._conn.close()

    def __enter__(self) -> DuckDBLoader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def read_ohlcv(
    db_path: str | Path,
    table: str,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Lit l'historique OHLCV d'un ticker depuis l'entrepôt DuckDB.

    Args:
        db_path: Chemin de la base DuckDB.
        table: Nom de la table OHLCV.
        ticker: Symbole du titre à lire.
        start: Première date (incluse) à inclure ; pas de borne si `None`.
        end: Dernière date (incluse) à inclure ; pas de borne si `None`.

    Returns:
        DataFrame indexé par `date` (croissant), colonnes `open`, `high`,
        `low`, `close`, `adj_close`, `volume`.

    Raises:
        ValueError: Si aucune donnée n'est trouvée pour ce ticker sur la
            période demandée.
    """
    query = f"SELECT * FROM {table} WHERE ticker = ?"
    params: list[object] = [ticker]
    if start is not None:
        query += " AND date >= ?"
        params.append(start)
    if end is not None:
        query += " AND date <= ?"
        params.append(end)
    query += " ORDER BY date"

    with duckdb.connect(str(db_path), read_only=True) as conn:
        df = conn.execute(query, params).fetchdf()

    if df.empty:
        raise ValueError(f"Aucune donnée pour {ticker} dans {db_path} (table {table})")

    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").drop(columns="ticker").sort_index()
