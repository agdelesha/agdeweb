#!/bin/bash
# Скрипт удаления клиента AmneziaWG
# Использование: ./awg-remove-client.sh <username> [public_key]

set -e

AWG_CONFIG="/etc/amnezia/amneziawg/awg0.conf"
CLIENT_DIR="/etc/amnezia/amneziawg/clients"

USERNAME="$1"
PUBLIC_KEY="$2"

if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username> [public_key]"
    exit 1
fi

# Удаляем клиента из конфига (включая BEGIN_PEER и END_PEER)
sed -i "/# BEGIN_PEER $USERNAME/,/# END_PEER $USERNAME/d" $AWG_CONFIG

# Удаляем файлы клиента
rm -f "$CLIENT_DIR/$USERNAME.conf" "$CLIENT_DIR/$USERNAME.png"

# Перезапускаем AWG
systemctl restart awg-quick@awg0

echo "OK: $USERNAME removed"
