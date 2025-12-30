#!/bin/bash

# ============================================================
# Скрипт установки WireGuard на новый сервер
# Запускать на целевом сервере от root
# ============================================================

set -e

echo "🚀 Установка WireGuard VPN сервера"
echo "=================================="

# Определяем внешний IP
SERVER_IP=$(curl -s ifconfig.me || curl -s icanhazip.com)
if [ -z "$SERVER_IP" ]; then
    read -p "Введите внешний IP сервера: " SERVER_IP
fi

echo "📍 IP сервера: $SERVER_IP"

# Порт WireGuard
WG_PORT="${1:-443}"
echo "🔌 Порт: $WG_PORT"

# Интерфейс
WG_INTERFACE="wg0"
VPN_SUBNET="10.7.0"
WG_DIR="/etc/wireguard"
CLIENT_DIR="${WG_DIR}/clients"
SCRIPTS_DIR="/usr/local/bin"

# === Установка пакетов ===
echo ""
echo "📦 Установка пакетов..."
apt update
apt install -y wireguard qrencode curl dos2unix iptables

# === Включаем IP forwarding ===
echo ""
echo "🔧 Настройка IP forwarding..."
echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-wireguard.conf
echo "net.ipv6.conf.all.forwarding = 1" >> /etc/sysctl.d/99-wireguard.conf
sysctl -p /etc/sysctl.d/99-wireguard.conf

# === Генерация ключей сервера ===
echo ""
echo "🔑 Генерация ключей сервера..."
mkdir -p "$WG_DIR" "$CLIENT_DIR"
chmod 700 "$WG_DIR"

SERVER_PRIVATE_KEY=$(wg genkey)
SERVER_PUBLIC_KEY=$(echo "$SERVER_PRIVATE_KEY" | wg pubkey)

echo "$SERVER_PRIVATE_KEY" > "${WG_DIR}/server_private.key"
echo "$SERVER_PUBLIC_KEY" > "${WG_DIR}/server_public.key"
chmod 600 "${WG_DIR}/server_private.key"

# Определяем сетевой интерфейс
DEFAULT_IFACE=$(ip route | grep default | awk '{print $5}' | head -n1)
echo "🌐 Сетевой интерфейс: $DEFAULT_IFACE"

# === Создание конфигурации сервера ===
echo ""
echo "📝 Создание конфигурации WireGuard..."
cat > "${WG_DIR}/${WG_INTERFACE}.conf" <<EOF
[Interface]
Address = ${VPN_SUBNET}.1/24, fddd:2c4:2c4:2c4::1/64
ListenPort = ${WG_PORT}
PrivateKey = ${SERVER_PRIVATE_KEY}

PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o ${DEFAULT_IFACE} -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o ${DEFAULT_IFACE} -j MASQUERADE

EOF

chmod 600 "${WG_DIR}/${WG_INTERFACE}.conf"

# === Создание скрипта добавления клиента ===
echo ""
echo "📜 Создание скрипта wg-new-conf.sh..."
cat > "${SCRIPTS_DIR}/wg-new-conf.sh" <<'SCRIPT'
#!/bin/bash

# === НАСТРОЙКИ (автоматически определяются) ===
WG_INTERFACE="wg0"
VPN_SUBNET="10.7.0"
WG_DIR="/etc/wireguard"
WG_CONF="${WG_DIR}/${WG_INTERFACE}.conf"
CLIENT_DIR="${WG_DIR}/clients"

# Читаем публичный ключ сервера
SERVER_PUBLIC_KEY=$(cat "${WG_DIR}/server_public.key")
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
SERVER_PORT=$(grep "^ListenPort" "$WG_CONF" | cut -d'=' -f2 | tr -d ' ')
SERVER_ENDPOINT="${SERVER_IP}:${SERVER_PORT}"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
  echo "❌ Укажи имя клиента: $0 <username>"
  exit 1
fi

for cmd in wg qrencode dos2unix; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "⛔ Установи $cmd: apt install $cmd -y"
    exit 1
  fi
done

mkdir -p "$CLIENT_DIR"

# === Генерация ключей ===
PRIVATE_KEY=$(wg genkey)
PUBLIC_KEY=$(echo "$PRIVATE_KEY" | wg pubkey)
PRESHARED_KEY=$(wg genpsk)

# === Определение последнего IP ===
LAST_IP=$(grep -rhPo "(?<=AllowedIPs = ${VPN_SUBNET}\.)[0-9]+" "$WG_CONF" "$CLIENT_DIR"/*.conf 2>/dev/null | sort -n | tail -n1)
if [[ ! $LAST_IP =~ ^[0-9]+$ ]]; then
  LAST_IP=1
fi
NEXT_IP=$((LAST_IP + 1))
CLIENT_IPV4="${VPN_SUBNET}.${NEXT_IP}"
CLIENT_IPV6="fddd:2c4:2c4:2c4::${NEXT_IP}"

CONFIG_FILE="$CLIENT_DIR/${USERNAME}.conf"
QR_PNG="$CLIENT_DIR/${USERNAME}.png"

# === Конфигурация клиента ===
cat > "$CONFIG_FILE" <<EOF
[Interface]
PrivateKey = $PRIVATE_KEY
Address = $CLIENT_IPV4/24, $CLIENT_IPV6/64
DNS = 1.1.1.1

[Peer]
PublicKey = $SERVER_PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
Endpoint = $SERVER_ENDPOINT
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
EOF

sed -i 's/ *= */=/g' "$CONFIG_FILE"
dos2unix "$CONFIG_FILE" 2>/dev/null

# === Добавляем в серверный конфиг ===
cat >> "$WG_CONF" <<EOF

# BEGIN_PEER $USERNAME
[Peer]
PublicKey = $PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
AllowedIPs = ${CLIENT_IPV4}/32, ${CLIENT_IPV6}/128
# END_PEER $USERNAME
EOF

# === Применение ===
wg set "$WG_INTERFACE" peer "$PUBLIC_KEY" preshared-key <(echo "$PRESHARED_KEY") allowed-ips "${CLIENT_IPV4}/32,${CLIENT_IPV6}/128"

# === QR-код ===
qrencode -o "$QR_PNG" < "$CONFIG_FILE"

echo "✅ Клиент $USERNAME добавлен: $CLIENT_IPV4"
echo "📁 Конфиг: $CONFIG_FILE"
echo "📷 QR-код: $QR_PNG"
SCRIPT

chmod +x "${SCRIPTS_DIR}/wg-new-conf.sh"

# === Создание скрипта удаления клиента ===
echo "📜 Создание скрипта wg-remove-client.sh..."
cat > "${SCRIPTS_DIR}/wg-remove-client.sh" <<'SCRIPT'
#!/bin/bash

WG_INTERFACE="wg0"
WG_CONF="/etc/wireguard/${WG_INTERFACE}.conf"
CLIENT_DIR="/etc/wireguard/clients"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
  echo "❌ Укажи имя клиента: $0 <username>"
  exit 1
fi

CONFIG_FILE="$CLIENT_DIR/${USERNAME}.conf"
QR_PNG="$CLIENT_DIR/${USERNAME}.png"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "❌ Клиент $USERNAME не найден!"
  exit 1
fi

# Получаем публичный ключ из серверного конфига
PUBLIC_KEY=$(awk "/# BEGIN_PEER $USERNAME/,/# END_PEER $USERNAME/" "$WG_CONF" | grep "PublicKey" | cut -d'=' -f2 | tr -d ' ')

if [ -n "$PUBLIC_KEY" ]; then
  wg set "$WG_INTERFACE" peer "$PUBLIC_KEY" remove 2>/dev/null
fi

# Удаляем из конфига
TEMP_CONF=$(mktemp)
awk "
  /^# BEGIN_PEER $USERNAME\$/ { skip=1; next }
  /^# END_PEER $USERNAME\$/ { skip=0; next }
  !skip { print }
" "$WG_CONF" > "$TEMP_CONF"

cp "$TEMP_CONF" "$WG_CONF"
rm -f "$TEMP_CONF"

# Удаляем файлы
rm -f "$CONFIG_FILE" "$QR_PNG"

echo "✅ Клиент $USERNAME удален"
SCRIPT

chmod +x "${SCRIPTS_DIR}/wg-remove-client.sh"

# === Запуск WireGuard ===
echo ""
echo "🚀 Запуск WireGuard..."
systemctl enable wg-quick@${WG_INTERFACE}
systemctl start wg-quick@${WG_INTERFACE}

# === Проверка ===
echo ""
echo "✅ Установка завершена!"
echo ""
echo "📋 Информация о сервере:"
echo "   IP: $SERVER_IP"
echo "   Порт: $WG_PORT"
echo "   Публичный ключ: $SERVER_PUBLIC_KEY"
echo ""
echo "📁 Пути:"
echo "   Конфиг: ${WG_DIR}/${WG_INTERFACE}.conf"
echo "   Клиенты: $CLIENT_DIR"
echo "   Скрипт добавления: ${SCRIPTS_DIR}/wg-new-conf.sh"
echo "   Скрипт удаления: ${SCRIPTS_DIR}/wg-remove-client.sh"
echo ""
echo "🔧 Статус WireGuard:"
wg show

echo ""
echo "✅ Сервер готов к использованию!"
echo ""
echo "Для добавления в бот используйте:"
echo "  Имя|${SERVER_IP}|<ssh_password>|30"
