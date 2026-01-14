#!/bin/bash
# Скрипт создания нового клиента V2Ray/Xray (VLESS + Reality)
# Использование: ./v2ray-new-conf.sh <username>

set -e

XRAY_CONFIG="/usr/local/etc/xray/config.json"
CLIENT_DIR="/usr/local/etc/xray/clients"
SERVER_IP=$(curl -s ifconfig.me)
SERVER_PORT="8443"
PUBLIC_KEY=$(cat /usr/local/etc/xray/public.key)
SHORT_ID="abcd1234"
SNI="www.google.com"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

mkdir -p "$CLIENT_DIR"

# Генерируем UUID для клиента
CLIENT_UUID=$(cat /proc/sys/kernel/random/uuid)

# Проверяем наличие jq
if ! command -v jq &> /dev/null; then
    apt-get update && apt-get install -y jq >/dev/null 2>&1
fi

# Добавляем клиента в конфиг Xray
jq --arg uuid "$CLIENT_UUID" --arg email "$USERNAME" \
    '.inbounds[0].settings.clients += [{"id": $uuid, "email": $email, "flow": "xtls-rprx-vision"}]' \
    "$XRAY_CONFIG" > "${XRAY_CONFIG}.tmp" && mv "${XRAY_CONFIG}.tmp" "$XRAY_CONFIG"

# Перезапускаем Xray
systemctl restart xray

# Создаём ссылку VLESS для клиента
VLESS_LINK="vless://${CLIENT_UUID}@${SERVER_IP}:${SERVER_PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=${SNI}&fp=chrome&pbk=${PUBLIC_KEY}&sid=${SHORT_ID}&type=tcp&headerType=none#${USERNAME}"

# Сохраняем конфиг клиента
echo "$VLESS_LINK" > "$CLIENT_DIR/${USERNAME}.txt"
echo "$CLIENT_UUID" > "$CLIENT_DIR/${USERNAME}.uuid"

# Создаём QR-код
qrencode -o "$CLIENT_DIR/${USERNAME}.png" "$VLESS_LINK" 2>/dev/null || true

echo "OK: $USERNAME created"
echo "UUID:$CLIENT_UUID"
echo "VLESS_LINK:$VLESS_LINK"
echo "CONFIG_FILE:$CLIENT_DIR/${USERNAME}.txt"
