import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database import async_session, User, Subscription, Config, Server, BotSettings
from services.wireguard import WireGuardService
from services.wireguard_multi import WireGuardMultiService
from services.monitoring import MonitoringService
from config import ADMIN_ID

logger = logging.getLogger(__name__)


async def get_setting(key: str, default: str = None) -> str:
    """Получить значение настройки из БД"""
    async with async_session() as session:
        stmt = select(BotSettings).where(BotSettings.key == key)
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        return setting.value if setting else default


async def set_setting(key: str, value: str):
    """Установить значение настройки в БД"""
    async with async_session() as session:
        stmt = select(BotSettings).where(BotSettings.key == key)
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = value
        else:
            setting = BotSettings(key=key, value=value)
            session.add(setting)
        await session.commit()


class SchedulerService:
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
    
    def start(self):
        self.scheduler.add_job(
            self.check_expiring_subscriptions,
            IntervalTrigger(hours=1),
            id="check_expiring",
            replace_existing=True
        )
        
        self.scheduler.add_job(
            self.disable_expired_configs,
            IntervalTrigger(hours=1),
            id="disable_expired",
            replace_existing=True
        )
        
        self.scheduler.add_job(
            self.check_suspicious_activity,
            IntervalTrigger(hours=6),
            id="check_suspicious",
            replace_existing=True
        )
        
        self.scheduler.start()
        logger.info("Планировщик запущен")
    
    def stop(self):
        self.scheduler.shutdown()
        logger.info("Планировщик остановлен")
    
    async def check_expiring_subscriptions(self):
        logger.info("Проверка истекающих подписок...")
        
        async with async_session() as session:
            three_days_later = datetime.utcnow() + timedelta(days=3)
            
            stmt = select(Subscription).where(
                Subscription.expires_at.isnot(None),
                Subscription.expires_at <= three_days_later,
                Subscription.expires_at > datetime.utcnow(),
                Subscription.notified_3_days == False
            ).options(selectinload(Subscription.user))
            
            result = await session.execute(stmt)
            subscriptions = result.scalars().all()
            
            for sub in subscriptions:
                try:
                    user = sub.user
                    
                    # Проверяем, есть ли у пользователя другая активная подписка (бессрочная или с более поздней датой)
                    active_sub_stmt = select(Subscription).where(
                        Subscription.user_id == user.id,
                        Subscription.id != sub.id,
                        (Subscription.expires_at.is_(None) | (Subscription.expires_at > sub.expires_at))
                    )
                    active_result = await session.execute(active_sub_stmt)
                    has_better_sub = active_result.scalar() is not None
                    
                    if has_better_sub:
                        # У пользователя есть бессрочная или более длительная подписка — не уведомляем
                        sub.notified_3_days = True
                        await session.commit()
                        logger.info(f"Пропускаем уведомление для {user.telegram_id} — есть активная подписка")
                        continue
                    
                    days_left = (sub.expires_at - datetime.utcnow()).days
                    
                    await self.bot.send_message(
                        user.telegram_id,
                        f"⚠️ *Внимание!*\n\n"
                        f"Ваша подписка истекает через {days_left} дн.\n"
                        f"Дата окончания: {sub.expires_at.strftime('%d.%m.%Y')}\n\n"
                        f"Продлите подписку, чтобы не потерять доступ к VPN.",
                        parse_mode="Markdown"
                    )
                    
                    sub.notified_3_days = True
                    await session.commit()
                    
                    logger.info(f"Уведомление отправлено пользователю {user.telegram_id}")
                    
                except (TelegramBadRequest, TelegramForbiddenError) as e:
                    error_msg = str(e)
                    logger.error(f"Ошибка отправки уведомления user_id={user.telegram_id} (@{user.username}): {error_msg}")
                    if "chat not found" in error_msg.lower() or "bot was blocked" in error_msg.lower() or "user is deactivated" in error_msg.lower():
                        await self._handle_inactive_user(user)
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления user_id={user.telegram_id}: {e}")
    
    async def _handle_inactive_user(self, user):
        """Обработка неактивного пользователя (чат не найден / бот заблокирован)"""
        async with async_session() as session:
            stmt = select(User).where(User.id == user.id)
            result = await session.execute(stmt)
            db_user = result.scalar_one_or_none()
            if not db_user:
                return
            
            db_user.failed_notifications += 1
            await session.commit()
            
            user_info = f"@{db_user.username}" if db_user.username else db_user.full_name
            
            if db_user.failed_notifications >= 3:
                auto_delete = await get_setting("auto_delete_inactive", "false")
                
                if auto_delete == "true":
                    # Автоудаление включено
                    await self._delete_user(db_user.id)
                    logger.info(f"Пользователь {user_info} (ID: {db_user.telegram_id}) автоматически удалён (неактивен)")
                else:
                    # Уведомляем админа
                    try:
                        from keyboards.admin_kb import get_inactive_user_kb
                        await self.bot.send_message(
                            ADMIN_ID,
                            f"⚠️ Неактивный пользователь\n\n"
                            f"👤 {user_info}\n"
                            f"🆔 ID: {db_user.telegram_id}\n"
                            f"❌ Неудачных уведомлений: {db_user.failed_notifications}\n\n"
                            f"Пользователь заблокировал бота или удалил аккаунт.",
                            reply_markup=get_inactive_user_kb(db_user.id)
                        )
                    except Exception as e:
                        logger.error(f"Ошибка уведомления админа о неактивном пользователе: {e}")
    
    async def _delete_user(self, user_id: int):
        """Удаление пользователя и его конфигов"""
        async with async_session() as session:
            stmt = select(User).where(User.id == user_id).options(selectinload(User.configs))
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if not user:
                return
            
            # Удаляем конфиги с серверов
            for config in user.configs:
                if config.server_id:
                    server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                    if server:
                        await WireGuardMultiService.delete_config(config.name, server, config.public_key)
                else:
                    await WireGuardService.delete_config(config.name)
            
            await session.delete(user)
            await session.commit()
    
    async def disable_expired_configs(self):
        logger.info("Проверка истекших подписок...")
        
        async with async_session() as session:
            stmt = select(Subscription).where(
                Subscription.expires_at.isnot(None),
                Subscription.expires_at <= datetime.utcnow()
            ).options(
                selectinload(Subscription.user).selectinload(User.configs)
            )
            
            result = await session.execute(stmt)
            subscriptions = result.scalars().all()
            
            for sub in subscriptions:
                user = sub.user
                
                active_sub_stmt = select(Subscription).where(
                    Subscription.user_id == user.id,
                    Subscription.id != sub.id,
                    (Subscription.expires_at.is_(None) | (Subscription.expires_at > datetime.utcnow()))
                )
                active_result = await session.execute(active_sub_stmt)
                has_active_sub = active_result.scalar() is not None
                
                if has_active_sub:
                    continue
                
                for config in user.configs:
                    if config.is_active:
                        # Определяем на каком сервере конфиг
                        if config.server_id:
                            # Мультисервер - отключаем на удалённом сервере
                            server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                            if server:
                                success, msg = await WireGuardMultiService.disable_config(config.public_key, server)
                            else:
                                success, msg = True, "Сервер удалён"
                        else:
                            # Локальный сервер
                            success, msg = await WireGuardService.disable_config(config.public_key)
                        
                        if success:
                            config.is_active = False
                            logger.info(f"Конфиг {config.name} отключен (подписка истекла)")
                        else:
                            logger.error(f"Ошибка отключения конфига {config.name}: {msg}")
                
                await session.commit()
                
                try:
                    await self.bot.send_message(
                        user.telegram_id,
                        "❌ Подписка истекла\n\n"
                        "Ваши VPN конфиги были отключены.\n"
                        "Продлите подписку для возобновления доступа.",
                        parse_mode=None
                    )
                except (TelegramBadRequest, TelegramForbiddenError) as e:
                    error_msg = str(e)
                    logger.error(f"Ошибка отправки уведомления об истечении user_id={user.telegram_id} (@{user.username}): {error_msg}")
                    if "chat not found" in error_msg.lower() or "bot was blocked" in error_msg.lower() or "user is deactivated" in error_msg.lower():
                        await self._handle_inactive_user(user)
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления об истечении user_id={user.telegram_id}: {e}")
    
    async def check_suspicious_activity(self):
        """Проверяет подозрительную активность пользователей"""
        logger.info("Проверка подозрительной активности...")
        try:
            alerts = await MonitoringService.check_suspicious_activity(self.bot)
            if alerts:
                logger.info(f"Обнаружено {len(alerts)} подозрительных активностей")
            else:
                logger.info("Подозрительная активность не обнаружена")
        except Exception as e:
            logger.error(f"Ошибка проверки подозрительной активности: {e}")
