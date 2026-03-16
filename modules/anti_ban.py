"""
anti_ban.py — Système anti-ban intelligent

Protège le compte TikTok contre la détection de publication automatisée
en randomisant les délais, limitant les publications quotidiennes,
et respectant des fenêtres d'activité humaines.
"""

import logging
import random
from datetime import datetime, date, time as dtime
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class AntiBanManager:
    """
    Gestionnaire anti-ban vérifiant 4 conditions avant chaque publication :
    1. Heure dans la fenêtre d'activité
    2. Limite quotidienne non atteinte
    3. Cooldown respecté après échecs consécutifs
    4. Pattern weekend (limite réduite)
    """

    def __init__(self, config: Dict):
        cfg = config.get("anti_ban", {})
        self.actif = cfg.get("actif", True)
        self.limite_quotidienne = cfg.get("limite_quotidienne", 10)
        self.limite_weekend = cfg.get("limite_quotidienne_weekend", 6)
        self.cooldown_seuil = cfg.get("cooldown_echecs_consecutifs", 2)
        self.pattern_weekend = cfg.get("pattern_weekend", True)

        # Fenêtre d'activité
        fenetre = cfg.get("fenetre_activite", ["08:00", "23:00"])
        self.heure_debut = self._parse_heure(fenetre[0]) if len(fenetre) >= 1 else dtime(8, 0)
        self.heure_fin = self._parse_heure(fenetre[1]) if len(fenetre) >= 2 else dtime(23, 0)

        # Compteurs (réinitialisés quotidiennement)
        self._publications_aujourdhui = 0
        self._echecs_consecutifs = 0
        self._date_compteur = date.today()
        self._intervalle_base_secondes = config.get("publication", {}).get("intervalle_minutes", 5) * 60

    @staticmethod
    def _parse_heure(s: str) -> dtime:
        """Parse une heure au format 'HH:MM'."""
        try:
            parts = s.strip().split(":")
            return dtime(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return dtime(8, 0)

    def _reset_si_nouveau_jour(self):
        """Réinitialise les compteurs si on a changé de jour."""
        aujourdhui = date.today()
        if aujourdhui != self._date_compteur:
            self._publications_aujourdhui = 0
            self._date_compteur = aujourdhui
            logger.info("Anti-ban : compteur quotidien réinitialisé")

    def _est_weekend(self) -> bool:
        return datetime.now().weekday() >= 5  # samedi=5, dimanche=6

    def _limite_du_jour(self) -> int:
        if self.pattern_weekend and self._est_weekend():
            return self.limite_weekend
        return self.limite_quotidienne

    def peut_publier(self) -> Tuple[bool, str]:
        """
        Vérifie si la publication est autorisée maintenant.

        Returns:
            (autorisé, raison) — raison est vide si autorisé
        """
        if not self.actif:
            return True, ""

        self._reset_si_nouveau_jour()

        # 1. Fenêtre d'activité
        heure_actuelle = datetime.now().time()
        if self.heure_debut <= self.heure_fin:
            dans_fenetre = self.heure_debut <= heure_actuelle <= self.heure_fin
        else:
            # Fenêtre traversant minuit (ex: 22:00 → 02:00)
            dans_fenetre = heure_actuelle >= self.heure_debut or heure_actuelle <= self.heure_fin

        if not dans_fenetre:
            return False, (
                f"Hors fenêtre d'activité ({self.heure_debut.strftime('%H:%M')}"
                f"–{self.heure_fin.strftime('%H:%M')})"
            )

        # 2. Limite quotidienne
        limite = self._limite_du_jour()
        if self._publications_aujourdhui >= limite:
            jour_type = "weekend" if self._est_weekend() else "semaine"
            return False, f"Limite quotidienne atteinte ({self._publications_aujourdhui}/{limite}, {jour_type})"

        # 3. Cooldown après échecs consécutifs
        if self._echecs_consecutifs >= self.cooldown_seuil:
            return False, (
                f"Cooldown actif — {self._echecs_consecutifs} échecs consécutifs "
                f"(seuil : {self.cooldown_seuil}). Réessayez après un délai."
            )

        return True, ""

    def calculer_delai(self) -> float:
        """
        Retourne un délai gaussien randomisé en secondes.
        Centré sur l'intervalle configuré, avec une variance naturelle.
        """
        mean = self._intervalle_base_secondes
        std = mean / 3.0
        delai = random.gauss(mean, std)
        # Clamper entre 30% et 300% de la moyenne
        delai = max(mean * 0.3, min(delai, mean * 3.0))
        return delai

    def enregistrer_publication(self):
        """Enregistre une publication réussie."""
        self._reset_si_nouveau_jour()
        self._publications_aujourdhui += 1
        self._echecs_consecutifs = 0
        logger.info(
            f"Anti-ban : publication {self._publications_aujourdhui}/{self._limite_du_jour()} aujourd'hui"
        )

    def enregistrer_echec(self):
        """Enregistre un échec de publication."""
        self._echecs_consecutifs += 1
        logger.warning(f"Anti-ban : {self._echecs_consecutifs} échec(s) consécutif(s)")

    def reset_cooldown(self):
        """Réinitialise le compteur d'échecs (après intervention manuelle)."""
        self._echecs_consecutifs = 0
        logger.info("Anti-ban : cooldown réinitialisé manuellement")

    def charger_depuis_state(self, statistiques: Dict):
        """Restaure les compteurs depuis l'état persisté."""
        self._publications_aujourdhui = statistiques.get("publications_aujourdhui", 0)
        self._echecs_consecutifs = statistiques.get("echecs_consecutifs", 0)
        date_str = statistiques.get("date_compteur_anti_ban")
        if date_str:
            try:
                self._date_compteur = date.fromisoformat(date_str)
            except ValueError:
                self._date_compteur = date.today()
        self._reset_si_nouveau_jour()

    def sauvegarder_dans_state(self, statistiques: Dict):
        """Persiste les compteurs dans l'état."""
        statistiques["publications_aujourdhui"] = self._publications_aujourdhui
        statistiques["echecs_consecutifs"] = self._echecs_consecutifs
        statistiques["date_compteur_anti_ban"] = self._date_compteur.isoformat()

    def get_statut(self) -> Dict:
        """Retourne l'état actuel pour l'affichage UI."""
        self._reset_si_nouveau_jour()
        limite = self._limite_du_jour()
        autorise, raison = self.peut_publier()
        return {
            "actif": self.actif,
            "publications_aujourdhui": self._publications_aujourdhui,
            "limite": limite,
            "restant": max(0, limite - self._publications_aujourdhui),
            "echecs_consecutifs": self._echecs_consecutifs,
            "cooldown_actif": self._echecs_consecutifs >= self.cooldown_seuil,
            "dans_fenetre": autorise or "fenêtre" not in raison,
            "fenetre": f"{self.heure_debut.strftime('%H:%M')}–{self.heure_fin.strftime('%H:%M')}",
            "est_weekend": self._est_weekend(),
            "peut_publier": autorise,
            "raison_blocage": raison,
        }
