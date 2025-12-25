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

    if user_id in data.get("banned", []):
        return

    # تسجيل الطالب
    if user_id not in data["students"]:
        data["students"][user_id] = {
            "name": user.first_name,
            "username": user.username,
            "joined": str(datetime.now()),
        }
        save_data(data)
        if CONTROLLER_ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=int(CONTROLLER_ADMIN_ID),
                    text=f"➕ طالب جديد: {user.first_name}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

    # لو الطالب في محادثة مطوّلة مقفولة: ابعت للأدمن المسؤول فقط
    if user_id in LOCKED_CHATS:
        admin_id = int(LOCKED_CHATS[user_id]['admin_id'])
        try:
            fwd = await context.bot.forward_message(
                chat_id=admin_id,
                from_chat_id=update.effective_chat.id,
                message_id=update.effective_message.message_id,
            )
            ADMIN_MSG_MAP.setdefault(str(admin_id), {})[fwd.message_id] = user_id

            kb = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data=f"end_{user_id}")]]
            await context.bot.send_message(
                chat_id=admin_id,
                text="رد بالريبلاي على الرسالة اللي فوق.
لو في وضع الرسالة المطوّلة: ابعت أجزاء الرسالة وبعدين اضغط إنهاء.",
                reply_to_message_id=fwd.message_id,
                reply_markup=InlineKeyboardMarkup(kb),
            )
        except Exception:
            pass
        return

    # لو مش محجوز: طمّن الطالب وابعت رسالته للأدمنز
    try:
        await update.message.reply_text('تم الاستلام، سيتم الرد قريباً.. ⏳')
    except Exception:
        pass

    for admin_id in ADMIN_IDS:
        try:
            admin_int = int(admin_id)

            fwd = await context.bot.forward_message(
                chat_id=admin_int,
                from_chat_id=update.effective_chat.id,
                message_id=update.effective_message.message_id,
            )

            # تخزين ربط الرسالة بالطالب عشان الأدمن يرد بالـ Reply
            ADMIN_MSG_MAP.setdefault(str(admin_int), {})[fwd.message_id] = user_id

            # رسالة تعليمات + زر الرسالة المطوّلة
            kb = [[InlineKeyboardButton("✍️ رد برسالة مطوّلة", callback_data=f"long_{user_id}")]]
            info = (
                f"📩 من: {user.first_name} (ID: {user_id})
"
                f"@{user.username or 'NoUser'}

"
                "- للرد السريع: اعمل Reply على رسالة الطالب نفسها واكتب ردّك.
"
                "- لو محتاج تجمع رد طويل: اضغط زر (رد برسالة مطوّلة)."
            )
            await context.bot.send_message(
                chat_id=admin_int,
                text=info,
                reply_to_message_id=fwd.message_id,
                reply_markup=InlineKeyboardMarkup(kb),
            )

            # قصّ الماب لو كبر (استقرار RAM)
            m = ADMIN_MSG_MAP.get(str(admin_int), {})
            if len(m) > 500:
                # امسح أقدم 200 عنصر
                for k in sorted(m.keys())[:200]:
                    m.pop(k, None)

        except Exception:
            pass

async def _extract_student_id_from_reply(admin_id: str, replied_msg):
    # 1) لو Telegram رجّع forward_from (مش دايماً بسبب الخصوصية)
    try:
        if replied_msg.forward_from and replied_msg.forward_from.id:
            return str(replied_msg.forward_from.id)
    except Exception:
        pass

    # 2) fallback: استخدم الماب اللي خزّنّاه وقت الإرسال
    try:
        return ADMIN_MSG_MAP.get(admin_id, {}).get(replied_msg.message_id)
    except Exception:
        return None


async def _send_admin_message_to_student(update: Update, context: ContextTypes.DEFAULT_TYPE, student_id: str):
    try:
        await context.bot.send_chat_action(chat_id=int(student_id), action=ChatAction.TYPING)
    except Exception:
        pass

    # copy أفضل من forward عشان ميظهرش عند الطالب إنه فوروورد من الأدمن
    await update.effective_message.copy(chat_id=int(student_id))


async def handle_admin_reply_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    admin_id = str(update.effective_user.id)
    msg = update.effective_message

    # 1) وضع الرسالة المطوّلة: نجمع أجزاء لحد ما يضغط إنهاء
    if admin_id in LONG_SESSIONS:
        if msg.text and msg.text.startswith('/'):
            return
        # نخزّن النصوص فقط (لو محتاج وسائط ابعته كـ Reply عادي خارج الوضع ده)
        text = msg.text or msg.caption
        if text:
            LONG_SESSIONS[admin_id]['parts'].append(text)
            await msg.reply_text("✅ تم حفظ الجزء. ابعت جزء تاني أو اضغط (إنهاء المحادثة).")
        else:
            await msg.reply_text("⚠️ الوضع المطوّل بيجمع نصوص فقط. ابعت نص أو اخرج من الوضع بالضغط على إنهاء.")
        return

    # 2) الرد السريع: لازم يبقى Reply على رسالة الطالب (اللي البوت بعتها للأدمن)
    if not msg.reply_to_message:
        return

    student_id = await _extract_student_id_from_reply(admin_id, msg.reply_to_message)
    if not student_id:
        await msg.reply_text("⚠️ مش قادر أحدد الطالب. لازم تعمل Reply على رسالة الطالب اللي وصلتلك من البوت.")
        return

    # لو في قفل، لازم الأدمن صاحب القفل
    if student_id in LOCKED_CHATS and LOCKED_CHATS[student_id]['admin_id'] != admin_id:
        await msg.reply_text("⛔ المحادثة دي مقفولة عند أدمن تاني حالياً.")
        return

    try:
        await _send_admin_message_to_student(update, context, student_id)
    except Exception as e:
        await msg.reply_text(f"❌ لم تصل الرسالة (الطالب حظر البوت؟): {e}")


# ==============================================================================
# 5. BUTTONS HANDLER (THE MAGIC HAPPENS HERE)
# ==============================================================================

async def buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    query = update.callback_query
    await query.answer()

    action = query.data
    admin_id = str(update.effective_user.id)
    admin_name = update.effective_user.first_name

    # بدء وضع رسالة مطوّلة
    if action.startswith('long_'):
        student_id = action.split('_', 1)[1]

        # لو في قفل لشخص تاني
        if student_id in LOCKED_CHATS and LOCKED_CHATS[student_id]['admin_id'] != admin_id:
            current_owner = LOCKED_CHATS[student_id]['admin_name']
            await context.bot.answer_callback_query(query.id, text=f"⛔ الأدمن {current_owner} ماسك المحادثة دي.", show_alert=True)
            return

        # فعّل القفل وابدأ سيشن تجميع
        LOCKED_CHATS[student_id] = {'admin_id': admin_id, 'admin_name': admin_name}
        LONG_SESSIONS[admin_id] = {'student_id': student_id, 'parts': []}

        kb = [[InlineKeyboardButton("❌ إنهاء المحادثة", callback_data=f'end_{student_id}')]]
        student_name = data.get('students', {}).get(student_id, {}).get('name', 'الطالب')

        await query.message.reply_text(
            f"🟢 دخلت وضع الرسالة المطوّلة مع {student_name}.
"
            "ابعت أجزاء الرسالة (نصوص) وانا هاجمعها، ولما تخلص اضغط (إنهاء المحادثة).",
            reply_markup=InlineKeyboardMarkup(kb),
        )

        if CONTROLLER_ADMIN_ID and admin_id != str(CONTROLLER_ADMIN_ID):
            try:
                await context.bot.send_message(
                    chat_id=int(CONTROLLER_ADMIN_ID),
                    text=f"🔒 الأدمن **{admin_name}** بدأ رسالة مطوّلة للطالب `{student_id}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        return

    # إنهاء المحادثة / إرسال الرسالة المطوّلة وفك القفل
    if action.startswith('end_'):
        student_id = action.split('_', 1)[1]

        # صلاحيات إنهاء القفل
        if student_id in LOCKED_CHATS and LOCKED_CHATS[student_id]['admin_id'] != admin_id:
            await context.bot.answer_callback_query(query.id, text="ليس لديك صلاحية لإنهاء محادثة زميلك!", show_alert=True)
            return

        # لو كان في سيشن مطوّل: ابعت الرسالة المجمعة
        session = LONG_SESSIONS.get(admin_id)
        if session and session.get('student_id') == student_id:
            parts = [p.strip() for p in session.get('parts', []) if p and p.strip()]
            text = "

".join(parts).strip()

            if text:
                # لو النص كبير، قسّمه
                max_len = 3800
                chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
                for ch in chunks:
                    try:
                        await context.bot.send_message(chat_id=int(student_id), text=ch)
                    except Exception as e:
                        await query.message.reply_text(f"❌ فشل الإرسال للطالب: {e}")
                        break
            else:
                await query.message.reply_text("⚠️ مفيش نصوص متجمعة للإرسال.")

            LONG_SESSIONS.pop(admin_id, None)

        # فك القفل
        if student_id in LOCKED_CHATS:
            del LOCKED_CHATS[student_id]

        await query.message.reply_text("✅ تم إنهاء المحادثة وفك القفل.")

        if CONTROLLER_ADMIN_ID and admin_id != str(CONTROLLER_ADMIN_ID):
            try:
                await context.bot.send_message(
                    chat_id=int(CONTROLLER_ADMIN_ID),
                    text=f"🔓 الأدمن **{admin_name}** أنهى المحادثة مع `{student_id}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        return

    # أدوات الأدمن
    if action == 'stats_btn':
        await query.message.reply_text(f"👥 الطلاب: {len(data.get('students', {}))}
🔒 محادثات جارية: {len(LOCKED_CHATS)}")

    elif action == 'force_unlock_all':
        if admin_id == str(CONTROLLER_ADMIN_ID):
            LOCKED_CHATS.clear()
            LONG_SESSIONS.clear()
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
    user_id = str(update.effective_user.id)

    # رسائل الأدمن
    if user_id in ADMIN_IDS:
        if update.effective_message.text and update.effective_message.text.startswith('/'):
            return
        await handle_admin_reply_mode(update, context, data)
        return

    # رسائل الطلاب
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
