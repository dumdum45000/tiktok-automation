"""
publisher.py — Module 10 : Publication TikTok et scheduling

Gère la file d'attente de publication avec scheduling automatique.
Implémente deux approches :

1. TikTok Content Posting API v2 (méthode primaire si tokens configurés)
   - Nécessite un compte développeur TikTok approuvé
   - Flux OAuth 2.0 pour obtenir les tokens

2. Playwright (méthode de secours — simulation navigateur)
   - Automatise le site web TikTok
   - Risque de détection bot — à utiliser avec précaution
   - Non disponible en mode headless (requiert affichage)

Intervalle par défaut : 1 publication toutes les 5 minutes.
Chaque clip est retenté 3 fois avant d'être marqué échec définitif.
"""

import json
import logging
import os
import random
import subprocess
import time
import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import requests

# ─── Chargement des identifiants depuis .env ─────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass  # python-dotenv optionnel — les variables d'environnement système suffisent


def _get_credentials(config: Dict) -> Dict[str, str]:
    """Charge les identifiants TikTok depuis .env puis fallback config.json."""
    creds_cfg = config.get("publication", {}).get("tiktok_credentials", {})
    return {
        "email": os.environ.get("TIKTOK_EMAIL") or creds_cfg.get("email", ""),
        "password": os.environ.get("TIKTOK_PASSWORD") or creds_cfg.get("password", ""),
    }


def _get_api_credentials(config: Dict) -> Dict[str, str]:
    """Charge les tokens API TikTok depuis .env puis fallback config.json."""
    api_cfg = config.get("publication", {}).get("tiktok_api", {})
    return {
        "client_key": os.environ.get("TIKTOK_CLIENT_KEY") or api_cfg.get("client_key", ""),
        "client_secret": os.environ.get("TIKTOK_CLIENT_SECRET") or api_cfg.get("client_secret", ""),
        "access_token": os.environ.get("TIKTOK_ACCESS_TOKEN") or api_cfg.get("access_token", ""),
        "refresh_token": os.environ.get("TIKTOK_REFRESH_TOKEN") or api_cfg.get("refresh_token", ""),
    }


def _pause(base: float, ecart: float = 0.4):
    """Pause aléatoire centrée sur `base` ± `ecart` secondes (comportement humain)."""
    time.sleep(max(0.1, random.uniform(base - ecart, base + ecart)))

logger = logging.getLogger(__name__)


# ─── TikTok Content Posting API v2 ────────────────────────────────────────────

class TikTokAPIPublisher:
    """
    Publie des vidéos via la TikTok Content Posting API v2.

    Prérequis :
    - Compte développeur sur developers.tiktok.com
    - Application avec permission "Content Posting API" approuvée
    - Access token valide (obtenu via OAuth 2.0)

    Documentation officielle :
    https://developers.tiktok.com/doc/content-posting-api-get-started/
    """

    API_BASE = "https://open.tiktokapis.com/v2"
    AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

    def __init__(self, config: Dict):
        self.config = config
        api_creds = _get_api_credentials(config)
        self.client_key = api_creds["client_key"]
        self.client_secret = api_creds["client_secret"]
        self.access_token = api_creds["access_token"]
        self.refresh_token = api_creds["refresh_token"]

    def est_configure(self) -> bool:
        """Vérifie si l'API est correctement configurée."""
        return bool(self.client_key and self.client_secret and self.access_token)

    def get_url_autorisation(self, redirect_uri: str) -> str:
        """
        Génère l'URL d'autorisation OAuth 2.0 pour obtenir le code d'autorisation.

        L'utilisateur doit visiter cette URL et approuver l'accès.
        TikTok redirigera ensuite vers redirect_uri?code=XXXX

        Args:
            redirect_uri: URI de redirection (doit correspondre à l'app TikTok)

        Returns:
            URL d'autorisation complète
        """
        import urllib.parse
        scopes = "user.info.basic,video.publish,video.upload"
        params = {
            "client_key": self.client_key,
            "response_type": "code",
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": "tiktok_auth_state"
        }
        return self.AUTH_URL + "?" + urllib.parse.urlencode(params)

    def echanger_code_pour_token(
        self,
        code: str,
        redirect_uri: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Échange le code d'autorisation contre des tokens d'accès.

        Returns:
            (access_token, refresh_token, erreur)
        """
        payload = {
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }
        try:
            response = requests.post(
                self.TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30
            )
            data = response.json()
            if "access_token" in data:
                return data["access_token"], data.get("refresh_token"), None
            else:
                erreur = data.get("description", str(data))
                return None, None, f"Erreur token : {erreur}"
        except Exception as e:
            return None, None, f"Erreur réseau : {e}"

    def rafraichir_token(self) -> bool:
        """Rafraîchit l'access token avec le refresh token."""
        if not self.refresh_token:
            return False

        payload = {
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token
        }
        try:
            response = requests.post(
                self.TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30
            )
            data = response.json()
            if "access_token" in data:
                self.access_token = data["access_token"]
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                # Mettre à jour le config.json
                self._sauvegarder_tokens()
                return True
        except Exception as e:
            logger.error(f"Erreur rafraîchissement token : {e}")
        return False

    def _sauvegarder_tokens(self):
        """Sauvegarde les nouveaux tokens dans config.json."""
        try:
            chemin_config = "config.json"
            if os.path.exists(chemin_config):
                with open(chemin_config, "r", encoding="utf-8") as f:
                    config = json.load(f)
                config["publication"]["tiktok_api"]["access_token"] = self.access_token
                config["publication"]["tiktok_api"]["refresh_token"] = self.refresh_token
                with open(chemin_config, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Impossible de sauvegarder les tokens : {e}")

    def _headers(self) -> Dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def publier_video(
        self,
        chemin_video: str,
        description: str,
        hashtags: List[str]
    ) -> Tuple[bool, str]:
        """
        Publie une vidéo sur TikTok via l'API Content Posting.

        Flux en 3 étapes :
        1. Initialiser l'upload (obtenir upload_url)
        2. Uploader le fichier vidéo
        3. Confirmer la publication

        Args:
            chemin_video: Chemin vers la vidéo MP4 finale
            description: Description du post
            hashtags: Liste de hashtags

        Returns:
            (succes, message)
        """
        if not self.est_configure():
            return False, "API TikTok non configurée (tokens manquants dans config.json)"

        if not os.path.exists(chemin_video):
            return False, f"Fichier vidéo introuvable : {chemin_video}"

        taille_video = os.path.getsize(chemin_video)
        texte_complet = description + " " + " ".join(hashtags)

        # Étape 1 : Initialiser l'upload
        logger.info("Étape 1/3 : Initialisation de l'upload TikTok...")
        payload_init = {
            "post_info": {
                "title": texte_complet[:2200],  # Max 2200 caractères
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": taille_video,
                "chunk_size": taille_video,  # Upload en un seul chunk
                "total_chunk_count": 1
            }
        }

        try:
            response_init = requests.post(
                f"{self.API_BASE}/post/publish/video/init/",
                json=payload_init,
                headers=self._headers(),
                timeout=30
            )

            if response_init.status_code == 401:
                # Token expiré → essayer de le rafraîchir
                logger.info("Token expiré, tentative de rafraîchissement...")
                if self.rafraichir_token():
                    response_init = requests.post(
                        f"{self.API_BASE}/post/publish/video/init/",
                        json=payload_init,
                        headers=self._headers(),
                        timeout=30
                    )
                else:
                    return False, "Token expiré et impossible de le rafraîchir. Reconnectez-vous."

            data_init = response_init.json()

            if response_init.status_code != 200:
                erreur = data_init.get("error", {}).get("message", str(data_init))
                return False, f"Erreur initialisation upload : {erreur}"

            upload_url = data_init.get("data", {}).get("upload_url")
            publish_id = data_init.get("data", {}).get("publish_id")

            if not upload_url or not publish_id:
                return False, f"Réponse inattendue de l'API : {data_init}"

        except requests.RequestException as e:
            return False, f"Erreur réseau initialisation : {e}"

        # Étape 2 : Uploader la vidéo
        logger.info(f"Étape 2/3 : Upload de la vidéo ({taille_video / (1024**2):.1f} Mo)...")
        try:
            with open(chemin_video, "rb") as f:
                video_data = f.read()

            response_upload = requests.put(
                upload_url,
                data=video_data,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(taille_video),
                    "Content-Range": f"bytes 0-{taille_video - 1}/{taille_video}"
                },
                timeout=300  # 5 minutes pour l'upload
            )

            if response_upload.status_code not in (200, 201, 206):
                return False, f"Erreur upload vidéo (HTTP {response_upload.status_code})"

        except requests.RequestException as e:
            return False, f"Erreur réseau upload : {e}"

        # Étape 3 : Vérifier le statut de publication
        logger.info("Étape 3/3 : Vérification de la publication...")
        for tentative_verif in range(10):
            time.sleep(5)  # Attendre 5s entre chaque vérification
            try:
                response_status = requests.post(
                    f"{self.API_BASE}/post/publish/status/fetch/",
                    json={"publish_id": publish_id},
                    headers=self._headers(),
                    timeout=30
                )
                data_status = response_status.json()
                statut = data_status.get("data", {}).get("status", "")

                if statut == "PUBLISH_COMPLETE":
                    url_post = data_status.get("data", {}).get("publicaly_available_post_id", "")
                    logger.info(f"Publication réussie ! ID : {publish_id}")
                    return True, f"Publication réussie (ID: {publish_id})"
                elif statut in ("FAILED", "SEND_BY_USER_FAILED"):
                    raison = data_status.get("data", {}).get("fail_reason", "Raison inconnue")
                    return False, f"Publication échouée : {raison}"
                else:
                    logger.debug(f"Statut publication : {statut} (tentative {tentative_verif + 1}/10)")

            except requests.RequestException as e:
                logger.warning(f"Erreur vérification statut : {e}")

        return False, "Timeout : la publication n'a pas été confirmée dans les 50 secondes"


# ─── Publication via Playwright ────────────────────────────────────────────────

class PlaywrightPublisher:
    """
    Publie des vidéos sur TikTok via l'automatisation du navigateur.

    IMPORTANT : Cette approche simule un utilisateur humain et comporte
    un risque de détection par TikTok. Utilisez-la uniquement si l'API
    officielle n'est pas disponible.

    Risques :
    - Restriction temporaire ou permanente du compte si détecté
    - Instable si TikTok change son interface
    - Nécessite une session TikTok active dans le navigateur

    Statut 2026 : Fonctionne avec précautions (délais, stealth).
    """

    def __init__(self, config: Dict):
        self.config = config

    def est_disponible(self) -> bool:
        """Vérifie si playwright est installé."""
        try:
            import playwright
            return True
        except ImportError:
            return False

    def publier_video(
        self,
        chemin_video: str,
        description: str,
        hashtags: List[str],
        callback: Optional[Callable] = None
    ) -> Tuple[bool, str]:
        """
        Publie une vidéo sur TikTok via le navigateur automatisé.

        PRÉREQUIS : Vous devez être connecté à TikTok dans un navigateur Chromium.
        L'application utilise le profil de navigateur existant pour éviter
        de re-saisir les identifiants à chaque fois.

        Args:
            chemin_video: Chemin vers la vidéo finale
            description: Description du post
            hashtags: Liste de hashtags
            callback: Fonction de progression

        Returns:
            (succes, message)
        """
        def log(msg):
            logger.info(msg)
            if callback:
                callback(msg)

        if not self.est_disponible():
            return False, "Playwright non installé. Exécutez : pip install playwright && playwright install chromium"

        texte_complet = description + "\n" + " ".join(hashtags)
        texte_complet = texte_complet[:2200]

        dossier_profil = os.path.expanduser("~/.tiktok_automation_browser_profile")
        os.makedirs(dossier_profil, exist_ok=True)

        # Tuer tout Chrome for Testing existant + nettoyer les verrous
        try:
            subprocess.run(["pkill", "-f", "Google Chrome for Testing"], capture_output=True)
            time.sleep(2)
        except Exception as e:
            logger.debug(f"Impossible de tuer Chrome for Testing : {e}")
        for verrou in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            chemin_verrou = os.path.join(dossier_profil, verrou)
            try:
                if os.path.exists(chemin_verrou):
                    os.remove(chemin_verrou)
            except Exception as e:
                logger.debug(f"Impossible de supprimer {verrou} : {e}")

        log("Lancement du navigateur...")

        browser = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                # Viewport légèrement aléatoire — évite la signature fixe 1280×800
                vp_w = random.randint(1240, 1400)
                vp_h = random.randint(780, 860)

                # Chrome for Testing (Playwright) — support WASM/SIMD complet pour clip-forge
                chrome_path = (
                    "/Users/dumdum45/Library/Caches/ms-playwright/chromium-1208"
                    "/chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS"
                    "/Google Chrome for Testing"
                )
                browser = p.chromium.launch_persistent_context(
                    dossier_profil,
                    headless=False,
                    executable_path=chrome_path,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-infobars",
                        "--disable-extensions",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--password-store=basic",
                        "--use-mock-keychain",
                        # GPU désactivé — clip-forge GPU exhaustait la VRAM → crash renderer
                        # CPU-only : clip-forge prend 2-5 min (vs 5s GPU) mais Chrome stable
                        # Phase 2 polling (10 min) couvre le temps de traitement CPU
                        "--disable-gpu",
                    ],
                    viewport={"width": vp_w, "height": vp_h},
                    ignore_https_errors=True,
                )

                page = browser.pages[0] if browser.pages else browser.new_page()

                # NOTE: Stealth désactivé — la session Creator Center qui fonctionnait
                # (18:50-18:53) n'avait PAS de stealth installé. Stealth peut interférer
                # avec les APIs Blob/Fetch/File utilisées par TikTok Studio pour l'upload.
                log("Stealth désactivé (test sans anti-détection)")

                # Intercepter les messages console et les WebWorkers pour diagnostiquer clip-forge
                _worker_msgs = []
                _console_errors = []
                def _on_console(msg):
                    if msg.type in ('error', 'warning') or 'forge' in msg.text.lower() or 'worker' in msg.text.lower():
                        _console_errors.append(f"[{msg.type}] {msg.text[:200]}")
                page.on('console', _on_console)

                # NOTE: add_init_script Worker patching retiré — TikTok vérifie
                # Worker.toString() pour détecter les fonctions non-natives,
                # ce qui stoppait silencieusement clip-forge-media-decoder.js.

                # Intercepter les requêtes échouées pour diagnostic
                _failed_reqs = []
                _upload_reqs = []
                def _on_req_failed(req):
                    _failed_reqs.append(f"{req.method} {req.url[:120]} → {req.failure}")
                def _on_response(resp):
                    if any(x in resp.url for x in ['upload', 'video', 'media', 'post', 'publish']):
                        _upload_reqs.append(f"{resp.status} {resp.url[:100]}")
                page.on('requestfailed', _on_req_failed)
                page.on('response', _on_response)

                # ── Navigation ─────────────────────────────────────────────
                # creator-center/upload redirige systématiquement vers Studio —
                # on navigue directement pour éviter la double navigation et l'état
                # React corrompu qui en résultait.
                log("Navigation vers TikTok Studio...")
                _url_upload = "https://www.tiktok.com/tiktokstudio/upload"
                try:
                    page.goto(_url_upload, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
                _pause(4.0, 1.0)

                # ── Connexion si nécessaire ────────────────────────────────
                url_actuelle = page.url.lower()
                if any(x in url_actuelle for x in ["login", "signin", "passport", "account/login"]):
                    creds = _get_credentials(self.config)
                    email = creds["email"]
                    password = creds["password"]
                    if not email or not password:
                        browser.close()
                        return False, "Identifiants TikTok non configurés. Ajoutez TIKTOK_EMAIL et TIKTOK_PASSWORD dans le fichier .env"

                    log("Connexion à TikTok...")
                    try:
                        try:
                            page.goto("https://www.tiktok.com/login/phone-or-email/email",
                                      wait_until="domcontentloaded", timeout=60000)
                        except Exception:
                            pass
                        _pause(3.0, 0.8)

                        # Saisie email caractère par caractère (vitesse humaine)
                        champ_email = page.locator('input[name="username"], input[type="email"]').first
                        champ_email.click()
                        _pause(0.5, 0.2)
                        for car in email:
                            champ_email.press(car)
                            time.sleep(random.uniform(0.06, 0.18))
                        _pause(0.8, 0.3)

                        # Saisie mot de passe caractère par caractère
                        champ_mdp = page.locator('input[type="password"]').first
                        champ_mdp.click()
                        _pause(0.4, 0.2)
                        for car in password:
                            champ_mdp.press(car)
                            time.sleep(random.uniform(0.07, 0.20))
                        _pause(1.2, 0.4)  # Hésitation avant de cliquer

                        page.locator('button[type="submit"], button:has-text("Log in")').first.click()
                        _pause(6.5, 1.5)

                        if any(x in page.url.lower() for x in ["login", "passport"]):
                            log("⚠️ Captcha détecté — attente 30s...")
                            time.sleep(random.uniform(28, 35))

                        try:
                            page.goto(_url_upload,
                                      wait_until="domcontentloaded", timeout=60000)
                        except Exception:
                            pass
                        _pause(4.0, 1.0)
                    except Exception as e:
                        browser.close()
                        return False, f"Échec connexion : {e}"

                # ── Attente page prête (bouton "Sélectionner une vidéo" visible) ──
                log("Attente page upload prête...")
                try:
                    page.wait_for_selector(
                        'button:has-text("Sélectionner une vidéo"), button:has-text("Select video"), '
                        'button:has-text("Select files"), input[type="file"]',
                        state="attached", timeout=20000
                    )
                    _pause(1.5, 0.5)  # Humain : lit la page avant d'agir
                except Exception:
                    _pause(3.0, 0.8)

                # ── Upload fichier ─────────────────────────────────────────
                log("Upload de la vidéo...")
                try:
                    taille_mo = os.path.getsize(chemin_video) / (1024 * 1024)
                    _video_nom = os.path.basename(chemin_video)
                    _chemin_abs = os.path.abspath(chemin_video)
                    _sur_creator_center = "creator-center" in page.url

                    _upload_ok = False

                    if _sur_creator_center:
                        # ── Creator Center : set_input_files direct sur l'input caché ──
                        # Cette approche fonctionne parfaitement sur Creator Center
                        # (input accept="video/*", class="jsx-*").
                        # Pas besoin d'un file chooser ni d'événements React manuels.
                        try:
                            page.locator('input[type="file"]').first.set_input_files(
                                _chemin_abs, timeout=15000
                            )
                            log(f"Fichier vidéo sélectionné ({taille_mo:.1f} Mo) ✅")
                            _upload_ok = True
                        except Exception as e_cc:
                            log(f"set_input_files Creator Center échoué : {e_cc}")
                    else:
                        # ── TikTok Studio : file chooser → fallback set_input_files ──
                        try:
                            with page.expect_file_chooser(timeout=20000) as fc_info:
                                # Chercher le bouton upload dans la zone centrale (pas la nav)
                                try:
                                    page.locator(
                                        'button:has-text("Sélectionner des fichiers"), '
                                        'button:has-text("Sélectionner une vidéo"), '
                                        'button:has-text("Select video"), '
                                        'button:has-text("Select files"), '
                                        'button:has-text("Choisir"), '
                                        'button:has-text("Choose")'
                                    ).first.click(timeout=5000)
                                except Exception:
                                    # Fallback : cliquer l'input directement
                                    page.locator('input[type="file"]').first.click(
                                        force=True, timeout=8000
                                    )
                            fc_info.value.set_files(_chemin_abs)
                            log(f"Fichier sélectionné via file chooser ({taille_mo:.1f} Mo) ✅")
                            _upload_ok = True
                        except Exception as e_fc:
                            log(f"file_chooser échoué ({e_fc}) — fallback set_input_files...")
                            try:
                                page.locator('input[type="file"]').first.set_input_files(
                                    _chemin_abs, timeout=15000
                                )
                                log(f"Fichier injecté via set_input_files ({taille_mo:.1f} Mo) ✅")
                                _upload_ok = True
                            except Exception as e2:
                                log(f"set_input_files échoué ({e2})")

                    log("Vidéo envoyée — attente que TikTok soit prêt...")
                    # Fermer automatiquement tout dialog natif (alert/confirm)
                    page.on('dialog', lambda d: d.dismiss())

                    # ── Attente du champ description ──────────────────────────
                    # Creator Center : traitement rapide (~30s), pas besoin de boucle longue.
                    # TikTok Studio  : peut prendre jusqu'à 10 min (traitement serveur lent).
                    _SELECTEUR_DESCRIPTION = (
                        'div[contenteditable="true"], '
                        '[class*="caption"] div[contenteditable], '
                        '[role="textbox"]'
                    )
                    _MOTS_POPUP = ["Supprimer", "Delete", "Continuer", "Continue",
                                   "Discard", "Keep", "Garder", "Annuler", "Cancel"]

                    # Screenshot précoce pour voir l'état réel de la page
                    try:
                        shot_path = "/tmp/tiktok_apres_upload.png"
                        page.screenshot(path=shot_path, full_page=False)
                        log(f"Screenshot après upload : {shot_path}")
                    except Exception:
                        pass

                    # Avec GPU actif, clip-forge traite en ~10-30s (vs 10 min en CPU).
                    # On attend 60s pour couvrir les cas lents, puis on vérifie
                    # toutes les 5s pendant encore 5 min si nécessaire.
                    log("Attente traitement clip-forge TikTok Studio...")
                    pret = False

                    # Phase 1 : attente courte active (vérif toutes les 5s pendant 90s max)
                    for _tentative_attente in range(18):  # 18 × 5s = 90s
                        time.sleep(5)
                        # Vérifier si le champ description est visible
                        try:
                            page.wait_for_selector(
                                _SELECTEUR_DESCRIPTION,
                                state='visible',
                                timeout=1000
                            )
                            pret = True
                            log(f"TikTok prêt après {(_tentative_attente+1)*5}s ✅")
                            break
                        except Exception:
                            pass
                        # Fermer les popups éventuels
                        for mot in _MOTS_POPUP:
                            try:
                                btn = page.locator(f'button:has-text("{mot}")').first
                                if btn.is_visible(timeout=300):
                                    btn.click(timeout=1500)
                                    log(f"Popup '{mot}' fermé")
                                    break
                            except Exception:
                                pass

                    # Phase 2 : si pas encore prêt, poll toutes les 15s pendant 10 min
                    if not pret:
                        log("clip-forge lent — passage en polling 15s (max 10 min)...")
                        for _tentative_attente in range(40):  # 40 × 15s = 10 min
                            try:
                                page.wait_for_selector(
                                    _SELECTEUR_DESCRIPTION,
                                    state='visible',
                                    timeout=15000
                                )
                                pret = True
                                log(f"TikTok prêt (phase 2, cycle {_tentative_attente+1}) ✅")
                                break
                            except Exception:
                                pass
                            # Screenshot diagnostic toutes les 2 cycles
                            if _tentative_attente % 2 == 0:
                                try:
                                    page.screenshot(path="/tmp/tiktok_attente.png", full_page=False)
                                    _btns = page.evaluate(
                                        'Array.from(document.querySelectorAll("button"))'
                                        '.map(b=>b.textContent.trim()).filter(t=>t&&t.length<40).slice(0,6)'
                                    )
                                    log(f"[{90+_tentative_attente*15}s] Boutons: {_btns}")
                                except Exception:
                                    pass
                            for mot in _MOTS_POPUP:
                                try:
                                    btn = page.locator(f'button:has-text("{mot}")').first
                                    if btn.is_visible(timeout=300):
                                        btn.click(timeout=1500)
                                        log(f"Popup '{mot}' fermé")
                                        break
                                except Exception:
                                    pass

                    if not pret:
                        log("⚠️ Champ description non trouvé après 10 min — tentative publication directe")
                except Exception as e:
                    browser.close()
                    return False, f"Échec upload : {e}"

                # ── Diagnostic réseau post-attente ─────────────────────────
                try:
                    if _upload_reqs:
                        log(f"Upload requests ({len(_upload_reqs)}): {_upload_reqs[:5]}")
                    if _failed_reqs:
                        log(f"FAILED requests: {_failed_reqs[:5]}")
                    if _worker_msgs:
                        log(f"Worker msgs ({len(_worker_msgs)}): {_worker_msgs[:8]}")
                    if _console_errors:
                        log(f"Console errors: {_console_errors[:8]}")
                except Exception as e:
                    log(f"Diagnostic réseau échoué: {e}")

                # ── Description ────────────────────────────────────────────
                log("Saisie de la description...")
                try:
                    # Récupérer les coordonnées du champ pour un clic natif Playwright
                    coords_champ = page.evaluate('''() => {
                        const all = Array.from(document.querySelectorAll(
                            '[contenteditable], textarea, [role="textbox"]'
                        )).filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.width > 30 && r.height > 10;
                        });
                        if (!all.length) return null;
                        const el = all[all.length - 1];
                        const r = el.getBoundingClientRect();
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                    }''')
                    if coords_champ:
                        # Clic natif Playwright avec trajectoire humaine
                        # (mouvement depuis coin aléatoire de l'écran)
                        x_depart = random.randint(200, 800)
                        y_depart = random.randint(100, 400)
                        page.mouse.move(x_depart, y_depart)
                        _pause(0.15, 0.08)
                        page.mouse.move(coords_champ['x'], coords_champ['y'])
                        _pause(0.12, 0.06)
                        page.mouse.click(coords_champ['x'], coords_champ['y'])
                        _pause(0.5, 0.2)
                        page.keyboard.press("Meta+a")
                        page.keyboard.press("Backspace")
                        _pause(0.3, 0.1)
                        # Coller via presse-papiers (pbcopy + Cmd+V) — instantané
                        # évite le crash Chrome causé par 30+ secondes de keyboard.type()
                        import subprocess
                        subprocess.run(
                            ['pbcopy'],
                            input=texte_complet.encode('utf-8'),
                            check=True
                        )
                        page.keyboard.press("Meta+v")
                        _pause(0.8, 0.2)
                        log("✅ Description saisie")
                    else:
                        log("⚠️ Champ description introuvable")
                except Exception as e:
                    log(f"⚠️ Description ignorée : {e}")

                # ── Désactiver Duet/Stitch ─────────────────────────────────
                try:
                    for label in ["Duet", "Stitch"]:
                        try:
                            toggle = page.locator(
                                f'label:has-text("{label}") input[type="checkbox"],'
                                f'div:has-text("{label}") input[type="checkbox"]'
                            ).first
                            if toggle.is_checked():
                                toggle.click()
                                _pause(0.4, 0.15)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Pause naturelle avant Publier
                _pause(3.0, 1.0)

                # ── Clic Publier ───────────────────────────────────────────
                log("Publication en cours...")
                try:
                    # Tentative 1 : locator direct sans wait_for (évite les requêtes DOM
                    # longues qui font crasher le renderer après GPU clip-forge)
                    _clique = False
                    for _sel in [
                        'button:has-text("Publier")',
                        'button:has-text("Post")',
                        'button:has-text("Publish")',
                        'button:has-text("Poster")',
                    ]:
                        try:
                            _btn = page.locator(_sel).last
                            if _btn.is_visible(timeout=2000):
                                _btn.click(timeout=5000)
                                _clique = True
                                log(f"✅ Clip publié ({_sel}) !")
                                break
                        except Exception:
                            continue

                    if _clique:
                        _pause(9.0, 2.0)  # Attente confirmation TikTok
                        browser.close()
                        return True, "Publication réussie"

                    # Tentative 2 : evaluate JS (dernier recours)
                    _res = page.evaluate('''() => {
                        const mots = ["post","publier","publish","poster"];
                        const btn = Array.from(document.querySelectorAll("button"))
                            .find(b => mots.includes(b.textContent.trim().toLowerCase()));
                        if (btn) { btn.click(); return btn.textContent.trim(); }
                        return null;
                    }''')
                    if _res:
                        log(f"✅ Clip publié via JS ({_res}) !")
                        _pause(9.0, 2.0)
                        browser.close()
                        return True, "Publication réussie"

                    browser.close()
                    return False, "Bouton Publier introuvable"

                except Exception as e:
                    # Diagnostic boutons disponibles
                    try:
                        _btns_dispo = page.evaluate(
                            'Array.from(document.querySelectorAll("button"))'
                            '.map(b=>b.textContent.trim()).filter(t=>t&&t.length<40)'
                        )
                        log(f"Boutons disponibles: {_btns_dispo}")
                    except Exception:
                        pass
                    browser.close()
                    return False, f"Échec clic Publier : {e}"

        except Exception as e:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            logger.exception("Erreur Playwright")
            return False, f"Erreur navigateur : {e}"


# ─── Gestionnaire de file de publication ──────────────────────────────────────

class PublicationScheduler:
    """
    Gestionnaire de la file de publication avec scheduling automatique.

    Publie un clip toutes les N minutes (défaut 5 min).
    Gère les retries (3 tentatives avant échec définitif).
    Envoie des notifications macOS à chaque étape.
    """

    def __init__(self, config: Dict, state_manager, callback_ui: Optional[Callable] = None):
        self.config = config
        self.state = state_manager
        self.callback_ui = callback_ui
        self.en_cours = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Instancier les publishers disponibles
        self.api_publisher = TikTokAPIPublisher(config)
        self.playwright_publisher = PlaywrightPublisher(config)

        cfg_pub = config.get("publication", {})
        self.intervalle_minutes = cfg_pub.get("intervalle_minutes", 5)
        self.max_retries = cfg_pub.get("max_retries", 3)
        self.delai_retry = cfg_pub.get("delai_retry_secondes", 30)
        self.methode = cfg_pub.get("methode", "api")

    def _log(self, msg: str):
        logger.info(msg)
        if self.callback_ui:
            self.callback_ui(msg)

    def _publier_un_clip(self, entree: Dict) -> Tuple[bool, str]:
        """
        Tente de publier un clip en utilisant la méthode disponible.
        Essaie l'API en premier, puis Playwright si l'API échoue.
        """
        chemin_video = entree.get("chemin_clip", "")
        description = entree.get("description", "")
        hashtags = entree.get("hashtags", [])

        if not os.path.exists(chemin_video):
            return False, f"Fichier introuvable : {chemin_video}"

        # Méthode 1 : TikTok API officielle
        if self.methode == "api" and self.api_publisher.est_configure():
            self._log(f"Publication via API TikTok : {os.path.basename(chemin_video)}")
            succes, message = self.api_publisher.publier_video(chemin_video, description, hashtags)
            if succes:
                return True, message
            else:
                self._log(f"⚠️ API échouée : {message}. Tentative Playwright...")

        # Méthode 2 : Playwright
        if self.playwright_publisher.est_disponible():
            self._log(f"Publication via navigateur : {os.path.basename(chemin_video)}")
            succes, message = self.playwright_publisher.publier_video(
                chemin_video, description, hashtags, self._log
            )
            return succes, message

        # Aucune méthode disponible
        return False, (
            "Aucune méthode de publication disponible. "
            "Configurez l'API TikTok ou installez Playwright."
        )

    def publier_prochain(self) -> Dict:
        """
        Publie le prochain clip en attente dans la file.

        Returns:
            Dictionnaire avec statut, clip_id, message
        """
        from modules.notifications import notifier_publication_succes, notifier_publication_echec

        entree = self.state.get_prochain_a_publier()
        if not entree:
            return {"statut": "file_vide", "message": "Aucun clip en attente"}

        clip_id = entree["clip_id"]
        nom_clip = os.path.basename(entree.get("chemin_clip", clip_id))

        self._log(f"Début publication : {nom_clip}")
        self.state.mettre_a_jour_statut_publication(clip_id, "en_cours")

        # Tentatives avec retries
        for tentative in range(1, self.max_retries + 1):
            self._log(f"Tentative {tentative}/{self.max_retries}...")

            succes, message = self._publier_un_clip(entree)

            if succes:
                self.state.mettre_a_jour_statut_publication(clip_id, "succes", message)
                self.state.ajouter_historique("publication_succes", {"clip_id": clip_id})
                # Nettoyage des fichiers intermédiaires si activé
                if self.config.get("disque", {}).get("nettoyage_actif", False):
                    try:
                        self.state.nettoyer_fichiers_publies()
                    except Exception as e:
                        logger.warning(f"Nettoyage post-publication : {e}")
                notifier_publication_succes(nom_clip)
                self._log(f"✅ Publication réussie : {nom_clip}")
                return {"statut": "succes", "clip_id": clip_id, "message": message}
            else:
                nb_tentatives = self.state.incrementer_tentatives(clip_id)
                self._log(f"❌ Échec tentative {tentative} : {message}")
                notifier_publication_echec(nom_clip, tentative, self.max_retries)

                if tentative < self.max_retries:
                    self._log(f"Nouvelle tentative dans {self.delai_retry}s...")
                    time.sleep(self.delai_retry)

        # Toutes les tentatives ont échoué
        self.state.mettre_a_jour_statut_publication(
            clip_id, "echec_definitif",
            f"Échec après {self.max_retries} tentatives"
        )
        self.state.ajouter_historique("publication_echec", {"clip_id": clip_id, "message": message})
        return {"statut": "echec", "clip_id": clip_id, "message": message}

    def _boucle_publication(self):
        """Boucle interne du thread de publication automatique."""
        from modules.notifications import notifier_publication_terminee

        self._log(f"🚀 Démarrage publication automatique — 1 clip toutes les {self.intervalle_minutes} min")

        nb_succes = 0
        nb_echecs = 0

        while not self._stop_event.is_set():
            prochain = self.state.get_prochain_a_publier()

            if prochain is None:
                self._log("✅ File de publication vide — arrêt de la boucle")
                break

            resultat = self.publier_prochain()

            if resultat["statut"] == "succes":
                nb_succes += 1
            elif resultat["statut"] == "echec":
                nb_echecs += 1

            # Vérifier s'il reste des clips à publier
            prochain = self.state.get_prochain_a_publier()
            if prochain is None:
                break

            # Attendre jusqu'à l'heure prévue du prochain clip
            delai_attente = int(self.intervalle_minutes * 60)
            heure_prevue_str = prochain.get("heure_prevue")
            if heure_prevue_str:
                try:
                    heure_prevue = datetime.fromisoformat(heure_prevue_str)
                    maintenant = datetime.now()
                    if heure_prevue > maintenant:
                        delai_attente = int((heure_prevue - maintenant).total_seconds())
                        self._log(f"Prochain clip prévu à {heure_prevue.strftime('%H:%M:%S')} (dans {delai_attente}s)")
                    else:
                        retard = int((maintenant - heure_prevue).total_seconds())
                        self._log(f"Clip en retard de {retard}s — publication immédiate")
                        delai_attente = 0
                except (ValueError, TypeError):
                    self._log(f"Heure prévue invalide — attente par défaut ({delai_attente}s)")
            else:
                self._log(f"Prochain clip dans {delai_attente}s")

            for _ in range(delai_attente):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        self.en_cours = False
        notifier_publication_terminee(nb_succes, nb_echecs)
        self._log(f"Publication terminée : {nb_succes} succès, {nb_echecs} échec(s)")

    def demarrer(self):
        """Démarre la publication automatique dans un thread séparé."""
        if self.en_cours:
            self._log("⚠️ Publication déjà en cours")
            return

        if self.state.get_prochain_a_publier() is None:
            self._log("⚠️ File de publication vide")
            return

        self.en_cours = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._boucle_publication,
            daemon=True,
            name="PublicationThread"
        )
        self._thread.start()
        self._log("Thread de publication démarré")

    def arreter(self):
        """Arrête la publication automatique proprement."""
        if not self.en_cours:
            return
        self._log("Arrêt de la publication en cours...")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.en_cours = False
        self._log("Publication arrêtée")

    def recalculer_horaires(self, intervalle_minutes: Optional[int] = None):
        """Recalcule les horaires prévus pour tous les clips en attente."""
        if intervalle_minutes:
            self.intervalle_minutes = intervalle_minutes

        file = self.state.get_file_publication()
        maintenant = datetime.now()
        compteur = 0

        for entree in file:
            if entree.get("statut") == "en_attente":
                heure_prevue = maintenant + timedelta(minutes=self.intervalle_minutes * compteur)
                entree["heure_prevue"] = heure_prevue.isoformat()
                compteur += 1

        self.state.sauvegarder()
        self._log(f"Horaires recalculés pour {compteur} clip(s) en attente")
