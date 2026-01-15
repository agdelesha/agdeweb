#!/bin/bash
set -e

XRAY_CONFIG="/usr/local/etc/xray/config.json"

USERNAME="$1"
ACTION="$2"  # disable or enable

if [ -z "$USERNAME" ] || [ -z "$ACTION" ]; then
    echo "Usage: $0 <username> <disable|enable>"
    exit 1
fi

if [ "$ACTION" = "disable" ]; then
    # Отключаем клиента (enable: false)
    jq --arg email "$USERNAME" '(.inbounds[0].settings.clients[] | select(.email == $email) | .enable) = false' \
        "$XRAY_CONFIG" > "${XRAY_CONFIG}.tmp" && mv "${XRAY_CONFIG}.tmp" "$XRAY_CONFIG"
elif [ "$ACTION" = "enable" ]; then
    # Включаем клиента (enable: true)
    jq --arg email "$USERNAME" '(.inbounds[0].settings.clients[] | select(.email == $email) | .enable) = true' \
        "$XRAY_CONFIG" > "${XRAY_CONFIG}.tmp" && mv "${XRAY_CONFIG}.tmp" "$XRAY_CONFIG"
else
    echo "Invalid action: $ACTION. Use 'disable' or 'enable'"
    exit 1
fi

# Перезапускаем Xray
systemctl restart xray

echo "OK: $USERNAME $ACTION"
