import logging
import json
import os
import asyncio
from functools import partial
from datetime import datetime
from threading import Thread

# --- مكتبة سيرفر الويب (عشان Render و UptimeRobot) ---
from flask import Flask

# --- مكتبات تيليجرام ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode, ChatAction

# ==============================================================================
# 0. سيرفر الويب (Keep Alive for Render/Replit)
# ==============================================================================
app = Flask('')

@app.route('/')
def home():
    return "Bot is running and alive! 🚀"

def run_web_server():
    # Render بيحتاج بورت، وغالباً بيستخدم Environment Variable اسمه PORT
    # لو ملقاش، هيستخدم 8080 كافتراضي
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def start_keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# ==============================================================================
# 1. الإعدادات (CONFIGURATION)
# ==============================================================================
TOKEN = "8175662986:AAEWfKO69YNZ_jTXq5qBRWsROUVohuiNbtY"
ADMIN_IDS_STR = "5324699237,5742283044,1207574750,6125721799,5933051169,5361987371,1388167296"
CONTROLLER_ADMIN_ID = "1388167296"

ADMIN_IDS = [admin_id.strip() for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip()]
DATA_FILE = "bot_data.json"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. إدارة البيانات (DATA MANAGER)
# ==============================================================================
def load_data():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"students": {}, "banned": []}

def save_data(data):
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

# ==============================================================================
# 3. وظائف الأدمن (ADMIN FEATURES)
# ==============================================================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats_btn')],
        [InlineKeyboardButton("💾 نسخة احتياطية (Backup)", callback_data='backup_btn')],
        [InlineKeyboardButton("📢 طريقة البث", callback_data='help_broadcast')],
        [InlineKeyboardButton("🚫 كيفية الحظر", callback_data='help_ban')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👮‍♂️ **لوحة تحكم الأدمن**", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def ban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    try:
        target_id = context.args[0]
        if target_id not in data["banned"]:
            data["banned"].append(target_id)
            save_data(data)
            await update.message.reply_text(f"⛔ تم حظر الطالب `{target_id}`.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("هذا الطالب محظور بالفعل.")
    except IndexError:
        await update.message.reply_text("استخدم: `/ban ID`", parse_mode=ParseMode.MARKDOWN)

async def unban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    try:
        target_id = context.args[0]
        if target_id in data["banned"]:
            data["banned"].remove(target_id)
            save_data(data)
            await update.message.reply_text(f"✅ تم فك الحظر عن `{target_id}`.", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("هذا الطالب ليس محظوراً.")
    except IndexError:
        await update.message.reply_text("استخدم: `/unban ID`", parse_mode=ParseMode.MARKDOWN)

async def send_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_document(
            document=open(DATA_FILE, 'rb'),
            caption=f"💾 Backup: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    except FileNotFoundError:
        await update.message.reply_text("لا يوجد ملف بيانات.")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not update.message.reply_to_message and not context.args:
        await update.message.reply_text("⚠️ للبث: رد على رسالة بـ `/broadcast` أو اكتب النص بعد الأمر.")
        return

    students = data.get("students", {}).keys()
    if not students:
        await update.message.reply_text("لا يوجد طلاب.")
        return

    status_msg = await update.message.reply_text(f"⏳ جاري البث لـ {len(students)} طالب...")
    success = 0
    
    for student_id in students:
        try:
            if update.message.reply_to_message:
                await update.message.reply_to_message.copy(chat_id=student_id)
            else:
                await context.bot.send_message(chat_id=student_id, text=' '.join(context.args))
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, text=f"✅ تم البث بنجاح لـ: {success}")

# ==============================================================================
# 4. معالجة الرسائل (CORE LOGIC)
# ==============================================================================
async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    
    if user_id in data.get("banned", []): return

    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username, "joined": str(datetime.now())}
        save_data(data)
        if CONTROLLER_ADMIN_ID:
             await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=f"➕ طالب جديد: {user.first_name} (`{user_id}`)", parse_mode=ParseMode.MARKDOWN)

    await update.message.reply_text('تم الاستلام، سيتم الرد قريباً.. ⏳')

    keyboard = [[InlineKeyboardButton(f"🗣️ رد على {user.first_name}", callback_data=f'reply_{user_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    for admin_id in ADMIN_IDS:
        try:
            fwd = await update.message.forward(chat_id=admin_id)
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📩 من: *{user.first_name}* (`{user_id}`)\n@{user.username or 'NoUser'}",
                reply_to_message_id=fwd.message_id,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception: pass

async def handle_admin_reply_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    student_id = context.user_data.get('reply_to_student_id')
    if not student_id: return

    try:
        await context.bot.send_chat_action(chat_id=student_id, action=ChatAction.TYPING)
        await asyncio.sleep(0.5)
        await update.message.copy(chat_id=student_id)
        await update.message.set_reaction("👍")

        if str(update.effective_user.id) != CONTROLLER_ADMIN_ID and CONTROLLER_ADMIN_ID:
            await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=f"📝 رد من {update.effective_user.first_name} على `{student_id}`", parse_mode=ParseMode.MARKDOWN)
            await update.message.copy(chat_id=CONTROLLER_ADMIN_ID)
    except Exception as e:
        await update.message.reply_text(f"❌ فشل الإرسال: {e}")

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'reply_to_student_id' in context.user_data:
        del context.user_data['reply_to_student_id']
        await update.message.reply_text("✅ تم إنهاء المحادثة.")
    else:
        await update.message.reply_text("لست في محادثة.")

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action.startswith('reply_'):
        student_id = action.split('_')[1]
        context.user_data['reply_to_student_id'] = student_id
        name = data["students"].get(student_id, {}).get("name", "الطالب")
        await query.edit_message_text(f"🟢 محادثة مفتوحة مع **{name}**.\nللإغلاق: `/done`", parse_mode=ParseMode.MARKDOWN)
    elif action == 'stats_btn':
        await query.message.reply_text(f"👥 الطلاب: {len(data.get('students', {}))}\n🚫 المحظورين: {len(data.get('banned', []))}")
    elif action == 'backup_btn':
        await send_backup(query, context)
    elif action == 'help_broadcast':
        await query.message.reply_text("📢 للبث: `/broadcast النص` أو رد على رسالة بـ `/broadcast`", parse_mode=ParseMode.MARKDOWN)
    elif action == 'help_ban':
        await query.message.reply_text("🚫 `/ban ID` للحظر\n`/unban ID` لفك الحظر", parse_mode=ParseMode.MARKDOWN)

async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if str(update.effective_user.id) in ADMIN_IDS:
        if context.user_data.get('reply_to_student_id'):
            if update.message.text and update.message.text.startswith('/'): return 
            await handle_admin_reply_mode(update, context, data)
        else:
            if not update.message.text or not update.message.text.startswith('/'):
                await update.message.reply_text("أهلاً أدمن 👋. /admin للتحكم.")
    else:
        await handle_student_message(update, context, data)

# ==============================================================================
# 5. التشغيل (MAIN)
# ==============================================================================
def main():
    # تشغيل سيرفر الويب في الخلفية (مهم لـ Render)
    start_keep_alive()

    bot_data = load_data()
    app = Application.builder().token(TOKEN).build()
    
    p = partial
    router_p = p(main_router, data=bot_data)
    btns_p = p(buttons_handler, data=bot_data)
    ban_p = p(ban_user_command, data=bot_data)
    unban_p = p(unban_user_command, data=bot_data)
    broad_p = p(broadcast_command, data=bot_data)

    admin_only = filters.User(user_id=[int(uid) for uid in ADMIN_IDS])

    app.add_handler(CommandHandler("start", partial(start_command, data=bot_data)))
    app.add_handler(CommandHandler("admin", admin_panel, filters=admin_only))
    app.add_handler(CommandHandler("done", done_command, filters=admin_only))
    app.add_handler(CommandHandler("ban", ban_p, filters=admin_only))
    app.add_handler(CommandHandler("unban", unban_p, filters=admin_only))
    app.add_handler(CommandHandler("broadcast", broad_p, filters=admin_only))
    
    app.add_handler(CallbackQueryHandler(btns_p))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, router_p))

    print("Bot is Running on Render/Replit Mode...")
    app.run_polling()

if __name__ == '__main__':
    main()
