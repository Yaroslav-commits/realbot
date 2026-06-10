import json
from cards import TITLES

titles_list = []

# Перебираем словарь TITLES.
# Так как здесь значение — это просто строка, мы сами собираем из него словарик
for title_id, title_name in TITLES.items():
    titles_list.append({
        "id": title_id,
        "name": title_name
    })

# Создаем файл для сайта
with open("title.json", "w", encoding="utf-8") as f:
    json.dump(titles_list, f, ensure_ascii=False, indent=4)

print("Готово! Файл title.json успешно создан.")