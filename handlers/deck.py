import os
import asyncio
import logging
import sqlite3
import random
import calendar
from datetime import datetime, timedelta
from html import escape
import base64
from urllib.parse import quote

from aiogram import Bot, F, types
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           CallbackQuery, LabeledPrice, PreCheckoutQuery,
                           FSInputFile)
from aiogram.filters import Command, StateFilter, CommandStart, CommandObject
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (BOT_TOKEN, ADMIN_IDS, DB_PATH,
                    GET_COOLDOWN_HOURS, BATTLE_COOLDOWN_HOURS,
                    MAIN_PRIZE_NORMAL_TITLE, MAIN_PRIZE_ROYALE_CARD)
from data.cards import (CARDS, RARITIES, BGS, VIDEO_BGS, TITLES,
                        NORMAL_PASS, ROYALE_PASS, is_divine,
                        AWAKENED_SKIN, ABSOLUTE_SKIN,
    COPY_STYLE, RISE_STYLE, BERSERK_STYLE,
    SPACE_STYLE, PIERCE_STYLE, EVADE_STYLE)
from database.db import (db_exec, init_db, get_user, add_user, get_rank,
                         pull_random_card, give_card_to_user,
                         get_active_skin, get_user_skins_for_card, equip_skin, get_all_user_skins_by_type, swap_skins, unequip_skin,
                         check_and_update_quests, notify_pass_levelup)
from handlers import (router, TradeState, SettingsState, PromoState,
                      MATCH_QUEUE, GAMES, PENDING_TRADES, kb_main)
from media_cache import send_cached_video
from config import is_owner

# ============ ИНВЕНТАРЬ И ТРЕЙД ============
RARITY_ORDER = {
    "Божественная ⚫️": 6,
    "Мифическая 🔴": 5,
    "Легендарная 🔵": 4,
    "Эпическая 🟢": 3,
    "Редкая 🟡": 2,
    "Обычная ⚪️": 1
}

RARITY_FILTERS = [
    ("⚫️", "divine",      "Божественная ⚫️"),
    ("🔴", "mythic",      "Мифическая 🔴"),
    ("🔵", "legendary",   "Легендарная 🔵"),
    ("🟢", "epic",        "Эпическая 🟢"),
    ("🟡", "rare",        "Редкая 🟡"),
    ("⚪️", "common",     "Обычная ⚪️"),
]

SKILL_MAP = {
    # 👁️ Копирование
    "копирование": COPY_STYLE,
    "👁️ копирование": COPY_STYLE,
    "👁 копирование": COPY_STYLE,
    "копирование 👁️": COPY_STYLE,
    "копирование 👁": COPY_STYLE,
    "👁️": COPY_STYLE,
    "👁": COPY_STYLE,

    # 🌑 Восстание
    "восстание": RISE_STYLE,
    "🌑 восстание": RISE_STYLE,
    "восстание 🌑": RISE_STYLE,
    "🌑": RISE_STYLE,

    # 🩸 Берсерк
    "берсерк": BERSERK_STYLE,
    "🩸 берсерк": BERSERK_STYLE,
    "берсерк 🩸": BERSERK_STYLE,
    "🩸": BERSERK_STYLE,

    # 🌊 Пространство
    "пространство": SPACE_STYLE,
    "🌊 пространство": SPACE_STYLE,
    "пространство 🌊": SPACE_STYLE,
    "🌊": SPACE_STYLE,

    # ⚔️ Пробивание
    "пробивание": PIERCE_STYLE,
    "⚔️ пробивание": PIERCE_STYLE,
    "⚔ пробивание": PIERCE_STYLE,
    "пробивание ⚔️": PIERCE_STYLE,
    "пробивание ⚔": PIERCE_STYLE,
    "⚔️": PIERCE_STYLE,
    "⚔": PIERCE_STYLE,

    # 🌪 Уклонение
    "уклонение": EVADE_STYLE,
    "🌪 уклонение": EVADE_STYLE,
    "уклонение 🌪": EVADE_STYLE,
    "🌪": EVADE_STYLE,
}
RARITY_SLUG_TO_LABEL = {slug: label for _, slug, label in RARITY_FILTERS}

class SearchState(StatesGroup):
    waiting_for_query = State()

def _card_power(cid: str) -> int:
    c = CARDS.get(cid)
    if not c:
        return 0
    return c.get('speed', 0) + c.get('strength', 0) + c.get('intellect', 0)


def _get_user_cids(uid: int, include_stash: bool = False) -> list[str]:
    """Возвращает уникальные card_id из инвентаря. Опционально - вместе с сундуком для коллекции."""
    rows = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (uid,), fetchall=True)

    if include_stash:
        stash_rows = db_exec("SELECT card_id FROM cards_stash WHERE user_id = ?", (uid,), fetchall=True)
        rows = rows + stash_rows

    seen = set()
    result = []
    for (cid,) in rows:
        if cid not in seen:
            seen.add(cid)
            result.append(cid)
    return result


def _apply_filter(cids: list[str], rarity_filter: str, excl_filter: int = 0) -> list[str]:
    from data.cards import EVENT_CARDS_LIST
    filtered = cids
    if rarity_filter != "all":
        label = RARITY_SLUG_TO_LABEL.get(rarity_filter)
        if label:
            filtered = [cid for cid in filtered if CARDS.get(cid, {}).get('rarity') == label]

    if excl_filter == 1:
        # Лимитированные (exclusive=True, но НЕ ивентовые)
        filtered = [cid for cid in filtered if
                    CARDS.get(cid, {}).get('exclusive', False) and cid not in EVENT_CARDS_LIST]
    elif excl_filter == 2:
        # Только Ивентовые
        filtered = [cid for cid in filtered if cid in EVENT_CARDS_LIST]

    return filtered

def _sort_cards(cids: list[str]) -> list[str]:
    return sorted(
        cids,
        key=lambda cid: (RARITY_ORDER.get(CARDS.get(cid, {}).get('rarity', ''), 0), _card_power(cid)),
        reverse=True
    )
def _build_inv_main_text(uid: int) -> str:
    all_cids = _get_user_cids(uid)
    total = len(all_cids)
    total_all = len(CARDS)

    lines = [
        "<tg-emoji emoji-id='5438154974490022622'>🌌</tg-emoji> <b>ГЛАВНОЕ МЕНЮ ИНВЕНТАРЯ</b> <tg-emoji emoji-id='5438154974490022622'>🌌</tg-emoji>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"<tg-emoji emoji-id='5231200819986047254'>📦</tg-emoji> <b>Коллекция:</b> {total} / {total_all} карт",
        "",
        "<tg-emoji emoji-id='5201914481671682382'>⚜️</tg-emoji> <b>Распределение по редкости:</b>"
    ]
    for _, slug, label in RARITY_FILTERS:
        count = sum(1 for cid in all_cids if CARDS.get(cid, {}).get('rarity') == label)
        if count:
            lines.append(f"  └ {label}: <b>{count}</b> шт.")

    if total:
        top_cids = _sort_cards(all_cids)[:3]
        lines.append("")
        lines.append("<tg-emoji emoji-id='5258203794772085854'>⚡️</tg-emoji> <b>Авангард (Топ-3):</b>")
        for i, cid in enumerate(top_cids, 1):
            c = CARDS.get(cid)
            if c:
                power = _card_power(cid)
                lines.append(f"  {i}. {c['name']} — 💥 {power}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i><tg-emoji emoji-id='5296773623292388914'>👇</tg-emoji> Выберите нужное действие:</i>")
    return "\n".join(lines)

def _build_inv_main_kb() -> InlineKeyboardMarkup:
    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 Просмотр карт",    callback_data="inv_view:0:all:0")
    bld.button(text="🔍 Поиск по категориям", callback_data="inv_search_start")
    bld.button(text="📊 Коллекция",         callback_data="inv_collection")
    bld.button(text="📦 Сундук",            callback_data="stash_menu:0:inv")
    bld.row(InlineKeyboardButton(text="Мои скины 🏵️", callback_data="my_skins_categories"))
    bld.adjust(1,1,2,1)
    return bld.as_markup()

@router.message(F.text == "🧳 Мои Карты")
async def my_cards(msg: types.Message, state: FSMContext):
    await state.clear()
    cids = _get_user_cids(msg.from_user.id)
    if not cids:
        return await msg.answer(
            "🧳 <b>Мои Карты</b>\n\nУ вас пока нет карт. Попробуйте получить их через крутку!",
            parse_mode="HTML"
        )
    await msg.answer(
        _build_inv_main_text(msg.from_user.id),
        parse_mode="HTML",
        reply_markup=_build_inv_main_kb()
    )

@router.callback_query(F.data == "inv_main")
async def inv_main_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    cids = _get_user_cids(cq.from_user.id)
    if not cids:
        text = "🧳 <b>Мои Карты</b>\n\nУ вас пока нет карт."
        kb = None
    else:
        text = _build_inv_main_text(cq.from_user.id)
        kb = _build_inv_main_kb()

    try:
        await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await cq.message.delete()
        await cq.message.answer(text, parse_mode="HTML", reply_markup=kb)
    await cq.answer()


# ── Продвинутый Поиск ──────────────────────────────────────────────────

class SearchState(StatesGroup):
    waiting_for_name = State()
    waiting_for_speed = State()
    waiting_for_strength = State()
    waiting_for_intellect = State()


@router.callback_query(F.data == "inv_search_start")
async def inv_search_menu(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    bld = InlineKeyboardBuilder()
    bld.button(text="🔤 По названию", callback_data="inv_search_name")
    bld.button(text="🪐 По вселенной", callback_data="inv_search_series")
    bld.button(text="📊 По статам", callback_data="inv_search_stats")
    bld.button(text="✨ По навыку", callback_data="inv_search_skill")
    bld.button(text="🔙 Назад", callback_data="inv_main")
    bld.adjust(1, 1, 2, 1)

    txt = "🔍 <b>Продвинутый поиск</b>\n\nВыберите, по какому критерию вы хотите найти свои карты:"

    try:
        await cq.message.edit_text(txt, parse_mode="HTML", reply_markup=bld.as_markup())
    except Exception:
        await cq.message.delete()
        await cq.message.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())
    await cq.answer()


@router.callback_query(F.data == "inv_search_name")
async def inv_search_name_start(cq: CallbackQuery, state: FSMContext):
    await state.set_state(SearchState.waiting_for_name)
    bld = InlineKeyboardBuilder()
    bld.button(text="❌ Отмена", callback_data="inv_search_start")
    try:
        await cq.message.edit_text("🔍 <b>Поиск по названию</b>\n\nВведите название карты (или его часть):",
                                   reply_markup=bld.as_markup(), parse_mode="HTML")
    except:
        await cq.message.answer("🔍 <b>Поиск по названию</b>\n\nВведите название карты (или его часть):",
                                reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


# --- ПОИСК ПО ВСЕЛЕННОЙ (КНОПКИ) ---
@router.callback_query(F.data == "inv_search_series")
async def inv_search_series_start(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await _render_series_page(cq, 0)


@router.callback_query(F.data.startswith("inv_search_series_page:"))
async def inv_search_series_page(cq: CallbackQuery):
    page = int(cq.data.split(":")[1])
    await _render_series_page(cq, page)


async def _render_series_page(cq: CallbackQuery, page: int):
    # Собираем уникальные названия вселенных из всех существующих карт
    all_series = sorted(list(set(c.get('series', 'Неизвестно') for c in CARDS.values() if c.get('series'))))

    items_per_page = 10
    total_pages = max(1, (len(all_series) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * items_per_page
    page_series = all_series[start:start + items_per_page]

    bld = InlineKeyboardBuilder()
    for s in page_series:
        # Лимит callback_data в Telegram 64 байта, поэтому безопасно обрезаем
        safe_s = s[:40]
        bld.button(text=s, callback_data=f"inv_s_sel:{safe_s}")

    bld.adjust(1)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"inv_search_series_page:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"inv_search_series_page:{page + 1}"))

    if nav:
        bld.row(*nav)

    bld.row(InlineKeyboardButton(text="❌ Отмена", callback_data="inv_search_start"))

    txt = "🪐 <b>Поиск по вселенной</b>\n\nВыберите нужную вселенную из списка ниже:"
    try:
        await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except:
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data.startswith("inv_s_sel:"))
async def inv_series_selected(cq: CallbackQuery, state: FSMContext):
    query = cq.data.split(":", 1)[1].lower()
    await state.update_data(search_type="series", search_query=query)
    await _process_search_and_show(cq, state, page=0)


@router.callback_query(F.data == "inv_search_skill")
async def inv_search_skill_start(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    bld = InlineKeyboardBuilder()
    skills = [
        ("👁️ Копирование", "Копирование"),
        ("🌑 Восстание", "Восстание"),
        ("🩸 Берсерк", "Берсерк"),
        ("🌊 Пространство", "Пространство"),
        ("⚔️ Пробивание", "Пробивание"),
        ("🌪 Уклонение", "Уклонение"),
        ("⚪ Базовый", "Базовый")  # Для карт без навыка
    ]

    for label, slug in skills:
        bld.button(text=label, callback_data=f"inv_sk_sel:{slug}")

    bld.adjust(2)  # Выстраиваем кнопки в 2 столбца
    bld.row(InlineKeyboardButton(text="🔙 Назад", callback_data="inv_search_start"))

    txt = "✨ <b>Поиск по навыкам</b>\n\nВыберите интересующий вас стиль боя:"
    try:
        await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except:
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data.startswith("inv_sk_sel:"))
async def inv_skill_selected(cq: CallbackQuery, state: FSMContext):
    query = cq.data.split(":", 1)[1]
    await state.update_data(search_type="skill", search_query=query)
    await _process_search_and_show(cq, state, page=0)

# --- ПОИСК ПО СТАТАМ ---
@router.callback_query(F.data == "inv_search_stats")
async def inv_search_stats_start(cq: CallbackQuery, state: FSMContext):
    await state.set_state(SearchState.waiting_for_speed)
    bld = InlineKeyboardBuilder()
    bld.button(text="❌ Отмена", callback_data="inv_search_start")
    try:
        await cq.message.edit_text(
            "⚡️ <b>Поиск по статам: Шаг 1/3</b>\n\nВведите целевое значение <b>Скорости</b> (или отправьте 0, если не важно):",
            reply_markup=bld.as_markup(), parse_mode="HTML")
    except:
        pass
    await cq.answer()


@router.message(StateFilter(SearchState.waiting_for_name))
async def inv_search_name_msg(msg: types.Message, state: FSMContext):
    query = (msg.text or "").strip().lower()
    if not query:
        return await msg.answer("Пустой запрос. Попробуйте ещё раз.")
    await state.update_data(search_type="name", search_query=query)
    await state.set_state(None)
    await _process_search_and_show(msg, state, page=0)


@router.message(StateFilter(SearchState.waiting_for_speed))
async def inv_search_speed_msg(msg: types.Message, state: FSMContext):
    val = msg.text.strip()
    if not val.isdigit(): return await msg.answer("Пожалуйста, введите число (0 или больше).")
    await state.update_data(min_spd=int(val))
    await state.set_state(SearchState.waiting_for_strength)
    bld = InlineKeyboardBuilder()
    bld.button(text="❌ Отмена", callback_data="inv_search_start")
    await msg.answer("💪 <b>Поиск по статам: Шаг 2/3</b>\n\nВведите целевое значение <b>Силы</b> (или отправьте 0):",
                     reply_markup=bld.as_markup(), parse_mode="HTML")


@router.message(StateFilter(SearchState.waiting_for_strength))
async def inv_search_strength_msg(msg: types.Message, state: FSMContext):
    val = msg.text.strip()
    if not val.isdigit(): return await msg.answer("Пожалуйста, введите число (0 или больше).")
    await state.update_data(min_str=int(val))
    await state.set_state(SearchState.waiting_for_intellect)
    bld = InlineKeyboardBuilder()
    bld.button(text="❌ Отмена", callback_data="inv_search_start")
    await msg.answer(
        "🧠 <b>Поиск по статам: Шаг 3/3</b>\n\nВведите целевое значение <b>Интеллекта</b> (или отправьте 0):",
        reply_markup=bld.as_markup(), parse_mode="HTML")


@router.message(StateFilter(SearchState.waiting_for_intellect))
async def inv_search_intellect_msg(msg: types.Message, state: FSMContext):
    val = msg.text.strip()
    if not val.isdigit(): return await msg.answer("Пожалуйста, введите число (0 или больше).")
    await state.update_data(min_int=int(val), search_type="stats")
    await state.set_state(None)
    await _process_search_and_show(msg, state, page=0)


async def _process_search_and_show(target, state: FSMContext, page: int):
    uid = target.from_user.id
    data = await state.get_data()
    search_type = data.get("search_type")

    user_cids = _get_user_cids(uid)
    matched = []

    if search_type == "name":
        query = data.get("search_query", "")
        matched = [cid for cid in user_cids if query in CARDS.get(cid, {}).get('name', '').lower()]
        matched = _sort_cards(matched)
        info_txt = f"по названию «<b>{query}</b>»"

    elif search_type == "series":
        query = data.get("search_query", "")
        matched = [cid for cid in user_cids if query in CARDS.get(cid, {}).get('series', 'неизвестно').lower()]
        matched = _sort_cards(matched)
        info_txt = f"по вселенной «<b>{query.title()}</b>»"

    elif search_type == "stats":
        spd = data.get("min_spd", 0)
        str_ = data.get("min_str", 0)
        int_ = data.get("min_int", 0)

        def stat_distance(cid):
            c = CARDS.get(cid, {})
            dist = 0
            # Считаем разницу только для статов, которые игрок указал (> 0)
            if spd > 0: dist += abs(c.get('speed', 0) - spd)
            if str_ > 0: dist += abs(c.get('strength', 0) - str_)
            if int_ > 0: dist += abs(c.get('intellect', 0) - int_)
            return dist

        matched = list(user_cids)
        # Сортируем: сначала самое близкое совпадение (dist ближе к 0), при равенстве - по наибольшей силе
        matched.sort(key=lambda cid: (stat_distance(cid), -_card_power(cid)))
        info_txt = f"по статам (Ближе к ⚡️{spd} 💪{str_} 🧠{int_})"


    elif search_type == "skill":

        query = data.get("search_query", "")

        query_lower = query.lower().strip()

        # Обработка кнопки "Базовый" (карты без навыков)

        if query_lower == "базовый":

            # Собираем все ID карт с навыками в одну кучу

            all_skill_ids = set(

                COPY_STYLE + RISE_STYLE + BERSERK_STYLE +

                SPACE_STYLE + PIERCE_STYLE + EVADE_STYLE

            )

            # Оставляем только те карты, которых НЕТ в списке навыков

            matched = [cid for cid in user_cids if cid not in all_skill_ids]

            info_txt = "без навыка (Базовые карты)"


        # Обработка конкретного навыка

        elif query_lower in SKILL_MAP:

            target_ids = SKILL_MAP[query_lower]

            matched = [cid for cid in user_cids if cid in target_ids]

            info_txt = f"по навыку «<b>{query}</b>»"


        else:

            matched = []

            info_txt = "по неизвестному навыку"

        matched = _sort_cards(matched)

    else:
        return

    if not matched:
        bld = InlineKeyboardBuilder()
        bld.button(text="🔙 К поиску", callback_data="inv_search_start")
        txt = f"🔍 Карт {info_txt} не найдено."
        if isinstance(target, types.Message):
            await target.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())
        else:
            try:
                await target.message.edit_text(txt, parse_mode="HTML", reply_markup=bld.as_markup())
            except:
                await target.message.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())
        return

    items_per_page = 12
    total_pages = max(1, (len(matched) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * items_per_page
    page_cids = matched[start:start + items_per_page]

    bld = InlineKeyboardBuilder()
    for cid in page_cids:
        c = CARDS.get(cid)
        if c:
            emoji = c['rarity'].split()[-1] if len(c['rarity'].split()) > 1 else ""
            c_spd = c.get('speed', 0)
            c_str = c.get('strength', 0)
            c_int = c.get('intellect', 0)

            bld.row(types.InlineKeyboardButton(
                text=f"{c['name']} {emoji} · ⚡️{c_spd} 💪{c_str} 🧠{c_int}",
                callback_data=f"viewcard:{cid}:{page}:all:0"
            ))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"inv_search_page:{page - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"inv_search_page:{page + 1}"))
    if nav:
        bld.row(*nav)

    bld.row(types.InlineKeyboardButton(text="🔍 Новый поиск", callback_data="inv_search_start"))
    bld.row(types.InlineKeyboardButton(text="🔙 В инвентарь", callback_data="inv_main"))

    txt = f"🔍 <b>Результаты {info_txt}</b>\nНайдено карт: <b>{len(matched)}</b>"

    if isinstance(target, types.Message):
        await target.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())
    else:
        try:
            await target.message.edit_text(txt, parse_mode="HTML", reply_markup=bld.as_markup())
        except:
            await target.message.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("inv_search_page:"))
async def inv_search_page_cb(cq: CallbackQuery, state: FSMContext):
    page = int(cq.data.split(":")[1])
    await _process_search_and_show(cq, state, page=page)
    await cq.answer()

@router.callback_query(F.data.startswith("inv_view:"))
async def inv_view_paginated(cq: CallbackQuery):
    parts = cq.data.split(":")
    page = int(parts[1])
    rarity_filter = parts[2] if len(parts) > 2 else "all"
    excl_filter = int(parts[3]) if len(parts) > 3 else 0

    all_cids = _get_user_cids(cq.from_user.id)
    if not all_cids:
        return await cq.answer("У вас нет карт.", show_alert=True)

    filtered = _apply_filter(all_cids, rarity_filter, excl_filter)
    sorted_cids = _sort_cards(filtered)

    items_per_page = 12
    total_pages = max(1, (len(sorted_cids) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * items_per_page
    page_cids = sorted_cids[start:start + items_per_page]

    bld = InlineKeyboardBuilder()

    # ── Строка фильтров редкости ──
    from data.cards import EVENT_CARDS_LIST
    filter_row = []
    for emoji, slug, _ in RARITY_FILTERS:
        # Считаем количество с учетом активного фильтра лимиток/ивента
        if excl_filter == 1:
            count = sum(1 for cid in all_cids if
                        CARDS.get(cid, {}).get('rarity') == RARITY_SLUG_TO_LABEL[slug] and CARDS.get(cid, {}).get(
                            'exclusive', False) and cid not in EVENT_CARDS_LIST)
        elif excl_filter == 2:
            count = sum(1 for cid in all_cids if
                        CARDS.get(cid, {}).get('rarity') == RARITY_SLUG_TO_LABEL[slug] and cid in EVENT_CARDS_LIST)
        else:
            count = sum(1 for cid in all_cids if CARDS.get(cid, {}).get('rarity') == RARITY_SLUG_TO_LABEL[slug])

        active = "›" if slug == rarity_filter else ""
        btn_text = f"{active}{emoji}{count}{active}" if count else f"{emoji}—"
        filter_row.append(types.InlineKeyboardButton(
            text=btn_text,
            callback_data=f"inv_view:0:{slug}:{excl_filter}"
        ))

    all_mark = "›" if rarity_filter == "all" else ""
    filter_row.append(types.InlineKeyboardButton(
        text=f"{all_mark}Все{all_mark}",
        callback_data=f"inv_view:0:all:{excl_filter}"
    ))
    bld.row(*filter_row)

    # ── Кнопка-переключатель Лимиток / Ивентовых ──
    if excl_filter == 0:
        excl_text = "✨ Лимитированные: ВЫКЛ"
        new_excl = 1
    elif excl_filter == 1:
        excl_text = "✨ Лимитированные: ВКЛ"
        new_excl = 2
    elif excl_filter == 2:
        excl_text = "❓ Ивентовые: ВКЛ"
        new_excl = 0

    bld.row(types.InlineKeyboardButton(text=excl_text, callback_data=f"inv_view:0:{rarity_filter}:{new_excl}"))

    # ── Карточки ──
    card_buttons = []
    for cid in page_cids:
        c = CARDS.get(cid)
        if c:
            emoji = c['rarity'].split()[-1] if len(c['rarity'].split()) > 1 else ""
            power = _card_power(cid)
            # Отмечаем карту нужным значком (❓ для ивента, ✨ для лимиток)
            is_excl = "❓" if cid in EVENT_CARDS_LIST else ("✨" if c.get('exclusive') else "")
            card_buttons.append(types.InlineKeyboardButton(
                text=f"{is_excl}{c['name']} {emoji}",
                callback_data=f"viewcard:{cid}:{page}:{rarity_filter}:{excl_filter}"
            ))

    for i in range(0, len(card_buttons), 2):
        bld.row(*card_buttons[i:i + 2])

    # ── Навигация ──
    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(
            text="⬅️", callback_data=f"inv_view:{page - 1}:{rarity_filter}:{excl_filter}"
        ))
    else:
        nav_row.append(types.InlineKeyboardButton(text="⬅️", callback_data="ignore"))

    nav_row.append(types.InlineKeyboardButton(
        text=f"📄 {page + 1} / {total_pages}", callback_data="ignore"
    ))

    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton(
            text="➡️", callback_data=f"inv_view:{page + 1}:{rarity_filter}:{excl_filter}"
        ))
    else:
        nav_row.append(types.InlineKeyboardButton(text="➡️", callback_data="ignore"))

    bld.row(*nav_row)

    # ── Нижние кнопки ──
    bld.row(
        types.InlineKeyboardButton(text="🔍 Поиск", callback_data="inv_search_start"),
        types.InlineKeyboardButton(text="🔙 Назад", callback_data="inv_main")
    )
    bld.row(types.InlineKeyboardButton(text="📊 Коллекция", callback_data="inv_collection"))

    filter_label = RARITY_SLUG_TO_LABEL.get(rarity_filter, "Все")
    if excl_filter == 1:
        excl_label = " + ✨ Лимитки"
    elif excl_filter == 2:
        excl_label = " + ❓ Ивентовые"
    else:
        excl_label = ""
    shown = len(sorted_cids)
    txt = (
        f"🎴 <b>Мои Карты</b>\n"
        f"Фильтр: {filter_label}{excl_label} · {shown} карт\n"
        f"Сортировка: по редкости + силе ⬇️"
    )

    try:
        await cq.message.edit_text(txt, parse_mode="HTML", reply_markup=bld.as_markup())
    except Exception:
        await cq.message.delete()
        await cq.message.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())
    await cq.answer()

# ── Коллекция ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "inv_collection")
async def inv_collection_cb(cq: CallbackQuery):
    # Добавляем include_stash=True, чтобы в прогресс коллекции шли и карты из сундука
    user_cids = _get_user_cids(cq.from_user.id, include_stash=True)
    user_owned = set(user_cids)

    total_cards = len(CARDS)
    owned_total = len(user_owned)
    total_pct = int((owned_total / total_cards) * 100) if total_cards else 0

    # Прогресс-бар
    filled = total_pct // 10
    bar = "█" * filled + "░" * (10 - filled)

    lines = [
        "<tg-emoji emoji-id='5231200819986047254'>📦</tg-emoji> <b>Коллекция</b>",
        "",
        f"Прогресс: [{bar}] {total_pct}%",
        f"Собрано: <b>{owned_total}</b> / {total_cards} карт",
        "",
        "<tg-emoji emoji-id='5201914481671682382'>⚜️</tg-emoji> <b>По редкостям:</b>",
        "<blockquote>"
    ]
    rarity_lines = []
    for _, slug, label in RARITY_FILTERS:
        all_r = [cid for cid, c in CARDS.items() if c.get('rarity') == label]
        t_r = len(all_r)
        if not t_r:
            continue
        o_t = sum(1 for cid in all_r if cid in user_owned)
        pct = int((o_t / t_r) * 100)
        r_bar_f = pct // 10
        r_bar = "█" * r_bar_f + "░" * (10 - r_bar_f)
        rarity_lines.append(f"{label}: {o_t}/{t_r}  [{r_bar}] {pct}%")

    lines.append("\n".join(rarity_lines))
    lines.append("</blockquote>")

    # === ВСЕЛЕННЫЕ (Скрываемые / Только с собранными картами) ===
    series_map: dict[str, dict] = {}
    for cid, c in CARDS.items():
        s = c.get('series', 'Неизвестно')
        series_map.setdefault(s, {'total': 0, 'owned': 0})
        series_map[s]['total'] += 1
        if cid in user_owned:
            series_map[s]['owned'] += 1

    sorted_series = sorted(series_map.items(), key=lambda x: x[1]['owned'], reverse=True)

    # Используем expandable цитату (свёрнута по умолчанию)
    lines += ["", "<tg-emoji emoji-id='5211138022724091185'>🪐</tg-emoji> <b>Вселенные (нажми, чтобы развернуть):</b>", "<blockquote expandable>"]
    series_lines = []

    for s_name, s_data in sorted_series:
        # 1. Показываем ТОЛЬКО те вселенные, у которых открыта хотя бы 1 карта
        if s_data['owned'] == 0:
            continue

        pct_s = int((s_data['owned'] / s_data['total']) * 100) if s_data['total'] else 0
        mark = "✅" if pct_s == 100 else ("🔥" if pct_s >= 50 else "📦")
        series_lines.append(f"{mark} {s_name}: {s_data['owned']}/{s_data['total']} ({pct_s}%)")

    # Если игрок вообще ещё ничего не собрал
    if not series_lines:
        series_lines.append("<i>У вас пока нет собранных карт ни из одной вселенной.</i>")

    lines.append("\n".join(series_lines))
    lines.append("</blockquote>")

    txt = "\n".join(lines)
    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 К картам", callback_data="inv_view:0:all")
    bld.button(text="🔙 Назад", callback_data="inv_main")
    bld.adjust(2)

    try:
        await cq.message.edit_text(txt, parse_mode="HTML", reply_markup=bld.as_markup())
    except Exception:
        await cq.message.delete()
        await cq.message.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())
    await cq.answer()

@router.callback_query(F.data.startswith("viewcard:"))
async def view_card(cq: CallbackQuery):
    parts = cq.data.split(":")
    cid = parts[1]
    page = parts[2] if len(parts) > 2 else "0"
    r_filter = parts[3] if len(parts) > 3 else "all"
    excl_filter = parts[4] if len(parts) > 4 else "0"

    c = CARDS.get(cid)
    if not c:
        return await cq.answer("Карта не найдена.", show_alert=True)

    # --- ПРОВЕРКА СКИНОВ ---
    active_skin = get_active_skin(cq.from_user.id, cid)
    is_video = False
    skin_label = ""
    asset_path = ""

    # Подменяем медиафайл и добавляем приписку к имени, если надет скин
    if active_skin == "awakened" and cid in AWAKENED_SKIN:
        asset_path = f"images/cards/{AWAKENED_SKIN[cid]['skin_art_file']}"
        skin_label = " <i>(💠 Пробужденный облик)</i>"
    elif active_skin == "absolute" and cid in ABSOLUTE_SKIN:
        asset_path = f"images/cards/{ABSOLUTE_SKIN[cid]['skin_video_file']}"
        is_video = True
        skin_label = " <i>(🔮 Абсолютный облик)</i>"
    else:
        if is_divine(cid) and c.get("video"):
            asset_path = f"images/cards/{c['video']}"
            is_video = True
        else:
            asset_path = f"images/cards/{c['file']}"

    power = _card_power(cid)
    power_filled = min(10, power // 30)
    power_bar = "▰" * power_filled + "▱" * (10 - power_filled)

    txt = (
        f"🃏 <b>{c['name']}</b>{skin_label}\n\n"
        f"🔮 Редкость: {c['rarity']}\n"
        f"👊 Стиль боя: {c['style']}\n"
        f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
        f"⚡️ Скорость:   <b>{c['speed']}</b>\n"
        f"💪 Сила:       <b>{c['strength']}</b>\n"
        f"🧠 Интеллект:  <b>{c['intellect']}</b>\n\n"
        f"💥 Мощь: {power}  [{power_bar}]"
    )

    bld = InlineKeyboardBuilder()

    # Расставляем кнопки по рядам красиво
    bld.row(InlineKeyboardButton(text="〽️ Трейд", callback_data=f"trade_init:{cid}"))

    if not active_skin and is_divine(cid) and c.get("video"):
        bld.row(InlineKeyboardButton(text="Показать арт 👀",
                                     callback_data=f"divshow:{cid}:art:{page}:{r_filter}:{excl_filter}"))

    # Кнопка меню Обликов появляется только если у карты вообще существуют скины
    if cid in AWAKENED_SKIN or cid in ABSOLUTE_SKIN:
        # Сделаем её заметной, если скин уже надет
        skin_btn_text = "🎭 Настройки облика (Активен)" if active_skin else "🎭 Облики"
        bld.row(
            InlineKeyboardButton(text=skin_btn_text, callback_data=f"card_skins:{cid}:{page}:{r_filter}:{excl_filter}"))

    bld.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"inv_view:{page}:{r_filter}:{excl_filter}"))

    await cq.message.delete()
    if is_video:
        await send_cached_video(
            cq.bot,
            chat_id=cq.message.chat.id,
            file_path=asset_path,
            caption=txt,
            width=c.get("width", 960),
            height=c.get("height", 1280),
            reply_markup=bld.as_markup(),
            supports_streaming=True,
            parse_mode="HTML"
        )
    else:
        await cq.message.answer_photo(
            photo=FSInputFile(asset_path),
            caption=txt,
            parse_mode="HTML",
            reply_markup=bld.as_markup()
        )

# ================== МЕНЮ СКИНОВ ==================
@router.callback_query(F.data.startswith("card_skins:"))
async def card_skins_menu(cq: CallbackQuery):
    parts = cq.data.split(":")
    cid = parts[1]
    page = parts[2]
    r_filter = parts[3]
    excl_filter = parts[4]

    c = CARDS.get(cid)
    uid = cq.from_user.id

    # Получаем скины игрока
    user_skins = get_user_skins_for_card(uid, cid)
    owned_skins = {st: bool(ia) for st, ia in user_skins}

    txt = (
        f"🎭 <b>Гардероб Скинов</b> 🎭\n"
        f"🃏 <b>Карта:</b> {c['name']}\n"
        f"<i>Выбранный облик моментально применяется везде, где отображается ваша карта.</i>\n\n"
    )
    bld = InlineKeyboardBuilder()

    # 💠 Пробужденный скин
    if cid in AWAKENED_SKIN:
        is_owned = "awakened" in owned_skins
        is_active = owned_skins.get("awakened", False)

        if is_active:
            status = "🟢 <b>Активен</b>"
        elif is_owned:
            status = "🟡 <b>Доступен</b>"
        else:
            status = "🔒 <b>Заблокирован</b>"

        txt += f"<blockquote>💠 <b>Пробужденный (Арт)</b>\n└ Статус: {status}</blockquote>\n"

        if is_owned:
            bld.row(
                InlineKeyboardButton(text="👁️ Смотреть 💠",
                                     callback_data=f"sk_act:vw:aw:{cid}:{page}:{r_filter}:{excl_filter}:m"),
                InlineKeyboardButton(text="❌ Снять 💠" if is_active else "✅ Надеть 💠",
                                     callback_data=f"sk_act:{'un' if is_active else 'eq'}:aw:{cid}:{page}:{r_filter}:{excl_filter}:m")
            )

    # 🔮 Абсолютный скин
    if cid in ABSOLUTE_SKIN:
        is_owned = "absolute" in owned_skins
        is_active = owned_skins.get("absolute", False)

        if is_active:
            status = "🟢 <b>Активен</b>"
        elif is_owned:
            status = "🟡 <b>Доступен</b>"
        else:
            status = "🔒 <b>Заблокирован</b>"

        txt += f"<blockquote>🔮 <b>Абсолютный (Видео)</b>\n└ Статус: {status}</blockquote>\n"

        if is_owned:
            bld.row(
                InlineKeyboardButton(text="👁️ Смотреть 🔮",
                                     callback_data=f"sk_act:vw:ab:{cid}:{page}:{r_filter}:{excl_filter}:m"),
                InlineKeyboardButton(text="❌ Снять 🔮" if is_active else "✅ Надеть 🔮",
                                     callback_data=f"sk_act:{'un' if is_active else 'eq'}:ab:{cid}:{page}:{r_filter}:{excl_filter}:m")
            )

    bld.row(InlineKeyboardButton(text="🔙 Вернуться к карте",
                                 callback_data=f"viewcard:{cid}:{page}:{r_filter}:{excl_filter}"))

    # Бесшовная замена текста
    try:
        await cq.message.edit_caption(caption=txt, parse_mode="HTML", reply_markup=bld.as_markup())
    except Exception:
        pass  # Игнорируем ошибку, если текст не изменился


@router.callback_query(F.data.startswith("sk_act:"))
async def skin_action(cq: CallbackQuery):
    parts = cq.data.split(":")
    # Парсим сжатые аргументы: sk_act:eq:aw:cid:page:r_filter:excl_filter:source
    action, short_skin, cid, page, r_filter, excl_filter = parts[1:7]
    source = parts[7] if len(parts) > 7 else "m"

    skin_type = "awakened" if short_skin == "aw" else "absolute"
    uid = cq.from_user.id
    c = CARDS.get(cid)

    # === НАДЕТЬ / СНЯТЬ (БЕСШОВНО) ===
    if action in ("eq", "un"):
        if action == "eq":
            equip_skin(uid, cid, skin_type)
            await cq.answer("✨ Облик успешно надет!")
        else:
            unequip_skin(uid, cid)
            await cq.answer("❌ Облик снят!")

        # Если нажали из главного меню Обликов — моментально обновляем его
        if source == "m":
            new_cq = cq.model_copy(update={"data": f"card_skins:{cid}:{page}:{r_filter}:{excl_filter}"})
            return await card_skins_menu(new_cq)

        # Если нажали прямо во время просмотра скина — моментально обновляем меню под скином
        elif source == "v":
            bld = InlineKeyboardBuilder()
            user_skins = get_user_skins_for_card(uid, cid)
            owned_skins = {st: bool(ia) for st, ia in user_skins}
            is_active = owned_skins.get(skin_type, False)

            if is_active:
                bld.button(text="❌ Снять облик",
                           callback_data=f"sk_act:un:{short_skin}:{cid}:{page}:{r_filter}:{excl_filter}:v")
            else:
                bld.button(text="✅ Надеть облик",
                           callback_data=f"sk_act:eq:{short_skin}:{cid}:{page}:{r_filter}:{excl_filter}:v")

            bld.button(text="🔙 Назад к гардеробу", callback_data=f"card_skins:{cid}:{page}:{r_filter}:{excl_filter}")
            bld.adjust(1)

            txt = (
                f"🎭 <b>ПРОСМОТР СКИНА</b>\n\n"
                f"🃏 <b>Карта:</b> {c['name']}\n"
                f"Тип: {'💠 Пробужденный' if skin_type == 'awakened' else '🔮 Абсолютный'}\n\n"
                f"Статус: {'🟢 <b>Надет</b>' if is_active else '🟡 <b>Доступен</b>'}"
            )
            try:
                await cq.message.edit_caption(caption=txt, parse_mode="HTML", reply_markup=bld.as_markup())
            except Exception:
                pass
            return

    # === ПРОСМОТР СКИНА (Требует отправки нового фото/видео) ===
    elif action == "vw":
        await cq.message.delete()
        bld = InlineKeyboardBuilder()

        user_skins = get_user_skins_for_card(uid, cid)
        owned_skins = {st: bool(ia) for st, ia in user_skins}
        is_active = owned_skins.get(skin_type, False)

        if is_active:
            bld.button(text="❌ Снять облик",
                       callback_data=f"sk_act:un:{short_skin}:{cid}:{page}:{r_filter}:{excl_filter}:v")
        else:
            bld.button(text="✅ Надеть облик",
                       callback_data=f"sk_act:eq:{short_skin}:{cid}:{page}:{r_filter}:{excl_filter}:v")

        bld.button(text="🔙 Назад к гардеробу", callback_data=f"card_skins:{cid}:{page}:{r_filter}:{excl_filter}")
        bld.adjust(1)

        txt = (
            f"🎭 <b>ПРОСМОТР СКИНА</b>\n\n"
            f"🃏 <b>Карта:</b> {c['name']}\n"
            f"Тип: {'💠 Пробужденный' if skin_type == 'awakened' else '🔮 Абсолютный'}\n\n"
            f"Статус: {'🟢 <b>Надет</b>' if is_active else '🟡 <b>Доступен</b>'}"
        )

        if skin_type == "awakened":
            file_path = f"images/cards/{AWAKENED_SKIN[cid]['skin_art_file']}"
            await cq.message.answer_photo(
                photo=FSInputFile(file_path),
                caption=txt, parse_mode="HTML", reply_markup=bld.as_markup()
            )
        else:
            file_path = f"images/cards/{ABSOLUTE_SKIN[cid]['skin_video_file']}"
            await send_cached_video(
                cq.bot, chat_id=cq.message.chat.id,
                file_path=file_path, caption=txt,
                reply_markup=bld.as_markup(), parse_mode="HTML",
                supports_streaming=True
            )

# ===== Переключение арт/видео для Божественной карты =====
@router.callback_query(F.data.startswith("divshow:"))
async def divine_toggle(cq: CallbackQuery):
    parts = cq.data.split(":")
    cid, mode = parts[1], parts[2]
    page = parts[3] if len(parts) > 3 else "0"
    r_filter = parts[4] if len(parts) > 4 else "all"

    c = CARDS.get(cid)
    if not c: return await cq.answer("Карта не найдена.", show_alert=True)

    txt = (
        f"🃏 <b>{c['name']}</b>\n\n"
        f"🔮 Редкость: {c['rarity']}\n"
        f"👊 Стиль боя: {c['style']}\n"
        f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
        f"⚡️ Скорость:   <b>{c['speed']}</b>\n"
        f"💪 Сила:       <b>{c['strength']}</b>\n"
        f"🧠 Интеллект:  <b>{c['intellect']}</b>\n\n"
        f"💥 Мощь: {_card_power(cid)}  [{'▰' * min(10, _card_power(cid) // 30) + '▱' * (10 - min(10, _card_power(cid) // 30))}]"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="〽️ Трейд", callback_data=f"trade_init:{cid}")

    if mode == "art":
        bld.button(text="Показать видео 👀", callback_data=f"divshow:{cid}:video:{page}:{r_filter}")
    else:
        bld.button(text="Показать арт 👀", callback_data=f"divshow:{cid}:art:{page}:{r_filter}")
    bld.button(text="Назад", callback_data=f"inv_view:{page}:{r_filter}")
    bld.adjust(1)

    try:
        await cq.message.delete()
    except:
        pass

    if mode == "art":
        await cq.message.answer_photo(
            photo=FSInputFile(f"images/cards/{c['file']}"),
            caption=txt, parse_mode="HTML",
            reply_markup=bld.as_markup()
        )
    else:
        await send_cached_video(
            cq.bot,
            chat_id=cq.message.chat.id,
            file_path=f"images/cards/{c['video']}",
            caption=txt,
            width=c.get("width", 960),
            height=c.get("height", 1280),
            reply_markup=bld.as_markup(),
            supports_streaming=True
        )
    await cq.answer()


# ============ ИСПРАВЛЕННЫЙ БЛОК ТРЕЙДОВ ============

@router.callback_query(F.data.startswith("trade_init:"))
async def trade_init(cq: CallbackQuery, state: FSMContext):
    # ЗАЩИТА: Проверяем, не занят ли инициатор уже каким-то трейдом
    def is_user_busy(uid):
        if uid in PENDING_TRADES: return True
        for tr in PENDING_TRADES.values():
            if tr.get('receiver_id') == uid: return True
        return False

    if is_user_busy(cq.from_user.id):
        return await cq.answer("❌ Вы уже участвуете в активном обмене! Завершите или отмените его перед новым.", show_alert=True)

    await cq.answer()
    cid = cq.data.split(":", 1)[1]

    c = CARDS.get(cid)
    if not c:
        return await cq.message.answer("❌ Ошибка: карта не найдена в базе данных.")

    await state.update_data(trade_card=cid)

    bld = InlineKeyboardBuilder()
    bld.button(text="👤 По ID игрока", callback_data=f"trade_method:id:{cid}")
    bld.button(text="🔗 По ссылке", callback_data=f"trade_method:link:{cid}")
    bld.button(text="❌ Отменить", callback_data="trade_cancel_init")
    bld.adjust(2, 1)

    try:
        await cq.message.delete()
    except Exception:
        pass

    photo_path = f"images/cards/{c['file']}"
    caption = (
        f"〽️ <b>Трейд карты</b> {c['name']} ({c['rarity']})\n\n"
        f"Выберите удобный способ передачи предложения обмена 👇"
    )

    if os.path.exists(photo_path):
        await cq.message.answer_photo(
            photo=FSInputFile(photo_path),
            caption=caption,
            reply_markup=bld.as_markup(),
            parse_mode="HTML"
        )
    else:
        await cq.message.answer(caption, reply_markup=bld.as_markup(), parse_mode="HTML")


# --- Обработка выбора: Трейд по ID ---
@router.callback_query(F.data.startswith("trade_method:id:"))
async def trade_method_id(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await state.set_state(TradeState.waiting_for_trade_id)

    bld = InlineKeyboardBuilder()
    bld.button(text="❌ Отменить", callback_data="trade_cancel_init")

    await cq.message.edit_caption(
        caption="⏳ Отправьте 🆔 игрока, которому хотите предложить обмен:",
        reply_markup=bld.as_markup(),
        parse_mode="HTML"
    )


# --- Обработка выбора: Трейд по ссылке ---
@router.callback_query(F.data.startswith("trade_method:link:"))
async def trade_method_link(cq: CallbackQuery):
    await cq.answer()
    cid = cq.data.split(":", 2)[2]
    c = CARDS.get(cid)

    if not c:
        return await cq.message.answer("❌ Ошибка: карта не найдена.")

    bot_info = await cq.bot.get_me()
    raw_payload = f"trade:{cq.from_user.id}:{cid}"
    b64_payload = base64.urlsafe_b64encode(raw_payload.encode()).decode().rstrip("=")
    trade_link = f"https://t.me/{bot_info.username}?start={b64_payload}"

    share_text = f"Давай меняться! Я предлагаю карту {c['name']} ({c['rarity']}) в боте.\n\n Переходи по ссылке и делай свое предложение!"
    share_url = f"https://t.me/share/url?url={trade_link}&text={quote(share_text)}"

    bld = InlineKeyboardBuilder()
    bld.button(text="Переслать в чат 🚀", url=share_url)
    bld.button(text="❌ Отменить", callback_data="trade_cancel_init")
    bld.adjust(1)

    caption = (
        f"🔗 <b>Ваша персональная трейд-ссылка создана!</b>\n\n"
        f"🎴 Карточка: <b>{c['name']}</b> ({c['rarity']})\n\n"
        f"Отправьте эту ссылку другому игроку, чтобы он смог предложить вам обмен:\n"
        f"👉 <code>{trade_link}</code>\n\n"
        f"<i>Вы можете скопировать ссылку, просто нажав на неё ☝️, или использовать кнопку ниже для быстрой пересылки.</i>"
    )

    await cq.message.edit_caption(
        caption=caption,
        reply_markup=bld.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "trade_cancel_init")
async def trade_cancel_init(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await state.clear()
    PENDING_TRADES.pop(cq.from_user.id, None)
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.message.answer("❌ Трейд отменен.")


@router.message(TradeState.waiting_for_trade_id)
async def process_trade_id(msg: types.Message, state: FSMContext):
    # ЗАЩИТА: Проверка занятости (Trade Lock)
    def is_user_busy(uid):
        if uid in PENDING_TRADES: return True
        for tr in PENDING_TRADES.values():
            if tr.get('receiver_id') == uid: return True
        return False

    if is_user_busy(msg.from_user.id):
        await state.clear()
        return await msg.answer("❌ Вы уже участвуете в активном обмене! Сначала завершите или отмените его.")

    data = await state.get_data()
    cid = data.get('trade_card')
    if not cid:
        await state.clear()
        return

    target_id_str = (msg.text or "").strip()

    if not target_id_str.isdigit():
        await state.clear()
        PENDING_TRADES.pop(msg.from_user.id, None)
        return await msg.answer("❌ Неверный ID. Трейд отменен.")

    target_id = int(target_id_str)
    if target_id == msg.from_user.id:
        await state.clear()
        PENDING_TRADES.pop(msg.from_user.id, None)
        return await msg.answer("❌ Нельзя трейдиться с самим собой. Трейд отменен.")

    if is_user_busy(target_id):
        await state.clear()
        PENDING_TRADES.pop(msg.from_user.id, None)
        return await msg.answer("❌ Этот игрок сейчас занят другим обменом. Попробуйте позже.")

    u_target = get_user(target_id)
    if not u_target:
        await state.clear()
        PENDING_TRADES.pop(msg.from_user.id, None)
        return await msg.answer("❌ Игрок с таким ID не найден в базе бота. Трейд отменен.")

    await state.clear()

    PENDING_TRADES[msg.from_user.id] = {
        'sender_card': cid,
        'receiver_id': target_id,
        'receiver_card': None
    }

    c = CARDS.get(cid)
    target_name = escape(u_target[2] if u_target[2] else f"Игрок {target_id}")
    sender_name = escape(msg.from_user.first_name)

    await msg.answer(
        f"📨 Запрос на обмен успешно отправлен игроку <a href='tg://user?id={target_id}'>{target_name}</a>.\n"
        f"Ожидаем его ответа ⏳",
        parse_mode="HTML"
    )

    has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (target_id, cid), fetch=True)
    warning = "\n<i>(⚠️ Осторожно: у вас уже есть копия этой карты)</i>" if has_card else ""

    caption = (
        f"⚖️ <b>Новый запрос на обмен!</b>\n\n"
        f"Игрок <a href='tg://user?id={msg.from_user.id}'>{sender_name}</a> хочет обменяться с вами картами!\n"
        f"<blockquote>🎁 <b>Он предлагает:</b>\n"
        f"🎴 {c['name']} ({c['rarity']})</blockquote>"
        f"{warning}"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="Выбрать карту взамен 🎴", callback_data=f"trade_p2_select:{msg.from_user.id}")
    bld.button(text="Отказаться ❌", callback_data=f"trade_decline:{msg.from_user.id}")
    bld.adjust(1)

    try:
        photo_path = f"images/cards/{c['file']}"
        await msg.bot.send_photo(
            target_id,
            photo=FSInputFile(photo_path) if os.path.exists(photo_path) else "https://via.placeholder.com/300",
            caption=caption,
            reply_markup=bld.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Trade send error: {e}")
        await msg.answer("❌ Не удалось отправить запрос. Возможно, игрок заблокировал бота.")
        PENDING_TRADES.pop(msg.from_user.id, None)


@router.callback_query(F.data.startswith("trade_p2_select:"))
async def trade_p2_select(cq: CallbackQuery):
    await cq.answer()
    sender_id = int(cq.data.split(":")[1])
    try:
        await cq.message.delete()
    except Exception:
        pass
    await _render_trade_page(cq, sender_id, page=0, sort_dir=0, is_new_message=True)


@router.callback_query(F.data.startswith("trade_p2_page:"))
async def trade_p2_page(cq: CallbackQuery):
    await cq.answer()
    parts = cq.data.split(":")
    sender_id = int(parts[1])
    page = int(parts[2])
    sort_dir = int(parts[3])
    await _render_trade_page(cq, sender_id, page, sort_dir, is_new_message=False)


async def _render_trade_page(cq: CallbackQuery, sender_id: int, page: int, sort_dir: int, is_new_message: bool):
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != cq.from_user.id:
        txt = "❌ Трейд более не актуален или был отменен."
        if is_new_message:
            return await cq.message.answer(txt)
        else:
            return await cq.message.edit_text(txt, reply_markup=None)

    # ЗАЩИТА: Проверяем, есть ли всё ещё карта у инициатора
    sender_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, t['sender_card']),
                         fetch=True)
    if not sender_has:
        PENDING_TRADES.pop(sender_id, None)
        txt = "❌ Трейд больше не актуален: у инициатора больше нет этой карты."
        if is_new_message:
            return await cq.message.answer(txt)
        else:
            return await cq.message.edit_text(txt, reply_markup=None)

    sender_card_id = t['sender_card']
    sender_card_data = CARDS.get(sender_card_id)
    if not sender_card_data:
        txt = "❌ Ошибка: карта инициатора не найдена."
        if is_new_message:
            return await cq.message.answer(txt)
        else:
            return await cq.message.edit_text(txt, reply_markup=None)

    rarity = sender_card_data['rarity']
    inv_data = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)

    valid_cards = []
    for (cid,) in inv_data:
        card_info = CARDS.get(cid)
        if card_info and card_info.get('rarity') == rarity:
            valid_cards.append(cid)

    valid_cards = list(set(valid_cards))

    if not valid_cards:
        PENDING_TRADES.pop(sender_id, None)
        txt = f"❌ У вас нет карт редкости <b>{rarity}</b> для равноценного обмена. Трейд отменен."
        if is_new_message:
            await cq.message.answer(txt, parse_mode="HTML")
        else:
            await cq.message.edit_text(txt, parse_mode="HTML")
        try:
            await cq.bot.send_message(sender_id, "❌ Игрок не может принять трейд: нет подходящих карт по редкости.")
        except:
            pass
        return

    valid_cards.sort(key=lambda cid: _card_power(cid), reverse=(sort_dir == 0))

    items_per_page = 8
    total_pages = max(1, (len(valid_cards) + items_per_page - 1) // items_per_page)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * items_per_page
    page_cards = valid_cards[start_idx: start_idx + items_per_page]

    bld = InlineKeyboardBuilder()

    card_btns = []
    for cid in page_cards:
        c_info = CARDS[cid]
        name = c_info['name']
        c_spd = c_info.get('speed', 0)
        c_str = c_info.get('strength', 0)
        c_int = c_info.get('intellect', 0)

        card_btns.append(
            InlineKeyboardButton(
                text=f"{name} · ⚡️{c_spd} 💪{c_str} 🧠{c_int}",
                callback_data=f"trade_p2_conf:{sender_id}:{cid}"
            )
        )

    for i in range(0, len(card_btns), 2):
        bld.row(*card_btns[i:i + 2])

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"trade_p2_page:{sender_id}:{page - 1}:{sort_dir}"))
    else:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data="ignore"))

    nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="ignore"))

    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(text="➡️", callback_data=f"trade_p2_page:{sender_id}:{page + 1}:{sort_dir}"))
    else:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data="ignore"))
    bld.row(*nav_row)

    sort_text = "🔄 Сортировка: Сильные ⬇️" if sort_dir == 0 else "🔄 Сортировка: Слабые ⬆️"
    next_sort = 1 if sort_dir == 0 else 0
    bld.row(InlineKeyboardButton(text=sort_text, callback_data=f"trade_p2_page:{sender_id}:0:{next_sort}"))

    bld.row(InlineKeyboardButton(text="Отказаться ❌", callback_data=f"trade_decline:{sender_id}"))

    txt = (
        f"🎴 <b>Выберите вашу карту, которую отдадите взамен:</b>\n"
        f"<i>(Показаны только карты редкости {rarity})</i>\n"
        f"Всего доступно карт на выбор: <b>{len(valid_cards)}</b>"
    )

    if is_new_message:
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    else:
        await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("trade_p2_conf:"))
async def trade_p2_conf(cq: CallbackQuery):
    await cq.answer()
    parts = cq.data.split(":", 2)
    sender_id = int(parts[1])
    p2_card = parts[2]

    t = PENDING_TRADES.get(sender_id)
    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.message.answer("❌ Трейд не актуален.")

    # ЗАЩИТА: Проверяем, есть ли всё ещё карта у инициатора
    sender_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, t['sender_card']),
                         fetch=True)
    if not sender_has:
        PENDING_TRADES.pop(sender_id, None)
        return await cq.message.answer("❌ Трейд больше не актуален: у инициатора больше нет этой карты.")

    t['receiver_card'] = p2_card

    c_sender = CARDS.get(t['sender_card'])
    c_receiver = CARDS.get(p2_card)

    sender_user = get_user(sender_id)
    sender_name = escape(sender_user[2] if sender_user else f"Игрок {sender_id}")

    await cq.message.delete()

    media = []
    for c in [c_receiver, c_sender]:
        p = f"images/cards/{c['file']}"
        if os.path.exists(p):
            media.append(types.InputMediaPhoto(media=FSInputFile(p)))

    if media:
        await cq.message.answer_media_group(media=media)

    has_c_sender = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?",
                           (cq.from_user.id, t['sender_card']), fetch=True)
    warning = "\n<i>(⚠️ Осторожно: у вас уже есть копия этой карты)</i>" if has_c_sender else ""

    txt = (
        f"⚖️ <b>Подготовка к обмену</b> с <a href='tg://user?id={sender_id}'>{sender_name}</a>\n\n"
        f"<blockquote>📤 <b>Вы отдаёте:</b> {c_receiver['name']} ({c_receiver['rarity']})\n"
        f"📥 <b>Вы получаете:</b> {c_sender['name']} ({c_sender['rarity']}){warning}</blockquote>\n\n"
        f"❓ Всё верно? Подтвердите выбор для отправки встречного предложения 🤝"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="Отправить предложение ✅", callback_data=f"trade_p2_final:{sender_id}")
    bld.button(text="Отказаться ❌", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(1)
    await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("trade_p2_final:"))
async def trade_p2_final(cq: CallbackQuery):
    sender_id = int(cq.data.split(":")[1])
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.answer("Трейд не актуален.", show_alert=True)

    # ЗАЩИТА: Если игрок 1 уже куда-то дел карту
    sender_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, t['sender_card']),
                         fetch=True)
    if not sender_has:
        PENDING_TRADES.pop(sender_id, None)
        return await cq.answer("❌ Инициатор уже обменял эту карту!", show_alert=True)

    c1 = CARDS[t['sender_card']]
    c2 = CARDS[t['receiver_card']]

    sender_user = get_user(sender_id)
    sender_name = escape(sender_user[2] if sender_user else f"Игрок {sender_id}")

    has_c_sender = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?",
                           (cq.from_user.id, t['sender_card']), fetch=True)
    warning_p2 = "\n<i>(⚠️ Осторожно: у вас уже есть копия этой карты)</i>" if has_c_sender else ""

    p2_txt_update = (
        f"⚖️ <b>Подготовка к обмену</b> с <a href='tg://user?id={sender_id}'>{sender_name}</a>\n\n"
        f"<blockquote>📤 <b>Вы отдаёте:</b> {c2['name']} ({c2['rarity']})\n"
        f"📥 <b>Вы получаете:</b> {c1['name']} ({c1['rarity']}){warning_p2}</blockquote>\n\n"
        f"⏳ <i>Ожидание подтверждения от инициатора сделки...</i>"
    )

    try:
        await cq.message.edit_text(p2_txt_update, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass

    has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, t['receiver_card']),
                       fetch=True)
    warning_p1 = "\n<i>(⚠️ Осторожно: у вас уже есть эта копия)</i>" if has_card else ""
    p2_name = escape(cq.from_user.first_name)

    txt = (
        f"✨ <b>Встречное предложение получено!</b>\n\n"
        f"Игрок <a href='tg://user?id={cq.from_user.id}'>{p2_name}</a> выбрал карту для обмена.\n"
        f"<blockquote>📤 <b>Вы отдаёте:</b> {c1['name']} ({c1['rarity']})\n"
        f"📥 <b>Вы получаете:</b> {c2['name']} ({c2['rarity']}){warning_p1}</blockquote>\n\n"
        f"Ударить по рукам и завершить сделку? 🤝"
    )

    media = []
    p1_path = f"images/cards/{c1['file']}"
    p2_path = f"images/cards/{c2['file']}"
    if os.path.exists(p1_path):
        media.append(types.InputMediaPhoto(media=FSInputFile(p1_path)))
    if os.path.exists(p2_path):
        media.append(types.InputMediaPhoto(media=FSInputFile(p2_path)))

    bld = InlineKeyboardBuilder()
    bld.button(text="Ударить по рукам 🤝", callback_data=f"trade_p1_final:{cq.from_user.id}")
    bld.button(text="Сорвать сделку ❌", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(1)

    try:
        if media:
            await cq.bot.send_media_group(sender_id, media=media)
        await cq.bot.send_message(sender_id, txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        await cq.message.answer("❌ Не удалось связаться с инициатором. Трейд отменен.")
        PENDING_TRADES.pop(sender_id, None)


@router.callback_query(F.data.startswith("trade_p1_final:"))
async def trade_p1_final(cq: CallbackQuery):
    p2_id = int(cq.data.split(":")[1])
    sender_id = cq.from_user.id

    # 🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ АСИНХРОННОЙ ГОНКИ (ДЮПА)
    # Мгновенно забираем трейд из памяти через .pop() ПЕРЕД любыми проверками
    # Если нажать кнопку дважды, второй раз t уже будет None и дублирования не произойдет.
    t = PENDING_TRADES.pop(sender_id, None)

    if not t or t['receiver_id'] != p2_id:
        return await cq.answer("Трейд не актуален или уже завершен.", show_alert=True)

    c1_id = t['sender_card']
    c2_id = t['receiver_card']

    # === НАЧАЛО СИНХРОННОГО БЛОКА: НИКАКИХ AWAIT ДО ОКОНЧАНИЯ РАБОТЫ С БД ===
    p1_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c1_id), fetch=True)
    p2_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c2_id), fetch=True)

    if not p1_has or not p2_has:
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.message.answer("❌ <b>Трейд сорвался:</b> у одного из игроков больше нет нужной карты.",
                                parse_mode="HTML")
        try:
            await cq.bot.send_message(p2_id, "❌ <b>Трейд сорвался:</b> у одного из игроков больше нет нужной карты.",
                                      parse_mode="HTML")
        except:
            pass
        return

    # Удаление карты у инициатора
    row1 = db_exec("SELECT rowid FROM cards_inv WHERE user_id = ? AND card_id = ? LIMIT 1", (sender_id, c1_id),
                   fetch=True)
    if row1:
        db_exec("DELETE FROM cards_inv WHERE rowid = ?", (row1[0],))

    l_inv1 = db_exec("SELECT COUNT(*) FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c1_id), fetch=True)
    l_st1 = db_exec("SELECT COUNT(*) FROM cards_stash WHERE user_id = ? AND card_id = ?", (sender_id, c1_id),
                    fetch=True)
    if (l_inv1[0] if l_inv1 else 0) + (l_st1[0] if l_st1 else 0) == 0:
        db_exec("DELETE FROM decks WHERE user_id = ? AND card_id = ?", (sender_id, c1_id))
        try:
            db_exec(
                "DELETE FROM multi_deck_slots WHERE card_id = ? AND deck_id IN (SELECT deck_id FROM multi_decks WHERE user_id = ?)",
                (c1_id, sender_id))
        except:
            pass
        db_exec("DELETE FROM favorite_cards WHERE user_id = ? AND card_id = ?", (sender_id, c1_id))

    # Удаление карты у получателя
    row2 = db_exec("SELECT rowid FROM cards_inv WHERE user_id = ? AND card_id = ? LIMIT 1", (p2_id, c2_id), fetch=True)
    if row2:
        db_exec("DELETE FROM cards_inv WHERE rowid = ?", (row2[0],))

    l_inv2 = db_exec("SELECT COUNT(*) FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c2_id), fetch=True)
    l_st2 = db_exec("SELECT COUNT(*) FROM cards_stash WHERE user_id = ? AND card_id = ?", (p2_id, c2_id), fetch=True)
    if (l_inv2[0] if l_inv2 else 0) + (l_st2[0] if l_st2 else 0) == 0:
        db_exec("DELETE FROM decks WHERE user_id = ? AND card_id = ?", (p2_id, c2_id))
        try:
            db_exec(
                "DELETE FROM multi_deck_slots WHERE card_id = ? AND deck_id IN (SELECT deck_id FROM multi_decks WHERE user_id = ?)",
                (c2_id, p2_id))
        except:
            pass
        db_exec("DELETE FROM favorite_cards WHERE user_id = ? AND card_id = ?", (p2_id, c2_id))

    # ВЫДАЕМ КАРТЫ С ЗАЩИТОЙ
    p1_has_c2 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c2_id), fetch=True)
    p2_has_c1 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c1_id), fetch=True)

    if not p1_has_c2:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (sender_id, c2_id))
    if not p2_has_c1:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (p2_id, c1_id))

    # === КОНЕЦ СИНХРОННОГО БЛОКА ===
    # ТОЛЬКО ТЕПЕРЬ мы можем безопасно вызывать await, так как обмен в базе уже завершен!

    has_card_p1 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c2_id), fetch=True)
    warning_p1_final = "\n<i>(⚠️ Осторожно: у вас уже есть эта копия)</i>" if has_card_p1 else ""
    p2_user = get_user(p2_id)
    p2_name = escape(p2_user[2] if p2_user and p2_user[2] else f"Игрок {p2_id}")

    p1_txt_update = (
        f"✨ <b>Встречное предложение получено!</b>\n\n"
        f"Игрок <a href='tg://user?id={p2_id}'>{p2_name}</a> выбрал карту для обмена.\n"
        f"<blockquote>📤 <b>Вы отдаёте:</b> {CARDS[c1_id]['name']} ({CARDS[c1_id]['rarity']})\n"
        f"📥 <b>Вы получаете:</b> {CARDS[c2_id]['name']} ({CARDS[c2_id]['rarity']}){warning_p1_final}</blockquote>\n\n"
        f"✅ <b>Сделка успешно завершена!</b>"
    )
    try:
        await cq.message.edit_text(p1_txt_update, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass

    u1 = get_user(sender_id)
    u2 = get_user(p2_id)
    n1 = escape(u1[2] if u1 and u1[2] else f"Игрок {sender_id}")
    n2 = escape(u2[2] if u2 and u2[2] else f"Игрок {p2_id}")

    p2_card_path = f"images/cards/{CARDS[c2_id]['file']}"
    if os.path.exists(p2_card_path):
        await cq.message.answer_photo(
            photo=FSInputFile(p2_card_path),
            caption=(
                f"🎉 <b>Обмен успешно завершён!</b>\n\n"
                f"<blockquote>🎴 <b>Новая карта:</b> {CARDS[c2_id]['name']}</blockquote>\n"
                f"Сделка с <a href='tg://user?id={p2_id}'>{n2}</a> прошла успешно 🤝"
            ),
            parse_mode="HTML"
        )
    else:
        await cq.message.answer(
            f"🎉 <b>Обмен успешно завершён!</b>\n\n"
            f"<blockquote>🎴 <b>Новая карта:</b> {CARDS[c2_id]['name']}</blockquote>\n"
            f"Сделка с <a href='tg://user?id={p2_id}'>{n2}</a> прошла успешно 🤝",
            parse_mode="HTML"
        )

    try:
        p1_card_path = f"images/cards/{CARDS[c1_id]['file']}"
        if os.path.exists(p1_card_path):
            await cq.bot.send_photo(
                p2_id,
                photo=FSInputFile(p1_card_path),
                caption=(
                    f"🎉 <b>Обмен успешно завершён!</b>\n\n"
                    f"<blockquote>🎴 <b>Новая карта:</b> {CARDS[c1_id]['name']}</blockquote>\n"
                    f"Сделка с <a href='tg://user?id={sender_id}'>{n1}</a> прошла успешно 🤝"
                ),
                parse_mode="HTML"
            )
        else:
            await cq.bot.send_message(
                p2_id,
                f"🎉 <b>Обмен успешно завершён!</b>\n\n"
                f"<blockquote>🎴 <b>Новая карта:</b> {CARDS[c1_id]['name']}</blockquote>\n"
                f"Сделка с <a href='tg://user?id={sender_id}'>{n1}</a> прошла успешно 🤝",
                parse_mode="HTML"
            )
    except:
        pass

        # === MANHWCARD PASS: ЗАДАНИЯ ЗА ТРЕЙД ===
        # ВАЖНО: Этот блок теперь на правильном уровне отступа (не внутри except!)
    for uid in (sender_id, p2_id):
        q_res = check_and_update_quests(uid, 'q_3_trades', 1)

        if q_res and q_res.get("leveled_up"):
            try:
                asyncio.create_task(cq.bot.send_message(
                    uid,
                    f"⚡️ <b>[СИСТЕМА]</b>\n\n"
                    f"Ваш уровень ManhwCard Pass повышен!\n"
                    f"Текущий уровень: <b>{q_res['level']}</b>.\n\n"
                    f"<i>Зайдите в Web App, чтобы забрать награду.</i>",
                    parse_mode="HTML"
                ))
            except:
                pass

    await cq.answer()
                
@router.callback_query(F.data.startswith("trade_decline:"))
async def trade_decline(cq: CallbackQuery):
    sender_id = int(cq.data.split(":")[1])
    t = PENDING_TRADES.pop(sender_id, None)

    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await cq.message.answer("❌ Трейд отменен.")

    # Интеллектуально вычисляем, кому нужно отправить уведомление
    other_id = None
    if t:
        # Если трейд был активен, смотрим, кто нажал отмену
        if cq.from_user.id == sender_id:
            other_id = t.get('receiver_id') # Отменил создатель, уведомляем второго
        else:
            other_id = sender_id # Отменил второй, уведомляем создателя
    else:
        # Если трейд уже удален, но кнопку нажал второй игрок
        if cq.from_user.id != sender_id:
            other_id = sender_id

    if other_id:
        try:
            name = escape(cq.from_user.first_name)
            await cq.bot.send_message(
                other_id,
                f"❌ Игрок <a href='tg://user?id={cq.from_user.id}'>{name}</a> отменил трейд.",
                parse_mode="HTML"
            )
        except:
            pass


@router.callback_query(F.data == "ignore")
async def ignore_cb(cq: CallbackQuery):
    await cq.answer()

def _render_skin_slide_data(uid: int, skin_type: str, index: int, owned_cids: list):
    """Вспомогательный хелпер для генерации красивого текста и кнопок слайдера"""
    total = len(owned_cids)
    cid = owned_cids[index]
    c = CARDS.get(cid)

    # Проверяем наличие базовой карты
    base_owned = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ? LIMIT 1", (uid, cid), fetch=True)
    has_base_str = "🟢 Есть в инвентаре ✅" if base_owned else "🔴 Отсутствует в инвентаре ❌"

    # Динамические заголовки по твоей просьбе
    if skin_type == "awakened":
        header_title = "Пробужденные скины 💠"
        asset_path = f"images/cards/{AWAKENED_SKIN[cid]['skin_art_file']}"
        is_video = False
    else:
        header_title = "Абсолютные скины 🔮"
        asset_path = f"images/cards/{ABSOLUTE_SKIN[cid]['skin_video_file']}"
        is_video = True

    active_skin = get_active_skin(uid, cid)
    is_current_active = (active_skin == skin_type)

    # Дополнительная красота: Жирный и заметный статус-баннер облика
    if is_current_active:
        status_banner = "✨ <b>Активен и надет</b>"
    elif base_owned:
        status_banner = "📦 <b>Доступен к применению</b>"
    else:
        status_banner = "🔒 <b>Базовая карта отсутствует</b>"

    txt = (
        f"<b>{header_title}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Персонаж:</b> {c['name']}\n"
        f"🔮 <b>Редкость карты:</b> {c['rarity']}\n"
        f"👊 <b>Стиль боя:</b> {c.get('style', '—')}\n\n"
        f"<blockquote>🎭 Статус облика:\n└ {status_banner}</blockquote>\n"
        f"<blockquote>📋 Наличие карты:\n└ {has_base_str}</blockquote>\n"
        f"Порядковый номер: <b>{index + 1} из {total}</b>"
    )

    bld = InlineKeyboardBuilder()

    # Ряд навигации
    bld.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_skins_slider:{skin_type}:{index - 1}"),
        InlineKeyboardButton(text=f"▪️ {index + 1} / {total} ▪️", callback_data="ignore"),
        InlineKeyboardButton(text="Вперед ➡️", callback_data=f"view_skins_slider:{skin_type}:{index + 1}")
    )

    # Кнопка моментального переключения
    if base_owned:
        if is_current_active:
            bld.row(
                InlineKeyboardButton(text="❌ Снять этот скин", callback_data=f"sl_sk_act:un:{skin_type}:{cid}:{index}"))
        else:
            bld.row(InlineKeyboardButton(text="✅ Применить этот скин",
                                         callback_data=f"sl_sk_act:eq:{skin_type}:{cid}:{index}"))

    bld.row(InlineKeyboardButton(text="🔙 К категориям", callback_data="my_skins_categories"))

    return asset_path, is_video, txt, bld.as_markup()


@router.callback_query(F.data == "my_skins_categories")
async def my_skins_categories_menu(cq: CallbackQuery):
    txt = (
        "🏵️ <b>ГАЛЕРЕЯ СКИНОВ</b> 🏵️\n\n"
        "Добро пожаловать в ваш личный гардероб редких скинов на вашы карты!\n\n"
        "<i>Выберите интересующую вас редкость, чтобы открыть просмотр вашей коллекции:</i>"
    )
    bld = InlineKeyboardBuilder()

    # 4 кнопки в один ряд, как ты просил
    bld.add(InlineKeyboardButton(text="💠 Пробужденные", callback_data="view_skins_slider:awakened:0"))
    bld.add(InlineKeyboardButton(text="🔮 Абсолютные", callback_data="view_skins_slider:absolute:0"))
    bld.add(InlineKeyboardButton(text="Трейд ♻️", callback_data="skin_trade_menu"))
    bld.add(InlineKeyboardButton(text="Назад 🔙", callback_data="inv_main"))
    bld.adjust(1)

    # ИСПРАВЛЕНО: Сначала удаляем картинку/видео, затем отправляем чистое текстовое меню
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()

# ♻️ СИСТЕМА ТРЕЙДА СКИНАМИ ♻️
PENDING_SKIN_TRADES = {}
ACTIVE_SKIN_TRADES = {}

kb_trade_accept = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Принять ✅"), KeyboardButton(text="Отказать ❌")]],
    resize_keyboard=True
)

kb_trade_cancel = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Отменить трейд ❌")]],
    resize_keyboard=True
)

@router.callback_query(F.data == "skin_trade_menu")
async def skin_trade_menu(cq: CallbackQuery, bot: Bot):
    me = await bot.get_me()
    uid = cq.from_user.id

    link_awa = f"https://t.me/{me.username}?start=sktrad_awa_{uid}"
    link_abs = f"https://t.me/{me.username}?start=sktrad_abs_{uid}"

    txt = "♻️ <b>Трейд Скинами</b>\n\nВыберите редкость скина для обмена и отправьте ссылку партнеру в чат:"

    # Используем ** вместо <b>, чтобы Telegram сам сделал текст жирным при отправке в чат!
    text_awa = "♻️ Предлагаю совершить со мной трейд скинами!?\nРедкость - Пробужденный 💠\n\nТыкай по ссылке и го обмен🤫"
    text_abs = "♻️ Предлагаю совершить со мной трейд скинами!?\nРедкость - Абсолютный 🔮\n\nТыкай по ссылке и го обмен🤫"

    bld = InlineKeyboardBuilder()
    bld.row(InlineKeyboardButton(text="Пробуждённый трейд 💠",
                                 url=f"https://t.me/share/url?url={quote(link_awa)}&text={quote(text_awa)}"))
    bld.row(InlineKeyboardButton(text="Абсолютный трейд 🔮",
                                 url=f"https://t.me/share/url?url={quote(link_abs)}&text={quote(text_abs)}"))
    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="my_skins_categories"))

    try:
        await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    await cq.answer()


@router.message(F.text == "Отказать ❌")
async def decline_skin_trade(message: types.Message, bot: Bot):
    uid = message.from_user.id
    from handlers import kb_main
    if uid in PENDING_SKIN_TRADES:
        data = PENDING_SKIN_TRADES.pop(uid)
        data['task'].cancel()
        await message.answer("❌ Заявка на обмен отклонена.", reply_markup=kb_main())
        await bot.send_message(data['sender_id'], "❌ Партнер отклонил заявку на обмен скинами.")


@router.message(F.text == "Принять ✅")
async def accept_skin_trade(message: types.Message, bot: Bot):
    uid = message.from_user.id
    from handlers import kb_main
    if uid not in PENDING_SKIN_TRADES:
        return await message.answer("У вас нет активных заявок на обмен.", reply_markup=kb_main())

    data = PENDING_SKIN_TRADES.pop(uid)
    data['task'].cancel()

    sender_id = data['sender_id']
    skin_type = data['type']
    trade_id = f"sktr_{sender_id}_{uid}"

    ACTIVE_SKIN_TRADES[trade_id] = {
        'p1': sender_id, 'p2': uid, 'type': skin_type,
        'p1_skin': None, 'p2_skin': None,
        'p1_ready': False, 'p2_ready': False,
        'p1_msg_ids': [], 'p2_msg_ids': []
    }

    r_u = get_user(uid)
    raw_name_r = r_u[2] if (r_u and r_u[2]) else f"Игрок {uid}"
    receiver_link = f"<a href='tg://user?id={uid}'>{escape(raw_name_r)}</a>"
    type_lbl = "Пробужденный 💠" if skin_type == "awakened" else "Абсолютный 🔮"

    s_u = get_user(sender_id)
    raw_name_s = s_u[2] if (s_u and s_u[2]) else f"Игрок {sender_id}"
    sender_link = f"<a href='tg://user?id={sender_id}'>{escape(raw_name_s)}</a>"

    s_txt = (
        f"🎭 Заявка на обмен от {receiver_link} принята ✅\n\n"
        f"Редкость скина - {type_lbl}\n\n"
        f"В данный момент выберите скин для обмена ниже:"
    )
    await bot.send_message(sender_id, s_txt, reply_markup=kb_trade_cancel, parse_mode="HTML")
    await send_skin_selection_ui(bot, sender_id, trade_id, 1, 0)

    r_txt = (
        f"🎭 Заявка на обмен от {sender_link} принята ✅\n\n"
        f"Редкость скина - {type_lbl}\n\n"
        f"В данный момент игрок выбирает скин для обмена, ожидайте…"
    )
    await message.answer(r_txt, reply_markup=kb_trade_cancel, parse_mode="HTML")


@router.message(F.text == "Отменить трейд ❌")
async def cancel_active_skin_trade(message: types.Message, bot: Bot):
    uid = message.from_user.id
    from handlers import kb_main
    to_del, other_id = None, None

    for tid, tdata in ACTIVE_SKIN_TRADES.items():
        if tdata['p1'] == uid or tdata['p2'] == uid:
            to_del = tid
            other_id = tdata['p2'] if tdata['p1'] == uid else tdata['p1']
            break

    if to_del:
        tdata = ACTIVE_SKIN_TRADES[to_del]
        for mid in tdata.get('p1_msg_ids', []):
            try:
                await bot.delete_message(tdata['p1'], mid)
            except:
                pass
        for mid in tdata.get('p2_msg_ids', []):
            try:
                await bot.delete_message(tdata['p2'], mid)
            except:
                pass

        del ACTIVE_SKIN_TRADES[to_del]
        await message.answer("❌ Вы отменили трейд.", reply_markup=kb_main())
        await bot.send_message(other_id, "❌ Партнер отменил трейд.", reply_markup=kb_main())
    else:
        await message.answer("У вас нет активного трейда.", reply_markup=kb_main())


async def send_skin_selection_ui(bot: Bot, uid: int, trade_id: str, player_num: int, page: int, message_to_edit=None):
    tdata = ACTIVE_SKIN_TRADES.get(trade_id)
    if not tdata: return

    other_id = tdata['p2'] if player_num == 1 else tdata['p1']
    skin_type = tdata['type']

    from database.db import get_all_user_skins_by_type
    my_skins = get_all_user_skins_by_type(uid, skin_type)
    their_skins = get_all_user_skins_by_type(other_id, skin_type)

    diff = list(set(my_skins) - set(their_skins))
    diff.sort(key=lambda cid: CARDS[cid]['speed'] + CARDS[cid]['strength'] + CARDS[cid]['intellect'], reverse=True)

    per_page = 10
    total_pages = max(1, (len(diff) + per_page - 1) // per_page)
    if page >= total_pages: page = total_pages - 1
    if page < 0: page = 0

    start = page * per_page
    curr_skins = diff[start:start + per_page]

    bld = InlineKeyboardBuilder()
    for cid in curr_skins:
        c = CARDS[cid]
        btn_txt = f"• {c['name']} {c['speed']} | {c['strength']} | {c['intellect']}"
        bld.button(text=btn_txt, callback_data=f"sksel:{trade_id}:{player_num}:{cid}")
    bld.adjust(1)

    nav = []
    if page > 0: nav.append(
        InlineKeyboardButton(text="⬅️", callback_data=f"sksel_p:{trade_id}:{player_num}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"Стр. {page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1: nav.append(
        InlineKeyboardButton(text="➡️", callback_data=f"sksel_p:{trade_id}:{player_num}:{page + 1}"))
    bld.row(*nav)

    txt = "👇 <b>Выберите скин для обмена из вашей коллекции:</b>"

    if message_to_edit:
        await message_to_edit.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    else:
        await bot.send_message(uid, txt, reply_markup=bld.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("sksel_p:"))
async def skin_sel_page_cb(cq: CallbackQuery):
    parts = cq.data.split(":")
    trade_id, p_num, page = parts[1], int(parts[2]), int(parts[3])
    await send_skin_selection_ui(cq.bot, cq.from_user.id, trade_id, p_num, page, cq.message)
    await cq.answer()


@router.callback_query(F.data.startswith("sksel:"))
async def skin_sel_action_cb(cq: CallbackQuery):
    parts = cq.data.split(":")
    trade_id, p_num, cid = parts[1], int(parts[2]), parts[3]

    tdata = ACTIVE_SKIN_TRADES.get(trade_id)
    if not tdata: return await cq.answer("Трейд не найден или отменен.", show_alert=True)

    c = CARDS[cid]
    stype = tdata['type']
    pool = ABSOLUTE_SKIN if stype == "absolute" else AWAKENED_SKIN
    is_v = (stype == "absolute")
    asset_path = f"images/cards/{pool[cid].get('skin_video_file') or pool[cid].get('skin_art_file')}"

    try:
        await cq.message.delete()
    except:
        pass

    if p_num == 1:
        tdata['p1_skin'] = cid
        p1_id = tdata['p1']
        p2_id = tdata['p2']

        s_u = get_user(p1_id)
        raw_name1 = s_u[2] if (s_u and s_u[2]) else f"Игрок {p1_id}"
        name1_link = f"<a href='tg://user?id={p1_id}'>{escape(raw_name1)}</a>"

        # 1. Показываем Игроку 1 визуал того, что он выбрал
        txt_p1 = f"✅ Вы выбрали скин для карты <b>«{c['name']}»</b>!\n\n<i>Ожидайте, пока партнер выберет свой скин...</i>"
        if is_v:
            msg_p1 = await send_cached_video(cq.bot, chat_id=p1_id, file_path=asset_path, caption=txt_p1,
                                             width=c.get("width", 960), height=c.get("height", 1280), parse_mode="HTML",
                                             supports_streaming=True)
        else:
            msg_p1 = await cq.bot.send_photo(chat_id=p1_id, photo=FSInputFile(asset_path), caption=txt_p1,
                                             parse_mode="HTML")
        tdata['p1_msg_ids'].append(msg_p1.message_id)

        # 2. Показываем Игроку 2 визуал того, что выбрал Игрок 1
        txt_p2 = f"🎭 {name1_link} выбрал скин для карты <b>{c['name']}</b>!"
        if is_v:
            msg_p2 = await send_cached_video(cq.bot, chat_id=p2_id, file_path=asset_path, caption=txt_p2,
                                             width=c.get("width", 960), height=c.get("height", 1280), parse_mode="HTML",
                                             supports_streaming=True)
        else:
            msg_p2 = await cq.bot.send_photo(chat_id=p2_id, photo=FSInputFile(asset_path), caption=txt_p2,
                                             parse_mode="HTML")
        tdata['p2_msg_ids'].append(msg_p2.message_id)

        # 3. Присылаем меню выбора для Игрока 2
        await send_skin_selection_ui(cq.bot, p2_id, trade_id, 2, 0)

    elif p_num == 2:
        tdata['p2_skin'] = cid
        p2_id = tdata['p2']

        txt_p2_self = f"✅ Вы выбрали скин для карты <b>{c['name']}</b>!"
        if is_v:
            msg_p2_self = await send_cached_video(cq.bot, chat_id=p2_id, file_path=asset_path, caption=txt_p2_self,
                                                  width=c.get("width", 960), height=c.get("height", 1280),
                                                  parse_mode="HTML", supports_streaming=True)
        else:
            msg_p2_self = await cq.bot.send_photo(chat_id=p2_id, photo=FSInputFile(asset_path), caption=txt_p2_self,
                                                  parse_mode="HTML")
        tdata['p2_msg_ids'].append(msg_p2_self.message_id)

        await send_trade_preview_screen(cq.bot, trade_id)

    await cq.answer()


# ==========================================
# 🖼 ЭТАП СРАВНЕНИЯ И ФИНИША (МЕДИАГРУППЫ)
# ==========================================

async def send_trade_preview_screen(bot: Bot, trade_id: str):
    tdata = ACTIVE_SKIN_TRADES.get(trade_id)
    if not tdata: return

    p1, p2 = tdata['p1'], tdata['p2']
    c1, c2 = CARDS[tdata['p1_skin']], CARDS[tdata['p2_skin']]
    stype = tdata['type']
    pool = ABSOLUTE_SKIN if stype == "absolute" else AWAKENED_SKIN

    u1, u2 = get_user(p1), get_user(p2)

    raw_name1 = u1[2] if (u1 and u1[2]) else f"Игрок {p1}"
    raw_name2 = u2[2] if (u2 and u2[2]) else f"Игрок {p2}"

    name1_link = f"<a href='tg://user?id={p1}'>{escape(raw_name1)}</a>"
    name2_link = f"<a href='tg://user?id={p2}'>{escape(raw_name2)}</a>"

    path1 = f"images/cards/{pool[tdata['p1_skin']].get('skin_video_file') or pool[tdata['p1_skin']].get('skin_art_file')}"
    path2 = f"images/cards/{pool[tdata['p2_skin']].get('skin_video_file') or pool[tdata['p2_skin']].get('skin_art_file')}"

    is_v = (stype == "absolute")
    type_lbl = "💠" if stype == "awakened" else "🔮"

    txt_for_p1 = (
        f"⚖️ <b>Трейд</b>\n\n"
        f"Вы отдаете:\n"
        f"🎭 <b>{c1['name']}</b> {type_lbl}\n\n"
        f"Вы получаете от {name2_link}:\n"
        f"🎭 <b>{c2['name']}</b> {type_lbl}"
    )

    txt_for_p2 = (
        f"⚖️ <b>Трейд</b>\n\n"
        f"Вы отдаете:\n"
        f"🎭 <b>{c2['name']}</b> {type_lbl}\n\n"
        f"Вы получаете от {name1_link}:\n"
        f"🎭 <b>{c1['name']}</b> {type_lbl}"
    )

    def make_media_group(path_give, path_get, caption, card_give, card_get):
        media = []
        if is_v:
            media.append(types.InputMediaVideo(
                media=FSInputFile(path_give),
                width=card_give.get("width", 960), height=card_give.get("height", 1280)
            ))
            media.append(types.InputMediaVideo(
                media=FSInputFile(path_get), caption=caption, parse_mode="HTML",
                width=card_get.get("width", 960), height=card_get.get("height", 1280)
            ))
        else:
            media.append(types.InputMediaPhoto(media=FSInputFile(path_give)))
            media.append(types.InputMediaPhoto(media=FSInputFile(path_get), caption=caption, parse_mode="HTML"))
        return media

    msgs1 = await bot.send_media_group(p1, media=make_media_group(path1, path2, txt_for_p1, c1, c2))
    bld1 = InlineKeyboardBuilder()
    bld1.row(InlineKeyboardButton(text="Подтвердить 🤝", callback_data=f"sktr_fin:confirm:1:{trade_id}"))
    bld1.row(InlineKeyboardButton(text="Отменить ❌", callback_data=f"sktr_fin:cancel:1:{trade_id}"))
    kb_msg1 = await bot.send_message(p1, "<i>Ожидаем подтверждения... 👇</i>", reply_markup=bld1.as_markup(),
                                     parse_mode="HTML")

    msgs2 = await bot.send_media_group(p2, media=make_media_group(path2, path1, txt_for_p2, c2, c1))
    bld2 = InlineKeyboardBuilder()
    bld2.row(InlineKeyboardButton(text="Подтвердить 🤝", callback_data=f"sktr_fin:confirm:2:{trade_id}"))
    bld2.row(InlineKeyboardButton(text="Отменить ❌", callback_data=f"sktr_fin:cancel:2:{trade_id}"))
    kb_msg2 = await bot.send_message(p2, "<i>Ожидаем подтверждения... 👇</i>", reply_markup=bld2.as_markup(),
                                     parse_mode="HTML")

    tdata['p1_msg_ids'].extend([m.message_id for m in msgs1] + [kb_msg1.message_id])
    tdata['p2_msg_ids'].extend([m.message_id for m in msgs2] + [kb_msg2.message_id])


@router.callback_query(F.data.startswith("sktr_fin:"))
async def skin_trade_finish_cb(cq: CallbackQuery, bot: Bot):
    parts = cq.data.split(":")
    action, p_num, trade_id = parts[1], int(parts[2]), parts[3]

    tdata = ACTIVE_SKIN_TRADES.get(trade_id)
    if not tdata:
        return await cq.answer("⚠️ Данная сделка уже завершена или аннулирована.", show_alert=True)

    from handlers import kb_main
    p1, p2 = tdata['p1'], tdata['p2']

    if action == "cancel":
        for mid in tdata.get('p1_msg_ids', []):
            try:
                await bot.delete_message(p1, mid)
            except:
                pass
        for mid in tdata.get('p2_msg_ids', []):
            try:
                await bot.delete_message(p2, mid)
            except:
                pass

        del ACTIVE_SKIN_TRADES[trade_id]
        await bot.send_message(p1, "❌ Обмен обликами был отменен одной из сторон.", reply_markup=kb_main())
        await bot.send_message(p2, "❌ Обмен обликами был отменен одной из сторон.", reply_markup=kb_main())
        return await cq.answer()

    if action == "confirm":
        new_text = "<b>✅ Вы подтвердили обмен! Ожидаем партнера...</b>"
        try:
            await cq.message.edit_text(text=new_text, reply_markup=None, parse_mode="HTML")
        except:
            pass

        if p_num == 1:
            tdata['p1_ready'] = True
        else:
            tdata['p2_ready'] = True

        await cq.answer("✅ Подтверждено!", show_alert=False)

        if tdata['p1_ready'] and tdata['p2_ready']:
            for mid in tdata.get('p1_msg_ids', []):
                try:
                    await bot.delete_message(p1, mid)
                except:
                    pass
            for mid in tdata.get('p2_msg_ids', []):
                try:
                    await bot.delete_message(p2, mid)
                except:
                    pass

            from database.db import swap_skins
            # ВНИМАНИЕ: Ловим статус успешности обмена из базы
            success = swap_skins(p1, tdata['p1_skin'], p2, tdata['p2_skin'], tdata['type'])

            # 🛑 ЕСЛИ КТО-ТО ПЫТАЕТСЯ ДЮПАТЬ ИЛИ СКИНА УЖЕ НЕТ:
            if not success:
                del ACTIVE_SKIN_TRADES[trade_id]
                await bot.send_message(p1,
                                       "❌ Трейд отменен: Ошибка транзакции. (Возможно, скин уже был продан или у вас уже есть такой облик).",
                                       reply_markup=kb_main())
                await bot.send_message(p2,
                                       "❌ Трейд отменен: Ошибка транзакции. (Возможно, скин уже был продан или у вас уже есть такой облик).",
                                       reply_markup=kb_main())
                return

            # ✅ Если все чисто, продолжаем обычную выдачу наград:
            c1, c2 = CARDS[tdata['p1_skin']], CARDS[tdata['p2_skin']]
            stype = tdata['type']
            pool = ABSOLUTE_SKIN if stype == "absolute" else AWAKENED_SKIN
            type_lbl = "Пробужденный 💠" if stype == "awakened" else "Абсолютный 🔮"
            is_v = (stype == "absolute")

            u1, u2 = get_user(p1), get_user(p2)
            raw_n1 = u1[2] if (u1 and u1[2]) else f"Игрок {p1}"
            raw_n2 = u2[2] if (u2 and u2[2]) else f"Игрок {p2}"

            name1_link = f"<a href='tg://user?id={p1}'>{escape(raw_n1)}</a>"
            name2_link = f"<a href='tg://user?id={p2}'>{escape(raw_n2)}</a>"

            path1 = f"images/cards/{pool[tdata['p1_skin']].get('skin_video_file') or pool[tdata['p1_skin']].get('skin_art_file')}"
            path2 = f"images/cards/{pool[tdata['p2_skin']].get('skin_video_file') or pool[tdata['p2_skin']].get('skin_art_file')}"

            fin_txt1 = (
                f"🎊 Вы совершили успешный трейд с {name2_link} и получили новый скин с трейда\n"
                f"🎭 <b>{c2['name']}</b> ({type_lbl})"
            )
            fin_txt2 = (
                f"🎊 Вы совершили успешный трейд с {name1_link} и получили новый скин с трейда\n"
                f"🎭 <b>{c1['name']}</b> ({type_lbl})"
            )

            if is_v:
                await send_cached_video(bot, chat_id=p1, file_path=path2, caption=fin_txt1, width=c2.get("width", 960),
                                        height=c2.get("height", 1280), reply_markup=kb_main())
            else:
                await bot.send_photo(chat_id=p1, photo=FSInputFile(path2), caption=fin_txt1, parse_mode="HTML",
                                     reply_markup=kb_main())

            if is_v:
                await send_cached_video(bot, chat_id=p2, file_path=path1, caption=fin_txt2, width=c1.get("width", 960),
                                        height=c1.get("height", 1280), reply_markup=kb_main())
            else:
                await bot.send_photo(chat_id=p2, photo=FSInputFile(path1), caption=fin_txt2, parse_mode="HTML",
                                     reply_markup=kb_main())

            del ACTIVE_SKIN_TRADES[trade_id]

@router.callback_query(F.data.startswith("view_skins_slider:"))
async def view_skins_slider(cq: CallbackQuery):
    parts = cq.data.split(":")
    skin_type = parts[1]
    index = int(parts[2])
    uid = cq.from_user.id

    rows = db_exec("SELECT card_id FROM skins_inv WHERE user_id = ? AND skin_type = ?", (uid, skin_type), fetchall=True)
    owned_cids = [r[0] for r in rows] if rows else []

    if not owned_cids:
        type_name = "Пробужденный 💠" if skin_type == "awakened" else "Абсолютный 🔮"
        txt = (
            f"🔒 <b>Коллекция пуста</b>\n\n"
            f"У вас пока нет скинов редкости <b>{type_name}</b>.\n"
            f"Вы можете получить их в магазине! 🛒"
        )
        bld = InlineKeyboardBuilder()
        bld.button(text="🔙 Назад к категориям", callback_data="my_skins_categories")

        try:
            await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        except Exception:
            try:
                await cq.message.delete()
            except Exception:
                pass
            await cq.message.answer(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
        return await cq.answer()

    total = len(owned_cids)
    if index >= total: index = 0
    if index < 0: index = total - 1

    asset_path, is_video, txt, reply_markup = _render_skin_slide_data(uid, skin_type, index, owned_cids)
    c = CARDS.get(owned_cids[index])

    await cq.message.delete()

    if is_video:
        await send_cached_video(
            cq.bot, chat_id=cq.message.chat.id, file_path=asset_path, caption=txt,
            width=c.get("width", 960), height=c.get("height", 1280),
            reply_markup=reply_markup, parse_mode="HTML", supports_streaming=True
        )
    else:
        await cq.message.answer_photo(
            photo=FSInputFile(asset_path), caption=txt, parse_mode="HTML", reply_markup=reply_markup
        )
    await cq.answer()


@router.callback_query(F.data.startswith("sl_sk_act:"))
async def slider_skin_action(cq: CallbackQuery):
    parts = cq.data.split(":")
    action = parts[1]
    skin_type = parts[2]
    cid = parts[3]
    index = int(parts[4])
    uid = cq.from_user.id

    if action == "eq":
        equip_skin(uid, cid, skin_type)
        await cq.answer("✨ Скин успешно надет!")
    else:
        unequip_skin(uid, cid)
        await cq.answer("❌ Скин снят!")

    # БЕСШОВНОЕ ОБНОВЛЕНИЕ: Пересобираем только текст и кнопки в ту же секунду!
    rows = db_exec("SELECT card_id FROM skins_inv WHERE user_id = ? AND skin_type = ?", (uid, skin_type), fetchall=True)
    owned_cids = [r[0] for r in rows] if rows else []

    if owned_cids:
        _, _, txt, reply_markup = _render_skin_slide_data(uid, skin_type, index, owned_cids)
        try:
            # Магия aiogram: меняем только описание и разметку кнопок под медиафайлом
            await cq.message.edit_caption(caption=txt, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            pass