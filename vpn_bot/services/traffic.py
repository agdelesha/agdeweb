"""
Сервис для работы с трафиком конфигов.
Централизованное получение трафика с кэшированием.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

from config import LOCAL_MODE

logger = logging.getLogger(__name__)

# Кэш трафика: {server_id: {'data': {...}, 'expires': datetime}}
_traffic_cache: Dict[int, dict] = {}
CACHE_TTL_SECONDS = 30  # Время жизни кэша в секундах


@dataclass
class TrafficStats:
    """Статистика трафика конфига"""
    received: int = 0
    sent: int = 0
    
    @property
    def total(self) -> int:
        return self.received + self.sent
    
    def format_received(self) -> str:
        return format_bytes(self.received)
    
    def format_sent(self) -> str:
        return format_bytes(self.sent)
    
    def format_total(self) -> str:
        return format_bytes(self.total)


def format_bytes(size: int) -> str:
    """Форматирует размер в байтах в человекочитаемый формат"""
    for unit in ['B', 'KiB', 'MiB', 'GiB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"


def _is_cache_valid(server_id: int) -> bool:
    """Проверяет валидность кэша для сервера"""
    if server_id not in _traffic_cache:
        return False
    cache_entry = _traffic_cache[server_id]
    return datetime.utcnow() < cache_entry.get('expires', datetime.min)


def _get_cached_traffic(server_id: int) -> Optional[Dict[str, Dict[str, int]]]:
    """Получает трафик из кэша если он валиден"""
    if _is_cache_valid(server_id):
        logger.debug(f"Трафик для сервера {server_id} взят из кэша")
        return _traffic_cache[server_id]['data']
    return None


def _set_cached_traffic(server_id: int, data: Dict[str, Dict[str, int]]):
    """Сохраняет трафик в кэш"""
    _traffic_cache[server_id] = {
        'data': data,
        'expires': datetime.utcnow() + timedelta(seconds=CACHE_TTL_SECONDS)
    }
    logger.debug(f"Трафик для сервера {server_id} сохранён в кэш (TTL: {CACHE_TTL_SECONDS}с)")


def clear_traffic_cache(server_id: Optional[int] = None):
    """Очищает кэш трафика (для конкретного сервера или весь)"""
    global _traffic_cache
    if server_id is not None:
        _traffic_cache.pop(server_id, None)
        logger.debug(f"Кэш трафика для сервера {server_id} очищен")
    else:
        _traffic_cache = {}
        logger.debug("Весь кэш трафика очищен")


async def get_server_traffic(server, session=None) -> Dict[str, Dict[str, int]]:
    """
    Получает трафик с сервера с кэшированием.
    
    Args:
        server: объект Server или None для локального сервера
        session: SQLAlchemy session (опционально)
    
    Returns:
        Dict[public_key, {'received': int, 'sent': int}]
    """
    if LOCAL_MODE:
        return {}
    
    # Определяем server_id (0 для локального)
    server_id = server.id if server else 0
    
    # Проверяем кэш
    cached = _get_cached_traffic(server_id)
    if cached is not None:
        return cached
    
    # Получаем свежие данные
    if server:
        from services.wireguard_multi import WireGuardMultiService
        traffic_stats = await WireGuardMultiService.get_traffic_stats(server)
        logger.info(f"Получен трафик с сервера {server.name} ({len(traffic_stats)} пиров)")
    else:
        from services.wireguard import WireGuardService
        traffic_stats = await WireGuardService.get_traffic_stats()
        logger.info(f"Получен трафик с локального сервера ({len(traffic_stats)} пиров)")
    
    # Сохраняем в кэш
    _set_cached_traffic(server_id, traffic_stats)
    
    return traffic_stats


async def get_config_traffic(config, session) -> TrafficStats:
    """
    Получает трафик для конкретного конфига.
    
    Args:
        config: объект Config
        session: SQLAlchemy session
    
    Returns:
        TrafficStats с данными о трафике
    """
    if LOCAL_MODE or not config.public_key:
        return TrafficStats()
    
    # Получаем сервер конфига
    server = None
    if config.server_id:
        from services.wireguard_multi import WireGuardMultiService
        server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
        if not server:
            logger.warning(f"Сервер {config.server_id} для конфига {config.name} не найден")
            return TrafficStats()
    
    # Получаем трафик с сервера (с кэшированием)
    traffic_stats = await get_server_traffic(server, session)
    
    # Извлекаем данные для конкретного конфига
    if config.public_key in traffic_stats:
        stats = traffic_stats[config.public_key]
        return TrafficStats(
            received=stats.get('received', 0),
            sent=stats.get('sent', 0)
        )
    
    return TrafficStats()


async def get_user_total_traffic(user, session) -> TrafficStats:
    """
    Получает суммарный трафик всех конфигов пользователя.
    
    Args:
        user: объект User с загруженными configs
        session: SQLAlchemy session
    
    Returns:
        TrafficStats с суммарными данными
    """
    if LOCAL_MODE or not user.configs:
        return TrafficStats()
    
    total_received = 0
    total_sent = 0
    
    # Кэш серверов для оптимизации
    server_cache = {}
    
    for config in user.configs:
        if not config.public_key:
            continue
        
        # Получаем сервер (с кэшированием)
        server = None
        if config.server_id:
            if config.server_id not in server_cache:
                from services.wireguard_multi import WireGuardMultiService
                server_cache[config.server_id] = await WireGuardMultiService.get_server_by_id(
                    session, config.server_id
                )
            server = server_cache[config.server_id]
        
        # Получаем трафик с сервера (с кэшированием)
        traffic_stats = await get_server_traffic(server, session)
        
        if config.public_key in traffic_stats:
            stats = traffic_stats[config.public_key]
            total_received += stats.get('received', 0)
            total_sent += stats.get('sent', 0)
    
    return TrafficStats(received=total_received, sent=total_sent)
