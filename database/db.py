import os
import sqlite3
import random
from datetime import datetime, timedelta

from config import DB_PATH
from data.cards import CARDS, RARITIES

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
    db_exec('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT, nickname TEXT,
        diamond INTEGER DEFAULT 0, krw INTEGER DEFAULT 0, battlecoin INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
        rank_points INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        last_get TEXT DEFAULT '2000-01-01 00:00:00', last_battle TEXT DEFAULT '2000-01-01 00:00:00',
        active_bg TEXT DEFAULT 'default', active_title TEXT, join_date TEXT, royale_pass INTEGER DEFAULT 0
    )''')
    # Добавляем колонку для Premium автоматически (если её нет)
    try:
        db_exec("ALTER TABLE users ADD COLUMN premium_until TEXT DEFAULT '2000-01-01 00:00:00'")
    except sqlite3.OperationalError:
        pass

    db_exec("CREATE TABLE IF NOT EXISTS cards_inv (user_id INTEGER, card_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS decks (user_id INTEGER, card_id TEXT, slot_index INTEGER)")
    db_exec("CREATE TABLE IF NOT EXISTS bgs_inv (user_id INTEGER, bg_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS titles_inv (user_id INTEGER, title_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS pass_claims (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, p_type TEXT, val TEXT, uses INTEGER)")

def pull_random_card(force_rarity=None, uid=None):
    """Возвращает ключ случайной карты или None, если пул пуст."""
    try:
        from data.cards import PREMIUM_RARITIES, RARITIES
        rates = RARITIES
        # Если передан ID игрока, проверяем, есть ли у него Премиум
        if uid:
            u = get_user(uid)
            if u and len(u) > 17 and u[17] and u[17] > datetime.now().strftime("%Y-%m-%d %H:%M:%S"):
                rates = PREMIUM_RARITIES

        if force_rarity:
            pool = [k for k, v in CARDS.items() if v.get('rarity') == force_rarity and not v.get('exclusive')]
        else:
            roll = random.uniform(0, 100)
            cum = 0
            rolled_r = "Обычная ⚪️"
            for r, d in rates.items():
                cum += d.get('chance', 0)
                if roll <= cum:
                    rolled_r = r
                    break
            pool = [k for k, v in CARDS.items() if v.get('rarity') == rolled_r and not v.get('exclusive')]
            if not pool:
                pool = [k for k, v in CARDS.items() if not v.get('exclusive')]
        return random.choice(pool) if pool else None
    except Exception:
        return None


def get_user(uid):
    return db_exec("SELECT * FROM users WHERE id = ?", (uid,), fetch=True)

def add_user(uid, uname, fname):
    if not get_user(uid):
        db_exec("INSERT INTO users (id, username, nickname, join_date, active_bg) VALUES (?, ?, ?, ?, 'default')",
                (uid, uname, fname, datetime.now().strftime("%Y-%m-%d")))
        db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, 'default'))

# ================== ЛОГИКА ==================
def get_rank(pts):
    ranks = [(3000, "Безупречная мощь 😈"), (2000, "Сильнейший ☄️"), (1600, "Уровень нулевого 🧬"), (1000, "Уровень короля города 👑"),
             (600, "Уровень 1-го поколения 👾"), (300, "Уровень 2-го поколения 🪬"), (100, "Боец 🦸‍♂️"), (0, "Новичок 💩")]
    for p, n in ranks:
        if pts >= p:
            return n


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
