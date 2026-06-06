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

from aiogram.types import WebAppInfo, MenuButtonWebApp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, DB_PATH
from database.db import init_db, is_premium, pull_random_card, give_card_to_user
from handlers import router

# Импорты хендлеров
from handlers import user as _user  # noqa: F401
from handlers import deck as _deck  # noqa: F401
from handlers import battle as _battle  # noqa: F401
from handlers.pass_shop import shop as _shop  # noqa: F401
from handlers.user import cooldown_notification_scheduler, battle_cooldown_notification_scheduler
from handlers.battle import auto_top_distributor


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
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    WEBAPP_URL = "https://yaroslav-commits.github.io/cards-catalog-manhw/"

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="🃏 Каталог", web_app=WebAppInfo(url=WEBAPP_URL))
    )

    await bot.delete_webhook(drop_pending_updates=True)

    asyncio.create_task(cooldown_notification_scheduler(bot))
    asyncio.create_task(battle_cooldown_notification_scheduler(bot))
    asyncio.create_task(auto_top_distributor(bot))

    print("Бот успешно запущен в фоновом режиме!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    migrate_daily()
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
        user = db_exec_sync(
            "SELECT diamond, krw, battlecoin FROM users WHERE id = ?",
            (user_id,), fetch=True
        )
        if not user:
            return {"diamond": 0, "krw": 0, "battlecoin": 0, "is_premium": False,
                    "owned_cards": [], "daily_day": 0, "can_claim_daily": False}

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

        return {
            "diamond": user[0],
            "krw": user[1],
            "battlecoin": user[2],
            "is_premium": is_prem,
            "owned_cards": owned_cards,
            "daily_day": daily_day,
            "can_claim_daily": can_claim_daily
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in get_profile: {e}")
        return {"diamond": 0, "krw": 0, "battlecoin": 0, "is_premium": False,
                "owned_cards": [], "daily_day": 0, "can_claim_daily": False}


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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)