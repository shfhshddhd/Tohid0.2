---
name: AI provider selection
description: Provider selection for Telegram AI replies
---

Telegram AI replies use only the Gemini provider and its secure secret. OpenAI is intentionally not part of the runtime path.

**Why:** The user explicitly chose Gemini-only operation after Gemini quota errors and an invalid OpenAI credential caused failed replies.

**How to apply:** Keep the Gemini secret out of URLs and chat, verify the userbot after restart, and remember that Gemini quota or rate limits can still prevent replies.