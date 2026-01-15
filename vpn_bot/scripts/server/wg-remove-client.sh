#!/bin/bash
# Скрипт удаления клиента WireGuard
# Использование: ./wg-remove-client.sh <username>

set -e

WG_CONFIG="/etc/wireguard/wg0.conf"
CLIENT_DIR="/etc/wireguard/clients"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

# Находим публичный ключ клиента
PUBLIC_KEY=$(grep -A2 "# BEGIN_PEER $USERNAME" $WG_CONFIG | grep PublicKey | awk '{print $3}')
if [ -z "$PUBLIC_KEY" ]; then
    echo "Client not found"
    exit 1
fi

# Удаляем клиента из конфига (включая BEGIN_PEER и END_PEER)
sed -i "/# BEGIN_PEER $USERNAME/,/# END_PEER $USERNAME/d" $WG_CONFIG

# Удаляем файлы клиента
rm -f "$CLIENT_DIR/$USERNAME.conf" "$CLIENT_DIR/$USERNAME.png"

# Применяем изменения
wg syncconf wg0 <(wg-quick strip wg0)

echo "OK: $USERNAME removed"
