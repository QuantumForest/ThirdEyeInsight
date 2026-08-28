"""
backend.deck
------------
Persistance des decks (CSV), des règles de combo et des scénarios de
sideboard nommés. Aucune donnée n'est jamais perdue silencieusement : toute
erreur de lecture est journalisée et retombe sur une structure vide sûre.
"""

import json
import os
import shutil
from typing import Dict, List, Optional

import pandas as pd

from .logging_setup import get_logger
from .paths import DECKS_DIR
from .security import DECK_COLUMNS, DECK_LIMITS, sanitize_filename, sanitize_id, sanitize_section
from .security import get_default_section

logger = get_logger()


def _deck_folder(deck_name: str) -> str:
    """
    Nom sécurisé du deck (via sanitize_filename) -> chemin de son sous-dossier
    dédié sous DECKS_DIR, créé automatiquement s'il n'existe pas encore. Chaque
    deck vit dans son propre dossier (deck.csv, combos.json, conditions.json,
    scenarios.json) plutôt que 4 fichiers séparés à plat partageant un préfixe
    de nom commun — plus simple à sauvegarder/déplacer/supprimer d'un bloc, et
    bien plus lisible en parcourant le dossier manuellement.
    """
    safe_name = sanitize_filename(deck_name)
    folder_name = safe_name[:-4] if safe_name.lower().endswith(".csv") else safe_name
    folder = os.path.join(DECKS_DIR, folder_name)
    os.makedirs(folder, exist_ok=True)
    return folder


def get_deck_path(deck_name: str) -> str:
    return os.path.join(_deck_folder(deck_name), "deck.csv")


def get_combos_path(deck_name: str) -> str:
    return os.path.join(_deck_folder(deck_name), "combos.json")


def get_conditions_path(deck_name: str) -> str:
    return os.path.join(_deck_folder(deck_name), "conditions.json")


def get_scenarios_path(deck_name: str) -> str:
    return os.path.join(_deck_folder(deck_name), "scenarios.json")


def _empty_deck_df() -> pd.DataFrame:
    return pd.DataFrame(columns=DECK_COLUMNS)


def load_deck_df(deck_name: str, db_dict: Optional[dict] = None) -> pd.DataFrame:
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
        logger.error("Erreur chargement deck '%s' : %s", deck_name, e)
        return _empty_deck_df()


def save_deck_df(df: pd.DataFrame, deck_name: str) -> None:
    path = get_deck_path(deck_name)
    for col in DECK_COLUMNS:
        if col not in df.columns:
            df[col] = "Main" if col == "Section" else 0
    df[DECK_COLUMNS].to_csv(path, index=False, sep=';')


def validate_deck_size(df: pd.DataFrame) -> Dict[str, dict]:
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
        result[section] = {"count": count, "min": min_c, "max": max_c, "valid": min_c <= count <= max_c}
    return result


def load_combos_list(deck_name: str) -> list:
    path = get_combos_path(deck_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Erreur chargement combos '%s' : %s", deck_name, e)
            return []
    return []


def save_combos_list(combos_list: list, deck_name: str) -> None:
    path = get_combos_path(deck_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(combos_list, f, indent=4)


def migrate_flat_decks_to_subfolders() -> None:
    """
    Migration légère, silencieuse, à usage unique : si d'anciens fichiers plats
    (deck_name.csv directement sous DECKS_DIR, avec ses _combos.json/etc. à
    côté) traînent encore d'une version antérieure à la structure en
    sous-dossiers, les regroupe dans leur nouveau sous-dossier dédié.
    Appelée à chaque démarrage — ne fait rien si aucun fichier plat n'est
    trouvé (cas normal après la première migration).
    """
    if not os.path.isdir(DECKS_DIR):
        return
    flat_csvs = [f for f in os.listdir(DECKS_DIR) if f.lower().endswith(".csv") and os.path.isfile(os.path.join(DECKS_DIR, f))]
    for csv_file in flat_csvs:
        deck_name = sanitize_filename(csv_file)
        folder = _deck_folder(deck_name)  # crée le sous-dossier
        base = csv_file[:-4]  # nom sans l'extension .csv, pour retrouver les fichiers associés
        moves = [
            (csv_file, "deck.csv"),
            (f"{base}_combos.json", "combos.json"),
            (f"{base}_conditions.json", "conditions.json"),
            (f"{base}_scenarios.json", "scenarios.json"),
        ]
        for old_name, new_name in moves:
            old_path = os.path.join(DECKS_DIR, old_name)
            new_path = os.path.join(folder, new_name)
            if os.path.exists(old_path) and not os.path.exists(new_path):
                try:
                    shutil.move(old_path, new_path)
                except Exception as e:
                    logger.error("Erreur migration '%s' vers '%s' : %s", old_path, new_path, e)


def list_available_decks() -> List[str]:
    entries = []
    if os.path.isdir(DECKS_DIR):
        for name in os.listdir(DECKS_DIR):
            folder = os.path.join(DECKS_DIR, name)
            if os.path.isdir(folder) and os.path.isfile(os.path.join(folder, "deck.csv")):
                entries.append(f"{name}.csv")
    if not entries:
        entries = ["default_deck.csv"]
        default_folder = _deck_folder("default_deck.csv")
        _empty_deck_df().to_csv(os.path.join(default_folder, "deck.csv"), index=False, sep=';')
    return [sanitize_filename(f) for f in entries]


def delete_deck(deck_name: str) -> None:
    """
    Supprime définitivement un deck : tout son sous-dossier dédié (deck,
    combos, scénarios, conditions personnalisées) en une seule opération.
    Silencieux si le dossier n'existe pas (rien à supprimer).
    """
    folder = _deck_folder(deck_name)
    try:
        if os.path.isdir(folder):
            shutil.rmtree(folder)
    except Exception as e:
        logger.error("Erreur suppression dossier '%s' : %s", folder, e)


def rename_deck(old_name: str, new_name: str) -> bool:
    """
    Renomme un deck en renommant tout son sous-dossier dédié en une seule
    opération. Retourne False (sans rien modifier) si un deck porte déjà le
    nouveau nom, True en cas de succès.
    """
    old_folder = _deck_folder(old_name)
    new_folder = _deck_folder(new_name)
    if old_folder == new_folder:
        return True
    if os.path.isdir(new_folder) and os.path.isfile(os.path.join(new_folder, "deck.csv")):
        return False
    if os.path.isdir(old_folder):
        if os.path.isdir(new_folder):
            os.rmdir(new_folder)  # dossier vide auto-créé par _deck_folder ci-dessus, retiré pour laisser place au rename
        os.rename(old_folder, new_folder)
    return True


# --- SCÉNARIOS POST-SIDE (sideboard nommés, indépendants du deck sauvegardé) ---

def load_scenarios_list(deck_name: str) -> list:
    path = get_scenarios_path(deck_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.error("Erreur chargement scénarios '%s' : %s", deck_name, e)
    return []


def save_scenarios_list(scenarios: list, deck_name: str) -> None:
    path = get_scenarios_path(deck_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(scenarios, f, indent=4)


def new_scenario(name: str, turn_order: Optional[str] = None) -> dict:
    """
    `turn_order` vaut "first", "second", ou None (scénario libre, sans contexte de
    tour associé). Sert uniquement à mettre en avant la bonne métrique (Aller en
    Premier/Second) dans le tableau de bord de résultats — n'affecte jamais le calcul.

    `combos` : règles de combo propres à CE scénario (en plus des combos globaux du
    deck). Utile quand le sideboard casse un combo global (carte retirée) et que
    l'utilisateur veut en définir un autre spécifique à ce plan de sideboard.
    """
    return {"name": name, "removals": {}, "additions": {}, "turn_order": turn_order, "combos": []}


# --- CONDITIONS DE VICTOIRE PERSONNALISÉES ("Paramètres") ---
#
# Une condition personnalisée est nommée par l'utilisateur et définie comme une
# conjonction (ET) de clauses "au moins N cartes de telle catégorie" :
#   {"name": "Jouer à travers les interruptions",
#    "clauses": [{"category": "Starter", "min": 1}, {"category": "Extender", "min": 1}],
#    "operator": "or"}
#
# Le connecteur logique se trouve ENTRE CHAQUE CONDITION DE LA LISTE (pas entre
# les clauses d'une même condition, et pas un réglage global unique par côté) :
# le champ "operator" d'une condition définit comment ELLE se combine avec le
# résultat cumulé de TOUTES les conditions qui la précèdent dans la liste
# ("or" par défaut, "and" si l'utilisateur le choisit). La toute première
# condition d'une liste n'a pas d'"operator" (rien à combiner avant elle).
# Évaluation séquentielle dans l'ordre de la liste, sans parenthésage :
# C1 [op de C2] C2 [op de C3] C3 ...
#
# Aller en Premier et Aller en Second ont chacun leur propre liste de
# conditions, totalement indépendante l'une de l'autre, évaluée sur la main de
# 5 cartes (Premier) ou 6 cartes (Second) respectivement.
# Format : {"first": {"conditions": [...]}, "second": {"conditions": [...]}}

def default_conditions() -> Dict[str, dict]:
    return {
        "first": {"conditions": []},
        "second": {"conditions": []},
    }


def load_conditions(deck_name: str) -> Dict[str, dict]:
    path = get_conditions_path(deck_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "first" in data and "second" in data:
                migrated = False
                for side in ("first", "second"):
                    value = data.get(side)
                    if isinstance(value, list):
                        # Tout ancien format (avant le connecteur, ou avant le nesting) :
                        # une simple liste de conditions.
                        conditions = value
                        migrated = True
                    elif isinstance(value, dict):
                        conditions = value.get("conditions", [])
                        old_combinator = value.get("combinator")
                        if old_combinator is not None:
                            # Ancien format avec un connecteur unique global pour tout le
                            # côté : applique-le à chaque maillon pour préserver EXACTEMENT
                            # le comportement précédent, l'utilisateur peut ensuite affiner
                            # maillon par maillon dans l'interface.
                            for i, cond in enumerate(conditions):
                                if i > 0 and "operator" not in cond:
                                    cond["operator"] = old_combinator
                            migrated = True
                    else:
                        conditions = []
                        migrated = True
                    data[side] = {"conditions": conditions}
                if migrated:
                    save_conditions(data, deck_name)
                return data
        except Exception as e:
            logger.error("Erreur chargement conditions '%s' : %s", deck_name, e)
    return default_conditions()


def save_conditions(conditions: Dict[str, dict], deck_name: str) -> None:
    path = get_conditions_path(deck_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(conditions, f, indent=4)
