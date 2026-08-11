"""
Access control decorators for the bot.
"""
from functools import wraps
from config import OWNER_IDS


def owner_only(func):
    """
    Decorator to restrict handler access to bot owners only.
    Silently ignores non-owner access (no error message sent).
    Neither owner knows the other exists.
    """
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        user_id = update.effective_user.id if update.effective_user else None
        if user_id not in OWNER_IDS:
            return  # Silent rejection — no trace
        return await func(update, context, *args, **kwargs)
    return wrapper
