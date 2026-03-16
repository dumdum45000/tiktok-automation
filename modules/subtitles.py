"""
subtitles.py — Module 6 : Sous-titres adaptatifs

Génère des sous-titres automatiques via whisper-cpp (binaire C++, sans PyTorch).
Style adaptatif : analyse la luminosité de la zone sous-titre pour choisir
texte blanc sur fond sombre ou texte noir sur fond clair.

whisper-cpp est installé via : brew install whisper-cpp
Les modèles sont téléchargés automatiquement au premier lancement.

Si aucune parole détectée → pas de sous-titres ajoutés.
Tout fonctionne sur CPU Intel sans CUDA.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Chemin par défaut de whisper-cpp sur macOS Homebrew
WHISPER_CPP_BIN = "/usr/local/bin/whisper-cpp"
# Dossier des modèles whisper-cpp
WHISPER_MODELS_DIR = os.path.expanduser("~/.whisper-cpp/models")
# URL de base pour télécharger les modèles
WHISPER_MODEL_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"


def trouver_whisper_cpp() -> Optional[str]:
    """Trouve le binaire whisper-cpp sur le système."""
    candidats = [
        "/usr/local/bin/whisper-cpp",
        "/opt/homebrew/bin/whisper-cpp",
        "/usr/local/bin/main",  # Ancien nom whisper.cpp
    ]
    for chemin in candidats:
        if os.path.exists(chemin) and os.access(chemin, os.X_OK):
            return chemin

    # Chercher dans le PATH
    try:
        result = subprocess.run(["which", "whisper-cpp"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def telecharger_modele_whisper(modele: str = "base") -> Optional[str]:
    """
    Télécharge le modèle whisper-cpp si absent.

    Args:
        modele: Nom du modèle (tiny, base, small)

    Returns:
        Chemin vers le fichier .bin du modèle, ou None si échec
    """
    os.makedirs(WHISPER_MODELS_DIR, exist_ok=True)

    # Nom de fichier du modèle
    nom_fichier = f"ggml-{modele}.en.bin"  # Version anglais (plus légère)
    chemin_modele = os.path.join(WHISPER_MODELS_DIR, nom_fichier)

    if os.path.exists(chemin_modele) and os.path.getsize(chemin_modele) > 1000:
        return chemin_modele

    # Chercher aussi dans les chemins Homebrew
    chemins_homebrew = [
        f"/usr/local/opt/whisper-cpp/share/whisper-cpp/models/{nom_fichier}",
        f"/opt/homebrew/opt/whisper-cpp/share/whisper-cpp/models/{nom_fichier}",
    ]
    for chemin in chemins_homebrew:
        if os.path.exists(chemin):
            return chemin

    # Télécharger le modèle
    url = f"{WHISPER_MODEL_BASE_URL}/{nom_fichier}"
    logger.info(f"Téléchargement du modèle Whisper '{modele}'...")

    try:
        result = subprocess.run(
            ["curl", "-L", "-o", chemin_modele, url, "--progress-bar"],
            timeout=600
        )
        if result.returncode == 0 and os.path.exists(chemin_modele):
            taille = os.path.getsize(chemin_modele) / (1024 ** 2)
            logger.info(f"Modèle téléchargé : {chemin_modele} ({taille:.0f} Mo)")
            return chemin_modele
    except Exception as e:
        logger.error(f"Erreur téléchargement modèle : {e}")

    return None


def transcrire_whisper_cpp(
    chemin_video: str,
    modele: str = "base",
    langue: str = "en",
    nb_threads: int = None
) -> Optional[List[Dict]]:
    """
    Transcrit l'audio d'une vidéo avec whisper-cpp (C++, pas PyTorch).

    Retourne une liste de segments : [{debut, fin, texte}]

    Args:
        chemin_video: Chemin vers la vidéo
        modele: Modèle whisper (tiny/base/small — .en pour anglais uniquement)
        langue: Code langue

    Returns:
        Liste de segments avec timestamps, ou None si erreur/pas de parole
    """
    whisper_bin = trouver_whisper_cpp()
    if not whisper_bin:
        logger.warning("whisper-cpp introuvable. Installez : brew install whisper-cpp")
        return None

    chemin_modele = telecharger_modele_whisper(modele)
    if not chemin_modele:
        logger.error("Impossible de trouver ou télécharger le modèle whisper")
        return None

    # Extraire l'audio en WAV 16kHz mono (format requis par whisper-cpp)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        chemin_wav = tmp.name
    with tempfile.NamedTemporaryFile(suffix="", delete=False, prefix="whisper_out_") as tmp2:
        chemin_sortie_base = tmp2.name

    try:
        # Extraire audio
        cmd_audio = [
            "ffmpeg", "-y", "-i", chemin_video,
            "-vn", "-ar", "16000", "-ac", "1",
            "-acodec", "pcm_s16le", chemin_wav
        ]
        r = subprocess.run(cmd_audio, capture_output=True, timeout=120)
        if r.returncode != 0:
            logger.error("Impossible d'extraire l'audio")
            return None

        # Lancer whisper-cpp avec sortie JSON
        cmd_whisper = [
            whisper_bin,
            "-m", chemin_modele,
            "-f", chemin_wav,
            "-l", langue,
            "--output-json",
            "-of", chemin_sortie_base,
            "-t", str(nb_threads or min(os.cpu_count() or 4, 8)),
        ]

        logger.info(f"Transcription whisper-cpp (modèle {modele}, CPU)...")
        result = subprocess.run(cmd_whisper, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            logger.warning(f"whisper-cpp stderr : {result.stderr[-200:]}")

        # Lire le fichier JSON de sortie
        chemin_json = chemin_sortie_base + ".json"
        if not os.path.exists(chemin_json):
            # Essayer le format SRT comme fallback
            return _lire_sortie_texte(chemin_sortie_base, result.stdout)

        with open(chemin_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Parser les segments
        segments = []
        for seg in data.get("transcription", []):
            offsets = seg.get("offsets", {})
            texte = seg.get("text", "").strip()
            if texte:
                segments.append({
                    "debut": offsets.get("from", 0) / 1000.0,  # ms → secondes
                    "fin": offsets.get("to", 0) / 1000.0,
                    "texte": texte
                })

        return segments if segments else None

    except subprocess.TimeoutExpired:
        logger.error("Timeout whisper-cpp (>10 min)")
        return None
    except Exception as e:
        logger.error(f"Erreur whisper-cpp : {e}")
        return None
    finally:
        for f in [chemin_wav, chemin_sortie_base,
                  chemin_sortie_base + ".json",
                  chemin_sortie_base + ".txt",
                  chemin_sortie_base + ".srt"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass


def _lire_sortie_texte(chemin_base: str, stdout: str) -> Optional[List[Dict]]:
    """
    Fallback : parse la sortie texte ou SRT si JSON indisponible.
    """
    # Essayer le fichier SRT
    chemin_srt = chemin_base + ".srt"
    if os.path.exists(chemin_srt):
        return _parser_srt(chemin_srt)

    # Essayer de parser depuis stdout
    if stdout.strip():
        return _parser_stdout_whisper(stdout)

    return None


def _parser_srt(chemin_srt: str) -> List[Dict]:
    """Parse un fichier SRT en liste de segments."""
    segments = []
    with open(chemin_srt, "r", encoding="utf-8") as f:
        contenu = f.read()

    # Pattern SRT : numéro, timestamps, texte
    pattern = r"\d+\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+?)(?=\n\n|\Z)"
    for match in re.finditer(pattern, contenu, re.DOTALL):
        debut_str, fin_str, texte = match.groups()
        segments.append({
            "debut": _srt_temps_en_sec(debut_str),
            "fin": _srt_temps_en_sec(fin_str),
            "texte": texte.strip().replace("\n", " ")
        })
    return segments


def _srt_temps_en_sec(temps: str) -> float:
    """Convertit HH:MM:SS,mmm en secondes."""
    h, m, reste = temps.split(":")
    s, ms = reste.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _parser_stdout_whisper(stdout: str) -> List[Dict]:
    """Parse la sortie texte brute de whisper-cpp avec timestamps."""
    segments = []
    # Format : [HH:MM:SS.mmm --> HH:MM:SS.mmm] texte
    pattern = r"\[(\d{2}:\d{2}:\d{2}\.\d+) --> (\d{2}:\d{2}:\d{2}\.\d+)\]\s*(.+)"
    for match in re.finditer(pattern, stdout):
        debut_str, fin_str, texte = match.groups()
        segments.append({
            "debut": _whisper_temps_en_sec(debut_str),
            "fin": _whisper_temps_en_sec(fin_str),
            "texte": texte.strip()
        })
    return segments


def _whisper_temps_en_sec(temps: str) -> float:
    """Convertit HH:MM:SS.mmm en secondes."""
    h, m, reste = temps.split(":")
    s_ms = float(reste)
    return int(h) * 3600 + int(m) * 60 + s_ms


def a_parole_detectee(segments: Optional[List[Dict]], seuil_confiance: float = 0.4) -> bool:
    """Vérifie si des segments valides ont été détectés."""
    if not segments:
        return False
    # Compter les segments avec du vrai texte (pas juste musique)
    segments_valides = [
        s for s in segments
        if len(s.get("texte", "").split()) >= 2
    ]
    return len(segments_valides) >= 2


def grouper_segments(segments: List[Dict], max_chars: int = 35) -> List[Dict]:
    """
    Groupe les segments courts en lignes de max_chars caractères max.
    Garde les segments déjà bien dimensionnés.
    """
    groupes = []
    for seg in segments:
        texte = seg["texte"].strip()
        if not texte:
            continue

        # Si le segment est trop long, le découper
        if len(texte) > max_chars:
            mots = texte.split()
            ligne = []
            chars = 0
            debut_frac = seg["debut"]
            duree = seg["fin"] - seg["debut"]
            duree_par_mot = duree / max(1, len(mots))

            for i, mot in enumerate(mots):
                if chars + len(mot) + 1 > max_chars and ligne:
                    fin_frac = debut_frac + duree_par_mot * len(ligne)
                    groupes.append({
                        "debut": debut_frac,
                        "fin": fin_frac,
                        "texte": " ".join(ligne)
                    })
                    debut_frac = fin_frac
                    ligne = [mot]
                    chars = len(mot)
                else:
                    ligne.append(mot)
                    chars += len(mot) + 1

            if ligne:
                groupes.append({
                    "debut": debut_frac,
                    "fin": seg["fin"],
                    "texte": " ".join(ligne)
                })
        else:
            groupes.append(seg)

    return groupes


def analyser_luminosite(chemin_video: str, temps_sec: float, position_bas: float = 0.15) -> float:
    """
    Analyse la luminosité de la zone sous-titre à un instant donné.
    Retourne une valeur de 0 (noir) à 255 (blanc).
    """
    cap = cv2.VideoCapture(chemin_video)
    if not cap.isOpened():
        return 128.0

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_idx = min(int(temps_sec * fps), int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) - 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ret, frame = cap.read()
    cap.release()

    if not ret:
        return 128.0

    h, w = frame.shape[:2]
    y_bas = h - int(h * position_bas)
    y_haut = y_bas - int(h * 0.15)
    zone = frame[max(0, y_haut):y_bas, :]

    if len(zone) == 0:
        return 128.0

    gris = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    return float(gris.mean())


def generer_ass(segments: List[Dict], chemin_video: str, cfg_sub: Dict) -> str:
    """
    Génère un fichier ASS (Advanced SubStation Alpha) avec couleurs adaptatives.

    Analyse la luminosité de la zone sous-titre pour chaque segment et
    choisit automatiquement texte blanc (fond sombre) ou noir (fond clair).
    """
    seuil_lum = cfg_sub.get("seuil_luminosite", 128)
    taille_rel = cfg_sub.get("taille_police_relative", 0.042)
    contour = cfg_sub.get("epaisseur_contour", 3)
    pos_bas = cfg_sub.get("position_bas_pourcent", 0.15)
    taille_police = int(1920 * taille_rel)
    marge_v = int(1920 * pos_bas)

    entete = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sombre,Arial,{taille_police},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{contour},0,2,30,30,{marge_v},1
Style: Clair,Arial,{taille_police},&H00000000,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,{contour},0,2,30,30,{marge_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def fmt_temps(s: float) -> str:
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        cs = int((s - int(s)) * 100)
        return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"

    lignes = []
    for seg in segments:
        debut, fin = seg["debut"], seg["fin"]
        texte = seg["texte"].strip()
        if not texte:
            continue

        # Analyser luminosité au milieu du segment
        temps_milieu = (debut + fin) / 2
        luminosite = analyser_luminosite(chemin_video, temps_milieu, pos_bas)
        style = "Sombre" if luminosite < seuil_lum else "Clair"

        # Échapper les caractères spéciaux ASS
        texte_esc = (
            texte.replace("\\", "\\\\")
                 .replace("{", "\\{")
                 .replace("}", "\\}")
                 .replace("\n", "\\N")
        )
        lignes.append(
            f"Dialogue: 0,{fmt_temps(debut)},{fmt_temps(fin)},{style},,0,0,0,,{texte_esc}"
        )

    return entete + "\n".join(lignes) + "\n"


def bruler_sous_titres(chemin_entree: str, chemin_sortie: str, chemin_ass: str) -> bool:
    """Brûle un fichier ASS dans la vidéo via ffmpeg."""
    # Échapper le chemin pour le filtre ass= de ffmpeg
    chemin_esc = chemin_ass.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    cmd = [
        "ffmpeg", "-y",
        "-i", chemin_entree,
        "-vf", f"ass='{chemin_esc}'",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        chemin_sortie
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            logger.error(f"Erreur ffmpeg ASS : {r.stderr[-300:]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Erreur brûlage : {e}")
        return False


def ajouter_sous_titres(
    chemin_entree: str,
    chemin_sortie: str,
    config: Dict,
    callback=None
) -> bool:
    """
    Pipeline complet : transcription whisper-cpp + ASS adaptatif + brûlage.

    Si whisper-cpp n'est pas installé ou si aucune parole n'est détectée,
    la vidéo est copiée sans sous-titres (comportement normal, pas une erreur).

    Returns:
        True si succès (avec ou sans sous-titres)
    """
    def log(msg):
        logger.info(msg)
        if callback:
            callback(msg)

    os.makedirs(os.path.dirname(chemin_sortie), exist_ok=True)

    cfg_sub = config.get("sous_titres", {})
    modele = cfg_sub.get("modele_whisper", "base")
    langue = cfg_sub.get("langue", "en")
    seuil = cfg_sub.get("seuil_confiance", 0.4)
    max_chars = cfg_sub.get("max_chars_par_ligne", 35)

    def copier_sans_sous_titres():
        cmd = ["ffmpeg", "-y", "-i", chemin_entree, "-c", "copy", chemin_sortie]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        return r.returncode == 0

    # Vérifier si la vidéo contient un flux audio
    from modules.ffmpeg_utils import has_audio_stream
    if not has_audio_stream(chemin_entree):
        log("Pas de flux audio détecté → pas de sous-titres")
        return copier_sans_sous_titres()

    # Vérifier si whisper-cpp est disponible
    if not trouver_whisper_cpp():
        log("⚠️ whisper-cpp non installé — vidéo copiée sans sous-titres")
        log("   Installez : brew install whisper-cpp")
        return copier_sans_sous_titres()

    nb_threads = cfg_sub.get("whisper_threads", min(os.cpu_count() or 4, 8))
    log(f"Transcription whisper-cpp (modèle '{modele}', {nb_threads} threads CPU)...")
    segments = transcrire_whisper_cpp(chemin_entree, modele=modele, langue=langue, nb_threads=nb_threads)

    if not a_parole_detectee(segments):
        log("Aucune parole détectée → pas de sous-titres")
        return copier_sans_sous_titres()

    nb_mots = sum(len(s.get("texte", "").split()) for s in segments)
    log(f"Transcription : {len(segments)} segments, ~{nb_mots} mots")

    # Grouper les segments
    segments_groupes = grouper_segments(segments, max_chars=max_chars)
    log(f"{len(segments_groupes)} segments d'affichage")

    # Générer le fichier ASS
    log("Analyse luminosité et génération des sous-titres adaptatifs...")
    contenu_ass = generer_ass(segments_groupes, chemin_entree, cfg_sub)

    chemin_ass = chemin_sortie.replace(".mp4", "_tmp.ass")
    with open(chemin_ass, "w", encoding="utf-8") as f:
        f.write(contenu_ass)

    # Brûler
    log("Brûlage des sous-titres...")
    succes = bruler_sous_titres(chemin_entree, chemin_sortie, chemin_ass)

    if os.path.exists(chemin_ass):
        os.remove(chemin_ass)

    if succes:
        log(f"✅ Sous-titres ajoutés : {os.path.basename(chemin_sortie)}")
    else:
        log("❌ Échec brûlage → copie sans sous-titres")
        succes = copier_sans_sous_titres()

    return succes
