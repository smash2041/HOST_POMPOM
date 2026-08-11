"""
VIP Access Bot — Multi-Bot Helper Utilities
Provides dynamic db/owner access via context.bot_data.
All handlers use these helpers instead of direct global imports.
"""


def get_db(context):
    """Get the database instance for the current bot from context."""
    return context.bot_data["db"]


def get_owner_ids(context):
    """Get the set of owner IDs for the current bot from context."""
    return context.bot_data["owner_ids"]


def is_main_bot(context):
    """Check if the current bot is the main (hosting) bot."""
    return context.bot_data.get("is_main_bot", False)


def get_main_db(context):
    """Get the main bot's database instance (for global settings)."""
    return context.bot_data.get("main_db") or context.bot_data["db"]


async def get_forward_protection(context):
    """
    Read forward_protection setting from the MAIN bot's database.
    This is a GLOBAL setting controlled only by OWNER_1 on the main bot.
    """
    main_db = get_main_db(context)
    result = await main_db.get_setting("forward_protection")
    return result == "1"


async def get_auto_disappear(context):
    """
    Read auto_disappear setting from the MAIN bot's database.
    This is a GLOBAL setting controlled only by OWNER_1 on the main bot.
    """
    main_db = get_main_db(context)
    result = await main_db.get_setting("auto_disappear")
    return result == "1"
