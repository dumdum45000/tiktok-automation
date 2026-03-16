"""
download_music.py — Script utilitaire de téléchargement de musiques libres de droits

Télécharge des musiques depuis des sources légales et libres de droits
et les organise dans la bibliothèque par catégorie.

Sources utilisées :
- yt-dlp avec des playlists YouTube de musiques libres (Creative Commons / No Copyright)
- Les musiques téléchargées sont 100% libres pour TikTok (vérifiez toujours la licence)

Usage :
    python download_music.py
    python download_music.py --categorie sport
    python download_music.py --max 5
"""

import argparse
import json
import logging
import os
import subprocess
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── Sources de musiques libres de droits ────────────────────────────────────
# Ces URLs pointent vers des playlists YouTube de musiques libres de droits (No Copyright)
# Vérifiez toujours les licences avant d'utiliser en production

SOURCES_MUSIQUES = {
    "sport": [
        # NCS (NoCopyrightSounds) — musiques énergie / électro libres pour YouTube
        "https://www.youtube.com/playlist?list=PLRBp0Fe2GpgmsW46rSmjE-UYQ3cbRqr_R",
        # Epidemic Sound alternatives libres
        "https://www.youtube.com/playlist?list=PLRBp0Fe2Gpgk7_PQ7PLFgkw1BNQ2z7Vn5",
    ],
    "humour": [
        # Musiques légères / comiques libres de droits
        "https://www.youtube.com/playlist?list=PLRBp0Fe2Gpgk7gCZaqh_-9Y7_L1Y5_PJM",
    ],
    "autre": [
        # Musiques ambiantes / neutres
        "https://www.youtube.com/playlist?list=PLRBp0Fe2Gpgnv-RKmtj2hI4d3SaAyO3LR",
    ]
}

# Sources NoCopyrightSounds (NCS) — libres de droits pour YouTube/TikTok avec attribution
# Voir : https://ncs.io/usage-policy
MUSIQUES_INDIVIDUELLES = {
    "sport": [
        "https://www.youtube.com/watch?v=vBGiFtb8Rpw",  # Elektronomia - Energy [NCS]
        "https://www.youtube.com/watch?v=J2X5mJ3HDYE",  # DEAF KEV - Invincible [NCS]
        "https://www.youtube.com/watch?v=TW9d8vYrVFQ",  # Elektronomia - Sky High [NCS]
    ],
    "humour": [
        "https://www.youtube.com/watch?v=K4DyBUG242c",  # Cartoon - On & On [NCS]
        "https://www.youtube.com/watch?v=zyXmsVwZqX4",  # Jim Yosef - Canary [NCS]
        "https://www.youtube.com/watch?v=4lXBHD5C8do",  # Tobu & Itro - Sunburst [NCS]
    ],
    "autre": [
        "https://www.youtube.com/watch?v=TW9d8vYrVFQ",  # Elektronomia - Sky High [NCS]
        "https://www.youtube.com/watch?v=n1ddqXIbpa8",  # JJD - Future [NCS]
        "https://www.youtube.com/watch?v=DwKjHfJVMtw",  # Elektronomia - Summersong [NCS]
    ]
}


def telecharger_musique(url: str, dossier_sortie: str, nom_fichier: str = None) -> bool:
    """
    Télécharge une musique depuis YouTube en MP3 via yt-dlp.
    Ne télécharge que l'audio (pas la vidéo) pour économiser l'espace.

    Args:
        url: URL YouTube de la musique
        dossier_sortie: Dossier où sauvegarder le fichier
        nom_fichier: Nom du fichier (sans extension), None = auto

    Returns:
        True si succès
    """
    os.makedirs(dossier_sortie, exist_ok=True)

    if nom_fichier:
        sortie_template = os.path.join(dossier_sortie, f"{nom_fichier}.%(ext)s")
    else:
        sortie_template = os.path.join(dossier_sortie, "%(title)s.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--format", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "192k",
        "--output", sortie_template,
        "--no-warnings",
        "--retries", "3",
        "--max-filesize", "20m",  # Max 20 Mo par musique
        "--match-filter", "duration < 300",  # Max 5 minutes
        "--no-playlist",  # Pas de playlist par défaut (une musique à la fois)
        url
    ]

    try:
        logger.info(f"Téléchargement : {url}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            logger.info(f"✅ Musique téléchargée dans : {dossier_sortie}")
            return True
        else:
            logger.warning(f"⚠️ Échec téléchargement {url} : {result.stderr[-200:]}")
            return False
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout téléchargement : {url}")
        return False
    except Exception as e:
        logger.error(f"Erreur : {e}")
        return False


def telecharger_playlist(url: str, dossier_sortie: str, max_items: int = 10) -> int:
    """
    Télécharge une playlist entière de musiques.

    Args:
        url: URL de la playlist YouTube
        dossier_sortie: Dossier de destination
        max_items: Nombre maximum de musiques à télécharger

    Returns:
        Nombre de musiques téléchargées
    """
    os.makedirs(dossier_sortie, exist_ok=True)

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--format", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "192k",
        "--output", os.path.join(dossier_sortie, "%(title)s.%(ext)s"),
        "--no-warnings",
        "--retries", "2",
        "--max-filesize", "20m",
        "--match-filter", "duration < 300",
        "--playlist-end", str(max_items),
        "--yes-playlist",
        url
    ]

    try:
        logger.info(f"Téléchargement playlist : {url} (max {max_items} musiques)")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        # Compter les fichiers téléchargés
        nb = len([f for f in os.listdir(dossier_sortie) if f.endswith(".mp3")])
        logger.info(f"Playlist terminée : {nb} musiques dans {dossier_sortie}")
        return nb

    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout playlist : {url}")
        return 0
    except Exception as e:
        logger.error(f"Erreur playlist : {e}")
        return 0


def verifier_ytdlp_disponible() -> bool:
    """Vérifie que yt-dlp est installé."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def afficher_progression(categorie: str, nb_actuels: int, nb_cibles: int):
    """Affiche la progression du téléchargement."""
    print(f"\n{'─' * 50}")
    print(f"Catégorie : {categorie.upper()}")
    print(f"Musiques actuelles : {nb_actuels}")
    print(f"Cible : {nb_cibles}")
    print(f"{'─' * 50}")


def main():
    """Point d'entrée du script de téléchargement de musiques."""
    parser = argparse.ArgumentParser(
        description="Télécharge des musiques libres de droits pour la bibliothèque TikTok"
    )
    parser.add_argument(
        "--categorie",
        choices=["sport", "humour", "autre", "all"],
        default="all",
        help="Catégorie à télécharger (défaut : toutes)"
    )
    parser.add_argument(
        "--max",
        type=int,
        default=5,
        help="Nombre max de musiques à télécharger par catégorie (défaut : 5)"
    )
    parser.add_argument(
        "--dossier",
        default="music_library",
        help="Dossier de la bibliothèque musicale (défaut : music_library/)"
    )
    args = parser.parse_args()

    print("🎵 Script de téléchargement de musiques libres de droits")
    print("=" * 60)
    print("Sources : YouTube No Copyright Music")
    print("Format  : MP3 192kbps, max 5 minutes, max 20 Mo")
    print("=" * 60)

    # Vérifier yt-dlp
    if not verifier_ytdlp_disponible():
        print("❌ yt-dlp non disponible. Installez-le avec : pip install yt-dlp")
        sys.exit(1)

    categories = ["sport", "humour", "autre"] if args.categorie == "all" else [args.categorie]
    total_telecharge = 0

    for categorie in categories:
        dossier_cat = os.path.join(args.dossier, categorie)
        os.makedirs(dossier_cat, exist_ok=True)

        # Compter les fichiers existants
        existants = [f for f in os.listdir(dossier_cat) if f.endswith(".mp3")]
        nb_existants = len(existants)
        afficher_progression(categorie, nb_existants, args.max)

        if nb_existants >= args.max:
            print(f"✅ {categorie} : déjà {nb_existants} musiques — aucun téléchargement nécessaire")
            continue

        manquants = args.max - nb_existants
        print(f"Téléchargement de {manquants} musique(s)...")

        # Télécharger les musiques individuelles
        urls_individuelles = MUSIQUES_INDIVIDUELLES.get(categorie, [])
        nb_telecharge = 0

        for url in urls_individuelles:
            if nb_telecharge >= manquants:
                break

            succes = telecharger_musique(url, dossier_cat)
            if succes:
                nb_telecharge += 1
                total_telecharge += 1
            print(f"  {'✅' if succes else '❌'} {url[:60]}")

        if nb_telecharge < manquants:
            print(f"⚠️ {manquants - nb_telecharge} musique(s) manquante(s) pour {categorie}")
            print(f"   Ajoutez manuellement des MP3 dans {dossier_cat}/")

    print("\n" + "=" * 60)
    print(f"✅ Téléchargement terminé : {total_telecharge} musique(s) ajoutée(s)")
    print("\nPour ajouter manuellement des musiques :")
    print("  1. Téléchargez des MP3 libres de droits depuis :")
    print("     - https://pixabay.com/music/")
    print("     - https://www.bensound.com/")
    print("     - https://freemusicarchive.org/")
    print("     - https://incompetech.com/")
    print("  2. Copiez les fichiers dans music_library/{categorie}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
