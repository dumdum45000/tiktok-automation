"""
disk_monitor.py — Surveillance de l'espace disque

Surveille l'espace disque disponible et gère le nettoyage automatique
des fichiers anciens pour éviter le remplissage du disque.
"""

import os
import shutil
import logging
from datetime import datetime, timedelta
from typing import Tuple

from modules.notifications import notifier_espace_disque_faible

logger = logging.getLogger(__name__)


def get_espace_disque() -> Tuple[float, float, float]:
    """
    Retourne l'espace disque total, utilisé et libre en Go.

    Returns:
        (total_go, utilise_go, libre_go)
    """
    try:
        # Surveiller le disque où l'app est installée (ex: /Volumes/disque)
        chemin_app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        usage = shutil.disk_usage(chemin_app)
        total_go = usage.total / (1024 ** 3)
        utilise_go = usage.used / (1024 ** 3)
        libre_go = usage.free / (1024 ** 3)
        return total_go, utilise_go, libre_go
    except Exception as e:
        logger.error(f"Impossible de lire l'espace disque : {e}")
        return 0.0, 0.0, 0.0


def get_taille_dossier_go(chemin: str) -> float:
    """Calcule la taille totale d'un dossier en Go."""
    if not os.path.exists(chemin):
        return 0.0
    total = 0
    for dirpath, dirnames, filenames in os.walk(chemin):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total / (1024 ** 3)


def verifier_espace_disque(seuil_go: float = 50.0, notifier: bool = True) -> bool:
    """
    Vérifie si l'espace libre est suffisant.

    Args:
        seuil_go: Seuil d'alerte en Go
        notifier: Envoyer une notification macOS si alerte

    Returns:
        True si espace suffisant, False si alerte
    """
    _, _, libre_go = get_espace_disque()
    if libre_go < seuil_go:
        logger.warning(f"Espace disque faible : {libre_go:.1f} Go libre")
        if notifier:
            notifier_espace_disque_faible(libre_go)
        return False
    return True


def nettoyer_anciens_fichiers(
    dossiers: list,
    jours: int = 7,
    extensions: list = None,
    simulation: bool = False
) -> dict:
    """
    Supprime les fichiers plus anciens que N jours dans les dossiers spécifiés.

    Args:
        dossiers: Liste des chemins de dossiers à nettoyer
        jours: Âge maximum des fichiers à conserver
        extensions: Extensions à nettoyer (ex: ['.mp4', '.json']). None = tout.
        simulation: Si True, liste sans supprimer

    Returns:
        Dictionnaire avec nombre de fichiers supprimés et espace libéré
    """
    if extensions is None:
        extensions = [".mp4", ".json", ".srt", ".ass", ".wav"]

    seuil = datetime.now() - timedelta(days=jours)
    resultat = {"fichiers_supprimes": 0, "espace_libere_go": 0.0, "erreurs": []}

    for dossier in dossiers:
        if not os.path.exists(dossier):
            continue

        for dirpath, dirnames, filenames in os.walk(dossier):
            for fichier in filenames:
                ext = os.path.splitext(fichier)[1].lower()
                if ext not in extensions:
                    continue

                chemin = os.path.join(dirpath, fichier)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(chemin))
                    if mtime < seuil:
                        taille = os.path.getsize(chemin) / (1024 ** 3)
                        if not simulation:
                            os.remove(chemin)
                            logger.info(f"Supprimé (ancien) : {chemin}")
                        else:
                            logger.info(f"[Simulation] À supprimer : {chemin}")
                        resultat["fichiers_supprimes"] += 1
                        resultat["espace_libere_go"] += taille
                except OSError as e:
                    msg = f"Impossible de supprimer {chemin} : {e}"
                    logger.warning(msg)
                    resultat["erreurs"].append(msg)

    logger.info(
        f"Nettoyage {'[simulation] ' if simulation else ''}terminé : "
        f"{resultat['fichiers_supprimes']} fichier(s), "
        f"{resultat['espace_libere_go']:.2f} Go libérés"
    )
    return resultat


def nettoyer_dossiers_vides(dossier_racine: str):
    """Supprime les sous-dossiers vides dans un dossier racine."""
    for dirpath, dirnames, filenames in os.walk(dossier_racine, topdown=False):
        if dirpath == dossier_racine:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                logger.debug(f"Dossier vide supprimé : {dirpath}")
        except OSError:
            pass
