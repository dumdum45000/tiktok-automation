#!/bin/bash
# publish.sh — Lance la publication de tous les clips en attente
# Usage : ./publish.sh
cd "$(dirname "$0")"
source venv/bin/activate
echo "📤 Publication des clips en attente..."
python3 run_publication.py
