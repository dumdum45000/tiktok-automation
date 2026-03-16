"""
ffmpeg_utils.py — Utilitaires FFmpeg/FFprobe partagés

Fonctions communes utilisées par converter, analyzer, intro_outro, music_mixer, subtitles.
Evite la duplication de code pour les opérations ffprobe/ffmpeg courantes.
"""

import json
import logging
import os
import subprocess
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def get_dimensions_video(chemin_video: str) -> Optional[Tuple[int, int]]:
    """
    Retourne (largeur, hauteur) d'une vidéo via ffprobe.
    Retourne None en cas d'erreur.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "v:0",
        chemin_video
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        if not streams:
            return None
        w = streams[0].get("width", 0)
        h = streams[0].get("height", 0)
        if w > 0 and h > 0:
            return w, h
    except Exception as e:
        logger.error(f"Erreur ffprobe dimensions : {e}")
    return None


def get_duree_video(chemin_video: str) -> Optional[float]:
    """
    Retourne la durée en secondes d'une vidéo via ffprobe.
    Retourne None en cas d'erreur.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        chemin_video
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
        duree = info.get("format", {}).get("duration")
        if duree:
            return float(duree)
    except Exception as e:
        logger.error(f"Erreur ffprobe durée : {e}")
    return None


def has_audio_stream(chemin_video: str) -> bool:
    """
    Vérifie si une vidéo contient un flux audio via ffprobe.
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "a",
        chemin_video
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return False
        info = json.loads(result.stdout)
        return len(info.get("streams", [])) > 0
    except Exception as e:
        logger.error(f"Erreur ffprobe audio : {e}")
        return False


def get_video_info(chemin_video: str) -> Optional[Dict]:
    """
    Retourne les informations complètes d'une vidéo (durée, dimensions, fps, audio).
    """
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        chemin_video
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)

        duree = float(info.get("format", {}).get("duration", 0))
        largeur, hauteur, fps = 0, 0, 25.0
        has_audio = False

        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video" and largeur == 0:
                largeur = stream.get("width", 0)
                hauteur = stream.get("height", 0)
                fps_str = stream.get("r_frame_rate", "25/1")
                try:
                    num, den = fps_str.split("/")
                    fps = float(num) / float(den)
                except (ValueError, ZeroDivisionError):
                    fps = 25.0
            elif stream.get("codec_type") == "audio":
                has_audio = True

        return {
            "duree": duree,
            "largeur": largeur,
            "hauteur": hauteur,
            "fps": fps,
            "has_audio": has_audio,
        }
    except Exception as e:
        logger.error(f"Erreur ffprobe info : {e}")
        return None


def run_ffmpeg(cmd: list, timeout: int = 600, description: str = "") -> bool:
    """
    Exécute une commande ffmpeg avec logging standardisé.

    Args:
        cmd: Commande complète (incluant 'ffmpeg')
        timeout: Timeout en secondes
        description: Description pour le logging

    Returns:
        True si succès (returncode == 0)
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.error(f"FFmpeg échoué{f' ({description})' if description else ''} : {result.stderr[-300:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"FFmpeg timeout ({timeout}s){f' — {description}' if description else ''}")
        return False
    except Exception as e:
        logger.error(f"FFmpeg erreur{f' ({description})' if description else ''} : {e}")
        return False


def verifier_ffmpeg_installe() -> Tuple[bool, bool, Optional[str]]:
    """
    Vérifie que ffmpeg et ffprobe sont installés.

    Returns:
        (ffmpeg_ok, ffprobe_ok, version_ffmpeg)
    """
    ffmpeg_ok = False
    ffprobe_ok = False
    version = None

    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        ffmpeg_ok = r.returncode == 0
        if ffmpeg_ok:
            premiere_ligne = r.stdout.split("\n")[0]
            version = premiere_ligne.strip()
    except Exception:
        pass

    try:
        r = subprocess.run(["ffprobe", "-version"], capture_output=True, text=True, timeout=10)
        ffprobe_ok = r.returncode == 0
    except Exception:
        pass

    return ffmpeg_ok, ffprobe_ok, version


def classifier_erreur_ffmpeg(stderr: str) -> str:
    """Traduit une erreur ffmpeg en message utilisateur clair (français)."""
    if not stderr:
        return "Erreur inconnue (pas de détail ffmpeg)"
    stderr_lower = stderr.lower()
    if "no such file or directory" in stderr_lower:
        return "Fichier source introuvable. Vérifiez que le fichier n'a pas été déplacé ou supprimé."
    if "invalid data found" in stderr_lower:
        return "Format vidéo corrompu ou incompatible. Essayez de re-télécharger la vidéo."
    if "permission denied" in stderr_lower:
        return "Permission refusée sur le dossier de sortie. Vérifiez les droits d'accès."
    if "no space left on device" in stderr_lower:
        return "Espace disque insuffisant. Libérez de l'espace et relancez le traitement."
    if "out of memory" in stderr_lower or "killed" in stderr_lower:
        return "Mémoire insuffisante. Fermez d'autres applications et relancez."
    if "does not contain" in stderr_lower and "stream" in stderr_lower:
        return "Flux audio ou vidéo manquant dans le fichier source."
    if "codec not currently supported" in stderr_lower or "decoder" in stderr_lower and "not found" in stderr_lower:
        return "Codec vidéo non supporté. Essayez un format différent (MP4/H264)."
    return f"Erreur de traitement vidéo (voir logs pour détails)"
