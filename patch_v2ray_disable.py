#!/usr/bin/env python3
"""
Патч для добавления функций disable/enable V2Ray конфигов
"""

# Читаем wireguard_multi.py
with open("/root/vpn_bot/services/wireguard_multi.py", "r") as f:
    content = f.read()

# Добавляем функции disable_v2ray_config и enable_v2ray_config после disable_config
v2ray_disable_enable = '''
    @classmethod
    async def disable_v2ray_config(cls, config_name: str, server: Server) -> Tuple[bool, str]:
        """Отключить V2Ray конфиг (disable в X-UI)"""
        
        if LOCAL_MODE:
            return True, "Конфиг отключен (LOCAL_MODE)"
        
        logger.info(f"Отключение V2Ray конфига {config_name} на сервере {server.name}")
        
        success, stdout, stderr = await cls._ssh_execute(
            server,
            f"sudo /usr/local/bin/v2ray-disable-client.sh {config_name} disable"
        )
        
        if success:
            logger.info(f"V2Ray конфиг {config_name} успешно отключен на {server.name}")
            return True, "Конфиг отключен"
        else:
            logger.error(f"Ошибка отключения V2Ray конфига на {server.name}: {stderr}")
            return False, stderr or "Ошибка отключения"
    
    @classmethod
    async def enable_v2ray_config(cls, config_name: str, server: Server) -> Tuple[bool, str]:
        """Включить V2Ray конфиг (enable в X-UI)"""
        
        if LOCAL_MODE:
            return True, "Конфиг включен (LOCAL_MODE)"
        
        logger.info(f"Включение V2Ray конфига {config_name} на сервере {server.name}")
        
        success, stdout, stderr = await cls._ssh_execute(
            server,
            f"sudo /usr/local/bin/v2ray-disable-client.sh {config_name} enable"
        )
        
        if success:
            logger.info(f"V2Ray конфиг {config_name} успешно включен на {server.name}")
            return True, "Конфиг включен"
        else:
            logger.error(f"Ошибка включения V2Ray конфига на {server.name}: {stderr}")
            return False, stderr or "Ошибка включения"

'''

# Находим место после функции enable_config и добавляем новые функции
# Ищем "async def get_traffic_stats" и вставляем перед ним
if "async def disable_v2ray_config" not in content:
    content = content.replace(
        "    @classmethod\n    async def get_traffic_stats",
        v2ray_disable_enable + "    @classmethod\n    async def get_traffic_stats"
    )
    print("Added disable_v2ray_config and enable_v2ray_config functions")
else:
    print("Functions already exist")

# Сохраняем
with open("/root/vpn_bot/services/wireguard_multi.py", "w") as f:
    f.write(content)

print("Patched wireguard_multi.py")
