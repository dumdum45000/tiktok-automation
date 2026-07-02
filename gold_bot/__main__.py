"""Point d'entrée du bot de trading sur l'or.

Usage :
    python -m gold_bot backtest            # simulation historique
    python -m gold_bot signal              # signal actuel
    python -m gold_bot paper               # trading papier en boucle
    python -m gold_bot paper --une-fois    # un seul cycle de trading papier

AVERTISSEMENT : outil éducatif, aucun ordre réel n'est passé.
"""

import argparse
import json
import logging
import os
import sys

from .backtest import executer_backtest
from .data import telecharger_historique
from .paper import PortefeuillePapier, boucle, cycle
from .strategy import signal_actuel

CONFIG_PAR_DEFAUT = os.path.join(os.path.dirname(__file__), "config.json")


def charger_config(chemin: str) -> dict:
    with open(chemin, "r", encoding="utf-8") as f:
        return json.load(f)


def cmd_backtest(config: dict) -> None:
    market = config["market"]
    df = telecharger_historique(
        market["symbol"], market["interval"], market["history_period"],
        market.get("symbol_fallback"),
    )
    resultat = executer_backtest(df, config["strategy"], config["risk"])
    trades = resultat.pop("trades")

    print("\n=== Résultats du backtest (or, {} bougies {}) ===".format(
        len(df), market["interval"]))
    print(f"Période             : {df.index[0]} -> {df.index[-1]}")
    print(f"Capital initial     : {resultat['capital_initial']:>12.2f} $")
    print(f"Capital final       : {resultat['capital_final']:>12.2f} $")
    print(f"Performance         : {resultat['performance_pct']:>11.2f} %")
    print(f"Nombre de trades    : {resultat['nb_trades']}")
    print(f"Taux de réussite    : {resultat['taux_reussite_pct']} % "
          f"({resultat['nb_gagnants']} gagnants / {resultat['nb_perdants']} perdants)")
    print(f"Profit factor       : {resultat['profit_factor']}")
    print(f"Drawdown maximum    : {resultat['drawdown_max_pct']} %")

    if trades:
        print("\nDerniers trades :")
        for t in trades[-5:]:
            print(f"  {t['date_entree'][:16]} -> {t['date_sortie'][:16]} | "
                  f"entrée {t['entree']:.2f} sortie {t['sortie']:.2f} | "
                  f"PnL {t['pnl']:+.2f} $ ({t['raison']})")


def cmd_signal(config: dict) -> None:
    market = config["market"]
    df = telecharger_historique(
        market["symbol"], market["interval"], market["history_period"],
        market.get("symbol_fallback"),
    )
    info = signal_actuel(df, config["strategy"])
    libelle = {1: "ACHAT", -1: "SORTIE / VENTE", 0: "NEUTRE (aucune action)"}
    print("\n=== Signal actuel sur l'or ===")
    print(f"Date      : {info['date']}")
    print(f"Prix      : {info['prix']} $")
    print(f"EMA {config['strategy']['ema_fast']:>3}   : {info['ema_fast']}")
    print(f"EMA {config['strategy']['ema_slow']:>3}   : {info['ema_slow']}")
    print(f"RSI       : {info['rsi']}")
    print(f"ATR       : {info['atr']}")
    print(f"Tendance  : {info['tendance']}")
    print(f"Signal    : {libelle[info['signal']]}")


def cmd_paper(config: dict, une_fois: bool) -> None:
    print("AVERTISSEMENT : trading papier uniquement, aucun argent réel.")
    if une_fois:
        portefeuille = PortefeuillePapier(
            config["paper"]["fichier_etat"], config["risk"]["capital_initial"]
        )
        cycle(portefeuille, config)
        print(f"Capital fictif : {portefeuille.etat['capital']:.2f} $")
        if portefeuille.etat["position"]:
            p = portefeuille.etat["position"]
            print(f"Position ouverte : {p['quantite']:.4f} oz à {p['entree']:.2f} $ "
                  f"(stop {p['stop']:.2f}, objectif {p['target']:.2f})")
    else:
        boucle(config)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gold_bot",
        description="Bot de trading sur l'or (backtest, signal, trading papier).",
    )
    parser.add_argument("commande", choices=["backtest", "signal", "paper"])
    parser.add_argument("--config", default=CONFIG_PAR_DEFAUT,
                        help="Chemin du fichier de configuration JSON")
    parser.add_argument("--une-fois", action="store_true",
                        help="Mode paper : exécuter un seul cycle puis quitter")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
    )

    config = charger_config(args.config)
    if args.commande == "backtest":
        cmd_backtest(config)
    elif args.commande == "signal":
        cmd_signal(config)
    elif args.commande == "paper":
        cmd_paper(config, args.une_fois)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrêt demandé par l'utilisateur.")
        sys.exit(0)
