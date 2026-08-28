"""
calculs.py
----------
Moteur de probabilité : chargement du deck, loi hypergéométrique exacte et
simulation Monte-Carlo de la main d'ouverture (Aller en Premier / Second).

La simulation est vectorisée avec numpy plutôt qu'exécutée via une boucle
Python classique : à 500 000 itérations, la version vectorisée tourne en une
fraction de seconde là où la version boucle-par-boucle prenait plusieurs
secondes. Le principe :
  - on tire, pour TOUTES les itérations à la fois, 6 indices de cartes
    distincts par ligne (astuce de l'argpartition sur des clés aléatoires) ;
  - on "gather" en une seule opération les drapeaux de rôle (Starter,
    Handtrap, etc.) de ces mains, puis on calcule les agrégats par colonnes.
"""

import os
import random
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import hypergeom

from backend.logging_setup import get_logger
from backend.paths import DECKS_DIR

logger = get_logger()

CATEGORIES = ["Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick", "Pioche"]

Card = Dict[str, object]


def sanitize_id(card_id) -> str:
    """Assainit l'identifiant d'une carte pour éviter tout Path Traversal."""
    if not card_id:
        return ""
    clean = str(card_id).split('.')[0]
    return re.sub(r'[^a-zA-Z0-9_-]', '', clean)


def deck_df_to_list(df: pd.DataFrame) -> List[Card]:
    """
    Convertit un DataFrame déjà prêt (une ligne par carte unique, avec sa quantité)
    en une liste "à plat" d'une entrée par exemplaire physique de carte, c'est cette
    liste que la simulation Monte-Carlo pioche.

    Ne fait AUCUN filtrage de section : appelant responsable de ne fournir que les
    cartes qui doivent réellement être piochées dans la main d'ouverture (Main Deck,
    ou Main Deck modifié pour un scénario post-side).
    """
    deck: List[Card] = []
    uid = 0
    audit = {cat: 0 for cat in CATEGORIES}

    for _, row in df.iterrows():
        try:
            qte = max(1, int(row.get("Quantite", 1)))
        except (ValueError, TypeError):
            qte = 1

        clean_id = sanitize_id(row.get("ID", ""))

        for _ in range(qte):
            try:
                pioche_count = int(row.get("PiocheCount", 0))
            except (ValueError, TypeError):
                pioche_count = 0
            carte: Card = {
                "uid": uid,
                "id": clean_id,
                "nom": str(row.get("Nom", "Inconnue")),
                **{cat: (1 if str(row.get(cat, 0)) == '1' else 0) for cat in CATEGORIES},
                "pioche_count": max(0, min(3, pioche_count)),
            }
            deck.append(carte)
            for cat in CATEGORIES:
                audit[cat] += carte[cat]
            uid += 1

    logger.debug("Deck chargé pour simulation : %d cartes - %s", len(deck), audit)
    return deck


def charger_deck(chemin_csv: str) -> Optional[List[Card]]:
    """
    Charge le deck depuis un CSV et génère la structure de cartes de manière sécurisée.

    IMPORTANT : seules les cartes de la section "Main" sont chargées ici. Les cartes
    d'Extra Deck et de Side Deck ne font jamais partie de la pioche d'ouverture et ne
    doivent donc jamais entrer dans le calcul de probabilité / la simulation.
    Pour simuler un scénario post-side (Main modifié avec des cartes du Side), voir
    `deck_df_to_list`, utilisée directement sur un DataFrame en mémoire.
    """
    if not os.path.exists(chemin_csv):
        logger.error("Deck introuvable : %s", chemin_csv)
        return None

    try:
        with open(chemin_csv, 'r', encoding='utf-8', errors='replace') as f:
            first_line = f.readline()
            sep = ';' if ';' in first_line else ','

        df = pd.read_csv(chemin_csv, sep=sep, dtype={"ID": str})
    except Exception as e:
        logger.error("Erreur lors du chargement du fichier CSV (%s) : %s", chemin_csv, e)
        return None

    df.columns = df.columns.str.strip()

    if "Section" not in df.columns:
        df["Section"] = "Main"

    df = df[df["Section"] == "Main"]
    return deck_df_to_list(df)


def charger_combos(chemin_csv: str) -> list:
    """Charge le fichier JSON de règles de combos associé au deck CSV s'il existe."""
    import json
    chemin_combos = chemin_csv.replace(".csv", "_combos.json")
    if os.path.exists(chemin_combos):
        try:
            with open(chemin_combos, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.error("Erreur lors du chargement des combos : %s", e)
    return []


def verifier_combo_dans_main(main_ids: List[str], combo_pair) -> bool:
    """
    Version non-vectorisée (référence / tests unitaires) : vérifie si une main
    contient le combo à 2 cartes spécifié (y compris avec "ANY").
    La simulation vectorisée utilise `_combo_mask_vectorized`, logiquement
    équivalente mais appliquée à toutes les itérations en une fois.
    """
    if not isinstance(combo_pair, (list, tuple)) or len(combo_pair) < 2:
        return False

    c1, c2 = combo_pair[0], combo_pair[1]

    if c1 != "ANY" and c2 != "ANY":
        if c1 == c2:
            return main_ids.count(c1) >= 2
        return (c1 in main_ids) and (c2 in main_ids)

    target = c1 if c2 == "ANY" else c2
    if target == "ANY":
        return len(main_ids) >= 2

    return main_ids.count(target) >= 1


def _combo_mask_vectorized(hand5_ids: np.ndarray, combo_pair) -> np.ndarray:
    """Équivalent vectorisé de `verifier_combo_dans_main`, appliqué à toutes les mains à la fois."""
    n = hand5_ids.shape[0]
    if not isinstance(combo_pair, (list, tuple)) or len(combo_pair) < 2:
        return np.zeros(n, dtype=bool)

    c1, c2 = combo_pair[0], combo_pair[1]

    if c1 == "ANY" and c2 == "ANY":
        return np.ones(n, dtype=bool)
    if c1 == "ANY" or c2 == "ANY":
        target = c2 if c1 == "ANY" else c1
        return (hand5_ids == target).sum(axis=1) >= 1
    if c1 == c2:
        return (hand5_ids == c1).sum(axis=1) >= 2
    return ((hand5_ids == c1).sum(axis=1) >= 1) & ((hand5_ids == c2).sum(axis=1) >= 1)


def calcul_hypergeometrique(taille_deck: int, count_categorie: int, main_size: int = 5) -> Dict[str, float]:
    """Calcule les probabilités exactes via la loi hypergéométrique."""
    if taille_deck <= 0 or count_categorie <= 0:
        return {
            "p_at_least_1": 0.0, "p_at_least_2": 0.0, "p_exact_0": 100.0,
            "p_exact_1": 0.0, "p_exact_2": 0.0,
        }

    N = taille_deck
    K = min(count_categorie, N)
    n = min(main_size, N)

    p_0 = hypergeom.pmf(0, N, K, n)
    p_1 = hypergeom.pmf(1, N, K, n)
    p_2 = hypergeom.pmf(2, N, K, n)

    p_at_least_1 = (1.0 - p_0) * 100.0
    p_at_least_2 = (1.0 - (p_0 + p_1)) * 100.0

    return {
        "p_at_least_1": max(0.0, min(100.0, p_at_least_1)),
        "p_at_least_2": max(0.0, min(100.0, p_at_least_2)),
        "p_exact_0": max(0.0, min(100.0, p_0 * 100.0)),
        # Probabilités EXACTES (P(=1), P(=2)) — mutuellement exclusives entre
        # elles (et avec P(=0), P(=3), etc.), donc leur somme ne dépasse
        # JAMAIS 100%, contrairement à "au moins 1" + "au moins 2" qui se
        # chevauchent (avoir 2+ implique déjà avoir 1+).
        "p_exact_1": max(0.0, min(100.0, p_1 * 100.0)),
        "p_exact_2": max(0.0, min(100.0, p_2 * 100.0)),
    }


def run_simulation(
    deck: List[Card],
    chemin_csv: str = os.path.join(DECKS_DIR, "default_deck.csv"),
    iterations: int = 500_000,
    use_combos: bool = True,
    combos_rules: Optional[list] = None,
) -> Tuple[Dict[str, float], Dict[int, float], float]:
    """
    Exécute la simulation Monte-Carlo (vectorisée numpy). `deck` ne doit contenir
    que des cartes destinées à la pioche d'ouverture (Main Deck standard, ou Main
    Deck modifié pour un scénario post-side).

    Si `combos_rules` est fourni explicitement (ex. scénario post-side sans fichier
    _combos.json dédié), il est utilisé tel quel plutôt que rechargé depuis le disque.

    Retourne (stats_moyennes, résultats_premier, résultat_second). Voir
    `run_simulation_with_conditions` pour la variante qui calcule EN PLUS les
    conditions de victoire personnalisées ("Paramètres") sur le même tirage.
    """
    stats, first, second, _, _, _, _ = _run_simulation_core(
        deck, chemin_csv=chemin_csv, iterations=iterations, use_combos=use_combos, combos_rules=combos_rules
    )
    return stats, first, second


def run_simulation_with_conditions(
    deck: List[Card],
    chemin_csv: str = os.path.join(DECKS_DIR, "default_deck.csv"),
    iterations: int = 500_000,
    use_combos: bool = True,
    combos_rules: Optional[list] = None,
    conditions_first: Optional[list] = None,
    conditions_second: Optional[list] = None,
) -> Tuple[Dict[str, float], Dict[int, float], float, Dict[str, float], Dict[str, float], Optional[float], Optional[float]]:
    """
    Identique à `run_simulation`, mais calcule EN PLUS les conditions de victoire
    personnalisées ("Paramètres") sur le MÊME tirage Monte-Carlo — une seule
    simulation de 500 000 mains au lieu de deux tirages séparés (c'était la cause
    du ralentissement d'une analyse individuelle : `run_simulation` et l'ancienne
    fonction séparée `evaluate_custom_conditions` tiraient chacune leurs propres
    500 000 mains, doublant le temps de calcul à chaque clic).

    `conditions_first`/`conditions_second` : listes ORDONNÉES de conditions au
    format {"name": str, "clauses": [{"category": str, "min": int}, ...],
    "operator": "or"|"and"}. Les clauses D'UNE MÊME condition sont toujours
    combinées par ET. Le connecteur logique se trouve ENTRE CHAQUE CONDITION DE
    LA LISTE (pas entre ses clauses) : `operator` sur la 2ᵉ condition définit
    comment elle se combine avec la 1ʳᵉ, `operator` sur la 3ᵉ définit comment
    elle se combine avec le résultat cumulé des deux précédentes, etc. (pas de
    parenthésage — évaluation séquentielle dans l'ordre de la liste). La 1ʳᵉ
    condition n'a pas d'`operator` (rien à combiner avant elle). Par défaut
    "or" : la main est gagnante dès qu'AU MOINS UNE condition est vraie ;
    l'utilisateur peut changer un maillon précis en "and" (TOUTES les
    conditions jusque-là doivent être vraies). `conditions_first` est évalué
    sur des mains de 5 cartes, `conditions_second` sur des mains de 6 cartes —
    deux listes totalement indépendantes l'une de l'autre.

    Retourne (stats_moyennes, résultats_premier, résultat_second,
    résultats_conditions_premier, résultats_conditions_second,
    résultat_combiné_premier, résultat_combiné_second). Les résultats "conditions"
    sont des dicts {nom: pourcentage}. Les résultats combinés valent None s'il y
    a moins de 2 conditions pour ce côté (rien à combiner).
    """
    return _run_simulation_core(
        deck, chemin_csv=chemin_csv, iterations=iterations, use_combos=use_combos, combos_rules=combos_rules,
        conditions_first=conditions_first, conditions_second=conditions_second,
    )


def _run_simulation_core(
    deck: List[Card],
    chemin_csv: str = os.path.join(DECKS_DIR, "default_deck.csv"),
    iterations: int = 500_000,
    use_combos: bool = True,
    combos_rules: Optional[list] = None,
    conditions_first: Optional[list] = None,
    conditions_second: Optional[list] = None,
):
    """Implémentation partagée par `run_simulation` et `run_simulation_with_conditions` (un seul tirage)."""
    taille_deck = len(deck)
    # Il faut au moins 6 cartes pour tirer une main de 6 (5 + la carte "go second").
    if taille_deck < 6:
        return {"st": 0, "ex": 0, "ht": 0, "ah": 0, "bb": 0, "br": 0}, {0: 0, 1: 0, 2: 0, 3: 0}, 0, {}, {}, None, None

    if combos_rules is None:
        combos_rules = charger_combos(chemin_csv) if use_combos else []
    elif not use_combos:
        combos_rules = []

    categories_map = {"st": "Starter", "ex": "Extender", "ht": "Handtrap",
                       "ah": "Anti_Handtrap", "bb": "Boardbreaker", "br": "Brick"}
    stats_main = {}
    for key, cat_name in categories_map.items():
        count_cat = sum(c[cat_name] for c in deck)
        stats_main[key] = 5.0 * (count_cat / taille_deck)

    # --- Préparation des tableaux numpy ---
    flags = np.array([[c[cat] for cat in CATEGORIES] for c in deck], dtype=np.int8)  # (N, 6)
    ids_array = np.array([c["id"] for c in deck], dtype='<U20')  # (N,)

    rng = np.random.default_rng()

    # Tire 6 indices distincts par itération : pour chaque ligne, les 6 index
    # correspondant aux 6 plus petites clés aléatoires forment un sous-ensemble
    # uniforme sans remise (équivalent à random.sample, mais vectorisé).
    random_keys = rng.random((iterations, taille_deck), dtype=np.float32)
    drawn = np.argpartition(random_keys, 5, axis=1)[:, :6]  # (iterations, 6)

    hand6_flags = flags[drawn]          # (iterations, 6, 6catégories)
    hand5_flags = hand6_flags[:, :5, :]

    st5 = hand5_flags[:, :, 0].sum(axis=1)
    ex5 = hand5_flags[:, :, 1].sum(axis=1)
    ah5 = hand5_flags[:, :, 3].sum(axis=1)
    br5 = hand5_flags[:, :, 5].sum(axis=1)

    st6 = hand6_flags[:, :, 0].sum(axis=1)
    ht6 = hand6_flags[:, :, 2].sum(axis=1)
    bb6 = hand6_flags[:, :, 4].sum(axis=1)

    has_custom_combo = np.zeros(iterations, dtype=bool)
    if combos_rules:
        hand5_ids = ids_array[drawn[:, :5]]  # (iterations, 5)
        for rule in combos_rules:
            has_custom_combo |= _combo_mask_vectorized(hand5_ids, rule)

    base_ok = ((st5 >= 1) | has_custom_combo) & (br5 <= 2)
    capital_play = np.where(st5 >= 1, st5 + ex5 - 1, ex5)

    res_first = {}
    for x in (0, 1, 2, 3):
        disruptions_effectives = np.maximum(0, x - ah5)
        res_first[x] = int(np.count_nonzero(base_ok & (capital_play >= disruptions_effectives)))

    res_second = int(np.count_nonzero((st6 >= 1) & ((ht6 >= 2) | (bb6 >= 1))))

    first_results = {k: (v / iterations) * 100 for k, v in res_first.items()}
    second_results = (res_second / iterations) * 100

    def _evaluate_conditions_chain(conditions, hand_flags):
        """
        Évalue chaque condition, puis chaîne les conditions ENTRE ELLES dans
        l'ordre de la liste via le champ "operator" de chacune (sauf la
        première, qui n'en a pas) : combined = c0, puis
        combined = combined OP(ci) ci pour chaque condition suivante.

        À l'intérieur d'UNE condition, ses clauses se chaînent de la MÊME
        façon, via le champ "connector" de chaque clause (sauf la première
        clause de la condition, qui n'en a pas) : clause_mask = clause0, puis
        clause_mask = clause_mask CONNECTOR(clausei) clausei. Par défaut "and"
        (rétrocompatible avec les clauses sans "connector", qui étaient
        jusqu'ici toujours combinées par ET).

        Chaque clause compare le nombre de cartes d'une catégorie dans la main
        à une valeur, via un opérateur : ">", "<", "=", ">=" (par défaut) ou
        "<=". Rétrocompatibilité : une clause à l'ancien format (juste
        {"category", "min"}, sans "op"/"value") est interprétée comme
        {"op": ">=", "value": min}.
        """
        results = {}
        combined_mask = None
        for i, condition in enumerate(conditions or []):
            name = condition.get("name", "?")
            clauses = condition.get("clauses", [])
            mask = None
            for clause in clauses:
                cat_name = clause.get("category")
                op = clause.get("op", ">=")
                value = clause.get("value", clause.get("min", 1))
                if cat_name not in CATEGORIES:
                    continue
                cat_idx = CATEGORIES.index(cat_name)
                count = hand_flags[:, :, cat_idx].sum(axis=1)
                if op == ">":
                    clause_mask = count > value
                elif op == "<":
                    clause_mask = count < value
                elif op == "=":
                    clause_mask = count == value
                elif op == "<=":
                    clause_mask = count <= value
                else:  # ">=" par défaut
                    clause_mask = count >= value

                if mask is None:
                    mask = clause_mask
                elif clause.get("connector", "and") == "or":
                    mask = mask | clause_mask
                else:
                    mask = mask & clause_mask
            if mask is None:
                mask = np.ones(iterations, dtype=bool)
            results[name] = (float(np.count_nonzero(mask)) / iterations) * 100

            if combined_mask is None:
                combined_mask = mask
            elif condition.get("operator", "or") == "and":
                combined_mask = combined_mask & mask
            else:
                combined_mask = combined_mask | mask

        combined = None
        if conditions and len(conditions) >= 2 and combined_mask is not None:
            combined = (float(np.count_nonzero(combined_mask)) / iterations) * 100
        return results, combined

    custom_first, combined_first = _evaluate_conditions_chain(conditions_first, hand5_flags)
    custom_second, combined_second = _evaluate_conditions_chain(conditions_second, hand6_flags)

    return stats_main, first_results, second_results, custom_first, custom_second, combined_first, combined_second


def run_pioche_simulation(
    deck: List[Card],
    chemin_csv: str = os.path.join(DECKS_DIR, "default_deck.csv"),
    iterations: int = 300_000,
    use_combos: bool = True,
    combos_rules: Optional[list] = None,
    conditions_first: Optional[list] = None,
    conditions_second: Optional[list] = None,
) -> dict:
    """
    Simulation étendue du sous-onglet "Avec Pioche" : résout la pioche sur UN
    SEUL NIVEAU. Seules les cartes de catégorie "Pioche" présentes dans la main
    de DÉPART déclenchent un tirage de cartes supplémentaires (`pioche_count`,
    1 à 3 par carte) depuis le reste du deck. Si l'une des cartes piochées EN
    PLUS est elle-même une carte "Pioche", elle ne se déclenche PAS à son tour
    (pas de chaînage) — volontairement simple, pour garder le calcul léger. Le
    tirage est naturellement plafonné à la taille du deck si celui-ci venait à
    s'épuiser.

    Deux comptages de catégories sont dérivés de la main étendue :
    - le comptage COMPLET (une carte Pioche qui a aussi un autre rôle compte
      normalement pour cet autre rôle) — utilisé pour le taux de victoire et la
      résistance aux interruptions de ce sous-onglet ;
    - le comptage EXCLUANT les cartes Pioche elles-mêmes (uniquement les cartes
      qu'elles ont fait piocher comptent) — utilisé pour les Résultats
      personnalisés, puisque les cartes Pioche ne sont jamais des conditions de
      victoire en elles-mêmes.

    La section hypergéométrique et les moyennes en main de départ, elles,
    restent volontairement basées sur la main NON étendue (5 cartes), comme la
    page Analyse standard — seule "Pioche" y est ajoutée comme 7ᵉ catégorie.
    """
    taille_deck = len(deck)
    empty_result = {
        "stats": {"st": 0, "ex": 0, "ht": 0, "ah": 0, "bb": 0, "br": 0, "pi": 0},
        "first": {0: 0, 1: 0, 2: 0, 3: 0}, "second": 0.0,
        "hyper_by_cat": [], "custom_first": {}, "custom_second": {},
        "custom_combined_first": None, "custom_combined_second": None,
        "deck_cards": deck, "extra_draw_avg_first": 0.0, "extra_draw_avg_second": 0.0,
    }
    if taille_deck < 6:
        return empty_result

    if combos_rules is None:
        combos_rules = charger_combos(chemin_csv) if use_combos else []
    elif not use_combos:
        combos_rules = []

    flags = np.array([[c[cat] for cat in CATEGORIES] for c in deck], dtype=np.int8)  # (N, 7)
    pioche_count_arr = np.array([c.get("pioche_count", 0) for c in deck], dtype=np.int64)  # (N,)
    ids_array = np.array([c["id"] for c in deck], dtype='<U20')

    rng = np.random.default_rng()
    random_keys = rng.random((iterations, taille_deck), dtype=np.float32)
    order = np.argsort(random_keys, axis=1)  # ordre COMPLET de pioche par itération (équivalent deck mélangé)

    ordered_flags = flags[order]              # (iterations, taille_deck, 7)
    ordered_pioche_count = pioche_count_arr[order]  # (iterations, taille_deck)

    col_idx = np.arange(taille_deck)[None, :]  # (1, taille_deck)
    pioche_idx = CATEGORIES.index("Pioche")

    def _resolve_single_level(base_size):
        """
        Résout la pioche pour une main de base de `base_size` cartes, sur un
        seul niveau (voir docstring de run_pioche_simulation) : une seule
        opération vectorisée, sans boucle — plus léger que la résolution en
        chaîne à plusieurs niveaux.
        """
        in_base_hand = col_idx < base_size  # (1, taille_deck), broadcastable
        extra = (ordered_pioche_count * in_base_hand).sum(axis=1)  # (iterations,)
        return np.minimum(base_size + extra, taille_deck)

    def _tally(final_size, exclude_pioche):
        position_mask = col_idx < final_size[:, None]  # (iterations, taille_deck)
        if exclude_pioche:
            is_pioche = ordered_flags[:, :, pioche_idx].astype(bool)
            contribute = position_mask & (~is_pioche)
        else:
            contribute = position_mask
        return (ordered_flags * contribute[:, :, None]).sum(axis=1)  # (iterations, 7)

    def _evaluate_conditions_chain_from_tally(conditions, tally):
        """Comme la version interne de run_simulation_with_conditions, mais à
        partir d'un comptage par catégorie déjà calculé (main étendue), pas de
        drapeaux de main bruts."""
        results = {}
        combined_mask = None
        for condition in (conditions or []):
            name = condition.get("name", "?")
            clauses = condition.get("clauses", [])
            mask = None
            for clause in clauses:
                cat_name = clause.get("category")
                op = clause.get("op", ">=")
                value = clause.get("value", clause.get("min", 1))
                if cat_name not in CATEGORIES:
                    continue
                count = tally[:, CATEGORIES.index(cat_name)]
                if op == ">":
                    clause_mask = count > value
                elif op == "<":
                    clause_mask = count < value
                elif op == "=":
                    clause_mask = count == value
                elif op == "<=":
                    clause_mask = count <= value
                else:
                    clause_mask = count >= value

                if mask is None:
                    mask = clause_mask
                elif clause.get("connector", "and") == "or":
                    mask = mask | clause_mask
                else:
                    mask = mask & clause_mask
            if mask is None:
                mask = np.ones(iterations, dtype=bool)
            results[name] = (float(np.count_nonzero(mask)) / iterations) * 100

            if combined_mask is None:
                combined_mask = mask
            elif condition.get("operator", "or") == "and":
                combined_mask = combined_mask & mask
            else:
                combined_mask = combined_mask | mask

        combined = None
        if conditions and len(conditions) >= 2 and combined_mask is not None:
            combined = (float(np.count_nonzero(combined_mask)) / iterations) * 100
        return results, combined

    final_size_5 = _resolve_single_level(5)
    final_size_6 = _resolve_single_level(6)

    tally_full_5 = _tally(final_size_5, exclude_pioche=False)
    tally_full_6 = _tally(final_size_6, exclude_pioche=False)
    tally_excl_5 = _tally(final_size_5, exclude_pioche=True)
    tally_excl_6 = _tally(final_size_6, exclude_pioche=True)

    st5, ex5, ah5, br5 = tally_full_5[:, 0], tally_full_5[:, 1], tally_full_5[:, 3], tally_full_5[:, 5]
    st6, ht6, bb6 = tally_full_6[:, 0], tally_full_6[:, 2], tally_full_6[:, 4]

    has_custom_combo = np.zeros(iterations, dtype=bool)
    if combos_rules:
        # Détection sur la main ÉTENDUE (après résolution de la pioche), pas
        # seulement les 5 cartes d'ouverture : si une carte piochée en plus
        # complète un combo, il doit être détecté ici aussi. Les positions
        # au-delà de la main étendue de CHAQUE itération sont masquées à ""
        # (aucun ID de carte réel ne vaut cette chaîne), pour que la même
        # comparaison vectorisée les ignore naturellement — une main étendue
        # de taille variable n'a donc pas besoin d'un traitement séparé.
        ordered_ids_full = ids_array[order]  # (iterations, taille_deck)
        in_extended_hand = col_idx < final_size_5[:, None]
        extended_ids_masked = np.where(in_extended_hand, ordered_ids_full, "")
        for rule in combos_rules:
            has_custom_combo |= _combo_mask_vectorized(extended_ids_masked, rule)

    base_ok = ((st5 >= 1) | has_custom_combo) & (br5 <= 2)
    capital_play = np.where(st5 >= 1, st5 + ex5 - 1, ex5)

    res_first = {}
    for x in (0, 1, 2, 3):
        disruptions_effectives = np.maximum(0, x - ah5)
        res_first[x] = int(np.count_nonzero(base_ok & (capital_play >= disruptions_effectives)))
    res_second = int(np.count_nonzero((st6 >= 1) & ((ht6 >= 2) | (bb6 >= 1))))

    first_results = {k: (v / iterations) * 100 for k, v in res_first.items()}
    second_results = (res_second / iterations) * 100

    # -- Moyennes en main de départ (déterministe, main NON étendue — comme la
    # page standard —, avec Pioche en 7ᵉ catégorie) --
    categories_map = {"st": "Starter", "ex": "Extender", "ht": "Handtrap",
                       "ah": "Anti_Handtrap", "bb": "Boardbreaker", "br": "Brick", "pi": "Pioche"}
    stats_main = {key: 5.0 * (sum(c[cat] for c in deck) / taille_deck) for key, cat in categories_map.items()}

    # -- Hypergéométrique classique (main NON étendue, les 7 catégories dont Pioche) --
    hyper_by_cat = []
    for cat in CATEGORIES:
        count_cat = sum(c[cat] for c in deck)
        prob = calcul_hypergeometrique(taille_deck, count_cat, 5)
        hyper_by_cat.append((cat, prob["p_at_least_1"]))

    # -- Résultats personnalisés (main ÉTENDUE, cartes Pioche exclues du comptage) --
    custom_first, combined_first = _evaluate_conditions_chain_from_tally(conditions_first, tally_excl_5)
    custom_second, combined_second = _evaluate_conditions_chain_from_tally(conditions_second, tally_excl_6)

    return {
        "stats": stats_main,
        "first": first_results, "second": second_results,
        "hyper_by_cat": hyper_by_cat,
        "custom_first": custom_first, "custom_second": custom_second,
        "custom_combined_first": combined_first, "custom_combined_second": combined_second,
        "deck_cards": deck,
        "extra_draw_avg_first": float(final_size_5.mean() - 5),
        "extra_draw_avg_second": float(final_size_6.mean() - 6),
    }


def resolve_pioche_hand_grouped(deck: List[Card], opening_hand: List[Card]) -> List[Tuple[Card, List[Card]]]:
    """
    Résout la pioche pour UNE main déjà tirée (utilisé par le tirage interactif
    "Tirer une main" du sous-onglet Avec Pioche), sur UN SEUL NIVEAU : seules
    les cartes "Pioche" de la main de DÉPART font piocher des cartes
    supplémentaires. Si l'une des cartes piochées en plus est elle-même une
    carte "Pioche", elle ne se déclenche pas à son tour (pas de chaînage) —
    cohérent avec `run_pioche_simulation`.

    Retourne une liste de `(carte_pioche, [cartes_qu_elle_a_fait_piocher])` —
    UNE entrée par carte "Pioche" présente dans la main de départ, dans
    l'ordre où elles y apparaissent, pour que l'affichage puisse montrer
    explicitement QUELLE carte a fait piocher QUOI. Si le deck restant
    s'épuise en cours de route, la liste piochée par une carte peut être plus
    courte que son `pioche_count` (jamais d'erreur, jamais de doublon).
    """
    drawn_uids = {c["uid"] for c in opening_hand}
    remaining = [c for c in deck if c["uid"] not in drawn_uids]
    random.shuffle(remaining)

    groups: List[Tuple[Card, List[Card]]] = []
    pos = 0
    for card in opening_hand:
        if card.get("Pioche") != 1:
            continue
        drawn_by_this_card: List[Card] = []
        for _ in range(int(card.get("pioche_count", 0))):
            if pos >= len(remaining):
                break
            drawn_by_this_card.append(remaining[pos])
            pos += 1
        groups.append((card, drawn_by_this_card))
    return groups


def analyze_deck_extras(
    deck: List[Card],
    iterations: int = 300_000,
) -> dict:
    """
    Statistiques d'analyse supplémentaires, calculées de façon INDÉPENDANTE de
    `run_simulation_with_conditions`/`run_pioche_simulation` (fonction dédiée
    plutôt que d'étendre les fonctions existantes) — pour ne prendre AUCUN
    risque de régression sur le calcul standard déjà en place, qui est appelé
    à plusieurs endroits distincts de l'application.

    - "Taux de main morte" : probabilité de n'avoir NI Starter NI Extender en
      main d'ouverture (5 cartes) — la main la plus défavorable possible, où
      rien ne peut être joué.
    - "Distribution des boardbreakers en Second" : répartition du nombre de
      cartes "Boardbreaker" en main de 6 cartes (0, 1, ou 2+) — symétrique à la
      résistance aux interruptions déjà affichée côté "Aller en Premier", pour
      donner la même profondeur de lecture des deux côtés.

    Ni l'une ni l'autre de ces deux statistiques ne dépend des extensions
    Combos/Cartes Pioche (main NON étendue, pas de bonus de combo) — elles
    restent donc toujours les mêmes quelles que soient les cases cochées,
    comme les probabilités par rôle (hypergéométrique) et les moyennes en main.
    """
    taille_deck = len(deck)
    empty = {"dead_hand_rate": 0.0, "second_boardbreaker_dist": {0: 0.0, 1: 0.0, "2+": 0.0}}
    if taille_deck < 6:
        return empty

    flags = np.array([[c[cat] for cat in CATEGORIES] for c in deck], dtype=np.int8)

    rng = np.random.default_rng()
    random_keys = rng.random((iterations, taille_deck), dtype=np.float32)
    drawn = np.argpartition(random_keys, 5, axis=1)[:, :6]

    hand6_flags = flags[drawn]
    hand5_flags = hand6_flags[:, :5, :]

    st5 = hand5_flags[:, :, 0].sum(axis=1)
    ex5 = hand5_flags[:, :, 1].sum(axis=1)
    bb6 = hand6_flags[:, :, 4].sum(axis=1)

    dead_hand_mask = (st5 == 0) & (ex5 == 0)
    dead_hand_rate = (float(np.count_nonzero(dead_hand_mask)) / iterations) * 100

    second_boardbreaker_dist = {
        0: (float(np.count_nonzero(bb6 == 0)) / iterations) * 100,
        1: (float(np.count_nonzero(bb6 == 1)) / iterations) * 100,
        "2+": (float(np.count_nonzero(bb6 >= 2)) / iterations) * 100,
    }

    return {"dead_hand_rate": dead_hand_rate, "second_boardbreaker_dist": second_boardbreaker_dist}


def analyze_role_overlap(deck: List[Card]) -> dict:
    """
    Chevauchement de rôles : combien d'exemplaires du Main Deck comptent dans
    PLUSIEURS catégories à la fois (ex. une carte à la fois Starter ET
    Handtrap). Une info utile pour ne pas surestimer la diversité réelle d'un
    deck en lisant seulement les totaux par catégorie, qui peuvent compter
    plusieurs fois les mêmes cartes. Analyse de composition pure (pas de
    simulation), donc déterministe et instantanée.
    """
    role_cats = ["Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick"]
    total = len(deck)
    multi_role_count = 0
    for card in deck:
        roles_present = sum(1 for cat in role_cats if card.get(cat) == 1)
        if roles_present >= 2:
            multi_role_count += 1
    pct = (multi_role_count / total * 100) if total > 0 else 0.0
    return {"multi_role_count": multi_role_count, "multi_role_pct": pct, "total_cards": total}


def analyze_concentration(
    deck: List[Card],
    combos_rules: Optional[list] = None,
    iterations: int = 150_000,
) -> dict:
    """
    Analyse de concentration : pour chaque carte "Starter" unique et chaque
    combo, calcule le pourcentage de mains JOUABLES (main de 5 cartes) où
    cette pièce précise est présente — peu importe ce qu'il y a d'autre dans
    la main. Une pièce présente dans 60% des mains jouables est une pièce dont
    le deck dépend beaucoup ; une pièce qui n'apparaît que dans 5% des mains
    jouables est une pièce dont la perte (handtrap ciblée, par exemple)
    changerait peu de choses, car d'autres pièces prennent le relais la
    plupart du temps.

    (Version précédente, abandonnée : compter uniquement les mains où la pièce
    est la SEULE source de starter présente, sans aucune autre carte Starter
    ni combo. Ce critère est trop strict pour un deck avec plusieurs pièces
    redondantes — il écrase presque toutes les pièces vers 0%, y compris des
    pièces qui contribuent en réalité très souvent, simplement parce qu'elles
    sont rarement SEULES en main. Le taux de présence ci-dessus reste
    significatif même dans un deck redondant.)

    Structurel comme les probabilités par rôle : indépendant des cases
    Combos/Cartes Pioche actuellement cochées (les combos pris en compte sont
    toujours ceux configurés pour ce deck/scénario, pour révéler la structure
    réelle, pas seulement ce qui est affiché à l'instant).

    Retourne {"pieces": [{"id", "nom", "type", "presence_pct_of_playable"}, ...]
    triée par contribution décroissante, "total_playable_rate": float}.
    """
    taille_deck = len(deck)
    if taille_deck < 5:
        return {"pieces": [], "total_playable_rate": 0.0}

    flags = np.array([[c[cat] for cat in CATEGORIES] for c in deck], dtype=np.int8)
    ids_array = np.array([c["id"] for c in deck], dtype='<U20')
    starter_idx = CATEGORIES.index("Starter")

    rng = np.random.default_rng()
    random_keys = rng.random((iterations, taille_deck), dtype=np.float32)
    drawn = np.argpartition(random_keys, 4, axis=1)[:, :5]  # main d'ouverture (5 cartes) uniquement

    hand_flags = flags[drawn]
    hand_ids = ids_array[drawn]
    st5 = hand_flags[:, :, starter_idx].sum(axis=1)

    combos_rules = combos_rules or []
    combo_masks = {i: _combo_mask_vectorized(hand_ids, rule) for i, rule in enumerate(combos_rules)}
    any_combo_mask = np.zeros(iterations, dtype=bool)
    for m in combo_masks.values():
        any_combo_mask |= m

    playable_mask = (st5 >= 1) | any_combo_mask
    total_playable = int(np.count_nonzero(playable_mask))
    total_playable_rate = (total_playable / iterations * 100) if iterations else 0.0

    pieces = []

    unique_starter_ids = sorted({c["id"] for c in deck if c.get("Starter") == 1})
    for card_id in unique_starter_ids:
        card_present = (hand_ids == card_id).any(axis=1)
        presence_mask = card_present & playable_mask
        presence_count = int(np.count_nonzero(presence_mask))
        pct = (presence_count / total_playable * 100) if total_playable else 0.0
        card_name = next((c["nom"] for c in deck if c["id"] == card_id), card_id)
        pieces.append({"id": card_id, "nom": card_name, "type": "card", "presence_pct_of_playable": pct})

    for i, rule in enumerate(combos_rules):
        m = combo_masks[i]
        # Un combo satisfait implique déjà "main jouable" (voir playable_mask
        # ci-dessus) : compter directement m suffit, pas besoin de recroiser
        # avec playable_mask, le résultat serait identique.
        presence_count = int(np.count_nonzero(m))
        pct = (presence_count / total_playable * 100) if total_playable else 0.0
        combo_label = " + ".join(str(x) for x in rule[:2]) if isinstance(rule, (list, tuple)) else str(rule)
        pieces.append({"id": f"combo_{i}", "nom": combo_label, "type": "combo", "presence_pct_of_playable": pct})

    pieces.sort(key=lambda p: -p["presence_pct_of_playable"])
    return {"pieces": pieces, "total_playable_rate": total_playable_rate}
