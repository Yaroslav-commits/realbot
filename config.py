import os

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [6378471773]
DB_PATH = os.path.join("data", "lookism_bot.db")

GET_COOLDOWN_HOURS = 3
BATTLE_COOLDOWN_HOURS = 1.5

MAIN_PRIZE_NORMAL_TITLE = "title_pass_hero"   # Ключ титула
MAIN_PRIZE_ROYALE_CARD = "exclusive_card_1"   # Ключ эксклюзивной карты
