"""Runtime translation of user-facing bot messages.

Messages that AstrBot sends back to a chat user (errors, command replies,
status notices) are looked up here instead of being hardcoded. The locale is
resolved per message event, so two users on the same instance can receive
replies in different languages.

Locale resources live in ``astrbot/core/i18n/locales/<locale>.json`` and use
nested objects addressed by dotted keys, mirroring the WebUI locale layout.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from string import Formatter
from typing import Any

DEFAULT_LOCALE = "en-US"
"""Locale used when nothing else can be determined."""

SUPPORTED_LOCALES: tuple[str, ...] = ("en-US", "zh-CN", "ru-RU")
"""Locales shipped with AstrBot, in no particular order."""

AUTO_LOCALE = "auto"
"""Configuration value meaning "detect the locale from the platform"."""

_LOCALES_DIR = Path(__file__).parent / "locales"

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}

# Language subtags mapped onto the locales AstrBot ships. Platforms report
# anything from "ru" to "zh-Hans-CN", so only the primary subtag is matched.
_LANGUAGE_ALIASES: dict[str, str] = {
    "en": "en-US",
    "zh": "zh-CN",
    "ru": "ru-RU",
    # Languages without their own resources, routed to the closest one that
    # a speaker is far more likely to read than the English default.
    "be": "ru-RU",
    "kk": "ru-RU",
    "ky": "ru-RU",
    "uk": "ru-RU",
    "uz": "ru-RU",
    "yue": "zh-CN",
}


def normalize_locale(raw: object) -> str | None:
    """Map an arbitrary language tag onto a supported locale.

    Args:
        raw: A language tag such as ``ru``, ``ru-RU``, ``ru_RU`` or
            ``zh-Hans-CN``. Anything that is not a non-empty string is ignored.

    Returns:
        A locale from :data:`SUPPORTED_LOCALES`, or ``None`` when the tag does
        not map onto any of them.
    """
    if not isinstance(raw, str):
        return None
    tag = raw.strip().replace("_", "-")
    if not tag:
        return None

    for locale in SUPPORTED_LOCALES:
        if tag.lower() == locale.lower():
            return locale

    primary = tag.split("-", 1)[0].lower()
    return _LANGUAGE_ALIASES.get(primary)


def _load_locale(locale: str) -> dict[str, Any]:
    cached = _cache.get(locale)
    if cached is not None:
        return cached

    with _lock:
        cached = _cache.get(locale)
        if cached is not None:
            return cached

        path = _LOCALES_DIR / f"{locale}.json"
        data: dict[str, Any] = {}
        try:
            with path.open(encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except FileNotFoundError:
            data = {}
        except Exception:
            # A broken resource file must never break message delivery; fall
            # back to the other locales instead.
            data = {}

        _cache[locale] = data
        return data


def _lookup(locale: str, key: str) -> str | None:
    node: Any = _load_locale(locale)
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node if isinstance(node, str) else None


class _SafeParams(dict):
    """Formatting mapping that renders unknown placeholders literally."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format(template: str, params: dict[str, Any]) -> str:
    if not params or "{" not in template:
        return template
    try:
        return Formatter().vformat(template, (), _SafeParams(params))
    except Exception:
        # Malformed templates (stray braces in a translation) must not raise.
        return template


def translate(key: str, locale: str | None = None, /, **params: Any) -> str:
    """Translate ``key`` into ``locale``.

    Args:
        key: Dotted resource key, e.g. ``agent.error.request_failed``.
        locale: Target locale. Unsupported or missing values fall back to
            :data:`DEFAULT_LOCALE`.
        **params: Values substituted into ``{placeholder}`` markers.

    Returns:
        The translated string. When the key is missing from every locale the
        key itself is returned, so a gap degrades into something diagnosable
        rather than an exception.
    """
    resolved = normalize_locale(locale) or DEFAULT_LOCALE

    template = _lookup(resolved, key)
    if template is None and resolved != DEFAULT_LOCALE:
        template = _lookup(DEFAULT_LOCALE, key)
    if template is None:
        return key

    return _format(template, params)


# Short alias used at call sites.
t = translate


def has_translation(key: str, locale: str | None = None) -> bool:
    """Report whether ``key`` resolves in ``locale`` or in the default locale."""
    resolved = normalize_locale(locale) or DEFAULT_LOCALE
    if _lookup(resolved, key) is not None:
        return True
    return _lookup(DEFAULT_LOCALE, key) is not None


def clear_cache() -> None:
    """Drop cached locale resources. Intended for tests and hot reloads."""
    with _lock:
        _cache.clear()
