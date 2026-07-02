"""Backtest de la stratégie sur données historiques.

Règles simulées :
- Entrée en position longue sur signal d'achat, à la clôture de la bougie.
- Taille de position dimensionnée pour risquer `risque_par_trade_pct` du
  capital entre le prix d'entrée et le stop (stop = entrée - k * ATR).
- Sortie sur stop-loss, take-profit (touchés en intra-bougie via high/low)
  ou signal de sortie de la stratégie.
- Frais appliqués à l'entrée et à la sortie (`frais_pct`).
"""

import math

import pandas as pd

from .strategy import generer_signaux


def executer_backtest(df: pd.DataFrame, params: dict, risk: dict) -> dict:
    data = generer_signaux(df, params)

    capital = risk["capital_initial"]
    frais = risk["frais_pct"] / 100.0
    risque = risk["risque_par_trade_pct"] / 100.0

    position = None  # dict: entree, quantite, stop, target
    trades = []
    equity = []

    for date, row in data.iterrows():
        prix = float(row["close"])

        if position is not None:
            sortie = None
            # Stop et objectif évalués en intra-bougie
            if float(row["low"]) <= position["stop"]:
                sortie = (position["stop"], "stop-loss")
            elif float(row["high"]) >= position["target"]:
                sortie = (position["target"], "take-profit")
            elif row["signal"] == -1:
                sortie = (prix, "signal de sortie")

            if sortie is not None:
                prix_sortie, raison = sortie
                brut = (prix_sortie - position["entree"]) * position["quantite"]
                cout = (position["entree"] + prix_sortie) * position["quantite"] * frais
                pnl = brut - cout
                capital += pnl
                trades.append({
                    "date_entree": position["date"],
                    "date_sortie": str(date),
                    "entree": round(position["entree"], 2),
                    "sortie": round(prix_sortie, 2),
                    "quantite": round(position["quantite"], 4),
                    "pnl": round(pnl, 2),
                    "raison": raison,
                })
                position = None

        if position is None and row["signal"] == 1 and not math.isnan(row["atr"]):
            stop_dist = risk["stop_atr_multiple"] * float(row["atr"])
            if stop_dist > 0:
                quantite = (capital * risque) / stop_dist
                position = {
                    "date": str(date),
                    "entree": prix,
                    "quantite": quantite,
                    "stop": prix - stop_dist,
                    "target": prix + risk["take_profit_atr_multiple"] * float(row["atr"]),
                }

        valeur = capital
        if position is not None:
            valeur += (prix - position["entree"]) * position["quantite"]
        equity.append((date, valeur))

    # Clôture de la position restante au dernier cours
    if position is not None:
        prix = float(data["close"].iloc[-1])
        pnl = (prix - position["entree"]) * position["quantite"]
        pnl -= (position["entree"] + prix) * position["quantite"] * frais
        capital += pnl
        trades.append({
            "date_entree": position["date"],
            "date_sortie": str(data.index[-1]),
            "entree": round(position["entree"], 2),
            "sortie": round(prix, 2),
            "quantite": round(position["quantite"], 4),
            "pnl": round(pnl, 2),
            "raison": "fin de backtest",
        })

    return _statistiques(trades, equity, risk["capital_initial"], capital)


def _statistiques(trades, equity, capital_initial, capital_final) -> dict:
    courbe = pd.Series([v for _, v in equity], index=[d for d, _ in equity])
    pics = courbe.cummax()
    drawdown_max = float(((courbe - pics) / pics).min()) * 100 if len(courbe) else 0.0

    gagnants = [t for t in trades if t["pnl"] > 0]
    perdants = [t for t in trades if t["pnl"] <= 0]
    gains = sum(t["pnl"] for t in gagnants)
    pertes = abs(sum(t["pnl"] for t in perdants))

    return {
        "capital_initial": round(capital_initial, 2),
        "capital_final": round(capital_final, 2),
        "performance_pct": round((capital_final / capital_initial - 1) * 100, 2),
        "nb_trades": len(trades),
        "nb_gagnants": len(gagnants),
        "nb_perdants": len(perdants),
        "taux_reussite_pct": round(100 * len(gagnants) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gains / pertes, 2) if pertes > 0 else float("inf"),
        "drawdown_max_pct": round(drawdown_max, 2),
        "trades": trades,
    }
