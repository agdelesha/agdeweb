#!/usr/bin/env python3
"""
Патч для admin.py - использовать V2Ray функции для V2Ray конфигов
"""

with open("/root/vpn_bot/handlers/admin.py", "r") as f:
    content = f.read()

# Заменяем логику отключения/включения конфига чтобы учитывала V2Ray
old_disable = '''        if config.is_active:
            # Отключаем конфиг
            if config.server_id:
                # Мультисервер - отключаем на удалённом сервере
                server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                if server:
                    success, msg = await WireGuardMultiService.disable_config(config.public_key, server)'''

new_disable = '''        if config.is_active:
            # Отключаем конфиг
            if config.server_id:
                # Мультисервер - отключаем на удалённом сервере
                server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                if server:
                    # Для V2Ray используем отдельную функцию
                    if config.name.startswith("v2ray_") or getattr(config, 'protocol_type', '') == 'v2ray':
                        success, msg = await WireGuardMultiService.disable_v2ray_config(config.name, server)
                    else:
                        success, msg = await WireGuardMultiService.disable_config(config.public_key, server)'''

if old_disable in content:
    content = content.replace(old_disable, new_disable)
    print("Patched disable logic")
else:
    print("Disable pattern not found, trying alternative...")

# Заменяем логику включения конфига
old_enable = '''            # Включаем конфиг
            if config.server_id:
                # Мультисервер - включаем на удалённом сервере
                server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                if server:
                    success, msg = await WireGuardMultiService.enable_config(
                        config.public_key, config.preshared_key, config.allowed_ips, server
                    )'''

new_enable = '''            # Включаем конфиг
            if config.server_id:
                # Мультисервер - включаем на удалённом сервере
                server = await WireGuardMultiService.get_server_by_id(session, config.server_id)
                if server:
                    # Для V2Ray используем отдельную функцию
                    if config.name.startswith("v2ray_") or getattr(config, 'protocol_type', '') == 'v2ray':
                        success, msg = await WireGuardMultiService.enable_v2ray_config(config.name, server)
                    else:
                        success, msg = await WireGuardMultiService.enable_config(
                            config.public_key, config.preshared_key, config.allowed_ips, server
                        )'''

if old_enable in content:
    content = content.replace(old_enable, new_enable)
    print("Patched enable logic")
else:
    print("Enable pattern not found")

with open("/root/vpn_bot/handlers/admin.py", "w") as f:
    f.write(content)

print("Done patching admin.py")
