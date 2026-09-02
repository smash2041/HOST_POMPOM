"""
Host Bot — Web Server
aiohttp server for health checks and UptimeRobot monitoring.
"""
import time
from aiohttp import web

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


def create_web_app():
    """Create and return the aiohttp web application."""
    app = web.Application()

    # Add GET handlers
    app.router.add_get("/", handle_root)
    app.router.add_get("/ping", handle_ping)
    app.router.add_get("/health", handle_health)

    # Add HEAD handlers for monitoring compatibility
    app.router.add_head("/", handle_root)
    app.router.add_head("/ping", handle_ping)
    app.router.add_head("/health", handle_health)

    return app
