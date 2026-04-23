import os
import asyncio
import logging
import sqlite3
import random
import calendar
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types, Router
from aiogram.types import (ReplyKeyboardMarkup, KeyboardButton,
                           InlineKeyboardMarkup, InlineKeyboardButton,
                           CallbackQuery, LabeledPrice, PreCheckoutQuery)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [6378471773]
DB_PATH = os.path.join("data", "lookism_bot.db")

GET_COOLDOWN_HOURS = 3
BATTLE_COOLDOWN_HOURS = 1.5

MAIN_PRIZE_NORMAL_TITLE = "title_pass_hero" # Ключ титула
MAIN_PRIZE_ROYALE_CARD = "exclusive_card_1" # Ключ эксклюзивной карты
# ================== БАЗА ДАННЫХ ==================
CARDS = {
    "daniel_fat": {"name": "Толстый Даниэль", "rarity": "Обычная ⚪️", "style": "Универсал 💠", "speed": 13, "strength": 30, "intellect": 16, "file_id": "AgACAgIAAxkBAAFHkiRp5PC_zGf8JQKire2HNasTTISaDQAChRZrG_QMKEuNvVy9BpKorwEAAwIAA3kAAzsE", "exclusive": False},
    "gun_park": {"name": "Пак Чонгон", "rarity": "Мифическая 🔴", "style": "Каратэ", "speed": 97, "strength": 100, "intellect": 97, "file_id": "AgACAgIAAxkBAAIPhmnn449dJIpuvnB_RINQRehRRPV-AAL0H2sbFc5BSxYlHH2qhUy3AQADAgADdwADOwQ", "exclusive": False},
    "ha": {"name": "Ха Ныль", "rarity": "Обычная ⚪️", "style": "Отсутсвует", "speed": 11, "strength": 11, "intellect": 11, "file_id": "AgACAgIAAxkBAAFHk-Vp5QOMPeGE0TNI3gVAoh3C_X21mAACPBdrG_QMKEuIszmorC592gEAAwIAA3cAAzsE", "exclusive": False},
    "yena": {"name": "Йена", "rarity": "Обычная ⚪️", "style": "Отсутсвует", "speed": 10, "strength": 10, "intellect": 10, "file_id": "AgACAgIAAxkBAAFHk9hp5QNU5eewMS8ePhlnFNAn2CqhKwACOhdrG_QMKEuOrQy5VZI4qAEAAwIAA3cAAzsE", "exclusive": False},
    "doch": {"name": "Дочь Чансу", "rarity": "Обычная ⚪️", "style": "Отсутсвует", "speed": 10, "strength": 10, "intellect": 10, "file_id": "AgACAgIAAxkBAAFHk-dp5QONq9oMhMyFikq1JTcYulv-5QACPRdrG_QMKEsC2Amf8r_NfAEAAwIAA3cAAzsE", "exclusive": False},
    "yujin": {"name": "Юджин", "rarity": "Обычная ⚪️", "style": "Отсутсвует", "speed": 12, "strength": 10, "intellect": 20, "file_id": "AgACAgIAAxkBAAFHlCZp5QYC80T4Bj6jltacuheo1lE5kwACUBdrG_QMKEtoLvqGMc2dbgEAAwIAA3cAAzsE", "exclusive": False},
    "seo_soneun": {"name": "Со Сонын", "rarity": "Эпическая 🟢", "style": "Уличный", "speed": 80, "strength": 80, "intellect": 79, "file_id": "AgACAgIAAxkBAAFHlkRp5SWDI9zilzQhNf1QQFHSfj-ZbwACDBhrG_QMKEsSJ77u_oyFyQEAAwIAA3kAAzsE", "exclusive": False},
    "kim_kimyeon": {"name": "Ким Кимён", "rarity": "Эпическая 🟢", "style": "ММА", "speed": 80, "strength": 80, "intellect": 80, "file_id": "AgACAgIAAxkBAAFHllhp5SY1eOk6pn6KVGScUqxx2xwtAgACEBhrG_QMKEtRqjVWG4OQmwEAAwIAA3kAAzsE", "exclusive": False},
    "kwak_jichan": {"name": "Квак Джичан", "rarity": "Эпическая 🟢", "style": "Лезвия рук", "speed": 80, "strength": 79, "intellect": 80, "file_id": "AgACAgIAAxkBAAFHll5p5SZ_XL-LQqbtzwqiU2ko5VO-XwACExhrG_QMKEtC2GWTvWF7XAEAAwIAA3kAAzsE", "exclusive": False},
    "ma_tesu": {"name": "Ма Тэсу", "rarity": "Эпическая 🟢", "style": "Железный кулак", "speed": 79, "strength": 80, "intellect": 78, "file_id": "AgACAgIAAxkBAAFHln1p5Sd2NlZurVjq-9DMiKO8-VqgEwACFhhrG_QMKEtXVKwzsojjwwEAAwIAA3kAAzsE", "exclusive": False},
    "ji_gonsop": {"name": "Джи Гонсоп", "rarity": "Эпическая 🟢", "style": "Бокс", "speed": 79, "strength": 80, "intellect": 78, "file_id": "AgACAgIAAxkBAAFHln9p5See1_nNFUB8JgdvnyA8NeiTYwACFxhrG_QMKEs5k39hXX1w1AEAAwIAA3kAAzsE", "exclusive": False},
    "lee_jinson": {"name": "Ли Джинсон", "rarity": "Эпическая 🟢", "style": "Бокс", "speed": 80, "strength": 80, "intellect": 76, "file_id": "AgACAgIAAxkBAAFHlotp5Se8_QrK1WTF3Q2rGJjK5-ldRgACGBhrG_QMKEuTGUSdJ8GuqAEAAwIAA3kAAzsE", "exclusive": False},
    "wang_mugak": {"name": "Ванг Мугак", "rarity": "Эпическая 🟢", "style": "Уличный", "speed": 78, "strength": 79, "intellect": 79, "file_id": "AgACAgIAAxkBAAFHlplp5Sgi4Be3J8j4OfVrMow2ok8_7wACHBhrG_QMKEvMpAUngXd2GwEAAwIAA3kAAzsE", "exclusive": False},
    "ryu_jaun": {"name": "Рю Джаун", "rarity": "Эпическая 🟢", "style": "Канабо", "speed": 78, "strength": 78, "intellect": 79, "file_id": "AgACAgIAAxkBAAFHlqFp5ShF9jAk_1TQA3KusVeD_qhojAACHRhrG_QMKEuufZDtXa7HPgEAAwIAA3kAAzsE", "exclusive": False},
    "odalyan": {"name": "Одалян", "rarity": "Эпическая 🟢", "style": "Уличный (Гапрена)", "speed": 79, "strength": 79, "intellect": 75, "file_id": "AgACAgIAAxkBAAFHlqlp5Shvqa4Yg5RmB5lqoPdHc1mVmgACHxhrG_QMKEs3gLmREKMYLgEAAwIAA3kAAzsE", "exclusive": False},
    "an_hyeonson": {"name": "Ан Хёнсон", "rarity": "Эпическая 🟢", "style": "Железный кулак", "speed": 60, "strength": 62, "intellect": 60, "file_id": "AgACAgIAAxkBAAFHlrJp5SiLmUjUA_5I36TretJfeqGxGgACIBhrG_QMKEtNykEsRzYnFQEAAwIAA3kAAzsE", "exclusive": False},
    "hong_jayeol": {"name": "Хон Джаёль", "rarity": "Эпическая 🟢", "style": "Система", "speed": 61, "strength": 60, "intellect": 61, "file_id": "AgACAgIAAxkBAAFHlrpp5SiuzEqduaEz6sPGi-yL5BDkKgACIRhrG_QMKEvSXT3kWntIqwEAAwIAA3kAAzsE", "exclusive": False},
    "kim_gapryeon": {"name": "Ким Гапрён", "rarity": "Мифическая 🔴", "style": "Уличный (Гапрена)", "speed": 98, "strength": 100, "intellect": 98, "file_id": "AgACAgIAAxkBAAINwWnnlKlMLq4b8d4JaUgThCdcjcQjAAJiF2sbOodBS7LIeJnGnbgBAQADAgADdwADOwQ", "exclusive": False},

    "shingen_yamazaki": {"name": "Шинген Ямадзаки", "rarity": "Мифическая 🔴", "style": "Каратэ", "speed": 97, "strength": 99, "intellect": 95, "file_id": "AgACAgIAAxkBAAINw2nnlWY3lG2-aYCuhnqb6cdU959aAAJrF2sbOodBS8XziYdeisaDAQADAgADdwADOwQ", "exclusive": False},

    "yohan_son": {"name": "Йохан Сон", "rarity": "Легендарная 🔵", "style": "Универсал (копирование)", "speed": 89, "strength": 87, "intellect": 86, "file_id": "AgACAgIAAxkBAAINxWnnlsynk4tzfYhTT3uiz_z_bqkiAAJxF2sbOodBS5B26w0oE4cRAQADAgADdwADOwQ", "exclusive": False},

    "jinran": {"name": "Джинран", "rarity": "Легендарная 🔵", "style": "Уличный (Волк)", "speed": 85, "strength": 86, "intellect": 88, "file_id": "AgACAgIAAxkBAAINx2nnltXqdUoT6bnRUbEy9o78XIidAAJyF2sbOodBSyag0ewCztpxAQADAgADdwADOwQ", "exclusive": False},

    "yuk_sonji": {"name": "Юк Сонджи", "rarity": "Легендарная 🔵", "style": "Дзюдо", "speed": 86, "strength": 84, "intellect": 85, "file_id": "AgACAgIAAxkBAAINyWnnltqo6ojwdFLtTOnBK6fhlvKpAAJzF2sbOodBS7r6GSoswOcLAQADAgADdwADOwQ", "exclusive": False},

    "baek_san": {"name": "Бэк Сан", "rarity": "Эпическая 🟢", "style": "Уличный", "speed": 78, "strength": 78, "intellect": 78, "file_id": "AgACAgIAAxkBAAINzWnnnGUj06rDka0jwzgrmIbS73OiAAKQF2sbOodBS62S1syMsFVzAQADAgADdwADOwQ", "exclusive": False},

    "magami_kenta": {"name": "Магами Кента", "rarity": "Редкая 🟡", "style": "Каратэ", "speed": 59, "strength": 59, "intellect": 59, "file_id": "AgACAgIAAxkBAAINz2nnnG6iV9jh7PnoMt9WG-CiMsCOAAKSF2sbOodBS3SG2zLwZIlYAQADAgADdwADOwQ", "exclusive": False},

    "choi_minsik": {"name": "Чой Минсик", "rarity": "Редкая 🟡", "style": "CQC", "speed": 58, "strength": 59, "intellect": 59, "file_id": "AgACAgIAAxkBAAIKT2nlKrW_KDTUz9kp6QySknP-AAEJrAACCBhrG_QMKEvN7YBV6h5FWQEAAwIAA3cAAzsE", "exclusive": False},

    "kazuma_sato": {"name": "Казума Сато", "rarity": "Редкая 🟡", "style": "Сумо", "speed": 58, "strength": 59, "intellect": 58, "file_id": "AgACAgIAAxkBAAIN02nnnHx58M6F9KFZ5kPaDLFreNkGAAKTF2sbOodBSwqwf5nVZDjxAQADAgADdwADOwQ", "exclusive": False},

    "akira": {"name": "Акира", "rarity": "Редкая 🟡", "style": "Уличный ✊", "speed": 59, "strength": 58, "intellect": 50, "file_id": "AgACAgIAAxkBAAIN1WnnnIX8eJL8nZLzrTebU3DJF6UFAAKVF2sbOodBSxu3aYikCSZNAQADAgADdwADOwQ", "exclusive": False},

    "hyeon_sejin": {"name": "Хён Седжин", "rarity": "Редкая 🟡", "style": "Звериный", "speed": 54, "strength": 53, "intellect": 58, "file_id": "AgACAgIAAxkBAAIN22nnnKCRvHXqWCLa_YlbnD9nlzICAAKZF2sbOodBS37gFP-PlgzvAQADAgADdwADOwQ", "exclusive": False},

    "shin_arim": {"name": "Шин Арим", "rarity": "Редкая 🟡", "style": "Бокс 🥊", "speed": 55, "strength": 54, "intellect": 54, "file_id": "AgACAgIAAxkBAAIN3WnnnKqPglnD-TRKWYObsFiSoNjoAAKbF2sbOodBS7ptQZ8HlsOCAQADAgADdwADOwQ", "exclusive": False},

    "min_jinhun": {"name": "Мин Джинхун", "rarity": "Редкая 🟡", "style": "Бокс 🥊", "speed": 57, "strength": 52, "intellect": 54, "file_id": "AgACAgIAAxkBAAIN12nnnI0LW7LZiC1mhBuUvA41qRufAAKWF2sbOodBS-0DLa70AaVyAQADAgADdwADOwQ", "exclusive": False},

    "kwak_jihan": {"name": "Квак Джихан", "rarity": "Редкая 🟡", "style": "Лезвия рук", "speed": 55, "strength": 51, "intellect": 55, "file_id": "AgACAgIAAxkBAAIN2WnnnJgwqcdDp0Lj1XfaFb0WeFQlAAKXF2sbOodBS_GS2CUdBdxzAQADAgADdwADOwQ", "exclusive": False},

    "kwak_jibom": {"name": "Квак Джибом", "rarity": "Редкая 🟡", "style": "Борьба 🤼", "speed": 52, "strength": 56, "intellect": 54, "file_id": "AgACAgIAAxkBAAIN32nnnLea-aG7EUTyLV_z733SlxODAAKcF2sbOodBS3jygVdzyGc7AQADAgADdwADOwQ", "exclusive": False},

    "watanabe_kokuin": {"name": "Ватанабэ Кокуин", "rarity": "Редкая 🟡", "style": "Каратэ 🥋", "speed": 51, "strength": 49, "intellect": 52, "file_id": "AgACAgIAAxkBAAIN4WnnnMmFAwsD_Dx2w9vNlQc2DpUiAAKdF2sbOodBS8Kyv01oLqJ3AQADAgADdwADOwQ", "exclusive": False},
    "takeshi_matsumoto": {"name": "Такеши Матсумото", "rarity": "Редкая 🟡", "style": "Каратэ 🥋", "speed": 48, "strength": 47, "intellect": 51, "file_id": "AgACAgIAAxkBAAIN42nnnNIPdUNjtQPsbjYfJFqGP4fHAAKeF2sbOodBS_vd6KTqgUWbAQADAgADdwADOwQ", "exclusive": False},

    "doctor_cho": {"name": "Доктор Чжо", "rarity": "Редкая 🟡", "style": "Ядовитый", "speed": 37, "strength": 38, "intellect": 37, "file_id": "AgACAgIAAxkBAAIN52nnnODmr24xg33WOSvNMShSYkMiAAKgF2sbOodBS6j016gvR7BbAQADAgADdwADOwQ", "exclusive": False},

     "haye_ul": {"name": "Ха Е Уль", "rarity": "Редкая 🟡", "style": "Тхэквондо", "speed": 36, "strength": 37, "intellect": 36, "file_id": "AgACAgIAAxkBAAIN6WnnnOjrraBC9_2lN2i1QovX6hfaAAKhF2sbOodBS_UQsUBxiQXeAQADAgADdwADOwQ", "exclusive": False},

"wang_junsok": {"name": "Ван Джунсок", "rarity": "Эпическая 🟢", "style": "Уличный", "speed": 78, "strength": 79, "intellect": 76, "file_id": "AgACAgIAAxkBAAICQWnpUDWRQ4chtEgRaPTvHnI7DUsQAALyFWsbYxlIS2d7MoeIt9vhAQADAgADdwADOwQ", "exclusive": False},

"jang_hyeon": {"name": "Чан Хён", "rarity": "Эпическая 🟢", "style": "Айкидо (Звериный)", "speed": 79, "strength": 78, "intellect": 75, "file_id": "AgACAgIAAxkBAAICQ2npUD6obP19H8Qy6N8LxsvWvAKRAALzFWsbYxlIS4cnyDD-MLElAQADAgADdwADOwQ", "exclusive": False},

"baek_jin_hyeok": {"name": "Бэк Джин Хёк", "rarity": "Эпическая 🟢", "style": "Уличный (Звериный)", "speed": 78, "strength": 77, "intellect": 76, "file_id": "AgACAgIAAxkBAAICRWnpUEU_k2L0-78JM1YPLq2cxWkxAAL0FWsbYxlIS-5bRfpV65CiAQADAgADdwADOwQ", "exclusive": False},

"son_hashik": {"name": "Сон Хашик", "rarity": "Эпическая 🟢", "style": "Бокс", "speed": 77, "strength": 77, "intellect": 77, "file_id": "AgACAgIAAxkBAAICR2npUEzlqPVBRzE8v67mj5Db0jleAAL1FWsbYxlISzgULkRM0BJ0AQADAgADdwADOwQ", "exclusive": False},

"do_jaewan": {"name": "До Джэван", "rarity": "Эпическая 🟢", "style": "Бокс", "speed": 77, "strength": 78, "intellect": 75, "file_id": "AgACAgIAAxkBAAICSWnpUFGPFh4Yv4x4g7Fvfoh3VYHSAAL2FWsbYxlIS5hRDQzKmVGHAQADAgADdwADOwQ", "exclusive": False},

"yuseong": {"name": "Юсон", "rarity": "Эпическая 🟢", "style": "Капоейра", "speed": 78, "strength": 75, "intellect": 76, "file_id": "AgACAgIAAxkBAAICS2npUFYPQHKKvexJheW3Tep3WbJVAAL3FWsbYxlIS52dXJ73I8c6AQADAgADdwADOwQ", "exclusive": False},

"ban_mandeok": {"name": "Бан Мандок", "rarity": "Эпическая 🟢", "style": "Капоейра", "speed": 75, "strength": 78, "intellect": 75, "file_id": "AgACAgIAAxkBAAICTWnpUFpCKZJl0H-_M7B5A01L6PHGAAL4FWsbYxlIS_lsvXxhVm_gAQADAgADdwADOwQ", "exclusive": False},

"kuroda_ryuhei": {"name": "Курода Рюхей", "rarity": "Эпическая 🟢", "style": "Кендо", "speed": 77, "strength": 75, "intellect": 74, "file_id": "AgACAgIAAxkBAAICT2npUGLpbbf1G0XDvXhoJBYsNIMEAAL5FWsbYxlIS285zI7tNfmyAQADAgADdwADOwQ", "exclusive": False},

"han_sinu": {"name": "Хан Сину", "rarity": "Эпическая 🟢", "style": "Уличный", "speed": 80, "strength": 75, "intellect": 72, "file_id": "AgACAgIAAxkBAAICZWnpUQAB2RKiaDt2HiRJciXHmgMrUQACBRZrG2MZSEs6UNzBo8bIKgEAAwIAA3cAAzsE", "exclusive": False},

"cheon_taejin": {"name": "Чхон Тэджин", "rarity": "Эпическая 🟢", "style": "Кудо", "speed": 73, "strength": 79, "intellect": 73, "file_id": "AgACAgIAAxkBAAICUWnpUGejRoaYw8E0rrOO7tKEeHgbAAL6FWsbYxlISwiVWbRWebFOAQADAgADdwADOwQ", "exclusive": False},

"wang_seokdu": {"name": "Ван Сокду", "rarity": "Эпическая 🟢", "style": "Удары Головы", "speed": 73, "strength": 78, "intellect": 69, "file_id": "AgACAgIAAxkBAAICU2npUG6mEzFmbtxN_BKT4Tt389sWAAL7FWsbYxlIS5pkifWTtfFmAQADAgADdwADOwQ", "exclusive": False},

"chae_wonseok": {"name": "Чэ Вонсок", "rarity": "Эпическая 🟢", "style": "CQC", "speed": 75, "strength": 74, "intellect": 70, "file_id": "AgACAgIAAxkBAAICVWnpUHE8GZlxKyU7OhcQ-kHmIvEqAAL8FWsbYxlISzC_FkdRZu9AAQADAgADdwADOwQ", "exclusive": False},

"kwon_jitae": {"name": "Квон Джитэ", "rarity": "Эпическая 🟢", "style": "Бокс", "speed": 71, "strength": 72, "intellect": 73, "file_id": "AgACAgIAAxkBAAICV2npUIGhZN9EWz3XqbQRD1Zy5o6NAAL9FWsbYxlIS2lNTB9L--oVAQADAgADdwADOwQ", "exclusive": False},

"vasco": {"name": "Васко", "rarity": "Эпическая 🟢", "style": "Муайтай", "speed": 70, "strength": 78, "intellect": 65, "file_id": "AgACAgIAAxkBAAICWWnpUIgnSAWu10OpUa83t_OsVKk-AAL-FWsbYxlISwoS_m5smTrmAQADAgADdwADOwQ", "exclusive": False},

"jin_hobin": {"name": "Джин Хобин", "rarity": "Эпическая 🟢", "style": "Дзюдо", "speed": 77, "strength": 69, "intellect": 66, "file_id": "AgACAgIAAxkBAAICW2npUJERdiYGjrI7_dyE3TyvXIJZAAMWaxtjGUhLdo1VSA01TdQBAAMCAAN3AAM7BA", "exclusive": False},

"shigeaki_kojima": {"name": "Шигеаки Кодзима", "rarity": "Эпическая 🟢", "style": "Каратэ", "speed": 65, "strength": 67, "intellect": 79, "file_id": "AgACAgIAAxkBAAICXWnpUJdQ-S0cuq-V4C1jPzz11j_vAAIBFmsbYxlIS2Ch6ft8-sc5AQADAgADdwADOwQ", "exclusive": False},

"hiroaki_kojima": {"name": "Хироаки Кодзима", "rarity": "Эпическая 🟢", "style": "Каратэ", "speed": 68, "strength": 65, "intellect": 78, "file_id": "AgACAgIAAxkBAAICX2npUJ17bu0rgkvxm7c0p7k3d0WKAAICFmsbYxlIS1uXxwnUfwQbAQADAgADdwADOwQ", "exclusive": False},

"lineman": {"name": "Лайнмен", "rarity": "Эпическая 🟢", "style": "Молния Чоя", "speed": 75, "strength": 70, "intellect": 69, "file_id": "AgACAgIAAxkBAAICYWnpUKZCkz6AwAftTxGKol0KLZPYAAIDFmsbYxlIS44dpNs4BUzHAQADAgADdwADOwQ", "exclusive": False},

"xiao_long": {"name": "Сяо Лун", "rarity": "Эпическая 🟢", "style": "Чхон Хохуйгун", "speed": 74, "strength": 68, "intellect": 70, "file_id": "AgACAgIAAxkBAAICY2npUKs-KQioBd4ZJzEQHoUrpFTSAAIEFmsbYxlIS1y6iuu4RErQAQADAgADdwADOwQ", "exclusive": False},

"masashi_takanobu": {"name": "Масаши Таканобу", "rarity": "Редкая 🟡", "style": "Уличный ✊", "speed": 48, "strength": 50, "intellect": 49, "file_id": "AgACAgIAAxkBAAIN5WnnnNe_ccvJ002YoNX_sXjrmkQKAAKfF2sbOodBSw3r3xL82M1VAQADAgADdwADOwQ", "exclusive": False},

}

RARITIES = {
    "Обычная ⚪️": {"chance": 55.0, "dup": 1},
    "Редкая 🟡": {"chance": 29.0, "dup": 2},
    "Эпическая 🟢": {"chance": 14.47, "dup": (4, 5)},
    "Легендарная 🔵": {"chance": 1.2, "dup": (8, 11)},
    "Мифическая 🔴": {"chance": 0.3, "dup": (15, 20)},
    "Божественная ⚫️": {"chance": 0.03, "dup": (50, 100)}
}

BGS = {
    "default": {"name": "Стандартный", "file_id": "AgACAgIAAxkBAAFHkflp5O8qYvKQnm5R8Nylqe0KM15SLgACoRNrG6trIUsNqPCQiCAlRgEAAwIAA3cAAzsE", "price": 0},
    "zero": {"name": "Нулевое Поколение", "file_id": "AAMCAgADGQEAAUeTx2nlAskfpVPNWk6gZFNE-yH3-cMTAAIolgAC9AwoS_gxR6WaCm79AQAHbQADOwQ","price": 3000},
    "lookism_1": {"name": "Лукизм", "file_id": "AgACAgIAAxkBAAFHkldp5PKPLG3TCvsedNSdNZWpAmyi8wACeRZrGz0vIUt6PZ3wUkrtlQEAAwIAA3cAAzsE", "price": 500}
}

TITLES = {
    "title_pass_hero": "Герой Месяца 🏆",
    "title_dev": "Создатель ⚜️"
}

NORMAL_PASS = {1:('krw',25), 2:('atm',5), 3:('bc',12), 4:('atm',2), 5:('pack','epic'), 6:('dia',5), 7:('krw',30), 8:('atm',3), 9:('krw',40), 10:('bc',10), 11:('atm',4), 12:('pack','epic'), 13:('krw',50), 14:('dia',2), 15:('krw',60), 16:('atm',5), 17:('bc',20), 18:('krw',70), 19:('pack','epic'), 20:('atm',3), 21:('krw',30), 22:('dia',3), 23:('krw',90), 24:('bc',10), 25:('pack','leg'), 26:('krw',100), 27:('atm',5), 28:('krw',25), 29:('dia',4), 30:('krw',50), 31:('atm',10)}
ROYALE_PASS = {1:('krw',80), 2:('atm',10), 3:('bc',25), 4:('atm',8), 5:('pack','epic'), 6:('dia',25), 7:('krw',100), 8:('atm',8), 9:('krw',120), 10:('bc',50), 11:('atm',10), 12:('pack','epic'), 13:('krw',150), 14:('dia',150), 15:('krw',180), 16:('atm',5), 17:('bc',60), 18:('krw',200), 19:('pack','epic'), 20:('atm',10), 21:('krw',230), 22:('dia',50), 23:('krw',260), 24:('bc',50), 25:('pack','leg'), 26:('krw',300), 27:('atm',12), 28:('krw',350), 29:('dia',200), 30:('krw',400), 31:('atm',20)}

def db_exec(query, params=(), fetch=False, fetchall=False):
    os.makedirs("data", exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute(query, params)
        if fetchall: return c.fetchall()
        if fetch: return c.fetchone()
        conn.commit()
def init_db():
    db_exec('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, username TEXT, nickname TEXT,
        diamond INTEGER DEFAULT 0, krw INTEGER DEFAULT 0, battlecoin INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
        rank_points INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        last_get TEXT DEFAULT '2000-01-01 00:00:00', last_battle TEXT DEFAULT '2000-01-01 00:00:00',
        active_bg TEXT DEFAULT 'default', active_title TEXT, join_date TEXT, royale_pass INTEGER DEFAULT 0
    )''')
    db_exec("CREATE TABLE IF NOT EXISTS cards_inv (user_id INTEGER, card_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS decks (user_id INTEGER, card_id TEXT, slot_index INTEGER)")
    db_exec("CREATE TABLE IF NOT EXISTS bgs_inv (user_id INTEGER, bg_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS titles_inv (user_id INTEGER, title_id TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS pass_claims (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
    db_exec("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, p_type TEXT, val TEXT, uses INTEGER)")

def get_user(uid):
    return db_exec("SELECT * FROM users WHERE id = ?", (uid,), fetch=True)

def add_user(uid, uname, fname):
    if not get_user(uid):
        db_exec("INSERT INTO users (id, username, nickname, join_date, active_bg) VALUES (?, ?, ?, ?, 'default')",
                (uid, uname, fname, datetime.now().strftime("%Y-%m-%d")))
        db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, 'default'))

# ================== ЛОГИКА ==================
def get_rank(pts):
    ranks = [(3000, "Безупречная мощь 😈"), (2000, "Сильнейший ☄️"), (1600, "Уровень нулевого 🧬"), (1000, "Уровень короля города 👑"),
             (600, "Уровень 1-го поколения 👾"), (300, "Уровень 2-го поколения 🪬"), (100, "Боец 🦸‍♂️"), (0, "Новичок 💩")]
    for p, n in ranks:
        if pts >= p: return n

def pull_random_card(force_rarity=None):
    if force_rarity:
        pool = [k for k, v in CARDS.items() if v['rarity'] == force_rarity and not v['exclusive']]
    else:
        roll = random.uniform(0, 100)
        cum = 0
        rolled_r = "Обычная ⚪️"
        for r, d in RARITIES.items():
            cum += d['chance']
            if roll <= cum:
                rolled_r = r
                break
        pool = [k for k, v in CARDS.items() if v['rarity'] == rolled_r and not v['exclusive']]
        if not pool: pool = [k for k, v in CARDS.items() if not v['exclusive']]
    return random.choice(pool) if pool else None

def give_card_to_user(uid, card_key):
    c = CARDS[card_key]
    has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (uid, card_key), fetch=True)
    if has_card:
        dup_val = RARITIES[c['rarity']]['dup']
        krw_earned = random.randint(dup_val[0], dup_val[1]) if isinstance(dup_val, tuple) else dup_val
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (krw_earned, uid))
        return False, krw_earned, c
    else:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (uid, card_key))
        return True, 0, c

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


# ================== HANDLERS ==================
@router.message(Command("start"))
async def start_cmd(msg: types.Message):
    add_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    await msg.answer("Добро пожаловать в Lookism Card! \nКанал бота: https://t.me/bradkofflood\nНаш чат:https://t.me/+as-Ypv7Kfjg3YTMy\n\nВыбирай действие и начни игру:", reply_markup=kb_main())


@router.message(F.photo)
async def get_photo_id(msg: types.Message):
    # Эта функция будет присылать тебе ID любой картинки, которую ты скинешь боту
    file_id = msg.photo[-1].file_id
    await msg.answer(f"Вот ID этой картинки:\n<code>{file_id}</code>")

@router.message(F.text == "⛩️ Банды")
async def gangs(msg: types.Message):
    await msg.answer("В разработке")


# ============ ГАЧА ============
@router.message(F.text == "🎴 Получить карту")
@router.message(Command("get"))
async def get_card_cmd(msg: types.Message):
    uid = msg.from_user.id
    u = get_user(uid)
    if not u: return

    attempts = u[6]
    last_get = datetime.strptime(u[11], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()

    if attempts > 0:
        db_exec("UPDATE users SET attempts = attempts - 1 WHERE id = ?", (uid,))
    else:
        if (now - last_get).total_seconds() < GET_COOLDOWN_HOURS * 3600:
            rem = int(GET_COOLDOWN_HOURS * 3600 - (now - last_get).total_seconds())
            await msg.answer(f"⏳ Следующая карта через {rem // 3600}ч {(rem % 3600) // 60}м.")
            return
        db_exec("UPDATE users SET last_get = ? WHERE id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), uid))

    card_key = pull_random_card()
    is_new, krw, c = give_card_to_user(uid, card_key)

    if is_new:
        txt = f"🃏 Получена новая боевая карта!\n\n🎴 Персонаж: {c['name']}\n🔮 Редкость: {c['rarity']}\n👊 Стиль боя: {c['style']}\n\n⚡️ Скорость: {c['speed']}\n💪 Сила: {c['strength']}\n🧠 Интеллект: {c['intellect']}"
    else:
        txt = f"🛑 Вам попалась повторная карта! Вы получаете {krw} 💴 KRW\n\n🎴 Персонаж: {c['name']}\n🔮 Редкость: {c['rarity']}\n👊 Стиль боя: {c['style']}\n\n⚡️ Скорость: {c['speed']}\n💪 Сила: {c['strength']}\n🧠 Интеллект: {c['intellect']}"

    await msg.answer_photo(photo=c['file_id'], caption=txt, has_spoiler=True)


# ============ ПРОФИЛЬ ============
@router.message(F.text == "👤 Профиль")
async def profile(msg: types.Message):
    uid = msg.from_user.id
    u = get_user(uid)

    # Если юзера нет в базе, добавляем его:
    if not u:
        add_user(uid, msg.from_user.username, msg.from_user.first_name)
        u = get_user(uid)

    pts = u[7]

    # Формирование строки с титулом
    if u[14] and u[14] in TITLES:
        title_str = f"🔱 Титул: {TITLES[u[14]]}\n\n"
    else:
        title_str = "\n"

    txt = (
        f"👤 Профиль {u[2]} 🧩\n"
        f"🆔 Id: <code>{u[0]}</code>\n"
        f"{title_str}"
        f"Баланс:\n"
        f"• 💎 Diamond - {u[3]}\n"
        f"• 💴 KRW - {u[4]}\n"
        f"• 🪙 BattleCoin - {u[5]}\n\n"
        f"Попытки:\n"
        f"• 💳 - {u[6]}\n\n"
        f"Ранг: {get_rank(pts)} ({pts}🏅)\n"
        f"Победы/ничьи/поражения\n"
        f"{u[8]}/{u[9]}/{u[10]}"
    )

    bld = InlineKeyboardBuilder()
    bld.button(text="🔱 Мои титулы", callback_data="my_titles")
    bld.button(text="🌄 Мои фоны", callback_data="my_bgs")
    bld.button(text="⚙️ Настройка", callback_data="settings")
    bld.adjust(1)

    # Получаем file_id фона (если нет, берем дефолтный из ТЗ)
    bg_id = BGS.get(u[13], BGS['default'])['file_id']
    if not bg_id or u[13] in [None, 'default']:
        bg_id = "AgACAgIAAxkBAAFHkflp5O8qYvKQnm5R8Nylqe0KM15SLgACoRNrG6trIUsNqPCQiCAlRgEAAwIAA3cAAzsE"

    try:
        await msg.answer_photo(photo=bg_id, caption=txt, reply_markup=bld.as_markup(), parse_mode="HTML")
    except Exception:
        await msg.answer(f"{txt}\n\n[Картинка не загрузилась.]", reply_markup=bld.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "settings")
async def settings_cq(cq: CallbackQuery):
    u = get_user(cq.from_user.id)
    await cq.message.answer(
        f"⚙️ Настройки\nДата регистрации: {u[15]}\nДля смены ника отправьте команду /nick [новый ник]"
    )
    await cq.answer()


@router.message(Command("nick"))
async def change_nick(msg: types.Message):
    new_nick = msg.text.replace("/nick", "").strip()
    if not new_nick:
        return await msg.answer("Использование: /nick НовыйНик")
    db_exec("UPDATE users SET nickname = ? WHERE id = ?", (new_nick, msg.from_user.id))
    await msg.answer(f"✅ Ник изменен на {new_nick}")


@router.callback_query(F.data.in_(["my_bgs", "my_titles"]))
async def bgs_titles_cq(cq: CallbackQuery):
    is_bg = cq.data == "my_bgs"
    table = "bgs_inv" if is_bg else "titles_inv"
    col = "bg_id" if is_bg else "title_id"

    items = db_exec(f"SELECT {col} FROM {table} WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    item_ids = [itm[0] for itm in items]

    # Добавляем стандартный фон в список фонов, чтобы его всегда можно было вернуть
    if is_bg and "default" not in item_ids:
        item_ids.insert(0, "default")

    if not item_ids:
        await cq.answer("У вас пока ничего нет!", show_alert=True)
        return

    bld = InlineKeyboardBuilder()
    for itm in item_ids:
        if is_bg:
            name = BGS.get(itm, {}).get('name', 'Неизвестный фон')
        else:
            name = TITLES.get(itm, 'Неизвестный титул')
        bld.button(text=name, callback_data=f"preview_{'bg' if is_bg else 'title'}:{itm}")

    bld.adjust(1)
    text_msg = "Выберите фон для просмотра:" if is_bg else "Выберите титул для просмотра:"
    await cq.message.answer(text_msg, reply_markup=bld.as_markup())
    await cq.answer()


@router.callback_query(F.data.startswith("preview_"))
async def preview_cq(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 2: return
    type_str, itm = parts[0].replace("preview_", ""), parts[1]

    u = get_user(cq.from_user.id)
    current_active = u[13] if type_str == "bg" else u[14]

    # Проверяем, установлен ли сейчас этот предмет
    is_active = (current_active == itm)
    if type_str == "bg" and itm == "default" and current_active in [None, 'default']:
        is_active = True

    btn_text = "✅ Установлено" if is_active else "☑️ Установить"
    bld = InlineKeyboardBuilder()
    bld.button(text=btn_text, callback_data=f"equip_{type_str}:{itm}")

    if type_str == "bg":
        bg_data = BGS.get(itm, BGS['default'])
        photo_id = bg_data.get('file_id',
                               "AgACAgIAAxkBAAFHkflp5O8qYvKQnm5R8Nylqe0KM15SLgACoRNrG6trIUsNqPCQiCAlRgEAAwIAA3cAAzsE")
        name = bg_data.get('name', 'Фон')
        await cq.message.answer_photo(photo=photo_id, caption=f"🌄 Предпросмотр фона: {name}",
                                      reply_markup=bld.as_markup())
    else:
        name = TITLES.get(itm, 'Титул')
        await cq.message.answer(f"🔱 Предпросмотр титула: {name}", reply_markup=bld.as_markup())

    await cq.answer()


@router.callback_query(F.data.startswith("equip_"))
async def equip_cq(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 2: return
    type_str, itm = parts[0].replace("equip_", ""), parts[1]

    u = get_user(cq.from_user.id)
    col = "active_bg" if type_str == "bg" else "active_title"
    current_active = u[13] if type_str == "bg" else u[14]

    is_active = (current_active == itm)
    if type_str == "bg" and itm == "default" and current_active in [None, 'default']:
        is_active = True

    if is_active:
        # Если уже установлено, снимаем предмет
        new_val = 'default' if type_str == "bg" else None
        db_exec(f"UPDATE users SET {col} = ? WHERE id = ?", (new_val, cq.from_user.id))
        btn_text = "☑️ Установить"
        alert_text = "Убрано из профиля!"
    else:
        # Устанавливаем новый предмет
        new_val = itm
        db_exec(f"UPDATE users SET {col} = ? WHERE id = ?", (new_val, cq.from_user.id))
        btn_text = "✅ Установлено"
        alert_text = "Успешно установлено!"

        # Меняем кнопку на лету
    bld = InlineKeyboardBuilder()
    bld.button(text=btn_text, callback_data=f"equip_{type_str}:{itm}")

    try:
        await cq.message.edit_reply_markup(reply_markup=bld.as_markup())
    except:
        pass  # Игнорируем ошибку, если статус кнопки не изменился

    await cq.answer(alert_text)


# ============ ИНВЕНТАРЬ И ТРЕЙД ============
RARITY_ORDER = {"Божественная ⚫️": 6, "Мифическая 🔴": 5, "Легендарная 🔵": 4, "Эпическая 🟢": 3, "Редкая 🟡": 2, "Обычная ⚪️": 1}

@router.message(F.text == "🧳 Мои карты")
async def my_cards(msg: types.Message):
    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (msg.from_user.id,), fetchall=True)
    if not cards: return await msg.answer("У вас пока нет карт.")

    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 Карты", callback_data="inv_view:0:all")
    bld.button(text="📊 Коллекция", callback_data="inv_collection")
    bld.adjust(2)

    await msg.answer("🧳 Ваш инвентарь карт. Выберите раздел:", reply_markup=bld.as_markup())

@router.callback_query(F.data == "inv_main")
async def inv_main_cb(cq: CallbackQuery):
    bld = InlineKeyboardBuilder()
    bld.button(text="🎴 Карты", callback_data="inv_view:0:all")
    bld.button(text="📊 Коллекция", callback_data="inv_collection")
    bld.adjust(2)
    try:
        await cq.message.edit_text("🧳 Ваш инвентарь карт. Выберите раздел:", reply_markup=bld.as_markup())
    except:
        await cq.message.delete()
        await cq.message.answer("🧳 Ваш инвентарь карт. Выберите раздел:", reply_markup=bld.as_markup())
    await cq.answer()

@router.callback_query(F.data.startswith("inv_view:"))
async def inv_view_paginated(cq: CallbackQuery):
    _, page_str, rarity_filter = cq.data.split(":")
    page = int(page_str)

    cards_db = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if not cards_db:
        return await cq.answer("У вас нет карт.", show_alert=True)

    user_cids = [row[0] for row in cards_db]

    rev_map = {
        "divine": "Божественная ⚫️",
        "mythic": "Мифическая 🔴",
        "legendary": "Легендарная 🔵",
        "epic": "Эпическая 🟢",
        "rare": "Редкая 🟡",
        "common": "Обычная ⚪️"
    }

    target_rarity = rev_map.get(rarity_filter, "all")
    if target_rarity != "all":
        user_cids = [cid for cid in user_cids if CARDS.get(cid, {}).get('rarity') == target_rarity]

    def card_power(cid):
        c = CARDS.get(cid)
        if not c: return 0
        return c.get('speed', 0) + c.get('strength', 0) + c.get('intellect', 0)

    user_cids.sort(key=lambda cid: (RARITY_ORDER.get(CARDS.get(cid, {}).get('rarity'), 0), card_power(cid)), reverse=True)

    items_per_page = 12
    total_pages = (len(user_cids) + items_per_page - 1) // items_per_page if user_cids else 1
    if page >= total_pages: page = max(0, total_pages - 1)
    if page < 0: page = 0

    start_idx = page * items_per_page
    page_cids = user_cids[start_idx:start_idx + items_per_page]

    bld = InlineKeyboardBuilder()
    bld.button(text="⚫️", callback_data="inv_view:0:divine")
    bld.button(text="🔴", callback_data="inv_view:0:mythic")
    bld.button(text="🔵", callback_data="inv_view:0:legendary")
    bld.button(text="🟢", callback_data="inv_view:0:epic")
    bld.button(text="🟡", callback_data="inv_view:0:rare")
    bld.button(text="⚪️", callback_data="inv_view:0:common")
    bld.button(text="Все", callback_data="inv_view:0:all")
    bld.adjust(7)

    card_buttons = []
    for cid in page_cids:
        c = CARDS.get(cid)
        if c:
            emoji = c['rarity'].split()[-1] if len(c['rarity'].split()) > 1 else ""
            card_buttons.append(types.InlineKeyboardButton(text=f"{c['name']} {emoji}", callback_data=f"viewcard:{cid}:{page_str}:{rarity_filter}"))

    for i in range(0, len(card_buttons), 2):
        bld.row(*card_buttons[i:i + 2])

    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"inv_view:{page - 1}:{rarity_filter}"))
    else:
        nav_row.append(types.InlineKeyboardButton(text=" ", callback_data="ignore"))

    nav_row.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))

    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton(text="Вперед ➡️", callback_data=f"inv_view:{page + 1}:{rarity_filter}"))
    else:
        nav_row.append(types.InlineKeyboardButton(text=" ", callback_data="ignore"))

    bld.row(*nav_row)
    bld.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="inv_main"))

    filter_name = rev_map.get(rarity_filter, "Все")
    txt = f"🎴 Ваши карты\nФильтр: {filter_name}"

    try:
        await cq.message.edit_text(txt, reply_markup=bld.as_markup())
    except Exception:
        await cq.message.delete()
        await cq.message.answer(txt, reply_markup=bld.as_markup())
    await cq.answer()

@router.callback_query(F.data == "inv_collection")
async def inv_collection_cb(cq: CallbackQuery):
    cards_db = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    user_owned = set([row[0] for row in cards_db])

    total_cards = len(CARDS)
    owned_total = len(user_owned)
    total_pct = int((owned_total / total_cards) * 100) if total_cards > 0 else 0

    rarities = [
        ("Божественная ⚫️", "⚫️ Божественная"),
        ("Мифическая 🔴", "🔴 Мифическая"),
        ("Легендарная 🔵", "🔵 Легендарная"),
        ("Эпическая 🟢", "🟢 Эпическая"),
        ("Редкая 🟡", "🟡 Редкая"),
        ("Обычная ⚪️", "⚪️ Обычная")
    ]

    lines = [
        "📊 Коллекция собранных карт\n",
        f"Всего карт: {owned_total}/{total_cards} ({total_pct}%)\n",
        "💎 Количество карт по редкостям:"
    ]

    for db_rarity, disp_name in rarities:
        all_r = [cid for cid, c in CARDS.items() if c.get('rarity') == db_rarity]
        t_r = len(all_r)
        if t_r == 0: continue

        o_r = [cid for cid in all_r if cid in user_owned]
        o_t = len(o_r)
        pct = int((o_t / t_r) * 100) if t_r > 0 else 0

        lines.append(f"{disp_name}: {o_t}/{t_r} ({pct}%)")

    txt = "\n".join(lines)

    bld = InlineKeyboardBuilder()
    bld.button(text="🔙 Назад", callback_data="inv_main")

    try:
        await cq.message.edit_text(txt, reply_markup=bld.as_markup())
    except:
        await cq.message.delete()
        await cq.message.answer(txt, reply_markup=bld.as_markup())
    await cq.answer()


@router.callback_query(F.data.startswith("viewcard:"))
async def view_card(cq: CallbackQuery):
    parts = cq.data.split(":")
    cid = parts[1]

    page = parts[2] if len(parts) > 2 else "0"
    r_filter = parts[3] if len(parts) > 3 else "all"

    c = CARDS[cid]
    txt = f"🃏 Ваша боевая карта!\n\n🎴 Персонаж: {c['name']}\n🔮 Редкость: {c['rarity']}\n👊 Стиль боя: {c['style']}\n\n⚡️ Скорость: {c['speed']}\n💪 Сила: {c['strength']}\n🧠 Интеллект: {c['intellect']}"

    bld = InlineKeyboardBuilder()
    bld.button(text="〽️ Трейд", callback_data=f"trade_init:{cid}")
    bld.button(text="Назад", callback_data=f"inv_view:{page}:{r_filter}")
    bld.adjust(1)

    await cq.message.delete()
    await cq.message.answer_photo(photo=c['file_id'], caption=txt, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("trade_init:"))
async def trade_init(cq: CallbackQuery, state: FSMContext):
    cid = cq.data.split(":")[1]
    await state.update_data(trade_card=cid)
    await state.set_state(TradeState.waiting_for_trade_id)

    c = CARDS[cid]
    bld = InlineKeyboardBuilder()
    bld.button(text="Отменить", callback_data="trade_cancel_init")

    await cq.message.delete()
    await cq.message.answer_photo(
        photo=c['file_id'],
        caption="⏳ Отправьте 🆔 игрока, которому хотите предложить обмен",
        reply_markup=bld.as_markup()
    )


@router.callback_query(F.data == "trade_cancel_init")
async def trade_cancel_init(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.delete()
    await cq.message.answer("Трейд отменен.", reply_markup=kb_main())


@router.message(TradeState.waiting_for_trade_id)
async def process_trade_id(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    cid = data.get('trade_card')
    if not cid:
        await state.clear()
        return

    target_id = msg.text.strip()
    if not target_id.isdigit():
        return await msg.answer("Неверный ID. Попробуйте еще раз или нажмите Отменить.")
    target_id = int(target_id)

    if target_id == msg.from_user.id:
        return await msg.answer("Нельзя трейдиться с собой.")

    u_target = get_user(target_id)
    if not u_target:
        return await msg.answer("Игрок не найден.")

    await state.clear()

    PENDING_TRADES[msg.from_user.id] = {
        'sender_card': cid,
        'receiver_id': target_id,
        'receiver_card': None
    }

    c = CARDS[cid]
    target_name = u_target[2] if u_target[2] else f"Игрок {target_id}"

    await msg.answer(f"Ваш запрос отправлен трейдеру: {target_name}")

    has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (target_id, cid), fetch=True)

    warning = " (⚠️ У вас есть эта карта!)" if has_card else ""
    caption = f"{msg.from_user.first_name} хочет с вами трейд{warning}"

    bld = InlineKeyboardBuilder()
    bld.button(text="Выбрать карту для обмена", callback_data=f"trade_p2_select:{msg.from_user.id}")
    bld.button(text="Отказаться", callback_data=f"trade_decline:{msg.from_user.id}")
    bld.adjust(1)

    try:
        await msg.bot.send_photo(
            target_id,
            photo=c['file_id'],
            caption=caption,
            reply_markup=bld.as_markup()
        )
    except Exception:
        await msg.answer("Не удалось отправить запрос. Возможно, игрок заблокировал бота.")
        PENDING_TRADES.pop(msg.from_user.id, None)


@router.callback_query(F.data.startswith("trade_p2_select:"))
async def trade_p2_select(cq: CallbackQuery):
    sender_id = int(cq.data.split(":")[1])
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.answer("Трейд не актуален или отменен.", show_alert=True)

    sender_card = t['sender_card']
    rarity = CARDS[sender_card]['rarity']

    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    valid_cards = list(set([cid for (cid,) in cards if CARDS[cid]['rarity'] == rarity]))

    if not valid_cards:
        await cq.message.delete()
        await cq.message.answer("У вас нет карт такой же редкости для обмена. Трейд отменен.")
        PENDING_TRADES.pop(sender_id, None)
        try:
            await cq.bot.send_message(sender_id, "Игрок не может принять трейд, так как у него нет подходящих карт.")
        except:
            pass
        return

    bld = InlineKeyboardBuilder()
    for cid in valid_cards[:40]:
        bld.button(text=CARDS[cid]['name'], callback_data=f"trade_p2_conf:{sender_id}:{cid}")
    bld.button(text="❌ Отказаться", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(2)

    await cq.message.delete()
    await cq.message.answer("Выбери карту, которую хочешь отдать взамен:", reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("trade_p2_conf:"))
async def trade_p2_conf(cq: CallbackQuery):
    _, sender_id, p2_card = cq.data.split(":")
    sender_id = int(sender_id)

    t = PENDING_TRADES.get(sender_id)
    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.answer("Трейд не актуален.", show_alert=True)

    t['receiver_card'] = p2_card

    c1 = CARDS[t['sender_card']]
    c2 = CARDS[p2_card]
    sender_user = get_user(sender_id)
    sender_name = sender_user[2] if sender_user else f"Игрок {sender_id}"

    await cq.message.delete()

    media = [
        types.InputMediaPhoto(media=c2['file_id']),
        types.InputMediaPhoto(media=c1['file_id'])
    ]
    await cq.message.answer_media_group(media=media)

    txt = (f"🔄 Подтверждение трейда c {sender_name}:\n\n"
           f"📤 Вы отдаете: {c2['name']}\n"
           f"📥 Вы получите: {c1['name']}\n\n"
           f"❓ Вы уверены, что хотите совершить трейд?")

    bld = InlineKeyboardBuilder()
    bld.button(text="✅ Подтвердить", callback_data=f"trade_p2_final:{sender_id}")
    bld.button(text="❌ Отказаться", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(2)

    await cq.message.answer(txt, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("trade_p2_final:"))
async def trade_p2_final(cq: CallbackQuery):
    sender_id = int(cq.data.split(":")[1])
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != cq.from_user.id:
        return await cq.answer("Трейд не актуален.", show_alert=True)

    await cq.message.edit_text("✅ Ожидание подтверждения от инициатора...")

    c1 = CARDS[t['sender_card']]
    c2 = CARDS[t['receiver_card']]

    has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, t['receiver_card']),
                       fetch=True)

    warning = " (⚠️ У вас есть эта карта)" if has_card else ""
    p2_name = cq.from_user.first_name

    txt = (f"🔄 💫 {p2_name} предлагает вам трейд:\n\n"
           f"📤 Вы отдаете: {c1['name']}\n"
           f"📥 Вы получите: {c2['name']}{warning}\n\n"
           f"❓ Вы уверены, что хотите совершить трейд?")

    media = [
        types.InputMediaPhoto(media=c1['file_id']),
        types.InputMediaPhoto(media=c2['file_id'])
    ]

    bld = InlineKeyboardBuilder()
    bld.button(text="✅ Согласиться", callback_data=f"trade_p1_final:{cq.from_user.id}")
    bld.button(text="❌ Отказаться", callback_data=f"trade_decline:{sender_id}")
    bld.adjust(2)

    try:
        await cq.bot.send_media_group(sender_id, media=media)
        await cq.bot.send_message(sender_id, txt, reply_markup=bld.as_markup())
    except Exception:
        await cq.message.answer("Не удалось связаться с инициатором. Трейд отменен.")
        PENDING_TRADES.pop(sender_id, None)


@router.callback_query(F.data.startswith("trade_p1_final:"))
async def trade_p1_final(cq: CallbackQuery):
    p2_id = int(cq.data.split(":")[1])
    sender_id = cq.from_user.id
    t = PENDING_TRADES.get(sender_id)

    if not t or t['receiver_id'] != p2_id:
        return await cq.answer("Трейд не актуален.", show_alert=True)

    c1_id = t['sender_card']
    c2_id = t['receiver_card']

    p1_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c1_id), fetch=True)
    p2_has = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c2_id), fetch=True)

    if not p1_has or not p2_has:
        PENDING_TRADES.pop(sender_id, None)
        await cq.message.edit_text("❌ Трейд сорвался: у одного из игроков больше нет нужной карты.")
        try:
            await cq.bot.send_message(p2_id, "❌ Трейд сорвался: у одного из игроков больше нет нужной карты.")
        except:
            pass
        return

    # Удаляем отданные карты из инвентаря
    db_exec("DELETE FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c1_id))
    db_exec("DELETE FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c2_id))

    # Проверка на наличие карты, которую они сейчас получают
    p1_has_c2 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (sender_id, c2_id), fetch=True)
    p2_has_c1 = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?", (p2_id, c1_id), fetch=True)

    # Записываем только в том случае, если дубликата нет (иначе сгорает)
    if not p1_has_c2:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (sender_id, c2_id))

    if not p2_has_c1:
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (p2_id, c1_id))
    PENDING_TRADES.pop(sender_id, None)

    await cq.message.delete()
    await cq.message.answer_photo(
        photo=CARDS[c2_id]['file_id'],
        caption="✅ Получена карта с трейда"
    )

    try:
        await cq.bot.send_photo(
            p2_id,
            photo=CARDS[c1_id]['file_id'],
            caption="✅ Получена карта с трейда"
        )
    except:
        pass


@router.callback_query(F.data.startswith("trade_decline:"))
async def trade_decline(cq: CallbackQuery):
    sender_id = int(cq.data.split(":")[1])
    t = PENDING_TRADES.pop(sender_id, None)

    await cq.message.delete()
    await cq.message.answer("❌ Трейд отменен.")

    other_id = sender_id if cq.from_user.id != sender_id else (t['receiver_id'] if t else None)
    if other_id:
        try:
            await cq.bot.send_message(other_id, "❌ Игрок отказался от трейда (или трейд отменен).")
        except:
            pass


@router.callback_query(F.data == "ignore")
async def ignore_cb(cq: CallbackQuery):
    await cq.answer()


# ============ МАГАЗИН ============
@router.message(F.text == "🛍 Магазин")
async def shop(msg: types.Message):
    bld = InlineKeyboardBuilder()
    bld.button(text="🗃️ Паки", callback_data="shop_packs")
    bld.button(text="🌄 Фоны", callback_data="shop_bgs")
    await msg.answer("🛍 Добро пожаловать в Магазин!", reply_markup=bld.as_markup())


@router.callback_query(F.data == "shop_packs")
async def shop_packs(cq: CallbackQuery):
    kb = [
        [KeyboardButton(text="🗃️ Купить Легендарный пак"), KeyboardButton(text="🗃️ Купить Эпический пак")],
        [KeyboardButton(text="🔙 Назад к пакам")]
    ]
    await cq.message.answer("Выберите пак для покупки:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@router.message(F.text == "🔙 Назад к пакам")
async def back_to_menu(msg: types.Message):
    await msg.answer("Возвращаемся...", reply_markup=kb_main())


@router.message(F.text.in_(["🗃️ Купить Легендарный пак", "🗃️ Купить Эпический пак"]))
async def buy_pack(msg: types.Message):
    u = get_user(msg.from_user.id)
    is_leg = "Легендарный" in msg.text
    cost = 450 if is_leg else 150
    rarity = "Легендарная 🔵" if is_leg else "Эпическая 🟢"

    if u[4] < cost:
        return await msg.answer(f"❌ Недостаточно KRW. Нужно: {cost} 💴")

    db_exec("UPDATE users SET krw = krw - ? WHERE id = ?", (cost, msg.from_user.id))
    card_key = pull_random_card(force_rarity=rarity)
    if not card_key: card_key = pull_random_card()  # Фолбэк

    is_new, krw, c = give_card_to_user(msg.from_user.id, card_key)
    txt = f"📦 Вы открыли пак!\n\n🎴 {c['name']}\n🔮 {c['rarity']}\n" + (
        "(Повторка, конвертировано в KRW)" if not is_new else "(Новая карта!)")
    await msg.answer_photo(photo=c['file_id'], caption=txt, reply_markup=kb_main())


@router.callback_query(F.data == "shop_bgs")
async def shop_bgs(cq: CallbackQuery):
    bld = InlineKeyboardBuilder()
    bld.button(text="🛍️ Купить", callback_data="buy_bg:lookism_1")
    txt = f"🌄 Фон: {BGS['lookism_1']['name']}\n💰 Цена: {BGS['lookism_1']['price']}🪙"
    await cq.message.answer_photo(photo=BGS['lookism_1']['file_id'], caption=txt, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("buy_bg:"))
async def buy_bg(cq: CallbackQuery):
    bg_id = cq.data.split(":")[1]
    cost = BGS[bg_id]['price']
    u = get_user(cq.from_user.id)
    if u[5] < cost: return await cq.answer(f"❌ Нужно {cost} BattleCoin", show_alert=True)

    has_bg = db_exec("SELECT 1 FROM bgs_inv WHERE user_id = ? AND bg_id = ?", (cq.from_user.id, bg_id), fetch=True)
    if has_bg: return await cq.answer("Уже куплено!", show_alert=True)

    db_exec("UPDATE users SET battlecoin = battlecoin - ? WHERE id = ?", (cost, cq.from_user.id))
    db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (cq.from_user.id, bg_id))
    await cq.message.answer("✅ Фон успешно куплен и добавлен в «🌄 Мои фоны»!")


# ============ ПАСС ============
from datetime import datetime, timezone, timedelta
import calendar

# Жесткая привязка к МСК (UTC+3)
MSK = timezone(timedelta(hours=3))

PASS_NORMAL_IMG_1 = "AgACAgIAAxkBAAICN2npT2usTg9JKYcN77omcGfxSMy_AALrFWsbYxlIS8jrNH8Lp0d_AQADAgADdwADOwQ"
PASS_NORMAL_IMG_2 = "AgACAgIAAxkBAAICOWnpT3Nb7Od3EEVKv7rF-ubLjKd-AALsFWsbYxlIS4xOhVzQesKRAQADAgADdwADOwQ"
PASS_NORMAL_IMG_3 = "AgACAgIAAxkBAAICO2npT3qdFBDkzJEtJvpAv76tZfsPAALtFWsbYxlIS9SubA_87SHZAQADAgADdwADOwQ"
PASS_NORMAL_IMG_4 = "AgACAgIAAxkBAAICPWnpT4DmcHYmlKkeldmpIKAy4I9wAALuFWsbYxlIS5HkxNAVGOqGAQADAgADdwADOwQ"
PASS_NORMAL_IMG_5 = "AgACAgIAAxkBAAICP2npT4bQwb50eaG4jiP9vxak_cJyAALvFWsbYxlIS0_NW8CdRi_FAQADAgADdwADOwQ"

PASS_ROYALE_IMG_1 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"
PASS_ROYALE_IMG_2 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"
PASS_ROYALE_IMG_3 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"
PASS_ROYALE_IMG_4 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"
PASS_ROYALE_IMG_5 = "AgACAgIAAxkBAAFHlWlp5RYNIdTKRATRsk13YOweDtWx-QAC_xhrG-CeKEvC9zzmqTrx3AEAAwIAA3cAAzsE"


@router.message(F.text == "🏞️ Пасс")
async def pass_menu(msg: types.Message):
    bld = InlineKeyboardBuilder()
    bld.button(text="🏙️ Обычный пасс", callback_data="pass:normal:start")
    bld.button(text="🌠 Рояль пасс", callback_data="pass:royale:start")
    await msg.answer("Выберите Пасс:", reply_markup=bld.as_markup())


@router.callback_query(F.data == "pass_back")
async def pass_back(cq: CallbackQuery):
    await cq.message.delete()
    await pass_menu(cq.message)


@router.callback_query(F.data.startswith("pass:"))
async def show_pass(cq: CallbackQuery):
    _, p_type, page_str = cq.data.split(":")
    uid = cq.from_user.id
    u = get_user(uid)
    now = datetime.now(MSK)
    _, days_in_month = calendar.monthrange(now.year, now.month)

    # Автоматическое открытие страницы с текущим днем при первом входе
    if page_str == "start":
        if now.day <= 6:
            page = 0
        elif now.day <= 12:
            page = 1
        elif now.day <= 18:
            page = 2
        elif now.day <= 24:
            page = 3
        else:
            page = 4
    else:
        page = int(page_str)

    await render_pass_page(cq, p_type, page, u, now, days_in_month)


async def render_pass_page(cq: CallbackQuery, p_type: str, page: int, u: tuple, now: datetime, days_in_month: int):
    uid = u[0]
    is_royale = (p_type == "royale")
    if is_royale and u[16] == 0:
        bld = InlineKeyboardBuilder()
        bld.button(text="Купить ⭐️", callback_data="buy_royale_pass")
        bld.button(text="Назад 🔙", callback_data="pass_back")
        bld.adjust(1)
        try:
            await cq.message.edit_media(
                media=types.InputMediaPhoto(media=PASS_ROYALE_IMG_1,
                                            caption="🌠 Рояль пасс\n\n⚠️ Данный пасс у вас ещё не приобретен."),
                reply_markup=bld.as_markup()
            )
        except:
            await cq.message.answer_photo(photo=PASS_ROYALE_IMG_1,
                                          caption="🌠 Рояль пасс\n\n⚠️ Данный пасс у вас ещё не приобретен.",
                                          reply_markup=bld.as_markup())
        return
    data = ROYALE_PASS if is_royale else NORMAL_PASS
    imgs_normal = [PASS_NORMAL_IMG_1, PASS_NORMAL_IMG_2, PASS_NORMAL_IMG_3, PASS_NORMAL_IMG_4, PASS_NORMAL_IMG_5]
    imgs_royale = [PASS_ROYALE_IMG_1, PASS_ROYALE_IMG_2, PASS_ROYALE_IMG_3, PASS_ROYALE_IMG_4, PASS_ROYALE_IMG_5]
    img = imgs_royale[page] if is_royale else imgs_normal[page]

    start_d = page * 6 + 1
    end_d = min(start_d + 5, days_in_month)
    if page == 4:
        end_d = days_in_month

    claims = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
                     (uid, now.month, p_type), fetchall=True)
    claimed_days = [d[0] for d in claims]

    pass_name = "🌠 Рояль пасс" if is_royale else "🏙️ Обычный пасс"
    icons = {'krw': '💴', 'atm': '💳', 'bc': '🪙', 'dia': '💎', 'pack': '🗃️'}
    pack_names = {'epic': 'Эпический пак 🟢', 'leg': 'Легендарный Пак 🔵'}

    rewards_txt = ""
    for d in range(start_d, end_d + 1):
        r_type, r_val = data.get(d, ('krw', 10))
        if r_type == 'pack':
            r_str = pack_names.get(r_val, 'Пак')
        else:
            r_str = f"{r_val} {icons.get(r_type, '')}"
        rewards_txt += f"{d} день: {r_str}\n"

    txt = (f"{pass_name}\n\n"
           f"🟢 Заходи в пасс каждый день и получай награды, сегодня {now.day}-й день.\n\n"
           f"Награды на этой странице:\n{rewards_txt}\n"
           f"Обозначения:\n"
           f"❌ - День пропущен\n"
           f"✅ - Награда получена\n"
           f"🕓 - Ожидание награды\n\n"
           f"Получено дней - {len(claimed_days)}/{days_in_month}")

    bld = InlineKeyboardBuilder()

    # Кнопки ячеек
    cells = []
    for i in range(5):
        text = f"[{i + 1}]" if i == page else str(i + 1)
        cells.append(InlineKeyboardButton(text=text, callback_data=f"pass:{p_type}:{i}"))
    bld.row(*cells)

    # Кнопки дней
    day_buttons = []
    for d in range(start_d, end_d + 1):
        if d in claimed_days:
            status = "✅"
        elif d < now.day:
            status = "❌"
        elif d == now.day:
            status = "🎯"
        else:
            status = "🕓"
        day_buttons.append(InlineKeyboardButton(text=f"{status} {d}", callback_data=f"claim_pass:{p_type}:{d}:{page}"))

    for i in range(0, len(day_buttons), 3):
        bld.row(*day_buttons[i:i + 3])

    bld.row(InlineKeyboardButton(text="Купить дни 💎", callback_data=f"buy_days_menu:{p_type}"))

    # Главный приз только на последней ячейке
    if page == 4:
        bld.row(InlineKeyboardButton(text="Главный приз 🐦‍🔥", callback_data=f"pass_main_prize:{p_type}"))

    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data="pass_back"))

    try:
        await cq.message.edit_media(media=types.InputMediaPhoto(media=img, caption=txt), reply_markup=bld.as_markup())
    except Exception as e:
        if "message is not modified" not in str(e).lower():
            try:
                await cq.message.delete()
                await cq.message.answer_photo(photo=img, caption=txt, reply_markup=bld.as_markup())
            except:
                pass


@router.callback_query(F.data.startswith("claim_pass:"))
async def claim_pass(cq: CallbackQuery):
    _, p_type, day_str, page_str = cq.data.split(":")
    day = int(day_str)
    page = int(page_str)
    uid = cq.from_user.id
    now = datetime.now(MSK)

    is_claimed = db_exec("SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = ?",
                         (uid, now.month, day, p_type), fetch=True)
    if is_claimed:
        return await cq.answer("Вы уже забрали эту награду! ✅", show_alert=True)

    if day > now.day:
        return await cq.answer("Этот день еще не наступил! 🕓", show_alert=True)
    if day < now.day:
        return await cq.answer("Этот день пропущен! ❌ Используйте «Купить дни 💎»", show_alert=True)

    data = ROYALE_PASS if p_type == "royale" else NORMAL_PASS
    r_type, r_val = data.get(day, ('krw', 10))

    if r_type == 'krw':
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (r_val, uid))
    elif r_type == 'atm':
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (r_val, uid))
    elif r_type == 'bc':
        db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (r_val, uid))
    elif r_type == 'dia':
        db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (r_val, uid))
    elif r_type == 'pack':
        card_key = pull_random_card(force_rarity="Легендарная 🔵" if r_val == "leg" else "Эпическая 🟢")
        if not card_key: card_key = pull_random_card()
        give_card_to_user(uid, card_key)
        await cq.message.answer(f"🎁 Из пака выпала карта: {CARDS[card_key]['name']}!")

    db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
            (uid, now.month, day, p_type))

    icon = {'krw': '💴', 'atm': '💳', 'bc': '🪙', 'dia': '💎', 'pack': '🗃️'}.get(r_type, '')
    await cq.answer(f"✅ Вы забрали награду: {r_val} {icon}!", show_alert=True)

    u = get_user(uid)
    _, days_in_month = calendar.monthrange(now.year, now.month)
    await render_pass_page(cq, p_type, page, u, now, days_in_month)


@router.callback_query(F.data.startswith("buy_days_menu:"))
async def buy_days_menu(cq: CallbackQuery):
    _, p_type = cq.data.split(":")
    uid = cq.from_user.id
    now = datetime.now(MSK)

    claims = db_exec("SELECT day FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
                     (uid, now.month, p_type), fetchall=True)
    claimed_days = [d[0] for d in claims]
    missed_days = [d for d in range(1, now.day) if d not in claimed_days]

    if not missed_days:
        return await cq.answer("У вас нет пропущенных дней! 🎉", show_alert=True)

    db_exec("CREATE TABLE IF NOT EXISTS pass_bought_days (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
    bought_count = db_exec("SELECT COUNT(*) FROM pass_bought_days WHERE user_id = ? AND month = ? AND pass_type = ?",
                           (uid, now.month, p_type), fetch=True)[0]

    next_cost = (bought_count + 1) * 20

    txt = (f"💎 Восстановление пропущенных дней\n\n"
           f"Стоимость каждого дня увеличивается на 20:\n"
           f"1-й день — 20 💎\n"
           f"2-й день — 40 💎\n"
           f"3-й день — 60 💎\n"
           f"и т.д.\n\n"
           f"Текущая стоимость восстановления: {next_cost} 💎\n\n"
           f"Выберите, какие дни хотите купить:")

    bld = InlineKeyboardBuilder()
    day_buttons = []
    for d in missed_days:
        day_buttons.append(InlineKeyboardButton(text=f"❌ {d}", callback_data=f"buy_missed_day:{p_type}:{d}"))

    for i in range(0, len(day_buttons), 4):
        bld.row(*day_buttons[i:i + 4])

    bld.row(InlineKeyboardButton(text="Назад 🔙", callback_data=f"pass:{p_type}:start"))

    try:
        await cq.message.edit_caption(caption=txt, reply_markup=bld.as_markup())
    except:
        pass

    @router.callback_query(F.data.startswith("buy_missed_day:"))
    async def buy_missed_day(cq: CallbackQuery):
        _, p_type, day_str = cq.data.split(":")
        day = int(day_str)
        uid = cq.from_user.id
        now = datetime.now(MSK)

        is_claimed = db_exec("SELECT 1 FROM pass_claims WHERE user_id = ? AND month = ? AND day = ? AND pass_type = ?",
                             (uid, now.month, day, p_type), fetch=True)
        if is_claimed:
            return await cq.answer("Этот день уже получен!", show_alert=True)

        db_exec(
            "CREATE TABLE IF NOT EXISTS pass_bought_days (user_id INTEGER, month INTEGER, day INTEGER, pass_type TEXT)")
        bought_count = \
        db_exec("SELECT COUNT(*) FROM pass_bought_days WHERE user_id = ? AND month = ? AND pass_type = ?",
                (uid, now.month, p_type), fetch=True)[0]
        cost = (bought_count + 1) * 20

        u = get_user(uid)
        if u[3] < cost:  # u[3] - это алмазы
            return await cq.answer(f"❌ Недостаточно алмазов! Нужно: {cost} 💎", show_alert=True)

        db_exec("UPDATE users SET diamond = diamond - ? WHERE id = ?", (cost, uid))
        db_exec("INSERT INTO pass_bought_days (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
                (uid, now.month, day, p_type))

        data = ROYALE_PASS if p_type == "royale" else NORMAL_PASS
        r_type, r_val = data.get(day, ('krw', 10))

        if r_type == 'krw':
            db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (r_val, uid))
        elif r_type == 'atm':
            db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (r_val, uid))
        elif r_type == 'bc':
            db_exec("UPDATE users SET battlecoin = battlecoin + ? WHERE id = ?", (r_val, uid))
        elif r_type == 'dia':
            db_exec("UPDATE users SET diamond = diamond + ? WHERE id = ?", (r_val, uid))
        elif r_type == 'pack':
            card_key = pull_random_card(force_rarity="Легендарная 🔵" if r_val == "leg" else "Эпическая 🟢")
            if not card_key: card_key = pull_random_card()
            give_card_to_user(uid, card_key)
            await cq.message.answer(f"🎁 Из пака выпала карта: {CARDS[card_key]['name']}!")

        db_exec("INSERT INTO pass_claims (user_id, month, day, pass_type) VALUES (?, ?, ?, ?)",
                (uid, now.month, day, p_type))

        await cq.answer(f"✅ День {day} восстановлен!", show_alert=True)

        # Обновляем меню покупки
        await buy_days_menu(cq)

    @router.callback_query(F.data.startswith("pass_main_prize:"))
    async def pass_main(cq: CallbackQuery):
        p_type = cq.data.split(":")[1]
        uid = cq.from_user.id
        now = datetime.now(MSK)
        _, dim = calendar.monthrange(now.year, now.month)
        claims = db_exec("SELECT COUNT(*) FROM pass_claims WHERE user_id = ? AND month = ? AND pass_type = ?",
                         (uid, now.month, p_type), fetch=True)

        if claims[0] < dim:
            return await cq.answer("❌ Соберите награды за все дни месяца!", show_alert=True)

        if p_type == "normal":
            has_title = db_exec("SELECT 1 FROM titles_inv WHERE user_id = ? AND title_id = ?",
                                (uid, MAIN_PRIZE_NORMAL_TITLE), fetch=True)
            if has_title:
                return await cq.answer("✅ Главный приз уже в инвентаре!", show_alert=True)
            db_exec("INSERT INTO titles_inv (user_id, title_id) VALUES (?, ?)", (uid, MAIN_PRIZE_NORMAL_TITLE))
            await cq.answer("✅ Получен главный приз: Титул!", show_alert=True)
        else:
            has_card = db_exec("SELECT 1 FROM cards_inv WHERE user_id = ? AND card_id = ?",
                               (uid, MAIN_PRIZE_ROYALE_CARD), fetch=True)
            if has_card:
                return await cq.answer("✅ Главный приз уже в инвентаре!", show_alert=True)
            give_card_to_user(uid, MAIN_PRIZE_ROYALE_CARD)
            await cq.answer("✅ Получен эксклюзивный персонаж Рояль Пасса!", show_alert=True)

    @router.callback_query(F.data == "buy_royale_pass")
    async def buy_rp(cq: CallbackQuery, bot: Bot):
        await bot.send_invoice(cq.from_user.id, title="🌠 Рояль Пасс",
                               description="Доступ к эксклюзивным наградам на этот месяц",
                               payload="rp_buy", provider_token="", currency="XTR",
                               prices=[LabeledPrice(label="Stars", amount=50)])

    @router.pre_checkout_query()
    async def pre_chk(pcq: PreCheckoutQuery, bot: Bot):
        await bot.answer_pre_checkout_query(pcq.id, ok=True)

    @router.message(F.successful_payment)
    async def success_pay(msg: types.Message):
        db_exec("UPDATE users SET royale_pass = 1 WHERE id = ?", (msg.from_user.id,))
        await msg.answer("✅ Вы успешно приобрели Рояль Пасс!")

    # ============ БОЕВКА ============
def check_advantage(style1, style2):
    if style1 == style2: return 0
    if style1 == 'int' and style2 == 'str': return 1
    if style1 == 'str' and style2 == 'spd': return 1
    if style1 == 'spd' and style2 == 'int': return 1
    return -1


@router.message(F.text == "⚔️ Поле битвы")
async def battle_menu(msg: types.Message):
    u = get_user(msg.from_user.id)
    txt = (f"⚔️ Добро пожаловать на поле битвы!\n\n"
           f"Здесь ты можешь собрать свою колоду, сразиться с другими игроками и получать очки ранга.\n\n"
           f"Собрать свою уникальную колоду ты сможешь в разделе «Моя колода 🗂» после получения 10 боевых карт\n\n"
           f"🏅 {u[7]} Очков | Ранг {get_rank(u[7])}\n"
           f"Победа/Ничья/Поражение :\n"
           f"{u[8]}/{u[9]}/{u[10]}")
    bld = InlineKeyboardBuilder()
    bld.button(text="Найти противника 👁️", callback_data="find_match")
    bld.button(text="Моя колода 🗂", callback_data="my_deck")
    bld.adjust(1)
    await msg.answer(txt, reply_markup=bld.as_markup())


@router.callback_query(F.data == "my_deck")
async def my_deck_menu(cq: CallbackQuery):
    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if len(cards) < 10:
        return await cq.answer("❌ Нужно получить минимум 10 боевых карт, чтобы открыть этот раздел!", show_alert=True)

    bld = InlineKeyboardBuilder()
    bld.button(text="Посмотреть колоду 🃏", callback_data="view_deck")
    bld.button(text="Автосбор 🔁", callback_data="auto_deck")
    bld.button(text="Собрать колоду 🆕", callback_data="manual_deck_start")
    bld.adjust(1)
    await cq.message.edit_text("🗂 Меню колоды:\nВыберите действие:", reply_markup=bld.as_markup())


@router.callback_query(F.data == "view_deck")
async def view_deck(cq: CallbackQuery):
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ? ORDER BY slot_index", (cq.from_user.id,), fetchall=True)
    if len(deck) != 6:
        return await cq.answer("Колода не собрана полностью!", show_alert=True)

    rarity_order = {"Божественная ⚫️": 6, "Мифическая 🔴": 5, "Легендарная 🔵": 4, "Эпическая 🟢": 3, "Редкая 🟡": 2,
                    "Обычная ⚪️": 1}
    c_objs = [(cid, CARDS[cid]) for (cid,) in deck]
    c_objs.sort(key=lambda x: rarity_order.get(x[1]['rarity'], 0), reverse=True)

    media = []
    for i, (cid, c) in enumerate(c_objs):
        txt_card = f"{i + 1}. {c['name']} ({c['rarity']})\n⚡️{c['speed']} | 💪{c['strength']} | 🧠{c['intellect']}"
        media.append(types.InputMediaPhoto(media=c['file_id'], caption=txt_card))

    await cq.message.answer_media_group(media=media)


@router.callback_query(F.data == "auto_deck")
async def auto_deck(cq: CallbackQuery):
    cards = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (cq.from_user.id,), fetchall=True)
    if len(cards) < 6: return await cq.answer("Для колоды нужно минимум 6 карт!", show_alert=True)

    c_objs = []
    for (cid,) in cards:
        c = CARDS[cid]
        c_objs.append({'id': cid, 't': c['speed'] + c['strength'] + c['intellect'], 'r': c['rarity']})
    c_objs.sort(key=lambda x: x['t'], reverse=True)

    new_deck = []
    mythic, leg = 0, 0
    for c in c_objs:
        if len(new_deck) == 6: break
        if "Мифическая" in c['r']:
            if mythic >= 1: continue
            mythic += 1
        elif "Легендарная" in c['r']:
            if leg >= 2: continue
            leg += 1
        new_deck.append(c['id'])

    if len(new_deck) < 6:
        return await cq.answer("Не удалось собрать 6 карт из-за ограничений редкости.", show_alert=True)

    db_exec("DELETE FROM decks WHERE user_id = ?", (cq.from_user.id,))
    for i, cid in enumerate(new_deck):
        db_exec("INSERT INTO decks (user_id, card_id, slot_index) VALUES (?, ?, ?)", (cq.from_user.id, cid, i))
    await cq.answer("✅ Колода автоматически собрана лучшими картами!", show_alert=True)


@router.callback_query(F.data == "manual_deck_start")
async def manual_deck_start(cq: CallbackQuery):
    db_exec("DELETE FROM decks WHERE user_id = ?", (cq.from_user.id,))
    await cq.message.answer("🆕 Сборка колоды начата. Выберите 6 карт по очереди.")
    await show_deck_builder(cq.message, cq.from_user.id, 1)


async def show_deck_builder(msg, uid, slot):
    if slot > 6:
        await msg.answer("✅ Колода успешно собрана!")
        return

    inv = db_exec("SELECT card_id FROM cards_inv WHERE user_id = ?", (uid,), fetchall=True)
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ?", (uid,), fetchall=True)
    deck_ids = [d[0] for d in deck]

    mythic_cnt = sum(1 for cid in deck_ids if "Мифическая" in CARDS[cid]['rarity'])
    leg_cnt = sum(1 for cid in deck_ids if "Легендарная" in CARDS[cid]['rarity'])

    avail = []
    owned_counts = {}
    for (cid,) in inv:
        owned_counts[cid] = owned_counts.get(cid, 0) + 1

    for cid, count in owned_counts.items():
        if deck_ids.count(cid) >= count: continue
        if "Мифическая" in CARDS[cid]['rarity'] and mythic_cnt >= 1: continue
        if "Легендарная" in CARDS[cid]['rarity'] and leg_cnt >= 2: continue
        avail.append(cid)

    if not avail:
        await msg.answer(
            "❌ Недостаточно подходящих карт для завершения колоды. Вы не можете выполнить правила (максимум 1 Мифическая, 2 Легендарные). Колода сброшена.")
        db_exec("DELETE FROM decks WHERE user_id = ?", (uid,))
        return

    bld = InlineKeyboardBuilder()
    for cid in avail[:40]:
        bld.button(text=CARDS[cid]['name'], callback_data=f"bdeck:{slot}:{cid}")
    bld.adjust(2)
    await msg.answer(f"Выберите карту для слота {slot}/6:", reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("bdeck:"))
async def bdeck_select(cq: CallbackQuery):
    _, slot, cid = cq.data.split(":")
    slot = int(slot)
    db_exec("INSERT INTO decks (user_id, card_id, slot_index) VALUES (?, ?, ?)", (cq.from_user.id, cid, slot - 1))
    await cq.message.delete()
    await show_deck_builder(cq.message, cq.from_user.id, slot+1)

@router.callback_query(F.data == "find_match")
async def find_match(cq: CallbackQuery):
    uid = cq.from_user.id
    deck = db_exec("SELECT card_id FROM decks WHERE user_id = ?", (uid,), fetchall=True)
    if len(deck) != 6: return await cq.answer("Соберите колоду из 6 карт!", show_alert=True)
    u = get_user(uid)
    last_b = datetime.strptime(u[12], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    if (now - last_b).total_seconds() < BATTLE_COOLDOWN_HOURS * 3600:
        rem = int(BATTLE_COOLDOWN_HOURS * 3600 - (now - last_b).total_seconds())
        return await cq.answer(f"⏳ Кулдаун битвы: {rem // 3600}ч {(rem % 3600) // 60}м", show_alert=True)

    if MATCH_QUEUE and MATCH_QUEUE[0] != uid:
        p2 = MATCH_QUEUE.pop(0)
        await start_battle(p2, uid)
    else:
        MATCH_QUEUE.append(uid)
        await cq.message.answer("Ищем противника... (50 сек)")
        asyncio.create_task(wait_match(uid, cq.bot))


async def wait_match(uid, bot):
    for _ in range(50):
        await asyncio.sleep(1)
        if uid not in MATCH_QUEUE: return
    MATCH_QUEUE.remove(uid)
    await start_battle(uid, -1)


async def start_battle(p1, p2):
    gid = f"g_{random.randint(10000, 99999)}"
    deck1 = [c[0] for c in db_exec("SELECT card_id FROM decks WHERE user_id = ?", (p1,), fetchall=True)]

    if p2 == -1:
        deck2 = random.choices(list(CARDS.keys()), k=6)
        name2 = random.choice(["Важни Гий", "Ли Джи Хуй", "Йена пик форма", "Злодей Васко", "Жирдяй Хён Сок", "Джей Хон", "Срасул", "Клон Хикса", "Король Бибизян"])
        rank2 = "Бот"
    else:
        deck2 = [c[0] for c in db_exec("SELECT card_id FROM decks WHERE user_id = ?", (p2,), fetchall=True)]
        u2 = get_user(p2)
        name2, rank2 = u2[2], get_rank(u2[7])

    GAMES[gid] = {'p1': p1, 'p2': p2, 'd1': deck1.copy(), 'd2': deck2.copy(), 'n2': name2, 'r2': rank2,
                  'p1_c': None, 'p2_c': None, 'p1_s': None, 'p2_s': None, 'score1': 0, 'score2': 0, 'round': 1}

    u1 = get_user(p1)
    db_exec("UPDATE users SET last_battle = ? WHERE id IN (?, ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), p1, p2))

    bot = Dispatcher.get_current().bot if hasattr(Dispatcher, "get_current") else Bot(token=BOT_TOKEN)

    txt1 = f"Противник найден!\n\n· Имя: {name2} 🧩\n· Ранг: {rank2}\n· Награда: 3 очка🏅, 3 BattleCoin 🪙\n\nБитва начинается!"
    await bot.send_message(p1, txt1)

    if p2 != -1:
        txt2 = f"Противник найден!\n\n· Имя: {u1[2]} 🧩\n· Ранг: {get_rank(u1[7])}\n· Награда: 3 очка🏅, 3 BattleCoin 🪙\n\nБитва начинается!"
        await bot.send_message(p2, txt2)

    await asyncio.sleep(1)
    await send_card_choice(p1, GAMES[gid]['d1'], gid, bot)
    if p2 != -1:
        await send_card_choice(p2, GAMES[gid]['d2'], gid, bot)


async def send_card_choice(uid, deck_left, gid, bot):
    bld = InlineKeyboardBuilder()
    for c in set(deck_left):
        bld.button(text=CARDS[c]['name'], callback_data=f"b_card:{gid}:{c}")
    bld.adjust(2)
    txt = f"—————————————————\n\nРаунд {GAMES[gid]['round']}.\nВыберите 🎴 Карту для атаки\n\nНа выбор дается 20 секунд"
    await bot.send_message(uid, txt, reply_markup=bld.as_markup())


@router.callback_query(F.data.startswith("b_card:"))
async def b_card(cq: CallbackQuery):
    _, gid, card = cq.data.split(":")
    g = GAMES.get(gid)
    if not g: return await cq.answer("Игра окончена.", show_alert=True)

    is_p1 = (cq.from_user.id == g['p1'])
    if is_p1:
        if card not in g['d1']: return await cq.answer("Эта карта уже использована!", show_alert=True)
        g['p1_c'] = card
        g['d1'].remove(card)
    else:
        if card not in g['d2']: return await cq.answer("Эта карта уже использована!", show_alert=True)
        g['p2_c'] = card
        g['d2'].remove(card)
    bld = InlineKeyboardBuilder()
    bld.button(text="⚡️ Скорость", callback_data=f"b_style:{gid}:spd")
    bld.button(text="💪 Сила", callback_data=f"b_style:{gid}:str")
    bld.button(text="🧠 Интеллект", callback_data=f"b_style:{gid}:int")

    await cq.message.delete()
    txt = f"Выбрана карта: {CARDS[card]['name']}\nВыберите ⚔️ Атаку \nСтили: ⚡️ Скорость, 💪 Сила, 🧠 Интеллект.\n\nНа выбор дается 20 секунд"
    await cq.message.answer_photo(photo=CARDS[card]['file_id'], caption=txt, reply_markup=bld.as_markup())

    if g['p2'] == -1 and g['p2_c'] is None:
        bot_c = random.choice(g['d2'])
        g['p2_c'] = bot_c
        g['d2'].remove(bot_c)
        g['p2_s'] = random.choice(['spd', 'str', 'int'])

        if g['p1_s'] and g['p2_s']:
            await resolve_round(gid, cq.bot)


@router.callback_query(F.data.startswith("b_style:"))
async def b_style(cq: CallbackQuery):
    _, gid, style = cq.data.split(":")
    g = GAMES.get(gid)
    if not g: return await cq.answer("Игра окончена.", show_alert=True)

    is_p1 = (cq.from_user.id == g['p1'])
    if is_p1:
        g['p1_s'] = style
    else:
        g['p2_s'] = style

    await cq.message.delete()
    msg = await cq.message.answer("Ожидание противника...")

    if is_p1:
        g['p1_wait_msg'] = msg.message_id
    else:
        g['p2_wait_msg'] = msg.message_id

    if g['p1_s'] and g['p2_s']:
        try:
            if g.get('p1_wait_msg'): await cq.bot.delete_message(g['p1'], g['p1_wait_msg'])
            if g.get('p2_wait_msg') and g['p2'] != -1: await cq.bot.delete_message(g['p2'], g['p2_wait_msg'])
        except:
            pass
        await resolve_round(gid, cq.bot)


async def resolve_round(gid, bot):
    g = GAMES[gid]
    c1, c2 = CARDS[g['p1_c']], CARDS[g['p2_c']]

    s_map = {'spd': ('⚡️ Скорость', '⚡️ Скоростную', 'speed'),
             'str': ('💪 Сила', '💪 Силовую', 'strength'),
             'int': ('🧠 Интеллект', '🧠 Интеллектуальную', 'intellect')}

    n1 = "Вы"
    n2 = g['n2'] if g['p2'] == -1 else get_user(g['p2'])[2]
    my_name = get_user(g['p1'])[2]

    val1, val2 = c1[s_map[g['p1_s']][2]], c2[s_map[g['p2_s']][2]]

    adv = check_advantage(g['p1_s'], g['p2_s'])

    m1, m2 = 1.0, 1.0
    bonus_txt_1, bonus_txt_2 = "", ""

    if adv == 1:
        m2 = 0.9
        bonus_txt_1 = f"{s_map[g['p2_s']][0]} -10% ↘️"
        bonus_txt_2 = f"{s_map[g['p2_s']][0]} -10% ↘️"
    elif adv == -1:
        m1 = 0.9
        bonus_txt_1 = f"{s_map[g['p1_s']][0]} -10% ↘️"
        bonus_txt_2 = f"{s_map[g['p1_s']][0]} -10% ↘️"

    f1, f2 = int(val1 * m1), int(val2 * m2)

    if f1 > f2:
        g['score1'] += 1
        winner_name = my_name
    elif f2 > f1:
        g['score2'] += 1
        winner_name = n2
    else:
        winner_name = "Ничья"

    def format_text(p_name, e_name, score_p, score_e, p_s, e_s, p_val, e_val, p_final, e_final, b_txt):
        t = (f"⬆️ Ваша карта | Карта врага ⬆️\nРаунд - {g['round']}\n\n"
             f"Счет:\n{p_name} (я)🧩 - {score_p}\n{e_name} (противник)🧩 - {score_e}\n\n"
             f"⚔️ Вы совершаете {s_map[p_s][1]} атаку\nУровень атаки: {p_val}\n\n"
             f"🛡️ Противник ставит {s_map[e_s][1]} защиту\nУровень защиты: {e_val}\n\n")
        if adv != 0: t += f"Бонус\n{b_txt}\n\n"
        t += (f"Итоговый уровень атаки {s_map[p_s][0].split()[0]} : {p_final}\n"
              f"Итоговый уровень защиты {s_map[e_s][0].split()[0]}: {e_final}\n\n")
        t += f"Раунд завершился в ничью!" if winner_name == "Ничья" else f"Раунд выиграл {winner_name}🧩"
        return t

    txt1 = format_text(my_name, n2, g['score1'], g['score2'], g['p1_s'], g['p2_s'], val1, val2, f1, f2, bonus_txt_1)
    media1 = [types.InputMediaPhoto(media=c1['file_id'], caption=txt1), types.InputMediaPhoto(media=c2['file_id'])]
    await bot.send_media_group(g['p1'], media=media1)
    if g['p2'] != -1:
        txt2 = format_text(n2, my_name, g['score2'], g['score1'], g['p2_s'], g['p1_s'], val2, val1, f2, f1, bonus_txt_2)
        media2 = [types.InputMediaPhoto(media=c2['file_id'], caption=txt2), types.InputMediaPhoto(media=c1['file_id'])]
        await bot.send_media_group(g['p2'], media=media2)

    g['round'] += 1
    g['p1_c'] = g['p2_c'] = g['p1_s'] = g['p2_s'] = None

    if g['round'] > 5:
        await finish_game(gid, bot)
    else:
        await asyncio.sleep(2)
        await send_card_choice(g['p1'], g['d1'], gid, bot)
        if g['p2'] != -1: await send_card_choice(g['p2'], g['d2'], gid, bot)


async def finish_game(gid, bot):
    g = GAMES.pop(gid)
    p1, p2, s1, s2 = g['p1'], g['p2'], g['score1'], g['score2']

    def apply_res(uid, is_win, is_draw):
        if uid == -1: return 0, 0
        pts = 3 if is_win else (1 if is_draw else -2)
        bc = 3 if is_win else 1
        db_exec(f"UPDATE users SET rank_points = MAX(0, rank_points + {pts}), battlecoin = battlecoin + {bc}, " +
                ("wins = wins + 1" if is_win else ("draws = draws + 1" if is_draw else "losses = losses + 1")) +
                " WHERE id = ?", (uid,))
        return pts, bc

    draw = (s1 == s2)
    r1 = apply_res(p1, s1 > s2, draw)
    r2 = apply_res(p2, s2 > s1, draw)

    my_name = get_user(p1)[2]
    n2 = g['n2'] if p2 == -1 else get_user(p2)[2]

    await bot.send_message(p1, f"Игра окончена!\nСчет: {my_name} {s1} - {s2} {n2}\nНаграда: {r1[0]}🏅, {r1[1]}🪙")
    if p2 != -1:
        await bot.send_message(p2, f"Игра окончена!\nСчет: {n2} {s2} - {s1} {my_name}\nНаграда: {r2[0]}🏅, {r2[1]}🪙")

     #============ КЛАН БАНДЫ ==============



    # ============ АДМИН И ПРОМО ============
@router.message(
    Command(commands=["give_attempts", "give_card", "give_money", "give_title", "give_background", "create_promo"]))
async def admin_cmds(msg: types.Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS: return
    args = msg.text.split()
    cmd = args[0]

    if cmd == "/create_promo":
        await state.set_state(PromoState.waiting_for_promo_data)
        await msg.answer(
            "Отправь данные промокода в формате:\n[КОД] [ТИП: krw/atm/card] [ЗНАЧЕНИЕ] [КОЛ-ВО ИСПОЛЬЗОВАНИЙ]\nПример: LOOKISM krw 500 10")
        return

    if len(args) < 3: return await msg.answer("Ошибка аргументов.")
    uid, val = int(args[1]), args[2]

    if cmd == "/give_attempts":
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (int(val), uid))
    elif cmd == "/give_money":
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (int(val), uid))
    elif cmd == "/give_card":
        db_exec("INSERT INTO cards_inv (user_id, card_id) VALUES (?, ?)", (uid, val))
    elif cmd == "/give_title":
        db_exec("INSERT INTO titles_inv (user_id, title_id) VALUES (?, ?)", (uid, val))
    elif cmd == "/give_background":
        db_exec("INSERT INTO bgs_inv (user_id, bg_id) VALUES (?, ?)", (uid, val))
    await msg.answer(f"✅ Выдано пользователю {uid}!")


@router.message(PromoState.waiting_for_promo_data)
async def create_promo(msg: types.Message, state: FSMContext):
    args = msg.text.split()
    if len(args) != 4: return await msg.answer("Неверный формат.")
    db_exec("INSERT INTO promos (code, p_type, val, uses) VALUES (?, ?, ?, ?)",
            (args[0], args[1], args[2], int(args[3])))
    await state.clear()
    await msg.answer(f"✅ Промокод {args[0]} создан!")


@router.message(Command("promo"))
async def use_promo(msg: types.Message):
    args = msg.text.split()
    if len(args) < 2: return await msg.answer("Введи промокод: /promo КОД")
    code = args[1]

    p = db_exec("SELECT p_type, val, uses FROM promos WHERE code = ?", (code,), fetch=True)
    if not p or p[2] <= 0: return await msg.answer("Промокод недействителен.")

    db_exec("UPDATE promos SET uses = uses - 1 WHERE code = ?", (code,))

    if p[0] == 'krw':
        db_exec("UPDATE users SET krw = krw + ? WHERE id = ?", (int(p[1]), msg.from_user.id))
    elif p[0] == 'atm':
        db_exec("UPDATE users SET attempts = attempts + ? WHERE id = ?", (int(p[1]), msg.from_user.id))
    elif p[0] == 'card':
        give_card_to_user(msg.from_user.id, p[1])
    await msg.answer("✅ Промокод активирован!")

# ================== ЗАПУСК ==================
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