#!/usr/bin/env python3
"""
VPN Telegram Bot
Сервис для предоставления WireGuard конфигов с системой оплаты
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound
import aiohttp

from config import BOT_TOKEN, BOT_TOKEN_2
from database import init_db, async_session, BotInstance
from database.models import BotInstance
from handlers import user_router, admin_router
from services.scheduler import SchedulerService
from services.uptime_monitor import init_monitor
from sqlalchemy import select

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RetryAiohttpSession(AiohttpSession):
    """Сессия с автоматическими retry при сетевых ошибках"""
    
    def __init__(self, max_retries: int = 5, retry_delay: float = 2.0, **kwargs):
        super().__init__(**kwargs)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    async def make_request(self, bot, method, timeout=None):
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return await super().make_request(bot, method, timeout=timeout)
            except (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound) as e:
                # Не повторяем ошибки API Telegram — они не сетевые
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (attempt + 1)
                    logger.warning(f"Сетевая ошибка (попытка {attempt + 1}/{self.max_retries}): {e}. Повтор через {delay}с...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Все {self.max_retries} попыток не удались: {e}")
        raise last_error


async def register_bot_in_db(bot: Bot):
    """Регистрирует бота в БД если его там нет"""
    try:
        bot_info = await bot.get_me()
        async with async_session() as session:
            stmt = select(BotInstance).where(BotInstance.bot_id == bot_info.id)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()
            
            if not existing:
                # Получаем токен из бота
                token = bot.token
                new_bot = BotInstance(
                    bot_id=bot_info.id,
                    token=token,
                    username=bot_info.username,
                    name=bot_info.first_name,
                    is_active=True
                )
                session.add(new_bot)
                await session.commit()
                logger.info(f"Бот @{bot_info.username} зарегистрирован в БД")
            else:
                # Обновляем username если изменился
                if existing.username != bot_info.username:
                    existing.username = bot_info.username
                    await session.commit()
    except Exception as e:
        logger.error(f"Ошибка регистрации бота в БД: {e}")


async def load_bots_from_db() -> list:
    """Загружает дополнительных ботов из БД"""
    bots = []
    try:
        async with async_session() as session:
            stmt = select(BotInstance).where(BotInstance.is_active == True)
            result = await session.execute(stmt)
            bot_instances = result.scalars().all()
            
            for bi in bot_instances:
                try:
                    bot_session = RetryAiohttpSession(max_retries=5, retry_delay=2.0)
                    bot = Bot(
                        token=bi.token,
                        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
                        session=bot_session
                    )
                    # Проверяем что токен валидный
                    bot_info = await bot.get_me()
                    if bot_info.id == bi.bot_id:
                        bots.append(bot)
                        logger.info(f"Загружен бот из БД: @{bot_info.username}")
                except Exception as e:
                    logger.error(f"Ошибка загрузки бота {bi.username}: {e}")
    except Exception as e:
        logger.error(f"Ошибка загрузки ботов из БД: {e}")
    return bots


async def main():
    logger.info("Инициализация базы данных...")
    await init_db()
    
    # Создаём сессию с retry логикой
    session = RetryAiohttpSession(
        max_retries=5,
        retry_delay=2.0
    )
    logger.info("Используется сессия с retry (5 попыток)")
    
    # Основной бот (из .env)
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
        session=session
    )
    
    # Регистрируем основной бот в БД
    await register_bot_in_db(bot)
    
    # Список ботов для polling
    bots = [bot]
    
    # Второй бот из .env (если токен указан)
    if BOT_TOKEN_2:
        session2 = RetryAiohttpSession(max_retries=5, retry_delay=2.0)
        bot2 = Bot(
            token=BOT_TOKEN_2,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
            session=session2
        )
        await register_bot_in_db(bot2)
        bots.append(bot2)
        logger.info("Второй бот из .env подключен")
    
    # Загружаем дополнительных ботов из БД (добавленных через админку)
    db_bots = await load_bots_from_db()
    # Добавляем только тех, кого ещё нет в списке
    existing_ids = {(await b.get_me()).id for b in bots}
    for db_bot in db_bots:
        db_bot_info = await db_bot.get_me()
        if db_bot_info.id not in existing_ids:
            bots.append(db_bot)
            existing_ids.add(db_bot_info.id)
    
    dp = Dispatcher()
    
    # Middleware для проверки блокировки пользователей
    from aiogram import BaseMiddleware
    from aiogram.types import Update, Message, CallbackQuery
    from typing import Callable, Dict, Any, Awaitable
    
    class BlockedUserMiddleware(BaseMiddleware):
        async def __call__(
            self,
            handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
            event: Update,
            data: Dict[str, Any]
        ) -> Any:
            # Получаем telegram_id пользователя
            user_id = None
            if isinstance(event, Message) and event.from_user:
                user_id = event.from_user.id
            elif isinstance(event, CallbackQuery) and event.from_user:
                user_id = event.from_user.id
            
            if user_id:
                # Проверяем блокировку админом (is_banned)
                async with async_session() as session:
                    from database import User
                    stmt = select(User).where(User.telegram_id == user_id, User.is_banned == True)
                    result = await session.execute(stmt)
                    banned_user = result.scalar_one_or_none()
                    
                    if banned_user:
                        # Пользователь заблокирован админом
                        if isinstance(event, Message):
                            await event.answer("а всё")
                        elif isinstance(event, CallbackQuery):
                            await event.answer("а всё", show_alert=True)
                        return  # Не передаём дальше
            
            return await handler(event, data)
    
    # Регистрируем middleware
    dp.message.middleware(BlockedUserMiddleware())
    dp.callback_query.middleware(BlockedUserMiddleware())
    
    # Глобальный обработчик ошибок
    @dp.error()
    async def error_handler(event, exception):
        """Обработчик типичных ошибок Telegram"""
        if isinstance(exception, TelegramBadRequest):
            msg = str(exception)
            # Игнорируем "message is not modified" — это не ошибка
            if "message is not modified" in msg:
                logger.debug(f"Игнорируем: {msg}")
                return True
            # Игнорируем "query is too old" — пользователь нажал кнопку слишком поздно
            if "query is too old" in msg:
                logger.debug(f"Игнорируем: {msg}")
                return True
        if isinstance(exception, (TelegramForbiddenError, TelegramNotFound)):
            # Пользователь заблокировал бота или чат не найден
            logger.debug(f"Чат недоступен: {exception}")
            return True
        # Остальные ошибки логируем
        logger.error(f"Необработанная ошибка: {exception}", exc_info=True)
        return True
    
    # admin_router первый — чтобы FSM-состояния админки обрабатывались раньше AI-ассистента
    dp.include_router(admin_router)
    dp.include_router(user_router)
    
    # Планировщик работает только с основным ботом
    scheduler = SchedulerService(bot)
    scheduler.start()
    
    # Мониторинг uptime
    uptime_monitor = init_monitor(bot)
    uptime_monitor.start()
    
    # Логирование в Telegram
    from services.telegram_logger import setup_telegram_logging, TelegramLogHandler
    setup_telegram_logging(bot)
    
    logger.info(f"Запуск бота... (ботов: {len(bots)})")
    
    # Бесконечный цикл с перезапуском при критических ошибках
    while True:
        try:
            await dp.start_polling(*bots, allowed_updates=dp.resolve_used_update_types())
            break
        except Exception as e:
            logger.error(f"Критическая ошибка polling: {e}. Перезапуск через 10с...")
            await asyncio.sleep(10)
    
    scheduler.stop()
    uptime_monitor.stop()
    TelegramLogHandler.stop()
    for b in bots:
        await b.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
