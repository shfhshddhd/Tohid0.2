"""AI reply mode controls."""
from telegram import Update
from telegram.ext import ContextTypes
import database.mongo as db


async def aimodeon_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Enable delayed AI replies to mentions in group chats."""
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    if not manager.is_hosted(user_id):
        await update.message.reply_text(
            "⚠️ You need a hosted account first. Use /host to set one up."
        )
        return

    await db.set_setting(user_id, "ai_mode", True)
    userbot = manager.get_client(user_id)
    if userbot is not None:
        userbot.enable_ai_mode()
    await update.message.reply_text(
        "🟢 AI mode ON. I will reply to group mentions after a short delay."
    )


async def aimodeoff_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Disable AI replies while keeping conversation memory."""
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    if not manager.is_hosted(user_id):
        await update.message.reply_text(
            "⚠️ You need a hosted account first. Use /host to set one up."
        )
        return

    await db.set_setting(user_id, "ai_mode", False)
    userbot = manager.get_client(user_id)
    if userbot is not None:
        await userbot.disable_ai_mode()
    await update.message.reply_text(
        "🔴 AI mode OFF. Saved conversation memory is unchanged."
    )