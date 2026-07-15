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

CATEGORY_NAMES = {"outerwear": "🧥 Верхняя одежда", "lightwear": "👚 Лёгкая одежда"}


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
        [InlineKeyboardButton("👗 Открыть каталог", callback_data="menu:catalog")],
        [InlineKeyboardButton("✨ Новинки", callback_data="collection:Новинка"), InlineKeyboardButton("🔥 Хиты", callback_data="collection:Хит продаж")],
        [InlineKeyboardButton("📦 В наличии", callback_data="collection:В наличии")],
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
        logger.warning("Не удалось подтвердить нажатие кнопки: %s", error)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("cart", [])
    username = (update.effective_user.username or "").lstrip("@").lower()
    if MANAGER_USERNAME and username == MANAGER_USERNAME.lstrip("@").lower():
        MANAGER_ID_FILE.write_text(str(update.effective_user.id), encoding="utf-8")
        logger.info("Manager chat linked")
    text = (
        "<b>Оптовая B2B-витрина</b>\n\n"
        "Подберите модели, посмотрите размеры и цвета, затем отправьте нам одну готовую заявку."
    )
    await update.effective_message.reply_text(text, reply_markup=main_menu(), parse_mode=ParseMode.HTML)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"Ваш Telegram ID: <code>{update.effective_user.id}</code>\n"
        "Скопируйте его в MANAGER_CHAT_ID в файле .env.",
        parse_mode=ParseMode.HTML,
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await replace(update, "<b>Каталог</b>\nВыберите раздел:", InlineKeyboardMarkup([
        [InlineKeyboardButton("🧥 Верхняя одежда", callback_data="category:outerwear")],
        [InlineKeyboardButton("👚 Лёгкая одежда", callback_data="category:lightwear")],
        [InlineKeyboardButton("🌸 Весна–лето", callback_data="season:summer"), InlineKeyboardButton("🍂 Демисезон", callback_data="season:demi")],
        [InlineKeyboardButton("❄️ Зима", callback_data="season:winter")],
        [InlineKeyboardButton("← В меню", callback_data="home")],
    ]))


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE, products, heading):
    if not products:
        await replace(update, "В этом разделе пока нет моделей.", InlineKeyboardMarkup([[InlineKeyboardButton("← К каталогу", callback_data="menu:catalog")]]))
        return
    buttons = [[InlineKeyboardButton(f"{p['title']} · {p['price']}", callback_data=f"product:{p['id']}")] for p in products]
    buttons.append([InlineKeyboardButton("← К каталогу", callback_data="menu:catalog")])
    await replace(update, f"<b>{heading}</b>\nВыберите модель:", InlineKeyboardMarkup(buttons))


async def browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind, value = update.callback_query.data.split(":", 1)
    products = [p for p in catalog() if p.get(kind) == value] if kind in {"category", "season"} else [p for p in catalog() if p.get("tag") == value]
    title = CATEGORY_NAMES.get(value, {"summer": "🌸 Весна–лето", "demi": "🍂 Демисезон", "winter": "❄️ Зима"}.get(value, f"{value}"))
    await show_products(update, context, products, title)


async def product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product_id = update.callback_query.data.split(":", 1)[1]
    item = find_product(product_id)
    if not item:
        await update.callback_query.answer("Модель не найдена. Обновите каталог.", show_alert=True)
        return
    context.user_data["current_product"] = product_id
    text = (
        f"<b>{item['title']}</b>\n"
        f"Артикул: <code>{item['id']}</code>\n"
        f"{item['tag']}\n\n{item['description']}\n\n"
        f"<b>Опт:</b> {item['price']}\n"
        f"<b>Размеры:</b> {', '.join(item['sizes'])}\n"
        f"<b>Цвета:</b> {', '.join(item['colors'])}"
    )
    buttons = [[InlineKeyboardButton(color, callback_data=f"color:{product_id}:{i}")] for i, color in enumerate(item["colors"])]
    buttons.append([InlineKeyboardButton("← К моделям", callback_data=f"category:{item['category']}")])
    markup = InlineKeyboardMarkup(buttons)
    card = text + "\n\n<b>Выберите цвет для заявки:</b>"
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
    buttons = [[InlineKeyboardButton(size, callback_data=f"size:{product_id}:{size}") for size in item["sizes"]]]
    buttons.append([InlineKeyboardButton("← К карточке", callback_data=f"product:{product_id}")])
    await replace(update, f"<b>{item['title']}</b>\nЦвет: <b>{context.user_data['selection']['color']}</b>\n\nВыберите размер:", InlineKeyboardMarkup(buttons))


async def choose_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, product_id, size = update.callback_query.data.split(":")
    context.user_data["selection"]["size"] = size
    buttons = [[InlineKeyboardButton(str(qty), callback_data=f"qty:{product_id}:{qty}") for qty in range(1, 6)], [InlineKeyboardButton("← К карточке", callback_data=f"product:{product_id}")]]
    await replace(update, f"Размер: <b>{size}</b>\n\nВыберите количество:", InlineKeyboardMarkup(buttons))


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, product_id, qty = update.callback_query.data.split(":")
    selection = context.user_data.get("selection", {})
    item = find_product(product_id)
    entry = {"id": product_id, "title": item["title"], "price": item["price"], "color": selection.get("color"), "size": selection.get("size"), "qty": int(qty)}
    context.user_data.setdefault("cart", []).append(entry)
    await replace(update, "✅ Позиция добавлена в заявку.", InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Продолжить выбор", callback_data="menu:catalog")],
        [InlineKeyboardButton("🛒 Посмотреть заявку", callback_data="cart:view")],
    ]))


def cart_text(cart):
    if not cart:
        return "<b>Моя заявка</b>\n\nПока в заявке нет моделей."
    rows = ["<b>Моя заявка</b>\n"]
    for i, entry in enumerate(cart, 1):
        rows.append(f"{i}. <b>{entry['title']}</b> ({entry['id']})\n   {entry['color']} · размер {entry['size']} · {entry['qty']} шт. · {entry['price']}")
    rows.append("\nПроверьте позиции и отправьте заявку менеджеру.")
    return "\n".join(rows)


async def cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = context.user_data.setdefault("cart", [])
    buttons = []
    if items:
        buttons.append([InlineKeyboardButton("✅ Отправить заявку", callback_data="cart:send")])
        buttons.append([InlineKeyboardButton("🗑 Очистить", callback_data="cart:clear")])
    buttons.append([InlineKeyboardButton("← В меню", callback_data="home")])
    await replace(update, cart_text(items), InlineKeyboardMarkup(buttons))


async def clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cart"] = []
    await cart(update, context)


async def send_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = context.user_data.get("cart", [])
    if not items:
        await update.callback_query.answer("Заявка пока пуста.", show_alert=True)
        return
    user = update.effective_user
    request = "<b>🛒 Новая B2B-заявка</b>\n\n" + cart_text(items) + f"\n\n<b>Клиент:</b> {user.full_name}\n<b>Telegram:</b> @{user.username or 'не указан'}\n<b>ID:</b> <code>{user.id}</code>"
    manager_chat_id = configured_manager_id()
    if not manager_chat_id or manager_chat_id == "123456789":
        logger.warning("MANAGER_CHAT_ID is not configured")
        await replace(update, "Заявка собрана, но менеджер ещё не подключён. Пожалуйста, напишите нам в личные сообщения.", main_menu())
        return
    await context.bot.send_message(chat_id=manager_chat_id, text=request, parse_mode=ParseMode.HTML)
    context.user_data["cart"] = []
    await replace(update, "✅ Заявка отправлена менеджеру. Мы скоро свяжемся с вами для подтверждения наличия и оплаты.", main_menu())


async def manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = []
    if MANAGER_USERNAME:
        buttons.append([InlineKeyboardButton("💬 Написать менеджеру", url=f"https://t.me/{MANAGER_USERNAME.lstrip('@')}")])
    buttons.append([InlineKeyboardButton("← В меню", callback_data="home")])
    await replace(update, "Уточнить наличие, сроки и условия сотрудничества можно напрямую у менеджера.", InlineKeyboardMarkup(buttons))


async def home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await replace(update, "<b>Оптовая B2B-витрина</b>\n\nВыберите нужный раздел:", main_menu())


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Ошибка обработки обновления", exc_info=context.error)


def create_application():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CallbackQueryHandler(home, pattern="^home$"))
    app.add_handler(CallbackQueryHandler(menu, pattern="^menu:catalog$"))
    app.add_handler(CallbackQueryHandler(browse, pattern="^(category|season|collection):"))
    app.add_handler(CallbackQueryHandler(product, pattern="^product:"))
    app.add_handler(CallbackQueryHandler(choose_color, pattern="^color:"))
    app.add_handler(CallbackQueryHandler(choose_size, pattern="^size:"))
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern="^qty:"))
    app.add_handler(CallbackQueryHandler(cart, pattern="^cart:view$"))
    app.add_handler(CallbackQueryHandler(clear_cart, pattern="^cart:clear$"))
    app.add_handler(CallbackQueryHandler(send_cart, pattern="^cart:send$"))
    app.add_handler(CallbackQueryHandler(manager, pattern="^manager$"))
    app.add_error_handler(on_error)
    return app


def main():
    if not TOKEN or TOKEN == "PASTE_NEW_TOKEN_HERE":
        raise RuntimeError("Создайте .env по примеру .env.example и укажите BOT_TOKEN.")
    while True:
        # Python 3.14 no longer creates a default event loop automatically.
        asyncio.set_event_loop(asyncio.new_event_loop())
        app = create_application()
        try:
            logger.info("Bot is running")
            app.run_polling(allowed_updates=Update.ALL_TYPES)
            return
        except NetworkError:
            logger.exception("Связь с Telegram потеряна. Новая попытка через 10 секунд.")
            time.sleep(10)


if __name__ == "__main__":
    main()
