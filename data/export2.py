import json
from cards import BGS

bgs_list = []

# Перебираем словарь BGS и добавляем id внутрь словаря фона
for bg_id, bg_data in BGS.items():
    bg_data["id"] = bg_id  # сохраняем системное имя (например, admin или lookism_1)
    bgs_list.append(bg_data)

# Создаем файл для сайта
with open("bgs.json", "w", encoding="utf-8") as f:
    json.dump(bgs_list, f, ensure_ascii=False, indent=4)

print("Готово! Файл bgs.json успешно создан.")