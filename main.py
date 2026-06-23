import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from aiogram.types import (
    WebAppInfo, MenuButtonWebApp,
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from pydantic import BaseModel

from config import BOT_TOKEN, DB_PATH
from database.db import init_db, is_premium, pull_random_card, give_card_to_user
from data.cards import TITLES
from handlers import router

# Импорты хендлеров
from handlers import user as _user  # noqa: F401
from handlers import deck as _deck  # noqa: F401
from handlers import battle as _battle  # noqa: F401
from handlers.pass_shop import shop as _shop  # noqa: F401
from handlers.user import cooldown_notification_scheduler, battle_cooldown_notification_scheduler
from handlers.battle import auto_top_distributor


# ============================================================
#  НАСТРОЙКИ РАЗДЕЛА «ЗАРАБОТОК»  (меняй значения тут)
# ============================================================
MSK = timezone(timedelta(hours=3))

# Публичный @username канала (нужен для проверки подписки и буста).
# Бот ОБЯЗАТЕЛЬНО должен быть администратором этого канала!
CHANNEL_USERNAME = "@manhwcard"
CHANNEL_LINK = "https://t.me/manhwcard"
BOOST_LINK = "https://t.me/boost/manhwcard"
TIKTOK_HASHTAG_LINK = "https://vt.tiktok.com/ZS92ocVcSbVA5-QEi0R/"

# Куда приходят заявки на проверку TikTok-видео и Сторис (твой Telegram ID
# или ID группы модерации). Узнать свой ID: напиши @userinfobot.
MODERATION_CHAT_ID = 0  # <-- ОБЯЗАТЕЛЬНО ЗАМЕНИ НА СВОЙ ID

# Кто имеет право жать «Одобрить / Отклонить» под заявкой.
# Если заявки летят в группу — впиши сюда РЕАЛЬНЫЕ user_id админов.
ADMIN_IDS = {MODERATION_CHAT_ID}

# Награды за задания (₩ = krw, 💎 = diamond).
REWARDS = {
    "subscribe": {"krw": 1000, "dia": 5},    # подписка на канал (Партнёры)
    "boost":     {"krw": 2000, "dia": 10},   # буст канала (раз в 7 дней)
    "tiktok":    {"krw": 5000, "dia": 10},   # TikTok-видео (после модерации)
    "story":     {"krw": 3000, "dia": 5},    # Сторис (после модерации)
}
BOOST_COOLDOWN_DAYS = 7

# Глобальные ссылки на бота и его username (заполняются при старте).
BOT_INSTANCE: Bot | None = None
BOT_USERNAME: str = ""

# Отдельный роутер для модерации соцзаданий (подключается в start_bot).
mod_router = Router()


# ============================================================
#  БД-ХЕЛПЕР (с гарантированным закрытием соединения!)
# ============================================================
def db_exec_sync(query, params=(), fetch=False, fetchall=False):
    # ВАЖНО: `with sqlite3.connect(...)` коммитит транзакцию, но НЕ закрывает
    # соединение. В старой версии соединения утекали — со временем это
    # приводит к "database is locked" и зависаниям. Теперь закрываем явно.
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        c = conn.cursor()
        c.execute(query, params)
        if fetchall:
            return c.fetchall()
        if fetch:
            return c.fetchone()
        conn.commit()
    finally:
        conn.close()


def migrate_daily():
    try:
        db_exec_sync("ALTER TABLE users ADD COLUMN daily_day INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        db_exec_sync("ALTER TABLE users ADD COLUMN last_daily_claim TEXT DEFAULT '2000-01-01'")
    except Exception:
        pass


# ============================================================
#  МИГРАЦИИ ДЛЯ «ЗАРАБОТКА»
# ============================================================
def migrate_earn():
    # Выполненные одноразовые задания (например, подписка на канал)
    db_exec_sync("""CREATE TABLE IF NOT EXISTS task_claims (
        user_id INTEGER, task_key TEXT, claimed_at TEXT,
        PRIMARY KEY (user_id, task_key))""")
    # Последний клейм награды за буст (для кулдауна 7 дней)
    db_exec_sync("""CREATE TABLE IF NOT EXISTS boost_claims (
        user_id INTEGER PRIMARY KEY, last_claim TEXT)""")
    # Заявки на проверку TikTok-видео и Сторис
    db_exec_sync("""CREATE TABLE IF NOT EXISTS social_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, task_type TEXT, link TEXT, note TEXT,
        status TEXT DEFAULT 'pending', created_at TEXT)""")
    # Храним сумму выданной за реферала награды, чтобы показывать «заработано»
    for col, col_def in [
        ("reward_krw", "INTEGER DEFAULT 0"),
        ("reward_attempts", "INTEGER DEFAULT 0"),
    ]:
        try:
            db_exec_sync(f"ALTER TABLE referrals ADD COLUMN {col} {col_def}")
        except Exception:
            pass


def _now_str():
    return datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S")


def _secs_left(next_str):
    """Сколько секунд осталось до даты next_str (формат MSK). 0 если уже прошло."""
    if not next_str:
        return 0
    try:
        nxt = datetime.strptime(next_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
        d = (nxt - datetime.now(MSK)).total_seconds()
        return int(d) if d > 0 else 0
    except Exception:
        return 0


def _credit(user_id, krw=0, dia=0, attempts=0):
    """Начисление валюты игроку."""
    if krw:
        db_exec_sync("UPDATE users SET krw = krw + ? WHERE id = ?", (krw, user_id))
    if dia:
        db_exec_sync("UPDATE users SET diamond = diamond + ? WHERE id = ?", (dia, user_id))
    if attempts:
        db_exec_sync("UPDATE users SET attempts = attempts + ? WHERE id = ?", (attempts, user_id))


async def _is_subscribed(user_id) -> bool:
    """Проверка подписки на канал через Bot API (бот должен быть админом канала)."""
    if BOT_INSTANCE is None:
        return False
    try:
        m = await BOT_INSTANCE.get_chat_member(CHANNEL_USERNAME, user_id)
        return str(m.status) in ("member", "administrator", "creator", "ChatMemberStatus.MEMBER",
                                  "ChatMemberStatus.ADMINISTRATOR", "ChatMemberStatus.CREATOR")
    except Exception as e:
        logging.error(f"is_subscribed error: {e}")
        return False


async def _is_boosting(user_id) -> bool:
    """Проверка активного буста канала через Bot API (бот должен быть админом канала)."""
    if BOT_INSTANCE is None:
        return False
    try:
        res = await BOT_INSTANCE.get_user_chat_boosts(CHANNEL_USERNAME, user_id)
        return bool(res and res.boosts and len(res.boosts) > 0)
    except Exception as e:
        logging.error(f"is_boosting error: {e}")
        return False


def _insert_submission(user_id, task_type, link, note, created_at) -> int:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        c = conn.cursor()
        c.execute(
            "INSERT INTO social_submissions (user_id, task_type, link, note, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (user_id, task_type, link, note, created_at)
        )
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


# ============================================================
#  ПЛАНИРОВЩИК: Уведомления об окончании Premium
# ============================================================
async def premium_expiration_scheduler(bot: Bot):
    """Фоновый task: уведомляет об окончании Premium-подписки."""
    while True:
        try:
            now = datetime.now()
            # Берем всех юзеров, у которых установлена дата према
            users = db_exec_sync("SELECT id, premium_until FROM users WHERE premium_until IS NOT NULL", fetchall=True)

            if users:
                for uid, until_str in users:
                    try:
                        until_dt = datetime.strptime(until_str, "%Y-%m-%d %H:%M:%S")
                        if until_dt < now:
                            # Срок действия истек!
                            try:
                                await bot.send_message(
                                    uid,
                                    "🥀 <b>Срок действия Premium-подписки истёк...</b>\n\n"
                                    "Премиум-бонусы больше недоступны. "
                                    "Но ты всегда можешь вернуть свой статус 👑 в Магазине!",
                                    parse_mode="HTML"
                                )
                            except Exception:
                                pass # Игрок мог заблокировать бота

                            # Обнуляем дату, чтобы уведа пришла только один раз
                            db_exec_sync("UPDATE users SET premium_until = NULL WHERE id = ?", (uid,))
                    except Exception:
                        # Если дата кривая (ошибка парсинга), тоже сбрасываем
                        db_exec_sync("UPDATE users SET premium_until = NULL WHERE id = ?", (uid,))

        except Exception as e:
            logging.error(f"Premium notification scheduler error: {e}")

        # Проверяем каждые 5 минут
        await asyncio.sleep(300)


# ============================================================
#  ЗАЩИТА: проверка подписи Telegram WebApp (initData)
# ============================================================
# Telegram подписывает initData ключом, производным от токена бота.
# Подделать user_id без токена невозможно. Поэтому мы НЕ доверяем id
# из URL, а берём его только из проверенной подписи.
def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400):
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data))
    except Exception:
        return None

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    # constant-time сравнение, чтобы не утекало время
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    # Свежесть данных (по желанию): отбрасываем слишком старые initData
    auth_date = parsed.get("auth_date")
    if auth_date:
        try:
            age = datetime.now(timezone.utc).timestamp() - int(auth_date)
            if age > max_age_seconds:
                return None
        except ValueError:
            pass

    user_raw = parsed.get("user")
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
        return int(user["id"])
    except (ValueError, KeyError, TypeError):
        return None


# FastAPI-зависимость: достаём проверенный user_id из заголовка
# и сверяем с тем, что пришёл в URL. Несовпадение -> 403.
def authed_user_id(user_id: int, x_telegram_init_data: str = Header(default="")) -> int:
    verified = verify_telegram_init_data(x_telegram_init_data, BOT_TOKEN)
    if verified is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация Telegram")
    if verified != user_id:
        raise HTTPException(status_code=403, detail="Чужой профиль")
    return verified


# ============================================================
#  ЗАПУСК БОТА
# ============================================================
async def start_bot():
    global BOT_INSTANCE, BOT_USERNAME
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_INSTANCE = bot
    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(mod_router)  # <-- модерация TikTok/Сторис

    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username or ""
    except Exception:
        BOT_USERNAME = ""

    WEBAPP_URL = "https://yaroslav-commits.github.io/cards-catalog-manhw/"

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="🃏 Каталог", web_app=WebAppInfo(url=WEBAPP_URL))
    )

    await bot.delete_webhook(drop_pending_updates=True)

    # Запускаем все наши фоновые задачи
    asyncio.create_task(cooldown_notification_scheduler(bot))
    asyncio.create_task(battle_cooldown_notification_scheduler(bot))
    asyncio.create_task(auto_top_distributor(bot))
    asyncio.create_task(premium_expiration_scheduler(bot))  # <-- Добавлен планировщик Premium!

    print("Ждём 3 секунды для отключения старых процессов...")
    await asyncio.sleep(3)  # <-- Даем старому боту спокойно умереть

    print("Бот успешно запущен в фоновом режиме!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logging.error(f"Ошибка при поллинге (возможно, конфликт): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    migrate_daily()
    migrate_earn()
    # WAL заметно снижает конфликты блокировок при одновременном чтении/записи
    try:
        db_exec_sync("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    bot_task = asyncio.create_task(start_bot())
    yield
    bot_task.cancel()


app = FastAPI(lifespan=lifespan)

# allow_credentials=False — мы используем не куки, а подписанный заголовок,
# поэтому "*" в origins абсолютно валиден (с credentials=True "*" запрещён
# спецификацией CORS и браузер бы резал запросы).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def healthcheck():
    return {"status": "ok"}


DAILY_REWARDS = {
    1: {'krw': 200}, 2: {'krw': 300}, 3: {'krw': 350}, 4: {'krw': 350},
    5: {'krw': 400}, 6: {'krw': 400}, 7: {'pack': 'leg'}, 8: {'krw': 450},
    9: {'krw': 450}, 10: {'krw': 500, 'dia': 10}, 11: {'krw': 500},
    12: {'krw': 500}, 13: {'krw': 550}, 14: {'pack': 'leg'},
    15: {'krw': 600}, 16: {'krw': 600}, 17: {'krw': 650},
    18: {'krw': 650}, 19: {'krw': 700}, 20: {'krw': 700, 'dia': 10},
    21: {'pack': 'leg'}, 22: {'krw': 750}, 23: {'krw': 750},
    24: {'krw': 800}, 25: {'krw': 850}, 26: {'krw': 900},
    27: {'krw': 950}, 28: {'pack': 'leg'}, 29: {'krw': 1000},
    30: {'pack': 'mythic'}
}


# Синхронные эндпоинты (без async) FastAPI выполняет в отдельном потоке —
# это защищает event loop от блокировок при работе с sqlite. Так и оставляем.
@app.get("/api/profile/{user_id}")
def get_profile(user_id: int = Depends(authed_user_id)):
    try:
        # Достаём новые поля: победы, поражения, стрик и активный титул
        user = db_exec_sync(
            "SELECT diamond, krw, battlecoin, wins, losses, max_streak, active_title, active_bg FROM users WHERE id = ?",
            (user_id,), fetch=True
        )
        if not user:
            return {"diamond": 0, "krw": 0, "battlecoin": 0, "is_premium": False,
                    "owned_cards": [], "daily_day": 0, "can_claim_daily": False,
                    "wins": 0, "losses": 0, "winrate": 0, "max_streak": 0,
                    "active_title": None, "fav_cards": {}, "unlocked_titles": []}

        # Миграция ежедневных наград (оставляем как было)
        daily_day = 0
        last_claim_date = '2000-01-01'
        try:
            daily_info = db_exec_sync(
                "SELECT daily_day, last_daily_claim FROM users WHERE id = ?",
                (user_id,), fetch=True
            )
            if daily_info:
                daily_day = daily_info[0] or 0
                last_claim_date = daily_info[1] or '2000-01-01'
        except Exception:
            migrate_daily()

        is_prem = is_premium(user_id)
        cards_rows = db_exec_sync(
            "SELECT card_id FROM cards_inv WHERE user_id = ?", (user_id,), fetchall=True
        )
        owned_cards = [row[0] for row in cards_rows] if cards_rows else []

        now_msk = datetime.now(timezone(timedelta(hours=3)))
        today_str = now_msk.strftime("%Y-%m-%d")
        can_claim_daily = (last_claim_date.split(" ")[0] != today_str)

        # Подсчёт статистики боёв
        wins = user[3] or 0
        losses = user[4] or 0
        max_streak = user[5] or 0
        active_title = user[6]

        total_battles = wins + losses
        winrate = int((wins / total_battles) * 100) if total_battles > 0 else 0

        # Любимые карты
        fav_rows = db_exec_sync("SELECT slot_index, card_id FROM favorite_cards WHERE user_id = ?", (user_id,),
                                fetchall=True)
        fav_cards = {str(row[0]): row[1] for row in fav_rows} if fav_rows else {}

        # Титулы (какие есть у игрока)
        titles_rows = db_exec_sync("SELECT title_id FROM titles_inv WHERE user_id = ?", (user_id,), fetchall=True)
        unlocked_titles = [row[0] for row in titles_rows] if titles_rows else []

        # Фоны (какие есть у игрока)
        bgs_rows = db_exec_sync("SELECT bg_id FROM bgs_inv WHERE user_id = ?", (user_id,), fetchall=True)
        unlocked_bgs = [row[0] for row in bgs_rows] if bgs_rows else []

        # --- ДОБАВЛЕНО: Мастер-список титулов напрямую из файла data.cards ---
        all_titles_list = [{"id": k, "name": v} for k, v in TITLES.items()]

        return {
            "diamond": user[0],
            "krw": user[1],
            "battlecoin": user[2],
            "is_premium": is_prem,
            "owned_cards": owned_cards,
            "daily_day": daily_day,
            "can_claim_daily": can_claim_daily,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "max_streak": max_streak,
            "active_title": active_title,
            "fav_cards": fav_cards,
            "unlocked_titles": unlocked_titles,
            "all_titles": all_titles_list,
            "active_bg": user[7] if len(user) > 7 and user[7] else "default",
            "unlocked_bgs": unlocked_bgs
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in get_profile: {e}")
        return {"diamond": 0, "krw": 0, "battlecoin": 0, "is_premium": False,
                "owned_cards": [], "daily_day": 0, "can_claim_daily": False,
                "wins": 0, "losses": 0, "winrate": 0, "max_streak": 0,
                "active_title": None, "fav_cards": {}, "unlocked_titles": []}

# Модели для запросов
class FavPayload(BaseModel):
    card_id: str
    slot_index: int

class TitlePayload(BaseModel):
    title_id: str

class BgPayload(BaseModel):
    bg_id: str

@app.post("/api/profile/bg/{user_id}")
def set_active_bg_api(payload: BgPayload, user_id: int = Depends(authed_user_id)):
    try:
        if payload.bg_id == "default":
            db_exec_sync("UPDATE users SET active_bg = 'default' WHERE id = ?", (user_id,))
            return {"success": True}

        # Проверяем наличие фона в нашей таблице bgs_inv
        has_bg = db_exec_sync("SELECT 1 FROM bgs_inv WHERE user_id = ? AND bg_id = ?", (user_id, payload.bg_id), fetch=True)
        if not has_bg:
            return {"success": False, "error": "У вас нет этого фона в инвентаре"}

        db_exec_sync("UPDATE users SET active_bg = ? WHERE id = ?", (payload.bg_id, user_id))
        return {"success": True}
    except Exception as e:
        logging.error(f"Bg update error: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/profile/favorite/{user_id}")
def set_favorite_card_api(payload: FavPayload, user_id: int = Depends(authed_user_id)):
    try:
        # Удаляем старую карту из этого слота
        db_exec_sync("DELETE FROM favorite_cards WHERE user_id = ? AND slot_index = ?", (user_id, payload.slot_index))
        # Ставим новую (если id передан)
        if payload.card_id and payload.card_id != "none":
            db_exec_sync("INSERT INTO favorite_cards (user_id, card_id, slot_index) VALUES (?, ?, ?)", (user_id, payload.card_id, payload.slot_index))
        return {"success": True}
    except Exception as e:
        logging.error(f"Fav update error: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/profile/title/{user_id}")
def set_active_title_api(payload: TitlePayload, user_id: int = Depends(authed_user_id)):
    try:
        if payload.title_id == "none" or not payload.title_id:
            db_exec_sync("UPDATE users SET active_title = NULL WHERE id = ?", (user_id,))
        else:
            # ПРОВЕРКА: есть ли этот титул у игрока в инвентаре?
            has_title = db_exec_sync("SELECT 1 FROM titles_inv WHERE user_id = ? AND title_id = ?",
                                     (user_id, payload.title_id), fetch=True)
            if not has_title:
                return {"success": False, "error": "У вас нет этого титула"}

            db_exec_sync("UPDATE users SET active_title = ? WHERE id = ?", (payload.title_id, user_id))
        return {"success": True}
    except Exception as e:
        logging.error(f"Title update error: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/claim_daily/{user_id}")
def claim_daily(user_id: int = Depends(authed_user_id)):
    try:
        # Всю проверку + начисление валюты + сдвиг дня делаем в ОДНОЙ
        # транзакции на одном соединении — атомарно. Это убирает риск
        # двойного клейма и "полузачисленных" наград.
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        card_key = None
        try:
            c = conn.cursor()

            try:
                c.execute("SELECT daily_day, last_daily_claim FROM users WHERE id = ?", (user_id,))
                user = c.fetchone()
            except Exception:
                conn.close()
                migrate_daily()
                conn = sqlite3.connect(DB_PATH, timeout=5.0)
                c = conn.cursor()
                c.execute("SELECT daily_day, last_daily_claim FROM users WHERE id = ?", (user_id,))
                user = c.fetchone()

            if not user:
                return {"success": False, "error": "Пользователь не найден в базе"}

            now_msk = datetime.now(timezone(timedelta(hours=3)))
            today_str = now_msk.strftime("%Y-%m-%d")
            last_claim_date = user[1].split(" ")[0] if user[1] else '2000-01-01'

            if last_claim_date == today_str:
                return {"success": False, "error": "Награда уже получена сегодня!"}

            current_day = (user[0] or 0) + 1
            if current_day > 30:
                current_day = 1

            reward = DAILY_REWARDS.get(current_day, {'krw': 200})
            is_pack = 'pack' in reward
            pack_type = reward.get('pack')

            # Сначала фиксируем сам факт клейма + валюту (атомарно).
            if 'krw' in reward:
                c.execute("UPDATE users SET krw = krw + ? WHERE id = ?", (reward['krw'], user_id))
            if 'dia' in reward:
                c.execute("UPDATE users SET diamond = diamond + ? WHERE id = ?", (reward['dia'], user_id))

            c.execute(
                "UPDATE users SET daily_day = ?, last_daily_claim = ? WHERE id = ?",
                (current_day, today_str, user_id)
            )
            conn.commit()
        finally:
            conn.close()

        # Карту выдаём ПОСЛЕ закрытия основной транзакции, чтобы не держать
        # write-lock на БД, пока pull/give открывают свои соединения.
        if is_pack:
            rarity = "Мифическая 🔴" if pack_type == 'mythic' else "Легендарная 🔵"
            card_key = pull_random_card(force_rarity=rarity)
            if card_key:
                give_card_to_user(user_id, card_key)

        new_user = db_exec_sync("SELECT diamond, krw FROM users WHERE id = ?", (user_id,), fetch=True)

        return {
            "success": True,
            "new_krw": new_user[1],
            "new_dia": new_user[0],
            "card_key": card_key
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in claim_daily: {e}")
        return {"success": False, "error": "Внутренняя ошибка сервера. Попробуйте позже."}


# Счётчик карт в боте — это публичная информация (не привязана к юзеру),
# поэтому авторизация тут не нужна.
@app.get("/api/card_count/{card_id}")
def get_card_count(card_id: str):
    res = db_exec_sync("SELECT COUNT(*) FROM cards_inv WHERE card_id = ?", (card_id,), fetch=True)
    count = res[0] if res else 0
    return {"card_id": card_id, "count": count}


# ============================================================
#  РЕФЕРАЛЫ
# ============================================================
@app.get("/api/referral/{user_id}")
def get_referral(user_id: int = Depends(authed_user_id)):
    code_row = db_exec_sync("SELECT referral_code FROM users WHERE id = ?", (user_id,), fetch=True)
    code = code_row[0] if code_row and code_row[0] else ""

    cnt_row = db_exec_sync("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,), fetch=True)
    count = cnt_row[0] if cnt_row else 0

    # Суммы заработанного с рефералов (если колонки заполнялись)
    earned_krw = 0
    earned_attempts = 0
    try:
        agg = db_exec_sync(
            "SELECT COALESCE(SUM(reward_krw),0), COALESCE(SUM(reward_attempts),0) "
            "FROM referrals WHERE referrer_id = ?", (user_id,), fetch=True
        )
        if agg:
            earned_krw = agg[0] or 0
            earned_attempts = agg[1] or 0
    except Exception:
        pass
    # Подстраховка для старых записей без сохранённой награды: минимум 5 круток на реферала
    if count and earned_attempts == 0:
        earned_attempts = count * 5

    return {
        "count": count,
        "code": code,
        "bot_username": BOT_USERNAME,
        "earned_krw": earned_krw,
        "earned_attempts": earned_attempts,
        "reward_krw_min": 500,
        "reward_krw_max": 850,
        "reward_attempts": 5,
    }


# ============================================================
#  ЗАДАНИЯ: общий статус
# ============================================================
@app.get("/api/tasks/{user_id}")
def get_tasks(user_id: int = Depends(authed_user_id)):
    sub = db_exec_sync(
        "SELECT 1 FROM task_claims WHERE user_id = ? AND task_key = 'subscribe'",
        (user_id,), fetch=True
    )
    subscribe_done = bool(sub)

    bc = db_exec_sync("SELECT last_claim FROM boost_claims WHERE user_id = ?", (user_id,), fetch=True)
    boost_last = bc[0] if bc and bc[0] else None
    boost_next = None
    boost_secs = 0
    boost_on_cd = False
    if boost_last:
        try:
            last_dt = datetime.strptime(boost_last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
            nxt = last_dt + timedelta(days=BOOST_COOLDOWN_DAYS)
            boost_next = nxt.strftime("%Y-%m-%d %H:%M:%S")
            boost_secs = _secs_left(boost_next)
            boost_on_cd = boost_secs > 0
        except Exception:
            pass

    def latest_status(t):
        r = db_exec_sync(
            "SELECT status FROM social_submissions WHERE user_id = ? AND task_type = ? "
            "ORDER BY id DESC LIMIT 1", (user_id, t), fetch=True
        )
        return r[0] if r else "none"

    return {
        "subscribe_done": subscribe_done,
        "boost_last_claim": boost_last,
        "boost_next_claim": boost_next,
        "boost_seconds_left": boost_secs,
        "boost_on_cooldown": boost_on_cd,
        "tiktok_status": latest_status("tiktok"),
        "story_status": latest_status("story"),
        "rewards": REWARDS,
        "links": {"channel": CHANNEL_LINK, "boost": BOOST_LINK, "tiktok": TIKTOK_HASHTAG_LINK},
    }


# ============================================================
#  ЗАДАНИЯ: проверка подписки (Партнёры)
# ============================================================
@app.post("/api/check_subscription/{user_id}")
async def check_subscription(user_id: int = Depends(authed_user_id)):
    if BOT_INSTANCE is None:
        return {"ok": False, "error": "Бот ещё не запущен, попробуйте позже"}
    subscribed = await _is_subscribed(user_id)
    if not subscribed:
        return {"ok": True, "subscribed": False, "rewarded": False}

    already = db_exec_sync(
        "SELECT 1 FROM task_claims WHERE user_id = ? AND task_key = 'subscribe'",
        (user_id,), fetch=True
    )
    if already:
        return {"ok": True, "subscribed": True, "rewarded": False}

    r = REWARDS["subscribe"]
    _credit(user_id, krw=r.get("krw", 0), dia=r.get("dia", 0))
    db_exec_sync(
        "INSERT OR IGNORE INTO task_claims (user_id, task_key, claimed_at) VALUES (?, 'subscribe', ?)",
        (user_id, _now_str())
    )
    return {"ok": True, "subscribed": True, "rewarded": True, "reward": r}


# ============================================================
#  ЗАДАНИЯ: проверка буста (раз в 7 дней)
# ============================================================
@app.post("/api/check_boost/{user_id}")
async def check_boost(user_id: int = Depends(authed_user_id)):
    if BOT_INSTANCE is None:
        return {"ok": False, "error": "Бот ещё не запущен, попробуйте позже"}

    boosting = await _is_boosting(user_id)
    if not boosting:
        return {"ok": True, "boosting": False}

    now = datetime.now(MSK)
    bc = db_exec_sync("SELECT last_claim FROM boost_claims WHERE user_id = ?", (user_id,), fetch=True)
    last = bc[0] if bc and bc[0] else None

    if last:
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
            nxt = last_dt + timedelta(days=BOOST_COOLDOWN_DAYS)
            if now < nxt:
                nxt_str = nxt.strftime("%Y-%m-%d %H:%M:%S")
                return {"ok": True, "boosting": True, "claimed": False,
                        "boost_next_claim": nxt_str, "boost_seconds_left": _secs_left(nxt_str)}
        except Exception:
            pass

    # Можно забирать награду
    r = REWARDS["boost"]
    _credit(user_id, krw=r.get("krw", 0), dia=r.get("dia", 0))
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    if last:
        db_exec_sync("UPDATE boost_claims SET last_claim = ? WHERE user_id = ?", (now_str, user_id))
    else:
        db_exec_sync("INSERT INTO boost_claims (user_id, last_claim) VALUES (?, ?)", (user_id, now_str))
    nxt_str = (now + timedelta(days=BOOST_COOLDOWN_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    return {"ok": True, "boosting": True, "claimed": True, "reward": r,
            "boost_next_claim": nxt_str, "boost_seconds_left": _secs_left(nxt_str)}


# ============================================================
#  ЗАДАНИЯ: отправка TikTok/Сторис на модерацию
# ============================================================
class SocialPayload(BaseModel):
    task_type: str = ""
    link: str = ""
    note: str = ""


@app.post("/api/submit_social/{user_id}")
async def submit_social(payload: SocialPayload, user_id: int = Depends(authed_user_id)):
    task_type = (payload.task_type or "").strip()
    link = (payload.link or "").strip()
    note = (payload.note or "").strip()

    if task_type not in ("tiktok", "story"):
        return {"ok": False, "error": "Неизвестный тип задания"}
    if not link.startswith("http"):
        return {"ok": False, "error": "Вставьте корректную ссылку (https://...)"}

    sub_id = _insert_submission(user_id, task_type, link, note, _now_str())

    # Отправляем заявку модератору с кнопками Одобрить/Отклонить
    if BOT_INSTANCE is not None and MODERATION_CHAT_ID:
        r = REWARDS.get(task_type, {})
        label = "🎬 TikTok-видео" if task_type == "tiktok" else "📲 Сторис в Telegram"
        txt = (f"<b>🆕 Новая заявка #{sub_id}</b>\n{label}\n\n"
               f"👤 ID игрока: <code>{user_id}</code>\n"
               f"🔗 Ссылка: {link}\n"
               f"💬 Комментарий: {note or '—'}\n\n"
               f"💰 При одобрении: {r.get('krw', 0)} ₩ + {r.get('dia', 0)} 💎")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ts:ok:{sub_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ts:no:{sub_id}"),
        ]])
        try:
            await BOT_INSTANCE.send_message(MODERATION_CHAT_ID, txt, reply_markup=kb)
        except Exception as e:
            logging.error(f"Не удалось отправить заявку модератору: {e}")

    return {"ok": True}


# ============================================================
#  МОДЕРАЦИЯ: кнопки Одобрить / Отклонить под заявкой
# ============================================================
@mod_router.callback_query(F.data.startswith("ts:"))
async def moderate_submission(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("⛔ У вас нет прав на модерацию.", show_alert=True)
        return

    try:
        _, action, sid = cq.data.split(":")
        sid = int(sid)
    except Exception:
        await cq.answer("Ошибка данных заявки.", show_alert=True)
        return

    row = db_exec_sync(
        "SELECT user_id, task_type, status FROM social_submissions WHERE id = ?",
        (sid,), fetch=True
    )
    if not row:
        await cq.answer("Заявка не найдена.", show_alert=True)
        return

    uid, ttype, status = row[0], row[1], row[2]
    if status != "pending":
        await cq.answer("Эта заявка уже обработана.", show_alert=True)
        return

    if action == "ok":
        r = REWARDS.get(ttype, {})
        _credit(uid, krw=r.get("krw", 0), dia=r.get("dia", 0))
        db_exec_sync("UPDATE social_submissions SET status = 'approved' WHERE id = ?", (sid,))
        try:
            await BOT_INSTANCE.send_message(
                uid,
                f"✅ <b>Твоя заявка одобрена!</b>\n\nНачислено: "
                f"{r.get('krw', 0)} ₩ + {r.get('dia', 0)} 💎"
            )
        except Exception:
            pass
        try:
            await cq.message.edit_text(cq.message.html_text + "\n\n<b>✅ ОДОБРЕНО</b>")
        except Exception:
            pass
        await cq.answer("Одобрено ✅")
    else:
        db_exec_sync("UPDATE social_submissions SET status = 'rejected' WHERE id = ?", (sid,))
        try:
            await BOT_INSTANCE.send_message(
                uid, "❌ <b>Твоя заявка отклонена модератором.</b>\nПопробуй ещё раз, соблюдая условия задания."
            )
        except Exception:
            pass
        try:
            await cq.message.edit_text(cq.message.html_text + "\n\n<b>❌ ОТКЛОНЕНО</b>")
        except Exception:
            pass
        await cq.answer("Отклонено")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
