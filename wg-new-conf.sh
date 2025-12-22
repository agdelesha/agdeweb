#!/bin/bash

# === НАСТРОЙКИ ===
SERVER_PUBLIC_KEY="VHPiRcWa+TImoPq7q3tAU3OxxFPP1wyl4tDdrEdaQWM="
SERVER_ENDPOINT="83.217.9.75:443"
WG_INTERFACE="wg0"
VPN_SUBNET="10.7.0"
WG_CONF="/etc/wireguard/${WG_INTERFACE}.conf"
CLIENT_DIR="/etc/wireguard/clients"
BOT_TOKEN="8442866845:AAGYSqhU-8WFyr1qEsEANHIMRAi1xOsw2C4"
CHAT_ID="906888481"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
  echo "❌ Укажи имя клиента: $0 <username>"
  exit 1
fi

for cmd in wg qrencode curl dos2unix grep; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "⛔ Установи $cmd: sudo apt install $cmd -y"
    exit 1
  fi
done

mkdir -p "$CLIENT_DIR"

# === Генерация ключей ===
PRIVATE_KEY=$(wg genkey)
PUBLIC_KEY=$(echo "$PRIVATE_KEY" | wg pubkey)
PRESHARED_KEY=$(wg genpsk)

# === Определение последнего использованного октета ===
LAST_IP=$(grep -rhPo "(?<=AllowedIPs = ${VPN_SUBNET}\.)[0-9]+" "$WG_CONF" "$CLIENT_DIR"/*.conf 2>/dev/null | sort -n | tail -n1)
if [[ ! $LAST_IP =~ ^[0-9]+$ ]]; then
  LAST_IP=1
fi
NEXT_IP=$((LAST_IP + 1))
CLIENT_IPV4="${VPN_SUBNET}.${NEXT_IP}"
CLIENT_IPV6="fddd:2c4:2c4:2c4::${NEXT_IP}"

CONFIG_FILE="$CLIENT_DIR/${USERNAME}.conf"
QR_PNG="$CLIENT_DIR/${USERNAME}.png"

# === Наполнение конфигурации клиента ===
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

# === Очистка конфигурации ===
sed -i 's/ *= */=/g' "$CONFIG_FILE"
dos2unix "$CONFIG_FILE" 2>/dev/null

# === Добавляем клиента в конфиг сервера ===
cat >> "$WG_CONF" <<EOF

# BEGIN_PEER $USERNAME
[Peer]
PublicKey = $PUBLIC_KEY
PresharedKey = $PRESHARED_KEY
AllowedIPs = ${CLIENT_IPV4}/32, ${CLIENT_IPV6}/128
# END_PEER $USERNAME
EOF

# === Применение конфигурации ===
# Добавляем пира в работающий интерфейс
wg set "$WG_INTERFACE" peer "$PUBLIC_KEY" preshared-key <(echo "$PRESHARED_KEY") allowed-ips "${CLIENT_IPV4}/32,${CLIENT_IPV6}/128"

# === Генерация QR-кода в PNG ===
qrencode -o "$QR_PNG" < "$CONFIG_FILE"

# === Отправка на Telegram ===
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" \
  -F chat_id="$CHAT_ID" \
  -F document=@"${CONFIG_FILE}" \
  -F caption="📝 WireGuard конфиг: $USERNAME" >/dev/null

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto" \
  -F chat_id="$CHAT_ID" \
  -F photo=@"${QR_PNG}" \
  -F caption="📷 QR-код для: $USERNAME" >/dev/null

echo "✅ Клиент $USERNAME добавлен: $CLIENT_IPV4"
echo "📁 Конфиг: $CONFIG_FILE"
echo "📷 QR-код: $QR_PNG"
echo "📤 Отправлено в Telegram."