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
    get_setting_with_fallback, get_products_db,
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

    for product in products:
        keyboard.append([InlineKeyboardButton(
            f"{product['name']} — {format_price(product['price'])}",
            callback_data=f"product_{product['id']}",
        )])

    # Proof button — uses fallback (per-bot → main)
    proof_enabled = await get_setting_with_fallback(context, "proof_button_enabled")
    if proof_enabled == "1":
        proof_link = await get_setting_with_fallback(context, "proof_link") or ""
        if proof_link:
            keyboard.append([InlineKeyboardButton("📸 Proof", url=proof_link)])
        else:
            keyboard.append([InlineKeyboardButton("📸 Proof", callback_data="show_proof")])

    keyboard.append([InlineKeyboardButton("❓ How to Use", callback_data="how_to_use")])
    keyboard.append([InlineKeyboardButton("🆘 Report an Issue", callback_data="report_issue")])

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

    # Register user in per-bot DB
    await db.add_user(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    protect = await get_forward_protection(context)

    # Send welcome media — uses fallback (per-bot setting → main setting)
    media_ids_json = await get_setting_with_fallback(context, "welcome_media_ids")
    if media_ids_json and media_ids_json != "[]":
        try:
            welcome_ids = json.loads(media_ids_json)
            if welcome_ids:
                media_group = []
                for raw_id in welcome_ids:
                    translated_id = await get_translated_file_id(context, raw_id)
                    if translated_id.startswith("photo:"):
                        media_group.append(InputMediaPhoto(translated_id.replace("photo:", "", 1)))
                    elif translated_id.startswith("video:"):
                        media_group.append(InputMediaVideo(translated_id.replace("video:", "", 1)))
                    else:
                        media_group.append(InputMediaVideo(translated_id))

                chunk_size = 10
                for i in range(0, len(media_group), chunk_size):
                    chunk = media_group[i:i + chunk_size]
                    try:
                        if len(chunk) == 1:
                            item = chunk[0]
                            if isinstance(item, InputMediaPhoto):
                                await context.bot.send_photo(chat_id=update.message.chat_id, photo=item.media, protect_content=protect)
                            else:
                                await context.bot.send_video(chat_id=update.message.chat_id, video=item.media, protect_content=protect)
                        else:
                            await context.bot.send_media_group(
                                chat_id=update.message.chat_id, media=chunk,
                                protect_content=protect,
                            )
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
                    await update.message.reply_video(video=video_id, protect_content=protect)
                except Exception:
                    pass
        else:
            photo_id = await get_setting_with_fallback(context, "welcome_photo_id")
            if photo_id:
                translated_id = await get_translated_file_id(context, f"photo:{photo_id}")
                photo_id = translated_id.replace("photo:", "")
                try:
                    await update.message.reply_photo(photo=photo_id, protect_content=protect)
                except Exception:
                    pass

    reply_markup = await build_product_keyboard(context)
    await update.message.reply_text(WELCOME_TEXT, reply_markup=reply_markup, parse_mode="Markdown")


async def product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Product button click — trial media + buy/back buttons."""
    products_db = await get_products_db(context)
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split("_")[1])
    product = await products_db.get_product(product_id)

    if not product:
        await query.message.reply_text("❌ Product not found.")
        return

    trial_ids = product.get("trial_file_ids_list", [])
    if trial_ids:
        protect = await get_forward_protection(context)
        should_disappear = await get_auto_disappear(context)
        sent_messages = []

        media_group = []
        for raw_id in trial_ids:
            if not raw_id:
                continue
            translated_id = await get_translated_file_id(context, raw_id)
            if translated_id.startswith("photo:"):
                media_group.append(InputMediaPhoto(translated_id.replace("photo:", "", 1)))
            elif translated_id.startswith("video:"):
                media_group.append(InputMediaVideo(translated_id.replace("video:", "", 1)))
            else:
                media_group.append(InputMediaVideo(translated_id))

        chunk_size = 10
        for i in range(0, len(media_group), chunk_size):
            chunk = media_group[i:i + chunk_size]
            try:
                if len(chunk) == 1:
                    item = chunk[0]
                    if isinstance(item, InputMediaPhoto):
                        msg = await context.bot.send_photo(chat_id=query.message.chat_id, photo=item.media, protect_content=protect)
                    else:
                        msg = await context.bot.send_video(chat_id=query.message.chat_id, video=item.media, protect_content=protect)
                    if should_disappear and msg:
                        sent_messages.append(msg)
                else:
                    sent_msgs = await context.bot.send_media_group(
                        chat_id=query.message.chat_id, media=chunk,
                        protect_content=protect,
                    )
                    if should_disappear and sent_msgs:
                        sent_messages.extend(sent_msgs)
            except Exception as e:
                logger.warning(f"Failed to send media group chunk: {e}")

        if should_disappear and sent_messages:
            async def _auto_delete(bot, chat_id, messages):
                await asyncio.sleep(1800)
                for msg in messages:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
                    except Exception:
                        pass
            asyncio.create_task(_auto_delete(context.bot, query.message.chat_id, list(sent_messages)))

    text = f"📦 *{product['name']}*\n"
    if product.get("description"):
        text += f"📝 {product['description']}\n"
    text += f"\n💰 Price: *{format_price(product['price'])}*\n"
    text += "\n🔥 *Full Indian content approx 40000+ videos - Buy now to get access*"

    keyboard = [
        [
            InlineKeyboardButton("💰 Buy Now", callback_data=f"buy_{product_id}"),
            InlineKeyboardButton("🔙 Back", callback_data="back_to_products"),
        ]
    ]

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def back_to_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    reply_markup = await build_product_keyboard(context)
    try:
        await query.edit_message_text(WELCOME_TEXT, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=WELCOME_TEXT, reply_markup=reply_markup, parse_mode="Markdown",
        )


async def show_proof_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
    await query.answer()
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
    await query.answer()
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
    await query.answer()
    context.user_data.pop("awaiting_issue", None)
    await query.edit_message_text("✅ Issue reporting cancelled.")
