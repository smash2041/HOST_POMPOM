"""
VIP Access Bot — Start & Product Browsing Handlers
Handles /start command, welcome media, product listing, trial videos.
"""
import asyncio
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import ContextTypes

from database import db
from utils.helpers import format_price

logger = logging.getLogger(__name__)

# ── Welcome message text (exact as specified) ──
WELCOME_TEXT = """🎉 *Welcome to VIP Access Bot!*

✨ Get exclusive access to premium content

💰 Affordable plans starting at just ₹99
✨ Cotent quality aisi ki dekhi nahi hogi 
✨ Only Premium Content
✨ Daily New Uploads
✨ Cp, Rp, Indian, Foreign, Dark 
✨ 10000+ Cp videos
✨  25000+ Rp videos 
✨  M0m S0n 5k Videos

✨ *TRY OUR ANY PLAN FOR CHECKING THE QUALITY* ✨

👇 *Choose a plan below to get started:*"""


async def build_product_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard with dynamic product buttons + optional proof."""
    products = await db.get_all_products()
    keyboard = []

    for product in products:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{product['name']} — {format_price(product['price'])}",
                    callback_data=f"product_{product['id']}",
                )
            ]
        )

    # Add proof button if enabled by owner
    proof_enabled = await db.get_setting("proof_button_enabled")
    if proof_enabled == "1":
        proof_link = await db.get_setting("proof_link") or ""
        if proof_link:
            keyboard.append(
                [InlineKeyboardButton("📸 Proof", url=proof_link)]
            )
        else:
            keyboard.append(
                [InlineKeyboardButton("📸 Proof", callback_data="show_proof")]
            )

    # Add How to Use and Report Issue
    keyboard.append([InlineKeyboardButton("❓ How to Use", callback_data="how_to_use")])
    keyboard.append([InlineKeyboardButton("🆘 Report an Issue", callback_data="report_issue")])

    return InlineKeyboardMarkup(keyboard)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle /start command.
    1. Register user in DB
    2. Send welcome video/photo (via file_id)
    3. Send welcome message with dynamic product buttons
    """
    user = update.effective_user

    # Register / update user
    await db.add_user(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
    )

    # Send welcome media
    fwd_protect = await db.get_setting("forward_protection")
    protect = fwd_protect == "1"

    media_ids_json = await db.get_setting("welcome_media_ids")
    if media_ids_json and media_ids_json != "[]":
        try:
            welcome_ids = json.loads(media_ids_json)
            if welcome_ids:
                media_group = []
                for raw_id in welcome_ids:
                    if raw_id.startswith("photo:"):
                        media_group.append(InputMediaPhoto(raw_id.replace("photo:", "", 1)))
                    elif raw_id.startswith("video:"):
                        media_group.append(InputMediaVideo(raw_id.replace("video:", "", 1)))
                    else:
                        media_group.append(InputMediaVideo(raw_id))
                
                chunk_size = 10
                for i in range(0, len(media_group), chunk_size):
                    chunk = media_group[i:i + chunk_size]
                    try:
                        await context.bot.send_media_group(
                            chat_id=update.message.chat_id, media=chunk,
                            protect_content=protect
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send welcome media group chunk: {e}")
        except Exception as e:
            logger.warning(f"Failed to parse welcome_media_ids: {e}")
    else:
        # Fallback for old single media
        media_type = await db.get_setting("welcome_media_type") or "video"
        if media_type == "video":
            video_id = await db.get_setting("welcome_video_id")
            if video_id:
                try:
                    await update.message.reply_video(video=video_id, protect_content=protect)
                except Exception:
                    pass
        else:
            photo_id = await db.get_setting("welcome_photo_id")
            if photo_id:
                try:
                    await update.message.reply_photo(photo=photo_id, protect_content=protect)
                except Exception:
                    pass

    # Send welcome message with product buttons
    reply_markup = await build_product_keyboard()
    await update.message.reply_text(
        WELCOME_TEXT, reply_markup=reply_markup, parse_mode="Markdown"
    )


async def product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle product button click.
    Shows trial videos (via file_id) + Buy Now / Back buttons.
    """
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split("_")[1])
    product = await db.get_product(product_id)

    if not product:
        await query.message.reply_text("❌ Product not found.")
        return

    # Send trial media as Telegram Albums (Media Group)
    trial_ids = product.get("trial_file_ids_list", [])
    if trial_ids:
        # Check forward protection & auto disappear settings
        fwd_protect = await db.get_setting("forward_protection")
        protect = fwd_protect == "1"
        auto_disappear = await db.get_setting("auto_disappear")
        should_disappear = auto_disappear == "1"
        sent_messages = []  # track for auto-delete

        media_group = []
        for raw_id in trial_ids:
            if not raw_id:
                continue
                
            # Determine type from prefix (backward compatible with old videos)
            if raw_id.startswith("photo:"):
                media_group.append(InputMediaPhoto(raw_id.replace("photo:", "", 1)))
            elif raw_id.startswith("video:"):
                media_group.append(InputMediaVideo(raw_id.replace("video:", "", 1)))
            else:
                # Legacy un-prefixed IDs are assumed to be videos
                media_group.append(InputMediaVideo(raw_id))

        # Telegram allows max 10 media items per group
        chunk_size = 10
        for i in range(0, len(media_group), chunk_size):
            chunk = media_group[i:i + chunk_size]
            try:
                sent_msgs = await context.bot.send_media_group(
                    chat_id=query.message.chat_id, media=chunk,
                    protect_content=protect
                )
                if should_disappear:
                    sent_messages.extend(sent_msgs)
            except Exception as e:
                logger.warning(f"Failed to send media group chunk: {e}")

        # Schedule auto-delete after 30 minutes if enabled
        if should_disappear and sent_messages:
            async def _auto_delete_trial(bot, chat_id, messages):
                """Delete trial media messages after 30 minutes."""
                await asyncio.sleep(1800)  # 30 minutes
                for msg in messages:
                    try:
                        await bot.delete_message(
                            chat_id=chat_id, message_id=msg.message_id
                        )
                    except Exception:
                        pass  # Message may already be deleted
            asyncio.create_task(
                _auto_delete_trial(
                    context.bot, query.message.chat_id, list(sent_messages)
                )
            )

    # Build buy message
    text = f"📦 *{product['name']}*\n"
    if product.get("description"):
        text += f"📝 {product['description']}\n"
    text += f"\n💰 Price: *{format_price(product['price'])}*\n"
    text += "\n🔥 *Full Indian content approx 40000+ videos - Buy now to get access*"

    keyboard = [
        [
            InlineKeyboardButton(
                "💰 Buy Now", callback_data=f"buy_{product_id}"
            ),
            InlineKeyboardButton(
                "🔙 Back", callback_data="back_to_products"
            ),
        ]
    ]

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def back_to_products_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle Back button — return to product listing."""
    query = update.callback_query
    await query.answer()

    reply_markup = await build_product_keyboard()

    try:
        await query.edit_message_text(
            WELCOME_TEXT, reply_markup=reply_markup, parse_mode="Markdown"
        )
    except Exception:
        # If edit fails (message has media), send new message
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=WELCOME_TEXT,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )


async def show_proof_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handle proof button click — show proof photo if available."""
    query = update.callback_query
    await query.answer()

    proof_photo = await db.get_setting("proof_photo_id")
    if proof_photo:
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=proof_photo,
                caption="📸 *Customer Proof*",
                parse_mode="Markdown",
            )
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="❌ Could not load proof photo.",
            )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📸 No proof available yet.",
        )


async def how_to_use_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle How to Use button click."""
    query = update.callback_query
    await query.answer()

    video_id = await db.get_setting("htu_video_id")
    text = await db.get_setting("htu_text")

    if video_id and text:
        try:
            await context.bot.send_video(
                chat_id=query.message.chat_id,
                video=video_id,
                caption=text,
                parse_mode="Markdown"
            )
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❓ *How to Use*\n\n{text}",
                parse_mode="Markdown"
            )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="❌ 'How to Use' is not configured yet."
        )

async def report_issue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Report an Issue button click."""
    query = update.callback_query
    await query.answer()

    context.user_data["awaiting_issue"] = True
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_issue")]]
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="🆘 *Report an Issue*\n\nPlease describe your issue below (you can send text or a photo).\nYour message will be sent to the support team.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def cancel_issue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel issue button."""
    query = update.callback_query
    await query.answer()
    
    context.user_data.pop("awaiting_issue", None)
    await query.edit_message_text("✅ Issue reporting cancelled.")
