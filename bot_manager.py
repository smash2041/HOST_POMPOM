"""
VIP Access Bot — Bot Manager
Manages multiple hosted bot instances running in the same process.
Each hosted bot gets its own database partition (table prefix).
"""
import re
import logging
import asyncio
import hashlib
from datetime import datetime

import httpx
from telegram.ext import (
    Application, MessageHandler, CallbackQueryHandler,
    CommandHandler, filters
)

from database import Database
from handlers.admin import get_admin_conversation_handler
from handlers.start import (
    start_command, product_callback, back_to_products_callback,
    show_proof_callback, how_to_use_callback, report_issue_callback,
    cancel_issue_callback
)
from handlers.payment import (
    buy_callback, paid_callback, handle_payment_proof,
    approve_callback, reject_callback
)
from utils.bot_helpers import get_owner_ids

logger = logging.getLogger(__name__)

# Column names for hosted_bots table
HOSTED_BOT_COLS = [
    "id", "bot_token", "bot_username", "owner_user_id", "table_prefix",
    "status", "created_at", "expires_at", "stopped_at", "stopped_reason",
]


def _row_to_bot(row):
    """Convert a hosted_bots row tuple to a dict."""
    return dict(zip(HOSTED_BOT_COLS, row))


# ═══════════════════════════════════════════════
#  MESSAGE & ERROR HANDLERS FOR HOSTED BOTS
# ═══════════════════════════════════════════════

async def _hosted_handle_message(update, context):
    """Global message handler for hosted bots (same logic as main)."""
    if not update.message:
        return

    owner_ids = get_owner_ids(context)

    if update.effective_user.id in owner_ids and update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        if reply_msg.text and "ID:" in reply_msg.text:
            try:
                match = re.search(r"ID:\s*(\d+)", reply_msg.text)
                if match:
                    user_id = int(match.group(1))
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"📨 *Reply from Admin:*\n\n{update.message.text}",
                        parse_mode="Markdown"
                    )
                    await update.message.reply_text("✅ Reply sent to user.")
                    return
            except Exception as e:
                logger.warning(f"Failed to parse user ID from reply: {e}")

    if context.user_data.get("awaiting_issue"):
        for owner_id in owner_ids:
            try:
                await update.message.forward(chat_id=owner_id)
                username = f"@{update.effective_user.username}" if update.effective_user.username else "No Username"
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=f"🆘 *New Issue Reported*\nFrom: {update.effective_user.first_name} ({username})\n[User ID: {update.effective_user.id}]\n\n_Reply to this message to answer the user._",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Failed to forward issue to owner {owner_id}: {e}")

        context.user_data.pop("awaiting_issue", None)
        await update.message.reply_text("✅ Your issue has been sent to the support team. We will reply soon.")
        return

    await handle_payment_proof(update, context)


async def _hosted_error_handler(update, context):
    """Error handler for hosted bots."""
    logger.error(f"Hosted bot error: {context.error}", exc_info=context.error)
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ An error occurred. Please try again.",
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════
#  BOT MANAGER
# ═══════════════════════════════════════════════

class BotManager:
    """Manages multiple hosted bot instances running in the same process."""

    def __init__(self, main_db, main_owner_id):
        self.main_db = main_db
        self.main_owner_id = main_owner_id
        self.bots = {}  # bot_id -> {"app": Application, "db": Database, "record": dict}

    # ──────────────── STARTUP ────────────────

    async def load_all_bots(self):
        """On startup, load and start all active hosted bots from the database."""
        try:
            result = await self.main_db.execute(
                "SELECT * FROM hosted_bots WHERE status = 'active'"
            )
            if not result.rows:
                logger.info("No active hosted bots to load")
                return

            for row in result.rows:
                bot_record = _row_to_bot(row)
                try:
                    await self._start_bot(bot_record)
                    logger.info(f"✅ Started hosted bot @{bot_record['bot_username']} ({bot_record['id']})")
                except Exception as e:
                    logger.error(f"❌ Failed to start bot {bot_record['id']}: {e}")
        except Exception as e:
            logger.error(f"Failed to load hosted bots: {e}")

    # ──────────────── HOST NEW BOT ────────────────

    async def host_bot(self, bot_token, owner_user_id, expires_at=None):
        """
        Host a new bot instance.
        Validates token, creates tables, starts polling.
        Returns bot info dict on success, raises on failure.
        """
        # 1. Validate the bot token via Telegram API
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"https://api.telegram.org/bot{bot_token}/getMe")
                data = response.json()
                if not data.get("ok"):
                    raise ValueError("Invalid bot token — Telegram API returned error")
                bot_username = data["result"].get("username", "unknown")
        except httpx.HTTPError as e:
            raise ValueError(f"Failed to validate bot token: {e}")

        # 2. Check if token is already registered
        existing = await self.main_db.execute(
            "SELECT id FROM hosted_bots WHERE bot_token = ?", [bot_token]
        )
        if existing.rows:
            raise ValueError("This bot token is already registered.")

        # 3. Generate unique bot_id
        bot_id = hashlib.sha256(bot_token.encode()).hexdigest()[:8]

        # 4. Table prefix
        table_prefix = f"hb_{bot_id}_"

        # 5. Insert into hosted_bots
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        expires_str = expires_at or ""

        await self.main_db.execute(
            "INSERT INTO hosted_bots "
            "(id, bot_token, bot_username, owner_user_id, table_prefix, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            [bot_id, bot_token, bot_username, owner_user_id, table_prefix, now, expires_str],
        )

        bot_record = {
            "id": bot_id,
            "bot_token": bot_token,
            "bot_username": bot_username,
            "owner_user_id": owner_user_id,
            "table_prefix": table_prefix,
            "status": "active",
            "created_at": now,
            "expires_at": expires_str,
            "stopped_at": "",
            "stopped_reason": "",
        }

        # 6. Create Database with prefix + init tables
        db_instance = Database(table_prefix=table_prefix)
        await db_instance.init()

        # 7. Start the bot
        await self._start_bot(bot_record)

        # 8. Log
        await self._log(bot_id, "created", f"Hosted bot @{bot_username} for user {owner_user_id}")

        logger.info(f"✅ Successfully hosted bot @{bot_username} ({bot_id})")
        return bot_record

    # ──────────────── START BOT INSTANCE ────────────────

    async def _start_bot(self, bot_record):
        """Create Application, register handlers, and start polling for a hosted bot."""
        bot_id = bot_record["id"]
        bot_token = bot_record["bot_token"]
        owner_user_id = bot_record["owner_user_id"]
        table_prefix = bot_record["table_prefix"]

        # Create Database instance
        db_instance = Database(table_prefix=table_prefix)
        await db_instance.init()

        # Create Application
        app = Application.builder().token(bot_token).build()

        # Set bot_data for multi-bot system
        app.bot_data["db"] = db_instance
        app.bot_data["owner_ids"] = {self.main_owner_id, owner_user_id} - {0}
        app.bot_data["is_main_bot"] = False
        app.bot_data["main_db"] = self.main_db
        app.bot_data["bot_manager"] = self

        # Register handlers (same as main bot)
        # Group 0: Admin conversation handler
        app.add_handler(get_admin_conversation_handler(), group=0)

        # Group 1: Commands and callbacks
        app.add_handler(CommandHandler("start", start_command), group=1)
        app.add_handler(CallbackQueryHandler(product_callback, pattern=r"^product_\d+$"), group=1)
        app.add_handler(CallbackQueryHandler(buy_callback, pattern=r"^buy_\d+$"), group=1)
        app.add_handler(CallbackQueryHandler(paid_callback, pattern=r"^paid_\d+$"), group=1)
        app.add_handler(CallbackQueryHandler(back_to_products_callback, pattern=r"^back_to_products$"), group=1)
        app.add_handler(CallbackQueryHandler(show_proof_callback, pattern=r"^show_proof$"), group=1)
        app.add_handler(CallbackQueryHandler(how_to_use_callback, pattern=r"^how_to_use$"), group=1)
        app.add_handler(CallbackQueryHandler(report_issue_callback, pattern=r"^report_issue$"), group=1)
        app.add_handler(CallbackQueryHandler(cancel_issue_callback, pattern=r"^cancel_issue$"), group=1)
        app.add_handler(CallbackQueryHandler(approve_callback, pattern=r"^approve_\d+$"), group=1)
        app.add_handler(CallbackQueryHandler(reject_callback, pattern=r"^reject_\d+$"), group=1)

        # Group 2: Global message handler
        app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, _hosted_handle_message), group=2)

        # Error handler
        app.add_error_handler(_hosted_error_handler)

        # Initialize and start polling
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )

        # Store
        self.bots[bot_id] = {
            "app": app,
            "db": db_instance,
            "record": bot_record,
        }

    # ──────────────── STOP BOT ────────────────

    async def stop_bot(self, bot_id, reason="manual"):
        """Stop a running bot instance."""
        if bot_id not in self.bots:
            # Bot not running, just update status
            await self.main_db.execute(
                "UPDATE hosted_bots SET status = 'stopped', stopped_at = ?, stopped_reason = ? WHERE id = ?",
                [datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), reason, bot_id],
            )
            return

        bot_data = self.bots[bot_id]
        app = bot_data["app"]
        db_inst = bot_data["db"]

        # Stop polling
        try:
            if app.updater and app.updater.running:
                await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logger.warning(f"Error stopping bot {bot_id}: {e}")

        # Close db
        await db_inst.close()

        # Remove from running bots
        del self.bots[bot_id]

        # Update status
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        await self.main_db.execute(
            "UPDATE hosted_bots SET status = 'stopped', stopped_at = ?, stopped_reason = ? WHERE id = ?",
            [now, reason, bot_id],
        )

        # Log
        await self._log(bot_id, "stopped", f"Reason: {reason}")

    # ──────────────── RESUME BOT ────────────────

    async def resume_bot(self, bot_id):
        """Resume a stopped bot instance."""
        result = await self.main_db.execute(
            "SELECT * FROM hosted_bots WHERE id = ?", [bot_id]
        )
        if not result.rows:
            raise ValueError("Bot not found.")

        record = _row_to_bot(result.rows[0])

        if bot_id in self.bots:
            return  # Already running

        # Update status
        await self.main_db.execute(
            "UPDATE hosted_bots SET status = 'active', stopped_at = '', stopped_reason = '' WHERE id = ?",
            [bot_id],
        )

        # Start the bot
        record["status"] = "active"
        await self._start_bot(record)

        # Log
        await self._log(bot_id, "resumed", "Bot resumed")

    # ──────────────── DELETE BOT ────────────────

    async def delete_bot(self, bot_id):
        """Stop and completely delete a bot — drops all its data tables."""
        # Stop if running
        if bot_id in self.bots:
            await self.stop_bot(bot_id, reason="deleted")

        # Get table prefix
        result = await self.main_db.execute(
            "SELECT table_prefix FROM hosted_bots WHERE id = ?", [bot_id]
        )
        if result.rows:
            prefix = result.rows[0][0]
            # Drop all data tables
            temp_db = Database(table_prefix=prefix)
            await temp_db.connect()
            await temp_db.drop_all_tables()
            await temp_db.close()

        # Delete record
        await self.main_db.execute("DELETE FROM hosted_bots WHERE id = ?", [bot_id])

        # Log
        await self._log(bot_id, "deleted", "Bot deleted — all data removed")

    # ──────────────── EXPIRY ────────────────

    async def set_expiry(self, bot_id, expires_at):
        """Set the expiry date for a bot."""
        expires_str = expires_at or ""
        await self.main_db.execute(
            "UPDATE hosted_bots SET expires_at = ? WHERE id = ?",
            [expires_str, bot_id],
        )
        await self._log(bot_id, "expiry_set", f"Expiry set to: {expires_str or 'never'}")

    async def check_expiries(self):
        """Background task: check for expired bots every hour."""
        while True:
            try:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                result = await self.main_db.execute(
                    "SELECT * FROM hosted_bots WHERE status = 'active' AND expires_at != '' AND expires_at < ?",
                    [now],
                )

                for row in (result.rows or []):
                    record = _row_to_bot(row)
                    bot_id = record["id"]
                    logger.info(f"⏰ Bot {bot_id} (@{record['bot_username']}) has expired")

                    # Stop the bot
                    await self.stop_bot(bot_id, reason="expired")

                    # Drop all data tables
                    try:
                        temp_db = Database(table_prefix=record["table_prefix"])
                        await temp_db.connect()
                        await temp_db.drop_all_tables()
                        await temp_db.close()
                    except Exception as e:
                        logger.warning(f"Failed to drop tables for expired bot {bot_id}: {e}")

                    # Update status to expired
                    await self.main_db.execute(
                        "UPDATE hosted_bots SET status = 'expired' WHERE id = ?",
                        [bot_id],
                    )

                    await self._log(bot_id, "expired", "Bot expired — data cleaned up")
                    logger.info(f"✅ Expired bot {bot_id} cleaned up")

            except Exception as e:
                logger.error(f"Error in expiry checker: {e}")

            await asyncio.sleep(3600)  # Check every 1 hour

    # ──────────────── INFO & STATS ────────────────

    async def get_bot_info(self, bot_id):
        """Get information about a specific hosted bot."""
        result = await self.main_db.execute(
            "SELECT * FROM hosted_bots WHERE id = ?", [bot_id]
        )
        if result.rows:
            return _row_to_bot(result.rows[0])
        return None

    async def get_all_bots(self):
        """Get all hosted bots."""
        result = await self.main_db.execute(
            "SELECT * FROM hosted_bots ORDER BY created_at DESC"
        )
        return [_row_to_bot(row) for row in (result.rows or [])]

    async def get_bot_stats(self, bot_id):
        """Get statistics for a specific hosted bot."""
        result = await self.main_db.execute(
            "SELECT table_prefix FROM hosted_bots WHERE id = ?", [bot_id]
        )
        if not result.rows:
            return None

        prefix = result.rows[0][0]
        temp_db = Database(table_prefix=prefix)
        await temp_db.connect()
        try:
            stats = await temp_db.get_stats()
        finally:
            await temp_db.close()
        return stats

    # ──────────────── SHUTDOWN ────────────────

    async def shutdown_all(self):
        """Stop all running bots gracefully."""
        bot_ids = list(self.bots.keys())
        for bot_id in bot_ids:
            try:
                await self.stop_bot(bot_id, reason="shutdown")
                logger.info(f"Stopped hosted bot {bot_id}")
            except Exception as e:
                logger.error(f"Error shutting down bot {bot_id}: {e}")

    # ──────────────── HELPERS ────────────────

    async def _log(self, bot_id, action, details=""):
        """Log a hosting action."""
        try:
            await self.main_db.execute(
                "INSERT INTO hosting_logs (bot_id, action, performed_by, details) "
                "VALUES (?, ?, ?, ?)",
                [bot_id, action, self.main_owner_id, details],
            )
        except Exception as e:
            logger.warning(f"Failed to write hosting log: {e}")
