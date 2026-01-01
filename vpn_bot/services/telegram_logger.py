"""
Сервис для отправки логов в Telegram чаты/каналы
"""
import logging
import asyncio
import subprocess
from typing import Optional, List
from datetime import datetime
from aiogram import Bot
from sqlalchemy import select

from database import async_session
from database.models import LogChannel


# Кэш настроек каналов (чтобы не дёргать БД на каждый лог)
_channels_cache: List[LogChannel] = []
_cache_time: float = 0
CACHE_TTL = 30  # секунд


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
    async def _get_channels_cached(cls) -> List[LogChannel]:
        """Получить каналы с кэшированием"""
        global _channels_cache, _cache_time
        import time
        
        now = time.time()
        if now - _cache_time > CACHE_TTL or not _channels_cache:
            try:
                async with async_session() as session:
                    stmt = select(LogChannel).where(LogChannel.is_active == True)
                    result = await session.execute(stmt)
                    _channels_cache = result.scalars().all()
                    _cache_time = now
            except:
                pass
        return _channels_cache
    
    @classmethod
    async def _send_log(cls, record: logging.LogRecord):
        """Отправить лог в активные каналы"""
        if not cls._bot:
            return
        
        try:
            channels = await cls._get_channels_cached()
            if not channels:
                return
            
            # Определяем тип лога
            is_aiogram = record.name.startswith(('aiogram', 'aiohttp', 'asyncio'))
            is_system = getattr(record, 'is_system_log', False)
            
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
            
            # Добавляем метку типа лога
            if is_system:
                type_label = "🖥 SYS"
            elif is_aiogram:
                type_label = "🤖 NET"
            else:
                type_label = "📦 BOT"
            
            message = (
                f"{emoji} `{timestamp}` *{record.levelname}* {type_label}\n"
                f"`{logger_name}`\n"
                f"```\n{record.getMessage()[:3500]}```"
            )
            
            for channel in channels:
                # Проверяем уровень лога
                channel_level = level_priority.get(getattr(channel, 'log_level', 'INFO'), 1)
                record_level = level_priority.get(record.levelname, 1)
                
                if record_level < channel_level:
                    continue
                
                # Проверяем тип лога
                bot_logs = getattr(channel, 'bot_logs', True)
                system_logs = getattr(channel, 'system_logs', False)
                aiogram_logs = getattr(channel, 'aiogram_logs', False)
                
                if is_system and not system_logs:
                    continue
                if is_aiogram and not aiogram_logs:
                    continue
                if not is_system and not is_aiogram and not bot_logs:
                    continue
                
                try:
                    await cls._bot.send_message(
                        channel.chat_id,
                        message,
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
                    
        except Exception:
            pass
    
    def emit(self, record: logging.LogRecord):
        """Добавить запись в очередь"""
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


async def toggle_log_type(channel_id: int, log_type: str) -> Optional[bool]:
    """Переключить тип логов для канала"""
    global _channels_cache, _cache_time
    
    if log_type not in ('bot_logs', 'system_logs', 'aiogram_logs'):
        return None
    
    async with async_session() as session:
        stmt = select(LogChannel).where(LogChannel.id == channel_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        
        if channel:
            current = getattr(channel, log_type, False)
            setattr(channel, log_type, not current)
            await session.commit()
            _cache_time = 0  # Сбрасываем кэш
            return not current
        return None


class JournaldLogReader:
    """Чтение серверных логов из journald"""
    
    _instance = None
    _task: Optional[asyncio.Task] = None
    _running: bool = False
    _handler: Optional[TelegramLogHandler] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def start(cls, handler: TelegramLogHandler):
        """Запустить чтение journald"""
        if cls._running:
            return
        cls._running = True
        cls._handler = handler
        cls._task = asyncio.create_task(cls._read_journald())
    
    @classmethod
    def stop(cls):
        """Остановить чтение"""
        cls._running = False
        if cls._task:
            cls._task.cancel()
    
    @classmethod
    async def _read_journald(cls):
        """Читать логи из journald в реальном времени"""
        try:
            # Запускаем journalctl -f для сервиса vpn-bot
            process = await asyncio.create_subprocess_exec(
                'journalctl', '-f', '-u', 'vpn-bot', '-n', '0', '--no-pager',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            while cls._running:
                try:
                    line = await asyncio.wait_for(
                        process.stdout.readline(),
                        timeout=1.0
                    )
                    if line:
                        line_text = line.decode('utf-8', errors='ignore').strip()
                        if line_text and cls._handler:
                            # Создаём запись лога
                            record = logging.LogRecord(
                                name='journald',
                                level=cls._parse_level(line_text),
                                pathname='',
                                lineno=0,
                                msg=line_text,
                                args=(),
                                exc_info=None
                            )
                            record.is_system_log = True
                            cls._handler._queue.put_nowait(record)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception:
                    continue
            
            process.terminate()
        except Exception as e:
            print(f"Journald reader error: {e}")
    
    @classmethod
    def _parse_level(cls, line: str) -> int:
        """Определить уровень лога по содержимому"""
        line_lower = line.lower()
        if 'error' in line_lower or 'exception' in line_lower or 'traceback' in line_lower:
            return logging.ERROR
        elif 'warning' in line_lower or 'warn' in line_lower:
            return logging.WARNING
        elif 'debug' in line_lower:
            return logging.DEBUG
        return logging.INFO


def setup_telegram_logging(bot: Bot, level: int = logging.DEBUG):
    """Настроить отправку логов в Telegram"""
    handler = TelegramLogHandler()
    handler.setLevel(level)
    TelegramLogHandler.set_bot(bot)
    TelegramLogHandler.start()
    
    # Добавляем handler к корневому логгеру
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    
    # Запускаем чтение journald
    JournaldLogReader.start(handler)
    
    return handler


def invalidate_channels_cache():
    """Сбросить кэш каналов"""
    global _cache_time
    _cache_time = 0
