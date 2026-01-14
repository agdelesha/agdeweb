#!/bin/bash
# Скрипт создания нового клиента AmneziaWG
# Использование: ./awg-new-conf.sh <username>

set -e

AWG_CONFIG="/etc/amnezia/amneziawg/awg0.conf"
CLIENT_DIR="/etc/amnezia/amneziawg/clients"
SERVER_PUBLIC_KEY=$(grep PrivateKey $AWG_CONFIG | awk '{print $3}' | wg pubkey)
SERVER_IP=$(curl -s ifconfig.me)
SERVER_PORT=$(grep ListenPort $AWG_CONFIG | awk '{print $3}')

# AWG параметры обфускации (должны совпадать с сервером)
JC=$(grep "Jc" $AWG_CONFIG | awk '{print $3}' || echo "4")
JMIN=$(grep "Jmin" $AWG_CONFIG | awk '{print $3}' || echo "40")
JMAX=$(grep "Jmax" $AWG_CONFIG | awk '{print $3}' || echo "70")
S1=$(grep "S1" $AWG_CONFIG | awk '{print $3}' || echo "30")
S2=$(grep "S2" $AWG_CONFIG | awk '{print $3}' || echo "40")
H1=$(grep "H1" $AWG_CONFIG | awk '{print $3}' || echo "1234567891")
H2=$(grep "H2" $AWG_CONFIG | awk '{print $3}' || echo "2134567891")
H3=$(grep "H3" $AWG_CONFIG | awk '{print $3}' || echo "3214567891")
H4=$(grep "H4" $AWG_CONFIG | awk '{print $3}' || echo "4321567891")

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
LAST_IP=$(grep -oP '10\.8\.1\.\K[0-9]+' $AWG_CONFIG | sort -n | tail -1)
if [ -z "$LAST_IP" ]; then
    LAST_IP=1
fi
NEW_IP=$((LAST_IP + 1))
CLIENT_IP="10.8.1.$NEW_IP"

# Добавляем клиента в конфиг сервера
cat >> $AWG_CONFIG << EOF

[Peer]
# $USERNAME
PublicKey = $CLIENT_PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
AllowedIPs = $CLIENT_IP/32
EOF

# Создаём конфиг клиента
cat > "$CLIENT_DIR/$USERNAME.conf" << EOF
[Interface]
PrivateKey = $CLIENT_PRIVATE_KEY
Address = $CLIENT_IP/24
DNS = 1.1.1.1, 8.8.8.8
Jc = $JC
Jmin = $JMIN
Jmax = $JMAX
S1 = $S1
S2 = $S2
H1 = $H1
H2 = $H2
H3 = $H3
H4 = $H4

[Peer]
PublicKey = $SERVER_PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
Endpoint = $SERVER_IP:$SERVER_PORT
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
EOF

# Генерируем QR-код
qrencode -o "$CLIENT_DIR/$USERNAME.png" < "$CLIENT_DIR/$USERNAME.conf"

# Перезапускаем AWG
systemctl restart awg-quick@awg0

echo "OK: $USERNAME created"
echo "PUBLIC_KEY:$CLIENT_PUBLIC_KEY"
echo "CLIENT_IP:$CLIENT_IP"
echo "CONFIG_FILE:$CLIENT_DIR/$USERNAME.conf"
