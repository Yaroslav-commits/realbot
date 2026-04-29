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


# ============ ИНВЕНТАРЬ И ТРЕЙД ============
RARITY_ORDER = {"Божественная ⚫️": 6, "Мифическая 🔴": 5, "Легендарная 🔵": 4, "Эпическая 🟢": 3, "Редкая 🟡": 2, "Обычная ⚪️": 1}

@router.message(F.text == "🧳 Мои карты")
async def my_cards(msg: types.Message):
    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (msg.from_user.id,), fetchall=True)
    if not cards: return await msg.answer("У вас пока нет карт.")

    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 Карты", callback_data="inv_view:0:all")
    bld.button(text="📊 Коллекция", callback_data="inv_collection")
    bld.adjust(2)

    await msg.answer("🧳 Ваш инвентарь карт. Выберите раздел:", reply_markup=bld.as_markup())

@router.callback_query(F.data == "inv_main")
async def inv_main_cb(cq: CallbackQuery):
    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 Карты", callback_data="inv_view:0:all")
    bld.button(text="📊 Коллекция", callback_data="inv_collection")
    bld.adjust(2)
    try:
        await cq.message.edit_text("🧳 Ваш инвентарь карт. Выберите раздел:", reply_markup=bld.as_markup())
    except:
        await cq.message.delete()
        await cq.message.answer("🧳 Ваш инвентарь карт. Выберите раздел:", reply_markup=bld.as_markup())
    await cq.answer()

@router.callback_query(F.data.startswith("inv_view:"))
async def inv_view_paginated(cq: CallbackQuery):
    _, page_str, rarity_filter = cq.data.split(":")
    page = int(page_str)

    cards_db = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if not cards_db:
        return await cq.answer("У вас нет карт.", show_alert=True)

    user_cids = [row[0] for row in cards_db]

    rev_map = {
        "divine": "Божественная ⚫️",
        "mythic": "Мифическая 🔴",
        "legendary": "Легендарная 🔵",
        "epic": "Эпическая 🟢",
        "rare": "Редкая 🟡",
        "common": "Обычная ⚪️"
    }

    target_rarity = rev_map.get(rarity_filter, "all")
    if target_rarity != "all":
        user_cids = [cid for cid in user_cids if CARDS.get(cid, {}).get('rarity') == target_rarity]

    def card_power(cid):
        c = CARDS.get(cid)
        if not c: return 0
        return c.get('speed', 0) + c.get('strength', 0) + c.get('intellect', 0)

    user_cids.sort(key=lambda cid: (RARITY_ORDER.get(CARDS.get(cid, {}).get('rarity'), 0), card_power(cid)), reverse=True)

    items_per_page = 12
    total_pages = (len(user_cids) + items_per_page - 1) // items_per_page if user_cids else 1
    if page >= total_pages: page = max(0, total_pages - 1)
    if page < 0: page = 0

    start_idx = page * items_per_page
    page_cids = user_cids[start_idx:start_idx + items_per_page]

    bld = InlineKeyboardBuilder()
    bld.button(text="⚫️", callback_data="inv_view:0:divine")
    bld.button(text="🔴", callback_data="inv_view:0:mythic")
    bld.button(text="🔵", callback_data="inv_view:0:legendary")
    bld.button(text="🟢", callback_data="inv_view:0:epic")
    bld.button(text="🟡", callback_data="inv_view:0:rare")
    bld.button(text="⚪️", callback_data="inv_view:0:common")
    bld.button(text="Все", callback_data="inv_view:0:all")
    bld.adjust(7)

    card_buttons = []
    for cid in page_cids:
        c = CARDS.get(cid)
        if c:
            emoji = c['rarity'].split()[-1] if len(c['rarity'].split()) > 1 else ""
            card_buttons.append(types.InlineKeyboardButton(text=f"{c['name']} {emoji}", callback_data=f"viewcard:{cid}:{page_str}:{rarity_filter}"))

    for i in range(0, len(card_buttons), 2):
        bld.row(*card_buttons[i:i + 2])

    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"inv_view:{page - 1}:{rarity_filter}"))
    else:
        nav_row.append(types.InlineKeyboardButton(text=" ", callback_data="ignore"))

    nav_row.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))

    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton(text="Вперед ➡️", callback_data=f"inv_view:{page + 1}:{rarity_filter}"))
    else:
        nav_row.append(types.InlineKeyboardButton(text=" ", callback_data="ignore"))

    bld.row(*nav_row)
    bld.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="inv_main"))

    filter_name = rev_map.get(rarity_filter, "Все")
    txt = f"🎴 Ваши карты\nФильтр: {filter_name}"

    try:
        await cq.message.edit_text(txt, reply_markup=bld.as_markup())
    except Exception:
        await cq.message.delete()
        await cq.message.answer(txt, reply_markup=bld.as_markup())
    await cq.answer()

@router.callback_query(F.data == "inv_collection")
async def inv_collection_cb(cq: CallbackQuery):
    cards_db = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    user_owned = set([row[0] for row in cards_db])

    total_cards = len(CARDS)
    owned_total = len(user_owned)
    total_pct = int((owned_total / total_cards) * 100) if total_cards > 0 else 0

    # --- Редкости ---
    rarities = [
        ("Божественная ⚫️", "⚫️ Божественная"),
        ("Мифическая 🔴", "🔴 Мифическая"),
        ("Легендарная 🔵", "🔵 Легендарная"),
        ("Эпическая 🟢", "🟢 Эпическая"),
        ("Редкая 🟡", "🟡 Редкая"),
        ("Обычная ⚪️", "⚪️ Обычная")
    ]

    lines = [
        "📊 Коллекция собранных карт\n",
        f"Всего карт: {owned_total}/{total_cards} ({total_pct}%)\n",
        "💎 Количество карт по редкостям:"
    ]

    rarity_lines = []
    for db_rarity, disp_name in rarities:
        all_r = [cid for cid, c in CARDS.items() if c.get('rarity') == db_rarity]
        t_r = len(all_r)
        if t_r == 0:
            continue

        o_r = [cid for cid in all_r if cid in user_owned]
        o_t = len(o_r)
        pct = int((o_t / t_r) * 100) if t_r > 0 else 0

        rarity_lines.append(f"{disp_name}: {o_t}/{t_r} ({pct}%)")

    if rarity_lines:
        lines.append("<blockquote>" + "\n".join(rarity_lines) + "</blockquote>")

    # --- Вселенные ---
    series_total = {}
    for cid, c in CARDS.items():
        series = c.get('series', 'Неизвестно')
        series_total[series] = series_total.get(series, 0) + 1

    series_owned = {}
    for cid in user_owned:
        c = CARDS.get(cid)
        if c:
            series = c.get('series', 'Неизвестно')
            series_owned[series] = series_owned.get(series, 0) + 1

    sorted_series = sorted(series_total.items(), key=lambda x: x[1])

    if sorted_series:
        lines.append("\n🪐 Собранные вселленные:")
        series_lines = []
        for series, total in sorted_series:
            owned = series_owned.get(series, 0)
            series_lines.append(f"{series}: {owned}/{total}")
        lines.append("<blockquote>" + "\n".join(series_lines) + "</blockquote>")

    txt = "\n".join(lines)

    bld = InlineKeyboardBuilder()
    bld.button(text="🔙 Назад", callback_data="inv_main")

    try:
        await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except:
        await cq.message.delete()
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()



@router.callback_query(F.data.startswith("viewcard:"))
async def view_card(cq: CallbackQuery):
    parts = cq.data.split(":")
    cid = parts[1]

    page = parts[2] if len(parts) > 2 else "0"
    r_filter = parts[3] if len(parts) > 3 else "all"

    c = CARDS[cid]
    txt = f"🃏 Ваша боевая карта!\n\n🎴 Персонаж: {c['name']}\n🔮 Редкость: {c['rarity']}\n👊 Стиль боя: {c['style']}\n🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n⚡️ Скорость: {c['speed']}\n💪 Сила: {c['strength']}\n🧠 Интеллект: {c['intellect']}"

    bld = InlineKeyboardBuilder()
    bld.button(text="〽️ Трейд", callback_data=f"trade_init:{cid}")
    bld.button(text="Назад", callback_data=f"inv_view:{page}:{r_filter}")
    bld.adjust(1)

    await cq.message.delete()
    await cq.message.answer_photo(photo=c['file_id'], caption=txt, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("trade_init:"))
async def trade_init(cq: CallbackQuery, state: FSMContext):
    cid = cq.data.split(":")[1]
    await state.update_data(trade_card=cid)
    await state.set_state(TradeState.waiting_for_trade_id)

    c = CARDS[cid]
    bld = InlineKeyboardBuilder()
    bld.button(text="Отменить", callback_data="trade_cancel_init")

    await cq.message.delete()
    await cq.message.answer_photo(
        photo=c['file_id'],
        caption="⏳ Отправьте 🆔 игрока, которому хотите предложить обмен",
        reply_markup=bld.as_markup()
    )


@router.callback_query(F.data == "trade_cancel_init")
async def trade_cancel_init(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.delete()
    await cq.message.answer("Трейд отменен.", reply_markup=kb_main())


@router.message(TradeState.waiting_for_trade_id)
async def process_trade_id(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    cid = data.get('trade_card')
    if not cid:
        await state.clear()
        return

    target_id = msg.text.strip()
    if not target_id.isdigit():
        return await msg.answer("Неверный ID. Попробуйте еще раз или нажмите Отменить.")
    target_id = int(target_id)

    if target_id == msg.from_user.id:
        return await msg.answer("Нельзя трейдиться с собой.")

    u_target = get_user(target_id)
    if not u_target:
        return await msg.answer("Игрок не найден.")

    await state.clear()

    PENDING_TRADES[msg.from_user.id] = {
        'sender_card': cid,
        'receiver_id': target_id,
        'receiver_card': None
    }

    c = CARDS[cid]
    target_name = u_target[2] if u_target[2] else f"Игрок {target_id}"

    await msg.answer(f"Ваш запрос отправлен трейдеру: {target_name}")

    has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (target_id, cid), fetch=True)

    warning = " (⚠️ У вас есть эта карта!)" if has_card else ""
    caption = f"{msg.from_user.first_name} хочет с вами трейд{warning}"

    bld = InlineKeyboardBuilder()
    bld.button(text="Выбрать карту для обмена", callback_data=f"trade_p2_select:{msg.from_user.id}")
    bld.button(text="Отказаться", callback_data=f"trade_decline:{msg.from_user.id}")
    bld.adjust(1)

    try:
        await msg.bot.send_photo(
            target_id,
            photo=c['file_id'],
            caption=caption,
            reply_markup=bld.as_markup()
        )
    except Exception:
        await msg.answer("Не удалось отправить запрос. Возможно, игрок заблокировал бота.")
        PENDING_TRADES.pop(msg.from_user.id, None)


@router.callback_query(F.data.startswith("trade_p2_select:"))
async def trade_p2_select(cq: CallbackQuery):
    sender_id = int(cq.data.split(":")[1])
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.answer("Трейд не актуален или отменен.", show_alert=True)

    sender_card = t['sender_card']
    rarity = CARDS[sender_card]['rarity']

    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    valid_cards = list(set([cid for (cid,) in cards if CARDS[cid]['rarity'] == rarity]))

    if not valid_cards:
        await cq.message.delete()
        await cq.message.answer("У вас нет карт такой же редкости для обмена. Трейд отменен.")
        PENDING_TRADES.pop(sender_id, None)
        try:
            await cq.bot.send_message(sender_id, "Игрок не может принять трейд, так как у него нет подходящих карт.")
        except:
            pass
        return

    bld = InlineKeyboardBuilder()
    for cid in valid_cards[:40]:
        bld.button(text=CARDS[cid]['name'], callback_data=f"trade_p2_conf:{sender_id}:{cid}")
    bld.button(text="❌ Отказаться", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(2)

    await cq.message.delete()
    await cq.message.answer("Выбери карту, которую хочешь отдать взамен:", reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("trade_p2_conf:"))
async def trade_p2_conf(cq: CallbackQuery):
    _, sender_id, p2_card = cq.data.split(":")
    sender_id = int(sender_id)

    t = PENDING_TRADES.get(sender_id)
    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.answer("Трейд не актуален.", show_alert=True)

    t['receiver_card'] = p2_card

    c1 = CARDS[t['sender_card']]
    c2 = CARDS[p2_card]
    sender_user = get_user(sender_id)
    sender_name = sender_user[2] if sender_user else f"Игрок {sender_id}"

    await cq.message.delete()

    media = [
        types.InputMediaPhoto(media=c2['file_id']),
        types.InputMediaPhoto(media=c1['file_id'])
    ]
    await cq.message.answer_media_group(media=media)

    txt = (f"🔄 Подтверждение трейда c {sender_name}:\n\n"
           f"📤 Вы отдаете: {c2['name']}\n"
           f"📥 Вы получите: {c1['name']}\n\n"
           f"❓ Вы уверены, что хотите совершить трейд?")

    bld = InlineKeyboardBuilder()
    bld.button(text="✅ Подтвердить", callback_data=f"trade_p2_final:{sender_id}")
    bld.button(text="❌ Отказаться", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(2)

    await cq.message.answer(txt, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("trade_p2_final:"))
async def trade_p2_final(cq: CallbackQuery):
    sender_id = int(cq.data.split(":")[1])
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.answer("Трейд не актуален.", show_alert=True)

    await cq.message.edit_text("✅ Ожидание подтверждения от инициатора...")

    c1 = CARDS[t['sender_card']]
    c2 = CARDS[t['receiver_card']]

    has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, t['receiver_card']),
                       fetch=True)

    warning = " (⚠️ У вас есть эта карта)" if has_card else ""
    p2_name = cq.from_user.first_name

    txt = (f"🔄 💫 {p2_name} предлагает вам трейд:\n\n"
           f"📤 Вы отдаете: {c1['name']}\n"
           f"📥 Вы получите: {c2['name']}{warning}\n\n"
           f"❓ Вы уверены, что хотите совершить трейд?")

    media = [
        types.InputMediaPhoto(media=c1['file_id']),
        types.InputMediaPhoto(media=c2['file_id'])
    ]

    bld = InlineKeyboardBuilder()
    bld.button(text="✅ Согласиться", callback_data=f"trade_p1_final:{cq.from_user.id}")
    bld.button(text="❌ Отказаться", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(2)

    try:
        await cq.bot.send_media_group(sender_id, media=media)
        await cq.bot.send_message(sender_id, txt, reply_markup=bld.as_markup())
    except Exception:
        await cq.message.answer("Не удалось связаться с инициатором. Трейд отменен.")
        PENDING_TRADES.pop(sender_id, None)


@router.callback_query(F.data.startswith("trade_p1_final:"))
async def trade_p1_final(cq: CallbackQuery):
    p2_id = int(cq.data.split(":")[1])
    sender_id = cq.from_user.id
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != p2_id:
        return await cq.answer("Трейд не актуален.", show_alert=True)

    c1_id = t['sender_card']
    c2_id = t['receiver_card']

    p1_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c1_id), fetch=True)
    p2_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c2_id), fetch=True)

    if not p1_has or not p2_has:
        PENDING_TRADES.pop(sender_id, None)
        await cq.message.edit_text("❌ Трейд сорвался: у одного из игроков больше нет нужной карты.")
        try:
            await cq.bot.send_message(p2_id, "❌ Трейд сорвался: у одного из игроков больше нет нужной карты.")
        except:
            pass
        return

    # Удаляем отданные карты из инвентаря
    db_exec("DELETE FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c1_id))
    db_exec("DELETE FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c2_id))

    # Проверка на наличие карты, которую они сейчас получают
    p1_has_c2 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c2_id), fetch=True)
    p2_has_c1 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c1_id), fetch=True)

    # Записываем только в том случае, если дубликата нет (иначе сгорает)
    if not p1_has_c2:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (sender_id, c2_id))

    if not p2_has_c1:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (p2_id, c1_id))
    PENDING_TRADES.pop(sender_id, None)

    await cq.message.delete()
    await cq.message.answer_photo(
        photo=CARDS[c2_id]['file_id'],
        caption="✅ Получена карта с трейда"
    )

    try:
        await cq.bot.send_photo(
            p2_id,
            photo=CARDS[c1_id]['file_id'],
            caption="✅ Получена карта с трейда"
        )
    except:
        pass


@router.callback_query(F.data.startswith("trade_decline:"))
async def trade_decline(cq: CallbackQuery):
    sender_id = int(cq.data.split(":")[1])
    t = PENDING_TRADES.pop(sender_id, None)

    await cq.message.delete()
    await cq.message.answer("❌ Трейд отменен.")

    other_id = sender_id if cq.from_user.id != sender_id else (t['receiver_id'] if t else None)
    if other_id:
        try:
            await cq.bot.send_message(other_id, "❌ Игрок отказался от трейда (или трейд отменен).")
        except:
            pass


@router.callback_query(F.data == "ignore")
async def ignore_cb(cq: CallbackQuery):
    await cq.answer()

