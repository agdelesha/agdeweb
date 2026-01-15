#!/bin/bash
set -e

XUI_DB="/etc/x-ui/x-ui.db"
CLIENT_DIR="/usr/local/etc/xray/clients"
SERVER_IP=$(curl -s ifconfig.me)
SERVER_PORT="443"
PUBLIC_KEY=$(cat /usr/local/etc/xray/public.key)

# Получаем параметры из config.json
XRAY_CONFIG="/usr/local/x-ui/bin/config.json"
SHORT_ID=$(jq -r '.inbounds[] | select(.tag == "inbound-443") | .streamSettings.realitySettings.shortIds[0]' $XRAY_CONFIG)
SNI=$(jq -r '.inbounds[] | select(.tag == "inbound-443") | .streamSettings.realitySettings.serverNames[0]' $XRAY_CONFIG)

USERNAME="$1"
if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

mkdir -p "$CLIENT_DIR"
CLIENT_UUID=$(cat /proc/sys/kernel/random/uuid)
TIMESTAMP=$(date +%s)000
SUB_ID=$(cat /dev/urandom | tr -dc 'a-z0-9' | head -c 16)

# Получаем текущий settings из БД
CURRENT_SETTINGS=$(sqlite3 "$XUI_DB" "SELECT settings FROM inbounds WHERE tag='inbound-443'")

# Создаём нового клиента как JSON
NEW_CLIENT=$(cat <<EOF
{"comment":"","created_at":$TIMESTAMP,"email":"$USERNAME","enable":true,"expiryTime":0,"flow":"xtls-rprx-vision","id":"$CLIENT_UUID","limitIp":0,"reset":0,"subId":"$SUB_ID","tgId":"","totalGB":0,"updated_at":$TIMESTAMP}
EOF
)

# Добавляем клиента в JSON
NEW_SETTINGS=$(echo "$CURRENT_SETTINGS" | jq --argjson client "$NEW_CLIENT" '.clients += [$client]')

# Экранируем кавычки для SQL
ESCAPED_SETTINGS=$(echo "$NEW_SETTINGS" | sed "s/'/''/g")

# Обновляем БД
sqlite3 "$XUI_DB" "UPDATE inbounds SET settings='$ESCAPED_SETTINGS' WHERE tag='inbound-443'"

# Перезапускаем X-UI чтобы применить изменения
systemctl restart x-ui

# Формируем VLESS ссылку
VLESS_LINK="vless://${CLIENT_UUID}@${SERVER_IP}:${SERVER_PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=${SNI}&fp=chrome&pbk=${PUBLIC_KEY}&sid=${SHORT_ID}&type=tcp&headerType=none#${USERNAME}"

echo "$VLESS_LINK" > "$CLIENT_DIR/${USERNAME}.txt"
echo "$CLIENT_UUID" > "$CLIENT_DIR/${USERNAME}.uuid"

echo "OK: $USERNAME created"
echo "UUID:$CLIENT_UUID"
echo "VLESS_LINK:$VLESS_LINK"
echo "CONFIG_FILE:$CLIENT_DIR/${USERNAME}.txt"
