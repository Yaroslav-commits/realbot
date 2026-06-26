import os
import re
import asyncio
import logging
import sqlite3
import random
import calendar
from html import escape
from urllib.parse import quote_plus
from datetime import datetime, timedelta, timezone
from aiogram import Bot, F, types
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardRemove,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           CallbackQuery, LabeledPrice, PreCheckoutQuery,
                           FSInputFile, Message)
import base64
from aiogram.filters import Command, StateFilter, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (BOT_TOKEN, ADMIN_IDS, DB_PATH,
                    GET_COOLDOWN_HOURS, BATTLE_COOLDOWN_HOURS,
                    MAIN_PRIZE_NORMAL_TITLE, MAIN_PRIZE_ROYALE_CARD)
from data.cards import (CARDS, RARITIES, BGS, VIDEO_BGS, TITLES,
                        NORMAL_PASS, ROYALE_PASS)
from database.db import (db_exec, init_db, get_user, add_user, get_rank,
                         pull_random_card, give_card_to_user, try_use_promo, grant_retroactive_royale_pass,
                         get_user_by_ref_code, get_referral_count, get_users_for_cooldown_notify,
                         mark_cooldown_notified, reset_cooldown_notified, toggle_notifications,
                         get_notifications_enabled, is_anonymous, toggle_anonymity,
                         user_has_bg, user_has_title, give_bg_to_user, give_title_to_user,
                         is_premium, get_premium_until,
                         get_users_for_battle_cooldown_notify, mark_battle_cooldown_notified)
from handlers import (router, TradeState, SettingsState, PromoState,
                      MATCH_QUEUE, GAMES, PENDING_TRADES, kb_main)
from media_cache import send_cached_video

# Регулярка для эмодзи в нике (запрет для не-Premium)
EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002500-\U00002BEF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA70-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\u200d"
    "\ufe0f"
    "]+",
    flags=re.UNICODE
)
class GiftBgState(StatesGroup):
    waiting_for_id = State()
class BroadcastState(StatesGroup):
    waiting_for_message = State()
class NicknameState(StatesGroup):
    waiting_for_nick = State()
# ================== HANDLERS ==================
@router.message(CommandStart())
async def start_cmd(msg: types.Message, command: CommandObject, state: FSMContext):
    payload = command.args  # Получаем данные из ссылки
    referred_by = None
    is_trade = False
    trade_sender_id = None
    trade_card_id = None

    if payload:
        # 1. Проверяем, не трейд-ссылка ли это
        try:
            # Восстанавливаем паддинг base64, если нужно
            padding = 4 - (len(payload) % 4)
            padded_payload = payload + "=" * padding if padding != 4 else payload
            raw = base64.urlsafe_b64decode(padded_payload.encode()).decode()

            # ВАЖНОЕ ИСПРАВЛЕНИЕ: Делим ровно на 3 части, так как ID карты может содержать двоеточие (напр. battle:EPIC)
            parts = raw.split(":", 2)

            if len(parts) == 3 and parts[0] == "trade":
                is_trade = True
                trade_sender_id = int(parts[1])
                trade_card_id = parts[2]
        except Exception:
            pass  # Если расшифровать не вышло, значит это не трейд

        # 2. Если это не трейд, проверяем на рефералку
        if not is_trade:
            referrer = get_user_by_ref_code(payload)
            if referrer:
                referred_by = referrer[0]

    # ДОБАВЛЯЕМ ЮЗЕРА В БАЗУ (даже если он перешел по трейд-ссылке впервые)
    reward_amount = add_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name, referred_by)

    if reward_amount and referred_by:
        try:
            await msg.bot.send_message(
                referred_by,
                f"🤝 По твоей ссылке зашёл новый игрок!\nТебе начислено: <b>{reward_amount}💴</b> и <b>3💳</b>"
            )
        except Exception:
            pass

    # === ЕСЛИ ЭТО ТРЕЙД, ЗАПУСКАЕМ МЕНЮ ОБМЕНА И ПРЕРЫВАЕМ СТАРТ ===
    if is_trade:
        if trade_sender_id == msg.from_user.id:
            return await msg.answer("❌ Вы не можете обмениваться сами с собой по своей же ссылке!")

        # Проверяем, есть ли всё ещё эта карта у инициатора
        sender_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?",
                             (trade_sender_id, trade_card_id), fetch=True)
        if not sender_has:
            return await msg.answer("❌ Трейд больше не актуален: у инициатора больше нет этой карты.")

        c = CARDS.get(trade_card_id)
        if not c:
            return await msg.answer("❌ Ошибка: карта не найдена в базе данных.")

        # Записываем трейд
        PENDING_TRADES[trade_sender_id] = {
            'sender_card': trade_card_id,
            'receiver_id': msg.from_user.id,
            'receiver_card': None
        }

        u_sender = get_user(trade_sender_id)
        sender_name = escape(u_sender[2] if u_sender and u_sender[2] else f"Игрок {trade_sender_id}")

        has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?",
                           (msg.from_user.id, trade_card_id), fetch=True)
        warning = "\n<i>(⚠️ Осторожно: у вас уже есть копия этой карты)</i>" if has_card else ""

        caption = (
            f"⚖️ <b>Запрос на обмен по ссылке!</b>\n\n"
            f"Игрок <a href='tg://user?id={trade_sender_id}'>{sender_name}</a> предлагает вам обмен.\n"
            f"<blockquote>🎁 <b>Он отдает:</b>\n"
            f"🎴 {c['name']} ({c['rarity']})</blockquote>"
            f"{warning}"
        )

        bld = InlineKeyboardBuilder()
        bld.button(text="Выбрать карту взамен 🎴", callback_data=f"trade_p2_select:{trade_sender_id}")
        bld.button(text="Отказаться ❌", callback_data=f"trade_decline:{trade_sender_id}")
        bld.adjust(1)

        photo_path = f"images/cards/{c.get('file', 'default.png')}"
        if os.path.exists(photo_path):
            await msg.answer_photo(
                photo=FSInputFile(photo_path),
                caption=caption,
                reply_markup=bld.as_markup(),
                parse_mode="HTML"
            )
        else:
            await msg.answer(caption, reply_markup=bld.as_markup(), parse_mode="HTML")

        # Уведомляем создателя ссылки
        try:
            receiver_name = escape(msg.from_user.first_name)
            await msg.bot.send_message(
                trade_sender_id,
                f"🔔 Игрок <a href='tg://user?id={msg.from_user.id}'>{receiver_name}</a> перешел по вашей трейд-ссылке и сейчас выбирает карту взамен <b>{c['name']}</b>!",
                parse_mode="HTML"
            )
        except Exception:
            pass

        return  # ВАЖНО: Прерываем функцию, чтобы бот не прислал обычное приветствие

    # === ЕСЛИ ЭТО НЕ ТРЕЙД, ВЫВОДИМ ОБЫЧНОЕ ПРИВЕТСТВИЕ ===
    if msg.chat.type == "private":
        markup = kb_main()
    else:
        markup = ReplyKeyboardRemove()

    await msg.answer(
        "🎴 Добро пожаловать в *ManhwCard*! 🎴\n\n"
        "Здесь ты сможешь собирать карты любимых персонажей, сражаться с другими игроками и обмениваться редкими картами 💥\n\n"
        "📢 [Канал](https://t.me/manhwcard)\n"
        "💬 [Чат](https://t.me/manhwcardchat)\n\n"
        "Выбирай действие ниже и начинай своё приключение 👇",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@router.message(F.text == "⛩️ Банды")
async def gangs(msg: types.Message):
    await msg.answer("В разработке")

# Словарики для анти-спама и защиты от двойного списания попыток
anti_spam_locks = {}
user_locks = {}

# ============ ГАЧА ============
@router.message(F.text == "🎴 Получить карту")
@router.message(Command("get"))
async def get_card_cmd(msg: types.Message):
    uid = msg.from_user.id
    now_time = datetime.now()

    # Инициализация блокировки пользователя
    if uid not in user_locks:
        user_locks[uid] = asyncio.Lock()
    lock = user_locks[uid]

    last_click = anti_spam_locks.get(uid)

    # Если уже идет крутка или клик был менее 1.5 секунд назад
    if lock.locked() or (last_click and (now_time - last_click).total_seconds() < 0.6):
        anti_spam_locks[uid] = now_time # Обновляем таймер, чтобы спамер продолжал ждать
        warnings = [
            "Ах герой, полегче, ты меня заспамил 🥵",
            "Воу воу воин, куда ты так спешишь, будь медленнее 🛡",
            "Поспешишь — людей насмешишь, не торопись сплинтер 🐢",
            "Эй-эй, подожди! Карты нужно вытягивать с чувством, с расстановкой 🧘‍♂️",
            "Куда гонишь? У нас тут не Формула-1, сбавь обороты 🏎🛑",
            "Эй, полегче! Ты сейчас кнопку до дыр сотрёшь 😅",
            "Скорость хорошая, но сервер не успевает за твоим энтузиазмом ⚡️",
            "Ого, ты кликаешь как будто тебе за это платят 💀",
            "Подожди секунду, я не робот-бог, я просто бот 🤖",
            "Ты слишком разогнался, тут не чит-коды 😤",
            "Успокой пальцы, чемпион 🏆",
            "Я всё понимаю, но дай системе жить 🧠",
            "Ты сейчас устроишь мне цифровую мигрень 😵‍💫",
            "Стоп-стоп, я не резиновый сервер 🧯",
            "Ещё чуть-чуть и я начну мстить задержками 😈",
            "Ты точно не автокликер? 👀",
            "Дай отдышаться, я не марафонец 🏃‍♂️",
            "Карты не любят давление… они капризные 😌",
            "Сбавь обороты, ковбой 🤠",
            "Машина выдачи карт перегрелась от твоей скорости! Дай ей секунду 🔥"
        ]
        return await msg.answer(random.choice(warnings))

    anti_spam_locks[uid] = now_time

    # Блокируем выполнение для конкретного юзера, пока он не получит карту
    async with lock:
        u = get_user(uid)
        if not u:
            return

        attempts = u[6]
        now = datetime.now()

        # Premium = 1 час, обычный = GET_COOLDOWN_HOURS (3 часа)
        user_is_premium = is_premium(uid)
        cooldown_hours = 1 if user_is_premium else GET_COOLDOWN_HOURS

        # Сначала проверяем кулдаун (если попыток нет)
        if attempts <= 0:
            try:
                last_get = datetime.strptime(u[11], "%Y-%m-%d %H:%M:%S")
            except Exception:
                last_get = datetime.min
            if (now - last_get).total_seconds() < cooldown_hours * 3600:
                rem = int(cooldown_hours * 3600 - (now - last_get).total_seconds())
                return await msg.answer(f"⏳ Следующая карта через {rem // 3600}ч {(rem % 3600) // 60}м.")

        # Получаем карту (Premium — повышенный шанс)
        card_key = pull_random_card(premium=user_is_premium)
        if not card_key:
            return await msg.answer("❌ Ошибка: пул карт пуст или произошла ошибка.")
        is_new, krw, c = give_card_to_user(uid, card_key)
        # Если карта или данные повреждены — не списываем попытку
        if c is None:
            return await msg.answer("❌ Ошибка при получении карты. Попробуйте снова.")

        # Формируем текст
        if is_new:
            txt = (f"<b>🃏 Получена новая боевая карта!</b>\n\n"
                   f"<b>🎴 Персонаж:</b> {c['name']}\n"
                   f"<b>🔮 Редкость:</b> {c['rarity']}\n"
                   f"<b>👊 Стиль боя:</b> {c['style']}\n"
                   f"<b>🪐 Вселенная:</b> {c.get('series', 'Неизвестно')}\n\n"
                   f"<b>⚡️ Скорость:</b> {c['speed']}\n"
                   f"<b>💪 Сила:</b> {c['strength']}\n"
                   f"<b>🧠 Интеллект:</b> {c['intellect']}")
        else:
            txt = (f"🛑 Вам попалась повторная карта! Вы получаете {krw} 💴 KRW\n\n"
                   f"<b>🎴 Персонаж:</b> {c['name']}\n"
                   f"<b>🔮 Редкость:</b> {c['rarity']}\n"
                   f"👊 <b>Стиль боя:</b> {c['style']}\n"
                   f"<b>🪐 Вселенная:</b> {c.get('series', 'Неизвестно')}\n\n"
                   f"<b>⚡️ Скорость:</b> {c['speed']}\n"
                   f"<b>💪 Сила:</b> {c['strength']}\n"
                   f"<b>🧠 Интеллект:</b> {c['intellect']}")
            # === ИВЕНТ: НАГРАДА ЗА КРУТКУ ===
        from database.db import add_event_item
        ev_amount = random.randint(2, 5)
        if random.choice([True, False]):
            add_event_item(uid, "icecream", ev_amount)
            txt += f"\n\n<b>🪎 Ивент:</b>\n🍨 Мороженое +{ev_amount}"
        else:
            add_event_item(uid, "dango", ev_amount)
            txt += f"\n\n<b>🪎 Ивент:</b>\n🍡 Данго +{ev_amount}"
        # ================================

        # Божественные карты приходят видео, остальные — фото.

        # Божественные карты приходят видео, остальные — фото.
        try:
            if "Божественная" in c.get("rarity", "") and c.get("video"):
                await send_cached_video(
                    msg.bot,
                    chat_id=uid,
                    file_path=f"images/cards/{c['video']}",
                    caption=txt,
                    width=c.get("width", 960),
                    height=c.get("height", 1280),
                    has_spoiler=True,
                    supports_streaming=True
                )
            else:
                await msg.answer_photo(photo=FSInputFile(f"images/cards/{c['file']}"), caption=txt, has_spoiler=True, parse_mode="HTML")
        except Exception:
            try:
                await msg.answer(txt, parse_mode="HTML")
            except:
                return await msg.answer("❌ Не удалось открутить.")

        # Списываем попытку ТОЛЬКО после успешной отправки
        if attempts > 0:
            new_attempts = attempts - 1
            db_exec("UPDATE users SET attempts = ? WHERE id = ?", (new_attempts, uid))
            if new_attempts == 0:
                # Последняя попытка использована — запускаем кулдаун и сбрасываем флаг уведомления
                db_exec("UPDATE users SET last_get = ?, cooldown_notified = 0 WHERE id = ?",
                        (now.strftime("%Y-%m-%d %H:%M:%S"), uid))
        else:
            # Попыток 0 — кулдаун уже идёт, обновляем last_get и сбрасываем флаг
            db_exec("UPDATE users SET last_get = ?, cooldown_notified = 0 WHERE id = ?",
                    (now.strftime("%Y-%m-%d %H:%M:%S"), uid))


RU_MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

def format_ru_date(dt: datetime) -> str:
    return f"{dt.day}-го {RU_MONTHS_GENITIVE.get(dt.month, '')}"

def is_royale_active(u) -> bool:
    try:
        now_msk = datetime.now(timezone(timedelta(hours=3)))
        current_ym = int(now_msk.strftime("%Y%m"))
        return int(u[16] or 0) == current_ym
    except Exception:
        return False

def build_profile_keyboard() -> InlineKeyboardMarkup:
    bld = InlineKeyboardBuilder()
    bld.button(text="🔱 Мои титулы", callback_data="my_titles")
    bld.button(text="🌄 Мои фоны", callback_data="my_bgs")
    bld.button(text="⚙️ Настройка", callback_data="settings")
    bld.adjust(1)
    return bld.as_markup()

def profile_user_name(u, viewer_id: int | None = None, admin: bool = False) -> str:
    nick = escape(u[2] or "Игрок")
    anonymous = bool(u[23]) if len(u) > 23 and u[23] is not None else False

    if anonymous and not admin and viewer_id != u[0]:
        return nick

    return f'<a href="tg://user?id={u[0]}">{nick}</a>'

def build_own_profile_text(u, viewer_id: int | None = None) -> str:
    uid = u[0]
    pts = u[7]

    if u[14] and u[14] in TITLES:
        title_str = f"🔱 Титул: {TITLES[u[14]]}\n\n"
    else:
        title_str = "\n"

    status_emoji = "👑" if is_premium(uid) else "🧩"
    user_link = profile_user_name(u, viewer_id=viewer_id)

    from database.db import get_event_items
    cocktail, icecream, dango = get_event_items(uid)
    event_str = (
        f"<b>🪎 Ивент:</b>\n"
        f"🍹 Коктейль - {cocktail}\n"
        f"🍨 Мороженое - {icecream}\n"
        f"🍡 Данго - {dango}\n\n"
    )
    return (
        f" {status_emoji} Профиль - {user_link}\n"
        f"━━━━━━━━━━━━━━━\n"
        f'🆔 ID: <code>{u[0]}</code>\n'
        f"{title_str}\n"
        f"<b>💰 Баланс:</b>\n"
        f"┌ 💎 Diamond — {u[3]}\n"
        f'├ 💴 KRW — {u[4]}\n'
        f"└ 🪙 BattleCoin — {u[5]}\n\n"
        f"{event_str}"
        f"<b>🎟 Попытки:</b>\n"
        f"└ 💳 {u[6]}\n\n"
        f"<b>🏆 Ранг:</b>\n"
        f'✨ {get_rank(pts)} • {pts}🏅\n\n'
        f"<b>⚔️ Статистика боёв:</b>\n"
        f"├ 🏆 Побед — {u[8]}\n"
        f"├ ⚔️ Ничьих — {u[9]}\n"
        f"└ ☠️ Поражений — {u[10]}"
    )

def build_settings_text(uid: int) -> str:
    u = get_user(uid)
    nick = escape(u[2] or "Игрок")

    premium_until_dt = get_premium_until(uid)
    if premium_until_dt and premium_until_dt > datetime.now():
        premium_text = f"Активен до {format_ru_date(premium_until_dt)} ✅"
    else:
        premium_text = "Не Активна ❌"

    royale_text = "Активен ✅" if is_royale_active(u) else "Не Активен ❌"

    return (
        "⚙️ <b>Настройки:</b>\n\n"
        f"👤 <b>Ник</b> - {nick}\n"
        f"👑 <b>Подписка</b> - {premium_text}\n"
        f"🌠 <b>Рояль-Пасс</b> - {royale_text}"
    )

def build_settings_keyboard(uid: int) -> InlineKeyboardMarkup:
    notif_on = get_notifications_enabled(uid)

    bld = InlineKeyboardBuilder()
    bld.button(text="Изменить ник 🔄", callback_data="change_nick_start")
    bld.button(text="Реферальная система 🔗", callback_data="referral_system")
    bld.button(
        text=f"Уведомления 📣 {'✅' if notif_on else '☑️'}",
        callback_data="toggle_notifications"
    )
    bld.button(text="Анонимность 🥷", callback_data="anonymity_settings")
    bld.button(text="Назад 🔙", callback_data="back_to_profile")
    bld.adjust(1, 1, 2, 1)
    return bld.as_markup()

def build_anonymity_keyboard(uid: int) -> InlineKeyboardMarkup:
    anon = is_anonymous(uid)

    bld = InlineKeyboardBuilder()
    bld.button(
        text="Выключить ✅" if anon else "Включить ☑️",
        callback_data="toggle_anonymity"
    )
    bld.button(text="Назад 🔙", callback_data="settings")
    bld.adjust(1)
    return bld.as_markup()

async def smart_edit_message(message: Message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """
    Редактирует текущее сообщение.
    Если это фото/видео профиля — меняет caption.
    Если это текст — меняет text.
    Новое сообщение не отправляет.
    """
    try:
        if message.content_type == "text":
            await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await message.edit_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        # Например: message is not modified
        pass

# ======== ПРОФИЛЬ =========
@router.message(F.text == "👤 Профиль")
async def profile(msg: types.Message):
    uid = msg.from_user.id
    u = get_user(uid)

    if not u:
        add_user(uid, msg.from_user.username, msg.from_user.first_name)
        u = get_user(uid)

    pts = u[7]

    if u[14] and u[14] in TITLES:
        title_str = f"🔱 Титул: {TITLES[u[14]]}\n\n"
    else:
        title_str = "\n"

    # Эмодзи статуса
    status_emoji = "👑" if is_premium(uid) else "🧩"

    user_link = profile_user_name(u, viewer_id=msg.from_user.id)

    # === ИВЕНТ ===
    from database.db import get_event_items
    cocktail, icecream, dango = get_event_items(uid)
    event_str = (
        f"<b>🪎 Ивент:</b>\n"
        f"🍹 Коктейль - {cocktail}\n"
        f"🍨 Мороженое - {icecream}\n"
        f"🍡 Данго - {dango}\n\n"
    )
    # =============

    txt = (
        f" {status_emoji} Профиль - {user_link}\n"
        f"━━━━━━━━━━━━━━━\n"
        f'🆔 ID: <code>{u[0]}</code>\n'
        f"{title_str}\n"
        f"<b>💰 Баланс:</b>\n"
        f"┌ 💎 Diamond — {u[3]}\n"
        f'├ 💴 KRW — {u[4]}\n'
        f"└ 🪙 BattleCoin — {u[5]}\n\n"
        f"{event_str}"
        f"<b>🎟 Попытки:</b>\n"
        f"└ 💳 {u[6]}\n\n"
        f"<b>🏆 Ранг:</b>\n"
        f'✨ {get_rank(pts)} • {pts}🏅\n\n'
        f"<b>⚔️ Статистика боёв:</b>\n"
        f"├ 🏆 Побед — {u[8]}\n"
        f"├ ⚔️ Ничьих — {u[9]}\n"
        f"└ ☠️ Поражений — {u[10]}"
    )



    bld = InlineKeyboardBuilder()

    bld.button(
        text="🔱 Мои титулы",
        callback_data="my_titles"
    )

    bld.button(
        text="🌄 Мои фоны",
        callback_data="my_bgs"
    )

    bld.button(
        text="⚙️ Настройка",
        callback_data="settings"
    )

    bld.adjust(1)

    bg_key = u[13] or 'default'

    bg_data = BGS.get(
        bg_key,
        BGS['default']
    )

    bg_file = FSInputFile(
        f"images/backgrounds/{bg_data.get('file')}"
    )

    try:
        if bg_key in VIDEO_BGS:

            await send_cached_video(
                msg.bot,
                chat_id=msg.chat.id,
                file_path=f"images/backgrounds/{bg_data.get('file')}",
                caption=txt,
                reply_markup=bld.as_markup(),
                parse_mode="HTML",
                supports_streaming=True,
                width=bg_data.get('width'),
                height=bg_data.get('height')
            )

        else:

            await msg.answer_photo(
                photo=bg_file,
                caption=txt,
                reply_markup=bld.as_markup(),
                parse_mode="HTML"
            )

    except Exception:

        await msg.answer(
            f"{txt}\n\n[Фон не загрузился.]",
            reply_markup=bld.as_markup(),
            parse_mode="HTML"
        )


@router.message(Command("profile"))
async def cmd_profile(msg: types.Message):
    is_admin = msg.from_user.id in ADMIN_IDS
    target_id = None

    args = msg.text.split()
    if len(args) > 1 and is_admin:
        try:
            target_id = int(args[1])
        except ValueError:
            return await msg.answer("❌ Неверный формат ID. Использование: /profile <id>")
    elif msg.reply_to_message:
        target_id = msg.reply_to_message.from_user.id
    else:
        return await msg.answer(
            "❌ Ответьте на сообщение игрока командой /profile, чтобы посмотреть его профиль." +
            (" Или используйте /profile <id>." if is_admin else "")
        )

    u = get_user(target_id)
    if not u:
        return await msg.answer("❌ Игрок не найден в базе бл..")
    if is_anonymous(target_id) and target_id != msg.from_user.id and not is_admin:
        return await msg.answer("🥷 Этот игрок включил режим инкогнито. Его профиль скрыт.")
    pts = u[7]
    title_str = f"🔱 Титул: {TITLES[u[14]]}\n\n" if u[14] and u[14] in TITLES else "\n"
    status_emoji = "👑" if is_premium(target_id) else "🧩"
    user_link = profile_user_name(u, viewer_id=msg.from_user.id, admin=is_admin)

    # === ИВЕНТ ===
    from database.db import get_event_items
    cocktail, icecream, dango = get_event_items(target_id)
    event_str = (
        f"<b>🪎 Ивент:</b>\n"
        f"🍹 Коктейль - {cocktail}\n"
        f"🍨 Мороженое - {icecream}\n"
        f"🍡 Данго - {dango}\n\n"
    )
    # =============

    if is_admin:
        # Полный профиль для админа
        txt = (
            f" {status_emoji} Профиль - {user_link}\n"
            f"━━━━━━━━━━━━━━━\n"
            f'🆔 ID: <code>{u[0]}</code>\n'
            f"{title_str}"
            f"<b>💰 Баланс:</b>\n"
            f"┌ 💎 Diamond — {u[3]}\n"
            f'├ 💴 KRW — {u[4]}\n'
            f"└ 🪙 BattleCoin — {u[5]}\n\n"
            f"<b>🎟 Попытки:</b>\n"
            f"└ 💳 {u[6]}\n\n"
            f"<b>🏆 Ранг:</b>\n"
            f'✨ {get_rank(pts)} • {pts}🏅\n\n'
            f"<b>⚔️ Статистика боёв:</b>\n"
            f"├ 🏆 Побед — {u[8]}\n"
            f"├ ⚔️ Ничьих — {u[9]}\n"
            f"└ ☠️ Поражений — {u[10]}"
        )
    else:
        # Урезанный профиль для обычных игроков (с красивой цитатой)
        txt = (
            f"{user_link} {status_emoji}\n"
            f"🆔 ID: <code>{u[0]}</code>\n"
            f"{title_str}"
            f"<blockquote>"
            f"💰 Баланс:\n"
            f"┌ 💎 Diamond — {u[3]}\n"
            f"├ 💴 KRW — {u[4]}\n"
            f"└ 🪙 BattleCoin — {u[5]}\n\n"
            f"🏆 Ранг:\n"
            f"✨ {get_rank(pts)} • {pts}🏅\n"
            f"</blockquote>"
        )

    # Превращаем обычные символы в кастомные HTML эмодзи, как в стандартном профиле
    txt = txt.replace("🆔", '🆔')
    txt = txt.replace("💴", '💴')
    txt = txt.replace("🏅", '🏅')

    bg_key = u[13] or 'default'
    bg_data = BGS.get(bg_key, BGS['default'])
    bg_file = FSInputFile(f"images/backgrounds/{bg_data.get('file')}")

    try:
        # Проверяем, видео фон или фото
        if bg_key in VIDEO_BGS:
            await send_cached_video(
                msg.bot,
                chat_id=msg.chat.id,
                file_path=f"images/backgrounds/{bg_data.get('file')}",
                caption=txt,
                parse_mode="HTML",
                supports_streaming=True,
                width=bg_data.get('width'),
                height=bg_data.get('height')
            )
        else:
            await msg.answer_photo(photo=bg_file, caption=txt, parse_mode="HTML")
    except Exception:
        await msg.answer(f"{txt}\n\n[Фон не загрузился.]", parse_mode="HTML")


# ============ НАСТРОЙКИ ============
@router.callback_query(F.data == "settings")
async def settings_cq(cq: CallbackQuery):
    u = get_user(cq.from_user.id)
    if not u:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    await smart_edit_message(
        cq.message,
        build_settings_text(cq.from_user.id),
        reply_markup=build_settings_keyboard(cq.from_user.id),
        parse_mode="HTML"
    )
    await cq.answer()

@router.callback_query(F.data == "back_to_profile")
async def back_to_profile_cq(cq: CallbackQuery):
    u = get_user(cq.from_user.id)
    if not u:
        await cq.answer("Пользователь не найден", show_alert=True)
        return
    await smart_edit_message(
        cq.message,
        build_own_profile_text(u, viewer_id=cq.from_user.id),
        reply_markup=build_profile_keyboard(),
        parse_mode="HTML"
    )
    await cq.answer()

@router.callback_query(F.data == "change_nick_start")
async def change_nick_start_cq(cq: CallbackQuery, state: FSMContext):
    bld = InlineKeyboardBuilder()
    bld.button(text="Отмена ✖️", callback_data="cancel_change_nick")

    await state.set_state(NicknameState.waiting_for_nick)

    await smart_edit_message(
        cq.message,
        "Введите новый никнейм:",
        reply_markup=bld.as_markup(),
        parse_mode="HTML"
    )
    await cq.answer()

@router.callback_query(F.data == "cancel_change_nick")
async def cancel_change_nick_cq(cq: CallbackQuery, state: FSMContext):
    await state.clear()

    await smart_edit_message(
        cq.message,
        build_settings_text(cq.from_user.id),
        reply_markup=build_settings_keyboard(cq.from_user.id),
        parse_mode="HTML"
    )
    await cq.answer("Отменено")

@router.message(NicknameState.waiting_for_nick)
async def process_new_nick(msg: Message, state: FSMContext):
    new_nick = (msg.text or "").strip()

    if not new_nick:
        return await msg.answer("❌ Ник не может быть пустым.")

    if len(new_nick) > 32:
        return await msg.answer("❌ Ник слишком длинный. Максимум 32 символа.")

    if EMOJI_RE.search(new_nick) and not is_premium(msg.from_user.id):
        return await msg.answer("❌ Эмодзи в нике доступны только Premium 👑 пользователям.")

    db_exec("UPDATE users SET nickname = ? WHERE id = ?", (new_nick, msg.from_user.id))
    await state.clear()

    await msg.answer(f"✅ Ник изменён на <b>{escape(new_nick)}</b>", parse_mode="HTML")

@router.message(Command("nick"))
async def change_nick(msg: types.Message):
    new_nick = msg.text.replace("/nick", "", 1).strip()

    if not new_nick:
        return await msg.answer("Использование: /nick НовыйНик")

    if len(new_nick) > 32:
        return await msg.answer("❌ Ник слишком длинный. Максимум 32 символа.")

    if EMOJI_RE.search(new_nick) and not is_premium(msg.from_user.id):
        return await msg.answer("❌ Эмодзи в нике доступны только Premium 👑 пользователям.")

    db_exec("UPDATE users SET nickname = ? WHERE id = ?", (new_nick, msg.from_user.id))
    await msg.answer(f"✅ Ник изменён на <b>{escape(new_nick)}</b>", parse_mode="HTML")

@router.callback_query(F.data == "toggle_notifications")
async def toggle_notifications_cq(cq: CallbackQuery):
    new_state = toggle_notifications(cq.from_user.id)

    await smart_edit_message(
        cq.message,
        build_settings_text(cq.from_user.id),
        reply_markup=build_settings_keyboard(cq.from_user.id),
        parse_mode="HTML"
    )

    await cq.answer(f"Уведомления {'включены' if new_state else 'выключены'}")

@router.callback_query(F.data == "anonymity_settings")
async def anonymity_settings_cq(cq: CallbackQuery):
    text = (
        "С помощью кнопки ниже активируется режим инкогнито, "
        "можно использовать, чтобы другие игроки не могли просматривать твой профиль."
    )

    await smart_edit_message(
        cq.message,
        text,
        reply_markup=build_anonymity_keyboard(cq.from_user.id),
        parse_mode="HTML"
    )
    await cq.answer()

@router.callback_query(F.data == "toggle_anonymity")
async def toggle_anonymity_cq(cq: CallbackQuery):
    new_state = toggle_anonymity(cq.from_user.id)

    text = (
        "С помощью кнопки ниже активируется режим инкогнито, "
        "можно использовать, чтобы другие игроки не могли просматривать твой профиль."
    )

    await smart_edit_message(
        cq.message,
        text,
        reply_markup=build_anonymity_keyboard(cq.from_user.id),
        parse_mode="HTML"
    )
    await cq.answer("Инкогнито включено" if new_state else "Инкогнито выключено")

# ============ РЕФЕРАЛЬНАЯ СИСТЕМА ============
@router.callback_query(F.data == "referral_system")
async def referral_system_cq(cq: CallbackQuery, bot: Bot):
    from database.db import get_referral_code_fixed, get_referral_count

    uid = cq.from_user.id
    ref_code = get_referral_code_fixed(uid)
    ref_count = get_referral_count(uid)

    if not ref_code:
        await cq.answer("Ошибка: код не найден. Попробуйте /start", show_alert=True)
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={ref_code}"

    txt = (
        f"👥 <b>Всего приглашённых:</b> {ref_count}\n\n"
        f"Приглашай друзей! За каждого игрока, перешедшего по твоей ссылке, "
        f"<b>ты и твой друг</b> получите от 300💴 до 550💴 и 3💳 попыток бонусом!\n\n"
        f"⛓️‍💥 <b>Твоя уникальная реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>"
    )

    share_url = (
        "https://t.me/share/url"
        f"?url={quote_plus(ref_link)}"
        f"&text={quote_plus('Залетай в ManhwCard 🎴 По моей ссылке дадут бонус!')}"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="📨 Отправить реф ссылку", url=share_url)
    bld.button(text="🔙 Назад", callback_data="settings")
    bld.adjust(1)

    await smart_edit_message(cq.message, txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()

# ============ ПЕРЕКЛЮЧЕНИЕ УВЕДОМЛЕНИЙ ============
@router.callback_query(F.data == "toggle_notifications")
async def toggle_notifications_cq(cq: CallbackQuery):
    new_state = toggle_notifications(cq.from_user.id)

    # Обновляем данные пользователя
    u = get_user(cq.from_user.id)
    if not u:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    notif_emoji = "✅" if new_state else "☑️"
    notif_text = "Включить уведомления" if new_state else "Выключить уведомления"

    premium_until_dt = get_premium_until(cq.from_user.id)
    if premium_until_dt and premium_until_dt > datetime.now():
        premium_line = f"👑 Premium активен до: {premium_until_dt.strftime('%d.%m.%Y')}\n"
    else:
        premium_line = "👑 Premium: не активен\n"

    txt = (
        f"⚙️ Настройки\n"
        f"Дата регистрации: {u[15]}\n"
        f"{premium_line}"
        f"Для смены ника отправьте команду /nick [новый ник]\n"
        f"{notif_text} > {notif_emoji}"
    )
    bld = InlineKeyboardBuilder()
    bld.button(text="👥 Реферальная система", callback_data="referral_system")
    bld.button(text=f"Вкл/выкл уведомления {notif_emoji}", callback_data="toggle_notifications")
    bld.adjust(1)

    try:
        await cq.message.edit_text(txt, reply_markup=bld.as_markup())
    except Exception:
        pass

    await cq.answer(f"Уведомления {'включены' if new_state else 'выключены'}")


@router.callback_query(F.data.in_(["my_bgs", "my_titles"]))
async def bgs_titles_cq(cq: CallbackQuery):
    is_bg = cq.data == "my_bgs"
    table = "bgs_inv" if is_bg else "titles_inv"
    col = "bg_id" if is_bg else "title_id"
    valid_items = BGS if is_bg else TITLES

    # Чистим неизвестные предметы конкретно у этого игрока
    valid_keys = list(valid_items.keys())
    if valid_keys:
        ph = ",".join(["?"] * len(valid_keys))
        db_exec(
            f"DELETE FROM {table} WHERE user_id = ? AND {col} NOT IN ({ph})",
            tuple([cq.from_user.id] + valid_keys)
        )

    # Чистим дубли конкретно у этого игрока
    db_exec(f"""
        DELETE FROM {table}
        WHERE user_id = ?
        AND rowid NOT IN (
            SELECT MIN(rowid)
            FROM {table}
            WHERE user_id = ?
            GROUP BY {col}
        )
    """, (cq.from_user.id, cq.from_user.id))

    items = db_exec(
        f"SELECT DISTINCT {col} FROM {table} WHERE user_id = ?",
        (cq.from_user.id,),
        fetchall=True
    )

    item_ids = []
    seen = set()

    for row in items or []:
        item_id = row[0]
        if item_id in valid_items and item_id not in seen:
            item_ids.append(item_id)
            seen.add(item_id)

    if is_bg and "default" not in seen:
        item_ids.insert(0, "default")

    if not item_ids:
        await cq.answer("У вас пока ничего нет!", show_alert=True)
        return

    bld = InlineKeyboardBuilder()

    # Заполняем кнопки (это остается внутри цикла)
    for itm in item_ids:
        if is_bg:
            name = BGS[itm].get("name", itm)
            callback = f"preview_bg:{itm}"
        else:
            name = TITLES[itm]
            callback = f"preview_title:{itm}"

        bld.button(text=name, callback_data=callback)

    # ❗️ ВАЖНО: Вышли из цикла (сдвинули код влево)
    bld.adjust(2) # 👈 Формируем сетку: по 2 кнопки в ряд

    # Отправляем один раз готовое меню со всеми кнопками
    text_msg = "🌄 Выберите фон для просмотра:" if is_bg else "🔱 Выберите титул для просмотра:"
    await cq.message.answer(text_msg, reply_markup=bld.as_markup())
    await cq.answer()

# ============ Предпросмотр фона/титула ============
@router.callback_query(F.data.startswith("preview_"))
async def preview_cq(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 2:
        return
    type_str, itm = parts[0].replace("preview_", ""), parts[1]

    if type_str == "bg" and itm not in BGS:
        db_exec("DELETE FROM bgs_inv WHERE user_id = ? AND bg_id = ?", (cq.from_user.id, itm))
        await cq.answer("Этот фон был удалён из игры и убран из инвентаря.", show_alert=True)
        return

    if type_str == "title" and itm not in TITLES:
        db_exec("DELETE FROM titles_inv WHERE user_id = ? AND title_id = ?", (cq.from_user.id, itm))
        await cq.answer("Этот титул был удалён из игры и убран из инвентаря.", show_alert=True)
        return

    u = get_user(cq.from_user.id)
    current_active = u[13] if type_str == "bg" else u[14]

    is_active = (current_active == itm)
    if type_str == "bg" and itm == "default" and current_active in [None, 'default']:
        is_active = True

    btn_text = "✅ Установлено" if is_active else "☑️ Установить"
    bld = InlineKeyboardBuilder()
    bld.button(text=btn_text, callback_data=f"equip_{type_str}:{itm}")

    if type_str == "bg" and itm != "default":
        bld.button(text="🎁 Подарить", callback_data=f"gift_bg:{itm}")
    bld.adjust(1)

    if type_str == "bg":
        bg_data = BGS.get(itm, BGS['default'])
        bg_file = bg_data.get('file')
        name = bg_data.get('name', 'Фон')
        caption = f"🌄 Предпросмотр фона: {name}"
        if itm in VIDEO_BGS:
            bg_data = BGS.get(itm, BGS['default'])
            await send_cached_video(
                cq.bot,
                chat_id=cq.message.chat.id,
                file_path=f"images/backgrounds/{bg_file}",
                caption=caption,
                reply_markup=bld.as_markup(),
                supports_streaming=True,
                width=bg_data.get('width'),
                height=bg_data.get('height')
            )
        else:
            await cq.message.answer_photo(photo=FSInputFile(f"images/backgrounds/{bg_file}"), caption=caption, reply_markup=bld.as_markup())
    else:
        name = TITLES.get(itm, 'Титул')
        await cq.message.answer(f"🔱 Предпросмотр титула: {name}", reply_markup=bld.as_markup())

    await cq.answer()


@router.callback_query(F.data.startswith("equip_"))
async def equip_cq(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 2:
        return

    type_str, itm = parts[0].replace("equip_", ""), parts[1]

    if type_str not in ("bg", "title"):
        return await cq.answer("Ошибка типа предмета.", show_alert=True)

    if type_str == "bg":
        if itm not in BGS:
            db_exec("DELETE FROM bgs_inv WHERE user_id = ? AND bg_id = ?", (cq.from_user.id, itm))
            return await cq.answer("Этот фон был удалён из игры.", show_alert=True)

        if itm != "default" and not user_has_bg(cq.from_user.id, itm):
            return await cq.answer("У вас нет этого фона.", show_alert=True)

        col = "active_bg"
    else:
        if itm not in TITLES:
            db_exec("DELETE FROM titles_inv WHERE user_id = ? AND title_id = ?", (cq.from_user.id, itm))
            return await cq.answer("Этот титул был удалён из игры.", show_alert=True)

        if not user_has_title(cq.from_user.id, itm):
            return await cq.answer("У вас нет этого титула.", show_alert=True)

        col = "active_title"

    u = get_user(cq.from_user.id)
    current_active = u[13] if type_str == "bg" else u[14]

    is_active = current_active == itm
    if type_str == "bg" and itm == "default" and current_active in [None, "default"]:
        is_active = True

    if is_active:
        new_val = "default" if type_str == "bg" else None
        db_exec(f"UPDATE users SET {col} = ? WHERE id = ?", (new_val, cq.from_user.id))
        btn_text = "☑️ Установить"
        alert_text = "Убрано из профиля!"
    else:
        db_exec(f"UPDATE users SET {col} = ? WHERE id = ?", (itm, cq.from_user.id))
        btn_text = "✅ Установлено"
        alert_text = "Успешно установлено!"

    bld = InlineKeyboardBuilder()
    bld.button(text=btn_text, callback_data=f"equip_{type_str}:{itm}")

    if type_str == "bg" and itm != "default":
        bld.button(text="🎁 Подарить", callback_data=f"gift_bg:{itm}")

    bld.adjust(1)

    try:
        await cq.message.edit_reply_markup(reply_markup=bld.as_markup())
    except Exception:
        pass

    await cq.answer(alert_text)

# ============ ПОДАРИТЬ ФОН ============
@router.callback_query(F.data.startswith("gift_bg:"))
async def start_gift_bg(cq: CallbackQuery, state: FSMContext):
    bg_id = cq.data.split(":")[1]
    await state.update_data(bg_to_gift=bg_id)

    bld = InlineKeyboardBuilder()
    bld.button(text="Отменить", callback_data="cancel_gift")

    await cq.message.answer(
        "Отправьте 🆔 игрока, которому хотите <b>подарить</b> данный фон:",
        reply_markup=bld.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(GiftBgState.waiting_for_id)
    await cq.answer()


@router.callback_query(F.data == "cancel_gift")
async def cancel_gift_bg(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.delete()
    await cq.answer("Действие отменено.")


@router.message(GiftBgState.waiting_for_id)
async def process_gift_id(msg: Message, state: FSMContext, bot: Bot):
    # Защита от багов: если игрок кликает на постороннюю кнопку (не вводит число)
    if not msg.text or not msg.text.isdigit():
        await state.clear()
        await msg.answer("❌ Действие отменено. Ожидался ID игрока (число).")
        return

    target_id = int(msg.text)
    data = await state.get_data()
    bg_id = data.get("bg_to_gift")
    sender_id = msg.from_user.id

    if target_id == sender_id:
        await state.clear()
        return await msg.answer("❌ Вы не можете подарить фон самому себе.")

    target_user = get_user(target_id)
    if not target_user:
        await state.clear()
        return await msg.answer("❌ Игрок с таким ID не найден в базе.")

    # Проверяем, есть ли фон у отправителя
    sender_has = db_exec("SELECT rowid FROM bgs_inv WHERE user_id = ? AND bg_id = ?", (sender_id, bg_id), fetch=True)
    if not sender_has:
        await state.clear()
        return await msg.answer("❌ Ошибка: у вас больше нет этого фона в инвентаре.")
    # Проверяем, есть ли уже фон у получателя
    target_bgs = db_exec("SELECT bg_id FROM bgs_inv WHERE user_id = ?", (target_id,), fetchall=True)
    target_bg_ids = [b[0] for b in target_bgs] if target_bgs else []
    has_bg = bg_id in target_bg_ids

    bg_data = BGS.get(bg_id, BGS.get('default'))
    bg_file = bg_data.get('file')
    bg_name = bg_data.get('name', 'Фон')

    if has_bg:
        caption = f"Вам хотят подарить фон «{bg_name}» ⚠️ У ВАС УЖЕ ЕСТЬ ЭТОТ ФОН, выберете действие:"
    else:
        caption = f"Вам хотят подарить фон «{bg_name}», выберете действие:"

    bld = InlineKeyboardBuilder()
    # Размещаем кнопки в один ряд (слева Отказаться, справа Согласиться)
    bld.button(text="Отказаться ❌", callback_data=f"gift_ans:reject:{sender_id}:{bg_id}")
    bld.button(text="Согласиться ✅", callback_data=f"gift_ans:accept:{sender_id}:{bg_id}")
    bld.adjust(2)

    try:
        if bg_id in VIDEO_BGS:
            await send_cached_video(
                bot,
                chat_id=target_id,
                file_path=f"images/backgrounds/{bg_file}",
                caption=caption,
                reply_markup=bld.as_markup(),
                supports_streaming=True,
                width=bg_data.get('width'),
                height=bg_data.get('height')
            )
        else:
            await bot.send_photo(
                chat_id=target_id,
                photo=FSInputFile(f"images/backgrounds/{bg_file}"),
                caption=caption,
                reply_markup=bld.as_markup()
            )
        await msg.answer("✅ Предложение о подарке отправлено игроку!")
    except Exception:
        await msg.answer("❌ Не удалось отправить сообщение игроку. Возможно, он заблокировал бота.")

    await state.clear()


@router.callback_query(F.data.startswith("gift_ans:"))
async def process_gift_answer(cq: CallbackQuery, bot: Bot):
    parts = cq.data.split(":")
    if len(parts) != 4:
        return
    action, sender_id, bg_id = parts[1], int(parts[2]), parts[3]
    receiver_id = cq.from_user.id

    bg_data = BGS.get(bg_id, BGS.get('default'))
    bg_name = bg_data.get('name', 'Фон')

    if action == "reject":
        await cq.message.edit_caption(caption=f"❌ Вы отказались от подарка фона «{bg_name}».", reply_markup=None)
        try:
            await bot.send_message(sender_id, f"❌ Игрок {receiver_id} отказался от подарка фона «{bg_name}».")
        except:
            pass
        return await cq.answer()

    if user_has_bg(receiver_id, bg_id):
            try:
                await cq.message.edit_caption(
                    caption=f"⚠️ У вас уже есть фон «{bg_name}». Подарок не был принят.",
                    reply_markup=None
                )
            except Exception:
                await cq.message.edit_text(
                    f"⚠️ У вас уже есть фон «{bg_name}». Подарок не был принят.",
                    reply_markup=None
                )
            return await cq.answer("Этот фон уже есть у вас.", show_alert=True)

    if action == "accept":
        # Проверяем, есть ли всё ещё фон у отправителя
        sender_has = db_exec("SELECT rowid FROM bgs_inv WHERE user_id = ? AND bg_id = ?", (sender_id, bg_id),
                             fetch=True)
        if not sender_has:
            await cq.message.edit_caption(caption=f"❌ Ошибка: У игрока {sender_id} больше нет этого фона.",
                                          reply_markup=None)
            return await cq.answer()

        # Забираем фон у отправителя
        db_exec("DELETE FROM bgs_inv WHERE rowid = ?", (sender_has[0],))

        # Если фон был установлен как активный у отправителя, сбрасываем на default
        sender_user = get_user(sender_id)
        if sender_user and sender_user[13] == bg_id:
            db_exec("UPDATE users SET active_bg = 'default' WHERE id = ?", (sender_id,))

        # Выдаем фон получателю
        give_bg_to_user(receiver_id,bg_id)
        await cq.message.edit_caption(caption=f"✅ Вы успешно приняли фон «{bg_name}»!", reply_markup=None)
        try:
            await bot.send_message(sender_id, f"✅ Игрок {receiver_id} принял ваш подарок! Фон «{bg_name}» передан.")
        except:
            pass

        await cq.answer("Фон успешно получен!")

@router.message(Command("card"))
async def cmd_card_info(msg: types.Message):
    # Разбиваем сообщение, чтобы получить название или ID карты
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        return await msg.answer("Введи название или ID карты, например: <code>/card Ким Гитэ</code>", parse_mode="HTML")

    query = args[1].strip().lower()
    card_id = None
    card_data = None
    exact_match = False

    # Сначала ищем по ID (ключу), затем по названию (на точное совпадение)
    if query in CARDS:
        card_id = query
        card_data = CARDS[query]
        exact_match = True
    else:
        for cid, cdata in CARDS.items():
            if cdata.get("name", "").lower() == query:
                card_id = cid
                card_data = cdata
                exact_match = True
                break

    # Если точного совпадения нет — ищем частичные вхождения
    if not exact_match:
        partial_matches = []
        for cid, cdata in CARDS.items():
            if query in cdata.get("name", "").lower():
                partial_matches.append((cid, cdata.get("name", "Неизвестно")))

        # Если вообще ничего не нашли
        if not partial_matches:
            return await msg.answer(f"❌ Карта по запросу «{args[1]}» не найдена!")

        # Формируем инлайн-кнопки с вариантами
        builder = InlineKeyboardBuilder()
        for cid, cname in partial_matches[:10]:  # Ограничиваем список до 10 кнопок, чтобы не спамить
            # Ограничение callback_data - 64 байта
            builder.button(text=f"{cname} 🃏", callback_data=f"c_inf:{cid}"[:64])
        builder.adjust(1)  # По одной кнопке в ряд

        return await msg.answer("Может вы имели ввиду кого-то из этих:", reply_markup=builder.as_markup())

    # --- Если точное совпадение найдено, показываем карточку ---
    # Считаем сумму из активного инвентаря И сундука
    count_res = db_exec("""
        SELECT 
            (SELECT COUNT(*) FROM cards_inv WHERE card_id = ?) + 
            (SELECT COUNT(*) FROM cards_stash WHERE card_id = ?)
    """, (card_id, card_id), fetch=True)
    count = count_res[0] if count_res and count_res[0] is not None else 0

    from data.cards import EVENT_CARDS_LIST

    if card_id in EVENT_CARDS_LIST:
        is_exclusive = "Ивентовая 🪎"
    elif card_data.get("exclusive"):
        is_exclusive = "Лимитированная ✨"
    else:
        is_exclusive = "Стандартная 🧿"

    text = (
        f"🃏 Боевая карта: {card_data.get('name', 'Неизвестно')}\n"
        f"<blockquote>🔮 Редкость: {card_data.get('rarity', 'Неизвестно')}\n"
        f"👊 Стиль боя: {card_data.get('style', 'Неизвестно')}\n"
        f"🪐 Вселенная: {card_data.get('series', 'Неизвестно')}\n"
        f"⚡️ Скорость: {card_data.get('speed', 0)}\n"
        f"💪 Сила: {card_data.get('strength', 0)}\n"
        f"🧠 Интеллект: {card_data.get('intellect', 0)}\n"
        f"🧬 {is_exclusive}\n"
        f"♻️ Количество карт в боте: {count}</blockquote>"
    )
    try:
        if "video" in card_data:
            from media_cache import send_cached_video
            await send_cached_video(
                msg.bot,
                chat_id=msg.chat.id,
                file_path=f"images/cards/{card_data['video']}",
                caption=text,
                width=card_data.get("width", 960),
                height=card_data.get("height", 960),
                parse_mode="HTML"
            )
        else:
            from aiogram.types import FSInputFile
            await msg.answer_photo(
                photo=FSInputFile(f"images/cards/{card_data.get('file', 'default.png')}"),
                caption=text,
                parse_mode="HTML"
            )
    except Exception:
        await msg.answer(text, parse_mode="HTML")

@router.callback_query(F.data.startswith("c_inf:"))
async def cb_card_info(call: types.CallbackQuery):
    card_id = call.data.split(":", 1)[1]
    card_data = CARDS.get(card_id)

    if not card_data:
        return await call.answer("❌ Карта не найдена!", show_alert=True)

    await call.message.delete()  # Убираем сообщение "Может вы имели ввиду..."

    # Считаем сумму из активного инвентаря И сундука
    count_res = db_exec("""
        SELECT 
            (SELECT COUNT(*) FROM cards_inv WHERE card_id = ?) + 
            (SELECT COUNT(*) FROM cards_stash WHERE card_id = ?)
    """, (card_id, card_id), fetch=True)
    count = count_res[0] if count_res and count_res[0] is not None else 0

    from data.cards import EVENT_CARDS_LIST

    if card_id in EVENT_CARDS_LIST:
        is_exclusive = "Ивентовая 🪎"
    elif card_data.get("exclusive"):
        is_exclusive = "Лимитированная ✨"
    else:
        is_exclusive = "Стандартная 🧿"

    text = (
        f"🃏 Боевая карта: {card_data.get('name', 'Неизвестно')}\n"
        f"<blockquote>🔮 Редкость: {card_data.get('rarity', 'Неизвестно')}\n"
        f"👊 Стиль боя: {card_data.get('style', 'Неизвестно')}\n"
        f"🪐 Вселенная: {card_data.get('series', 'Неизвестно')}\n"
        f"⚡️ Скорость: {card_data.get('speed', 0)}\n"
        f"💪 Сила: {card_data.get('strength', 0)}\n"
        f"🧠 Интеллект: {card_data.get('intellect', 0)}\n"
        f"🧬 {is_exclusive}\n"
        f"♻️ Количество карт в боте: {count}</blockquote>"
    )

    try:
        if "video" in card_data:
            from media_cache import send_cached_video
            await send_cached_video(
                call.bot,
                chat_id=call.message.chat.id,
                file_path=f"images/cards/{card_data['video']}",
                caption=text,
                width=card_data.get("width", 960),
                height=card_data.get("height", 960),
                parse_mode="HTML"
            )
        else:
            from aiogram.types import FSInputFile
            await call.message.answer_photo(
                photo=FSInputFile(f"images/cards/{card_data.get('file', 'default.png')}"),
                caption=text,
                parse_mode="HTML"
            )
    except Exception:
        await call.message.answer(text, parse_mode="HTML")

    await call.answer()


# ============ ФОНЫ (/fon) ============
async def _send_bg_card(bot, chat_id, bg_id, bg_data):
    """Отправляет карточку фона (фото или видео) с количеством у игроков."""
    name = bg_data.get("name", bg_id)
    file = bg_data.get("file")

    # Считаем количество таких фонов у игроков
    if bg_id == "default":
        count = "∞ (Базовый фон есть у всех)"
    else:
        count_res = db_exec("SELECT COUNT(*) FROM bgs_inv WHERE bg_id = ?", (bg_id,), fetch=True)
        count = count_res[0] if count_res else 0

    text = (
        f"🌄 <b>Фон:</b> {name}\n"
        f"<blockquote>♻️ <b>Количество в боте:</b> {count}</blockquote>"
    )

    try:
        if bg_id in VIDEO_BGS:
            from media_cache import send_cached_video
            await send_cached_video(
                bot,
                chat_id=chat_id,
                file_path=f"images/backgrounds/{file}",
                caption=text,
                width=bg_data.get("width", 1280),
                height=bg_data.get("height", 720),
                parse_mode="HTML",
                supports_streaming=True
            )
        else:
            await bot.send_photo(
                chat_id,
                photo=FSInputFile(f"images/backgrounds/{file}"),
                caption=text,
                parse_mode="HTML"
            )
    except Exception:
        await bot.send_message(chat_id, text, parse_mode="HTML")


@router.message(Command("fon"))
async def cmd_fon_info(msg: types.Message):
    args = msg.text.split(maxsplit=1)

    # Если ввели просто /fon без аргументов
    if len(args) < 2:
        # Генерируем красивый список фонов
        bg_list = "\n".join([f"🔸 <code>{bid}</code> — {bdata.get('name', bid)}" for bid, bdata in BGS.items()])
        text = (
            "ℹ️ <b>Как пользоваться:</b>\n"
            "Введи команду <code>/fon [название или ID]</code>, чтобы посмотреть анимацию/картинку и узнать редкость фона.\n\n"
            "📜 <b>Доступные фоны в игре:</b>\n"
            f"<blockquote>{bg_list}</blockquote>"
        )
        return await msg.answer(text, parse_mode="HTML")

    query = args[1].strip().lower()
    bg_id = None
    bg_data = None
    exact_match = False

    if query in BGS:
        bg_id = query
        bg_data = BGS[query]
        exact_match = True
    else:
        for bid, bdata in BGS.items():
            if bdata.get("name", "").lower() == query:
                bg_id = bid
                bg_data = bdata
                exact_match = True
                break

    if not exact_match:
        partial_matches = []
        for bid, bdata in BGS.items():
            name = bdata.get("name", bid)
            if query in name.lower() or query in bid.lower():
                partial_matches.append((bid, name))

        if not partial_matches:
            return await msg.answer(f"❌ Фон по запросу «{args[1]}» не найден!")

        builder = InlineKeyboardBuilder()
        for bid, bname in partial_matches[:10]:
            builder.button(text=f"{bname} 🌄", callback_data=f"f_inf:{bid}"[:64])
        builder.adjust(1)
        return await msg.answer("Найдено несколько фонов, выбери нужный:", reply_markup=builder.as_markup())

    await _send_bg_card(msg.bot, msg.chat.id, bg_id, bg_data)


@router.callback_query(F.data.startswith("f_inf:"))
async def cb_fon_info(call: types.CallbackQuery):
    bg_id = call.data.split(":", 1)[1]
    bg_data = BGS.get(bg_id)

    if not bg_data:
        return await call.answer("❌ Фон не найден!", show_alert=True)

    await call.message.delete()
    await _send_bg_card(call.bot, call.message.chat.id, bg_id, bg_data)
    await call.answer()
# ============ PREMIUM (/premium) ============
@router.message(Command("premium"))
async def cmd_premium(msg: types.Message):
    text = (
        "<b>Преимущества Premium-подписки:</b>\n\n"
        "🃏 Получение лимитированной карты;\n"
        "🎁 Повышенный шанс выпадения редких карт;\n"
        "⏱️ Сокращённый кулдаун на получения карт и битв;\n"
        "🪙 Можно получать больше BattleCoin;\n"
        "🛡️ Бонусы на Поле Битвы (PVP +1);\n"
        "🫠️ Возможность юзать эмодзи в нике;\n"
    )
    await msg.answer(text, parse_mode="HTML")


import math


# ============ ИСТОРИЯ КАРТ (АДМИН КОМАНДА) ============
@router.message(Command("card_history"))
async def cmd_card_history(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return

    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer(
            "❌ Использование: <code>/card_history [card_id] [дней]</code>\n"
            "Пример: <code>/card_history yunsu 7</code>\n"
            "<i>(Если не указать дни, по умолчанию покажет за 7 дней)</i>",
            parse_mode="HTML"
        )

    card_id = args[1]
    days = int(args[2]) if len(args) > 2 and args[2].isdigit() else 7

    if card_id not in CARDS:
        return await msg.answer(f"❌ Карта <b>{card_id}</b> не найдена в системе.", parse_mode="HTML")

    await send_card_history_page(msg, card_id, days, page=0)


async def send_card_history_page(event, card_id: str, days: int, page: int):
    """Генерация и отправка страницы истории карты"""
    try:
        logs = db_exec(
            "SELECT action, user_id, target_id, details, created_at FROM card_logs "
            "WHERE card_id = ? AND created_at >= datetime('now', ?) ORDER BY created_at DESC",
            (card_id, f"-{days} days"), fetchall=True
        )
    except Exception:
        text = "⚠️ <b>Ошибка:</b> Таблица логов (card_logs) не создана или пуста. Обновите базу данных!"
        if isinstance(event, types.Message):
            return await event.answer(text, parse_mode="HTML")
        else:
            return await event.message.edit_text(text, parse_mode="HTML")

    if not logs:
        logs = []

    items_per_page = 10
    total_pages = max(1, math.ceil(len(logs) / items_per_page))

    # Защита от выхода за пределы
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0

    chunk = logs[page * items_per_page: (page + 1) * items_per_page]
    c_name = CARDS[card_id].get('name', card_id)

    txt = f"🗃 <b>История карты:</b> {c_name} (<code>{card_id}</code>)\n"
    txt += f"📅 <b>Период:</b> последние {days} дней\n\n"

    if not chunk:
        txt += "<i>Движений по этой карте за указанный период не найдено. Начните записывать логи!</i>"
    else:
        for action, uid, tid, details, dt in chunk:
            user_link = f"<a href='tg://user?id={uid}'>{uid}</a>"
            date_short = dt.split()[0][5:] + " " + dt.split()[1][:5]  # Формат MM-DD HH:MM

            if action == 'TRADE':
                target_link = f"<a href='tg://user?id={tid}'>{tid}</a>"
                txt += f"🤝 [{date_short}] {user_link} передал {target_link}\n"
            elif action == 'DROP':
                txt += f"🎁 [{date_short}] {user_link} выбил из гачи\n"
            elif action == 'CRAFT':
                txt += f"🧬 [{date_short}] {user_link} скрафтил в реакторе\n"
            elif action == 'ADMIN':
                txt += f"⚡️ [{date_short}] Выдано админом игроку {user_link}\n"
            elif action == 'STASH':
                txt += f"📦 [{date_short}] {user_link}: {details}\n"
            else:
                txt += f"📝 [{date_short}] {user_link}: {details}\n"

    bld = InlineKeyboardBuilder()

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"ch_page:{card_id}:{days}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"Стр. {page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"ch_page:{card_id}:{days}:{page + 1}"))

    if nav_row:
        bld.row(*nav_row)

    bld.row(InlineKeyboardButton(text="👥 Кто владеет картой?", callback_data=f"ch_owners:{card_id}"))

    markup = bld.as_markup()

    if isinstance(event, types.Message):
        await event.answer(txt, reply_markup=markup, parse_mode="HTML")
    else:
        await event.message.edit_text(txt, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("ch_page:"))
async def cb_card_history_page(cq: CallbackQuery):
    _, card_id, days_str, page_str = cq.data.split(":")
    await send_card_history_page(cq, card_id, int(days_str), int(page_str))
    await cq.answer()


@router.callback_query(F.data.startswith("ch_owners:"))
async def cb_card_history_owners(cq: CallbackQuery):
    _, card_id = cq.data.split(":")

    owners_inv = db_exec("SELECT user_id, COUNT(*) FROM cards_inv WHERE card_id = ? GROUP BY user_id", (card_id,),
                         fetchall=True)
    owners_stash = db_exec("SELECT user_id, COUNT(*) FROM cards_stash WHERE card_id = ? GROUP BY user_id", (card_id,),
                           fetchall=True)

    total_owners = {}
    for uid, count in (owners_inv or []):
        total_owners[uid] = total_owners.get(uid, 0) + count
    for uid, count in (owners_stash or []):
        total_owners[uid] = total_owners.get(uid, 0) + count

    c_name = CARDS[card_id].get('name', card_id)
    txt = f"👥 <b>Владельцы карты:</b> {c_name} (<code>{card_id}</code>)\n\n"

    if not total_owners:
        txt += "<i>Этой карты сейчас ни у кого нет.</i>"
    else:
        sorted_owners = sorted(total_owners.items(), key=lambda x: x[1], reverse=True)
        for uid, count in sorted_owners[:30]:
            u = get_user(uid)
            nick = u[2] if u else "Неизвестный"
            txt += f"👤 <a href='tg://user?id={uid}'>{nick}</a> (<code>{uid}</code>) — {count} шт.\n"

        if len(sorted_owners) > 30:
            txt += f"\n<i>...и еще {len(sorted_owners) - 30} игроков.</i>"

    bld = InlineKeyboardBuilder()
    bld.button(text="🔙 Назад к истории", callback_data=f"ch_page:{card_id}:7:0")

    await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()
# ============ АДМИН И ПРОМО ============
# ============ КОМАНДА РАССЫЛКИ (NOTIFER) ============

@router.message(Command("notifer"))
async def notifier_cmd(msg: types.Message, state: FSMContext):
    # Проверка на админа (используем твой список ADMIN_IDS из config)
    if msg.from_user.id not in ADMIN_IDS:
        return

    await msg.answer("📥 Пришлите сообщение, которое хотите разослать всем пользователям.\n"
                     "Это может быть текст, фото, видео или <b>пересланное сообщение</b> из канала.")
    await state.set_state(BroadcastState.waiting_for_message)


@router.message(BroadcastState.waiting_for_message)
async def process_broadcast(msg: types.Message, state: FSMContext, bot: Bot):
    await state.clear()

    # Получаем всех пользователей из базы данных
    users = db_exec("SELECT id FROM users", fetchall=True)

    if not users:
        return await msg.answer("❌ В базе данных нет пользователей.")

    await msg.answer(f"🚀 Начинаю рассылку для {len(users)} пользователей...")

    count = 0
    blocked = 0
    errors = 0

    for (uid,) in users:
        try:
            # Используем copy_to, так как оно идеально копирует всё:
            # текст, кнопки, медиа и сохраняет ссылки.
            # Если это пересланное сообщение, оно сохранится как пересланное.
            await msg.copy_to(chat_id=uid)
            count += 1
            # Небольшая задержка, чтобы Telegram не забанил за спам
            await asyncio.sleep(0.05)
        except Exception as e:
            # Если пользователь заблокировал бота
            if "forbidden" in str(e).lower():
                blocked += 1
            else:
                errors += 1

    await msg.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"👤 Получили: {count}\n"
        f"🚫 Заблокировали бота: {blocked}\n"
        f"⚠️ Ошибок: {errors}",
        parse_mode="HTML"
    )


@router.message(Command("stats"))
async def cmd_stats(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return

    now_msk = datetime.now(timezone(timedelta(hours=3)))
    current_ym = int(now_msk.strftime("%Y%m"))

    # === АУДИТОРИЯ ===
    total_users = db_exec("SELECT COUNT(*) FROM users", fetch=True)[0]
    active_users_7d = db_exec("SELECT COUNT(*) FROM users WHERE last_get >= datetime('now', '-7 days')", fetch=True)[0]
    premium_users = \
    db_exec("SELECT COUNT(*) FROM users WHERE premium_until IS NOT NULL AND premium_until > datetime('now')",
            fetch=True)[0]
    pass_users = db_exec("SELECT COUNT(*) FROM users WHERE royale_pass = ?", (current_ym,), fetch=True)[0]

    # === ИГРОВЫЕ ПРЕДМЕТЫ И ПРОЦЕСС ===
    total_cards_inv = db_exec("SELECT COUNT(*) FROM cards_inv", fetch=True)[0]
    total_cards_stash = db_exec("SELECT COUNT(*) FROM cards_stash", fetch=True)[0]
    total_cards = total_cards_inv + total_cards_stash
    total_bgs = db_exec("SELECT COUNT(*) FROM bgs_inv", fetch=True)[0]
    total_titles = db_exec("SELECT COUNT(*) FROM titles_inv", fetch=True)[0]

    total_battles = db_exec("SELECT SUM(wins + draws + losses) FROM users", fetch=True)[0] or 0

    # Считаем успешные трейды из логов карт
    try:
        total_trades = db_exec("SELECT COUNT(*) FROM card_logs WHERE action = 'TRADE'", fetch=True)[0] or 0
    except Exception:
        total_trades = 0

    # === ЭКОНОМИКА ===
    total_krw = db_exec("SELECT SUM(krw) FROM users", fetch=True)[0] or 0
    total_dia = db_exec("SELECT SUM(diamond) FROM users", fetch=True)[0] or 0
    total_bc = db_exec("SELECT SUM(battlecoin) FROM users", fetch=True)[0] or 0
    total_attempts = db_exec("SELECT SUM(attempts) FROM users", fetch=True)[0] or 0

    # === ИВЕНТ ===
    try:
        event_res = db_exec("SELECT SUM(cocktail), SUM(icecream), SUM(dango) FROM event_items", fetch=True)
        ev_cocktail = event_res[0] if event_res and event_res[0] else 0
        ev_icecream = event_res[1] if event_res and event_res[1] else 0
        ev_dango = event_res[2] if event_res and event_res[2] else 0
        total_event = ev_cocktail + ev_icecream + ev_dango
    except Exception:
        ev_cocktail, ev_icecream, ev_dango, total_event = 0, 0, 0, 0

    text = (
        "📊 <b>Расширенная статистика бота:</b>\n\n"
        f"👥 <b>Аудитория:</b>\n"
        f"├ Всего юзеров: <b>{total_users}</b>\n"
        f"├ Активные (7 дней): <b>{active_users_7d}</b>\n"
        f"├ Premium 👑: <b>{premium_users}</b>\n"
        f"└ Royale Pass 🌠: <b>{pass_users}</b>\n\n"
        f"🎴 <b>Игровой процесс:</b>\n"
        f"├ Карт на руках (Инв / Сундук): <b>{total_cards_inv} / {total_cards_stash}</b> (Всего: <b>{total_cards}</b>)\n"
        f"├ Выдано фонов: <b>{total_bgs}</b>\n"
        f"├ Выдано титулов: <b>{total_titles}</b>\n"
        f"├ Сыграно боёв: <b>{total_battles}</b> ⚔️\n"
        f"└ Проведено обменов: <b>{total_trades}</b> 🤝\n\n"
        f"💰 <b>Экономика (в обороте):</b>\n"
        f"├ KRW: <b>{total_krw}</b> 💴\n"
        f"├ Diamond: <b>{total_dia}</b> 💎\n"
        f"├ BattleCoin: <b>{total_bc}</b> 🪙\n"
        f"└ Неиспользованных попыток (круток): <b>{total_attempts}</b> 💳\n\n"
        f"🪎 <b>Летний Ивент (Ресурсы на руках):</b>\n"
        f"└ Всего: <b>{total_event}</b> (🍹 {ev_cocktail} | 🍨 {ev_icecream} | 🍡 {ev_dango})"
    )
    await msg.answer(text, parse_mode="HTML")

# ============ ИНФО О ПАССЕ И МАССОВОЕ ВОССТАНОВЛЕНИЕ ============

@router.message(Command("pass_info"))
async def cmd_pass_info(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return

    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("❌ Формат: /pass_info [ID]\nПример: /pass_info 123456789")

    try:
        uid = int(args[1])
    except ValueError:
        return await msg.answer("❌ Некорректный ID. Должен быть числом.")

    u = get_user(uid)
    if not u:
        return await msg.answer("❌ Игрок с таким ID не найден в базе.")

    now_msk = datetime.now(timezone(timedelta(hours=3)))
    current_month = now_msk.month
    today = now_msk.day

    claims_normal = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = 'normal'", (uid, current_month), fetchall=True)
    claimed_n = [d[0] for d in claims_normal] if claims_normal else []

    claims_royale = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = 'royale'", (uid, current_month), fetchall=True)
    claimed_r = [d[0] for d in claims_royale] if claims_royale else []

    passed_days_this_month = [d for d in range(1, today + 1) if d in NORMAL_PASS]
    missed_n = [str(d) for d in passed_days_this_month if d not in claimed_n]

    has_royale = is_royale_active(u)
    missed_r = []
    if has_royale:
        passed_days_royale = [d for d in range(1, today + 1) if d in ROYALE_PASS]
        missed_r = [str(d) for d in passed_days_royale if d not in claimed_r]

    txt = f"📊 <b>Инфо по пассу для ID {uid}</b>:\n\n"
    txt += f"📅 <b>Текущий день месяца:</b> {today}\n\n"
    txt += f"🏙️ <b>Обычный пасс:</b>\n"
    txt += f"└ ❌ Пропущенные дни: {', '.join(missed_n) if missed_n else 'Нет'}\n\n"

    txt += f"🌠 <b>Рояль пасс:</b> " + ("(Активен ✅)\n" if has_royale else "(Не активен ❌)\n")
    if has_royale:
        txt += f"└ ❌ Пропущенные дни: {', '.join(missed_r) if missed_r else 'Нет'}\n"

    await msg.answer(txt, parse_mode="HTML")


@router.message(Command("restore_pass_days"))
async def cmd_restore_pass_days(msg: types.Message, bot: Bot):
    if msg.from_user.id not in ADMIN_IDS:
        return

    args = msg.text.split()
    # Ожидаем минимум 4 аргумента: команда, id, тип, и хотя бы один день
    if len(args) < 4:
        return await msg.answer(
            "❌ Формат: /restore_pass_days [ID] [normal/royale] [день1] [день2] ...\n"
            "Пример: /restore_pass_days 123456789 normal 1 2 3 5"
        )

    try:
        uid = int(args[1])
        p_type = args[2].lower()
        days = [int(x) for x in args[3:]]
    except ValueError:
        return await msg.answer("❌ Некорректные аргументы. ID и ДНИ должны быть числами.")

    if p_type not in ("normal", "royale"):
        return await msg.answer("❌ Тип пасса должен быть: normal или royale")

    u = get_user(uid)
    if not u:
        return await msg.answer("❌ Пользователь не найден.")

    data = ROYALE_PASS if p_type == "royale" else NORMAL_PASS
    now_msk = datetime.now(timezone(timedelta(hours=3)))
    current_month = now_msk.month

    # Получаем уже забранные дни
    claims = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?", (uid, current_month, p_type), fetchall=True)
    claimed = [d[0] for d in claims] if claims else []

    rewards_summary = {'krw': 0, 'atm': 0, 'bc': 0, 'dia': 0, 'packs': 0}
    restored_days = []
    already_claimed_days = []
    invalid_days = []

    for day in days:
        if day not in data:
            invalid_days.append(str(day))
            continue
        if day in claimed:
            already_claimed_days.append(str(day))
            continue

        # Выдаем награду
        r_type, r_val = data.get(day, ('krw', 10))
        if r_type == 'krw':
            db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (r_val, uid))
            rewards_summary['krw'] += r_val
        elif r_type == 'atm':
            db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (r_val, uid))
            rewards_summary['atm'] += r_val
        elif r_type == 'bc':
            db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (r_val, uid))
            rewards_summary['bc'] += r_val
        elif r_type == 'dia':
            db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (r_val, uid))
            rewards_summary['dia'] += r_val
        elif r_type == 'pack':
            card_key = pull_random_card(force_rarity="Легендарная 🔵" if r_val == "leg" else "Эпическая 🟢")
            if not card_key:
                card_key = pull_random_card()
            give_card_to_user(uid, card_key)
            rewards_summary['packs'] += 1

        # Отмечаем как полученный
        db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)", (uid, current_month, day, p_type))
        restored_days.append(str(day))

    # Формируем текст отчета для админа
    lines = []
    if rewards_summary['krw']: lines.append(f"• {rewards_summary['krw']} 💴 KRW")
    if rewards_summary['atm']: lines.append(f"• {rewards_summary['atm']} 💳 попыток")
    if rewards_summary['bc']: lines.append(f"• {rewards_summary['bc']} 🪙 BattleCoin")
    if rewards_summary['dia']: lines.append(f"• {rewards_summary['dia']} 💎 Алмазов")
    if rewards_summary['packs']: lines.append(f"• {rewards_summary['packs']} 🗃️ Паков")

    res_txt = f"⚙️ <b>Отчет о восстановлении:</b>\n"
    res_txt += f"✅ Восстановлены дни: {', '.join(restored_days) if restored_days else 'Ничего'}\n"
    if already_claimed_days:
        res_txt += f"⚠️ Уже были получены ранее: {', '.join(already_claimed_days)}\n"
    if invalid_days:
        res_txt += f"❌ Не существуют в пассе: {', '.join(invalid_days)}\n"

    if restored_days:
        res_txt += f"\n🎁 <b>Выдано суммарно:</b>\n" + ("\n".join(lines) if lines else "Ничего")

        # Уведомляем игрока
        pass_name = "Рояль пасс 🌠" if p_type == "royale" else "Обычный пасс 🏙️"
        u_txt = f"🎁 Администратор восстановил пропущенные дни (<b>{', '.join(restored_days)}</b>) вашего {pass_name}!\nНачислена общая награда:\n" + "\n".join(lines)
        try:
            await bot.send_message(uid, u_txt, parse_mode="HTML")
        except Exception:
            pass

    await msg.answer(res_txt, parse_mode="HTML")

@router.message(
    Command(commands=["give_attempts", "give_card", "delete_card", "give_money", "give_title", "give_background", "give_diamond", "delete_diamond", "give_pass", "give_prem", "create_promo", "restore_pass_day", "qdelete_diamond"]))
async def admin_cmds(msg: types.Message, state: FSMContext, bot: Bot):
    if msg.from_user.id not in ADMIN_IDS: return
    args = msg.text.split()
    cmd = args[0]

    if cmd == "/create_promo":
        await state.set_state(PromoState.waiting_for_promo_data)
        await msg.answer(
            "Отправь данные промокода в формате:\n[КОД] [ТИП: krw/atm/card/dia/pass/prem/bc] [ЗНАЧЕНИЕ] [КОЛ-ВО ИСПОЛЬЗОВАНИЙ]\n"
            "Пример: LOOKISM krw 500 10\n"
            "Пример Premium: VDAY prem 7 10 (премиум на 7 дней, 10 активаций)\n\n"
            "Типы:\n"
            "• krw — KRW 💴\n"
            "• atm — попытки 💳\n"
            "• card — карта (ключ)\n"
            "• dia — алмазы 💎\n"
            "• pass — Рояль Пасс (значение любое, например 1)\n"
            "• bc — BattleCoin\n"
            "• prem — Premium 👑 (значение = кол-во дней)")
        return

    # /give_pass — только 2 аргумента
    if cmd == "/give_pass":
        if len(args) < 2:
            return await msg.answer("Использование: /give_pass [ID пользователя]")
        uid = int(args[1])
        summary = grant_retroactive_royale_pass(uid)
        try:
            await bot.send_message(uid, f"🌠 Получен Рояль Пасс на этот месяц ✅{summary}")
        except Exception:
            pass
        return await msg.answer(f"✅ Рояль Пасс выдан пользователю {uid}!")  # ← вынести из except

    if cmd == "/restore_pass_day":
        # Формат: /restore_pass_day [ID] [normal/royale] [ДЕНЬ]
        if len(args) < 4:
            return await msg.answer(
                "❌ Формат: /restore_pass_day [ID] [normal/royale] [ДЕНЬ]\n"
                "Пример: /restore_pass_day 123456789 normal 5\n"
                "Пример: /restore_pass_day 123456789 royale 12"
            )
        try:
            uid = int(args[1])
            p_type = args[2].lower()
            day = int(args[3])
        except ValueError:
            return await msg.answer("❌ Некорректные аргументы. ID и ДЕНЬ должны быть числами.")

        if p_type not in ("normal", "royale"):
            return await msg.answer("❌ Тип пасса должен быть: normal или royale")

        data = ROYALE_PASS if p_type == "royale" else NORMAL_PASS

        if day not in data:
            return await msg.answer(f"❌ День {day} не найден в {p_type} пассе.")

        now_msk = datetime.now(timezone(timedelta(hours=3)))

        # Проверяем, не забирал ли уже пользователь этот день
        is_claimed = db_exec(
            "SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = ?",
            (uid, now_msk.month, day, p_type), fetch=True
        )
        if is_claimed:
            return await msg.answer(
                f"⚠️ Пользователь {uid} уже получил день {day} ({p_type} пасс) в этом месяце.\n"
                f"Если нужно выдать повторно — удали запись вручную из pass_claims."
            )

        # Начисляем награду
        r_type, r_val = data.get(day, ('krw', 10))
        icon_map = {'krw': '💴', 'atm': '💳', 'bc': '🪙', 'dia': '💎', 'pack': '🗃️'}
        if r_type == 'krw':
            db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (r_val, uid))
            reward_text = f"{r_val} {icon_map['krw']} KRW"
        elif r_type == 'atm':
            db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (r_val, uid))
            reward_text = f"{r_val} {icon_map['atm']} попыток"
        elif r_type == 'bc':
            db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (r_val, uid))
            reward_text = f"{r_val} {icon_map['bc']} BattleCoin"
        elif r_type == 'dia':
            db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (r_val, uid))
            reward_text = f"{r_val} {icon_map['dia']} Алмазов"
        elif r_type == 'pack':
            card_key = pull_random_card(force_rarity="Легендарная 🔵" if r_val == "leg" else "Эпическая 🟢")
            if not card_key:
                card_key = pull_random_card()
            give_card_to_user(uid, card_key)
            reward_text = f"Пак {icon_map['pack']} ({r_val})"
        else:
            reward_text = str(r_val)

        # Ставим отметку о получении
        db_exec(
            "INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
            (uid, now_msk.month, day, p_type)
        )

        pass_name = "Рояль пасс 🌠" if p_type == "royale" else "Обычный пасс 🏙️"
        try:
            await bot.send_message(
                uid,
                f"🎁 Администратор восстановил день <b>{day}</b> вашего {pass_name}!\n"
                f"Начислена награда: <b>{reward_text}</b> ✅",
                parse_mode="HTML"
            )
        except Exception:
            pass

        return await msg.answer(
            f"✅ День {day} ({p_type} пасс) восстановлен пользователю {uid}!\n"
            f"Начислена награда: {reward_text}"
        )

        if len(args) < 3:
            return await msg.answer("Ошибка аргументов. Формат: /команда [ID] [значение/id_карты]")

    uid, val = int(args[1]), args[2]

    if cmd == "/give_attempts":
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (int(val), uid))
        try:
            await bot.send_message(uid, f"Вам начислено {val}💳 попыток")
        except Exception:
            pass
        await msg.answer(f"✅ Выдано пользователю {uid}!")

    elif cmd == "/give_money":
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (int(val), uid))
        try:
            await bot.send_message(uid, f"Вам начислено {val}💴 KRW")
        except Exception:
            pass
        await msg.answer(f"✅ Выдано пользователю {uid}!")


    elif cmd == "/give_diamond":

        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (int(val), uid))

        try:

            await bot.send_message(uid, f"Вам начислено {val}💎 Алмазов")

        except Exception:

            pass

        await msg.answer(f"✅ Выдано пользователю {uid}!")

    elif cmd == "/qdelete_diamond":
        # Списываем алмазы, не давая уйти в минус (MAX(0, ...)), без уведомления юзеру
        db_exec("UPDATE users SET diamond = MAX(0, diamond - ?) WHERE id = ?", (int(val), uid))

        # Только отчет тебе в админку
        await msg.answer(f"🥷 Тихо списано {val}💎 у пользователя {uid}!")

    elif cmd == "/delete_diamond":

        # Списываем алмазы, но не даем уйти в минус (MAX(0, ...))

        db_exec("UPDATE users SET diamond = MAX(0, diamond - ?) WHERE id = ?", (int(val), uid))

        try:

            await bot.send_message(uid, f"Администратор списал у вас {val}💎 Алмазов ❌")

        except Exception:

            pass

        await msg.answer(f"✅ Списано {val}💎 у пользователя {uid}!")
    elif cmd == "/give_prem":
        try:
            days = int(val)
        except ValueError:
            return await msg.answer("❌ Кол-во дней должно быть числом.\nИспользование: /give_prem [ID] [ДНИ]")

        now = datetime.now()
        res = db_exec("SELECT premium_until FROM users WHERE id = ?", (uid,), fetch=True)
        current_until_str = res[0] if res else None
        if current_until_str:
            try:
                current_until = datetime.strptime(current_until_str, "%Y-%m-%d %H:%M:%S")
                new_until = (current_until if current_until > now else now) + timedelta(days=days)
            except Exception:
                new_until = now + timedelta(days=days)
        else:
            new_until = now + timedelta(days=days)
        new_until_str = new_until.strftime("%Y-%m-%d %H:%M:%S")
        db_exec("UPDATE users SET premium_until = ? WHERE id = ?", (new_until_str, uid))
        # Лимитированная премиум-карта (только при покупке в магазине или /give_prem)
        is_new, krw_earn, c = give_card_to_user(uid, "premium_card_1")
        try:
            await bot.send_message(
                uid,
                f"👑 Получен Premium на {days} дн. от администратора ✅\nДействует до: {new_until_str}"
            )
            if is_new and c:
                card_txt = (f"🃏 Бонус: лимитированная Premium карта!\n\n"
                            f"🎴 Персонаж: {c['name']}\n"
                            f"🔮 Редкость: {c['rarity']}\n"
                            f"👊 Стиль боя: {c['style']}\n"
                            f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
                            f"⚡️ Скорость: {c['speed']}\n"
                            f"💪 Сила: {c['strength']}\n"
                            f"🧠 Интеллект: {c['intellect']}")
                try:
                    await bot.send_photo(
                        uid,
                        photo=FSInputFile(f"images/cards/{c.get('file', 'premium_card.jpeg')}"),
                        caption=card_txt, has_spoiler=True
                    )
                except Exception:
                    await bot.send_message(uid, card_txt)
        except Exception:
            pass
        return await msg.answer(f"✅ Premium на {days} дн. выдан пользователю {uid}!")
    elif cmd == "/give_card":
        c = CARDS.get(val)
        if not c:
            return await msg.answer(f"❌ Карта с ключом «{val}» не найдена!")
        # Прямая выдача
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (uid, val))
        txt = (f"<b>🃏 Получена новая боевая карта от администратора ✅</b>\n\n"
               f"<b>🎴 Персонаж:</b> {c['name']}\n"
               f"<b>🔮 Редкость:</b> {c['rarity']}\n"
               f"<b>👊 Стиль боя:</b> {c['style']}\n"
               f"<b>🪐 Вселенная:</b> {c.get('series', 'Неизвестно')}\n\n"
               f"<b>⚡️ Скорость:</b> {c['speed']}\n"
               f"<b>💪 Сила:</b> {c['strength']}\n"
               f"<b>🧠 Интеллект:</b> {c['intellect']}")
        try:
            if "Божественная" in c.get("rarity", "") and c.get("video"):
                await send_cached_video(
                    bot,
                    chat_id=uid,
                    file_path=f"images/cards/{c['video']}",
                    caption=txt,
                    width=c.get("width", 960),
                    height=c.get("height", 1280),
                    supports_streaming=True
                )
            else:
                await bot.send_photo(uid, photo=FSInputFile(f"images/cards/{c['file']}"), caption=txt, parse_mode="HTML")
        except Exception:
            pass
        await msg.answer(f"✅ Карта «{c['name']}» выдана пользователю {uid}!")

    elif cmd == "/delete_card":
        c = CARDS.get(val)
        if not c:
            return await msg.answer(f"❌ Карта с ключом «{val}» не найдена в базе кода!")

        # Проверяем наличие карты у игрока
        has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (uid, val), fetch=True)
        if not has_card:
            return await msg.answer(f"❌ У пользователя {uid} нет карты «{c['name']}»")

        # Удаляем одну копию (используя rowid для SQLite, чтобы не удалить все сразу, если их несколько)
        db_exec(
            "DELETE FROM cards_inv WHERE rowid = (SELECT rowid FROM cards_inv WHERE user_id = ? AND card_id = ? LIMIT 1)",
            (uid, val))

        try:
            await bot.send_message(uid, f"⚠️ Администратор удалил у вас карту: {c['name']}")
        except Exception:
            pass
        await msg.answer(f"✅ Карта «{c['name']}» успешно удалена у пользователя {uid}!")

    elif cmd == "/give_title":
        if val not in TITLES:
            return await msg.answer(f"❌ Титул с ключом «{val}» не найден!")

        title_name = TITLES[val]
        added = give_title_to_user(uid, val)

        if not added:
            return await msg.answer(f"⚠️ У пользователя {uid} уже есть титул «{title_name}».")
        try:
            await bot.send_message(uid, f"Получен титул «{title_name}» от администратора ✅")
        except Exception:
            pass
        await msg.answer(f"✅ Титул выдан пользователю {uid}!")

    elif cmd == "/give_background":
        bg_data = BGS.get(val)
        if not bg_data:
            return await msg.answer(f"❌ Фон с ключом «{val}» не найден!")
        added = give_bg_to_user(uid, val)

        if not added:
            return await msg.answer(f"⚠️ У пользователя {uid} уже есть фон «{bg_data.get('name', val)}».")
        is_video = val in VIDEO_BGS
        try:
            bg_file = FSInputFile(f"images/backgrounds/{bg_data['file']}")  # Оставляем для фото
            if is_video:
                await send_cached_video(
                    bot,
                    chat_id=uid,
                    file_path=f"images/backgrounds/{bg_data['file']}",
                    caption="Получен фон от администратора ✅",
                    supports_streaming=True
                )
            else:
                await bot.send_photo(uid, photo=bg_file,
                                     caption="Получен фон от администратора ✅")
        except Exception:
            pass
        await msg.answer(f"✅ Фон «{bg_data.get('name', val)}» выдан пользователю {uid}!")

    elif cmd == "/give_title":
        if val not in TITLES:
            return await msg.answer(f"❌ Титул с ключом «{val}» не найден!")

        title_name = TITLES[val]
        added = give_title_to_user(uid, val)

        if not added:
            return await msg.answer(f"⚠️ У пользователя {uid} уже есть титул «{title_name}».")
        try:
            await bot.send_message(uid, f"Получен титул «{title_name}» от администратора ✅")
        except Exception:
            pass
        await msg.answer(f"✅ Титул выдан пользователю {uid}!")

    elif cmd == "/give_background":
        bg_data = BGS.get(val)
        if not bg_data:
            return await msg.answer(f"❌ Фон с ключом «{val}» не найден!")
        added = give_bg_to_user(uid, val)

        if not added:
            return await msg.answer(f"⚠️ У пользователя {uid} уже есть фон «{bg_data.get('name', val)}».")
        is_video = val in VIDEO_BGS
        try:
            bg_file = FSInputFile(f"images/backgrounds/{bg_data['file']}")
            if is_video:
                await send_cached_video(
                    bot,
                    chat_id=uid,
                    file_path=f"images/backgrounds/{bg_data['file']}",
                    caption="Получен фон от администратора ✅",
                    supports_streaming=True
                )
            else:
                await bot.send_photo(uid, photo=bg_file,
                                     caption="Получен фон от администратора ✅")
        except Exception:
            pass
        await msg.answer(f"✅ Фон «{bg_data.get('name', val)}» выдан пользователю {uid}!")

@router.message(PromoState.waiting_for_promo_data)
async def create_promo(msg: types.Message, state: FSMContext):
    args = msg.text.split()
    if len(args) != 4:
        return await msg.answer("Неверный формат. Нужно: [КОД] [ТИП] [ЗНАЧЕНИЕ] [ИСПОЛЬЗОВАНИЙ]")
    p_type = args[1]
    if p_type not in ('krw', 'atm', 'card', 'dia', 'pass', 'prem', 'bc'):
        return await msg.answer("Неверный тип. Допустимые: krw, atm, card, dia, pass, prem, bc")
    if p_type == 'prem':
        try:
            if int(args[2]) <= 0:
                return await msg.answer("❌ Для prem значение — кол-во дней (целое число > 0).")
        except ValueError:
            return await msg.answer("❌ Для prem значение должно быть числом дней.")
    db_exec("INSERT INTO promos (code, p_type, val, uses) VALUES (?, ?, ?, ?)",
            (args[0], args[1], args[2], int(args[3])))
    await state.clear()
    await msg.answer(f"✅ Промокод «{args[0]}» создан!")

@router.message(Command("promo"))
async def use_promo(msg: types.Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("Введи промокод: /promo КОД")
    code = args[1]
    uid = msg.from_user.id

    # 1. Проверяем, существует ли промокод и остались ли использования
    p = db_exec("SELECT p_type, val, uses FROM promos WHERE code = ?", (code,), fetch=True)
    if not p or p[2] <= 0:
        return await msg.answer("❌ Промокод недействителен.")

    # 2. Проверяем, не использовал ли уже этот пользователь данный промокод
    if not try_use_promo(uid, code):
        return await msg.answer("❌ Вы уже использовали этот промокод!")

    # 3. Уменьшаем счётчик использований
    db_exec("UPDATE promos SET uses = uses - 1 WHERE code = ?", (code,))

    # 4. Выдаём награду
    if p[0] == 'krw':
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (int(p[1]), uid))
        await msg.answer(f"✅ Промокод активирован! Вы получаете {p[1]}💴 KRW")
    elif p[0] == 'bc':
        db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (int(p[1]), uid))
        await msg.answer(f"✅ Промокод активирован! Вы получаете {p[1]} 🪙 BattleCoin")
    elif p[0] == 'atm':
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (int(p[1]), uid))
        await msg.answer(f"✅ Промокод активирован! Вы получаете {p[1]} попыток 💳")
    elif p[0] == 'dia':
        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (int(p[1]), uid))
        await msg.answer(f"✅ Промокод активирован! Вы получаете {p[1]}💎 Алмазов")
    elif p[0] == 'pass':
        summary = grant_retroactive_royale_pass(uid)
        await msg.answer(f"✅ Промокод активирован! Вы получаете Рояль Пасс на этот месяц 🌠{summary}")
    elif p[0] == 'prem':
        try:
            days = int(p[1])
        except ValueError:
            return await msg.answer("❌ Ошибка промокода: некорректное значение дней.")
        now = datetime.now()
        res = db_exec("SELECT premium_until FROM users WHERE id = ?", (uid,), fetch=True)
        current_until_str = res[0] if res else None
        if current_until_str:
            try:
                current_until = datetime.strptime(current_until_str, "%Y-%m-%d %H:%M:%S")
                new_until = (current_until if current_until > now else now) + timedelta(days=days)
            except Exception:
                new_until = now + timedelta(days=days)
        else:
            new_until = now + timedelta(days=days)
        new_until_str = new_until.strftime("%Y-%m-%d %H:%M:%S")
        db_exec("UPDATE users SET premium_until = ? WHERE id = ?", (new_until_str, uid))
        await msg.answer(
            f"✅ Промокод активирован! Вы получаете Premium на {days} дн. 👑\n"
            f"Premium действует до: {new_until_str}"
        )
    elif p[0] == 'card':
        c = CARDS.get(p[1])
        if not c:
            return await msg.answer("✅ Промокод активирован, но карта не найдена!")
        is_new, krw_earned, card_data = give_card_to_user(uid, p[1])
        txt = (f"✅ Промокод активирован!\n\n"
               f"🃏 Получена новая боевая карта!\n\n"
               f"🎴 Персонаж: {c['name']}\n"
               f"🔮 Редкость: {c['rarity']}\n"
               f"👊 Стиль боя: {c['style']}\n"
               f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
               f"⚡️ Скорость: {c['speed']}\n"
               f"💪 Сила: {c['strength']}\n"
               f"🧠 Интеллект: {c['intellect']}")
        try:
            if "Божественная" in c.get("rarity", "") and c.get("video"):
                await send_cached_video(
                    msg.bot,
                    chat_id=uid,
                    file_path=f"images/cards/{c['video']}",
                    caption=txt,
                    width=c.get("width", 960),
                    height=c.get("height", 1280),
                    supports_streaming=True
                )
            else:
                await msg.answer_photo(photo=FSInputFile(f"images/cards/{c['file']}"), caption=txt)
        except Exception:
            await msg.answer(txt)

@router.message(Command("update_refs"))
async def update_refs_cmd(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return

    from database.db import generate_unique_ref_code

    # Получаем всех пользователей
    users = db_exec("SELECT id FROM users", fetchall=True)
    count = 0

    for (uid,) in users:
        new_code = generate_unique_ref_code()
        db_exec("UPDATE users SET referral_code = ? WHERE id = ?", (new_code, uid))
        count += 1

    await msg.answer(f"✅ Успешно обновлено {count} кодов! Теперь у всех уникальные ссылки из букв.")

# ================== ПЛАНИРОВЩИК УВЕДОМЛЕНИЙ ==================

async def cooldown_notification_scheduler(bot: Bot):
    """Фоновый task: уведомляет о сбросе кулдауна крутки.
    Premium = 1 час, обычные = 3 часа."""
    while True:
        try:
            users = get_users_for_cooldown_notify()
            now = datetime.now()
            for row in users:
                uid, last_get_str, premium_until_str = row

                # Проверка Premium статуса
                user_premium = False
                if premium_until_str:
                    try:
                        until_dt = datetime.strptime(premium_until_str, "%Y-%m-%d %H:%M:%S")
                        user_premium = until_dt > now
                    except Exception:
                        user_premium = False

                # Кулдаун в часах
                cd_hours = 1 if user_premium else GET_COOLDOWN_HOURS

                try:
                    last_get = datetime.strptime(last_get_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    last_get = datetime.min

                # Если время еще не пришло — пропускаем
                if (now - last_get).total_seconds() < cd_hours * 3600:
                    continue

                try:
                    await bot.send_message(
                        uid,
                        "🎴 Крутка восстановлена! Ты можешь получить новую карту."
                    )
                    mark_cooldown_notified(uid)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Cooldown scheduler error: {e}")
        await asyncio.sleep(60)


async def battle_cooldown_notification_scheduler(bot: Bot):
    """Фоновый task ТОЛЬКО для Premium: уведомляет о сбросе кулдауна боя (30 мин)."""
    while True:
        try:
            users = get_users_for_battle_cooldown_notify()
            now = datetime.now()
            for row in users:
                uid, last_battle_str, premium_until_str = row

                # Проверяем, не истек ли Premium за это время
                user_premium = False
                if premium_until_str:
                    try:
                        until_dt = datetime.strptime(premium_until_str, "%Y-%m-%d %H:%M:%S")
                        user_premium = until_dt > now
                    except Exception:
                        user_premium = False

                if not user_premium:
                    continue

                try:
                    last_battle = datetime.strptime(last_battle_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    last_battle = datetime.min

                # Кулдаун для Premium — 30 минут (0.5 часа)
                if (now - last_battle).total_seconds() < 0.5 * 3600:
                    continue
                try:
                    await bot.send_message(
                        uid,
                        "⚔️ Вы снова можете сражаться на Поле Битвы!\n"
                        "Отправляйтесь в бой и покажите свою силу."
                    )
                    mark_battle_cooldown_notified(uid)
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Battle cooldown scheduler error: {e}")
        await asyncio.sleep(60)
