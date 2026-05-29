import asyncio
import logging
import os
import sqlite3
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from aiogram.types import WebAppInfo, MenuButtonWebApp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, DB_PATH
from database.db import init_db
from handlers import router

# Импорты модулей, чтобы зарегистрировать хендлеры в router
from handlers import user as _user  # noqa: F401
from handlers import deck as _deck  # noqa: F401
from handlers import battle as _battle  # noqa: F401
from handlers.pass_shop import shop as _shop  # noqa: F401
from handlers.user import cooldown_notification_scheduler, battle_cooldown_notification_scheduler
from handlers.battle import auto_top_distributor

# ==========================================
# 1. НАСТРОЙКА API-СЕРВЕРА ДЛЯ САЙТА (FastAPI)
# ==========================================
app = FastAPI()

# Разрешаем твоему сайту на GitHub Pages делать запросы к серверу Bothost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешает запросы со всех доменов (включая твой GitHub)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def db_exec_sync(query, params=(), fetch=False, fetchall=False):
    """Синхронная функция для чтения твоей базы lookism_bot.db внутри API"""
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(query, params)
        if fetchall:
            return c.fetchall()
        if fetch:
            return c.fetchone()
        conn.commit()


@app.get("/api/profile/{user_id}")
async def get_profile(user_id: int):
    # Берём балансы из таблицы users
    user = db_exec_sync(
        "SELECT diamond, krw, battlecoin FROM users WHERE id = ?",
        (user_id,), fetch=True
    )

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден в базе данных бота")

    # Берём список ID карт, которые есть у этого игрока
    cards_rows = db_exec_sync(
        "SELECT card_id FROM cards_inv WHERE user_id = ?",
        (user_id,), fetchall=True
    )
    owned_cards = [row[0] for row in cards_rows] if cards_rows else []

    return {
        "diamond": user[0],
        "krw": user[1],
        "battlecoin": user[2],
        "owned_cards": owned_cards
    }


# ==========================================
# 2. НАСТРОЙКА И ЗАПУСК БОТА (aiogram)
# ==========================================
async def start_bot():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    # Твоя ссылка на GitHub Pages
    WEBAPP_URL = "https://yaroslav-commits.github.io/cards-catalog-manhw/"

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="🃏 Каталог", web_app=WebAppInfo(url=WEBAPP_URL))
    )

    # Удаляем зависшие вебхуки и старые апдейты
    await bot.delete_webhook(drop_pending_updates=True)

    # Запускаем фоновые планировщики уведомлений
    asyncio.create_task(cooldown_notification_scheduler(bot))
    asyncio.create_task(battle_cooldown_notification_scheduler(bot))
    asyncio.create_task(auto_top_distributor(bot))

    print("Бот успешно запущен в фоновом режиме!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


# ==========================================
# 3. ОБЪЕДИНЕНИЕ ЗАПУСКА БОТА И СЕРВЕРА
# ==========================================
@app.on_event("startup")
async def on_startup():
    init_db()  # Инициализируем базу данных при старте сервера
    asyncio.create_task(start_bot())  # Запускаем aiogram-бота как фоновую задачу


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Получаем порт от Bothost через переменные окружения, либо ставим 8080 по умолчанию
    port = int(os.environ.get("PORT", 8080))

    # Запускаем веб-сервер uvicorn, который будет держать наш FastAPI
    uvicorn.run(app, host="0.0.0.0", port=port)