"""Runtime internationalization for messages AstrBot sends to chat users."""

from .resolver import (
    LOCALE_EXTRA_KEY,
    resolve_locale,
    set_config_manager,
    set_event_locale,
    t_event,
)
from .translator import (
    AUTO_LOCALE,
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    clear_cache,
    has_translation,
    normalize_locale,
    t,
    translate,
)

__all__ = [
    "AUTO_LOCALE",
    "DEFAULT_LOCALE",
    "LOCALE_EXTRA_KEY",
    "SUPPORTED_LOCALES",
    "clear_cache",
    "has_translation",
    "normalize_locale",
    "resolve_locale",
    "set_config_manager",
    "set_event_locale",
    "t",
    "t_event",
    "translate",
]
