import os
import asyncio
import logging
import sqlite3
import random
import calendar
from datetime import datetime, timedelta, timezone
from html import escape

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
                        NORMAL_PASS, ROYALE_PASS, is_divine,
                        AWAKENED_SKIN, ABSOLUTE_SKIN,
                        COPY_STYLE, RISE_STYLE, BERSERK_STYLE, SPACE_STYLE, PIERCE_STYLE, EVADE_STYLE)
from database.db import (db_exec, init_db, get_user, add_user, get_rank,
                         pull_random_card, give_card_to_user, is_premium,
                         stash_card, unstash_card, get_stash, get_active_skin,
                         add_pass_xp, check_and_update_quests)
from handlers import (router, TradeState, SettingsState, PromoState,
                      MATCH_QUEUE, GAMES, PENDING_TRADES, kb_main)
from media_cache import send_cached_video
import handlers as _handlers

# ================= БЕЗОПАСНАЯ МИГРАЦИЯ ДЛЯ СТРИКА =================
try:
    db_exec("ALTER TABLE users ADD COLUMN current_streak INTEGER DEFAULT 0")
except:
    pass
# ==================================================================

# ============ БОЕВКА ============
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import asyncio

def get_card_media_info(uid: int, cid: str, base_card: dict):
    """Определяет, какой медиафайл (арт/видео/скин) нужно показать для карты."""
    if uid <= 0:  # Для бота
        active_skin = None
    else:
        active_skin = get_active_skin(uid, cid)

    is_video = False
    asset_path = ""
    skin_label = ""

    if active_skin == "awakened" and cid in AWAKENED_SKIN:
        asset_path = f"images/cards/{AWAKENED_SKIN[cid]['skin_art_file']}"
        skin_label = " 💠"
    elif active_skin == "absolute" and cid in ABSOLUTE_SKIN:
        asset_path = f"images/cards/{ABSOLUTE_SKIN[cid]['skin_video_file']}"
        is_video = True
        skin_label = " 🔮"
    else:
        if ("Божественная" in base_card.get('rarity', '') or is_divine(base_card)) and base_card.get("video"):
            asset_path = f"images/cards/{base_card['video']}"
            is_video = True
        else:
            asset_path = f"images/cards/{base_card['file']}"

    return asset_path, is_video, skin_label

class BattleState(StatesGroup):
    waiting_for_friend_id = State()

class CraftState(StatesGroup):
    waiting_for_item = State()
    confirm_craft = State()
    choosing_slot = State()

class DiamondExchangeState(StatesGroup):
    entering_amount = State()

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
           f"━━━━━━━━━━━━━━━\n"
           f"Каждое сражение фиксируется в хронике данных.\n\n"
           f"<tg-emoji emoji-id='5267267636055520629'>👁️</tg-emoji> [Гайд по битвам](https://telegra.ph/Gajd-Pole-Bitvy-07-29)")

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


@router.message(Command("add_wins"))
async def admin_add_wins(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return

    args = msg.text.split()
    if len(args) != 3:
        return await msg.answer("❌ Использование: /add_wins <id_игрока> <количество>")

    try:
        target_id = int(args[1])
        amount = int(args[2])
    except ValueError:
        return await msg.answer("❌ ID игрока и количество должны быть числами!")

    db_exec("UPDATE users SET wins = wins + ?, season_wins = season_wins + ? WHERE id = ?", (amount, amount, target_id))

    await msg.answer(f"✅ <b>Успешно!</b>\nИгроку <code>{target_id}</code> тихо начислено <b>{amount}</b> побед.",
                     parse_mode="HTML")


@router.message(Command("add_points"))
async def admin_add_points(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return

    args = msg.text.split()
    if len(args) != 3:
        return await msg.answer("❌ Использование: /add_points <id_игрока> <количество>")

    try:
        target_id = int(args[1])
        amount = int(args[2])
    except ValueError:
        return await msg.answer("❌ ID игрока и количество должны быть числами!")

    db_exec("UPDATE users SET rank_points = rank_points + ? WHERE id = ?", (amount, target_id))

    await msg.answer(f"✅ <b>Успешно!</b>\nИгроку <code>{target_id}</code> тихо начислено <b>{amount}</b> очков ранга.",
                     parse_mode="HTML")


@router.message(Command("reset_cd"))
async def admin_reset_cd(msg: types.Message):
    # Проверка на админа
    if msg.from_user.id not in ADMIN_IDS:
        return

    args = msg.text.split()
    target_id = msg.from_user.id  # По умолчанию сбрасываем себе

    # Если передан ID игрока
    if len(args) > 1:
        try:
            target_id = int(args[1])
        except ValueError:
            return await msg.answer("❌ ID игрока должен быть числом!\nИспользование: <code>/reset_cd [ID]</code>",
                                    parse_mode="HTML")

    # Сбрасываем таймер в базе данных на 2000 год (чтобы КД 100% прошел)
    db_exec("UPDATE users SET last_battle = '2000-01-01 00:00:00' WHERE id = ?", (target_id,))

    # Красивое уведомление
    if target_id == msg.from_user.id:
        await msg.answer("✅ <b>Твой кулдаун на битву успешно сброшен!</b>\nМожешь снова искать противника ⚔️",
                         parse_mode="HTML")
    else:
        await msg.answer(f"✅ <b>Успешно!</b>\nКулдаун на битву сброшен для игрока <code>{target_id}</code> ⚔️",
                         parse_mode="HTML")


import base64
from urllib.parse import quote

# Хранилище заявок на бой
PENDING_FRIENDLY_BATTLES = {}


@router.callback_query(F.data == "friendly_match_start")
async def friendly_match_start(cq: CallbackQuery, bot: Bot):
    bot_info = await bot.get_me()

    # Делаем ссылку уникальной и безопасной (прячем ID в Base64)
    b64_id = base64.urlsafe_b64encode(str(cq.from_user.id).encode()).decode().rstrip('=')
    link = f"https://t.me/{bot_info.username}?start=btl_{b64_id}"

    txt = (
        "<tg-emoji emoji-id='5454172148782359440'>🗡️</tg-emoji> Скидывай ссылку в чат или друзьям и начните свое дружеское сражение, копируй ссылку или пересылай по кнопке <tg-emoji emoji-id='5231102735817918643'>👇</tg-emoji>\n\n"
        f"<code>{link}</code>"
    )

    # Идеальный текст без плюсиков
    share_text = "👆Готов к битве со мной? Тогда переходи по ссылке выше 🗡️"
    share_url = f"https://t.me/share/url?url={quote(link)}&text={quote(share_text)}"

    bld = InlineKeyboardBuilder()
    bld.button(text="Отправить в чат 📨", url=share_url)
    bld.button(text="Назад 🔙", callback_data="b_menu_back")
    bld.adjust(1)

    try:
        await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        try:
            await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        except:
            pass
    await cq.answer()


@router.callback_query(F.data.startswith("f_acc:"))
async def friendly_accept(cq: CallbackQuery):
    req_key = cq.data.split(":")[1]
    req = PENDING_FRIENDLY_BATTLES.get(req_key)

    if not req or req["status"] != "pending":
        await cq.message.edit_text("⏳ Заявка на бой устарела или была отозвана.", reply_markup=None)
        return await cq.answer("Неактуально!", show_alert=True)

    req_time = int(req_key.split("_")[1])
    if int(datetime.now().timestamp()) - req_time > 60:
        req["status"] = "expired"
        await cq.message.edit_text("⏳ Заявка на бой истекла (прошла 1 минута).", reply_markup=None)
        return await cq.answer("Истекло время!", show_alert=True)

    host_id = req["host_id"]
    challenger_id = req["challenger_id"]

    if host_id != cq.from_user.id:
        return await cq.answer("Это не ваша заявка!", show_alert=True)

    # --- VERIFY HOST ---
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ?", (host_id,), fetchall=True)
    if len(deck) != 6:
        return await cq.answer("У вас не собрана колода!", show_alert=True)

    u_host = get_user(host_id)
    last_b = datetime.strptime(u_host[12], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    cd_hours_host = 0.5 if is_premium(host_id) else BATTLE_COOLDOWN_HOURS
    if (now - last_b).total_seconds() < cd_hours_host * 3600:
        rem = int(cd_hours_host * 3600 - (now - last_b).total_seconds())
        return await cq.answer(f"У вас кулдаун битвы: {rem // 3600}ч {(rem % 3600) // 60}м", show_alert=True)

    # --- VERIFY CHALLENGER ---
    u_chall = get_user(challenger_id)
    last_b_c = datetime.strptime(u_chall[12], "%Y-%m-%d %H:%M:%S")
    cd_hours_chall = 0.5 if is_premium(challenger_id) else BATTLE_COOLDOWN_HOURS
    if (now - last_b_c).total_seconds() < cd_hours_chall * 3600:
        return await cq.answer("У инициатора боя сейчас кулдаун.", show_alert=True)

    req["status"] = "accepted"
    await cq.message.edit_text("✅ <b>Вызов принят!</b> Бой начинается.", parse_mode="HTML", reply_markup=None)

    try:
        await cq.bot.edit_message_text(
            "✅ <b>Противник принял вызов!</b> Бой начинается.",
            chat_id=challenger_id, message_id=req["chall_msg_id"], parse_mode="HTML", reply_markup=None
        )
    except:
        pass

    await start_battle(host_id, challenger_id, cq.bot, friendly=True)
    await cq.answer()


@router.callback_query(F.data.startswith("f_dec:"))
async def friendly_decline(cq: CallbackQuery):
    req_key = cq.data.split(":")[1]
    req = PENDING_FRIENDLY_BATTLES.get(req_key)

    if not req or req["status"] != "pending":
        await cq.message.edit_text("⏳ Заявка на бой устарела или была отозвана.", reply_markup=None)
        return await cq.answer()

    req["status"] = "declined"
    await cq.message.edit_text("❌ Вы отклонили вызов.", reply_markup=None)

    try:
        await cq.bot.edit_message_text(
            "❌ Противник отклонил ваш вызов.",
            chat_id=req["challenger_id"], message_id=req["chall_msg_id"], reply_markup=None
        )
    except:
        pass
    await cq.answer()


@router.callback_query(F.data.startswith("f_cancel:"))
async def friendly_cancel(cq: CallbackQuery):
    req_key = cq.data.split(":")[1]
    req = PENDING_FRIENDLY_BATTLES.get(req_key)

    if not req or req["status"] != "pending":
        await cq.message.edit_text("⏳ Заявка уже обработана или истекла.", reply_markup=None)
        return await cq.answer()

    if req["challenger_id"] != cq.from_user.id:
        return await cq.answer("Это не ваша заявка!", show_alert=True)

    req["status"] = "cancelled"
    await cq.message.edit_text("❌ Вы отозвали вызов.", reply_markup=None)

    if "host_msg_id" in req:
        try:
            await cq.bot.edit_message_text(
                "❌ Противник отозвал свой вызов.",
                chat_id=req["host_id"], message_id=req["host_msg_id"], reply_markup=None
            )
        except:
            pass
    await cq.answer()


# ============ СИСТЕМА УПРАВЛЕНИЯ КОЛОДАМИ (ОБНОВЛЕННАЯ) ============

@router.callback_query(F.data == "my_deck")
async def my_deck_menu(cq: CallbackQuery):
    """Главный вход в меню колод с проверкой на минимальное количество карт"""
    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if len(cards) < 10:
        return await cq.answer("❌ Нужно получить минимум 10 боевых карт, чтобы открыть этот раздел!", show_alert=True)

    await show_multi_deck_main(cq, cq.from_user.id)
    await cq.answer()


@router.callback_query(F.data == "view_deck")
async def view_deck(cq: CallbackQuery):
    """Показ карт текущей активной колоды с учётом установленных скинов"""
    uid = cq.from_user.id
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ? ORDER BY slot_index", (uid,), fetchall=True)
    if len(deck) != 6:
        return await cq.answer("Колода не собрана полностью!", show_alert=True)

    rarity_order = {"Божественная ⚫️": 6, "Мифическая 🔴": 5, "Легендарная 🔵": 4, "Эпическая 🟢": 3, "Редкая 🟡": 2,
                    "Обычная ⚪️": 1}
    c_objs = [(cid, CARDS[cid]) for (cid,) in deck if cid in CARDS]
    c_objs.sort(key=lambda x: rarity_order.get(x[1]['rarity'], 0), reverse=True)

    media = []
    for i, (cid, c) in enumerate(c_objs):
        # Получаем пути к медиа с учётом надетого скина (арта/видео/значка)
        asset_path, is_video, skin_label = get_card_media_info(uid, cid, c)
        txt_card = f"{i + 1}. {c['name']}{skin_label} ({c['rarity']})\n⚡️{c['speed']} | 💪{c['strength']} | 🧠{c['intellect']}"

        if is_video:
            media.append(types.InputMediaVideo(
                media=FSInputFile(asset_path),
                caption=txt_card,
                width=960,
                height=1280,
                supports_streaming=True
            ))
        else:
            media.append(types.InputMediaPhoto(
                media=FSInputFile(asset_path),
                caption=txt_card
            ))

    try:
        await cq.message.answer_media_group(media=media)
    except Exception as e:
        logging.error(f"Failed to send deck media group to {uid}: {e}")
        await cq.answer("❌ Ошибка при отображении колоды.", show_alert=True)
        return

    await cq.answer()


@router.callback_query(F.data == "auto_deck")
async def auto_deck(cq: CallbackQuery):
    """Автосбор лучших карт строго для ВЫБРАННОЙ (активной) колоды"""
    uid = cq.from_user.id

    active_deck = db_exec("SELECT deck_id FROM multi_decks WHERE user_id = ? AND is_active = 1", (uid,), fetch=True)
    if not active_deck:
        return await cq.answer("❌ Сначала создайте колоду ниже и нажмите «✅ Выбрать», чтобы применить к ней автосбор!",
                               show_alert=True)

    deck_id = active_deck[0]

    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (uid,), fetchall=True)
    if len(cards) < 6:
        return await cq.answer("Для колоды нужно минимум 6 карт в инвентаре!", show_alert=True)

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
        if len(new_deck) == 6:
            break
        if "Мифическая" in c['r'] or "Божественная" in c['r']:
            if mythic_divine >= 1:
                continue
            mythic_divine += 1
        elif "Легендарная" in c['r']:
            if leg >= 2:
                continue
            leg += 1
        new_deck.append(c['id'])

    if len(new_deck) < 6:
        return await cq.answer("Не удалось собрать 6 карт из-за ограничений редкости предметов.", show_alert=True)

    db_exec("DELETE FROM multi_deck_slots WHERE deck_id = ?", (deck_id,))
    for i, cid in enumerate(new_deck):
        db_exec("INSERT INTO multi_deck_slots (deck_id, slot_index, card_id) VALUES (?, ?, ?)", (deck_id, i + 1, cid))

    sync_active_deck(uid, deck_id)

    await cq.answer("✅ Выбранная колода автоматически заполнена лучшими картами!", show_alert=True)
    await show_multi_deck_main(cq, uid)


class MultiDeckState(StatesGroup):
    waiting_for_deck_name = State()
    waiting_for_deck_rename = State()


def ensure_multi_deck_tables():
    db_exec('''CREATE TABLE IF NOT EXISTS multi_decks (
        deck_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        is_active INTEGER DEFAULT 0
    )''')
    try:
        db_exec("ALTER TABLE multi_decks ADD COLUMN is_active INTEGER DEFAULT 0")
    except Exception:
        pass
    db_exec('''CREATE TABLE IF NOT EXISTS multi_deck_slots (
        deck_id INTEGER,
        slot_index INTEGER,
        card_id TEXT
    )''')


def sync_active_deck(user_id, deck_id):
    db_exec("DELETE FROM decks WHERE user_id = ?", (user_id,))
    slots = db_exec("SELECT slot_index, card_id FROM multi_deck_slots WHERE deck_id = ?", (deck_id,), fetchall=True)
    for slot_index, card_id in slots:
        db_exec("INSERT INTO decks (user_id, card_id, slot_index) VALUES (?, ?, ?)", (user_id, card_id, slot_index - 1))


def _get_multi_deck_main_ui(user_id: int):
    """Генерирует текст и кнопки для главного меню колод"""
    ensure_multi_deck_tables()
    decks = db_exec("SELECT deck_id, name, is_active FROM multi_decks WHERE user_id = ?", (user_id,), fetchall=True)

    bld = InlineKeyboardBuilder()

    for d in decks:
        did, dname, is_active = d
        if is_active:
            # Если колода активна, показываем галочку и заглушку "Активна"
            bld.row(
                InlineKeyboardButton(text=f"{dname} ✅", callback_data=f"mdeck_view:{did}"),
                InlineKeyboardButton(text="🟢 Активна", callback_data="ignore")
            )
        else:
            bld.row(
                InlineKeyboardButton(text=f"{dname}", callback_data=f"mdeck_view:{did}"),
                InlineKeyboardButton(text="✅ Выбрать", callback_data=f"mdeck_select:{did}")
            )

    if len(decks) < 2:
        bld.row(InlineKeyboardButton(text="Добавить колоду 🆕", callback_data="mdeck_add"))

    bld.row(
        InlineKeyboardButton(text="Посмотреть колоду 🃏", callback_data="view_deck"),
        InlineKeyboardButton(text="Автосбор 🔁", callback_data="auto_deck")
    )
    bld.row(
        InlineKeyboardButton(text="📦 Сундук", callback_data="stash_menu:0:deck"),
        InlineKeyboardButton(text="Назад 🔙", callback_data="b_menu_back")
    )

    text = (
        "🎴 <b>Здесь место для ваших колод</b>\n\n"
        "Можно иметь лишь две колоды. Выберите колоду и нажмите «✅ Выбрать», чтобы сделать её <b>активной</b> для боёв.\n\n"
        "Нажмите на название колоды, чтобы редактировать её."
    )
    return text, bld.as_markup()


async def show_multi_deck_main(target, user_id):
    """Главный экран хаба колод с умным редактированием (без спама)"""
    text, markup = _get_multi_deck_main_ui(user_id)

    if isinstance(target, CallbackQuery):
        try:
            # Пытаемся просто изменить текст (сработает, если мы уже в текстовом меню)
            await target.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            try:
                # Если прошлое сообщение было с картинкой, edit_text не сработает.
                # Поэтому удаляем старое сообщение с картинкой и отправляем новое текстовое.
                await target.message.delete()
            except Exception:
                pass
            await target.message.answer(text, reply_markup=markup, parse_mode="HTML")

    elif isinstance(target, types.Message):
        await target.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "manual_deck_start")
async def manual_deck_start(cq: CallbackQuery):
    await show_multi_deck_main(cq, cq.from_user.id)
    await cq.answer()


@router.callback_query(F.data.startswith("mdeck_select:"))
async def mdeck_select_cb(cq: CallbackQuery):
    deck_id = int(cq.data.split(":")[1])
    uid = cq.from_user.id

    deck = db_exec("SELECT deck_id FROM multi_decks WHERE deck_id = ? AND user_id = ?", (deck_id, uid), fetch=True)
    if not deck:
        return await cq.answer("Колода не найдена!", show_alert=True)

    db_exec("UPDATE multi_decks SET is_active = 0 WHERE user_id = ?", (uid,))
    db_exec("UPDATE multi_decks SET is_active = 1 WHERE deck_id = ?", (deck_id,))
    sync_active_deck(uid, deck_id)

    await cq.answer("✅ Колода выбрана как активная!", show_alert=True)
    await show_multi_deck_main(cq, uid)


@router.callback_query(F.data == "mdeck_add")
async def mdeck_add_cb(cq: CallbackQuery, state: FSMContext):
    decks = db_exec("SELECT deck_id FROM multi_decks WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if len(decks) >= 2:
        return await cq.answer("Максимум 2 колоды!", show_alert=True)

    bld = InlineKeyboardBuilder()
    bld.button(text="Отменить ❌", callback_data="mdeck_cancel_add")

    await cq.message.edit_text("🗞️ <b>Введите название для колоды</b> (максимум 10 символов):",
                               reply_markup=bld.as_markup(), parse_mode="HTML")

    await state.set_state(MultiDeckState.waiting_for_deck_name)
    # Запоминаем ID сообщения, чтобы отредактировать его после ввода текста
    await state.update_data(prompt_msg_id=cq.message.message_id)


@router.callback_query(F.data == "mdeck_cancel_add")
async def mdeck_cancel_add_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_multi_deck_main(cq, cq.from_user.id)
    await cq.answer()


@router.message(MultiDeckState.waiting_for_deck_name)
async def mdeck_name_entered(msg: types.Message, state: FSMContext):
    name = msg.text.strip()

    # Удаляем сообщение пользователя, чтобы не засорять чат
    try:
        await msg.delete()
    except Exception:
        pass

    if len(name) > 10:
        sent = await msg.answer("⚠️ Название должно быть не более 10 символов! Попробуйте еще раз.")
        await asyncio.sleep(3)
        try:
            await sent.delete()
        except:
            pass
        return

    db_exec("INSERT INTO multi_decks (user_id, name) VALUES (?, ?)", (msg.from_user.id, name))

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    await state.clear()

    # Если мы сохранили ID меню бота, редактируем его
    if prompt_msg_id:
        text, markup = _get_multi_deck_main_ui(msg.from_user.id)
        try:
            await msg.bot.edit_message_text(text, chat_id=msg.chat.id, message_id=prompt_msg_id, reply_markup=markup,
                                            parse_mode="HTML")
            return
        except Exception:
            pass

    # Фолбек, если отредактировать не вышло
    await show_multi_deck_main(msg, msg.from_user.id)


@router.callback_query(F.data.startswith("mdeck_view:"))
async def mdeck_view_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()  # На случай, если игрок отменил переименование

    deck_id = int(cq.data.split(":")[1])
    deck = db_exec("SELECT name FROM multi_decks WHERE deck_id = ? AND user_id = ?", (deck_id, cq.from_user.id),
                   fetch=True)
    if not deck:
        return await cq.answer("Колода не найдена!", show_alert=True)
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

    try:
        await cq.message.edit_text(text, reply_markup=bld.as_markup())
    except Exception:
        pass


@router.callback_query(F.data.startswith("mdeck_rename:"))
async def mdeck_rename_cb(cq: CallbackQuery, state: FSMContext):
    deck_id = int(cq.data.split(":")[1])
    await state.update_data(rename_deck_id=deck_id, prompt_msg_id=cq.message.message_id)
    bld = InlineKeyboardBuilder()
    bld.button(text="Отменить ❌", callback_data=f"mdeck_view:{deck_id}")
    await cq.message.edit_text("🗞️ <b>Введите новое название для колоды</b> (максимум 10 символов):",
                               reply_markup=bld.as_markup(), parse_mode="HTML")
    await state.set_state(MultiDeckState.waiting_for_deck_rename)


@router.message(MultiDeckState.waiting_for_deck_rename)
async def mdeck_renamed(msg: types.Message, state: FSMContext):
    name = msg.text.strip()

    try:
        await msg.delete()
    except Exception:
        pass

    if len(name) > 10:
        sent = await msg.answer("⚠️ Максимум 10 символов! Попробуйте еще раз.")
        await asyncio.sleep(3)
        try:
            await sent.delete()
        except:
            pass
        return

    data = await state.get_data()
    deck_id = data.get('rename_deck_id')
    prompt_msg_id = data.get('prompt_msg_id')

    db_exec("UPDATE multi_decks SET name = ? WHERE deck_id = ? AND user_id = ?", (name, deck_id, msg.from_user.id))
    await state.clear()

    if prompt_msg_id:
        text, markup = _get_multi_deck_main_ui(msg.from_user.id)
        try:
            await msg.bot.edit_message_text(text, chat_id=msg.chat.id, message_id=prompt_msg_id, reply_markup=markup,
                                            parse_mode="HTML")
            return
        except Exception:
            pass

    await show_multi_deck_main(msg, msg.from_user.id)


@router.callback_query(F.data.startswith("mdeck_del:"))
async def mdeck_del_cb(cq: CallbackQuery):
    deck_id = int(cq.data.split(":")[1])
    db_exec("DELETE FROM multi_decks WHERE deck_id = ? AND user_id = ?", (deck_id, cq.from_user.id))
    db_exec("DELETE FROM multi_deck_slots WHERE deck_id = ?", (deck_id,))
    await cq.answer("Колода удалена!")
    await show_multi_deck_main(cq, cq.from_user.id)


@router.callback_query(F.data.startswith("mdeck_edit:"))
async def mdeck_edit_cb(cq: CallbackQuery):
    deck_id = int(cq.data.split(":")[1])
    await show_mdeck_slots(cq, deck_id)


async def show_mdeck_slots(cq: CallbackQuery, deck_id: int):
    deck = db_exec("SELECT name FROM multi_decks WHERE deck_id = ? AND user_id = ?", (deck_id, cq.from_user.id),
                   fetch=True)
    if not deck: return
    deck_name = deck[0]

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
            spd = str_ = int_ = 0
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

    try:
        await cq.message.edit_text(text, reply_markup=bld.as_markup())
    except Exception:
        pass


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

    try:
        await cq.message.edit_text(text, reply_markup=bld.as_markup())
    except Exception:
        pass


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

    avail.sort(key=lambda cid: (CARDS[cid]['speed'] + CARDS[cid]['strength'] + CARDS[cid]['intellect']), reverse=True)

    items_per_page = 10
    total_pages = (len(avail) + items_per_page - 1) // items_per_page
    if page >= total_pages:
        page = max(0, total_pages - 1)

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

    try:
        await cq.message.edit_text(f"Выберите карту ({rarity}):", reply_markup=bld.as_markup())
    except Exception:
        pass


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
        # Если игрока нет в очереди, значит он либо нажал отмену, либо его забрал реальный игрок
        if uid not in MATCH_QUEUE:
            # Проверяем, находится ли игрок сейчас в активном бою
            in_game = any(g['p1'] == uid or g['p2'] == uid for g in GAMES.values())
            if in_game:
                try:
                    await msg_to_edit.delete()
                except:
                    pass
            return  # Тихо завершаем процесс

    # Если прошло 50 секунд и игрок все еще в очереди - даем ему бота
    if uid in MATCH_QUEUE:
        MATCH_QUEUE.remove(uid)
        try:
            await msg_to_edit.delete()
        except:
            pass
        await start_battle(uid, -1, bot)


async def start_battle(p1, p2, bot: Bot, friendly=False):
    gid = f"g_{random.randint(10000, 99999)}"
    deck1 = [c[0] for c in db_exec("SELECT card_id FROM decks WHERE user_id = ?", (p1,), fetchall=True)]

    # Получаем данные первого игрока и формируем строчку титула (если он есть)
    u1 = get_user(p1)
    title1_val = u1[14] if u1 else None
    title1_str = TITLES.get(title1_val) if title1_val else None
    title_line1 = f"· Титул: {title1_str}\n" if title1_str else ""

    if p2 == -1:
        # --- ИСПРАВЛЕНИЕ: БОТ ИСПОЛЬЗУЕТ КОЛОДЫ РЕАЛЬНЫХ ИГРОКОВ ---
        # Ищем всех пользователей, у которых в колоде ровно 6 карт
        valid_users = db_exec("SELECT user_id FROM decks GROUP BY user_id HAVING COUNT(card_id) = 6", fetchall=True)
        valid_uids = [u[0] for u in valid_users]

        if valid_uids:
            # Стараемся не давать боту колоду самого игрока, если есть другие варианты
            if p1 in valid_uids and len(valid_uids) > 1:
                valid_uids.remove(p1)

            chosen_uid = random.choice(valid_uids)
            deck2 = [c[0] for c in db_exec("SELECT card_id FROM decks WHERE user_id = ?", (chosen_uid,), fetchall=True)]
        else:
            # Фоллбек: если в базе вообще нет собранных колод, генерируем случайную (без дубликатов)
            all_cards = list(CARDS.keys())
            deck2 = random.sample(all_cards, k=min(6, len(all_cards)))

        name2 = random.choice([
            "Важни Гий", "Ли Джи Ху..", "Йена пик форма", "Злодей Васко",
            "Великий Мага", "Босс Табаско", "Брад", "Клон Хикса", "Король Бибизян",
            "Пак Хён Сок", "Сон Джин У", "Йегер Не..", "Ю Джунхёк",
            "Чхве Дон Су", "Кан Даниэль", "Баек Юн Хо", "Скрытый Герой",
            "Мастер Вжух", "Дед Инсайд", "Тапок Справедливости", "Гигачад",
            "Мамин Киберспортсмен", "Шаурмичный Лорд", "Неуловимый Джо",
            "Котлетка Ниндзя", "Капибара Киллер", "Агро Школьник",
            "Пельмень Судьбы", "Безумный Фармер", "Хлебный Мякиш",
            "Скрытый ге..", "Toxic Хантер", "Noob Slayer", "Shadow Fiend",
            "Solo Player", "Cyber Ninja", "Dark Samurai", "ProGamer_2026",
            "Киберкотлета", "Тёмный Властелин", "Азиат"
        ])
        rank2 = "Бот"
        title_line2 = ""  # У ботов титулов никогда нет, строка пустая
    else:
        deck2 = [c[0] for c in db_exec("SELECT card_id FROM decks WHERE user_id = ?", (p2,), fetchall=True)]
        u2 = get_user(p2)
        name2, rank2 = f"<a href='tg://user?id={p2}'>{u2[2]}</a>", get_rank(u2[7])

        # Получаем данные второго игрока и формируем строчку титула (если он есть)
        title2_val = u2[14] if u2 else None
        title2_str = TITLES.get(title2_val) if title2_val else None
        title_line2 = f"· Титул: {title2_str}\n" if title2_str else ""

    GAMES[gid] = {
        'p1': p1, 'p2': p2, 'd1': deck1.copy(), 'd2': deck2.copy(), 'n2': name2, 'r2': rank2,
        'p1_c': None, 'p2_c': None, 'p1_s': None, 'p2_s': None, 'score1': 0, 'score2': 0, 'round': 1,
        'friendly': friendly, 'resolving': False,
        # === НОВЫЕ ТРЕКЕРЫ ДЛЯ НАВЫКОВ ===
        'p1_skill_uses': 2, 'p2_skill_uses': 2,  # Лимит 2 ульты за бой
        'p1_use_skill': False, 'p2_use_skill': False,  # Юзает ли ульту в ТЕКУЩЕМ раунде
        'p1_next_debuff': 0, 'p2_next_debuff': 0,  # Штраф от Берсерка на СЛЕДУЮЩИЙ раунд
        'p1_copy_used': False, 'p2_copy_used': False  # Трекер пассивки Копирования
    }

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

    # Вставляем динамическую строку титула {title_line2}
    txt1 = f"Противник найден!\n\n· Имя: {name2} {emoji2}\n{title_line2}· Ранг: {rank2}\n· Награда: {pts1_txt}🏅, {bc1_txt} BattleCoin 🪙\n\nБитва начинается!"

    try:
        if p2 != -1:
            bg_key2 = u2[13] or 'default'
            bg_data2 = BGS.get(bg_key2, BGS['default'])
            bg_file2 = FSInputFile(f"images/backgrounds/{bg_data2.get('file')}")
            try:
                if bg_key2 in VIDEO_BGS:
                    await send_cached_video(
                        bot, chat_id=p1, file_path=f"images/backgrounds/{bg_data2.get('file')}",
                        caption=txt1, parse_mode="HTML", supports_streaming=True,
                        width=bg_data2.get('width'), height=bg_data2.get('height')
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

            # Вставляем динамическую строку титула {title_line1}
            txt2 = f"Противник найден!\n\n· Имя: <a href='tg://user?id={p1}'>{u1[2]}</a> {emoji1}\n{title_line1}· Ранг: {get_rank(u1[7])}\n· Награда: {pts2_txt}🏅, {bc2_txt} BattleCoin 🪙\n\nБитва начинается!"

            bg_key1 = u1[13] or 'default'
            bg_data1 = BGS.get(bg_key1, BGS['default'])
            bg_file1 = FSInputFile(f"images/backgrounds/{bg_data1.get('file')}")
            try:
                if bg_key1 in VIDEO_BGS:
                    await send_cached_video(
                        bot, chat_id=p2, file_path=f"images/backgrounds/{bg_data1.get('file')}",
                        caption=txt2, parse_mode="HTML", supports_streaming=True,
                        width=bg_data1.get('width'), height=bg_data1.get('height')
                    )
                else:
                    await bot.send_photo(p2, photo=bg_file1, caption=txt2, parse_mode="HTML")
            except:
                await bot.send_message(p2, txt2, parse_mode="HTML")

        await asyncio.sleep(1)
        await send_card_choice(p1, GAMES[gid]['d1'], gid, bot)
        if p2 != -1:
            await send_card_choice(p2, GAMES[gid]['d2'], gid, bot)

    except Exception as e:
        logging.error(f"Failed to start battle {gid} properly: {e}")
        GAMES.pop(gid, None)
        try:
            await bot.send_message(p1, "⚠️ Ошибка инициализации битвы. Противник или сервер недоступен.")
            if p2 != -1:
                await bot.send_message(p2, "⚠️ Ошибка инициализации битвы. Противник или сервер недоступен.")
        except:
            pass



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
        # Если время вышло, бот бьет обычной атакой (is_skill = False)
        await process_style_choice(gid, uid, random_style, False, bot)


def get_card_skill_info(card_id: str):
    """Возвращает название и эмодзи навыка карты."""
    if card_id in COPY_STYLE: return "👁️ Копирование"
    if card_id in RISE_STYLE: return "🌑 Восстание"
    if card_id in BERSERK_STYLE: return "🩸 Берсерк"
    if card_id in SPACE_STYLE: return "🌊 Пространство"
    if card_id in PIERCE_STYLE: return "⚔️ Пробивание"
    if card_id in EVADE_STYLE: return "🌪 Уклонение"
    return None


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

    card_data = CARDS[card]
    asset_path, is_video, skin_label = get_card_media_info(uid, card, card_data)

    # Определяем наличие ульты и её название
    skill_uses = g['p1_skill_uses'] if is_p1 else g['p2_skill_uses']
    skill_name = get_card_skill_info(card)

    is_passive_copy = (card in COPY_STYLE)
    has_active_skill = card in (RISE_STYLE + BERSERK_STYLE + SPACE_STYLE + PIERCE_STYLE + EVADE_STYLE)

    bld = InlineKeyboardBuilder()
    # 0 - обычный удар, 1 - ульта
    if has_active_skill and skill_uses > 0:
        bld.row(
            InlineKeyboardButton(text="⚡️ Скорость", callback_data=f"b_style:{gid}:spd:0"),
            InlineKeyboardButton(text=f"💥 Ульта ({skill_uses})", callback_data=f"b_style:{gid}:spd:1")
        )
        bld.row(
            InlineKeyboardButton(text="💪 Сила", callback_data=f"b_style:{gid}:str:0"),
            InlineKeyboardButton(text=f"💥 Ульта ({skill_uses})", callback_data=f"b_style:{gid}:str:1")
        )
        bld.row(
            InlineKeyboardButton(text="🧠 Интеллект", callback_data=f"b_style:{gid}:int:0"),
            InlineKeyboardButton(text=f"💥 Ульта ({skill_uses})", callback_data=f"b_style:{gid}:int:1")
        )
        skill_text = (
            f"<blockquote>✨ <b>Навык карты: {skill_name}</b>\n"
            f"💥 Доступно для применения: <b>{skill_uses}/2</b></blockquote>\n\n"
        )
    else:
        bld.row(InlineKeyboardButton(text="⚡️ Скорость", callback_data=f"b_style:{gid}:spd:0"))
        bld.row(InlineKeyboardButton(text="💪 Сила", callback_data=f"b_style:{gid}:str:0"))
        bld.row(InlineKeyboardButton(text="🧠 Интеллект", callback_data=f"b_style:{gid}:int:0"))

        # Информируем о пассивке Копирования
        if is_passive_copy:
            copy_used = g.get('p1_copy_used', False) if is_p1 else g.get('p2_copy_used', False)
            if not copy_used:
                skill_text = (
                    f"<blockquote>✨ <b>Навык карты: {skill_name} (Пассивный)</b>\n"
                    f"🌀 Сработает автоматически 1 раз за бой!</blockquote>\n\n"
                )
            else:
                skill_text = f"<blockquote>✨ <b>Навык карты: {skill_name}</b>\n❌ Уже использован в этом бою.</blockquote>\n\n"
        else:
            skill_text = ""

    txt = (
        f"🃏 Выбрана карта: <b>{card_data['name']}{skin_label}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⚔️ Выберите Атаку:\n\n"
        f"{skill_text}"
        f"⏳ На выбор дается 30 секунд"
    )

    msg = None
    try:
        if is_video:
            msg = await send_cached_video(
                bot, chat_id=uid, file_path=asset_path, caption=txt,
                width=960, height=1280, parse_mode="HTML",
                reply_markup=bld.as_markup(), supports_streaming=True
            )
        else:
            msg = await bot.send_photo(
                uid, photo=FSInputFile(asset_path), caption=txt, parse_mode="HTML", reply_markup=bld.as_markup()
            )
    except Exception as e:
        logging.error(f"send card media failed for {uid}, card={card}: {e}")
        try:
            msg = await bot.send_message(uid, txt, parse_mode="HTML", reply_markup=bld.as_markup())
        except Exception as e2:
            logging.error(f"fallback send_message failed for {uid}: {e2}")

    if msg is not None:
        current_round = g['round']
        asyncio.create_task(auto_style_choice(gid, uid, current_round, msg.message_id, bot))

    # Логика бота
    if g['p2'] == -1 and g['p2_c'] is None:
        bot_c = random.choice(g['d2'])
        g['p2_c'] = bot_c
        g['d2'].remove(bot_c)
        g['p2_s'] = random.choice(['spd', 'str', 'int'])
        g['p2_use_skill'] = False

        if g['p1_s'] and g['p2_s']:
            if not g.get('resolving'):
                g['resolving'] = True
                try:
                    await resolve_round(gid, bot)
                except Exception as e:
                    logging.error(f"resolve_round failed (bot path): {e}")
                    if gid in GAMES:
                        GAMES[gid]['round'] += 1
                        GAMES[gid]['p1_c'] = GAMES[gid]['p2_c'] = GAMES[gid]['p1_s'] = GAMES[gid]['p2_s'] = None
                        try:
                            await bot.send_message(GAMES[gid]['p1'],
                                                   "⚠️ Возникла ошибка сервера в прошлом раунде, раунд пропущен.")
                        except:
                            pass
                        if GAMES[gid]['round'] > 5:
                            await finish_game(gid, bot)
                        else:
                            await send_card_choice(GAMES[gid]['p1'], GAMES[gid]['d1'], gid, bot)

                if gid in GAMES: GAMES[gid]['resolving'] = False


async def process_style_choice(gid, uid, style, is_skill, bot):
    g = GAMES.get(gid)
    if not g: return
    if g.get('resolving'): return

    is_p1 = (uid == g['p1'])
    if is_p1:
        if g['p1_s'] is not None: return
        g['p1_s'] = style
        # Фиксируем использование навыка
        if is_skill and g['p1_skill_uses'] > 0:
            g['p1_use_skill'] = True
            g['p1_skill_uses'] -= 1
        else:
            g['p1_use_skill'] = False
    else:
        if g['p2_s'] is not None: return
        g['p2_s'] = style
        # Фиксируем использование навыка
        if is_skill and g['p2_skill_uses'] > 0:
            g['p2_use_skill'] = True
            g['p2_skill_uses'] -= 1
        else:
            g['p2_use_skill'] = False

    # БЛОКИРОВКА: Проверяем и блокируем до любых await!
    should_resolve = False
    if g['p1_s'] and g['p2_s'] and not g.get('resolving'):
        g['resolving'] = True
        should_resolve = True

    try:
        msg = await bot.send_message(uid, "Ожидание противника...")
        if is_p1:
            g['p1_wait_msg'] = msg.message_id
        else:
            g['p2_wait_msg'] = msg.message_id
    except:
        pass

    # Если именно этот вызов закрыл раунд, он же его и решает
    if should_resolve:
        try:
            if g.get('p1_wait_msg'): await bot.delete_message(g['p1'], g['p1_wait_msg'])
            if g.get('p2_wait_msg') and g['p2'] != -1: await bot.delete_message(g['p2'], g['p2_wait_msg'])
        except:
            pass

        try:
            await resolve_round(gid, bot)
        except Exception as e:
            logging.error(f"Critical error in resolve_round: {e}")
            # Fallback на случай сбоя
            if gid in GAMES:
                GAMES[gid]['round'] += 1
                GAMES[gid]['p1_c'] = GAMES[gid]['p2_c'] = GAMES[gid]['p1_s'] = GAMES[gid]['p2_s'] = None
                GAMES[gid]['p1_use_skill'] = GAMES[gid]['p2_use_skill'] = False
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

    is_p1 = (uid == g['p1'])
    debuff = g['p1_next_debuff'] if is_p1 else g['p2_next_debuff']

    c_objs = [(cid, CARDS[cid]) for cid in set(deck_left)]
    rarity_order = {"Божественная ⚫️": 6, "Мифическая 🔴": 5, "Легендарная 🔵": 4, "Эпическая 🟢": 3, "Редкая 🟡": 2,
                    "Обычная ⚪️": 1}
    c_objs.sort(key=lambda x: rarity_order.get(x[1]['rarity'], 0), reverse=True)

    media = []
    for i, (cid, c) in enumerate(c_objs):
        asset_path, is_video, skin_label = get_card_media_info(uid, cid, c)

        spd, str_val, int_val = c['speed'], c['strength'], c['intellect']

        # Если есть дебафф от Берсерка, сразу показываем игроку сниженные статы!
        if debuff != 0:
            r = c.get('rarity', '')
            min_lim = 91 if 'Божественная' in r or 'Мифическая' in r else (
                81 if 'Легендарная' in r else (60 if 'Эпическая' in r else 1))
            spd = max(min_lim, spd + debuff)
            str_val = max(min_lim, str_val + debuff)
            int_val = max(min_lim, int_val + debuff)

        txt_card = f"{i + 1}. {c['name']}{skin_label} ({c['rarity']})\n⚡️{spd} | 💪{str_val} | 🧠{int_val}"
        if is_video:
            media.append(types.InputMediaVideo(
                media=FSInputFile(asset_path),
                caption=txt_card,
                width=960, height=1280,
                supports_streaming=True
            ))
        else:
            media.append(types.InputMediaPhoto(media=FSInputFile(asset_path), caption=txt_card))

    try:
        await bot.send_media_group(uid, media=media)
    except Exception as e:
        logging.error(f"Failed to send visual deck to {uid}: {e}")

    bld = InlineKeyboardBuilder()
    for cid, c in c_objs:
        bld.button(text=c['name'], callback_data=f"b_card:{gid}:{cid}")
    bld.adjust(2)

    # Красивое предупреждение об истощении
    debuff_warning = ""
    if debuff != 0:
        debuff_warning = f"⚠️ <b>Внимание: Истощение!</b>\nВсе ваши статы временно снижены на {abs(debuff)} (до порога редкости).\n\n"

    txt = f"—————————————————\n\nРаунд {g['round']}.\n{debuff_warning}Выберите 🎴 карту для атаки\n\n⏳ На выбор дается 30 секунд"
    try:
        msg = await bot.send_message(uid, txt, reply_markup=bld.as_markup(), parse_mode="HTML")
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
    parts = cq.data.split(":")
    gid = parts[1]
    style = parts[2]
    # Читаем флаг активации ульты (0 - обычная, 1 - ульта)
    is_skill = (parts[3] == "1") if len(parts) > 3 else False

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
    await process_style_choice(gid, cq.from_user.id, style, is_skill, cq.bot)

def get_rarity_limits(card_dict):
    """Возвращает (минимум, максимум) статов для редкости карты."""
    rarity = card_dict.get('rarity', '')
    if 'Божественная' in rarity or 'Мифическая' in rarity:
        return 91, 100
    elif 'Легендарная' in rarity:
        return 81, 90
    elif 'Эпическая' in rarity:
        return 60, 80
    else:
        return 1, 100

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

    val1 = c1[s_map[g['p1_s']][2]]
    val2 = c2[s_map[g['p2_s']][2]]

    val1_base, val2_base = val1, val2

    # === MANHWCARD PASS: ЗАДАНИЯ НА СТИЛИ ===
    if g['p1'] != -1:
        if g['p1_s'] == 'spd': check_and_update_quests(g['p1'], 'q_15_style_spd', 1)
        elif g['p1_s'] == 'str': check_and_update_quests(g['p1'], 'q_15_style_str', 1)
        elif g['p1_s'] == 'int': check_and_update_quests(g['p1'], 'q_15_style_int', 1)

    if g['p2'] != -1:
        if g['p2_s'] == 'spd': check_and_update_quests(g['p2'], 'q_15_style_spd', 1)
        elif g['p2_s'] == 'str': check_and_update_quests(g['p2'], 'q_15_style_str', 1)
        elif g['p2_s'] == 'int': check_and_update_quests(g['p2'], 'q_15_style_int', 1)
    # ========================================

    skill_log_1, skill_log_2 = [], []

    # --- 1. ПРИМЕНЕНИЕ ДЕБАФФА ПРОШЛОГО БЕРСЕРКА ---
    if g.get('p1_next_debuff', 0) != 0:
        debuff = g['p1_next_debuff']
        g['p1_next_debuff'] = 0
        min1, _ = get_rarity_limits(c1)
        val1 = max(min1, val1 + debuff)
        skill_log_1.append(f"⚠️ <b>Истощение (от прошлого Берсерка):</b> Стат снижен на {abs(debuff)} (текущий: {val1})")

    if g.get('p2_next_debuff', 0) != 0:
        debuff = g['p2_next_debuff']
        g['p2_next_debuff'] = 0
        min2, _ = get_rarity_limits(c2)
        val2 = max(min2, val2 + debuff)
        skill_log_2.append(f"⚠️ <b>Истощение (от прошлого Берсерка):</b> Стат снижен на {abs(debuff)} (текущий: {val2})")

    # --- 2. ПРОВЕРКА НЕМОТИ (ПРОСТРАНСТВО) ---
    p1_is_space = (g['p1_c'] in SPACE_STYLE) and g['p1_use_skill']
    p2_is_space = (g['p2_c'] in SPACE_STYLE) and g['p2_use_skill']

    p1_skill_blocked = g['p1_use_skill'] and p2_is_space and not p1_is_space
    p2_skill_blocked = g['p2_use_skill'] and p1_is_space and not p2_is_space

    if p1_skill_blocked:
        skill_log_1.append("🚫 <b>Навык заблокирован</b> Пространством противника!")
    if p2_skill_blocked:
        skill_log_2.append("🚫 <b>Навык заблокирован</b> Пространством противника!")

    # --- 2.5 ПАССИВНОЕ КОПИРОВАНИЕ ---
    cid1, cid2 = g['p1_c'], g['p2_c']
    min1, max1 = get_rarity_limits(c1)
    min2, max2 = get_rarity_limits(c2)

    if cid1 in COPY_STYLE and not g.get('p1_copy_used', False):
        target_val = min(max1, val2_base)
        if target_val > val1:
            val1 = target_val
            skill_log_1.append(f"👁️ <b>Пассивка (Копирование):</b> Автоматически скопирован стат врага ➔ {val1}")
        else:
            skill_log_1.append(f"👁️ <b>Пассивка (Копирование):</b> Ваш стат ({val1}) выше вражеского ({val2_base}), сохранен собственный стат!")
        g['p1_copy_used'] = True

    if cid2 in COPY_STYLE and not g.get('p2_copy_used', False):
        target_val = min(max2, val1_base)
        if target_val > val2:
            val2 = target_val
            skill_log_2.append(f"👁️ <b>Пассивка (Копирование):</b> Автоматически скопирован стат врага ➔ {val2}")
        else:
            skill_log_2.append(f"👁️ <b>Пассивка (Копирование):</b> Ваш стат ({val2}) выше вражеского ({val1_base}), сохранен собственный стат!")
        g['p2_copy_used'] = True

    # --- 3. РАСЧЕТ АКТИВНОЙ УЛЬТЫ ДЛЯ ИГРОКА 1 ---
    p1_pierce = False
    if g['p1_use_skill'] and not p1_skill_blocked:
        if cid1 in RISE_STYLE:
            stolen = int(val2_base * 0.07)
            val1 += stolen
            skill_log_1.append(f"🌑 <b>Ульта (Восстание):</b> Поглощено 7% стата врага (+{stolen})")
        elif cid1 in BERSERK_STYLE:
            if g['round'] == 5:
                val1 += 5
                skill_log_1.append(
                    "🩸 <b>Ульта (Берсерк):</b> Атака +5! <i>(Финальный раунд - бафф уменьшен)</i>")
            else:
                val1 += 10
                g['p1_next_debuff'] = -8
                skill_log_1.append(
                    "🩸 <b>Ульта (Берсерк):</b> Атака +10! <i>(На следующий раунд наложится Истощение -8)</i>")
        elif cid1 in SPACE_STYLE:
            val1 += 2
            skill_log_1.append("🌊 <b>Ульта (Пространство):</b> Немота на врага! (+2 к атакующему стату)")
        elif cid1 in PIERCE_STYLE:
            p1_pierce = True
            cut = 5 if ('Мифическая' in c2.get('rarity', '') or 'Легендарная' in c2.get('rarity', '')) else 10
            val2 = max(min2, val2 - cut)
            skill_log_1.append(f"⚔️ <b>Ульта (Пробивание):</b> Защита врага игнорируется! Стат врага снижен (-{cut})")
        elif cid1 in EVADE_STYLE:
            if random.random() < 0.5:
                val2 = min2
                skill_log_1.append(f"🌪 <b>Ульта (Уклонение):</b> Успешный уворот! Стат врага снижен до минимума ({min2})")
            else:
                skill_log_1.append("🌪 <b>Ульта (Уклонение):</b> Уклонение не сработало!")

    # --- 4. РАСЧЕТ АКТИВНОЙ УЛЬТЫ ДЛЯ ИГРОКА 2 ---
    p2_pierce = False
    if g['p2_use_skill'] and not p2_skill_blocked:
        if cid2 in RISE_STYLE:
            stolen = int(val1_base * 0.07)
            val2 += stolen
            skill_log_2.append(f"🌑 <b>Ульта (Восстание):</b> Поглощено 7% стата врага (+{stolen})")
        elif cid2 in BERSERK_STYLE:
            if g['round'] == 5:
                val2 += 5
                skill_log_2.append(
                    "🩸 <b>Ульта (Берсерк):</b> Атака +5! <i>(Финальный раунд - бафф уменьшен)</i>")
            else:
                val2 += 10
                g['p2_next_debuff'] = -8
                skill_log_2.append(
                    "🩸 <b>Ульта (Берсерк):</b> Атака +10! <i>(На следующий раунд наложится Истощение -8)</i>")
        elif cid2 in SPACE_STYLE:
            val2 += 2
            skill_log_2.append("🌊 <b>Ульта (Пространство):</b> Немота на врага! (+2 к атакующему стату)")
        elif cid2 in PIERCE_STYLE:
            p2_pierce = True
            cut = 5 if ('Мифическая' in c1.get('rarity', '') or 'Легендарная' in c1.get('rarity', '')) else 10
            val1 = max(min1, val1 - cut)
            skill_log_2.append(f"⚔️ <b>Ульта (Пробивание):</b> Защита врага игнорируется! Стат врага снижен (-{cut})")
        elif cid2 in EVADE_STYLE:
            if random.random() < 0.5:
                val1 = min1
                skill_log_2.append(f"🌪 <b>Ульта (Уклонение):</b> Успешный уворот! Стат врага снижен до минимума ({min1})")
            else:
                skill_log_2.append("🌪 <b>Ульта (Уклонение):</b> Уклонение не сработало!")

    # --- 5. ПРЕИМУЩЕСТВО СТИЛЕЙ И ИТОГОВЫЙ УРОН ---
    adv = check_advantage(g['p1_s'], g['p2_s'])
    m1, m2 = 1.0, 1.0
    bonus_txt_1, bonus_txt_2 = "", ""

    if adv == 1:
        if not p2_pierce:
            m2 = 0.9
            bonus_txt_1 = f"{s_map[g['p2_s']][0]} -10% ↘️"
            bonus_txt_2 = f"{s_map[g['p2_s']][0]} -10% ↘️"
        else:
            bonus_txt_1 = "Пробивание врага сработало! Дебафф от стиля отменен."
            bonus_txt_2 = "Пробивание сработало! Дебафф от стиля отменен."
    elif adv == -1:
        if not p1_pierce:
            m1 = 0.9
            bonus_txt_1 = f"{s_map[g['p1_s']][0]} -10% ↘️"
            bonus_txt_2 = f"{s_map[g['p1_s']][0]} -10% ↘️"
        else:
            bonus_txt_1 = "Пробивание сработало! Дебафф от стиля отменен."
            bonus_txt_2 = "Пробивание врага сработало! Дебафф от стиля отменен."

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

    def format_text(p_name, e_name, score_p, score_e, p_s, e_s, p_val, e_val, p_final, e_final, b_txt, p_emoji, e_emoji, p_logs, e_logs):
        t = (f"⬆️ Ваша карта | Карта врага ⬆️\nРаунд - {g['round']}\n\n"
             f"Счет:\n{p_name} {p_emoji} - {score_p}\n{e_name} {e_emoji} - {score_e}\n\n"
             f"⚔️ Вы совершаете {s_map[p_s][1]} атаку\nУровень атаки: {p_val}\n\n"
             f"🛡️ Противник ставит {s_map[e_s][1]} защиту\nУровень защиты: {e_val}\n\n")

        # Логи ульт
        if p_logs:
            t += "<b>Ваш навык:</b>\n" + "\n".join(p_logs) + "\n\n"
        if e_logs:
            t += "<b>Навык противника:</b>\n" + "\n".join(e_logs) + "\n\n"

        if adv != 0 and b_txt:
            t += f"Бонус стиля:\n{b_txt}\n\n"

        t += (f"Итоговый уровень атаки {s_map[p_s][0].split()[0]} : {p_final}\n"
              f"Итоговый уровень защиты {s_map[e_s][0].split()[0]}: {e_final}\n\n")
        t += f"Раунд завершился в ничью!" if winner_name == "Ничья" else f"Раунд выиграл {winner_name}"
        return t

    # === ПОЛУЧАЕМ МЕДИА-ИНФОРМАЦИЮ О КАРТАХ СО СКИНАМИ ===
    c1_path, c1_is_video, c1_label = get_card_media_info(g['p1'], g['p1_c'], c1)
    c2_path, c2_is_video, c2_label = get_card_media_info(g['p2'], g['p2_c'], c2)

    def create_media(path_main, is_video_main, path_secondary, is_video_secondary, card_main, card_secondary, caption_txt):
        m = []
        if is_video_main:
            m.append(types.InputMediaVideo(
                media=FSInputFile(path_main), caption=caption_txt, parse_mode="HTML",
                width=960, height=1280, supports_streaming=True
            ))
        else:
            m.append(types.InputMediaPhoto(media=FSInputFile(path_main), caption=caption_txt, parse_mode="HTML"))

        if is_video_secondary:
            m.append(types.InputMediaVideo(
                media=FSInputFile(path_secondary),
                width=960, height=1280, supports_streaming=True
            ))
        else:
            m.append(types.InputMediaPhoto(media=FSInputFile(path_secondary)))
        return m

    try:
        txt1 = format_text(n1, n2_link, g['score1'], g['score2'], g['p1_s'], g['p2_s'], val1_base, val2_base, f1, f2, bonus_txt_1, emoji1, emoji2, skill_log_1, skill_log_2)
        media1 = create_media(c1_path, c1_is_video, c2_path, c2_is_video, c1, c2, txt1)
        await bot.send_media_group(g['p1'], media=media1)
    except Exception as e:
        logging.error(f"Error sending round result to p1: {e}")

    if g['p2'] != -1:
        try:
            txt2 = format_text(n2_link, n1, g['score2'], g['score1'], g['p2_s'], g['p1_s'], val2_base, val1_base, f2, f1, bonus_txt_2, emoji2, emoji1, skill_log_2, skill_log_1)
            media2 = create_media(c2_path, c2_is_video, c1_path, c1_is_video, c2, c1, txt2)
            await bot.send_media_group(g['p2'], media=media2)
        except Exception as e:
            logging.error(f"Error sending round result to p2: {e}")

    # Сбрасываем текущий выбор карт и флаги ульт для следующего раунда
    g['round'] += 1
    g['p1_c'] = g['p2_c'] = g['p1_s'] = g['p2_s'] = None
    g['p1_use_skill'] = g['p2_use_skill'] = False

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
    # Используем .get() на случай, если игра уже удалилась (защита от двойного вызова)
    g = GAMES.pop(gid, None)
    if not g:
        return

    p1, p2, s1, s2 = g['p1'], g['p2'], g['score1'], g['score2']
    friendly = g.get('friendly', False)
    surrendered_uid = g.get('surrendered')

    def apply_res(uid, is_win, is_draw, friendly):
        if uid == -1: return 0, 0
        premium = is_premium(uid)

        if friendly:
            pts = 0
            bc = 3 if is_win else 1
            # В дружеских боях выдаем только копейки BattleCoin, стату не трогаем!
            db_exec(f"UPDATE users SET battlecoin = battlecoin + {bc} WHERE id = ?", (uid,))
        else:
            if is_win:
                pts = 4 if premium else 3
                bc = 14 if premium else 10
                # Плюс 1 к текущему стрику при победе
                stat_update = "wins = wins + 1, season_wins = season_wins + 1, current_streak = current_streak + 1"
            elif is_draw:
                pts = 2 if premium else 1
                bc = 6 if premium else 4
                # Сбрасываем стрик при ничьей
                stat_update = "draws = draws + 1, current_streak = 0"
            else:
                pts = -1 if premium else -2
                bc = 3 if premium else 2
                # Сбрасываем стрик при поражении
                stat_update = "losses = losses + 1, current_streak = 0"

            # В обычных боях обновляем очки, коины и статистику
            db_exec(
                f"UPDATE users SET rank_points = MAX(0, rank_points + {pts}), battlecoin = battlecoin + {bc}, {stat_update} WHERE id = ?",
                (uid,))

            # Если это победа, проверяем, не побит ли рекорд (max_streak)
            if is_win:
                db_exec("UPDATE users SET max_streak = current_streak WHERE id = ? AND current_streak > max_streak",
                        (uid,))

        return pts, bc

    # Логика победителя с учетом сдачи
    if surrendered_uid:
        is_p1_surrendered = (surrendered_uid == p1)
        p1_win = not is_p1_surrendered
        p2_win = is_p1_surrendered
        draw = False
    else:
        p1_win = s1 > s2
        p2_win = s2 > s1
        draw = (s1 == s2)

    r1 = apply_res(p1, p1_win, draw, friendly)
    r2 = apply_res(p2, p2_win, draw, friendly)

    # === MANHWCARD PASS: ОПЫТ И ЗАДАНИЯ ЗА БОЙ ===
    rounds_played = max(1, g.get('round', 2) - 1)

    def process_battle_pass(uid, is_win, is_draw, is_friendly, rounds):
        if uid == -1: return

        # Базовый опыт: 150 за победу, 100 за поражение/ничью
        base_xp = 150 if is_win else 100
        xp_res = add_pass_xp(uid, base_xp)

        # Задания
        check_and_update_quests(uid, 'q_10_battles', 1)
        check_and_update_quests(uid, 'q_30_rounds', rounds)

        if is_win:
            check_and_update_quests(uid, 'q_5_wins', 1)

        if is_friendly:
            check_and_update_quests(uid, 'q_5_friendly', 1)

        # Уведомление о повышении уровня
        if xp_res and xp_res.get("leveled_up"):
            try:
                asyncio.create_task(bot.send_message(
                    uid,
                    f"⚡️ <b>[СИСТЕМА]</b>\n\nВаш уровень ManhwCard Pass повышен!\nТекущий уровень: <b>{xp_res['level']}</b>.\n\n<i>Зайдите в Web App, чтобы забрать награду.</i>",
                    parse_mode="HTML"
                ))
            except:
                pass

    process_battle_pass(p1, p1_win, draw, friendly, rounds_played)
    process_battle_pass(p2, p2_win, draw, friendly, rounds_played)
    # ===============================================

    # Формируем ссылки на профили игроков для красивого текста
    my_name_raw = get_user(p1)[2]
    n1_link = f"<a href='tg://user?id={p1}'>{my_name_raw}</a>"

    if p2 == -1:
        n2_link = g['n2'] # У бота нет ссылки, просто имя
    else:
        n2_raw = get_user(p2)[2]
        n2_link = f"<a href='tg://user?id={p2}'>{n2_raw}</a>"

    # --- КРАСИВЫЕ СООБЩЕНИЯ О ЗАВЕРШЕНИИ ---
    score_text = f"📊 Финальный счет: <b>{s1} - {s2}</b>"

    if surrendered_uid:
        if surrendered_uid == p1:
            title1 = f"🏳️ <b>Вы сдались... Поражение.</b>\nПротивник {n2_link} оказался слишком силен."
            title2 = f"🏆 <b>ПОБЕДА!</b>\nПротивник {n1_link} трусливо сбежал с поля боя!"
        else:
            title1 = f"🏆 <b>ПОБЕДА!</b>\nПротивник {n2_link} не выдержал вашей мощи и сдался!"
            title2 = f"🏳️ <b>Вы сдались... Поражение.</b>\nПротивник {n1_link} оказался слишком силен."
    else:
        if p1_win:
            title1 = f"🏆 <b>ПОБЕДА!</b>\nВы эффектно растоптали оппонента {n2_link}!"
            title2 = f"💀 <b>ПОРАЖЕНИЕ.</b>\nВ этот раз {n1_link} оказался сильнее..."
        elif p2_win:
            title1 = f"💀 <b>ПОРАЖЕНИЕ.</b>\nВ этот раз {n2_link} оказался сильнее..."
            title2 = f"🏆 <b>ПОБЕДА!</b>\nВы эффектно растоптали оппонента {n1_link}!"
        else:
            title1 = f"🤝 <b>НИЧЬЯ.</b>\nДостойная битва двух равных соперников против {n2_link}."
            title2 = f"🤝 <b>НИЧЬЯ.</b>\nДостойная битва двух равных соперников против {n1_link}."

    msg1 = f"{title1}\n\n{score_text}\n<b>🎁 Награда:</b> {r1[0]} 🏅, {r1[1]} 🪙"
    msg2 = f"{title2}\n\n{score_text}\n<b>🎁 Награда:</b> {r2[0]} 🏅, {r2[1]} 🪙"

    try:
        await bot.send_message(p1, msg1, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Failed to send finish msg to p1: {e}")

    if p2 != -1:
        try:
            await bot.send_message(p2, msg2, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Failed to send finish msg to p2: {e}")

# ============ ЗАЩИТА И БЛОКИРОВКА ВО ВРЕМЯ БОЯ ============
from aiogram import BaseMiddleware


class BattleLockMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, types.CallbackQuery):
            uid = event.from_user.id

            # --- 1. ЗАЩИТА ВО ВРЕМЯ ПОИСКА ПРОТИВНИКА ---
            from handlers import MATCH_QUEUE  # Импортируем очередь, если она в другом файле
            if uid in MATCH_QUEUE:
                # Разрешаем только кнопку отмены поиска
                allowed_search = ('cancel_search',)
                if not event.data.startswith(allowed_search):
                    try:
                        await event.answer(
                            "⏳ Вы находитесь в поиске противника!\n\n"
                            "Пожалуйста, сначала отмените поиск, "
                            "чтобы воспользоваться другими функциями магазина или меню.",
                            show_alert=True
                        )
                    except:
                        pass
                    return  # Жестко блокируем выполнение любого другого кода

            # --- 2. ЗАЩИТА ВО ВРЕМЯ БОЯ ---
            in_battle = any(g['p1'] == uid or g['p2'] == uid for g in GAMES.values())
            if in_battle:
                # Разрешаем только кнопки выбора карты, стиля и сдачи
                allowed_battle = ('b_card:', 'b_style:', 'surrender:')
                if not event.data.startswith(allowed_battle):
                    gid = next((k for k, v in GAMES.items() if v['p1'] == uid or v['p2'] == uid), None)
                    if gid:
                        bld = InlineKeyboardBuilder()
                        bld.button(text="Сдаться 🏳️", callback_data=f"surrender:{gid}")
                        try:
                            # Уведомляем игрока прямо в чате, чтобы он не потерялся
                            await event.message.answer(
                                "⚔️ Вы сейчас находитесь в активном бою!\n"
                                "Сделайте ход или сдайтесь, чтобы открыть меню.",
                                reply_markup=bld.as_markup()
                            )
                            await event.answer()
                        except:
                            pass
                        return  # Жестко блокируем выполнение

        return await handler(event, data)


router.callback_query.middleware(BattleLockMiddleware())


@router.callback_query(F.data.startswith("surrender:"))
async def surrender_battle(cq: CallbackQuery):
    _, gid = cq.data.split(":")
    g = GAMES.get(gid)

    uid = cq.from_user.id

    # ПРИНУДИТЕЛЬНАЯ ОЧИСТКА: Если игра зависла и удалилась, но игрок застрял в Middleware
    if not g:
        zombie_gids = [k for k, v in GAMES.items() if v['p1'] == uid or v['p2'] == uid]
        for zg in zombie_gids:
            GAMES.pop(zg, None)
        if uid in MATCH_QUEUE:
            MATCH_QUEUE.remove(uid)
        try:
            await cq.message.delete()
        except:
            pass
        return await cq.answer("Бой завершен или был отменен.", show_alert=True)

    # ЗАЩИТА: Нельзя сдаться, если раунд уже подсчитывается (защита от крашей)
    if g.get('resolving'):
        return await cq.answer("Сейчас идет вычисление результатов раунда, сбежать не получится!", show_alert=True)

    # Ставим метку, кто именно сдался, вместо ломания счета
    g['surrendered'] = uid

    try:
        await cq.message.delete()
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
           f"━━━━━━━━━━━━━━━\n"
           f"Каждое сражение фиксируется в хронике данных.\n\n"
           f"<tg-emoji emoji-id='5267267636055520629'>👁️</tg-emoji> [Гайд по битвам](https://telegra.ph/Gajd-Pole-Bitvy-07-29)")

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
    "Уровень Короля 👑": 10, "Титан 🧬": 15, "Легенда 🐉": 20, "Безупречная мощь 😈": 25,
    "Абсолют ♾️": 30, "Владыка Хаоса 🌋": 35, "Монарх Пустоты 🌑": 45, "Бессмертный Архонт 🪽": 60
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
# === ТОП ПО ПОБЕДАМ ===
@router.callback_query(F.data == "b_top_wins")
async def b_top_wins_cb(cq: CallbackQuery):
    # Теперь ищем только по season_wins
    top_users = db_exec("SELECT id, nickname, season_wins FROM users ORDER BY season_wins DESC LIMIT 10", fetchall=True)
    all_users = db_exec("SELECT id FROM users ORDER BY season_wins DESC", fetchall=True)
    my_place = "Без места"
    for idx, (uid,) in enumerate(all_users):
        if uid == cq.from_user.id:
            my_place = idx + 1
            break

    txt = "🏆 ТОП 10 по Победам (Сезон):\n\n"
    for i, user in enumerate(top_users):
        emoji = "👑" if is_premium(user[0]) else "🧩"
        txt += f"{i + 1}. <a href='tg://user?id={user[0]}'>{user[1]}</a> {emoji} — {user[2]} 🎖\n"

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
        "🎁 Приз за 1-15 места лимитированная карта:\n"
        "<blockquote>🃏 Лим Се Джун</blockquote>\n\n"
        "📅 Дата окончания: 17-го Августа\n"
        f"🏆 Ваше место в ТОП-е: {my_place}\n"
        "🚸 ТОП обновляется в режиме реального времени."
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
        txt += f"{i + 1}. <a href='tg://user?id={user[0]}'>{user[1]}</a> {emoji} - {user[2]}\n"

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

    if os.path.exists("images/shop/top_wins.jpeg"):
        await cq.message.answer_photo(photo=FSInputFile("images/shop/top_wins.jpeg"), caption=txt,
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
    bld.button(text="Боевой Пак 🗄️",       callback_data="b_shop_pack")
    bld.button(text="Крутки 🪙",            callback_data="b_shop_spins")
    bld.button(text="Крафты 🧬",           callback_data="b_craft_menu")
    bld.button(text="Ставки 🎰",           callback_data="b_bet_menu")
    bld.button(text="Спин удачи 🍀",       callback_data="b_stub_luck")
    bld.button(text="Обмен алмазов 💎",    callback_data="b_diamond_exchange")
    bld.button(text="Назад 🔙",            callback_data="b_menu_back")
    bld.adjust(1, 2, 2, 1, 1)

    try:
        await cq.message.delete()
    except:
        pass

    if os.path.exists("images/shop/battle_shop.png"):
        await cq.message.answer_photo(
            photo=FSInputFile("images/shop/battle_shop.png"),
            caption=txt, reply_markup=bld.as_markup()
        )
    else:
        await cq.message.answer(txt, reply_markup=bld.as_markup())
    await cq.answer()


# === ВСТАВИТЬ В НАЧАЛО battle.py ПОСЛЕ ИМПОРТОВ ===
PACK_CARD = "lim_sae_jun"
PACK_DEFOLT_CARD = "nabirose"
PACK_BG1 = "golden_hours"
PACK_BG2 = "lookism_summer"
PACK_TITLE = "title_pack3"
# Переключатель выдачи карты за ТОП-20 (True - выдавать, False - временно отключено)
GIVE_TOP_20_CARD = True
# Переключатель второй карты в паке (True - включена, False - выключена)
ENABLE_SECOND_PACK_CARD = True

# === ЗАМЕНИТЬ ФУНКЦИИ b_shop_pack_cb И b_shop_pack_buy_cb (строки 1459-1533) ===

@router.callback_query(F.data == "b_shop_pack")
async def b_shop_pack_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz)
    # Сдвигаем время на 1 час назад, чтобы неделя сменялась ровно в 01:00 по МСК
    adjusted_time = now_msk - timedelta(hours=1)
    week_num = adjusted_time.isocalendar()[1]

    res = db_exec("SELECT bought_count FROM battle_shop_packs WHERE user_id = ? AND week_number = ?", (uid, week_num), fetch=True)
    bought = res[0] if res else 0

    txt = (
        "<b>Летний Боевой Пак ☀️</b>\n"
        f"💵 Можно купить: <b>{5 - bought}</b>\n"
        f"💸 Куплено: <b>{bought}</b>\n\n"
        "<blockquote>Стоимость: 350 🪙</blockquote>\n\n"
        "🔥 Главный приз: <b>Лим Се Джун</b>\n"
        "🧪 Содержимое:\n"
        "<blockquote>🃏 Лим Се Джун 0.1%\n"
        "🃏 Набироза 2%\n"
        "🌄 Lookism Summer 2.5%\n"
        "🔱 Железная стена 🧱 3%\n"
        "🌄 Golden Hours (арт) 5%\n"
        "🔴 Мифическая карта 6.5%\n"
        "🔵 Легендарная карта 78.9%</blockquote>\n\n"
        "🏆 Главный приз выдается автоматически за ТОП 15 по победам!\n\n"
        "📅 Дата окончания пака: 17-го Августа 📆"
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
    lock = _get_shop_lock(uid)

    # Если уже идет покупка - отбиваем спам
    if lock.locked():
        return await cq.answer("⏳ Транзакция в обработке, не спамьте...", show_alert=False)

    async with lock:
        # Устанавливаем московское время и сдвигаем на 1 час для синхронизации
        msk_tz = timezone(timedelta(hours=3))
        now_msk = datetime.now(msk_tz)
        adjusted_time = now_msk - timedelta(hours=1)
        week_num = adjusted_time.isocalendar()[1]

        res = db_exec("SELECT bought_count FROM battle_shop_packs WHERE user_id = ? AND week_number = ?",
                      (uid, week_num), fetch=True)
        bought = res[0] if res else 0

        if bought >= 5:
            return await cq.answer("На этой неделе вы уже скупили все паки!", show_alert=True)

        u = get_user(uid)
        if u[5] < 350:
            return await cq.answer("❌ Недостаточно BattleCoin! Нужно: 350 🪙", show_alert=True)

        # Списание валюты и обновление счетчика
        db_exec("UPDATE users SET battlecoin = battlecoin - 350 WHERE id = ?", (uid,))
        bought += 1

        # === MANHWCARD PASS ===
        check_and_update_quests(uid, 'q_4_packs', 1)

        if res:
            db_exec("UPDATE battle_shop_packs SET bought_count = ? WHERE user_id = ? AND week_number = ?",
                    (bought, uid, week_num))
        else:
            db_exec("INSERT INTO battle_shop_packs (user_id, week_number, bought_count) VALUES (?, ?, ?)",
                    (uid, week_num, bought))

        # Логика шансов с учетом тумблера
        if ENABLE_SECOND_PACK_CARD:
            rewards = ["card_main", "card_second", "bg_yamazaki", "bg_jaehwan", "title", "mythic", "legendary"]
            weights = [0.1, 2.0, 5.0, 3.5, 3.7, 6.5, 80.9]  # Веса под новый текст
        else:
            rewards = ["card_main", "bg_yamazaki", "bg_jaehwan", "title", "mythic", "legendary"]
            weights = [1.5, 5.0, 3.6, 3.4, 6.5, 80.0]  # Старые веса

        result = random.choices(rewards, weights=weights, k=1)[0]

        reward_text = ""
        card_c = None

        exists_bg1 = False
        exists_bg2 = False

        # БЕЗОПАСНАЯ ОБРАБОТКА (защита от NoneType и IntegrityError)
        if result == "card_main":
            is_new, krw, card_c = give_card_to_user(uid, PACK_CARD)
            if card_c:
                reward_text = format_card_msg(card_c, is_new, krw)
            else:
                reward_text = "🎁 <b>Главная карта временно недоступна!</b>\nВам начислена компенсация: 10000 💴"
                db_exec("UPDATE users SET krw = krw + 10000 WHERE id = ?", (uid,))
        elif result == "card_second":
            is_new, krw, card_c = give_card_to_user(uid, PACK_DEFOLT_CARD)
            if card_c:
                reward_text = format_card_msg(card_c, is_new, krw)
            else:
                reward_text = "🎁 <b>Дополнительная карта временно недоступна!</b>\nВам начислена компенсация: 5000 💴"
                db_exec("UPDATE users SET krw = krw + 5000 WHERE id = ?", (uid,))
        elif result == "bg_yamazaki":
            exists_bg1 = db_exec("SELECT 1 FROM bgs_inv WHERE user_id = ? AND bg_id = ?", (uid, PACK_BG1), fetch=True)
            bg_key = PACK_BG1
            bg_data = VIDEO_BGS.get(bg_key) or BGS.get(bg_key)
            bg_name = bg_data.get('name', 'Lookism Summer') if bg_data else 'Lookism Summer'

            if exists_bg1:
                reward_text = f"🌄 Вам выпал фон: <b>{bg_name}</b>, но он у вас уже есть!"
            else:
                db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, PACK_BG1))
                reward_text = f"✨ <b>Поздравляем!</b>\n\nТебе выпал новый фон: <b>{bg_name}</b>"
        elif result == "bg_jaehwan":
            exists_bg2 = db_exec("SELECT 1 FROM bgs_inv WHERE user_id = ? AND bg_id = ?", (uid, PACK_BG2), fetch=True)
            bg_key = PACK_BG2
            bg_data = VIDEO_BGS.get(bg_key) or BGS.get(bg_key)
            bg_name = bg_data.get('name', 'Golden Hours') if bg_data else 'Golden Hours'

            if exists_bg2:
                reward_text = f"🌄 Вам выпал фон: <b>{bg_name}</b>, но он у вас уже есть!"
            else:
                db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, PACK_BG2))
                reward_text = f"✨ <b>Поздравляем!</b>\n\nТебе выпал новый фон: <b>{bg_name}</b>"
        elif result == "title":
            exists = db_exec("SELECT 1 FROM titles_inv WHERE user_id = ? AND title_id = ?", (uid, PACK_TITLE),
                             fetch=True)
            if exists:
                reward_text = f"🔱 Вам выпал титул: <b>Железная стена 🧱</b>, но, к сожалению, он у вас уже есть!"
            else:
                db_exec("INSERT INTO titles_inv (user_id, title_id) VALUES (?, ?)", (uid, PACK_TITLE))
                reward_text = f"🔱 Получен новый титул: <b>Железная стена 🧱</b>!"
        elif result == "mythic":
            card_key = pull_random_card(force_rarity="Мифическая 🔴")
            is_new, krw, card_c = give_card_to_user(uid, card_key)
            if card_c:
                reward_text = format_card_msg(card_c, is_new, krw)
            else:
                reward_text = "🎁 <b>СБОЙ Мифическая карта не найдена в пуле!</b>\nВам начислена компенсация: 700 💴"
                db_exec("UPDATE users SET krw = krw + 700 WHERE id = ?", (uid,))
        else:  # legendary
            card_key = pull_random_card(force_rarity="Легендарная 🔵")
            is_new, krw, card_c = give_card_to_user(uid, card_key)
            if card_c:
                reward_text = format_card_msg(card_c, is_new, krw)
            else:
                reward_text = "🎁 <b>СБОЙ Легендарная карта не найдена в пуле!</b>\nВам начислена компенсация: 300 💴"
                db_exec("UPDATE users SET krw = krw + 300 WHERE id = ?", (uid,))

        # КЛАВИАТУРА ДЛЯ ПОВТОРНОГО ОТКРЫТИЯ
        bld = InlineKeyboardBuilder()
        if bought < 5:
            bld.button(text=f"Купить еще 💵 ({5 - bought}/5)", callback_data="b_shop_pack_buy")
        bld.button(text="Назад к пакам 🔙", callback_data="b_shop_pack")
        bld.adjust(1)

        try:
            # Вместо удаления старого сообщения — просто убираем кнопки!
            # Так игрок будет видеть всю историю своих дропов.
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        # ОТПРАВКА НАГРАДЫ
        if card_c is not None and card_c.get("file"):
            try:
                if "Божественная" in card_c.get("rarity", "") and card_c.get("video"):
                    await send_cached_video(
                        cq.bot, chat_id=uid, file_path=f"images/cards/{card_c['video']}",
                        caption=reward_text, width=card_c.get("width", 960), height=card_c.get("height", 1280),
                        has_spoiler=True, supports_streaming=True, reply_markup=bld.as_markup()
                    )
                else:
                    await cq.bot.send_photo(
                        uid, photo=FSInputFile(f"images/cards/{card_c['file']}"),
                        caption=reward_text, has_spoiler=True, parse_mode="HTML", reply_markup=bld.as_markup()
                    )
            except Exception:
                await cq.bot.send_message(uid, reward_text, parse_mode="HTML", reply_markup=bld.as_markup())

        elif result in ["bg_yamazaki", "bg_jaehwan"]:
            bg_key = PACK_BG1 if result == "bg_yamazaki" else PACK_BG2
            bg_data = VIDEO_BGS.get(bg_key) or BGS.get(bg_key)

            # Если фон выпал впервые — отправляем его картинкой/видео. Если дубль — просто текстом.
            if bg_data and not (exists_bg1 if result == "bg_yamazaki" else exists_bg2):
                file_path = f"images/backgrounds/{bg_data.get('file')}"
                try:
                    if bg_key in VIDEO_BGS:
                        await send_cached_video(
                            cq.bot, chat_id=uid, file_path=file_path,
                            caption=reward_text, parse_mode="HTML", supports_streaming=True,
                            width=bg_data.get('width'), height=bg_data.get('height'), reply_markup=bld.as_markup()
                        )
                    else:
                        await cq.bot.send_photo(uid, photo=FSInputFile(file_path), caption=reward_text,
                                                parse_mode="HTML", reply_markup=bld.as_markup())
                except Exception:
                    await cq.bot.send_message(uid, reward_text, parse_mode="HTML", reply_markup=bld.as_markup())
            else:
                await cq.bot.send_message(uid, reward_text, parse_mode="HTML", reply_markup=bld.as_markup())
        else:
            await cq.bot.send_message(uid, reward_text, parse_mode="HTML", reply_markup=bld.as_markup())

    await cq.answer()


def format_card_msg(c, is_new=True, krw=0):
    """Вспомогательная функция для формирования текста карты"""
    if is_new:
        header = "🃏 <b>Получена новая боевая карта!</b>"
    else:
        header = f"🛑 Вам попалась повторная карта! Вы получаете {krw} 💴 KRW"

    return (
        f"{header}\n\n"
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
    bld.button(text="50 🪙 = 1 💳", callback_data="b_spin_buy:50:1")
    bld.button(text="500 🪙 = 10 💳", callback_data="b_spin_buy:500:10")
    bld.button(text="5000 🪙 = 110 💳", callback_data="b_spin_buy:5000:110")
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


async def auto_pack_reset_notifier(bot: Bot):
    """Фоновая задача для сброса лимита паков и уведомления игроков в понедельник в 01:00 МСК"""
    msk_tz = timezone(timedelta(hours=3))

    while True:
        now_msk = datetime.now(msk_tz)

        # Проверяем: Понедельник (weekday == 0) ровно в 01:00
        if now_msk.weekday() == 0 and now_msk.hour == 1 and now_msk.minute == 0:

            # Получаем номер завершившейся недели
            last_week = (now_msk - timedelta(hours=2)).isocalendar()[1]

            # Ищем всех, кто скупал паки на прошлой неделе (чтобы не спамить "мертвым" аккаунтам)
            users_to_notify = db_exec("SELECT DISTINCT user_id FROM battle_shop_packs WHERE week_number = ?",
                                      (last_week,), fetchall=True)

            if users_to_notify:
                logging.info(f"Начинаем рассылку об обновлении паков для {len(users_to_notify)} игроков.")

            for (uid,) in users_to_notify:
                try:
                    await bot.send_message(
                        uid,
                        "📦 <b>Лимит паков обновлён!</b>\n\nБоевой пак снова доступен в BattleShop. Заходи и успей выбить эксклюзивные карты и фоны! 🃏",
                        parse_mode="HTML"
                    )
                    # Анти-флуд задержка (лимит Telegram - 30 сообщений в секунду)
                    await asyncio.sleep(0.05)
                except Exception:
                    pass  # Игрок мог заблокировать бота

            # Спим 60 секунд, чтобы цикл не выполнил рассылку дважды за эту же минуту
            await asyncio.sleep(60)

        # Проверяем совпадение времени каждую минуту
        await asyncio.sleep(60)

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

CRAFT_GIF_PATH   = "images/shop/craft_animation.mp4"   # путь к mp4 (Telegram сам покажет как GIF)
CRAFT_GIF_WIDTH  = 960          # ширина анимации крафта
CRAFT_GIF_HEIGHT = 480          # высота анимации крафта
_CRAFT_GIF_FILE_ID: str | None = None   # кеш file_id анимации крафта (заполняется после первой отправки)
CRAFT_REQUIRED  = 5          # легендарок нужно
CRAFT_COIN_COST = 200        # монет нужно
DIAMOND_RATE    = 6          # 1 💎 = ? 🪙
DIAMOND_MIN     = 10         # минимум к обмену

def _get_craft_slots(uid: int) -> list:
    """Возвращает список из 5 card_id (или None) для пользователя."""
    row = db_exec(
        "SELECT slot1, slot2, slot3, slot4, slot5 FROM craft_slots WHERE user_id = ?",
        (uid,), fetch=True
    )
    if not row:
        db_exec("INSERT INTO craft_slots (user_id) VALUES (?)", (uid,))
        return [None] * 5
    return list(row)

def _save_craft_slot(uid: int, slot_idx: int, card_id):
    """Сохраняет card_id в нужный слот (slot_idx: 0-4)."""
    col = f"slot{slot_idx + 1}"
    _get_craft_slots(uid)  # гарантируем строку в БД
    db_exec(f"UPDATE craft_slots SET {col} = ? WHERE user_id = ?", (card_id, uid))

def _clear_craft_slots(uid: int):
    db_exec(
        "UPDATE craft_slots SET slot1=NULL, slot2=NULL, slot3=NULL, slot4=NULL, slot5=NULL WHERE user_id=?",
        (uid,)
    )

def _card_stat_value(card: dict, key: str) -> int:
    value = card.get(key, 0)
    try:
        return int(value if value is not None else 0)
    except (TypeError, ValueError):
        return 0

def _craft_slots_text(slots: list) -> str:
    """Красивый текст с отображением слотов и статов."""
    text_lines = []
    total_slots = len(slots)

    for i, card_id in enumerate(slots, start=1):
        if i == 1 and i == total_slots:
            prefix = "•"
        elif i == 1:
            prefix = "┌"
        elif i == total_slots:
            prefix = "└"
        else:
            prefix = "├"

        if card_id and card_id in CARDS:
            c = CARDS[card_id]
            card_name = f"«{escape(str(c.get('name', card_id)))}»"
            spd = _card_stat_value(c, "speed")
            str_ = _card_stat_value(c, "strength")
            int_ = _card_stat_value(c, "intellect")
        else:
            card_name = "Пусто"
            spd = str_ = int_ = 0

        text_lines.append(f"{prefix} [{i}] {card_name}")

        stat_prefix = "    " if i == total_slots else "│"
        text_lines.append(f"{stat_prefix} ⚡️ {spd} │ 💪 {str_} │ 🧠 {int_}")

    return "\n".join(text_lines)


# ═══════════════════════════════════════════════════════════════
# СТАВКИ 🎰
# ═══════════════════════════════════════════════════════════════

BET_DEFAULT = 35
BET_MIN = 25
COINFLIP_STICKERS = {
    "eagle": "CAACAgIAAxkBAAFKh1lqEzS9mRtqZYN_N7KbMzOXPiG6BgACqIMAAvaUyErsbrJIlVH9hzsE",
    "tails": "CAACAgIAAxkBAAFKh1hqEzS9gTamhA9QzEioKB4D82T1KQACl4cAAmkJ0UoCXcTupNR67DsE",
}
BET_VALID_CHOICES = {
    "coin": {"eagle", "tails"},
    "dice": {"even", "odd"},
    "ball": {"goal", "miss"},
}
# Замки для защиты транзакций от автокликеров
SHOP_LOCKS: dict[int, asyncio.Lock] = {}

def _get_shop_lock(uid: int) -> asyncio.Lock:
    if uid not in SHOP_LOCKS:
        SHOP_LOCKS[uid] = asyncio.Lock()
    return SHOP_LOCKS[uid]

BET_PLAY_LOCKS: dict[int, asyncio.Lock] = {}

def _get_bet_lock(uid: int) -> asyncio.Lock:
    lock = BET_PLAY_LOCKS.get(uid)
    if lock is None:
        lock = asyncio.Lock()
        BET_PLAY_LOCKS[uid] = lock
    return lock


def _get_bet_data(uid: int) -> tuple:
    """Возвращает (streak, bet) для игрока."""
    row = db_exec(
        "SELECT streak, bet FROM bets_streak WHERE user_id = ?",
        (uid,), fetch=True
    )
    if not row:
        db_exec("INSERT INTO bets_streak (user_id, streak, bet) VALUES (?, 0, ?)", (uid, BET_DEFAULT))
        return 0, BET_DEFAULT

    # Если у игрока сохранилась старая ставка (например, 10), принудительно поднимаем её до 35
    bet = row[1]
    if bet < BET_MIN:
        bet = BET_MIN

    return row[0], bet

def _save_bet(uid: int, streak: int, bet: int):
    db_exec(
        "UPDATE bets_streak SET streak = ?, bet = ? WHERE user_id = ?",
        (streak, bet, uid)
    )

def _bet_keyboard(game: str = "") -> InlineKeyboardMarkup:
    """Строит главное меню ставок с выделением выбранной игры."""
    coin  = "🪙*" if game == "coin"   else "🪙"
    dice  = "🎲*" if game == "dice"   else "🎲"
    ball  = "⚽️*" if game == "ball"  else "⚽️"

    bld = InlineKeyboardBuilder()
    bld.row(
        InlineKeyboardButton(text=coin, callback_data="b_bet_coin"),
        InlineKeyboardButton(text=dice, callback_data="b_bet_dice"),
        InlineKeyboardButton(text=ball, callback_data="b_bet_ball"),
    )
    if game == "coin":
        bld.row(
            InlineKeyboardButton(text="Орёл x2",  callback_data="b_bet_play:coin:eagle"),
            InlineKeyboardButton(text="Решка x2", callback_data="b_bet_play:coin:tails"),
        )
    elif game == "dice":
        bld.row(
            InlineKeyboardButton(text="Чётное x2",   callback_data="b_bet_play:dice:even"),
            InlineKeyboardButton(text="Нечётное x2", callback_data="b_bet_play:dice:odd"),
        )
    elif game == "ball":
        bld.row(
            InlineKeyboardButton(text="Гол x1.5",    callback_data="b_bet_play:ball:goal"),
            InlineKeyboardButton(text="Промах x2",   callback_data="b_bet_play:ball:miss"),
        )
    bld.row(InlineKeyboardButton(text="✍️ Изменить Ставку", callback_data="b_bet_change"))
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="b_shop_main"))
    return bld.as_markup()

def _bet_result_keyboard(game: str, choice: str) -> InlineKeyboardMarkup:
    bld = InlineKeyboardBuilder()
    bld.row(
        InlineKeyboardButton(text="Повторить",     callback_data=f"b_bet_play:{game}:{choice}"),
        InlineKeyboardButton(text="Уменьшить",     callback_data="b_bet_half"),
        InlineKeyboardButton(text="Удвоить",       callback_data="b_bet_double"),
        InlineKeyboardButton(text="Назад к играм", callback_data="b_bet_menu"),
    )
    bld.adjust(2, 2)
    return bld.as_markup()

async def _show_bet_menu(cq: CallbackQuery, game: str = ""):
    uid = cq.from_user.id
    u = get_user(uid)
    balance = u[5]  # battlecoin
    _, bet = _get_bet_data(uid)

    hot = "<tg-emoji emoji-id='5276032951342088188'>💥</tg-emoji> Крупные выигрыши"
    txt = (
        f"<tg-emoji emoji-id='5235989279024373566'>🎰</tg-emoji> Выберите игру\n\n"
        f"Баланс — {balance} 🪙\n"
        f"Ставка — {bet} 🪙\n\n"
        f"{hot}"
    )
    try:
        await cq.message.edit_text(txt, reply_markup=_bet_keyboard(game))
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(txt, reply_markup=_bet_keyboard(game))

@router.callback_query(F.data == "b_bet_menu")
async def b_bet_menu_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await _show_bet_menu(cq)
    await cq.answer()

@router.callback_query(F.data == "b_bet_coin")
async def b_bet_coin_cb(cq: CallbackQuery):
    await _show_bet_menu(cq, game="coin")
    await cq.answer()

@router.callback_query(F.data == "b_bet_dice")
async def b_bet_dice_cb(cq: CallbackQuery):
    await _show_bet_menu(cq, game="dice")
    await cq.answer()

@router.callback_query(F.data == "b_bet_ball")
async def b_bet_ball_cb(cq: CallbackQuery):
    await _show_bet_menu(cq, game="ball")
    await cq.answer()


@router.callback_query(F.data == "b_bet_double")
async def b_bet_double_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    streak, bet = _get_bet_data(uid)
    _save_bet(uid, streak, bet * 2)
    await cq.answer(f"Ставка удвоена: {bet * 2} 🪙", show_alert=False)
@router.callback_query(F.data == "b_bet_half")
async def b_bet_half_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    streak, bet = _get_bet_data(uid)
    new_bet = max(BET_MIN, bet // 2)
    _save_bet(uid, streak, new_bet)
    await cq.answer(f"Ставка уменьшена: {new_bet} 🪙", show_alert=False)

class BetChangeState(StatesGroup):
    waiting_for_bet = State()

@router.callback_query(F.data == "b_bet_change")
async def b_bet_change_cb(cq: CallbackQuery, state: FSMContext):
    bld = InlineKeyboardBuilder()
    bld.button(text="Отмена", callback_data="b_bet_menu")
    await cq.message.answer(
        f"Введите новую ставку (минимум {BET_MIN} 🪙):",
        reply_markup=bld.as_markup()
    )
    await state.set_state(BetChangeState.waiting_for_bet)
    await cq.answer()

@router.message(BetChangeState.waiting_for_bet)
async def b_bet_change_msg(msg: types.Message, state: FSMContext):
    if not msg.text or not msg.text.strip().isdigit():
        return await msg.answer(f"Введите число (минимум {BET_MIN}).")
    new_bet = int(msg.text.strip())
    if new_bet < BET_MIN:
        return await msg.answer(f"Минимальная ставка — {BET_MIN} 🪙.")
    uid = msg.from_user.id
    streak, _ = _get_bet_data(uid)
    _save_bet(uid, streak, new_bet)
    await state.clear()
    await msg.answer(f"✅ Ставка установлена: {new_bet} 🪙")

@router.callback_query(F.data.startswith("b_bet_play:"))
async def b_bet_play_cb(cq: CallbackQuery):
    try:
        _, game, choice = cq.data.split(":", 2)
    except ValueError:
        return await cq.answer("❌ Некорректная ставка.", show_alert=True)

    if choice not in BET_VALID_CHOICES.get(game, set()):
        return await cq.answer("❌ Некорректная ставка.", show_alert=True)

    uid = cq.from_user.id
    lock = _get_bet_lock(uid)

    if lock.locked():
        return await cq.answer("⏳ Предыдущая игра ещё не завершилась.", show_alert=False)

    async with lock:
        u = get_user(uid)
        balance = u[5]
        streak, bet = _get_bet_data(uid)

        if balance < bet:
            return await cq.answer("❌ Недостаточно BattleCoin!", show_alert=True)

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer()

        db_exec("UPDATE users SET battlecoin = battlecoin - ? WHERE id = ?", (bet, uid))
        balance -= bet
        # === MANHWCARD PASS ===
        check_and_update_quests(uid, 'q_20_bets', 1)

        win = False
        result_val = None
        result_label = "-"
        choice_label = "-"
        try:
            if game == "coin":
                result_val = random.choice(["eagle", "tails"])
                win = (result_val == choice)
                result_label = "Орёл" if result_val == "eagle" else "Решка"
                choice_label = "Орёл" if choice == "eagle" else "Решка"

                sticker_id = COINFLIP_STICKERS.get(result_val)
                if sticker_id:
                    try:
                        await cq.bot.send_sticker(uid, sticker_id)
                    except Exception:
                        pass
                await asyncio.sleep(1)

            elif game == "dice":
                dice_msg = await cq.bot.send_dice(uid, emoji="🎲")
                await asyncio.sleep(4)
                result_val = dice_msg.dice.value
                is_even = (result_val % 2 == 0)
                win = (choice == "even" and is_even) or (choice == "odd" and not is_even)
                result_label = f"{result_val} ({'Чётное' if is_even else 'Нечётное'})"
                choice_label = "Чётное" if choice == "even" else "Нечётное"

            elif game == "ball":
                ball_msg = await cq.bot.send_dice(uid, emoji="⚽️")
                await asyncio.sleep(4)
                result_val = ball_msg.dice.value
                is_goal = result_val in [3, 4, 5]
                win = (choice == "miss" and not is_goal) or (choice == "goal" and is_goal)
                result_label = "Гол" if is_goal else "Промах"
                choice_label = "Гол" if choice == "goal" else "Промах"

            else:
                db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (bet, uid))
                return await cq.message.answer("❌ Неизвестная игра. Ставка возвращена.")

        except Exception:
            logging.exception("Ошибка в ставках: uid=%s game=%s choice=%s", uid, game, choice)
            db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (bet, uid))
            return await cq.message.answer("❌ Ошибка игры. Ставка возвращена.")

        multiplier = 1.5 if game == "ball" and choice == "goal" else 2.0

        if win:
            prize = int(bet * multiplier)
            db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (prize, uid))
            balance += prize
            streak += 1
            _save_bet(uid, streak, bet)

            # 🔥 НОВАЯ ЛОГИКА: Выполняем квест, если игрок сделал стрик из 3 побед
            if streak == 3:
                q_res = check_and_update_quests(uid, 'q_3_bet_wins', 1)
                if q_res and q_res.get("leveled_up"):
                    try:
                        await cq.bot.send_message(
                            uid,
                            f"⚡️ <b>[СИСТЕМА]</b>\n\n"
                            f"Требования выполнены.\n"
                            f"Ваш уровень ManhwCard Pass повышен!\n"
                            f"Текущий уровень: <b>{q_res['level']}</b>.\n\n"
                            f"<i>Зайдите в Web App, чтобы забрать награду.</i>",
                            parse_mode="HTML"
                        )
                    except:
                        pass

            jackpot_bonus = 0
            if random.random() < 0.01:
                jackpot_bonus = 100
                db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (jackpot_bonus, uid))
                balance += jackpot_bonus

            net = prize - bet
            txt = (
                f"<tg-emoji emoji-id='5201730588351945766'>🎉</tg-emoji> Выигрыш +{net} 🪙\n\n"
                f"Выбрано: {choice_label}\n"
                f"Выпало: {result_label}\n"
            )
            if jackpot_bonus:
                txt += f"\n<tg-emoji emoji-id='5188464800174190349'>💎</tg-emoji> JACKPOT!\nБонус: +{jackpot_bonus} 🪙\n"

            if streak > 0 and streak % 5 == 0:
                bonus = 75
                db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (bonus, uid))
                balance += bonus
                txt += f"\n🔥 Побед подряд: {streak}\n🎁 Бонус за серию: +{bonus} 🪙\n"
            elif streak > 1:
                txt += f"\n🔥 Побед подряд: {streak}\n"

        else:
            streak = 0
            _save_bet(uid, streak, bet)
            txt = (
                f"<tg-emoji emoji-id='5924675271914426175'>💔</tg-emoji> Могло быть +{int(bet * multiplier) - bet} 🪙\n\n"
                f"Выбрано: {choice_label}\n"
                f"Выпало: {result_label}\n"
            )

        # ── общая часть для победы и поражения ──
        txt += (
            f"\n<blockquote>"
            f"Ставка: {bet} 🪙\n"
            f"Баланс: {balance} 🪙"
            f"</blockquote>"
        )

        await cq.message.answer(
            txt,
            reply_markup=_bet_result_keyboard(game, choice),
            parse_mode="HTML"
        )

@router.callback_query(F.data == "b_stub_luck")
async def b_stub_luck(cq: CallbackQuery):
    await cq.answer("🍀 Спин удачи — скоро будет доступен!", show_alert=True)

# КРАФТ-СИСТЕМА
# ═══════════════════════════════════════════════════════════════

@router.callback_query(F.data == "b_craft_menu")
async def b_craft_menu_cb(cq: CallbackQuery):
    txt = (
        "🧬 <b>Craft System</b>\n\n"
        "Добро пожаловать в систему крафта.\n\n"
        "Здесь доступны:\n"
        "• 🧪 <b>Fusion Reactor</b> — создание Mythic-карт\n"
        "• 🧩 <b>Переработка лишних карт</b>\n\n"
        "⚠️ Некоторые крафты могут уничтожить материалы."
    )
    bld = InlineKeyboardBuilder()
    bld.button(text="🧪 Fusion Reactor",  callback_data="b_craft_reactor")
    bld.button(text="Назад 🔙",           callback_data="b_shop_main")
    bld.adjust(1)

    try:
        await cq.message.delete()
    except:
        pass

    if os.path.exists("images/shop/battle_shop.png"):
        await cq.message.answer_photo(
            photo=FSInputFile("images/shop/battle_shop.png"),
            caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML"
        )
    else:
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data == "b_craft_corrupted")
async def b_craft_corrupted_cb(cq: CallbackQuery):
    await cq.answer("☠️ Corrupted Fusion — скоро будет доступен!", show_alert=True)


# ─── Fusion Reactor — главный экран ──────────────────────────
@router.callback_query(F.data == "b_craft_reactor")
async def b_craft_reactor_cb(cq: CallbackQuery):
    uid  = cq.from_user.id
    slots = _get_craft_slots(uid)
    filled = [s for s in slots if s is not None]
    ready  = len(filled) == CRAFT_REQUIRED

    status_line = "\n🧬 <b>Reactor готов к запуску...</b>" if ready else ""

    txt = (
        "🧪 <b>Fusion Reactor</b>\n\n"
        "⚠️ Нестабильный синтез карт высокой редкости.\n\n"
        "━━━━━━━━━━━━━━\n"
        "📦 <b>Требуется:</b>\n"
        f"🔵 Легендарные x{CRAFT_REQUIRED}\n"
        f"💸 {CRAFT_COIN_COST} 🪙\n\n"
        "🎲 <b>Шансы:</b>\n"
        "🔴 Мифическая 55%\n"
        "🔵 Рандом Легендарная 35%\n"
        "💥 Потеря материалов 8%\n"
        "✨ Шанс Exclusive карты 2%\n"
        "━━━━━━━━━━━━━━\n\n"
        f"🃏 <b>Слоты ({len(filled)}/{CRAFT_REQUIRED}):</b>\n"
        f"{_craft_slots_text(slots)}"
        f"{status_line}"
    )

    bld = InlineKeyboardBuilder()
    if ready:
        bld.button(text="Крафтить 🧬",         callback_data="b_craft_do")
    bld.button(text="Положить карты 🃏",     callback_data="b_craft_add_card")
    bld.button(text="Очистить слоты 🗑️",     callback_data="b_craft_clear")
    bld.button(text="Назад 🔙",              callback_data="b_craft_menu")
    bld.adjust(1)

    try:
        await cq.message.delete()
    except:
        pass

    if os.path.exists("images/shop/craft.jpeg"):          # КЛЮЧ 🗝️ — вставь нужную картинку
        await cq.message.answer_photo(
            photo=FSInputFile("images/shop/craft.jpeg"),
            caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML"
        )
    else:
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


# ─── Очистить слоты ───────────────────────────────────────────
@router.callback_query(F.data == "b_craft_clear")
async def b_craft_clear_cb(cq: CallbackQuery):
    _clear_craft_slots(cq.from_user.id)
    await cq.answer("🗑️ Слоты очищены!", show_alert=True)
    await b_craft_reactor_cb(cq)


# ─── Выбор карты для слота: показываем список легендарок ─────
@router.callback_query(F.data == "b_craft_add_card")
async def b_craft_add_card_cb(cq: CallbackQuery, state: FSMContext):
    uid   = cq.from_user.id
    slots = _get_craft_slots(uid)
    in_slots = [s for s in slots if s is not None]

    if len(in_slots) >= CRAFT_REQUIRED:
        return await cq.answer("✅ Все слоты уже заполнены!", show_alert=True)

    # Берём Легендарные карты пользователя, которых нет в слотах
    all_cards = db_exec(
        "SELECT card_id FROM cards_inv WHERE user_id = ?",
        (uid,), fetchall=True
    )
    legend_cards = []
    seen = set()
    for (cid,) in all_cards:
        if cid in seen:
            continue
        seen.add(cid)
        c = CARDS.get(cid)
        if c and "Легендарная" in c.get("rarity", "") and cid not in in_slots:
            legend_cards.append((cid, c))

    if not legend_cards:
        return await cq.answer("❌ Нет доступных Легендарных карт!", show_alert=True)

    # Сохраняем в FSM, какие карты доступны для выбора
    await state.update_data(legend_cards=[cid for cid, _ in legend_cards])
    await state.set_state(CraftState.choosing_slot)

    bld = InlineKeyboardBuilder()
    for cid, c in legend_cards[:20]:  # макс 20 кнопок
        spd = _card_stat_value(c, "speed")
        str_ = _card_stat_value(c, "strength")
        int_ = _card_stat_value(c, "intellect")

        bld.button(
            text=f"🔵 {c.get('name', cid)} | ⚡️{spd} 💪{str_} 🧠{int_}",
            callback_data=f"b_craft_slot:{cid}"
        )
    bld.button(text="Отмена ✖️", callback_data="b_craft_reactor")
    bld.adjust(1)

    txt = (
        "🃏 <b>Выбери карту для слота</b>\n\n"
        "Отображаются только Легендарные карты,\n"
        "которых ещё нет в Reactor."
    )

    try:
        await cq.message.delete()
    except:
        pass
    await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


# ─── Добавить карту в первый свободный слот ──────────────────
@router.callback_query(F.data.startswith("b_craft_slot:"), CraftState.choosing_slot)
async def b_craft_slot_cb(cq: CallbackQuery, state: FSMContext):
    _, card_id = cq.data.split(":", 1)
    uid   = cq.from_user.id
    slots = _get_craft_slots(uid)

    # Первый свободный слот
    free_idx = next((i for i, s in enumerate(slots) if s is None), None)
    if free_idx is None:
        await cq.answer("✅ Все слоты заняты!", show_alert=True)
        await state.clear()
        return

    _save_craft_slot(uid, free_idx, card_id)
    c = CARDS.get(card_id, {})
    await state.clear()
    await cq.answer(f"✅ {c.get('name', card_id)} → Слот {free_idx + 1}", show_alert=True)

    # Обновляем экран Reactor
    await b_craft_reactor_cb(cq)


# ─── КРАФТ — выполнение ──────────────────────────────────────
EXCLUSIVE_CRAFT_CARD = "yunsu"   # 🗝️ ВСТАВЬ СЮДА card_id эксклюзивной карты (аналог PACK_CARD)

@router.callback_query(F.data == "b_craft_do")
async def b_craft_do_cb(cq: CallbackQuery):
    uid = cq.from_user.id
    slots = _get_craft_slots(uid)
    filled = [s for s in slots if s is not None]

    if len(filled) < CRAFT_REQUIRED:
        return await cq.answer("❌ Нужно заполнить все 5 слотов!", show_alert=True)

    # --- ИСПРАВЛЕНИЕ: ЖЕСТКАЯ ПРОВЕРКА НАЛИЧИЯ КАРТ (Анти-дюп) ---
    needed_counts = {}
    for cid in filled:
        needed_counts[cid] = needed_counts.get(cid, 0) + 1

    inv_rows = db_exec("SELECT card_id, COUNT(*) FROM cards_inv WHERE user_id = ? GROUP BY card_id", (uid,), fetchall=True)
    inv_counts = {row[0]: row[1] for row in inv_rows}

    for cid, needed in needed_counts.items():
        if inv_counts.get(cid, 0) < needed:
            _clear_craft_slots(uid)
            return await cq.answer("❌ Ошибка синхронизации! Карт не хватает в инвентаре (вы перенесли их в сундук?). Слоты сброшены.", show_alert=True)
    # -------------------------------------------------------------

    u = get_user(uid)
    if u[5] < CRAFT_COIN_COST:
        return await cq.answer(
            f"❌ Недостаточно BattleCoin! Нужно: {CRAFT_COIN_COST} 🪙", show_alert=True
        )

        # БЕЗОПАСНОЕ СПИСАНИЕ МАТЕРИАЛОВ: удаляем по одному rowid
    db_exec("UPDATE users SET battlecoin = battlecoin - ? WHERE id = ?", (CRAFT_COIN_COST, uid))
    # === MANHWCARD PASS ===
    check_and_update_quests(uid, 'q_2_fusions', 1)
    for cid in filled:
        row = db_exec("SELECT rowid FROM cards_inv WHERE user_id = ? AND card_id = ? LIMIT 1", (uid, cid),
                          fetch=True)
        if row:
            db_exec("DELETE FROM cards_inv WHERE rowid = ?", (row[0],))

            # --- ПРОВЕРКА ОСТАТКОВ (Анти-Фантом) ---
        l_inv = db_exec("SELECT COUNT(*) FROM cards_inv WHERE user_id = ? AND card_id = ?", (uid, cid), fetch=True)
        l_st = db_exec("SELECT COUNT(*) FROM cards_stash WHERE user_id = ? AND card_id = ?", (uid, cid), fetch=True)

        if (l_inv[0] if l_inv else 0) + (l_st[0] if l_st else 0) == 0:
            db_exec("DELETE FROM decks WHERE user_id = ? AND card_id = ?", (uid, cid))
            try:
                db_exec(
                        "DELETE FROM multi_deck_slots WHERE card_id = ? AND deck_id IN (SELECT deck_id FROM multi_decks WHERE user_id = ?)",
                        (cid, uid))
            except:
                pass
            db_exec("DELETE FROM favorite_cards WHERE user_id = ? AND card_id = ?", (uid, cid))

    _clear_craft_slots(uid)

    # Рулетка результата
    outcomes = ["exclusive", "mythic", "legendary", "loss"]
    weights = [2, 55.0, 35.0, 8.0]
    result = random.choices(outcomes, weights=weights, k=1)[0]

    # --- Отправляем GIF-анимацию ---
    try:
        await cq.message.delete()
    except:
        pass

    global _CRAFT_GIF_FILE_ID
    gif_msg = None
    _craft_caption = "⚗️ <b>Реактор запущен...</b>\n\nСинтез идёт..."
    try:
        # ИСПОЛЬЗУЕМ send_video ВМЕСТО send_animation ДЛЯ MP4
        if _CRAFT_GIF_FILE_ID:
            gif_msg = await cq.bot.send_video(
                uid,
                video=_CRAFT_GIF_FILE_ID,
                caption=_craft_caption,
                parse_mode="HTML",
                width=CRAFT_GIF_WIDTH,
                height=CRAFT_GIF_HEIGHT
            )
        elif os.path.exists(CRAFT_GIF_PATH):
            gif_msg = await cq.bot.send_video(
                uid,
                video=FSInputFile(CRAFT_GIF_PATH),
                caption=_craft_caption,
                parse_mode="HTML",
                width=CRAFT_GIF_WIDTH,
                height=CRAFT_GIF_HEIGHT
            )
            if gif_msg and gif_msg.video:
                _CRAFT_GIF_FILE_ID = gif_msg.video.file_id
                logging.info(f"[craft] cached video file_id: {_CRAFT_GIF_FILE_ID}")
        else:
            gif_msg = await cq.bot.send_message(
                uid,
                "⚗️ <b>Реактор запущен...</b>\n\n🔄 Синтез идёт...",
                parse_mode="HTML"
            )
    except Exception as e:
        logging.exception(f"[craft] send_video failed, resetting cache: {e}")
        _CRAFT_GIF_FILE_ID = None
        # Безопасный фоллбэк: если видео вообще не грузится, шлем текст, чтобы игрок не завис
        gif_msg = await cq.bot.send_message(uid, _craft_caption, parse_mode="HTML")

    await asyncio.sleep(8)  # держим ожидание

    # --- Удаляем гифку ---
    try:
        if gif_msg:
            await gif_msg.delete()
    except:
        pass

    # --- Формируем результат ---
    if result == "loss":
        txt = (
            "💥 <b>Реактор нестабилен!</b>\n\n"
            "Синтез провалился — все материалы уничтожены.\n"
            "Попробуй ещё раз."
        )
        await cq.bot.send_message(uid, txt, parse_mode="HTML")
        await cq.answer()
        return

    card_c = None
    is_excl = False

    if result == "exclusive" and EXCLUSIVE_CRAFT_CARD and EXCLUSIVE_CRAFT_CARD in CARDS:
        is_new, krw, card_c = give_card_to_user(uid, EXCLUSIVE_CRAFT_CARD)
        is_excl = True
    elif result == "mythic":
        card_key = pull_random_card(force_rarity="Мифическая 🔴")
        is_new, krw, card_c = give_card_to_user(uid, card_key)
    else:  # legendary
        card_key = pull_random_card(force_rarity="Легендарная 🔵")
        is_new, krw, card_c = give_card_to_user(uid, card_key)

    # Заголовок как в обычной гаче, с отличием для Exclusive
    if is_excl:
        header = "💫 <b>Получена новая лимитированная карта!</b>"
    elif is_new:
        header = "🃏 <b>Получена новая боевая карта!</b>"
    else:
        header = f"🛑 Вам попалась повторная карта! Вы получаете {krw} 💴 KRW"

    reward_txt = (
        f"{header}\n\n"
        f"🎴 <b>Персонаж:</b> {card_c['name']}\n"
        f"🔮 <b>Редкость:</b> {card_c['rarity']}\n"
        f"👊 <b>Стиль боя:</b> {card_c['style']}\n"
        f"🪐 <b>Вселенная:</b> {card_c.get('series', 'Неизвестно')}\n\n"
        f"⚡️ <b>Скорость:</b> {card_c['speed']}\n"
        f"💪 <b>Сила:</b> {card_c['strength']}\n"
        f"🧠 <b>Интеллект:</b> {card_c['intellect']}"
    )

    # Отправляем карту как в гаче (со спойлером)
    if card_c and card_c.get("file"):
        try:
            if "Божественная" in card_c.get("rarity", "") and card_c.get("video"):
                await send_cached_video(
                    cq.bot,
                    chat_id=uid,
                    file_path=f"images/cards/{card_c['video']}",
                    caption=reward_txt,
                    width=card_c.get("width", 960),
                    height=card_c.get("height", 1280),
                    has_spoiler=True,
                    supports_streaming=True
                )
            else:
                await cq.bot.send_photo(
                    uid,
                    photo=FSInputFile(f"images/cards/{card_c['file']}"),
                    caption=reward_txt,
                    has_spoiler=True,
                    parse_mode="HTML"
                )
        except Exception:
            await cq.bot.send_message(uid, reward_txt, parse_mode="HTML")
    else:
        await cq.bot.send_message(uid, reward_txt, parse_mode="HTML")

    await cq.answer()


# ═══════════════════════════════════════════════════════════════
# ОБМЕН АЛМАЗОВ
# ═══════════════════════════════════════════════════════════════

@router.callback_query(F.data == "b_diamond_exchange")
async def b_diamond_exchange_cb(cq: CallbackQuery, state: FSMContext):
    u   = get_user(cq.from_user.id)
    dia = u[3]  # колонка diamond

    txt = (
        "💎 <b>Обмен алмазов</b>\n\n"
        "Здесь ты можешь обменять свои Алмазы 💎 на валюту нашего "
        "магазина BattleShop и при этом БЕЗ комиссии %😆\n\n"
        "<blockquote>Текущий курс: 1💎 = 7🪙</blockquote>\n"
        f"Принимаем обмен от {DIAMOND_MIN} алмазов 💎\n"
        f"Ваш баланс: <b>{dia} 💎</b>\n\n"
        "Введите сумму которую хотите внести:"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="Назад 🔙", callback_data="b_dia_back")

    try:
        await cq.message.delete()
    except:
        pass

    if os.path.exists("images/shop/battle_shop.png"):
        sent = await cq.message.answer_photo(
            photo=FSInputFile("images/shop/battle_shop.png"),
            caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML"
        )
    else:
        sent = await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")

    await state.set_state(DiamondExchangeState.entering_amount)
    await state.update_data(prompt_msg_id=sent.message_id)
    await cq.answer()

@router.callback_query(F.data == "b_dia_back")
async def b_dia_back_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await b_shop_main_cb(cq)
    await cq.answer()

@router.message(DiamondExchangeState.entering_amount)
async def b_diamond_amount_msg(msg: types.Message, state: FSMContext):
    uid = msg.from_user.id

    if not msg.text or not msg.text.strip().isdigit():
        bld = InlineKeyboardBuilder()
        bld.button(text="Отменить", callback_data="b_dia_cancel")
        return await msg.answer(
            "❌ Введите целое число алмазов для обмена или нажмите «Отменить».",
            reply_markup=bld.as_markup()
        )

    amount = int(msg.text.strip())

    if amount < DIAMOND_MIN:
        return await msg.answer(
            f"❌ Минимальная сумма для обмена — {DIAMOND_MIN} 💎"
        )

    u   = get_user(uid)
    dia = u[3]

    if dia < amount:
        return await msg.answer(
            f"❌ Недостаточно алмазов! У тебя: {dia} 💎"
        )

    coins = amount * 7   # 1 💎 = 7 🪙

    await state.update_data(exchange_amount=amount, exchange_coins=coins)

    confirm_txt = (
        "⚠️ <b>Подтвердите детали обмена:</b>\n\n"
        "Сумма для обмена:\n"
        f"💎 {amount}\n\n"
        "Вы получите:\n"
        f"🪙 {coins}\n\n"
        "Подтвердите продажу или отмените."
    )

    bld = InlineKeyboardBuilder()
    # Telegram не поддерживает цвет кнопок, но добавим эмодзи для наглядности
    bld.button(text="✅ Подтвердить", callback_data="b_dia_confirm")
    bld.button(text="❌ Отмена",     callback_data="b_dia_cancel")
    bld.adjust(2)

    await msg.answer(confirm_txt, reply_markup=bld.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "b_dia_confirm", DiamondExchangeState.entering_amount)
async def b_dia_confirm_cb(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    lock = _get_shop_lock(uid)

    if lock.locked():
        return await cq.answer("⏳ Транзакция в обработке...", show_alert=False)

    async with lock:
        data   = await state.get_data()
        amount = data.get("exchange_amount", 0)
        coins  = data.get("exchange_coins",  0)

        # Снова сверяем баланс прямо внутри блокировки
        u = get_user(uid)
        if u[3] < amount:
            await state.clear()
            return await cq.answer("❌ Недостаточно алмазов! Возможно, вы уже произвели обмен.", show_alert=True)

        db_exec(
            "UPDATE users SET diamond = diamond - ?, battlecoin = battlecoin + ? WHERE id = ?",
            (amount, coins, uid)
        )
        db_exec(
            "INSERT INTO diamond_exchange_log (user_id, diamonds, coins) VALUES (?, ?, ?)",
            (uid, amount, coins)
        )
        # === MANHWCARD PASS ===
        check_and_update_quests(uid, 'q_1_exchange', 1)
        await state.clear()
        try:
            await cq.message.delete()
        except:
            pass

        await cq.bot.send_message(
            uid,
            f"✅ <b>Обмен выполнен!</b>\n\n💎 -{amount} → 🪙 +{coins}\n\nПриятной игры!",
            parse_mode="HTML"
        )
    await cq.answer()

@router.callback_query(F.data == "b_dia_cancel")
async def b_dia_cancel_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cq.message.delete()
    except:
        pass
    await cq.answer("❌ Обмен отменён.", show_alert=True)
    # Возвращаем в главное меню
    await b_shop_main_cb(cq)

async def distribute_top_20_rewards(bot: Bot):
    if not GIVE_TOP_20_CARD: # Если выдача отключена - просто выходим из функции
        return 0

    # Заменили LIMIT 20 на LIMIT 15
    top_15 = db_exec("SELECT id FROM users ORDER BY season_wins DESC LIMIT 15", fetchall=True)
    count = 0
    for (uid,) in top_15:
        exists = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (uid, PACK_CARD), fetch=True)
        if not exists:
            give_card_to_user(uid, PACK_CARD)
            count += 1
            try:
                c = CARDS[PACK_CARD]
                # Изменили текст для игрока на ТОП-15
                txt = f"🏆 <b>Поздравляем!</b>\nВы вошли в ТОП-15 по победам и получаете эксклюзивную награду!\n\n" + format_card_msg(c)
                await bot.send_photo(uid, photo=FSInputFile(f"images/cards/{c['file']}"), caption=txt, parse_mode="HTML")
            except:
                pass
    return count

# Можно добавить команду админа для запуска выдачи вручную
@router.message(Command("distribute_top"))
async def cmd_distribute_top(msg: Message, bot: Bot):
    if msg.from_user.id not in ADMIN_IDS: return
    count = await distribute_top_20_rewards(bot)
    # Изменили текст для админа
    await msg.answer(f"✅ Награды выданы {count} игрокам из ТОП-15!")


async def distribute_all_top_rewards(bot: Bot):
    """Распределяет награды и карты для ТОП 150 игроков по победам"""
    top_users = db_exec("SELECT id, season_wins FROM users WHERE season_wins > 0 ORDER BY season_wins DESC LIMIT 150",
                        fetchall=True)
    count_curr, count_cards = 0, 0

    for i, (uid, wins) in enumerate(top_users):
        place = i + 1
        dia, bc = 0, 0

        if place == 1:
            dia, bc = 150, 2000
        elif place == 2:
            dia, bc = 100, 1500
        elif place == 3:
            dia, bc = 75, 1250
        elif 4 <= place <= 10:
            dia, bc = 50, 750
        elif 11 <= place <= 25:
            dia, bc = 10, 600
        elif 26 <= place <= 75:
            dia, bc = 0, 400
        elif 76 <= place <= 150:
            dia, bc = 0, 250

        if dia > 0 or bc > 0:
            db_exec("UPDATE users SET diamond = diamond + ?, battlecoin = battlecoin + ? WHERE id = ?", (dia, bc, uid))
            count_curr += 1
            try:
                await bot.send_message(uid,
                                       f"🏆 <b>Итоги сезона ТОПа!</b>\nВы заняли <b>{place}-е место</b> по победам!\n\nВаша награда: {dia} 💎, {bc} 🪙",
                                       parse_mode="HTML")
            except:
                pass

        # Изменили условие с 20 на 15
        if GIVE_TOP_20_CARD and place <= 15:
            exists = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (uid, PACK_CARD), fetch=True)
            if not exists:
                give_card_to_user(uid, PACK_CARD)
                count_cards += 1
                try:
                    c = CARDS[PACK_CARD]
                    # Изменили текст уведомления на ТОП-15
                    txt = f"🏆 <b>Поздравляем!</b>\nВы вошли в ТОП-15 по победам и получаете лимитированную карту!\n\n" + format_card_msg(
                        c)
                    await bot.send_photo(uid, photo=FSInputFile(f"images/cards/{c['file']}"), caption=txt,
                                         parse_mode="HTML")
                except:
                    pass

    return count_curr, count_cards


@router.message(Command("check_broken_cards"))
async def admin_check_broken_cards(msg: types.Message):
    """Ищет все сломанные/несуществующие ID карт в базе данных"""
    if msg.from_user.id not in ADMIN_IDS:
        return

    # Список таблиц и колонок, где хранятся ID карт
    tables_columns = {
        "cards_inv": "card_id",
        "cards_stash": "card_id",
        "decks": "card_id",
        "multi_deck_slots": "card_id",
        "favorite_cards": "card_id",
        "skins_inv": "card_id"
    }

    broken_ids = set()

    for table, col in tables_columns.items():
        try:
            # Достаем все уникальные ID из каждой таблицы
            rows = db_exec(f"SELECT DISTINCT {col} FROM {table}", fetchall=True)
            if rows:
                for row in rows:
                    cid = row[0]
                    # Если ID есть в БД, но его нет в словаре CARDS — это фантом
                    if cid and cid not in CARDS:
                        broken_ids.add(cid)
        except Exception:
            pass  # Пропускаем, если какой-то таблицы вдруг нет

    # Отдельная проверка для слотов крафта (там 5 колонок)
    try:
        craft_rows = db_exec("SELECT slot1, slot2, slot3, slot4, slot5 FROM craft_slots", fetchall=True)
        if craft_rows:
            for row in craft_rows:
                for cid in row:
                    if cid and cid not in CARDS:
                        broken_ids.add(cid)
    except Exception:
        pass

    if not broken_ids:
        return await msg.answer("✅ <b>Всё идеально!</b>\nВ базе данных нет сломанных или удаленных ID карт.",
                                parse_mode="HTML")

    txt = "⚠️ <b>Внимание! Найдены фантомные ID карт в базе:</b>\n\n"
    for bid in broken_ids:
        txt += f"<code>{bid}</code>\n"

    txt += "\nИспользуйте команду <code>/replace_card [старый_ID] [новый_ID]</code>, чтобы безопасно заменить их на актуальные."
    await msg.answer(txt, parse_mode="HTML")


@router.message(Command("replace_card"))
async def admin_replace_card(msg: types.Message):
    """Универсальная команда для безопасной замены ID карты во ВСЕХ таблицах"""
    if msg.from_user.id not in ADMIN_IDS:
        return

    args = msg.text.split()
    if len(args) != 3:
        return await msg.answer(
            "❌ <b>Формат:</b> <code>/replace_card [старый_ID] [новый_ID]</code>\n"
            "<b>Пример:</b> <code>/replace_card hwang_jae_won hwang_jae_won1</code>",
            parse_mode="HTML"
        )

    old_id = args[1]
    new_id = args[2]

    # Жесткая защита: проверяем, существует ли новый ID в игре
    if new_id not in CARDS:
        return await msg.answer(
            f"❌ <b>Ошибка:</b> Новый ID <code>{new_id}</code> не найден в словаре CARDS! Замена отменена для безопасности.",
            parse_mode="HTML")

    try:
        # Массово и безопасно обновляем все возможные таблицы
        db_exec("UPDATE cards_inv SET card_id = ? WHERE card_id = ?", (new_id, old_id))
        db_exec("UPDATE cards_stash SET card_id = ? WHERE card_id = ?", (new_id, old_id))
        db_exec("UPDATE decks SET card_id = ? WHERE card_id = ?", (new_id, old_id))
        db_exec("UPDATE multi_deck_slots SET card_id = ? WHERE card_id = ?", (new_id, old_id))

        try:
            db_exec("UPDATE favorite_cards SET card_id = ? WHERE card_id = ?", (new_id, old_id))
        except:
            pass

        try:
            db_exec("UPDATE skins_inv SET card_id = ? WHERE card_id = ?", (new_id, old_id))
        except:
            pass

        # Обновляем все 5 слотов в крафт-машине
        for i in range(1, 6):
            try:
                db_exec(f"UPDATE craft_slots SET slot{i} = ? WHERE slot{i} = ?", (new_id, old_id))
            except:
                pass

        await msg.answer(
            f"✅ <b>Успешно!</b>\nВсе записи со старой картой <code>{old_id}</code> были заменены на <code>{new_id}</code> во всех инвентарях и колодах.",
            parse_mode="HTML")
    except Exception as e:
        await msg.answer(f"❌ Произошла ошибка при обновлении БД: {e}")

@router.message(Command("reset_top"))
async def cmd_reset_top(msg: Message, bot: Bot):
    """Команда для ручного сброса сезонного ТОПа"""
    if msg.from_user.id not in ADMIN_IDS: return

    # Сбрасываем ТОЛЬКО сезонные победы
    db_exec("UPDATE users SET season_wins = 0")

    await msg.answer(
        "🧨 <b>Сезон ТОПа сброшен!</b>\n\nСезонные победы обнулены у всех игроков. Общая статистика (победы, поражения) сохранена. Начинается новая битва за первенство! 🏆",
        parse_mode="HTML")

@router.message(Command("distribute_top"))
async def cmd_distribute_top(msg: Message, bot: Bot):
    """Команда для ручной выдачи наград за ТОП"""
    if msg.from_user.id not in ADMIN_IDS: return
    count_curr, count_cards = await distribute_all_top_rewards(bot)
    await msg.answer(f"✅ Награды выданы! Игрокам выдано валют: {count_curr}, карт: {count_cards}.")


async def auto_top_distributor(bot: Bot):
    """Фоновая задача для автоматической выдачи 17-го числа в 00:00 по МСК"""
    # Создаем таймзону для Москвы (UTC+3)
    msk_tz = timezone(timedelta(hours=3))

    while True:
        # Получаем текущее время по Москве
        now_msk = datetime.now(msk_tz)

        # Проверяем: 17-е число, 00 часов, 00 минут
        if now_msk.day == 17 and now_msk.hour == 0 and now_msk.minute == 0:
            month_str = now_msk.strftime("%Y-%m")
            already = db_exec("SELECT 1 FROM user_ranks_claims WHERE claim_date = ?", (f"top_reward_{month_str}",),
                              fetch=True)

            if not already:
                # 1. Сначала распределяем награды ВСЕМ игрокам
                await distribute_all_top_rewards(bot)

                # Записываем в базу, что награды за этот месяц успешно выданы
                db_exec("INSERT INTO user_ranks_claims (user_id, claim_date) VALUES (?, ?)",
                        (0, f"top_reward_{month_str}"))

                # 2. Только ПОСЛЕ выдачи сбрасываем сезонные победы
                db_exec("UPDATE users SET season_wins = 0")

        # Проверяем раз в минуту
        await asyncio.sleep(60)


# =========================================================================
# СУНДУК — ПРОДВИНУТАЯ СИСТЕМА (ПОИСК, ФИЛЬТРЫ, СОРТИРОВКА)
# =========================================================================

STASH_PAGE_SIZE = 7  # Чуть уменьшили, чтобы влезли кнопки фильтров


class StashState(StatesGroup):
    waiting_for_search = State()


def _stash_menu_kb(uid: int, page: int = 0, source: str = "deck"):
    """Меню сундука: показывает что лежит, плюс кнопки."""
    bld = InlineKeyboardBuilder()
    bld.button(text="➕ Положить карту", callback_data=f"stash_put:0:{source}")
    bld.button(text="📤 Забрать карту", callback_data=f"stash_take:0:{source}")
    bld.button(text="Гайд 📖", callback_data=f"stash_guide:{source}")
    back_cb = "inv_main" if source == "inv" else "my_deck"
    bld.button(text="Назад 🔙", callback_data=back_cb)
    bld.adjust(1)
    return bld.as_markup()


@router.callback_query(F.data.startswith("stash_guide:"))
async def stash_guide_cb(cq: CallbackQuery):
    parts = cq.data.split(":")
    source = parts[1] if len(parts) > 1 else "deck"
    text = (
        "📖 <b>Гайд по Сундуку</b>\n\n"
        "Сундук это ваше личное хранилище. Вот как его можно использовать:\n\n"
        "📦 <b>Скрытие карт:</b> Отложенные сюда карты не будут участвовать в битвах и автосборке колоды.\n"
        "💎 <b>Хранилище редких карт:</b> Прячьте самые ценные карточки, чтобы случайно их не использовать или не потерять.\n"
        "🛡️ <b>Одинаковые карты:</b> В сундуке можно хранить максимум 2 дубликата одной карты.\n\n"
        "<i>⚠️ Важно: из сундука нельзя забрать карту, если точно такая же уже лежит в вашем активном инвентаре.</i>"
    )
    bld = InlineKeyboardBuilder()
    bld.button(text="Назад 🔙", callback_data=f"stash_menu:0:{source}")
    try:
        await cq.message.edit_text(text, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(text, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data.startswith("stash_menu:"))
async def stash_menu_cb(cq: CallbackQuery, state: FSMContext):
    # Очищаем фильтры при возврате в главное меню сундука
    await state.update_data(stash_sort="rdesc", stash_filt="all", stash_search="")

    parts = cq.data.split(":")
    page = int(parts[1]) if len(parts) > 1 else 0
    source = parts[2] if len(parts) > 2 else "deck"

    uid = cq.from_user.id
    stash = get_stash(uid)

    counts = {}
    for cid in stash:
        counts[cid] = counts.get(cid, 0) + 1

    unique_stash = list(counts.keys())

    lines = [f"📦 <b>Ваш Сундук</b>\n",
             f"Всего отложено карт: <b>{len(stash)}</b>\n"]

    if unique_stash:
        rarity_order = {"Обычная ⚪️": 1, "Редкая 🟡": 2, "Эпическая 🟢": 3,
                        "Легендарная 🔵": 4, "Мифическая 🔴": 5, "Божественная ⚫️": 6}

        c_objs = []
        for cid in unique_stash:
            c = CARDS.get(cid)
            if c:
                c_objs.append((cid, c))
            else:
                count_str = f" × {counts[cid]}" if counts[cid] > 1 else ""
                lines.append(f"• {cid}{count_str}")

        c_objs.sort(key=lambda x: rarity_order.get(x[1].get('rarity'), 0), reverse=True)

        for cid, c in c_objs:
            count_str = f" × {counts[cid]}" if counts[cid] > 1 else ""
            lines.append(f"• {c['name']} {c['rarity']}{count_str}")
    else:
        lines.append("<i>Сундук пуст.</i>")

    lines.append("\n<blockquote>Карты в Сундуке НЕ участвуют в боях и автосборе.</blockquote>")

    text = "\n".join(lines)
    try:
        await cq.message.edit_text(text, reply_markup=_stash_menu_kb(uid, page, source), parse_mode="HTML")
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(text, reply_markup=_stash_menu_kb(uid, page, source), parse_mode="HTML")
    await cq.answer()


# --- ОБЩИЕ КОНТРОЛЛЕРЫ ФИЛЬТРОВ И СОРТИРОВКИ ---

@router.callback_query(F.data.startswith("stash_sort:"))
async def stash_sort_cb(cq: CallbackQuery, state: FSMContext):
    _, action, next_sort, source = cq.data.split(":")
    await state.update_data(stash_sort=next_sort)
    await handle_stash_redirect(cq, state, action, source)
    await cq.answer()


@router.callback_query(F.data.startswith("stash_filt:"))
async def stash_filt_cb(cq: CallbackQuery, state: FSMContext):
    _, action, next_filt, source = cq.data.split(":")
    await state.update_data(stash_filt=next_filt)
    await handle_stash_redirect(cq, state, action, source)
    await cq.answer()


@router.callback_query(F.data.startswith("stash_search:"))
async def stash_search_cb(cq: CallbackQuery, state: FSMContext):
    _, action, source = cq.data.split(":")
    await state.update_data(stash_active_source=source, stash_active_action=action)
    await state.set_state(StashState.waiting_for_search)
    bld = InlineKeyboardBuilder()
    bld.button(text="Отмена", callback_data=f"stash_{action}:0:{source}")
    await cq.message.edit_text("🔍 Введите название карты (или часть имени) для поиска:", reply_markup=bld.as_markup())
    await cq.answer()


@router.callback_query(F.data.startswith("stash_clear_search:"))
async def stash_clear_search_cb(cq: CallbackQuery, state: FSMContext):
    _, action, source = cq.data.split(":")
    await state.update_data(stash_search="")
    await handle_stash_redirect(cq, state, action, source)
    await cq.answer()


@router.message(StashState.waiting_for_search)
async def stash_search_msg(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    source = data.get("stash_active_source", "deck")
    action = data.get("stash_active_action", "put")
    await state.update_data(stash_search=msg.text.strip())
    await state.set_state(None)
    bld = InlineKeyboardBuilder()
    bld.button(text="Показать результаты 🔍", callback_data=f"stash_{action}:0:{source}")
    await msg.answer(f"✅ Поиск по запросу «{msg.text.strip()}» применен!", reply_markup=bld.as_markup())


async def handle_stash_redirect(cq: CallbackQuery, state: FSMContext, action: str, source: str):
    new_cq = cq.model_copy(update={"data": f"stash_{action}:0:{source}"})
    if action == "put":
        await stash_put_cb(new_cq, state)
    else:
        await stash_take_cb(new_cq, state)


def apply_stash_filters_and_sort(items, sort_mode, filt_mode, search_q):
    filtered = []
    r_map = {"div": "Божественная", "myth": "Мифическая", "leg": "Легендарная",
             "epic": "Эпическая", "rare": "Редкая", "com": "Обычная"}

    for cid, c, cnt in items:
        # Фильтр редкости
        if filt_mode != "all" and r_map[filt_mode] not in c.get("rarity", ""):
            continue
        # Поиск по имени
        if search_q and search_q not in c.get("name", "").lower():
            continue
        filtered.append((cid, c, cnt))

    # Сортировка
    rarity_order = {"Обычная ⚪️": 1, "Редкая 🟡": 2, "Эпическая 🟢": 3,
                    "Легендарная 🔵": 4, "Мифическая 🔴": 5, "Божественная ⚫️": 6}

    if sort_mode == "rdesc":
        filtered.sort(key=lambda x: rarity_order.get(x[1].get('rarity'), 0), reverse=True)
    elif sort_mode == "rasc":
        filtered.sort(key=lambda x: rarity_order.get(x[1].get('rarity'), 0), reverse=False)
    elif sort_mode == "stdesc":
        filtered.sort(key=lambda x: (x[1].get('speed', 0) + x[1].get('strength', 0) + x[1].get('intellect', 0)),
                      reverse=True)

    return filtered


def add_stash_controls(bld: InlineKeyboardBuilder, action: str, source: str, sort_mode: str, filt_mode: str,
                       search_q: str):
    s_names = {"rdesc": "Редкость ⬇️", "rasc": "Редкость ⬆️", "stdesc": "Статы ⬇️"}
    f_names = {"all": "Все", "div": "Бож ⚫️", "myth": "Миф 🔴", "leg": "Лег 🔵", "epic": "Эпик 🟢", "rare": "Редк 🟡",
               "com": "Обыч ⚪️"}
    next_s = {"rdesc": "rasc", "rasc": "stdesc", "stdesc": "rdesc"}
    next_f = {"all": "div", "div": "myth", "myth": "leg", "leg": "epic", "epic": "rare", "rare": "com", "com": "all"}

    bld.row(
        InlineKeyboardButton(text=f"🔃 {s_names.get(sort_mode, 'Сорт')}",
                             callback_data=f"stash_sort:{action}:{next_s[sort_mode]}:{source}"),
        InlineKeyboardButton(text=f"⚡️ Фильтр: {f_names.get(filt_mode, 'Все')}",
                             callback_data=f"stash_filt:{action}:{next_f[filt_mode]}:{source}")
    )

    search_text = f"🔍 Поиск: {search_q}" if search_q else "🔍 Поиск по категориям"
    bld.row(InlineKeyboardButton(text=search_text, callback_data=f"stash_search:{action}:{source}"))
    if search_q:
        bld.row(InlineKeyboardButton(text="❌ Сбросить поиск", callback_data=f"stash_clear_search:{action}:{source}"))


# --- ПОЛОЖИТЬ КАРТУ В СУНДУК ---

@router.callback_query(F.data.startswith("stash_put:"))
async def stash_put_cb(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split(":")
    page = int(parts[1])
    source = parts[2] if len(parts) > 2 else "deck"
    uid = cq.from_user.id

    data = await state.get_data()
    sort_mode = data.get("stash_sort", "rdesc")
    filt_mode = data.get("stash_filt", "all")
    search_q = data.get("stash_search", "").lower()

    rows = db_exec("SELECT card_id, COUNT(*) FROM cards_inv WHERE user_id = ? GROUP BY card_id",
                   (uid,), fetchall=True)
    if not rows:
        # Если инвентарь пуст, обновляем сообщение, чтобы убрать лишние кнопки
        bld = InlineKeyboardBuilder()
        bld.button(text="Назад 🔙", callback_data=f"stash_menu:0:{source}")
        txt = "📥 <b>Инвентарь пуст.</b>\nУ вас нет карт для перемещения в Сундук."
        try:
            await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        except Exception:
            pass
        return await cq.answer("В инвентаре нет карт.", show_alert=False)

    items = []
    for cid, cnt in rows:
        c = CARDS.get(cid)
        if c: items.append((cid, c, cnt))

    filtered_items = apply_stash_filters_and_sort(items, sort_mode, filt_mode, search_q)

    total_pages = max(1, (len(filtered_items) + STASH_PAGE_SIZE - 1) // STASH_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = filtered_items[page * STASH_PAGE_SIZE:(page + 1) * STASH_PAGE_SIZE]

    bld = InlineKeyboardBuilder()
    for cid, c, cnt in chunk:
        emoji = c['rarity'].split()[-1] if len(c['rarity'].split()) > 1 else ""
        cnt_str = f" ({cnt} шт)" if cnt > 1 else ""
        stats = f"⚡️{c.get('speed', 0)} 💪{c.get('strength', 0)} 🧠{c.get('intellect', 0)}"
        bld.button(text=f"«{c['name']}» {emoji} | {stats}{cnt_str}",
                   callback_data=f"stash_do_put:{cid}:{page}:{source}")
    bld.adjust(1)

    add_stash_controls(bld, "put", source, sort_mode, filt_mode, search_q)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"stash_put:{page - 1}:{source}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"stash_put:{page + 1}:{source}"))
    if nav:
        bld.row(*nav)
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data=f"stash_menu:0:{source}"))

    try:
        await cq.message.edit_text("📥 <b>Инвентарь</b>\nВыберите карту, которую хотите положить в Сундук:",
                                   reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        try:
            await cq.message.delete()
        except:
            pass
        await cq.message.answer("📥 <b>Инвентарь</b>\nВыберите карту, которую хотите положить в Сундук:",
                                reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data.startswith("stash_do_put:"))
async def stash_do_put_cb(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split(":")
    cid = parts[1]
    page = parts[2]
    source = parts[3] if len(parts) > 3 else "deck"
    uid = cq.from_user.id

    in_deck = db_exec("SELECT 1 FROM decks WHERE user_id = ? AND card_id = ?", (uid, cid), fetch=True)
    if in_deck:
        return await cq.answer("Эта карта стоит в активной колоде! Уберите её сначала.", show_alert=True)

    stash_count = db_exec("SELECT COUNT(*) FROM cards_stash WHERE user_id = ? AND card_id = ?", (uid, cid), fetch=True)
    if stash_count and stash_count[0] >= 2:
        return await cq.answer("❌ В сундуке нельзя хранить больше 2-х одинаковых карт!", show_alert=True)

    ok = stash_card(uid, cid)
    if not ok:
        return await cq.answer("Карта не найдена в инвентаре.", show_alert=True)

    check_and_update_quests(uid, 'q_10_stash', 1)

    c = CARDS.get(cid, {})
    await cq.answer(f"📦 {c.get('name', cid)} → в Сундук", show_alert=False)

    new_cq = cq.model_copy(update={"data": f"stash_put:{page}:{source}"})
    await stash_put_cb(new_cq, state)


# --- ЗАБРАТЬ КАРТУ ИЗ СУНДУКА ---

@router.callback_query(F.data.startswith("stash_take:"))
async def stash_take_cb(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split(":")
    page = int(parts[1])
    source = parts[2] if len(parts) > 2 else "deck"
    uid = cq.from_user.id

    data = await state.get_data()
    sort_mode = data.get("stash_sort", "rdesc")
    filt_mode = data.get("stash_filt", "all")
    search_q = data.get("stash_search", "").lower()

    stash = get_stash(uid)
    if not stash:
        # Если сундук опустел, обновляем сообщение, чтобы кнопка последней карты пропала
        bld = InlineKeyboardBuilder()
        bld.button(text="Назад 🔙", callback_data=f"stash_menu:0:{source}")
        txt = "📤 <b>Ваш Сундук пуст.</b>\nЗдесь больше нет карт."
        try:
            await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        except Exception:
            pass
        return await cq.answer("Сундук пуст.", show_alert=False)

    counts = {}
    for cid in stash:
        counts[cid] = counts.get(cid, 0) + 1

    items = []
    for cid, cnt in counts.items():
        c = CARDS.get(cid)
        if c: items.append((cid, c, cnt))

    filtered_items = apply_stash_filters_and_sort(items, sort_mode, filt_mode, search_q)

    if not filtered_items and search_q:
        total_pages = 1
        page = 0
        chunk = []
    else:
        total_pages = max(1, (len(filtered_items) + STASH_PAGE_SIZE - 1) // STASH_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        chunk = filtered_items[page * STASH_PAGE_SIZE:(page + 1) * STASH_PAGE_SIZE]

    bld = InlineKeyboardBuilder()
    for cid, c, cnt in chunk:
        emoji = c['rarity'].split()[-1] if len(c['rarity'].split()) > 1 else ""
        cnt_str = f" (1 из {cnt})" if cnt > 1 else ""
        stats = f"⚡️{c.get('speed', 0)} 💪{c.get('strength', 0)} 🧠{c.get('intellect', 0)}"
        bld.button(text=f"«{c['name']}» {emoji} | {stats}{cnt_str}",
                   callback_data=f"stash_do_take:{cid}:{page}:{source}")
    bld.adjust(1)

    add_stash_controls(bld, "take", source, sort_mode, filt_mode, search_q)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"stash_take:{page - 1}:{source}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"stash_take:{page + 1}:{source}"))
    if nav:
        bld.row(*nav)
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data=f"stash_menu:0:{source}"))

    text = "📤 <b>Ваш Сундук</b>\nВыберите карту, которую хотите забрать:"
    if not chunk and search_q:
        text = "📤 <b>Ваш Сундук</b>\nКарты по вашему запросу не найдены."

    try:
        await cq.message.edit_text(text, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        try:
            await cq.message.delete()
        except:
            pass
        await cq.message.answer(text, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data.startswith("stash_do_take:"))
async def stash_do_take_cb(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split(":")
    cid = parts[1]
    page = parts[2]
    source = parts[3] if len(parts) > 3 else "deck"
    uid = cq.from_user.id

    existing = db_exec(
        "SELECT COUNT(*) FROM cards_inv WHERE user_id = ? AND card_id = ?",
        (uid, cid), fetch=True
    )
    if existing and existing[0] > 0:
        c = CARDS.get(cid, {})
        return await cq.answer(
            f"❌ У вас уже есть «{c.get('name', cid)}» в инвентаре!\n"
            "Сначала обменяйте или потратьте её, чтобы забрать из сундука.",
            show_alert=True
        )

    ok = unstash_card(uid, cid)
    if not ok:
        return await cq.answer("Карты нет в сундуке.", show_alert=True)

    c = CARDS.get(cid, {})
    await cq.answer(f"📤 {c.get('name', cid)} → в инвентарь", show_alert=False)

    new_cq = cq.model_copy(update={"data": f"stash_take:{page}:{source}"})
    await stash_take_cb(new_cq, state)
