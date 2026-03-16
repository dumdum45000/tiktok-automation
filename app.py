"""
app.py — Interface Streamlit principale

Application complète de création et publication TikTok automatisée.
5 onglets : Import / Preview & Validation / Publication / Statistiques / Paramètres

Lancement : streamlit run app.py
"""

import json
import logging
import logging.handlers
import os
import re
import sys
from urllib.parse import urlparse
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import streamlit as st

# ─── Chargement des variables d'environnement (.env + st.secrets) ─────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optionnel

# Streamlit Community Cloud : injecter st.secrets dans os.environ
try:
    for key, value in st.secrets.items():
        if isinstance(value, str) and key not in os.environ:
            os.environ[key] = value
except Exception:
    pass  # Pas de secrets configurés ou en local

# ─── Répertoire de travail : toujours le dossier de l'app ────────────────────
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(_APP_DIR)

# Dict global partagé entre threads (évite le ScriptRunContext de st.session_state)
_pipeline_progress_global: dict = {
    "etape_nom": "",
    "etape_num": 0,
    "etape_total": 6,
    "pct": 0.0,
    # ETA
    "debut_traitement": None,
    "clip_courant": 0,
    "total_clips": 0,
    "temps_par_clip": [],
    "eta_secondes": None,
    "debut_clip_courant": None,
}

# ─── Filtre de log pour masquer les données sensibles ────────────────────────
class _SensitiveFilter(logging.Filter):
    """Masque les emails et mots de passe dans les messages de log."""
    def filter(self, record):
        msg = record.getMessage()
        # Masquer les adresses email
        msg = re.sub(r'[\w.+-]+@[\w-]+\.[\w.]+', '***@***.***', msg)
        # Masquer les mots de passe après "password"
        msg = re.sub(r'(password["\s:=]+)[^\s,"]+', r'\1****', msg, flags=re.IGNORECASE)
        record.msg = msg
        record.args = ()
        return True

# ─── Configuration du logging avec rotation ──────────────────────────────────
os.makedirs(os.path.join(_APP_DIR, "logs"), exist_ok=True)
_log_file = os.path.join(_APP_DIR, "logs", "app.log")
_file_handler = logging.handlers.RotatingFileHandler(
    _log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.addFilter(_SensitiveFilter())
_stream_handler = logging.StreamHandler()
_stream_handler.addFilter(_SensitiveFilter())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[_file_handler, _stream_handler],
    force=True
)
logger = logging.getLogger(__name__)


# ─── Chargement de la configuration ──────────────────────────────────────────

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


@st.cache_data
def charger_config() -> Dict:
    """Charge la configuration depuis config.json."""
    if not os.path.exists(_CONFIG_PATH):
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def sauvegarder_config(config: Dict):
    """Sauvegarde la configuration dans config.json."""
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        charger_config.clear()
        return True
    except Exception as e:
        st.error(f"Impossible de sauvegarder la config : {e}")
        return False


# ─── Initialisation de l'état Streamlit ──────────────────────────────────────

def _get_state_path_for_account(compte_id: str) -> str:
    """Retourne le chemin du fichier d'état pour un compte donné."""
    if not compte_id or compte_id == "principal":
        return os.path.join(_APP_DIR, "pipeline_state.json")
    return os.path.join(_APP_DIR, f"pipeline_state_{compte_id}.json")


def get_compte_actif(config: Dict) -> Dict:
    """Retourne la config du compte actif, avec fallback sur l'identité globale."""
    compte_id = config.get("compte_actif", "principal")
    comptes = config.get("comptes", [])
    for c in comptes:
        if c.get("id") == compte_id:
            return c
    # Fallback : créer un compte à partir de l'identité globale
    identite = config.get("identite", {})
    return {
        "id": "principal",
        "nom": identite.get("nom_chaine", "Mon compte"),
        "tiktok_username": identite.get("tiktok_username", ""),
    }


def init_session():
    """Initialise les variables de session Streamlit."""
    if "config" not in st.session_state:
        st.session_state.config = charger_config()

    if "compte_actif" not in st.session_state:
        st.session_state.compte_actif = st.session_state.config.get("compte_actif", "principal")

    if "state_manager" not in st.session_state:
        from modules.state_manager import StateManager
        state_path = _get_state_path_for_account(st.session_state.compte_actif)
        st.session_state.state_manager = StateManager(state_path)

    if "logs_pipeline" not in st.session_state:
        st.session_state.logs_pipeline = []

    if "pipeline_en_cours" not in st.session_state:
        st.session_state.pipeline_en_cours = False

    if "scheduler" not in st.session_state:
        st.session_state.scheduler = None

    if "urls_batch" not in st.session_state:
        st.session_state.urls_batch = []


def ajouter_log(msg: str):
    """Ajoute un message aux logs de l'interface."""
    horodatage = datetime.now().strftime("%H:%M:%S")
    entree = f"[{horodatage}] {msg}"
    if "logs_pipeline" in st.session_state:
        st.session_state.logs_pipeline.append(entree)
        # Garder seulement les 200 derniers logs
        if len(st.session_state.logs_pipeline) > 200:
            st.session_state.logs_pipeline = st.session_state.logs_pipeline[-200:]
    logger.info(msg)


# ─── Composants UI réutilisables ──────────────────────────────────────────────

def afficher_sidebar():
    """Sidebar avec info espace disque et statut général."""
    with st.sidebar:
        st.title("🎬 TikTok Auto")

        # Sélecteur de compte
        config = st.session_state.config
        comptes = config.get("comptes", [])
        if len(comptes) > 1:
            noms_comptes = {c["id"]: c.get("nom", c["id"]) for c in comptes}
            compte_ids = list(noms_comptes.keys())
            idx_actuel = 0
            if st.session_state.compte_actif in compte_ids:
                idx_actuel = compte_ids.index(st.session_state.compte_actif)
            choix = st.selectbox(
                "Compte",
                compte_ids,
                index=idx_actuel,
                format_func=lambda x: noms_comptes.get(x, x),
                key="sidebar_compte"
            )
            if choix != st.session_state.compte_actif:
                st.session_state.compte_actif = choix
                config["compte_actif"] = choix
                sauvegarder_config(config)
                # Réinitialiser le state manager et le scheduler pour le nouveau compte
                from modules.state_manager import StateManager
                state_path = _get_state_path_for_account(choix)
                st.session_state.state_manager = StateManager(state_path)
                st.session_state.scheduler = None
                st.rerun()
        else:
            compte = get_compte_actif(config)
            st.caption(compte.get("nom", "divertissement45000"))

        st.divider()

        # Espace disque
        try:
            from modules.disk_monitor import get_espace_disque, get_taille_dossier_go
            total, utilise, libre = get_espace_disque()
            config = st.session_state.get("config", {})
            seuil = config.get("disque", {}).get("alerte_espace_go", 50)

            st.subheader("💾 Espace disque")
            pct_utilise = utilise / max(total, 1)
            couleur = "🔴" if libre < seuil else ("🟡" if libre < seuil * 2 else "🟢")
            st.metric(f"{couleur} Libre", f"{libre:.1f} Go")
            st.progress(pct_utilise)
            st.caption(f"{utilise:.1f} Go / {total:.1f} Go utilisés")

            # Espace utilisé par l'app
            _base = os.path.dirname(os.path.abspath(__file__))
            dossiers_app = [
                os.path.join(_base, "data/downloads"),
                os.path.join(_base, "data/clips"),
                os.path.join(_base, "data/processed"),
            ]
            taille_app = sum(get_taille_dossier_go(d) for d in dossiers_app)
            st.caption(f"App : {taille_app:.2f} Go")

        except Exception as e:
            st.warning(f"Impossible de lire l'espace disque : {e}")

        st.divider()

        # Statut pipeline
        en_cours = st.session_state.get("pipeline_en_cours", False)
        scheduler = st.session_state.get("scheduler")
        publication_active = scheduler and getattr(scheduler, "en_cours", False)

        st.subheader("📊 Statut")
        st.write(f"Pipeline : {'🔄 En cours' if en_cours else '⏸️ Inactif'}")
        st.write(f"Publication : {'📤 Active' if publication_active else '⏸️ Inactive'}")

        try:
            state = st.session_state.state_manager
            stats = state.get_statistiques()
            file_pub = state.get_file_publication()
            nb_attente = sum(1 for e in file_pub if e.get("statut") == "en_attente")
            nb_echec = sum(1 for e in file_pub if e.get("statut") == "echec_definitif")
            st.write(f"Importées : {stats.get('total_imports', 0)}")
            st.write(f"Clips générés : {stats.get('total_clips_generes', 0)}")
            st.write(f"Publiées : {stats.get('total_publies', 0)}")
            if nb_attente:
                st.write(f"⏳ En attente : {nb_attente}")
            if nb_echec:
                st.warning(f"❌ Échecs : {nb_echec}")
            # Prochain à publier
            prochain = state.get_prochain_a_publier()
            if prochain:
                nom_prochain = os.path.basename(prochain.get("chemin_clip", "?"))
                # Extraire le numéro de partie
                partie_info = ""
                if prochain.get("numero_partie"):
                    partie_info = f" (P{prochain['numero_partie']}/{prochain.get('total_parties', '?')})"
                st.caption(f"Prochain : ...{nom_prochain[-30:]}{partie_info}")
        except Exception:
            pass

        st.divider()

        # Vérification des prérequis
        st.subheader("🔧 Prérequis")
        try:
            from modules.pipeline import verifier_prerequis
            config = st.session_state.get("config", {})
            prerequis = verifier_prerequis(config)
            for p in prerequis:
                icone = {"ok": "✅", "warning": "⚠️", "error": "❌"}.get(p["niveau"], "ℹ️")
                st.caption(f"{icone} {p['message']}")
        except Exception as e:
            st.caption(f"⚠️ Vérification impossible : {e}")

        st.divider()
        if st.button("🔄 Actualiser", use_container_width=True):
            st.rerun()


def afficher_alerte_session():
    """Affiche une alerte si une session précédente est détectée."""
    state = st.session_state.get("state_manager")
    if state and state.a_session_precedente():
        derniere_maj = state.state.get("derniere_mise_a_jour", "")
        nb_videos = len(state.get_toutes_videos())

        st.info(
            f"🔄 **Session précédente détectée** — {nb_videos} vidéo(s) en cours de traitement. "
            f"Dernière mise à jour : {derniere_maj[:19] if derniere_maj else 'inconnue'}. "
            f"Le pipeline reprend automatiquement depuis le dernier état sauvegardé.",
            icon="ℹ️"
        )


DOMAINES_SUPPORTES = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com",
    "instagram.com", "www.instagram.com",
}


def valider_urls(texte: str):
    """Valide et nettoie les URLs saisies. Retourne (urls_valides, erreurs)."""
    urls_valides = []
    erreurs = []
    vues = set()

    for num, ligne in enumerate(texte.strip().split("\n"), 1):
        url = ligne.strip()
        if not url:
            continue

        if len(url) > 500:
            erreurs.append(f"Ligne {num} : URL trop longue ({len(url)} caractères)")
            continue

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            erreurs.append(f"Ligne {num} : '{url[:60]}' — utilisez http:// ou https://")
            continue

        domaine = parsed.hostname or ""
        if domaine not in DOMAINES_SUPPORTES:
            erreurs.append(f"Ligne {num} : '{domaine}' — plateforme non supportée (YouTube, TikTok, Instagram)")
            continue

        if url in vues:
            erreurs.append(f"Ligne {num} : URL en doublon ignorée")
            continue

        vues.add(url)
        urls_valides.append(url)

    return urls_valides, erreurs


# ─── ONGLET 1 : Import ────────────────────────────────────────────────────────

def onglet_import():
    """Onglet 1 : Téléchargement de vidéos depuis des URLs."""
    from modules.downloader import telecharger_video

    st.header("📥 Importer des vidéos")

    config = st.session_state.config
    state = st.session_state.state_manager

    col1, col2 = st.columns([2, 1])

    with col1:
        urls_texte = st.text_area(
            "Coller les liens (un par ligne) :",
            placeholder="https://www.youtube.com/watch?v=...\nhttps://www.tiktok.com/@.../video/...\nhttps://www.instagram.com/reels/.../",
            height=200,
            help="Supporte YouTube, TikTok et Instagram Reels"
        )

    with col2:
        st.subheader("Options")
        categorie_defaut = st.selectbox(
            "Catégorie par défaut",
            options=["auto", "musique", "sport", "humour", "autre"],
            format_func=lambda x: {
                "auto": "🤖 Auto-détection",
                "musique": "🎵 Musique",
                "sport": "⚽ Sport",
                "humour": "😂 Humour",
                "autre": "🎬 Autre"
            }.get(x, x),
            help="Auto-détection analyse le titre et la description"
        )

        lancer_traitement = st.checkbox(
            "Lancer le traitement automatiquement après téléchargement",
            value=True
        )

    # Bouton de lancement
    if st.button("⬇️ Télécharger tout", type="primary", use_container_width=True):
        urls, erreurs_url = valider_urls(urls_texte)
        for err in erreurs_url:
            st.warning(err)

        if not urls:
            st.error("Aucun lien valide trouvé. Plateformes supportées : YouTube, TikTok, Instagram.")
            return

        dossier_downloads = config.get("chemins", {}).get("downloads", "data/downloads")
        os.makedirs(dossier_downloads, exist_ok=True)
        qualite = config.get("telechargement", {}).get("qualite_preferee", "bestvideo+bestaudio/best")

        # Barre de progression globale (toutes les vidéos)
        st.write(f"**{len(urls)} lien(s) à télécharger**")
        prog_global = st.progress(0, text=f"0 / {len(urls)} vidéos traitées")

        resultats = []

        for i, url in enumerate(urls):
            prog_global.progress(i / len(urls), text=f"{i} / {len(urls)} vidéos traitées")

            st.markdown(f"**[{i+1}/{len(urls)}]** `{url[:70]}`")
            prog = st.progress(0.03, text="1/3 • Récupération des informations vidéo...")
            statut_texte = st.empty()

            def cb(msg, prog=prog, statut_texte=statut_texte):
                if not msg:
                    return
                if "[download]" in msg and "%" in msg:
                    m = re.search(r"(\d+\.?\d*)%", msg)
                    if m:
                        pct = float(m.group(1))
                        barre = 0.05 + (pct / 100) * 0.85
                        taille = ""
                        m2 = re.search(r"of\s+([\d.]+\s*\w+)", msg)
                        if m2:
                            taille = f" de {m2.group(1)}"
                        prog.progress(min(barre, 0.90), text=f"2/3 • Téléchargement {pct:.0f}%{taille}")
                elif "Assemblage" in msg or "Merger" in msg or "ffmpeg" in msg.lower():
                    prog.progress(0.92, text="2/3 • Assemblage audio + vidéo...")
                elif "Récupération" in msg:
                    prog.progress(0.04, text="1/3 • Récupération des informations vidéo...")

            chemin, metadata, erreur = telecharger_video(
                url=url,
                dossier_sortie=dossier_downloads,
                qualite=qualite,
                callback_progression=cb
            )

            if erreur:
                prog.progress(1.0, text="❌ Échec")
                statut_texte.error(f"❌ {erreur}")
                resultats.append({"url": url, "succes": False, "erreur": erreur})
            else:
                prog.progress(0.94, text="3/3 • Catégorisation automatique...")
                chemin_json = os.path.splitext(chemin)[0] + ".json"
                categorie_finale = categorie_defaut

                if categorie_defaut == "auto":
                    from modules.tagger import auto_tag
                    cat_suggeree, _ = auto_tag(
                        metadata.get("title", ""),
                        metadata.get("description", ""),
                        config
                    )
                    categorie_finale = cat_suggeree or "autre"

                if os.path.exists(chemin_json):
                    from modules.tagger import appliquer_categorie
                    appliquer_categorie(chemin_json, categorie_finale)

                video_id = str(uuid.uuid4())[:8] + "_" + os.path.splitext(os.path.basename(chemin))[0][:20]
                metadata["categorie"] = categorie_finale
                state.enregistrer_video(video_id, chemin, metadata)

                taille_mo = os.path.getsize(chemin) / (1024 ** 2)
                prog.progress(1.0, text=f"✅ Terminé — {taille_mo:.0f} Mo")
                titre_dl = (metadata.get("title") or "")[:45]
                statut_texte.success(f"✅ {titre_dl} — {categorie_finale.upper()}")

                resultats.append({
                    "url": url,
                    "succes": True,
                    "chemin": chemin,
                    "video_id": video_id,
                    "categorie": categorie_finale
                })

        nb_succes = sum(1 for r in resultats if r["succes"])
        nb_echecs = len(resultats) - nb_succes
        prog_global.progress(1.0, text=f"✅ {nb_succes} / {len(urls)} vidéos téléchargées")

        if nb_succes > 0:
            st.success(f"✅ Téléchargement terminé : {nb_succes}/{len(urls)} vidéos")
        if nb_echecs > 0:
            st.warning(f"⚠️ {nb_echecs} lien(s) en échec")

        # Lancer la découpe automatique si demandé
        if lancer_traitement and nb_succes > 0:
            video_ids_a_traiter = [r["video_id"] for r in resultats if r.get("succes")]
            st.info("🔄 Découpe automatique des vidéos...")
            lancer_decoupe(video_ids_a_traiter)

    # ── Progression du pipeline de traitement ──────────────────────────────────
    if st.session_state.get("pipeline_en_cours") or (
        _pipeline_progress_global.get("etape_nom", "") not in ("", "Démarrage...", "✅ Terminé")
        and _pipeline_progress_global.get("pct", 0) > 0
    ):
        p = _pipeline_progress_global
        st.divider()
        st.subheader("⚙️ Traitement en cours")

        ETAPES_LISTE = [
            "Conversion portrait 9:16",
            "Ajout du filigrane",
            "Génération des sous-titres",
            "Ajout de la musique de fond",
            "Intro / Outro",
            "Description & hashtags",
        ]
        etape_courante = p.get("etape_num", 0)
        pct = p.get("pct", 0)
        nom_etape = p.get("etape_nom", "...")

        st.progress(pct, text=f"**Étape {etape_courante}/6** — {nom_etape}  ({int(pct*100)}%)")

        # Afficher les étapes comme une checklist
        cols = st.columns(3)
        for idx, nom in enumerate(ETAPES_LISTE):
            num = idx + 1
            col = cols[idx % 3]
            if num < etape_courante:
                col.success(f"✅ {num}. {nom}")
            elif num == etape_courante:
                col.info(f"🔄 {num}. {nom}")
            else:
                col.caption(f"⬜ {num}. {nom}")

        if st.session_state.get("pipeline_en_cours"):
            if st.button("🔄 Actualiser la progression"):
                st.rerun()
        else:
            st.success("✅ Traitement terminé !")

    # ── Import depuis dossier local (data/imports/) ─────────────────────
    st.divider()
    st.subheader("📁 Importer depuis le dossier local")
    st.caption("Place tes fichiers MP4 dans le dossier **`data/imports/`** puis clique sur Importer.")

    dossier_imports = os.path.join(
        config.get("chemins", {}).get("base", "data"), "imports"
    )
    os.makedirs(dossier_imports, exist_ok=True)

    # Scanner les MP4 dans le dossier
    mp4_dans_dossier = sorted([
        f for f in os.listdir(dossier_imports)
        if f.lower().endswith(".mp4") and os.path.isfile(os.path.join(dossier_imports, f))
    ])

    if mp4_dans_dossier:
        st.info(f"**{len(mp4_dans_dossier)}** fichier(s) MP4 trouvé(s) dans `data/imports/`")

        # Afficher la liste
        for nom in mp4_dans_dossier:
            taille = os.path.getsize(os.path.join(dossier_imports, nom)) / (1024 ** 2)
            st.write(f"- **{nom}** ({taille:.1f} Mo)")

        col_imp_a, col_imp_b = st.columns([2, 1])
        with col_imp_a:
            titre_dossier = st.text_input(
                "Titre commun (optionnel)",
                placeholder="Ex: Compilation humour",
                key="titre_dossier_import"
            )
        with col_imp_b:
            categorie_dossier = st.selectbox(
                "Catégorie",
                options=["humour", "musique", "sport", "autre"],
                format_func=lambda x: {
                    "humour": "😂 Humour", "musique": "🎵 Musique",
                    "sport": "⚽ Sport", "autre": "🎬 Autre",
                }.get(x, x),
                key="cat_dossier_import"
            )
            traiter_auto_dossier = st.checkbox("Traiter automatiquement", value=True, key="traiter_dossier")

        pipeline_actif_dossier = st.session_state.get("pipeline_en_cours", False)
        if pipeline_actif_dossier:
            st.warning("⚠️ Un traitement est déjà en cours.")

        if st.button(
            f"⬆️ Importer {len(mp4_dans_dossier)} fichier(s) depuis le dossier",
            type="primary", use_container_width=True, disabled=pipeline_actif_dossier
        ):
            dossier_dl = config.get("chemins", {}).get("downloads", "data/downloads")
            os.makedirs(dossier_dl, exist_ok=True)
            video_ids_dossier = []

            for nom_mp4 in mp4_dans_dossier:
                chemin_src = os.path.join(dossier_imports, nom_mp4)
                nom_base = re.sub(r"[^\w\s-]", "", os.path.splitext(nom_mp4)[0], flags=re.UNICODE)
                nom_base = re.sub(r"\s+", "_", nom_base.strip())[:50]
                date_str = datetime.now().strftime("%Y%m%d")
                nom_dest = f"{date_str}_local_{nom_base}.mp4"
                chemin_dest = os.path.join(dossier_dl, nom_dest)

                # Déplacer (pas copier) vers downloads
                import shutil
                shutil.move(chemin_src, chemin_dest)
                taille_mo = os.path.getsize(chemin_dest) / (1024 ** 2)

                # Durée via ffprobe
                duree = 0
                try:
                    import subprocess as _sp
                    _res = _sp.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", chemin_dest],
                        capture_output=True, text=True, timeout=10
                    )
                    _info = json.loads(_res.stdout)
                    duree = float(_info.get("format", {}).get("duration", 0))
                except Exception:
                    pass

                titre_final = titre_dossier.strip() or os.path.splitext(nom_mp4)[0]
                metadata = {
                    "url": "",
                    "platform": "local",
                    "title": titre_final if len(mp4_dans_dossier) == 1 else f"{titre_final} — {nom_base}",
                    "description": titre_final,
                    "duration": duree,
                    "uploader": "local",
                    "upload_date": date_str,
                    "categorie": categorie_dossier,
                }

                chemin_json = os.path.splitext(chemin_dest)[0] + ".json"
                with open(chemin_json, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)

                video_id = str(uuid.uuid4())[:8] + "_" + nom_base[:20]
                state.enregistrer_video(video_id, chemin_dest, metadata)
                state.enregistrer_categorie(video_id, categorie_dossier)
                video_ids_dossier.append(video_id)

                st.success(f"✅ {nom_mp4} — {taille_mo:.0f} Mo — {int(duree//60)}:{int(duree%60):02d}")

            if traiter_auto_dossier and video_ids_dossier:
                st.info("🔄 Lancement du traitement automatique...")
                lancer_pipeline(video_ids_dossier)

    else:
        st.write("Aucun fichier MP4 dans `data/imports/`. Place-y tes vidéos pour les importer.")

    # ── Import depuis fichier MP4 local (upload navigateur) ───────────────
    st.divider()
    st.subheader("📂 Importer un fichier MP4 local")

    fichiers_uploades = st.file_uploader(
        "Glisser-déposer des fichiers MP4",
        type=["mp4"],
        accept_multiple_files=True,
        help="Importe directement des vidéos MP4 sans passer par une URL"
    )

    if fichiers_uploades:
        col_mp4_a, col_mp4_b = st.columns([2, 1])
        with col_mp4_a:
            titre_mp4 = st.text_input(
                "Titre (optionnel — utilisé pour les descriptions et hashtags)",
                placeholder="Ex: ON ACCOSTE DES GENS EN VOITURE"
            )
        with col_mp4_b:
            categorie_mp4 = st.selectbox(
                "Catégorie",
                options=["humour", "musique", "sport", "autre"],
                format_func=lambda x: {
                    "humour": "😂 Humour",
                    "musique": "🎵 Musique",
                    "sport": "⚽ Sport",
                    "autre": "🎬 Autre",
                }.get(x, x),
                key="cat_mp4"
            )
            lancer_apres_mp4 = st.checkbox("Traiter automatiquement", value=True, key="lancer_mp4")

        pipeline_actif = st.session_state.get("pipeline_en_cours", False)
        if pipeline_actif:
            st.warning("⚠️ Un traitement est déjà en cours — attends qu'il se termine avant d'importer.")

        if st.button(f"⬆️ Importer {len(fichiers_uploades)} fichier(s)", type="primary", use_container_width=True, disabled=pipeline_actif):
            dossier_dl = config.get("chemins", {}).get("downloads", "data/downloads")
            os.makedirs(dossier_dl, exist_ok=True)
            video_ids_mp4 = []

            for fichier in fichiers_uploades:
                # Nom de fichier propre
                nom_base = re.sub(r"[^\w\s-]", "", os.path.splitext(fichier.name)[0], flags=re.UNICODE)
                nom_base = re.sub(r"\s+", "_", nom_base.strip())[:50]
                date_str = datetime.now().strftime("%Y%m%d")
                nom_fichier = f"{date_str}_local_{nom_base}.mp4"
                chemin_mp4 = os.path.join(dossier_dl, nom_fichier)

                # Sauvegarder le fichier sur disque
                with open(chemin_mp4, "wb") as f:
                    f.write(fichier.read())
                taille_mo = os.path.getsize(chemin_mp4) / (1024 ** 2)

                # Durée via ffprobe
                duree = 0
                try:
                    import subprocess as _sp
                    _res = _sp.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", chemin_mp4],
                        capture_output=True, text=True, timeout=10
                    )
                    _info = json.loads(_res.stdout)
                    duree = float(_info.get("format", {}).get("duration", 0))
                except Exception:
                    pass

                titre_final = titre_mp4.strip() or os.path.splitext(fichier.name)[0]

                metadata = {
                    "url": "",
                    "platform": "local",
                    "title": titre_final,
                    "description": titre_final,
                    "duration": duree,
                    "uploader": "local",
                    "upload_date": date_str,
                    "categorie": categorie_mp4,
                }

                # Sauvegarder le JSON de métadonnées
                chemin_json = os.path.splitext(chemin_mp4)[0] + ".json"
                with open(chemin_json, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)

                # Enregistrer dans le state
                video_id = str(uuid.uuid4())[:8] + "_" + nom_base[:20]
                metadata["categorie"] = categorie_mp4
                state.enregistrer_video(video_id, chemin_mp4, metadata)
                state.enregistrer_categorie(video_id, categorie_mp4)
                video_ids_mp4.append(video_id)

                st.success(f"✅ {fichier.name} — {taille_mo:.0f} Mo — {int(duree//60)}:{int(duree%60):02d}")

            if lancer_apres_mp4 and video_ids_mp4:
                st.info("🔄 Lancement du traitement automatique...")
                lancer_pipeline(video_ids_mp4)

    # Tableau des vidéos existantes
    st.divider()
    st.subheader("📁 Vidéos importées")

    toutes_videos = state.get_toutes_videos()
    if not toutes_videos:
        st.info("Aucune vidéo importée pour le moment.")
        return

    for video_id, video_data in list(toutes_videos.items())[:20]:
        with st.expander(
            f"{'✅' if video_data.get('etape') == 'pret' else '🔄'} "
            f"{video_data.get('metadata', {}).get('title', video_id)[:60]} "
            f"— {(video_data.get('categorie') or '?').upper()}"
        ):
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.write(f"**Étape :** {video_data.get('etape', 'inconnu')}")
                st.write(f"**Catégorie :** {video_data.get('categorie', 'autre')}")
            with col_b:
                duree = video_data.get("metadata", {}).get("duration", 0)
                st.write(f"**Durée :** {int(duree // 60)}:{int(duree % 60):02d}")
                st.write(f"**Clips :** {len(video_data.get('clips', []))}")
            with col_c:
                plateforme = video_data.get("metadata", {}).get("platform", "?")
                st.write(f"**Source :** {plateforme}")

            # Sélecteur de catégorie manuel
            nouvelle_cat = st.selectbox(
                "Modifier la catégorie",
                options=["musique", "sport", "humour", "autre"],
                index=["musique", "sport", "humour", "autre"].index(
                    video_data.get("categorie") or "autre"
                ),
                key=f"cat_{video_id}"
            )
            if nouvelle_cat != video_data.get("categorie"):
                if st.button(f"Appliquer '{nouvelle_cat}'", key=f"btn_cat_{video_id}"):
                    state.enregistrer_categorie(video_id, nouvelle_cat)
                    chemin_json = os.path.splitext(video_data.get("chemin", ""))[0] + ".json"
                    from modules.tagger import appliquer_categorie
                    appliquer_categorie(chemin_json, nouvelle_cat)
                    st.success("Catégorie mise à jour")
                    st.rerun()

            col_x, col_y = st.columns(2)
            with col_x:
                if st.button("▶️ Traiter maintenant", key=f"traiter_{video_id}"):
                    lancer_pipeline([video_id])
            with col_y:
                erreurs = video_data.get("erreurs", [])
                if erreurs:
                    with st.expander(f"❌ {len(erreurs)} erreur(s)"):
                        for err in erreurs[-3:]:
                            ts = err.get("timestamp", "")[:19]
                            msg = err.get("message", "Erreur inconnue")
                            st.error(f"**{ts}** — {msg}")


def lancer_decoupe(video_ids: List[str]):
    """Lance uniquement la phase de découpe (analyse + extraction clips) dans un thread."""
    config = st.session_state.config
    state = st.session_state.state_manager

    if st.session_state.get("pipeline_en_cours"):
        st.warning("Un traitement est déjà en cours.")
        return

    st.session_state.pipeline_en_cours = True

    _pipeline_progress_global.update({
        "etape_nom": "Analyse et découpe...",
        "etape_num": 0,
        "etape_total": 1,
        "pct": 0.05,
    })

    def run():
        try:
            from modules.pipeline import decouper_video
            for i, vid_id in enumerate(video_ids):
                _pipeline_progress_global["pct"] = round((i + 1) / len(video_ids) * 0.9, 2)
                _pipeline_progress_global["etape_nom"] = f"Découpe vidéo {i+1}/{len(video_ids)}"
                decouper_video(vid_id, config, state, lambda msg: logger.info(msg))
        except Exception as e:
            logger.exception("Erreur découpe")
        finally:
            st.session_state.pipeline_en_cours = False
            _pipeline_progress_global["etape_nom"] = "✅ Découpe terminée — validez les clips dans l'onglet Preview"
            _pipeline_progress_global["pct"] = 1.0

    thread = threading.Thread(target=run, daemon=True, name="DecoupeThread")
    thread.start()
    st.info("✂️ Découpe lancée. Les clips bruts apparaîtront dans l'onglet Preview pour validation.")


def lancer_pipeline(video_ids: List[str]):
    """Lance le pipeline de traitement complet dans un thread séparé."""
    config = st.session_state.config
    state = st.session_state.state_manager

    if st.session_state.pipeline_en_cours:
        st.warning("Un traitement est déjà en cours.")
        return

    st.session_state.pipeline_en_cours = True
    st.session_state.logs_pipeline = []

    # Réinitialiser le dict global (thread-safe, pas de ScriptRunContext)
    _pipeline_progress_global.update({
        "etape_nom": "Démarrage...",
        "etape_num": 0,
        "etape_total": 6,
        "pct": 0.0,
        "debut_traitement": datetime.now().isoformat(),
        "clip_courant": 0,
        "total_clips": 0,
        "temps_par_clip": [],
        "eta_secondes": None,
        "debut_clip_courant": None,
    })

    ETAPES = {
        "portrait":    (1, "Conversion portrait 9:16"),
        "watermark":   (2, "Ajout du filigrane"),
        "subtitles":   (3, "Génération des sous-titres"),
        "music":       (4, "Ajout de la musique de fond"),
        "final":       (5, "Intro / Outro"),
        "description": (6, "Description & hashtags"),
    }

    def callback_pipeline(msg: str):
        # Logger Python uniquement depuis le thread (pas st.session_state)
        logger.info(msg)

        # ── ETA : détecter début/fin de clip ──
        m_clip = re.search(r"Clip (\d+)/(\d+)", msg)
        if m_clip:
            n, total = int(m_clip.group(1)), int(m_clip.group(2))
            maintenant = datetime.now().isoformat()
            # Si on commence un nouveau clip, enregistrer la durée du précédent
            debut_prec = _pipeline_progress_global.get("debut_clip_courant")
            if debut_prec and n > _pipeline_progress_global.get("clip_courant", 0):
                try:
                    duree = (datetime.fromisoformat(maintenant) - datetime.fromisoformat(debut_prec)).total_seconds()
                    if duree > 0:
                        _pipeline_progress_global["temps_par_clip"].append(duree)
                except (ValueError, TypeError):
                    pass
            _pipeline_progress_global["clip_courant"] = n
            _pipeline_progress_global["total_clips"] = total
            _pipeline_progress_global["debut_clip_courant"] = maintenant
            # Recalculer ETA
            temps_list = _pipeline_progress_global.get("temps_par_clip", [])
            if temps_list:
                moyenne = sum(temps_list) / len(temps_list)
                restants = total - n
                _pipeline_progress_global["eta_secondes"] = moyenne * restants

            _pipeline_progress_global["etape_nom"] = f"Analyse — Clip {n}/{total}"
            _pipeline_progress_global["etape_num"] = 0
            _pipeline_progress_global["pct"] = round((n / max(total, 1)) * 0.12, 3)
            return

        # Clip terminé → enregistrer sa durée
        if "clip prêt" in msg.lower() or "clip pret" in msg.lower():
            debut_prec = _pipeline_progress_global.get("debut_clip_courant")
            if debut_prec:
                try:
                    duree = (datetime.now() - datetime.fromisoformat(debut_prec)).total_seconds()
                    if duree > 0:
                        _pipeline_progress_global["temps_par_clip"].append(duree)
                        total = _pipeline_progress_global.get("total_clips", 0)
                        courant = _pipeline_progress_global.get("clip_courant", 0)
                        restants = max(0, total - courant)
                        moyenne = sum(_pipeline_progress_global["temps_par_clip"]) / len(_pipeline_progress_global["temps_par_clip"])
                        _pipeline_progress_global["eta_secondes"] = moyenne * restants
                except (ValueError, TypeError):
                    pass
                _pipeline_progress_global["debut_clip_courant"] = None

        if any(k in msg.lower() for k in ("analyse", "découpe", "sélection des meilleurs", "analyse audio", "analyse vidéo")):
            _pipeline_progress_global["etape_nom"] = "Analyse et découpe de la vidéo..."
            _pipeline_progress_global["etape_num"] = 0
            _pipeline_progress_global["pct"] = 0.03
            return
        # Phase traitement des clips (étapes 1-6)
        for cle, (num, nom) in ETAPES.items():
            if cle in msg.lower() or nom.lower() in msg.lower():
                _pipeline_progress_global["etape_num"] = num
                _pipeline_progress_global["etape_nom"] = nom
                _pipeline_progress_global["pct"] = round(num / 6, 2)
                break

    def run():
        try:
            from modules.pipeline import traiter_batch_videos
            traiter_batch_videos(video_ids, config, state, callback_pipeline)
        except Exception as e:
            logger.exception("Erreur pipeline")
            ajouter_log(f"❌ Erreur critique : {e}")
        finally:
            st.session_state.pipeline_en_cours = False
            _pipeline_progress_global["etape_nom"] = "✅ Terminé"
            _pipeline_progress_global["pct"] = 1.0

    thread = threading.Thread(target=run, daemon=True, name="PipelineThread")
    thread.start()
    st.info("🔄 Traitement lancé en arrière-plan. Consultez l'onglet Preview pour suivre l'avancement.")


# ─── ONGLET 2 : Preview & Validation ──────────────────────────────────────────

def onglet_preview():
    """Onglet 2 : Prévisualisation et validation des clips générés."""
    st.header("👁️ Preview & Validation")

    state = st.session_state.state_manager

    # ── Progression du pipeline ────────────────────────────────────────────────
    if st.session_state.get("pipeline_en_cours"):
        p = _pipeline_progress_global
        etape_num = p.get("etape_num", 0)
        etape_nom = p.get("etape_nom", "Démarrage...")
        pct = p.get("pct", 0.0)

        st.subheader("⚙️ Traitement en cours")
        st.progress(pct, text=f"**Étape {etape_num}/6** — {etape_nom}  ({int(pct * 100)}%)")

        # Affichage ETA
        eta = p.get("eta_secondes")
        clip_c = p.get("clip_courant", 0)
        clip_t = p.get("total_clips", 0)
        if eta and eta > 0:
            if eta > 3600:
                eta_str = f"{int(eta // 3600)}h {int((eta % 3600) // 60)}min"
            elif eta > 60:
                eta_str = f"{int(eta // 60)}min {int(eta % 60)}s"
            else:
                eta_str = f"{int(eta)}s"
            st.caption(f"Clip {clip_c}/{clip_t} — Temps restant estimé : ~{eta_str}")
        elif clip_t > 0:
            st.caption(f"Clip {clip_c}/{clip_t} — Calcul du temps restant...")

        ETAPES_LISTE = [
            "Conversion portrait 9:16",
            "Ajout du filigrane",
            "Génération des sous-titres",
            "Ajout de la musique de fond",
            "Intro / Outro",
            "Description & hashtags",
        ]
        cols = st.columns(3)
        for idx, nom in enumerate(ETAPES_LISTE):
            num = idx + 1
            col = cols[idx % 3]
            if num < etape_num:
                col.success(f"✅ {num}. {nom}")
            elif num == etape_num:
                col.info(f"🔄 {num}. {nom}")
            else:
                col.caption(f"⬜ {num}. {nom}")

        # Statut par vidéo
        toutes = state.get_toutes_videos()
        if toutes:
            st.divider()
            st.caption("**Statut des vidéos en traitement :**")
            ICONES = {
                "telecharge": "📥", "analyse": "🔍", "decoupé": "✂️",
                "portrait": "📐", "watermark": "💧", "subtitles": "💬",
                "music": "🎵", "final": "🎬", "pret": "✅", "erreur": "❌",
            }
            for vid_id, vid_data in list(toutes.items())[:10]:
                etape_vid = vid_data.get("etape", "?")
                titre_vid = vid_data.get("metadata", {}).get("title", vid_id)[:40]
                nb_clips = len(vid_data.get("clips", []))
                icone = ICONES.get(etape_vid, "🔄")
                st.caption(f"{icone} **{titre_vid}** — {etape_vid}  ({nb_clips} clips)")

        col_refresh, col_stop = st.columns([1, 3])
        with col_refresh:
            if st.button("🔄 Actualiser", use_container_width=True):
                st.rerun()
        st.divider()

    # ── Aperçu des clips bruts (avant traitement complet) ──────────────────
    toutes_videos = state.get_toutes_videos()
    clips_bruts_preview = []
    for vid_id, vid_data in toutes_videos.items():
        if vid_data.get("etape") == "decoupé":
            for clip in vid_data.get("clips", []):
                if clip.get("etape") == "decoupé" and clip.get("statut_validation") != "rejeté":
                    chemin_clip = clip.get("chemin", "")
                    if chemin_clip and os.path.exists(chemin_clip):
                        clips_bruts_preview.append({
                            **clip,
                            "video_id": vid_id,
                            "video_titre": vid_data.get("metadata", {}).get("title", "Sans titre")[:50],
                        })

    if clips_bruts_preview:
        st.subheader("✂️ Clips bruts — Aperçu avant traitement")
        st.caption(f"{len(clips_bruts_preview)} clip(s) en attente de validation")

        col_app_all, col_trait = st.columns(2)
        clips_bruts_en_attente = [c for c in clips_bruts_preview if c.get("statut_validation") != "approuvé"]
        clips_bruts_approuves = [c for c in clips_bruts_preview if c.get("statut_validation") == "approuvé"]

        with col_app_all:
            if clips_bruts_en_attente and st.button(f"✅ Approuver tout ({len(clips_bruts_en_attente)} clips bruts)", key="approuver_bruts"):
                for clip in clips_bruts_en_attente:
                    state.mettre_a_jour_clip(clip["video_id"], clip["id"], {"statut_validation": "approuvé"})
                st.success(f"{len(clips_bruts_en_attente)} clips approuvés")
                st.rerun()

        with col_trait:
            if clips_bruts_approuves and st.button(f"▶️ Traiter les {len(clips_bruts_approuves)} clips approuvés", type="primary", key="traiter_approuves"):
                video_ids_a_traiter = list({c["video_id"] for c in clips_bruts_approuves})
                from modules.pipeline import traiter_clips_approuves as _traiter_approuves
                config = st.session_state.config
                for vid_id in video_ids_a_traiter:
                    _traiter_approuves(vid_id, config, state)
                st.success("Traitement terminé !")
                st.rerun()

        nb_col = 2
        for i in range(0, len(clips_bruts_preview), nb_col):
            cols = st.columns(nb_col)
            for j, col in enumerate(cols):
                idx = i + j
                if idx >= len(clips_bruts_preview):
                    break
                clip = clips_bruts_preview[idx]
                with col:
                    st.markdown(f"**{clip.get('video_titre', 'Clip')}**")
                    st.caption(f"ID: {clip['id']} | {clip.get('duree', 0):.0f}s | Score: {clip.get('score', 0):.2f}")
                    try:
                        with open(clip["chemin"], "rb") as f:
                            st.video(f.read())
                    except Exception:
                        st.warning("Impossible d'afficher l'aperçu")
                    statut_val = clip.get("statut_validation", "en_attente")
                    if statut_val != "approuvé":
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("✅", key=f"ok_brut_{clip['id']}"):
                                state.mettre_a_jour_clip(clip["video_id"], clip["id"], {"statut_validation": "approuvé"})
                                st.rerun()
                        with c2:
                            if st.button("❌", key=f"ko_brut_{clip['id']}"):
                                state.mettre_a_jour_clip(clip["video_id"], clip["id"], {"statut_validation": "rejeté"})
                                st.rerun()
                    else:
                        st.success("Approuvé")
                    st.divider()
        st.divider()

    # Récupérer tous les clips prêts (dédupliqués par id)
    clips_a_valider = []
    _ids_vus = set()

    for video_id, video_data in toutes_videos.items():
        for clip in video_data.get("clips", []):
            clip_id = clip.get("id")
            if not clip_id or clip_id in _ids_vus:
                continue
            if clip.get("etape") == "pret" and clip.get("chemin_final"):
                _ids_vus.add(clip_id)
                clips_a_valider.append({
                    **clip,
                    "video_titre": video_data.get("metadata", {}).get("title", "Sans titre")[:50],
                    "video_id": video_id
                })

    if not clips_a_valider:
        st.info("Aucun clip prêt pour validation. Importez et traitez des vidéos d'abord.")
        return

    clips_en_attente = [c for c in clips_a_valider if c.get("statut_validation") == "en_attente"]
    clips_approuves = [c for c in clips_a_valider if c.get("statut_validation") == "approuvé"]
    clips_rejetes = [c for c in clips_a_valider if c.get("statut_validation") == "rejeté"]

    st.metric("Total clips", len(clips_a_valider), f"{len(clips_en_attente)} en attente")

    col_a, col_b = st.columns(2)
    with col_a:
        st.success(f"✅ {len(clips_approuves)} approuvé(s)")
    with col_b:
        st.error(f"❌ {len(clips_rejetes)} rejeté(s)")

    # Bouton "Approuver tout"
    if clips_en_attente:
        if st.button(f"✅ Approuver tout ({len(clips_en_attente)} clips)", type="primary"):
            for clip in clips_en_attente:
                state.mettre_a_jour_clip(clip["video_id"], clip["id"], {"statut_validation": "approuvé"})
                intervalle_pub = config.get("publication", {}).get("intervalle_minutes", 5)
                state.ajouter_a_file_publication(clip["id"], clip, intervalle_minutes=intervalle_pub)
            st.success(f"{len(clips_en_attente)} clips approuvés et ajoutés à la file de publication")
            st.rerun()

    st.divider()

    # Grille des clips
    if clips_en_attente:
        st.subheader("⏳ En attente de validation")
        afficher_grille_clips(clips_en_attente, state, "attente")

    if clips_approuves:
        st.subheader("✅ Approuvés")
        afficher_grille_clips(clips_approuves, state, "approuve")

    if clips_rejetes:
        with st.expander(f"❌ Rejetés ({len(clips_rejetes)})"):
            afficher_grille_clips(clips_rejetes, state, "rejete")


def afficher_grille_clips(clips: List[Dict], state, suffixe: str):
    """Affiche une grille de clips avec lecteur vidéo et actions."""
    nb_colonnes = 2
    for i in range(0, len(clips), nb_colonnes):
        cols = st.columns(nb_colonnes)
        for j, col in enumerate(cols):
            idx = i + j
            if idx >= len(clips):
                break

            clip = clips[idx]
            chemin_final = clip.get("chemin_final", "")

            with col:
                st.markdown(f"**{clip.get('video_titre', 'Clip')}**")
                st.caption(f"ID: {clip['id']} | {clip.get('duree', 0):.0f}s | {clip.get('categorie', '?')}")

                # Score viral prédictif
                try:
                    from modules.viral_predictor import ViralPredictor
                    predictor = ViralPredictor(state)
                    if predictor.est_entraine():
                        score_viral = predictor.predire(clip, {})
                        if score_viral is not None:
                            if score_viral >= 70:
                                st.success(f"🔥 Score viral : {score_viral}/100")
                            elif score_viral >= 40:
                                st.info(f"📊 Score viral : {score_viral}/100")
                            else:
                                st.caption(f"📊 Score viral : {score_viral}/100")
                except Exception:
                    pass

                # Lecteur vidéo
                if chemin_final and os.path.exists(chemin_final):
                    try:
                        with open(chemin_final, "rb") as f:
                            video_bytes = f.read()
                        st.video(video_bytes)
                        # Bouton de téléchargement
                        taille_mo = os.path.getsize(chemin_final) / (1024 ** 2)
                        if taille_mo < 200:
                            st.download_button(
                                label=f"Télécharger ({taille_mo:.1f} Mo)",
                                data=video_bytes,
                                file_name=os.path.basename(chemin_final),
                                mime="video/mp4",
                                key=f"dl_{clip['id']}_{suffixe}"
                            )
                        else:
                            st.caption(f"Fichier volumineux ({taille_mo:.0f} Mo) : `{chemin_final}`")
                    except Exception as e:
                        st.warning(f"Impossible d'afficher la vidéo : {e}")
                else:
                    st.warning("Fichier vidéo introuvable")

                # Description et hashtags (modifiables)
                description = st.text_area(
                    "Description",
                    value=clip.get("description", ""),
                    height=80,
                    key=f"desc_{clip['id']}_{suffixe}"
                )
                hashtags_str = st.text_input(
                    "Hashtags (séparés par des espaces)",
                    value=" ".join(clip.get("hashtags", [])),
                    key=f"hash_{clip['id']}_{suffixe}"
                )

                # Sauvegarder les modifications
                if description != clip.get("description") or hashtags_str != " ".join(clip.get("hashtags", [])):
                    hashtags_liste = [h.strip() for h in hashtags_str.split() if h.strip()]
                    state.mettre_a_jour_clip(
                        clip["video_id"], clip["id"],
                        {"description": description, "hashtags": hashtags_liste}
                    )

                # Boutons Approuver / Rejeter
                if clip.get("statut_validation") == "en_attente":
                    col_btn1, col_btn2 = st.columns(2)
                    with col_btn1:
                        if st.button("✅ Approuver", key=f"ok_{clip['id']}_{suffixe}", use_container_width=True):
                            # Mettre à jour description/hashtags avant approbation
                            hashtags_liste = [h.strip() for h in hashtags_str.split() if h.strip()]
                            clip["description"] = description
                            clip["hashtags"] = hashtags_liste
                            state.mettre_a_jour_clip(
                                clip["video_id"], clip["id"],
                                {"statut_validation": "approuvé", "description": description, "hashtags": hashtags_liste}
                            )
                            intervalle_pub = config.get("publication", {}).get("intervalle_minutes", 5)
                            state.ajouter_a_file_publication(clip["id"], clip, intervalle_minutes=intervalle_pub)
                            st.success("Approuvé !")
                            st.rerun()
                    with col_btn2:
                        if st.button("❌ Rejeter", key=f"ko_{clip['id']}_{suffixe}", use_container_width=True):
                            state.mettre_a_jour_clip(
                                clip["video_id"], clip["id"],
                                {"statut_validation": "rejeté"}
                            )
                            st.warning("Rejeté")
                            st.rerun()

                st.divider()


# ─── ONGLET 3 : Publication ────────────────────────────────────────────────────

def onglet_publication():
    """Onglet 3 : File de publication et scheduling."""
    st.header("📤 Publication TikTok")

    config = st.session_state.config
    state = st.session_state.state_manager

    # Initialiser le scheduler
    if st.session_state.scheduler is None:
        from modules.publisher import PublicationScheduler
        st.session_state.scheduler = PublicationScheduler(config, state, ajouter_log)

    scheduler = st.session_state.scheduler
    file = state.get_file_publication()
    clips_en_attente = [e for e in file if e.get("statut") == "en_attente"]
    clips_publies = [e for e in file if e.get("statut") == "succes"]
    clips_echec = [e for e in file if e.get("statut") in ("echec_definitif",)]
    clips_en_cours = [e for e in file if e.get("statut") == "en_cours"]

    # Statut de la publication
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("En attente", len(clips_en_attente))
    with col2:
        st.metric("En cours", len(clips_en_cours))
    with col3:
        st.metric("Publiés", len(clips_publies))
    with col4:
        st.metric("Échecs", len(clips_echec))

    # Statut anti-ban
    if hasattr(scheduler, 'anti_ban'):
        ab_statut = scheduler.anti_ban.get_statut()
        if ab_statut["actif"]:
            col_ab1, col_ab2, col_ab3 = st.columns(3)
            with col_ab1:
                pct_use = ab_statut["publications_aujourdhui"] / max(ab_statut["limite"], 1) * 100
                couleur = "🟢" if pct_use < 70 else ("🟡" if pct_use < 90 else "🔴")
                st.metric(
                    f"{couleur} Quota quotidien",
                    f"{ab_statut['publications_aujourdhui']}/{ab_statut['limite']}",
                    delta=f"{ab_statut['restant']} restant(s)"
                )
            with col_ab2:
                fen_icon = "🟢" if ab_statut["dans_fenetre"] else "🔴"
                st.metric(f"{fen_icon} Fenêtre", ab_statut["fenetre"])
            with col_ab3:
                if ab_statut["cooldown_actif"]:
                    st.warning(f"⏸️ Cooldown — {ab_statut['echecs_consecutifs']} échecs consécutifs")
                    if st.button("🔄 Reset cooldown", key="reset_cooldown"):
                        scheduler.anti_ban.reset_cooldown()
                        st.rerun()
                elif not ab_statut["peut_publier"]:
                    st.warning(f"🛡️ {ab_statut['raison_blocage']}")
                else:
                    st.success("🛡️ Publication autorisée")

    # Contrôles de publication
    st.subheader("Contrôles")
    col_ctrl1, col_ctrl2, col_ctrl3 = st.columns(3)

    with col_ctrl1:
        if not scheduler.en_cours:
            if st.button(
                "🚀 Lancer la publication automatique",
                type="primary",
                disabled=len(clips_en_attente) == 0,
                use_container_width=True
            ):
                scheduler.demarrer()
                st.success(f"Publication démarrée — 1 clip toutes les {scheduler.intervalle_minutes} min")
                st.rerun()
        else:
            if st.button("⏹️ Arrêter la publication", type="secondary", use_container_width=True):
                scheduler.arreter()
                st.info("Publication arrêtée")
                st.rerun()

    with col_ctrl2:
        if st.button("📤 Publier le prochain maintenant", use_container_width=True, disabled=len(clips_en_attente) == 0):
            with st.spinner("Publication en cours..."):
                resultat = scheduler.publier_prochain()
            if resultat["statut"] == "succes":
                st.success(f"✅ Clip publié avec succès !")
            else:
                st.error(f"❌ Échec : {resultat.get('message', '')}")
            st.rerun()

    with col_ctrl3:
        cfg_pub = config.get("publication", {})
        mode_sched = cfg_pub.get("mode_scheduling", "fixe")
        mode_label = "intelligent" if mode_sched == "intelligent" else "fixe"
        mode_choix = st.radio(
            "Mode scheduling",
            ["Intelligent", "Fixe"],
            index=0 if mode_sched == "intelligent" else 1,
            horizontal=True,
            key="mode_sched_radio"
        )
        if mode_choix == "Intelligent":
            creneaux_str = st.text_input(
                "Créneaux (HH:MM séparés par virgule)",
                value=", ".join(cfg_pub.get("creneaux_optimaux", ["12:00", "18:00", "21:00"])),
                key="creneaux_input"
            )
            if st.button("Appliquer les créneaux", use_container_width=True, key="apply_creneaux"):
                creneaux_list = [c.strip() for c in creneaux_str.split(",") if c.strip()]
                config["publication"]["mode_scheduling"] = "intelligent"
                config["publication"]["creneaux_optimaux"] = creneaux_list
                sauvegarder_config(config)
                scheduler.recalculer_horaires()
                st.success(f"Mode intelligent activé : {', '.join(creneaux_list)}")
                st.rerun()
        else:
            intervalle_nouveau = st.number_input(
                "Intervalle (minutes)",
                min_value=0.5, max_value=60.0, step=0.5,
                value=float(scheduler.intervalle_minutes),
                key="intervalle_fixe"
            )
            if st.button("Appliquer l'intervalle", use_container_width=True, key="apply_intervalle"):
                config["publication"]["mode_scheduling"] = "fixe"
                config["publication"]["intervalle_minutes"] = intervalle_nouveau
                sauvegarder_config(config)
                scheduler.recalculer_horaires(intervalle_nouveau)
                st.success(f"Mode fixe : {intervalle_nouveau} min entre chaque clip")
                st.rerun()

    # Vue calendrier (mode intelligent)
    if cfg_pub.get("mode_scheduling") == "intelligent" and clips_en_attente:
        st.subheader("📅 Calendrier de publication")
        import pandas as pd
        maintenant = datetime.now()
        # Grouper les clips par jour + créneau
        planning = {}
        for e in clips_en_attente:
            hp_str = e.get("heure_prevue", "")
            if not hp_str:
                continue
            try:
                hp = datetime.fromisoformat(hp_str)
                jour_str = hp.strftime("%a %d/%m")
                heure_str = hp.strftime("%H:%M")
                key = (jour_str, heure_str)
                planning[key] = planning.get(key, 0) + 1
            except (ValueError, TypeError):
                continue

        if planning:
            # Construire un tableau jours x créneaux
            jours = sorted(set(k[0] for k in planning), key=lambda d: list(planning.keys()))
            heures = sorted(set(k[1] for k in planning))
            # Limiter à 7 jours
            jours = jours[:7]
            data = {}
            for jour in jours:
                data[jour] = [planning.get((jour, h), 0) for h in heures]
            df_cal = pd.DataFrame(data, index=heures)
            st.dataframe(df_cal, use_container_width=True)

    # Prochain à publier
    if clips_en_attente:
        prochain = clips_en_attente[0]
        heure_prevue_str = prochain.get("heure_prevue", "")
        nom_prochain = os.path.basename(prochain.get("chemin_clip", "?"))
        partie_p = prochain.get("numero_partie")
        total_p = prochain.get("total_parties")
        partie_txt = f" — Partie {partie_p}/{total_p}" if partie_p else ""
        if heure_prevue_str:
            try:
                heure_prevue = datetime.fromisoformat(heure_prevue_str)
                maintenant = datetime.now()
                if heure_prevue > maintenant:
                    secondes_restantes = int((heure_prevue - maintenant).total_seconds())
                    minutes = secondes_restantes // 60
                    secondes = secondes_restantes % 60
                    st.info(f"⏱️ Prochain dans **{minutes}:{secondes:02d}** — `{nom_prochain[-40:]}`{partie_txt}")
                else:
                    st.info(f"⏱️ Prochain maintenant — `{nom_prochain[-40:]}`{partie_txt}")
            except Exception:
                st.info(f"Prochain : `{nom_prochain[-40:]}`{partie_txt}")
        else:
            st.info(f"Prochain : `{nom_prochain[-40:]}`{partie_txt}")

    st.divider()

    # File de publication
    st.subheader("📋 File de publication")

    if not file:
        st.info("La file est vide. Approuvez des clips dans l'onglet Preview pour les ajouter.")
        return

    # ── Filtre par statut ──────────────────────────────────────────────────
    filtre_statut = st.radio(
        "Afficher :",
        options=["Tout", "⏳ En attente", "❌ Échecs", "✅ Publiés"],
        horizontal=True,
        label_visibility="collapsed"
    )
    filtre_map = {
        "Tout": None,
        "⏳ En attente": "en_attente",
        "❌ Échecs": "echec_definitif",
        "✅ Publiés": "succes",
    }
    filtre_valeur = filtre_map[filtre_statut]
    file_filtree = [e for e in file if filtre_valeur is None or e.get("statut") == filtre_valeur]

    if not file_filtree:
        st.info("Aucun clip dans cette catégorie.")
        return

    # ── Grouper par série (video_id) ───────────────────────────────────────
    toutes_videos = state.get_toutes_videos()
    groupes: Dict[str, list] = {}
    for entree in file_filtree:
        vid = entree.get("video_id", "inconnu")
        groupes.setdefault(vid, []).append(entree)

    # Trier les séries : d'abord celles avec des clips en attente/en cours, puis les terminées
    def priorite_serie(vid_clips):
        statuts = [c.get("statut") for c in vid_clips[1]]
        if "en_cours" in statuts: return 0
        if "en_attente" in statuts: return 1
        if "echec_definitif" in statuts: return 2
        return 3

    groupes_tries = sorted(groupes.items(), key=priorite_serie)

    for video_id, clips_serie in groupes_tries:
        # Titre de la série
        video_data = toutes_videos.get(video_id, {})
        titre_serie = video_data.get("metadata", {}).get("title") or video_id
        titre_serie = titre_serie[:60]

        # Résumé de la série
        nb_total   = len(clips_serie)
        nb_succes  = sum(1 for c in clips_serie if c.get("statut") == "succes")
        nb_attente = sum(1 for c in clips_serie if c.get("statut") == "en_attente")
        nb_echec   = sum(1 for c in clips_serie if c.get("statut") == "echec_definitif")
        nb_cours   = sum(1 for c in clips_serie if c.get("statut") == "en_cours")

        if nb_cours > 0:     icone_serie = "🔄"
        elif nb_echec > 0:   icone_serie = "❌"
        elif nb_attente > 0: icone_serie = "⏳"
        else:                icone_serie = "✅"

        resume_parts = [f"✅ {nb_succes}/{nb_total}"]
        if nb_attente: resume_parts.append(f"⏳ {nb_attente} en attente")
        if nb_cours:   resume_parts.append(f"🔄 {nb_cours} en cours")
        if nb_echec:   resume_parts.append(f"❌ {nb_echec} échec(s)")

        st.markdown(f"#### {icone_serie} {titre_serie}")
        st.caption("  —  ".join(resume_parts))

        # ── Clips de la série en ordre ─────────────────────────────────────
        clips_tries = sorted(clips_serie, key=lambda c: c.get("numero_partie", 1))
        for i, entree in enumerate(clips_tries):
            statut      = entree.get("statut", "inconnu")
            clip_id     = entree.get("clip_id", "?")
            num_partie  = entree.get("numero_partie")
            tot_parties = entree.get("total_parties")
            label_p     = f"Partie {num_partie}/{tot_parties}" if num_partie else os.path.basename(entree.get("chemin_clip", clip_id))

            icone = {"en_attente": "⏳", "en_cours": "🔄", "succes": "✅", "echec_definitif": "❌"}.get(statut, "❓")

            heure_prevue_str = entree.get("heure_prevue", "")
            try:
                hp = datetime.fromisoformat(heure_prevue_str).strftime("%H:%M") if heure_prevue_str else "--:--"
            except Exception:
                hp = "--:--"

            # Clips publiés : ligne compacte
            if statut == "succes":
                ts = entree.get("timestamp_succes", "")
                try:
                    ts_fmt = datetime.fromisoformat(ts).strftime("%d/%m %H:%M") if ts else "--"
                except Exception:
                    ts_fmt = "--"
                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{icone} **{label_p}** — publié le {ts_fmt}")
                continue

            # Autres statuts : expander
            key_suffix = f"{clip_id}_{video_id}_{i}"
            with st.expander(
                f"{icone} {label_p} | {hp} | {statut}",
                expanded=(statut in ("en_cours", "echec_definitif"))
            ):
                col_i, col_ii = st.columns(2)
                with col_i:
                    st.write(f"**Statut :** {statut}")
                    st.write(f"**Heure prévue :** {hp}")
                    st.write(f"**Tentatives :** {entree.get('tentatives', 0)}/{scheduler.max_retries}")
                with col_ii:
                    msg = entree.get("message", "")
                    if msg:
                        st.write(f"**Message :** {msg[:100]}")

                desc = entree.get("description", "")
                tags = " ".join(entree.get("hashtags", []))
                if desc or tags:
                    st.text_area("Description + hashtags", value=f"{desc}\n{tags}", height=80, disabled=True, key=f"desc_pub_{key_suffix}")

                # Bouton Réessayer
                if statut == "echec_definitif":
                    if st.button("🔄 Réessayer ce clip", key=f"retry_{key_suffix}", type="primary"):
                        for e in state.state["file_publication"]:
                            if e["clip_id"] == clip_id:
                                e["statut"] = "en_attente"
                                e["tentatives"] = 0
                                e.pop("timestamp_echec_definitif", None)
                                e.pop("message", None)
                                break
                        state.state["file_publication"].sort(key=lambda e: (
                            e.get("video_id", ""), e.get("numero_partie", 1)
                        ))
                        state.sauvegarder()
                        st.success("Remis en attente.")
                        st.rerun()

        st.divider()


# ─── ONGLET 4 : Statistiques ──────────────────────────────────────────────────

def onglet_statistiques():
    """Onglet 4 : Statistiques et historique."""
    st.header("📊 Statistiques")

    state = st.session_state.state_manager
    stats = state.get_statistiques()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total importées", stats.get("total_imports", 0))
    with col2:
        st.metric("Clips générés", stats.get("total_clips_generes", 0))
    with col3:
        st.metric("Publiés", stats.get("total_publies", 0))
    with col4:
        total = stats.get("total_publies", 0) + stats.get("total_echecs", 0)
        taux = (stats.get("total_publies", 0) / max(1, total)) * 100
        st.metric("Taux de succès", f"{taux:.0f}%")

    # Espace disque
    st.subheader("💾 Utilisation du disque")
    try:
        from modules.disk_monitor import get_espace_disque, get_taille_dossier_go
        total_d, utilise_d, libre_d = get_espace_disque()

        dossiers = {
            "Downloads": "data/downloads",
            "Clips bruts": "data/clips",
            "Clips traités": "data/processed",
            "Publiés": "data/published"
        }
        tailles = {nom: get_taille_dossier_go(chemin) for nom, chemin in dossiers.items()}

        for nom, taille in tailles.items():
            pct = (taille / max(0.01, libre_d + utilise_d)) * 100
            st.write(f"**{nom}** : {taille:.2f} Go")
            st.progress(min(1.0, pct / 10))

        st.write(f"**Espace libre total** : {libre_d:.1f} Go / {total_d:.1f} Go")
        st.progress(utilise_d / max(1.0, total_d))

    except Exception as e:
        st.warning(f"Impossible de lire les statistiques disque : {e}")

    # Historique des 30 derniers jours
    st.subheader("📅 Historique (30 derniers jours)")
    historique = stats.get("historique", [])

    if not historique:
        st.info("Aucun historique disponible.")
        return

    # Compter par jour
    comptage_par_jour = {}
    for evenement in historique:
        date_str = evenement.get("date", "")[:10]
        if date_str:
            if date_str not in comptage_par_jour:
                comptage_par_jour[date_str] = {"succes": 0, "echecs": 0}
            if evenement.get("evenement") == "publication_succes":
                comptage_par_jour[date_str]["succes"] += 1
            elif evenement.get("evenement") == "publication_echec":
                comptage_par_jour[date_str]["echecs"] += 1

    if comptage_par_jour:
        import pandas as pd
        df = pd.DataFrame([
            {"Date": date, "Publiés": v["succes"], "Échecs": v["echecs"]}
            for date, v in sorted(comptage_par_jour.items())
        ])
        st.bar_chart(df.set_index("Date")[["Publiés", "Échecs"]])

    # ── Répartition par catégorie ─────────────────────────────────────────────
    st.subheader("📂 Répartition par catégorie")
    toutes_videos = state.get_toutes_videos()
    comptage_cat = {}
    for vid_data in toutes_videos.values():
        cat = vid_data.get("categorie", "autre") or "autre"
        comptage_cat[cat] = comptage_cat.get(cat, 0) + 1

    if comptage_cat:
        import pandas as pd
        df_cat = pd.DataFrame([
            {"Catégorie": cat, "Vidéos": nb}
            for cat, nb in sorted(comptage_cat.items())
        ])
        st.bar_chart(df_cat.set_index("Catégorie"))
    else:
        st.info("Aucune vidéo importée.")

    # ── Publications par heure de la journée ──────────────────────────────────
    if historique:
        st.subheader("🕐 Publications par heure")
        comptage_heure = {h: 0 for h in range(24)}
        for evt in historique:
            if evt.get("evenement") == "publication_succes":
                date_str = evt.get("date", "")
                if len(date_str) >= 13:
                    try:
                        comptage_heure[int(date_str[11:13])] += 1
                    except (ValueError, IndexError):
                        pass
        if any(v > 0 for v in comptage_heure.values()):
            import pandas as pd
            df_h = pd.DataFrame([
                {"Heure": f"{h:02d}h", "Publications": comptage_heure[h]}
                for h in range(24)
            ])
            st.bar_chart(df_h.set_index("Heure"))

    # ── Temps de traitement ───────────────────────────────────────────────────
    st.subheader("⏱️ Temps de traitement")
    durees = []
    for vid_data in toutes_videos.values():
        ts_import = vid_data.get("timestamp_import")
        ts_pret = vid_data.get("timestamp_pret")
        if ts_import and ts_pret:
            try:
                d = (datetime.fromisoformat(ts_pret) - datetime.fromisoformat(ts_import)).total_seconds()
                if 0 < d < 86400:
                    durees.append(d)
            except (ValueError, TypeError):
                pass

    if durees:
        moy = sum(durees) / len(durees)
        col_t1, col_t2, col_t3 = st.columns(3)
        with col_t1:
            st.metric("Temps moyen", f"{moy / 60:.1f} min")
        with col_t2:
            st.metric("Plus rapide", f"{min(durees) / 60:.1f} min")
        with col_t3:
            st.metric("Plus lent", f"{max(durees) / 60:.1f} min")
    else:
        st.info("Pas assez de données pour les statistiques de temps.")

    # ── Tableau des vidéos ────────────────────────────────────────────────────
    st.subheader("📋 Vidéos traitées")
    donnees_videos = []
    for vid_id, vid_data in toutes_videos.items():
        titre = vid_data.get("metadata", {}).get("title", vid_id)[:40]
        nb_clips = len(vid_data.get("clips", []))
        cat = vid_data.get("categorie", "?")
        etape = vid_data.get("etape", "?")
        donnees_videos.append({
            "Titre": titre,
            "Catégorie": cat,
            "Clips": nb_clips,
            "Statut": etape
        })
    if donnees_videos:
        import pandas as pd
        st.dataframe(pd.DataFrame(donnees_videos), use_container_width=True)
    else:
        st.info("Aucune vidéo.")

    # Engagement
    st.subheader("📈 Engagement TikTok")
    engagement_global = stats.get("engagement_global", {})
    if engagement_global and engagement_global.get("total_vues", 0) > 0:
        col_e1, col_e2, col_e3, col_e4 = st.columns(4)
        with col_e1:
            st.metric("Total vues", f"{engagement_global.get('total_vues', 0):,}")
        with col_e2:
            st.metric("Total likes", f"{engagement_global.get('total_likes', 0):,}")
        with col_e3:
            st.metric("Total partages", f"{engagement_global.get('total_partages', 0):,}")
        with col_e4:
            st.metric("Total commentaires", f"{engagement_global.get('total_commentaires', 0):,}")

        # Engagement par catégorie
        par_cat = engagement_global.get("par_categorie", {})
        if par_cat:
            import pandas as pd
            data_cat = [
                {"Catégorie": cat, "Vues moyennes": d["vues"] // max(d["count"], 1), "Clips": d["count"]}
                for cat, d in par_cat.items()
            ]
            st.write("**Performance par catégorie**")
            st.dataframe(pd.DataFrame(data_cat), use_container_width=True)

        # Meilleures heures
        par_heure = engagement_global.get("par_heure", {})
        if par_heure:
            import pandas as pd
            data_h = [
                {"Heure": f"{int(h):02d}h", "Vues moyennes": d["vues"] // max(d["count"], 1)}
                for h, d in sorted(par_heure.items(), key=lambda x: int(x[0]))
            ]
            st.write("**Meilleures heures de publication**")
            st.bar_chart(pd.DataFrame(data_h).set_index("Heure"))

        # Top clips
        try:
            from modules.engagement_tracker import EngagementTracker
            tracker = EngagementTracker(st.session_state.config, state)
            top = tracker.get_top_clips(5)
            if top:
                import pandas as pd
                st.write("**Top clips**")
                st.dataframe(pd.DataFrame(top), use_container_width=True)
        except Exception:
            pass
    else:
        st.info("Aucune donnée d'engagement. Les métriques apparaîtront après publication avec l'API TikTok configurée.")

    # Modèle prédictif viral
    st.subheader("🧠 Modèle Prédictif Viral")
    try:
        from modules.viral_predictor import ViralPredictor
        predictor = ViralPredictor(state)
        info = predictor.get_info_modele()
        if info["entraine"]:
            col_m1, col_m2, col_m3 = st.columns(3)
            with col_m1:
                st.metric("R² score", f"{info['r2_score']:.3f}")
            with col_m2:
                st.metric("Échantillons", info["nb_echantillons"])
            with col_m3:
                if info["date_entrainement"]:
                    date_str = info["date_entrainement"][:10]
                    st.metric("Dernier entraînement", date_str)
        else:
            st.info(f"Le modèle nécessite au moins 15 clips avec données d'engagement pour s'entraîner.")

        if st.button("🔄 Réentraîner le modèle", key="retrain_viral"):
            with st.spinner("Entraînement en cours..."):
                succes = predictor.entrainer()
            if succes:
                st.success(f"Modèle entraîné : R²={predictor.r2_score:.3f}, {predictor.nb_echantillons} clips")
            else:
                st.warning("Pas assez de données d'engagement pour entraîner le modèle.")
            st.rerun()
    except Exception as e:
        st.warning(f"Module prédictif non disponible : {e}")

    # Hashtags tendance
    st.subheader("🔥 Hashtags Tendance")
    try:
        from modules.trending import get_info_cache, forcer_rafraichissement
        config = st.session_state.config
        info_cache = get_info_cache(config)
        if info_cache["existe"] and info_cache["hashtags"]:
            st.write(" ".join(info_cache["hashtags"][:10]))
            age = info_cache.get("age_heures", 0)
            st.caption(f"Dernière mise à jour : il y a {age:.1f}h")
        else:
            st.info("Aucun hashtag trending en cache. Cliquez sur Rafraîchir.")
        if st.button("🔄 Rafraîchir les hashtags trending", key="refresh_trending"):
            with st.spinner("Récupération des trending..."):
                nouveaux = forcer_rafraichissement(config)
            if nouveaux:
                st.success(f"{len(nouveaux)} hashtags trending récupérés")
            else:
                st.warning("Impossible de récupérer les trending — hashtags par défaut utilisés")
            st.rerun()
    except Exception as e:
        st.warning(f"Module trending non disponible : {e}")


# ─── ONGLET 5 : Paramètres ────────────────────────────────────────────────────

def onglet_parametres():
    """Onglet 5 : Configuration de l'application."""
    st.header("⚙️ Paramètres")

    config = st.session_state.config
    if not config:
        st.error("Configuration non chargée.")
        return

    modifie = False

    # ── Section : Intro/Outro ──────────────────────────────────────────────
    with st.expander("🎬 Intro & Outro", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Intro")
            texte_intro = st.text_input(
                "Texte principal",
                value=config["intro_outro"]["intro"].get("texte_principal", "divertissement45000")
            )
            couleur_fond_intro = st.color_picker(
                "Couleur de fond",
                value=f"#{config['intro_outro']['intro'].get('couleur_fond_hex', '1a1a2e')}"
            )
            couleur_accent = st.color_picker(
                "Couleur accent",
                value=f"#{config['intro_outro']['intro'].get('couleur_accent_hex', 'e94560')}"
            )
            duree_intro = st.slider("Durée intro (secondes)", 1, 5, config["intro_outro"].get("duree_intro_secondes", 3))

        with col2:
            st.subheader("Outro")
            texte_outro_1 = st.text_input(
                "Texte principal",
                value=config["intro_outro"]["outro"].get("texte_principal", "Follow @divertissement45000")
            )
            texte_outro_2 = st.text_input(
                "Sous-texte",
                value=config["intro_outro"]["outro"].get("texte_secondaire", "Like & Share")
            )
            duree_outro = st.slider("Durée outro (secondes)", 2, 8, config["intro_outro"].get("duree_outro_secondes", 4))

        if st.button("Sauvegarder intro/outro"):
            config["intro_outro"]["intro"]["texte_principal"] = texte_intro
            config["intro_outro"]["intro"]["couleur_fond_hex"] = couleur_fond_intro.lstrip("#")
            config["intro_outro"]["intro"]["couleur_accent_hex"] = couleur_accent.lstrip("#")
            config["intro_outro"]["duree_intro_secondes"] = duree_intro
            config["intro_outro"]["outro"]["texte_principal"] = texte_outro_1
            config["intro_outro"]["outro"]["texte_secondaire"] = texte_outro_2
            config["intro_outro"]["duree_outro_secondes"] = duree_outro
            if sauvegarder_config(config):
                st.success("✅ Intro/Outro sauvegardée")
                st.session_state.config = config

    # ── Section : Publication ──────────────────────────────────────────────
    with st.expander("📤 Publication TikTok"):
        st.info(
            "**Comment configurer l'API TikTok :**\n"
            "1. Allez sur [developers.tiktok.com](https://developers.tiktok.com)\n"
            "2. Créez une application\n"
            "3. Demandez la permission 'Content Posting API'\n"
            "4. Collez vos identifiants ci-dessous"
        )

        methode_pub = st.selectbox(
            "Méthode de publication",
            options=["api", "playwright"],
            format_func=lambda x: {
                "api": "🔑 TikTok Content Posting API (recommandé)",
                "playwright": "🌐 Navigateur automatisé (Playwright)"
            }.get(x, x),
            index=["api", "playwright"].index(config.get("publication", {}).get("methode", "api"))
        )

        client_key = st.text_input(
            "Client Key",
            value=config["publication"]["tiktok_api"].get("client_key", ""),
            type="password"
        )
        client_secret = st.text_input(
            "Client Secret",
            value=config["publication"]["tiktok_api"].get("client_secret", ""),
            type="password"
        )
        access_token = st.text_input(
            "Access Token",
            value=config["publication"]["tiktok_api"].get("access_token", ""),
            type="password"
        )
        refresh_token = st.text_input(
            "Refresh Token",
            value=config["publication"]["tiktok_api"].get("refresh_token", ""),
            type="password"
        )

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("💾 Sauvegarder API"):
                config["publication"]["methode"] = methode_pub
                config["publication"]["tiktok_api"]["client_key"] = client_key
                config["publication"]["tiktok_api"]["client_secret"] = client_secret
                config["publication"]["tiktok_api"]["access_token"] = access_token
                config["publication"]["tiktok_api"]["refresh_token"] = refresh_token
                if sauvegarder_config(config):
                    st.success("✅ Identifiants API sauvegardés")
                    st.session_state.config = config

        with col_btn2:
            if st.button("🔗 Générer URL d'autorisation OAuth"):
                if client_key:
                    from modules.publisher import TikTokAPIPublisher
                    pub = TikTokAPIPublisher(config)
                    pub.client_key = client_key
                    url = pub.get_url_autorisation("https://localhost:8080/callback")
                    st.code(url)
                    st.info("Visitez cette URL, connectez-vous à TikTok, puis copiez le code 'code=...' depuis l'URL de redirection")
                else:
                    st.warning("Entrez d'abord le Client Key")

    # ── Section : Comptes TikTok ─────────────────────────────────────────
    with st.expander("👥 Comptes TikTok"):
        comptes = config.get("comptes", [])
        compte_actif_id = config.get("compte_actif", "principal")

        if comptes:
            st.write(f"**{len(comptes)} compte(s) configuré(s)**")
            for i, c in enumerate(comptes):
                icone = "🟢" if c.get("id") == compte_actif_id else "⚪"
                st.write(f"{icone} **{c.get('nom', c.get('id'))}** — {c.get('tiktok_username', 'N/A')}")
        else:
            st.info("Aucun compte configuré. Ajoutez votre premier compte ci-dessous.")

        st.divider()
        st.write("**Ajouter un nouveau compte**")
        new_id = st.text_input("Identifiant (unique, sans espaces)", key="new_compte_id", placeholder="mon_compte_2")
        new_nom = st.text_input("Nom d'affichage", key="new_compte_nom", placeholder="Mon Compte Sport")
        new_username = st.text_input("Username TikTok", key="new_compte_user", placeholder="@moncompte")

        if st.button("➕ Ajouter le compte", key="add_compte", use_container_width=True):
            if not new_id or not new_nom:
                st.error("L'identifiant et le nom sont requis.")
            elif any(c.get("id") == new_id for c in comptes):
                st.error(f"Le compte '{new_id}' existe déjà.")
            else:
                new_compte = {
                    "id": new_id.strip().replace(" ", "_"),
                    "nom": new_nom.strip(),
                    "tiktok_username": new_username.strip(),
                }
                comptes.append(new_compte)
                config["comptes"] = comptes
                sauvegarder_config(config)
                st.success(f"Compte '{new_nom}' ajouté. Sélectionnez-le dans la sidebar.")
                st.rerun()

    # ── Section : Anti-Ban ────────────────────────────────────────────────
    with st.expander("🛡️ Système Anti-Ban"):
        cfg_ab = config.get("anti_ban", {})
        ab_actif = st.checkbox("Anti-ban activé", value=cfg_ab.get("actif", True), key="ab_actif")
        col_ab1, col_ab2 = st.columns(2)
        with col_ab1:
            ab_limite = st.number_input(
                "Limite quotidienne (semaine)",
                min_value=1, max_value=50,
                value=cfg_ab.get("limite_quotidienne", 10),
                key="ab_limite"
            )
            fenetre_vals = cfg_ab.get("fenetre_activite", ["08:00", "23:00"])
            ab_debut = st.text_input("Début fenêtre d'activité", value=fenetre_vals[0] if fenetre_vals else "08:00", key="ab_debut")
            ab_fin = st.text_input("Fin fenêtre d'activité", value=fenetre_vals[1] if len(fenetre_vals) > 1 else "23:00", key="ab_fin")
        with col_ab2:
            ab_weekend = st.checkbox("Pattern weekend (limite réduite)", value=cfg_ab.get("pattern_weekend", True), key="ab_weekend")
            ab_limite_we = st.number_input(
                "Limite quotidienne (weekend)",
                min_value=1, max_value=50,
                value=cfg_ab.get("limite_quotidienne_weekend", 6),
                key="ab_limite_we",
                disabled=not ab_weekend
            )
            ab_cooldown = st.number_input(
                "Cooldown après N échecs consécutifs",
                min_value=1, max_value=10,
                value=cfg_ab.get("cooldown_echecs_consecutifs", 2),
                key="ab_cooldown"
            )
        if st.button("💾 Sauvegarder anti-ban", use_container_width=True, key="save_ab"):
            config["anti_ban"] = {
                "actif": ab_actif,
                "limite_quotidienne": ab_limite,
                "fenetre_activite": [ab_debut, ab_fin],
                "cooldown_echecs_consecutifs": ab_cooldown,
                "pattern_weekend": ab_weekend,
                "limite_quotidienne_weekend": ab_limite_we,
            }
            sauvegarder_config(config)
            st.success("Configuration anti-ban sauvegardée")
            st.rerun()

    # ── Section : Espace disque ────────────────────────────────────────────
    with st.expander("💾 Gestion de l'espace disque"):
        seuil_alerte = st.number_input(
            "Alerte espace disque (Go)",
            min_value=1,
            max_value=500,
            value=config.get("disque", {}).get("alerte_espace_go", 1)
        )
        jours_nettoyage = st.number_input(
            "Supprimer les fichiers anciens de plus de (jours)",
            min_value=1,
            max_value=90,
            value=config.get("disque", {}).get("nettoyage_auto_jours", 7)
        )
        nettoyage_actif = st.checkbox(
            "Nettoyage automatique activé",
            value=config.get("disque", {}).get("nettoyage_actif", True)
        )

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            if st.button("💾 Sauvegarder paramètres disque"):
                config["disque"]["alerte_espace_go"] = seuil_alerte
                config["disque"]["nettoyage_auto_jours"] = jours_nettoyage
                config["disque"]["nettoyage_actif"] = nettoyage_actif
                if sauvegarder_config(config):
                    st.success("✅ Paramètres disque sauvegardés")
                    st.session_state.config = config

        with col_d2:
            if st.button("🗑️ Nettoyage manuel maintenant", type="secondary"):
                from modules.disk_monitor import nettoyer_anciens_fichiers
                dossiers = ["data/downloads", "data/clips"]
                with st.spinner("Nettoyage en cours..."):
                    resultat = nettoyer_anciens_fichiers(
                        dossiers, jours_nettoyage,
                        simulation=False
                    )
                st.success(
                    f"✅ {resultat['fichiers_supprimes']} fichier(s) supprimé(s), "
                    f"{resultat['espace_libere_go']:.2f} Go libérés"
                )

    # ── Section : Bibliothèque musicale ───────────────────────────────────
    with st.expander("🎵 Bibliothèque musicale"):
        dossier_music = config.get("chemins", {}).get("music_library", "music_library")
        st.write(f"**Dossier :** `{dossier_music}/`")

        categories_music = ["sport", "humour", "autre"]
        for cat in categories_music:
            chemin_cat = os.path.join(dossier_music, cat)
            os.makedirs(chemin_cat, exist_ok=True)
            fichiers = [f for f in os.listdir(chemin_cat) if not f.startswith(".")]
            st.write(f"**{cat.capitalize()}** : {len(fichiers)} fichier(s)")
            if fichiers:
                st.caption(", ".join(fichiers[:5]))

        st.info(
            "Ajoutez vos musiques MP3 dans `music_library/sport/`, `music_library/humour/` et `music_library/autre/`.\n"
            "Utilisez le script `download_music.py` pour télécharger des musiques libres de droits automatiquement."
        )

        if st.button("🎵 Lancer download_music.py"):
            try:
                import subprocess as sp
                proc = sp.Popen(
                    [sys.executable, "download_music.py"],
                    stdout=sp.PIPE,
                    stderr=sp.STDOUT,
                    text=True
                )
                st.info("Script de téléchargement lancé. Consultez les logs pour le suivi.")
            except Exception as e:
                st.error(f"Impossible de lancer le script : {e}")

    # ── Section : Whisper ─────────────────────────────────────────────────
    with st.expander("🎤 Sous-titres (Whisper)"):
        modele_whisper = st.selectbox(
            "Modèle Whisper",
            options=["tiny", "base", "small"],
            index=["tiny", "base", "small"].index(
                config.get("sous_titres", {}).get("modele_whisper", "base")
            ),
            help="tiny: rapide mais moins précis. base: bon compromis. small: plus précis mais plus lent."
        )
        seuil_conf = st.slider(
            "Seuil de confiance minimum",
            0.0, 1.0,
            config.get("sous_titres", {}).get("seuil_confiance", 0.4),
            step=0.05
        )

        if st.button("💾 Sauvegarder Whisper"):
            config["sous_titres"]["modele_whisper"] = modele_whisper
            config["sous_titres"]["seuil_confiance"] = seuil_conf
            if sauvegarder_config(config):
                st.success("✅ Paramètres Whisper sauvegardés")
                st.session_state.config = config

    # ── Section : Logs ────────────────────────────────────────────────────
    with st.expander("📋 Logs"):
        try:
            with open("logs/app.log", "r", encoding="utf-8") as f:
                lignes = f.readlines()
            derniers_logs = "".join(lignes[-100:])
            st.text_area("Derniers 100 logs", value=derniers_logs, height=300)
        except FileNotFoundError:
            st.info("Aucun log disponible.")
        except Exception as e:
            st.warning(f"Impossible de lire les logs : {e}")


# ─── Application principale ───────────────────────────────────────────────────

def main():
    """Point d'entrée principal de l'application Streamlit."""
    st.set_page_config(
        page_title="TikTok Automation — divertissement45000",
        page_icon="🎬",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Importer ici pour éviter les imports circulaires
    from modules.downloader import telecharger_video

    # Initialiser la session
    init_session()

    # Sidebar
    afficher_sidebar()

    # Alerte session précédente
    afficher_alerte_session()

    # Nettoyage + reset au démarrage (une seule fois)
    if "nettoyage_partiel_fait" not in st.session_state:
        try:
            config = st.session_state.config
            state = st.session_state.state_manager
            state.nettoyer_fichiers_partiels(
                config.get("chemins", {}).get("clips", "data/clips"),
                config.get("chemins", {}).get("processed", "data/processed")
            )
            # Reset automatique des clips bloqués en "en_cours" au démarrage
            for entree in state.get_file_publication():
                if entree.get("statut") == "en_cours":
                    entree["statut"] = "en_attente"
            state.sauvegarder()
        except Exception:
            pass
        st.session_state.nettoyage_partiel_fait = True

    # Auto-démarrage publication (peu importe l'onglet actif)
    if st.session_state.scheduler is None:
        from modules.publisher import PublicationScheduler
        st.session_state.scheduler = PublicationScheduler(
            st.session_state.config, st.session_state.state_manager, ajouter_log
        )
    _scheduler = st.session_state.scheduler
    _clips_attente = [e for e in st.session_state.state_manager.get_file_publication() if e.get("statut") == "en_attente"]
    if _clips_attente and not _scheduler.en_cours:
        _scheduler.demarrer()

    # Navigation par onglets
    onglets = st.tabs([
        "📥 Import",
        "👁️ Preview & Validation",
        "📤 Publication",
        "📊 Statistiques",
        "⚙️ Paramètres"
    ])

    with onglets[0]:
        onglet_import()

    with onglets[1]:
        onglet_preview()

    with onglets[2]:
        onglet_publication()

    with onglets[3]:
        onglet_statistiques()

    with onglets[4]:
        onglet_parametres()

    # ── Auto-refresh quand pipeline réellement actif (thread vivant) ────────
    import threading as _threading
    _pipeline_vivant = any(
        t.name == "PipelineThread" and t.is_alive()
        for t in _threading.enumerate()
    )
    # Synchroniser session_state avec la réalité du thread
    if not _pipeline_vivant and st.session_state.get("pipeline_en_cours"):
        st.session_state.pipeline_en_cours = False
    if _pipeline_vivant:
        time.sleep(2)
        st.rerun()


if __name__ == "__main__":
    main()
