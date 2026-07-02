"""Trading papier (paper trading) : le bot tourne en boucle, applique la
stratégie sur les derniers cours et tient un portefeuille FICTIF persistant.

Aucun ordre réel n'est passé. L'état est sauvegardé dans un fichier JSON
pour survivre aux redémarrages.
"""

import json
import logging
import os
import time

from .data import telecharger_historique
from .strategy import generer_signaux

logger = logging.getLogger("gold_bot.paper")


class PortefeuillePapier:
    def __init__(self, fichier_etat: str, capital_initial: float):
        self.fichier_etat = fichier_etat
        self.etat = {
            "capital": capital_initial,
            "position": None,
            "historique": [],
        }
        self._charger()

    def _charger(self):
        if os.path.exists(self.fichier_etat):
            with open(self.fichier_etat, "r", encoding="utf-8") as f:
                self.etat = json.load(f)
            logger.info("État chargé : capital=%.2f", self.etat["capital"])

    def sauvegarder(self):
        os.makedirs(os.path.dirname(self.fichier_etat) or ".", exist_ok=True)
        tmp = self.fichier_etat + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.etat, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.fichier_etat)


def cycle(portefeuille: PortefeuillePapier, config: dict) -> None:
    """Un tour de boucle : télécharge les prix, applique la stratégie,
    met à jour le portefeuille fictif."""
    market, params, risk = config["market"], config["strategy"], config["risk"]
    df = telecharger_historique(
        market["symbol"], market["interval"], market["history_period"],
        market.get("symbol_fallback"),
    )
    data = generer_signaux(df, params)
    row = data.iloc[-1]
    prix = float(row["close"])
    date = str(data.index[-1])
    etat = portefeuille.etat
    frais = risk["frais_pct"] / 100.0

    position = etat["position"]
    if position is not None:
        raison = None
        if prix <= position["stop"]:
            raison = "stop-loss"
        elif prix >= position["target"]:
            raison = "take-profit"
        elif int(row["signal"]) == -1:
            raison = "signal de sortie"
        if raison:
            pnl = (prix - position["entree"]) * position["quantite"]
            pnl -= (position["entree"] + prix) * position["quantite"] * frais
            etat["capital"] += pnl
            etat["historique"].append({
                "date": date, "action": "VENTE", "prix": prix,
                "quantite": position["quantite"], "pnl": round(pnl, 2),
                "raison": raison,
            })
            etat["position"] = None
            logger.info("[PAPIER] VENTE à %.2f (%s) | PnL %.2f | capital %.2f",
                        prix, raison, pnl, etat["capital"])
    elif int(row["signal"]) == 1:
        stop_dist = risk["stop_atr_multiple"] * float(row["atr"])
        quantite = (etat["capital"] * risk["risque_par_trade_pct"] / 100.0) / stop_dist
        etat["position"] = {
            "date": date,
            "entree": prix,
            "quantite": quantite,
            "stop": prix - stop_dist,
            "target": prix + risk["take_profit_atr_multiple"] * float(row["atr"]),
        }
        etat["historique"].append({
            "date": date, "action": "ACHAT", "prix": prix, "quantite": quantite,
        })
        logger.info("[PAPIER] ACHAT à %.2f | stop %.2f | objectif %.2f",
                    prix, etat["position"]["stop"], etat["position"]["target"])
    else:
        logger.info("[PAPIER] Aucun signal (prix %.2f, RSI %.1f, tendance %s)",
                    prix, float(row["rsi"]),
                    "haussière" if row["ema_fast"] > row["ema_slow"] else "baissière")

    portefeuille.sauvegarder()


def boucle(config: dict) -> None:
    """Boucle principale du trading papier (Ctrl+C pour arrêter)."""
    paper = config["paper"]
    portefeuille = PortefeuillePapier(
        paper["fichier_etat"], config["risk"]["capital_initial"]
    )
    logger.info("Démarrage du trading papier (aucun argent réel). "
                "Intervalle : %ss", paper["intervalle_secondes"])
    while True:
        try:
            cycle(portefeuille, config)
        except Exception:
            logger.exception("Erreur pendant le cycle, nouvelle tentative au prochain tour")
        time.sleep(paper["intervalle_secondes"])
