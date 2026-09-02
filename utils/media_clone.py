import logging
import tempfile
import os
import asyncio

from utils.bot_helpers import is_main_bot, get_db, get_owner_ids

logger = logging.getLogger(__name__)

# Timeout for uploading large videos (default 10s is too low)
UPLOAD_TIMEOUT = 120


async def get_translated_file_id(context, raw_id_with_prefix):
    """
    Auto-clones file_ids for hosted bots.
    If the file_id is foreign (from main bot), it downloads it via main_bot,
    silently uploads it via the hosted bot, and caches the new native file_id.
    """
    if not raw_id_with_prefix or raw_id_with_prefix == "[]":
        return raw_id_with_prefix

    if is_main_bot(context):
        return raw_id_with_prefix

    db = get_db(context)

    # Extract prefix (e.g. video:BAAC...)
    if raw_id_with_prefix.startswith("video:"):
        prefix, file_id = "video:", raw_id_with_prefix[6:]
    elif raw_id_with_prefix.startswith("photo:"):
        prefix, file_id = "photo:", raw_id_with_prefix[6:]
    else:
        prefix, file_id = "", raw_id_with_prefix

    # 1. Check Cache
    cached_id = await db.get_cached_media(file_id)
    if cached_id:
        return f"{prefix}{cached_id}"

    # 2. Check if Native
    try:
        await context.bot.get_file(file_id)
        await db.cache_media(file_id, file_id)
        return raw_id_with_prefix
    except Exception:
        pass  # Not native, proceed to clone

    # 3. Auto-Clone
    main_bot = context.bot_data.get("main_bot")
    if not main_bot:
        return raw_id_with_prefix

    # Retry up to 3 times for transient failures (timeout, 502, etc.)
    for attempt in range(3):
        try:
            logger.info(f"Auto-cloning media {file_id[:20]}... (attempt {attempt + 1})")
            tg_file = await main_bot.get_file(file_id)

            owner_list = list(get_owner_ids(context))
            dump_chat_id = owner_list[-1] if owner_list else None

            if not dump_chat_id:
                logger.error("No owner found for media dump.")
                return raw_id_with_prefix

            msg = None
            new_file_id = None

            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                temp_path = tmp_file.name

            try:
                await tg_file.download_to_drive(temp_path)
                with open(temp_path, 'rb') as f:
                    if prefix == "video:":
                        msg = await context.bot.send_video(
                            chat_id=dump_chat_id, video=f,
                            disable_notification=True,
                            write_timeout=UPLOAD_TIMEOUT,
                            read_timeout=UPLOAD_TIMEOUT,
                        )
                        new_file_id = msg.video.file_id if msg and msg.video else None
                    elif prefix == "photo:":
                        msg = await context.bot.send_photo(
                            chat_id=dump_chat_id, photo=f,
                            disable_notification=True,
                            write_timeout=UPLOAD_TIMEOUT,
                            read_timeout=UPLOAD_TIMEOUT,
                        )
                        new_file_id = msg.photo[-1].file_id if msg and msg.photo else None
                    else:
                        msg = await context.bot.send_document(
                            chat_id=dump_chat_id, document=f,
                            disable_notification=True,
                            write_timeout=UPLOAD_TIMEOUT,
                            read_timeout=UPLOAD_TIMEOUT,
                        )
                        if msg and msg.document:
                            new_file_id = msg.document.file_id
                        elif msg and msg.video:
                            new_file_id = msg.video.file_id
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            # Instantly delete the dump message
            if msg:
                try:
                    await msg.delete()
                except Exception:
                    pass

            if new_file_id:
                await db.cache_media(file_id, new_file_id)
                logger.info(f"Successfully cloned media {file_id[:20]}...")
                return f"{prefix}{new_file_id}"

            break  # Upload OK but no file_id — don't retry

        except Exception as e:
            logger.warning(f"Auto-clone attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(3)
            else:
                logger.error(f"Auto-clone gave up after 3 attempts for {file_id[:20]}...")

    return raw_id_with_prefix
