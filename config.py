import os

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [6378471773]
DB_PATH = os.path.join("data", "lookism_bot.db")

GET_COOLDOWN_HOURS = 3
BATTLE_COOLDOWN_HOURS = 1

MAIN_PRIZE_NORMAL_TITLE = "title_pass_cap"   # Ключ титула
MAIN_PRIZE_ROYALE_CARD = "yoo_seol_ha"   # Ключ эксклюзивной карты

from aiogram.types import CallbackQuery

async def is_owner(cq: CallbackQuery, expected_id: int) -> bool:
    """
    Универсальная проверка владельца кнопки.
    Возвращает True, если нажал нужный игрок, и False, если чужой.
    """
    if cq.from_user.id != expected_id:
        await cq.answer("❌ Это не ваша кнопка!", show_alert=True)
        return False
    return True