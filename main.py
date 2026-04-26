import asyncio
import logging

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


async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
