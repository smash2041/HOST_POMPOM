"""
Host Bot — Bot Manager
Manages multiple hosted bot instances running in the same process.
Each hosted bot mirrors the main bot's products/settings.
Per-bot DB only for users/orders. Overrides for UPI/QR.
"""
import re
import logging
import asyncio
import time
import json
from urllib.error import URLError
from datetime import datetime
import hashlib

from telegram import Update
from telegram.ext import (
    Application, MessageHandler, CallbackQueryHandler,
    CommandHandler, filters
)

import httpx

from utils.media_clone import get_translated_file_id
from database import Database, _row_to_hosted_bot, HOSTED_BOT_COLS
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
    err_str = str(context.error).lower()
    # Suppress harmless Telegram errors so users don't see false error messages
    if any(s in err_str for s in ["query is too old", "message is not modified", "chat not found", "bot was blocked by"]):
        return

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
#  SIMPLE CONTEXT PROXY (for pre-caching)
# ═══════════════════════════════════════════════

class _SimpleContext:
    """Lightweight context proxy for background pre-caching.
    Mimics context.bot and context.bot_data for get_translated_file_id."""
    def __init__(self, app):
        self.bot = app.bot
        self.bot_data = app.bot_data


# ═══════════════════════════════════════════════
#  BOT MANAGER
# ═══════════════════════════════════════════════

class BotManager:
    """Manages multiple hosted bot instances running in the same process."""

    def __init__(self, main_db, main_owner_id, main_application=None):
        self.main_db = main_db
        self.main_owner_id = main_owner_id
        self.main_application = main_application
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
                bot_record = _row_to_hosted_bot(row)
                try:
                    await self._start_bot(bot_record)
                    logger.info(f"✅ Started hosted bot @{bot_record['bot_username']} ({bot_record['id']})")
                except Exception as e:
                    logger.error(f"❌ Failed to start bot {bot_record['id']}: {e}")
        except Exception as e:
            logger.error(f"Failed to load hosted bots: {e}")

    # ──────────────── DEEP CLONE DATA ────────────────

    async def _deep_clone_data(self, db_instance):
        """Deep clone products and settings from main DB to hosted bot DB."""
        try:
            # 1. Clone all products from main bot
            main_products = await self.main_db.get_all_products(active_only=False)
            for product in main_products:
                await db_instance.add_product(
                    name=product["name"],
                    price=product["price"],
                    description=product.get("description", ""),
                    trial_file_ids=json.dumps(product.get("trial_file_ids_list", [])),
                    channel_link=product.get("channel_link", ""),
                )

            # 2. Clone all settings from main bot
            main_settings = await self.main_db.execute("SELECT key, value FROM settings")
            cloned_settings = 0
            for row in main_settings.rows:
                await db_instance.set_setting(row[0], row[1])
                cloned_settings += 1

            logger.info(
                f"✅ Deep cloned {len(main_products)} products and "
                f"{cloned_settings} settings to {db_instance.table_prefix}"
            )
        except Exception as e:
            logger.error(f"❌ Deep clone failed for {db_instance.table_prefix}: {e}")

    async def fix_existing_bots(self):
        """For existing hosted bots that have 0 products, deep clone from main."""
        try:
            result = await self.main_db.execute(
                "SELECT * FROM hosted_bots WHERE status = 'active'"
            )
            if not result.rows:
                return

            fixed = 0
            for row in result.rows:
                bot_record = _row_to_hosted_bot(row)
                db_instance = Database(table_prefix=bot_record["table_prefix"])
                await db_instance.init()
                count = await db_instance.get_product_count()
                if count == 0:
                    await self._deep_clone_data(db_instance)
                    fixed += 1
                    logger.info(f"🔧 Fixed bot {bot_record['id']}: deep cloned data")
                await db_instance.close()

            if fixed:
                logger.info(f"✅ Fixed {fixed} existing hosted bots with deep clone")
            else:
                logger.info("✅ All existing hosted bots already have products")
        except Exception as e:
            logger.error(f"Failed to fix existing bots: {e}")

    # ──────────────── HOST NEW BOT ────────────────

    async def host_bot(self, bot_token, owner_user_id, expires_at=None, progress_chat_id=None):
        """
        Host a new bot instance.
        Validates token, creates tables, starts polling.
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

        # 6. Deep clone products & settings from main bot into new bot's DB
        new_db = Database(table_prefix=table_prefix)
        await new_db.init()
        await self._deep_clone_data(new_db)
        await new_db.close()

        # 7. Start the bot (with pre-cache since it's new)
        await self._start_bot(bot_record, is_new_bot=True, progress_chat_id=progress_chat_id)

        # 8. Log
        await self._log(bot_id, "created", f"Hosted bot @{bot_username} for user {owner_user_id}")

        logger.info(f"✅ Successfully hosted bot @{bot_username} ({bot_id})")
        return bot_record

    # ──────────────── START BOT INSTANCE ────────────────

    async def _start_bot(self, bot_record, is_new_bot=False, progress_chat_id=None):
        """Create Application, register handlers, start polling — MIRROR setup."""
        from handlers.admin import get_admin_conversation_handler

        bot_id = bot_record["id"]
        bot_token = bot_record["bot_token"]
        owner_user_id = bot_record["owner_user_id"]
        table_prefix = bot_record["table_prefix"]

        # Create per-bot Database (only for users/orders)
        db_instance = Database(table_prefix=table_prefix)
        await db_instance.init()

        # Create Application with concurrent updates for parallel speed
        app = Application.builder().token(bot_token).concurrent_updates(True).build()

        # MIRROR SETUP — the key architecture
        app.bot_data["db"] = db_instance          # Per-bot DB (users/orders)
        app.bot_data["main_db"] = self.main_db     # Main DB (products/settings/overrides)
        app.bot_data["owner_ids"] = {self.main_owner_id, owner_user_id} - {0}
        app.bot_data["is_main_bot"] = False
        app.bot_data["bot_id"] = bot_id            # For override lookups
        app.bot_data["bot_manager"] = self
        app.bot_data["main_bot"] = self.main_application.bot if self.main_application else None
        app.bot_data["dump_chat_id"] = owner_user_id  # Always use admin's chat for media clone

        # Register handlers (same as main bot)
        app.add_handler(get_admin_conversation_handler(), group=0)

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

        app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, _hosted_handle_message), group=2)
        app.add_error_handler(_hosted_error_handler)

        # Initialize
        await app.initialize()

        # For NEW hosted bots: set awaiting_admin_start flag
        # Pre-cache will happen AFTER admin /start confirms
        if self.main_application and is_new_bot:
            app.bot_data["awaiting_admin_start"] = True
            app.bot_data["admin_started"] = False
            app.bot_data["progress_chat_id"] = progress_chat_id
            app.bot_data["progress_bot"] = self.main_application.bot if self.main_application else None

        # Start polling
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

        # For new bots: wait for admin /start, then pre-cache
        if self.main_application and is_new_bot:
            asyncio.create_task(self._wait_for_admin_start(bot_id, app, db_instance))

    # ──────────────── WAIT FOR ADMIN /START ────────────────

    async def _wait_for_admin_start(self, bot_id, app, db_instance):
        """Wait for admin to /start the hosted bot before pre-caching media.
        Polls every 3 seconds for up to 5 minutes."""
        bot_username = app.bot.username or bot_id
        logger.info(f"⏳ Waiting for admin to /start @{bot_username}...")

        for i in range(100):  # 100 * 3s = 5 minutes max
            await asyncio.sleep(3)
            if app.bot_data.get("admin_started"):
                logger.info(f"✅ Admin started @{bot_username}, beginning media pre-cache...")
                await self._pre_cache_media(app, db_instance)
                return

        # Timeout — admin didn't start the bot
        logger.warning(f"⏰ Admin didn't /start @{bot_username} within 5 minutes")
        # Clear the flag so bot works normally even without pre-cache
        app.bot_data["awaiting_admin_start"] = False
        try:
            owner_ids = list(app.bot_data.get("owner_ids", set()))
            for oid in owner_ids:
                try:
                    await app.bot.send_message(
                        chat_id=oid,
                        text=(
                            f"⚠️ *Setup Timeout*\n\n"
                            f"You didn't /start @{bot_username} within 5 minutes.\n"
                            f"Media sync skipped. Bot is running but media may not work properly.\n\n"
                            f"Send /start to @{bot_username} to use it normally."
                        ),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Failed to notify about timeout: {e}")

    # ──────────────── PRE-CACHE MEDIA ────────────────

    async def _pre_cache_media(self, hosted_app, hosted_db):
        """
        Pre-cache all media from main bot to hosted bot at startup.
        Reports clone progress % to main bot.
        """
        try:
            context_proxy = _SimpleContext(hosted_app)
            main_bot_obj = hosted_app.bot_data.get("main_bot")
            if not main_bot_obj:
                return

            from utils.bot_helpers import get_owner_ids
            owner_list = list(get_owner_ids(context_proxy))
            dump_chat_id = hosted_app.bot_data.get("dump_chat_id")
            if not dump_chat_id and owner_list:
                dump_chat_id = sorted(owner_list)[0]

            bot_username = hosted_app.bot.username or "unknown"

            # Get progress reporting targets (main bot chat)
            progress_chat_id = hosted_app.bot_data.get("progress_chat_id")
            progress_bot = hosted_app.bot_data.get("progress_bot")

            setup_msg = None
            progress_msg = None

            if dump_chat_id:
                try:
                    setup_msg = await hosted_app.bot.send_message(
                        chat_id=dump_chat_id,
                        text="⚙️ *Bot Setup in Progress...*\n\nSyncing media files from the main bot. You will see some media appear and disappear here.\n\n*Please ignore them until setup is complete.*",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            # ── Collect all media items to clone ──
            all_media = []

            # 1. Welcome media (multi)
            welcome_ids_json = await self.main_db.get_setting("welcome_media_ids")
            if welcome_ids_json and welcome_ids_json != "[]":
                try:
                    for raw_id in json.loads(welcome_ids_json):
                        if raw_id:
                            all_media.append(raw_id)
                except Exception as e:
                    logger.warning(f"Pre-cache welcome media parse: {e}")

            # 2. Welcome video/photo (legacy single)
            for key, prefix in [("welcome_video_id", "video:"), ("welcome_photo_id", "photo:")]:
                fid = await self.main_db.get_setting(key)
                if fid:
                    all_media.append(f"{prefix}{fid}")

            # 3. Proof photo
            proof_photo = await self.main_db.get_setting("proof_photo_id")
            if proof_photo:
                all_media.append(f"photo:{proof_photo}")

            # 4. HTU video
            htu_video = await self.main_db.get_setting("htu_video_id")
            if htu_video:
                all_media.append(f"video:{htu_video}")

            # 5. QR image
            qr_image = await self.main_db.get_setting("qr_image_id")
            if qr_image:
                all_media.append(f"photo:{qr_image}")

            # 6. All product trial media
            products = await self.main_db.get_all_products()
            for product in products:
                trial_ids = product.get("trial_file_ids_list", [])
                for raw_id in trial_ids:
                    if raw_id:
                        all_media.append(raw_id)

            total = len(all_media)

            # Send initial progress to main bot
            if progress_chat_id and progress_bot:
                try:
                    if total > 0:
                        progress_msg = await progress_bot.send_message(
                            chat_id=progress_chat_id,
                            text=self._format_progress(0, total, 0, 0, bot_username),
                            parse_mode="HTML",
                        )
                    else:
                        await progress_bot.send_message(
                            chat_id=progress_chat_id,
                            text=(
                                f"✅ <b>Media Sync — @{bot_username}</b>\n\n"
                                f"No media files to sync.\n\n"
                                f"🎉 <b>Bot is ready to use!</b>"
                            ),
                            parse_mode="HTML",
                        )
                except Exception:
                    pass

            # ── Clone media with progress tracking ──
            cloned_count = 0
            failed_count = 0
            failed_items = []  # Track failed items for retry
            last_update_pct = -10

            for i, rid in enumerate(all_media):
                success = False
                for attempt in range(3):  # Up to 3 attempts per item
                    try:
                        result = await get_translated_file_id(context_proxy, rid)
                        if result != rid:
                            cloned_count += 1
                            success = True
                            break
                        else:
                            if attempt < 2:
                                logger.info(f"Pre-cache retry {attempt+1} for {str(rid)[:30]}...")
                                await asyncio.sleep(3)
                    except Exception as e:
                        if attempt < 2:
                            logger.info(f"Pre-cache retry {attempt+1} for {str(rid)[:20]}...: {e}")
                            await asyncio.sleep(3)

                if not success:
                    failed_count += 1
                    failed_items.append(rid)
                    logger.warning(f"Pre-cache FAILED after 3 attempts: {str(rid)[:30]}...")

                await asyncio.sleep(2)

                # Update progress in main bot (every 10% or on last item)
                current = i + 1
                pct = int(100 * current / total) if total > 0 else 100
                if progress_msg and progress_bot and (pct >= last_update_pct + 10 or current == total):
                    last_update_pct = pct
                    try:
                        if current == total:
                            txt = self._format_progress_complete(total, cloned_count, failed_count, bot_username)
                        else:
                            txt = self._format_progress(current, total, cloned_count, failed_count, bot_username)
                        await progress_msg.edit_text(txt, parse_mode="HTML")
                    except Exception:
                        pass

            # Update setup message in hosted bot admin chat
            if setup_msg:
                try:
                    status = f"✅ Bot Setup Complete!\n\n✅ {cloned_count} media synced"
                    if failed_count:
                        status += f"\n⚠️ {failed_count} failed (will retry on demand)"
                    await setup_msg.edit_text(status)
                except Exception:
                    pass

            logger.info(f"✅ Pre-cached {cloned_count} media, {failed_count} failed for bot {bot_username}")

        except Exception as e:
            logger.error(f"Pre-cache media failed: {e}")

    # ──────────────── PROGRESS FORMATTING ────────────────

    @staticmethod
    def _format_progress(current, total, cloned, failed, bot_username):
        """Format progress bar message for main bot."""
        bar_length = 20
        if total == 0:
            filled, pct = bar_length, 100
        else:
            filled = int(bar_length * current / total)
            pct = int(100 * current / total)
        bar = "█" * filled + "░" * (bar_length - filled)

        text = (
            f"⚙️ <b>Media Sync — @{bot_username}</b>\n\n"
            f"<code>{bar}</code> {pct}%\n"
            f"📦 {current} / {total} files processed\n"
        )
        if cloned:
            text += f"✅ {cloned} synced"
        if failed:
            text += f" | ⚠️ {failed} failed"
        text += "\n\n⏳ Please wait..."
        return text

    @staticmethod
    def _format_progress_complete(total, cloned, failed, bot_username):
        """Format completion message for main bot."""
        bar = "█" * 20
        text = (
            f"✅ <b>Media Sync Complete — @{bot_username}</b>\n\n"
            f"<code>{bar}</code> 100%\n"
            f"📦 {total} / {total} files processed\n"
            f"✅ {cloned} synced"
        )
        if failed:
            text += f" | ⚠️ {failed} failed (will retry on demand)"
        text += f"\n\n🎉 <b>Bot is ready to use!</b>"
        return text

    # ──────────────── STOP BOT ────────────────

    async def stop_bot(self, bot_id, reason="manual"):
        """Stop a running bot instance."""
        if bot_id not in self.bots:
            await self.main_db.execute(
                "UPDATE hosted_bots SET status = 'stopped', stopped_at = ?, stopped_reason = ? WHERE id = ?",
                [datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), reason, bot_id],
            )
            return

        bot_data = self.bots[bot_id]
        app = bot_data["app"]
        db_inst = bot_data["db"]

        try:
            async def _shutdown_app():
                if app.updater and app.updater.running:
                    await app.updater.stop()
                await app.stop()
                await app.shutdown()

            try:
                await asyncio.wait_for(_shutdown_app(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"Shutdown timed out for bot {bot_id}, forcing close")
        except Exception as e:
            logger.warning(f"Error stopping bot {bot_id}: {e}")

        await db_inst.close()
        del self.bots[bot_id]

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        await self.main_db.execute(
            "UPDATE hosted_bots SET status = 'stopped', stopped_at = ?, stopped_reason = ? WHERE id = ?",
            [now, reason, bot_id],
        )
        await self._log(bot_id, "stopped", f"Reason: {reason}")

    # ──────────────── RESUME BOT ────────────────

    async def resume_bot(self, bot_id):
        """Resume a stopped bot instance."""
        result = await self.main_db.execute(
            "SELECT * FROM hosted_bots WHERE id = ?", [bot_id]
        )
        if not result.rows:
            raise ValueError("Bot not found.")

        record = _row_to_hosted_bot(result.rows[0])

        if bot_id in self.bots:
            return  # Already running

        await self.main_db.execute(
            "UPDATE hosted_bots SET status = 'active', stopped_at = '', stopped_reason = '' WHERE id = ?",
            [bot_id],
        )

        record["status"] = "active"
        await self._start_bot(record)
        await self._log(bot_id, "resumed", "Bot resumed")

    # ──────────────── DELETE BOT ────────────────

    async def delete_bot(self, bot_id):
        """Stop and completely delete a bot — drops all data tables."""
        if bot_id in self.bots:
            await self.stop_bot(bot_id, reason="deleted")

        # Get table prefix and drop per-bot tables (users, orders, settings)
        result = await self.main_db.execute(
            "SELECT table_prefix FROM hosted_bots WHERE id = ?", [bot_id]
        )
        if result.rows:
            prefix = result.rows[0][0]
            temp_db = Database(table_prefix=prefix)
            await temp_db.connect()
            await temp_db.drop_bot_tables()
            await temp_db.close()

        # Delete record
        await self.main_db.execute("DELETE FROM hosted_bots WHERE id = ?", [bot_id])
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
        """Background task: check for expired bots and admin access every hour."""
        while True:
            try:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

                # ── 1. Check bot expiries ──
                result = await self.main_db.execute(
                    "SELECT * FROM hosted_bots WHERE status = 'active' AND expires_at != '' AND expires_at < ?",
                    [now],
                )

                for row in (result.rows or []):
                    record = _row_to_hosted_bot(row)
                    bot_id = record["id"]
                    logger.info(f"⏰ Bot {bot_id} (@{record['bot_username']}) has expired")

                    await self.stop_bot(bot_id, reason="expired")

                    try:
                        temp_db = Database(table_prefix=record["table_prefix"])
                        await temp_db.connect()
                        await temp_db.drop_bot_tables()
                        await temp_db.close()
                    except Exception as e:
                        logger.warning(f"Failed to drop tables for expired bot {bot_id}: {e}")

                    await self.main_db.execute(
                        "UPDATE hosted_bots SET status = 'expired' WHERE id = ?",
                        [bot_id],
                    )
                    await self._log(bot_id, "expired", "Bot expired — data cleaned up")

                # ── 2. Check admin expiries ──
                try:
                    expired_admins = await self.main_db.get_expired_admins()
                    for admin in expired_admins:
                        admin_id = admin["user_id"]
                        logger.info(f"⏰ Admin {admin_id} access has expired")

                        # Stop their hosted bot
                        hosted = await self.main_db.get_hosted_bot_by_admin(admin_id)
                        if hosted and hosted["id"] in self.bots:
                            await self.stop_bot(hosted["id"], reason="admin_expired")
                            await self.main_db.execute(
                                "UPDATE hosted_bots SET status = 'stopped', stopped_reason = 'admin_expired' WHERE id = ?",
                                [hosted["id"]],
                            )
                            await self._log(hosted["id"], "admin_expired", f"Admin {admin_id} access expired — bot paused")

                        # Remove admin access
                        await self.main_db.remove_hosting_admin(admin_id)
                        logger.info(f"✅ Removed expired admin {admin_id}")

                        # Notify main owner
                        try:
                            if self.main_application:
                                bot_info = f" (@{hosted['bot_username']})" if hosted else ""
                                await self.main_application.bot.send_message(
                                    chat_id=self.main_owner_id,
                                    text=(
                                        f"⏰ *Admin Expired*\n\n"
                                        f"Admin `{admin_id}` access has expired.\n"
                                        f"Hosted bot{bot_info} has been paused.\n\n"
                                        f"Expired at: {admin.get('expires_at', '?')}"
                                    ),
                                    parse_mode="Markdown",
                                )
                        except Exception as e:
                            logger.warning(f"Failed to notify owner about admin expiry: {e}")

                except Exception as e:
                    logger.error(f"Error checking admin expiries: {e}")

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
            return _row_to_hosted_bot(result.rows[0])
        return None

    async def get_all_bots(self):
        """Get all hosted bots."""
        result = await self.main_db.execute(
            "SELECT * FROM hosted_bots ORDER BY created_at DESC"
        )
        return [_row_to_hosted_bot(row) for row in (result.rows or [])]

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
