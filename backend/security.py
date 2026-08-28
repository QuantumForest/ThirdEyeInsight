"""
backend.security
-----------------
Sanitisation des entrées utilisateur/fichier et constantes partagées liées
aux règles de deck-building Yu-Gi-Oh!. Aucune dépendance réseau ni disque ici :
ce module est pur et donc trivial à tester unitairement.
"""

import os
import re
from typing import Optional

# Colonnes du CSV/DataFrame de deck. "Section" vaut "Main", "Extra" ou "Side".
# "Pioche"/"PiocheCount" : catégorie "carte qui fait piocher d'autres cartes"
# (combien, de 1 à 3) — utilisée pour la catégorisation en Construction du Deck
# uniquement. Le calcul de probabilité qui en tiendrait compte n'est pas encore
# fiable et a été retiré de l'interface pour l'instant.
DECK_COLUMNS = [
    "ID", "Nom", "Quantite", "Section",
    "Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick",
    "Pioche", "PiocheCount",
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


def sanitize_filename(filename: Optional[str], default: str = "default_deck.csv", allowed_ext: str = ".csv") -> str:
    """Assainit un nom de fichier pour parer aux attaques Path Traversal (CWE-22)."""
    if not filename:
        return default
    base = os.path.basename(filename.replace('\\', '/'))
    clean = re.sub(r'[^a-zA-Z0-9_.-]', '', base)

    if not clean.lower().endswith(allowed_ext):
        clean += allowed_ext
    return clean if clean != allowed_ext else default


def sanitize_id(card_id) -> str:
    """Assainit un identifiant de carte Yu-Gi-Oh pour prévenir la manipulation de chemins."""
    if not card_id:
        return ""
    clean = str(card_id).split('.')[0]
    return re.sub(r'[^a-zA-Z0-9_-]', '', clean)


def sanitize_section(section: Optional[str]) -> str:
    """Force une valeur de section à être une des 3 valeurs valides (repli sur 'Main')."""
    if section in DECK_LIMITS:
        return section
    return "Main"


def get_default_section(card_type: Optional[str]) -> str:
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
