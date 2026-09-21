import logging
import json
import os
import asyncio
from functools import partial
from datetime import datetime
from threading import Thread

# --- Flask Server for Render ---
from flask import Flask

# --- Telegram Libraries ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

try:
    import psycopg
except ImportError:
    psycopg = None

# ==============================================================================
# 0. WEB SERVER (KEEP ALIVE)
# ==============================================================================
app = Flask('')

@app.route('/')
def home():
    return "🚀 Bot is Running - Fix Applied!"

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

def start_keep_alive():
    t = Thread(target=run_web_server, daemon=True)
    t.start()

# ==============================================================================
# 1. CONFIGURATION (الإعدادات)
# ==============================================================================
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS_STR = os.environ.get("ADMIN_IDS", "5324699237,5742283044,1207574750,6125721799,5933051169,5361987371,1388167296")
CONTROLLER_ADMIN_ID = os.environ.get("CONTROLLER_ADMIN_ID", "1388167296")
DATABASE_URL = os.environ.get("DATABASE_URL")

# تحويل جميع الـ IDs إلى نصوص (Strings) لضمان التوافق
ADMIN_IDS = [str(aid.strip()) for aid in ADMIN_IDS_STR.split(',') if aid.strip()]
DATA_FILE = "bot_data.json"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ==============================================================================
# 2. DATA MANAGEMENT
# ==============================================================================
LOCKED_CHATS = {}
REPLY_MAP = {}

def now_iso():
    return datetime.utcnow().isoformat() + "Z"

def default_data():
    return {"students": {}, "banned": []}

def normalize_data(data):
    if not isinstance(data, dict):
        data = {}
    data.setdefault("students", {})
    data.setdefault("banned", [])
    if not isinstance(data["students"], dict):
        data["students"] = {}
    if not isinstance(data["banned"], list):
        data["banned"] = []
    return data

def init_db():
    if not DATABASE_URL or psycopg is None:
        return
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_state (
                id SMALLINT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.commit()

def _load_local_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return normalize_data(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return default_data()
    except Exception as exc:
        logger.error("Local data load failed: %s", exc)
        return default_data()

def load_data():
    if DATABASE_URL and psycopg is not None:
        try:
            init_db()
            with psycopg.connect(DATABASE_URL) as conn:
                row = conn.execute("SELECT payload FROM bot_state WHERE id = 1").fetchone()
                if row:
                    payload = row[0]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    return normalize_data(payload)

            local_data = _load_local_data()
            save_data(local_data)
            return local_data
        except Exception as exc:
            logger.error("Database load failed; using local fallback: %s", exc)

    return _load_local_data()

def save_data(data):
    data = normalize_data(data)

    if DATABASE_URL and psycopg is not None:
        try:
            init_db()
            payload = json.dumps(data, ensure_ascii=False)
            with psycopg.connect(DATABASE_URL) as conn:
                conn.execute(
                    """
                    INSERT INTO bot_state (id, payload, updated_at)
                    VALUES (1, %s::jsonb, NOW())
                    ON CONFLICT (id)
                    DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
                    """,
                    (payload,),
                )
                conn.commit()
            return
        except Exception as exc:
            logger.error("Database save failed; using local fallback: %s", exc)

    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as exc:
        logger.error("Local data save failed: %s", exc)

def is_admin(user_id):
    return str(user_id) in ADMIN_IDS

def touch_student(data, user):
    uid = str(user.id)
    profile = data["students"].get(uid)
    if profile is None:
        profile = {
            "name": user.first_name,
            "username": user.username,
            "first_seen": now_iso(),
        }
        data["students"][uid] = profile

    profile["name"] = user.first_name
    profile["username"] = user.username
    profile["last_seen"] = now_iso()
    profile["blocked"] = False
    return profile

async def deny_if_not_admin(update):
    if is_admin(update.effective_user.id):
        return False
    if update.effective_message:
        await update.effective_message.reply_text("⛔ هذا الأمر مخصص للإدارة فقط.")
    return True

async def notify_controller(context, text):
    if not CONTROLLER_ADMIN_ID:
        return
    try:
        await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=text)
    except Exception as exc:
        logger.error("Controller notify error: %s", exc)

# ==============================================================================
# 3. COMMANDS / STUDENT LOGIC
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)

    if is_admin(user_id):
        await update.message.reply_text("👮‍♂️ Xavier Admin mode is active. استخدم /admin")
        return

    if user_id in data.get("banned", []):
        return

    is_new = user_id not in data["students"]
    touch_student(data, user)
    save_data(data)

    if is_new:
        await notify_controller(context, f"➕ طالب جديد: {user.first_name} ({user_id})")

    await update.message.reply_text(
        "👋 أهلاً بك! أرسل رسالتك الآن (نص، صورة، صوت) وسنرد عليك."
    )

async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned", []):
        return

    touch_student(data, user)
    save_data(data)

    if user_id in LOCKED_CHATS:
        admin_data = LOCKED_CHATS[user_id]
        target_admin = admin_data["admin_id"]
        try:
            forwarded = await update.message.forward(chat_id=target_admin)
            REPLY_MAP[f"{target_admin}_{forwarded.message_id}"] = user_id
            kb = [[InlineKeyboardButton("❌ إنهاء", callback_data=f"end_{user_id}")]]
            await context.bot.send_message(
                chat_id=target_admin,
                text="💬 رسالة جديدة:",
                reply_to_message_id=forwarded.message_id,
                reply_markup=InlineKeyboardMarkup(kb),
            )
        except Exception as exc:
            logger.error("Failed to send to admin %s: %s", target_admin, exc)
            LOCKED_CHATS.pop(user_id, None)
        return

    try:
        await update.message.reply_text("✅ وصل سؤالك، انتظر الرد.")
    except Exception as exc:
        logger.error("Failed to reply to student: %s", exc)

    kb = [[InlineKeyboardButton("🗣️ فتح محادثة", callback_data=f"chat_{user_id}")]]
    msg_text = f"📩 تذكرة جديدة\n👤 {user.first_name} ({user_id})"

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=msg_text)
            forwarded = await update.message.forward(chat_id=admin_id)
            await context.bot.send_message(
                chat_id=admin_id,
                text="👇 للرد: اضغط Reply أو الزر:",
                reply_markup=InlineKeyboardMarkup(kb),
                reply_to_message_id=forwarded.message_id,
            )
            REPLY_MAP[f"{admin_id}_{forwarded.message_id}"] = user_id
        except Exception as exc:
            logger.error("Broadcasting to admin %s failed: %s", admin_id, exc)

# ==============================================================================
# 4. ADMIN LOGIC
# ==============================================================================
async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    admin_id = str(update.effective_user.id)
    msg = update.effective_message

    if msg.text and msg.text.startswith("/"):
        return

    target_student = None

    for sid, info in LOCKED_CHATS.items():
        if info["admin_id"] == admin_id:
            target_student = sid
            break

    if not target_student and msg.reply_to_message:
        map_key = f"{admin_id}_{msg.reply_to_message.message_id}"
        target_student = REPLY_MAP.get(map_key)

    if target_student:
        try:
            await msg.copy(chat_id=target_student)
            try:
                await msg.set_reaction("👍")
            except Exception:
                pass
        except Exception as exc:
            await msg.reply_text(f"❌ فشل الإرسال: {exc}")
            LOCKED_CHATS.pop(target_student, None)
    else:
        await msg.reply_text("⚠️ للرد: استخدم Reply على رسالة الطالب أو افتح محادثة.")

# ==============================================================================
# 5. BUTTONS
# ==============================================================================
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()

    if not is_admin(update.effective_user.id):
        await query.message.reply_text("⛔ غير مصرح.")
        return

    action = query.data
    admin_id = str(update.effective_user.id)
    admin_name = update.effective_user.first_name

    if action.startswith("chat_"):
        sid = action.split("_", 1)[1]
        if sid in LOCKED_CHATS:
            owner = LOCKED_CHATS[sid]["admin_name"]
            if LOCKED_CHATS[sid]["admin_id"] == admin_id:
                await query.edit_message_text("✅ المحادثة معك.")
            else:
                await query.message.reply_text(f"⛔ {owner} يتحدث معه!")
            return

        LOCKED_CHATS[sid] = {"admin_id": admin_id, "admin_name": admin_name}
        kb = [[InlineKeyboardButton("❌ إنهاء", callback_data=f"end_{sid}")]]
        await query.edit_message_text(
            "🟢 بدأت المحادثة.\nأرسل ردودك مباشرة.",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        await notify_controller(context, f"🔒 {admin_name} بدأ مع {sid}")

    elif action.startswith("end_"):
        sid = action.split("_", 1)[1]
        if sid in LOCKED_CHATS:
            if LOCKED_CHATS[sid]["admin_id"] != admin_id:
                await query.message.reply_text("⛔ لست صاحب المحادثة!")
                return
            LOCKED_CHATS.pop(sid, None)
            await query.edit_message_text("✅ تم الإنهاء.")
            await notify_controller(context, f"🔓 {admin_name} أنهى مع {sid}")
        else:
            await query.edit_message_text("⚠️ منتهية.")

    elif action == "stats_btn":
        students = data.get("students", {})
        blocked = sum(1 for p in students.values() if p.get("blocked"))
        active = len(students) - blocked
        await query.message.reply_text(
            f"👥 إجمالي المستخدمين: {len(students)}\n"
            f"✅ نشط: {active}\n"
            f"🚫 حظر البوت: {blocked}\n"
            f"⛔ محظور إداريًا: {len(data.get('banned', []))}"
        )

    elif action == "force_unlock":
        if admin_id == CONTROLLER_ADMIN_ID:
            LOCKED_CHATS.clear()
            await query.message.reply_text("✅ تم فك كل الأقفال.")

    elif action == "help_broadcast":
        await query.message.reply_text(
            "📢 Broadcast V2\n"
            "1) اعمل Reply على الصورة/الرسالة ثم اكتب /broadcast أو #broadcast\n"
            "2) أو ابعت صورة Caption يبدأ بـ #broadcast"
        )

    elif action == "help_ban":
        await query.message.reply_text("/ban ID\n/unban ID")

# ==============================================================================
# 6. ADMIN COMMANDS + BROADCAST V2
# ==============================================================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if await deny_if_not_admin(update):
        return

    kb = [
        [InlineKeyboardButton("📊 إحصائيات", callback_data="stats_btn")],
        [InlineKeyboardButton("🔓 فك قفل", callback_data="force_unlock")],
        [
            InlineKeyboardButton("📢 بث", callback_data="help_broadcast"),
            InlineKeyboardButton("🚫 حظر", callback_data="help_ban"),
        ],
    ]
    await update.message.reply_text(
        "👮‍♂️ Xavier Control Panel",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if await deny_if_not_admin(update):
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /ban ID")
        return

    target = str(context.args[0]).strip()
    banned = data.setdefault("banned", [])
    if target not in banned:
        banned.append(target)
        save_data(data)
    await update.message.reply_text(f"✅ تم حظر {target}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if await deny_if_not_admin(update):
        return
    if not context.args:
        await update.message.reply_text("الاستخدام: /unban ID")
        return

    target = str(context.args[0]).strip()
    if target in data.get("banned", []):
        data["banned"].remove(target)
        save_data(data)
        await update.message.reply_text(f"✅ تم فك حظر {target}")
    else:
        await update.message.reply_text("ℹ️ المستخدم غير موجود في قائمة الحظر.")

async def _copy_broadcast(context, source_message, sid, caption_override=None):
    kwargs = {
        "chat_id": sid,
        "from_chat_id": source_message.chat_id,
        "message_id": source_message.message_id,
    }
    if caption_override is not None:
        kwargs["caption"] = caption_override
    await context.bot.copy_message(**kwargs)

async def _send_with_retry(context, sid, source_message=None, text=None, caption_override=None):
    async def do_send():
        if source_message is not None:
            await _copy_broadcast(context, source_message, sid, caption_override)
        else:
            await context.bot.send_message(chat_id=sid, text=text)

    try:
        await do_send()
        return "success", None
    except RetryAfter as exc:
        delay = exc.retry_after
        if hasattr(delay, "total_seconds"):
            delay = delay.total_seconds()
        await asyncio.sleep(float(delay) + 0.5)
        try:
            await do_send()
            return "success", None
        except Forbidden as retry_exc:
            return "blocked", str(retry_exc)
        except TelegramError as retry_exc:
            return "failed", str(retry_exc)
    except Forbidden as exc:
        return "blocked", str(exc)
    except (BadRequest, TelegramError) as exc:
        return "failed", str(exc)
    except Exception as exc:
        return "failed", str(exc)

async def run_broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: dict,
    source_message=None,
    text=None,
    caption_override=None,
):
    if await deny_if_not_admin(update):
        return

    students = data.get("students", {})
    recipients = [
        sid
        for sid, profile in students.items()
        if sid not in data.get("banned", []) and not profile.get("blocked", False)
    ]

    if not recipients:
        await update.effective_message.reply_text(
            "⚠️ لا يوجد مستخدمون نشطون في قاعدة البيانات حاليًا."
        )
        return

    status_msg = await update.effective_message.reply_text(
        f"📢 بدأ البث إلى {len(recipients)} مستخدم..."
    )

    success = blocked = failed = 0
    failures = []

    for index, sid in enumerate(recipients, start=1):
        result, error_text = await _send_with_retry(
            context,
            sid,
            source_message=source_message,
            text=text,
            caption_override=caption_override,
        )

        if result == "success":
            success += 1
        elif result == "blocked":
            blocked += 1
            students.get(sid, {})["blocked"] = True
        else:
            failed += 1
            if error_text and len(failures) < 10:
                failures.append(f"{sid}: {error_text[:120]}")

        if index % 25 == 0 or index == len(recipients):
            try:
                await status_msg.edit_text(
                    f"📢 جاري البث... {index}/{len(recipients)}\n"
                    f"✅ {success} | 🚫 {blocked} | ❌ {failed}"
                )
            except Exception:
                pass

        await asyncio.sleep(0.06)

    save_data(data)

    report = (
        "✅ Broadcast V2 انتهى\n"
        f"👥 المستهدفون: {len(recipients)}\n"
        f"✅ تم الإرسال: {success}\n"
        f"🚫 حظروا البوت: {blocked}\n"
        f"❌ فشل: {failed}"
    )
    if failures:
        report += "\n\nأول الأخطاء:\n" + "\n".join(failures)

    await status_msg.edit_text(report)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if await deny_if_not_admin(update):
        return

    source = update.message.reply_to_message
    if source is None:
        await update.message.reply_text(
            "📌 اعمل Reply على الصورة/الرسالة المراد إرسالها ثم اكتب /broadcast"
        )
        return

    await run_broadcast(update, context, data, source_message=source)

async def hashtag_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if await deny_if_not_admin(update):
        return

    msg = update.effective_message

    if msg.reply_to_message:
        await run_broadcast(update, context, data, source_message=msg.reply_to_message)
        return

    raw = msg.caption if msg.caption is not None else msg.text
    raw = raw or ""
    clean = raw[len("#broadcast"):].lstrip(" \n:-")

    if msg.photo or msg.video or msg.document or msg.animation or msg.audio:
        await run_broadcast(
            update,
            context,
            data,
            source_message=msg,
            caption_override=clean,
        )
        return

    if clean:
        await run_broadcast(update, context, data, text=clean)
        return

    await msg.reply_text(
        "📌 اعمل Reply على الصورة/الرسالة ثم اكتب #broadcast، "
        "أو ابعت صورة Caption يبدأ بـ #broadcast."
    )

# ==============================================================================
# 7. ROUTER + BOOTSTRAP
# ==============================================================================
async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not update.message:
        return

    uid = str(update.effective_user.id)
    if uid in ADMIN_IDS:
        await handle_admin_message(update, context, data)
    else:
        await handle_student_message(update, context, data)

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required.")

    start_keep_alive()
    bot_data = load_data()
    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(
        CommandHandler("start", partial(start_command, data=bot_data))
    )
    telegram_app.add_handler(
        CommandHandler("admin", partial(admin_panel, data=bot_data))
    )
    telegram_app.add_handler(
        CommandHandler("ban", partial(ban_user, data=bot_data))
    )
    telegram_app.add_handler(
        CommandHandler("unban", partial(unban_user, data=bot_data))
    )
    telegram_app.add_handler(
        CommandHandler("broadcast", partial(broadcast, data=bot_data))
    )

    telegram_app.add_handler(
        CallbackQueryHandler(partial(buttons_handler, data=bot_data))
    )

    hashtag_filter = (
        (filters.TEXT & filters.Regex(r"(?i)^#broadcast(?:\\s|$)"))
        | filters.CaptionRegex(r"(?i)^#broadcast(?:\\s|$)")
    )
    telegram_app.add_handler(
        MessageHandler(
            hashtag_filter,
            partial(hashtag_broadcast, data=bot_data),
        ),
        group=0,
    )
    telegram_app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            partial(main_router, data=bot_data),
        ),
        group=1,
    )

    logger.info("Xavier Bot starting with Broadcast V2")
    telegram_app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
