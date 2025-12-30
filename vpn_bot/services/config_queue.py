"""
Сервис очереди конфигов
Управляет очередью пользователей, ожидающих конфиги когда все серверы заполнены
"""

import logging
from datetime import datetime
from typing import Optional, List, Tuple
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from database import async_session, ConfigQueue, User, Server, Config
from database.models import ConfigQueue
from config import ADMIN_ID

logger = logging.getLogger(__name__)


class ConfigQueueService:
    """Сервис управления очередью конфигов"""
    
    @classmethod
    async def add_to_queue(cls, user_id: int, config_name: str) -> ConfigQueue:
        """Добавить пользователя в очередь ожидания"""
        async with async_session() as session:
            queue_item = ConfigQueue(
                user_id=user_id,
                config_name=config_name,
                status="waiting"
            )
            session.add(queue_item)
            await session.commit()
            await session.refresh(queue_item)
            logger.info(f"Пользователь {user_id} добавлен в очередь (конфиг: {config_name})")
            return queue_item
    
    @classmethod
    async def get_waiting_count(cls) -> int:
        """Получить количество ожидающих в очереди"""
        async with async_session() as session:
            result = await session.execute(
                select(func.count(ConfigQueue.id)).where(ConfigQueue.status == "waiting")
            )
            return result.scalar() or 0
    
    @classmethod
    async def get_waiting_queue(cls) -> List[ConfigQueue]:
        """Получить всех ожидающих в очереди"""
        async with async_session() as session:
            result = await session.execute(
                select(ConfigQueue)
                .where(ConfigQueue.status == "waiting")
                .options(selectinload(ConfigQueue.user))
                .order_by(ConfigQueue.created_at.asc())
            )
            return list(result.scalars().all())
    
    @classmethod
    async def is_user_in_queue(cls, user_id: int) -> bool:
        """Проверить, есть ли пользователь уже в очереди"""
        async with async_session() as session:
            result = await session.execute(
                select(ConfigQueue).where(
                    ConfigQueue.user_id == user_id,
                    ConfigQueue.status == "waiting"
                )
            )
            return result.scalar_one_or_none() is not None
    
    @classmethod
    async def get_user_queue_position(cls, user_id: int) -> Optional[int]:
        """Получить позицию пользователя в очереди"""
        async with async_session() as session:
            # Получаем все ожидающие записи в порядке создания
            result = await session.execute(
                select(ConfigQueue)
                .where(ConfigQueue.status == "waiting")
                .order_by(ConfigQueue.created_at.asc())
            )
            queue = result.scalars().all()
            
            for i, item in enumerate(queue, 1):
                if item.user_id == user_id:
                    return i
            return None
    
    @classmethod
    async def process_queue(cls, bot, max_to_process: int = 10) -> Tuple[int, int]:
        """
        Обработать очередь - создать конфиги для ожидающих если есть свободные места
        Возвращает (успешно_обработано, ошибок)
        """
        from services.wireguard_multi import WireGuardMultiService, send_config_file
        
        processed = 0
        errors = 0
        
        async with async_session() as session:
            # Получаем ожидающих в порядке очереди
            result = await session.execute(
                select(ConfigQueue)
                .where(ConfigQueue.status == "waiting")
                .options(selectinload(ConfigQueue.user))
                .order_by(ConfigQueue.created_at.asc())
                .limit(max_to_process)
            )
            queue_items = result.scalars().all()
            
            for item in queue_items:
                # Проверяем есть ли свободный сервер
                server = await WireGuardMultiService.get_best_server(session)
                if not server:
                    logger.info("Нет свободных серверов для обработки очереди")
                    break
                
                try:
                    # Помечаем как обрабатываемый
                    item.status = "processing"
                    await session.commit()
                    
                    # Создаём конфиг
                    success, config_data, msg = await WireGuardMultiService.create_config(
                        item.config_name, session
                    )
                    
                    if success and config_data:
                        # Сохраняем конфиг в БД
                        config = Config(
                            user_id=item.user_id,
                            server_id=config_data.server_id,
                            name=item.config_name,
                            public_key=config_data.public_key,
                            preshared_key=config_data.preshared_key,
                            allowed_ips=config_data.allowed_ips,
                            client_ip=config_data.client_ip,
                            is_active=True
                        )
                        session.add(config)
                        
                        # Помечаем как выполненный
                        item.status = "completed"
                        item.processed_at = datetime.utcnow()
                        await session.commit()
                        
                        # Отправляем конфиг пользователю
                        user = item.user
                        if user and user.telegram_id:
                            try:
                                await bot.send_message(
                                    user.telegram_id,
                                    f"🎉 *Отличные новости!*\n\n"
                                    f"Твой конфиг *{item.config_name}* готов!\n"
                                    f"Мы добавили новый сервер и теперь можем тебя подключить.",
                                    parse_mode="Markdown"
                                )
                                await send_config_file(
                                    bot, user.telegram_id, item.config_name, 
                                    config_data, config_data.server_id,
                                    caption="📄 Твой WireGuard конфиг"
                                )
                                processed += 1
                                logger.info(f"Конфиг из очереди выдан пользователю {user.telegram_id}")
                            except Exception as e:
                                logger.error(f"Ошибка отправки конфига пользователю: {e}")
                                processed += 1  # Конфиг создан, просто не отправлен
                    else:
                        # Ошибка создания - возвращаем в очередь
                        item.status = "waiting"
                        await session.commit()
                        errors += 1
                        logger.error(f"Ошибка создания конфига из очереди: {msg}")
                        
                except Exception as e:
                    item.status = "waiting"
                    await session.commit()
                    errors += 1
                    logger.error(f"Ошибка обработки очереди: {e}")
        
        return processed, errors
    
    @classmethod
    async def cancel_user_queue(cls, user_id: int) -> bool:
        """Отменить ожидание пользователя в очереди"""
        async with async_session() as session:
            result = await session.execute(
                select(ConfigQueue).where(
                    ConfigQueue.user_id == user_id,
                    ConfigQueue.status == "waiting"
                )
            )
            item = result.scalar_one_or_none()
            if item:
                item.status = "cancelled"
                await session.commit()
                return True
            return False
    
    @classmethod
    async def notify_admin_no_servers(cls, bot, user_telegram_id: int, username: str = None):
        """Уведомить админа о нехватке серверов"""
        waiting_count = await cls.get_waiting_count()
        
        user_info = f"@{username}" if username else f"ID: {user_telegram_id}"
        
        try:
            await bot.send_message(
                ADMIN_ID,
                f"⚠️ *Внимание! Все серверы заполнены!*\n\n"
                f"Пользователь {user_info} добавлен в очередь ожидания.\n"
                f"👥 Всего в очереди: *{waiting_count}*\n\n"
                f"Добавьте новый сервер или увеличьте лимит клиентов на существующих.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления админа: {e}")


async def check_and_process_queue(bot):
    """Проверить и обработать очередь (вызывается при добавлении/активации сервера)"""
    waiting = await ConfigQueueService.get_waiting_count()
    if waiting > 0:
        processed, errors = await ConfigQueueService.process_queue(bot)
        if processed > 0:
            logger.info(f"Обработано из очереди: {processed}, ошибок: {errors}")
            # Уведомляем админа
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"✅ *Очередь обработана*\n\n"
                    f"Выдано конфигов: {processed}\n"
                    f"Ошибок: {errors}\n"
                    f"Осталось в очереди: {waiting - processed}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления админа: {e}")
