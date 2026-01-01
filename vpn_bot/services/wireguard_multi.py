"""
WireGuard сервис с поддержкой нескольких серверов через SSH
"""
import asyncio
import re
import logging
from typing import Optional, Tuple, Dict, List
from dataclasses import dataclass

import asyncssh
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Server, Config
from config import LOCAL_MODE

logger = logging.getLogger(__name__)


@dataclass
class ConfigData:
    """Данные созданного WireGuard конфига"""
    name: str
    public_key: str
    preshared_key: str
    allowed_ips: str
    client_ip: str
    config_content: str  # содержимое .conf файла
    qr_content: bytes    # содержимое QR PNG
    server_id: int


class WireGuardMultiService:
    """Сервис для управления WireGuard на нескольких серверах"""
    
    @classmethod
    async def get_best_server(cls, session: AsyncSession) -> Optional[Server]:
        """
        Умная балансировка нагрузки.
        Алгоритм:
        1. Только активные серверы с свободными местами
        2. Вычисляем % заполненности каждого сервера
        3. Сортируем по: priority (desc), % заполненности (asc)
        
        Это обеспечивает равномерное распределение по всем серверам,
        а не заполнение одного сервера до конца.
        """
        stmt = (
            select(
                Server,
                func.count(Config.id).label("client_count"),
                # % заполненности: clients / max_clients * 100
                (func.count(Config.id) * 100.0 / Server.max_clients).label("fill_percent")
            )
            .outerjoin(Config, (Config.server_id == Server.id) & (Config.is_active == True))
            .where(Server.is_active == True)
            .group_by(Server.id)
            .having(func.count(Config.id) < Server.max_clients)
            .order_by(
                Server.priority.desc(),  # Сначала высокий приоритет
                (func.count(Config.id) * 100.0 / Server.max_clients).asc()  # Потом менее заполненные
            )
        )
        
        result = await session.execute(stmt)
        row = result.first()
        
        if row:
            server = row[0]
            client_count = row[1]
            fill_percent = row[2] if row[2] else 0
            logger.info(f"Выбран сервер {server.name}: {client_count}/{server.max_clients} ({fill_percent:.1f}%)")
            return server
        
        return None
    
    @classmethod
    async def get_server_by_id(cls, session: AsyncSession, server_id: int) -> Optional[Server]:
        """Получить сервер по ID"""
        result = await session.execute(select(Server).where(Server.id == server_id))
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_all_servers(cls, session: AsyncSession) -> List[Server]:
        """Получить все серверы"""
        result = await session.execute(select(Server).order_by(Server.priority.desc()))
        return list(result.scalars().all())
    
    @classmethod
    async def get_server_client_count(cls, session: AsyncSession, server_id: int) -> int:
        """Получить количество активных клиентов на сервере"""
        result = await session.execute(
            select(func.count(Config.id))
            .where(Config.server_id == server_id, Config.is_active == True)
        )
        return result.scalar() or 0
    
    @classmethod
    async def _ssh_connect(cls, server: Server) -> asyncssh.SSHClientConnection:
        """Создать SSH соединение к серверу"""
        return await asyncssh.connect(
            server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            password=server.ssh_password,
            known_hosts=None,
            connect_timeout=10
        )
    
    @classmethod
    async def _ssh_execute(cls, server: Server, command: str) -> Tuple[bool, str, str]:
        """Выполнить команду на удалённом сервере через SSH"""
        try:
            async with await cls._ssh_connect(server) as conn:
                result = await asyncio.wait_for(
                    conn.run(command),
                    timeout=30
                )
                return result.exit_status == 0, result.stdout or "", result.stderr or ""
        except asyncio.TimeoutError:
            logger.error(f"SSH timeout to {server.host}")
            return False, "", "Таймаут SSH соединения"
        except asyncssh.Error as e:
            logger.error(f"SSH error to {server.host}: {e}")
            return False, "", str(e)
        except Exception as e:
            logger.error(f"Unexpected error connecting to {server.host}: {e}")
            return False, "", str(e)
    
    @classmethod
    async def _ssh_read_file(cls, server: Server, path: str) -> Optional[bytes]:
        """Прочитать файл с удалённого сервера"""
        try:
            async with await cls._ssh_connect(server) as conn:
                async with conn.start_sftp_client() as sftp:
                    async with sftp.open(path, 'rb') as f:
                        return await f.read()
        except Exception as e:
            logger.error(f"SFTP read error from {server.host}:{path}: {e}")
            return None
    
    @classmethod
    async def check_server_connection(cls, server: Server) -> Tuple[bool, str]:
        """Проверить подключение к серверу"""
        if LOCAL_MODE:
            return True, "OK (LOCAL_MODE)"
        
        success, stdout, stderr = await cls._ssh_execute(server, "echo 'OK'")
        if success and "OK" in stdout:
            return True, "Подключение успешно"
        return False, stderr or "Не удалось подключиться"
    
    @classmethod
    async def check_wireguard_installed(cls, server: Server) -> Tuple[bool, str]:
        """Проверить установлен ли WireGuard на сервере"""
        if LOCAL_MODE:
            return True, "OK (LOCAL_MODE)"
        
        success, stdout, stderr = await cls._ssh_execute(server, "which wg && wg --version")
        if success:
            return True, stdout.strip()
        return False, "WireGuard не установлен"
    
    @classmethod
    async def create_config(
        cls, 
        username: str, 
        session: AsyncSession,
        server: Optional[Server] = None
    ) -> Tuple[bool, Optional[ConfigData], str]:
        """Создать конфиг на сервере"""
        
        if LOCAL_MODE:
            logger.info(f"[LOCAL_MODE] Создание конфига для {username}")
            return True, ConfigData(
                name=username,
                public_key="LOCAL_MODE_PUBLIC_KEY",
                preshared_key="LOCAL_MODE_PSK",
                allowed_ips="10.7.0.100/32",
                client_ip="10.7.0.100",
                config_content="[Interface]\nPrivateKey = LOCAL_MODE\nAddress = 10.7.0.100/24\nDNS = 1.1.1.1",
                qr_content=b"",
                server_id=0
            ), "Конфиг создан (LOCAL_MODE)"
        
        # Выбрать сервер если не указан
        if not server:
            server = await cls.get_best_server(session)
            if not server:
                return False, None, "Нет доступных серверов. Добавьте сервер в админ-панели."
        
        logger.info(f"Создание конфига {username} на сервере {server.name} ({server.host})")
        
        # Создать конфиг на удалённом сервере
        success, stdout, stderr = await cls._ssh_execute(
            server, 
            f"{server.add_script} {username}"
        )
        
        if not success:
            logger.error(f"Ошибка создания конфига на {server.host}: {stderr}")
            return False, None, stderr or "Ошибка создания конфига"
        
        # Прочитать созданные файлы
        config_path = f"{server.client_dir}/{username}.conf"
        qr_path = f"{server.client_dir}/{username}.png"
        
        config_content = await cls._ssh_read_file(server, config_path)
        qr_content = await cls._ssh_read_file(server, qr_path)
        
        if not config_content:
            return False, None, "Не удалось прочитать созданный конфиг"
        
        # Парсим данные из wg0.conf на сервере
        wg_conf_content = await cls._ssh_read_file(server, server.wg_conf_path)
        if not wg_conf_content:
            return False, None, "Не удалось прочитать wg0.conf"
        
        parsed = cls._parse_peer_from_wg_conf(wg_conf_content.decode('utf-8'), username)
        
        if not parsed:
            return False, None, "Не удалось распарсить данные пира"
        
        return True, ConfigData(
            name=username,
            public_key=parsed['public_key'],
            preshared_key=parsed['preshared_key'],
            allowed_ips=parsed['allowed_ips'],
            client_ip=parsed['client_ip'],
            config_content=config_content.decode('utf-8'),
            qr_content=qr_content or b"",
            server_id=server.id
        ), f"Конфиг создан на сервере {server.name}"
    
    @classmethod
    async def fetch_config_content(cls, config_name: str, server: Server) -> Optional[str]:
        """Получить содержимое конфига с удалённого сервера"""
        if LOCAL_MODE:
            return None
        
        config_path = f"{server.client_dir}/{config_name}.conf"
        content = await cls._ssh_read_file(server, config_path)
        if content:
            return content.decode('utf-8')
        return None
    
    @classmethod
    def _parse_peer_from_wg_conf(cls, wg_content: str, username: str) -> Optional[Dict[str, str]]:
        """Парсинг данных пира из wg0.conf"""
        try:
            pattern = rf'# BEGIN_PEER {re.escape(username)}\n(.*?)# END_PEER {re.escape(username)}'
            match = re.search(pattern, wg_content, re.DOTALL)
            
            if not match:
                logger.error(f"Peer block not found for {username}")
                return None
            
            peer_block = match.group(1)
            
            pubkey_match = re.search(r'PublicKey\s*=\s*([a-zA-Z0-9+/=]+)', peer_block)
            psk_match = re.search(r'PresharedKey\s*=\s*([a-zA-Z0-9+/=]+)', peer_block)
            ips_match = re.search(r'AllowedIPs\s*=\s*([^\n]+)', peer_block)
            
            if not all([pubkey_match, psk_match, ips_match]):
                logger.error(f"Failed to parse peer data for {username}")
                return None
            
            allowed_ips = ips_match.group(1).strip()
            client_ip = allowed_ips.split('/')[0].split(',')[0].strip()
            
            return {
                'public_key': pubkey_match.group(1),
                'preshared_key': psk_match.group(1),
                'allowed_ips': allowed_ips,
                'client_ip': client_ip
            }
            
        except Exception as e:
            logger.error(f"Error parsing peer config: {e}")
            return None
    
    @classmethod
    async def delete_config(cls, username: str, server: Server, public_key: str = None) -> Tuple[bool, str]:
        """Удалить конфиг с сервера"""
        
        if LOCAL_MODE:
            logger.info(f"[LOCAL_MODE] Удаление конфига {username}")
            return True, "Конфиг удален (LOCAL_MODE)"
        
        logger.info(f"Удаление конфига {username} с сервера {server.name}")
        
        # Сначала отключаем пир из активного WireGuard (если есть public_key)
        if public_key:
            logger.info(f"Отключение пира {public_key[:20]}... перед удалением")
            await cls._ssh_execute(
                server,
                f"wg set {server.wg_interface} peer {public_key} remove"
            )
        
        # Затем удаляем файлы конфига через скрипт
        success, stdout, stderr = await cls._ssh_execute(
            server,
            f"{server.remove_script} {username}"
        )
        
        # Сохраняем конфигурацию WireGuard
        await cls._ssh_execute(server, f"wg-quick save {server.wg_interface}")
        
        if success:
            logger.info(f"Конфиг {username} успешно удалён с сервера {server.name}")
            return True, f"Конфиг удален с сервера {server.name}"
        
        logger.error(f"Ошибка удаления конфига {username} с {server.name}: {stderr}")
        return False, stderr or "Ошибка удаления"
    
    @classmethod
    async def disable_config(cls, public_key: str, server: Server) -> Tuple[bool, str]:
        """Отключить конфиг (убрать пир из WireGuard)"""
        
        if LOCAL_MODE:
            return True, "Конфиг отключен (LOCAL_MODE)"
        
        logger.info(f"Отключение конфига на сервере {server.name} (peer: {public_key[:20]}...)")
        
        success, stdout, stderr = await cls._ssh_execute(
            server,
            f"wg set {server.wg_interface} peer {public_key} remove"
        )
        
        if success:
            logger.info(f"Конфиг успешно отключен на {server.name}")
        else:
            logger.error(f"Ошибка отключения конфига на {server.name}: {stderr}")
        
        return success, "Конфиг отключен" if success else stderr
    
    @classmethod
    async def enable_config(
        cls, 
        public_key: str, 
        preshared_key: str, 
        allowed_ips: str,
        server: Server
    ) -> Tuple[bool, str]:
        """Включить конфиг (добавить пир в WireGuard)"""
        
        if LOCAL_MODE:
            return True, "Конфиг включен (LOCAL_MODE)"
        
        logger.info(f"Включение конфига на сервере {server.name} (peer: {public_key[:20]}...)")
        
        # Создаём временный файл с preshared key и используем его
        # Убираем пробелы после запятых в allowed_ips (WireGuard их не понимает)
        allowed_ips_clean = allowed_ips.replace(", ", ",").replace(" ,", ",")
        cmd = f"""
echo '{preshared_key}' > /tmp/psk_{public_key[:8]}.key && \
wg set {server.wg_interface} peer {public_key} preshared-key /tmp/psk_{public_key[:8]}.key allowed-ips {allowed_ips_clean} && \
rm /tmp/psk_{public_key[:8]}.key
"""
        success, stdout, stderr = await cls._ssh_execute(server, cmd)
        
        if success:
            logger.info(f"Конфиг успешно включен на {server.name}")
        else:
            logger.error(f"Ошибка включения конфига на {server.name}: {stderr}")
        
        return success, "Конфиг включен" if success else stderr
    
    @classmethod
    async def get_traffic_stats(cls, server: Server) -> Dict[str, Dict[str, int]]:
        """Получить статистику трафика с сервера"""
        
        if LOCAL_MODE:
            return {}
        
        success, stdout, stderr = await cls._ssh_execute(
            server,
            f"wg show {server.wg_interface}"
        )
        
        if not success:
            return {}
        
        peers = {}
        current_peer = None
        
        for line in stdout.split('\n'):
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
    
    @staticmethod
    def _convert_to_bytes(value: float, unit: str) -> int:
        """Конвертировать значение в байты"""
        units = {
            'b': 1,
            'kib': 1024,
            'mib': 1024 * 1024,
            'gib': 1024 * 1024 * 1024
        }
        return int(value * units.get(unit.lower(), 1))
    
    @staticmethod
    def format_bytes(size: int) -> str:
        """Форматировать байты в читаемый вид"""
        for unit in ['B', 'KiB', 'MiB', 'GiB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TiB"
    
    @classmethod
    async def check_wireguard_ready(cls, server: Server) -> Tuple[bool, str]:
        """
        Проверить готов ли сервер к работе с WireGuard.
        Возвращает (ready, message)
        Общий таймаут: 60 секунд на всю проверку.
        """
        if LOCAL_MODE:
            return True, "OK (LOCAL_MODE)"
        
        logger.info(f"[{server.host}] Начинаю проверку WireGuard...")
        
        try:
            # Общий таймаут на всю проверку
            return await asyncio.wait_for(
                cls._check_wireguard_ready_impl(server),
                timeout=60
            )
        except asyncio.TimeoutError:
            logger.error(f"[{server.host}] Общий таймаут проверки WireGuard (60 сек)")
            return False, "Таймаут проверки (60 сек)"
        except Exception as e:
            logger.error(f"[{server.host}] Ошибка проверки WireGuard: {e}")
            return False, f"Ошибка: {str(e)}"
    
    @classmethod
    async def _check_wireguard_ready_impl(cls, server: Server) -> Tuple[bool, str]:
        """Внутренняя реализация проверки WireGuard"""
        
        # Проверяем SSH подключение
        logger.info(f"[{server.host}] Проверка SSH...")
        conn_ok, conn_msg = await cls.check_server_connection(server)
        if not conn_ok:
            logger.warning(f"[{server.host}] SSH не доступен: {conn_msg}")
            return False, f"SSH: {conn_msg}"
        logger.info(f"[{server.host}] SSH OK")
        
        # Проверяем WireGuard
        logger.info(f"[{server.host}] Проверка наличия WireGuard...")
        success, stdout, stderr = await cls._ssh_execute(server, "which wg")
        if not success:
            logger.warning(f"[{server.host}] WireGuard не установлен")
            return False, "WireGuard не установлен"
        logger.info(f"[{server.host}] WireGuard найден: {stdout.strip()}")
        
        # Проверяем интерфейс
        logger.info(f"[{server.host}] Проверка интерфейса {server.wg_interface}...")
        success, stdout, stderr = await cls._ssh_execute(
            server, 
            f"wg show {server.wg_interface} 2>/dev/null || echo 'NOT_RUNNING'"
        )
        if "NOT_RUNNING" in stdout:
            logger.warning(f"[{server.host}] Интерфейс {server.wg_interface} не запущен")
            return False, f"Интерфейс {server.wg_interface} не запущен"
        logger.info(f"[{server.host}] Интерфейс {server.wg_interface} работает")
        
        # Проверяем скрипты
        logger.info(f"[{server.host}] Проверка скриптов управления...")
        success, stdout, stderr = await cls._ssh_execute(
            server,
            f"test -x {server.add_script} && test -x {server.remove_script} && echo 'OK'"
        )
        if "OK" not in stdout:
            logger.warning(f"[{server.host}] Скрипты управления не найдены")
            return False, "Скрипты управления не найдены"
        logger.info(f"[{server.host}] Скрипты найдены, сервер готов")
        
        return True, "Сервер готов к работе"
    
    @classmethod
    async def install_wireguard(cls, server: Server, progress_callback=None) -> Tuple[bool, str]:
        """
        Установить WireGuard и настроить сервер.
        progress_callback(step, message) - опциональный callback для отслеживания прогресса
        """
        if LOCAL_MODE:
            return True, "OK (LOCAL_MODE)"
        
        async def report(step: str, msg: str):
            if progress_callback:
                await progress_callback(step, msg)
            logger.info(f"[{server.host}] {step}: {msg}")
        
        await report("connect", "Подключение к серверу...")
        
        # Проверяем SSH подключение
        conn_ok, conn_msg = await cls.check_server_connection(server)
        if not conn_ok:
            return False, f"Не удалось подключиться: {conn_msg}"
        
        await report("check", "Проверка системы...")
        
        # Определяем ОС
        success, stdout, stderr = await cls._ssh_execute(server, "cat /etc/os-release | grep -E '^ID='")
        os_id = stdout.split('=')[-1].strip().strip('"') if success else "unknown"
        
        if os_id not in ["ubuntu", "debian"]:
            return False, f"Поддерживаются только Ubuntu/Debian. Обнаружено: {os_id}"
        
        await report("install", "Установка пакетов...")
        
        # Установка пакетов
        install_cmd = "DEBIAN_FRONTEND=noninteractive apt update && apt install -y wireguard qrencode curl dos2unix iptables"
        success, stdout, stderr = await cls._ssh_execute_long(server, install_cmd, timeout=120)
        if not success:
            return False, f"Ошибка установки пакетов: {stderr}"
        
        await report("sysctl", "Настройка IP forwarding...")
        
        # IP forwarding
        sysctl_cmd = """
echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/99-wireguard.conf
echo 'net.ipv6.conf.all.forwarding = 1' >> /etc/sysctl.d/99-wireguard.conf
sysctl -p /etc/sysctl.d/99-wireguard.conf
"""
        success, stdout, stderr = await cls._ssh_execute(server, sysctl_cmd)
        if not success:
            return False, f"Ошибка настройки sysctl: {stderr}"
        
        await report("keys", "Генерация ключей...")
        
        # Генерация ключей
        success, stdout, stderr = await cls._ssh_execute(server, """
mkdir -p /etc/wireguard/clients
chmod 700 /etc/wireguard
wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
chmod 600 /etc/wireguard/server_private.key
cat /etc/wireguard/server_private.key
""")
        if not success:
            return False, f"Ошибка генерации ключей: {stderr}"
        
        server_private_key = stdout.strip().split('\n')[-1]
        
        # Получаем публичный ключ
        success, stdout, stderr = await cls._ssh_execute(server, "cat /etc/wireguard/server_public.key")
        server_public_key = stdout.strip() if success else ""
        
        await report("interface", "Определение сетевого интерфейса...")
        
        # Определяем сетевой интерфейс
        success, stdout, stderr = await cls._ssh_execute(server, "ip route | grep default | awk '{print $5}' | head -n1")
        default_iface = stdout.strip() if success else "eth0"
        
        await report("config", "Создание конфигурации WireGuard...")
        
        # Создаём конфиг WireGuard
        wg_conf = f"""[Interface]
Address = 10.7.0.1/24, fddd:2c4:2c4:2c4::1/64
ListenPort = 443
PrivateKey = {server_private_key}

PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o {default_iface} -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o {default_iface} -j MASQUERADE

"""
        
        # Записываем конфиг
        escaped_conf = wg_conf.replace("'", "'\\''")
        success, stdout, stderr = await cls._ssh_execute(
            server, 
            f"echo '{escaped_conf}' > /etc/wireguard/{server.wg_interface}.conf && chmod 600 /etc/wireguard/{server.wg_interface}.conf"
        )
        if not success:
            return False, f"Ошибка записи конфига: {stderr}"
        
        await report("scripts", "Создание скриптов управления...")
        
        # Создаём скрипт добавления клиента
        add_script = cls._get_add_client_script()
        success, stdout, stderr = await cls._ssh_upload_script(server, server.add_script, add_script)
        if not success:
            return False, f"Ошибка создания скрипта добавления: {stderr}"
        
        # Создаём скрипт удаления клиента
        remove_script = cls._get_remove_client_script()
        success, stdout, stderr = await cls._ssh_upload_script(server, server.remove_script, remove_script)
        if not success:
            return False, f"Ошибка создания скрипта удаления: {stderr}"
        
        await report("start", "Запуск WireGuard...")
        
        # Запускаем WireGuard
        success, stdout, stderr = await cls._ssh_execute(
            server,
            f"systemctl enable wg-quick@{server.wg_interface} && systemctl start wg-quick@{server.wg_interface}"
        )
        if not success:
            return False, f"Ошибка запуска WireGuard: {stderr}"
        
        await report("verify", "Проверка...")
        
        # Проверяем что всё работает
        success, stdout, stderr = await cls._ssh_execute(server, f"wg show {server.wg_interface}")
        if not success:
            return False, f"WireGuard не запустился: {stderr}"
        
        await report("done", f"Установка завершена! Публичный ключ: {server_public_key}")
        
        return True, f"WireGuard установлен. Публичный ключ: {server_public_key}"
    
    @classmethod
    async def _ssh_execute_long(cls, server: Server, command: str, timeout: int = 120) -> Tuple[bool, str, str]:
        """Выполнить длительную команду на удалённом сервере"""
        try:
            async with await cls._ssh_connect(server) as conn:
                result = await asyncio.wait_for(
                    conn.run(command),
                    timeout=timeout
                )
                return result.exit_status == 0, result.stdout or "", result.stderr or ""
        except asyncio.TimeoutError:
            logger.error(f"SSH timeout to {server.host}")
            return False, "", "Таймаут SSH соединения"
        except Exception as e:
            logger.error(f"SSH error to {server.host}: {e}")
            return False, "", str(e)
    
    @classmethod
    async def _ssh_upload_script(cls, server: Server, path: str, content: str) -> Tuple[bool, str, str]:
        """Загрузить скрипт на сервер"""
        try:
            async with await cls._ssh_connect(server) as conn:
                async with conn.start_sftp_client() as sftp:
                    async with sftp.open(path, 'w') as f:
                        await f.write(content)
                # Делаем исполняемым
                result = await conn.run(f"chmod +x {path}")
                return result.exit_status == 0, "", ""
        except Exception as e:
            return False, "", str(e)
    
    @staticmethod
    def _get_add_client_script() -> str:
        """Скрипт добавления клиента"""
        return '''#!/bin/bash

WG_INTERFACE="wg0"
VPN_SUBNET="10.7.0"
WG_DIR="/etc/wireguard"
WG_CONF="${WG_DIR}/${WG_INTERFACE}.conf"
CLIENT_DIR="${WG_DIR}/clients"

SERVER_PUBLIC_KEY=$(cat "${WG_DIR}/server_public.key")
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
SERVER_PORT=$(grep "^ListenPort" "$WG_CONF" | cut -d'=' -f2 | tr -d ' ')
SERVER_ENDPOINT="${SERVER_IP}:${SERVER_PORT}"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
  echo "Usage: $0 <username>"
  exit 1
fi

mkdir -p "$CLIENT_DIR"

PRIVATE_KEY=$(wg genkey)
PUBLIC_KEY=$(echo "$PRIVATE_KEY" | wg pubkey)
PRESHARED_KEY=$(wg genpsk)

LAST_IP=$(grep -rhPo "(?<=AllowedIPs = ${VPN_SUBNET}\\.)[0-9]+" "$WG_CONF" "$CLIENT_DIR"/*.conf 2>/dev/null | sort -n | tail -n1)
if [[ ! $LAST_IP =~ ^[0-9]+$ ]]; then
  LAST_IP=1
fi
NEXT_IP=$((LAST_IP + 1))
CLIENT_IPV4="${VPN_SUBNET}.${NEXT_IP}"
CLIENT_IPV6="fddd:2c4:2c4:2c4::${NEXT_IP}"

CONFIG_FILE="$CLIENT_DIR/${USERNAME}.conf"
QR_PNG="$CLIENT_DIR/${USERNAME}.png"

cat > "$CONFIG_FILE" <<EOF
[Interface]
PrivateKey = $PRIVATE_KEY
Address = $CLIENT_IPV4/24, $CLIENT_IPV6/64
DNS = 1.1.1.1

[Peer]
PublicKey = $SERVER_PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
Endpoint = $SERVER_ENDPOINT
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
EOF

sed -i 's/ *= */=/g' "$CONFIG_FILE"
dos2unix "$CONFIG_FILE" 2>/dev/null

cat >> "$WG_CONF" <<EOF

# BEGIN_PEER $USERNAME
[Peer]
PublicKey = $PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
AllowedIPs = ${CLIENT_IPV4}/32, ${CLIENT_IPV6}/128
# END_PEER $USERNAME
EOF

wg set "$WG_INTERFACE" peer "$PUBLIC_KEY" preshared-key <(echo "$PRESHARED_KEY") allowed-ips "${CLIENT_IPV4}/32,${CLIENT_IPV6}/128"

qrencode -o "$QR_PNG" < "$CONFIG_FILE"

echo "OK: $USERNAME added with IP $CLIENT_IPV4"
echo "CONFIG_FILE: $CONFIG_FILE"
echo "QR_FILE: $QR_PNG"
'''
    
    @staticmethod
    def _get_remove_client_script() -> str:
        """Скрипт удаления клиента"""
        return '''#!/bin/bash

WG_INTERFACE="wg0"
WG_CONF="/etc/wireguard/${WG_INTERFACE}.conf"
CLIENT_DIR="/etc/wireguard/clients"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
  echo "Usage: $0 <username>"
  exit 1
fi

CONFIG_FILE="$CLIENT_DIR/${USERNAME}.conf"
QR_PNG="$CLIENT_DIR/${USERNAME}.png"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "Client $USERNAME not found"
  exit 1
fi

PUBLIC_KEY=$(awk "/# BEGIN_PEER $USERNAME/,/# END_PEER $USERNAME/" "$WG_CONF" | grep "PublicKey" | cut -d'=' -f2 | tr -d ' ')

if [ -n "$PUBLIC_KEY" ]; then
  wg set "$WG_INTERFACE" peer "$PUBLIC_KEY" remove 2>/dev/null
fi

TEMP_CONF=$(mktemp)
awk "
  /^# BEGIN_PEER $USERNAME\$/ { skip=1; next }
  /^# END_PEER $USERNAME\$/ { skip=0; next }
  !skip { print }
" "$WG_CONF" > "$TEMP_CONF"

cp "$TEMP_CONF" "$WG_CONF"
rm -f "$TEMP_CONF"

rm -f "$CONFIG_FILE" "$QR_PNG"

echo "OK: $USERNAME removed"
'''
