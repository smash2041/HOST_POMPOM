"""
VIP Access Bot — Main Entry Point
Starts Telegram bot (polling) + aiohttp web server concurrently.
Supports multi-bot hosting via BotManager.
Optimized for AWS EC2 deployment.
"""
import re
import asyncio
import logging

from aiohttp import web
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, PORT, OWNER_ID
from database import db
from web_server import create_web_app
from utils.bot_helpers import get_owner_ids
from handlers.start import (
    start_command,
    product_callback,
    back_to_products_callback,
    show_proof_callback,
    how_to_use_callback,
    report_issue_callback,
    cancel_issue_callback,
)
from handlers.payment import (
    buy_callback,
    paid_callback,
    handle_payment_proof,
    approve_callback,
    reject_callback,
)
from handlers.admin import get_admin_conversation_handler

# ── Logging ──
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
#  GLOBAL MESSAGE HANDLER
# ═══════════════════════════════════════════════

async def handle_message(update, context):
    """
    Global message handler (lowest priority).
    Routes payment proof, issue reporting, and owner replies.
    Uses dynamic owner_ids from context.bot_data.
    """
    if not update.message:
        return

    owner_ids = get_owner_ids(context)

    # Check for Owner Reply to an Issue
    if update.effective_user.id in owner_ids and update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        if reply_msg.text and "ID:" in reply_msg.text:
            try:
                # Extract ID using regex to avoid markdown bracket parsing issues
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

    # Check if user is reporting an issue
    if context.user_data.get("awaiting_issue"):
        # Forward issue to owners
        for owner_id in owner_ids:
            try:
                # Forward the exact message
                await update.message.forward(chat_id=owner_id)
                # Send context message with user ID
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

    # Check if user is sending payment proof
    await handle_payment_proof(update, context)


# ═══════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════

async def error_handler(update, context):
    """Global error handler — logs errors and notifies user."""
    logger.error(f"Update {update} caused error: {context.error}", exc_info=context.error)
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ An error occurred. Please try again.",
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════

async def main():
    """Main entry point — starts bot polling + web server + hosted bots."""
    logger.info("=" * 50)
    logger.info("Starting VIP Access Bot...")
    logger.info("=" * 50)

    # ── 1. Initialize Database ──
    await db.init()
    logger.info("✅ Database initialized")

    # ── 2. Build Telegram Bot Application ──
    app = Application.builder().token(BOT_TOKEN).build()

    # Set bot_data for multi-bot system
    app.bot_data["db"] = db
    app.bot_data["owner_ids"] = {OWNER_ID} - {0}
    app.bot_data["is_main_bot"] = True
    app.bot_data["main_db"] = db

    # Admin conversation handler (highest priority — group 0)
    app.add_handler(get_admin_conversation_handler(), group=0)

    # Command handlers (group 1)
    app.add_handler(CommandHandler("start", start_command), group=1)

    # Callback query handlers (group 1)
    app.add_handler(
        CallbackQueryHandler(product_callback, pattern=r"^product_\d+$"),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(buy_callback, pattern=r"^buy_\d+$"),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(paid_callback, pattern=r"^paid_\d+$"),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(
            back_to_products_callback, pattern=r"^back_to_products$"
        ),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(show_proof_callback, pattern=r"^show_proof$"),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(how_to_use_callback, pattern=r"^how_to_use$"),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(report_issue_callback, pattern=r"^report_issue$"),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(cancel_issue_callback, pattern=r"^cancel_issue$"),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(approve_callback, pattern=r"^approve_\d+$"),
        group=1,
    )
    app.add_handler(
        CallbackQueryHandler(reject_callback, pattern=r"^reject_\d+$"),
        group=1,
    )

    # Global message handler for payment proofs (lowest priority — group 2)
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_message),
        group=2,
    )

    # Error handler
    app.add_error_handler(error_handler)

    # ── 3. Start Bot (manual lifecycle for async compat) ──
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )
    logger.info("✅ Main bot polling started")

    # ── 4. Initialize BotManager & Load Hosted Bots ──
    from bot_manager import BotManager
    bot_manager = BotManager(db, OWNER_ID)
    app.bot_data["bot_manager"] = bot_manager

    await bot_manager.load_all_bots()
    logger.info("✅ Hosted bots loaded")

    # Start expiry checker background task
    expiry_task = asyncio.create_task(bot_manager.check_expiries())
    logger.info("✅ Expiry checker started")

    # ── 5. Start aiohttp Web Server ──
    web_app = create_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✅ Web server started on 0.0.0.0:{PORT}")

    logger.info("=" * 50)
    logger.info("🚀 VIP Access Bot is fully operational!")
    logger.info("=" * 50)

    # ── Keep running until interrupted ──
    try:
        stop_event = asyncio.Event()
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received...")
    finally:
        # ── Graceful Shutdown ──
        logger.info("Shutting down...")
        expiry_task.cancel()
        try:
            await expiry_task
        except asyncio.CancelledError:
            pass
        # Shutdown all hosted bots
        await bot_manager.shutdown_all()
        # Shutdown main bot
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await runner.cleanup()
        await db.close()
        logger.info("✅ Bot shut down complete")


if __name__ == "__main__":
    asyncio.run(main())
