"""
Package backend : façade publique.

Le module a été scindé en sous-modules par responsabilité (security, db, deck,
ydk) pour rester lisible, mais tout reste accessible via `import backend`
exactement comme avant — aucun autre fichier du projet n'a besoin de changer
ses imports.
"""

from .paths import (
    DECKS_DIR,
    DATABASE_DIR,
    IMAGES_DIR,
    LOGS_DIR,
    EXPORTS_DIR,
    IMPORTS_DIR,
    CACHE_DIR,
)

from .security import (
    DECK_COLUMNS,
    DECK_LIMITS,
    MAX_COPIES_PER_CARD,
    sanitize_filename,
    sanitize_id,
    sanitize_section,
    get_default_section,
)

from .db import (
    DB_PATH,
    RateLimiter,
    init_folders,
    is_db_expired,
    download_ygopro_db,
    load_local_db,
    download_card_image,
    download_images_bulk,
)

from .deck import (
    get_deck_path,
    get_combos_path,
    get_scenarios_path,
    load_deck_df,
    save_deck_df,
    validate_deck_size,
    load_combos_list,
    save_combos_list,
    list_available_decks,
    delete_deck,
    rename_deck,
    migrate_flat_decks_to_subfolders,
    load_scenarios_list,
    save_scenarios_list,
    new_scenario,
    get_conditions_path,
    default_conditions,
    load_conditions,
    save_conditions,
)

from .ydk import (
    export_to_ydk,
    parse_ydk_file,
    parse_ydk_text,
    decode_ydke,
    fetch_deck_from_url,
)

from .logging_setup import get_logger

__all__ = [
    "DECKS_DIR", "DATABASE_DIR", "IMAGES_DIR", "LOGS_DIR", "EXPORTS_DIR", "IMPORTS_DIR", "CACHE_DIR",
    "DECK_COLUMNS", "DECK_LIMITS", "MAX_COPIES_PER_CARD",
    "sanitize_filename", "sanitize_id", "sanitize_section", "get_default_section",
    "DB_PATH", "RateLimiter", "init_folders", "is_db_expired", "download_ygopro_db",
    "load_local_db", "download_card_image", "download_images_bulk",
    "get_deck_path", "get_combos_path", "get_scenarios_path", "load_deck_df", "save_deck_df",
    "validate_deck_size", "load_combos_list", "save_combos_list", "list_available_decks", "delete_deck", "rename_deck",
    "migrate_flat_decks_to_subfolders",
    "load_scenarios_list", "save_scenarios_list", "new_scenario",
    "get_conditions_path", "default_conditions", "load_conditions", "save_conditions",
    "export_to_ydk", "parse_ydk_file", "parse_ydk_text", "decode_ydke", "fetch_deck_from_url",
    "get_logger",
]
