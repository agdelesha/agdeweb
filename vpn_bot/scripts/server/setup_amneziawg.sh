#!/bin/bash
# Скрипт установки AmneziaWG на сервер
# Использование: ./setup_amneziawg.sh

set -e

echo "=== Установка AmneziaWG ==="

# Обновляем систему
apt-get update

# Устанавливаем зависимости
apt-get install -y software-properties-common qrencode

# Добавляем PPA AmneziaWG
add-apt-repository -y ppa:amnezia/ppa
apt-get update

# Устанавливаем AmneziaWG
apt-get install -y amneziawg amneziawg-tools

# Создаём директории
mkdir -p /etc/amnezia/amneziawg/clients

# Генерируем ключи сервера
SERVER_PRIVATE_KEY=$(wg genkey)
SERVER_PUBLIC_KEY=$(echo "$SERVER_PRIVATE_KEY" | wg pubkey)

# Получаем внешний IP и сетевой интерфейс
SERVER_IP=$(curl -s ifconfig.me)
INTERFACE=$(ip route | grep default | awk '{print $5}' | head -1)

# Генерируем случайные параметры обфускации
JC=$((RANDOM % 5 + 3))
JMIN=$((RANDOM % 20 + 30))
JMAX=$((JMIN + RANDOM % 50 + 20))
S1=$((RANDOM % 30 + 20))
S2=$((RANDOM % 30 + 30))
H1=$((RANDOM % 2000000000 + 1000000000))
H2=$((RANDOM % 2000000000 + 1000000000))
H3=$((RANDOM % 2000000000 + 1000000000))
H4=$((RANDOM % 2000000000 + 1000000000))

# Создаём конфиг сервера
cat > /etc/amnezia/amneziawg/awg0.conf << EOF
[Interface]
PrivateKey = $SERVER_PRIVATE_KEY
Address = 10.8.1.1/24
ListenPort = 51820
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -t nat -A POSTROUTING -o $INTERFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -t nat -D POSTROUTING -o $INTERFACE -j MASQUERADE

# AWG обфускация
Jc = $JC
Jmin = $JMIN
Jmax = $JMAX
S1 = $S1
S2 = $S2
H1 = $H1
H2 = $H2
H3 = $H3
H4 = $H4
EOF

chmod 600 /etc/amnezia/amneziawg/awg0.conf

# Включаем IP forwarding
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p

# Копируем скрипты управления клиентами
cat > /usr/local/bin/awg-new-conf.sh << 'SCRIPT'
#!/bin/bash
set -e

AWG_CONFIG="/etc/amnezia/amneziawg/awg0.conf"
CLIENT_DIR="/etc/amnezia/amneziawg/clients"
SERVER_PUBLIC_KEY=$(grep PrivateKey $AWG_CONFIG | awk '{print $3}' | wg pubkey)
SERVER_IP=$(curl -s ifconfig.me)
SERVER_PORT=$(grep ListenPort $AWG_CONFIG | awk '{print $3}')

JC=$(grep "Jc" $AWG_CONFIG | awk '{print $3}')
JMIN=$(grep "Jmin" $AWG_CONFIG | awk '{print $3}')
JMAX=$(grep "Jmax" $AWG_CONFIG | awk '{print $3}')
S1=$(grep "S1" $AWG_CONFIG | awk '{print $3}')
S2=$(grep "S2" $AWG_CONFIG | awk '{print $3}')
H1=$(grep "H1" $AWG_CONFIG | awk '{print $3}')
H2=$(grep "H2" $AWG_CONFIG | awk '{print $3}')
H3=$(grep "H3" $AWG_CONFIG | awk '{print $3}')
H4=$(grep "H4" $AWG_CONFIG | awk '{print $3}')

USERNAME="$1"
if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

mkdir -p "$CLIENT_DIR"

CLIENT_PRIVATE_KEY=$(wg genkey)
CLIENT_PUBLIC_KEY=$(echo "$CLIENT_PRIVATE_KEY" | wg pubkey)
PRESHARED_KEY=$(wg genpsk)

LAST_IP=$(grep -oP '10\.8\.1\.\K[0-9]+' $AWG_CONFIG | sort -n | tail -1)
if [ -z "$LAST_IP" ]; then LAST_IP=1; fi
NEW_IP=$((LAST_IP + 1))
CLIENT_IP="10.8.1.$NEW_IP"

cat >> $AWG_CONFIG << EOF

[Peer]
# $USERNAME
PublicKey = $CLIENT_PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
AllowedIPs = $CLIENT_IP/32
EOF

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

qrencode -o "$CLIENT_DIR/$USERNAME.png" < "$CLIENT_DIR/$USERNAME.conf"
systemctl restart awg-quick@awg0

echo "OK: $USERNAME created"
echo "PUBLIC_KEY:$CLIENT_PUBLIC_KEY"
echo "CLIENT_IP:$CLIENT_IP"
echo "CONFIG_FILE:$CLIENT_DIR/$USERNAME.conf"
SCRIPT

cat > /usr/local/bin/awg-remove-client.sh << 'SCRIPT'
#!/bin/bash
set -e

AWG_CONFIG="/etc/amnezia/amneziawg/awg0.conf"
CLIENT_DIR="/etc/amnezia/amneziawg/clients"

USERNAME="$1"
PUBLIC_KEY="$2"

if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username> [public_key]"
    exit 1
fi

if [ -z "$PUBLIC_KEY" ]; then
    PUBLIC_KEY=$(grep -A1 "# $USERNAME" $AWG_CONFIG | grep PublicKey | awk '{print $3}')
fi

if [ -n "$PUBLIC_KEY" ]; then
    awk -v pk="$PUBLIC_KEY" '
    BEGIN { skip=0 }
    /^\[Peer\]/ { peer_block=$0; skip=0; next }
    /^PublicKey/ { 
        if ($3 == pk) { skip=1 } 
        else if (peer_block != "") { print peer_block }
        peer_block=""
    }
    { if (!skip) print }
    ' $AWG_CONFIG > ${AWG_CONFIG}.tmp
    mv ${AWG_CONFIG}.tmp $AWG_CONFIG
fi

rm -f "$CLIENT_DIR/$USERNAME.conf" "$CLIENT_DIR/$USERNAME.png"
systemctl restart awg-quick@awg0

echo "OK: $USERNAME removed"
SCRIPT

chmod +x /usr/local/bin/awg-new-conf.sh /usr/local/bin/awg-remove-client.sh

# Запускаем AWG
systemctl enable awg-quick@awg0
systemctl start awg-quick@awg0

echo "=== AmneziaWG установлен ==="
echo "Порт: 51820"
echo "Публичный ключ сервера: $SERVER_PUBLIC_KEY"
