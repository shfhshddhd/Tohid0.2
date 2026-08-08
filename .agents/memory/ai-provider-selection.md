---
name: AI provider selection
description: Provider selection for Telegram AI replies
---

Telegram AI replies use only the Gemini provider and its secure secret. OpenAI is intentionally not part of the runtime path. Gemini model aliases may need updating when Google retires a model for new users.

**Why:** The user explicitly chose Gemini-only operation after Gemini quota errors and an invalid OpenAI credential caused failed replies. A later credential verification showed the configured `gemini-2.5-flash` model returned 404 while `gemini-flash-latest` worked.

**How to apply:** Keep the Gemini secret out of URLs and chat, use a currently available Gemini model, verify the userbot after restart, and remember that Gemini quota or rate limits can still prevent replies.