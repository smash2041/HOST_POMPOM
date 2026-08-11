"""
Access control decorators for the bot.
"""
from functools import wraps
from utils.bot_helpers import get_owner_ids


def owner_only(func):
    """
    Decorator to restrict handler access to bot owners only.
    Silently ignores non-owner access (no error message sent).
    Uses dynamic owner_ids from context.bot_data.
    """
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id not in get_owner_ids(context):
            return  # Silent rejection — no trace
        return await func(update, context, *args, **kwargs)
    return wrapper
