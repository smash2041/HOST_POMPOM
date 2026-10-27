"""
Host Bot — Database Layer
Turso DB connection via libsql_client with retry logic.
Mirror architecture:
  - Products: ALWAYS from main DB (no override)
  - Settings: Per-bot settings table (empty initially, falls back to main)
  - Users/Orders: Per-bot prefixed tables
"""
import json
import asyncio
import uuid
import logging
import libsql_client

from config import TURSO_URL, TURSO_AUTH_TOKEN

logger = logging.getLogger(__name__)

PRODUCT_COLS = [
    "id", "name", "price", "description", "trial_file_ids",
    "channel_link", "sort_order", "is_active", "button_style",
]
USER_COLS = ["user_id", "username", "first_name", "joined_at", "is_banned"]
ORDER_COLS = [
    "id", "user_id", "product_id", "status", "payment_proof",
    "proof_type", "created_at", "resolved_at", "resolved_by",
    "utr_text",
]
SWIPE_ORDER_COLS = [
    "id", "bot_id", "user_id", "session_id", "product_id", "product_name",
    "amount", "status", "payment_proof", "proof_type", "utr_text",
    "created_at", "resolved_at", "resolved_by",
]


class Database:
    """Async database wrapper for Turso with automatic retry logic."""

    def __init__(self, table_prefix=""):
        self.client = None
        self.table_prefix = table_prefix

    # ──────────────── Connection ────────────────

    async def connect(self):
        try:
            if self.client:
                try:
                    await self.client.close()
                except Exception:
                    pass
            safe_url = TURSO_URL.replace("libsql://", "https://") if TURSO_URL else TURSO_URL
            safe_token = TURSO_AUTH_TOKEN.strip("\"'") if TURSO_AUTH_TOKEN else TURSO_AUTH_TOKEN
            self.client = libsql_client.create_client(url=safe_url, auth_token=safe_token)
            logger.info(f"Connected to Turso DB (prefix='{self.table_prefix}')")
        except Exception as e:
            logger.error(f"Failed to connect to Turso: {e}")
            raise

    async def execute(self, sql, params=None):
        for attempt in range(5):
            try:
                if self.client is None:
                    await self.connect()
                if params:
                    result = await self.client.execute(sql, params)
                else:
                    result = await self.client.execute(sql)
                return result
            except Exception as e:
                logger.warning(f"DB query attempt {attempt + 1}/5 failed: {e}")
                if attempt < 4:
                    await asyncio.sleep(1.0 * (1.5 ** attempt))
                    try:
                        await self.connect()
                    except Exception:
                        pass
                else:
                    logger.error(f"DB query failed after 5 attempts: {sql}")
                    raise

    async def init(self):
        await self.connect()
        await self._create_tables()
        await self._migrate_columns()
        await self._set_defaults()
        logger.info(f"Database initialized (prefix='{self.table_prefix}')")

    def _t(self, table_name):
        return f"{self.table_prefix}{table_name}"

    async def _create_tables(self):
        t = self._t

        # Per-bot tables (prefixed for hosted bots, unprefixed for main)
        # BOTH main and hosted bots get: products, users, orders, settings
        per_bot_tables = [
            f"""CREATE TABLE IF NOT EXISTS {t('products')} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                description TEXT DEFAULT '',
                trial_file_ids TEXT DEFAULT '[]',
                channel_link TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                button_style TEXT DEFAULT ''
            )""",
            f"""CREATE TABLE IF NOT EXISTS {t('users')} (
                user_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT '',
                first_name TEXT DEFAULT '',
                joined_at TEXT DEFAULT (datetime('now')),
                is_banned INTEGER DEFAULT 0
            )""",
            f"""CREATE TABLE IF NOT EXISTS {t('orders')} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                payment_proof TEXT DEFAULT '',
                proof_type TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT DEFAULT '',
                resolved_by INTEGER DEFAULT 0,
                utr_text TEXT DEFAULT ''
            )""",
            f"""CREATE TABLE IF NOT EXISTS {t('settings')} (
                key TEXT PRIMARY KEY,
                value TEXT DEFAULT ''
            )""",
            f"""CREATE TABLE IF NOT EXISTS {t('media_cache')} (
                original_file_id TEXT PRIMARY KEY,
                cloned_file_id TEXT NOT NULL
            )""",
        ]
        for sql in per_bot_tables:
            await self.execute(sql)

        # Main-only tables (master hosting tables) — only for unprefixed DB
        if not self.table_prefix:
            main_tables = [
                """CREATE TABLE IF NOT EXISTS hosted_bots (
                    id TEXT PRIMARY KEY,
                    bot_token TEXT NOT NULL UNIQUE,
                    bot_username TEXT DEFAULT '',
                    owner_user_id INTEGER NOT NULL,
                    table_prefix TEXT NOT NULL UNIQUE,
                    status TEXT DEFAULT 'active',
                    created_at TEXT DEFAULT (datetime('now')),
                    expires_at TEXT DEFAULT '',
                    stopped_at TEXT DEFAULT '',
                    stopped_reason TEXT DEFAULT ''
                )""",
                """CREATE TABLE IF NOT EXISTS hosting_admins (
                    user_id INTEGER PRIMARY KEY,
                    added_at TEXT DEFAULT (datetime('now')),
                    added_by INTEGER DEFAULT 0,
                    expires_at TEXT DEFAULT ''
                )""",
                """CREATE TABLE IF NOT EXISTS hosting_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    performed_by INTEGER DEFAULT 0,
                    details TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )""",
                # ── Swipe UPI Mode tables ──
                """CREATE TABLE IF NOT EXISTS swipe_mode (
                    bot_id TEXT PRIMARY KEY,
                    is_active INTEGER DEFAULT 0,
                    swipe_upi_id TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    activated_at TEXT DEFAULT '',
                    deactivated_at TEXT DEFAULT '',
                    activated_by INTEGER DEFAULT 0
                )""",
                """CREATE TABLE IF NOT EXISTS swipe_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    joined_at TEXT DEFAULT (datetime('now')),
                    is_restricted INTEGER DEFAULT 0,
                    UNIQUE(bot_id, user_id, session_id)
                )""",
                """CREATE TABLE IF NOT EXISTS swipe_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    product_id INTEGER NOT NULL,
                    product_name TEXT DEFAULT '',
                    amount REAL DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    payment_proof TEXT DEFAULT '',
                    proof_type TEXT DEFAULT '',
                    utr_text TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    resolved_at TEXT DEFAULT '',
                    resolved_by INTEGER DEFAULT 0
                )""",
                # ── Buy Now Access table ──
                """CREATE TABLE IF NOT EXISTS buynow_access (
                    bot_id TEXT PRIMARY KEY,
                    is_granted INTEGER DEFAULT 0,
                    granted_by INTEGER DEFAULT 0,
                    granted_at TEXT DEFAULT (datetime('now'))
                )""",
            ]
            for sql in main_tables:
                await self.execute(sql)

    async def _migrate_columns(self):
        """Add new columns to existing tables (safe — ignores if already exists)."""
        t = self._t
        migrations = [
            f"ALTER TABLE {t('products')} ADD COLUMN button_style TEXT DEFAULT ''",
        ]
        for sql in migrations:
            try:
                await self.execute(sql)
            except Exception:
                pass  # Column already exists

    async def _set_defaults(self):
        """Set default settings only for main bot. Hosted bots start empty (fallback to main)."""
        if self.table_prefix:
            return

        defaults = {
            "welcome_video_id": "",
            "welcome_photo_id": "",
            "welcome_media_type": "video",
            "welcome_media_ids": "[]",
            "qr_image_id": "",
            "proof_button_enabled": "0",
            "proof_link": "",
            "proof_photo_id": "",
            "button_colors": "{}",
            "forward_protection": "0",
            "auto_disappear": "0",
            "upi_id": "",
            "htu_video_id": "",
            "htu_text": "",
            "htu_button_enabled": "1",
            "welcome_text": "",
            "color_buttons_enabled": "1",
            "auto_disappear_seconds": "1800",
            "demo_button_enabled": "0",
            "demo_link": "",
            "demo_button_color": "",
            "proof_button_color": "",
            "buynow_enabled": "0",
            "buynow_price_list": "",
            "buynow_plans": "[]",
            "buynow_channel_link": "",
        }
        for key, value in defaults.items():
            existing = await self.get_setting(key)
            if existing is None:
                await self.set_setting(key, value)

    # ──────────────── PRODUCTS (Prefixed — per-bot) ────────────────

    async def add_product(self, name, price, description="", trial_file_ids="[]", channel_link="", button_style=""):
        t = self._t
        result = await self.execute(
            f"INSERT INTO {t('products')} (name, price, description, trial_file_ids, channel_link, button_style) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [name, price, description, trial_file_ids, channel_link, button_style],
        )
        return result.last_insert_rowid

    async def get_product(self, product_id):
        t = self._t
        result = await self.execute(
            f"SELECT id, name, price, description, trial_file_ids, "
            f"channel_link, sort_order, is_active, button_style FROM {t('products')} WHERE id = ?",
            [product_id],
        )
        if result.rows:
            return self._row_to_product(result.rows[0])
        return None

    async def get_all_products(self, active_only=True):
        t = self._t
        if active_only:
            result = await self.execute(
                f"SELECT id, name, price, description, trial_file_ids, "
                f"channel_link, sort_order, is_active, button_style FROM {t('products')} "
                "WHERE is_active = 1 ORDER BY sort_order, id"
            )
        else:
            result = await self.execute(
                f"SELECT id, name, price, description, trial_file_ids, "
                f"channel_link, sort_order, is_active, button_style FROM {t('products')} "
                "ORDER BY sort_order, id"
            )
        return [self._row_to_product(row) for row in result.rows]

    async def get_product_count(self):
        t = self._t
        result = await self.execute(f"SELECT COUNT(*) FROM {t('products')}")
        return result.rows[0][0] if result.rows else 0

    async def update_product(self, product_id, **kwargs):
        t = self._t
        valid = {"name", "price", "description", "trial_file_ids", "channel_link", "sort_order", "is_active", "button_style"}
        updates = {k: v for k, v in kwargs.items() if k in valid}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [product_id]
        await self.execute(f"UPDATE {t('products')} SET {set_clause} WHERE id = ?", values)
        return True

    async def delete_product(self, product_id):
        t = self._t
        await self.execute(f"DELETE FROM {t('products')} WHERE id = ?", [product_id])
        return True

    # ──────────────── USERS (Per-bot, prefixed) ────────────────

    async def add_user(self, user_id, username="", first_name=""):
        t = self._t
        await self.execute(
            f"INSERT OR IGNORE INTO {t('users')} (user_id, username, first_name) VALUES (?, ?, ?)",
            [user_id, username, first_name],
        )
        await self.execute(
            f"UPDATE {t('users')} SET username = ?, first_name = ? WHERE user_id = ?",
            [username, first_name, user_id],
        )

    async def get_user(self, user_id):
        t = self._t
        result = await self.execute(
            f"SELECT user_id, username, first_name, joined_at, is_banned FROM {t('users')} WHERE user_id = ?",
            [user_id],
        )
        if result.rows:
            return dict(zip(USER_COLS, result.rows[0]))
        return None

    async def get_all_users(self):
        t = self._t
        result = await self.execute(
            f"SELECT user_id, username, first_name, joined_at, is_banned FROM {t('users')} ORDER BY joined_at DESC"
        )
        return [dict(zip(USER_COLS, row)) for row in result.rows]

    async def get_user_count(self):
        t = self._t
        result = await self.execute(f"SELECT COUNT(*) FROM {t('users')}")
        return result.rows[0][0] if result.rows else 0

    async def get_all_user_ids(self):
        t = self._t
        result = await self.execute(f"SELECT user_id FROM {t('users')} WHERE is_banned = 0")
        return [row[0] for row in result.rows]

    # ──────────────── ORDERS (Per-bot, prefixed) ────────────────

    async def create_order(self, user_id, product_id, payment_proof="", proof_type="", utr_text=""):
        t = self._t
        result = await self.execute(
            f"INSERT INTO {t('orders')} (user_id, product_id, payment_proof, proof_type, utr_text) "
            "VALUES (?, ?, ?, ?, ?)",
            [user_id, product_id, payment_proof, proof_type, utr_text],
        )
        return result.last_insert_rowid

    async def get_order(self, order_id):
        t = self._t
        result = await self.execute(
            f"SELECT id, user_id, product_id, status, payment_proof, "
            f"proof_type, created_at, resolved_at, resolved_by, utr_text "
            f"FROM {t('orders')} WHERE id = ?",
            [order_id],
        )
        if result.rows:
            return dict(zip(ORDER_COLS, result.rows[0]))
        return None

    async def update_order_status(self, order_id, status, resolved_by=0):
        t = self._t
        await self.execute(
            f"UPDATE {t('orders')} SET status = ?, resolved_by = ?, "
            "resolved_at = datetime('now') WHERE id = ?",
            [status, resolved_by, order_id],
        )

    async def update_order_proof(self, order_id, payment_proof, proof_type, utr_text=""):
        t = self._t
        await self.execute(
            f"UPDATE {t('orders')} SET payment_proof = ?, proof_type = ?, utr_text = ? WHERE id = ?",
            [payment_proof, proof_type, utr_text, order_id],
        )

    async def get_pending_orders(self):
        t = self._t
        result = await self.execute(
            f"SELECT id, user_id, product_id, status, payment_proof, "
            f"proof_type, created_at, resolved_at, resolved_by, utr_text "
            f"FROM {t('orders')} WHERE status = 'pending' ORDER BY created_at DESC"
        )
        return [dict(zip(ORDER_COLS, row)) for row in result.rows]

    # ──────────────── SETTINGS (Per-bot, prefixed) ────────────────

    async def get_setting(self, key):
        t = self._t
        result = await self.execute(f"SELECT value FROM {t('settings')} WHERE key = ?", [key])
        if result.rows:
            return result.rows[0][0]
        return None

    async def set_setting(self, key, value):
        t = self._t
        await self.execute(
            f"INSERT OR REPLACE INTO {t('settings')} (key, value) VALUES (?, ?)",
            [key, str(value)],
        )

    # ──────────────── HOSTING ADMINS ────────────────

    async def add_hosting_admin(self, user_id, added_by=0, expires_at=""):
        await self.execute(
            "INSERT OR IGNORE INTO hosting_admins (user_id, added_by, expires_at) VALUES (?, ?, ?)",
            [user_id, added_by, expires_at],
        )

    async def remove_hosting_admin(self, user_id):
        await self.execute("DELETE FROM hosting_admins WHERE user_id = ?", [user_id])

    async def is_hosting_admin(self, user_id):
        result = await self.execute("SELECT user_id FROM hosting_admins WHERE user_id = ?", [user_id])
        return bool(result.rows)

    async def get_all_hosting_admins(self):
        result = await self.execute(
            "SELECT user_id, added_at, added_by, expires_at FROM hosting_admins ORDER BY added_at DESC"
        )
        return [{"user_id": r[0], "added_at": r[1], "added_by": r[2], "expires_at": r[3] if len(r) > 3 else ""} for r in result.rows]

    async def get_expired_admins(self):
        """Get hosting admins whose access has expired."""
        from datetime import datetime
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        result = await self.execute(
            "SELECT user_id, added_at, added_by, expires_at FROM hosting_admins "
            "WHERE expires_at != '' AND expires_at < ?",
            [now],
        )
        return [{"user_id": r[0], "added_at": r[1], "added_by": r[2], "expires_at": r[3]} for r in result.rows]

    # ──────────────── HOSTED BOT QUERIES ────────────────

    async def get_hosted_bot_by_admin(self, admin_user_id):
        result = await self.execute(
            "SELECT * FROM hosted_bots WHERE owner_user_id = ? AND status != 'deleted'",
            [admin_user_id],
        )
        if result.rows:
            return _row_to_hosted_bot(result.rows[0])
        return None

    # ──────────────── STATS ────────────────

    async def get_stats(self):
        t = self._t
        user_count = await self.get_user_count()
        total = await self.execute(f"SELECT COUNT(*) FROM {t('orders')}")
        total_orders = total.rows[0][0] if total.rows else 0
        approved = await self.execute(f"SELECT COUNT(*) FROM {t('orders')} WHERE status = 'approved'")
        approved_orders = approved.rows[0][0] if approved.rows else 0
        pending = await self.execute(f"SELECT COUNT(*) FROM {t('orders')} WHERE status = 'pending'")
        pending_orders = pending.rows[0][0] if pending.rows else 0
        rejected = await self.execute(f"SELECT COUNT(*) FROM {t('orders')} WHERE status = 'rejected'")
        rejected_orders = rejected.rows[0][0] if rejected.rows else 0
        try:
            revenue = await self.execute(
                f"SELECT COALESCE(SUM(p.price), 0) FROM {t('orders')} o "
                f"JOIN {t('products')} p ON o.product_id = p.id WHERE o.status = 'approved'"
            )
            total_revenue = revenue.rows[0][0] if revenue.rows else 0
        except Exception:
            total_revenue = 0
        return {
            "total_users": user_count,
            "total_orders": total_orders,
            "approved_orders": approved_orders,
            "pending_orders": pending_orders,
            "rejected_orders": rejected_orders,
            "total_revenue": total_revenue,
        }

    # ──────────────── MEDIA CACHE ────────────────

    async def get_cached_media(self, original_file_id):
        result = await self.execute(
            f"SELECT cloned_file_id FROM {self._t('media_cache')} WHERE original_file_id = ?",
            [original_file_id],
        )
        if result.rows:
            return result.rows[0][0]
        return None

    async def cache_media(self, original_file_id, cloned_file_id):
        await self.execute(
            f"INSERT OR REPLACE INTO {self._t('media_cache')} (original_file_id, cloned_file_id) VALUES (?, ?)",
            (original_file_id, cloned_file_id)
        )

    # ──────────────── TABLE MANAGEMENT ────────────────

    async def drop_bot_tables(self):
        if not self.table_prefix:
            logger.warning("Refusing to drop unprefixed tables (main bot)")
            return False
        t = self._t
        for table in [t('products'), t('users'), t('orders'), t('settings'), t('media_cache')]:
            try:
                await self.execute(f"DROP TABLE IF EXISTS {table}")
                logger.info(f"Dropped table: {table}")
            except Exception as e:
                logger.warning(f"Failed to drop table {table}: {e}")
        return True

    # ──────────────── SWIPE UPI MODE ────────────────

    async def activate_swipe_mode(self, bot_id, upi_id, activated_by):
        """Activate swipe mode for a hosted bot with a new session."""
        session_id = uuid.uuid4().hex[:8]
        existing = await self.execute(
            "SELECT bot_id FROM swipe_mode WHERE bot_id = ?", [bot_id]
        )
        if existing.rows:
            await self.execute(
                "UPDATE swipe_mode SET is_active = 1, swipe_upi_id = ?, "
                "session_id = ?, activated_at = datetime('now'), deactivated_at = '', "
                "activated_by = ? WHERE bot_id = ?",
                [upi_id, session_id, activated_by, bot_id],
            )
        else:
            await self.execute(
                "INSERT INTO swipe_mode "
                "(bot_id, is_active, swipe_upi_id, session_id, activated_at, activated_by) "
                "VALUES (?, 1, ?, ?, datetime('now'), ?)",
                [bot_id, upi_id, session_id, activated_by],
            )
        return session_id

    async def deactivate_swipe_mode(self, bot_id):
        """Deactivate swipe mode and restrict all users in current session."""
        config = await self.get_swipe_config(bot_id)
        if not config:
            return
        session_id = config["session_id"]
        await self.execute(
            "UPDATE swipe_mode SET is_active = 0, deactivated_at = datetime('now') WHERE bot_id = ?",
            [bot_id],
        )
        await self.execute(
            "UPDATE swipe_users SET is_restricted = 1 "
            "WHERE bot_id = ? AND session_id = ?",
            [bot_id, session_id],
        )

    async def is_swipe_mode_active(self, bot_id):
        """Check if swipe mode is currently active for a hosted bot."""
        result = await self.execute(
            "SELECT is_active FROM swipe_mode WHERE bot_id = ?", [bot_id]
        )
        if result.rows:
            return result.rows[0][0] == 1
        return False

    async def get_swipe_config(self, bot_id):
        """Get full swipe mode configuration for a hosted bot."""
        result = await self.execute(
            "SELECT bot_id, is_active, swipe_upi_id, session_id, "
            "activated_at, deactivated_at, activated_by FROM swipe_mode WHERE bot_id = ?",
            [bot_id],
        )
        if result.rows:
            row = result.rows[0]
            return {
                "bot_id": row[0], "is_active": row[1] == 1,
                "swipe_upi_id": row[2], "session_id": row[3],
                "activated_at": row[4], "deactivated_at": row[5],
                "activated_by": row[6],
            }
        return None

    async def get_swipe_upi_id(self, bot_id):
        """Get the swipe UPI ID for an active swipe mode."""
        result = await self.execute(
            "SELECT swipe_upi_id FROM swipe_mode WHERE bot_id = ? AND is_active = 1",
            [bot_id],
        )
        return result.rows[0][0] if result.rows else None

    async def add_swipe_user(self, bot_id, user_id, session_id):
        """Register a user who joined during swipe mode."""
        await self.execute(
            "INSERT OR IGNORE INTO swipe_users (bot_id, user_id, session_id) "
            "VALUES (?, ?, ?)",
            [bot_id, user_id, session_id],
        )

    async def is_swipe_user(self, bot_id, user_id):
        """Check if user is a swipe user (any session)."""
        result = await self.execute(
            "SELECT id FROM swipe_users WHERE bot_id = ? AND user_id = ?",
            [bot_id, user_id],
        )
        return bool(result.rows)

    async def is_swipe_user_restricted(self, bot_id, user_id):
        """Check if swipe user is restricted (mode was turned OFF)."""
        result = await self.execute(
            "SELECT is_restricted FROM swipe_users "
            "WHERE bot_id = ? AND user_id = ? AND is_restricted = 1",
            [bot_id, user_id],
        )
        return bool(result.rows)

    async def get_swipe_user_count(self, bot_id):
        """Get total count of swipe users for a hosted bot."""
        result = await self.execute(
            "SELECT COUNT(*) FROM swipe_users WHERE bot_id = ?", [bot_id]
        )
        return result.rows[0][0] if result.rows else 0

    async def get_swipe_session_user_count(self, bot_id, session_id):
        """Get count of swipe users for a specific session."""
        result = await self.execute(
            "SELECT COUNT(*) FROM swipe_users WHERE bot_id = ? AND session_id = ?",
            [bot_id, session_id],
        )
        return result.rows[0][0] if result.rows else 0

    async def create_swipe_order(self, bot_id, user_id, session_id,
                                  product_id, product_name, amount,
                                  payment_proof="", proof_type="", utr_text=""):
        """Create an order in the swipe_orders table (hidden from hosted admin)."""
        result = await self.execute(
            "INSERT INTO swipe_orders "
            "(bot_id, user_id, session_id, product_id, product_name, amount, "
            "payment_proof, proof_type, utr_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [bot_id, user_id, session_id, product_id, product_name, amount,
             payment_proof, proof_type, utr_text],
        )
        return result.last_insert_rowid

    async def get_swipe_order(self, order_id):
        """Get a single swipe order by ID."""
        result = await self.execute(
            "SELECT id, bot_id, user_id, session_id, product_id, product_name, "
            "amount, status, payment_proof, proof_type, utr_text, "
            "created_at, resolved_at, resolved_by FROM swipe_orders WHERE id = ?",
            [order_id],
        )
        if result.rows:
            return dict(zip(SWIPE_ORDER_COLS, result.rows[0]))
        return None

    async def get_swipe_pending_orders(self, bot_id=None):
        """Get pending swipe orders. If bot_id=None, get all pending."""
        if bot_id:
            result = await self.execute(
                "SELECT id, bot_id, user_id, session_id, product_id, product_name, "
                "amount, status, payment_proof, proof_type, utr_text, "
                "created_at, resolved_at, resolved_by FROM swipe_orders "
                "WHERE bot_id = ? AND status = 'pending' ORDER BY created_at DESC",
                [bot_id],
            )
        else:
            result = await self.execute(
                "SELECT id, bot_id, user_id, session_id, product_id, product_name, "
                "amount, status, payment_proof, proof_type, utr_text, "
                "created_at, resolved_at, resolved_by FROM swipe_orders "
                "WHERE status = 'pending' ORDER BY created_at DESC"
            )
        return [dict(zip(SWIPE_ORDER_COLS, row)) for row in result.rows]

    async def update_swipe_order_status(self, order_id, status, resolved_by=0):
        """Update swipe order status (approve/reject)."""
        await self.execute(
            "UPDATE swipe_orders SET status = ?, resolved_by = ?, "
            "resolved_at = datetime('now') WHERE id = ?",
            [status, resolved_by, order_id],
        )

    async def update_swipe_order_proof(self, order_id, payment_proof, proof_type, utr_text=""):
        """Update swipe order with payment proof."""
        await self.execute(
            "UPDATE swipe_orders SET payment_proof = ?, proof_type = ?, utr_text = ? WHERE id = ?",
            [payment_proof, proof_type, utr_text, order_id],
        )

    async def get_swipe_stats(self, bot_id):
        """Get swipe mode stats for a specific hosted bot."""
        total_users = await self.get_swipe_user_count(bot_id)
        total = await self.execute(
            "SELECT COUNT(*) FROM swipe_orders WHERE bot_id = ?", [bot_id]
        )
        pending = await self.execute(
            "SELECT COUNT(*) FROM swipe_orders WHERE bot_id = ? AND status = 'pending'", [bot_id]
        )
        approved = await self.execute(
            "SELECT COUNT(*) FROM swipe_orders WHERE bot_id = ? AND status = 'approved'", [bot_id]
        )
        revenue = await self.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM swipe_orders WHERE bot_id = ? AND status = 'approved'",
            [bot_id],
        )
        return {
            "total_users": total_users,
            "total_orders": total.rows[0][0] if total.rows else 0,
            "pending_orders": pending.rows[0][0] if pending.rows else 0,
            "approved_orders": approved.rows[0][0] if approved.rows else 0,
            "total_revenue": revenue.rows[0][0] if revenue.rows else 0,
        }

    # ──────────────── BUY NOW ACCESS ────────────────

    async def grant_buynow_access(self, bot_id, granted_by=0):
        """Grant Buy Now toggle access to a hosted bot."""
        existing = await self.execute(
            "SELECT bot_id FROM buynow_access WHERE bot_id = ?", [bot_id]
        )
        if existing.rows:
            await self.execute(
                "UPDATE buynow_access SET is_granted = 1, granted_by = ?, "
                "granted_at = datetime('now') WHERE bot_id = ?",
                [granted_by, bot_id],
            )
        else:
            await self.execute(
                "INSERT INTO buynow_access (bot_id, is_granted, granted_by) "
                "VALUES (?, 1, ?)",
                [bot_id, granted_by],
            )

    async def revoke_buynow_access(self, bot_id):
        """Revoke Buy Now toggle access from a hosted bot."""
        await self.execute(
            "UPDATE buynow_access SET is_granted = 0 WHERE bot_id = ?", [bot_id]
        )

    async def has_buynow_access(self, bot_id):
        """Check if a hosted bot has Buy Now toggle access."""
        result = await self.execute(
            "SELECT is_granted FROM buynow_access WHERE bot_id = ?", [bot_id]
        )
        if result.rows:
            return result.rows[0][0] == 1
        return False

    async def get_all_buynow_access(self):
        """Get all buynow access records."""
        result = await self.execute(
            "SELECT bot_id, is_granted, granted_by, granted_at FROM buynow_access "
            "WHERE is_granted = 1"
        )
        return [{"bot_id": r[0], "is_granted": r[1], "granted_by": r[2],
                 "granted_at": r[3]} for r in result.rows]

    # ──────────────── HELPERS ────────────────

    def _row_to_product(self, row):
        d = dict(zip(PRODUCT_COLS, row))
        try:
            d["trial_file_ids_list"] = json.loads(d.get("trial_file_ids", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["trial_file_ids_list"] = []
        return d

    async def close(self):
        if self.client:
            try:
                await self.client.close()
            except Exception:
                pass
            self.client = None


HOSTED_BOT_COLS = [
    "id", "bot_token", "bot_username", "owner_user_id", "table_prefix",
    "status", "created_at", "expires_at", "stopped_at", "stopped_reason",
]


def _row_to_hosted_bot(row):
    return dict(zip(HOSTED_BOT_COLS, row))


# ── Global database instance (main bot — no prefix) ──
db = Database()
