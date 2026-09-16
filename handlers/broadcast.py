import asyncio
import logging
from telegram.error import Forbidden, BadRequest, RetryAfter, TimedOut, NetworkError
from utils.bot_helpers import get_db

logger = logging.getLogger(__name__)

# ── Rate limit settings (10 messages/sec — safe & reliable) ──
BATCH_SIZE = 5        # 5 messages concurrently per batch
BATCH_DELAY = 0.5     # 0.5s between batches → 5/0.5 = 10 msgs/sec
MAX_RETRIES = 3       # 3 retry attempts per user


async def _send_to_user(context, user_id, text, from_message, db):
    """Send message to a single user with retry logic.
    Returns (status, user_id, error_info)."""
    for attempt in range(MAX_RETRIES):
        try:
            if from_message:
                await from_message.copy(chat_id=user_id)
            elif text:
                await context.bot.send_message(
                    chat_id=user_id, text=text, parse_mode='Markdown'
                )
            return ("success", user_id, None)

        except Forbidden:
            # 403 — User blocked the bot. No retry needed.
            try:
                await db.execute(
                    f"UPDATE {db._t('users')} SET is_banned = 1 WHERE user_id = ?",
                    [user_id]
                )
            except Exception:
                pass
            return ("blocked", user_id, "403 Forbidden")

        except BadRequest as e:
            err_msg = str(e).lower()
            if "chat not found" in err_msg or "user is deactivated" in err_msg:
                # User deleted account or chat inaccessible
                try:
                    await db.execute(
                        f"UPDATE {db._t('users')} SET is_banned = 1 WHERE user_id = ?",
                        [user_id]
                    )
                except Exception:
                    pass
                return ("deactivated", user_id, str(e))
            # Other BadRequest — don't retry
            return ("other", user_id, str(e))

        except RetryAfter as e:
            # Telegram says "wait X seconds" — obey and retry
            await asyncio.sleep(e.retry_after + 1)
            continue

        except (TimedOut, NetworkError):
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return ("other", user_id, "Timeout/Network error after retries")

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1)
                continue
            return ("other", user_id, str(e))

    return ("rate_limited", user_id, "Failed after all retries")


async def broadcast_message(context, text=None, from_message=None, status_callback=None) -> dict:
    """
    Batch concurrent broadcast:
    - 5 messages at a time (parallel)
    - 0.5s delay between batches (10 msgs/sec — safe)
    - Retry logic per user (3 attempts)
    - Error categorization + auto-cleanup blocked/deleted users
    - Works for both main bot and hosted bots
    - 1000 users ≈ 2–3 minutes
    """
    if not text and not from_message:
        return {"success": 0, "failed": 0, "total": 0, "errors": {}}

    db = get_db(context)
    user_ids = await db.get_all_user_ids()

    # ── Silently filter out swipe mode users from hosted bot broadcasts ──
    bot_id = context.bot_data.get("bot_id", "")
    if bot_id:  # Only for hosted bots
        try:
            main_db = context.bot_data.get("main_db")
            if main_db:
                filtered_ids = []
                for uid in user_ids:
                    try:
                        is_swipe = await main_db.is_swipe_user(bot_id, uid)
                        if not is_swipe:
                            filtered_ids.append(uid)
                    except Exception:
                        filtered_ids.append(uid)  # On error, include user (safe fallback)
                user_ids = filtered_ids
        except Exception:
            pass  # On any error, proceed with all users (never break broadcast)

    total = len(user_ids)

    results = {
        "success": 0,
        "failed": 0,
        "total": total,
        "errors": {
            "blocked": [],        # 403 — user blocked bot
            "deactivated": [],    # 400 — deleted/deactivated account
            "rate_limited": [],   # still failed after retries
            "other": [],          # unknown errors
        }
    }

    # Process in batches of BATCH_SIZE
    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch = user_ids[batch_start:batch_end]

        # Send to all users in batch concurrently
        tasks = [
            _send_to_user(context, uid, text, from_message, db)
            for uid in batch
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for res in batch_results:
            if isinstance(res, Exception):
                results["failed"] += 1
                results["errors"]["other"].append((0, str(res)))
                continue

            status, user_id, error_info = res
            if status == "success":
                results["success"] += 1
            else:
                results["failed"] += 1
                if status in results["errors"]:
                    results["errors"][status].append(user_id)
                else:
                    results["errors"]["other"].append((user_id, error_info))

        # Rate limit delay between batches
        await asyncio.sleep(BATCH_DELAY)

        # Progress callback every 100 users
        processed = batch_end
        if status_callback and (processed % 100 == 0 or processed == total):
            await status_callback(
                processed, total, results["success"], results["failed"]
            )

    return results
