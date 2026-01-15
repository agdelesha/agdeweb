#!/bin/bash
set -e

XUI_DB="/etc/x-ui/x-ui.db"
CLIENT_DIR="/usr/local/etc/xray/clients"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

# Получаем текущий settings из БД
CURRENT_SETTINGS=$(sqlite3 "$XUI_DB" "SELECT settings FROM inbounds WHERE tag='inbound-443'")

# Удаляем клиента из JSON
NEW_SETTINGS=$(echo "$CURRENT_SETTINGS" | jq --arg email "$USERNAME" '.clients |= map(select(.email != $email))')

# Экранируем кавычки для SQL
ESCAPED_SETTINGS=$(echo "$NEW_SETTINGS" | sed "s/'/''/g")

# Обновляем БД
sqlite3 "$XUI_DB" "UPDATE inbounds SET settings='$ESCAPED_SETTINGS' WHERE tag='inbound-443'"

# Удаляем файлы конфига
rm -f "$CLIENT_DIR/${USERNAME}.txt" "$CLIENT_DIR/${USERNAME}.uuid" "$CLIENT_DIR/${USERNAME}.png"

# Перезапускаем X-UI
systemctl restart x-ui

echo "OK: $USERNAME removed"
