#!/bin/bash
# Скрипт удаления клиента V2Ray/Xray
# Использование: ./v2ray-remove-client.sh <username>

set -e

XRAY_CONFIG="/usr/local/etc/xray/config.json"
CLIENT_DIR="/usr/local/etc/xray/clients"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

# Проверяем наличие jq
if ! command -v jq &> /dev/null; then
    apt-get update && apt-get install -y jq >/dev/null 2>&1
fi

# Удаляем клиента из конфига по email
jq --arg email "$USERNAME" \
    '.inbounds[0].settings.clients = [.inbounds[0].settings.clients[] | select(.email != $email)]' \
    "$XRAY_CONFIG" > "${XRAY_CONFIG}.tmp" && mv "${XRAY_CONFIG}.tmp" "$XRAY_CONFIG"

# Перезапускаем Xray
systemctl restart xray

# Удаляем файлы клиента
rm -f "$CLIENT_DIR/${USERNAME}.txt" "$CLIENT_DIR/${USERNAME}.uuid" "$CLIENT_DIR/${USERNAME}.png"

echo "OK: $USERNAME removed"
