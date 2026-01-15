#!/bin/bash
# Скрипт создания нового клиента WireGuard
# Использование: ./wg-new-conf.sh <username>

set -e

WG_CONFIG="/etc/wireguard/wg0.conf"
CLIENT_DIR="/etc/wireguard/clients"
SERVER_PUBLIC_KEY=$(cat /etc/wireguard/server_public.key)
SERVER_IP=$(curl -s ifconfig.me)
SERVER_PORT=$(grep ListenPort $WG_CONFIG | awk '{print $3}')

USERNAME="$1"
if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

mkdir -p "$CLIENT_DIR"

# Генерируем ключи клиента
CLIENT_PRIVATE_KEY=$(wg genkey)
CLIENT_PUBLIC_KEY=$(echo "$CLIENT_PRIVATE_KEY" | wg pubkey)
PRESHARED_KEY=$(wg genpsk)

# Находим следующий свободный IP
LAST_IP=$(grep -oP '10\.0\.0\.\K[0-9]+' $WG_CONFIG | sort -n | tail -1)
if [ -z "$LAST_IP" ]; then
    LAST_IP=1
fi
NEW_IP=$((LAST_IP + 1))
CLIENT_IP="10.0.0.$NEW_IP"

# Добавляем клиента в конфиг сервера
cat >> $WG_CONFIG << EOF

# BEGIN_PEER $USERNAME
[Peer]
PublicKey = $CLIENT_PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
AllowedIPs = $CLIENT_IP/32
# END_PEER $USERNAME
EOF

# Создаём конфиг клиента
cat > "$CLIENT_DIR/$USERNAME.conf" << EOF
[Interface]
PrivateKey = $CLIENT_PRIVATE_KEY
Address = $CLIENT_IP/24
DNS = 1.1.1.1, 8.8.8.8

[Peer]
PublicKey = $SERVER_PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
Endpoint = $SERVER_IP:$SERVER_PORT
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF

# Применяем изменения без перезапуска
wg syncconf wg0 <(wg-quick strip wg0)

echo "OK: $USERNAME created"
echo "PUBLIC_KEY:$CLIENT_PUBLIC_KEY"
echo "CLIENT_IP:$CLIENT_IP"
echo "CONFIG_FILE:$CLIENT_DIR/$USERNAME.conf"
