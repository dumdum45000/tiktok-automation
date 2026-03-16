"""
converter.py — Module 4 : Conversion en portrait 9:16

Convertit les clips en format TikTok 1080x1920 (portrait 9:16).
- Vidéo 16:9 → fond flou zoomé + vidéo centrée par-dessus
- Vidéo déjà 9:16 → simple redimensionnement
- Amélioration automatique : saturation +10%, contraste +5%
"""

import logging
import os
import subprocess
from typing import Dict, Optional

from modules.ffmpeg_utils import get_dimensions_video

logger = logging.getLogger(__name__)

LARGEUR_CIBLE = 1080
HAUTEUR_CIBLE = 1920


def construire_filtre_fond_flou(
    largeur_src: int,
    hauteur_src: int,
    saturation: float = 1.1,
    contraste: float = 1.05,
    sigma_flou: int = 20
) -> str:
    """
    Construit le filtre ffmpeg complexe pour le fond flou + vidéo centrée.

    L'arrière-plan est la vidéo zooomée et floutée pour remplir 1080x1920.
    La vidéo originale est centrée par-dessus à sa taille naturelle.
    """
    # Ratio d'aspect source et cible
    ratio_src = largeur_src / hauteur_src  # ex: 1.778 pour 16:9
    ratio_cible = LARGEUR_CIBLE / HAUTEUR_CIBLE  # 0.5625 pour 9:16

    # Taille de la vidéo principale centrée
    # Si source plus large que cible → contraindre par largeur
    if ratio_src > ratio_cible:
        w_main = LARGEUR_CIBLE
        h_main = int(LARGEUR_CIBLE / ratio_src)
    else:
        h_main = HAUTEUR_CIBLE
        w_main = int(HAUTEUR_CIBLE * ratio_src)

    # S'assurer que les dimensions sont paires (requis par h264)
    w_main = w_main if w_main % 2 == 0 else w_main - 1
    h_main = h_main if h_main % 2 == 0 else h_main - 1

    # Position du clip principal (centré)
    x_main = (LARGEUR_CIBLE - w_main) // 2
    y_main = (HAUTEUR_CIBLE - h_main) // 2

    # Construire le filtre complexe
    # [0:v] = flux vidéo source
    # 1. Fond : scale + zoom pour couvrir 1080x1920, puis gblur
    # 2. Principal : scale aux dimensions calculées, amélioration couleurs
    # 3. Overlay : superposer principal sur fond
    filtre = (
        f"[0:v]split=2[src_bg][src_fg];"
        # Fond : zoomer pour couvrir, puis flouter
        f"[src_bg]scale={LARGEUR_CIBLE}:{HAUTEUR_CIBLE}:force_original_aspect_ratio=increase,"
        f"crop={LARGEUR_CIBLE}:{HAUTEUR_CIBLE},"
        f"gblur=sigma={sigma_flou},"
        f"eq=saturation={saturation * 0.7:.2f}:contrast=0.8[bg];"
        # Vidéo principale : redimensionner + amélioration couleurs
        f"[src_fg]scale={w_main}:{h_main},"
        f"eq=saturation={saturation:.2f}:contrast={contraste:.2f},"
        f"unsharp=5:5:0.8:5:5:0[fg];"
        # Superposition
        f"[bg][fg]overlay={x_main}:{y_main}"
    )
    return filtre


def construire_filtre_portrait_direct(
    saturation: float = 1.1,
    contraste: float = 1.05
) -> str:
    """
    Filtre pour une vidéo déjà en 9:16 ou proche : simple scale + amélioration.
    """
    return (
        f"scale={LARGEUR_CIBLE}:{HAUTEUR_CIBLE}:force_original_aspect_ratio=decrease,"
        f"pad={LARGEUR_CIBLE}:{HAUTEUR_CIBLE}:(ow-iw)/2:(oh-ih)/2:black,"
        f"eq=saturation={saturation:.2f}:contrast={contraste:.2f},"
        f"unsharp=5:5:0.8:5:5:0"
    )


def convertir_en_portrait(
    chemin_entree: str,
    chemin_sortie: str,
    config: Dict,
    callback: Optional[callable] = None
) -> bool:
    """
    Convertit un clip en portrait 9:16 1080x1920.

    Détecte automatiquement le format source et choisit la méthode appropriée.

    Args:
        chemin_entree: Clip source
        chemin_sortie: Clip converti
        config: Configuration (saturation, contraste, etc.)
        callback: Fonction de progression

    Returns:
        True si succès, False sinon
    """
    def log(msg):
        logger.info(msg)
        if callback:
            callback(msg)

    os.makedirs(os.path.dirname(chemin_sortie), exist_ok=True)

    cfg_conv = config.get("conversion", {})
    saturation = cfg_conv.get("saturation", 1.1)
    contraste = cfg_conv.get("contraste", 1.05)
    sigma_flou = cfg_conv.get("flou_arriere_plan_sigma", 20)

    # Détecter les dimensions source
    dims = get_dimensions_video(chemin_entree)
    if dims is None:
        log("❌ Impossible de lire les dimensions de la vidéo")
        return False

    largeur_src, hauteur_src = dims
    ratio_src = largeur_src / hauteur_src
    ratio_portrait = LARGEUR_CIBLE / HAUTEUR_CIBLE  # ~0.5625

    log(f"Dimensions source : {largeur_src}x{hauteur_src} (ratio {ratio_src:.3f})")

    # Choisir la méthode de conversion
    # Si la vidéo est déjà proche du portrait (ratio < 0.75), simple scale
    # Sinon, fond flou
    if ratio_src < 0.75:
        log("Format portrait détecté → redimensionnement simple")
        vf_filtre = construire_filtre_portrait_direct(saturation, contraste)
        cmd = [
            "ffmpeg", "-y",
            "-i", chemin_entree,
            "-vf", vf_filtre,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            chemin_sortie
        ]
    else:
        log("Format paysage détecté → fond flou + vidéo centrée")
        filtre_complexe = construire_filtre_fond_flou(
            largeur_src, hauteur_src, saturation, contraste, sigma_flou
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", chemin_entree,
            "-filter_complex", filtre_complexe,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            chemin_sortie
        ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600
        )
        if result.returncode != 0:
            log(f"❌ Erreur ffmpeg : {result.stderr[-300:]}")
            logger.error(f"Commande échouée : {' '.join(cmd)}")
            logger.error(f"Stderr : {result.stderr}")
            return False

        taille_mo = os.path.getsize(chemin_sortie) / (1024 ** 2)
        log(f"✅ Converti : {os.path.basename(chemin_sortie)} ({taille_mo:.1f} Mo)")
        return True

    except subprocess.TimeoutExpired:
        log("❌ Délai de conversion dépassé (10 min)")
        return False
    except Exception as e:
        log(f"❌ Erreur inattendue : {e}")
        logger.exception("Erreur convertir_en_portrait")
        return False
