import json
from cards import CARDS

cards_list = []

# Перебираем твой словарь CARDS и превращаем в список
for card_id, card_data in CARDS.items():
    card_data["id"] = card_id  # сохраняем системное имя (например, daniel_fat)
    cards_list.append(card_data)

# Создаем файл для сайта
with open("cards.json", "w", encoding="utf-8") as f:
    json.dump(cards_list, f, ensure_ascii=False, indent=4)

print("Готово! Файл cards.json успешно создан.")