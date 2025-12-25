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
    return "🚀 Bot is Running with Full Media Support!"

def run_web_server():
    # استخدام البورت المخصص من Render أو 10000 كاحتياطي
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

# القفل: لمنع تداخل الأدمنز (كل طالب مع أدمن واحد فقط)
LOCKED_CHATS = {} 

# الخريطة الذكية: لربط رسائل الأدمن بالطالب للرد السريع
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

# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================
async def notify_controller(context, text):
    if CONTROLLER_ADMIN_ID:
        try:
            await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=text, parse_mode=ParseMode.MARKDOWN)
        except: pass

# ==============================================================================
# 4. ADMIN PANEL & COMMANDS
# ==============================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    
    if user_id in data.get("banned", []): return

    if user_id not in data["students"]:
        data["students"][user_id] = {
            "name": user.first_name, 
            "username": user.username, 
            "joined": str(datetime.now())
        }
        save_data(data)
        await notify_controller(context, f"➕ **طالب جديد:** {user.first_name} (`{user_id}`)")

    await update.message.reply_text(
        "👋 **أهلاً بك!**\n\n"
        "أرسل سؤالك الآن (نص، صورة، صوت، فيديو...)\n"
        "نحن ندعم جميع أنواع الرسائل. 🎤📷\n"
        "وسيقوم المشرفون بالرد عليك قريباً. 🤍"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats_btn')],
        [InlineKeyboardButton("🔓 فك قفل الكل (Emergency)", callback_data='force_unlock')],
        [InlineKeyboardButton("📢 البث", callback_data='help_broadcast'), InlineKeyboardButton("🚫 الحظر", callback_data='help_ban')]
    ]
    await update.message.reply_text("👮‍♂️ **لوحة التحكم**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

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
        return await update.message.reply_text("📢 للبث: رد على رسالة بـ `/broadcast` أو اكتب النص.")
    
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
# 5. STUDENT HANDLER (استقبال جميع أنواع الرسائل من الطلاب)
# ==============================================================================
async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    
    if user_id in data.get("banned", []): return

    # تسجيل
    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username}
        save_data(data)

    # 1. لو الطالب في محادثة مفتوحة مع أدمن
    if user_id in LOCKED_CHATS:
        admin_data = LOCKED_CHATS[user_id]
        target_admin_id = admin_data['admin_id']
        try:
            # Forward يحافظ على كل أنواع الميديا (صوت، صورة، ملفات)
            forwarded = await update.message.forward(chat_id=target_admin_id)
            
            REPLY_MAP[f"{target_admin_id}_{forwarded.message_id}"] = user_id
            
            kb = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data=f'end_{user_id}')]]
            await context.bot.send_message(
                chat_id=target_admin_id, 
                text="💬 رسالة جديدة (اضغط Reply للرد أو استخدم المايك 🎙️):", 
                reply_to_message_id=forwarded.message_id,
                reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception:
            del LOCKED_CHATS[user_id]
        return

    # 2. رسالة جديدة (مش محجوزة)
    await update.message.reply_text("✅ وصل سؤالك، انتظر الرد.", quote=True)
    
    keyboard = [[InlineKeyboardButton("🗣️ فتح محادثة (Long Chat)", callback_data=f'chat_{user_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg_text = (
        f"📩 **تذكرة جديدة**\n"
        f"👤: {user.first_name} (`{user_id}`)\n"
        f"🔗: @{user.username or 'NoUser'}\n"
    )

    for admin_id in ADMIN_IDS:
        try:
            # إشعار ببيانات الطالب
            await context.bot.send_message(chat_id=admin_id, text=msg_text, parse_mode=ParseMode.MARKDOWN)
            
            # توجيه الرسالة الأصلية (مهما كان نوعها)
            forwarded_msg = await update.message.forward(chat_id=admin_id)
            
            # زر التحكم
            await context.bot.send_message(
                chat_id=admin_id, 
                text="👇 للرد اضغط Reply أو الزر بالأسفل:", 
                reply_markup=reply_markup,
                reply_to_message_id=forwarded_msg.message_id
            )

            # تخزين الرابط
            REPLY_MAP[f"{admin_id}_{forwarded_msg.message_id}"] = user_id

        except Exception as e:
            logger.error(f"Failed to forward to {admin_id}: {e}")

# ==============================================================================
# 6. ADMIN HANDLER (دعم الميديا الكامل للأدمن)
# ==============================================================================
async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    admin_id = str(update.effective_user.id)
    msg = update.effective_message

    # تجاهل الأوامر (سواء في النص أو الكابشن)
    text_content = msg.text or msg.caption
    if text_content and text_content.startswith('/'): return

    target_student_id = None

    # هل الأدمن فاتح محادثة؟
    for sid, info in LOCKED_CHATS.items():
        if info['admin_id'] == admin_id:
            target_student_id = sid
            break
    
    # لو مش فاتح محادثة، هل عامل Reply؟
    if not target_student_id and msg.reply_to_message:
        map_key = f"{admin_id}_{msg.reply_to_message.message_id}"
        target_student_id = REPLY_MAP.get(map_key)

    if target_student_id:
        try:
            # السطر السحري: copy() ينسخ أي نوع رسالة (صورة، صوت، فيديو) ويبعته للطالب
            await msg.copy(chat_id=target_student_id)
            # علامة صح ✅
            await msg.set_reaction("👍")
        except Exception as e:
            await msg.reply_text(f"❌ فشل الإرسال: {e}")
            if target_student_id in LOCKED_CHATS:
                del LOCKED_CHATS[target_student_id]
    else:
        await msg.reply_text("⚠️ **تنبيه:** للرد، استخدم **Reply** أو افتح محادثة.", parse_mode=ParseMode.MARKDOWN)

# ==============================================================================
# 7. BUTTONS HANDLER
# ==============================================================================
async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()
    action = query.data
    admin_id = str(update.effective_user.id)
    admin_name = update.effective_user.first_name

    if action.startswith('chat_'):
        student_id = action.split('_')[1]
        
        if student_id in LOCKED_CHATS:
            owner = LOCKED_CHATS[student_id]['admin_name']
            if LOCKED_CHATS[student_id]['admin_id'] == admin_id:
                await query.edit_message_text("✅ أنت تتحدث معه بالفعل.")
            else:
                await context.bot.answer_callback_query(query.id, text=f"⛔ {owner} يتحدث معه الآن!", show_alert=True)
            return

        LOCKED_CHATS[student_id] = {'admin_id': admin_id, 'admin_name': admin_name}
        student_name = data["students"].get(student_id, {}).get("name", "الطالب")
        
        kb = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data=f'end_{student_id}')]]
        await query.edit_message_text(
            f"🟢 **تم فتح الخط مع {student_name}**\n\n"
            f"يمكنك الآن إرسال:\n🎙️ ريكوردات\n📷 صور\n🎥 فيديوهات\n📝 نصوص\n\nاضغط إنهاء عند الانتهاء.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )
        await notify_controller(context, f"🔒 **{admin_name}** بدأ محادثة مع `{student_id}`")

    elif action.startswith('end_'):
        student_id = action.split('_')[1]
        if student_id in LOCKED_CHATS:
            if LOCKED_CHATS[student_id]['admin_id'] != admin_id:
                return await context.bot.answer_callback_query(query.id, text="لست صاحب المحادثة!", show_alert=True)
            
            del LOCKED_CHATS[student_id]
            await query.edit_message_text("✅ **تم إنهاء المحادثة.**", parse_mode=ParseMode.MARKDOWN)
            await notify_controller(context, f"🔓 **{admin_name}** أنهى المحادثة مع `{student_id}`")
        else:
            await query.edit_message_text("⚠️ المحادثة منتهية بالفعل.")

    elif action == 'stats_btn':
        await query.message.reply_text(f"👥 الطلاب: {len(data.get('students', {}))}\n🔒 المحادثات: {len(LOCKED_CHATS)}")
    elif action == 'force_unlock':
        if admin_id == CONTROLLER_ADMIN_ID:
            LOCKED_CHATS.clear()
            await query.message.reply_text("☢️ تم فك قفل الجميع.")
        else:
            await context.bot.answer_callback_query(query.id, text="للمدير فقط!", show_alert=True)
    elif action == 'help_broadcast':
        await query.message.reply_text("📢 رد على رسالة بـ `/broadcast`")
    elif action == 'help_ban':
        await query.message.reply_text("🚫 `/ban ID`")

# ==============================================================================
# 8. MAIN
# ==============================================================================
async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if str(update.effective_user.id) in ADMIN_IDS:
        await handle_admin_message(update, context, data)
    else:
        await handle_student_message(update, context, data)

def main():
    start_keep_alive()
    bot_data = load_data()
    app = Application.builder().token(TOKEN).build()
    
    p = partial
    router_p = p(main_router, data=bot_data)
    btns_p = p(buttons_handler, data=bot_data)
    
    admin_only = filters.User(user_id=[int(i) for i in ADMIN_IDS])
    app.add_handler(CommandHandler("start", partial(start_command, data=bot_data)))
    app.add_handler(CommandHandler("admin", admin_panel, filters=admin_only))
    app.add_handler(CommandHandler("ban", partial(ban_user, data=bot_data), filters=admin_only))
    app.add_handler(CommandHandler("unban", partial(unban_user, data=bot_data), filters=admin_only))
    app.add_handler(CommandHandler("broadcast", partial(broadcast, data=bot_data), filters=admin_only))
    
    app.add_handler(CallbackQueryHandler(btns_p))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, router_p))

    print("Bot is Running Full Media Mode...")
    app.run_polling()

if __name__ == '__main__':
    main()
