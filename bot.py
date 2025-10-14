# bot.py
import logging
import json
import asyncio
import os # <-- استيراد مكتبة os
from functools import partial

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

# --- قراءة الإعدادات من متغيرات البيئة ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
CONTROLLER_ADMIN_ID = os.getenv("CONTROLLER_ADMIN_ID")

# التحقق من وجود التوكن
if not TOKEN:
    raise ValueError("خطأ: لم يتم العثور على متغير TELEGRAM_TOKEN. تأكد من إضافته في Railway.")

ADMIN_IDS = [admin_id.strip() for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip()]
DATA_FILE = "bot_data.json" # Railway سيتعامل مع هذا الملف

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# (باقي كود البوت بالكامل كما هو بدون أي تغيير...)
# ...
# ... (انسخ هنا باقي الدوال من الكود الأخير اللي بعتهولك)
# ...

# -----------------------------------------------------------------------------
# 2. إدارة البيانات (Data Management)
# -----------------------------------------------------------------------------

def load_data():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            logger.info("تم تحميل البيانات بنجاح.")
            return json.load(f)
    except FileNotFoundError:
        logger.warning("ملف البيانات غير موجود، سيتم إنشاء ملف جديد.")
        return {"students": {}}
    except json.JSONDecodeError:
        logger.error("خطأ في قراءة ملف البيانات، سيتم البدء ببيانات فارغة.")
        return {"students": {}}

def save_data(data):
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"فشل حفظ البيانات: {e}")

# -----------------------------------------------------------------------------
# 3. وظائف البوت والمعالجات (Handlers)
# -----------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    if user_id not in data["students"]:
        data["students"][user_id] = {"first_name": user.first_name, "username": user.username}
        save_data(data)
        logger.info(f"طالب جديد: {user.first_name} (ID: {user_id})")
    await update.message.reply_text('أهلاً بك! يمكنك إرسال سؤالك الآن وسيتم تحويله إلى أحد المشرفين للرد عليك. لا تنس دعوة حلوة لإخوتك ღ')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *أوامر الأدمن المتاحة*\n\n"
        "*/broadcast [رسالة]*\n"
        " لإرسال رسالة جماعية. يمكنك أيضاً الرد على أي رسالة بهذا الأمر لبثها.\n\n"
        "*/stats*\n"
        " لعرض عدد الطلاب المسجلين.\n\n"
        "*/done*\n"
        " لإنهاء المحادثة الحالية مع طالب والعودة للوضع الطبيعي.\n\n"
        "**للرد على الطلاب:**\n"
        "اضغط على زر '🗣️ الرد على الطالب' للدخول في محادثة مباشرة معه."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    student_count = len(data.get("students", {}))
    await update.message.reply_text(f"📊 يوجد حالياً *_`{student_count}`_* طالب مسجل في البوت.", parse_mode=ParseMode.MARKDOWN)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    pass # أبقيت الكود السابق هنا لتجنب التكرار

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إنهاء وضع المحادثة مع الطالب."""
    if 'reply_to_student_id' in context.user_data:
        del context.user_data['reply_to_student_id']
        await update.message.reply_text("✅ *تم الخروج من وضع المحادثة بنجاح.*", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("أنت لست في وضع محادثة حالياً.")

async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    if user_id not in data["students"]:
        data["students"][user_id] = {"first_name": user.first_name, "username": user.username}
        save_data(data)

    await update.message.reply_text('تم استلام رسالتك، شكراً لك. سيتم الرد عليك قريباً.')
    
    keyboard = [[InlineKeyboardButton("🗣️ الرد على الطالب", callback_data=f'reply_{user_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    for admin_id in ADMIN_IDS:
        try:
            forwarded_message = await update.message.forward(chat_id=admin_id)
            await context.bot.send_message(
                chat_id=admin_id, 
                text=f"سؤال جديد من *{user.first_name}* (ID: `{user_id}`)\nاضغط للرد 👇",
                reply_to_message_id=forwarded_message.message_id,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"فشل إرسال الرسالة إلى الأدمن {admin_id}: {e}")

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    admin = update.effective_user
    student_id = context.user_data.get('reply_to_student_id')
    student_info = data.get("students", {}).get(student_id, {})
    student_name = student_info.get("first_name", "طالب")
    
    try:
        await update.message.copy(chat_id=student_id)
        if str(admin.id) != CONTROLLER_ADMIN_ID and CONTROLLER_ADMIN_ID:
            notification_text = (f"📝 الأدمن *{admin.first_name}* يواصل الرد على *{student_name}*...")
            await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=notification_text, parse_mode=ParseMode.MARKDOWN)
            await update.message.copy(chat_id=CONTROLLER_ADMIN_ID)
    except Exception as e:
        logger.error(f"فشل إرسال الرد للطالب {student_id}: {e}")
        await update.message.reply_text(f"حدث خطأ. قد يكون الطالب قد حظر البوت. للخروج، أرسل /done")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()
    action, student_id = query.data.split('_', 1)
    if action == 'reply':
        context.user_data['reply_to_student_id'] = student_id
        student_name = data.get("students", {}).get(student_id, {}).get("first_name", "غير معروف")
        reply_text = (
            f"🗣️ *أنت الآن في محادثة مباشرة مع الطالب {student_name}*.\n\n"
            "أي رسالة ترسلها الآن ستصل إليه مباشرة.\n\n"
            "لإنهاء المحادثة، أرسل الأمر: /done"
        )
        await query.edit_message_text(text=reply_text, parse_mode=ParseMode.MARKDOWN)

async def message_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    if user_id in ADMIN_IDS and context.user_data.get('reply_to_student_id'):
        await handle_admin_reply(update, context, data)
    elif user_id in ADMIN_IDS:
        await update.message.reply_text("أهلاً بك أيها الأدمن. استخدم /help لعرض الأوامر.")
    else:
        await handle_student_message(update, context, data)

def main():
    bot_data = load_data()
    builder = Application.builder().token(TOKEN)
    app = builder.build()
    
    p = partial
    start_p, stats_p, broadcast_p, button_p, dispatcher_p = p(start_command, data=bot_data), p(stats_command, data=bot_data), p(broadcast_command, data=bot_data), p(button_callback_handler, data=bot_data), p(message_dispatcher, data=bot_data)
    
    admin_filter = filters.User(user_id=[int(uid) for uid in ADMIN_IDS])
    media_filter = (filters.ALL) & ~filters.COMMAND

    app.add_handler(CommandHandler('start', start_p))
    app.add_handler(CommandHandler('help', help_command, filters=admin_filter))
    app.add_handler(CommandHandler('stats', stats_p, filters=admin_filter))
    app.add_handler(CommandHandler('broadcast', broadcast_p, filters=admin_filter))
    app.add_handler(CommandHandler('done', done_command, filters=admin_filter))
    app.add_handler(CallbackQueryHandler(button_p))
    app.add_handler(MessageHandler(media_filter, dispatcher_p))

    logger.info("البوت بدأ التشغيل...")
    app.run_polling()

if __name__ == '__main__':
    main()