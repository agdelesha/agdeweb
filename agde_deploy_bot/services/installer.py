"""
Сервис для установки компонентов на серверы клиентов
"""
import asyncio
import asyncssh
import logging

logger = logging.getLogger(__name__)

GITHUB_REPO = "https://github.com/agdelesha/agdedeployvpnbot.git"
VPN_BOT_PATH = "/root/vpn_bot"


class ServerInstaller:
    """Класс для установки компонентов на сервер"""
    
    def __init__(self, ip: str, password: str):
        self.ip = ip
        self.password = password
        self.conn = None
    
    async def connect(self) -> bool:
        """Подключение к серверу"""
        try:
            self.conn = await asyncssh.connect(
                host=self.ip,
                username="root",
                password=self.password,
                known_hosts=None
            )
            return True
        except Exception as e:
            logger.error(f"Connection error to {self.ip}: {e}")
            return False
    
    async def disconnect(self):
        """Отключение от сервера"""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    async def run_cmd(self, cmd: str, timeout: int = 300) -> tuple[bool, str]:
        """Выполнить команду на сервере"""
        if not self.conn:
            return False, "Not connected"
        try:
            result = await asyncio.wait_for(
                self.conn.run(cmd, check=False),
                timeout=timeout
            )
            success = result.exit_status == 0
            # Объединяем stdout и stderr для полной информации
            output = result.stdout + result.stderr if result.stdout or result.stderr else ""
            return success, output
        except asyncio.TimeoutError:
            return False, "Command timeout"
        except Exception as e:
            return False, str(e)
    
    async def check_wg_installed(self) -> bool:
        """Проверить установлен ли WireGuard"""
        success, _ = await self.run_cmd("which wg")
        return success
    
    async def check_awg_installed(self) -> bool:
        """Проверить установлен ли AmneziaWG"""
        success, _ = await self.run_cmd("which awg-quick")
        return success
    
    async def check_v2ray_installed(self) -> bool:
        """Проверить установлен ли V2Ray/Xray"""
        success, _ = await self.run_cmd("systemctl is-active xray")
        return success
    
    async def check_vpn_bot_installed(self) -> bool:
        """Проверить установлен ли VPN бот"""
        success, _ = await self.run_cmd("systemctl is-active vpn-bot")
        return success
    
    async def install_wireguard(self, progress_callback=None) -> tuple[bool, str]:
        """Установка WireGuard"""
        steps = [
            ("Обновление пакетов...", "apt-get update"),
            ("Установка WireGuard...", "apt-get install -y wireguard wireguard-tools"),
            ("Включение IP forwarding...", "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf && sysctl -p"),
            ("Генерация ключей...", """
                mkdir -p /etc/wireguard/clients
                wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
                chmod 600 /etc/wireguard/server_private.key
            """),
            ("Создание конфигурации...", """
                PRIVATE_KEY=$(cat /etc/wireguard/server_private.key)
                INTERFACE=$(ip route | grep default | awk '{print $5}' | head -1)
                cat > /etc/wireguard/wg0.conf << EOF
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $PRIVATE_KEY
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $INTERFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $INTERFACE -j MASQUERADE
EOF
                chmod 600 /etc/wireguard/wg0.conf
            """),
            ("Запуск WireGuard...", "systemctl enable wg-quick@wg0 && systemctl start wg-quick@wg0"),
        ]
        
        for step_name, cmd in steps:
            if progress_callback:
                await progress_callback(step_name)
            success, output = await self.run_cmd(cmd)
            if not success and "already" not in output.lower():
                return False, f"Ошибка на шаге '{step_name}': {output}"
        
        return True, "WireGuard успешно установлен!"
    
    async def install_amneziawg(self, progress_callback=None) -> tuple[bool, str]:
        """Установка AmneziaWG"""
        steps = [
            ("Обновление пакетов...", "apt-get update"),
            ("Установка зависимостей...", "apt-get install -y software-properties-common"),
            ("Добавление репозитория AmneziaWG...", """
                add-apt-repository -y ppa:amnezia/ppa || true
                apt-get update
            """),
            ("Установка AmneziaWG...", "apt-get install -y amneziawg amneziawg-tools || apt-get install -y wireguard wireguard-tools"),
            ("Включение IP forwarding...", "echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf && sysctl -p"),
            ("Генерация ключей...", """
                mkdir -p /etc/amnezia/amneziawg
                wg genkey | tee /etc/amnezia/amneziawg/server_private.key | wg pubkey > /etc/amnezia/amneziawg/server_public.key
                chmod 600 /etc/amnezia/amneziawg/server_private.key
            """),
            ("Создание конфигурации...", """
                PRIVATE_KEY=$(cat /etc/amnezia/amneziawg/server_private.key)
                INTERFACE=$(ip route | grep default | awk '{print $5}' | head -1)
                cat > /etc/amnezia/amneziawg/awg0.conf << EOF
[Interface]
Address = 10.8.0.1/24
ListenPort = 51821
PrivateKey = $PRIVATE_KEY
Jc = 4
Jmin = 40
Jmax = 70
S1 = 0
S2 = 0
H1 = 1
H2 = 2
H3 = 3
H4 = 4
PostUp = iptables -A FORWARD -i awg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $INTERFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i awg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $INTERFACE -j MASQUERADE
EOF
                chmod 600 /etc/amnezia/amneziawg/awg0.conf
            """),
            ("Запуск AmneziaWG...", "systemctl enable awg-quick@awg0 && systemctl start awg-quick@awg0 || (systemctl enable wg-quick@awg0 && systemctl start wg-quick@awg0)"),
        ]
        
        for step_name, cmd in steps:
            if progress_callback:
                await progress_callback(step_name)
            success, output = await self.run_cmd(cmd, timeout=180)
            if not success and "already" not in output.lower() and "active" not in output.lower():
                logger.warning(f"Step '{step_name}' warning: {output}")
        
        return True, "AmneziaWG успешно установлен!"
    
    async def install_v2ray(self, progress_callback=None) -> tuple[bool, str]:
        """Установка V2Ray/Xray"""
        
        # Шаг 1: Обновление и зависимости
        if progress_callback:
            await progress_callback("Обновление пакетов...")
        await self.run_cmd("apt-get update", timeout=120)
        
        if progress_callback:
            await progress_callback("Установка зависимостей...")
        await self.run_cmd("apt-get install -y curl unzip", timeout=120)
        
        # Шаг 2: Установка Xray
        if progress_callback:
            await progress_callback("Скачивание и установка Xray...")
        success, output = await self.run_cmd(
            'bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install',
            timeout=300
        )
        if not success:
            return False, f"Ошибка установки Xray: {output}"
        
        # Шаг 3: Генерация ключей отдельно
        if progress_callback:
            await progress_callback("Генерация ключей...")
        
        success, keys_output = await self.run_cmd("xray x25519")
        if not success:
            return False, "Не удалось сгенерировать ключи x25519"
        
        # Парсим ключи
        private_key = ""
        public_key = ""
        for line in keys_output.split('\n'):
            if 'Private' in line:
                private_key = line.split(':')[-1].strip()
            if 'Public' in line:
                public_key = line.split(':')[-1].strip()
        
        if not private_key:
            return False, "Не удалось получить приватный ключ"
        
        # Генерируем UUID
        success, uuid_output = await self.run_cmd("cat /proc/sys/kernel/random/uuid")
        uuid = uuid_output.strip() if success else "auto-generated-uuid"
        
        # Шаг 4: Создание конфигурации
        if progress_callback:
            await progress_callback("Создание конфигурации...")
        
        config = f'''{{
  "inbounds": [{{
    "port": 443,
    "protocol": "vless",
    "settings": {{
      "clients": [{{"id": "{uuid}", "flow": "xtls-rprx-vision"}}],
      "decryption": "none"
    }},
    "streamSettings": {{
      "network": "tcp",
      "security": "reality",
      "realitySettings": {{
        "dest": "www.google.com:443",
        "serverNames": ["www.google.com"],
        "privateKey": "{private_key}",
        "shortIds": [""]
      }}
    }}
  }}],
  "outbounds": [{{"protocol": "freedom"}}]
}}'''
        
        # Записываем конфиг
        config_escaped = config.replace("'", "'\\''")
        success, output = await self.run_cmd(f"echo '{config_escaped}' > /usr/local/etc/xray/config.json")
        if not success:
            return False, f"Ошибка создания конфига: {output}"
        
        # Шаг 5: Запуск
        if progress_callback:
            await progress_callback("Запуск Xray...")
        
        await self.run_cmd("systemctl daemon-reload")
        await self.run_cmd("systemctl enable xray")
        await self.run_cmd("systemctl restart xray")
        
        # Проверяем что xray запустился
        await asyncio.sleep(3)
        success, _ = await self.run_cmd("systemctl is-active xray")
        if not success:
            _, logs = await self.run_cmd("journalctl -u xray -n 10 --no-pager")
            return False, f"Xray не запустился. Логи:\n{logs[:500]}"
        
        # Сохраняем public key для скриптов
        await self.run_cmd(f"echo '{public_key}' > /usr/local/etc/xray/public.key")
        await self.run_cmd("mkdir -p /usr/local/etc/xray/clients")
        
        return True, f"V2Ray/Xray успешно установлен!\n\nUUID: `{uuid}`\nPublic Key: `{public_key}`"
    
    async def deploy_vpn_bot(self, client_telegram_id: int, bot_token: str = None, 
                             wg_installed: bool = False, awg_installed: bool = False, 
                             v2ray_installed: bool = False, progress_callback=None) -> tuple[bool, str]:
        """Деплой VPN бота на сервер клиента"""
        
        steps = [
            ("Установка Python и Git...", "apt-get update && apt-get install -y python3 python3-pip python3-venv git"),
            ("Очистка старых файлов...", f"rm -rf {VPN_BOT_PATH}"),
            ("Клонирование репозитория...", f"git clone {GITHUB_REPO} {VPN_BOT_PATH}"),
            ("Создание виртуального окружения...", f"cd {VPN_BOT_PATH} && python3 -m venv venv"),
            ("Установка зависимостей...", f"cd {VPN_BOT_PATH} && ./venv/bin/pip install -r requirements.txt"),
        ]
        
        for step_name, cmd in steps:
            if progress_callback:
                await progress_callback(step_name)
            success, output = await self.run_cmd(cmd, timeout=300)
            if not success:
                return False, f"Ошибка на шаге '{step_name}': {output}"
        
        # Создаём .env файл - клиент становится админом
        if progress_callback:
            await progress_callback("Настройка конфигурации...")
        
        env_content = f"""BOT_TOKEN={bot_token if bot_token else 'YOUR_BOT_TOKEN_HERE'}
ADMIN_ID={client_telegram_id}
CLIENT_DIR=/etc/wireguard/clients
WG_INTERFACE=wg0
ADD_SCRIPT=/usr/local/bin/wg-new-conf.sh
REMOVE_SCRIPT=/usr/local/bin/wg-remove-client.sh
"""
        env_escaped = env_content.replace("'", "'\\''")
        success, output = await self.run_cmd(f"echo '{env_escaped}' > {VPN_BOT_PATH}/.env")
        if not success:
            return False, f"Ошибка создания .env: {output}"
        
        # Создаём systemd сервис
        if progress_callback:
            await progress_callback("Создание сервиса...")
        
        service_content = f"""[Unit]
Description=VPN Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={VPN_BOT_PATH}
ExecStart={VPN_BOT_PATH}/venv/bin/python {VPN_BOT_PATH}/bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""
        service_escaped = service_content.replace("'", "'\\''")
        success, output = await self.run_cmd(f"echo '{service_escaped}' > /etc/systemd/system/vpn-bot.service")
        if not success:
            return False, f"Ошибка создания сервиса: {output}"
        
        # Устанавливаем скрипты для создания конфигов (если протоколы уже установлены)
        if progress_callback:
            await progress_callback("Установка скриптов для конфигов...")
        
        # Проверяем и настраиваем WG
        wg_exists, _ = await self.run_cmd("which wg")
        if wg_exists:
            # Создаём директории
            await self.run_cmd("mkdir -p /etc/wireguard/clients")
            # Проверяем есть ли конфиг WG
            wg_conf_exists, _ = await self.run_cmd("test -f /etc/wireguard/wg0.conf")
            if not wg_conf_exists:
                # Создаём конфигурацию WG
                await self.run_cmd("""
                    wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
                    chmod 600 /etc/wireguard/server_private.key
                    PRIVATE_KEY=$(cat /etc/wireguard/server_private.key)
                    INTERFACE=$(ip route | grep default | awk '{print $5}' | head -1)
                    cat > /etc/wireguard/wg0.conf << EOF
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $PRIVATE_KEY
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $INTERFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $INTERFACE -j MASQUERADE
EOF
                    chmod 600 /etc/wireguard/wg0.conf
                    systemctl enable wg-quick@wg0
                    systemctl start wg-quick@wg0
                """)
            # Копируем скрипты WG из репозитория
            await self.run_cmd(f"cp {VPN_BOT_PATH}/scripts/server/wg-new-conf.sh /usr/local/bin/ && chmod +x /usr/local/bin/wg-new-conf.sh")
            await self.run_cmd(f"cp {VPN_BOT_PATH}/scripts/server/wg-remove-client.sh /usr/local/bin/ && chmod +x /usr/local/bin/wg-remove-client.sh")
        
        # Проверяем и настраиваем AWG
        awg_exists, _ = await self.run_cmd("which awg")
        if awg_exists:
            await self.run_cmd("mkdir -p /etc/amnezia/amneziawg/clients")
            # Проверяем есть ли конфиг AWG
            awg_conf_exists, _ = await self.run_cmd("test -f /etc/amnezia/amneziawg/awg0.conf")
            if not awg_conf_exists:
                # Создаём конфигурацию AWG
                await self.run_cmd("""
                    mkdir -p /etc/amnezia/amneziawg
                    wg genkey | tee /etc/amnezia/amneziawg/server_private.key | wg pubkey > /etc/amnezia/amneziawg/server_public.key
                    chmod 600 /etc/amnezia/amneziawg/server_private.key
                    PRIVATE_KEY=$(cat /etc/amnezia/amneziawg/server_private.key)
                    INTERFACE=$(ip route | grep default | awk '{print $5}' | head -1)
                    cat > /etc/amnezia/amneziawg/awg0.conf << EOF
[Interface]
Address = 10.8.0.1/24
ListenPort = 51821
PrivateKey = $PRIVATE_KEY
Jc = 4
Jmin = 40
Jmax = 70
S1 = 0
S2 = 0
H1 = 1
H2 = 2
H3 = 3
H4 = 4
PostUp = iptables -A FORWARD -i awg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $INTERFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i awg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $INTERFACE -j MASQUERADE
EOF
                    chmod 600 /etc/amnezia/amneziawg/awg0.conf
                    systemctl enable awg-quick@awg0 2>/dev/null || systemctl enable wg-quick@awg0 2>/dev/null || true
                    systemctl start awg-quick@awg0 2>/dev/null || systemctl start wg-quick@awg0 2>/dev/null || true
                """)
            # Копируем скрипты AWG из репозитория
            await self.run_cmd(f"cp {VPN_BOT_PATH}/scripts/server/awg-new-conf.sh /usr/local/bin/ && chmod +x /usr/local/bin/awg-new-conf.sh")
            await self.run_cmd(f"cp {VPN_BOT_PATH}/scripts/server/awg-remove-client.sh /usr/local/bin/ && chmod +x /usr/local/bin/awg-remove-client.sh")
        
        # Проверяем V2Ray/Xray
        xray_exists, _ = await self.run_cmd("which xray")
        if xray_exists:
            await self.run_cmd("mkdir -p /usr/local/etc/xray/clients")
            # Копируем скрипты V2Ray из репозитория
            await self.run_cmd(f"cp {VPN_BOT_PATH}/scripts/server/v2ray-new-conf.sh /usr/local/bin/ && chmod +x /usr/local/bin/v2ray-new-conf.sh")
            await self.run_cmd(f"cp {VPN_BOT_PATH}/scripts/server/v2ray-remove-client.sh /usr/local/bin/ && chmod +x /usr/local/bin/v2ray-remove-client.sh")
        
        # Запускаем бота
        if progress_callback:
            await progress_callback("Запуск бота...")
        
        await self.run_cmd("systemctl daemon-reload")
        await self.run_cmd("systemctl enable vpn-bot")
        await self.run_cmd("systemctl restart vpn-bot")
        
        await asyncio.sleep(3)
        success, _ = await self.run_cmd("systemctl is-active vpn-bot")
        
        if not success:
            _, logs = await self.run_cmd("journalctl -u vpn-bot -n 20 --no-pager")
            return False, f"Бот не запустился. Логи:\n{logs[:300]}"
        
        # Добавляем сервер в БД vpn_bot (async)
        # vpn_bot сам проверяет доступность AWG и V2Ray через SSH
        # Поэтому добавляем только один сервер с WG настройками
        if progress_callback:
            await progress_callback("Настройка сервера в боте...")
        
        # Экранируем пароль для Python строки
        escaped_password = self.password.replace("'", "\\'").replace('"', '\\"')
        
        # Добавляем один сервер - vpn_bot сам определит доступные протоколы
        add_server_cmd = f'''cd {VPN_BOT_PATH} && ./venv/bin/python -c "
import asyncio
from database.db import async_session
from database.models import Server
from sqlalchemy import select

async def add_server():
    async with async_session() as session:
        result = await session.execute(select(Server).filter(Server.host == '{self.ip}'))
        existing = result.scalars().first()
        if not existing:
            server = Server(
                name='VPN Server',
                host='{self.ip}',
                ssh_user='root',
                ssh_password='{escaped_password}',
                wg_interface='wg0',
                wg_conf_path='/etc/wireguard/wg0.conf',
                client_dir='/etc/wireguard/clients',
                add_script='/usr/local/bin/wg-new-conf.sh',
                remove_script='/usr/local/bin/wg-remove-client.sh',
                max_clients=50,
                is_active=True,
                priority=10
            )
            session.add(server)
            await session.commit()
            print('Server added')
        else:
            print('Server exists')

asyncio.run(add_server())
"'''
        
        success, output = await self.run_cmd(add_server_cmd, timeout=30)
        if not success:
            logger.warning(f"Failed to add server to vpn_bot DB: {output}")
        
        return True, "VPN бот успешно установлен! Сервер добавлен в базу данных."
