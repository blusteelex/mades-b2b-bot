import asyncio
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters


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


def product_photo_path(item):
    """Find a product image in the catalog photo folder or next to the bot."""
    filename = item.get("photo", "")
    if not filename:
        return None
    for path in (BASE_DIR / "photos" / filename, BASE_DIR / filename):
        if path.is_file():
            return path
    return None


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
    context.user_data["browse_product_ids"] = [item["id"] for item in products]
    context.user_data["browse_heading"] = heading
    await show_product_card(update, context, products, 0, heading)


def product_card_markup(item, index, total):
    buttons = []
    navigation = []
    if index > 0:
        navigation.append(InlineKeyboardButton("←", callback_data=f"browsepage:{index - 1}"))
    navigation.append(InlineKeyboardButton(f"{index + 1} / {total}", callback_data="noop"))
    if index < total - 1:
        navigation.append(InlineKeyboardButton("→", callback_data=f"browsepage:{index + 1}"))
    buttons.append(navigation)
    buttons.append([InlineKeyboardButton("🛍 Обрати цю модель", callback_data=f"product:{item['id']}")])
    buttons.append([InlineKeyboardButton("← До каталогу", callback_data="menu:catalog")])
    return InlineKeyboardMarkup(buttons)


def product_card_caption(item, heading, index, total):
    return (
        f"<b>{heading}</b>\n\n"
        f"<b>{item['title']}</b>\n"
        f"Артикул: <code>{item['id']}</code> · {item.get('tag', '')}\n\n"
        f"<b>Оптова ціна:</b> {item['price']}\n"
        f"<b>Розміри в лінійці:</b> {', '.join(item['sizes'])}\n"
        f"<b>Кольори:</b> {', '.join(item['colors'])}\n\n"
        f"Модель {index + 1} із {total}. Оберіть її або перегляньте наступну."
    )


async def show_product_card(update: Update, context: ContextTypes.DEFAULT_TYPE, products, index, heading):
    item = products[index]
    context.user_data["browse_index"] = index
    caption = product_card_caption(item, heading, index, len(products))
    markup = product_card_markup(item, index, len(products))
    query = update.callback_query
    await safe_answer(query)
    photo_path = product_photo_path(item)

    if photo_path and query.message.photo:
        with photo_path.open("rb") as photo:
            await query.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=caption, parse_mode=ParseMode.HTML),
                reply_markup=markup,
            )
        return

    if photo_path:
        with photo_path.open("rb") as photo:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=photo,
                caption=caption,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
        await query.message.delete()
        return

    await query.edit_message_text(
        caption + "\n\n<i>Фото цієї моделі ще додається.</i>",
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


async def browse_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        index = int(update.callback_query.data.split(":", 1)[1])
        ids = context.user_data["browse_product_ids"]
        products = [find_product(product_id) for product_id in ids]
        products = [item for item in products if item]
        heading = context.user_data["browse_heading"]
        if not products or index < 0 or index >= len(products):
            raise ValueError
    except (KeyError, ValueError):
        await update.callback_query.answer("Каталог оновлено. Відкрийте розділ ще раз.", show_alert=True)
        return
    await show_product_card(update, context, products, index, heading)


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_answer(update.callback_query)


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
    browse_ids = context.user_data.get("browse_product_ids", [])
    if product_id in browse_ids:
        back_callback = f"browsepage:{context.user_data.get('browse_index', 0)}"
    else:
        back_callback = f"category:{item['category']}"
    buttons.append([InlineKeyboardButton("← До моделей", callback_data=back_callback)])
    markup = InlineKeyboardMarkup(buttons)
    card = text + "\n\n<b>Оберіть колір для заявки:</b>"
    query = update.callback_query
    await safe_answer(query)
    photo_path = product_photo_path(item)
    if photo_path:
        if query.message.photo:
            await query.edit_message_caption(card, reply_markup=markup, parse_mode=ParseMode.HTML)
            return
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
    context.user_data["awaiting_contact"] = True
    await safe_answer(update.callback_query)
    await update.callback_query.message.reply_text(
        "<b>Ще один крок</b>\n\n"
        "Щоб менеджер міг одразу зв’язатися з вами щодо заявки, "
        "натисніть кнопку нижче та поділіться номером телефону.",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Поділитися контактом", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
        parse_mode=ParseMode.HTML,
    )


async def receive_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_contact"):
        return
    user = update.effective_user
    contact = update.effective_message.contact
    if contact.user_id and contact.user_id != user.id:
        await update.effective_message.reply_text(
            "Будь ласка, надішліть свій власний контакт.",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 Поділитися контактом", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return

    items = context.user_data.get("cart", [])
    if not items:
        context.user_data["awaiting_contact"] = False
        await update.effective_message.reply_text(
            "Заявка поки порожня.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    user = update.effective_user
    username = f"@{user.username}" if user.username else "немає username"
    request = (
        "<b>🛒 Нова B2B-заявка</b>\n\n"
        + cart_text(items)
        + f"\n\n<b>Клієнт:</b> {user.full_name}"
        + f"\n<b>Телефон:</b> {contact.phone_number}"
        + f"\n<b>Telegram:</b> {username}"
        + f"\n<b>ID:</b> <code>{user.id}</code>"
    )
    manager_chat_id = configured_manager_id()
    if not manager_chat_id or manager_chat_id == "123456789":
        logger.warning("MANAGER_CHAT_ID is not configured")
        context.user_data["awaiting_contact"] = False
        await update.effective_message.reply_text(
            "Заявку сформовано, але менеджера ще не підключено. Будь ласка, напишіть нам у приватні повідомлення.",
            reply_markup=ReplyKeyboardRemove(),
        )
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
    context.user_data["awaiting_contact"] = False
    await update.effective_message.reply_text(
        "✅ Заявку надіслано менеджеру. Незабаром ми зв’яжемося з вами для підтвердження наявності й оплати.",
        reply_markup=ReplyKeyboardRemove(),
    )


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
    app.add_handler(CallbackQueryHandler(browse_page, pattern="^browsepage:"))
    app.add_handler(CallbackQueryHandler(noop, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(product, pattern="^product:"))
    app.add_handler(CallbackQueryHandler(choose_color, pattern="^color:"))
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern="^line:"))
    app.add_handler(CallbackQueryHandler(cart, pattern="^cart:view$"))
    app.add_handler(CallbackQueryHandler(clear_cart, pattern="^cart:clear$"))
    app.add_handler(CallbackQueryHandler(send_cart, pattern="^cart:send$"))
    app.add_handler(CallbackQueryHandler(manager, pattern="^manager$"))
    app.add_handler(MessageHandler(filters.CONTACT, receive_contact))
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
