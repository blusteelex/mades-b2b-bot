import asyncio
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("BOT_TOKEN", "")
MANAGER_CHAT_ID = os.getenv("MANAGER_CHAT_ID", "")
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME", "")
MANAGER_ID_FILE = BASE_DIR / ".manager_chat_id"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler(BASE_DIR / "bot_runtime.log", encoding="utf-8")],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

CATEGORY_NAMES = {"outerwear": "🧥 Верхній одяг", "lightwear": "👚 Легкий одяг"}


def catalog():
    with open(BASE_DIR / "catalog.json", encoding="utf-8") as file:
        return json.load(file)


def find_product(product_id):
    return next((item for item in catalog() if item["id"] == product_id), None)


def configured_manager_id():
    if MANAGER_CHAT_ID:
        return MANAGER_CHAT_ID
    if MANAGER_ID_FILE.is_file():
        return MANAGER_ID_FILE.read_text(encoding="utf-8").strip()
    return ""


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👗 Відкрити каталог", callback_data="menu:catalog")],
        [InlineKeyboardButton("✨ Новинки", callback_data="collection:Новинка"), InlineKeyboardButton("🔥 Хіти", callback_data="collection:Хіт продажів")],
        [InlineKeyboardButton("📦 У наявності", callback_data="collection:У наявності")],
        [InlineKeyboardButton("🛒 Моя заявка", callback_data="cart:view"), InlineKeyboardButton("💬 Менеджер", callback_data="manager")],
    ])


async def replace(update: Update, text: str, markup: InlineKeyboardMarkup):
    query = update.callback_query
    await safe_answer(query)
    if query.message.photo:
        await query.edit_message_caption(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def safe_answer(query):
    """A late button click may no longer accept a spinner response, but its message can still be updated."""
    try:
        await query.answer()
    except BadRequest as error:
        logger.warning("Не вдалося підтвердити натискання кнопки: %s", error)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("cart", [])
    username = (update.effective_user.username or "").lstrip("@").lower()
    if MANAGER_USERNAME and username == MANAGER_USERNAME.lstrip("@").lower():
        MANAGER_ID_FILE.write_text(str(update.effective_user.id), encoding="utf-8")
        logger.info("Manager chat linked")
    text = (
        "<b>Оптова B2B-вітрина</b>\n\n"
        "Оберіть моделі, перегляньте розміри й кольори, а потім надішліть нам одну готову заявку."
    )
    await update.effective_message.reply_text(text, reply_markup=main_menu(), parse_mode=ParseMode.HTML)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Ваш Telegram ID: <code>{update.effective_user.id}</code>\n"
        "Скопіюйте його в MANAGER_CHAT_ID у файлі .env.",
        parse_mode=ParseMode.HTML,
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await replace(update, "<b>Каталог</b>\nОберіть розділ:", InlineKeyboardMarkup([
        [InlineKeyboardButton("🧥 Верхній одяг", callback_data="category:outerwear")],
        [InlineKeyboardButton("👚 Легкий одяг", callback_data="category:lightwear")],
        [InlineKeyboardButton("🌸 Весна–літо", callback_data="season:summer"), InlineKeyboardButton("🍂 Демісезон", callback_data="season:demi")],
        [InlineKeyboardButton("❄️ Зима", callback_data="season:winter")],
        [InlineKeyboardButton("← До меню", callback_data="home")],
    ]))


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, products, heading):
    if not products:
        await replace(update, "У цьому розділі поки немає моделей.", InlineKeyboardMarkup([[InlineKeyboardButton("← До каталогу", callback_data="menu:catalog")]]))
        return
    buttons = [[InlineKeyboardButton(f"{p['title']} · {p['price']}", callback_data=f"product:{p['id']}")] for p in products]
    buttons.append([InlineKeyboardButton("← До каталогу", callback_data="menu:catalog")])
    await replace(update, f"<b>{heading}</b>\nОберіть модель:", InlineKeyboardMarkup(buttons))


async def browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind, value = update.callback_query.data.split(":", 1)
    products = [p for p in catalog() if p.get(kind) == value] if kind in {"category", "season"} else [p for p in catalog() if p.get("tag") == value]
    title = CATEGORY_NAMES.get(value, {"summer": "🌸 Весна–літо", "demi": "🍂 Демісезон", "winter": "❄️ Зима"}.get(value, f"{value}"))
    await show_products(update, context, products, title)


async def product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product_id = update.callback_query.data.split(":", 1)[1]
    item = find_product(product_id)
    if not item:
        await update.callback_query.answer("Модель не знайдена. Оновіть каталог.", show_alert=True)
        return
    context.user_data["current_product"] = product_id
    text = (
        f"<b>{item['title']}</b>\n"
        f"Артикул: <code>{item['id']}</code>\n"
        f"{item['tag']}\n\n{item['description']}\n\n"
        f"<b>Опт:</b> {item['price']}\n"
        f"<b>Розміри:</b> {', '.join(item['sizes'])}\n"
        f"<b>Мінімальне замовлення:</b> від {len(item['sizes'])} шт. (1 лінійка)\n"
        f"<b>Кольори:</b> {', '.join(item['colors'])}"
    )
    buttons = [[InlineKeyboardButton(color, callback_data=f"color:{product_id}:{i}")] for i, color in enumerate(item["colors"])]
    buttons.append([InlineKeyboardButton("← До моделей", callback_data=f"category:{item['category']}")])
    markup = InlineKeyboardMarkup(buttons)
    card = text + "\n\n<b>Оберіть колір для заявки:</b>"
    query = update.callback_query
    await safe_answer(query)
    photo_path = BASE_DIR / item.get("photo", "")
    if item.get("photo") and photo_path.is_file():
        with photo_path.open("rb") as photo:
            await query.message.reply_photo(photo=photo, caption=card, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(card, reply_markup=markup, parse_mode=ParseMode.HTML)


async def choose_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, product_id, color_index = update.callback_query.data.split(":")
    item = find_product(product_id)
    context.user_data["selection"] = {"id": product_id, "color": item["colors"][int(color_index)]}
    buttons = [[InlineKeyboardButton(str(lines), callback_data=f"line:{product_id}:{lines}") for lines in range(1, 6)]]
    buttons.append([InlineKeyboardButton("← До картки", callback_data=f"product:{product_id}")])
    await replace(
        update,
        f"<b>{item['title']}</b>\n"
        f"Колір: <b>{context.user_data['selection']['color']}</b>\n\n"
        f"<b>Скільки лінійок хочете замовити?</b>\n"
        f"1 лінійка = {', '.join(item['sizes'])} ({len(item['sizes'])} шт.)",
        InlineKeyboardMarkup(buttons),
    )


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, product_id, lines = update.callback_query.data.split(":")
    selection = context.user_data.get("selection", {})
    item = find_product(product_id)
    line_count = int(lines)
    entry = {
        "id": product_id,
        "title": item["title"],
        "price": item["price"],
        "color": selection.get("color"),
        "sizes": item["sizes"],
        "lines": line_count,
        "pieces": line_count * len(item["sizes"]),
    }
    context.user_data.setdefault("cart", []).append(entry)
    await replace(update, "✅ Позицію додано до заявки.", InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Продовжити вибір", callback_data="menu:catalog")],
        [InlineKeyboardButton("🛒 Переглянути заявку", callback_data="cart:view")],
    ]))


def line_word(count):
    if count % 10 == 1 and count % 100 != 11:
        return "лінійка"
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return "лінійки"
    return "лінійок"


def cart_text(cart):
    if not cart:
        return "<b>Моя заявка</b>\n\nУ заявці поки немає моделей."
    rows = ["<b>Моя заявка</b>\n"]
    for i, entry in enumerate(cart, 1):
        rows.append(
            f"{i}. <b>{entry['title']}</b> ({entry['id']})\n"
            f"   {entry['color']} · {entry['lines']} {line_word(entry['lines'])} · "
            f"{', '.join(entry['sizes'])} · {entry['pieces']} шт. · {entry['price']}"
        )
    rows.append("\nПеревірте позиції та надішліть заявку менеджеру.")
    return "\n".join(rows)


async def cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = context.user_data.setdefault("cart", [])
    buttons = []
    if items:
        buttons.append([InlineKeyboardButton("✅ Надіслати заявку", callback_data="cart:send")])
        buttons.append([InlineKeyboardButton("🗑 Очистити", callback_data="cart:clear")])
    buttons.append([InlineKeyboardButton("← До меню", callback_data="home")])
    await replace(update, cart_text(items), InlineKeyboardMarkup(buttons))


async def clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cart"] = []
    await cart(update, context)


async def send_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = context.user_data.get("cart", [])
    if not items:
        await update.callback_query.answer("Заявка поки порожня.", show_alert=True)
        return
    user = update.effective_user
    username = f"@{user.username}" if user.username else "немає username"
    request = "<b>🛒 Нова B2B-заявка</b>\n\n" + cart_text(items) + f"\n\n<b>Клієнт:</b> {user.full_name}\n<b>Telegram:</b> {username}\n<b>ID:</b> <code>{user.id}</code>"
    manager_chat_id = configured_manager_id()
    if not manager_chat_id or manager_chat_id == "123456789":
        logger.warning("MANAGER_CHAT_ID is not configured")
        await replace(update, "Заявку сформовано, але менеджера ще не підключено. Будь ласка, напишіть нам у приватні повідомлення.", main_menu())
        return
    await context.bot.send_message(
        chat_id=manager_chat_id,
        text=request,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Написати клієнту", url=f"tg://user?id={user.id}")],
        ]),
    )
    context.user_data["cart"] = []
    await replace(update, "✅ Заявку надіслано менеджеру. Незабаром ми зв’яжемося з вами для підтвердження наявності й оплати.", main_menu())


async def manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = []
    if MANAGER_USERNAME:
        buttons.append([InlineKeyboardButton("💬 Написати менеджеру", url=f"https://t.me/{MANAGER_USERNAME.lstrip('@')}")])
    buttons.append([InlineKeyboardButton("← До меню", callback_data="home")])
    await replace(update, "Уточнити наявність, терміни й умови співпраці можна безпосередньо у менеджера.", InlineKeyboardMarkup(buttons))


async def home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await replace(update, "<b>Оптова B2B-вітрина</b>\n\nОберіть потрібний розділ:", main_menu())


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Помилка обробки оновлення", exc_info=context.error)


def create_application():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CallbackQueryHandler(home, pattern="^home$"))
    app.add_handler(CallbackQueryHandler(menu, pattern="^menu:catalog$"))
    app.add_handler(CallbackQueryHandler(browse, pattern="^(category|season|collection):"))
    app.add_handler(CallbackQueryHandler(product, pattern="^product:"))
    app.add_handler(CallbackQueryHandler(choose_color, pattern="^color:"))
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern="^line:"))
    app.add_handler(CallbackQueryHandler(cart, pattern="^cart:view$"))
    app.add_handler(CallbackQueryHandler(clear_cart, pattern="^cart:clear$"))
    app.add_handler(CallbackQueryHandler(send_cart, pattern="^cart:send$"))
    app.add_handler(CallbackQueryHandler(manager, pattern="^manager$"))
    app.add_error_handler(on_error)
    return app


def main():
    if not TOKEN or TOKEN == "PASTE_NEW_TOKEN_HERE":
        raise RuntimeError("Створіть .env за прикладом .env.example та вкажіть BOT_TOKEN.")
    while True:
        # Python 3.14 no longer creates a default event loop automatically.
        asyncio.set_event_loop(asyncio.new_event_loop())
        app = create_application()
        try:
            logger.info("Bot is running")
            # This runs inside Flask's background thread on Render, where
            # signal handlers are not permitted.
            app.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)
            return
        except NetworkError:
            logger.exception("Зв’язок із Telegram втрачено. Нова спроба через 10 секунд.")
            time.sleep(10)


if __name__ == "__main__":
    main()
