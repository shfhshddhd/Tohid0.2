---
name: AI provider fallback
description: Provider selection for Telegram AI replies
---

When both provider secrets exist, the Telegram AI reply path prefers Gemini and only uses OpenAI when Gemini is unavailable.

**Why:** The previously configured OpenAI credential was rejected, while the user had a Gemini key available.

**How to apply:** Keep provider selection behind configuration, never expose secrets in URLs or chat, and verify the first real mention after restarting the userbot.