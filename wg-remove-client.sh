#!/bin/bash

# === НАСТРОЙКИ ===
WG_INTERFACE="wg0"
WG_CONF="/etc/wireguard/${WG_INTERFACE}.conf"
CLIENT_DIR="/etc/wireguard/clients"
BOT_TOKEN="${BOT_TOKEN:-}"  # Задать в переменных окружения
CHAT_ID="906888481"

USERNAME="$1"
if [ -z "$USERNAME" ]; then
  echo "❌ Укажи имя клиента для удаления: $0 <username>"
  exit 1
fi

# Проверка существования клиента
CONFIG_FILE="$CLIENT_DIR/${USERNAME}.conf"
QR_PNG="$CLIENT_DIR/${USERNAME}.png"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "❌ Клиент $USERNAME не найден!"
  echo "📁 Файл не существует: $CONFIG_FILE"
  exit 1
fi

echo "🔍 Найден клиент: $USERNAME"
echo "📁 Конфиг: $CONFIG_FILE"

# === Получаем публичный ключ клиента ===
PUBLIC_KEY=$(grep "^PublicKey" "$CONFIG_FILE" 2>/dev/null | cut -d'=' -f2 | tr -d ' ')

if [ -z "$PUBLIC_KEY" ]; then
  echo "⚠️  Не удалось найти публичный ключ в конфиге клиента"
else
  echo "🔑 Публичный ключ: $PUBLIC_KEY"
  
  # === Удаляем пира из работающего интерфейса ===
  echo "🔧 Удаляем пира из интерфейса $WG_INTERFACE..."
  wg set "$WG_INTERFACE" peer "$PUBLIC_KEY" remove 2>/dev/null
  
  if [ $? -eq 0 ]; then
    echo "✅ Пир удален из работающего интерфейса"
  else
    echo "⚠️  Не удалось удалить пира из интерфейса (возможно, уже удален)"
  fi
fi

# === Удаляем из серверной конфигурации ===
echo "📝 Удаляем из серверной конфигурации..."

# Создаем временный файл без секции клиента
TEMP_CONF=$(mktemp)
awk "
  /^# BEGIN_PEER $USERNAME\$/ { skip=1; next }
  /^# END_PEER $USERNAME\$/ { skip=0; next }
  !skip { print }
" "$WG_CONF" > "$TEMP_CONF"

# Проверяем, что изменения есть
if ! cmp -s "$WG_CONF" "$TEMP_CONF"; then
  cp "$TEMP_CONF" "$WG_CONF"
  echo "✅ Клиент удален из серверной конфигурации"
else
  echo "⚠️  Клиент не найден в серверной конфигурации"
fi

rm -f "$TEMP_CONF"

# === Удаляем файлы клиента ===
echo "🗑️  Удаляем файлы клиента..."

if [ -f "$CONFIG_FILE" ]; then
  rm -f "$CONFIG_FILE"
  echo "✅ Удален конфиг: $CONFIG_FILE"
fi

if [ -f "$QR_PNG" ]; then
  rm -f "$QR_PNG"
  echo "✅ Удален QR-код: $QR_PNG"
fi

# === Отправляем уведомление в Telegram ===
echo "📤 Отправляем уведомление в Telegram..."

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d chat_id="$CHAT_ID" \
  -d text="🗑️ Клиент WireGuard удален: $USERNAME" \
  -d parse_mode="HTML" >/dev/null

if [ $? -eq 0 ]; then
  echo "✅ Уведомление отправлено в Telegram"
else
  echo "⚠️  Не удалось отправить уведомление в Telegram"
fi

# === Показываем статистику ===
echo ""
echo "🎉 Клиент $USERNAME успешно удален!"
echo "📊 Текущие активные пиры:"
wg show "$WG_INTERFACE" peers | wc -l | xargs echo "   Количество пиров:"

# === Показываем оставшихся клиентов ===
echo "👥 Оставшиеся клиенты:"
if ls "$CLIENT_DIR"/*.conf >/dev/null 2>&1; then
  for conf in "$CLIENT_DIR"/*.conf; do
    client_name=$(basename "$conf" .conf)
    echo "   - $client_name"
  done
else
  echo "   (нет клиентов)"
fi

echo ""
echo "✅ Удаление завершено!"
