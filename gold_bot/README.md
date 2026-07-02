# Gold Bot — Bot de trading sur l'or (XAU/USD)

Bot de trading autonome sur l'or, écrit en Python. Il fonctionne en trois
modes : **backtest** (simulation historique), **signal** (état actuel du
marché) et **paper** (trading papier en boucle, sans argent réel).

> ⚠️ **Avertissement** : outil éducatif. Aucun ordre réel n'est passé et
> aucune performance n'est garantie. Le trading comporte un risque de perte
> en capital. Ne connectez jamais ce bot à un compte réel sans comprendre
> exactement ce qu'il fait.

## Installation

```bash
pip install -r gold_bot/requirements.txt
```

## Utilisation

```bash
# Simulation de la stratégie sur 10 ans de données journalières
python -m gold_bot backtest

# Signal actuel (achat / sortie / neutre) avec les indicateurs
python -m gold_bot signal

# Trading papier : un cycle unique (pratique en cron)
python -m gold_bot paper --une-fois

# Trading papier : boucle continue (Ctrl+C pour arrêter)
python -m gold_bot paper
```

## Stratégie

Suivi de tendance classique sur bougies **journalières**, volontairement
simple et lisible :

1. **Entrée (achat)** : l'EMA 10 croise au-dessus de l'EMA 30 et le RSI 14
   est sous 70 (on n'achète pas en zone de surachat).
2. **Sortie** : stop-loss à `2 × ATR` sous l'entrée, take-profit à
   `3 × ATR` au-dessus, ou croisement baissier des EMA.
3. **Gestion du risque** : chaque trade risque au maximum **1 % du capital**
   (la taille de position est calculée à partir de la distance au stop).
   Des frais de 0,05 % par transaction sont simulés.

Les données proviennent de Yahoo Finance : `GC=F` (contrat à terme or,
COMEX), avec repli sur `XAUUSD=X` (spot).

## Configuration

Tout est paramétrable dans [`config.json`](config.json) :

| Section    | Clé                       | Rôle                                      |
|------------|---------------------------|-------------------------------------------|
| `market`   | `symbol`, `interval`      | Instrument et pas de temps (`1h`, `1d`…)   |
| `strategy` | `ema_fast`, `ema_slow`    | Périodes des moyennes mobiles              |
| `strategy` | `rsi_period`, `rsi_overbought` | Filtre RSI                            |
| `risk`     | `capital_initial`         | Capital fictif de départ                   |
| `risk`     | `risque_par_trade_pct`    | % du capital risqué par trade              |
| `risk`     | `stop_atr_multiple`       | Distance du stop en multiples d'ATR        |
| `risk`     | `take_profit_atr_multiple`| Distance de l'objectif en multiples d'ATR  |
| `paper`    | `intervalle_secondes`     | Fréquence de la boucle papier              |

Vous pouvez dupliquer le fichier et le passer avec `--config mon_config.json`.

## Structure du module

```
gold_bot/
├── __main__.py   # CLI (backtest / signal / paper)
├── config.json   # Paramètres de marché, stratégie et risque
├── data.py       # Téléchargement des prix (yfinance)
├── strategy.py   # Indicateurs (EMA, RSI, ATR) et signaux
├── backtest.py   # Moteur de backtest + statistiques
└── paper.py      # Portefeuille papier persistant + boucle
```

L'état du trading papier est sauvegardé dans `gold_bot/paper_state.json`
(ignoré par git) : le bot reprend là où il s'était arrêté.

## Dépannage

**Erreur SSL / « Connection reset by peer » derrière un proxy d'entreprise** :
yfinance utilise par défaut `curl_cffi` avec une empreinte TLS de navigateur,
que certains proxys interceptant le TLS rejettent. Dans ce cas, désactivez-le :

```bash
YF_DISABLE_CURL_CFFI=1 python -m gold_bot backtest
```
