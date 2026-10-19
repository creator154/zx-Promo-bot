import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import escape

import asyncpg

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "")

START_IMAGE = "https://files.catbox.moe/vg3vae.jpg"
OWNER_USERNAME = "@SumitTripathi"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

PROMO_STATE = "promo_state"

DURATIONS = {
    "24": ("24 Hours", 24),
    "48": ("48 Hours", 48),
    "72": ("72 Hours", 72),
    "permanent": ("Permanent", None),
}


# ================= DATABASE =================

async def get_pool(application):
    return application.bot_data["db"]


async def init_db(application):
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        ssl="require",
        min_size=1,
        max_size=5,
    )

    application.bot_data["db"] = pool

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zx_promo_destinations (
                chat_id BIGINT PRIMARY KEY,
                title TEXT NOT NULL,
                added_by BIGINT NOT NULL,
                added_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zx_promo_admins (
                user_id BIGINT PRIMARY KEY,
                added_by BIGINT NOT NULL,
                added_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zx_promo_messages (
                id BIGSERIAL PRIMARY KEY,
                source_user_id BIGINT NOT NULL,
                destination_chat_id BIGINT NOT NULL,
                destination_message_id BIGINT NOT NULL,
                expires_at TIMESTAMPTZ,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(destination_chat_id, destination_message_id)
            )
        """)

    logger.info("Database initialized")


async def close_db(application):
    pool = application.bot_data.get("db")
    if pool:
        await pool.close()


# ================= AUTH =================

async def is_owner(user_id):
    return user_id == OWNER_ID


async def is_authorized(application, user_id):
    if user_id == OWNER_ID:
        return True

    pool = await get_pool(application)

    async with pool.acquire() as conn:
        result = await conn.fetchval(
            "SELECT 1 FROM zx_promo_admins WHERE user_id = $1",
            user_id,
        )

    return result is not None


async def owner_only(update):
    if not update.effective_user:
        return False

    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text(
            "❌ This command is available to the owner only."
        )
        return False

    return True


async def authorized_only(update, context):
    if not update.effective_user:
        return False

    if not await is_authorized(
        context.application,
        update.effective_user.id,
    ):
        await update.effective_message.reply_text(
            "❌ You are not an authorized promo admin."
        )
        return False

    return True


# ================= START / HELP =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = (
        "🚀 <b>PROMO MANAGER</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📢 Promote Everywhere. Reach More.\n\n"
        "⚡ Fast • Reliable • Automated\n\n"
        "📚 Type /help for commands\n\n"
        "👑 <b>Powered by</b> @SumitTripathi"
    )

    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=START_IMAGE,
            caption=caption,
            parse_mode="HTML",
        )

    except TelegramError as exc:
        logger.warning("Start image failed: %s", exc)

        await update.effective_message.reply_text(
            caption,
            parse_mode="HTML",
        )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    text = (
        "📚 <b>PROMO MANAGER — HELP</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📢 <b>PROMO COMMANDS</b>\n"
        "/promo — Start promoting\n"
        "/cancel — Cancel current promo\n"
    )

    if user_id == OWNER_ID:
        text += (
            "\n👑 <b>OWNER COMMANDS</b>\n"
            "/auth USER_ID — Authorize admin\n"
            "/unauth USER_ID — Remove admin\n"
            "/admins — View authorized admins\n\n"
            "📡 <b>DESTINATION COMMANDS</b>\n"
            "/connect CHAT_ID — Connect channel/group\n"
            "/destinations — View destinations\n"
            "/disconnect CHAT_ID — Remove destination\n"
        )

    text += (
        "\n━━━━━━━━━━━━━━━━━━\n\n"
        "👑 <b>Powered by</b> @SumitTripathi"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
    )


# ================= ADMIN MANAGEMENT =================

async def auth_admin(update, context):
    if not await owner_only(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /auth USER_ID"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid user ID."
        )
        return

    if user_id <= 0:
        await update.effective_message.reply_text(
            "❌ Invalid user ID."
        )
        return

    if user_id == OWNER_ID:
        await update.effective_message.reply_text(
            "👑 Owner is already authorized."
        )
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO zx_promo_admins (user_id, added_by)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
            update.effective_user.id,
        )

    await update.effective_message.reply_text(
        f"✅ User {user_id} is now an authorized promo admin."
    )


async def unauth_admin(update, context):
    if not await owner_only(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /unauth USER_ID"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid user ID."
        )
        return

    if user_id == OWNER_ID:
        await update.effective_message.reply_text(
            "❌ The owner cannot be removed."
        )
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM zx_promo_admins WHERE user_id = $1",
            user_id,
        )

    if result == "DELETE 0":
        await update.effective_message.reply_text(
            "ℹ️ This user was not an authorized admin."
        )
    else:
        await update.effective_message.reply_text(
            f"✅ Authorization removed for {user_id}."
        )


async def list_admins(update, context):
    if not await owner_only(update):
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM zx_promo_admins ORDER BY added_at"
        )

    if not rows:
        await update.effective_message.reply_text(
            "No additional authorized admins."
        )
        return

    text = "👑 <b>AUTHORIZED ADMINS</b>\n\n"

    for row in rows:
        text += f"• <code>{row['user_id']}</code>\n"

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
    )


# ================= DESTINATIONS =================

async def check_bot_permissions(bot, chat_id):
    try:
        member = await bot.get_chat_member(chat_id, bot.id)

        if member.status != ChatMemberStatus.ADMINISTRATOR:
            return False

        chat = await bot.get_chat(chat_id)

        if chat.type == "channel":
            return (
                bool(getattr(member, "can_post_messages", False))
                and bool(getattr(member, "can_delete_messages", False))
            )

        return bool(getattr(member, "can_delete_messages", False))

    except TelegramError as exc:
        logger.warning("Permission check failed for %s: %s", chat_id, exc)
        return False


async def connect_destination(update, context):
    if not await owner_only(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /connect CHAT_ID\n\n"
            "Example: /connect -1001234567890"
        )
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid CHAT_ID."
        )
        return

    try:
        chat = await context.bot.get_chat(chat_id)

        if chat.type not in ("group", "supergroup", "channel"):
            await update.effective_message.reply_text(
                "❌ Only groups and channels can be connected."
            )
            return

        if not await check_bot_permissions(context.bot, chat_id):
            await update.effective_message.reply_text(
                "❌ Bot ko admin banao aur required permissions do.\n\n"
                "Channel: Post Messages + Delete Messages\n"
                "Group: Delete Messages"
            )
            return

        pool = await get_pool(context.application)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO zx_promo_destinations
                    (chat_id, title, added_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id)
                DO UPDATE SET title = EXCLUDED.title
                """,
                chat_id,
                chat.title or str(chat_id),
                update.effective_user.id,
            )

        await update.effective_message.reply_text(
            f"✅ Destination connected!\n\n"
            f"Name: {escape(chat.title or str(chat_id))}\n"
            f"ID: <code>{chat_id}</code>",
            parse_mode="HTML",
        )

    except TelegramError as exc:
        logger.warning("Connect failed: %s", exc)

        await update.effective_message.reply_text(
            "❌ Chat access nahi mila. Chat ID aur bot permissions check karo."
        )


async def list_destinations(update, context):
    if not await owner_only(update):
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chat_id, title
            FROM zx_promo_destinations
            ORDER BY added_at
            """
        )

    if not rows:
        await update.effective_message.reply_text(
            "No destinations connected yet."
        )
        return

    text = "📡 <b>CONNECTED DESTINATIONS</b>\n\n"

    for row in rows:
        text += (
            f"• {escape(row['title'])}\n"
            f"  <code>{row['chat_id']}</code>\n\n"
        )

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
    )


async def disconnect_destination(update, context):
    if not await owner_only(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /disconnect CHAT_ID"
        )
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid CHAT_ID."
        )
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM zx_promo_destinations WHERE chat_id = $1",
            chat_id,
        )

    if result == "DELETE 0":
        await update.effective_message.reply_text(
            "ℹ️ Destination not found."
        )
    else:
        await update.effective_message.reply_text(
            f"✅ Destination {chat_id} disconnected."
        )


# ================= PROMO FLOW =================

async def promo(update, context):
    if not await authorized_only(update, context):
        return

    context.user_data[PROMO_STATE] = {
        "step": "waiting_message",
    }

    await update.effective_message.reply_text(
        "📢 <b>Send your promo message now.</b>\n\n"
        "Text, photo, video and forwarded messages are supported.\n"
        "Forwarded messages will retain their attribution when Telegram allows it.\n\n"
        "Send /cancel to cancel.",
        parse_mode="HTML",
    )


async def cancel_promo(update, context):
    if PROMO_STATE not in context.user_data:
        await update.effective_message.reply_text(
            "No active promo process."
        )
        return

    context.user_data.pop(PROMO_STATE, None)

    await update.effective_message.reply_text(
        "❌ Promo process cancelled."
    )


async def receive_promo(update, context):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    state = context.user_data.get(PROMO_STATE)

    if not state or state.get("step") != "waiting_message":
        return

    if not await is_authorized(context.application, user.id):
        context.user_data.pop(PROMO_STATE, None)
        return

    # Ignore commands.
    if message.text and message.text.startswith("/"):
        return

    if message.has_protected_content:
        await message.reply_text(
            "❌ This message is protected and cannot be forwarded."
        )
        return

    state["source_chat_id"] = message.chat_id
    state["source_message_id"] = message.message_id
    state["step"] = "waiting_duration"

    keyboard = [
        [
            InlineKeyboardButton("24 Hours", callback_data="duration:24"),
            InlineKeyboardButton("48 Hours", callback_data="duration:48"),
        ],
        [
            InlineKeyboardButton("72 Hours", callback_data="duration:72"),
            InlineKeyboardButton("Permanent", callback_data="duration:permanent"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="duration:cancel"),
        ],
    ]

    await message.reply_text(
        "⏳ <b>Select promo duration:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def choose_duration(update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not await is_authorized(context.application, user_id):
        await query.edit_message_text("❌ Unauthorized.")
        return

    state = context.user_data.get(PROMO_STATE)

    if not state or state.get("step") != "waiting_duration":
        await query.edit_message_text(
            "⚠️ Promo process expired. Send /promo again."
        )
        return

    choice = query.data.split(":", 1)[1]

    if choice == "cancel":
        context.user_data.pop(PROMO_STATE, None)
        await query.edit_message_text("❌ Promo cancelled.")
        return

    if choice not in DURATIONS:
        await query.edit_message_text("❌ Invalid duration.")
        return

    duration_name, hours = DURATIONS[choice]

    expires_at = None

    if hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        destinations = await conn.fetch(
            """
            SELECT chat_id, title
            FROM zx_promo_destinations
            ORDER BY added_at
            """
        )

    if not destinations:
        context.user_data.pop(PROMO_STATE, None)
        await query.edit_message_text(
            "❌ No destinations connected. Ask the owner to use /connect."
        )
        return

    await query.edit_message_text(
        f"⏳
