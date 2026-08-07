"""Per-user Telethon client for persistent group-to-target message bridges."""
import asyncio
import logging
import random
import unicodedata
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from openai import AsyncOpenAI
import config
import database.mongo as db

logger = logging.getLogger(__name__)

FLOOD_RETRY_LIMIT = 5


class UserbotClient:
    def __init__(self, user_id: int, session_string: str):
        self.user_id = user_id
        self.session_string = session_string
        self.client: TelegramClient = TelegramClient(
            StringSession(session_string),
            config.API_ID,
            config.API_HASH,
        )
        self._running = False
        self._own_id: int | None = None  # cached Telegram user ID of the hosted account
        self._monitoring_enabled = False
        self._event_handlers: list[tuple[object, object]] = []
        self._ai_event_handlers: list[tuple[object, object]] = []
        self._ai_tasks: set[asyncio.Task] = set()
        self._ai_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._bridge_mappings: dict[int, dict] = {}
        self._ai_client: AsyncOpenAI | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            logger.warning("User %s session is no longer authorized.", self.user_id)
            return
        me = await self.client.get_me()
        self._own_id = me.id
        self._running = True
        if (
            await db.get_setting(self.user_id, "bot_enabled", True)
            and await db.get_target_mappings(self.user_id)
        ):
            await self.enable_monitoring()
        if await db.get_setting(self.user_id, "ai_mode", False):
            self.enable_ai_mode()
        logger.info("Userbot started for user %s (own_id=%s).", self.user_id, self._own_id)

    async def stop(self) -> None:
        self._running = False
        self._remove_event_handlers()
        await self.disable_ai_mode()
        self._monitoring_enabled = False
        try:
            await self.client.disconnect()
        except Exception:
            pass
        logger.info("Userbot stopped for user %s.", self.user_id)

    def is_running(self) -> bool:
        return self._running and self.client.is_connected()

    # ── Event handlers ─────────────────────────────────────────────────────────

    async def enable_monitoring(self) -> None:
        """Register bridge handlers while at least one mapping exists."""
        if not await db.get_setting(self.user_id, "bot_enabled", True):
            logger.info("Monitoring remains disabled for user %s.", self.user_id)
            return
        if self._monitoring_enabled:
            return
        self._monitoring_enabled = True
        await self._load_bridge_mappings()
        self._register_handlers()
        logger.info("Monitoring enabled for user %s.", self.user_id)

    async def disable_monitoring(self) -> None:
        """Stop handlers and remove every cache/bridge record they use."""
        self._monitoring_enabled = False
        self._remove_event_handlers()
        await self._clear_monitoring_data()
        logger.info("Monitoring disabled and cleared for user %s.", self.user_id)

    async def clear_target_monitoring(
        self,
        target_id: int,
        group_chat_id: int | None = None,
    ) -> None:
        """Clear bridge state for one target, optionally within one group."""
        await self._clear_monitoring_data(
            target_id=target_id,
            group_chat_id=group_chat_id,
        )

    async def clear_group_monitoring(self, group_chat_id: int) -> None:
        """Clear all monitoring state belonging to one group."""
        await self._clear_monitoring_data(group_chat_id=group_chat_id)

    def _remove_event_handlers(self) -> None:
        for callback, event in self._event_handlers:
            self.client.remove_event_handler(callback, event)
        self._event_handlers.clear()

    def enable_ai_mode(self) -> None:
        """Register the independent AI mention listener."""
        if self._ai_event_handlers:
            return

        async def on_ai_message(event):
            try:
                if not await self._is_ai_mention(event):
                    return
                task = asyncio.create_task(self._handle_ai_mention(event))
                self._ai_tasks.add(task)
                task.add_done_callback(self._ai_tasks.discard)
            except Exception:
                logger.exception("Error in AI mention handler for user %s", self.user_id)

        incoming_event = events.NewMessage(incoming=True)
        self.client.add_event_handler(on_ai_message, incoming_event)
        self._ai_event_handlers.append((on_ai_message, incoming_event))
        logger.info("AI mode enabled for user %s.", self.user_id)

    async def disable_ai_mode(self) -> None:
        """Remove the AI listener and cancel delayed replies."""
        for callback, event in self._ai_event_handlers:
            self.client.remove_event_handler(callback, event)
        self._ai_event_handlers.clear()
        tasks = list(self._ai_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._ai_tasks.clear()
        logger.info("AI mode disabled for user %s.", self.user_id)

    async def _is_ai_mention(self, event: events.NewMessage.Event) -> bool:
        if not self._running or not event.message or not event.is_group:
            return False
        if not getattr(event.message, "mentioned", False):
            return False
        sender = await event.get_sender()
        if sender is None or getattr(sender, "bot", False):
            return False
        return int(getattr(sender, "id", 0)) != self._own_id

    async def _handle_ai_mention(self, event: events.NewMessage.Event) -> None:
        message_text = (event.message.text or "").strip()
        if not message_text:
            return
        sender = await event.get_sender()
        if sender is None:
            return
        chat_id = int(event.chat_id)
        participant_id = int(sender.id)
        lock = self._ai_locks.setdefault((chat_id, participant_id), asyncio.Lock())

        async with lock:
            await asyncio.sleep(random.uniform(5, 6))
            if not self._running or not self._ai_event_handlers:
                return
            reply = await self._generate_ai_reply(
                chat_id=chat_id,
                participant_id=participant_id,
                sender_name=self._sender_name(sender),
                message_text=message_text,
            )
            if not reply:
                return
            await self.client.send_message(
                entity=chat_id,
                message=reply,
                reply_to=event.message.id,
            )
            await db.append_ai_memory(
                user_id=self.user_id,
                chat_id=chat_id,
                participant_id=participant_id,
                user_message=message_text,
                assistant_message=reply,
            )

    async def _generate_ai_reply(
        self,
        chat_id: int,
        participant_id: int,
        sender_name: str,
        message_text: str,
    ) -> str:
        if not config.OPENAI_API_KEY:
            logger.error("AI mode cannot reply: OPENAI_API_KEY is not configured.")
            return ""
        if self._ai_client is None:
            self._ai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

        history = await db.get_ai_memory(
            self.user_id,
            chat_id,
            participant_id,
        )
        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "YOU WRITE A NATURAL TELEGRAM GROUP REPLY TO THE USER "
                    "WHO JUST MENTIONED THE ACCOUNT OWNER. ACT LIKE A COLD, "
                    "CONFIDENT, HIGH-STATUS PERSONA: SHORT, DIRECT, ASSERTIVE, "
                    "AND UNBOTHERED. MATCH THE USER'S LANGUAGE, TONE, HUMOR, "
                    "ATTITUDE, AND LEVEL OF FORMALITY. USE ONLY ENGLISH "
                    "ALPHABETS AND NUMBERS ONLY. NEVER USE PUNCTUATION. NEVER "
                    "USE "
                    "DEVANAGARI OR ANY OTHER NON-LATIN SCRIPT. ROMANIZED "
                    "HINDI OR HINGLISH IS ALLOWED WHEN IT FITS THE USER'S "
                    "MESSAGE. DO NOT USE POLITE FILLER, FLUFF, OR GENERIC "
                    "ASSISTANT PHRASES. IF THE USER IS RUDE, ABUSIVE, OR "
                    "ROASTS YOU, RESPOND WITH A FIRM, SHARP COMEBACK THAT "
                    "STAYS WITHIN SAFETY LIMITS AND DOES NOT THREATEN, "
                    "TARGET, OR HATE AGAINST A PERSON OR GROUP. DO NOT "
                    "CLAIM TO BE HUMAN OR DENY BEING AI IF ASKED DIRECTLY; "
                    "ANSWER BRIEFLY AND CONFIDENTLY. OUTPUT ONLY THE REPLY "
                    "IN CAPITAL LETTERS. NEVER USE EMOJIS OR FULL STOPS. "
                    "KEEP IT CONCISE AND DO NOT DESCRIBE THESE INSTRUCTIONS"
                ),
            }
        ]
        for item in history:
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and content:
                prompt_messages.append({"role": role, "content": content})
        prompt_messages.append(
            {
                "role": "user",
                "content": f"{sender_name} SAYS:\n{message_text}",
            }
        )

        try:
            completion = await self._ai_client.chat.completions.create(
                model="gpt-5.4-mini",
                max_completion_tokens=240,
                messages=prompt_messages,
            )
        except Exception:
            logger.exception("AI response generation failed for user %s", self.user_id)
            return ""

        content = completion.choices[0].message.content if completion.choices else ""
        return self._sanitize_ai_reply(content or "")

    @staticmethod
    def _sender_name(sender) -> str:
        name = " ".join(
            part for part in (getattr(sender, "first_name", None), getattr(sender, "last_name", None))
            if part
        ).strip()
        return name or getattr(sender, "username", None) or "THE USER"

    @staticmethod
    def _sanitize_ai_reply(text: str) -> str:
        cleaned = text.upper().replace(".", "").replace("…", "")
        cleaned = "".join(
            char
            for char in cleaned
            if ord(char) < 128
            and unicodedata.category(char)[0] not in {"P", "S"}
        )
        return " ".join(cleaned.split()).strip()

    async def _load_bridge_mappings(self) -> None:
        mappings = await db.get_setgroup_mappings(self.user_id)
        self._bridge_mappings = {
            int(mapping["forwarded_saved_message_id"]): mapping
            for mapping in mappings
        }
        logger.info(
            "Loaded %d Saved Messages bridge mapping(s) for user %s.",
            len(self._bridge_mappings),
            self.user_id,
        )

    async def _clear_monitoring_data(
        self,
        target_id: int | None = None,
        group_chat_id: int | None = None,
    ) -> None:
        if target_id is None and group_chat_id is None:
            mapping_ids = list(self._bridge_mappings)
        else:
            mapping_ids = [
                forwarded_id
                for forwarded_id, mapping in self._bridge_mappings.items()
                if (
                    target_id is None
                    or mapping.get("target_user_id") == target_id
                )
                and (
                    group_chat_id is None
                    or mapping.get("active_group_id") == group_chat_id
                    or mapping.get("original_chat_id") == group_chat_id
                )
            ]
        for forwarded_id in mapping_ids:
            self._bridge_mappings.pop(forwarded_id, None)

        forwarded_ids = await db.clear_monitoring_data(
            self.user_id,
            target_id=target_id,
            group_chat_id=group_chat_id,
        )
        forwarded_ids = list(dict.fromkeys(forwarded_ids + mapping_ids))
        if not forwarded_ids or not self.client.is_connected():
            return
        try:
            await self.client.delete_messages("me", forwarded_ids)
        except Exception as exc:
            logger.warning(
                "Could not remove %d Saved Messages bridge message(s) for user %s: %s",
                len(forwarded_ids),
                self.user_id,
                exc,
            )

    def _register_handlers(self) -> None:
        # Monitor incoming group messages from mapped targets.
        async def on_incoming(event):
            try:
                await self._handle_incoming(event)
            except Exception as exc:
                logger.exception("Error in incoming handler for user %s: %s", self.user_id, exc)

        # Monitor outgoing Saved Messages messages for the bridge.
        async def on_outgoing(event):
            try:
                await self._handle_outgoing(event)
            except Exception as exc:
                logger.exception("Error in outgoing handler for user %s: %s", self.user_id, exc)

        incoming_event = events.NewMessage(incoming=True)
        outgoing_event = events.NewMessage(outgoing=True)
        self.client.add_event_handler(on_incoming, incoming_event)
        self.client.add_event_handler(on_outgoing, outgoing_event)
        self._event_handlers.extend([
            (on_incoming, incoming_event),
            (on_outgoing, outgoing_event),
        ])

    async def _handle_incoming(self, event: events.NewMessage.Event) -> None:
        if not self._monitoring_enabled:
            return
        if not event.message:
            return
        # Only track messages in groups — never in private chats
        if not event.is_group:
            return
        sender = await event.get_sender()
        if sender is None:
            return

        chat_id = int(event.chat_id)
        mappings = await db.get_target_mappings(
            self.user_id,
            group_chat_id=chat_id,
            target_user_id=int(sender.id),
        )
        if not mappings:
            return

        await db.update_target_last_message(
            self.user_id,
            int(sender.id),
            chat_id,
            event.message.id,
            message_date=event.message.date,
        )
        logger.debug(
            "Stored mapped group message: user_id=%s target_id=%s chat_id=%s msg_id=%s",
            self.user_id,
            sender.id,
            chat_id,
            event.message.id,
        )
        await self._forward_to_saved_messages(
            event=event,
            target_id=int(sender.id),
            group_chat_id=chat_id,
            group_msg_id=event.message.id,
        )

    async def _handle_outgoing(self, event: events.NewMessage.Event) -> None:
        if not self._monitoring_enabled:
            return
        if not event.message:
            return

        # ── Saved Messages bridge ─────────────────────────────────────────────
        if (
            event.is_private
            and self._own_id is not None
            and event.chat_id == self._own_id
        ):
            # The forwarded source message itself is an outgoing Saved Messages
            # event. It must never be echoed back into its source group.
            if event.message.id in self._bridge_mappings or event.message.fwd_from is not None:
                return
            if event.message.reply_to is not None:
                reply_to_saved_id = event.message.reply_to.reply_to_msg_id
                mapping = self._bridge_mappings.get(reply_to_saved_id)
                if mapping is None:
                    mapping = await db.get_setgroup_mapping(
                        self.user_id, reply_to_saved_id
                    )
                    if mapping is not None:
                        self._bridge_mappings[reply_to_saved_id] = mapping
                if mapping is not None:
                    await self._handle_setgroup_reply(event)
                    return

            await self._handle_saved_message_message(event)
            return

    async def _handle_saved_message_message(
        self, event: events.NewMessage.Event
    ) -> None:
        """
        Send a fresh Saved Messages text to the group containing the newest
        message from any permanently mapped target.
        """
        latest = await db.get_latest_mapped_target_message(self.user_id)
        if latest is None:
            logger.error(
                "Saved Messages bridge error: no mapped target message exists; "
                "saved_message_id=%s",
                event.message.id,
            )
            return

        mapping, latest_target_message_id = latest
        group_chat_id = int(mapping["group_chat_id"])
        target_id = int(mapping["target_user_id"])

        logger.info(
            "Saved Messages outgoing message detected: saved_message_id=%s "
            "mapped_group_id=%s target_user_id=%s latest_target_message_id=%s",
            event.message.id,
            group_chat_id,
            target_id,
            latest_target_message_id,
        )

        try:
            group_entity = await self.client.get_entity(group_chat_id)
            logger.info(
                "Saved Messages target group resolved: group_chat_id=%s title=%s",
                group_chat_id,
                getattr(group_entity, "title", None) or str(group_chat_id),
            )
        except Exception as exc:
            logger.error(
                "Saved Messages bridge error: target group resolution failed; "
                "group_chat_id=%s reason=%s",
                group_chat_id,
                exc,
            )
            return

        sent = await self._send_saved_message_with_retry(
            entity=group_entity,
            message=event.message,
            reply_to=latest_target_message_id,
            active_group_id=group_chat_id,
            target_id=target_id,
        )
        if sent:
            logger.info(
                "Saved Messages standalone message bridged: group_chat_id=%s "
                "target_user_id=%s saved_message_id=%s",
                group_chat_id,
                target_id,
                event.message.id,
            )

    async def _send_saved_message_with_retry(
        self,
        entity,
        message,
        reply_to: int,
        active_group_id: int,
        target_id: int,
    ) -> bool:
        for attempt in range(1, FLOOD_RETRY_LIMIT + 1):
            try:
                await self.client.send_message(
                    entity=entity,
                    message=message.text or None,
                    file=message.media,
                    reply_to=reply_to,
                )
                return True
            except FloodWaitError as exc:
                wait = exc.seconds + 1
                logger.warning(
                    "Saved Messages bridge FloodWait: active_group_id=%s "
                    "target_user_id=%s wait_seconds=%s attempt=%s/%s",
                    active_group_id,
                    target_id,
                    wait,
                    attempt,
                    FLOOD_RETRY_LIMIT,
                )
                await asyncio.sleep(wait)
            except Exception as exc:
                logger.error(
                    "Saved Messages bridge error: target group send failed; "
                    "active_group_id=%s target_user_id=%s reason=%s",
                    active_group_id,
                    target_id,
                    exc,
                )
                return False

        logger.error(
            "Saved Messages bridge error: target group flood-wait retry limit "
            "exceeded; active_group_id=%s target_user_id=%s attempts=%s",
            active_group_id,
            target_id,
            FLOOD_RETRY_LIMIT,
        )
        return False

    # ── Saved Messages bridge helpers ──────────────────────────────────────────

    async def _forward_to_saved_messages(
        self,
        event: events.NewMessage.Event,
        target_id: int,
        group_chat_id: int,
        group_msg_id: int,
    ) -> None:
        """
        Forward a target's group message to the host's Saved Messages and store
        the mapping so that a reply in Saved Messages can be routed back.
        """
        try:
            if not self._monitoring_enabled:
                return
            forwarded = await self.client.forward_messages(
                entity="me",
                messages=event.message.id,
                from_peer=group_chat_id,
            )
            # forward_messages returns a list; grab the first result
            fwd_msg = forwarded[0] if isinstance(forwarded, list) else forwarded
            saved_msg_id = fwd_msg.id

            if not self._monitoring_enabled or not await db.get_target_mapping(
                self.user_id,
                group_chat_id,
                target_id,
            ):
                await self.client.delete_messages("me", [saved_msg_id])
                return

            await db.save_setgroup_mapping(
                user_id=self.user_id,
                original_chat_id=group_chat_id,
                original_message_id=group_msg_id,
                forwarded_message_id=saved_msg_id,
                target_id=target_id,
                active_group_id=group_chat_id,
            )
            self._bridge_mappings[saved_msg_id] = {
                "original_chat_id": group_chat_id,
                "original_message_id": group_msg_id,
                "forwarded_saved_message_id": saved_msg_id,
                "target_user_id": target_id,
                "active_group_id": group_chat_id,
                "reply_sent": False,
            }
            logger.info(
                "Saved Messages bridge mapping created: "
                "original_chat_id=%s original_message_id=%s "
                "forwarded_saved_message_id=%s target_user_id=%s active_group_id=%s",
                group_chat_id,
                group_msg_id,
                saved_msg_id,
                target_id,
                group_chat_id,
            )
        except Exception as exc:
            logger.error(
                "Saved Messages bridge mapping/forwarding error for user %s: %s",
                self.user_id,
                exc,
            )

    async def _handle_setgroup_reply(self, event: events.NewMessage.Event) -> None:
        """
        Called when the host sends a reply inside Saved Messages.
        Looks up which group message the replied-to Saved Messages message maps to,
        then sends the reply text into the active group as a reply to that message.
        """
        reply_to_saved_id = event.message.reply_to.reply_to_msg_id
        logger.info(
            "Saved Messages reply detected: user_id=%s reply_message_id=%s "
            "reply_to_saved_message_id=%s",
            self.user_id,
            event.message.id,
            reply_to_saved_id,
        )

        mapping = self._bridge_mappings.get(reply_to_saved_id)
        if mapping is None:
            mapping = await db.get_setgroup_mapping(self.user_id, reply_to_saved_id)
            if mapping is not None:
                self._bridge_mappings[reply_to_saved_id] = mapping
        if not mapping:
            # This reply is to a Saved Messages message unrelated to setgroup — ignore
            logger.debug(
                "Saved Messages reply ignored: no bridge mapping for "
                "saved_message_id=%s",
                reply_to_saved_id,
            )
            return
        if mapping.get("reply_sent", False):
            logger.info(
                "Saved Messages reply ignored: bridge mapping already consumed "
                "for forwarded_saved_message_id=%s",
                reply_to_saved_id,
            )
            return

        group_chat_id: int = mapping["original_chat_id"]
        group_msg_id: int = mapping["original_message_id"]
        target_id: int = mapping["target_user_id"]

        try:
            group_entity = await self.client.get_entity(group_chat_id)
            logger.info(
                "Original group resolved: original_chat_id=%s title=%s "
                "original_message_id=%s",
                group_chat_id,
                getattr(group_entity, "title", None) or str(group_chat_id),
                group_msg_id,
            )
        except Exception as exc:
            logger.error(
                "Saved Messages bridge error: original group resolution failed; "
                "original_chat_id=%s reason=%s",
                group_chat_id,
                exc,
            )
            return

        try:
            sent = await self._send_bridge_reply(
                entity=group_entity,
                message=event.message,
                reply_to=group_msg_id,
                forwarded_saved_message_id=reply_to_saved_id,
            )
            if not sent:
                return
            await db.mark_setgroup_reply_sent(
                user_id=self.user_id,
                forwarded_saved_message_id=reply_to_saved_id,
                reply_message_id=event.message.id,
            )
            mapping["reply_sent"] = True
            logger.info(
                "Saved Messages bridge reply sent successfully: "
                "original_chat_id=%s original_message_id=%s "
                "forwarded_saved_message_id=%s target_user_id=%s",
                group_chat_id,
                group_msg_id,
                reply_to_saved_id,
                target_id,
            )
        except Exception as exc:
            logger.error(
                "Saved Messages bridge error: reply send failed; "
                "original_chat_id=%s original_message_id=%s "
                "forwarded_saved_message_id=%s reason=%s",
                group_chat_id,
                group_msg_id,
                reply_to_saved_id,
                exc,
            )

    async def _send_bridge_reply(
        self,
        entity,
        message,
        reply_to: int,
        forwarded_saved_message_id: int,
    ) -> bool:
        """Send one Saved Messages bridge reply, retrying Telegram flood waits."""
        for attempt in range(1, FLOOD_RETRY_LIMIT + 1):
            try:
                await self.client.send_message(
                    entity=entity,
                    message=message.text or None,
                    file=message.media,
                    reply_to=reply_to,
                )
                return True
            except FloodWaitError as exc:
                wait = exc.seconds + 1
                logger.warning(
                    "Saved Messages bridge FloodWait: wait_seconds=%s "
                    "attempt=%s/%s forwarded_saved_message_id=%s",
                    wait,
                    attempt,
                    FLOOD_RETRY_LIMIT,
                    forwarded_saved_message_id,
                )
                await asyncio.sleep(wait)
            except Exception as exc:
                logger.error(
                    "Saved Messages bridge error: Telegram rejected reply; "
                    "forwarded_saved_message_id=%s reason=%s",
                    forwarded_saved_message_id,
                    exc,
                )
                return False

        logger.error(
            "Saved Messages bridge error: flood-wait retry limit exceeded; "
            "forwarded_saved_message_id=%s attempts=%s",
            forwarded_saved_message_id,
            FLOOD_RETRY_LIMIT,
        )
        return False
