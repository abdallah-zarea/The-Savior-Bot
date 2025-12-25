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
from telegram.error import TelegramError

# ==============================================================================
# 0. WEB SERVER (KEEP ALIVE)
# ==============================================================================
app = Flask('')

@app.route('/')
def home():
    return "🚀 System Operational: Handling High Traffic..."

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

ADMIN_IDS = [int(aid.strip()) for aid in ADMIN_IDS_STR.split(',') if aid.strip()]
DATA_FILE = "bot_data.json"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. DATA MANAGEMENT (إدارة البيانات والذاكرة)
# ==============================================================================

# قفل المحادثات (لمنع التضارب)
LOCKED_CHATS = {} 

# ذاكرة الرد السريع (تربط رسالة الأدمن بالطالب)
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
        logger.error(f"Data Save Error: {e}")

def clean_memory():
    """تنظيف الذاكرة من البيانات القديمة لمنع التهنيج"""
    if len(REPLY_MAP) > 5000:
        REPLY_MAP.clear()
        logger.info("Memory cleaned.")

# ==============================================================================
# 3. CORE LOGIC HELPER
# ==============================================================================
async def notify_admins_of_new_msg(context, update, user_id, user):
    """دالة مركزية لتوزيع الرسائل على الأدمنز بذكاء"""
    
    # 1. تجهيز زر التحكم
    keyboard = [[InlineKeyboardButton(f"🗣️ فتح محادثة ({user.first_name})", callback_data=f'chat_{user_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # 2. الإرسال لكل أدمن
    for admin_id in ADMIN_IDS:
        try:
            # محاولة توجيه الرسالة (Forward) للحفاظ على الميديا
            forwarded = await update.message.forward(chat_id=admin_id)
            
            # إرسال زر التحكم كرد على الرسالة الموجهة (تقليل التشتت)
            await context.bot.send_message(
                chat_id=admin_id,
                text="👆 للرد: اضغط **Reply** عليها، أو الزر بالأسفل للنقاش.",
                reply_to_message_id=forwarded.message_id,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
            # تسجيل الرابط في الذاكرة
            REPLY_MAP[f"{admin_id}_{forwarded.message_id}"] = user_id
            
            # فاصل زمني صغير جداً لتجنب الحظر من تيليجرام عند الضغط العالي
            await asyncio.sleep(0.05)

        except TelegramError as e:
            # لو التوجيه فشل (بسبب الخصوصية مثلاً)، نبعت نسخة (Copy)
            try:
                copied = await update.message.copy(chat_id=admin_id, caption=f"📩 من: {user.first_name} (ID: {user_id})\n(تعذر التوجيه المباشر)")
                REPLY_MAP[f"{admin_id}_{copied.message_id}"] = user_id
                
                await context.bot.send_message(
                    chat_id=admin_id,
                    text="👆 للرد: اضغط **Reply**، أو الزر بالأسفل.",
                    reply_to_message_id=copied.message_id,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e2:
                logger.error(f"Failed to send to admin {admin_id}: {e2}")

# ==============================================================================
# 4. HANDLERS
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    if user_id in data.get("banned", []): return

    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username, "joined": str(datetime.now())}
        save_data(data)
        
        # إشعار للمراقب فقط عند دخول طالب جديد
        if CONTROLLER_ADMIN_ID:
            try:
                await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=f"➕ **طالب جديد:** {user.first_name}")
            except: pass

    await update.message.reply_text("👋 السلام عليكم يا دكتور!\nابعت رسالتك وهنرد عليك في أقرب وقت. 🤍")

async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    
    if user_id in data.get("banned", []): return

    # ضمان التسجيل
    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username}
        save_data(data)

    clean_memory() # تنظيف دوري سريع

    # --- الحالة 1: الطالب في محادثة خاصة مع أدمن (Locked) ---
    if user_id in LOCKED_CHATS:
        admin_data = LOCKED_CHATS[user_id]
        target_admin_id = admin_data['admin_id']
        try:
            # توجيه مباشر للأدمن المسؤول
            forwarded = await update.message.forward(chat_id=target_admin_id)
            REPLY_MAP[f"{target_admin_id}_{forwarded.message_id}"] = user_id
            
            # زر إنهاء سريع
            kb = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data=f'end_{user_id}')]]
            await context.bot.send_message(
                chat_id=target_admin_id,
                text="💬 رسالة جديدة في المحادثة:",
                reply_to_message_id=forwarded.message_id,
                reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception:
            # لو فشل الوصول للأدمن، نفك القفل ونحولها للجميع
            del LOCKED_CHATS[user_id]
            await notify_admins_of_new_msg(context, update, user_id, user)
        return

    # --- الحالة 2: رسالة جديدة للجميع ---
    # رد تلقائي للطالب
    await update.message.reply_text("✅ وصل سؤالك، دقايق وهيتم الرد عليك.", quote=True)
    
    # توزيع على الأدمنز
    await notify_admins_of_new_msg(context, update, user_id, user)

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    admin_id = str(update.effective_user.id)
    msg = update.effective_message

    # تجاهل الأوامر
    if msg.text and msg.text.startswith('/'): return

    target_student_id = None

    # 1. فحص هل الأدمن في وضع "محادثة مفتوحة"؟
    for sid, info in LOCKED_CHATS.items():
        if info['admin_id'] == admin_id:
            target_student_id = sid
            break
    
    # 2. فحص هل الأدمن بيعمل Reply؟
    if not target_student_id and msg.reply_to_message:
        map_key = f"{admin_id}_{msg.reply_to_message.message_id}"
        target_student_id = REPLY_MAP.get(map_key)

    if target_student_id:
        try:
            # إرسال للطالب
            await msg.copy(chat_id=target_student_id)
            await msg.set_reaction("👍")
        except Exception as e:
            await msg.reply_text(f"❌ لم تصل الرسالة (قد يكون الطالب حظر البوت): {e}")
            if target_student_id in LOCKED_CHATS:
                del LOCKED_CHATS[target_student_id]
    else:
        await msg.reply_text("⚠️ **تنبيه:** للرد، يجب عمل **Reply** على رسالة الطالب، أو استخدام زر **فتح محادثة**.", parse_mode=ParseMode.MARKDOWN)

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()
    action = query.data
    admin_id = str(update.effective_user.id)
    admin_name = update.effective_user.first_name

    # فتح محادثة (Lock)
    if action.startswith('chat_'):
        student_id = action.split('_')[1]
        
        # حماية التضارب (Race Condition)
        if student_id in LOCKED_CHATS:
            owner = LOCKED_CHATS[student_id]['admin_name']
            owner_id = LOCKED_CHATS[student_id]['admin_id']
            if owner_id == admin_id:
                await query.edit_message_text("✅ أنت بالفعل في محادثة معه.")
            else:
                await context.bot.answer_callback_query(query.id, text=f"⛔ {owner} يتحدث معه الآن!", show_alert=True)
            return

        LOCKED_CHATS[student_id] = {'admin_id': admin_id, 'admin_name': admin_name}
        student_name = data["students"].get(student_id, {}).get("name", "الطالب")
        
        kb = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data=f'end_{student_id}')]]
        await query.edit_message_text(
            f"🟢 **تم فتح الخط مع {student_name}**\nالمحادثة مغلقة عليك الآن.\nأرسل ردودك مباشرة.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )

    # إنهاء
    elif action.startswith('end_'):
        student_id = action.split('_')[1]
        if student_id in LOCKED_CHATS:
            if LOCKED_CHATS[student_id]['admin_id'] != admin_id:
                return await context.bot.answer_callback_query(query.id, text="لست صاحب المحادثة!", show_alert=True)
            del LOCKED_CHATS[student_id]
            await query.edit_message_text("✅ **تم إنهاء المحادثة.**", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("⚠️ المحادثة منتهية.")

    # أدوات إضافية
    elif action == 'stats_btn':
        await query.message.reply_text(f"👥 الطلاب: {len(data.get('students', {}))}\n🔒 المحادثات: {len(LOCKED_CHATS)}")
    elif action == 'force_unlock':
        if str(admin_id) == str(CONTROLLER_ADMIN_ID):
            LOCKED_CHATS.clear()
            await query.message.reply_text("☢️ تم تصفير المحادثات.")
        else:
            await context.bot.answer_callback_query(query.id, text="للمدير فقط", show_alert=True)
    elif action == 'help_broadcast':
        await query.message.reply_text("📢 للبث: رد على رسالة بـ `/broadcast`")
    elif action == 'help_ban':
        await query.message.reply_text("🚫 `/ban ID`")

# ==============================================================================
# 5. ADMIN COMMANDS
# ==============================================================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats_btn')],
        [InlineKeyboardButton("🔓 فك قفل الطوارئ", callback_data='force_unlock')],
        [InlineKeyboardButton("📢 البث", callback_data='help_broadcast'), InlineKeyboardButton("🚫 الحظر", callback_data='help_ban')]
    ]
    await update.message.reply_text("👮‍♂️ **لوحة التحكم**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not update.message.reply_to_message and not context.args:
        return await update.message.reply_text("⚠️ طريقة البث:\nرد على رسالة بـ `/broadcast` أو اكتب النص.")
    
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
            await asyncio.sleep(0.05) # Rate limit protection
        except: pass
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"✅ تم البث لـ {count} طالب.")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not context.args: return await update.message.reply_text("`/ban ID`")
    target = context.args[0]
    data.setdefault("banned", []).append(target)
    save_data(data)
    await update.message.reply_text(f"⛔ تم حظر {target}")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not context.args: return await update.message.reply_text("`/unban ID`")
    target = context.args[0]
    if target in data.get("banned", []):
        data["banned"].remove(target)
        save_data(data)
        await update.message.reply_text(f"✅ تم فك الحظر.")

# ==============================================================================
# 6. MAIN
# ==============================================================================
async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    uid = update.effective_user.id
    if uid in ADMIN_IDS:
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
    
    admin_filter = filters.User(user_id=ADMIN_IDS)
    
    app.add_handler(CommandHandler("start", partial(start_command, data=bot_data)))
    app.add_handler(CommandHandler("admin", admin_panel, filters=admin_filter))
    app.add_handler(CommandHandler("ban", partial(ban_user, data=bot_data), filters=admin_filter))
    app.add_handler(CommandHandler("unban", partial(unban_user, data=bot_data), filters=admin_filter))
    app.add_handler(CommandHandler("broadcast", partial(broadcast, data=bot_data), filters=admin_filter))
    
    app.add_handler(CallbackQueryHandler(btns_p))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, router_p))

    print("Bot is Running High Performance Mode...")
    app.run_polling()

if __name__ == '__main__':
    main()
