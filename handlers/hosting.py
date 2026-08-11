"""
VIP Access Bot — Hosting Management UI
Admin panel for managing hosted bots.
Only accessible to OWNER_ID on the main bot.
"""
import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from utils.bot_helpers import is_main_bot
from config import OWNER_ID

logger = logging.getLogger(__name__)

# Import ADMIN_MENU as constant (not from admin.py to avoid circular imports)
ADMIN_MENU = 0

# ConversationHandler states (start from 20 to avoid conflict with admin states 0-19)
(
    HOST_TOKEN, HOST_OWNER_ID, HOST_EXPIRY,
    HOST_LIST, HOST_ACTION, HOST_CONFIRM,
) = range(20, 26)


def get_bot_manager(context: ContextTypes.DEFAULT_TYPE):
    """Get the BotManager instance from bot_data."""
    return context.bot_data.get("bot_manager")


# ═══════════════════════════════════════════════
#  HOSTING MENU
# ═══════════════════════════════════════════════

async def hosting_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show hosting management menu."""
    if update.effective_user.id != OWNER_ID or not is_main_bot(context):
        if update.callback_query:
            await update.callback_query.answer("Unauthorized.", show_alert=True)
        return ADMIN_MENU

    keyboard = [
        [InlineKeyboardButton("🤖 Host New Bot", callback_data="hst_new")],
        [InlineKeyboardButton("📋 List Hosted Bots", callback_data="hst_list")],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data="hst_back")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "🤖 *Bot Hosting Management*\n\nSelect an option:"

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    return HOST_LIST


async def hosting_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Route button clicks from the hosting menu."""
    query = update.callback_query
    await query.answer()

    if query.data == "hst_new":
        return await host_start(update, context)
    elif query.data == "hst_list":
        return await show_hosted_bots(update, context)
    elif query.data == "hst_back":
        # Return to admin menu — import inline to avoid circular
        from handlers.admin import _show_admin_menu
        return await _show_admin_menu(update, context)
    elif query.data.startswith("hst_bot_"):
        return await bot_action_menu(update, context)
    elif query.data == "hst_menu":
        return await hosting_menu(update, context)

    return HOST_LIST


# ═══════════════════════════════════════════════
#  HOST NEW BOT FLOW
# ═══════════════════════════════════════════════

async def host_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 1: Ask for bot token."""
    text = (
        "🤖 *Host New Bot*\n\n"
        "📝 Send the Bot API Token (from @BotFather):\n\n"
        "_Send /cancel to abort_"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    return HOST_TOKEN


async def host_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 2: Receive token, ask for owner user_id."""
    token = update.message.text.strip()

    # Basic token format validation
    if ":" not in token or len(token) < 30:
        await update.message.reply_text(
            "❌ Invalid token format. Telegram bot tokens look like:\n"
            "`1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`\n\n"
            "Please send a valid token:",
            parse_mode="Markdown"
        )
        return HOST_TOKEN

    context.user_data["host_token"] = token
    await update.message.reply_text(
        "✅ Token received.\n\n"
        "👤 Now send the *Telegram User ID* of the bot owner:\n\n"
        "_This user will get admin access to the hosted bot_",
        parse_mode="Markdown"
    )
    return HOST_OWNER_ID


async def host_owner_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 3: Receive user_id, ask for expiry."""
    try:
        owner_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID. Send a valid numeric ID:")
        return HOST_OWNER_ID

    context.user_data["host_owner_id"] = owner_id
    context.user_data["host_action"] = "new"

    keyboard = [
        [
            InlineKeyboardButton("1 Day", callback_data="hst_exp_1d"),
            InlineKeyboardButton("1 Week", callback_data="hst_exp_1w"),
        ],
        [
            InlineKeyboardButton("1 Month", callback_data="hst_exp_1m"),
            InlineKeyboardButton("3 Months", callback_data="hst_exp_3m"),
        ],
        [InlineKeyboardButton("♾ No Expiry", callback_data="hst_exp_never")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⏰ Select hosting duration:",
        reply_markup=reply_markup,
    )
    return HOST_EXPIRY


# ═══════════════════════════════════════════════
#  LIST HOSTED BOTS
# ═══════════════════════════════════════════════

async def show_hosted_bots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show list of all hosted bots with status."""
    bot_manager = get_bot_manager(context)
    if not bot_manager:
        if update.callback_query:
            await update.callback_query.answer("Bot Manager not available.", show_alert=True)
        return await hosting_menu(update, context)

    try:
        bots = await bot_manager.get_all_bots()
    except Exception as e:
        logger.error(f"Error fetching bots: {e}")
        if update.callback_query:
            await update.callback_query.answer("Error fetching bots.", show_alert=True)
        return await hosting_menu(update, context)

    keyboard = []
    for bot in bots:
        status = bot.get("status", "active")
        if status == "active":
            emoji = "🟢"
        elif status == "stopped":
            emoji = "🔴"
        elif status == "expired":
            emoji = "⏰"
        else:
            emoji = "⚪"

        bot_username = bot.get("bot_username", "unknown")
        owner_uid = bot.get("owner_user_id", "?")
        bot_id = bot.get("id")

        btn_text = f"{emoji} @{bot_username} (owner: {owner_uid})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"hst_bot_{bot_id}")])

    if not bots:
        text = "📋 *Hosted Bots*\n\n_No bots hosted yet._"
    else:
        text = f"📋 *Hosted Bots* ({len(bots)})\n\nSelect a bot to manage:"

    keyboard.append([InlineKeyboardButton("🔙 Back to Hosting Menu", callback_data="hst_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return HOST_LIST


# ═══════════════════════════════════════════════
#  BOT ACTIONS
# ═══════════════════════════════════════════════

async def bot_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_id: str = None) -> int:
    """Show bot details + action buttons."""
    if bot_id is None:
        query = update.callback_query
        # Data format: hst_bot_{bot_id}
        bot_id = query.data.replace("hst_bot_", "")

    context.user_data["selected_bot_id"] = bot_id
    bot_manager = get_bot_manager(context)

    try:
        bot = await bot_manager.get_bot_info(bot_id)
        if not bot:
            if update.callback_query:
                await update.callback_query.answer("Bot not found.", show_alert=True)
            return await show_hosted_bots(update, context)
    except Exception as e:
        logger.error(f"Error getting bot info: {e}")
        if update.callback_query:
            await update.callback_query.answer("Error fetching bot details.", show_alert=True)
        return await show_hosted_bots(update, context)

    status = bot.get("status", "active")
    is_running = bot_id in bot_manager.bots

    keyboard = []
    if status == "active" and is_running:
        keyboard.append([InlineKeyboardButton("⏸️ Stop Bot", callback_data="hst_stop")])
    elif status in ("stopped", "expired"):
        keyboard.append([InlineKeyboardButton("▶️ Resume Bot", callback_data="hst_resume")])

    keyboard.extend([
        [InlineKeyboardButton("⏰ Set Expiry", callback_data="hst_setexp")],
        [InlineKeyboardButton("📊 View Stats", callback_data="hst_stats")],
        [InlineKeyboardButton("🗑️ Delete Bot", callback_data="hst_delete")],
        [InlineKeyboardButton("🔙 Back to List", callback_data="hst_bk_list")],
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    bot_username = bot.get("bot_username", "Unknown")
    owner_uid = bot.get("owner_user_id", "Unknown")
    expires_at = bot.get("expires_at", "")
    created_at = bot.get("created_at", "?")

    # Status emoji
    status_emoji = "🟢" if status == "active" else "🔴" if status == "stopped" else "⏰"

    text = (
        f"🤖 *Bot Details*\n\n"
        f"📛 Username: @{bot_username}\n"
        f"🆔 Bot ID: `{bot_id}`\n"
        f"👤 Owner: `{owner_uid}`\n"
        f"📊 Status: {status_emoji} {status.upper()}\n"
        f"📅 Created: {created_at[:16]}\n"
        f"⏰ Expiry: {expires_at[:16] if expires_at else 'Never'}"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    return HOST_ACTION


async def bot_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Route action button clicks."""
    query = update.callback_query
    await query.answer()

    if query.data == "hst_bk_list":
        return await show_hosted_bots(update, context)
    elif query.data == "hst_stop":
        return await bot_stop_action(update, context)
    elif query.data == "hst_resume":
        return await bot_resume_action(update, context)
    elif query.data == "hst_setexp":
        return await bot_set_expiry(update, context)
    elif query.data == "hst_stats":
        return await bot_view_stats(update, context)
    elif query.data == "hst_delete":
        return await bot_delete_confirm(update, context)
    elif query.data == "hst_bk_bot":
        bot_id = context.user_data.get("selected_bot_id")
        return await bot_action_menu(update, context, bot_id)

    return HOST_ACTION


async def bot_stop_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stop the selected bot."""
    bot_id = context.user_data.get("selected_bot_id")
    bot_manager = get_bot_manager(context)

    try:
        await bot_manager.stop_bot(bot_id)
        await update.callback_query.answer("✅ Bot stopped successfully.", show_alert=True)
    except Exception as e:
        logger.error(f"Error stopping bot {bot_id}: {e}")
        await update.callback_query.answer(f"❌ Error: {e}", show_alert=True)

    return await bot_action_menu(update, context, bot_id)


async def bot_resume_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Resume the selected bot."""
    bot_id = context.user_data.get("selected_bot_id")
    bot_manager = get_bot_manager(context)

    try:
        await bot_manager.resume_bot(bot_id)
        await update.callback_query.answer("✅ Bot resumed successfully.", show_alert=True)
    except Exception as e:
        logger.error(f"Error resuming bot {bot_id}: {e}")
        await update.callback_query.answer(f"❌ Error: {e}", show_alert=True)

    return await bot_action_menu(update, context, bot_id)


# ═══════════════════════════════════════════════
#  DELETE BOT
# ═══════════════════════════════════════════════

async def bot_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask confirmation for deletion."""
    keyboard = [
        [
            InlineKeyboardButton("❌ Cancel", callback_data="hst_del_no"),
            InlineKeyboardButton("✅ Yes, Delete", callback_data="hst_del_yes"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "⚠️ *DANGER ZONE* ⚠️\n\n"
        "Are you sure you want to *DELETE* this bot?\n\n"
        "🗑 ALL bot data (products, users, orders, settings) will be permanently erased.\n\n"
        "⚠️ *This action cannot be undone!*"
    )

    await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return HOST_CONFIRM


async def bot_delete_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute deletion or cancel."""
    query = update.callback_query
    await query.answer()

    bot_id = context.user_data.get("selected_bot_id")

    if query.data == "hst_del_no":
        return await bot_action_menu(update, context, bot_id)

    # Execute deletion
    bot_manager = get_bot_manager(context)

    try:
        await bot_manager.delete_bot(bot_id)
        text = "✅ Bot deleted successfully.\n\n🗑 All data has been removed."
    except Exception as e:
        logger.error(f"Error deleting bot {bot_id}: {e}")
        text = f"❌ Error deleting bot: {e}"

    keyboard = [[InlineKeyboardButton("🔙 Back to Hosted Bots", callback_data="hst_list")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return HOST_LIST


# ═══════════════════════════════════════════════
#  SET EXPIRY
# ═══════════════════════════════════════════════

async def bot_set_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show expiry options for editing."""
    context.user_data["host_action"] = "edit"

    keyboard = [
        [
            InlineKeyboardButton("1 Day", callback_data="hst_exp_1d"),
            InlineKeyboardButton("1 Week", callback_data="hst_exp_1w"),
        ],
        [
            InlineKeyboardButton("1 Month", callback_data="hst_exp_1m"),
            InlineKeyboardButton("3 Months", callback_data="hst_exp_3m"),
        ],
        [InlineKeyboardButton("♾ No Expiry", callback_data="hst_exp_never")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="hst_exp_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "⏰ Select new expiry duration:", reply_markup=reply_markup
    )
    return HOST_EXPIRY


async def bot_expiry_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle expiry selection — for both new bot and edit bot flows."""
    query = update.callback_query
    await query.answer()

    if query.data == "hst_exp_cancel":
        bot_id = context.user_data.get("selected_bot_id")
        return await bot_action_menu(update, context, bot_id)

    # Calculate expiry datetime
    now = datetime.utcnow()
    expires_at = ""

    if query.data == "hst_exp_1d":
        expires_at = (now + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    elif query.data == "hst_exp_1w":
        expires_at = (now + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    elif query.data == "hst_exp_1m":
        expires_at = (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    elif query.data == "hst_exp_3m":
        expires_at = (now + timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    elif query.data == "hst_exp_never":
        expires_at = ""

    action = context.user_data.get("host_action")
    bot_manager = get_bot_manager(context)

    if action == "new":
        # ── HOST NEW BOT ──
        token = context.user_data.get("host_token")
        owner_id = context.user_data.get("host_owner_id")

        try:
            await query.edit_message_text("⏳ Setting up new bot... Please wait.")
            bot_record = await bot_manager.host_bot(token, owner_id, expires_at)
            bot_username = bot_record.get("bot_username", "unknown")

            text = (
                f"✅ *Bot Hosted Successfully!*\n\n"
                f"🤖 @{bot_username}\n"
                f"👤 Owner: `{owner_id}`\n"
                f"⏰ Expiry: {expires_at[:16] if expires_at else 'Never'}\n\n"
                f"The bot is now live and accepting commands!"
            )
        except Exception as e:
            logger.error(f"Error hosting new bot: {e}")
            text = f"❌ *Failed to host bot*\n\n{e}"

        keyboard = [[InlineKeyboardButton("🔙 Back to Hosting Menu", callback_data="hst_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

        # Clean up user_data
        context.user_data.pop("host_token", None)
        context.user_data.pop("host_owner_id", None)
        context.user_data.pop("host_action", None)
        return HOST_LIST

    elif action == "edit":
        # ── EDIT EXPIRY ──
        bot_id = context.user_data.get("selected_bot_id")
        try:
            await bot_manager.set_expiry(bot_id, expires_at)
            await query.answer(
                f"✅ Expiry updated to: {expires_at[:16] if expires_at else 'Never'}",
                show_alert=True,
            )
        except Exception as e:
            logger.error(f"Error updating expiry: {e}")
            await query.answer(f"❌ Error: {e}", show_alert=True)

        context.user_data.pop("host_action", None)
        return await bot_action_menu(update, context, bot_id)

    return HOST_LIST


# ═══════════════════════════════════════════════
#  VIEW STATS
# ═══════════════════════════════════════════════

async def bot_view_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show stats for the selected hosted bot."""
    bot_id = context.user_data.get("selected_bot_id")
    bot_manager = get_bot_manager(context)

    try:
        stats = await bot_manager.get_bot_stats(bot_id)

        if not stats:
            text = "📊 *Bot Stats*\n\n_No data available._"
        else:
            text = (
                f"📊 *Bot Stats*\n\n"
                f"👥 Users: *{stats.get('total_users', 0)}*\n"
                f"📦 Orders: *{stats.get('total_orders', 0)}*\n"
                f"✅ Approved: *{stats.get('approved_orders', 0)}*\n"
                f"⏳ Pending: *{stats.get('pending_orders', 0)}*\n"
                f"❌ Rejected: *{stats.get('rejected_orders', 0)}*\n"
                f"💰 Revenue: *₹{stats.get('total_revenue', 0)}*"
            )
    except Exception as e:
        logger.error(f"Error fetching stats for {bot_id}: {e}")
        text = f"❌ Error fetching stats: {e}"

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="hst_bk_bot")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return HOST_ACTION


# ═══════════════════════════════════════════════
#  GET STATES (for admin.py integration)
# ═══════════════════════════════════════════════

def get_hosting_states():
    """Return a dict of state -> handlers to be merged into admin ConversationHandler."""
    return {
        HOST_LIST: [
            CallbackQueryHandler(hosting_menu_handler, pattern=r"^hst_"),
        ],
        HOST_TOKEN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, host_token),
        ],
        HOST_OWNER_ID: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, host_owner_id),
        ],
        HOST_EXPIRY: [
            CallbackQueryHandler(bot_expiry_selected, pattern=r"^hst_exp_"),
        ],
        HOST_ACTION: [
            CallbackQueryHandler(bot_action_handler, pattern=r"^hst_"),
        ],
        HOST_CONFIRM: [
            CallbackQueryHandler(bot_delete_execute, pattern=r"^hst_del_"),
        ],
    }
