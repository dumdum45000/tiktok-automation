#!/bin/bash
# TikTok_Automation.command
# Double-cliquer pour tout lancer automatiquement

cd "$(dirname "$0")"

echo "╔══════════════════════════════════════════╗"
echo "║      TikTok Automation — démarrage       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Activer l'environnement Python
source venv/bin/activate

# ── 1. Lancer l'interface Streamlit en arrière-plan ─────────────────────────
if ! lsof -ti:8501 > /dev/null 2>&1; then
    echo "🖥  Démarrage interface Streamlit..."
    nohup streamlit run app.py \
        --server.port 8501 \
        --server.address localhost \
        --server.headless true \
        >> logs/streamlit.log 2>&1 &
    sleep 3
    echo "✅ Interface disponible sur http://localhost:8501"
else
    echo "✅ Interface déjà en cours sur http://localhost:8501"
fi

# ── 2. Ouvrir l'interface dans le navigateur ─────────────────────────────────
open http://localhost:8501

# ── 3. Lancer la publication des clips en attente ────────────────────────────
echo ""
echo "📤 Lancement publication..."
echo "──────────────────────────────────────────────"
python3 run_publication.py

echo ""
echo "──────────────────────────────────────────────"
echo "✅ Session terminée."
echo "   L'interface reste disponible sur http://localhost:8501"
read -n 1 -s -r -p "   Appuyer sur une touche pour fermer..."
