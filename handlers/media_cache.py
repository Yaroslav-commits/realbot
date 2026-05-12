import asyncio
import json
import os
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile


FILE_IDS_CACHE = "file_ids.json"
_media_cache_lock = asyncio.Lock()


def _normalize_media_key(file_path: str) -> str:
    """Делаем ключ одинаковым для Windows/Linux путей."""
    return os.path.normpath(file_path).replace("\\", "/")


def _load_media_cache() -> dict[str, str]:
    if not os.path.exists(FILE_IDS_CACHE):
        return {}

    try:
        with open(FILE_IDS_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    # Оставляем только пары строка -> строка, чтобы битый JSON не ломал отправку.
    return {str(k): str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


_media_cache: dict[str, str] = _load_media_cache()


def _save_media_cache() -> None:
    tmp_path = f"{FILE_IDS_CACHE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_media_cache, f, ensure_ascii=False, indent=4)
    os.replace(tmp_path, FILE_IDS_CACHE)


def _is_invalid_file_id_error(error: Exception) -> bool:
    text = str(error).lower()
    return (
        "wrong file identifier" in text
        or "wrong remote file identifier" in text
        or "file_id" in text and "invalid" in text
        or "file is unavailable" in text
        or "file reference expired" in text
    )


async def send_cached_video(bot: Any, chat_id: int, file_path: str, **kwargs: Any):
    """
    Отправляет видео через сохранённый Telegram file_id.

    Как работает:
    1. Ищет file_id по пути файла в file_ids.json.
    2. Если file_id есть — отправляет видео без загрузки локального файла.
    3. Если Telegram вернул ошибку протухшего/битого file_id — удаляет его из кэша.
    4. Загружает локальный файл через FSInputFile, получает новый msg.video.file_id и сохраняет его.
    """
    cache_key = _normalize_media_key(file_path)
    file_id = _media_cache.get(cache_key)

    if file_id:
        try:
            return await bot.send_video(chat_id=chat_id, video=file_id, **kwargs)
        except TelegramBadRequest as e:
            if not _is_invalid_file_id_error(e):
                raise

            async with _media_cache_lock:
                if _media_cache.get(cache_key) == file_id:
                    _media_cache.pop(cache_key, None)
                    _save_media_cache()

    msg = await bot.send_video(chat_id=chat_id, video=FSInputFile(file_path), **kwargs)

    if getattr(msg, "video", None) and getattr(msg.video, "file_id", None):
        async with _media_cache_lock:
            _media_cache[cache_key] = msg.video.file_id
            _save_media_cache()

    return msg
