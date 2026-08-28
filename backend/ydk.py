"""
backend.ydk
-----------
Export/import au format .ydk (Main/Extra/Side), plus import depuis une URL :
- un lien "ydke://...!...!...!" (format ouvert utilisé par EDOPro, le deck
  builder ygoprodeck, Duelingbook, etc.)
- ou une URL pointant directement vers un fichier .ydk brut
"""

import base64
import os
import struct
from typing import Dict, List, Optional

import pandas as pd
import requests

from .logging_setup import get_logger
from .security import DECK_COLUMNS, sanitize_id

logger = get_logger()

_HEADERS = {"User-Agent": "YGOProb/1.0 (+local desktop app)"}


def export_to_ydk(df: pd.DataFrame, ydk_path: str) -> None:
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


def _section_ids_from_ydk_text(text: str) -> Dict[str, List[str]]:
    section_ids = {"Main": [], "Extra": [], "Side": []}
    current_section = "Main"
    marker_map = {"#main": "Main", "#extra": "Extra", "!side": "Side"}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower in marker_map:
            current_section = marker_map[lower]
            continue
        if line.isdigit():
            section_ids[current_section].append(line)

    return section_ids


def _section_ids_to_df(section_ids: Dict[str, List[str]], db_dict: dict) -> pd.DataFrame:
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
                "ID": str(clean_cid), "Nom": name, "Quantite": int(count), "Section": section,
                "Starter": 0, "Extender": 0, "Handtrap": 0,
                "Anti_Handtrap": 0, "Boardbreaker": 0, "Brick": 0,
                "Pioche": 0, "PiocheCount": 0,
            })

    return pd.DataFrame(rows, columns=DECK_COLUMNS)


def parse_ydk_text(text: str, db_dict: dict) -> pd.DataFrame:
    """Parse le contenu texte d'un fichier .ydk (Main/Extra/Side) en DataFrame de deck."""
    return _section_ids_to_df(_section_ids_from_ydk_text(text), db_dict)


def parse_ydk_file(filepath: str, db_dict: dict) -> pd.DataFrame:
    """Importe un fichier .ydk local en respectant ses 3 sections (#main / #extra / !side)."""
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    return parse_ydk_text(text, db_dict)


def decode_ydke(ydke_string: str) -> Dict[str, List[str]]:
    """
    Décode un lien ydke:// (format ouvert : 3 blocs base64 d'entiers uint32
    little-endian, un par exemplaire de carte, séparés par '!').
    Lève ValueError si la chaîne n'est pas un lien ydke:// valide.
    """
    ydke_string = ydke_string.strip()
    if not ydke_string.startswith("ydke://"):
        raise ValueError("Ce n'est pas un lien ydke:// valide")

    body = ydke_string[len("ydke://"):]
    # IMPORTANT : ne JAMAIS filtrer les segments vides ici. Le format encode 3
    # sections positionnellement (Main!Extra!Side!) ; si l'Extra Deck est vide
    # (deck sans Fusion/Synchro/XYZ/Link, très courant), le lien contient "!!"
    # et filtrer les segments vides décalerait le Side Deck sur la position Extra.
    parts = body.split("!")
    if parts and parts[-1] == "":  # dernier "!" de fin de format
        parts = parts[:-1]

    section_keys = ["Main", "Extra", "Side"]
    result: Dict[str, List[str]] = {"Main": [], "Extra": [], "Side": []}

    for key, part in zip(section_keys, parts):
        if part == "":
            continue
        padded = part + "=" * (-len(part) % 4)
        try:
            raw = base64.b64decode(padded)
        except Exception as e:
            raise ValueError(f"Section {key} illisible (base64 invalide) : {e}")

        count = len(raw) // 4
        if count == 0:
            continue
        ids = struct.unpack(f"<{count}I", raw[:count * 4])
        result[key] = [str(i) for i in ids]

    return result


def fetch_deck_from_url(url: str, db_dict: dict, timeout: int = 15) -> Optional[pd.DataFrame]:
    """
    Importe un deck depuis une URL. Accepte :
      - un lien ydke://...!...!...!  (décodé localement, aucune requête réseau)
      - une URL http(s) pointant vers un fichier .ydk brut (ex: raw GitHub, pastebin brut...)

    Retourne None en cas d'échec (réseau, format non reconnu), avec le détail
    dans les logs applicatifs plutôt qu'une exception qui remonterait à l'UI.
    """
    url = url.strip()

    if url.startswith("ydke://"):
        try:
            sections = decode_ydke(url)
            df = _section_ids_to_df(sections, db_dict)
            if df.empty:
                logger.warning("Lien ydke:// décodé mais vide")
                return None
            return df
        except ValueError as e:
            logger.error("Échec décodage ydke:// : %s", e)
            return None

    if not (url.startswith("http://") or url.startswith("https://")):
        logger.error("URL d'import non reconnue (ni ydke://, ni http(s)://) : %s", url)
        return None

    try:
        response = requests.get(url, timeout=timeout, headers=_HEADERS)
        response.raise_for_status()
        df = parse_ydk_text(response.text, db_dict)
        if df.empty:
            logger.warning("URL récupérée mais aucun contenu .ydk reconnu : %s", url)
            return None
        return df
    except requests.RequestException as e:
        logger.error("Échec de récupération de l'URL '%s' : %s", url, e)
        return None
