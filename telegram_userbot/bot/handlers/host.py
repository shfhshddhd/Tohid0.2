"""
/host and /unhost command handlers.

/host flow:
  1. Ask for phone number  → PHONE
  2. Send OTP              → OTP
  3. [Optional] 2FA pass   → PASSWORD
  4. Done
"""
import logging
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logger = logging.getLogger(__name__)

PHONE, OTP, PASSWORD = range(3)

# Temporary auth state keyed by bot user_id
# {user_id: {"client": TelegramClient, "phone": str, "phone_code_hash": str}}
_pending: dict[int, dict] = {}


# ── /host ──────────────────────────────────────────────────────────────────────

async def host_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]

    if manager.is_hosted(user_id):
        await update.message.reply_text(
            "✅ You already have an active hosted account.\n"
            "Use /unhost first if you want to replace it."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📱 Please send your phone number in international format.\n"
        "Example: +14155552671\n\n"
        "Send /cancel at any time to abort."
    )
    return PHONE


async def host_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    phone = update.message.text.strip()

    await update.message.reply_text("⏳ Sending verification code…")
    try:
        client, phone_code_hash = await manager.begin_auth(user_id, phone)
    except Exception as exc:
        logger.error("begin_auth failed for %s: %s", user_id, exc)
        await update.message.reply_text(f"❌ Could not send OTP: {exc}\nTry /host again.")
        return ConversationHandler.END

    _pending[user_id] = {"client": client, "phone": phone, "phone_code_hash": phone_code_hash}
    await update.message.reply_text(
        "✉️ A verification code has been sent to your Telegram account.\n"
        "Please enter your OTP with spaces between each digit.\n"
        "Example: 1 2 3 4 5."
    )
    return OTP


async def host_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    otp = update.message.text.strip()

    pending = _pending.get(user_id)
    if not pending:
        await update.message.reply_text("❌ Session expired. Please start over with /host.")
        return ConversationHandler.END

    try:
        await manager.sign_in_with_code(
            user_id=user_id,
            client=pending["client"],
            phone=pending["phone"],
            phone_code_hash=pending["phone_code_hash"],
            otp=otp,
        )
        _pending.pop(user_id, None)
        await update.message.reply_text(
            "🎉 Your account has been hosted successfully!\n\n"
            "Use /targetadd <group_chat_id> <@username_or_user_id> to create a mapping."
        )
        return ConversationHandler.END

    except SessionPasswordNeededError:
        # Keep the client alive in _pending so we can use it for 2FA
        await update.message.reply_text(
            "🔒 Your account has Two-Step Verification enabled.\n"
            "Please enter your 2FA password:"
        )
        return PASSWORD

    except PhoneCodeInvalidError:
        await update.message.reply_text("❌ Invalid code. Please try again:")
        return OTP

    except Exception as exc:
        logger.exception("sign_in error for %s: %s", user_id, exc)
        _pending.pop(user_id, None)
        await update.message.reply_text(f"❌ Authentication failed: {exc}\nTry /host again.")
        return ConversationHandler.END


async def host_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]
    password = update.message.text.strip()

    pending = _pending.get(user_id)
    if not pending:
        await update.message.reply_text("❌ Session expired. Please start over with /host.")
        return ConversationHandler.END

    try:
        await manager.sign_in_with_password(
            user_id=user_id,
            client=pending["client"],
            password=password,
        )
        _pending.pop(user_id, None)
        await update.message.reply_text(
            "🎉 Your account has been hosted successfully!\n\n"
            "Use /targetadd <group_chat_id> <@username_or_user_id> to create a mapping."
        )
    except Exception as exc:
        logger.exception("2FA error for %s: %s", user_id, exc)
        _pending.pop(user_id, None)
        await update.message.reply_text(f"❌ 2FA failed: {exc}\nTry /host again.")

    return ConversationHandler.END


async def host_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    pending = _pending.pop(user_id, None)
    if pending:
        try:
            await pending["client"].disconnect()
        except Exception:
            pass
    await update.message.reply_text("❎ /host cancelled.")
    return ConversationHandler.END


def build_host_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("host", host_start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_phone)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_otp)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_password)],
        },
        fallbacks=[CommandHandler("cancel", host_cancel)],
        allow_reentry=True,
    )


# ── /unhost ────────────────────────────────────────────────────────────────────

async def unhost_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    manager = ctx.bot_data["manager"]

    if not manager.is_hosted(user_id):
        await update.message.reply_text("ℹ️ You don't have an active hosted account.")
        return

    await manager.remove_session(user_id)
    await update.message.reply_text(
        "🗑️ Your hosted account has been removed.\n"
        "Session deleted, userbot stopped, all data cleared."
    )
