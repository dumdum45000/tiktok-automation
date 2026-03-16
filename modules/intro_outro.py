"""
intro_outro.py — Module 8 : Intro et Outro générées dynamiquement

Génère via ffmpeg lavfi :
- INTRO 3s : fond dégradé + texte "divertissement45000" fade-in
- OUTRO 4s : dernière frame floutée + texte "Follow @divertissement45000" + "Like & Share"
- Transitions de 0.5s en fondu enchaîné

Tout généré dynamiquement, aucun fichier pré-créé nécessaire.
L'intro + outro sont comptées dans la limite de 60s (clip utile max 53s).
"""

import logging
import os
import subprocess
import tempfile
from typing import Dict, Optional, Tuple

from modules.ffmpeg_utils import get_duree_video

logger = logging.getLogger(__name__)


def hex_to_ffmpeg_couleur(hex_str: str) -> str:
    """Convertit un code hex (ex: '1a1a2e') en format ffmpeg (ex: '0x1a1a2e')."""
    return f"0x{hex_str.lstrip('#').upper()}"


def extraire_derniere_frame(chemin_video: str, chemin_sortie: str) -> bool:
    """Extrait la dernière frame d'une vidéo en image PNG."""
    cmd = [
        "ffmpeg", "-y",
        "-sseof", "-0.1",
        "-i", chemin_video,
        "-vframes", "1",
        "-q:v", "2",
        chemin_sortie
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0 and os.path.exists(chemin_sortie)
    except Exception as e:
        logger.error(f"Erreur extraction dernière frame : {e}")
        return False


def trouver_police(config: Dict) -> str:
    """Trouve une police système disponible (réutilise la même logique que watermark)."""
    polices_candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    ]
    for chemin in polices_candidates:
        if os.path.exists(chemin):
            return chemin
    return ""


def generer_intro(
    config_intro: Dict,
    chemin_sortie: str,
    duree: float = 3.0,
    police: str = ""
) -> bool:
    """
    Génère la séquence intro en 1080x1920 via ffmpeg lavfi.

    Effet : fond uni dégradé + texte fade-in après 0.5s.

    Args:
        config_intro: Configuration intro (couleurs, texte, taille police)
        chemin_sortie: Chemin du fichier vidéo intro généré
        duree: Durée de l'intro en secondes
        police: Chemin vers la police

    Returns:
        True si succès
    """
    couleur_fond = hex_to_ffmpeg_couleur(config_intro.get("couleur_fond_hex", "1a1a2e"))
    couleur_texte = config_intro.get("couleur_texte_hex", "FFFFFF")
    couleur_accent = hex_to_ffmpeg_couleur(config_intro.get("couleur_accent_hex", "e94560"))
    texte = config_intro.get("texte_principal", "divertissement45000")
    taille_police = config_intro.get("taille_police", 72)

    # Échapper le @ pour ffmpeg
    texte_esc = texte.replace("@", "\\@").replace("'", "\\'").replace(":", "\\:")

    option_police = f":fontfile='{police}'" if police else ""

    # Filtre : fond couleur + ligne décorative + texte principal avec fade-in
    # Le texte apparaît progressivement entre t=0.3 et t=1.0
    filtre = (
        # Source : fond couleur plein
        f"color=c={couleur_fond}:s=1080x1920:d={duree}:r=30[fond];"
        # Ligne décorative colorée (barre horizontale accent)
        f"[fond]drawbox="
        f"x=200:y=(h/2)+{int(taille_police * 0.8)}:"
        f"w=680:h=4:"
        f"color={couleur_accent}@1.0:t=fill"
        f"[avec_barre];"
        # Texte principal : fade-in entre t=0.3 et t=1.2
        f"[avec_barre]drawtext="
        f"text='{texte_esc}'"
        f":fontsize={taille_police}"
        f"{option_police}"
        f":fontcolor=0x{couleur_texte}@1.0"
        f":x=(w-text_w)/2"
        f":y=(h-text_h)/2"
        f":alpha='if(lt(t,0.3),0,if(lt(t,1.2),(t-0.3)/0.9,1))'"
        f":shadowx=3:shadowy=3:shadowcolor=black@0.5"
        f"[video_out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-filter_complex", filtre,
        "-map", "[video_out]",
        # Audio silence pour la même durée
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duree}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(duree),
        "-movflags", "+faststart",
        chemin_sortie
    ]

    # Simplification : utiliser deux inputs séparés pour vidéo et audio
    cmd_simplifie = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={couleur_fond}:s=1080x1920:d={duree}:r=30",
        "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=stereo",
        "-filter_complex",
        (
            f"[0:v]"
            f"drawbox=x=200:y=(h/2)+{int(taille_police * 0.8)}:"
            f"w=680:h=4:color={couleur_accent}@1.0:t=fill,"
            f"drawtext="
            f"text='{texte_esc}'"
            f":fontsize={taille_police}"
            f"{option_police}"
            f":fontcolor=0x{couleur_texte}@1.0"
            f":x=(w-text_w)/2"
            f":y=(h-text_h)/2"
            f":alpha='if(lt(t,0.3),0,if(lt(t,1.2),(t-0.3)/0.9,1))'"
            f":shadowx=3:shadowy=3:shadowcolor=black@0.5"
            f"[v]"
        ),
        "-map", "[v]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(duree),
        "-movflags", "+faststart",
        chemin_sortie
    ]

    try:
        result = subprocess.run(cmd_simplifie, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"Erreur génération intro : {result.stderr[-500:]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Exception génération intro : {e}")
        return False


def generer_outro(
    chemin_derniere_frame: str,
    config_outro: Dict,
    chemin_sortie: str,
    duree: float = 4.0,
    police: str = ""
) -> bool:
    """
    Génère l'outro à partir de la dernière frame du clip floutée.

    Effets : dernière frame floutée progressivement + textes fade-in.

    Args:
        chemin_derniere_frame: Image PNG de la dernière frame
        config_outro: Configuration outro
        chemin_sortie: Fichier vidéo outro généré
        duree: Durée de l'outro
        police: Chemin vers la police

    Returns:
        True si succès
    """
    couleur_texte_hex = config_outro.get("couleur_texte_hex", "FFFFFF")
    texte_principal = config_outro.get("texte_principal", "Follow @divertissement45000")
    texte_secondaire = config_outro.get("texte_secondaire", "Like & Share")
    taille_principale = config_outro.get("taille_police_principale", 64)
    taille_secondaire = config_outro.get("taille_police_secondaire", 48)

    # Échapper les caractères spéciaux
    texte_p_esc = texte_principal.replace("@", "\\@").replace("'", "\\'").replace(":", "\\:")
    texte_s_esc = texte_secondaire.replace("'", "\\'").replace(":", "\\:")

    option_police = f":fontfile='{police}'" if police else ""

    # Position Y : texte principal au centre, secondaire un peu en dessous
    y_principal = "(h/2) - 60"
    y_secondaire = "(h/2) + 60"

    # Si pas de dernière frame disponible, utiliser un fond noir
    if chemin_derniere_frame and os.path.exists(chemin_derniere_frame):
        source_video = f"-loop 1 -i {chemin_derniere_frame}"
        filtre_fond = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,"
            f"gblur=sigma=15,"  # Flou fixe (sigma dynamique non supporté par tous les builds)
            f"eq=brightness=-0.15"  # Légèrement assombri
        )
    else:
        source_video = "-f lavfi -i color=c=0x000000:s=1080x1920:d={duree}:r=30"
        filtre_fond = "[0:v]"

    filtre_complet = (
        # Note : gblur sigma dynamique non supporté par certains builds ffmpeg statiques
        # On utilise un sigma fixe
        f"{filtre_fond}"
        # Texte principal
        f",drawtext="
        f"text='{texte_p_esc}'"
        f":fontsize={taille_principale}"
        f"{option_police}"
        f":fontcolor=white@1.0"
        f":x=(w-text_w)/2"
        f":y={y_principal}"
        f":alpha='if(lt(t,0.5),0,if(lt(t,1.5),(t-0.5)/1.0,1))'"
        f":shadowx=3:shadowy=3:shadowcolor=black@0.7"
        # Texte secondaire
        f",drawtext="
        f"text='{texte_s_esc}'"
        f":fontsize={taille_secondaire}"
        f"{option_police}"
        f":fontcolor=white@0.85"
        f":x=(w-text_w)/2"
        f":y={y_secondaire}"
        f":alpha='if(lt(t,1.0),0,if(lt(t,2.0),(t-1.0)/1.0,1))'"
        f":shadowx=2:shadowy=2:shadowcolor=black@0.7"
    )

    if chemin_derniere_frame and os.path.exists(chemin_derniere_frame):
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-framerate", "30",      # Forcer 30fps comme le clip
            "-i", chemin_derniere_frame,
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=stereo",
            "-filter_complex", filtre_complet + ",format=yuv420p[v]",  # Forcer yuv420p
            "-map", "[v]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-r", "30",              # Forcer 30fps en sortie
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", str(duree),
            "-movflags", "+faststart",
            chemin_sortie
        ]
    else:
        # Fallback : fond noir
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s=1080x1920:d={duree}:r=30",
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=stereo",
            "-filter_complex",
            (
                f"[0:v]"
                f"drawtext=text='{texte_p_esc}':fontsize={taille_principale}"
                f"{option_police}:fontcolor=white:x=(w-text_w)/2:y={y_principal}"
                f":alpha='if(lt(t,0.5),0,if(lt(t,1.5),(t-0.5)/1.0,1))'"
                f":shadowx=3:shadowy=3:shadowcolor=black@0.7,"
                f"drawtext=text='{texte_s_esc}':fontsize={taille_secondaire}"
                f"{option_police}:fontcolor=white@0.85:x=(w-text_w)/2:y={y_secondaire}"
                f":alpha='if(lt(t,1.0),0,if(lt(t,2.0),(t-1.0)/1.0,1))'"
                f":shadowx=2:shadowy=2:shadowcolor=black@0.7,"
                f"format=yuv420p[v]"
            ),
            "-map", "[v]",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-r", "30",
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", str(duree),
            "-movflags", "+faststart",
            chemin_sortie
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"Erreur génération outro : {result.stderr[-500:]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Exception génération outro : {e}")
        return False


def concatener_avec_transitions(
    chemin_intro: str,
    chemin_clip: str,
    chemin_outro: str,
    chemin_sortie: str,
    duree_transition: float = 0.5
) -> bool:
    """
    Concatène intro + clip + outro avec des fondus enchaînés entre chaque segment.

    Utilise ffmpeg xfade pour les transitions fluides.

    Args:
        chemin_intro: Fichier intro
        chemin_clip: Clip principal
        chemin_outro: Fichier outro
        chemin_sortie: Fichier final assemblé

    Returns:
        True si succès
    """
    # Obtenir les durées pour calculer les offsets xfade
    duree_intro = get_duree_video(chemin_intro) or 3.0
    duree_clip = get_duree_video(chemin_clip) or 3.0

    # Offset pour la transition intro→clip
    offset_1 = duree_intro - duree_transition

    # Filtre xfade pour les transitions
    # Transition intro → clip à offset_1
    # Transition clip → outro : calculé après la durée de (intro+clip - transition)
    duree_total_1 = duree_intro + duree_clip - duree_transition
    offset_2 = duree_total_1 - duree_transition

    filtre = (
        # Transition intro → clip
        f"[0:v][1:v]xfade=transition=fade:duration={duree_transition}:offset={offset_1:.3f}[v01];"
        f"[0:a][1:a]acrossfade=d={duree_transition}[a01];"
        # Transition (intro+clip) → outro
        f"[v01][2:v]xfade=transition=fade:duration={duree_transition}:offset={offset_2:.3f}[vfinal];"
        f"[a01][2:a]acrossfade=d={duree_transition}[afinal]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", chemin_intro,
        "-i", chemin_clip,
        "-i", chemin_outro,
        "-filter_complex", filtre,
        "-map", "[vfinal]",
        "-map", "[afinal]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        chemin_sortie
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error(f"Erreur concatenation : {result.stderr[-500:]}")
            # Fallback : concaténation simple sans transitions
            return concatener_simple(chemin_intro, chemin_clip, chemin_outro, chemin_sortie)
        return True
    except Exception as e:
        logger.error(f"Exception concatenation : {e}")
        return concatener_simple(chemin_intro, chemin_clip, chemin_outro, chemin_sortie)


def concatener_simple(
    chemin_intro: str,
    chemin_clip: str,
    chemin_outro: str,
    chemin_sortie: str
) -> bool:
    """
    Concaténation robuste via filter_complex concat.
    Ré-encode tout pour garantir la compatibilité des streams.
    """
    # Obtenir les durées de chaque segment
    d_intro = get_duree_video(chemin_intro) or 3.0
    d_clip = get_duree_video(chemin_clip) or 3.0
    d_outro = get_duree_video(chemin_outro) or 3.0

    logger.info(f"Concaténation : intro={d_intro:.1f}s, clip={d_clip:.1f}s, outro={d_outro:.1f}s")

    # filter_complex concat pour 3 segments vidéo+audio
    filtre = (
        "[0:v][0:a]"
        "[1:v][1:a]"
        "[2:v][2:a]"
        "concat=n=3:v=1:a=1[vout][aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", chemin_intro,
        "-i", chemin_clip,
        "-i", chemin_outro,
        "-filter_complex", filtre,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        chemin_sortie
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error(f"Erreur concaténation : {result.stderr[-300:]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Erreur concaténation simple : {e}")
        return False


def ajouter_intro_outro(
    chemin_entree: str,
    chemin_sortie: str,
    config: Dict,
    callback: Optional[callable] = None
) -> bool:
    """
    Pipeline complet : génère l'intro et l'outro puis assemble le clip final.

    Args:
        chemin_entree: Clip avec musique et sous-titres
        chemin_sortie: Clip final avec intro et outro
        config: Configuration globale
        callback: Fonction de progression

    Returns:
        True si succès
    """
    def log(msg):
        logger.info(msg)
        if callback:
            callback(msg)

    os.makedirs(os.path.dirname(chemin_sortie), exist_ok=True)

    cfg_io = config.get("intro_outro", {})
    duree_intro = cfg_io.get("duree_intro_secondes", 3)
    duree_outro = cfg_io.get("duree_outro_secondes", 4)
    duree_transition = cfg_io.get("duree_transition_secondes", 0.5)

    cfg_intro = cfg_io.get("intro", {})
    cfg_outro = cfg_io.get("outro", {})

    police = trouver_police(config)

    with tempfile.TemporaryDirectory() as dossier_tmp:
        chemin_intro_tmp = os.path.join(dossier_tmp, "intro.mp4")
        chemin_outro_tmp = os.path.join(dossier_tmp, "outro.mp4")
        chemin_frame_tmp = os.path.join(dossier_tmp, "last_frame.png")

        # Générer l'intro
        log("Génération de l'intro...")
        if not generer_intro(cfg_intro, chemin_intro_tmp, duree_intro, police):
            log("❌ Échec génération intro")
            return False
        log(f"✅ Intro générée ({duree_intro}s)")

        # Extraire la dernière frame pour l'outro
        log("Extraction de la dernière frame...")
        extraire_derniere_frame(chemin_entree, chemin_frame_tmp)

        # Générer l'outro
        log("Génération de l'outro...")
        if not generer_outro(chemin_frame_tmp, cfg_outro, chemin_outro_tmp, duree_outro, police):
            log("❌ Échec génération outro")
            return False
        log(f"✅ Outro générée ({duree_outro}s)")

        # Assembler intro + clip + outro
        log("Assemblage final avec transitions...")
        succes = concatener_avec_transitions(
            chemin_intro_tmp,
            chemin_entree,
            chemin_outro_tmp,
            chemin_sortie,
            duree_transition
        )

    if succes:
        taille_mo = os.path.getsize(chemin_sortie) / (1024 ** 2) if os.path.exists(chemin_sortie) else 0
        log(f"✅ Clip final assemblé : {os.path.basename(chemin_sortie)} ({taille_mo:.1f} Mo)")
    else:
        log("❌ Échec assemblage final")

    return succes
