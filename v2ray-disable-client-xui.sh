#!/bin/bash
set -e

XUI_DB="/etc/x-ui/x-ui.db"

USERNAME="$1"
ACTION="$2"  # disable or enable

if [ -z "$USERNAME" ] || [ -z "$ACTION" ]; then
    echo "Usage: $0 <username> <disable|enable>"
    exit 1
fi

# Получаем текущий settings из БД
CURRENT_SETTINGS=$(sqlite3 "$XUI_DB" "SELECT settings FROM inbounds WHERE tag='inbound-443'")

if [ "$ACTION" = "disable" ]; then
    # Отключаем клиента (enable: false)
    NEW_SETTINGS=$(echo "$CURRENT_SETTINGS" | jq --arg email "$USERNAME" '(.clients[] | select(.email == $email) | .enable) = false')
elif [ "$ACTION" = "enable" ]; then
    # Включаем клиента (enable: true)
    NEW_SETTINGS=$(echo "$CURRENT_SETTINGS" | jq --arg email "$USERNAME" '(.clients[] | select(.email == $email) | .enable) = true')
else
    echo "Invalid action: $ACTION. Use 'disable' or 'enable'"
    exit 1
fi

# Экранируем кавычки для SQL
ESCAPED_SETTINGS=$(echo "$NEW_SETTINGS" | sed "s/'/''/g")

# Обновляем БД
sqlite3 "$XUI_DB" "UPDATE inbounds SET settings='$ESCAPED_SETTINGS' WHERE tag='inbound-443'"

# Перезапускаем X-UI
systemctl restart x-ui

echo "OK: $USERNAME $ACTION"
