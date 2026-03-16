"""
downloader.py — Module 1 : Téléchargement des vidéos sources

Télécharge des vidéos depuis YouTube, TikTok et Instagram Reels
via yt-dlp. Gère les erreurs (liens morts, vidéos privées, géo-restrictions)
avec messages clairs. Extrait et sauvegarde les métadonnées en JSON.
"""

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def nettoyer_titre(titre: str, max_longueur: int = 50) -> str:
    """
    Transforme un titre vidéo en nom de fichier sûr et lisible.

    Supprime les caractères spéciaux, remplace les espaces par des underscores,
    limite la longueur pour éviter les noms de fichiers trop longs.
    """
    # Supprimer les caractères non-alphanumériques sauf tiret/underscore
    titre_propre = re.sub(r"[^\w\s-]", "", titre, flags=re.UNICODE)
    # Remplacer les espaces par underscore
    titre_propre = re.sub(r"\s+", "_", titre_propre.strip())
    # Limiter la longueur
    return titre_propre[:max_longueur]


def detecter_plateforme(url: str) -> str:
    """Détecte la plateforme source à partir de l'URL."""
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    elif "tiktok.com" in url_lower:
        return "tiktok"
    elif "instagram.com" in url_lower:
        return "instagram"
    else:
        return "autre"


def construire_nom_fichier(metadata: Dict, dossier_sortie: str) -> str:
    """
    Construit le chemin de fichier final selon la convention :
    {date}_{source}_{titre_nettoyé}.mp4
    """
    date_str = datetime.now().strftime("%Y%m%d")
    source = metadata.get("platform", "source")
    titre = nettoyer_titre(metadata.get("title", "video_sans_titre"))
    nom = f"{date_str}_{source}_{titre}.mp4"
    return os.path.join(dossier_sortie, nom)


def extraire_metadata_yt_dlp(url: str) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Récupère les métadonnées d'une vidéo sans la télécharger.

    Returns:
        (metadata_dict, erreur_message) — l'un des deux est None.
    """
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--dump-json",
        "--no-download",
        "--no-warnings",
        url
    ]
    try:
        resultat = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        if resultat.returncode != 0:
            erreur = resultat.stderr.strip()
            # Traduire les erreurs courantes en messages utilisateur
            if "Private video" in erreur or "private" in erreur.lower():
                return None, "Cette vidéo est privée et ne peut pas être téléchargée."
            elif "is not available" in erreur or "removed" in erreur.lower():
                return None, "Cette vidéo n'est plus disponible (supprimée ou lien mort)."
            elif "geo" in erreur.lower() or "not available in your country" in erreur.lower():
                return None, "Cette vidéo est géo-restreinte et non disponible dans votre région."
            elif "sign in" in erreur.lower() or "login" in erreur.lower():
                return None, "Cette vidéo nécessite une connexion. Essayez un lien public."
            else:
                return None, f"Impossible d'accéder à la vidéo : {erreur[:200]}"

        info = json.loads(resultat.stdout.split("\n")[0])
        return info, None

    except subprocess.TimeoutExpired:
        return None, "Délai d'attente dépassé lors de la récupération des informations."
    except json.JSONDecodeError as e:
        return None, f"Impossible de lire les métadonnées : {e}"
    except Exception as e:
        return None, f"Erreur inattendue : {e}"


def telecharger_video(
    url: str,
    dossier_sortie: str,
    qualite: str = "bestvideo[height>=1080]+bestaudio/best[height>=1080]/bestvideo+bestaudio/best",
    callback_progression: Optional[Callable[[str], None]] = None
) -> Tuple[Optional[str], Optional[Dict], Optional[str]]:
    """
    Télécharge une vidéo via yt-dlp.

    Args:
        url: URL de la vidéo
        dossier_sortie: Dossier de destination
        qualite: Sélection de format yt-dlp
        callback_progression: Fonction appelée avec les messages de progression

    Returns:
        (chemin_fichier, metadata, erreur) — erreur=None si succès
    """
    os.makedirs(dossier_sortie, exist_ok=True)

    # Étape 1 : récupérer les métadonnées
    if callback_progression:
        callback_progression("Récupération des informations de la vidéo...")

    info, erreur = extraire_metadata_yt_dlp(url)
    if erreur:
        return None, None, erreur

    # Construire les métadonnées épurées
    plateforme = detecter_plateforme(url)
    metadata = {
        "url": url,
        "platform": plateforme,
        "title": info.get("title", "Sans titre"),
        "description": info.get("description", ""),
        "duration": info.get("duration", 0),
        "uploader": info.get("uploader", ""),
        "upload_date": info.get("upload_date", ""),
        "view_count": info.get("view_count", 0),
        "like_count": info.get("like_count", 0),
        "tags": info.get("tags", []),
        "categories": info.get("categories", []),
        "timestamp_import": datetime.now().isoformat()
    }

    # Construire le nom de fichier final
    chemin_video = construire_nom_fichier(metadata, dossier_sortie)

    # Vérifier si le fichier existe déjà
    if os.path.exists(chemin_video):
        if callback_progression:
            callback_progression(f"Vidéo déjà téléchargée : {os.path.basename(chemin_video)}")
        return chemin_video, metadata, None

    # Étape 2 : télécharger la vidéo
    if callback_progression:
        callback_progression(f"Téléchargement de '{metadata['title']}'...")

    chemin_sortie_template = os.path.splitext(chemin_video)[0] + ".%(ext)s"

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--format", qualite,
        "--merge-output-format", "mp4",
        "--output", chemin_sortie_template,
        "--print", "after_move:filepath",  # Affiche le chemin réel après téléchargement
        "--no-warnings",
        "--retries", "3",
        "--fragment-retries", "3",
        "--socket-timeout", "30",
        url
    ]

    chemin_final = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        for ligne in proc.stdout:
            ligne = ligne.strip()
            if not ligne:
                continue
            # --print after_move:filepath affiche le chemin absolu du fichier
            if os.path.exists(ligne) and any(ligne.endswith(ext) for ext in (".mp4", ".mkv", ".webm", ".mov")):
                chemin_final = ligne
            elif callback_progression:
                if "[download]" in ligne and "%" in ligne:
                    callback_progression(ligne)
                elif "[Merger]" in ligne or "[ffmpeg]" in ligne:
                    callback_progression("Assemblage de la vidéo...")

        proc.wait()

        # Fallback : chercher dans le dossier si --print n'a pas retourné le chemin
        if not chemin_final or not os.path.exists(chemin_final):
            dossier_check = os.path.dirname(chemin_video)
            base_check = os.path.splitext(os.path.basename(chemin_video))[0]
            extensions = (".mp4", ".mkv", ".webm", ".mov")
            for f in os.listdir(dossier_check):
                if f.startswith(base_check) and any(f.endswith(ext) for ext in extensions):
                    chemin_final = os.path.join(dossier_check, f)
                    break

        if not chemin_final or not os.path.exists(chemin_final):
            if proc.returncode != 0:
                return None, None, "Le téléchargement a échoué. Vérifiez le lien et réessayez."
            return None, None, "Le fichier vidéo n'a pas été trouvé après téléchargement."

        # Convertir en mp4 si nécessaire
        if not chemin_final.endswith(".mp4"):
            chemin_mp4 = os.path.splitext(chemin_final)[0] + ".mp4"
            if callback_progression:
                callback_progression("Conversion en MP4...")
            r = subprocess.run(
                ["ffmpeg", "-i", chemin_final, "-c", "copy", chemin_mp4, "-y"],
                capture_output=True, timeout=120
            )
            if r.returncode == 0 and os.path.exists(chemin_mp4):
                os.remove(chemin_final)
                chemin_final = chemin_mp4
            # Sinon on garde le fichier original

    except subprocess.TimeoutExpired:
        proc.kill()
        return None, None, "Délai de téléchargement dépassé."
    except Exception as e:
        return None, None, f"Erreur lors du téléchargement : {e}"

    # Sauvegarder les métadonnées dans un fichier JSON associé
    chemin_json = os.path.splitext(chemin_final)[0] + ".json"
    with open(chemin_json, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    taille_mo = os.path.getsize(chemin_final) / (1024 ** 2)
    if callback_progression:
        callback_progression(f"✅ Téléchargé : {os.path.basename(chemin_final)} ({taille_mo:.1f} Mo)")

    logger.info(f"Vidéo téléchargée : {chemin_final}")
    return chemin_final, metadata, None


def telecharger_batch(
    urls: List[str],
    dossier_sortie: str,
    config: Dict,
    callback_progression: Optional[Callable[[str, str, float], None]] = None
) -> List[Dict]:
    """
    Télécharge une liste d'URLs en séquence avec gestion des erreurs.

    Args:
        urls: Liste des URLs à télécharger
        dossier_sortie: Dossier de destination
        config: Configuration (pour qualité etc.)
        callback_progression: Appelé avec (url, message, progression_0_1)

    Returns:
        Liste de résultats par URL avec statut, chemin, metadata, erreur
    """
    qualite = config.get("telechargement", {}).get(
        "qualite_preferee",
        "bestvideo[height>=1080]+bestaudio/best[height>=1080]/bestvideo+bestaudio/best"
    )

    resultats = []
    nb_total = len(urls)

    for i, url in enumerate(urls):
        url = url.strip()
        if not url:
            continue

        progression_globale = i / nb_total

        def cb(msg, url=url, i=i):
            if callback_progression:
                callback_progression(url, msg, i / nb_total)

        try:
            chemin, metadata, erreur = telecharger_video(
                url=url,
                dossier_sortie=dossier_sortie,
                qualite=qualite,
                callback_progression=cb
            )

            resultats.append({
                "url": url,
                "succes": erreur is None,
                "chemin": chemin,
                "metadata": metadata,
                "erreur": erreur
            })

        except Exception as e:
            erreur_msg = f"Erreur inattendue : {e}"
            logger.exception(f"Erreur téléchargement {url}")
            resultats.append({
                "url": url,
                "succes": False,
                "chemin": None,
                "metadata": None,
                "erreur": erreur_msg
            })

        if callback_progression:
            callback_progression(url, "", (i + 1) / nb_total)

    nb_succes = sum(1 for r in resultats if r["succes"])
    logger.info(f"Batch terminé : {nb_succes}/{nb_total} vidéos téléchargées avec succès")
    return resultats
