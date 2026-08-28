# backend.py

import os
import re
import json
import time
import requests
import pandas as pd

# --- CONSTANTES DECK ---

# Colonnes du CSV/DataFrame de deck. "Section" vaut "Main", "Extra" ou "Side".
DECK_COLUMNS = [
    "ID", "Nom", "Quantite", "Section",
    "Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick"
]

# Règles officielles Yu-Gi-Oh! : (min, max) de cartes par section.
DECK_LIMITS = {
    "Main": (40, 60),
    "Extra": (0, 15),
    "Side": (0, 15),
}

MAX_COPIES_PER_CARD = 3

# Mots-clés du champ "type" de l'API ygoprodeck qui indiquent une carte d'Extra Deck.
_EXTRA_DECK_KEYWORDS = ("Fusion", "Synchro", "XYZ", "Link")

DB_PATH = os.path.join("database", "ygopro_db.json")


# --- FONCTIONS DE SÉCURITÉ APPLICATIVE (AppSec) ---

def sanitize_filename(filename, default="default_deck.csv", allowed_ext=".csv"):
    """Assainit un nom de fichier pour parer aux attaques Path Traversal (CWE-22)."""
    if not filename:
        return default
    base = os.path.basename(filename.replace('\\', '/'))
    clean = re.sub(r'[^a-zA-Z0-9_.-]', '', base)

    if not clean.lower().endswith(allowed_ext):
        clean += allowed_ext
    return clean if clean != allowed_ext else default

def sanitize_id(card_id):
    """Assainit un identifiant de carte Yu-Gi-Oh pour prévenir la manipulation de chemins."""
    if not card_id:
        return ""
    clean = str(card_id).split('.')[0]
    return re.sub(r'[^a-zA-Z0-9_-]', '', clean)

def sanitize_section(section):
    """Force une valeur de section à être une des 3 valeurs valides (repli sur 'Main')."""
    if section in DECK_LIMITS:
        return section
    return "Main"


# --- GESTION DES DOSSIERS DE PROJET ---

def init_folders():
    for folder in ['database', 'images', 'decks', 'exports', 'imports']:
        os.makedirs(folder, exist_ok=True)


# --- GESTION DE LA BASE DE DONNÉES YGOPRO (ANGLAIS UNIQUEMENT) ---
#
# L'API ygoprodeck ne fournit pas de traduction fiable des noms/textes de cartes.
# Une seule base de données (anglaise) est donc utilisée, quelle que soit la langue
# de l'interface : seuls les libellés de l'UI sont traduits (voir translations.py).

def is_db_expired(db_path=DB_PATH):
    return not os.path.exists(db_path) or (time.time() - os.path.getmtime(db_path)) > 86400

def download_ygopro_db(db_path=DB_PATH):
    """Télécharge la base de données de manière atomique (.tmp) pour éviter toute corruption."""
    try:
        url = "https://db.ygoprodeck.com/api/v7/cardinfo.php"
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            tmp_path = db_path + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(response.json()['data'], f, ensure_ascii=False, indent=4)
            os.replace(tmp_path, db_path)
            return True
    except requests.RequestException as e:
        print(f"Error updating DB: {e}")
    return False

def load_local_db(db_path=DB_PATH):
    """
    Rends la liste brute pour les recherches textuelles
    ET un dictionnaire indexé par ID pour la recherche O(1).
    """
    if os.path.exists(db_path):
        try:
            with open(db_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                dict_db = {sanitize_id(c['id']): c for c in data if 'id' in c}
                return data, dict_db
        except Exception as e:
            print(f"Error loading local DB: {e}")
    return [], {}

def download_card_image(card):
    """Télécharge une image de manière sécurisée et atomique."""
    clean_id = sanitize_id(card.get('id'))
    if not clean_id:
        return None

    img_path = os.path.join("images", f"{clean_id}.jpg")
    if not os.path.exists(img_path):
        try:
            img_url = card['card_images'][0]['image_url_small']
            res = requests.get(img_url, timeout=10)
            if res.status_code == 200:
                tmp_path = img_path + ".tmp"
                with open(tmp_path, 'wb') as f:
                    f.write(res.content)
                os.replace(tmp_path, img_path)
        except (requests.RequestException, KeyError, IndexError, OSError) as e:
            print(f"Image download error: {e}")
            if os.path.exists(img_path + ".tmp"):
                try: os.remove(img_path + ".tmp")
                except OSError: pass
            return None
    return img_path


# --- SECTION DE DECK (MAIN / EXTRA / SIDE) ---

def get_default_section(card_type):
    """
    Détermine automatiquement si une carte va au Main Deck ou à l'Extra Deck,
    d'après le champ 'type' renvoyé par l'API ygoprodeck.
    (Le Side Deck n'est jamais déduit automatiquement : c'est un choix du joueur.)
    """
    if not card_type:
        return "Main"
    if any(keyword in card_type for keyword in _EXTRA_DECK_KEYWORDS):
        return "Extra"
    return "Main"

def validate_deck_size(df):
    """
    Vérifie le nombre de cartes par section par rapport aux règles officielles.
    Retourne un dict : {section: {"count": int, "min": int, "max": int, "valid": bool}}
    """
    result = {}
    for section, (min_c, max_c) in DECK_LIMITS.items():
        if df.empty or "Section" not in df.columns:
            count = 0
        else:
            count = int(df.loc[df["Section"] == section, "Quantite"].sum())
        result[section] = {
            "count": count,
            "min": min_c,
            "max": max_c,
            "valid": min_c <= count <= max_c,
        }
    return result


# --- GESTION DES DECKS ET COMBOS (FS) ---

def get_deck_path(deck_name):
    safe_name = sanitize_filename(deck_name)
    return os.path.join("decks", safe_name)

def get_combos_path(deck_name):
    safe_name = sanitize_filename(deck_name)
    return os.path.join("decks", safe_name.replace(".csv", "_combos.json"))

def _empty_deck_df():
    return pd.DataFrame(columns=DECK_COLUMNS)

def load_deck_df(deck_name, db_dict=None):
    """
    Charge un deck depuis son CSV. Si le fichier a été créé avant l'ajout de la
    colonne 'Section' (anciens decks), elle est reconstruite automatiquement :
    - si db_dict est fourni, la section est déduite du type de carte (Main/Extra)
    - sinon, tout est considéré comme 'Main' par défaut
    """
    path = get_deck_path(deck_name)
    if not os.path.exists(path):
        return _empty_deck_df()
    try:
        df = pd.read_csv(path, sep=';', dtype={"ID": str})
        df = df.dropna(subset=["ID"])

        if "Section" not in df.columns:
            if db_dict:
                def infer(row):
                    card_info = db_dict.get(sanitize_id(row["ID"]))
                    return get_default_section(card_info.get("type") if card_info else None)
                df["Section"] = df.apply(infer, axis=1)
            else:
                df["Section"] = "Main"
        else:
            df["Section"] = df["Section"].apply(sanitize_section)

        for col in DECK_COLUMNS:
            if col not in df.columns:
                df[col] = 0
        return df[DECK_COLUMNS]
    except Exception as e:
        print(f"Error loading deck '{deck_name}': {e}")
        return _empty_deck_df()

def save_deck_df(df, deck_name):
    path = get_deck_path(deck_name)
    for col in DECK_COLUMNS:
        if col not in df.columns:
            df[col] = "Main" if col == "Section" else 0
    df[DECK_COLUMNS].to_csv(path, index=False, sep=';')

def load_combos_list(deck_name):
    path = get_combos_path(deck_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_combos_list(combos_list, deck_name):
    path = get_combos_path(deck_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(combos_list, f, indent=4)

def list_available_decks():
    files = [f for f in os.listdir("decks") if f.endswith(".csv")]
    if not files:
        files = ["default_deck.csv"]
        _empty_deck_df().to_csv(os.path.join("decks", "default_deck.csv"), index=False, sep=';')
    return [sanitize_filename(f) for f in files]


# --- EXPORT / IMPORT YDK (MAIN / EXTRA / SIDE) ---

def export_to_ydk(df, ydk_path):
    """Exporte le deck au format .ydk standard, avec ses 3 sections distinctes."""
    with open(ydk_path, "w", encoding="utf-8") as f:
        f.write("#created by YGOProb\n")
        for section, header in (("Main", "#main"), ("Extra", "#extra"), ("Side", "!side")):
            f.write(f"{header}\n")
            if not df.empty:
                subset = df[df["Section"] == section]
                for _, row in subset.iterrows():
                    card_id = sanitize_id(row["ID"])
                    for _ in range(int(row["Quantite"])):
                        f.write(f"{card_id}\n")

def parse_ydk_file(filepath, db_dict):
    """
    Importe un fichier .ydk en respectant ses 3 sections (#main / #extra / !side).
    Utilise le dictionnaire indexé O(1) pour retrouver nom + type de chaque carte.
    """
    section_ids = {"Main": [], "Extra": [], "Side": []}
    current_section = "Main"
    marker_map = {"#main": "Main", "#extra": "Extra", "!side": "Side"}

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            lower = line.lower()
            if lower in marker_map:
                current_section = marker_map[lower]
                continue
            if line.isdigit():
                section_ids[current_section].append(line)

    if not any(section_ids.values()):
        return pd.DataFrame()

    rows = []
    for section, ids in section_ids.items():
        if not ids:
            continue
        counts = pd.Series(ids).value_counts()
        for cid, count in counts.items():
            clean_cid = sanitize_id(cid)
            card_info = db_dict.get(str(clean_cid))
            name = card_info['name'] if card_info else f"Card {clean_cid}"
            rows.append({
                "ID": str(clean_cid),
                "Nom": name,
                "Quantite": int(count),
                "Section": section,
                "Starter": 0, "Extender": 0, "Handtrap": 0,
                "Anti_Handtrap": 0, "Boardbreaker": 0, "Brick": 0
            })

    return pd.DataFrame(rows, columns=DECK_COLUMNS)
