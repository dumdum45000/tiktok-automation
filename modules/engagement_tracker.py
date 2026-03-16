"""
engagement_tracker.py — Boucle de feedback engagement TikTok

Après chaque publication, collecte les métriques d'engagement
(vues, likes, partages, commentaires) via l'API TikTok v2 à
intervalles réguliers (24h, 48h, 7j).

Permet d'analyser les performances par catégorie, heure de
publication, et type de contenu.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Checkpoints de collecte après publication
CHECKPOINTS_HEURES = [24, 48, 168]  # 24h, 48h, 7 jours


class EngagementTracker:
    """
    Collecte les métriques d'engagement pour les clips publiés
    et fournit des agrégations analytiques.
    """

    def __init__(self, config: Dict, state_manager):
        self.config = config
        self.state = state_manager
        api_cfg = config.get("publication", {}).get("tiktok_api", {})
        self.access_token = api_cfg.get("access_token", "")
        self.client_key = api_cfg.get("client_key", "")

    def _api_disponible(self) -> bool:
        """Vérifie si l'API TikTok est configurée."""
        return bool(self.access_token and self.client_key)

    def collecter_engagement_clip(self, publish_id: str) -> Optional[Dict]:
        """
        Récupère les métriques d'engagement d'un clip via l'API TikTok v2.

        Args:
            publish_id: L'ID TikTok de la vidéo publiée

        Returns:
            Dict avec vues, likes, partages, commentaires ou None si erreur
        """
        if not self._api_disponible():
            logger.debug("Engagement : API non configurée")
            return None

        try:
            url = "https://open.tiktokapis.com/v2/video/query/"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
            payload = {
                "filters": {
                    "video_ids": [publish_id]
                },
                "fields": [
                    "like_count", "comment_count", "share_count", "view_count"
                ]
            }

            resp = requests.post(url, json=payload, headers=headers, timeout=15)

            if resp.status_code == 401:
                logger.warning("Engagement : token expiré — rafraîchissement nécessaire")
                return None

            if resp.status_code != 200:
                logger.warning(f"Engagement API erreur {resp.status_code}: {resp.text[:200]}")
                return None

            data = resp.json()
            videos = data.get("data", {}).get("videos", [])
            if not videos:
                logger.debug(f"Engagement : aucune donnée pour {publish_id}")
                return None

            video = videos[0]
            return {
                "vues": video.get("view_count", 0),
                "likes": video.get("like_count", 0),
                "partages": video.get("share_count", 0),
                "commentaires": video.get("comment_count", 0),
            }

        except requests.RequestException as e:
            logger.warning(f"Engagement : erreur réseau — {e}")
            return None

    def planifier_collecte(self, clip_id: str, publish_id: str):
        """
        Programme les checkpoints de collecte d'engagement pour un clip.

        Appelée juste après une publication réussie.
        """
        maintenant = datetime.now()
        prochaine = maintenant + timedelta(hours=CHECKPOINTS_HEURES[0])

        # Stocker dans l'entrée de publication
        for entree in self.state.get_file_publication():
            if entree["clip_id"] == clip_id:
                entree["engagement"] = {
                    "publish_id": publish_id,
                    "collectes": [],
                    "prochaine_collecte": prochaine.isoformat(),
                    "checkpoint_index": 0,
                }
                self.state.sauvegarder()
                logger.info(f"Engagement : collecte planifiée pour {clip_id} dans {CHECKPOINTS_HEURES[0]}h")
                return

    def executer_collectes_en_attente(self):
        """
        Vérifie et exécute les collectes d'engagement dues.
        Appelée à chaque itération de la boucle de publication.
        """
        if not self._api_disponible():
            return

        maintenant = datetime.now()
        nb_collectes = 0

        for entree in self.state.get_file_publication():
            engagement = entree.get("engagement")
            if not engagement:
                continue

            prochaine_str = engagement.get("prochaine_collecte")
            if not prochaine_str:
                continue

            try:
                prochaine = datetime.fromisoformat(prochaine_str)
            except (ValueError, TypeError):
                continue

            if prochaine > maintenant:
                continue

            publish_id = engagement.get("publish_id")
            if not publish_id:
                continue

            # Collecter les métriques
            metriques = self.collecter_engagement_clip(publish_id)
            if metriques is None:
                continue

            checkpoint_idx = engagement.get("checkpoint_index", 0)
            delai_h = CHECKPOINTS_HEURES[checkpoint_idx] if checkpoint_idx < len(CHECKPOINTS_HEURES) else 0

            collecte = {
                "date_collecte": maintenant.isoformat(),
                "delai_heures": delai_h,
                **metriques,
            }
            engagement["collectes"].append(collecte)

            # Planifier le prochain checkpoint
            next_idx = checkpoint_idx + 1
            if next_idx < len(CHECKPOINTS_HEURES):
                # Calculer la prochaine collecte depuis la publication
                timestamp_pub = entree.get("timestamp_succes", maintenant.isoformat())
                try:
                    dt_pub = datetime.fromisoformat(timestamp_pub)
                except (ValueError, TypeError):
                    dt_pub = maintenant
                prochaine_dt = dt_pub + timedelta(hours=CHECKPOINTS_HEURES[next_idx])
                engagement["prochaine_collecte"] = prochaine_dt.isoformat()
                engagement["checkpoint_index"] = next_idx
            else:
                engagement["prochaine_collecte"] = None  # Plus de collectes

            nb_collectes += 1
            logger.info(
                f"Engagement collecté pour {entree['clip_id']} : "
                f"{metriques['vues']} vues, {metriques['likes']} likes"
            )

        if nb_collectes:
            self._mettre_a_jour_stats_globales()
            self.state.sauvegarder()

    def _mettre_a_jour_stats_globales(self):
        """Met à jour les statistiques d'engagement globales."""
        stats = self.state.get_statistiques()
        total_vues = 0
        total_likes = 0
        total_partages = 0
        total_commentaires = 0
        par_categorie = {}
        par_heure = {}

        for entree in self.state.get_file_publication():
            engagement = entree.get("engagement", {})
            collectes = engagement.get("collectes", [])
            if not collectes:
                continue

            # Prendre la dernière collecte
            derniere = collectes[-1]
            vues = derniere.get("vues", 0)
            likes = derniere.get("likes", 0)

            total_vues += vues
            total_likes += likes
            total_partages += derniere.get("partages", 0)
            total_commentaires += derniere.get("commentaires", 0)

            # Par catégorie
            video_id = entree.get("video_id")
            if video_id:
                video = self.state.get_video(video_id)
                if video:
                    cat = video.get("categorie", "autre")
                    if cat not in par_categorie:
                        par_categorie[cat] = {"vues": 0, "likes": 0, "count": 0}
                    par_categorie[cat]["vues"] += vues
                    par_categorie[cat]["likes"] += likes
                    par_categorie[cat]["count"] += 1

            # Par heure de publication
            ts_pub = entree.get("timestamp_succes")
            if ts_pub:
                try:
                    heure = datetime.fromisoformat(ts_pub).hour
                    if heure not in par_heure:
                        par_heure[heure] = {"vues": 0, "likes": 0, "count": 0}
                    par_heure[heure]["vues"] += vues
                    par_heure[heure]["likes"] += likes
                    par_heure[heure]["count"] += 1
                except (ValueError, TypeError):
                    pass

        stats["engagement_global"] = {
            "total_vues": total_vues,
            "total_likes": total_likes,
            "total_partages": total_partages,
            "total_commentaires": total_commentaires,
            "par_categorie": par_categorie,
            "par_heure": par_heure,
        }

    def get_engagement_global(self) -> Dict:
        """Retourne les stats d'engagement globales pour l'UI."""
        stats = self.state.get_statistiques()
        return stats.get("engagement_global", {
            "total_vues": 0,
            "total_likes": 0,
            "total_partages": 0,
            "total_commentaires": 0,
            "par_categorie": {},
            "par_heure": {},
        })

    def get_top_clips(self, n: int = 10) -> List[Dict]:
        """Retourne les N clips les plus performants."""
        clips_avec_engagement = []
        for entree in self.state.get_file_publication():
            engagement = entree.get("engagement", {})
            collectes = engagement.get("collectes", [])
            if not collectes:
                continue
            derniere = collectes[-1]
            clips_avec_engagement.append({
                "clip_id": entree["clip_id"],
                "description": entree.get("description", "")[:50],
                "vues": derniere.get("vues", 0),
                "likes": derniere.get("likes", 0),
                "partages": derniere.get("partages", 0),
                "date_pub": entree.get("timestamp_succes", ""),
            })
        clips_avec_engagement.sort(key=lambda x: x["vues"], reverse=True)
        return clips_avec_engagement[:n]
