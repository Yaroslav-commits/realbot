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
from data.cards import TITLES, CARDS, RARITIES
import random
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from aiogram.types import (
    WebAppInfo, MenuButtonWebApp,
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from pydantic import BaseModel

from config import BOT_TOKEN, DB_PATH
from database.db import init_db, is_premium, pull_random_card, give_card_to_user, add_pass_xp, check_and_update_quests
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
MODERATION_CHAT_ID = 6378471773  # <-- ОБЯЗАТЕЛЬНО ЗАМЕНИ НА СВОЙ ID

# Кто имеет право жать «Одобрить / Отклонить» под заявкой.
# Если заявки летят в группу — впиши сюда РЕАЛЬНЫЕ user_id админов.
ADMIN_IDS = {MODERATION_CHAT_ID}

# Награды за задания (₩ = krw, 💎 = diamond).
REWARDS = {
    "subscribe": {"krw": 1000, "dia": 5},    # подписка на канал (Партнёры)
    "boost":     {"krw": 2000, "dia": 10},   # буст канала (раз в 7 дней)
    "tiktok":    {"krw": 4000, "dia": 10},   # TikTok-видео (после модерации)
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
            # Отсекаем пустые строки и текст 'NULL'/'None' прямо в SQL-запросе
            query = "SELECT id, premium_until FROM users WHERE premium_until IS NOT NULL AND premium_until != '' AND premium_until != 'NULL' AND premium_until != 'None'"
            users = db_exec_sync(query, fetchall=True)

            if users:
                for uid, until_str in users:
                    try:
                        until_dt = datetime.strptime(until_str.strip(), "%Y-%m-%d %H:%M:%S")

                        # Если дата из прошлого (до 2023 года) — это заглушка '2000-01-01'.
                        # Игрок никогда не имел премиума. Просто молча сбрасываем и пропускаем.
                        if until_dt.year < 2023:
                            db_exec_sync("UPDATE users SET premium_until = NULL WHERE id = ?", (uid,))
                            continue

                        if until_dt < now:
                            # ВАЖНО: СНАЧАЛА обнуляем дату в БД, чтобы при любых лагах Телеграма не было спама
                            db_exec_sync("UPDATE users SET premium_until = NULL WHERE id = ?", (uid,))

                            # Срок действия истек! Уведомляем игрока.
                            try:
                                await bot.send_message(
                                    uid,
                                    "🥀 <b>Срок действия Premium-подписки истёк...</b>\n\n"
                                    "Премиум-бонусы больше недоступны. "
                                    "Но ты всегда можешь вернуть свой статус 👑 в Магазине!",
                                    parse_mode="HTML"
                                )
                            except Exception:
                                pass  # Игрок мог заблокировать бота

                    except Exception:
                        # Если дата кривая (ошибка парсинга), просто молча сбрасываем
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


# Глобальный список для строгого контроля и уничтожения зомби-процессов при перезапусках
BACKGROUND_TASKS = []


# ============================================================
#  ЗАПУСК БОТА (ОПТИМИЗИРОВАННЫЙ ПОД ХОСТИНГ)
# ============================================================
async def start_bot():
    global BACKGROUND_TASKS, BOT_INSTANCE, BOT_USERNAME
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

    from handlers.battle import auto_pack_reset_notifier

    # Фиксируем каждую фоновую задачу в глобальный список, чтобы гарантированно убить их при деплое
    t1 = asyncio.create_task(auto_pack_reset_notifier(bot))
    t2 = asyncio.create_task(cooldown_notification_scheduler(bot))
    t3 = asyncio.create_task(battle_cooldown_notification_scheduler(bot))
    t4 = asyncio.create_task(auto_top_distributor(bot))
    t5 = asyncio.create_task(premium_expiration_scheduler(bot))
    BACKGROUND_TASKS.extend([t1, t2, t3, t4, t5])

    print("Ждём 3 секунды для отключения старых процессов...")
    await asyncio.sleep(3)  # <-- Даем старому боту спокойно умереть

    print("Бот успешно запущен в фоновом режиме!")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logging.error(f"Ошибка при поллинге (возможно, конфликт): {e}")
    finally:
        # Важнейший фикс: при любой остановке принудительно закрываем сетевую сессию бота
        await bot.session.close()


# ============================================================
#  СПАСИТЕЛЬНАЯ МИГРАЦИЯ ДЛЯ БАЗЫ ДАННЫХ
# ============================================================
def migrate_profile_stats():
    """Добавляет недостающие колонки в старую базу данных, чтобы профиль не крашился с нулями."""
    columns = [
        ("wins", "INTEGER DEFAULT 0"),
        ("losses", "INTEGER DEFAULT 0"),
        ("season_wins", "INTEGER DEFAULT 0"),  # <-- ДОБАВИЛИ ЭТУ СТРОКУ
        ("max_streak", "INTEGER DEFAULT 0"),
        ("active_bg", "TEXT DEFAULT 'default'"),
        ("active_title", "TEXT"),
        ("pass_level", "INTEGER DEFAULT 1"),
        ("pass_xp", "INTEGER DEFAULT 0"),
        ("claimed_pass_levels", "INTEGER DEFAULT 1")
    ]
    for col, col_def in columns:
        try:
            db_exec_sync(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
        except Exception:
            pass

    # Гарантируем, что новые таблицы для инвентаря тоже созданы
    try:
        db_exec_sync("CREATE TABLE IF NOT EXISTS bgs_inv (user_id INTEGER, bg_id TEXT)")
        db_exec_sync("CREATE TABLE IF NOT EXISTS titles_inv (user_id INTEGER, title_id TEXT)")
        db_exec_sync("CREATE TABLE IF NOT EXISTS favorite_cards (user_id INTEGER, card_id TEXT, slot_index INTEGER)")
    except Exception:
        pass


async def weekly_quest_reset_loop():
    """Фоновая задача для тихого сброса недельных заданий каждый понедельник в 00:00 МСК"""
    msk_tz = timezone(timedelta(hours=3))
    while True:
        now_msk = datetime.now(msk_tz)
        # Если наступил Понедельник (0) и время ровно 00:00
        if now_msk.weekday() == 0 and now_msk.hour == 0 and now_msk.minute == 0:
            try:
                from database.db import generate_new_quests
                users = db_exec_sync("SELECT id FROM users", fetchall=True)
                for (uid,) in users:
                    generate_new_quests(uid[0])
                logging.info("Недельные задания пасса успешно сброшены и обновлены для всех игроков.")
            except Exception as e:
                logging.error(f"Ошибка при автоматическом сбросе недельных заданий: {e}")

            # Засыпаем на 60 секунд, чтобы код не выполнился повторно в эту же минуту
            await asyncio.sleep(60)

        # Проверяем время каждые 30 секунд
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global BACKGROUND_TASKS
    init_db()
    migrate_daily()
    migrate_earn()
    migrate_profile_stats()

    try:
        db_exec_sync("PRAGMA journal_mode=WAL")
    except Exception:
        pass

    bot_task = asyncio.create_task(start_bot())

    # === НАШ НОВЫЙ ТИХИЙ СБРОС КВЕСТОВ ПО ПОНЕДЕЛЬНИКАМ ===
    quest_task = asyncio.create_task(weekly_quest_reset_loop())
    BACKGROUND_TASKS.append(quest_task)

    yield

    # --- НАЧАЛО БЕЗОПАСНОГО ВЫКЛЮЧЕНИЯ (ОБНУЛЕНИЕ СТАРЫХ ХУКОВ) ---
    print("ВНИМАНИЕ: Запущено полное уничтожение старых процессов приложения...")
    bot_task.cancel()

    # Принудительно отменяем абсолютно все запущенные циклы уведомлений и топов
    for task in BACKGROUND_TASKS:
        if not task.done():
            task.cancel()

    # Дожидаемся пока они полностью отпустят базу данных и Telegram-токен
    await asyncio.gather(bot_task, *BACKGROUND_TASKS, return_exceptions=True)
    BACKGROUND_TASKS.clear()
    print("Все зомби-процессы успешно ликвидированы. База данных и порт чисты!")


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


@app.get("/api/profile/{user_id}")
def get_profile(user_id: int):
    try:
        # Автоматически проверяем и создаем новые колонки для Пасса, если их нет
        for col, col_type in [
            ("pass_level", "INTEGER DEFAULT 1"),
            ("pass_xp", "INTEGER DEFAULT 0"),
            ("claimed_pass_levels", "INTEGER DEFAULT 1"),
            ("pass_quests", "TEXT")
        ]:
            try:
                db_exec_sync(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
            except Exception:
                pass

        # === MANHWCARD PASS: 30 XP ЗА ЕЖЕДНЕВНЫЙ ВХОД ===
        try:
            db_exec_sync("ALTER TABLE users ADD COLUMN last_webapp_login TEXT DEFAULT '2000-01-01'")
        except Exception:
            pass

        msk_tz = timezone(timedelta(hours=3))
        today_str = datetime.now(msk_tz).strftime("%Y-%m-%d")
        res_login = db_exec_sync("SELECT last_webapp_login FROM users WHERE id = ?", (user_id,), fetch=True)
        if res_login and res_login[0] != today_str:
            add_pass_xp(user_id, 30)
            db_exec_sync("UPDATE users SET last_webapp_login = ? WHERE id = ?", (today_str, user_id))
        # =================================================

        # Извлекаем все необходимые данные пользователя одним запросом (включая Пасс и Квесты)
        user = db_exec_sync(
            "SELECT diamond, krw, battlecoin, wins, losses, max_streak, active_title, active_bg, pass_level, pass_xp, claimed_pass_levels, pass_quests, attempts FROM users WHERE id = ?",
            (user_id,), fetch=True
        )
        if not user:
            return {"diamond": 0, "krw": 0, "battlecoin": 0, "is_premium": False,
                    "owned_cards": [], "daily_day": 0, "can_claim_daily": False,
                    "wins": 0, "losses": 0, "winrate": 0, "max_streak": 0,
                    "active_title": None, "fav_cards": {}, "unlocked_titles": [],
                    "pass_level": 1, "pass_xp": 0, "claimed_pass_levels": 1, "pass_quests": {}}

        # Обработка и генерация квестов Пасса
        from database.db import generate_new_quests
        pass_quests_dict = {}
        pass_quests_raw = user[11]
        if not pass_quests_raw:
            pass_quests_dict = generate_new_quests(user_id)
        else:
            try:
                pass_quests_dict = json.loads(pass_quests_raw)
            except Exception:
                pass_quests_dict = generate_new_quests(user_id)

        # Логика миграции и проверки ежедневных наград Web App
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

        now_msk = datetime.now(timezone(timedelta(hours=3)))
        today_date = now_msk.date()

        last_claim_date_str = last_claim_date.split(" ")[0] if last_claim_date else '2000-01-01'
        try:
            last_dt = datetime.strptime(last_claim_date_str, "%Y-%m-%d").date()
            days_passed = (today_date - last_dt).days
        except Exception:
            days_passed = 0

        can_claim_daily = (days_passed > 0)
        needs_recovery = (days_passed > 1 and 0 < daily_day < 30)

        is_prem = is_premium(user_id)
        # Собираем карты и из инвентаря, и из сундука
        cards_rows = db_exec_sync(
            """
            SELECT card_id FROM cards_inv WHERE user_id = ?
            UNION ALL
            SELECT card_id FROM cards_stash WHERE user_id = ?
            """, (user_id, user_id), fetchall=True
        )
        owned_cards = [row[0] for row in cards_rows] if cards_rows else []

        # Статистика боёв
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

        # Инвентарь титулов
        titles_rows = db_exec_sync("SELECT title_id FROM titles_inv WHERE user_id = ?", (user_id,), fetchall=True)
        unlocked_titles = [row[0] for row in titles_rows] if titles_rows else []

        # Инвентарь фонов
        bgs_rows = db_exec_sync("SELECT bg_id FROM bgs_inv WHERE user_id = ?", (user_id,), fetchall=True)
        unlocked_bgs = [row[0] for row in bgs_rows] if bgs_rows else []

        import re
        all_titles_list = []
        for k, v in TITLES.items():
            clean_name = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', v)
            all_titles_list.append({"id": k, "name": clean_name})

        return {
            "diamond": user[0],
            "krw": user[1],
            "battlecoin": user[2],
            "attempts": user[12] if len(user) > 12 else 0,
            "is_premium": is_prem,
            "owned_cards": owned_cards,
            "daily_day": daily_day,
            "can_claim_daily": can_claim_daily,
            "needs_recovery": needs_recovery,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "max_streak": max_streak,
            "active_title": active_title,
            "fav_cards": fav_cards,
            "unlocked_titles": unlocked_titles,
            "all_titles": all_titles_list,
            "active_bg": user[7] if len(user) > 7 and user[7] else "default",
            "unlocked_bgs": unlocked_bgs,
            "pass_level": user[8] if user[8] is not None else 1,
            "pass_xp": user[9] if user[9] is not None else 0,
            "claimed_pass_levels": user[10] if user[10] is not None else 1,
            "pass_quests": pass_quests_dict
        }
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

@app.post("/api/set_favorite/{user_id}")
def set_favorite_card_api(payload: FavPayload, user_id: int = Depends(authed_user_id)):
    try:
        # 1. Гарантируем, что таблица любимых карт существует
        db_exec_sync("""
            CREATE TABLE IF NOT EXISTS favorite_cards (
                user_id INTEGER,
                slot_index INTEGER,
                card_id TEXT,
                PRIMARY KEY (user_id, slot_index)
            )
        """)

        # 2. Удаляем старую карту из этого слота
        db_exec_sync("DELETE FROM favorite_cards WHERE user_id = ? AND slot_index = ?", (user_id, payload.slot_index))

        # 3. Ставим новую (если id передан и это не 'none')
        if payload.card_id and payload.card_id != "none":
            db_exec_sync("INSERT INTO favorite_cards (user_id, card_id, slot_index) VALUES (?, ?, ?)",
                         (user_id, payload.card_id, payload.slot_index))

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

class DailyPayload(BaseModel):
    action: str = "claim"  # Может быть: "claim", "recover", "reset"

@app.post("/api/claim_daily/{user_id}")
def claim_daily(payload: DailyPayload, user_id: int = Depends(authed_user_id)):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        card_key = None
        is_duplicate = False
        dup_reward = 0

        try:
            c = conn.cursor()

            try:
                c.execute("SELECT daily_day, last_daily_claim, diamond FROM users WHERE id = ?", (user_id,))
                user = c.fetchone()
            except Exception:
                conn.close()
                migrate_daily()
                conn = sqlite3.connect(DB_PATH, timeout=5.0)
                c = conn.cursor()
                c.execute("SELECT daily_day, last_daily_claim, diamond FROM users WHERE id = ?", (user_id,))
                user = c.fetchone()

            if not user:
                return {"success": False, "error": "Пользователь не найден в базе"}

            now_msk = datetime.now(timezone(timedelta(hours=3)))
            today_date = now_msk.date()
            today_str = today_date.strftime("%Y-%m-%d")

            last_claim_date_str = user[1].split(" ")[0] if user[1] else '2000-01-01'
            try:
                last_dt = datetime.strptime(last_claim_date_str, "%Y-%m-%d").date()
                days_passed = (today_date - last_dt).days
            except Exception:
                days_passed = 0

            if days_passed == 0:
                return {"success": False, "error": "Награда уже получена сегодня!"}

            daily_day = user[0] or 0
            diamonds = user[2] or 0
            needs_recovery = (days_passed > 1 and 0 < daily_day < 30)

            if payload.action == "recover":
                if not needs_recovery:
                    return {"success": False, "error": "Стрик не прерван, восстановление не требуется!"}
                if diamonds < 10:
                    return {"success": False, "error": "Недостаточно алмазов (нужно 10 💎)"}
                c.execute("UPDATE users SET diamond = diamond - 10 WHERE id = ?", (user_id,))
                current_day = daily_day + 1

            elif payload.action == "reset":
                if not needs_recovery:
                    return {"success": False, "error": "Стрик не прерван!"}
                current_day = 1

            else:
                if needs_recovery:
                    return {"success": False, "error": "Нужно восстановить стрик или начать заново!"}
                current_day = daily_day + 1 if daily_day < 30 else 1

            reward = DAILY_REWARDS.get(current_day, {'krw': 200})
            is_pack = 'pack' in reward
            pack_type = reward.get('pack')

            if 'krw' in reward:
                c.execute("UPDATE users SET krw = krw + ? WHERE id = ?", (reward['krw'], user_id))
            if 'dia' in reward:
                c.execute("UPDATE users SET diamond = diamond + ? WHERE id = ?", (reward['dia'], user_id))

            # Логика выдачи пака
            if is_pack:
                rarity = "Мифическая 🔴" if pack_type == 'mythic' else "Легендарная 🔵"
                card_key = pull_random_card(force_rarity=rarity)

                if card_key:
                    card_data = CARDS.get(card_key)
                    c.execute("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (user_id, card_key))
                    if c.fetchone():
                        is_duplicate = True
                        r_name = card_data.get('rarity', 'Обычная ⚪️') if card_data else 'Обычная ⚪️'
                        dup_range = RARITIES.get(r_name, {}).get("dup", (10, 20))
                        dup_reward = random.randint(dup_range[0], dup_range[1])
                        c.execute("UPDATE users SET krw = krw + ? WHERE id = ?", (dup_reward, user_id))
                    else:
                        give_card_to_user(user_id, card_key)

            # Сохраняем день сбора
            c.execute(
                "UPDATE users SET daily_day = ?, last_daily_claim = ? WHERE id = ?",
                (current_day, today_str, user_id)
            )

            conn.commit()
        finally:
            # ЗАКРЫВАЕМ БАЗУ ДАННЫХ И СНИМАЕМ БЛОКИРОВКУ
            conn.close()

        # =========================================================
        # === MANHWCARD PASS: 70 XP ЗА СБОР ЕЖЕДНЕВКИ В WEB APP ===
        # Теперь это вызывается безопасно, когда база свободна!
        # =========================================================
        try:
            add_pass_xp(user_id, 70)
        except Exception as pass_err:
            logging.error(f"Ошибка при выдаче XP за пасс: {pass_err}")

        # Получаем обновленные данные пользователя для ответа фронту
        from database.db import db_exec
        new_user = db_exec("SELECT diamond, krw FROM users WHERE id = ?", (user_id,), fetch=True)

        resp = {
            "success": True,
            "new_krw": new_user[1] if new_user else 0,
            "new_dia": new_user[0] if new_user else 0
        }

        if is_pack and card_key:
            card_data = CARDS.get(card_key, {})
            resp["card_key"] = card_key
            resp["card_file"] = card_data.get("file", "")
            resp["card_name"] = card_data.get("name", "")
            resp["card_rarity"] = card_data.get("rarity", "")
            resp["is_duplicate"] = is_duplicate
            resp["dup_reward"] = dup_reward

        return resp
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in claim_daily: {e}")
        return {"success": False, "error": "Внутренняя ошибка сервера. Попробуйте позже."}


# Счётчик карт в боте — это публичная информация (не привязана к юзеру),
# поэтому авторизация тут не нужна.
@app.get("/api/card_count/{card_id}")
def get_card_count(card_id: str):
    res = db_exec_sync("""
        SELECT 
            (SELECT COUNT(*) FROM cards_inv WHERE card_id = ?) + 
            (SELECT COUNT(*) FROM cards_stash WHERE card_id = ?)
    """, (card_id, card_id), fetch=True)
    count = res[0] if res and res[0] is not None else 0
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
    # Подстраховка для старых записей без сохранённой награды: минимум 3 круток на реферала
    if count and earned_attempts == 0:
        earned_attempts = count * 3

    return {
        "count": count,
        "code": code,
        "bot_username": BOT_USERNAME,
        "earned_krw": earned_krw,
        "earned_attempts": earned_attempts,
        "reward_krw_min": 300,
        "reward_krw_max": 550,
        "reward_attempts": 3,
    }


# ============================================================
#  БЕЗОПАСНАЯ МИГРАЦИЯ (Бусты, Подписки, Стрик)
# ============================================================
try:
    db_exec_sync("ALTER TABLE users ADD COLUMN subscribe_done INTEGER DEFAULT 0")
except:
    pass
try:
    db_exec_sync("ALTER TABLE users ADD COLUMN last_boost_claim TEXT")
except:
    pass
try:
    db_exec_sync("ALTER TABLE users ADD COLUMN max_streak INTEGER DEFAULT 0")
except:
    pass


# ============================================================
#  ЗАДАНИЯ: общий статус
# ============================================================
@app.get("/api/tasks/{user_id}")
def get_tasks(user_id: int = Depends(authed_user_id)):
    user = db_exec_sync("SELECT subscribe_done, last_boost_claim FROM users WHERE id = ?", (user_id,), fetch=True)
    subscribe_done = bool(user[0]) if user else False
    boost_last = user[1] if user else None

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
        "ok": True,
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
    try:
        member = await BOT_INSTANCE.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        is_subbed = str(member.status) in ("member", "administrator", "creator", "ChatMemberStatus.MEMBER",
                                           "ChatMemberStatus.ADMINISTRATOR", "ChatMemberStatus.CREATOR")
    except Exception as e:
        logging.error(f"Sub check error: {e}")
        return {"ok": False, "error": "Бот не является админом канала или канал не найден."}

    if not is_subbed:
        return {"ok": True, "subscribed": False, "rewarded": False}

    user = db_exec_sync("SELECT subscribe_done FROM users WHERE id = ?", (user_id,), fetch=True)
    if user and user[0]:
        return {"ok": True, "subscribed": True, "rewarded": False}

    r = REWARDS.get("subscribe", {"krw": 1000, "dia": 5})
    _credit(user_id, krw=r.get("krw", 0), dia=r.get("dia", 0))
    db_exec_sync("UPDATE users SET subscribe_done = 1 WHERE id = ?", (user_id,))
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
    user = db_exec_sync("SELECT last_boost_claim FROM users WHERE id = ?", (user_id,), fetch=True)
    last = user[0] if user else None

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

    r = REWARDS.get("boost", {"krw": 2000, "dia": 10})
    _credit(user_id, krw=r.get("krw", 0), dia=r.get("dia", 0))
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    db_exec_sync("UPDATE users SET last_boost_claim = ? WHERE id = ?", (now_str, user_id))

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
               f"💰 При одобрении: {r.get('krw', 0)} KRW + {r.get('dia', 0)} 💎")
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
                f"{r.get('krw', 0)} KRW 💴 + {r.get('dia', 0)} 💎"
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


# ============================================================
#  ТОПЫ И РЕЙТИНГИ ИГРОКОВ (ВЫДАЧА ТОП-25)
# ============================================================

@app.get("/api/tops/time_left")
def get_season_time_left():
    """Высчитывает, сколько времени осталось до 17-го числа 00:00 МСК."""
    try:
        now_msk = datetime.now(MSK)
        # Если сегодня 17-е число или позже, то следующий сброс будет в следующем месяце
        if now_msk.day >= 17:
            if now_msk.month == 12:
                next_reset = datetime(now_msk.year + 1, 1, 17, 0, 0, 0, tzinfo=MSK)
            else:
                next_reset = datetime(now_msk.year, now_msk.month + 1, 17, 0, 0, 0, tzinfo=MSK)
        else:
            # Сегодня до 17-го числа, сброс в этом месяце
            next_reset = datetime(now_msk.year, now_msk.month, 17, 0, 0, 0, tzinfo=MSK)

        seconds_left = int((next_reset - now_msk).total_seconds())
        return {"success": True, "seconds_left": max(0, seconds_left)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/tops/{category}")
def get_leaderboard(category: str):
    """Возвращает ТОП-25 игроков по выбранной категории (С УРОВНЯМИ)."""
    try:
        if category == "krw":
            query = "SELECT id, nickname, username, krw, pass_level FROM users ORDER BY krw DESC LIMIT 25"
            rows = db_exec_sync(query, fetchall=True)
            leaderboard = [{"id": r[0], "name": r[1] or r[2] or f"Игрок {r[0]}", "score": f"{r[3]:,} ₩", "level": r[4] or 1} for r in rows]

        elif category == "rank":
            query = "SELECT id, nickname, username, rank_points, pass_level FROM users ORDER BY rank_points DESC LIMIT 25"
            rows = db_exec_sync(query, fetchall=True)
            leaderboard = [{"id": r[0], "name": r[1] or r[2] or f"Игрок {r[0]}", "score": f"{r[3]} RP", "level": r[4] or 1} for r in rows]

        elif category == "diamond":
            query = "SELECT id, nickname, username, diamond, pass_level FROM users ORDER BY diamond DESC LIMIT 25"
            rows = db_exec_sync(query, fetchall=True)
            leaderboard = [{"id": r[0], "name": r[1] or r[2] or f"Игрок {r[0]}", "score": f"{r[3]} 💎", "level": r[4] or 1} for r in rows]

        elif category == "bc":
            # 🔥 НОВАЯ КАТЕГОРИЯ: ТОП ПО BATTLECOIN 🔥
            query = "SELECT id, nickname, username, battlecoin, pass_level FROM users ORDER BY battlecoin DESC LIMIT 25"
            rows = db_exec_sync(query, fetchall=True)
            leaderboard = [{"id": r[0], "name": r[1] or r[2] or f"Игрок {r[0]}", "score": f"{r[3]:,} 🪙", "level": r[4] or 1} for r in rows]

        elif category == "pvp":
            # ТОП PvP (ЗА ВСЁ ВРЕМЯ)
            query = "SELECT id, nickname, username, wins, pass_level FROM users ORDER BY wins DESC LIMIT 25"
            rows = db_exec_sync(query, fetchall=True)
            leaderboard = [{"id": r[0], "name": r[1] or r[2] or f"Игрок {r[0]}", "score": f"{r[3] or 0} побед", "level": r[4] or 1} for r in rows]

        elif category == "pvp_season":
            # ТОП PvP (ТЕКУЩИЙ СЕЗОН)
            query = "SELECT id, nickname, username, season_wins, pass_level FROM users ORDER BY season_wins DESC LIMIT 25"
            rows = db_exec_sync(query, fetchall=True)
            leaderboard = [{"id": r[0], "name": r[1] or r[2] or f"Игрок {r[0]}", "score": f"{r[3] or 0} побед", "level": r[4] or 1} for r in rows]

        elif category == "cards":
            query = """
                SELECT u.id, u.nickname, u.username, COUNT(c.card_id) as cards_count, u.pass_level 
                FROM users u
                LEFT JOIN cards_inv c ON u.id = c.user_id
                GROUP BY u.id
                ORDER BY cards_count DESC
                LIMIT 25
            """
            rows = db_exec_sync(query, fetchall=True)
            leaderboard = [{"id": r[0], "name": r[1] or r[2] or f"Игрок {r[0]}", "score": f"{r[3]} шт.", "level": r[4] or 1} for r in rows]

        elif category == "level":
            # ТОП ПО УРОВНЯМ
            query = "SELECT id, nickname, username, pass_level FROM users ORDER BY pass_level DESC, pass_xp DESC LIMIT 25"
            rows = db_exec_sync(query, fetchall=True)
            leaderboard = [{"id": r[0], "name": r[1] or r[2] or f"Игрок {r[0]}", "score": f"Ур. {r[3]}", "level": r[3] or 1} for r in rows]

        else:
            return {"success": False, "error": "Неизвестная категория рейтинга"}

        return {"success": True, "leaderboard": leaderboard}
    except Exception as e:
        logging.error(f"Error loading leaderboard {category}: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/public_profile/{target_id}")
def get_public_profile(target_id: int):
    """Возвращает публичные данные профиля (ИСПРАВЛЕН БАГ ПРЕМИУМА)."""
    try:
        user = db_exec_sync(
            "SELECT id, username, nickname, diamond, krw, battlecoin, wins, losses, max_streak, active_title, active_bg, royale_pass FROM users WHERE id = ?",
            (target_id,), fetch=True
        )
        if not user:
            return {"success": False, "error": "Игрок не найден"}

        wins = user[6] or 0
        losses = user[7] or 0
        total_games = wins + losses
        winrate = int((wins / total_games) * 100) if total_games > 0 else 0

        fav_rows = db_exec_sync("SELECT slot_index, card_id FROM favorite_cards WHERE user_id = ?", (target_id,), fetchall=True)
        fav_cards = {row[0]: row[1] for row in fav_rows} if fav_rows else {}

        return {
            "success": True,
            "profile": {
                "id": user[0],
                "username": user[1],
                "nickname": user[2],
                "diamond": user[3],
                "krw": user[4],
                "battlecoin": user[5],
                "wins": wins,
                "losses": losses,
                "max_streak": user[8],
                "winrate": winrate,
                "active_title": user[9],
                "active_bg": user[10] or "default",
                "is_premium": is_premium(target_id),
                "fav_cards": fav_cards
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/avatar/{user_id}")
async def get_telegram_user_avatar(user_id: int):
    """Умный шлюз: запрашивает аватарку любого игрока у Telegram."""
    try:
        photos = await BOT_INSTANCE.get_user_profile_photos(user_id=user_id, limit=1)
        if photos and photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            file = await BOT_INSTANCE.get_file(file_id)
            if file and file.file_path:
                return RedirectResponse(url=f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}")
    except Exception as e:
        logging.error(f"Error fetching telegram avatar for user {user_id}: {e}")

    return RedirectResponse(url="https://placehold.co/150x150/1c1c28/8b5cf6?text=U")


@app.post("/api/pass_claim_level/{user_id}")
def pass_claim_level(user_id: int = Depends(authed_user_id)):
    """Эндпоинт для выдачи награды за получение уровня в ManhwCard Pass"""
    try:
        user = db_exec_sync("SELECT pass_level, claimed_pass_levels FROM users WHERE id = ?", (user_id,), fetch=True)
        if not user:
            return {"success": False, "error": "Игрок не найден"}

        real_level = user[0] if user[0] is not None else 1
        claimed_level = user[1] if user[1] is not None else 1

        # Проверяем, есть ли несобранные награды
        if claimed_level >= real_level:
            return {"success": False, "error": "Нет доступных наград за уровни!"}

        target_level = claimed_level + 1

        # Отмечаем, что забрали один уровень
        db_exec_sync("UPDATE users SET claimed_pass_levels = claimed_pass_levels + 1 WHERE id = ?", (user_id,))

        # Логика шансов остается прежней: 70% KRW, 25% BattleCoin, 5% Алмазы
        reward_type = random.choices(['krw', 'bc', 'dia'], weights=[70, 25, 5], k=1)[0]

        # Таблица наград по уровням (до 30 уровня)
        rewards_map = {
            2: {'krw': (30, 35), 'dia': (2, 3), 'bc': (10, 25)},
            3: {'krw': (30, 35), 'dia': (2, 3), 'bc': (20, 25)},
            4: {'krw': (35, 35), 'dia': (2, 3), 'bc': (25, 25)},
            5: {'krw': (35, 50), 'dia': (2, 3), 'bc': (25, 30)},
            6: {'krw': (35, 50), 'dia': (2, 3), 'bc': (25, 30)},
            7: {'krw': (50, 50), 'dia': (2, 3), 'bc': (35, 35)},
            8: {'krw': (50, 65), 'dia': (2, 3), 'bc': (25, 30)},
            9: {'krw': (50, 65), 'dia': (2, 3), 'bc': (25, 30)},
            10: {'krw': (65, 65), 'dia': (2, 3), 'bc': (50, 50)},
            11: {'krw': (65, 70), 'dia': (2, 3), 'bc': (30, 30)},
            12: {'krw': (65, 75), 'dia': (2, 3), 'bc': (25, 35)},
            13: {'krw': (65, 75), 'dia': (2, 3), 'bc': (25, 35)},
            14: {'krw': (75, 75), 'dia': (2, 3), 'bc': (50, 50)},
            15: {'krw': (75, 80), 'dia': (2, 3), 'bc': (25, 35)},
            16: {'krw': (80, 80), 'dia': (2, 3), 'bc': (35, 35)},
            17: {'krw': (80, 85), 'dia': (2, 3), 'bc': (30, 35)},
            18: {'krw': (85, 85), 'dia': (2, 3), 'bc': (40, 40)},
            19: {'krw': (85, 90), 'dia': (2, 3), 'bc': (30, 35)},
            20: {'krw': (90, 90), 'dia': (2, 3), 'bc': (50, 50)},
            21: {'krw': (90, 95), 'dia': (2, 3), 'bc': (35, 40)},
            22: {'krw': (95, 95), 'dia': (2, 3), 'bc': (40, 40)},
            23: {'krw': (95, 100), 'dia': (2, 3), 'bc': (35, 40)},
            24: {'krw': (100, 100), 'dia': (2, 3), 'bc': (45, 45)},
            25: {'krw': (100, 110), 'dia': (2, 3), 'bc': (35, 45)},
            26: {'krw': (110, 110), 'dia': (2, 3), 'bc': (45, 45)},
            27: {'krw': (110, 120), 'dia': (2, 3), 'bc': (40, 45)},
            28: {'krw': (120, 130), 'dia': (2, 3), 'bc': (45, 45)},
            29: {'krw': (130, 140), 'dia': (2, 3), 'bc': (45, 50)},
            30: {'krw': (150, 150), 'dia': (2, 3), 'bc': (50, 50)}
        }

        # После 30-го уровня награды стабильно высокие
        if target_level > 30:
            r_krw, r_dia, r_bc = (200, 300), (2, 8), (75, 175)
        else:
            r_krw = rewards_map[target_level]['krw']
            r_dia = rewards_map[target_level]['dia']
            r_bc = rewards_map[target_level]['bc']

        amount = 0
        reward_text = ""

        if reward_type == 'krw':
            amount = random.randint(r_krw[0], r_krw[1])
            db_exec_sync("UPDATE users SET krw = krw + ? WHERE id = ?", (amount, user_id))
            reward_text = f"{amount} 💴 KRW"
        elif reward_type == 'bc':
            amount = random.randint(r_bc[0], r_bc[1])
            db_exec_sync("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (amount, user_id))
            reward_text = f"{amount} 🪙 BattleCoin"
        else:
            amount = random.randint(r_dia[0], r_dia[1])
            db_exec_sync("UPDATE users SET diamond = diamond + ? WHERE id = ?", (amount, user_id))
            reward_text = f"{amount} 💎 Алмазов"

        return {"success": True, "reward": reward_text}
    except Exception as e:
        logging.error(f"Error in pass_claim_level: {e}")
        return {"success": False, "error": str(e)}

# =====================================================================
# НОВАЯ СИСТЕМА МУЛЬТИ-КРУТОК ПО ТЗ (С ПРЕМИУМОМ, ОПЫТОМ И КВЕСТАМИ)
# =====================================================================
class MultiSummonRequest(BaseModel):
    amount: int


@app.post("/api/multi_summon/{user_id}")
def multi_summon_api(req: MultiSummonRequest, user_id: int = Depends(authed_user_id)):
    amount = req.amount
    valid_amounts = [1, 2, 4, 8, 12, 16]

    if amount not in valid_amounts:
        return {"success": False, "error": "Неверное количество круток"}

    # Проверяем, сколько попыток (attempts) у игрока
    user = db_exec_sync("SELECT attempts FROM users WHERE id = ?", (user_id,), fetch=True)
    if not user or user[0] < amount:
        return {"success": False, "error": f"Недостаточно попыток! Нужно: {amount}, у вас: {user[0] if user else 0}"}

    # Списываем ровно то количество попыток, которое открываем
    db_exec_sync("UPDATE users SET attempts = attempts - ? WHERE id = ?", (amount, user_id))

    is_prem = is_premium(user_id)
    mythic_count = 0  # Считаем мифические карты для квеста

    results = []
    for _ in range(amount):
        card_id = pull_random_card(uid=user_id, premium=is_prem)
        card_data = CARDS.get(card_id, {})

        # Проверяем на мифическую для квеста
        if 'Мифич' in card_data.get('rarity', ''):
            mythic_count += 1

        is_dup = False
        dup_reward = 0

        # Проверяем на дубликат
        existing = db_exec_sync("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (user_id, card_id),
                                fetch=True)

        if existing:
            is_dup = True
            r_name = card_data.get('rarity', 'Обычная ⚪️')
            dup_range = RARITIES.get(r_name, {}).get("dup", (10, 20))
            dup_reward = random.randint(dup_range[0], dup_range[1])

            db_exec_sync("UPDATE users SET krw = krw + ? WHERE id = ?", (dup_reward, user_id))
        else:
            give_card_to_user(user_id, card_id)

        results.append({
            "id": card_id,
            "name": card_data.get("name", "Unknown"),
            "rarity": card_data.get("rarity", "Обычная ⚪️"),
            "file": card_data.get("file", ""),
            "is_dup": is_dup,
            "dup_reward": dup_reward
        })

    # 🔥 НАЧИСЛЕНИЕ ОПЫТА И КВЕСТОВ 🔥
    # 1. Даем по 10 опыта за каждую открытую карту
    add_pass_xp(user_id, amount * 10)

    # 2. Засчитываем количество круток в квесты (q_15_pulls)
    check_and_update_quests(user_id, 'q_15_pulls', amount)

    # 3. Если выпали мифические, засчитываем квест (q_1_mythic)
    if mythic_count > 0:
        check_and_update_quests(user_id, 'q_1_mythic', mythic_count)

    return {"success": True, "cards": results, "new_attempts": user[0] - amount}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
