import asyncio
import logging
import os
from datetime import datetime, timezone
from threading import Thread

import psycopg
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Conflict, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =============================================================================
# XAVIER BOT — CLEAN CORE
# =============================================================================
# Security principles:
# - No bot token in source code.
# - Exactly one owner, supplied by OWNER_ID.
# - No forced-channel join logic.
# - No local JSON persistence on Render.
# - PostgreSQL is mandatory so redeploys cannot erase users.
# =============================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = os.environ.get("OWNER_ID", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PORT = int(os.environ.get("PORT", "10000"))

OWNER_ID_INT = int(OWNER_ID) if OWNER_ID.isdigit() else 0


def validate_config():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is required")
    if not OWNER_ID.isdigit():
        raise RuntimeError("OWNER_ID must be a numeric Telegram user ID")
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required; Xavier will not use ephemeral local storage"
        )

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("xavier")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

web = Flask(__name__)


@web.get("/")
def health():
    return {"service": "Xavier Bot", "status": "ok"}, 200


def run_web():
    web.run(host="0.0.0.0", port=PORT)


def start_web():
    Thread(target=run_web, daemon=True).start()


# =============================================================================
# DATABASE
# =============================================================================

def db():
    return psycopg.connect(DATABASE_URL)


def init_db():
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                blocked_by_bot BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id BIGINT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relay_map (
                owner_message_id BIGINT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_runs (
                id BIGSERIAL PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                total INTEGER NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_active ON users(active)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_relay_created ON relay_map(created_at)"
        )
        conn.commit()


def upsert_user(user):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, first_name, username, first_seen, last_seen, active, blocked_by_bot)
            VALUES (%s, %s, %s, NOW(), NOW(), TRUE, FALSE)
            ON CONFLICT (user_id) DO UPDATE SET
                first_name = EXCLUDED.first_name,
                username = EXCLUDED.username,
                last_seen = NOW(),
                active = TRUE,
                blocked_by_bot = FALSE
            """,
            (user.id, user.first_name, user.username),
        )
        conn.commit()


def is_banned(user_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM banned_users WHERE user_id = %s",
            (user_id,),
        ).fetchone()
        return bool(row)


def set_banned(user_id: int, banned: bool):
    with db() as conn:
        if banned:
            conn.execute(
                "INSERT INTO banned_users(user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (user_id,),
            )
        else:
            conn.execute("DELETE FROM banned_users WHERE user_id = %s", (user_id,))
        conn.commit()


def active_user_ids():
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM users WHERE active = TRUE ORDER BY user_id"
        ).fetchall()
        return [int(r[0]) for r in rows]


def mark_unreachable(user_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE users SET active = FALSE, blocked_by_bot = TRUE WHERE user_id = %s",
            (user_id,),
        )
        conn.commit()


def stats():
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM users WHERE active = TRUE"
        ).fetchone()[0]
        blocked = conn.execute(
            "SELECT COUNT(*) FROM users WHERE blocked_by_bot = TRUE"
        ).fetchone()[0]
        banned = conn.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0]
        return int(total), int(active), int(blocked), int(banned)


def save_relay(owner_message_id: int, user_id: int):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO relay_map(owner_message_id, user_id, created_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (owner_message_id)
            DO UPDATE SET user_id = EXCLUDED.user_id, created_at = NOW()
            """,
            (owner_message_id, user_id),
        )
        conn.execute(
            "DELETE FROM relay_map WHERE created_at < NOW() - INTERVAL '30 days'"
        )
        conn.commit()


def relay_target(owner_message_id: int):
    with db() as conn:
        row = conn.execute(
            "SELECT user_id FROM relay_map WHERE owner_message_id = %s",
            (owner_message_id,),
        ).fetchone()
        return int(row[0]) if row else None


def create_broadcast_run(total: int) -> int:
    with db() as conn:
        row = conn.execute(
            "INSERT INTO broadcast_runs(total) VALUES (%s) RETURNING id",
            (total,),
        ).fetchone()
        conn.commit()
        return int(row[0])


def finish_broadcast_run(run_id: int, sent: int, blocked: int, failed: int):
    with db() as conn:
        conn.execute(
            """
            UPDATE broadcast_runs
            SET finished_at = NOW(), sent = %s, blocked = %s, failed = %s
            WHERE id = %s
            """,
            (sent, blocked, failed, run_id),
        )
        conn.commit()


# =============================================================================
# AUTH / UI
# =============================================================================

def is_owner(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == OWNER_ID_INT)


async def owner_only(update: Update) -> bool:
    if is_owner(update):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ غير مصرح.")
    return False


def admin_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 الإحصائيات", callback_data="stats")],
            [InlineKeyboardButton("📢 طريقة البث", callback_data="broadcast_help")],
        ]
    )


async def show_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return
    await update.effective_message.reply_text(
        "🛡️ Xavier Control\n\n"
        "أنت المالك الوحيد المصرح له حاليًا.\n"
        "لا يوجد Force Join أو قناة إجبارية في هذه النسخة.",
        reply_markup=admin_keyboard(),
    )


# =============================================================================
# USER FLOW
# =============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update):
        await show_admin(update, context)
        return

    user = update.effective_user
    if is_banned(user.id):
        return

    upsert_user(user)
    await update.effective_message.reply_text(
        "👋 أهلاً بك في Xavier.\n"
        "أرسل رسالتك أو صورتك أو الملف، وسيتم تحويله للإدارة."
    )


async def handle_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message

    if not user or not msg or user.id == OWNER_ID_INT:
        return
    if is_banned(user.id):
        return

    upsert_user(user)

    try:
        await msg.reply_text("✅ تم استلام رسالتك.")
    except TelegramError:
        pass

    try:
        header = await context.bot.send_message(
            chat_id=OWNER_ID_INT,
            text=(
                "📩 رسالة جديدة\n"
                f"👤 {user.first_name or '-'}\n"
                f"🆔 {user.id}\n"
                f"🔗 @{user.username}" if user.username else
                "📩 رسالة جديدة\n"
                f"👤 {user.first_name or '-'}\n"
                f"🆔 {user.id}"
            ),
        )
        forwarded = await msg.forward(chat_id=OWNER_ID_INT)
        save_relay(forwarded.message_id, user.id)
        save_relay(header.message_id, user.id)
    except TelegramError as exc:
        logger.error("Failed forwarding user %s to owner: %s", user.id, exc)


async def handle_owner_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return

    msg = update.effective_message
    if not msg:
        return

    # Commands and #broadcast are handled earlier.
    if msg.text and (msg.text.startswith("/") or msg.text.lower().startswith("#broadcast")):
        return

    if msg.reply_to_message:
        target = relay_target(msg.reply_to_message.message_id)
        if target:
            try:
                await msg.copy(chat_id=target)
                try:
                    await msg.set_reaction("👍")
                except TelegramError:
                    pass
            except Forbidden:
                mark_unreachable(target)
                await msg.reply_text("❌ المستخدم حظر البوت أو لم يعد متاحًا.")
            except TelegramError as exc:
                await msg.reply_text(f"❌ فشل الإرسال: {exc}")
            return

    await msg.reply_text(
        "ℹ️ للرد على طالب: اعمل Reply على الرسالة المحولة منه.\n"
        "للبث: اعمل Reply على الرسالة المطلوبة واكتب /broadcast أو #broadcast."
    )


# =============================================================================
# BROADCAST
# =============================================================================

def retry_seconds(exc: RetryAfter) -> float:
    value = exc.retry_after
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    return float(value)


async def perform_broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    source_message=None,
    text_message=None,
):
    if not await owner_only(update):
        return

    recipients = active_user_ids()
    total = len(recipients)
    if total == 0:
        await update.effective_message.reply_text("⚠️ لا يوجد مستخدمون نشطون في قاعدة البيانات.")
        return

    status_msg = await update.effective_message.reply_text(
        f"📢 بدء البث إلى {total} مستخدم..."
    )

    run_id = create_broadcast_run(total)
    sent = 0
    blocked = 0
    failed = 0

    for index, user_id in enumerate(recipients, start=1):
        try:
            if source_message is not None:
                await source_message.copy(chat_id=user_id)
            else:
                await context.bot.send_message(chat_id=user_id, text=text_message)

            sent += 1

        except RetryAfter as exc:
            await asyncio.sleep(retry_seconds(exc) + 0.5)
            try:
                if source_message is not None:
                    await source_message.copy(chat_id=user_id)
                else:
                    await context.bot.send_message(chat_id=user_id, text=text_message)
                sent += 1
            except Forbidden:
                blocked += 1
                mark_unreachable(user_id)
            except TelegramError as retry_exc:
                failed += 1
                logger.warning("Broadcast retry failed for %s: %s", user_id, retry_exc)

        except Forbidden:
            blocked += 1
            mark_unreachable(user_id)

        except BadRequest as exc:
            failed += 1
            logger.warning("Broadcast bad request for %s: %s", user_id, exc)

        except TelegramError as exc:
            failed += 1
            logger.warning("Broadcast failed for %s: %s", user_id, exc)

        if index % 25 == 0 or index == total:
            try:
                await status_msg.edit_text(
                    "📢 Xavier Broadcast\n"
                    f"التقدم: {index}/{total}\n"
                    f"✅ تم: {sent}\n"
                    f"🚫 غير متاح: {blocked}\n"
                    f"⚠️ فشل: {failed}"
                )
            except TelegramError:
                pass

        await asyncio.sleep(0.06)

    finish_broadcast_run(run_id, sent, blocked, failed)

    await status_msg.edit_text(
        "✅ انتهى البث\n\n"
        f"👥 الإجمالي: {total}\n"
        f"✅ تم الإرسال: {sent}\n"
        f"🚫 حظر/غير متاح: {blocked}\n"
        f"⚠️ فشل: {failed}"
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return

    msg = update.effective_message
    if msg.reply_to_message:
        await perform_broadcast(update, context, source_message=msg.reply_to_message)
        return

    if context.args:
        await perform_broadcast(
            update,
            context,
            text_message=" ".join(context.args),
        )
        return

    await msg.reply_text(
        "📢 للبث:\n"
        "1) أرسل الصورة/الفيديو/الرسالة.\n"
        "2) اعمل Reply عليها.\n"
        "3) اكتب /broadcast أو #broadcast."
    )


async def hashtag_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return

    msg = update.effective_message
    if msg.reply_to_message:
        await perform_broadcast(update, context, source_message=msg.reply_to_message)
        return

    raw = (msg.text or "").strip()
    payload = raw[len("#broadcast"):].strip()
    if payload:
        await perform_broadcast(update, context, text_message=payload)
    else:
        await msg.reply_text("اعمل Reply على الرسالة المطلوبة ثم اكتب #broadcast.")


# =============================================================================
# OWNER TOOLS
# =============================================================================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return
    total, active, blocked, banned = stats()
    await update.effective_message.reply_text(
        "📊 Xavier Statistics\n\n"
        f"👥 إجمالي المسجلين: {total}\n"
        f"✅ نشط: {active}\n"
        f"🚫 حظر البوت/غير متاح: {blocked}\n"
        f"⛔ محظور إداريًا: {banned}"
    )


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("الاستخدام: /ban USER_ID")
        return
    target = int(context.args[0])
    set_banned(target, True)
    await update.effective_message.reply_text(f"⛔ تم حظر {target}.")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_only(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("الاستخدام: /unban USER_ID")
        return
    target = int(context.args[0])
    set_banned(target, False)
    await update.effective_message.reply_text(f"✅ تم فك حظر {target}.")


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_owner(update):
        await query.answer("غير مصرح", show_alert=True)
        return

    if query.data == "stats":
        total, active, blocked, banned = stats()
        await query.message.reply_text(
            f"👥 الإجمالي: {total}\n✅ نشط: {active}\n🚫 غير متاح: {blocked}\n⛔ محظور: {banned}"
        )
    elif query.data == "broadcast_help":
        await query.message.reply_text(
            "📢 أرسل الرسالة أو الصورة، ثم اعمل Reply عليها واكتب /broadcast أو #broadcast."
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    exc = context.error
    if isinstance(exc, Conflict):
        logger.critical(
            "Telegram polling conflict: another process is using this bot token. Rotate the token immediately."
        )
        return
    logger.exception("Unhandled Telegram error", exc_info=exc)


# =============================================================================
# MAIN
# =============================================================================

def main():
    validate_config()
    init_db()
    start_web()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", show_admin))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))

    # #broadcast must be handled before generic message routing.
    application.add_handler(
        MessageHandler(filters.Regex(r"(?i)^#broadcast(?:\\s|$)"), hashtag_broadcast),
        group=0,
    )

    application.add_handler(CallbackQueryHandler(callbacks), group=0)

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.Regex(r"(?i)^#broadcast(?:\\s|$)"),
            handle_owner_message,
        ),
        group=1,
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.Regex(r"(?i)^#broadcast(?:\\s|$)"),
            handle_student,
        ),
        group=2,
    )

    application.add_error_handler(error_handler)

    logger.info("Xavier Bot starting with secure single-owner mode")
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
