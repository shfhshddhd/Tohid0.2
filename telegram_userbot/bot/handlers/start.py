"""
/start and /allcommands handlers.
"""
from telegram import Update
from telegram.ext import ContextTypes

MENU_TEXT = """👋 <b>Welcome to the Multi-User Telegram Userbot Manager!</b>

This bot lets you host your own Telegram account as a userbot, copy mapped target messages to Saved Messages, and bridge replies back into groups.

━━━━━━━━━━━━━━━━━━━━━━
📋 <b>ALL AVAILABLE COMMANDS</b>
━━━━━━━━━━━━━━━━━━━━━━

<b>🔐 Account Management</b>
/host — Connect and host your Telegram account
/unhost — Remove your hosted account and stop the userbot

<b>🎯 Target Management</b>
/targetadd &lt;group_chat_id&gt; &lt;@username_or_user_id&gt; — Map a target to a group
/targetremove &lt;group_chat_id&gt; &lt;@username_or_user_id&gt; — Remove a mapping

<b>ℹ️ General</b>
/start — Show this welcome message
/allcommands — Show this command list
/cancel — Cancel any in-progress operation

━━━━━━━━━━━━━━━━━━━━━━
<b>💡 How the bridge works:</b>
When a mapped target sends a message in its group, it is copied to Saved Messages. Send a message in Saved Messages to reply to that target's latest mapped group message.
━━━━━━━━━━━━━━━━━━━━━━"""


async def start_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(MENU_TEXT)


async def allcommands_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(MENU_TEXT)
