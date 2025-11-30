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
    return "Legendary Bot is Alive! 🛡️"

def run_web_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

def start_keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================
TOKEN = "8175662986:AAEWfKO69YNZ_jTXq5qBRWsROUVohuiNbtY"
ADMIN_IDS_STR = "5324699237,5742283044,1207574750,6125721799,5933051169,5361987371,1388167296"
CONTROLLER_ADMIN_ID = "1388167296"

ADMIN_IDS = [admin_id.strip() for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip()]
DATA_FILE = "bot_data.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# 2. DATA MANAGEMENT
# ==============================================================================
# Global variable to track locked chats in memory (RAM)
# Format: {'student_id': {'admin_id': '123', 'admin_name': 'Ahmed'}}
LOCKED_CHATS = {} 

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
# 3. ADMIN TOOLS & PANEL
# ==============================================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats_btn')],
        [InlineKeyboardButton("🔓 فك قفل جميع المحادثات (طوارئ)", callback_data='force_unlock_all')],
        [InlineKeyboardButton("📢 البث", callback_data='help_broadcast'), InlineKeyboardButton("🚫 الحظر", callback_data='help_ban')]
    ]
    await update.message.reply_text("👮‍♂️ **لوحة القيادة المركزية**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

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

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if not update.message.reply_to_message and not context.args:
        await update.message.reply_text("⚠️ للبث: رد على رسالة بـ `/broadcast` أو اكتب النص بعد الأمر.")
        return

    students = data.get("students", {}).keys()
    msg = await update.message.reply_text(f"⏳ جاري البث لـ {len(students)} طالب...")
    success = 0
    
    for student_id in students:
        try:
            if update.message.reply_to_message:
                await update.message.reply_to_message.copy(chat_id=student_id)
            else:
                await context.bot.send_message(chat_id=student_id, text=' '.join(context.args))
            success += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    
    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"✅ تم البث بنجاح لـ: {success}")

# ==============================================================================
# 4. CORE LOGIC (MESSAGING & LOCKING SYSTEM)
# ==============================================================================

async def handle_student_message(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    user = update.effective_user
    user_id = str(user.id)
    
    if user_id in data.get("banned", []): return

    # تسجيل الطالب
    if user_id not in data["students"]:
        data["students"][user_id] = {"name": user.first_name, "username": user.username, "joined": str(datetime.now())}
        save_data(data)
        if CONTROLLER_ADMIN_ID:
             await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=f"➕ طالب جديد: {user.first_name}", parse_mode=ParseMode.MARKDOWN)

    # التحقق هل الطالب محجوز؟
    if user_id in LOCKED_CHATS:
        admin_name = LOCKED_CHATS[user_id]['admin_name']
        # لا نرسل رسالة للطالب، لكن نرسل للأدمن المسؤول عنه فقط
        admin_id = LOCKED_CHATS[user_id]['admin_id']
        try:
            # توجيه الرسالة للأدمن المسؤول فقط
            await update.message.forward(chat_id=admin_id)
            await context.bot.send_message(chat_id=admin_id, text="👆 رسالة جديدة من الطالب في محادثتك الحالية.")
        except: pass
        return

    # لو مش محجوز، ابعت لكل الأدمنز
    await update.message.reply_text('تم الاستلام، سيتم الرد قريباً.. ⏳')
    
    keyboard = [[InlineKeyboardButton(f"🗣️ رد على {user.first_name}", callback_data=f'take_{user_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📩 *{user.first_name}* (`{user_id}`)\n@{user.username or 'NoUser'}",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except: pass

async def handle_admin_reply_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    student_id = context.user_data.get('reply_to_student_id')
    if not student_id: return

    # التحقق من أن الأدمن ما زال يملك القفل
    if student_id not in LOCKED_CHATS or LOCKED_CHATS[student_id]['admin_id'] != str(update.effective_user.id):
        await update.message.reply_text("⚠️ انتهت جلستك مع هذا الطالب أو قام أدمن آخر بفك القفل.")
        del context.user_data['reply_to_student_id']
        return

    try:
        await context.bot.send_chat_action(chat_id=student_id, action=ChatAction.TYPING)
        await asyncio.sleep(0.3)
        await update.message.copy(chat_id=student_id)
        await update.message.set_reaction("👍")
    except Exception as e:
        await update.message.reply_text(f"❌ لم تصل الرسالة (حظر البوت؟): {e}")

# ==============================================================================
# 5. BUTTONS HANDLER (THE MAGIC HAPPENS HERE)
# ==============================================================================

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()
    action = query.data
    admin_id = str(update.effective_user.id)
    admin_name = update.effective_user.first_name

    # 1. استلام المحادثة (Taking the chat)
    if action.startswith('take_'):
        student_id = action.split('_')[1]

        # Check Lock
        if student_id in LOCKED_CHATS:
            current_owner = LOCKED_CHATS[student_id]['admin_name']
            if LOCKED_CHATS[student_id]['admin_id'] == admin_id:
                 await query.edit_message_text("أنت بالفعل تتحدث مع هذا الطالب! ✅")
            else:
                 await context.bot.answer_callback_query(query.id, text=f"⛔ توقف! الأدمن {current_owner} يرد عليه الآن.", show_alert=True)
            return

        # Apply Lock
        LOCKED_CHATS[student_id] = {'admin_id': admin_id, 'admin_name': admin_name}
        context.user_data['reply_to_student_id'] = student_id
        
        # زر إنهاء المحادثة
        keyboard = [[InlineKeyboardButton("❌ إنهاء المحادثة وإغلاق القفل", callback_data=f'end_{student_id}')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        student_name = data["students"].get(student_id, {}).get("name", "الطالب")
        await query.edit_message_text(
            f"🟢 **بدأت المحادثة مع {student_name}**\n🔒 تم قفل المحادثة باسمك.\nلا يمكن لأي أدمن آخر التدخل حتى تضغط إنهاء.", 
            reply_markup=reply_markup, 
            parse_mode=ParseMode.MARKDOWN
        )
        
        # إشعار للمراقب
        if CONTROLLER_ADMIN_ID and admin_id != CONTROLLER_ADMIN_ID:
            await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=f"🔒 الأدمن **{admin_name}** استلم الرد على الطالب `{student_id}`", parse_mode=ParseMode.MARKDOWN)

    # 2. إنهاء المحادثة (Ending the chat)
    elif action.startswith('end_'):
        student_id = action.split('_')[1]
        
        # التحقق من الصلاحية
        if student_id in LOCKED_CHATS and LOCKED_CHATS[student_id]['admin_id'] != admin_id:
             await context.bot.answer_callback_query(query.id, text="ليس لديك صلاحية لإنهاء محادثة زميلك!", show_alert=True)
             return

        if student_id in LOCKED_CHATS:
            del LOCKED_CHATS[student_id]
        
        if 'reply_to_student_id' in context.user_data:
            del context.user_data['reply_to_student_id']

        await query.edit_message_text(f"✅ **تم إنهاء المحادثة وفك القفل.**\nيمكن للآخرين الرد الآن.", parse_mode=ParseMode.MARKDOWN)
        
        if CONTROLLER_ADMIN_ID and admin_id != CONTROLLER_ADMIN_ID:
            await context.bot.send_message(chat_id=CONTROLLER_ADMIN_ID, text=f"🔓 الأدمن **{admin_name}** أنهى المحادثة مع `{student_id}`", parse_mode=ParseMode.MARKDOWN)

    # 3. أدوات الأدمن
    elif action == 'stats_btn':
        await query.message.reply_text(f"👥 الطلاب: {len(data.get('students', {}))}\n🔒 محادثات جارية: {len(LOCKED_CHATS)}")
    elif action == 'force_unlock_all':
        if admin_id == CONTROLLER_ADMIN_ID:
            LOCKED_CHATS.clear()
            await query.message.reply_text("🔓⚠️ تم فك قفل جميع المحادثات إجبارياً!")
        else:
            await context.bot.answer_callback_query(query.id, text="هذا الزر للمدير المراقب فقط!", show_alert=True)
    elif action == 'help_broadcast':
        await query.message.reply_text("📢 للبث: رد على رسالة بـ `/broadcast`", parse_mode=ParseMode.MARKDOWN)
    elif action == 'help_ban':
        await query.message.reply_text("🚫 `/ban ID` للحظر", parse_mode=ParseMode.MARKDOWN)

# ==============================================================================
# 6. ROUTER
# ==============================================================================
async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    if str(update.effective_user.id) in ADMIN_IDS:
        if context.user_data.get('reply_to_student_id'):
            if update.message.text and update.message.text.startswith('/'): return 
            await handle_admin_reply_mode(update, context, data)
        else:
            if not update.message.text or not update.message.text.startswith('/'):
                await update.message.reply_text("👋 أهلاً أدمن. انتظر رسائل الطلاب.")
    else:
        await handle_student_message(update, context, data)

# ==============================================================================
# 7. MAIN
# ==============================================================================
def main():
    start_keep_alive()
    bot_data = load_data()
    app = Application.builder().token(TOKEN).build()
    
    p = partial
    router_p = p(main_router, data=bot_data)
    btns_p = p(buttons_handler, data=bot_data)
    ban_p = p(ban_user_command, data=bot_data)
    unban_p = p(unban_user_command, data=bot_data)
    broad_p = p(broadcast_command, data=bot_data)
    
    start_p = p(lambda u,c,d: u.message.reply_text("أهلاً بك!"), data=bot_data) # Simple start for brevity
    
    admin_only = filters.User(user_id=[int(uid) for uid in ADMIN_IDS])

    app.add_handler(CommandHandler("start", partial(lambda u,c,d: u.message.reply_text("أهلاً بك! ابعت سؤالك."), data=bot_data)))
    app.add_handler(CommandHandler("admin", admin_panel, filters=admin_only))
    app.add_handler(CommandHandler("ban", ban_p, filters=admin_only))
    app.add_handler(CommandHandler("unban", unban_p, filters=admin_only))
    app.add_handler(CommandHandler("broadcast", broad_p, filters=admin_only))
    
    app.add_handler(CallbackQueryHandler(btns_p))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, router_p))

    print("Bot is Running Legendary Mode...")
    app.run_polling()

if __name__ == '__main__':
    main()
