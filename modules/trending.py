"""
trending.py — Récupération des hashtags TikTok en tendance

Scrape la page Discover/Explore de TikTok pour extraire les hashtags
populaires du moment. Utilise un cache de 6h pour éviter les requêtes
excessives.
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

# Hashtags de fallback si le scraping échoue
FALLBACK_TRENDING = [
    "#trending", "#explore", "#content", "#fypシ", "#pourtoi",
    "#viral", "#trend", "#xyzbca", "#fypage", "#tendance"
]

CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "trending_cache.json"
)


def _cache_valide(cache_path: str, duree_heures: float = 6.0) -> bool:
    """Vérifie si le cache existe et n'a pas expiré."""
    if not os.path.exists(cache_path):
        return False
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        ts = cache.get("timestamp", 0)
        age_heures = (time.time() - ts) / 3600
        return age_heures < duree_heures
    except (json.JSONDecodeError, OSError):
        return False


def charger_cache(cache_path: str) -> List[str]:
    """Charge les hashtags depuis le cache."""
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache.get("hashtags", [])
    except (json.JSONDecodeError, OSError):
        return []


def sauvegarder_cache(cache_path: str, hashtags: List[str]):
    """Sauvegarde les hashtags dans le cache."""
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "date": datetime.now().isoformat(),
                "hashtags": hashtags
            }, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"Impossible de sauvegarder le cache trending : {e}")


def _scraper_tiktok_trending() -> List[str]:
    """
    Tente de récupérer les hashtags trending depuis TikTok.
    Essaie plusieurs approches : page explore, API discover.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    hashtags = set()

    # Approche 1 : Page explore/discover
    urls_to_try = [
        "https://www.tiktok.com/explore",
        "https://www.tiktok.com/discover",
    ]

    for url in urls_to_try:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue

            # Chercher les hashtags dans le HTML/JSON embarqué
            # TikTok embarque souvent les données dans un script JSON
            patterns = [
                r'"hashtagName"\s*:\s*"([^"]+)"',
                r'"title"\s*:\s*"#([^"]+)"',
                r'href="/tag/([^"?]+)"',
                r'"challengeName"\s*:\s*"([^"]+)"',
            ]

            for pattern in patterns:
                matches = re.findall(pattern, resp.text)
                for m in matches:
                    tag = m.strip().lower()
                    if tag and len(tag) > 1 and len(tag) < 50:
                        hashtags.add(f"#{tag}")

            if len(hashtags) >= 5:
                break

        except (requests.RequestException, Exception) as e:
            logger.debug(f"Erreur scraping {url} : {e}")
            continue

    result = list(hashtags)[:20]
    if result:
        logger.info(f"Trending : {len(result)} hashtags récupérés depuis TikTok")
    return result


def recuperer_trending_hashtags(config: Dict) -> List[str]:
    """
    Récupère les hashtags trending avec cache.

    Args:
        config: Configuration de l'application

    Returns:
        Liste de hashtags trending (avec #)
    """
    cfg_trending = config.get("trending", {})
    if not cfg_trending.get("actif", True):
        return FALLBACK_TRENDING[:5]

    duree_cache = cfg_trending.get("cache_duree_heures", 6.0)
    max_hashtags = cfg_trending.get("max_trending_hashtags", 5)
    cache_path = cfg_trending.get("cache_path", CACHE_FILE)

    # Vérifier le cache
    if _cache_valide(cache_path, duree_cache):
        cached = charger_cache(cache_path)
        if cached:
            return cached[:max_hashtags]

    # Tenter le scraping
    trending = _scraper_tiktok_trending()

    if trending:
        sauvegarder_cache(cache_path, trending)
        return trending[:max_hashtags]

    # Fallback
    logger.info("Trending : utilisation des hashtags par défaut (scraping échoué)")
    return FALLBACK_TRENDING[:max_hashtags]


def forcer_rafraichissement(config: Dict) -> List[str]:
    """Force un rafraîchissement du cache trending."""
    cfg_trending = config.get("trending", {})
    cache_path = cfg_trending.get("cache_path", CACHE_FILE)

    # Supprimer le cache existant
    if os.path.exists(cache_path):
        os.remove(cache_path)

    return recuperer_trending_hashtags(config)


def get_info_cache(config: Dict) -> Dict:
    """Retourne les infos sur le cache pour l'affichage UI."""
    cfg_trending = config.get("trending", {})
    cache_path = cfg_trending.get("cache_path", CACHE_FILE)

    if not os.path.exists(cache_path):
        return {"existe": False, "hashtags": [], "date": None}

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return {
            "existe": True,
            "hashtags": cache.get("hashtags", []),
            "date": cache.get("date"),
            "age_heures": (time.time() - cache.get("timestamp", 0)) / 3600,
        }
    except (json.JSONDecodeError, OSError):
        return {"existe": False, "hashtags": [], "date": None}
