#!/usr/bin/env python3
"""
Патч для vpn_bot - показывать только V2Ray протокол
"""

# Читаем handlers/user.py
with open("/root/vpn_bot/handlers/user.py", "r") as f:
    content = f.read()

# 1. В функции выбора протокола для воронки - сразу ставим has_v2ray=True, остальные False
content = content.replace(
    "has_wg = False\n    has_awg = False\n    has_v2ray = False",
    "has_wg = False\n    has_awg = False\n    has_v2ray = True  # Только V2Ray на этом сервере"
)

# 2. Убираем проверку WG (она по умолчанию True в некоторых местах)
content = content.replace(
    "has_wg = True\n    has_awg = False\n    has_v2ray = False",
    "has_wg = False\n    has_awg = False\n    has_v2ray = True  # Только V2Ray"
)

# 3. Заменяем проверку протоколов - всегда только V2Ray
old_check = """for server in servers:
            if await WireGuardMultiService.check_wg_available(server):
                has_wg = True
            if await WireGuardMultiService.check_awg_available(server):
                has_awg = True
            if await WireGuardMultiService.check_v2ray_available(server):
                has_v2ray = True"""

new_check = """for server in servers:
            # Только V2Ray на этом сервере
            if await WireGuardMultiService.check_v2ray_available(server):
                has_v2ray = True"""

content = content.replace(old_check, new_check)

# Сохраняем
with open("/root/vpn_bot/handlers/user.py", "w") as f:
    f.write(content)

print("Patched handlers/user.py - V2Ray only mode enabled")
