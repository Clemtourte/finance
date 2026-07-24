"""Implémentation de :class:`DataProvider` basée sur yfinance."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from src.data.provider import DataProvider, DataProviderError, empty_ohlcv_frame

logger = logging.getLogger(__name__)

_YF_TO_SCHEMA = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


class YFinanceProvider(DataProvider):
    """Récupère l'OHLCV daily ajusté d'un ticker via l'API yfinance.

    Attributes:
        max_retries: Nombre maximal de tentatives en cas d'échec réseau.
        retry_backoff_seconds: Délai (secondes) avant nouvelle tentative,
            multiplié par le numéro de tentative (backoff linéaire).
        request_pause_seconds: Pause appliquée après chaque appel réussi,
            pour rester raisonnable vis-à-vis de l'API non officielle.
    """

    def __init__(
        self,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        request_pause_seconds: float = 0.3,
    ) -> None:
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.request_pause_seconds = request_pause_seconds

    def get_ohlcv(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Voir :meth:`DataProvider.get_ohlcv`.

        yfinance traite `end` comme exclusif : on ajoute un jour pour que
        la date `end` demandée soit incluse dans le résultat.
        """
        if start > end:
            return empty_ohlcv_frame()

        yf_end = end + timedelta(days=1)
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                raw = yf.Ticker(ticker).history(
                    start=start.isoformat(),
                    end=yf_end.isoformat(),
                    auto_adjust=False,
                    actions=False,
                )
                time.sleep(self.request_pause_seconds)
                return self._normalize(raw)
            except Exception as exc:  # noqa: BLE001 - toute erreur réseau/API est retentée
                last_error = exc
                logger.warning(
                    "Échec téléchargement %s (tentative %d/%d): %s",
                    ticker,
                    attempt,
                    self.max_retries,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * attempt)

        raise DataProviderError(
            f"Impossible de récupérer {ticker} après {self.max_retries} tentatives"
        ) from last_error

    @staticmethod
    def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
        """Convertit la sortie yfinance vers le schéma OHLCV canonique."""
        if raw.empty:
            return empty_ohlcv_frame()

        df = raw.rename(columns=_YF_TO_SCHEMA)[list(_YF_TO_SCHEMA.values())].copy()
        df["date"] = pd.to_datetime(df.index).tz_localize(None).date
        df["volume"] = df["volume"].fillna(0).astype("int64")
        df = df.reset_index(drop=True)
        return df[["date", "open", "high", "low", "close", "adj_close", "volume"]]
