"""
VIP Access Bot — Payment Flow Handlers
Handles: Buy → QR display → I Have Paid → Proof collection → Owner approve/reject.
Dynamic UPI QR auto-generation OR custom QR image from admin panel.
"""
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.bot_helpers import get_db, get_owner_ids
from config import UPI_ID
from utils.qr_generator import generate_upi_qr
from utils.helpers import format_price

logger = logging.getLogger(__name__)


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle Buy Now button.
    Shows UPI QR (auto-generated or custom) + payment instructions.
    """
    db = get_db(context)
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split("_")[1])
    product = await db.get_product(product_id)

    if not product:
        await query.message.reply_text("❌ Product not found.")
        return

    # Store buying context for later
    context.user_data["buying_product_id"] = product_id

    # Get UPI settings
    upi_id = await db.get_setting("upi_id") or UPI_ID
    custom_qr = await db.get_setting("qr_image_id")

    product_name = html.escape(product['name'])
    price_str = html.escape(format_price(product['price']))
    upi_escaped = html.escape(upi_id)

    caption = (
        f"💳 <b>Payment for: {product_name}</b>\n\n"
        f"💰 Amount: <b>{price_str}</b>\n\n"
        f"📱 UPI ID: <code>{upi_escaped}</code>\n\n"
        f"📌 Scan the QR code or copy the UPI ID above to make payment.\n"
        f"After payment, click <b>I Have Paid</b> button below."
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ I Have Paid", callback_data=f"paid_{product_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel", callback_data="back_to_products"
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if custom_qr:
            # Use custom QR image uploaded by owner
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=custom_qr,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        else:
            # Auto-generate dynamic QR with plan price
            qr_buffer = generate_upi_qr(
                upi_id, product["price"], product["name"]
            )
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=qr_buffer,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Error sending payment QR: {e}")
        # Fallback: send text-only payment info
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=caption,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )


async def paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle 'I Have Paid' button.
    Creates a pending order and asks user for payment proof.
    """
    db = get_db(context)
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split("_")[1])
    product = await db.get_product(product_id)

    if not product:
        await query.message.reply_text("❌ Product not found.")
        return

    # Create pending order in DB
    order_id = await db.create_order(
        user_id=query.from_user.id, product_id=product_id
    )

    # Set awaiting-proof state in user_data
    context.user_data["awaiting_proof"] = True
    context.user_data["pending_order_id"] = order_id
    context.user_data["pending_product_id"] = product_id

    text = (
        "✅ <b>Great!</b>\n\n"
        "📸 Please send your payment screenshot, or\n"
        "📝 Type your UTR / Transaction ID\n\n"
        "We'll verify and activate your plan within 30 minutes.\n\n"
        "⏳ <i>Waiting for your proof...</i>"
    )

    await context.bot.send_message(
        chat_id=query.message.chat_id, text=text, parse_mode="HTML"
    )


async def handle_payment_proof(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """
    Handle payment proof (screenshot or UTR) from user.
    Called from the global message handler.

    Returns:
        True if this message was handled as payment proof, False otherwise.
    """
    if not context.user_data.get("awaiting_proof"):
        return False

    order_id = context.user_data.get("pending_order_id")
    if not order_id:
        context.user_data["awaiting_proof"] = False
        return False

    db = get_db(context)
    owner_ids = get_owner_ids(context)
    user = update.effective_user

    # Determine proof type and data
    utr_text = ""  # Capture UTR/Transaction ID from caption or text
    if update.message.photo:
        proof_data = update.message.photo[-1].file_id  # Highest resolution
        proof_type = "screenshot"
        # Capture caption as UTR if user typed it with the screenshot
        if update.message.caption:
            utr_text = update.message.caption.strip()
    elif update.message.document:
        proof_data = update.message.document.file_id
        proof_type = "screenshot"
        if update.message.caption:
            utr_text = update.message.caption.strip()
    elif update.message.text:
        proof_data = update.message.text.strip()
        proof_type = "utr"
        utr_text = proof_data  # Store UTR text in dedicated field too
        if not proof_data:
            await update.message.reply_text(
                "❌ Please send a valid screenshot or UTR/Transaction ID."
            )
            return True
    else:
        await update.message.reply_text(
            "❌ Please send a screenshot (photo) or type your UTR/Transaction ID."
        )
        return True

    # Update order with proof
    await db.update_order_proof(order_id, proof_data, proof_type, utr_text)

    # Get order and product details
    order = await db.get_order(order_id)
    product = await db.get_product(order["product_id"])

    # Clear awaiting state BEFORE notifying owners
    context.user_data["awaiting_proof"] = False
    context.user_data.pop("pending_order_id", None)
    context.user_data.pop("pending_product_id", None)

    # Forward proof to owners (dynamic per-bot)
    # Escape user-provided data to prevent HTML/Markdown parse errors
    safe_name = html.escape(user.first_name or 'Unknown')
    safe_username = html.escape(user.username) if user.username else ''
    safe_product_name = html.escape(product['name'])
    safe_price = html.escape(format_price(product['price']))

    for owner_id in owner_ids:
        try:
            owner_text = (
                f"🔔 <b>New Payment Proof Received</b>\n\n"
                f"👤 User: {safe_name}"
                f"{f' (@{safe_username})' if safe_username else ''}\n"
                f"🆔 User ID: <code>{user.id}</code>\n"
                f"📦 Plan: <b>{safe_product_name}</b>\n"
                f"💰 Price: <b>{safe_price}</b>\n"
                f"📋 Order: #{order_id}\n"
                f"📝 Proof: {proof_type.upper()}"
            )

            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Approve",
                        callback_data=f"approve_{order_id}",
                    ),
                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=f"reject_{order_id}",
                    ),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if proof_type == "screenshot":
                # Add UTR to caption if user typed it with screenshot
                if utr_text:
                    safe_utr = html.escape(utr_text)
                    owner_text += f"\n\n💬 UTR/Transaction ID:\n<code>{safe_utr}</code>"
                await context.bot.send_photo(
                    chat_id=owner_id,
                    photo=proof_data,
                    caption=owner_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
            else:
                safe_proof = html.escape(proof_data)
                owner_text += f"\n\n💬 UTR/Transaction ID:\n<code>{safe_proof}</code>"
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=owner_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.warning(f"Failed to notify owner {owner_id}: {e}")

    # Confirm to user
    await update.message.reply_text(
        "✅ <b>Payment proof received!</b>\n\n"
        "⏳ Our team will verify and activate your plan within 30 minutes.\n"
        "You'll receive a notification once approved. 🔔",
        parse_mode="HTML",
    )
    return True


async def approve_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle order approval by owner — sends channel link to user."""
    db = get_db(context)
    query = update.callback_query
    await query.answer()

    # Verify owner access (silent)
    if query.from_user.id not in get_owner_ids(context):
        return

    order_id = int(query.data.split("_")[1])
    order = await db.get_order(order_id)

    if not order:
        await query.answer("❌ Order not found.", show_alert=True)
        return

    if order["status"] != "pending":
        await query.answer(
            f"⚠️ Order already {order['status']}.", show_alert=True
        )
        return

    product = await db.get_product(order["product_id"])

    # Approve the order
    await db.update_order_status(order_id, "approved", query.from_user.id)

    # Send channel link to user
    channel_link = product.get("channel_link", "") if product else ""
    try:
        safe_prod = html.escape(product['name'])
        user_text = (
            f"🎉 <b>Congratulations!</b>\n\n"
            f"✅ Your payment for <b>{safe_prod}</b> has been approved!\n\n"
        )
        if channel_link:
            safe_link = html.escape(channel_link)
            user_text += (
                f"🔗 <b>Access your premium content here:</b>\n{safe_link}\n\n"
            )
        user_text += "Thank you for your purchase! Enjoy! 🌟"

        await context.bot.send_message(
            chat_id=order["user_id"],
            text=user_text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(
            f"Failed to send approval to user {order['user_id']}: {e}"
        )

    # Update owner's message to show approved status
    try:
        if query.message.caption:
            new_caption = query.message.caption + "\n\n✅ <b>APPROVED</b> ✅"
            await query.edit_message_caption(
                caption=new_caption, parse_mode="HTML"
            )
        else:
            new_text = query.message.text + "\n\n✅ <b>APPROVED</b> ✅"
            await query.edit_message_text(
                text=new_text, parse_mode="HTML"
            )
    except Exception:
        await query.answer("✅ Order approved!", show_alert=True)


async def reject_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle order rejection by owner — notifies user."""
    db = get_db(context)
    query = update.callback_query
    await query.answer()

    # Verify owner access (silent)
    if query.from_user.id not in get_owner_ids(context):
        return

    order_id = int(query.data.split("_")[1])
    order = await db.get_order(order_id)

    if not order:
        await query.answer("❌ Order not found.", show_alert=True)
        return

    if order["status"] != "pending":
        await query.answer(
            f"⚠️ Order already {order['status']}.", show_alert=True
        )
        return

    product = await db.get_product(order["product_id"])

    # Reject the order
    await db.update_order_status(order_id, "rejected", query.from_user.id)

    # Notify user
    try:
        safe_prod = html.escape(product['name'])
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=(
                f"❌ <b>Payment Rejected</b>\n\n"
                f"Your payment for <b>{safe_prod}</b> could not be verified.\n\n"
                f"If you believe this is an error, please try again or contact support."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(
            f"Failed to send rejection to user {order['user_id']}: {e}"
        )

    # Update owner's message to show rejected status
    try:
        if query.message.caption:
            new_caption = query.message.caption + "\n\n❌ <b>REJECTED</b> ❌"
            await query.edit_message_caption(
                caption=new_caption, parse_mode="HTML"
            )
        else:
            new_text = query.message.text + "\n\n❌ <b>REJECTED</b> ❌"
            await query.edit_message_text(
                text=new_text, parse_mode="HTML"
            )
    except Exception:
        await query.answer("❌ Order rejected.", show_alert=True)
