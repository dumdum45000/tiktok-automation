"""
analyzer.py — Module 3 : Analyse et découpe intelligente des vidéos

Détecte les passages les plus dynamiques d'une vidéo selon sa catégorie.
Implémenté avec scipy + soundfile + numpy + OpenCV — SANS librosa/numba
pour une compatibilité maximale avec Python 3.13 sur macOS Intel.

- Musique : énergie spectrale (STFT scipy) + onsets
- Sport   : optical flow OpenCV + pics audio
- Humour  : amplitude + transitions scènes + timing comique
- Autre   : combinaison généraliste
"""

import logging
import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import scipy.signal
import scipy.fft
import soundfile as sf

logger = logging.getLogger(__name__)


# ─── Utilitaires audio ────────────────────────────────────────────────────────

def extraire_audio(chemin_video: str, chemin_audio: str) -> bool:
    """Extrait l'audio d'une vidéo en WAV mono 16kHz via ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", chemin_video,
        "-vn", "-ac", "1", "-ar", "16000",
        "-acodec", "pcm_s16le",
        chemin_audio
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Erreur extraction audio : {e}")
        return False


def charger_audio(chemin_audio: str) -> Optional[Tuple[np.ndarray, int]]:
    """Charge un fichier audio WAV avec soundfile."""
    try:
        y, sr = sf.read(chemin_audio, dtype="float32", always_2d=False)
        # Convertir stéréo → mono si nécessaire
        if y.ndim == 2:
            y = y.mean(axis=1)
        return y, int(sr)
    except Exception as e:
        logger.error(f"Erreur chargement audio : {e}")
        return None


# ─── Analyse audio par catégorie ─────────────────────────────────────────────

def rms_par_fenetres(y: np.ndarray, sr: int, fenetre_sec: float = 0.1) -> np.ndarray:
    """Calcule l'énergie RMS par fenêtres glissantes."""
    taille = max(1, int(sr * fenetre_sec))
    nb_fenetres = len(y) // taille
    rms = np.zeros(nb_fenetres)
    for i in range(nb_fenetres):
        seg = y[i * taille:(i + 1) * taille]
        rms[i] = np.sqrt(np.mean(seg ** 2))
    return rms


def onset_strength(y: np.ndarray, sr: int) -> np.ndarray:
    """
    Calcule la force d'onset (changements d'énergie spectrale soudains).
    Implémentation scipy sans librosa.
    """
    # Paramètres STFT
    hop = 512
    n_fft = 2048
    nperseg = min(n_fft, len(y))

    # STFT via scipy
    f, t, Zxx = scipy.signal.stft(y, fs=sr, nperseg=nperseg, noverlap=nperseg - hop)
    mag = np.abs(Zxx)

    # Flux spectral : somme des différences positives entre frames consécutives
    flux = np.sum(np.maximum(0, np.diff(mag, axis=1)), axis=0)
    flux = np.pad(flux, (1, 0))  # Aligner sur la longueur de t

    return flux


def analyser_audio_musique(y: np.ndarray, sr: int) -> np.ndarray:
    """
    Pour Musique : détecte drops, beats, refrains via analyse spectrale.
    Utilise onset strength + RMS sur bandes de fréquences (graves/médiums).
    """
    # Onset strength (changements spectraux)
    flux = onset_strength(y, sr)

    # RMS global
    rms = rms_par_fenetres(y, sr, 0.05)

    # Énergie basses fréquences (kick, bass) : filtrer < 300 Hz
    sos = scipy.signal.butter(4, 300 / (sr / 2), btype="low", output="sos")
    y_bass = scipy.signal.sosfilt(sos, y)
    rms_bass = rms_par_fenetres(y_bass, sr, 0.05)

    def norm(arr):
        mn, mx = arr.min(), arr.max()
        return (arr - mn) / (mx - mn + 1e-8)

    # Aligner sur la longueur minimale
    longueur = min(len(flux), len(rms), len(rms_bass))
    score = (
        0.4 * norm(flux[:longueur]) +
        0.3 * norm(rms[:longueur]) +
        0.3 * norm(rms_bass[:longueur])
    )
    return score


def analyser_audio_sport(y: np.ndarray, sr: int) -> np.ndarray:
    """
    Pour Sport : détecte cris commentateurs + réactions foule.
    Utilise RMS + centroïde spectrale (voix criées = hautes fréquences).
    """
    rms = rms_par_fenetres(y, sr, 0.05)

    # Centroïde spectrale : proxy de la « brillance » du son
    hop = 512
    n_fft = 1024
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)

    centroid_values = []
    for i in range(0, len(y) - n_fft, hop):
        segment = y[i:i + n_fft] * np.hanning(n_fft)
        mag = np.abs(np.fft.rfft(segment))
        mag_sum = mag.sum()
        centroid = (freqs * mag).sum() / (mag_sum + 1e-8)
        centroid_values.append(centroid)

    centroid = np.array(centroid_values)

    def norm(arr):
        mn, mx = arr.min(), arr.max()
        return (arr - mn) / (mx - mn + 1e-8)

    longueur = min(len(rms), len(centroid))
    return 0.6 * norm(rms[:longueur]) + 0.4 * norm(centroid[:longueur])


def analyser_audio_humour(y: np.ndarray, sr: int) -> np.ndarray:
    """
    Pour Humour : pics amplitude + silences (timing comique).
    Bonus sur frames suivant un pic (applaudissements/rires).
    """
    rms = rms_par_fenetres(y, sr, 0.05)
    rms_norm = rms / (rms.max() + 1e-8)

    seuil_pic = np.percentile(rms_norm, 75)
    score = np.copy(rms_norm)

    for i in range(1, len(rms_norm) - 5):
        if rms_norm[i - 1] > seuil_pic:
            score[i:i + 3] = np.minimum(1.0, score[i:i + 3] + 0.3)

    return score


def analyser_audio_autre(y: np.ndarray, sr: int) -> np.ndarray:
    """Pour Autre : combinaison généraliste onset + RMS."""
    flux = onset_strength(y, sr)
    rms = rms_par_fenetres(y, sr, 0.05)

    def norm(arr):
        mn, mx = arr.min(), arr.max()
        return (arr - mn) / (mx - mn + 1e-8)

    longueur = min(len(flux), len(rms))
    return 0.5 * norm(flux[:longueur]) + 0.5 * norm(rms[:longueur])


def analyser_audio_reaction(y: np.ndarray, sr: int) -> np.ndarray:
    """
    Détecte les moments drôles / gênants / humiliants via :
    - Énergie vocale (500-3500 Hz) : rires, exclamations, cris de surprise
    - Patterns "silence → pic soudain" (setup → punchline/réaction)
    - Soutien post-pic (rires qui durent)
    """
    rms = rms_par_fenetres(y, sr, 0.1)
    rms_norm = rms / (rms.max() + 1e-8)

    # Passe-bande vocal (rires, voix expressives)
    try:
        sos_h = scipy.signal.butter(4, 500 / (sr / 2), btype="high", output="sos")
        sos_l = scipy.signal.butter(4, 3500 / (sr / 2), btype="low", output="sos")
        y_vocal = scipy.signal.sosfilt(sos_l, scipy.signal.sosfilt(sos_h, y))
        rms_vocal = rms_par_fenetres(y_vocal, sr, 0.1)
        rms_vocal_norm = rms_vocal / (rms_vocal.max() + 1e-8)
    except Exception:
        rms_vocal_norm = rms_norm.copy()

    score = np.copy(rms_norm)

    seuil_bas = np.percentile(rms_norm, 25)
    seuil_haut = np.percentile(rms_norm, 70)

    # Bonus sur les patterns "calme → explosion" (punchline, chute, moment gênant)
    for i in range(4, len(rms_norm) - 8):
        avant_calme = rms_norm[max(0, i - 4):i].mean() < seuil_bas
        pic_soudain = rms_norm[i] > seuil_haut
        if avant_calme and pic_soudain:
            fin_bonus = min(i + 60, len(score))  # ~6s de réaction
            score[i:fin_bonus] = np.minimum(1.0, score[i:fin_bonus] + 0.45)

    # Fusionner avec énergie vocale
    long = min(len(score), len(rms_vocal_norm))
    score[:long] = 0.55 * score[:long] + 0.45 * rms_vocal_norm[:long]

    return score


def frames_audio_vers_secondes(score_frames: np.ndarray, sr: int, hop: int, duree_totale: float) -> np.ndarray:
    """Convertit un tableau de scores par frame audio en scores par seconde."""
    frames_par_sec = sr / hop
    nb_sec = int(duree_totale) + 2
    score_par_sec = np.zeros(nb_sec)
    for sec in range(nb_sec):
        idx_start = int(sec * frames_par_sec)
        idx_end = int((sec + 1) * frames_par_sec)
        idx_end = min(idx_end, len(score_frames))
        if idx_start < len(score_frames):
            score_par_sec[sec] = float(score_frames[idx_start:idx_end].mean())
    return score_par_sec


# ─── Analyse vidéo ────────────────────────────────────────────────────────────

def analyser_video_optical_flow(chemin_video: str, fps_echantillon: float = 2.0) -> Tuple[np.ndarray, float]:
    """
    Calcule le flux optique moyen par seconde (CPU, OpenCV Farneback).
    Échantillonnage à 2fps pour rester rapide.
    """
    cap = cv2.VideoCapture(chemin_video)
    if not cap.isOpened():
        return np.array([0.0]), 25.0

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    nb_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duree_totale = nb_frames / fps
    pas = max(1, int(fps / fps_echantillon))

    scores = []
    prev_gris = None
    idx = 0

    while idx < nb_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            break

        petit = cv2.resize(frame, (320, 180))
        gris = cv2.cvtColor(petit, cv2.COLOR_BGR2GRAY)

        if prev_gris is not None:
            flow = cv2.calcOpticalFlowFarneback(
                prev_gris, gris, None,
                pyr_scale=0.5, levels=2, winsize=12,
                iterations=2, poly_n=5, poly_sigma=1.1, flags=0
            )
            mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
            scores.append(float(mag.mean()))
        else:
            scores.append(0.0)

        prev_gris = gris
        idx += pas

    cap.release()

    if not scores:
        return np.array([0.0]), fps

    scores_arr = np.array(scores)
    duree_ech = pas / fps

    scores_par_sec = []
    for sec in range(int(duree_totale) + 2):
        i0 = int(sec / duree_ech)
        i1 = min(int((sec + 1) / duree_ech), len(scores_arr))
        if i0 < len(scores_arr):
            scores_par_sec.append(float(scores_arr[i0:i1].mean()))
        else:
            scores_par_sec.append(0.0)

    return np.array(scores_par_sec), fps


def analyser_changements_scene(chemin_video: str) -> np.ndarray:
    """Détecte les changements de scène (différence inter-frames)."""
    cap = cv2.VideoCapture(chemin_video)
    if not cap.isOpened():
        return np.array([0.0])

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    nb_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duree_totale = nb_frames / fps
    pas = max(1, int(fps / 2))

    scores = []
    prev_gris = None
    idx = 0

    while idx < nb_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            break
        petit = cv2.resize(frame, (160, 90))
        gris = cv2.cvtColor(petit, cv2.COLOR_BGR2GRAY)
        if prev_gris is not None:
            diff = np.abs(gris.astype(float) - prev_gris.astype(float))
            scores.append(float(diff.mean()))
        else:
            scores.append(0.0)
        prev_gris = gris
        idx += pas

    cap.release()

    if not scores:
        return np.array([0.0])

    scores_arr = np.array(scores)
    duree_ech = pas / fps
    scores_par_sec = []
    for sec in range(int(duree_totale) + 2):
        i0 = int(sec / duree_ech)
        i1 = min(int((sec + 1) / duree_ech), len(scores_arr))
        if i0 < len(scores_arr):
            scores_par_sec.append(float(scores_arr[i0:i1].mean()))
        else:
            scores_par_sec.append(0.0)
    return np.array(scores_par_sec)


# ─── Scoring et sélection ─────────────────────────────────────────────────────

def normaliser(arr: np.ndarray) -> np.ndarray:
    mn, mx = arr.min(), arr.max()
    if mx - mn < 1e-8:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


def scores_en_segments(
    score_par_sec: np.ndarray,
    duree_segment: int,
    pas: int = 5
) -> List[Tuple[float, int, int]]:
    """Score chaque fenêtre glissante de duree_segment secondes."""
    longueur = len(score_par_sec)
    segments = []
    for debut in range(0, max(1, longueur - duree_segment), pas):
        fin = min(debut + duree_segment, longueur)
        if fin - debut < 10:
            continue
        score_moy = float(score_par_sec[debut:fin].mean())
        segments.append((score_moy, debut, fin))
    segments.sort(reverse=True, key=lambda x: x[0])
    return segments


def selectionner_sans_chevauchement(
    segments: List[Tuple[float, int, int]],
    nb_max: int,
    gap_min: int = 5
) -> List[Tuple[float, int, int]]:
    """Sélectionne les N meilleurs segments sans chevauchement."""
    selectionnes = []
    intervalles = []
    for score, debut, fin in segments:
        if len(selectionnes) >= nb_max:
            break
        chevauchement = any(
            not (fin + gap_min <= d or debut >= f + gap_min)
            for d, f in intervalles
        )
        if not chevauchement:
            selectionnes.append((score, debut, fin))
            intervalles.append((debut, fin))
    selectionnes.sort(key=lambda x: x[1])
    return selectionnes


# ─── Découpe ──────────────────────────────────────────────────────────────────

def decouper_clip(chemin_video: str, debut: float, fin: float, sortie: str) -> bool:
    """Découpe un clip via ffmpeg avec ré-encodage propre."""
    os.makedirs(os.path.dirname(sortie), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(debut),
        "-i", chemin_video,
        "-t", str(fin - debut),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "aac",
        sortie
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        return r.returncode == 0
    except Exception as e:
        logger.error(f"Erreur découpe : {e}")
        return False


# ─── Fonction principale ──────────────────────────────────────────────────────

def analyser_et_decouper(
    chemin_video: str,
    dossier_clips: str,
    categorie: str,
    config: Dict,
    callback=None
) -> List[Dict]:
    """
    Pipeline complet d'analyse et découpe.
    Retourne la liste des clips générés (dicts avec id, chemin, debut, fin, score).
    """
    def log(msg):
        logger.info(msg)
        if callback:
            callback(msg)

    cfg = config.get("analyse", {})
    duree_max = cfg.get("duree_clip_max_secondes", 53)
    nb_max = cfg.get("nb_clips_max_par_video", 5)
    poids_audio = cfg.get("poids_audio", {}).get(categorie, 0.5)
    poids_video = cfg.get("poids_video", {}).get(categorie, 0.5)

    os.makedirs(dossier_clips, exist_ok=True)

    # Durée de la vidéo (OpenCV d'abord, ffprobe en fallback si codec VP9/AV1)
    duree_totale = None
    fps_video = 25.0

    cap = cv2.VideoCapture(chemin_video)
    if cap.isOpened():
        fps_video = cap.get(cv2.CAP_PROP_FPS) or 25.0
        nb_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if nb_frames > 0:
            duree_totale = nb_frames / fps_video
        cap.release()

    if not duree_totale:
        # Fallback ffprobe (VP9, AV1, chemins Unicode non supportés par OpenCV)
        log("OpenCV ne peut pas lire ce codec → fallback ffprobe")
        try:
            import json as _json
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", chemin_video],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0:
                info = _json.loads(r.stdout)
                duree_totale = float(info.get("format", {}).get("duration", 0))
                for s in info.get("streams", []):
                    if s.get("codec_type") == "video":
                        fps_str = s.get("r_frame_rate", "25/1")
                        num, den = fps_str.split("/")
                        fps_video = float(num) / float(den)
                        break
        except Exception as e:
            log(f"ffprobe aussi échoué : {e}")

    if not duree_totale or duree_totale <= 0:
        log("❌ Impossible de lire la durée de la vidéo")
        return []

    log(f"Durée : {duree_totale:.1f}s | Catégorie : {categorie}")

    # Vidéo courte → conserver entière
    if duree_totale <= duree_max:
        log(f"Vidéo ≤ {duree_max}s → conservée telle quelle")
        nom_base = os.path.splitext(os.path.basename(chemin_video))[0]
        sortie = os.path.join(dossier_clips, f"{nom_base}_clip_001.mp4")
        if decouper_clip(chemin_video, 0, duree_totale, sortie):
            return [{"id": f"{nom_base}_clip_001", "chemin": sortie,
                     "debut_sec": 0, "fin_sec": duree_totale,
                     "duree": duree_totale, "score": 1.0, "etape": "decoupé"}]
        return []

    # Analyse audio
    log(f"Analyse audio ({categorie})...")
    score_audio = np.zeros(int(duree_totale) + 2)
    score_reaction = np.zeros(int(duree_totale) + 2)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        chemin_wav = tmp.name

    try:
        if extraire_audio(chemin_video, chemin_wav):
            res = charger_audio(chemin_wav)
            if res is not None:
                y, sr = res
                hop = 512

                if categorie == "musique":
                    sf_frames = analyser_audio_musique(y, sr)
                elif categorie == "sport":
                    sf_frames = analyser_audio_sport(y, sr)
                elif categorie == "humour":
                    sf_frames = analyser_audio_humour(y, sr)
                else:
                    sf_frames = analyser_audio_autre(y, sr)

                # Score réaction (moments drôles/gênants) — toutes catégories
                reaction_frames = analyser_audio_reaction(y, sr)

                frames_par_sec = sr / hop
                for sec in range(len(score_audio)):
                    i0 = int(sec * frames_par_sec)
                    i1 = min(int((sec + 1) * frames_par_sec), len(sf_frames))
                    if i0 < len(sf_frames):
                        score_audio[sec] = float(sf_frames[i0:i1].mean())

                for sec in range(len(score_reaction)):
                    i0 = int(sec * frames_par_sec)
                    i1 = min(int((sec + 1) * frames_par_sec), len(reaction_frames))
                    if i0 < len(reaction_frames):
                        score_reaction[sec] = float(reaction_frames[i0:i1].mean())
    finally:
        if os.path.exists(chemin_wav):
            os.remove(chemin_wav)

    # Analyse vidéo
    log("Analyse vidéo (changements de scène / mouvements)...")
    score_video = np.zeros(len(score_audio))

    if categorie in ("sport", "humour", "autre"):
        of, _ = analyser_video_optical_flow(chemin_video)
        sc = analyser_changements_scene(chemin_video)
        long = min(len(of), len(sc), len(score_video))
        of, sc = of[:long], sc[:long]
        if categorie == "sport":
            score_video[:long] = 0.7 * normaliser(of) + 0.3 * normaliser(sc)
        elif categorie == "humour":
            score_video[:long] = 0.4 * normaliser(of) + 0.6 * normaliser(sc)
        else:
            score_video[:long] = 0.5 * normaliser(of) + 0.5 * normaliser(sc)

    # Score final : 40% catégorie + 35% réaction (drôle/gênant) + 25% vidéo
    long = min(len(score_audio), len(score_video), len(score_reaction))
    score_final = (
        0.40 * normaliser(score_audio[:long]) +
        0.35 * normaliser(score_reaction[:long]) +
        0.25 * normaliser(score_video[:long])
    )

    # Sélection des segments
    log("Sélection des meilleurs segments...")
    candidats = scores_en_segments(score_final, duree_max, pas=5)
    retenus = selectionner_sans_chevauchement(candidats, nb_max, gap_min=5)

    if not retenus:
        retenus = [(1.0, 0, min(duree_max, int(duree_totale)))]

    # Découpe
    nom_base = os.path.splitext(os.path.basename(chemin_video))[0]
    clips = []
    for i, (score, debut, fin) in enumerate(retenus):
        nom = f"{nom_base}_clip_{i + 1:03d}.mp4"
        sortie = os.path.join(dossier_clips, nom)
        log(f"Clip {i+1}/{len(retenus)} : {debut}s → {fin}s")
        if decouper_clip(chemin_video, debut, fin, sortie):
            clips.append({
                "id": os.path.splitext(nom)[0],
                "chemin": sortie,
                "debut_sec": debut, "fin_sec": fin,
                "duree": fin - debut,
                "score": round(score, 4),
                "etape": "decoupé"
            })
            log(f"✅ {nom}")
        else:
            log(f"❌ Échec clip {i+1}")

    log(f"Analyse terminée : {len(clips)}/{len(retenus)} clips")
    return clips
