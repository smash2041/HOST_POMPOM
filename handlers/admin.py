"""
VIP Access Bot — Admin Panel Handler
Full admin panel with ConversationHandler for multi-step flows.
13 admin features accessible only to bot owners.
Owner list is NEVER displayed — neither owner knows about the other.
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

from database import db
from config import OWNER_IDS
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


# ═══════════════════════════════════════════════
#  ADMIN MENU
# ═══════════════════════════════════════════════

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin command — show admin panel (owners only)."""
    if update.effective_user.id not in OWNER_IDS:
        return ConversationHandler.END
    return await _show_admin_menu(update, context)


async def _show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display the admin menu with all option buttons."""
    proof_enabled = await db.get_setting("proof_button_enabled")
    proof_status = "ON ✅" if proof_enabled == "1" else "OFF ❌"

    fwd_enabled = await db.get_setting("forward_protection")
    fwd_status = "ON ✅" if fwd_enabled == "1" else "OFF ❌"

    disappear_enabled = await db.get_setting("auto_disappear")
    disappear_status = "ON ✅" if disappear_enabled == "1" else "OFF ❌"

    keyboard = [
        [InlineKeyboardButton("🎁 Edit Product", callback_data="adm_edit")],
        [InlineKeyboardButton("➕ Add Product", callback_data="adm_add")],
        [InlineKeyboardButton("🗑 Remove Product", callback_data="adm_remove")],
        [InlineKeyboardButton(
            f"🔘 Toggle Proof Button ({proof_status})",
            callback_data="adm_toggle_proof",
        )],
        [InlineKeyboardButton(
            f"🔒 Forward Protection ({fwd_status})",
            callback_data="adm_toggle_fwd",
        )],
        [InlineKeyboardButton(
            f"⏱ Auto Disappear 30min ({disappear_status})",
            callback_data="adm_toggle_disappear",
        )],
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
    ]

    text = "🔧 *Admin Panel*\n\nSelect an option below:"
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        except Exception:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text, reply_markup=reply_markup, parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="Markdown"
        )
    return ADMIN_MENU


async def admin_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route admin menu button clicks to the correct sub-handler."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "adm_edit":
        return await _show_edit_products(update, context)
    elif data == "adm_add":
        context.user_data["add_product"] = {}
        await query.edit_message_text(
            "➕ *Add New Product*\n\n📝 Enter the product name:",
            parse_mode="Markdown",
        )
        return AP_NAME
    elif data == "adm_remove":
        return await _show_remove_products(update, context)
    elif data == "adm_toggle_proof":
        return await _toggle_proof(update, context)
    elif data == "adm_toggle_fwd":
        return await _toggle_forward_protection(update, context)
    elif data == "adm_toggle_disappear":
        return await _toggle_auto_disappear(update, context)
    elif data == "adm_proof_link":
        current = await db.get_setting("proof_link") or "Not set"
        await query.edit_message_text(
            f"📄 *Set Proof Link*\n\nCurrent: {current}\n\n"
            f"🔗 Send the new proof link/URL:\n\n"
            f"_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_PROOF_LINK_INPUT
    elif data == "adm_proof_photo":
        await query.edit_message_text(
            "🖼 *Set Proof Photo*\n\n"
            "📸 Send the proof photo:\n\n"
            "_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_PROOF_PHOTO_INPUT
    elif data == "adm_colors":
        return await _show_colors_info(update, context)
    elif data == "adm_broadcast":
        await query.edit_message_text(
            "📢 *Broadcast Message*\n\n"
            "Send the message you want to broadcast to all users.\n"
            "You can send text, photo, video, or any media.\n\n"
            "_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return BC_MESSAGE
    elif data == "adm_upi":
        from config import UPI_ID as default_upi
        current = await db.get_setting("upi_id") or default_upi or "Not set"
        await query.edit_message_text(
            f"💳 *Set UPI ID*\n\nCurrent: `{current}`\n\n"
            f"Send the new UPI ID:\n\n"
            f"_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_UPI_INPUT
    elif data == "adm_qr":
        await query.edit_message_text(
            "💳 *Set Custom QR Image*\n\n"
            "📸 Send the QR code image.\n"
            "This will override auto-generated QR codes.\n\n"
            "To remove custom QR and use auto-generated, type `remove`.\n\n"
            "_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_QR_INPUT
    elif data == "adm_welcome":
        context.user_data["welcome_ids"] = []
        await query.edit_message_text(
            "🎬 *Set Welcome Media*\n\n"
            "Send welcome videos/photos one by one.\n"
            "Bot will send them as an Album (Media Group).\n\n"
            "Type *done* when finished, or *clear* to remove all.",
            parse_mode="Markdown",
        )
        return SET_WELCOME_INPUT
    elif data == "adm_htu":
        await query.edit_message_text(
            "❓ *Set 'How to Use'*\n\n"
            "🎬 Send the How to Use video:\n\n"
            "_Send /cancel to go back_",
            parse_mode="Markdown",
        )
        return SET_HTU_VIDEO
    elif data == "adm_users":
        return await _show_users_list(update, context, page=0)
    elif data == "adm_stats":
        return await _show_stats(update, context)
    elif data == "adm_pending":
        return await _show_pending_orders(update, context, page=0)
    elif data == "adm_back":
        return await _show_admin_menu(update, context)

    return ADMIN_MENU


# ═══════════════════════════════════════════════
#  ADD PRODUCT FLOW (5 steps)
# ═══════════════════════════════════════════════

async def _ap_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1/5: Get product name."""
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Name cannot be empty. Try again:")
        return AP_NAME

    context.user_data["add_product"] = {"name": name}
    await update.message.reply_text(
        f"✅ Name: *{name}*\n\n💰 Enter the price (in ₹):",
        parse_mode="Markdown",
    )
    return AP_PRICE


async def _ap_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2/5: Get product price."""
    try:
        price = float(update.message.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid positive number:")
        return AP_PRICE

    context.user_data["add_product"]["price"] = price
    await update.message.reply_text(
        f"✅ Price: *{format_price(price)}*\n\n"
        f"📝 Enter product description (or type `skip`):",
        parse_mode="Markdown",
    )
    return AP_DESC


async def _ap_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 3/5: Get product description."""
    text = update.message.text.strip()
    desc = "" if text.lower() == "skip" else text

    context.user_data["add_product"]["description"] = desc
    context.user_data["add_product"]["trial_ids"] = []

    await update.message.reply_text(
        "🎬 *Trial Media*\n\n"
        "Send trial videos or photos one by one.\n"
        "Bot will save their file\\_id.\n\n"
        "Type *done* when finished, or *skip* to add none.",
        parse_mode="Markdown",
    )
    return AP_TRIAL


async def _ap_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 4/5: Collect trial video file_ids one by one."""
    if update.message.text:
        text = update.message.text.strip().lower()
        if text in ("done", "skip"):
            trial_ids = context.user_data["add_product"].get("trial_ids", [])
            count = len(trial_ids)
            await update.message.reply_text(
                f"✅ {count} trial video(s) saved.\n\n"
                f"🔗 Send the *channel invite link* for this product:\n\n"
                f"_Example: https://t.me/+xxxxx_",
                parse_mode="Markdown",
            )
            return AP_CHANNEL
        else:
            await update.message.reply_text(
                "❌ Send a video/photo file, or type *done* / *skip*.",
                parse_mode="Markdown",
            )
            return AP_TRIAL

    # Handle video / photo / animation / document
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
        await update.message.reply_text(
            f"✅ Media #{count} added!\n"
            f"Send more or type *done* to finish.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ Send a video/photo file, or type *done* / *skip*.",
            parse_mode="Markdown",
        )
    return AP_TRIAL


async def _ap_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 5/5: Get channel invite link and create the product."""
    channel_link = update.message.text.strip()
    data = context.user_data.get("add_product", {})
    trial_ids = json.dumps(data.get("trial_ids", []))

    product_id = await db.add_product(
        name=data["name"],
        price=data["price"],
        description=data.get("description", ""),
        trial_file_ids=trial_ids,
        channel_link=channel_link,
    )

    context.user_data.pop("add_product", None)

    await update.message.reply_text(
        f"✅ *Product Created!*\n\n"
        f"📦 Name: {data['name']}\n"
        f"💰 Price: {format_price(data['price'])}\n"
        f"📝 Description: {data.get('description') or 'None'}\n"
        f"🎬 Trial Videos: {len(data.get('trial_ids', []))}\n"
        f"🔗 Channel: {channel_link}\n"
        f"🆔 Product ID: #{product_id}",
        parse_mode="Markdown",
    )
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  EDIT PRODUCT FLOW
# ═══════════════════════════════════════════════

async def _show_edit_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show product list for editing."""
    query = update.callback_query
    products = await db.get_all_products(active_only=False)

    if not products:
        await query.edit_message_text(
            "❌ No products found.\n\nUse 'Add Product' first.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]
            ),
        )
        return ADMIN_MENU

    keyboard = []
    for p in products:
        status = "✅" if p.get("is_active") else "❌"
        keyboard.append(
            [InlineKeyboardButton(
                f"{status} {p['name']} — {format_price(p['price'])}",
                callback_data=f"adm_ep_{p['id']}",
            )]
        )
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])

    await query.edit_message_text(
        "🎁 *Edit Product*\n\nSelect a product to edit:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return EP_SELECT


async def _ep_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product selection for editing."""
    query = update.callback_query
    await query.answer()

    if query.data == "adm_back":
        return await _show_admin_menu(update, context)

    product_id = int(query.data.split("_")[2])
    product = await db.get_product(product_id)

    if not product:
        await query.answer("❌ Product not found.", show_alert=True)
        return await _show_edit_products(update, context)

    context.user_data["edit_product_id"] = product_id

    trial_count = len(product.get("trial_file_ids_list", []))
    status = "Active ✅" if product.get("is_active") else "Inactive ❌"

    text = (
        f"🎁 *Editing: {product['name']}*\n\n"
        f"💰 Price: {format_price(product['price'])}\n"
        f"📝 Description: {product.get('description') or 'None'}\n"
        f"🎬 Trial Media: {trial_count}\n"
        f"🔗 Channel: {product.get('channel_link') or 'Not set'}\n"
        f"📊 Status: {status}\n\n"
        f"Select field to edit:"
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

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return EP_FIELD


async def _ep_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle field selection for editing."""
    query = update.callback_query
    await query.answer()

    if query.data == "adm_back":
        return await _show_admin_menu(update, context)

    field = query.data.split("_")[2]
    product_id = context.user_data.get("edit_product_id")

    # Toggle active/inactive immediately (no value input needed)
    if field == "toggle":
        product = await db.get_product(product_id)
        new_status = 0 if product.get("is_active") else 1
        await db.update_product(product_id, is_active=new_status)
        label = "Active ✅" if new_status else "Inactive ❌"
        await query.answer(f"Product is now {label}", show_alert=True)
        # Re-show edit view for same product
        query.data = f"adm_ep_{product_id}"
        return await _ep_select(update, context)

    context.user_data["edit_field"] = field

    if field == "trial":
        context.user_data["edit_trial_ids"] = []
        await query.edit_message_text(
            "🎬 *Edit Trial Media*\n\n"
            "Send new trial videos/photos one by one.\n"
            "These will *replace* all existing trial media.\n\n"
            "Type *done* when finished.",
            parse_mode="Markdown",
        )
        return EP_VALUE

    prompts = {
        "name": "📝 Enter new product name:",
        "price": "💰 Enter new price (in ₹):",
        "description": '📄 Enter new description (or type `clear` to remove):',
        "channel": "🔗 Enter new channel invite link:",
    }

    await query.edit_message_text(
        prompts.get(field, f"Enter new value for {field}:")
    )
    return EP_VALUE


async def _ep_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new value entry for product editing."""
    product_id = context.user_data.get("edit_product_id")
    field = context.user_data.get("edit_field")

    # ── Trial video collection ──
    if field == "trial":
        if update.message.text and update.message.text.strip().lower() == "done":
            trial_ids = context.user_data.get("edit_trial_ids", [])
            await db.update_product(
                product_id, trial_file_ids=json.dumps(trial_ids)
            )
            await update.message.reply_text(
                f"✅ Trial media updated! ({len(trial_ids)} items)"
            )
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
            await update.message.reply_text(
                f"✅ Media #{count} added! Send more or type *done*.",
                parse_mode="Markdown",
            )
            return EP_VALUE
        else:
            await update.message.reply_text(
                "❌ Send a video/photo or type *done*.", parse_mode="Markdown"
            )
            return EP_VALUE

    # ── Text-based field updates ──
    value = update.message.text.strip()

    if field == "price":
        try:
            value = float(value)
            if value <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Enter a valid positive number:")
            return EP_VALUE
        await db.update_product(product_id, price=value)

    elif field == "name":
        if not value:
            await update.message.reply_text("❌ Name cannot be empty:")
            return EP_VALUE
        await db.update_product(product_id, name=value)

    elif field == "description":
        if value.lower() == "clear":
            value = ""
        await db.update_product(product_id, description=value)

    elif field == "channel":
        await db.update_product(product_id, channel_link=value)

    await update.message.reply_text("✅ Product updated successfully!")
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  REMOVE PRODUCT FLOW
# ═══════════════════════════════════════════════

async def _show_remove_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show product list for removal."""
    query = update.callback_query
    products = await db.get_all_products(active_only=False)

    if not products:
        await query.edit_message_text(
            "❌ No products to remove.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]
            ),
        )
        return ADMIN_MENU

    keyboard = []
    for p in products:
        keyboard.append(
            [InlineKeyboardButton(
                f"🗑 {p['name']} — {format_price(p['price'])}",
                callback_data=f"adm_rp_{p['id']}",
            )]
        )
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])

    await query.edit_message_text(
        "🗑 *Remove Product*\n\nSelect a product to remove:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return RP_SELECT


async def _rp_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product selection for removal — ask confirmation."""
    query = update.callback_query
    await query.answer()

    if query.data == "adm_back":
        return await _show_admin_menu(update, context)

    product_id = int(query.data.split("_")[2])
    product = await db.get_product(product_id)

    if not product:
        await query.answer("❌ Product not found.", show_alert=True)
        return await _show_admin_menu(update, context)

    context.user_data["remove_product_id"] = product_id

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Remove", callback_data="adm_rc_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="adm_rc_no"),
        ]
    ]

    await query.edit_message_text(
        f"⚠️ *Confirm Removal*\n\n"
        f"Are you sure you want to remove:\n"
        f"📦 *{product['name']}* — {format_price(product['price'])}?\n\n"
        f"⚠️ This action cannot be undone!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return RP_CONFIRM


async def _rp_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle removal confirmation (Yes/No)."""
    query = update.callback_query
    await query.answer()

    if query.data == "adm_rc_yes":
        product_id = context.user_data.get("remove_product_id")
        if product_id:
            product = await db.get_product(product_id)
            await db.delete_product(product_id)
            name = product["name"] if product else "Unknown"
            await query.answer(f"✅ '{name}' removed!", show_alert=True)

    context.user_data.pop("remove_product_id", None)
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  TOGGLE PROOF BUTTON
# ═══════════════════════════════════════════════

async def _toggle_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle the proof button ON/OFF in the /start menu."""
    current = await db.get_setting("proof_button_enabled")
    new_value = "0" if current == "1" else "1"
    await db.set_setting("proof_button_enabled", new_value)

    status = "ON ✅" if new_value == "1" else "OFF ❌"
    await update.callback_query.answer(
        f"Proof button is now {status}", show_alert=True
    )
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  TOGGLE FORWARD PROTECTION
# ═══════════════════════════════════════════════

async def _toggle_forward_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle forward protection ON/OFF for trial & welcome media."""
    current = await db.get_setting("forward_protection")
    new_value = "0" if current == "1" else "1"
    await db.set_setting("forward_protection", new_value)

    status = "ON ✅" if new_value == "1" else "OFF ❌"
    await update.callback_query.answer(
        f"Forward Protection is now {status}", show_alert=True
    )
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  TOGGLE AUTO DISAPPEAR
# ═══════════════════════════════════════════════

async def _toggle_auto_disappear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle auto disappear ON/OFF — trial media deletes after 30 minutes."""
    current = await db.get_setting("auto_disappear")
    new_value = "0" if current == "1" else "1"
    await db.set_setting("auto_disappear", new_value)

    status = "ON ✅" if new_value == "1" else "OFF ❌"
    await update.callback_query.answer(
        f"Auto Disappear (30 min) is now {status}", show_alert=True
    )
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET PROOF LINK
# ═══════════════════════════════════════════════

async def _set_proof_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the proof link URL."""
    link = update.message.text.strip()
    await db.set_setting("proof_link", link)
    await update.message.reply_text(f"✅ Proof link updated to:\n{link}")
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET PROOF PHOTO
# ═══════════════════════════════════════════════

async def _set_proof_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the proof photo file_id."""
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await db.set_setting("proof_photo_id", file_id)
        await update.message.reply_text("✅ Proof photo updated!")
    else:
        await update.message.reply_text("❌ Please send a photo.")
        return SET_PROOF_PHOTO_INPUT
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET STATIC BUTTON COLORS (Visual Reference Only)
# ═══════════════════════════════════════════════

async def _show_colors_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show button color info (admin panel visual reference only)."""
    query = update.callback_query

    text = (
        "🎨 *Static Button Colors*\n\n"
        "Visual color assignments for admin buttons:\n\n"
        "🔵 Blue — Standard actions (Edit, Set, View)\n"
        "🟢 Green — Add / Create actions\n"
        "🔴 Red — Remove / Delete actions\n"
        "🟡 Yellow — Toggle actions\n\n"
        "_Note: Telegram InlineKeyboard doesn't support custom colors natively._\n"
        "_Colors are indicated using emojis in button text._"
    )

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_back")]]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return ADMIN_MENU


# ═══════════════════════════════════════════════
#  BROADCAST
# ═══════════════════════════════════════════════

async def _bc_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the broadcast message content."""
    context.user_data["broadcast_message"] = update.message
    user_count = await db.get_user_count()

    keyboard = [
        [
            InlineKeyboardButton("✅ Send to All", callback_data="adm_bc_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="adm_bc_no"),
        ]
    ]

    await update.message.reply_text(
        f"📢 *Broadcast Confirmation*\n\n"
        f"This message will be sent to *{user_count}* users.\n\n"
        f"Are you sure?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return BC_CONFIRM


async def _bc_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast confirmation — send to all users or cancel."""
    query = update.callback_query
    await query.answer()

    if query.data == "adm_bc_yes":
        msg = context.user_data.get("broadcast_message")
        if msg:
            await query.edit_message_text(
                "📢 *Broadcasting...*\n\n⏳ Please wait...",
                parse_mode="Markdown",
            )

            success, failed = await broadcast_message(context, from_message=msg)

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"📢 *Broadcast Complete!*\n\n"
                    f"✅ Sent: {success}\n"
                    f"❌ Failed: {failed}\n"
                    f"📊 Total: {success + failed}"
                ),
                parse_mode="Markdown",
            )

    context.user_data.pop("broadcast_message", None)
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET UPI
# ═══════════════════════════════════════════════

async def _set_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the UPI ID."""
    upi = update.message.text.strip()
    if not upi or "@" not in upi:
        await update.message.reply_text(
            "❌ Enter a valid UPI ID (e.g., name@upi):"
        )
        return SET_UPI_INPUT

    await db.set_setting("upi_id", upi)
    await update.message.reply_text(
        f"✅ UPI ID updated to: `{upi}`", parse_mode="Markdown"
    )
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET QR IMAGE (Custom Override)
# ═══════════════════════════════════════════════

async def _set_qr_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save custom QR image or remove it to use auto-generated."""
    if update.message.text and update.message.text.strip().lower() == "remove":
        await db.set_setting("qr_image_id", "")
        await update.message.reply_text(
            "✅ Custom QR removed. Auto-generated QR will be used."
        )
        return await _show_admin_menu(update, context)

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if file_id:
        await db.set_setting("qr_image_id", file_id)
        await update.message.reply_text("✅ Custom QR image updated!")
    else:
        await update.message.reply_text(
            "❌ Send a photo or type `remove`.", parse_mode="Markdown"
        )
        return SET_QR_INPUT

    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  SET WELCOME MEDIA (Multiple / Album)
# ═══════════════════════════════════════════════

async def _set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect welcome media one by one until 'done' is typed."""
    if update.message.text:
        text = update.message.text.strip().lower()
        if text == "clear":
            await db.set_setting("welcome_media_ids", "[]")
            await update.message.reply_text("✅ Welcome media cleared!")
            return await _show_admin_menu(update, context)
        elif text == "done":
            ids = context.user_data.get("welcome_ids", [])
            await db.set_setting("welcome_media_ids", json.dumps(ids))
            await update.message.reply_text(f"✅ Welcome media updated! ({len(ids)} items)")
            return await _show_admin_menu(update, context)
        else:
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
#  SET HOW TO USE
# ═══════════════════════════════════════════════

async def _set_htu_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def _set_htu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("❌ Text cannot be empty.")
        return SET_HTU_TEXT
        
    await db.set_setting("htu_video_id", context.user_data["htu_video"])
    await db.set_setting("htu_text", text)
    await update.message.reply_text("✅ How to Use updated successfully!")
    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  VIEW USERS (Paginated)
# ═══════════════════════════════════════════════

async def _show_users_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
):
    """Show paginated user list (10 per page)."""
    query = update.callback_query

    users = await db.get_all_users()
    # Hide owners from the user list so they remain invisible to each other
    users = [u for u in users if u.get("user_id") not in OWNER_IDS]
    
    total = len(users)
    per_page = 10
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]
    total_pages = max(1, (total + per_page - 1) // per_page)

    if not users:
        text = "👥 *No users yet.*"
    else:
        text = f"👥 *Users* (Page {page + 1}/{total_pages})\n"
        text += f"📊 Total: {total}\n\n"

        for i, user in enumerate(page_users, start=start + 1):
            username = f"@{escape_md(user['username'])}" if user.get("username") else "N/A"
            text += (
                f"*{i}.* {escape_md(user.get('first_name', 'Unknown'))} "
                f"({username})\n"
                f"    🆔 `{user['user_id']}` | 📅 {escape_md(user.get('joined_at', 'N/A'))}\n"
            )

    keyboard = []
    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_vu_p_{page - 1}")
        )
    if end < total:
        nav_row.append(
            InlineKeyboardButton("➡️ Next", callback_data=f"adm_vu_p_{page + 1}")
        )
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])

    try:
        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    except Exception:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text, reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
    return ADMIN_MENU


async def _users_page_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user list pagination buttons."""
    query = update.callback_query
    await query.answer()

    if query.data == "adm_back":
        return await _show_admin_menu(update, context)

    page = int(query.data.split("_")[3])
    return await _show_users_list(update, context, page=page)


# ═══════════════════════════════════════════════
#  STATS
# ═══════════════════════════════════════════════

async def _show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show aggregated bot statistics."""
    query = update.callback_query
    stats = await db.get_stats()

    products = await db.get_all_products(active_only=False)
    active_count = sum(1 for p in products if p.get("is_active"))

    text = (
        "📊 *Bot Statistics*\n\n"
        f"👥 Total Users: *{stats['total_users']}*\n\n"
        f"📦 Total Orders: *{stats['total_orders']}*\n"
        f"✅ Approved: *{stats['approved_orders']}*\n"
        f"⏳ Pending: *{stats['pending_orders']}*\n"
        f"❌ Rejected: *{stats['rejected_orders']}*\n\n"
        f"💰 Total Revenue: *{format_price(stats['total_revenue'])}*\n\n"
        f"📦 Total Products: *{len(products)}*\n"
        f"✅ Active: *{active_count}* | ❌ Inactive: *{len(products) - active_count}*"
    )

    keyboard = []
    if stats['pending_orders'] > 0:
        keyboard.append([InlineKeyboardButton(
            f"📋 View Pending Orders ({stats['pending_orders']})",
            callback_data="adm_pending",
        )])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="adm_back")])
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return ADMIN_MENU


async def _show_pending_orders(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
):
    """
    Show pending orders individually with screenshots/proof,
    matching the real-time payment alert format.
    Each order is sent as a separate message with Approve/Reject buttons.
    """
    query = update.callback_query
    chat_id = query.message.chat_id
    orders = await db.get_pending_orders()

    if not orders:
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="adm_stats")]]
        await query.edit_message_text(
            "📋 <b>Pending Orders</b>\n\n✅ No pending orders!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        return ADMIN_MENU

    per_page = 5
    total_pages = (len(orders) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    page_orders = orders[page * per_page : (page + 1) * per_page]

    # Delete the stats/menu message to keep chat clean
    try:
        await query.message.delete()
    except Exception:
        pass

    # Send each pending order as individual message (like real-time alert)
    for order in page_orders:
        product = await db.get_product(order["product_id"])
        user_info = await db.get_user(order["user_id"])

        safe_product = html.escape(product["name"]) if product else "Unknown"
        safe_price = html.escape(format_price(product["price"])) if product else "?"
        safe_name = html.escape(user_info["first_name"] or "Unknown") if user_info else "Unknown"
        safe_username = html.escape(user_info["username"]) if user_info and user_info.get("username") else ""
        proof_type = order.get("proof_type") or "none"
        created = order.get("created_at", "?")[:16]

        order_text = (
            f"🔔 <b>Pending Payment — Order #{order['id']}</b>\n\n"
            f"👤 User: {safe_name}"
            f"{f' (@{safe_username})' if safe_username else ''}\n"
            f"🆔 User ID: <code>{order['user_id']}</code>\n"
            f"📦 Plan: <b>{safe_product}</b>\n"
            f"💰 Price: <b>{safe_price}</b>\n"
            f"📋 Order: #{order['id']}\n"
            f"📝 Proof: {proof_type.upper()}\n"
            f"🕐 {created}\n"
        )

        # Add UTR/Transaction ID if available
        utr = order.get("utr_text", "")
        if utr:
            order_text += f"\n💬 UTR/Transaction ID:\n<code>{html.escape(utr)}</code>"

        keyboard = [[
            InlineKeyboardButton(
                "✅ Approve", callback_data=f"approve_{order['id']}"
            ),
            InlineKeyboardButton(
                "❌ Reject", callback_data=f"reject_{order['id']}"
            ),
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            if proof_type == "screenshot" and order.get("payment_proof"):
                # Send with screenshot, same as real-time alert
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=order["payment_proof"],
                    caption=order_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=order_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.warning(f"Failed to send pending order #{order['id']}: {e}")

    # Send navigation message at the end
    nav_text = f"📋 <b>Pending Orders</b> — Page {page + 1}/{total_pages} ({len(orders)} total)\n"
    nav_keyboard = []

    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_po_p_{page - 1}")
        )
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"adm_po_p_{page + 1}")
        )
    if nav_buttons:
        nav_keyboard.append(nav_buttons)

    nav_keyboard.append([InlineKeyboardButton("🔙 Back to Stats", callback_data="adm_stats")])
    nav_keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="adm_pending")])

    await context.bot.send_message(
        chat_id=chat_id,
        text=nav_text,
        reply_markup=InlineKeyboardMarkup(nav_keyboard),
        parse_mode="HTML",
    )
    return ADMIN_MENU


async def _pending_page_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pending orders pagination."""
    query = update.callback_query
    await query.answer()
    # Delete the nav message before showing new page
    try:
        await query.message.delete()
    except Exception:
        pass
    page = int(query.data.split("_")[-1])
    return await _show_pending_orders(update, context, page=page)


# ═══════════════════════════════════════════════
#  CANCEL / FALLBACK
# ═══════════════════════════════════════════════

async def _cancel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current admin operation and return to menu."""
    # Clear all temporary admin data
    for key in [
        "add_product", "edit_product_id", "edit_field", "edit_trial_ids",
        "remove_product_id", "broadcast_message", "welcome_media_type",
    ]:
        context.user_data.pop(key, None)

    if update.effective_user.id not in OWNER_IDS:
        return ConversationHandler.END

    return await _show_admin_menu(update, context)


# ═══════════════════════════════════════════════
#  BUILD THE CONVERSATION HANDLER
# ═══════════════════════════════════════════════

def get_admin_conversation_handler() -> ConversationHandler:
    """
    Build and return the admin ConversationHandler.
    Handles all 13 admin features with proper state routing.
    """
    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_command)],
        states={
            # ── Main menu ──
            ADMIN_MENU: [
                CallbackQueryHandler(_users_page_cb, pattern=r"^adm_vu_p_\d+$"),
                CallbackQueryHandler(_pending_page_cb, pattern=r"^adm_po_p_\d+$"),
                CallbackQueryHandler(admin_menu_handler, pattern=r"^adm_"),
            ],
            # ── Add Product (5 steps) ──
            AP_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _ap_name)
            ],
            AP_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _ap_price)
            ],
            AP_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _ap_desc)
            ],
            AP_TRIAL: [
                MessageHandler(
                    (filters.VIDEO | filters.PHOTO | filters.ANIMATION | filters.Document.ALL
                     | filters.TEXT) & ~filters.COMMAND,
                    _ap_trial,
                )
            ],
            AP_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _ap_channel)
            ],
            # ── Edit Product ──
            EP_SELECT: [
                CallbackQueryHandler(_ep_select, pattern=r"^adm_")
            ],
            EP_FIELD: [
                CallbackQueryHandler(_ep_field, pattern=r"^adm_")
            ],
            EP_VALUE: [
                MessageHandler(
                    (filters.VIDEO | filters.PHOTO | filters.ANIMATION | filters.Document.ALL
                     | filters.TEXT) & ~filters.COMMAND,
                    _ep_value,
                )
            ],
            # ── Remove Product ──
            RP_SELECT: [
                CallbackQueryHandler(_rp_select, pattern=r"^adm_")
            ],
            RP_CONFIRM: [
                CallbackQueryHandler(_rp_confirm, pattern=r"^adm_rc_")
            ],
            # ── Settings ──
            SET_UPI_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _set_upi)
            ],
            SET_QR_INPUT: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL | filters.TEXT)
                    & ~filters.COMMAND,
                    _set_qr_image,
                )
            ],
            SET_WELCOME_INPUT: [
                MessageHandler(
                    (filters.VIDEO | filters.ANIMATION | filters.PHOTO
                     | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND,
                    _set_welcome,
                )
            ],
            SET_HTU_VIDEO: [
                MessageHandler(
                    (filters.VIDEO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND,
                    _set_htu_video,
                )
            ],
            SET_HTU_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _set_htu_text)
            ],
            SET_PROOF_LINK_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _set_proof_link)
            ],
            SET_PROOF_PHOTO_INPUT: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND,
                    _set_proof_photo,
                )
            ],
            # ── Broadcast ──
            BC_MESSAGE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, _bc_message)
            ],
            BC_CONFIRM: [
                CallbackQueryHandler(_bc_confirm, pattern=r"^adm_bc_")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", _cancel_admin),
            CommandHandler("admin", admin_command),
            CallbackQueryHandler(
                lambda u, c: _show_admin_menu(u, c), pattern=r"^adm_back$"
            ),
        ],
        per_message=False,
        allow_reentry=True,
    )
