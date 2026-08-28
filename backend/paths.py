r"""
backend.paths
-------------
Constantes de chemins centralisées, organisées par ce que l'utilisateur a
vraiment besoin de voir/atteindre :

- decks/ : les decks eux-mêmes (+ leurs combos/conditions/scénarios dans un
  sous-dossier par deck) — le seul contenu réellement irremplaçable, donc
  visible et à la racine.
- exports/ et imports/ : dossiers d'échange avec l'utilisateur (fichiers
  qu'il dépose ou récupère), donc visibles eux aussi.
- .cache/ : tout le reste (base de cartes, images téléchargées, logs) —
  entièrement régénérable/re-téléchargeable, jamais quelque chose que
  l'utilisateur a besoin de parcourir ou de modifier à la main, donc CACHÉ
  (préfixe point, respecté nativement par Linux/macOS ; sous Windows, où le
  préfixe point n'a aucun effet particulier, l'attribut "caché" du système de
  fichiers est posé explicitement sur ce dossier — voir _hide_folder_windows).

    YGOProb/
    ├── main.py, calculs.py, translations.py, backend/...
    ├── decks/           (DECKS_DIR)
    │   └── NomDuDeck/   (un sous-dossier par deck : deck.csv, combos.json,
    │                      conditions.json, scenarios.json)
    ├── exports/         (EXPORTS_DIR)
    ├── imports/         (IMPORTS_DIR)
    └── .cache/          (CACHE_DIR, caché)
        ├── database/    (DATABASE_DIR)
        ├── images/      (IMAGES_DIR)
        └── logs/        (LOGS_DIR)

Chemins ABSOLUS, ancrés à l'emplacement réel de ce fichier (donc à la racine de
l'application), et non au répertoire de travail courant (cwd) du processus :
avec des chemins relatifs, toute action qui changerait le cwd en cours
d'exécution (ex. une boîte de dialogue native d'ouverture de fichier, qui sur
certaines plateformes modifie silencieusement le cwd du processus) ferait
ensuite pointer toutes les lectures/écritures de decks vers un tout autre
dossier, sans aucune erreur visible.

Cas particulier PyInstaller (--onefile) : à l'exécution, sys.frozen vaut True
et __file__ pointe vers le dossier d'extraction TEMPORAIRE de l'exécutable
(ex. C:\Users\...\AppData\Local\Temp\_MEIxxxxx\...), supprimé à la fermeture
de l'application — utiliser __file__ dans ce cas ferait perdre tous les decks
à chaque fermeture. sys.executable, lui, pointe toujours vers l'emplacement
RÉEL du .exe, là où l'utilisateur l'a placé : c'est ce qu'on utilise pour que
l'application reste portable (dossiers créés à côté du .exe, pas dans un
dossier temporaire qui disparaît).
"""

import ctypes
import os
import sys

if getattr(sys, "frozen", False):
    _APP_ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    _APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/ -> racine YGOProb/

DECKS_DIR = os.path.join(_APP_ROOT, "decks")
EXPORTS_DIR = os.path.join(_APP_ROOT, "exports")
IMPORTS_DIR = os.path.join(_APP_ROOT, "imports")

CACHE_DIR = os.path.join(_APP_ROOT, ".cache")
DATABASE_DIR = os.path.join(CACHE_DIR, "database")
IMAGES_DIR = os.path.join(CACHE_DIR, "images")
LOGS_DIR = os.path.join(CACHE_DIR, "logs")

ALL_MANAGED_DIRS = [DECKS_DIR, DATABASE_DIR, IMAGES_DIR, LOGS_DIR, EXPORTS_DIR, IMPORTS_DIR]


def hide_folder_windows(path: str) -> None:
    """
    Pose l'attribut "caché" du système de fichiers Windows sur `path`. Sans
    effet ailleurs que sous Windows (no-op silencieux) : sur Linux/macOS, le
    préfixe point du nom de dossier suffit déjà à le cacher par convention.
    Échec silencieux si l'appel Windows échoue (ex. permissions) — un dossier
    caché resté visible n'est jamais bloquant pour l'application.
    """
    if os.name != "nt":
        return
    try:
        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass
