# Telegram Asset Manager

A Telegram bot that lets users host their own Telegram account, track selected targets in groups, and automate replies through Saved Messages.

## Run & Operate

- `.pythonlibs/bin/python telegram_userbot/main.py` — run the Telegram bot
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required secrets: `BOT_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `MONGO_URI`

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `telegram_userbot/main.py` — bot entrypoint and polling lifecycle
- `telegram_userbot/bot/handlers/` — Telegram command handlers
- `telegram_userbot/userbot/` — hosted-account Telethon clients and manager
- `telegram_userbot/database/mongo.py` — MongoDB persistence and indexes
- `telegram_userbot/config.py` — required environment settings and validation
- `lib/api-spec/openapi.yaml` — API contract for the shared API service

## Architecture decisions

- The bot account uses `python-telegram-bot`, while hosted user accounts use Telethon.
- Hosted-account sessions and asset metadata are stored in MongoDB.
- Telegram and database credentials are supplied as Replit Secrets rather than committed to the repository.

## Product

- Users can connect and remove their hosted Telegram account.
- Users can add, list, and remove target accounts.
- Users can enable automatic target replies and configure a focused group.
- Saved Messages replies can be bridged back into the configured group.

## User preferences

- The user wants the Telegram bot restored and running.

## Gotchas

- The bot cannot start until all four required secrets are present.
- The Telegram API ID and hash are for the Telegram client API; the bot token is separate.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
