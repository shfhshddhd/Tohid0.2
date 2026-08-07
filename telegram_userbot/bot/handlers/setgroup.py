"""
/setgroup <chat_id> — set the one active group for the setgroup auto-reply feature.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
import database.mongo as db

logger = logging.getLogger(__name__)


async def setgroup_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]

    if not manager.is_hosted(user_id):
        await update.message.reply_text(
            "⚠️ You need a hosted account first. Use /host to set one up."
        )
        return

    args = ctx.args or []
    if not args:
        current = await db.get_active_group(user_id)
        if current:
            await update.message.reply_text(
                f"📌 Active group is currently set to: <code>{current}</code>\n\n"
                "Usage: /setgroup &lt;chat_id&gt;\n"
                "Example: /setgroup -1001234567890",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "📌 No active group is set.\n\n"
                "Usage: /setgroup &lt;chat_id&gt;\n"
                "Example: /setgroup -1001234567890",
                parse_mode="HTML",
            )
        return

    raw = args[0].strip()
    try:
        chat_id = int(raw)
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid chat_id. It must be a number.\n"
            "Example: /setgroup -1001234567890"
        )
        return

    # Verify the hosted account can actually see this group
    uc = manager.get_client(user_id)
    group_title = None
    if uc is not None:
        try:
            entity = await uc.client.get_entity(chat_id)
            group_title = getattr(entity, "title", None) or str(chat_id)
        except Exception as exc:
            logger.warning("Could not resolve chat_id %s for user %s: %s", chat_id, user_id, exc)
            await update.message.reply_text(
                f"❌ Could not find a group with ID <code>{chat_id}</code>.\n"
                "Make sure your hosted account is a member of that group.",
                parse_mode="HTML",
            )
            return

    previous_group = await db.get_active_group(user_id)
    if previous_group != chat_id:
        uc = manager.get_client(user_id)
        if uc is not None:
            if previous_group is not None:
                await uc.clear_group_monitoring(previous_group)
        else:
            # No live client is available, but stale records must still be
            # removed before the new group becomes active.
            if previous_group is not None:
                await db.clear_monitoring_data(
                    user_id,
                    group_chat_id=previous_group,
                )
        await db.set_active_group(user_id, chat_id)

    await update.message.reply_text(
        f"✅ <b>Active group set!</b>\n\n"
        f"Group: <b>{group_title}</b>\n"
        f"Chat ID: <code>{chat_id}</code>\n\n"
        "From now on:\n"
        "• Only messages from your targets <b>in this group</b> will be tracked.\n"
        "• Target messages will be copied to your <b>Saved Messages</b>.\n"
        "• When you reply to a copied message in Saved Messages, your reply is "
        "automatically sent to this group.",
        parse_mode="HTML",
    )
