import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import async_session, User, Config, Settings
from services.wireguard import WireGuardService
from config import ADMIN_ID

logger = logging.getLogger(__name__)

# Дефолтные пороговые значения (могут быть переопределены в БД)
DEFAULT_TRAFFIC_THRESHOLD_GB = 50
DEFAULT_CONFIGS_THRESHOLD = 3


class MonitoringService:
    # Храним предыдущие значения трафика для расчёта дельты
    _previous_stats: Dict[str, Dict[str, int]] = {}
    # Храним уже отправленные предупреждения чтобы не спамить
    _sent_alerts: Dict[int, datetime] = {}
    # Минимальный интервал между предупреждениями для одного пользователя
    ALERT_COOLDOWN_HOURS = 24
    
    @classmethod
    async def _get_setting(cls, key: str) -> str:
        """Получает настройку из БД"""
        async with async_session() as session:
            stmt = select(Settings).where(Settings.key == key)
            result = await session.execute(stmt)
            setting = result.scalar_one_or_none()
            return setting.value if setting else None
    
    @classmethod
    async def is_monitoring_enabled(cls) -> bool:
        """Проверяет, включён ли мониторинг"""
        value = await cls._get_setting("monitoring_enabled")
        return value != "0"  # По умолчанию включён
    
    @classmethod
    async def get_traffic_threshold(cls) -> int:
        """Получает порог трафика в GB"""
        value = await cls._get_setting("monitoring_traffic_gb")
        return int(value) if value else DEFAULT_TRAFFIC_THRESHOLD_GB
    
    @classmethod
    async def get_configs_threshold(cls) -> int:
        """Получает порог количества конфигов"""
        value = await cls._get_setting("monitoring_configs")
        return int(value) if value else DEFAULT_CONFIGS_THRESHOLD
    
    @classmethod
    async def check_suspicious_activity(cls, bot) -> List[Dict]:
        """Проверяет подозрительную активность и возвращает список алертов"""
        # Проверяем, включён ли мониторинг
        if not await cls.is_monitoring_enabled():
            logger.info("Мониторинг выключен, пропускаем проверку")
            return []
        
        alerts = []
        
        # 1. Проверяем трафик
        traffic_alerts = await cls._check_traffic_abuse(bot)
        alerts.extend(traffic_alerts)
        
        # 2. Проверяем количество конфигов
        config_alerts = await cls._check_config_abuse()
        alerts.extend(config_alerts)
        
        # Отправляем алерты админу
        for alert in alerts:
            await cls._send_alert_to_admin(bot, alert)
        
        return alerts
    
    @classmethod
    async def _check_traffic_abuse(cls, bot) -> List[Dict]:
        """Проверяет злоупотребление трафиком"""
        alerts = []
        
        # Получаем порог из настроек
        traffic_threshold = await cls.get_traffic_threshold()
        
        # Получаем текущую статистику трафика
        current_stats = await WireGuardService.get_traffic_stats()
        
        if not current_stats:
            return alerts
        
        # Получаем все конфиги из БД
        async with async_session() as session:
            stmt = select(Config).options(selectinload(Config.user))
            result = await session.execute(stmt)
            configs = result.scalars().all()
            
            # Создаём маппинг public_key -> config
            config_map = {c.public_key: c for c in configs}
        
        for public_key, stats in current_stats.items():
            if public_key not in config_map:
                continue
            
            config = config_map[public_key]
            user = config.user
            
            if not user:
                continue
            
            # Считаем общий трафик (received + sent)
            total_bytes = stats.get('received', 0) + stats.get('sent', 0)
            total_gb = total_bytes / (1024 ** 3)
            
            # Проверяем превышение порога
            if total_gb > traffic_threshold:
                # Проверяем, не отправляли ли уже алерт
                if cls._can_send_alert(user.id, 'traffic'):
                    alerts.append({
                        'type': 'traffic_abuse',
                        'user_id': user.id,
                        'telegram_id': user.telegram_id,
                        'username': user.username or user.full_name,
                        'config_name': config.name,
                        'traffic_gb': round(total_gb, 2),
                        'threshold_gb': traffic_threshold,
                        'reason': f"Трафик конфига {config.name} превысил {traffic_threshold} GB"
                    })
                    cls._mark_alert_sent(user.id, 'traffic')
        
        return alerts
    
    @classmethod
    async def _check_config_abuse(cls) -> List[Dict]:
        """Проверяет злоупотребление количеством конфигов"""
        alerts = []
        
        # Получаем порог из настроек
        configs_threshold = await cls.get_configs_threshold()
        
        async with async_session() as session:
            stmt = select(User).options(selectinload(User.configs))
            result = await session.execute(stmt)
            users = result.scalars().all()
            
            for user in users:
                active_configs = [c for c in user.configs if c.is_active]
                
                if len(active_configs) > configs_threshold:
                    if cls._can_send_alert(user.id, 'configs'):
                        alerts.append({
                            'type': 'config_abuse',
                            'user_id': user.id,
                            'telegram_id': user.telegram_id,
                            'username': user.username or user.full_name,
                            'config_count': len(active_configs),
                            'threshold': configs_threshold,
                            'reason': f"У пользователя {len(active_configs)} активных конфигов (порог: {configs_threshold})"
                        })
                        cls._mark_alert_sent(user.id, 'configs')
        
        return alerts
    
    @classmethod
    def _can_send_alert(cls, user_id: int, alert_type: str) -> bool:
        """Проверяет, можно ли отправить алерт (cooldown)"""
        key = f"{user_id}_{alert_type}"
        if key not in cls._sent_alerts:
            return True
        
        last_sent = cls._sent_alerts[key]
        cooldown = timedelta(hours=cls.ALERT_COOLDOWN_HOURS)
        return datetime.utcnow() - last_sent > cooldown
    
    @classmethod
    def _mark_alert_sent(cls, user_id: int, alert_type: str):
        """Отмечает, что алерт был отправлен"""
        key = f"{user_id}_{alert_type}"
        cls._sent_alerts[key] = datetime.utcnow()
    
    @classmethod
    async def _send_alert_to_admin(cls, bot, alert: Dict):
        """Отправляет алерт админу"""
        try:
            if alert['type'] == 'traffic_abuse':
                text = (
                    "⚠️ *Подозрительная активность: трафик*\n\n"
                    f"👤 Пользователь: {alert['username']}\n"
                    f"🆔 Telegram ID: `{alert['telegram_id']}`\n"
                    f"📱 Конфиг: {alert['config_name']}\n"
                    f"📊 Трафик: *{alert['traffic_gb']} GB*\n"
                    f"🚨 Порог: {alert['threshold_gb']} GB\n\n"
                    f"📝 *Причина:* {alert['reason']}\n\n"
                    "💡 Возможно, пользователь раздаёт конфиг другим людям."
                )
            elif alert['type'] == 'config_abuse':
                text = (
                    "⚠️ *Подозрительная активность: конфиги*\n\n"
                    f"👤 Пользователь: {alert['username']}\n"
                    f"🆔 Telegram ID: `{alert['telegram_id']}`\n"
                    f"📱 Активных конфигов: *{alert['config_count']}*\n"
                    f"🚨 Порог: {alert['threshold']}\n\n"
                    f"📝 *Причина:* {alert['reason']}\n\n"
                    "💡 Возможно, пользователь берёт конфиги для раздачи."
                )
            else:
                text = f"⚠️ Неизвестный тип алерта: {alert}"
            
            await bot.send_message(ADMIN_ID, text, parse_mode="Markdown")
            logger.info(f"Отправлен алерт админу: {alert['type']} для user_id={alert['user_id']}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки алерта: {e}")
    
    @classmethod
    async def get_user_stats(cls, user_id: int) -> Dict:
        """Получает статистику пользователя для админки"""
        async with async_session() as session:
            stmt = select(User).where(User.id == user_id).options(
                selectinload(User.configs),
                selectinload(User.subscriptions)
            )
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                return {}
            
            # Получаем трафик для конфигов пользователя
            traffic_stats = await WireGuardService.get_traffic_stats()
            
            configs_info = []
            total_traffic = 0
            
            for config in user.configs:
                config_traffic = traffic_stats.get(config.public_key, {})
                received = config_traffic.get('received', 0)
                sent = config_traffic.get('sent', 0)
                total = received + sent
                total_traffic += total
                
                configs_info.append({
                    'name': config.name,
                    'is_active': config.is_active,
                    'received': WireGuardService.format_bytes(received),
                    'sent': WireGuardService.format_bytes(sent),
                    'total': WireGuardService.format_bytes(total)
                })
            
            return {
                'user': user,
                'configs': configs_info,
                'total_traffic': WireGuardService.format_bytes(total_traffic),
                'config_count': len(user.configs),
                'active_config_count': len([c for c in user.configs if c.is_active])
            }
