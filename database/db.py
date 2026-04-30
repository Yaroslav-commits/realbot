import os
import sqlite3
import random
from datetime import datetime, timedelta, timezone

from config import DB_PATH
from data.cards import CARDS, RARITIES, ROYALE_PASS

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
    db_exec("CREATE TABLE IF NOT EXISTS cards_inv (user_id INTEGER, card_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS decks (user_id INTEGER, card_id TEXT, slot_index INTEGER)")
    db_exec("CREATE TABLE IF NOT EXISTS bgs_inv (user_id INTEGER, bg_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS titles_inv (user_id INTEGER, title_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS pass_claims (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, p_type TEXT, val TEXT, uses INTEGER)")
    db_exec('''CREATE TABLE IF NOT EXISTS promo_uses (
        user_id INTEGER,
        promo_code TEXT,
        used_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, promo_code)
    )''')

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

def pull_random_card(force_rarity=None):
    """Возвращает ключ случайной карты или None, если пул пуст."""
    try:
        if force_rarity:
            pool = [k for k, v in CARDS.items() if v.get('rarity') == force_rarity and not v.get('exclusive')]
        else:
            roll = random.uniform(0, 100)
            cum = 0
            rolled_r = "Обычная ⚪️"
            for r, d in RARITIES.items():
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

