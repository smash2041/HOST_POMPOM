"""
Host Bot — Hosting Management Handler
Manages bot hosting lifecycle: host/stop/resume/delete.
OWNER sees full management, hosting admins see self-service.
"""
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from utils.bot_helpers import get_main_db
from config import OWNER_ID

logger = logging.getLogger(__name__)

# ── States (20-25, avoid conflict with admin states 0-19) ──
ADMIN_MENU = 0
HOST_TOKEN = 20
HOST_OWNER_ID = 21
HOST_EXPIRY = 22
HOST_LIST = 23
HOST_ACTION = 24
HOST_CONFIRM = 25


def get_bot_manager(context):
    """Get the BotManager from context."""
    return context.bot_data.get("bot_manager")


# ═══════════════════════════════════════════════
#  HOSTING MENU
# ═══════════════════════════════════════════════

async def hosting_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show hosting menu — different for owner vs admin."""
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id

    if user_id == OWNER_ID:
        keyboard = [
            [InlineKeyboardButton("🤖 Host New Bot", callback_data="hst_new")],
            [InlineKeyboardButton("📋 List Hosted Bots", callback_data="hst_list")],
            [InlineKeyboardButton("🔙 Back to Admin", callback_data="adm_back")],
        ]
        text = "👑 <b>Master Hosting Management</b>\n\nChoose an action:"
    else:
        main_db = get_main_db(context)
        hosted_bot = await main_db.get_hosted_bot_by_admin(user_id)

        if not hosted_bot:
            keyboard = [
                [InlineKeyboardButton("🤖 Host My Bot", callback_data="hst_new")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_back")],
            ]
            text = "🤖 <b>Host Your Own Bot</b>\n\nYou don't have a hosted bot yet.\nClick below to host one!"
        else:
            status_emoji = "🟢" if hosted_bot["status"] == "active" else "🔴"
            keyboard = [
                [InlineKeyboardButton("📊 My Bot Status", callback_data="hst_my_status")],
                [InlineKeyboardButton("🗑 Delete My Bot", callback_data="hst_my_delete")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_back")],
            ]
            text = (
                f"🤖 <b>Your Hosted Bot</b>\n\n"
                f"Bot: @{hosted_bot.get('bot_username', 'unknown')}\n"
                f"Status: {status_emoji} {hosted_bot['status'].upper()}\n\n"
                f"Choose an action:"
            )

    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text, parse_mode="HTML", reply_markup=reply_markup,
            )
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

    return HOST_LIST


# ═══════════════════════════════════════════════
#  HOSTING MENU ROUTER
# ═══════════════════════════════════════════════

async def hosting_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Route hosting menu button clicks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "hst_new":
        return await host_start(update, context)
    elif data == "hst_list":
        return await show_hosted_bots(update, context)
    elif data == "hst_my_status":
        return await my_bot_status(update, context)
    elif data == "hst_my_delete":
        return await delete_my_bot_prompt(update, context)
    elif data == "hst_menu":
        return await hosting_menu(update, context)
    elif data == "adm_back" or data == "admin_menu":
        # Go back to admin menu
        from handlers.admin import _show_admin_menu
        return await _show_admin_menu(update, context)

    return await hosting_menu(update, context)


# ═══════════════════════════════════════════════
#  HOST NEW BOT
# ═══════════════════════════════════════════════

async def host_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the hosting flow — check limits first."""
    query = update.callback_query
    user_id = update.effective_user.id

    # Check 1-bot-per-admin limit (for non-owners)
    if user_id != OWNER_ID:
        main_db = get_main_db(context)
        existing = await main_db.get_hosted_bot_by_admin(user_id)
        if existing:
            await query.edit_message_text(
                "❌ <b>You already have a hosted bot!</b>\n\n"
                f"Bot: @{existing.get('bot_username', 'unknown')}\n"
                f"Status: {existing['status'].upper()}\n\n"
                "Delete your current bot first before hosting a new one.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑 Delete My Bot", callback_data="hst_my_delete")],
                    [InlineKeyboardButton("🔙 Back", callback_data="hst_menu")],
                ]),
            )
            return HOST_LIST

    await query.edit_message_text(
        "🤖 *Host a New Bot*\n\n"
        "Send the bot token from @BotFather:\n\n"
        "_Example: 123456789:ABCdefGhIjKlMnOpQrStUvWxYz_\n\n"
        "Send /cancel to go back.",
        parse_mode="Markdown",
    )
    return HOST_TOKEN


async def host_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and validate bot token."""
    token = update.message.text.strip()

    # Basic validation
    if ":" not in token or len(token) < 30:
        await update.message.reply_text(
            "❌ Invalid token format. A bot token looks like:\n"
            "`123456789:ABCdefGhIjKlMnOpQrStUvWxYz`\n\n"
            "Please send a valid token:",
            parse_mode="Markdown",
        )
        return HOST_TOKEN

    context.user_data["host_token"] = token
    user_id = update.effective_user.id

    if user_id == OWNER_ID:
        # Owner can set any user as the admin
        await update.message.reply_text(
            "✅ Token saved.\n\n"
            "👤 Enter the *User ID* of the person who will admin this bot:",
            parse_mode="Markdown",
        )
        return HOST_OWNER_ID
    else:
        # Non-owner — auto-set themselves
        context.user_data["host_owner_id"] = user_id
        return await _show_expiry_options(update, context)


async def host_owner_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the owner user ID for the hosted bot (OWNER flow only)."""
    try:
        owner_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID. Send a numeric ID:")
        return HOST_OWNER_ID

    context.user_data["host_owner_id"] = owner_id
    return await _show_expiry_options(update, context)


async def _show_expiry_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show expiry selection buttons."""
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
        "⏱ *Set Bot Expiry*\n\nChoose how long this hosted bot should run:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )
    return HOST_EXPIRY


async def bot_expiry_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle expiry selection and actually host the bot."""
    query = update.callback_query
    await query.answer()

    expiry_choice = query.data.replace("hst_exp_", "")
    now = datetime.utcnow()

    expiry_map = {
        "1d": now + timedelta(days=1),
        "1w": now + timedelta(weeks=1),
        "1m": now + timedelta(days=30),
        "3m": now + timedelta(days=90),
        "never": None,
    }

    expires_at = expiry_map.get(expiry_choice)
    expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else ""

    token = context.user_data.get("host_token")
    owner_user_id = context.user_data.get("host_owner_id", 0)

    if not token:
        await query.edit_message_text("❌ Something went wrong. Please try again.")
        return ADMIN_MENU

    bot_manager = get_bot_manager(context)
    if not bot_manager:
        await query.edit_message_text("❌ Bot Manager not available. Please restart the main bot.")
        return ADMIN_MENU

    await query.edit_message_text("⏳ <b>Setting up your Bot...</b>\n\n1. Validating token\n2. Syncing media files\n\nYou'll receive a message when it's ready!", parse_mode="HTML")

    # Clean up user_data
    context.user_data.pop("host_token", None)
    context.user_data.pop("host_owner_id", None)

    # Run hosting in background so owner's buttons don't freeze
    chat_id = query.message.chat_id
    bot_obj = context.bot
    
    async def _host_in_background():
        try:
            bot_record = await bot_manager.host_bot(token, owner_user_id, expires_str, progress_chat_id=chat_id)
            expiry_text = expires_at.strftime("%Y-%m-%d %H:%M") if expires_at else "Never"

            # Show success message with deep-link to start the hosted bot
            start_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"▶️ Start @{bot_record['bot_username']}",
                    url=f"https://t.me/{bot_record['bot_username']}?start=setup",
                )],
            ])

            await bot_obj.send_message(
                chat_id=chat_id,
                text=(
                    f"✅ <b>Bot Hosted Successfully!</b>\n\n"
                    f"🤖 Bot: @{bot_record['bot_username']}\n"
                    f"🆔 Bot ID: <code>{bot_record['id']}</code>\n"
                    f"👤 Admin: <code>{owner_user_id}</code>\n"
                    f"⏱ Expires: {expiry_text}\n\n"
                    f"⚠️ <b>IMPORTANT:</b> Please click the button below to /start the hosted bot first!\n"
                    f"Media sync will begin after you start it."
                ),
                parse_mode="HTML",
                reply_markup=start_keyboard,
            )
        except ValueError as e:
            await bot_obj.send_message(chat_id=chat_id, text=f"❌ Failed to host bot:\n\n{str(e)}")
        except Exception as e:
            logger.error(f"Failed to host bot: {e}", exc_info=True)
            await bot_obj.send_message(chat_id=chat_id, text=f"❌ Error:\n\n{str(e)}")

    asyncio.create_task(_host_in_background())

    from handlers.admin import _show_admin_menu
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  LIST HOSTED BOTS (Owner)
# ═══════════════════════════════════════════════

async def show_hosted_bots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show list of all hosted bots."""
    query = update.callback_query
    bot_manager = get_bot_manager(context)
    if not bot_manager:
        await query.edit_message_text("❌ Bot Manager not available.")
        return ADMIN_MENU

    bots = await bot_manager.get_all_bots()

    if not bots:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="hst_menu")]]
        await query.edit_message_text(
            "📋 *Hosted Bots*\n\nNo bots hosted yet.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return HOST_LIST

    text = "📋 <b>Hosted Bots</b>\n\n"
    keyboard = []

    status_emojis = {
        "active": "🟢", "stopped": "🔴",
        "expired": "⏰", "deleted": "🗑",
    }

    for bot in bots:
        emoji = status_emojis.get(bot["status"], "❓")
        text += f'{emoji} @{bot.get("bot_username", "?")} — {bot["status"].upper()}\n'
        if bot["status"] in ("active", "stopped"):
            keyboard.append([InlineKeyboardButton(
                f'{emoji} @{bot.get("bot_username", bot["id"])}',
                callback_data=f"hst_bot_{bot['id']}",
            )])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="hst_menu")])
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML",
    )
    return HOST_ACTION


async def bot_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle actions on a specific hosted bot."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "hst_menu":
        return await hosting_menu(update, context)

    bot_manager = get_bot_manager(context)
    if not bot_manager:
        return HOST_LIST

    if data.startswith("hst_bot_"):
        bot_id = data.replace("hst_bot_", "")
        context.user_data["selected_bot_id"] = bot_id
        bot_info = await bot_manager.get_bot_info(bot_id)

        if not bot_info:
            await query.answer("❌ Bot not found.", show_alert=True)
            return await show_hosted_bots(update, context)

        is_running = bot_id in bot_manager.bots

        text = (
            f"🤖 <b>Bot: @{bot_info.get('bot_username', '?')}</b>\n\n"
            f"🆔 ID: <code>{bot_id}</code>\n"
            f"👤 Admin: <code>{bot_info['owner_user_id']}</code>\n"
            f"📊 Status: {bot_info['status'].upper()}\n"
            f"🕐 Created: {bot_info.get('created_at', '?')}\n"
            f"⏱ Expires: {bot_info.get('expires_at') or 'Never'}\n"
            f"🔄 Running: {'✅' if is_running else '❌'}"
        )

        keyboard = []
        if is_running:
            keyboard.append([InlineKeyboardButton("⏸ Stop Bot", callback_data=f"hst_stop_{bot_id}")])
        else:
            keyboard.append([InlineKeyboardButton("▶️ Resume Bot", callback_data=f"hst_resume_{bot_id}")])
        keyboard.append([InlineKeyboardButton("🗑 Delete Bot", callback_data=f"hst_del_ask_{bot_id}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="hst_list")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return HOST_ACTION

    elif data.startswith("hst_stop_"):
        bot_id = data.replace("hst_stop_", "")
        try:
            await bot_manager.stop_bot(bot_id, reason="manual")
            await query.answer("✅ Bot stopped!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ Error: {e}", show_alert=True)
        return await show_hosted_bots(update, context)

    elif data.startswith("hst_resume_"):
        bot_id = data.replace("hst_resume_", "")
        try:
            await bot_manager.resume_bot(bot_id)
            await query.answer("✅ Bot resumed!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ Error: {e}", show_alert=True)
        return await show_hosted_bots(update, context)

    elif data.startswith("hst_del_ask_"):
        bot_id = data.replace("hst_del_ask_", "")
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"hst_del_yes_{bot_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="hst_list"),
            ]
        ]
        await query.edit_message_text(
            f"⚠️ *Delete Bot*\n\nAre you sure you want to delete bot `{bot_id}`?\n\n"
            "This will remove ALL data (users, orders, overrides)!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return HOST_CONFIRM

    elif data == "hst_list":
        return await show_hosted_bots(update, context)

    return await hosting_menu(update, context)


async def bot_delete_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute bot deletion after confirmation."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("hst_del_yes_"):
        bot_id = data.replace("hst_del_yes_", "")
        bot_manager = get_bot_manager(context)
        if bot_manager:
            try:
                await bot_manager.delete_bot(bot_id)
                await query.answer(f"✅ Bot {bot_id} deleted successfully!", show_alert=True)
            except Exception as e:
                await query.answer(f"❌ Error deleting bot: {e}", show_alert=True)
    else:
        if data == "hst_menu":
            return await hosting_menu(update, context)
        return await show_hosted_bots(update, context)

    from handlers.admin import _show_admin_menu
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  ADMIN SELF-SERVICE
# ═══════════════════════════════════════════════

async def my_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show admin's own hosted bot status."""
    query = update.callback_query
    main_db = get_main_db(context)
    user_id = update.effective_user.id

    hosted = await main_db.get_hosted_bot_by_admin(user_id)
    if not hosted:
        await query.answer("❌ You don't have a hosted bot.", show_alert=True)
        return await hosting_menu(update, context)

    bot_manager = get_bot_manager(context)
    is_running = bot_manager and hosted["id"] in bot_manager.bots

    text = (
        f"🤖 <b>Your Bot: @{hosted.get('bot_username', '?')}</b>\n\n"
        f"📊 Status: {hosted['status'].upper()}\n"
        f"🔄 Running: {'✅' if is_running else '❌'}\n"
        f"🕐 Created: {hosted.get('created_at', '?')}\n"
        f"⏱ Expires: {hosted.get('expires_at') or 'Never'}"
    )

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="hst_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return HOST_LIST


async def delete_my_bot_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask admin to confirm deleting their own hosted bot."""
    query = update.callback_query
    main_db = get_main_db(context)
    user_id = update.effective_user.id

    hosted = await main_db.get_hosted_bot_by_admin(user_id)
    if not hosted:
        await query.answer("❌ You don't have a hosted bot.", show_alert=True)
        return await hosting_menu(update, context)

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Delete", callback_data=f"hst_del_yes_{hosted['id']}"),
            InlineKeyboardButton("❌ Cancel", callback_data="hst_menu"),
        ]
    ]
    await query.edit_message_text(
        f"⚠️ <b>Delete Your Hosted Bot</b>\n\n"
        f"Bot: @{hosted.get('bot_username', '?')}\n\n"
        "This will remove ALL data associated with your bot!\n\n"
        "Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return HOST_CONFIRM


# ═══════════════════════════════════════════════
#  STATE EXPORTS (merged into admin ConversationHandler)
# ═══════════════════════════════════════════════

def get_hosting_states():
    """Return hosting-related states for merging into admin ConversationHandler."""
    return {
        HOST_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_token)],
        HOST_OWNER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, host_owner_id)],
        HOST_EXPIRY: [CallbackQueryHandler(bot_expiry_selected, pattern=r"^hst_exp_")],
        HOST_LIST: [CallbackQueryHandler(hosting_menu_handler, pattern=r"^hst_|^adm_back$|^admin_menu$")],
        HOST_ACTION: [CallbackQueryHandler(bot_action_handler, pattern=r"^hst_")],
        HOST_CONFIRM: [CallbackQueryHandler(bot_delete_execute, pattern=r"^hst_")],
    }
