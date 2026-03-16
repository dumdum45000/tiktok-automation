"""
notifications.py — Notifications natives macOS

Utilise osascript pour envoyer des notifications système macOS
avec son activé. Compatible macOS Intel sans dépendance externe.
"""

import subprocess
import logging

logger = logging.getLogger(__name__)


def envoyer_notification(titre: str, message: str, son: bool = True):
    """
    Envoie une notification macOS native via osascript.

    Args:
        titre: Titre de la notification (affiché en gras)
        message: Corps du message
        son: Active le son de notification
    """
    # Échapper les guillemets pour éviter l'injection de commandes
    titre_safe = titre.replace('"', '\\"').replace("'", "\\'")
    message_safe = message.replace('"', '\\"').replace("'", "\\'")

    script = f'display notification "{message_safe}" with title "{titre_safe}"'
    if son:
        script += ' sound name "default"'

    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            timeout=10
        )
        logger.debug(f"Notification envoyée : {titre}")
    except subprocess.TimeoutExpired:
        logger.warning("Timeout lors de l'envoi de la notification")
    except subprocess.CalledProcessError as e:
        logger.warning(f"Erreur osascript : {e.stderr.decode()}")
    except FileNotFoundError:
        logger.warning("osascript introuvable — notifications désactivées")


def notifier_batch_termine(nb_clips: int):
    """Notification : traitement d'un batch terminé."""
    envoyer_notification(
        titre="✅ Batch terminé",
        message=f"{nb_clips} clip(s) générés, prêts pour validation",
        son=True
    )


def notifier_publication_terminee(nb_succes: int, nb_echecs: int):
    """Notification : session de publication terminée."""
    envoyer_notification(
        titre="📤 Publication terminée",
        message=f"{nb_succes} clip(s) publiés, {nb_echecs} échec(s)",
        son=True
    )


def notifier_espace_disque_faible(espace_go: float):
    """Notification d'alerte espace disque."""
    envoyer_notification(
        titre="⚠️ Espace disque faible",
        message=f"Seulement {espace_go:.1f} Go disponibles — nettoyage recommandé",
        son=True
    )


def notifier_erreur_critique(detail: str):
    """Notification d'erreur critique bloquant le pipeline."""
    envoyer_notification(
        titre="🚨 Erreur critique",
        message=f"Le pipeline s'est arrêté : {detail[:100]}",
        son=True
    )


def notifier_publication_succes(nom_clip: str):
    """Notification : un clip a été publié avec succès."""
    envoyer_notification(
        titre="🎉 Clip publié",
        message=f"{nom_clip} publié sur TikTok avec succès",
        son=False
    )


def notifier_publication_echec(nom_clip: str, tentative: int, max_tentatives: int):
    """Notification : échec de publication d'un clip."""
    if tentative >= max_tentatives:
        envoyer_notification(
            titre="❌ Échec définitif",
            message=f"Impossible de publier {nom_clip} après {max_tentatives} tentatives",
            son=True
        )
    else:
        envoyer_notification(
            titre="⚠️ Nouvelle tentative",
            message=f"Erreur pour {nom_clip}, tentative {tentative}/{max_tentatives}",
            son=False
        )
