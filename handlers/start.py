"""
Host Bot — Start & Product Browsing Handlers
/start on hosted bots: normal for ALL users (products, welcome, etc.)
/start on main bot: handled separately in main.py
MIRROR: Products from main DB. Settings from per-bot with fallback to main.
"""
import asyncio
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import ContextTypes

from utils.bot_helpers import (
    get_db, get_forward_protection, get_auto_disappear,
    get_setting_with_fallback, get_products_db, is_main_bot,
    get_color_buttons_enabled, get_auto_disappear_seconds,
    has_buynow_access,
)
from utils.media_clone import get_translated_file_id
from utils.helpers import format_price

logger = logging.getLogger(__name__)

WELCOME_TEXT = """🎉 *Welcome to VIP Access Bot!*

✨ Get exclusive access to premium content
💰 Affordable plans starting at just ₹99
✨ Content quality aisi ki dekhi nahi hogi 
✨ Only Premium Content
✨ Daily New Uploads
✨ 10000+ videos
✨ 25000+ videos 
✨ 5k Videos

✨ *TRY OUR ANY PLAN FOR CHECKING THE QUALITY* ✨

👇 *Choose a plan below to get started:*"""


async def build_product_keyboard(context) -> InlineKeyboardMarkup:
    """Build product keyboard — products from per-bot DB if customized, else main DB."""
    products_db = await get_products_db(context)
    products = await products_db.get_all_products()
    keyboard = []
    color_enabled = await get_color_buttons_enabled(context)

    for product in products:
        btn_kwargs = {
            "text": f"{product['name']} — {format_price(product['price'])}",
            "callback_data": f"product_{product['id']}",
        }
        style = product.get("button_style", "") or "success"  # default green
        if color_enabled and style in ("primary", "success", "danger"):
            btn_kwargs["style"] = style
        keyboard.append([InlineKeyboardButton(**btn_kwargs)])

    # Buy Now button — vertical (only if hosted bot has access AND toggle enabled)
    bot_id = context.bot_data.get("bot_id", "")
    if bot_id:
        try:
            buynow_access = await has_buynow_access(context)
            if buynow_access:
                buynow_enabled = await get_setting_with_fallback(context, "buynow_enabled")
                if buynow_enabled == "1":
                    keyboard.append([InlineKeyboardButton("💰 Buy Now", callback_data="buynow_menu", style="success")])
        except Exception:
            pass

    # Demo button — vertical (if enabled)
    demo_enabled = await get_setting_with_fallback(context, "demo_button_enabled")
    if demo_enabled == "1":
        demo_link = await get_setting_with_fallback(context, "demo_link") or ""
        demo_color = await get_setting_with_fallback(context, "demo_button_color") or ""
        btn_kwargs = {"text": "🎬 Demo"}
        if demo_link:
            btn_kwargs["url"] = demo_link
        else:
            btn_kwargs["callback_data"] = "show_demo"
        if color_enabled and demo_color in ("primary", "success", "danger"):
            btn_kwargs["style"] = demo_color
        keyboard.append([InlineKeyboardButton(**btn_kwargs)])

    # Proof button — uses fallback (per-bot → main)
    proof_enabled = await get_setting_with_fallback(context, "proof_button_enabled")
    if proof_enabled == "1":
        proof_link = await get_setting_with_fallback(context, "proof_link") or ""
        proof_color = await get_setting_with_fallback(context, "proof_button_color") or ""
        proof_btn_kwargs = {"text": "📸 Proof"}
        if proof_link:
            proof_btn_kwargs["url"] = proof_link
        else:
            proof_btn_kwargs["callback_data"] = "show_proof"
        if color_enabled and proof_color in ("primary", "success", "danger"):
            proof_btn_kwargs["style"] = proof_color
        keyboard.append([InlineKeyboardButton(**proof_btn_kwargs)])

    # Bottom row — horizontal: How to Use + Report an Issue
    bottom_row = []
    htu_enabled = await get_setting_with_fallback(context, "htu_button_enabled")
    if htu_enabled != "0":
        bottom_row.append(InlineKeyboardButton("❓ How to Use", callback_data="how_to_use"))
    bottom_row.append(InlineKeyboardButton("🆘 Report an Issue", callback_data="report_issue", style="primary"))
    if bottom_row:
        keyboard.append(bottom_row)

    return InlineKeyboardMarkup(keyboard)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start on hosted bots — normal for ALL users.
    Products from main DB, settings with fallback, users in per-bot DB.
    """
    # ── Admin must /start the bot first (for new hosted bots) ──
    if context.bot_data.get("awaiting_admin_start"):
        from utils.bot_helpers import get_owner_ids
        owner_ids = get_owner_ids(context)
        if update.effective_user.id in owner_ids:
            context.bot_data["awaiting_admin_start"] = False
            context.bot_data["admin_started"] = True
            await update.message.reply_text(
                "✅ *Bot Activated!*\n\n"
                "⚙️ Setting up media files... Please wait.",
                parse_mode="Markdown",
            )
            return
        else:
            bot_username = context.bot.username or "this bot"
            await update.message.reply_text(
                "⚠️ *Bot is being set up by admin.*\n\n"
                "Please try again in a few minutes.",
                parse_mode="Markdown",
            )
            return

    db = get_db(context)
    user = update.effective_user

    # ── SWIPE MODE: Check if user is NEW before registering ──
    bot_id = context.bot_data.get("bot_id", "")
    is_new_user = False
    if bot_id:
        existing_user = await db.get_user(user.id)
        if not existing_user:
            is_new_user = True

    # Register user in per-bot DB
    await db.add_user(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    # ── SWIPE MODE: Register new user in swipe_users if mode is active ──
    if bot_id and is_new_user:
        main_db = context.bot_data.get("main_db")
        if main_db:
            try:
                swipe_config = await main_db.get_swipe_config(bot_id)
                if swipe_config and swipe_config["is_active"]:
                    await main_db.add_swipe_user(
                        bot_id, user.id, swipe_config["session_id"]
                    )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Swipe user check failed: {e}")

    protect = await get_forward_protection(context)

    # Send welcome media — uses fallback (per-bot setting → main setting)
    # Store message IDs so we can delete them when user clicks a product
    welcome_media_msg_ids = []
    chat_id = update.message.chat_id

    media_ids_json = await get_setting_with_fallback(context, "welcome_media_ids")
    if media_ids_json and media_ids_json != "[]":
        try:
            welcome_ids = json.loads(media_ids_json)
            if welcome_ids:
                # Check if welcome media is from per-bot settings (native) or main fallback
                per_bot_db = get_db(context)
                per_bot_value = await per_bot_db.get_setting("welcome_media_ids")
                is_from_main = not is_main_bot(context) and (not per_bot_value or per_bot_value == "[]")

                media_group = []
                for raw_id in welcome_ids:
                    translated_id = await get_translated_file_id(context, raw_id)
                    # Only skip un-cloned media when it came from main bot fallback
                    if translated_id == raw_id and is_from_main:
                        logger.warning(f"Skipping un-cloned welcome media: {raw_id[:30]}...")
                        continue
                    if translated_id.startswith("photo:"):
                        media_group.append(InputMediaPhoto(translated_id.replace("photo:", "", 1), has_spoiler=True))
                    elif translated_id.startswith("video:"):
                        media_group.append(InputMediaVideo(translated_id.replace("video:", "", 1), has_spoiler=True))
                    else:
                        media_group.append(InputMediaVideo(translated_id, has_spoiler=True))

                chunk_size = 10
                for i in range(0, len(media_group), chunk_size):
                    chunk = media_group[i:i + chunk_size]
                    try:
                        if len(chunk) == 1:
                            item = chunk[0]
                            if isinstance(item, InputMediaPhoto):
                                _wm = await context.bot.send_photo(chat_id=chat_id, photo=item.media, protect_content=protect, has_spoiler=True)
                            else:
                                _wm = await context.bot.send_video(chat_id=chat_id, video=item.media, protect_content=protect, has_spoiler=True)
                            if _wm:
                                welcome_media_msg_ids.append(_wm.message_id)
                        else:
                            _wms = await context.bot.send_media_group(
                                chat_id=chat_id, media=chunk,
                                protect_content=protect,
                            )
                            if _wms:
                                welcome_media_msg_ids.extend([m.message_id for m in _wms])
                    except Exception as e:
                        logger.warning(f"Failed to send welcome media group chunk: {e}")
        except Exception as e:
            logger.warning(f"Failed to parse welcome_media_ids: {e}")
    else:
        # Fallback: single welcome video/photo
        media_type = await get_setting_with_fallback(context, "welcome_media_type") or "video"
        if media_type == "video":
            video_id = await get_setting_with_fallback(context, "welcome_video_id")
            if video_id:
                translated_id = await get_translated_file_id(context, f"video:{video_id}")
                video_id = translated_id.replace("video:", "")
                try:
                    _wm = await update.message.reply_video(video=video_id, protect_content=protect, has_spoiler=True)
                    if _wm:
                        welcome_media_msg_ids.append(_wm.message_id)
                except Exception:
                    pass
        else:
            photo_id = await get_setting_with_fallback(context, "welcome_photo_id")
            if photo_id:
                translated_id = await get_translated_file_id(context, f"photo:{photo_id}")
                photo_id = translated_id.replace("photo:", "")
                try:
                    _wm = await update.message.reply_photo(photo=photo_id, protect_content=protect, has_spoiler=True)
                    if _wm:
                        welcome_media_msg_ids.append(_wm.message_id)
                except Exception:
                    pass

    # Store welcome media message IDs for deletion when user clicks a product
    context.user_data["welcome_media_msg_ids"] = welcome_media_msg_ids
    context.user_data["welcome_chat_id"] = chat_id

    reply_markup = await build_product_keyboard(context)
    custom_text = await get_setting_with_fallback(context, "welcome_text")
    welcome_text = custom_text if custom_text else WELCOME_TEXT
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")


async def product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Product button click — trial media + buy/back buttons."""
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

    # ── Delete welcome media messages so product page looks clean ──
    welcome_msg_ids = context.user_data.pop("welcome_media_msg_ids", [])
    if welcome_msg_ids:
        w_chat_id = context.user_data.get("welcome_chat_id", query.message.chat_id)
        for mid in welcome_msg_ids:
            try:
                await context.bot.delete_message(chat_id=w_chat_id, message_id=mid)
            except Exception:
                pass

    # ── Delete the welcome text message (the one with product buttons) ──
    try:
        await query.message.delete()
    except Exception:
        pass

    chat_id = query.message.chat_id

    trial_ids = product.get("trial_file_ids_list", [])
    trial_sent_msg_ids = []  # Store all trial media msg IDs for back button cleanup
    if trial_ids:
        protect = await get_forward_protection(context)
        should_disappear = await get_auto_disappear(context)
        sent_messages = []

        # Check if products are from main DB (mirrored) or hosted bot's own DB
        is_mirrored = not is_main_bot(context) and products_db is not get_db(context)

        media_group = []
        for raw_id in trial_ids:
            if not raw_id:
                continue
            translated_id = await get_translated_file_id(context, raw_id)
            # Only skip un-cloned media when products are MIRRORED from main bot
            # If hosted bot admin uploaded media directly, file_ids are already native
            if translated_id == raw_id and is_mirrored:
                logger.warning(f"Skipping un-cloned trial media: {raw_id[:30]}...")
                continue
            if translated_id.startswith("photo:"):
                media_group.append(InputMediaPhoto(translated_id.replace("photo:", "", 1), has_spoiler=True))
            elif translated_id.startswith("video:"):
                media_group.append(InputMediaVideo(translated_id.replace("video:", "", 1), has_spoiler=True))
            else:
                media_group.append(InputMediaVideo(translated_id, has_spoiler=True))

        chunk_size = 10
        for i in range(0, len(media_group), chunk_size):
            chunk = media_group[i:i + chunk_size]
            try:
                if len(chunk) == 1:
                    item = chunk[0]
                    if isinstance(item, InputMediaPhoto):
                        msg = await context.bot.send_photo(chat_id=chat_id, photo=item.media, protect_content=protect, has_spoiler=True)
                    else:
                        msg = await context.bot.send_video(chat_id=chat_id, video=item.media, protect_content=protect, has_spoiler=True)
                    if msg:
                        trial_sent_msg_ids.append(msg.message_id)
                        if should_disappear:
                            sent_messages.append(msg)
                else:
                    sent_msgs = await context.bot.send_media_group(
                        chat_id=chat_id, media=chunk,
                        protect_content=protect,
                    )
                    if sent_msgs:
                        trial_sent_msg_ids.extend([m.message_id for m in sent_msgs])
                        if should_disappear:
                            sent_messages.extend(sent_msgs)
            except Exception as e:
                logger.warning(f"Failed to send media group chunk: {e}")

        if should_disappear and sent_messages:
            disappear_seconds = await get_auto_disappear_seconds(context)
            async def _auto_delete(bot, _chat_id, messages, delay):
                await asyncio.sleep(delay)
                for msg in messages:
                    try:
                        await bot.delete_message(chat_id=_chat_id, message_id=msg.message_id)
                    except Exception:
                        pass
            asyncio.create_task(_auto_delete(context.bot, chat_id, list(sent_messages), disappear_seconds))

    # Store trial media msg IDs so back button can clean them up
    context.user_data["product_trial_msg_ids"] = trial_sent_msg_ids

    text = f"📦 *{product['name']}*\n"
    if product.get("description"):
        text += f"📝 {product['description']}\n"
    text += f"\n💰 Price: *{format_price(product['price'])}*\n"
    text += "\n🔥 *Full Indian content approx 40000+ videos - Buy now to get access*"

    keyboard = [
        [
            InlineKeyboardButton("💰 Buy Now", callback_data=f"buy_{product_id}", style="success"),
        ],
        [
            InlineKeyboardButton("🔙 Back", callback_data="back_to_products", style="danger"),
        ],
    ]

    # Send product info + buttons as NEW message BELOW the trial media
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def back_to_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    chat_id = query.message.chat_id

    # ── Delete trial media messages that were sent for the product ──
    trial_msg_ids = context.user_data.pop("product_trial_msg_ids", [])
    if trial_msg_ids:
        for mid in trial_msg_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass

    # ── Delete the product text message (Buy Now / Back buttons) ──
    try:
        await query.message.delete()
    except Exception:
        pass

    # ── Re-send welcome media ──
    protect = await get_forward_protection(context)
    welcome_media_msg_ids = []

    media_ids_json = await get_setting_with_fallback(context, "welcome_media_ids")
    if media_ids_json and media_ids_json != "[]":
        try:
            welcome_ids = json.loads(media_ids_json)
            if welcome_ids:
                per_bot_db = get_db(context)
                per_bot_value = await per_bot_db.get_setting("welcome_media_ids")
                is_from_main = not is_main_bot(context) and (not per_bot_value or per_bot_value == "[]")

                media_group = []
                for raw_id in welcome_ids:
                    translated_id = await get_translated_file_id(context, raw_id)
                    if translated_id == raw_id and is_from_main:
                        continue
                    if translated_id.startswith("photo:"):
                        media_group.append(InputMediaPhoto(translated_id.replace("photo:", "", 1), has_spoiler=True))
                    elif translated_id.startswith("video:"):
                        media_group.append(InputMediaVideo(translated_id.replace("video:", "", 1), has_spoiler=True))
                    else:
                        media_group.append(InputMediaVideo(translated_id, has_spoiler=True))

                chunk_size = 10
                for i in range(0, len(media_group), chunk_size):
                    chunk = media_group[i:i + chunk_size]
                    try:
                        if len(chunk) == 1:
                            item = chunk[0]
                            if isinstance(item, InputMediaPhoto):
                                _wm = await context.bot.send_photo(chat_id=chat_id, photo=item.media, protect_content=protect, has_spoiler=True)
                            else:
                                _wm = await context.bot.send_video(chat_id=chat_id, video=item.media, protect_content=protect, has_spoiler=True)
                            if _wm:
                                welcome_media_msg_ids.append(_wm.message_id)
                        else:
                            _wms = await context.bot.send_media_group(
                                chat_id=chat_id, media=chunk,
                                protect_content=protect,
                            )
                            if _wms:
                                welcome_media_msg_ids.extend([m.message_id for m in _wms])
                    except Exception as e:
                        logger.warning(f"Failed to re-send welcome media chunk: {e}")
        except Exception as e:
            logger.warning(f"Failed to parse welcome_media_ids for back: {e}")
    else:
        media_type = await get_setting_with_fallback(context, "welcome_media_type") or "video"
        if media_type == "video":
            video_id = await get_setting_with_fallback(context, "welcome_video_id")
            if video_id:
                translated_id = await get_translated_file_id(context, f"video:{video_id}")
                video_id = translated_id.replace("video:", "")
                try:
                    _wm = await context.bot.send_video(chat_id=chat_id, video=video_id, protect_content=protect, has_spoiler=True)
                    if _wm:
                        welcome_media_msg_ids.append(_wm.message_id)
                except Exception:
                    pass
        else:
            photo_id = await get_setting_with_fallback(context, "welcome_photo_id")
            if photo_id:
                translated_id = await get_translated_file_id(context, f"photo:{photo_id}")
                photo_id = translated_id.replace("photo:", "")
                try:
                    _wm = await context.bot.send_photo(chat_id=chat_id, photo=photo_id, protect_content=protect, has_spoiler=True)
                    if _wm:
                        welcome_media_msg_ids.append(_wm.message_id)
                except Exception:
                    pass

    # Store new welcome media IDs for next product click
    context.user_data["welcome_media_msg_ids"] = welcome_media_msg_ids
    context.user_data["welcome_chat_id"] = chat_id

    reply_markup = await build_product_keyboard(context)
    custom_text = await get_setting_with_fallback(context, "welcome_text")
    welcome_text = custom_text if custom_text else WELCOME_TEXT
    # Send as NEW message below re-sent welcome media
    await context.bot.send_message(
        chat_id=chat_id,
        text=welcome_text, reply_markup=reply_markup, parse_mode="Markdown",
    )


async def show_proof_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    proof_photo = await get_setting_with_fallback(context, "proof_photo_id")
    if proof_photo:
        translated_id = await get_translated_file_id(context, f"photo:{proof_photo}")
        proof_photo = translated_id.replace("photo:", "")
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id, photo=proof_photo,
                caption="📸 *Customer Proof*", parse_mode="Markdown",
            )
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text="❌ Could not load proof photo.")
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text="📸 No proof available yet.")


async def how_to_use_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    video_id = await get_setting_with_fallback(context, "htu_video_id")
    text = await get_setting_with_fallback(context, "htu_text")
    if video_id and text:
        translated_id = await get_translated_file_id(context, f"video:{video_id}")
        video_id = translated_id.replace("video:", "")
        try:
            await context.bot.send_video(chat_id=query.message.chat_id, video=video_id, caption=text, parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(chat_id=query.message.chat_id, text=f"❓ *How to Use*\n\n{text}", parse_mode="Markdown")
    elif text:
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"❓ *How to Use*\n\n{text}", parse_mode="Markdown")
    elif video_id:
        translated_id = await get_translated_file_id(context, f"video:{video_id}")
        video_id = translated_id.replace("video:", "")
        await context.bot.send_video(chat_id=query.message.chat_id, video=video_id)
    else:
        await context.bot.send_message(chat_id=query.message.chat_id, text="❌ 'How to Use' is not configured yet.")


async def report_issue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    context.user_data["awaiting_issue"] = True
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_issue")]]
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🆘 *Report an Issue*\n\nPlease describe your issue below.\nYour message will be sent to the support team.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def cancel_issue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    context.user_data.pop("awaiting_issue", None)
    await query.edit_message_text("✅ Issue reporting cancelled.")


async def buynow_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Buy Now menu — shows price list + plan buttons. Replaces main menu via edit."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    db = get_db(context)
    price_list = await get_setting_with_fallback(context, "buynow_price_list")
    plans_json = await get_setting_with_fallback(context, "buynow_plans")

    try:
        plans = json.loads(plans_json) if plans_json else []
    except (json.JSONDecodeError, TypeError):
        plans = []

    text = "💰 *Buy Now*\n\n"
    if price_list:
        text += f"{price_list}\n\n"
    if plans:
        text += "👇 *Choose a plan below:*"
    else:
        text += "❌ _No plans configured yet._"

    keyboard = []
    for i, plan in enumerate(plans[:3]):
        label = f"{plan.get('label', f'Plan {i+1}')} — ₹{plan.get('price', 0)} / {plan.get('days', 0)} Days"
        style = plan.get("color", "")
        btn_kwargs = {"text": label, "callback_data": f"buynow_plan_{i}"}
        if style in ("primary", "success", "danger"):
            btn_kwargs["style"] = style
        keyboard.append([InlineKeyboardButton(**btn_kwargs)])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_products", style="danger")])

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
        )


async def show_demo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Demo button callback — when no demo link is set, show a message."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🎬 *Demo*\n\n❌ Demo link is not configured yet.\nPlease contact admin.",
        parse_mode="Markdown",
    )
