"""Récupération des données de prix de l'or via Yahoo Finance (yfinance).

Symbole principal : GC=F (contrat à terme sur l'or, COMEX).
Repli : XAUUSD=X (taux spot) si le principal ne renvoie rien.
"""

import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger("gold_bot.data")


def telecharger_historique(symbol: str, interval: str, period: str,
                           symbol_fallback: str = None) -> pd.DataFrame:
    """Télécharge l'historique OHLC et renvoie un DataFrame propre.

    Colonnes renvoyées : open, high, low, close, volume (index datetime UTC).
    Lève RuntimeError si aucune donnée n'est disponible.
    """
    for sym in filter(None, [symbol, symbol_fallback]):
        logger.info("Téléchargement %s (interval=%s, period=%s)...", sym, interval, period)
        try:
            df = yf.download(sym, interval=interval, period=period,
                             progress=False, auto_adjust=True)
        except Exception as exc:
            logger.warning("Échec du téléchargement pour %s : %s", sym, exc)
            continue
        if df is None or df.empty:
            logger.warning("Aucune donnée pour %s", sym)
            continue
        return _nettoyer(df)
    raise RuntimeError(
        "Impossible de récupérer les prix de l'or. Vérifier la connexion réseau ; "
        "derrière un proxy d'entreprise (TLS intercepté), lancer le bot avec "
        "YF_DISABLE_CURL_CFFI=1."
    )


def _nettoyer(df: pd.DataFrame) -> pd.DataFrame:
    # yfinance renvoie parfois des colonnes MultiIndex (colonne, ticker)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["close"])
    df.index.name = "date"
    return df


def dernier_prix(df: pd.DataFrame) -> float:
    """Dernier cours de clôture connu."""
    return float(df["close"].iloc[-1])
