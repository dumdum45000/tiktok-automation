"""
description_generator.py — Module 9 : Description et hashtags automatiques

Génère une description TikTok accrocheuse et 10-15 hashtags pertinents
à partir du titre, de la description source et de la catégorie.
Tout local, sans API externe payante.
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Extraction de mots-clés ──────────────────────────────────────────────────

# Mots vides courants à ignorer (anglais + français)
MOTS_VIDES = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "through", "is",
    "are", "was", "were", "be", "been", "have", "has", "do", "does", "did",
    "this", "that", "these", "those", "i", "you", "he", "she", "it", "we",
    "they", "what", "which", "who", "all", "each", "both", "few", "more",
    "most", "other", "some", "such", "no", "not", "only", "same", "so",
    "than", "too", "very", "just", "can", "will", "would", "should", "may",
    "might", "must", "shall", "could", "le", "la", "les", "un", "une", "des",
    "de", "du", "et", "ou", "en", "au", "aux", "sur", "avec", "par", "pour",
    "dans", "qui", "que", "qu", "se", "sa", "son", "ses", "mon", "ma", "mes",
    "ton", "ta", "tes", "lui", "ils", "elles", "nous", "vous", "ce", "cet",
    "cette", "ces", "je", "tu", "il", "elle", "on", "lors", "plus", "très",
    "tout", "tous", "toute", "toutes", "also", "like", "new", "get", "now"
}

def extraire_mots_cles(texte: str, max_mots: int = 10) -> List[str]:
    """
    Extrait les mots-clés significatifs d'un texte.

    Filtre les mots courts, les mots vides, et les mots non-alphabétiques.
    Retourne les mots les plus fréquents.

    Args:
        texte: Texte source (titre + description)
        max_mots: Nombre maximum de mots-clés à retourner

    Returns:
        Liste de mots-clés en minuscules, triés par pertinence
    """
    # Nettoyer le texte
    texte_propre = re.sub(r"[^\w\s]", " ", texte.lower())
    texte_propre = re.sub(r"\s+", " ", texte_propre)

    # Extraire les mots
    mots = texte_propre.split()

    # Filtrer
    mots_filtres = [
        mot for mot in mots
        if (len(mot) > 3
            and mot not in MOTS_VIDES
            and not mot.isdigit()
            and mot.isalpha())
    ]

    # Compter les occurrences
    compteur = {}
    for mot in mots_filtres:
        compteur[mot] = compteur.get(mot, 0) + 1

    # Trier par fréquence décroissante
    mots_tries = sorted(compteur.items(), key=lambda x: x[1], reverse=True)

    return [mot for mot, _ in mots_tries[:max_mots]]


def nettoyer_titre_pour_description(titre: str) -> str:
    """
    Nettoie le titre source pour l'utiliser dans la description.
    Supprime les suffixes courants YouTube : "(Official Video)", "[HD]", etc.
    """
    patterns_a_supprimer = [
        r"\(official\s*(video|audio|mv|music video|lyric video)?\)",
        r"\[official\s*(video|audio|mv)?\]",
        r"\(ft\..*?\)", r"\(feat\..*?\)",
        r"\[hd\]", r"\[4k\]", r"\[full\s*hd\]",
        r"\|\s*\w+\s*tv",
        r"#\w+",  # Hashtags dans le titre
    ]
    titre_propre = titre
    for pattern in patterns_a_supprimer:
        titre_propre = re.sub(pattern, "", titre_propre, flags=re.IGNORECASE)

    # Nettoyer les espaces multiples
    titre_propre = re.sub(r"\s+", " ", titre_propre).strip()
    return titre_propre


def generer_description(
    titre_source: str,
    description_source: str,
    categorie: str,
    nom_chaine: str = "divertissement45000"
) -> str:
    """
    Génère une description courte et accrocheuse pour TikTok (1-2 phrases max).

    Args:
        titre_source: Titre de la vidéo originale
        description_source: Description de la vidéo originale
        categorie: Catégorie du clip
        nom_chaine: Nom de la chaîne

    Returns:
        Description TikTok (1-2 phrases, max ~150 caractères)
    """
    titre_propre = nettoyer_titre_pour_description(titre_source)[:80]

    # Templates par catégorie
    templates = {
        "musique": [
            f"🎵 {titre_propre} 🔥 Impossible de ne pas bouger !",
            f"Ce son va te rester dans la tête 👂🎶 {titre_propre}",
            f"Drop incroyable 🎵 {titre_propre} — à écouter à fond !",
        ],
        "sport": [
            f"🏆 {titre_propre} — Ce moment est INCROYABLE ! 🔥",
            f"Moment épique 💪 {titre_propre} — tu vas pas y croire !",
            f"⚡ Ce clip de sport va te couper le souffle ! {titre_propre}",
        ],
        "humour": [
            f"😂 {titre_propre} — MDR je peux plus !",
            f"Ce moment m'a tué 💀 {titre_propre} 😂",
            f"🤣 REGARDEZ JUSQU'AU BOUT ! {titre_propre}",
        ],
        "autre": [
            f"🔥 {titre_propre} — Tu dois absolument voir ça !",
            f"Incroyable ! {titre_propre} — Like si tu kiffes 🙌",
            f"⚡ {titre_propre} — Partage à tes amis !",
        ]
    }

    import random
    templates_cat = templates.get(categorie, templates["autre"])
    description = random.choice(templates_cat)

    # S'assurer que la description ne dépasse pas 150 caractères
    if len(description) > 150:
        description = description[:147] + "..."

    return description


def generer_hashtags(
    titre_source: str,
    description_source: str,
    categorie: str,
    config: Dict
) -> List[str]:
    """
    Génère 10-15 hashtags pertinents pour TikTok.

    Structure :
    - Hashtags de base (toujours présents) : #divertissement45000 #fyp #foryou #viral
    - Hashtags de catégorie : selon le tag
    - Hashtags contextuels : extraits du titre/description source
    - Hashtags tendance génériques

    Returns:
        Liste de hashtags (avec #) dédoublonnés, 10-15 au total
    """
    cfg_hashtags = config.get("hashtags", {})

    # 1. Hashtags de base (toujours présents)
    hashtags_base = cfg_hashtags.get("base", [
        "#divertissement45000", "#fyp", "#foryou", "#pourtoi", "#viral"
    ])

    # 2. Hashtags de catégorie
    hashtags_categorie = cfg_hashtags.get(categorie, [])

    # 3. Hashtags contextuels (extraits du titre/description)
    texte_combine = f"{titre_source} {description_source}"
    mots_cles = extraire_mots_cles(texte_combine, max_mots=8)
    hashtags_contextuels = [f"#{mot}" for mot in mots_cles if len(mot) > 3][:5]

    # 4. Hashtags tendance génériques
    hashtags_tendance = [
        "#trending", "#explore", "#content", "#fypシ", "#pourtoi"
    ]

    # Assembler en évitant les doublons (insensible à la casse)
    tous_hashtags = []
    vus = set()

    for liste in [hashtags_base, hashtags_categorie, hashtags_contextuels, hashtags_tendance]:
        for tag in liste:
            tag_norm = tag.lower().replace(" ", "")
            if tag_norm not in vus and len(tous_hashtags) < 15:
                tous_hashtags.append(tag)
                vus.add(tag_norm)

    return tous_hashtags


def generer_description_et_hashtags(
    chemin_json_video: str,
    clip_id: str,
    config: Dict,
    numero_partie: int = 1,
    total_parties: int = 1,
) -> Tuple[str, List[str]]:
    """
    Point d'entrée principal : génère description + hashtags pour un clip.

    Charge les métadonnées de la vidéo source depuis le JSON,
    génère description et hashtags, et les sauvegarde dans le JSON du clip.

    Args:
        chemin_json_video: Chemin vers le JSON de la vidéo source
        clip_id: Identifiant du clip
        config: Configuration globale

    Returns:
        (description, liste_hashtags)
    """
    nom_chaine = config.get("identite", {}).get("nom_chaine", "divertissement45000")

    # Charger les métadonnées
    titre_source = "Vidéo incroyable"
    description_source = ""
    categorie = "autre"

    if os.path.exists(chemin_json_video):
        try:
            with open(chemin_json_video, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            titre_source = metadata.get("title", titre_source)
            description_source = metadata.get("description", "")[:500]  # Limiter la taille
            categorie = metadata.get("categorie", "autre")
        except Exception as e:
            logger.warning(f"Impossible de lire {chemin_json_video} : {e}")

    # Générer la description de base
    description = generer_description(titre_source, description_source, categorie, nom_chaine)

    # Ajouter le numéro de partie si plusieurs clips
    if total_parties > 1:
        suffix_partie = f" | Partie {numero_partie}/{total_parties}"
        if numero_partie < total_parties:
            suffix_partie += f" 👉 Suite en partie {numero_partie + 1} !"
        else:
            suffix_partie += " (Fin) 🔁"
        # Tronquer la description de base si nécessaire pour laisser de la place
        max_base = 150 - len(suffix_partie)
        if len(description) > max_base:
            description = description[:max_base - 3] + "..."
        description = description + suffix_partie

    # Ajouter hashtag de partie
    hashtags = generer_hashtags(titre_source, description_source, categorie, config)
    if total_parties > 1:
        hashtags_partie = [f"#partie{numero_partie}", "#serie", "#suite"]
        for tag in hashtags_partie:
            if tag not in hashtags and len(hashtags) < 15:
                hashtags.append(tag)

    logger.info(
        f"Description générée pour {clip_id} : '{description[:60]}...' "
        f"({len(hashtags)} hashtags)"
    )

    return description, hashtags


def formater_description_complete(description: str, hashtags: List[str]) -> str:
    """
    Formate la description complète TikTok : texte + hashtags.

    Returns:
        Texte prêt à copier dans TikTok
    """
    hashtags_str = " ".join(hashtags)
    return f"{description}\n\n{hashtags_str}"
