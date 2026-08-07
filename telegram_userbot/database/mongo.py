import logging
import motor.motor_asyncio
from config import MONGO_URI

logger = logging.getLogger(__name__)

_client: motor.motor_asyncio.AsyncIOMotorClient | None = None
_db: motor.motor_asyncio.AsyncIOMotorDatabase | None = None


async def connect() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    global _client, _db
    _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    _db = _client["telegram_userbot"]
    # Create indexes
    await _db.users.create_index("user_id", unique=True)
    # Per-group latest-message index: (user_id, target_id, chat_id) is the unique key
    await _db.group_messages.create_index(
        [("user_id", 1), ("target_id", 1), ("chat_id", 1)],
        unique=True,
    )
    # Saved Messages reply bridge: maps a forwarded message back to its source.
    await _db.setgroup_map.create_index(
        [("user_id", 1), ("saved_msg_id", 1)],
        unique=True,
    )
    await _db.setgroup_map.create_index(
        [("user_id", 1), ("forwarded_message_id", 1)]
    )
    await _db.setgroup_map.create_index(
        [("user_id", 1), ("forwarded_saved_message_id", 1)]
    )
    logger.info("Connected to MongoDB.")
    return _db


def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not connected. Call connect() first.")
    return _db


# ── User record ────────────────────────────────────────────────────────────────

async def get_user(user_id: int) -> dict | None:
    return await get_db().users.find_one({"user_id": user_id})


async def upsert_user(user_id: int, data: dict) -> None:
    await get_db().users.update_one(
        {"user_id": user_id},
        {"$set": data},
        upsert=True,
    )


async def delete_user(user_id: int) -> None:
    await get_db().users.delete_one({"user_id": user_id})


# ── Session ────────────────────────────────────────────────────────────────────

async def save_session(user_id: int, session_string: str) -> None:
    await upsert_user(user_id, {"session_string": session_string, "active": True})


async def get_session(user_id: int) -> str | None:
    user = await get_user(user_id)
    return user.get("session_string") if user else None


# ── Settings ───────────────────────────────────────────────────────────────────

async def get_setting(user_id: int, key: str, default=None):
    user = await get_user(user_id)
    return user.get(key, default) if user else default


async def set_setting(user_id: int, key: str, value) -> None:
    await upsert_user(user_id, {key: value})


# ── Targets ────────────────────────────────────────────────────────────────────

async def get_targets(user_id: int) -> list[dict]:
    user = await get_user(user_id)
    return user.get("targets", []) if user else []


async def add_target(user_id: int, target: dict) -> bool:
    """Add a target if not already present. Returns True if added, False if duplicate."""
    user = await get_user(user_id)
    existing = user.get("targets", []) if user else []
    for t in existing:
        if t["target_id"] == target["target_id"]:
            return False
    await get_db().users.update_one(
        {"user_id": user_id},
        {"$push": {"targets": target}},
        upsert=True,
    )
    return True


async def remove_target(
    user_id: int,
    identifier: str,
    resolved_target_id: int | None = None,
) -> bool:
    """Remove target by username (with or without @) or resolved numeric ID."""
    user = await get_user(user_id)
    if not user:
        return False
    targets = user.get("targets", [])
    ident = identifier.lstrip("@").lower()
    new_targets = [
        t for t in targets
        if not (
            (
                resolved_target_id is not None
                and t.get("target_id") == resolved_target_id
            )
            or (
                resolved_target_id is None
                and (
                    str(t.get("target_id")) == ident
                    or (t.get("username") or "").lower() == ident
                )
            )
        )
    ]
    if len(new_targets) == len(targets):
        return False
    await get_db().users.update_one(
        {"user_id": user_id},
        {"$set": {"targets": new_targets}},
    )
    return True


async def clear_targets(user_id: int) -> None:
    await upsert_user(user_id, {"targets": []})


async def get_target(user_id: int, target_id: int) -> dict | None:
    """Return one stored target by its stable Telegram user ID."""
    targets = await get_targets(user_id)
    return next((t for t in targets if t.get("target_id") == target_id), None)


async def update_target_last_message(
    user_id: int, target_id: int, chat_id: int, message_id: int
) -> None:
    """
    Store the latest message from a target user in a specific group.
    Keyed by (user_id, target_id, chat_id) so each group is tracked independently.
    """
    await get_db().group_messages.update_one(
        {"user_id": user_id, "target_id": target_id, "chat_id": chat_id},
        {"$set": {"message_id": message_id}},
        upsert=True,
    )


async def get_target_message_in_chat(
    user_id: int, target_id: int, chat_id: int
) -> int | None:
    """
    Return the latest message_id from target_id in chat_id, or None if not seen yet.
    """
    doc = await get_db().group_messages.find_one(
        {"user_id": user_id, "target_id": target_id, "chat_id": chat_id}
    )
    return doc["message_id"] if doc else None


async def get_latest_target_in_chat(
    user_id: int, chat_id: int
) -> tuple[dict, int] | None:
    """
    Return the active target with the newest tracked message in one group.

    Target membership is read from the user's current target list, so removed
    targets cannot be selected even if an old group_messages record remains.
    Telegram message IDs increase within a chat and therefore provide the
    existing latest-message ordering without changing the stored schema.
    """
    targets = await get_targets(user_id)
    target_by_id = {
        int(target["target_id"]): target
        for target in targets
        if target.get("target_id") is not None
    }
    if not target_by_id:
        return None

    doc = await get_db().group_messages.find_one(
        {
            "user_id": user_id,
            "chat_id": chat_id,
            "target_id": {"$in": list(target_by_id)},
        },
        sort=[("message_id", -1)],
    )
    if not doc or doc.get("message_id") is None:
        return None

    target = target_by_id.get(int(doc["target_id"]))
    if target is None:
        return None
    return target, int(doc["message_id"])


# ── SetGroup ───────────────────────────────────────────────────────────────────

async def set_active_group(user_id: int, chat_id: int) -> None:
    """Persist the one active group for the setgroup feature."""
    await upsert_user(user_id, {"active_group": chat_id})


async def get_active_group(user_id: int) -> int | None:
    """Return the active group chat_id, or None if not set."""
    user = await get_user(user_id)
    return user.get("active_group") if user else None


async def clear_active_group(user_id: int) -> None:
    """Remove the active group so a later target add cannot reuse it."""
    await get_db().users.update_one(
        {"user_id": user_id},
        {"$unset": {"active_group": ""}},
    )


async def save_setgroup_mapping(
    user_id: int,
    original_chat_id: int,
    original_message_id: int,
    forwarded_message_id: int,
    target_id: int,
    active_group_id: int,
) -> None:
    """
    Store the complete Saved Messages reply bridge mapping.
    """
    await get_db().setgroup_map.update_one(
        {"user_id": user_id, "saved_msg_id": forwarded_message_id},
        {"$set": {
            "original_chat_id": original_chat_id,
            "original_message_id": original_message_id,
            "forwarded_saved_message_id": forwarded_message_id,
            "target_user_id": target_id,
            "active_group_id": active_group_id,
            "reply_sent": False,
            # Legacy aliases retained for existing records and indexes.
            "forwarded_message_id": forwarded_message_id,
            "saved_msg_id": forwarded_message_id,
            "target_id": target_id,
        }},
        upsert=True,
    )


async def get_setgroup_mapping(user_id: int, saved_msg_id: int) -> dict | None:
    """
    Return a normalized mapping for a Saved Messages message, or None.

    The legacy field names are accepted so an existing database does not break
    while old mappings are being cleaned up.
    """
    collection = get_db().setgroup_map
    doc = await collection.find_one(
        {
            "user_id": user_id,
            "$or": [
                {"forwarded_saved_message_id": saved_msg_id},
                {"forwarded_message_id": saved_msg_id},
                {"saved_msg_id": saved_msg_id},
            ],
        },
        {"_id": 0},
    )
    if not doc:
        return None
    return {
        "original_chat_id": doc.get("original_chat_id", doc.get("group_chat_id")),
        "original_message_id": doc.get(
            "original_message_id", doc.get("group_msg_id")
        ),
        "forwarded_saved_message_id": doc.get(
            "forwarded_saved_message_id",
            doc.get("forwarded_message_id", doc.get("saved_msg_id", saved_msg_id)),
        ),
        "target_user_id": doc.get("target_user_id", doc.get("target_id")),
        "active_group_id": doc.get(
            "active_group_id", doc.get("group_chat_id")
        ),
        "reply_sent": bool(doc.get("reply_sent", False)),
    }


async def get_setgroup_mappings(user_id: int) -> list[dict]:
    """Load all Saved Messages bridge mappings for a hosted user."""
    cursor = get_db().setgroup_map.find(
        {"user_id": user_id},
        {"_id": 0},
    )
    docs = await cursor.to_list(length=None)
    mappings: list[dict] = []
    for doc in docs:
        forwarded_id = doc.get(
            "forwarded_saved_message_id",
            doc.get("forwarded_message_id", doc.get("saved_msg_id")),
        )
        original_chat_id = doc.get("original_chat_id", doc.get("group_chat_id"))
        original_message_id = doc.get(
            "original_message_id", doc.get("group_msg_id")
        )
        if (
            forwarded_id is None
            or original_chat_id is None
            or original_message_id is None
            or doc.get("target_user_id", doc.get("target_id")) is None
        ):
            continue
        mappings.append(
            {
                "original_chat_id": original_chat_id,
                "original_message_id": original_message_id,
                "forwarded_saved_message_id": forwarded_id,
                "target_user_id": doc.get("target_user_id", doc.get("target_id")),
                "active_group_id": doc.get(
                    "active_group_id", doc.get("group_chat_id")
                ),
                "reply_sent": bool(doc.get("reply_sent", False)),
            }
        )
    return mappings


async def mark_setgroup_reply_sent(
    user_id: int,
    forwarded_saved_message_id: int,
    reply_message_id: int,
) -> None:
    """Persist that a Saved Messages reply was already bridged successfully."""
    await get_db().setgroup_map.update_one(
        {
            "user_id": user_id,
            "$or": [
                {"forwarded_saved_message_id": forwarded_saved_message_id},
                {"forwarded_message_id": forwarded_saved_message_id},
                {"saved_msg_id": forwarded_saved_message_id},
            ],
        },
        {
            "$set": {
                "reply_sent": True,
                "reply_message_id": reply_message_id,
            }
        },
    )


async def clear_monitoring_data(
    user_id: int,
    target_id: int | None = None,
    group_chat_id: int | None = None,
) -> list[int]:
    """
    Remove cached target messages and Saved Messages bridge mappings.

    Returns forwarded Saved Messages IDs so the caller can remove the
    corresponding messages from Saved Messages as well.
    """
    target_filter: dict = {"user_id": user_id}
    if target_id is not None:
        target_filter["target_id"] = target_id

    await get_db().group_messages.delete_many(target_filter | (
        {"chat_id": group_chat_id} if group_chat_id is not None else {}
    ))

    mapping_filter: dict = {"user_id": user_id}
    if target_id is not None:
        mapping_filter["target_id"] = target_id
    if group_chat_id is not None:
        mapping_filter["$or"] = [
            {"active_group_id": group_chat_id},
            {"original_chat_id": group_chat_id},
            {"group_chat_id": group_chat_id},
        ]

    cursor = get_db().setgroup_map.find(
        mapping_filter,
        {
            "_id": 0,
            "forwarded_saved_message_id": 1,
            "forwarded_message_id": 1,
            "saved_msg_id": 1,
        },
    )
    docs = await cursor.to_list(length=None)
    forwarded_ids = [
        int(
            doc["forwarded_saved_message_id"]
            if doc.get("forwarded_saved_message_id") is not None
            else (
                doc["forwarded_message_id"]
                if doc.get("forwarded_message_id") is not None
                else doc["saved_msg_id"]
            )
        )
        for doc in docs
        if doc.get("forwarded_saved_message_id") is not None
        or doc.get("forwarded_message_id") is not None
        or doc.get("saved_msg_id") is not None
    ]
    await get_db().setgroup_map.delete_many(mapping_filter)
    return forwarded_ids


# ── Bulk load ──────────────────────────────────────────────────────────────────

async def get_all_active_users() -> list[dict]:
    """Return all users with an active session."""
    cursor = get_db().users.find({"active": True, "session_string": {"$exists": True}})
    return await cursor.to_list(length=None)
