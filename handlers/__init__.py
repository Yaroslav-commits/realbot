from aiogram import Router
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# ================== FSM И РОУТЕР ==================
router = Router()

class TradeState(StatesGroup):
    waiting_for_trade_id = State()
class SettingsState(StatesGroup):
    waiting_for_nick = State()
class PromoState(StatesGroup):
    waiting_for_promo_data = State()
MATCH_QUEUE = []
GAMES = {}
PENDING_TRADES = {}

def kb_main():
    kb = [
        [KeyboardButton(text="🎴 Получить карту"), KeyboardButton(text="⚔️ Поле битвы")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🛍 Магазин")],
        [KeyboardButton(text="🏞️ Пасс"), KeyboardButton(text="🧳 Мои карты")],
        [KeyboardButton(text="⛩️ Банды")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
