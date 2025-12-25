import logging
import json
import os
import asyncio
from functools import partial
from threading import Thread

# --- Flask Server (عشان Render يفضل شغال) ---
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
    return "Simple & Pro Bot is Alive! 🚀"

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

def start_keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# ==============================================================================
# 1. CONFIGURATION (الإعدادات)
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
# 2. DATA MANAGEMENT (إدارة البيانات)
# ==============================================================================
# active_chats: لتخزين المحادثات المطولة المفتوحة حالياً في الذاكرة
# Format: {admin_id: student_id}
ACTIVE_CHATS = {} 

def load_data():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # students: بيانات الطلاب
        # msg_map: ربط رسالة الأدمن برسالة الطالب (عشان الرد العادي)
        return {"students": {}, "msg_map": {}}

def save_data(data):
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

# ==============================================================================
# 3. CORE LOGIC (المنطق الأساسي)
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """أمر البداية للطالب"""
    user = update.effective_user
    user_id = str(user.id)
    
    # تسجيل الطالب
    if user_id not in data["students"]:
        data["students"][user_id] = {
            "name": user.first_name, 
            "username": user.username
        }
        save_data(data)

    await update.message.reply_text(
        "👋 **أهلاً بك!**\n\n"
        "أرسل سؤالك أو رسالتك الآن، وسيقوم المشرفون بالرد عليك.\n"
        "لا تنسَ الدعاء لإخوانك. 🤍",
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """استقبال رسالة الطالب وتوجيهها للأدمن"""
    user = update.effective_user
    user_id = str(user.id)
    
    # تحديث البيانات
    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username}
    
    # إشعار للطالب
    await update.message.reply_text("✅ تم الإرسال، انتظر الرد.", quote=True)

    # تجهيز زر المحادثة المطولة
    keyboard = [[InlineKeyboardButton("🗣️ فتح محادثة مطولة", callback_data=f'chat_{user_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # التوجيه لكل الأدمنز
    for admin_id in ADMIN_IDS:
        try:
            # توجيه الرسالة (Forward)
            forwarded_msg = await update.message.forward(chat_id=admin_id)
            
            # إرسال زر التحكم تحتها
            sent_msg = await context.bot.send_message(
                chat_id=admin_id,
                text=f"👆 رسالة من الطالب: {user.first_name} (`{user_id}`)\nلـ *الرد السريع*: اعمل Reply على الرسالة اللي فوق.\nلـ *النقاش*: اضغط الزر تحت.",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
            # حفظ معرف الرسالة الموجهة لربطها بالطالب (عشان الرد السريع)
            # data["msg_map"][str(forwarded_msg.message_id)] = user_id  <-- (الطريقة دي معقدة مع تعدد الأدمنز)
            # الحل الأبسط للرد السريع: نعتمد على الـ Reply بتاعة تيليجرام
            # لكن تيليجرام بيخفي أحياناً الـ User ID في الـ Forward
            # لذلك سنقوم بتخزين الرابط محلياً:
            # Map Admin's Forwarded Message ID -> Student ID
            if "msg_map" not in data: data["msg_map"] = {}
            data["msg_map"][f"{admin_id}_{forwarded_msg.message_id}"] = user_id
            save_data(data)

        except Exception as e:
            logger.error(f"Failed to send to admin {admin_id}: {e}")

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """معالجة رسائل الأدمن (سواء رد سريع أو محادثة مطولة)"""
    admin_id = str(update.effective_user.id)
    
    # 1. الحالة الأولى: الأدمن فاتح محادثة مطولة (Long Chat Mode)
    if admin_id in ACTIVE_CHATS:
        student_id = ACTIVE_CHATS[admin_id]
        try:
            # نسخ الرسالة للطالب
            await update.message.copy(chat_id=student_id)
            # تأكيد للأدمن
            await update.message.set_reaction("👍")
        except Exception as e:
            await update.message.reply_text(f"❌ لم تصل الرسالة (ربما حظر البوت): {e}")
            del ACTIVE_CHATS[admin_id] # إنهاء المحادثة
        return

    # 2. الحالة الثانية: الأدمن بيعمل Reply عادي (One-Shot Reply)
    if update.message.reply_to_message:
        replied_msg_id = update.message.reply_to_message.message_id
        
        # البحث عن صاحب الرسالة الأصلية
        # المفتاح في الداتا هو: "AdminID_MessageID"
        key = f"{admin_id}_{replied_msg_id}"
        
        student_id = data.get("msg_map", {}).get(key)
        
        if student_id:
            try:
                await update.message.copy(chat_id=student_id)
                await update.message.reply_text("✅ تم إرسال الرد السريع.")
            except Exception as e:
                await update.message.reply_text(f"❌ فشل الإرسال: {e}")
        else:
            # ممكن يكون بيرد على رسالة قديمة اتمسحت من الداتا أو رسالة بين الأدمنز
            # نتجاهلها عشان منعملش إزعاج
            pass
        return

    # 3. الحالة الثالثة: أدمن بيكتب في الهواء (بدون رد وبدون محادثة مفتوحة)
    # نتجاهله، أو ممكن نرد عليه نقوله "يا باشا اعمل ريبلاي"
    if not update.message.text.startswith('/'): # نتجاهل الأوامر
        await update.message.reply_text("⚠️ **تنبيه:**\nللرد على طالب، قم بعمل **Reply** على رسالته.\nأو اضغط زر **محادثة مطولة** لفتح شات.", parse_mode=ParseMode.MARKDOWN)

# ==============================================================================
# 4. BUTTONS & COMMANDS (الأزرار والأوامر)
# ==============================================================================

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """التعامل مع زر فتح/غلق المحادثة المطولة"""
    query = update.callback_query
    await query.answer()
    action = query.data
    admin_id = str(update.effective_user.id)

    # فتح محادثة
    if action.startswith('chat_'):
        student_id = action.split('_')[1]
        
        # تفعيل الوضع
        ACTIVE_CHATS[admin_id] = student_id
        
        student_name = data["students"].get(student_id, {}).get("name", "الطالب")
        
        # زر الإغلاق
        keyboard = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data='close_chat')]]
        
        await query.edit_message_text(
            f"🟢 **تم فتح محادثة مطولة مع {student_name}**\n\n"
            "الآن أي رسالة سترسلها (بدون Reply) ستصل للطالب مباشرة.\n"
            "عند الانتهاء اضغط إنهاء.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        
        # إشعار للطالب (اختياري)
        try:
            await context.bot.send_chat_action(chat_id=student_id, action=ChatAction.TYPING)
        except: pass

    # إغلاق محادثة
    elif action == 'close_chat':
        if admin_id in ACTIVE_CHATS:
            del ACTIVE_CHATS[admin_id]
            await query.edit_message_text("🔴 **تم إنهاء المحادثة المطولة.**\nعدنا لوضع الرد العادي (Reply).", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("⚠️ المحادثة مغلقة بالفعل.")

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🛠 **كيفية الرد:**\n"
        "1️⃣ **رد سريع:** اضغط ضغطة مطولة على رسالة الطالب -> Reply -> اكتب ردك.\n"
        "2️⃣ **نقاش طويل:** اضغط زر 'محادثة مطولة' أسفل رسالة الطالب.\n\n"
        "📊 /stats - لعرض العدد."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    count = len(data.get("students", {}))
    await update.message.reply_text(f"👥 عدد الطلاب المسجلين: {count}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not update.message.reply_to_message and not context.args:
        return await update.message.reply_text("📢 للبث: رد على رسالة بـ `/broadcast`")
    
    students = data.get("students", {}).keys()
    msg = await update.message.reply_text(f"⏳ جاري البث لـ {len(students)} طالب...")
    c = 0
    for sid in students:
        try:
            if update.message.reply_to_message:
                await update.message.reply_to_message.copy(chat_id=sid)
            else:
                await context.bot.send_message(chat_id=sid, text=" ".join(context.args))
            c+=1
            await asyncio.sleep(0.05)
        except: pass
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"✅ تم البث لـ {c}")

# ==============================================================================
# 5. MAIN (التشغيل)
# ==============================================================================

async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """الموزع الرئيسي"""
    if str(update.effective_user.id) in ADMIN_IDS:
        await handle_admin_message(update, context, data)
    else:
        await handle_student_message(update, context, data)

def main():
    start_keep_alive() # تشغيل السيرفر
    bot_data = load_data()
    app = Application.builder().token(TOKEN).build()
    
    p = partial
    router_p = p(main_router, data=bot_data)
    btns_p = p(buttons_handler, data=bot_data)
    
    # أوامر
    app.add_handler(CommandHandler("start", partial(start_command, data=bot_data)))
    app.add_handler(CommandHandler("help", admin_help))
    app.add_handler(CommandHandler("stats", partial(stats_command, data=bot_data)))
    app.add_handler(CommandHandler("broadcast", partial(broadcast_command, data=bot_data)))
    
    app.add_handler(CallbackQueryHandler(btns_p))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, router_p))

    print("Bot is Running Simple & Pro Mode...")
    app.run_polling()

if __name__ == '__main__':
    main()
