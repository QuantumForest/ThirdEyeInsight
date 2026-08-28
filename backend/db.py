"""
backend.db
----------
Accès à l'API ygoprodeck (base de cartes + images) et cache local.

Respecte les règles d'usage officielles de l'API (https://ygoprodeck.com/api-guide/) :
- Limite de 20 requêtes/seconde par IP (dépassement = blocage 1h) -> RateLimiter
- "Download and host images yourself" -> toutes les images sont mises en cache
  localement dans images/ et jamais rechargées si déjà présentes.
- "Please download and store all data pulled from this API locally" -> la base
  de cartes est mise en cache dans database/ygopro_db.json (rafraîchie 1x/jour).

Durcissements ajoutés par rapport à la version initiale :
- Intégrité : une réponse anormalement petite ou malformée n'écrase jamais le
  cache local valide.
- Taille : un fichier de cache local anormalement volumineux n'est pas chargé.
- Fiabilité : retry avec backoff exponentiel sur les erreurs réseau transitoires.
"""

import collections
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

import requests

from .logging_setup import get_logger
from .paths import DATABASE_DIR, IMAGES_DIR, ALL_MANAGED_DIRS, CACHE_DIR, hide_folder_windows
from .security import sanitize_id

logger = get_logger()

DB_PATH = os.path.join(DATABASE_DIR, "ygopro_db.json")

# La base complète compte plus de 13 000 cartes : en dessous de ce seuil, la
# réponse est considérée comme suspecte (erreur API, page d'erreur HTML, etc.)
# et n'écrase jamais le cache local existant.
MIN_EXPECTED_CARD_COUNT = 5000

# Garde-fou anti-corruption : la DB réelle pèse ~20-30 Mo, on refuse de charger
# un fichier local aberrant (limite large pour ne jamais gêner un usage normal).
MAX_DB_FILE_BYTES = 300 * 1024 * 1024

_HEADERS = {"User-Agent": "YGOProb/1.0 (+local desktop app; respects 20req/s rate limit)"}


class RateLimiter:
    """Limiteur de débit simple (fenêtre glissante 1s), thread-safe."""

    def __init__(self, max_per_second: int):
        self.max_per_second = max_per_second
        self._lock = threading.Lock()
        self._timestamps: "collections.deque[float]" = collections.deque()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] > 1.0:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_per_second:
                sleep_for = 1.0 - (now - self._timestamps[0])
                if sleep_for > 0:
                    time.sleep(sleep_for)
            self._timestamps.append(time.monotonic())


# Réglé confortablement sous la limite officielle de 20/s pour absorber la
# latence réseau et laisser de la marge à d'autres appels API concurrents.
_IMAGE_RATE_LIMITER = RateLimiter(max_per_second=12)


def init_folders() -> None:
    for folder in ALL_MANAGED_DIRS:
        os.makedirs(folder, exist_ok=True)
    # Pose l'attribut "caché" Windows sur .cache/ une fois qu'il existe — sans
    # effet sous Linux/macOS, où le préfixe point suffit déjà (voir paths.py).
    hide_folder_windows(CACHE_DIR)


def is_db_expired(db_path: str = DB_PATH) -> bool:
    return not os.path.exists(db_path) or (time.time() - os.path.getmtime(db_path)) > 86400


def download_ygopro_db(db_path: str = DB_PATH, max_attempts: int = 3) -> bool:
    """
    Télécharge la base de données de manière atomique (.tmp), avec retry/backoff
    exponentiel sur erreur réseau, et refuse d'écraser le cache local si la
    réponse semble incomplète ou malformée.
    """
    url = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, timeout=15, headers=_HEADERS)
            if response.status_code == 200:
                payload = response.json()
                data = payload.get('data') if isinstance(payload, dict) else None

                if not isinstance(data, list) or len(data) < MIN_EXPECTED_CARD_COUNT:
                    count = len(data) if isinstance(data, list) else "N/A"
                    logger.warning("Réponse API suspecte (%s cartes) - DB locale conservée telle quelle", count)
                    return False

                os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
                tmp_path = db_path + ".tmp"
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                os.replace(tmp_path, db_path)
                logger.info("Base de données mise à jour (%d cartes)", len(data))
                return True

            logger.warning("Téléchargement DB : HTTP %s (tentative %d/%d)", response.status_code, attempt, max_attempts)
        except requests.RequestException as e:
            last_exc = e
            logger.warning("Erreur réseau DB (tentative %d/%d) : %s", attempt, max_attempts, e)
        except (ValueError, KeyError) as e:
            logger.warning("Réponse API illisible (tentative %d/%d) : %s", attempt, max_attempts, e)

        if attempt < max_attempts:
            time.sleep(0.5 * (2 ** (attempt - 1)))

    if last_exc:
        logger.error("Échec du téléchargement de la DB après %d tentatives : %s", max_attempts, last_exc)
    return False


def load_local_db(db_path: str = DB_PATH) -> Tuple[list, dict]:
    """
    Charge la base locale. Rend la liste brute pour les recherches textuelles
    ET un dictionnaire indexé par ID pour la recherche O(1).
    Refuse de charger un fichier anormalement volumineux (protection mémoire).
    """
    if not os.path.exists(db_path):
        return [], {}

    try:
        size = os.path.getsize(db_path)
        if size > MAX_DB_FILE_BYTES:
            logger.error("Fichier DB anormalement volumineux (%d Mo) - chargement refusé", size // (1024 * 1024))
            return [], {}

        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        dict_db = {sanitize_id(c['id']): c for c in data if 'id' in c}
        return data, dict_db
    except Exception as e:
        logger.error("Erreur chargement DB locale : %s", e)
        return [], {}


def download_card_image(card: dict, max_attempts: int = 2) -> Optional[str]:
    """Télécharge une image de manière sécurisée et atomique, avec un petit retry."""
    clean_id = sanitize_id(card.get('id'))
    if not clean_id:
        return None

    img_path = os.path.join(IMAGES_DIR, f"{clean_id}.jpg")
    if os.path.exists(img_path):
        return img_path

    try:
        img_url = card['card_images'][0]['image_url_small']
    except (KeyError, IndexError, TypeError):
        return None

    for attempt in range(1, max_attempts + 1):
        try:
            res = requests.get(img_url, timeout=10, headers=_HEADERS)
            if res.status_code == 200:
                tmp_path = img_path + ".tmp"
                with open(tmp_path, 'wb') as f:
                    f.write(res.content)
                os.replace(tmp_path, img_path)
                return img_path
            logger.debug("Image HTTP %s pour la carte %s (tentative %d)", res.status_code, clean_id, attempt)
        except (requests.RequestException, OSError) as e:
            logger.debug("Erreur téléchargement image %s (tentative %d) : %s", clean_id, attempt, e)

        if attempt < max_attempts:
            time.sleep(0.3 * attempt)

    tmp_path = img_path + ".tmp"
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return None


def download_images_bulk(
    cards: List[dict],
    on_progress: Optional[Callable[[int, int], None]] = None,
    max_workers: int = 5,
) -> None:
    """
    Télécharge plusieurs images en parallèle (max_workers threads), en respectant
    la limite de 20 requêtes/seconde de l'API via un RateLimiter partagé.
    `on_progress(done, total)` est appelé après chaque image (thread appelant :
    à charge de l'appelant de revenir sur le thread UI si besoin, ex. via
    tkinter `.after()`).
    """
    total = len(cards)
    if total == 0:
        return

    done_counter = {"n": 0}
    counter_lock = threading.Lock()

    def _task(card: dict) -> None:
        _IMAGE_RATE_LIMITER.acquire()
        download_card_image(card)
        with counter_lock:
            done_counter["n"] += 1
            n = done_counter["n"]
        if on_progress:
            on_progress(n, total)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_task, cards))
