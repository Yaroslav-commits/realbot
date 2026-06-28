import os
import sqlite3
import random
import string
from datetime import datetime, timedelta, timezone

from config import DB_PATH
from data.cards import CARDS, RARITIES, PREMIUM_RARITIES, ROYALE_PASS, BGS, TITLES

# ================== ФУНКЦИИ БД ==================
def db_exec(query, params=(), fetch=False, fetchall=False):
    os.makedirs("data", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(query, params)
        if fetchall:
            return c.fetchall()
        if fetch:
            return c.fetchone()
        conn.commit()

def init_db():
    init_event_db()
    db_exec('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT, nickname TEXT,
        diamond INTEGER DEFAULT 0, krw INTEGER DEFAULT 0, battlecoin INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
        rank_points INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        last_get TEXT DEFAULT '2000-01-01 00:00:00', last_battle TEXT DEFAULT '2000-01-01 00:00:00',
        active_bg TEXT DEFAULT 'default', active_title TEXT, join_date TEXT, royale_pass INTEGER DEFAULT 0,
        notifications INTEGER DEFAULT 1, referral_code TEXT, referred_by INTEGER, cooldown_notified INTEGER DEFAULT 1,
        premium_until TEXT DEFAULT NULL, battle_cooldown_notified INTEGER DEFAULT 1,
        anonymous INTEGER DEFAULT 0, season_wins INTEGER DEFAULT 0
    )''')
    db_exec("CREATE TABLE IF NOT EXISTS cards_inv (user_id INTEGER, card_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS favorite_cards (user_id INTEGER, card_id TEXT, slot_index INTEGER)")
    db_exec("CREATE TABLE IF NOT EXISTS bgs_inv (user_id INTEGER, bg_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS decks (user_id INTEGER, card_id TEXT, slot_index INTEGER)")
    db_exec("CREATE TABLE IF NOT EXISTS titles_inv (user_id INTEGER, title_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS pass_claims (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, p_type TEXT, val TEXT, uses INTEGER)")
    db_exec('''CREATE TABLE IF NOT EXISTS battle_shop_packs (
        user_id INTEGER,
        week_number INTEGER,
        bought_count INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, week_number)
    )''')
    db_exec('''CREATE TABLE IF NOT EXISTS user_ranks_claims (
        user_id INTEGER,
        claim_date TEXT,
        PRIMARY KEY (user_id, claim_date)
    )''')
    db_exec('''CREATE TABLE IF NOT EXISTS promo_uses (
        user_id INTEGER,
        promo_code TEXT,
        used_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, promo_code)
    )''')
    db_exec('''CREATE TABLE IF NOT EXISTS referrals (
        referrer_id INTEGER,
        referred_id INTEGER PRIMARY KEY,
        rewarded INTEGER DEFAULT 0,
        reward_krw INTEGER DEFAULT 0,
        reward_attempts INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )''')
    db_exec('''CREATE TABLE IF NOT EXISTS card_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT,
            action TEXT,
            user_id INTEGER,
            target_id INTEGER,
            details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
    # ========== ВСТАВИТЬ БЛОК 1 СЮДА ==========
    db_exec('''CREATE TABLE IF NOT EXISTS craft_slots (
        user_id   INTEGER PRIMARY KEY,
        slot1     TEXT DEFAULT NULL,
        slot2     TEXT DEFAULT NULL,
        slot3     TEXT DEFAULT NULL,
        slot4     TEXT DEFAULT NULL,
        slot5     TEXT DEFAULT NULL
    )''')

    db_exec('''CREATE TABLE IF NOT EXISTS diamond_exchange_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER,
        diamonds  INTEGER,
        coins     INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    )''')
    db_exec('''CREATE TABLE IF NOT EXISTS bets_streak (
        user_id   INTEGER PRIMARY KEY,
        streak    INTEGER DEFAULT 0,
        bet       INTEGER DEFAULT 10
    )''')
    db_exec('''CREATE TABLE IF NOT EXISTS cards_stash (
        user_id  INTEGER,
        card_id  TEXT
    )''')

    # Миграция — добавить слоты, если таблицы уже были созданы ранее
    for col, col_def in [
        ('notifications', 'INTEGER DEFAULT 1'),
        ('referral_code', 'TEXT'),
        ('referred_by', 'INTEGER'),
        ('cooldown_notified', 'INTEGER DEFAULT 1'),
        ('premium_until', 'TEXT DEFAULT NULL'),
        ('battle_cooldown_notified', 'INTEGER DEFAULT 1'),
        ('anonymous', 'INTEGER DEFAULT 0'),
        ('season_wins', 'INTEGER DEFAULT 0'),
        ('max_streak', 'INTEGER DEFAULT 0'),  # <-- ДОБАВИЛИ СЮДА ТВОЙ СТРИК ИЗ МАКЕТА
    ]:
        try:
            db_exec(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass
    # ==========================================

    # Генерируем реферальные коды для существующих пользователей, у которых их нет
    users_no_code = db_exec("SELECT id FROM users WHERE referral_code IS NULL", fetchall=True)
    if users_no_code:
        for (uid,) in users_no_code:
            code = generate_unique_ref_code()
            db_exec("UPDATE users SET referral_code = ? WHERE id = ?", (code, uid))

    # Чистим старые дубли, удалённые фоны/титулы и ставим защиту от дублей
    cleanup_visual_inventory()

    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_bgs_inv_unique ON bgs_inv(user_id, bg_id)")
    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_titles_inv_unique ON titles_inv(user_id, title_id)")
    # skinchik
    db_exec("CREATE TABLE IF NOT EXISTS skins_inv (user_id INTEGER, card_id TEXT, skin_type TEXT, is_active INTEGER DEFAULT 0)")

def give_skin_to_user(uid: int, card_id: str, skin_type: str) -> bool:
    """Выдает скин. skin_type должен быть 'awakened' или 'absolute'. Возвращает True, если скина не было."""
    exists = db_exec("SELECT 1 FROM skins_inv WHERE user_id = ? AND card_id = ? AND skin_type = ?", (uid, card_id, skin_type), fetch=True)
    if exists:
        return False
    db_exec("INSERT INTO skins_inv (user_id, card_id, skin_type, is_active) VALUES (?, ?, ?, 0)", (uid, card_id, skin_type))
    return True

def get_user_skins_for_card(uid: int, card_id: str):
    """Возвращает список доступных скинов игрока для конкретной карты."""
    rows = db_exec("SELECT skin_type, is_active FROM skins_inv WHERE user_id = ? AND card_id = ?", (uid, card_id), fetchall=True)
    return rows if rows else []

def get_active_skin(uid: int, card_id: str):
    """Возвращает активный скин ('awakened' или 'absolute') или None."""
    res = db_exec("SELECT skin_type FROM skins_inv WHERE user_id = ? AND card_id = ? AND is_active = 1", (uid, card_id), fetch=True)
    return res[0] if res else None

def equip_skin(uid: int, card_id: str, skin_type: str):
    """Надевает скин (снимая остальные для этой карты)."""
    db_exec("UPDATE skins_inv SET is_active = 0 WHERE user_id = ? AND card_id = ?", (uid, card_id))
    db_exec("UPDATE skins_inv SET is_active = 1 WHERE user_id = ? AND card_id = ? AND skin_type = ?", (uid, card_id, skin_type))

def unequip_skin(uid: int, card_id: str):
    """Снимает любой активный скин с карты."""
    db_exec("UPDATE skins_inv SET is_active = 0 WHERE user_id = ? AND card_id = ?", (uid, card_id))

def get_user(uid):
    return db_exec("SELECT * FROM users WHERE id = ?", (uid,), fetch=True)

def add_user(uid, uname, fname, referred_by=None):
    if not get_user(uid):
        ref_code = generate_unique_ref_code()
        db_exec("INSERT INTO users (id, username, nickname, join_date, active_bg, referral_code, referred_by) VALUES (?, ?, ?, ?, 'default', ?, ?)",
                (uid, uname, fname, datetime.now().strftime("%Y-%m-%d"), ref_code, referred_by))
        db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, 'default'))

        # Обрабатываем реферала и возвращаем сумму награды
        if referred_by and referred_by != uid:
            return process_referral(referred_by, uid)
    return None

def log_card_action(card_id: str, action: str, user_id: int, target_id: int = None, details: str = None):
    """
    action: 'DROP', 'TRADE', 'CRAFT', 'ADMIN', 'STASH'
    """
    db_exec("INSERT INTO card_logs (card_id, action, user_id, target_id, details) VALUES (?, ?, ?, ?, ?)",
            (card_id, action, user_id, target_id, details))
# ================== ФОНЫ / ТИТУЛЫ / АНОНИМНОСТЬ ==================
def cleanup_visual_inventory():
    """
    Удаляет:
    - дубли фонов/титулов;
    - фоны/титулы, которые были удалены из data.cards;
    - сбрасывает активный удалённый фон/титул.
    """
    valid_bgs = list(BGS.keys())
    valid_titles = list(TITLES.keys())

    # Удаляем неизвестные фоны
    if valid_bgs:
        ph = ",".join(["?"] * len(valid_bgs))
        db_exec(f"DELETE FROM bgs_inv WHERE bg_id NOT IN ({ph})", tuple(valid_bgs))
        db_exec(f"""
            UPDATE users
            SET active_bg = 'default'
            WHERE active_bg IS NULL OR active_bg NOT IN ({ph})
        """, tuple(valid_bgs))
    # Удаляем неизвестные титулы
    if valid_titles:
        ph = ",".join(["?"] * len(valid_titles))
        db_exec(f"DELETE FROM titles_inv WHERE title_id NOT IN ({ph})", tuple(valid_titles))
        db_exec(f"""
            UPDATE users
            SET active_title = NULL
            WHERE active_title IS NOT NULL AND active_title NOT IN ({ph})
        """, tuple(valid_titles))

    # Удаляем дубли фонов
    db_exec("""
        DELETE FROM bgs_inv
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM bgs_inv
            GROUP BY user_id, bg_id
        )
    """)

    # Удаляем дубли титулов
    db_exec("""
        DELETE FROM titles_inv
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM titles_inv
            GROUP BY user_id, title_id
        )
    """)

def user_has_bg(uid: int, bg_id: str) -> bool:
    return bool(db_exec(
        "SELECT 1 FROM bgs_inv WHERE user_id = ? AND bg_id = ?",
        (uid, bg_id),
        fetch=True
    ))

def user_has_title(uid: int, title_id: str) -> bool:
    return bool(db_exec(
        "SELECT 1 FROM titles_inv WHERE user_id = ? AND title_id = ?",
        (uid, title_id),
        fetch=True
    ))

def give_bg_to_user(uid: int, bg_id: str) -> bool:
    """
    Выдаёт фон без дублей.
    Возвращает True, если фон реально добавлен.
    """
    if bg_id not in BGS:
        return False

    if user_has_bg(uid, bg_id):
        return False

    db_exec(
        "INSERT OR IGNORE INTO bgs_inv (user_id, bg_id) VALUES (?, ?)",
        (uid, bg_id)
    )
    return True

def give_title_to_user(uid: int, title_id: str) -> bool:
    """
    Выдаёт титул без дублей.
    Возвращает True, если титул реально добавлен.
    """
    if title_id not in TITLES:
        return False

    if user_has_title(uid, title_id):
        return False

    db_exec(
        "INSERT OR IGNORE INTO titles_inv (user_id, title_id) VALUES (?, ?)",
        (uid, title_id)
    )
    return True

def is_anonymous(uid: int) -> bool:
    res = db_exec("SELECT anonymous FROM users WHERE id = ?", (uid,), fetch=True)
    return bool(res[0]) if res else False

def toggle_anonymity(uid: int) -> bool:
    """
    Переключает режим инкогнито.
    True = включён.
    """
    current = db_exec("SELECT anonymous FROM users WHERE id = ?", (uid,), fetch=True)
    old_val = int(current[0]) if current and current[0] is not None else 0
    new_val = 0 if old_val else 1
    db_exec("UPDATE users SET anonymous = ? WHERE id = ?", (new_val, uid))
    return bool(new_val)

def get_notifications_enabled(uid: int) -> bool:
    res = db_exec("SELECT notifications FROM users WHERE id = ?", (uid,), fetch=True)
    return bool(res[0]) if res else True

# ================== РЕФЕРАЛЬНАЯ СИСТЕМА ==================
def generate_unique_ref_code():
    """Генерирует уникальный 12-символьный код только из букв."""
    import random
    import string
    # Убедимся, что используем только буквы, как ты просил
    chars = string.ascii_letters
    while True:
        code = ''.join(random.choices(chars, k=12))
        # Проверяем уникальность
        res = db_exec("SELECT 1 FROM users WHERE referral_code = ?", (code,), fetch=True)
        if not res:
            return code


def process_referral(referrer_id, referred_id):
    """Выдаёт награду ОБОИМ игрокам (300-550 KRW и 3 попытки)."""
    import random
    # Проверяем, не был ли игрок уже приглашен
    existing = db_exec("SELECT 1 FROM referrals WHERE referred_id = ?", (referred_id,), fetch=True)
    if existing:
        return None

    try:
        # Генерируем награду
        krw_reward = random.randint(300, 529)

        # Записываем связь (сразу с суммой награды — для показа в WebApp)
        db_exec(
            "INSERT INTO referrals (referrer_id, referred_id, rewarded, reward_krw, reward_attempts) VALUES (?, ?, 1, ?, ?)",
            (referrer_id, referred_id, krw_reward, 3)
        )

        # Начисляем награду НОВОМУ игроку
        db_exec("UPDATE users SET krw = krw + ?, attempts = attempts + 3 WHERE id = ?", (krw_reward, referred_id))

        # Начисляем награду ВЛАДЕЛЬЦУ ССЫЛКИ (исправлено)
        db_exec("UPDATE users SET krw = krw + ?, attempts = attempts + 3 WHERE id = ?", (krw_reward, referrer_id))

        return krw_reward
    except Exception as e:
        print(f"Ошибка в process_referral: {e}")
        return None


def get_referral_code_fixed(uid):
    """Надежный способ получить код пользователя."""
    res = db_exec("SELECT referral_code FROM users WHERE id = ?", (uid,), fetch=True)
    return res[0] if res and res[0] else None


def get_user_by_ref_code(code):
    """Находит пользователя по реферальному коду."""
    return db_exec("SELECT * FROM users WHERE referral_code = ?", (code,), fetch=True)

def get_referral_count(uid):
    """Возвращает количество приглашённых пользователем."""
    result = db_exec("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (uid,), fetch=True)
    return result[0] if result else 0



# ================== УВЕДОМЛЕНИЯ ==================
# ================== УВЕДОМЛЕНИЯ ==================
def get_users_for_cooldown_notify():
    """Возвращает (id, last_get, premium_until) всех кандидатов на уведомление о крутках."""
    return db_exec("""
        SELECT id, last_get, premium_until FROM users
        WHERE attempts = 0
        AND notifications = 1
        AND cooldown_notified = 0
    """, fetchall=True)

def get_users_for_battle_cooldown_notify():
    """Возвращает (id, last_battle, premium_until) — Premium-игроков с истёкшим кулдауном битвы."""
    return db_exec("""
        SELECT id, last_battle, premium_until FROM users
        WHERE notifications = 1
        AND battle_cooldown_notified = 0
        AND premium_until IS NOT NULL
    """, fetchall=True)

def mark_cooldown_notified(uid):
    """Отмечает, что уведомление о кулдауне крутки отправлено."""
    db_exec("UPDATE users SET cooldown_notified = 1 WHERE id = ?", (uid,))

def reset_cooldown_notified(uid):
    """Сбрасывает флаг уведомления о крутке (при старте нового кулдауна)."""
    db_exec("UPDATE users SET cooldown_notified = 0 WHERE id = ?", (uid,))

def mark_battle_cooldown_notified(uid):
    """Отмечает, что уведомление о кулдауне битвы отправлено."""
    db_exec("UPDATE users SET battle_cooldown_notified = 1 WHERE id = ?", (uid,))

def reset_battle_cooldown_notified(uid):
    """Сбрасывает флаг уведомления о битве (вызывается при старте боя)."""
    db_exec("UPDATE users SET battle_cooldown_notified = 0 WHERE id = ?", (uid,))

def toggle_notifications(uid):
    """Переключает уведомления и возвращает новое состояние True = включены."""
    current = db_exec("SELECT notifications FROM users WHERE id = ?", (uid,), fetch=True)
    old_val = int(current[0]) if current and current[0] is not None else 1
    new_val = 0 if old_val else 1
    db_exec("UPDATE users SET notifications = ? WHERE id = ?", (new_val, uid))
    return bool(new_val)

# ================== PREMIUM ==================
def is_premium(uid):
    """Проверяет, активна ли Premium подписка."""
    res = db_exec("SELECT premium_until FROM users WHERE id = ?", (uid,), fetch=True)
    if not res or not res[0]:
        return False
    try:
        until = datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S")
        return until > datetime.now()
    except Exception:
        return False

def get_premium_until(uid):
    """Возвращает datetime окончания Premium или None."""
    res = db_exec("SELECT premium_until FROM users WHERE id = ?", (uid,), fetch=True)
    if not res or not res[0]:
        return None
    try:
        return datetime.strptime(res[0], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
def add_premium_months(uid, months=1):
    """Продлевает Premium на N месяцев. Если уже активен — продлевает от даты окончания.
    Возвращает новую дату окончания."""
    current = get_premium_until(uid)
    base = current if (current and current > datetime.now()) else datetime.now()
    new_until = base + timedelta(days=30 * months)
    db_exec("UPDATE users SET premium_until = ? WHERE id = ?",
            (new_until.strftime("%Y-%m-%d %H:%M:%S"), uid))
    return new_until


# ================== ЛОГИКА ==================
def get_rank(pts):
    ranks = [
        (14000, "Бессмертный Архонт 🪽"),
        (10000, "Монарх Пустоты 🌑"),
        (6500, "Владыка Хаоса 🌋"),
        (4500, "Абсолют ♾️"),
        (3000, "Безупречная мощь 😈"),
        (2000, "Легенда 🐉"),
        (1600, "Титан 🧬"),
        (1000, "Уровень Короля 👑"),
        (600, "Неоспоримый 👾"),
        (300, "Пробуждённый 🪬"),
        (100, "Боец 🦸‍♂️"),
        (0, "Новичок 💩"),
    ]
    for p, n in ranks:
        if pts >= p:
            return n

# ===== ВЕСА ВЫПАДЕНИЯ КАРТ ВНУТРИ РЕДКОСТИ =====
# Чем сильнее карта, тем реже падает. Стат 100/99/90 — топ, режутся жёстче.
def _card_pull_weight(card: dict) -> float:
    """Вес карты для random.choices. Чем меньше — тем реже падает."""
    # Божественные карты не подвержены штрафам за статы
    if "Божественная" in card.get("rarity", ""):
        return 1.0

    spd = card.get('speed', 0)
    strg = card.get('strength', 0)
    intl = card.get('intellect', 0)
    stats = (spd, strg, intl)
    total = spd + strg + intl

    weight = 1.0

    if any(s == 100 for s in stats):
        weight *= 0.04
    elif any(s == 99 for s in stats):
        weight *= 0.10
    elif any(s == 90 for s in stats):
        weight *= 0.20

    if total >= 285:
        weight *= 0.40
    elif total >= 270:
        weight *= 0.70

    if weight < 0.02:
        weight = 0.02
    return weight

def pull_random_card(uid=None, force_rarity=None, premium=False):
    """Возвращает ключ случайной карты или None, если пул пуст.
    Если передан uid — проверяет наличие персональных шансов.
    Если premium=True — используется PREMIUM_RARITIES.
    Внутри редкости карты с 100/99/90 в статах падают реже (взвешенный выбор)."""
    try:
        if force_rarity:
            pool = [(k, v) for k, v in CARDS.items()
                    if v.get('rarity') == force_rarity and not v.get('exclusive')]
        else:
            rarities_dict = None

            # Если передали ID игрока, проверяем его персональные шансы
            if uid:
                try:
                    custom = db_exec(
                        "SELECT common, rare, epic, leg, myth, div FROM custom_user_rarities WHERE user_id = ?", (uid,),
                        fetch=True)
                    if custom:
                        rarities_dict = {
                            "Обычная ⚪️": {"chance": custom[0]},
                            "Редкая 🟡": {"chance": custom[1]},
                            "Эпическая 🟢": {"chance": custom[2]},
                            "Легендарная 🔵": {"chance": custom[3]},
                            "Мифическая 🔴": {"chance": custom[4]},
                            "Божественная ⚫️": {"chance": custom[5]}
                        }
                except Exception:
                    pass  # Если таблицы нет или ошибка — идём дальше по стандарту

            # Если кастомных шансов нет, берем стандартные
            if not rarities_dict:
                rarities_dict = PREMIUM_RARITIES if premium else RARITIES

            total = sum(d.get('chance', 0) for d in rarities_dict.values())
            roll = random.uniform(0, total)
            cum = 0
            rolled_r = next(iter(rarities_dict.keys()))
            for r, d in rarities_dict.items():
                cum += d.get('chance', 0)
                if roll <= cum:
                    rolled_r = r
                    break
            pool = [(k, v) for k, v in CARDS.items()
                    if v.get('rarity') == rolled_r and not v.get('exclusive')]
            if not pool:
                pool = [(k, v) for k, v in CARDS.items() if not v.get('exclusive')]

        if not pool:
            return None

        keys = [k for k, _ in pool]
        weights = [_card_pull_weight(v) for _, v in pool]
        return random.choices(keys, weights=weights, k=1)[0]
    except Exception as e:
        print(f"Ошибка в pull_random_card: {e}")
        return None

# ===== СУНДУК (ОТЛОЖЕННЫЕ КАРТЫ) =====
def stash_card(uid: int, card_key: str) -> bool:
    """Перекладывает 1 экземпляр карты из инвентаря в сундук.
    Возвращает True, если успешно."""
    row = db_exec(
        "SELECT rowid FROM cards_inv WHERE user_id = ? AND card_id = ? LIMIT 1",
        (uid, card_key), fetch=True
    )
    if not row:
        return False
    db_exec("DELETE FROM cards_inv WHERE rowid = ?", (row[0],))
    db_exec("INSERT INTO cards_stash (user_id, card_id) VALUES (?, ?)", (uid, card_key))
    return True

def unstash_card(uid: int, card_key: str) -> bool:
    """Возвращает 1 экземпляр карты из сундука обратно в инвентарь."""
    row = db_exec(
        "SELECT rowid FROM cards_stash WHERE user_id = ? AND card_id = ? LIMIT 1",
        (uid, card_key), fetch=True
    )
    if not row:
        return False
    db_exec("DELETE FROM cards_stash WHERE rowid = ?", (row[0],))
    db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (uid, card_key))
    return True

def get_stash(uid: int):
    """Возвращает список card_id из сундука пользователя."""
    rows = db_exec("SELECT card_id FROM cards_stash WHERE user_id = ?", (uid,), fetchall=True)
    return [r[0] for r in rows] if rows else []



def give_card_to_user(uid, card_key):
    """Выдаёт карту игроку. Возвращает (is_new, krw, card_data) или (False, 0, None) при ошибке."""
    try:
        c = CARDS.get(card_key)
        if not c:
            return False, 0, None

        has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (uid, card_key), fetch=True)

        if has_card:
            rarity_data = RARITIES.get(c.get('rarity'))
            if not rarity_data:
                return False, 0, None
            dup_val = rarity_data.get('dup', (1, 10))
            krw_earned = random.randint(dup_val[0], dup_val[1]) if isinstance(dup_val, tuple) else dup_val
            db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (krw_earned, uid))
            return False, krw_earned, c
        else:
            db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (uid, card_key))
            return True, 0, c
    except Exception:
        return False, 0, None

def try_use_promo(uid, code):
    """Пытается записать использование промокода пользователем.
    Возвращает True, если промокод ещё не был использован этим пользователем.
    Возвращает False, если пользователь уже активировал этот промокод."""
    try:
        db_exec("INSERT INTO promo_uses (user_id, promo_code) VALUES (?, ?)", (uid, code))
        return True
    except sqlite3.IntegrityError:
        return False


def grant_retroactive_royale_pass(uid):
    """
    Выдает Рояль Пасс на текущий месяц (формат YYYYMM).
    Начисляет награды за те дни, которые уже пройдены в обычном пассе.
    Возвращает строку с описанием выданных наград (или пустую строку).
    """
    now = datetime.now(timezone(timedelta(hours=3)))
    current_ym = int(now.strftime("%Y%m"))

    db_exec("UPDATE users SET royale_pass = ? WHERE id = ?", (current_ym, uid))

    claims = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = 'normal'", 
                     (uid, now.month), fetchall=True)
    claimed_normal = [d[0] for d in claims] if claims else []

    claims_rp = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = 'royale'", 
                        (uid, now.month), fetchall=True)
    claimed_rp = [d[0] for d in claims_rp] if claims_rp else []

    days_to_grant = [d for d in claimed_normal if d not in claimed_rp]

    if not days_to_grant:
        return ""

    rewards_summary = {'krw': 0, 'atm': 0, 'bc': 0, 'dia': 0, 'packs': 0}
    for d in days_to_grant:
        r_type, r_val = ROYALE_PASS.get(d, ('krw', 10))
        if r_type == 'krw':
            db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (r_val, uid))
            rewards_summary['krw'] += r_val
        elif r_type == 'atm':
            db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (r_val, uid))
            rewards_summary['atm'] += r_val
        elif r_type == 'bc':
            db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (r_val, uid))
            rewards_summary['bc'] += r_val
        elif r_type == 'dia':
            db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (r_val, uid))
            rewards_summary['dia'] += r_val
        elif r_type == 'pack':
            card_key = pull_random_card(force_rarity="Легендарная 🔵" if r_val == "leg" else "Эпическая 🟢")
            if not card_key: card_key = pull_random_card()
            give_card_to_user(uid, card_key)
            rewards_summary['packs'] += 1

        db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, 'royale')", 
                (uid, now.month, d))

    lines = []
    if rewards_summary['krw']: lines.append(f"• {rewards_summary['krw']} 💴")
    if rewards_summary['atm']: lines.append(f"• {rewards_summary['atm']} 💳")
    if rewards_summary['bc']: lines.append(f"• {rewards_summary['bc']} 🪙")
    if rewards_summary['dia']: lines.append(f"• {rewards_summary['dia']} 💎")
    if rewards_summary['packs']: lines.append(f"• {rewards_summary['packs']} 🗃️ Паков (карты добавлены в инвентарь)")

    return "\n\n🎁 Автоматически начислены награды за " + str(len(days_to_grant)) + " дн. (из обычного пасса):\n" + "\n".join(lines)

# ================== ИВЕНТ (ЛЕГКО УДАЛИТЬ) ==================
def init_event_db():
    db_exec('''CREATE TABLE IF NOT EXISTS event_items (
        user_id INTEGER PRIMARY KEY,
        cocktail INTEGER DEFAULT 0,
        icecream INTEGER DEFAULT 0,
        dango INTEGER DEFAULT 0
    )''')

def get_event_items(uid):
    res = db_exec("SELECT cocktail, icecream, dango FROM event_items WHERE user_id = ?", (uid,), fetch=True)
    if not res:
        db_exec("INSERT OR IGNORE INTO event_items (user_id) VALUES (?)", (uid,))
        return 0, 0, 0
    return res

def add_event_item(uid, item_type, amount):
    get_event_items(uid)
    db_exec(f"UPDATE event_items SET {item_type} = {item_type} + ? WHERE user_id = ?", (amount, uid))
# ================== ЛЮБИМЫЕ КАРТЫ И ТИТУЛЫ ПРОФИЛЯ ==================

def get_favorite_cards(uid: int):
    """Возвращает список любимых карт игрока в виде словаря {slot_index: card_id}"""
    rows = db_exec("SELECT card_id, slot_index FROM favorite_cards WHERE user_id = ?", (uid,), fetchall=True)
    return {row[1]: row[0] for row in rows} if rows else {}

def set_favorite_card(uid: int, card_id: str, slot_index: int):
    """Устанавливает или обновляет любимую карту в конкретном слоте (0, 1, 2)"""
    db_exec("DELETE FROM favorite_cards WHERE user_id = ? AND slot_index = ?", (uid, slot_index))
    if card_id:
        db_exec("INSERT INTO favorite_cards (user_id, card_id, slot_index) VALUES (?, ?, ?)", (uid, card_id, slot_index))

def get_user_unlocked_titles(uid: int):
    """Возвращает список ID титулов, которые есть у игрока в инвентаре"""
    rows = db_exec("SELECT title_id FROM titles_inv WHERE user_id = ?", (uid,), fetchall=True)
    return [row[0] for row in rows] if rows else []

def set_user_active_title(uid: int, title_id: str) -> bool:
    """Устанавливает активный титул, если он у игрока разблокирован (или снимает, если title_id=None)"""
    if title_id:
        # Проверяем, есть ли такой титул вообще в инвентаре
        if not user_has_title(uid, title_id):
            return False
        db_exec("UPDATE users SET active_title = ? WHERE id = ?", (title_id, uid))
    else:
        db_exec("UPDATE users SET active_title = NULL WHERE id = ?", (uid,))
    return True