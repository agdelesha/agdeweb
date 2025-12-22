"""Сервис для работы с настройками бота"""
from typing import Optional
from sqlalchemy import select

from database import async_session, Settings


async def get_setting(key: str) -> Optional[str]:
    """Получает настройку из БД"""
    async with async_session() as session:
        stmt = select(Settings).where(Settings.key == key)
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        return setting.value if setting else None


async def set_setting(key: str, value: str):
    """Устанавливает настройку в БД"""
    async with async_session() as session:
        stmt = select(Settings).where(Settings.key == key)
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            setting = Settings(key=key, value=value)
            session.add(setting)
        await session.commit()


async def is_password_required() -> bool:
    """Проверяет, требуется ли пароль"""
    return await get_setting("password_enabled") == "1"


async def get_bot_password() -> Optional[str]:
    """Получает пароль бота"""
    return await get_setting("bot_password")


async def is_channel_required() -> bool:
    """Проверяет, требуется ли подписка на канал"""
    return await get_setting("channel_required") == "1"
