import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from astrbot.core.platform.register import unregister_platform_adapters_by_module
from tests.fixtures.helpers import (
    NoopAwaitable,
    create_mock_file,
    create_mock_update,
    make_platform_config,
)
from tests.fixtures.mocks.telegram import (
    MockTelegramBuilder,
    MockTelegramNetworkError,
    create_mock_telegram_modules,
)

_TELEGRAM_PLATFORM_ADAPTER = None
_TELEGRAM_PLATFORM_EVENT = None
_TELEGRAM_MODULES: dict[str, object] = {}


def _build_telegram_patched_modules():
    mocks = create_mock_telegram_modules()
    return {
        "telegram": mocks["telegram"],
        "telegram.constants": mocks["telegram"].constants,
        "telegram.error": mocks["telegram"].error,
        "telegram.ext": mocks["telegram.ext"],
        "telegramify_markdown": mocks["telegramify_markdown"],
        "apscheduler": mocks["apscheduler"],
        "apscheduler.schedulers": mocks["apscheduler"].schedulers,
        "apscheduler.schedulers.asyncio": mocks["apscheduler"].schedulers.asyncio,
        "apscheduler.schedulers.background": mocks["apscheduler"].schedulers.background,
    }


def _load_telegram_module(module_name: str):
    module = _TELEGRAM_MODULES.get(module_name)
    if module is not None:
        return module

    with patch.dict(sys.modules, _build_telegram_patched_modules()):
        if module_name == "astrbot.core.platform.sources.telegram.tg_adapter":
            unregister_platform_adapters_by_module(module_name)
        sys.modules.pop(module_name, None)
        module = importlib.import_module(module_name)

    sys.modules[module_name] = module
    _TELEGRAM_MODULES[module_name] = module
    return module


def _load_telegram_adapter():
    global _TELEGRAM_PLATFORM_ADAPTER
    if _TELEGRAM_PLATFORM_ADAPTER is not None:
        return _TELEGRAM_PLATFORM_ADAPTER

    module = _load_telegram_module("astrbot.core.platform.sources.telegram.tg_adapter")
    _TELEGRAM_PLATFORM_ADAPTER = module.TelegramPlatformAdapter
    return _TELEGRAM_PLATFORM_ADAPTER


def _load_telegram_platform_event():
    global _TELEGRAM_PLATFORM_EVENT
    if _TELEGRAM_PLATFORM_EVENT is not None:
        return _TELEGRAM_PLATFORM_EVENT

    module = _load_telegram_module("astrbot.core.platform.sources.telegram.tg_event")
    _TELEGRAM_PLATFORM_EVENT = module.TelegramPlatformEvent
    return _TELEGRAM_PLATFORM_EVENT


def _build_context() -> MagicMock:
    context = MagicMock()
    context.bot.username = "test_bot"
    context.bot.id = 12345678
    return context


def test_telegram_enables_local_mode_for_loopback_bot_api():
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config(
            "telegram",
            telegram_api_base_url="http://127.0.0.1:8081/bot",
            telegram_file_base_url="http://127.0.0.1:8081/file/bot",
        ),
        {},
        asyncio.Queue(),
    )

    assert adapter.local_mode is True


def test_telegram_disables_local_mode_for_remote_bot_api():
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )

    assert adapter.local_mode is False


@pytest.mark.asyncio
async def test_telegram_voice_uses_file_download_to_drive(tmp_path):
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )
    voice = create_mock_file("/var/lib/telegram-bot-api/token/voice/file.oga")
    file = MagicMock()
    file.file_path = "/var/lib/telegram-bot-api/token/voice/file.oga"
    file.download_to_drive = AsyncMock()
    voice.get_file = AsyncMock(return_value=file)
    update = create_mock_update(message_text=None, voice=voice)
    wav_path = tmp_path / "voice.wav"

    with (
        patch(
            "astrbot.core.platform.sources.telegram.tg_adapter.get_astrbot_temp_path",
            return_value=str(tmp_path),
        ),
        patch(
            "astrbot.core.platform.sources.telegram.tg_adapter.MediaResolver.to_path",
            AsyncMock(return_value=str(wav_path)),
        ),
    ):
        result = await adapter.convert_message(update, _build_context())

    voice.get_file.assert_awaited_once()
    file.download_to_drive.assert_awaited_once_with(
        custom_path=str(tmp_path / "file.oga")
    )
    assert result is not None
    assert isinstance(result.message[0], Comp.Record)
    assert result.message[0].file == str(wav_path)


@pytest.mark.asyncio
async def test_telegram_partial_quote_uses_exact_quote_text():
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )
    original_text = "😀 prefix target suffix"
    quoted_text = "target"
    reply_update = create_mock_update(
        message_text=original_text,
        message_id=42,
        user_id=1001,
        username="original_sender",
    )
    quote = MagicMock(text=quoted_text, position=10)
    update = create_mock_update(
        message_text="What does this mean?",
        reply_to_message=reply_update.message,
        quote=quote,
    )

    result = await adapter.convert_message(update, _build_context())

    assert result is not None
    reply = result.message[0]
    assert isinstance(reply, Comp.Reply)
    assert reply.id == "42"
    assert reply.message_str == quoted_text
    assert reply.text == quoted_text
    assert reply.chain is not None
    assert len(reply.chain) == 1
    assert isinstance(reply.chain[0], Comp.Plain)
    assert reply.chain[0].text == quoted_text


@pytest.mark.asyncio
@pytest.mark.parametrize("quote_text", [None, ""])
async def test_telegram_reply_without_quote_text_uses_full_message(quote_text):
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )
    original_text = "Use the complete replied message"
    reply_update = create_mock_update(
        message_text=original_text,
        message_id=43,
        user_id=1002,
        username="original_sender",
    )
    quote = MagicMock(text=quote_text) if quote_text is not None else None
    update = create_mock_update(
        message_text="Follow-up question",
        reply_to_message=reply_update.message,
        quote=quote,
    )

    result = await adapter.convert_message(update, _build_context())

    assert result is not None
    reply = result.message[0]
    assert isinstance(reply, Comp.Reply)
    assert reply.message_str == original_text
    assert reply.text == original_text
    assert reply.chain is not None
    assert len(reply.chain) == 1
    assert isinstance(reply.chain[0], Comp.Plain)
    assert reply.chain[0].text == original_text


@pytest.mark.asyncio
async def test_telegram_document_caption_populates_message_text_and_plain():
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )
    document = create_mock_file("https://api.telegram.org/file/test/report.md")
    document.file_name = "report.md"
    mention = MagicMock(type="mention", offset=0, length=6)
    update = create_mock_update(
        message_text=None,
        document=document,
        caption="@alice 请总结这份文档",
        caption_entities=[mention],
    )

    result = await adapter.convert_message(update, _build_context())

    assert result is not None
    assert result.message_str == "@alice 请总结这份文档"
    assert any(isinstance(component, Comp.File) for component in result.message)
    assert any(
        isinstance(component, Comp.Plain) and component.text == "@alice 请总结这份文档"
        for component in result.message
    )
    assert any(
        isinstance(component, Comp.At) and component.qq == "alice"
        for component in result.message
    )


@pytest.mark.asyncio
async def test_telegram_video_caption_populates_message_text_and_plain():
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )
    video = create_mock_file("https://api.telegram.org/file/test/lesson.mp4")
    video.file_name = "lesson.mp4"
    update = create_mock_update(
        message_text=None,
        video=video,
        caption="这段视频讲了什么",
    )

    result = await adapter.convert_message(update, _build_context())

    assert result is not None
    assert result.message_str == "这段视频讲了什么"
    assert any(isinstance(component, Comp.Video) for component in result.message)
    assert any(
        isinstance(component, Comp.Plain) and component.text == "这段视频讲了什么"
        for component in result.message
    )


@pytest.mark.asyncio
async def test_telegram_voice_message_creates_record_component(tmp_path):
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )
    voice = create_mock_file("https://api.telegram.org/file/test/voice.oga")
    downloaded_file = MagicMock()
    downloaded_file.file_path = "https://api.telegram.org/file/test/voice.oga"
    downloaded_file.download_to_drive = AsyncMock()
    voice.get_file = AsyncMock(return_value=downloaded_file)
    update = create_mock_update(
        message_text=None,
        voice=voice,
    )
    wav_path = tmp_path / "voice.oga.wav"
    convert_message_globals = adapter.convert_message.__func__.__globals__

    with (
        patch.dict(
            convert_message_globals,
            {
                "get_astrbot_temp_path": MagicMock(return_value=str(tmp_path)),
                "download_file": AsyncMock(),
            },
        ),
        patch(
            "astrbot.core.utils.media_utils.ensure_wav",
            AsyncMock(return_value=str(wav_path)),
        ),
    ):
        result = await adapter.convert_message(update, _build_context())

    assert result is not None
    assert len(result.message) == 1
    assert isinstance(result.message[0], Comp.Record)
    assert result.message[0].file == str(wav_path)
    assert result.message[0].path == str(wav_path)
    assert result.message[0].url == str(wav_path)


@pytest.mark.asyncio
async def test_telegram_sends_plain_messages_with_rich_markdown():
    TelegramPlatformEvent = _load_telegram_platform_event()
    client = MagicMock()
    client._post = AsyncMock()

    await TelegramPlatformEvent._send_rich_text_chunks(
        client, "# Heading\n\n**rich** text", {"chat_id": "123456"}
    )

    client._post.assert_awaited_once_with(
        "sendRichMessage",
        {
            "chat_id": "123456",
            "rich_message": {"markdown": "# Heading\n\n**rich** text"},
        },
    )


@pytest.mark.asyncio
async def test_telegram_final_segment_splits_long_rich_messages():
    TelegramPlatformEvent = _load_telegram_platform_event()
    client = MagicMock()
    client._post = AsyncMock()
    event = TelegramPlatformEvent("msg", MagicMock(), MagicMock(), "session", client)

    delta = "A" * (TelegramPlatformEvent.MAX_RICH_MESSAGE_LENGTH + 32)
    await event._send_final_segment(delta, {"chat_id": "123456"})

    assert client._post.await_count == 2
    first_call = client._post.await_args_list[0].args
    second_call = client._post.await_args_list[1].args
    assert first_call[0] == "sendRichMessage"
    assert second_call[0] == "sendRichMessage"
    assert len(first_call[1]["rich_message"]["markdown"]) == (
        TelegramPlatformEvent.MAX_RICH_MESSAGE_LENGTH
    )
    assert len(second_call[1]["rich_message"]["markdown"]) == 32


@pytest.mark.asyncio
async def test_telegram_rich_draft_uses_rich_api():
    TelegramPlatformEvent = _load_telegram_platform_event()
    client = MagicMock()
    client._post = AsyncMock()
    event = TelegramPlatformEvent("msg", MagicMock(), MagicMock(), "session", client)

    await event._send_rich_message_draft("123456", 7, "**partial**")

    client._post.assert_awaited_once_with(
        "sendRichMessageDraft",
        {
            "chat_id": 123456,
            "draft_id": 7,
            "rich_message": {"markdown": "**partial**"},
        },
    )


@pytest.mark.asyncio
async def test_telegram_regular_messages_are_used_when_rich_messages_disabled():
    TelegramPlatformEvent = _load_telegram_platform_event()
    client = MagicMock()
    client.send_message = AsyncMock()
    chain = MagicMock()
    chain.chain = [Comp.Plain("regular text")]

    await TelegramPlatformEvent.send_with_client(
        client, chain, "123456", use_rich_messages=False
    )

    client._post.assert_not_called()
    client.send_message.assert_awaited_once_with(
        text="regular text", parse_mode="MarkdownV2", chat_id="123456"
    )


@pytest.mark.asyncio
async def test_telegram_rich_message_falls_back_when_api_call_fails():
    TelegramPlatformEvent = _load_telegram_platform_event()
    client = MagicMock()
    client._post = AsyncMock(side_effect=RuntimeError("unsupported"))
    client.send_message = AsyncMock()

    await TelegramPlatformEvent._send_rich_text_chunks(
        client, "fallback", {"chat_id": "123456"}
    )

    client.send_message.assert_awaited_once_with(
        text="fallback", parse_mode="MarkdownV2", chat_id="123456"
    )


@pytest.mark.asyncio
async def test_telegram_polling_error_requests_rebuild_after_threshold():
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )
    adapter._loop = asyncio.get_running_loop()

    assert not adapter._polling_recovery_requested.is_set()

    for _ in range(adapter._polling_recovery_threshold):
        adapter._on_polling_error(MockTelegramNetworkError("proxy disconnected"))

    await asyncio.sleep(0)

    assert adapter._polling_recovery_requested.is_set()


@pytest.mark.asyncio
async def test_telegram_run_rebuilds_application_after_repeated_polling_errors():
    TelegramPlatformAdapter = _load_telegram_adapter()
    module_globals = TelegramPlatformAdapter.__init__.__globals__
    app_one = MockTelegramBuilder.create_application()
    app_one.updater.running = True
    app_two = MockTelegramBuilder.create_application()
    app_two.updater.running = True
    created_apps = [app_one, app_two]

    builder = MagicMock()
    builder.token.return_value = builder
    builder.base_url.return_value = builder
    builder.base_file_url.return_value = builder
    builder.build.side_effect = created_apps

    adapter = None

    def start_polling_side_effect(*args, **kwargs):
        nonlocal adapter
        error_callback = kwargs["error_callback"]
        assert adapter is not None

        async def _emit_errors():
            await asyncio.sleep(0)
            for _ in range(adapter._polling_recovery_threshold):
                error_callback(MockTelegramNetworkError("proxy disconnected"))

        asyncio.create_task(_emit_errors())
        return NoopAwaitable()

    app_one.updater.start_polling.side_effect = start_polling_side_effect

    async def second_start_polling(*args, **kwargs):
        assert adapter is not None
        adapter._terminating = True

    app_two.updater.start_polling.side_effect = second_start_polling

    with patch.dict(
        module_globals,
        {
            "ApplicationBuilder": MagicMock(return_value=builder),
            "AsyncIOScheduler": MagicMock(
                return_value=MockTelegramBuilder.create_scheduler()
            ),
        },
    ):
        adapter = TelegramPlatformAdapter(
            make_platform_config("telegram"),
            {},
            asyncio.Queue(),
        )
        await adapter.run()

    assert builder.build.call_count == 2
    app_one.updater.stop.assert_awaited()
    app_one.bot.delete_my_commands.assert_not_awaited()
    app_one.stop.assert_awaited()
    app_one.shutdown.assert_awaited()
    app_two.initialize.assert_awaited()
    app_two.start.assert_awaited()


@pytest.mark.asyncio
async def test_telegram_recreate_application_is_skipped_during_termination():
    TelegramPlatformAdapter = _load_telegram_adapter()
    adapter = TelegramPlatformAdapter(
        make_platform_config("telegram"),
        {},
        asyncio.Queue(),
    )
    adapter._terminating = True
    adapter._polling_recovery_requested.set()

    await adapter._recreate_application()

    assert not adapter._polling_recovery_requested.is_set()


@pytest.mark.asyncio
async def test_telegram_run_rebuilds_fresh_application_after_recreate_init_failure():
    TelegramPlatformAdapter = _load_telegram_adapter()
    module_globals = TelegramPlatformAdapter.__init__.__globals__
    app_one = MockTelegramBuilder.create_application()
    app_one.updater.running = True
    app_two = MockTelegramBuilder.create_application()
    app_three = MockTelegramBuilder.create_application()
    app_three.updater.running = True
    created_apps = [app_one, app_two, app_three]

    builder = MagicMock()
    builder.token.return_value = builder
    builder.base_url.return_value = builder
    builder.base_file_url.return_value = builder
    builder.build.side_effect = created_apps

    adapter = None

    def first_start_polling(*args, **kwargs):
        nonlocal adapter
        error_callback = kwargs["error_callback"]
        assert adapter is not None

        async def _emit_errors():
            await asyncio.sleep(0)
            for _ in range(adapter._polling_recovery_threshold):
                error_callback(MockTelegramNetworkError("proxy disconnected"))

        asyncio.create_task(_emit_errors())
        return NoopAwaitable()

    app_one.updater.start_polling.side_effect = first_start_polling
    app_two.initialize.side_effect = TimeoutError("init timeout")

    async def final_start_polling(*args, **kwargs):
        assert adapter is not None
        adapter._terminating = True

    app_three.updater.start_polling.side_effect = final_start_polling

    with patch.dict(
        module_globals,
        {
            "ApplicationBuilder": MagicMock(return_value=builder),
            "AsyncIOScheduler": MagicMock(
                return_value=MockTelegramBuilder.create_scheduler()
            ),
        },
    ):
        adapter = TelegramPlatformAdapter(
            make_platform_config(
                "telegram",
                telegram_polling_restart_delay=0.1,
            ),
            {},
            asyncio.Queue(),
        )
        await adapter.run()

    assert builder.build.call_count == 3
    app_two.stop.assert_awaited()
    app_two.shutdown.assert_awaited()
    app_three.initialize.assert_awaited()
    app_three.start.assert_awaited()


@pytest.mark.asyncio
async def test_telegram_rich_drafts_do_not_cross_streaming_break_boundaries():
    """Each logical MessageDraft must retain its own Telegram draft_id."""
    TelegramPlatformEvent = _load_telegram_platform_event()
    event = object.__new__(TelegramPlatformEvent)
    event.client = MagicMock()
    event._send_rich_message_draft = AsyncMock()
    event._send_final_segment = AsyncMock()

    async def generator():
        yield MessageChain(chain=[Comp.Plain("first")])
        await asyncio.sleep(0)
        yield MessageChain(chain=[], type="break")
        yield MessageChain(chain=[Comp.Plain("second")])
        await asyncio.sleep(0)

    initial_draft_id = TelegramPlatformEvent._next_draft_id
    await event._send_streaming_draft("123", None, {"chat_id": "123"}, generator())

    draft_calls = event._send_rich_message_draft.await_args_list
    assert [call.args[1] for call in draft_calls] == [
        initial_draft_id + 1,
        initial_draft_id + 2,
    ]
    assert [call.args[2] for call in draft_calls] == ["first", "second"]
    assert [call.args[0] for call in event._send_final_segment.await_args_list] == [
        "first",
        "second",
    ]


@pytest.mark.asyncio
async def test_telegram_rich_tool_status_uses_one_rich_message():
    TelegramPlatformEvent = _load_telegram_platform_event()
    event = object.__new__(TelegramPlatformEvent)
    event.client = MagicMock()
    event.client._post = AsyncMock(return_value={"message_id": 700})
    event.use_rich_messages = True
    event.show_tool_calling_execution = True
    event._tool_call_groups = []
    event._tool_status_message_id = None
    event._last_response_message_id = None
    event._last_response_markdown = ""
    event._last_response_payload = None
    event.get_message_type = MagicMock(return_value=1)
    event.get_sender_id = MagicMock(return_value="1001")

    await event._update_tool_call_status(
        MessageChain(
            type="tool_call",
            chain=[MagicMock(data={"name": "web_search", "args": {"q": "first"}})],
        )
    )
    await event._update_tool_call_status(
        MessageChain(
            type="tool_call",
            chain=[MagicMock(data={"name": "web_search", "args": {"q": "second"}})],
        )
    )

    assert event.client._post.await_count == 2
    assert event.client._post.await_args_list[0].args[0] == "sendRichMessage"
    assert event.client._post.await_args_list[1].args[0] == "sendRichMessage"
    markdown = event.client._post.await_args_list[1].args[1]["rich_message"]["markdown"]
    assert "Tool calls" not in markdown
    assert "<summary>web_search (2)</summary>" in markdown
    assert '- {"q": "first"}' in markdown


@pytest.mark.asyncio
async def test_telegram_regular_tool_status_updates_plain_message():
    TelegramPlatformEvent = _load_telegram_platform_event()
    event = object.__new__(TelegramPlatformEvent)
    event.client = MagicMock()
    event.client.send_message = AsyncMock(return_value=MagicMock(message_id=701))
    event.client.edit_message_text = AsyncMock()
    event.use_rich_messages = False
    event.show_tool_calling_execution = True
    event._tool_call_groups = []
    event._tool_status_message_id = None
    event._last_response_message_id = None
    event._last_response_markdown = ""
    event._last_response_payload = None
    event.get_message_type = MagicMock(return_value=1)
    event.get_sender_id = MagicMock(return_value="1001")

    await event._update_tool_call_status(
        MessageChain(
            type="tool_call", chain=[MagicMock(data={"name": "shell", "args": {}})]
        )
    )
    await event._update_tool_call_status(
        MessageChain(
            type="tool_call", chain=[MagicMock(data={"name": "shell", "args": {}})]
        )
    )

    assert event.client.send_message.await_count == 2
    assert (
        event.client.send_message.await_args.kwargs["text"]
        == "<details><summary>shell (2)</summary>\n\n- {}\n- {}\n\n</details>"
    )


@pytest.mark.asyncio
async def test_telegram_guest_message_groups_consecutive_tool_calls():
    TelegramPlatformEvent = _load_telegram_platform_event()
    event = object.__new__(TelegramPlatformEvent)
    event.client = MagicMock()
    event.client._post = AsyncMock(return_value={"inline_message_id": "guest-1"})
    event.guest_query_id = "guest-query"
    event._guest_inline_message_id = None
    event._guest_parts = []
    event._guest_active_tool = None

    await event._send_guest_message(
        MessageChain(
            type="tool_call",
            chain=[MagicMock(data={"name": "web_search", "args": {"query": "one"}})],
        )
    )
    await event._send_guest_message(
        MessageChain(
            type="tool_call",
            chain=[MagicMock(data={"name": "web_search", "args": {"query": "two"}})],
        )
    )
    await event._send_guest_message(
        MessageChain(
            type="tool_call",
            chain=[MagicMock(data={"name": "shell", "args": {"command": "pwd"}})],
        )
    )

    assert event.client._post.await_count == 2
    assert event.client._post.await_args_list[0].args[0] == "answerGuestQuery"
    assert event.client._post.await_args_list[1].args[0] == "editMessageText"
    markdown = event.client._post.await_args_list[1].args[1]["rich_message"]["markdown"]
    assert "web_search (2)" in markdown
    assert "shell</summary>" in markdown
    assert "Инструмент выполняется" in markdown


@pytest.mark.asyncio
async def test_telegram_guest_message_finalizes_tools_before_text():
    TelegramPlatformEvent = _load_telegram_platform_event()
    event = object.__new__(TelegramPlatformEvent)
    event.client = MagicMock()
    event.client._post = AsyncMock(return_value={"inline_message_id": "guest-2"})
    event.guest_query_id = "guest-query"
    event._guest_inline_message_id = "guest-2"
    event._guest_parts = []
    event._guest_active_tool = {
        "name": "web_search",
        "count": 1,
        "args": ['{"q": "x"}'],
    }

    await event._send_guest_message(MessageChain().message("Готовый ответ."))

    event.client._post.assert_awaited_once()
    payload = event.client._post.await_args.args[1]
    assert payload["inline_message_id"] == "guest-2"
    markdown = payload["rich_message"]["markdown"]
    assert "web_search (1)" in markdown
    assert markdown.endswith("Готовый ответ.")


@pytest.mark.asyncio
async def test_telegram_guest_message_omits_reply_marker_from_output():
    TelegramPlatformEvent = _load_telegram_platform_event()
    event = object.__new__(TelegramPlatformEvent)
    event.client = MagicMock()
    event.client._post = AsyncMock(return_value={"inline_message_id": "guest-3"})
    event.guest_query_id = "guest-query"
    event._guest_inline_message_id = None
    event._guest_parts = []
    event._guest_active_tool = None

    await event._send_guest_message(
        MessageChain(
            chain=[
                Comp.Reply(id="1", text="quoted message"),
                Comp.Plain("Ответ без метки."),
            ]
        )
    )

    markdown = event.client._post.await_args.args[1]["result"]["input_message_content"][
        "rich_message"
    ]["markdown"]
    assert markdown == "Ответ без метки."


@pytest.mark.asyncio
async def test_telegram_tool_calls_are_appended_to_last_rich_response():
    TelegramPlatformEvent = _load_telegram_platform_event()
    event = object.__new__(TelegramPlatformEvent)
    event.client = MagicMock()
    event.client._post = AsyncMock(return_value={"message_id": 900})
    event.use_rich_messages = True
    event.show_tool_calling_execution = True
    event._tool_call_groups = []
    event._tool_status_message_id = None
    event._last_response_message_id = None
    event._last_response_markdown = ""
    event._last_response_payload = None

    await event._send_final_segment("Проверяю данные.", {"chat_id": "1001"})
    await event._update_tool_call_status(
        MessageChain(
            type="tool_call",
            chain=[MagicMock(data={"name": "web_search", "args": {"query": "test"}})],
        )
    )

    assert event.client._post.await_args_list[0].args[0] == "sendRichMessage"
    assert event.client._post.await_args_list[1].args[0] == "editMessageText"
    payload = event.client._post.await_args_list[1].args[1]
    assert payload["message_id"] == 900
    markdown = payload["rich_message"]["markdown"]
    assert markdown.startswith("Проверяю данные.")
    assert "<summary>web_search (1)</summary>" in markdown
    assert "Tool calls" not in markdown
