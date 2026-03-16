"""
run_publication.py — Lance la publication de tous les clips en attente.
Usage : python3 run_publication.py
"""
import json
import logging
import os
import sys

# Toujours s'exécuter depuis le répertoire de l'app
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log"),
        logging.StreamHandler(sys.stdout),
    ]
)

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

from modules.state_manager import StateManager
from modules.publisher import PublicationScheduler

state = StateManager(config["chemins"]["state_file"])

def log_cb(msg):
    print(msg)

scheduler = PublicationScheduler(config, state, log_cb)

prochain = state.get_prochain_a_publier()
if prochain is None:
    print("✅ Aucun clip en attente.")
    sys.exit(0)

print(f"🚀 Lancement publication — {sum(1 for c in state.get_file_publication() if c.get('statut') == 'en_attente')} clip(s) en attente")
scheduler._boucle_publication()
