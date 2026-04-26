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


# ================== HANDLERS ==================
@router.message(Command("start"))
async def start_cmd(msg: types.Message):
    add_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    await msg.answer("Добро пожаловать в Lookism Card! \nКанал бота: https://t.me/bradkofflood\nНаш чат:https://t.me/+as-Ypv7Kfjg3YTMy\n\nВыбирай действие и начни игру:", reply_markup=kb_main())

@router.message(F.video)
async def get_video_id(msg: types.Message):
    await msg.answer(f"ID видео:\n<code>{msg.video.file_id}</code>")

@router.message(F.photo)
async def get_photo_id(msg: types.Message):
    # Эта функция будет присылать тебе ID любой картинки, которую ты скинешь боту
    file_id = msg.photo[-1].file_id
    await msg.answer(f"Вот ID этой картинки:\n<code>{file_id}</code>")

@router.message(F.text == "⛩️ Банды")
async def gangs(msg: types.Message):
    await msg.answer("В разработке")

# ============ ГАЧА ============
@router.message(F.text == "🎴 Получить карту")
@router.message(Command("get"))
async def get_card_cmd(msg: types.Message):
    uid = msg.from_user.id
    u = get_user(uid)
    if not u: return

    attempts = u[6]
    last_get = datetime.strptime(u[11], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()

    if attempts > 0:
        db_exec("UPDATE users SET attempts = attempts - 1 WHERE id = ?", (uid,))
    else:
        if (now - last_get).total_seconds() < GET_COOLDOWN_HOURS * 3600:
            rem = int(GET_COOLDOWN_HOURS * 3600 - (now - last_get).total_seconds())
            await msg.answer(f"⏳ Следующая карта через {rem // 3600}ч {(rem % 3600) // 60}м.")
            return
        db_exec("UPDATE users SET last_get = ? WHERE id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), uid))

    card_key = pull_random_card()
    is_new, krw, c = give_card_to_user(uid, card_key)

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
        txt = (f"🛑 Вам попалась повторная карта! Вы получаете {krw} 💴 KRW\n\n"
               f"🎴 Персонаж: {c['name']}\n"
               f"🔮 Редкость: {c['rarity']}\n"
               f"👊 Стиль боя: {c['style']}\n"
               f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
               f"⚡️ Скорость: {c['speed']}\n"
               f"💪 Сила: {c['strength']}\n"
               f"🧠 Интеллект: {c['intellect']}")

    await msg.answer_photo(photo=c['file_id'], caption=txt, has_spoiler=True)


# ============ ПРОФИЛЬ ============
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

    txt = (
        f"👤 Профиль {u[2]} 🧩\n"
        f"🆔 Id: <code>{u[0]}</code>\n"
        f"{title_str}"
        f"Баланс:\n"
        f"• 💎 Diamond - {u[3]}\n"
        f"• 💴 KRW - {u[4]}\n"
        f"• 🪙 BattleCoin - {u[5]}\n\n"
        f"Попытки:\n"
        f"• 💳 - {u[6]}\n\n"
        f"Ранг: {get_rank(pts)} ({pts}🏅)\n"
        f"Победы/ничьи/поражения\n"
        f"{u[8]}/{u[9]}/{u[10]}"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="🔱 Мои титулы", callback_data="my_titles")
    bld.button(text="🌄 Мои фоны",   callback_data="my_bgs")
    bld.button(text="⚙️ Настройка",  callback_data="settings")
    bld.adjust(1)

    bg_key = u[13] or 'default'
    bg_data = BGS.get(bg_key, BGS['default'])
    bg_file = bg_data.get('file_id')
    try:
        if bg_key in VIDEO_BGS:
            await msg.answer_video(video=bg_file, caption=txt,
                                   reply_markup=bld.as_markup(), parse_mode="HTML")
        else:
            await msg.answer_photo(photo=bg_file, caption=txt,
                                   reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        await msg.answer(f"{txt}\n\n[Фон не загрузился.]",
                         reply_markup=bld.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "settings")
async def settings_cq(cq: CallbackQuery):
    u = get_user(cq.from_user.id)
    await cq.message.answer(
        f"⚙️ Настройки\nДата регистрации: {u[15]}\nДля смены ника отправьте команду /nick [новый ник]"
    )
    await cq.answer()


@router.message(Command("nick"))
async def change_nick(msg: types.Message):
    new_nick = msg.text.replace("/nick", "").strip()
    if not new_nick:
        return await msg.answer("Использование: /nick НовыйНик")
    db_exec("UPDATE users SET nickname = ? WHERE id = ?", (new_nick, msg.from_user.id))
    await msg.answer(f"✅ Ник изменен на {new_nick}")


@router.callback_query(F.data.in_(["my_bgs", "my_titles"]))
async def bgs_titles_cq(cq: CallbackQuery):
    is_bg = cq.data == "my_bgs"
    table = "bgs_inv" if is_bg else "titles_inv"
    col = "bg_id" if is_bg else "title_id"

    items = db_exec(f"SELECT {col} FROM {table} WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    item_ids = [itm[0] for itm in items]

    # Добавляем стандартный фон в список фонов, чтобы его всегда можно было вернуть
    if is_bg and "default" not in item_ids:
        item_ids.insert(0, "default")

    if not item_ids:
        await cq.answer("У вас пока ничего нет!", show_alert=True)
        return

    bld = InlineKeyboardBuilder()
    for itm in item_ids:
        if is_bg:
            name = BGS.get(itm, {}).get('name', 'Неизвестный фон')
        else:
            name = TITLES.get(itm, 'Неизвестный титул')
        bld.button(text=name, callback_data=f"preview_{'bg' if is_bg else 'title'}:{itm}")

    bld.adjust(1)
    text_msg = "Выберите фон для просмотра:" if is_bg else "Выберите титул для просмотра:"
    await cq.message.answer(text_msg, reply_markup=bld.as_markup())
    await cq.answer()


# ============ Предпросмотр фона/титула ============
@router.callback_query(F.data.startswith("preview_"))
async def preview_cq(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 2:
        return
    type_str, itm = parts[0].replace("preview_", ""), parts[1]

    u = get_user(cq.from_user.id)
    current_active = u[13] if type_str == "bg" else u[14]

    is_active = (current_active == itm)
    if type_str == "bg" and itm == "default" and current_active in [None, 'default']:
        is_active = True

    btn_text = "✅ Установлено" if is_active else "☑️ Установить"
    bld = InlineKeyboardBuilder()
    bld.button(text=btn_text, callback_data=f"equip_{type_str}:{itm}")

    if type_str == "bg":
        bg_data = BGS.get(itm, BGS['default'])
        file_id = bg_data.get('file_id')
        name = bg_data.get('name', 'Фон')
        caption = f"🌄 Предпросмотр фона: {name}"
        if itm in VIDEO_BGS:
            await cq.message.answer_video(video=file_id, caption=caption, reply_markup=bld.as_markup())
        else:
            await cq.message.answer_photo(photo=file_id, caption=caption, reply_markup=bld.as_markup())
    else:
        name = TITLES.get(itm, 'Титул')
        await cq.message.answer(f"🔱 Предпросмотр титула: {name}", reply_markup=bld.as_markup())

    await cq.answer()


@router.callback_query(F.data.startswith("equip_"))
async def equip_cq(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 2: return
    type_str, itm = parts[0].replace("equip_", ""), parts[1]

    u = get_user(cq.from_user.id)
    col = "active_bg" if type_str == "bg" else "active_title"
    current_active = u[13] if type_str == "bg" else u[14]

    is_active = (current_active == itm)
    if type_str == "bg" and itm == "default" and current_active in [None, 'default']:
        is_active = True

    if is_active:
        # Если уже установлено, снимаем предмет
        new_val = 'default' if type_str == "bg" else None
        db_exec(f"UPDATE users SET {col} = ? WHERE id = ?", (new_val, cq.from_user.id))
        btn_text = "☑️ Установить"
        alert_text = "Убрано из профиля!"
    else:
        # Устанавливаем новый предмет
        new_val = itm
        db_exec(f"UPDATE users SET {col} = ? WHERE id = ?", (new_val, cq.from_user.id))
        btn_text = "✅ Установлено"
        alert_text = "Успешно установлено!"

        # Меняем кнопку на лету
    bld = InlineKeyboardBuilder()
    bld.button(text=btn_text, callback_data=f"equip_{type_str}:{itm}")

    try:
        await cq.message.edit_reply_markup(reply_markup=bld.as_markup())
    except:
        pass  # Игнорируем ошибку, если статус кнопки не изменился

    await cq.answer(alert_text)



# ============ АДМИН И ПРОМО ============
@router.message(
    Command(commands=["give_attempts", "give_card", "give_money", "give_title", "give_background", "create_promo"]))
async def admin_cmds(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS: return
    args = msg.text.split()
    cmd = args[0]

    if cmd == "/create_promo":
        await state.set_state(PromoState.waiting_for_promo_data)
        await msg.answer(
            "Отправь данные промокода в формате:\n[КОД] [ТИП: krw/atm/card] [ЗНАЧЕНИЕ] [КОЛ-ВО ИСПОЛЬЗОВАНИЙ]\nПример: LOOKISM krw 500 10")
        return

    if len(args) < 3: return await msg.answer("Ошибка аргументов.")
    uid, val = int(args[1]), args[2]

    if cmd == "/give_attempts":
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (int(val), uid))
    elif cmd == "/give_money":
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (int(val), uid))
    elif cmd == "/give_card":
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (uid, val))
    elif cmd == "/give_title":
        db_exec("INSERT INTO titles_inv (user_id, title_id) VALUES (?, ?)", (uid, val))
    elif cmd == "/give_background":
        db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, val))
    await msg.answer(f"✅ Выдано пользователю {uid}!")


@router.message(PromoState.waiting_for_promo_data)
async def create_promo(msg: types.Message, state: FSMContext):
    args = msg.text.split()
    if len(args) != 4: return await msg.answer("Неверный формат.")
    db_exec("INSERT INTO promos (code, p_type, val, uses) VALUES (?, ?, ?, ?)",
            (args[0], args[1], args[2], int(args[3])))
    await state.clear()
    await msg.answer(f"✅ Промокод {args[0]} создан!")


@router.message(Command("promo"))
async def use_promo(msg: types.Message):
    args = msg.text.split()
    if len(args) < 2: return await msg.answer("Введи промокод: /promo КОД")
    code = args[1]

    p = db_exec("SELECT p_type, val, uses FROM promos WHERE code = ?", (code,), fetch=True)
    if not p or p[2] <= 0: return await msg.answer("Промокод недействителен.")

    db_exec("UPDATE promos SET uses = uses - 1 WHERE code = ?", (code,))

    if p[0] == 'krw':
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (int(p[1]), msg.from_user.id))
    elif p[0] == 'atm':
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (int(p[1]), msg.from_user.id))
    elif p[0] == 'card':
        give_card_to_user(msg.from_user.id, p[1])
    await msg.answer("✅ Промокод активирован!")
