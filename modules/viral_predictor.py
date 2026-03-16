"""
viral_predictor.py — Score viral prédictif

Utilise une régression linéaire simple (numpy, pas de sklearn)
entraînée sur les données d'engagement historiques pour prédire
le potentiel viral d'un clip avant validation.

Features : durée, score analyseur, catégorie, heure, jour, hashtags.
Target : log(vues + 1) au checkpoint 48h.
"""

import logging
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Minimum de clips avec engagement pour entraîner le modèle
MIN_ECHANTILLONS = 15

# Catégories connues pour l'encodage one-hot
CATEGORIES = ["musique", "sport", "humour", "autre"]


class ViralPredictor:
    """
    Prédicteur de score viral basé sur régression linéaire régularisée.
    Entraîné sur les clips ayant au moins un checkpoint d'engagement (48h).
    """

    def __init__(self, state_manager):
        self.state = state_manager
        self.coefficients = None
        self.intercept = 0.0
        self.r2_score = 0.0
        self.nb_echantillons = 0
        self.date_entrainement = None
        self.features_names = []

        # Tenter de charger un modèle existant
        self._charger_modele()

    def _charger_modele(self):
        """Charge le modèle depuis l'état persisté."""
        stats = self.state.get_statistiques()
        modele = stats.get("modele_viral")
        if modele and modele.get("coefficients"):
            self.coefficients = np.array(modele["coefficients"])
            self.intercept = modele.get("intercept", 0.0)
            self.r2_score = modele.get("r2_score", 0.0)
            self.nb_echantillons = modele.get("nb_echantillons", 0)
            self.date_entrainement = modele.get("date_entrainement")
            self.features_names = modele.get("features", [])
            logger.info(f"Modèle viral chargé : R²={self.r2_score:.3f}, N={self.nb_echantillons}")

    def _sauvegarder_modele(self):
        """Persiste le modèle dans l'état."""
        stats = self.state.get_statistiques()
        stats["modele_viral"] = {
            "coefficients": self.coefficients.tolist() if self.coefficients is not None else [],
            "intercept": float(self.intercept),
            "r2_score": float(self.r2_score),
            "nb_echantillons": self.nb_echantillons,
            "date_entrainement": datetime.now().isoformat(),
            "features": self.features_names,
        }
        self.state.sauvegarder()

    def _extraire_features(self, clip_info: Dict, queue_entry: Dict) -> Optional[np.ndarray]:
        """
        Extrait le vecteur de features d'un clip.

        Features :
        0: durée du clip (secondes)
        1: score de l'analyseur (0-1)
        2-5: catégorie one-hot (musique, sport, humour, autre)
        6: heure de publication (0-23)
        7: jour de la semaine (0-6, lundi=0)
        8: nombre de hashtags
        9: numéro de partie
        """
        try:
            duree = clip_info.get("duree", 30)
            score = clip_info.get("score", 0.5)

            # Catégorie one-hot
            video_id = clip_info.get("video_id") or queue_entry.get("video_id")
            categorie = "autre"
            if video_id:
                video = self.state.get_video(video_id)
                if video:
                    categorie = video.get("categorie", "autre")
            cat_encoding = [1.0 if c == categorie else 0.0 for c in CATEGORIES]

            # Heure et jour
            heure_pub = 12
            jour_semaine = 0
            hp_str = queue_entry.get("heure_prevue")
            if hp_str:
                try:
                    hp = datetime.fromisoformat(hp_str)
                    heure_pub = hp.hour
                    jour_semaine = hp.weekday()
                except (ValueError, TypeError):
                    pass

            nb_hashtags = len(queue_entry.get("hashtags", []))
            num_partie = queue_entry.get("numero_partie", 1)

            features = [duree, score] + cat_encoding + [heure_pub, jour_semaine, nb_hashtags, num_partie]
            return np.array(features, dtype=float)

        except Exception as e:
            logger.debug(f"Erreur extraction features : {e}")
            return None

    def entrainer(self) -> bool:
        """
        Entraîne le modèle sur les clips avec données d'engagement ≥48h.

        Returns:
            True si l'entraînement a réussi
        """
        X_list = []
        y_list = []

        for entree in self.state.get_file_publication():
            engagement = entree.get("engagement", {})
            collectes = engagement.get("collectes", [])

            # Chercher un checkpoint avec ≥48h de données
            vues = None
            for collecte in collectes:
                if collecte.get("delai_heures", 0) >= 48:
                    vues = collecte.get("vues", 0)
                    break

            if vues is None:
                # Prendre la dernière collecte si elle existe
                if collectes:
                    vues = collectes[-1].get("vues")

            if vues is None:
                continue

            # Trouver le clip correspondant
            video_id = entree.get("video_id")
            clip_id = entree.get("clip_id")
            clip_info = {}
            if video_id:
                video = self.state.get_video(video_id)
                if video:
                    for clip in video.get("clips", []):
                        if clip.get("id") == clip_id:
                            clip_info = clip
                            break

            features = self._extraire_features(clip_info, entree)
            if features is None:
                continue

            X_list.append(features)
            y_list.append(math.log(vues + 1))

        if len(X_list) < MIN_ECHANTILLONS:
            logger.info(
                f"Modèle viral : pas assez de données ({len(X_list)}/{MIN_ECHANTILLONS})"
            )
            return False

        X = np.array(X_list)
        y = np.array(y_list)

        # Normaliser X (mean/std)
        self._X_mean = X.mean(axis=0)
        self._X_std = X.std(axis=0)
        self._X_std[self._X_std == 0] = 1.0  # Éviter division par zéro
        X_norm = (X - self._X_mean) / self._X_std

        # Ajouter colonne de biais
        X_bias = np.column_stack([np.ones(len(X_norm)), X_norm])

        # Régression linéaire régularisée (Ridge)
        # coefficients = (X^T X + λI)^{-1} X^T y
        lambda_reg = 1.0
        I = np.eye(X_bias.shape[1])
        I[0, 0] = 0  # Pas de régularisation sur le biais
        try:
            theta = np.linalg.solve(
                X_bias.T @ X_bias + lambda_reg * I,
                X_bias.T @ y
            )
        except np.linalg.LinAlgError:
            logger.warning("Modèle viral : erreur d'algèbre linéaire")
            return False

        self.intercept = float(theta[0])
        self.coefficients = theta[1:]

        # Calculer R²
        y_pred = X_bias @ theta
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        self.r2_score = float(1 - ss_res / max(ss_tot, 1e-10))

        self.nb_echantillons = len(X_list)
        self.features_names = [
            "duree", "score_analyse",
            "cat_musique", "cat_sport", "cat_humour", "cat_autre",
            "heure", "jour_semaine", "nb_hashtags", "num_partie"
        ]
        self.date_entrainement = datetime.now().isoformat()

        self._sauvegarder_modele()
        logger.info(
            f"Modèle viral entraîné : R²={self.r2_score:.3f}, "
            f"N={self.nb_echantillons}, intercept={self.intercept:.2f}"
        )
        return True

    def predire(self, clip_info: Dict, queue_entry: Optional[Dict] = None) -> Optional[float]:
        """
        Prédit le score viral d'un clip (0-100).

        Args:
            clip_info: Infos du clip (duree, score, etc.)
            queue_entry: Entrée de la file de publication (pour heure, hashtags)

        Returns:
            Score 0-100, ou None si le modèle n'est pas entraîné
        """
        if self.coefficients is None:
            return None

        if queue_entry is None:
            queue_entry = {}

        features = self._extraire_features(clip_info, queue_entry)
        if features is None:
            return None

        try:
            # Normaliser avec les mêmes paramètres d'entraînement
            if hasattr(self, '_X_mean') and hasattr(self, '_X_std'):
                features_norm = (features - self._X_mean) / self._X_std
            else:
                features_norm = features

            log_vues_pred = self.intercept + features_norm @ self.coefficients

            # Convertir en score 0-100
            # log(vues+1) typique : 0 (1 vue) à 12 (~160k vues)
            score = max(0, min(100, (log_vues_pred / 12.0) * 100))
            return round(score, 1)

        except Exception as e:
            logger.debug(f"Erreur prédiction : {e}")
            return None

    def est_entraine(self) -> bool:
        """Vérifie si un modèle est disponible."""
        return self.coefficients is not None

    def get_info_modele(self) -> Dict:
        """Retourne les infos du modèle pour l'affichage UI."""
        return {
            "entraine": self.est_entraine(),
            "r2_score": self.r2_score,
            "nb_echantillons": self.nb_echantillons,
            "date_entrainement": self.date_entrainement,
            "features": self.features_names,
        }
