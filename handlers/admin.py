"""
Host Bot — Admin Panel Handler
TWO modes:
  Main Bot (owner): Full panel + product management + hosting management
  Main Bot (hosting admin): Only hosting flow (host/delete bot)
  Hosted Bot (admin): Full customization (products, settings, UPI, QR, welcome, proof, etc.)
                      Initially mirrored from main — admin can override everything
"""
import asyncio
import html
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from utils.bot_helpers import get_db, get_main_db, get_owner_ids, is_main_bot, get_products_db
from config import OWNER_ID
from utils.helpers import format_price, escape_md
from handlers.broadcast import broadcast_message

logger = logging.getLogger(__name__)

# ── ConversationHandler States ──
(
    ADMIN_MENU,
    AP_NAME, AP_PRICE, AP_DESC, AP_TRIAL, AP_CHANNEL,
    EP_SELECT, EP_FIELD, EP_VALUE,
    RP_SELECT, RP_CONFIRM,
    SET_UPI_INPUT, SET_QR_INPUT,
    SET_WELCOME_INPUT, SET_HTU_VIDEO, SET_HTU_TEXT,
    SET_PROOF_LINK_INPUT, SET_PROOF_PHOTO_INPUT,
    BC_MESSAGE, BC_CONFIRM,
) = range(20)

MANAGE_ADMIN_MENU = 26
MANAGE_ADMIN_INPUT = 27
MANAGE_ADMIN_DURATION = 28
WELCOME_TEXT_MENU = 29
SET_WELCOME_TEXT = 30
SET_DISAPPEAR_TIME = 31

# ── New states for Demo & Buy Now features ──
SET_DEMO_LINK_INPUT = 32
BUYNOW_SETTINGS_MENU = 47
SET_BUYNOW_PRICE_LIST = 48
BUYNOW_ADD_PLAN_LABEL = 49
BUYNOW_ADD_PLAN_PRICE = 50
BUYNOW_ADD_PLAN_DAYS = 51
BUYNOW_ADD_PLAN_COLOR = 52
SET_BUYNOW_CHANNEL_LINK = 53

# ── Mass Add Products state ──
MASS_ADD_NAMES = 54


# ═══════════════════════════════════════════════
#  ADMIN MENU
# ═══════════════════════════════════════════════

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_main_bot(context):
        if user_id == OWNER_ID:
            pass
        else:
            main_db = get_main_db(context)
            is_admin = await main_db.is_hosting_admin(user_id)
            if not is_admin:
                return ConversationHandler.END
    else:
        if user_id not in get_owner_ids(context):
            return ConversationHandler.END
    return await _show_admin_menu(update, context)


async def _show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display admin menu based on context."""
    main_db = get_main_db(context)
    user_id = update.effective_user.id

    if is_main_bot(context) and user_id == OWNER_ID:
        # ── FULL ADMIN PANEL (Main Bot Owner) ──
        proof_enabled = await main_db.get_setting("proof_button_enabled")
        proof_status = "ON ✅" if proof_enabled == "1" else "OFF ❌"
        fwd_enabled = await main_db.get_setting("forward_protection")
        fwd_status = "ON ✅" if fwd_enabled == "1" else "OFF ❌"
        disappear_enabled = await main_db.get_setting("auto_disappear")
        disappear_status = "ON ✅" if disappear_enabled == "1" else "OFF ❌"
        htu_enabled = await main_db.get_setting("htu_button_enabled")
        htu_status = "ON ✅" if htu_enabled != "0" else "OFF ❌"
        color_btn_enabled = await main_db.get_setting("color_buttons_enabled")
        color_btn_status = "ON ✅" if color_btn_enabled != "0" else "OFF ❌"
        disappear_seconds = await main_db.get_setting("auto_disappear_seconds")
        try:
            disappear_time_display = int(disappear_seconds) if disappear_seconds else 1800
        except (ValueError, TypeError):
            disappear_time_display = 1800
        if disappear_time_display >= 60:
            disappear_time_label = f"{disappear_time_display // 60} min"
        else:
            disappear_time_label = f"{disappear_time_display} sec"

        keyboard = [
            [InlineKeyboardButton("🎁 Edit Product", callback_data="adm_edit")],
            [InlineKeyboardButton("➕ Add Product", callback_data="adm_add")],
            [InlineKeyboardButton("📦 Mass Add Products", callback_data="adm_mass_add")],
            [InlineKeyboardButton("🗑 Remove Product", callback_data="adm_remove")],
            [InlineKeyboardButton(f"🔘 Toggle Proof Button ({proof_status})", callback_data="adm_toggle_proof")],
            [InlineKeyboardButton(f"🔒 Forward Protection ({fwd_status})", callback_data="adm_toggle_fwd")],
            [InlineKeyboardButton(f"⏱ Auto Disappear ({disappear_status})", callback_data="adm_toggle_disappear")],
            [InlineKeyboardButton(f"⏱ Set Disappear Time ({disappear_time_label})", callback_data="adm_set_disappear_time")],
            [InlineKeyboardButton(f"🎨 Color Buttons ({color_btn_status})", callback_data="adm_toggle_colors")],
            [InlineKeyboardButton(f"❓ How to Use Button ({htu_status})", callback_data="adm_toggle_htu")],
            [InlineKeyboardButton("📄 Set Proof Link", callback_data="adm_proof_link")],
            [InlineKeyboardButton("🖼 Set Proof Photo", callback_data="adm_proof_photo")],
            [InlineKeyboardButton("🎨 Set Static Button Colors", callback_data="adm_colors")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
            [InlineKeyboardButton("💳 Set UPI", callback_data="adm_upi")],
            [InlineKeyboardButton("💳 Set QR Image", callback_data="adm_qr")],
            [InlineKeyboardButton("🎬 Set Welcome Media", callback_data="adm_welcome")],
            [InlineKeyboardButton("📝 Welcome Text", callback_data="adm_welcome_text")],
            [InlineKeyboardButton("❓ Set How to Use", callback_data="adm_htu")],
            [InlineKeyboardButton("👥 View Users", callback_data="adm_users")],
            [InlineKeyboardButton("📊 Stats", callback_data="adm_stats")],
            [InlineKeyboardButton("👑 Manage Admins", callback_data="adm_manage_admins")],
            [InlineKeyboardButton("🤖 Bot Hosting", callback_data="adm_hosting")],
            [InlineKeyboardButton("🔄 Swipe UPI Mode", callback_data="adm_swipe")],
            [InlineKeyboardButton("💰 Buy Now Access", callback_data="adm_buynow_access")],
        ]

        # Fetch demo toggle status for main bot
        demo_enabled_main = await main_db.get_setting("demo_button_enabled")
        demo_status_main = "ON ✅" if demo_enabled_main == "1" else "OFF ❌"
        keyboard.extend([
            [InlineKeyboardButton(f"🎬 Demo Toggle ({demo_status_main})", callback_data="adm_toggle_demo")],
            [InlineKeyboardButton("🔗 Set Demo Link", callback_data="adm_set_demo_link")],
            [InlineKeyboardButton("🎨 Demo Button Color", callback_data="adm_btnclr_demo_button_color")],
            [InlineKeyboardButton("🎨 Proof Button Color", callback_data="adm_btnclr_proof_button_color")],
        ])
        text = "👑 *Main Bot Admin Panel*\n\nSelect an option below:"

    elif not is_main_bot(context):
        # ── HOSTED BOT ADMIN — Full customization (products + settings) ──
        db = get_db(context)
        proof_enabled = await db.get_setting("proof_button_enabled")
        if proof_enabled is None:
            proof_enabled = await main_db.get_setting("proof_button_enabled")
        proof_status = "ON ✅" if proof_enabled == "1" else "OFF ❌"

        htu_enabled = await db.get_setting("htu_button_enabled")
        if htu_enabled is None:
            htu_enabled = await main_db.get_setting("htu_button_enabled")
        htu_status = "ON ✅" if htu_enabled != "0" else "OFF ❌"

        # Check hosted bot product count (independent after clone)
        product_count = await db.get_product_count()
        products_source = f"{product_count} products" if product_count > 0 else "No products (use Reset to re-clone from Main)"

        keyboard = [
            [InlineKeyboardButton("🎁 Edit Product", callback_data="adm_edit")],
            [InlineKeyboardButton("➕ Add Product", callback_data="adm_add")],
            [InlineKeyboardButton("📦 Mass Add Products", callback_data="adm_mass_add")],
            [InlineKeyboardButton("🗑 Remove Product", callback_data="adm_remove")],
            [InlineKeyboardButton("🗑💣 Mass Remove All Products", callback_data="adm_mass_remove")],
            [InlineKeyboardButton("🔄 Reset Products to Main", callback_data="adm_reset_products")],
            [InlineKeyboardButton(f"🔘 Toggle Proof Button ({proof_status})", callback_data="adm_toggle_proof")],
            [InlineKeyboardButton(f"❓ How to Use Button ({htu_status})", callback_data="adm_toggle_htu")],
            [InlineKeyboardButton("📄 Set Proof Link", callback_data="adm_proof_link")],
            [InlineKeyboardButton("🖼 Set Proof Photo", callback_data="adm_proof_photo")],
            [InlineKeyboardButton("💳 Set UPI", callback_data="adm_upi")],
            [InlineKeyboardButton("💳 Set QR Image", callback_data="adm_qr")],
            [InlineKeyboardButton("🎬 Set Welcome Media", callback_data="adm_welcome")],
            [InlineKeyboardButton("📝 Welcome Text", callback_data="adm_welcome_text")],
            [InlineKeyboardButton("❓ Set How to Use", callback_data="adm_htu")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
            [InlineKeyboardButton("👥 View Users", callback_data="adm_users")],
            [InlineKeyboardButton("📊 Stats", callback_data="adm_stats")],
        ]

        # Demo toggle + link for hosted bot
        demo_enabled_hb = await db.get_setting("demo_button_enabled")
        if demo_enabled_hb is None:
            demo_enabled_hb = await main_db.get_setting("demo_button_enabled")
        demo_status_hb = "ON ✅" if demo_enabled_hb == "1" else "OFF ❌"
        keyboard.extend([
            [InlineKeyboardButton(f"🎬 Demo Toggle ({demo_status_hb})", callback_data="adm_toggle_demo")],
            [InlineKeyboardButton("🔗 Set Demo Link", callback_data="adm_set_demo_link")],
            [InlineKeyboardButton("🎨 Demo Button Color", callback_data="adm_btnclr_demo_button_color")],
            [InlineKeyboardButton("🎨 Proof Button Color", callback_data="adm_btnclr_proof_button_color")],
        ])

        # Buy Now settings — only if main bot owner granted access
        bot_id = context.bot_data.get("bot_id", "")
        if bot_id:
            has_bn_access = await main_db.has_buynow_access(bot_id)
            if has_bn_access:
                buynow_enabled_hb = await db.get_setting("buynow_enabled")
                if buynow_enabled_hb is None:
                    buynow_enabled_hb = await main_db.get_setting("buynow_enabled")
                buynow_status_hb = "ON ✅" if buynow_enabled_hb == "1" else "OFF ❌"
                keyboard.append([InlineKeyboardButton(f"💰 Buy Now Toggle ({buynow_status_hb})", callback_data="adm_toggle_buynow")])
                keyboard.append([InlineKeyboardButton("💰 Buy Now Settings", callback_data="adm_buynow_settings")])
        text = (
            "🛠 *Hosted Bot Admin Panel*\n\n"
            f"📦 Products: {products_source}\n"
            "Customize everything below:"
        )

    else:
        # ── Main bot — hosting admin (not owner) ──
        keyboard = [
            [InlineKeyboardButton("🤖 Host a Bot", callback_data="adm_hosting")],
        ]
        text = "🤖 *Bot Hosting Panel*\n\nUse the button below to host your bot:"

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text, reply_markup=reply_markup, parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return ADMIN_MENU


async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route admin menu button clicks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "adm_edit":
        return await _show_edit_products(update, context)
    elif data == "adm_add":
        context.user_data["add_product"] = {}
        await query.edit_message_text("➕ *Add New Product*\n\n📝 Enter the product name:", parse_mode="Markdown")
        return AP_NAME
    elif data == "adm_mass_add":
        await query.edit_message_text(
            "📦 *Mass Add Products*\n\n"
            "Send product names — one name per line.\n"
            "All products will be created with ₹0 price.\n\n"
            "📝 *Example:*\n"
            "`Plan A`\n"
            "`Plan B`\n"
            "`Plan C`\n\n"
            "Then use 🎁 Edit Product to set price, media, channel link etc.\n\n"
            "_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return MASS_ADD_NAMES
    elif data == "adm_remove":
        return await _show_remove_products(update, context)
    elif data == "adm_reset_products":
        return await _reset_products(update, context)
    elif data == "adm_toggle_proof":
        return await _toggle_setting(update, context, "proof_button_enabled", "Proof Button")
    elif data == "adm_toggle_fwd":
        return await _toggle_setting(update, context, "forward_protection", "Forward Protection", main_only=True)
    elif data == "adm_toggle_disappear":
        return await _toggle_setting(update, context, "auto_disappear", "Auto Disappear", main_only=True)
    elif data == "adm_set_disappear_time":
        main_db = get_main_db(context)
        current_sec = await main_db.get_setting("auto_disappear_seconds")
        try:
            current_val = int(current_sec) if current_sec else 1800
        except (ValueError, TypeError):
            current_val = 1800
        await query.edit_message_text(
            f"⏱ *Set Auto Disappear Time*\n\n"
            f"Current: *{current_val} seconds ({current_val // 60} min)*\n\n"
            f"Enter new time in *seconds*:\n"
            f"_(Min: 60 sec, Max: 86400 sec / 24 hours)_\n\n"
            f"_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_DISAPPEAR_TIME
    elif data == "adm_toggle_colors":
        return await _toggle_setting(update, context, "color_buttons_enabled", "Color Buttons", main_only=True)
    elif data == "adm_toggle_htu":
        return await _toggle_setting(update, context, "htu_button_enabled", "How to Use Button")
    elif data == "adm_welcome_text":
        return await _show_welcome_text_menu(update, context)
    elif data == "adm_proof_link":
        current = await _get_current_setting(context, "proof_link")
        await query.edit_message_text(
            f"📄 *Set Proof Link*\n\nCurrent: {current or 'Not set'}\n\n🔗 Send the new proof link/URL:\n\n_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_PROOF_LINK_INPUT
    elif data == "adm_proof_photo":
        await query.edit_message_text(
            "🖼 *Set Proof Photo*\n\n📸 Send the proof photo:\n\n_Send /cancel to go back_", parse_mode="Markdown",
        )
        return SET_PROOF_PHOTO_INPUT
    elif data == "adm_colors":
        return await _show_colors_info(update, context)
    elif data == "adm_broadcast":
        await query.edit_message_text(
            "📢 *Broadcast Message*\n\nSend the message to broadcast to all users.\nYou can send text, photo, video, or any media.\n\n_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return BC_MESSAGE
    elif data == "adm_upi":
        current = await _get_current_setting(context, "upi_id")
        await query.edit_message_text(
            f"💳 *Set UPI ID*\n\nCurrent: `{current or 'Not set'}`\n\nSend the new UPI ID:\n\n_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_UPI_INPUT
    elif data == "adm_qr":
        await query.edit_message_text(
            "💳 *Set Custom QR Image*\n\n📸 Send the QR code image.\nTo remove custom QR, type `remove`.\n\n_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_QR_INPUT
    elif data == "adm_welcome":
        context.user_data["welcome_ids"] = []
        await query.edit_message_text(
            "🎬 *Set Welcome Media*\n\nSend welcome videos/photos one by one.\nType *done* when finished, or *clear* to remove all.",
            parse_mode="Markdown",
        )
        return SET_WELCOME_INPUT
    elif data == "adm_htu":
        await query.edit_message_text(
            "❓ *Set 'How to Use'*\n\n🎬 Send the How to Use video:\n\n_Send /cancel to go back_", parse_mode="Markdown",
        )
        return SET_HTU_VIDEO
    elif data == "adm_users":
        return await _show_users_list(update, context, page=0)
    elif data == "adm_stats":
        return await _show_stats(update, context)
    elif data == "adm_swipe":
        from handlers.swipe_mode import swipe_menu
        return await swipe_menu(update, context)
    elif data == "adm_buynow_access":
        from handlers.buynow_mode import buynow_access_menu
        return await buynow_access_menu(update, context)
    elif data == "adm_toggle_demo":
        return await _toggle_setting(update, context, "demo_button_enabled", "Demo Button")
    elif data == "adm_set_demo_link":
        current = await _get_current_setting(context, "demo_link")
        await query.edit_message_text(
            f"🔗 *Set Demo Link*\n\nCurrent: {current or 'Not set'}\n\n🔗 Send the new demo link/URL:\n\n_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_DEMO_LINK_INPUT
    elif data == "adm_toggle_buynow":
        return await _toggle_setting(update, context, "buynow_enabled", "Buy Now")
    elif data == "adm_buynow_settings":
        return await _show_buynow_settings_menu(update, context)
    elif data == "adm_mass_remove":
        return await _mass_remove_confirm(update, context)
    elif data == "adm_mass_remove_yes":
        return await _mass_remove_execute(update, context)
    elif data.startswith("adm_btnclr_"):
        return await _show_button_color_picker(update, context)
    elif data == "adm_pending":
        return await _show_pending_orders(update, context, page=0)
    elif data == "adm_manage_admins":
        return await _show_manage_admins(update, context)
    elif data == "adm_hosting":
        from handlers.hosting import hosting_menu
        return await hosting_menu(update, context)
    elif data == "adm_back":
        return await _show_admin_menu(update, context)

    return ADMIN_MENU


# ═══════════════════════════════════════════════
#  HELPER: Get/Set setting for correct DB
# ═══════════════════════════════════════════════

def _get_settings_db(context):
    """
    Get the right DB for setting writes:
    - Main bot: writes to main DB (no prefix)
    - Hosted bot: writes to per-bot DB (prefixed) — this is the override
    """
    if is_main_bot(context):
        return get_main_db(context)
    else:
        return get_db(context)


def _get_products_write_db(context):
    """
    Get the right DB for product writes:
    - Main bot: writes to main products table
    - Hosted bot: writes to per-bot products table (override)
    """
    if is_main_bot(context):
        return get_main_db(context)
    else:
        return get_db(context)


async def _get_current_setting(context, key):
    """Get current effective setting value (per-bot → main fallback)."""
    from utils.bot_helpers import get_setting_with_fallback
    return await get_setting_with_fallback(context, key)


async def _reset_products(update, context):
    """Reset hosted bot's custom products — deep clone fresh from main bot."""
    if is_main_bot(context):
        await update.callback_query.answer("❌ Cannot reset on main bot.", show_alert=True)
        return await _show_admin_menu(update, context)

    db = get_db(context)
    main_db = get_main_db(context)

    # Delete all per-bot products
    t = db._t
    await db.execute(f"DELETE FROM {t('products')}")

    # Deep clone fresh products from main bot
    main_products = await main_db.get_all_products(active_only=False)
    for product in main_products:
        await db.add_product(
            name=product["name"],
            price=product["price"],
            description=product.get("description", ""),
            trial_file_ids=json.dumps(product.get("trial_file_ids_list", [])),
            channel_link=product.get("channel_link", ""),
            button_style=product.get("button_style", ""),
        )

    await update.callback_query.answer(
        f"✅ Products reset! {len(main_products)} products cloned from main bot.",
        show_alert=True,
    )
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  ADD PRODUCT (Main bot only)
# ═══════════════════════════════════════════════

async def _ap_name(update, context):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Name cannot be empty. Try again:")
        return AP_NAME
    context.user_data["add_product"] = {"name": name}
    await update.message.reply_text(f"✅ Name: *{name}*\n\n💰 Enter the price (in ₹):", parse_mode="Markdown")
    return AP_PRICE


async def _ap_price(update, context):
    try:
        price = float(update.message.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid positive number:")
        return AP_PRICE
    context.user_data["add_product"]["price"] = price
    await update.message.reply_text(
        f"✅ Price: *{format_price(price)}*\n\n📝 Enter product description (or type `skip`):", parse_mode="Markdown",
    )
    return AP_DESC


async def _ap_desc(update, context):
    text = update.message.text.strip()
    context.user_data["add_product"]["description"] = "" if text.lower() == "skip" else text
    context.user_data["add_product"]["trial_ids"] = []
    await update.message.reply_text(
        "🎬 *Trial Media*\n\nSend trial videos or photos one by one.\nType *done* when finished, or *skip* to add none.",
        parse_mode="Markdown",
    )
    return AP_TRIAL


async def _ap_trial(update, context):
    if update.message.text:
        t = update.message.text.strip().lower()
        if t in ("done", "skip"):
            trial_ids = context.user_data["add_product"].get("trial_ids", [])
            await update.message.reply_text(
                f"✅ {len(trial_ids)} trial media saved.\n\n🔗 Send the *channel invite link*:\n_Example: https://t.me/+xxxxx_",
                parse_mode="Markdown",
            )
            return AP_CHANNEL
        await update.message.reply_text("❌ Send a video/photo file, or type *done* / *skip*.", parse_mode="Markdown")
        return AP_TRIAL

    file_id = None
    if update.message.photo:
        file_id = "photo:" + update.message.photo[-1].file_id
    elif update.message.video:
        file_id = "video:" + update.message.video.file_id
    elif update.message.animation:
        file_id = update.message.animation.file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    if file_id:
        context.user_data["add_product"].setdefault("trial_ids", []).append(file_id)
        count = len(context.user_data["add_product"]["trial_ids"])
        await update.message.reply_text(f"✅ Media #{count} added! Send more or type *done*.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Send a video/photo file, or type *done* / *skip*.", parse_mode="Markdown")
    return AP_TRIAL


async def _ap_channel(update, context):
    main_db = _get_products_write_db(context)
    channel_link = update.message.text.strip()
    data = context.user_data.get("add_product", {})
    trial_ids = json.dumps(data.get("trial_ids", []))
    product_id = await main_db.add_product(
        name=data["name"], price=data["price"],
        description=data.get("description", ""),
        trial_file_ids=trial_ids, channel_link=channel_link,
    )
    context.user_data.pop("add_product", None)
    await update.message.reply_text(
        f"✅ *Product Created!*\n\n📦 Name: {data['name']}\n💰 Price: {format_price(data['price'])}\n"
        f"🎬 Trial Media: {len(data.get('trial_ids', []))}\n🔗 Channel: {channel_link}\n🆔 ID: #{product_id}",
        parse_mode="Markdown",
    )
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  MASS ADD PRODUCTS — Multiple names at once
# ═══════════════════════════════════════════════

async def _mass_add_products(update, context):
    """Receive multiple product names (one per line) and create them all with ₹0 price."""
    main_db = _get_products_write_db(context)
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Send product names, one per line.")
        return MASS_ADD_NAMES

    # Split by newlines, strip each, remove empty
    names = [n.strip() for n in text.split("\n") if n.strip()]
    if not names:
        await update.message.reply_text("❌ No valid names found. Send one name per line.")
        return MASS_ADD_NAMES

    created = []
    for name in names:
        try:
            pid = await main_db.add_product(
                name=name, price=0,
                description="", trial_file_ids="[]", channel_link="",
            )
            created.append(f"✅ #{pid} — {name}")
        except Exception as e:
            created.append(f"❌ {name} — Error: {str(e)[:50]}")

    result_text = (
        f"📦 *Mass Add Complete!*\n\n"
        f"Created *{len(created)}* products:\n\n" +
        "\n".join(created) +
        "\n\n📝 Use 🎁 *Edit Product* to set price, media, channel link for each."
    )
    await update.message.reply_text(result_text, parse_mode="Markdown")
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  EDIT PRODUCT (Main bot only)
# ═══════════════════════════════════════════════

async def _show_edit_products(update, context):
    main_db = _get_products_write_db(context)
    query = update.callback_query
    products = await main_db.get_all_products(active_only=False)
    if not products:
        await query.edit_message_text(
            "❌ No products found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]),
        )
        return ADMIN_MENU
    keyboard = []
    for p in products:
        status = "✅" if p.get("is_active") else "❌"
        keyboard.append([InlineKeyboardButton(
            f"{status} {p['name']} — {format_price(p['price'])}", callback_data=f"adm_ep_{p['id']}",
        )])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])
    await query.edit_message_text("🎁 *Edit Product*\n\nSelect a product:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return EP_SELECT


async def _ep_select(update, context, override_product_id=None):
    main_db = _get_products_write_db(context)
    query = update.callback_query
    await query.answer()
    if query.data == "adm_back":
        return await _show_admin_menu(update, context)
    if override_product_id is not None:
        product_id = override_product_id
    else:
        product_id = int(query.data.split("_")[2])
    product = await main_db.get_product(product_id)
    if not product:
        await query.answer("❌ Product not found.", show_alert=True)
        return await _show_edit_products(update, context)
    context.user_data["edit_product_id"] = product_id
    trial_count = len(product.get("trial_file_ids_list", []))
    status = "Active ✅" if product.get("is_active") else "Inactive ❌"
    style = product.get("button_style", "")
    style_label = {"primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red"}.get(style, "⚪ Default")
    # Escape user-provided fields to prevent Markdown parsing errors (e.g. underscores in @usernames)
    safe_name = product['name'].replace("_", "\\_").replace("*", "\\*")
    safe_desc = (product.get('description') or 'None').replace("_", "\\_").replace("*", "\\*")
    safe_channel = (product.get('channel_link') or 'Not set').replace("_", "\\_").replace("*", "\\*")
    text = (
        f"🎁 *Editing: {safe_name}*\n\n"
        f"💰 Price: {format_price(product['price'])}\n📝 Description: {safe_desc}\n"
        f"🎬 Trial: {trial_count}\n🔗 Channel: {safe_channel}\n📊 Status: {status}\n🎨 Button Color: {style_label}\n\nSelect field:"
    )
    keyboard = [
        [InlineKeyboardButton("📝 Name", callback_data="adm_ef_name")],
        [InlineKeyboardButton("💰 Price", callback_data="adm_ef_price")],
        [InlineKeyboardButton("📄 Description", callback_data="adm_ef_description")],
        [InlineKeyboardButton("🎬 Trial Media", callback_data="adm_ef_trial")],
        [InlineKeyboardButton("🔗 Channel Link", callback_data="adm_ef_channel")],
        [InlineKeyboardButton(f"🎨 Button Color ({style_label})", callback_data="adm_ef_color")],
        [InlineKeyboardButton("🔄 Toggle Active/Inactive", callback_data="adm_ef_toggle")],
        [InlineKeyboardButton("🔙 Back", callback_data="adm_back")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return EP_FIELD


async def _ep_field(update, context):
    main_db = _get_products_write_db(context)
    query = update.callback_query
    await query.answer()
    if query.data == "adm_back":
        return await _show_admin_menu(update, context)
    field = query.data.split("_")[2]
    product_id = context.user_data.get("edit_product_id")
    if field == "toggle":
        product = await main_db.get_product(product_id)
        new_status = 0 if product.get("is_active") else 1
        await main_db.update_product(product_id, is_active=new_status)
        await query.answer(f"Product is now {'Active ✅' if new_status else 'Inactive ❌'}", show_alert=True)
        return await _ep_select(update, context, override_product_id=product_id)
    if field == "color":
        product = await main_db.get_product(product_id)
        current_style = product.get("button_style", "")
        current_label = {"primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red"}.get(current_style, "⚪ Default")
        keyboard = [
            [InlineKeyboardButton(f"{'✅ ' if current_style == 'primary' else ''}🔵 Blue (Primary)", callback_data="adm_ec_primary")],
            [InlineKeyboardButton(f"{'✅ ' if current_style == 'success' else ''}🟢 Green (Success)", callback_data="adm_ec_success")],
            [InlineKeyboardButton(f"{'✅ ' if current_style == 'danger' else ''}🔴 Red (Danger)", callback_data="adm_ec_danger")],
            [InlineKeyboardButton(f"{'✅ ' if current_style == '' else ''}⚪ Default (No Color)", callback_data="adm_ec_default")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"adm_ep_{product_id}")],
        ]
        await query.edit_message_text(
            f"🎨 *Select Button Color for:* `{product['name']}`\n\n"
            f"Current: {current_label}\n\n"
            "Choose a color below:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return EP_FIELD
    context.user_data["edit_field"] = field
    if field == "trial":
        context.user_data["edit_trial_ids"] = []
        await query.edit_message_text(
            "🎬 *Edit Trial Media*\n\nSend new trial videos/photos one by one.\nThese will *replace* all existing trial media.\n\nType *done* when finished.",
            parse_mode="Markdown",
        )
        return EP_VALUE
    prompts = {"name": "📝 Enter new product name:", "price": "💰 Enter new price (in ₹):",
               "description": "📄 Enter new description (or type `clear` to remove):", "channel": "🔗 Enter new channel link:"}
    await query.edit_message_text(prompts.get(field, f"Enter new value for {field}:"))
    return EP_VALUE


async def _ep_color_select(update, context):
    """Handle button color selection from the color picker."""
    main_db = _get_products_write_db(context)
    query = update.callback_query
    await query.answer()
    product_id = context.user_data.get("edit_product_id")
    color_key = query.data.split("_")[2]  # adm_ec_primary -> primary
    style_value = "" if color_key == "default" else color_key
    style_label = {"primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red"}.get(style_value, "⚪ Default")
    await main_db.update_product(product_id, button_style=style_value)
    await query.answer(f"✅ Button color set to {style_label}", show_alert=True)
    # Go back to edit product field selection
    return await _ep_select(update, context, override_product_id=product_id)


async def _ep_value(update, context):
    main_db = _get_products_write_db(context)
    product_id = context.user_data.get("edit_product_id")
    field = context.user_data.get("edit_field")

    if field == "trial":
        if update.message.text and update.message.text.strip().lower() == "done":
            trial_ids = context.user_data.get("edit_trial_ids", [])
            await main_db.update_product(product_id, trial_file_ids=json.dumps(trial_ids))
            await update.message.reply_text(f"✅ Trial media updated! ({len(trial_ids)} items)")
            return await _show_admin_menu(update, context)
        file_id = None
        if update.message.photo:
            file_id = "photo:" + update.message.photo[-1].file_id
        elif update.message.video:
            file_id = "video:" + update.message.video.file_id
        elif update.message.animation:
            file_id = update.message.animation.file_id
        elif update.message.document:
            file_id = update.message.document.file_id
        if file_id:
            context.user_data.setdefault("edit_trial_ids", []).append(file_id)
            count = len(context.user_data["edit_trial_ids"])
            await update.message.reply_text(f"✅ Media #{count} added! Send more or type *done*.", parse_mode="Markdown")
            return EP_VALUE
        await update.message.reply_text("❌ Send a video/photo or type *done*.", parse_mode="Markdown")
        return EP_VALUE

    if not update.message.text:
        await update.message.reply_text("❌ Please send valid text.")
        return EP_VALUE
        
    value = update.message.text.strip()
    if field == "price":
        try:
            value = float(value)
            if value <= 0: raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Enter a valid positive number:")
            return EP_VALUE
        await main_db.update_product(product_id, price=value)
    elif field == "name":
        if not value:
            await update.message.reply_text("❌ Name cannot be empty:")
            return EP_VALUE
        await main_db.update_product(product_id, name=value)
    elif field == "description":
        if value.lower() == "clear": value = ""
        await main_db.update_product(product_id, description=value)
    elif field == "channel":
        await main_db.update_product(product_id, channel_link=value)
    await update.message.reply_text("✅ Product updated successfully!")
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  REMOVE PRODUCT (Main bot only)
# ═══════════════════════════════════════════════

async def _show_remove_products(update, context):
    main_db = _get_products_write_db(context)
    query = update.callback_query
    products = await main_db.get_all_products(active_only=False)
    if not products:
        await query.edit_message_text(
            "❌ No products to remove.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]),
        )
        return ADMIN_MENU
    keyboard = []
    for p in products:
        keyboard.append([InlineKeyboardButton(
            f"🗑 {p['name']} — {format_price(p['price'])}", callback_data=f"adm_rp_{p['id']}",
        )])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])
    await query.edit_message_text("🗑 *Remove Product*\n\nSelect a product:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return RP_SELECT


async def _rp_select(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "adm_back":
        return await _show_admin_menu(update, context)
    product_id = int(query.data.split("_")[2])
    main_db = _get_products_write_db(context)
    product = await main_db.get_product(product_id)
    if not product:
        return await _show_admin_menu(update, context)
    context.user_data["remove_product_id"] = product_id
    keyboard = [[InlineKeyboardButton("✅ Yes, Remove", callback_data="adm_rc_yes"), InlineKeyboardButton("❌ Cancel", callback_data="adm_rc_no")]]
    await query.edit_message_text(
        f"⚠️ *Confirm Removal*\n\n📦 *{product['name']}* — {format_price(product['price'])}?\n\n⚠️ Cannot be undone!",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
    )
    return RP_CONFIRM


async def _rp_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "adm_rc_yes":
        product_id = context.user_data.get("remove_product_id")
        if product_id:
            main_db = _get_products_write_db(context)
            await main_db.delete_product(product_id)
            await query.answer("✅ Product removed!", show_alert=True)
    context.user_data.pop("remove_product_id", None)
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  MASS REMOVE ALL PRODUCTS (Hosted bot only)
# ═══════════════════════════════════════════════

async def _mass_remove_confirm(update, context):
    """Show confirmation prompt before mass removing all products."""
    query = update.callback_query
    if is_main_bot(context):
        await query.answer("❌ Not available on main bot.", show_alert=True)
        return await _show_admin_menu(update, context)

    db = get_db(context)
    count = await db.get_product_count()
    if count == 0:
        await query.answer("❌ No products to remove.", show_alert=True)
        return await _show_admin_menu(update, context)

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Remove ALL", callback_data="adm_mass_remove_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="adm_back"),
        ]
    ]
    await query.edit_message_text(
        f"⚠️ *Mass Remove All Products*\n\n"
        f"This will permanently delete ALL *{count}* products from this bot.\n\n"
        f"⚠️ *This action cannot be undone!*\n"
        f"Use 🔄 Reset Products to Main to re-clone from main bot.\n\n"
        f"Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return ADMIN_MENU


async def _mass_remove_execute(update, context):
    """Execute mass removal of all products for hosted bot."""
    query = update.callback_query
    await query.answer()

    if is_main_bot(context):
        await query.answer("❌ Not available on main bot.", show_alert=True)
        return await _show_admin_menu(update, context)

    db = get_db(context)
    t = db._t
    # Delete all products from hosted bot's table
    await db.execute(f"DELETE FROM {t('products')}")

    await query.answer("✅ All products removed!", show_alert=True)
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  TOGGLES — writes to per-bot DB for hosted bots
# ═══════════════════════════════════════════════

async def _toggle_setting(update, context, key, label, main_only=False):
    """Toggle a boolean setting. Writes to correct DB based on context."""
    if main_only:
        settings_db = get_main_db(context)
    else:
        settings_db = _get_settings_db(context)
    current = await settings_db.get_setting(key)
    if current is None:
        # Hosted bot first toggle — get from main to know current state
        main_db = get_main_db(context)
        current = await main_db.get_setting(key)
    new_value = "0" if current == "1" else "1"
    await settings_db.set_setting(key, new_value)
    status = "ON ✅" if new_value == "1" else "OFF ❌"
    scope = "(ALL BOTS)" if main_only else ""
    await update.callback_query.answer(f"{label} is now {status} {scope}", show_alert=True)
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET PROOF LINK / PHOTO
# ═══════════════════════════════════════════════

async def _set_proof_link(update, context):
    settings_db = _get_settings_db(context)
    link = update.message.text.strip()
    # Auto-prepend https:// if missing — Telegram requires valid URL scheme
    if link and not link.startswith(("http://", "https://", "tg://")):
        link = "https://" + link
    await settings_db.set_setting("proof_link", link)
    await update.message.reply_text(f"✅ Proof link updated to:\n{link}")
    return await _show_admin_menu(update, context)


async def _set_proof_photo(update, context):
    settings_db = _get_settings_db(context)
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await settings_db.set_setting("proof_photo_id", file_id)
        await update.message.reply_text("✅ Proof photo updated!")
    else:
        await update.message.reply_text("❌ Please send a photo.")
        return SET_PROOF_PHOTO_INPUT
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  COLORS INFO
# ═══════════════════════════════════════════════

async def _show_colors_info(update, context):
    query = update.callback_query
    text = (
        "🎨 *Button Color Settings*\n\n"
        "Telegram now supports colourful buttons! 🎉\n\n"
        "📌 *Available Colors:*\n"
        "🔵 Blue (Primary) — Main actions\n"
        "🟢 Green (Success) — Positive actions\n"
        "🔴 Red (Danger) — Destructive actions\n"
        "⚪ Default — Standard look\n\n"
        "📝 *How to set:*\n"
        "Go to 🎁 Edit Product → Select a product → 🎨 Button Color\n\n"
        "🔒 *Fixed colors (auto):*\n"
        "✅ I Have Paid → 🟢 Green\n"
        "❌ Cancel → 🔴 Red\n"
        "💰 Buy Now → 🔵 Blue\n"
        "✅ Approve → 🟢 Green\n"
        "❌ Reject → 🔴 Red"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_MENU


# ═══════════════════════════════════════════════
#  SET AUTO DISAPPEAR TIME — Main owner only (GLOBAL)
# ═══════════════════════════════════════════════

async def _set_disappear_time(update, context):
    """Set custom auto-disappear time in seconds. Main bot owner only."""
    text = update.message.text.strip()
    try:
        seconds = int(text)
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number in seconds.\n\n_Example: 300 = 5 minutes_", parse_mode="Markdown")
        return SET_DISAPPEAR_TIME

    if seconds < 60:
        await update.message.reply_text("❌ Minimum time is *60 seconds* (1 minute).", parse_mode="Markdown")
        return SET_DISAPPEAR_TIME
    if seconds > 86400:
        await update.message.reply_text("❌ Maximum time is *86400 seconds* (24 hours).", parse_mode="Markdown")
        return SET_DISAPPEAR_TIME

    main_db = get_main_db(context)
    await main_db.set_setting("auto_disappear_seconds", str(seconds))

    if seconds >= 60:
        display = f"{seconds} seconds ({seconds // 60} min)"
    else:
        display = f"{seconds} seconds"

    await update.message.reply_text(
        f"✅ Auto Disappear time set to *{display}* (ALL BOTS)",
        parse_mode="Markdown",
    )
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  BROADCAST — Per-bot DB users
# ═══════════════════════════════════════════════

async def _bc_message(update, context):
    db = get_db(context)
    context.user_data["broadcast_message"] = update.message
    user_count = await db.get_user_count()
    keyboard = [[InlineKeyboardButton("✅ Send to All", callback_data="adm_bc_yes"), InlineKeyboardButton("❌ Cancel", callback_data="adm_bc_no")]]
    await update.message.reply_text(
        f"📢 *Broadcast Confirmation*\n\nThis message will be sent to *{user_count}* users.\n\nAre you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
    )
    return BC_CONFIRM


async def _bc_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "adm_bc_yes":
        msg = context.user_data.get("broadcast_message")
        if msg:
            await query.edit_message_text(
                "📢 *Broadcasting started!*\n\n"
                "⏳ Background mein chal raha hai...\n"
                "Bot normally kaam karega — koi freeze nahi hoga.\n"
                "Result aayega jab complete ho jayega.",
                parse_mode="Markdown"
            )
            chat_id = query.message.chat_id
            # Run broadcast in background — bot will NOT freeze
            asyncio.create_task(
                _run_broadcast_background(context, msg, chat_id)
            )
    context.user_data.pop("broadcast_message", None)
    return await _show_admin_menu(update, context)


async def _run_broadcast_background(context, msg, chat_id):
    """Background broadcast — bot remains 100% responsive during this."""
    try:
        progress_msg = None

        async def progress_callback(current, total, success, failed):
            nonlocal progress_msg
            pct = int(100 * current / total) if total else 100
            bar_len = 20
            filled = int(bar_len * current / total) if total else bar_len
            bar = "█" * filled + "░" * (bar_len - filled)
            text = (
                f"📢 *Broadcasting...*\n\n"
                f"`{bar}` {pct}%\n"
                f"📦 {current}/{total} processed\n"
                f"✅ {success} sent | ❌ {failed} failed"
            )
            try:
                if progress_msg:
                    await progress_msg.edit_text(text, parse_mode="Markdown")
                else:
                    progress_msg = await context.bot.send_message(
                        chat_id=chat_id, text=text, parse_mode="Markdown"
                    )
            except Exception:
                pass

        result = await broadcast_message(
            context, from_message=msg, status_callback=progress_callback
        )

        # ── Build detailed report ──
        errors = result["errors"]
        report = (
            f"📢 *Broadcast Complete!*\n\n"
            f"✅ Success: *{result['success']}*\n"
            f"❌ Failed: *{result['failed']}*\n"
            f"📊 Total: *{result['total']}*\n"
        )

        if errors["blocked"]:
            report += f"\n🚫 *Blocked (403):* {len(errors['blocked'])} users"
            report += f"\n   _Auto-cleaned from DB ✅_"
        if errors["deactivated"]:
            report += f"\n👻 *Deleted/Deactivated:* {len(errors['deactivated'])} users"
            report += f"\n   _Auto-cleaned from DB ✅_"
        if errors["rate_limited"]:
            report += f"\n⏱ *Rate Limited:* {len(errors['rate_limited'])} users"
        if errors["other"]:
            report += f"\n⚠️ *Other Errors:* {len(errors['other'])} users"

        if not any(len(v) for v in errors.values()):
            report += "\n\n🎉 *Zero failures! Perfect broadcast!*"
        else:
            cleaned = len(errors["blocked"]) + len(errors["deactivated"])
            if cleaned > 0:
                report += f"\n\n🧹 *{cleaned} dead users cleaned* — next broadcast will be faster!"

        # Delete progress message
        if progress_msg:
            try:
                await progress_msg.delete()
            except Exception:
                pass

        await context.bot.send_message(
            chat_id=chat_id, text=report, parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Background broadcast error: {e}")
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=f"❌ Broadcast error: {str(e)[:200]}"
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════
#  SET UPI / QR — writes to per-bot DB for hosted bots
# ═══════════════════════════════════════════════

async def _set_upi(update, context):
    settings_db = _get_settings_db(context)
    upi = update.message.text.strip()
    if not upi or "@" not in upi:
        await update.message.reply_text("❌ Enter a valid UPI ID (e.g., name@upi):")
        return SET_UPI_INPUT
    await settings_db.set_setting("upi_id", upi)
    await update.message.reply_text(f"✅ UPI ID updated to: `{upi}`", parse_mode="Markdown")
    return await _show_admin_menu(update, context)


async def _set_qr_image(update, context):
    settings_db = _get_settings_db(context)
    if update.message.text and update.message.text.strip().lower() == "remove":
        await settings_db.set_setting("qr_image_id", "")
        await update.message.reply_text("✅ Custom QR removed. Auto-generated QR will be used.")
        return await _show_admin_menu(update, context)
    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    if file_id:
        await settings_db.set_setting("qr_image_id", file_id)
        await update.message.reply_text("✅ Custom QR image updated!")
    else:
        await update.message.reply_text("❌ Send a photo or type `remove`.", parse_mode="Markdown")
        return SET_QR_INPUT
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET WELCOME MEDIA — per-bot override
# ═══════════════════════════════════════════════

async def _set_welcome(update, context):
    settings_db = _get_settings_db(context)
    if update.message.text:
        t = update.message.text.strip().lower()
        if t == "clear":
            await settings_db.set_setting("welcome_media_ids", "[]")
            await settings_db.set_setting("welcome_video_id", "")
            await settings_db.set_setting("welcome_photo_id", "")
            await update.message.reply_text("✅ Welcome media cleared!")
            return await _show_admin_menu(update, context)
        elif t == "done":
            ids = context.user_data.get("welcome_ids", [])
            await settings_db.set_setting("welcome_media_ids", json.dumps(ids))
            await update.message.reply_text(f"✅ Welcome media updated! ({len(ids)} items)")
            return await _show_admin_menu(update, context)
        await update.message.reply_text("❌ Send a video/photo, or type *done* / *clear*.", parse_mode="Markdown")
        return SET_WELCOME_INPUT
    file_id = None
    if update.message.photo:
        file_id = "photo:" + update.message.photo[-1].file_id
    elif update.message.video:
        file_id = "video:" + update.message.video.file_id
    elif update.message.animation:
        file_id = update.message.animation.file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    if file_id:
        context.user_data.setdefault("welcome_ids", []).append(file_id)
        count = len(context.user_data["welcome_ids"])
        await update.message.reply_text(f"✅ Media #{count} added! Send more or type *done*.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Please send a valid video/photo.", parse_mode="Markdown")
    return SET_WELCOME_INPUT


# ═══════════════════════════════════════════════
#  SET HOW TO USE — per-bot override
# ═══════════════════════════════════════════════

async def _set_htu_video(update, context):
    if update.message.video:
        file_id = update.message.video.file_id
    elif update.message.animation:
        file_id = update.message.animation.file_id
    else:
        await update.message.reply_text("❌ Please send a valid video.")
        return SET_HTU_VIDEO
    context.user_data["htu_video"] = file_id
    await update.message.reply_text("✅ Video saved. Now send the description text:")
    return SET_HTU_TEXT


async def _set_htu_text(update, context):
    settings_db = _get_settings_db(context)
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Text cannot be empty.")
        return SET_HTU_TEXT
    await settings_db.set_setting("htu_video_id", context.user_data["htu_video"])
    await settings_db.set_setting("htu_text", text)
    await update.message.reply_text("✅ How to Use updated successfully!")
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  WELCOME TEXT MANAGEMENT
# ═══════════════════════════════════════════════

async def _show_welcome_text_menu(update, context):
    """Show welcome text management sub-menu."""
    settings_db = _get_settings_db(context)
    current_text = await settings_db.get_setting("welcome_text")
    if not current_text and not is_main_bot(context):
        main_db = get_main_db(context)
        current_text = await main_db.get_setting("welcome_text")
    status = "✅ Custom text set" if current_text else "⚪ Using default"
    keyboard = [
        [InlineKeyboardButton("✏️ Edit / Set New", callback_data="adm_wt_edit")],
        [InlineKeyboardButton("👁 Preview Current", callback_data="adm_wt_preview")],
        [InlineKeyboardButton("🗑 Remove (Use Default)", callback_data="adm_wt_remove")],
        [InlineKeyboardButton("🔙 Back", callback_data="adm_back")],
    ]
    query = update.callback_query
    await query.edit_message_text(
        f"📝 *Welcome Text Management*\n\nStatus: {status}\n\nSelect an option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return WELCOME_TEXT_MENU


async def _welcome_text_menu_handler(update, context):
    """Handle welcome text sub-menu button clicks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "adm_wt_edit":
        await query.edit_message_text(
            "📝 *Set Welcome Text*\n\n"
            "Send your new welcome text below.\n"
            "You can use Telegram formatting:\n"
            "• `*bold*` → *bold*\n"
            "• `_italic_` → _italic_\n"
            "• `` `code` `` → `code`\n"
            "• Emojis supported ✨🎉💰\n\n"
            "_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_WELCOME_TEXT

    elif data == "adm_wt_preview":
        from handlers.start import WELCOME_TEXT as DEFAULT_WELCOME
        settings_db = _get_settings_db(context)
        current_text = await settings_db.get_setting("welcome_text")
        if not current_text and not is_main_bot(context):
            main_db = get_main_db(context)
            current_text = await main_db.get_setting("welcome_text")
        preview = current_text if current_text else DEFAULT_WELCOME
        source = "📝 Custom Text" if current_text else "⚪ Default Text"
        try:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"👁 *Preview* ({source}):\n\n─────────────\n\n{preview}\n\n─────────────",
                parse_mode="Markdown",
            )
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"👁 Preview ({source}):\n\n─────────────\n\n{preview}\n\n─────────────",
            )
        return await _show_welcome_text_menu(update, context)

    elif data == "adm_wt_remove":
        settings_db = _get_settings_db(context)
        await settings_db.set_setting("welcome_text", "")
        await query.answer("✅ Welcome text removed! Using default now.", show_alert=True)
        return await _show_admin_menu(update, context)

    elif data == "adm_back":
        return await _show_admin_menu(update, context)

    return WELCOME_TEXT_MENU


async def _set_welcome_text_input(update, context):
    """Receive and save new welcome text from admin."""
    settings_db = _get_settings_db(context)
    text = update.message.text
    if not text or not text.strip():
        await update.message.reply_text("❌ Text cannot be empty. Send your welcome text:")
        return SET_WELCOME_TEXT
    await settings_db.set_setting("welcome_text", text.strip())
    await update.message.reply_text("✅ Welcome text updated successfully!")
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  VIEW USERS (Paginated) — Per-bot DB
# ═══════════════════════════════════════════════

async def _show_users_list(update, context, page=0):
    db = get_db(context)
    query = update.callback_query
    users = await db.get_all_users()
    owner_ids = get_owner_ids(context)
    users = [u for u in users if u.get("user_id") not in owner_ids]

    total = len(users)
    per_page = 10
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]
    total_pages = max(1, (total + per_page - 1) // per_page)

    if not users:
        text = "👥 *No users yet.*"
    else:
        text = f"👥 *Users* (Page {page + 1}/{total_pages})\n📊 Total: {total}\n\n"
        for i, user in enumerate(page_users, start=start + 1):
            username = f"@{escape_md(user['username'])}" if user.get("username") else "N/A"
            text += f"*{i}.* {escape_md(user.get('first_name', 'Unknown'))} ({username})\n    🆔 `{user['user_id']}`\n"

    keyboard = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_vu_p_{page - 1}"))
    if end < total:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"adm_vu_p_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=text,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
        )
    return ADMIN_MENU


async def _users_page_cb(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "adm_back":
        return await _show_admin_menu(update, context)
    page = int(query.data.split("_")[3])
    return await _show_users_list(update, context, page=page)


# ═══════════════════════════════════════════════
#  STATS — Per-bot DB
# ═══════════════════════════════════════════════

async def _show_stats(update, context):
    db = get_db(context)
    products_db = await get_products_db(context)
    query = update.callback_query
    stats = await db.get_stats()
    products = await products_db.get_all_products(active_only=False)
    active_count = sum(1 for p in products if p.get("is_active"))

    text = (
        "📊 *Bot Statistics*\n\n"
        f"👥 Total Users: *{stats['total_users']}*\n\n"
        f"📦 Total Orders: *{stats['total_orders']}*\n"
        f"✅ Approved: *{stats['approved_orders']}*\n"
        f"⏳ Pending: *{stats['pending_orders']}*\n"
        f"❌ Rejected: *{stats['rejected_orders']}*\n\n"
        f"💰 Total Revenue: *{format_price(stats['total_revenue'])}*\n\n"
        f"📦 Products: *{len(products)}* (Active: {active_count})"
    )
    keyboard = []
    if stats["pending_orders"] > 0:
        keyboard.append([InlineKeyboardButton(f"📋 View Pending Orders ({stats['pending_orders']})", callback_data="adm_pending")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_MENU


async def _show_pending_orders(update, context, page=0):
    db = get_db(context)
    products_db = await get_products_db(context)
    query = update.callback_query
    chat_id = query.message.chat_id
    orders = await db.get_pending_orders()

    if not orders:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_stats")]]
        await query.edit_message_text("📋 *Pending Orders*\n\n✅ No pending orders!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return ADMIN_MENU

    per_page = 5
    total_pages = (len(orders) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    page_orders = orders[page * per_page:(page + 1) * per_page]

    try:
        await query.message.delete()
    except Exception:
        pass

    for order in page_orders:
        product = await products_db.get_product(order["product_id"])
        user_info = await db.get_user(order["user_id"])
        safe_product = html.escape(product["name"]) if product else "Unknown"
        safe_price = html.escape(format_price(product["price"])) if product else "?"
        safe_name = html.escape(user_info["first_name"] or "Unknown") if user_info else "Unknown"

        order_text = (
            f"🔔 <b>Pending — Order #{order['id']}</b>\n\n"
            f"👤 User: {safe_name}\n🆔 User ID: <code>{order['user_id']}</code>\n"
            f"📦 Plan: <b>{safe_product}</b>\n💰 Price: <b>{safe_price}</b>\n"
            f"📝 Proof: {(order.get('proof_type') or 'none').upper()}"
        )
        keyboard = [[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{order['id']}"),
        ]]
        try:
            if order.get("proof_type") == "screenshot" and order.get("payment_proof"):
                await context.bot.send_photo(chat_id=chat_id, photo=order["payment_proof"], caption=order_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=chat_id, text=order_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send pending order #{order['id']}: {e}")

    nav_keyboard = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_po_p_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm_po_p_{page + 1}"))
    if nav_buttons:
        nav_keyboard.append(nav_buttons)
    nav_keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_stats")])
    await context.bot.send_message(chat_id=chat_id, text=f"📋 <b>Pending Orders</b> — Page {page + 1}/{total_pages}", reply_markup=InlineKeyboardMarkup(nav_keyboard), parse_mode="HTML")
    return ADMIN_MENU


async def _pending_page_cb(update, context):
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    page = int(query.data.split("_")[-1])
    return await _show_pending_orders(update, context, page=page)


# ═══════════════════════════════════════════════
#  MANAGE HOSTING ADMINS — Main Bot Owner Only
# ═══════════════════════════════════════════════

async def _show_manage_admins(update, context):
    main_db = get_main_db(context)
    query = update.callback_query
    admins = await main_db.get_all_hosting_admins()
    text = "👑 *Manage Hosting Admins*\n\n"
    if admins:
        for i, admin in enumerate(admins, 1):
            added_date = admin.get('added_at', '?')[:10] if admin.get('added_at') else '?'
            expires = admin.get('expires_at', '')
            if expires:
                text += f"{i}. User ID: `{admin['user_id']}` (added: {added_date}) ⏱ Expires: {expires[:10]}\n"
            else:
                text += f"{i}. User ID: `{admin['user_id']}` (added: {added_date}) ♾ No Expiry\n"
    else:
        text += "_No hosting admins yet._\n"
    text += "\nSelect an action:"
    keyboard = [
        [InlineKeyboardButton("➕ Add Admin", callback_data="adm_ma_add")],
        [InlineKeyboardButton("❌ Remove Admin", callback_data="adm_ma_remove")],
        [InlineKeyboardButton("🔙 Back", callback_data="adm_back")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return MANAGE_ADMIN_MENU


async def _manage_admin_action(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "adm_back":
        return await _show_admin_menu(update, context)
    elif query.data == "adm_ma_add":
        await query.edit_message_text("➕ *Add Hosting Admin*\n\nSend the Telegram User ID:\n\n_Send /cancel to go back_", parse_mode="Markdown")
        context.user_data["manage_admin_action"] = "add"
        return MANAGE_ADMIN_INPUT
    elif query.data == "adm_ma_remove":
        main_db = get_main_db(context)
        admins = await main_db.get_all_hosting_admins()
        if not admins:
            await query.answer("No admins to remove.", show_alert=True)
            return await _show_manage_admins(update, context)
        keyboard = []
        for admin in admins:
            keyboard.append([InlineKeyboardButton(f"❌ Remove {admin['user_id']}", callback_data=f"adm_ma_rm_{admin['user_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])
        await query.edit_message_text("❌ *Remove Hosting Admin*\n\nSelect admin to remove:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return MANAGE_ADMIN_MENU
    elif query.data.startswith("adm_ma_rm_"):
        user_id_to_remove = int(query.data.split("_")[3])
        main_db = get_main_db(context)
        await main_db.remove_hosting_admin(user_id_to_remove)
        hosted = await main_db.get_hosted_bot_by_admin(user_id_to_remove)
        if hosted:
            bot_manager = context.bot_data.get("bot_manager")
            if bot_manager:
                try:
                    await bot_manager.delete_bot(hosted["id"])
                except Exception as e:
                    logger.warning(f"Failed to delete hosted bot for removed admin: {e}")
        await query.answer(f"✅ Admin {user_id_to_remove} removed!", show_alert=True)
        return await _show_manage_admins(update, context)
    return MANAGE_ADMIN_MENU


async def _manage_admin_input(update, context):
    main_db = get_main_db(context)
    action = context.user_data.get("manage_admin_action")
    if action == "add":
        try:
            new_admin_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID. Send a valid numeric ID:")
            return MANAGE_ADMIN_INPUT
        context.user_data["new_admin_id"] = new_admin_id
        # Show duration selection
        keyboard = [
            [
                InlineKeyboardButton("7 Days", callback_data="adm_dur_7d"),
                InlineKeyboardButton("15 Days", callback_data="adm_dur_15d"),
            ],
            [
                InlineKeyboardButton("1 Month", callback_data="adm_dur_1m"),
                InlineKeyboardButton("♾ No Expiry", callback_data="adm_dur_never"),
            ],
        ]
        await update.message.reply_text(
            f"⏱ *Set Admin Duration*\n\n"
            f"User ID: `{new_admin_id}`\n\n"
            f"How long should this admin have access?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return MANAGE_ADMIN_DURATION
    return await _show_admin_menu(update, context)


async def _manage_admin_duration(update, context):
    """Handle duration selection for new admin."""
    from datetime import datetime, timedelta
    query = update.callback_query
    await query.answer()
    data = query.data

    main_db = get_main_db(context)
    new_admin_id = context.user_data.get("new_admin_id")
    if not new_admin_id:
        await query.edit_message_text("❌ Something went wrong. Please try again.")
        return await _show_admin_menu(update, context)

    now = datetime.utcnow()
    duration_map = {
        "adm_dur_7d": ("7 Days", now + timedelta(days=7)),
        "adm_dur_15d": ("15 Days", now + timedelta(days=15)),
        "adm_dur_1m": ("1 Month", now + timedelta(days=30)),
        "adm_dur_never": ("No Expiry", None),
    }

    label, expires_at = duration_map.get(data, ("No Expiry", None))
    expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else ""

    await main_db.add_hosting_admin(new_admin_id, added_by=OWNER_ID, expires_at=expires_str)

    expiry_text = expires_at.strftime("%Y-%m-%d %H:%M") if expires_at else "Never"
    await query.edit_message_text(
        f"✅ User `{new_admin_id}` added as hosting admin!\n\n"
        f"⏱ Duration: *{label}*\n"
        f"📅 Expires: {expiry_text}",
        parse_mode="Markdown",
    )

    context.user_data.pop("manage_admin_action", None)
    context.user_data.pop("new_admin_id", None)
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET DEMO LINK — per-bot override
# ═══════════════════════════════════════════════

async def _set_demo_link(update, context):
    """Receive and save demo link from admin."""
    settings_db = _get_settings_db(context)
    link = update.message.text.strip()
    # Auto-prepend https:// if missing — Telegram requires valid URL scheme
    if link and not link.startswith(("http://", "https://", "tg://")):
        link = "https://" + link
    await settings_db.set_setting("demo_link", link)
    await update.message.reply_text(f"✅ Demo link updated to:\n{link}")
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  BUTTON COLOR PICKER — Demo & Proof button colors
# ═══════════════════════════════════════════════

async def _show_button_color_picker(update, context):
    """Show color picker for demo/proof button color."""
    query = update.callback_query
    # adm_btnclr_demo_button_color or adm_btnclr_proof_button_color
    setting_key = query.data.replace("adm_btnclr_", "")
    context.user_data["btnclr_setting_key"] = setting_key

    current_color = await _get_current_setting(context, setting_key) or ""
    current_label = {"primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red"}.get(current_color, "⚪ Default")

    btn_label = "Demo Button" if "demo" in setting_key else "Proof Button"
    keyboard = [
        [InlineKeyboardButton(f"{'✅ ' if current_color == 'primary' else ''}🔵 Blue", callback_data="adm_btnclr_set_primary")],
        [InlineKeyboardButton(f"{'✅ ' if current_color == 'success' else ''}🟢 Green", callback_data="adm_btnclr_set_success")],
        [InlineKeyboardButton(f"{'✅ ' if current_color == 'danger' else ''}🔴 Red", callback_data="adm_btnclr_set_danger")],
        [InlineKeyboardButton(f"{'✅ ' if current_color == '' else ''}⚪ Default", callback_data="adm_btnclr_set_default")],
        [InlineKeyboardButton("🔙 Back", callback_data="adm_back")],
    ]
    await query.edit_message_text(
        f"🎨 *{btn_label} Color*\n\nCurrent: {current_label}\n\nChoose a color:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return ADMIN_MENU


async def _button_color_selected(update, context):
    """Handle color selection for demo/proof button."""
    query = update.callback_query
    await query.answer()
    setting_key = context.user_data.get("btnclr_setting_key", "")
    if not setting_key:
        return await _show_admin_menu(update, context)

    # adm_btnclr_set_primary -> primary
    color_key = query.data.replace("adm_btnclr_set_", "")
    color_value = "" if color_key == "default" else color_key
    color_label = {"primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red"}.get(color_value, "⚪ Default")

    settings_db = _get_settings_db(context)
    await settings_db.set_setting(setting_key, color_value)

    btn_label = "Demo Button" if "demo" in setting_key else "Proof Button"
    await query.answer(f"✅ {btn_label} color set to {color_label}", show_alert=True)
    context.user_data.pop("btnclr_setting_key", None)
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  BUY NOW SETTINGS — Hosted Bot Admin (if access granted)
# ═══════════════════════════════════════════════

async def _show_buynow_settings_menu(update, context):
    """Show Buy Now settings sub-menu for hosted bot admin."""
    query = update.callback_query

    price_list = await _get_current_setting(context, "buynow_price_list") or "Not set"
    channel_link = await _get_current_setting(context, "buynow_channel_link") or "Not set"
    plans_json = await _get_current_setting(context, "buynow_plans")
    try:
        plans = json.loads(plans_json) if plans_json else []
    except (json.JSONDecodeError, TypeError):
        plans = []

    text = (
        "💰 *Buy Now Settings*\n\n"
        f"📋 Price List: {price_list[:50]}{'...' if len(price_list) > 50 else ''}\n"
        f"🔗 Channel Link: {channel_link}\n"
        f"📦 Plans: {len(plans)}/3 configured\n\n"
        "Select an option:"
    )
    keyboard = [
        [InlineKeyboardButton("📋 Set Price List", callback_data="adm_bn_pricelist")],
        [InlineKeyboardButton("📦 Manage Plans", callback_data="adm_bn_plans")],
        [InlineKeyboardButton("🔗 Set Channel Link", callback_data="adm_bn_channel")],
        [InlineKeyboardButton("🔙 Back", callback_data="adm_back")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BUYNOW_SETTINGS_MENU


async def _buynow_settings_handler(update, context):
    """Handle Buy Now settings menu callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "adm_bn_pricelist":
        current = await _get_current_setting(context, "buynow_price_list")
        await query.edit_message_text(
            f"📋 *Set Buy Now Price List*\n\nCurrent:\n{current or 'Not set'}\n\n"
            "Send the price list text (can include emojis & formatting):\n\n_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_BUYNOW_PRICE_LIST
    elif data == "adm_bn_plans":
        return await _show_buynow_plans_menu(update, context)
    elif data == "adm_bn_channel":
        current = await _get_current_setting(context, "buynow_channel_link")
        await query.edit_message_text(
            f"🔗 *Set Buy Now Channel Link*\n\nCurrent: {current or 'Not set'}\n\n"
            "Send the channel invite link:\n_Example: https://t.me/+xxxxx_\n\n_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_BUYNOW_CHANNEL_LINK
    elif data == "adm_back":
        return await _show_admin_menu(update, context)

    return BUYNOW_SETTINGS_MENU


async def _set_buynow_price_list(update, context):
    """Receive and save Buy Now price list text."""
    settings_db = _get_settings_db(context)
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Price list cannot be empty. Try again:")
        return SET_BUYNOW_PRICE_LIST
    await settings_db.set_setting("buynow_price_list", text)
    await update.message.reply_text("✅ Buy Now price list updated!")
    return await _show_admin_menu(update, context)


async def _set_buynow_channel_link(update, context):
    """Receive and save Buy Now channel link."""
    settings_db = _get_settings_db(context)
    link = update.message.text.strip()
    await settings_db.set_setting("buynow_channel_link", link)
    await update.message.reply_text(f"✅ Buy Now channel link updated to:\n{link}")
    return await _show_admin_menu(update, context)


async def _show_buynow_plans_menu(update, context):
    """Show current plans and options to add/remove."""
    query = update.callback_query
    plans_json = await _get_current_setting(context, "buynow_plans")
    try:
        plans = json.loads(plans_json) if plans_json else []
    except (json.JSONDecodeError, TypeError):
        plans = []

    text = "📦 *Buy Now Plans*\n\n"
    if plans:
        for i, plan in enumerate(plans):
            color_label = {"primary": "🔵", "success": "🟢", "danger": "🔴"}.get(plan.get("color", ""), "⚪")
            text += f"{i+1}. {color_label} {plan.get('label', 'Plan')} — ₹{plan.get('price', 0)} / {plan.get('days', 0)} Days\n"
    else:
        text += "_No plans configured yet._\n"
    text += f"\n📌 Max 3 plans allowed ({len(plans)}/3)"

    keyboard = []
    if len(plans) < 3:
        keyboard.append([InlineKeyboardButton("➕ Add Plan", callback_data="adm_bn_add_plan")])
    if plans:
        for i, plan in enumerate(plans):
            keyboard.append([InlineKeyboardButton(f"🗑 Remove: {plan.get('label', f'Plan {i+1}')}", callback_data=f"adm_bn_rm_{i}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_bn_back_settings")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return BUYNOW_SETTINGS_MENU


async def _buynow_plans_handler(update, context):
    """Handle plan management callbacks."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "adm_bn_add_plan":
        context.user_data["buynow_new_plan"] = {}
        await query.edit_message_text(
            "➕ *Add Buy Now Plan*\n\n📝 Enter the plan label/name:\n_Example: Basic Plan_\n\n_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return BUYNOW_ADD_PLAN_LABEL
    elif data.startswith("adm_bn_rm_"):
        plan_index = int(data.replace("adm_bn_rm_", ""))
        settings_db = _get_settings_db(context)
        plans_json = await _get_current_setting(context, "buynow_plans")
        try:
            plans = json.loads(plans_json) if plans_json else []
        except (json.JSONDecodeError, TypeError):
            plans = []
        if 0 <= plan_index < len(plans):
            removed = plans.pop(plan_index)
            await settings_db.set_setting("buynow_plans", json.dumps(plans))
            await query.answer(f"✅ Plan '{removed.get('label', '')}' removed!", show_alert=True)
        return await _show_buynow_plans_menu(update, context)
    elif data == "adm_bn_back_settings":
        return await _show_buynow_settings_menu(update, context)
    elif data == "adm_back":
        return await _show_admin_menu(update, context)

    return BUYNOW_SETTINGS_MENU


async def _buynow_plan_label(update, context):
    """Receive plan label."""
    label = update.message.text.strip()
    if not label:
        await update.message.reply_text("❌ Label cannot be empty. Try again:")
        return BUYNOW_ADD_PLAN_LABEL
    context.user_data["buynow_new_plan"]["label"] = label
    await update.message.reply_text(
        f"✅ Label: *{label}*\n\n💰 Enter the plan price (in ₹):",
        parse_mode="Markdown",
    )
    return BUYNOW_ADD_PLAN_PRICE


async def _buynow_plan_price(update, context):
    """Receive plan price."""
    try:
        price = float(update.message.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid positive number:")
        return BUYNOW_ADD_PLAN_PRICE
    context.user_data["buynow_new_plan"]["price"] = price
    await update.message.reply_text(
        f"✅ Price: *₹{price}*\n\n📅 Enter the number of days for this plan:",
        parse_mode="Markdown",
    )
    return BUYNOW_ADD_PLAN_DAYS


async def _buynow_plan_days(update, context):
    """Receive plan days."""
    try:
        days = int(update.message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid positive number of days:")
        return BUYNOW_ADD_PLAN_DAYS
    context.user_data["buynow_new_plan"]["days"] = days
    keyboard = [
        [InlineKeyboardButton("🔵 Blue", callback_data="adm_bn_color_primary")],
        [InlineKeyboardButton("🟢 Green", callback_data="adm_bn_color_success")],
        [InlineKeyboardButton("🔴 Red", callback_data="adm_bn_color_danger")],
        [InlineKeyboardButton("⚪ Default", callback_data="adm_bn_color_default")],
    ]
    await update.message.reply_text(
        f"✅ Days: *{days}*\n\n🎨 Choose a button color for this plan:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return BUYNOW_ADD_PLAN_COLOR


async def _buynow_plan_color(update, context):
    """Receive plan color selection and save the complete plan."""
    query = update.callback_query
    await query.answer()

    # adm_bn_color_primary -> primary
    color_key = query.data.replace("adm_bn_color_", "")
    color_value = "" if color_key == "default" else color_key

    plan_data = context.user_data.get("buynow_new_plan", {})
    plan_data["color"] = color_value

    settings_db = _get_settings_db(context)
    plans_json = await _get_current_setting(context, "buynow_plans")
    try:
        plans = json.loads(plans_json) if plans_json else []
    except (json.JSONDecodeError, TypeError):
        plans = []

    plans.append(plan_data)
    await settings_db.set_setting("buynow_plans", json.dumps(plans))

    color_label = {"primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red"}.get(color_value, "⚪ Default")
    await query.edit_message_text(
        f"✅ *Plan Added!*\n\n"
        f"📝 Label: {plan_data.get('label', '')}\n"
        f"💰 Price: ₹{plan_data.get('price', 0)}\n"
        f"📅 Days: {plan_data.get('days', 0)}\n"
        f"🎨 Color: {color_label}",
        parse_mode="Markdown",
    )
    context.user_data.pop("buynow_new_plan", None)
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  CANCEL / FALLBACK
# ═══════════════════════════════════════════════

async def _cancel_admin(update, context):
    for key in ["add_product", "edit_product_id", "edit_field", "edit_trial_ids",
                 "remove_product_id", "broadcast_message", "welcome_ids", "manage_admin_action", "htu_video", "new_admin_id",
                 "btnclr_setting_key", "buynow_new_plan"]:
        context.user_data.pop(key, None)
    user_id = update.effective_user.id
    if is_main_bot(context):
        if user_id != OWNER_ID:
            main_db = get_main_db(context)
            is_admin = await main_db.is_hosting_admin(user_id)
            if not is_admin:
                return ConversationHandler.END
    else:
        if user_id not in get_owner_ids(context):
            return ConversationHandler.END
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  BUILD CONVERSATION HANDLER
# ═══════════════════════════════════════════════

def get_admin_conversation_handler() -> ConversationHandler:
    states = {
        ADMIN_MENU: [
            CallbackQueryHandler(_users_page_cb, pattern=r"^adm_vu_p_\d+$"),
            CallbackQueryHandler(_pending_page_cb, pattern=r"^adm_po_p_\d+$"),
            CallbackQueryHandler(_button_color_selected, pattern=r"^adm_btnclr_set_"),
            CallbackQueryHandler(admin_menu_handler, pattern=r"^adm_"),
        ],
        AP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, _ap_name)],
        AP_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _ap_price)],
        AP_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, _ap_desc)],
        AP_TRIAL: [MessageHandler(
            (filters.VIDEO | filters.PHOTO | filters.ANIMATION | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, _ap_trial,
        )],
        AP_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, _ap_channel)],
        EP_SELECT: [CallbackQueryHandler(_ep_select, pattern=r"^adm_")],
        EP_FIELD: [
            CallbackQueryHandler(_ep_color_select, pattern=r"^adm_ec_"),
            CallbackQueryHandler(_ep_field, pattern=r"^adm_"),
        ],
        EP_VALUE: [MessageHandler(
            (filters.VIDEO | filters.PHOTO | filters.ANIMATION | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, _ep_value,
        )],
        RP_SELECT: [CallbackQueryHandler(_rp_select, pattern=r"^adm_")],
        RP_CONFIRM: [CallbackQueryHandler(_rp_confirm, pattern=r"^adm_rc_")],
        SET_UPI_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_upi)],
        SET_QR_INPUT: [MessageHandler(
            (filters.PHOTO | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, _set_qr_image,
        )],
        SET_WELCOME_INPUT: [MessageHandler(
            (filters.VIDEO | filters.ANIMATION | filters.PHOTO | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, _set_welcome,
        )],
        SET_HTU_VIDEO: [MessageHandler((filters.VIDEO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND, _set_htu_video)],
        SET_HTU_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_htu_text)],
        SET_PROOF_LINK_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_proof_link)],
        SET_PROOF_PHOTO_INPUT: [MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, _set_proof_photo)],
        BC_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, _bc_message)],
        BC_CONFIRM: [CallbackQueryHandler(_bc_confirm, pattern=r"^adm_bc_")],
        MANAGE_ADMIN_MENU: [CallbackQueryHandler(_manage_admin_action, pattern=r"^adm_")],
        MANAGE_ADMIN_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _manage_admin_input)],
        MANAGE_ADMIN_DURATION: [CallbackQueryHandler(_manage_admin_duration, pattern=r"^adm_dur_")],
        WELCOME_TEXT_MENU: [CallbackQueryHandler(_welcome_text_menu_handler, pattern=r"^adm_wt_|^adm_back$")],
        SET_WELCOME_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_welcome_text_input)],
        SET_DISAPPEAR_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_disappear_time)],
        # ── Demo & Buy Now states ──
        SET_DEMO_LINK_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_demo_link)],
        BUYNOW_SETTINGS_MENU: [
            CallbackQueryHandler(_buynow_plans_handler, pattern=r"^adm_bn_add_plan$|^adm_bn_rm_\d+$|^adm_bn_back_settings$"),
            CallbackQueryHandler(_buynow_settings_handler, pattern=r"^adm_bn_|^adm_back$"),
        ],
        SET_BUYNOW_PRICE_LIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_buynow_price_list)],
        BUYNOW_ADD_PLAN_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, _buynow_plan_label)],
        BUYNOW_ADD_PLAN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _buynow_plan_price)],
        BUYNOW_ADD_PLAN_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, _buynow_plan_days)],
        BUYNOW_ADD_PLAN_COLOR: [CallbackQueryHandler(_buynow_plan_color, pattern=r"^adm_bn_color_")],
        SET_BUYNOW_CHANNEL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, _set_buynow_channel_link)],
        MASS_ADD_NAMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, _mass_add_products)],
    }

    try:
        from handlers.hosting import get_hosting_states
        states.update(get_hosting_states())
    except ImportError:
        logger.warning("Hosting module not found")

    # ── SWIPE MODE states ──
    try:
        from handlers.swipe_mode import get_swipe_states
        states.update(get_swipe_states())
    except ImportError:
        logger.warning("Swipe mode module not found")

    # ── BUY NOW ACCESS states ──
    try:
        from handlers.buynow_mode import get_buynow_states
        states.update(get_buynow_states())
    except ImportError:
        logger.warning("Buy Now mode module not found")

    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_command)],
        states=states,
        fallbacks=[
            CommandHandler("cancel", _cancel_admin),
            CommandHandler("admin", admin_command),
            CallbackQueryHandler(_show_admin_menu, pattern=r"^adm_back$"),
        ],
        per_message=False,
        allow_reentry=True,
    )
