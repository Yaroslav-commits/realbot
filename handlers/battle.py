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
    txt = (f"⚔️ Добро пожаловать на поле битвы!\n\n"
           f"Здесь ты можешь собрать свою колоду, сразиться с другими игроками и получать очки ранга.\n\n"
           f"Собрать свою уникальную колоду ты сможешь в разделе «Моя колода 🗂» после получения 10 боевых карт\n\n"
           f"🏅 {u[7]} Очков | Ранг {get_rank(u[7])}\n"
           f"Победа/Ничья/Поражение :\n"
           f"{u[8]}/{u[9]}/{u[10]}")
    bld = InlineKeyboardBuilder()
    bld.button(text="Найти противника 👁️", callback_data="find_match")
    bld.button(text="Дружеский бой 🔪", callback_data="friendly_match_start")
    bld.button(text="Моя колода 🗂", callback_data="my_deck")
    bld.adjust(1)
    await msg.answer(txt, reply_markup=bld.as_markup())

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
    if (now - last_b).total_seconds() < BATTLE_COOLDOWN_HOURS * 3600:
        rem = int(BATTLE_COOLDOWN_HOURS * 3600 - (now - last_b).total_seconds())
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
    if (now - last_b).total_seconds() < BATTLE_COOLDOWN_HOURS * 3600:
        rem = int(BATTLE_COOLDOWN_HOURS * 3600 - (now - last_b).total_seconds())
        await cq.answer(f"У вас кулдаун битвы: {rem // 3600}ч {(rem % 3600) // 60}м", show_alert=True)
        try:
            await cq.bot.send_message(sender_id, "У игрока кулдаун битвы. Он не может принять бой.")
        except:
            pass
        return

    u_sender = get_user(sender_id)
    last_b_s = datetime.strptime(u_sender[12], "%Y-%m-%d %H:%M:%S")
    if (now - last_b_s).total_seconds() < BATTLE_COOLDOWN_HOURS * 3600:
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
    await cq.message.edit_text("🗂 Меню колоды:\nВыберите действие:", reply_markup=bld.as_markup())


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
        media.append(types.InputMediaPhoto(media=c['file_id'], caption=txt_card))

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


@router.callback_query(F.data == "manual_deck_start")
async def manual_deck_start(cq: CallbackQuery):
    db_exec("DELETE FROM decks WHERE user_id = ?", (cq.from_user.id,))
    await cq.answer()
    await cq.message.answer("🆕 Сборка колоды начата. Выберите 6 карт по очереди.")
    await show_deck_builder(cq.message, cq.from_user.id, 1)


async def show_deck_builder(msg, uid, slot):
    if slot > 6:
        await msg.answer("✅ Колода успешно собрана!")
        return

    inv = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (uid,), fetchall=True)
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ?", (uid,), fetchall=True)
    deck_ids = [d[0] for d in deck]

    mythic_divine_cnt = sum(1 for cid in deck_ids if "Мифическая" in CARDS[cid]['rarity'] or "Божественная" in CARDS[cid]['rarity'])
    leg_cnt = sum(1 for cid in deck_ids if "Легендарная" in CARDS[cid]['rarity'])

    avail = []
    owned_counts = {}
    for (cid,) in inv:
        owned_counts[cid] = owned_counts.get(cid, 0) + 1

    for cid, count in owned_counts.items():
        if deck_ids.count(cid) >= count: continue
        if ("Мифическая" in CARDS[cid]['rarity'] or "Божественная" in CARDS[cid]['rarity']) and mythic_divine_cnt >= 1: continue
        if "Легендарная" in CARDS[cid]['rarity'] and leg_cnt >= 2: continue
        avail.append(cid)

    if not avail:
        await msg.answer(
            "❌ Недостаточно подходящих карт для завершения колоды. Вы не можете выполнить правила (максимум 1 Божественная или Мифическая, 2 Легендарные). Колода сброшена.")
        db_exec("DELETE FROM decks WHERE user_id = ?", (uid,))
        return

    bld = InlineKeyboardBuilder()
    for cid in avail[:40]:
        bld.button(text=CARDS[cid]['name'], callback_data=f"bdeck:{slot}:{cid}")
    bld.adjust(2)
    await msg.answer(f"Выберите карту для слота {slot}/6:", reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("bdeck:"))
async def bdeck_select(cq: CallbackQuery):
    _, slot, cid = cq.data.split(":")
    slot = int(slot)
    db_exec("INSERT INTO decks (user_id, card_id, slot_index) VALUES (?, ?, ?)", (cq.from_user.id, cid, slot - 1))
    await cq.answer()
    await cq.message.delete()
    await show_deck_builder(cq.message, cq.from_user.id, slot + 1)
@router.callback_query(F.data == "find_match")
async def find_match(cq: CallbackQuery):
    uid = cq.from_user.id
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ?", (uid,), fetchall=True)
    if len(deck) != 6: return await cq.answer("Соберите колоду из 6 карт!", show_alert=True)
    u = get_user(uid)
    last_b = datetime.strptime(u[12], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    if (now - last_b).total_seconds() < BATTLE_COOLDOWN_HOURS * 3600:
        rem = int(BATTLE_COOLDOWN_HOURS * 3600 - (now - last_b).total_seconds())
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
        name2, rank2 = u2[2], get_rank(u2[7])

    GAMES[gid] = {'p1': p1, 'p2': p2, 'd1': deck1.copy(), 'd2': deck2.copy(), 'n2': name2, 'r2': rank2,
                  'p1_c': None, 'p2_c': None, 'p1_s': None, 'p2_s': None, 'score1': 0, 'score2': 0, 'round': 1,
                  'friendly': friendly, 'resolving': False}

    u1 = get_user(p1)
    db_exec("UPDATE users SET last_battle = ? WHERE id IN (?, ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), p1, p2))



    txt1 = f"Противник найден!\n\n· Имя: {name2} 🧩\n· Ранг: {rank2}\n· Награда: {'0 очков' if friendly else '3 очка'}🏅, 3 BattleCoin 🪙\n\nБитва начинается!"
    await bot.send_message(p1, txt1)

    if p2 != -1:
        txt2 = f"Противник найден!\n\n· Имя: {u1[2]} 🧩\n· Ранг: {get_rank(u1[7])}\n· Награда: {'0 очков' if friendly else '3 очка'}🏅, 3 BattleCoin 🪙\n\nБитва начинается!"
        await bot.send_message(p2, txt2)

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
    msg = await bot.send_photo(uid, photo=CARDS[card]['file_id'], caption=txt, reply_markup=bld.as_markup())

    current_round = g['round']
    asyncio.create_task(auto_style_choice(gid, uid, current_round, msg.message_id, bot))

    if g['p2'] == -1 and g['p2_c'] is None:
        bot_c = random.choice(g['d2'])
        g['p2_c'] = bot_c
        g['d2'].remove(bot_c)
        g['p2_s'] = random.choice(['spd', 'str', 'int'])

        if g['p1_s'] and g['p2_s']:
            g['resolving'] = True
            await resolve_round(gid, bot)
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

    msg = await bot.send_message(uid, "Ожидание противника...")

    if is_p1:
        g['p1_wait_msg'] = msg.message_id
    else:
        g['p2_wait_msg'] = msg.message_id

    if g['p1_s'] and g['p2_s']:
        g['resolving'] = True
        try:
            if g.get('p1_wait_msg'): await bot.delete_message(g['p1'], g['p1_wait_msg'])
            if g.get('p2_wait_msg') and g['p2'] != -1: await bot.delete_message(g['p2'], g['p2_wait_msg'])
        except:
            pass
        await resolve_round(gid, bot)
        if gid in GAMES: GAMES[gid]['resolving'] = False

async def send_card_choice(uid, deck_left, gid, bot):
    bld = InlineKeyboardBuilder()
    for c in set(deck_left):
        bld.button(text=CARDS[c]['name'], callback_data=f"b_card:{gid}:{c}")
    bld.adjust(2)
    txt = f"—————————————————\n\nРаунд {GAMES[gid]['round']}.\nВыберите 🎴 Карту для атаки\n\nНа выбор дается 30 секунд"
    msg = await bot.send_message(uid, txt, reply_markup=bld.as_markup())
    asyncio.create_task(auto_card_choice(gid, uid, GAMES[gid]['round'], msg.message_id, bot))


@router.callback_query(F.data.startswith("b_card:"))
async def b_card(cq: CallbackQuery):
    _, gid, card = cq.data.split(":")
    g = GAMES.get(gid)
    if not g: return await cq.answer("Игра окончена.", show_alert=True)
    is_p1 = (cq.from_user.id == g['p1'])
    deck = g['d1'] if is_p1 else g['d2']
    if card not in deck: return await cq.answer("Эта карта уже использована!", show_alert=True)

    await cq.message.delete()
    await process_card_choice(gid, cq.from_user.id, card, cq.bot)


@router.callback_query(F.data.startswith("b_style:"))
async def b_style(cq: CallbackQuery):
    _, gid, style = cq.data.split(":")
    g = GAMES.get(gid)
    if not g: return await cq.answer("Игра окончена.", show_alert=True)

    is_p1 = (cq.from_user.id == g['p1'])
    if (is_p1 and g['p1_s'] is not None) or (not is_p1 and g['p2_s'] is not None):
        return await cq.answer("Вы уже выбрали стиль!", show_alert=True)

    await cq.message.delete()
    await process_style_choice(gid, cq.from_user.id, style, cq.bot)


async def resolve_round(gid, bot):
    g = GAMES[gid]
    c1, c2 = CARDS[g['p1_c']], CARDS[g['p2_c']]

    s_map = {'spd': ('⚡️ Скорость', '⚡️ Скоростную', 'speed'),
             'str': ('💪 Сила', '💪 Силовую', 'strength'),
             'int': ('🧠 Интеллект', '🧠 Интеллектуальную', 'intellect')}

    n1 = "Вы"
    n2 = g['n2'] if g['p2'] == -1 else get_user(g['p2'])[2]
    my_name = get_user(g['p1'])[2]

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

    if f1 > f2:
        g['score1'] += 1
        winner_name = my_name
    elif f2 > f1:
        g['score2'] += 1
        winner_name = n2
    else:
        winner_name = "Ничья"

    def format_text(p_name, e_name, score_p, score_e, p_s, e_s, p_val, e_val, p_final, e_final, b_txt):
        t = (f"⬆️ Ваша карта | Карта врага ⬆️\nРаунд - {g['round']}\n\n"
             f"Счет:\n{p_name} (я)🧩 - {score_p}\n{e_name} (противник)🧩 - {score_e}\n\n"
             f"⚔️ Вы совершаете {s_map[p_s][1]} атаку\nУровень атаки: {p_val}\n\n"
             f"🛡️ Противник ставит {s_map[e_s][1]} защиту\nУровень защиты: {e_val}\n\n")
        if adv != 0: t += f"Бонус\n{b_txt}\n\n"
        t += (f"Итоговый уровень атаки {s_map[p_s][0].split()[0]} : {p_final}\n"
              f"Итоговый уровень защиты {s_map[e_s][0].split()[0]}: {e_final}\n\n")
        t += f"Раунд завершился в ничью!" if winner_name == "Ничья" else f"Раунд выиграл {winner_name}🧩"
        return t

    txt1 = format_text(my_name, n2, g['score1'], g['score2'], g['p1_s'], g['p2_s'], val1, val2, f1, f2, bonus_txt_1)
    media1 = [types.InputMediaPhoto(media=c1['file_id'], caption=txt1), types.InputMediaPhoto(media=c2['file_id'])]
    await bot.send_media_group(g['p1'], media=media1)
    if g['p2'] != -1:
        txt2 = format_text(n2, my_name, g['score2'], g['score1'], g['p2_s'], g['p1_s'], val2, val1, f2, f1, bonus_txt_2)
        media2 = [types.InputMediaPhoto(media=c2['file_id'], caption=txt2), types.InputMediaPhoto(media=c1['file_id'])]
        await bot.send_media_group(g['p2'], media=media2)

    g['round'] += 1
    g['p1_c'] = g['p2_c'] = g['p1_s'] = g['p2_s'] = None

    if g['round'] > 5:
        await finish_game(gid, bot)
    else:
        await asyncio.sleep(2)
        await send_card_choice(g['p1'], g['d1'], gid, bot)
        if g['p2'] != -1: await send_card_choice(g['p2'], g['d2'], gid, bot)


async def finish_game(gid, bot):
    g = GAMES.pop(gid)
    p1, p2, s1, s2 = g['p1'], g['p2'], g['score1'], g['score2']
    friendly = g.get('friendly', False)

    def apply_res(uid, is_win, is_draw, friendly):
        if uid == -1: return 0, 0
        if friendly:
            pts = 0
            bc = 3 if is_win else 1
        else:
            pts = 3 if is_win else (1 if is_draw else -2)
            bc = 3 if is_win else 1

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

