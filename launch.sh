#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
echo "🚀 Démarrage TikTok Automation — divertissement45000"
echo "📌 Interface : http://localhost:8501"
echo ""
streamlit run app.py --server.port 8501 --server.address localhost
