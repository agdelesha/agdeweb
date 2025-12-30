"""Общие утилиты для бота"""

from database import async_session, BotInstance
from sqlalchemy import select


async def get_bot_settings(bot_id: int) -> dict:
    """Получает настройки конкретного бота по его ID"""
    async with async_session() as session:
        stmt = select(BotInstance).where(BotInstance.bot_id == bot_id)
        result = await session.execute(stmt)
        bot_instance = result.scalar_one_or_none()
        
        if bot_instance:
            return {
                "password": bot_instance.password,
                "channel": bot_instance.channel,
                "require_phone": bot_instance.require_phone,
                "max_configs": bot_instance.max_configs,
                "username": bot_instance.username,
                "name": bot_instance.name
            }
        # Дефолтные настройки если бот не найден
        return {
            "password": None,
            "channel": None,
            "require_phone": False,
            "max_configs": 3,
            "username": None,
            "name": None
        }


async def update_bot_setting(bot_id: int, key: str, value) -> bool:
    """Обновляет настройку конкретного бота"""
    async with async_session() as session:
        stmt = select(BotInstance).where(BotInstance.bot_id == bot_id)
        result = await session.execute(stmt)
        bot_instance = result.scalar_one_or_none()
        
        if bot_instance:
            setattr(bot_instance, key, value)
            await session.commit()
            return True
        return False


def transliterate_ru_to_en(text: str) -> str:
    """Транслитерация русских букв в английские"""
    translit_map = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
        'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
        'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
        'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
        'Ф': 'F', 'Х': 'H', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Sch', 'Ъ': '',
        'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya'
    }
    result = []
    for char in text:
        result.append(translit_map.get(char, char))
    return ''.join(result)
