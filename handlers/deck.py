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
                        NORMAL_PASS, ROYALE_PASS, is_divine)
from database.db import (db_exec, init_db, get_user, add_user, get_rank,
                         pull_random_card, give_card_to_user)
from handlers import (router, TradeState, SettingsState, PromoState,
                      MATCH_QUEUE, GAMES, PENDING_TRADES, kb_main)
from media_cache import send_cached_video


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

RARITY_SLUG_TO_LABEL = {slug: label for _, slug, label in RARITY_FILTERS}

class SearchState(StatesGroup):
    waiting_for_query = State()

def _card_power(cid: str) -> int:
    c = CARDS.get(cid)
    if not c:
        return 0
    return c.get('speed', 0) + c.get('strength', 0) + c.get('intellect', 0)


def _get_user_cids(uid: int) -> list[str]:
    """Возвращает уникальные card_id из инвентаря И сундука пользователя."""
    rows = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (uid,), fetchall=True)
    stash_rows = db_exec("SELECT card_id FROM cards_stash WHERE user_id = ?", (uid,), fetchall=True)

    seen = set()
    result = []
    # Объединяем результаты обеих таблиц
    for (cid,) in rows + stash_rows:
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
        "🧳 <b>Мои Карты</b>",
        "",
        f"📦 Коллекция: <b>{total}</b> из <b>{total_all}</b> карт",
        "",
        "💎 По редкостям:",
    ]
    for _, slug, label in RARITY_FILTERS:
        count = sum(1 for cid in all_cids if CARDS.get(cid, {}).get('rarity') == label)
        if count:
            lines.append(f"  {label}: <b>{count}</b>")

    if total:
        top_cids = _sort_cards(all_cids)[:3]
        lines.append("")
        lines.append("⚡️ Топ-3 по силе:")
        for i, cid in enumerate(top_cids, 1):
            c = CARDS.get(cid)
            if c:
                power = _card_power(cid)
                lines.append(f"  {i}. {c['name']} {c['rarity'].split()[-1]} — {power} 💥")

    lines.append("")
    lines.append("Выбери раздел 👇")
    return "\n".join(lines)

def _build_inv_main_kb() -> InlineKeyboardMarkup:
    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 Просмотр карт",    callback_data="inv_view:0:all:0")
    bld.button(text="🔍 Поиск по названию", callback_data="inv_search_start")
    bld.button(text="📊 Коллекция",         callback_data="inv_collection")
    bld.button(text="📦 Сундук",            callback_data="stash_menu:0:inv")
    bld.adjust(1,1,2)
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

# ── Поиск по названию ──────────────────────────────────────────────────

@router.callback_query(F.data == "inv_search_start")
async def inv_search_start(cq: CallbackQuery, state: FSMContext):
    await state.set_state(SearchState.waiting_for_query)
    bld = InlineKeyboardBuilder()
    bld.button(text="❌ Отмена", callback_data="inv_main")
    try:
        await cq.message.edit_text(
            "🔍 <b>Поиск карты</b>\n\nВведите название (или его часть):",
            parse_mode="HTML",
            reply_markup=bld.as_markup()
        )
    except Exception:
        await cq.message.delete()
        await cq.message.answer(
            "🔍 <b>Поиск карты</b>\n\nВведите название (или его часть):",
            parse_mode="HTML",
            reply_markup=bld.as_markup()
        )
    await cq.answer()

@router.message(StateFilter(SearchState.waiting_for_query))
async def inv_search_query(msg: types.Message, state: FSMContext):
    await state.clear()
    query = (msg.text or "").strip().lower()
    if not query:
        return await msg.answer("Пустой запрос. Попробуйте ещё раз.", parse_mode="HTML")

    user_cids = _get_user_cids(msg.from_user.id)
    matched = [
        cid for cid in user_cids
        if query in CARDS.get(cid, {}).get('name', '').lower()
    ]
    matched = _sort_cards(matched)
    if not matched:
        bld = InlineKeyboardBuilder()
        bld.button(text="🔙 Назад", callback_data="inv_main")
        return await msg.answer(
            f"🔍 По запросу «<b>{msg.text}</b>» карт не найдено.",
            parse_mode="HTML",
            reply_markup=bld.as_markup()
        )

    # Сохраняем результаты поиска и показываем первую страницу
    await _send_search_results(msg, matched, page=0, query=msg.text)

async def _send_search_results(target, matched: list, page: int, query: str):
    """Отправляет страницу результатов поиска."""
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
            power = _card_power(cid)
            bld.row(types.InlineKeyboardButton(
                text=f"{c['name']} {emoji} · {power}💥",
                callback_data=f"viewcard:{cid}:{page}:all:0"
            ))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️", callback_data=f"inv_search_page:{page - 1}:{query}"))
    nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(text="➡️", callback_data=f"inv_search_page:{page + 1}:{query}"))
    if nav:
        bld.row(*nav)

    bld.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="inv_main"))

    txt = f"🔍 Результаты по «<b>{query}</b>»: {len(matched)} карт"

    if isinstance(target, types.Message):
        await target.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())
    else:
        try:
            await target.message.edit_text(txt, parse_mode="HTML", reply_markup=bld.as_markup())
        except Exception:
            await target.message.delete()
            await target.message.answer(txt, parse_mode="HTML", reply_markup=bld.as_markup())

@router.callback_query(F.data.startswith("inv_search_page:"))
async def inv_search_page(cq: CallbackQuery):
    parts = cq.data.split(":", 2)
    page = int(parts[1])
    query = parts[2] if len(parts) > 2 else ""

    user_cids = _get_user_cids(cq.from_user.id)
    matched = _sort_cards([
        cid for cid in user_cids
        if query.lower() in CARDS.get(cid, {}).get('name', '').lower()
    ])
    await _send_search_results(cq, matched, page=page, query=query)
    await cq.answer()

# ── Просмотр с фильтром и листалкой ───────────────────────────────────

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
    user_cids = _get_user_cids(cq.from_user.id)
    user_owned = set(user_cids)

    total_cards = len(CARDS)
    owned_total = len(user_owned)
    total_pct = int((owned_total / total_cards) * 100) if total_cards else 0

    # Прогресс-бар
    filled = total_pct // 10
    bar = "█" * filled + "░" * (10 - filled)

    lines = [
        "📊 <b>Коллекция</b>",
        "",
        f"Прогресс: [{bar}] {total_pct}%",
        f"Собрано: <b>{owned_total}</b> / {total_cards} карт",
        "",
        "💎 <b>По редкостям:</b>",
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

    # Вселенные
    series_map: dict[str, dict] = {}
    for cid, c in CARDS.items():
        s = c.get('series', 'Неизвестно')
        series_map.setdefault(s, {'total': 0, 'owned': 0})
        series_map[s]['total'] += 1
        if cid in user_owned:
            series_map[s]['owned'] += 1

    sorted_series = sorted(series_map.items(), key=lambda x: x[1]['owned'], reverse=True)

    lines += ["", "🪐 <b>Вселенные:</b>", "<blockquote>"]
    series_lines = []
    for s_name, s_data in sorted_series:
        pct_s = int((s_data['owned'] / s_data['total']) * 100) if s_data['total'] else 0
        mark = "✅" if pct_s == 100 else ("🔥" if pct_s >= 50 else "")
        series_lines.append(f"{mark} {s_name}: {s_data['owned']}/{s_data['total']} ({pct_s}%)")
    lines.append("\n".join(series_lines))
    lines.append("</blockquote>")

    txt = "\n".join(lines)
    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 К картам", callback_data="inv_view:0:all")
    bld.button(text="🔙 Назад",    callback_data="inv_main")
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

    power = _card_power(cid)
    power_filled = min(10, power // 30)
    power_bar = "▰" * power_filled + "▱" * (10 - power_filled)

    txt = (
        f"🃏 <b>{c['name']}</b>\n\n"
        f"🔮 Редкость: {c['rarity']}\n"
        f"👊 Стиль боя: {c['style']}\n"
        f"🪐 Вселенная: {c.get('series', 'Неизвестно')}\n\n"
        f"⚡️ Скорость:   <b>{c['speed']}</b>\n"
        f"💪 Сила:       <b>{c['strength']}</b>\n"
        f"🧠 Интеллект:  <b>{c['intellect']}</b>\n\n"
        f"💥 Мощь: {power}  [{power_bar}]"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="〽️ Трейд", callback_data=f"trade_init:{cid}")

    if is_divine(cid) and c.get("video"):
        bld.button(text="Показать арт 👀", callback_data=f"divshow:{cid}:art:{page}:{r_filter}:{excl_filter}")

    bld.button(text="🔙 Назад", callback_data=f"inv_view:{page}:{r_filter}:{excl_filter}")
    bld.adjust(1)
    await cq.message.delete()
    if is_divine(cid) and c.get("video"):
        await send_cached_video(
            cq.bot,
            chat_id=cq.message.chat.id,
            file_path=f"images/cards/{c['video']}",
            caption=txt,
            width=c.get("width", 960),
            height=c.get("height", 1280),
            reply_markup=bld.as_markup(),
            supports_streaming=True,
            parse_mode="HTML"
        )
    else:
        await cq.message.answer_photo(
            photo=FSInputFile(f"images/cards/{c['file']}"),
            caption=txt,
            parse_mode="HTML",
            reply_markup=bld.as_markup()
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
    await cq.answer()
    # ВАЖНО: Делим на 2 части (maxsplit=1), чтобы не обрезать ID карты (например battle:EPIC)
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
    # ВАЖНО: Делим на 3 части (maxsplit=2), чтобы сохранить ID карты
    cid = cq.data.split(":", 2)[2]
    c = CARDS.get(cid)

    if not c:
        return await cq.message.answer("❌ Ошибка: карта не найдена.")

    bot_info = await cq.bot.get_me()
    raw_payload = f"trade:{cq.from_user.id}:{cid}"
    b64_payload = base64.urlsafe_b64encode(raw_payload.encode()).decode().rstrip("=")
    trade_link = f"https://t.me/{bot_info.username}?start={b64_payload}"

    share_text = f"Привет! Давай меняться! Я предлагаю карту {c['name']} ({c['rarity']}) в боте. Переходи по ссылке и делай свое предложение!"
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
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.message.answer("❌ Трейд более не актуален или был отменен.")

    sender_card_id = t['sender_card']
    sender_card_data = CARDS.get(sender_card_id)
    if not sender_card_data:
        return await cq.message.answer("❌ Ошибка: карта инициатора не найдена.")

    rarity = sender_card_data['rarity']
    inv_data = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)

    valid_cards = []
    for (cid,) in inv_data:
        card_info = CARDS.get(cid)
        if card_info and card_info.get('rarity') == rarity:
            valid_cards.append(cid)

    valid_cards = list(set(valid_cards))

    if not valid_cards:
        await cq.message.delete()
        await cq.message.answer(f"❌ У вас нет карт редкости <b>{rarity}</b> для равноценного обмена. Трейд отменен.",
                                parse_mode="HTML")
        PENDING_TRADES.pop(sender_id, None)
        try:
            await cq.bot.send_message(sender_id, "❌ Игрок не может принять трейд: нет подходящих карт по редкости.")
        except:
            pass
        return

    bld = InlineKeyboardBuilder()
    for cid in valid_cards[:40]:
        name = CARDS[cid]['name']
        # ВАЖНО: ID карты может содержать двоеточие, передаем как есть
        bld.button(text=name, callback_data=f"trade_p2_conf:{sender_id}:{cid}")

    bld.button(text="Отказаться ❌", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(2)

    await cq.message.delete()
    await cq.message.answer("🎴 <b>Выберите вашу карту, которую отдадите взамен:</b>", reply_markup=bld.as_markup(),
                            parse_mode="HTML")


@router.callback_query(F.data.startswith("trade_p2_conf:"))
async def trade_p2_conf(cq: CallbackQuery):
    await cq.answer()
    # ИСПРАВЛЕНИЕ-КИЛЛЕР БАГОВ: Делим строку ровно на 3 куска!
    # Если ID карты `battle:EPIC`, он не будет отрезан
    parts = cq.data.split(":", 2)
    sender_id = int(parts[1])
    p2_card = parts[2]

    t = PENDING_TRADES.get(sender_id)
    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.message.answer("❌ Трейд не актуален.")

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

    txt = (
        f"⚖️ <b>Подготовка к обмену</b> с <a href='tg://user?id={sender_id}'>{sender_name}</a>\n\n"
        f"<blockquote>📤 <b>Вы отдаёте:</b> {c_receiver['name']} ({c_receiver['rarity']})\n"
        f"📥 <b>Вы получаете:</b> {c_sender['name']} ({c_sender['rarity']})</blockquote>\n\n"
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

    await cq.message.edit_text("⏳ Ожидание подтверждения от инициатора сделки...")

    c1 = CARDS[t['sender_card']]
    c2 = CARDS[t['receiver_card']]

    has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, t['receiver_card']), fetch=True)
    warning = "\n<i>(⚠️ Осторожно: у вас уже есть эта копия)</i>" if has_card else ""
    p2_name = escape(cq.from_user.first_name)

    txt = (
        f"✨ <b>Встречное предложение получено!</b>\n\n"
        f"Игрок <a href='tg://user?id={cq.from_user.id}'>{p2_name}</a> выбрал карту для обмена.\n"
        f"<blockquote>📤 <b>Вы отдаёте:</b> {c1['name']} ({c1['rarity']})\n"
        f"📥 <b>Вы получаете:</b> {c2['name']} ({c2['rarity']}){warning}</blockquote>\n\n"
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
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != p2_id:
        return await cq.answer("Трейд не актуален.", show_alert=True)

    c1_id = t['sender_card']
    c2_id = t['receiver_card']

    p1_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c1_id), fetch=True)
    p2_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c2_id), fetch=True)

    if not p1_has or not p2_has:
        PENDING_TRADES.pop(sender_id, None)
        await cq.message.delete()
        await cq.message.answer("❌ <b>Трейд сорвался:</b> у одного из игроков больше нет нужной карты.", parse_mode="HTML")
        try:
            await cq.bot.send_message(p2_id, "❌ <b>Трейд сорвался:</b> у одного из игроков больше нет нужной карты.", parse_mode="HTML")
        except:
            pass
        return

    # БЕЗОПАСНОЕ УДАЛЕНИЕ: по одной копии
    row1 = db_exec("SELECT rowid FROM cards_inv WHERE user_id = ? AND card_id = ? LIMIT 1", (sender_id, c1_id), fetch=True)
    if row1:
        db_exec("DELETE FROM cards_inv WHERE rowid = ?", (row1[0],))
    db_exec("DELETE FROM decks WHERE user_id = ? AND card_id = ?", (sender_id, c1_id))
    try:
        db_exec(
            "DELETE FROM multi_deck_slots WHERE card_id = ? AND deck_id IN (SELECT deck_id FROM multi_decks WHERE user_id = ?)",
            (c1_id, sender_id))
    except:
        pass

    row2 = db_exec("SELECT rowid FROM cards_inv WHERE user_id = ? AND card_id = ? LIMIT 1", (p2_id, c2_id), fetch=True)
    if row2:
        db_exec("DELETE FROM cards_inv WHERE rowid = ?", (row2[0],))
    db_exec("DELETE FROM decks WHERE user_id = ? AND card_id = ?", (p2_id, c2_id))
    try:
        db_exec(
            "DELETE FROM multi_deck_slots WHERE card_id = ? AND deck_id IN (SELECT deck_id FROM multi_decks WHERE user_id = ?)",
            (c2_id, p2_id))
    except:
        pass

    # ИСПРАВЛЕНИЕ: Выдаем карты с защитой (как было в старом рабочем файле)
    p1_has_c2 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c2_id), fetch=True)
    p2_has_c1 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c1_id), fetch=True)

    if not p1_has_c2:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (sender_id, c2_id))

    if not p2_has_c1:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (p2_id, c1_id))

    PENDING_TRADES.pop(sender_id, None)

    # Достаем ники для красивого финала
    u1 = get_user(sender_id)
    u2 = get_user(p2_id)
    n1 = escape(u1[2] if u1 and u1[2] else f"Игрок {sender_id}")
    n2 = escape(u2[2] if u2 and u2[2] else f"Игрок {p2_id}")

    await cq.message.delete()

    # Сообщение инициатору
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

    # Сообщение получателю
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