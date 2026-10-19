import os
import logging
import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DATABASE_URL = os.environ["DATABASE_URL"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = None

DURATIONS = {
    "24h": ("24 Hours", 24),
    "48h": ("48 Hours", 48),
    "72h": ("72 Hours", 72),
    "permanent": ("Permanent", None),
}


async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

    async with db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS destinations (
                chat_id BIGINT PRIMARY KEY,
                title TEXT,
                added_by BIGINT,
                added_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS promos (
                id BIGSERIAL PRIMARY KEY,
                source_chat_id BIGINT,
                source_message_id BIGINT,
                destination_chat_id BIGINT,
                destination_message_id BIGINT,
                expires_at TIMESTAMPTZ,
                status TEXT DEFAULT 'active'
            )
        """)


async def is_owner(user_id):
    return user_id == OWNER_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 ZX Promo Bot\n\n"
        "Commands:\n"
        "/connect - Channel/group connect karein\n"
        "/destinations - Connected channels dekhein\n"
        "/disconnect CHAT_ID - Channel disconnect karein\n"
        "/promote - Promo bhejne ka process start karein\n\n"
        "Owner: Authorized promotion manager"
    )


async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        await update.message.reply_text(
            "❌ Sirf bot owner destinations manage kar sakta hai."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/connect CHAT_ID\n\n"
            "Bot ko pehle channel/group mein admin banayein."
        )
        return

    try:
        chat_id = int(context.args[0])
        chat = await context.bot.get_chat(chat_id)
        me = await context.bot.get_chat_member(chat_id, context.bot.id)

        if chat.type == "channel":
            permitted = getattr(me, "can_post_messages", False)
        elif chat.type in ("group", "supergroup"):
            permitted = getattr(me, "can_delete_messages", False)
            if not permitted:
                permitted = getattr(me, "can_manage_chat", False)
        else:
            await update.message.reply_text(
                "❌ Sirf channels aur groups connect ho sakte hain."
            )
            return

        if not permitted:
            await update.message.reply_text(
                "❌ Bot ko required admin permissions nahi mili hain."
            )
            return

        async with db.acquire() as conn:
            await conn.execute("""
                INSERT INTO destinations (chat_id, title, added_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id)
                DO UPDATE SET title = EXCLUDED.title,
                              added_by = EXCLUDED.added_by
            """, chat_id, chat.title or str(chat_id), update.effective_user.id)

        await update.message.reply_text(
            f"✅ Connected!\n{chat.title}\nID: {chat_id}"
        )

    except (ValueError, TelegramError) as e:
        await update.message.reply_text(f"❌ Connect nahi hua: {e}")


async def destinations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return

    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, title FROM destinations ORDER BY added_at"
        )

    if not rows:
        await update.message.reply_text("Abhi koi destination connected nahi hai.")
        return

    text = "📢 Connected destinations:\n\n"
    for row in rows:
        text += f"• {row['title']}\nID: {row['chat_id']}\n\n"

    await update.message.reply_text(text)


async def disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /disconnect CHAT_ID"
        )
        return

    try:
        chat_id = int(context.args[0])
        async with db.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM destinations WHERE chat_id = $1",
                chat_id,
            )

        if result.endswith("1"):
            await update.message.reply_text("✅ Destination disconnected.")
        else:
            await update.message.reply_text("❌ Destination nahi mila.")

    except ValueError:
        await update.message.reply_text("❌ Valid numeric chat ID dein.")


async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Access denied.")
        return

    context.user_data["awaiting_promo"] = True

    await update.message.reply_text(
        "📨 Ab apna promotional message bhejo.\n\n"
        "Text, photo, video ya doosra supported Telegram message bhej sakte ho."
    )


async def receive_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update.effective_user.id):
        return

    if not context.user_data.get("awaiting_promo"):
        return

    context.user_data["awaiting_promo"] = False

    msg = update.effective_message

    context.user_data["promo_chat_id"] = msg.chat_id
    context.user_data["promo_message_id"] = msg.message_id

    keyboard = [
        [
            InlineKeyboardButton("24 Hours", callback_data="duration:24h"),
            InlineKeyboardButton("48 Hours", callback_data="duration:48h"),
        ],
        [
            InlineKeyboardButton("72 Hours", callback_data="duration:72h"),
            InlineKeyboardButton("Permanent", callback_data="duration:permanent"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="duration:cancel")
        ],
    ]

    await msg.reply_text(
        "⏱️ Promo kitne time tak rakhna hai?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def choose_duration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if not await is_owner(query.from_user.id):
        await query.edit_message_text("❌ Access denied.")
        return

    choice = query.data.split(":", 1)[1]

    if choice == "cancel":
        context.user_data.pop("promo_chat_id", None)
        context.user_data.pop("promo_message_id", None)
        await query.edit_message_text("❌ Promo cancelled.")
        return

    if choice not in DURATIONS:
        await query.edit_message_text("❌ Invalid duration.")
        return

    source_chat = context.user_data.get("promo_chat_id")
    source_message = context.user_data.get("promo_message_id")

    if not source_chat or not source_message:
        await query.edit_message_text(
            "❌ Promo message nahi mila. /promote se dobara shuru karein."
        )
        return

    label, hours = DURATIONS[choice]

    expires_at = None
    if hours is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    async with db.acquire() as conn:
        destinations_rows = await conn.fetch(
            "SELECT chat_id, title FROM destinations ORDER BY added_at"
        )

    if not destinations_rows:
        await query.edit_message_text(
            "❌ Koi destination connected nahi hai. Pehle /connect karein."
        )
        return

    await query.edit_message_text(
        f"⏳ Promo publish ho raha hai...\nDuration: {label}"
    )

    success = 0
    failed = 0

    for destination in destinations_rows:
        chat_id = destination["chat_id"]

        try:
            copied = await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=source_chat,
                message_id=source_message,
            )

            async with db.acquire() as conn:
                await conn.execute("""
                    INSERT INTO promos (
                        source_chat_id,
                        source_message_id,
                        destination_chat_id,
                        destination_message_id,
                        expires_at,
                        status
                    )
                    VALUES ($1, $2, $3, $4, $5, 'active')
                """,
                    source_chat,
                    source_message,
                    chat_id,
                    copied.message_id,
                    expires_at,
                )

            success += 1

        except TelegramError as e:
            failed += 1
            logger.warning(
                "Promo failed for destination %s: %s",
                chat_id, e
            )

        await asyncio.sleep(0.05)

    context.user_data.pop("promo_chat_id", None)
    context.user_data.pop("promo_message_id", None)

    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=(
            f"📊 PROMO REPORT\n\n"
            f"⏱️ Duration: {label}\n"
            f"✅ Published: {success}\n"
            f"❌ Failed: {failed}\n"
            f"🗑️ Auto-delete: "
            f"{'Enabled' if expires_at else 'Disabled'}"
        ),
    )


async def delete_expired_promos(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone.utc)

    async with db.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, destination_chat_id, destination_message_id
            FROM promos
            WHERE status = 'active'
              AND expires_at IS NOT NULL
              AND expires_at <= $1
            ORDER BY expires_at
            LIMIT 100
        """, now)

    for row in rows:
        promo_id = row["id"]
        chat_id = row["destination_chat_id"]
        message_id = row["destination_message_id"]

        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=message_id,
            )
            status = "deleted"

        except TelegramError as e:
            logger.warning(
                "Delete failed for promo %s in %s: %s",
                promo_id, chat_id, e
            )
            status = "delete_failed"

        async with db.acquire() as conn:
            await conn.execute(
                "UPDATE promos SET status = $1 WHERE id = $2",
                status,
                promo_id,
            )

        await asyncio.sleep(0.05)


async def post_init(application: Application):
    await init_db()

    # Recover expiry jobs from the database after restart/redeploy.
    application.job_queue.run_repeating(
        delete_expired_promos,
        interval=30,
        first=5,
        name="promo_expiry_worker",
    )

    logger.info("ZX Promo Bot started.")


def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("connect", connect))
    application.add_handler(CommandHandler("destinations", destinations))
    application.add_handler(CommandHandler("disconnect", disconnect))
    application.add_handler(CommandHandler("promote", promote))

    application.add_handler(
        CallbackQueryHandler(
            choose_duration,
            pattern=r"^duration:"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            receive_promo,
        )
    )

    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
