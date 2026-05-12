import asyncio
import logging

from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.filters import CommandStart
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from database.db import init_db
from handlers import router

# Важно: импорты модулей ниже — чтобы зарегистрировать хендлеры в router
from handlers import user as _user        # noqa: F401
from handlers import deck as _deck        # noqa: F401
from handlers import battle as _battle    # noqa: F401
from handlers.pass_shop import shop as _shop  # noqa: F401
from handlers.user import cooldown_notification_scheduler, battle_cooldown_notification_scheduler

# Импортируем токен из твоего файла config.py
from config import BOT_TOKEN

# Вставь сюда ссылку на сайт (с Шага 3)
WEBAPP_URL = "https://yaroslav-commits.github.io/cards-catalog-manhw/"

async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🃏 Открыть каталог", web_app=WebAppInfo(url=WEBAPP_URL))]
            ],
            resize_keyboard=True
        )
        await message.answer("Привет! Нажми на кнопку ниже, чтобы открыть каталог карт:", reply_markup=kb)

    # Удаляем зависшие вебхуки и старые апдейты
    await bot.delete_webhook(drop_pending_updates=True)

    # Запускаем фоновые планировщики уведомлений
    asyncio.create_task(cooldown_notification_scheduler(bot))
    asyncio.create_task(battle_cooldown_notification_scheduler(bot))

    print("Бот успешно запущен!")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
