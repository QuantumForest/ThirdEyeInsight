# main.py

import os
import sys
import re
import copy
import random
import collections
import subprocess
import threading
import pandas as pd
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont
from tkinter import filedialog, messagebox

import calculs
from translations import TRANSLATIONS
import backend

# Sections réellement gérées par l'interface de construction du deck.
# L'Extra Deck n'est jamais piocher dans une main d'ouverture : il n'a donc pas
# sa place ici. S'il est présent (import d'un .ydk existant), il est conservé
# silencieusement dans self.df pour ne pas casser un futur export, mais n'est
# ni affiché, ni éditable, ni utilisé dans aucun calcul.
EDIT_SECTIONS = ["Main", "Side"]

# Icônes utilisées dans le tableau de bord d'analyse (une par rôle de carte)
CATEGORY_ICONS = {
    "Starter": "🔑",
    "Extender": "🔗",
    "Handtrap": "🪤",
    "Anti_Handtrap": "🛡️",
    "Boardbreaker": "💥",
    "Brick": "🧱",
    "Pioche": "📚",
}

# Catégories de premier niveau du filtre de recherche avancée (clé interne -> détection sur card['type'])
ADV_CATEGORIES = ["Monster", "Spell", "Trap"]

# Sous-types de second niveau (valeurs réelles du champ 'race' de l'API ygoprodeck pour Magies/Pièges)
SPELL_SUBTYPES = ["Normal", "Quick-Play", "Continuous", "Field", "Ritual", "Equip"]
TRAP_SUBTYPES = ["Normal", "Continuous", "Counter"]

# Sous-types de monstre : détectés par sous-chaîne dans card['type'] (ex: "Pendulum
# Tuner Effect Monster" correspond à la fois à Effect, Tuner ET Pendulum).
MONSTER_SUBTYPES = ["Normal", "Effect", "Ritual", "Fusion", "Synchro", "XYZ", "Link",
                    "Pendulum", "Tuner", "Toon", "Spirit", "Union", "Gemini", "Flip"]

# Scénarios pré-créés, couvrant les 4 situations fondamentales après une Game 1
# (qui décide qui commence en G2 dépend des règles officielles : le perdant de la
# game précédente choisit d'aller en premier ou en second). Chaque entrée :
# (clé de traduction du nom, turn_order "first"/"second" mis en avant dans l'analyse).
STANDARD_SCENARIOS = [
    ("scenario_tpl_won_opp_first", "second"),
    ("scenario_tpl_won_i_first", "first"),
    ("scenario_tpl_lost_i_first", "first"),
    ("scenario_tpl_lost_opp_first", "second"),
]

# Conditions personnalisées pré-remplies au tout premier chargement d'un deck
# (jamais réinjectées ensuite, même si l'utilisateur les supprime toutes — voir
# _seed_default_conditions_if_needed). Définies avec l'utilisateur pour refléter
# une réalité de jeu cohérente :
# - Aller en Premier : une main jouable de base, une main résiliente à une
#   interruption (starter + extender de secours), et une main "propre" sans
#   carte morte.
# - Aller en Second : la présence d'interaction à elle seule (handtraps OU
#   boardbreaker), et séparément la main complète qui interagit ET rebondit
#   avec un starter (ces deux-là sont affichées côte à côte, librement
#   modifiables ou supprimables par l'utilisateur ensuite).
# Format de chaque clause : (catégorie, opérateur, valeur, connecteur_avec_la_clause_precedente).
# Le connecteur vaut None pour la toute première clause d'une condition (rien à
# combiner avant elle).
DEFAULT_CONDITIONS_FIRST = [
    ("default_condition_playable_hand", [("Starter", ">=", 1, None)]),
    ("default_condition_resist_interruption", [("Starter", ">=", 1, None), ("Extender", ">=", 1, "and")]),
    ("default_condition_clean_hand", [("Brick", "=", 0, None)]),
]
DEFAULT_CONDITIONS_SECOND = [
    ("default_condition_has_interaction", [("Handtrap", ">=", 2, None), ("Boardbreaker", ">=", 1, "or")]),
    ("default_condition_interact_and_bounce", [
        ("Handtrap", ">=", 2, None), ("Boardbreaker", ">=", 1, "or"), ("Starter", ">=", 1, "and"),
    ]),
]

# Opérateurs de comparaison disponibles pour une clause de condition personnalisée
# ("Paramètres") : (valeur interne stockée dans les données, symbole affiché).
# Symboles universels, pas besoin de traduction par langue.
OPERATORS = [(">=", "≥"), ("<=", "≤"), (">", ">"), ("<", "<"), ("=", "=")]
OPERATOR_DISPLAY = {internal: display for internal, display in OPERATORS}
OPERATOR_FROM_DISPLAY = {display: internal for internal, display in OPERATORS}


def _card_category(card_type):
    """Classe une carte en Monster / Spell / Trap d'après son champ 'type' brut (API, en anglais)."""
    if not card_type:
        return None
    if "Monster" in card_type:
        return "Monster"
    if card_type == "Spell Card":
        return "Spell"
    if card_type == "Trap Card":
        return "Trap"
    return None


class DeckApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        backend.init_folders()
        # Migration ponctuelle et silencieuse : regroupe d'éventuels anciens
        # decks à plat (d'avant la structure en sous-dossiers) dans leur
        # nouveau sous-dossier dédié. Ne fait rien si aucun fichier plat n'est
        # trouvé (cas normal après la première exécution).
        backend.migrate_flat_decks_to_subfolders()

        self.lang = "FR"
        self.geometry("1500x900")
        self.minsize(1200, 750)

        self.selected_card_id = None
        self.selected_card_name = None
        self.selected_card_type = ""     # utilisé pour détecter/bloquer l'Extra Deck
        self._card_is_extra_blocked = False
        self.matches = []
        self.db_data = []
        self.db_dict = {}  # Index pour accès instantané O(1)
        self.current_deck_name = "default_deck.csv"
        self.custom_combos = []
        self.custom_conditions = {"first": [], "second": []}
        self.df = pd.DataFrame()

        # Scénarios de sideboard nommés (Post-Side). Chacun est indépendant du
        # deck sauvegardé : {"name": str, "removals": {id: qty}, "additions": {id: qty}}.
        self.scenarios = []
        # Sélection dans l'onglet "Combos & Scénarios" (quel scénario est en cours d'édition)
        self.active_scenario_index = None
        # Sélection dans l'onglet "Analyse" (quoi analyser) : None = Deck Actuel (Game 1)
        self.analysis_target = None
        self.compare_mode = False
        self.compare_selection = []  # jusqu'à 2 éléments : None (Deck Actuel) ou un index de scénario
        self._last_deck_cards = []
        self._last_render_data = None
        self._hand_draw_slots = {}  # slot -> {"frame": widget, "deck_cards": [...]}
        self._scenario_unsaved_changes = False
        self._current_page_key = "build"
        self._scenario_row_widgets = {}  # (colonne, card_id) -> {"frame":..., "label":...}
        self._deck_list_row_widgets = {}  # (section, card_id) -> {"frame":..., "btn_text":...}
        self._compare_checkbox_widgets = {}  # key (None ou index scenario) -> CTkCheckBox
        self._deck_unsaved_changes = False

        # Caches de performance
        self._search_timer = None
        self.img_cache = collections.OrderedDict()  # cache LRU borné (voir get_cached_thumb)
        self._img_cache_max_size = 300
        self._image_download_in_progress = False
        self._simulation_running = False

        # Un seul chargement de la base de données : elle est TOUJOURS en anglais,
        # quelle que soit la langue de l'interface (l'API ne fournit pas de
        # traduction fiable des cartes).
        self.check_and_update_db()

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # --- SÉLECTEUR DE PAGE (Construction / Analyse) ---
        self.page_switch = ctk.CTkSegmentedButton(self, values=[], command=self._on_page_switch, font=("Arial", 16))
        self.page_switch.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))

        # --- BANDEAU DU DECK ACTIF : bien visible, sur sa propre ligne, sur
        # TOUTES les pages (pas seulement Construction) pour toujours savoir sans
        # ambiguïté sur quel deck on travaille. ---
        self.frame_active_deck_banner = ctk.CTkFrame(self, fg_color=("gray85", "gray20"), corner_radius=8)
        self.frame_active_deck_banner.grid(row=1, column=0, sticky="ew", padx=10, pady=(6, 0))
        self.lbl_active_deck_global = ctk.CTkLabel(
            self.frame_active_deck_banner, text="", font=("Arial", 28, "bold"), text_color=("gray15", "gray90")
        )
        self.lbl_active_deck_global.pack(pady=6)

        self.page_container = ctk.CTkFrame(self, fg_color="transparent")
        self.page_container.grid(row=2, column=0, sticky="nsew", padx=5, pady=(5, 5))
        self.page_container.grid_rowconfigure(0, weight=1)
        self.page_container.grid_columnconfigure(0, weight=1)

        self.page_build = ctk.CTkFrame(self.page_container, fg_color="transparent")
        self.page_build.grid(row=0, column=0, sticky="nsew")
        self.page_combos = ctk.CTkFrame(self.page_container, fg_color="transparent")
        self.page_combos.grid(row=0, column=0, sticky="nsew")
        self.page_scenarios = ctk.CTkFrame(self.page_container, fg_color="transparent")
        self.page_scenarios.grid(row=0, column=0, sticky="nsew")
        self.page_analysis = ctk.CTkFrame(self.page_container, fg_color="transparent")
        self.page_analysis.grid(row=0, column=0, sticky="nsew")

        self._build_page_construction()
        self._build_page_combos()
        self._build_page_scenarios()
        self._build_page_analysis()

        # Initialisations
        self.refresh_deck_list_menu()
        self.load_data()
        self.update_language_texts()
        self.page_build.tkraise()
        self.refresh_ui()
        self.download_missing_images_async()

    # ==================================================================
    # PAGE 1 : CONSTRUCTION DU DECK
    # ==================================================================
    def _build_page_construction(self):
        self.page_build.grid_columnconfigure(0, weight=3, minsize=340)
        self.page_build.grid_columnconfigure(1, weight=6, minsize=600)
        self.page_build.grid_rowconfigure(0, weight=1)

        # --- Colonne gauche : recherche, ajout de carte, combos ---
        self.frame_filters = ctk.CTkScrollableFrame(self.page_build)
        self.frame_filters.grid(row=0, column=0, padx=(0, 5), sticky="nsew")

        self.lbl_add_edit = ctk.CTkLabel(self.frame_filters, text="", font=("Arial", 22, "bold"))
        self.lbl_add_edit.pack(pady=5)

        # Conteneur dédié en grid() pour la zone de recherche : grid_remove()/grid()
        # est robuste pour montrer/masquer un widget (contrairement à pack(before=...),
        # qui casse avec les CTkScrollableFrame imbriqués).
        self.search_container = ctk.CTkFrame(self.frame_filters, fg_color="transparent")
        self.search_container.pack(fill="x", padx=0, pady=0)
        self.search_container.grid_columnconfigure(0, weight=1)
        search_container = self.search_container

        self.search_entry = ctk.CTkEntry(search_container, font=("Arial", 17))
        self.search_entry.grid(row=0, column=0, sticky="ew", pady=5, padx=5)
        self.search_entry.bind("<KeyRelease>", self.on_search_key_release)

        self.btn_toggle_adv_search = ctk.CTkButton(
            search_container, text="", fg_color="transparent", border_width=1,
            text_color=("black", "white"), hover_color=("gray80", "gray25"),
            command=self.toggle_advanced_search, font=("Arial", 16)
        )
        self.btn_toggle_adv_search.grid(row=1, column=0, sticky="ew", pady=(0, 5), padx=5)

        self._adv_search_visible = False
        self.frame_adv_search = ctk.CTkFrame(search_container, fg_color=("gray88", "gray20"), corner_radius=10)
        self.frame_adv_search.grid(row=2, column=0, sticky="ew", pady=(0, 5), padx=5)
        self.frame_adv_search.grid_remove()  # masqué par défaut, grid() le réaffichera à sa place exacte

        # Niveau 1 : Catégorie (Tous / Monstre / Magie / Piège)
        self.lbl_adv_category = ctk.CTkLabel(self.frame_adv_search, text="", font=("Arial", 16))
        self.lbl_adv_category.pack(anchor="w", padx=10, pady=(8, 0))
        self.adv_category_menu = ctk.CTkOptionMenu(self.frame_adv_search, values=[], command=self._on_adv_category_change, font=("Arial", 16))
        self.adv_category_menu.pack(fill="x", padx=10, pady=(0, 5))

        # Niveau 2 : reconstruit dynamiquement selon la catégorie choisie
        # (range ATK/DEF/Niveau pour Monstre, sous-type pour Magie/Piège, rien pour Tous)
        self.frame_adv_dynamic = ctk.CTkFrame(self.frame_adv_search, fg_color="transparent")
        self.frame_adv_dynamic.pack(fill="x", padx=10, pady=(0, 8))
        self._adv_range_entries = {}
        self.adv_monster_type_menu = None
        self._adv_subtype_vars = {}

        self.lbl_search_results = ctk.CTkLabel(search_container, text="", font=("Arial", 16, "bold"), text_color="gray60", anchor="w")
        self.lbl_search_results.grid(row=3, column=0, sticky="ew", padx=5, pady=(5, 0))
        # Positionné en SURVOL (voir _show_suggestions_overlay/_hide_suggestions_overlay),
        # PAS dans le flux normal (grid()/pack()) : un widget placé avec .place()
        # ne participe JAMAIS au calcul de taille de son parent — le masquer via
        # .place_forget() ne laisse donc AUCUN espace réservé derrière lui, quelle
        # que soit la taille qu'il avait juste avant (contrairement à grid()/pack(),
        # qui laissaient un vide fantôme après une grande liste de résultats détruite
        # — les deux tentatives précédentes avec update_idletasks()/scrollregion
        # n'ont pas suffi à corriger ça de façon fiable).
        self.frame_suggestions = ctk.CTkScrollableFrame(
            self.frame_filters, fg_color=("gray95", "gray17"), corner_radius=6, height=280
        )

        self.card_preview_frame = ctk.CTkFrame(
            self.frame_filters, border_width=2, border_color="#1f6aa5", fg_color=("gray85", "gray20")
        )
        self.card_preview_frame.pack(pady=5, padx=10)

        self.img_label = ctk.CTkLabel(
            self.card_preview_frame, text="Aperçu Carte\n(Cliquez pour zoomer)", width=140, height=200, cursor="hand2", font=("Arial", 16)
        )
        self.img_label.pack(padx=8, pady=8)
        self.img_label.bind("<Button-1>", lambda e: self.show_large_image(self.selected_card_id))

        self.card_desc_text = ctk.CTkTextbox(self.frame_filters, height=110, font=("Arial", 15), wrap="word")
        self.card_desc_text.pack(fill="x", padx=10, pady=8)
        self.card_desc_text.insert("1.0", "Sélectionnez une carte pour voir sa description...")
        self.card_desc_text.configure(state="disabled")

        self.lbl_qty = ctk.CTkLabel(self.frame_filters, text="", font=("Arial", 16))
        self.lbl_qty.pack(anchor="w", padx=10)
        self.qte_menu = ctk.CTkOptionMenu(self.frame_filters, values=["1", "2", "3"], font=("Arial", 16))
        self.qte_menu.set("3")
        self.qte_menu.pack(pady=5)

        self.lbl_section = ctk.CTkLabel(self.frame_filters, text="", font=("Arial", 16))
        self.lbl_section.pack(anchor="w", padx=10)
        self.section_menu = ctk.CTkOptionMenu(self.frame_filters, values=[], font=("Arial", 16))
        self.section_menu.pack(pady=5)

        self.checks = {}
        for cat in ["Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick"]:
            self.checks[cat] = ctk.CTkCheckBox(self.frame_filters, text=cat, font=("Arial", 16))
            self.checks[cat].pack(anchor="w", padx=20, pady=3)

        # "Pioche" : catégorie spéciale avec un réglage supplémentaire (combien de
        # cartes elle fait piocher, 1 à 3), affiché uniquement si la case est cochée.
        pioche_row = ctk.CTkFrame(self.frame_filters, fg_color="transparent")
        pioche_row.pack(anchor="w", padx=20, pady=3, fill="x")
        self.checks["Pioche"] = ctk.CTkCheckBox(pioche_row, text="Pioche", command=self._on_pioche_checkbox_toggled, font=("Arial", 16))
        self.checks["Pioche"].pack(side="left")
        self.pioche_count_menu = ctk.CTkOptionMenu(pioche_row, values=["1", "2", "3"], width=60, font=("Arial", 16))
        self.pioche_count_menu.set("1")
        self.pioche_count_menu.pack(side="left", padx=(8, 0))
        self.pioche_count_menu.pack_forget()  # masqué tant que la case n'est pas cochée

        self.btn_save_card = ctk.CTkButton(self.frame_filters, text="", fg_color="blue", command=self.add_or_update_card, font=("Arial", 16))
        self.btn_save_card.pack(pady=15, fill="x", padx=10)

        # --- Colonne droite : sélecteur de deck, taille, onglets Main/Side ---
        self.frame_center = ctk.CTkFrame(self.page_build, fg_color="transparent")
        self.frame_center.grid(row=0, column=1, sticky="nsew")
        self.frame_center.grid_rowconfigure(2, weight=1)
        self.frame_center.grid_columnconfigure(0, weight=1)

        self.frame_deck_selector = ctk.CTkFrame(self.frame_center)
        self.frame_deck_selector.grid(row=0, column=0, sticky="ew", pady=(0, 5))

        self.lbl_active_deck = ctk.CTkLabel(self.frame_deck_selector, text="", font=("Arial", 16))
        self.lbl_active_deck.pack(side="left", padx=5)
        self.deck_menu = ctk.CTkOptionMenu(self.frame_deck_selector, values=[], command=self.change_deck, font=("Arial", 16))
        self.deck_menu.pack(side="left", padx=5)

        self.btn_new_deck = ctk.CTkButton(self.frame_deck_selector, text="", width=70, command=self.create_new_deck, font=("Arial", 16))
        self.btn_new_deck.pack(side="left", padx=2)
        self.btn_rename_deck = ctk.CTkButton(
            self.frame_deck_selector, text="✎", width=36, fg_color="gray40", command=self.rename_current_deck, font=("Arial", 16)
        )
        self.btn_rename_deck.pack(side="left", padx=2)
        self.btn_delete_deck = ctk.CTkButton(
            self.frame_deck_selector, text="🗑", width=36, fg_color="red", command=self.delete_current_deck, font=("Arial", 16)
        )
        self.btn_delete_deck.pack(side="left", padx=2)
        self.btn_export_ydk = ctk.CTkButton(self.frame_deck_selector, text="", width=80, command=self.export_ydk, font=("Arial", 16))
        self.btn_export_ydk.pack(side="left", padx=2)
        self.btn_import_ydk = ctk.CTkButton(self.frame_deck_selector, text="", width=80, command=self.import_ydk, font=("Arial", 16))
        self.btn_import_ydk.pack(side="left", padx=2)
        self.btn_import_url = ctk.CTkButton(self.frame_deck_selector, text="", width=90, command=self.import_from_url, font=("Arial", 16))
        self.btn_import_url.pack(side="left", padx=2)

        self.lbl_extra_info = ctk.CTkLabel(self.frame_deck_selector, text="", font=("Arial", 14), text_color="gray60")
        self.lbl_extra_info.pack(side="left", padx=10)

        self.lbl_download_status = ctk.CTkLabel(self.frame_deck_selector, text="", font=("Arial", 14), text_color="gray60")
        self.lbl_download_status.pack(side="left", padx=10)

        self.lang_menu = ctk.CTkOptionMenu(
            self.frame_deck_selector,
            values=["🇫🇷 FR", "🇬🇧 EN", "🇪🇸 ES", "🇩🇪 DE", "🇮🇹 IT", "🇧🇷 PT"],
            width=90, fg_color="purple", button_color="#5a189a", command=self.change_language, font=("Arial", 16)
        )
        self.lang_menu.pack(side="right", padx=5)
        self.lang_menu.set("🇫🇷 FR")

        self.frame_size_status = ctk.CTkFrame(self.frame_center)
        self.frame_size_status.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        self.lbl_size = {}
        for section in EDIT_SECTIONS:
            lbl = ctk.CTkLabel(self.frame_size_status, text="", font=("Arial", 17, "bold"))
            lbl.pack(side="left", padx=15, pady=5)
            self.lbl_size[section] = lbl
        self.btn_save_deck = ctk.CTkButton(
            self.frame_size_status, text="", width=160, fg_color="#2b8a3e", hover_color="#1e602b",
            command=self._save_deck_explicit, font=("Arial", 17, "bold")
        )
        self.btn_save_deck.pack(side="right", padx=15, pady=5)

        self.tabview = ctk.CTkTabview(self.frame_center, command=self._on_deck_tab_change)
        self.tabview.grid(row=2, column=0, sticky="nsew")
        self.tab_frames = {}
        self._dirty_sections = {section: True for section in EDIT_SECTIONS}
        self._deck_tab_refresh_after_id = None
        # Même principe que _dirty_sections ci-dessus, mais pour les pages
        # Scénarios et Combos Starters : leurs listes ne sont reconstruites
        # que lorsque l'utilisateur navigue réellement dessus, pas à chaque
        # modification faite en Construction du Deck (qui appelle refresh_ui()
        # à chaque +/- ou suppression — sans ça, chaque clic y reconstruisait
        # inutilement des listes potentiellement volumineuses sur une page que
        # l'utilisateur ne regarde même pas).
        self._scenario_editor_dirty = True
        self._combos_ui_dirty = True
        for section in EDIT_SECTIONS:
            tab = self.tabview.add(f"{section} Deck")
            # La grille d'images absorbe TOUT l'espace vertical disponible
            # (weight=1) ; la liste texte en dessous garde une hauteur fixe et
            # modeste (weight=0 + height=180) plutôt que de se partager
            # l'espace à parts égales avec la grille comme avant — la grille
            # est l'affichage principal, la liste reste une vue d'appoint
            # pratique pour ajuster rapidement des quantités au clavier/souris.
            tab.grid_rowconfigure(1, weight=1)
            tab.grid_rowconfigure(2, weight=0)
            tab.grid_columnconfigure(0, weight=1)

            filter_bar = ctk.CTkFrame(tab, fg_color="transparent")
            filter_bar.grid(row=0, column=0, sticky="ew", padx=5, pady=2)
            frame_visual = ctk.CTkScrollableFrame(tab)
            frame_visual.grid(row=1, column=0, sticky="nsew", pady=(0, 5))
            frame_list = ctk.CTkScrollableFrame(tab, height=180)
            frame_list.grid(row=2, column=0, sticky="ew")

            self.tab_frames[section] = {"filter_bar": filter_bar, "visual": frame_visual, "list": frame_list}

        self.lbl_filter_cat = ctk.CTkLabel(self.tab_frames["Main"]["filter_bar"], text="", font=("Arial", 16))
        self.lbl_filter_cat.pack(side="left", padx=5)
        self.filter_category = ctk.CTkOptionMenu(
            self.tab_frames["Main"]["filter_bar"], values=[], command=lambda _: self.refresh_ui(), font=("Arial", 16)
        )
        self.filter_category.pack(side="left", padx=5)
        self.lbl_filter_total = ctk.CTkLabel(self.tab_frames["Main"]["filter_bar"], text="", font=("Arial", 17, "bold"))
        self.lbl_filter_total.pack(side="left", padx=10)

        self.lbl_filter_cat_side = ctk.CTkLabel(self.tab_frames["Side"]["filter_bar"], text="", font=("Arial", 16))
        self.lbl_filter_cat_side.pack(side="left", padx=5)
        self.filter_category_side = ctk.CTkOptionMenu(
            self.tab_frames["Side"]["filter_bar"], values=[], command=lambda _: self.refresh_ui(), font=("Arial", 16)
        )
        self.filter_category_side.pack(side="left", padx=5)
        self.lbl_filter_total_side = ctk.CTkLabel(self.tab_frames["Side"]["filter_bar"], text="", font=("Arial", 17, "bold"))
        self.lbl_filter_total_side.pack(side="left", padx=10)

    # ==================================================================
    # PAGE 2 : ANALYSE (Deck actuel + scénarios de sideboard nommés)
    # ==================================================================
    def _build_page_analysis(self):
        self.page_analysis.grid_columnconfigure(0, weight=2, minsize=260)
        self.page_analysis.grid_columnconfigure(1, weight=6, minsize=600)
        self.page_analysis.grid_rowconfigure(0, weight=1)

        # --- Colonne gauche : sélecteur (toujours visible) + boutons d'analyse
        # (agissent sur la sélection courante, Deck Actuel OU un scénario) +
        # déclencheur de comparaison (ouvre une fenêtre séparée) ---
        frame_list_col = ctk.CTkFrame(self.page_analysis, fg_color="transparent")
        frame_list_col.grid(row=0, column=0, padx=(0, 5), sticky="new")
        frame_list_col.grid_columnconfigure(0, weight=1)

        self.lbl_analysis_target = ctk.CTkLabel(frame_list_col, text="", anchor="w", font=("Arial", 16))
        self.lbl_analysis_target.grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.analysis_target_menu = ctk.CTkOptionMenu(
            frame_list_col, values=[], command=self.select_analysis_target, font=("Arial", 16)
        )
        self.analysis_target_menu.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        self.lbl_scenario_summary = ctk.CTkLabel(
            frame_list_col, text="", wraplength=260, justify="left", font=("Arial", 15), text_color="gray70"
        )
        self.lbl_scenario_summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        row_std = ctk.CTkFrame(frame_list_col, fg_color="transparent")
        row_std.grid(row=3, column=0, sticky="ew", pady=3)
        self.btn_run_std = ctk.CTkButton(
            row_std, text="", fg_color="#1f6aa5", hover_color="#144870",
            font=("Arial", 17, "bold"), height=35, command=self.run_current_target_analysis
        )
        self.btn_run_std.pack(side="left", fill="x", expand=True)
        self._make_info_icon(row_std, "run_std_analysis", "run_std_info_text").pack(side="left", padx=(6, 0))

        # Extensions composables : Combos et Cartes Pioche s'appliquent TOUTES LES
        # DEUX au-dessus du résultat standard (indépendamment l'une de l'autre,
        # cochables ensemble) plutôt que d'être des analyses concurrentes — le
        # moteur de calcul (calculs.run_pioche_simulation avec use_combos=True)
        # sait déjà les combiner nativement.
        self.frame_analysis_extensions = ctk.CTkFrame(frame_list_col, corner_radius=8, fg_color=("gray88", "gray20"))
        self.frame_analysis_extensions.grid(row=4, column=0, sticky="ew", pady=(6, 3))
        self.lbl_extensions_title = ctk.CTkLabel(
            self.frame_analysis_extensions, text="", font=("Arial", 14), text_color="gray60", anchor="w"
        )
        self.lbl_extensions_title.pack(fill="x", padx=10, pady=(8, 4), anchor="w")

        row_ext_combos = ctk.CTkFrame(self.frame_analysis_extensions, fg_color="transparent")
        row_ext_combos.pack(fill="x", padx=10, pady=2)
        self.check_extend_combos = ctk.CTkCheckBox(row_ext_combos, text="", command=self._update_compare_extensions_hint, font=("Arial", 16))
        self.check_extend_combos.pack(side="left")
        self._make_info_icon(row_ext_combos, "extend_combos_title", "extend_combos_text").pack(side="left", padx=(4, 0))

        row_ext_pioche = ctk.CTkFrame(self.frame_analysis_extensions, fg_color="transparent")
        row_ext_pioche.pack(fill="x", padx=10, pady=(2, 2))
        self.check_extend_pioche = ctk.CTkCheckBox(row_ext_pioche, text="", command=self._update_compare_extensions_hint, font=("Arial", 16))
        self.check_extend_pioche.pack(side="left")
        self._make_info_icon(row_ext_pioche, "extend_pioche_title", "extend_pioche_text").pack(side="left", padx=(4, 0))

        self.lbl_pioche_extension_hint = ctk.CTkLabel(
            self.frame_analysis_extensions, text="", font=("Arial", 13), text_color="gray55",
            wraplength=220, justify="left", anchor="w"
        )
        self.lbl_pioche_extension_hint.pack(fill="x", padx=10, pady=(0, 8), anchor="w")

        ctk.CTkFrame(frame_list_col, height=14, fg_color="transparent").grid(row=5, column=0)

        self.btn_open_compare = ctk.CTkButton(
            frame_list_col, text="", fg_color="gray40", command=self.toggle_compare_panel, font=("Arial", 16)
        )
        self.btn_open_compare.grid(row=6, column=0, sticky="ew")

        # Encadré de comparaison : replié par défaut, se déplie juste en dessous du
        # bouton (jamais un popup séparé, jamais à la place du sélecteur du dessus).
        self.frame_compare_panel = ctk.CTkFrame(frame_list_col, corner_radius=10, fg_color=("gray88", "gray20"))
        self.frame_compare_panel.grid(row=7, column=0, sticky="ew", pady=(6, 0))
        self._compare_panel_expanded = False
        self.frame_compare_panel.grid_remove()

        ctk.CTkLabel(
            self.frame_compare_panel, text="", wraplength=240, justify="left", font=("Arial", 14), text_color="gray60"
        ).pack(padx=10, pady=(10, 6), anchor="w")
        self.lbl_compare_hint = self.frame_compare_panel.winfo_children()[-1]

        self.frame_compare_checklist = ctk.CTkScrollableFrame(self.frame_compare_panel, label_text="", height=160, fg_color="transparent")
        self.frame_compare_checklist.pack(fill="x", padx=10, pady=(0, 8))

        self.lbl_compare_extensions_hint = ctk.CTkLabel(
            self.frame_compare_panel, text="", anchor="w", font=("Arial", 14), text_color="gray60",
            wraplength=240, justify="left"
        )
        self.lbl_compare_extensions_hint.pack(fill="x", padx=10, pady=(0, 8))

        self.btn_run_compare = ctk.CTkButton(
            self.frame_compare_panel, text="", fg_color="#2b8a3e", command=self._run_comparison_from_panel, font=("Arial", 16)
        )
        self.btn_run_compare.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkFrame(frame_list_col, height=14, fg_color="transparent").grid(row=8, column=0)
        self.btn_open_settings = ctk.CTkButton(
            frame_list_col, text="", fg_color="gray40", command=self.open_settings_dialog, font=("Arial", 16)
        )
        self.btn_open_settings.grid(row=9, column=0, sticky="ew")

        # --- Colonne droite : titre + résultats (vue unique, pas d'onglets —
        # Combos et Cartes Pioche sont des extensions composables du même
        # résultat, pas des vues séparées à naviguer) ---
        self.frame_analysis_detail = ctk.CTkFrame(self.page_analysis, fg_color="transparent")
        self.frame_analysis_detail.grid(row=0, column=1, sticky="nsew")
        self.frame_analysis_detail.grid_rowconfigure(1, weight=1)
        self.frame_analysis_detail.grid_columnconfigure(0, weight=1)

        self.lbl_detail_title = ctk.CTkLabel(self.frame_analysis_detail, text="", font=("Arial", 22, "bold"))
        self.lbl_detail_title.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.result_container = ctk.CTkScrollableFrame(self.frame_analysis_detail, fg_color="transparent")
        self.result_container.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self._render_results_placeholder()

    def run_current_target_analysis(self):
        """Lance l'analyse sur la sélection courante du dropdown (Deck Actuel ou un
        scénario), avec les extensions actuellement cochées (Combos / Cartes Pioche
        — composables, lues ici au moment du clic, pas figées par bouton)."""
        use_combos = bool(self.check_extend_combos.get())
        use_pioche = bool(self.check_extend_pioche.get())
        if self.analysis_target is None:
            self.run_analysis(use_combos=use_combos, use_pioche=use_pioche)
        else:
            self.run_scenario_analysis(use_combos=use_combos, use_pioche=use_pioche)

    def toggle_compare_panel(self):
        self._compare_panel_expanded = not self._compare_panel_expanded
        if self._compare_panel_expanded:
            self.compare_selection = []
            self.frame_compare_panel.grid()
            self._refresh_compare_checklist()
        else:
            self.frame_compare_panel.grid_remove()

    def _run_comparison_from_panel(self):
        use_combos = bool(self.check_extend_combos.get())
        use_pioche = bool(self.check_extend_pioche.get())
        self.run_comparison(use_combos=use_combos, use_pioche=use_pioche)
        self._compare_panel_expanded = False
        self.frame_compare_panel.grid_remove()

    # ==================================================================
    # PARAMÈTRES : conditions de victoire personnalisées (indépendantes pour
    # Aller en Premier / Aller en Second)
    # ==================================================================
    def open_settings_dialog(self):
        if getattr(self, "_settings_dialog", None) is not None and self._settings_dialog.winfo_exists():
            self._settings_dialog.lift()
            self._settings_dialog.focus()
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title(self.t("settings_title"))
        dialog.geometry("640x600")
        dialog.attributes("-topmost", True)
        dialog.protocol("WM_DELETE_WINDOW", self._close_settings_dialog)
        self._settings_dialog = dialog

        ctk.CTkLabel(
            dialog, text=self.t("settings_intro"), wraplength=600, justify="left", font=("Arial", 15), text_color="gray60"
        ).pack(padx=15, pady=(15, 10), anchor="w")

        cols = ctk.CTkFrame(dialog, fg_color="transparent")
        cols.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        cols.grid_columnconfigure(0, weight=1)
        cols.grid_columnconfigure(1, weight=1)
        cols.grid_rowconfigure(0, weight=1)

        self._build_settings_column(cols, "first", self.t("settings_section_first")).grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._build_settings_column(cols, "second", self.t("settings_section_second")).grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._refresh_settings_dialog()

    def _close_settings_dialog(self):
        if getattr(self, "_settings_dialog", None) is not None:
            self._settings_dialog.destroy()
            self._settings_dialog = None

    def _build_settings_column(self, parent, side, title):
        col = ctk.CTkFrame(parent, corner_radius=10, fg_color=("gray92", "gray17"))
        ctk.CTkLabel(col, text=title, font=("Arial", 18, "bold")).pack(anchor="w", padx=10, pady=(10, 6))

        list_frame = ctk.CTkScrollableFrame(col, label_text="", fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        ctk.CTkButton(
            col, text=self.t("settings_add_condition"), fg_color="darkblue",
            command=lambda s=side: self.open_condition_editor(s), font=("Arial", 16)
        ).pack(fill="x", padx=10, pady=(0, 10))
        if side == "first":
            self.frame_settings_list_first = list_frame
        else:
            self.frame_settings_list_second = list_frame
        return col

    def _refresh_settings_dialog(self):
        if getattr(self, "_settings_dialog", None) is None or not self._settings_dialog.winfo_exists():
            return
        self._refresh_settings_column("first", self.frame_settings_list_first)
        self._refresh_settings_column("second", self.frame_settings_list_second)

    def _refresh_settings_column(self, side, list_frame):
        """
        Affiche chaque condition sous forme de bloc, avec — ENTRE chaque paire de
        blocs consécutifs — un petit bouton cliquable "OU"/"ET" représentant le
        connecteur logique entre CES deux conditions précisément (`operator` de
        la condition qui suit). La toute première condition n'a pas de connecteur
        au-dessus d'elle (rien à combiner avant elle).
        """
        for w in list_frame.winfo_children():
            w.destroy()
        conditions = self.custom_conditions.get(side, {}).get("conditions", [])
        if not conditions:
            ctk.CTkLabel(list_frame, text=self.t("settings_no_conditions"), font=("Arial", 14), text_color="gray60").pack(pady=8)
            return
        for idx, cond in enumerate(conditions):
            if idx > 0:
                operator = cond.get("operator", "or")
                connector_text = self.t("condition_combinator_and") if operator == "and" else self.t("condition_combinator_or")
                ctk.CTkButton(
                    list_frame, text=f"— {connector_text} —", width=90, height=22, fg_color="gray35",
                    hover_color="gray45", font=("Arial", 15, "bold"),
                    command=lambda s=side, i=idx: self._toggle_condition_operator(s, i)
                ).pack(pady=2)

            row = ctk.CTkFrame(list_frame)
            row.pack(fill="x", pady=2)
            clause_parts = []
            for j, c in enumerate(cond.get("clauses", [])):
                text = self.t("condition_summary_clause").format(
                    op=OPERATOR_DISPLAY.get(c.get("op", ">="), "≥"),
                    value=c.get("value", c.get("min", 1)),
                    category=self.t(c.get("category", "?"))
                )
                if j > 0:
                    connector = c.get("connector", "and")
                    connector_text = self.t("condition_combinator_and") if connector == "and" else self.t("condition_combinator_or")
                    clause_parts.append(f" {connector_text} {text}")
                else:
                    clause_parts.append(text)
            summary = "".join(clause_parts)
            ctk.CTkLabel(
                row, text=f"{cond.get('name', '?')}\n{summary}", font=("Arial", 14),
                anchor="w", justify="left"
            ).pack(side="left", padx=6, pady=4, fill="x", expand=True)
            ctk.CTkButton(
                row, text="🗑", width=26, fg_color="red",
                command=lambda s=side, i=idx: self.delete_custom_condition(s, i), font=("Arial", 16)
            ).pack(side="right", padx=4)
            ctk.CTkButton(
                row, text="✎", width=26, fg_color="gray40",
                command=lambda s=side, i=idx: self.open_condition_editor(s, edit_index=i), font=("Arial", 16)
            ).pack(side="right", padx=(4, 0))

    def _toggle_condition_operator(self, side, index):
        conditions = self.custom_conditions.get(side, {}).get("conditions", [])
        if 0 <= index < len(conditions):
            current = conditions[index].get("operator", "or")
            conditions[index]["operator"] = "and" if current == "or" else "or"
            backend.save_conditions(self.custom_conditions, self.current_deck_name)
            list_frame = self.frame_settings_list_first if side == "first" else self.frame_settings_list_second
            self._refresh_settings_column(side, list_frame)

    def delete_custom_condition(self, side, index):
        conditions = self.custom_conditions.get(side, {}).get("conditions", [])
        if 0 <= index < len(conditions):
            del conditions[index]
            backend.save_conditions(self.custom_conditions, self.current_deck_name)
            self._refresh_settings_dialog()

    def open_condition_editor(self, side, edit_index=None):
        """
        Sous-fenêtre de construction d'UNE condition : un nom + une liste
        dynamique de clauses ("[opérateur] [valeur] [catégorie]"), avec — ENTRE
        chaque paire de clauses consécutives — un connecteur ET/OU cliquable
        (même mécanisme que le connecteur entre conditions). `edit_index` : si
        fourni, pré-remplit le formulaire avec la condition existante à cet
        index et MET À JOUR celle-ci au lieu d'en ajouter une nouvelle (son
        `operator` de chaînage avec la condition précédente est conservé tel
        quel — seuls son nom et ses clauses changent).
        """
        conditions = self.custom_conditions.get(side, {}).get("conditions", [])
        existing = conditions[edit_index] if (edit_index is not None and 0 <= edit_index < len(conditions)) else None

        editor = ctk.CTkToplevel(self)
        editor.title(self.t("condition_editor_edit_title") if existing else self.t("condition_editor_title"))
        editor.geometry("460x520")
        editor.attributes("-topmost", True)

        ctk.CTkLabel(editor, text=self.t("condition_name_prompt"), wraplength=420, justify="left", font=("Arial", 16)).pack(padx=15, pady=(15, 4), anchor="w")
        name_entry = ctk.CTkEntry(editor, font=("Arial", 16))
        name_entry.pack(fill="x", padx=15, pady=(0, 10))
        if existing:
            name_entry.insert(0, existing.get("name", ""))

        clauses_frame = ctk.CTkScrollableFrame(editor, label_text="")
        clauses_frame.pack(fill="both", expand=True, padx=15, pady=(0, 8))

        categories = ["Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick"]
        categories_display = [self.t(c) for c in categories]
        display_to_cat = dict(zip(categories_display, categories))
        operator_display_values = [d for _, d in OPERATORS]
        clause_widgets = []

        def _connector_label(value):
            return f"— {self.t('condition_combinator_and') if value == 'and' else self.t('condition_combinator_or')} —"

        def add_clause_row(default_cat_display=None, default_op_display=None, default_value=1, default_connector="and"):
            connector_widget = None
            connector_state = None
            if clause_widgets:  # pas la première clause du formulaire -> connecteur cliquable au-dessus
                connector_state = {"value": default_connector}
                connector_widget = ctk.CTkButton(
                    clauses_frame, text=_connector_label(default_connector), width=80, height=20,
                    fg_color="gray35", hover_color="gray45", font=("Arial", 14, "bold")
                )

                def _toggle():
                    connector_state["value"] = "or" if connector_state["value"] == "and" else "and"
                    connector_widget.configure(text=_connector_label(connector_state["value"]))

                connector_widget.configure(command=_toggle)
                connector_widget.pack(pady=1)

            row = ctk.CTkFrame(clauses_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            # Ordre catégorie -> opérateur -> valeur (ex. "Starter ≥ 3") plutôt
            # que opérateur -> valeur -> catégorie : on lit le symbole de
            # comparaison APRÈS avoir déjà vu à quoi il s'applique, ce qui est
            # plus facile à suivre que de devoir garder "≥ 3" en mémoire sans
            # contexte jusqu'à la fin de la ligne.
            menu = ctk.CTkOptionMenu(row, values=categories_display, width=140, font=("Arial", 16))
            menu.set(default_cat_display or categories_display[0])
            menu.pack(side="left", padx=(0, 4))
            op_menu = ctk.CTkOptionMenu(row, values=operator_display_values, width=60, font=("Arial", 16))
            op_menu.set(default_op_display or OPERATOR_DISPLAY[">="])
            op_menu.pack(side="left", padx=4)
            entry = ctk.CTkEntry(row, width=50, font=("Arial", 16))
            entry.insert(0, str(default_value))
            entry.pack(side="left", padx=4)
            ctk.CTkButton(
                row, text="🗑", width=26, fg_color="red", command=lambda r=row: remove_clause_row(r), font=("Arial", 16)
            ).pack(side="left", padx=4)
            clause_widgets.append({
                "row": row, "menu": menu, "op_menu": op_menu, "entry": entry,
                "connector_widget": connector_widget, "connector_state": connector_state,
            })

        def remove_clause_row(row):
            idx = next((i for i, c in enumerate(clause_widgets) if c["row"] == row), None)
            if idx is None:
                return
            removed = clause_widgets.pop(idx)
            if removed["connector_widget"] is not None:
                removed["connector_widget"].destroy()
            removed["row"].destroy()
            # Si on supprime le tout premier maillon et qu'il en reste d'autres,
            # le nouveau premier ne doit plus avoir de connecteur au-dessus de lui
            # (rien à combiner avant lui désormais).
            if idx == 0 and clause_widgets:
                new_first = clause_widgets[0]
                if new_first["connector_widget"] is not None:
                    new_first["connector_widget"].destroy()
                    new_first["connector_widget"] = None
                    new_first["connector_state"] = None

        if existing and existing.get("clauses"):
            for clause in existing["clauses"]:
                cat = clause.get("category")
                op = clause.get("op", ">=")
                value = clause.get("value", clause.get("min", 1))
                add_clause_row(
                    default_cat_display=self.t(cat) if cat in categories else None,
                    default_op_display=OPERATOR_DISPLAY.get(op, "≥"),
                    default_value=value,
                    default_connector=clause.get("connector", "and"),
                )
        else:
            add_clause_row()  # une première clause vide par défaut, pour ne pas partir d'un formulaire complètement vide

        ctk.CTkButton(
            editor, text=self.t("condition_clause_add"), fg_color="gray40", command=lambda: add_clause_row(), font=("Arial", 16)
        ).pack(fill="x", padx=15, pady=(0, 10))

        lbl_error = ctk.CTkLabel(editor, text="", text_color="#d9480f", wraplength=420, justify="left", font=("Arial", 16))
        lbl_error.pack(fill="x", padx=15)

        def save_condition():
            name = name_entry.get().strip()
            clauses = []
            for c in clause_widgets:
                cat = display_to_cat.get(c["menu"].get())
                op = OPERATOR_FROM_DISPLAY.get(c["op_menu"].get(), ">=")
                try:
                    value = int(c["entry"].get())
                except (TypeError, ValueError):
                    value = None
                if cat and value is not None and value >= 0:
                    clause_dict = {"category": cat, "op": op, "value": value}
                    if clauses and c["connector_state"] is not None:
                        clause_dict["connector"] = c["connector_state"]["value"]
                    clauses.append(clause_dict)
            if not name or not clauses:
                lbl_error.configure(text=self.t("condition_invalid"))
                return

            self.custom_conditions.setdefault(side, {"conditions": []})
            conditions_list = self.custom_conditions[side].setdefault("conditions", [])
            if existing is not None:
                existing["name"] = name
                existing["clauses"] = clauses
                # "operator" (chaînage avec la condition précédente) volontairement
                # conservé tel quel : on ne modifie ici que le contenu de CETTE
                # condition, pas sa relation avec les autres.
            else:
                conditions_list.append({"name": name, "clauses": clauses, "operator": "or"})
            backend.save_conditions(self.custom_conditions, self.current_deck_name)
            self._refresh_settings_dialog()
            editor.destroy()

        btns_row = ctk.CTkFrame(editor, fg_color="transparent")
        btns_row.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkButton(
            btns_row, text=self.t("condition_cancel"), fg_color="gray40", command=editor.destroy, font=("Arial", 16)
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            btns_row, text=self.t("condition_save"), fg_color="#2b8a3e", command=save_condition, font=("Arial", 16)
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

    # ==================================================================
    # PAGE (nouvelle, entre Construction et Analyse) : COMBOS & SCÉNARIOS
    # ==================================================================
    def _build_page_combos(self):
        """Page 'Combos Starters' : TOUS les combos du deck, chacun avec sa portée (tous les scénarios, ou un seul en particulier)."""
        self.page_combos.grid_columnconfigure(0, weight=1, minsize=320)
        self.page_combos.grid_rowconfigure(1, weight=1)

        title_row = ctk.CTkFrame(self.page_combos, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.lbl_combo_title = ctk.CTkLabel(title_row, text="", font=("Arial", 22, "bold"))
        self.lbl_combo_title.pack(side="left", padx=(0, 6))
        self._make_info_icon(title_row, "combo_info_title", "combo_info_text").pack(side="left")

        frame_combo_col = ctk.CTkScrollableFrame(self.page_combos)
        frame_combo_col.grid(row=1, column=0, sticky="nsew")

        form_row = ctk.CTkFrame(frame_combo_col, fg_color="transparent")
        form_row.pack(fill="x", pady=(0, 5))
        self.combo_card1_menu = ctk.CTkOptionMenu(form_row, values=[], font=("Arial", 16))
        self.combo_card1_menu.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.combo_card2_menu = ctk.CTkOptionMenu(form_row, values=[], font=("Arial", 16))
        self.combo_card2_menu.pack(side="left", fill="x", expand=True, padx=3)

        scope_row = ctk.CTkFrame(frame_combo_col, fg_color="transparent")
        scope_row.pack(fill="x", pady=(0, 5))
        self.lbl_combo_scope = ctk.CTkLabel(scope_row, text="", width=90, anchor="w", font=("Arial", 16))
        self.lbl_combo_scope.pack(side="left")
        self.combo_scope_menu = ctk.CTkOptionMenu(scope_row, values=[], font=("Arial", 16))
        self.combo_scope_menu.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.btn_add_combo = ctk.CTkButton(frame_combo_col, text="", fg_color="darkblue", command=self.add_combo_rule, font=("Arial", 16))
        self.btn_add_combo.pack(pady=(5, 10), fill="x")
        self.frame_combos_list = ctk.CTkFrame(frame_combo_col, fg_color="transparent")
        self.frame_combos_list.pack(fill="x")

    def _build_page_scenarios(self):
        """Page 'Scénarios' : élaboration des scénarios de sideboard, en pleine largeur (plus de place pour le swap Main/Side)."""
        self.page_scenarios.grid_columnconfigure(0, weight=1)
        self.page_scenarios.grid_rowconfigure(4, weight=1)

        scenario_title_row = ctk.CTkFrame(self.page_scenarios, fg_color="transparent")
        scenario_title_row.grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.lbl_scenario_col_title = ctk.CTkLabel(scenario_title_row, text="", font=("Arial", 22, "bold"))
        self.lbl_scenario_col_title.pack(side="left", padx=(0, 6))
        self._make_info_icon(scenario_title_row, "scenario_info_title", "scenario_info_text").pack(side="left")

        buttons_row = ctk.CTkFrame(self.page_scenarios, fg_color="transparent")
        buttons_row.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        self.btn_new_scenario = ctk.CTkButton(buttons_row, text="", width=160, command=self.create_scenario, font=("Arial", 16))
        self.btn_new_scenario.pack(side="left", padx=(0, 6))

        # Sélecteur de scénario : un dropdown compact (plutôt qu'une liste toujours
        # affichée) + icônes d'action sur le scénario actuellement sélectionné.
        picker_row = ctk.CTkFrame(self.page_scenarios, fg_color="transparent")
        picker_row.grid(row=2, column=0, sticky="ew", pady=(0, 5))
        self.scenario_picker_menu = ctk.CTkOptionMenu(picker_row, values=[], width=280, command=self._on_scenario_picked, font=("Arial", 16))
        self.scenario_picker_menu.pack(side="left", padx=(0, 3))
        self.btn_duplicate_scenario = ctk.CTkButton(picker_row, text="⧉", width=30, fg_color="gray40", command=self._duplicate_active_scenario, font=("Arial", 16))
        self.btn_duplicate_scenario.pack(side="left", padx=1)
        self.btn_delete_scenario = ctk.CTkButton(picker_row, text="🗑", width=30, fg_color="red", command=self._delete_active_scenario, font=("Arial", 16))
        self.btn_delete_scenario.pack(side="left", padx=(1, 15))
        self.lbl_turn_order = ctk.CTkLabel(picker_row, text="", anchor="w", font=("Arial", 16))
        self.lbl_turn_order.pack(side="left", padx=(0, 6))
        self.turn_order_menu = ctk.CTkSegmentedButton(picker_row, values=[], command=self._on_turn_order_picked, font=("Arial", 16))
        self.turn_order_menu.pack(side="left")

        # Zone d'édition : hint (aucun scénario sélectionné) OU constructeur de swap
        self.frame_scenario_editor = ctk.CTkFrame(self.page_scenarios, fg_color="transparent")
        self.frame_scenario_editor.grid(row=4, column=0, sticky="nsew")
        self.frame_scenario_editor.grid_rowconfigure(0, weight=1)
        self.frame_scenario_editor.grid_columnconfigure(0, weight=1)

        self.frame_scenario_editor_hint = ctk.CTkFrame(self.frame_scenario_editor, fg_color="transparent")
        self.frame_scenario_editor_hint.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(
            self.frame_scenario_editor_hint, text="", wraplength=350, justify="left", font=("Arial", 16), text_color="gray60"
        ).pack(padx=10, pady=20)

        self.frame_scenario_editor_swap = ctk.CTkFrame(self.frame_scenario_editor, fg_color="transparent")
        self.frame_scenario_editor_swap.grid(row=0, column=0, sticky="nsew")
        self.frame_scenario_editor_swap.grid_remove()  # masqué par défaut (aucun scénario sélectionné)
        self.frame_scenario_editor_swap.grid_rowconfigure(5, weight=1)
        self.frame_scenario_editor_swap.grid_columnconfigure(0, weight=1)
        self.frame_scenario_editor_swap.grid_columnconfigure(1, weight=1)

        self.lbl_scenario_editor_title = ctk.CTkLabel(
            self.frame_scenario_editor_swap, text="", font=("Arial", 20, "bold"), cursor="hand2"
        )
        self.lbl_scenario_editor_title.bind("<Button-1>", lambda e: self._rename_active_scenario())
        self.lbl_scenario_editor_title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self.lbl_scenario_desc = ctk.CTkLabel(
            self.frame_scenario_editor_swap, text="", wraplength=900, justify="left", font=("Arial", 15), text_color="gray70"
        )
        self.lbl_scenario_desc.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 5))

        toolbar = ctk.CTkFrame(self.frame_scenario_editor_swap)
        toolbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self.btn_scenario_reset = ctk.CTkButton(toolbar, text="", width=100, command=self.reset_active_scenario, font=("Arial", 16))
        self.btn_scenario_reset.pack(side="left", padx=5, pady=5)
        self.btn_scenario_save = ctk.CTkButton(
            toolbar, text="", width=130, fg_color="#2b8a3e", hover_color="#1e602b", command=self._save_active_scenario, font=("Arial", 16)
        )
        self.btn_scenario_save.pack(side="left", padx=5, pady=5)
        self.lbl_scenario_size = ctk.CTkLabel(toolbar, text="", font=("Arial", 17, "bold"))
        self.lbl_scenario_size.pack(side="left", padx=15)

        # Panneau de résultat automatique : recalculé à chaque clic sur
        # "Sauvegarder" (pas à chaque clic +/-, pour ne pas réintroduire le
        # ralentissement qu'on a corrigé en passant à la sauvegarde manuelle).
        # Affiche uniquement Aller en Premier et/ou Aller en Second selon ce
        # qui est pertinent pour ce scénario (turn_order), comme sur la page
        # Analyse — sans devoir naviguer là-bas ni resélectionner ce scénario.
        self.frame_scenario_auto_result = ctk.CTkFrame(self.frame_scenario_editor_swap, corner_radius=10, fg_color=("gray90", "gray17"))
        self.frame_scenario_auto_result.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.lbl_scenario_result_caption = ctk.CTkLabel(
            self.frame_scenario_auto_result, text="", font=("Arial", 14), text_color="gray55",
            anchor="w", justify="left", wraplength=850
        )
        self.lbl_scenario_result_caption.pack(anchor="w", padx=10, pady=(8, 2))
        self.row_scenario_result_stats = ctk.CTkFrame(self.frame_scenario_auto_result, fg_color="transparent")
        self.row_scenario_result_stats.pack(fill="x", padx=10, pady=(0, 8))
        self.lbl_scenario_result_first = ctk.CTkLabel(self.row_scenario_result_stats, text="", font=("Arial", 25, "bold"))
        self.lbl_scenario_result_first.pack(side="left", padx=(0, 24))
        self.lbl_scenario_result_second = ctk.CTkLabel(self.row_scenario_result_stats, text="", font=("Arial", 25, "bold"))
        self.lbl_scenario_result_second.pack(side="left")

        self.lbl_scenario_main = ctk.CTkLabel(self.frame_scenario_editor_swap, text="", font=("Arial", 18, "bold"))
        self.lbl_scenario_main.grid(row=4, column=0, sticky="w", padx=5)
        self.frame_scenario_main = ctk.CTkScrollableFrame(self.frame_scenario_editor_swap)
        self.frame_scenario_main.grid(row=5, column=0, sticky="nsew", padx=(0, 2), pady=5)

        self.lbl_scenario_side = ctk.CTkLabel(self.frame_scenario_editor_swap, text="", font=("Arial", 18, "bold"))
        self.lbl_scenario_side.grid(row=4, column=1, sticky="w", padx=5)
        self.frame_scenario_side = ctk.CTkScrollableFrame(self.frame_scenario_editor_swap)
        self.frame_scenario_side.grid(row=5, column=1, sticky="nsew", padx=(2, 0), pady=5)

        # frame_scenario_editor_hint est déjà seule visible : frame_scenario_editor_swap
        # a été masquée via grid_remove() à sa création (voir plus haut).

    def _on_page_switch(self, value):
        page_map = getattr(self, "_page_display_map", {})
        key = page_map.get(value, "build")
        if getattr(self, "_current_page_key", None) == "build" and key != "build":
            if not self._confirm_discard_deck_changes():
                key_to_display = {v: k for k, v in self._page_display_map.items()}
                self.page_switch.set(key_to_display.get("build", value))
                return
        if getattr(self, "_current_page_key", None) == "scenarios" and key != "scenarios":
            if not self._confirm_discard_scenario_changes():
                key_to_display = {v: k for k, v in self._page_display_map.items()}
                self.page_switch.set(key_to_display.get("scenarios", value))
                return
        self._current_page_key = key
        if key == "build":
            self.page_build.tkraise()
        elif key == "combos":
            self.page_combos.tkraise()
            if getattr(self, "_combos_ui_dirty", False):
                self.refresh_combos_ui()
                self._combos_ui_dirty = False
        elif key == "scenarios":
            self.page_scenarios.tkraise()
            if getattr(self, "_scenario_editor_dirty", False):
                self.refresh_scenario_editor_list()
                self.refresh_scenario_editor_detail()
                self._scenario_editor_dirty = False
        else:
            self.page_analysis.tkraise()

    # --- SÉCURITÉ & TRADUCTION ---
    def t(self, key):
        return TRANSLATIONS.get(self.lang, TRANSLATIONS["FR"]).get(key, key)

    def section_label(self, section):
        return self.t(f"section_{section.lower()}")

    def change_language(self, selected_option):
        """
        Change uniquement les libellés de l'interface. Les cartes restent en
        anglais (base de données unique) et le deck n'est jamais retraduit.
        """
        lang_code = selected_option.split()[-1]
        if lang_code in TRANSLATIONS and lang_code != self.lang:
            self.lang = lang_code
            self.update_language_texts()
            self.refresh_ui()

    def update_language_texts(self):
        self.title(self.t("title"))

        page_values = [self.t("page_build"), self.t("page_combos"), self.t("page_scenarios"), self.t("page_analysis")]
        current_page_key = getattr(self, "_current_page_key", "build")
        self._page_display_map = {
            self.t("page_build"): "build",
            self.t("page_combos"): "combos",
            self.t("page_scenarios"): "scenarios",
            self.t("page_analysis"): "analysis",
        }
        self.page_switch.configure(values=page_values)
        key_to_display = {v: k for k, v in self._page_display_map.items()}
        self.page_switch.set(key_to_display.get(current_page_key, self.t("page_build")))

        self.lbl_add_edit.configure(text=self.t("add_edit_card"))
        self.search_entry.configure(placeholder_text=self.t("search_ph"))
        self.lbl_search_results.configure(text=self.t("search_results"))

        self.btn_toggle_adv_search.configure(text=self.t("adv_search_toggle"))
        self.lbl_adv_category.configure(text=self.t("adv_category_label"))

        category_values = [self.t("all"), self.t("adv_cat_monster"), self.t("adv_cat_spell"), self.t("adv_cat_trap")]
        prev_map = getattr(self, "_adv_category_display_map", {})
        current_category_key = prev_map.get(self.adv_category_menu.get(), "all")
        self._adv_category_display_map = {
            self.t("all"): "all",
            self.t("adv_cat_monster"): "Monster",
            self.t("adv_cat_spell"): "Spell",
            self.t("adv_cat_trap"): "Trap",
        }
        self.adv_category_menu.configure(values=category_values)
        key_to_display = {v: k for k, v in self._adv_category_display_map.items()}
        self.adv_category_menu.set(key_to_display.get(current_category_key, self.t("all")))
        self._rebuild_adv_dynamic_filters()

        if not self.selected_card_id:
            self.img_label.configure(text=self.t("preview_title"))
            self.card_desc_text.configure(state="normal")
            self.card_desc_text.delete("1.0", "end")
            self.card_desc_text.insert("1.0", self.t("desc_default"))
            self.card_desc_text.configure(state="disabled")

        self.lbl_qty.configure(text=self.t("quantity"))
        self.lbl_section.configure(text=self.t("section_label"))

        section_values = [self.section_label(s) for s in EDIT_SECTIONS]
        prev_map = getattr(self, "_section_display_map", {})
        current_key = prev_map.get(self.section_menu.get(), "Main")
        self._section_display_map = {self.section_label(s): s for s in EDIT_SECTIONS}
        self.section_menu.configure(values=section_values)
        self.section_menu.set(self.section_label(current_key))

        self.btn_save_card.configure(text=self.t("save_card"))
        self.lbl_combo_title.configure(text=self.t("combo_title"))
        self.btn_add_combo.configure(text=self.t("add_combo"))

        for cat_key, check_widget in self.checks.items():
            check_widget.configure(text=self.t(cat_key))

        self.lbl_active_deck.configure(text=self.t("active_deck"))
        self.btn_new_deck.configure(text=self.t("new_deck"))
        self.btn_rename_deck.configure(text="✎")
        self.btn_delete_deck.configure(text="🗑")
        self.btn_export_ydk.configure(text=self.t("export_ydk"))
        self.btn_import_ydk.configure(text=self.t("import_ydk"))
        self.btn_import_url.configure(text=self.t("import_url"))
        self.lbl_filter_cat.configure(text=self.t("filter_cat"))
        self.lbl_filter_cat_side.configure(text=self.t("filter_cat"))

        self.btn_run_std.configure(text=self.t("run_std_analysis"))
        self.lbl_extensions_title.configure(text=self.t("extensions_title"))
        self.check_extend_combos.configure(text=self.t("extend_combos_label"))
        self.check_extend_pioche.configure(text=self.t("extend_pioche_label"))
        self.lbl_analysis_target.configure(text=self.t("analysis_target_label"))
        self.btn_open_compare.configure(text=self.t("compare_mode_toggle"))
        self.lbl_compare_hint.configure(text=self.t("compare_hint"))
        self._update_compare_extensions_hint()
        self.btn_run_compare.configure(text=self.t("compare_run"))
        self.btn_open_settings.configure(text=self.t("btn_open_settings"))
        self._update_pioche_extension_availability()

        self.lbl_combo_title.configure(text=self.t("combo_title"))
        self.lbl_combo_scope.configure(text=self.t("combo_scope_label"))
        self.btn_add_combo.configure(text=self.t("add_combo"))
        self.lbl_scenario_col_title.configure(text=self.t("scenario_list_title"))
        self.btn_new_scenario.configure(text=self.t("scenario_new"))
        # Resynchronise les noms des scénarios standards sur la langue courante
        # (voir _seed_standard_scenarios_if_needed) avant le rafraîchissement de
        # la liste juste en dessous.
        if hasattr(self, "scenarios"):
            self._seed_standard_scenarios_if_needed()
        self.lbl_turn_order.configure(text=self.t("turn_order_label"))
        turn_values = [self.t("turn_order_first"), self.t("turn_order_second"), self.t("turn_order_unknown")]
        self.turn_order_menu.configure(values=turn_values)
        self._turn_order_display_map = {
            self.t("turn_order_first"): "first", self.t("turn_order_second"): "second", self.t("turn_order_unknown"): None,
        }
        if self.active_scenario_index is not None and self.active_scenario_index < len(self.scenarios):
            self._refresh_turn_order_menu(self.scenarios[self.active_scenario_index])
        else:
            self.turn_order_menu.set(self.t("turn_order_unknown"))
        self.lbl_scenario_desc.configure(text=self.t("postside_desc"))
        self.btn_scenario_reset.configure(text=self.t("postside_reset"))
        self._update_scenario_save_button_state()
        self._update_deck_save_button_state()
        self.lbl_scenario_main.configure(text=self.t("postside_current"))
        self.lbl_scenario_side.configure(text=self.t("postside_side_pool"))
        for widget in self.frame_scenario_editor_hint.winfo_children():
            widget.configure(text=self.t("scenario_editor_hint"))

        categories = [self.t("all")] + [self.t(c) for c in ["Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick", "Pioche"]]
        self.filter_category.configure(values=categories)
        self.filter_category.set(self.t("all"))
        self.filter_category_side.configure(values=categories)
        self.filter_category_side.set(self.t("all"))

        self.refresh_extra_info()
        self.refresh_scenario_editor_list()
        self.refresh_scenario_editor_detail()
        self.refresh_analysis_target_menus()

    # --- BULLES D'INFORMATION (ⓘ au survol) ---
    def _make_info_icon(self, parent, title_key, text_key):
        """
        Crée un petit label "ⓘ" qui affiche une bulle d'information au survol de
        la souris (fenêtre sans bordure positionnée près de l'icône). `title_key`
        et `text_key` sont des clés de traduction, relues dynamiquement à chaque
        affichage pour rester à jour après un changement de langue.
        """
        icon = ctk.CTkLabel(parent, text="ⓘ", font=("Arial", 19, "bold"), text_color="#1f6aa5", cursor="hand2")
        state = {"win": None}

        def show(_event=None):
            if state["win"] is not None:
                return
            x = icon.winfo_rootx() + 18
            y = icon.winfo_rooty() + icon.winfo_height() + 4
            win = ctk.CTkToplevel(self)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            win.attributes("-topmost", True)
            card = ctk.CTkFrame(win, fg_color=("gray90", "gray17"), corner_radius=10, border_width=1, border_color="#1f6aa5")
            card.pack()
            ctk.CTkLabel(card, text=self.t(title_key), font=("Arial", 17, "bold"), anchor="w").pack(
                padx=14, pady=(10, 2), anchor="w"
            )
            ctk.CTkLabel(
                card, text=self.t(text_key), font=("Arial", 15), wraplength=320, justify="left", anchor="w"
            ).pack(padx=14, pady=(0, 10), anchor="w")
            state["win"] = win

        def hide(_event=None):
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None

        icon.bind("<Enter>", show)
        icon.bind("<Leave>", hide)
        return icon

    # --- BASE DE DONNÉES ET RECHERCHE (toujours en anglais) ---
    def check_and_update_db(self):
        if backend.is_db_expired():
            backend.download_ygopro_db()
        self.db_data, self.db_dict = backend.load_local_db()
        # Index pré-calculé (nom en minuscule, calculé une seule fois) au lieu de
        # refaire ~13000 appels .lower() à chaque frappe dans la recherche.
        self._search_index = [(str(c.get('name', '')).lower(), c) for c in self.db_data]

    def toggle_advanced_search(self):
        self._adv_search_visible = not self._adv_search_visible
        if self._adv_search_visible:
            self.frame_adv_search.grid()  # réaffiche à sa position grid d'origine (row=2)
        else:
            self.frame_adv_search.grid_remove()
        self.update_suggestions()

    def _on_adv_category_change(self, _selected_value=None):
        self._rebuild_adv_dynamic_filters()
        self.update_suggestions()

    def _rebuild_adv_dynamic_filters(self):
        """
        Reconstruit le second niveau de filtre selon la catégorie choisie :
        - Monstre -> dropdown Type de monstre + plages ATK / DEF / Niveau
        - Magie / Piège -> cases à cocher par sous-type (sélection multiple, OU logique)
        - Tous -> rien (interface épurée)
        """
        for w in self.frame_adv_dynamic.winfo_children():
            w.destroy()
        self._adv_range_entries = {}
        self.adv_monster_type_menu = None
        self._adv_monster_type_display_map = {}
        self._adv_subtype_vars = {}  # {subtype_api_value: ctk.BooleanVar}

        category = self._adv_category_display_map.get(self.adv_category_menu.get(), "all")

        if category == "Monster":
            self._adv_monster_type_display_map = {self.t("all"): None}
            display_values = [self.t("all")]
            for subtype in MONSTER_SUBTYPES:
                label = self.t(f"monster_{subtype}")
                display_values.append(label)
                self._adv_monster_type_display_map[label] = subtype

            ctk.CTkLabel(self.frame_adv_dynamic, text=self.t("adv_monster_type_label"), anchor="w", font=("Arial", 16)).pack(anchor="w")
            self.adv_monster_type_menu = ctk.CTkOptionMenu(
                self.frame_adv_dynamic, values=display_values, command=lambda _: self.update_suggestions(), font=("Arial", 16)
            )
            self.adv_monster_type_menu.pack(fill="x", pady=(2, 8))
            self.adv_monster_type_menu.set(self.t("all"))

            for key, label_key in (("atk", "adv_atk_label"), ("def", "adv_def_label"), ("level", "adv_level_label")):
                row = ctk.CTkFrame(self.frame_adv_dynamic, fg_color="transparent")
                row.pack(fill="x", pady=(0, 6))
                ctk.CTkLabel(row, text=self.t(label_key), width=60, anchor="w", font=("Arial", 16)).pack(side="left")
                min_entry = ctk.CTkEntry(row, width=65, placeholder_text="min", font=("Arial", 16))
                min_entry.pack(side="left", padx=(4, 4))
                min_entry.bind("<KeyRelease>", self.on_search_key_release)
                ctk.CTkLabel(row, text="–", width=10, font=("Arial", 16)).pack(side="left")
                max_entry = ctk.CTkEntry(row, width=65, placeholder_text="max", font=("Arial", 16))
                max_entry.pack(side="left", padx=(4, 0))
                max_entry.bind("<KeyRelease>", self.on_search_key_release)
                self._adv_range_entries[key] = (min_entry, max_entry)

        elif category in ("Spell", "Trap"):
            subtypes = SPELL_SUBTYPES if category == "Spell" else TRAP_SUBTYPES
            prefix = "spell" if category == "Spell" else "trap"

            ctk.CTkLabel(self.frame_adv_dynamic, text=self.t("adv_subtype_label"), anchor="w", font=("Arial", 16)).pack(anchor="w", pady=(0, 2))
            for subtype in subtypes:
                var = ctk.BooleanVar(value=False)
                cb = ctk.CTkCheckBox(
                    self.frame_adv_dynamic, text=self.t(f"{prefix}_{subtype}"), variable=var,
                    onvalue=True, offvalue=False, command=self.update_suggestions, font=("Arial", 16)
                )
                cb.pack(anchor="w", pady=1)
                self._adv_subtype_vars[subtype] = var

    def _advanced_filters_active(self):
        if not self._adv_search_visible:
            return False
        if self.adv_category_menu.get() not in (self.t("all"), ""):
            return True
        for min_entry, max_entry in self._adv_range_entries.values():
            if min_entry.get().strip() or max_entry.get().strip():
                return True
        if self.adv_monster_type_menu is not None and self.adv_monster_type_menu.get() not in (self.t("all"), ""):
            return True
        if any(var.get() for var in self._adv_subtype_vars.values()):
            return True
        return False

    def on_search_key_release(self, event):
        if self._search_timer:
            self.after_cancel(self._search_timer)
        self._search_timer = self.after(250, self.update_suggestions)

    @staticmethod
    def _parse_int(entry_widget):
        raw = entry_widget.get().strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _show_suggestions_overlay(self):
        """
        Positionne frame_suggestions juste sous la zone de recherche, en
        SURVOL du reste du panneau (via .place(), ancré sur search_container
        pour suivre sa position même si son contenu change — ex. recherche
        avancée dépliée/repliée). .lift() garantit qu'il s'affiche au-dessus
        de tout ce qu'il recouvre, peu importe l'ordre de création des widgets.
        """
        self.frame_suggestions.place(in_=self.search_container, relx=0, rely=1.0, relwidth=1.0, anchor="nw")
        self.frame_suggestions.lift()

    def _hide_suggestions_overlay(self):
        self.frame_suggestions.place_forget()

    def update_suggestions(self):
        for w in self.frame_suggestions.winfo_children():
            w.destroy()

        query = self.search_entry.get().lower().strip()
        filters_active = self._advanced_filters_active()
        if (len(query) <= 1 and not filters_active) or not self._search_index:
            self._hide_suggestions_overlay()
            return

        category_filter = self._adv_category_display_map.get(self.adv_category_menu.get(), "all") if self._adv_search_visible else "all"

        ranges = {}
        monster_type_filter = None
        if self._adv_search_visible:
            for key, (min_entry, max_entry) in self._adv_range_entries.items():
                ranges[key] = (self._parse_int(min_entry), self._parse_int(max_entry))
            if self.adv_monster_type_menu is not None:
                monster_type_filter = self._adv_monster_type_display_map.get(self.adv_monster_type_menu.get())

        # Sous-types Magie/Piège cochés (sélection multiple = OU logique). Aucune case
        # cochée = pas de restriction (toutes les Magies/tous les Pièges passent).
        checked_subtypes = {k for k, v in self._adv_subtype_vars.items() if v.get()} if self._adv_search_visible else set()

        def _in_range(value, bounds):
            lo, hi = bounds
            if lo is None and hi is None:
                return True
            if value is None:
                return False
            if lo is not None and value < lo:
                return False
            if hi is not None and value > hi:
                return False
            return True

        results = []
        for name_lower, card in self._search_index:
            if query and query not in name_lower:
                continue

            if category_filter != "all":
                if _card_category(card.get('type')) != category_filter:
                    continue
                if category_filter == "Monster":
                    if not _in_range(card.get('atk'), ranges.get('atk', (None, None))):
                        continue
                    if not _in_range(card.get('def'), ranges.get('def', (None, None))):
                        continue
                    if not _in_range(card.get('level'), ranges.get('level', (None, None))):
                        continue
                    if monster_type_filter and monster_type_filter not in str(card.get('type', '')):
                        continue
                elif category_filter in ("Spell", "Trap") and checked_subtypes:
                    if card.get('race') not in checked_subtypes:
                        continue

            results.append(card)
            if len(results) >= 30:
                break

        self.matches = results
        for card in self.matches:
            btn = ctk.CTkButton(
                self.frame_suggestions, text=card['name'], anchor="w", fg_color="transparent",
                text_color=("black", "white"), hover_color=("gray70", "gray30"), font=("Arial", 15),
                height=28, command=lambda c=card: self.on_select_card(c)
            )
            btn.pack(fill="x", pady=1, padx=2)
        if len(self.matches) >= 30:
            ctk.CTkLabel(
                self.frame_suggestions, text=self.t("search_results_truncated_hint"),
                font=("Arial", 13), text_color="gray55"
            ).pack(anchor="w", padx=4, pady=(2, 0))

        if self.matches:
            self._show_suggestions_overlay()
        else:
            self._hide_suggestions_overlay()

    def on_select_card(self, card):
        self.selected_card_id = backend.sanitize_id(card['id'])
        self.selected_card_name = card['name']
        self.selected_card_type = card.get('type', '')
        self.search_entry.delete(0, "end")
        self.search_entry.insert(0, card['name'])

        if backend.get_default_section(self.selected_card_type) == "Extra":
            self._card_is_extra_blocked = True
            self.update_card_description(self.t("extra_deck_blocked"))
            self.img_label.configure(text="", image=None)
        else:
            self._card_is_extra_blocked = False
            self.section_menu.set(self.section_label("Main"))
            self.update_card_description(card.get('desc', ''))
            threading.Thread(target=self._async_load_image, args=(card,), daemon=True).start()

        for w in self.frame_suggestions.winfo_children():
            w.destroy()
        self._hide_suggestions_overlay()

    def _async_load_image(self, card):
        img_path = backend.download_card_image(card)
        if img_path and os.path.exists(img_path):
            try:
                img_data = Image.open(img_path)
                ctk_img = ctk.CTkImage(light_image=img_data, dark_image=img_data, size=(140, 200))
                self.after(0, lambda: self.img_label.configure(image=ctk_img, text=""))
            except Exception as e:
                print(f"Image open error: {e}")

    def update_card_description(self, desc_text):
        self.card_desc_text.configure(state="normal")
        self.card_desc_text.delete("1.0", "end")
        self.card_desc_text.insert("1.0", desc_text if desc_text else self.t("no_desc"))
        self.card_desc_text.configure(state="disabled")

    def show_large_image(self, card_id):
        clean_cid = backend.sanitize_id(card_id)
        if not clean_cid:
            return
        img_path = os.path.join(backend.IMAGES_DIR, f"{clean_cid}.jpg")
        if not os.path.exists(img_path):
            return
        top = ctk.CTkToplevel(self)
        top.title(self.t("zoom_title"))
        top.geometry("420x610")
        top.attributes("-topmost", True)
        img_data = Image.open(img_path)
        ctk_img = ctk.CTkImage(light_image=img_data, dark_image=img_data, size=(400, 580))
        ctk.CTkLabel(top, image=ctk_img, text="", font=("Arial", 16)).pack(expand=True, fill="both", padx=10, pady=10)

    # --- ÉDITION CARTES ET COMBOS ---
    def _on_pioche_checkbox_toggled(self):
        if self.checks["Pioche"].get():
            self.pioche_count_menu.pack(side="left", padx=(8, 0))
        else:
            self.pioche_count_menu.pack_forget()

    def add_or_update_card(self):
        if not self.selected_card_id or self._card_is_extra_blocked:
            return

        clean_id = backend.sanitize_id(self.selected_card_id)
        section = self._section_display_map.get(self.section_menu.get(), "Main")

        # Limite de 3 exemplaires CUMULÉE entre Main et Side (règle officielle :
        # le total de copies disponibles dans la pool de cartes, toutes sections
        # confondues, ne peut jamais dépasser 3 — 2 en Main + 1 en Side est
        # valide, mais 3 en Main + 1 en Side ne l'est pas). Calculée AVANT toute
        # modification de self.df, pour ne jamais laisser le deck dans un état
        # partiel si l'ajout doit être refusé ou réduit.
        other_section = "Side" if section == "Main" else "Main"
        other_mask = (self.df["ID"].astype(str) == str(clean_id)) & (self.df["Section"] == other_section)
        other_count = int(self.df.loc[other_mask, "Quantite"].sum()) if other_mask.any() else 0
        max_allowed_here = max(0, backend.MAX_COPIES_PER_CARD - other_count)

        try:
            requested_qte = int(self.qte_menu.get())
        except ValueError:
            requested_qte = 3
        qte = min(requested_qte, max_allowed_here, backend.MAX_COPIES_PER_CARD)

        if max_allowed_here <= 0:
            messagebox.showwarning(
                self.t("copy_limit_title"),
                self.t("copy_limit_text").format(
                    name=self.selected_card_name, other=self.section_label(other_section), count=other_count
                )
            )
            return
        if qte < requested_qte:
            messagebox.showwarning(
                self.t("copy_limit_title"),
                self.t("copy_limit_reduced_text").format(
                    name=self.selected_card_name, qte=qte,
                    other=self.section_label(other_section), count=other_count
                )
            )

        mask_existing = (self.df["ID"].astype(str) == str(clean_id)) & (self.df["Section"] == section)
        self.df = self.df[~mask_existing]

        new_row = {
            "ID": str(clean_id), "Nom": str(self.selected_card_name), "Quantite": qte, "Section": section,
            **{cat: (1 if self.checks[cat].get() else 0) for cat in self.checks},
            "PiocheCount": int(self.pioche_count_menu.get()) if self.checks["Pioche"].get() else 0,
        }
        self.df = pd.concat([self.df, pd.DataFrame([new_row])], ignore_index=True)
        self.save_current_deck()
        self.refresh_ui()

    def edit_card_from_list(self, card_id, section):
        clean_cid = backend.sanitize_id(card_id)
        mask = (self.df["ID"].astype(str) == str(clean_cid)) & (self.df["Section"] == section)
        card_row = self.df[mask]
        if not card_row.empty:
            row = card_row.iloc[0]
            self.selected_card_id = str(row["ID"])
            self.selected_card_name = str(row["Nom"])
            self._card_is_extra_blocked = False

            self.search_entry.delete(0, "end")
            self.search_entry.insert(0, self.selected_card_name)
            self.qte_menu.set(str(row["Quantite"]))
            self.section_menu.set(self.section_label(section))

            for cat in self.checks:
                (self.checks[cat].select() if row.get(cat) == 1 else self.checks[cat].deselect())

            if row.get("Pioche") == 1:
                try:
                    count = int(row.get("PiocheCount", 1)) or 1
                except (TypeError, ValueError):
                    count = 1
                self.pioche_count_menu.set(str(max(1, min(3, count))))
                self.pioche_count_menu.pack(side="left", padx=(8, 0))
            else:
                self.pioche_count_menu.pack_forget()

            card_info = self.db_dict.get(str(clean_cid))
            self.selected_card_type = card_info.get('type', '') if card_info else ''
            self.update_card_description(card_info.get('desc', '') if card_info else '')

            threading.Thread(target=self._async_load_image, args=({"id": clean_cid},), daemon=True).start()

    def change_card_qty(self, card_id, section, delta):
        clean_cid = backend.sanitize_id(card_id)
        mask = (self.df["ID"].astype(str) == str(clean_cid)) & (self.df["Section"] == section)
        if mask.any():
            current_qty = int(self.df.loc[mask, "Quantite"].values[0])
            new_qty = current_qty + delta
            if new_qty <= 0:
                self.df = self.df[~mask]
            else:
                self.df.loc[mask, "Quantite"] = min(new_qty, backend.MAX_COPIES_PER_CARD)
            self._mark_deck_dirty()
            # Met à jour la ligne concernée de la liste texte INSTANTANÉMENT.
            # defer_grid_refresh=True : la grille d'images n'est PAS reconstruite
            # ici, même différée — elle reste marquée "sale" et ne se rattrape
            # qu'au changement d'onglet ou au clic sur "Sauvegarder le deck"
            # (voir _save_deck_explicit), pour ne plus jamais redéclencher une
            # reconstruction complète à chaque clic +/-.
            self._sync_deck_list_row(section, str(clean_cid))
            self.refresh_ui(defer_grid_refresh=True)

    def delete_card(self, index):
        self.df = self.df.drop(index)
        self.save_current_deck()
        self.refresh_ui()

    def add_combo_rule(self):
        c1_name = self.combo_card1_menu.get()
        c2_name = self.combo_card2_menu.get()
        any_text = self.t("any_card")
        main_df = self.df[self.df["Section"] == "Main"]

        if c1_name and c2_name and c1_name not in [self.t("select_c1"), self.t("no_cards")]:
            id1 = "ANY" if c1_name == any_text else backend.sanitize_id(str(main_df[main_df["Nom"] == c1_name].iloc[0]["ID"])) if not main_df[main_df["Nom"] == c1_name].empty else None
            id2 = "ANY" if c2_name == any_text else backend.sanitize_id(str(main_df[main_df["Nom"] == c2_name].iloc[0]["ID"])) if not main_df[main_df["Nom"] == c2_name].empty else None

            if id1 and id2:
                combo_pair = sorted([id1, id2])
                scope = getattr(self, "_combo_scope_display_map", {}).get(self.combo_scope_menu.get(), "all")
                new_entry = {"pair": combo_pair, "scope": scope}
                already_exists = any(e["pair"] == combo_pair and e.get("scope", "all") == scope for e in self.custom_combos)
                if not already_exists:
                    self.custom_combos.append(new_entry)
                    backend.save_combos_list(self.custom_combos, self.current_deck_name)
                    self.refresh_combos_ui()

    def delete_combo_rule(self, entry):
        if entry in self.custom_combos:
            self.custom_combos.remove(entry)
            backend.save_combos_list(self.custom_combos, self.current_deck_name)
            self.refresh_combos_ui()

    def refresh_combos_ui(self):
        """Rafraîchit le dropdown de portée + la liste complète des combos (page Combos Starters)."""
        if not hasattr(self, "combo_scope_menu"):
            return

        # Ordre demandé : Scénario de base (Deck Actuel) en premier choix, puis
        # chaque scénario, et "Tous les scénarios" en dernier (portée la plus large).
        scope_values = [self.t("combo_scope_base")] + [s["name"] for s in self.scenarios] + [self.t("combo_scope_all")]
        self._combo_scope_display_map = {self.t("combo_scope_base"): "base", self.t("combo_scope_all"): "all"}
        self._combo_scope_display_map.update({s["name"]: s["name"] for s in self.scenarios})
        current_scope = self.combo_scope_menu.get()
        self.combo_scope_menu.configure(values=scope_values)
        if current_scope not in scope_values:
            self.combo_scope_menu.set(scope_values[0])

        for widget in self.frame_combos_list.winfo_children():
            widget.destroy()

        main_df = self.df[self.df["Section"] == "Main"]
        scope_colors = {"all": "#1f6aa5", "base": "#2b8a3e"}
        for entry in self.custom_combos:
            pair = entry["pair"]
            scope = entry.get("scope", "all")
            txts = []
            for item in pair:
                if item == "ANY":
                    txts.append(f"<{self.t('any_card')}>")
                else:
                    n = main_df[main_df["ID"].astype(str) == str(item)]["Nom"].values
                    txts.append(n[0] if len(n) > 0 else item)

            frame = ctk.CTkFrame(self.frame_combos_list)
            frame.pack(fill="x", pady=2)
            ctk.CTkLabel(frame, text=f"{txts[0]} + {txts[1]}", font=("Arial", 14), anchor="w").pack(side="left", padx=5, fill="x", expand=True)
            if scope == "all":
                scope_label = self.t("combo_scope_all")
            elif scope == "base":
                scope_label = self.t("combo_scope_base")
            else:
                scope_label = scope
            ctk.CTkLabel(
                frame, text=f"  {scope_label}  ", font=("Arial", 14, "bold"), text_color="white",
                fg_color=scope_colors.get(scope, "#8a5a2b"), corner_radius=6
            ).pack(side="left", padx=4)
            ctk.CTkButton(frame, text="X", width=25, fg_color="red", command=lambda e=entry: self.delete_combo_rule(e), font=("Arial", 16)).pack(side="right")

        if not self.custom_combos:
            ctk.CTkLabel(
                self.frame_combos_list, text=self.t("combo_empty_hint"),
                wraplength=280, justify="left", font=("Arial", 14), text_color="gray60"
            ).pack(fill="x", padx=5, pady=10)

    # --- DECK & IMPORT/EXPORT ---
    def refresh_deck_list_menu(self):
        cleaned_files = backend.list_available_decks()
        self.deck_menu.configure(values=cleaned_files)
        self.deck_menu.set(backend.sanitize_filename(self.current_deck_name))
        self._update_active_deck_label()

    def _update_active_deck_label(self):
        """Met à jour l'indicateur du deck actif, visible sur toutes les pages."""
        name = backend.sanitize_filename(self.current_deck_name)
        if name.lower().endswith(".csv"):
            name = name[:-4]
        self.lbl_active_deck_global.configure(text=f"🎴 {name}")

    def _load_scenarios_for_current_deck(self):
        self.scenarios = backend.load_scenarios_list(self.current_deck_name)
        self._seed_standard_scenarios_if_needed()
        self.active_scenario_index = None
        self.analysis_target = None
        self.compare_mode = False
        self.compare_selection = []
        self._migrate_combos_and_scenarios()

    def _seed_standard_scenarios_if_needed(self):
        """
        Garantit la présence des scénarios standards (STANDARD_SCENARIOS) dans
        self.scenarios, à CHAQUE chargement de deck (pas seulement le premier) :
        contrairement aux conditions personnalisées, ces scénarios sont
        "protected" (non supprimables par l'utilisateur — voir
        _delete_active_scenario), ils doivent donc toujours exister, réunis
        avec les scénarios personnalisés dans la même liste, sans action
        manuelle de l'utilisateur.

        Identifiés par un `template_key` STABLE (indépendant de la langue),
        pas par leur nom affiché : comparer par nom traduit casserait dès que
        l'utilisateur change la langue de l'interface (le nom traduit change,
        donc un simple `s["name"] == name` ne reconnaîtrait plus le scénario
        déjà créé et en recréerait un doublon à chaque rechargement). Le nom
        affiché d'un scénario déjà présent est aussi resynchronisé sur la
        langue courante à chaque appel, pour rester cohérent même après un
        changement de langue.
        """
        existing_by_key = {s.get("template_key"): s for s in self.scenarios if s.get("template_key")}

        # Migration rétroactive : un scénario standard créé via l'ANCIEN sélecteur
        # séparé (avant ce changement) est "protected" mais n'a pas encore de
        # template_key — le reconnaître par son nom, dans TOUTES les langues
        # possibles (il a pu être créé alors que l'app était dans une autre
        # langue que l'actuelle), pour éviter de le dupliquer.
        unresolved_protected = [
            s for s in self.scenarios if s.get("protected") and not s.get("template_key")
        ]
        if unresolved_protected:
            for name_key, _ in STANDARD_SCENARIOS:
                if name_key in existing_by_key:
                    continue
                possible_names = {
                    lang_dict[name_key] for lang_dict in TRANSLATIONS.values() if name_key in lang_dict
                }
                match = next((s for s in unresolved_protected if s["name"] in possible_names), None)
                if match is not None:
                    match["template_key"] = name_key
                    existing_by_key[name_key] = match
                    unresolved_protected.remove(match)

        changed = False
        for name_key, turn_order in STANDARD_SCENARIOS:
            current_name = self.t(name_key)
            existing = existing_by_key.get(name_key)
            if existing is not None:
                if existing["name"] != current_name:
                    existing["name"] = current_name
                    changed = True
                continue
            new_scenario = backend.new_scenario(current_name, turn_order=turn_order)
            new_scenario["protected"] = True
            new_scenario["template_key"] = name_key
            self.scenarios.append(new_scenario)
            changed = True
        if changed:
            backend.save_scenarios_list(self.scenarios, self.current_deck_name)

    def _migrate_combos_and_scenarios(self):
        """
        Normalise self.custom_combos vers le format unifié {"pair": [id1, id2],
        "scope": "all" | nom_de_scenario} (portée : "all" = tous les scénarios +
        le Deck Actuel ; un nom de scénario = seulement ce scénario-là).

        Récupère aussi les anciens combos qui vivaient DANS chaque scénario
        (scenario["combos"]) et les fusionne dans cette liste unique avec
        scope = nom du scénario, pour n'avoir plus qu'un seul endroit où tous
        les combos sont gérés (page "Combos Starters").
        """
        normalized = []
        changed = False
        for entry in self.custom_combos:
            if isinstance(entry, dict) and "pair" in entry:
                normalized.append({"pair": list(entry["pair"]), "scope": entry.get("scope") or "all"})
            elif isinstance(entry, (list, tuple)):
                normalized.append({"pair": list(entry), "scope": "all"})
                changed = True
        self.custom_combos = normalized

        for scenario in self.scenarios:
            legacy = scenario.get("combos")
            if legacy:
                for pair in legacy:
                    self.custom_combos.append({"pair": list(pair), "scope": scenario["name"]})
                scenario["combos"] = []
                changed = True

        if changed:
            backend.save_combos_list(self.custom_combos, self.current_deck_name)
            backend.save_scenarios_list(self.scenarios, self.current_deck_name)

    def _seed_default_conditions_if_needed(self):
        """
        Pré-remplit des conditions personnalisées par défaut (voir
        DEFAULT_CONDITIONS_FIRST/SECOND), reflétant la formule déjà intégrée au
        calcul standard de l'app, UNIQUEMENT si le fichier de conditions de ce
        deck n'existe pas encore sur disque (tout premier chargement). Une fois
        que l'utilisateur a sauvegardé quoi que ce soit (même en ayant tout
        supprimé), ce pré-remplissage ne se reproduit jamais — ses choix sont
        toujours respectés.
        """
        path = backend.get_conditions_path(self.current_deck_name)
        if os.path.exists(path):
            return

        def _build_side(entries):
            conditions = []
            for i, (name_key, clauses) in enumerate(entries):
                built_clauses = []
                for cat, op, value, connector in clauses:
                    clause = {"category": cat, "op": op, "value": value}
                    if connector is not None:
                        clause["connector"] = connector
                    built_clauses.append(clause)
                cond = {"name": self.t(name_key), "clauses": built_clauses}
                if i > 0:
                    cond["operator"] = "or"  # OU par défaut entre les conditions pré-remplies
                conditions.append(cond)
            return {"conditions": conditions}

        self.custom_conditions = {
            "first": _build_side(DEFAULT_CONDITIONS_FIRST),
            "second": _build_side(DEFAULT_CONDITIONS_SECOND),
        }
        backend.save_conditions(self.custom_conditions, self.current_deck_name)

    def change_deck(self, selected_deck):
        if not self._confirm_discard_deck_changes():
            self.deck_menu.set(backend.sanitize_filename(self.current_deck_name))
            return
        if not self._confirm_discard_scenario_changes():
            self.deck_menu.set(backend.sanitize_filename(self.current_deck_name))
            return
        self.current_deck_name = backend.sanitize_filename(selected_deck)
        self._update_active_deck_label()
        self.load_data()
        self._load_scenarios_for_current_deck()
        self.refresh_ui()
        self.download_missing_images_async()

    def create_new_deck(self):
        dialog = ctk.CTkInputDialog(text=self.t("enter_new_deck"), title="New Deck")
        raw_name = dialog.get_input()
        if raw_name:
            safe_name = backend.sanitize_filename(raw_name)
            self.current_deck_name = safe_name
            self._update_active_deck_label()
            self.df = pd.DataFrame(columns=backend.DECK_COLUMNS)
            self.save_current_deck()
            self.refresh_deck_list_menu()
            self._load_scenarios_for_current_deck()
            self.refresh_ui()

    def rename_current_deck(self):
        dialog = ctk.CTkInputDialog(text=self.t("rename_deck_prompt"), title=self.t("rename_deck_title"))
        raw_name = dialog.get_input()
        if not raw_name:
            return
        new_name = backend.sanitize_filename(raw_name)
        ok = backend.rename_deck(self.current_deck_name, new_name)
        if not ok:
            messagebox.showwarning(self.t("rename_deck_conflict_title"), self.t("rename_deck_conflict_text"))
            return
        self.current_deck_name = new_name
        self._update_active_deck_label()
        self.refresh_deck_list_menu()

    def delete_current_deck(self):
        """
        Supprime définitivement le deck actif (CSV + combos + scénarios +
        conditions personnalisées associés). Toujours au moins un deck doit
        rester disponible — la suppression est bloquée si c'est le dernier.
        """
        available = backend.list_available_decks()
        if len(available) <= 1:
            messagebox.showwarning(self.t("cannot_delete_last_deck_title"), self.t("cannot_delete_last_deck_text"))
            return

        confirmed = messagebox.askyesno(
            self.t("delete_deck_confirm_title"),
            self.t("delete_deck_confirm_text").format(name=self.current_deck_name)
        )
        if not confirmed:
            return

        deck_to_delete = backend.sanitize_filename(self.current_deck_name)
        remaining = [d for d in available if d != deck_to_delete]
        next_deck = remaining[0] if remaining else "default_deck.csv"

        backend.delete_deck(deck_to_delete)
        self.change_deck(next_deck)
        self.refresh_deck_list_menu()

    def save_current_deck(self):
        backend.save_deck_df(self.df, self.current_deck_name)

    def _mark_deck_dirty(self):
        """
        Signale une modification du deck SANS la sauvegarder sur disque —
        remplace l'ancienne sauvegarde automatique à chaque clic sur +/-/X,
        qui écrivait le fichier CSV à chaque fois même si l'écriture en
        elle-même est rapide, ça s'ajoutait à chaque interaction. L'utilisateur
        doit désormais cliquer explicitement sur "Sauvegarder le deck" (voir
        _save_deck_explicit) ; s'il tente de changer de deck avant ça, une
        confirmation lui est demandée (voir _confirm_discard_deck_changes).
        """
        self._deck_unsaved_changes = True
        self._update_deck_save_button_state()

    def _update_deck_save_button_state(self):
        if not hasattr(self, "btn_save_deck"):
            return
        if self._deck_unsaved_changes:
            self.btn_save_deck.configure(text=f"💾 {self.t('deck_save_pending')}", fg_color="#d9822b", hover_color="#b8690f")
        else:
            self.btn_save_deck.configure(text=f"💾 {self.t('deck_save')}", fg_color="#2b8a3e", hover_color="#1e602b")

    def _save_deck_explicit(self):
        self.save_current_deck()
        self._deck_unsaved_changes = False
        self._update_deck_save_button_state()
        # Rattrape immédiatement (pas différé) la grille d'images de l'onglet
        # actuellement visible si elle était restée "sale" depuis des clics
        # +/-/X précédents (voir refresh_ui(defer_grid_refresh=True)) — c'est
        # le moment naturel où l'utilisateur s'attend à voir tout à jour.
        self._refresh_visible_deck_tab_if_dirty()

    def _confirm_discard_deck_changes(self):
        """
        S'il reste des modifications du deck non sauvegardées, demande à
        l'utilisateur s'il veut les sauvegarder avant de continuer (changement
        de deck OU changement de page — les deux appellent cette fonction).
        Retourne True si on peut continuer (rien à sauvegarder, sauvegardé, ou
        l'utilisateur a choisi d'abandonner les changements), False si annulé.
        """
        if not self._deck_unsaved_changes:
            return True
        choice = messagebox.askyesnocancel(self.t("deck_unsaved_title"), self.t("deck_unsaved_text"))
        if choice is None:  # Annuler : reste sur le deck actuel
            return False
        if choice:  # Oui : sauvegarder puis continuer
            self._save_deck_explicit()
            return True
        # Non : abandonne les changements — recharge explicitement depuis le
        # disque ICI (ne suppose plus qu'un load_data() suivra forcément côté
        # appelant : change_deck en fait un juste après, mais un simple
        # changement de page, lui, n'en ferait aucun, laissant self.df avec des
        # changements "abandonnés" en apparence mais toujours actifs en mémoire).
        self.load_data()
        self._deck_unsaved_changes = False
        self._update_deck_save_button_state()
        self.refresh_ui()
        return True

    def load_data(self):
        self.df = backend.load_deck_df(self.current_deck_name, self.db_dict)
        self.custom_combos = backend.load_combos_list(self.current_deck_name)
        self.scenarios = backend.load_scenarios_list(self.current_deck_name)
        self.custom_conditions = backend.load_conditions(self.current_deck_name)
        self._migrate_combos_and_scenarios()
        self._seed_default_conditions_if_needed()

    def export_ydk(self):
        safe_name = backend.sanitize_filename(self.current_deck_name)
        default_filename = safe_name.replace(".csv", ".ydk")
        exports_dir = os.path.abspath(backend.EXPORTS_DIR)

        ydk_path = filedialog.asksaveasfilename(
            initialdir=exports_dir, initialfile=default_filename, defaultextension=".ydk",
            filetypes=[("YDK files", "*.ydk"), ("All files", "*.*")], title=self.t("export_dialog_title")
        )
        if not ydk_path:
            return

        backend.export_to_ydk(self.df, ydk_path)
        folder_path = os.path.dirname(os.path.abspath(ydk_path))
        if os.path.exists(folder_path):
            if os.name == 'nt':
                os.startfile(folder_path)
            else:
                cmd = 'open' if sys.platform == 'darwin' else 'xdg-open'
                subprocess.Popen([cmd, folder_path])

    def import_ydk(self):
        imports_dir = os.path.abspath(backend.IMPORTS_DIR)
        filepath = filedialog.askopenfilename(
            initialdir=imports_dir, title=self.t("import_dialog_title"),
            filetypes=[("YDK files", "*.ydk"), ("All files", "*.*")]
        )
        if not filepath:
            return

        new_df = backend.parse_ydk_file(filepath, self.db_dict)
        if new_df.empty:
            return

        self.df = new_df
        raw_filename = os.path.basename(filepath)
        safe_name = backend.sanitize_filename(raw_filename.replace(".ydk", ".csv"))
        self.current_deck_name = safe_name

        self.save_current_deck()
        self.refresh_deck_list_menu()
        self._load_scenarios_for_current_deck()
        self.refresh_ui()
        self.download_missing_images_async()

    def import_from_url(self):
        """Importe un deck depuis un lien ydke:// ou une URL directe vers un fichier .ydk brut."""
        dialog = ctk.CTkInputDialog(text=self.t("import_url_prompt"), title=self.t("import_url_title"))
        url = dialog.get_input()
        if not url:
            return

        new_df = backend.fetch_deck_from_url(url.strip(), self.db_dict)
        if new_df is None or new_df.empty:
            messagebox.showerror(self.t("import_url_title"), self.t("import_url_error"))
            return

        self.df = new_df
        safe_name = backend.sanitize_filename(f"url_import_{abs(hash(url)) % 100000}.csv")
        self.current_deck_name = safe_name

        self.save_current_deck()
        self.refresh_deck_list_menu()
        self._load_scenarios_for_current_deck()
        self.refresh_ui()
        self.download_missing_images_async()

    # --- IMAGES : TÉLÉCHARGEMENT AUTOMATIQUE EN ARRIÈRE-PLAN ---
    def download_missing_images_async(self):
        """
        Télécharge les images manquantes en parallèle (5 threads), via
        backend.download_images_bulk qui respecte la limite de 20 requêtes/
        seconde de l'API ygoprodeck. Nettement plus rapide qu'un téléchargement
        séquentiel sur un import de deck avec de nombreuses images manquantes.
        """
        if self.df.empty or self._image_download_in_progress:
            return

        ids = self.df.loc[self.df["Section"].isin(["Main", "Side"]), "ID"].astype(str).unique().tolist()
        missing_ids = [
            backend.sanitize_id(cid) for cid in ids
            if not os.path.exists(os.path.join(backend.IMAGES_DIR, f"{backend.sanitize_id(cid)}.jpg"))
        ]
        missing_cards = [self.db_dict[cid] for cid in missing_ids if cid in self.db_dict]
        if not missing_cards:
            return

        self._image_download_in_progress = True
        total = len(missing_cards)
        self.lbl_download_status.configure(text=self.t("downloading_images_status").format(done=0, total=total))

        def on_progress(done, total_count):
            if done % 5 == 0 or done == total_count:
                self.after(0, lambda d=done, t=total_count: self._on_image_download_progress(d, t))

        def worker():
            backend.download_images_bulk(missing_cards, on_progress=on_progress, max_workers=5)
            self.after(0, self._on_image_download_finished)

        threading.Thread(target=worker, daemon=True).start()

    def _on_image_download_progress(self, done, total):
        self.lbl_download_status.configure(text=self.t("downloading_images_status").format(done=done, total=total))
        self.refresh_ui()

    def _on_image_download_finished(self):
        self._image_download_in_progress = False
        self.lbl_download_status.configure(text="")
        self.refresh_ui()

    # --- RAFRAÎCHISSEMENT : PAGE CONSTRUCTION ---
    def get_cached_thumb(self, img_path):
        """
        Cache LRU borné : évite de recharger/redimensionner une image déjà vue,
        tout en empêchant la mémoire de grossir indéfiniment sur une session
        longue avec plusieurs decks (limite : self._img_cache_max_size images).
        """
        if img_path in self.img_cache:
            self.img_cache.move_to_end(img_path)
            return self.img_cache[img_path]

        img_data = Image.open(img_path)
        ctk_img = ctk.CTkImage(light_image=img_data, dark_image=img_data, size=(60, 85))
        self.img_cache[img_path] = ctk_img
        self.img_cache.move_to_end(img_path)

        if len(self.img_cache) > self._img_cache_max_size:
            self.img_cache.popitem(last=False)  # évince l'entrée la moins récemment utilisée

        return ctk_img

    def refresh_extra_info(self):
        count = int(self.df.loc[self.df["Section"] == "Extra", "Quantite"].sum()) if not self.df.empty else 0
        self.lbl_extra_info.configure(text=self.t("extra_deck_info").format(count=count) if count > 0 else "")

    def refresh_size_status(self):
        sizes = backend.validate_deck_size(self.df)
        for section in EDIT_SECTIONS:
            info = sizes[section]
            color = "#2b8a3e" if info["valid"] else "#d9480f"
            self.lbl_size[section].configure(
                text=f"{self.section_label(section)} : {info['count']} / {info['min']}-{info['max']}", text_color=color
            )

    def refresh_ui(self, defer_grid_refresh=False):
        self.refresh_size_status()
        self.refresh_extra_info()
        self._update_pioche_extension_availability()

        card_names = [self.t("any_card")] + (
            self.df.loc[self.df["Section"] == "Main", "Nom"].tolist() if not self.df.empty else []
        )
        self.combo_card1_menu.configure(values=card_names)
        self.combo_card2_menu.configure(values=card_names)
        self.combo_card1_menu.set(card_names[1] if len(card_names) > 1 else card_names[0])
        self.combo_card2_menu.set(card_names[0])

        # Les données ont changé : les deux onglets Main/Side sont "sales", mais on
        # ne reconstruit immédiatement que celui actuellement visible (l'autre sera
        # reconstruit paresseusement au moment où l'utilisateur clique dessus, via
        # _on_deck_tab_change). Si defer_grid_refresh=True (utilisé par les clics
        # +/-/X, qui mettent déjà à jour la liste texte INSTANTANÉMENT via
        # _sync_deck_list_row), on ne planifie même PAS la reconstruction
        # différée de la grille d'images ici : elle reste "sale" mais n'est
        # reconstruite qu'au changement d'onglet ou au clic sur "Sauvegarder le
        # deck" (voir _save_deck_explicit) — sinon, même différée de 200ms,
        # cette reconstruction complète se redéclenchait après CHAQUE clic et
        # restait la cause principale de la lenteur ressentie.
        for section in EDIT_SECTIONS:
            self._dirty_sections[section] = True
        if not defer_grid_refresh:
            self._schedule_deck_tab_refresh()

        # Même logique pour les listes de la page Combos Starters et de
        # l'éditeur de Scénario : marquées "sales" ici, mais reconstruites
        # PARESSEUSEMENT seulement quand l'utilisateur navigue réellement sur
        # ces pages (voir _on_page_switch) — pas à chaque clic +/- fait ici,
        # en Construction du Deck, sur une page qu'il ne regarde même pas.
        self._combos_ui_dirty = True
        self._scenario_editor_dirty = True
        if getattr(self, "_current_page_key", None) == "combos":
            self.refresh_combos_ui()
            self._combos_ui_dirty = False
        if getattr(self, "_current_page_key", None) == "scenarios":
            self.refresh_scenario_editor_list()
            self.refresh_scenario_editor_detail()
            self._scenario_editor_dirty = False

        self.refresh_analysis_target_menus()

    def _update_pioche_extension_availability(self):
        """
        Active/désactive la case "Cartes Pioche" selon que le deck actuel
        contient réellement au moins une carte de catégorie Pioche — pas la
        peine de proposer une extension qui n'aurait aucun effet. Si elle est
        désactivée alors qu'elle était cochée, on la décoche aussi (sinon un
        calcul pourrait lire un état coché mais grisé, jamais nettoyé).
        """
        has_pioche_card = (
            not self.df.empty and "Pioche" in self.df.columns
            and (self.df["Pioche"].astype(str) == "1").any()
        )
        if has_pioche_card:
            self.check_extend_pioche.configure(state="normal")
            self.lbl_pioche_extension_hint.configure(text="")
        else:
            if self.check_extend_pioche.get():
                self.check_extend_pioche.deselect()
            self.check_extend_pioche.configure(state="disabled")
            self.lbl_pioche_extension_hint.configure(text=self.t("pioche_extension_unavailable_hint"))
        # deselect() ci-dessus ne déclenche PAS le command= de la case (seul un
        # clic utilisateur le ferait) : resynchronise donc explicitement le
        # rappel de la comparaison pour ne jamais rester sur un état périmé.
        self._update_compare_extensions_hint()

    def _update_compare_extensions_hint(self):
        """
        Rappelle, dans le panneau de comparaison, quelles extensions (Combos /
        Cartes Pioche) sont actuellement cochées dans le panneau principal —
        la comparaison les réutilise telles quelles, sans sélecteur séparé.
        """
        if not hasattr(self, "lbl_compare_extensions_hint"):
            return
        use_combos = bool(self.check_extend_combos.get())
        use_pioche = bool(self.check_extend_pioche.get())
        if use_combos and use_pioche:
            text = self.t("compare_extensions_both")
        elif use_combos:
            text = self.t("compare_extensions_combos")
        elif use_pioche:
            text = self.t("compare_extensions_pioche")
        else:
            text = self.t("compare_extensions_none")
        self.lbl_compare_extensions_hint.configure(text=text)

    def _current_deck_tab_section(self):
        current = self.tabview.get()
        for section in EDIT_SECTIONS:
            if f"{section} Deck" == current:
                return section
        return None

    def _schedule_deck_tab_refresh(self):
        """
        Différé (debounce, 200ms) de la reconstruction de la grille d'images du
        deck visible : regroupe les rafales d'éditions rapides (ex. ajuster une
        quantité plusieurs fois de suite, ajouter plusieurs cartes coup sur coup)
        en une seule reconstruction, au lieu d'une par action.
        """
        if getattr(self, "_deck_tab_refresh_after_id", None) is not None:
            self.after_cancel(self._deck_tab_refresh_after_id)
        self._deck_tab_refresh_after_id = self.after(200, self._flush_deck_tab_refresh)

    def _flush_deck_tab_refresh(self):
        self._deck_tab_refresh_after_id = None
        self._refresh_visible_deck_tab_if_dirty()

    def _refresh_visible_deck_tab_if_dirty(self):
        section = self._current_deck_tab_section()
        if section is None or not self._dirty_sections.get(section, True):
            return
        target_cat = self._get_filter_target_cat(section)
        self._refresh_section_tab(section, target_cat)
        self._dirty_sections[section] = False

    def _get_filter_target_cat(self, section):
        menu = self.filter_category if section == "Main" else self.filter_category_side
        selected_cat_raw = menu.get()
        cat_map_inv = {self.t(c): c for c in ["Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick", "Pioche"]}
        return cat_map_inv.get(selected_cat_raw, None)

    def _on_deck_tab_change(self):
        self._refresh_visible_deck_tab_if_dirty()

    def _build_deck_list_row_text(self, row_data):
        cats = []
        for c in self.checks.keys():
            if row_data.get(c) != 1:
                continue
            if c == "Pioche":
                try:
                    n = int(row_data.get("PiocheCount", 0))
                except (TypeError, ValueError):
                    n = 0
                cats.append(f"{self.t(c)} (+{n})" if n > 0 else self.t(c))
            else:
                cats.append(self.t(c))
        return f"{row_data['Nom']} (x{row_data['Quantite']})" + (f" | {', '.join(cats)}" if cats else "")

    def _upsert_deck_list_row(self, section, card_id, row_index, row_data):
        """
        Crée ou met à jour UNE SEULE ligne de la liste texte (Construction du
        Deck) — si elle existe déjà, seuls son texte et ses commandes de
        bouton sont mis à jour (pas de destruction/recréation), sinon elle
        est créée. La grille d'images (frame_visual), elle, n'est PAS
        concernée par cette fonction — elle continue d'être entièrement
        reconstruite par _refresh_section_tab, sans changement.
        """
        key = (section, card_id)
        txt = self._build_deck_list_row_text(row_data)
        existing = self._deck_list_row_widgets.get(key)

        if existing is not None:
            existing["btn_text"].configure(
                text=txt, command=lambda cid=card_id, sec=section: self.edit_card_from_list(cid, sec)
            )
            existing["btn_delete"].configure(command=lambda idx=row_index: self.delete_card(idx))
            existing["btn_plus"].configure(command=lambda cid=card_id, sec=section: self.change_card_qty(cid, sec, 1))
            existing["btn_minus"].configure(command=lambda cid=card_id, sec=section: self.change_card_qty(cid, sec, -1))
            return

        frame_list = self.tab_frames[section]["list"]
        frame = ctk.CTkFrame(frame_list)
        frame.pack(fill="x", pady=2, padx=5)
        btn_text = ctk.CTkButton(
            frame, text=txt, anchor="w", fg_color="transparent", text_color=("black", "white"),
            hover_color=("gray70", "gray30"),
            command=lambda cid=card_id, sec=section: self.edit_card_from_list(cid, sec), font=("Arial", 16)
        )
        btn_text.pack(side="left", padx=5, fill="x", expand=True)
        btn_delete = ctk.CTkButton(frame, text="X", width=28, fg_color="red",
                                    command=lambda idx=row_index: self.delete_card(idx), font=("Arial", 16))
        btn_delete.pack(side="right", padx=2)
        btn_plus = ctk.CTkButton(frame, text="+", width=28, fg_color="#2b8a3e",
                                  command=lambda cid=card_id, sec=section: self.change_card_qty(cid, sec, 1), font=("Arial", 16))
        btn_plus.pack(side="right", padx=2)
        btn_minus = ctk.CTkButton(frame, text="-", width=28, fg_color="#d9480f",
                                   command=lambda cid=card_id, sec=section: self.change_card_qty(cid, sec, -1), font=("Arial", 16))
        btn_minus.pack(side="right", padx=2)
        self._deck_list_row_widgets[key] = {
            "frame": frame, "btn_text": btn_text, "btn_delete": btn_delete, "btn_plus": btn_plus, "btn_minus": btn_minus
        }

    def _sync_deck_list_row(self, section, card_id):
        """
        Met à jour INSTANTANÉMENT UNIQUEMENT la ligne de la liste texte
        concernée par ce card_id précis, après un clic +/- ou une suppression
        — sans attendre le rendu différé complet (qui, lui, continue de
        s'occuper de la grille d'images, laissée inchangée comme demandé).
        Respecte le filtre de catégorie actuellement actif : une carte qui ne
        correspond plus au filtre n'est pas affichée, même si elle existe
        toujours dans le deck.
        """
        key = (section, card_id)
        mask = (self.df["ID"].astype(str) == card_id) & (self.df["Section"] == section)
        matching = self.df[mask]

        target_cat = self._get_filter_target_cat(section)
        should_show = not matching.empty and (not target_cat or matching.iloc[0].get(target_cat) == 1)

        if not should_show:
            existing = self._deck_list_row_widgets.get(key)
            if existing is not None:
                try:
                    existing["frame"].destroy()
                except Exception:
                    pass
                del self._deck_list_row_widgets[key]
        else:
            row_index = matching.index[0]
            row_data = matching.iloc[0]
            self._upsert_deck_list_row(section, card_id, row_index, row_data)

        frame_list = self.tab_frames[section]["list"]
        section_df = self.df[self.df["Section"] == section] if not self.df.empty else self.df
        total_cards = int(section_df["Quantite"].sum()) if not section_df.empty else 0
        frame_list.configure(label_text=self.t("deck_list").format(count=total_cards))
        lbl_total = self.lbl_filter_total if section == "Main" else self.lbl_filter_total_side
        if target_cat and not section_df.empty:
            filtered_total = int(section_df.loc[section_df[target_cat] == 1, "Quantite"].sum())
            lbl_total.configure(text=self.t("filter_total_format").format(count=filtered_total))
        else:
            lbl_total.configure(text="")

    def _refresh_section_tab(self, section, target_cat):
        frames = self.tab_frames[section]
        frame_visual, frame_list = frames["visual"], frames["list"]

        for widget in frame_visual.winfo_children():
            widget.destroy()
        for widget in frame_list.winfo_children():
            widget.destroy()
        # Le rendu complet reconstruit toujours tout : purge les references de
        # suivi de CETTE section pour ne pas laisser trainer des references
        # vers des widgets qui viennent d'etre detruits ci-dessus (la mise a
        # jour ciblee _sync_deck_list_row s'appuie sur ce suivi pour savoir si
        # une ligne existe deja ou doit etre creee).
        for key in [k for k in self._deck_list_row_widgets if k[0] == section]:
            del self._deck_list_row_widgets[key]

        section_df = self.df[self.df["Section"] == section] if not self.df.empty else self.df
        total_cards = int(section_df["Quantite"].sum()) if not section_df.empty else 0
        frame_list.configure(label_text=self.t("deck_list").format(count=total_cards))
        frame_visual.configure(label_text=f"{self.section_label(section)}")

        lbl_total = self.lbl_filter_total if section == "Main" else self.lbl_filter_total_side
        if target_cat and not section_df.empty:
            filtered_total = int(section_df.loc[section_df[target_cat] == 1, "Quantite"].sum())
            lbl_total.configure(text=self.t("filter_total_format").format(count=filtered_total))
        else:
            lbl_total.configure(text="")

        # Nombre de colonnes calculé à partir de la largeur RÉELLEMENT
        # disponible (pas une valeur fixe à 8) : sur un panneau large, la
        # grille utilise tout l'espace au lieu de laisser un grand vide à
        # droite ; sur un panneau étroit, elle reste lisible sans déborder.
        frame_visual.update_idletasks()
        available_width = frame_visual.winfo_width()
        cell_width = 64  # vignette 60px + 2px de marge de chaque côté
        n_cols = max(4, min(20, available_width // cell_width)) if available_width > 1 else 8

        col, row = 0, 0
        for _, card in section_df.iterrows():
            if target_cat and card.get(target_cat) != 1:
                continue
            card_id = backend.sanitize_id(card['ID'])
            img_path = os.path.join(backend.IMAGES_DIR, f"{card_id}.jpg")
            is_pioche_card = card.get('Pioche') == 1
            try:
                pioche_n = int(card.get('PiocheCount', 0)) if is_pioche_card else 0
            except (TypeError, ValueError):
                pioche_n = 0
            if os.path.exists(img_path):
                try:
                    ctk_img = self.get_cached_thumb(img_path)
                    for _ in range(int(card['Quantite'])):
                        cell = ctk.CTkFrame(frame_visual, fg_color="transparent")
                        cell.grid(row=row, column=col, padx=2, pady=2)
                        lbl = ctk.CTkLabel(cell, image=ctk_img, text="", cursor="hand2", font=("Arial", 16))
                        lbl.pack()
                        lbl.bind("<Button-1>", lambda e, cid=card_id: self.show_large_image(cid))
                        if is_pioche_card and pioche_n > 0:
                            # Badge "+N" en superposition (coin bas-droit) : combien de
                            # cartes cette carte Pioche fait piocher en plus.
                            ctk.CTkLabel(
                                cell, text=f"+{pioche_n}", font=("Arial", 15, "bold"),
                                fg_color="#1f9e5a", text_color="white", corner_radius=6,
                                width=22, height=16
                            ).place(relx=1.0, rely=1.0, anchor="se", x=-2, y=-2)
                        col += 1
                        if col >= n_cols:
                            col, row = 0, row + 1
                except Exception as e:
                    print(f"Error rendering thumbnail: {e}")

        for i, row_data in section_df.iterrows():
            if target_cat and row_data.get(target_cat) != 1:
                continue
            self._upsert_deck_list_row(section, str(row_data["ID"]), i, row_data)

    # ==================================================================
    # GESTION DES SCÉNARIOS DE SIDEBOARD (onglet "Combos & Scénarios")
    # ==================================================================
    def create_scenario(self):
        dialog = ctk.CTkInputDialog(text=self.t("scenario_name_prompt"), title=self.t("scenario_new"))
        name = dialog.get_input()
        if name:
            self.scenarios.append(backend.new_scenario(name))
            backend.save_scenarios_list(self.scenarios, self.current_deck_name)
            self.active_scenario_index = len(self.scenarios) - 1
            self.refresh_scenario_editor_list()
            self.refresh_scenario_editor_detail()
            self.refresh_analysis_target_menus()
            self.refresh_combos_ui()

    def rename_scenario(self, index):
        dialog = ctk.CTkInputDialog(text=self.t("scenario_rename_prompt"), title=self.t("scenario_rename"))
        name = dialog.get_input()
        if name:
            old_name = self.scenarios[index]["name"]
            self.scenarios[index]["name"] = name
            # Les combos attribués spécifiquement à ce scénario doivent suivre le
            # renommage (sinon leur portée pointerait vers un nom qui n'existe plus).
            for entry in self.custom_combos:
                if entry.get("scope", "all") == old_name:
                    entry["scope"] = name
            backend.save_scenarios_list(self.scenarios, self.current_deck_name)
            backend.save_combos_list(self.custom_combos, self.current_deck_name)
            self.refresh_scenario_editor_list()
            self.refresh_scenario_editor_detail()
            self.refresh_analysis_target_menus()
            self.refresh_combos_ui()

    def duplicate_scenario(self, index):
        original_name = self.scenarios[index]["name"]
        dup = copy.deepcopy(self.scenarios[index])
        dup["name"] = original_name + " (copie)"
        self.scenarios.insert(index + 1, dup)
        # Les combos spécifiques au scénario d'origine sont aussi dupliqués, pour
        # que la copie se comporte à l'identique dès sa création.
        new_combo_entries = [
            {"pair": list(entry["pair"]), "scope": dup["name"]}
            for entry in self.custom_combos if entry.get("scope", "all") == original_name
        ]
        self.custom_combos.extend(new_combo_entries)
        backend.save_scenarios_list(self.scenarios, self.current_deck_name)
        backend.save_combos_list(self.custom_combos, self.current_deck_name)
        self.active_scenario_index = index + 1
        self.refresh_scenario_editor_list()
        self.refresh_scenario_editor_detail()
        self.refresh_analysis_target_menus()
        self.refresh_combos_ui()

    def delete_scenario(self, index):
        if self.scenarios[index].get("protected"):
            messagebox.showwarning(self.t("scenario_protected_title"), self.t("scenario_protected_text"))
            return

        deleted_name = self.scenarios[index]["name"]
        del self.scenarios[index]
        # Les combos attribués spécifiquement à ce scénario disparaissent avec lui
        # (ils n'ont plus de sens sans lui) ; les combos "Tous les scénarios" ou
        # "Scénario de base" restent intacts puisqu'ils s'appliquent ailleurs.
        removed_count = sum(1 for e in self.custom_combos if e.get("scope", "all") == deleted_name)
        if removed_count:
            self.custom_combos = [e for e in self.custom_combos if e.get("scope", "all") != deleted_name]
            backend.save_combos_list(self.custom_combos, self.current_deck_name)
        backend.save_scenarios_list(self.scenarios, self.current_deck_name)
        if self.active_scenario_index == index:
            self.active_scenario_index = None
        elif self.active_scenario_index is not None and self.active_scenario_index > index:
            self.active_scenario_index -= 1
        self.refresh_scenario_editor_list()
        self.refresh_scenario_editor_detail()
        self.refresh_analysis_target_menus()
        self.refresh_combos_ui()

    def select_scenario_for_editing(self, index):
        if index == self.active_scenario_index:
            return
        if not self._confirm_discard_scenario_changes():
            self.refresh_scenario_editor_list()  # resynchronise le sélecteur sur le scénario resté actif
            return
        self.active_scenario_index = index
        self.refresh_scenario_editor_list()
        self.refresh_scenario_editor_detail()

    def refresh_scenario_editor_list(self):
        """
        Sélecteur de scénarios dans l'onglet Combos & Scénarios : un dropdown
        compact (pas une liste toujours affichée à l'écran) + les boutons d'action
        (⧉/🗑) agissent sur le scénario actuellement sélectionné dans ce dropdown.
        Reste vide tant que l'utilisateur n'a rien choisi explicitement (pas
        d'auto-sélection du premier scénario de la liste).
        """
        if not hasattr(self, "scenario_picker_menu"):
            return

        names = [s["name"] for s in self.scenarios]
        self._scenario_picker_display_map = {name: i for i, name in enumerate(names)}
        has_scenarios = bool(names)
        empty_placeholder = self.t("scenario_empty_hint_short")
        none_selected_placeholder = self.t("scenario_picker_none_selected")
        self.scenario_picker_menu.configure(values=names if has_scenarios else [empty_placeholder])

        has_active = self.active_scenario_index is not None and self.active_scenario_index < len(self.scenarios)
        if has_active:
            self.scenario_picker_menu.set(self.scenarios[self.active_scenario_index]["name"])
        else:
            self.scenario_picker_menu.set(none_selected_placeholder if has_scenarios else empty_placeholder)

        is_protected = has_active and self.scenarios[self.active_scenario_index].get("protected", False)
        self.btn_duplicate_scenario.configure(state=("normal" if has_active else "disabled"))
        self.btn_delete_scenario.configure(state=("normal" if (has_active and not is_protected) else "disabled"))

    def _on_scenario_picked(self, selected_value):
        idx = getattr(self, "_scenario_picker_display_map", {}).get(selected_value)
        if idx is not None:
            self.select_scenario_for_editing(idx)

    def _rename_active_scenario(self):
        if self.active_scenario_index is not None:
            self.rename_scenario(self.active_scenario_index)

    def _duplicate_active_scenario(self):
        if self.active_scenario_index is not None:
            self.duplicate_scenario(self.active_scenario_index)

    def _delete_active_scenario(self):
        if self.active_scenario_index is not None:
            self.delete_scenario(self.active_scenario_index)

    def refresh_scenario_editor_detail(self):
        """Zone d'édition du swap Main<->Side pour le scénario sélectionné (ou indice si aucun)."""
        if not hasattr(self, "frame_scenario_editor"):
            return
        if self.active_scenario_index is None or self.active_scenario_index >= len(self.scenarios):
            self.active_scenario_index = None
            self.lbl_scenario_editor_title.configure(text="")
            self.frame_scenario_editor_swap.grid_remove()
            self.frame_scenario_editor_hint.grid()
            self.turn_order_menu.configure(state="disabled")
            return
        scenario = self.scenarios[self.active_scenario_index]
        self.lbl_scenario_editor_title.configure(text=f"✎ {scenario['name']}")
        self.frame_scenario_editor_hint.grid_remove()
        self.frame_scenario_editor_swap.grid()
        self.turn_order_menu.configure(state="normal")
        self._update_scenario_save_button_state()
        self.lbl_scenario_result_caption.configure(text=self.t("scenario_auto_result_placeholder"), text_color="gray55")
        self.lbl_scenario_result_first.configure(text="")
        self.lbl_scenario_result_second.configure(text="")
        self._refresh_turn_order_menu(scenario)
        self._render_scenario_swap_builder(scenario)

    def _refresh_turn_order_menu(self, scenario):
        """Reflète scenario['turn_order'] dans le sélecteur Premier/Second/Indéterminé."""
        display_map = {
            self.t("turn_order_first"): "first",
            self.t("turn_order_second"): "second",
            self.t("turn_order_unknown"): None,
        }
        self._turn_order_display_map = display_map
        current = scenario.get("turn_order")
        reverse = {v: k for k, v in display_map.items()}
        self.turn_order_menu.set(reverse.get(current, self.t("turn_order_unknown")))

    def _on_turn_order_picked(self, selected_value):
        if self.active_scenario_index is None:
            return
        display_map = getattr(self, "_turn_order_display_map", {})
        scenario = self.scenarios[self.active_scenario_index]
        scenario["turn_order"] = display_map.get(selected_value)
        self._mark_scenario_dirty()
        # Ré-affiche le tableau de bord si ce scénario est celui actuellement
        # analysé sur la page Analyse, pour refléter le changement immédiatement.
        if self.analysis_target == self.active_scenario_index:
            self.refresh_analysis_detail()

    def _scenario_remaining_main_qty(self, card_id, main_qty_map, scenario):
        qty = main_qty_map.get(card_id, 0)
        removed = min(scenario["removals"].get(card_id, 0), qty)
        return qty, qty - removed

    def _scenario_remaining_side_qty(self, card_id, side_qty_map, scenario):
        qty = side_qty_map.get(card_id, 0)
        added = min(scenario["additions"].get(card_id, 0), qty)
        return qty, qty - added

    def scenario_adjust_removal(self, card_id, delta):
        if self.active_scenario_index is None:
            return
        scenario = self.scenarios[self.active_scenario_index]
        main_df = self.df[self.df["Section"] == "Main"]
        main_qty_map = dict(zip(main_df["ID"].astype(str), main_df["Quantite"].astype(int)))
        qty, _ = self._scenario_remaining_main_qty(card_id, main_qty_map, scenario)
        current = scenario["removals"].get(card_id, 0)
        scenario["removals"][card_id] = max(0, min(qty, current + delta))
        self._mark_scenario_dirty()
        self._sync_scenario_card_rows(card_id, scenario)

    def scenario_adjust_addition(self, card_id, delta):
        if self.active_scenario_index is None:
            return
        scenario = self.scenarios[self.active_scenario_index]
        side_df = self.df[self.df["Section"] == "Side"]
        main_df = self.df[self.df["Section"] == "Main"]
        side_qty_map = dict(zip(side_df["ID"].astype(str), side_df["Quantite"].astype(int)))
        main_qty_map = dict(zip(main_df["ID"].astype(str), main_df["Quantite"].astype(int)))
        qty_avail = side_qty_map.get(card_id, 0)
        _, remaining_main = self._scenario_remaining_main_qty(card_id, main_qty_map, scenario)
        max_add = max(0, min(qty_avail, backend.MAX_COPIES_PER_CARD - remaining_main))
        current = scenario["additions"].get(card_id, 0)
        scenario["additions"][card_id] = max(0, min(max_add, current + delta))
        self._mark_scenario_dirty()
        self._sync_scenario_card_rows(card_id, scenario)

    def _sync_scenario_card_rows(self, card_id, scenario):
        """
        Met à jour UNIQUEMENT les 1 ou 2 lignes concernées par ce card_id
        précis (sa ligne "native" dans sa colonne d'origine, et sa ligne
        "déplacée" dans l'autre colonne si des exemplaires ont été échangés)
        — jamais les autres lignes, jamais de destruction/reconstruction des
        listes entières. C'est ça qui rend le clic +/- réellement instantané,
        contrairement à un rendu complet à chaque fois (même différé).
        """
        main_df = self.df[self.df["Section"] == "Main"] if not self.df.empty else self.df
        side_df = self.df[self.df["Section"] == "Side"] if not self.df.empty else self.df
        main_qty_map = dict(zip(main_df["ID"].astype(str), main_df["Quantite"].astype(int)))
        side_qty_map = dict(zip(side_df["ID"].astype(str), side_df["Quantite"].astype(int)))

        if card_id in main_qty_map:
            match = main_df[main_df["ID"].astype(str) == card_id]
            name = match.iloc[0]["Nom"] if not match.empty else card_id
            qty_orig, remaining = self._scenario_remaining_main_qty(card_id, main_qty_map, scenario)
            removed = min(scenario["removals"].get(card_id, 0), qty_orig)
            self._upsert_scenario_row("main", card_id, name, remaining, "→", "#d9480f",
                                       lambda c=card_id: self.scenario_adjust_removal(c, 1), moved=False)
            self._upsert_scenario_row("side", card_id, name, removed, "→", "#f5b700",
                                       lambda c=card_id: self.scenario_adjust_removal(c, -1), moved=True)
        elif card_id in side_qty_map:
            match = side_df[side_df["ID"].astype(str) == card_id]
            name = match.iloc[0]["Nom"] if not match.empty else card_id
            added = min(scenario["additions"].get(card_id, 0), side_qty_map.get(card_id, 0))
            _, remaining_side = self._scenario_remaining_side_qty(card_id, side_qty_map, scenario)
            self._upsert_scenario_row("main", card_id, name, added, "←", "#1f6aa5",
                                       lambda c=card_id: self.scenario_adjust_addition(c, -1), moved=True)
            self._upsert_scenario_row("side", card_id, name, remaining_side, "←", "#2b8a3e",
                                       lambda c=card_id: self.scenario_adjust_addition(c, 1), moved=False)

        self._update_scenario_size_label(scenario)

    def _upsert_scenario_row(self, column, card_id, name, qty, button_symbol, button_color, command, moved):
        """
        Crée, met à jour, ou détruit UNE SEULE ligne (frame) selon l'état
        actuel — jamais les autres. Si la ligne existe déjà et qu'il ne s'agit
        que d'un changement de quantité, seul le texte du label est modifié
        (pas de destruction/recréation du frame ni du bouton).
        """
        key = (column, card_id)
        existing = self._scenario_row_widgets.get(key)
        if qty <= 0:
            if existing is not None:
                try:
                    existing["frame"].destroy()
                except Exception:
                    pass
                del self._scenario_row_widgets[key]
            return

        prefix = "↩ " if moved else ""
        text = f"{prefix}{name}  (x{qty})"
        if existing is not None:
            existing["label"].configure(text=text)
            return

        parent = self.frame_scenario_main if column == "main" else self.frame_scenario_side
        frame = ctk.CTkFrame(parent)
        frame.pack(fill="x", pady=2, padx=5)
        lbl = ctk.CTkLabel(frame, text=text, anchor="w", font=("Arial", 16))
        if moved:
            lbl.configure(text_color="#f5b700")
        lbl.pack(side="left", padx=5, fill="x", expand=True)
        ctk.CTkButton(
            frame, text=button_symbol, width=30, fg_color=button_color, command=command, font=("Arial", 16)
        ).pack(side="right", padx=2)
        self._scenario_row_widgets[key] = {"frame": frame, "label": lbl}

    def _update_scenario_size_label(self, scenario):
        main_df = self.df[self.df["Section"] == "Main"] if not self.df.empty else self.df
        side_df = self.df[self.df["Section"] == "Side"] if not self.df.empty else self.df
        main_qty_map = dict(zip(main_df["ID"].astype(str), main_df["Quantite"].astype(int)))
        side_qty_map = dict(zip(side_df["ID"].astype(str), side_df["Quantite"].astype(int)))

        total_remaining = 0
        for cid in main_qty_map:
            _, remaining = self._scenario_remaining_main_qty(cid, main_qty_map, scenario)
            total_remaining += remaining
        total_added = 0
        for cid, qty in side_qty_map.items():
            total_added += min(scenario["additions"].get(cid, 0), qty)

        target_size = int(main_df["Quantite"].sum()) if not main_df.empty else 0
        new_size = total_remaining + total_added
        valid = new_size == target_size
        color = "#2b8a3e" if valid else "#d9480f"
        self.lbl_scenario_size.configure(
            text=self.t("scenario_size_format").format(actual=new_size, target=target_size), text_color=color
        )

    def _mark_scenario_dirty(self):
        """
        Signale une modification du scénario en cours d'édition SANS la
        sauvegarder sur disque — remplace l'ancienne sauvegarde automatique
        différée (400ms à chaque clic sur une flèche Main<->Side), qui
        ralentissait la construction de deck même si la sauvegarde en elle-même
        était déjà légère. L'utilisateur doit désormais cliquer explicitement
        sur "Sauvegarder" (voir _save_active_scenario) ; s'il tente de changer
        d'onglet, de scénario ou de deck avant ça, une confirmation lui est
        demandée (voir _confirm_discard_scenario_changes).
        """
        self._scenario_unsaved_changes = True
        self._update_scenario_save_button_state()
        # Si un résultat était déjà affiché (d'une sauvegarde précédente), le
        # signale comme périmé sans effacer les chiffres — ils restent une
        # référence utile pendant que l'utilisateur continue d'ajuster,
        # jusqu'à la prochaine sauvegarde qui les recalculera.
        if hasattr(self, "lbl_scenario_result_first") and (
            self.lbl_scenario_result_first.cget("text") or self.lbl_scenario_result_second.cget("text")
        ):
            self.lbl_scenario_result_caption.configure(text=self.t("scenario_auto_result_stale"), text_color="#d9822b")

    def _update_scenario_save_button_state(self):
        if not hasattr(self, "btn_scenario_save"):
            return
        if self._scenario_unsaved_changes:
            self.btn_scenario_save.configure(text=f"💾 {self.t('scenario_save_pending')}", fg_color="#d9822b", hover_color="#b8690f")
        else:
            self.btn_scenario_save.configure(text=f"💾 {self.t('scenario_save')}", fg_color="#2b8a3e", hover_color="#1e602b")

    def _save_active_scenario(self):
        backend.save_scenarios_list(self.scenarios, self.current_deck_name)
        self._scenario_unsaved_changes = False
        self._update_scenario_save_button_state()
        self._run_scenario_auto_analysis()

    def _run_scenario_auto_analysis(self):
        """
        Calcule automatiquement Aller en Premier/Second pour le scénario tout
        juste sauvegardé — déclenché UNIQUEMENT par un clic explicite sur
        "Sauvegarder" (jamais par la sauvegarde silencieuse du garde-fou en
        quittant l'éditeur, qui mettrait à jour un panneau que l'utilisateur
        ne regarde déjà plus à ce moment-là). Thread dédié, comme les autres
        calculs annexes de cette page : ne rafraîchit que ce petit panneau,
        pas toute la page.
        """
        if self.active_scenario_index is None:
            return
        scenario_snapshot = copy.deepcopy(self.scenarios[self.active_scenario_index])
        df_snapshot = self.df.copy()
        combos_snapshot = list(self.custom_combos)
        conditions_snapshot = copy.deepcopy(self.custom_conditions)
        use_combos = bool(self.check_extend_combos.get())
        use_pioche = bool(self.check_extend_pioche.get())

        self.lbl_scenario_result_caption.configure(text=self.t("scenario_auto_result_running"), text_color="gray55")
        self.lbl_scenario_result_first.configure(text="")
        self.lbl_scenario_result_second.configure(text="")

        def worker():
            try:
                data = self._compute_scenario_results(
                    scenario_snapshot, df=df_snapshot, combos=combos_snapshot,
                    use_combos=use_combos, use_pioche=use_pioche, conditions=conditions_snapshot,
                )
            except Exception as e:
                backend.get_logger().error("Erreur calcul automatique de scénario : %s", e, exc_info=True)
                data = None
            self.after(0, lambda: self._finish_scenario_auto_analysis(scenario_snapshot["name"], data))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_scenario_auto_analysis(self, scenario_name, data):
        # Si l'utilisateur a changé de scénario pendant le calcul, n'affiche pas
        # un résultat qui ne correspond plus à ce qui est actuellement ouvert.
        if self.active_scenario_index is None or self.scenarios[self.active_scenario_index]["name"] != scenario_name:
            return
        if not data:
            self.lbl_scenario_result_caption.configure(text=self.t("scenario_auto_result_error"), text_color="#d9480f")
            return

        relevant_metric = data.get("relevant_metric")
        show_first = relevant_metric != "second"
        show_second = relevant_metric != "first"
        first = data.get("first") or {}
        second = data.get("second") or 0.0

        self.lbl_scenario_result_caption.configure(text=self.t("scenario_auto_result_caption"), text_color="gray55")
        self.lbl_scenario_result_first.configure(
            text=f"⚔️ {self.t('sec_first_short')} : {first.get(0, 0.0):.1f}%" if show_first else ""
        )
        self.lbl_scenario_result_second.configure(
            text=f"🛡️ {self.t('sec_second_short')} : {second:.1f}%" if show_second else ""
        )

    def _flush_scenario_save(self):
        """Sauvegarde immédiate, sans confirmation — utilisée par le garde-fou
        lui-même (l'utilisateur a déjà répondu "Sauvegarder") et à la fermeture
        de l'app, jamais appelée directement au fil des éditions."""
        backend.save_scenarios_list(self.scenarios, self.current_deck_name)
        self._scenario_unsaved_changes = False
        self._update_scenario_save_button_state()

    def _confirm_discard_scenario_changes(self):
        """
        S'il reste des modifications de scénario non sauvegardées, demande à
        l'utilisateur s'il veut les sauvegarder avant de continuer (changement
        d'onglet, de scénario, ou de deck). Retourne True si on peut continuer
        (rien à sauvegarder, sauvegardé, ou l'utilisateur a choisi d'abandonner
        les changements), False si l'utilisateur a annulé (reste où il était).
        """
        if not getattr(self, "_scenario_unsaved_changes", False):
            return True
        choice = messagebox.askyesnocancel(self.t("scenario_unsaved_title"), self.t("scenario_unsaved_text"))
        if choice is None:  # Annuler : reste sur l'éditeur en cours
            return False
        if choice:  # Oui : sauvegarder puis continuer
            self._flush_scenario_save()
            return True
        # Non : abandonne les changements, recharge depuis le disque
        self.scenarios = backend.load_scenarios_list(self.current_deck_name)
        self._scenario_unsaved_changes = False
        self._update_scenario_save_button_state()
        return True

    def reset_active_scenario(self):
        if self.active_scenario_index is None:
            return
        scenario = self.scenarios[self.active_scenario_index]
        scenario["removals"] = {}
        scenario["additions"] = {}
        self._mark_scenario_dirty()
        self.refresh_scenario_editor_detail()

    def _render_scenario_swap_builder(self, scenario):
        """
        Rendu initial complet du swap builder (à l'ouverture d'un scénario, ou
        après Sauvegarder/Réinitialiser) — utilise les MÊMES fonctions que les
        mises à jour incrémentales au clic (_upsert_scenario_row), pour
        garantir que les deux chemins produisent toujours un résultat
        identique. Les clics +/- individuels, eux, passent directement par
        _sync_scenario_card_rows (voir scenario_adjust_removal/addition) sans
        jamais repasser par ici — c'est ce qui les rend instantanés.
        """
        for widget in self.frame_scenario_main.winfo_children():
            widget.destroy()
        for widget in self.frame_scenario_side.winfo_children():
            widget.destroy()
        self._scenario_row_widgets = {}

        main_df = self.df[self.df["Section"] == "Main"] if not self.df.empty else self.df
        side_df = self.df[self.df["Section"] == "Side"] if not self.df.empty else self.df
        main_qty_map = dict(zip(main_df["ID"].astype(str), main_df["Quantite"].astype(int)))
        side_qty_map = dict(zip(side_df["ID"].astype(str), side_df["Quantite"].astype(int)))

        # -- Colonne Main : cartes du Main encore présentes + cartes ramenées depuis le Side --
        for _, row_data in main_df.iterrows():
            cid = str(row_data["ID"])
            _, remaining = self._scenario_remaining_main_qty(cid, main_qty_map, scenario)
            self._upsert_scenario_row("main", cid, row_data["Nom"], remaining, "→", "#d9480f",
                                       lambda c=cid: self.scenario_adjust_removal(c, 1), moved=False)

        for _, row_data in side_df.iterrows():
            cid = str(row_data["ID"])
            added = min(scenario["additions"].get(cid, 0), int(row_data["Quantite"]))
            self._upsert_scenario_row("main", cid, row_data["Nom"], added, "←", "#1f6aa5",
                                       lambda c=cid: self.scenario_adjust_addition(c, -1), moved=True)

        # -- Colonne Side : cartes du Side encore disponibles + cartes retirées du Main --
        for _, row_data in side_df.iterrows():
            cid = str(row_data["ID"])
            _, remaining_side = self._scenario_remaining_side_qty(cid, side_qty_map, scenario)
            self._upsert_scenario_row("side", cid, row_data["Nom"], remaining_side, "←", "#2b8a3e",
                                       lambda c=cid: self.scenario_adjust_addition(c, 1), moved=False)

        for _, row_data in main_df.iterrows():
            cid = str(row_data["ID"])
            removed = scenario["removals"].get(cid, 0)
            qty_main, _ = self._scenario_remaining_main_qty(cid, main_qty_map, scenario)
            removed = min(removed, qty_main)
            self._upsert_scenario_row("side", cid, row_data["Nom"], removed, "→", "#f5b700",
                                       lambda c=cid: self.scenario_adjust_removal(c, -1), moved=True)

        self._update_scenario_size_label(scenario)

    # ==================================================================
    # TABLEAU DE BORD VISUEL DES RÉSULTATS D'ANALYSE
    # ==================================================================
    def _grade_and_color(self, pct):
        """Convertit un pourcentage en rang façon jeu vidéo (S/A/B/C/D) + couleur associée."""
        if pct >= 90:
            return "S", "#f5b700"
        if pct >= 75:
            return "A", "#2b8a3e"
        if pct >= 60:
            return "B", "#1f6aa5"
        if pct >= 40:
            return "C", "#d9822b"
        return "D", "#d9480f"


    def _clear_results(self, container=None):
        target = container if container is not None else self.result_container
        for widget in target.winfo_children():
            widget.destroy()

    def _render_results_placeholder(self, container=None):
        target = container if container is not None else self.result_container
        self._clear_results(target)
        ctk.CTkLabel(
            target, text="", font=("Arial", 52)
        ).pack(pady=(30, 0))

    # ==================================================================
    # CALCUL EN ARRIÈRE-PLAN (évite de geler l'interface pendant la simulation)
    # ==================================================================
    def _set_analysis_buttons_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for attr in ("btn_run_std", "btn_run_compare", "check_extend_combos", "check_extend_pioche"):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.configure(state=state)
                except Exception:
                    pass
        if enabled:
            # Réapplique la désactivation de "Cartes Pioche" si le deck n'en a pas
            # (sinon on la réactiverait par erreur en sortant du calcul).
            self._update_pioche_extension_availability()

    def _show_computing_placeholder(self):
        self._clear_results()
        wrap = ctk.CTkFrame(self.result_container, fg_color="transparent")
        wrap.pack(fill="both", expand=True, pady=50)
        ctk.CTkLabel(wrap, text=f"🎲 {self.t('computing_label')}", font=("Arial", 20, "bold")).pack(pady=(0, 12))
        bar = ctk.CTkProgressBar(wrap, mode="indeterminate", width=280)
        bar.pack()
        bar.start()
        self._computing_progress_bar = bar

    def _start_computation(self, compute_fn, on_done):
        """
        Exécute `compute_fn` (calcul pur, sans toucher aux widgets) dans un thread
        d'arrière-plan, puis appelle `on_done(résultat)` sur le thread principal une
        fois terminé. Empêche les calculs superposés (double-clic) et désactive les
        boutons "Analyser" le temps du calcul, avec un indicateur visuel.
        """
        if getattr(self, "_simulation_running", False):
            return
        self._simulation_running = True
        self._set_analysis_buttons_enabled(False)
        self._show_computing_placeholder()
        # Force Tkinter à peindre la barre de progression à l'écran MAINTENANT,
        # avant de lancer le calcul en arrière-plan — sans ça, si le calcul se
        # termine très vite (comparaison sur un petit deck par exemple), le
        # rendu de la barre et son remplacement par le résultat final peuvent
        # se retrouver dans le même cycle de rafraîchissement, et la barre
        # n'est jamais réellement visible à l'écran.
        self.update_idletasks()

        def worker():
            try:
                result = compute_fn()
            except Exception as e:
                backend.get_logger().error("Erreur pendant le calcul d'analyse : %s", e)
                result = None
            self.after(0, lambda: self._finish_computation(result, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_computation(self, result, on_done):
        self._simulation_running = False
        self._set_analysis_buttons_enabled(True)
        bar = getattr(self, "_computing_progress_bar", None)
        if bar is not None:
            try:
                bar.stop()
            except Exception:
                pass
            self._computing_progress_bar = None
        on_done(result)

    def _build_probability_card(self, parent, col, label, pct, sublabel=None, emphasized=False, info_keys=None):
        """
        Grosse carte affichant en évidence UNE probabilité de victoire (celle
        d'avoir une main jouable face à 0 interruption pour "Aller en Premier",
        ou celle de percer le terrain adverse pour "Aller en Second") — c'est le
        chiffre le plus important de toute l'analyse, donc affiché en très
        grand, tout en haut des résultats.
        `info_keys` est un tuple optionnel (clé_titre, clé_texte) pour une bulle
        d'info expliquant comment cette métrique est calculée.
        """
        grade, color = self._grade_and_color(pct)
        card = ctk.CTkFrame(
            parent, corner_radius=16, fg_color=("gray90", "gray17"),
            border_width=2 if emphasized else 0, border_color="#f5b700"
        )
        card.grid(row=0, column=col, sticky="nsew", padx=6, pady=4)

        if emphasized:
            ctk.CTkLabel(
                card, text=self.t("relevant_metric_badge"), font=("Arial", 15, "bold"), text_color="#f5b700"
            ).pack(pady=(10, 0))

        label_row = ctk.CTkFrame(card, fg_color="transparent")
        label_row.pack(pady=(14 if not emphasized else 2, 2))
        ctk.CTkLabel(label_row, text=label, font=("Arial", 18, "bold")).pack(side="left")
        if info_keys:
            self._make_info_icon(label_row, info_keys[0], info_keys[1]).pack(side="left", padx=(4, 0))

        ctk.CTkLabel(card, text=f"{pct:.1f}%", font=("Arial", 53, "bold"), text_color=color).pack()
        if sublabel:
            ctk.CTkLabel(card, text=sublabel, font=("Arial", 14), text_color="gray60").pack(pady=(0, 4))

        bar = ctk.CTkProgressBar(card, progress_color=color, height=10, corner_radius=5)
        bar.set(max(0.0, min(1.0, pct / 100)))
        bar.pack(fill="x", padx=24, pady=(4, 8))

        badge = ctk.CTkLabel(
            card, text=f"  RANG {grade}  ", font=("Arial", 17, "bold"),
            text_color="white", fg_color=color, corner_radius=8
        )
        badge.pack(pady=(0, 14))

    def _build_section_title(self, parent, text, info_keys, font):
        """Titre de section avec bulle d'info accolée (ⓘ expliquant le calcul)."""
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(anchor="w", padx=10, pady=(8, 4))
        ctk.CTkLabel(row, text=text, font=font).pack(side="left")
        if info_keys:
            self._make_info_icon(row, info_keys[0], info_keys[1]).pack(side="left", padx=(4, 0))

    def _build_bar_row(self, parent, label, pct, color=None, bold=False, sub_label=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=4)
        ctk.CTkLabel(
            row, text=label, width=180, anchor="w", font=("Arial", 14, "bold" if bold else "normal")
        ).pack(side="left")
        bar = ctk.CTkProgressBar(row, progress_color=color or self._grade_and_color(pct)[1], height=14, corner_radius=7)
        bar.set(max(0.0, min(1.0, pct / 100)))
        bar.pack(side="left", fill="x", expand=True, padx=8)
        ctk.CTkLabel(row, text=f"{pct:.1f}%", width=55, anchor="e", font=("Arial", 17, "bold")).pack(side="left")
        if sub_label:
            ctk.CTkLabel(row, text=sub_label, width=95, anchor="e", font=("Arial", 14), text_color="gray55").pack(side="left", padx=(4, 0))

    def _build_layered_bar_row(self, parent, label, pct_main, pct_layer):
        """
        Barre à deux segments côte à côte (PAS imbriqués) : pct_main est la
        probabilité d'avoir EXACTEMENT 1 carte de ce rôle en main, pct_layer
        celle d'en avoir EXACTEMENT 2 — deux tranches mutuellement exclusives
        (jamais les deux en même temps), donc placées l'une à la suite de
        l'autre plutôt que superposées, et dont la somme ne dépasse jamais
        100% (contrairement à "au moins 1" et "au moins 2", qui se chevauchent
        et peuvent largement dépasser 100% additionnés ensemble).

        Le second segment (exactement 2) est TOUJOURS en vert foncé fixe,
        peu importe la couleur du premier segment — le vert signale "en plus"
        de façon cohérente avec le "+X%" déjà en vert à côté (même logique
        que les puces "+X% avec pioche" ailleurs dans l'app), plutôt que de
        varier selon le dégradé de performance du premier segment.

        Texte affiché À CÔTÉ de la barre (pas incrusté dedans) : le
        pourcentage exact-1 en priorité, suivi du complément exact-2 au
        format "+X%".
        """
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=3, padx=4)
        ctk.CTkLabel(row, text=label, width=180, anchor="w", font=("Arial", 16)).pack(side="left")

        bar_track = ctk.CTkFrame(row, height=14, corner_radius=7, fg_color=("gray80", "gray30"))
        bar_track.pack(side="left", fill="x", expand=True, padx=8)
        bar_track.pack_propagate(False)

        color_main = self._grade_and_color(pct_main)[1]
        segment_1 = ctk.CTkFrame(bar_track, corner_radius=7, fg_color=color_main)
        segment_1.place(relx=0, rely=0, relwidth=max(0.0, min(1.0, pct_main / 100)), relheight=1)

        if pct_layer > 0:
            segment_2 = ctk.CTkFrame(bar_track, corner_radius=7, fg_color="#1e6b3a")
            start = max(0.0, min(1.0, pct_main / 100))
            width = max(0.0, min(1.0 - start, pct_layer / 100))
            segment_2.place(relx=start, rely=0, relwidth=width, relheight=1)

        ctk.CTkLabel(row, text=f"{pct_main:.1f}%", width=55, anchor="e", font=("Arial", 17, "bold")).pack(side="left")
        if pct_layer > 0:
            ctk.CTkLabel(
                row, text=f"+{pct_layer:.1f}%", width=65, anchor="e", font=("Arial", 15, "bold"), text_color="#2b8a3e"
            ).pack(side="left", padx=(2, 0))

    def _build_stat_chip(self, parent, row, col, icon, label, value):
        chip = ctk.CTkFrame(parent, corner_radius=10, fg_color=("gray88", "gray20"))
        chip.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
        ctk.CTkLabel(chip, text=icon, font=("Arial", 26)).pack(side="left", padx=(10, 4), pady=8)
        inner = ctk.CTkFrame(chip, fg_color="transparent")
        inner.pack(side="left", padx=(0, 10), pady=6)
        ctk.CTkLabel(inner, text=label, font=("Arial", 14), text_color="gray60", anchor="w").pack(anchor="w")
        ctk.CTkLabel(inner, text=f"{value:.2f}", font=("Arial", 20, "bold"), anchor="w").pack(anchor="w")

    def _section_badge(self, parent, text, valid):
        color = "#2b8a3e" if valid else "#d9480f"
        return ctk.CTkLabel(
            parent, text=f"  {text}  ", font=("Arial", 17, "bold"),
            text_color="white", fg_color=color, corner_radius=8
        )

    def _render_results_dashboard(self, title, subtitle, size_badges, stats, hyper_by_cat, first, second,
                                   warning_text=None, info_text=None, deck_cards=None, relevant_metric=None,
                                   custom_first=None, custom_second=None,
                                   custom_combined_first=None, custom_combined_second=None,
                                   base_first=None, base_second=None,
                                   base_custom_first=None, base_custom_second=None,
                                   base_custom_combined_first=None, base_custom_combined_second=None,
                                   combos_active=False, pioche_active=False,
                                   dead_hand_rate=None, second_boardbreaker_dist=None, role_overlap=None,
                                   concentration=None, category_breakdown=None):
        """
        Construit le tableau de bord visuel des résultats (analyse simple, plein
        format), dans la vue unique de résultats — pas d'onglets séparés :
        Combos et Cartes Pioche sont des extensions composables du MÊME calcul
        (voir run_current_target_analysis / _compute_standard_results), donc un
        seul jeu de résultats à afficher, jamais deux vues à naviguer.

        `base_*` : résultats SANS aucune extension, fournis en plus de `first`/
        `second`/`custom_first`/`custom_second` (qui reflètent les extensions
        actuellement actives) pour permettre l'affichage d'une comparaison
        (valeur de base barrée + valeur finale) quand au moins une extension
        est active — voir `_build_analysis_section`.

        `dead_hand_rate`/`second_boardbreaker_dist`/`role_overlap`/`concentration` :
        nouveaux axes d'analyse (taux de main morte, distribution des
        boardbreakers en Second, chevauchement de rôles, concentration des
        pièces) — voir calculs.analyze_deck_extras/analyze_role_overlap/
        analyze_concentration.
        """
        self._last_render_data = {
            "title": title, "subtitle": subtitle, "first": first, "second": second,
            "hyper_by_cat": hyper_by_cat, "stats": stats,
        }
        data = {
            "title": title, "subtitle": subtitle, "size_badges": size_badges, "stats": stats,
            "hyper_by_cat": hyper_by_cat, "first": first, "second": second,
            "warning_text": warning_text, "info_text": info_text, "deck_cards": deck_cards,
            "relevant_metric": relevant_metric,
            "custom_first": custom_first, "custom_second": custom_second,
            "custom_combined_first": custom_combined_first, "custom_combined_second": custom_combined_second,
            "base_first": base_first, "base_second": base_second,
            "base_custom_first": base_custom_first, "base_custom_second": base_custom_second,
            "base_custom_combined_first": base_custom_combined_first, "base_custom_combined_second": base_custom_combined_second,
            "combos_active": combos_active, "pioche_active": pioche_active,
            "dead_hand_rate": dead_hand_rate, "second_boardbreaker_dist": second_boardbreaker_dist,
            "role_overlap": role_overlap, "concentration": concentration,
            "category_breakdown": category_breakdown,
        }
        self._clear_results()
        self._build_analysis_section(self.result_container, data, slot="single", compact=False)

    def _build_analysis_section(self, parent, data, slot, compact=False):
        """
        Construit une section d'analyse complète (indicateurs principaux, tirage de main, résistance
        aux interruptions, moyennes en main, probabilités par rôle) dans `parent`.
        Utilisée à la fois par l'analyse simple (compact=False, pleine largeur) et
        par la comparaison côte à côte (compact=True, deux colonnes plus étroites)
        — pour que les deux modes offrent le même niveau de détail.
        `slot` identifie la zone "Tirer une main" propre à cette section (nécessaire
        en mode comparaison où deux tirages indépendants coexistent à l'écran).
        """
        title = data["title"]
        subtitle = data.get("subtitle")
        size_badges = data["size_badges"]
        stats = data["stats"]
        hyper_by_cat = data["hyper_by_cat"]
        first = data["first"]
        second = data["second"]
        warning_text = data.get("warning_text")
        info_text = data.get("info_text")
        relevant_metric = data.get("relevant_metric")
        deck_cards = data.get("deck_cards") or []
        custom_first = data.get("custom_first") or {}
        custom_second = data.get("custom_second") or {}
        custom_combined_first = data.get("custom_combined_first")
        custom_combined_second = data.get("custom_combined_second")
        dead_hand_rate = data.get("dead_hand_rate")
        second_boardbreaker_dist = data.get("second_boardbreaker_dist")
        role_overlap = data.get("role_overlap")
        concentration = data.get("concentration")
        category_breakdown = data.get("category_breakdown")

        self._hand_draw_slots[slot] = {"frame": None, "deck_cards": deck_cards, "relevant_metric": relevant_metric}

        wrap_len = 280 if compact else 680
        title_font = ("Arial", 17 if compact else 18, "bold")
        section_font = ("Arial", 14 if compact else 13, "bold")

        # relevant_metric fixe le contexte de tour pour CE scénario : "first" = on
        # sait qu'on va en premier (les stats "Aller en Second" n'ont alors aucun
        # sens et ne sont pas affichées), "second" = inverse. None (Deck Actuel,
        # ou un scénario dont le tour n'a pas été précisé) = on ne sait pas encore
        # qui commence, donc les deux métriques restent affichées.
        show_first = relevant_metric != "second"
        show_second = relevant_metric != "first"

        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(header, text=title, font=title_font, wraplength=wrap_len, justify="left").pack(side="left", anchor="w")
        if not compact:
            ctk.CTkButton(header, text=self.t("export_image"), width=150, fg_color="gray40",
                         command=self._export_dashboard_image, font=("Arial", 16)).pack(side="right")
        if subtitle:
            ctk.CTkLabel(parent, text=subtitle, font=("Arial", 15), text_color="gray60",
                        wraplength=wrap_len, justify="left").pack(anchor="w")

        badges_row = ctk.CTkFrame(parent, fg_color="transparent")
        badges_row.pack(fill="x", pady=(4, 10))
        for text, valid in size_badges:
            self._section_badge(badges_row, text, valid).pack(side="left", padx=(0, 6), pady=2)

        if info_text:
            info = ctk.CTkFrame(parent, corner_radius=10, fg_color=("gray85", "#12324a"))
            info.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(info, text=f"ℹ️  {info_text}", font=("Arial", 12 if compact else 11),
                        text_color=("black", "#7fc2ff"), wraplength=wrap_len, justify="left").pack(padx=10, pady=6, anchor="w")

        if warning_text:
            warn = ctk.CTkFrame(parent, corner_radius=10, fg_color="#4a2a12")
            warn.pack(fill="x", pady=(0, 10))
            ctk.CTkLabel(warn, text=f"⚠️  {warning_text}", font=("Arial", 12 if compact else 11, "bold"),
                        text_color="#f5b700", wraplength=wrap_len, justify="left").pack(padx=10, pady=6, anchor="w")

        # -- Indicateurs principaux : Aller en Premier (probabilité de main
        # jouable face à 0 interruption) et/ou Aller en Second (probabilité de
        # percer le terrain adverse), selon ce qui est pertinent pour ce
        # scénario (voir show_first/show_second) --
        metrics_row = ctk.CTkFrame(parent, fg_color="transparent")
        metrics_row.pack(fill="x", pady=(0, 10))
        visible_cols = int(show_first) + int(show_second)
        for i in range(visible_cols):
            metrics_row.grid_columnconfigure(i, weight=1)
        first_label = self.t("sec_first_short") if compact else f"⚔️  {self.t('sec_first')}".replace("=", "").strip()
        second_label = self.t("sec_second_short") if compact else f"🛡️  {self.t('sec_second')}".replace("=", "").strip()
        col = 0
        if show_first:
            self._build_probability_card(
                metrics_row, col, first_label, first.get(0, 0.0),
                None if compact else self.t("vs_disrupt").format(k=0), emphasized=(relevant_metric == "first"),
                info_keys=("metric_first_info_title", "metric_first_info_text")
            )
            col += 1
        if show_second:
            self._build_probability_card(
                metrics_row, col, second_label, second,
                None if compact else self.t("win_rate"), emphasized=(relevant_metric == "second"),
                info_keys=("metric_second_info_title", "metric_second_info_text")
            )

        # -- Tirer une main réelle (illustration concrète, en plus des stats agrégées) --
        hand_section = ctk.CTkFrame(parent, corner_radius=12, fg_color=("gray92", "gray15"))
        hand_section.pack(fill="x", pady=(0, 10))
        hand_header = ctk.CTkFrame(hand_section, fg_color="transparent")
        hand_header.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(hand_header, text=f"🎲 {self.t('draw_hand_title')}", font=section_font).pack(side="left")
        draw_command = self._draw_random_hand_pioche if data.get("pioche_active") else self._draw_random_hand
        ctk.CTkButton(hand_header, text=self.t("draw_hand_button"), width=100,
                     command=lambda s=slot: draw_command(s), font=("Arial", 16)).pack(side="right")
        frame_hand_draw = ctk.CTkFrame(hand_section, fg_color="transparent")
        frame_hand_draw.pack(fill="x", padx=10, pady=(0, 10))
        self._hand_draw_slots[slot]["frame"] = frame_hand_draw

        # -- Résistance aux interruptions supplémentaires : uniquement pertinent
        # pour "Aller en Premier" (c'est la métrique qu'elle détaille), donc masquée
        # quand ce scénario est fixé sur "Aller en Second" --
        if show_first:
            section = ctk.CTkFrame(parent, corner_radius=12, fg_color=("gray92", "gray15"))
            section.pack(fill="x", pady=(0, 10))
            self._build_section_title(section, f"🎯 {self.t('resistance_title')}",
                                       ("resistance_info_title", "resistance_info_text"), section_font)
            for k, v in first.items():
                self._build_bar_row(section, self.t("vs_disrupt").format(k=k), v)
            if dead_hand_rate is not None:
                ctk.CTkFrame(section, height=6, fg_color="transparent").pack()
                # Couleur INVERSÉE (100 - taux) : un pourcentage élevé de main morte
                # est une MAUVAISE nouvelle, contrairement aux barres au-dessus.
                dead_color = self._grade_and_color(100 - dead_hand_rate)[1]
                self._build_bar_row(section, f"💀 {self.t('dead_hand_label')}", dead_hand_rate, color=dead_color, bold=True)
            ctk.CTkFrame(section, height=4, fg_color="transparent").pack()

        # -- Profondeur en Second, symétrique à la résistance aux interruptions
        # ci-dessus : combien de boardbreakers en main de 6 cartes (0, 1, 2+) —
        # masquée quand ce scénario est fixé sur "Aller en Premier" --
        if show_second and second_boardbreaker_dist:
            section_second_depth = ctk.CTkFrame(parent, corner_radius=12, fg_color=("gray92", "gray15"))
            section_second_depth.pack(fill="x", pady=(0, 10))
            self._build_section_title(section_second_depth, f"🛡️ {self.t('second_depth_title')}",
                                       ("second_depth_info_title", "second_depth_info_text"), section_font)
            for key, row_label_key, invert in ((0, "bb_count_0", True), (1, "bb_count_1", False), ("2+", "bb_count_2plus", False)):
                v = second_boardbreaker_dist.get(key, 0.0)
                color = self._grade_and_color(100 - v)[1] if invert else None
                self._build_bar_row(section_second_depth, self.t(row_label_key), v, color=color)
            ctk.CTkFrame(section_second_depth, height=4, fg_color="transparent").pack()

        # -- Moyennes en main (chips par rôle) --
        section2 = ctk.CTkFrame(parent, corner_radius=12, fg_color=("gray92", "gray15"))
        section2.pack(fill="x", pady=(0, 10))
        self._build_section_title(section2, f"✋ {self.t('sec_means')}".replace("=", "").strip(),
                                   ("means_info_title", "means_info_text"), section_font)
        chips_grid = ctk.CTkFrame(section2, fg_color="transparent")
        chips_grid.pack(fill="x", padx=6, pady=(0, 8))
        n_cols = 2 if compact else 3
        for i in range(n_cols):
            chips_grid.grid_columnconfigure(i, weight=1)
        stat_keys = [("st", "Starter"), ("ex", "Extender"), ("ht", "Handtrap"),
                     ("ah", "Anti_Handtrap"), ("bb", "Boardbreaker"), ("br", "Brick")]
        for idx, (key, cat) in enumerate(stat_keys):
            self._build_stat_chip(chips_grid, idx // n_cols, idx % n_cols, CATEGORY_ICONS[cat], self.t(cat), stats[key])

        # -- Répartition du deck par catégorie : combien de cartes de chaque
        # rôle contient RÉELLEMENT le deck, et quelle part ça représente —
        # une donnée de composition pure (pas une probabilité de tirage,
        # contrairement à la section suivante), donc toujours la même quel
        # que soit le nombre de cartes en main. Couleur NEUTRE fixe plutôt que
        # la coloration automatique par performance (_grade_and_color) : cette
        # dernière juge tout ce qui est sous 40% comme "mauvais" (rouge), ce
        # qui n'a aucun sens ici — un deck 100% Handtrap n'existe pas, ces
        # pourcentages ne sont jamais censés approcher 100%.
        if category_breakdown:
            section_breakdown = ctk.CTkFrame(parent, corner_radius=12, fg_color=("gray92", "gray15"))
            section_breakdown.pack(fill="x", pady=(0, 10))
            self._build_section_title(section_breakdown, f"🥧 {self.t('category_breakdown_title')}",
                                       ("category_breakdown_info_title", "category_breakdown_info_text"), section_font)
            for cat, count, pct in category_breakdown:
                self._build_bar_row(
                    section_breakdown, f"{CATEGORY_ICONS.get(cat, '')} {self.t(cat)}", pct, color="#1f6aa5",
                    sub_label=self.t("category_breakdown_count_label").format(count=count)
                )
            ctk.CTkFrame(section_breakdown, height=4, fg_color="transparent").pack()

        # -- Probabilité d'avoir au moins 1 carte de chaque rôle en main, avec la
        # redondance (≥2) en petit à droite : une carte n'est pas juste "présente
        # ou non", savoir si elle a un backup change la lecture du risque --
        section3 = ctk.CTkFrame(parent, corner_radius=12, fg_color=("gray92", "gray15"))
        section3.pack(fill="x", pady=(0, 10))
        self._build_section_title(section3, f"📊 {self.t('sec_hyper')}".replace("=", "").strip(),
                                   ("hyper_info_title", "hyper_info_text"), section_font)
        row_legend = ctk.CTkFrame(section3, fg_color="transparent")
        row_legend.pack(anchor="w", padx=8, pady=(0, 6))
        ctk.CTkFrame(row_legend, width=12, height=12, corner_radius=3, fg_color="#1e6b3a").pack(side="left", pady=1)
        ctk.CTkLabel(
            row_legend, text=f" {self.t('hyper_legend_note')}", font=("Arial", 14), text_color="gray55"
        ).pack(side="left")
        for cat, p1, p2 in hyper_by_cat:
            self._build_layered_bar_row(
                section3, f"{CATEGORY_ICONS.get(cat, '')} {self.t(cat)}", p1, p2
            )
        if role_overlap and role_overlap.get("multi_role_count", 0) > 0:
            ctk.CTkLabel(
                section3, text=f"🔀 {self.t('role_overlap_note').format(count=role_overlap['multi_role_count'], pct=round(role_overlap['multi_role_pct'], 1))}",
                font=("Arial", 14), text_color="gray55", wraplength=wrap_len - 20, justify="left"
            ).pack(anchor="w", padx=8, pady=(4, 0))
        ctk.CTkFrame(section3, height=4, fg_color="transparent").pack()

        # -- Résultats personnalisés ("Paramètres") : conditions de victoire définies
        # par l'utilisateur, uniquement celles pertinentes pour ce qui est affiché
        # (Aller en Premier et/ou Aller en Second, selon show_first/show_second). Le
        # résultat COMBINÉ (selon le connecteur OU/ET choisi pour ce côté) est mis
        # en avant en gras, au-dessus du détail de chaque condition individuelle.
        has_custom_first = show_first and bool(custom_first)
        has_custom_second = show_second and bool(custom_second)
        if has_custom_first or has_custom_second:
            section4 = ctk.CTkFrame(parent, corner_radius=12, fg_color=("gray92", "gray15"))
            section4.pack(fill="x", pady=(0, 10))
            self._build_section_title(section4, f"🧩 {self.t('custom_conditions_title')}",
                                       ("custom_conditions_info_title", "custom_conditions_info_text"), section_font)

            both_sides = has_custom_first and has_custom_second

            def _render_custom_side(results, combined, side_key):
                side_tag = ""
                if both_sides:
                    side_tag = f" — {self.t('turn_order_first') if side_key == 'first' else self.t('turn_order_second')}"
                if combined is not None:
                    self._build_bar_row(
                        section4, f"🎯 {self.t('custom_combined_label')}{side_tag}", combined, bold=True
                    )
                for name, pct in results.items():
                    self._build_bar_row(section4, f"{name}{side_tag}", pct)

            if has_custom_first:
                _render_custom_side(custom_first, custom_combined_first, "first")
            if has_custom_second:
                _render_custom_side(custom_second, custom_combined_second, "second")

            ctk.CTkFrame(section4, height=4, fg_color="transparent").pack()

        # -- Concentration : quelles pièces individuelles (carte Starter ou combo)
        # représentent, à elles seules, une grande part des mains jouables — un
        # deck dépendant d'une seule pièce est plus fragile face à une handtrap
        # ciblée, même à taux de main jouable égal. Coloration INVERSÉE (100 -
        # contribution) : une contribution élevée est un signal négatif, pas
        # positif, contrairement aux barres des autres sections. Seules les 3
        # pièces les plus critiques sont affichées d'emblée ; le reste se
        # déploie via un bouton à flèche, pour ne pas noyer l'écran sur un deck
        # avec beaucoup de cartes Starter différentes.
        if concentration and concentration.get("pieces"):
            section5 = ctk.CTkFrame(parent, corner_radius=12, fg_color=("gray92", "gray15"))
            section5.pack(fill="x", pady=(0, 10))
            self._build_section_title(section5, f"⚠️ {self.t('concentration_title')}",
                                       ("concentration_info_title", "concentration_info_text"), section_font)

            def _piece_row(container, piece):
                icon = "🧩" if piece["type"] == "combo" else "🃏"
                color = self._grade_and_color(100 - piece["presence_pct_of_playable"])[1]
                self._build_bar_row(container, f"{icon} {piece['nom']}", piece["presence_pct_of_playable"], color=color)

            all_pieces = concentration["pieces"]
            top_pieces, rest_pieces = all_pieces[:3], all_pieces[3:]
            for piece in top_pieces:
                _piece_row(section5, piece)

            if rest_pieces:
                frame_rest = ctk.CTkFrame(section5, fg_color="transparent")
                for piece in rest_pieces:
                    _piece_row(frame_rest, piece)
                # Replié par défaut : frame_rest n'est volontairement PAS empaqueté ici.

                def _toggle_concentration_rest():
                    if frame_rest.winfo_ismapped():
                        frame_rest.pack_forget()
                        btn_toggle.configure(text=f"▼ {self.t('concentration_show_more').format(count=len(rest_pieces))}")
                    else:
                        frame_rest.pack(fill="x", after=btn_toggle)
                        btn_toggle.configure(text=f"▲ {self.t('concentration_show_less')}")

                btn_toggle = ctk.CTkButton(
                    section5, text=f"▼ {self.t('concentration_show_more').format(count=len(rest_pieces))}",
                    fg_color="transparent", text_color="gray60", hover_color=("gray85", "gray25"),
                    anchor="w", height=24, font=("Arial", 14), command=_toggle_concentration_rest
                )
                btn_toggle.pack(fill="x", padx=8, pady=(4, 0))

            ctk.CTkFrame(section5, height=4, fg_color="transparent").pack()

    def _draw_random_hand(self, slot="single"):
        """
        Tire une main. Par défaut (Deck Actuel ou scénario "Aller en Second") :
        5 cartes de main de départ + 1 carte distincte représentant la pioche de
        la Draw Phase (le joueur qui va en second peut piocher dès son premier
        tour). Si le scénario est spécifiquement fixé sur "Aller en Premier",
        cette pioche n'existe pas (le premier joueur ne pioche pas à son T1) :
        seules 5 cartes sont alors tirées. `slot` identifie quelle zone de tirage
        mettre à jour (plusieurs coexistent en mode comparaison).
        """
        state = self._hand_draw_slots.get(slot)
        if not state or state["frame"] is None:
            return
        frame = state["frame"]

        for w in frame.winfo_children():
            w.destroy()

        deck_cards = state["deck_cards"]
        draws_extra_card = state.get("relevant_metric") != "first"
        needed = 6 if draws_extra_card else 5

        if not deck_cards or len(deck_cards) < needed:
            ctk.CTkLabel(
                frame, text=self.t("draw_hand_too_small"),
                font=("Arial", 15), text_color="#d9822b", wraplength=280, justify="left"
            ).pack(anchor="w", pady=6)
            return

        hand = random.sample(deck_cards, needed)
        if draws_extra_card:
            self._render_hand_images(frame, hand[:5], hand[5])
        else:
            self._render_hand_images(frame, hand, None)

    def _draw_random_hand_pioche(self, slot="single"):
        """
        Comme `_draw_random_hand`, mais pour le sous-onglet "Avec Pioche" :
        affiche, à la droite de CHAQUE carte "Pioche" (main de départ ou carte
        de la Draw Phase), les cartes qu'elle a précisément fait piocher (voir
        calculs.resolve_pioche_hand_grouped — un seul niveau, pas de chaînage,
        limité aux cartes Pioche déjà présentes dans la main de départ).
        """
        state = self._hand_draw_slots.get(slot)
        if not state or state["frame"] is None:
            return
        frame = state["frame"]

        for w in frame.winfo_children():
            w.destroy()

        deck_cards = state["deck_cards"]
        draws_extra_card = state.get("relevant_metric") != "first"
        needed = 6 if draws_extra_card else 5

        if not deck_cards or len(deck_cards) < needed:
            ctk.CTkLabel(
                frame, text=self.t("draw_hand_too_small"),
                font=("Arial", 15), text_color="#d9822b", wraplength=280, justify="left"
            ).pack(anchor="w", pady=6)
            return

        hand = random.sample(deck_cards, needed)
        opening_hand = hand[:5] if draws_extra_card else hand
        draw_phase_card = hand[5] if draws_extra_card else None

        full_hand = opening_hand + ([draw_phase_card] if draw_phase_card is not None else [])
        groups = calculs.resolve_pioche_hand_grouped(deck_cards, full_hand)
        drawn_by_uid = {pioche_card["uid"]: drawn_cards for pioche_card, drawn_cards in groups}

        ctk.CTkLabel(frame, text=self.t("opening_hand_label"),
                    font=("Arial", 15, "bold"), text_color="gray60").pack(anchor="w")
        row_opening = ctk.CTkFrame(frame, fg_color="transparent")
        row_opening.pack(fill="x", anchor="w")
        # La ligne "Main de départ" ne contient TOUJOURS que les 5 cartes
        # réellement tirées — jamais les cartes piochées en plus, pour ne pas
        # contredire visuellement le "(5 cartes)" du libellé ci-dessus. Chaque
        # carte Pioche de la main de départ obtient sa PROPRE sous-section,
        # juste en dessous, avec les cartes qu'elle a précisément fait piocher.
        for card in opening_hand:
            self._build_hand_card_cell(row_opening, card)
        for card in opening_hand:
            if card["uid"] in drawn_by_uid:
                self._render_pioche_drawn_section(frame, card, drawn_by_uid[card["uid"]])

        if draw_phase_card is not None:
            ctk.CTkLabel(frame, text=self.t("draw_phase_label"),
                        font=("Arial", 15, "bold"), text_color="#f5b700").pack(anchor="w", pady=(8, 0))
            row_draw = ctk.CTkFrame(frame, fg_color="transparent")
            row_draw.pack(fill="x", anchor="w")
            self._build_hand_card_cell(row_draw, draw_phase_card, highlight=True)
            # Ici, en revanche, la ligne ne contient qu'UNE seule carte (la carte
            # de la Draw Phase elle-même) : y ajouter ses cartes piochées en
            # ligne, à sa droite, ne casse aucune promesse de comptage.
            if draw_phase_card["uid"] in drawn_by_uid:
                self._render_pioche_drawn_inline(row_draw, drawn_by_uid[draw_phase_card["uid"]])

    def _render_pioche_drawn_section(self, parent, pioche_card, drawn_cards):
        """Sous-section dédiée pour une carte Pioche de la main de départ : son
        nom en légende, puis les cartes qu'elle a précisément fait piocher sur
        leur propre ligne (jamais mélangées avec la main de départ elle-même)."""
        ctk.CTkLabel(
            parent, text=f"➜ {pioche_card.get('nom', '?')} {self.t('pioche_drew_label')}",
            font=("Arial", 15, "bold"), text_color="#1f9e5a", wraplength=280, justify="left"
        ).pack(anchor="w", pady=(6, 2))
        if not drawn_cards:
            ctk.CTkLabel(
                parent, text=self.t("pioche_drew_nothing"),
                font=("Arial", 13), text_color="gray55"
            ).pack(anchor="w", padx=4)
            return
        row_drawn = ctk.CTkFrame(parent, fg_color="transparent")
        row_drawn.pack(fill="x", anchor="w")
        for card in drawn_cards:
            self._build_hand_card_cell(row_drawn, card)

    def _render_pioche_drawn_inline(self, row, drawn_cards):
        """Affiche, à la suite (à droite) d'une carte Pioche dans SA ligne, une
        petite flèche puis les cartes qu'elle a précisément fait piocher."""
        if not drawn_cards:
            ctk.CTkLabel(
                row, text=f"➜ {self.t('pioche_drew_nothing')}",
                font=("Arial", 13), text_color="gray55", wraplength=140, justify="left"
            ).pack(side="left", padx=(6, 8))
            return
        ctk.CTkLabel(row, text="➜", font=("Arial", 25, "bold"), text_color="#1f9e5a").pack(side="left", padx=(4, 2))
        for card in drawn_cards:
            self._build_hand_card_cell(row, card)

    def _build_hand_card_cell(self, parent, card, highlight=False):
        cid = backend.sanitize_id(card.get("id"))
        img_path = os.path.join(backend.IMAGES_DIR, f"{cid}.jpg")
        cell = ctk.CTkFrame(
            parent, fg_color=("gray85", "gray20"), width=76,
            border_width=2 if highlight else 0, border_color="#f5b700", corner_radius=6
        )
        cell.pack(side="left", padx=4, pady=2)

        if os.path.exists(img_path):
            try:
                ctk_img = self.get_cached_thumb(img_path)
                ctk.CTkLabel(cell, image=ctk_img, text="", font=("Arial", 16)).pack(padx=3, pady=(3, 0))
            except Exception:
                ctk.CTkLabel(cell, text="🂠", font=("Arial", 38)).pack(padx=3, pady=(3, 0))
        else:
            ctk.CTkLabel(cell, text="🂠", font=("Arial", 38)).pack(padx=3, pady=(3, 0))

        # Le nom est TOUJOURS affiché (même sans image encore téléchargée) :
        # sans ça, une carte sans image se résume à un dos de carte anonyme,
        # ce qui donne l'impression que le tirage ne fonctionne pas.
        ctk.CTkLabel(
            cell, text=str(card.get("nom", "?")), font=("Arial", 14, "bold"),
            wraplength=70, justify="center"
        ).pack(padx=2)

        roles = "".join(CATEGORY_ICONS[c] for c in CATEGORY_ICONS if card.get(c) == 1)
        if roles:
            ctk.CTkLabel(cell, text=roles, font=("Arial", 16)).pack(pady=(0, 3))
        else:
            ctk.CTkFrame(cell, height=4, fg_color="transparent").pack()

    def _render_hand_images(self, frame, opening_hand, draw_phase_card):
        for w in frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(frame, text=self.t("opening_hand_label"),
                    font=("Arial", 15, "bold"), text_color="gray60").pack(anchor="w")
        row_opening = ctk.CTkFrame(frame, fg_color="transparent")
        row_opening.pack(fill="x", anchor="w")
        for card in opening_hand:
            self._build_hand_card_cell(row_opening, card)

        if draw_phase_card is not None:
            ctk.CTkLabel(frame, text=self.t("draw_phase_label"),
                        font=("Arial", 15, "bold"), text_color="#f5b700").pack(anchor="w", pady=(8, 0))
            row_draw = ctk.CTkFrame(frame, fg_color="transparent")
            row_draw.pack(fill="x", anchor="w")
            self._build_hand_card_cell(row_draw, draw_phase_card, highlight=True)


    def _export_dashboard_image(self):
        """
        Exporte les résultats affichés en image PNG (rendu autonome via PIL, sans
        dépendre d'une capture d'écran système, donc portable Windows/Mac/Linux).
        """
        data = getattr(self, "_last_render_data", None)
        if not data:
            return

        width, height = 900, 760
        img = Image.new("RGB", (width, height), "#1a1a1a")
        draw = ImageDraw.Draw(img)

        def _font(size, bold=False):
            names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
            for name in names:
                try:
                    return ImageFont.truetype(name, size)
                except Exception:
                    continue
            return ImageFont.load_default()

        font_title = _font(26, bold=True)
        font_big = _font(42, bold=True)
        font_normal = _font(15, bold=True)
        font_small = _font(13)

        y = 28
        draw.text((30, y), re.sub(r'[^\x00-\x7F\u00C0-\u017F]+', '', data["title"]).strip() or data["title"], font=font_title, fill="white")
        y += 38
        if data.get("subtitle"):
            draw.text((30, y), data["subtitle"], font=font_small, fill="#999999")
            y += 28
        y += 10

        card_w = (width - 90) // 2
        first0 = data["first"].get(0, 0.0)
        second = data["second"]
        for i, (label, pct) in enumerate([
            (self.t("sec_first_short"), first0),
            (self.t("sec_second_short"), second),
        ]):
            x = 30 + i * (card_w + 30)
            _, color = self._grade_and_color(pct)
            draw.rounded_rectangle([x, y, x + card_w, y + 150], radius=16, fill="#2b2b2b")
            draw.text((x + 20, y + 16), label, font=font_normal, fill="white")
            draw.text((x + 20, y + 48), f"{pct:.1f}%", font=font_big, fill=color)
        y += 175

        draw.text((30, y), self.t("resistance_title"), font=font_normal, fill="white")
        y += 30
        for k, v in data["first"].items():
            _, color = self._grade_and_color(v)
            draw.text((30, y), self.t("vs_disrupt").format(k=k), font=font_small, fill="white")
            bar_x = 260
            draw.rectangle([bar_x, y + 2, bar_x + 400, y + 16], fill="#3a3a3a")
            draw.rectangle([bar_x, y + 2, bar_x + int(4 * max(0, min(100, v))), y + 16], fill=color)
            draw.text((bar_x + 410, y), f"{v:.1f}%", font=font_small, fill="white")
            y += 26
        y += 10

        draw.text((30, y), self.t("sec_hyper").replace("=", "").strip(), font=font_normal, fill="white")
        y += 30
        for cat, p1, p2 in data["hyper_by_cat"]:
            _, color = self._grade_and_color(p1)
            draw.text((30, y), self.t(cat), font=font_small, fill="white")
            bar_x = 260
            draw.rectangle([bar_x, y + 2, bar_x + 400, y + 16], fill="#3a3a3a")
            draw.rectangle([bar_x, y + 2, bar_x + int(4 * max(0, min(100, p1))), y + 16], fill=color)
            draw.text((bar_x + 410, y), f"{p1:.1f}%", font=font_small, fill="white")
            y += 26

        os.makedirs(backend.EXPORTS_DIR, exist_ok=True)
        safe_title = re.sub(r'[^a-zA-Z0-9_-]', '_', data["title"])[:40].strip('_') or "analyse"
        out_path = os.path.join(backend.EXPORTS_DIR, f"{safe_title}.png")
        img.save(out_path)

        folder_path = os.path.abspath(backend.EXPORTS_DIR)
        if os.path.exists(folder_path):
            if os.name == 'nt':
                os.startfile(folder_path)
            else:
                cmd = 'open' if sys.platform == 'darwin' else 'xdg-open'
                subprocess.Popen([cmd, folder_path])

    # ==================================================================

    def _resolve_effective_combos(self, combos_pool, scenario_name=None):
        """
        Résout la liste de paires de combo applicables à partir du pool unifié
        (liste de {"pair": [id1, id2], "scope": "all" | "base" | nom_de_scenario}) :
        - "all"   : s'applique toujours (Deck Actuel ET tout scénario)
        - "base"  : s'applique UNIQUEMENT au Deck Actuel (scenario_name=None)
        - un nom de scénario précis : ne s'applique qu'à CE scénario-là
        """
        result = []
        for entry in combos_pool:
            scope = entry.get("scope", "all")
            if scope == "all":
                result.append(entry["pair"])
            elif scope == "base" and scenario_name is None:
                result.append(entry["pair"])
            elif scenario_name is not None and scope == scenario_name:
                result.append(entry["pair"])
        return result

    def _compute_scenario_results(self, scenario, df=None, combos=None, use_combos=True, use_pioche=False, conditions=None):
        """
        Calcule les résultats d'un scénario donné, sans rien afficher (réutilisé par
        l'affichage seul et la comparaison). `df`/`combos` permettent de passer un
        instantané figé (copie) plutôt que de relire self.df/self.custom_combos en
        direct : utile quand ce calcul tourne dans un thread d'arrière-plan pendant
        que l'utilisateur continue à modifier le deck sur le thread principal.

        Combos et Cartes Pioche sont des extensions composables (voir
        _compute_standard_results pour le détail du modèle base/finale — même
        logique ici, appliquée au deck virtuel après swap du scénario).

        `relevant_metric` (déduit de scenario["turn_order"]) détermine si Aller en
        Premier et/ou Aller en Second sont pertinents pour ce scénario — voir
        `_build_analysis_section` pour le filtrage d'affichage correspondant.
        """
        source_df = self.df if df is None else df
        combos_pool = self.custom_combos if combos is None else combos
        conditions_pool = self.custom_conditions if conditions is None else conditions

        main_df = source_df[source_df["Section"] == "Main"].copy()
        side_df = source_df[source_df["Section"] == "Side"].copy()
        target_size = int(main_df["Quantite"].sum()) if not main_df.empty else 0

        for cid, qty in scenario["removals"].items():
            if qty <= 0:
                continue
            mask = main_df["ID"].astype(str) == str(cid)
            if mask.any():
                main_df.loc[mask, "Quantite"] = (main_df.loc[mask, "Quantite"].astype(int) - int(qty)).clip(lower=0)
        main_df = main_df[main_df["Quantite"] > 0]

        added_rows = []
        for cid, qty in scenario["additions"].items():
            if qty <= 0:
                continue
            mask = side_df["ID"].astype(str) == str(cid)
            if mask.any():
                r = side_df[mask].iloc[0].copy()
                r["Quantite"] = int(qty)
                added_rows.append(r)

        frames = [main_df] + ([pd.DataFrame(added_rows)] if added_rows else [])
        virtual_df = pd.concat(frames, ignore_index=True)
        deck = calculs.deck_df_to_list(virtual_df)

        if len(deck) < 5:
            return None

        use_pioche = use_pioche and any(c.get("Pioche") == 1 for c in deck)

        conditions_first = conditions_pool.get("first", {}).get("conditions", [])
        conditions_second = conditions_pool.get("second", {}).get("conditions", [])

        base_stats, base_first, base_second, base_cf, base_cs, base_ccf, base_ccs = (
            calculs.run_simulation_with_conditions(
                deck, use_combos=False, combos_rules=None,
                conditions_first=conditions_first, conditions_second=conditions_second,
            )
        )
        taille = len(deck)
        valid_size = taille == target_size

        hyper_by_cat = []
        category_breakdown = []
        for cat in ["Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick"]:
            count = sum(c[cat] for c in deck)
            prob = calculs.calcul_hypergeometrique(taille, count, 5)
            hyper_by_cat.append((cat, prob["p_exact_1"], prob["p_exact_2"]))
            category_breakdown.append((cat, count, (count / taille * 100) if taille else 0.0))

        effective_combos = self._resolve_effective_combos(combos_pool, scenario["name"]) if use_combos else []

        if use_pioche:
            pioche_raw = calculs.run_pioche_simulation(
                deck, combos_rules=(effective_combos if use_combos else None), use_combos=use_combos,
                conditions_first=conditions_first, conditions_second=conditions_second,
            )
            final_first, final_second = pioche_raw["first"], pioche_raw["second"]
            final_cf, final_cs = pioche_raw["custom_first"], pioche_raw["custom_second"]
            final_ccf, final_ccs = pioche_raw["custom_combined_first"], pioche_raw["custom_combined_second"]
        elif use_combos:
            _, final_first, final_second, final_cf, final_cs, final_ccf, final_ccs = (
                calculs.run_simulation_with_conditions(
                    deck, combos_rules=effective_combos, use_combos=True,
                    conditions_first=conditions_first, conditions_second=conditions_second,
                )
            )
        else:
            final_first, final_second = base_first, base_second
            final_cf, final_cs, final_ccf, final_ccs = base_cf, base_cs, base_ccf, base_ccs

        warning_text = None if valid_size else self.t("scenario_size_mismatch").format(actual=taille, target=target_size)
        info_text = self.t("no_combos_defined_notice") if (use_combos and not effective_combos) else None
        if use_combos and use_pioche:
            mode_str = self.t("mode_combos_and_pioche")
        elif use_pioche:
            mode_str = self.t("mode_pioche")
        elif use_combos:
            mode_str = self.t("mode_custom").format(count=len(effective_combos))
        else:
            mode_str = self.t("mode_std")
        subtitle = f"{self.t('postside_result_title').replace('=', '').strip()} — {mode_str}"

        size_badges = [(self.t("scenario_size_format").format(actual=taille, target=target_size), valid_size)]

        extras = calculs.analyze_deck_extras(deck)
        role_overlap = calculs.analyze_role_overlap(deck)
        concentration = calculs.analyze_concentration(deck, combos_rules=effective_combos)

        return {
            "title": f"🔄 {scenario['name']}",
            "subtitle": subtitle,
            "size_badges": size_badges,
            "stats": base_stats, "hyper_by_cat": hyper_by_cat,
            "warning_text": warning_text, "info_text": info_text, "deck_cards": deck,
            "relevant_metric": scenario.get("turn_order"),
            "first": final_first, "second": final_second,
            "custom_first": final_cf, "custom_second": final_cs,
            "custom_combined_first": final_ccf, "custom_combined_second": final_ccs,
            "base_first": base_first, "base_second": base_second,
            "base_custom_first": base_cf, "base_custom_second": base_cs,
            "base_custom_combined_first": base_ccf, "base_custom_combined_second": base_ccs,
            "combos_active": use_combos, "pioche_active": use_pioche,
            "dead_hand_rate": extras["dead_hand_rate"],
            "second_boardbreaker_dist": extras["second_boardbreaker_dist"],
            "concentration": concentration,
            "role_overlap": role_overlap,
            "category_breakdown": category_breakdown,
        }

    def run_scenario_analysis(self, use_combos=True, use_pioche=False):
        if self.analysis_target is None or self.analysis_target >= len(self.scenarios):
            return
        # Instantanés figés (copies) : le calcul tournera en arrière-plan pendant que
        # l'utilisateur peut continuer à modifier le deck/scénario sur l'autre page.
        scenario_snapshot = copy.deepcopy(self.scenarios[self.analysis_target])
        df_snapshot = self.df.copy()
        combos_snapshot = list(self.custom_combos)
        conditions_snapshot = copy.deepcopy(self.custom_conditions)

        self._start_computation(
            lambda: self._compute_scenario_results(
                scenario_snapshot, df=df_snapshot, combos=combos_snapshot,
                use_combos=use_combos, use_pioche=use_pioche, conditions=conditions_snapshot
            ),
            lambda data: self._render_results_dashboard(**data) if data else self._render_results_placeholder()
        )

    # --- ANALYSE STANDARD (deck sauvegardé, Game 1, sans aucun swap) ---
    def _compute_standard_results(self, use_combos=False, use_pioche=False, df=None, deck_name=None, combos=None, conditions=None):
        """
        Calcule les résultats de l'analyse standard (Game 1, deck sauvegardé), sans
        rien afficher. Combos et Cartes Pioche sont des extensions COMPOSABLES
        (cochées indépendamment l'une de l'autre — voir run_current_target_analysis),
        pas des modes exclusifs : le résultat "de base" (sans aucune extension)
        est TOUJOURS calculé, en plus du résultat "final" (avec les extensions
        actuellement actives) — le premier sert de référence de comparaison à
        afficher à côté du second.

        Les indicateurs principaux (Aller en Premier/Second) / la résistance aux interruptions ET les Résultats
        personnalisés changent bien avec les extensions actives — mais PAS de
        la même façon : les combos ne changent QUE le taux de victoire (une
        paire de cartes peut se substituer à un starter), jamais les Résultats
        personnalisés (qui comptent des catégories, indépendamment des combos).
        Seule la Pioche change les Résultats personnalisés, en changeant les
        cartes réellement en main. Les moyennes en main et les probabilités par
        rôle (hypergéométrique), elles, ne dépendent QUE de la composition du
        deck — jamais des extensions — donc n'ont pas de version "finale"
        distincte, contrairement à ce qu'affichait l'ancien sous-onglet Pioche.
        """
        source_df = self.df if df is None else df
        source_deck_name = self.current_deck_name if deck_name is None else deck_name
        combos_pool = self.custom_combos if combos is None else combos
        conditions_pool = self.custom_conditions if conditions is None else conditions

        deck_path = backend.get_deck_path(source_deck_name)
        deck = calculs.charger_deck(deck_path)
        if not deck:
            return None

        # Garde-fou en plus de la case grisée côté UI : une extension Pioche
        # n'a de sens que si le deck contient réellement des cartes Pioche.
        use_pioche = use_pioche and any(c.get("Pioche") == 1 for c in deck)

        conditions_first = conditions_pool.get("first", {}).get("conditions", [])
        conditions_second = conditions_pool.get("second", {}).get("conditions", [])

        base_stats, base_first, base_second, base_cf, base_cs, base_ccf, base_ccs = (
            calculs.run_simulation_with_conditions(
                deck, chemin_csv=deck_path, use_combos=False, combos_rules=None,
                conditions_first=conditions_first, conditions_second=conditions_second,
            )
        )
        taille = len(deck)
        hyper_by_cat = []
        category_breakdown = []
        for cat in ["Starter", "Extender", "Handtrap", "Anti_Handtrap", "Boardbreaker", "Brick"]:
            count = sum(c[cat] for c in deck)
            prob = calculs.calcul_hypergeometrique(taille, count, 5)
            hyper_by_cat.append((cat, prob["p_exact_1"], prob["p_exact_2"]))
            category_breakdown.append((cat, count, (count / taille * 100) if taille else 0.0))

        effective_combos = self._resolve_effective_combos(combos_pool, scenario_name=None) if use_combos else []

        if use_pioche:
            pioche_raw = calculs.run_pioche_simulation(
                deck, chemin_csv=deck_path, use_combos=use_combos,
                combos_rules=(effective_combos if use_combos else None),
                conditions_first=conditions_first, conditions_second=conditions_second,
            )
            final_first, final_second = pioche_raw["first"], pioche_raw["second"]
            final_cf, final_cs = pioche_raw["custom_first"], pioche_raw["custom_second"]
            final_ccf, final_ccs = pioche_raw["custom_combined_first"], pioche_raw["custom_combined_second"]
        elif use_combos:
            _, final_first, final_second, final_cf, final_cs, final_ccf, final_ccs = (
                calculs.run_simulation_with_conditions(
                    deck, chemin_csv=deck_path, use_combos=True, combos_rules=effective_combos,
                    conditions_first=conditions_first, conditions_second=conditions_second,
                )
            )
        else:
            final_first, final_second = base_first, base_second
            final_cf, final_cs, final_ccf, final_ccs = base_cf, base_cs, base_ccf, base_ccs

        sizes = backend.validate_deck_size(source_df)
        size_badges = [
            (f"{self.section_label(s)} : {sizes[s]['count']}/{sizes[s]['min']}-{sizes[s]['max']}", sizes[s]["valid"])
            for s in EDIT_SECTIONS
        ]
        invalid_sections = [self.section_label(s) for s in EDIT_SECTIONS if not sizes[s]["valid"]]
        warning_text = (self.t("postside_invalid_size") + " (" + ", ".join(invalid_sections) + ")") if invalid_sections else None
        info_text = self.t("no_combos_defined_notice") if (use_combos and not effective_combos) else None

        if use_combos and use_pioche:
            subtitle = self.t("mode_combos_and_pioche")
        elif use_pioche:
            subtitle = self.t("mode_pioche")
        elif use_combos:
            subtitle = self.t("mode_custom").format(count=len(effective_combos))
        else:
            subtitle = self.t("mode_std")

        # Nouveaux axes d'analyse (taux de main morte, distribution en Second,
        # chevauchement des rôles) : indépendants des extensions Combos/Cartes
        # Pioche, comme les probabilités par rôle et les moyennes — voir
        # calculs.analyze_deck_extras/analyze_role_overlap.
        extras = calculs.analyze_deck_extras(deck)
        role_overlap = calculs.analyze_role_overlap(deck)
        concentration = calculs.analyze_concentration(deck, combos_rules=effective_combos)

        return {
            "title": f"🎴 {source_deck_name}", "subtitle": subtitle, "size_badges": size_badges,
            "stats": base_stats, "hyper_by_cat": hyper_by_cat,
            "warning_text": warning_text, "info_text": info_text, "deck_cards": deck, "relevant_metric": None,
            "first": final_first, "second": final_second,
            "custom_first": final_cf, "custom_second": final_cs,
            "custom_combined_first": final_ccf, "custom_combined_second": final_ccs,
            "base_first": base_first, "base_second": base_second,
            "base_custom_first": base_cf, "base_custom_second": base_cs,
            "base_custom_combined_first": base_ccf, "base_custom_combined_second": base_ccs,
            "combos_active": use_combos, "pioche_active": use_pioche,
            "dead_hand_rate": extras["dead_hand_rate"],
            "second_boardbreaker_dist": extras["second_boardbreaker_dist"],
            "concentration": concentration,
            "role_overlap": role_overlap,
            "category_breakdown": category_breakdown,
        }

    def run_analysis(self, use_combos=False, use_pioche=False):
        df_snapshot = self.df.copy()
        deck_name_snapshot = self.current_deck_name
        combos_snapshot = list(self.custom_combos)
        conditions_snapshot = copy.deepcopy(self.custom_conditions)

        self._start_computation(
            lambda: self._compute_standard_results(
                use_combos=use_combos, use_pioche=use_pioche, df=df_snapshot, deck_name=deck_name_snapshot,
                combos=combos_snapshot, conditions=conditions_snapshot
            ),
            lambda data: self._render_results_dashboard(**data) if data else self._render_results_placeholder()
        )

    # ==================================================================
    # PAGE ANALYSE : QUOI ANALYSER (Deck actuel ou un scénario déjà défini)
    # ==================================================================
    def select_analysis_target(self, selected_display_value):
        self.analysis_target = self._analysis_target_display_map.get(selected_display_value)
        self.refresh_analysis_detail()

    def refresh_analysis_target_menus(self):
        """Rafraîchit le dropdown de sélection (mode normal) ET la checklist (mode comparaison)."""
        if not hasattr(self, "analysis_target_menu"):
            return

        values = [self.t("scenario_standard")] + [s["name"] for s in self.scenarios]
        self._analysis_target_display_map = {self.t("scenario_standard"): None}
        for i, s in enumerate(self.scenarios):
            self._analysis_target_display_map[s["name"]] = i
        self.analysis_target_menu.configure(values=values)

        if self.analysis_target is not None and self.analysis_target >= len(self.scenarios):
            self.analysis_target = None
        key_to_display = {v: k for k, v in self._analysis_target_display_map.items()}
        self.analysis_target_menu.set(key_to_display.get(self.analysis_target, self.t("scenario_standard")))

        # Purge les sélections de comparaison devenues invalides (scénario supprimé)
        self.compare_selection = [k for k in self.compare_selection if k is None or k < len(self.scenarios)]

        self._refresh_compare_checklist()
        self.refresh_analysis_detail()

    def refresh_analysis_detail(self):
        """Titre + résumé au-dessus des résultats, pour la sélection actuelle (Deck Actuel ou un scénario)."""
        if not hasattr(self, "frame_analysis_detail"):
            return
        if self.analysis_target is None:
            self.lbl_detail_title.configure(text=self.t("scenario_standard"))
            self.lbl_scenario_summary.configure(text="")
        else:
            if self.analysis_target >= len(self.scenarios):
                self.analysis_target = None
                self.refresh_analysis_detail()
                return
            scenario = self.scenarios[self.analysis_target]
            self.lbl_detail_title.configure(text=scenario["name"])
            removed = sum(v for v in scenario["removals"].values() if v > 0)
            added = sum(v for v in scenario["additions"].values() if v > 0)
            self.lbl_scenario_summary.configure(text=self.t("scenario_summary").format(removed=removed, added=added))

    # ==================================================================
    # COMPARAISON DE SCÉNARIOS CÔTE À CÔTE
    # ==================================================================
    def toggle_compare_selection(self, key):
        """`key` est None pour le deck standard, ou un index de scénario."""
        if key in self.compare_selection:
            self.compare_selection.remove(key)
        elif len(self.compare_selection) < 2:
            self.compare_selection.append(key)
        # Ne reconstruit PAS toutes les cases à chaque clic (ça les faisait
        # disparaître/réapparaître à chaque fois) — se contente de mettre à
        # jour leur état activé/désactivé, sans jamais les détruire. La
        # reconstruction complète (_refresh_compare_checklist) ne sert plus
        # qu'à la création initiale ou quand la liste des scénarios change.
        self._update_compare_checklist_states()

    def _update_compare_checklist_states(self):
        if not hasattr(self, "_compare_checkbox_widgets"):
            return
        for key, cb in self._compare_checkbox_widgets.items():
            checked = key in self.compare_selection
            cb.configure(state="disabled" if (not checked and len(self.compare_selection) >= 2) else "normal")
        if hasattr(self, "btn_run_compare"):
            self.btn_run_compare.configure(state=("normal" if len(self.compare_selection) == 2 else "disabled"))

    def _refresh_compare_checklist(self):
        if not hasattr(self, "frame_compare_checklist"):
            return
        for w in self.frame_compare_checklist.winfo_children():
            w.destroy()
        self._compare_checkbox_widgets = {}

        items = [(None, self.t("scenario_standard"))] + [(i, s["name"]) for i, s in enumerate(self.scenarios)]
        for key, label in items:
            checked = key in self.compare_selection
            var = ctk.BooleanVar(value=checked)
            cb = ctk.CTkCheckBox(
                self.frame_compare_checklist, text=label, variable=var, onvalue=True, offvalue=False,
                command=lambda k=key: self.toggle_compare_selection(k), font=("Arial", 16)
            )
            if not checked and len(self.compare_selection) >= 2:
                cb.configure(state="disabled")
            cb.pack(anchor="w", pady=2, padx=4)
            self._compare_checkbox_widgets[key] = cb

        if hasattr(self, "btn_run_compare"):
            self.btn_run_compare.configure(state=("normal" if len(self.compare_selection) == 2 else "disabled"))

    def _label_for_compare_key(self, key):
        if key is None:
            return self.t("scenario_standard")
        return self.scenarios[key]["name"]

    def _compute_for_compare_key(self, key, df=None, deck_name=None, combos=None, use_combos=True, use_pioche=False, conditions=None):
        if key is None:
            return self._compute_standard_results(
                use_combos=use_combos, use_pioche=use_pioche, df=df, deck_name=deck_name, combos=combos, conditions=conditions
            )
        scenario_snapshot = copy.deepcopy(self.scenarios[key])
        return self._compute_scenario_results(
            scenario_snapshot, df=df, combos=combos, use_combos=use_combos, use_pioche=use_pioche, conditions=conditions
        )

    def run_comparison(self, use_combos=True, use_pioche=False):
        if len(self.compare_selection) != 2:
            return
        key_a, key_b = self.compare_selection
        df_snapshot = self.df.copy()
        deck_name_snapshot = self.current_deck_name
        combos_snapshot = list(self.custom_combos)
        conditions_snapshot = copy.deepcopy(self.custom_conditions)

        def compute():
            data_a = self._compute_for_compare_key(
                key_a, df=df_snapshot, deck_name=deck_name_snapshot, combos=combos_snapshot,
                use_combos=use_combos, use_pioche=use_pioche, conditions=conditions_snapshot
            )
            data_b = self._compute_for_compare_key(
                key_b, df=df_snapshot, deck_name=deck_name_snapshot, combos=combos_snapshot,
                use_combos=use_combos, use_pioche=use_pioche, conditions=conditions_snapshot
            )
            return data_a, data_b

        def on_done(result):
            data_a, data_b = result if result else (None, None)
            if data_a and data_b:
                self._render_comparison_dashboard(data_a, data_b)
            else:
                self._render_results_placeholder()

        self._start_computation(compute, on_done)

    def _render_comparison_dashboard(self, data_a, data_b):
        """
        Comparaison côte à côte. Utilise `_build_analysis_section` (compact=True)
        pour chaque côté, exactement comme l'analyse simple — même niveau de
        détail (indicateurs principaux, tirage de main, résistance, moyennes, probabilités par
        rôle), juste dans une mise en page plus étroite en 2 colonnes.
        """
        self._clear_results()
        parent = self.result_container
        self._last_render_data = None
        self._last_deck_cards = []

        ctk.CTkLabel(parent, text=self.t("compare_title"), font=("Arial", 25, "bold")).pack(anchor="w", pady=(0, 10))

        cols = ctk.CTkFrame(parent, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        cols.grid_columnconfigure(0, weight=1)
        cols.grid_columnconfigure(1, weight=1)

        for col, (data, slot) in enumerate([(data_a, "compare_a"), (data_b, "compare_b")]):
            side = ctk.CTkFrame(cols, corner_radius=12, fg_color=("gray96", "gray12"))
            side.grid(row=0, column=col, sticky="nsew", padx=6)
            inner = ctk.CTkFrame(side, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=10, pady=10)
            self._build_analysis_section(inner, data, slot=slot, compact=True)


if __name__ == "__main__":
    app = DeckApp()
    app.mainloop()
