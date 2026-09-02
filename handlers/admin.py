"""
Host Bot — Admin Panel Handler
TWO modes:
  Main Bot (owner): Full panel + product management + hosting management
  Main Bot (hosting admin): Only hosting flow (host/delete bot)
  Hosted Bot (admin): Full customization (products, settings, UPI, QR, welcome, proof, etc.)
                      Initially mirrored from main — admin can override everything
"""
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

        keyboard = [
            [InlineKeyboardButton("🎁 Edit Product", callback_data="adm_edit")],
            [InlineKeyboardButton("➕ Add Product", callback_data="adm_add")],
            [InlineKeyboardButton("🗑 Remove Product", callback_data="adm_remove")],
            [InlineKeyboardButton(f"🔘 Toggle Proof Button ({proof_status})", callback_data="adm_toggle_proof")],
            [InlineKeyboardButton(f"🔒 Forward Protection ({fwd_status})", callback_data="adm_toggle_fwd")],
            [InlineKeyboardButton(f"⏱ Auto Disappear 30min ({disappear_status})", callback_data="adm_toggle_disappear")],
            [InlineKeyboardButton("📄 Set Proof Link", callback_data="adm_proof_link")],
            [InlineKeyboardButton("🖼 Set Proof Photo", callback_data="adm_proof_photo")],
            [InlineKeyboardButton("🎨 Set Static Button Colors", callback_data="adm_colors")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
            [InlineKeyboardButton("💳 Set UPI", callback_data="adm_upi")],
            [InlineKeyboardButton("💳 Set QR Image", callback_data="adm_qr")],
            [InlineKeyboardButton("🎬 Set Welcome Media", callback_data="adm_welcome")],
            [InlineKeyboardButton("❓ Set How to Use", callback_data="adm_htu")],
            [InlineKeyboardButton("👥 View Users", callback_data="adm_users")],
            [InlineKeyboardButton("📊 Stats", callback_data="adm_stats")],
            [InlineKeyboardButton("👑 Manage Admins", callback_data="adm_manage_admins")],
            [InlineKeyboardButton("🤖 Bot Hosting", callback_data="adm_hosting")],
        ]
        text = "👑 *Main Bot Admin Panel*\n\nSelect an option below:"

    elif not is_main_bot(context):
        # ── HOSTED BOT ADMIN — Full customization (products + settings) ──
        db = get_db(context)
        proof_enabled = await db.get_setting("proof_button_enabled")
        if proof_enabled is None:
            proof_enabled = await main_db.get_setting("proof_button_enabled")
        proof_status = "ON ✅" if proof_enabled == "1" else "OFF ❌"

        # Check if hosted bot has own products or using main mirror
        product_count = await db.get_product_count()
        products_source = f"Custom ({product_count})" if product_count > 0 else "Mirrored from Main"

        keyboard = [
            [InlineKeyboardButton("🎁 Edit Product", callback_data="adm_edit")],
            [InlineKeyboardButton("➕ Add Product", callback_data="adm_add")],
            [InlineKeyboardButton("🗑 Remove Product", callback_data="adm_remove")],
            [InlineKeyboardButton("🔄 Reset Products to Main", callback_data="adm_reset_products")],
            [InlineKeyboardButton(f"🔘 Toggle Proof Button ({proof_status})", callback_data="adm_toggle_proof")],
            [InlineKeyboardButton("📄 Set Proof Link", callback_data="adm_proof_link")],
            [InlineKeyboardButton("🖼 Set Proof Photo", callback_data="adm_proof_photo")],
            [InlineKeyboardButton("💳 Set UPI", callback_data="adm_upi")],
            [InlineKeyboardButton("💳 Set QR Image", callback_data="adm_qr")],
            [InlineKeyboardButton("🎬 Set Welcome Media", callback_data="adm_welcome")],
            [InlineKeyboardButton("❓ Set How to Use", callback_data="adm_htu")],
            [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
            [InlineKeyboardButton("👥 View Users", callback_data="adm_users")],
            [InlineKeyboardButton("📊 Stats", callback_data="adm_stats")],
        ]
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
    elif data == "adm_remove":
        return await _show_remove_products(update, context)
    elif data == "adm_reset_products":
        return await _reset_products(update, context)
    elif data == "adm_toggle_proof":
        return await _toggle_setting(update, context, "proof_button_enabled", "Proof Button")
    elif data == "adm_toggle_fwd":
        return await _toggle_setting(update, context, "forward_protection", "Forward Protection", main_only=True)
    elif data == "adm_toggle_disappear":
        return await _toggle_setting(update, context, "auto_disappear", "Auto Disappear 30min", main_only=True)
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
    """Reset hosted bot's custom products — go back to mirroring main bot."""
    if is_main_bot(context):
        await update.callback_query.answer("❌ Cannot reset on main bot.", show_alert=True)
        return await _show_admin_menu(update, context)

    db = get_db(context)
    product_count = await db.get_product_count()
    if product_count == 0:
        await update.callback_query.answer("ℹ️ Already using mirrored products from main bot.", show_alert=True)
        return await _show_admin_menu(update, context)

    # Delete all per-bot products
    t = db._t
    await db.execute(f"DELETE FROM {t('products')}")
    await update.callback_query.answer(f"✅ {product_count} custom products removed!\nNow using main bot's mirrored products.", show_alert=True)
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


async def _ep_select(update, context):
    main_db = _get_products_write_db(context)
    query = update.callback_query
    await query.answer()
    if query.data == "adm_back":
        return await _show_admin_menu(update, context)
    product_id = int(query.data.split("_")[2])
    product = await main_db.get_product(product_id)
    if not product:
        await query.answer("❌ Product not found.", show_alert=True)
        return await _show_edit_products(update, context)
    context.user_data["edit_product_id"] = product_id
    trial_count = len(product.get("trial_file_ids_list", []))
    status = "Active ✅" if product.get("is_active") else "Inactive ❌"
    text = (
        f"🎁 *Editing: {product['name']}*\n\n"
        f"💰 Price: {format_price(product['price'])}\n📝 Description: {product.get('description') or 'None'}\n"
        f"🎬 Trial: {trial_count}\n🔗 Channel: {product.get('channel_link') or 'Not set'}\n📊 Status: {status}\n\nSelect field:"
    )
    keyboard = [
        [InlineKeyboardButton("📝 Name", callback_data="adm_ef_name")],
        [InlineKeyboardButton("💰 Price", callback_data="adm_ef_price")],
        [InlineKeyboardButton("📄 Description", callback_data="adm_ef_description")],
        [InlineKeyboardButton("🎬 Trial Media", callback_data="adm_ef_trial")],
        [InlineKeyboardButton("🔗 Channel Link", callback_data="adm_ef_channel")],
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
        query.data = f"adm_ep_{product_id}"
        return await _ep_select(update, context)
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
        "🎨 *Static Button Colors*\n\n"
        "🔵 Blue — Standard actions\n🟢 Green — Add/Create\n🔴 Red — Remove/Delete\n🟡 Yellow — Toggle\n\n"
        "_Telegram InlineKeyboard doesn't support custom colors._\n_Colors indicated using emojis._"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_MENU


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
            await query.edit_message_text("📢 *Broadcasting...*\n\n⏳ Please wait...", parse_mode="Markdown")
            success, failed = await broadcast_message(context, from_message=msg)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"📢 *Broadcast Complete!*\n\n✅ Sent: {success}\n❌ Failed: {failed}\n📊 Total: {success + failed}",
                parse_mode="Markdown",
            )
    context.user_data.pop("broadcast_message", None)
    return await _show_admin_menu(update, context)


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
#  CANCEL / FALLBACK
# ═══════════════════════════════════════════════

async def _cancel_admin(update, context):
    for key in ["add_product", "edit_product_id", "edit_field", "edit_trial_ids",
                 "remove_product_id", "broadcast_message", "welcome_ids", "manage_admin_action", "htu_video", "new_admin_id"]:
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
        EP_FIELD: [CallbackQueryHandler(_ep_field, pattern=r"^adm_")],
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
    }

    try:
        from handlers.hosting import get_hosting_states
        states.update(get_hosting_states())
    except ImportError:
        logger.warning("Hosting module not found")

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
