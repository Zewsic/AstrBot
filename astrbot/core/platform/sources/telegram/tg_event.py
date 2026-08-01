import asyncio
import os
import re
from collections.abc import Callable
from typing import Any, cast

import telegramify_markdown
from telegram import ReactionTypeCustomEmoji, ReactionTypeEmoji
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ExtBot

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import (
    At,
    File,
    Image,
    Plain,
    Record,
    Reply,
    Video,
)
from astrbot.api.platform import AstrBotMessage, MessageType, PlatformMetadata
from astrbot.core.utils.metrics import Metric


def _is_gif(path: str) -> bool:
    if path.lower().endswith(".gif"):
        return True
    try:
        with open(path, "rb") as f:
            return f.read(6) in (b"GIF87a", b"GIF89a")
    except OSError:
        return False


class TelegramPlatformEvent(AstrMessageEvent):
    # Telegram 的最大消息长度限制
    MAX_MESSAGE_LENGTH = 4096
    # Rich messages accept up to 32,768 UTF-8 characters.
    MAX_RICH_MESSAGE_LENGTH = 32768
    # Telegram may rate-limit frequent draft updates.
    DRAFT_UPDATE_INTERVAL = 3.0

    SPLIT_PATTERNS = {
        "paragraph": re.compile(r"\n\n"),
        "line": re.compile(r"\n"),
        "sentence": re.compile(r"[.!?。！？]"),
        "word": re.compile(r"\s"),
    }

    # sendRichMessageDraft 的 draft_id 类级递增计数器
    _TELEGRAM_DRAFT_ID_MAX = 2_147_483_647
    _next_draft_id: int = 0

    @classmethod
    def _allocate_draft_id(cls) -> int:
        """分配一个递增的 draft_id，溢出时归 1。"""
        cls._next_draft_id = (
            1
            if cls._next_draft_id >= cls._TELEGRAM_DRAFT_ID_MAX
            else cls._next_draft_id + 1
        )
        return cls._next_draft_id

    # 消息类型到 chat action 的映射，用于优先级判断
    ACTION_BY_TYPE: dict[type, str] = {
        Record: ChatAction.UPLOAD_VOICE,
        Video: ChatAction.UPLOAD_VIDEO,
        File: ChatAction.UPLOAD_DOCUMENT,
        Image: ChatAction.UPLOAD_PHOTO,
        Plain: ChatAction.TYPING,
    }

    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        client: ExtBot,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client

    @classmethod
    def _split_message(cls, text: str) -> list[str]:
        if len(text) <= cls.MAX_MESSAGE_LENGTH:
            return [text]

        chunks = []
        while text:
            if len(text) <= cls.MAX_MESSAGE_LENGTH:
                chunks.append(text)
                break

            split_point = cls.MAX_MESSAGE_LENGTH
            segment = text[: cls.MAX_MESSAGE_LENGTH]

            for _, pattern in cls.SPLIT_PATTERNS.items():
                if matches := list(pattern.finditer(segment)):
                    last_match = matches[-1]
                    split_point = last_match.end()
                    break

            chunks.append(text[:split_point])
            text = text[split_point:].lstrip()

        return chunks

    @classmethod
    def _split_rich_message(cls, text: str) -> list[str]:
        """Split Rich Markdown while preserving the largest practical blocks."""
        if len(text) <= cls.MAX_RICH_MESSAGE_LENGTH:
            return [text]

        chunks = []
        while text:
            if len(text) <= cls.MAX_RICH_MESSAGE_LENGTH:
                chunks.append(text)
                break
            segment = text[: cls.MAX_RICH_MESSAGE_LENGTH]
            split_point = cls.MAX_RICH_MESSAGE_LENGTH
            for pattern in cls.SPLIT_PATTERNS.values():
                matches = list(pattern.finditer(segment))
                if matches:
                    split_point = matches[-1].end()
                    break
            chunks.append(text[:split_point])
            text = text[split_point:].lstrip()
        return chunks

    @classmethod
    async def _call_rich_api(
        cls, client: ExtBot, method: str, data: dict[str, Any]
    ) -> Any:
        """Call a new Bot API method not yet wrapped by python-telegram-bot.

        PTB serializes the nested ``rich_message`` object and multipart media in
        ``_post`` exactly as it does for its public Bot methods. Keeping this in
        one place makes the workaround removable once PTB exposes these methods.
        """
        post = getattr(client, "_post", None)
        if post is None:
            raise RuntimeError("Installed python-telegram-bot lacks Bot._post")
        return await post(method, data)

    @classmethod
    async def _send_rich_text_chunks(
        cls, client: ExtBot, text: str, payload: dict[str, Any]
    ) -> None:
        """Send Rich Markdown text, falling back to a regular message if needed."""
        for chunk in cls._split_rich_message(text):
            try:
                await cls._call_rich_api(
                    client,
                    "sendRichMessage",
                    {**payload, "rich_message": {"markdown": chunk}},
                )
            except Exception as e:
                logger.warning(
                    "[Telegram] sendRichMessage failed; falling back to sendMessage: %s",
                    e,
                )
                await cls._send_text_chunks(client, chunk, payload)

    @classmethod
    async def _send_text_chunks(
        cls,
        client: ExtBot,
        text: str,
        payload: dict[str, Any],
    ) -> None:
        """Legacy fallback: split and send MarkdownV2 text."""
        for chunk in cls._split_message(text):
            try:
                markdown_text = telegramify_markdown.markdownify(chunk)
                await client.send_message(
                    text=markdown_text, parse_mode="MarkdownV2", **cast(Any, payload)
                )
            except (ValueError, BadRequest) as e:
                logger.warning(
                    f"Failed to convert message to Markdown，using normal text: {e!s}"
                )
                await client.send_message(text=chunk, **cast(Any, payload))

    @classmethod
    async def _send_chat_action(
        cls,
        client: ExtBot,
        chat_id: str,
        action: ChatAction | str,
        message_thread_id: str | None = None,
    ) -> None:
        """发送聊天状态动作"""
        try:
            payload: dict[str, Any] = {"chat_id": chat_id, "action": action}
            if message_thread_id:
                payload["message_thread_id"] = message_thread_id
            await client.send_chat_action(**payload)
        except Exception as e:
            logger.warning(f"[Telegram] 发送 chat action 失败: {e}")

    @classmethod
    def _get_chat_action_for_chain(cls, chain: list[Any]) -> ChatAction | str:
        """根据消息链中的组件类型确定合适的 chat action（按优先级）"""
        for seg_type, action in cls.ACTION_BY_TYPE.items():
            if any(isinstance(seg, seg_type) for seg in chain):
                return action
        return ChatAction.TYPING

    @classmethod
    async def _send_media_with_action(
        cls,
        client: ExtBot,
        upload_action: ChatAction | str,
        send_coro,
        *,
        user_name: str,
        message_thread_id: str | None = None,
        **payload: Any,
    ) -> None:
        """发送媒体时显示 upload action，发送完成后恢复 typing"""
        effective_thread_id = message_thread_id or cast(
            str | None, payload.get("message_thread_id")
        )
        await cls._send_chat_action(
            client, user_name, upload_action, effective_thread_id
        )
        send_payload = dict(payload)
        if effective_thread_id and "message_thread_id" not in send_payload:
            send_payload["message_thread_id"] = effective_thread_id
        await send_coro(**send_payload)
        await cls._send_chat_action(
            client, user_name, ChatAction.TYPING, effective_thread_id
        )

    @classmethod
    async def _send_voice_with_fallback(
        cls,
        client: ExtBot,
        path: str,
        payload: dict[str, Any],
        *,
        caption: str | None = None,
        user_name: str = "",
        message_thread_id: str | None = None,
        use_media_action: bool = False,
    ) -> None:
        """Send a voice message, falling back to a document if the user's
        privacy settings forbid voice messages (``BadRequest`` with
        ``Voice_messages_forbidden``).

        When *use_media_action* is ``True`` the helper wraps the send calls
        with ``_send_media_with_action`` (used by the streaming path).
        """
        try:
            if use_media_action:
                media_payload = dict(payload)
                if message_thread_id and "message_thread_id" not in media_payload:
                    media_payload["message_thread_id"] = message_thread_id
                await cls._send_media_with_action(
                    client,
                    ChatAction.UPLOAD_VOICE,
                    client.send_voice,
                    user_name=user_name,
                    voice=path,
                    **cast(Any, media_payload),
                )
            else:
                await client.send_voice(voice=path, **cast(Any, payload))
        except BadRequest as e:
            # python-telegram-bot raises BadRequest for Voice_messages_forbidden;
            # distinguish the voice-privacy case via the API error message.
            if "Voice_messages_forbidden" not in e.message:
                raise
            logger.warning(
                "User privacy settings prevent receiving voice messages, falling back to sending an audio file. "
                "To enable voice messages, go to Telegram Settings → Privacy and Security → Voice Messages → set to 'Everyone'."
            )
            if use_media_action:
                media_payload = dict(payload)
                if message_thread_id and "message_thread_id" not in media_payload:
                    media_payload["message_thread_id"] = message_thread_id
                await cls._send_media_with_action(
                    client,
                    ChatAction.UPLOAD_DOCUMENT,
                    client.send_document,
                    user_name=user_name,
                    document=path,
                    caption=caption,
                    **cast(Any, media_payload),
                )
            else:
                await client.send_document(
                    document=path,
                    caption=caption,
                    **cast(Any, payload),
                )

    async def _ensure_typing(
        self,
        user_name: str,
        message_thread_id: str | None = None,
    ) -> None:
        """确保显示 typing 状态"""
        await self._send_chat_action(
            self.client, user_name, ChatAction.TYPING, message_thread_id
        )

    async def send_typing(self) -> None:
        message_thread_id = None
        if self.get_message_type() == MessageType.GROUP_MESSAGE:
            user_name = self.message_obj.group_id
        else:
            user_name = self.get_sender_id()

        if "#" in user_name:
            user_name, message_thread_id = user_name.split("#")

        await self._ensure_typing(user_name, message_thread_id)

    @classmethod
    async def send_with_client(
        cls,
        client: ExtBot,
        message: MessageChain,
        user_name: str,
    ) -> None:
        image_path = None

        has_reply = False
        reply_message_id = None
        at_user_id = None
        for i in message.chain:
            if isinstance(i, Reply):
                has_reply = True
                reply_message_id = i.id
            if isinstance(i, At):
                at_user_id = i.name

        at_flag = False
        message_thread_id = None
        if "#" in user_name:
            # it's a supergroup chat with message_thread_id
            user_name, message_thread_id = user_name.split("#")

        # 根据消息链确定合适的 chat action 并发送
        action = cls._get_chat_action_for_chain(message.chain)
        await cls._send_chat_action(client, user_name, action, message_thread_id)

        for i in message.chain:
            payload = {
                "chat_id": user_name,
            }
            if has_reply:
                payload["reply_to_message_id"] = str(reply_message_id)
            if message_thread_id:
                payload["message_thread_id"] = message_thread_id

            if isinstance(i, Plain):
                if at_user_id and not at_flag:
                    i.text = f"@{at_user_id} {i.text}"
                    at_flag = True
                rich_payload = dict(payload)
                # sendRichMessage uses ReplyParameters rather than the legacy
                # sendMessage reply_to_message_id field.
                if reply_message_id is not None:
                    rich_payload.pop("reply_to_message_id", None)
                    rich_payload["reply_parameters"] = {
                        "message_id": int(reply_message_id)
                    }
                await cls._send_rich_text_chunks(client, i.text, rich_payload)
            elif isinstance(i, Image):
                image_path = await i.convert_to_file_path()
                if _is_gif(image_path):
                    send_coro = client.send_animation
                    media_kwarg = {"animation": image_path}
                else:
                    send_coro = client.send_photo
                    media_kwarg = {"photo": image_path}
                await send_coro(**media_kwarg, **cast(Any, payload))
            elif isinstance(i, File):
                path = await i.get_file()
                name = i.name or os.path.basename(path)
                await client.send_document(
                    document=path, filename=name, **cast(Any, payload)
                )
            elif isinstance(i, Record):
                path = await i.convert_to_file_path()
                await cls._send_voice_with_fallback(
                    client,
                    path,
                    payload,
                    caption=i.text or None,
                    use_media_action=False,
                )
            elif isinstance(i, Video):
                path = await i.convert_to_file_path()
                await client.send_video(
                    video=path,
                    caption=getattr(i, "text", None) or None,
                    **cast(Any, payload),
                )

    async def send(self, message: MessageChain) -> None:
        if self.get_message_type() == MessageType.GROUP_MESSAGE:
            await self.send_with_client(self.client, message, self.message_obj.group_id)
        else:
            await self.send_with_client(self.client, message, self.get_sender_id())
        await super().send(message)

    async def react(self, emoji: str | None, big: bool = False) -> None:
        """给原消息添加 Telegram 反应：
        - 普通 emoji：传入 '👍'、'😂' 等
        - 自定义表情：传入其 custom_emoji_id（纯数字字符串）
        - 取消本机器人的反应：传入 None 或空字符串
        """
        try:
            # 解析 chat_id（去掉超级群的 "#<thread_id>" 片段）
            if self.get_message_type() == MessageType.GROUP_MESSAGE:
                chat_id = (self.message_obj.group_id or "").split("#")[0]
            else:
                chat_id = self.get_sender_id()

            message_id = int(self.message_obj.message_id)

            # 组装 reaction 参数（必须是 ReactionType 的列表）
            if not emoji:  # 清空本 bot 的反应
                reaction_param = []  # 空列表表示移除本 bot 的反应
            elif emoji.isdigit():  # 自定义表情：传 custom_emoji_id
                reaction_param = [ReactionTypeCustomEmoji(emoji)]
            else:  # 普通 emoji
                reaction_param = [ReactionTypeEmoji(emoji)]

            await self.client.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=reaction_param,  # 注意是列表
                is_big=big,  # 可选：大动画
            )
        except Exception as e:
            logger.error(f"[Telegram] 添加反应失败: {e}")

    async def _send_rich_message_draft(
        self,
        chat_id: str,
        draft_id: int,
        markdown: str,
        message_thread_id: str | None = None,
    ) -> None:
        """Update an ephemeral Rich Markdown draft in a private chat."""
        if not markdown or not markdown.strip():
            return
        data: dict[str, Any] = {
            "chat_id": int(chat_id),
            "draft_id": draft_id,
            "rich_message": {"markdown": markdown},
        }
        if message_thread_id:
            data["message_thread_id"] = int(message_thread_id)
        try:
            await self._call_rich_api(self.client, "sendRichMessageDraft", data)
        except Exception as e:
            logger.warning("[Telegram] sendRichMessageDraft failed: %s", e)

    async def _process_chain_items(
        self,
        chain: MessageChain,
        payload: dict[str, Any],
        user_name: str,
        message_thread_id: str | None,
        on_text: Callable[[str], None],
    ) -> None:
        """处理 MessageChain 中的各类组件，文本通过 on_text 回调追加，媒体直接发送。"""
        for i in chain.chain:
            if isinstance(i, Plain):
                on_text(i.text)
            elif isinstance(i, Image):
                image_path = await i.convert_to_file_path()
                if _is_gif(image_path):
                    action = ChatAction.UPLOAD_VIDEO
                    send_coro = self.client.send_animation
                    media_kwarg = {"animation": image_path}
                else:
                    action = ChatAction.UPLOAD_PHOTO
                    send_coro = self.client.send_photo
                    media_kwarg = {"photo": image_path}
                await self._send_media_with_action(
                    self.client,
                    action,
                    send_coro,
                    user_name=user_name,
                    **media_kwarg,
                    **cast(Any, payload),
                )
            elif isinstance(i, File):
                path = await i.get_file()
                name = i.name or os.path.basename(path)
                await self._send_media_with_action(
                    self.client,
                    ChatAction.UPLOAD_DOCUMENT,
                    self.client.send_document,
                    user_name=user_name,
                    document=path,
                    filename=name,
                    **cast(Any, payload),
                )
            elif isinstance(i, Record):
                path = await i.convert_to_file_path()
                await self._send_voice_with_fallback(
                    self.client,
                    path,
                    payload,
                    caption=i.text or None,
                    user_name=user_name,
                    message_thread_id=message_thread_id,
                    use_media_action=True,
                )
            elif isinstance(i, Video):
                path = await i.convert_to_file_path()
                await self._send_media_with_action(
                    self.client,
                    ChatAction.UPLOAD_VIDEO,
                    self.client.send_video,
                    user_name=user_name,
                    video=path,
                    **cast(Any, payload),
                )
            else:
                logger.warning(f"不支持的消息类型: {type(i)}")

    async def _send_final_segment(self, delta: str, payload: dict[str, Any]) -> None:
        """Persist accumulated text as a Rich Markdown message."""
        await self._send_rich_text_chunks(self.client, delta, payload)

    async def send_streaming(self, generator, use_fallback: bool = False):
        message_thread_id = None

        if self.get_message_type() == MessageType.GROUP_MESSAGE:
            user_name = self.message_obj.group_id
        else:
            user_name = self.get_sender_id()

        if "#" in user_name:
            # it's a supergroup chat with message_thread_id
            user_name, message_thread_id = user_name.split("#")
        payload = {
            "chat_id": user_name,
        }
        if message_thread_id:
            payload["message_thread_id"] = message_thread_id

        # sendRichMessageDraft 仅支持私聊（显式检查 FRIEND_MESSAGE）
        is_private = self.get_message_type() == MessageType.FRIEND_MESSAGE

        if is_private:
            logger.info("[Telegram] 流式输出: 使用 sendRichMessageDraft (私聊)")
            await self._send_streaming_draft(
                user_name, message_thread_id, payload, generator
            )
        else:
            logger.info("[Telegram] 流式输出: 使用 edit_message_text fallback (群聊)")
            await self._send_streaming_edit(
                user_name, message_thread_id, payload, generator
            )

        # 内联父类 send_streaming 的副作用（避免传入已消费的 generator）
        asyncio.create_task(
            Metric.upload(msg_event_tick=1, adapter_name=self.platform_meta.name),
        )
        self._has_send_oper = True

    async def _send_streaming_draft(
        self,
        user_name: str,
        message_thread_id: str | None,
        payload: dict[str, Any],
        generator,
    ) -> None:
        """使用 sendRichMessageDraft API 进行流式推送（私聊专用）。

        流式过程中使用 sendRichMessageDraft 推送草稿动画，
        流式结束后发送一条真实消息保留最终内容（draft 是临时的，会消失）。
        使用信号驱动的发送循环：每次有新 token 到达时唤醒发送，
        发送频率由网络 RTT 自然限制（最多一个请求 in-flight）。
        """
        draft_id = self._allocate_draft_id()
        delta = ""
        last_sent_text = ""
        last_sent_at = float("-inf")
        done = False  # 信号：生成器已结束
        text_changed = asyncio.Event()  # 有新 token 到达时触发

        async def _draft_sender_loop() -> None:
            """信号驱动的草稿发送循环，有新内容就发，RTT 自然限流。"""
            nonlocal last_sent_at, last_sent_text
            while not done:
                await text_changed.wait()
                text_changed.clear()
                # Coalesce arriving tokens and update the draft at most once every
                # DRAFT_UPDATE_INTERVAL seconds. This deliberately uses the latest
                # buffer after sleeping, rather than queueing stale intermediate drafts.
                if delta and delta != last_sent_text:
                    elapsed = asyncio.get_running_loop().time() - last_sent_at
                    if elapsed < self.DRAFT_UPDATE_INTERVAL:
                        await asyncio.sleep(self.DRAFT_UPDATE_INTERVAL - elapsed)
                    draft_text = delta[: self.MAX_RICH_MESSAGE_LENGTH]
                    if draft_text != last_sent_text:
                        await self._send_rich_message_draft(
                            user_name, draft_id, draft_text, message_thread_id
                        )
                        last_sent_text = draft_text
                        last_sent_at = asyncio.get_running_loop().time()

        sender_task = asyncio.create_task(_draft_sender_loop())

        def _append_text(t: str) -> None:
            nonlocal delta
            delta += t
            text_changed.set()  # 唤醒发送循环

        try:
            async for chain in generator:
                if not isinstance(chain, MessageChain):
                    continue

                if chain.type == "break":
                    # 分割符：发送真实消息保留内容，重置缓冲区
                    if delta:
                        await self._send_final_segment(delta, payload)
                    delta = ""
                    last_sent_text = ""
                    draft_id = self._allocate_draft_id()
                    continue

                await self._process_chain_items(
                    chain, payload, user_name, message_thread_id, _append_text
                )
        finally:
            done = True
            text_changed.set()  # 唤醒循环使其退出
            await sender_task
        # The draft is ephemeral; persist the completed Rich Message instead.
        if delta:
            await self._send_final_segment(delta, payload)

    async def _send_streaming_edit(
        self,
        user_name: str,
        message_thread_id: str | None,
        payload: dict[str, Any],
        generator,
    ) -> None:
        """Buffer group output and persist each segment as a Rich Message.

        Telegram only permits ``sendRichMessageDraft`` in private chats. In
        groups we keep the typing indicator alive and send completed segments
        through ``sendRichMessage`` instead of emitting regular-message edits.
        """
        delta = ""
        last_typing_at = float("-inf")

        def _append_text(text: str) -> None:
            nonlocal delta
            delta += text

        async for chain in generator:
            if not isinstance(chain, MessageChain):
                continue
            if chain.type == "break":
                if delta:
                    await self._send_final_segment(delta, payload)
                    delta = ""
                continue

            await self._process_chain_items(
                chain, payload, user_name, message_thread_id, _append_text
            )
            now = asyncio.get_running_loop().time()
            if now - last_typing_at >= 3.0:
                await self._ensure_typing(user_name, message_thread_id)
                last_typing_at = now

        if delta:
            await self._send_final_segment(delta, payload)
