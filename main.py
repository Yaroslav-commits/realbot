
import asyncio
import logging
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
import uvicorn

from aiogram.types import WebAppInfo, MenuButtonWebApp, BotCommand
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN, DB_PATH
from database.db import init_db, get_user, get_rank
from handlers import router

# FastAPI Setup
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Запуск бота в фоне при старте FastAPI
    asyncio.create_task(run_bot())
    yield

app = FastAPI(lifespan=lifespan)

# Раздача статики
app.mount("/static", StaticFiles(directory="webapp/static"), name="static")
app.mount("/cards", StaticFiles(directory="images/cards"), name="cards")
templates = Jinja2Templates(directory="webapp/templates")

# --- API Endpoints ---

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/cards")
async def get_api_cards():
    if os.path.exists("cards.json"):
        with open("cards.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return []

@app.get("/api/profile/{uid}")
async def get_api_profile(uid: int):
    user_data = get_user(uid)
    if not user_data:
        return {"error": "User not found"}

    # Индексы из твоей БД: 3-diamond, 4-krw, 5-bc, 7-rank_points, 8-wins
    return {
        "id": user_data[0],
        "username": user_data[1],
        "nickname": user_data[2],
        "diamond": user_data[3],
        "krw": user_data[4],
        "battlecoin": user_data[5],
        "rank": get_rank(user_data[7]),
        "wins": user_data[8]
    }

# --- Aiogram Setup ---

async def run_bot():
    from handlers.user import cooldown_notification_scheduler, battle_cooldown_notification_scheduler
    from handlers.battle import auto_top_distributor

    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    # Здесь укажи URL твоего сервера (хостинга)
    WEBAPP_URL = "http://твой_домен_или_ip:3000" 

    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="🃏 Каталог", web_app=WebAppInfo(url=WEBAPP_URL))
    )

    await bot.delete_webhook(drop_pending_updates=True)

    # Список команд, который показывается при вводе "/"
    await bot.set_my_commands([
        BotCommand(command="start",   description="Перезапуск бота"),
        BotCommand(command="get",     description="Получить карту"),
        BotCommand(command="profile", description="Открыть профиль"),
        BotCommand(command="card",    description="Информация о картах"),
        BotCommand(command="fon",     description="Информация о фонах"),
        BotCommand(command="premium", description="Возможности Premium"),
    ])

    # Фоновые задачи
    asyncio.create_task(cooldown_notification_scheduler(bot))
    asyncio.create_task(battle_cooldown_notification_scheduler(bot))
    asyncio.create_task(auto_top_distributor(bot))

    print("Бот успешно запущен внутри FastAPI!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Запуск сервера на порту 3000
    uvicorn.run(app, host="0.0.0.0", port=3000)
