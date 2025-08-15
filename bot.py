import os
import logging
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import (
    Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not set")

ADMINS = [6582122671, 861941692]

DB_FILE = "bot.db"
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

active_chats = {}
user_state = {}

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            phone TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS consultations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            datetime TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    conn.commit()
    conn.close()

def add_or_update_user(user_id, first_name, last_name, username, phone):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (user_id, first_name, last_name, username, phone)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name=excluded.first_name,
            last_name=excluded.last_name,
            username=excluded.username,
            phone=excluded.phone
    """, (user_id, first_name, last_name, username, phone))
    conn.commit()
    conn.close()

def get_user_phone(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT phone FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return None

def add_consultation(user_id, datetime_text):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO consultations (user_id, datetime) VALUES (?, ?)", (user_id, datetime_text))
    conn.commit()
    conn.close()

def fetch_consultations():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.user_id, u.username, u.phone, c.datetime, c.created_at
        FROM consultations c
        LEFT JOIN users u ON u.user_id = c.user_id
        ORDER BY c.id DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def fetch_users():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT user_id, first_name, last_name, username, phone FROM users ORDER BY user_id")
    rows = cur.fetchall()
    conn.close()
    return rows

def delete_consultation(consult_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM consultations WHERE id = ?", (consult_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

def delete_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM consultations WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'Bot is running 24/7!')
    
    def log_message(self, format, *args):
        # Вимкнути логування HTTP запитів
        pass

def run_server():
    server = HTTPServer(('0.0.0.0', 8080), KeepAliveHandler)
    server.serve_forever()

def start_keepalive():
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    logger.info("HTTP keepalive server запущено на порту 8080")

def main_menu_markup():
    return ReplyKeyboardMarkup(
        [
            ["Контекстна реклама", "Створення сайту"],
            ["Консультації", "Додавання міток на карту"]
        ], resize_keyboard=True
    )

def service_options_markup():
    return ReplyKeyboardMarkup(
        [["Зв’язатися з адміністрацією"], ["Повернутись на головну"]], resize_keyboard=True
    )

def admin_menu_markup():
    return ReplyKeyboardMarkup(
        [
            ["Переглянути консультації", "Переглянути користувачів"],
            ["Видалити акаунт"],
            ["Завершити чат"]
        ], resize_keyboard=True
    )

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    phone = get_user_phone(user.id)

    if user.id in ADMINS:
        await update.message.reply_text("Ласкаво просимо, Адміністраторе! Ось меню:", reply_markup=admin_menu_markup())
        return

    if not phone:
        kb = ReplyKeyboardMarkup([[KeyboardButton("Поділитися номером", request_contact=True)]],
                                 one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "Вітаю! Будь ласка, поділіться своїм номером телефону, щоб користуватись ботом:",
            reply_markup=kb
        )
    else:
        await update.message.reply_text("Ласкаво просимо! Ось меню:", reply_markup=main_menu_markup())

async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    user = update.effective_user
    phone = contact.phone_number
    add_or_update_user(user.id, user.first_name, user.last_name or "", user.username or "", phone)

    if user.id in ADMINS:
        await update.message.reply_text("Дякуємо! Номер отримано.", reply_markup=admin_menu_markup())
    else:
        await update.message.reply_text("Дякуємо! Тепер ви можете користуватися ботом.", reply_markup=main_menu_markup())

async def menu_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = get_user_phone(user_id)

    if user_id not in ADMINS and not phone:
        kb = ReplyKeyboardMarkup([[KeyboardButton("Поділитися номером", request_contact=True)]],
                                 one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text(
            "Будь ласка, спочатку поділіться своїм номером телефону, щоб користуватись ботом:",
            reply_markup=kb
        )
        return

    if user_id in active_chats:
        text = (update.message.text or "").strip()

        if user_id in ADMINS and text.startswith("/"):
            return

        if text.lower() == "завершити чат":
            target_id = active_chats[user_id]
            await context.bot.send_message(target_id, "Чат завершено.")
            await context.bot.send_message(user_id, "Чат завершено.")
            for k in (user_id, target_id):
                if k in active_chats:
                    del active_chats[k]

            if user_id in ADMINS:
                await update.message.reply_text("Адмін меню:", reply_markup=admin_menu_markup())
            else:
                await update.message.reply_text("Ось головне меню:", reply_markup=main_menu_markup())
            return

        return await relay_message(update, context)

    text = (update.message.text or "").strip()

    if user_id in ADMINS:
        if text == "Переглянути консультації":
            await consultations_command(update, context)
        elif text == "Переглянути користувачів":
            await users_command(update, context)
        elif text == "Видалити акаунт":
            await show_users_for_deletion(update, context)
        elif text == "Завершити чат":
            await update.message.reply_text("Ви не перебуваєте у чаті.", reply_markup=admin_menu_markup())
        else:
            await update.message.reply_text("Обрати кнопку меню:", reply_markup=admin_menu_markup())
        return

    if text == "Контекстна реклама":
        await update.message.reply_text("📢 Контекстна реклама допоможе швидко залучити клієнтів...\n\n",
                                        reply_markup=service_options_markup())
    elif text == "Створення сайту":
        await update.message.reply_text("💻 Створимо сучасний сайт під ваш бізнес...\n\n",
                                        reply_markup=service_options_markup())
    elif text == "Додавання міток на карту":
        await update.message.reply_text("🗺️ Додаємо мітки у Google Maps для вашого бізнесу...\n\n",
                                        reply_markup=service_options_markup())
    elif text == "Консультації":
        await update.message.reply_text(
            "💬 На консультації я поділюсь простими порадами, як вести профіль у Google Maps та самостійно просувати свою мітку, щоб вас легко знаходили клієнти."
        )
        await update.message.reply_text("Вкажіть бажану дату та час (наприклад: 15.08 14:00):", reply_markup=ReplyKeyboardRemove())
        user_state[user_id] = "awaiting_datetime"
    elif text == "Зв’язатися з адміністрацією":
        await update.message.reply_text("Запит на зв'язок відправлено адміністрації. Очікуйте відповіді.", reply_markup=main_menu_markup())
        for admin_id in ADMINS:
            await context.bot.send_message(admin_id,
                                           f"📩 Користувач хоче зв'язатися:\nID: {user_id}\nІм'я: {update.effective_user.full_name}")
    elif text == "Повернутись на головну":
        await update.message.reply_text("Ось головне меню:", reply_markup=main_menu_markup())
    else:
        if user_state.get(user_id) == "awaiting_datetime":
            datetime_text = text
            add_consultation(user_id, datetime_text)
            user_state[user_id] = None
            await update.message.reply_text("Дякуємо! Ми отримали ваш запит на консультацію.", reply_markup=main_menu_markup())
            for admin_id in ADMINS:
                await context.bot.send_message(admin_id,
                                               f"🗓 Нова заявка на консультацію:\nКористувач ID: {user_id}\n"
                                               f"Ім'я: {update.effective_user.full_name}\nДата/час: {datetime_text}")
        else:
            await update.message.reply_text("Невідома команда. Оберіть опцію з меню.", reply_markup=main_menu_markup())

async def show_users_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("⛔ У вас немає доступу до цієї команди.")
        return

    rows = fetch_users()
    if not rows:
        await update.message.reply_text("База користувачів порожня.")
        return

    lines = ["👥 Користувачі для видалення:"]
    keyboard = []
    for r in rows:
        u_id, first, last, uname, phone = r
        name = (first or "") + (" " + last if last else "")
        lines.append(f"{u_id} | {name.strip()} | @{(uname or '-')} | {phone or '-'}")
        if u_id != user_id:
            keyboard.append([InlineKeyboardButton(f"Видалити {u_id}", callback_data=f"deleteuser:{u_id}")])

    await update.message.reply_text("\n".join(lines),
                                   reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data.startswith("startchat:"):
        admin_id = query.from_user.id
        if admin_id not in ADMINS:
            await query.edit_message_text("У вас немає доступу.")
            return
        try:
            user_id = int(data.split(":", 1)[1])
        except Exception:
            await query.edit_message_text("Некоректні дані.")
            return

        if user_id == admin_id:
            await query.edit_message_text("❌ Неможливо почати чат із самим собою.")
            return

        active_chats[admin_id] = user_id
        active_chats[user_id] = admin_id

        await context.bot.send_message(admin_id, f"✅ Чат з користувачем {user_id} розпочато. Пиши повідомлення. Для завершення напишіть 'завершити чат'.")
        await context.bot.send_message(user_id, "👋 Адміністратор почав чат з вами. Ви можете писати. Для завершення напишіть 'завершити чат'.")

        await query.edit_message_text("Чат запущено ✅")

    elif data.startswith("deleteconsult:"):
        admin_id = query.from_user.id
        if admin_id not in ADMINS:
            await query.edit_message_text("У вас немає доступу.")
            return
        try:
            consult_id = int(data.split(":", 1)[1])
        except Exception:
            await query.edit_message_text("Некоректні дані.")
            return

        if delete_consultation(consult_id):
            await query.edit_message_text(f"✅ Консультацію #{consult_id} видалено.")
        else:
            await query.edit_message_text(f"❌ Консультація з ID #{consult_id} не знайдена.")

    elif data.startswith("deleteuser:"):
        admin_id = query.from_user.id
        if admin_id not in ADMINS:
            await query.edit_message_text("У вас немає доступу.")
            return
        try:
            user_to_delete = int(data.split(":", 1)[1])
        except Exception:
            await query.edit_message_text("Некоректні дані.")
            return

        if user_to_delete == admin_id:
            await query.edit_message_text("❌ Неможливо видалити власний акаунт.")
            return

        deleted = delete_user(user_to_delete)
        if deleted:
            await query.edit_message.edit_text(f"✅ Користувача {user_to_delete} видалено з бази.")
        else:
            await query.edit_message.edit_text(f"❌ Користувача {user_to_delete} не знайдено.")

async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user
    sender_id = sender.id
    if sender_id not in active_chats:
        return

    target_id = active_chats[sender_id]

    try:
        await context.bot.copy_message(chat_id=target_id,
                                       from_chat_id=sender_id,
                                       message_id=update.message.message_id)
    except Exception as e:
        logger.exception("Помилка при пересиланні повідомлення: %s", e)
        if update.message.text:
            await context.bot.send_message(target_id, update.message.text)
        else:
            await context.bot.send_message(sender_id, "Не вдалося переслати це повідомлення.")

async def consultations_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("⛔ У вас немає доступу до цієї команди.")
        return
    rows = fetch_consultations()
    if not rows:
        await update.message.reply_text("📭 Немає записів на консультації.")
        return

    lines = ["📋 Список консультацій (останні):"]
    keyboard = []
    for r in rows:
        c_id, u_id, username, phone, dt, created_at = r
        uname = username if username else "-"
        lines.append(f"#{c_id} | user_id:{u_id} | @{uname} | {phone or '-'} | {dt} | додано: {created_at}")
        keyboard.append([InlineKeyboardButton(f"Видалити #{c_id}", callback_data=f"deleteconsult:{c_id}")])

    await update.message.reply_text("\n".join(lines),
                                   reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("⛔ У вас немає доступу до цієї команди.")
        return
    rows = fetch_users()
    if not rows:
        await update.message.reply_text("База користувачів порожня.")
        return
    lines = ["👥 Користувачі:"]
    keyboard = []
    for r in rows:
        u_id, first, last, uname, phone = r
        name = (first or "") + (" " + last if last else "")
        lines.append(f"{u_id} | {name.strip()} | @{(uname or '-')} | {phone or '-'}")
        if u_id != user_id:
            keyboard.append([InlineKeyboardButton(f"Почати чат з {u_id}", callback_data=f"startchat:{u_id}")])

    await update.message.reply_text("\n".join(lines),
                                   reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

def main():
    init_db()
    start_keepalive()  # Запускаємо HTTP сервер для 24/7 роботи

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("consultations", consultations_command))
    app.add_handler(CommandHandler("users", users_command))

    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text_handler))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, relay_message))

    logger.info("Запуск бота...")
    app.run_polling()

if __name__ == "__main__":
    main()
