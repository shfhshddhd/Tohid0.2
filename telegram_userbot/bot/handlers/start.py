"""
/start and /allcommands handlers.
"""
from telegram import Update
from telegram.ext import ContextTypes

MENU_TEXT = """👋 <b>Welcome to the Multi-User Telegram Userbot Manager!</b>

This bot lets you host your own Telegram account as a userbot and automatically reply to target users in groups.

━━━━━━━━━━━━━━━━━━━━━━
📋 <b>ALL AVAILABLE COMMANDS</b>
━━━━━━━━━━━━━━━━━━━━━━

<b>🔐 Account Management</b>
/host — Connect and host your Telegram account
/unhost — Remove your hosted account and stop the userbot

<b>🎯 Target Management</b>
/target add &lt;username or user_id&gt; — Add a user to your target list
/target remove &lt;username or user_id&gt; — Remove a user from your target list
/target list — View all your saved targets (name, username, user ID)
/target removeall — Delete all targets (asks for confirmation)

<b>⚡ AutoTag</b>
/autotag on — Enable automatic replies to all targets
/autotag off — Disable automatic replies

<b>📌 SetGroup</b>
/setgroup &lt;chat_id&gt; — Set one specific group for focused auto-reply
↳ Target messages in that group are copied to Saved Messages
↳ Reply in Saved Messages → reply is sent to the group automatically

<b>ℹ️ General</b>
/start — Show this welcome message
/allcommands — Show this command list
/cancel — Cancel any in-progress operation

━━━━━━━━━━━━━━━━━━━━━━
<b>💡 How AutoTag works:</b>
When enabled, every text message you send in a group is automatically sent as a reply to each target user's latest message in that same group. Your original message is deleted — only the reply remains.
━━━━━━━━━━━━━━━━━━━━━━"""


async def start_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(MENU_TEXT)


async def allcommands_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(MENU_TEXT)
