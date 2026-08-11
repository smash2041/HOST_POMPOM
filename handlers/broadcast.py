"""
VIP Access Bot — Broadcast Handler
Sends messages to all non-banned users with rate limiting.
Telegram limit: ~30 msgs/sec, we use 25/sec for safety.
"""
import asyncio
import logging
from telegram.ext import ContextTypes

from utils.bot_helpers import get_db

logger = logging.getLogger(__name__)


async def broadcast_message(
    context: ContextTypes.DEFAULT_TYPE,
    text: str = None,
    from_message=None,
) -> tuple:
    """
    Broadcast a message to all non-banned users.

    Args:
        context: Bot context for sending messages
        text: Text message to send (if from_message is None)
        from_message: Message object to copy/forward

    Returns:
        Tuple of (success_count, failed_count)
    """
    db = get_db(context)
    user_ids = await db.get_all_user_ids()
    success = 0
    failed = 0

    for user_id in user_ids:
        try:
            if from_message:
                await from_message.copy(chat_id=user_id)
            elif text:
                await context.bot.send_message(
                    chat_id=user_id, text=text, parse_mode="Markdown"
                )
            success += 1
        except Exception as e:
            logger.debug(f"Broadcast failed for user {user_id}: {e}")
            failed += 1

        # Rate limiting: ~25 messages per second (Telegram limit is 30/sec)
        if (success + failed) % 25 == 0:
            await asyncio.sleep(1.1)

    logger.info(
        f"Broadcast complete: {success} sent, {failed} failed"
    )
    return success, failed
