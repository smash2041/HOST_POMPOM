"""
Host Bot — Buy Now Access Mode Handler
Allows OWNER_ID to grant/revoke Buy Now toggle access to hosted bots.
Managed from main bot admin panel, similar to swipe_mode.
"""
import html
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from config import OWNER_ID
from utils.bot_helpers import get_main_db
from database import _row_to_hosted_bot

logger = logging.getLogger(__name__)

# ── Conversation States (unique range 55) ──
BUYNOW_ACCESS_MENU = 55


async def buynow_access_menu(update, context):
    """Show list of hosted bots with Buy Now access status."""
    query = update.callback_query
    main_db = get_main_db(context)

    result = await main_db.execute(
        "SELECT * FROM hosted_bots WHERE status != 'deleted' ORDER BY created_at DESC"
    )
    if not result.rows:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]
        await query.edit_message_text(
            "💰 <b>Buy Now Access</b>\n\n❌ No hosted bots found.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return 0  # ADMIN_MENU

    keyboard = []
    for row in result.rows:
        bot = _row_to_hosted_bot(row)
        has_access = await main_db.has_buynow_access(bot["id"])
        status = "🟢 ON" if has_access else "🔴 OFF"
        keyboard.append([
            InlineKeyboardButton(
                f"@{bot['bot_username']} — Buy Now {status}",
                callback_data=f"bna_bot_{bot['id']}",
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])
    await query.edit_message_text(
        "💰 <b>Buy Now Access</b>\n\nSelect a hosted bot to grant/revoke Buy Now toggle access:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return BUYNOW_ACCESS_MENU


async def buynow_access_handler(update, context):
    """Handle Buy Now access menu callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("bna_bot_"):
        bot_id = data.replace("bna_bot_", "")
        return await _show_buynow_bot_actions(update, context, bot_id)
    elif data.startswith("bna_grant_"):
        bot_id = data.replace("bna_grant_", "")
        main_db = get_main_db(context)
        await main_db.grant_buynow_access(bot_id, granted_by=OWNER_ID)
        await query.answer("✅ Buy Now access granted!", show_alert=True)
        return await _show_buynow_bot_actions(update, context, bot_id)
    elif data.startswith("bna_revoke_"):
        bot_id = data.replace("bna_revoke_", "")
        main_db = get_main_db(context)
        await main_db.revoke_buynow_access(bot_id)
        await query.answer("❌ Buy Now access revoked!", show_alert=True)
        return await _show_buynow_bot_actions(update, context, bot_id)
    elif data == "bna_back":
        return await buynow_access_menu(update, context)
    elif data == "adm_back":
        from handlers.admin import _show_admin_menu
        return await _show_admin_menu(update, context)

    return BUYNOW_ACCESS_MENU


async def _show_buynow_bot_actions(update, context, bot_id):
    """Show Buy Now access actions for a specific bot."""
    query = update.callback_query
    main_db = get_main_db(context)

    result = await main_db.execute(
        "SELECT * FROM hosted_bots WHERE id = ?", [bot_id]
    )
    if not result.rows:
        await query.edit_message_text("❌ Bot not found.")
        return BUYNOW_ACCESS_MENU

    bot = _row_to_hosted_bot(result.rows[0])
    safe_username = html.escape(bot['bot_username'])
    has_access = await main_db.has_buynow_access(bot_id)

    if has_access:
        text = (
            f"💰 <b>Buy Now Access — @{safe_username}</b>\n\n"
            f"📡 Status: 🟢 <b>GRANTED</b>\n\n"
            f"The hosted bot admin can now enable the Buy Now toggle "
            f"and configure plans from their admin panel."
        )
        keyboard = [
            [InlineKeyboardButton("❌ Revoke Buy Now Access", callback_data=f"bna_revoke_{bot_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="bna_back")],
        ]
    else:
        text = (
            f"💰 <b>Buy Now Access — @{safe_username}</b>\n\n"
            f"📡 Status: 🔴 <b>NOT GRANTED</b>\n\n"
            f"Grant access to allow the hosted bot admin to use the Buy Now feature."
        )
        keyboard = [
            [InlineKeyboardButton("✅ Grant Buy Now Access", callback_data=f"bna_grant_{bot_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="bna_back")],
        ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML",
    )
    return BUYNOW_ACCESS_MENU


def get_buynow_states():
    """Return Buy Now access conversation states for admin ConversationHandler."""
    return {
        BUYNOW_ACCESS_MENU: [
            CallbackQueryHandler(buynow_access_handler, pattern=r"^bna_|^adm_back$"),
        ],
    }
