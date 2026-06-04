# ==========================================
# ИЗМЕНЕНИЕ 1 — Добавляем is_active в таблицу
# Строки 289-299 ЗАМЕНИТЬ на:
# ==========================================

def ensure_multi_deck_tables():
    db_exec('''CREATE TABLE IF NOT EXISTS multi_decks (
        deck_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT,
        is_active INTEGER DEFAULT 0
    )''')
    # Добавляем колонку is_active если её нет (для старых БД)
    try:
        db_exec("ALTER TABLE multi_decks ADD COLUMN is_active INTEGER DEFAULT 0")
    except Exception:
        pass  # колонка уже есть — ок
    db_exec('''CREATE TABLE IF NOT EXISTS multi_deck_slots (
        deck_id INTEGER,
        slot_index INTEGER,
        card_id TEXT
    )''')

# ==========================================
# ИЗМЕНЕНИЕ 2 — show_multi_deck_main с кнопкой выбора
# Строки 310-336 ЗАМЕНИТЬ на:
# ==========================================

async def show_multi_deck_main(message, user_id):
    ensure_multi_deck_tables()
    decks = db_exec("SELECT deck_id, name, is_active FROM multi_decks WHERE user_id = ?", (user_id,), fetchall=True)

    bld = InlineKeyboardBuilder()
    if len(decks) == 0:
        bld.button(text="Добавить колоду 🆕", callback_data="mdeck_add")
        bld.button(text="Назад 🔙", callback_data="view_deck")
        bld.adjust(1)
    else:
        for d in decks:
            did, dname, is_active = d
            active_mark = " ✅" if is_active else ""
            bld.row(
                InlineKeyboardButton(text=f"{dname}{active_mark}", callback_data=f"mdeck_view:{did}"),
                InlineKeyboardButton(text="✅ Выбрать", callback_data=f"mdeck_select:{did}")
            )
        if len(decks) < 2:
            bld.row(InlineKeyboardButton(text="Добавить колоду 🆕", callback_data="mdeck_add"))
        bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="view_deck"))

    text = (
        "Здесь место для вашых колод 🎴\n\n"
        "Можно иметь лишь две колоды. Выберите колоду и нажмите «✅ Выбрать» "
        "чтобы сделать её активной для боёв.\n\n"
        "Нажмите на название колоды, чтобы редактировать её."
    )
    if isinstance(message, types.Message):
        await message.answer(text, reply_markup=bld.as_markup())
    else:
        await message.edit_text(text, reply_markup=bld.as_markup())

# ==========================================
# ИЗМЕНЕНИЕ 3 — Новый callback выбора колоды
# ВСТАВИТЬ ПОСЛЕ строки 340 (после manual_deck_start):
# ==========================================

@router.callback_query(F.data.startswith("mdeck_select:"))
async def mdeck_select_cb(cq: CallbackQuery):
    deck_id = int(cq.data.split(":")[1])
    uid = cq.from_user.id

    deck = db_exec("SELECT deck_id FROM multi_decks WHERE deck_id = ? AND user_id = ?", (deck_id, uid), fetch=True)
    if not deck:
        return await cq.answer("Колода не найдена!", show_alert=True)

    # Сбрасываем активность у всех колод пользователя
    db_exec("UPDATE multi_decks SET is_active = 0 WHERE user_id = ?", (uid,))
    # Устанавливаем выбранную
    db_exec("UPDATE multi_decks SET is_active = 1 WHERE deck_id = ?", (deck_id,))
    # Синхронизируем с таблицей decks для боёв
    sync_active_deck(uid, deck_id)

    await cq.answer("✅ Колода выбрана как активная!", show_alert=True)
    await show_multi_deck_main(cq.message, uid)

# ==========================================
# ИЗМЕНЕНИЕ 4 — Фикс отмены при создании колоды
# Строка 351: поменять callback_data у кнопки "Отменить"
#   было: callback_data="manual_deck_start"
#   стало: callback_data="mdeck_cancel_add"
#
# И ВСТАВИТЬ новый обработчик ПОСЛЕ manual_deck_start
# ==========================================

@router.callback_query(F.data == "mdeck_cancel_add")
async def mdeck_cancel_add_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_multi_deck_main(cq.message, cq.from_user.id)
    await cq.answer()

# ==========================================
# ИЗМЕНЕНИЕ 5 — Проверка дубликатов при заборе из сундука
# Строка 2895 (stash_do_take_cb) — ВЕСЬ обработчик ЗАМЕНИТЬ на:
# ==========================================

@router.callback_query(F.data.startswith("stash_do_take:"))
async def stash_do_take_cb(cq: CallbackQuery):
    _, cid, page = cq.data.split(":")
    uid = cq.from_user.id

    # ✅ ПРОВЕРКА: если карта уже есть в инвентаре — не даём забрать
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
    cq.data = f"stash_take:{page}"
    await stash_take_cb(cq)

# ==========================================
# ИЗМЕНЕНИЕ 6 — Сортировка от сильных к слабым
# В mdeck_rarity_cb, ПОСЛЕ строки 560 (if not avail: ...)
# ВСТАВИТЬ сортировку:
# ==========================================

    # --- ВСТАВИТЬ ПОСЛЕ "if not avail: return ..." ---
    # Сортируем от сильнейшего к слабейшему (по сумме статов)
    avail.sort(key=lambda cid: (
        CARDS[cid]['speed'] + CARDS[cid]['strength'] + CARDS[cid]['intellect']
    ), reverse=True)
    # --- ДАЛЬШЕ КОД ИДЁТ КАК БЫЛ (items_per_page = 10 и т.д.) ---