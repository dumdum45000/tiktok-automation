"""
tagger.py — Module 2 : Tags et catégorisation

Analyse automatique du titre et de la description d'une vidéo pour
suggérer une catégorie (Musique, Sport, Humour, Autre).
La catégorie influence l'analyse, la musique de fond et les hashtags.
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CATEGORIES_VALIDES = ["musique", "sport", "humour", "autre"]


def normaliser_texte(texte: str) -> str:
    """Met le texte en minuscules et supprime la ponctuation."""
    return re.sub(r"[^\w\s]", " ", texte.lower())


def scorer_categorie(texte: str, mots_cles: List[str]) -> int:
    """Compte le nombre de mots-clés présents dans le texte."""
    texte_norm = normaliser_texte(texte)
    mots_texte = set(texte_norm.split())
    score = 0
    for mot_cle in mots_cles:
        mot_norm = normaliser_texte(mot_cle)
        # Match exact ou présence dans le texte
        if mot_norm in texte_norm:
            score += 2  # Bonus pour match de phrase
        for mot in mot_norm.split():
            if mot in mots_texte and len(mot) > 3:
                score += 1
    return score


def auto_tag(
    titre: str,
    description: str,
    config: Dict
) -> Tuple[str, Dict[str, int]]:
    """
    Analyse le titre et la description pour suggérer une catégorie.

    Args:
        titre: Titre de la vidéo source
        description: Description de la vidéo source
        config: Configuration avec les listes de mots-clés par catégorie

    Returns:
        (categorie_suggeree, scores_par_categorie)
        categorie_suggeree est "autre" si aucune catégorie n'est clairement identifiée
    """
    auto_tag_config = config.get("auto_tag", {})
    texte_complet = f"{titre} {description}"

    scores = {}
    for categorie in ["musique", "sport", "humour"]:
        cle_config = f"mots_cles_{categorie}"
        mots_cles = auto_tag_config.get(cle_config, [])
        scores[categorie] = scorer_categorie(texte_complet, mots_cles)

    scores["autre"] = 0  # Catégorie par défaut, score toujours 0

    # La catégorie avec le score le plus élevé gagne
    # En cas d'égalité à 0, on retourne "autre"
    meilleure = max(scores, key=lambda k: scores[k])
    if scores[meilleure] == 0:
        meilleure = "autre"

    logger.info(f"Auto-tag : '{titre[:40]}' → {meilleure} (scores: {scores})")
    return meilleure, scores


def charger_metadata(chemin_json: str) -> Optional[Dict]:
    """Charge le fichier JSON de métadonnées d'une vidéo."""
    try:
        with open(chemin_json, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"Impossible de charger {chemin_json} : {e}")
        return None


def sauvegarder_metadata(chemin_json: str, metadata: Dict) -> bool:
    """Sauvegarde les métadonnées mises à jour dans le fichier JSON."""
    try:
        with open(chemin_json, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Impossible de sauvegarder {chemin_json} : {e}")
        return False


def appliquer_categorie(chemin_json: str, categorie: str) -> bool:
    """
    Applique une catégorie à une vidéo en mettant à jour son fichier JSON.

    Args:
        chemin_json: Chemin vers le fichier JSON de métadonnées
        categorie: Catégorie à appliquer (musique/sport/humour/autre)

    Returns:
        True si succès, False sinon
    """
    if categorie not in CATEGORIES_VALIDES:
        logger.error(f"Catégorie invalide : {categorie}")
        return False

    metadata = charger_metadata(chemin_json)
    if metadata is None:
        return False

    metadata["categorie"] = categorie
    metadata["timestamp_tag"] = __import__("datetime").datetime.now().isoformat()
    return sauvegarder_metadata(chemin_json, metadata)


def get_categorie(chemin_json: str) -> Optional[str]:
    """Retourne la catégorie d'une vidéo depuis son fichier JSON."""
    metadata = charger_metadata(chemin_json)
    if metadata is None:
        return None
    return metadata.get("categorie", "autre")


def formater_label_categorie(categorie: str) -> str:
    """Retourne un label lisible avec emoji pour l'interface."""
    labels = {
        "musique": "🎵 Musique",
        "sport": "⚽ Sport",
        "humour": "😂 Humour",
        "autre": "🎬 Autre"
    }
    return labels.get(categorie, "🎬 Autre")
