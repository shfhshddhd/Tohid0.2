"""
/target <add|remove|list|removeall> command handler.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
import database.mongo as db

logger = logging.getLogger(__name__)

# Awaiting confirmation for removeall
_pending_removeall: set[int] = set()


async def target_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    args = ctx.args or []

    if not args:
        await update.message.reply_text(
            "ℹ️ Usage:\n"
            "/target add <username or user_id>\n"
            "/target remove <username or user_id>\n"
            "/target list\n"
            "/target removeall"
        )
        return

    sub = args[0].lower()

    # ── list ──────────────────────────────────────────────────────────────────
    if sub == "list":
        targets = await db.get_targets(user_id)
        if not targets:
            await update.message.reply_text("📋 Your target list is empty.")
            return
        lines = ["📋 <b>Your targets:</b>\n"]
        for i, t in enumerate(targets, 1):
            name = t.get("name") or "Unknown"
            username = f"@{t['username']}" if t.get("username") else "—"
            tid = t.get("target_id", "—")
            lines.append(f"{i}. <b>{name}</b>  {username}  (<code>{tid}</code>)")
        await update.message.reply_html("\n".join(lines))
        return

    # ── add ───────────────────────────────────────────────────────────────────
    if sub == "add":
        if len(args) < 2:
            await update.message.reply_text("Usage: /target add <username or user_id>")
            return

        if not manager.is_hosted(user_id):
            await update.message.reply_text(
                "⚠️ You need a hosted account first. Use /host to set one up."
            )
            return

        identifier = args[1]
        await update.message.reply_text(f"🔍 Resolving {identifier}…")
        target = await manager.resolve_target(user_id, identifier)
        if target is None:
            await update.message.reply_text(
                f"❌ Could not find user '{identifier}'.\n"
                "Make sure the username is correct or they share a group with your hosted account."
            )
            return

        added = await db.add_target(user_id, target)
        if not added:
            await update.message.reply_text(
                f"⚠️ {target['name']} is already in your target list."
            )
            return

        name = target["name"]
        username = f"@{target['username']}" if target.get("username") else ""
        tid = target["target_id"]
        await update.message.reply_text(
            f"✅ Added target:\n"
            f"Name: <b>{name}</b>\n"
            f"Username: {username or '—'}\n"
            f"User ID: <code>{tid}</code>",
            parse_mode="HTML",
        )
        return

    # ── remove ────────────────────────────────────────────────────────────────
    if sub == "remove":
        if len(args) < 2:
            await update.message.reply_text("Usage: /target remove <username or user_id>")
            return
        identifier = args[1]
        stored_target_id: int | None = None
        for target in await db.get_targets(user_id):
            if (
                str(target.get("target_id")) == identifier.lstrip("@")
                or (target.get("username") or "").lower() == identifier.lstrip("@").lower()
            ):
                stored_target_id = target.get("target_id")
                break

        resolved_target_id = stored_target_id
        if resolved_target_id is None and manager.is_hosted(user_id):
            resolved = await manager.resolve_target(user_id, identifier)
            if resolved is not None:
                resolved_target_id = resolved["target_id"]

        removed = await db.remove_target(
            user_id,
            identifier,
            resolved_target_id=resolved_target_id,
        )
        if removed:
            uc = manager.get_client(user_id)
            # Removing a target ends the current monitoring generation
            # completely. This prevents any old listener, bridge mapping,
            # cache, AutoTag state, or active group from being revived when a
            # target is added again later.
            await db.set_setting(user_id, "autotag", False)
            await db.clear_active_group(user_id)
            if uc is not None:
                await uc.disable_monitoring()
            else:
                await db.clear_monitoring_data(
                    user_id,
                    target_id=None,
                )
            await update.message.reply_text(f"🗑️ Removed '{identifier}' from your target list.")
        else:
            await update.message.reply_text(
                f"❌ '{identifier}' was not found in your target list."
            )
        return

    # ── removeall ─────────────────────────────────────────────────────────────
    if sub == "removeall":
        targets = await db.get_targets(user_id)
        if not targets:
            await update.message.reply_text("📋 Your target list is already empty.")
            return

        if user_id not in _pending_removeall:
            _pending_removeall.add(user_id)
            count = len(targets)
            await update.message.reply_text(
                f"⚠️ This will remove all <b>{count}</b> target(s).\n"
                "Send /target removeall again to confirm, or any other command to cancel.",
                parse_mode="HTML",
            )
            return

        # Confirmed
        _pending_removeall.discard(user_id)
        await db.clear_targets(user_id)
        uc = manager.get_client(user_id)
        if uc is not None:
            await uc.disable_monitoring()
        await update.message.reply_text("🗑️ All targets have been removed.")
        return

    # Cancel pending removeall on any other subcommand
    _pending_removeall.discard(user_id)
    await update.message.reply_text(
        f"❓ Unknown subcommand '{sub}'.\nValid: add, remove, list, removeall"
    )
