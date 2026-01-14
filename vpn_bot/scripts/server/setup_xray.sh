#!/bin/bash
# Скрипт установки Xray (V2Ray) на сервер
# Использование: ./setup_xray.sh

set -e

echo "=== Установка Xray (V2Ray) ==="

# Обновляем систему
apt-get update

# Устанавливаем зависимости
apt-get install -y curl unzip jq qrencode

# Скачиваем и устанавливаем Xray
curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh -o /tmp/install-xray.sh
bash /tmp/install-xray.sh install

# Создаём директории
mkdir -p /usr/local/etc/xray/clients

# Генерируем ключи Reality
KEYS=$(/usr/local/bin/xray x25519)
PRIVATE_KEY=$(echo "$KEYS" | grep 'Private' | awk '{print $2}')
PUBLIC_KEY=$(echo "$KEYS" | grep 'Public' | awk '{print $2}')

# Сохраняем ключи
echo "$PRIVATE_KEY" > /usr/local/etc/xray/private.key
echo "$PUBLIC_KEY" > /usr/local/etc/xray/public.key

# Получаем внешний IP
SERVER_IP=$(curl -s ifconfig.me)

# Генерируем короткий ID
SHORT_ID=$(openssl rand -hex 4)

# Создаём конфиг Xray
cat > /usr/local/etc/xray/config.json << EOF
{
  "log": {
    "loglevel": "warning",
    "access": "/var/log/xray/access.log",
    "error": "/var/log/xray/error.log"
  },
  "inbounds": [
    {
      "port": 8443,
      "protocol": "vless",
      "settings": {
        "clients": [],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "www.google.com:443",
          "xver": 0,
          "serverNames": ["www.google.com", "google.com"],
          "privateKey": "$PRIVATE_KEY",
          "shortIds": ["$SHORT_ID"]
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls"]
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom",
      "tag": "direct"
    },
    {
      "protocol": "blackhole",
      "tag": "block"
    }
  ]
}
EOF

# Обновляем скрипт создания клиентов с правильным SHORT_ID
cat > /usr/local/bin/v2ray-new-conf.sh << SCRIPT
#!/bin/bash
set -e

XRAY_CONFIG="/usr/local/etc/xray/config.json"
CLIENT_DIR="/usr/local/etc/xray/clients"
SERVER_IP="$SERVER_IP"
SERVER_PORT="8443"
PUBLIC_KEY=\$(cat /usr/local/etc/xray/public.key)
SHORT_ID="$SHORT_ID"
SNI="www.google.com"

USERNAME="\$1"
if [ -z "\$USERNAME" ]; then
    echo "Usage: \$0 <username>"
    exit 1
fi

mkdir -p "\$CLIENT_DIR"

CLIENT_UUID=\$(cat /proc/sys/kernel/random/uuid)

jq --arg uuid "\$CLIENT_UUID" --arg email "\$USERNAME" \
    '.inbounds[0].settings.clients += [{"id": \$uuid, "email": \$email, "flow": "xtls-rprx-vision"}]' \
    "\$XRAY_CONFIG" > "\${XRAY_CONFIG}.tmp" && mv "\${XRAY_CONFIG}.tmp" "\$XRAY_CONFIG"

systemctl restart xray

VLESS_LINK="vless://\${CLIENT_UUID}@\${SERVER_IP}:\${SERVER_PORT}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=\${SNI}&fp=chrome&pbk=\${PUBLIC_KEY}&sid=\${SHORT_ID}&type=tcp&headerType=none#\${USERNAME}"

echo "\$VLESS_LINK" > "\$CLIENT_DIR/\${USERNAME}.txt"
echo "\$CLIENT_UUID" > "\$CLIENT_DIR/\${USERNAME}.uuid"

qrencode -o "\$CLIENT_DIR/\${USERNAME}.png" "\$VLESS_LINK" 2>/dev/null || true

echo "OK: \$USERNAME created"
echo "UUID:\$CLIENT_UUID"
echo "VLESS_LINK:\$VLESS_LINK"
echo "CONFIG_FILE:\$CLIENT_DIR/\${USERNAME}.txt"
SCRIPT

cat > /usr/local/bin/v2ray-remove-client.sh << 'SCRIPT'
#!/bin/bash
set -e

XRAY_CONFIG="/usr/local/etc/xray/config.json"
CLIENT_DIR="/usr/local/etc/xray/clients"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username>"
    exit 1
fi

jq --arg email "$USERNAME" \
    '.inbounds[0].settings.clients = [.inbounds[0].settings.clients[] | select(.email != $email)]' \
    "$XRAY_CONFIG" > "${XRAY_CONFIG}.tmp" && mv "${XRAY_CONFIG}.tmp" "$XRAY_CONFIG"

systemctl restart xray

rm -f "$CLIENT_DIR/${USERNAME}.txt" "$CLIENT_DIR/${USERNAME}.uuid" "$CLIENT_DIR/${USERNAME}.png"

echo "OK: $USERNAME removed"
SCRIPT

chmod +x /usr/local/bin/v2ray-new-conf.sh /usr/local/bin/v2ray-remove-client.sh

# Запускаем Xray
systemctl enable xray
systemctl restart xray

echo "=== Xray установлен ==="
echo "Порт: 8443"
echo "Публичный ключ: $PUBLIC_KEY"
echo "Short ID: $SHORT_ID"
