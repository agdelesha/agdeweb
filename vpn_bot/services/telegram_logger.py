"""
Сервис для отправки логов в Telegram чаты/каналы
"""
import logging
import asyncio
from typing import Optional, List
from datetime import datetime
from aiogram import Bot
from sqlalchemy import select

from database import async_session
from database.models import LogChannel


class TelegramLogHandler(logging.Handler):
    """Handler для отправки логов в Telegram"""
    
    _instance = None
    _bot: Optional[Bot] = None
    _queue: asyncio.Queue = None
    _task: Optional[asyncio.Task] = None
    _running: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._queue = asyncio.Queue()
        return cls._instance
    
    @classmethod
    def set_bot(cls, bot: Bot):
        """Установить бота для отправки логов"""
        cls._bot = bot
    
    @classmethod
    def start(cls):
        """Запустить обработку очереди логов"""
        if cls._running:
            return
        cls._running = True
        cls._task = asyncio.create_task(cls._process_queue())
    
    @classmethod
    def stop(cls):
        """Остановить обработку"""
        cls._running = False
        if cls._task:
            cls._task.cancel()
    
    @classmethod
    async def _process_queue(cls):
        """Обработка очереди логов"""
        while cls._running:
            try:
                # Ждём сообщение из очереди
                record = await asyncio.wait_for(cls._queue.get(), timeout=1.0)
                await cls._send_log(record)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Не логируем ошибки отправки логов чтобы избежать рекурсии
                print(f"Error sending log: {e}")
    
    @classmethod
    async def _send_log(cls, record: logging.LogRecord):
        """Отправить лог в активные каналы"""
        if not cls._bot:
            return
        
        try:
            async with async_session() as session:
                stmt = select(LogChannel).where(LogChannel.is_active == True)
                result = await session.execute(stmt)
                channels = result.scalars().all()
                
                if not channels:
                    return
                
                # Форматируем сообщение
                level_emoji = {
                    'DEBUG': '🔍',
                    'INFO': 'ℹ️',
                    'WARNING': '⚠️',
                    'ERROR': '❌',
                    'CRITICAL': '🔥'
                }
                
                level_priority = {'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3, 'CRITICAL': 4}
                
                emoji = level_emoji.get(record.levelname, '📝')
                timestamp = datetime.now().strftime('%H:%M:%S')
                
                # Сокращаем имя логгера
                logger_name = record.name
                if '.' in logger_name:
                    logger_name = logger_name.split('.')[-1]
                
                message = (
                    f"{emoji} `{timestamp}` *{record.levelname}*\n"
                    f"📦 `{logger_name}`\n"
                    f"```\n{record.getMessage()[:3500]}```"
                )
                
                for channel in channels:
                    # Проверяем уровень лога
                    channel_level = level_priority.get(channel.log_level, 1)
                    record_level = level_priority.get(record.levelname, 1)
                    
                    if record_level < channel_level:
                        continue
                    
                    try:
                        await cls._bot.send_message(
                            channel.chat_id,
                            message,
                            parse_mode="Markdown"
                        )
                    except Exception:
                        # Игнорируем ошибки отправки в конкретный канал
                        pass
                        
        except Exception:
            pass
    
    def emit(self, record: logging.LogRecord):
        """Добавить запись в очередь"""
        # Игнорируем логи от aiogram и aiohttp чтобы не спамить
        if record.name.startswith(('aiogram', 'aiohttp', 'asyncio', 'asyncssh')):
            return
        
        try:
            self._queue.put_nowait(record)
        except:
            pass


# === Функции для управления каналами логов ===

async def get_log_channels() -> List[LogChannel]:
    """Получить все каналы логов"""
    async with async_session() as session:
        stmt = select(LogChannel).order_by(LogChannel.created_at.desc())
        result = await session.execute(stmt)
        return result.scalars().all()


async def add_log_channel(chat_id: int, title: str = None, log_level: str = "INFO") -> LogChannel:
    """Добавить канал для логов"""
    async with async_session() as session:
        # Проверяем существует ли уже
        stmt = select(LogChannel).where(LogChannel.chat_id == chat_id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            existing.is_active = True
            existing.title = title or existing.title
            existing.log_level = log_level
            await session.commit()
            return existing
        
        channel = LogChannel(
            chat_id=chat_id,
            title=title,
            log_level=log_level,
            is_active=True
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        return channel


async def remove_log_channel(channel_id: int) -> bool:
    """Удалить канал логов"""
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            await session.delete(channel)
            await session.commit()
            return True
        return False


async def toggle_log_channel(channel_id: int) -> Optional[bool]:
    """Переключить активность канала"""
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            channel.is_active = not channel.is_active
            await session.commit()
            return channel.is_active
        return None


async def set_log_level(channel_id: int, level: str) -> bool:
    """Установить уровень логов для канала"""
    if level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        return False
    
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            channel.log_level = level
            await session.commit()
            return True
        return False


def setup_telegram_logging(bot: Bot, level: int = logging.INFO):
    """Настроить отправку логов в Telegram"""
    handler = TelegramLogHandler()
    handler.setLevel(level)
    TelegramLogHandler.set_bot(bot)
    TelegramLogHandler.start()
    
    # Добавляем handler к корневому логгеру
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    
    return handler
