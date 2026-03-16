"""
pipeline.py — Orchestrateur du pipeline de traitement

Enchaîne tous les modules (1 à 9) pour traiter une vidéo source
du téléchargement jusqu'au clip final prêt à publier.

Chaque étape est persistée dans le state_manager pour permettre
la reprise après crash ou mise en veille.
"""

import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

from modules.state_manager import StateManager
from modules.downloader import telecharger_video
from modules.tagger import auto_tag, appliquer_categorie
from modules.analyzer import analyser_et_decouper
from modules.converter import convertir_en_portrait
from modules.watermark import ajouter_watermark
from modules.subtitles import ajouter_sous_titres
from modules.music_mixer import ajouter_musique_fond
from modules.intro_outro import ajouter_intro_outro
from modules.description_generator import generer_description_et_hashtags
from modules.disk_monitor import verifier_espace_disque
from modules.notifications import notifier_batch_termine, notifier_erreur_critique
from modules.ffmpeg_utils import verifier_ffmpeg_installe

logger = logging.getLogger(__name__)

# Ordre des étapes pour la reprise intelligente
ORDRE_ETAPES = {
    "decoupé": 0, "portrait": 1, "watermark": 2,
    "subtitles": 3, "music": 4, "final": 5, "partie": 6, "pret": 7,
}


def verifier_prerequis(config: Dict) -> List[Dict]:
    """
    Vérifie que les outils nécessaires sont installés.

    Returns:
        Liste de dicts {niveau: 'ok'|'warning'|'error', message: str}
    """
    resultats = []

    # FFmpeg / FFprobe
    ffmpeg_ok, ffprobe_ok, version = verifier_ffmpeg_installe()
    if ffmpeg_ok and ffprobe_ok:
        resultats.append({"niveau": "ok", "message": f"FFmpeg installé ({version[:60] if version else '?'})"})
    else:
        if not ffmpeg_ok:
            resultats.append({"niveau": "error", "message": "FFmpeg introuvable. Installez : brew install ffmpeg"})
        if not ffprobe_ok:
            resultats.append({"niveau": "error", "message": "FFprobe introuvable."})

    # Whisper-cpp (optionnel)
    from modules.subtitles import trouver_whisper_cpp
    if trouver_whisper_cpp():
        resultats.append({"niveau": "ok", "message": "whisper-cpp installé"})
    else:
        resultats.append({"niveau": "warning", "message": "whisper-cpp absent — sous-titres désactivés. Installez : brew install whisper-cpp"})

    # Bibliothèque musicale
    dossier_music = config.get("chemins", {}).get("music_library", "music_library")
    nb_musiques = 0
    if os.path.exists(dossier_music):
        for sous_dossier in os.listdir(dossier_music):
            chemin_sd = os.path.join(dossier_music, sous_dossier)
            if os.path.isdir(chemin_sd):
                nb_musiques += len([f for f in os.listdir(chemin_sd) if f.endswith(('.mp3', '.m4a', '.wav', '.ogg'))])
    if nb_musiques > 0:
        resultats.append({"niveau": "ok", "message": f"Bibliothèque musicale : {nb_musiques} fichier(s)"})
    else:
        resultats.append({"niveau": "warning", "message": "Bibliothèque musicale vide — ajoutez des musiques dans music_library/"})

    # Espace disque
    seuil = config.get("disque", {}).get("alerte_espace_go", 1)
    if verifier_espace_disque(seuil):
        resultats.append({"niveau": "ok", "message": "Espace disque suffisant"})
    else:
        resultats.append({"niveau": "warning", "message": f"Espace disque faible (< {seuil} Go)"})

    return resultats


def construire_chemins_processing(clip_id: str, dossier: str) -> Dict[str, str]:
    """
    Construit les chemins de chaque étape de traitement pour un clip donné.
    Chaque étape produit un fichier distinct pour faciliter le debug et la reprise.
    """
    base = os.path.join(dossier, clip_id)
    return {
        "brut": f"{base}_0_brut.mp4",
        "portrait": f"{base}_1_portrait.mp4",
        "watermark": f"{base}_2_watermark.mp4",
        "subtitles": f"{base}_3_subtitles.mp4",
        "music": f"{base}_4_music.mp4",
        "final": f"{base}_5_final.mp4",
        "partie": f"{base}_6_partie.mp4",
    }


def traiter_clip(
    clip_info: Dict,
    video_id: str,
    chemin_json_video: str,
    categorie: str,
    config: Dict,
    state: StateManager,
    callback: Optional[Callable[[str], None]] = None,
    numero_partie: int = 1,
    total_parties: int = 1,
) -> Optional[Dict]:
    """
    Traite un clip brut à travers les modules 4-9.

    Reprend à l'étape suivante si une étape précédente est déjà réalisée
    (fichier déjà existant sur disque).

    Args:
        clip_info: Informations du clip (chemin, id, durée, etc.)
        video_id: ID de la vidéo parente
        chemin_json_video: Chemin vers le JSON des métadonnées source
        categorie: Catégorie du clip
        config: Configuration globale
        state: Gestionnaire d'état
        callback: Fonction de progression

    Returns:
        clip_info mis à jour avec le chemin final, ou None si échec
    """
    def log(msg):
        logger.info(f"[{clip_info['id']}] {msg}")
        if callback:
            callback(f"[{clip_info['id']}] {msg}")

    dossier_processed = config.get("chemins", {}).get("processed", "data/processed")
    os.makedirs(dossier_processed, exist_ok=True)

    clip_id = clip_info["id"]
    chemins = construire_chemins_processing(clip_id, dossier_processed)

    # Reprise intelligente : combiner fichier existant + étape enregistrée
    etape_clip = clip_info.get("etape", "")
    num_etape_clip = ORDRE_ETAPES.get(etape_clip, -1)

    def etape_completee(nom_etape: str, chemin_fichier: str) -> bool:
        fichier_ok = os.path.exists(chemin_fichier) and os.path.getsize(chemin_fichier) > 1000
        etape_ok = ORDRE_ETAPES.get(nom_etape, 99) <= num_etape_clip
        return fichier_ok and etape_ok

    # Copier le clip brut si nécessaire
    chemin_brut = clip_info["chemin"]
    if not os.path.exists(chemins["brut"]) or os.path.getsize(chemins["brut"]) == 0:
        import shutil
        shutil.copy2(chemin_brut, chemins["brut"])

    # ── Étape 4 : Conversion portrait 9:16 ────────────────────────────────
    if not etape_completee("portrait", chemins["portrait"]):
        log("Conversion portrait 9:16...")
        from modules.converter import convertir_en_portrait
        if not convertir_en_portrait(chemins["brut"], chemins["portrait"], config, callback):
            log("❌ Échec conversion portrait — vérifiez que ffmpeg est installé et que le fichier source est valide")
            state.enregistrer_erreur(video_id, f"Échec conversion portrait pour {clip_id} — fichier source peut-être corrompu")
            return None
        state.mettre_a_jour_clip(video_id, clip_id, {"etape": "portrait"})
    else:
        log("Étape portrait déjà effectuée ✓")

    # ── Étape 5 : Watermark ────────────────────────────────────────────────
    if not etape_completee("watermark", chemins["watermark"]):
        log("Ajout du filigrane...")
        from modules.watermark import ajouter_watermark
        if not ajouter_watermark(chemins["portrait"], chemins["watermark"], config, callback):
            log("❌ Échec watermark — le clip sera utilisé sans filigrane")
            import shutil
            shutil.copy2(chemins["portrait"], chemins["watermark"])
        state.mettre_a_jour_clip(video_id, clip_id, {"etape": "watermark"})
    else:
        log("Étape watermark déjà effectuée ✓")

    # ── Étape 6 : Sous-titres ──────────────────────────────────────────────
    if not etape_completee("subtitles", chemins["subtitles"]):
        log("Génération des sous-titres (Whisper)...")
        from modules.subtitles import ajouter_sous_titres
        if not ajouter_sous_titres(chemins["watermark"], chemins["subtitles"], config, callback):
            log("❌ Échec sous-titres — les sous-titres sont désactivés pour ce clip")
            import shutil
            shutil.copy2(chemins["watermark"], chemins["subtitles"])
        state.mettre_a_jour_clip(video_id, clip_id, {"etape": "subtitles"})
    else:
        log("Étape sous-titres déjà effectuée ✓")

    # ── Étape 7 : Musique de fond ──────────────────────────────────────────
    if not etape_completee("music", chemins["music"]):
        log(f"Ajout de la musique de fond (catégorie : {categorie})...")
        from modules.music_mixer import ajouter_musique_fond
        if not ajouter_musique_fond(chemins["subtitles"], chemins["music"], categorie, config, callback):
            log("❌ Échec musique de fond — le clip conserve son audio original")
            import shutil
            shutil.copy2(chemins["subtitles"], chemins["music"])
        state.mettre_a_jour_clip(video_id, clip_id, {"etape": "music"})
    else:
        log("Étape musique déjà effectuée ✓")

    # ── Étape 8 : Intro/Outro ──────────────────────────────────────────────
    if not etape_completee("final", chemins["final"]):
        log("Génération de l'intro et de l'outro...")
        from modules.intro_outro import ajouter_intro_outro
        if not ajouter_intro_outro(chemins["music"], chemins["final"], config, callback):
            log("❌ Échec intro/outro — le clip est utilisé tel quel sans intro/outro")
            import shutil
            shutil.copy2(chemins["music"], chemins["final"])
        state.mettre_a_jour_clip(video_id, clip_id, {"etape": "final"})
    else:
        log("Étape intro/outro déjà effectuée ✓")

    # ── Étape 9 : Numéro de partie ─────────────────────────────────────────
    chemin_sortie_partie = chemins["partie"]
    if not etape_completee("partie", chemin_sortie_partie):
        log(f"Ajout 'Partie {numero_partie}/{total_parties}'...")
        from modules.watermark import ajouter_numero_partie
        if ajouter_numero_partie(chemins["final"], chemin_sortie_partie, numero_partie, total_parties, config, callback):
            chemin_final_avec_partie = chemin_sortie_partie
        else:
            log("❌ Échec partie — utilisation du clip sans numéro")
            chemin_final_avec_partie = chemins["final"]
    else:
        log(f"Étape partie déjà effectuée ✓")
        chemin_final_avec_partie = chemin_sortie_partie

    # ── Étape 10 : Description et hashtags ────────────────────────────────
    description, hashtags = generer_description_et_hashtags(
        chemin_json_video, clip_id, config, numero_partie, total_parties
    )

    # Mettre à jour les infos du clip
    clip_info_complet = {
        **clip_info,
        "chemin_final": chemin_final_avec_partie,
        "chemins_etapes": chemins,
        "description": description,
        "hashtags": hashtags,
        "categorie": categorie,
        "video_id": video_id,
        "numero_partie": numero_partie,
        "total_parties": total_parties,
        "etape": "pret",
        "statut_validation": "en_attente"
    }

    state.mettre_a_jour_clip(video_id, clip_id, clip_info_complet)
    log(f"✅ Clip prêt : {chemin_final_avec_partie}")

    return clip_info_complet


def traiter_video_complete(
    video_id: str,
    config: Dict,
    state: StateManager,
    callback: Optional[Callable[[str], None]] = None
) -> List[Dict]:
    """
    Traite une vidéo source complète depuis l'analyse jusqu'aux clips finaux.

    Reprend depuis la dernière étape réussie si le pipeline a été interrompu.

    Args:
        video_id: ID de la vidéo à traiter
        config: Configuration globale
        state: Gestionnaire d'état
        callback: Fonction de progression

    Returns:
        Liste des clips finaux générés
    """
    def log(msg):
        logger.info(f"[Pipeline] {msg}")
        if callback:
            callback(msg)

    video_data = state.get_video(video_id)
    if not video_data:
        log(f"❌ Vidéo inconnue : {video_id}")
        return []

    chemin_video = video_data["chemin"]
    metadata = video_data.get("metadata", {})
    categorie_brute = video_data.get("categorie")
    categorie = categorie_brute or "autre"

    # Auto-tag si la catégorie n'a pas été choisie manuellement
    if not categorie_brute or categorie_brute == "auto":
        titre = metadata.get("title", "")
        desc_meta = metadata.get("description", "")
        if titre or desc_meta:
            cat_suggeree, scores = auto_tag(titre, desc_meta, config)
            if scores.get(cat_suggeree, 0) > 0:
                categorie = cat_suggeree
                log(f"Auto-tag : catégorie détectée = {categorie} (scores: {scores})")
            else:
                categorie = "autre"
        else:
            categorie = "autre"
        # Persister la catégorie dans le state
        video_data["categorie"] = categorie
        state.enregistrer_categorie(video_id, categorie)

    etape_actuelle = video_data.get("etape", "telecharge")

    chemin_json = os.path.splitext(chemin_video)[0] + ".json"

    log(f"Traitement de '{metadata.get('title', video_id)[:50]}' (catégorie : {categorie})")

    # Vérifier l'espace disque
    if not verifier_espace_disque(config.get("disque", {}).get("alerte_espace_go", 50)):
        log("⚠️ Espace disque faible — traitement continué mais nettoyage recommandé")

    # ── Module 3 : Analyse et découpe ─────────────────────────────────────
    clips_existants = video_data.get("clips", [])
    clips_bruts = [c for c in clips_existants if c.get("etape") in ("decoupé", "portrait", "watermark", "subtitles", "music", "final", "pret")]

    if not clips_bruts:
        log("Analyse et découpe de la vidéo...")
        state.mettre_a_jour_etape(video_id, "analyse")

        dossier_clips = config.get("chemins", {}).get("clips", "data/clips")
        clips_bruts = analyser_et_decouper(chemin_video, dossier_clips, categorie, config, callback)

        if not clips_bruts:
            log("❌ Aucun clip généré par l'analyse")
            state.mettre_a_jour_etape(video_id, "erreur")
            return []

        for clip in clips_bruts:
            clip["video_id"] = video_id
            state.enregistrer_clip(video_id, clip)

        state.mettre_a_jour_etape(video_id, "decoupé")
        log(f"{len(clips_bruts)} clip(s) brut(s) générés")
    else:
        log(f"Reprise avec {len(clips_bruts)} clip(s) existant(s)")

    # ── Modules 4-9 : Traitement de chaque clip ────────────────────────────
    # Filtrer les clips à traiter
    clips_a_traiter = []
    clips_finaux = []
    for i, clip_info in enumerate(clips_bruts):
        if clip_info.get("statut_validation") == "rejeté":
            log(f"Clip {clip_info['id']} rejeté → ignoré")
            continue
        if clip_info.get("etape") == "pret" and clip_info.get("chemin_final"):
            if os.path.exists(clip_info["chemin_final"]):
                log(f"Clip {clip_info['id']} déjà traité → réutilisé")
                clips_finaux.append(clip_info)
                continue
        clips_a_traiter.append((i, clip_info))

    # Traitement parallèle ou séquentiel selon la config
    cfg_perf = config.get("performance", {})
    parallele = cfg_perf.get("traitement_parallele", False)
    max_workers = cfg_perf.get("max_workers", 2)

    def _traiter_un(args):
        i, clip_info = args
        return traiter_clip(
            clip_info=clip_info,
            video_id=video_id,
            chemin_json_video=chemin_json,
            categorie=categorie,
            config=config,
            state=state,
            callback=callback,
            numero_partie=i + 1,
            total_parties=len(clips_bruts),
        )

    if parallele and len(clips_a_traiter) > 1:
        log(f"Traitement parallèle ({max_workers} workers) de {len(clips_a_traiter)} clip(s)...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_traiter_un, args): args for args in clips_a_traiter}
            for future in as_completed(futures):
                i, clip_info = futures[future]
                try:
                    clip_final = future.result()
                    if clip_final:
                        clips_finaux.append(clip_final)
                    else:
                        log(f"❌ Échec traitement clip {clip_info['id']}")
                except Exception as e:
                    log(f"❌ Erreur clip {clip_info['id']} : {e}")
    else:
        for i, clip_info in clips_a_traiter:
            log(f"\n--- Traitement clip {i + 1}/{len(clips_bruts)} ---")
            clip_final = _traiter_un((i, clip_info))
            if clip_final:
                clips_finaux.append(clip_final)
            else:
                log(f"❌ Échec traitement clip {clip_info['id']}")

    state.mettre_a_jour_etape(video_id, "pret", {"nb_clips_prets": len(clips_finaux)})
    log(f"\n✅ {len(clips_finaux)}/{len(clips_bruts)} clip(s) prêts pour validation")

    return clips_finaux


def decouper_video(
    video_id: str,
    config: Dict,
    state: StateManager,
    callback: Optional[Callable[[str], None]] = None
) -> List[Dict]:
    """
    Phase 1 : Analyse et découpe uniquement.

    Retourne la liste des clips bruts générés sans les traiter (pas de portrait,
    watermark, etc.). Permet à l'utilisateur de prévisualiser et valider
    les clips avant le traitement complet.
    """
    def log(msg):
        logger.info(f"[Découpe] {msg}")
        if callback:
            callback(msg)

    video_data = state.get_video(video_id)
    if not video_data:
        log(f"Vidéo inconnue : {video_id}")
        return []

    chemin_video = video_data["chemin"]
    metadata = video_data.get("metadata", {})
    categorie = video_data.get("categorie") or "autre"

    # Auto-tag si nécessaire
    categorie_brute = video_data.get("categorie")
    if not categorie_brute or categorie_brute == "auto":
        titre = metadata.get("title", "")
        desc_meta = metadata.get("description", "")
        if titre or desc_meta:
            cat_suggeree, scores = auto_tag(titre, desc_meta, config)
            if scores.get(cat_suggeree, 0) > 0:
                categorie = cat_suggeree
                log(f"Auto-tag : catégorie détectée = {categorie}")
        video_data["categorie"] = categorie
        state.enregistrer_categorie(video_id, categorie)

    log(f"Découpe de '{metadata.get('title', video_id)[:50]}' (catégorie : {categorie})")

    clips_existants = video_data.get("clips", [])
    clips_bruts = [c for c in clips_existants if c.get("etape") in (
        "decoupé", "portrait", "watermark", "subtitles", "music", "final", "pret"
    )]

    if clips_bruts:
        log(f"{len(clips_bruts)} clip(s) déjà découpé(s)")
        return clips_bruts

    log("Analyse et découpe de la vidéo...")
    state.mettre_a_jour_etape(video_id, "analyse")

    dossier_clips = config.get("chemins", {}).get("clips", "data/clips")
    clips_bruts = analyser_et_decouper(chemin_video, dossier_clips, categorie, config, callback)

    if not clips_bruts:
        log("Aucun clip généré par l'analyse")
        state.mettre_a_jour_etape(video_id, "erreur")
        return []

    for clip in clips_bruts:
        clip["video_id"] = video_id
        state.enregistrer_clip(video_id, clip)

    state.mettre_a_jour_etape(video_id, "decoupé")
    log(f"{len(clips_bruts)} clip(s) brut(s) générés — prêts pour validation")
    return clips_bruts


def traiter_clips_approuves(
    video_id: str,
    config: Dict,
    state: StateManager,
    callback: Optional[Callable[[str], None]] = None
) -> List[Dict]:
    """
    Phase 2 : Traite uniquement les clips approuvés (ou en_attente) d'une vidéo.

    Applique les étapes 4-9 (portrait, watermark, sous-titres, musique, intro/outro)
    aux clips qui ont passé la validation.
    """
    def log(msg):
        logger.info(f"[Pipeline] {msg}")
        if callback:
            callback(msg)

    video_data = state.get_video(video_id)
    if not video_data:
        log(f"Vidéo inconnue : {video_id}")
        return []

    chemin_video = video_data["chemin"]
    categorie = video_data.get("categorie") or "autre"
    chemin_json = os.path.splitext(chemin_video)[0] + ".json"

    clips_existants = video_data.get("clips", [])
    clips_bruts = [c for c in clips_existants if c.get("etape") in (
        "decoupé", "portrait", "watermark", "subtitles", "music", "final", "pret"
    )]

    if not clips_bruts:
        log("Aucun clip à traiter")
        return []

    # Filtrer : traiter seulement les approuvés et en_attente
    clips_a_traiter = []
    clips_finaux = []
    for i, clip_info in enumerate(clips_bruts):
        if clip_info.get("statut_validation") == "rejeté":
            continue
        if clip_info.get("etape") == "pret" and clip_info.get("chemin_final"):
            if os.path.exists(clip_info["chemin_final"]):
                clips_finaux.append(clip_info)
                continue
        clips_a_traiter.append((i, clip_info))

    if not clips_a_traiter:
        log("Tous les clips sont déjà traités ou rejetés")
        return clips_finaux

    log(f"Traitement de {len(clips_a_traiter)} clip(s) approuvé(s)...")

    cfg_perf = config.get("performance", {})
    parallele = cfg_perf.get("traitement_parallele", False)
    max_workers = cfg_perf.get("max_workers", 2)

    def _traiter_un(args):
        i, clip_info = args
        return traiter_clip(
            clip_info=clip_info,
            video_id=video_id,
            chemin_json_video=chemin_json,
            categorie=categorie,
            config=config,
            state=state,
            callback=callback,
            numero_partie=i + 1,
            total_parties=len(clips_bruts),
        )

    if parallele and len(clips_a_traiter) > 1:
        log(f"Traitement parallèle ({max_workers} workers)...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_traiter_un, args): args for args in clips_a_traiter}
            for future in as_completed(futures):
                i, clip_info = futures[future]
                try:
                    clip_final = future.result()
                    if clip_final:
                        clips_finaux.append(clip_final)
                except Exception as e:
                    log(f"Erreur clip {clip_info['id']} : {e}")
    else:
        for i, clip_info in clips_a_traiter:
            log(f"\n--- Traitement clip {i + 1}/{len(clips_bruts)} ---")
            clip_final = _traiter_un((i, clip_info))
            if clip_final:
                clips_finaux.append(clip_final)

    state.mettre_a_jour_etape(video_id, "pret", {"nb_clips_prets": len(clips_finaux)})
    log(f"{len(clips_finaux)}/{len(clips_bruts)} clip(s) prêts")
    return clips_finaux


def traiter_batch_videos(
    video_ids: List[str],
    config: Dict,
    state: StateManager,
    callback: Optional[Callable[[str], None]] = None
) -> Dict:
    """
    Traite un batch de vidéos en séquence.

    Returns:
        Dictionnaire avec nb_succes, nb_echecs, clips_generes
    """
    def log(msg):
        if callback:
            callback(msg)

    resultats = {
        "nb_succes": 0,
        "nb_echecs": 0,
        "clips_generes": []
    }

    for i, video_id in enumerate(video_ids):
        log(f"\n{'='*50}")
        log(f"Vidéo {i + 1}/{len(video_ids)} : {video_id}")
        log(f"{'='*50}")

        try:
            clips = traiter_video_complete(video_id, config, state, callback)
            if clips:
                resultats["nb_succes"] += 1
                resultats["clips_generes"].extend(clips)
            else:
                resultats["nb_echecs"] += 1
        except Exception as e:
            logger.exception(f"Erreur critique traitement {video_id}")
            log(f"❌ Erreur critique : {e}")
            notifier_erreur_critique(str(e)[:100])
            resultats["nb_echecs"] += 1
            state.enregistrer_erreur(video_id, str(e))

    nb_clips = len(resultats["clips_generes"])
    notifier_batch_termine(nb_clips)
    log(f"\n🎉 Batch terminé : {resultats['nb_succes']} vidéo(s) traitées, {nb_clips} clip(s) générés")

    return resultats
