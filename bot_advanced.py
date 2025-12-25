import logging
import json
import os
import asyncio
from functools import partial
from datetime import datetime
from threading import Thread

# --- Flask Server for Render (عشان البوت ميفصلش) ---
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
from telegram.error import TelegramError

# ==============================================================================
# 0. WEB SERVER (KEEP ALIVE)
# ==============================================================================
app = Flask('')

@app.route('/')
def home():
    return "🚀 Legendary Bot is Running Smoothly!"

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

def start_keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# ==============================================================================
# 1. CONFIGURATION (إعدادات البوت)
# ==============================================================================
TOKEN = "8175662986:AAEWfKO69YNZ_jTXq5qBRWsROUVohuiNbtY"
ADMIN_IDS_STR = "5324699237,5742283044,1207574750,6125721799,5933051169,5361987371,1388167296"
CONTROLLER_ADMIN_ID = "1388167296"

# تنظيف القائمة من المسافات الزائدة
ADMIN_IDS = [admin_id.strip() for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip()]
DATA_FILE = "bot_data.json"

# إعداد اللوجر
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. DATA MANAGEMENT & LOCKING SYSTEM
# ==============================================================================
# نظام القفل في الذاكرة (RAM) - يضمن عدم تداخل الأدمنز
# Structure: {'student_id': {'admin_id': '...', 'admin_name': '...', 'start_time': '...'}}
LOCKED_CHATS = {} 

def load_data():
    """تحميل البيانات مع التعامل مع الملفات التالفة"""
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"students": {}, "banned": []}

def save_data(data):
    """حفظ البيانات"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

# ==============================================================================
# 3. HELPER FUNCTIONS (دوال مساعدة)
# ==============================================================================
def get_student_name(data, user_id):
    return data["students"].get(str(user_id), {}).get("name", "طالب غير مسجل")

async def notify_controller(context, text):
    """إرسال إشعار للمراقب فقط"""
    if CONTROLLER_ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=text, parse_mode=ParseMode.MARKDOWN)
        except:
            pass

# ==============================================================================
# 4. ADMIN PANEL & COMMANDS
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    
    # تجاهل المحظورين
    if user_id in data.get("banned", []): return

    if user_id not in data["students"]:
        data["students"][user_id] = {
            "name": user.first_name, 
            "username": user.username, 
            "joined": str(datetime.now())
        }
        save_data(data)
        await notify_controller(context, f"➕ **طالب جديد انضم:** {user.first_name} (`{user_id}`)")

    welcome_text = (
        "👋 **أهلاً بك في بوت التواصل الرسمي**\n\n"
        "📩 أرسل سؤالك أو رسالتك الآن (نص، صورة، ملف صوتي...)\n"
        "وسيقوم أحد المشرفين بالرد عليك في أقرب وقت.\n\n"
        "🌹 _نسعد بخدمتكم_"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats_btn')],
        [InlineKeyboardButton("🔓 فك قفل الكل (Emergency)", callback_data='force_unlock')],
        [InlineKeyboardButton("📢 البث الجماعي", callback_data='help_broadcast')]
    ]
    await update.message.reply_text("🛠 **لوحة تحكم الأدمن**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not context.args: return await update.message.reply_text("استخدم: `/ban ID`")
    target_id = context.args[0]
    if target_id not in data.get("banned", []):
        data.setdefault("banned", []).append(target_id)
        save_data(data)
        await update.message.reply_text(f"⛔ تم حظر `{target_id}`")
    else:
        await update.message.reply_text("محظور بالفعل.")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not context.args: return await update.message.reply_text("استخدم: `/unban ID`")
    target_id = context.args[0]
    if target_id in data.get("banned", []):
        data["banned"].remove(target_id)
        save_data(data)
        await update.message.reply_text(f"✅ تم فك الحظر عن `{target_id}`")
    else:
        await update.message.reply_text("غير محظور.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not update.message.reply_to_message and not context.args:
        return await update.message.reply_text("⚠️ للبث: رد على رسالة بـ `/broadcast` أو اكتب النص.")
    
    students = data.get("students", {}).keys()
    msg = await update.message.reply_text(f"⏳ جاري البث لـ {len(students)} طالب...")
    count = 0
    for sid in students:
        try:
            if update.message.reply_to_message:
                await update.message.reply_to_message.copy(chat_id=sid)
            else:
                await context.bot.send_message(chat_id=sid, text=" ".join(context.args))
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"✅ تم البث لـ {count} طالب.")

# ==============================================================================
# 5. CORE MESSAGING LOGIC (THE BRAIN)
# ==============================================================================

async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """معالجة رسائل الطلاب وتوجيهها بذكاء"""
    user = update.effective_user
    user_id = str(user.id)
    
    # 1. فحص الحظر
    if user_id in data.get("banned", []): return

    # 2. تسجيل تلقائي لو مش مسجل
    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username, "joined": str(datetime.now())}
        save_data(data)

    # 3. التحقق من القفل (هل الطالب يتحدث مع أدمن حالياً؟)
    if user_id in LOCKED_CHATS:
        admin_id = LOCKED_CHATS[user_id]['admin_id']
        admin_name = LOCKED_CHATS[user_id]['admin_name']
        
        # توجيه الرسالة للأدمن المسؤول فقط (Direct Tunnel)
        try:
            forwarded = await update.message.forward(chat_id=admin_id)
            # زر إنهاء يظهر للأدمن مع كل رسالة جديدة من الطالب
            keyboard = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data=f'end_{user_id}')]]
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"👆 رسالة جديدة من الطالب في محادثتك الجارية.",
                reply_to_message_id=forwarded.message_id,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            # لو فشل الإرسال للأدمن (عمل بلوك مثلاً)، نفك القفل
            del LOCKED_CHATS[user_id]
            await update.message.reply_text("⚠️ حدث خطأ في الاتصال بالمشرف، حاول مرة أخرى.")
        return

    # 4. لو الطالب حر (غير محجوز) -> إشعار لكل الأدمنز
    await update.message.reply_text("✅ تم استلام رسالتك. انتظر رد المشرف.")
    
    keyboard = [[InlineKeyboardButton(f"🙋‍♂️ استلام الرد (Reply)", callback_data=f'take_{user_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg_text = (
        f"📩 **تذكرة جديدة**\n"
        f"👤 الطالب: {user.first_name}\n"
        f"🆔 ID: `{user_id}`\n"
        f"🔗 @{user.username or 'NoUser'}"
    )

    for aid in ADMIN_IDS:
        try:
            # نبعت الرسالة الأصلية + الإشعار
            await update.message.forward(chat_id=aid)
            await context.bot.send_message(chat_id=aid, text=msg_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        except: pass

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """معالجة ردود الأدمن"""
    admin_id = str(update.effective_user.id)
    
    # البحث عن الطالب الذي يتحدث معه هذا الأدمن
    # (نبحث في القاموس عن الطالب اللي الـ admin_id بتاعه هو ده)
    active_student_id = None
    for sid, info in LOCKED_CHATS.items():
        if info['admin_id'] == admin_id:
            active_student_id = sid
            break
    
    if not active_student_id:
        # الأدمن مش في محادثة، ويتفلسف وبيبعت كلام
        if not update.message.text.startswith('/'):
            await update.message.reply_text("⚠️ أنت لست في محادثة نشطة.\nانتظر رسائل الطلاب واضغط 'استلام الرد'.")
        return

    # إرسال الرد للطالب
    try:
        await context.bot.send_chat_action(chat_id=active_student_id, action=ChatAction.TYPING)
        await asyncio.sleep(0.2) # تأخير بسيط للواقعية
        await update.message.copy(chat_id=active_student_id)
        
        # تأكيد للأدمن + زر الإنهاء المتكرر (عشان يكون قدام عينه دايماً)
        keyboard = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data=f'end_{active_student_id}')]]
        await update.message.reply_text("✅ تم الإرسال.", reply_markup=InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        await update.message.reply_text(f"❌ فشل الإرسال (ربما حظر البوت): {e}")
        # إنهاء المحادثة إجبارياً
        if active_student_id in LOCKED_CHATS:
            del LOCKED_CHATS[active_student_id]

# ==============================================================================
# 6. BUTTONS HANDLER (نظام التذاكر)
# ==============================================================================
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()
    action = query.data
    admin_id = str(update.effective_user.id)
    admin_name = update.effective_user.first_name

    # --- استلام التذكرة (Take Ticket) ---
    if action.startswith('take_'):
        student_id = action.split('_')[1]
        
        # Race Condition Check (هل حد سبقه؟)
        if student_id in LOCKED_CHATS:
            owner = LOCKED_CHATS[student_id]['admin_name']
            if LOCKED_CHATS[student_id]['admin_id'] == admin_id:
                await query.edit_message_text("⚠️ أنت بالفعل تتحدث معه!")
            else:
                await context.bot.answer_callback_query(query.id, text=f"⛔ سبقك بها {owner}!", show_alert=True)
                await query.edit_message_text(f"🔒 تم الاستلام بواسطة: {owner}")
            return

        # حجز الطالب للأدمن
        LOCKED_CHATS[student_id] = {'admin_id': admin_id, 'admin_name': admin_name}
        
        # تغيير رسالة الزرار لتوضيح إنه اتاخد
        student_name = get_student_name(data, student_id)
        await query.edit_message_text(f"🟢 **تم بدء المحادثة مع {student_name}**\nالمحادثة مغلقة عليك الآن.\nأرسل ردودك مباشرة هنا.")
        
        # إرسال رسالة ترحيب من الأدمن للطالب (اختياري، بس شيك)
        try:
            await context.bot.send_message(chat_id=student_id, text="👨‍💻 **تواصل معك أحد المشرفين الآن.**")
        except: pass
        
        await notify_controller(context, f"🔒 **{admin_name}** بدأ محادثة مع الطالب `{student_id}`")

    # --- إنهاء المحادثة (End Chat) ---
    elif action.startswith('end_'):
        student_id = action.split('_')[1]
        
        # التأكد إن اللي بيقفل هو صاحب المحادثة
        if student_id in LOCKED_CHATS:
            if LOCKED_CHATS[student_id]['admin_id'] != admin_id:
                return await context.bot.answer_callback_query(query.id, text="مش محادثتك عشان تقفلها!", show_alert=True)
            
            del LOCKED_CHATS[student_id]
            await query.edit_message_text("✅ **تم إنهاء المحادثة.**")
            await context.bot.send_message(chat_id=admin_id, text="🔓 المحادثة انتهت. أنت حر الآن.")
            try:
                await context.bot.send_message(chat_id=student_id, text="✅ **تم إنهاء المحادثة من قبل المشرف.**\nشكراً لتواصلك.")
            except: pass
            
            await notify_controller(context, f"🔓 **{admin_name}** أنهى المحادثة مع `{student_id}`")
        else:
            await query.edit_message_text("⚠️ المحادثة منتهية بالفعل.")

    # --- أدوات أخرى ---
    elif action == 'stats_btn':
        await query.message.reply_text(f"👥 الطلاب: {len(data.get('students', {}))}\n🔒 محادثات جارية: {len(LOCKED_CHATS)}")
    elif action == 'force_unlock':
        if admin_id == CONTROLLER_ADMIN_ID:
            LOCKED_CHATS.clear()
            await query.message.reply_text("☢️ تم فك قفل جميع المحادثات!")
        else:
            await context.bot.answer_callback_query(query.id, text="للمدير فقط!", show_alert=True)
    elif action == 'help_broadcast':
        await query.message.reply_text("📢 للبث: رد على رسالة بـ `/broadcast`")

# ==============================================================================
# 7. MAIN RUNNER
# ==============================================================================
async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    """الموزع الرئيسي للرسائل"""
    uid = str(update.effective_user.id)
    
    if uid in ADMIN_IDS:
        await handle_admin_reply(update, context, data)
    else:
        await handle_student_message(update, context, data)

def main():
    start_keep_alive()
    bot_data = load_data()
    app = Application.builder().token(TOKEN).build()
    
    p = partial
    # تجهيز الدوال بالبيانات
    router_p = p(main_router, data=bot_data)
    btns_p = p(buttons_handler, data=bot_data)
    ban_p = p(ban_user, data=bot_data)
    unban_p = p(unban_user, data=bot_data)
    broad_p = p(broadcast, data=bot_data)
    start_p = p(start_command, data=bot_data)

    # الفلاتر
    admin_only = filters.User(user_id=[int(i) for i in ADMIN_IDS])
    
    # تسجيل الأوامر
    app.add_handler(CommandHandler("start", start_p))
    app.add_handler(CommandHandler("admin", admin_panel, filters=admin_only))
    app.add_handler(CommandHandler("ban", ban_p, filters=admin_only))
    app.add_handler(CommandHandler("unban", unban_p, filters=admin_only))
    app.add_handler(CommandHandler("broadcast", broad_p, filters=admin_only))
    
    app.add_handler(CallbackQueryHandler(btns_p))
    # استقبال كل الرسائل ما عدا الأوامر
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, router_p))

    print("🚀 Legendary Bot is LIVE...")
    app.run_polling()

if __name__ == '__main__':
    main()
