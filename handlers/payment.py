"""
Host Bot — Payment Flow Handlers
Handles: Buy → QR display → I Have Paid → Proof collection → Owner approve/reject.
MIRROR: Products from main DB, UPI/QR from overrides, orders in per-bot DB.
"""
import html
import json
import asyncio
import logging
import secrets
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.bot_helpers import get_db, get_owner_ids, get_upi_id, get_qr_image_id, get_products_db
from utils.media_clone import get_translated_file_id
from utils.qr_generator import generate_upi_qr
from utils.helpers import format_price

logger = logging.getLogger(__name__)


async def _begin_payfino_checkout(update, context, product_id, product_name, amount, channel_link):
    """Create Payfino checkout without asking the buyer for contact details."""
    bot_id = context.bot_data.get("bot_id", "")
    if not bot_id:
        return False
    db = get_db(context)
    from utils.payfino import client_from_bot_db
    client = await client_from_bot_db(db)
    if not client:
        return False

    callback_url = (await db.get_setting("payfino_callback_url") or "").strip()
    if not callback_url.startswith("https://"):
        await update.callback_query.message.reply_text(
            "⚠️ Online payment is not configured yet. Please contact the bot owner."
        )
        return True

    from utils.payfino import make_qr, PayfinoError
    main_db = context.bot_data.get("main_db")
    bot_id = context.bot_data.get("bot_id", "")
    reference_id = f"PF{bot_id}{secrets.token_hex(10)}"
    map_key = f"payfino_order_{reference_id}"
    mapping = {
        "bot_id": bot_id, "user_id": update.effective_user.id,
        "product_id": product_id,
        "product_name": str(product_name),
        "amount": float(amount), "channel_link": channel_link or "",
        "status": "creating",
    }
    if not main_db:
        await update.callback_query.message.reply_text("⚠️ Payment setup is unavailable. Please contact the bot owner.")
        return True
    await main_db.set_setting(map_key, json.dumps(mapping))
    names = (update.effective_user.full_name or "Customer").split(maxsplit=1)
    payload = {
        "amount": float(amount), "orderid": reference_id,
        "payee_fname": names[0], "payee_lname": names[1] if len(names) > 1 else "",
        "callbackurl": callback_url,
    }
    try:
        result = await client.create_payin(payload)
        data = result.get("data") or {}
        if not data:
            raise PayfinoError("Payfino returned no payment data")
        mapping.update({"status": "pending", "txn_id": data.get("txn_id", "")})
        await main_db.set_setting(map_key, json.dumps(mapping))
        qr_data = next((data.get(k) for k in
                        ("qr_string", "upi_link", "payment_link", "intent_url", "Paytm", "phonepe")
                        if data.get(k)), None)
        if not qr_data:
            raise PayfinoError("Payfino returned no usable QR or payment link")
        caption = (
            f"💳 <b>Payment for {html.escape(str(product_name))}</b>\n\n"
            f"Amount: <b>{html.escape(format_price(amount))}</b>\n\n"
            "Scan this QR using a UPI app. Access will be sent automatically after payment verification."
        )
        keyboard = None
        link = data.get("payment_link") or data.get("tiny_url")
        if link and link.startswith(("https://", "http://")):
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Pay online", url=link)]])
        await context.bot.send_photo(
            chat_id=update.callback_query.message.chat_id, photo=make_qr(qr_data),
            caption=caption, parse_mode="HTML", reply_markup=keyboard,
        )
    except Exception as exc:
        logger.warning("Payfino order creation failed: %s", exc)
        mapping["status"] = "failed"
        await main_db.set_setting(map_key, json.dumps(mapping))
        await update.callback_query.message.reply_text(
            "⚠️ Payfino could not create this payment. Its API may require buyer mobile and email; this bot does not collect them. Please try the normal UPI flow or contact the owner."
        )
    return True


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle Buy Now button.
    Shows UPI QR (override or auto-generated) + payment instructions.
    Product from main DB, UPI/QR from override system.
    """
    products_db = await get_products_db(context)
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    product_id = int(query.data.split("_")[1])
    product = await products_db.get_product(product_id)

    if not product:
        await query.message.reply_text("❌ Product not found.")
        return

    # Store buying context
    context.user_data["buying_product_id"] = product_id

    # ── SWIPE MODE: Determine UPI source ──
    bot_id = context.bot_data.get("bot_id", "")
    main_db = context.bot_data.get("main_db")
    is_swipe_user = False
    swipe_active = False

    if bot_id and main_db:
        is_swipe_user = await main_db.is_swipe_user(bot_id, query.from_user.id)
        if is_swipe_user:
            swipe_config = await main_db.get_swipe_config(bot_id)
            swipe_active = swipe_config and swipe_config["is_active"]

    # Get UPI/QR (checks overrides for hosted bots)
    upi_id = await get_upi_id(context)
    custom_qr = await get_qr_image_id(context)

    # SWIPE OVERRIDE: If swipe mode is active and user is swipe user, use swipe UPI
    if is_swipe_user and swipe_active:
        upi_id = swipe_config["swipe_upi_id"]
        custom_qr = ""  # Force auto-generated QR with swipe UPI

    # Merchant gateway is considered only after the unchanged Swipe override.
    if not (is_swipe_user and swipe_active):
        if await _begin_payfino_checkout(
            update, context, product_id, product["name"], product["price"],
            product.get("channel_link", ""),
        ):
            return

    # Store swipe context for payment proof handler
    context.user_data["is_swipe_purchase"] = is_swipe_user and swipe_active

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
                "✅ I Have Paid", callback_data=f"paid_{product_id}",
                style="success",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel", callback_data="back_to_products",
                style="danger",
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    sent_msg = None
    try:
        if custom_qr:
            translated_id = await get_translated_file_id(context, f"photo:{custom_qr}")
            custom_qr = translated_id.replace("photo:", "")
            sent_msg = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=custom_qr,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        else:
            qr_buffer = generate_upi_qr(
                upi_id, product["price"], product["name"]
            )
            sent_msg = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=qr_buffer,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Error sending payment QR: {e}")
        sent_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=caption,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    # ── SWIPE MODE: Start 5-min auto-expiry timer for swipe QR ──
    if is_swipe_user and swipe_active and sent_msg:
        asyncio.create_task(
            _swipe_qr_auto_expire(
                context, query.message.chat_id, sent_msg.message_id,
                product_id, bot_id, main_db,
            )
        )


async def paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle 'I Have Paid' button.
    Creates a pending order in per-bot DB and asks user for payment proof.
    """
    db = get_db(context)
    products_db = await get_products_db(context)
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    product_id = int(query.data.split("_")[1])
    product = await products_db.get_product(product_id)

    if not product:
        await query.message.reply_text("❌ Product not found.")
        return

    # ── SWIPE MODE: Block restricted users, route swipe orders ──
    bot_id = context.bot_data.get("bot_id", "")
    main_db = context.bot_data.get("main_db")
    if bot_id and main_db:
        is_swipe = await main_db.is_swipe_user(bot_id, query.from_user.id)
        if is_swipe:
            is_restricted = await main_db.is_swipe_user_restricted(bot_id, query.from_user.id)
            swipe_config = await main_db.get_swipe_config(bot_id)
            swipe_active = swipe_config and swipe_config["is_active"]
            if is_restricted and not swipe_active:
                await query.answer(
                    "⚠️ Purchases are currently unavailable for your account.",
                    show_alert=True,
                )
                return
            # Active swipe user → create order in swipe_orders
            if swipe_active and swipe_config:
                order_id = await main_db.create_swipe_order(
                    bot_id=bot_id,
                    user_id=query.from_user.id,
                    session_id=swipe_config["session_id"],
                    product_id=product_id,
                    product_name=product["name"],
                    amount=product["price"],
                )
                context.user_data["awaiting_proof"] = True
                context.user_data["pending_order_id"] = order_id
                context.user_data["pending_product_id"] = product_id
                context.user_data["is_swipe_purchase"] = True
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
                return

    # ── NORMAL FLOW: Create pending order in per-bot DB ──
    order_id = await db.create_order(
        user_id=query.from_user.id, product_id=product_id
    )

    # Set awaiting-proof state
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
    Orders in per-bot DB, products from main DB.
    """
    if not context.user_data.get("awaiting_proof"):
        return False

    order_id = context.user_data.get("pending_order_id")
    if not order_id:
        context.user_data["awaiting_proof"] = False
        return False

    db = get_db(context)
    products_db = await get_products_db(context)
    owner_ids = get_owner_ids(context)
    user = update.effective_user

    # Determine proof type and data
    utr_text = ""
    if update.message.photo:
        proof_data = update.message.photo[-1].file_id
        proof_type = "screenshot"
        if update.message.caption:
            utr_text = update.message.caption.strip()
    elif update.message.document:
        proof_data = update.message.document.file_id
        proof_type = "document"
        if update.message.caption:
            utr_text = update.message.caption.strip()
    elif update.message.text:
        proof_data = update.message.text.strip()
        proof_type = "utr"
        utr_text = proof_data
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

    # ── SWIPE MODE: Route proof to OWNER_ID only ──
    if context.user_data.get("is_swipe_purchase"):
        bot_id = context.bot_data.get("bot_id", "")
        main_db = context.bot_data.get("main_db")
        if bot_id and main_db:
            await main_db.update_swipe_order_proof(order_id, proof_data, proof_type, utr_text)
            products_db_local = await get_products_db(context)
            product = await products_db_local.get_product(
                context.user_data.get("pending_product_id")
            )
            # Buynow plan fallback for swipe orders
            if not product and context.user_data.get("buynow_plan_info"):
                _plan = context.user_data["buynow_plan_info"]
                product = {"name": _plan.get("label", "Buy Now Plan"), "price": _plan.get("price", 0),
                           "channel_link": "", "trial_file_ids_list": []}

            context.user_data["awaiting_proof"] = False
            context.user_data.pop("pending_order_id", None)
            context.user_data.pop("pending_product_id", None)
            context.user_data.pop("is_swipe_purchase", None)

            # Notify ONLY main bot owner
            from config import OWNER_ID
            safe_name = html.escape(user.first_name or 'Unknown')
            safe_username = html.escape(user.username) if user.username else ''
            safe_product_name = html.escape(product['name']) if product else 'VIP Plan'
            safe_price = html.escape(format_price(product['price'])) if product else 'N/A'
            bot_username = context.bot.username or bot_id

            owner_text = (
                f"🔔 <b>SWIPE MODE — New Payment Proof</b>\n\n"
                f"🤖 Bot: @{html.escape(bot_username)}\n"
                f"🔑 Bot ID: <code>{html.escape(bot_id)}</code>\n"
                f"👤 User: {safe_name}"
                f"{f' (@{safe_username})' if safe_username else ''}\n"
                f"🆔 User ID: <code>{user.id}</code>\n"
                f"📦 Plan: <b>{safe_product_name}</b>\n"
                f"💰 Price: <b>{safe_price}</b>\n"
                f"📋 Swipe Order: #{order_id}\n"
                f"📝 Proof: {proof_type.upper()}"
            )
            keyboard = [[
                InlineKeyboardButton(
                    "✅ Approve", callback_data=f"swipe_approve_{order_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    "❌ Reject", callback_data=f"swipe_reject_{order_id}",
                    style="danger",
                ),
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            try:
                if proof_type in ("screenshot", "document"):
                    if utr_text:
                        owner_text += f"\n\n💬 UTR:\n<code>{html.escape(utr_text)}</code>"
                    if proof_type == "screenshot":
                        await context.bot.send_photo(
                            chat_id=OWNER_ID, photo=proof_data,
                            caption=owner_text, reply_markup=reply_markup,
                            parse_mode="HTML",
                        )
                    else:
                        await context.bot.send_document(
                            chat_id=OWNER_ID, document=proof_data,
                            caption=owner_text, reply_markup=reply_markup,
                            parse_mode="HTML",
                        )
                else:
                    owner_text += f"\n\n💬 UTR:\n<code>{html.escape(proof_data)}</code>"
                    await context.bot.send_message(
                        chat_id=OWNER_ID, text=owner_text,
                        reply_markup=reply_markup, parse_mode="HTML",
                    )
            except Exception as e:
                logger.warning(f"Failed to notify main owner about swipe order: {e}")

            await update.message.reply_text(
                "✅ <b>Payment proof received!</b>\n\n"
                "⏳ Our team will verify and activate your plan within 30 minutes.\n"
                "You'll receive a notification once approved. 🔔",
                parse_mode="HTML",
            )
            return True

    # ── NORMAL FLOW ──
    # Update order with proof in per-bot DB
    await db.update_order_proof(order_id, proof_data, proof_type, utr_text)

    # Get order and product details
    order = await db.get_order(order_id)
    product = await products_db.get_product(order["product_id"])
    # Buynow plan fallback — if product_id=0 (buynow order), use plan info from user_data
    if not product and context.user_data.get("buynow_plan_info"):
        _plan = context.user_data["buynow_plan_info"]
        product = {"name": _plan.get("label", "Buy Now Plan"), "price": _plan.get("price", 0),
                   "channel_link": "", "trial_file_ids_list": []}

    # Clear awaiting state
    context.user_data["awaiting_proof"] = False
    context.user_data.pop("pending_order_id", None)
    context.user_data.pop("pending_product_id", None)

    # Forward proof to owners
    safe_name = html.escape(user.first_name or 'Unknown')
    safe_username = html.escape(user.username) if user.username else ''
    safe_product_name = html.escape(product['name']) if product else 'VIP Plan (Deleted)'
    safe_price = html.escape(format_price(product['price'])) if product else 'N/A'

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
                        style="success",
                    ),
                    InlineKeyboardButton(
                        "❌ Reject",
                        callback_data=f"reject_{order_id}",
                        style="danger",
                    ),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            if proof_type in ("screenshot", "document"):
                if utr_text:
                    safe_utr = html.escape(utr_text)
                    owner_text += f"\n\n💬 UTR/Transaction ID:\n<code>{safe_utr}</code>"
                
                if proof_type == "screenshot":
                    await context.bot.send_photo(
                        chat_id=owner_id,
                        photo=proof_data,
                        caption=owner_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                else:
                    await context.bot.send_document(
                        chat_id=owner_id,
                        document=proof_data,
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
    products_db = await get_products_db(context)
    query = update.callback_query
    await query.answer()

    # Verify owner access
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

    product = await products_db.get_product(order["product_id"])

    # Approve the order
    await db.update_order_status(order_id, "approved", query.from_user.id)

    # Send channel link to user
    channel_link = product.get("channel_link", "") if product else ""
    # Buynow fallback: if no channel link and product_id is 0, try buynow_channel_link setting
    if not channel_link and order.get("product_id") == 0:
        from utils.bot_helpers import get_setting_with_fallback
        buynow_channel = await get_setting_with_fallback(context, "buynow_channel_link")
        if buynow_channel:
            channel_link = buynow_channel
    try:
        safe_prod = html.escape(product['name']) if product else 'VIP Plan'
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

    # Update owner's message
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
    products_db = await get_products_db(context)
    query = update.callback_query
    await query.answer()

    # Verify owner access
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

    product = await products_db.get_product(order["product_id"])

    # Reject the order
    await db.update_order_status(order_id, "rejected", query.from_user.id)

    # Notify user
    try:
        safe_prod = html.escape(product['name']) if product else 'VIP Plan'
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

    # Update owner's message
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


# ═══════════════════════════════════════════════
#  SWIPE MODE — Approve / Reject / QR Auto-Expiry
# ═══════════════════════════════════════════════

async def swipe_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle swipe order approval — only OWNER_ID can approve."""
    from config import OWNER_ID
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        return

    order_id = int(query.data.split("_")[2])  # swipe_approve_123
    main_db = context.bot_data.get("main_db") or context.bot_data.get("db")
    order = await main_db.get_swipe_order(order_id)

    if not order:
        await query.answer("❌ Order not found.", show_alert=True)
        return

    if order["status"] != "pending":
        await query.answer(f"⚠️ Order already {order['status']}.", show_alert=True)
        return

    await main_db.update_swipe_order_status(order_id, "approved", query.from_user.id)

    # Get product for channel link
    products_db = await get_products_db(context)
    product = await products_db.get_product(order["product_id"])
    channel_link = product.get("channel_link", "") if product else ""

    try:
        safe_prod = html.escape(order.get("product_name", "VIP Plan"))
        user_text = (
            f"🎉 <b>Congratulations!</b>\n\n"
            f"✅ Your payment for <b>{safe_prod}</b> has been approved!\n\n"
        )
        if channel_link:
            user_text += f"🔗 <b>Access your premium content here:</b>\n{html.escape(channel_link)}\n\n"
        user_text += "Thank you for your purchase! Enjoy! 🌟"

        await context.bot.send_message(
            chat_id=order["user_id"], text=user_text, parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Failed to send swipe approval to user {order['user_id']}: {e}")

    try:
        if query.message.caption:
            await query.edit_message_caption(
                caption=query.message.caption + "\n\n✅ <b>APPROVED</b> ✅",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                text=query.message.text + "\n\n✅ <b>APPROVED</b> ✅",
                parse_mode="HTML",
            )
    except Exception:
        await query.answer("✅ Swipe order approved!", show_alert=True)


async def swipe_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle swipe order rejection — only OWNER_ID can reject."""
    from config import OWNER_ID
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        return

    order_id = int(query.data.split("_")[2])  # swipe_reject_123
    main_db = context.bot_data.get("main_db") or context.bot_data.get("db")
    order = await main_db.get_swipe_order(order_id)

    if not order:
        await query.answer("❌ Order not found.", show_alert=True)
        return

    if order["status"] != "pending":
        await query.answer(f"⚠️ Order already {order['status']}.", show_alert=True)
        return

    await main_db.update_swipe_order_status(order_id, "rejected", query.from_user.id)

    try:
        safe_prod = html.escape(order.get("product_name", "VIP Plan"))
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
        logger.warning(f"Failed to send swipe rejection to user {order['user_id']}: {e}")

    try:
        if query.message.caption:
            await query.edit_message_caption(
                caption=query.message.caption + "\n\n❌ <b>REJECTED</b> ❌",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                text=query.message.text + "\n\n❌ <b>REJECTED</b> ❌",
                parse_mode="HTML",
            )
    except Exception:
        await query.answer("❌ Swipe order rejected.", show_alert=True)


async def _swipe_qr_auto_expire(context, chat_id, msg_id, product_id, bot_id, main_db):
    """Background task: auto-delete swipe QR after 5 min or if mode turns OFF."""
    try:
        for _ in range(30):  # 30 × 10 sec = 5 min
            await asyncio.sleep(10)
            still_active = await main_db.is_swipe_mode_active(bot_id)
            if not still_active:
                break  # Mode turned OFF — remove QR immediately

        # Delete QR message
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

        # Re-send Buy Now + Back buttons
        keyboard = [
            [InlineKeyboardButton("💰 Buy Now", callback_data=f"buy_{product_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_products")],
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏰ Payment window expired. Click Buy Now to try again.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.warning(f"Swipe QR auto-expire error: {e}")


# ═══════════════════════════════════════════════
#  BUY NOW MODE — Plan Selection & Payment
# ═══════════════════════════════════════════════

async def buynow_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Buy Now plan button click — generates dynamic QR for the selected plan."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    # Parse plan index from callback data: buynow_plan_0, buynow_plan_1, buynow_plan_2
    plan_index = int(query.data.split("_")[2])

    # Get plans from settings
    from utils.bot_helpers import get_setting_with_fallback
    plans_json = await get_setting_with_fallback(context, "buynow_plans")
    try:
        plans = json.loads(plans_json) if plans_json else []
    except (json.JSONDecodeError, TypeError):
        plans = []

    if plan_index >= len(plans):
        await query.message.reply_text("❌ Plan not found.")
        return

    plan = plans[plan_index]
    plan_price = float(plan.get("price", 0))
    plan_label = plan.get("label", f"Plan {plan_index + 1}")
    plan_days = plan.get("days", 0)

    # Store buynow context for payment proof handler
    context.user_data["buying_product_id"] = 0  # special marker for buynow
    context.user_data["buynow_plan_info"] = {
        "label": plan_label,
        "price": plan_price,
        "days": plan_days,
        "index": plan_index,
    }

    # ── Determine UPI source (same logic as buy_callback) ──
    bot_id = context.bot_data.get("bot_id", "")
    main_db = context.bot_data.get("main_db")
    is_swipe_user = False
    swipe_active = False

    if bot_id and main_db:
        is_swipe_user = await main_db.is_swipe_user(bot_id, query.from_user.id)
        if is_swipe_user:
            swipe_config = await main_db.get_swipe_config(bot_id)
            swipe_active = swipe_config and swipe_config["is_active"]

    upi_id = await get_upi_id(context)
    custom_qr = await get_qr_image_id(context)

    # SWIPE OVERRIDE
    if is_swipe_user and swipe_active:
        upi_id = swipe_config["swipe_upi_id"]
        custom_qr = ""

    if not (is_swipe_user and swipe_active):
        from utils.bot_helpers import get_setting_with_fallback
        plan_channel = await get_setting_with_fallback(context, "buynow_channel_link") or ""
        if await _begin_payfino_checkout(
            update, context, 0, plan_label, plan_price, plan_channel,
        ):
            return

    context.user_data["is_swipe_purchase"] = is_swipe_user and swipe_active

    safe_label = html.escape(plan_label)
    price_str = html.escape(format_price(plan_price))
    upi_escaped = html.escape(upi_id)

    caption = (
        f"💳 <b>Payment for: {safe_label}</b>\n\n"
        f"💰 Amount: <b>{price_str}</b>\n"
        f"📅 Duration: <b>{plan_days} Days</b>\n\n"
        f"📱 UPI ID: <code>{upi_escaped}</code>\n\n"
        f"📌 Scan the QR code or copy the UPI ID above to make payment.\n"
        f"After payment, click <b>I Have Paid</b> button below."
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ I Have Paid", callback_data=f"buynow_paid_{plan_index}",
                style="success",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel", callback_data="back_to_products",
                style="danger",
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    sent_msg = None
    try:
        if custom_qr:
            translated_id = await get_translated_file_id(context, f"photo:{custom_qr}")
            custom_qr = translated_id.replace("photo:", "")
            sent_msg = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=custom_qr,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        else:
            qr_buffer = generate_upi_qr(upi_id, plan_price, plan_label)
            sent_msg = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=qr_buffer,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Error sending buynow payment QR: {e}")
        sent_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=caption,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    # SWIPE MODE: Start 5-min auto-expiry timer for swipe QR
    if is_swipe_user and swipe_active and sent_msg:
        import asyncio
        asyncio.create_task(
            _swipe_qr_auto_expire(
                context, query.message.chat_id, sent_msg.message_id,
                0, bot_id, main_db,
            )
        )


async def buynow_paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'I Have Paid' for Buy Now plans — creates order and asks for proof."""
    db = get_db(context)
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    plan_index = int(query.data.split("_")[2])
    plan_info = context.user_data.get("buynow_plan_info")

    if not plan_info:
        await query.message.reply_text("❌ Session expired. Please try again from the menu.")
        return

    # ── SWIPE MODE: Block restricted users, route swipe orders ──
    bot_id = context.bot_data.get("bot_id", "")
    main_db = context.bot_data.get("main_db")
    if bot_id and main_db:
        is_swipe = await main_db.is_swipe_user(bot_id, query.from_user.id)
        if is_swipe:
            is_restricted = await main_db.is_swipe_user_restricted(bot_id, query.from_user.id)
            swipe_config = await main_db.get_swipe_config(bot_id)
            swipe_active = swipe_config and swipe_config["is_active"]
            if is_restricted and not swipe_active:
                await query.answer(
                    "⚠️ Purchases are currently unavailable for your account.",
                    show_alert=True,
                )
                return
            # Active swipe user → create order in swipe_orders
            if swipe_active and swipe_config:
                order_id = await main_db.create_swipe_order(
                    bot_id=bot_id,
                    user_id=query.from_user.id,
                    session_id=swipe_config["session_id"],
                    product_id=0,
                    product_name=plan_info.get("label", "Buy Now Plan"),
                    amount=plan_info.get("price", 0),
                )
                context.user_data["awaiting_proof"] = True
                context.user_data["pending_order_id"] = order_id
                context.user_data["pending_product_id"] = 0
                context.user_data["is_swipe_purchase"] = True
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
                return

    # ── NORMAL FLOW: Create pending order in per-bot DB ──
    order_id = await db.create_order(
        user_id=query.from_user.id, product_id=0
    )

    context.user_data["awaiting_proof"] = True
    context.user_data["pending_order_id"] = order_id
    context.user_data["pending_product_id"] = 0

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
