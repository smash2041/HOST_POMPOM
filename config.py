"""
VIP Access Bot — Configuration
Loads environment variables for bot operation.
Single-owner system with multi-bot hosting support.
"""
import os

# Load .env file if present (for local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Telegram Bot ──
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ── Turso Database ──
TURSO_URL = os.getenv("TURSO_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# ── Owner (Single main owner with full hosting control) ──
OWNER_ID = int(os.getenv("OWNER_ID", os.getenv("OWNER_1", "0")))

# ── Payment ──
UPI_ID = os.getenv("UPI_ID", "")

# ── Server ──
PORT = int(os.getenv("PORT", "10000"))

# ── Bot Info ──
BOT_NAME = "VIP Access Bot"
