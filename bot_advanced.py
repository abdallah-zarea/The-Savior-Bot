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
from telegram.constants import ParseMode, ChatAction

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
TOKEN = "8175662986:AAEWfKO69YNZ_jTXq5qBRWsROUVohuiNbtY"
ADMIN_IDS_STR = "5324699237,5742283044,1207574750,6125721799,5933051169,5361987371,1388167296"
CONTROLLER_ADMIN_ID = "1388167296"

# تحويل جميع الـ IDs إلى نصوص (Strings) لضمان التوافق
ADMIN_IDS = [str(aid.strip()) for aid in ADMIN_IDS_STR.split(',') if aid.strip()]
DATA_FILE = "bot_data.json"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. DATA MANAGEMENT
# ==============================================================================
LOCKED_CHATS = {} 
REPLY_MAP = {}

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

async def notify_controller(context, text):
    if CONTROLLER_ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Controller notify error: {e}")

# ==============================================================================
# 3. COMMANDS
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    
    if user_id in data.get("banned", []): return

    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username}
        save_data(data)
        await notify_controller(context, f"➕ **طالب جديد:** {user.first_name} (`{user_id}`)")

    await update.message.reply_text("👋 أهلاً بك! أرسل رسالتك الآن (نص، صورة، صوت) وسنرد عليك.")

# ==============================================================================
# 4. STUDENT LOGIC
# ==============================================================================
async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    
    # التحقق من الحظر
    if user_id in data.get("banned", []): return

    # ضمان وجود الطالب في البيانات
    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username}
        save_data(data)

    # 1. حالة المحادثة المقفلة (Locked Chat)
    if user_id in LOCKED_CHATS:
        admin_data = LOCKED_CHATS[user_id]
        target_admin = admin_data['admin_id']
        try:
            forwarded = await update.message.forward(chat_id=target_admin)
            REPLY_MAP[f"{target_admin}_{forwarded.message_id}"] = user_id
            
            kb = [[InlineKeyboardButton("❌ إنهاء", callback_data=f'end_{user_id}')]]
            await context.bot.send_message(
                chat_id=target_admin,
                text="💬 رسالة جديدة:",
                reply_to_message_id=forwarded.message_id,
                reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception as e:
            logger.error(f"Failed to send to admin {target_admin}: {e}")
            del LOCKED_CHATS[user_id] # فك القفل لو فشل الإرسال
        return

    # 2. حالة الرسالة الجديدة العامة
    # إشعار للطالب فوراً
    try:
        await update.message.reply_text("✅ وصل سؤالك، انتظر الرد.", quote=True)
    except Exception as e:
        logger.error(f"Failed to reply to student: {e}")

    # إشعار للأدمنز
    kb = [[InlineKeyboardButton("🗣️ فتح محادثة", callback_data=f'chat_{user_id}')]]
    msg_text = f"📩 **تذكرة جديدة**\n👤: {user.first_name} (`{user_id}`)"

    for admin_id in ADMIN_IDS:
        try:
            # رسالة التنبيه
            await context.bot.send_message(chat_id=admin_id, text=msg_text, parse_mode=ParseMode.MARKDOWN)
            # توجيه الرسالة الأصلية
            fwd = await update.message.forward(chat_id=admin_id)
            # زر التحكم
            await context.bot.send_message(
                chat_id=admin_id,
                text="👇 للرد: اضغط Reply أو الزر:",
                reply_markup=InlineKeyboardMarkup(kb),
                reply_to_message_id=fwd.message_id
            )
            # تخزين الرابط
            REPLY_MAP[f"{admin_id}_{fwd.message_id}"] = user_id
        except Exception as e:
            logger.error(f"Broadcasting to admin {admin_id} failed: {e}")

# ==============================================================================
# 5. ADMIN LOGIC
# ==============================================================================
async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    admin_id = str(update.effective_user.id)
    msg = update.effective_message

    # تجاهل الأوامر
    if msg.text and msg.text.startswith('/'): return

    target_student = None

    # البحث في المحادثات المقفلة
    for sid, info in LOCKED_CHATS.items():
        if info['admin_id'] == admin_id:
            target_student = sid
            break
    
    # البحث في الردود (Reply)
    if not target_student and msg.reply_to_message:
        map_key = f"{admin_id}_{msg.reply_to_message.message_id}"
        target_student = REPLY_MAP.get(map_key)

    if target_student:
        try:
            await msg.copy(chat_id=target_student)
            await msg.set_reaction("👍")
        except Exception as e:
            await msg.reply_text(f"❌ فشل الإرسال: {e}")
            if target_student in LOCKED_CHATS:
                del LOCKED_CHATS[target_student]
    else:
        await msg.reply_text("⚠️ للرد: استخدم Reply على رسالة الطالب أو افتح محادثة.")

# ==============================================================================
# 6. BUTTONS
# ==============================================================================
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()
    action = query.data
    admin_id = str(update.effective_user.id)
    admin_name = update.effective_user.first_name

    if action.startswith('chat_'):
        sid = action.split('_')[1]
        if sid in LOCKED_CHATS:
            owner = LOCKED_CHATS[sid]['admin_name']
            if LOCKED_CHATS[sid]['admin_id'] == admin_id:
                await query.edit_message_text("✅ المحادثة معك.")
            else:
                await context.bot.answer_callback_query(query.id, text=f"⛔ {owner} يتحدث معه!", show_alert=True)
            return
        
        LOCKED_CHATS[sid] = {'admin_id': admin_id, 'admin_name': admin_name}
        kb = [[InlineKeyboardButton("❌ إنهاء", callback_data=f'end_{sid}')]]
        await query.edit_message_text(f"🟢 **بدأت المحادثة.**\nأرسل ردودك مباشرة.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        await notify_controller(context, f"🔒 **{admin_name}** بدأ مع `{sid}`")

    elif action.startswith('end_'):
        sid = action.split('_')[1]
        if sid in LOCKED_CHATS:
            if LOCKED_CHATS[sid]['admin_id'] != admin_id:
                return await context.bot.answer_callback_query(query.id, text="لست صاحب المحادثة!", show_alert=True)
            del LOCKED_CHATS[sid]
            await query.edit_message_text("✅ **تم الإنهاء.**", parse_mode=ParseMode.MARKDOWN)
            await notify_controller(context, f"🔓 **{admin_name}** أنهى مع `{sid}`")
        else:
            await query.edit_message_text("⚠️ منتهية.")

    # باقي الأزرار
    elif action == 'stats_btn':
        await query.message.reply_text(f"👥: {len(data.get('students', {}))}")
    elif action == 'force_unlock':
        if admin_id == CONTROLLER_ADMIN_ID:
            LOCKED_CHATS.clear()
            await query.message.reply_text("تم فك القفل.")
    elif action == 'help_broadcast':
        await query.message.reply_text("رد بـ /broadcast")
    elif action == 'help_ban':
        await query.message.reply_text("/ban ID")

# ==============================================================================
# 7. MAIN ROUTER
# ==============================================================================
async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    # التأكد من أن التحديث عبارة عن رسالة (وليس تعديل رسالة أو غيره)
    if not update.message: return

    uid = str(update.effective_user.id)
    if uid in ADMIN_IDS:
        await handle_admin_message(update, context, data)
    else:
        await handle_student_message(update, context, data)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📊 إحصائيات", callback_data='stats_btn')],
        [InlineKeyboardButton("🔓 فك قفل", callback_data='force_unlock')],
        [InlineKeyboardButton("📢 بث", callback_data='help_broadcast'), InlineKeyboardButton("🚫 حظر", callback_data='help_ban')]
    ]
    await update.message.reply_text("👮‍♂️ لوحة التحكم", reply_markup=InlineKeyboardMarkup(kb))

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not context.args: return await update.message.reply_text("/ban ID")
    data.setdefault("banned", []).append(context.args[0])
    save_data(data)
    await update.message.reply_text("تم الحظر")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not context.args: return await update.message.reply_text("/unban ID")
    target = context.args[0]
    if target in data.get("banned", []):
        data["banned"].remove(target)
        save_data(data)
        await update.message.reply_text("تم الفك")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not update.message.reply_to_message: return await update.message.reply_text("رد على رسالة")
    students = data.get("students", {}).keys()
    c = 0
    await update.message.reply_text("جاري البث...")
    for sid in students:
        try:
            await update.message.reply_to_message.copy(chat_id=sid)
            c+=1
            await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text(f"تم لـ {c}")

def main():
    start_keep_alive()
    bot_data = load_data()
    app = Application.builder().token(TOKEN).build()
    
    # تنظيف الويب هوك القديم لضمان العمل (خطوة مهمة)
    print("Cleaning old webhook...")
    try:
        # نقوم بإنشاء loop مؤقت لتنفيذ أمر الحذف
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(app.bot.delete_webhook(drop_pending_updates=True))
        print("Webhook deleted successfully.")
    except Exception as e:
        print(f"Webhook deletion warning: {e}")

    p = partial
    router_p = p(main_router, data=bot_data)
    btns_p = p(buttons_handler, data=bot_data)
    
    # Admin Filter (String check)
    # ملاحظة: الفلاتر في المكتبة تتوقع int، لذا سنستخدم فلتر مخصص بسيط أو نحول القائمة ل int
    # لكن الأسهل هنا هو الاعتماد على فحص الـ ID داخل الدوال، واستخدام فلتر عام للأوامر
    
    app.add_handler(CommandHandler("start", partial(start_command, data=bot_data)))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("ban", partial(ban_user, data=bot_data)))
    app.add_handler(CommandHandler("unban", partial(unban_user, data=bot_data)))
    app.add_handler(CommandHandler("broadcast", partial(broadcast, data=bot_data)))
    
    app.add_handler(CallbackQueryHandler(btns_p))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, router_p))

    print("Bot is Running - FIX APPLIED...")
    app.run_polling()

if __name__ == '__main__':
    main()
