import os
import re
import subprocess
import logging
from typing import Optional, Tuple, Dict
from dataclasses import dataclass

from config import (
    WG_INTERFACE, WG_CONF, CLIENT_DIR, 
    ADD_SCRIPT, REMOVE_SCRIPT, LOCAL_MODE
)

logger = logging.getLogger(__name__)


@dataclass
class ConfigData:
    name: str
    public_key: str
    preshared_key: str
    allowed_ips: str
    client_ip: str
    config_path: str
    qr_path: str


class WireGuardService:
    
    @classmethod
    async def create_config(cls, username: str) -> Tuple[bool, Optional[ConfigData], str]:
        if LOCAL_MODE:
            logger.info(f"[LOCAL_MODE] Создание конфига для {username}")
            return True, ConfigData(
                name=username,
                public_key="LOCAL_MODE_PUBLIC_KEY",
                preshared_key="LOCAL_MODE_PSK",
                allowed_ips="10.7.0.100/32",
                client_ip="10.7.0.100",
                config_path=f"/tmp/{username}.conf",
                qr_path=f"/tmp/{username}.png"
            ), "Конфиг создан (LOCAL_MODE)"
        
        try:
            result = subprocess.run(
                [ADD_SCRIPT, username],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logger.error(f"Ошибка создания конфига: {result.stderr}")
                return False, None, result.stderr or "Неизвестная ошибка"
            
            config_path = f"{CLIENT_DIR}/{username}.conf"
            qr_path = f"{CLIENT_DIR}/{username}.png"
            
            config_data = cls._parse_config_file(config_path, username)
            if config_data:
                return True, config_data, "Конфиг успешно создан"
            else:
                return False, None, "Не удалось прочитать созданный конфиг"
                
        except subprocess.TimeoutExpired:
            return False, None, "Таймаут при создании конфига"
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return False, None, str(e)
    
    @classmethod
    def _parse_config_file(cls, config_path: str, username: str) -> Optional[ConfigData]:
        try:
            if not os.path.exists(WG_CONF):
                return None
            
            with open(WG_CONF, 'r') as f:
                wg_content = f.read()
            
            pattern = rf'# BEGIN_PEER {re.escape(username)}\n(.*?)# END_PEER {re.escape(username)}'
            match = re.search(pattern, wg_content, re.DOTALL)
            
            if not match:
                return None
            
            peer_block = match.group(1)
            
            pubkey_match = re.search(r'PublicKey\s*=\s*([a-zA-Z0-9+/=]+)', peer_block)
            psk_match = re.search(r'PresharedKey\s*=\s*([a-zA-Z0-9+/=]+)', peer_block)
            ips_match = re.search(r'AllowedIPs\s*=\s*([^\n]+)', peer_block)
            
            if not all([pubkey_match, psk_match, ips_match]):
                return None
            
            allowed_ips = ips_match.group(1).strip()
            client_ip = allowed_ips.split('/')[0].split(',')[0].strip()
            
            return ConfigData(
                name=username,
                public_key=pubkey_match.group(1),
                preshared_key=psk_match.group(1),
                allowed_ips=allowed_ips,
                client_ip=client_ip,
                config_path=config_path,
                qr_path=f"{CLIENT_DIR}/{username}.png"
            )
            
        except Exception as e:
            logger.error(f"Ошибка парсинга конфига: {e}")
            return None
    
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
