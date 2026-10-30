"""
Host Bot — Web Server
aiohttp server for health checks and UptimeRobot monitoring.
"""
import time
import json
import logging
import html
from aiohttp import web
from utils.payfino import client_from_bot_db

logger = logging.getLogger(__name__)

start_time = time.time()


async def handle_root(request):
    """Root endpoint — health check."""
    uptime = int(time.time() - start_time)
    hours, remainder = divmod(uptime, 3600)
    minutes, seconds = divmod(remainder, 60)
    return web.Response(
        text=(
            f"🤖 Host Bot is alive!\n"
            f"⏱ Uptime: {hours}h {minutes}m {seconds}s"
        ),
        content_type="text/plain",
    )


async def handle_ping(request):
    """Ping endpoint — for UptimeRobot / monitoring services."""
    return web.json_response(
        {"status": "ok", "uptime": int(time.time() - start_time)}
    )


async def handle_health(request):
    """Detailed health check endpoint."""
    return web.json_response(
        {
            "status": "healthy",
            "uptime": int(time.time() - start_time),
            "service": "Host Bot",
        }
    )


async def handle_payfino_callback(request):
    """Verify Payfino callbacks against the gateway before granting access."""
    try:
        callback = await request.json()
        reference_id = str(callback.get("reference_id") or "")
        main_app = request.app["main_app"]
        main_db = main_app.bot_data["db"]
        map_key = f"payfino_order_{reference_id}"
        raw = await main_db.get_setting(map_key)
        mapping = json.loads(raw) if raw else None
        if not mapping or mapping.get("status") != "pending":
            return web.json_response({"received": True}, status=200)
        if str(callback.get("status", "")).upper() != "SUCCESS":
            return web.json_response({"received": True}, status=200)

        manager = main_app.bot_data.get("bot_manager")
        hosted = manager.bots.get(mapping["bot_id"]) if manager else None
        if not hosted:
            logger.warning("Payfino callback for unavailable hosted bot %s", mapping.get("bot_id"))
            return web.json_response({"error": "bot unavailable"}, status=503)
        bot_app = hosted["app"]
        bot_db = hosted["db"]
        client = await client_from_bot_db(bot_db)
        if not client:
            return web.json_response({"error": "gateway configuration unavailable"}, status=503)

        verified = await client.verify_payment(reference_id)
        verified_data = verified.get("data") or verified
        if str(verified_data.get("status", "")).upper() != "SUCCESS":
            return web.json_response({"received": True}, status=200)
        verified_order = str(verified_data.get("order_id") or verified_data.get("reference_id") or reference_id)
        if verified_order != reference_id:
            return web.json_response({"error": "order mismatch"}, status=400)
        amount = float(verified_data.get("amount", mapping["amount"]))
        if abs(amount - float(mapping["amount"])) > 0.01:
            return web.json_response({"error": "amount mismatch"}, status=400)

        # Persist approval before notification to make duplicate callbacks idempotent.
        mapping["status"] = "approved"
        await main_db.set_setting(map_key, json.dumps(mapping))
        message = (
            f"🎉 <b>Payment verified!</b>\n\n"
            f"✅ Your payment for <b>{html.escape(str(mapping['product_name']))}</b> was successful.\n\n"
        )
        if mapping.get("channel_link"):
            message += f"🔗 <b>Access your premium content:</b>\n{html.escape(str(mapping['channel_link']))}\n\n"
        message += "Thank you for your purchase!"
        await bot_app.bot.send_message(
            chat_id=int(mapping["user_id"]), text=message, parse_mode="HTML"
        )
        return web.json_response({"received": True, "verified": True})
    except Exception as exc:
        logger.exception("Payfino callback processing failed: %s", exc)
        return web.json_response({"error": "callback processing failed"}, status=500)


def create_web_app(main_app=None):
    """Create and return the aiohttp web application."""
    app = web.Application()
    if main_app is not None:
        app["main_app"] = main_app

    # Add GET handlers
    app.router.add_get("/", handle_root)
    app.router.add_get("/ping", handle_ping)
    app.router.add_get("/health", handle_health)
    if main_app is not None:
        app.router.add_post("/payfino/callback", handle_payfino_callback)

    # Add HEAD handlers for monitoring compatibility
    app.router.add_head("/", handle_root)
    app.router.add_head("/ping", handle_ping)
    app.router.add_head("/health", handle_health)

    return app
