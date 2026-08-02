"""Resolve which locale a reply to a given message event should use."""

from __future__ import annotations

from typing import Any

from .translator import AUTO_LOCALE, DEFAULT_LOCALE, normalize_locale, translate

LOCALE_EXTRA_KEY = "astrbot_reply_locale"
"""Event extra that overrides locale detection for a single event."""

_CONFIG_KEY = "reply_locale"
"""``platform_settings`` entry holding the locale preference."""

_config_mgr: Any = None
"""Config manager registered by the core lifecycle, if it is up yet."""


def set_config_manager(config_mgr: Any) -> None:
    """Register the config manager used to read per-session locale settings."""
    global _config_mgr
    _config_mgr = config_mgr


def _configured_locale(event: Any = None) -> str:
    """Read the configured locale preference, honouring per-session configs."""
    config = None
    if _config_mgr is not None and event is not None:
        try:
            config = _config_mgr.get_conf(event.unified_msg_origin)
        except Exception:
            config = None

    if config is None:
        try:
            from astrbot.core import astrbot_config

            config = astrbot_config
        except Exception:
            return AUTO_LOCALE

    try:
        value = config["platform_settings"].get(_CONFIG_KEY, AUTO_LOCALE)
    except Exception:
        return AUTO_LOCALE

    return value if isinstance(value, str) and value else AUTO_LOCALE


def _sender_language(event: Any) -> str | None:
    """Read the language tag the platform reported for the message sender."""
    try:
        sender = event.message_obj.sender
    except Exception:
        return None
    return getattr(sender, "language_code", None)


def resolve_locale(event: Any) -> str:
    """Determine the locale for replies to ``event``.

    The first of these that yields a supported locale wins:

    1. an explicit per-event override stored via :func:`set_event_locale`;
    2. the configured ``platform_settings.reply_locale``, unless it is ``auto``;
    3. the language tag the platform reported for the sender (for example
       Telegram's ``language_code``);
    4. :data:`~astrbot.core.i18n.translator.DEFAULT_LOCALE`.

    Args:
        event: An ``AstrMessageEvent``-like object, or ``None``.

    Returns:
        A supported locale identifier. Never raises.
    """
    if event is not None:
        try:
            override = event.get_extra(LOCALE_EXTRA_KEY)
        except Exception:
            override = None
        resolved = normalize_locale(override)
        if resolved:
            return resolved

    configured = _configured_locale(event)
    if configured != AUTO_LOCALE:
        resolved = normalize_locale(configured)
        if resolved:
            return resolved

    if event is not None:
        resolved = normalize_locale(_sender_language(event))
        if resolved:
            return resolved

    return DEFAULT_LOCALE


def set_event_locale(event: Any, locale: str | None) -> None:
    """Pin ``event`` to ``locale``, overriding detection."""
    try:
        event.set_extra(LOCALE_EXTRA_KEY, locale)
    except Exception:
        pass


def t_event(event: Any, key: str, /, **params: Any) -> str:
    """Translate ``key`` into the locale resolved for ``event``."""
    return translate(key, resolve_locale(event), **params)
