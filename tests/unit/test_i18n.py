"""Tests for the runtime message translation layer."""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrbot.core.i18n import (
    DEFAULT_LOCALE,
    LOCALE_EXTRA_KEY,
    SUPPORTED_LOCALES,
    normalize_locale,
    resolve_locale,
    translate,
)
from astrbot.core.i18n import resolver as resolver_module

LOCALES_DIR = Path("astrbot/core/i18n/locales")


def _flatten(data: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _load(locale: str) -> dict[str, str]:
    with (LOCALES_DIR / f"{locale}.json").open(encoding="utf-8") as f:
        return _flatten(json.load(f))


@pytest.fixture
def event():
    """A message event with no configured locale and no sender language."""
    ev = MagicMock()
    ev.get_extra.return_value = None
    ev.unified_msg_origin = "telegram:FriendMessage:123"
    ev.message_obj.sender.language_code = None
    return ev


@pytest.fixture(autouse=True)
def no_config_manager():
    """Isolate tests from whatever config manager the process registered."""
    previous = resolver_module._config_mgr
    resolver_module.set_config_manager(None)
    yield
    resolver_module.set_config_manager(previous)


class TestNormalizeLocale:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ru", "ru-RU"),
            ("ru-RU", "ru-RU"),
            ("ru_RU", "ru-RU"),
            ("RU", "ru-RU"),
            ("uk", "ru-RU"),
            ("zh", "zh-CN"),
            ("zh-Hans-CN", "zh-CN"),
            ("en", "en-US"),
            ("en-GB", "en-US"),
        ],
    )
    def test_known_tags(self, raw, expected):
        assert normalize_locale(raw) == expected

    @pytest.mark.parametrize("raw", ["de", "", "   ", None, 42, [], "x" * 64])
    def test_unknown_tags(self, raw):
        assert normalize_locale(raw) is None


class TestTranslate:
    def test_translates_into_requested_locale(self):
        assert translate("agent.error.unknown", "ru-RU") == "неизвестная ошибка"

    def test_accepts_a_bare_language_tag(self):
        assert translate("agent.error.unknown", "ru") == translate(
            "agent.error.unknown", "ru-RU"
        )

    def test_falls_back_to_default_locale_for_unknown_language(self):
        assert translate("agent.error.unknown", "de") == translate(
            "agent.error.unknown", DEFAULT_LOCALE
        )

    def test_substitutes_parameters(self):
        assert translate("agent.tool.calling", "en-US", name="search") == (
            "🔨 Calling tool: search"
        )

    def test_unknown_key_returns_the_key(self):
        assert translate("no.such.key", "ru-RU") == "no.such.key"

    def test_missing_parameter_is_left_in_place(self):
        assert "{name}" in translate("agent.tool.calling", "en-US")


class TestResolveLocale:
    def test_defaults_without_any_signal(self, event):
        assert resolve_locale(event) == DEFAULT_LOCALE

    def test_uses_the_sender_language(self, event):
        event.message_obj.sender.language_code = "ru"
        assert resolve_locale(event) == "ru-RU"

    def test_event_override_wins(self, event):
        event.message_obj.sender.language_code = "ru"
        event.get_extra.side_effect = lambda key: (
            "zh-CN" if key == LOCALE_EXTRA_KEY else None
        )
        assert resolve_locale(event) == "zh-CN"

    def test_configured_locale_overrides_the_sender_language(self, event):
        event.message_obj.sender.language_code = "ru"
        config_mgr = MagicMock()
        config_mgr.get_conf.return_value = {
            "platform_settings": {"reply_locale": "en-US"}
        }
        resolver_module.set_config_manager(config_mgr)
        assert resolve_locale(event) == "en-US"

    def test_auto_falls_through_to_the_sender_language(self, event):
        event.message_obj.sender.language_code = "ru"
        config_mgr = MagicMock()
        config_mgr.get_conf.return_value = {
            "platform_settings": {"reply_locale": "auto"}
        }
        resolver_module.set_config_manager(config_mgr)
        assert resolve_locale(event) == "ru-RU"

    def test_no_event_is_safe(self):
        assert resolve_locale(None) == DEFAULT_LOCALE

    def test_broken_event_is_safe(self):
        broken = MagicMock()
        broken.get_extra.side_effect = RuntimeError("boom")
        del broken.message_obj
        assert resolve_locale(broken) == DEFAULT_LOCALE


class TestLocaleResources:
    def test_every_supported_locale_has_a_resource_file(self):
        for locale in SUPPORTED_LOCALES:
            assert (LOCALES_DIR / f"{locale}.json").is_file()

    def test_keys_match_the_default_locale(self):
        reference = _load(DEFAULT_LOCALE)
        for locale in SUPPORTED_LOCALES:
            if locale == DEFAULT_LOCALE:
                continue
            assert set(_load(locale)) == set(reference), locale

    def test_placeholders_match_the_default_locale(self):
        reference = _load(DEFAULT_LOCALE)
        placeholders = re.compile(r"{(\w+)}")
        for locale in SUPPORTED_LOCALES:
            if locale == DEFAULT_LOCALE:
                continue
            for key, value in _load(locale).items():
                assert set(placeholders.findall(value)) == set(
                    placeholders.findall(reference[key])
                ), f"{locale}:{key}"

    def test_no_value_is_empty(self):
        for locale in SUPPORTED_LOCALES:
            for key, value in _load(locale).items():
                assert isinstance(value, str) and value.strip(), f"{locale}:{key}"
