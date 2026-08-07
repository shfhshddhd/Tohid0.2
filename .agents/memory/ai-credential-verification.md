---
name: AI credential verification
description: Runtime verification requirement for Telegram AI reply credentials
---

AI mode startup only confirms that the listener is registered; it does not validate the provider credential. A valid-looking saved secret may still produce authentication failures on the first real mention.

**Why:** The bot reported AI mode enabled while every generated reply failed at the provider boundary with an authentication error.

**How to apply:** After changing the provider secret, restart the bot, confirm the AI listener loads, and verify one real mention produces a reply before declaring AI mode fixed. Never expose credentials in chat; use the secure secret flow.