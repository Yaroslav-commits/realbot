import os
import asyncio
import logging
import sqlite3
import random
import calendar
from datetime import datetime, timedelta

from aiogram import Bot, F, types
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           CallbackQuery, LabeledPrice, PreCheckoutQuery)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (BOT_TOKEN, ADMIN_IDS, DB_PATH,
                    GET_COOLDOWN_HOURS, BATTLE_COOLDOWN_HOURS,
                    MAIN_PRIZE_NORMAL_TITLE, MAIN_PRIZE_ROYALE_CARD)
from data.cards import (CARDS, RARITIES, BGS, VIDEO_BGS, TITLES,
                        NORMAL_PASS, ROYALE_PASS)
from database.db import (db_exec, init_db, get_user, add_user, get_rank,
                         pull_random_card, give_card_to_user)
from handlers import (router, TradeState, SettingsState, PromoState,
                      MATCH_QUEUE, GAMES, PENDING_TRADES, kb_main)


# ============ МАГАЗИН И ПАСС ============
# Картинка магазина
SHOP_IMG = "AgACAgIAAxkBAAIUM2nstgoTQDqJbUmaeCLLFoxMZnLiAAKJFmsbUZNhS6EpHcRVEBvhAQADAgADdwADOwQ"

# Картинки паков
PACK_LEG_IMG = "AgACAgIAAxkBAAIok2nuAAGHn07nXxOme7Ucn69VtOtlygACgRVrG8GDcEse656dcucp4QEAAwIAA3cAAzsE"
PACK_EPIC_IMG = "AgACAgIAAxkBAAIol2nuAAGQxDnghclp-deT9emYyhTV7wACghVrG8GDcEv9vCutdP8ehgEAAwIAA3cAAzsE"

# Картинка Евента
EVENT_IMG = "AgACAgIAAxkBAAIWi2ns3TnmIMe_lIjVKcVkgKF-LwiAAAKGF2sbUZNhS5heUt8GN34fAQADAgADeAADOwQ"

# Наборы алмазов: (звёзды, алмазы)
DIAMOND_PACKS = [
    (25, 75),
    (75, 250),
    (150, 600),
    (500, 2000),
    (1000, 5000),
]

# Крутки: (алмазы, попытки)
SPIN_PACKS = [
    (50, 10),
    (100, 22),
    (250, 60),
    (500, 140),
]

# Фоны в магазине: ключ, цена, валюта: bc = BattleCoin, krw = KRW, dia = Diamond
SHOP_BG_LIST = [
    {"id": "lookism_1", "price": 500,  "currency": "bc",  "icon": "🪙"},
    {"id": "adminn",      "price": 99999, "currency": "krw", "icon": "💴"},
    {"id": "zero",      "price": 2500, "currency": "krw", "icon": "💴"},
]

# ВИДЕО-ФОНЫ. Сюда кидаешь ключи тех фонов, которые у тебя загружены как видео.
VIDEO_BGS = {"zero"}, {"adminn"}

# ====== ЕВЕНТ ======
EVENT_ENABLED = False  # Поставь False, чтобы скрыть Евент и показывать "нет событий"
EVENT_CARDS = {
    1: {"key": "event_card_1", "price": 1000},  # Сон Джин Ву
    2: {"key": "event_card_2", "price": 1000},  # Богиня судьбы
    3: {"key": "event_card_3", "price": 1000},  # Санта Клаус
}
# Если ключа ещё нет в CARDS — возьмётся имя отсюда
EVENT_FALLBACK_NAMES = {
    1: "Сон Джин Ву",
    2: "Богиня судьбы",
    3: "Санта Клаус",
}

def _event_card_name(idx):
    info = EVENT_CARDS.get(idx)
    if info:
        c = CARDS.get(info["key"])
        if c:
            return c["name"]
    return EVENT_FALLBACK_NAMES.get(idx, f"Карта {idx}")

def _shop_main_kb():
    bld = InlineKeyboardBuilder()
    bld.button(text="Купить алмазы 💎", callback_data="shop:dia")
    bld.button(text="Premium 🎫",       callback_data="shop:premium")
    bld.button(text="Крутки 🎴",        callback_data="shop:spins")
    bld.button(text="Фоны 🌄",          callback_data="shop:bgs:0")
    bld.button(text="Паки 🗃️",          callback_data="shop:packs")
    bld.button(text="Евент 🤩",         callback_data="shop:event")
    bld.adjust(2)
    return bld.as_markup()

@router.message(F.text == "🛍 Магазин")
async def shop(msg: types.Message):
    await msg.answer_photo(
        photo=SHOP_IMG,
        caption="🛍 Добро пожаловать в Магазин!",
        reply_markup=_shop_main_kb()
    )

async def _back_to_shop_main(cq: CallbackQuery):
    try:
        await cq.message.edit_media(
            media=types.InputMediaPhoto(media=SHOP_IMG, caption="🛍 Добро пожаловать в Магазин!"),
            reply_markup=_shop_main_kb()
        )
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer_photo(
            photo=SHOP_IMG, caption="🛍 Добро пожаловать в Магазин!",
            reply_markup=_shop_main_kb()
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
@router.callback_query(F.data == "shop:premium")
async def shop_premium_cb(cq: CallbackQuery):
    await cq.answer("🎫 Premium пока недоступен.", show_alert=True)

# ===== Крутки =====
def _spin_kb():
    bld = InlineKeyboardBuilder()
    for dia, att in SPIN_PACKS:
        bld.button(text=f"{dia}💎 = {att}💳", callback_data=f"shop:spin_buy:{dia}:{att}")
    bld.button(text="Назад 🔙", callback_data="shop:main")
    bld.adjust(1)
    return bld.as_markup()

@router.callback_query(F.data == "shop:spins")
async def shop_spins_cb(cq: CallbackQuery):
    try:
        await cq.message.edit_reply_markup(reply_markup=_spin_kb())
    except Exception:
        pass
    await cq.answer()

@router.callback_query(F.data.startswith("shop:spin_buy:"))
async def shop_spin_buy_cb(cq: CallbackQuery):
    _, _, dia, att = cq.data.split(":")
    dia = int(dia); att = int(att)
    u = get_user(cq.from_user.id)
    if u[3] < dia:
        return await cq.answer(f"❌ Недостаточно алмазов! Нужно: {dia} 💎", show_alert=True)
    db_exec("UPDATE users SET diamond = diamond - ?, attempts = attempts + ? WHERE id = ?",
            (dia, att, cq.from_user.id))
    await cq.answer(f"✅ Куплено {att} попыток!", show_alert=True)

# ===== Фоны (с листалкой) =====
@router.callback_query(F.data.startswith("shop:bgs:"))
async def shop_bgs_cb(cq: CallbackQuery):
    idx = int(cq.data.split(":")[2]) % len(SHOP_BG_LIST)
    item = SHOP_BG_LIST[idx]
    bg = BGS.get(item["id"])
    if not bg:
        return await cq.answer("Фон не найден", show_alert=True)

    caption = f"🌄 Фон: {bg['name']}\n💰 Цена: {item['price']}{item['icon']}"

    left_idx  = (idx - 1) % len(SHOP_BG_LIST)
    right_idx = (idx + 1) % len(SHOP_BG_LIST)

    bld = InlineKeyboardBuilder()
    if idx == 0:
        bld.row(
            InlineKeyboardButton(text="🛍️ Купить", callback_data=f"shop:bg_buy:{item['id']}"),
            InlineKeyboardButton(text="——>",       callback_data=f"shop:bgs:{right_idx}")
        )
    elif idx == len(SHOP_BG_LIST) - 1:
        bld.row(
            InlineKeyboardButton(text="<——",       callback_data=f"shop:bgs:{left_idx}"),
            InlineKeyboardButton(text="🛍️ Купить", callback_data=f"shop:bg_buy:{item['id']}")
        )
    else:
        bld.row(
            InlineKeyboardButton(text="<——",       callback_data=f"shop:bgs:{left_idx}"),
            InlineKeyboardButton(text="🛍️ Купить", callback_data=f"shop:bg_buy:{item['id']}"),
            InlineKeyboardButton(text="——>",       callback_data=f"shop:bgs:{right_idx}")
        )
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="shop:main"))
    is_video = item["id"] in VIDEO_BGS
    try:
        if is_video:
            await cq.message.edit_media(
                media=types.InputMediaVideo(media=bg['file_id'], caption=caption),
                reply_markup=bld.as_markup()
            )
        else:
            await cq.message.edit_media(
                media=types.InputMediaPhoto(media=bg['file_id'], caption=caption),
                reply_markup=bld.as_markup()
            )
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        if is_video:
            await cq.message.answer_video(video=bg['file_id'], caption=caption, reply_markup=bld.as_markup())
        else:
            await cq.message.answer_photo(photo=bg['file_id'], caption=caption, reply_markup=bld.as_markup())
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
    bld.button(text="Легендарный пак 🔵", callback_data="shop:pack:leg")
    bld.button(text="Эпический пак 🟢",  callback_data="shop:pack:epic")
    bld.button(text="Назад 🔙",           callback_data="shop:main")
    bld.adjust(1)
    return bld.as_markup()

@router.callback_query(F.data == "shop:packs")
async def shop_packs_cb(cq: CallbackQuery):
    try:
        await cq.message.edit_reply_markup(reply_markup=_packs_kb())
    except Exception:
        pass
    await cq.answer()

@router.callback_query(F.data.startswith("shop:pack:"))
async def shop_pack_select_cb(cq: CallbackQuery):
    kind = cq.data.split(":")[2]
    is_leg = (kind == "leg")
    if is_leg:
        img = PACK_LEG_IMG
        caption = ("🗃️ Легендарный Пак\n\n"
                   "🔵 Легендарная: 100%\n\n"
                   "Стоимость: 450 💴")
        buy_btn = "🗃️ Купить легендарный пак"
    else:
        img = PACK_EPIC_IMG
        caption = ("🗃️ Эпический Пак\n\n"
                   "🟢 Эпическая: 100%\n\n"
                   "Стоимость: 150 💴")
        buy_btn = "🗃️ Купить эпический пак"

    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=buy_btn)],
        [KeyboardButton(text="🔙 Назад к пакам")]
    ], resize_keyboard=True)

    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.message.answer_photo(photo=img, caption=caption)
    await cq.message.answer("Выбери действие:", reply_markup=kb)
    await cq.answer()

@router.message(F.text.in_(["🗃️ Купить легендарный пак", "🗃️ Купить эпический пак"]))
async def buy_pack(msg: types.Message):
    is_leg = "легендарный" in msg.text.lower()
    cost = 450 if is_leg else 150
    rarity = "Легендарная 🔵" if is_leg else "Эпическая 🟢"

    u = get_user(msg.from_user.id)
    if u[4] < cost:
        return await msg.answer(f"❌ Недостаточно KRW. Нужно: {cost} 💴")
    db_exec("UPDATE users SET krw = krw - ? WHERE id = ?", (cost, msg.from_user.id))
    card_key = pull_random_card(force_rarity=rarity) or pull_random_card()
    is_new, krw_earn, c = give_card_to_user(msg.from_user.id, card_key)

    if is_new:
        txt = (f"🃏 Получена новая боевая карта!\n\n"
               f"🎴 Персонаж: {c['name']}\n"
               f"🔮 Редкость: {c['rarity']}\n"
               f"👊 Стиль боя: {c['style']}\n"
               f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
               f"⚡️ Скорость: {c['speed']}\n"
               f"💪 Сила: {c['strength']}\n"
               f"🧠 Интеллект: {c['intellect']}")
    else:
        txt = (f"🛑 Вам попалась повторная карта! Вы получаете {krw_earn} 💴 KRW\n\n"
               f"🎴 Персонаж: {c['name']}\n"
               f"🔮 Редкость: {c['rarity']}\n"
               f"👊 Стиль боя: {c['style']}\n"
               f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
               f"⚡️ Скорость: {c['speed']}\n"
               f"💪 Сила: {c['strength']}\n"
               f"🧠 Интеллект: {c['intellect']}")

    await msg.answer_photo(photo=c['file_id'], caption=txt, has_spoiler=True)

@router.message(F.text == "🔙 Назад к пакам")
async def back_to_packs(msg: types.Message):
    await msg.answer("Возвращаемся в магазин...", reply_markup=kb_main())
    await msg.answer_photo(
        photo=SHOP_IMG,
        caption="🛍 Магазин — 🗃️ Паки",
        reply_markup=_packs_kb()
    )

# ===== Евент =====
@router.callback_query(F.data == "shop:event")
async def shop_event_cb(cq: CallbackQuery):
    if not EVENT_ENABLED:
        return await cq.answer("В данный момент нету никаких событий", show_alert=True)

    n1, n2, n3 = _event_card_name(1), _event_card_name(2), _event_card_name(3)

    caption = (
        f"🌅 Карточки текущего События.\n\n"
        f"<blockquote>Карта 1: {n1}\n"
        f"Карта 2: {n2}\n"
        f"Карта 3: {n3}</blockquote>\n\n"
        f"Здесь можно приобрести новые карточки"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 Купить Карту 1", callback_data="shop:event_buy:1")
    bld.button(text="🎴 Купить Карту 2", callback_data="shop:event_buy:2")
    bld.button(text="🎴 Купить Карту 3", callback_data="shop:event_buy:3")
    bld.button(text="Назад 🔙",           callback_data="shop:main")
    bld.adjust(1)

    try:
        await cq.message.edit_media(
            media=types.InputMediaPhoto(media=EVENT_IMG, caption=caption, parse_mode="HTML"),
            reply_markup=bld.as_markup()
        )
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer_photo(
            photo=EVENT_IMG, caption=caption, reply_markup=bld.as_markup(), parse_mode="HTML"
        )
    await cq.answer()

@router.callback_query(F.data.startswith("shop:event_buy:"))
async def shop_event_buy_cb(cq: CallbackQuery):
    if not EVENT_ENABLED:
        return await cq.answer("В данный момент нету никаких событий", show_alert=True)

    idx = int(cq.data.split(":")[2])
    info = EVENT_CARDS.get(idx)
    if not info:
        return await cq.answer("Карта не найдена", show_alert=True)

    card_key = info["key"]
    price = info["price"]
    if card_key not in CARDS:
        return await cq.answer("❌ Карта ещё не добавлена в CARDS. Пропиши ключ в EVENT_CARDS.", show_alert=True)

    u = get_user(cq.from_user.id)
    if u[3] < price:
        return await cq.answer(f"❌ Недостаточно алмазов! Нужно: {price} 💎", show_alert=True)

    db_exec("UPDATE users SET diamond = diamond - ? WHERE id = ?", (price, cq.from_user.id))
    is_new, krw_earn, c = give_card_to_user(cq.from_user.id, card_key)
    if is_new:
        txt = (f"🃏 Получена новая боевая карта!\n\n"
               f"🎴 Персонаж: {c['name']}\n"
               f"🔮 Редкость: {c['rarity']}\n"
               f"👊 Стиль боя: {c['style']}\n"
               f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
               f"⚡️ Скорость: {c['speed']}\n"
               f"💪 Сила: {c['strength']}\n"
               f"🧠 Интеллект: {c['intellect']}")
    else:
        txt = (f"🛑 Вам попалась повторная карта! Вы получаете {krw_earn} 💴 KRW\n\n"
               f"🎴 Персонаж: {c['name']}\n"
               f"🔮 Редкость: {c['rarity']}\n"
               f"👊 Стиль боя: {c['style']}\n"
               f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
               f"⚡️ Скорость: {c['speed']}\n"
               f"💪 Сила: {c['strength']}\n"
               f"🧠 Интеллект: {c['intellect']}")

    await cq.message.answer_photo(photo=c['file_id'], caption=txt, has_spoiler=True)
    await cq.answer()

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
        db_exec("UPDATE users SET royale_pass = 1 WHERE id = ?", (msg.from_user.id,))
        await msg.answer("✅ Вы успешно приобрели Рояль Пасс!")
    else:
        await msg.answer("✅ Оплата принята!")


# ============ ПАСС ============
from datetime import datetime, timezone, timedelta
import calendar

# Жесткая привязка к МСК (UTC+3)
MSK = timezone(timedelta(hours=3))

PASS_NORMAL_IMG_1 = "AgACAgIAAxkBAAICN2npT2usTg9JKYcN77omcGfxSMy_AALrFWsbYxlIS8jrNH8Lp0d_AQADAgADdwADOwQ"
PASS_NORMAL_IMG_2 = "AgACAgIAAxkBAAICOWnpT3Nb7Od3EEVKv7rF-ubLjKd-AALsFWsbYxlIS4xOhVzQesKRAQADAgADdwADOwQ"
PASS_NORMAL_IMG_3 = "AgACAgIAAxkBAAICO2npT3qdFBDkzJEtJvpAv76tZfsPAALtFWsbYxlIS9SubA_87SHZAQADAgADdwADOwQ"
PASS_NORMAL_IMG_4 = "AgACAgIAAxkBAAICPWnpT4DmcHYmlKkeldmpIKAy4I9wAALuFWsbYxlIS5HkxNAVGOqGAQADAgADdwADOwQ"
PASS_NORMAL_IMG_5 = "AgACAgIAAxkBAAICP2npT4bQwb50eaG4jiP9vxak_cJyAALvFWsbYxlIS0_NW8CdRi_FAQADAgADdwADOwQ"

PASS_ROYALE_IMG_1 = "AgACAgIAAxkBAAIH5mnqYyG_EeenGODy4EZWyhhS0uv5AALzE2sbwYNYS_eXd7RzSjByAQADAgADdwADOwQ"
PASS_ROYALE_IMG_2 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"
PASS_ROYALE_IMG_3 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"
PASS_ROYALE_IMG_4 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"
PASS_ROYALE_IMG_5 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"


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
    _, p_type, page_str = cq.data.split(":")
    uid = cq.from_user.id
    u = get_user(uid)
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


async def render_pass_page(cq: CallbackQuery, p_type: str, page: int, u: tuple, now: datetime, days_in_month: int):
    uid = u[0]
    is_royale = (p_type == "royale")
    if is_royale and u[16] == 0:
        bld = InlineKeyboardBuilder()
        bld.button(text="Купить ⭐️", callback_data="buy_royale_pass")
        bld.button(text="Назад 🔙", callback_data="pass_back")
        bld.adjust(1)
        try:
            await cq.message.edit_media(
                media=types.InputMediaPhoto(media=PASS_ROYALE_IMG_1,
                                            caption="🌠 Рояль пасс\n\n⚠️ Данный пасс у вас ещё не приобретен."),
                reply_markup=bld.as_markup()
            )
        except:
            await cq.message.answer_photo(photo=PASS_ROYALE_IMG_1,
                                          caption="🌠 Рояль пасс\n\n⚠️ Данный пасс у вас ещё не приобретен.",
                                          reply_markup=bld.as_markup())
        return
    data = ROYALE_PASS if is_royale else NORMAL_PASS
    imgs_normal = [PASS_NORMAL_IMG_1, PASS_NORMAL_IMG_2, PASS_NORMAL_IMG_3, PASS_NORMAL_IMG_4, PASS_NORMAL_IMG_5]
    imgs_royale = [PASS_ROYALE_IMG_1, PASS_ROYALE_IMG_2, PASS_ROYALE_IMG_3, PASS_ROYALE_IMG_4, PASS_ROYALE_IMG_5]
    img = imgs_royale[page] if is_royale else imgs_normal[page]

    start_d = page * 6 + 1
    end_d = min(start_d + 5, days_in_month)
    if page == 4:
        end_d = days_in_month

    claims = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
                     (uid, now.month, p_type), fetchall=True)
    claimed_days = [d[0] for d in claims]

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
        rewards_txt += f"{d} день: {r_str}\n"

    txt = (f"{pass_name}\n\n"
           f"🟢 Заходи в пасс каждый день и получай награды, сегодня {now.day}-й день.\n\n"
           f"Награды на этой странице:\n{rewards_txt}\n"
           f"Обозначения:\n"
           f"❌ - День пропущен\n"
           f"✅ - Награда получена\n"
           f"🕓 - Ожидание награды\n\n"
           f"Получено дней - {len(claimed_days)}/{days_in_month}")

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
        await cq.message.edit_media(media=types.InputMediaPhoto(media=img, caption=txt), reply_markup=bld.as_markup())
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            try:
                await cq.message.delete()
                await cq.message.answer_photo(photo=img, caption=txt, reply_markup=bld.as_markup())
            except:
                pass


@router.callback_query(F.data.startswith("claim_pass:"))
async def claim_pass(cq: CallbackQuery):
    _, p_type, day_str, page_str = cq.data.split(":")
    day = int(day_str)
    page = int(page_str)
    uid = cq.from_user.id
    now = datetime.now(MSK)

    is_claimed = db_exec("SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = ?",
                         (uid, now.month, day, p_type), fetch=True)
    if is_claimed:
        return await cq.answer("Вы уже забрали эту награду! ✅", show_alert=True)

    if day > now.day:
        return await cq.answer("Этот день еще не наступил! 🕓", show_alert=True)
    if day < now.day:
        return await cq.answer("Этот день пропущен! ❌ Используйте «Купить дни 💎»", show_alert=True)

    data = ROYALE_PASS if p_type == "royale" else NORMAL_PASS
    r_type, r_val = data.get(day, ('krw', 10))

    if r_type == 'krw':
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (r_val, uid))
    elif r_type == 'atm':
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (r_val, uid))
    elif r_type == 'bc':
        db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (r_val, uid))
    elif r_type == 'dia':
        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (r_val, uid))
    elif r_type == 'pack':
        card_key = pull_random_card(force_rarity="Легендарная 🔵" if r_val == "leg" else "Эпическая 🟢")
        if not card_key: card_key = pull_random_card()
        give_card_to_user(uid, card_key)
        await cq.message.answer(f"🎁 Из пака выпала карта: {CARDS[card_key]['name']}!")

    db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
            (uid, now.month, day, p_type))

    icon = {'krw': '💴', 'atm': '💳', 'bc': '🪙', 'dia': '💎', 'pack': '🗃️'}.get(r_type, '')
    await cq.answer(f"✅ Вы забрали награду: {r_val} {icon}!", show_alert=True)

    u = get_user(uid)
    _, days_in_month = calendar.monthrange(now.year, now.month)
    await render_pass_page(cq, p_type, page, u, now, days_in_month)

    @router.callback_query(F.data.startswith("buy_days_menu:"))
    async def buy_days_menu(cq: CallbackQuery):
        _, p_type = cq.data.split(":")
        uid = cq.from_user.id
        now = datetime.now(MSK)

        claims = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
                         (uid, now.month, p_type), fetchall=True)
        claimed_days = [d[0] for d in claims]
        missed_days = [d for d in range(1, now.day) if d not in claimed_days]

        if not missed_days:
            return await cq.answer("У вас нет пропущенных дней! 🎉", show_alert=True)

        db_exec(
            "CREATE TABLE IF NOT EXISTS pass_bought_days (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
        bought_count = \
        db_exec("SELECT COUNT(*) FROM pass_bought_days WHERE user_id = ? AND month = ? AND pass_type = ?",
                (uid, now.month, p_type), fetch=True)[0]

        next_cost = (bought_count + 1) * 20

        txt = (f"💎 Восстановление пропущенных дней\n\n"
               f"Стоимость каждого дня увеличивается на 20:\n"
               f"1-й день — 20 💎\n"
               f"2-й день — 40 💎\n"
               f"3-й день — 60 💎\n"
               f"и т.д.\n\n"
               f"Текущая стоимость восстановления: {next_cost} 💎\n\n"
               f"Выберите, какие дни хотите купить:")

        bld = InlineKeyboardBuilder()
        day_buttons = []
        for d in missed_days:
            day_buttons.append(InlineKeyboardButton(text=f"❌ {d}", callback_data=f"buy_missed_day:{p_type}:{d}"))

        for i in range(0, len(day_buttons), 4):
            bld.row(*day_buttons[i:i + 4])
        bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data=f"pass:{p_type}:start"))

        try:
            await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup())
        except:
            pass
        await cq.answer()

    @router.callback_query(F.data.startswith("buy_missed_day:"))
    async def buy_missed_day(cq: CallbackQuery):
        _, p_type, day_str = cq.data.split(":")
        day = int(day_str)
        uid = cq.from_user.id
        now = datetime.now(MSK)
        is_claimed = db_exec("SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = ?",
                             (uid, now.month, day, p_type), fetch=True)
        if is_claimed:
            return await cq.answer("Этот день уже получен!", show_alert=True)

        db_exec(
            "CREATE TABLE IF NOT EXISTS pass_bought_days (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
        bought_count = \
        db_exec("SELECT COUNT(*) FROM pass_bought_days WHERE user_id = ? AND month = ? AND pass_type = ?",
                (uid, now.month, p_type), fetch=True)[0]
        cost = (bought_count + 1) * 20

        u = get_user(uid)
        if u[3] < cost:
            return await cq.answer(f"❌ Недостаточно алмазов! Нужно: {cost} 💎", show_alert=True)

        db_exec("UPDATE users SET diamond = diamond - ? WHERE id = ?", (cost, uid))
        db_exec("INSERT INTO pass_bought_days (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
                (uid, now.month, day, p_type))

        data = ROYALE_PASS if p_type == "royale" else NORMAL_PASS
        r_type, r_val = data.get(day, ('krw', 10))

        if r_type == 'krw':
            db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (r_val, uid))
        elif r_type == 'atm':
            db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (r_val, uid))
        elif r_type == 'bc':
            db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (r_val, uid))
        elif r_type == 'dia':
            db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (r_val, uid))
        elif r_type == 'pack':
            card_key = pull_random_card(force_rarity="Легендарная 🔵" if r_val == "leg" else "Эпическая 🟢")
            if not card_key: card_key = pull_random_card()
            give_card_to_user(uid, card_key)
            await cq.message.answer(f"🎁 Из пака выпала карта: {CARDS[card_key]['name']}!")

        db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
                (uid, now.month, day, p_type))

        await cq.answer(f"✅ День {day} восстановлен!", show_alert=True)
        await buy_days_menu(cq)

    @router.callback_query(F.data.startswith("pass_main_prize:"))
    async def pass_main(cq: CallbackQuery):
        p_type = cq.data.split(":")[1]
        uid = cq.from_user.id
        now = datetime.now(MSK)
        _, dim = calendar.monthrange(now.year, now.month)
        claims = db_exec("SELECT COUNT(*) FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
                         (uid, now.month, p_type), fetch=True)

        if claims[0] < dim:
            return await cq.answer("❌ Соберите награды за все дни месяца!", show_alert=True)

        if p_type == "normal":
            has_title = db_exec("SELECT 1 FROM titles_inv WHERE user_id = ? AND title_id = ?",
                                (uid, MAIN_PRIZE_NORMAL_TITLE), fetch=True)
            if has_title:
                return await cq.answer("✅ Главный приз уже в инвентаре!", show_alert=True)
            db_exec("INSERT INTO titles_inv (user_id, title_id) VALUES (?, ?)", (uid, MAIN_PRIZE_NORMAL_TITLE))
            await cq.answer("✅ Получен главный приз: Титул!", show_alert=True)
        else:
            has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?",
                               (uid, MAIN_PRIZE_ROYALE_CARD), fetch=True)
            if has_card:
                return await cq.answer("✅ Главный приз уже в инвентаре!", show_alert=True)
            give_card_to_user(uid, MAIN_PRIZE_ROYALE_CARD)
        await cq.answer("✅ Получен эксклюзивный персонаж Рояль Пасса!", show_alert=True)

    @router.callback_query(F.data == "buy_royale_pass")
    async def buy_rp(cq: CallbackQuery, bot: Bot):
        await bot.send_invoice(cq.from_user.id, title="🌠 Рояль Пасс",
                               description="Доступ к эксклюзивным наградам на этот месяц",
                               payload="rp_buy", provider_token="", currency="XTR",
                               prices=[LabeledPrice(label="Stars", amount=50)])
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

