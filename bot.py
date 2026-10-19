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
from telegram.error import TelegramError, BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =============== CONFIG ===============

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
START_IMAGE = "https://files.catbox.moe/vg3vae.jpg"

STATE_KEY = "promo_state"

DURATIONS = {
    "24": ("24 Hours", 24),
    "48": ("48 Hours", 48),
    "72": ("72 Hours", 72),
    "permanent": ("Permanent", None),
}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =============== DATABASE ===============

async def init_db(app):
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        ssl="require",
        min_size=1,
        max_size=5,
    )

    app.bot_data["db"] = pool

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zx_promo_admins (
                user_id BIGINT PRIMARY KEY,
                added_by BIGINT NOT NULL,
                added_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zx_promo_destinations (
                chat_id BIGINT PRIMARY KEY,
                title TEXT NOT NULL,
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

    logger.info("Database ready")


async def get_pool(app):
    return app.bot_data["db"]


async def shutdown(app):
    pool = app.bot_data.get("db")
    if pool:
        await pool.close()


# =============== ACCESS CONTROL ===============

async def is_authorized(app, user_id):
    if user_id == OWNER_ID:
        return True

    pool = await get_pool(app)

    async with pool.acquire() as conn:
        result = await conn.fetchval(
            "SELECT 1 FROM zx_promo_admins WHERE user_id=$1",
            user_id,
        )

    return result is not None


async def owner_only(update):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text(
            "❌ Owner only command."
        )
        return False

    return True


async def authorized_only(update, context):
    if not await is_authorized(
        context.application,
        update.effective_user.id,
    ):
        await update.effective_message.reply_text(
            "❌ You are not authorized to use this bot."
        )
        return False

    return True


# =============== START / HELP ===============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = (
        "<b>🚀 PROMO MANAGER</b>\n\n"
        "<b>◆━━━━━━━━━▣✦▣━━━━━━━━━◆</b>\n\n"
        "<b>• Promote Everywhere. Reach More.</b>\n\n"
        "<b>• Fast • Reliable • Automated</b>\n\n"
        "<b>• Type /help for commands</b>\n\n"
        "<b>• Powered by @SumitTripathi</b>\n\n"
        "<b>◆━━━━━━━━━▣✦▣━━━━━━━━━◆</b>"
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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>🚀 PROMO MANAGER — HELP</b>\n\n"
        "<b>◆━━━━━━━━━▣✦▣━━━━━━━━━◆</b>\n\n"
        "<b>📢 PROMO COMMANDS</b>\n"
        "<b>/promo</b> — Start a promotion\n"
        "<b>/cancel</b> — Cancel current promotion\n"
    )

    if update.effective_user.id == OWNER_ID:
        text += (
            "\n<b>👑 OWNER COMMANDS</b>\n"
            "<b>/auth USER_ID</b> — Authorize admin\n"
            "<b>/unauth USER_ID</b> — Remove admin\n"
            "<b>/admins</b> — View authorized admins\n\n"
            "<b>📡 DESTINATION COMMANDS</b>\n"
            "<b>/connect CHAT_ID</b> — Connect group/channel\n"
            "<b>/destinations</b> — View connected destinations\n"
            "<b>/disconnect CHAT_ID</b> — Remove destination\n"
        )

    text += (
        "\n<b>◆━━━━━━━━━▣✦▣━━━━━━━━━◆</b>\n\n"
        "<b>👑 Powered by @SumitTripathi</b>"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
    )

=============== ADMIN COMMANDS ===============
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
        if user_id <= 0 or user_id == OWNER_ID:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid user ID."
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
        f"✅ Authorized: {user_id}"
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
            "❌ Cannot remove the owner."
        )
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM zx_promo_admins WHERE user_id=$1",
            user_id,
        )

    await update.effective_message.reply_text(
        "✅ Admin removed." if result != "DELETE 0"
        else "ℹ️ Admin not found."
    )


async def list_admins(update, context):
    if not await owner_only(update):
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM zx_promo_admins ORDER BY added_at"
        )

    text = "👑 <b>AUTHORIZED ADMINS</b>\n\n"

    if not rows:
        text += "No additional admins."
    else:
        for row in rows:
            text += f"• <code>{row['user_id']}</code>\n"

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
    )


# =============== DESTINATIONS ===============

async def bot_has_permissions(bot, chat_id):
    try:
        member = await bot.get_chat_member(chat_id, bot.id)

        if member.status != ChatMemberStatus.ADMINISTRATOR:
            return False

        chat = await bot.get_chat(chat_id)

        if chat.type == "channel":
            return (
                bool(member.can_post_messages)
                and bool(member.can_delete_messages)
            )

        return bool(member.can_delete_messages)

    except TelegramError:
        return False


async def connect_destination(update, context):
    if not await owner_only(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /connect CHAT_ID"
        )
        return

    try:
        chat_id = int(context.args[0])
        chat = await context.bot.get_chat(chat_id)

        if chat.type not in ("group", "supergroup", "channel"):
            raise ValueError("Only groups and channels are supported.")

        if not await bot_has_permissions(context.bot, chat_id):
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
                DO UPDATE SET title=EXCLUDED.title
                """,
                chat_id,
                chat.title or str(chat_id),
                update.effective_user.id,
            )

        await update.effective_message.reply_text(
            f"✅ Connected: {escape(chat.title or str(chat_id))}",
            parse_mode="HTML",
        )

    except ValueError as exc:
        await update.effective_message.reply_text(
            f"❌ {escape(str(exc))}",
            parse_mode="HTML",
        )
    except TelegramError:
        await update.effective_message.reply_text(
            "❌ Chat nahi mila. ID aur bot permissions check karo."
        )


async def list_destinations(update, context):
    if not await owner_only(update):
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, title FROM zx_promo_destinations ORDER BY added_at"
        )

    if not rows:
        await update.effective_message.reply_text(
            "No destinations connected."
        )
        return

    text = "📡 <b>CONNECTED DESTINATIONS</b>\n\n"

    for row in rows:
        text += (
            f"• {escape(row['title'])}\n"
            f"<code>{row['chat_id']}</code>\n\n"
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
            "❌ Invalid chat ID."
        )
        return

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM zx_promo_destinations WHERE chat_id=$1",
            chat_id,
        )

    await update.effective_message.reply_text(
        "✅ Destination disconnected."
        if result != "DELETE 0"
        else "ℹ️ Destination not found."
    )


# =============== PROMO ===============

async def promo(update, context):
    if not await authorized_only(update, context):
        return

    context.user_data[STATE_KEY] = {"step": "message"}

    await update.effective_message.reply_text(
        "📢 Promo message bhejo.\n\n"
        "• Normal text normally publish hoga.\n"
        "• Channel se forwarded message ka attribution preserve hoga.\n"
        "• Photo, video aur caption bhi supported hain.\n\n"
        "Cancel karne ke liye /cancel bhejo."
    )


async def cancel_promo(update, context):
    if context.user_data.pop(STATE_KEY, None):
        await update.effective_message.reply_text(
            "❌ Promo cancelled."
        )
    else:
        await update.effective_message.reply_text(
            "Koi active promo process nahi hai."
        )


async def receive_promo(update, context):
    message = update.effective_message
    user = update.effective_user

    if not message or not user:
        return

    state = context.user_data.get(STATE_KEY)

    if not state or state.get("step") != "message":
        return

    if not await is_authorized(context.application, user.id):
        context.user_data.pop(STATE_KEY, None)
        return

    if message.has_protected_content:
        await message.reply_text(
            "❌ Protected content forward nahi ho sakta."
        )
        return

    if not (
        message.text
        or message.photo
        or message.video
        or message.animation
        or message.document
        or message.audio
        or message.voice
        or message.video_note
        or message.sticker
    ):
        await message.reply_text(
            "❌ Is message type ko support nahi kiya gaya."
        )
        return

    state["source_chat_id"] = message.chat_id
    state["source_message_id"] = message.message_id
    state["source_is_forwarded"] = message.forward_origin is not None
    state["step"] = "duration"

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
        "⏳ Promo duration select karo:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def publish_message(context, source_chat_id, source_message_id,
                          destination_chat_id, is_forwarded):
    """
    Forwarded message:
        Telegram forwarding API use karo, attribution preserve karne ke liye.

    Normal message:
        Copy API use karo, taaki Forwarded from na aaye.
    """
    if is_forwarded:
        return await context.bot.forward_message(
            chat_id=destination_chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
        )

    return await context.bot.copy_message(
        chat_id=destination_chat_id,
        from_chat_id=source_chat_id,
        message_id=source_message_id,
    )


async def choose_duration(update, context):
    query = update.callback_query
    await query.answer()

    if not await is_authorized(
        context.application,
        query.from_user.id,
    ):
        await query.edit_message_text("❌ Unauthorized.")
        return

    state = context.user_data.get(STATE_KEY)

    if not state or state.get("step") != "duration":
        await query.edit_message_text(
            "⚠️ Session expired. /promo dobara bhejo."
        )
        return

    choice = query.data.split(":", 1)[1]

    if choice == "cancel":
        context.user_data.pop(STATE_KEY, None)
        await query.edit_message_text("❌ Promo cancelled.")
        return

    if choice not in DURATIONS:
        await query.edit_message_text("❌ Invalid duration.")
        return

    duration_name, hours = DURATIONS[choice]

    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=hours)
        if hours is not None
        else None
    )

    pool = await get_pool(context.application)

    async with pool.acquire() as conn:
        destinations = await conn.fetch(
            "SELECT chat_id, title FROM zx_promo_destinations ORDER BY added_at"
        )

    if not destinations:
        context.user_data.pop(STATE_KEY, None)
        await query.edit_message_text(
            "❌ Koi destination connected nahi hai."
        )
        return

    await query.edit_message_text(
        f"⏳ Publishing...\nDuration: {duration_name}"
    )

    success = 0
    failed = []
    source_chat_id = state["source_chat_id"]
    source_message_id = state["source_message_id"]
    is_forwarded = state["source_is_forwarded"]

    for destination in destinations:
        chat_id = destination["chat_id"]
        title = destination["title"]

        try:
            if not await bot_has_permissions(context.bot, chat_id):
                failed.append(f"{title}: missing permissions")
                continue

            sent = await publish_message(
                context,
                source_chat_id,
                source_message_id,
                chat_id,
                is_forwarded,
            )

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO zx_promo_messages
                    (
                        source_user_id,
                        destination_chat_id,
                        destination_message_id,
                        expires_at,
                        status
                    )
                    VALUES ($1, $2, $3, $4, 'active')
                    ON CONFLICT (destination_chat_id, destination_message_id)
                    DO NOTHING
                    """,
                    query.from_user.id,
                    chat_id,
                    sent.message_id,
                    expires_at,
                )

            success += 1

        except (TelegramError, asyncpg.PostgresError) as exc:
            logger.warning("Publishing to %s failed: %s", chat_id, exc)
            failed.append(f"{title}: {str(exc)[:100]}")

        await asyncio.sleep(0.1)

    context.user_data.pop(STATE_KEY, None)

    report = (
        "📢 <b>PROMO REPORT</b>\n\n"
        f"✅ Published: {success}\n"
        f"❌ Failed: {len(failed)}\n"
        f"⏳ Duration: {duration_name}\n"
    )

    if expires_at:
        report += (
            "🗓 Expires: "
            f"{expires_at.strftime('%d-%m-%Y %H:%M UTC')}\n"
        )
    else:
        report += "♾ Auto-delete: Off\n"

    if failed:
        report += "\n<b>Failed destinations:</b>\n"
        for item in failed[:15]:
            report += f"• {escape(item)}\n"

        if len(failed) > 15:
            report += f"• ...and {len(failed) - 15} more\n"

    try:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=report,
            parse_mode="HTML",
        )
    except TelegramError:
        logger.exception("Could not send promo report")


# =============== AUTO DELETE ===============

async def delete_expired_promos(context):
    pool = await get_pool(context.application)
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, destination_chat_id, destination_message_id
            FROM zx_promo_messages
            WHERE status='active'
              AND expires_at IS NOT NULL
              AND expires_at <= $1
            ORDER BY expires_at
            LIMIT 100
            """,
            now,
        )

    for row in rows:
        status = "active"

        try:
            await context.bot.delete_message(
                chat_id=row["destination_chat_id"],
                message_id=row["destination_message_id"],
            )
            status = "deleted"

        except BadRequest as exc:
            if "message to delete not found" in str(exc).lower():
                status = "deleted"
            else:
                logger.warning("Delete failed: %s", exc)

        except (Forbidden, TelegramError) as exc:
            logger.warning("Delete failed: %s", exc)

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE zx_promo_messages SET status=$1 WHERE id=$2",
                status,
                row["id"],
            )

        await asyncio.sleep(0.1)


# =============== ERRORS ===============

async def error_handler(update, context):
    logger.error(
        "Unhandled exception",
        exc_info=context.error,
    )


# =============== STARTUP ===============

async def post_init(app):
    await init_db(app)

    app.job_queue.run_repeating(
        delete_expired_promos,
        interval=60,
        first=10,
        name="delete_expired_promos",
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing in Config Vars")

    if not OWNER_ID:
        raise RuntimeError("OWNER_ID missing in Config Vars")

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing in Config Vars")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("promo", promo))
    app.add_handler(CommandHandler("cancel", cancel_promo))

    app.add_handler(CommandHandler("auth", auth_admin))
    app.add_handler(CommandHandler("unauth", unauth_admin))
    app.add_handler(CommandHandler("admins", list_admins))

    app.add_handler(CommandHandler("connect", connect_destination))
    app.add_handler(CommandHandler("destinations", list_destinations))
    app.add_handler(CommandHandler("disconnect", disconnect_destination))

    app.add_handler(
        CallbackQueryHandler(
            choose_duration,
            pattern=r"^duration:",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            receive_promo,
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Promo Manager started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
