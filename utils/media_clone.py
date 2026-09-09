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
    Uses in-memory cache first (instant), then DB cache, then auto-clone.
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

    # 1. Check in-memory cache FIRST (instant — no DB query)
    mem_cache = context.bot_data.get("_media_cache", {})
    if file_id in mem_cache:
        cached_id = mem_cache[file_id]
        if cached_id and cached_id != file_id:
            return f"{prefix}{cached_id}"

    # 2. Check DB cache (fallback)
    cached_id = await db.get_cached_media(file_id)
    if cached_id and cached_id != file_id:
        # Store in memory for future instant lookups
        context.bot_data.setdefault("_media_cache", {})[file_id] = cached_id
        return f"{prefix}{cached_id}"

    # 3. Try Auto-Clone from Main Bot
    main_bot = context.bot_data.get("main_bot")
    if not main_bot:
        # No main bot reference — return as-is, don't cache
        return raw_id_with_prefix

    try:
        # Check if main bot owns this file
        try:
            tg_file = await main_bot.get_file(file_id)
        except Exception as e:
            # Could be native file or transient error — don't cache, retry next time
            logger.warning(f"CLONE FAIL [get_file] {file_id[:25]}...: {e}")
            return raw_id_with_prefix

        # Use dedicated dump_chat_id (admin's chat)
        dump_chat_id = context.bot_data.get("dump_chat_id")
        if not dump_chat_id:
            owner_list = sorted(get_owner_ids(context))
            dump_chat_id = owner_list[0] if owner_list else None

        if not dump_chat_id:
            logger.warning(f"CLONE FAIL [no dump_chat_id] {file_id[:25]}...")
            return raw_id_with_prefix

        msg = None
        new_file_id = None

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            temp_path = tmp_file.name

        try:
            await tg_file.download_to_drive(temp_path)
            file_size = os.path.getsize(temp_path)
            logger.info(f"Downloaded {file_id[:20]}... ({file_size} bytes), uploading to {dump_chat_id}")
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
        except Exception as upload_err:
            logger.warning(f"CLONE FAIL [upload] {file_id[:25]}... to chat {dump_chat_id}: {upload_err}")
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

        # Instantly delete the dump message
        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

        if new_file_id:
            await db.cache_media(file_id, new_file_id)
            # Also cache in memory for instant future lookups
            context.bot_data.setdefault("_media_cache", {})[file_id] = new_file_id
            logger.info(f"CLONE OK {file_id[:20]}... → {new_file_id[:20]}...")
            return f"{prefix}{new_file_id}"
        else:
            logger.warning(f"CLONE FAIL [no new_file_id] {file_id[:25]}... msg={msg is not None}")
            return raw_id_with_prefix

    except Exception as e:
        logger.warning(f"CLONE FAIL [exception] {file_id[:20]}...: {e}")
        # DON'T cache failed clone — will retry on next request
        return raw_id_with_prefix
