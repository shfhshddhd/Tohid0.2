"""Persistent bridge monitoring controls."""
from telegram import Update
from telegram.ext import ContextTypes
import database.mongo as db


async def boton_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable bridge monitoring without changing saved mappings."""
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    if not manager.is_hosted(user_id):
        await update.message.reply_text(
            "⚠️ You need a hosted account first. Use /host to set one up."
        )
        return

    await db.set_setting(user_id, "bot_enabled", True)
    userbot = manager.get_client(user_id)
    mappings = await db.get_target_mappings(user_id)
    if userbot is not None and mappings:
        await userbot.enable_monitoring()
    await update.message.reply_text(
        f"🟢 Bot enabled. Monitoring {len(mappings)} target mapping(s)."
        if mappings
        else "🟢 Bot enabled. Add a mapping with /targetadd to start monitoring."
    )


async def botoff_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable bridge monitoring while keeping saved mappings intact."""
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    if not manager.is_hosted(user_id):
        await update.message.reply_text(
            "⚠️ You need a hosted account first. Use /host to set one up."
        )
        return

    await db.set_setting(user_id, "bot_enabled", False)
    userbot = manager.get_client(user_id)
    if userbot is not None:
        await userbot.disable_monitoring()
    else:
        await db.clear_monitoring_data(user_id)
    await update.message.reply_text(
        "🔴 Bot disabled. Mappings safe hain aur /boton ke baad resume honge."
    )