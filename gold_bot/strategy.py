"""Stratégie de trading sur l'or.

Logique : suivi de tendance par croisement de moyennes mobiles exponentielles
(EMA rapide / EMA lente), filtré par le RSI pour éviter d'acheter en zone de
surachat ou de vendre en zone de survente. L'ATR sert à dimensionner le stop
et l'objectif de gain.

Signaux :
  +1 = achat (EMA rapide croise au-dessus de l'EMA lente, RSI < surachat)
  -1 = sortie (EMA rapide croise sous l'EMA lente, ou RSI > surachat extrême)
   0 = rien à faire
"""

import pandas as pd


def calculer_indicateurs(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Ajoute les colonnes ema_fast, ema_slow, rsi et atr au DataFrame."""
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=params["ema_fast"], adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=params["ema_slow"], adjust=False).mean()
    out["rsi"] = _rsi(out["close"], params["rsi_period"])
    out["atr"] = _atr(out, params["atr_period"])
    return out


def generer_signaux(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Ajoute une colonne 'signal' (+1 achat, -1 sortie, 0 neutre)."""
    out = calculer_indicateurs(df, params)
    au_dessus = out["ema_fast"] > out["ema_slow"]
    croise_haut = au_dessus & ~au_dessus.shift(1, fill_value=False)
    croise_bas = ~au_dessus & au_dessus.shift(1, fill_value=True)

    out["signal"] = 0
    out.loc[croise_haut & (out["rsi"] < params["rsi_overbought"]), "signal"] = 1
    out.loc[croise_bas, "signal"] = -1
    # Pas de signal tant que les indicateurs ne sont pas stabilisés
    out.iloc[: params["ema_slow"], out.columns.get_loc("signal")] = 0
    return out


def signal_actuel(df: pd.DataFrame, params: dict) -> dict:
    """Résume l'état de la dernière bougie (pour la commande `signal`)."""
    out = generer_signaux(df, params)
    row = out.iloc[-1]
    tendance = "haussière" if row["ema_fast"] > row["ema_slow"] else "baissière"
    return {
        "date": str(out.index[-1]),
        "prix": round(float(row["close"]), 2),
        "ema_fast": round(float(row["ema_fast"]), 2),
        "ema_slow": round(float(row["ema_slow"]), 2),
        "rsi": round(float(row["rsi"]), 1),
        "atr": round(float(row["atr"]), 2),
        "tendance": tendance,
        "signal": int(row["signal"]),
    }


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI de Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    perte = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / perte.replace(0, 1e-12)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range (volatilité moyenne d'une bougie)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()
