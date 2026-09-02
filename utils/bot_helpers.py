"""
Host Bot — Multi-Bot Helper Utilities
Mirror architecture with per-bot override:
  - Products: per-bot if customized, else fallback to main DB
  - Settings: per-bot if customized, else fallback to main DB
  - Forward Protection & Auto Disappear: GLOBAL (main DB only)
"""
from config import UPI_ID


def get_db(context):
    """Get the per-bot database instance (prefixed for hosted bots)."""
    return context.bot_data["db"]


def get_main_db(context):
    """Get the main bot's database instance (for products and fallback settings)."""
    return context.bot_data.get("main_db") or context.bot_data["db"]


def get_owner_ids(context):
    """Get the set of owner IDs for the current bot from context."""
    return context.bot_data["owner_ids"]


def is_main_bot(context):
    """Check if the current bot is the main (hosting) bot."""
    return context.bot_data.get("is_main_bot", False)


def get_bot_id(context):
    """Get the hosted bot ID (empty string for main bot)."""
    return context.bot_data.get("bot_id", "")


async def get_setting_with_fallback(context, key):
    """
    Get a setting value with fallback logic:
    1. Check per-bot DB (hosted bot's own settings)
    2. If empty/None, fallback to main DB settings
    
    For main bot, both are the same DB so no fallback needed.
    """
    db = get_db(context)
    value = await db.get_setting(key)
    if value is not None and value != "":
        return value

    # Fallback to main DB (only matters for hosted bots)
    main_db = get_main_db(context)
    if main_db is not db:
        return await main_db.get_setting(key)

    return value


async def get_upi_id(context):
    """
    Get UPI ID: per-bot setting → main setting → env variable.
    """
    value = await get_setting_with_fallback(context, "upi_id")
    if value:
        return value
    return UPI_ID


async def get_products_db(context):
    """
    Get the DB to read products from:
    1. If hosted bot has its own products → use per-bot DB
    2. If hosted bot has NO products → fall back to main DB (mirror)
    3. Main bot → always uses main DB
    """
    db = get_db(context)
    main_db = get_main_db(context)
    if db is main_db:
        return main_db  # Main bot — no fallback needed

    # Hosted bot: check if it has its own products
    count = await db.get_product_count()
    if count > 0:
        return db  # Hosted bot has customized products
    return main_db  # Fall back to main bot products (mirror)


async def get_qr_image_id(context):
    """
    Get custom QR image ID: per-bot setting → main setting.
    """
    return await get_setting_with_fallback(context, "qr_image_id") or ""


async def get_forward_protection(context):
    """Forward protection — GLOBAL (always from main DB)."""
    main_db = get_main_db(context)
    result = await main_db.get_setting("forward_protection")
    return result == "1"


async def get_auto_disappear(context):
    """Auto disappear — GLOBAL (always from main DB)."""
    main_db = get_main_db(context)
    result = await main_db.get_setting("auto_disappear")
    return result == "1"
