"""
Host Bot — Swipe UPI Mode Handler
Allows OWNER_ID to activate/deactivate swipe mode on hosted bots.
Manages swipe UPI, swipe orders, and swipe stats from main bot admin panel.
"""
import html
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

from config import OWNER_ID
from utils.bot_helpers import get_main_db
from utils.helpers import format_price
from database import _row_to_hosted_bot

logger = logging.getLogger(__name__)

# ── Conversation States (unique range 40-44) ──
SWIPE_MENU = 40
SWIPE_UPI_INPUT = 42


async def swipe_menu(update, context):
    """Show list of hosted bots with swipe mode status."""
    query = update.callback_query
    main_db = get_main_db(context)

    result = await main_db.execute(
        "SELECT * FROM hosted_bots WHERE status != 'deleted' ORDER BY created_at DESC"
    )
    if not result.rows:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]
        await query.edit_message_text(
            "🔄 <b>Swipe UPI Mode</b>\n\n❌ No hosted bots found.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return 0  # ADMIN_MENU

    keyboard = []
    for row in result.rows:
        bot = _row_to_hosted_bot(row)
        swipe_config = await main_db.get_swipe_config(bot["id"])
        status = "🟢 ON" if (swipe_config and swipe_config["is_active"]) else "🔴 OFF"
        keyboard.append([
            InlineKeyboardButton(
                f"@{bot['bot_username']} — Swipe {status}",
                callback_data=f"swp_bot_{bot['id']}",
            )
        ])

    # Show all pending swipe orders count
    all_pending = await main_db.get_swipe_pending_orders()
    if all_pending:
        keyboard.append([
            InlineKeyboardButton(
                f"📋 All Swipe Pending Orders ({len(all_pending)})",
                callback_data="swp_all_pending",
            )
        ])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])
    await query.edit_message_text(
        "🔄 <b>Swipe UPI Mode</b>\n\nSelect a hosted bot to manage swipe mode:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return SWIPE_MENU


async def swipe_menu_handler(update, context):
    """Handle swipe menu callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("swp_bot_"):
        bot_id = data.replace("swp_bot_", "")
        return await _show_swipe_actions(update, context, bot_id)
    elif data == "swp_all_pending":
        return await _show_all_swipe_pending(update, context)
    elif data == "adm_back":
        from handlers.admin import _show_admin_menu
        return await _show_admin_menu(update, context)
    elif data.startswith("swp_activate_"):
        bot_id = data.replace("swp_activate_", "")
        context.user_data["swipe_target_bot"] = bot_id
        await query.edit_message_text(
            "💳 <b>Set Swipe UPI ID</b>\n\n"
            "Enter the UPI ID to use during swipe mode:\n\n"
            "<i>This UPI will be shown to all new users joining this bot.</i>",
            parse_mode="HTML",
        )
        return SWIPE_UPI_INPUT
    elif data.startswith("swp_deactivate_"):
        bot_id = data.replace("swp_deactivate_", "")
        return await _deactivate_swipe(update, context, bot_id)
    elif data.startswith("swp_stats_"):
        bot_id = data.replace("swp_stats_", "")
        return await _show_swipe_stats(update, context, bot_id)
    elif data.startswith("swp_pending_"):
        bot_id = data.replace("swp_pending_", "")
        return await _show_bot_swipe_pending(update, context, bot_id)
    elif data == "swp_back":
        return await swipe_menu(update, context)
    elif data == "adm_swipe":
        return await swipe_menu(update, context)

    return SWIPE_MENU


async def _show_swipe_actions(update, context, bot_id):
    """Show swipe mode actions for a specific bot."""
    query = update.callback_query
    main_db = get_main_db(context)

    result = await main_db.execute(
        "SELECT * FROM hosted_bots WHERE id = ?", [bot_id]
    )
    if not result.rows:
        await query.edit_message_text("❌ Bot not found.")
        return SWIPE_MENU

    bot = _row_to_hosted_bot(result.rows[0])
    safe_username = html.escape(bot['bot_username'])
    swipe_config = await main_db.get_swipe_config(bot_id)

    if swipe_config and swipe_config["is_active"]:
        session_count = await main_db.get_swipe_session_user_count(
            bot_id, swipe_config["session_id"]
        )
        text = (
            f"🔄 <b>Swipe Mode — @{safe_username}</b>\n\n"
            f"📡 Status: 🟢 <b>ACTIVE</b>\n"
            f"💳 Swipe UPI: <code>{html.escape(swipe_config['swipe_upi_id'])}</code>\n"
            f"🔑 Session: <code>{html.escape(swipe_config['session_id'])}</code>\n"
            f"👥 Users this session: <b>{session_count}</b>\n"
            f"📅 Activated: {html.escape(swipe_config['activated_at'] or '')}"
        )
        keyboard = [
            [InlineKeyboardButton("❌ Deactivate Swipe Mode", callback_data=f"swp_deactivate_{bot_id}")],
            [InlineKeyboardButton("📋 View Swipe Pending Orders", callback_data=f"swp_pending_{bot_id}")],
            [InlineKeyboardButton("📊 Swipe Stats", callback_data=f"swp_stats_{bot_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="swp_back")],
        ]
    else:
        total_swipe_users = await main_db.get_swipe_user_count(bot_id)
        text = (
            f"🔄 <b>Swipe Mode — @{safe_username}</b>\n\n"
            f"📡 Status: 🔴 <b>INACTIVE</b>\n"
            f"👥 Total swipe users (all time): <b>{total_swipe_users}</b>"
        )
        if swipe_config and swipe_config.get("deactivated_at"):
            text += f"\n📅 Last deactivated: {html.escape(swipe_config['deactivated_at'] or '')}"
        keyboard = [
            [InlineKeyboardButton("✅ Activate Swipe Mode", callback_data=f"swp_activate_{bot_id}")],
            [InlineKeyboardButton("📊 Swipe Stats", callback_data=f"swp_stats_{bot_id}")],
            [InlineKeyboardButton("📋 View Swipe Pending Orders", callback_data=f"swp_pending_{bot_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="swp_back")],
        ]

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML",
    )
    return SWIPE_MENU


async def swipe_upi_input(update, context):
    """Receive UPI ID and activate swipe mode."""
    upi = update.message.text.strip()
    if "@" not in upi:
        await update.message.reply_text(
            "❌ Invalid UPI ID. Must contain @\n\nExample: <code>name@upi</code>",
            parse_mode="HTML",
        )
        return SWIPE_UPI_INPUT

    bot_id = context.user_data.get("swipe_target_bot")
    if not bot_id:
        await update.message.reply_text("❌ Error: No target bot. Try again from /admin.")
        return 0  # ADMIN_MENU

    main_db = get_main_db(context)
    session_id = await main_db.activate_swipe_mode(bot_id, upi, OWNER_ID)
    context.user_data.pop("swipe_target_bot", None)

    result = await main_db.execute("SELECT bot_username FROM hosted_bots WHERE id = ?", [bot_id])
    bot_username = result.rows[0][0] if result.rows else bot_id

    keyboard = [[InlineKeyboardButton("🔙 Back to Swipe Menu", callback_data="adm_swipe")]]
    await update.message.reply_text(
        f"✅ <b>Swipe Mode Activated!</b>\n\n"
        f"🤖 Bot: @{html.escape(bot_username)}\n"
        f"💳 UPI: <code>{html.escape(upi)}</code>\n"
        f"🔑 Session: <code>{html.escape(session_id)}</code>\n\n"
        f"All new users joining this bot will now see your UPI.\n"
        f"Their payments will come to you directly.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return 0  # ADMIN_MENU


async def _deactivate_swipe(update, context, bot_id):
    """Deactivate swipe mode and restrict users."""
    query = update.callback_query
    main_db = get_main_db(context)

    config = await main_db.get_swipe_config(bot_id)
    if not config or not config["is_active"]:
        await query.answer("Mode is already OFF.", show_alert=True)
        return SWIPE_MENU

    session_count = await main_db.get_swipe_session_user_count(
        bot_id, config["session_id"]
    )
    await main_db.deactivate_swipe_mode(bot_id)

    result = await main_db.execute("SELECT bot_username FROM hosted_bots WHERE id = ?", [bot_id])
    bot_username = result.rows[0][0] if result.rows else bot_id

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="swp_back")]]
    await query.edit_message_text(
        f"❌ <b>Swipe Mode Deactivated</b>\n\n"
        f"🤖 Bot: @{html.escape(bot_username)}\n"
        f"👥 Users restricted: <b>{session_count}</b>\n\n"
        f"All swipe users are now restricted from buying.\n"
        f"They can still browse products and see the hosted bot's UPI.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return SWIPE_MENU


async def _show_swipe_stats(update, context, bot_id):
    """Show swipe stats for a specific bot."""
    query = update.callback_query
    main_db = get_main_db(context)

    stats = await main_db.get_swipe_stats(bot_id)
    result = await main_db.execute("SELECT bot_username FROM hosted_bots WHERE id = ?", [bot_id])
    bot_username = result.rows[0][0] if result.rows else bot_id

    text = (
        f"📊 <b>Swipe Stats — @{html.escape(bot_username)}</b>\n\n"
        f"👥 Total Swipe Users: <b>{stats['total_users']}</b>\n\n"
        f"📦 Total Swipe Orders: <b>{stats['total_orders']}</b>\n"
        f"✅ Approved: <b>{stats['approved_orders']}</b>\n"
        f"⏳ Pending: <b>{stats['pending_orders']}</b>\n\n"
        f"💰 Swipe Revenue: <b>{format_price(stats['total_revenue'])}</b>"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"swp_bot_{bot_id}")]]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML",
    )
    return SWIPE_MENU


async def _show_bot_swipe_pending(update, context, bot_id):
    """Show pending swipe orders for a specific bot."""
    query = update.callback_query
    main_db = get_main_db(context)
    chat_id = query.message.chat_id

    orders = await main_db.get_swipe_pending_orders(bot_id)
    if not orders:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"swp_bot_{bot_id}")]]
        await query.edit_message_text(
            "📋 <b>Swipe Pending Orders</b>\n\n✅ No pending swipe orders!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return SWIPE_MENU

    try:
        await query.message.delete()
    except Exception:
        pass

    for order in orders[:10]:
        order_text = (
            f"🔔 <b>Swipe Order #{order['id']}</b>\n\n"
            f"👤 User ID: <code>{order['user_id']}</code>\n"
            f"📦 Plan: <b>{html.escape(order.get('product_name', 'Unknown'))}</b>\n"
            f"💰 Price: <b>{format_price(order['amount'])}</b>\n"
            f"📝 Proof: {(order.get('proof_type') or 'none').upper()}"
        )
        keyboard = [[
            InlineKeyboardButton("✅ Approve", callback_data=f"swipe_approve_{order['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"swipe_reject_{order['id']}"),
        ]]
        try:
            if order.get("proof_type") == "screenshot" and order.get("payment_proof"):
                await context.bot.send_photo(
                    chat_id=chat_id, photo=order["payment_proof"],
                    caption=order_text, reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id, text=order_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.warning(f"Failed to show swipe order #{order['id']}: {e}")

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"swp_bot_{bot_id}")]]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📋 Showing {min(len(orders), 10)} of {len(orders)} pending swipe orders.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SWIPE_MENU


async def _show_all_swipe_pending(update, context):
    """Show all pending swipe orders across all bots."""
    query = update.callback_query
    main_db = get_main_db(context)
    chat_id = query.message.chat_id

    orders = await main_db.get_swipe_pending_orders()
    if not orders:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_swipe")]]
        await query.edit_message_text(
            "📋 <b>All Swipe Pending Orders</b>\n\n✅ No pending orders!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return SWIPE_MENU

    try:
        await query.message.delete()
    except Exception:
        pass

    for order in orders[:15]:
        order_text = (
            f"🔔 <b>Swipe Order #{order['id']}</b>\n\n"
            f"🤖 Bot ID: <code>{html.escape(str(order['bot_id']))}</code>\n"
            f"👤 User ID: <code>{order['user_id']}</code>\n"
            f"📦 Plan: <b>{html.escape(order.get('product_name', 'Unknown'))}</b>\n"
            f"💰 Price: <b>{format_price(order['amount'])}</b>\n"
            f"📝 Proof: {(order.get('proof_type') or 'none').upper()}"
        )
        keyboard = [[
            InlineKeyboardButton("✅ Approve", callback_data=f"swipe_approve_{order['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"swipe_reject_{order['id']}"),
        ]]
        try:
            if order.get("proof_type") == "screenshot" and order.get("payment_proof"):
                await context.bot.send_photo(
                    chat_id=chat_id, photo=order["payment_proof"],
                    caption=order_text, reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id, text=order_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
        except Exception:
            pass

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_swipe")]]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📋 Showing {min(len(orders), 15)} of {len(orders)} total pending swipe orders.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SWIPE_MENU


def get_swipe_states():
    """Return swipe mode conversation states for admin ConversationHandler."""
    return {
        SWIPE_MENU: [
            CallbackQueryHandler(swipe_menu_handler, pattern=r"^swp_|^adm_swipe$|^adm_back$"),
        ],
        SWIPE_UPI_INPUT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, swipe_upi_input),
        ],
    }
