"""
VIP Access Bot — Configuration
Loads environment variables for bot operation.
Owner IDs are hidden — neither owner knows about the other.
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

# ── Owners (Hidden from each other — NEVER list them anywhere) ──
OWNER_1 = int(os.getenv("OWNER_1", "0"))
OWNER_2 = int(os.getenv("OWNER_2", "0"))
OWNER_IDS = {OWNER_1, OWNER_2} - {0}  # Remove 0 if env not set

# ── Payment ──
UPI_ID = os.getenv("UPI_ID", "")

# ── Server ──
PORT = int(os.getenv("PORT", "10000"))

# ── Bot Info ──
BOT_NAME = "VIP Access Bot"
