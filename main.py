import asyncio
import logging
import os
import sqlite3
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from aiogram.types import WebAppInfo, MenuButtonWebApp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, DB_PATH
# Импорт функций для выдачи карт
from database.db import init_db, is_premium, pull_random_card, give_card_to_user
from handlers import router

# 👇 ИСПРАВЛЕННЫЙ БЛОК ИМПОРТОВ (БОЛЬШЕ НЕ БУДЕТ ПАДАТЬ)
from handlers import user as _user  # noqa: F401
from handlers import deck as _deck  # noqa: F401
from handlers import battle as _battle  # noqa: F401
from handlers.pass_shop import shop as _shop  # noqa: F401
from handlers.user import cooldown_notification_scheduler, battle_cooldown_notification_scheduler
from handlers.battle import auto_top_distributor


# Функция для БД (с защитой от зависания)
def db_exec_sync(query, params=(), fetch=False, fetchall=False):
    with sqlite3.connect(DB_PATH, timeout=3.0) as conn:
        c = conn.cursor()
        c.execute(query, params)
        if fetchall:
            return c.fetchall()
        if fetch:
            return c.fetchone()
        conn.commit()


# Автоматическое создание колонок для Ежедневного бонуса
def migrate_daily():
    try:
        db_exec_sync("ALTER TABLE users ADD COLUMN daily_day INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        db_exec_sync("ALTER TABLE users ADD COLUMN last_daily_claim TEXT DEFAULT '2000-01-01'")
    except Exception:
        pass


# ==========================================
# ЗАПУСК БОТА (aiogram)
# ==========================================
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


# ==========================================
# НОВЫЙ МЕТОД ЗАПУСКА СЕРВЕРА (Lifespan)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    migrate_daily()
    bot_task = asyncio.create_task(start_bot())
    yield
    bot_task.cancel()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def healthcheck():
    return {"status": "ok", "message": "ManhwCard Server is running perfectly"}


# ==========================================
# API ЕЖЕДНЕВНЫХ НАГРАД И ПРОФИЛЯ
# ==========================================

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
async def get_profile(user_id: int):
    user = db_exec_sync(
        "SELECT diamond, krw, battlecoin, daily_day, last_daily_claim FROM users WHERE id = ?",
        (user_id,), fetch=True
    )
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    is_prem = is_premium(user_id)
    cards_rows = db_exec_sync("SELECT card_id FROM cards_inv WHERE user_id = ?", (user_id,), fetchall=True)
    owned_cards = [row[0] for row in cards_rows] if cards_rows else []

    now_msk = datetime.now(timezone(timedelta(hours=3)))
    today_str = now_msk.strftime("%Y-%m-%d")
    last_claim_date = user[4].split(" ")[0] if user[4] else '2000-01-01'
    can_claim_daily = (last_claim_date != today_str)

    return {
        "diamond": user[0],
        "krw": user[1],
        "battlecoin": user[2],
        "is_premium": is_prem,
        "owned_cards": owned_cards,
        "daily_day": user[3] if user[3] else 0,
        "can_claim_daily": can_claim_daily
    }


@app.post("/api/claim_daily/{user_id}")
async def claim_daily(user_id: int):
    user = db_exec_sync("SELECT daily_day, last_daily_claim FROM users WHERE id = ?", (user_id,), fetch=True)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    now_msk = datetime.now(timezone(timedelta(hours=3)))
    today_str = now_msk.strftime("%Y-%m-%d")
    last_claim_date = user[1].split(" ")[0] if user[1] else '2000-01-01'

    if last_claim_date == today_str:
        return {"success": False, "error": "Награда уже получена сегодня!"}

    current_day = (user[0] or 0) + 1
    if current_day > 30:
        current_day = 1

    reward = DAILY_REWARDS.get(current_day, {'krw': 200})

    if 'krw' in reward:
        db_exec_sync("UPDATE users SET krw = krw + ? WHERE id = ?", (reward['krw'], user_id))
    if 'dia' in reward:
        db_exec_sync("UPDATE users SET diamond = diamond + ? WHERE id = ?", (reward['dia'], user_id))

    card_key = None
    if 'pack' in reward:
        pack_type = reward['pack']
        rarity = "Мифическая 🔴" if pack_type == 'mythic' else "Легендарная 🔵"
        card_key = pull_random_card(force_rarity=rarity)
        if card_key:
            give_card_to_user(user_id, card_key)

    db_exec_sync("UPDATE users SET daily_day = ?, last_daily_claim = ? WHERE id = ?",
                 (current_day, today_str, user_id))

    new_user = db_exec_sync("SELECT diamond, krw FROM users WHERE id = ?", (user_id,), fetch=True)

    return {
        "success": True,
        "new_krw": new_user[1],
        "new_dia": new_user[0],
        "card_key": card_key
    }


@app.get("/api/card_count/{card_id}")
async def get_card_count(card_id: str):
    res = db_exec_sync("SELECT COUNT(*) FROM cards_inv WHERE card_id = ?", (card_id,), fetch=True)
    count = res[0] if res else 0
    return {"card_id": card_id, "count": count}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)