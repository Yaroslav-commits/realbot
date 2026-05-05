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
                           CallbackQuery, LabeledPrice, PreCheckoutQuery,
                           FSInputFile)
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
                         pull_random_card, give_card_to_user, try_use_promo, grant_retroactive_royale_pass,
                         get_user_by_ref_code, get_referral_count, get_users_for_cooldown_notify,
                         mark_cooldown_notified, reset_cooldown_notified, toggle_notifications)
from handlers import (router, TradeState, SettingsState, PromoState,
                      MATCH_QUEUE, GAMES, PENDING_TRADES, kb_main)


# ================== HANDLERS ==================
@router.message(Command("start"))
async def start_cmd(msg: types.Message):
    args = msg.text.split()
    referred_by = None
    if len(args) > 1:
        ref_code = args[1]  # Берем код как он есть
        referrer = get_user_by_ref_code(ref_code)
        if referrer:
            referred_by = referrer[0]

    # Вызываем добавление пользователя и получаем сумму награды, если это был реферал
    reward_amount = add_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name, referred_by)

    # Если награда выдана, уведомляем владельца ссылки
    if reward_amount and referred_by:
        try:
            await msg.bot.send_message(
                referred_by,
                f"🤝 По твоей ссылке зашёл новый игрок!\nТебе начислено: <b>{reward_amount}💴</b> и <b>5💳</b>"
            )
        except Exception:
            pass  # Если у владельца бот заблокирован

    await msg.answer(
        "🎴 Добро пожаловать в *ManhwCard*! 🎴\n\n"
        "Здесь ты сможешь собирать карты любимых персонажей, сражаться с другими игроками и обмениваться редкими картами 💥\n\n"
        "📢 [Канал](https://t.me/manhwcard)\n"
        "💬 [Чат](https://t.me/manhwcardchat)\n\n"
        "Выбирай действие ниже и начинай своё приключение 👇",
        reply_markup=kb_main(),
        parse_mode="Markdown"
    )

@router.message(F.text == "⛩️ Банды")
async def gangs(msg: types.Message):
    await msg.answer("В разработке")

# ============ ГАЧА ============
@router.message(F.text == "🎴 Получить карту")
@router.message(Command("get"))
async def get_card_cmd(msg: types.Message):
    uid = msg.from_user.id
    u = get_user(uid)
    if not u:
        return

    attempts = u[6]
    now = datetime.now()

    # Сначала проверяем кулдаун (если попыток нет)
    if attempts <= 0:
        try:
            last_get = datetime.strptime(u[11], "%Y-%m-%d %H:%M:%S")
        except Exception:
            last_get = datetime.min
        if (now - last_get).total_seconds() < GET_COOLDOWN_HOURS * 3600:
            rem = int(GET_COOLDOWN_HOURS * 3600 - (now - last_get).total_seconds())
            return await msg.answer(f"⏳ Следующая карта через {rem // 3600}ч {(rem % 3600) // 60}м.")

    # Получаем карту
    card_key = pull_random_card()
    if not card_key:
        return await msg.answer("❌ Ошибка: пул карт пуст или произошла ошибка.")

    is_new, krw, c = give_card_to_user(uid, card_key)

    # Если карта или данные повреждены — не списываем попытку
    if c is None:
        return await msg.answer("❌ Ошибка при получении карты. Попробуйте снова.")
    # Формируем текст
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

    # Пытаемся отправить фото. Если не вышло — шлём текст.
    try:
        photo_file = FSInputFile(f"images/cards/{c['file']}")
        await msg.answer_photo(photo=photo_file, caption=txt, has_spoiler=True)
    except Exception:
        try:
            await msg.answer(txt)
        except Exception:
            return await msg.answer("❌ Не удалось открутить. Попробуйте снова.")

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

    user_link = f'<a href="tg://user?id={u[0]}">{u[2]}</a>'
    txt = (
        f"👤 Профиль {user_link} 🧩\n"
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
    bg_file = FSInputFile(f"images/backgrounds/{bg_data.get('file')}")
    try:
        if bg_key in VIDEO_BGS:
            await msg.answer_video(
                video=bg_file, caption=txt,
                reply_markup=bld.as_markup(), parse_mode="HTML",
                supports_streaming=True,
                width=bg_data.get('width'),
                height=bg_data.get('height')
            )
        else:
            await msg.answer_photo(photo=bg_file, caption=txt,
                                   reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        await msg.answer(f"{txt}\n\n[Фон не загрузился.]",
                         reply_markup=bld.as_markup(), parse_mode="HTML")


# ============ НАСТРОЙКИ ============
@router.callback_query(F.data == "settings")
async def settings_cq(cq: CallbackQuery):
    u = get_user(cq.from_user.id)
    if not u:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    notif_on = bool(u[17])  # notifications (индекс 17)
    notif_emoji = "✅" if notif_on else "☑️"
    notif_text = "Включить уведомления" if notif_on else "Выключить уведомления"

    txt = (
        f"⚙️ Настройки\n"
        f"Дата регистрации: {u[15]}\n"
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
        await cq.message.answer(txt, reply_markup=bld.as_markup())

    await cq.answer()


@router.message(Command("nick"))
async def change_nick(msg: types.Message):
    new_nick = msg.text.replace("/nick", "").strip()
    if not new_nick:
        return await msg.answer("Использование: /nick НовыйНик")
    db_exec("UPDATE users SET nickname = ? WHERE id = ?", (new_nick, msg.from_user.id))
    await msg.answer(f"✅ Ник изменен на {new_nick}")


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
        f"👥 Всего приглашенных: {ref_count}\n\n"
        f"Приглашай друзей! За каждого игрока, перешедшего по твоей ссылке, "
        f"<b>ты и твой друг</b> получите от 500💴 до 850💴 и 5💳 попыток бонусом!\n\n"
        f"⛓️‍💥 Твоя уникальная реферальная ссылка:\n<code>{ref_link}</code>"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="🔙 Назад", callback_data="settings")
    await cq.message.edit_text(txt, reply_markup=bld.as_markup(), parse_mode="HTML")
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

    txt = (
        f"⚙️ Настройки\n"
        f"Дата регистрации: {u[15]}\n"
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
        bg_file = bg_data.get('file')
        name = bg_data.get('name', 'Фон')
        caption = f"🌄 Предпросмотр фона: {name}"
        if itm in VIDEO_BGS:
            bg_data = BGS.get(itm, BGS['default'])
            await cq.message.answer_video(
                video=FSInputFile(f"images/backgrounds/{bg_file}"),
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
    Command(commands=["give_attempts", "give_card", "give_money", "give_title", "give_background", "give_diamond", "give_pass", "create_promo"]))
async def admin_cmds(msg: types.Message, state: FSMContext, bot: Bot):
    if msg.from_user.id not in ADMIN_IDS: return
    args = msg.text.split()
    cmd = args[0]

    if cmd == "/create_promo":
        await state.set_state(PromoState.waiting_for_promo_data)
        await msg.answer(
            "Отправь данные промокода в формате:\n[КОД] [ТИП: krw/atm/card/dia/pass] [ЗНАЧЕНИЕ] [КОЛ-ВО ИСПОЛЬЗОВАНИЙ]\n"
            "Пример: LOOKISM krw 500 10\n\n"
            "Типы:\n"
            "• krw — KRW 💴\n"
            "• atm — попытки 💳\n"
            "• card — карта (ключ)\n"
            "• dia — алмазы 💎\n"
            "• pass — Рояль Пасс (значение любое, например 1)")
        return

    # /give_pass — только 2 аргумента
    if cmd == "/give_pass":
        if len(args) < 2:
            return await msg.answer("Использование: /give_pass [ID пользователя]")
        uid = int(args[1])
        summary = grant_retroactive_royale_pass(uid)
        try:
            await bot.send_message(uid, f"🌠 Получен Рояль Пасс на этот месяц от администратора ✅{summary}")
        except Exception:
            pass
        return await msg.answer(f"✅ Рояль Пасс выдан пользователю {uid}!")

    if len(args) < 3:
        return await msg.answer("Ошибка аргументов. Формат: /команда [ID] [значение]")

    uid, val = int(args[1]), args[2]

    if cmd == "/give_attempts":
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (int(val), uid))
        try:
            await bot.send_message(uid, f"Получено {val}💳 попыток от администратора ✅")
        except Exception:
            pass
        await msg.answer(f"✅ Выдано пользователю {uid}!")

    elif cmd == "/give_money":
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (int(val), uid))
        try:
            await bot.send_message(uid, f"Получено {val}💴 от администратора ✅")
        except Exception:
            pass
        await msg.answer(f"✅ Выдано пользователю {uid}!")

    elif cmd == "/give_diamond":
        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (int(val), uid))
        try:
            await bot.send_message(uid, f"Получено {val}💎 Алмазов от администратора ✅")
        except Exception:
            pass
        await msg.answer(f"✅ Выдано пользователю {uid}!")

    elif cmd == "/give_card":
        c = CARDS.get(val)
        if not c:
            return await msg.answer(f"❌ Карта с ключом «{val}» не найдена!")
        # Прямая выдача (даже если дубликат)
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (uid, val))
        txt = (f"🃏 Получена новая боевая карта от администратора ✅\n\n"
               f"🎴 Персонаж: «{c['name']}»\n"
               f"🔮 Редкость: «{c['rarity']}»\n"
               f"👊 Стиль боя: «{c['style']}»\n"
               f"🪐 Вселенная: «{c.get('series', 'Неизвестно')}»\n\n"
               f"⚡️ Скорость: «{c['speed']}»\n"
               f"💪 Сила: «{c['strength']}»\n"
               f"🧠 Интеллект: «{c['intellect']}»")
        try:
            photo_file = FSInputFile(f"images/cards/{c['file']}")
            await bot.send_photo(uid, photo=photo_file, caption=txt)
        except Exception:
            pass
        await msg.answer(f"✅ Карта «{c['name']}» выдана пользователю {uid}!")
    elif cmd == "/give_title":
        title_name = TITLES.get(val, val)
        db_exec("INSERT INTO titles_inv (user_id, title_id) VALUES (?, ?)", (uid, val))
        try:
            await bot.send_message(uid, f"Получен титул «{title_name}» от администратора ✅")
        except Exception:
            pass
        await msg.answer(f"✅ Титул выдан пользователю {uid}!")

    elif cmd == "/give_background":
        bg_data = BGS.get(val)
        if not bg_data:
            return await msg.answer(f"❌ Фон с ключом «{val}» не найден!")
        db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, val))
        is_video = val in VIDEO_BGS
        try:
            bg_file = FSInputFile(f"images/backgrounds/{bg_data['file']}")
            if is_video:
                await bot.send_video(uid, video=bg_file,
                                     caption="Получен фон от администратора ✅",
                                     supports_streaming=True)
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
    if p_type not in ('krw', 'atm', 'card', 'dia', 'pass'):
        return await msg.answer("Неверный тип. Допустимые: krw, atm, card, dia, pass")
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
    elif p[0] == 'atm':
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (int(p[1]), uid))
        await msg.answer(f"✅ Промокод активирован! Вы получаете {p[1]} попыток 💳")
    elif p[0] == 'dia':
        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (int(p[1]), uid))
        await msg.answer(f"✅ Промокод активирован! Вы получаете {p[1]}💎 Алмазов")
    elif p[0] == 'pass':
        summary = grant_retroactive_royale_pass(uid)
        await msg.answer(f"✅ Промокод активирован! Вы получаете Рояль Пасс на этот месяц 🌠{summary}")
    elif p[0] == 'card':
        c = CARDS.get(p[1])
        if not c:
            return await msg.answer("✅ Промокод активирован, но карта не найдена!")
        is_new, krw_earned, card_data = give_card_to_user(uid, p[1])
        txt = (f"✅ Промокод активирован!\n\n"
               f"🃏 Получена новая боевая карта!\n\n"
               f"🎴 Персонаж: «{c['name']}»\n"
               f"🔮 Редкость: «{c['rarity']}»\n"
               f"👊 Стиль боя: «{c['style']}»\n"
               f"🪐 Вселенная: «{c.get('series', 'Неизвестно')}»\n\n"
               f"⚡️ Скорость: «{c['speed']}»\n"
               f"💪 Сила: «{c['strength']}»\n"
               f"🧠 Интеллект: «{c['intellect']}»")
        try:
            photo_file = FSInputFile(f"images/cards/{c['file']}")
            await msg.answer_photo(photo=photo_file, caption=txt)
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

# ================== ПЛАНИРОВЩИК УВЕДОМЛЕНИЙ О КУЛДАУНЕ ==================
async def cooldown_notification_scheduler(bot: Bot):
    """Фоновый task: проверяет истёкшие кулдауны и шлёт уведомления в ЛС."""
    while True:
        try:
            users = get_users_for_cooldown_notify(GET_COOLDOWN_HOURS * 3600)
            for (uid,) in users:
                try:
                    await bot.send_message(
                        uid,
                        "🎴 Крутка восстановлена! Ты можешь получить новую карту.\n"
                        "Используй кнопку «Получить карту» в главном меню."
                    )
                    mark_cooldown_notified(uid)
                except Exception:
                    pass  # Пользователь мог заблокировать бота
        except Exception as e:
            logging.error(f"Cooldown scheduler error: {e}")
        await asyncio.sleep(60)  # Проверка раз в минуту
