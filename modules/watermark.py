"""
watermark.py — Module 5 : Filigrane "divertissement45000"

Ajoute le watermark texte centré avec ffmpeg drawtext.
Opacité ~15-20%, texte blanc avec ombre noire, pendant toute la durée.
"""

import logging
import os
import subprocess
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def trouver_police(config: Dict) -> str:
    """
    Cherche une police sans-serif bold disponible sur le système.
    Retourne le chemin de la police ou le nom de la police système.
    """
    # Ordre de préférence : polices système macOS
    polices_candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFCompactDisplay-Bold.otf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
        # Police custom dans le projet
        os.path.join(config.get("chemins", {}).get("fonts_dir", "assets/fonts"), "font.ttf")
    ]

    for chemin in polices_candidates:
        if os.path.exists(chemin):
            return chemin

    # Fallback : laisser ffmpeg utiliser sa police par défaut
    return ""


def ajouter_numero_partie(
    chemin_entree: str,
    chemin_sortie: str,
    numero: int,
    total: int,
    config: Dict,
    callback: Optional[callable] = None
) -> bool:
    """
    Ajoute "PARTIE X/Y" en haut à gauche de la vidéo.
    Texte blanc gras avec contour noir, bien visible.
    """
    def log(msg):
        logger.info(msg)
        if callback:
            callback(msg)

    os.makedirs(os.path.dirname(chemin_sortie), exist_ok=True)

    chemin_police = trouver_police(config)
    police_option = f":fontfile='{chemin_police}'" if chemin_police else ""
    texte = f"PARTIE {numero}/{total}"
    taille_px = 52  # Taille fixe lisible sur 1080x1920

    # Texte blanc avec contour noir épais — toujours visible
    filtre_contour = (
        f"drawtext=text='{texte}'"
        f":fontsize={taille_px}"
        f"{police_option}"
        f":fontcolor=black"
        f":x=30:y=30"
        f":borderw=4:bordercolor=black"
    )
    filtre_texte = (
        f"drawtext=text='{texte}'"
        f":fontsize={taille_px}"
        f"{police_option}"
        f":fontcolor=white"
        f":x=30:y=30"
        f":borderw=3:bordercolor=black"
    )
    filtre_vf = f"{filtre_contour},{filtre_texte}"

    cmd = [
        "ffmpeg", "-y",
        "-i", chemin_entree,
        "-vf", filtre_vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        chemin_sortie
    ]

    log(f"Ajout '{texte}'...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log(f"❌ Erreur partie : {result.stderr[-200:]}")
            return False
        log(f"✅ '{texte}' ajouté")
        return True
    except Exception as e:
        log(f"❌ Erreur : {e}")
        return False


def ajouter_watermark(
    chemin_entree: str,
    chemin_sortie: str,
    config: Dict,
    callback: Optional[callable] = None
) -> bool:
    """
    Ajoute le filigrane "divertissement45000" centré sur la vidéo.

    Le texte est affiché pendant toute la durée avec :
    - Opacité 15-20% (alpha 0.15-0.20)
    - Taille ~5% de la hauteur de la vidéo
    - Couleur blanche avec ombre portée noire
    - Police sans-serif bold

    Args:
        chemin_entree: Vidéo en entrée (déjà convertie en 9:16)
        chemin_sortie: Vidéo avec watermark en sortie
        config: Configuration globale
        callback: Fonction de progression

    Returns:
        True si succès, False sinon
    """
    def log(msg):
        logger.info(msg)
        if callback:
            callback(msg)

    os.makedirs(os.path.dirname(chemin_sortie), exist_ok=True)

    cfg_wm = config.get("watermark", {})
    texte = cfg_wm.get("texte", "divertissement45000")
    opacite = cfg_wm.get("opacite", 0.18)
    taille_relative = cfg_wm.get("taille_relative", 0.05)

    # Taille en pixels pour une vidéo 1920px de haut
    taille_px = int(1920 * taille_relative)  # ~96px

    # Trouver une police disponible
    chemin_police = trouver_police(config)

    # Construire le filtre drawtext
    # alpha='0.18' → 18% d'opacité (très discret)
    # shadowx/shadowy → ombre portée légère pour lisibilité sur tous les fonds

    params_communs = (
        f"text='{texte}'"
        f":fontsize={taille_px}"
        f":x=(w-text_w)/2"
        f":y=(h-text_h)/2"
        f":alpha={opacite:.2f}"
    )

    if chemin_police:
        police_option = f":fontfile='{chemin_police}'"
    else:
        police_option = ""

    # Deux drawtext superposés : ombre noire + texte blanc
    # Ombre : décalage +2px, couleur noire, même opacité
    filtre_ombre = (
        f"drawtext="
        f"{params_communs}"
        f"{police_option}"
        f":fontcolor=black"
        f":shadowx=2:shadowy=2"
        f":shadowcolor=black@{opacite:.2f}"
    )

    # Texte principal blanc
    filtre_texte = (
        f"drawtext="
        f"{params_communs}"
        f"{police_option}"
        f":fontcolor=white@{opacite:.2f}"
        f":shadowx=2:shadowy=2"
        f":shadowcolor=black@{opacite:.2f}"
    )

    # Combiner les deux filtres
    filtre_vf = f"{filtre_ombre},{filtre_texte}"

    cmd = [
        "ffmpeg", "-y",
        "-i", chemin_entree,
        "-vf", filtre_vf,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        chemin_sortie
    ]

    log(f"Ajout du filigrane '{texte}'...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300
        )
        if result.returncode != 0:
            log(f"❌ Erreur ffmpeg watermark : {result.stderr[-300:]}")
            logger.error(f"Stderr complet : {result.stderr}")
            return False

        log(f"✅ Filigrane ajouté : {os.path.basename(chemin_sortie)}")
        return True

    except subprocess.TimeoutExpired:
        log("❌ Délai dépassé lors de l'ajout du filigrane")
        return False
    except Exception as e:
        log(f"❌ Erreur inattendue : {e}")
        logger.exception("Erreur ajouter_watermark")
        return False
