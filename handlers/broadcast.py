import asyncio
import logging
from utils.bot_helpers import get_db

logger = logging.getLogger(__name__)

async def broadcast_message(context, text=None, from_message=None) -> tuple:
    if not text and not from_message:
        return 0, 0
    
    db = get_db(context)
    user_ids = await db.get_all_user_ids()
    success = 0
    failed = 0
    for user_id in user_ids:
        try:
            if from_message:
                await from_message.copy(chat_id=user_id)
            elif text:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
            success += 1
        except Exception as e:
            logger.debug(f'Broadcast failed for user {user_id}: {e}')
            failed += 1
        if (success + failed) % 25 == 0:
            await asyncio.sleep(1.1)
    return success, failed
