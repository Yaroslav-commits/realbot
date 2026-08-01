import os
import asyncio
import logging
import sqlite3
import random
import calendar
from datetime import datetime, timedelta

from media_cache import send_cached_video
from aiogram import Bot, F, types
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           CallbackQuery, LabeledPrice, PreCheckoutQuery)
from aiogram.types import FSInputFile
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (BOT_TOKEN, ADMIN_IDS, DB_PATH,
                    GET_COOLDOWN_HOURS, BATTLE_COOLDOWN_HOURS,
                    MAIN_PRIZE_NORMAL_TITLE, MAIN_PRIZE_ROYALE_CARD)
from data.cards import (CARDS, RARITIES, BGS, VIDEO_BGS, TITLES,
                        NORMAL_PASS, ROYALE_PASS, ABSOLUTE_SKIN, AWAKENED_SKIN)
from database.db import (db_exec, init_db, get_user, add_user, get_rank,
                         pull_random_card, give_card_to_user, grant_retroactive_royale_pass,
                         add_pass_xp, check_and_update_quests, give_skin_to_user)
from handlers import (router, TradeState, SettingsState, PromoState,
                      MATCH_QUEUE, GAMES, PENDING_TRADES, kb_main)


# ============ МАГАЗИН И ПАСС ============
# Картинка магазина
SHOP_IMG = FSInputFile("images/shop/shop.png")

# Картинки паков
IMG_CAPSULE   = FSInputFile("images/shop/capsule.jpeg")
IMG_STASH     = FSInputFile("images/shop/stash.jpeg")
IMG_UNIVERSE  = FSInputFile("images/shop/universe.jpeg")
IMG_LEG_PACK  = FSInputFile("images/shop/pack_leg.jpeg")
IMG_EPIC_PACK = FSInputFile("images/shop/pack_epic.jpeg")

# Картинка Евента
EVENT_IMG = FSInputFile("images/shop/event.jpeg")

# Карты из них будут падать в паке "Свежие Вселенные"
LATEST_UNIVERSES = [
    "Башня Бога",
    "Борьба в прямом эфире",
    "Поднятие уровня в одиночку"
]

# Цены на паки (можешь менять в любой момент)
PRICE_CAPSULE  = 125   # 💎 Прем. Капсула
PRICE_STASH    = 40    # 💎 Тайник Обликов
PRICE_NEW_UNIV = 800   # 💴 Свежие Вселенные (Поставил 800, так как там есть шанс на Мифик)
PRICE_EPIC     = 100   # 💴 Эпический Пак
PRICE_LEG      = 450   # 💴 Легендарный Пак
# ==========================================
# Наборы алмазов: (звёзды, алмазы)
DIAMOND_PACKS = [
    (25, 75),
    (75, 250),
    (150, 600),
    (500, 2000),
    (1000, 5000),
]

# Крутки за алмазы: (алмазы, попытки)
SPIN_PACKS_DIA = [
    (50, 15),
    (100, 33),
    (250, 90),
    (500, 210),
    (1000, 475),
]

# Крутки за KRW: (krw, попытки)
SPIN_PACKS_KRW = [
    (75, 1),
    (375, 6),
    (750, 13),
    (1500, 28),
    (3750, 75),
]
# Фоны в магазине: ключ, цена, валюта, дата окончания (ГГГГ-ММ-ДД), текст даты
SHOP_BG_LIST = [
    {"id": "evil_ring", "price": 1500,  "currency": "krw", "icon": "💴", "ends_at": "2026-09-01", "date_str": "1-го Сентября"},
    {"id": "jin_woo_antares", "price": 8999, "currency": "krw", "icon": "💴", "ends_at": "2026-09-01", "date_str": "1-го Сентября"},
]
# ВИДЕО-ФОНЫ. Сюда кидаешь ключи тех фонов, которые у тебя загружены как видео.
VIDEO_BGS = {"jin_woo_antares", "admin, jaehwan", "king_grey"}

# ====== ЕВЕНТ ======
EVENT_ENABLED = False  # Включаем ивент!

def pull_universe_card(universes, weights_by_rarity):
    """Вытягивает карту из конкретных вселенных с учетом редкости"""
    pool = {r: [] for r in weights_by_rarity}
    for cid, c in CARDS.items():
        # ИСПРАВЛЕНИЕ: Жестко исключаем карты с exclusive = True
        if c.get('series') in universes and not c.get('exclusive', False):
            for r_key in pool:
                if r_key in c['rarity']:
                    pool[r_key].append(cid)

    valid_weights, valid_rarities = [], []
    for r, w in weights_by_rarity.items():
        if pool[r]:
            valid_rarities.append(r)
            valid_weights.append(w)

    if not valid_rarities:
        # Если ты ошибся в названии вселенной и пул пуст — выдаст рандомную карту игры
        return pull_random_card()

    chosen_rarity = random.choices(valid_rarities, weights=valid_weights, k=1)[0]
    return random.choice(pool[chosen_rarity])

def _shop_main_kb():
    bld = InlineKeyboardBuilder()
    bld.button(text="Купить алмазы 💎", callback_data="shop:dia")
    bld.button(text="Premium 🎫",       callback_data="shop:premium")
    bld.button(text="Крутки 🎴",        callback_data="shop:spins")
    bld.button(text="Фоны 🌄",          callback_data="shop:bgs:0")
    bld.button(text="Паки 🗃️",          callback_data="shop:packs")
    #bld.button(text="Ивент 🪎",         callback_data="shop:event")
    bld.adjust(1,2,2)
    return bld.as_markup()

# ── Красивый текст магазина ──────────────────────────────
SHOP_CAPTION = (
    "<tg-emoji emoji-id='5278702045883292456'>🛍</tg-emoji> <b>Добро пожаловать в Магазин!</b>\n\n"
    "<blockquote>"
    "Здесь ты найдёшь всё, что нужно настоящему бойцу:\n"
    "алмазы, крутки, паки, фоны и многое другое.\n"
    "Каждая покупка — шаг к победе! 💪"
    "</blockquote>\n\n"
    "Выбери раздел ниже 👇"
)

@router.message(F.text == "🛍 Магазин")
async def shop(msg: types.Message):
    await msg.answer_photo(
        photo=SHOP_IMG,
        caption=SHOP_CAPTION,
        reply_markup=_shop_main_kb(),
        parse_mode="HTML"
    )

async def _back_to_shop_main(cq: CallbackQuery):
    try:
        await cq.message.edit_media(
            media=types.InputMediaPhoto(media=SHOP_IMG, caption=SHOP_CAPTION, parse_mode="HTML"),
            reply_markup=_shop_main_kb()
        )
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer_photo(
            photo=SHOP_IMG, caption=SHOP_CAPTION,
            reply_markup=_shop_main_kb(),
            parse_mode="HTML"
        )

@router.callback_query(F.data == "shop:main")
async def shop_main_cb(cq: CallbackQuery):
    await _back_to_shop_main(cq)
    await cq.answer()

# ===== Купить алмазы =====
def _dia_kb():
    bld = InlineKeyboardBuilder()
    for stars, dia in DIAMOND_PACKS:
        bld.button(text=f"{stars} ⭐️ = {dia} 💎", callback_data=f"shop:dia_buy:{stars}:{dia}")
    bld.button(text="Назад 🔙", callback_data="shop:main")
    bld.adjust(1)
    return bld.as_markup()


@router.callback_query(F.data == "shop:dia")
async def shop_dia_cb(cq: CallbackQuery):
    try:
        await cq.message.edit_reply_markup(reply_markup=_dia_kb())
    except Exception as e:
        print(e)  # ← добавь для отладки
    await cq.answer()


@router.callback_query(F.data.startswith("shop:dia_buy:"))
async def shop_dia_buy_cb(cq: CallbackQuery, bot: Bot):
    _, _, stars, dia = cq.data.split(":")
    stars = int(stars); dia = int(dia)
    await bot.send_invoice(
        chat_id=cq.from_user.id,
        title=f"{dia} 💎",
        description=f"Пополнение баланса на {dia} алмазов",
        payload=f"dia_buy:{stars}:{dia}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{dia} 💎", amount=stars)]
    )
    await cq.answer()

# ===== Premium =====
PREMIUM_PRICE = 250  # алмазов
PREMIUM_BONUS_ATTEMPTS = 15
PREMIUM_BONUS_KRW = 1500
PREMIUM_CARD_KEY = "premium_card_2"

def _premium_kb():
    bld = InlineKeyboardBuilder()
    bld.button(text=f"Купить {PREMIUM_PRICE} 💎", callback_data="shop:premium_buy")
    bld.button(text="Назад 🔙", callback_data="shop:main")
    bld.adjust(1)
    return bld.as_markup()

@router.callback_query(F.data == "shop:premium")
async def shop_premium_cb(cq: CallbackQuery):
    txt = (
        "<b>Список бонусов на месяц, что вы получите при покупке Premium 👑</b>\n\n"
        "👑 Вы будете отображаться как премиум пользователь;\n"
        "📞 Уведомление о сбросе кулдауна в ⚔️ Поле Битвы;\n"
        "⌛️ Возможность получать карточки каждый 1 час вместо 3;\n"
        "⚔️ Возможность сражаться в поле битвы каждые пол часа (30м) вместо 1ч;\n"
        "🃏 Повышенная вероятность выпадения Легендарных, Мифических и Божественных карт;\n"
        "👤 Возможность изменять никнейм и добавлять в него эмодзи;\n"
        "🪙 Получение большего количества BATTLECOIN;\n"
        "🏅 Увеличение количества очков ранга за победу и ничью в PVP +1;\n\n"
        "При покупке Premium подписки на 1 месяц вы получаете сразу:\n"
        "15 круток 💳\n"
        "1500 KRW 💴\n"
        "Лимитированная карточка 🎴👑"
    )
    try:
        await cq.message.edit_caption(caption=txt, reply_markup=_premium_kb())
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer_photo(photo=SHOP_IMG, caption=txt, reply_markup=_premium_kb())
    await cq.answer()
@router.callback_query(F.data == "shop:premium_buy")
async def shop_premium_buy_cb(cq: CallbackQuery):
    from database.db import add_premium_months
    uid = cq.from_user.id
    u = get_user(uid)
    if not u:
        return await cq.answer("Пользователь не найден.", show_alert=True)
    if u[3] < PREMIUM_PRICE:
        return await cq.answer(f"❌ Недостаточно алмазов! Нужно: {PREMIUM_PRICE} 💎", show_alert=True)

    # Списываем алмазы и продлеваем Premium на 1 месяц
    db_exec("UPDATE users SET diamond = diamond - ? WHERE id = ?", (PREMIUM_PRICE, uid))
    new_until = add_premium_months(uid, months=1)

    # Молча начисляем крутки и валюту
    db_exec("UPDATE users SET attempts = attempts + ?, krw = krw + ? WHERE id = ?",
            (PREMIUM_BONUS_ATTEMPTS, PREMIUM_BONUS_KRW, uid))

    # Выдаём премиум-карту (как при выбивании). Дубликат не задвоится — будет повторка с KRW.
    if PREMIUM_CARD_KEY in CARDS:
        is_new, krw_earn, c = give_card_to_user(uid, PREMIUM_CARD_KEY)
        if is_new:
            txt_card = (f"🃏 Получена новая боевая карта!\n\n"
                        f"🎴 Персонаж: {c['name']}\n"
                        f"🔮 Редкость: {c['rarity']}\n"
                        f"👊 Стиль боя: {c['style']}\n"
                        f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
                        f"⚡️ Скорость: {c['speed']}\n"
                        f"💪 Сила: {c['strength']}\n"
                        f"🧠 Интеллект: {c['intellect']}")
        else:
            txt_card = (f"🛑 Вам попалась повторная карта! Вы получаете {krw_earn} 💴 KRW\n\n"
                        f"🎴 Персонаж: {c['name']}\n"
                        f"🔮 Редкость: {c['rarity']}\n"
                        f"👊 Стиль боя: {c['style']}\n"
                        f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
                        f"⚡️ Скорость: {c['speed']}\n"
                        f"💪 Сила: {c['strength']}\n"
                        f"🧠 Интеллект: {c['intellect']}")
        try:
            if "Божественная" in c.get("rarity", "") and c.get("video"):
                await cq.message.answer_video(
                    video=FSInputFile(f"images/cards/{c['video']}"),
                    caption=txt_card,
                    width=c.get("width", 960),
                    height=c.get("height", 1280),
                    has_spoiler=True,
                    supports_streaming=True
                )
            else:
                await cq.message.answer_photo(
                    photo=FSInputFile(f"images/cards/{c['file']}"),
                    caption=txt_card, has_spoiler=True
                )
        except Exception:
            await cq.message.answer(txt_card)

    await cq.message.answer(
        f"👑 Premium активирован до {new_until.strftime('%d.%m.%Y')}!\nСпасибо за покупку!"
    )
    await cq.answer("✅ Premium успешно куплен!", show_alert=True)


# ===== Крутки =====
SPIN_PAGES = ["dia", "krw"]
SPIN_DATA = {
    "dia": {"packs": SPIN_PACKS_DIA, "icon": "💎", "col": "diamond",    "col_idx": 3, "name": "алмазов"},
    "krw": {"packs": SPIN_PACKS_KRW, "icon": "💴", "col": "krw",        "col_idx": 4, "name": "KRW"},
}


def _spin_kb(page: int = 0):
    page = max(0, min(page, len(SPIN_PAGES) - 1))
    cur = SPIN_PAGES[page]
    info = SPIN_DATA[cur]

    bld = InlineKeyboardBuilder()
    for cost, att in info["packs"]:
        bld.row(InlineKeyboardButton(
            text=f"{cost}{info['icon']} = {att}💳",
            callback_data=f"shop:spin_buy:{cur}:{cost}:{att}",
        ))

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"shop:spins:{page - 1}"))
    if page < len(SPIN_PAGES) - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"shop:spins:{page + 1}"))
    if nav:
        bld.row(*nav)

    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="shop:main"))
    return bld.as_markup()


@router.callback_query(F.data.startswith("shop:spins"))
async def shop_spins_cb(cq: CallbackQuery):
    parts = cq.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else 0

    # Тексты для разных страниц круток
    descriptions = {
        0: "Здесь вы можете приобрести крутки за валюту <b>Алмазы 💎</b>",
        1: "Здесь вы можете приобрести крутки за валюту <b>KRW 💴</b>"
    }
    caption_text = descriptions.get(page, "Здесь вы можете приобрести крутки")

    try:
        await cq.message.edit_caption(
            caption=caption_text,
            reply_markup=_spin_kb(page),
            parse_mode="HTML"
        )
    except Exception:
        # На случай если текст не изменился, просто обновим кнопки
        try:
            await cq.message.edit_reply_markup(reply_markup=_spin_kb(page))
        except:
            pass

    await cq.answer()
@router.callback_query(F.data.startswith("shop:spin_buy:"))
async def shop_spin_buy_cb(cq: CallbackQuery):
    parts = cq.data.split(":")
    # формат: shop:spin_buy:<cur>:<cost>:<att>
    if len(parts) != 5:
        return await cq.answer("Ошибка покупки.", show_alert=True)
    cur, cost_s, att_s = parts[2], parts[3], parts[4]
    info = SPIN_DATA.get(cur)
    if not info:
        return await cq.answer("Неизвестная валюта.", show_alert=True)
    try:
        cost, att = int(cost_s), int(att_s)
    except ValueError:
        return await cq.answer("Ошибка покупки.", show_alert=True)
    u = get_user(cq.from_user.id)
    if not u:
        return await cq.answer("Пользователь не найден.", show_alert=True)
    if u[info["col_idx"]] < cost:
        return await cq.answer(
            f"❌ Недостаточно средств! Нужно: {cost}{info['icon']}",
            show_alert=True,
        )

    db_exec(
        f"UPDATE users SET {info['col']} = {info['col']} - ?, attempts = attempts + ? WHERE id = ?",
        (cost, att, cq.from_user.id),
    )
    await cq.answer(f"✅ Куплено {att} попыток!", show_alert=True)


# ===== Фоны (с листалкой) =====
@router.callback_query(F.data.startswith("shop:bgs:"))
async def shop_bgs_cb(cq: CallbackQuery):
    # Фильтруем список: показываем только те, чья дата окончания еще не наступила
    now_str = datetime.now().strftime("%Y-%m-%d")
    available_bgs = [bg for bg in SHOP_BG_LIST if bg.get("ends_at", "9999-12-31") >= now_str]

    if not available_bgs:
        return await cq.answer("К сожалению, лимитированные фоны закончились! 😔", show_alert=True)

    idx = int(cq.data.split(":")[2]) % len(available_bgs)
    item = available_bgs[idx]
    bg_data = BGS.get(item["id"])

    if not bg_data:
        return await cq.answer("Фон не найден в базе.", show_alert=True)

    # Формируем текст с названием, датой и цитатой цены
    caption = (
        f"🌄 Фон: {bg_data['name']}\n\n"
        f"🗓️ До {item.get('date_str', '5-го Июля')}\n"
        f"<blockquote>💰 Цена: {item['price']}{item['icon']}</blockquote>"
    )
    # Навигация
    left_idx = (idx - 1) % len(available_bgs)
    right_idx = (idx + 1) % len(available_bgs)

    bld = InlineKeyboardBuilder()
    if len(available_bgs) > 1:
        # Кнопки влево/вправо только если фонов больше одного
        bld.row(
            InlineKeyboardButton(text="<——", callback_data=f"shop:bgs:{left_idx}"),
            InlineKeyboardButton(text="🛍️ Купить", callback_data=f"shop:bg_buy:{item['id']}"),
            InlineKeyboardButton(text="——>", callback_data=f"shop:bgs:{right_idx}")
        )
    else:
        bld.row(InlineKeyboardButton(text="🛍️ Купить", callback_data=f"shop:bg_buy:{item['id']}"))

    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="shop:main"))

    # Видео или фото
    is_video = item["id"] in VIDEO_BGS
    file_path = f"images/backgrounds/{bg_data['file']}"

    try:
        await cq.message.delete()
    except Exception:
        pass

    try:
        if is_video:
            await send_cached_video(
                cq.bot,
                chat_id=cq.message.chat.id,
                file_path=file_path,
                caption=caption,
                reply_markup=bld.as_markup(),
                parse_mode="HTML",
                supports_streaming=True,
                width=bg_data.get('width'),
                height=bg_data.get('height')
            )
        else:
            await cq.message.answer_photo(
                photo=FSInputFile(file_path),
                caption=caption,
                reply_markup=bld.as_markup(),
                parse_mode="HTML"
            )
    except Exception:
        await cq.message.answer(
            f"{caption}\n\n[Фон не загрузился.]",
            reply_markup=bld.as_markup(),
            parse_mode="HTML"
        )

    await cq.answer()

@router.callback_query(F.data.startswith("shop:bg_buy:"))
async def shop_bg_buy_cb(cq: CallbackQuery):
    bg_id = cq.data.split(":")[2]
    item = next((b for b in SHOP_BG_LIST if b["id"] == bg_id), None)
    if not item:
        return await cq.answer("Фон не найден", show_alert=True)

    u = get_user(cq.from_user.id)
    col_map = {"bc": (5, "battlecoin"), "krw": (4, "krw"), "dia": (3, "diamond")}
    col_idx, col_name = col_map[item["currency"]]
    if u[col_idx] < item["price"]:
        return await cq.answer(f"❌ Недостаточно средств! Нужно: {item['price']}{item['icon']}", show_alert=True)

    has_bg = db_exec("SELECT 1 FROM bgs_inv WHERE user_id = ? AND bg_id = ?",
                     (cq.from_user.id, bg_id), fetch=True)
    if has_bg:
        return await cq.answer("У вас уже есть этот фон!", show_alert=True)

    db_exec(f"UPDATE users SET {col_name} = {col_name} - ? WHERE id = ?", (item['price'], cq.from_user.id))
    db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (cq.from_user.id, bg_id))
    await cq.answer("✅ Фон куплен и добавлен в «🌄 Мои фоны»!", show_alert=True)


# ===== Паки =====
def _packs_kb():
    bld = InlineKeyboardBuilder()
    bld.row(InlineKeyboardButton(text="🏆 Истинный Облик", callback_data="shop:pack_pre:capsule"))
    bld.row(InlineKeyboardButton(text="🔮 Тайник Обликов", callback_data="shop:pack_pre:stash"))
    bld.row(InlineKeyboardButton(text="🌌 Свежие Вселенные", callback_data="shop:pack_pre:universe"))
    bld.row(
        InlineKeyboardButton(text="🔵 Легендарный пак", callback_data="shop:pack_pre:leg"),
        InlineKeyboardButton(text="🟢 Эпический пак", callback_data="shop:pack_pre:epic")
    )
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="shop:main"))
    return bld.as_markup()


@router.callback_query(F.data == "shop:packs")
async def shop_packs(cq: CallbackQuery):
    txt = (
        "📦 <b>Магазин Наборов</b> 📦\n\n"
        "Добро пожаловать в раздел паков! Выберите интересующий вас набор, "
        "чтобы изучить его содержимое, точные шансы выпадения и перейти к покупке.\n\n"
        "<i>Каждый пак содержит уникальные награды для вашей коллекции персонажей и обликов!</i>"
    )
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.message.answer(txt, reply_markup=_packs_kb(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data.startswith("shop:pack_pre:"))
async def shop_pack_preview(cq: CallbackQuery):
    pack_type = cq.data.split(":")[2]
    bld = InlineKeyboardBuilder()

    if pack_type == "capsule":
        img = IMG_CAPSULE
        txt = (
            "🏆 <b>Капсула «Истинный Облик»</b>\n\n"
            "Премиальный элитный набор, гарантирующий получение косметического облика!\n\n"
            "✨ <b>Шансы распределения:</b>\n"
            "🔮 Абсолютный скин — <b>40%</b>\n"
            "💠 Пробужденный скин — <b>60%</b>\n\n"
            "♻️ <i>При выпадении дубликата вы мгновенно получаете назад 35% от потраченной суммы на баланс!</i>"
        )
        bld.row(InlineKeyboardButton(text=f"Купить за {PRICE_CAPSULE} 💎", callback_data="shop:pack_buy:capsule"))

    elif pack_type == "stash":
        img = IMG_STASH
        txt = (
            "🔮 <b>Тайник Обликов</b>\n\n"
            "Сбалансированный набор для тех, кто хочет получить ценных персонажей и испытать удачу в выпадении скинов!\n\n"
            "✨ <b>Шансы распределения:</b>\n"
            "🔮 Абсолютный скин — <b>4%</b>\n"
            "💠 Пробужденный скин — <b>16%</b>\n"
            "🔴 Мифическая карта — <b>10%</b>\n"
            "🔵 Легендарная карта — <b>30%</b>\n"
            "🟢 Эпическая карта — <b>40%</b>\n\n"
            "♻️ <i>Дубликаты скинов компенсируются возвратом 35% алмазов!</i>"
        )
        bld.row(InlineKeyboardButton(text=f"Купить за {PRICE_STASH} 💎", callback_data="shop:pack_buy:stash"))

    elif pack_type == "universe":
        img = IMG_UNIVERSE
        txt = (
            "🌌 <b>Пак «Свежие Вселенные»</b>\n\n"
            "Уникальный тематический набор, содержащий карты исключительно из последних добавленных тайтлов:\n"
            f"👉 <i>{', '.join(LATEST_UNIVERSES)}</i>\n\n"
            "✨ <b>Шансы распределения:</b>\n"
            "🔴 Мифическая карта — <b>10%</b>\n"
            "🔵 Легендарная карта — <b>40%</b>\n"
            "🟢 Эпическая карта — <b>50%</b>\n\n"
            "⚠️ <i>Божественные, Редкие и Обычные карты полностью исключены!</i>"
        )
        bld.row(InlineKeyboardButton(text=f"Купить за {PRICE_NEW_UNIV} 💴", callback_data="shop:pack_buy:universe"))

    elif pack_type == "leg":
        img = IMG_LEG_PACK
        txt = (
            "🔵 <b>Легендарный Пак</b>\n\n"
            "Элитный набор карт для профессиональных игроков. Идеальное решение для сбора лег на крафт!\n\n"
            "✨ <b>Содержимое:</b>\n"
            "🔵 Гарантированное получение <b>Легендарной карты (100%)</b>!"
        )
        bld.row(InlineKeyboardButton(text=f"Купить за {PRICE_LEG} 💴", callback_data="shop:pack_buy:leg"))

    else:  # epic
        img = IMG_EPIC_PACK
        txt = (
            "🟢 <b>Эпический Пак</b>\n\n"
            "Классический базовый набор карт, позволяющий быстро и уверенно усилить вашу основную колоду персонажей.\n\n"
            "✨ <b>Содержимое:</b>\n"
            "🟢 Гарантированное получение <b>Эпической карты (100%)</b>!"
        )
        bld.row(InlineKeyboardButton(text=f"Купить за {PRICE_EPIC} 💴", callback_data="shop:pack_buy:epic"))

    bld.row(InlineKeyboardButton(text="Назад к пакам 🔙", callback_data="shop:packs"))

    try:
        await cq.message.delete()
    except Exception:
        pass

    await cq.message.answer_photo(photo=img, caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


# --- ЗАЩИТА ОТ СПАМА И ДЮПА ПАКОВ ---
_pack_locks = set()


@router.callback_query(F.data.startswith("shop:pack_buy:"))
async def shop_pack_buy(cq: CallbackQuery):
    uid = cq.from_user.id

    # АНТИСПАМ: Если игрок нажал второй раз, пока пак еще крутится - блокируем
    if uid in _pack_locks:
        return await cq.answer("⏳ Открываем пак... Не нажимайте так быстро!", show_alert=True)
    _pack_locks.add(uid)

    try:
        pack_type = cq.data.split(":")[2]
        u = get_user(uid)
        dia, krw = u[3], u[4]

        cost_dia, cost_krw = 0, 0
        if pack_type == "capsule":
            cost_dia = PRICE_CAPSULE
        elif pack_type == "stash":
            cost_dia = PRICE_STASH
        elif pack_type == "universe":
            cost_krw = PRICE_NEW_UNIV
        elif pack_type == "leg":
            cost_krw = PRICE_LEG
        elif pack_type == "epic":
            cost_krw = PRICE_EPIC

        if cost_dia > 0 and dia < cost_dia:
            return await cq.answer(f"❌ Недостаточно Алмазов! Нужно: {cost_dia} 💎", show_alert=True)
        if cost_krw > 0 and krw < cost_krw:
            return await cq.answer(f"❌ Недостаточно средств! Нужно: {cost_krw} 💴", show_alert=True)

        if cost_dia > 0:
            db_exec("UPDATE users SET diamond = diamond - ? WHERE id = ?", (cost_dia, uid))
        if cost_krw > 0:
            db_exec("UPDATE users SET krw = krw - ? WHERE id = ?", (cost_krw, uid))

        result_type = "card"
        card_key = None
        skin_type = None

        if pack_type == "capsule":
            skin_type = random.choices(["absolute", "awakened"], weights=[40, 60], k=1)[0]
            result_type = "skin"
        elif pack_type == "stash":
            choices = ["skin_abs", "skin_awa", "mythic", "legendary", "epic"]
            weights = [4, 18, 8, 30, 40]
            res = random.choices(choices, weights=weights, k=1)[0]
            if res == "skin_abs":
                skin_type, result_type = "absolute", "skin"
            elif res == "skin_awa":
                skin_type, result_type = "awakened", "skin"
            elif res == "mythic":
                card_key = pull_random_card(force_rarity="Мифическая 🔴")
            elif res == "legendary":
                card_key = pull_random_card(force_rarity="Легендарная 🔵")
            else:
                card_key = pull_random_card(force_rarity="Эпическая 🟢")
        elif pack_type == "universe":
            weights = {'Мифическая 🔴': 10, 'Легендарная 🔵': 40, 'Эпическая 🟢': 50}
            card_key = pull_universe_card(LATEST_UNIVERSES, weights)
        elif pack_type == "leg":
            card_key = pull_random_card(force_rarity="Легендарная 🔵")
        elif pack_type == "epic":
            card_key = pull_random_card(force_rarity="Эпическая 🟢")

        bld = InlineKeyboardBuilder()
        bld.button(text="Открыть еще 🔄", callback_data=f"shop:pack_buy:{pack_type}")
        bld.button(text="Назад к пакам 🔙", callback_data="shop:packs")
        bld.adjust(1)

        try:
            await cq.message.delete()
        except Exception:
            pass

        # ЕСЛИ ВЫПАЛ СКИН:
        if result_type == "skin":
            pool = ABSOLUTE_SKIN if skin_type == "absolute" else AWAKENED_SKIN

            # ИСПРАВЛЕНИЕ: Исключаем эксклюзивные скины
            available_skins = [s_id for s_id, s_data in pool.items() if not s_data.get('exclusive', False)]
            if not available_skins:
                available_skins = list(pool.keys())  # Защита от пустой базы

            cid = random.choice(available_skins)
            c = CARDS[cid]

            is_new = give_skin_to_user(uid, cid, skin_type)
            type_name = "Абсолютный 🔮" if skin_type == "absolute" else "Пробужденный 💠"

            if is_new:
                txt = (
                    f"<b>Вам выпал новый скин!</b>\n\n"
                    f"🎭 <b>Тип:</b> {type_name}\n"
                    f"🃏 <b>Для карты:</b> {c['name']}\n\n"
                    f"<i>Облик добавлен в вашу Галерею 🏵️</i>"
                )
                await cq.answer("🎉 Получен редчайший скин!", show_alert=True)
            else:
                # Математический расчет: ровно 35% от стоимости конкретного пака
                spent = PRICE_CAPSULE if pack_type == "capsule" else PRICE_STASH
                cashback = int(spent * 0.35)

                db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (cashback, uid))
                txt = (
                    f"♻️ <b>ДУБЛИКАТ СКИНА!</b>\n\n"
                    f"Вам выпал <b>{type_name}</b> для <b>{c['name']}</b>, но он у вас уже есть!\n\n"
                    f"💎 <b>Утешительный бонус (35% от цены пака): +{cashback} Алмазов!</b>"
                )
                await cq.answer(f"♻️ Дубликат скина! Бонус: +{cashback} Алмазов", show_alert=True)

            is_video = (skin_type == "absolute")
            asset_path = f"images/cards/{pool[cid].get('skin_video_file') or pool[cid].get('skin_art_file')}"

            if is_video:
                from media_cache import send_cached_video
                await send_cached_video(
                    cq.bot, chat_id=uid, file_path=asset_path, caption=txt,
                    width=c.get("width", 960), height=c.get("height", 1280),
                    reply_markup=bld.as_markup(), parse_mode="HTML", supports_streaming=True
                )
            else:
                await cq.message.answer_photo(
                    photo=FSInputFile(asset_path), caption=txt, parse_mode="HTML", reply_markup=bld.as_markup()
                )

        # ЕСЛИ ВЫПАЛА КАРТА:
        else:
            is_new, krw_earn, card_c = give_card_to_user(uid, card_key)

            if is_new:
                txt = (
                    f"🎉 <b>Поздравляем! Вам выпала новая карта!</b>\n\n"
                    f"🎴 <b>Персонаж:</b> {card_c['name']}\n"
                    f"🔮 <b>Редкость:</b> {card_c['rarity']}\n"
                    f"👊 <b>Стиль боя:</b> {card_c['style']}\n"
                    f"🪐 <b>Вселенная:</b> {card_c.get('series', 'Неизвестно')}\n\n"
                    f"⚡️ <b>Скорость:</b> {card_c['speed']}\n"
                    f"💪 <b>Сила:</b> {card_c['strength']}\n"
                    f"🧠 <b>Интеллект:</b> {card_c['intellect']}"
                )
                alert_text = "🎉 Получен новый персонаж!"
            else:
                txt = (
                    f"🛑 <b>Вам попалась повторная карта!</b>\n"
                    f"Вы получаете компенсацию: <b>{krw_earn} 💴</b>\n\n"
                    f"🎴 <b>Персонаж:</b> {card_c['name']}\n"
                    f"🔮 <b>Редкость:</b> {card_c['rarity']}\n"
                    f"🪐 <b>Вселенная:</b> {card_c.get('series', 'Неизвестно')}"
                )
                alert_text = f"✅ Персонаж уже был у вас и конвертирован в {krw_earn} KRW!"

            try:
                if "Божественная" in card_c.get("rarity", "") and card_c.get("video"):
                    from media_cache import send_cached_video
                    await send_cached_video(
                        cq.bot, chat_id=uid, file_path=f"images/cards/{card_c['video']}",
                        caption=txt, width=card_c.get("width", 960), height=card_c.get("height", 1280),
                        reply_markup=bld.as_markup(), has_spoiler=True, supports_streaming=True
                    )
                else:
                    await cq.message.answer_photo(
                        photo=FSInputFile(f"images/cards/{card_c['file']}"),
                        caption=txt, parse_mode="HTML", reply_markup=bld.as_markup(), has_spoiler=True
                    )
            except Exception:
                await cq.message.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())

            await cq.answer(alert_text, show_alert=True)

        # === MANHWCARD PASS: ОПЫТ ЗА ПАКИ И КВЕСТЫ ===
        earned_xp = 0
        if pack_type == "epic":
            earned_xp = 45
            check_and_update_quests(uid, 'q_3_epic_packs', 1)
        elif pack_type == "leg":
            earned_xp = 95
        elif pack_type == "universe":
            earned_xp = 140

        if earned_xp > 0:
            xp_res = add_pass_xp(uid, earned_xp)
            if xp_res and xp_res.get("leveled_up"):
                try:
                    await cq.bot.send_message(
                        uid,
                        f"⚡️ <b>[СИСТЕМА]</b>\n\n"
                        f"Уровень ManhwCard Pass повышен!\n"
                        f"Текущий уровень: <b>{xp_res['level']}</b>.\n\n"
                        f"<i>Зайдите в Web App, чтобы забрать награду.</i>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
    finally:
        # ОБЯЗАТЕЛЬНО: Снимаем блокировку, чтобы игрок мог купить следующий пак
        _pack_locks.discard(uid)

# ===== Единая обработка оплат (алмазы через звёзды + Рояль пасс) =====
@router.pre_checkout_query()
async def universal_pre_checkout(pcq: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(pcq.id, ok=True)

@router.message(F.successful_payment)
async def universal_success_payment(msg: types.Message):
    payload = (msg.successful_payment.invoice_payload or "")
    if payload.startswith("dia_buy"):
        parts = payload.split(":")
        dia = int(parts[2])
        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (dia, msg.from_user.id))
        await msg.answer(f"✅ Оплата принята! На баланс зачислено {dia} 💎")
    elif payload == "rp_buy":
        uid = msg.from_user.id
        summary = grant_retroactive_royale_pass(uid)
        await msg.answer(f"✅ Вы успешно приобрели Рояль Пасс на этот месяц!{summary}")
    else:
        await msg.answer("✅ Оплата принята!")


# ============ ПАСС ============
from datetime import datetime, timezone, timedelta
import calendar

# Жесткая привязка к МСК (UTC+3)
MSK = timezone(timedelta(hours=3))

PASS_NORMAL_IMG_1 = FSInputFile("images/shop/pass_normal_1.jpeg")
PASS_NORMAL_IMG_2 = FSInputFile("images/shop/pass_normal_2.jpeg")
PASS_NORMAL_IMG_3 = FSInputFile("images/shop/pass_normal_3.jpeg")
PASS_NORMAL_IMG_4 = FSInputFile("images/shop/pass_normal_4.jpeg")
PASS_NORMAL_IMG_5 = FSInputFile("images/shop/pass_normal_5.jpeg")

PASS_ROYALE_IMG_1 = FSInputFile("images/shop/pass_royale_1.jpeg")
PASS_ROYALE_IMG_2 = FSInputFile("images/shop/pass_royale_2.jpeg")
PASS_ROYALE_IMG_3 = FSInputFile("images/shop/pass_royale_3.jpeg")
PASS_ROYALE_IMG_4 = FSInputFile("images/shop/pass_royale_4.jpeg")
PASS_ROYALE_IMG_5 = FSInputFile("images/shop/pass_royale_5.jpeg")


@router.message(F.text == "🏞️ Пасс")
async def pass_menu(msg: types.Message):
    bld = InlineKeyboardBuilder()
    bld.button(text="🏙️ Обычный пасс", callback_data="pass:normal:start")
    bld.button(text="🌠 Рояль пасс", callback_data="pass:royale:start")
    await msg.answer("Выберите Пасс:", reply_markup=bld.as_markup())


@router.callback_query(F.data == "pass_back")
async def pass_back(cq: CallbackQuery):
    await cq.message.delete()
    await pass_menu(cq.message)


@router.callback_query(F.data.startswith("pass:"))
async def show_pass(cq: CallbackQuery):
    try:
        _, p_type, page_str = cq.data.split(":")
        uid = cq.from_user.id
        u = get_user(uid)

        if not u:
            return await cq.answer("❌ Пользователь не найден в БД!", show_alert=True)

        now = datetime.now(MSK)
        _, days_in_month = calendar.monthrange(now.year, now.month)

        # Автоматическое открытие страницы с текущим днем при первом входе
        if page_str == "start":
            if now.day <= 6:
                page = 0
            elif now.day <= 12:
                page = 1
            elif now.day <= 18:
                page = 2
            elif now.day <= 24:
                page = 3
            else:
                page = 4
        else:
            page = int(page_str)

        await render_pass_page(cq, p_type, page, u, now, days_in_month)

        # Обязательно подтверждаем нажатие, чтобы кнопка не "висла"
        try:
            await cq.answer()
        except:
            pass

    except Exception as e:
        logging.error(f"Ошибка в show_pass: {e}")
        await cq.answer(f"⚠️ Ошибка открытия пасса: {e}", show_alert=True)


async def render_pass_page(cq: CallbackQuery, p_type: str, page: int, u: tuple, now: datetime, days_in_month: int):
    uid = u[0]
    is_royale = (p_type == "royale")
    current_ym = int(now.strftime("%Y%m"))

    if is_royale and u[16] != current_ym:
        bld = InlineKeyboardBuilder()
        bld.button(text="Купить ⭐️", callback_data="buy_royale_pass")
        bld.button(text="Назад 🔙", callback_data="pass_back")
        bld.adjust(1)
        try:
            await cq.message.edit_media(
                media=types.InputMediaPhoto(media=PASS_ROYALE_IMG_1,
                                            caption="🌠 Рояль пасс\n\n425 алмазов 💎\n4x больше наград 🏆\n🃏 Лимитированная карта: Ю Соль Ха\n⚠️ Данный пасс у вас ещё не приобретен."),
                reply_markup=bld.as_markup()
            )
        except Exception:
            try:
                await cq.message.delete()
            except:
                pass
            await cq.message.answer_photo(photo=PASS_ROYALE_IMG_1,
                                          caption="🌠 Рояль пасс\n\n425 алмазов 💎\n4x больше наград 🏆\n🃏 Лимитированная карта: Ю Соль Ха\n\n⚠️ Данный пасс у вас ещё не приобретен.",
                                          reply_markup=bld.as_markup())
        return

    data = ROYALE_PASS if is_royale else NORMAL_PASS
    imgs_normal = [PASS_NORMAL_IMG_1, PASS_NORMAL_IMG_2, PASS_NORMAL_IMG_3, PASS_NORMAL_IMG_4, PASS_NORMAL_IMG_5]
    imgs_royale = [PASS_ROYALE_IMG_1, PASS_ROYALE_IMG_2, PASS_ROYALE_IMG_3, PASS_ROYALE_IMG_4, PASS_ROYALE_IMG_5]

    # Защита от выхода за пределы списка картинок
    page_safe = max(0, min(page, 4))
    img = imgs_royale[page_safe] if is_royale else imgs_normal[page_safe]

    start_d = page * 6 + 1
    end_d = min(start_d + 5, days_in_month)
    if page == 4:
        end_d = days_in_month

    claims = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
                     (uid, now.month, p_type), fetchall=True)
    claimed_days = [d[0] for d in claims] if claims else []

    pass_name = "🌠 Рояль пасс" if is_royale else "🏙️ Обычный пасс"
    icons = {'krw': '💴', 'atm': '💳', 'bc': '🪙', 'dia': '💎', 'pack': '🗃️'}
    pack_names = {'epic': 'Эпический пак 🟢', 'leg': 'Легендарный Пак 🔵'}

    rewards_txt = ""
    for d in range(start_d, end_d + 1):
        r_type, r_val = data.get(d, ('krw', 10))
        if r_type == 'pack':
            r_str = pack_names.get(r_val, 'Пак')
        else:
            r_str = f"{r_val} {icons.get(r_type, '')}"

        # Игнорируем технический "99 день" при отрисовке
        if d == 99: continue

        mark = "✅" if d in claimed_days else ("❌" if d < now.day else ("🎯" if d == now.day else "🕓"))
        rewards_txt += f"{mark} {d} день — {r_str}\n"

    # Считаем реальные дни (без учета 99-го технического дня за главный приз)
    real_claimed = len([d for d in claimed_days if d != 99])

    txt = (
        f"<b>{pass_name}</b>\n\n"
        f"🟢 Заходи каждый день и забирай награды. Сегодня <b>{now.day}-й</b> день.\n\n"
        f"<blockquote>🎁 Награды на этой странице:\n{rewards_txt}</blockquote>\n"
        f"<b>Обозначения:</b>\n"
        f"❌ — День пропущен\n"
        f"✅ — Награда получена\n"
        f"🎯 — Забери сегодня!\n"
        f"🕓 — Ещё не наступил\n\n"
        f"Получено дней — <b>{real_claimed}/{days_in_month}</b>"
    )

    bld = InlineKeyboardBuilder()

    # Кнопки ячеек
    cells = []
    for i in range(5):
        text = f"[{i + 1}]" if i == page else str(i + 1)
        cells.append(InlineKeyboardButton(text=text, callback_data=f"pass:{p_type}:{i}"))
    bld.row(*cells)

    # Кнопки дней
    day_buttons = []
    for d in range(start_d, end_d + 1):
        if d in claimed_days:
            status = "✅"
        elif d < now.day:
            status = "❌"
        elif d == now.day:
            status = "🎯"
        else:
            status = "🕓"
        day_buttons.append(InlineKeyboardButton(text=f"{status} {d}", callback_data=f"claim_pass:{p_type}:{d}:{page}"))

    for i in range(0, len(day_buttons), 3):
        bld.row(*day_buttons[i:i + 3])

    bld.row(InlineKeyboardButton(text="Купить дни 💎", callback_data=f"buy_days_menu:{p_type}"))

    # Главный приз только на последней ячейке
    if page == 4:
        bld.row(InlineKeyboardButton(text="Главный приз 🐦‍🔥", callback_data=f"pass_main_prize:{p_type}"))

    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="pass_back"))

    try:
        await cq.message.edit_media(
            media=types.InputMediaPhoto(media=img, caption=txt, parse_mode="HTML"),
            reply_markup=bld.as_markup()
        )
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            try:
                await cq.message.delete()
                await cq.message.answer_photo(photo=img, caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
            except Exception as inner_e:
                logging.error(f"Не удалось отправить фото: {inner_e}")
                # Фолбэк на случай, если картинки не найдены вообще
                await cq.message.answer(f"⚠️ Ошибка отображения: {inner_e}\n\n{txt}", reply_markup=bld.as_markup(),
                                        parse_mode="HTML")


# ── Хранилище выбранных дней (в памяти, per-user) ──────────
_selected_days: dict[int, dict] = {}
# структура: { uid: { "p_type": str, "days": set[int] } }

# ── Хранилище блокировок для защиты от спам-кликов ──────────
_claim_locks = set()

@router.callback_query(F.data.startswith("claim_pass:"))
async def claim_pass(cq: CallbackQuery):
    _, p_type, day_str, page_str = cq.data.split(":")
    day = int(day_str)
    page = int(page_str)
    uid = cq.from_user.id
    now = datetime.now(MSK)

    # Защита от двойного клика
    lock_key = f"{uid}_{p_type}_{day}"
    if lock_key in _claim_locks:
        return await cq.answer("⏳ Обработка... Не спамь!", show_alert=True)
    _claim_locks.add(lock_key)

    try:
        is_claimed = db_exec(
            "SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = ?",
            (uid, now.month, day, p_type), fetch=True
        )
        if is_claimed:
            return await cq.answer("Вы уже забрали эту награду! ✅", show_alert=True)

        if day > now.day:
            return await cq.answer("Этот день ещё не наступил! 🕓", show_alert=True)

        if day < now.day:
            return await cq.answer("День пропущен ❌\nИспользуй «Купить дни 💎»", show_alert=True)

        u = get_user(uid)
        current_ym = int(now.strftime("%Y%m"))
        has_royale = (u[16] == current_ym)

        # 1. Выдаем награду за выбранный пасс
        data = ROYALE_PASS if p_type == "royale" else NORMAL_PASS
        r_type, r_val = data.get(day, ('krw', 10))
        reward_msg = await _give_pass_reward(uid, p_type, day, r_type, r_val, cq.message)

        db_exec(
            "INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
            (uid, now.month, day, p_type)
        )

        # 2. АВТО-СИНХРОНИЗАЦИЯ: Если есть Рояль, сразу собираем вторую награду (чтобы дни не пропускались)
        other_p_type = "royale" if p_type == "normal" else "normal"
        if has_royale:
            is_claimed_other = db_exec(
                "SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = ?",
                (uid, now.month, day, other_p_type), fetch=True
            )
            if not is_claimed_other:
                other_data = ROYALE_PASS if other_p_type == "royale" else NORMAL_PASS
                o_type, o_val = other_data.get(day, ('krw', 10))
                o_msg = await _give_pass_reward(uid, other_p_type, day, o_type, o_val, cq.message)
                db_exec(
                    "INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
                    (uid, now.month, day, other_p_type)
                )
                if o_msg:
                    reward_msg = f"{reward_msg}\n\n{o_msg}" if reward_msg else o_msg

        icons = {'krw': '💴', 'atm': '💳', 'bc': '🪙', 'dia': '💎', 'pack': '🗃️'}
        icon = icons.get(r_type, '')
        val_str = r_val if r_type != 'pack' else '🗃️ Пак'
        await cq.answer(f"✅ Награда получена: {val_str} {icon}", show_alert=True)

        if reward_msg:
            await cq.message.answer(reward_msg, parse_mode="HTML")

            # === MANHWCARD PASS: 100 XP ЗА СБОР ДНЯ В ПАССЕ ===
        from database.db import add_pass_xp
        xp_res = add_pass_xp(uid, 100)

        if xp_res["leveled_up"]:
            try:
                await cq.bot.send_message(
                    uid,
                    f"⚡️ <b>[СИСТЕМА]</b>\n\n"
                    f"Требования выполнены.\n"
                    f"Ваш уровень ManhwCard Pass повышен!\n"
                    f"Текущий уровень: <b>{xp_res['level']}</b>.\n\n"
                    f"<i>Зайдите в Web App, чтобы забрать награду.</i>",
                    parse_mode="HTML"
                )
            except:
                pass

        # Обновляем менюшку
        u2 = get_user(uid)
        _, days_in_month = calendar.monthrange(now.year, now.month)
        await render_pass_page(cq, p_type, page, u2, now, days_in_month)
    finally:
        # Снимаем блокировку
        _claim_locks.discard(lock_key)

async def _give_pass_reward(uid, p_type, day, r_type, r_val, message=None):
    """Начисляет награду и возвращает текст для отправки в чат (или None)."""
    icons = {'krw': '💴', 'atm': '💳', 'bc': '🪙', 'dia': '💎', 'pack': '🗃️'}
    pass_label = "🌠 Рояль Пасс" if p_type == "royale" else "🏙️ Обычный Пасс"
    if r_type == 'krw':
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (r_val, uid))
        return f"🎁 <b>{pass_label} · День {day}</b>\n\n<blockquote>Получено: <b>{r_val} 💴 KRW</b></blockquote>"
    elif r_type == 'atm':
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (r_val, uid))
        return f"🎁 <b>{pass_label} · День {day}</b>\n\n<blockquote>Получено: <b>{r_val} 💳 круток</b></blockquote>"
    elif r_type == 'bc':
        db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (r_val, uid))
        return f"🎁 <b>{pass_label} · День {day}</b>\n\n<blockquote>Получено: <b>{r_val} 🪙 Battlecoin</b></blockquote>"
    elif r_type == 'dia':
        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (r_val, uid))
        return f"🎁 <b>{pass_label} · День {day}</b>\n\n<blockquote>Получено: <b>{r_val} 💎 алмазов</b></blockquote>"
    elif r_type == 'pack':
        force_r = "Легендарная 🔵" if r_val == "leg" else "Эпическая 🟢"
        card_key = pull_random_card(force_rarity=force_r) or pull_random_card()
        is_new, krw_earn, c = give_card_to_user(uid, card_key)
        if c:
            if is_new:
                card_txt = (
                    f"🃏 <b>Получена новая боевая карта!</b>\n\n"
                    f"🎴 Персонаж: <b>{c['name']}</b>\n"
                    f"🔮 Редкость: {c['rarity']}\n"
                    f"👊 Стиль боя: {c['style']}\n"
                    f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
                    f"⚡️ Скорость: {c['speed']}\n"
                    f"💪 Сила: {c['strength']}\n"
                    f"🧠 Интеллект: {c['intellect']}"
                )
            else:
                card_txt = (
                    f"🛑 <b>Повторная карта!</b> Получено <b>{krw_earn} 💴 KRW</b>\n\n"
                    f"🎴 Персонаж: <b>{c['name']}</b>\n"
                    f"🔮 Редкость: {c['rarity']}\n"
                    f"👊 Стиль боя: {c['style']}\n"
                    f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
                    f"⚡️ Скорость: {c['speed']}\n"
                    f"💪 Сила: {c['strength']}\n"
                    f"🧠 Интеллект: {c['intellect']}"
                )
            if message:
                try:
                    if "Божественная" in c.get("rarity", "") and c.get("video"):
                        await message.answer_video(
                            video=FSInputFile(f"images/cards/{c['video']}"),
                            caption=card_txt, parse_mode="HTML",
                            width=c.get("width", 960), height=c.get("height", 1280),
                            has_spoiler=True, supports_streaming=True
                        )
                    else:
                        await message.answer_photo(
                            photo=FSInputFile(f"images/cards/{c['file']}"),
                            caption=card_txt, parse_mode="HTML", has_spoiler=True
                        )
                    return None  # уже отправили
                except Exception:
                    return card_txt
    return None

async def _render_buy_days_menu(cq: CallbackQuery, uid: int, p_type: str, missed_days: list, bought_count: int,
                                now: datetime):
    # Получаем уже выбранные пользователем дни из памяти
    sel = _selected_days.get(uid, {}).get("days", set())

    txt = (
        f"💎 <b>Покупка пропущенных дней</b>\n\n"
        f"Выберите дни, которые хотите восстановить.\n"
        f"<i>С каждым купленным днём в этом месяце цена возрастает на 15💎!</i>\n\n"
        f"Уже куплено дней в этом месяце: <b>{bought_count}</b>"
    )

    bld = InlineKeyboardBuilder()

    # Кнопки пропущенных дней
    buttons = []
    for d in missed_days:
        mark = "✅ " if d in sel else ""
        buttons.append(InlineKeyboardButton(text=f"{mark}День {d}", callback_data=f"toggle_missed_day:{p_type}:{d}"))

    # Группируем их по 3 штуки в ряд
    for i in range(0, len(buttons), 3):
        bld.row(*buttons[i:i + 3])

    # Если хоть один день выбран, показываем кнопку "Купить"
    if sel:
        # Считаем сумму с учётом прогрессии
        costs = [(bought_count + i + 1) * 15 for i in range(len(sel))]
        total = sum(costs)
        bld.row(
            InlineKeyboardButton(text=f"🛒 Купить выбранные ({total} 💎)", callback_data=f"confirm_buy_days:{p_type}"))

    # Кнопка возврата в меню пасса
    # Отправляем на нулевую страницу, чтобы избежать ошибок навигации
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data=f"pass:{p_type}:0"))

    try:
        await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data.startswith("buy_days_menu:"))
async def buy_days_menu(cq: CallbackQuery):
    _, p_type = cq.data.split(":")
    uid = cq.from_user.id
    now = datetime.now(MSK)

    db_exec("CREATE TABLE IF NOT EXISTS pass_bought_days (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")

    claims = db_exec(
        "SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
        (uid, now.month, p_type), fetchall=True
    )
    claimed_days = [d[0] for d in claims]
    missed_days = [d for d in range(1, now.day) if d not in claimed_days]

    if not missed_days:
        return await cq.answer("У вас нет пропущенных дней! 🎉", show_alert=True)

    # Считаем общее количество купленных дней в этом месяце (без привязки к типу пасса)
    bought_count = db_exec(
        "SELECT COUNT(DISTINCT day) FROM pass_bought_days WHERE user_id = ? AND month = ?",
        (uid, now.month), fetch=True
    )[0]

    _selected_days[uid] = {"p_type": p_type, "days": set()}
    await _render_buy_days_menu(cq, uid, p_type, missed_days, bought_count, now)
    await cq.answer()


@router.callback_query(F.data.startswith("toggle_missed_day:"))
async def toggle_missed_day(cq: CallbackQuery):
    _, p_type, day_str = cq.data.split(":")
    day = int(day_str)
    uid = cq.from_user.id
    now = datetime.now(MSK)

    if uid not in _selected_days or _selected_days[uid].get("p_type") != p_type:
        _selected_days[uid] = {"p_type": p_type, "days": set()}

    sel = _selected_days[uid]["days"]
    if day in sel:
        sel.discard(day)
        await cq.answer(f"Снят выбор дня {day}")
    else:
        sel.add(day)
        await cq.answer(f"День {day} выбран ✳️")

    db_exec("CREATE TABLE IF NOT EXISTS pass_bought_days (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
    claims = db_exec(
        "SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
        (uid, now.month, p_type), fetchall=True
    )
    claimed_days = [d[0] for d in claims]
    missed_days = [d for d in range(1, now.day) if d not in claimed_days]

    bought_count = db_exec(
        "SELECT COUNT(DISTINCT day) FROM pass_bought_days WHERE user_id = ? AND month = ?",
        (uid, now.month), fetch=True
    )[0]
    await _render_buy_days_menu(cq, uid, p_type, missed_days, bought_count, now)


@router.callback_query(F.data.startswith("confirm_buy_days:"))
async def confirm_buy_days(cq: CallbackQuery):
    _, p_type = cq.data.split(":")
    uid = cq.from_user.id
    now = datetime.now(MSK)

    sel_data = _selected_days.get(uid)
    if not sel_data or not sel_data.get("days"):
        return await cq.answer("Не выбрано ни одного дня!", show_alert=True)
    selected = sorted(sel_data["days"])
    db_exec("CREATE TABLE IF NOT EXISTS pass_bought_days (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")

    bought_count = db_exec(
        "SELECT COUNT(DISTINCT day) FROM pass_bought_days WHERE user_id = ? AND month = ?",
        (uid, now.month), fetch=True
    )[0]

    costs = [(bought_count + i + 1) * 15 for i in range(len(selected))]
    total_cost = sum(costs)

    days_str = "\n".join(f"  • День {d}" for d in selected)
    txt = (
        "🔂 <b>Покупка пропущенных дней</b>\n\n"
        f"<blockquote>Вы выбрали:\n{days_str}</blockquote>\n\n"
        f"💎 К оплате: <b>{total_cost} алмазов</b>\n\n"
        "Подтвердите покупку:"
    )
    bld = InlineKeyboardBuilder()
    bld.row(
        InlineKeyboardButton(text=f"✅ Подтвердить ({total_cost} 💎)", callback_data=f"exec_buy_days:{p_type}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"buy_days_menu:{p_type}")
    )
    try:
        await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    await cq.answer()


@router.callback_query(F.data.startswith("exec_buy_days:"))
async def exec_buy_days(cq: CallbackQuery):
    _, p_type = cq.data.split(":")
    uid = cq.from_user.id
    now = datetime.now(MSK)

    sel_data = _selected_days.get(uid)
    if not sel_data or not sel_data.get("days"):
        return await cq.answer("Выбор устарел, зайди заново.", show_alert=True)

    selected = sorted(sel_data["days"])
    db_exec("CREATE TABLE IF NOT EXISTS pass_bought_days (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")

    bought_count = db_exec(
        "SELECT COUNT(DISTINCT day) FROM pass_bought_days WHERE user_id = ? AND month = ?",
        (uid, now.month), fetch=True
    )[0]

    costs = [(bought_count + i + 1) * 15 for i in range(len(selected))]
    total_cost = sum(costs)

    u = get_user(uid)
    if u[3] < total_cost:
        return await cq.answer(f"❌ Недостаточно алмазов! Нужно: {total_cost} 💎", show_alert=True)

    current_ym = int(now.strftime("%Y%m"))
    has_royale = (u[16] == current_ym)

    db_exec("UPDATE users SET diamond = diamond - ? WHERE id = ?", (total_cost, uid))

    reward_lines = []

    for day in selected:
        # 1. Восстанавливаем Обычный Пасс
        already_normal = db_exec(
            "SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = 'normal'",
            (uid, now.month, day), fetch=True
        )
        if not already_normal:
            n_type, n_val = NORMAL_PASS.get(day, ('krw', 10))
            n_text = await _give_pass_reward(uid, "normal", day, n_type, n_val, cq.message)
            if n_text: reward_lines.append(n_text)

            db_exec("INSERT INTO pass_bought_days (user_id, month, day, pass_type) VALUES (?, ?, ?, 'normal')",
                    (uid, now.month, day))
            db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, 'normal')",
                    (uid, now.month, day))

        # 2. Восстанавливаем Рояль Пасс (если он куплен)
        if has_royale:
            already_royale = db_exec(
                "SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = 'royale'",
                (uid, now.month, day), fetch=True
            )
            if not already_royale:
                r_type, r_val = ROYALE_PASS.get(day, ('krw', 10))
                r_text = await _give_pass_reward(uid, "royale", day, r_type, r_val, cq.message)
                if r_text: reward_lines.append(r_text)

                db_exec("INSERT INTO pass_bought_days (user_id, month, day, pass_type) VALUES (?, ?, ?, 'royale')",
                        (uid, now.month, day))
                db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, 'royale')",
                        (uid, now.month, day))

    _selected_days.pop(uid, None)

    # === MANHWCARD PASS: 100 XP ЗА КАЖДЫЙ ВОССТАНОВЛЕННЫЙ ДЕНЬ ===
    from database.db import add_pass_xp
    xp_res = add_pass_xp(uid, 100 * len(selected))

    if xp_res["leveled_up"]:
        try:
            await cq.bot.send_message(
                uid,
                f"⚡️ <b>[СИСТЕМА]</b>\n\n"
                f"Требования выполнены.\n"
                f"Ваш уровень ManhwCard Pass повышен!\n"
                f"Текущий уровень: <b>{xp_res['level']}</b>.\n\n"
                f"<i>Зайдите в Web App, чтобы забрать награду.</i>",
                parse_mode="HTML"
            )
        except:
            pass

    summary_days = ", ".join(str(d) for d in selected)
    result_txt = (
        f"✅ <b>Пропущенные дни восстановлены!</b>\n\n"
        f"<blockquote>Дни: {summary_days}\nПотрачено: {total_cost} 💎\n"
    )
    if has_royale:
        result_txt += "\n🌠 Награды зачислены в ОБА пасса!"
    result_txt += "</blockquote>"

    await cq.message.answer(result_txt, parse_mode="HTML")
    await cq.answer("✅ Готово!", show_alert=True)

    u2 = get_user(uid)
    _, days_in_month = calendar.monthrange(now.year, now.month)
    await render_pass_page(cq, p_type, 0, u2, now, days_in_month)

@router.callback_query(F.data.startswith("pass_main_prize:"))
async def pass_main(cq: CallbackQuery):
    p_type = cq.data.split(":")[1]
    uid = cq.from_user.id
    now = datetime.now(MSK)
    _, dim = calendar.monthrange(now.year, now.month)

    # 1. Проверяем, забрал ли игрок ВСЕ обычные дни (исключаем день 99 из подсчета, если он там есть)
    claims = db_exec("SELECT COUNT(*) FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ? AND day <= ?",
                     (uid, now.month, p_type, dim), fetch=True)

    if claims[0] < dim:
        return await cq.answer("❌ Соберите награды за все дни месяца!", show_alert=True)

    # 🔥 ФИКС БЕСКОНЕЧНОГО ДЮПА ТРЕЙДАМИ:
    # Проверяем не инвентарь, а ИСТОРИЮ получения главного приза в этом месяце (день 99)
    prize_claimed = db_exec("SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ? AND day = 99",
                            (uid, now.month, p_type), fetch=True)

    if prize_claimed:
        return await cq.answer("✅ Вы уже забирали главный приз в этом месяце!", show_alert=True)

    # 2. Выдача приза
    if p_type == "normal":
        # Проверяем наличие титула (титулы не трейдятся, но защита от дублей не помешает)
        has_title = db_exec("SELECT 1 FROM titles_inv WHERE user_id = ? AND title_id = ?",
                            (uid, MAIN_PRIZE_NORMAL_TITLE), fetch=True)
        if not has_title:
            db_exec("INSERT INTO titles_inv (user_id, title_id) VALUES (?, ?)", (uid, MAIN_PRIZE_NORMAL_TITLE))

        # ЗАПИСЫВАЕМ ФАКТ ПОЛУЧЕНИЯ
        db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, 99, ?)",
                (uid, now.month, p_type))
        await cq.answer("✅ Получен главный приз: Титул!", show_alert=True)

    else:
        # ЗАПИСЫВАЕМ ФАКТ ПОЛУЧЕНИЯ ДО ВЫДАЧИ КАРТЫ
        db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, 99, ?)",
                (uid, now.month, p_type))

        # Выдаем карту (тут уже работает исправленная в db.py функция, которая чекает и сундук, если вдруг карта уже есть)
        is_new, krw_earn, c = give_card_to_user(uid, MAIN_PRIZE_ROYALE_CARD)

        if c and is_new:
            txt = (
                f"🃏 <b>Получена лимитированная карта!</b>\n\n"
                f"<b>🎴 Персонаж:</b> {c['name']}\n"
                f"<b>🔮 Редкость:</b> {c['rarity']}\n"
                f"<b>👊 Стиль боя:</b> {c['style']}\n"
                f"<b>🪐 Вселенная:</b> {c.get('series', 'Неизвестно')}\n\n"
                f"<b>⚡️ Скорость:</b> {c['speed']}\n"
                f"<b>💪 Сила:</b> {c['strength']}\n"
                f"<b>🧠 Интеллект:</b> {c['intellect']}"
            )

            try:
                if "Божественная" in c.get("rarity", "") and c.get("video"):
                    await cq.message.answer_video(
                        video=FSInputFile(f"images/cards/{c['video']}"),
                        caption=txt, parse_mode="HTML",
                        width=c.get("width", 960), height=c.get("height", 1280),
                        has_spoiler=True, supports_streaming=True
                    )
                else:
                    await cq.message.answer_photo(
                        photo=FSInputFile(f"images/cards/{c['file']}"),
                        caption=txt, parse_mode="HTML", has_spoiler=True
                    )
            except Exception:
                await cq.message.answer(txt, parse_mode="HTML")

            await cq.answer("✅ Получен эксклюзивный персонаж Рояль Пасса!", show_alert=True)
        else:
            await cq.answer(f"✅ Карта уже была у вас и конвертирована в {krw_earn} KRW!", show_alert=True)

@router.callback_query(F.data == "buy_royale_pass")
async def buy_rp(cq: CallbackQuery, bot: Bot):
    await bot.send_invoice(cq.from_user.id, title="🌠 Рояль Пасс",
                           description="Доступ к эксклюзивным наградам на этот месяц",
                           payload="rp_buy", provider_token="", currency="XTR",
                           prices=[LabeledPrice(label="Stars", amount=99)])
    await cq.answer()


# ============ БОЕВКА ============
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import asyncio

class BattleState(StatesGroup):
    waiting_for_friend_id = State()

def check_advantage(style1, style2):
    if style1 == style2: return 0
    if style1 == 'int' and style2 == 'str': return 1
    if style1 == 'str' and style2 == 'spd': return 1
    if style1 == 'spd' and style2 == 'int': return 1
    return -1

