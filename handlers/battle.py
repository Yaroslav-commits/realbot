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
                           CallbackQuery, LabeledPrice, PreCheckoutQuery, FSInputFile, Message)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (BOT_TOKEN, ADMIN_IDS, DB_PATH,
                    GET_COOLDOWN_HOURS, BATTLE_COOLDOWN_HOURS,
                    MAIN_PRIZE_NORMAL_TITLE, MAIN_PRIZE_ROYALE_CARD)
from data.cards import (CARDS, RARITIES, BGS, VIDEO_BGS, TITLES,
                        NORMAL_PASS, ROYALE_PASS, is_divine)
from database.db import (db_exec, init_db, get_user, add_user, get_rank,
                         pull_random_card, give_card_to_user, is_premium)
from handlers import (router, TradeState, SettingsState, PromoState,
                      MATCH_QUEUE, GAMES, PENDING_TRADES, kb_main)
from media_cache import send_cached_video
import handlers as _handlers

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

@router.message(F.text == "⚔️ Поле битвы")
async def battle_menu(msg: types.Message):
    u = get_user(msg.from_user.id)
    txt = (f"⚔️ BATTLE FIELD ACCESS\n\n"
           f"Добро пожаловать на поле битвы, Игрок.\n\n"
           f"Вы входите в зону PvP-испытаний. Здесь формируется сила через сражения, а каждый бой влияет на ваш ранг 📊\n\n"
           f"<blockquote>🔓 Условия доступа к «Битвам ⚔️»:\n"
           f"→ Необходимо собрать 10 боевых карт 🃏</blockquote>\n\n"
           f"▶️ РЕЖИМ: АКТИВЕН\n"
           f"▶️ СТАТУС: БОЕВАЯ СИСТЕМА ОНЛАЙН И ОФЛАЙН\n\n"
           f"━━━━━━━━━━━━━━━\n"
           f'🏅 {u[7]} Очков | Ранг {get_rank(u[7])}\n'
           f"Победа / Ничья / Поражение :\n"
           f"{u[8]} / {u[9]} / {u[10]}\n"
           f"━━━━━━━━━━━━━━━\n\n"
           f"Каждое сражение фиксируется в хронике данных.")

    bld = InlineKeyboardBuilder()
    bld.button(text="Найти противника 👁️", callback_data="find_match")
    bld.button(text="Дружеский бой 🔪", callback_data="friendly_match_start")
    bld.button(text="Моя колода 🗂️", callback_data="my_deck")
    bld.button(text="🛒 BattleShop", callback_data="b_shop_main")
    bld.button(text="🔝 ТОП И РАНГИ", callback_data="b_top_ranks")
    bld.adjust(1, 2, 1, 1)

    if os.path.exists("images/shop/battle.jpeg"):
        await msg.answer_photo(photo=FSInputFile("images/shop/battle.jpeg"), caption=txt, reply_markup=bld.as_markup())
    else:
        await msg.answer(txt, reply_markup=bld.as_markup())

@router.message(Command("pause"))
async def pause_cmd(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    _handlers.BATTLE_PAUSED = not _handlers.BATTLE_PAUSED
    state_text = "приостановлен ⏸️" if _handlers.BATTLE_PAUSED else "возобновлён ▶️"
    await msg.answer(f"⚙️ Поиск боёв {state_text}.")

@router.callback_query(F.data == "friendly_match_start")
async def friendly_match_start(cq: CallbackQuery, state: FSMContext):
    bld = InlineKeyboardBuilder()
    bld.button(text="Отменить", callback_data="cancel_friendly")
    await cq.message.answer("Отправьте ID игрока с которым хотите сыграть", reply_markup=bld.as_markup())
    await state.set_state(BattleState.waiting_for_friend_id)
    await cq.answer()

@router.callback_query(F.data == "cancel_friendly")
async def cancel_friendly(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.delete()
    await cq.message.answer("Запрос отменен.")

@router.message(BattleState.waiting_for_friend_id)
async def process_friend_id(msg: types.Message, state: FSMContext):
    try:
        target_id = int(msg.text)
    except ValueError:
        return await msg.answer("Пожалуйста, отправьте корректный ID (число).")

    if target_id == msg.from_user.id:
        return await msg.answer("Нельзя сыграть с самим собой!")

    target_user = get_user(target_id)
    if not target_user:
        return await msg.answer("Игрок с таким ID не найден.")

    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ?", (msg.from_user.id,), fetchall=True)
    if len(deck) != 6:
        await state.clear()
        return await msg.answer("Сначала соберите колоду из 6 карт!")
    u = get_user(msg.from_user.id)
    last_b = datetime.strptime(u[12], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()

    cd_hours = 0.5 if is_premium(msg.from_user.id) else BATTLE_COOLDOWN_HOURS

    if (now - last_b).total_seconds() < cd_hours * 3600:
        rem = int(cd_hours * 3600 - (now - last_b).total_seconds())
        await state.clear()
        return await msg.answer(f"⏳ Кулдаун битвы: {rem // 3600}ч {(rem % 3600) // 60}м")
    my_name = u[2]
    await state.clear()

    bld = InlineKeyboardBuilder()
    bld.button(text="Согласиться", callback_data=f"accept_f:{msg.from_user.id}")
    bld.button(text="Отказаться", callback_data=f"decline_f:{msg.from_user.id}")
    bld.adjust(2)

    try:
        await msg.bot.send_message(target_id, f"{my_name} вызывает тебя на дружеский бой", reply_markup=bld.as_markup())
        await msg.answer("Запрос отправлен")
    except Exception:
        await msg.answer("Не удалось отправить запрос. Возможно, игрок заблокировал бота.")


@router.callback_query(F.data.startswith("decline_f:"))
async def decline_f(cq: CallbackQuery):
    _, sender_id = cq.data.split(":")
    await cq.message.delete()
    try:
        await cq.bot.send_message(int(sender_id), "Произошел отказ от дружеского боя.")
    except:
        pass
    await cq.answer()


@router.callback_query(F.data.startswith("accept_f:"))
async def accept_f(cq: CallbackQuery):
    _, sender_id = cq.data.split(":")
    sender_id = int(sender_id)
    target_id = cq.from_user.id

    await cq.message.delete()

    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ?", (target_id,), fetchall=True)
    if len(deck) != 6:
        await cq.answer("У вас не собрана колода!", show_alert=True)
        try:
            await cq.bot.send_message(sender_id, "Игрок не может принять бой (не собрана колода).")
        except:
            pass
        return

    u = get_user(target_id)
    last_b = datetime.strptime(u[12], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    cd_hours_target = 0.5 if is_premium(target_id) else BATTLE_COOLDOWN_HOURS
    if (now - last_b).total_seconds() < cd_hours_target * 3600:
        rem = int(cd_hours_target * 3600 - (now - last_b).total_seconds())
        await cq.answer(f"У вас кулдаун битвы: {rem // 3600}ч {(rem % 3600) // 60}м", show_alert=True)
        try:
            await cq.bot.send_message(sender_id, "У игрока кулдаун битвы. Он не может принять бой.")
        except:
            pass
        return

    u_sender = get_user(sender_id)
    last_b_s = datetime.strptime(u_sender[12], "%Y-%m-%d %H:%M:%S")
    cd_hours_sender = 0.5 if is_premium(sender_id) else BATTLE_COOLDOWN_HOURS
    if (now - last_b_s).total_seconds() < cd_hours_sender * 3600:
        await cq.answer("У инициатора боя сейчас кулдаун.", show_alert=True)
        try:
            await cq.bot.send_message(sender_id, "Ваш кулдаун не позволяет начать бой.")
        except:
            pass
        return


    await start_battle(sender_id, target_id, cq.bot, friendly=True)


@router.callback_query(F.data == "my_deck")
async def my_deck_menu(cq: CallbackQuery):
    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if len(cards) < 10:
        return await cq.answer("❌ Нужно получить минимум 10 боевых карт, чтобы открыть этот раздел!", show_alert=True)

    bld = InlineKeyboardBuilder()
    bld.button(text="Посмотреть колоду 🃏", callback_data="view_deck")
    bld.button(text="Автосбор 🔁", callback_data="auto_deck")
    bld.button(text="Собрать колоду 🆕", callback_data="manual_deck_start")
    bld.adjust(1)

    text = "🗂 Меню колоды:\nВыберите действие:"
    # сообщение из «⚔️ Поле битвы» приходит как фото — edit_text на фото падает,
    # из-за этого кнопка «Моя колода 🗂️» казалась неактивной. Делаем безопасно.
    try:
        await cq.message.edit_text(text, reply_markup=bld.as_markup())
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(text, reply_markup=bld.as_markup())
    await cq.answer()


@router.callback_query(F.data == "view_deck")
async def view_deck(cq: CallbackQuery):
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ? ORDER BY slot_index", (cq.from_user.id,), fetchall=True)
    if len(deck) != 6:
        return await cq.answer("Колода не собрана полностью!", show_alert=True)
    rarity_order = {"Божественная ⚫️": 6, "Мифическая 🔴": 5, "Легендарная 🔵": 4, "Эпическая 🟢": 3, "Редкая 🟡": 2,
                    "Обычная ⚪️": 1}
    c_objs = [(cid, CARDS[cid]) for (cid,) in deck]
    c_objs.sort(key=lambda x: rarity_order.get(x[1]['rarity'], 0), reverse=True)
    media = []
    for i, (cid, c) in enumerate(c_objs):
        txt_card = f"{i + 1}. {c['name']} ({c['rarity']})\n⚡️{c['speed']} | 💪{c['strength']} | 🧠{c['intellect']}"
        media.append(types.InputMediaPhoto(media=FSInputFile(f"images/cards/{c['file']}"), caption=txt_card))

    await cq.message.answer_media_group(media=media)

@router.callback_query(F.data == "auto_deck")
async def auto_deck(cq: CallbackQuery):
    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if len(cards) < 6:
        return await cq.answer("Для колоды нужно минимум 6 карт!", show_alert=True)

    c_objs = []
    for (cid,) in cards:
        c = CARDS.get(cid)
        if not c:
            continue
        c_objs.append({'id': cid, 't': c['speed'] + c['strength'] + c['intellect'], 'r': c['rarity']})
    c_objs.sort(key=lambda x: x['t'], reverse=True)

    new_deck = []
    mythic_divine, leg = 0, 0
    for c in c_objs:
        if len(new_deck) == 6: break
        if "Мифическая" in c['r'] or "Божественная" in c['r']:
            if mythic_divine >= 1: continue
            mythic_divine += 1
        elif "Легендарная" in c['r']:
            if leg >= 2: continue
            leg += 1
        new_deck.append(c['id'])

    if len(new_deck) < 6:
        return await cq.answer("Не удалось собрать 6 карт из-за ограничений редкости.", show_alert=True)

    db_exec("DELETE FROM decks WHERE user_id = ?", (cq.from_user.id,))
    for i, cid in enumerate(new_deck):
        db_exec("INSERT INTO decks (user_id, card_id, slot_index) VALUES (?, ?, ?)", (cq.from_user.id, cid, i))
    await cq.answer("✅ Колода автоматически собрана лучшими картами!", show_alert=True)


# ============ СИСТЕМА КОЛОД (НОВАЯ) ============

class MultiDeckState(StatesGroup):
    waiting_for_deck_name = State()
    waiting_for_deck_rename = State()


def ensure_multi_deck_tables():
    db_exec('''CREATE TABLE IF NOT EXISTS multi_decks (
        deck_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT
    )''')
    db_exec('''CREATE TABLE IF NOT EXISTS multi_deck_slots (
        deck_id INTEGER,
        slot_index INTEGER,
        card_id TEXT
    )''')


def sync_active_deck(user_id, deck_id):
    # Синхронизируем собранную колоду с основной таблицей decks для совместимости с боями
    db_exec("DELETE FROM decks WHERE user_id = ?", (user_id,))
    slots = db_exec("SELECT slot_index, card_id FROM multi_deck_slots WHERE deck_id = ?", (deck_id,), fetchall=True)
    for slot_index, card_id in slots:
        db_exec("INSERT INTO decks (user_id, card_id, slot_index) VALUES (?, ?, ?)", (user_id, card_id, slot_index - 1))


async def show_multi_deck_main(message, user_id):
    ensure_multi_deck_tables()
    decks = db_exec("SELECT deck_id, name FROM multi_decks WHERE user_id = ?", (user_id,), fetchall=True)

    bld = InlineKeyboardBuilder()
    if len(decks) == 0:
        bld.button(text="Добавить колоду 🆕", callback_data="mdeck_add")
        bld.button(text="Назад 🔙", callback_data="view_deck")  # Возврат в меню колоды
    elif len(decks) == 1:
        bld.button(text=decks[0][1], callback_data=f"mdeck_view:{decks[0][0]}")
        bld.button(text="Добавить колоду 🆕", callback_data="mdeck_add")
        bld.button(text="Назад 🔙", callback_data="view_deck")
    else:
        for d in decks:
            bld.button(text=d[1], callback_data=f"mdeck_view:{d[0]}")
        bld.button(text="Назад 🔙", callback_data="view_deck")

    bld.adjust(1)

    text = (
        "Здесь место для вашых колод 🎴\n\n"
        "Можно иметь лишь две колоды. Нажмите на кнопку «Собрать колоду», для сбора своей боевой колоды."
    )
    if isinstance(message, types.Message):
        await message.answer(text, reply_markup=bld.as_markup())
    else:
        await message.edit_text(text, reply_markup=bld.as_markup())


@router.callback_query(F.data == "manual_deck_start")
async def manual_deck_start(cq: CallbackQuery):
    await show_multi_deck_main(cq.message, cq.from_user.id)


@router.callback_query(F.data == "mdeck_add")
async def mdeck_add_cb(cq: CallbackQuery, state: FSMContext):
    decks = db_exec("SELECT deck_id FROM multi_decks WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if len(decks) >= 2:
        return await cq.answer("Максимум 2 колоды!", show_alert=True)

    bld = InlineKeyboardBuilder()
    bld.button(text="Отменить", callback_data="manual_deck_start")
    await cq.message.edit_text("🗞️ Введите название для колоды, максимум 10 букв..", reply_markup=bld.as_markup())
    await state.set_state(MultiDeckState.waiting_for_deck_name)


@router.message(MultiDeckState.waiting_for_deck_name)
async def mdeck_name_entered(msg: types.Message, state: FSMContext):
    name = msg.text.strip()
    if len(name) > 10:
        return await msg.answer("Максимум 10 букв! Попробуйте еще раз.")

    db_exec("INSERT INTO multi_decks (user_id, name) VALUES (?, ?)", (msg.from_user.id, name))
    await state.clear()
    await show_multi_deck_main(msg, msg.from_user.id)


@router.callback_query(F.data.startswith("mdeck_view:"))
async def mdeck_view_cb(cq: CallbackQuery):
    deck_id = int(cq.data.split(":")[1])
    deck = db_exec("SELECT name FROM multi_decks WHERE deck_id = ? AND user_id = ?", (deck_id, cq.from_user.id),
                   fetch=True)
    if not deck: return await cq.answer("Колода не найдена!", show_alert=True)
    deck_name = deck[0]

    slots = db_exec("SELECT slot_index, card_id FROM multi_deck_slots WHERE deck_id = ?", (deck_id,), fetchall=True)
    cards_text = ""
    count = 0
    for s in slots:
        cid = s[1]
        c = CARDS.get(cid)
        if c:
            count += 1
            emoji = c['rarity'].split()[-1] if len(c['rarity'].split()) > 1 else ""
            cards_text += f"«{c['name']}» {emoji} - 1 | Рейтинги - {c['speed']}, {c['strength']}, {c['intellect']}\n"

    if not cards_text:
        cards_text = "Пусто\n"

    text = (f"🃏 Колода - «{deck_name}»\n\n"
            f"Количество карт - {count} ✅\n\n"
            f"Карты и редкости:\n{cards_text}\n"
            "Добавьте карты в колоду")

    bld = InlineKeyboardBuilder()
    bld.button(text="Переименовать колоду 📝", callback_data=f"mdeck_rename:{deck_id}")
    bld.button(text="Ручная сборка 🔃", callback_data=f"mdeck_edit:{deck_id}")
    bld.button(text="Удалить колоду ♻️", callback_data=f"mdeck_del:{deck_id}")
    bld.button(text="Назад 🔙", callback_data="manual_deck_start")
    bld.adjust(1)

    await cq.message.edit_text(text, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("mdeck_rename:"))
async def mdeck_rename_cb(cq: CallbackQuery, state: FSMContext):
    deck_id = int(cq.data.split(":")[1])
    await state.update_data(rename_deck_id=deck_id)
    bld = InlineKeyboardBuilder()
    bld.button(text="Отменить", callback_data=f"mdeck_view:{deck_id}")
    await cq.message.edit_text("🗞️ Введите новое название для колоды, максимум 10 букв..", reply_markup=bld.as_markup())
    await state.set_state(MultiDeckState.waiting_for_deck_rename)


@router.message(MultiDeckState.waiting_for_deck_rename)
async def mdeck_renamed(msg: types.Message, state: FSMContext):
    name = msg.text.strip()
    if len(name) > 10:
        return await msg.answer("Максимум 10 букв! Попробуйте еще раз.")

    data = await state.get_data()
    deck_id = data.get('rename_deck_id')
    db_exec("UPDATE multi_decks SET name = ? WHERE deck_id = ? AND user_id = ?", (name, deck_id, msg.from_user.id))
    await state.clear()

    await show_multi_deck_main(msg, msg.from_user.id)


@router.callback_query(F.data.startswith("mdeck_del:"))
async def mdeck_del_cb(cq: CallbackQuery):
    deck_id = int(cq.data.split(":")[1])
    db_exec("DELETE FROM multi_decks WHERE deck_id = ? AND user_id = ?", (deck_id, cq.from_user.id))
    db_exec("DELETE FROM multi_deck_slots WHERE deck_id = ?", (deck_id,))
    await cq.answer("Колода удалена!")
    await show_multi_deck_main(cq.message, cq.from_user.id)
@router.callback_query(F.data.startswith("mdeck_edit:"))
async def mdeck_edit_cb(cq: CallbackQuery):
    deck_id = int(cq.data.split(":")[1])
    await show_mdeck_slots(cq, deck_id)


async def show_mdeck_slots(cq: CallbackQuery, deck_id: int):
    deck = db_exec("SELECT name FROM multi_decks WHERE deck_id = ? AND user_id = ?", (deck_id, cq.from_user.id),
                   fetch=True)
    if not deck: return
    deck_name = deck[0]

    # Делаем эту колоду активной
    sync_active_deck(cq.from_user.id, deck_id)

    slots = db_exec("SELECT slot_index, card_id FROM multi_deck_slots WHERE deck_id = ?", (deck_id,), fetchall=True)
    slot_dict = {s[0]: s[1] for s in slots}

    text_lines = [f"🃏 Колода: «{deck_name}»", "Нажимайте на ячейки снизу, чтобы выбрать карту:\n"]

    bld = InlineKeyboardBuilder()
    row_btns = []

    for i in range(1, 7):
        cid = slot_dict.get(i)
        if cid and cid in CARDS:
            c = CARDS[cid]
            cname = f"«{c['name']}»"
            spd, str_, int_ = c['speed'], c['strength'], c['intellect']
            btn_text = f"✅"
        else:
            cname = "Пусто"
            spd, str_, int_ = 0, 0, 0
            btn_text = f"❌"

        prefix = "┌" if i == 1 else ("└" if i == 6 else "├")
        if i == 6:
            text_lines.append(f"{prefix} {cname}")
            text_lines.append(f"    ⚡️ {spd} │ 💪 {str_} │ 🧠 {int_} ")
        else:
            text_lines.append(f"{prefix} {cname}")
            text_lines.append(f"│ ⚡️ {spd} │ 💪 {str_} │ 🧠 {int_} ")

        row_btns.append(InlineKeyboardButton(text=btn_text, callback_data=f"mdeck_slot:{deck_id}:{i}"))

    bld.row(*row_btns)
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data=f"mdeck_view:{deck_id}"))

    text = "\n".join(text_lines)
    if isinstance(cq, types.Message):
        await cq.answer(text, reply_markup=bld.as_markup())
    else:
        await cq.message.edit_text(text, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("mdeck_slot:"))
async def mdeck_slot_cb(cq: CallbackQuery):
    parts = cq.data.split(":")
    deck_id, slot_index = int(parts[1]), int(parts[2])

    text = (
        "📜 Правила формирования колоды:\n\n"
        "В колоде допускается максимум 6 карт. При этом действуют следующие ограничения:\n"
        "🎴 1 Божественная или Мифическая карта\n"
        "🎴 2 Легендарные карты\n"
        "🎴 Без ограничений остальные редкости, можно иметь в колоде до 6 эпических карт\n\n"
        "➡️ Выберите редкость для вывода списка карт"
    )

    bld = InlineKeyboardBuilder()
    inv_cids = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    user_rarities = set()
    for (cid,) in inv_cids:
        if cid in CARDS:
            user_rarities.add(CARDS[cid]['rarity'])

    r_key_to_emoji = {
        "divine": "Божественная ⚫️", "mythic": "Мифическая 🔴",
        "legendary": "Легендарная 🔵", "epic": "Эпическая 🟢",
        "rare": "Редкая 🟡", "common": "Обычная ⚪️"
    }
    order = ["divine", "mythic", "legendary", "epic", "rare", "common"]

    for rk in order:
        if r_key_to_emoji[rk] in user_rarities:
            bld.button(text=r_key_to_emoji[rk], callback_data=f"mdeck_rarity:{deck_id}:{slot_index}:{rk}:0")

    bld.button(text="Назад 🔙", callback_data=f"mdeck_edit:{deck_id}")
    bld.adjust(1)
    await cq.message.edit_text(text, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("mdeck_rarity:"))
async def mdeck_rarity_cb(cq: CallbackQuery):
    parts = cq.data.split(":")
    deck_id, slot_index, r_key, page = int(parts[1]), int(parts[2]), parts[3], int(parts[4])

    r_key_to_emoji = {
        "divine": "Божественная ⚫️", "mythic": "Мифическая 🔴",
        "legendary": "Легендарная 🔵", "epic": "Эпическая 🟢",
        "rare": "Редкая 🟡", "common": "Обычная ⚪️"
    }
    rarity = r_key_to_emoji.get(r_key)

    inv_cids = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    owned_counts = {}
    for (cid,) in inv_cids:
        owned_counts[cid] = owned_counts.get(cid, 0) + 1

    slots = db_exec("SELECT card_id FROM multi_deck_slots WHERE deck_id = ? AND slot_index != ?", (deck_id, slot_index),
                    fetchall=True)
    current_deck_cids = [s[0] for s in slots]

    mythic_divine_cnt = sum(1 for cid in current_deck_cids if cid in CARDS and (
                "Мифическая" in CARDS[cid]['rarity'] or "Божественная" in CARDS[cid]['rarity']))
    leg_cnt = sum(1 for cid in current_deck_cids if cid in CARDS and "Легендарная" in CARDS[cid]['rarity'])

    if r_key in ["divine", "mythic"] and mythic_divine_cnt >= 1:
        return await cq.answer("Максимум 1 Божественная или Мифическая карта!", show_alert=True)
    if r_key == "legendary" and leg_cnt >= 2:
        return await cq.answer("Максимум 2 Легендарные карты!", show_alert=True)

    avail = []
    for cid, count in owned_counts.items():
        if cid in CARDS and CARDS[cid]['rarity'] == rarity:
            if current_deck_cids.count(cid) < count:
                avail.append(cid)

    if not avail:
        return await cq.answer("Нет доступных карт этой редкости для добавления!", show_alert=True)

    items_per_page = 10
    total_pages = (len(avail) + items_per_page - 1) // items_per_page
    if page >= total_pages: page = max(0, total_pages - 1)

    start_idx = page * items_per_page
    page_cids = avail[start_idx:start_idx + items_per_page]

    bld = InlineKeyboardBuilder()
    for cid in page_cids:
        c = CARDS[cid]
        btn_text = f"«{c['name']}» {c['speed']} | {c['strength']} | {c['intellect']}"
        bld.button(text=btn_text, callback_data=f"mdeck_set:{deck_id}:{slot_index}:{cid}")

    bld.adjust(1)

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"mdeck_rarity:{deck_id}:{slot_index}:{r_key}:{page - 1}"))
    else:
        nav_row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))

    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))

    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(text="➡️", callback_data=f"mdeck_rarity:{deck_id}:{slot_index}:{r_key}:{page + 1}"))
    else:
        nav_row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))

    bld.row(*nav_row)
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data=f"mdeck_slot:{deck_id}:{slot_index}"))

    await cq.message.edit_text(f"Выберите карту ({rarity}):", reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("mdeck_set:"))
async def mdeck_set_cb(cq: CallbackQuery):
    parts = cq.data.split(":")
    deck_id, slot_index, cid = int(parts[1]), int(parts[2]), parts[3]

    db_exec("DELETE FROM multi_deck_slots WHERE deck_id = ? AND slot_index = ?", (deck_id, slot_index))
    db_exec("INSERT INTO multi_deck_slots (deck_id, slot_index, card_id) VALUES (?, ?, ?)", (deck_id, slot_index, cid))

    await cq.answer("Карта установлена!")
    await show_mdeck_slots(cq, deck_id)


@router.callback_query(F.data == "find_match")
async def find_match(cq: CallbackQuery):
    if _handlers.BATTLE_PAUSED:
        return await cq.answer(
            "В боте проводится тех. работа, игра на короткое время недоступна.",
            show_alert=True
        )
    uid = cq.from_user.id
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ?", (uid,), fetchall=True)
    if len(deck) != 6: return await cq.answer("Соберите колоду из 6 карт!", show_alert=True)
    u = get_user(uid)
    last_b = datetime.strptime(u[12], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    cd_hours = 0.5 if is_premium(uid) else BATTLE_COOLDOWN_HOURS
    if (now - last_b).total_seconds() < cd_hours * 3600:
        rem = int(cd_hours * 3600 - (now - last_b).total_seconds())
        return await cq.answer(f"⏳ Кулдаун битвы: {rem // 3600}ч {(rem % 3600) // 60}м", show_alert=True)

    if MATCH_QUEUE and MATCH_QUEUE[0] != uid:
        p2 = MATCH_QUEUE.pop(0)
        await cq.message.delete()
        await start_battle(p2, uid, cq.bot)
    else:
        if uid not in MATCH_QUEUE:
            MATCH_QUEUE.append(uid)
        bld = InlineKeyboardBuilder()
        bld.button(text="Отменить", callback_data="cancel_search")
        msg = await cq.message.answer("Ищем противника... (50 сек)", reply_markup=bld.as_markup())
        asyncio.create_task(wait_match(uid, cq.bot, msg))

@router.callback_query(F.data == "cancel_search")
async def cancel_search(cq: CallbackQuery):
    uid = cq.from_user.id
    if uid in MATCH_QUEUE:
        MATCH_QUEUE.remove(uid)
        await cq.message.delete()
        await cq.message.answer("Поиск отменен. Кулдаун не сброшен.")
    else:
        await cq.message.delete()
        await cq.answer("Вы уже не в поиске.")

async def wait_match(uid, bot, msg_to_edit):
    for _ in range(50):
        await asyncio.sleep(1)
        if uid not in MATCH_QUEUE:
            try: await msg_to_edit.delete()
            except: pass
            return
    if uid in MATCH_QUEUE:
        MATCH_QUEUE.remove(uid)
        try: await msg_to_edit.delete()
        except: pass
        await start_battle(uid, -1, bot)

async def start_battle(p1, p2, bot: Bot, friendly=False):
    gid = f"g_{random.randint(10000, 99999)}"
    deck1 = [c[0] for c in db_exec("SELECT card_id FROM decks WHERE user_id = ?", (p1,), fetchall=True)]

    if p2 == -1:
        deck2 = random.choices(list(CARDS.keys()), k=6)
        name2 = random.choice(["Важни Гий", "Ли Джи Ху..", "Йена пик форма", "Злодей Васко", "Великий Мага", "Босс Табаско", "Срасул", "Брад", "Клон Хикса", "Король Бибизян"])
        rank2 = "Бот"
    else:
        deck2 = [c[0] for c in db_exec("SELECT card_id FROM decks WHERE user_id = ?", (p2,), fetchall=True)]
        u2 = get_user(p2)
        name2, rank2 = f"<a href='tg://user?id={p2}'>{u2[2]}</a>", get_rank(u2[7])

    GAMES[gid] = {'p1': p1, 'p2': p2, 'd1': deck1.copy(), 'd2': deck2.copy(), 'n2': name2, 'r2': rank2,
                  'p1_c': None, 'p2_c': None, 'p1_s': None, 'p2_s': None, 'score1': 0, 'score2': 0, 'round': 1,
                  'friendly': friendly, 'resolving': False}

    u1 = get_user(p1)
    if p2 == -1:
        db_exec("UPDATE users SET last_battle = ?, battle_cooldown_notified = 0 WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), p1))
    else:
        db_exec("UPDATE users SET last_battle = ?, battle_cooldown_notified = 0 WHERE id IN (?, ?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), p1, p2))

    emoji1 = "👑" if is_premium(p1) else "🧩"
    emoji2 = "👑" if p2 != -1 and is_premium(p2) else "🧩"

    prem1 = is_premium(p1)
    pts1_txt = "0 очков" if friendly else f"{4 if prem1 else 3} очка"
    bc1_txt = "3" if friendly else f"{10 if prem1 else 7}"

    txt1 = f"Противник найден!\n\n· Имя: {name2} {emoji2}\n· Ранг: {rank2}\n· Награда: {pts1_txt}🏅, {bc1_txt} BattleCoin 🪙\n\nБитва начинается!"

    if p2 != -1:
        bg_key2 = u2[13] or 'default'
        bg_data2 = BGS.get(bg_key2, BGS['default'])
        bg_file2 = FSInputFile(f"images/backgrounds/{bg_data2.get('file')}")
        try:
            if bg_key2 in VIDEO_BGS:
                await send_cached_video(
                    bot,
                    chat_id=p1,
                    file_path=f"images/backgrounds/{bg_data2.get('file')}",
                    caption=txt1,
                    parse_mode="HTML",
                    supports_streaming=True,
                    width=bg_data2.get('width'),
                    height=bg_data2.get('height')
                )
            else:
                await bot.send_photo(p1, photo=bg_file2, caption=txt1, parse_mode="HTML")
        except:
            await bot.send_message(p1, txt1, parse_mode="HTML")
    else:
        await bot.send_message(p1, txt1, parse_mode="HTML")

    if p2 != -1:
        prem2 = is_premium(p2)
        pts2_txt = "0 очков" if friendly else f"{4 if prem2 else 3} очка"
        bc2_txt = "3" if friendly else f"{10 if prem2 else 7}"
        txt2 = f"Противник найден!\n\n· Имя: <a href='tg://user?id={p1}'>{u1[2]}</a> {emoji1}\n· Ранг: {get_rank(u1[7])}\n· Награда: {pts2_txt}🏅, {bc2_txt} BattleCoin 🪙\n\nБитва начинается!"
        bg_key1 = u1[13] or 'default'
        bg_data1 = BGS.get(bg_key1, BGS['default'])
        bg_file1 = FSInputFile(f"images/backgrounds/{bg_data1.get('file')}")
        try:
            if bg_key1 in VIDEO_BGS:
                await send_cached_video(
                    bot,
                    chat_id=p2,
                    file_path=f"images/backgrounds/{bg_data1.get('file')}",
                    caption=txt2,
                    parse_mode="HTML",
                    supports_streaming=True,
                    width=bg_data1.get('width'),
                    height=bg_data1.get('height')
                )
            else:
                await bot.send_photo(p2, photo=bg_file1, caption=txt2, parse_mode="HTML")
        except:
            await bot.send_message(p2, txt2, parse_mode="HTML")

    await asyncio.sleep(1)
    await send_card_choice(p1, GAMES[gid]['d1'], gid, bot)
    if p2 != -1:
        await send_card_choice(p2, GAMES[gid]['d2'], gid, bot)



async def auto_card_choice(gid, uid, round_num, msg_id, bot):
    await asyncio.sleep(30)
    g = GAMES.get(gid)
    if not g or g['round'] != round_num: return

    is_p1 = (uid == g['p1'])
    card_key = 'p1_c' if is_p1 else 'p2_c'
    deck_key = 'd1' if is_p1 else 'd2'

    if g[card_key] is None and g[deck_key]:
        random_card = random.choice(g[deck_key])
        try:
            await bot.delete_message(uid, msg_id)
        except:
            pass
        await process_card_choice(gid, uid, random_card, bot)


async def auto_style_choice(gid, uid, round_num, msg_id, bot):
    await asyncio.sleep(30)
    g = GAMES.get(gid)
    if not g or g['round'] != round_num: return

    is_p1 = (uid == g['p1'])
    style_key = 'p1_s' if is_p1 else 'p2_s'

    if g[style_key] is None:
        random_style = random.choice(['spd', 'str', 'int'])
        try:
            await bot.delete_message(uid, msg_id)
        except:
            pass
        await process_style_choice(gid, uid, random_style, bot)


async def process_card_choice(gid, uid, card, bot):
    g = GAMES.get(gid)
    if not g: return
    is_p1 = (uid == g['p1'])

    if is_p1:
        if g['p1_c'] is not None: return
        if card not in g['d1']: return
        g['p1_c'] = card
        g['d1'].remove(card)
    else:
        if g['p2_c'] is not None: return
        if card not in g['d2']: return
        g['p2_c'] = card
        g['d2'].remove(card)

    bld = InlineKeyboardBuilder()
    bld.button(text="⚡️ Скорость", callback_data=f"b_style:{gid}:spd")
    bld.button(text="💪 Сила", callback_data=f"b_style:{gid}:str")
    bld.button(text="🧠 Интеллект", callback_data=f"b_style:{gid}:int")

    txt = f"Выбрана карта: {CARDS[card]['name']}\nВыберите ⚔️ Атаку \nСтили: ⚡️ Скорость, 💪 Сила, 🧠 Интеллект.\n\nНа выбор дается 30 секунд"
    card_data = CARDS[card]
    msg = None
    try:
        if is_divine(card_data) and card_data.get("video"):
            msg = await send_cached_video(
                bot,
                chat_id=uid,
                file_path=f"images/cards/{card_data['video']}",
                caption=txt,
                width=card_data.get("width", 960),
                height=card_data.get("height", 1280),
                reply_markup=bld.as_markup(),
                supports_streaming=True
            )
            opponent_id = g['p2'] if uid == g['p1'] else g['p1']
            if opponent_id != -1:
                try:
                    await send_cached_video(
                        bot,
                        chat_id=opponent_id,
                        file_path=f"images/cards/{card_data['video']}",
                        caption=f"⚫️ Противник выбрасывает Божественную карту: {card_data['name']}!",
                        width=card_data.get("width", 960),
                        height=card_data.get("height", 1280),
                        supports_streaming=True
                    )
                except Exception:
                    pass
        else:
            msg = await bot.send_photo(
                uid,
                photo=FSInputFile(f"images/cards/{card_data['file']}"),
                caption=txt,
                reply_markup=bld.as_markup()
            )
    except Exception as e:
        # критично: если медиа карты упало, игра НЕ должна виснуть — шлём fallback с кнопками
        logging.error(f"send card media failed for {uid}, card={card}: {e}")
        try:
            msg = await bot.send_message(uid, txt, reply_markup=bld.as_markup())
        except Exception as e2:
            logging.error(f"fallback send_message failed for {uid}: {e2}")

    if msg is not None:
        current_round = g['round']
        asyncio.create_task(auto_style_choice(gid, uid, current_round, msg.message_id, bot))

    if g['p2'] == -1 and g['p2_c'] is None:
        bot_c = random.choice(g['d2'])
        g['p2_c'] = bot_c
        g['d2'].remove(bot_c)
        g['p2_s'] = random.choice(['spd', 'str', 'int'])

        if g['p1_s'] and g['p2_s']:
            g['resolving'] = True
            try:
                await resolve_round(gid, bot)
            except Exception as e:
                logging.error(f"resolve_round failed (bot path): {e}")
            if gid in GAMES: GAMES[gid]['resolving'] = False


async def process_style_choice(gid, uid, style, bot):
    g = GAMES.get(gid)
    if not g: return
    if g.get('resolving'): return

    is_p1 = (uid == g['p1'])
    if is_p1:
        if g['p1_s'] is not None: return
        g['p1_s'] = style
    else:
        if g['p2_s'] is not None: return
        g['p2_s'] = style

    try:
        msg = await bot.send_message(uid, "Ожидание противника...")
        if is_p1:
            g['p1_wait_msg'] = msg.message_id
        else:
            g['p2_wait_msg'] = msg.message_id
    except:
        pass
    if g['p1_s'] and g['p2_s']:
        g['resolving'] = True
        try:
            if g.get('p1_wait_msg'): await bot.delete_message(g['p1'], g['p1_wait_msg'])
            if g.get('p2_wait_msg') and g['p2'] != -1: await bot.delete_message(g['p2'], g['p2_wait_msg'])
        except:
            pass

        try:
            await resolve_round(gid, bot)
        except Exception as e:
            logging.error(f"Critical error in resolve_round: {e}")
            # Fallback - если произошел сбой, просто переводим игру в следующий раунд, чтобы не зависла
            if gid in GAMES:
                GAMES[gid]['round'] += 1
                GAMES[gid]['p1_c'] = GAMES[gid]['p2_c'] = GAMES[gid]['p1_s'] = GAMES[gid]['p2_s'] = None
                try:
                    await bot.send_message(GAMES[gid]['p1'],
                                           "⚠️ Возникла сетевая ошибка в прошлом раунде, раунд пропущен.")
                    if GAMES[gid]['p2'] != -1:
                        await bot.send_message(GAMES[gid]['p2'],
                                               "⚠️ Возникла сетевая ошибка в прошлом раунде, раунд пропущен.")
                except:
                    pass

                if GAMES[gid]['round'] > 5:
                    await finish_game(gid, bot)
                else:
                    await send_card_choice(GAMES[gid]['p1'], GAMES[gid]['d1'], gid, bot)
                    if GAMES[gid]['p2'] != -1:
                        await send_card_choice(GAMES[gid]['p2'], GAMES[gid]['d2'], gid, bot)

        if gid in GAMES: GAMES[gid]['resolving'] = False


async def send_card_choice(uid, deck_left, gid, bot):
    g = GAMES.get(gid)
    if not g: return

    # Сортируем карты по редкости для отображения от сильнейшей
    c_objs = [(cid, CARDS[cid]) for cid in set(deck_left)]
    rarity_order = {"Божественная ⚫️": 6, "Мифическая 🔴": 5, "Легендарная 🔵": 4, "Эпическая 🟢": 3, "Редкая 🟡": 2,
                    "Обычная ⚪️": 1}
    c_objs.sort(key=lambda x: rarity_order.get(x[1]['rarity'], 0), reverse=True)

    # Формируем медиагруппу (сверху изображения карт)
    media = []
    for i, (cid, c) in enumerate(c_objs):
        txt_card = f"{i + 1}. {c['name']} ({c['rarity']})\n⚡️{c['speed']} | 💪{c['strength']} | 🧠{c['intellect']}"
        media.append(types.InputMediaPhoto(media=FSInputFile(f"images/cards/{c['file']}"), caption=txt_card))

    try:
        await bot.send_media_group(uid, media=media)
    except Exception as e:
        logging.error(f"Failed to send visual deck to {uid}: {e}")

    # Кнопки выбора (снизу, в порядке силы)
    bld = InlineKeyboardBuilder()
    for cid, c in c_objs:
        bld.button(text=c['name'], callback_data=f"b_card:{gid}:{cid}")
    bld.adjust(2)

    txt = f"—————————————————\n\nРаунд {g['round']}.\nВыберите 🎴 карту для атаки\n\n⏳ На выбор дается 30 секунд"
    try:
        msg = await bot.send_message(uid, txt, reply_markup=bld.as_markup())
        asyncio.create_task(auto_card_choice(gid, uid, g['round'], msg.message_id, bot))
    except Exception as e:
        logging.error(f"Failed to send card choice keyboard: {e}")


@router.callback_query(F.data.startswith("b_card:"))
async def b_card(cq: CallbackQuery):
    _, gid, card = cq.data.split(":")
    g = GAMES.get(gid)
    if not g: return await cq.answer("Игра окончена.", show_alert=True)
    is_p1 = (cq.from_user.id == g['p1'])
    deck = g['d1'] if is_p1 else g['d2']
    if card not in deck: return await cq.answer("Эта карта уже использована!", show_alert=True)

    await cq.answer()
    try:
        await cq.message.delete()
    except Exception:
        pass
    await process_card_choice(gid, cq.from_user.id, card, cq.bot)

@router.callback_query(F.data.startswith("b_style:"))
async def b_style(cq: CallbackQuery):
    _, gid, style = cq.data.split(":")
    g = GAMES.get(gid)
    if not g: return await cq.answer("Игра окончена.", show_alert=True)
    is_p1 = (cq.from_user.id == g['p1'])
    if (is_p1 and g['p1_s'] is not None) or (not is_p1 and g['p2_s'] is not None):
        return await cq.answer("Вы уже выбрали стиль!", show_alert=True)

    await cq.answer()
    try:
        await cq.message.delete()
    except Exception:
        pass
    await process_style_choice(gid, cq.from_user.id, style, cq.bot)


async def resolve_round(gid, bot):
    g = GAMES[gid]
    c1, c2 = CARDS[g['p1_c']], CARDS[g['p2_c']]

    s_map = {'spd': ('⚡️ Скорость', '⚡️ Скоростную', 'speed'),
             'str': ('💪 Сила', '💪 Силовую', 'strength'),
             'int': ('🧠 Интеллект', '🧠 Интеллектуальную', 'intellect')}

    my_name = get_user(g['p1'])[2]
    n1 = f"<a href='tg://user?id={g['p1']}'>{my_name}</a>"
    if g['p2'] == -1:
        n2 = g['n2']
        n2_link = g['n2']
    else:
        n2 = get_user(g['p2'])[2]
        n2_link = f"<a href='tg://user?id={g['p2']}'>{n2}</a>"

    val1, val2 = c1[s_map[g['p1_s']][2]], c2[s_map[g['p2_s']][2]]

    adv = check_advantage(g['p1_s'], g['p2_s'])

    m1, m2 = 1.0, 1.0
    bonus_txt_1, bonus_txt_2 = "", ""

    if adv == 1:
        m2 = 0.9
        bonus_txt_1 = f"{s_map[g['p2_s']][0]} -10% ↘️"
        bonus_txt_2 = f"{s_map[g['p2_s']][0]} -10% ↘️"
    elif adv == -1:
        m1 = 0.9
        bonus_txt_1 = f"{s_map[g['p1_s']][0]} -10% ↘️"
        bonus_txt_2 = f"{s_map[g['p1_s']][0]} -10% ↘️"

    f1, f2 = int(val1 * m1), int(val2 * m2)

    emoji1 = "👑" if is_premium(g['p1']) else "🧩"
    emoji2 = "👑" if g['p2'] != -1 and is_premium(g['p2']) else "🧩"

    if f1 > f2:
        g['score1'] += 1
        winner_name = f"{n1} {emoji1}"
    elif f2 > f1:
        g['score2'] += 1
        winner_name = f"{n2_link} {emoji2}"
    else:
        winner_name = "Ничья"

    def format_text(p_name, e_name, score_p, score_e, p_s, e_s, p_val, e_val, p_final, e_final, b_txt, p_emoji, e_emoji):
        t = (f"⬆️ Ваша карта | Карта врага ⬆️\nРаунд - {g['round']}\n\n"
             f"Счет:\n{p_name} {p_emoji} - {score_p}\n{e_name} {e_emoji} - {score_e}\n\n"
             f"⚔️ Вы совершаете {s_map[p_s][1]} атаку\nУровень атаки: {p_val}\n\n"
             f"🛡️ Противник ставит {s_map[e_s][1]} защиту\nУровень защиты: {e_val}\n\n")
        if adv != 0: t += f"Бонус\n{b_txt}\n\n"
        t += (f"Итоговый уровень атаки {s_map[p_s][0].split()[0]} : {p_final}\n"
              f"Итоговый уровень защиты {s_map[e_s][0].split()[0]}: {e_final}\n\n")
        t += f"Раунд завершился в ничью!" if winner_name == "Ничья" else f"Раунд выиграл {winner_name}"
        return t

    try:
        txt1 = format_text(n1, n2_link, g['score1'], g['score2'], g['p1_s'], g['p2_s'], val1, val2, f1, f2, bonus_txt_1, emoji1, emoji2)
        media1 = [types.InputMediaPhoto(media=FSInputFile(f"images/cards/{c1['file']}"), caption=txt1, parse_mode="HTML"),
                  types.InputMediaPhoto(media=FSInputFile(f"images/cards/{c2['file']}"))]
        await bot.send_media_group(g['p1'], media=media1)
    except Exception as e:
        logging.error(f"Error sending round result to p1: {e}")

    if g['p2'] != -1:
        try:
            txt2 = format_text(n2_link, n1, g['score2'], g['score1'], g['p2_s'], g['p1_s'], val2, val1, f2, f1,
                               bonus_txt_2, emoji2, emoji1)
            media2 = [types.InputMediaPhoto(media=FSInputFile(f"images/cards/{c2['file']}"), caption=txt2, parse_mode="HTML"),
                      types.InputMediaPhoto(media=FSInputFile(f"images/cards/{c1['file']}"))]
            await bot.send_media_group(g['p2'], media=media2)
        except Exception as e:
            logging.error(f"Error sending round result to p2: {e}")

    g['round'] += 1
    g['p1_c'] = g['p2_c'] = g['p1_s'] = g['p2_s'] = None

    if g['round'] > 5:
        await finish_game(gid, bot)
    else:
        await asyncio.sleep(2)
        try:
            await send_card_choice(g['p1'], g['d1'], gid, bot)
        except Exception as e:
            logging.error(f"Error sending card choice to p1: {e}")

        if g['p2'] != -1:
            try:
                await send_card_choice(g['p2'], g['d2'], gid, bot)
            except Exception as e:
                logging.error(f"Error sending card choice to p2: {e}")


async def finish_game(gid, bot):
    g = GAMES.pop(gid)
    p1, p2, s1, s2 = g['p1'], g['p2'], g['score1'], g['score2']
    friendly = g.get('friendly', False)

    def apply_res(uid, is_win, is_draw, friendly):
        if uid == -1: return 0, 0
        premium = is_premium(uid)

        if friendly:
            pts = 0
            bc = 3 if is_win else 1
        else:
            if is_win:
                pts = 4 if premium else 3
                bc = 10 if premium else 7
            elif is_draw:
                pts = 2 if premium else 1
                bc = 3 if premium else 2
            else:
                pts = -1 if premium else -2
                bc = 2 if premium else 1

        db_exec(f"UPDATE users SET rank_points = MAX(0, rank_points + {pts}), battlecoin = battlecoin + {bc}, " +
                ("wins = wins + 1" if is_win else ("draws = draws + 1" if is_draw else "losses = losses + 1")) +
                " WHERE id = ?", (uid,))
        return pts, bc

    draw = (s1 == s2)
    r1 = apply_res(p1, s1 > s2, draw, friendly)
    r2 = apply_res(p2, s2 > s1, draw, friendly)

    my_name = get_user(p1)[2]
    n2 = g['n2'] if p2 == -1 else get_user(p2)[2]

    await bot.send_message(p1, f"Игра окончена!\nСчет: {my_name} {s1} - {s2} {n2}\nНаграда: {r1[0]}🏅, {r1[1]}🪙")
    if p2 != -1:
        await bot.send_message(p2, f"Игра окончена!\nСчет: {n2} {s2} - {s1} {my_name}\nНаграда: {r2[0]}🏅, {r2[1]}🪙")


# ============ ЗАЩИТА И БЛОКИРОВКА ВО ВРЕМЯ БОЯ ============
from aiogram import BaseMiddleware


class BattleLockMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, types.CallbackQuery):
            uid = event.from_user.id
            in_battle = any(g['p1'] == uid or g['p2'] == uid for g in GAMES.values())

            allowed = ('b_card:', 'b_style:', 'surrender:')
            if in_battle and not event.data.startswith(allowed):
                gid = next((k for k, v in GAMES.items() if v['p1'] == uid or v['p2'] == uid), None)
                if gid:
                    bld = InlineKeyboardBuilder()
                    bld.button(text="Сдаться 🏳️", callback_data=f"surrender:{gid}")
                    try:
                        await event.message.answer("Вы совершили недопустимое действие во время боя. Сдаться?",
                                                   reply_markup=bld.as_markup())
                        await event.answer()
                    except:
                        pass
                    return
        return await handler(event, data)


router.callback_query.middleware(BattleLockMiddleware())


@router.callback_query(F.data.startswith("surrender:"))
async def surrender_battle(cq: CallbackQuery):
    _, gid = cq.data.split(":")
    g = GAMES.get(gid)
    if not g:
        return await cq.answer("Бой уже завершен.", show_alert=True)

    uid = cq.from_user.id
    is_p1 = (uid == g['p1'])
    if is_p1:
        g['score1'] = -1
        g['score2'] = 99
    else:
        g['score2'] = -1
        g['score1'] = 99

    try:
        await cq.message.answer("Вы сдались! Поражение.")
        await cq.message.delete()
    except:
        pass

    if g['p2'] != -1:
        other_id = g['p2'] if is_p1 else g['p1']
        try:
            await cq.bot.send_message(other_id, "Противник сдался! Вы победили.")
        except:
            pass

    await finish_game(gid, cq.bot)
    await cq.answer()

# ============ НОВОЕ МЕНЮ ТОП И РАНГИ ============
@router.callback_query(F.data == "b_top_ranks")
async def b_top_ranks_cb(cq: CallbackQuery):
    txt = "<i>Здесь можно получать награды, посмотреть топ и ранги, выбирай что хочешь посмотреть:</i>"
    bld = InlineKeyboardBuilder()
    bld.button(text="🏆 ТОП", callback_data="b_top_menu")
    bld.button(text="РАНГИ 🎖", callback_data="b_ranks_menu")
    bld.button(text="Назад 🔙", callback_data="b_menu_back")
    bld.adjust(2, 1)

    try:
        await cq.message.edit_caption(caption=txt, reply_markup= bld.as_markup(), parse_mode="HTML")
    except Exception:
        try:
            await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        except:
            pass
    await cq.answer()


@router.callback_query(F.data == "b_menu_back")
async def b_menu_back_cb(cq: CallbackQuery):
    try:
        await cq.message.delete()
    except:
        pass
    u = get_user(cq.from_user.id)
    txt = (f"⚔️ BATTLE FIELD ACCESS\n\n"
           f"Добро пожаловать на поле битвы, Игрок.\n\n"
           f"Вы входите в зону PvP-испытаний. Здесь формируется сила через сражения, а каждый бой влияет на ваш ранг 📊\n\n"
           f"<blockquote>🔓 Условия доступа к «Битвам ⚔️»:\n"
           f"→ Необходимо собрать 10 боевых карт 🃏</blockquote>\n\n"
           f"▶️ РЕЖИМ: АКТИВЕН\n"
           f"▶️ СТАТУС: БОЕВАЯ СИСТЕМА ОНЛАЙН И ОФЛАЙН\n\n"
           f"━━━━━━━━━━━━━━━\n"
           f'🏅 {u[7]} Очков | Ранг {get_rank(u[7])}\n'
           f"Победа / Ничья / Поражение :\n"
           f"{u[8]} / {u[9]} / {u[10]}\n"
           f"━━━━━━━━━━━━━━━\n\n"
           f"Каждое сражение фиксируется в хронике данных.")

    bld = InlineKeyboardBuilder()
    bld.button(text="Найти противника 👁️", callback_data="find_match")
    bld.button(text="Дружеский бой 🔪", callback_data="friendly_match_start")
    bld.button(text="Моя колода 🗂️", callback_data="my_deck")
    bld.button(text="🛒 BattleShop", callback_data="b_shop_main")
    bld.button(text="🔝 ТОП И РАНГИ", callback_data="b_top_ranks")
    bld.adjust(1, 2, 1, 1)

    if os.path.exists("images/shop/battle.jpeg"):
        await cq.message.answer_photo(photo=FSInputFile("images/shop/battle.jpeg"), caption=txt,
                                      reply_markup=bld.as_markup())
    else:
        await cq.message.answer(txt, reply_markup=bld.as_markup())
    await cq.answer()


@router.callback_query(F.data == "b_top_menu")
async def b_top_menu_cb(cq: CallbackQuery):
    txt = "<i>Выбери каталог топа:</i>"
    bld = InlineKeyboardBuilder()
    bld.button(text="🏆 Топ по победам", callback_data="b_top_wins")
    bld.button(text="🏆 Топ по рангам", callback_data="b_top_rankpts")
    bld.button(text="Назад 🔙", callback_data="b_top_ranks")
    bld.adjust(2, 1)

    try:
        await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        try:
            await cq.message.edit_text(text=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        except:
            pass
    await cq.answer()


# === РАНГИ И НАГРАДЫ ===
RANK_REWARDS = {
    "Новичок 💩": 0, "Боец 🦸‍♂️": 1, "Пробуждённый 🪬": 3, "Неоспоримый 👾": 5,
    "Уровень Короля 👑": 10, "Титан 🧬": 15, "Легенда 🐉": 20, "Безупречная мощь 😈": 30,
    "Абсолют ♾️": 40, "Владыка Хаоса 🌋": 55, "Монарх Пустоты 🌑": 75, "Бессмертный Архонт 🪽": 100
}


@router.callback_query(F.data == "b_ranks_menu")
async def b_ranks_menu_cb(cq: CallbackQuery):
    u = get_user(cq.from_user.id)
    my_pts = u[7]
    my_rank = get_rank(my_pts)

    ranks = [
        (14000, "Бессмертный Архонт 🪽"), (10000, "Монарх Пустоты 🌑"), (6500, "Владыка Хаоса 🌋"),
        (4500, "Абсолют ♾️"), (3000, "Безупречная мощь 😈"), (2000, "Легенда 🐉"),
        (1600, "Титан 🧬"), (1000, "Уровень Короля 👑"), (600, "Неоспоримый 👾"),
        (300, "Пробуждённый 🪬"), (100, "Боец 🦸‍♂️"), (0, "Новичок 💩")
    ]
    next_rank = "Максимальный"
    for i in range(len(ranks) - 1, -1, -1):
        if my_pts < ranks[i][0]:
            next_rank = ranks[i][1]
            break

    my_reward = RANK_REWARDS.get(my_rank, 0)

    txt = (
        "📊 Система рангов:\n\n"
        "1. Новичок 💩 - 0 очков\n"
        "2. Боец 🦸‍♂️ - 100 очков\n"
        "3. Пробуждённый 🪬 - 300 очков\n"
        "4. Неоспоримый 👾 - 600 очков\n"
        "5. Уровень Короля 👑 - 1000 очков\n"
        "6. Титан 🧬 - 1600 очков\n"
        "7. Легенда 🐉 - 2000 очков\n"
        "8. Безупречная мощь 😈 - 3000 очков\n"
        "9. Абсолют ♾️ - 4500 очков\n"
        "10. Владыка Хаоса 🌋 - 6500 очков\n"
        "11. Монарх Пустоты 🌑 - 10000 очков\n"
        "12. Бессмертный Архонт 🪽 - 14000 очков\n\n"
        f"Твой ранг: {my_rank}\n"
        f"Следующий ранг: {next_rank}\n"
        f"Твои очки: {my_pts} очков\n"
        f"Награда: {my_reward} 💎\n\n"
        "<blockquote>Собрать награды можно по кнопке «Собрать награду 💎» каждого 1-го и 15-го числа</blockquote>"
    )
    bld = InlineKeyboardBuilder()
    bld.button(text="Собрать награду 💎", callback_data="b_rank_claim")
    bld.button(text="Назад 🔙", callback_data="b_top_ranks")
    bld.adjust(1)

    try:
        await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except:
        try:
            await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        except:
            pass
    await cq.answer()


@router.callback_query(F.data == "b_rank_claim")
async def b_rank_claim_cb(cq: CallbackQuery):
    now = datetime.now()
    if now.day not in [1, 15]:
        return await cq.answer("Награду можно забрать только 1-го и 15-го числа!", show_alert=True)

    uid = cq.from_user.id
    claim_date = now.strftime("%Y-%m-%d")

    already_claimed = db_exec("SELECT 1 FROM user_ranks_claims WHERE user_id = ? AND claim_date = ?", (uid, claim_date),
                              fetch=True)
    if already_claimed:
        return await cq.answer("Вы уже забрали награду за этот период!", show_alert=True)

    u = get_user(uid)
    my_rank = get_rank(u[7])
    reward = RANK_REWARDS.get(my_rank, 0)

    if reward > 0:
        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (reward, uid))
        db_exec("INSERT INTO user_ranks_claims (user_id, claim_date) VALUES (?, ?)", (uid, claim_date))
        await cq.answer(f"✅ Вы успешно забрали {reward} 💎!", show_alert=True)
    else:
        await cq.answer("Ваш ранг не позволяет получить награду.", show_alert=True)


# === ТОП ПО ПОБЕДАМ И РАНГАМ ===
@router.callback_query(F.data == "b_top_wins")
async def b_top_wins_cb(cq: CallbackQuery):
    top_users = db_exec("SELECT id, nickname, wins FROM users ORDER BY wins DESC LIMIT 10", fetchall=True)
    all_users = db_exec("SELECT id FROM users ORDER BY wins DESC", fetchall=True)
    my_place = "Без места"
    for idx, (uid,) in enumerate(all_users):
        if uid == cq.from_user.id:
            my_place = idx + 1
            break
    txt = "🏆 ТОП 10 по Победам:\n\n"
    for i, user in enumerate(top_users):
        emoji = "👑" if is_premium(user[0]) else "🧩"
        txt += f"{i + 1}. {user[1]} {emoji} — {user[2]} 🎖\n"

    txt += (
        "\nНаграды:\n"
        "<blockquote>🥇 1-е место: 150 💎 Алмазов, 2000 🪙 BattleCoin\n"
        "🥈 2-е место: 100 💎 Алмазов, 1500 🪙 BattleCoin\n"
        "🥉 3-е место: 75 💎 Алмазов, 1250 🪙 BattleCoin\n"
        "🏅 4-10 места: 50 💎 Алмазов, 750 🪙 BattleCoin\n"
        "🏅 11-25 места: 10 💎 Алмазов, 600 🪙 BattleCoin\n"
        "🏅 26-75 места: 400 🪙 BattleCoin\n"
        "🏅 76-150 места: 250 🪙 BattleCoin</blockquote>\n\n"
        "Награда выдается автоматически каждого 17-го числа🎖\n\n"
        "🎁 Приз за 1-20 места лимитированная карта:\n"
        "<blockquote>🃏 Дже Хван</blockquote>\n\n"
        "📅 Дата окончания: 17-го июня\n"
        f"🏆 Ваше место в ТОП-е: {my_place}\n"
        "🚸 ТОП обновляется каждые 3 часа."
    )
    bld = InlineKeyboardBuilder()
    bld.button(text="Назад 🔙", callback_data="b_top_menu")

    try:
        await cq.message.delete()
    except:
        pass

    if os.path.exists("images/shop/top_wins.jpeg"):
        await cq.message.answer_photo(photo=FSInputFile("images/shop/top_wins.jpeg"), caption=txt,
                                      reply_markup=bld.as_markup(), parse_mode="HTML")
    else:
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data == "b_top_rankpts")
async def b_top_rankpts_cb(cq: CallbackQuery):
    top_users = db_exec("SELECT id, nickname, rank_points FROM users ORDER BY rank_points DESC LIMIT 10", fetchall=True)
    all_users = db_exec("SELECT id FROM users ORDER BY rank_points DESC", fetchall=True)
    my_place = "Без места"
    for idx, (uid,) in enumerate(all_users):
        if uid == cq.from_user.id:
            my_place = idx + 1
            break

    txt = "🏆 Топ пользователей по Рангам и Очкам\n\n"
    for i, user in enumerate(top_users):
        emoji = "👑" if is_premium(user[0]) else "🧩"
        txt += f"{i + 1}. {user[1]} {emoji} - {user[2]}\n"

    txt += (
        "\n🕓 Топ обновляется раз в сутки\n"
        f"🔝 Ваше место в топе: {my_place}"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="Назад 🔙", callback_data="b_top_menu")

    try:
        await cq.message.delete()
    except:
        pass

    if os.path.exists("images/shop/top_ranks.jpeg"):
        await cq.message.answer_photo(photo=FSInputFile("images/shop/top_ranks.jpeg"), caption=txt,
                                      reply_markup=bld.as_markup())
    else:
        await cq.message.answer(txt, reply_markup=bld.as_markup())
    await cq.answer()


# ============ МАГАЗИН БИТВЫ ============
@router.callback_query(F.data == "b_shop_main")
async def b_shop_main_cb(cq: CallbackQuery):
    txt = (
        "[ SYSTEM MESSAGE ]\n\n"
        "🛒 Боевой магазин активирован.\n\n"
        "Доступны новые карты, эксклюзивные титулы\n"
        "и видео-фоны.\n\n"
        "Некоторые награды имеют мифический ранг.\n"
        "Есть особый пак, где шанс выпадения редких предметов повышен."
    )
    bld = InlineKeyboardBuilder()
    bld.button(text="Боевой Пак 🗄️", callback_data="b_shop_pack")
    bld.button(text="Крутки 🪙", callback_data="b_shop_spins")
    bld.button(text="Назад 🔙", callback_data="b_menu_back")
    bld.adjust(2, 1)

    try:
        await cq.message.delete()
    except:
        pass

    if os.path.exists("images/shop/battle_shop.png"):
        await cq.message.answer_photo(photo=FSInputFile("images/shop/battle_shop.png"), caption=txt,
                                      reply_markup=bld.as_markup())
    else:
        await cq.message.answer(txt, reply_markup=bld.as_markup())
    await cq.answer()


# === ВСТАВИТЬ В НАЧАЛО battle.py ПОСЛЕ ИМПОРТОВ (примерно строка 30) ===
PACK_CARD = "excluzive_card_jaehwan"
PACK_BG1 = "yamzaki_clan"
PACK_BG2 = "jaehwan"
PACK_TITLE = "title_pack"

# === ЗАМЕНИТЬ ФУНКЦИИ b_shop_pack_cb И b_shop_pack_buy_cb (строки 1459-1533) ===

@router.callback_query(F.data == "b_shop_pack")
async def b_shop_pack_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    now = datetime.now()
    week_num = now.isocalendar()[1]

    res = db_exec("SELECT bought_count FROM battle_shop_packs WHERE user_id = ? AND week_number = ?", (uid, week_num), fetch=True)
    bought = res[0] if res else 0

    txt = (
        "<b>Боевой Пак ⚡️</b>\n"
        f"💵 Можно купить: <b>{3 - bought}</b>\n"
        f"💸 Куплено: <b>{bought}</b>\n\n"
        "<blockquote>Стоимость: 400 🪙</blockquote>\n\n"
        "🔥 Главный приз: <b>Дже Хван</b>\n"
        "🧪 Содержимое:\n"
        "<blockquote>🃏 Дже Хван 0.05%\n"
        "🌄 Клан Ямадзаки 0.5%\n"
        "🌄 Дже Хван 2.5%\n"
        "🔱 Пронзающий судьбу 2.5%\n"
        "🔴 Мифическая карта 4.45%\n"
        "🔵 Легендарная карта 90%</blockquote>\n\n"
        "🏆 Главный приз выдается автоматически за ТОП 20 по победам!\n\n"
        "📅 Дата окончания пака: 17-го Июня 📆"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="• Купить 💵", callback_data="b_shop_pack_buy")
    bld.button(text="Назад 🔙", callback_data="b_shop_main")
    bld.adjust(1)

    try:
        await cq.message.delete()
    except:
        pass

    if os.path.exists("images/shop/battlepack.jpeg"):
        await cq.message.answer_photo(photo=FSInputFile("images/shop/battlepack.jpeg"), caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    else:
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()

@router.callback_query(F.data == "b_shop_pack_buy")
async def b_shop_pack_buy_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    now = datetime.now()
    week_num = now.isocalendar()[1]

    res = db_exec("SELECT bought_count FROM battle_shop_packs WHERE user_id = ? AND week_number = ?", (uid, week_num), fetch=True)
    bought = res[0] if res else 0

    if bought >= 3:
        return await cq.answer("Вы уже купили этот пак 3 раза на этой неделе!", show_alert=True)

    u = get_user(uid)
    if u[5] < 400:
        return await cq.answer("❌ Недостаточно BattleCoin! Нужно: 400 🪙", show_alert=True)

    # Списание валюты и обновление счетчика
    db_exec("UPDATE users SET battlecoin = battlecoin - 400 WHERE id = ?", (uid,))
    if res:
        db_exec("UPDATE battle_shop_packs SET bought_count = bought_count + 1 WHERE user_id = ? AND week_number = ?",
                (uid, week_num))
    else:
        db_exec("INSERT INTO battle_shop_packs (user_id, week_number, bought_count) VALUES (?, ?, 1)", (uid, week_num))

    # Логика шансов
    rewards = ["card_main", "bg_yamazaki", "bg_jaehwan", "title", "mythic", "legendary"]
    weights = [0.05, 0.5, 2.5, 2.5, 4.45, 90.0]
    result = random.choices(rewards, weights=weights, k=1)[0]

    reward_text = ""
    sent_media = False
    card_c = None  # Сюда будем записывать объект карты для картинки

    if result == "card_main":
        is_new, krw, card_c = give_card_to_user(uid, PACK_CARD)
        reward_text = format_card_msg(card_c)
    elif result == "bg_yamazaki":
        db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, PACK_BG1))
        bg_key = PACK_BG1
        bg_data = VIDEO_BGS.get(bg_key) or BGS.get(bg_key)
        if bg_data:
            file_path = f"images/backgrounds/{bg_data.get('file')}"
            bg_name = bg_data.get('name', 'Новый фон')
            caption_text = f"✨ <b>Поздравляем!</b>\n\nТебе выпал новый фон: <b>{bg_name}</b> {{joy}}"
            try:
                if bg_key in VIDEO_BGS:
                    await send_cached_video(
                        cq.bot,
                        chat_id=uid,
                        file_path=file_path,
                        caption=caption_text,
                        parse_mode="HTML",
                        supports_streaming=True,
                        width=bg_data.get('width'),
                        height=bg_data.get('height')
                    )
                else:
                    await cq.bot.send_photo(uid, photo=FSInputFile(file_path), caption=caption_text, parse_mode="HTML")
                sent_media = True
            except Exception:
                reward_text = f"🌄 Получен новый фон: <b>{bg_name}</b>!"
        else:
            reward_text = f"🌄 Получен новый фон: <b>Клан Ямадзаки</b>!"
    elif result == "bg_jaehwan":
        db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, PACK_BG2))
        bg_key = PACK_BG2
        bg_data = VIDEO_BGS.get(bg_key) or BGS.get(bg_key)
        if bg_data:
            file_path = f"images/backgrounds/{bg_data.get('file')}"
            bg_name = bg_data.get('name', 'Новый фон')
            caption_text = f"✨ <b>Поздравляем!</b>\n\nТебе выпал новый фон: <b>{bg_name}</b> {{joy}}"
            try:
                if bg_key in VIDEO_BGS:
                    await send_cached_video(
                        cq.bot,
                        chat_id=uid,
                        file_path=file_path,
                        caption=caption_text,
                        parse_mode="HTML",
                        supports_streaming=True,
                        width=bg_data.get('width'),
                        height=bg_data.get('height')
                    )
                else:
                    await cq.bot.send_photo(uid, photo=FSInputFile(file_path), caption=caption_text, parse_mode="HTML")
                sent_media = True
            except Exception:
                reward_text = f"🌄 Получен новый фон: <b>{bg_name}</b>!"
        else:
            reward_text = f"🌄 Получен новый фон: <b>Дже Хван</b>!"
    elif result == "title":
        db_exec("INSERT INTO titles_inv (user_id, title_id) VALUES (?, ?)", (uid, PACK_TITLE))
        reward_text = f"🔱 Получен новый титул: <b>Пронзающий судьбу 🩸</b>!"
    elif result == "mythic":
        card_key = pull_random_card(force_rarity="Мифическая 🔴")
        is_new, krw, card_c = give_card_to_user(uid, card_key)
        reward_text = format_card_msg(card_c)
    else:  # legendary
        card_key = pull_random_card(force_rarity="Легендарная 🔵")
        is_new, krw, card_c = give_card_to_user(uid, card_key)
        reward_text = format_card_msg(card_c)

    # ЭФФЕКТ ГАЧИ: Отправляем с картинкой и спойлером, если выпала карта
    if not sent_media and card_c is not None and card_c.get("file"):
        try:
            if "Божественная" in card_c.get("rarity", "") and card_c.get("video"):
                await send_cached_video(
                    cq.bot,
                    chat_id=uid,
                    file_path=f"images/cards/{card_c['video']}",
                    caption=reward_text,
                    width=card_c.get("width", 960),
                    height=card_c.get("height", 1280),
                    has_spoiler=True, # Эффект размытия (гача)
                    supports_streaming=True
                )
            else:
                await cq.bot.send_photo(
                    uid,
                    photo=FSInputFile(f"images/cards/{card_c['file']}"),
                    caption=reward_text,
                    has_spoiler=True, # Эффект размытия (гача)
                    parse_mode="HTML"
                )
        except Exception:
            await cq.message.answer(reward_text, parse_mode="HTML")
    elif not sent_media:
        await cq.message.answer(reward_text, parse_mode="HTML")

    await cq.answer("Пак открыт!", show_alert=True)
    await b_shop_pack_cb(cq)


def format_card_msg(c):
    """Вспомогательная функция для формирования текста карты по твоему примеру"""
    return (
        f"🃏 <b>Получена новая боевая карта!</b>\n\n"
        f"🎴 <b>Персонаж:</b> {c['name']}\n"
        f"🔮 <b>Редкость:</b> {c['rarity']}\n"
        f"👊 <b>Стиль боя:</b> {c['style']}\n"
        f"🪐 <b>Вселенная:</b> {c.get('series', 'Неизвестно')}\n\n"
        f"⚡️ <b>Скорость:</b> {c['speed']}\n"
        f"💪 <b>Сила:</b> {c['strength']}\n"
        f"🧠 <b>Интеллект:</b> {c['intellect']}"
    )


@router.callback_query(F.data == "b_shop_spins")
async def b_shop_spins_cb(cq: CallbackQuery):
    txt = "Здесь вы можете приобрести крутки за валюту <b>BattleCoin 🪙</b>"

    bld = InlineKeyboardBuilder()
    bld.button(text="25 🪙 = 1 💳", callback_data="b_spin_buy:25:1")
    bld.button(text="250 🪙 = 10 💳", callback_data="b_spin_buy:250:10")
    bld.button(text="2500 🪙 = 110 💳", callback_data="b_spin_buy:2500:110")
    bld.button(text="Назад 🔙", callback_data="b_shop_main")
    bld.adjust(1)

    try:
        await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except:
        try:
            await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        except:
            pass
    await cq.answer()


@router.callback_query(F.data.startswith("b_spin_buy:"))
async def b_spin_buy_cb(cq: CallbackQuery):
    _, cost_str, att_str = cq.data.split(":")
    cost = int(cost_str)
    att = int(att_str)

    uid = cq.from_user.id
    u = get_user(uid)
    if u[5] < cost:
        return await cq.answer(f"❌ Недостаточно средств! Нужно: {cost} 🪙", show_alert=True)

    db_exec("UPDATE users SET battlecoin = battlecoin - ?, attempts = attempts + ? WHERE id = ?", (cost, att, uid))
    await cq.answer(f"✅ Куплено {att} попыток!", show_alert=True)
    # === ДОБАВИТЬ В КОНЕЦ battle.py (ФУНКЦИЯ ДЛЯ ВЫДАЧИ ТОП-20) ===

async def distribute_top_20_rewards(bot: Bot):
    """
    Функция для автоматической выдачи карты Дже Хван ТОП-20 игрокам по победам.
    Её можно вызывать по команде админа или через планировщик.
    """
    top_20 = db_exec("SELECT id FROM users ORDER BY wins DESC LIMIT 20", fetchall=True)
    count = 0
    for (uid,) in top_20:
        # Проверяем, есть ли уже эта карта у игрока (чтобы не дублировать)
        exists = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (uid, PACK_CARD), fetch=True)
        if not exists:
            give_card_to_user(uid, PACK_CARD)
            count += 1
            try:
                c = CARDS[PACK_CARD]
                txt = f"🏆 <b>Поздравляем!</b>\nВы вошли в ТОП-20 по победам и получаете эксклюзивную награду!\n\n" + format_card_msg(
                    c)
                await bot.send_photo(uid, photo=FSInputFile(f"images/cards/{c['file']}"), caption=txt, parse_mode="HTML")
            except:
                pass
    return count

# Можно добавить команду админа для запуска выдачи вручную
@router.message(Command("distribute_top"))
async def cmd_distribute_top(msg: Message, bot: Bot):
    if msg.from_user.id not in ADMIN_IDS: return
    count = await distribute_top_20_rewards(bot)
    await msg.answer(f"✅ Награды выданы {count} игрокам из ТОП-20!")

async def distribute_all_top_rewards(bot: Bot):
    """Распределяет награды и карты для ТОП 150 игроков по победам"""
    top_users = db_exec("SELECT id, wins FROM users WHERE wins > 0 ORDER BY wins DESC LIMIT 150", fetchall=True)
    count_curr, count_cards = 0, 0

    for i, (uid, wins) in enumerate(top_users):
        place = i + 1
        dia, bc = 0, 0

        # Награды в зависимости от места
        if place == 1: dia, bc = 150, 2000
        elif place == 2: dia, bc = 100, 1500
        elif place == 3: dia, bc = 75, 1250
        elif 4 <= place <= 10: dia, bc = 50, 750
        elif 11 <= place <= 25: dia, bc = 10, 600
        elif 26 <= place <= 75: dia, bc = 0, 400
        elif 76 <= place <= 150: dia, bc = 0, 250
        # Начисляем валюту
        if dia > 0 or bc > 0:
            db_exec("UPDATE users SET diamond = diamond + ?, battlecoin = battlecoin + ? WHERE id = ?", (dia, bc, uid))
            count_curr += 1
            try:
                await bot.send_message(uid,
                                       f"🏆 <b>Итоги сезона ТОПа!</b>\nВы заняли <b>{place}-е место</b> по победам!\n\nВаша награда: {dia} 💎, {bc} 🪙",
                                       parse_mode="HTML")
            except:
                pass

        # Выдаем карту за 1-20 место
        if place <= 20:
            exists = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (uid, PACK_CARD), fetch=True)
            if not exists:
                give_card_to_user(uid, PACK_CARD)
                count_cards += 1
                try:
                    c = CARDS[PACK_CARD]
                    txt = f"🏆 <b>Поздравляем!</b>\nВы вошли в ТОП-20 по победам и получаете лимитированную карту!\n\n" + format_card_msg(
                        c)
                    await bot.send_photo(uid, photo=FSInputFile(f"images/cards/{c['file']}"), caption=txt,
                                         parse_mode="HTML")
                except:
                    pass

    return count_curr, count_cards


@router.message(Command("reset_top"))
async def cmd_reset_top(msg: Message, bot: Bot):
    """Команда для принудительного сброса ТОПа"""
    if msg.from_user.id not in ADMIN_IDS: return
    db_exec("UPDATE users SET wins = 0")
    await msg.answer("✅ ТОП по победам был успешно сброшен! Начались новые битвы за места в ТОПЕ.")


@router.message(Command("distribute_top"))
async def cmd_distribute_top(msg: Message, bot: Bot):
    """Команда для ручной выдачи наград за ТОП"""
    if msg.from_user.id not in ADMIN_IDS: return
    count_curr, count_cards = await distribute_all_top_rewards(bot)
    await msg.answer(f"✅ Награды выданы! Игрокам выдано валют: {count_curr}, карт: {count_cards}.")


async def auto_top_distributor(bot: Bot):
    """Фоновая задача для автоматической выдачи 17-го числа"""
    while True:
        now = datetime.now()
        # Проверяем, 17-е ли число и время 12:00
        if now.day == 17 and now.hour == 12 and now.minute == 0:
            month_str = now.strftime("%Y-%m")
            already = db_exec("SELECT 1 FROM user_ranks_claims WHERE claim_date = ?", (f"top_reward_{month_str}",),
                              fetch=True)

            if not already:
                await distribute_all_top_rewards(bot)
                db_exec("INSERT INTO user_ranks_claims (user_id, claim_date) VALUES (?, ?)",
                        (0, f"top_reward_{month_str}"))
                db_exec("UPDATE users SET wins = 0")  # Автоматический сброс ТОПА после выдачи

        await asyncio.sleep(60)
