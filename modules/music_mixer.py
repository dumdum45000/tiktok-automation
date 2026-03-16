"""
music_mixer.py — Module 7 : Musique de fond tendance

Sélectionne et mixe une musique de fond dans la bibliothèque locale
selon la catégorie du clip. La musique est toujours sous l'audio original.
Catégorie "musique" → pas de musique ajoutée (audio original intact).
"""

import logging
import os
import random
import subprocess
import tempfile
from typing import Dict, List, Optional

from modules.ffmpeg_utils import get_duree_video

logger = logging.getLogger(__name__)


def lister_musiques_categorie(dossier_music_library: str, categorie: str) -> List[str]:
    """
    Liste toutes les musiques disponibles pour une catégorie donnée.

    Args:
        dossier_music_library: Chemin vers music_library/
        categorie: Catégorie (sport, humour, autre)

    Returns:
        Liste des chemins vers les fichiers audio
    """
    sous_dossier = os.path.join(dossier_music_library, categorie.lower())

    if not os.path.exists(sous_dossier):
        logger.warning(f"Dossier musical absent : {sous_dossier}")
        return []

    extensions_audio = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"}
    musiques = []

    for fichier in os.listdir(sous_dossier):
        ext = os.path.splitext(fichier)[1].lower()
        if ext in extensions_audio:
            musiques.append(os.path.join(sous_dossier, fichier))

    return musiques


def selectionner_musique_aleatoire(musiques: List[str]) -> Optional[str]:
    """Sélectionne une musique aléatoire dans la liste."""
    if not musiques:
        return None
    return random.choice(musiques)


def detecter_niveau_audio(chemin_video: str) -> float:
    """
    Mesure le niveau RMS de l'audio d'une vidéo via ffmpeg volumedetect.
    Retourne le niveau moyen en dBFS (typiquement entre -60 et 0).
    Retourne -60 si pas d'audio.
    """
    cmd = [
        "ffmpeg",
        "-i", chemin_video,
        "-af", "volumedetect",
        "-vn", "-f", "null", "-"
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        # Chercher "mean_volume" dans la sortie
        for ligne in result.stderr.split("\n"):
            if "mean_volume" in ligne:
                # Format : "mean_volume: -23.5 dB"
                parties = ligne.split(":")
                if len(parties) >= 2:
                    try:
                        return float(parties[1].strip().split()[0])
                    except ValueError:
                        pass
    except Exception as e:
        logger.warning(f"Impossible de détecter le niveau audio : {e}")

    return -60.0  # Valeur par défaut (silence)


def mixer_musique(
    chemin_entree: str,
    chemin_sortie: str,
    chemin_musique: str,
    volume_fond: float,
    duree_clip: float,
    fondu_duree: float = 1.5
) -> bool:
    """
    Mixe la musique de fond avec l'audio original via ffmpeg amix.

    La musique est rognée ou répétée pour correspondre à la durée du clip.
    Un fondu sortant est appliqué en fin de musique.

    Args:
        chemin_entree: Vidéo source
        chemin_sortie: Vidéo avec musique mixée
        chemin_musique: Fichier audio de la musique de fond
        volume_fond: Volume de la musique (0.0 à 1.0)
        duree_clip: Durée du clip en secondes
        fondu_duree: Durée du fondu en secondes

    Returns:
        True si succès, False sinon
    """
    # Construire le filtre audio complexe
    # 1. Préparer la musique : découper à la durée du clip + fondu sortant
    # 2. Ajuster le volume
    # 3. Mixer avec l'audio original

    # `aloop` permet de répéter la musique si elle est plus courte que le clip
    filtre_audio = (
        f"[1:a]"
        f"aloop=loop=-1:size=2e+09,"  # Boucle infinie
        f"atrim=duration={duree_clip:.3f},"
        f"afade=t=out:st={max(0, duree_clip - fondu_duree):.3f}:d={fondu_duree:.3f},"
        f"volume={volume_fond:.3f}"
        f"[musique_fond];"
        f"[0:a][musique_fond]amix=inputs=2:duration=first:dropout_transition=2"
        f"[audio_final]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", chemin_entree,
        "-i", chemin_musique,
        "-filter_complex", filtre_audio,
        "-map", "0:v",
        "-map", "[audio_final]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        chemin_sortie
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"Erreur ffmpeg amix : {result.stderr[-500:]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Erreur mixer_musique : {e}")
        return False


def ajouter_musique_fond(
    chemin_entree: str,
    chemin_sortie: str,
    categorie: str,
    config: Dict,
    callback: Optional[callable] = None
) -> bool:
    """
    Pipeline complet d'ajout de musique de fond selon la catégorie.

    Catégorie "musique" → pas de musique ajoutée, copie directe.
    Autres catégories → sélection aléatoire + mixage.

    Args:
        chemin_entree: Vidéo avec sous-titres
        chemin_sortie: Vidéo avec musique de fond
        categorie: Catégorie du clip
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

    cfg_music = config.get("musique", {})
    dossier_music = config.get("chemins", {}).get("music_library", "music_library")
    vol_normal = cfg_music.get("volume_fond_normal", 0.25)
    vol_sans_audio = cfg_music.get("volume_fond_sans_audio", 0.80)
    seuil_silence = cfg_music.get("seuil_silence_db", -40)
    fondu_duree = cfg_music.get("fondu_duree_secondes", 1.5)

    # Catégorie musique → pas de fond musical (préserver l'audio original)
    if categorie == "musique":
        log("Catégorie Musique → audio original conservé (pas de fond musical)")
        cmd_copie = ["ffmpeg", "-y", "-i", chemin_entree, "-c", "copy", chemin_sortie]
        result = subprocess.run(cmd_copie, capture_output=True, timeout=60)
        return result.returncode == 0

    # Chercher une musique dans la catégorie
    musiques = lister_musiques_categorie(dossier_music, categorie)

    # Si pas de musique pour cette catégorie, essayer "autre"
    if not musiques and categorie != "autre":
        log(f"Pas de musique pour '{categorie}', tentative avec 'autre'...")
        musiques = lister_musiques_categorie(dossier_music, "autre")

    # Si toujours pas de musique, copier sans fond
    if not musiques:
        log(f"⚠️ Bibliothèque musicale vide pour '{categorie}' → sans musique de fond")
        log("Ajoutez des musiques dans music_library/ (voir guide dans le README)")
        cmd_copie = ["ffmpeg", "-y", "-i", chemin_entree, "-c", "copy", chemin_sortie]
        result = subprocess.run(cmd_copie, capture_output=True, timeout=60)
        return result.returncode == 0

    # Sélectionner une musique aléatoire
    chemin_musique = selectionner_musique_aleatoire(musiques)
    log(f"Musique sélectionnée : {os.path.basename(chemin_musique)}")

    # Détecter le niveau audio de la vidéo
    niveau_audio_db = detecter_niveau_audio(chemin_entree)
    log(f"Niveau audio original : {niveau_audio_db:.1f} dBFS")

    # Ajuster le volume de la musique selon l'audio présent
    if niveau_audio_db <= seuil_silence:
        volume_fond = vol_sans_audio
        log(f"Audio original silencieux → musique à {vol_sans_audio:.0%}")
    else:
        volume_fond = vol_normal
        log(f"Mixage musique à {vol_normal:.0%} du volume")

    # Obtenir la durée du clip
    duree_clip = get_duree_video(chemin_entree) or 60.0

    # Mixer
    log("Mixage audio en cours...")
    succes = mixer_musique(
        chemin_entree, chemin_sortie, chemin_musique,
        volume_fond, duree_clip, fondu_duree
    )

    if succes:
        log(f"✅ Musique de fond ajoutée : {os.path.basename(chemin_sortie)}")
    else:
        log("❌ Échec mixage → copie sans musique")
        cmd_copie = ["ffmpeg", "-y", "-i", chemin_entree, "-c", "copy", chemin_sortie]
        result = subprocess.run(cmd_copie, capture_output=True, timeout=60)
        succes = result.returncode == 0

    return succes
