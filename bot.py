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
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
LOGGER_GROUP_ID = int(os.getenv("LOGGER_GROUP_ID", "0"))

if not BOT_TOKEN or not DATABASE_URL or not OWNER_ID:
    raise RuntimeError(
        "BOT_TOKEN, OWNER_ID and DATABASE_URL must be configured."
    )


# ================= LOGGING =================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

db = None

DURATIONS = {
    "24": ("24 Hours", 24),
    "48": ("48 Hours", 48),
    "72": ("72 Hours", 72),
    "permanent": ("Permanent", None),
}


# ================= DATABASE =================

async def init_db():
    global db

    db = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=60,
    )

    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zx_promo_admins (
                user_id BIGINT PRIMARY KEY,
                added_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zx_promo_destinations (
                chat_id BIGINT PRIMARY KEY,
                chat_title TEXT,
                chat_link TEXT,
                added_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zx_promo_messages (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                expires_at TIMESTAMPTZ,
                deleted BOOLEAN DEFAULT FALSE,
                delete_attempts INTEGER DEFAULT 0,
                last_delete_error TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            ALTER TABLE zx_promo_messages
            ADD COLUMN IF NOT EXISTS delete_attempts INTEGER DEFAULT 0
        """)

        await conn.execute("""
            ALTER TABLE zx_promo_messages
            ADD COLUMN IF NOT EXISTS last_delete_error TEXT
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS
            zx_promo_messages_expiry_idx
            ON zx_promo_messages (expires_at)
            WHERE deleted = FALSE
        """)

    logger.info("DATABASE CONNECTED")


# ================= LOGGER =================

async def send_log(context, message):
    if not LOGGER_GROUP_ID:
        return

    try:
        await context.bot.send_message(
            chat_id=LOGGER_GROUP_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("LOGGER GROUP MESSAGE FAILED")


async def log_chat_event(context, title, chat_id, chat_title, chat_link):
    await send_log(
        context,
        (
            f"<b>{escape(title)}</b>\n\n"
            f"Name: {escape(str(chat_title))}\n"
            f"Chat ID: <code>{chat_id}</code>\n"
            f"Link: {escape(str(chat_link or 'Private'))}"
        ),
    )


# ================= ACCESS CONTROL =================

async def is_admin(user_id):
    if user_id == OWNER_ID:
        return True

    async with db.acquire() as conn:
        result = await conn.fetchval(
            "SELECT 1 FROM zx_promo_admins WHERE user_id = $1",
            user_id,
        )

    return bool(result)


async def owner_only(update):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text(
            "❌ This command is only for the bot owner."
        )
        return False

    return True


# ================= START =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🚀 <b>PROMO MANAGER</b>\n"
        "◆━━━━━━━━━▣✦▣━━━━━━━━━◆\n\n"
        "Promote Everywhere. Reach More.\n\n"
        "Fast • Reliable • Automated\n\n"
        "Type /help for commands\n\n"
        "Powered by @SumitTripathi"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# ================= HELP =================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>📚 PROMO MANAGER HELP</b>\n\n"
        "<b>Promo Commands</b>\n"
        "/promo - Send a new promotion\n"
        "/cancel - Cancel current operation\n\n"
        "<b>Owner Commands</b>\n"
        "/auth - Add an admin\n"
        "/unauth - Remove an admin\n"
        "/admins - List admins\n"
        "/connect - Connect a group/channel\n"
        "/destinations - List connected chats\n"
        "/disconnect - Disconnect a chat\n\n"
        "<b>Duration Options</b>\n"
        "24 Hours\n"
        "48 Hours\n"
        "72 Hours\n"
        "Permanent\n\n"
        "Powered by @SumitTripathi"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# ================= AUTH =================

async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /auth USER_ID"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO zx_promo_admins (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )

    await update.message.reply_text(
        f"✅ Admin added: <code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def unauth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /unauth USER_ID"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    async with db.acquire() as conn:
        await conn.execute(
            "DELETE FROM zx_promo_admins WHERE user_id = $1",
            user_id,
        )

    await update.message.reply_text(
        f"✅ Admin removed: <code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return

    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM zx_promo_admins ORDER BY user_id"
        )

    text = "<b>👮 ADMIN LIST</b>\n\n"
    text += f"Owner: <code>{OWNER_ID}</code>\n"

    for row in rows:
        text += f"• <code>{row['user_id']}</code>\n"

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# ================= CONNECT =================

async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return

    await update.message.reply_text(
        "Add the bot to your group/channel and make it an admin.\n"
        "Then send /connect in that group/channel."
    )


async def connect_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type == "private":
        return

    try:
        member = await context.bot.get_chat_member(
            chat.id,
            context.bot.id,
        )

        if member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "❌ Please make the bot an administrator first."
            )
            return

        invite_link = None

        try:
            invite_link = await context.bot.export_chat_invite_link(
                chat.id
            )
        except TelegramError:
            pass

        async with db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO zx_promo_destinations
                (chat_id, chat_title, chat_link)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id)
                DO UPDATE SET
                    chat_title = EXCLUDED.chat_title,
                    chat_link = EXCLUDED.chat_link
                """,
                chat.id,
                chat.title or "Unknown",
                invite_link,
            )

        await update.message.reply_text(
            "✅ This chat has been connected successfully."
        )

        await log_chat_event(
            context,
            "NEW DESTINATION CONNECTED",
            chat.id,
            chat.title or "Unknown",
            invite_link,
        )

    except Exception:
        logger.exception("CONNECT FAILED")
        await update.message.reply_text(
            "❌ Could not connect this chat."
        )


# ================= DESTINATIONS =================

async def destinations(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_only(update):
        return

    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chat_id, chat_title, chat_link
            FROM zx_promo_destinations
            ORDER BY added_at DESC
            """
        )

    if not rows:
        await update.message.reply_text(
            "No destinations connected."
        )
        return

    text = "<b>📢 CONNECTED DESTINATIONS</b>\n\n"

    for row in rows:
        text += (
            f"<b>{escape(row['chat_title'] or 'Unknown')}</b>\n"
            f"ID: <code>{row['chat_id']}</code>\n"
            f"Link: {escape(row['chat_link'] or 'Private')}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ================= DISCONNECT =================

async def disconnect(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await owner_only(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /disconnect CHAT_ID"
        )
        return

    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid chat ID.")
        return

    async with db.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM zx_promo_destinations
            WHERE chat_id = $1
            """,
            chat_id,
        )

    if result == "DELETE 0":
        await update.message.reply_text(
            "This destination was not found."
        )
        return

    await update.message.reply_text(
        f"✅ Disconnected: <code>{chat_id}</code>",
        parse_mode=ParseMode.HTML,
    )

    await send_log(
        context,
        f"❌ <b>DESTINATION DISCONNECTED</b>\n"
        f"Chat ID: <code>{chat_id}</code>",
    )


# ================= PROMO =================

async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ You are not authorized to use this command."
        )
        return

    context.user_data["promo_state"] = "waiting_message"

    await update.message.reply_text(
        "📩 Send the promotion message now.\n\n"
        "You can send text, photo, video or a forwarded message.\n"
        "Use /cancel to stop."
    )


async def receive_promo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if context.user_data.get("promo_state") != "waiting_message":
        return

    message = update.effective_message

    if message.text and message.text.startswith("/"):
        return

    context.user_data["promo_message"] = message
    context.user_data["promo_state"] = "waiting_duration"

    keyboard = [
        [
            InlineKeyboardButton(
                "24 Hours",
                callback_data="duration:24",
            ),
            InlineKeyboardButton(
                "48 Hours",
                callback_data="duration:48",
            ),
        ],
        [
            InlineKeyboardButton(
                "72 Hours",
                callback_data="duration:72",
            ),
            InlineKeyboardButton(
                "Permanent",
                callback_data="duration:permanent",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="duration:cancel",
            ),
        ],
    ]

    await message.reply_text(
        "⏳ Select promotion duration:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def duration_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data.split(":", 1)[1]

    if data == "cancel":
        context.user_data.clear()
        await query.edit_message_text(
            "❌ Promotion cancelled."
        )
        return

    if context.user_data.get("promo_state") != "waiting_duration":
        await query.edit_message_text(
            "This promo session has expired. Use /promo again."
        )
        return

    if data not in DURATIONS:
        await query.edit_message_text(
            "Invalid duration selected."
        )
        return

    promo_message = context.user_data.get("promo_message")

    if not promo_message:
        context.user_data.clear()
        await query.edit_message_text(
            "Promo message not found. Use /promo again."
        )
        return

    label, hours = DURATIONS[data]

    expires_at = None

    if hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=hours
        )

    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, chat_title FROM zx_promo_destinations"
        )

    if not rows:
        context.user_data.clear()
        await query.edit_message_text(
            "❌ No connected destinations found."
        )
        return

    await query.edit_message_text(
        f"📤 Publishing promotion...\nDuration: {label}"
    )

    success = 0
    failed = 0

    for row in rows:
        chat_id = row["chat_id"]

        try:
            sent = await promo_message.copy(chat_id=chat_id)

            async with db.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO zx_promo_messages
                    (chat_id, message_id, expires_at)
                    VALUES ($1, $2, $3)
                    """,
                    chat_id,
                    sent.message_id,
                    expires_at,
                )

            success += 1

        except Exception:
            failed += 1
            logger.exception(
                "PROMO SEND FAILED FOR CHAT %s",
                chat_id,
            )

    context.user_data.clear()

    await query.message.reply_text(
        "✅ <b>PROMO PUBLISHED</b>\n\n"
        f"Duration: {escape(label)}\n"
        f"Successful: {success}\n"
        f"Failed: {failed}\n\n"
        "Powered by @SumitTripathi",
        parse_mode=ParseMode.HTML,
    )

    await send_log(
        context,
        (
            "📢 <b>PROMO PUBLISHED</b>\n\n"
            f"Duration: {escape(label)}\n"
            f"Successful: {success}\n"
            f"Failed: {failed}\n"
            f"By: <code>{update.effective_user.id}</code>"
        ),
    )


# ================= CANCEL =================

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Current operation cancelled."
    )


# ================= AUTO DELETE =================

async def delete_expired_promos(
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.info("AUTO DELETE JOB RUNNING")

    try:
        async with db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, chat_id, message_id, delete_attempts
                FROM zx_promo_messages
                WHERE deleted = FALSE
                  AND expires_at IS NOT NULL
                  AND expires_at <= NOW()
                ORDER BY expires_at ASC
                LIMIT 100
                """
            )

        if not rows:
            logger.info("NO EXPIRED PROMOS FOUND")
            return

        for row in rows:
            promo_id = row["id"]
            chat_id = row["chat_id"]
            message_id = row["message_id"]

            try:
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=message_id,
                )

                async with db.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE zx_promo_messages
                        SET deleted = TRUE,
                            last_delete_error = NULL
                        WHERE id = $1
                        """,
                        promo_id,
                    )

                logger.info(
                    "AUTO DELETE SUCCESS: %s",
                    promo_id,
                )

            except BadRequest as e:
                error_text = str(e)

                if (
                    "message to delete not found" in error_text.lower()
                    or "message_id_invalid" in error_text.lower()
                ):
                    async with db.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE zx_promo_messages
                            SET deleted = TRUE,
                                last_delete_error = NULL
                            WHERE id = $1
                            """,
                            promo_id,
                        )

                    logger.info(
                        "MESSAGE ALREADY DELETED: %s",
                        promo_id,
                    )

                else:
                    async with db.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE zx_promo_messages
                            SET delete_attempts = delete_attempts + 1,
                                last_delete_error = $2
                            WHERE id = $1
                            """,
                            promo_id,
                            error_text[:1000],
                        )

                    logger.error(
                        "AUTO DELETE FAILED: %s - %s",
                        promo_id,
                        error_text,
                    )

            except TelegramError as e:
                async with db.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE zx_promo_messages
                        SET delete_attempts = delete_attempts + 1,
                            last_delete_error = $2
                        WHERE id = $1
                        """,
                        promo_id,
                        str(e)[:1000],
                    )

                logger.error(
                    "AUTO DELETE TELEGRAM ERROR: %s - %s",
                    promo_id,
                    e,
                )

            except Exception as e:
                async with db.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE zx_promo_messages
                        SET delete_attempts = delete_attempts + 1,
                            last_delete_error = $2
                        WHERE id = $1
                        """,
                        promo_id,
                        str(e)[:1000],
                    )

                logger.exception(
                    "AUTO DELETE UNKNOWN ERROR: %s",
                    promo_id,
                )

    except Exception:
        logger.exception("AUTO DELETE JOB ERROR")


# ================= POST INIT =================

async def post_init(application: Application):
    await init_db()

    application.job_queue.run_repeating(
        delete_expired_promos,
        interval=30,
        first=10,
        name="auto_delete_promos",
    )

    logger.info("AUTO DELETE JOB STARTED")


# ================= MAIN =================

def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    application.add_handler(CommandHandler("auth", auth))
    application.add_handler(CommandHandler("unauth", unauth))
    application.add_handler(CommandHandler("admins", admins))

    application.add_handler(CommandHandler("connect", connect))
    application.add_handler(CommandHandler("destinations", destinations))
    application.add_handler(CommandHandler("disconnect", disconnect))

    application.add_handler(CommandHandler("promo", promo))
    application.add_handler(CommandHandler("cancel", cancel))

    application.add_handler(
        CallbackQueryHandler(
            duration_callback,
            pattern=r"^duration:",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            receive_promo,
        )
    )

    logger.info("PROMO MANAGER STARTED")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
