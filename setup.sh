#!/bin/bash
# setup.sh — Script d'installation automatique de TikTok Automation
# Compatible macOS Intel (pas de GPU NVIDIA requis)
# Usage : bash setup.sh

set -e  # Arrêter en cas d'erreur

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       TikTok Automation — Installation               ║"
echo "║       divertissement45000                            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ─── Vérification du système ──────────────────────────────────────────────────
echo "📌 Étape 1/8 — Vérification du système"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "❌ Ce script est prévu pour macOS uniquement."
    exit 1
fi

# Vérifier l'architecture (Intel vs Apple Silicon)
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    echo "⚠️  Architecture Apple Silicon (M1/M2) détectée."
    echo "   Ce projet est optimisé pour Intel, mais devrait fonctionner."
else
    echo "✅ Architecture Intel (x86_64) — optimale"
fi

echo ""

# ─── Homebrew ─────────────────────────────────────────────────────────────────
echo "📌 Étape 2/8 — Installation de Homebrew (si absent)"

if ! command -v brew &> /dev/null; then
    echo "Installation de Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    echo "✅ Homebrew déjà installé ($(brew --version | head -1))"
fi

echo ""

# ─── ffmpeg ───────────────────────────────────────────────────────────────────
echo "📌 Étape 3/8 — Installation de ffmpeg"

if ! command -v ffmpeg &> /dev/null; then
    echo "Installation de ffmpeg via Homebrew..."
    brew install ffmpeg
    echo "✅ ffmpeg installé"
else
    echo "✅ ffmpeg déjà installé ($(ffmpeg -version 2>&1 | head -1))"
fi

echo ""

# ─── Python 3 ────────────────────────────────────────────────────────────────
echo "📌 Étape 4/8 — Vérification de Python 3"

if ! command -v python3 &> /dev/null; then
    echo "Installation de Python 3 via Homebrew..."
    brew install python@3.11
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "✅ $PYTHON_VERSION"

# Vérifier la version minimale (3.9+)
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [[ "$PYTHON_MAJOR" -lt 3 ]] || [[ "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 9 ]]; then
    echo "❌ Python 3.9+ requis (vous avez Python $PYTHON_MAJOR.$PYTHON_MINOR)"
    echo "   Installez Python 3.11 : brew install python@3.11"
    exit 1
fi

echo ""

# ─── Environnement virtuel ────────────────────────────────────────────────────
echo "📌 Étape 5/8 — Création de l'environnement virtuel Python"

VENV_DIR="venv"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Création du venv dans $VENV_DIR/..."
    python3 -m venv "$VENV_DIR"
    echo "✅ Environnement virtuel créé"
else
    echo "✅ Environnement virtuel existant réutilisé"
fi

# Activer le venv pour ce script
source "$VENV_DIR/bin/activate"
echo "✅ Environnement virtuel activé"

echo ""

# ─── Dépendances Python ───────────────────────────────────────────────────────
echo "📌 Étape 6/8 — Installation des dépendances Python"
echo "(Cela peut prendre 3-10 minutes la première fois)"
echo ""

# Mettre à jour pip
pip install --upgrade pip --quiet

# Installer les dépendances en plusieurs groupes pour un meilleur retour d'erreur
echo "Installation de Streamlit et utilitaires..."
pip install streamlit==1.32.2 watchdog==4.0.0 requests==2.31.0 \
    python-dateutil==2.9.0 psutil==5.9.8 schedule==1.2.1 colorlog==6.8.2 \
    --quiet
echo "✅ Streamlit et utilitaires"

echo "Installation de yt-dlp..."
pip install yt-dlp==2024.3.10 --quiet
echo "✅ yt-dlp"

echo "Installation de OpenCV et numpy..."
pip install opencv-python==4.9.0.80 numpy==1.26.4 --quiet
echo "✅ OpenCV et numpy"

echo "Installation de librosa et traitement audio..."
pip install librosa==0.10.1 soundfile==0.12.1 scipy==1.12.0 --quiet
echo "✅ librosa et audio"

echo "Installation de Pillow et scikit-image..."
pip install Pillow==10.2.0 scikit-image==0.22.0 --quiet
echo "✅ Pillow et scikit-image"

echo "Installation de PyTorch (CPU uniquement — peut prendre 5-10 min)..."
pip install torch==2.2.1 torchaudio==2.2.1 --index-url https://download.pytorch.org/whl/cpu --quiet
echo "✅ PyTorch CPU"

echo "Installation de Whisper..."
pip install openai-whisper==20231117 --quiet
echo "✅ Whisper"

echo "Installation de Playwright..."
pip install playwright==1.42.0 playwright-stealth==1.0.6 --quiet
echo "✅ Playwright"

echo "Installation des outils NLP..."
pip install rake-nltk==1.0.6 nltk==3.8.1 --quiet
echo "✅ NLP"

# Données NLTK nécessaires pour rake-nltk
echo "Téléchargement des données NLTK..."
python3 -c "import nltk; nltk.download('stopwords', quiet=True); nltk.download('punkt', quiet=True)"
echo "✅ Données NLTK"

# Installer le navigateur Chromium pour Playwright
echo ""
echo "Installation du navigateur Chromium (pour Playwright)..."
playwright install chromium
echo "✅ Chromium installé"

echo ""

# ─── Téléchargement du modèle Whisper ─────────────────────────────────────────
echo "📌 Étape 7/8 — Téléchargement du modèle Whisper 'base'"
echo "(Environ 140 Mo, téléchargé une seule fois)"

python3 -c "
import whisper
import sys
print('Téléchargement du modèle Whisper base...')
try:
    model = whisper.load_model('base')
    print('✅ Modèle Whisper base chargé avec succès')
except Exception as e:
    print(f'⚠️  Avertissement : {e}')
    print('   Le modèle sera téléchargé automatiquement au premier lancement')
"

echo ""

# ─── Création de la structure des dossiers ────────────────────────────────────
echo "📌 Étape 8/8 — Création de la structure des dossiers"

mkdir -p data/downloads data/clips data/processed data/published
mkdir -p music_library/sport music_library/humour music_library/autre
mkdir -p logs assets/fonts

echo "✅ Structure des dossiers créée"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║           ✅ INSTALLATION TERMINÉE !                 ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║                                                      ║"
echo "║  Pour lancer l'application :                         ║"
echo "║                                                      ║"
echo "║    source venv/bin/activate                          ║"
echo "║    streamlit run app.py                              ║"
echo "║                                                      ║"
echo "║  L'interface s'ouvrira dans votre navigateur         ║"
echo "║  sur http://localhost:8501                           ║"
echo "║                                                      ║"
echo "║  PROCHAINES ÉTAPES :                                 ║"
echo "║  1. Ajoutez des musiques : python download_music.py  ║"
echo "║  2. Configurez TikTok API dans l'onglet Paramètres   ║"
echo "║  3. Collez vos liens dans l'onglet Import            ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Créer un script de lancement rapide
cat > launch.sh << 'LAUNCHER'
#!/bin/bash
# Lance TikTok Automation
cd "$(dirname "$0")"
source venv/bin/activate
echo "🚀 Lancement de TikTok Automation..."
streamlit run app.py --server.port 8501 --server.headless true
LAUNCHER
chmod +x launch.sh
echo "💡 Raccourci créé : ./launch.sh"
echo ""
