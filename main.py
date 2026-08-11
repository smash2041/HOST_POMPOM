"""
VIP Access Bot — Main Entry Point
Starts Telegram bot (polling) + aiohttp web server concurrently.
Uses httpx for self-ping keep-alive on Render free tier.
"""
import os
import re
import asyncio
import logging

import httpx
from aiohttp import web
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, PORT, OWNER_IDS
from database import db
from web_server import create_web_app
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
    """
    if not update.message:
        return

    # Check for Owner Reply to an Issue
    if update.effective_user.id in OWNER_IDS and update.message.reply_to_message:
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
        for owner_id in OWNER_IDS:
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
#  SELF-PING (Render Keep-Alive)
# ═══════════════════════════════════════════════

async def self_ping(url: str, interval: int = 300):
    """
    Self-ping loop to keep Render free-tier service alive.
    Uses httpx async client. Pings every 5 minutes.
    """
    await asyncio.sleep(30)  # Wait for server startup
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            try:
                response = await client.get(f"{url}/ping")
                logger.info(f"Self-ping OK: {response.status_code}")
            except Exception as e:
                logger.warning(f"Self-ping failed: {e}")
            await asyncio.sleep(interval)


# ═══════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════

async def main():
    """Main entry point — starts bot polling + web server concurrently."""
    logger.info("=" * 50)
    logger.info("Starting VIP Access Bot...")
    logger.info("=" * 50)

    # ── 1. Initialize Database ──
    await db.init()
    logger.info("✅ Database initialized")

    # ── 2. Build Telegram Bot Application ──
    app = Application.builder().token(BOT_TOKEN).build()

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
    logger.info("✅ Bot polling started")

    # ── 4. Start aiohttp Web Server ──
    web_app = create_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✅ Web server started on 0.0.0.0:{PORT}")

    # ── 5. Start Self-Ping (Render keep-alive via httpx) ──
    render_url = os.getenv(
        "RENDER_EXTERNAL_URL", f"http://localhost:{PORT}"
    )
    ping_task = asyncio.create_task(self_ping(render_url))
    logger.info(f"✅ Self-ping task started → {render_url}/ping")

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
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await runner.cleanup()
        await db.close()
        logger.info("✅ Bot shut down complete")


if __name__ == "__main__":
    asyncio.run(main())
