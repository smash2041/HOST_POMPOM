"""
Host Bot — Database Migration Script
Migrates data from local SQLite (host-pompom.db) to Turso DB.
Run once: python migrate_db.py
"""
import os
import sys
import sqlite3
import asyncio
import logging

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from database import Database

SOURCE_DB = os.path.join(os.path.dirname(__file__), "host-pompom.db")


async def migrate():
    """Main migration function."""
    if not os.path.exists(SOURCE_DB):
        logger.error(f"Source database not found: {SOURCE_DB}")
        sys.exit(1)

    # Connect to source SQLite
    src = sqlite3.connect(SOURCE_DB)
    src.row_factory = sqlite3.Row
    cursor = src.cursor()

    # Connect to target Turso DB
    target = Database()
    await target.init()
    logger.info("✅ Connected to Turso DB and initialized tables")

    # ── 1. Migrate main bot tables ──
    await _migrate_table(cursor, target, "products",
        "INSERT OR REPLACE INTO products (id, name, price, description, trial_file_ids, channel_link, sort_order, is_active, button_style) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["id", "name", "price", "description", "trial_file_ids", "channel_link", "sort_order", "is_active", "button_style"],
    )

    await _migrate_table(cursor, target, "settings",
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ["key", "value"],
    )

    await _migrate_table(cursor, target, "users",
        "INSERT OR REPLACE INTO users (user_id, username, first_name, joined_at, is_banned) VALUES (?, ?, ?, ?, ?)",
        ["user_id", "username", "first_name", "joined_at", "is_banned"],
    )

    await _migrate_table(cursor, target, "orders",
        "INSERT OR REPLACE INTO orders (id, user_id, product_id, status, payment_proof, proof_type, created_at, resolved_at, resolved_by, utr_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["id", "user_id", "product_id", "status", "payment_proof", "proof_type", "created_at", "resolved_at", "resolved_by", "utr_text"],
    )

    # ── 2. Migrate hosted_bots ──
    await _migrate_table(cursor, target, "hosted_bots",
        "INSERT OR REPLACE INTO hosted_bots (id, bot_token, bot_username, owner_user_id, table_prefix, status, created_at, expires_at, stopped_at, stopped_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["id", "bot_token", "bot_username", "owner_user_id", "table_prefix", "status", "created_at", "expires_at", "stopped_at", "stopped_reason"],
    )

    # ── 3. Migrate hosting_logs ──
    await _migrate_table(cursor, target, "hosting_logs",
        "INSERT OR REPLACE INTO hosting_logs (id, bot_id, action, performed_by, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ["id", "bot_id", "action", "performed_by", "details", "created_at"],
    )

    # ── 4. Create hosting_admins from hosted_bots owner_user_ids ──
    logger.info("Creating hosting_admins from hosted_bots owner_user_ids...")
    cursor.execute("SELECT DISTINCT owner_user_id FROM hosted_bots WHERE status != 'deleted'")
    admin_rows = cursor.fetchall()
    for row in admin_rows:
        owner_id = row[0]
        await target.execute(
            "INSERT OR IGNORE INTO hosting_admins (user_id, added_by, expires_at) VALUES (?, 0, '')",
            [owner_id],
        )
        logger.info(f"  Added admin: {owner_id}")
    logger.info(f"✅ hosting_admins: {len(admin_rows)} admins created")

    # ── 5. Migrate all hb_* prefixed tables ──
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hb_%' ORDER BY name")
    hb_tables = [row[0] for row in cursor.fetchall()]

    for table_name in hb_tables:
        # Determine table type from suffix
        if table_name.endswith("_products"):
            sql = f"INSERT OR REPLACE INTO {table_name} (id, name, price, description, trial_file_ids, channel_link, sort_order, is_active, button_style) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            cols = ["id", "name", "price", "description", "trial_file_ids", "channel_link", "sort_order", "is_active", "button_style"]
        elif table_name.endswith("_settings"):
            sql = f"INSERT OR REPLACE INTO {table_name} (key, value) VALUES (?, ?)"
            cols = ["key", "value"]
        elif table_name.endswith("_users"):
            sql = f"INSERT OR REPLACE INTO {table_name} (user_id, username, first_name, joined_at, is_banned) VALUES (?, ?, ?, ?, ?)"
            cols = ["user_id", "username", "first_name", "joined_at", "is_banned"]
        elif table_name.endswith("_orders"):
            sql = f"INSERT OR REPLACE INTO {table_name} (id, user_id, product_id, status, payment_proof, proof_type, created_at, resolved_at, resolved_by, utr_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            cols = ["id", "user_id", "product_id", "status", "payment_proof", "proof_type", "created_at", "resolved_at", "resolved_by", "utr_text"]
        elif table_name.endswith("_media_cache"):
            sql = f"INSERT OR REPLACE INTO {table_name} (original_file_id, cloned_file_id) VALUES (?, ?)"
            cols = ["original_file_id", "cloned_file_id"]
        else:
            logger.warning(f"  Skipping unknown table: {table_name}")
            continue

        # Create the table first (Turso needs it to exist)
        # Extract prefix from table name
        parts = table_name.rsplit("_", 1)
        if len(parts) == 2:
            # Simple suffix like _products, _users etc
            pass
        # For hb_XXXX_products -> prefix is hb_XXXX_
        prefix = table_name.rsplit("_", 1)[0] + "_"
        # Actually need smarter parsing: hb_2ce90b8c_products -> prefix = hb_2ce90b8c_
        # The table_name format is: {prefix}{table_type}
        # prefix is like hb_2ce90b8c_
        # table_type is like products, settings, users, orders, media_cache
        for suffix in ["products", "settings", "users", "orders", "media_cache"]:
            if table_name.endswith(f"_{suffix}"):
                prefix = table_name[: len(table_name) - len(suffix)]
                break

        # Create the per-bot tables using Database class
        temp_db = Database(table_prefix=prefix)
        await temp_db.connect()
        await temp_db._create_tables()
        await temp_db.close()

        await _migrate_table(cursor, target, table_name, sql, cols)

    src.close()
    await target.close()
    logger.info("="*50)
    logger.info("🎉 Migration complete! All data transferred to Turso DB.")
    logger.info("="*50)


async def _migrate_table(cursor, target_db, table_name, insert_sql, columns):
    """Migrate a single table from SQLite to Turso."""
    try:
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()
    except Exception as e:
        logger.warning(f"  Skipping {table_name}: {e}")
        return

    if not rows:
        logger.info(f"  {table_name}: 0 rows (empty)")
        return

    success = 0
    failed = 0
    for row in rows:
        try:
            values = [row[col] for col in columns]
            await target_db.execute(insert_sql, values)
            success += 1
        except Exception as e:
            failed += 1
            if failed <= 3:
                logger.warning(f"  {table_name} row failed: {e}")

    status = "✅" if failed == 0 else "⚠️"
    logger.info(f"  {status} {table_name}: {success}/{len(rows)} migrated" + (f" ({failed} failed)" if failed else ""))


if __name__ == "__main__":
    print("🚀 Starting database migration: host-pompom.db → Turso DB")
    print("="*50)
    asyncio.run(migrate())
