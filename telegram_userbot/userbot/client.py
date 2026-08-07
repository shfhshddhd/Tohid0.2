"""
Per-user Telethon userbot client with autotag and flood-wait handling.
"""
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, SessionPasswordNeededError
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
        self._bridge_mappings: dict[int, dict] = {}
        self._saved_message_outgoing_markers: set[tuple[int, str]] = set()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            logger.warning("User %s session is no longer authorized.", self.user_id)
            return
        me = await self.client.get_me()
        self._own_id = me.id
        self._running = True
        if await db.get_setting(self.user_id, "autotag", False):
            await self.enable_monitoring()
        else:
            await self._clear_monitoring_data()
        logger.info("Userbot started for user %s (own_id=%s).", self.user_id, self._own_id)

    async def stop(self) -> None:
        self._running = False
        self._remove_event_handlers()
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
        """Register monitoring handlers only while AutoTag is enabled."""
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

    async def clear_target_monitoring(self, target_id: int) -> None:
        """Clear all monitoring state belonging to one target."""
        await self._clear_monitoring_data(target_id=target_id)

    async def clear_group_monitoring(self, group_chat_id: int) -> None:
        """Clear all monitoring state belonging to one group."""
        await self._clear_monitoring_data(group_chat_id=group_chat_id)

    def _remove_event_handlers(self) -> None:
        for callback, event in self._event_handlers:
            self.client.remove_event_handler(callback, event)
        self._event_handlers.clear()

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
        # Monitor incoming messages to track targets' latest messages
        async def on_incoming(event):
            try:
                await self._handle_incoming(event)
            except Exception as exc:
                logger.exception("Error in incoming handler for user %s: %s", self.user_id, exc)

        # Monitor outgoing messages to auto-reply to targets
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
        if not event.message or not event.message.text:
            return
        # Only track messages in groups — never in private chats
        if not event.is_group:
            return
        sender = await event.get_sender()
        if sender is None:
            return

        if not await db.get_setting(self.user_id, "autotag", False):
            return
        active_group = await db.get_active_group(self.user_id)
        if active_group is not None and active_group != event.chat_id:
            return

        targets = await db.get_targets(self.user_id)
        target_ids = {t["target_id"] for t in targets}

        if sender.id in target_ids:
            chat_id = event.chat_id
            await db.update_target_last_message(
                self.user_id,
                sender.id,
                chat_id,
                event.message.id,
            )
            logger.debug(
                "Stored group message for target %s (user %s): msg_id=%s chat_id=%s",
                sender.id, self.user_id, event.message.id, chat_id,
            )

            # ── SetGroup: forward to Saved Messages if this is the active group ──
            if active_group and active_group == chat_id:
                await self._forward_to_saved_messages(
                    event=event,
                    target_id=sender.id,
                    group_chat_id=chat_id,
                    group_msg_id=event.message.id,
                )

    async def _handle_outgoing(self, event: events.NewMessage.Event) -> None:
        if not self._monitoring_enabled:
            return
        if not event.message:
            return

        # ── Saved Messages bridge ─────────────────────────────────────────────
        # Mapped replies keep their existing behavior. A fresh Saved Messages
        # message is handled independently and does not require reply_to.
        if (
            event.is_private
            and self._own_id is not None
            and event.chat_id == self._own_id
            and await db.get_setting(self.user_id, "autotag", False)
        ):
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

            if not event.message.text:
                logger.warning(
                    "Saved Messages message ignored: only text messages can be "
                    "sent to the active target group; message_id=%s",
                    event.message.id,
                )
                return
            await self._handle_saved_message_message(event)
            return

        if not event.message.text:
            return

        # Only trigger autotag when the host sends in a group — never in private chats
        if not event.is_group:
            return
        if self._consume_saved_message_outgoing_marker(event):
            return

        autotag = await db.get_setting(self.user_id, "autotag", False)
        if not autotag:
            return
        active_group = await db.get_active_group(self.user_id)
        if active_group is not None and active_group != event.chat_id:
            return

        # Capture these immediately before any await that could let the message change
        text = event.message.text
        original_message_id = event.message.id
        outgoing_chat_id = event.chat_id

        targets = await db.get_targets(self.user_id)

        # Resolve all targets' latest messages in this group in parallel
        async def _resolve(target: dict) -> tuple[dict, int] | None:
            msg_id = await db.get_target_message_in_chat(
                self.user_id, target["target_id"], outgoing_chat_id
            )
            return (target, msg_id) if msg_id else None

        resolved = await asyncio.gather(*(_resolve(t) for t in targets))
        reply_tasks = [r for r in resolved if r is not None]

        # No targets have spoken in this group — leave the original message alone
        if not reply_tasks:
            return

        # Delete the original message as fast as possible so only the reply remains
        try:
            await self.client.delete_messages(outgoing_chat_id, [original_message_id])
        except Exception as exc:
            logger.warning(
                "Could not delete original message %s in chat %s: %s",
                original_message_id, outgoing_chat_id, exc,
            )

        # Send all replies concurrently
        await asyncio.gather(
            *(
                self._send_with_retry(outgoing_chat_id, text, reply_to=msg_id, target=t)
                for t, msg_id in reply_tasks
            ),
            return_exceptions=True,
        )

    async def _send_with_retry(
        self, chat_id: int, text: str, reply_to: int, target: dict
    ) -> None:
        for attempt in range(1, FLOOD_RETRY_LIMIT + 1):
            try:
                await self.client.send_message(
                    entity=chat_id,
                    message=text,
                    reply_to=reply_to,
                )
                logger.debug(
                    "Autotag reply sent to target %s in chat %s.",
                    target.get("target_id"), chat_id,
                )
                return
            except FloodWaitError as e:
                wait = e.seconds + 1
                logger.warning(
                    "FloodWait %ds for user %s target %s (attempt %d/%d). Waiting…",
                    wait, self.user_id, target.get("target_id"), attempt, FLOOD_RETRY_LIMIT,
                )
                await asyncio.sleep(wait)
            except Exception as exc:
                logger.error(
                    "Failed to send autotag reply to %s: %s", target.get("target_id"), exc
                )
                return
        logger.error(
            "Gave up sending autotag reply to %s after %d attempts.",
            target.get("target_id"), FLOOD_RETRY_LIMIT,
        )

    def _consume_saved_message_outgoing_marker(
        self, event: events.NewMessage.Event
    ) -> bool:
        """Prevent a generated Saved Messages bridge message from re-triggering AutoTag."""
        marker = (int(event.chat_id), event.message.text or "")
        if marker not in self._saved_message_outgoing_markers:
            return False
        self._saved_message_outgoing_markers.remove(marker)
        logger.debug(
            "Ignored generated Saved Messages bridge message in AutoTag handler: "
            "chat_id=%s message_id=%s",
            event.chat_id,
            event.message.id,
        )
        return True

    async def _handle_saved_message_message(
        self, event: events.NewMessage.Event
    ) -> None:
        """
        Send a fresh Saved Messages text to the active group mentioning the
        target whose message was tracked most recently in that group.
        """
        text = event.message.text or ""
        active_group = await db.get_active_group(self.user_id)
        if active_group is None:
            logger.error(
                "Saved Messages bridge error: no active group configured; "
                "saved_message_id=%s",
                event.message.id,
            )
            return

        latest = await db.get_latest_target_in_chat(self.user_id, active_group)
        if latest is None:
            logger.error(
                "Saved Messages bridge error: no active target mapping found; "
                "active_group_id=%s saved_message_id=%s",
                active_group,
                event.message.id,
            )
            return

        target, latest_target_message_id = latest
        target_id = int(target["target_id"])

        logger.info(
            "Saved Messages outgoing message detected: saved_message_id=%s "
            "active_group_id=%s target_user_id=%s latest_target_message_id=%s",
            event.message.id,
            active_group,
            target_id,
            latest_target_message_id,
        )

        try:
            group_entity = await self.client.get_entity(active_group)
            logger.info(
                "Saved Messages target group resolved: active_group_id=%s title=%s",
                active_group,
                getattr(group_entity, "title", None) or str(active_group),
            )
        except Exception as exc:
            logger.error(
                "Saved Messages bridge error: target group resolution failed; "
                "active_group_id=%s reason=%s",
                active_group,
                exc,
            )
            return

        marker = (int(active_group), text)
        self._saved_message_outgoing_markers.add(marker)
        try:
            sent = await self._send_saved_message_with_retry(
                entity=group_entity,
                text=text,
                reply_to=latest_target_message_id,
                active_group_id=active_group,
                target_id=target_id,
            )
            if sent:
                logger.info(
                    "Saved Messages outgoing message sent successfully: "
                    "active_group_id=%s target_user_id=%s "
                    "saved_message_id=%s",
                    active_group,
                    target_id,
                    event.message.id,
                )
        finally:
            self._saved_message_outgoing_markers.discard(marker)

    async def _send_saved_message_with_retry(
        self,
        entity,
        text: str,
        reply_to: int,
        active_group_id: int,
        target_id: int,
    ) -> bool:
        for attempt in range(1, FLOOD_RETRY_LIMIT + 1):
            try:
                await self.client.send_message(
                    entity=entity,
                    message=text,
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

    # ── SetGroup helpers ───────────────────────────────────────────────────────

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

            if (
                not self._monitoring_enabled
                or not await db.get_setting(self.user_id, "autotag", False)
                or await db.get_active_group(self.user_id) != group_chat_id
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
        text = event.message.text or ""
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

        # Confirm the active group still matches (guard against stale mappings)
        active_group = await db.get_active_group(self.user_id)
        if active_group != group_chat_id:
            logger.error(
                "Saved Messages bridge error: active group mismatch; "
                "active_group_id=%s mapping_group_id=%s "
                "forwarded_saved_message_id=%s",
                active_group,
                group_chat_id,
                reply_to_saved_id,
            )
            return

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
                text=text,
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
        text: str,
        reply_to: int,
        forwarded_saved_message_id: int,
    ) -> bool:
        """Send one Saved Messages bridge reply, retrying Telegram flood waits."""
        for attempt in range(1, FLOOD_RETRY_LIMIT + 1):
            try:
                await self.client.send_message(
                    entity=entity,
                    message=text,
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
