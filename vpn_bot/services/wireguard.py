"""
WireGuardService - локальный сервис для работы с WireGuard
DEPRECATED: Большинство методов заменены на WireGuardMultiService для мультисерверной архитектуры.
Оставлены только методы для fallback (локальный сервер) и утилиты.
"""

import subprocess
import logging
from typing import Tuple, Dict

from config import WG_INTERFACE, CLIENT_DIR, REMOVE_SCRIPT, LOCAL_MODE

logger = logging.getLogger(__name__)


class WireGuardService:
    """
    Локальный WireGuard сервис.
    Используется как fallback когда нет удалённых серверов.
    Для мультисерверной архитектуры используйте WireGuardMultiService.
    """
    
    @classmethod
    async def disable_config(cls, public_key: str) -> Tuple[bool, str]:
        if LOCAL_MODE:
            logger.info(f"[LOCAL_MODE] Отключение конфига с ключом {public_key[:20]}...")
            return True, "Конфиг отключен (LOCAL_MODE)"
        
        try:
            result = subprocess.run(
                ['wg', 'set', WG_INTERFACE, 'peer', public_key, 'remove'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return True, "Конфиг отключен"
            else:
                return False, result.stderr or "Ошибка отключения"
                
        except Exception as e:
            logger.error(f"Ошибка отключения конфига: {e}")
            return False, str(e)
    
    @classmethod
    async def enable_config(cls, public_key: str, preshared_key: str, allowed_ips: str) -> Tuple[bool, str]:
        if LOCAL_MODE:
            logger.info(f"[LOCAL_MODE] Включение конфига с ключом {public_key[:20]}...")
            return True, "Конфиг включен (LOCAL_MODE)"
        
        try:
            result = subprocess.run(
                ['wg', 'set', WG_INTERFACE, 'peer', public_key, 
                 'preshared-key', '/dev/stdin', 
                 'allowed-ips', allowed_ips],
                input=preshared_key,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return True, "Конфиг включен"
            else:
                return False, result.stderr or "Ошибка включения"
                
        except Exception as e:
            logger.error(f"Ошибка включения конфига: {e}")
            return False, str(e)
    
    @classmethod
    async def delete_config(cls, username: str) -> Tuple[bool, str]:
        if LOCAL_MODE:
            logger.info(f"[LOCAL_MODE] Удаление конфига {username}")
            return True, "Конфиг удален (LOCAL_MODE)"
        
        try:
            result = subprocess.run(
                [REMOVE_SCRIPT, username],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return True, "Конфиг удален"
            else:
                return False, result.stderr or "Ошибка удаления"
                
        except Exception as e:
            logger.error(f"Ошибка удаления конфига: {e}")
            return False, str(e)
    
    @classmethod
    async def get_traffic_stats(cls) -> Dict[str, Dict[str, int]]:
        if LOCAL_MODE:
            return {}
        
        try:
            result = subprocess.run(
                ['wg', 'show', WG_INTERFACE],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return {}
            
            peers = {}
            current_peer = None
            
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('peer:'):
                    current_peer = line.split()[-1]
                    peers[current_peer] = {'received': 0, 'sent': 0}
                elif current_peer and 'transfer:' in line:
                    parts = line.replace(',', '').split()
                    if 'received' in parts:
                        rx_idx = parts.index('received')
                        rx_value = float(parts[rx_idx - 2])
                        rx_unit = parts[rx_idx - 1].lower()
                        peers[current_peer]['received'] = cls._convert_to_bytes(rx_value, rx_unit)
                    
                    if 'sent' in parts:
                        tx_idx = parts.index('sent')
                        tx_value = float(parts[tx_idx - 2])
                        tx_unit = parts[tx_idx - 1].lower()
                        peers[current_peer]['sent'] = cls._convert_to_bytes(tx_value, tx_unit)
            
            return peers
            
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return {}
    
    @staticmethod
    def _convert_to_bytes(value: float, unit: str) -> int:
        units = {
            'b': 1,
            'kib': 1024,
            'mib': 1024 * 1024,
            'gib': 1024 * 1024 * 1024
        }
        return int(value * units.get(unit.lower(), 1))
    
    @staticmethod
    def format_bytes(size: int) -> str:
        for unit in ['B', 'KiB', 'MiB', 'GiB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TiB"
    
    @classmethod
    def get_config_file_path(cls, username: str) -> str:
        return f"{CLIENT_DIR}/{username}.conf"
    
    @classmethod
    def get_qr_file_path(cls, username: str) -> str:
        return f"{CLIENT_DIR}/{username}.png"
