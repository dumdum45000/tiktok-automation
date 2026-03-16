"""
state_manager.py — Gestionnaire d'état persistant du pipeline

Gère le fichier pipeline_state.json qui permet à l'application de
reprendre exactement là où elle s'était arrêtée après un crash, une
mise en veille ou une fermeture accidentelle.

Chaque opération réussie est immédiatement persistée sur disque.
"""

import json
import os
import shutil
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StateManager:
    """
    Gestionnaire singleton de l'état du pipeline.
    Toutes les opérations d'écriture sont atomiques (write + rename).
    """

    def __init__(self, state_file: str = "pipeline_state.json"):
        self.state_file = state_file
        self.state: Dict[str, Any] = self._charger_ou_initialiser()

    def _charger_ou_initialiser(self) -> Dict[str, Any]:
        """Charge l'état existant ou crée un état initial propre. Essaie les backups si corrompu."""
        fichiers_a_essayer = [self.state_file]
        for i in range(1, 4):
            fichiers_a_essayer.append(f"{self.state_file}.bak.{i}")

        for fichier in fichiers_a_essayer:
            if not os.path.exists(fichier):
                continue
            try:
                with open(fichier, "r", encoding="utf-8") as f:
                    etat = json.load(f)
                nb_reset = 0
                for entree in etat.get("file_publication", []):
                    if entree.get("statut") == "en_cours":
                        entree["statut"] = "en_attente"
                        nb_reset += 1
                if nb_reset:
                    logger.warning(f"{nb_reset} clip(s) remis en attente (statut en_cours au démarrage)")
                source = f"backup {fichier}" if fichier != self.state_file else self.state_file
                logger.info(f"État chargé depuis {source}")
                return etat
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Fichier {fichier} corrompu ({e}), essai suivant...")
                continue

        logger.info("Aucun état valide trouvé, initialisation propre.")
        return self._etat_initial()

    def _etat_initial(self) -> Dict[str, Any]:
        """Retourne la structure d'état vide."""
        return {
            "version": "1.0",
            "derniere_mise_a_jour": None,
            "videos": {},
            "file_publication": [],
            "statistiques": {
                "total_imports": 0,
                "total_clips_generes": 0,
                "total_publies": 0,
                "total_echecs": 0,
                "historique": []
            }
        }

    def _rotation_backups(self):
        """Effectue une rotation des 3 derniers backups avant sauvegarde."""
        try:
            # .bak.2 → .bak.3, .bak.1 → .bak.2, principal → .bak.1
            for i in range(3, 1, -1):
                src = f"{self.state_file}.bak.{i - 1}"
                dst = f"{self.state_file}.bak.{i}"
                if os.path.exists(src):
                    shutil.copy2(src, dst)
            if os.path.exists(self.state_file):
                shutil.copy2(self.state_file, f"{self.state_file}.bak.1")
        except Exception as e:
            logger.debug(f"Rotation backups : {e}")

    def sauvegarder(self):
        """
        Sauvegarde atomique via un fichier temporaire + rotation de 3 backups.
        Évite la corruption si le programme crash pendant l'écriture.
        """
        self.state["derniere_mise_a_jour"] = datetime.now().isoformat()
        self._rotation_backups()
        fichier_temp = self.state_file + ".tmp"
        try:
            with open(fichier_temp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            shutil.move(fichier_temp, self.state_file)
        except Exception as e:
            logger.error(f"Erreur sauvegarde état : {e}")
            if os.path.exists(fichier_temp):
                os.remove(fichier_temp)

    # ─── Gestion des vidéos ───────────────────────────────────────────────

    def enregistrer_video(self, video_id: str, chemin: str, metadata: Dict):
        """Enregistre une nouvelle vidéo téléchargée dans l'état."""
        self.state["videos"][video_id] = {
            "id": video_id,
            "chemin": chemin,
            "metadata": metadata,
            "etape": "telecharge",
            "categorie": None,
            "clips": [],
            "timestamp_import": datetime.now().isoformat(),
            "erreurs": []
        }
        self.state["statistiques"]["total_imports"] += 1
        self.sauvegarder()
        logger.info(f"Vidéo enregistrée : {video_id}")

    def mettre_a_jour_etape(self, video_id: str, etape: str, donnees_extra: Optional[Dict] = None):
        """Met à jour l'étape courante d'une vidéo dans le pipeline."""
        if video_id not in self.state["videos"]:
            logger.warning(f"Video ID inconnu : {video_id}")
            return
        self.state["videos"][video_id]["etape"] = etape
        self.state["videos"][video_id][f"timestamp_{etape}"] = datetime.now().isoformat()
        if donnees_extra:
            self.state["videos"][video_id].update(donnees_extra)
        self.sauvegarder()

    def enregistrer_categorie(self, video_id: str, categorie: str):
        """Enregistre la catégorie d'une vidéo."""
        if video_id in self.state["videos"]:
            self.state["videos"][video_id]["categorie"] = categorie
            self.sauvegarder()

    def enregistrer_clip(self, video_id: str, clip_info: Dict):
        """Ajoute un clip généré à la liste des clips d'une vidéo (sans doublon)."""
        if video_id not in self.state["videos"]:
            return
        clip_id = clip_info.get("id")
        clips = self.state["videos"][video_id]["clips"]
        # Si le clip existe déjà, mettre à jour au lieu d'ajouter
        for clip in clips:
            if clip.get("id") == clip_id:
                clip.update(clip_info)
                self.sauvegarder()
                return
        clips.append(clip_info)
        self.state["statistiques"]["total_clips_generes"] += 1
        self.sauvegarder()

    def mettre_a_jour_clip(self, video_id: str, clip_id: str, donnees: Dict):
        """Met à jour les informations d'un clip spécifique."""
        if video_id not in self.state["videos"]:
            return
        for clip in self.state["videos"][video_id]["clips"]:
            if clip.get("id") == clip_id:
                clip.update(donnees)
                self.sauvegarder()
                return

    def enregistrer_erreur(self, video_id: str, erreur: str):
        """Enregistre une erreur non-bloquante sur une vidéo."""
        if video_id in self.state["videos"]:
            self.state["videos"][video_id]["erreurs"].append({
                "message": erreur,
                "timestamp": datetime.now().isoformat()
            })
            self.sauvegarder()

    def get_video(self, video_id: str) -> Optional[Dict]:
        """Retourne les données d'une vidéo."""
        return self.state["videos"].get(video_id)

    def get_toutes_videos(self) -> Dict[str, Dict]:
        """Retourne toutes les vidéos enregistrées."""
        return self.state["videos"]

    def get_videos_par_etape(self, etape: str) -> List[Dict]:
        """Retourne toutes les vidéos à une étape donnée."""
        return [v for v in self.state["videos"].values() if v.get("etape") == etape]

    # ─── Gestion de la file de publication ───────────────────────────────

    def ajouter_a_file_publication(self, clip_id: str, clip_info: Dict, intervalle_minutes: float = 5.0):
        """Ajoute un clip approuvé à la file de publication (sans doublon)."""
        # Refuser si clip déjà publié avec succès ou déjà en attente/en cours
        for entree_existante in self.state["file_publication"]:
            if entree_existante["clip_id"] == clip_id:
                statut_ex = entree_existante.get("statut", "")
                if statut_ex == "succes":
                    logger.warning(f"Clip {clip_id} déjà publié avec succès — doublon ignoré")
                    return entree_existante
                if statut_ex in ("en_attente", "en_cours"):
                    logger.warning(f"Clip {clip_id} déjà en file ({statut_ex}) — doublon ignoré")
                    return entree_existante

        # Calculer l'heure de publication prévue
        nb_en_attente = len([
            c for c in self.state["file_publication"]
            if c.get("statut") in ("en_attente", "en_cours")
        ])
        from datetime import timedelta
        heure_prevue = (datetime.now() + timedelta(minutes=intervalle_minutes * nb_en_attente)).isoformat()

        entree = {
            "clip_id": clip_id,
            "video_id": clip_info.get("video_id"),
            "chemin_clip": clip_info.get("chemin_final"),
            "description": clip_info.get("description", ""),
            "hashtags": clip_info.get("hashtags", []),
            "statut": "en_attente",
            "heure_prevue": heure_prevue,
            "tentatives": 0,
            "timestamp_ajout": datetime.now().isoformat(),
            "numero_partie": clip_info.get("numero_partie", 1),
            "total_parties": clip_info.get("total_parties", 1),
        }
        self.state["file_publication"].append(entree)
        # Trier la file : d'abord par video_id (même série ensemble), puis par numéro de partie
        self.state["file_publication"].sort(key=lambda e: (
            e.get("video_id", ""),
            e.get("numero_partie", 1)
        ))
        self.sauvegarder()
        return entree

    def mettre_a_jour_statut_publication(self, clip_id: str, statut: str, message: str = ""):
        """Met à jour le statut de publication d'un clip."""
        for entree in self.state["file_publication"]:
            if entree["clip_id"] == clip_id:
                entree["statut"] = statut
                entree[f"timestamp_{statut}"] = datetime.now().isoformat()
                if message:
                    entree["message"] = message
                if statut == "succes":
                    self.state["statistiques"]["total_publies"] += 1
                elif statut == "echec_definitif":
                    self.state["statistiques"]["total_echecs"] += 1
                self.sauvegarder()
                return

    def incrementer_tentatives(self, clip_id: str) -> int:
        """Incrémente le compteur de tentatives et retourne le nouveau total."""
        for entree in self.state["file_publication"]:
            if entree["clip_id"] == clip_id:
                entree["tentatives"] = entree.get("tentatives", 0) + 1
                self.sauvegarder()
                return entree["tentatives"]
        return 0

    def get_file_publication(self) -> List[Dict]:
        """Retourne la file de publication complète."""
        return self.state["file_publication"]

    def get_prochain_a_publier(self) -> Optional[Dict]:
        """Retourne le prochain clip en attente de publication (Partie 1 avant 2 avant 3...)."""
        en_attente = [e for e in self.state["file_publication"] if e.get("statut") == "en_attente"]
        if not en_attente:
            return None
        # Garantir l'ordre : même série ensemble, puis numéro de partie croissant
        en_attente.sort(key=lambda e: (e.get("video_id", ""), e.get("numero_partie", 1)))
        return en_attente[0]

    # ─── Statistiques ────────────────────────────────────────────────────

    def ajouter_historique(self, evenement: str, details: Dict = None):
        """Ajoute un événement à l'historique des 30 derniers jours."""
        entree = {
            "date": datetime.now().isoformat(),
            "evenement": evenement,
            "details": details or {}
        }
        historique = self.state["statistiques"]["historique"]
        historique.append(entree)
        # Garder seulement 30 jours
        seuil = (datetime.now() - timedelta(days=30)).isoformat()
        self.state["statistiques"]["historique"] = [
            h for h in historique if h["date"] >= seuil
        ]
        self.sauvegarder()

    def get_statistiques(self) -> Dict:
        """Retourne les statistiques globales."""
        return self.state["statistiques"]

    def a_session_precedente(self) -> bool:
        """Vérifie s'il existe une session précédente récupérable."""
        return (
            self.state.get("derniere_mise_a_jour") is not None
            and len(self.state["videos"]) > 0
        )

    def nettoyer_fichiers_partiels(self, dossier_clips: str, dossier_processed: str):
        """
        Nettoie les fichiers temporaires interrompus au milieu du traitement.
        Les fichiers .tmp ou avec suffixe _partial sont supprimés.
        """
        for dossier in [dossier_clips, dossier_processed]:
            if not os.path.exists(dossier):
                continue
            for fichier in os.listdir(dossier):
                if fichier.endswith(".tmp") or "_partial" in fichier:
                    chemin = os.path.join(dossier, fichier)
                    try:
                        os.remove(chemin)
                        logger.info(f"Fichier partiel supprimé : {chemin}")
                    except Exception as e:
                        logger.warning(f"Impossible de supprimer {chemin} : {e}")

    def nettoyer_fichiers_publies(self):
        """
        Supprime les fichiers intermédiaires (étapes 0-5) des clips publiés avec succès.
        Garde uniquement le fichier final (_6_partie.mp4).
        """
        nb_supprimes = 0
        for entree in self.state["file_publication"]:
            if entree.get("statut") != "succes":
                continue
            video_id = entree.get("video_id")
            clip_id = entree.get("clip_id")
            if not video_id or video_id not in self.state["videos"]:
                continue
            for clip in self.state["videos"][video_id].get("clips", []):
                if clip.get("id") != clip_id:
                    continue
                chemins_etapes = clip.get("chemins_etapes", {})
                chemin_final = clip.get("chemin_final", "")
                for etape, chemin in chemins_etapes.items():
                    if chemin and chemin != chemin_final and os.path.exists(chemin):
                        try:
                            os.remove(chemin)
                            nb_supprimes += 1
                        except Exception as e:
                            logger.debug(f"Nettoyage {chemin} : {e}")
                break
        if nb_supprimes:
            logger.info(f"Nettoyage : {nb_supprimes} fichier(s) intermédiaire(s) supprimé(s)")
