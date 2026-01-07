"""Сервис для работы с настройками бота"""
from typing import Optional
from sqlalchemy import select

from database import async_session, Settings, BotInstance


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


async def get_bot_instance(bot_id: int) -> Optional[BotInstance]:
    """Получает экземпляр бота из БД"""
    async with async_session() as session:
        stmt = select(BotInstance).where(BotInstance.bot_id == bot_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def is_password_required(bot_id: int = None) -> bool:
    """Проверяет, требуется ли пароль (для конкретного бота или глобально)"""
    if bot_id:
        bot = await get_bot_instance(bot_id)
        if bot and bot.password:
            return True
        return False
    return await get_setting("password_enabled") == "1"


async def get_bot_password(bot_id: int = None) -> Optional[str]:
    """Получает пароль бота"""
    if bot_id:
        bot = await get_bot_instance(bot_id)
        if bot:
            return bot.password
    return await get_setting("bot_password")


async def is_channel_required(bot_id: int = None) -> bool:
    """Проверяет, требуется ли подписка на канал"""
    if bot_id:
        bot = await get_bot_instance(bot_id)
        if bot and bot.channel:
            return True
        return False
    return await get_setting("channel_required") == "1"


async def get_channel_name(bot_id: int = None) -> Optional[str]:
    """Получает название канала для подписки"""
    if bot_id:
        bot = await get_bot_instance(bot_id)
        if bot:
            return bot.channel
    return await get_setting("channel_name")


async def is_phone_required(bot_id: int = None) -> bool:
    """Проверяет, требуется ли запрос номера телефона"""
    if bot_id:
        bot = await get_bot_instance(bot_id)
        if bot:
            return bot.require_phone
        return False
    return await get_setting("phone_required") != "0"


async def get_max_configs(bot_id: int = None) -> int:
    """Получает лимит конфигов"""
    if bot_id:
        bot = await get_bot_instance(bot_id)
        if bot:
            return bot.max_configs
    val = await get_setting("max_configs")
    return int(val) if val else 3


async def is_config_approval_required() -> bool:
    """Проверяет, требуется ли подтверждение админа для доп. конфига"""
    return await get_setting("config_approval_required") != "0"


async def get_all_bots() -> list:
    """Получает список всех ботов"""
    async with async_session() as session:
        stmt = select(BotInstance).order_by(BotInstance.id)
        result = await session.execute(stmt)
        return result.scalars().all()


async def add_bot_instance(token: str, bot_id: int, username: str, name: str) -> BotInstance:
    """Добавляет нового бота в БД"""
    async with async_session() as session:
        bot = BotInstance(
            bot_id=bot_id,
            token=token,
            username=username,
            name=name,
            is_active=True
        )
        session.add(bot)
        await session.commit()
        await session.refresh(bot)
        return bot


async def update_bot_setting(bot_id: int, key: str, value) -> bool:
    """Обновляет настройку конкретного бота"""
    async with async_session() as session:
        stmt = select(BotInstance).where(BotInstance.bot_id == bot_id)
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()
        
        if bot:
            setattr(bot, key, value)
            await session.commit()
            return True
        return False


async def delete_bot_instance(bot_id: int) -> bool:
    """Удаляет бота из БД"""
    async with async_session() as session:
        stmt = select(BotInstance).where(BotInstance.bot_id == bot_id)
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()
        
        if bot:
            await session.delete(bot)
            await session.commit()
            return True
        return False


# ===== УПРАВЛЕНИЕ ЦЕНАМИ =====

async def get_prices() -> dict:
    """Получает все цены из БД"""
    prices = {
        "trial_days": 3,
        "price_30": 200,
        "price_90": 400,
        "price_180": 600,
    }
    
    # Загружаем из БД если есть
    trial = await get_setting("trial_days")
    if trial:
        prices["trial_days"] = int(trial)
    
    p30 = await get_setting("price_30")
    if p30:
        prices["price_30"] = int(p30)
    
    p90 = await get_setting("price_90")
    if p90:
        prices["price_90"] = int(p90)
    
    p180 = await get_setting("price_180")
    if p180:
        prices["price_180"] = int(p180)
    
    return prices


async def set_price(key: str, value: int):
    """Устанавливает цену"""
    await set_setting(key, str(value))


async def get_referral_discount_percent() -> int:
    """Получает % скидки для рефералов (по умолчанию 50%)"""
    value = await get_setting("referral_discount_percent")
    return int(float(value)) if value else 50
