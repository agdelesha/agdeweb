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

from config import BOT_TOKEN
from database import init_db
from handlers import user_router, admin_router
from services.scheduler import SchedulerService

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


async def main():
    logger.info("Инициализация базы данных...")
    await init_db()
    
    # Создаём сессию с retry логикой
    session = RetryAiohttpSession(
        max_retries=5,
        retry_delay=2.0
    )
    logger.info("Используется сессия с retry (5 попыток)")
    
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
        session=session
    )
    
    dp = Dispatcher()
    
    dp.include_router(user_router)
    dp.include_router(admin_router)
    
    scheduler = SchedulerService(bot)
    scheduler.start()
    
    logger.info("Запуск бота...")
    
    # Бесконечный цикл с перезапуском при критических ошибках
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            break
        except Exception as e:
            logger.error(f"Критическая ошибка polling: {e}. Перезапуск через 10с...")
            await asyncio.sleep(10)
    
    scheduler.stop()
    await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
