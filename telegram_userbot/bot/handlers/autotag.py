"""
/autotag on|off command handler.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
import database.mongo as db

logger = logging.getLogger(__name__)


async def autotag_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    args = ctx.args or []

    if not manager.is_hosted(user_id):
        await update.message.reply_text(
            "⚠️ You need a hosted account first. Use /host to set one up."
        )
        return

    if not args or args[0].lower() not in ("on", "off"):
        current = await db.get_setting(user_id, "autotag", False)
        status = "🟢 ON" if current else "🔴 OFF"
        await update.message.reply_text(
            f"Autotag is currently {status}.\nUsage: /autotag on  or  /autotag off"
        )
        return

    enable = args[0].lower() == "on"
    uc = manager.get_client(user_id)

    if enable:
        await db.set_setting(user_id, "autotag", True)
        if uc is not None:
            await uc.enable_monitoring()
        targets = await db.get_targets(user_id)
        target_count = len(targets)
        await update.message.reply_text(
            "🟢 <b>Autotag enabled.</b>\n\n"
            f"Monitoring {target_count} target(s).\n\n"
            "Whenever a target sends a message in a group you're both in, "
            "their latest message is tracked.\n"
            "Any text message <i>you</i> send will automatically be sent as a "
            "reply to every target's latest message.",
            parse_mode="HTML",
        )
    else:
        await db.set_setting(user_id, "autotag", False)
        if uc is not None:
            await uc.disable_monitoring()
        await update.message.reply_text(
            "🔴 <b>Autotag disabled.</b>\n\n"
            "All target monitoring, forwarding, Saved Messages reply mappings, "
            "and cached messages have been stopped and cleared.",
            parse_mode="HTML",
        )
